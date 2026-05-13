"""
Dry-run: MWTGPROV-4230 with parsed evidence documents fed into V8 pipeline.
Tests the full flow: Jira + subtasks + evidence docs → dimension extraction → TC generation.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import init_db, _conn
from modules.jira_fetcher import JiraIssue
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.data_first_engine import build_test_suite_v8
from modules.doc_parser import ParsedDoc

init_db()

print('=' * 70)
print('V8.0 DRY RUN: MWTGPROV-4230 + Evidence Documents')
print('=' * 70)

# Load Jira from DB
c = _conn()
jira_row = c.execute("SELECT * FROM jira_cache WHERE feature_id LIKE '%4230%'").fetchone()
c.close()

d = dict(jira_row)
jira = JiraIssue(
    key=d['feature_id'], summary=d.get('summary', ''),
    description=d.get('description', ''),
    acceptance_criteria=d.get('ac_text', ''),
    channel=d.get('channel', 'NBOP'), pi=d.get('pi', ''),
    status=d.get('status', ''), priority=d.get('priority', ''),
    labels=json.loads(d.get('labels_json', '[]')),
    subtasks=json.loads(d.get('subtasks_json', '[]')),
)

# Build deep mine with subtask mining
subtask_mines = []
for st in jira.subtasks:
    mine = _mine_subtask(st, log=lambda x: None)
    subtask_mines.append(mine)
deep_mine = DeepMineResult(feature_id=jira.key, subtask_mines=subtask_mines, data_sources_used=['Jira subtasks'])

# Build parsed docs from evidence
parsed_docs = [
    ParsedDoc(
        filename='MWTGNBOP-5558 Unit testing document.docx',
        file_type='.docx',
        doc_type='Test Reference',
        paragraphs=[
            'MWTGNBOP-5558',
            'NBOP - TMO - Usage Inquiry update',
            'Step 1: Log in to NBOP application and search for an existing Phone, Tablet or Smartwatch TMO lines so that we can able to see line-summary screen.',
            'Step 2: Click on the Data Details menu, so that the page will navigate to Data Details Screen. Ensure the cards and parameters are not visible according to the TMO requirements.',
            'Step 3: Click on the View Historical Usage, so that the page will navigate to Historical Usage Grid. Ensure the cards and parameters are not visible according to the TMO requirements.',
        ],
    ),
    ParsedDoc(
        filename='Test Result MWTGNBOP-5558-Phone.docx',
        file_type='.docx',
        doc_type='Test Reference',
        paragraphs=[
            'MWTGNBOP-5558 : NBOP - TMO - Usage Inquiry update - Phone',
            'Login to NBOP and Navigate to TMO Lines',
            'IMEI - 350277580098101',
            'Verify that it applicable only for Account Type is Commercial and MNO T-Mobile',
            'Verify TMO line access for Admin role only in NBOP',
            'Verify that the Data Details option displayed on the Home/Line Summary screen for TMO line',
            'Verify that the following total usage attributes are removed from the Data Details and Historical Usage screens for TMO',
            'Total MNO Usage',
            'Total HMNO Usage',
            'Total Promo Usage',
            'Total Usage',
            'Threshold',
            'Percentage Used',
            'Verify that the following fields should be displayed under Total Usage (Current Billing Period) on Data Details Usage screen',
            'MNO Data Usage',
            'MNO MHS Data Usage',
            'HMNO Data Usage',
            'HMNO MHS Usage',
            'Promo Data Usage',
            'Promo MHS Usage',
            'No changes for Verizon subscribers, keep displaying the Total Usage attributes for them on both screens.',
        ],
    ),
]

print('\nJira: %s' % jira.summary[:70])
print('Subtasks: %d' % len(jira.subtasks))
print('Parsed docs: %d' % len(parsed_docs))
for doc in parsed_docs:
    print('  - %s (%d paragraphs)' % (doc.filename, len(doc.paragraphs)))

# Run V8 engine WITH parsed docs
print('\n' + '=' * 70)
print('RUNNING V8.0 ENGINE WITH EVIDENCE DOCUMENTS')
print('=' * 70 + '\n')

suite = build_test_suite_v8(jira, None, parsed_docs, {'channel': ['NBOP'], 'engine_version': '8'}, deep_mine, log=print)

# Output
print('\n' + '=' * 70)
print('OUTPUT: %d TCs, %d steps' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print('Route: %s (%.2f)' % (suite.routing_audit.classification, suite.routing_audit.confidence))
print('=' * 70)

print('\nTest Cases:')
print('  %-4s %-12s %-70s' % ('S.No', 'Category', 'Summary'))
print('  ' + '-' * 86)
for tc in suite.test_cases:
    print('  %-4s %-12s %-70s' % (tc.sno, tc.category, tc.summary[:70]))

print('\nData Inventory:')
for src in suite.data_inventory.sources:
    print('  %-20s | %-10s | %d items | %s' % (src.source_name[:20], src.source_type, src.items_extracted, src.status))
print('  Total testable items: %d' % suite.data_inventory.total_testable_items)

print('\nDetailed Steps (first 3 TCs):')
for tc in suite.test_cases[:3]:
    print('\n  %s [%s]' % (tc.summary[:60], tc.category))
    for s in tc.steps:
        print('    %d. %s' % (s.step_num, s.summary[:75]))
        print('       -> %s' % s.expected[:75])

# ── Validation Summary ──
print('\n' + '=' * 70)
print('VALIDATION SUMMARY')
print('=' * 70)
tc_count = len(suite.test_cases)
target = 8
status = 'PASS' if tc_count == target else 'FAIL'
print('  TC Count: %d (target=%d) [%s]' % (tc_count, target, status))

# Check Historical Usage coverage in TC01-TC03
for i, tc in enumerate(suite.test_cases[:3]):
    has_hist = any('historical usage' in s.summary.lower() for s in tc.steps)
    hist_verify = sum(1 for s in tc.steps if 'historical usage screen' in s.summary.lower())
    status = 'PASS' if has_hist and hist_verify >= 6 else 'FAIL'
    print('  TC%02d Historical Usage: nav=%s, verify_steps=%d [%s]' % (
        i + 1, has_hist, hist_verify, status))

# Check TC05 title
tc05 = suite.test_cases[4] if len(suite.test_cases) > 4 else None
if tc05:
    has_correct_title = 'Data_Details_and_Historical_Usage' in tc05.summary
    print('  TC05 Title: %s [%s]' % (
        'correct' if has_correct_title else 'WRONG: ' + tc05.summary[:50],
        'PASS' if has_correct_title else 'FAIL'))

print('=' * 70)

print('\n' + '=' * 70)
print('DRY RUN COMPLETE')
print('=' * 70)
