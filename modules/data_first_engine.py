"""
data_first_engine.py — V8.0 Data-First Engine Entry Point.

Orchestrates the complete data-first test suite generation pipeline:
  1. Dimension Extraction — scan all sources for testable dimensions
  2. Combination Planning — determine smart multiplication strategy
  3. TC Building — construct concrete test cases from plan
  4. Validation — enforce zero-generic rule

If zero testable items are found, produces a WarningReport instead of
fabricating test cases. If validation finds generic content, logs warnings
but still returns the suite.

This module replaces the V7.0 `build_test_suite()` in `test_engine.py`
when engine_version='8' is selected.
"""
from typing import List, Dict, Any, Callable, Optional

from .traceability import TraceabilityRecord
from .data_models_v8 import (
    TestSuite, TestCase, WarningReport, DataInventory, CombinationPlan,
    DataSourceEntry,
)
from .dimension_extractor import extract_dimensions
from .combination_engine import plan_combinations
from .tc_builder import build_test_cases, classify_feature
from .zero_generic_validator import validate_suite
from .nmno_api_lookup import extract_api_operation_name, lookup_api_specs
from .cr_detector import is_cr_or_bug


# Engine version identifier
ENGINE_VERSION = '8.0.0'


# ================================================================
# MAIN ENTRY POINT
# ================================================================


def build_test_suite_v8(
    jira,
    chalk,
    parsed_docs: List = None,
    options: Dict[str, Any] = None,
    deep_mine_result=None,
    log: Callable = print,
) -> TestSuite:
    """V8.0 Data-First Engine entry point.

    Orchestrates: Dimension Extraction → Combination → TC Build → Validation

    Returns a TestSuite with traceability on every TC, or a warning-only
    suite if zero testable items are found.

    Args:
        jira: JiraIssue dataclass with feature data
        chalk: ChalkData dataclass (may be None)
        parsed_docs: List of ParsedDoc from attachments
        options: Dict with generation options (channel, strategy, etc.)
        deep_mine_result: DeepMineResult with crawled API specs, subtask mines
        log: Logging function (default: print)

    Returns:
        TestSuite with engine_version='8.0.0'
    """
    parsed_docs = parsed_docs or []
    options = options or {}

    feature_id = jira.key if jira else ''
    feature_title = jira.summary if jira else ''

    log('═' * 60)
    log('[V8-ENGINE] Data-First Engine v%s starting...' % ENGINE_VERSION)
    log('[V8-ENGINE] Feature: %s - %s' % (feature_id, feature_title))
    log('═' * 60)

    # ── Step 0: Feature Classification ──
    log('[V8-ENGINE] Step 0: Classifying feature...')
    ac_text = jira.acceptance_criteria if jira and hasattr(jira, 'acceptance_criteria') else ''
    classification = classify_feature(feature_title, ac_text or '')
    log('[V8-ENGINE]   Classification: %s (confidence=%.2f, api_kw=%s, ui_kw=%s)' % (
        classification.classification, classification.confidence,
        classification.api_keywords_found[:3], classification.ui_keywords_found[:3]))

    # ── Step 0a: CR/Bug Fix Detection ──
    # CR/bug fix tickets ALWAYS use the CR-specific engine path.
    # This is a hard rule — no override based on Chalk scenario count.
    # Chalk can have 15 scenarios for a CR fix (4389 is proof) — that does NOT
    # mean it should get channel/device expansion. The "- CR -" in the title
    # is the definitive signal.
    _is_cr = is_cr_or_bug(
        summary=jira.summary if jira else '',
        issue_type=jira.issue_type if jira and hasattr(jira, 'issue_type') else '',
        description=jira.description if jira and hasattr(jira, 'description') else '',
    )
    if _is_cr:
        log('[V8-ENGINE] *** CR/Bug fix detected — delegating to CR-specific engine (no override) ***')
        return _build_cr_suite_v8(jira, chalk, parsed_docs, options, deep_mine_result, log)

    # ── Step 0b: NMNO API Lookup (if API or hybrid) ──
    nmno_result = None
    if classification.classification in ('api', 'hybrid'):
        log('[V8-ENGINE] Step 0b: NMNO API Lookup (local DB)...')
        chalk_urls = _extract_chalk_urls_from_ac(jira)
        api_name = extract_api_operation_name(feature_title, chalk_urls)
        if api_name:
            nmno_result = lookup_api_specs(api_name, log=log)
            if nmno_result and (nmno_result.business_rules or nmno_result.api_specs):
                log('[V8-ENGINE]   NMNO: %d Business Rules, %d API specs from TMO_API_Chalk' % (
                    len(nmno_result.business_rules), len(nmno_result.api_specs)))
            else:
                log('[V8-ENGINE]   NMNO: No data found for "%s" — will use deep_mine fallback' % api_name)
        else:
            log('[V8-ENGINE]   Could not extract API operation name — skipping NMNO lookup')

    # ── Step 0c: NBOP UI Lookup (if UI or hybrid) ──
    nbop_data = None
    if classification.classification in ('ui', 'hybrid'):
        log('[V8-ENGINE] Step 0c: NBOP UI Knowledge lookup...')
        nbop_data = _gather_nbop_data(jira, log)

    # ── Step 1: Dimension Extraction ──
    log('[V8-ENGINE] Step 1: Extracting dimensions from all data sources...')
    dimension_set = extract_dimensions(
        jira=jira,
        chalk=chalk,
        deep_mine_result=deep_mine_result,
        parsed_docs=parsed_docs,
        nmno_result=nmno_result,
        nbop_data=nbop_data,
        classification=classification.classification,
        log=log,
    )

    # ── Step 1b: Apply custom instructions to dimensions ──
    custom_text = options.get('custom_instructions', '')
    if custom_text and custom_text.strip():
        log('[V8-ENGINE] Step 1b: Applying custom instructions...')
        dimension_set = _apply_custom_instructions(dimension_set, custom_text, options, log)

    # ── Zero-items check ──
    if dimension_set.data_inventory.total_testable_items == 0:
        log('[V8-ENGINE] WARNING: Zero testable items found across all sources!')
        warning_report = _build_warning_report(feature_id, dimension_set.data_inventory)
        # Return a suite with warnings but no TCs
        return TestSuite(
            feature_id=feature_id,
            feature_title=feature_title,
            feature_desc='WARNING: No testable data found. See warnings for guidance.',
            test_cases=[],
            data_inventory=dimension_set.data_inventory,
            combination_plan=CombinationPlan(),
            warnings=warning_report.guidance,
            engine_version=ENGINE_VERSION,
            # Legacy fields for dashboard compatibility
            acceptance_criteria=_extract_ac_list(jira),
            scope=chalk.scope if chalk else '',
            rules=chalk.rules if chalk else '',
            channel=options.get('channel', jira.channel if jira and hasattr(jira, 'channel') else ''),
            pi=jira.pi if jira and hasattr(jira, 'pi') else '',
        )

    # ── FIX 4: V7 supplementary mining for low-dimension tickets ──
    # When dimension extraction yields < 5 testable items, call V7's
    # _build_from_jira_only and subtask mining as supplementary source.
    _v7_supplement_tcs = []
    if dimension_set.data_inventory.total_testable_items < 5:
        log('[V8-ENGINE] Low testable items (%d) — invoking V7 supplementary mining...' %
            dimension_set.data_inventory.total_testable_items)
        try:
            from .test_engine import _build_from_jira_only, _extract_feature_name
            _v7_feature_name = _extract_feature_name(feature_title, feature_id)
            _v7_supplement_tcs = _build_from_jira_only(jira, _v7_feature_name, log=log)
            if _v7_supplement_tcs:
                log('[V8-ENGINE]   V7 mining produced %d supplementary TCs' % len(_v7_supplement_tcs))
        except Exception as _v7_err:
            log('[V8-ENGINE]   V7 supplementary mining failed: %s — continuing' % str(_v7_err)[:100])

    # ── Step 2: Combination Planning ──
    log('[V8-ENGINE] Step 2: Planning smart combinations...')
    combination_plan = plan_combinations(dimension_set, log=log)

    # ── Step 3: TC Building ──
    log('[V8-ENGINE] Step 3: Building test cases from plan...')
    nbop_knowledge = options.get('nbop_knowledge', None)
    test_cases = build_test_cases(
        plan=combination_plan,
        jira=jira,
        chalk=chalk,
        deep_mine_result=deep_mine_result,
        nbop_knowledge=nbop_knowledge,
        nmno_result=nmno_result,
        log=log,
    )

    # ── FIX 4 (cont.): Merge V7 supplementary TCs with V8 output, dedup ──
    if _v7_supplement_tcs:
        # Deduplicate: remove V7 TCs whose summary overlaps >60% with V8 TCs
        import re as _re_v7
        _v8_summaries_norm = set()
        for _tc in test_cases:
            _norm = _re_v7.sub(r'^TC\d+_[A-Z]+-\d+[_ -]*', '', _tc.summary).strip().lower()
            _norm = _re_v7.sub(r'\s+', ' ', _norm)
            _v8_summaries_norm.add(_norm)

        _merged_count = 0
        for _v7_tc in _v7_supplement_tcs:
            _v7_norm = _re_v7.sub(r'^TC\d+_[A-Z]+-\d+[_ -]*', '', _v7_tc.summary).strip().lower()
            _v7_norm = _re_v7.sub(r'\s+', ' ', _v7_norm)
            # Check word overlap with each V8 TC
            _v7_words = set(_re_v7.findall(r'\b\w{4,}\b', _v7_norm))
            _is_dup = False
            for _v8_norm in _v8_summaries_norm:
                _v8_words = set(_re_v7.findall(r'\b\w{4,}\b', _v8_norm))
                if _v7_words and _v8_words:
                    _overlap = len(_v7_words & _v8_words) / max(len(_v7_words), len(_v8_words), 1)
                    if _overlap > 0.60:
                        _is_dup = True
                        break
            if not _is_dup:
                test_cases.append(_v7_tc)
                _v8_summaries_norm.add(_v7_norm)
                _merged_count += 1
        if _merged_count:
            log('[V8-ENGINE]   Merged %d unique V7 supplementary TCs (deduped %d)' % (
                _merged_count, len(_v7_supplement_tcs) - _merged_count))

    # ── Step 4: Validation ──
    log('[V8-ENGINE] Step 4: Validating zero-generic compliance...')
    suite = TestSuite(
        feature_id=feature_id,
        feature_title=feature_title,
        feature_desc=_build_feature_desc(jira, chalk),
        test_cases=test_cases,
        data_inventory=dimension_set.data_inventory,
        combination_plan=combination_plan,
        warnings=[],
        engine_version=ENGINE_VERSION,
        # Legacy fields for dashboard compatibility
        acceptance_criteria=_extract_ac_list(jira),
        scope=chalk.scope if chalk else '',
        rules=chalk.rules if chalk else '',
        channel=options.get('channel', jira.channel if jira and hasattr(jira, 'channel') else ''),
        pi=jira.pi if jira and hasattr(jira, 'pi') else '',
        # Jira metadata for Excel Summary sheet
        jira_status=getattr(jira, 'status', '') or '',
        jira_priority=getattr(jira, 'priority', '') or '',
        jira_assignee=getattr(jira, 'assignee', '') or '',
        jira_reporter=getattr(jira, 'reporter', '') or '',
        jira_labels=getattr(jira, 'labels', []) or [],
        jira_links=[{'key': l.get('key',''), 'summary': l.get('summary','')} for l in (getattr(jira, 'linked_issues', []) or [])],
        attachment_names=[a.filename for a in (getattr(jira, 'attachments', []) or [])] if hasattr(jira, 'attachments') else [],
    )

    validation_result = validate_suite(suite, log=log)

    if not validation_result.passed:
        # Log violations as warnings but still return the suite
        log('[V8-ENGINE] WARNING: Zero-generic validation found %d violations' % len(validation_result.violations))
        suite.warnings.extend(validation_result.violations)
    else:
        log('[V8-ENGINE] Zero-generic validation PASSED')

    # ── Grounding Gate: score every TC, drop those below threshold ──
    from .grounding_scorer import gate_suite, suite_grounding_pct, grounding_badge, GATE_THRESHOLD
    _gate_threshold = options.get('grounding_threshold', GATE_THRESHOLD)
    log('[V8-ENGINE] Step 4b: Grounding gate (threshold=%d)...' % _gate_threshold)
    suite.test_cases = gate_suite(suite.test_cases, threshold=_gate_threshold, log=log)
    _grounding_pct = suite_grounding_pct(suite.test_cases)
    _badge = grounding_badge(_grounding_pct)
    log('[V8-ENGINE] %s Grounding: %.1f%% | %d TCs passed gate' % (
        _badge, _grounding_pct, len(suite.test_cases)))

    # ── Build Routing Audit ──
    _api_tcs = sum(1 for tc in test_cases if tc.category != 'Negative' and classification.classification in ('api', 'hybrid'))
    _ui_tcs = sum(1 for tc in test_cases if classification.classification in ('ui', 'hybrid') and
                  any('nbop' in (s.summary or '').lower() or 'navigate' in (s.summary or '').lower() for s in tc.steps))
    _neg_tcs = sum(1 for tc in test_cases if tc.category == 'Negative')
    _data_sources_queried = []
    if nmno_result:
        _data_sources_queried.append('TMO_API_Chalk')
    if nbop_data:
        _data_sources_queried.append('NBOP_UI_Knowledge')
    _data_sources_queried.extend(['Jira_AC', 'Subtask_Mines'])

    from .data_models_v8 import RoutingAudit
    suite.routing_audit = RoutingAudit(
        classification=classification.classification,
        confidence=classification.confidence,
        matched_components=classification.api_keywords_found + classification.ui_keywords_found,
        matched_keywords=classification.api_keywords_found + classification.ui_keywords_found,
        data_sources_queried=_data_sources_queried,
        api_tcs_generated=_api_tcs if classification.classification in ('api', 'hybrid') else 0,
        ui_tcs_generated=_ui_tcs if classification.classification in ('ui', 'hybrid') else 0,
        negative_tcs_generated=_neg_tcs,
        total_tcs=len(test_cases),
    )

    # ── Summary ──
    log('═' * 60)
    log('[V8-ENGINE] Generation complete:')
    log('[V8-ENGINE]   Test cases: %d' % len(suite.test_cases))
    log('[V8-ENGINE]   Data sources: %d' % len(dimension_set.data_inventory.sources))
    log('[V8-ENGINE]   Testable items: %d' % dimension_set.data_inventory.total_testable_items)
    log('[V8-ENGINE]   Warnings: %d' % len(suite.warnings))
    log('[V8-ENGINE]   Engine version: %s' % ENGINE_VERSION)
    # Data source resolution path
    log('[V8-ENGINE]   Classification: %s' % classification.classification)
    if nmno_result:
        log('[V8-ENGINE]   TMO_API_Chalk: %d rules, %d specs' % (
            len(nmno_result.business_rules), len(nmno_result.api_specs)))
    if nbop_data:
        log('[V8-ENGINE]   NBOP UI: nav=%s' % (nbop_data.get('nav_path', 'none')))
    log('═' * 60)

    return suite


# ================================================================
# CR/BUG FIX DELEGATION
# ================================================================


def _build_cr_suite_v8(jira, chalk, parsed_docs, options, deep_mine_result, log):
    """Build a test suite for CR/bug fix tickets using the old engine's
    CR-specific path. This avoids channel/device expansion, raw Jira text
    mining, and NBOP UI knowledge injection that produce bad TCs for CRs.

    The old engine's build_test_suite() already has mature CR handling:
      - is_cr_or_bug detection
      - _build_from_jira_only CR mode (defect reproduction TCs)
      - Step 9b CR/Bug fix scope filter (cap at 8 TCs)
      - Skips UI mirror for CR tickets
      - Skips device matrix expansion for CR tickets
    """
    from .test_engine import build_test_suite as build_test_suite_v7

    log('[V8-ENGINE] CR delegation: using V7 engine CR path...')

    # Call the old engine which has proper CR handling
    v7_suite = build_test_suite_v7(
        jira=jira,
        chalk=chalk,
        parsed_docs=parsed_docs or [],
        options=options or {},
        log=log,
        deep_mine_result=deep_mine_result,
    )

    # Wrap in V8 TestSuite format for dashboard compatibility
    feature_id = jira.key if jira else ''
    feature_title = jira.summary if jira else ''

    suite = TestSuite(
        feature_id=feature_id,
        feature_title=feature_title,
        feature_desc=v7_suite.feature_desc if hasattr(v7_suite, 'feature_desc') else '',
        test_cases=v7_suite.test_cases,
        data_inventory=DataInventory(sources=[], total_testable_items=len(v7_suite.test_cases)),
        combination_plan=CombinationPlan(),
        warnings=v7_suite.warnings if hasattr(v7_suite, 'warnings') else [],
        engine_version='8.0.0-CR',
        # Legacy fields for dashboard compatibility
        acceptance_criteria=v7_suite.acceptance_criteria if hasattr(v7_suite, 'acceptance_criteria') else [],
        scope=v7_suite.scope if hasattr(v7_suite, 'scope') else '',
        rules=v7_suite.rules if hasattr(v7_suite, 'rules') else '',
        channel=options.get('channel', jira.channel if jira and hasattr(jira, 'channel') else ''),
        pi=jira.pi if jira and hasattr(jira, 'pi') else '',
        # Jira metadata
        jira_status=getattr(jira, 'status', '') or '',
        jira_priority=getattr(jira, 'priority', '') or '',
        jira_assignee=getattr(jira, 'assignee', '') or '',
        jira_reporter=getattr(jira, 'reporter', '') or '',
        jira_labels=getattr(jira, 'labels', []) or [],
        jira_links=[{'key': l.get('key', ''), 'summary': l.get('summary', '')} for l in (getattr(jira, 'linked_issues', []) or [])],
        attachment_names=[a.filename for a in (getattr(jira, 'attachments', []) or [])] if hasattr(jira, 'attachments') else [],
    )

    # Build routing audit for CR
    from .data_models_v8 import RoutingAudit
    suite.routing_audit = RoutingAudit(
        classification='cr_bug_fix',
        confidence=1.0,
        matched_components=['CR/Bug fix detected'],
        matched_keywords=['cr', 'bug', 'defect', 'not working'],
        data_sources_queried=['Jira_AC', 'Linked_Defects', 'Subtask_AC'],
        api_tcs_generated=0,
        ui_tcs_generated=0,
        negative_tcs_generated=sum(1 for tc in suite.test_cases if tc.category == 'Negative'),
        total_tcs=len(suite.test_cases),
    )

    log('[V8-ENGINE] CR delegation complete: %d TCs (capped at 8 for defect scope)' % len(suite.test_cases))
    log('═' * 60)

    return suite


# ================================================================
# HELPERS
# ================================================================


def _build_warning_report(feature_id: str, data_inventory: DataInventory) -> WarningReport:
    """Build a WarningReport when zero testable items are found."""
    guidance = []

    for source in data_inventory.sources:
        if source.status == 'empty':
            if source.source_type == 'jira':
                guidance.append('Jira AC is empty — add acceptance criteria to the ticket')
            elif source.source_type == 'chalk':
                guidance.append('No Chalk API specs found — link the API spec page in the Jira AC')
            elif source.source_type == 'subtask':
                guidance.append('Subtasks have no AC — add acceptance criteria to subtasks')
            elif source.source_type == 'attachment':
                guidance.append('No attachments found — attach LLD/HLD documents to the ticket')
            elif source.source_type == 'nbop':
                guidance.append('No NBOP UI data — ensure NBOP knowledge base covers this feature')
        elif source.status == 'failed':
            guidance.append('%s failed to load — check connectivity and retry' % source.source_name)

    if not guidance:
        guidance.append('All sources returned no testable data — verify feature has been specified')

    return WarningReport(
        feature_id=feature_id,
        sources_checked=data_inventory.sources,
        reason='No testable data found across all sources',
        guidance=guidance,
    )


def _build_feature_desc(jira, chalk) -> str:
    """Build a feature description from Jira and Chalk data."""
    parts = []
    if jira and jira.summary:
        parts.append(jira.summary)
    if jira and jira.description:
        desc = jira.description[:200] if len(jira.description or '') > 200 else (jira.description or '')
        parts.append(desc)
    if chalk and hasattr(chalk, 'scope') and chalk.scope:
        parts.append('Scope: %s' % chalk.scope[:100])
    return ' | '.join(parts) if parts else ''


def _extract_ac_list(jira) -> List[str]:
    """Extract acceptance criteria as a list from Jira."""
    if not jira or not jira.acceptance_criteria:
        return []
    ac_text = jira.acceptance_criteria
    # Split by numbered items or bullet points
    import re
    items = re.split(r'\n\s*\d+[\.\)]\s*|\n\s*[-•]\s*', ac_text)
    return [item.strip() for item in items if item.strip()]


def _extract_chalk_urls_from_ac(jira) -> List[str]:
    """Extract Chalk URLs from Jira AC text.

    Looks for URLs containing 'chalk' or TMO API Chalk patterns.
    """
    import re
    urls = []
    ac_text = ''
    if jira and hasattr(jira, 'acceptance_criteria') and jira.acceptance_criteria:
        ac_text += jira.acceptance_criteria
    if jira and hasattr(jira, 'description') and jira.description:
        ac_text += '\n' + jira.description

    if not ac_text:
        return urls

    # Find all URLs in the text
    url_pattern = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
    for match in url_pattern.finditer(ac_text):
        url = match.group(0).rstrip('.,;:)')
        if 'chalk' in url.lower() or 'tmo' in url.lower() or '/T0' in url:
            urls.append(url)

    return urls


def _gather_nbop_data(jira, log: Callable = print) -> Optional[Dict]:
    """Gather NBOP UI Knowledge data for the feature.

    Queries nbop_ui_knowledge module for navigation paths, fields, and UI elements.
    Returns None if NBOP knowledge is unavailable.
    """
    try:
        from .nbop_ui_knowledge import (
            is_available, get_navigation_path, get_page_fields,
        )
        if not is_available():
            log('[V8-ENGINE]   NBOP UI Knowledge not available (map file missing)')
            return None

        feature_name = ''
        if jira and jira.summary:
            # Extract feature name from summary
            import re
            cleaned = re.sub(r'^\[.*?\]\s*:?\s*', '', jira.summary).strip()
            cleaned = re.sub(r'^New MVNO\s*[-–—]\s*', '', cleaned).strip()
            cleaned = re.sub(r'\s*\((?:GET|POST|PUT|DELETE)[/\w]*\)\s*$', '', cleaned).strip()
            feature_name = cleaned

        if not feature_name:
            return None

        nav_path = get_navigation_path(feature_name)
        fields = []
        ui_elements = []

        # Try to get page fields if available
        if hasattr(get_page_fields, '__call__'):
            try:
                fields = get_page_fields(feature_name) or []
            except Exception:
                pass

        nbop_data = {
            'nav_path': nav_path or '',
            'fields': fields,
            'ui_elements': ui_elements,
        }

        if nav_path or fields or ui_elements:
            log('[V8-ENGINE]   NBOP: nav_path=%s, %d fields, %d elements' % (
                nav_path or 'none', len(fields), len(ui_elements)))
            return nbop_data
        else:
            log('[V8-ENGINE]   NBOP: No matching data for "%s"' % feature_name)
            return None

    except ImportError:
        log('[V8-ENGINE]   NBOP UI Knowledge module not available')
        return None
    except Exception as e:
        log('[V8-ENGINE]   NBOP lookup error: %s' % str(e)[:80])
        return None


# ================================================================
# CUSTOM INSTRUCTION HANDLING FOR V8.0
# ================================================================


# Mapping of instruction keywords to dimension values
_INSTRUCTION_DIMENSION_MAP = {
    # Products
    'tablet': ('product', 'Tablet'),
    'phone': ('product', 'Phone'),
    'smartwatch': ('product', 'Smartwatch'),
    'wearable': ('product', 'Wearable'),
    'hotspot': ('product', 'Hotspot'),
    'iot': ('product', 'IoT'),
    # SIM types
    'esim': ('sim_type', 'eSIM'),
    'psim': ('sim_type', 'pSIM'),
    'physical sim': ('sim_type', 'pSIM'),
    # Channels
    'nbop': ('channel', 'NBOP'),
    'itmbo': ('channel', 'ITMBO'),
    # Input types
    'imei': ('input_type', 'IMEI'),
    'iccid': ('input_type', 'ICCID'),
    'mdn': ('input_type', 'MDN'),
    'eid': ('input_type', 'EID'),
    'lineid': ('input_type', 'LineID'),
    # Networks
    '5g': ('network', '5G'),
    '4g': ('network', '4G'),
    'lte': ('network', 'LTE'),
}

# Keywords that indicate "add more" intent
_ADD_MORE_PATTERNS = [
    'add more', 'more', 'include', 'add', 'extra', 'additional',
    'focus on', 'emphasize', 'prioritize', 'expand',
]

# Keywords that indicate "only" / "filter" intent
_ONLY_PATTERNS = [
    'only', 'skip', 'no ', 'exclude', 'remove', 'without',
]


def _apply_custom_instructions(
    dimension_set,
    custom_text: str,
    options: Dict,
    log: Callable = print,
):
    """Apply custom instructions to the DimensionSet.

    Parses free-text instructions and:
      - Adds dimension values when user says "add more Tablet scenarios"
      - Forces specific dimensions when user says "focus on pSIM"
      - Filters out dimensions when user says "skip 4G" or "only NBOP"
      - Adds extra scenarios from explicit "Add:" lines

    Returns modified DimensionSet.
    """
    import re as _re
    t = custom_text.lower().strip()

    log('[V8-CUSTOM] Parsing: "%s"' % custom_text[:80])

    # ── Detect "add more X" patterns ──
    # These inject additional dimension values or ensure a value exists
    for keyword, (dim_name, dim_value) in _INSTRUCTION_DIMENSION_MAP.items():
        # Check if user wants MORE of this thing
        wants_more = any(
            ('%s %s' % (pattern, keyword) in t) or ('%s %s' % (keyword, pattern) in t)
            for pattern in _ADD_MORE_PATTERNS
        ) or ('%s scenario' % keyword in t and any(p in t for p in _ADD_MORE_PATTERNS))

        if wants_more:
            _ensure_dimension_value(dimension_set, dim_name, dim_value, log)

    # ── Detect "only X" / "skip X" patterns ──
    for keyword, (dim_name, dim_value) in _INSTRUCTION_DIMENSION_MAP.items():
        # "only X" — keep only this value in the dimension
        if 'only %s' % keyword in t or '%s only' % keyword in t:
            _filter_dimension_to(dimension_set, dim_name, [dim_value], log)
        # "skip X" / "no X" — remove this value from the dimension
        elif 'skip %s' % keyword in t or 'no %s' % keyword in t:
            _remove_dimension_value(dimension_set, dim_name, dim_value, log)

    # ── Detect "add more scenarios for X" — creates extra ExtractedScenarios ──
    scenario_match = _re.findall(
        r'(?:add|include|more)\s+(?:more\s+)?(\w+)\s+(?:scenario|test case|tc)s?',
        t, _re.IGNORECASE
    )
    for topic in scenario_match:
        # Check if topic maps to a known dimension value
        topic_lower = topic.lower()
        if topic_lower in _INSTRUCTION_DIMENSION_MAP:
            dim_name, dim_value = _INSTRUCTION_DIMENSION_MAP[topic_lower]
            _ensure_dimension_value(dimension_set, dim_name, dim_value, log)
            log('[V8-CUSTOM]   Ensuring %s=%s has scenarios' % (dim_name, dim_value))

    # ── Parse explicit "Add:" lines as extra scenarios ──
    for line in custom_text.split('\n'):
        line_stripped = line.strip()
        if line_stripped.lower().startswith(('add:', 'include:', 'also:')):
            desc = line_stripped.split(':', 1)[1].strip()
            if desc and len(desc) > 10:
                from .traceability import create_traceability
                from .data_models_v8 import ExtractedScenario
                tr = create_traceability(
                    source_type='Jira AC',
                    source_id='custom_instruction',
                    extracted_text='User instruction: %s' % desc[:200],
                )
                dimension_set.scenarios.append(ExtractedScenario(
                    title=desc[:120],
                    validation='Verify that %s' % desc[:100] if not desc.lower().startswith('verify') else desc[:120],
                    category='Happy Path',
                    source=tr,
                ))
                log('[V8-CUSTOM]   Added custom scenario: "%s"' % desc[:60])

    return dimension_set


def _ensure_dimension_value(dimension_set, dim_name: str, dim_value: str, log: Callable):
    """Ensure a dimension value exists. If the dimension doesn't exist, create it."""
    from .traceability import create_traceability
    from .data_models_v8 import Dimension

    # Find existing dimension
    for dim in dimension_set.dimensions:
        if dim.name == dim_name:
            if dim_value not in dim.values:
                dim.values.append(dim_value)
                log('[V8-CUSTOM]   Added %s=%s to existing dimension' % (dim_name, dim_value))
            else:
                log('[V8-CUSTOM]   %s=%s already present' % (dim_name, dim_value))
            return

    # Dimension doesn't exist — create it
    tr = create_traceability(
        source_type='Jira AC',
        source_id='custom_instruction',
        extracted_text='Custom instruction: add %s=%s' % (dim_name, dim_value),
    )
    dimension_set.dimensions.append(Dimension(
        name=dim_name,
        values=[dim_value],
        source=tr,
    ))
    log('[V8-CUSTOM]   Created new dimension %s=[%s]' % (dim_name, dim_value))


def _filter_dimension_to(dimension_set, dim_name: str, keep_values: List[str], log: Callable):
    """Filter a dimension to only keep specified values."""
    for dim in dimension_set.dimensions:
        if dim.name == dim_name:
            original = list(dim.values)
            dim.values = [v for v in dim.values if v in keep_values]
            if not dim.values:
                dim.values = keep_values  # If filter removed everything, use the requested values
            removed = set(original) - set(dim.values)
            if removed:
                log('[V8-CUSTOM]   Filtered %s: kept %s, removed %s' % (dim_name, dim.values, list(removed)))
            return


def _remove_dimension_value(dimension_set, dim_name: str, remove_value: str, log: Callable):
    """Remove a specific value from a dimension."""
    for dim in dimension_set.dimensions:
        if dim.name == dim_name:
            if remove_value in dim.values and len(dim.values) > 1:
                dim.values.remove(remove_value)
                log('[V8-CUSTOM]   Removed %s=%s' % (dim_name, remove_value))
            return
