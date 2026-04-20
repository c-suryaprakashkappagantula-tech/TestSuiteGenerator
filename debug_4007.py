"""Debug: rebuild 4007 with inquiry template."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4007' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4007', row['pi_label'])
jira_data = load_jira('MWTGPROV-4007')
jira = JiraIssue(key='MWTGPROV-4007', summary=jira_data.get('summary', ''),
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
print('4007 REBUILT: %d TCs | %d steps' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print()
for tc in suite.test_cases:
    name = tc.summary.split('_', 2)[-1] if '_' in tc.summary else tc.summary
    print('TC%-3s [%-10s] %s' % (tc.sno, tc.category[:10], name[:70]))
    for s in tc.steps[:2]:
        print('      S%s: %s' % (s.step_num, s.summary[:65]))
    print()
