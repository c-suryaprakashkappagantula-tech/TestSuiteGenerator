"""Check if 4152 has Syniverse enquiry/inquiry call coverage."""
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
jira = JiraIssue(key='MWTGPROV-4152', summary=jira_data.get('summary', ''),
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

enquiry_terms = ['enquiry', 'inquiry', 'query subscriber', 'get subscriber', 'lookup',
                 'check subscriber', 'subscriber info', 'subscriber status',
                 'enquir', 'inquir']

print('SYNIVERSE ENQUIRY/INQUIRY SEARCH in 4152 (%d TCs):' % len(suite.test_cases))
print()

found_tcs = []
for tc in suite.test_cases:
    all_text = (tc.summary + ' ' + (tc.description or '') + ' ' +
        ' '.join(s.summary + ' ' + s.expected for s in tc.steps)).lower()
    for term in enquiry_terms:
        if term in all_text:
            found_tcs.append((tc, term))
            break
    # Also check for Syniverse + query/check/verify patterns
    if 'syniverse' in all_text and any(kw in all_text for kw in ['query', 'check status', 'lookup', 'get subscriber']):
        if tc not in [t for t, _ in found_tcs]:
            found_tcs.append((tc, 'syniverse+query pattern'))

if found_tcs:
    print('  FOUND %d TCs with Syniverse enquiry/inquiry:' % len(found_tcs))
    for tc, term in found_tcs:
        print('    TC%s [%s]: %s' % (tc.sno, term, tc.summary[:75]))
else:
    print('  NO Syniverse enquiry/inquiry TC found in the suite')

print()
print('--- CHALK SCENARIOS ---')
for sc in chalk.scenarios:
    sc_text = (sc.title + ' ' + (sc.validation or '') + ' ' + ' '.join(sc.steps)).lower()
    if any(kw in sc_text for kw in enquiry_terms + ['query', 'check subscriber']):
        print('  Chalk: %s' % sc.title[:80])

print()
print('--- JIRA AC ---')
ac = jira_data.get('ac_text', '').lower()
for term in enquiry_terms + ['query subscriber', 'syniverse api call to check']:
    if term in ac:
        print('  AC mentions: "%s"' % term)

print()
print('--- JIRA DESCRIPTION (first 500 chars) ---')
desc = jira_data.get('description', '')[:500].lower()
for term in enquiry_terms + ['query', 'check subscriber', 'syniverse call']:
    if term in desc:
        print('  Description mentions: "%s"' % term)

print()
print('--- INTEGRATION CONTRACT ---')
from modules.integration_contract import resolve_operation, EXTERNAL_SYSTEMS
contract = resolve_operation('Integration with Syniverse', description=jira_data.get('description', ''))
if contract:
    syn_sys = EXTERNAL_SYSTEMS.get('syniverse')
    print('  Contract: %s' % contract.operation)
    print('  Syniverse call_types: %s' % syn_sys.call_types)
    print('  Note: "QuerySubscriber" or "GetSubscriber" is NOT in the call_types list')
