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
from .step_templates import truncate_at_word as _trim_title
from .data_models_v8 import (
    Dimension, ExtractedScenario, NegativeSpec, CombinationPlan,
    TestStep, TestCase,
)
from .integration_contract import (
    resolve_operation, build_downstream_verification_steps,
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


_DEGENERATE_TITLE_WORDS = {'verify', 'validate', 'check', 'test', 'confirm', 'ensure',
                           'the', 'a', 'an', 'that', 'this', 'it', 'is', 'are', 'to', 'of'}


def _is_degenerate_title(title: str) -> bool:
    """True when a scenario title carries no real content — e.g. "Verify", "Verify Verify",
    "Validate the" — so we don't emit stub steps like 'Verify expected result: Verify' or
    degenerate TCs named 'Verify_Verify'. A real title must have >=1 meaningful (non-filler) word."""
    t = re.sub(r'[\s_]+', ' ', (title or '').strip().lower())
    if len(t) < 6:
        return True
    meaningful = [w for w in t.split() if w not in _DEGENERATE_TITLE_WORDS]
    return len(meaningful) == 0


def _is_backend_requirement_heading(title: str) -> bool:
    """Return True for a non-testable source heading that introduces later requirements."""
    normalized = re.sub(r'\s+', ' ', (title or '').strip()).rstrip(':.').lower()
    return normalized == 'update the backend to support the following requirements'


def _provider_tokens(text: str) -> set:
    """Return normalized provider names explicitly mentioned in text."""
    providers = set()
    if re.search(r'\b(?:tmo|t-mobile|tmobile)\b', text or '', re.IGNORECASE):
        providers.add('TMO')
    if re.search(r'\b(?:vzw|vz|verizon)\b', text or '', re.IGNORECASE):
        providers.add('VZW')
    return providers


def _has_explicit_dual_provider_scope(text: str) -> bool:
    """True only when the requirement executes or directly compares both providers."""
    normalized = re.sub(r'\s+', ' ', text or '').lower()
    tmo = r'(?:tmo|t-mobile|tmobile)'
    vzw = r'(?:vzw|vz|verizon)'
    dual_patterns = (
        r'\bboth\s+(?:%s\s+(?:and|/)\s+%s|%s\s+(?:and|/)\s+%s)\b' % (tmo, vzw, vzw, tmo),
        r'\b(?:%s\s+apis?\s*(?:and|/)\s*%s\s+apis?|%s\s+apis?\s*(?:and|/)\s*%s\s+apis?)\b' % (tmo, vzw, vzw, tmo),
        r'\b(?:%s\s+(?:and|/)\s+%s|%s\s+(?:and|/)\s+%s)\s+(?:subscribers?|lines?|mdns?|routes?|apis?|requests?|transactions?)\b' % (tmo, vzw, vzw, tmo),
        r'\bcompare\b.{0,80}\b%s\b.{0,40}\b(?:with|to|and|versus|vs\.?)\b.{0,40}\b%s\b' % (tmo, vzw),
        r'\bcompare\b.{0,80}\b%s\b.{0,40}\b(?:with|to|and|versus|vs\.?)\b.{0,40}\b%s\b' % (vzw, tmo),
        r'\bone\s+(?:active\s+)?%s\b.{0,60}\bone\s+(?:active\s+)?%s\b' % (tmo, vzw),
        r'\bone\s+(?:active\s+)?%s\b.{0,60}\bone\s+(?:active\s+)?%s\b' % (vzw, tmo),
    )
    return any(re.search(pattern, normalized) for pattern in dual_patterns)


def _infer_network_provider(title: str, validation: str = '', context: str = '') -> str:
    """Infer the provider that the scenario executes, not providers used as references.

    MIXED is reserved for requirements that explicitly execute or compare both routes.
    Feature/subtask context is only a fallback, so a provider-specific scenario is not
    widened by a parent requirement that describes both providers.
    """
    primary = re.sub(r'\s+', ' ', '%s %s' % (title or '', validation or '')).strip()
    fallback = re.sub(r'\s+', ' ', context or '').strip()

    # Regression/impact wording names the provider whose behavior is protected.
    # Resolve that operational subject before considering incidental mentions of
    # both providers elsewhere in the validation text.
    provider_token = r'(tmo|t-mobile|tmobile|vzw|vz|verizon)'
    impact_match = re.search(
        r'\b(?:does\s+not|must\s+not|should\s+not|without)\s+'
        r'(?:impact(?:ing)?|affect(?:ing)?|alter(?:ing)?|chang(?:e|ing))\b.{0,80}?'
        r'\b%s\b' % provider_token,
        primary, re.IGNORECASE)
    if not impact_match:
        impact_match = re.search(
            r'\b%s\b.{0,60}\b(?:is|remains?)\s+(?:not\s+)?'
            r'(?:impacted|affected|altered|changed|unchanged)\b' % provider_token,
            primary, re.IGNORECASE)
    if impact_match:
        provider_name = impact_match.group(1).lower()
        return 'TMO' if provider_name in ('tmo', 't-mobile', 'tmobile') else 'VZW'

    if _has_explicit_dual_provider_scope(primary):
        return 'MIXED'

    preceding_regression_match = re.search(
        r'\b(tmo|t-mobile|tmobile|vzw|vz|verizon)\b.{0,50}'
        r'\b(?:regression|unchanged|not\s+affected|existing\s+behavior)\b',
        primary, re.IGNORECASE)
    if preceding_regression_match:
        return ('TMO' if preceding_regression_match.group(1).lower() in
                ('tmo', 't-mobile', 'tmobile') else 'VZW')

    unchanged_match = re.search(
        r'\b(?:regression|unchanged|no\s+changes?|retain(?:s|ed)?|existing\s+behavior)\b.{0,60}'
        r'\b(?:for\s+)?(tmo|t-mobile|tmobile|vzw|vz|verizon)\b',
        primary, re.IGNORECASE)
    if unchanged_match:
        return 'TMO' if unchanged_match.group(1).lower() in ('tmo', 't-mobile', 'tmobile') else 'VZW'

    # Remove parity/reference clauses before looking for the operational subject.
    reference_pattern = (
        r'\b(?:same\s+(?:process|flow|way|behavior|experience)?\s*as|parity\s+with|'
        r'equivalent\s+to|similar\s+to|like)\s+(?:for\s+)?'
        r'(?:tmo|t-mobile|tmobile|vzw|vz|verizon)(?:\s+subscribers?)?\b'
    )
    reference_provider_match = re.search(
        r'\b(?:same\s+(?:process|flow|way|behavior|experience)?\s*as|parity\s+with|'
        r'equivalent\s+to|similar\s+to|like)\s+(?:for\s+)?'
        r'(tmo|t-mobile|tmobile|vzw|vz|verizon)(?:\s+subscribers?)?\b',
        primary, re.IGNORECASE)
    operational_text = re.sub(reference_pattern, ' ', primary, flags=re.IGNORECASE)

    explicit_value = re.search(
        r'\b(?:network\s*provider|networkprovider|request\s*type|requesttype)\s*[:=]\s*'
        r'(tmo|t-mobile|tmobile|vzw|vz|verizon)\b', operational_text, re.IGNORECASE)
    if explicit_value:
        return 'TMO' if explicit_value.group(1).lower() in ('tmo', 't-mobile', 'tmobile') else 'VZW'

    scores = {'TMO': 0, 'VZW': 0}
    provider_patterns = {
        'TMO': r'(?:tmo|t-mobile|tmobile)',
        'VZW': r'(?:vzw|vz|verizon)',
    }
    subject_nouns = r'(?:subscriber|line|mdn|activation|request|route|api|endpoint|transaction)'
    for provider, token in provider_patterns.items():
        scores[provider] += 4 * len(re.findall(
            r'\b%s\b(?:[-\s]+(?:only|specific))?\s+%ss?\b' % (token, subject_nouns),
            operational_text, re.IGNORECASE))
        scores[provider] += 3 * len(re.findall(
            r'\b(?:for|using|with|on)\s+(?:an?\s+)?%s\b' % token,
            operational_text, re.IGNORECASE))
        scores[provider] += 2 * len(re.findall(
            r'\b%s[-\s]+only\b' % token, operational_text, re.IGNORECASE))

    remaining = _provider_tokens(operational_text)
    if len(remaining) == 1:
        return next(iter(remaining))
    if scores['TMO'] != scores['VZW']:
        return max(scores, key=scores.get)
    if not remaining and reference_provider_match:
        reference_provider = reference_provider_match.group(1).lower()
        return 'VZW' if reference_provider in ('tmo', 't-mobile', 'tmobile') else 'TMO'

    # Context supplies an implicit subject for parity wording such as "same way as
    # VZW"; it never overrides an explicit scenario subject above.
    if _has_explicit_dual_provider_scope(fallback):
        return 'MIXED'
    fallback_operational = re.sub(reference_pattern, ' ', fallback, flags=re.IGNORECASE)
    fallback_providers = _provider_tokens(fallback_operational)
    if len(fallback_providers) == 1:
        return next(iter(fallback_providers))
    return 'TMO'


def _classify_ui_scenario_intent(title: str, validation: str = '') -> str:
    """Identify strong backend evidence needs for one scenario inside a UI feature.

    A bare ``networkProvider`` mention is deliberately insufficient. Routing requires
    an explicit provider plus API/route/endpoint behavior, but it may target one
    provider or both.
    """
    text = re.sub(r'\s+', ' ', '%s %s' % (title or '', validation or '')).lower()
    has_provider = bool(_provider_tokens(text))

    if 'cpc' in text and any(term in text for term in (
            'api', 'wholesale plan', 'wholesaleplan', 'response')):
        return 'cpc_wholesale_response'
    if 'network' in text and 'syniverse' in text and any(term in text for term in (
            'compare', 'comparison', 'match', 'versus', ' vs ', 'difference')):
        return 'network_syniverse_comparison'
    if 'npanxx' in text and any(term in text for term in (
            'payload', 'backend', 'dependency', 'request field', 'outbound request',
            'request body', 'in the request', 'from the request', 'not send', 'not sent')):
        return 'npanxx_backend_dependency'
    if (has_provider and
            ('networkprovider' in text or 'network provider' in text) and
            any(term in text for term in ('route', 'routing', 'api', 'endpoint', 'based on'))):
        return 'provider_api_routing'
    return 'ui'


def _build_ui_description(title: str, validation: str, scenario_intent: str) -> str:
    """Build an untruncated, clean UI or hybrid description from full validation."""
    detail = re.sub(r'\s+', ' ', (validation or title or '').strip())
    detail = re.sub(r'^(?:UI verification|Hybrid UI/API verification)\s*:\s*', '', detail,
                    flags=re.IGNORECASE)
    detail = detail.rstrip()
    prefix = 'Hybrid UI/API verification' if scenario_intent != 'ui' else 'UI verification'
    return '%s: %s' % (prefix, detail) if detail else prefix


def _build_ui_preconditions(title: str, validation: str, provider: str,
                            scenario_intent: str) -> str:
    """Build provider- and scenario-aware UI prerequisites."""
    text = ('%s %s' % (title or '', validation or '')).lower()
    items = []
    is_new_activation = (
        any(term in text for term in ('activation', 'activate')) and
        not any(term in text for term in ('activation history', 'port-in activation', 'port in activation'))
    )
    if is_new_activation:
        if provider == 'MIXED':
            items.append('Eligible TMO and VZW activation test data are available in SIT with provider-specific RequestType semantics')
        else:
            items.append('Eligible %s activation test data are available in SIT with RequestType=%s semantics' % (provider, provider))
    elif provider == 'MIXED':
        items.append('Active TMO and VZW MDNs are available in SIT with RequestType=TMO and RequestType=VZW semantics')
    elif provider == 'VZW':
        items.append('Active VZW MDN is available in SIT with RequestType=VZW semantics')
    else:
        items.append('Active TMO MDN is available in SIT with RequestType=TMO semantics')
    items.extend([
        'NBOP portal is accessible with valid credentials',
        'User has permission to perform the scenario operation and view audit details',
    ])
    if 'port' in text and ('in progress' in text or 'in-progress' in text):
        items.append('A Port-In Activation record exists in IN PROGRESS status')
    if 'conflict' in text and 'feature' in text:
        items.append('Subscriber has a known conflicting feature combination and its current feature state is captured')
    if scenario_intent == 'cpc_wholesale_response':
        items.append('CPC request/response logging is available and the subscriber has a known wholesale-plan response')
    if scenario_intent == 'network_syniverse_comparison':
        items.append('Network and Syniverse response evidence plus Line Summary/Century Report access are available')
    if scenario_intent == 'npanxx_backend_dependency':
        items.append('ZIP-code test data and backend request tracing are available; NPANXX resolution is configured server-side')
    return '\n'.join('%d. %s' % (idx, item) for idx, item in enumerate(items, 1))


def _build_hybrid_ui_backend_steps(scenario_intent: str, provider: str,
                                   validation: str) -> List[TestStep]:
    """Build execution-ready UI-trigger plus backend-evidence chains."""
    provider_label = 'TMO and VZW' if provider == 'MIXED' else provider
    chains = {
        'cpc_wholesale_response': [
            ('Launch NBOP and search the %s subscriber by MDN' % provider_label,
             'Subscriber profile loads with the expected provider context'),
            ('Open ≡ Menu → Manage Line → Change Features',
             'Change Features screen loads and requests current plan/feature data'),
            ('Capture the CPC API request and response generated by the NBOP screen',
             'CPC evidence contains the subscriber identifier, successful status, and wholesale-plan response'),
            ('Verify the CPC response returns the expected Wholesale Plan and optional-feature data',
             'Wholesale Plan and eligible optional features match the configured CPC response'),
            ('Verify NBOP displays the CPC-backed Wholesale Plan and feature options',
             validation or 'NBOP values match the captured CPC response'),
            ('Open Line Summary(MNO) or the Century Report and verify the same plan context',
             'Backend evidence and NBOP display are consistent for the subscriber'),
        ],
        'provider_api_routing': (
            [
                ('Launch NBOP and search one active TMO MDN and one active VZW MDN',
                 'Both subscriber profiles load with the correct provider identity'),
                ('Perform the same NBOP operation for the TMO subscriber and capture the outbound API request/response',
                 'The request uses TMO semantics and reaches the configured TMO provider route'),
                ('Perform the same NBOP operation for the VZW subscriber and capture the outbound API request/response',
                 'The request uses VZW semantics and reaches the configured VZW provider route'),
                ('Verify networkProvider selects the correct provider API for each request',
                 'TMO traffic is not sent to VZW and VZW traffic is not sent to TMO'),
                ('Verify each provider response is rendered on the corresponding NBOP subscriber screen',
                 validation or 'NBOP displays the correct result for both provider routes'),
                ('Open Transaction History and compare both Transaction IDs with the captured routes',
                 'Each transaction records the correct MNO/provider and completed response'),
            ] if provider == 'MIXED' else [
                ('Launch NBOP and search an active %s subscriber by MDN' % provider,
                 '%s subscriber profile loads with the correct provider identity' % provider),
                ('Perform the required NBOP operation and capture its outbound API request and response',
                 'The request uses %s semantics and reaches the configured %s provider route' % (provider, provider)),
                ('Verify networkProvider selects the %s API route and endpoint' % provider,
                 'The request is not sent to a different provider route'),
                ('Verify the %s provider response is rendered on the NBOP subscriber screen' % provider,
                 validation or 'NBOP displays the result returned by the %s provider route' % provider),
                ('Open ≡ Menu → Transaction History and locate the operation Transaction ID',
                 'The matching transaction is displayed for the %s subscriber' % provider),
                ('Click the Transaction ID and verify its MNO/provider and captured route',
                 'Transaction details record %s and match the provider API evidence' % provider),
            ]
        ),
        'npanxx_backend_dependency': [
            ('Launch NBOP and search the %s subscriber by MDN' % provider_label,
             'Subscriber profile loads with the expected provider context'),
            ('Trigger the activation or Change MDN operation using the required ZIP code only',
             'NBOP accepts ZIP code input without requesting NPANXX'),
            ('Capture the NBOP backend request payload before it is sent downstream',
             'Request evidence is available for field-level inspection'),
            ('Verify NPANXX is absent from the request payload and no UI-supplied NPANXX dependency remains',
             'Payload contains the supported ZIP/provider fields and does not contain NPANXX'),
            ('Verify the backend resolves routing data and the operation response succeeds',
             validation or 'Backend processing completes without an NPANXX request field'),
            ('Open Transaction History and the Century Report or Line Summary(MNO)',
             'The completed transaction and resolved provider details match the backend response'),
        ],
        'network_syniverse_comparison': [
            ('Launch NBOP and search the %s subscriber by MDN' % provider_label,
             'Subscriber profile loads with current network information'),
            ('Open Line Summary(MNO) and capture the Network response for the subscriber',
             'Network provider/status evidence is available'),
            ('Capture the corresponding Syniverse response for the same MDN and transaction context',
             'Syniverse provider/status evidence is available'),
            ('Compare Network and Syniverse provider, status, and routing values field by field',
             'Differences are identified without substituting one source for the other'),
            ('Verify NBOP displays the source/value required by the scenario',
             validation or 'NBOP display agrees with the required Network-versus-Syniverse rule'),
            ('Open the Century Report or Transaction History and verify the comparison evidence is traceable',
             'Transaction evidence identifies the provider values used by the UI'),
        ],
    }
    return [TestStep(step_num=idx, summary=summary, expected=expected,
                     data_reference='Hybrid UI/API evidence')
            for idx, (summary, expected) in enumerate(chains.get(scenario_intent, []), 1)]


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

    # Only run legacy intent classifier when primary classification has low confidence
    if classification.confidence >= 0.7:
        feature_intent = {'channels': [jira.channel] if jira and hasattr(jira, 'channel') and jira.channel else ['ITMBO']}
        log('[TC-BUILD]   High-confidence classification — skipping legacy intent classifier')
    else:
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
        # ── GATE: Do NOT generate synchronous API dimension TCs for notification-driven features
        # when no real endpoint was found. De-prioritization, DPFO, throttle features are
        # triggered by Mediation notifications, not by a direct POST/GET API call.
        _has_real_endpoint = bool(api_context.get('endpoint', '').strip())
        _feature_lower = (feature_name or '').lower()
        _is_notification_driven = any(kw in _feature_lower for kw in [
            'de-priorit', 'deprioritiz', 'throttle', 'dpfo', 'nc_deprior',
            'notification', 'suppress', 'usage',
        ])
        _skip_dimension_tcs = _is_notification_driven and not _has_real_endpoint

        if _skip_dimension_tcs:
            log('[TC-BUILD]   GATE: Notification-driven feature with no endpoint — skipping dimension API TCs')
        else:
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
            if _is_backend_requirement_heading(scenario.title):
                log('[TC-BUILD]   Skipping non-testable backend requirement heading: %s' % scenario.title)
                # Preserve the source ordinal gap without borrowing partial
                # _source_tc_num metadata from only some scenario sources.
                tc_idx += 1
                continue

            subtask_ac = _get_subtask_ac_for_scenario(scenario, subtask_context)
            provider_context = ' '.join(filter(None, (
                feature_name, jira_summary, ac_text, subtask_ac)))
            scenario_intent = _classify_ui_scenario_intent(scenario.title, scenario.validation)
            network_provider = _infer_network_provider(
                scenario.title, scenario.validation, provider_context)

            # Source systems often label provider-impact checks as Happy Path even when
            # the requirement explicitly protects existing behavior. Preserve the
            # execution provider while classifying those checks as Regression so they
            # remain distinct from the new-provider implementation flow.
            scenario_text = '%s %s' % (scenario.title or '', scenario.validation or '')
            scenario_text_lower = scenario_text.lower()
            effective_category = scenario.category
            is_provider_regression = (
                network_provider in ('TMO', 'VZW') and
                any(term in scenario_text_lower for term in (
                    'does not affect', 'must not affect', 'should not affect',
                    'no cross-network impact', 'no change', 'unchanged', 'regression')))
            if (is_provider_regression and
                    (not effective_category or effective_category == 'Happy Path')):
                effective_category = 'Regression'

            scenario_dict = {
                'title': scenario.title,
                'validation': scenario.validation,
                'category': effective_category,
                'steps_hint': scenario.steps_hint,
                '_scenario_intent': scenario_intent,
                '_network_provider': network_provider,
            }

            # Determine if this scenario should be crossed by product.
            # Backend/hybrid evidence scenarios stay one-to-one with their source requirement.
            title_lower = (scenario.title or '').lower()
            is_regression = effective_category == 'Regression' or 'no change' in title_lower or 'verizon' in title_lower or 'vzw' in title_lower
            is_evidence = title_lower.startswith('evidence:') or 'log in to nbop' in title_lower
            is_positive_verify = 'attributes displayed' in title_lower or 'is displayed' in title_lower or 'should be displayed' in title_lower
            should_cross = (product_values and scenario_intent == 'ui' and
                            not is_regression and not is_evidence and not is_positive_verify)

            if should_cross:
                for product in product_values:
                    crossed_dict = dict(scenario_dict)
                    crossed_dict['title'] = '%s — %s' % (product, scenario.title)
                    crossed_dict['_product'] = product
                    tc = _build_ui_scenario_tc_enriched(
                        crossed_dict, tc_idx, feature_name, feature_id, nav_path,
                        subtask_ac_text=subtask_ac,
                        subtask_key=_get_subtask_key_for_scenario(scenario),
                        log=log,
                    )
                    tc.user_requested = getattr(scenario, 'user_requested', False)
                    test_cases.append(tc)
                    tc_idx += 1
            else:
                tc = _build_ui_scenario_tc_enriched(
                    scenario_dict, tc_idx, feature_name, feature_id, nav_path,
                    subtask_ac_text=subtask_ac,
                    subtask_key=_get_subtask_key_for_scenario(scenario),
                    log=log,
                )
                tc.user_requested = getattr(scenario, 'user_requested', False)
                test_cases.append(tc)
                tc_idx += 1

        log('[TC-BUILD]   Built %d UI scenario TCs (product crossing applied where appropriate)' % tc_idx)
    else:
        # API/hybrid path: standard scenario TC building
        built_scenario_count = 0
        for scenario in plan.scenario_tcs:
            if _is_backend_requirement_heading(scenario.title):
                log('[TC-BUILD]   Skipping non-testable backend requirement heading: %s' % scenario.title)
                continue
            tc = _build_scenario_tc(scenario, jira, feature_name, nbop_knowledge, api_context, feature_intent,
                                    feature_type=classification.classification)
            if not getattr(tc, 'user_requested', False):
                tc.user_requested = getattr(scenario, 'user_requested', False)
            test_cases.append(tc)
            built_scenario_count += 1
        log('[TC-BUILD]   Built %d scenario TCs' % built_scenario_count)

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
    _assign_serial_numbers(test_cases, feature_priority=getattr(jira, 'priority', '') if jira else '')

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
    feature_type: str = '',
) -> TestCase:
    """Build a TC from an ExtractedScenario with steps from hints and api_spec."""
    feature_id = jira.key if jira else ''
    api_context = api_context or {}

    # Build steps from scenario hints or api_spec
    steps = []
    if scenario.steps_hint:
        _is_negative = scenario.category == 'Negative'
        _rejection_msg = (scenario.validation or 'Operation rejected with appropriate error')
        _success_msg = (scenario.validation or 'Operation completes successfully')
        # Ensure expected results end with punctuation
        if _rejection_msg and not _rejection_msg.rstrip()[-1] in '.!?)':
            _rejection_msg = _rejection_msg.rstrip(',;: ') + '.'
        if _success_msg and not _success_msg.rstrip()[-1] in '.!?)':
            _success_msg = _success_msg.rstrip(',;: ') + '.'

        for i, hint in enumerate(scenario.steps_hint, 1):
            # Derive step-appropriate expected result from the hint text
            step_low = hint.lower()
            _is_last_step = (i == len(scenario.steps_hint))
            _val = scenario.validation or ''
            _val_is_header = (
                'scenario #' in _val.lower()
                or _val.lower().startswith(('test scenario', 'validation', 'negative scenarios',
                                            'edge scenarios', 'positive scenarios', 'regression scenarios'))
            )

            if any(kw in step_low for kw in ['set up', 'precondition', 'prepare', 'configure']):
                # Setup steps: expected = state is ready
                if 'state in sit' in step_low or 'environment' in step_low:
                    _state = hint.split('in SIT')[0].strip().split('Set up subscriber line in ')[-1].strip()
                    _exp = 'Subscriber line is in the required state and ready for testing'
                else:
                    _exp = 'Test environment configured and ready'
            elif any(kw in step_low for kw in ['trigger', 'invoke', 'submit']):
                # Trigger step (avoid matching 'call' inside words like 'specifically', 'recall')
                _exp = 'Request submitted to API endpoint' if _is_negative else 'API accepts the request and responds successfully'
            elif re.search(r'\b(call)\b', step_low):
                # 'call' as a standalone word (API call, make a call)
                _exp = 'Request submitted to API endpoint' if _is_negative else 'API accepts the request and responds successfully'
            elif any(kw in step_low for kw in ['send post', 'send get', 'send %s' % (api_context.get('method','') or 'post').lower()]):
                _exp = 'Request submitted to API endpoint' if _is_negative else 'API returns HTTP 200/202 with success response'
            elif any(kw in step_low for kw in ['verify operation rejected', 'verify.*rejected', 'verify.*error', 'rejected']):
                # Verify rejection — specific expected based on hint
                _exp = _rejection_msg if not _val_is_header else 'Operation rejected with appropriate error code'
            elif any(kw in step_low for kw in ['verify operation completes', 'verify.*completes', 'verify.*succeed', 'verify downstream', 'verify.*updated', 'verify.*consistent']):
                # Verify success — derive from the step hint itself
                _exp = hint.replace('Verify ', '').replace('Verify: ', '').strip()
                if not _exp.endswith('.'):
                    _exp += ' — confirmed.'
            elif any(kw in step_low for kw in ['verify', 'validate', 'check', 'confirm']):
                # Generic verify — derive expected from the hint text (not raw AC)
                _hint_as_expected = hint.replace('Verify ', '').replace('Verify: ', '').replace('Validate ', '').strip()
                if _hint_as_expected and len(_hint_as_expected) > 10:
                    _exp = _hint_as_expected if _hint_as_expected.endswith('.') else _hint_as_expected + '.'
                elif _is_negative:
                    _exp = _rejection_msg if not _val_is_header else 'System rejects as expected with appropriate error'
                else:
                    _exp = _success_msg if not _val_is_header else 'Verification passes as expected'
            elif any(kw in step_low for kw in ['verify line remains', 'no state change', 'unchanged']):
                _exp = 'Line remains in original state — no state transition occurred'
            elif any(kw in step_low for kw in ['login', 'navigate', 'open', 'launch']):
                _exp = 'Portal/screen loads successfully and is ready for input'
            elif any(kw in step_low for kw in ['view', 'display', 'observe']):
                _exp = 'Data displayed correctly matches expected values'
            else:
                # Fallback: use validation for last step, neutral for others
                if _is_last_step and _val and not _val_is_header:
                    _exp = _val
                elif _is_negative:
                    _exp = 'Step completed — continue to verification'
                else:
                    _exp = 'Step completed successfully'

            steps.append(TestStep(
                step_num=i,
                summary=hint,
                expected=_exp,
                data_reference=scenario.source.source_id,
            ))
        # Add final verification step with the actual validation from Chalk
        # GUARD: Only append when steps_hint was empty or had generic content.
        # If steps_hint provided 3+ real steps, those ARE the complete test — don't add filler.
        # Do NOT add for state-matrix / partial-failure scenarios — they already have concrete steps
        _is_generated_matrix = (scenario.source and
                                 getattr(scenario.source, 'source_id', '').startswith(
                                     ('State-Transition-Matrix', 'Partial-Failure-Matrix',
                                      'Idempotency-', 'Concurrency-', 'Field-Validation-Matrix')))
        _steps_hint_has_real_steps = len(scenario.steps_hint) >= 3
        if (scenario.validation and scenario.validation != scenario.title
                and not _is_generated_matrix
                and not _steps_hint_has_real_steps):
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
                # Skip if validation text is just a repeat of the title (adds no value)
                _title_norm = re.sub(r'\s+', ' ', scenario.title.lower().strip())
                _val_norm = re.sub(r'\s+', ' ', _val_clean.lower())
                _is_title_repeat = (_val_norm == _title_norm or _val_norm.startswith(_title_norm[:40]))
                if not _is_title_repeat:
                    # Build the verification step from the ACTUAL validation content (not a
                    # 'Verify expected result: <title>' stub). Strip a leading verb so we don't
                    # get 'Verify: Verify ...'.
                    _val_action = re.sub(r'^(verify|validate|check|ensure|confirm)\b[\s:,\-]*',
                                         '', _val_clean, flags=re.IGNORECASE).strip()
                    _fb_summary = 'Verify: %s' % (_trim_title((_val_action or _val_clean), 110))
                    steps.append(TestStep(
                        step_num=len(steps) + 1,
                        summary=_fb_summary,
                        expected=_trim_title(_val_clean, 200),
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
                         summary='Preconditions: Prepare invalid request data — %s' % _trim_title(scenario_title, 60),
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
                         summary='Preconditions: Set up test data — %s' % _trim_title(scenario_title, 60),
                         expected='Test environment configured for: %s' % _trim_title(scenario_title, 50),
                         data_reference='Scenario: %s' % scenario_title[:40]),
                TestStep(step_num=2,
                         summary='Send %s request to %s' % (method, endpoint),
                         expected='Request accepted and processed by NSL',
                         data_reference='API: %s %s' % (method, endpoint)),
                TestStep(step_num=3,
                         summary='Validate response status 200 OK and response body',
                         expected='Success response confirms: %s' % _trim_title(scenario_title, 50),
                         data_reference='Response validation'),
                TestStep(step_num=4,
                         summary='Verify: %s' % _trim_title(scenario_title, 70),
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
        _chain = _get_chain(scenario.title, scenario.validation, _ctx, feature_type=feature_type)
        steps = [TestStep(step_num=i, summary=s, expected=e, data_reference=scenario.source.source_id)
                 for i, (s, e) in enumerate(_chain, 1)]

    # Ensure at least one step (never a bare-verb 'Verify' — prefer the specific validation text)
    if not steps:
        _one_summary = scenario.title
        if _is_degenerate_title(scenario.title) and (scenario.validation or '').strip():
            _one_summary = 'Verify: %s' % _trim_title(scenario.validation.strip(), 80)
        steps = [TestStep(step_num=1, summary=_one_summary,
                 expected=scenario.validation, data_reference=scenario.source.source_id)]

    # ── Inject downstream-system verification steps (integration-layer depth) ──
    # Closes the domain-awareness gap: verify Service Grouping / Century Report,
    # Syniverse dual-assertion, NBOP MIG tables, and TMO Genesis — not just the
    # API request/response. Only fires for operations with a known integration
    # contract; read-only/inquiry/UI-only features resolve to None and get nothing.
    try:
        _contract = resolve_operation(
            feature_name,
            description=(scenario.validation or scenario.title or ''),
        )
        if _contract is not None:
            _existing_texts = [s.summary for s in steps]
            _ds_steps = build_downstream_verification_steps(
                _contract,
                scenario_category=scenario.category,
                existing_step_texts=_existing_texts,
                max_steps=3,
            )
            for _ds_summary, _ds_expected in _ds_steps:
                steps.append(TestStep(
                    step_num=len(steps) + 1,
                    summary=_ds_summary,
                    expected=_ds_expected,
                    data_reference='Integration Contract: %s' % _contract.operation,
                ))
    except Exception:
        # Never let verification enrichment break TC generation
        pass

    # Transform raw AC text into a proper test scenario title
    # Skip transform for state-matrix/partial-failure titles — they're already clean
    _is_generated_title = (
        scenario.title.startswith('Verify ') or
        scenario.title.startswith('Negative: Verify ') or
        scenario.title.startswith('Negative: Verify New ')
    )
    if _is_generated_title:
        # Just clean double prefixes and truncate
        _raw_title = scenario.title.strip()
        # Remove double "Verify" / "Negative: Verify Negative:" patterns
        _raw_title = re.sub(r'^(Negative:\s+)(Verify\s+)(Negative:\s+)', r'\1\2', _raw_title)
        _raw_title = re.sub(r'^Verify\s+Negative:\s+Verify\s+', 'Negative: Verify ', _raw_title)
        _raw_title = re.sub(r'^Verify\s+Verify\s+', 'Verify ', _raw_title)
        clean_title = _raw_title
    else:
        clean_title = _transform_to_scenario_title(scenario.title, feature_name)
    # Phase 4: Collapse newlines/tabs, replace spaces with underscores, then trim.
    # Budget: allow the title portion up to 150 chars (the feature_id prefix is separate).
    # Uses semantic-aware truncation to find natural sentence breaks rather than
    # chopping mid-phrase at the last underscore.
    clean_title = re.sub(r'[\r\n\t]+', ' ', clean_title)
    clean_title = re.sub(r'\s{2,}', ' ', clean_title).strip()
    # If multi-sentence and will exceed limit, keep only the first sentence
    if len(clean_title) > 140:
        _sentence_break = re.search(r'\.\s+[A-Z]', clean_title)
        if _sentence_break and _sentence_break.start() > 40:
            clean_title = clean_title[:_sentence_break.start()]
    clean_title_safe = clean_title.replace(' ', '_')
    _TITLE_MAX = 150  # title portion only — feature_id prefix adds ~15 more chars
    if len(clean_title_safe) > _TITLE_MAX:
        clean_title_safe = _smart_truncate_title(clean_title_safe, _TITLE_MAX)
    summary = '%s_%s' % (feature_id, clean_title_safe)

    # Description — use full validation text (untruncated) for complete context
    _desc_val = re.sub(r'[\r\n\t]+', ' ', (scenario.validation or scenario.title or '')).strip()
    _desc_val = re.sub(r'\s{2,}', ' ', _desc_val)
    if len(_desc_val) > 500:
        # Truncate at last space before 500
        _cut = _desc_val.rfind(' ', 0, 500)
        _desc_val = _desc_val[:_cut] if _cut > 200 else _desc_val[:500]
    # Ensure ends with punctuation
    if _desc_val and not _desc_val[-1] in '.!?)':
        _desc_val = _desc_val.rstrip(',;: ') + '.'
    description = 'To validate: %s' % _desc_val

    # Environment-specific preconditions
    # For state-matrix/partial-failure TCs: derive actual line state from steps_hint[0]
    # so TC14 (Suspended) doesn't say "Line Status: Active"
    _derived_state = None
    if scenario.steps_hint:
        _hint0 = scenario.steps_hint[0].lower()
        if 'set up subscriber line in' in _hint0 and 'state in sit' in _hint0:
            # Extract e.g. "Suspended", "Hotlined", "Pre-active"
            import re as _re2
            _m = _re2.search(r'set up subscriber line in (.+?) state in sit', _hint0)
            if _m:
                _derived_state = _m.group(1).strip().title()

    if scenario.source.source_type == 'Subtask AC':
        preconditions = '\n'.join([
            '1. Active TMO MDN available in SIT environment',
            '2. User logged into NBOP' if 'nbop' in (scenario.source.source_id or '').lower() or 'ui' in (scenario.source.source_id or '').lower() else '2. API endpoint accessible',
            '3. Line Status: Active',
        ])
    elif _derived_state and _derived_state.lower() != 'active':
        # State-matrix negative TC — precondition must reflect the actual required line state
        preconditions = (
            '1.\tActive TMO subscriber line in SIT environment\n'
            '2.\tAPI endpoint accessible\n'
            '3.\tLine Status: %s' % _derived_state
        )
    else:
        # Scenario-aware preconditions derived from the scenario title/validation so each TC
        # isn't the same generic boilerplate (device / plan / existing-line context).
        _t = (scenario.title + ' ' + (scenario.validation or '')).lower()
        _pc = ['1.\tActive TMO subscriber line in SIT environment', '2.\tAPI endpoint accessible']
        _extra = []
        if 'tablet' in _t:
            _extra.append('Tablet device on an eligible Tablet plan')
        elif 'phone' in _t or ('mobile' in _t and 'hotspot' not in _t[:40]):
            _extra.append('Mobile (Phone) device on an eligible plan')
        if 'unlimited plus' in _t or 'unl+' in _t or 'unlp' in _t:
            _extra.append('Line is on an Unlimited Plus rate plan')
        elif 'unlimited' in _t or 'unl' in _t:
            _extra.append('Line is on an Unlimited rate plan')
        if 'existing' in _t or 'eft' in _t:
            _extra.append('Existing active line (target feature not yet provisioned)')
        elif 'new ' in _t and 'activation' in _t:
            _extra.append('New subscriber ready to activate in SIT')
        if 'change feature' in _t and not any('not yet provisioned' in e for e in _extra):
            _extra.append('Target feature not yet provisioned on the line')
        _n = 3
        for _e in _extra[:3]:  # cap to keep it tight
            _pc.append('%d.\t%s' % (_n, _e)); _n += 1
        _pc.append('%d.\tLine Status: Active' % _n)
        preconditions = '\n'.join(_pc)

    return TestCase(
        summary=summary,
        description=description,
        preconditions=preconditions,
        steps=steps,
        story_linkage=feature_id,
        label=feature_id,
        category=scenario.category,
        priority=(getattr(scenario, 'priority_hint', '') or 'P2'),
        traceability=scenario.source,
        dimension_values={},
        user_requested=getattr(scenario, 'user_requested', False),
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

        summary = '%s_Negative - %s: %s' % (feature_id, error_code, rule_name or _trim_title(condition, 50))

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
        # Additional check: reject ALLCAPS_UNDERSCORE_SLUGS that are derived from plain text
        # rather than real ERR codes. Real codes start with ERR, are numeric, or have digits.
        import re as _re_ec
        if not _ec_looks_generic:
            _is_plain_slug = (
                bool(_re_ec.match(r'^[A-Z][A-Z_]+$', error_code))   # pure alpha caps slug
                and not _re_ec.match(r'^ERR', error_code, _re_ec.IGNORECASE)
                and not any(c.isdigit() for c in error_code)
            )
            # Also reject multi-word mixed-case natural language strings (not real codes)
            # e.g. "Line Status validation", "MDN not found" — these have spaces
            _is_natural_language = (
                ' ' in error_code
                and not _re_ec.match(r'^ERR', error_code, _re_ec.IGNORECASE)
                and not any(c.isdigit() for c in error_code)
                and len(error_code.split()) > 1
            )
            if _is_plain_slug or _is_natural_language:
                _ec_looks_generic = True
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
                summary='Preconditions: Set up error condition — %s' % _trim_title(condition, 70),
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
                expected='Error response: code=%s, message="%s"' % (error_code, _trim_title(error_msg, 80)),
                data_reference='Business Rule: %s' % (neg_spec.source.source_id if neg_spec.source else error_code),
            ),
        ]

    # ── Inject "no unintended downstream change" assertion (negative-path depth) ──
    try:
        _neg_contract = resolve_operation(feature_name, description=condition)
        if _neg_contract is not None:
            _existing_neg = [s.summary for s in steps]
            _ds_neg = build_downstream_verification_steps(
                _neg_contract,
                scenario_category='Negative',
                existing_step_texts=_existing_neg,
                max_steps=1,
            )
            for _ds_summary, _ds_expected in _ds_neg:
                steps.append(TestStep(
                    step_num=len(steps) + 1,
                    summary=_ds_summary,
                    expected=_ds_expected,
                    data_reference='Integration Contract: %s' % _neg_contract.operation,
                ))
    except Exception:
        pass

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
            summary='Prepare %s request to %s for scenario: %s' % (method, _endpoint_ref, _trim_title(scenario_title, 50)),
            expected='Request payload prepared per API specification',
            data_reference='%s %s' % (method, _endpoint_ref)),
        TestStep(step_num=2,
            summary='Send %s %s with required headers and payload' % (method, _endpoint_ref),
            expected='API responds with expected status code',
            data_reference='API: %s' % spec.api_name),
        TestStep(step_num=3,
            summary='Validate response matches scenario: %s' % _trim_title(scenario_title, 60),
            expected='Response fields match expected values per %s specification' % spec.api_name,
            data_reference='Scenario: %s' % scenario_title[:40]),
    ]

    return steps


# ================================================================
# HELPERS
# ================================================================


def _assign_serial_numbers(test_cases: List[TestCase], feature_priority: str = '') -> None:
    """Assign sequential serial numbers and priorities to all test cases.

    Priority assignment (Phase 5) — now criticality-aware:
      The Jira feature priority (Critical / High / Medium / Low) scales how many
      Happy Path scenarios are treated as P1 core verification, and whether the
      remainder fall to P2 (still important functional coverage) or P3. This fixes
      the old bug where a Critical feature's functional scenarios beyond the first
      few were all demoted to P3 "nice-to-have".

      P1 (Critical): E2E + core Happy Path verification (count scales with criticality).
      P2 (Important): Negative/regression + remaining functional Happy Path on
                      Critical/High features (functional scenarios are NOT throwaway).
      P3 (Nice-to-have): Edge cases, and remaining Happy Path only on lower-criticality
                         features.
    """
    # Substring match — Jira priority is often a combined label ("Critical/High",
    # "Blocker/Emergency"). Check most-critical tokens first.
    _fp = (feature_priority or '').strip().lower()
    if any(k in _fp for k in ('blocker', 'emergency', 'critical', 'highest')):
        _p1_cap, _overflow_pri = 8, 'P2'
    elif 'high' in _fp:
        _p1_cap, _overflow_pri = 6, 'P2'
    elif any(k in _fp for k in ('low', 'minor', 'trivial')):
        _p1_cap, _overflow_pri = 2, 'P3'
    else:  # Medium / unset — balanced default
        _p1_cap, _overflow_pri = 4, 'P3'

    # Count happy path TCs to determine P1 allocation
    happy_path_count = 0
    for i, tc in enumerate(test_cases, 1):
        tc.sno = str(i)

        # Priority assignment based on category and position
        category_lower = (tc.category or '').lower()

        if category_lower == 'negative':
            tc.priority = 'P2'
        elif category_lower == 'regression':
            tc.priority = 'P2'
        elif category_lower == 'edge case':
            tc.priority = 'P3'
        elif category_lower in ('e2e', 'end-to-end'):
            tc.priority = 'P1'
        elif category_lower == 'happy path':
            happy_path_count += 1
            # Core happy path TCs are P1 (count scales with feature criticality);
            # the remainder fall to P2 (functional) or P3 (lower criticality).
            if happy_path_count <= _p1_cap:
                tc.priority = 'P1'
            else:
                tc.priority = _overflow_pri
        else:
            tc.priority = 'P2'


def _smart_truncate_title(title: str, max_len: int) -> str:
    """Truncate a TC title at a natural sentence/phrase boundary.

    Strategy (in priority order):
      1. Find a clause boundary (preposition/conjunction) near the limit:
         _when_, _for_, _if_, _on_, _after_, _before_, _with_, _that_, _and_,
         _in_, _to_, _from_, _by_, _via_
      2. Fall back to last underscore (word boundary) before limit
      3. Hard chop at max_len as last resort

    The goal: names end at a meaningful phrase rather than mid-word or mid-clause.
    """
    if len(title) <= max_len:
        # Even if within limit, strip unclosed parens
        return _strip_unclosed_paren(title)

    # Define clause-boundary keywords (surrounded by underscores = word boundaries)
    # Ordered from strongest break signal to weakest
    _clause_breaks = [
        '_when_', '_if_', '_after_', '_before_', '_once_', '_until_',
        '_for_', '_on_', '_with_', '_that_', '_and_', '_but_',
        '_in_', '_to_', '_from_', '_by_', '_via_', '_as_',
    ]

    # Strategy 1: Find the LAST clause boundary in the sweet zone (60% to 100% of max_len)
    # Prefer breaking later (closer to max_len) for maximum info retention.
    _sweet_start = int(max_len * 0.55)
    best_break = -1
    for kw in _clause_breaks:
        # Search from the end of the sweet zone backwards
        pos = title.rfind(kw, _sweet_start, max_len)
        if pos > best_break:
            best_break = pos

    if best_break > _sweet_start:
        # Cut just before the clause boundary keyword
        return _strip_unclosed_paren(title[:best_break].rstrip('_-,'))

    # Strategy 2: Find last underscore (word boundary) before max_len
    cut_pos = title.rfind('_', 0, max_len)
    if cut_pos > int(max_len * 0.5):
        return _strip_unclosed_paren(title[:cut_pos].rstrip('_-,'))

    # Strategy 3: Hard chop
    return _strip_unclosed_paren(title[:max_len].rstrip('_-,'))


def _strip_unclosed_paren(title: str) -> str:
    """Remove trailing unclosed parenthesis content from a title.
    E.g. 'foo_(single-bucket' → 'foo'
    """
    import re as _re
    # If there's a '(' without a matching ')' at the end, strip from the '(' onwards
    if '(' in title and title.count('(') > title.count(')'):
        title = _re.sub(r'_?\([^)]*$', '', title)
    return title.rstrip('_-,')


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
    from .step_templates import strip_dangling_tail, truncate_at_word

    text = raw_text.strip()

    # ── Multi-sentence source: keep the first sentence ──
    #
    # A Chalk scenario is sometimes a paragraph. Truncating it yields a run-on cut at an
    # arbitrary point, whereas its first sentence is usually the scenario itself and the rest
    # is elaboration. MWTGPROV-4416's 185-character scenario produced
    # "...SMS/MMS=Messages. Validation ranges apply per" - ending on a dangling "per" -
    # where the first sentence alone is complete and readable.
    #
    # tc_builder has a first-sentence trim of its own, but it is gated on len > 140 and runs
    # AFTER this function, which had already shortened the text to 136. The gate therefore
    # never fired. Doing it here, before any truncation, is what makes it effective.
    if len(text) > 100:
        _sentence_end = re.search(r'\.\s+[A-Z0-9]', text)
        if _sentence_end and _sentence_end.start() > 40:
            text = text[:_sentence_end.start() + 1].strip()

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
    #
    # The condition is anchored to the COMMA. This was `(.{10,80}?)` - non-greedy, so it
    # stopped at the first 10 characters that let the rest of the pattern match, splitting
    # the sentence in the wrong place. On MWTGPROV-4086, "When CS access to the 'MNO_TMO'
    # permission is OFF, NBOP to display the default values..." gave condition="CS access to"
    # and action="the 'MNO_TMO' permission is OFF, NBOP to" - the clause boundary landed
    # mid-phrase and the two halves were then swapped, producing
    # "Verify the 'MNO_TMO' permission is OFF, NBOP to when CS access to". Garbled, not merely
    # truncated. Requiring the comma keeps the condition whole, and if there is no comma the
    # transform does not apply rather than guessing where the clause ends.
    when_match = re.match(
        r"^[Ww]hen\s+([^,]{10,90}),\s*"
        r"(?:NBOP\s+to\s+|the\s+system\s+(?:shall\s+)?|NSL\s+(?:shall\s+)?)?(.+)",
        text
    )
    if when_match:
        # truncate_at_word, not a raw slice: `[:40]` cut both halves mid-phrase.
        condition = truncate_at_word(when_match.group(1).strip().rstrip(','), 70)
        action = truncate_at_word(when_match.group(2).strip(), 70)
        return strip_dangling_tail('Verify %s when %s' % (action, condition))

    # ── Verb prefix → add "Verify" ──
    if re.match(r'^(Display|Show|Return|Send|Update|Create|Delete|Trigger|Process|Handle)\s', text, re.IGNORECASE):
        text = 'Verify %s' % text

    # ── Already starts with "Verify"/"Validate" — keep it ──
    if not text.lower().startswith(('verify ', 'validate ', 'for ')):
        text = 'Verify %s' % text

    # ── Final truncation using smart boundary detection ──
    # (outer tc_builder budget is 150 — this pre-truncation keeps titles clean)
    if len(text) > 140:
        # Use space-based version of smart truncate for pre-underscore text
        text = _smart_truncate_title(text.replace(' ', '_'), 140).replace('_', ' ')

    # Clean trailing punctuation/artifacts, then drop a dangling connective so the title does
    # not read as cut off mid-thought.
    text = text.rstrip(' —-,.:')

    return strip_dangling_tail(text)


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
    """DEPRECATED: Legacy intent classifier. Primary routing uses classify_feature().
    This function only provides supplementary channel/device hints for dual-path generation.
    Do NOT use its classification output for routing decisions.

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
    scenario_intent = scenario.get('_scenario_intent') or _classify_ui_scenario_intent(title, validation)
    network_provider = scenario.get('_network_provider') or _infer_network_provider(title, validation)
    tc_num = idx + 1

    clean_title = _build_ui_tc_summary_name(
        title, feature_name, tc_num, network_provider=network_provider)
    summary = '%s_TC%02d_%s' % (feature_id, tc_num, clean_title)

    description = _build_ui_description(title, validation, scenario_intent)
    preconditions = _build_ui_preconditions(
        title, validation, network_provider, scenario_intent)

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
                expected=validation or 'The step produces the scenario result described by: %s' % hint,
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
                summary='Verify: %s' % _trim_title(validation, 80),
                expected=validation or '%s UI state matches the scenario requirement' % feature_name,
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
        source_id='%s_scenario_%d' % (feature_id, tc_num),
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
        dimension_values={
            'channel': 'NBOP',
            'scenario': title[:50],
            'network_provider': network_provider,
        },
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
    specialized_chain: bool = False,
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
                    summary='Verify: %s' % _trim_title(subtask_ac_text, 80),
                    expected=subtask_ac_text,
                    data_reference=step.data_reference,
                ))
            else:
                enriched_steps.append(TestStep(
                    step_num=step.step_num,
                    summary='Verify: %s' % _trim_title(scenario_title, 80),
                    expected=scenario_title,
                    data_reference=step.data_reference,
                ))
            continue

        # Step is specific enough — keep as-is
        enriched_steps.append(step)

    # ── Validate structural completeness ──
    action_keywords = (
        'click', 'select', 'search', 'enter', 'open', 'perform', 'trigger',
        'capture', 'compare', 'submit', 'toggle')
    has_nav = any(
        any(kw in s.summary.lower() for kw in ('navigate', 'launch', 'login'))
        for s in enriched_steps
    )
    has_action = any(
        any(kw in s.summary.lower() for kw in action_keywords)
        for s in enriched_steps
    )
    has_verify = any(
        any(kw in s.summary.lower() for kw in ('verify', 'compare', 'confirm', 'validate'))
        for s in enriched_steps
    )

    # Pad missing step types. Specialized backend/hybrid chains intentionally use
    # capture/compare/trigger actions and must not receive an unrelated transaction search.
    pad_steps = []
    if not has_nav and not specialized_chain:
        pad_steps.append(TestStep(
            step_num=0,
            summary='Launch NBOP portal and search subscriber by MDN',
            expected='Subscriber profile loaded successfully',
            data_reference='Navigation step',
        ))
    if not has_action and not specialized_chain:
        pad_steps.append(TestStep(
            step_num=0,
            summary='Perform the scenario-specific NBOP action: %s' % scenario_title,
            expected=subtask_ac_text or 'The requested scenario action is accepted by NBOP',
            data_reference='Action step from scenario requirement',
        ))
    if not has_verify:
        verify_text = subtask_ac_text or scenario_title
        # Clean up verify text to avoid "Verify: Phone — Verify ..." double patterns
        # Strip product prefix (e.g., "Phone — ", "Tablet — ")
        if ' — ' in verify_text:
            verify_text = verify_text.split(' — ', 1)[1].strip()
        # Strip leading "Verify" / "Verify that" to avoid "Verify: Verify ..."
        import re as _re_pad
        verify_text = _re_pad.sub(r'^(?:Verify\s+(?:that\s+)?)', '', verify_text).strip()
        if not verify_text:
            verify_text = scenario_title
        pad_steps.append(TestStep(
            step_num=0,
            summary='Verify requirement: %s' % verify_text,
            expected=subtask_ac_text or scenario_title,
            data_reference='Scenario verification',
        ))

    # Insert pad steps at appropriate positions
    if pad_steps:
        # Nav steps go first, action in middle, verify at end
        nav_pads = [s for s in pad_steps if any(kw in s.summary.lower() for kw in ('launch', 'navigate', 'login'))]
        action_pads = [s for s in pad_steps if any(kw in s.summary.lower() for kw in action_keywords)]
        verify_pads = [s for s in pad_steps if 'verify' in s.summary.lower()]

        enriched_steps = nav_pads + enriched_steps + action_pads + verify_pads

    # ── Enforce step count bounds: 4–15 ──
    # Allow up to 15 steps for evidence-based TCs with explicit verification points.
    # Evidence TCs often verify multiple attributes across multiple screens.
    # Typically 4-8 for API TCs, up to 15 for evidence-based TCs.
    max_steps = 15
    if len(enriched_steps) > max_steps:
        enriched_steps = enriched_steps[:max_steps]

    while len(enriched_steps) < 4:
        # Keep the minimum chain meaningful and grounded in the complete requirement.
        requirement = subtask_ac_text or scenario_title
        enriched_steps.append(TestStep(
            step_num=0,
            summary='Verify additional scenario requirement: %s' % requirement,
            expected=requirement,
            data_reference='Scenario completeness',
        ))

    # ── Renumber steps ──
    for i, step in enumerate(enriched_steps):
        step.step_num = i + 1

    return enriched_steps


def _build_ui_tc_summary_name(title: str, feature_name: str, tc_num: int,
                              network_provider: str = '') -> str:
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

    # Use the inferred execution provider when one was supplied. A provider-neutral
    # title keeps its existing no-suffix form, while explicit parity/comparison text
    # is labelled by the route under test rather than the provider used as reference.
    mentioned_providers = _provider_tokens(text)
    normalized_provider = (network_provider or '').strip().upper()
    if normalized_provider in ('TMO', 'VZW', 'MIXED') and (
            mentioned_providers or normalized_provider == 'MIXED'):
        mno = normalized_provider
    elif 'tmo' in text_lower:
        mno = 'TMO'
    elif 'vzw' in text_lower:
        mno = 'VZW'
    else:
        mno = ''

    def _intent_summary(*segments: str) -> str:
        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.extend(segments)
        if mno:
            parts.append(mno)
        return '_'.join(parts)

    # Scenario-specific business intents must win over generic display/removal
    # wording, otherwise distinct operations collapse into Verify_Attributes_Displayed.
    is_change_features = 'change feature' in text_lower
    is_feature_conflict = (
        'feature' in text_lower and
        any(term in text_lower for term in ('conflict', 'incompatible')) and
        any(term in text_lower for term in (
            'error', 'reject', 'fail', 'cannot', 'unable', 'prevent', 'block')))
    if is_feature_conflict:
        return _intent_summary('Change_Features', 'Conflict_Rejection')

    if (is_change_features and 'manage line' in text_lower and
            any(term in text_lower for term in (
                'display', 'visible', 'visibility', 'available', 'accessible'))):
        has_mno_tmo_on = (
            'mno_tmo' in text_lower and
            'permission' in text_lower and
            bool(re.search(r'\b(?:is\s+)?on\b', text_lower)))
        has_active_line = bool(re.search(
            r'\b(?:line\s+status\s+is|status\s*=?)\s*active\b', text_lower))
        if has_mno_tmo_on and has_active_line:
            return _intent_summary(
                'Conditional_Access', 'MNO_TMO_Permission_ON',
                'Active_Line', 'Change_Features_Entry')
        return _intent_summary(
            'Baseline_Check', 'Manage_Line_Menu', 'Change_Features_Visibility')

    if (any(term in text_lower for term in (
            'mno activation history', 'hmno activation history', 'hmno activation')) and
            any(term in text_lower for term in ('display', 'visible', 'available', 'present'))):
        return _intent_summary('Verify', 'HMNO_Activation_History_Displayed')

    if ('npanxx' in text_lower and
            any(term in text_lower for term in ('change mdn', 'mdn screen')) and
            any(term in text_lower for term in ('activation', 'activate')) and
            any(term in text_lower for term in (
                'not displayed', 'removed', 'removal', 'remove', 'hidden', 'absent',
                'no longer'))):
        return _intent_summary(
            'Validate', 'Both_Screens', 'ZIP_Code_Only',
            'Activation_And_Change_MDN')

    if ('npanxx' in text_lower and
            any(term in text_lower for term in ('activation', 'activate')) and
            any(term in text_lower for term in (
                'not displayed', 'hidden', 'absent')) and
            any(term in text_lower for term in ('screen', 'control', 'label', 'field', 'option'))):
        return _intent_summary(
            'Activation_Form_Audit', 'NPANXX_Control_Absent',
            'ZIP_Code_Required')

    if ('npanxx' in text_lower and
            any(term in text_lower for term in ('activation flow', 'activation process')) and
            any(term in text_lower for term in ('removed', 'removal', 'remove', 'no longer'))):
        return _intent_summary(
            'ZIP_Code_Only', 'End_To_End_Activation',
            'Profile_Provider_Transaction_Evidence')

    if ('npanxx' in text_lower and
            any(term in text_lower for term in ('activation', 'activate')) and
            any(term in text_lower for term in (
                'not displayed', 'removed', 'removal', 'remove', 'hidden', 'absent',
                'no longer'))):
        return _intent_summary('Verify', 'NPANXX_Absent_From_Activation')

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
        # Keep operation intent in the title so semantic deduplication cannot
        # collapse Update availability into an in-progress Cancel/Update rule.
        has_update = 'update' in text_lower
        has_cancel = 'cancel' in text_lower
        status = ''
        if 'success' in text_lower:
            status = 'Success'
        elif "doesn't succeed" in text_lower or 'fail' in text_lower or 'bad' in text_lower:
            status = 'Failed'
        elif 'in progress' in text_lower or 'in-progress' in text_lower:
            status = 'InProgress'

        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        parts.append('PortIn')
        if has_cancel and has_update:
            parts.append('Cancel_Or_Update')
            if status:
                parts.append(status)
            parts.append('Verify_Both_Actions')
        elif has_update:
            parts.append('Update')
            if status:
                parts.append(status)
            if any(term in text_lower for term in (
                    'option', 'available', 'availability', 'displayed', 'visible')):
                parts.append('Option_Availability')
            else:
                parts.append('Verify_Update')
        else:
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

        # Convert to underscore-separated and remove modal filler before shortening. This
        # keeps the business condition in titles such as "should be able to call ... based
        # on network provider" instead of truncating that condition to "...based_on_n".
        from .step_templates import truncate_at_word as _tw
        clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean)
        clean = re.sub(r'\s+', '_', clean.strip())
        clean = re.sub(r'^(?:should|must)_be_able_to_', '', clean, flags=re.IGNORECASE)
        clean = _tw(clean, 50)

        parts = ['NBOP']
        if product_prefix:
            parts.append(product_prefix)
        if clean:
            parts.append(clean)
        if mno and clean.upper() != mno and not clean.upper().endswith('_' + mno):
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
    scenario_intent = scenario.get('_scenario_intent') or _classify_ui_scenario_intent(title, validation)
    network_provider = scenario.get('_network_provider') or _infer_network_provider(
        title, validation, '%s %s' % (feature_name, subtask_ac_text))
    tc_num = idx + 1

    # ── Build clean TC summary name ──
    clean_title = _build_ui_tc_summary_name(
        title, feature_name, tc_num, network_provider=network_provider)
    summary = '%s_TC%02d_%s' % (feature_id, tc_num, clean_title)
    description = _build_ui_description(title, validation, scenario_intent)
    preconditions = _build_ui_preconditions(
        title, validation, network_provider, scenario_intent)

    # Strong backend intents are routed per scenario even though the parent feature
    # remains UI. These chains include the NBOP trigger/display and backend evidence.
    steps: List[TestStep] = _build_hybrid_ui_backend_steps(
        scenario_intent, network_provider, validation)

    # Change Features and NPANXX requirements need exact, operation-specific NBOP
    # chains even when a source also supplies short/generic step hints.
    concrete_context = ('%s %s %s' % (feature_name, title, validation)).lower()
    zip_location_flow = (
        bool(re.search(r'\b(?:zip\s*code|zip-code|zipcode)\b', concrete_context)) and
        any(term in concrete_context for term in (
            'change mdn', 'mdn screen', 'mdn-screen',
            'activation screen', 'activation flow'))
    )
    feature_operation_flow = (
        'feature' in concrete_context and
        (any(term in concrete_context for term in ('conflict', 'incompatible')) or
         bool(re.search(r'\b(?:add(?:ing)?|remov(?:e|ing))\b.{0,40}\bfeatures?\b',
                        concrete_context)))
    )
    prefer_concrete_knowledge = (
        scenario_intent == 'ui' and
        ('npanxx' in concrete_context or zip_location_flow or feature_operation_flow or
         any(term in concrete_context for term in (
             'change feature', 'change features', 'optional feature',
             'required feature', 'included feature', 'pdl data')))
    )
    if not steps and prefer_concrete_knowledge:
        try:
            from .nbop_ui_knowledge import generate_ui_steps
            ui_steps = generate_ui_steps(
                feature_name, description=validation, scenario_title=title,
                network_provider=network_provider)
            for i, (desc, expected) in enumerate(ui_steps or []):
                steps.append(TestStep(
                    step_num=i + 1,
                    summary=desc,
                    expected=expected,
                    data_reference='NBOP UI Knowledge: %s' % title[:40],
                ))
        except (ImportError, Exception):
            pass

    # ── Priority 0: Use steps_hint from evidence documents (highest quality for other UI-only scenarios) ──
    steps_hint = scenario.get('steps_hint', [])
    if not steps and steps_hint and len(steps_hint) >= 2:
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
                expected = validation or 'The stated verification condition is satisfied: %s' % hint_text
            elif 'navigate' in hint_text.lower() or 'click' in hint_text.lower():
                expected = 'Navigation succeeds and the named target page or section loads'
            elif 'login' in hint_text.lower() or 'log in' in hint_text.lower() or 'search' in hint_text.lower():
                expected = 'The requested subscriber profile loads successfully'
            elif 'call' in hint_text.lower() and 'api' in hint_text.lower():
                expected = validation or 'The named API returns the response required by the scenario'
            elif 'check' in hint_text.lower() or 'validate' in hint_text.lower():
                expected = validation or 'The checked value matches the scenario requirement'
            else:
                expected = validation or 'The named scenario action produces its specified result'
            steps.append(TestStep(
                step_num=step_num,
                summary=hint_text,
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
                        hist_summary = '%s on Historical Usage screen' % _trim_title(hint_text, 100)
                    else:
                        hist_summary = _trim_title(hint_text, 120)
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
            from .nbop_ui_knowledge import generate_ui_steps
            ui_steps = generate_ui_steps(
                feature_name, description=validation, scenario_title=title,
                network_provider=network_provider)
            if ui_steps and len(ui_steps) >= 3:
                # Check if steps have specific content (not just generic placeholders)
                has_specific = any(
                    any(kw in desc.lower() for kw in (
                        'mdn', 'tab', 'dropdown', 'field', 'menu', 'tile', 'click',
                        'select', 'response', 'payload', 'transaction', 'history',
                        'feature', 'npanxx', 'zip'))
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

        # Only transaction/history requirements get a transaction search action.
        # Other ACs use their own requirement as the executable UI action.
        step_num += 1
        scenario_context = ('%s %s' % (title, subtask_ac_text)).lower()
        if any(term in scenario_context for term in ('transaction history', 'transaction id', 'line history', 'activation history')):
            steps.append(TestStep(
                step_num=step_num,
                summary='Locate the required history record using its Transaction ID or MDN',
                expected='The matching history record is displayed with its operation status',
                data_reference='History lookup required by scenario',
            ))
        else:
            steps.append(TestStep(
                step_num=step_num,
                summary='Perform the NBOP action required by the scenario: %s' % title,
                expected=subtask_ac_text,
                data_reference='Scenario-specific UI action',
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
                    summary='Verify requirement: %s' % raw,
                    expected=raw,
                    data_reference='AC text verification',
                ))

    # ── Priority 3: Fallback — use scenario title as step description ──
    if not steps:
        requirement = validation or title
        steps = [
            TestStep(step_num=1,
                     summary='Launch NBOP portal and search subscriber by MDN',
                     expected='Subscriber profile loads for the scenario provider',
                     data_reference='Navigation'),
            TestStep(step_num=2,
                     summary='Navigate to %s' % (nav_path or feature_name),
                     expected='The required NBOP page loads with its available controls',
                     data_reference='Navigation'),
            TestStep(step_num=3,
                     summary='Perform the NBOP action required by the scenario: %s' % title,
                     expected='NBOP accepts the scenario input and returns a result for verification',
                     data_reference='Scenario action'),
            TestStep(step_num=4,
                     summary='Verify requirement: %s' % requirement,
                     expected=requirement,
                     data_reference='Scenario validation'),
        ]

    # ── Validate step quality ──
    steps = _validate_step_quality(
        steps,
        scenario_title=title,
        subtask_ac_text=subtask_ac_text or validation,
        specialized_chain=scenario_intent != 'ui',
    )

    # ── Build traceability ──
    # Determine source_type based on scenario source
    source_type = scenario.get('source_type', 'Chalk Scenario')
    if subtask_key:
        source_type = 'Subtask AC'
        source_id = subtask_key
    else:
        source_id = '%s_scenario_%d' % (feature_id, tc_num)

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
        dimension_values={
            'channel': 'NBOP',
            'scenario': title[:50],
            'network_provider': network_provider,
        },
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
