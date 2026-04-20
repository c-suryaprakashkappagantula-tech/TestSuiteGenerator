"""
audit_chalk_vs_steps.py — Chalk Scenario vs Generated Steps Cross-Check
=========================================================================
For every PI-53 feature: rebuild suite, then for each Chalk scenario,
check if the generated TC steps actually match the scenario's INTENT.

Flags:
  - Chalk says "Syniverse SwapIMSI" but steps don't mention SwapIMSI
  - Chalk says "ICCID changes" but steps don't mention ICCID
  - Chalk says "no changes" but steps mention changes
  - Chalk says "ITMBO and EMM notified" but steps don't mention ITMBO/EMM
  - Steps use wrong template (notification steps for API feature, etc.)
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

# Key assertions that MUST appear in steps if Chalk mentions them
CHALK_TO_STEP_CHECKS = [
    # (chalk_keyword, step_must_contain, description)
    ('syniverse', 'syniverse', 'Syniverse call mentioned in Chalk but not in steps'),
    ('removesubscriber', 'removesubscriber', 'RemoveSubscriber in Chalk but not in steps'),
    ('createsubscriber', 'createsubscriber', 'CreateSubscriber in Chalk but not in steps'),
    ('swapimsi', 'swapimsi', 'SwapIMSI in Chalk but not in steps'),
    ('swap imsi', 'swapimsi', 'Swap IMSI in Chalk but not in steps'),
    ('iccid changes', 'iccid', 'ICCID changes in Chalk but not in steps'),
    ('iccid does not change', 'no', 'ICCID does not change but steps dont assert no-change'),
    ('wholesale plan', 'wholesale', 'Wholesale plan in Chalk but not in steps'),
    ('itmbo and emm', 'itmbo', 'ITMBO/EMM notification in Chalk but not in steps'),
    ('no changes', 'no', 'No changes in Chalk but steps dont assert no-change'),
    ('smartwatch', 'smartwatch', 'Smartwatch exception in Chalk but not in steps'),
    ('reference number', 'reference', 'Reference Number in Chalk but not in steps'),
    ('requesttype', 'requesttype', 'requestType in Chalk but not in steps'),
    ('networkprovider', 'networkprovider', 'networkProvider in Chalk but not in steps'),
    ('event_messages', 'event_message', 'EVENT_MESSAGES in Chalk but not in steps'),
]

all_features = load_all_features()
pi53_feats = all_features.get('PI-53', [])

print('=' * 110)
print('  CHALK vs STEPS CROSS-CHECK — PI-53 (%d features)' % len(pi53_feats))
print('=' * 110)
print()

total_chalk = 0
total_mismatches = 0
feature_results = []

for fid, title in sorted(pi53_feats):
    c = _conn()
    row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
    c.close()
    if not row:
        continue

    chalk = load_chalk_as_object(fid, row['pi_label'])
    jira_data = load_jira(fid)
    if not jira_data or not chalk:
        continue

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
        print('  %s: BUILD ERROR: %s' % (fid, str(e)[:60]))
        continue

    # For each Chalk scenario, find the matching TC and check steps
    mismatches = []
    for sc in chalk.scenarios:
        total_chalk += 1
        chalk_text = (sc.title + ' ' + (sc.validation or '')).lower()

        # Find matching TC
        matching_tc = None
        for tc in suite.test_cases:
            # Match by title overlap
            tc_title_words = set(re.findall(r'\b\w{5,}\b', tc.summary.lower()))
            chalk_words = set(re.findall(r'\b\w{5,}\b', chalk_text))
            if chalk_words:
                overlap = len(tc_title_words & chalk_words) / len(chalk_words)
                if overlap > 0.4:
                    matching_tc = tc
                    break

        if not matching_tc:
            continue

        # Check if steps match Chalk intent
        all_steps_text = ' '.join(
            (s.summary + ' ' + s.expected).lower() for s in matching_tc.steps
        )

        for chalk_kw, step_kw, desc in CHALK_TO_STEP_CHECKS:
            if chalk_kw in chalk_text and step_kw not in all_steps_text:
                mismatches.append({
                    'chalk_sc': sc.title[:60],
                    'tc': matching_tc.sno,
                    'issue': desc,
                    'chalk_kw': chalk_kw,
                })

    total_mismatches += len(mismatches)
    status = 'PASS' if not mismatches else ('WARN' if len(mismatches) <= 2 else 'FAIL')
    feature_results.append({
        'fid': fid, 'title': title[:45], 'chalk_count': len(chalk.scenarios),
        'tc_count': len(suite.test_cases), 'mismatches': len(mismatches),
        'status': status, 'detail': mismatches[:5],
    })

# Print results
print('%-18s %-45s %4s %4s %4s %s' % ('Feature', 'Title', 'Chk', 'TCs', 'Mis', 'Status'))
print('-' * 110)
for r in feature_results:
    m = 'PASS' if r['status'] == 'PASS' else ('WARN' if r['status'] == 'WARN' else 'FAIL')
    print('%-18s %-45s %4d %4d %4d %s' % (r['fid'], r['title'], r['chalk_count'], r['tc_count'], r['mismatches'], m))

print()
pass_c = sum(1 for r in feature_results if r['status'] == 'PASS')
warn_c = sum(1 for r in feature_results if r['status'] == 'WARN')
fail_c = sum(1 for r in feature_results if r['status'] == 'FAIL')
print('TOTAL: %d features | %d Chalk scenarios | %d mismatches' % (len(feature_results), total_chalk, total_mismatches))
print('PASS: %d | WARN: %d | FAIL: %d' % (pass_c, warn_c, fail_c))

# Detail for non-PASS
non_pass = [r for r in feature_results if r['status'] != 'PASS']
if non_pass:
    print()
    print('MISMATCHES:')
    for r in non_pass:
        print('  %s (%d mismatches):' % (r['fid'], r['mismatches']))
        for d in r['detail']:
            print('    TC%s: %s' % (d['tc'], d['issue']))
            print('      Chalk: "%s"' % d['chalk_kw'])
