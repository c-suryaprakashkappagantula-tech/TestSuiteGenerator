"""Dry-run MWTGPROV-4379 through the V7 test_engine (CR mode) to compare with V8 output."""
import sys, json
sys.path.insert(0, '.')
from modules.database import load_jira
from modules.test_engine import build_test_suite, TestSuite

raw = load_jira('MWTGPROV-4379')
if not raw:
    print('ERROR: MWTGPROV-4379 not in DB cache')
    sys.exit(1)

# build_test_suite expects an object with attributes — create a simple namespace
class JiraObj:
    pass

jira = JiraObj()
jira.key = raw.get('feature_id', 'MWTGPROV-4379')
jira.summary = raw.get('summary', '')
jira.description = raw.get('description', '')
jira.acceptance_criteria = raw.get('ac_text', '')
jira.status = raw.get('status', '')
jira.priority = raw.get('priority', '')
jira.labels = json.loads(raw.get('labels_json', '[]')) if raw.get('labels_json') else []
jira.subtasks = json.loads(raw.get('subtasks_json', '[]')) if raw.get('subtasks_json') else []
jira.comments = json.loads(raw.get('comments_json', '[]')) if raw.get('comments_json') else []
jira.attachments = json.loads(raw.get('attachments_json', '[]')) if raw.get('attachments_json') else []
jira.attachment_names = [a.get('filename', '') for a in jira.attachments] if jira.attachments else []
jira.pi = raw.get('pi', '')
jira.channel = raw.get('channel', '')
jira.links = json.loads(raw.get('links_json', '[]')) if raw.get('links_json') else []
jira.assignee = raw.get('assignee', '')
jira.reporter = raw.get('reporter', '')
jira.linked_issues = jira.links
jira.issue_type = 'Story'  # Default — CR detection uses summary keywords

print(f'Feature: {jira.summary}')
print(f'PI: {jira.pi} | Channel: {jira.channel}')
print(f'AC: {jira.acceptance_criteria[:200]}...')
print(f'Subtasks: {len(jira.subtasks)}')
for st in jira.subtasks:
    print(f'  - {st.get("key","")} | {st.get("summary","")[:60]}')
print()

# Build through V7 engine
suite = build_test_suite(jira, chalk=None, parsed_docs=[], options={}, log=print)

# Generate Excel output
from modules.excel_generator import generate_excel
from modules.config import OUTPUTS, ts_short

output_path = generate_excel(suite, log=print)
print(f'\n{"="*70}')
print(f'  EXCEL GENERATED: {output_path}')
print(f'{"="*70}')

print(f'\n  V7 ENGINE RESULT: {len(suite.test_cases)} TCs')
print(f'{"="*70}')
for i, tc in enumerate(suite.test_cases):
    print(f'\n  TC{i+1:02d} [{tc.category:<12}] {tc.summary[:80]}')
    print(f'       Steps: {len(tc.steps)}')
    for s in tc.steps[:3]:
        print(f'         S{s.step_num}. {s.summary[:70]}')
    if len(tc.steps) > 3:
        print(f'         ... +{len(tc.steps)-3} more steps')
