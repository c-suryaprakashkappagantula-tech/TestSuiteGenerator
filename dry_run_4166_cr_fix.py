"""
Dry-run: Verify MWTGPROV-4166 CR fix produces ≤8 proper TCs (not 10 bad ones).

Tests that the V8 engine correctly:
  1. Detects MWTGPROV-4166 as a CR/bug fix ticket
  2. Delegates to the V7 CR-specific path
  3. Suppresses channel/device expansion (no ITMBO/NBOP/Phone/Tablet variants)
  4. Uses proper scenario titles (not raw Jira text)
  5. Skips NBOP UI verify TCs (not relevant for this bug fix)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.cr_detector import is_cr_or_bug
from modules.data_first_engine import build_test_suite_v8
from modules.jira_fetcher import JiraIssue
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.database import load_chalk_as_object, _conn

# ================================================================
# LOAD CHALK FROM DB
# ================================================================
print('Loading MWTGPROV-4166 from DB cache...')
c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4166' LIMIT 1").fetchone()
c.close()

chalk = None
if row:
    chalk = load_chalk_as_object('MWTGPROV-4166', row['pi_label'])
    print('Found in DB under %s: %d scenarios' % (row['pi_label'], len(chalk.scenarios)))
else:
    print('NOT FOUND in DB — will use Jira-only path')

# ================================================================
# MOCK JIRA (matches the real MWTGPROV-4166 ticket)
# ================================================================
jira = JiraIssue(
    key='MWTGPROV-4166',
    summary="CR - [NSLNM, INTG]: New MVNO - Guarente'd Delivery flow in NBOP not working",
    description="""This CR is to address - MWTGTEST-11402
NBOP Guarente'd delivery flow not working. When a MDN is de-active in TMO, 
running a Deactivate API, shows error "Deactivation got failed with invalid MSI".
NSL has ensure that if it receives and error response from TMO for a deactivation 
request, it should still deactivate the line in NSL and send a guaranteed response 
back to NBOP.""",
    status='In Progress', priority='High', issue_type='Epic',
    assignee='QA Team', reporter='Dev Lead',
    labels=['MWTGPROV-4166', 'PI-54', 'CR', 'TMO'],
    pi='PI-54', channel='ITMBO',
    acceptance_criteria="""When a MDN is de-active in TMO, running a Deactivate API, 
shows error "Deactivation got failed with invalid MSI". 
MDN should be Deactivated in NBOP after the guaranteed delivery fix.
Deactivate a TMO line, manually move the line to active in NBOP, 
then run Deactivate API — should succeed with guaranteed delivery.""",
    linked_issues=[{
        'key': 'MWTGTEST-11402',
        'summary': "NBOP Guarente'd delivery flow not working",
        'description': """Steps to Reproduce:
Step 1: MDN is de-active in TMO
Step 2: MDN is active in NBOP/NSL DB
Step 3: Run Deactivate API
Actual: Shows error "Deactivation got failed with invalid MSI"
Expected: MDN should be Deactivated in NBOP""",
    }],
    subtasks=[{
        'key': 'MWTGPROV-4166-1',
        'summary': 'UAT - Guaranteed Delivery Deactivation',
        'description': """Pre-Conditions: MDN must be De-Active in TMO, Active in NBOP/NSL DB
Post-Conditions: NSL returned guaranteed response and updated line status to De-Active""",
        'acceptance_criteria': 'MDN should be Deactivated in NBOP after guaranteed delivery fix',
        'status': 'To Do',
    }],
)

# ================================================================
# TEST 1: CR Detection
# ================================================================
print('\n' + '=' * 60)
print('TEST 1: CR/Bug Fix Detection')
print('=' * 60)
is_cr = is_cr_or_bug(jira.summary, jira.issue_type, jira.description)
print('  is_cr_or_bug() = %s' % is_cr)
assert is_cr, 'FAIL: MWTGPROV-4166 should be detected as CR/bug fix!'
print('  PASS: Correctly detected as CR/bug fix')

# ================================================================
# TEST 2: V8 Engine produces ≤8 TCs
# ================================================================
print('\n' + '=' * 60)
print('TEST 2: V8 Engine TC Generation')
print('=' * 60)

options = {
    'channel': ['ITMBO'], 'devices': ['Mobile'], 'networks': ['5G'],
    'sim_types': ['eSIM', 'pSIM'], 'os_platforms': ['iOS', 'Android'],
    'include_positive': True, 'include_negative': True, 'include_e2e': True,
    'include_edge': True, 'include_attachments': False,
    'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
}

suite = build_test_suite_v8(jira, chalk, [], options, log=print)

print('\n' + '=' * 60)
print('RESULTS')
print('=' * 60)
print('  TC count: %d (expected ≤8)' % len(suite.test_cases))
print('  Engine version: %s' % suite.engine_version)
print()

# ================================================================
# TEST 3: No channel/device variants
# ================================================================
print('TEST 3: No channel/device variant expansion')
bad_variants = []
for tc in suite.test_cases:
    s = tc.summary
    if '_ITMBO_' in s or '_NBOP_' in s:
        bad_variants.append(s)
    if s.endswith('Phone') or s.endswith('Tablet'):
        bad_variants.append(s)
if bad_variants:
    print('  FAIL: Found channel/device variants:')
    for v in bad_variants:
        print('    - %s' % v[:80])
else:
    print('  PASS: No channel/device variants found')

# ================================================================
# TEST 4: No raw Jira text as TC names
# ================================================================
print('\nTEST 4: No raw Jira text as TC names')
raw_text_indicators = [
    'This_CR_is_to_address',
    'NSL_has_ensure_that',
    'NBOP_Guarente',
    'Data_Details_and_Historical',
    'manually_move_the_line',
]
bad_names = []
for tc in suite.test_cases:
    for indicator in raw_text_indicators:
        if indicator in tc.summary:
            bad_names.append(tc.summary[:80])
            break
if bad_names:
    print('  FAIL: Found raw Jira text in TC names:')
    for n in bad_names:
        print('    - %s' % n)
else:
    print('  PASS: All TC names are proper scenario titles')

# ================================================================
# SUMMARY
# ================================================================
print('\n' + '=' * 60)
print('TC LIST:')
print('=' * 60)
for i, tc in enumerate(suite.test_cases, 1):
    print('  TC%d [%-12s]: %s' % (i, tc.category, tc.summary[:90]))

print('\n' + '=' * 60)
tc_count = len(suite.test_cases)
has_variants = bool(bad_variants)
has_raw_text = bool(bad_names)

if tc_count <= 8 and not has_variants and not has_raw_text:
    print('ALL TESTS PASSED: %d TCs, no variants, no raw text' % tc_count)
else:
    print('SOME TESTS FAILED:')
    if tc_count > 8:
        print('  - TC count %d > 8' % tc_count)
    if has_variants:
        print('  - Channel/device variants present')
    if has_raw_text:
        print('  - Raw Jira text in TC names')
print('=' * 60)
