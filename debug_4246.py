"""Debug: reproduce the Set changed size during iteration error for 4246."""
import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue
from modules.excel_generator import generate_excel

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4246' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4246', row['pi_label'])
jira_data = load_jira('MWTGPROV-4246')

jira = JiraIssue(
    key='MWTGPROV-4246', summary=jira_data.get('summary', ''),
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

try:
    print('Building suite...')
    suite = build_test_suite(jira, chalk, [], options, log=print)
    print('Suite built: %d TCs' % len(suite.test_cases))
    
    print('Generating Excel...')
    out = generate_excel(suite, log=print)
    print('Excel generated: %s' % out)
    
except Exception as e:
    print()
    print('ERROR: %s' % e)
    print()
    traceback.print_exc()
