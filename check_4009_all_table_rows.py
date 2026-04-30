"""Check which table rows from NSLNM-601 made it into TCs."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4009' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4009', row['pi_label'])
jira_data = load_jira('MWTGPROV-4009')
jira = JiraIssue(key='MWTGPROV-4009', summary=jira_data.get('summary', ''),
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

# Capture logs to find table TC generation
logs = []
def capture(msg):
    logs.append(msg)
suite = build_test_suite(jira, chalk, [], options, log=capture)

# Find table-related logs
print('=== TABLE TC GENERATION LOGS ===')
for l in logs:
    if 'Table TC' in l or 'table' in l.lower():
        print(l)

print()
print('=== ALL TCs with TMO Status or Hotline ===')
for tc in suite.test_cases:
    s = tc.summary.lower()
    if 'tmo status' in s or ('hotline' in s and 'sync' in s):
        print('TC%s: %s' % (tc.sno, tc.summary[:90]))

# Check the 8 expected rows
print()
print('=== COVERAGE OF ALL 8 JIRA TABLE ROWS ===')
all_text = ' '.join(tc.summary.lower() for tc in suite.test_cases)
expected = [
    ('Active', 'Active'), ('Active', 'Deactive'), ('Active', 'Suspend'),
    ('Active', 'Hotline'), ('Deactive', 'Deactive'), ('Deactive', 'Active'),
    ('Deactive', 'Suspend'), ('Deactive', 'Hotline'),
]
for tmo, nsl in expected:
    key = 'tmo status=%s, nsl status=%s' % (tmo.lower(), nsl.lower())
    found = key in all_text
    print('  TMO=%s NSL=%s: %s' % (tmo, nsl, 'FOUND' if found else 'MISSING'))
