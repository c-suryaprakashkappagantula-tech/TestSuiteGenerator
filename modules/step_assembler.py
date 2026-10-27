"""
step_assembler.py — single reconciliation stage that owns the final ordered step list
and the preconditions of every test case (TSG step-assembly design, findings.md §8).

Historically many independent producers each appended steps or set preconditions on the
same TC and nothing reconciled them by MEANING: scenario-title dedup and exact step-string
dedup were the only reductions, so two steps that assert the same field+value with different
wording both survived (e.g. a grounded "Inspect call_direction_ind … BAU" plus a spliced
"Verify call_direction_ind = BAU"), and preconditions were chosen by per-TC keyword guessing
in two places while the computed batch_file/mediation classification was never consulted.

StepAssembler.reconcile() is the owner. It runs ONCE, AFTER every additive producer and
AFTER qmetry_pattern_library.enrich_suite, and BEFORE tc_name_polish / api_step_grounder /
the export sanitizer, and BEFORE the final _assert_chalk_coverage so coverage is re-verified
on the reconciled suite. It:

  1. Collapses steps that share a semantic assertion key
     ``(expected_field|input_field, expected_value)`` — keeping the step whose origin wins
     the tie-break ``authored > grounded > obligation > closer`` — so a redundant spliced
     restatement of an already-grounded assertion is dropped.
  2. Sets preconditions from the feature classification (batch_file / mediation) as the single
     source of truth, never overwriting a Chalk-authored precondition
     (``precond_from_chalk=True``) nor a genuinely-different negative/UI TC's own precondition.

Deterministic and idempotent. Never invents step text. Never drops a grounded/authored step
in favour of a weaker-origin duplicate, so Chalk-first and grounding guarantees are preserved.
"""
import re
from typing import Any, List, Optional

try:
    from . import qmetry_pattern_library
except Exception:  # pragma: no cover - import guard only
    qmetry_pattern_library = None


# Origin precedence for the tie-break (higher wins). An unknown/empty origin ranks as
# 'grounded' so legacy producers that never set the tag behave as before.
_ORIGIN_RANK = {'authored': 3, 'grounded': 2, 'obligation': 1, 'closer': 0, 'negative': 2}

_BATCH_PRECONDITIONS = (
    'SIT environment is ready and SFTP server is accessible.',
    'Valid PRR/batch file is prepared with test subscriber data (correct format, field lengths).',
    'Subscriber line exists in NSL DB with matching MDN for file processing.',
)


def _norm(v: str) -> str:
    return re.sub(r'\s+', ' ', (v or '').strip().lower())


def _scope_is_lnp_name_trim(blob: str) -> bool:
    """True when the feature scope is the NE LNP-customer-name / 25-char name-trim feature.

    Used to EXCLUDE this feature from batch/mediation precondition selection: its scope is
    port-in customer-name handling (NE concatenate/split/trim to 25 chars, GENS-0005), not
    SFTP/PRR file ingestion. Requires a concrete name-limit signal so it never excludes a
    genuine PRR/mediation feature (4628/4074) that merely mentions a name somewhere.
    """
    b = (blob or '').lower()
    if not b:
        return False
    has_name_limit = (
        'gens-0005' in b
        or '25-character' in b or '25 character' in b or '25-char' in b
        or 'lnp customer name' in b or 'lnp name' in b
        or ('customer name' in b and ('25' in b or 'port-in' in b or 'port in' in b)))
    # must be about port-in / NE name handling, not a PRR usage file
    has_portin_ne = 'port-in' in b or 'port in' in b or 'portin' in b or 'lnp' in b
    return bool(has_name_limit and has_portin_ne)


def _origin_rank(step) -> int:
    o = getattr(step, 'origin', '') or ''
    if getattr(step, 'authored', False):
        return _ORIGIN_RANK['authored']
    return _ORIGIN_RANK.get(o, _ORIGIN_RANK['grounded'])


def _feature_evidence_blob(suite) -> str:
    """Build the feature's OWN evidence blob for the grounded-only banned backstop.

    Includes Chalk feature_title/scope/rules + Jira acceptance_criteria + feature_desc,
    PLUS the text of every AUTHORED / Chalk-grounded step across the suite (ground truth
    written by the test architect or pulled verbatim from QMetry). It deliberately
    EXCLUDES fabricated generated/obligation/closer step text so a fabricated banned
    step can never 'ground itself'. Pure, case-insensitive. Module-level so both
    ``StepAssembler.reconcile`` and the export-time universal backstop share the exact
    same grounding signal.
    """
    parts = [
        getattr(suite, 'feature_title', '') or '',
        getattr(suite, 'feature_desc', '') or '',
        getattr(suite, 'scope', '') or '',
        getattr(suite, 'rules', '') or '',
    ]
    _ac = getattr(suite, 'acceptance_criteria', None) or []
    try:
        parts.append('\n'.join(str(a) for a in _ac))
    except Exception:
        pass
    for tc in getattr(suite, 'test_cases', []) or []:
        for st in getattr(tc, 'steps', []) or []:
            _authored = getattr(st, 'authored', False) or (getattr(st, 'origin', '') == 'authored')
            if _authored:
                parts.append(getattr(st, 'summary', '') or '')
                parts.append(getattr(st, 'expected', '') or '')
    return ' '.join(parts)


def _strip_ungrounded_banned_steps_tc(tc, evidence_blob: str, feature_grounded: bool) -> int:
    """Strip fabricated banned steps from a single TC. Shared implementation used by
    both ``StepAssembler._strip_ungrounded_banned_steps`` (per-TC, inside reconcile) and
    the module-level ``strip_ungrounded_banned_steps`` (whole-suite export backstop).

    Grounded-only policy, mirroring the closer gate:
      - If the feature's OWN evidence references the NSL/NBOP provisioning concept
        (``feature_grounded``), banned steps are LEGITIMATE for this feature; the
        per-step backstop still trims any individual step whose specific banned concept
        is itself ungrounded.
      - If the feature is NOT grounded at all, EVERY banned step is fabricated garbage
        and is dropped.

    NEVER drops an AUTHORED / QMetry-ground-truth step (origin='authored' / authored=True).
    Renumbers the surviving steps. Returns the count removed.
    """
    if qmetry_pattern_library is None:
        return 0
    steps = list(getattr(tc, 'steps', []) or [])
    if not steps:
        return 0
    keep = []
    removed = 0
    for st in steps:
        if getattr(st, 'authored', False) or (getattr(st, 'origin', '') == 'authored'):
            keep.append(st)
            continue
        text = '%s %s' % (getattr(st, 'summary', '') or '', getattr(st, 'expected', '') or '')
        banned = qmetry_pattern_library._step_is_banned_and_ungrounded(text, evidence_blob)
        if not feature_grounded:
            # Not grounded at all: drop ANY step that carries a banned phrase, regardless
            # of whether that specific phrase has an in-step grounding keyword.
            if qmetry_pattern_library._step_is_banned_and_ungrounded(text, ''):
                removed += 1
                continue
        elif banned:
            # Grounded feature: trim only the individually-ungrounded banned sub-concept.
            removed += 1
            continue
        keep.append(st)
    if removed:
        for i, st in enumerate(keep, 1):
            if hasattr(st, 'step_num'):
                st.step_num = i
        tc.steps = keep
    return removed


def strip_ungrounded_banned_steps(suite, log=print) -> int:
    """Universal grounded-gated banned-phrase backstop over an ENTIRE suite.

    This is the HARD, universal final net the review requires: it runs on EVERY TC and
    EVERY step of a suite regardless of path (main/PI, narrow-CR ``_build_cr_suite_v8``,
    manual-link) and regardless of step origin (grounded/obligation/authored/QMetry/
    AC-derived/closer). It is invoked from ``StepAssembler.reconcile`` (main path) AND
    from the shared export chokepoint ``excel_generator.generate_excel`` (so the CR/manual
    path that never reaches ``reconcile`` is still covered).

    For any step whose summary OR expected text carries a banned NSL/NBOP provisioning
    phrase that is NOT grounded in the feature's own evidence (``concept_grounded``), that
    single step is stripped — the TC's other grounded steps are kept (never drop a whole
    TC because one step was banned). A feature genuinely about Century Report / Service
    Grouping / NBOP keeps those steps (grounded there). Authored/QMetry ground-truth steps
    are never stripped. Pure, idempotent, never fatal. Returns the total count stripped.
    """
    _log = log or (lambda *_a, **_k: None)
    if qmetry_pattern_library is None:
        return 0
    evidence_blob = _feature_evidence_blob(suite)
    try:
        feature_grounded = qmetry_pattern_library.concept_grounded(evidence_blob)
    except Exception:
        feature_grounded = False
    total = 0
    for tc in getattr(suite, 'test_cases', []) or []:
        try:
            total += _strip_ungrounded_banned_steps_tc(tc, evidence_blob, feature_grounded)
        except Exception as exc:  # one bad TC is skipped, not fatal
            try:
                _log('[STEP-ASSEMBLER] backstop skip %s: %s'
                     % ((getattr(tc, 'summary', '') or '')[:40], str(exc)[:80]))
            except Exception:
                pass
    try:
        _log('[STEP-ASSEMBLER] universal banned backstop: %d ungrounded banned step(s) stripped'
             % total)
    except Exception:
        pass
    return total


def _assertion_key(step):
    """The semantic key a step asserts, or None when it asserts nothing concrete.

    Key = ``(field, normalized expected_value)`` where field is the step's
    ``expected_field`` (preferred) or its ``input_field``. A step with no concrete
    expected_value is never deduped (there is nothing to compare by meaning).

    A literal-default field ('input' / 'output' / 'result') is normalized to '' so a
    grounded step stamped with the real field ('call_direction_ind') still collapses a
    spliced restatement that defaulted its field — the match is on the asserted VALUE.
    """
    exp_val = _norm(getattr(step, 'expected_value', ''))
    if not exp_val:
        return None
    field = (getattr(step, 'expected_field', '') or '').strip().lower()
    if field in ('', 'output', 'result'):
        field = (getattr(step, 'input_field', '') or '').strip().lower()
    if field in ('input',):
        field = ''
    return (field, exp_val)


class StepAssembler:
    """Owns the final ordered step list and preconditions of a reconciled suite."""

    def __init__(self, log=print):
        self._log = log or (lambda *_a, **_k: None)

    # ── public API ──────────────────────────────────────────────────────────

    def reconcile(self, suite, classification, log=None) -> Any:
        """Reconcile every TC in *suite* in place. Returns the suite.

        *classification* is the FeatureClassification computed by the engine; its
        ``.classification`` string ('batch_file' | 'api' | 'ui' | 'hybrid' | ...) drives
        precondition selection. Never raises — a bad TC is skipped, not fatal.
        """
        log = log or self._log
        is_batch = self._is_batch_feature(suite, classification)
        # Build the feature-evidence blob ONCE for the grounded-only backstop (see
        # _strip_ungrounded_banned_steps). This is the SAME grounding signal the closer
        # gate uses — the feature's OWN evidence (Chalk feature_title/scope/rules + Jira AC
        # + feature_desc), plus the text of its authored/Chalk-grounded steps (ground truth
        # only; fabricated generated steps are deliberately EXCLUDED so they cannot
        # self-ground).
        evidence_blob = self._feature_evidence_blob(suite)
        try:
            feature_grounded = (qmetry_pattern_library is not None
                                and qmetry_pattern_library.concept_grounded(evidence_blob))
        except Exception:
            feature_grounded = False
        n_dedup = 0
        n_precon = 0
        n_stripped = 0
        for tc in getattr(suite, 'test_cases', []) or []:
            try:
                removed = self._dedup_steps(tc)
                n_dedup += removed
                n_stripped += _strip_ungrounded_banned_steps_tc(tc, evidence_blob, feature_grounded)
                if is_batch and self._set_batch_preconditions(tc):
                    n_precon += 1
            except Exception as exc:  # never fail the build over reconciliation
                try:
                    log('[STEP-ASSEMBLER] skip %s: %s'
                        % ((getattr(tc, 'summary', '') or '')[:40], str(exc)[:80]))
                except Exception:
                    pass
        # Surface any Tier-3 borderline-relevance flags as visible suite-level warnings so a
        # reviewer sees which QMetry-only TCs were kept-and-flagged (never silent).
        try:
            for tc in getattr(suite, 'test_cases', []) or []:
                if getattr(tc, 'needs_review', False):
                    _w = '[REVIEW] %s: %s' % (
                        (getattr(tc, 'sno', '') or getattr(tc, 'summary', '') or '')[:48],
                        getattr(tc, 'review_reason', '') or 'flagged for review')
                    if suite.warnings is None:
                        suite.warnings = []
                    if _w not in suite.warnings:
                        suite.warnings.append(_w)
        except Exception:
            pass
        try:
            log('[STEP-ASSEMBLER] reconciled suite: %d redundant step(s) collapsed, '
                '%d ungrounded banned step(s) stripped, '
                '%d precondition(s) set from classification (batch_file=%s)'
                % (n_dedup, n_stripped, n_precon, is_batch))
        except Exception:
            pass
        return suite

    # ── grounded-only banned-step backstop (ROOT-CAUSE fix) ───────────────────

    @staticmethod
    def _feature_evidence_blob(suite) -> str:
        """Thin wrapper over the module-level ``_feature_evidence_blob`` so both the
        reconcile path and the export-time universal backstop share one grounding signal."""
        return _feature_evidence_blob(suite)

    def _strip_ungrounded_banned_steps(self, tc, evidence_blob: str) -> int:
        """Thin per-TC wrapper over the shared strip implementation. Recomputes the
        feature-grounded flag from *evidence_blob* so direct callers behave exactly as
        before. ``reconcile`` bypasses this and calls the shared helper directly with a
        once-computed flag for efficiency. See ``strip_ungrounded_banned_steps`` for the
        whole-suite universal backstop and ``_strip_ungrounded_banned_steps_tc`` for the
        grounded-only policy. NEVER drops an authored/QMetry ground-truth step.
        """
        if qmetry_pattern_library is None:
            return 0
        try:
            feature_grounded = qmetry_pattern_library.concept_grounded(evidence_blob)
        except Exception:
            feature_grounded = False
        return _strip_ungrounded_banned_steps_tc(tc, evidence_blob, feature_grounded)

    # ── step dedup ──────────────────────────────────────────────────────────

    def _dedup_steps(self, tc) -> int:
        """Collapse steps that assert the same (field, value) by meaning.

        For each assertion key, keep the single highest-origin step (authored > grounded >
        obligation > closer; ties keep the earlier step). Drop the rest. A step that asserts
        nothing concrete is always kept. Returns the count removed.

        When the winner is a grounded/authored step and the loser is a spliced obligation
        restatement, any non-trivial intent carried only by the loser's expected text is
        merged into the winner's expected so no assertion detail is lost (findings.md §8.3:
        "a merged intent inside the grounded step's expected is acceptable").
        """
        steps = list(getattr(tc, 'steps', []) or [])
        if len(steps) < 2:
            return 0

        # First pass: find, per key, the winning step (index).
        winner_by_key = {}
        for idx, st in enumerate(steps):
            key = _assertion_key(st)
            if key is None:
                continue
            cur = winner_by_key.get(key)
            if cur is None or _origin_rank(st) > _origin_rank(steps[cur]):
                winner_by_key[key] = idx

        if not winner_by_key:
            return 0

        # Second pass: keep winners + all non-asserting steps; drop losing duplicates,
        # merging their intent into the winner's expected. When the dropped duplicate is a
        # spliced obligation ASSERT whose winner is a grounded/authored step, its paired
        # obligation SET step (same obligation_id, no expected_value) is now an orphan — a
        # "Set X = …" with no matching verify — so it is dropped too. This is what eliminates
        # TC01's redundant steps 5-8 while keeping the grounded 4-step PRR chain intact.
        dropped_obligation_sets = set()
        keep = []
        removed = 0
        for idx, st in enumerate(steps):
            key = _assertion_key(st)
            if key is None:
                keep.append(st)
                continue
            win_idx = winner_by_key.get(key)
            if win_idx == idx:
                keep.append(st)
                continue
            # This step is a redundant restatement of the winner — merge any extra intent.
            self._merge_expected(steps[win_idx], st)
            removed += 1
            # If the dropped duplicate was a weaker-origin obligation assert (winner is not
            # itself an obligation step), mark its obligation_id so the orphaned SET goes too.
            if (getattr(st, 'origin', '') == 'obligation'
                    and getattr(steps[win_idx], 'origin', '') != 'obligation'):
                _oid = getattr(st, 'obligation_id', '') or ''
                if _oid:
                    dropped_obligation_sets.add(_oid)

        # Third pass: drop orphaned obligation SET steps (no expected_value) whose paired
        # assert was collapsed into a grounded/authored step.
        if dropped_obligation_sets:
            pruned = []
            for st in keep:
                if (getattr(st, 'origin', '') == 'obligation'
                        and not _norm(getattr(st, 'expected_value', ''))
                        and (getattr(st, 'obligation_id', '') or '') in dropped_obligation_sets):
                    removed += 1
                    continue
                pruned.append(st)
            keep = pruned

        if removed:
            for i, st in enumerate(keep, 1):
                if hasattr(st, 'step_num'):
                    st.step_num = i
            tc.steps = keep
        return removed

    @staticmethod
    def _merge_expected(winner, loser) -> None:
        """Fold a loser restatement's non-duplicated expected text into the winner.

        Only appends when the loser carries genuinely extra prose (not already a substring
        of the winner's expected). Keeps the winner's wording as the lead.
        """
        w_exp = (getattr(winner, 'expected', '') or '').strip()
        l_exp = (getattr(loser, 'expected', '') or '').strip()
        if not l_exp:
            return
        if _norm(l_exp) in _norm(w_exp):
            return
        # Avoid appending a near-identical generic phrase.
        if _norm(w_exp) and _norm(l_exp) and _norm(l_exp)[:24] in _norm(w_exp):
            return
        sep = ' ' if w_exp.endswith(('.', '!', '?', ')')) or not w_exp else '. '
        winner.expected = (w_exp + sep + l_exp).strip() if w_exp else l_exp

    # ── preconditions from classification ─────────────────────────────────────

    def _is_batch_feature(self, suite, classification) -> bool:
        blob = '%s %s %s' % (
            getattr(suite, 'feature_title', '') or '',
            getattr(suite, 'feature_desc', '') or '',
            getattr(suite, 'scope', '') or '')
        # NE LNP-customer-name / 25-char name-trim feature is an NE name-handling feature,
        # NOT a mediation/PRR/batch-file feature — even though its large scope text may
        # incidentally mention "mediation" (e.g. "BI/Kafka, mediation" when describing an
        # unrelated downstream). It must not receive SFTP/PRR batch preconditions.
        if _scope_is_lnp_name_trim(blob):
            return False
        cls = (getattr(classification, 'classification', '') or '').strip().lower()
        if cls == 'batch_file':
            return True
        # Belt-and-suspenders: re-confirm via the mediation signal over the feature text so a
        # misclassification upstream still gets correct preconditions.
        if qmetry_pattern_library is not None:
            try:
                return bool(qmetry_pattern_library.is_mediation_feature(blob))
            except Exception:
                return False
        return False

    def _set_batch_preconditions(self, tc) -> bool:
        """Set SFTP/batch preconditions on a batch_file TC. Returns True if changed.

        NEVER overwrites a Chalk-authored precondition (Rule #1). NEVER overwrites a
        genuinely-different TC type's own precondition: a Negative TC that already names a
        concrete error/required-state precondition keeps it (per-TC override for different
        TC types, findings.md §8.7 decision 4).
        """
        if getattr(tc, 'precond_from_chalk', False):
            return False
        category = (getattr(tc, 'category', '') or '').lower()
        pre = (getattr(tc, 'preconditions', '') or '').strip()
        # A negative/error TC whose precondition is already specific (error condition / a
        # required non-active line state) is a genuinely different TC type — leave it.
        if 'negative' in category and self._precon_is_specific_negative(pre):
            return False
        new = '\n'.join('%d. %s' % (i, p) for i, p in enumerate(_BATCH_PRECONDITIONS, 1))
        if _norm(pre) == _norm(new):
            return False
        tc.preconditions = new
        return True

    @staticmethod
    def _precon_is_specific_negative(pre: str) -> bool:
        low = (pre or '').lower()
        return any(tok in low for tok in (
            'error condition', 'business rule', 'line status:', 'err', 'invalid',
            'out-of-spec', 'out of spec'))
