"""
Checkpoint Validation: MWTGPROV-3943 (API) + MWTGPROV-4006 (UI)
Validates the tsg-v8-feature-routing spec end-to-end.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import init_db, load_chalk_as_object, _conn
from modules.jira_fetcher import JiraIssue
from modules.data_first_engine import build_test_suite_v8
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.tc_builder import classify_feature

init_db()
c = _conn()

print('=' * 70)
print('CHECKPOINT VALIDATION: tsg-v8-feature-routing spec')
print('  Task 5: Validate both reference features end-to-end')
print('=' * 70)

# ═══════════════════════════════════════════════════════════════
# MWTGPROV-3943 (API Path)
# ═══════════════════════════════════════════════════════════════
print('\n--- 5.1: MWTGPROV-3943 (API Path) ---')
jira_row = c.execute(
    "SELECT * FROM jira_cache WHERE feature_id LIKE '%3943%'"
).fetchone()

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
else:
    jira = JiraIssue(
        key='MWTGPROV-3943',
        summary='[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)',
        description='Retrieve Device API', status='In Progress', priority='High',
        issue_type='Epic', labels=[], pi='PI-51', channel='ITMBO',
        acceptance_criteria='1. GET method\n2. POST method\n3. ERR06\n4. ERR16\n5. Phone, Tablet, Smartwatch',
        subtasks=[],
    )

subtask_mines = []
for st in jira.subtasks:
    mine = _mine_subtask(st, log=lambda x: None)
    subtask_mines.append(mine)
deep_mine = DeepMineResult(
    feature_id=jira.key, subtask_mines=subtask_mines, data_sources_used=['Jira subtasks']
)

suite1 = build_test_suite_v8(
    jira, None, [], {'channel': ['ITMBO', 'NBOP'], 'engine_version': '8'},
    deep_mine, log=lambda x: None
)

# Validate 3943
checks_3943 = {
    'Classification = api': suite1.routing_audit.classification == 'api',
    'Confidence >= 0.9': suite1.routing_audit.confidence >= 0.9,
    'Has negative TCs': suite1.routing_audit.negative_tcs_generated > 0,
    'TC count ~12 (8-20)': 8 <= len(suite1.test_cases) <= 20,
    'Has POST/GET steps': any(
        'POST' in s.summary or 'GET' in s.summary
        for tc in suite1.test_cases for s in tc.steps
    ),
    'Has Business Rules coverage': any(
        'ERR' in tc.summary or '404' in tc.summary
        for tc in suite1.test_cases
    ),
    'Routing audit populated': suite1.routing_audit is not None,
    'Total TCs recorded in audit': suite1.routing_audit.total_tcs == len(suite1.test_cases),
}

all_pass_3943 = True
for name, passed in checks_3943.items():
    status = 'PASS' if passed else 'FAIL'
    if not passed:
        all_pass_3943 = False
    print('  %s  %s' % (status, name))

print('  Summary: %d TCs, %d negative, route=%s' % (
    len(suite1.test_cases), suite1.routing_audit.negative_tcs_generated,
    suite1.routing_audit.classification))

# ═══════════════════════════════════════════════════════════════
# MWTGPROV-4006 (UI Path)
# ═══════════════════════════════════════════════════════════════
print('\n--- 5.2: MWTGPROV-4006 (UI Path) ---')
jira_row2 = c.execute(
    "SELECT * FROM jira_cache WHERE feature_id LIKE '%4006%'"
).fetchone()
chalk_rows = c.execute(
    "SELECT feature_id, pi_label FROM chalk_cache WHERE feature_id LIKE '%4006%'"
).fetchall()
chalk = None
if chalk_rows:
    best = dict(chalk_rows[0])
    chalk = load_chalk_as_object(best['feature_id'], best['pi_label'])

if jira_row2:
    d2 = dict(jira_row2)
    jira2 = JiraIssue(
        key=d2['feature_id'], summary=d2.get('summary', ''),
        description=d2.get('description', ''),
        acceptance_criteria=d2.get('ac_text', ''),
        channel=d2.get('channel', 'NBOP'), pi=d2.get('pi', ''),
        status=d2.get('status', ''), priority=d2.get('priority', ''),
        labels=json.loads(d2.get('labels_json', '[]')),
        subtasks=json.loads(d2.get('subtasks_json', '[]')),
    )
else:
    jira2 = JiraIssue(
        key='MWTGPROV-4006',
        summary='[NBOP]: 54.2 NBOP to hide the page Port-in status for TMO only',
        description='Hide Port-in Status page', status='In Progress', priority='Medium',
        issue_type='Story', labels=[], pi='PI-54.2', channel='NBOP',
        acceptance_criteria='1. Hide Port-in Status for TMO\n2. VZW should still see it',
        subtasks=[],
    )

deep_mine2 = DeepMineResult(feature_id=jira2.key, subtask_mines=[], data_sources_used=[])
suite2 = build_test_suite_v8(
    jira2, chalk, [], {'channel': ['NBOP'], 'engine_version': '8'},
    deep_mine2, log=lambda x: None
)

# Validate 4006
checks_4006 = {
    'Classification = ui': suite2.routing_audit.classification == 'ui',
    'Confidence >= 0.85': suite2.routing_audit.confidence >= 0.85,
    'TC count 2-6': 2 <= len(suite2.test_cases) <= 6,
    'Has NBOP nav steps': any(
        'nbop' in s.summary.lower() or 'navigate' in s.summary.lower() or 'launch' in s.summary.lower()
        for tc in suite2.test_cases for s in tc.steps
    ),
    'Has element verification': any(
        'verify' in s.summary.lower() or 'displayed' in s.summary.lower()
        for tc in suite2.test_cases for s in tc.steps
    ),
    'Routing audit populated': suite2.routing_audit is not None,
    'Total TCs recorded in audit': suite2.routing_audit.total_tcs == len(suite2.test_cases),
}

all_pass_4006 = True
for name, passed in checks_4006.items():
    status = 'PASS' if passed else 'FAIL'
    if not passed:
        all_pass_4006 = False
    print('  %s  %s' % (status, name))

print('  Summary: %d TCs, route=%s, confidence=%.2f' % (
    len(suite2.test_cases), suite2.routing_audit.classification,
    suite2.routing_audit.confidence))

# ═══════════════════════════════════════════════════════════════
# 5.3: Routing Audit Verification
# ═══════════════════════════════════════════════════════════════
print('\n--- 5.3: Routing Audit Verification ---')
checks_audit = {
    '3943 audit has classification': suite1.routing_audit.classification in ('api', 'ui', 'hybrid'),
    '3943 audit has confidence': 0.0 < suite1.routing_audit.confidence <= 1.0,
    '3943 audit has data_sources': len(suite1.routing_audit.data_sources_queried) > 0,
    '3943 audit has TC counts': suite1.routing_audit.total_tcs > 0,
    '4006 audit has classification': suite2.routing_audit.classification in ('api', 'ui', 'hybrid'),
    '4006 audit has confidence': 0.0 < suite2.routing_audit.confidence <= 1.0,
    '4006 audit has data_sources': len(suite2.routing_audit.data_sources_queried) > 0,
    '4006 audit has TC counts': suite2.routing_audit.total_tcs > 0,
    'Dashboard field exists': hasattr(suite1, 'routing_audit'),
}

all_pass_audit = True
for name, passed in checks_audit.items():
    status = 'PASS' if passed else 'FAIL'
    if not passed:
        all_pass_audit = False
    print('  %s  %s' % (status, name))

c.close()

# ═══════════════════════════════════════════════════════════════
# FINAL RESULT
# ═══════════════════════════════════════════════════════════════
all_pass = all_pass_3943 and all_pass_4006 and all_pass_audit
print('\n' + '=' * 70)
if all_pass:
    print('CHECKPOINT RESULT: ALL PASSED')
else:
    print('CHECKPOINT RESULT: SOME CHECKS FAILED')
print('  3943: %d TCs | route=%s | neg=%d | conf=%.2f' % (
    len(suite1.test_cases), suite1.routing_audit.classification,
    suite1.routing_audit.negative_tcs_generated, suite1.routing_audit.confidence))
print('  4006: %d TCs | route=%s | neg=%d | conf=%.2f' % (
    len(suite2.test_cases), suite2.routing_audit.classification,
    suite2.routing_audit.negative_tcs_generated, suite2.routing_audit.confidence))
print('=' * 70)
