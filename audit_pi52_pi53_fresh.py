"""Audit freshly generated PI-52 & PI-53 suites from DB."""
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

for pi_label in ['PI-52', 'PI-53']:
    feats = all_features.get(pi_label, [])
    print('=' * 110)
    print('  %s FRESH AUDIT — %d features' % (pi_label, len(feats)))
    print('=' * 110)

    total_tcs = 0; total_steps = 0; total_issues = 0
    no_suite = []; results = []

    for fid, title in sorted(feats):
        suite = load_latest_suite(fid)
        if not suite:
            no_suite.append((fid, title)); continue

        tcs = suite.get('test_cases', [])
        tc_count = len(tcs)
        step_count = sum(len(tc.get('steps', [])) for tc in tcs)
        total_tcs += tc_count; total_steps += step_count

        issues = []
        for tc in tcs:
            s = tc.get('summary', '') or ''
            p = tc.get('preconditions', '') or ''
            d = tc.get('description', '') or ''
            cat = tc.get('category', '') or ''
            steps = tc.get('steps', [])

            if s and s[-1] in '.;,!?:': issues.append('TC%s: dot' % tc.get('sno','?'))
            if not d or len(d) < 10: issues.append('TC%s: no desc' % tc.get('sno','?'))
            if not p or len(p.strip()) < 5: issues.append('TC%s: no precon' % tc.get('sno','?'))
            if cat and cat not in VALID_CATEGORIES: issues.append('TC%s: bad cat "%s"' % (tc.get('sno','?'), cat))
            if not steps: issues.append('TC%s: 0 steps' % tc.get('sno','?'))
            elif len(steps) < 2: issues.append('TC%s: 1 step' % tc.get('sno','?'))
            for step in steps:
                exp = (step.get('expected','') or '').strip().lower()
                if exp in GENERIC_EXPECTED: issues.append('TC%s S%s: generic' % (tc.get('sno','?'), step.get('step_num','?')))

        total_issues += len(issues)
        status = 'PASS' if not issues else ('CRITICAL' if any('no desc' in i or '0 steps' in i or 'bad cat' in i for i in issues) else 'MINOR')
        results.append({'fid': fid, 'title': title[:45], 'tcs': tc_count, 'steps': step_count,
                        'issues': len(issues), 'status': status, 'detail': issues[:5]})

    print()
    print('  %-18s %-45s %4s %5s %4s %s' % ('Feature', 'Title', 'TCs', 'Steps', 'Iss', 'Status'))
    print('  ' + '-' * 105)
    for r in results:
        m = '✅' if r['status'] == 'PASS' else ('⚠️ ' if r['status'] == 'MINOR' else '❌')
        print('  %-18s %-45s %4d %5d %4d %s %s' % (r['fid'], r['title'], r['tcs'], r['steps'], r['issues'], m, r['status']))

    pass_c = sum(1 for r in results if r['status'] == 'PASS')
    minor_c = sum(1 for r in results if r['status'] == 'MINOR')
    crit_c = sum(1 for r in results if r['status'] == 'CRITICAL')
    print()
    print('  TOTAL: %d features | %d TCs | %d steps | %d issues' % (len(results), total_tcs, total_steps, total_issues))
    print('  PASS: %d | MINOR: %d | CRITICAL: %d | No Suite: %d' % (pass_c, minor_c, crit_c, len(no_suite)))

    if no_suite:
        print('  NO SUITE: %s' % ', '.join(f[0] for f in no_suite))

    non_pass = [r for r in results if r['status'] != 'PASS']
    if non_pass:
        print()
        print('  ISSUES:')
        for r in non_pass:
            print('    %s (%d): %s' % (r['fid'], r['issues'], '; '.join(r['detail'][:3])))
    print()
