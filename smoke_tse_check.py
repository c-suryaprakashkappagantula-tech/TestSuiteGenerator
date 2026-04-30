"""Check TSE capability for all 7 TCs of MWTGPROV-4190."""
import sys, json
sys.path.insert(0, '.')

from modules.database import load_jira, load_chalk_as_object
from modules.jira_fetcher import JiraIssue, JiraAttachment
from modules.test_engine import build_test_suite

fid = 'MWTGPROV-4190'
cached = load_jira(fid)
chalk = load_chalk_as_object(fid, 'PI-52')
jira = JiraIssue(key=cached['feature_id'], summary=cached['summary'],
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
options = {'channel': ['ITMBO','NBOP'], 'devices': ['Mobile'], 'networks': ['4G','5G'],
    'sim_types': ['eSIM','pSIM'], 'os_platforms': ['iOS','Android'],
    'include_positive': True, 'include_negative': True, 'include_e2e': True,
    'include_edge': True, 'include_attachments': True,
    'strategy': 'Smart Suite (Recommended)', 'custom_instructions': ''}
suite = build_test_suite(jira, chalk, [], options, log=lambda m: None)

# TSE classification — use the ACTUAL StepMapperV2 (not IntentClassifier)
sys.path.insert(0, '../TestSuiteExecutor')
from TestSuiteExecutor.modules.step_mapper import StepMapperV2
from TestSuiteExecutor.modules.models import LoadedSuite, LoadedTestCase, LoadedStep
from TestSuiteExecutor.modules.step_handler_registry import StepHandlerRegistry

mapper = StepMapperV2()
reg = StepHandlerRegistry()

total_steps = 0
handled_steps = 0
missing = []

for tc in suite.test_cases:
    # Build a LoadedTestCase for the mapper
    loaded_steps = [LoadedStep(step_num=s.step_num, summary=s.summary, expected_result=s.expected)
                    for s in tc.steps]
    loaded_tc = LoadedTestCase(sno=tc.sno, summary=tc.summary, description=tc.description or '',
                                preconditions=tc.preconditions or '', steps=loaded_steps)
    mini_suite = LoadedSuite(feature_id='MWTGPROV-4190', feature_title='Change BCD', pi='PI-52',
                              test_cases=[loaded_tc])

    # Get execution plan from the REAL mapper
    plan = mapper.get_execution_plan(mini_suite)
    exec_steps = plan[0][1] if plan and plan[0][1] else []

    s = tc.summary.encode('ascii', 'replace').decode()[:75]
    print('TC%s [%s]: %s' % (tc.sno, tc.category, s))

    for step in exec_steps:
        total_steps += 1
        action_type = step.action_type
        api_action = step.api_action
        handler = reg.get_handler(api_action) or reg.get_handler(action_type)
        hname = type(handler).__name__ if handler else 'NONE'
        ok = 'Y' if handler else 'X'
        if handler:
            handled_steps += 1
        else:
            missing.append((tc.sno, step.step_num, step.original_text[:60], action_type, api_action))
        step_s = step.original_text.encode('ascii', 'replace').decode()[:50]
        print('  [%s] Step %d: %-50s type=%-20s action=%-20s -> %s' % (ok, step.step_num, step_s, action_type, api_action, hname))
    print()

print('=' * 80)
print('TOTAL: %d/%d steps have handlers (%.0f%%)' % (handled_steps, total_steps, handled_steps/total_steps*100 if total_steps else 0))
if missing:
    print()
    print('MISSING HANDLERS (%d):' % len(missing))
    for tc_sno, snum, text, atype, aaction in missing:
        print('  TC%s Step %d: %s -> type=%s action=%s' % (tc_sno, snum, text, atype, aaction))
