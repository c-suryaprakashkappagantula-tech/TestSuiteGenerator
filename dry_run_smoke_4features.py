"""
dry_run_smoke_4features.py — Dry Run + Smoke Test for 4009, 4254, 4152, 3949
=============================================================================
Loads Jira + Chalk from DB, runs full pipeline, validates every TC field:
  - Summary: no trailing dots, no truncation, proper TC ID prefix
  - Description: present, no cross-contamination
  - Preconditions: present, numbered, context-appropriate
  - Step Summary: present, meaningful
  - Expected Result: present, not generic
  - Category: standard values only
  - Contract: MUST NOT CALL / mandatory negatives covered
"""

import sys, os, json, re
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent))

from modules.config import OUTPUTS
from modules.test_engine import build_test_suite, TestStep, TestCase
from modules.jira_fetcher import JiraIssue
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.database import load_chalk_as_object, load_jira, _conn
from modules.integration_contract import resolve_operation, get_syniverse_assertion, get_must_not_call_systems


# ════════════════════════════════════════════════════════════════════
#  FEATURES TO TEST
# ════════════════════════════════════════════════════════════════════

FEATURES = [
    'MWTGPROV-4009',  # Sync Subscriber (hybrid, conditional Syniverse, PL/YL/YD/YM/YP)
    'MWTGPROV-4254',  # ILD & International Roaming (CDR/mediation, no API)
    'MWTGPROV-4152',  # Integration with Syniverse all MVP flows (integration meta-feature)
    'MWTGPROV-3949',  # Swap MDN (device change, SwapIMSI, multi-line)
]


# ════════════════════════════════════════════════════════════════════
#  QUALITY CHECKS
# ════════════════════════════════════════════════════════════════════

VALID_CATEGORIES = {'Happy Path', 'Positive', 'Negative', 'Edge Case', 'Edge Cases',
                    'E2E', 'End-to-End', 'Rollback', 'Timeout', 'Audit', 'Regression'}

GENERIC_EXPECTED = {'step completed successfully', 'completed', 'success', 'pass', 'ok',
                    'step completed', 'completed successfully'}


def smoke_test_tc(tc, feature_type=''):
    """Run all quality checks on a single TC. Returns list of issues."""
    issues = []
    s = tc.summary or ''
    d = tc.description or ''
    p = tc.preconditions or ''

    # ── Summary checks ──
    if s.endswith('.') or s.endswith(';') or s.endswith(',') or s.endswith('!') or s.endswith(':'):
        issues.append('Summary ends with "%s"' % s[-1])
    if len(s) < 15:
        issues.append('Summary too short (%d chars)' % len(s))
    if not re.match(r'^TC\d+', s):
        issues.append('Missing TC ID prefix')
    # Truncation check
    words = s.split()
    if words and len(words[-1]) <= 2 and words[-1] not in ('V2','V1','UI','ID','BCD','SIM','MDN','API','SP','CP','CE','PU','PD','PC','YL','YD','YM','YP','PL','NE','BI','OK','E2E','IR'):
        issues.append('Summary may be truncated (ends with "%s")' % words[-1])

    # ── Description checks ──
    if not d or len(d) < 10:
        issues.append('EMPTY description')
    elif feature_type == 'notification':
        if any(kw in d.lower() for kw in ['nbop', 'portal', 'screen']):
            issues.append('CDR feature has UI language in description')
        if any(kw in d.lower() for kw in ['century report', 'service grouping']):
            issues.append('CDR feature has API verification in description')

    # ── Precondition checks ──
    if not p or len(p.strip()) < 5:
        issues.append('EMPTY preconditions')
    elif not re.match(r'^\d+[\.\t]', p.strip()):
        issues.append('Preconditions not numbered')

    # ── Step checks ──
    steps = tc.steps if hasattr(tc, 'steps') else []
    if not steps:
        issues.append('NO STEPS')
    elif len(steps) < 2:
        issues.append('Only %d step (min 2)' % len(steps))
    for step in steps:
        s_sum = step.summary if hasattr(step, 'summary') else ''
        s_exp = step.expected if hasattr(step, 'expected') else ''
        if not s_sum or len(s_sum) < 5:
            issues.append('Step %s: empty summary' % (step.step_num if hasattr(step, 'step_num') else '?'))
        if not s_exp or len(s_exp) < 5:
            issues.append('Step %s: empty expected' % (step.step_num if hasattr(step, 'step_num') else '?'))
        if s_exp and s_exp.strip().lower() in GENERIC_EXPECTED:
            issues.append('Step %s: GENERIC expected "%s"' % (step.step_num if hasattr(step, 'step_num') else '?', s_exp.strip()))

    # ── Category check ──
    cat = tc.category or ''
    if cat and cat not in VALID_CATEGORIES:
        issues.append('Non-standard category: "%s"' % cat)

    return issues


def smoke_test_contract(feature_name, tcs):
    """Check integration contract coverage."""
    issues = []
    contract = resolve_operation(feature_name)
    if not contract:
        return issues

    all_text = ' '.join(
        (tc.summary + ' ' + (tc.description or '') + ' ' +
         ' '.join((s.summary + ' ' + s.expected) for s in tc.steps))
        for tc in tcs
    ).lower()

    # MUST NOT CALL checks
    for sys_obj in get_must_not_call_systems(contract):
        sys_lower = sys_obj.name.lower()
        has_assertion = ('no %s' % sys_lower in all_text or
                        '%s is not' % sys_lower in all_text or
                        '%s not called' % sys_lower in all_text or
                        'not call %s' % sys_lower in all_text)
        if not has_assertion:
            issues.append('CONTRACT: Missing "NO %s" assertion' % sys_obj.name)

    # Syniverse assertion
    syn = get_syniverse_assertion(contract)
    if syn['assert_type'] == 'MUST_CALL' and syn['action']:
        if syn['action'].lower() not in all_text:
            issues.append('CONTRACT: Missing Syniverse %s verification' % syn['action'])

    return issues


# ════════════════════════════════════════════════════════════════════
#  LOAD FROM DB + BUILD
# ════════════════════════════════════════════════════════════════════

def load_and_build(feature_id):
    """Load Jira + Chalk from DB, build suite through full pipeline."""
    # Load Chalk
    c = _conn()
    row = c.execute(
        "SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1",
        (feature_id,)).fetchone()
    c.close()

    chalk = None
    pi = ''
    if row:
        pi = row['pi_label']
        chalk = load_chalk_as_object(feature_id, pi)

    # Load Jira
    jira_data = load_jira(feature_id)
    if not jira_data:
        return None, 'No Jira data in DB'

    jira = JiraIssue(
        key=feature_id,
        summary=jira_data.get('summary', ''),
        description=jira_data.get('description', ''),
        status=jira_data.get('status', ''),
        priority=jira_data.get('priority', ''),
        issue_type='Story',
        assignee=jira_data.get('assignee', ''),
        reporter=jira_data.get('reporter', ''),
        labels=json.loads(jira_data.get('labels_json', '[]')),
        pi=jira_data.get('pi', pi),
        channel=jira_data.get('channel', 'ITMBO'),
        acceptance_criteria=jira_data.get('ac_text', ''),
        attachments=[],
        linked_issues=json.loads(jira_data.get('links_json', '[]')),
        subtasks=json.loads(jira_data.get('subtasks_json', '[]')),
        comments=json.loads(jira_data.get('comments_json', '[]')),
    )

    options = {
        'channel': [jira.channel] if jira.channel else ['ITMBO'],
        'devices': ['Mobile'], 'networks': ['4G', '5G'],
        'sim_types': ['eSIM', 'pSIM'], 'os_platforms': ['iOS', 'Android'],
        'include_positive': True, 'include_negative': True, 'include_e2e': True,
        'include_edge': True, 'include_attachments': False,
        'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
    }

    suite = build_test_suite(jira, chalk, [], options, log=lambda x: None)  # silent
    return suite, None


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    print('=' * 110)
    print('  DRY RUN + SMOKE TEST — 4 Features')
    print('  4009 (Sync Subscriber) | 4254 (ILD/Roaming) | 4152 (Syniverse) | 3949 (Swap MDN)')
    print('=' * 110)
    print()

    all_pass = True
    grand_tcs = 0
    grand_steps = 0
    grand_issues = 0

    for fid in FEATURES:
        print('━' * 110)
        print('  FEATURE: %s' % fid)
        print('━' * 110)

        suite, err = load_and_build(fid)
        if err:
            print('  ❌ ERROR: %s' % err)
            all_pass = False
            continue

        tcs = suite.test_cases
        tc_count = len(tcs)
        step_count = sum(len(tc.steps) for tc in tcs)
        grand_tcs += tc_count
        grand_steps += step_count

        print('  Title: %s' % suite.feature_title[:80])
        print('  TCs: %d | Steps: %d | Avg steps/TC: %.1f' % (tc_count, step_count, step_count/max(tc_count,1)))
        print()

        # Determine feature type
        from modules.tc_templates import classify_feature
        fc = classify_feature(suite.feature_title, description=suite.feature_desc or '')
        ftype = fc.feature_type

        # ── Per-TC smoke test ──
        feature_issues = defaultdict(list)
        tc_pass = 0
        tc_fail = 0

        for tc in tcs:
            issues = smoke_test_tc(tc, ftype)
            if issues:
                tc_fail += 1
                for issue in issues:
                    feature_issues[issue].append('TC%s' % tc.sno)
            else:
                tc_pass += 1

        # ── Contract smoke test ──
        contract_issues = smoke_test_contract(suite.feature_title, tcs)
        for ci in contract_issues:
            feature_issues[ci].append('SUITE')

        total_issues = sum(len(v) for v in feature_issues.values())
        grand_issues += total_issues

        # ── Print TC sample (first 5 + last 2) ──
        print('  ┌─ TC SAMPLE ─────────────────────────────────────────────────────────────────────────────────────┐')
        sample_tcs = tcs[:5] + (tcs[-2:] if len(tcs) > 7 else [])
        for tc in sample_tcs:
            cat_short = tc.category[:8] if tc.category else '?'
            steps_count = len(tc.steps)
            # Extract name part
            name = re.sub(r'^TC\d+_[\w-]+_', '', tc.summary)[:70]
            precon_ok = '✓' if tc.preconditions and len(tc.preconditions.strip()) > 5 else '✗'
            steps_ok = '✓' if steps_count >= 2 else '✗'
            dot_ok = '✓' if not tc.summary.endswith('.') else '✗'
            print('  │ TC%-3s [%-8s] %s' % (tc.sno, cat_short, name))
            print('  │      Precon:%s Steps:%d%s Dot:%s' % (precon_ok, steps_count, steps_ok, dot_ok))
        print('  └─────────────────────────────────────────────────────────────────────────────────────────────────┘')
        print()

        # ── Print issues ──
        if feature_issues:
            print('  ┌─ ISSUES (%d) ──────────────────────────────────────────────────────────────────────────────────┐' % total_issues)
            for issue, affected in sorted(feature_issues.items(), key=lambda x: -len(x[1])):
                affected_str = ', '.join(affected[:5])
                if len(affected) > 5:
                    affected_str += ' +%d more' % (len(affected) - 5)
                print('  │  %-60s [%s]' % (issue[:60], affected_str[:30]))
            print('  └─────────────────────────────────────────────────────────────────────────────────────────────────┘')
            all_pass = False
        else:
            print('  ✅ ALL %d TCs PASS — zero issues' % tc_count)

        # ── Category breakdown ──
        cats = defaultdict(int)
        for tc in tcs:
            cats[tc.category or 'Unknown'] += 1
        cat_str = ' | '.join('%s:%d' % (k, v) for k, v in sorted(cats.items()))
        print('  Categories: %s' % cat_str)

        # ── Contract status ──
        contract = resolve_operation(suite.feature_title)
        if contract:
            syn = get_syniverse_assertion(contract)
            no_call = [s.name for s in get_must_not_call_systems(contract)]
            print('  Contract: %s | Syniverse=%s | MustNotCall=%s' % (
                contract.operation, syn['action'], no_call[:3] if no_call else 'none'))
        print()

    # ════════════════════════════════════════════════════════════════
    #  FINAL VERDICT
    # ════════════════════════════════════════════════════════════════
    print('=' * 110)
    print('  FINAL VERDICT')
    print('=' * 110)
    print('  Features tested: %d' % len(FEATURES))
    print('  Total TCs: %d | Total Steps: %d' % (grand_tcs, grand_steps))
    print('  Total Issues: %d' % grand_issues)
    print()
    if all_pass and grand_issues == 0:
        print('  ✅ ALL 4 FEATURES PASS — READY TO REGENERATE PI-49 to PI-53')
    elif grand_issues <= 5:
        print('  ⚠️  MINOR ISSUES (%d) — acceptable, proceed with regeneration' % grand_issues)
    else:
        print('  ❌ %d ISSUES FOUND — review before regeneration' % grand_issues)
    print('=' * 110)


if __name__ == '__main__':
    main()
