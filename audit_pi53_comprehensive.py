"""
audit_pi53_comprehensive.py — Comprehensive PI-53 Audit
========================================================
Rebuilds every PI-53 feature from scratch and checks:
  1. Surface: trailing dots, empty fields, bad categories, generic expected
  2. Chalk alignment: do steps match Chalk scenario intent?
  3. Preconditions: are they scenario-specific (not generic)?
  4. Step-feature type: inquiry gets inquiry steps, CDR gets CDR steps, sync gets sync steps?
  5. Cross-contamination: no API in CDR, no CDR in inquiry, etc.
"""
import sys, os, json, re
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

VALID_CATS = {'Happy Path', 'Positive', 'Negative', 'Edge Case', 'Edge Cases',
              'E2E', 'End-to-End', 'Rollback', 'Timeout', 'Audit', 'Regression'}
GENERIC_EXP = {'step completed successfully', 'completed', 'success', 'pass', 'ok',
               'step completed', 'completed successfully'}

# Chalk keywords that MUST appear in steps if Chalk mentions them
CHALK_MUST_MATCH = [
    ('removesubscriber', 'removesubscriber', 'RemoveSubscriber missing'),
    ('createsubscriber', 'createsubscriber', 'CreateSubscriber missing'),
    ('swapimsi', 'swapimsi', 'SwapIMSI missing'),
    ('swap imsi', 'swapimsi', 'SwapIMSI missing'),
    ('wholesale plan', 'wholesale', 'Wholesale plan check missing'),
    ('iccid changes', 'iccid', 'ICCID change check missing'),
    ('reference number', 'reference', 'Reference Number missing'),
]

# Contamination rules
INQUIRY_FORBIDDEN = ['century report', 'service grouping', 'ne portal', 'line table',
                     'subscriber profile', 'nbop mig', 'mig table']
CDR_FORBIDDEN = ['century report', 'service grouping', 'ne portal', 'nsl response',
                 'succ00', 'nbop mig', 'mig table', 'apollo_ne']

all_features = load_all_features()
pi53 = all_features.get('PI-53', [])

print('=' * 110)
print('  COMPREHENSIVE PI-53 AUDIT — %d features (rebuilding each from scratch)' % len(pi53))
print('=' * 110)
print()

results = []
for fid, title in sorted(pi53):
    c = _conn()
    row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
    c.close()
    if not row: continue

    chalk = load_chalk_as_object(fid, row['pi_label'])
    jira_data = load_jira(fid)
    if not jira_data or not chalk: continue

    try:
        jira = JiraIssue(key=fid, summary=jira_data.get('summary', ''),
            description=jira_data.get('description', ''), status=jira_data.get('status', ''),
            priority=jira_data.get('priority', ''), issue_type='Story',
            assignee=jira_data.get('assignee', ''), reporter=jira_data.get('reporter', ''),
            labels=json.loads(jira_data.get('labels_json', '[]')), pi=jira_data.get('pi', row['pi_label']),
            channel=jira_data.get('channel', ''), acceptance_criteria=jira_data.get('ac_text', ''),
            attachments=[], linked_issues=json.loads(jira_data.get('links_json', '[]')),
            subtasks=json.loads(jira_data.get('subtasks_json', '[]')),
            comments=json.loads(jira_data.get('comments_json', '[]')))
        options = {'channel': [jira.channel] if jira.channel else ['ITMBO'], 'devices': ['Mobile'],
            'networks': ['4G', '5G'], 'sim_types': ['eSIM', 'pSIM'], 'os_platforms': ['iOS', 'Android'],
            'include_positive': True, 'include_negative': True, 'include_e2e': True,
            'include_edge': True, 'include_attachments': False, 'strategy': 'Smart Suite (Recommended)'}
        suite = build_test_suite(jira, chalk, [], options, log=lambda x: None)
    except Exception as e:
        results.append({'fid': fid, 'title': title[:45], 'tcs': 0, 'issues': ['BUILD ERROR: %s' % str(e)[:60]], 'status': 'ERROR'})
        continue

    issues = []
    tcs = suite.test_cases

    # Determine feature type
    is_inquiry = any(kw in title.lower() for kw in ['inquiry', 'enquiry', 'query', 'retrieve',
        'sim-info', 'sim info', 'device details', 'device lock', 'event status',
        'order status', 'eligibility', 'biller line', 'login auth', 'get transaction', 'retrigger'])
    is_cdr = any(kw in title.lower() for kw in ['cdr', 'mediation', 'prr', 'ild', 'roaming',
        'usage file', 'call type', 'metering', 'mhs data usage'])
    is_sync = 'sync' in title.lower()

    for tc in tcs:
        s = tc.summary or ''
        d = tc.description or ''
        p = tc.preconditions or ''
        cat = tc.category or ''
        steps = tc.steps

        # 1. Surface
        if s and s[-1] in '.;,!?:': issues.append('TC%s: trailing dot' % tc.sno)
        if not d or len(d) < 10: issues.append('TC%s: empty desc' % tc.sno)
        if not p or len(p.strip()) < 5: issues.append('TC%s: empty precon' % tc.sno)
        if cat and cat not in VALID_CATS: issues.append('TC%s: bad cat "%s"' % (tc.sno, cat))
        if not steps: issues.append('TC%s: 0 steps' % tc.sno)
        elif len(steps) < 2: issues.append('TC%s: 1 step' % tc.sno)
        for step in steps:
            exp = (step.expected or '').strip().lower()
            if exp in GENERIC_EXP: issues.append('TC%s S%s: generic exp' % (tc.sno, step.step_num))

        # 2. Precondition relevance for sync scenarios
        if is_sync and 'sync' in s.lower():
            p_low = p.lower()
            if 'active' in s.lower() and 'deactive' in s.lower():
                if 'active on nsl' not in p_low and 'deactive' not in p_low:
                    issues.append('TC%s: sync precon not scenario-specific' % tc.sno)
            elif 'hotline' in s.lower() and 'active' in s.lower():
                if 'hotline' not in p_low and 'active on nsl' not in p_low:
                    issues.append('TC%s: sync precon not scenario-specific' % tc.sno)

        # 3. Cross-contamination
        all_step_text = ' '.join((step.summary + ' ' + step.expected).lower() for step in steps)
        if is_inquiry and 'UI Verify' not in s:
            for term in INQUIRY_FORBIDDEN:
                if term in all_step_text:
                    issues.append('TC%s: inquiry has "%s"' % (tc.sno, term))
                    break
        if is_cdr and 'UI Verify' not in s:
            for term in CDR_FORBIDDEN:
                if term in all_step_text:
                    issues.append('TC%s: CDR has "%s"' % (tc.sno, term))
                    break

    # 4. Chalk alignment
    for sc in chalk.scenarios:
        chalk_text = (sc.title + ' ' + (sc.validation or '')).lower()
        # Find matching TC
        matching_tc = None
        for tc in tcs:
            tc_words = set(re.findall(r'\b\w{5,}\b', tc.summary.lower()))
            chalk_words = set(re.findall(r'\b\w{5,}\b', chalk_text))
            if chalk_words and len(tc_words & chalk_words) / len(chalk_words) > 0.4:
                matching_tc = tc; break
        if not matching_tc: continue
        all_step_text = ' '.join((s.summary + ' ' + s.expected).lower() for s in matching_tc.steps)
        for ck, sk, desc in CHALK_MUST_MATCH:
            if ck in chalk_text and sk not in all_step_text:
                issues.append('TC%s: Chalk says "%s" but steps missing' % (matching_tc.sno, ck))

    status = 'PASS' if not issues else ('WARN' if len(issues) <= 3 else 'FAIL')
    results.append({'fid': fid, 'title': title[:45], 'tcs': len(tcs), 'steps': sum(len(tc.steps) for tc in tcs),
                    'issues': issues, 'status': status, 'issue_count': len(issues)})

# Print
print('%-18s %-45s %4s %5s %4s %s' % ('Feature', 'Title', 'TCs', 'Steps', 'Iss', 'Status'))
print('-' * 110)
for r in results:
    m = 'PASS' if r['status'] == 'PASS' else ('WARN' if r['status'] == 'WARN' else 'FAIL')
    print('%-18s %-45s %4d %5d %4d %s' % (r['fid'], r['title'], r.get('tcs', 0), r.get('steps', 0), r.get('issue_count', len(r['issues'])), m))

print()
total_tcs = sum(r.get('tcs', 0) for r in results)
total_steps = sum(r.get('steps', 0) for r in results)
total_issues = sum(r.get('issue_count', len(r['issues'])) for r in results)
pass_c = sum(1 for r in results if r['status'] == 'PASS')
warn_c = sum(1 for r in results if r['status'] == 'WARN')
fail_c = sum(1 for r in results if r['status'] == 'FAIL')
print('TOTAL: %d features | %d TCs | %d steps | %d issues' % (len(results), total_tcs, total_steps, total_issues))
print('PASS: %d | WARN: %d | FAIL: %d' % (pass_c, warn_c, fail_c))

# Detail
non_pass = [r for r in results if r['status'] != 'PASS']
if non_pass:
    print()
    print('ISSUES:')
    for r in non_pass:
        print('  %s [%d issues]:' % (r['fid'], r.get('issue_count', len(r['issues']))))
        for iss in r['issues'][:8]:
            print('    - %s' % iss)
        if len(r['issues']) > 8:
            print('    ... +%d more' % (len(r['issues']) - 8))
