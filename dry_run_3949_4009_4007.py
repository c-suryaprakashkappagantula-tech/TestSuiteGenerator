"""
Dry-run: Full audit of MWTGPROV-3949 (Swap MDN), MWTGPROV-4009 (Sync Subscriber),
and MWTGPROV-4007 (Order Inquiry) from DB cache.

For every TC, checks:
  1) Is the scenario relevant to the feature?
  2) Are preconditions specific and correct?
  3) Are step summaries actionable (not raw Jira text)?
  4) Are expected results specific (not generic)?
  5) Do the steps match the TC summary (no wrong template)?

Flags every issue found and produces a fix report.
"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(__file__))

from modules.config import OUTPUTS, ts
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.excel_generator import generate_excel
from modules.database import load_chalk_as_object, _conn


# ================================================================
# QUALITY CHECKS
# ================================================================

GENERIC_EXPECTED = [
    'expected behavior confirmed',
    'expected result confirmed',
    'system behaves as expected',
    'verify expected behavior',
    'as expected',
    'should work correctly',
    'operation successful',
    'test passed',
    'no issues found',
    'works as designed',
]

RAW_JIRA_MARKERS = [
    'this api is used',
    'nsl has exposed a rest api',
    'the service applicable to products',
    'the ordering source are',
    'regardless of the tmo or vzw',
    'the following transaction types',
    '*yl -',
    '*yd -',
    '*yp -',
    '*ym -',
    '*pl -',
]


def check_relevance(tc, feature_keyword, feature_id):
    """Check 1: Is the scenario relevant to the feature?"""
    issues = []
    tc_text = (tc.summary + ' ' + tc.description + ' ' +
               ' '.join(s.summary for s in tc.steps)).lower()

    # Feature-specific relevance checks
    if feature_keyword == 'swap_mdn':
        swap_keywords = ['swap', 'mdn', 'change sim', 'change imei', 'iccid',
                         'syniverse', 'rollback', 'kafka', 'bi event', 'device']
        if not any(kw in tc_text for kw in swap_keywords):
            issues.append('RELEVANCE: TC does not mention any Swap MDN keywords')

    elif feature_keyword == 'sync_subscriber':
        sync_keywords = ['sync', 'yl', 'yd', 'yp', 'ym', 'pl', 'line status',
                         'subscriber', 'synchronize', 'iccid change', 'feature change']
        if not any(kw in tc_text for kw in sync_keywords):
            issues.append('RELEVANCE: TC does not mention any Sync Subscriber keywords')

    elif feature_keyword == 'order_inquiry':
        inquiry_keywords = ['inquiry', 'order', 'reference number', 'query',
                            'requesttype', 'network inquiry', 'status']
        if not any(kw in tc_text for kw in inquiry_keywords):
            issues.append('RELEVANCE: TC does not mention any Order Inquiry keywords')

    return issues


def check_preconditions(tc, feature_keyword):
    """Check 2: Are preconditions specific and correct?"""
    issues = []
    pre = tc.preconditions.strip()

    if not pre:
        issues.append('PRECONDITION: Empty preconditions')
        return issues

    if len(pre) < 15:
        issues.append('PRECONDITION: Too short (%d chars) — likely not specific enough' % len(pre))

    # Check for generic/placeholder preconditions
    pre_low = pre.lower()
    if pre_low in ['n/a', 'none', 'na', '-', 'tbd']:
        issues.append('PRECONDITION: Placeholder value "%s"' % pre)

    # Feature-specific precondition checks
    if feature_keyword == 'swap_mdn':
        if 'active' not in pre_low and 'line' not in pre_low and 'account' not in pre_low:
            if 'negative' not in tc.category.lower() and 'rollback' not in tc.summary.lower():
                issues.append('PRECONDITION: Swap MDN TC should mention active lines or account')

    elif feature_keyword == 'sync_subscriber':
        if 'lineid' not in pre_low and 'line' not in pre_low and 'subscriber' not in pre_low:
            issues.append('PRECONDITION: Sync Subscriber TC should mention LineId/MDN or subscriber state')

    elif feature_keyword == 'order_inquiry':
        if 'reference' not in pre_low and 'mdn' not in pre_low and 'nbop' not in pre_low:
            if 'negative' not in tc.category.lower():
                issues.append('PRECONDITION: Order Inquiry TC should mention Reference Number or MDN')

    return issues


def check_step_quality(tc):
    """Check 3: Are step summaries actionable (not raw Jira text)?"""
    issues = []
    for step in tc.steps:
        s_low = step.summary.lower()

        # Check for raw Jira text leaked into steps
        for marker in RAW_JIRA_MARKERS:
            if marker in s_low:
                issues.append('RAW_JIRA_TEXT: Step %d contains raw Jira text: "%s..."' % (
                    step.step_num, step.summary[:60]))
                break

        # Check for non-actionable steps
        if len(step.summary.strip()) < 10:
            issues.append('STEP_TOO_SHORT: Step %d is too short (%d chars): "%s"' % (
                step.step_num, len(step.summary), step.summary))

        # Check for steps that are just the TC title repeated
        if step.summary.strip() == tc.summary.strip():
            issues.append('STEP_IS_TITLE: Step %d is just the TC title repeated' % step.step_num)

    if len(tc.steps) == 0:
        issues.append('NO_STEPS: TC has zero steps')
    elif len(tc.steps) == 1:
        issues.append('SINGLE_STEP: TC has only 1 step — may need more detail')

    return issues


def check_expected_results(tc):
    """Check 4: Are expected results specific (not generic)?"""
    issues = []
    for step in tc.steps:
        exp_low = step.expected.lower().strip()

        if not exp_low:
            issues.append('EMPTY_EXPECTED: Step %d has empty expected result' % step.step_num)
            continue

        # Check for generic expected results
        for generic in GENERIC_EXPECTED:
            if generic in exp_low:
                issues.append('GENERIC_EXPECTED: Step %d has generic expected result: "%s..."' % (
                    step.step_num, step.expected[:60]))
                break

        # Check if expected result is just the step summary repeated
        if step.expected.strip() == step.summary.strip():
            issues.append('EXPECTED_IS_STEP: Step %d expected result is just the step summary repeated' % step.step_num)

        # Check if expected result is the TC title/validation repeated verbatim
        if step.expected.strip() == tc.summary.strip():
            issues.append('EXPECTED_IS_TITLE: Step %d expected result is just the TC title' % step.step_num)

        # Check for validation = title (Chalk data issue where validation == title)
        if len(step.expected) > 80 and step.expected.strip() == tc.description.strip():
            issues.append('EXPECTED_IS_DESC: Step %d expected result is the full TC description' % step.step_num)

    return issues


def check_step_template_match(tc, feature_keyword):
    """Check 5: Do the steps match the TC summary (no wrong template)?"""
    issues = []
    tc_low = tc.summary.lower()
    steps_text = ' '.join(s.summary.lower() for s in tc.steps)

    if feature_keyword == 'swap_mdn':
        # Swap MDN TCs should have swap-related steps
        if 'swap' in tc_low or 'change sim' in tc_low or 'change imei' in tc_low:
            if 'sync' in steps_text and 'swap' not in steps_text:
                issues.append('WRONG_TEMPLATE: Swap MDN TC has Sync Subscriber steps')
            if 'order inquiry' in steps_text:
                issues.append('WRONG_TEMPLATE: Swap MDN TC has Order Inquiry steps')
            if 'activate subscriber' in steps_text and 'rollback' not in tc_low:
                issues.append('WRONG_TEMPLATE: Swap MDN TC has Activation steps')

        # Rollback TCs should have rollback-specific steps
        if 'rollback' in tc_low:
            if 'rollback' not in steps_text and 'restore' not in steps_text and 'revert' not in steps_text:
                issues.append('MISSING_ROLLBACK: Rollback TC has no rollback/restore steps')

        # eSIM vs pSIM: check Syniverse step correctness
        if 'esim' in tc_low:
            if 'deregister' in steps_text and 'register' in steps_text and 'change imsi' not in steps_text:
                issues.append('WRONG_SYNIVERSE: eSIM TC has Deregister+Register instead of Change IMSI')
        if 'psim' in tc_low:
            if 'change imsi' in steps_text and 'deregister' not in steps_text:
                issues.append('WRONG_SYNIVERSE: pSIM TC has Change IMSI instead of Deregister+Register')

    elif feature_keyword == 'sync_subscriber':
        # Sync TCs should have sync-related steps
        if 'sync' in tc_low or 'yl ' in tc_low or 'yd ' in tc_low:
            if 'swap' in steps_text and 'swap' not in tc_low:
                issues.append('WRONG_TEMPLATE: Sync Subscriber TC has Swap MDN steps')
            if 'activate subscriber' in steps_text and 'active' not in tc_low:
                issues.append('WRONG_TEMPLATE: Sync Subscriber TC has Activation steps')

        # YL sync should mention line status
        if 'yl ' in tc_low and 'line status' not in steps_text and 'status' not in steps_text:
            if 'no change' not in tc_low and 'no line status' not in tc_low:
                issues.append('MISSING_CONTEXT: YL sync TC steps don\'t mention line status change')

        # YD sync should mention device/ICCID
        if 'yd ' in tc_low and 'iccid' not in steps_text and 'device' not in steps_text:
            issues.append('MISSING_CONTEXT: YD sync TC steps don\'t mention ICCID/device')

    elif feature_keyword == 'order_inquiry':
        # Inquiry TCs should have inquiry-related steps
        if 'inquiry' in tc_low or 'reference number' in tc_low:
            if 'swap' in steps_text:
                issues.append('WRONG_TEMPLATE: Order Inquiry TC has Swap MDN steps')
            if 'sync subscriber' in steps_text:
                issues.append('WRONG_TEMPLATE: Order Inquiry TC has Sync Subscriber steps')

    return issues


def check_validation_equals_title(tc, chalk_scenarios):
    """Extra check: Chalk data issue where validation == title (copy-paste)."""
    issues = []
    # Find matching Chalk scenario
    for sc in chalk_scenarios:
        if sc.get('title', '')[:50] in tc.summary or tc.summary[:50] in sc.get('title', ''):
            val = sc.get('validation', '')
            title = sc.get('title', '')
            if val and title and val.strip() == title.strip():
                issues.append('CHALK_VAL_IS_TITLE: Chalk validation is identical to title — no real expected result defined')
            break
    return issues


# ================================================================
# FEATURE CONFIGS
# ================================================================

FEATURES = {
    'MWTGPROV-3949': {
        'keyword': 'swap_mdn',
        'summary': '[NSLNM, NENM, INTG]: New MVNO - Swap MDN',
        'description': (
            'Swap MDN feature allows agents to exchange phone numbers between two lines '
            'on the same customer account. Supports swapping between eSIM and pSIM combinations. '
            'Transaction types: EM (eSIM-to-eSIM), SM (pSIM-to-pSIM), AM (cross-type swap). '
            'NSL orchestrates Change SIM, Change IMEI, Syniverse updates, and MBO notifications.'
        ),
        'acceptance_criteria': (
            '* NSL shall process Swap MDN request from NBOP for two active TMO lines on same account\n'
            '* NSL shall validate both lines are Active, on same network provider (TMO), and not Second Lines\n'
            '* NSL shall trigger Change SIM and Change IMEI to Apollo NE for both lines\n'
            '* NSL shall trigger Syniverse updates: Change IMSI for eSIM, Deregister+Register for pSIM\n'
            '* NSL shall reserve temporary ICCID from reference table pool for eSIM swaps\n'
            '* NSL shall update MBO, Connection Manager, and Syniverse after successful swap\n'
            '* NSL shall update BI KAFKA topic with InProgress/Success/Failed status\n'
            '* NSL shall rollback all changes if any step fails during swap\n'
            '* NSL shall reject swap for lines on different accounts or different network providers\n'
            '* NSL shall reject swap for inactive, suspended, or Second Line MDNs\n'
            '* NSL shall validate schema of inbound request (HTTP 400 for invalid schema)\n'
            '* NSL shall update transaction history table for both lines\n'
            '* NSL shall handle timeouts from Apollo NE and Syniverse gracefully\n'
        ),
        'pi': 'PI-52',
        'channel': 'ITMBO',
        'labels': ['MWTGPROV-3949', 'PI-52', 'SwapMDN', 'TMO'],
    },
    'MWTGPROV-4009': {
        'keyword': 'sync_subscriber',
        'summary': '[NSLNM, NBOP, INTG]: New MVNO - Sync Subscriber',
        'description': (
            'Sync Subscriber API synchronizes line status, device, plan, and features between '
            'TMO network and NSL. Extends existing Verizon implementation for TMO parity. '
            'Transaction types: YL (line status), YD (device/ICCID), YP (features), YM (MDN), PL (plan). '
            'Integrates with NBOP UI for both TMO (new) and Verizon (BAU validation).'
        ),
        'acceptance_criteria': (
            '* NSL shall process YL sync for TMO: Active↔Deactive, Active↔Hotlined, Active↔Suspended\n'
            '* NSL shall notify ITMBO and EMM on line status changes\n'
            '* NSL shall trigger Syniverse RemoveSubscriber on deactivation/hotline/suspend\n'
            '* NSL shall trigger Syniverse CreateSubscriber on reactivation/un-hotline/un-suspend\n'
            '* NSL shall skip Syniverse calls for Smartwatch lines\n'
            '* NSL shall process YD sync when ICCID changes (SwapIMSI to Syniverse)\n'
            '* NSL shall skip YD sync when ICCID does not change\n'
            '* NSL shall process YP sync for feature changes (roaming tier, Global Day Pass → Syniverse)\n'
            '* NSL shall process PL sync (plan info from NSL to TMO, no ITMBO/EMM/Syniverse)\n'
            '* NSL shall validate LineId exists (ERR20 if not found)\n'
            '* NSL shall validate MDN exists (ERR20 if not found)\n'
            '* NSL shall validate MDN matches LineId (ERR161 if mismatch)\n'
            '* NBOP UI shall support triggering YL/YD/YP/PL for TMO subscribers\n'
            '* Verizon BAU sync-subscriber behavior shall remain unchanged\n'
        ),
        'pi': 'PI-53',
        'channel': 'ITMBO',
        'labels': ['MWTGPROV-4009', 'PI-53', 'SyncSubscriber', 'TMO'],
    },
    'MWTGPROV-4007': {
        'keyword': 'order_inquiry',
        'summary': '[NSLNM, NBOP, INTG]: New MVNO - Order Inquiry',
        'description': (
            'Order Inquiry API retrieves order status by Reference Number for TMO and Verizon. '
            'Enhances NBOP UI with TMO vs Verizon radio buttons and Reference Number input. '
            'Enforces validation: Reference Number must be 10-100 characters.'
        ),
        'acceptance_criteria': (
            '* NBOP UI shall display TMO vs Verizon radio buttons for users with MNO_TMO permission\n'
            '* NBOP UI shall validate Reference Number is 10-100 characters before API call\n'
            '* NSL API shall accept requestType=TMO for TMO subscribers\n'
            '* NSL API shall accept requestType=MNO for Verizon subscribers (BAU)\n'
            '* NSL API shall return ERR13 for Reference Number < 10 characters\n'
            '* NSL API shall return ERR14 for Reference Number > 100 characters\n'
            '* NSL API shall return 404 when no Reference Number is provided\n'
            '* NBOP UI shall display returned order status fields as read-only\n'
            '* Verizon BAU Order Inquiry behavior shall remain unchanged\n'
        ),
        'pi': 'PI-53',
        'channel': 'ITMBO',
        'labels': ['MWTGPROV-4007', 'PI-53', 'OrderInquiry', 'TMO'],
    },
}


# ================================================================
# MAIN DRY RUN
# ================================================================

def run_dry_run():
    all_issues = {}
    all_suites = {}

    for feature_id, config in FEATURES.items():
        print('\n' + '=' * 80)
        print('  DRY RUN: %s (%s)' % (feature_id, config['summary']))
        print('=' * 80)

        # Load Chalk from DB
        c = _conn()
        row = c.execute(
            "SELECT pi_label, scenarios_json FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1",
            (feature_id,)).fetchone()
        c.close()

        if not row:
            print('  ERROR: %s not found in DB cache! Run preload_db.py first.' % feature_id)
            continue

        chalk = load_chalk_as_object(feature_id, row['pi_label'])
        chalk_scenarios_raw = json.loads(row['scenarios_json'])
        print('  Loaded from DB @ %s: %d Chalk scenarios' % (row['pi_label'], len(chalk.scenarios)))

        # Build mock Jira
        jira = JiraIssue(
            key=feature_id,
            summary=config['summary'],
            description=config['description'],
            status='In Progress', priority='High', issue_type='Epic',
            assignee='QA Team', reporter='Dev Lead',
            labels=config['labels'],
            pi=config['pi'], channel=config['channel'],
            acceptance_criteria=config['acceptance_criteria'],
        )

        # Build test suite
        options = {
            'channel': [config['channel']], 'devices': ['Mobile'],
            'networks': ['4G', '5G'], 'sim_types': ['eSIM', 'pSIM'],
            'os_platforms': ['iOS', 'Android'],
            'include_positive': True, 'include_negative': True,
            'include_e2e': True, 'include_edge': True,
            'include_attachments': False,
            'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
        }

        suite = build_test_suite(jira, chalk, [], options, log=lambda m: None)
        all_suites[feature_id] = suite

        # ============================================================
        # RUN ALL 5 CHECKS ON EVERY TC
        # ============================================================
        feature_issues = []
        tc_issue_counts = {}

        print('\n  --- TC-BY-TC AUDIT ---')
        for tc in suite.test_cases:
            tc_issues = []

            # Check 1: Relevance
            tc_issues.extend(check_relevance(tc, config['keyword'], feature_id))

            # Check 2: Preconditions
            tc_issues.extend(check_preconditions(tc, config['keyword']))

            # Check 3: Step quality
            tc_issues.extend(check_step_quality(tc))

            # Check 4: Expected results
            tc_issues.extend(check_expected_results(tc))

            # Check 5: Template match
            tc_issues.extend(check_step_template_match(tc, config['keyword']))

            # Extra: Chalk validation == title
            tc_issues.extend(check_validation_equals_title(tc, chalk_scenarios_raw))

            tc_issue_counts[tc.sno] = len(tc_issues)

            # Print TC with issues
            status = '✅' if not tc_issues else '⚠️ ' if len(tc_issues) <= 2 else '❌'
            print('  %s TC%s [%-12s] %s (%d steps)' % (
                status, tc.sno.zfill(2), tc.category, tc.summary[:75], len(tc.steps)))

            if tc_issues:
                for issue in tc_issues:
                    print('      🔸 %s' % issue)
                feature_issues.extend([
                    {'tc': tc.sno, 'summary': tc.summary[:80], 'issue': iss}
                    for iss in tc_issues
                ])

        all_issues[feature_id] = feature_issues

        # ============================================================
        # FEATURE SUMMARY
        # ============================================================
        print('\n  --- FEATURE SUMMARY: %s ---' % feature_id)
        print('  Total TCs: %d' % len(suite.test_cases))
        print('  Total Steps: %d' % sum(len(tc.steps) for tc in suite.test_cases))

        # Category breakdown
        cats = {}
        for tc in suite.test_cases:
            cats.setdefault(tc.category, 0)
            cats[tc.category] += 1
        for cat, count in sorted(cats.items()):
            print('    %-15s %d TCs' % (cat, count))

        # Issue summary
        clean_tcs = sum(1 for v in tc_issue_counts.values() if v == 0)
        warn_tcs = sum(1 for v in tc_issue_counts.values() if 0 < v <= 2)
        fail_tcs = sum(1 for v in tc_issue_counts.values() if v > 2)
        print('\n  ✅ Clean TCs:    %d' % clean_tcs)
        print('  ⚠️  Warning TCs:  %d' % warn_tcs)
        print('  ❌ Failed TCs:   %d' % fail_tcs)
        print('  Total issues:   %d' % len(feature_issues))

        # Issue type breakdown
        issue_types = {}
        for iss in feature_issues:
            itype = iss['issue'].split(':')[0]
            issue_types.setdefault(itype, 0)
            issue_types[itype] += 1
        if issue_types:
            print('\n  Issue breakdown:')
            for itype, count in sorted(issue_types.items(), key=lambda x: -x[1]):
                print('    %-30s %d' % (itype, count))

    # ================================================================
    # CROSS-FEATURE SUMMARY
    # ================================================================
    print('\n' + '=' * 80)
    print('  CROSS-FEATURE SUMMARY')
    print('=' * 80)

    total_tcs = 0
    total_issues = 0
    for fid, suite in all_suites.items():
        issues = all_issues.get(fid, [])
        tc_count = len(suite.test_cases)
        step_count = sum(len(tc.steps) for tc in suite.test_cases)
        total_tcs += tc_count
        total_issues += len(issues)
        health = '✅' if len(issues) == 0 else '⚠️' if len(issues) <= 5 else '❌'
        print('  %s %-18s %3d TCs | %4d steps | %2d issues' % (
            health, fid, tc_count, step_count, len(issues)))

    print('\n  Total TCs:    %d' % total_tcs)
    print('  Total Issues: %d' % total_issues)

    # Generate Excel for each
    print('\n  --- GENERATING EXCEL ---')
    for fid, suite in all_suites.items():
        out_path = generate_excel(suite, log=lambda m: None)
        print('  %s → %s' % (fid, out_path))

    print('\nDry run complete.')


if __name__ == '__main__':
    run_dry_run()
