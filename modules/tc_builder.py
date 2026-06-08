"""
tc_builder.py — Test Case Builder for V8.0 Data-First Engine.

Builds concrete TestCase objects from the CombinationPlan:
  - Dimension TCs: one per dimension value with data-specific content
  - Scenario TCs: from ExtractedScenario with steps derived from hints/api_spec
  - Negative TCs: from NegativeSpec with specific error codes and conditions
  - Channel-specific steps: ITMBO → API path, NBOP → UI navigation path

Every TC gets:
  - Summary derived from data source (never generic)
  - Steps derived from API contract / UI flow / business rule
  - Expected results with specific values from data
  - TraceabilityRecord linking to source
  - Category (Happy Path / Negative / Edge Case)
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any, Tuple

from .traceability import TraceabilityRecord, create_traceability
from .data_models_v8 import (
    Dimension, ExtractedScenario, NegativeSpec, CombinationPlan,
    TestStep, TestCase,
)


# ================================================================
# FEATURE CLASSIFICATION
# ================================================================

# API operation verbs — indicate API feature when no UI context marker present.
# These are action verbs that represent backend API operations.
API_OPERATION_VERBS = {
    'POST', 'GET', 'PUT', 'DELETE', 'PATCH',
    'Change', 'Update', 'Retrieve', 'Activate', 'Deactivate',
    'Swap', 'Add', 'Remove', 'Create', 'Modify', 'Cancel',
    'Suspend', 'Resume', 'Transfer', 'Move', 'Merge', 'Query',
    'Reset', 'Download',
}

# UI context markers — phrases that indicate the operation is performed through
# the NBOP portal UI. These OVERRIDE API verbs when present.
# e.g., "Network Reset using NBOP UI" = UI (even though "Reset" is an API verb)
UI_CONTEXT_MARKERS = [
    'via nbop ui', 'through nbop', 'using nbop ui', 'in nbop portal',
    'is displayed', 'is not displayed', 'should be displayed',
    'not displayed', 'hide the page', 'show the page',
    'displayed under', 'option is displayed', 'should not be displayed',
    'are displayed', 'details are displayed', 'fields are displayed',
    'nbop ui', 'nbop_ui',
    # NBOP as subject performing the action (UI integration, not API testing)
    'nbop to integrate', 'nbop to hide', 'nbop to show', 'nbop to display',
    'nbop to remove', 'nbop to update', 'nbop to enable', 'nbop to disable',
    # Attribute removal/display patterns (UI verification)
    'are removed from', 'removed from the', 'attributes are removed',
]

# UI behavior keywords — indicate UI feature when no API verb present.
UI_BEHAVIOR_KEYWORDS = {
    'hide', 'show', 'display', 'visible', 'invisible',
    'present', 'absent', 'page', 'navigate', 'screen',
    'layout', 'element', 'button disabled', 'field hidden',
}

# NEUTRAL keywords — used in both API and UI features, do NOT independently
# determine classification. "Validate" appears in both API and UI TCs.
# "tab" appears in both (CMUNL tab = API context, Navigation tab = UI context).
NEUTRAL_KEYWORDS = {'Validate', 'tab', 'verify'}

# Legacy keyword sets (kept for fallback when no component prefix found)
API_KEYWORDS = {'GET', 'POST', 'PUT', 'DELETE', 'endpoint', 'API', 'service',
                'request', 'response', 'payload', 'REST', 'HTTP'}
UI_KEYWORDS = {'portal', 'screen', 'navigation', 'menu', 'button',
               'field', 'form', 'page', 'UI', 'click'}


@dataclass
class FeatureClassification:
    """Result of classifying a feature as API, UI, or hybrid."""
    classification: str = 'api'       # "api" | "ui" | "hybrid"
    api_keywords_found: List[str] = field(default_factory=list)
    ui_keywords_found: List[str] = field(default_factory=list)
    confidence: float = 0.0           # 0.0–1.0 based on keyword density


def classify_feature(jira_summary: str, ac_text: str) -> FeatureClassification:
    """Classify feature as API-based, UI-based, or hybrid.

    CRITICAL: [NBOP] is a CHANNEL used for both API and UI features.
    [NBOP]: ChangeMDN POST = API.  [NBOP]: Hide Port-in Status page = UI.

    Classification priority:
      1. [NSLNM/NENM] without [NBOP] → always API (0.95)
      2. [NBOP] + [NSLNM/NENM] → HYBRID (0.9)
      3. [NBOP] only → examine feature name for intent:
         a. UI context markers present → UI (overrides API verbs)
         b. API operation verbs without UI markers → API
         c. UI behavior keywords without API verbs → UI
         d. Both API verbs + UI keywords (no marker) → HYBRID
      4. No prefix → keyword fallback on full text

    Returns FeatureClassification with matched keywords and confidence.
    """
    combined_text = '%s %s' % (jira_summary or '', ac_text or '')
    summary_upper = (jira_summary or '').upper()

    # ── Extract component prefix and feature name ──
    _has_prefix = ']:' in summary_upper
    _prefix_part = summary_upper.split(']:')[0] if _has_prefix else ''
    _feature_name = (jira_summary or '').split(']:')[1].strip() if _has_prefix and ']:' in (jira_summary or '') else (jira_summary or '')

    _has_nbop_component = 'NBOP' in _prefix_part if _has_prefix else False
    _has_nslnm_component = any(kw in _prefix_part for kw in ['NSLNM', 'NENM']) if _has_prefix else False

    # ── RULE 1: [NSLNM/NENM] without [NBOP] → always API ──
    if _has_nslnm_component and not _has_nbop_component:
        return FeatureClassification(
            classification='api',
            api_keywords_found=['NSLNM'],
            ui_keywords_found=[],
            confidence=0.95,
        )

    # ── RULE 2: [NBOP] + [NSLNM/NENM] → HYBRID ──
    if _has_nslnm_component and _has_nbop_component:
        return FeatureClassification(
            classification='hybrid',
            api_keywords_found=['NSLNM'],
            ui_keywords_found=['NBOP'],
            confidence=0.9,
        )

    # ── RULE 3: [NBOP] only → examine feature name for intent ──
    if _has_nbop_component and not _has_nslnm_component:
        return _classify_nbop_feature(_feature_name, ac_text or '')

    # ── RULE 4: No recognized prefix → keyword fallback ──
    return _classify_by_keywords(combined_text)


def _classify_nbop_feature(feature_name: str, ac_text: str) -> FeatureClassification:
    """Classify an [NBOP]-prefixed feature by examining the feature name intent.

    NBOP is a channel used for both API and UI features. The real signal is:
    - UI context markers (override API verbs): "via NBOP UI", "is displayed", etc.
    - API operation verbs (without UI markers): POST, GET, Change, Swap, etc.
    - UI behavior keywords (without API verbs): hide, show, navigate, etc.
    """
    text_to_check = '%s %s' % (feature_name, ac_text)
    text_lower = text_to_check.lower()

    api_found = []
    ui_found = []

    # ── Step 3a: Check for UI context markers (highest priority) ──
    # These override API verbs — "Network Reset using NBOP UI" = UI
    ui_markers_found = []
    for marker in UI_CONTEXT_MARKERS:
        if marker in text_lower:
            ui_markers_found.append(marker)

    # ── Step 3b: Check for API operation verbs ──
    for verb in API_OPERATION_VERBS:
        if verb in NEUTRAL_KEYWORDS:
            continue
        pattern = r'\b%s\b' % re.escape(verb)
        if re.search(pattern, text_to_check, re.IGNORECASE):
            api_found.append(verb)

    # ── Step 3c: Check for UI behavior keywords ──
    for kw in UI_BEHAVIOR_KEYWORDS:
        pattern = r'\b%s\b' % re.escape(kw)
        if re.search(pattern, text_to_check, re.IGNORECASE):
            ui_found.append(kw)

    # ── Decision logic ──
    has_ui_marker = len(ui_markers_found) > 0
    has_api_verb = len(api_found) > 0
    has_ui_keyword = len(ui_found) > 0

    # UI context markers override API verbs
    if has_ui_marker:
        # "Network Reset using NBOP UI" → UI even though "Reset" is API verb
        return FeatureClassification(
            classification='ui',
            api_keywords_found=api_found,
            ui_keywords_found=ui_markers_found + ui_found,
            confidence=0.9,
        )

    # API verb without UI markers → API
    if has_api_verb and not has_ui_keyword:
        return FeatureClassification(
            classification='api',
            api_keywords_found=api_found,
            ui_keywords_found=[],
            confidence=0.9,
        )

    # UI keywords without API verbs → UI
    if has_ui_keyword and not has_api_verb:
        return FeatureClassification(
            classification='ui',
            api_keywords_found=[],
            ui_keywords_found=ui_found,
            confidence=0.9,
        )

    # Both API verbs + UI keywords (no explicit marker) → HYBRID
    if has_api_verb and has_ui_keyword:
        return FeatureClassification(
            classification='hybrid',
            api_keywords_found=api_found,
            ui_keywords_found=ui_found,
            confidence=0.8,
        )

    # No clear signals — default to API (NBOP channel, most features are API)
    return FeatureClassification(
        classification='api',
        api_keywords_found=[],
        ui_keywords_found=[],
        confidence=0.3,
    )


def _classify_by_keywords(combined_text: str) -> FeatureClassification:
    """Fallback classification using keyword matching when no component prefix found."""
    api_found = []
    ui_found = []

    for kw in API_KEYWORDS:
        pattern = r'\b%s\b' % re.escape(kw)
        if re.search(pattern, combined_text, re.IGNORECASE):
            api_found.append(kw)

    for kw in UI_KEYWORDS:
        pattern = r'\b%s\b' % re.escape(kw)
        if re.search(pattern, combined_text, re.IGNORECASE):
            ui_found.append(kw)

    # Also check API operation verbs for stronger signal
    for verb in API_OPERATION_VERBS:
        if verb in NEUTRAL_KEYWORDS or verb in API_KEYWORDS:
            continue
        pattern = r'\b%s\b' % re.escape(verb)
        if re.search(pattern, combined_text, re.IGNORECASE):
            api_found.append(verb)

    has_api = len(api_found) > 0
    has_ui = len(ui_found) > 0

    if has_api and has_ui:
        classification = 'hybrid'
        confidence = min(1.0, (len(api_found) + len(ui_found)) / 6.0)
    elif has_ui and not has_api:
        classification = 'ui'
        confidence = min(1.0, len(ui_found) / 4.0)
    elif has_api:
        classification = 'api'
        confidence = min(1.0, len(api_found) / 4.0)
    else:
        classification = 'api'
        confidence = 0.1

    return FeatureClassification(
        classification=classification,
        api_keywords_found=api_found,
        ui_keywords_found=ui_found,
        confidence=confidence,
    )


# ================================================================
# MAIN ENTRY POINT
# ================================================================


def build_test_cases(
    plan: CombinationPlan,
    jira,
    chalk,
    deep_mine_result,
    nbop_knowledge: Optional[Dict] = None,
    nmno_result=None,
    log: Callable = print,
) -> List[TestCase]:
    """Build concrete TestCase objects from the combination plan.

    Each TC gets:
      - Summary derived from data source (never generic)
      - Steps derived from API contract / UI flow / business rule
      - Expected results with specific values from data
      - TraceabilityRecord linking to source
      - Category (Happy Path / Negative / Edge Case)

    Routing based on classify_feature():
      - "api": Dimension TCs + Negative TCs with POST/GET steps
      - "ui": Chalk scenarios → TCs with NBOP nav + element verification
      - "hybrid": Both paths

    Returns list of TestCase objects ready for validation and output.
    """
    test_cases: List[TestCase] = []
    feature_id = jira.key if jira else ''
    feature_name = _extract_feature_name(jira) if jira else 'Unknown Feature'

    log('[TC-BUILD] Building test cases from combination plan...')
    log('[TC-BUILD]   Planned: %d TCs' % plan.total_planned_tcs)

    # ── Classify feature using the authoritative classifier ──
    jira_summary = jira.summary if jira else ''
    ac_text = getattr(jira, 'acceptance_criteria', '') or ''
    classification = classify_feature(jira_summary, ac_text)
    log('[TC-BUILD]   Classification: %s (confidence=%.2f)' % (
        classification.classification, classification.confidence))
    if classification.confidence < 0.5:
        log('[TC-BUILD]   WARNING: Low-confidence routing — review may be needed')

    # Also get legacy intent for backward compat with dual-path logic
    feature_intent = _classify_feature_intent(jira, deep_mine_result, log)

    # ── Determine API spec context for step generation ──
    api_context = _build_api_context(jira, deep_mine_result, feature_name)

    # ── Enrich api_context with NMNO data (highest quality source) ──
    if nmno_result and nmno_result.api_specs:
        nmno_spec = nmno_result.api_specs[0]
        if nmno_spec.endpoint:
            api_context['endpoint'] = nmno_spec.endpoint
        if nmno_spec.http_method:
            api_context['method'] = nmno_spec.http_method
        if nmno_spec.api_name:
            api_context['api_name'] = nmno_spec.api_name
        if hasattr(nmno_spec, 'request_fields') and nmno_spec.request_fields:
            api_context['request_fields'] = nmno_spec.request_fields
        if hasattr(nmno_spec, 'response_fields') and nmno_spec.response_fields:
            api_context['response_fields'] = nmno_spec.response_fields
        if hasattr(nmno_spec, 'source_system') and nmno_spec.source_system:
            api_context['source_system'] = nmno_spec.source_system
        if hasattr(nmno_spec, 'target_system') and nmno_spec.target_system:
            api_context['target_system'] = nmno_spec.target_system
        # Store raw request_sample for test data injection
        if hasattr(nmno_spec, 'request_sample') and nmno_spec.request_sample:
            api_context['_nmno_request_sample'] = nmno_spec.request_sample
        api_context['_nmno_enriched'] = True

    # ── Determine routing path ──
    is_api_path = classification.classification in ('api', 'hybrid')
    is_ui_path = classification.classification in ('ui', 'hybrid')

    # ── Get NBOP navigation path for UI features ──
    nav_path = ''
    if is_ui_path:
        nav_path = _get_nbop_nav_path(feature_name)

    # ── 1. Build TCs from independent dimensions (API path only) ──
    # Structural/metadata dimensions are blocked — they are navigation context, not testable axes
    _STRUCTURAL_DIMS_API = {
        'precondition', 'nav_path', 'navigation', 'action_point',
        'page_name', 'context', 'ordering_channel', 'portal_screen',
    }

    def _is_nav_val(val: str) -> bool:
        s = str(val)
        return '→' in s or '->' in s or s.lower().startswith('navigate to')

    if is_api_path:
        for dim in plan.independent_dimensions:
            if dim.name.lower() in _STRUCTURAL_DIMS_API:
                continue
            if dim.values and all(_is_nav_val(v) for v in dim.values):
                continue
            for value in dim.values:
                if _is_nav_val(value):
                    continue
                tc = _build_dimension_tc(dim, value, jira, chalk, feature_name, deep_mine_result, api_context, feature_intent)
                test_cases.append(tc)
        log('[TC-BUILD]   Built %d dimension TCs (API path)' % sum(
            len(d.values) for d in plan.independent_dimensions))
    else:
        log('[TC-BUILD]   Skipping dimension TCs (UI-only feature)')

    # ── 2. Build TCs from crossed dimensions (API path only) ──
    if is_api_path:
        for dim1, dim2 in plan.crossed_dimensions:
            for val1 in dim1.values:
                for val2 in dim2.values:
                    tc = _build_crossed_dimension_tc(dim1, val1, dim2, val2, jira, chalk, feature_name)
                    test_cases.append(tc)
        if plan.crossed_dimensions:
            log('[TC-BUILD]   Built %d crossed dimension TCs' % sum(
                len(d1.values) * len(d2.values) for d1, d2 in plan.crossed_dimensions))

    # ── 3. Build TCs from scenarios ──
    if is_ui_path and classification.classification == 'ui':
        # UI path: scenario → TC mapping with enriched NBOP steps
        # Product crossing: only for TMO-specific verification scenarios (not regression, not evidence)
        product_dim = next((d for d in plan.independent_dimensions if d.name == 'product'), None)
        product_values = product_dim.values if product_dim and len(product_dim.values) > 1 else []

        subtask_context = _build_subtask_context(deep_mine_result)
        tc_idx = 0

        for scenario in plan.scenario_tcs:
            scenario_dict = {
                'title': scenario.title,
                'validation': scenario.validation,
                'category': scenario.category,
                'steps_hint': scenario.steps_hint,
            }

            # Determine if this scenario should be crossed by product
            # Cross by product ONLY for TMO removal/verification scenarios (not regression, not evidence)
            title_lower = (scenario.title or '').lower()
            is_regression = scenario.category == 'Regression' or 'no change' in title_lower or 'verizon' in title_lower or 'vzw' in title_lower
            is_evidence = title_lower.startswith('evidence:') or 'log in to nbop' in title_lower
            is_positive_verify = 'attributes displayed' in title_lower or 'is displayed' in title_lower or 'should be displayed' in title_lower
            should_cross = product_values and not is_regression and not is_evidence and not is_positive_verify

            if should_cross:
                for product in product_values:
                    crossed_dict = dict(scenario_dict)
                    crossed_dict['title'] = '%s — %s' % (product, scenario.title)
                    crossed_dict['_product'] = product
                    tc = _build_ui_scenario_tc_enriched(
                        crossed_dict, tc_idx, feature_name, feature_id, nav_path,
                        subtask_ac_text=_get_subtask_ac_for_scenario(scenario, subtask_context),
                        subtask_key=_get_subtask_key_for_scenario(scenario),
                        log=log,
                    )
                    test_cases.append(tc)
                    tc_idx += 1
            else:
                tc = _build_ui_scenario_tc_enriched(
                    scenario_dict, tc_idx, feature_name, feature_id, nav_path,
                    subtask_ac_text=_get_subtask_ac_for_scenario(scenario, subtask_context),
                    subtask_key=_get_subtask_key_for_scenario(scenario),
                    log=log,
                )
                test_cases.append(tc)
                tc_idx += 1

        log('[TC-BUILD]   Built %d UI scenario TCs (product crossing applied where appropriate)' % tc_idx)
    else:
        # API/hybrid path: standard scenario TC building
        for scenario in plan.scenario_tcs:
            tc = _build_scenario_tc(scenario, jira, feature_name, nbop_knowledge, api_context, feature_intent)
            test_cases.append(tc)
        log('[TC-BUILD]   Built %d scenario TCs' % len(plan.scenario_tcs))

    # ── 4. Build TCs from negative specs ──
    for neg_spec in plan.negative_tcs:
        # Try to find matching NMNOBusinessRule for enhanced step generation
        # (business_rule is passed via neg_spec metadata if available from NMNO lookup)
        business_rule = getattr(neg_spec, '_business_rule', None)
        tc = _build_negative_tc(neg_spec, jira, feature_name, api_context, business_rule=business_rule)
        test_cases.append(tc)

    log('[TC-BUILD]   Built %d negative TCs' % len(plan.negative_tcs))

    # ── 5. Dual-path generation (hybrid features) ──
    if classification.classification == 'hybrid' and feature_intent.get('channels'):
        dual_tcs = _generate_dual_path_tcs(plan, jira, feature_name, api_context, feature_intent, log)
        # Deduplication: remove UI TCs that overlap with API negative TCs
        if dual_tcs and plan.negative_tcs:
            _neg_conditions = set()
            for neg_spec in plan.negative_tcs:
                _cond = (neg_spec.error_code or '').lower()
                if _cond:
                    _neg_conditions.add(_cond)
            _before = len(dual_tcs)
            dual_tcs = [
                tc for tc in dual_tcs
                if not any(ec in tc.summary.lower() for ec in _neg_conditions)
            ]
            _deduped = _before - len(dual_tcs)
            if _deduped > 0:
                log('[TC-BUILD]   Dedup: removed %d UI TCs overlapping with API negative TCs' % _deduped)
        test_cases.extend(dual_tcs)
        if dual_tcs:
            log('[TC-BUILD]   Built %d dual-path TCs (API + UI coverage)' % len(dual_tcs))

    # ── 6. Assign serial numbers ──
    _assign_serial_numbers(test_cases)

    log('[TC-BUILD] Complete: %d test cases built (route=%s)' % (
        len(test_cases), classification.classification))
    return test_cases


# ================================================================
# DIMENSION TC BUILDERS
# ================================================================


def _build_dimension_tc(
    dimension: Dimension,
    value: str,
    jira,
    chalk,
    feature_name: str,
    deep_mine_result=None,
    api_context: Dict = None,
    feature_intent: Dict = None,
) -> TestCase:
    """Build a TC for a single dimension value with data-specific content."""
    feature_id = jira.key if jira else ''
    dim_name = dimension.name
    api_context = api_context or {}

    # Determine channel for step generation
    channel = value if dim_name == 'channel' else 'ITMBO'

    # Build steps based on dimension type with clear API context
    steps = _build_clear_api_steps(feature_name, dim_name, value, api_context)

    # Build summary — include "Negative:" prefix for negative categories
    summary = _build_dimension_summary(feature_name, dim_name, value, feature_id)

    # Build description — short, intent-focused (one sentence)
    if dim_name == 'line_state':
        description = 'To validate %s API rejects request when line is in %s state' % (feature_name, value)
    elif dim_name == 'input_type':
        description = 'To validate %s operation succeeds with %s as input identifier via ITMBO API' % (feature_name, value)
    elif dim_name == 'product':
        description = 'To validate %s operation for %s device type via ITMBO API' % (feature_name, value)
    elif dim_name == 'channel':
        description = 'To validate %s operation via %s channel' % (feature_name, value)
    else:
        description = 'To validate %s operation with %s=%s' % (feature_name, _humanize_dim_name(dim_name), value)

    # Determine category
    category = 'Happy Path'
    if dim_name == 'line_state':
        category = 'Negative'
    elif dim_name == 'error_code':
        category = 'Negative'

    # Build clear preconditions
    preconditions = _build_api_preconditions(dim_name, value, api_context)

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=category,
        traceability=dimension.source,
        dimension_values={dim_name: value, 'channel': channel},
    )


def _build_crossed_dimension_tc(
    dim1: Dimension, val1: str,
    dim2: Dimension, val2: str,
    jira, chalk, feature_name: str,
) -> TestCase:
    """Build a TC for a crossed dimension pair."""
    feature_id = jira.key if jira else ''

    # Determine channel
    channel = val1 if dim1.name == 'channel' else (val2 if dim2.name == 'channel' else _get_default_channel(jira))

    summary = '%s_%s_%s with %s=%s and %s=%s' % (
        feature_id, feature_name.replace(' ', '_'),
        _humanize_dim_name(dim1.name), _humanize_dim_name(dim1.name), val1,
        _humanize_dim_name(dim2.name), val2,
    )

    steps = [
        TestStep(step_num=1, summary='Prepare test data: %s=%s, %s=%s' % (
            _humanize_dim_name(dim1.name), val1, _humanize_dim_name(dim2.name), val2),
            expected='Test data configured for %s + %s combination' % (val1, val2),
            data_reference='%s=%s, %s=%s' % (dim1.name, val1, dim2.name, val2)),
        TestStep(step_num=2, summary='Execute %s operation via %s' % (feature_name, channel),
            expected='Operation processed successfully',
            data_reference='%s endpoint' % channel),
        TestStep(step_num=3, summary='Verify response contains correct data for %s=%s with %s=%s' % (
            _humanize_dim_name(dim1.name), val1, _humanize_dim_name(dim2.name), val2),
            expected='Response confirms %s behavior specific to %s + %s' % (feature_name, val1, val2),
            data_reference='Cross: %s × %s' % (dim1.name, dim2.name)),
    ]

    return TestCase(
        summary=summary,
        description='Verify %s with crossed dimensions: %s=%s × %s=%s' % (
            feature_name, dim1.name, val1, dim2.name, val2),
        preconditions=_build_preconditions(dim1.name, val1, channel),
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category='Happy Path',
        traceability=dim1.source,
        dimension_values={dim1.name: val1, dim2.name: val2},
    )


# ================================================================
# SCENARIO TC BUILDER
# ================================================================


def _build_scenario_tc(
    scenario: ExtractedScenario,
    jira,
    feature_name: str,
    nbop_knowledge: Optional[Dict] = None,
    api_context: Dict = None,
    feature_intent: Dict = None,
) -> TestCase:
    """Build a TC from an ExtractedScenario with steps from hints and api_spec."""
    feature_id = jira.key if jira else ''
    api_context = api_context or {}

    # Build steps from scenario hints or api_spec
    steps = []
    if scenario.steps_hint:
        for i, hint in enumerate(scenario.steps_hint, 1):
            # Derive a meaningful expected result from the step hint text —
            # NEVER use the generic "Step N completed as per <key> specification" placeholder.
            step_low = hint.lower()
            if any(kw in step_low for kw in ['invoke', 'call', 'trigger', 'send', 'submit']):
                _exp = 'API accepts the request and responds successfully'
            elif any(kw in step_low for kw in ['verify', 'validate', 'check', 'confirm']):
                _val = scenario.validation or ''
                _val_is_header = (
                    'scenario #' in _val.lower()
                    or _val.lower().startswith(('test scenario', 'validation', 'negative scenarios',
                                                'edge scenarios', 'positive scenarios', 'regression scenarios'))
                )
                _exp = _val[:120] if _val and not _val_is_header else 'Verification passes as expected'
            elif any(kw in step_low for kw in ['login', 'navigate', 'open', 'launch']):
                _exp = 'Portal/screen loads successfully and is ready for input'
            elif any(kw in step_low for kw in ['view', 'display', 'observe']):
                _exp = 'Data displayed correctly matches expected values'
            else:
                # Use the Chalk validation as the expected result, not a placeholder
                _val2 = scenario.validation or ''
                _val2_is_header = (
                    'scenario #' in _val2.lower()
                    or _val2.lower().startswith(('test scenario', 'validation', 'negative scenarios',
                                                 'edge scenarios', 'positive scenarios', 'regression scenarios'))
                )
                _exp = _val2[:120] if _val2 and not _val2_is_header else 'Step completed successfully'
            steps.append(TestStep(
                step_num=i,
                summary=hint,
                expected=_exp,
                data_reference=scenario.source.source_id,
            ))
        # Add final verification step with the actual validation from Chalk
        if scenario.validation and scenario.validation != scenario.title:
            # Guard: reject Chalk table header text that leaked into validation field
            _val_clean = (scenario.validation or '').strip()
            _table_headers = (
                'scenario #' in _val_clean.lower()
                or _val_clean.lower().startswith('test scenario')
                or _val_clean.lower().startswith('validation')
                or _val_clean.lower().startswith('negative scenarios')
                or _val_clean.lower().startswith('edge scenarios')
                or _val_clean.lower().startswith('positive scenarios')
                or _val_clean.lower().startswith('regression scenarios')
                or (len(_val_clean) < 20 and _val_clean.lower() in
                    ('validation', 'expected result', 'test scenario', 'scenario'))
            )
            if not _table_headers:
                steps.append(TestStep(
                    step_num=len(steps) + 1,
                    summary='Verify expected result: %s' % scenario.title[:80],
                    expected=_val_clean[:200],
                    data_reference=scenario.source.source_id,
                ))
    elif scenario.api_spec:
        # Build steps from API spec
        spec = scenario.api_spec
        steps = _build_api_spec_steps(spec, scenario.title, feature_name)
    elif api_context.get('_nmno_enriched'):
        # Build enriched API steps from NMNO context (Phase 2 enhancement)
        # Extract scenario-specific context for step alignment
        endpoint = api_context.get('endpoint', '')    # Empty if no real endpoint known
        method = api_context.get('method', 'POST')
        scenario_title = scenario.title or ''

        # Extract violation code from scenario title (e.g., "ANDROID_AS_IOS", "MAKE_MISSING")
        violation_code = ''
        violation_match = re.search(r'([A-Z][A-Z0-9_]{5,})', scenario_title)
        if violation_match:
            violation_code = violation_match.group(1)

        # Determine scenario action from title
        if 'corrects' in scenario_title.lower() and violation_code:
            # Violation correction scenario — specific steps
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Create %s out-of-sync condition in NSL DB for TMO subscriber' % violation_code,
                         expected='NSL DB has %s mismatch data ready for data-alignment correction' % violation_code,
                         data_reference='Violation: %s' % violation_code),
                TestStep(step_num=2,
                         summary='Send %s request to %s with violation=%s' % (method, endpoint, violation_code),
                         expected='API accepts request and triggers correction workflow for %s' % violation_code,
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate response status 200 OK and correction result in response body',
                         expected='Response confirms %s violation corrected — sync status=SUCCESS' % violation_code,
                         data_reference='Response: %s correction' % violation_code),
                TestStep(step_num=4,
                         summary='Verify downstream systems updated: NSL DB, CM, EMM reflect corrected %s data' % violation_code,
                         expected='%s mismatch resolved — NSL and external systems now in sync' % violation_code,
                         data_reference='Downstream: %s' % violation_code),
            ]
        elif 'rejects' in scenario_title.lower():
            # Rejection/negative scenario
            error_ref = violation_code or 'invalid request'
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Prepare invalid request data — %s' % scenario_title[:60],
                         expected='Invalid/error-triggering data prepared',
                         data_reference='Scenario: %s' % scenario_title[:40]),
                TestStep(step_num=2,
                         summary='Send %s request to %s with invalid data' % (method, endpoint),
                         expected='API rejects request with appropriate error code',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate error response contains expected error code and message',
                         expected='Error response returned — %s rejected with correct error details' % error_ref,
                         data_reference='Error validation'),
                TestStep(step_num=4,
                         summary='Verify no downstream changes occurred (NSL DB, CM unchanged)',
                         expected='No data modification — system state unchanged after rejection',
                         data_reference='Rollback verification'),
            ]
        elif 'kafka' in scenario_title.lower():
            # Kafka end-to-end scenario
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Publish data-alignment violation message to Kafka topic',
                         expected='Kafka message published successfully with violation payload',
                         data_reference='Kafka: data-alignment topic'),
                TestStep(step_num=2,
                         summary='Verify NSL consumes Kafka message and triggers %s %s' % (method, endpoint),
                         expected='NSL processes Kafka message — API call initiated automatically',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate data-alignment correction completed end-to-end',
                         expected='Violation corrected — response status 200, downstream systems updated',
                         data_reference='E2E: Kafka → NSL → correction'),
                TestStep(step_num=4,
                         summary='Verify transaction logged with correct Kafka correlation ID',
                         expected='Transaction history shows Kafka-triggered correction with matching correlation',
                         data_reference='Audit: Kafka E2E'),
            ]
        elif 'deactivated' in scenario_title.lower():
            # Deactivated line scenario
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Use TMO subscriber with line status = Deactivated',
                         expected='Deactivated TMO MDN available in SIT environment',
                         data_reference='Line state: Deactivated'),
                TestStep(step_num=2,
                         summary='Send %s request to %s for deactivated line with each violation type' % (method, endpoint),
                         expected='API processes request for deactivated line',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate all violation corrections FAIL for deactivated line',
                         expected='Each violation type returns error — corrections blocked for deactivated lines',
                         data_reference='Negative: deactivated line'),
                TestStep(step_num=4,
                         summary='Verify no downstream changes — NSL DB and CM remain unchanged',
                         expected='No data modification — deactivated line protection enforced',
                         data_reference='Safety: no changes on deactivated'),
            ]
        elif 'multiple violations' in scenario_title.lower():
            # Multiple violations scenario
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Create multiple out-of-sync conditions in NSL DB (2+ violations)',
                         expected='NSL DB has multiple mismatches ready for batch correction',
                         data_reference='Multi-violation setup'),
                TestStep(step_num=2,
                         summary='Send %s request to %s with multiple violation codes in single payload' % (method, endpoint),
                         expected='API accepts batch request with multiple violations',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate response shows correction result for each violation individually',
                         expected='Response contains per-violation status — all corrected successfully',
                         data_reference='Batch response validation'),
                TestStep(step_num=4,
                         summary='Verify all downstream systems updated for each corrected violation',
                         expected='All mismatches resolved — NSL, CM, EMM in sync for all violations',
                         data_reference='Multi-violation downstream'),
            ]
        else:
            # Generic API scenario with scenario-specific expected result
            steps = [
                TestStep(step_num=1,
                         summary='Preconditions: Set up test data — %s' % scenario_title[:60],
                         expected='Test environment configured for: %s' % scenario_title[:50],
                         data_reference='Scenario: %s' % scenario_title[:40]),
                TestStep(step_num=2,
                         summary='Send %s request to %s' % (method, endpoint),
                         expected='Request accepted and processed by NSL',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate response status 200 OK and response body',
                         expected='Success response confirms: %s' % scenario_title[:50],
                         data_reference='Response validation'),
                TestStep(step_num=4,
                         summary='Verify: %s' % scenario_title[:70],
                         expected=scenario.validation or 'Scenario condition verified successfully',
                         data_reference=scenario.source.source_id),
            ]
    else:
        # NO steps_hint, NO api_spec, NO NMNO context.
        # Use get_step_chain() with the scenario content — never dump title as "Execute: <title>".
        # This produces domain-specific steps (inquiry, notification, API, UI) based on the
        # scenario's actual subject matter.
        from .step_templates import get_step_chain as _get_chain
        _ctx = (feature_name + ' ' + (scenario.validation or '')).lower()
        _chain = _get_chain(scenario.title, scenario.validation, _ctx)
        steps = [TestStep(step_num=i, summary=s, expected=e, data_reference=scenario.source.source_id)
                 for i, (s, e) in enumerate(_chain, 1)]

    # Ensure at least one step
    if not steps:
        steps = [TestStep(step_num=1, summary=scenario.title,
                 expected=scenario.validation, data_reference=scenario.source.source_id)]

    # Transform raw AC text into a proper test scenario title
    clean_title = _transform_to_scenario_title(scenario.title, feature_name)
    # Phase 4: Truncate at word boundary (no mid-word cuts), replace spaces with underscores
    clean_title_safe = clean_title.replace(' ', '_')
    if len(clean_title_safe) > 80:
        # Find last underscore before position 80
        cut_pos = clean_title_safe.rfind('_', 0, 80)
        if cut_pos > 40:
            clean_title_safe = clean_title_safe[:cut_pos]
        else:
            clean_title_safe = clean_title_safe[:80]
    summary = '%s_%s' % (feature_id, clean_title_safe)

    # Short intent-focused description
    description = 'To validate: %s' % scenario.title[:120]

    # Environment-specific preconditions
    if scenario.source.source_type == 'Subtask AC':
        preconditions = '\n'.join([
            '1. Active TMO MDN available in SIT environment',
            '2. User logged into NBOP' if 'nbop' in (scenario.source.source_id or '').lower() or 'ui' in (scenario.source.source_id or '').lower() else '2. API endpoint accessible',
            '3. Line Status: Active',
        ])
    else:
        preconditions = '1.\tActive TMO subscriber line in SIT environment\n2.\tAPI endpoint accessible\n3.\tLine Status: Active'

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=scenario.category,
        traceability=scenario.source,
        dimension_values={},
    )


# ================================================================
# NEGATIVE TC BUILDER
# ================================================================


def _build_negative_tc(
    neg_spec: NegativeSpec,
    jira,
    feature_name: str,
    api_context: Dict = None,
    business_rule=None,
) -> TestCase:
    """Build a negative TC with specific error code, condition, and expected message.

    When business_rule (NMNOBusinessRule) is provided, generates a structured
    5-step POST/GET sequence:
      1. Preconditions: set up error condition
      2. Build request with invalid/missing data
      3. Send POST/GET to endpoint
      4. Validate error status code
      5. Validate error code + message match Business Rule

    When business_rule is None, falls back to the 3-step generic pattern.
    """
    feature_id = jira.key if jira else ''
    api_context = api_context or {}
    endpoint = api_context.get('endpoint', '')    # Empty if no real endpoint known — no slug fallback
    method = api_context.get('method', 'POST')
    _ep_display = _display_endpoint(endpoint, method, feature_name)

    # ── Use Business Rule data if available ──
    if business_rule is not None:
        error_code = business_rule.error_code or neg_spec.error_code
        rule_name = business_rule.rule_name or ''
        condition = business_rule.condition or neg_spec.triggering_condition
        expected_result = business_rule.expected_result or neg_spec.error_message
        error_details = business_rule.error_details or ''
        source_section = business_rule.source_section or ''

        summary = '%s_Negative - %s: %s' % (feature_id, error_code, rule_name or condition[:50])

        description = 'Validate %s API returns error %s when %s. Source: %s' % (
            feature_name, error_code, condition, source_section
        )

        preconditions = '\n'.join([
            '1. TMO MDN available in SIT environment',
            '2. Error condition: %s' % condition,
            '3. API endpoint accessible: %s %s' % (method, endpoint),
            '4. Business Rule: %s (%s)' % (error_code, rule_name),
        ])

        # 5-step POST/GET pattern for Business Rule negative TCs
        steps = [
            TestStep(
                step_num=1,
                summary='Preconditions: Set up error condition - %s' % condition,
                expected='System is in state to trigger error %s' % error_code,
                data_reference='Business Rule: %s - %s' % (error_code, rule_name),
            ),
            TestStep(
                step_num=2,
                summary='Build request payload with invalid/missing data to trigger %s' % error_code,
                expected='Request payload is constructed with error-triggering data',
                data_reference='Condition: %s' % condition,
            ),
            TestStep(
                step_num=3,
                summary='Send %s request to %s' % (method, _ep_display),
                expected='Request is sent to the API endpoint',
                data_reference='Endpoint: %s %s' % (method, _ep_display),
            ),
            TestStep(
                step_num=4,
                summary='Validate response status code is 4xx/5xx (error response)',
                expected='Error status code returned (400/404/500)',
                data_reference='Expected: error status for %s' % error_code,
            ),
            TestStep(
                step_num=5,
                summary='Validate error code is %s and message: %s' % (
                    error_code, error_details or expected_result),
                expected='Error response contains code=%s, details="%s"' % (
                    error_code, error_details or expected_result),
                data_reference='Source: %s' % source_section,
            ),
        ]
    else:
        # ── Fallback: 3-step pattern (no Business Rule object but has NegativeSpec) ──
        error_code = neg_spec.error_code
        condition = neg_spec.triggering_condition or ''
        error_msg = neg_spec.error_message or ''

        # Phase 3: If condition is too short/generic, use error_message as context
        if len(condition.strip()) < 10 or condition.strip().lower() in ('if not', 'if', 'when', 'not'):
            # Derive meaningful condition from error message
            if error_msg:
                condition = error_msg[:100]
            else:
                condition = 'Error condition triggers %s' % error_code

        # Guard: error_code must look like a real code (ERR123, 400, GENS-0001, etc.)
        # If it looks like a condition fragment ("If not", "When", "not"), replace with
        # a sanitized version derived from the condition text
        _ec_looks_generic = (
            not error_code
            or len(error_code.strip()) < 3
            or error_code.strip().lower() in ('if not', 'if', 'when', 'not', 'none', 'n/a')
            or (len(error_code) < 10 and not any(c.isdigit() for c in error_code))
        )
        if _ec_looks_generic:
            # Derive a short code from condition or error_msg
            _ec_source = condition or error_msg or 'unknown'
            # Take first meaningful words, max 30 chars
            import re as _re_ec
            _words = _re_ec.findall(r'\b[A-Za-z0-9_]{2,}\b', _ec_source)
            error_code = '_'.join(_words[:4])[:30] if _words else 'invalid_input'

        summary = '%s_Negative_%s_%s' % (
            feature_id, feature_name.replace(' ', '_'), error_code,
        )

        description = 'To validate %s API returns error %s when %s' % (
            feature_name, error_code, condition[:100]
        )

        preconditions = '\n'.join([
            '1. TMO MDN available in SIT environment',
            '2. Condition: %s' % condition[:80],
            '3. API endpoint accessible: %s %s' % (method, endpoint),
        ])

        steps = [
            TestStep(
                step_num=1,
                summary='Preconditions: Set up error condition — %s' % condition[:70],
                expected='System is in state to trigger error %s' % error_code,
                data_reference='%s: %s' % (error_code, condition[:50]),
            ),
            TestStep(
                step_num=2,
                summary='Send %s request to %s with error-triggering data' % (method, _ep_display),
                expected='Request sent to API endpoint',
                data_reference='Endpoint: %s %s' % (method, _ep_display),
            ),
            TestStep(
                step_num=3,
                summary='Validate error response: code=%s, message="%s"' % (
                    error_code, error_msg[:60]),
                expected='Error response: code=%s, message="%s"' % (error_code, error_msg[:80]),
                data_reference='Business Rule: %s' % (neg_spec.source.source_id if neg_spec.source else error_code),
            ),
        ]

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category='Negative',
        traceability=neg_spec.source,
        dimension_values={'error_code': error_code},
    )


def _build_post_get_steps(
    api_context: Dict,
    request_fields: Dict[str, str] = None,
    expected_status: int = 200,
    expected_body: Dict[str, str] = None,
    is_negative: bool = False,
    error_code: str = '',
    precondition_text: str = '',
) -> List[TestStep]:
    """Build the standard 5-step POST/GET sequence for API test cases.

    Returns steps in order:
      1. Preconditions/Setup
      2. Build Request (list fields + values)
      3. Send Request (method + endpoint)
      4. Validate Response Status (expected HTTP code)
      5. Validate Response Body (expected fields/values)

    Used by both positive dimension TCs and negative Business Rule TCs.
    """
    endpoint = api_context.get('endpoint', '/api/v1/operation')
    method = api_context.get('method', 'POST')
    request_fields = request_fields or {}
    expected_body = expected_body or {}

    # Step 1: Preconditions
    if precondition_text:
        precond_summary = 'Preconditions: %s' % precondition_text
    elif is_negative:
        precond_summary = 'Preconditions: Set up error condition for %s' % error_code
    else:
        # Inject real SIT test data into preconditions
        try:
            from .test_data_injector import get_sample_data
            _mdn = get_sample_data('MDN')['value']
            _line = get_sample_data('LINE_ID')['value']
            precond_summary = 'Preconditions: Active TMO MDN=%s, lineId=%s in SIT environment. API endpoint accessible.' % (_mdn, _line)
        except Exception:
            precond_summary = 'Preconditions: TMO MDN active in SIT, API endpoint accessible'

    steps = [
        TestStep(
            step_num=1,
            summary=precond_summary,
            expected='System is in required state',
            data_reference='Environment: SIT',
        ),
    ]

    # Step 2: Build Request — inject real test data
    if request_fields:
        field_list = ', '.join('%s=%s' % (k, v) for k, v in request_fields.items())
        build_summary = 'Build request payload with fields: %s' % field_list
        build_ref = 'Fields: %s' % ', '.join(request_fields.keys())
    elif is_negative:
        build_summary = 'Build request payload with invalid/missing data to trigger %s' % error_code
        build_ref = 'Error trigger: %s' % error_code
    else:
        # Inject real test data from pool or NMNO spec
        try:
            from .test_data_injector import get_operation_sample_request, format_request_sample
            _api_name = api_context.get('api_name', '')
            _endpoint = api_context.get('endpoint', '')
            _req_fields = api_context.get('request_fields', [])
            _nmno_sample = api_context.get('_nmno_request_sample', '')
            _sample = get_operation_sample_request(_api_name, _endpoint, _req_fields, _nmno_sample)
            _sample_str = format_request_sample(_sample)
            build_summary = 'Build %s request payload: %s' % (method, _sample_str)
            build_ref = 'Test data (SIT): %s' % _sample_str[:80]
        except Exception:
            build_summary = 'Build request payload with valid SIT test data'
            build_ref = 'Fields: per API spec'

    steps.append(TestStep(
        step_num=2,
        summary=build_summary,
        expected='Request payload is constructed',
        data_reference=build_ref,
    ))

    # Step 3: Send Request
    steps.append(TestStep(
        step_num=3,
        summary='Send %s request to %s' % (method, endpoint),
        expected='Request is sent successfully',
        data_reference='Endpoint: %s %s' % (method, endpoint),
    ))

    # Step 4: Validate Response Status
    if is_negative:
        status_summary = 'Validate response status code is %d (error response)' % expected_status
        status_expected = 'Status code %d returned' % expected_status
    else:
        status_summary = 'Validate response status code is %d' % expected_status
        status_expected = 'Status code %d (Success) returned' % expected_status

    steps.append(TestStep(
        step_num=4,
        summary=status_summary,
        expected=status_expected,
        data_reference='Expected status: %d' % expected_status,
    ))

    # Step 5: Validate Response Body
    if is_negative and error_code:
        body_summary = 'Validate error code is %s in response body' % error_code
        body_expected = 'Error code %s present in response with correct message' % error_code
    elif expected_body:
        field_checks = ', '.join('%s=%s' % (k, v) for k, v in expected_body.items())
        body_summary = 'Validate response body: %s' % field_checks
        body_expected = 'Response fields match expected values'
    else:
        body_summary = 'Validate response body contains expected data'
        body_expected = 'Response body is valid and contains required fields'

    steps.append(TestStep(
        step_num=5,
        summary=body_summary,
        expected=body_expected,
        data_reference='Error: %s' % error_code if is_negative else 'Per API spec',
    ))

    return steps


def _build_clear_api_steps(
    feature_name: str,
    dim_name: str,
    value: str,
    api_context: Dict,
) -> List[TestStep]:
    """Build crystal-clear API steps that a tester can follow immediately."""
    # Use the dimension value as method if dim_name is http_method
    if dim_name == 'http_method':
        method = value
    else:
        method = api_context.get('method', 'POST')  # POST is the primary/full API call
    endpoint = api_context.get('endpoint', '')    # Empty if no real endpoint known — no slug fallback

    human_dim = _humanize_dim_name(dim_name)

    steps = [
        TestStep(step_num=1,
            summary='Prepare %s request to %s with %s=%s in %s' % (
                method, endpoint, human_dim, value,
                'query parameters' if method == 'GET' else 'request body'),
            expected='Request payload/URL constructed with valid %s identifier for %s' % (value, feature_name),
            data_reference='%s %s | %s=%s' % (method, endpoint, dim_name, value)),
        TestStep(step_num=2,
            summary='Send %s %s via ITMBO channel with required headers (RequestType, messageHeader)' % (method, endpoint),
            expected='API responds with HTTP 200 OK within acceptable response time',
            data_reference='Channel: ITMBO | Endpoint: %s' % endpoint),
        TestStep(step_num=3,
            summary='Verify response body contains %s-specific data for %s=%s' % (feature_name, human_dim, value),
            expected='Response JSON includes correct %s details: %s' % (
                feature_name,
                ', '.join(api_context.get('response_fields', [])[:5]) or 'all expected fields populated'),
            data_reference='Response validation: %s=%s' % (dim_name, value)),
        TestStep(step_num=4,
            summary='Verify transaction logged in NSL Transaction History for this %s operation' % feature_name,
            expected='Transaction record created with correct %s=%s, timestamp, and status=SUCCESS' % (human_dim, value),
            data_reference='Transaction History verification'),
    ]

    # POST-specific: add Century Report and HTML file download validation
    if method == 'POST':
        steps.append(TestStep(
            step_num=5,
            summary='Verify Century Report entry for %s POST transaction' % feature_name,
            expected='Century Report shows transaction with correct service grouping, timestamp, and status',
            data_reference='Century Report validation'))
        steps.append(TestStep(
            step_num=6,
            summary='Download and validate HTML transaction report file',
            expected='HTML file downloaded successfully, contains transaction details matching the %s POST request' % feature_name,
            data_reference='HTML file download validation'))

    return steps


def _build_api_preconditions(dim_name: str, value: str, api_context: Dict) -> str:
    """Build environment-specific preconditions matching QMetry manual style."""
    endpoint = api_context.get('endpoint', '')

    if dim_name == 'product':
        device_type = value  # Phone, Tablet, Wearable
        return '\n'.join([
            '1. Active TMO MDN available in SIT environment with %s device' % device_type,
            '2. Line Status: Active',
            '3. API endpoint accessible: %s' % endpoint,
        ])
    elif dim_name == 'input_type':
        return '\n'.join([
            '1. Active TMO MDN available in SIT environment',
            '2. Valid %s identifier available for test subscriber' % value,
            '3. Line Status: Active',
            '4. API endpoint accessible: %s' % endpoint,
        ])
    elif dim_name == 'line_state':
        return '\n'.join([
            '1. TMO MDN available in SIT environment',
            '2. Line Status: %s' % value,
            '3. API endpoint accessible: %s' % endpoint,
        ])
    elif dim_name == 'http_method':
        return '\n'.join([
            '1. Active TMO MDN available in SIT environment',
            '2. Line Status: Active',
            '3. API endpoint accessible: %s %s' % (value, endpoint),
        ])
    else:
        return '\n'.join([
            '1. Active TMO MDN available in SIT environment',
            '2. Line Status: Active',
            '3. API endpoint accessible: %s' % endpoint,
        ])


# ================================================================
# CHANNEL-SPECIFIC STEP GENERATION (LEGACY)
# ================================================================


def _build_channel_specific_steps(
    channel: str,
    feature_name: str,
    dim_name: str,
    value: str,
    deep_mine_result=None,
) -> List[TestStep]:
    """Generate ITMBO API steps or NBOP UI steps based on channel."""
    if channel and channel.upper() == 'NBOP':
        return _build_nbop_ui_steps(feature_name, dim_name, value)
    else:
        return _build_itmbo_api_steps(feature_name, dim_name, value, deep_mine_result)


def _build_nbop_ui_steps(
    feature_name: str,
    dim_name: str,
    value: str,
) -> List[TestStep]:
    """Generate NBOP UI navigation steps using nbop_ui_knowledge."""
    # Try to get real navigation path from NBOP knowledge
    nav_path = None
    try:
        from .nbop_ui_knowledge import get_navigation_path, is_available
        if is_available():
            nav_path = get_navigation_path(feature_name)
    except (ImportError, Exception):
        pass

    if nav_path:
        steps = [
            TestStep(step_num=1,
                summary='Launch NBOP portal and navigate: %s' % nav_path,
                expected='%s page loaded successfully' % feature_name,
                data_reference='NBOP navigation: %s' % nav_path),
            TestStep(step_num=2,
                summary='Search subscriber and select %s=%s' % (_humanize_dim_name(dim_name), value),
                expected='Subscriber profile loaded with %s context' % value,
                data_reference='%s=%s' % (dim_name, value)),
            TestStep(step_num=3,
                summary='Execute %s operation for %s=%s via NBOP UI' % (feature_name, _humanize_dim_name(dim_name), value),
                expected='Operation completed successfully via NBOP portal',
                data_reference='NBOP UI: %s' % feature_name),
            TestStep(step_num=4,
                summary='Verify subscriber profile updated correctly for %s=%s' % (_humanize_dim_name(dim_name), value),
                expected='Profile reflects %s changes specific to %s' % (feature_name, value),
                data_reference='%s=%s via NBOP' % (dim_name, value)),
        ]
    else:
        # Fallback: best-effort UI steps without specific navigation
        steps = [
            TestStep(step_num=1,
                summary='Launch NBOP portal and search subscriber by MDN',
                expected='Subscriber profile loaded',
                data_reference='NBOP portal'),
            TestStep(step_num=2,
                summary='Navigate to %s feature with %s=%s' % (feature_name, _humanize_dim_name(dim_name), value),
                expected='Feature page accessible for %s' % value,
                data_reference='%s=%s' % (dim_name, value)),
            TestStep(step_num=3,
                summary='Execute %s operation for %s=%s' % (feature_name, _humanize_dim_name(dim_name), value),
                expected='Operation processed for %s via NBOP' % value,
                data_reference='NBOP: %s=%s' % (dim_name, value)),
        ]

    return steps


def _build_itmbo_api_steps(
    feature_name: str,
    dim_name: str,
    value: str,
    deep_mine_result=None,
) -> List[TestStep]:
    """Generate ITMBO API request steps with endpoint and method."""
    # Try to get API details from deep mine result
    endpoint = ''
    method = 'POST'
    if deep_mine_result and deep_mine_result.api_specs:
        spec = deep_mine_result.api_specs[0]
        endpoint = spec.endpoint or ''
        method = spec.http_method or 'POST'

    # If still no endpoint from spec, leave it empty — steps reference the API name only
    _endpoint_ref = endpoint if endpoint else ('%s API' % feature_name)

    steps = [
        TestStep(step_num=1,
            summary='Prepare %s request with %s=%s in payload' % (method, _humanize_dim_name(dim_name), value),
            expected='Request payload constructed with %s=%s' % (_humanize_dim_name(dim_name), value),
            data_reference='%s %s' % (method, _endpoint_ref)),
        TestStep(step_num=2,
            summary='Send %s request to %s via ITMBO channel' % (method, _endpoint_ref),
            expected='API returns 200 OK with valid response',
            data_reference='Endpoint: %s' % _endpoint_ref),
        TestStep(step_num=3,
            summary='Verify response confirms %s processed for %s=%s' % (feature_name, _humanize_dim_name(dim_name), value),
            expected='Response body contains confirmation for %s with %s-specific data' % (feature_name, value),
            data_reference='%s=%s via ITMBO API' % (dim_name, value)),
    ]

    return steps


def _build_api_spec_steps(
    spec,
    scenario_title: str,
    feature_name: str,
) -> List[TestStep]:
    """Build steps from an APISpec object."""
    method = spec.http_method or 'POST'
    endpoint = spec.endpoint or ''
    _endpoint_ref = endpoint if endpoint else ('%s API' % spec.api_name if spec.api_name else feature_name)

    steps = [
        TestStep(step_num=1,
            summary='Prepare %s request to %s for scenario: %s' % (method, _endpoint_ref, scenario_title[:50]),
            expected='Request payload prepared per API specification',
            data_reference='%s %s' % (method, _endpoint_ref)),
        TestStep(step_num=2,
            summary='Send %s %s with required headers and payload' % (method, _endpoint_ref),
            expected='API responds with expected status code',
            data_reference='API: %s' % spec.api_name),
        TestStep(step_num=3,
            summary='Validate response matches scenario: %s' % scenario_title[:60],
            expected='Response fields match expected values per %s specification' % spec.api_name,
            data_reference='Scenario: %s' % scenario_title[:40]),
    ]

    return steps


# ================================================================
# HELPERS
# ================================================================


def _assign_serial_numbers(test_cases: List[TestCase]) -> None:
    """Assign sequential serial numbers and priorities to all test cases.

    Priority assignment (Phase 5):
      P1 (Critical): Happy Path core scenarios — the primary feature verification.
                     First 3 happy path TCs, or any TC with 'corrects'/'validates' in title.
      P2 (Important): Negative/error scenarios, regression TCs.
      P3 (Nice-to-have): Edge cases, remaining happy path beyond core set.
    """
    # Count happy path TCs to determine P1 allocation
    happy_path_count = 0
    for i, tc in enumerate(test_cases, 1):
        tc.sno = str(i)

        # Priority assignment based on category and position
        category_lower = (tc.category or '').lower()
        summary_lower = (tc.summary or '').lower()

        if category_lower == 'negative':
            tc.priority = 'P2'
        elif category_lower == 'regression':
            tc.priority = 'P2'
        elif category_lower == 'edge case':
            tc.priority = 'P3'
        elif category_lower == 'happy path':
            happy_path_count += 1
            # First 5 happy path TCs are P1 (core verification)
            if happy_path_count <= 5:
                tc.priority = 'P1'
            else:
                tc.priority = 'P3'
        else:
            tc.priority = 'P2'


def _transform_to_scenario_title(raw_text: str, feature_name: str) -> str:
    """Transform raw AC text into a proper test scenario title.

    Rules:
      1. Strip implementation detail after the core intent
      2. For violation/error patterns: keep only the violation code
      3. Remove em-dashes and trailing explanations
      4. Never exceed 70 chars (truncate at word boundary)
      5. No punctuation artifacts at the end

    Examples:
      'Verify data-alignment corrects ANDROID_AS_IOS violation by NSL triggers CM event...'
      → 'Verify data-alignment corrects ANDROID_AS_IOS violation'

      'Verify data-alignment corrects MAKE_MISSING violation — device make differs between...'
      → 'Verify data-alignment corrects MAKE_MISSING violation'

      'When CS access to the MNO_TMO permission is OFF, NBOP to display...'
      → 'Verify MNO_TMO permission OFF hides MNO options'
    """
    text = raw_text.strip()

    # ── Strip implementation detail after violation code ──
    # Pattern: "corrects VIOLATION_CODE violation [by/—/when/OS/device...]"
    # Keep up to "violation" and drop the rest
    violation_match = re.match(
        r'^((?:Verify\s+)?.*?(?:corrects|rejects|handles)\s+[A-Z][A-Z0-9_]+(?:\s+violation)?)',
        text
    )
    if violation_match:
        text = violation_match.group(1).strip()
        # Clean trailing punctuation
        text = text.rstrip(' —-,.')
        # If still too long, abbreviate "data-alignment corrects" → "DataAlign"
        if len(text) > 60:
            text = re.sub(r'data-alignment\s+corrects\s+', 'DataAlign_', text)
            text = re.sub(r'data-alignment\s+rejects\s+', 'DataAlign_Rejects_', text)
            text = re.sub(r'data-alignment\s+handles\s+', 'DataAlign_Handles_', text)
        if len(text) <= 70:
            return text

    # ── Strip after em-dash (—) or " by " or " when " for long titles ──
    if len(text) > 70:
        for separator in [' — ', ' by NSL ', ' by NSL', ' OS differs', ' device make differs']:
            if separator in text:
                text = text.split(separator)[0].strip()
                break

    # ── "When X, Y" pattern → "Verify Y when X" ──
    when_match = re.match(
        r"^[Ww]hen\s+(.{10,80}?),?\s+(?:NBOP\s+to\s+|the\s+system\s+(?:shall\s+)?|NSL\s+(?:shall\s+)?)?(.+)",
        text
    )
    if when_match:
        condition = when_match.group(1).strip().rstrip(',')[:40]
        action = when_match.group(2).strip()[:40]
        return 'Verify %s when %s' % (action, condition)

    # ── Verb prefix → add "Verify" ──
    if re.match(r'^(Display|Show|Return|Send|Update|Create|Delete|Trigger|Process|Handle)\s', text, re.IGNORECASE):
        text = 'Verify %s' % text

    # ── Already starts with "Verify"/"Validate" — keep it ──
    if not text.lower().startswith(('verify ', 'validate ', 'for ')):
        text = 'Verify %s' % text

    # ── Final truncation at 70 chars, word boundary ──
    if len(text) > 70:
        # Find last space before position 70
        cut_pos = text.rfind(' ', 0, 70)
        if cut_pos > 30:
            text = text[:cut_pos]
        else:
            text = text[:70]

    # Clean trailing punctuation/artifacts
    text = text.rstrip(' —-,.:')

    return text


def _extract_feature_name(jira) -> str:
    """Extract a clean feature name from Jira summary.

    Handles formats like:
      '[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)'
      'Port-Out - Unsolicited Port Out / Update Port Out'
    """
    summary = jira.summary if jira and jira.summary else 'Unknown Feature'
    # Strip component prefix like "[NSLNM, NENM, INTG]: " or "[NSLNM]: "
    cleaned = re.sub(r'^\[.*?\]\s*:?\s*', '', summary).strip()
    # Strip "New MVNO - " prefix
    cleaned = re.sub(r'^New MVNO\s*[-–—]\s*', '', cleaned).strip()
    # Strip method suffix like "(GET/POST)" or "(GET)"
    cleaned = re.sub(r'\s*\((?:GET|POST|PUT|DELETE)[/\w]*\)\s*$', '', cleaned).strip()
    # Truncate to reasonable length
    if len(cleaned) > 50:
        cleaned = cleaned[:47] + '...'
    return cleaned or 'Unknown Feature'


def _get_default_channel(jira) -> str:
    """Get default channel from Jira data."""
    if jira and hasattr(jira, 'channel') and jira.channel:
        return jira.channel
    return 'ITMBO'


def _humanize_dim_name(dim_name: str) -> str:
    """Convert dimension name to human-readable form."""
    mapping = {
        'input_type': 'Input Type',
        'product': 'Product',
        'channel': 'Channel',
        'error_code': 'Error Code',
        'line_state': 'Line State',
        'precondition': 'Precondition',
        'nav_path': 'Navigation Path',
        'action_point': 'Action',
        'page_name': 'Page',
    }
    return mapping.get(dim_name, dim_name.replace('_', ' ').title())


def _display_endpoint(endpoint: str, method: str = 'POST', feature_name: str = '') -> str:
    """Return a clean endpoint display string — never produces trailing whitespace.

    If endpoint is empty, returns a descriptive fallback like 'NSL API endpoint'
    so steps never say 'Send POST request to ' (trailing space).
    """
    ep = (endpoint or '').strip()
    if ep:
        return ep
    # Fallback: derive from feature_name if available
    if feature_name:
        # Convert "Reset Plan" → "reset-plan API endpoint"
        slug = feature_name.lower().replace(' ', '-')[:30]
        return '/nsl/provisioning/... (%s API)' % slug
    return 'NSL API endpoint'


def _build_dimension_summary(feature_name: str, dim_name: str, value: str, feature_id: str) -> str:
    """Build a data-specific summary matching QMetry naming style.

    Format: FEATURE_TC_Channel_Operation_DimensionValue
    Negative TCs get 'Negative:' prefix.
    """
    if dim_name == 'input_type':
        return '%s_ITMBO_Validate %s by %s' % (feature_id, feature_name, value)
    elif dim_name == 'product':
        return '%s_ITMBO_Validate %s %s' % (feature_id, feature_name, value)
    elif dim_name == 'channel':
        return '%s_%s_Validate %s' % (feature_id, value, feature_name)
    elif dim_name == 'line_state':
        return '%s_Negative: %s rejected for %s MDN' % (feature_id, feature_name, value)
    elif dim_name == 'http_method':
        return '%s_ITMBO_Validate %s via %s method' % (feature_id, feature_name, value)
    else:
        return '%s_ITMBO_Validate %s %s=%s' % (feature_id, feature_name, _humanize_dim_name(dim_name), value)


def _build_preconditions(dim_name: str, value: str, channel: str) -> str:
    """Build preconditions based on dimension and channel."""
    preconditions = ['1. Subscriber line active in system']

    if channel and channel.upper() == 'NBOP':
        preconditions.append('2. NBOP portal accessible with valid credentials')
    else:
        preconditions.append('2. ITMBO API endpoint accessible')

    if dim_name == 'line_state':
        preconditions.append('3. Line set to %s state' % value)
    elif dim_name == 'input_type':
        preconditions.append('3. Valid %s available for test subscriber' % value)
    elif dim_name == 'product':
        preconditions.append('3. Subscriber has %s device type' % value)

    return '\n'.join(preconditions)


# ================================================================
# FEATURE INTENT CLASSIFICATION
# ================================================================


def _classify_feature_intent(jira, deep_mine_result, log: Callable = print) -> Dict:
    """Classify feature as API-only, UI-only, or dual-path (API+UI).

    Analyzes:
      - Jira summary components (NSLNM=API, MWTGNBOP=UI, INTG=Integration)
      - Subtask component types
      - AC content for API/UI keywords
    """
    intent = {
        'type': 'api',  # default
        'has_api': False,
        'has_ui': False,
        'channels': [],
        'api_subtasks': [],
        'ui_subtasks': [],
    }

    # ── From Jira summary ──
    summary = (jira.summary if jira else '').upper()
    if any(kw in summary for kw in ['NSLNM', 'NENM', 'INTG', 'API', 'REST']):
        intent['has_api'] = True
    # Only mark as UI if the MAIN feature summary says NBOP/UI (not just subtasks)
    if any(kw in summary for kw in ['NBOP', 'UI', 'PORTAL', 'SCREEN']):
        intent['has_ui'] = True

    # ── From subtask components ──
    # NOTE: Subtask keys containing "NBOP" does NOT mean the feature is UI-based.
    # The feature's own summary components determine the primary type.
    # Subtasks are implementation details — NBOP subtask might just be "display API results"
    if deep_mine_result and deep_mine_result.subtask_mines:
        for mine in deep_mine_result.subtask_mines:
            key_upper = (mine.key or '').upper()
            comp = (mine.component or '').upper()
            summary_low = (mine.summary or '').lower()

            if comp in ('API', 'INT', 'NE', '') or 'NSLNM' in key_upper or 'api' in summary_low or 'endpoint' in summary_low:
                intent['has_api'] = True
                intent['api_subtasks'].append(mine)
            if comp == 'UI' or 'NBOP' in key_upper or 'nbop' in summary_low or 'portal' in summary_low:
                intent['ui_subtasks'].append(mine)
                # Only set has_ui if the MAIN feature also indicates UI
                # (subtask being NBOP doesn't make the feature UI-testable)

    # ── From AC content ──
    ac_text = (jira.acceptance_criteria if jira else '').lower()
    if any(kw in ac_text for kw in ['api', 'endpoint', 'get ', 'post ', 'request', 'response', 'payload']):
        intent['has_api'] = True
    # Only mark UI if AC explicitly says "NBOP portal" or "UI testing"
    if any(kw in ac_text for kw in ['nbop portal', 'ui testing', 'navigate to nbop', 'subscriber profile']):
        intent['has_ui'] = True

    # ── Determine type and channels ──
    if intent['has_api'] and intent['has_ui']:
        intent['type'] = 'dual'
        intent['channels'] = ['ITMBO', 'NBOP']
    elif intent['has_ui'] and not intent['has_api']:
        intent['type'] = 'ui'
        intent['channels'] = ['NBOP']
    else:
        intent['type'] = 'api'
        intent['channels'] = ['ITMBO']

    return intent


def _build_api_context(jira, deep_mine_result, feature_name: str) -> Dict:
    """Build API context from available data for step generation.

    Extracts: endpoint, method, request fields, response fields, api_name.
    IMPORTANT: Never fall back to slugifying the feature title as an endpoint.
    If no real API spec is available, leave endpoint empty — callers must handle
    that case with domain-specific step templates, not generic slug endpoints.
    """
    ctx = {
        'endpoint': '',          # Empty = no known endpoint. Never slugify the feature title.
        'method': 'POST',
        'api_name': '',          # Empty = unknown. Callers check for this.
        'request_fields': [],
        'response_fields': [],
        'source_system': 'ITMBO',
        'target_system': 'NSL',
    }

    if deep_mine_result and deep_mine_result.api_specs:
        spec = deep_mine_result.api_specs[0]
        if spec.endpoint:
            ctx['endpoint'] = spec.endpoint
        if spec.http_method:
            ctx['method'] = spec.http_method
        if spec.api_name:
            ctx['api_name'] = spec.api_name
        if spec.request_fields:
            ctx['request_fields'] = spec.request_fields
        if spec.response_fields:
            ctx['response_fields'] = spec.response_fields
        if spec.source_system:
            ctx['source_system'] = spec.source_system
        if spec.target_system:
            ctx['target_system'] = spec.target_system

    # Also try to extract from Jira AC (Chalk URLs often have API name)
    if jira and jira.acceptance_criteria:
        import re as _re
        # Look for API endpoint patterns
        ep_match = _re.search(r'(/api/[^\s"\']+|/mbosportout/[^\s"\']+)', jira.acceptance_criteria)
        if ep_match and not deep_mine_result:
            ctx['endpoint'] = ep_match.group(1)
        # NOTE: Do NOT override method from Jira summary — it often says "GET/POST"
        # which would incorrectly default to GET. The method dimension handles this.

    return ctx


# ================================================================
# DUAL-PATH TC GENERATION
# ================================================================


def _generate_dual_path_tcs(
    plan: CombinationPlan,
    jira,
    feature_name: str,
    api_context: Dict,
    feature_intent: Dict,
    log: Callable = print,
) -> List[TestCase]:
    """Generate paired TCs for dual-path features (same scenario, API + UI paths).

    For hybrid features:
      - API TCs (dimension + negative) are already generated with channel "ITMBO"
      - This function generates the UI-path TCs with channel "NBOP"

    Deduplication is handled by the caller (build_test_cases).

    GUARD: Only fires when the feature is a genuine NBOP UI feature where
    NBOP navigation is the testing mechanism. Never fires for CR/mediation/
    notification features that happen to have NBOP as a display channel.
    """
    dual_tcs: List[TestCase] = []
    feature_id = jira.key if jira else ''

    # ── Hard gate: refuse to generate device-matrix UI TCs for non-UI features ──
    # Mediation/CDR/notification/CR features have NBOP as a DISPLAY channel only,
    # not a testing mechanism. Device-type matrix TCs are meaningless for them.
    # Only generate dual-path TCs when the feature has explicit UI testing intent.
    _has_ui_subtasks = bool(feature_intent.get('ui_subtasks'))
    _has_api_subtasks = bool(feature_intent.get('api_subtasks'))
    _is_pure_display = not _has_ui_subtasks and _has_api_subtasks
    if _is_pure_display:
        log('[TC-BUILD]   Dual-path skipped: no UI subtasks — feature uses NBOP for display only, not testing')
        return []

    # Also skip for mediation/notification/CR features by feature type
    _jira_summary = (jira.summary if jira else '').lower()
    _MEDIATION_SIGNALS = ['med, ', ', med,', 'nslnm, med', 'mediation', 'prr', 'cdr', 'dsource',
                          'dsource', 'data source', 'usage detail', 'usage data',
                          '- cr -', 'cr -', '- cr:', 'fix:', 'fix -', 'defect']
    if any(sig in _jira_summary for sig in _MEDIATION_SIGNALS):
        log('[TC-BUILD]   Dual-path skipped: mediation/CR feature — device matrix TCs do not apply')
        return []

    # Get NBOP navigation path
    nav_path = _get_nbop_nav_path(feature_name)

    # For each independent dimension, generate a UI-path TC
    # (The dimension TCs already generated are API-path by default)
    # ── Structural/metadata dimensions must NEVER become UI TCs ──
    _STRUCTURAL_DIMS_UI = {
        'precondition', 'nav_path', 'navigation', 'action_point',
        'page_name', 'context', 'ordering_channel', 'portal_screen',
    }

    def _is_nav_value_ui(val: str) -> bool:
        """True if value is a navigation path, not a testable identifier."""
        s = str(val)
        return '→' in s or '->' in s or s.lower().startswith('navigate to')

    for dim in plan.independent_dimensions:
        # Skip negative dimensions — handled separately
        if dim.name in ('error_code', 'line_state'):
            continue  # Negative scenarios handled separately
        # Skip structural/metadata dimensions — these are navigation context, not test axes
        if dim.name.lower() in _STRUCTURAL_DIMS_UI:
            continue
        # Skip if all values look like navigation paths
        if dim.values and all(_is_nav_value_ui(v) for v in dim.values):
            continue

        for value in dim.values:
            # Skip individual values that are navigation paths (e.g. "NBOP → Mobile Service Management")
            if _is_nav_value_ui(value):
                continue
            # Generate UI-path TC for this dimension value
            ui_tc = _build_ui_path_tc(
                feature_id, feature_name, dim.name, value,
                nav_path, api_context, jira
            )
            dual_tcs.append(ui_tc)

    log('[TC-BUILD]   Hybrid: generated %d UI-path TCs (channel=NBOP)' % len(dual_tcs))
    return dual_tcs


def _build_ui_path_tc(
    feature_id: str,
    feature_name: str,
    dim_name: str,
    value: str,
    nav_path: str,
    api_context: Dict,
    jira,
) -> TestCase:
    """Build a UI-path TC with NBOP-specific navigation steps."""
    human_dim = _humanize_dim_name(dim_name)
    _full_nav = nav_path or 'NBOP → Mobile Service Management'

    # Build TC summary — e.g. "MWTGPROV-4020_NBOP_Validate Reset Plan Product=Phone"
    summary = '%s_NBOP_Validate %s %s=%s' % (feature_id, feature_name, human_dim, value)

    # Short intent-focused description
    description = 'To validate %s via NBOP portal for %s %s' % (
        feature_name, human_dim, value
    )

    # Environment-specific preconditions
    if dim_name == 'product':
        sub_state = 'Active TMO subscriber line with %s device type' % value
    elif dim_name == 'line_state':
        sub_state = 'TMO subscriber line in %s state' % value
    else:
        sub_state = 'Active TMO subscriber line in SIT environment'

    preconditions = '\n'.join([
        '1. %s' % sub_state,
        '2. NBOP portal accessible (SIT environment)',
        '3. Agent credentials with MNO_TMO permission',
    ])

    # Build context-aware UI steps
    # Step 3 describes what to DO with the subscriber, not "Search using {dim_name}={value}"
    if dim_name == 'product':
        step3_action = 'Search for TMO subscriber MDN with active %s device' % value
        step3_expected = 'Subscriber found. Line profile loaded showing %s device.' % value
        step4_action = 'Execute %s operation and verify result for %s' % (feature_name, value)
        step4_expected = '%s completed successfully for %s device. NBOP Line Summary updated.' % (feature_name, value)
    elif dim_name == 'input_type':
        step3_action = 'Search for TMO subscriber using %s as the identifier' % value
        step3_expected = 'Subscriber found via %s. Line profile loaded.' % value
        step4_action = 'Execute %s and verify result' % feature_name
        step4_expected = '%s completed. NBOP reflects correct post-operation state.' % feature_name
    elif dim_name == 'line_state':
        step3_action = 'Search for TMO subscriber MDN in %s state' % value
        step3_expected = 'Subscriber found with line in %s state.' % value
        step4_action = 'Attempt %s operation and verify rejection/error handling' % feature_name
        step4_expected = 'Operation rejected with appropriate error for %s line state.' % value
    else:
        step3_action = 'Search for TMO subscriber MDN and load line profile'
        step3_expected = 'Subscriber line profile loaded in NBOP portal.'
        step4_action = 'Execute %s operation (%s: %s) and verify result' % (feature_name, human_dim, value)
        step4_expected = '%s completed. NBOP reflects correct post-operation state for %s=%s.' % (feature_name, human_dim, value)

    steps = [
        TestStep(step_num=1,
            summary='Login to NBOP portal with agent credentials (MNO_TMO permission required)',
            expected='NBOP dashboard loaded. Agent session active.',
            data_reference='NBOP portal login'),
        TestStep(step_num=2,
            summary='Navigate to: %s' % _full_nav,
            expected='%s section loaded and ready.' % feature_name,
            data_reference='Navigation: %s' % _full_nav),
        TestStep(step_num=3,
            summary=step3_action,
            expected=step3_expected,
            data_reference='%s=%s' % (dim_name, value)),
        TestStep(step_num=4,
            summary=step4_action,
            expected=step4_expected,
            data_reference='NBOP UI verification: %s=%s' % (dim_name, value)),
    ]

    tr = create_traceability(
        source_type='Jira AC',
        source_id=feature_id,
        extracted_text='UI path verification: %s=%s via NBOP portal' % (dim_name, value),
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
        dimension_values={dim_name: value, 'channel': 'NBOP'},
    )


def _get_nbop_nav_path(feature_name: str) -> str:
    """Get NBOP navigation path for a feature using nbop_ui_knowledge."""
    try:
        from .nbop_ui_knowledge import get_navigation_path, is_available
        if is_available():
            path = get_navigation_path(feature_name)
            if path:
                return path
    except (ImportError, Exception):
        pass
    return ''


# ================================================================
# UI PATH: SCENARIO-TO-TC MAPPING + ELEMENT VERIFICATION
# ================================================================


def _extract_elements_from_scenario(scenario_title: str, scenario_desc: str = '') -> List[Dict]:
    """Extract UI elements and their expected visibility state from scenario text.

    Parses scenario title/description for patterns like:
      - "hide the page X" / "hide X"
      - "show X" / "X is displayed"
      - "X is not displayed" / "remove X"

    Returns list of {"name": str, "state": "present"|"absent", "condition": str}
    """
    text = '%s %s' % (scenario_title or '', scenario_desc or '')
    elements = []

    # Extract condition first (e.g., "for TMO only", "for TMO subscribers")
    condition_match = re.search(r'\b(for\s+(?:TMO|VZW|MVNO)[\s\w]*)', text, re.IGNORECASE)
    condition = condition_match.group(1).strip() if condition_match else ''

    # ── ABSENT patterns (element should NOT be visible) ──
    absent_patterns = [
        # "hide the page Port-in status" → "Port-in status"
        (r'hide\s+the\s+page\s+(.+?)(?:\s+for\s+|\s*$)', 'absent'),
        # "hide the X tab/page/section" → "X"
        (r'hide\s+the\s+(.+?)\s+(?:tab|page|section)', 'absent'),
        # "remove the X tab/page/section" → "X"
        (r'remove\s+the\s+(.+?)\s+(?:tab|page|section|for\b)', 'absent'),
        # "X is not displayed" / "X should not be displayed"
        (r'(.+?)\s+(?:is|should)\s+not\s+(?:be\s+)?(?:displayed|visible|shown)', 'absent'),
    ]

    # ── PRESENT patterns (element should be visible) ──
    present_patterns = [
        # "show the X page/tab" → "X"
        (r'show\s+the\s+(.+?)\s+(?:tab|page|section)', 'present'),
        # "X option is displayed" / "X is displayed"
        (r'(.+?)\s+(?:option\s+)?(?:is|should\s+be)\s+displayed', 'present'),
        # "X is visible"
        (r'(.+?)\s+(?:is|should\s+be)\s+visible', 'present'),
    ]

    # Try absent patterns first (more specific)
    for pattern, state in absent_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip().rstrip('.,;')
            # Clean up: remove trailing "for TMO" etc from name
            name = re.sub(r'\s+for\s+(?:TMO|VZW|MVNO).*$', '', name, flags=re.IGNORECASE).strip()
            if name and 3 < len(name) < 50:
                elements.append({'name': name, 'state': state, 'condition': condition})
                break  # Take first match only

    # If no absent found, try present patterns
    if not elements:
        for pattern, state in present_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                name = match.group(1).strip().rstrip('.,;')
                name = re.sub(r'\s+for\s+(?:TMO|VZW|MVNO).*$', '', name, flags=re.IGNORECASE).strip()
                if name and 3 < len(name) < 50:
                    elements.append({'name': name, 'state': state, 'condition': condition})
                    break

    return elements


def _build_element_verification_step(
    element_name: str,
    expected_state: str,
    condition: str = '',
    step_num: int = 1,
) -> TestStep:
    """Build a single element verification step.

    Args:
        element_name: Name of the UI element (e.g., "Port-in Status")
        expected_state: "present" or "absent"
        condition: Optional condition (e.g., "for TMO subscribers")
        step_num: Step number in the sequence

    Returns TestStep with clear verification description and expected result.
    """
    condition_suffix = ' %s' % condition if condition else ''

    if expected_state == 'absent':
        summary = "Verify '%s' is NOT displayed on the page%s" % (element_name, condition_suffix)
        expected = "Element '%s' is not visible to the user" % element_name
    else:
        summary = "Verify '%s' IS displayed on the page%s" % (element_name, condition_suffix)
        expected = "Element '%s' is visible and accessible to the user" % element_name

    return TestStep(
        step_num=step_num,
        summary=summary,
        expected=expected,
        data_reference='UI element: %s (expected: %s)' % (element_name, expected_state),
    )


def _build_ui_scenario_tc(
    scenario: Dict,
    idx: int,
    feature_name: str,
    feature_id: str,
    nav_path: str = '',
    log: Callable = print,
) -> TestCase:
    """Convert a single Chalk scenario into a UI test case.

    1:1 mapping: one scenario = one TC.
    Prepends NBOP navigation steps.
    Appends element verification steps extracted from scenario.

    Args:
        scenario: Dict with 'title', 'validation', 'category', 'steps_hint', etc.
        idx: TC index (for numbering)
        feature_name: Feature name for context
        feature_id: Jira key (e.g., MWTGPROV-4006)
        nav_path: NBOP navigation path (from nbop_ui_knowledge)
    """
    title = scenario.get('title', 'Scenario %d' % (idx + 1))
    validation = scenario.get('validation', '')
    category = scenario.get('category', 'Happy Path')
    steps_hint = scenario.get('steps_hint', [])

    summary = '%s_TC%02d_NBOP_%s' % (feature_id, idx + 1, title[:60])

    description = 'UI verification: %s via NBOP portal' % title

    preconditions = '\n'.join([
        '1. Active TMO MDN available in SIT environment',
        '2. NBOP portal accessible with valid credentials',
        '3. User has appropriate role/permissions',
    ])

    steps = []
    step_num = 0

    # ── Step 1: Login to NBOP ──
    step_num += 1
    steps.append(TestStep(
        step_num=step_num,
        summary='Login to NBOP portal with valid credentials',
        expected='User is logged in successfully, dashboard displayed',
        data_reference='NBOP portal login',
    ))

    # ── Step 2: Navigate to target page ──
    step_num += 1
    if nav_path:
        steps.append(TestStep(
            step_num=step_num,
            summary='Navigate to: %s' % nav_path,
            expected='Target page/section loaded successfully',
            data_reference='Navigation: %s' % nav_path,
        ))
    else:
        steps.append(TestStep(
            step_num=step_num,
            summary='Navigate to %s section in subscriber profile' % feature_name,
            expected='%s page loaded' % feature_name,
            data_reference='NBOP navigation',
        ))

    # ── Step 3+: Scenario-specific action steps (from steps_hint) ──
    if steps_hint:
        for hint in steps_hint[:3]:  # Max 3 action steps from hints
            step_num += 1
            steps.append(TestStep(
                step_num=step_num,
                summary=hint,
                expected='Action completed successfully',
                data_reference='Scenario: %s' % title[:40],
            ))

    # ── Final steps: Element verification ──
    elements = _extract_elements_from_scenario(title, validation)
    if elements:
        for elem in elements:
            step_num += 1
            steps.append(_build_element_verification_step(
                element_name=elem['name'],
                expected_state=elem['state'],
                condition=elem['condition'],
                step_num=step_num,
            ))
    else:
        # If no elements extracted, add a generic verification step
        step_num += 1
        if validation:
            steps.append(TestStep(
                step_num=step_num,
                summary='Verify: %s' % validation[:80],
                expected=validation[:100] if validation else 'Expected behavior confirmed',
                data_reference='Scenario validation: %s' % title[:40],
            ))
        else:
            steps.append(TestStep(
                step_num=step_num,
                summary='Verify %s behavior matches scenario expectation' % feature_name,
                expected='UI displays correct state per scenario',
                data_reference='Scenario: %s' % title[:40],
            ))

    # Build traceability
    tr = create_traceability(
        source_type='Chalk Scenario',
        source_id='%s_scenario_%d' % (feature_id, idx + 1),
        extracted_text=title[:200],
    )

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=category,
        traceability=tr,
        dimension_values={'channel': 'NBOP', 'scenario': title[:50]},
    )


# ================================================================
# UI PATH: ENRICHED STEP GENERATION (Tasks 3.1–3.5)
# ================================================================


def _parse_ac_verification_points(ac_text: str) -> List[Dict]:
    """Parse subtask AC text into structured verification points.

    Extracts:
      - element_name: The UI element being verified
      - expected_state: "present" | "absent" | "changed" | "unchanged"
      - condition: Qualifying condition (e.g., "for TMO subscribers")
      - page_section: Where on the page (e.g., "History Details screen")

    Patterns recognized:
      - "{element} is removed from {page} for {condition}"
      - "{element} should not be displayed on {page}"
      - "{element} is hidden for {condition}"
      - "All other information remains visible"
      - "{element} is visible/displayed on {page}"

    Returns: List of dicts; if no pattern matches, returns dict with raw_text = full AC text.
    """
    if not ac_text or not ac_text.strip():
        return [{'raw_text': ac_text or ''}]

    points = []
    text = ac_text.strip()

    # Pattern: "{element} is removed from {page} for {condition}"
    m = re.search(
        r'(.+?)\s+is\s+removed\s+from\s+(.+?)\s+for\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        points.append({
            'element_name': m.group(1).strip(),
            'expected_state': 'absent',
            'condition': 'for %s' % m.group(3).strip(),
            'page_section': m.group(2).strip(),
            'raw_text': text,
        })
        return points

    # Pattern: "{element} should not be displayed on {page}"
    m = re.search(
        r'(.+?)\s+should\s+not\s+be\s+displayed\s+on\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        points.append({
            'element_name': m.group(1).strip(),
            'expected_state': 'absent',
            'condition': '',
            'page_section': m.group(2).strip(),
            'raw_text': text,
        })
        return points

    # Pattern: "{element} is hidden for {condition}"
    m = re.search(
        r'(.+?)\s+is\s+hidden\s+for\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        points.append({
            'element_name': m.group(1).strip(),
            'expected_state': 'absent',
            'condition': 'for %s' % m.group(2).strip(),
            'page_section': '',
            'raw_text': text,
        })
        return points

    # Pattern: "All other information remains visible"
    m = re.search(
        r'all\s+other\s+information\s+remains?\s+visible',
        text, re.IGNORECASE,
    )
    if m:
        points.append({
            'element_name': 'All other information',
            'expected_state': 'present',
            'condition': '',
            'page_section': '',
            'raw_text': text,
        })
        return points

    # Pattern: "{element} is visible/displayed on {page}"
    m = re.search(
        r'(.+?)\s+is\s+(?:visible|displayed)\s+on\s+(.+)',
        text, re.IGNORECASE,
    )
    if m:
        points.append({
            'element_name': m.group(1).strip(),
            'expected_state': 'present',
            'condition': '',
            'page_section': m.group(2).strip(),
            'raw_text': text,
        })
        return points

    # No pattern matched — return raw_text fallback (never empty)
    return [{'raw_text': text}]


def _validate_step_quality(
    steps: List[TestStep],
    scenario_title: str,
    subtask_ac_text: str = '',
) -> List[TestStep]:
    """Validate and fix step quality — reject generic patterns.

    Generic patterns rejected:
      - "Login to NBOP portal" (without navigation detail)
      - "Navigate to [page]" (without specific path)
      - "Verify [scenario title]" (without element/condition)

    When a generic step is found:
      1. Attempt enrichment from scenario_title and subtask_ac_text
      2. Fallback: use full scenario title + validation text

    Also validates structural completeness:
      - At least 1 Navigation_Step (contains "navigate"/"launch"/"login")
      - At least 1 Action_Step (contains "click"/"select"/"search"/"enter")
      - At least 1 Verification_Step (contains "verify")
      - Total: 4–8 steps (trim if >8, pad with context steps if <4)

    Returns: Validated/enriched step list.
    """
    if not steps:
        steps = []

    # ── Detect and replace generic steps ──
    enriched_steps = []
    for step in steps:
        summary_lower = step.summary.lower()

        # Generic login without navigation detail
        if re.match(r'^login\s+to\s+nbop\s+portal\s*$', summary_lower):
            enriched_steps.append(TestStep(
                step_num=step.step_num,
                summary='Launch NBOP portal and login with valid credentials',
                expected='User authenticated, subscriber search available',
                data_reference=step.data_reference,
            ))
            continue

        # Generic "Navigate to [page]" without specific path
        if re.match(r'^navigate\s+to\s+\[?\w+\]?\s*$', summary_lower):
            # Attempt enrichment from scenario title
            nav_target = scenario_title[:70] if scenario_title else 'target page'
            enriched_steps.append(TestStep(
                step_num=step.step_num,
                summary='Navigate to %s' % nav_target,
                expected='Page loads with expected content',
                data_reference=step.data_reference,
            ))
            continue

        # Generic "Verify [scenario title]" without element/condition
        if re.match(r'^verify(\s+.{0,10})?$', summary_lower) or summary_lower == 'verify scenario':
            # Attempt enrichment from AC text
            if subtask_ac_text:
                enriched_steps.append(TestStep(
                    step_num=step.step_num,
                    summary='Verify: %s' % subtask_ac_text[:80],
                    expected='Verification condition met per AC',
                    data_reference=step.data_reference,
                ))
            else:
                enriched_steps.append(TestStep(
                    step_num=step.step_num,
                    summary='Verify: %s' % scenario_title[:80],
                    expected='Expected behavior confirmed per scenario',
                    data_reference=step.data_reference,
                ))
            continue

        # Step is specific enough — keep as-is
        enriched_steps.append(step)

    # ── Validate structural completeness ──
    has_nav = any(
        any(kw in s.summary.lower() for kw in ('navigate', 'launch', 'login'))
        for s in enriched_steps
    )
    has_action = any(
        any(kw in s.summary.lower() for kw in ('click', 'select', 'search', 'enter'))
        for s in enriched_steps
    )
    has_verify = any('verify' in s.summary.lower() for s in enriched_steps)

    # Pad missing step types
    pad_steps = []
    if not has_nav:
        pad_steps.append(TestStep(
            step_num=0,
            summary='Launch NBOP portal and search subscriber by MDN',
            expected='Subscriber profile loaded successfully',
            data_reference='Navigation step',
        ))
    if not has_action:
        pad_steps.append(TestStep(
            step_num=0,
            summary='Select relevant option and search for transaction',
            expected='Search results displayed',
            data_reference='Action step from: %s' % scenario_title[:40],
        ))
    if not has_verify:
        verify_text = subtask_ac_text[:80] if subtask_ac_text else scenario_title[:80]
        # Clean up verify text to avoid "Verify: Phone — Verify ..." double patterns
        # Strip product prefix (e.g., "Phone — ", "Tablet — ")
        if ' — ' in verify_text:
            verify_text = verify_text.split(' — ', 1)[1].strip()
        # Strip leading "Verify" / "Verify that" to avoid "Verify: Verify ..."
        import re as _re_pad
        verify_text = _re_pad.sub(r'^(?:Verify\s+(?:that\s+)?)', '', verify_text).strip()
        if not verify_text:
            verify_text = scenario_title[:80]
        pad_steps.append(TestStep(
            step_num=0,
            summary='Verify: %s' % verify_text,
            expected='Verification condition met',
            data_reference='Verification step',
        ))

    # Insert pad steps at appropriate positions
    if pad_steps:
        # Nav steps go first, action in middle, verify at end
        nav_pads = [s for s in pad_steps if any(kw in s.summary.lower() for kw in ('launch', 'navigate', 'login'))]
        action_pads = [s for s in pad_steps if any(kw in s.summary.lower() for kw in ('click', 'select', 'search', 'enter'))]
        verify_pads = [s for s in pad_steps if 'verify' in s.summary.lower()]

        enriched_steps = nav_pads + enriched_steps + action_pads + verify_pads

    # ── Enforce step count bounds: 4–15 ──
    # Allow up to 15 steps for evidence-based TCs with explicit verification points.
    # Evidence TCs often verify multiple attributes across multiple screens.
    max_steps = 15
    if len(enriched_steps) > max_steps:
        enriched_steps = enriched_steps[:max_steps]

    while len(enriched_steps) < 4:
        # Pad with context steps
        enriched_steps.append(TestStep(
            step_num=0,
            summary='Verify page displays all expected information for: %s' % scenario_title[:50],
            expected='All relevant data visible on screen',
            data_reference='Context padding step',
        ))

    # ── Renumber steps ──
    for i, step in enumerate(enriched_steps):
        step.step_num = i + 1

    return enriched_steps


def _build_ui_tc_summary_name(title: str, feature_name: str, tc_num: int) -> str:
    """Build a clean, intent-focused TC summary name for UI test cases.

    Transforms raw Chalk/AC text into a proper naming convention:
      NBOP_{Product}_{Action/Verification}_{Element}_{Condition}

    Examples:
      - "Initiate a TMO PortIn activation that doesn't succeed..." → "NBOP_Verify_PortIn_TMO_Port_Status_Removed"
      - "Port Status (Syniverse) is removed from History Details..." → "NBOP_Verify_Port_Status_Syniverse_Removed_TMO"
      - "Phone — Verify attributes removed: Total MNO Usage..." → "NBOP_Phone_Verify_Attributes_Removed_TMO"
      - "There are no changes for VZW subscribers" → "NBOP_Verify_No_Changes_VZW"
    """
    text = title.strip()
    text_lower = text.lower()

    # ── Extract product prefix from crossed scenarios (e.g., "Phone — ...")
    product_prefix = ''
    if ' — ' in text:
        parts_split = text.split(' — ', 1)
        product_prefix = parts_split[0].strip()
        text = parts_split[1].strip()
        text_lower = text.lower()
    elif ' - ' in text and text.split(' - ', 1)[0].strip().lower() in ('phone', 'tablet', 'smartwatch', 'wearable', 'hotspot'):
        parts_split = text.split(' - ', 1)
        product_prefix = parts_split[0].strip()
        text = parts_split[1].strip()
        text_lower = text.lower()

    # Extract MNO from title
    mno = ''
    if 'tmo' in text_lower:
        mno = 'TMO'
    elif 'vzw' in text_lower:
        mno = 'VZW'

    # Determine the action/intent
    if any(kw in text_lower for kw in ['removed', 'hidden', 'hide', 'not displayed']):
        # Element removal scenario
        # Try to extract element name — skip leading "Verify/Verify that" before matching
        element = ''
        import re as _re
        # Strip leading "Verify (that) " before extracting element
        clean_text = _re.sub(r'^(?:verify\s+(?:that\s+)?(?:the\s+)?(?:following\s+)?)', '', text, flags=_re.IGNORECASE).strip()
        m = _re.search(r'([\w\s()]+?)\s+(?:are\s+)?(?:is\s+)?removed', clean_text, _re.IGNORECASE)
        if m:
            element = m.group(1).strip()
        elif 'port status' in text_lower:
            element = 'Port_Status_Syniverse'
        elif 'attributes' in text_lower:
            element = 'Attributes'
        else:
            element = feature_name.replace(' ', '_')

        # Capitalize first letter of element
        element_clean = element.replace(' ', '_').replace('(', '').replace(')', '')[:30]
        if element_clean and element_clean[0].islower():
            element_clean = element_clean[0].upper() + element_clean[1:]

        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.extend(['Verify', element_clean, 'Removed'])
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    elif any(kw in text_lower for kw in ['remains visible', 'remains', 'other information']):
        # Preservation scenario
        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.append('Verify_Other_Info_Remains_Visible')
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    elif any(kw in text_lower for kw in ['no changes', 'no change', 'unchanged']):
        # No-change scenario
        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.append('Verify_No_Changes')
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    elif 'portin' in text_lower or 'port-in' in text_lower or 'port in' in text_lower:
        # Port-in related scenario
        status = ''
        if 'success' in text_lower:
            status = 'Success'
        elif "doesn't succeed" in text_lower or 'fail' in text_lower or 'bad' in text_lower:
            status = 'Failed'
        elif 'in progress' in text_lower:
            status = 'InProgress'

        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.append('PortIn')
        if status:
            parts.append(status)
        parts.append('Verify_Port_Status_Removed')
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    elif any(kw in text_lower for kw in ['attributes displayed', 'fields displayed', 'is displayed', 'should be displayed']):
        # Positive display verification scenario
        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.append('Verify_Attributes_Displayed')
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    else:
        # Generic: clean up the title
        # Remove common prefixes and clean
        clean = text
        for prefix in ['NBOP_', 'NBOP ', 'Verify ', 'Validate ']:
            if clean.startswith(prefix):
                clean = clean[len(prefix):]

        # Convert to underscore-separated, truncate
        clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean)
        clean = re.sub(r'\s+', '_', clean.strip())
        clean = clean[:50]

        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        if clean:
            parts.append(clean)
        if mno and mno not in clean.upper():
            parts.append(mno)
        return '_'.join(parts)


def _build_ui_scenario_tc_enriched(
    scenario: Dict,
    idx: int,
    feature_name: str,
    feature_id: str,
    nav_path: str = '',
    subtask_ac_text: str = '',
    subtask_key: str = '',
    log: Callable = print,
) -> TestCase:
    """Build an enriched UI test case from a scenario.

    Enhancement over _build_ui_scenario_tc():
      1. Uses generate_ui_steps() with scenario_title for specific steps
      2. Parses subtask AC text for verification point details
      3. Validates step quality (no generic patterns)
      4. Falls back gracefully when knowledge is unavailable

    Step generation priority:
      1. generate_ui_steps(feature_name, scenario_title=title)
         → If returns >4 steps with specific content, use directly
      2. If generate_ui_steps returns generic/short results, build enriched
         steps from AC verification points
      3. Call _validate_step_quality() on generated steps
      4. Fallback: use full scenario title as step description (never produce empty TC)
    """
    title = scenario.get('title', 'Scenario %d' % (idx + 1))
    validation = scenario.get('validation', '')
    category = scenario.get('category', 'Happy Path')

    # ── Build clean TC summary name ──
    clean_title = _build_ui_tc_summary_name(title, feature_name, idx + 1)
    summary = '%s_TC%02d_%s' % (feature_id, idx + 1, clean_title)
    description = 'UI verification: %s via NBOP portal' % title
    preconditions = '\n'.join([
        '1. Active TMO MDN available in SIT environment',
        '2. NBOP portal accessible with valid credentials',
        '3. User has appropriate role/permissions',
    ])

    steps: List[TestStep] = []

    # ── Priority 0: Use steps_hint from evidence documents (highest quality) ──
    steps_hint = scenario.get('steps_hint', [])
    if steps_hint and len(steps_hint) >= 2:
        # Check if steps_hint is verification-only (no login/navigate steps)
        has_nav = any(
            any(kw in (h or '').lower() for kw in ['login', 'log in', 'navigate', 'launch', 'click on the'])
            for h in steps_hint
        )
        all_verify = all(
            'verify' in (h or '').lower() or 'not displayed' in (h or '').lower() or 'is displayed' in (h or '').lower()
            for h in steps_hint if h and h.strip()
        )

        step_num = 0

        # If verification-only, prepend navigation steps
        if all_verify and not has_nav:
            product = scenario.get('_product', '')
            step_num += 1
            steps.append(TestStep(
                step_num=step_num,
                summary='Launch NBOP portal and search %s subscriber by MDN' % (product + ' TMO' if product else 'TMO'),
                expected='Subscriber profile loaded successfully',
                data_reference='Navigation: NBOP login',
            ))
            step_num += 1
            steps.append(TestStep(
                step_num=step_num,
                summary='Click ≡ (hamburger menu) → Click on Data Details',
                expected='Data Details screen loaded with usage information',
                data_reference='Navigation: %s' % (nav_path or 'NBOP → Data Details'),
            ))

        # Add the steps_hint content
        for hint in steps_hint:
            hint_text = hint.strip() if isinstance(hint, str) else str(hint)
            if not hint_text:
                continue
            step_num += 1
            # Determine expected result from the step text
            if 'not displayed' in hint_text.lower() or 'is not' in hint_text.lower():
                expected = 'Element is NOT visible on the page'
            elif 'is displayed' in hint_text.lower() or 'should be displayed' in hint_text.lower():
                expected = 'Element IS visible and accessible'
            elif 'verify' in hint_text.lower() or 'ensure' in hint_text.lower():
                expected = 'Condition verified: %s' % hint_text[:80]
            elif 'navigate' in hint_text.lower() or 'click' in hint_text.lower():
                expected = 'Navigation successful — target page/section loaded'
            elif 'login' in hint_text.lower() or 'log in' in hint_text.lower() or 'search' in hint_text.lower():
                expected = 'Subscriber profile loaded successfully'
            elif 'call' in hint_text.lower() and 'api' in hint_text.lower():
                expected = 'API call returns expected response'
            elif 'verify' in hint_text.lower() or 'check' in hint_text.lower() or 'validate' in hint_text.lower():
                expected = 'Verification passes — result matches expected value'
            else:
                # Use the hint text itself to derive a contextual expected result
                expected = 'Expected outcome confirmed per scenario specification'
            steps.append(TestStep(
                step_num=step_num,
                summary=hint_text[:120],
                expected=expected,
                data_reference='Evidence document',
            ))

        # If verification-only, also add Historical Usage navigation + verify
        if all_verify and not has_nav:
            step_num += 1
            steps.append(TestStep(
                step_num=step_num,
                summary='Click on View Historical Usage to navigate to Historical Usage Grid',
                expected='Historical Usage Grid loaded',
                data_reference='Navigation: Historical Usage',
            ))
            # Repeat each verification step for Historical Usage screen
            for hint in steps_hint:
                hint_text = hint.strip() if isinstance(hint, str) else str(hint)
                if not hint_text:
                    continue
                # Only repeat verification steps (not navigation/login)
                hint_lower = hint_text.lower()
                if 'verify' in hint_lower or 'not displayed' in hint_lower or 'is displayed' in hint_lower:
                    step_num += 1
                    # Append "on Historical Usage screen" context if not already present
                    if 'historical' not in hint_lower:
                        hist_summary = '%s on Historical Usage screen' % hint_text[:100]
                    else:
                        hist_summary = hint_text[:120]
                    if 'not displayed' in hint_lower or 'is not' in hint_lower:
                        expected = 'Element is NOT visible on Historical Usage screen'
                    elif 'is displayed' in hint_lower or 'should be displayed' in hint_lower:
                        expected = 'Element IS visible on Historical Usage screen'
                    else:
                        expected = 'Condition verified on Historical Usage screen'
                    steps.append(TestStep(
                        step_num=step_num,
                        summary=hist_summary,
                        expected=expected,
                        data_reference='Evidence: Historical Usage verification',
                    ))

    # ── Priority 1: Try generate_ui_steps() for specific steps ──
    # Skip if Priority 0 already produced steps (steps_hint had content)
    if not steps:
        try:
            from .nbop_ui_knowledge import generate_ui_steps, is_available
            if is_available():
                ui_steps = generate_ui_steps(feature_name, scenario_title=title)
                if ui_steps and len(ui_steps) > 4:
                    # Check if steps have specific content (not just generic placeholders)
                    has_specific = any(
                        any(kw in desc.lower() for kw in ('mdn', 'tab', 'dropdown', 'field', 'menu', 'tile', 'click', 'select'))
                        for desc, _ in ui_steps
                    )
                    if has_specific:
                        # Use directly — these are high-quality steps
                        for i, (desc, expected) in enumerate(ui_steps):
                            steps.append(TestStep(
                                step_num=i + 1,
                                summary=desc,
                                expected=expected,
                                data_reference='NBOP UI Knowledge: %s' % title[:40],
                            ))
        except (ImportError, Exception):
            pass

    # ── Priority 2: Build enriched steps from AC verification points ──
    if not steps and subtask_ac_text:
        ac_points = _parse_ac_verification_points(subtask_ac_text)
        step_num = 0

        # Navigation step
        step_num += 1
        steps.append(TestStep(
            step_num=step_num,
            summary='Launch NBOP portal and search subscriber by MDN',
            expected='Subscriber profile loaded successfully',
            data_reference='Navigation: NBOP login',
        ))

        # Navigate to relevant page
        step_num += 1
        if nav_path:
            steps.append(TestStep(
                step_num=step_num,
                summary='Navigate to %s' % nav_path,
                expected='Target page loaded successfully',
                data_reference='Navigation: %s' % nav_path,
            ))
        else:
            steps.append(TestStep(
                step_num=step_num,
                summary='Navigate to %s section in subscriber profile' % feature_name,
                expected='%s page loaded' % feature_name,
                data_reference='NBOP navigation',
            ))

        # Action step — search/filter
        step_num += 1
        steps.append(TestStep(
            step_num=step_num,
            summary='Search for subscriber transaction using Transaction Id or MDN',
            expected='Search results displayed with matching records',
            data_reference='Action: search/filter',
        ))

        # Verification steps from AC points
        for point in ac_points:
            step_num += 1
            if 'element_name' in point:
                elem = point['element_name']
                state = point['expected_state']
                condition = point.get('condition', '')
                page_section = point.get('page_section', '')

                if state == 'absent':
                    step_summary = "Verify '%s' is NOT displayed" % elem
                    if page_section:
                        step_summary += ' on %s' % page_section
                    if condition:
                        step_summary += ' %s' % condition
                    step_expected = "'%s' element is absent from the page" % elem
                elif state == 'present':
                    step_summary = "Verify '%s' IS displayed" % elem
                    if page_section:
                        step_summary += ' on %s' % page_section
                    if condition:
                        step_summary += ' %s' % condition
                    step_expected = "'%s' element is visible and accessible" % elem
                else:
                    step_summary = "Verify '%s' state is %s" % (elem, state)
                    step_expected = "'%s' is in expected state: %s" % (elem, state)

                steps.append(TestStep(
                    step_num=step_num,
                    summary=step_summary,
                    expected=step_expected,
                    data_reference='AC verification: %s' % elem,
                ))
            else:
                # raw_text fallback — use full AC text as verification
                raw = point.get('raw_text', subtask_ac_text)
                steps.append(TestStep(
                    step_num=step_num,
                    summary='Verify: %s' % raw[:80],
                    expected='Condition met: %s' % raw[:100],
                    data_reference='AC text verification',
                ))

    # ── Priority 3: Fallback — use scenario title as step description ──
    if not steps:
        steps = [
            TestStep(step_num=1,
                     summary='Launch NBOP portal and search subscriber by MDN',
                     expected='Subscriber profile loaded',
                     data_reference='Navigation'),
            TestStep(step_num=2,
                     summary='Navigate to %s' % (nav_path or feature_name),
                     expected='Page loaded successfully',
                     data_reference='Navigation'),
            TestStep(step_num=3,
                     summary='Perform action: %s' % title[:70],
                     expected='Action completed',
                     data_reference='Scenario: %s' % title[:40]),
            TestStep(step_num=4,
                     summary='Verify: %s' % (validation or title)[:80],
                     expected='Expected behavior confirmed',
                     data_reference='Scenario validation'),
        ]

    # ── Validate step quality ──
    steps = _validate_step_quality(steps, scenario_title=title, subtask_ac_text=subtask_ac_text)

    # ── Build traceability ──
    # Determine source_type based on scenario source
    source_type = scenario.get('source_type', 'Chalk Scenario')
    if subtask_key:
        source_type = 'Subtask AC'
        source_id = subtask_key
    else:
        source_id = '%s_scenario_%d' % (feature_id, idx + 1)

    tr = create_traceability(
        source_type=source_type,
        source_id=source_id,
        extracted_text=title[:200],
    )

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=category,
        traceability=tr,
        dimension_values={'channel': 'NBOP', 'scenario': title[:50]},
    )


def _build_subtask_context(deep_mine_result) -> Dict[str, str]:
    """Extract all UI/NBOP subtask AC items into a lookup.

    Returns: {normalized_scenario_title: ac_text}
    Filters by component containing "UI" or "NBOP".
    """
    context: Dict[str, str] = {}
    if not deep_mine_result:
        return context

    subtask_mines = getattr(deep_mine_result, 'subtask_mines', []) or []
    for mine in subtask_mines:
        component = getattr(mine, 'component', '') or ''
        if not any(kw in component.upper() for kw in ('UI', 'NBOP')):
            continue
        ac_items = getattr(mine, 'ac_items', []) or []
        for ac_item in ac_items:
            if not ac_item or not ac_item.strip():
                continue
            # Normalize: lowercase, collapse whitespace, strip punctuation
            normalized = re.sub(r'[^\w\s]', '', ac_item.lower())
            normalized = re.sub(r'\s+', ' ', normalized).strip()
            if normalized:
                context[normalized] = ac_item.strip()
    return context


def _get_subtask_ac_for_scenario(scenario, subtask_context: Dict) -> str:
    """Match a scenario to its subtask AC text by normalized title comparison.

    Returns the AC text if found, empty string otherwise.
    """
    if not subtask_context:
        return ''

    title = getattr(scenario, 'title', '') or ''
    if not title:
        return ''

    # Normalize scenario title the same way
    normalized_title = re.sub(r'[^\w\s]', '', title.lower())
    normalized_title = re.sub(r'\s+', ' ', normalized_title).strip()

    # Exact match
    if normalized_title in subtask_context:
        return subtask_context[normalized_title]

    # Partial match — check if scenario title is a substring of any AC item or vice versa
    for norm_key, ac_text in subtask_context.items():
        if normalized_title in norm_key or norm_key in normalized_title:
            return ac_text

    # Token overlap match (>80%)
    title_tokens = set(normalized_title.split())
    if not title_tokens:
        return ''

    for norm_key, ac_text in subtask_context.items():
        key_tokens = set(norm_key.split())
        if not key_tokens:
            continue
        overlap = len(title_tokens & key_tokens) / max(len(title_tokens), len(key_tokens))
        if overlap > 0.8:
            return ac_text

    return ''


def _get_subtask_key_for_scenario(scenario) -> str:
    """Return the subtask Jira key from the scenario's traceability source_id.

    Looks for a source attribute on the scenario that contains the subtask key.
    """
    # Check if scenario has a source/traceability with subtask key
    source = getattr(scenario, 'source', None)
    if source:
        source_id = getattr(source, 'source_id', '') or ''
        source_type = getattr(source, 'source_type', '') or ''
        if source_type == 'Subtask AC' and source_id:
            return source_id

    # Check for _subtask_key attribute (set during aggregation)
    subtask_key = getattr(scenario, '_subtask_key', '') or ''
    if subtask_key:
        return subtask_key

    return ''
