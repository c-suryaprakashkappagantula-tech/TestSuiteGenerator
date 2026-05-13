"""
Before/After comparison for MWTGPROV-4348: Data Alignment API
Shows what the V8 engine produces with current fixes.
Since 4348 is not in jira_cache, we need to fetch it first or use known data.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import _conn, init_db, load_chalk_as_object
from modules.jira_fetcher import JiraIssue
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.data_first_engine import build_test_suite_v8

init_db()

print('=' * 70)
print('V8.0 DRY RUN: MWTGPROV-4348 — Data Alignment API')
print('=' * 70)

# ── Check what's in DB ──
c = _conn()

# Check jira_cache
jira_row = c.execute("SELECT * FROM jira_cache WHERE feature_id LIKE '%4348%'").fetchone()

# Check chalk_cache
chalk_row = c.execute("SELECT * FROM chalk_cache WHERE feature_id LIKE '%4348%'").fetchone()

c.close()

if jira_row:
    d = dict(jira_row)
    jira = JiraIssue(
        key=d['feature_id'], summary=d.get('summary', ''),
        description=d.get('description', ''),
        acceptance_criteria=d.get('ac_text', ''),
        channel=d.get('channel', 'ITMBO'), pi=d.get('pi', ''),
        status=d.get('status', ''), priority=d.get('priority', ''),
        labels=json.loads(d.get('labels_json', '[]')),
        subtasks=json.loads(d.get('subtasks_json', '[]')),
    )
    print('\n[OK] Jira loaded from cache: %s' % jira.key)
    print('  Summary: %s' % jira.summary[:80])
    print('  AC length: %d chars' % len(jira.acceptance_criteria or ''))
    print('  Subtasks: %d' % len(jira.subtasks))
else:
    print('\n[MISSING] MWTGPROV-4348 NOT in jira_cache!')
    print('  → Need to fetch from Jira first (use Dashboard "Fetch" button)')
    print('  → Or run: preload_db.py with 4348')

if chalk_row:
    chalk_d = dict(chalk_row)
    print('\n[OK] Chalk found: %s (%s)' % (chalk_d['feature_id'], chalk_d['pi_label']))
    print('  Scope: %s' % (chalk_d.get('scope', '') or '')[:80])
    scenarios_json = chalk_d.get('scenarios_json', '[]')
    scenarios = json.loads(scenarios_json) if scenarios_json else []
    print('  Scenarios: %d' % len(scenarios))
    for i, sc in enumerate(scenarios[:5]):
        print('    %d. %s' % (i+1, (sc.get('title', '') or '')[:70]))
else:
    print('\n[MISSING] No Chalk cache for 4348')

# ── If Jira is available, run the engine ──
if jira_row:
    # Build deep mine
    subtask_mines = []
    for st in jira.subtasks:
        mine = _mine_subtask(st, log=lambda x: None)
        subtask_mines.append(mine)
    deep_mine = DeepMineResult(feature_id=jira.key, subtask_mines=subtask_mines, data_sources_used=['Jira subtasks'])

    # Load chalk object
    chalk = None
    if chalk_row:
        chalk = load_chalk_as_object(chalk_d['feature_id'], chalk_d['pi_label'])

    print('\n' + '=' * 70)
    print('RUNNING V8.0 ENGINE')
    print('=' * 70 + '\n')

    options = {'channel': ['ITMBO'], 'engine_version': '8'}
    suite = build_test_suite_v8(jira, chalk, [], options, deep_mine, log=print)

    print('\n' + '=' * 70)
    print('OUTPUT: %d TCs, %d steps' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
    print('Route: %s (%.2f)' % (suite.routing_audit.classification, suite.routing_audit.confidence))
    print('=' * 70)

    print('\nTest Cases:')
    print('  %-4s %-12s %-70s Steps' % ('S.No', 'Category', 'Summary'))
    print('  ' + '-' * 92)
    for tc in suite.test_cases:
        print('  %-4s %-12s %-70s %d' % (tc.sno, tc.category, tc.summary[:70], len(tc.steps)))

    print('\nData Inventory:')
    for src in suite.data_inventory.sources:
        print('  %-20s | %-10s | %d items | %s' % (src.source_name[:20], src.source_type, src.items_extracted, src.status))

    print('\nDetailed Steps (first 3 TCs):')
    for tc in suite.test_cases[:3]:
        print('\n  %s [%s]' % (tc.summary[:65], tc.category))
        for s in tc.steps:
            print('    %d. %s' % (s.step_num, s.summary[:80]))
            print('       -> %s' % s.expected[:70])
else:
    print('\n' + '=' * 70)
    print('CANNOT RUN: Jira data not in cache.')
    print('Please fetch MWTGPROV-4348 via the Dashboard first.')
    print('=' * 70)

print('\n' + '=' * 70)
print('DRY RUN COMPLETE')
print('=' * 70)
