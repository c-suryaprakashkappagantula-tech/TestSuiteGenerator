"""
analyst_validity.py — STEP 2 validity/reconciliation gate on the COMBINED
(grounded + analyst) test-suite set for the V8 data-first engine.

This module is pure and deterministic: no I/O, no network, no LLM. It is invoked
ONLY when Analyst mode is ON (options['data_only'] is False); in the default
data-only mode the engine does not call it, so grounded-only output stays
byte-comparable.

Responsibilities (see FEAT-002):

  * build_feature_evidence(jira, chalk) -> a lowercased evidence blob built from
    the feature's own sources (feature title + description + Jira AC + Chalk
    scenarios/scope/rules/obligations). Reuses dimension_extractor._chalk_obligation_text
    for the Chalk part.

  * scenario_is_valid(scenario_or_tc, evidence_blob) -> (verdict, reason) where
    verdict is 'keep' | 'flag' | 'drop'. It classifies the analyst subject from the
    traceability source_id and checks that subject against the feature's own evidence.
    qmetry_pattern_library.concept_grounded is used as a coarse pre-check only.

  * apply_analyst_validity_gate(suite, jira, chalk, log) -> runs the gate over the
    combined set: grounded TCs are ALWAYS kept; unsupported analyst TCs are dropped;
    borderline analyst TCs are flagged needs_review; then the existing humanizer
    passes (dedup/flag/priority/reorder) run over the combined kept set with the
    Chalk-first invariant preserved.

The analyst generators (test_analyst.py D1/D2/A1/A2/A3) stamp a distinctive
traceability source_id prefix on every scenario they produce:

    State-Transition-Matrix-<feature>   (D1 — state transition)
    Partial-Failure-Matrix-<feature>    (D2 — downstream partial failure)
    Field-Validation-Matrix-<feature>   (A1 — field validation)
    Idempotency-<feature>               (A2 — write idempotency)
    Concurrency-<feature>               (A3 — concurrency / race)

Everything else (Chalk Scenario, Jira AC, QMetry Test Case, Business Rule not
carrying an analyst prefix, etc.) is treated as GROUNDED and never dropped.
"""
import re

# ── Analyst source_id prefixes → subject kind ──────────────────────────────────
# Matched case-insensitively against TraceabilityRecord.source_id. Order does not
# matter (prefixes are mutually exclusive). Anything that matches none of these is
# grounded.
_ANALYST_PREFIXES = {
    'state-transition-matrix': 'state_transition',
    'partial-failure-matrix': 'partial_failure',
    'field-validation-matrix': 'field_validation',
    'idempotency': 'idempotency',
    'concurrency': 'concurrency',
    # ── FEAT-001 evidence-derived analyst kinds (modules/analyst_derivation.py) ──
    # These carry their own traceability pointer (source.extracted_text) back to the
    # exact Jira AC line / Chalk element they were mined from. Validity hinges on that
    # pointer being present in the feature's evidence, NOT on a fixed token vocabulary.
    'ac-negative': 'ac_negative',
    'ac-boundary': 'ac_boundary',
    'ac-altflow': 'ac_alt_flow',
    'ac-regression': 'ac_regression',
}

# The four FEAT-001 evidence-derived kinds, kept/flagged by their traceability pointer.
_DERIVED_KINDS = frozenset(('ac_negative', 'ac_boundary', 'ac_alt_flow', 'ac_regression'))

# ── Subject token vocabularies (all lowercase; matched against the evidence blob) ──
# State tokens a State-Transition-Matrix scenario needs the evidence to mention.
_STATE_TOKENS = (
    'active', 'suspended', 'hotlined', 'hotline', 'pending port', 'pending-port',
    'cancelled', 'canceled', 'pre-active', 'preactive', 'pre active',
)

# Downstream-system tokens a Partial-Failure-Matrix scenario needs.
_DOWNSTREAM_TOKENS = (
    'emm', 'kafka', 'syniverse', 'mediation', 'apollo', 'century', 'itmbo',
    'connection manager',
)

# Concurrency / write-idempotency evidence tokens (shared by A2 and A3).
_CONCURRENCY_TOKENS = (
    'concurrent', 'simultaneous', 'parallel', 'duplicate request', 'duplicate',
    'retry', 'idempotent', 'idempotency', 'race',
)


def _norm(text):
    """Lowercase + collapse underscores/whitespace so 'NBOP_MIG' matches 'nbop mig'
    and 'Pending Port' matches 'pending port'. Pure, deterministic."""
    return re.sub(r'[\s_]+', ' ', (text or '').lower()).strip()


def build_feature_evidence(jira, chalk) -> str:
    """Build the feature's OWN lowercased evidence blob.

    Sources: feature title + description + Jira acceptance criteria + Chalk
    scenarios/scope/rules/obligations (via dimension_extractor._chalk_obligation_text).
    Pure, deterministic, tolerant of None / missing attributes.
    """
    parts = []
    if jira is not None:
        parts.append(getattr(jira, 'summary', '') or '')
        parts.append(getattr(jira, 'description', '') or '')
        _ac = getattr(jira, 'acceptance_criteria', '') or ''
        if isinstance(_ac, (list, tuple)):
            parts.append('\n'.join(str(a) for a in _ac))
        else:
            parts.append(str(_ac))
    if chalk is not None:
        try:
            from .dimension_extractor import _chalk_obligation_text
            parts.append(_chalk_obligation_text(chalk) or '')
        except Exception:
            # Fail-safe: fall back to the plain feature-owned Chalk panels.
            for attr in ('feature_title', 'scope', 'rules'):
                parts.append(getattr(chalk, attr, '') or '')
    return _norm('\n'.join(p for p in parts if p))


def _analyst_subject_kind(subject) -> str:
    """Return the analyst subject kind for a scenario/TC, or '' if it is grounded.

    Classifies by the traceability source_id prefix. *subject* may be an
    ExtractedScenario (has .source) or a TestCase (has .traceability)."""
    src = getattr(subject, 'traceability', None) or getattr(subject, 'source', None)
    source_id = _norm(getattr(src, 'source_id', '') if src is not None else '')
    if not source_id:
        return ''
    for prefix, kind in _ANALYST_PREFIXES.items():
        if source_id.startswith(prefix):
            return kind
    return ''


def is_analyst(subject) -> bool:
    """True when *subject* (scenario or TC) carries an analyst source_id prefix."""
    return bool(_analyst_subject_kind(subject))


def _subject_text(subject) -> str:
    """Collect the subject's own title/summary + validation/steps text for matching."""
    parts = [
        getattr(subject, 'title', '') or '',
        getattr(subject, 'summary', '') or '',
        getattr(subject, 'validation', '') or '',
        getattr(subject, 'description', '') or '',
    ]
    for s in (getattr(subject, 'steps', None) or []):
        parts.append(getattr(s, 'summary', '') or '')
        parts.append(getattr(s, 'expected', '') or '')
    for h in (getattr(subject, 'steps_hint', None) or []):
        parts.append(str(h))
    return _norm('\n'.join(p for p in parts if p))


def _verdict_for_tokens(present_any, subject_text, tokens, evidence_blob):
    """Shared keep/flag/drop decision for a token-family subject.

    - If a relevant token appears in the evidence -> keep.
    - Else if the subject itself names the concept (so it is on-topic for the feature,
      just not spelled out in the evidence) -> flag (needs_review).
    - Else -> drop (unsupported).
    """
    if present_any(tokens, evidence_blob):
        return 'keep', 'evidence names the subject token'
    if present_any(tokens, subject_text):
        return 'flag', 'subject references the concept but evidence does not confirm it'
    return 'drop', 'no supporting evidence for the subject'


def scenario_is_valid(scenario_or_tc, evidence_blob):
    """Classify one analyst scenario/TC against the feature's evidence.

    Returns (verdict, reason) where verdict in {'keep', 'flag', 'drop'}.

    Grounded subjects (no analyst source_id prefix) ALWAYS keep. Analyst subjects
    are validated by kind:
      * State-Transition-Matrix — valid only if a status token is in the evidence.
      * Field-Validation-Matrix — valid only if the subject's field name is in the evidence.
      * Partial-Failure-Matrix  — valid only if a downstream-system token is in the evidence.
      * Concurrency / Idempotency — valid only if the evidence implies concurrency/idempotency.
    Unsupported -> drop; subject on-topic but unconfirmed -> flag; otherwise keep.

    concept_grounded() is used as a coarse pre-check: when the feature's evidence is
    not even provisioning-grounded, analyst subjects that assert a provisioning data
    concept are, at best, flaggable rather than silently kept.
    """
    kind = _analyst_subject_kind(scenario_or_tc)
    if not kind:
        return 'keep', 'grounded (non-analyst) — always kept'

    blob = _norm(evidence_blob)
    subject_text = _subject_text(scenario_or_tc)

    def _present_any(tokens, hay):
        return any(t in hay for t in tokens)

    # Coarse pre-check (qmetry_pattern_library.concept_grounded): a True result means the
    # feature's evidence references a specific NSL/NBOP provisioning concept, which is a
    # supporting (not sufficient) signal for the matrix subjects below.
    concept_ok = _concept_grounded(blob)

    if kind == 'state_transition':
        # Subject carries a SPECIFIC status (Active/Suspended/...) — meaningful to flag on.
        return _verdict_for_tokens(_present_any, subject_text, _STATE_TOKENS, blob)

    if kind == 'partial_failure':
        # Subject carries a SPECIFIC downstream system (Kafka/EMM/...) — flag on subject.
        verdict, reason = _verdict_for_tokens(
            _present_any, subject_text, _DOWNSTREAM_TOKENS, blob)
        # Coarse pre-check reinforcement: a provisioning-grounded feature whose subject
        # names a downstream system but whose evidence does not spell the token out is a
        # reviewable borderline rather than a hard drop.
        if verdict == 'drop' and concept_ok and _present_any(_DOWNSTREAM_TOKENS, subject_text):
            return 'flag', 'downstream subject on a provisioning-grounded feature — needs review'
        return verdict, reason

    if kind == 'field_validation':
        # The field name travels in the subject's own text (title/steps). Valid only
        # when that field token also appears in the evidence.
        field_tokens = _field_tokens(scenario_or_tc)
        if not field_tokens:
            return 'flag', 'no identifiable field name on the scenario'
        if any(f in blob for f in field_tokens):
            return 'keep', 'field name present in evidence'
        return 'drop', 'field name not present in evidence'

    if kind in ('idempotency', 'concurrency'):
        # The subject's own generic title always carries its kind-word, so a subject-only
        # match is not meaningful here: validity hinges on the EVIDENCE implying
        # concurrency / write-idempotency. keep when it does, otherwise drop.
        if _present_any(_CONCURRENCY_TOKENS, blob):
            return 'keep', 'evidence implies concurrency/idempotency'
        return 'drop', 'no concurrency/idempotency evidence on the feature'

    if kind in _DERIVED_KINDS:
        # FEAT-001 evidence-derived scenarios. Each carries a traceability pointer
        # (source.extracted_text) back to the exact AC line / Chalk element it was mined
        # from. KEEP when that pointer is confirmed in the feature's own evidence; FLAG
        # when the scenario is on-topic (subject tokens overlap the evidence) but the
        # pointer itself is not confirmed; DROP otherwise.
        if _evidence_pointer_present(scenario_or_tc, blob):
            return 'keep', 'derived scenario traces to evidence (pointer present)'
        if _subject_shares_token(scenario_or_tc, blob):
            return 'flag', 'derived scenario on-topic but pointer not confirmed in evidence'
        return 'drop', 'derived scenario pointer not present in evidence'

    # Unknown analyst kind — be conservative and flag for review.
    return 'flag', 'unrecognized analyst subject kind'


# ── FEAT-001 evidence-pointer helpers (for the four derived analyst kinds) ────────

# Short/common words that are not meaningful "subject tokens" on their own — a shared
# occurrence of one of these in both the pointer and the evidence does not confirm a trace.
_STOPWORD_TOKENS = frozenset((
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'when', 'not', 'are', 'was',
    'has', 'have', 'does', 'into', 'per', 'via', 'any', 'all', 'but', 'its', 'only',
    'is', 'in', 'of', 'to', 'a', 'an', 'or', 'on', 'be', 'by', 'as', 'at', 'no',
    'verify', 'validate', 'ensure', 'confirm', 'check', 'remains', 'unchanged',
))


def _pointer_text(subject) -> str:
    """The traceability pointer text a derived scenario traces back to (extracted_text)."""
    src = getattr(subject, 'traceability', None) or getattr(subject, 'source', None)
    return _norm(getattr(src, 'extracted_text', '') if src is not None else '')


def _subject_tokens(text):
    """Meaningful (non-stopword, >=3 char) word tokens from *text*, underscore-normalized."""
    return {t for t in re.findall(r'[a-z0-9]+', _norm(text))
            if len(t) >= 3 and t not in _STOPWORD_TOKENS}


def _evidence_pointer_present(subject, blob) -> bool:
    """True when the derived scenario's traceability pointer traces into *blob*.

    A match is confirmed when the normalized extracted_text is a non-empty substring of
    the evidence blob OR when it shares a meaningful (non-stopword) subject token with it.
    Pure, deterministic."""
    pointer = _pointer_text(subject)
    if not pointer:
        return False
    blob = _norm(blob)
    if pointer and pointer in blob:
        return True
    ptr_tokens = _subject_tokens(pointer)
    if not ptr_tokens:
        return False
    blob_tokens = _subject_tokens(blob)
    return bool(ptr_tokens & blob_tokens)


def _subject_shares_token(subject, blob) -> bool:
    """True when the subject's own title/steps share a meaningful token with the evidence
    (used to FLAG an on-topic derived scenario whose pointer is not itself confirmed)."""
    subj_tokens = _subject_tokens(_subject_text(subject))
    if not subj_tokens:
        return False
    return bool(subj_tokens & _subject_tokens(blob))


def _field_tokens(subject):
    """Extract candidate field-name tokens from a Field-Validation-Matrix subject.

    Picks up explicit input_field values on steps plus UPPER_SNAKE / quoted field
    tokens in the subject text. Returns a set of lowercased, underscore-normalized
    tokens."""
    tokens = set()
    for s in (getattr(subject, 'steps', None) or []):
        f = getattr(s, 'input_field', '') or ''
        if f:
            tokens.add(_norm(f))
    text = _subject_text(subject)
    # Quoted field identifiers in the subject text, e.g. 'MMS_TYPE_INDICATOR' /
    # "call direction". Normalized so an underscore token matches a spaced evidence form.
    for m in re.findall(r"'([^']{2,40})'", text):
        tokens.add(_norm(m))
    for m in re.findall(r'"([^"]{2,40})"', text):
        tokens.add(_norm(m))
    return {t for t in tokens if t}


def _concept_grounded(blob) -> bool:
    """Coarse provisioning-grounding pre-check via qmetry_pattern_library.concept_grounded.

    Fail-safe False when the predicate is unavailable."""
    try:
        from .qmetry_pattern_library import concept_grounded
        return bool(concept_grounded(blob))
    except Exception:
        return False


def apply_analyst_validity_gate(suite, jira, chalk, log=print):
    """Run the combined-set validity/reconciliation gate on *suite* in place.

    Steps (see FEAT-002):
      (a) classify each TC as analyst vs grounded by its traceability source_id prefix;
      (b) for each analyst TC run scenario_is_valid — 'drop' removes it, 'flag' sets
          tc.needs_review=True + tc.review_reason (reconcile surfaces [REVIEW]), 'keep'
          retains it;
      (c) grounded TCs are left untouched (always kept);
      (d) on the combined kept set run the humanizer passes in order
          (dedup_and_merge -> flag_low_value -> score_priority -> reorder_by_risk);
      (e) assign the resulting list back to suite.test_cases.

    Chalk-first invariant: reorder_by_risk could otherwise interleave an analyst TC
    ahead of a Chalk-grounded one. To preserve a positional CHALK-GUARD check, the
    kept set is stable-partitioned grounded-first then analyst-appended BEFORE ordering.

    Returns suite.test_cases (the new list). Pure w.r.t. external state: no I/O.
    """
    _log = log or (lambda *_a, **_k: None)
    evidence = build_feature_evidence(jira, chalk)

    kept = []
    dropped = 0
    flagged = 0
    analyst_total = 0

    for tc in (suite.test_cases or []):
        if not is_analyst(tc):
            kept.append(tc)                       # (c) grounded — always kept
            continue
        analyst_total += 1
        verdict, reason = scenario_is_valid(tc, evidence)   # (b)
        if verdict == 'drop':
            dropped += 1
            continue
        if verdict == 'flag':
            tc.needs_review = True
            tc.review_reason = reason
            flagged += 1
        # Analyst scenarios carry rule-generated steps_hint content, which the TC builder
        # marks authored=True (it treats any steps_hint as QMetry ground truth). That flag
        # is spurious for analyst-generated steps and it shields their canned, fabricated
        # banned phrases ("Century Report / NBOP MIG tables") from the grounded-only banned
        # backstop that reconcile/export run next. Clear it on kept analyst TCs (ONLY — never
        # on grounded TCs, and never in default mode) so a fabricated banned step that this
        # feature's own evidence does not ground gets stripped downstream. A legitimately
        # grounded step (evidence names the concept) is still kept by that backstop.
        for _s in (getattr(tc, 'steps', None) or []):
            if getattr(_s, 'authored', False):
                _s.authored = False
                if getattr(_s, 'origin', '') == 'authored':
                    _s.origin = 'grounded'
        kept.append(tc)

    _log('[ANALYST-GATE] combined set: %d TCs, %d analyst (%d kept, %d flagged, %d dropped)' % (
        len(suite.test_cases or []), analyst_total,
        analyst_total - dropped - flagged, flagged, dropped))

    # (d) Humanizer passes over the combined kept set. Chalk-first stable partition
    #     BEFORE ordering so risk reordering never floats an analyst TC above a
    #     grounded (Chalk/Jira) one that an index-based CHALK-GUARD check expects first.
    grounded_first = [tc for tc in kept if not is_analyst(tc)]
    analyst_rest = [tc for tc in kept if is_analyst(tc)]
    ordered_input = grounded_first + analyst_rest

    try:
        from .humanizer import (
            dedup_and_merge, flag_low_value, score_priority, reorder_by_risk,
        )
        result = dedup_and_merge(ordered_input, log=_log)
        result = flag_low_value(result, log=_log)
        result = score_priority(
            result, log=_log,
            feature_priority=getattr(suite, 'jira_priority', '') or '')
        result = reorder_by_risk(result, log=_log)
    except Exception as _hp_err:
        # Humanizer fault must not break the run — fall back to the partitioned set.
        _log('[ANALYST-GATE]   WARNING: humanizer passes failed: %s — using partitioned set'
             % str(_hp_err)[:120])
        result = ordered_input

    suite.test_cases = result
    return suite.test_cases
