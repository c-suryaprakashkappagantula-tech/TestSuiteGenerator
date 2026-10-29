"""Scope-exclusion EXTRACTOR + conservative MATCHER (FEAT-002).

Pure, deterministic, offline, NO-LLM module. It scans a feature's OWN evidence
(Jira comments + acceptance criteria + description + summary + Chalk
scope/rules/owned-scenarios) for EXPLICIT exclusion statements and derives the set
of excluded-subject tokens/phrases from the evidence itself.

Design rules (see FEAT-002):
  * No I/O, no network, no LLM, no streamlit. Only ``re`` and a lazy import of
    ``dimension_extractor._chalk_obligation_text`` for the Chalk evidence blob.
  * Nothing feature-specific is hardcoded — there is no '4074' or 'fan-out' literal
    used as a trigger. The excluded subjects are captured from whatever exclusion
    sentence the evidence contains; the only generalisation is a small, bounded set
    of hyphen<->space and cross-/multi-environment synonym variants applied to the
    captured phrase.
  * The matcher is conservative: it only matches on a multi-word phrase (>= 2 words)
    or a distinctive compound (fan-out / multi-environment / across environments), so
    a single broad keyword such as 'environment' can never drop an in-scope TC.
"""

import re

__all__ = [
    'build_exclusion_evidence',
    'extract_excluded_subjects',
    'scenario_matches_excluded',
]


def _norm(text):
    """Lowercase + collapse underscores/whitespace.

    Mirrors ``analyst_validity._norm`` so tokens compare consistently across modules.
    Pure, deterministic."""
    return re.sub(r'[\s_]+', ' ', (text or '').lower()).strip()


def build_exclusion_evidence(jira, chalk) -> str:
    """Build the feature's OWN lowercased exclusion-evidence blob.

    Sources (all tolerant of None / missing attributes):
      * Jira comments (each dict's 'body'),
      * Jira acceptance_criteria (str or list),
      * Jira description, Jira summary,
      * Chalk text via ``dimension_extractor._chalk_obligation_text`` (falls back to
        chalk.feature_title/scope/rules on exception, matching
        ``analyst_validity.build_feature_evidence``).

    Pure, deterministic. Returns a normalized blob (lowercased, whitespace-collapsed).
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
        for c in (getattr(jira, 'comments', None) or []):
            if isinstance(c, dict):
                parts.append(str(c.get('body', '') or ''))
            else:
                parts.append(str(getattr(c, 'body', '') or c or ''))
    if chalk is not None:
        try:
            from .dimension_extractor import _chalk_obligation_text
            parts.append(_chalk_obligation_text(chalk) or '')
        except Exception:
            # Fail-safe: fall back to the plain feature-owned Chalk panels.
            for attr in ('feature_title', 'scope', 'rules'):
                parts.append(getattr(chalk, attr, '') or '')
    return _norm('\n'.join(p for p in parts if p))


# ─── Exclusion markers ──────────────────────────────────────────────
# Case-insensitive markers that signal an EXPLICIT scope exclusion. Each entry is a
# compiled regex; ``subject_group`` names a capture group that holds the excluded
# subject phrase when the marker itself carries the subject (e.g. 'excluded for X',
# 'scope excluded for X', 'X is out of scope'). Markers without a subject group fall
# back to the containing clause/sentence for subject capture.
_MARKERS = [
    # "... excluded for cross-environment fan-out ..."  /  "scope excluded for X"
    (re.compile(r'\bscope excluded for\b\s+(?P<subject>[^.;:\n]+)'), 'subject'),
    (re.compile(r'\bexcluded for\b\s+(?P<subject>[^.;:\n]+)'), 'subject'),
    # "X is out of scope"  — subject is the noun phrase immediately before the marker
    (re.compile(r'(?P<subject>[^.;:\n]+?)\s+\bis out of scope\b'), 'subject'),
    # Bare markers — no inline subject group; use the containing clause.
    (re.compile(r'\bscope excluded\b'), None),
    (re.compile(r'\bout of scope\b'), None),
    (re.compile(r'\bnot in scope\b'), None),
    (re.compile(r'\bexcluded\b.*\bscope\b'), None),
    (re.compile(r'\bexcluded\b'), None),
]

# Noise words trimmed from the leading edge of a captured subject phrase. Kept small
# and general — these are connectives that commonly precede the excluded subject.
_LEADING_NOISE = (
    'the', 'a', 'an', 'any', 'all', 'is', 'are', 'be', 'being', 'to', 'of', 'for',
    'on', 'in', 'with', 'this', 'that', 'these', 'those', 'and', 'or',
)
# Trailing connectives that commonly dangle after the subject noun phrase.
_TRAILING_NOISE = (
    'on', 'in', 'for', 'to', 'of', 'at', 'with', 'is', 'are', 'and', 'or', 'the',
)


def _clause_around(blob: str, start: int, end: int) -> str:
    """Return the clause/sentence of *blob* that contains [start, end).

    A clause boundary is a sentence terminator or a semicolon/colon/newline — the same
    separators the subject capture stops at — so the fallback stays consistent.
    """
    left = max(
        (blob.rfind(sep, 0, start) for sep in ('.', ';', ':', '!', '?', '\n')),
        default=-1,
    )
    right_candidates = [
        blob.find(sep, end) for sep in ('.', ';', ':', '!', '?', '\n')
    ]
    right_candidates = [r for r in right_candidates if r != -1]
    right = min(right_candidates) if right_candidates else len(blob)
    return blob[left + 1:right].strip()


def _trim_subject(phrase: str) -> str:
    """Trim leading/trailing connective noise words from a captured subject phrase."""
    words = _norm(phrase).split()
    while words and words[0] in _LEADING_NOISE:
        words.pop(0)
    while words and words[-1] in _TRAILING_NOISE:
        words.pop()
    return ' '.join(words)


def _expand_variants(phrase: str):
    """Expand a captured subject phrase into a small, general token/phrase variant set.

    Rules (bounded, evidence-driven):
      * hyphen <-> space on every token (so 'cross-environment' also yields
        'cross environment' and vice versa),
      * known cross-env synonyms: cross-environment <-> multi-environment,
      * 'fan-out' <-> 'fan out'.

    The captured compound phrase and each of its hyphen-compound tokens are emitted
    individually, so 'cross-environment fan-out' contributes the whole phrase plus
    'cross-environment' and 'fan-out' as standalone distinctive compounds.
    """
    phrase = _trim_subject(phrase)
    if not phrase:
        return set()

    out = set()

    def _add(p):
        p = p.strip()
        if not p:
            return
        out.add(p)
        # hyphen <-> space variants
        if '-' in p:
            out.add(p.replace('-', ' '))
        spaced = p.replace('-', ' ')
        # join two-word spaced forms back into a hyphen compound (e.g. 'fan out')
        if ' ' in spaced:
            words = spaced.split()
            if len(words) == 2:
                out.add('-'.join(words))

    _add(phrase)
    # Emit distinctive hyphen-compound tokens from the phrase individually.
    for tok in phrase.split():
        if '-' in tok:
            _add(tok)

    # Known, bounded cross-env synonym expansion driven by the captured phrase.
    joined = ' '.join(sorted(out))
    if re.search(r'\bcross[\s-]environment\b', joined):
        for v in ('cross-environment', 'cross environment',
                  'multi-environment', 'multi environment'):
            _add(v)
    if re.search(r'\bmulti[\s-]environment\b', joined):
        for v in ('multi-environment', 'multi environment',
                  'cross-environment', 'cross environment'):
            _add(v)
    if re.search(r'\bfan[\s-]out\b', joined):
        _add('fan-out')
        _add('fan out')

    return out


def extract_excluded_subjects(jira, chalk, log=print):
    """Return the set of normalized excluded-subject tokens/phrases for a feature.

    Runs the exclusion markers over the feature's own evidence blob
    (``build_exclusion_evidence``). For each marker hit it captures the subject phrase
    (inline subject group, or the containing clause for bare markers), then emits a
    normalized token/phrase set via ``_expand_variants``.

    Returns an empty set when no marker is found. Logs a one-line summary.
    """
    blob = build_exclusion_evidence(jira, chalk)
    subjects = set()
    if not blob:
        log('[SCOPE-EXCL] no evidence; excluded subjects: 0')
        return subjects

    for pattern, subject_group in _MARKERS:
        for m in pattern.finditer(blob):
            if subject_group:
                raw = m.group(subject_group) or ''
            else:
                raw = _clause_around(blob, m.start(), m.end())
            subjects |= _expand_variants(raw)

    log('[SCOPE-EXCL] excluded subjects: %d%s' % (
        len(subjects),
        (' -> ' + ', '.join(sorted(subjects))) if subjects else ''))
    return subjects


def _subject_text(subject) -> str:
    """Collect a scenario/TC's own title/summary/validation + step text, normalized.

    Mirrors ``analyst_validity._subject_text`` field coverage so matching sees the same
    surface as the validity gate. Tolerant of None / missing attributes.
    """
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


# Distinctive single-token compounds that ARE allowed to match on their own because
# they are specific enough never to hit an in-scope TC. Generated generically: any
# hyphen compound, plus the bounded cross-env family.
_DISTINCTIVE = {
    'fan-out', 'fan out',
    'multi-environment', 'multi environment',
    'cross-environment', 'cross environment',
    'across environments',
}


def _is_matchable(phrase: str) -> bool:
    """True when *phrase* is safe to match on: multi-word, or a distinctive compound."""
    p = phrase.strip()
    if not p:
        return False
    if p in _DISTINCTIVE:
        return True
    if '-' in p:                      # any hyphen compound is distinctive enough
        return True
    return len(p.split()) >= 2        # otherwise require >= 2 words


def scenario_matches_excluded(subject, excluded_subjects):
    """Conservatively test whether *subject* falls under an excluded subject.

    *subject* is a TestCase or ExtractedScenario. Returns ``(True, matched_phrase)``
    only when a MATCHABLE excluded phrase (>= 2 words, or a distinctive compound like
    'fan-out'/'multi-environment'/'across environments') appears in the subject's own
    text. Never matches on a single broad keyword. Returns ``(False, '')`` when
    *excluded_subjects* is empty or nothing matches.
    """
    if not excluded_subjects:
        return False, ''
    text = _subject_text(subject)
    if not text:
        return False, ''
    # Longest phrases first so the reported match is the most specific.
    for phrase in sorted(excluded_subjects, key=lambda p: (-len(p), p)):
        norm_phrase = _norm(phrase)
        if not _is_matchable(norm_phrase):
            continue
        if norm_phrase in text:
            return True, norm_phrase
    return False, ''
