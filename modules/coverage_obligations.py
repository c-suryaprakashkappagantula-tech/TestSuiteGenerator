"""Deterministic source-coverage obligations and fail-closed completeness checks."""
import re
from dataclasses import dataclass, asdict
from typing import List

from .data_models_v8 import (TestSuite, TestCase, TestStep, DataInventory,
                             DataSourceEntry, CombinationPlan, RoutingAudit)
from .traceability import create_traceability

POLICY_VERSION = '1.0'
DATA_ALIGNMENT_ENDPOINT = '/nsl/provisioning/mno/v1/synchronization/data-alignment'

# Stable API contract. Source extraction may add new values, but known values may not disappear
# merely because a Chalk table was flattened or an attachment parser missed a row.
DATA_ALIGNMENT_CODES = (
    'ANDROID_AS_IOS', 'IOS_AS_ANDROID', 'MAKE_MISSING', 'MAKE_INVALID',
    'OS_MISSING', 'OS_INVALID', 'LINE_STATUS_MISSING', 'LINE_STATUS_INVALID',
    'MDN_MISSING', 'MDN_INVALID', 'SOLO_MOBILE_ACCOUNT_ID_MISSING',
    'SOLO_MOBILE_ACCOUNT_ID_INVALID', 'EXTERNALACCOUNTNUMBER_MISSING',
    'EXTERNALACCOUNTNUMBER_MISMATCH', 'EXTERNALACCOUNTSTATUS_MISMATCH',
    'EXTERNALACCOUNTSTATUS_MISSING', 'ICCID_MISSING', 'ICCID_INVALID',
    'IMSI_MISSING', 'IMSI_INVALID', 'IOS_MAC_ADDRESS_MISSING',
    'IOS_MAC_ADDRESS_INVALID', 'CABLE_ACCOUNT_NUMBER_INVALID',
)
_SPECIALS = (
    ('INVALID_VIOLATION_CODE', 'Reject a missing or invalid violations value with ERR06'),
    ('INVALID_TRANSACTION_ID', 'Reject a missing or invalid transactionId with ERR06'),
    ('DEACTIVATED_LINE', 'Reject Data Alignment for a deactivated line without side effects'),
    ('MULTIPLE_VIOLATIONS', 'Correct multiple compatible violations in one request'),
)
_ENUM_RE = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b')
_ENUM_SUFFIXES = ('_MISSING', '_INVALID', '_MISMATCH')
_DA_FIELD_PREFIXES = {
    'MAKE', 'OS', 'LINE_STATUS', 'MDN', 'SOLO_MOBILE_ACCOUNT_ID',
    'EXTERNALACCOUNTNUMBER', 'EXTERNALACCOUNTSTATUS', 'ICCID', 'IMSI',
    'IOS_MAC_ADDRESS', 'CABLE_ACCOUNT_NUMBER', 'CABLE_ACCOUNT_STATUS',
    'DEVICE_TYPE',
}


def _is_data_alignment_code(token):
    """Accept atomic contract enums and reject concatenated table-cell artifacts."""
    if token in ('ANDROID_AS_IOS', 'IOS_AS_ANDROID'):
        return True
    suffix = next((value for value in _ENUM_SUFFIXES if token.endswith(value)), '')
    if not suffix:
        return False
    field_name = token[:-len(suffix)]
    return field_name in _DA_FIELD_PREFIXES


@dataclass(frozen=True)
class CoverageObligation:
    obligation_id: str
    text: str
    kind: str = 'explicit_enum'
    source_id: str = ''
    required: bool = True


class IncompleteSuiteError(RuntimeError):
    pass


def _source_text(jira=None, chalk=None, parsed_docs=None, deep_mine_result=None):
    parts = []
    for obj, names in ((jira, ('summary', 'description', 'acceptance_criteria')),
                       (chalk, ('scope', 'rules', 'raw_text'))):
        if obj:
            parts.extend(str(getattr(obj, n, '') or '') for n in names)
    if jira:
        for item in (getattr(jira, 'subtasks', None) or []):
            if isinstance(item, dict):
                parts.extend(str(item.get(n, '') or '') for n in
                             ('summary', 'description', 'acceptance_criteria'))
    if chalk:
        for scenario in (getattr(chalk, 'scenarios', None) or []):
            parts.extend((str(getattr(scenario, 'title', '') or ''),
                          str(getattr(scenario, 'validation', '') or '')))
        parts.append(str(getattr(chalk, 'tables', '') or ''))
    for doc in parsed_docs or []:
        for name in ('text', 'content', 'raw_text', 'title'):
            parts.append(str(getattr(doc, name, '') or ''))
    if deep_mine_result:
        parts.append(str(deep_mine_result))
    return '\n'.join(parts)


def is_data_alignment_feature(jira=None, chalk=None):
    """Identify the feature from its own Jira contract, never shared Chalk blobs.

    Cached Chalk pages can contain navigation, child-page, or cross-feature text. Using
    that material for routing caused unrelated features to enter the Data Alignment
    builder. Chalk remains a source of violation obligations only after Jira establishes
    the feature identity.
    """
    if not jira:
        return False
    text = '\n'.join(str(getattr(jira, name, '') or '') for name in
                     ('summary', 'description', 'acceptance_criteria')).lower()
    normalized = text.replace('-', ' ')
    if 'data alignment' not in normalized:
        return False
    return ('data alignment api' in normalized or
            DATA_ALIGNMENT_ENDPOINT.lower() in text or
            'violations' in text)


def derive_coverage_obligations(jira=None, chalk=None, parsed_docs=None,
                                deep_mine_result=None) -> List[CoverageObligation]:
    all_text = _source_text(jira, chalk, parsed_docs, deep_mine_result)
    if is_data_alignment_feature(jira, chalk):
        # Once Jira establishes the domain, all gathered feature sources may supply
        # additional contract values.
        tokens = set(_ENUM_RE.findall(all_text.upper()))
        explicit_codes = {t for t in tokens if _is_data_alignment_code(t)}
        codes = sorted(explicit_codes | set(DATA_ALIGNMENT_CODES),
                       key=lambda value: (DATA_ALIGNMENT_CODES.index(value)
                                          if value in DATA_ALIGNMENT_CODES else 999, value))
        result = [CoverageObligation('DA.%s' % code,
                                     'Dedicated Data Alignment correction for %s' % code,
                                     source_id=code) for code in codes]
        result.extend(CoverageObligation('DA.%s' % key, special_text,
                                         kind='special_negative', source_id=key)
                      for key, special_text in _SPECIALS)
        return result

    # Generic extraction is deliberately conservative: shared Chalk/deep-mine blobs
    # cannot impose obligations on an unrelated feature. Jira (including its subtasks)
    # and explicitly parsed feature attachments are owned by the current ticket.
    owned_text = _source_text(jira, None, parsed_docs, None)
    tokens = set(_ENUM_RE.findall(owned_text.upper()))
    generic_codes = sorted(t for t in tokens
                           if ('_AS_' in t or t.endswith(_ENUM_SUFFIXES)) and len(t) <= 64)
    return [CoverageObligation('ENUM.%s' % code, 'Dedicated coverage for %s' % code,
                               source_id=code) for code in generic_codes]


def is_broad_scope_cr(jira, obligations):
    """Separate ticket type from scope using explicit feature-owned Jira language."""
    text = _source_text(jira).lower()
    return any(p in text for p in ('all scenario', 'all use case', 'all violation',
                                   'full coverage', 'each violation', 'complete matrix'))


def _family(code):
    if code in ('ANDROID_AS_IOS', 'IOS_AS_ANDROID', 'MAKE_MISSING', 'MAKE_INVALID',
                'OS_MISSING', 'OS_INVALID', 'DEVICE_TYPE_MISSING', 'DEVICE_TYPE_INVALID'):
        field = ('DEVICE_TYPE' if code.startswith('DEVICE_TYPE') else
                 ('OS' if ('OS' in code or '_AS_' in code) else 'MAKE'))
        return ('DC' if '_AS_' in code else 'YD', 'NBOP_MIG_DEVICE', field,
                'Validate Device - MNO; Connection Manager device event')
    if code.startswith('LINE_STATUS'):
        return ('YL', 'NBOP_MIG_LINE', 'LINE_STATUS',
                'Line Inquiry; Connection Manager; Mediation; Syniverse')
    if code.startswith('MDN_'):
        return ('DC', 'NBOP_MIG_LINE', 'MDN', 'Line Inquiry; Connection Manager; Mediation')
    if code.startswith(('ICCID_', 'IMSI_')):
        return ('DC', 'NBOP_MIG_SIM', 'ICCID' if code.startswith('ICCID') else 'IMSI',
                'Line Inquiry; Connection Manager; Syniverse Change IMSI; Mediation')
    if code.startswith('IOS_MAC'):
        return ('CC', 'NBOP_MIG_DEVICE', 'IOS_MAC_ADDRESS',
                'Get Line Details; Connection Manager CC event')
    if code.startswith('CABLE_'):
        field = 'CABLE_ACCOUNT_STATUS' if 'STATUS' in code else 'CABLE_ACCOUNT_NUMBER'
        return ('CC', 'NBOP_MIG_LINE', field,
                'Get Line Details; Connection Manager CC event')
    if code.startswith('SOLO_'):
        return ('CC', 'NBOP_MIG_ACCOUNT', 'SOLO_MOBILE_ACCOUNT_ID',
                'Sync Key Info (YK); Connection Manager CC event; Mediation')
    field = ('EXTERNAL_ACCOUNT_STATUS' if 'STATUS' in code else 'EXTERNAL_ACCOUNT_NUMBER')
    return ('CC', 'NBOP_MIG_ACCOUNT', field, 'Get Line Details; Connection Manager CC event')


def _positive_case(feature_id, code, index, pi):
    txn, table, field, downstream = _family(code)
    trace = create_traceability('Chalk Scenario', 'DA.%s' % code,
                                'Data Alignment violation %s' % code, pi_label=pi)
    pre = ('1. Active TMO subscriber with stable MDN/line identifiers is available in SIT\n'
           '2. Capture the authoritative %s value and the current %s.%s value\n'
           '3. Deliberately create the %s discrepancy and retain before-state evidence\n'
           '4. Century transaction logs and required downstream evidence are accessible') % (
               field, table, field, code)
    steps = [
        TestStep(1, 'Prepare and confirm the %s discrepancy for the TMO subscriber' % code,
                 'The controlled before-state proves %s and records the authoritative value' % code,
                 'DA.%s.SETUP' % code),
        TestStep(2, 'POST %s with a unique transactionId, violations=["%s"], and transactionType=%s' % (
            DATA_ALIGNMENT_ENDPOINT, code, txn),
                 'The API accepts the request and returns the contract-defined successful response for %s' % code,
                 'DA.%s.REQUEST' % code),
        TestStep(3, 'Query Century by the root transactionId and verify the Data Alignment transaction',
                 'Exactly one current-run root transaction records %s, transactionType=%s, and successful completion' % (code, txn),
                 'DA.%s.AUDIT' % code),
        TestStep(4, 'Verify required correlated downstream operations: %s' % downstream,
                 'Every required child operation is successful, current-run, and correlated to the root transactionId',
                 'DA.%s.DOWNSTREAM' % code),
        TestStep(5, 'Query %s and verify %s after Data Alignment' % (table, field),
                 '%s.%s equals the captured authoritative value; the deliberately corrupt value is absent' % (table, field),
                 'DA.%s.TABLE' % code),
    ]
    return TestCase(str(index), 'TC%02d_%s_Verify_Data-Alignment_corrects_%s_violation' % (index, feature_id, code),
                    'Verify Data Alignment corrects %s and updates every required downstream system.' % code,
                    pre, steps, feature_id, feature_id, 'Happy Path', 'P1', trace,
                    {'violation_code': code, 'transaction_type': txn}, 100, False,
                    ['DA.%s' % code])


def _negative_case(feature_id, key, text, index, pi):
    trace = create_traceability('Jira AC', 'DA.%s' % key, text, pi_label=pi)
    endpoint = DATA_ALIGNMENT_ENDPOINT
    if key == 'INVALID_VIOLATION_CODE':
        action = 'POST %s with missing, empty, and unknown violations values' % endpoint
        expected = 'Each invalid request is rejected with ERR06 and violations-required details'
    elif key == 'INVALID_TRANSACTION_ID':
        action = 'POST %s with missing, empty, and invalid transactionId values' % endpoint
        expected = 'Each invalid request is rejected with ERR06 and transactionId-required details'
    elif key == 'DEACTIVATED_LINE':
        action = 'POST %s for a deactivated TMO line with a valid violation payload' % endpoint
        expected = 'Correction is rejected for the deactivated line'
    else:
        action = 'POST %s with two compatible violations sharing one transactionType' % endpoint
        expected = 'The response reports a successful result for each requested violation'
    no_side_effect = key != 'MULTIPLE_VIOLATIONS'
    steps = [
        TestStep(1, 'Prepare the controlled %s test condition and capture before-state evidence' % key,
                 'Input and before-state evidence are ready and uniquely identifiable', 'DA.%s.SETUP' % key),
        TestStep(2, action, expected, 'DA.%s.REQUEST' % key),
        TestStep(3, 'Verify the Century root transaction and response details for %s' % key,
                 'Audit status, error/success details, transactionId, and timestamps match the current request',
                 'DA.%s.AUDIT' % key),
        TestStep(4, 'Verify downstream calls and persisted values after the request',
                 ('No correction child transactions or data mutations occur' if no_side_effect else
                  'Every requested violation has its required correlated child operations and corrected value'),
                 'DA.%s.SIDE_EFFECTS' % key),
    ]
    return TestCase(str(index), 'TC%02d_%s_%s' % (index, feature_id, key), text,
                    '1. TMO SIT subscriber and API access are available\n2. Before-state evidence and a unique transactionId are captured',
                    steps, feature_id, feature_id,
                    'Negative' if no_side_effect else 'Edge Case', 'P1', trace,
                    {'special_condition': key}, 100, False, ['DA.%s' % key])


def _chalk_case(feature_id, scenario, index, pi):
    """Build a test case for a Chalk scenario on the obligation-first path.

    Rule #1: Chalk is ground truth. The Data-Alignment builder derives its cases from
    the violation matrix, so without this every Chalk scenario on such a page would be
    silently dropped. Steps come from the scenario's own hint/validation text so the TC
    stays grounded in what Chalk actually says.
    """
    title = (getattr(scenario, 'title', '') or '').strip()
    validation = (getattr(scenario, 'validation', '') or '').strip()
    trace = create_traceability('Chalk Scenario', feature_id, title[:200], pi_label=pi)

    raw_steps = list(getattr(scenario, 'steps', None) or [])
    steps = []
    for i, st in enumerate(raw_steps[:12], 1):
        if isinstance(st, dict):
            action = str(st.get('action') or st.get('summary') or st.get('step') or '').strip()
            expected = str(st.get('expected') or st.get('expected_result') or '').strip()
        else:
            action, expected = str(st or '').strip(), ''
        if not action:
            continue
        steps.append(TestStep(len(steps) + 1, action,
                              expected or 'The documented Chalk outcome is observed',
                              'CHALK.STEP%d' % i))
    if not steps:
        steps = [
            TestStep(1, 'Set up the preconditions described by the Chalk scenario and capture '
                        'before-state evidence',
                     'Environment and test data match the Chalk scenario', 'CHALK.SETUP'),
            TestStep(2, title or 'Execute the documented Chalk scenario',
                     validation or 'The documented Chalk outcome is observed', 'CHALK.EXEC'),
            TestStep(3, 'Verify the Century root transaction and persisted values for this scenario',
                     'Audit status, details, transactionId, and timestamps match the request',
                     'CHALK.AUDIT'),
        ]

    low = ('%s %s' % (title, validation)).lower()
    category = 'Negative' if any(k in low for k in (
        'error', 'invalid', 'reject', 'fail', 'not found', 'incorrect', 'missing')) \
        else ('Regression' if 'regression' in low else 'Happy Path')

    summary = title if title.lower().startswith(
        ('verify', 'validate', 'ensure', 'confirm', 'check')) else 'Verify %s' % title
    return TestCase(str(index), 'TC%02d_%s_%s' % (index, feature_id, summary), validation or title,
                    '1. TMO SIT subscriber and API access are available\n'
                    '2. Before-state evidence and a unique transactionId are captured',
                    steps, feature_id, feature_id, category, 'P2', trace,
                    {'chalk_scenario': title[:120]}, 100, True, [])


def build_data_alignment_suite(jira, chalk, obligations, options=None, log=print):
    feature_id = getattr(jira, 'key', '') or ''
    pi = getattr(jira, 'pi', '') or ''
    required_codes = [o.source_id for o in obligations if o.obligation_id.startswith('DA.')
                      and o.kind == 'explicit_enum']
    cases = [_positive_case(feature_id, code, i + 1, pi)
             for i, code in enumerate(required_codes)]
    for key, text in _SPECIALS:
        cases.append(_negative_case(feature_id, key, text, len(cases) + 1, pi))

    # ── Rule #1: append a case for every Chalk scenario not already covered ──
    # The obligation matrix above is synthesized from the violation enum; the Chalk page
    # also documents scenarios (error codes, notification simulation, regression) that
    # the matrix does not express. Those are ground truth and must ship.
    _existing = ' '.join([(c.summary or '') + ' ' + (c.description or '') for c in cases]).lower()
    _added = 0
    for sc in (getattr(chalk, 'scenarios', None) or []):
        _t = (getattr(sc, 'title', '') or '').strip()
        if not _t:
            continue
        _key = re.sub(r'[^a-z0-9 ]', ' ', _t.lower())
        _key = ' '.join(_key.split())
        _key = re.sub(r'^(?:verify|validate|ensure|confirm|check)\s+(?:that\s+)?', '', _key)
        if _key and _key in _existing:
            continue
        cases.append(_chalk_case(feature_id, sc, len(cases) + 1, pi))
        _added += 1
    if _added:
        log('[DATA-ALIGN] Added %d Chalk scenario TC(s) on top of %d obligation TC(s)'
            % (_added, len(cases) - _added))
    suite = TestSuite(
        feature_id=feature_id, feature_title=getattr(jira, 'summary', '') or '',
        feature_desc=getattr(jira, 'description', '') or '', test_cases=cases,
        data_inventory=DataInventory(sources=[DataSourceEntry(
            'Data Alignment obligation contract', 'chalk', len(obligations),
            [o.obligation_id for o in obligations], 'success')],
            total_testable_items=len(obligations)),
        combination_plan=CombinationPlan(total_planned_tcs=len(cases)),
        engine_version='9.1.0-OBLIGATION', acceptance_criteria=[],
        scope=getattr(chalk, 'scope', '') if chalk else '',
        rules=getattr(chalk, 'rules', '') if chalk else '', pi=pi,
        channel=(options or {}).get('channel', getattr(jira, 'channel', '') or ''),
        jira_status=getattr(jira, 'status', '') or '',
        jira_priority=getattr(jira, 'priority', '') or '',
        jira_assignee=getattr(jira, 'assignee', '') or '',
        jira_reporter=getattr(jira, 'reporter', '') or '',
        routing_audit=RoutingAudit('api', 1.0, ['Data Alignment'],
                                   ['explicit coverage obligations'],
                                   ['Jira_AC', 'Chalk_Child_Page', 'Attachments'],
                                   len(cases), 0, sum(c.category == 'Negative' for c in cases), len(cases)))
    attach_coverage_audit(suite, obligations, log)
    return suite


def attach_coverage_audit(suite, obligations, log=print):
    suite.coverage_obligations = [asdict(o) if isinstance(o, CoverageObligation) else o
                                  for o in obligations]
    covered = set()
    for tc in getattr(suite, 'test_cases', []) or []:
        covered.update(getattr(tc, 'obligation_ids', []) or [])
        text = ' '.join([getattr(tc, 'summary', '') or '', getattr(tc, 'description', '') or ''] +
                        [getattr(s, 'summary', '') or '' for s in getattr(tc, 'steps', []) or []]).upper()
        for obligation in obligations:
            if obligation.source_id and obligation.source_id.upper() in text:
                covered.add(obligation.obligation_id)
    required = {o.obligation_id for o in obligations if o.required}
    missing = sorted(required - covered)
    suite.coverage_audit = {'policy_version': POLICY_VERSION, 'required': len(required),
                            'covered': len(required - set(missing)), 'missing': missing,
                            'passed': not missing}
    suite.coverage_policy_version = POLICY_VERSION
    log('[COVERAGE-GATE] Explicit obligations: %d/%d covered%s' % (
        len(required - set(missing)), len(required),
        '' if not missing else ' | missing: %s' % ', '.join(missing)))
    return suite.coverage_audit


def assert_suite_complete(suite, context='output', log=print):
    obligations = getattr(suite, 'coverage_obligations', None)
    if not obligations:
        return
    # Reconstruct lightweight objects so edits between generation/export are re-audited.
    rebuilt = [CoverageObligation(**o) if isinstance(o, dict) else o for o in obligations]
    audit = attach_coverage_audit(suite, rebuilt, log=log)
    if not audit['passed']:
        raise IncompleteSuiteError('%s blocked: %d explicit coverage obligation(s) missing: %s' % (
            context, len(audit['missing']), ', '.join(audit['missing'])))
