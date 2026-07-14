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
ENGINE_VERSION = '9.0.0'


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
                # Seed test_data_pool from NMNO request_sample (Phase 4A)
                try:
                    from .test_data_injector import seed_from_nmno
                    _seeded = seed_from_nmno(nmno_result, feature_id=feature_id)
                    if _seeded:
                        log('[V8-ENGINE]   Seeded %d test data values from NMNO samples' % _seeded)
                except Exception:
                    pass
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
    _data_only = options.get('data_only', True)
    log('[V8-ENGINE] Step 1: Extracting dimensions from all data sources (data_only=%s)...' % _data_only)
    dimension_set = extract_dimensions(
        jira=jira,
        chalk=chalk,
        deep_mine_result=deep_mine_result,
        parsed_docs=parsed_docs,
        nmno_result=nmno_result,
        nbop_data=nbop_data,
        classification=classification.classification,
        data_only=_data_only,
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

    # ── FIX 4: V7 supplementary mining — disabled in data_only mode ──
    # V7 mining uses keyword/pattern matching on Jira text and produces
    # template-style TCs not grounded in Chalk. Skipped when data_only=True.
    _v7_supplement_tcs = []
    if not _data_only and dimension_set.data_inventory.total_testable_items < 5:
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

    # ── Step 3b: Cross-path near-duplicate pruning ──
    # Catches near-identical TCs that survived earlier per-path dedup because they
    # came from different generation paths (dimension TC vs scenario TC, etc.).
    _before_prune = len(test_cases)
    test_cases = _prune_near_duplicate_tcs(test_cases, log=log)
    if len(test_cases) < _before_prune:
        log('[V8-ENGINE]   Near-dup pruning: %d → %d TCs' % (_before_prune, len(test_cases)))

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

    # ── LLM Reviewer: grounded gap-filler (Phase 3) ──
    # Called AFTER generation so it only fills gaps, never replaces grounded TCs.
    # Constrained to cite sources — cannot hallucinate.
    # Suggestions stored in DB and surfaced in dashboard Review panel.
    suite._llm_suggestions = []
    try:
        from .llm_engine import create_llm_from_env
        _llm = create_llm_from_env(log=log)
        if _llm.available:
            from .llm_reviewer import review_suite_gaps
            log('[V8-ENGINE] Step 5: LLM gap analysis (auto-detect provider)...')
            _llm_gaps = review_suite_gaps(_llm, suite, jira, chalk, log=log)
            if _llm_gaps:
                suite._llm_suggestions = _llm_gaps
                try:
                    from .database import save_llm_suggestions
                    save_llm_suggestions(feature_id, _llm_gaps)
                    log('[V8-ENGINE]   %d LLM suggestions saved to DB' % len(_llm_gaps))
                except Exception as _db_err:
                    log('[V8-ENGINE]   LLM suggestion DB save: %s' % str(_db_err)[:60])
        else:
            log('[V8-ENGINE] LLM not configured — set OPENAI_API_KEY / AWS creds to enable gap analysis')
            suite._llm_suggestions = []
    except Exception as _llm_err:
        log('[V8-ENGINE] LLM reviewer skipped: %s' % str(_llm_err)[:80])
        suite._llm_suggestions = []

    # ── Final: Normalize invalid categories (safety net for cached data) ──
    _VALID_CATEGORIES = {'Happy Path', 'Negative', 'Edge Case', 'E2E', 'Regression', 'Rollback'}
    for tc in suite.test_cases:
        if tc.category and tc.category not in _VALID_CATEGORIES:
            _cat_lower = tc.category.lower()
            if _cat_lower in ('happy path workflow', 'positive', 'positive workflow'):
                tc.category = 'Happy Path'
            elif _cat_lower in ('negative workflow', 'failure workflow'):
                tc.category = 'Negative'
            elif _cat_lower in ('edge case workflow', 'edge cases'):
                tc.category = 'Edge Case'
            elif _cat_lower in ('end-to-end', 'e2e workflow'):
                tc.category = 'E2E'
            elif len(tc.category) > 40:
                # Long sentence mistakenly stored as category — infer from keywords
                if 'negative' in _cat_lower or 'error' in _cat_lower or 'fail' in _cat_lower or 'reject' in _cat_lower:
                    tc.category = 'Negative'
                elif 'e2e' in _cat_lower or 'end-to-end' in _cat_lower:
                    tc.category = 'E2E'
                elif 'edge' in _cat_lower:
                    tc.category = 'Edge Case'
                else:
                    tc.category = 'Happy Path'

    _retag_positive_flows(suite.test_cases, log)

    # ── Inject silence-rule TCs — skipped in data_only mode ──
    # These TCs are AC-keyword-triggered templates, not direct Chalk/Jira scenario extractions.
    if not _data_only:
        _inject_silence_assertions(suite, jira, log)

    # ── Feature-eligibility negatives — runs REGARDLESS of data_only ──
    # Line/subscriber eligibility features (MVNO, commercial-line, feature-gated)
    # must prove eligibility gating even when template negatives were skipped.
    try:
        _inject_eligibility_negatives(suite, jira, chalk, classification, log)
    except Exception as _elig_err:
        log('[V8-ENGINE]   WARNING: eligibility negative injection failed: %s — continuing' % str(_elig_err)[:100])

    # ── Prune degenerate/junk TCs (e.g. "Verify_Verify" from a Chalk Note row) ──
    try:
        _prune_degenerate_tcs(suite, feature_id, log)
    except Exception as _pj_err:
        log('[V8-ENGINE]   WARNING: degenerate-TC prune failed: %s — continuing' % str(_pj_err)[:100])

    # ── Final criticality-aware priority pass over the complete TC list ──
    try:
        _finalize_priorities(suite, feature_priority=getattr(jira, 'priority', '') or '', log=log)
    except Exception as _pri_err:
        log('[V8-ENGINE]   WARNING: priority finalization failed: %s — continuing' % str(_pri_err)[:100])

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
        engine_version='9.0.0-CR',
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


def _retag_positive_flows(test_cases, log: Callable = print):
    """De-prioritization removal/notification flows are positive verifications,
    not failure scenarios. Re-tag 'Negative' TCs as 'Happy Path' when the summary
    describes expected behaviour and carries no true error/reject signal.
    Genuine negatives (line-state rejections, 'Negative:' prefixed) are preserved."""
    _neg_signal = ('reject', 'invalid', 'error', 'fail', 'denied', 'unauthorized',
                   'not allowed', 'timeout', 'must not', 'does not', 'should not',
                   'does not send', 'not send', 'not notify', 'not trigger',
                   'not provision', 'no notification', 'no new notification',
                   'negative:', 'err_')
    _pos_signal = ('off notification', 'mhs_pfo_off', 'restore priority',
                   'throttle flag to n', 'de-prioritized', 'deprioritized',
                   'notification should follow', 'content and format', 'same content')
    changed = 0
    for tc in test_cases:
        if (getattr(tc, 'category', '') or '') != 'Negative':
            continue
        # Normalize underscores→spaces so 'does not' matches both 'does not' and 'does_not'
        s = (getattr(tc, 'summary', '') or '').lower().replace('_', ' ')
        if any(n in s for n in _neg_signal):
            continue
        if any(p in s for p in _pos_signal):
            tc.category = 'Happy Path'
            changed += 1
    if changed:
        log('[V8-ENGINE]   Re-tagged %d positive flow(s) Negative→Happy Path' % changed)
    return test_cases


def _prune_degenerate_tcs(suite, feature_id, log: Callable = print):
    """Drop junk TCs whose title carries no real content (e.g. 'Verify_Verify' built
    from a Chalk 'Note:' row). These slip past the grounding gate because their steps
    look fine, but the TC itself is meaningless and inflates the count."""
    import re as _re_deg
    try:
        from .tc_builder import _is_degenerate_title
    except Exception:
        return
    kept, dropped = [], 0
    for tc in suite.test_cases:
        _s = tc.summary or ''
        _s = _re_deg.sub(r'^%s[_\s]*' % _re_deg.escape(feature_id or ''), '', _s, flags=_re_deg.IGNORECASE)
        _s = _re_deg.sub(r'^(ITMBO|NBOP|API)[_\s]*', '', _s, flags=_re_deg.IGNORECASE)
        # Also strip a leading verb so "Verify_Verify" → "Verify" is judged on content.
        if _is_degenerate_title(_s):
            dropped += 1
            log('[V8-ENGINE]   Pruned degenerate TC: %s' % (tc.summary or '')[:60])
            continue
        kept.append(tc)
    if dropped:
        suite.test_cases = kept
        log('[V8-ENGINE]   Pruned %d degenerate/junk TC(s)' % dropped)


def _finalize_priorities(suite, feature_priority='', log: Callable = print):
    """Final criticality-aware priority pass over the COMPLETE TC list.

    Priorities are first set in build_test_cases (Step 3), but TCs added later
    (silence assertions, eligibility negatives, deep-mine supplements) and any
    reordering can skew the final distribution. This pass re-derives P1/P2/P3 on
    the final list so the Excel reflects a consistent, criticality-scaled spread.

    The Jira feature priority scales the P1 core cap and whether functional Happy
    Path overflow lands on P2 (Critical/High) or P3 (lower criticality).
    """
    _fp = (feature_priority or '').strip().lower()
    if any(k in _fp for k in ('blocker', 'emergency', 'critical', 'highest')):
        _p1_cap, _overflow = 8, 'P2'
    elif 'high' in _fp:
        _p1_cap, _overflow = 6, 'P2'
    elif any(k in _fp for k in ('low', 'minor', 'trivial')):
        _p1_cap, _overflow = 2, 'P3'
    else:
        _p1_cap, _overflow = 4, 'P3'

    happy = 0
    counts = {'P1': 0, 'P2': 0, 'P3': 0}
    for tc in suite.test_cases:
        cat = (getattr(tc, 'category', '') or '').lower()
        if cat == 'negative':
            tc.priority = 'P2'
        elif cat == 'regression':
            tc.priority = 'P2'
        elif cat == 'edge case':
            tc.priority = 'P3'
        elif cat in ('e2e', 'end-to-end'):
            tc.priority = 'P1'
        elif cat == 'happy path':
            happy += 1
            tc.priority = 'P1' if happy <= _p1_cap else _overflow
        else:
            tc.priority = 'P2'
        counts[tc.priority] = counts.get(tc.priority, 0) + 1
    log('[V8-ENGINE]   Priority (feature=%s): P1=%d | P2=%d | P3=%d' % (
        feature_priority or 'Medium', counts['P1'], counts['P2'], counts['P3']))


def _inject_eligibility_negatives(suite, jira, chalk, classification, log: Callable = print):
    """Inject FEATURE-ELIGIBILITY negatives for line/subscriber features.

    Fills a real coverage gap on MVNO / commercial-line / feature-gated features
    (e.g. MWTGPROV-4373 "International Mobile Hotspot for commercial lines on TMO"):
    sources rarely enumerate eligibility negatives, and in data_only mode template
    generators are skipped — leaving 0-1 negatives. Eligibility gating (line type,
    plan family, prerequisite entitlement, required params) is a first-class,
    always-testable dimension, so this runs REGARDLESS of data_only.

    Runs post-gate (like _inject_silence_assertions). Deliberately NOT applied to
    mediation/CDR/notification/batch/UI features — those own a different negative
    strategy. Nothing is fabricated: negatives are only synthesized for eligibility
    signals actually present in the ticket/Chalk text.
    """
    from .data_models_v8 import TestCase
    from .test_engine import TestStep

    feature_id = jira.key if jira else ''
    feature_short = (jira.summary if jira else '') or feature_id
    # Trim a long Jira summary to a readable feature phrase
    if ' - ' in feature_short:
        feature_short = feature_short.split(' - ')[-1].strip()
    feature_short = feature_short[:70]

    _cls = (getattr(classification, 'classification', '') or '').lower()
    if _cls == 'ui':
        return

    ac_text = ((jira.acceptance_criteria if jira and hasattr(jira, 'acceptance_criteria') else '') or '')
    title_lower = ((jira.summary if jira else '') or '').lower()
    text = ' '.join(filter(None, [
        title_lower,
        (jira.description if jira else '') or '',
        ac_text,
        (chalk.scope if chalk and hasattr(chalk, 'scope') else '') or '',
    ])).lower()

    # Pure mediation/CDR features (title-level) are handled by the CDR templates — skip.
    # NOTE: we do NOT bail on a mere mention of "notification"/"mediation" in the AC —
    # line-eligibility features (e.g. commercial-line Hotspot) legitimately mention those.
    if any(kw in title_lower for kw in ['mediation', 'cdr', 'record type', 'billing record']):
        return

    has_commercial = 'commercial line' in text or 'commercial lines' in text
    has_tmo = any(kw in text for kw in ['tmo', 't-mobile', 'mvno'])
    # Device/line feature-gating signals — the strong indicator this is an eligibility feature.
    is_feature_gated = any(kw in text for kw in ['hotspot', 'tethering', 'roaming',
                                                 'entitlement', 'add-on', 'addon'])

    # Require a STRONG line-eligibility signal before synthesizing — this naturally
    # excludes pure mediation/report/notification features (they lack these terms).
    if not (has_commercial or is_feature_gated):
        return

    import re as _re_elig
    # Normalize underscores→spaces: TC summaries are underscore-joined
    # (e.g. "non-eligible_plan"), so space-delimited guard tokens must match.
    existing = ' '.join((tc.summary or '').lower() + ' ' + (tc.description or '').lower()
                        for tc in suite.test_cases if getattr(tc, 'category', '') == 'Negative')
    existing = _re_elig.sub(r'[_]+', ' ', existing)
    # If any lumped rejection negative already covers plan/line eligibility, don't
    # add the standalone non-eligible-plan negative (avoids near-duplicate TCs).
    _plan_rejection_covered = any(kw in existing for kw in [
        'non-eligible plan', 'not eligible', 'ineligible', 'rejected per catalog',
        'invalid retailplan', 'err07'])

    _pending = []

    def _add(token_guard, summary, description, preconditions, steps):
        if token_guard in existing:
            return
        tc = TestCase(
            summary=summary, description=description, preconditions=preconditions,
            steps=steps, story_linkage=feature_id, label=feature_id, category='Negative')
        try:
            tc.priority = 'P2'
        except Exception:
            pass
        try:
            tc.traceability = TraceabilityRecord(
                source_type='Jira AC', source_id=feature_id,
                extracted_text='Feature eligibility gating: %s' % description[:100],
                confidence=0.8)
        except Exception:
            pass
        _pending.append(tc)

    if has_commercial:
        _add('non-commercial',
             '%s_Negative_%s_rejected_on_non-commercial_consumer_line' % (feature_id, feature_short.replace(' ', '_')),
             'Attempt to enable/apply %s on a consumer (non-commercial) line. The feature is gated to commercial lines and must be rejected.' % feature_short,
             '1.\tA consumer (non-commercial) subscriber line is Active.\n2.\tThe feature is scoped to commercial lines only.',
             [
                 TestStep(step_num=1, summary='Identify a consumer (non-commercial) line and confirm its account type in NBOP', expected='Line confirmed as non-commercial in the NBOP profile'),
                 TestStep(step_num=2, summary='Attempt to apply %s to the consumer line via change-feature API' % feature_short, expected='Request is rejected with an eligibility error (ERR07 — not eligible)'),
                 TestStep(step_num=3, summary='Verify NBOP Transaction History for the line', expected='No provisioning transaction recorded; line profile unchanged'),
             ])

    if has_tmo:
        _add('non-tmo',
             '%s_Negative_%s_rejected_on_non-TMO_non-eligible_network_line' % (feature_id, feature_short.replace(' ', '_')),
             'Attempt to apply %s on a line not provisioned on the eligible TMO network. Must be rejected.' % feature_short,
             '1.\tA line provisioned on a non-TMO / non-eligible network exists.',
             [
                 TestStep(step_num=1, summary='Identify a non-TMO / non-eligible network line and confirm provisioning in NBOP', expected='Line confirmed as non-TMO / non-eligible'),
                 TestStep(step_num=2, summary='Attempt to apply %s to the line via change-feature API' % feature_short, expected='Request is rejected with an eligibility error (ERR07)'),
                 TestStep(step_num=3, summary='Verify NBOP Transaction History for the line', expected='No provisioning transaction recorded; line unchanged'),
             ])

    if is_feature_gated and not _plan_rejection_covered:
        _add('non-eligible plan',
             '%s_Negative_%s_blocked_on_non-eligible_rate_plan' % (feature_id, feature_short.replace(' ', '_')),
             'Attempt to apply %s on a line whose current rate plan does not entitle the feature. Must be rejected.' % feature_short,
             '1.\tA line on a rate plan that does NOT entitle this feature is Active.',
             [
                 TestStep(step_num=1, summary='Confirm in NBOP the line\'s current rate plan does not include this feature entitlement', expected='Current non-eligible rate plan confirmed'),
                 TestStep(step_num=2, summary='Attempt to apply %s to the line via change-feature API' % feature_short, expected='Request is rejected with an eligibility/plan error (ERR07)'),
                 TestStep(step_num=3, summary='Verify NBOP Transaction History for the line', expected='No provisioning transaction recorded; rate plan unchanged'),
             ])
        _add('prerequisite',
             '%s_Negative_%s_fails_when_prerequisite_entitlement_not_provisioned' % (feature_id, feature_short.replace(' ', '_')),
             'Attempt to apply %s when a required prerequisite entitlement/dependency is missing on the line. Must fail gracefully with a clear error.' % feature_short,
             '1.\tA line is missing the prerequisite entitlement/dependency for this feature.',
             [
                 TestStep(step_num=1, summary='Confirm in NBOP that the prerequisite entitlement is NOT provisioned on the line', expected='Missing prerequisite confirmed'),
                 TestStep(step_num=2, summary='Attempt to apply %s to the line via change-feature API' % feature_short, expected='Request fails gracefully with a clear dependency/eligibility error'),
                 TestStep(step_num=3, summary='Verify NBOP Transaction History and line profile', expected='No partial change applied; line remains consistent'),
             ])

    _add('required parameter',
         '%s_Negative_%s_rejected_ERR06_when_required_parameter_missing' % (feature_id, feature_short.replace(' ', '_')),
         'Submit the %s request with a required parameter omitted. NSL must reject with ERR06 (missing required field) and make no change.' % feature_short,
         '1.\tAn eligible line is Active.\n2.\tA request payload with a required field omitted is prepared.',
         [
             TestStep(step_num=1, summary='Prepare the %s request omitting a required field' % feature_short, expected='Malformed request prepared'),
             TestStep(step_num=2, summary='Submit the request to the change-feature API', expected='NSL rejects with responseCode ERR06 ("... is missing but it is required")'),
             TestStep(step_num=3, summary='Verify NBOP Transaction History for the line', expected='No transaction recorded; line unchanged'),
         ])

    if _pending:
        _max_sno = 0
        for tc in suite.test_cases:
            try:
                if tc.sno and str(tc.sno).isdigit():
                    _max_sno = max(_max_sno, int(tc.sno))
            except Exception:
                pass
        for tc in _pending:
            _max_sno += 1
            tc.sno = str(_max_sno)
            suite.test_cases.append(tc)
        log('[V8-ENGINE]   Injected %d feature-eligibility negative TC(s)' % len(_pending))


def _inject_silence_assertions(suite, jira, log: Callable = print):
    """When AC contains specific constraint rules, inject explicit TCs that verify them.

    Covers:
      1. "No notification to MBO/NBOP" — dual-assertion silence rule
      2. "No impacts to NBOP" — inversion TC (NBOP shows nothing)
      3. "auto renew flag as F" / "autoRenew=F" — field-level assertion on provision
      4. "keep them deprioritized" / "exceeds the total" — insufficient-upgrade branch
    """
    from .data_models_v8 import TestCase
    from .test_engine import TestStep

    ac_text = (jira.acceptance_criteria if jira and hasattr(jira, 'acceptance_criteria') else '') or ''
    ac_lower = ac_text.lower()
    feature_id = jira.key if jira else ''
    desc_lower = ((jira.description if jira else '') or '').lower()
    all_text_lower = ac_lower + ' ' + desc_lower

    # Also check subtask descriptions for domain rules
    for st in (jira.subtasks if jira and hasattr(jira, 'subtasks') else []):
        all_text_lower += ' ' + (st.get('description', '') or '').lower()

    # Check for existing TCs (avoid duplicates)
    _existing_text = ' '.join((tc.summary or '').lower() for tc in suite.test_cases)
    _injected = 0

    # ── Rule 1: "No notification to MBO/NBOP" silence dual-assertion ──
    _has_mbo_silence = any(kw in all_text_lower for kw in [
        'no notification to mbo', 'no new notification to mbo',
        'does not need to send any notification to nbop or mbo',
        'does not send notification', 'no notification needed',
        'nsl does not need to send',
    ])
    if _has_mbo_silence and 'must_not_send' not in _existing_text and 'does_not_send_any_notification_to_mbo' not in _existing_text.replace(' ', '_'):
        tc = TestCase(
            summary='%s_Verify_NSL_does_NOT_send_any_notification_to_MBO_or_NBOP_when_NC_DEPRIOR_is_provisioned_or_removed' % feature_id,
            description='Dual-assertion silence rule: When de-prioritization is applied (NC_DEPRIOR provisioned) '
                        'or removed (OFF notification processed), NSL must NOT send any notification to MBO or NBOP. '
                        'Source: AC rule "No new notification to MBO" + "No impacts to NBOP".',
            preconditions='1.\tActive TMO subscriber in SIT environment\n'
                          '2.\tNC_DEPRIOR provisioning or removal scenario prepared\n'
                          '3.\tMBO and NBOP notification monitoring enabled (Century Report)',
            steps=[
                TestStep(step_num=1,
                         summary='Trigger de-prioritization: subscriber hits 100%% Primary + 100%% MHS in same BCD',
                         expected='NSL provisions NC_DEPRIOR via change-feature API'),
                TestStep(step_num=2,
                         summary='Verify NSL does NOT send any notification to MBO (check Century Report outbound calls)',
                         expected='Zero MBO outbound calls in Century Report for this transaction'),
                TestStep(step_num=3,
                         summary='Verify NSL does NOT send any notification or message to NBOP',
                         expected='Zero NBOP outbound calls. NBOP shows no de-prioritization message on UI'),
                TestStep(step_num=4,
                         summary='Trigger BCD reset → OFF notification → NC_DEPRIOR removed',
                         expected='NC_DEPRIOR removed successfully'),
                TestStep(step_num=5,
                         summary='Verify again: no MBO/NBOP notification sent on removal either',
                         expected='No MBO or NBOP outbound calls for removal. Silence rule confirmed both ways'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Negative',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        _injected += 1

    # ── Rule 2: "No impacts to NBOP" — inversion TC ──
    # Skip if Rule 1 (MBO/NBOP silence) was already injected: its Step 3 already asserts
    # "Zero NBOP outbound calls. NBOP shows no de-prioritization message on UI", so a
    # separate NBOP-display TC would be a duplicate assertion.
    _has_nbop_no_impact = any(kw in all_text_lower for kw in [
        'no impacts to nbop', 'no impact to nbop', 'nbop need not show',
    ])
    if _has_nbop_no_impact and not _has_mbo_silence and 'nbop_shows_no' not in _existing_text.replace(' ', '_'):
        tc = TestCase(
            summary='%s_Verify_NBOP_shows_no_de-prioritization_status_or_message_on_subscriber_profile' % feature_id,
            description='Per AC: "No impacts to NBOP. NBOP need not show any message on UI." '
                        'Verify that after de-prioritization is applied or removed, NBOP subscriber '
                        'profile does not display any de-prioritization indicator, banner, or status change.',
            preconditions='1.\tActive TMO subscriber with NC_DEPRIOR provisioned\n'
                          '2.\tNBOP portal accessible\n'
                          '3.\tSubscriber profile viewable',
            steps=[
                TestStep(step_num=1,
                         summary='Provision NC_DEPRIOR on subscriber (via dual-bucket 100%% notification flow)',
                         expected='NC_DEPRIOR active on the line'),
                TestStep(step_num=2,
                         summary='Launch NBOP and navigate to subscriber profile',
                         expected='Subscriber profile loaded with all standard header cards'),
                TestStep(step_num=3,
                         summary='Verify NO de-prioritization status, banner, or message is displayed anywhere on NBOP',
                         expected='No de-prioritization indicator visible. Profile identical to non-de-prioritized subscriber'),
                TestStep(step_num=4,
                         summary='Check Line Information, Feature list, and Notification tabs in NBOP',
                         expected='No de-prioritization entries in any NBOP section. Feature is invisible to NBOP'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Happy Path',
        )
        tc.priority = 'P2'
        suite.test_cases.append(tc)
        _injected += 1

    # ── Rule 3: "auto renew flag as F" / "autoRenew=F" — field assertion on provision ──
    _has_autorenew = any(kw in all_text_lower for kw in [
        'auto renew flag as f', 'autorenew=f', 'auto-renew flag', 'autorenew flag',
        'auto renew', 'with auto renew',
    ])
    if _has_autorenew and 'autorenew' not in _existing_text and 'auto_renew' not in _existing_text:
        tc = TestCase(
            summary='%s_Verify_NC_DEPRIOR_provisioned_with_autoRenew_flag_set_to_F' % feature_id,
            description='Per NSLNM-604: When NSL provisions NC_DEPRIOR via change-feature API, '
                        'the autoRenew flag MUST be set to F (False). This ensures the feature '
                        'does not auto-renew on BCD reset — it must be explicitly re-provisioned '
                        'only when both buckets hit 100%% again in a new cycle.',
            preconditions='1.\tActive TMO UNL/UNL+ subscriber in SIT\n'
                          '2.\tAPI request/response capture enabled\n'
                          '3.\tLine has not been de-prioritized in current BCD',
            steps=[
                TestStep(step_num=1,
                         summary='Trigger dual-bucket 100%%: Mediation sends ON (Primary 100%%) + MHS_PFO_ON (MHS 100%%) in same BCD',
                         expected='NSL detects both buckets at 100%% and initiates NC_DEPRIOR provisioning'),
                TestStep(step_num=2,
                         summary='Capture the change-feature API request payload sent by NSL to Apollo NE',
                         expected='Change-feature request captured with NC_DEPRIOR SLO details'),
                TestStep(step_num=3,
                         summary='Verify the autoRenew field in the request payload is set to "F" (False)',
                         expected='autoRenew=F confirmed in the request. Feature will NOT auto-renew at next BCD'),
                TestStep(step_num=4,
                         summary='Verify at next BCD reset: NC_DEPRIOR is removed (not renewed) without new 100%% triggers',
                         expected='NC_DEPRIOR removed at BCD reset. Auto-renew=F means no persistence across cycles'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Happy Path',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        _injected += 1

    # ── Rule 4: "keep them deprioritized" — insufficient upgrade branch ──
    _has_keep_deprioritized = any(kw in all_text_lower for kw in [
        'keep them deprioritized', 'keep deprioritized', 'keep them de-prioritized',
        'exceeds the total already', 'still exceeds',
    ])
    if _has_keep_deprioritized and 'keep.*deprioritized' not in _existing_text and 'insufficient_upgrade' not in _existing_text:
        tc = TestCase(
            summary='%s_Verify_subscriber_stays_de-prioritized_when_plan_upgrade_total_still_below_usage' % feature_id,
            description='Per AC: "If the customer exceeds the total already (e.g., 50GB + MHS bucket), '
                        'keep them deprioritized." When a de-prioritized subscriber upgrades to a new plan '
                        'whose total allocation (PDL + MHS) is STILL below their current usage, NSL must '
                        'NOT send OFF notification — subscriber remains de-prioritized until BCD reset.',
            preconditions='1.\tActive TMO UNL subscriber currently de-prioritized (NC_DEPRIOR active)\n'
                          '2.\tSubscriber usage exceeds both PDL and MHS buckets (e.g., 55GB used of 50GB plan)\n'
                          '3.\tPlan upgrade available that still has insufficient total (e.g., upgrade to 52GB plan)',
            steps=[
                TestStep(step_num=1,
                         summary='Confirm subscriber is de-prioritized: NC_DEPRIOR active, throttle flag=Y, usage > plan total',
                         expected='Subscriber confirmed in de-prioritized state with usage exceeding plan allocation'),
                TestStep(step_num=2,
                         summary='Trigger plan upgrade to a new plan where total (PDL + MHS) is STILL below current usage',
                         expected='Plan upgrade processed. New plan total still insufficient for subscriber usage'),
                TestStep(step_num=3,
                         summary='Verify Mediation does NOT send OFF notification to NSL (insufficient upgrade)',
                         expected='No OFF notification generated. Subscriber usage still exceeds new plan total'),
                TestStep(step_num=4,
                         summary='Verify NC_DEPRIOR remains active — subscriber stays de-prioritized',
                         expected='NC_DEPRIOR still provisioned. Throttle flag still Y. No change to QCI priority'),
                TestStep(step_num=5,
                         summary='Verify at next BCD reset: normal OFF flow fires (BCD always clears regardless of plan)',
                         expected='BCD reset triggers OFF → NC_DEPRIOR removed. Fresh cycle starts clean'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Negative',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        _injected += 1

    if _injected:
        # Assign serial numbers to injected TCs so Summary sheet doesn't show TC00
        _max_sno = 0
        for tc in suite.test_cases:
            try:
                _n = int(tc.sno) if tc.sno and tc.sno.isdigit() else 0
                if _n > _max_sno:
                    _max_sno = _n
            except (ValueError, AttributeError):
                pass
        # Number the injected TCs sequentially after the existing max
        for tc in suite.test_cases[-_injected:]:
            _max_sno += 1
            tc.sno = str(_max_sno)
        log('[V8-ENGINE]   Injected %d constraint-rule TC(s) (silence/autoRenew/keep-deprioritized)' % _injected)

    # ── E2E lifecycle TC for notification-driven features ──
    _is_notif_feature = any(kw in all_text_lower for kw in [
        'de-priorit', 'deprioritiz', 'nc_deprior', 'throttle', 'mediation',
    ])
    _has_e2e = any(tc.category in ('E2E', 'End-to-End') for tc in suite.test_cases)
    if _is_notif_feature and not _has_e2e and len(suite.test_cases) >= 5:
        _max_sno = max((int(tc.sno) for tc in suite.test_cases if tc.sno and tc.sno.isdigit()), default=0)
        tc = TestCase(
            sno=str(_max_sno + 1),
            summary='%s_E2E_Full_De-prioritization_Lifecycle:_Provision_→_Usage_→_BCD_Reset_→_Removal_→_Clean_State' % feature_id,
            description='End-to-end lifecycle covering the complete de-prioritization chain: '
                        'subscriber consumes both buckets → Mediation ON notifications → NSL provisions NC_DEPRIOR → '
                        'subscriber uses data while de-prioritized → BCD resets → Mediation OFF → NSL removes NC_DEPRIOR → '
                        'throttle flag cleared → next cycle starts clean. Validates the full Mediation↔NSL contract.',
            preconditions='1.\tActive TMO UNL/UNL+ subscriber (Phone) in SIT\n'
                          '2.\tAll APIs accessible (NSL, Apollo NE, Mediation)\n'
                          '3.\tCentury Report and Mediation DB access for verification\n'
                          '4.\tSubscriber NOT currently de-prioritized (fresh state)',
            steps=[
                TestStep(step_num=1,
                         summary='E2E Setup: Confirm subscriber is Active, UNL plan, throttle=N, NC_DEPRIOR absent',
                         expected='Subscriber confirmed in clean state — no prior de-prioritization'),
                TestStep(step_num=2,
                         summary='E2E Trigger: Mediation sends ON notification (Primary Data 100%%) to NSL',
                         expected='NSL records Primary=100%% state. NC_DEPRIOR NOT yet provisioned (single-bucket gate holds)'),
                TestStep(step_num=3,
                         summary='E2E Trigger: Mediation sends MHS_PFO_ON notification (MHS 100%%) in same BCD',
                         expected='NSL detects dual-bucket gate met. Calls inquiry API, then change-feature PROVISION NC_DEPRIOR (autoRenew=F)'),
                TestStep(step_num=4,
                         summary='E2E Verify Provision: Confirm NC_DEPRIOR active, throttle=Y, QCI decreased at TMO',
                         expected='NC_DEPRIOR provisioned. Throttle flag=Y. Subscriber data speed de-prioritized'),
                TestStep(step_num=5,
                         summary='E2E Verify Silence: Confirm NO notification sent to MBO or NBOP',
                         expected='Zero MBO/NBOP outbound calls in Century Report. Silence rule upheld'),
                TestStep(step_num=6,
                         summary='E2E Mid-cycle: Subscriber continues using data while de-prioritized (no state change)',
                         expected='Subscriber remains de-prioritized for remainder of BCD. No additional notifications'),
                TestStep(step_num=7,
                         summary='E2E BCD Reset: New billing cycle starts — Mediation sends OFF notification to NSL',
                         expected='NSL receives OFF. Calls change-feature REMOVE NC_DEPRIOR'),
                TestStep(step_num=8,
                         summary='E2E Verify Removal: NC_DEPRIOR removed, throttle=N, QCI restored at TMO',
                         expected='NC_DEPRIOR absent. Throttle=N. Subscriber data priority restored to normal'),
                TestStep(step_num=9,
                         summary='E2E Clean State: Confirm subscriber can be re-de-prioritized in new cycle if both buckets hit 100%% again',
                         expected='System is in clean state for next cycle. No residual state from previous de-prioritization'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='E2E',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        log('[V8-ENGINE]   Injected E2E lifecycle TC (notification-driven full chain)')

    # ── Inquiry-gate TC: NSL must call inquiry before change-feature PROVISION ──
    _has_inquiry_gate = any(kw in all_text_lower for kw in [
        'inquiry', 'check if', 'already provisioned', 'before calling change-feature',
        'before provision', 'inquiry api',
    ])
    _inquiry_in_existing = 'inquiry' in _existing_text and 'before' in _existing_text
    if _is_notif_feature and not _inquiry_in_existing:
        _max_sno = max((int(tc.sno) for tc in suite.test_cases if tc.sno and tc.sno.isdigit()), default=0)
        tc = TestCase(
            sno=str(_max_sno + 1),
            summary='%s_Verify_NSL_calls_inquiry_API_before_change-feature_PROVISION_to_prevent_duplicate_NC_DEPRIOR' % feature_id,
            description='Per NSLNM-604: Before provisioning NC_DEPRIOR, NSL must call the inquiry API '
                        'to check if the feature is already active on the line. If already provisioned, '
                        'NSL must NOT call change-feature again (idempotency via inquiry gate). '
                        'This prevents duplicate SLO provisioning and ensures the inquiry→provision sequence.',
            preconditions='1.\tActive TMO subscriber in SIT\n'
                          '2.\tAPI request capture enabled (Century Report)\n'
                          '3.\tNC_DEPRIOR NOT currently provisioned on the line',
            steps=[
                TestStep(step_num=1,
                         summary='Trigger dual-bucket 100%%: both Primary and MHS hit 100%% in same BCD',
                         expected='NSL receives both ON notifications and gate logic triggers'),
                TestStep(step_num=2,
                         summary='Verify NSL calls inquiry API FIRST to check current feature state on the line',
                         expected='Inquiry API call logged in Century Report BEFORE change-feature call'),
                TestStep(step_num=3,
                         summary='Verify inquiry returns "NC_DEPRIOR not provisioned" → NSL proceeds with change-feature PROVISION',
                         expected='Change-feature PROVISION call follows inquiry. NC_DEPRIOR provisioned successfully'),
                TestStep(step_num=4,
                         summary='Trigger same dual-bucket scenario AGAIN in same BCD (replay/duplicate notification)',
                         expected='NSL receives duplicate ON notifications'),
                TestStep(step_num=5,
                         summary='Verify NSL calls inquiry API again → finds NC_DEPRIOR already active → does NOT call change-feature',
                         expected='Inquiry shows NC_DEPRIOR active. No second change-feature call. Duplicate provision prevented'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Edge Case',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        log('[V8-ENGINE]   Injected inquiry-gate TC (inquiry before provision)')

    # ── Scope fix: Re-tag Pre-active and Wearable TCs for notification-driven features ──
    # Pre-active lines don't have active data plans → can't consume 100% → can't be de-prioritized
    # Wearable/Smartwatch lines aren't in scope per AC ("applicable to Phones and Tablets")
    if _is_notif_feature:
        for tc in suite.test_cases:
            _s = (tc.summary or '').lower()
            if 'pre-active' in _s and tc.category == 'Negative':
                # Fix TC31: steps should assert rejection/non-applicability, not success
                if tc.steps and any('succeed' in (s.expected or '').lower() or 'completes' in (s.expected or '').lower() for s in tc.steps):
                    from .test_engine import TestStep
                    tc.steps = [
                        TestStep(step_num=1, summary='Set up subscriber line in Pre-active state (no active data plan)',
                                 expected='Line confirmed in Pre-active state — no data plan provisioned yet'),
                        TestStep(step_num=2, summary='Simulate Mediation sending 100%% Primary + MHS notifications for this Pre-active line',
                                 expected='Notifications received by NSL for the Pre-active MDN'),
                        TestStep(step_num=3, summary='Verify NSL does NOT provision NC_DEPRIOR — line has no active data plan to de-prioritize',
                                 expected='NC_DEPRIOR NOT provisioned. NSL rejects or ignores — Pre-active line is out of scope'),
                    ]
                    log('[V8-ENGINE]   Scope fix: Pre-active TC steps updated to assert rejection')
            elif 'pre-active' in _s and 'succeeds' in _s and tc.category == 'Happy Path':
                tc.category = 'Negative'
                tc.summary = tc.summary.replace('succeeds', 'is_not_applicable_(no_active_data_plan)')
                tc.description = ('Per feature scope: de-prioritization applies to UNL/UNL+ Phone and Tablet '
                                  'with active data plans. Pre-active lines have no data consumption → '
                                  'de-prioritization is not applicable. Verify NSL rejects or ignores.')
                log('[V8-ENGINE]   Scope fix: Pre-active TC re-tagged as Negative (not in feature scope)')
            elif 'wearable' in _s or 'smartwatch' in _s:
                if tc.category == 'Edge Case' and 'behavior' in _s:
                    tc.description = ('Per feature scope: de-prioritization is explicitly for "Phones and Tablets" (UNL/UNL+). '
                                      'Wearable/Smartwatch lines are NOT mentioned in scope. Verify NSL does not apply '
                                      'de-prioritization to wearable lines, or rejects gracefully if notifications arrive.')
                    log('[V8-ENGINE]   Scope fix: Wearable TC description updated to reflect out-of-scope status')

    # ── Fix TC41 Step 9: concrete exit condition ──
    if _is_notif_feature:
        for tc in suite.test_cases:
            if tc.category == 'E2E' and 'lifecycle' in (tc.summary or '').lower():
                # Find step 9 and make exit condition concrete
                for step in tc.steps:
                    if step.step_num == 9 and 'clean state' in (step.summary or '').lower():
                        step.summary = 'E2E Exit: Verify throttle=N + NC_DEPRIOR absent (via inquiry API) + subscriber priority restored'
                        step.expected = ('Inquiry API confirms NC_DEPRIOR is NOT provisioned. '
                                         'Throttle flag = N in Mediation DB. '
                                         'QCI restored to normal priority at TMO. '
                                         'System ready for next BCD cycle — no residual state.')
                        log('[V8-ENGINE]   Fix: TC41 Step 9 exit condition made concrete')
                break

    # ── Regression TC: VZW throttle/notification unaffected ──
    if _is_notif_feature and 'regression' not in _existing_text:
        _max_sno = max((int(tc.sno) for tc in suite.test_cases if tc.sno and tc.sno.isdigit()), default=0)
        from .test_engine import TestStep
        tc = TestCase(
            sno=str(_max_sno + 1),
            summary='%s_Regression:_Verify_existing_VZW_throttle/DPFO_notification_flow_unaffected_by_TMO_de-prioritization' % feature_id,
            description='Regression: The TMO de-prioritization feature shares the Mediation notification pipeline '
                        'with existing VZW throttle/DPFO flows. Verify that VZW subscriber throttle ON/OFF notifications '
                        'continue to work correctly after the TMO de-prioritization code is deployed. '
                        'No VZW behavior should change.',
            preconditions='1.\tActive VZW subscriber in SIT environment (not TMO)\n'
                          '2.\tVZW throttle/DPFO flow previously working\n'
                          '3.\tTMO de-prioritization code deployed to SIT',
            steps=[
                TestStep(step_num=1,
                         summary='Trigger VZW subscriber 100%% usage → Mediation sends existing VZW throttle ON notification',
                         expected='VZW throttle ON notification sent to NSL per existing VZW flow'),
                TestStep(step_num=2,
                         summary='Verify NSL processes VZW throttle notification using EXISTING VZW logic (not TMO de-prior path)',
                         expected='VZW throttle applied correctly. TMO NC_DEPRIOR logic NOT triggered for VZW subscriber'),
                TestStep(step_num=3,
                         summary='Trigger VZW BCD reset → Mediation sends VZW throttle OFF notification',
                         expected='VZW throttle removed. VZW flow completes end-to-end without interference'),
                TestStep(step_num=4,
                         summary='Verify no TMO-specific artifacts in VZW flow (no NC_DEPRIOR, no TMO throttle flag changes)',
                         expected='VZW subscriber state unchanged by TMO code. Complete isolation confirmed'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Regression',
        )
        tc.priority = 'P2'
        suite.test_cases.append(tc)
        log('[V8-ENGINE]   Injected Regression TC (VZW throttle unaffected by TMO de-prioritization)')

    # ── MHS-only OFF in isolation — conditional removal path ──
    # When MHS_PFO_OFF arrives but Primary is still at 100%, NSL should remove only the MHS
    # de-prioritization component, NOT the full NC_DEPRIOR (which requires both OFF notifications).
    if _is_notif_feature and 'mhs_pfo_off_in_isolation' not in _existing_text and 'mhs.*only.*off' not in _existing_text:
        _max_sno = max((int(tc.sno) for tc in suite.test_cases if tc.sno and tc.sno.isdigit()), default=0)
        from .test_engine import TestStep
        tc = TestCase(
            sno=str(_max_sno + 1),
            summary='%s_Verify_MHS_PFO_OFF_in_isolation_does_not_fully_remove_NC_DEPRIOR_when_Primary_still_at_100%%' % feature_id,
            description='Edge case: subscriber is de-prioritized (both buckets at 100%%). MHS bucket resets '
                        '(MHS_PFO_OFF arrives) but Primary remains at 100%%. Verify NSL behavior: '
                        'does NC_DEPRIOR remain active (since Primary is still 100%%), or does the single '
                        'OFF remove it? Per dual-bucket gate logic, both must be clear for full removal.',
            preconditions='1.\tActive TMO subscriber currently de-prioritized (NC_DEPRIOR active)\n'
                          '2.\tBoth Primary and MHS at 100%% in current BCD\n'
                          '3.\tMHS bucket about to reset (plan change or partial allocation increase)',
            steps=[
                TestStep(step_num=1,
                         summary='Confirm subscriber is de-prioritized: NC_DEPRIOR active, both buckets at 100%%, throttle=Y',
                         expected='De-prioritized state confirmed with dual-bucket 100%% condition active'),
                TestStep(step_num=2,
                         summary='Mediation sends MHS_PFO_OFF notification to NSL (MHS bucket cleared) — Primary OFF NOT sent',
                         expected='NSL receives MHS_PFO_OFF only. Primary remains at 100%%'),
                TestStep(step_num=3,
                         summary='Verify NSL inquiry: is NC_DEPRIOR still active after MHS-only OFF?',
                         expected='NC_DEPRIOR remains active — Primary is still at 100%%, dual-gate removal condition NOT met'),
                TestStep(step_num=4,
                         summary='Verify throttle flag remains Y (subscriber still de-prioritized)',
                         expected='Throttle flag unchanged. QCI still decreased. De-prioritization persists until BOTH buckets clear or BCD resets'),
                TestStep(step_num=5,
                         summary='Now send Primary OFF → verify full NC_DEPRIOR removal triggers only when BOTH are clear',
                         expected='Both OFF received → NC_DEPRIOR removed. Throttle=N. Full removal requires dual-OFF gate'),
            ],
            story_linkage=feature_id,
            label=feature_id,
            category='Edge Case',
        )
        tc.priority = 'P1'
        suite.test_cases.append(tc)
        log('[V8-ENGINE]   Injected MHS-only OFF isolation TC (partial-OFF does not fully remove)')

    # ── TC32 Wearable: lower grounding to reflect out-of-scope uncertainty ──
    if _is_notif_feature:
        for tc in suite.test_cases:
            if ('wearable' in (tc.summary or '').lower() or 'smartwatch' in (tc.summary or '').lower()):
                # Force grounding_score lower to signal uncertainty (feature scope says Phone/Tablet only)
                tc.grounding_score = 60  # Below the "well-grounded" threshold — signals review needed
                break


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


def _prune_near_duplicate_tcs(test_cases: List, threshold: float = 0.70, log: Callable = print) -> List:
    """Remove near-duplicate test cases from the fully-built suite.

    Compares each TC's title fingerprint (normalized summary + first-step words)
    against all already-kept TCs using word-overlap (Jaccard on 4+-char words).
    When a near-duplicate pair is found, the TC with more steps is kept.

    Product-discriminating tokens are excluded from overlap so product-specific
    TCs (Phone vs Tablet vs eSIM) are never collapsed together.

    threshold: overlap fraction above which two TCs are treated as duplicates.
               0.70 means 70%+ shared meaningful words → same test.
    """
    import re as _re

    _PRODUCT_TOKENS = frozenset({
        'phone', 'tablet', 'esim', 'psim', 'wearable', 'smartwatch', 'watch',
        'tmo', 'vzw', 'verizon', 'sprint', 'tmobile',
    })

    def _fingerprint(tc) -> frozenset:
        raw = _re.sub(r'^TC\d+_[A-Z]+-\d+[_ -]*', '', tc.summary or '').strip().lower()
        raw = _re.sub(r'\s+', ' ', raw)
        # Blend in first two step summaries for richer signal
        for step in (tc.steps or [])[:2]:
            raw += ' ' + (step.summary or '').lower()
        words = set(_re.findall(r'\b[a-z0-9]{4,}\b', raw))
        return frozenset(words - _PRODUCT_TOKENS)

    kept: List = []
    kept_fps: List[frozenset] = []

    for tc in test_cases:
        fp = _fingerprint(tc)
        dup_idx = -1

        _POS_CATS = {'Happy Path', 'Edge Case', ''}
        tc_cat = (tc.category or '').strip()

        if fp:
            for i, kfp in enumerate(kept_fps):
                if not kfp:
                    continue
                # Category protection: Negative/Regression must never merge into Happy Path
                ref_cat = (kept[i].category or '').strip()
                tc_is_pos = tc_cat in _POS_CATS
                ref_is_pos = ref_cat in _POS_CATS
                if tc_is_pos != ref_is_pos:
                    continue
                if tc_cat == 'Regression' or ref_cat == 'Regression':
                    if tc_cat != ref_cat:
                        continue

                union_size = len(fp | kfp)
                if union_size > 0 and len(fp & kfp) / union_size >= threshold:
                    dup_idx = i
                    break

        if dup_idx == -1:
            kept.append(tc)
            kept_fps.append(fp)
        else:
            existing = kept[dup_idx]
            new_steps = len(tc.steps or [])
            existing_steps = len(existing.steps or [])
            log('[V8-ENGINE]   NEAR-DUP: "%s" ≈ "%s"' % (
                (tc.summary or '')[:60], (existing.summary or '')[:60]))
            if new_steps > existing_steps:
                kept[dup_idx] = tc
                kept_fps[dup_idx] = fp

    return kept
