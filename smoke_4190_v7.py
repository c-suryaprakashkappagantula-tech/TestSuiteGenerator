"""Smoke test for MWTGPROV-4190 using DB caches — no browser needed."""
import sys, json
sys.path.insert(0, '.')
from modules.database import load_jira, load_chalk_as_object
from modules.jira_fetcher import JiraIssue, JiraAttachment
from modules.test_engine import build_test_suite

fid = 'MWTGPROV-4190'
cached = load_jira(fid)
chalk = load_chalk_as_object(fid, 'PI-52')

jira = JiraIssue(
    key=cached['feature_id'], summary=cached['summary'],
    description=cached['description'] or '', status=cached['status'],
    priority=cached['priority'], assignee=cached['assignee'],
    reporter=cached['reporter'], labels=json.loads(cached['labels_json']),
    acceptance_criteria=cached['ac_text'] or '',
    attachments=[JiraAttachment(filename=a.get('filename',''), url=a.get('url',''), size=a.get('size',0))
                 for a in json.loads(cached['attachments_json'])],
    linked_issues=json.loads(cached['links_json']),
    subtasks=json.loads(cached['subtasks_json']),
    comments=json.loads(cached['comments_json']),
    pi=cached['pi'], channel=cached['channel'],
    raw_json=json.loads(cached['raw_json']) if cached.get('raw_json') else {},
)

options = {
    'channel': ['ITMBO', 'NBOP'], 'devices': ['Mobile'],
    'networks': ['4G', '5G'], 'sim_types': ['eSIM', 'pSIM'],
    'os_platforms': ['iOS', 'Android'],
    'include_positive': True, 'include_negative': True,
    'include_e2e': True, 'include_edge': True,
    'include_attachments': True,
    'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
}

logs = []
suite = build_test_suite(jira, chalk, [], options, log=lambda m: logs.append(m))

print('Feature: %s' % jira.summary)
print('TCs: %d' % len(suite.test_cases))
print()

for tc in suite.test_cases:
    print('=' * 70)
    print('TC%s: %s' % (tc.sno, tc.summary.encode('ascii', 'replace').decode()))
    print('Category: %s' % tc.category)
    precon = (tc.preconditions or 'N/A').replace('\n', ' | ')[:150]
    print('Preconditions: %s' % precon.encode('ascii', 'replace').decode())
    desc = (tc.description or 'N/A')[:150]
    print('Description: %s' % desc.encode('ascii', 'replace').decode())
    print('Steps (%d):' % len(tc.steps))
    for s in tc.steps:
        step_text = s.summary.encode('ascii', 'replace').decode()[:90]
        exp_text = s.expected.encode('ascii', 'replace').decode()[:80]
        print('  Step %d: %s' % (s.step_num, step_text))
        print('       => %s' % exp_text)
    print()

# Print key engine logs
print('=' * 70)
print('KEY ENGINE LOGS:')
for l in logs:
    if any(kw in l for kw in ['[REJECT]', '[DEDUP]', 'Sorted', 'Removed', 'wrong-feature',
                                'near-duplicate', 'Step 9', 'Step 3', '[OK]', 'WARNING']):
        print('  %s' % l.encode('ascii', 'replace').decode()[:120])
