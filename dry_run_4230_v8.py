"""
Dry-run: Build test suite for MWTGPROV-4230 using V8.0 Data-First Engine.
Loads data from DB cache, runs the full V8 pipeline, and analyzes output.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import _conn, init_db, load_chalk_as_object
from modules.jira_fetcher import JiraIssue
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.data_first_engine import build_test_suite_v8
from modules.tc_builder import classify_feature

init_db()

# ================================================================
# LOAD FROM DB CACHE
# ================================================================
print('=' * 70)
print('V8.0 DRY RUN: MWTGPROV-4230')
print('=' * 70)

c = _conn()

# Load Jira
jira_row = c.execute(
    "SELECT * FROM jira_cache WHERE feature_id LIKE '%4230%'"
).fetchone()

# Load Chalk
chalk_rows = c.execute(
    "SELECT feature_id, pi_label, scope, scenarios_json FROM chalk_cache WHERE feature_id LIKE '%4230%'"
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
        channel=d.get('channel', ''),
        pi=d.get('pi', ''),
        status=d.get('status', ''),
        priority=d.get('priority', ''),
        labels=json.loads(d.get('labels_json', '[]')),
        subtasks=json.loads(d.get('subtasks_json', '[]')),
    )
    print('\nJira loaded: %s' % jira.key)
    print('  Summary: %s' % jira.summary[:80])
    print('  AC length: %d chars' % len(jira.acceptance_criteria or ''))
    print('  Subtasks: %d' % len(jira.subtasks))
    print('  Channel: %s' % jira.channel)
    print('  PI: %s' % jira.pi)
else:
    print('\nERROR: No Jira cache found for MWTGPROV-4230')
    print('  Run the dashboard to fetch this feature first, or check the feature ID.')
    sys.exit(1)

# ── Classification test ──
print('\n' + '-' * 70)
print('CLASSIFICATION TEST')
print('-' * 70)
result = classify_feature(jira.summary, jira.acceptance_criteria or '')
print('  Summary: %s' % jira.summary[:70])
print('  Classification: %s' % result.classification)
print('  Confidence: %.2f' % result.confidence)
print('  API keywords: %s' % result.api_keywords_found)
print('  UI keywords: %s' % result.ui_keywords_found)

# ── Build Chalk object ──
chalk = None
if chalk_rows:
    best_row = dict(chalk_rows[0])
    chalk = load_chalk_as_object(best_row['feature_id'], best_row['pi_label'])
    if chalk:
        print('\nChalk loaded: %s (%s)' % (best_row['feature_id'], best_row['pi_label']))
        print('  Scenarios: %d' % len(chalk.scenarios))
        print('  Scope: %s' % (chalk.scope or '')[:60])
else:
    print('\nNo Chalk cache found — proceeding without Chalk data')

# ── Build DeepMineResult from subtasks ──
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
        print('  %s [%s]: %d AC items, %d rules' % (m.key, m.component, len(m.ac_items), len(m.testable_rules)))

# ================================================================
# RUN V8.0 ENGINE
# ================================================================
print('\n' + '=' * 70)
print('RUNNING V8.0 DATA-FIRST ENGINE')
print('=' * 70 + '\n')

options = {
    'channel': [jira.channel] if jira.channel else ['ITMBO', 'NBOP'],
    'engine_version': '8',
    'custom_instructions': '',
}

suite = build_test_suite_v8(jira, chalk, [], options, deep_mine_result, log=print)

# ================================================================
# ANALYSIS
# ================================================================
print('\n' + '=' * 70)
print('V8.0 OUTPUT ANALYSIS')
print('=' * 70)

print('\nSuite Summary:')
print('  Feature: %s' % suite.feature_id)
print('  Engine: %s' % suite.engine_version)
print('  Test Cases: %d' % len(suite.test_cases))
print('  Total Steps: %d' % sum(len(tc.steps) for tc in suite.test_cases))
print('  Warnings: %d' % len(suite.warnings))

if suite.routing_audit:
    ra = suite.routing_audit
    print('\nRouting Audit:')
    print('  Classification: %s (confidence=%.2f)' % (ra.classification, ra.confidence))
    print('  API TCs: %d | UI TCs: %d | Negative TCs: %d' % (
        ra.api_tcs_generated, ra.ui_tcs_generated, ra.negative_tcs_generated))
    print('  Data sources queried: %s' % ra.data_sources_queried)

print('\nData Inventory:')
for src in suite.data_inventory.sources:
    print('  %-20s | %-8s | %d items | %s' % (src.source_name[:20], src.source_type, src.items_extracted, src.status))
print('  Total testable items: %d' % suite.data_inventory.total_testable_items)

if suite.data_inventory.gaps:
    print('\n  Gaps:')
    for gap in suite.data_inventory.gaps:
        print('    - %s' % gap)

print('\nCombination Plan:')
print('  Independent dimensions: %d' % len(suite.combination_plan.independent_dimensions))
for dim in suite.combination_plan.independent_dimensions:
    print('    %s: %s' % (dim.name, dim.values[:8]))
print('  Crossed dimensions: %d' % len(suite.combination_plan.crossed_dimensions))
print('  Scenario TCs: %d' % len(suite.combination_plan.scenario_tcs))
print('  Negative TCs: %d' % len(suite.combination_plan.negative_tcs))
if suite.combination_plan.reduction_notes:
    print('  Reduction notes:')
    for note in suite.combination_plan.reduction_notes:
        print('    - %s' % note)

print('\nTest Cases:')
print('  %-4s %-12s %-70s' % ('S.No', 'Category', 'Summary'))
print('  ' + '-' * 86)
for tc in suite.test_cases:
    print('  %-4s %-12s %-70s' % (tc.sno, tc.category, tc.summary[:70]))

# ── Detailed step analysis (first 3 TCs) ──
print('\nDetailed Steps (first 3 TCs):')
for tc in suite.test_cases[:3]:
    print('\n  TC: %s' % tc.summary[:60])
    print('  Category: %s | Steps: %d' % (tc.category, len(tc.steps)))
    for s in tc.steps:
        print('    %d. %s' % (s.step_num, s.summary[:75]))
        print('       -> %s' % s.expected[:75])

print('\nTraceability Check:')
all_traced = all(tc.traceability is not None for tc in suite.test_cases)
print('  All TCs have traceability: %s' % ('YES' if all_traced else 'NO'))
if all_traced:
    source_types = {}
    for tc in suite.test_cases:
        st = tc.traceability.source_type
        source_types[st] = source_types.get(st, 0) + 1
    for st, count in sorted(source_types.items()):
        print('    %s: %d TCs' % (st, count))

print('\nZero-Generic Validation:')
print('  Passed: %s' % ('YES' if not suite.warnings else 'NO (%d warnings)' % len(suite.warnings)))
if suite.warnings:
    for w in suite.warnings[:5]:
        print('    - %s' % w[:80])

print('\n' + '=' * 70)
print('DRY RUN COMPLETE')
print('=' * 70)
