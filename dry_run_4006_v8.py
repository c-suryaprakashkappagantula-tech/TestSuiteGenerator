"""
Dry-run: Build test suite for MWTGPROV-4006 using V8.0 Data-First Engine.
Tests the UI routing path: [NBOP] → hide page → UI classification → scenario-to-TC mapping.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import _conn, init_db, load_chalk_as_object
from modules.jira_fetcher import JiraIssue
from modules.data_first_engine import build_test_suite_v8
from modules.tc_builder import classify_feature

init_db()

# ================================================================
# LOAD FROM DB CACHE
# ================================================================
print('=' * 70)
print('V8.0 DRY RUN: MWTGPROV-4006 — NBOP Hide Port-in Status (UI)')
print('=' * 70)

c = _conn()

# Load Jira
jira_row = c.execute(
    "SELECT * FROM jira_cache WHERE feature_id LIKE '%4006%'"
).fetchone()

# Load Chalk
chalk_rows = c.execute(
    "SELECT feature_id, pi_label, scope, scenarios_json FROM chalk_cache WHERE feature_id LIKE '%4006%'"
).fetchall()

c.close()

# ── Build Jira object ──
if jira_row:
    d = dict(jira_row)
    jira = JiraIssue(
        key=d['feature_id'],
        summary=d.get('summary', ''),
        description=d.get('description', ''),
        acceptance_criteria=d.get('ac_text', ''),
        channel=d.get('channel', 'NBOP'),
        pi=d.get('pi', ''),
        status=d.get('status', ''),
        priority=d.get('priority', ''),
        labels=json.loads(d.get('labels_json', '[]')),
        subtasks=json.loads(d.get('subtasks_json', '[]')),
    )
    print('\nJira loaded from DB: %s' % jira.key)
    print('  Summary: %s' % jira.summary[:80])
    print('  AC length: %d chars' % len(jira.acceptance_criteria or ''))
    print('  Channel: %s' % jira.channel)
else:
    # Fallback: use known data for MWTGPROV-4006
    print('\nNo Jira cache found — using known feature data')
    jira = JiraIssue(
        key='MWTGPROV-4006',
        summary='[NBOP]: 54.2 NBOP to hide the page Port-in status for TMO only',
        description='Hide the Port-in Status page in NBOP portal for TMO subscribers. VZW subscribers should still see it.',
        status='In Progress', priority='Medium', issue_type='Story',
        labels=['MWTGPROV-4006', 'NBOP', 'UI'],
        pi='PI-54.2', channel='NBOP',
        acceptance_criteria="""
1. NBOP shall hide the Port-in Status page/tab for TMO subscribers
2. Port-in Status page should NOT be visible when subscriber MNO = TMO
3. Port-in Status page should remain visible for VZW subscribers
4. Navigation path: Edit > Subscriber Profile > Port-in Status
5. The hide behavior applies to all user roles (Admin, MCS1, GEN, MCSSSUP)
6. No error should be thrown — the page simply does not appear in navigation
""",
        subtasks=[],
    )

# ── Test classification FIRST ──
print('\n' + '-' * 70)
print('CLASSIFICATION TEST')
print('-' * 70)
result = classify_feature(jira.summary, jira.acceptance_criteria or '')
print('  Summary: %s' % jira.summary[:70])
print('  Classification: %s' % result.classification)
print('  Confidence: %.2f' % result.confidence)
print('  API keywords: %s' % result.api_keywords_found)
print('  UI keywords: %s' % result.ui_keywords_found)
assert result.classification == 'ui', 'EXPECTED ui, GOT %s' % result.classification
print('  ✓ Correctly classified as UI')

# ── Build Chalk object ──
chalk = None
if chalk_rows:
    best_row = dict(chalk_rows[0])
    chalk = load_chalk_as_object(best_row['feature_id'], best_row['pi_label'])
    if chalk:
        print('\nChalk loaded: %s (%s)' % (best_row['feature_id'], best_row['pi_label']))
        print('  Scenarios: %d' % len(chalk.scenarios))
else:
    print('\nNo Chalk cache found — proceeding without Chalk data')

# ── Build DeepMineResult ──
from modules.deep_miner import DeepMineResult, _mine_subtask
subtask_mines = []
for st in jira.subtasks:
    mine = _mine_subtask(st, log=lambda x: None)
    subtask_mines.append(mine)
deep_mine_result = DeepMineResult(
    feature_id=jira.key,
    subtask_mines=subtask_mines,
    data_sources_used=['Jira subtasks'] if subtask_mines else ['Jira'],
)
if subtask_mines:
    print('\nSubtask mines: %d' % len(subtask_mines))
    for m in subtask_mines:
        print('  %s: %d AC items' % (m.key, len(m.ac_items)))

# ================================================================
# RUN V8.0 ENGINE
# ================================================================
print('\n' + '=' * 70)
print('RUNNING V8.0 DATA-FIRST ENGINE (UI PATH)')
print('=' * 70 + '\n')

options = {
    'channel': ['NBOP'],
    'engine_version': '8',
    'custom_instructions': '',
}

suite = build_test_suite_v8(jira, chalk, [], options, deep_mine_result, log=print)

# ================================================================
# ANALYSIS
# ================================================================
print('\n' + '=' * 70)
print('V8.0 OUTPUT ANALYSIS — UI ROUTING')
print('=' * 70)

print('\nSuite Summary:')
print('  Feature: %s' % suite.feature_id)
print('  Engine: %s' % suite.engine_version)
print('  Test Cases: %d' % len(suite.test_cases))
print('  Total Steps: %d' % sum(len(tc.steps) for tc in suite.test_cases))

print('\nData Inventory:')
for src in suite.data_inventory.sources:
    print('  %-20s | %-8s | %d items | %s' % (src.source_name[:20], src.source_type, src.items_extracted, src.status))
print('  Total testable items: %d' % suite.data_inventory.total_testable_items)

print('\nCombination Plan:')
print('  Independent dimensions: %d' % len(suite.combination_plan.independent_dimensions))
print('  Scenario TCs: %d' % len(suite.combination_plan.scenario_tcs))
print('  Negative TCs: %d' % len(suite.combination_plan.negative_tcs))

print('\nTest Cases:')
print('  %-4s %-12s %-70s' % ('S.No', 'Category', 'Summary'))
print('  ' + '-' * 86)
for tc in suite.test_cases:
    print('  %-4s %-12s %-70s' % (tc.sno, tc.category, tc.summary[:70]))

# ── Detailed step analysis ──
print('\nDetailed Steps (first 3 TCs):')
for tc in suite.test_cases[:3]:
    print('\n  TC: %s' % tc.summary[:60])
    print('  Category: %s | Steps: %d' % (tc.category, len(tc.steps)))
    for s in tc.steps:
        print('    %d. %s' % (s.step_num, s.summary[:75]))
        print('       -> %s' % s.expected[:75])

# ── UI Routing Validation ──
print('\n' + '-' * 70)
print('UI ROUTING VALIDATION')
print('-' * 70)

checks = {
    'Classification = ui': result.classification == 'ui',
    'Confidence >= 0.85': result.confidence >= 0.85,
    'TC count >= 2': len(suite.test_cases) >= 2,
    'TC count <= 8': len(suite.test_cases) <= 8,
}

# Check if TCs have NBOP navigation steps
has_login_step = any(
    any('login' in s.summary.lower() or 'nbop' in s.summary.lower() for s in tc.steps)
    for tc in suite.test_cases
)
checks['Has NBOP login/nav steps'] = has_login_step

# Check if TCs have element verification
has_verify_step = any(
    any('verify' in s.summary.lower() or 'displayed' in s.summary.lower() or 'not displayed' in s.summary.lower() for s in tc.steps)
    for tc in suite.test_cases
)
checks['Has element verification steps'] = has_verify_step

# Check traceability
all_traced = all(tc.traceability is not None for tc in suite.test_cases)
checks['All TCs have traceability'] = all_traced

print()
all_pass = True
for name, passed in checks.items():
    status = '✓ PASS' if passed else '✗ FAIL'
    if not passed:
        all_pass = False
    print('  %s  %s' % (status, name))

print('\n  Result: %s' % ('ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'))

print('\n' + '=' * 70)
print('DRY RUN COMPLETE')
print('=' * 70)
