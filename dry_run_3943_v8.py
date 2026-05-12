"""
Dry-run: Build test suite for MWTGPROV-3943 using V8.0 Data-First Engine.
Loads data from DB cache, runs the full V8 pipeline, and analyzes output.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import _conn, init_db, load_chalk_as_object
from modules.jira_fetcher import JiraIssue
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.deep_miner import DeepMineResult, APISpec, SubtaskMine
from modules.data_first_engine import build_test_suite_v8

init_db()

# ================================================================
# LOAD FROM DB CACHE
# ================================================================
print('=' * 70)
print('V8.0 DRY RUN: MWTGPROV-3943 — Retrieve Device (GET/POST)')
print('=' * 70)

c = _conn()

# Load Jira
jira_row = c.execute(
    "SELECT * FROM jira_cache WHERE feature_id LIKE '%3943%'"
).fetchone()

# Load Chalk
chalk_rows = c.execute(
    "SELECT feature_id, pi_label, scope, scenarios_json FROM chalk_cache WHERE feature_id LIKE '%3943%'"
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
        channel=d.get('channel', 'ITMBO'),
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
    # Fallback: use known data for MWTGPROV-3943
    print('\nNo Jira cache found — using known feature data')
    jira = JiraIssue(
        key='MWTGPROV-3943',
        summary='[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)',
        description='Retrieve Device API for TMO MVNO integration. Supports GET and POST methods.',
        status='In Progress', priority='High', issue_type='Epic',
        labels=['MWTGPROV-3943', 'RetrieveDevice', 'TMO'],
        pi='PI-51', channel='ITMBO',
        acceptance_criteria="""
1. NSL shall support Retrieve Device via GET method with MDN, IMEI, ICCID, EID, or LineID as input
2. NSL shall support Retrieve Device via POST method with the same input types
3. The API shall return device details including deviceType, manufacturer, model, IMEI, ICCID, SIM status
4. When input identifier is not found, return error ERR06 "Device not found"
5. When line is in Hotlined state, return error ERR16 "Line is hotlined - operation restricted"
6. When line is in Suspended state, return error ERR17 "Line is suspended"
7. When line is in Deactivated state, return error ERR18 "Line is deactivated"
8. Products supported: Phone, Tablet, Smartwatch
9. Channels: ITMBO (API), NBOP (UI portal)
10. NBOP shall display device information in subscriber profile under Device Information section
11. API response shall include all device attributes: IMEI, ICCID, EID, SIM type (eSIM/pSIM), device make/model
""",
        subtasks=[
            {'key': 'MWTGPROV-3944', 'summary': 'NSLNM - API Implementation for Retrieve Device',
             'acceptance_criteria': '1. Implement GET /api/v1/retrieve-device endpoint\n2. Implement POST /api/v1/retrieve-device endpoint\n3. Support MDN, IMEI, ICCID, EID, LineID as input parameters\n4. Return full device object with all attributes\n5. Handle MNO_TMO permission toggle - when OFF, return 403',
             'description': 'Pre-Conditions:\n1. Line must exist in NSL DB\n2. MNO_TMO permission must be ON\nPost-Conditions:\n1. No data modification (read-only operation)\n2. Transaction logged in history'},
            {'key': 'MWTGPROV-3945', 'summary': 'UI - NBOP Display Device Information',
             'acceptance_criteria': '1. Display device info in subscriber profile\n2. Show IMEI, ICCID, device type, manufacturer, model\n3. Show SIM type (eSIM/pSIM) with status indicator\n4. Error message displayed for invalid MDN search',
             'description': 'User Story: As a care agent, I want to see device details in NBOP subscriber profile'},
            {'key': 'MWTGPROV-3946', 'summary': 'INTG - Integration Testing for Retrieve Device',
             'acceptance_criteria': '1. Verify NSL-to-NE call for device lookup\n2. Verify response mapping from NE to NSL format\n3. Verify error propagation from NE to API response',
             'description': ''},
        ],
    )

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
from modules.deep_miner import _mine_subtask

subtask_mines = []
for st in jira.subtasks:
    mine = _mine_subtask(st, log=lambda x: None)
    subtask_mines.append(mine)

deep_mine_result = DeepMineResult(
    feature_id=jira.key,
    subtask_mines=subtask_mines,
    data_sources_used=['Jira subtasks'],
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
    'channel': ['ITMBO', 'NBOP'],
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

print('\nData Inventory:')
for src in suite.data_inventory.sources:
    print('  %-20s | %-8s | %d items | %s' % (src.source_name, src.source_type, src.items_extracted, src.status))
print('  Total testable items: %d' % suite.data_inventory.total_testable_items)

if suite.data_inventory.gaps:
    print('\n  Gaps:')
    for gap in suite.data_inventory.gaps:
        print('    - %s' % gap)

print('\nCombination Plan:')
print('  Independent dimensions: %d' % len(suite.combination_plan.independent_dimensions))
for dim in suite.combination_plan.independent_dimensions:
    print('    %s: %s' % (dim.name, dim.values))
print('  Crossed dimensions: %d' % len(suite.combination_plan.crossed_dimensions))
print('  Scenario TCs: %d' % len(suite.combination_plan.scenario_tcs))
print('  Negative TCs: %d' % len(suite.combination_plan.negative_tcs))
print('  Reduction notes:')
for note in suite.combination_plan.reduction_notes:
    print('    - %s' % note)

print('\nTest Cases:')
print('  %-4s %-12s %-70s' % ('S.No', 'Category', 'Summary'))
print('  ' + '-' * 86)
for tc in suite.test_cases:
    print('  %-4s %-12s %-70s' % (tc.sno, tc.category, tc.summary[:70]))

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

# ── Coverage analysis ──
print('\nCoverage Analysis (expected scenarios for Retrieve Device):')
all_text = ' '.join([
    tc.summary.lower() + ' ' + tc.description.lower() + ' ' +
    ' '.join(s.summary.lower() + ' ' + s.expected.lower() for s in tc.steps)
    for tc in suite.test_cases
])

checks = {
    'GET method': ['get'],
    'POST method': ['post'],
    'MDN input': ['mdn'],
    'IMEI input': ['imei'],
    'ICCID input': ['iccid'],
    'EID input': ['eid'],
    'LineID input': ['lineid', 'line id'],
    'Phone product': ['phone'],
    'Tablet product': ['tablet'],
    'Smartwatch product': ['smartwatch'],
    'ITMBO channel': ['itmbo'],
    'NBOP channel': ['nbop'],
    'Error ERR06 (not found)': ['err06', 'not found'],
    'Error ERR16 (hotlined)': ['err16', 'hotline'],
    'Error ERR17 (suspended)': ['err17', 'suspend'],
    'Error ERR18 (deactivated)': ['err18', 'deactivat'],
    'Hotlined state': ['hotline'],
    'Suspended state': ['suspend'],
    'Deactivated state': ['deactivat'],
    'Permission toggle': ['permission', 'mno_tmo'],
    'NBOP UI navigation': ['nbop', 'portal', 'profile'],
}

covered = 0
for name, keywords in checks.items():
    found = any(kw in all_text for kw in keywords)
    if found:
        covered += 1
    print('  %-30s %s' % (name, 'COVERED' if found else 'MISSING'))

print('\n  Coverage: %d/%d (%.0f%%)' % (covered, len(checks), covered / len(checks) * 100))

print('\n' + '=' * 70)
print('DRY RUN COMPLETE')
print('=' * 70)
