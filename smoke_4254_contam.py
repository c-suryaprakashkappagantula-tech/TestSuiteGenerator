"""Quick smoke test: verify 4254 has no NSL/NBOP/Century Report contamination."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

# Load from DB
c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4254' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4254', row['pi_label'])
jira_data = load_jira('MWTGPROV-4254')

jira = JiraIssue(
    key='MWTGPROV-4254', summary=jira_data.get('summary', ''),
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

suite = build_test_suite(jira, chalk, [], options, log=lambda x: None)

print('4254 CONTAMINATION CHECK:')
print('TCs: %d | Steps: %d' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print()

contam_terms = ['nsl ', 'nbop', 'century report', 'service grouping', 'ne portal',
                'mig table', 'mig_', 'succ00', 'http 200', 'http 202']
found_any = False
for tc in suite.test_cases:
    for step in tc.steps:
        all_text = (step.summary + ' ' + step.expected).lower()
        hits = [t for t in contam_terms if t in all_text]
        if hits:
            found_any = True
            print('  TC%s Step %s: CONTAMINATED with %s' % (tc.sno, step.step_num, hits))
            print('    Step: %s' % step.summary[:70])
            print('    Exp:  %s' % step.expected[:70])

    # Also check description
    desc_text = (tc.description or '').lower()
    desc_hits = [t for t in contam_terms if t in desc_text]
    if desc_hits:
        found_any = True
        print('  TC%s DESC: CONTAMINATED with %s' % (tc.sno, desc_hits))
        print('    Desc: %s' % tc.description[:70])

if not found_any:
    print('  ZERO contamination found across all TCs and steps')
    print('  ALL CLEAN')
