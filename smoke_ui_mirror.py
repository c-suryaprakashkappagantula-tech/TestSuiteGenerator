"""Smoke test: UI Mirror layer on 4009, 4254, 4152, 3949"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

FEATURES = ['MWTGPROV-4009', 'MWTGPROV-4254', 'MWTGPROV-4152', 'MWTGPROV-3949']

for fid in FEATURES:
    c = _conn()
    row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
    c.close()
    if not row:
        print('%s: No chalk data' % fid)
        continue

    chalk = load_chalk_as_object(fid, row['pi_label'])
    jira_data = load_jira(fid)
    if not jira_data:
        print('%s: No jira data' % fid)
        continue

    jira = JiraIssue(
        key=fid, summary=jira_data.get('summary', ''),
        description=jira_data.get('description', ''), status=jira_data.get('status', ''),
        priority=jira_data.get('priority', ''), issue_type='Story',
        assignee=jira_data.get('assignee', ''), reporter=jira_data.get('reporter', ''),
        labels=json.loads(jira_data.get('labels_json', '[]')),
        pi=jira_data.get('pi', row['pi_label']), channel=jira_data.get('channel', ''),
        acceptance_criteria=jira_data.get('ac_text', ''),
        attachments=[], linked_issues=json.loads(jira_data.get('links_json', '[]')),
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

    # Capture log to find UI mirror entries
    logs = []
    def capture_log(msg):
        logs.append(msg)

    suite = build_test_suite(jira, chalk, [], options, log=capture_log)

    # Find UI mirror TCs
    ui_tcs = [tc for tc in suite.test_cases if 'UI Verify' in tc.summary]
    mirror_logs = [l for l in logs if 'UI-MIRROR' in l]

    print('━' * 90)
    print('%s — %s' % (fid, jira.summary[:60]))
    print('  Total TCs: %d | UI Mirror TCs: %d' % (len(suite.test_cases), len(ui_tcs)))

    if ui_tcs:
        for tc in ui_tcs:
            print('  ✅ %s' % tc.summary[:80])
            print('     Steps: %d | Precon: %s' % (len(tc.steps), 'Y' if tc.preconditions else 'N'))
            for step in tc.steps[:2]:
                print('     Step %s: %s' % (step.step_num, step.summary[:65]))
    else:
        # Show why no UI mirror
        for l in mirror_logs:
            print('  %s' % l)
    print()

print('Done.')
