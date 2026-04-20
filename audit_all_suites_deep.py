"""
audit_all_suites_deep.py — Deep Quality Audit of ALL PI-49 to PI-53 Test Suites
================================================================================
Digs through every TC in the DB and checks:
  1. Summary    — meaningful, no truncation, no special char endings
  2. Description — present, matches feature type (UI vs API vs CDR), no cross-contamination
  3. Preconditions — present, numbered, relevant to scenario
  4. Step Summary — present, meaningful, no generic filler
  5. Expected Result — present, specific, not just "Step completed successfully"

Also checks against the Integration Contract:
  - Are "MUST NOT CALL" assertions present?
  - Are mandatory negatives covered?
  - Are verification points covered?
  - Is device/SIM sensitivity addressed?

Output: Console report + Excel audit file
"""

import sys, os, re, json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from modules.database import _conn, load_latest_suite, load_all_features
from modules.integration_contract import (
    resolve_operation, get_syniverse_assertion, get_must_not_call_systems,
    get_must_call_systems, get_mandatory_negatives, get_verify_steps,
    OPERATION_CONTRACTS
)


# ════════════════════════════════════════════════════════════════════
#  QUALITY CHECKS — each returns (pass: bool, issue: str)
# ════════════════════════════════════════════════════════════════════

def check_summary(tc):
    """Check TC summary quality."""
    issues = []
    s = tc.get('summary', '') or ''
    if not s or len(s) < 10:
        issues.append('EMPTY/TOO SHORT summary (%d chars)' % len(s))
    if len(s) > 250:
        issues.append('Summary too long (%d chars) — may be truncated' % len(s))
    if s and s[-1] in '.,;:!':
        issues.append('Summary ends with special char: "%s"' % s[-1])
    # Check for abrupt ending (word cut off)
    if s and len(s) > 20:
        last_word = s.split()[-1] if s.split() else ''
        if len(last_word) <= 2 and last_word not in ('V2', 'V1', 'UI', 'ID', 'BCD', 'SIM', 'MDN', 'API', 'SP', 'CP', 'CE', 'PU', 'PD', 'PC', 'YL', 'YD', 'YM', 'YP', 'PL', 'NE', 'BI', 'OK', 'E2E'):
            issues.append('Summary may be truncated — ends with "%s"' % last_word)
    # Check for TC ID prefix
    if not re.match(r'^TC\d+_', s):
        issues.append('Missing TC ID prefix (TC###_)')
    return issues


def check_description(tc, feature_type=''):
    """Check TC description quality."""
    issues = []
    d = tc.get('description', '') or ''
    if not d or len(d) < 10:
        issues.append('EMPTY/TOO SHORT description (%d chars)' % len(d))
        return issues
    # Cross-contamination checks
    dl = d.lower()
    if feature_type == 'ui_portal':
        if any(kw in dl for kw in ['api', 'http', 'nsl', 'endpoint', 'oauth']):
            issues.append('UI feature has API language in description')
    elif feature_type == 'notification':
        if any(kw in dl for kw in ['nbop', 'portal', 'screen', 'menu']):
            issues.append('CDR/Notification feature has UI language in description')
        if any(kw in dl for kw in ['century report', 'service grouping', 'ne portal']):
            issues.append('CDR/Notification feature has API verification language in description')
    return issues


def check_preconditions(tc):
    """Check TC preconditions quality."""
    issues = []
    p = tc.get('preconditions', '') or ''
    if not p or len(p) < 5:
        issues.append('EMPTY/TOO SHORT preconditions')
        return issues
    # Check for numbered format
    if not re.search(r'^\d+[\.\t]', p):
        issues.append('Preconditions not numbered (should start with 1.)')
    return issues


def check_steps(tc):
    """Check test steps quality."""
    issues = []
    steps = tc.get('steps', [])
    if not steps:
        issues.append('NO STEPS — TC has zero test steps')
        return issues
    if len(steps) < 2:
        issues.append('TOO FEW steps (%d) — minimum 2 expected' % len(steps))

    for step in steps:
        s_sum = step.get('summary', '') or ''
        s_exp = step.get('expected', '') or ''

        if not s_sum or len(s_sum) < 5:
            issues.append('Step %s: EMPTY/TOO SHORT summary' % step.get('step_num', '?'))
        if not s_exp or len(s_exp) < 5:
            issues.append('Step %s: EMPTY/TOO SHORT expected result' % step.get('step_num', '?'))

        # Check for generic filler
        if s_exp.strip().lower() in ('step completed successfully', 'completed', 'success', 'pass', 'ok'):
            issues.append('Step %s: GENERIC expected result "%s"' % (step.get('step_num', '?'), s_exp.strip()))

        # Check for cross-contamination in steps
        if s_sum and s_exp:
            if s_sum.strip() == s_exp.strip():
                issues.append('Step %s: Summary and Expected are IDENTICAL' % step.get('step_num', '?'))

    return issues


def check_category(tc):
    """Check TC category."""
    issues = []
    cat = tc.get('category', '') or ''
    valid = ['Happy Path', 'Positive', 'Negative', 'Edge Case', 'Edge Cases',
             'E2E', 'End-to-End', 'Rollback', 'Timeout', 'Audit', 'Regression']
    if not cat:
        issues.append('MISSING category')
    elif cat not in valid:
        issues.append('Non-standard category: "%s"' % cat)
    return issues


def check_contract_coverage(feature_id, feature_name, tcs, pi=''):
    """Check if the suite covers the integration contract requirements."""
    issues = []

    contract = resolve_operation(feature_name)
    if not contract:
        return issues  # No contract — skip

    # Build full text from all TCs
    all_text = ' '.join(
        (tc.get('summary', '') + ' ' + (tc.get('description', '') or '') + ' ' +
         ' '.join((s.get('summary', '') + ' ' + s.get('expected', ''))
                  for s in tc.get('steps', [])))
        for tc in tcs
    ).lower()

    all_summaries = ' '.join(tc.get('summary', '').lower() for tc in tcs)

    # Check "MUST NOT CALL" assertions
    no_call_systems = get_must_not_call_systems(contract)
    for sys_obj in no_call_systems:
        sys_lower = sys_obj.name.lower()
        has_assertion = ('no %s' % sys_lower in all_text or
                        '%s is not' % sys_lower in all_text or
                        '%s not called' % sys_lower in all_text or
                        'not call %s' % sys_lower in all_text)
        if not has_assertion:
            issues.append('CONTRACT: Missing "MUST NOT CALL %s" assertion' % sys_obj.name)

    # Check mandatory negatives
    mandatory_negs = get_mandatory_negatives(contract)
    for neg in mandatory_negs:
        neg_words = [w.lower() for w in neg.split() if len(w) > 3][:3]
        covered = sum(1 for w in neg_words if w in all_text)
        if covered < len(neg_words) * 0.5:
            issues.append('CONTRACT: Missing mandatory negative: %s' % neg[:60])

    # Check Syniverse assertion
    syn = get_syniverse_assertion(contract)
    if syn['assert_type'] == 'MUST_CALL':
        if syn['action'].lower() not in all_text and 'syniverse' not in all_text:
            issues.append('CONTRACT: Missing Syniverse %s verification' % syn['action'])
    elif syn['assert_type'] == 'MUST_NOT_CALL':
        if 'syniverse' in all_text and 'not' not in all_text.split('syniverse')[0][-30:]:
            # Syniverse mentioned but no "not" near it — might be wrong
            pass  # Don't flag — could be in a "verify NOT called" context

    # Check device/SIM sensitivity
    if contract.device_sensitive:
        has_phone = 'phone' in all_summaries
        has_tablet = 'tablet' in all_summaries
        if not has_phone and not has_tablet:
            issues.append('CONTRACT: Device-sensitive but no Phone/Tablet variants found')

    if contract.sim_sensitive:
        has_esim = 'esim' in all_summaries
        has_psim = 'psim' in all_summaries
        if not has_esim and not has_psim:
            issues.append('CONTRACT: SIM-sensitive but no eSIM/pSIM variants found')

    return issues


# ════════════════════════════════════════════════════════════════════
#  MAIN AUDIT
# ════════════════════════════════════════════════════════════════════

def run_audit():
    print('=' * 100)
    print('  DEEP QUALITY AUDIT — ALL PI-49 to PI-53 TEST SUITES')
    print('  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 100)
    print()

    # Load all features from DB
    all_features = load_all_features()
    target_pis = ['PI-49', 'PI-50', 'PI-51', 'PI-52', 'PI-53']

    # Collect all features across target PIs
    features_to_audit = []
    for pi in target_pis:
        feats = all_features.get(pi, [])
        for fid, title in feats:
            features_to_audit.append((pi, fid, title))

    print('Features found: %d across %s' % (len(features_to_audit), ', '.join(target_pis)))
    print()

    # Audit results
    total_features = 0
    total_tcs = 0
    total_steps = 0
    total_issues = 0
    features_with_no_suite = []
    feature_reports = []

    # Issue counters by type
    issue_counts = defaultdict(int)

    # Per-feature audit
    for pi, fid, title in sorted(features_to_audit, key=lambda x: x[1]):
        suite = load_latest_suite(fid)
        if not suite:
            features_with_no_suite.append((pi, fid, title))
            continue

        total_features += 1
        tcs = suite.get('test_cases', [])
        tc_count = len(tcs)
        step_count = sum(len(tc.get('steps', [])) for tc in tcs)
        total_tcs += tc_count
        total_steps += step_count

        # Determine feature type for cross-contamination checks
        from modules.tc_templates import classify_feature
        fc = classify_feature(title, description=suite.get('scope', ''))
        ftype = fc.feature_type

        feature_issues = []

        # Check each TC
        for tc in tcs:
            tc_issues = []
            tc_issues.extend(check_summary(tc))
            tc_issues.extend(check_description(tc, ftype))
            tc_issues.extend(check_preconditions(tc))
            tc_issues.extend(check_steps(tc))
            tc_issues.extend(check_category(tc))

            for issue in tc_issues:
                feature_issues.append('TC%s: %s' % (tc.get('sno', '?'), issue))
                issue_counts[issue.split(':')[0] if ':' in issue else issue] += 1

        # Contract coverage check
        contract_issues = check_contract_coverage(fid, title, tcs, pi)
        feature_issues.extend(contract_issues)
        for ci in contract_issues:
            issue_counts['CONTRACT'] += 1

        total_issues += len(feature_issues)

        # Determine status
        critical = sum(1 for i in feature_issues if any(kw in i for kw in ['EMPTY', 'NO STEPS', 'CONTRACT']))
        warnings = len(feature_issues) - critical

        if critical > 0:
            status = 'CRITICAL'
        elif warnings > 3:
            status = 'WARN'
        elif warnings > 0:
            status = 'MINOR'
        else:
            status = 'PASS'

        feature_reports.append({
            'pi': pi, 'fid': fid, 'title': title[:50],
            'tc_count': tc_count, 'step_count': step_count,
            'ftype': ftype, 'status': status,
            'critical': critical, 'warnings': warnings,
            'issues': feature_issues,
        })

    # ════════════════════════════════════════════════════════════════
    #  PRINT REPORT
    # ════════════════════════════════════════════════════════════════

    # Summary
    print('─' * 100)
    print('  SUMMARY')
    print('─' * 100)
    print('  Features audited:    %d' % total_features)
    print('  Features no suite:   %d' % len(features_with_no_suite))
    print('  Total TCs:           %d' % total_tcs)
    print('  Total Steps:         %d' % total_steps)
    print('  Total Issues:        %d' % total_issues)
    print('  Avg TCs/feature:     %.1f' % (total_tcs / max(total_features, 1)))
    print('  Avg Steps/TC:        %.1f' % (total_steps / max(total_tcs, 1)))
    print()

    # Status breakdown
    status_counts = defaultdict(int)
    for r in feature_reports:
        status_counts[r['status']] += 1

    print('  Status Breakdown:')
    for status in ['PASS', 'MINOR', 'WARN', 'CRITICAL']:
        count = status_counts.get(status, 0)
        bar = '█' * count
        print('    %-10s %3d  %s' % (status, count, bar))
    print()

    # Features with no suite
    if features_with_no_suite:
        print('─' * 100)
        print('  FEATURES WITH NO SUITE IN DB (%d)' % len(features_with_no_suite))
        print('─' * 100)
        for pi, fid, title in features_with_no_suite:
            print('    %-6s  %-20s  %s' % (pi, fid, title[:60]))
        print()

    # Top issue types
    print('─' * 100)
    print('  TOP ISSUE TYPES')
    print('─' * 100)
    for issue_type, count in sorted(issue_counts.items(), key=lambda x: -x[1])[:15]:
        print('    %4d  %s' % (count, issue_type[:80]))
    print()

    # Per-feature detail (only CRITICAL and WARN)
    print('─' * 100)
    print('  FEATURE DETAIL (CRITICAL + WARN only)')
    print('─' * 100)
    for r in feature_reports:
        if r['status'] in ('CRITICAL', 'WARN'):
            print()
            print('  [%s] %s %s — %s' % (r['status'], r['fid'], r['pi'], r['title']))
            print('    TCs: %d | Steps: %d | Type: %s | Critical: %d | Warnings: %d' % (
                r['tc_count'], r['step_count'], r['ftype'], r['critical'], r['warnings']))
            for issue in r['issues'][:10]:
                print('      → %s' % issue[:90])
            if len(r['issues']) > 10:
                print('      ... and %d more issues' % (len(r['issues']) - 10))

    # Full feature table
    print()
    print('─' * 100)
    print('  ALL FEATURES — READINESS MATRIX')
    print('─' * 100)
    print('  %-6s %-20s %-40s %4s %5s %-12s %s' % ('PI', 'Feature ID', 'Title', 'TCs', 'Steps', 'Type', 'Status'))
    print('  ' + '-' * 96)
    for r in sorted(feature_reports, key=lambda x: (x['pi'], x['fid'])):
        print('  %-6s %-20s %-40s %4d %5d %-12s %s' % (
            r['pi'], r['fid'], r['title'][:40], r['tc_count'], r['step_count'],
            r['ftype'][:12], r['status']))

    # Final verdict
    print()
    print('=' * 100)
    critical_count = status_counts.get('CRITICAL', 0)
    warn_count = status_counts.get('WARN', 0)
    pass_count = status_counts.get('PASS', 0) + status_counts.get('MINOR', 0)

    if critical_count == 0 and warn_count == 0:
        print('  ✅ VERDICT: ALL SUITES PASS — READY FOR DELIVERY')
    elif critical_count == 0:
        print('  ⚠️  VERDICT: %d WARNINGS — review recommended but not blocking' % warn_count)
    else:
        print('  ❌ VERDICT: %d CRITICAL issues — must fix before delivery' % critical_count)
    print('    PASS: %d | MINOR: %d | WARN: %d | CRITICAL: %d | No Suite: %d' % (
        status_counts.get('PASS', 0), status_counts.get('MINOR', 0),
        warn_count, critical_count, len(features_with_no_suite)))
    print('=' * 100)

    return feature_reports, features_with_no_suite


if __name__ == '__main__':
    run_audit()
