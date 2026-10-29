# -*- coding: utf-8 -*-
"""
analyst_derivation.py — FEAT-001 evidence-driven analyst scenario derivation.

WHY THIS EXISTS
    Analyst mode (options['data_only'] == False) used to gate its extra scenarios behind
    the D1 state-matrix and A1 field-validation generators, which only fire for NSL
    provisioning / NMNO-backed features. A plain API / batch / UI feature whose evidence
    never mentions the provisioning concept produced ZERO analyst scenarios even when its
    own Jira AC + Chalk clearly imply negative, boundary, alternate-flow and regression
    cases (reproduced OFFLINE on MWTGPROV-4575 / 4503 / 4533).

    This module derives those extra scenarios directly from the feature's OWN evidence,
    deterministically, with no LLM and no network. Each emitted scenario carries a
    traceability pointer (extracted_text) back to the exact AC line / Chalk element it was
    mined from, so modules/analyst_validity.scenario_is_valid KEEPS it (it traces to real
    evidence) and still DROPS speculative A2/A3 scenarios that do not.

SOURCE MATERIAL ONLY (modeled on modules/eligibility_negatives.py)
    The derivers read ONLY the ticket (summary/description/AC) and Chalk
    (scope/rules/scenarios/obligations, via dimension_extractor._chalk_obligation_text).
    They must NEVER read test cases the run has already generated. Word-boundary regex
    matchers; a dataclass is not needed here because the public entry returns
    ExtractedScenario objects directly.

OUTPUT CONTRACT
    derive_analyst_scenarios(jira, chalk, classification, existing_titles, log) ->
        List[ExtractedScenario]. Each scenario:
          * source = create_traceability(source_type='Jira AC'|'Chalk Scenario',
                       source_id='AC-Negative-<fid>'|'AC-Boundary-<fid>'|
                                 'AC-AltFlow-<fid>'|'AC-Regression-<fid>',
                       extracted_text=<exact AC line / Chalk element>)
          * steps_hint = deterministic hint steps
        Titles are normalize-deduped against existing_titles and each other; total derived
        per feature is capped at _MAX_DERIVED for determinism and stable ordering.
"""
import re
from typing import List, Optional

from .data_models_v8 import ExtractedScenario
from .traceability import create_traceability

# Cap total derived scenarios per feature so the output is deterministic and bounded.
_MAX_DERIVED = 12

# ── Operation verbs whose POSITIVE acceptance criterion implies a negative counterpart ──
# Word-boundary matched (plural/past tense tolerated by the _VERB_RE build below).
_OPERATION_VERBS = (
    'process', 'convert', 'accept', 'resend', 'sync', 'swap', 'store', 'provision',
    'activate', 'enable', 'add', 'update', 'send', 'return', 'generate',
)

_VERB_RE = re.compile(
    r'(?<![a-z])(?:%s)(?:es|s|ed|ing)?(?![a-z])'
    % '|'.join(re.escape(v) for v in _OPERATION_VERBS),
    re.IGNORECASE,
)

# A line that already states a prohibition/rejection is itself a negative anchor.
_NEGATION_RE = re.compile(
    r'(?<![a-z])(?:not|no|never|without|reject(?:ed|s)?|invalid|inactive|absent|'
    r'malformed|fail(?:s|ed)?|denied|restricted|unauthoriz(?:ed)?)(?![a-z])',
    re.IGNORECASE,
)

# A line that states "no impact / unchanged / unaffected" is a regression anchor.
_REGRESSION_RE = re.compile(
    r'(remains?\s+unchanged|unchanged|unaffected|no\s+impact|not\s+impacted|'
    r'not\s+degraded|not\s+affected|existing\s+[^.\n]*\b(?:remain|behavior|processing))',
    re.IGNORECASE,
)

# ── Boundary mining patterns ──
_VARCHAR_RE = re.compile(r'\bVARCHAR\s*\(\s*(\d{1,4})\s*\)', re.IGNORECASE)
_NUMERIC_RE = re.compile(r'\bNUMERIC\s*\(\s*(\d{1,4})\s*\)', re.IGNORECASE)
_UP_TO_CHARS_RE = re.compile(r'\bup\s+to\s+(\d{1,4})\s+(?:char|character|digit|byte)s?', re.IGNORECASE)
_N_DIGITS_RE = re.compile(r'\b(\d{1,4})\s+(?:significant\s+)?(?:char|character|digit|byte)s?\b', re.IGNORECASE)

# ── Alternate-flow enumeration patterns (named variants per feature) ──
_INPUT_ENUM_RE = re.compile(r'(?<![A-Za-z])(MDN|ICCID|IMEI|EID)(?![A-Za-z])')
_SIM_ENUM_RE = re.compile(r'(?<![A-Za-z])([eEpP]\s?SIM)(?![A-Za-z])')
_OP_CODE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z]{2})(?![A-Za-z0-9])')  # e.g. YD / AM
_FILE_SERIES_RE = re.compile(r'(?<!\d)(\d{4})(?!\d)')  # e.g. 3289 / 3290 / 8886

# Only treat a 2-letter uppercase token as an operation code when the surrounding text
# names an operation (so we do not pick up random abbreviations like "API" fragments).
_OP_CONTEXT_RE = re.compile(r'\b(operation|swap|sync|code|transaction|am|yd)\b', re.IGNORECASE)
# Only treat a 4-digit token as a file-series variant when "file" is mentioned nearby.
_FILE_CONTEXT_RE = re.compile(r'\bfile', re.IGNORECASE)


def _ac_lines(jira) -> List[str]:
    """Split the Jira acceptance criteria into non-empty, de-bulleted lines."""
    ac = (getattr(jira, 'acceptance_criteria', '') or '') if jira else ''
    if isinstance(ac, (list, tuple)):
        raw = '\n'.join(str(a) for a in ac)
    else:
        raw = str(ac)
    lines = []
    for ln in raw.split('\n'):
        ln = re.sub(r'^[\s#*\-\u2022\xa0]+', '', ln).strip()
        if ln:
            lines.append(ln)
    return lines


def _chalk_elements(chalk) -> List[str]:
    """Chalk obligation/scope/rules text split into lines, via the feature-owned blob."""
    if chalk is None:
        return []
    try:
        from .dimension_extractor import _chalk_obligation_text
        blob = _chalk_obligation_text(chalk) or ''
    except Exception:
        blob = ''
        for attr in ('feature_title', 'scope', 'rules'):
            v = getattr(chalk, attr, '') or ''
            if v:
                blob += '\n' + v
    lines = []
    for ln in blob.split('\n'):
        ln = ln.strip()
        if ln:
            lines.append(ln)
    return lines


def _norm_title(title: str) -> str:
    return re.sub(r'\s+', ' ', (title or '').lower()).strip()


def _short_fid(jira, classification) -> str:
    return (getattr(jira, 'key', '') or '') if jira else ''


def _clip(text: str, limit: int = 180) -> str:
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    return text if len(text) <= limit else text[:limit].rstrip()


def _negative_scenarios(ac_lines, chalk_lines, fid) -> List[ExtractedScenario]:
    """For each positive AC/rule with an operation verb, emit the invalid/absent/malformed
    counterpart; also directly surface AC lines that already state a prohibition."""
    out = []
    for line in ac_lines + chalk_lines:
        has_verb = bool(_VERB_RE.search(line))
        has_neg = bool(_NEGATION_RE.search(line))
        if not (has_verb or has_neg):
            continue
        src_type = 'Jira AC' if line in ac_lines else 'Chalk Scenario'
        if has_neg:
            # The line is itself a negative anchor — assert the rejection directly.
            title = 'Verify rejection/negative handling: %s' % _clip(line, 120)
        else:
            # Positive operation → derive the invalid/absent/malformed counterpart.
            title = ('Verify %s fails or is rejected for invalid/absent/malformed input'
                     % _clip(line, 100))
        out.append(ExtractedScenario(
            title=title,
            validation='Negative counterpart derived from: %s' % _clip(line),
            category='Negative',
            source=create_traceability(
                source_type=src_type,
                source_id='AC-Negative-%s' % fid,
                extracted_text=_clip(line, 200),
            ),
            steps_hint=[
                'Identify the input/condition described by: %s' % _clip(line, 120),
                'Supply an invalid, absent, or malformed value instead',
                'Verify the operation is rejected and no state change occurs',
            ],
        ))
    return out


def _boundary_scenarios(ac_lines, chalk_lines, fid) -> List[ExtractedScenario]:
    """Mine VARCHAR(n)/NUMERIC(n)/'up to N chars'/'N digits' → at-boundary + over-boundary
    scenarios, one pair per enumerated value."""
    out = []
    seen = set()
    for line in ac_lines + chalk_lines:
        src_type = 'Jira AC' if line in ac_lines else 'Chalk Scenario'
        found = []
        for m in _VARCHAR_RE.finditer(line):
            found.append(('VARCHAR', int(m.group(1))))
        for m in _NUMERIC_RE.finditer(line):
            found.append(('NUMERIC', int(m.group(1))))
        for m in _UP_TO_CHARS_RE.finditer(line):
            found.append(('length', int(m.group(1))))
        if not found:
            # Only fall back to the generic "N digits/chars" form when nothing stronger
            # matched on this line, to avoid double-counting VARCHAR(45) as "45 chars".
            for m in _N_DIGITS_RE.finditer(line):
                found.append(('length', int(m.group(1))))
        for kind, n in found:
            key = (kind, n)
            if key in seen:
                continue
            seen.add(key)
            label = '%s(%d)' % (kind, n) if kind != 'length' else '%d-char/digit' % n
            out.append(ExtractedScenario(
                title='Verify value at the %s boundary is accepted' % label,
                validation='At-boundary value for %s derived from: %s' % (label, _clip(line)),
                category='Edge Case',
                source=create_traceability(
                    source_type=src_type,
                    source_id='AC-Boundary-%s' % fid,
                    extracted_text=_clip(line, 200),
                ),
                steps_hint=[
                    'Prepare a value exactly at the %s boundary' % label,
                    'Submit it through the feature under test',
                    'Verify it is accepted/stored without truncation or overflow',
                ],
            ))
            out.append(ExtractedScenario(
                title='Verify value over the %s boundary is handled safely' % label,
                validation='Over-boundary value for %s derived from: %s' % (label, _clip(line)),
                category='Edge Case',
                source=create_traceability(
                    source_type=src_type,
                    source_id='AC-Boundary-%s' % fid,
                    extracted_text=_clip(line, 200),
                ),
                steps_hint=[
                    'Prepare a value exceeding the %s boundary by one unit' % label,
                    'Submit it through the feature under test',
                    'Verify it is rejected or handled without silent truncation/overflow',
                ],
            ))
    return out


def _alt_flow_scenarios(ac_lines, chalk_lines, fid) -> List[ExtractedScenario]:
    """One scenario per named variant: enumerated inputs {MDN, ICCID, ...}, SIM types
    {eSIM, pSIM}, operation codes {YD, AM}, file series {3289, 3290, 8886}."""
    out = []
    blob = '\n'.join(ac_lines + chalk_lines)
    ac_blob = '\n'.join(ac_lines)

    def _src_for(token):
        src_type = 'Jira AC' if token.lower() in ac_blob.lower() else 'Chalk Scenario'
        # extracted_text points at the first line that names the variant.
        for line in ac_lines + chalk_lines:
            if token.lower() in line.lower():
                return src_type, _clip(line, 200)
        return src_type, token

    def _emit(token, variant_label):
        src_type, ptr = _src_for(token)
        out.append(ExtractedScenario(
            title='Verify the %s flow using %s' % (variant_label, token),
            validation='Alternate-flow variant %s derived from evidence' % token,
            category='Happy Path',
            source=create_traceability(
                source_type=src_type,
                source_id='AC-AltFlow-%s' % fid,
                extracted_text=ptr,
            ),
            steps_hint=[
                'Select the %s variant: %s' % (variant_label, token),
                'Execute the feature flow with that variant',
                'Verify the outcome is correct for %s' % token,
            ],
        ))

    # Enumerated inputs (dedup preserving first-seen order).
    inputs = []
    for m in _INPUT_ENUM_RE.finditer(blob):
        v = m.group(1).upper()
        if v not in inputs:
            inputs.append(v)
    if len(inputs) >= 2:
        for v in inputs:
            _emit(v, 'input-type')

    # SIM types (normalize eSIM / pSIM casing).
    sims = []
    for m in _SIM_ENUM_RE.finditer(blob):
        v = re.sub(r'\s+', '', m.group(1))
        v = v[0].lower() + 'SIM'
        if v not in sims:
            sims.append(v)
    if len(sims) >= 2:
        for v in sims:
            _emit(v, 'SIM-type')

    # Operation codes (only when operation context present).
    if _OP_CONTEXT_RE.search(blob):
        ops = []
        for m in _OP_CODE_RE.finditer(blob):
            v = m.group(1)
            if v in ('YD', 'AM') and v not in ops:
                ops.append(v)
        if len(ops) >= 2:
            for v in ops:
                _emit(v, 'operation')

    # File series (only when "file" context present).
    if _FILE_CONTEXT_RE.search(blob):
        files = []
        for m in _FILE_SERIES_RE.finditer(blob):
            v = m.group(1)
            if v not in files:
                files.append(v)
        if len(files) >= 2:
            for v in files:
                _emit(v, 'file-series')

    return out


def _regression_scenarios(ac_lines, chalk_lines, fid) -> List[ExtractedScenario]:
    """AC/Chalk lines asserting 'remains unchanged'/'unaffected'/'no impact' → a no-impact
    regression scenario per such line."""
    out = []
    for line in ac_lines + chalk_lines:
        if not _REGRESSION_RE.search(line):
            continue
        src_type = 'Jira AC' if line in ac_lines else 'Chalk Scenario'
        out.append(ExtractedScenario(
            title='Verify no regression: %s' % _clip(line, 110),
            validation='No-impact regression derived from: %s' % _clip(line),
            category='Regression',
            source=create_traceability(
                source_type=src_type,
                source_id='AC-Regression-%s' % fid,
                extracted_text=_clip(line, 200),
            ),
            steps_hint=[
                'Establish the pre-change baseline for: %s' % _clip(line, 120),
                'Exercise the unchanged behavior described by the line',
                'Verify the behavior matches the baseline (no regression)',
            ],
        ))
    return out


def derive_analyst_scenarios(jira, chalk, classification=None,
                             existing_titles=None, log=print) -> List[ExtractedScenario]:
    """Public entry: derive evidence-grounded analyst scenarios from jira + chalk ONLY.

    Pure and deterministic: no I/O, no network, no LLM. Reads the ticket + Chalk and emits
    negative / boundary / alternate-flow / regression ExtractedScenario objects, each with
    an AC-<Kind>-<fid> traceability source_id and an extracted_text pointing back to the
    exact evidence line. Titles are normalize-deduped against *existing_titles* and each
    other; total output is capped at _MAX_DERIVED for stable, deterministic ordering.
    """
    _log = log or (lambda *_a, **_k: None)
    existing = {_norm_title(t) for t in (existing_titles or [])}

    fid = _short_fid(jira, classification)
    ac_lines = _ac_lines(jira)
    chalk_lines = _chalk_elements(chalk)
    if not (ac_lines or chalk_lines):
        return []

    derived: List[ExtractedScenario] = []
    counts = {'AC-Negative': 0, 'AC-Boundary': 0, 'AC-AltFlow': 0, 'AC-Regression': 0}

    # Build each producer's deduped candidate list first, then ROUND-ROBIN across the four
    # kinds (negative, boundary, alternate-flow, regression) when appending under the cap.
    # Round-robin — rather than a single concatenated pass — stops a prolific kind (e.g.
    # negatives) from starving a less prolific but valuable kind (e.g. a lone regression)
    # when the per-feature cap is reached. Order within each kind, and the fixed kind order,
    # stay deterministic, so the same input yields the same ordered titles.
    queues = []
    for producer in (_negative_scenarios, _boundary_scenarios,
                     _alt_flow_scenarios, _regression_scenarios):
        q = []
        for sc in producer(ac_lines, chalk_lines, fid):
            nt = _norm_title(sc.title)
            if not nt or nt in existing:
                continue
            existing.add(nt)
            q.append(sc)
        queues.append(q)

    n_kinds = len(queues)
    idx = 0
    capped = False
    while any(queues) and not capped:
        q = queues[idx % n_kinds]
        idx += 1
        if not q:
            continue
        sc = q.pop(0)
        derived.append(sc)
        prefix = (sc.source.source_id.rsplit('-', 1)[0]) if sc.source else ''
        if prefix in counts:
            counts[prefix] += 1
        if len(derived) >= _MAX_DERIVED:
            _log('[ANALYST-DERIVE] cap reached (%d) for %s' % (_MAX_DERIVED, fid))
            capped = True

    _log('[ANALYST-DERIVE] %s: %s' % (fid, counts))
    return derived
