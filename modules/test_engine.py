"""
test_engine.py -- Core test suite builder engine.
Merges Jira + Chalk + Attachments + Uploads -> structured test cases.
Produces checkpoint-quality output with enriched descriptions, structured steps,
specific expected results, and attachment-driven gap coverage.
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from .jira_fetcher import JiraIssue
from .chalk_parser import ChalkData, ChalkScenario
from .doc_parser import ParsedDoc
from .step_templates import get_step_chain
from .instruction_parser import parse_instructions, apply_adjustments


@dataclass
class TestStep:
    step_num: int = 0
    summary: str = ''
    expected: str = ''


@dataclass
class TestCase:
    sno: str = ''
    summary: str = ''
    description: str = ''
    preconditions: str = ''
    steps: List[TestStep] = field(default_factory=list)
    story_linkage: str = ''
    label: str = ''
    category: str = 'Happy Path'


@dataclass
class TestSuite:
    feature_id: str = ''
    feature_title: str = ''
    feature_desc: str = ''
    test_cases: List[TestCase] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    ac_traceability: Dict[str, List[str]] = field(default_factory=dict)
    open_items: List[str] = field(default_factory=list)
    open_item_coverage: Dict[str, str] = field(default_factory=dict)
    scope: str = ''
    rules: str = ''
    data_sources: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    channel: str = 'ITMBO'
    devices: List[str] = field(default_factory=list)
    networks: List[str] = field(default_factory=list)
    sim_types: List[str] = field(default_factory=list)
    pi: str = ''
    jira_status: str = ''
    jira_priority: str = ''
    jira_assignee: str = ''
    jira_reporter: str = ''
    jira_labels: List[str] = field(default_factory=list)
    jira_links: List[Dict] = field(default_factory=list)
    attachment_names: List[str] = field(default_factory=list)
    groups: Dict[str, List] = field(default_factory=dict)  # auto-detected TC groups for multi-sheet
    combinations: List[Dict] = field(default_factory=list)  # device matrix combinations


# ================================================================
# MAIN ENTRY
# ================================================================

def build_test_suite(jira, chalk, parsed_docs, options, log=print):
    # Parse custom instructions if provided
    strategy = options.get('strategy', 'Smart Suite (Recommended)')
    custom_text = options.get('custom_instructions', '')
    adjustments = {}
    if strategy == 'Custom Instructions' and custom_text:
        log('[ENGINE] Parsing custom instructions...')
        adjustments = parse_instructions(custom_text, options, log)
        options = apply_adjustments(options, adjustments)
        log('[ENGINE]   Adjusted options applied')
    elif strategy == 'Full Matrix':
        log('[ENGINE] Full Matrix mode — all combinations will be generated')

    suite = TestSuite(
        feature_id=jira.key, feature_title=jira.summary,
        channel=options.get('channel', jira.channel),
        devices=options.get('devices', ['Mobile']),
        networks=options.get('networks', ['4G', '5G']),
        sim_types=options.get('sim_types', ['eSIM', 'pSIM']),
        pi=jira.pi, jira_status=jira.status, jira_priority=jira.priority,
        jira_assignee=jira.assignee, jira_reporter=jira.reporter,
        jira_labels=jira.labels,
        jira_links=[{'key': l['key'], 'summary': l['summary']} for l in jira.linked_issues],
        attachment_names=[a.filename for a in jira.attachments],
    )

    log('[ENGINE] Step 1: Extracting acceptance criteria...')
    suite.acceptance_criteria = _extract_ac(jira)
    log('[ENGINE]   Found %d AC items' % len(suite.acceptance_criteria))

    log('[ENGINE] Step 2: Building feature description...')
    suite.feature_desc = _build_feature_desc(jira, chalk)
    if chalk:
        suite.scope = chalk.scope
        suite.rules = chalk.rules

    log('[ENGINE] Step 3: Building test cases from Chalk...')
    if chalk and chalk.scenarios:
        for i, sc in enumerate(chalk.scenarios, 1):
            tc = _chalk_scenario_to_tc(sc, i, jira.key)
            suite.test_cases.append(tc)
        log('[ENGINE]   Built %d TCs from Chalk' % len(suite.test_cases))
    else:
        log('[ENGINE]   [WARN] No Chalk scenarios -- building from Jira description')
        suite.test_cases = _build_from_jira_only(jira)
        suite.warnings.append('No Chalk data available -- TCs built from Jira description only')

    log('[ENGINE] Step 4: Cross-checking with attachments...')
    if parsed_docs and options.get('include_attachments', True):
        gaps = _cross_check_attachments(suite, parsed_docs, jira.key, log)
        if gaps:
            log('[ENGINE]   Found %d gap TCs from attachments' % len(gaps))
            suite.test_cases.extend(gaps)

    if options.get('include_negative', True):
        log('[ENGINE] Step 5: Generating negative scenarios...')
        neg = _generate_negative_scenarios(suite, jira.key, log)
        suite.test_cases.extend(neg)
        log('[ENGINE]   Added %d negative TCs' % len(neg))

    log('[ENGINE] Step 6: Preparing for expansion...')
    # Renumbering happens after matrix expansion (Step 9)

    # Step 6b: Apply custom instruction extras
    if adjustments:
        _next = len(suite.test_cases) + 1
        # Extra scenarios from custom instructions
        for desc in adjustments.get('extra_scenarios', []):
            suite.test_cases.append(TestCase(
                sno=str(_next), summary='TC%02d_%s - Custom: %s' % (_next, jira.key, desc[:60]),
                description=desc, preconditions='As per custom instruction',
                story_linkage=jira.key, label=jira.key, category='Happy Path',
                steps=[TestStep(1,'Execute: %s'%desc,'Completed'),TestStep(2,'Verify results','Results match expected')]))
            _next += 1
            log('[ENGINE]   Added custom scenario: %s' % desc[:50])
        # Boundary testing
        if adjustments.get('include_boundary'):
            suite.test_cases.append(TestCase(
                sno=str(_next), summary='TC%02d_%s - Boundary: Verify field length and format limits' % (_next, jira.key),
                description='Verify system handles boundary values: max length MDN, special chars, empty strings, min/max numeric values.',
                preconditions='System in ready state', story_linkage=jira.key, label=jira.key, category='Edge Case',
                steps=[TestStep(1,'Test max length input','Accepted or rejected gracefully'),
                       TestStep(2,'Test min length input','Handled correctly'),
                       TestStep(3,'Test special characters','No injection or crash'),
                       TestStep(4,'Verify error messages for boundary violations','Clear error messages returned')]))
            _next += 1; log('[ENGINE]   Added boundary testing TC')
        # Data integrity
        if adjustments.get('include_data_integrity'):
            suite.test_cases.append(TestCase(
                sno=str(_next), summary='TC%02d_%s - Data Integrity: Verify no data corruption after operation' % (_next, jira.key),
                description='Verify data integrity across NSL DB, MBO, Syniverse, and NBOP after the operation.',
                preconditions='Operation completed successfully', story_linkage=jira.key, label=jira.key, category='Happy Path',
                steps=[TestStep(1,'Compare pre and post operation data in NSL DB','Data consistent'),
                       TestStep(2,'Verify MBO data matches NSL','MBO in sync'),
                       TestStep(3,'Verify NBOP tables reflect correct state','NBOP consistent'),
                       TestStep(4,'Verify no orphaned or duplicate records','No data anomalies')]))
            _next += 1; log('[ENGINE]   Added data integrity TC')
        # Auth failure
        if adjustments.get('include_auth_failure'):
            suite.test_cases.append(TestCase(
                sno=str(_next), summary='TC%02d_%s - Negative: Verify API with expired/invalid OAuth token' % (_next, jira.key),
                description='Verify API rejects requests with expired, invalid, or missing OAuth token.',
                preconditions='Expired or invalid OAuth token', story_linkage=jira.key, label=jira.key, category='Negative',
                steps=[TestStep(1,'Send API request with expired token','Request sent'),
                       TestStep(2,'Verify HTTP 401 Unauthorized returned','401 returned with clear message'),
                       TestStep(3,'Verify no data modified','System state unchanged')]))
            _next += 1; log('[ENGINE]   Added auth failure TC')
        # Rollback for all
        if adjustments.get('include_rollback_all'):
            suite.test_cases.append(TestCase(
                sno=str(_next), summary='TC%02d_%s - Rollback: Verify rollback on mid-operation failure' % (_next, jira.key),
                description='Verify system rolls back all changes when operation fails midway.',
                preconditions='Simulate failure at each step', story_linkage=jira.key, label=jira.key, category='Negative',
                steps=[TestStep(1,'Execute operation until failure point','Operation fails at expected step'),
                       TestStep(2,'Verify rollback triggered automatically','Rollback initiated'),
                       TestStep(3,'Verify all prior changes reverted','Original state restored'),
                       TestStep(4,'Verify MBO and Syniverse notified of rollback','External systems notified'),
                       TestStep(5,'Verify transaction history records rollback','Audit trail complete')]))
            _next += 1; log('[ENGINE]   Added rollback TC')

    log('[ENGINE] Step 7: Building AC traceability...')
    suite.ac_traceability = _build_ac_traceability(suite)

    for doc in parsed_docs:
        suite.open_items.extend(doc.open_items)
    if chalk:
        suite.open_items.extend(chalk.open_items)
    # Deduplicate open items
    seen_items = set()
    unique_items = []
    for item in suite.open_items:
        key = item.strip().lower()[:80]
        if key not in seen_items:
            seen_items.add(key)
            unique_items.append(item)
    suite.open_items = unique_items

    suite.data_sources.append('Jira: %s (Description, AC, Labels, Links)' % jira.key)
    if chalk and chalk.scenarios:
        suite.data_sources.append('Chalk: %s section (%d scenarios)' % (chalk.feature_id, len(chalk.scenarios)))
    for doc in parsed_docs:
        suite.data_sources.append('Attachment: %s (%d paragraphs, %d tables)' % (doc.filename, len(doc.paragraphs), len(doc.tables)))

    total_steps = sum(len(tc.steps) for tc in suite.test_cases)
    log('[ENGINE] [OK] Suite complete: %d TCs | %d steps' % (len(suite.test_cases), total_steps))

    # Step 8: Device Matrix Expansion (only for core/positive TCs)
    log('[ENGINE] Step 8: Device matrix expansion...')
    _strategy = options.get('strategy', 'Smart Suite (Recommended)')
    if _strategy == 'Full Matrix':
        expanded = _expand_by_matrix(suite, options, log, max_combos=999)
    else:
        expanded = _expand_by_matrix(suite, options, log, max_combos=4)
    if expanded:
        suite.test_cases = expanded
        log('[ENGINE]   Expanded to %d TCs' % len(suite.test_cases))
    else:
        log('[ENGINE]   No expansion needed')

    # Step 9: Renumber after expansion
    log('[ENGINE] Step 9: Final renumbering...')
    for i, tc in enumerate(suite.test_cases, 1):
        tc.sno = str(i)
        for j, step in enumerate(tc.steps, 1):
            step.step_num = j

    # Step 10: Auto-group TCs by pattern detection
    log('[ENGINE] Step 10: Auto-grouping test cases...')
    suite.groups = _auto_group_tcs(suite.test_cases)
    log('[ENGINE]   %d groups detected' % len(suite.groups))
    for gname, gtcs in suite.groups.items():
        log('[ENGINE]     %s: %d TCs' % (gname, len(gtcs)))

    return suite


# ================================================================
# ACCEPTANCE CRITERIA EXTRACTION
# ================================================================

def _extract_ac(jira):
    ac_text = jira.acceptance_criteria
    if not ac_text:
        return []
    items = []
    for line in ac_text.split('\n'):
        line = line.strip()
        line = re.sub(r'\{[^}]+\}', '', line)
        line = re.sub(r'\*([^*]+)\*', r'\1', line)
        line = re.sub(r'\+([^+]+)\+', r'\1', line)
        line = line.strip(' *-#')
        if not line or len(line) < 10:
            continue
        if any(kw in line.lower() for kw in ['shall', 'must', 'should', 'verify', 'ensure',
                                               'derived', 'forwarded', 'available', 'kpi', 'sla', 'hld']):
            items.append(line)
    return items if items else [ac_text[:500]]


# ================================================================
# FEATURE DESCRIPTION (Row 1 of Excel)
# ================================================================

def _build_feature_desc(jira, chalk):
    parts = []
    if chalk and chalk.scope:
        parts.append(chalk.scope.strip())
    if chalk and chalk.rules:
        parts.append(chalk.rules.strip())
    if not parts and jira.description:
        desc = re.sub(r'\{[^}]+\}', '', jira.description)
        desc = re.sub(r'\[([^]]+)\]', r'\1', desc)
        desc = re.sub(r'\*([^*]+)\*', r'\1', desc)
        parts.append(desc[:500])
    ac_items = _extract_ac(jira)
    if ac_items:
        parts.append('Acceptance Criteria: ' + '; '.join(ac_items[:3]))
    return ' '.join(parts) if parts else jira.summary


# ================================================================
# CHALK SCENARIO -> TEST CASE (the key enrichment logic)
# ================================================================

def _chalk_scenario_to_tc(sc, idx, feature_id):
    """Convert a Chalk scenario into a rich, checkpoint-quality TestCase."""

    # Description: use validation text (rich PRR output), not title
    description = sc.validation if sc.validation and sc.validation != sc.title else sc.title

    # Preconditions: numbered list
    pre_lines = []
    if sc.prereq:
        raw = sc.prereq.replace('Pre-req:', '').replace('Pre-condition:', '').strip()
        parts = [p.strip() for p in re.split(r'[.\n]', raw) if p.strip() and len(p.strip()) > 5]
        for i, p in enumerate(parts, 1):
            pre_lines.append('%d.\t%s' % (i, p))
    has_cdr = bool(sc.cdr_input) or 'cdr' in sc.title.lower() or 'prr' in (sc.validation or '').lower()
    has_api = 'api' in (sc.title + ' ' + (sc.validation or '')).lower()
    has_ui = any(kw in (sc.title + ' ' + (sc.validation or '')).lower() for kw in ['menu', 'display', 'ui', 'nbop', 'portal'])
    if has_cdr:
        existing = '\n'.join(pre_lines).lower()
        if 'mediation' not in existing:
            pre_lines.append('%d.\tMediation and PRR batch jobs are up and running' % (len(pre_lines) + 1))
        if 'sftp' not in existing:
            pre_lines.append('%d.\tSFTP access available via FileZilla' % (len(pre_lines) + 1))
    elif has_api:
        existing = '\n'.join(pre_lines).lower()
        if 'api' not in existing and 'endpoint' not in existing:
            pre_lines.append('%d.\tAPI endpoint accessible and authenticated' % (len(pre_lines) + 1))
    elif has_ui:
        existing = '\n'.join(pre_lines).lower()
        if 'portal' not in existing and 'nbop' not in existing:
            pre_lines.append('%d.\tNBOP portal accessible and user logged in' % (len(pre_lines) + 1))
    preconditions = '\n'.join(pre_lines) if pre_lines else sc.prereq

    steps = []
    step_num = 1

    # Step 1: Activate
    if 'active' in (sc.prereq or '').lower() or 'activate' in (sc.prereq or '').lower():
        steps.append(TestStep(step_num, 'Activate subscriber line in TMO',
                              'Subscriber line should be active in TMO'))
        step_num += 1

    # Step 2: CDR Input (formatted as bullets)
    if sc.cdr_input:
        cdr_text = sc.cdr_input.replace('CDR Input:', '').strip()
        cdr_parts = [p.strip() for p in re.split(r',\s*', cdr_text) if p.strip()]
        if len(cdr_parts) > 2:
            formatted = 'Generate/mock CDR with:\n' + '\n'.join('- %s' % p for p in cdr_parts)
        else:
            formatted = 'Generate/mock CDR with: %s' % cdr_text
        # Contextual expected result based on scenario type
        cdr_exp = 'CDR record created with %s attributes' % _scenario_context(sc)
        steps.append(TestStep(step_num, formatted, cdr_exp))
        step_num += 1

    # Chalk-provided steps (Step 1:, Step 2:, etc.) — use THESE, don't add duplicates
    if sc.steps:
        for s in sc.steps:
            clean = re.sub(r'^Step\s*\d+\s*:\s*', '', s).strip()
            # Try to generate a meaningful expected result from the step text
            exp = _step_expected_result(clean, sc)
            steps.append(TestStep(step_num, clean, exp))
            step_num += 1
    else:
        # No Chalk steps -- derivation rule only (for CDR features)
        if sc.derivation_rule:
            rule_text = sc.derivation_rule.replace('Derivation Rule:', '').strip()
            steps.append(TestStep(step_num,
                'Derivation Rule applied: %s' % rule_text,
                'System correctly applies %s derivation rule' % _scenario_context(sc)))
            step_num += 1

        if has_cdr:
            steps.append(TestStep(step_num,
                'Wait for mediation and PRR batch processing to complete',
                'Mediation processes the usage records successfully'))
            step_num += 1
            steps.append(TestStep(step_num,
                'Connect to SFTP and download PRR file',
                'PRR file downloaded successfully'))
            step_num += 1
        # For API/UI/Workflow: DON'T add generic steps here
        # Let the template fallback handle it with proper step chains

    # Final Verify step with FULL validation as expected result
    # But ONLY if we already have other steps (don't make verify the only step)
    if sc.validation and sc.validation != sc.title and len(steps) > 0:
        verify_text = 'Verify PRR output:\n%s' % _format_validation_bullets(sc.validation)
        steps.append(TestStep(step_num, verify_text, sc.validation))
        step_num += 1

    # Variations step
    if sc.variations:
        clean_vars = [v for v in sc.variations if v.strip() and v.strip() != 'Variations']
        if clean_vars:
            var_text = 'Verify variations:\n' + '\n'.join(clean_vars)
            steps.append(TestStep(step_num, var_text,
                'Each variation correctly maps to expected country code'))

    # Fallback if no steps at all (numbered format scenarios land here)
    if not steps:
        # Use domain-specific step templates
        context = _scenario_context(sc)
        chain = get_step_chain(sc.title, sc.validation, context)
        for i, (step_sum, step_exp) in enumerate(chain, 1):
            steps.append(TestStep(i, step_sum, step_exp))

    return TestCase(
        sno=str(idx),
        summary='TC%02d_%s - %s' % (idx, feature_id, sc.title),
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=sc.category or 'Happy Path',
    )


def _scenario_context(sc):
    """Return a short context string based on scenario type."""
    t = (sc.title + ' ' + (sc.validation or '')).lower()
    # CDR/PRR
    if 'ild outgoing' in t: return 'ILD outgoing'
    if 'ild incoming' in t: return 'ILD incoming'
    if 'roaming' in t and 'receiving' in t: return 'IR incoming'
    if 'roaming' in t and 'usa' in t: return 'IR call to USA'
    if 'roaming' in t and 'india' in t: return 'IR outgoing to India'
    if 'roaming' in t and 'local' in t: return 'IR outgoing to local'
    if 'roaming' in t: return 'International Roaming'
    if 'domestic' in t: return 'domestic call'
    if 'canada' in t: return 'Canada call'
    # API
    if 'swap' in t and ('esim' in t or 'psim' in t): return 'SIM swap'
    if 'swap mdn' in t: return 'Swap MDN'
    if 'change sim' in t: return 'Change SIM'
    if 'change imei' in t: return 'Change IMEI'
    if 'change feature' in t: return 'Change Feature'
    if 'change rateplan' in t or 'change rate' in t: return 'Change Rateplan'
    if 'activate' in t or 'activation' in t: return 'Activation'
    if 'deactivat' in t: return 'Deactivation'
    if 'suspend' in t: return 'Suspend'
    if 'reconnect' in t or 'restore' in t: return 'Reconnect'
    # Notification
    if 'notification' in t or 'kafka' in t: return 'Notification'
    if 'usage' in t: return 'Usage'
    # UI
    if 'menu' in t or 'display' in t or 'ui' in t or 'nbop' in t: return 'UI'
    # E2E
    if 'e2e' in t or 'end-to-end' in t: return 'E2E'
    # Rollback
    if 'rollback' in t: return 'Rollback'
    # Upstream
    if 'mbo' in t or 'syniverse' in t or 'apollo' in t: return 'Integration'
    return 'specified'


def _step_expected_result(step_text, sc):
    """Generate a meaningful expected result for a Chalk step."""
    s = step_text.lower()
    # CDR/PRR specific
    if 'mediation collects' in s: return 'Mediation picks up CDR file successfully'
    if 'cdr-to-prr transformation' in s: return 'System identifies record and applies transformation'
    if 'parses call to tn' in s: return 'E.164 parsing correctly extracts country code'
    if 'prr populated' in s or 'prr:' in s: return sc.validation or 'PRR populated with correct values'
    if 'prr distributed' in s: return 'PRR file delivered to Amdocs SFTP path'
    if 'verify prr' in s: return sc.validation or 'PRR has correct country codes'
    if 'cdr file arrives' in s: return 'CDR file collected from SFTP SDM'
    if 'cdr collected' in s: return 'CDR collected and transformed'
    if 'tmo generates' in s: return 'CDR generated successfully by TMO'
    # API specific
    if 'api' in s and ('returns' in s or 'response' in s): return 'API returns expected response code and payload'
    if 'api' in s and 'call' in s: return 'API call executes successfully'
    if 'http 200' in s or 'returns 200' in s: return 'API returns HTTP 200 success'
    if 'http 400' in s or 'returns 400' in s: return 'API returns HTTP 400 with appropriate error message'
    if 'http 202' in s or 'returns 202' in s: return 'API returns HTTP 202 accepted'
    # Activation/Line
    if 'activate' in s: return 'Subscriber line should be active in TMO'
    if 'line' in s and ('active' in s or 'status' in s): return 'Line status verified as expected'
    # UI specific
    if 'display' in s or 'menu' in s or 'visible' in s: return 'UI element displayed correctly'
    if 'click' in s or 'select' in s: return 'User action completed successfully'
    # Notification/Event
    if 'notification' in s or 'kafka' in s: return 'Notification/event sent successfully with correct payload'
    if 'event' in s and 'status' in s: return 'Event status updated correctly'
    # Database/Transaction
    if 'transaction' in s and 'history' in s: return 'Transaction history record created with correct details'
    if 'database' in s or 'table' in s or 'insert' in s: return 'Database record created/updated correctly'
    # Upstream systems
    if 'mbo' in s: return 'MBO system updated with correct data'
    if 'syniverse' in s: return 'Syniverse configuration updated correctly'
    if 'connection manager' in s: return 'Connection Manager notified successfully'
    # Rollback/Error
    if 'rollback' in s or 'restore' in s: return 'System rolled back to original state successfully'
    if 'error' in s or 'fail' in s: return 'Error handled gracefully with appropriate message'
    if 'retry' in s: return 'Retry mechanism triggered and completed'
    # Swap specific
    if 'swap' in s and ('success' in s or 'complete' in s): return 'Swap operation completed successfully'
    if 'iccid' in s or 'imei' in s: return 'Device identifiers updated correctly'
    if 'mdn' in s and ('update' in s or 'change' in s): return 'MDN updated correctly in system'
    # Generic but meaningful
    if 'verify' in s or 'validate' in s or 'check' in s:
        return sc.validation if sc.validation else 'Verification passed as expected'
    if 'confirm' in s: return 'Confirmation received successfully'
    # Use validation text if available
    if sc.validation and len(sc.validation) > 10:
        return sc.validation
    return 'Step completed successfully'


def _format_validation_bullets(validation):
    """Convert validation text into bullet-point format."""
    parts = re.split(r'[.;]\s+', validation)
    if len(parts) <= 1:
        return validation
    bullets = []
    for p in parts:
        p = p.strip()
        if p and len(p) > 5:
            bullets.append('- %s' % p)
    return '\n'.join(bullets) if bullets else validation


# ================================================================
# FALLBACK: Build from Jira only
# ================================================================

def _build_from_jira_only(jira):
    tcs = []
    tc = TestCase(
        sno='1',
        summary='TC01_%s - Verify basic functionality: %s' % (jira.key, jira.summary[:80]),
        description=jira.description[:500] if jira.description else jira.summary,
        preconditions='1.\tRefer to Jira description for setup requirements\n2.\tEnsure system is in ready state',
        story_linkage=jira.key, label=jira.key, category='Happy Path',
        steps=[
            TestStep(1, 'Set up preconditions as per Jira description', 'Preconditions met'),
            TestStep(2, 'Execute the feature workflow', 'Workflow executes successfully'),
            TestStep(3, 'Verify output matches acceptance criteria', 'All acceptance criteria met'),
        ])
    tcs.append(tc)
    return tcs


# ================================================================
# ATTACHMENT CROSS-CHECK: Generate SPECIFIC gap TCs
# ================================================================

def _cross_check_attachments(suite, docs, feature_id, log=print):
    gap_tcs = []
    existing_text = ' '.join([tc.summary + ' ' + tc.description + ' ' +
                              ' '.join(s.summary for s in tc.steps)
                              for tc in suite.test_cases]).lower()
    next_idx = len(suite.test_cases) + 1

    for doc in docs:
        # ── Open items: Generate specific TCs ──
        for item in doc.open_items:
            item_kw = [w.lower() for w in re.findall(r'\b\w{4,}\b', item)]
            covered = sum(1 for kw in item_kw if kw in existing_text)
            if covered < len(item_kw) * 0.3:
                tc = _build_open_item_tc(item, next_idx, feature_id, doc.filename)
                if tc:
                    gap_tcs.append(tc)
                    suite.open_item_coverage[item[:80]] = 'TC%02d' % next_idx
                    next_idx += 1
                    log('[ENGINE]   Gap TC%02d for: %s' % (next_idx - 1, item[:60]))

        # ── Tables: Check for uncovered CDR/call scenarios ──
        for table in doc.tables:
            if len(table) < 2:
                continue
            header = ' '.join(str(c).lower() for c in table[0])
            if any(kw in header for kw in ['call', 'direction', 'origin', 'destination', 'cdr', 'record']):
                for row in table[1:]:
                    row_text = ' '.join(str(c).lower() for c in row)
                    row_kw = [w for w in re.findall(r'\b\w{4,}\b', row_text)]
                    covered = sum(1 for kw in row_kw if kw in existing_text)
                    if covered < len(row_kw) * 0.3 and len(row_kw) > 2:
                        suite.warnings.append('Attachment %s has uncovered data: %s' % (doc.filename, row_text[:100]))

    return gap_tcs


def _build_open_item_tc(item, idx, feature_id, filename):
    """Build a specific TC for an open item (not generic)."""
    item_low = item.lower()

    # NANP / Canada / area code distinction
    if 'nanp' in item_low or 'area code' in item_low or '+1' in item:
        return TestCase(
            sno=str(idx),
            summary='TC%02d_%s - Verify NANP countries (+1 prefix) - area code distinction' % (idx, feature_id),
            description='Per %s: %s. Verify system uses area code lookup to distinguish NANP countries from US.' % (filename, item),
            preconditions='1.\tActive TMO Phone line\n2.\tSubscriber makes ILD call to NANP country\n3.\tMediation and PRR batch jobs are up and running\n4.\tSFTP access available',
            story_linkage=feature_id, label=feature_id, category='Edge Case',
            steps=[
                TestStep(1, 'Activate subscriber line in TMO', 'Subscriber line should be active in TMO'),
                TestStep(2, 'Generate/mock CDR with:\n- Record Type=1\n- Call Direction=0\n- Call To TN=+1 NANP number (e.g. +18765551234 Jamaica)', 'CDR record created'),
                TestStep(3, 'Derivation Rule: Call To TN starts with +1 - system must use Area Code to distinguish NANP from US', 'System identifies need for area code lookup'),
                TestStep(4, 'Wait for mediation and PRR batch processing to complete', 'Mediation processes successfully'),
                TestStep(5, 'Connect to SFTP and download PRR file', 'PRR file downloaded successfully'),
                TestStep(6, 'Verify PRR output:\n- System correctly identifies NANP country using area code lookup, NOT as USA\n- to_country_code matches NANP country', 'System correctly distinguishes NANP country from US using area code lookup.'),
            ])

    # Portal / reporting impact
    if 'portal' in item_low or 'reporting' in item_low or 'display' in item_low:
        return TestCase(
            sno=str(idx),
            summary='TC%02d_%s - Verify Portal/Reporting impact - call origin display' % (idx, feature_id),
            description='Per %s: %s. Verify portal/reporting correctly displays call origin details.' % (filename, item),
            preconditions='1.\tActive TMO Phone line\n2.\tSubscriber receives incoming ILD call\n3.\tMediation and PRR batch jobs are up and running\n4.\tAccess to portal/reporting system',
            story_linkage=feature_id, label=feature_id, category='Edge Case',
            steps=[
                TestStep(1, 'Activate subscriber line in TMO', 'Subscriber line should be active in TMO'),
                TestStep(2, 'Generate/mock incoming ILD CDR', 'CDR record created'),
                TestStep(3, 'Wait for mediation and PRR batch processing to complete', 'Mediation processes successfully'),
                TestStep(4, 'Verify Amdocs billing: incoming ILD treated as domestic - no billing impact', 'Amdocs processes as domestic - no billing impact'),
                TestStep(5, 'Verify Portal/Reporting:\n- Call Origin should be correctly displayed\n- Reporting should capture origin country\n- No missing data in portal', 'Portal/Reporting correctly displays call origin. Addresses open item from %s.' % filename),
            ])

    # Domestic call inconsistency
    if 'domestic' in item_low or 'inconsisten' in item_low or 'call_to_tn' in item_low:
        return TestCase(
            sno=str(idx),
            summary='TC%02d_%s - Verify Domestic call handling - field consistency' % (idx, feature_id),
            description='Per %s: %s. Verify domestic calls pass through without country code enrichment.' % (filename, item),
            preconditions='1.\tActive TMO Phone line\n2.\tSubscriber makes/receives domestic call\n3.\tMediation and PRR batch jobs are up and running\n4.\tSFTP access available',
            story_linkage=feature_id, label=feature_id, category='Edge Case',
            steps=[
                TestStep(1, 'Activate subscriber line in TMO', 'Subscriber line should be active in TMO'),
                TestStep(2, 'Generate/mock domestic CDR (both outgoing and incoming)', 'CDR records created'),
                TestStep(3, 'Wait for mediation and PRR batch processing to complete', 'Mediation processes successfully'),
                TestStep(4, 'Connect to SFTP and download PRR file', 'PRR file downloaded successfully'),
                TestStep(5, 'Verify PRR output:\n- Domestic call passes through WITHOUT country code enrichment\n- No billing impact\n- Call_To_TN field consistency verified', 'Domestic call CDR passes through without country code enrichment. No billing impact.'),
            ])

    # Generic open item (fallback)
    return TestCase(
        sno=str(idx),
        summary='TC%02d_%s - Verify open item: %s' % (idx, feature_id, item[:60]),
        description='Per %s: %s' % (filename, item),
        preconditions='1.\tRefer to %s for details\n2.\tSystem in ready state' % filename,
        story_linkage=feature_id, label=feature_id, category='Edge Case',
        steps=[
            TestStep(1, 'Set up preconditions per attachment', 'Preconditions met'),
            TestStep(2, 'Execute scenario: %s' % item[:150], 'Scenario executes'),
            TestStep(3, 'Verify results address the open item:\n- %s' % item[:200], 'Open item validated: %s' % item[:100]),
        ])


# ================================================================
# NEGATIVE SCENARIOS
# ================================================================

def _generate_negative_scenarios(suite, feature_id, log=print):
    neg_tcs = []
    next_idx = len(suite.test_cases) + 1
    all_text = ' '.join([tc.summary + tc.description + ' '.join(s.summary for s in tc.steps)
                         for tc in suite.test_cases]).lower()

    # Check what already exists (don't duplicate if Chalk already has negatives)
    existing_neg = sum(1 for tc in suite.test_cases if tc.category == 'Negative')

    has_input = any(kw in all_text for kw in ['input', 'cdr', 'record type', 'field'])
    has_mapping = any(kw in all_text for kw in ['mapping', 'derive', 'parse', 'prefix', 'country'])
    has_e2e = any(kw in all_text for kw in ['end-to-end', 'e2e', 'amdocs'])
    has_api = any(kw in all_text for kw in ['api', 'http', 'endpoint', 'request', 'response'])
    has_ui = any(kw in all_text for kw in ['menu', 'display', 'portal', 'nbop', 'ui'])
    has_auth = any(kw in all_text for kw in ['auth', 'token', 'login', 'session'])

    # Skip if Chalk already provided many negatives (like 3949 with 14 negative TCs)
    if existing_neg >= 5:
        log('[ENGINE]   Skipping auto-negatives: Chalk already has %d negative TCs' % existing_neg)
        return neg_tcs

    if has_input and 'invalid/null input' not in all_text:
        neg_tcs.append(TestCase(
            sno=str(next_idx),
            summary='TC%02d_%s - Negative: Verify handling of invalid/null input fields' % (next_idx, feature_id),
            description='Verify system handles gracefully when required input fields are null, empty, or malformed.',
            preconditions='1.\tSystem in ready state\n2.\tPrepare input with null or invalid fields',
            story_linkage=feature_id, label=feature_id, category='Negative',
            steps=[
                TestStep(1, 'Prepare input with null/empty required fields', 'Input prepared with invalid data'),
                TestStep(2, 'Submit the input to the system', 'System processes without crash'),
                TestStep(3, 'Verify graceful handling:\n- No crash or exception\n- Appropriate error message\n- No data corruption', 'System handles invalid input gracefully.'),
            ])); next_idx += 1

    if has_mapping and 'unrecognized' not in all_text:
        neg_tcs.append(TestCase(
            sno=str(next_idx),
            summary='TC%02d_%s - Negative: Verify handling of unrecognized/invalid codes' % (next_idx, feature_id),
            description='Verify system handles unrecognized codes without incorrect mapping.',
            preconditions='1.\tSystem in ready state\n2.\tPrepare input with unrecognized codes',
            story_linkage=feature_id, label=feature_id, category='Negative',
            steps=[
                TestStep(1, 'Prepare input with unrecognized values', 'Input prepared'),
                TestStep(2, 'Submit to system', 'System processes without crash'),
                TestStep(3, 'Verify no incorrect mapping applied', 'Unrecognized codes handled gracefully.'),
            ])); next_idx += 1

    if has_api and 'unauthorized' not in all_text and 'auth' not in all_text:
        neg_tcs.append(TestCase(
            sno=str(next_idx),
            summary='TC%02d_%s - Negative: Verify API with invalid/expired authentication' % (next_idx, feature_id),
            description='Verify API rejects requests with invalid, expired, or missing authentication token.',
            preconditions='1.\tAPI endpoint accessible\n2.\tPrepare request with invalid/expired token',
            story_linkage=feature_id, label=feature_id, category='Negative',
            steps=[
                TestStep(1, 'Prepare API request with invalid/expired auth token', 'Request prepared'),
                TestStep(2, 'Send request to API endpoint', 'API rejects request'),
                TestStep(3, 'Verify HTTP 401/403 returned with appropriate error message', 'API returns 401/403. No data modified.'),
            ])); next_idx += 1

    if has_e2e and 'upstream' not in all_text and 'unavailable' not in all_text:
        neg_tcs.append(TestCase(
            sno=str(next_idx),
            summary='TC%02d_%s - Negative: Verify behavior when upstream system is unavailable' % (next_idx, feature_id),
            description='Verify system handles upstream dependency failure gracefully.',
            preconditions='1.\tSystem in ready state\n2.\tSimulate upstream unavailability',
            story_linkage=feature_id, label=feature_id, category='Negative',
            steps=[
                TestStep(1, 'Simulate upstream system unavailability', 'Upstream down'),
                TestStep(2, 'Trigger the workflow', 'Workflow initiated'),
                TestStep(3, 'Verify retry mechanism and error logging', 'System handles failure gracefully. No data loss.'),
            ])); next_idx += 1

    return neg_tcs


# ================================================================
# AC TRACEABILITY
# ================================================================

def _build_ac_traceability(suite):
    mapping = {}
    for ac in suite.acceptance_criteria:
        ac_kw = [w.lower() for w in re.findall(r'\b\w{4,}\b', ac)]
        covering = []
        for tc in suite.test_cases:
            tc_text = (tc.summary + ' ' + tc.description + ' ' +
                       ' '.join(s.summary + ' ' + s.expected for s in tc.steps)).lower()
            match = sum(1 for kw in ac_kw if kw in tc_text)
            if match >= max(1, len(ac_kw) * 0.2):
                covering.append('TC%s' % tc.sno.zfill(2))
        mapping[ac[:100]] = covering if covering else ['NO COVERAGE']
    return mapping


# ================================================================
# DEVICE MATRIX EXPANSION
# ================================================================

# Categories that should NOT be expanded by device matrix
_NO_EXPAND_CATEGORIES = ['Negative', 'Edge Case', 'Edge Cases', 'Rollback', 'Timeout', 'Audit', 'E2E', 'End-to-End']


def _expand_by_matrix(suite, options, log=print, max_combos=4):
    """Expand core/positive TCs by device matrix. Returns new TC list or None.
    Smart detection: only expands if feature is device/SIM-dependent."""
    channels = options.get('channel', ['ITMBO'])
    if isinstance(channels, str): channels = [channels]
    devices = options.get('devices', ['Mobile'])
    networks = options.get('networks', ['4G', '5G'])
    sim_types = options.get('sim_types', ['eSIM', 'pSIM'])
    os_platforms = options.get('os_platforms', ['iOS', 'Android'])

    # Build combinations — SMART: pick representative combos, not full cartesian
    all_combos = []
    combo_id = 1
    for ch in channels:
        for sim in sim_types:
            for dev in devices:
                for os_p in os_platforms:
                    all_combos.append({
                        'id': 'CMB-%03d' % combo_id,
                        'channel': ch, 'sim': sim, 'device': dev,
                        'os': os_p,
                        'network': '/'.join(networks),
                        'key': '%s|%s|%s|%s' % (ch, sim, dev, os_p),
                    })
                    combo_id += 1

    # Smart reduction: if too many combos, pick representative subset
    # Rule: max 6 combos. Ensure each channel, device, SIM appears at least once.
    MAX_COMBOS = max_combos if max_combos != 4 else 5  # 5 for smart mode (covers all 5 dimensions)
    if len(all_combos) > MAX_COMBOS:
        combos = _pick_representative_combos(all_combos, channels, devices, sim_types, networks, MAX_COMBOS)
        log('[ENGINE]   Reduced %d combos to %d representative' % (len(all_combos), len(combos)))
    else:
        combos = all_combos

    if len(combos) <= 1:
        suite.combinations = combos
        return None

    suite.combinations = combos

    # ── Smart detection: should this feature be expanded? ──
    all_text = ' '.join([tc.summary + ' ' + tc.description + ' ' +
                         ' '.join(s.summary + ' ' + s.expected for s in tc.steps)
                         for tc in suite.test_cases]).lower()

    # Keywords that indicate device/SIM-dependent feature (SHOULD expand)
    DEVICE_KEYWORDS = ['esim', 'psim', 'sim type', 'sim card', 'iccid', 'imei',
                       'mobile device', 'tablet device', 'smartwatch', 'wearable',
                       'activate subscriber', 'swap mdn', 'change sim', 'change imei',
                       'port-in', 'device type', 'product type']

    # Keywords that indicate device-independent feature (should NOT expand)
    NO_EXPAND_KEYWORDS = ['report', 'batch', 'differential', 'file format',
                          'csv', 'column', 'notification', 'suppress',
                          'kafka', 'dpfo', 'usage', 'throttle', 'speed reduction',
                          'billing', 'mediation', 'cdr', 'prr']

    device_score = sum(1 for kw in DEVICE_KEYWORDS if kw in all_text)
    no_expand_score = sum(1 for kw in NO_EXPAND_KEYWORDS if kw in all_text)

    log('[ENGINE]   Device-dependent score: %d | Device-independent score: %d' % (device_score, no_expand_score))

    if no_expand_score > device_score:
        log('[ENGINE]   Feature is device-independent (report/batch/notification) -- skipping matrix expansion')
        return None

    if device_score < 3:
        log('[ENGINE]   Low device relevance (%d) -- skipping matrix expansion' % device_score)
        return None

    # Split TCs into expandable and non-expandable
    expandable = []
    non_expandable = []
    for tc in suite.test_cases:
        if tc.category in _NO_EXPAND_CATEGORIES:
            non_expandable.append(tc)
        else:
            expandable.append(tc)

    if not expandable:
        return None

    log('[ENGINE]   %d expandable TCs x %d combos = %d expanded' % (
        len(expandable), len(combos), len(expandable) * len(combos)))
    log('[ENGINE]   %d non-expandable TCs (negative/edge/rollback)' % len(non_expandable))

    # Expand
    expanded = []
    for tc in expandable:
        for combo in combos:
            new_tc = TestCase(
                sno='',  # renumbered later
                summary='%s_%s_%s_%s(%s)' % (
                    tc.summary, combo['device'], combo['os'], combo['sim'], combo['network']),
                description='%s\nChannel: %s | Device: %s | OS: %s | SIM: %s | Network: %s' % (
                    tc.description, combo['channel'], combo['device'],
                    combo['os'], combo['sim'], combo['network']),
                preconditions='%s\n%d.\tDevice: %s - %s - %s\n%d.\tNetwork: %s' % (
                    tc.preconditions,
                    tc.preconditions.count('\n') + 2, combo['device'], combo['os'], combo['sim'],
                    tc.preconditions.count('\n') + 3, combo['network']),
                steps=list(tc.steps),  # same steps
                story_linkage=tc.story_linkage,
                label=tc.label,
                category=tc.category,
            )
            expanded.append(new_tc)

    # Add non-expandable TCs at the end (negative scenarios)
    expanded.extend(non_expandable)
    return expanded


def _pick_representative_combos(all_combos, channels, devices, sim_types, networks, max_count):
    """Pick lean representative subset ensuring each dimension covered."""
    picked = []
    used_keys = set()
    primary_ch = channels[0]
    os_list = list(set(c.get('os', 'iOS') for c in all_combos))

    # Phase 1: Primary channel - one combo per device, alternate SIM + OS
    for di, dev in enumerate(devices):
        sim = sim_types[di % len(sim_types)]
        os_p = os_list[di % len(os_list)] if os_list else 'iOS'
        for c in all_combos:
            if c['device'] == dev and c['sim'] == sim and c.get('os') == os_p and c['channel'] == primary_ch:
                if c['key'] not in used_keys:
                    picked.append(c); used_keys.add(c['key'])
                break

    # Phase 2: Ensure each SIM type covered
    for sim in sim_types:
        if not any(p['sim'] == sim for p in picked):
            for c in all_combos:
                if c['sim'] == sim and c['channel'] == primary_ch and c['key'] not in used_keys:
                    picked.append(c); used_keys.add(c['key'])
                    break

    # Phase 3: Ensure each OS covered
    for os_p in os_list:
        if not any(p.get('os') == os_p for p in picked):
            for c in all_combos:
                if c.get('os') == os_p and c['channel'] == primary_ch and c['key'] not in used_keys:
                    picked.append(c); used_keys.add(c['key'])
                    break

    # Phase 4: Secondary channel - just 1 combo
    for ch in channels[1:]:
        if len(picked) >= max_count: break
        for c in all_combos:
            if c['channel'] == ch and c['key'] not in used_keys:
                picked.append(c); used_keys.add(c['key'])
                break

    # Cap at max_count
    picked = picked[:max_count]

    # Assign network: most get 5G, last one gets 4G (if both selected)
    has_4g = '4G' in [n for n in networks if '5G' not in n] if networks else False
    has_5g = any('5G' in n for n in networks) if networks else False
    for i, c in enumerate(picked):
        if has_5g and has_4g:
            c['network'] = '4G' if i == len(picked) - 1 else '5G'
        elif has_5g:
            c['network'] = '5G'
        elif has_4g:
            c['network'] = '4G'
        else:
            c['network'] = '/'.join(networks)

    # Renumber
    for i, c in enumerate(picked, 1):
        c['id'] = 'CMB-%03d' % i

    return picked


# ================================================================
# AUTO-GROUPING: Feature-aware sheet naming
# ================================================================

# Generic rules (fallback for any feature)
_GENERIC_GROUP_RULES = [
    ('Negative',        ['fail', 'reject', 'invalid', 'error', 'expires', 'not in active', 'not exist', 'unavailable']),
    ('Rollback',        ['rollback', 'restore']),
    ('Timeout',         ['timeout']),
    ('E2E',             ['end-to-end', 'e2e']),
    ('Integration',     ['mbo', 'syniverse', 'connection manager', 'kafka', 'apollo', 'upstream']),
    ('UI',              ['ui', 'menu', 'display']),
    ('Audit',           ['transaction history', 'audit trail']),
    ('Edge Cases',      ['handles', 'retry', 'multiple', 'ported', 'different', 'wearable', 'identical', 'add-on']),
    ('API',             ['api', 'change sim', 'change imei']),
]

# Feature-specific rules (checked FIRST before generic)
_FEATURE_GROUP_RULES = {
    'swap': [
        ('eSIM-to-eSIM',    ['esim to esim', 'transaction type: em', 'type: em', '(em)']),
        ('pSIM-to-pSIM',    ['psim to psim', 'transaction type: sm', 'type: sm', '(sm)']),
        ('pSIM-eSIM',       ['psim to esim', 'esim to psim', 'transaction type: am', 'type: am', '(am)']),
    ],
    'activation': [
        ('Phone-eSIM',      ['phone', 'mobile'] ),
        ('Tablet',          ['tablet']),
        ('Wearable',        ['wearable', 'smartwatch', 'watch']),
    ],
    'change feature': [
        ('Feature-Add',     ['add feature', 'add optional', 'include', 'add nc_']),
        ('Feature-Remove',  ['remove feature', 'remove optional', 'remove nc_']),
        ('Feature-Reset',   ['reset feature', 'reset']),
    ],
    'change bcd': [
        ('BCD-Update',      ['update', 'change bcd', 'dpfo', 'reset day']),
        ('BCD-Validation',  ['verify', 'validate', 'check']),
    ],
    'notification': [
        ('Suppress',        ['suppress', 'block', 'filter']),
        ('Forward',         ['forward', 'send', 'trigger', 'notify']),
    ],
}

_MIN_GROUP_SIZE = 2
_MAX_SINGLE_SHEET = 15


def _auto_group_tcs(test_cases):
    """Auto-detect groups from TC titles. Feature-aware sheet naming."""
    if len(test_cases) <= _MAX_SINGLE_SHEET:
        return {'All': list(test_cases)}

    # Detect feature type from all TC titles
    all_text = ' '.join(tc.summary.lower() for tc in test_cases)
    feature_type = None
    for ft in _FEATURE_GROUP_RULES:
        if ft in all_text:
            feature_type = ft
            break

    # Build combined rules: feature-specific FIRST, then generic
    rules = []
    if feature_type and feature_type in _FEATURE_GROUP_RULES:
        rules.extend(_FEATURE_GROUP_RULES[feature_type])
    rules.extend(_GENERIC_GROUP_RULES)

    groups = {}
    for tc in test_cases:
        tl = tc.summary.lower() + ' ' + tc.description.lower()
        matched = False
        for group_name, keywords in rules:
            if any(kw in tl for kw in keywords):
                groups.setdefault(group_name, []).append(tc)
                matched = True
                break
        if not matched:
            groups.setdefault('General', []).append(tc)

    # Merge tiny groups into General
    final = {}
    for gname, gtcs in groups.items():
        if len(gtcs) < _MIN_GROUP_SIZE and gname != 'General':
            final.setdefault('General', []).extend(gtcs)
        else:
            final[gname] = gtcs

    # Sort: feature-specific first, then generic, General last
    order = []
    if feature_type and feature_type in _FEATURE_GROUP_RULES:
        order.extend([r[0] for r in _FEATURE_GROUP_RULES[feature_type]])
    order.extend(['Core', 'UI', 'API', 'Integration', 'E2E', 'Edge Cases',
                  'Negative', 'Rollback', 'Timeout', 'Audit', 'General'])

    sorted_groups = {}
    for g in order:
        if g in final:
            sorted_groups[g] = final[g]
    for g in final:
        if g not in sorted_groups:
            sorted_groups[g] = final[g]

    return sorted_groups
