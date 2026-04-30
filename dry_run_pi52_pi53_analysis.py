"""Batch dry-run: regenerate ALL PI-52 + PI-53 features from DB cache.
Compare TC counts and flag issues."""
import sys, re, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_jira, load_chalk_as_object
from modules.jira_fetcher import JiraIssue
from modules.test_engine import build_test_suite

all_features = load_all_features()
results = []

for pi in ['PI-52', 'PI-53']:
    features = all_features.get(pi, [])
    if not features:
        print(f'[SKIP] {pi}: no features in DB')
        continue
    print(f'\n{"="*70}')
    print(f'  {pi}: {len(features)} features')
    print(f'{"="*70}')

    for fid, ftitle in features:
        jira_raw = load_jira(fid)
        if not jira_raw:
            print(f'  [SKIP] {fid}: no Jira in DB')
            continue

        jira = JiraIssue(key=jira_raw.get('key', fid))
        for k in ('summary','description','acceptance_criteria','status','priority',
                   'assignee','reporter','labels','channel','pi','linked_issues',
                   'attachments','comments','subtasks'):
            setattr(jira, k, jira_raw.get(k, getattr(jira, k)))

        chalk = load_chalk_as_object(fid, pi)
        chalk_scenarios = len(chalk.scenarios) if chalk and chalk.scenarios else 0

        options = {
            'channel': ['ITMBO', 'NBOP'], 'devices': ['Mobile'],
            'networks': ['4G', '5G'], 'sim_types': ['eSIM', 'pSIM'],
            'os_platforms': ['iOS', 'Android'],
            'include_positive': True, 'include_negative': True,
            'include_e2e': True, 'include_edge': True,
            'include_attachments': True,
            'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
        }

        try:
            suite = build_test_suite(jira, chalk, [], options, log=lambda m: None)
            tc_count = len(suite.test_cases)
            step_count = sum(len(tc.steps) for tc in suite.test_cases)

            # Check for issues
            issues = []
            # 1. Syniverse NOT called on non-relevant features
            syn_tcs = [tc for tc in suite.test_cases if 'syniverse' in tc.summary.lower() and 'not' in tc.summary.lower()]
            if syn_tcs:
                issues.append(f'SYN_NOT:{len(syn_tcs)}')
            # 2. Generic subscriber profile TCs
            profile_tcs = [tc for tc in suite.test_cases if 'subscriber profile' in tc.summary.lower() and 'display' in tc.summary.lower()]
            if profile_tcs:
                issues.append(f'PROFILE:{len(profile_tcs)}')
            # 3. Duplicate step signatures
            step_sigs = []
            for tc in suite.test_cases:
                sig = frozenset(re.sub(r'\s+', ' ', s.summary).strip().lower()[:60] for s in tc.steps if s.summary)
                step_sigs.append(sig)
            from collections import Counter
            sig_counts = Counter(step_sigs)
            dups = sum(1 for c in sig_counts.values() if c > 1)
            if dups:
                issues.append(f'STEP_DUP:{dups}')
            # 4. Categories
            cats = {}
            for tc in suite.test_cases:
                cats[tc.category] = cats.get(tc.category, 0) + 1

            issue_str = ' | '.join(issues) if issues else 'CLEAN'
            cat_str = ' '.join(f'{k}:{v}' for k, v in sorted(cats.items()))

            results.append({
                'pi': pi, 'fid': fid, 'title': ftitle[:50],
                'chalk': chalk_scenarios, 'tcs': tc_count, 'steps': step_count,
                'issues': issue_str, 'cats': cat_str,
            })
            flag = '⚠️' if issues else '✅'
            print(f'  {flag} {fid}: {tc_count} TCs, {step_count} steps | Chalk:{chalk_scenarios} | {issue_str} | {cat_str}')

        except Exception as e:
            print(f'  ❌ {fid}: ERROR — {str(e)[:80]}')
            results.append({'pi': pi, 'fid': fid, 'title': ftitle[:50],
                           'chalk': chalk_scenarios, 'tcs': 0, 'steps': 0,
                           'issues': f'ERROR:{str(e)[:50]}', 'cats': ''})

# Summary
print(f'\n{"="*70}')
print(f'  SUMMARY')
print(f'{"="*70}')
total_features = len(results)
total_tcs = sum(r['tcs'] for r in results)
total_steps = sum(r['steps'] for r in results)
clean = sum(1 for r in results if r['issues'] == 'CLEAN')
syn_issues = sum(1 for r in results if 'SYN_NOT' in r['issues'])
profile_issues = sum(1 for r in results if 'PROFILE' in r['issues'])
dup_issues = sum(1 for r in results if 'STEP_DUP' in r['issues'])
errors = sum(1 for r in results if 'ERROR' in r['issues'])

print(f'  Features analyzed: {total_features}')
print(f'  Total TCs: {total_tcs}')
print(f'  Total Steps: {total_steps}')
print(f'  Clean (no issues): {clean}/{total_features}')
print(f'  Syniverse NOT called issues: {syn_issues}')
print(f'  Generic profile issues: {profile_issues}')
print(f'  Step duplicate issues: {dup_issues}')
print(f'  Errors: {errors}')
print(f'  Avg TCs/feature: {total_tcs/max(total_features,1):.1f}')
print(f'  Avg Steps/feature: {total_steps/max(total_features,1):.1f}')
