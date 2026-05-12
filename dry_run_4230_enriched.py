"""
Dry-run: MWTGPROV-4230 with evidence-enriched generation.
Uses unit testing doc + test result evidence to produce proper crossed-dimension TCs.

Evidence sources:
  1. Jira subtask MWTGNBOP-5558 (AC + description)
  2. Unit testing document (3-step flow)
  3. Test result docs (Phone, Tablet, Smartwatch — specific attributes)
  4. NBOP crawler cache (navigation paths)

Target: 8 TCs = Product(3) × Screen(2) + VZW Regression(2)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import init_db, _conn
from modules.jira_fetcher import JiraIssue
from modules.deep_miner import DeepMineResult, SubtaskMine
from modules.data_first_engine import build_test_suite_v8
from modules.data_models_v8 import (
    TestCase, TestStep, TestSuite, DimensionSet, Dimension,
    ExtractedScenario, DataInventory, DataSourceEntry, CombinationPlan,
)
from modules.traceability import create_traceability
from modules.tc_builder import classify_feature, _assign_serial_numbers
from modules.nbop_ui_knowledge import get_navigation_path

init_db()

print('=' * 70)
print('V8.0 ENRICHED DRY RUN: MWTGPROV-4230 — Usage Inquiry (parity)')
print('  Evidence: Unit Testing Doc + Test Results + NBOP Cache')
print('=' * 70)

# ================================================================
# EVIDENCE DATA (extracted from documents)
# ================================================================

# From unit testing document
UNIT_TEST_STEPS = [
    'Log in to NBOP application and search for an existing Phone, Tablet or Smartwatch TMO lines → line-summary screen',
    'Click on the Data Details menu → Data Details Screen. Ensure cards and parameters are not visible per TMO requirements',
    'Click on the View Historical Usage → Historical Usage Grid. Ensure cards and parameters are not visible per TMO requirements',
]

# From test result documents (Phone, Tablet, Smartwatch)
PRODUCTS = ['Phone', 'Tablet', 'Smartwatch']
SCREENS = ['Data Details', 'Historical Usage']
MNOS = ['TMO', 'VZW']

# Attributes that MUST be removed for TMO
REMOVED_ATTRIBUTES = [
    'Total MNO Usage',
    'Total HMNO Usage',
    'Total Promo Usage',
    'Total Usage',
    'Threshold',
    'Percentage Used',
]

# Attributes that SHOULD still display under Total Usage (Current Billing Period)
KEPT_ATTRIBUTES = [
    'MNO Data Usage',
    'MNO MHS Data Usage',
    'HMNO Data Usage',
    'HMNO MHS Usage',
    'Promo Data Usage',
    'Promo MHS Usage',
]

# Historical Usage grid fields
HISTORICAL_FIELDS = ['Start Date', 'End Date', 'Search option']

# NBOP navigation
NAV_DATA_DETAILS = 'NBOP → Subscriber Profile → ≡ Menu → Data Details'
NAV_HISTORICAL = 'NBOP → Subscriber Profile → ≡ Menu → View Historical Usage'

# Preconditions from evidence
PRECONDITIONS = [
    'Account Type = Commercial',
    'MNO = T-Mobile (for TMO TCs) / Verizon (for VZW TCs)',
    'User role = Admin',
    'Line Status = Active',
]

# ================================================================
# BUILD TEST CASES FROM EVIDENCE
# ================================================================

feature_id = 'MWTGPROV-4230'
feature_name = 'Usage Inquiry (parity)'
test_cases = []

def _build_tmo_tc(product: str, screen: str, idx: int) -> TestCase:
    """Build a TMO TC verifying attributes are removed from a specific screen."""
    is_data_details = 'Data Details' in screen
    nav_path = NAV_DATA_DETAILS if is_data_details else NAV_HISTORICAL

    summary = '%s_TC%02d_NBOP_%s_%s_Verify_Attributes_Removed_TMO' % (
        feature_id, idx, product, screen.replace(' ', '_'))

    description = (
        'Verify that total usage attributes (Total MNO Usage, Total HMNO Usage, '
        'Total Promo Usage, Total Usage, Threshold, Percentage Used) are NOT displayed '
        'on the %s screen for TMO %s subscriber.' % (screen, product)
    )

    preconditions = '\n'.join([
        '1. TMO %s line available in SIT (Account Type = Commercial)' % product,
        '2. User logged into NBOP with Admin role',
        '3. Line Status: Active, MNO: T-Mobile',
    ])

    steps = [
        TestStep(
            step_num=1,
            summary='Login to NBOP and search for TMO %s subscriber by MDN/IMEI' % product,
            expected='Line Summary screen displayed with subscriber profile cards (Account, Line, Device, SIM info)',
            data_reference='Product: %s | MNO: TMO' % product,
        ),
        TestStep(
            step_num=2,
            summary='Navigate to: %s' % nav_path,
            expected='%s screen loaded successfully' % screen,
            data_reference='Navigation: %s' % nav_path,
        ),
        TestStep(
            step_num=3,
            summary='Verify the following attributes are NOT displayed: %s' % ', '.join(REMOVED_ATTRIBUTES),
            expected='None of the 6 total usage attributes are visible on the %s screen for TMO subscriber' % screen,
            data_reference='Removed: %s' % ', '.join(REMOVED_ATTRIBUTES),
        ),
    ]

    # Data Details has additional verification of kept fields
    if is_data_details:
        steps.append(TestStep(
            step_num=4,
            summary='Verify the following fields ARE displayed under Total Usage (Current Billing Period): %s' % ', '.join(KEPT_ATTRIBUTES),
            expected='All 6 individual usage fields are visible and showing data for TMO %s' % product,
            data_reference='Kept: %s' % ', '.join(KEPT_ATTRIBUTES),
        ))
    else:
        steps.append(TestStep(
            step_num=4,
            summary='Verify Historical Usage grid displays: %s' % ', '.join(HISTORICAL_FIELDS),
            expected='Grid shows date range filters and search functionality',
            data_reference='Grid fields: %s' % ', '.join(HISTORICAL_FIELDS),
        ))

    tr = create_traceability(
        source_type='Subtask AC',
        source_id='MWTGNBOP-5558',
        extracted_text='%s - %s: attributes removed for TMO %s' % (screen, product, product),
    )

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category='Happy Path',
        traceability=tr,
        dimension_values={'product': product, 'screen': screen, 'mno': 'TMO'},
    )


def _build_vzw_regression_tc(screen: str, idx: int) -> TestCase:
    """Build a VZW regression TC verifying attributes are still present."""
    is_data_details = 'Data Details' in screen
    nav_path = NAV_DATA_DETAILS if is_data_details else NAV_HISTORICAL

    summary = '%s_TC%02d_NBOP_Phone_%s_Verify_No_Changes_VZW' % (
        feature_id, idx, screen.replace(' ', '_'))

    description = (
        'Verify that total usage attributes remain displayed on the %s screen '
        'for VZW subscribers (no changes for Verizon).' % screen
    )

    preconditions = '\n'.join([
        '1. VZW Phone line available in SIT (Account Type = Commercial)',
        '2. User logged into NBOP with Admin role',
        '3. Line Status: Active, MNO: Verizon',
    ])

    steps = [
        TestStep(
            step_num=1,
            summary='Login to NBOP and search for VZW Phone subscriber by MDN/IMEI',
            expected='Line Summary screen displayed with subscriber profile cards',
            data_reference='Product: Phone | MNO: VZW',
        ),
        TestStep(
            step_num=2,
            summary='Navigate to: %s' % nav_path,
            expected='%s screen loaded successfully' % screen,
            data_reference='Navigation: %s' % nav_path,
        ),
        TestStep(
            step_num=3,
            summary='Verify the following attributes ARE still displayed: %s' % ', '.join(REMOVED_ATTRIBUTES),
            expected='All 6 total usage attributes are visible on the %s screen for VZW subscriber (unchanged)' % screen,
            data_reference='Expected present: %s' % ', '.join(REMOVED_ATTRIBUTES),
        ),
        TestStep(
            step_num=4,
            summary='Verify all other usage information remains unchanged from previous behavior',
            expected='No visual or functional differences for VZW subscriber on %s screen' % screen,
            data_reference='Regression: VZW unchanged',
        ),
    ]

    tr = create_traceability(
        source_type='Subtask AC',
        source_id='MWTGNBOP-5558',
        extracted_text='No changes for Verizon subscribers on %s' % screen,
    )

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category='Regression',
        traceability=tr,
        dimension_values={'product': 'Phone', 'screen': screen, 'mno': 'VZW'},
    )


# ── Generate TMO TCs: Product × Screen ──
tc_idx = 1
for product in PRODUCTS:
    for screen in SCREENS:
        tc = _build_tmo_tc(product, screen, tc_idx)
        test_cases.append(tc)
        tc_idx += 1

# ── Generate VZW Regression TCs: Screen only (Phone representative) ──
for screen in SCREENS:
    tc = _build_vzw_regression_tc(screen, tc_idx)
    test_cases.append(tc)
    tc_idx += 1

# Assign serial numbers
_assign_serial_numbers(test_cases)

# ================================================================
# OUTPUT
# ================================================================
total_steps = sum(len(tc.steps) for tc in test_cases)

print('\n' + '=' * 70)
print('ENRICHED OUTPUT: %d TCs, %d steps' % (len(test_cases), total_steps))
print('=' * 70)

print('\nDimensions used:')
print('  Products: %s' % PRODUCTS)
print('  Screens: %s' % SCREENS)
print('  MNOs: TMO (remove) + VZW (regression)')
print('  Crossing: Product(3) × Screen(2) = 6 TMO TCs + 2 VZW Regression = 8 total')

print('\nPreconditions (all TCs):')
for p in PRECONDITIONS:
    print('  - %s' % p)

print('\nTest Cases:')
print('  %-4s %-12s %-70s' % ('S.No', 'Category', 'Summary'))
print('  ' + '-' * 86)
for tc in test_cases:
    print('  %-4s %-12s %-70s' % (tc.sno, tc.category, tc.summary[:70]))

print('\nDetailed Steps:')
for tc in test_cases:
    print('\n  %s [%s]' % (tc.summary[:65], tc.category))
    dims = tc.dimension_values
    print('  Product=%s | Screen=%s | MNO=%s' % (dims.get('product',''), dims.get('screen',''), dims.get('mno','')))
    for s in tc.steps:
        print('    %d. %s' % (s.step_num, s.summary[:80]))
        print('       → %s' % s.expected[:80])

print('\nTraceability:')
print('  All TCs traced to: MWTGNBOP-5558 (Subtask AC)')

print('\nEvidence Sources Used:')
print('  1. Jira subtask MWTGNBOP-5558 (AC + User Story + Pre/Post conditions)')
print('  2. Unit testing document: MWTGNBOP-5558 Unit testing document.docx')
print('  3. Test results: Phone.docx, Tablet.docx, Smartwatch.docx')
print('  4. NBOP crawler cache: Data Details page, navigation paths')

print('\nAttributes Removed (TMO only):')
for attr in REMOVED_ATTRIBUTES:
    print('  ✗ %s' % attr)

print('\nAttributes Kept (both TMO and VZW):')
for attr in KEPT_ATTRIBUTES:
    print('  ✓ %s' % attr)

print('\n' + '=' * 70)
print('DRY RUN COMPLETE — %d TCs, %d steps' % (len(test_cases), total_steps))
print('=' * 70)
