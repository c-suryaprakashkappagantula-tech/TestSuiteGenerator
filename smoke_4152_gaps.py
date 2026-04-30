"""Smoke test: verify 4152 gaps are closed — NO Syniverse, Invalid AccountId, Audit-History"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import load_chalk_as_object, load_jira, _conn
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4152' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4152', row['pi_label'])
jira_data = load_jira('MWTGPROV-4152')

jira = JiraIssue(
    key='MWTGPROV-4152', summary=jira_data.get('summary', ''),
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

print('4152 GAP CHECK:')
print('TCs: %d | Steps: %d' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print()

all_summaries = ' '.join(tc.summary.lower() for tc in suite.test_cases)
all_text = ' '.join(
    tc.summary.lower() + ' ' + (tc.description or '').lower() + ' ' +
    ' '.join(s.summary.lower() + ' ' + s.expected.lower() for s in tc.steps)
    for tc in suite.test_cases
)

# Gap 1: Explicit NO Syniverse for Hotline
has_no_syn = ('no syniverse' in all_summaries or
              'syniverse is not' in all_summaries or
              'syniverse not called' in all_summaries or
              'not call syniverse' in all_summaries or
              'not called for hotline' in all_text)
print('Gap 1: Explicit NO Syniverse for Hotline')
if has_no_syn:
    print('  CLOSED')
    # Find the TC
    for tc in suite.test_cases:
        if 'no syniverse' in tc.summary.lower() or 'syniverse is not' in tc.summary.lower() or 'not called' in tc.summary.lower():
            print('  TC%s: %s' % (tc.sno, tc.summary[:80]))
            break
else:
    print('  STILL OPEN')
print()

# Gap 2: Invalid AccountId
has_invalid_account = ('invalid account' in all_text or 'accountid' in all_text)
print('Gap 2: Invalid AccountId negative')
if has_invalid_account:
    print('  CLOSED')
    for tc in suite.test_cases:
        if 'invalid account' in tc.summary.lower() or 'accountid' in tc.summary.lower():
            print('  TC%s: %s' % (tc.sno, tc.summary[:80]))
            break
else:
    print('  STILL OPEN')
print()

# Gap 3: Audit-History Invariant Checklist
has_audit = ('audit' in all_summaries or 'transaction history' in all_summaries or
             'invariant' in all_summaries or 'audit trail' in all_text)
print('Gap 3: Audit-History Invariant Checklist')
if has_audit:
    print('  CLOSED')
    for tc in suite.test_cases:
        if 'audit' in tc.summary.lower() or 'invariant' in tc.summary.lower() or 'transaction history' in tc.summary.lower():
            print('  TC%s: %s' % (tc.sno, tc.summary[:80]))
            break
else:
    print('  STILL OPEN')
print()

# Summary
gaps_closed = sum([has_no_syn, has_invalid_account, has_audit])
print('RESULT: %d/3 gaps closed' % gaps_closed)
if gaps_closed == 3:
    print('ALL 3 GAPS CLOSED — ready to regenerate')
else:
    print('GAPS REMAINING — needs investigation')
