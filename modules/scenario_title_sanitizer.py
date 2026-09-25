"""Supplement scenario title sanitizer (Option C, part 2).

Chalk is ground truth and is NEVER touched here — rule #1 is absolute: every Chalk
scenario becomes a test case exactly as parsed. This module only cleans the titles of
SUPPLEMENT scenarios (Jira AC, subtask mines, attachments, related features) so the
suite stops shipping names the reviewers rejected:

  1. feature-title-as-TC  -> dropped (zero added coverage, it restates the story)
       "[NSLNM, INTG]: CR - New MVNO - NSL accepting TMO Add Wearable request ..."
  2. raw Gherkin          -> rewritten from the outcome (then-clause)
       "Given a TMO Add Wearable request ... when processed, then the request passes
        validation and activation proceeds as normal"
         -> "Request passes validation and activation proceeds as normal"
  3. ticket furniture     -> stripped ("[TAGS]:", "CR - New MVNO -", "Area:")

Generic by construction: no feature ids, no PI names, no per-ticket special cases.
"""
from __future__ import annotations

import re
from typing import Callable, List

# Source types that ARE ground truth. Left completely alone.
CHALK_SOURCE_TYPES = {
    'chalk', 'chalk scenario', 'chalk db scenario', 'business rule',
    'nbop ui', 'nbop',
}

# Longest title we emit. Titles are trimmed on a word boundary, never mid-word.
MAX_TITLE_LEN = 100

# Minimum usable title length after cleaning.
MIN_TITLE_LEN = 12

# Leading ticket furniture: "[NSLNM, INTG]:", "CR - New MVNO -", "Area:", "Scenario:"
_PREFIX_TAGS_RE = re.compile(r'^\s*\[[^\]]{1,60}\]\s*:?\s*')
_PREFIX_CR_RE = re.compile(
    r'^\s*(?:cr|change\s+request|bug\s*fix|defect)\s*[-–—:]\s*'
    r'(?:new\s+mvno\s*[-–—:]\s*)?', re.IGNORECASE)
_PREFIX_NOISE_RE = re.compile(
    r'^\s*(?:area|scenario|test\s+scenario|test\s+case|tc|section)\s*[:\-–—]\s*',
    re.IGNORECASE)
_PREFIX_VERIFY_RE = re.compile(
    r'^\s*(?:verify|validate|ensure|check|confirm)\s+(?:that\s+)?', re.IGNORECASE)
_TRAILING_COUNT_RE = re.compile(r'\s*\(\s*\d+\s+scenarios?\s*\)\s*$', re.IGNORECASE)

# Gherkin: given ... when ... then ...   (then-clause is the testable outcome)
_GHERKIN_RE = re.compile(
    r'\bgiven\b(?P<given>.*?)[,;]?\s*\bwhen\b(?P<when>.*?)[,;]?\s*\bthen\b(?P<then>.*)$',
    re.IGNORECASE | re.DOTALL)
# "when ... then ..." with no explicit given
_WHEN_THEN_RE = re.compile(
    r'\bwhen\b(?P<when>.*?)[,;]?\s*\bthen\b(?P<then>.*)$',
    re.IGNORECASE | re.DOTALL)
# Bare "then ..."
_THEN_ONLY_RE = re.compile(r'\bthen\b(?P<then>.*)$', re.IGNORECASE | re.DOTALL)

_LEADING_ARTICLE_RE = re.compile(r'^(?:the\s+same\s+|the\s+|a\s+|an\s+)', re.IGNORECASE)

# A then-clause ending in one of these is dangling — it points at context that only
# the when-clause carries, so we splice the when-clause back in.
_DANGLING_TAIL = {
    'both', 'it', 'this', 'that', 'same', 'the same', 'as expected', 'expected',
    'above', 'below', 'these', 'those', 'them', 'so', 'such',
}
_VAGUE_MIN_WORDS = 6

# Once a dangling tail is removed the clause can end on a preposition/conjunction
# ("... is consistent across"). Strip those so the spliced title reads naturally.
_TRAILING_JOINER_RE = re.compile(
    r'(?:\s+(?:across|between|among|for|in|on|at|to|of|with|within|by|from|into|'
    r'over|under|and|or|both))+$', re.IGNORECASE)


def _norm(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for equality comparisons."""
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', (s or '').lower()).split())


def _tokens(s: str) -> set:
    return set(_norm(s).split())


def _trim_words(s: str, limit: int = MAX_TITLE_LEN) -> str:
    """Trim to `limit` chars on a word boundary; never cut a word in half."""
    s = (s or '').strip()
    if len(s) <= limit:
        return s
    cut = s[:limit + 1]
    sp = cut.rfind(' ')
    if sp <= 0:
        sp = limit
    return cut[:sp].rstrip(' ,;:-–—')


def _strip_furniture(text: str) -> str:
    """Remove leading ticket furniture and trailing "(N scenarios)"."""
    out = (text or '').strip()
    for _ in range(4):  # prefixes stack: "[TAGS]: CR - New MVNO - Area: ..."
        before = out
        out = _PREFIX_TAGS_RE.sub('', out)
        out = _PREFIX_CR_RE.sub('', out)
        out = _PREFIX_NOISE_RE.sub('', out)
        if out == before:
            break
    out = _TRAILING_COUNT_RE.sub('', out)
    return ' '.join(out.split()).strip(' .,;:-–—')


def _sentence_case(s: str) -> str:
    """Uppercase the first letter, leave the rest (acronyms like VZW/TMO/NSL) intact."""
    s = (s or '').strip()
    if not s:
        return s
    return s[0].upper() + s[1:]


def _clean_clause(clause: str) -> str:
    c = ' '.join((clause or '').split()).strip(' .,;:-–—')
    c = _LEADING_ARTICLE_RE.sub('', c)
    return c.strip(' .,;:-–—')


def _is_vague(clause: str) -> bool:
    n = _norm(clause)
    if not n:
        return True
    words = n.split()
    if len(words) < _VAGUE_MIN_WORDS:
        return True
    if words[-1] in _DANGLING_TAIL:
        return True
    for tail in _DANGLING_TAIL:
        if n.endswith(' ' + tail):
            return True
    return False


def rewrite_gherkin_title(text: str) -> str:
    """Turn a Gherkin sentence into an outcome-first title.

    Uses the then-clause (the assertion). When that clause is vague or dangling,
    splices the when-clause back in so sibling scenarios stay distinguishable.
    Returns '' when the text is not Gherkin.
    """
    if not text:
        return ''
    src = ' '.join(str(text).split())
    m = _GHERKIN_RE.search(src) or _WHEN_THEN_RE.search(src)
    when_clause = ''
    if m:
        then_clause = _clean_clause(m.group('then'))
        when_clause = _clean_clause(m.groupdict().get('when', '') or '')
    else:
        m2 = _THEN_ONLY_RE.search(src)
        if not m2:
            return ''
        then_clause = _clean_clause(m2.group('then'))

    if not then_clause:
        # Nothing assertable — fall back to the action.
        return _sentence_case(_trim_words(when_clause)) if when_clause else ''

    if _is_vague(then_clause) and when_clause:
        # "behavior is consistent across both" + "tested across both A and B flows"
        #   -> "Behavior is consistent when tested across both A and B flows"
        base = then_clause
        for tail in sorted(_DANGLING_TAIL, key=len, reverse=True):
            if _norm(base).endswith(' ' + tail) or _norm(base) == tail:
                idx = base.lower().rfind(tail)
                if idx > 0:
                    base = base[:idx].strip(' .,;:-–—')
                break
        # Drop any preposition/conjunction left dangling by the tail removal.
        base = _TRAILING_JOINER_RE.sub('', base).strip(' .,;:-–—')
        joined = '%s when %s' % (base, when_clause) if base else when_clause
        return _sentence_case(_trim_words(joined))

    return _sentence_case(_trim_words(then_clause))


def looks_like_gherkin(text: str) -> bool:
    if not text:
        return False
    t = ' ' + _norm(text) + ' '
    return (' given ' in t and ' then ' in t) or (' when ' in t and ' then ' in t)


def clean_supplement_title(title: str, detail: str = '') -> str:
    """Clean one supplement title. Returns '' when nothing usable remains.

    `detail` is the fuller source text (validation/description); it is used to
    recover an outcome when the title itself was truncated mid-Gherkin.
    """
    raw = ' '.join((title or '').split())
    if not raw:
        return ''

    base = _strip_furniture(raw)
    base = _PREFIX_VERIFY_RE.sub('', base).strip(' .,;:-–—')

    # Prefer the fuller detail text for Gherkin, since titles arrive truncated.
    candidates = []
    if detail:
        candidates.append(_strip_furniture(' '.join(str(detail).split())))
    candidates.append(base)

    for cand in candidates:
        if looks_like_gherkin(cand):
            rewritten = rewrite_gherkin_title(cand)
            if rewritten and len(rewritten) >= MIN_TITLE_LEN:
                return rewritten

    base = _trim_words(base)
    if len(base) < MIN_TITLE_LEN:
        return ''
    return _sentence_case(base)


def _source_type_of(scenario) -> str:
    tr = getattr(scenario, 'source', None) or getattr(scenario, 'traceability', None)
    st = getattr(tr, 'source_type', '') if tr else ''
    if not st:
        st = getattr(scenario, 'source_type', '') or ''
    return (st or '').strip().lower()


def is_chalk_scenario(scenario) -> bool:
    """True when the scenario is Chalk ground truth (must never be altered/dropped)."""
    if getattr(scenario, 'from_chalk', False):
        return True
    return _source_type_of(scenario) in CHALK_SOURCE_TYPES


def sanitize_supplement_scenarios(
    scenarios: List,
    feature_summary: str = '',
    log: Callable = print,
) -> List:
    """Clean supplement scenario titles in place; drop feature-title restatements.

    Chalk scenarios and user-requested (custom-instruction) scenarios pass through
    untouched. Returns the filtered list.
    """
    if not scenarios:
        return scenarios

    feat_clean = _strip_furniture(feature_summary or '')
    feat_norm = _norm(feat_clean)
    feat_tokens = _tokens(feat_clean)

    kept, renamed, dropped_title, dropped_empty = [], 0, 0, 0

    for sc in scenarios:
        # Rule #1: Chalk is ground truth. Never rename, never drop.
        if is_chalk_scenario(sc):
            kept.append(sc)
            continue
        # Explicit user requests are protected too.
        if getattr(sc, 'user_requested', False):
            kept.append(sc)
            continue

        original = getattr(sc, 'title', '') or ''
        detail = (getattr(sc, 'validation', '') or getattr(sc, 'description', '') or '')

        # Drop feature-title-as-TC: it restates the story and adds no coverage.
        cand_norm = _norm(_strip_furniture(_PREFIX_VERIFY_RE.sub('', original)))
        if feat_norm and cand_norm:
            if cand_norm == feat_norm:
                dropped_title += 1
                continue
            ct = _tokens(cand_norm)
            if ct and feat_tokens and not looks_like_gherkin(original):
                overlap = len(ct & feat_tokens) / max(1, len(ct | feat_tokens))
                if overlap >= 0.85:
                    dropped_title += 1
                    continue

        cleaned = clean_supplement_title(original, detail)
        if not cleaned:
            dropped_empty += 1
            continue

        if _norm(cleaned) != _norm(original):
            try:
                sc.title = cleaned
                renamed += 1
            except Exception:
                pass
        kept.append(sc)

    if renamed or dropped_title or dropped_empty:
        log('[TITLE-SANITIZE]   supplements: %d renamed, %d feature-title dropped, '
            '%d unusable dropped (Chalk untouched)' % (renamed, dropped_title, dropped_empty))
    return kept
