"""Quick audit of freshly generated PI-53 suites from DB."""
import sys, os, re
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_latest_suite

VALID_CATEGORIES = {'Happy Path', 'Positive', 'Negative', 'Edge Case', 'Edge Cases',
                    'E2E', 'End-to-End', 'Rollback', 'Timeout', 'Audit', 'Regression'}
GENERIC_EXPECTED = {'step completed successfully', 'completed', 'success', 'pass', 'ok',
                    'step completed', 'completed successfully'}

all_features = load_all_features()
pi53_feats = all_features.get('PI-53', [])

print('=' * 110)
print('  PI-53 FRESH GENERATION AUDIT — %d features' % len(pi53_feats))
print('=' * 110)
print()

total_tcs = 0
total_steps = 0
total_issues = 0
no_suite = []
results = []

for fid, title in sorted(pi53_feats):
    suite = load_latest_suite(fid)
    if not suite:
        no_suite.append((fid, title))
        continue

    tcs = suite.get('test_cases', [])
    tc_count = len(tcs)
    step_count = sum(len(tc.get('steps', [])) for tc in tcs)
    total_tcs += tc_count
    total_steps += step_count

    issues = []
    for tc in tcs:
        s = tc.get('summary', '') or ''
        d = tc.get('description', '') or ''
        p = tc.get('preconditions', '') or ''
        cat = tc.get('category', '') or ''
        steps = tc.get('steps', [])

        # Summary: trailing dots
        if s and s[-1] in '.;,!?:':
            issues.append('TC%s: trailing "%s"' % (tc.get('sno', '?'), s[-1]))
        # Empty description
        if not d or len(d) < 10:
            issues.append('TC%s: empty desc' % tc.get('sno', '?'))
        # Empty preconditions
        if not p or len(p.strip()) < 5:
            issues.append('TC%s: empty precon' % tc.get('sno', '?'))
        # Bad category
        if cat and cat not in VALID_CATEGORIES:
            issues.append('TC%s: bad cat "%s"' % (tc.get('sno', '?'), cat))
        # No steps
        if not steps:
            issues.append('TC%s: NO steps' % tc.get('sno', '?'))
        elif len(steps) < 2:
            issues.append('TC%s: only %d step' % (tc.get('sno', '?'), len(steps)))
        # Generic expected
        for step in steps:
            exp = (step.get('expected', '') or '').strip().lower()
            if exp in GENERIC_EXPECTED:
                issues.append('TC%s Step%s: generic exp' % (tc.get('sno', '?'), step.get('step_num', '?')))

    total_issues += len(issues)
    critical = sum(1 for i in issues if any(kw in i for kw in ['empty', 'NO steps', 'bad cat']))
    status = 'PASS' if not issues else ('CRITICAL' if critical > 0 else 'MINOR')

    results.append({
        'fid': fid, 'title': title[:45], 'tcs': tc_count,
        'steps': step_count, 'issues': len(issues), 'status': status,
        'detail': issues[:5]
    })

# Print results
print('  %-18s %-45s %4s %5s %4s %s' % ('Feature', 'Title', 'TCs', 'Steps', 'Iss', 'Status'))
print('  ' + '-' * 105)
for r in results:
    marker = '✅' if r['status'] == 'PASS' else ('⚠️ ' if r['status'] == 'MINOR' else '❌')
    print('  %-18s %-45s %4d %5d %4d %s %s' % (
        r['fid'], r['title'], r['tcs'], r['steps'], r['issues'], marker, r['status']))

# Summary
print()
print('  ' + '-' * 105)
pass_count = sum(1 for r in results if r['status'] == 'PASS')
minor_count = sum(1 for r in results if r['status'] == 'MINOR')
critical_count = sum(1 for r in results if r['status'] == 'CRITICAL')
print('  TOTAL: %d features | %d TCs | %d steps | %d issues' % (len(results), total_tcs, total_steps, total_issues))
print('  PASS: %d | MINOR: %d | CRITICAL: %d | No Suite: %d' % (pass_count, minor_count, critical_count, len(no_suite)))

if no_suite:
    print()
    print('  NO SUITE:')
    for fid, title in no_suite:
        print('    %s %s' % (fid, title[:50]))

# Show issues for non-PASS features
print()
non_pass = [r for r in results if r['status'] != 'PASS']
if non_pass:
    print('  ISSUES DETAIL:')
    for r in non_pass:
        print('    %s (%d issues):' % (r['fid'], r['issues']))
        for iss in r['detail']:
            print('      - %s' % iss)
else:
    print('  🎉 ALL PI-53 FEATURES PASS — ZERO ISSUES')

print()
print('=' * 110)
if critical_count == 0 and total_issues <= 10:
    print('  VERDICT: PI-53 is READY ✅')
elif critical_count == 0:
    print('  VERDICT: PI-53 has MINOR issues (%d) — review recommended' % total_issues)
else:
    print('  VERDICT: PI-53 has %d CRITICAL issues — fix required' % critical_count)
print('=' * 110)
