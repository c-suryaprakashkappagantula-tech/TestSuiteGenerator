"""
tc_name_polish.py — Final polish pass on generated TC summary names.

Encodes the naming conventions a reviewer applies by hand after generation:

  1. STRIP IMPLEMENTATION DETAIL — storage types (VARCHAR(45), NUMERIC(15)),
     category labels that duplicate the Category column ((regression)), and bare
     value enumerations ((3289, 3290, 8886)). These belong in the steps or in
     their own column, not in the test-case name.

  2. SUBJECT PREFIX — a title that starts with a bare data field
     ("rating_group_code containing a string code") reads as a fragment. It
     becomes "<Subject> with rating_group_code containing a string code", where
     <Subject> is the entity the feature operates on.

     The subject is MINED FROM CHALK, never invented: it is the noun phrase the
     test architect already used in a "Verify a <X> with ..." scenario title on
     the same feature. When no such phrase exists, the rule is skipped entirely.
     That keeps the name grounded in the feature's own vocabulary.

Titles describing the fix/behaviour itself ("Verify the fix applies across ...")
are left alone — they have a subject already.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, List, Optional

# ── Rule 1: implementation detail that should not be in a TC name ────────────

_SQL_TYPES = r'VARCHAR|NUMERIC|NUMBER|CHAR|INT|INTEGER|DECIMAL|FLOAT|BIGINT|TEXT|BLOB'

_DROP_PAREN_RE = re.compile(
    r'_?\((?:'
    r'\s*(?:was\s+)?(?:' + _SQL_TYPES + r')[\s_]*\(?\d*\)?.*?'          # data types
    r'|\s*(?:regression|negative|positive|happy[\s_]*path|edge[\s_]*case'  # category dupes
    r'|e2e|integration|sanity|smoke)\s*'
    r'|[\d_,\s]+'                                                          # value lists
    r')\)?$', re.IGNORECASE)

# Clause whose only purpose was to introduce a stripped data type.
_DROP_TYPE_CLAUSE_RE = re.compile(
    r'_(?:is|are)_(?:stored|declared|defined|typed|persisted)_as(?:_\w+)?$', re.IGNORECASE)

# A trailing word left hanging by truncation ("..._the_old_assumption_is").
_DANGLING_TAIL_RE = re.compile(
    r'_(?:is|are|was|were|be|been|being|the|a|an|to|of|with|for|and|or|in|on|at|'
    r'as|that|than|from|by|via|when|if|into|over|under|per)$', re.IGNORECASE)


def strip_implementation_detail(title: str) -> str:
    """Remove storage types, category dupes, value lists and dangling fragments."""
    t = title or ''
    for _ in range(4):
        before = t
        t = re.sub(r'_?\([^)]*$', '', t)          # unclosed paren from truncation
        t = _DROP_PAREN_RE.sub('', t)
        t = _DROP_TYPE_CLAUSE_RE.sub('', t)
        t = t.rstrip('_-,. ')
        if t == before:
            break
    for _ in range(3):
        before = t
        t = _DANGLING_TAIL_RE.sub('', t).rstrip('_-,. ')
        if t == before:
            break
    return t


# ── Rule 2: subject prefix ───────────────────────────────────────────────────

# The "TC01_" ordinal is prepended later by excel_generator, so it is optional here.
_HEAD_RE = re.compile(r'^((?:TC\d+_)?[A-Z][A-Z0-9]+-\d+_Verify)_(.*)$')

# "Verify a PRR record with ..." -> subject "PRR record"
_SUBJECT_MINE_RE = re.compile(
    r'\bverif(?:y|ies)\s+(?:a|an|the)\s+([A-Za-z][A-Za-z0-9 \-]{2,40}?)\s+with\b',
    re.IGNORECASE)

# Words meaning the title is about the CHANGE/behaviour, not a record instance.
_ABOUT_BEHAVIOUR_RE = re.compile(
    r'\b(fix|change|enhancement|processing|behaviou?r|logic|usage|support|flow|job)\b',
    re.IGNORECASE)


# Nouns that denote the THING under test, so a candidate containing one is a
# far better subject than a bare attribute phrase.
_ENTITY_NOUN_RE = re.compile(
    r'\b(record|request|file|message|transaction|order|event|payload|'
    r'line|subscriber|account|response|job|report|call)s?\b', re.IGNORECASE)


def mine_subject(scenario_titles: List[str]) -> Optional[str]:
    """Best "Verify a <X> with ..." noun phrase across this feature's Chalk titles.

    A candidate that is itself a bare data field ("used numeric field") is
    disqualified — that is precisely what the subject rule exists to prefix.
    Candidates naming an entity ("PRR record") are preferred over ones that do not.

    Returns None when the feature's Chalk never uses that construction — the
    subject rule is then skipped rather than guessed at.
    """
    found = Counter()
    for t in (scenario_titles or []):
        for m in _SUBJECT_MINE_RE.finditer(str(t or '')):
            phrase = ' '.join(m.group(1).split()).strip(' -')
            if not (3 <= len(phrase) <= 40):
                continue
            if re.fullmatch(r'(?:it|this|that|them|these|those)', phrase, re.IGNORECASE):
                continue
            # Disqualify bare-attribute phrases — they are targets, not subjects.
            if re.search(r'\bfields?\b|\bvalues?\b|\bcolumns?\b', phrase, re.IGNORECASE):
                continue
            if re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+', phrase):
                continue
            found[phrase] += 1
    if not found:
        return None
    # Prefer an entity-bearing phrase; break ties on frequency, then brevity.
    return max(found.items(),
               key=lambda kv: (bool(_ENTITY_NOUN_RE.search(kv[0])), kv[1], -len(kv[0])))[0]


def _leading_noun_phrase(rest: str) -> str:
    r = re.sub(r'^(?:a|an|the)_', '', rest, flags=re.IGNORECASE)
    for sep in ('_containing', '_with', '_are', '_is', '_that', '_when'):
        r = r.split(sep)[0]
    return r


def _names_a_bare_data_field(np: str) -> bool:
    """True when the leading noun phrase is a data field rather than a subject."""
    if not np or _ABOUT_BEHAVIOUR_RE.search(np):
        return False
    if re.search(r'\bfields?\b', np, re.IGNORECASE):
        return True
    toks = [t for t in np.split('_') if t]
    # snake_case identifier: session_ip_addr, rating_group_code, data_volume_ctr
    if len(toks) >= 2 and all(re.fullmatch(r'[a-z][a-z0-9]*', t) for t in toks):
        return True
    # single identifier carrying a digit: data1
    if len(toks) == 1 and re.fullmatch(r'[a-z][a-z0-9]*\d[a-z0-9]*', toks[0]):
        return True
    return False


def apply_subject_prefix(title: str, subject: str) -> str:
    """Insert "<subject>_with_" when the title opens on a bare data field."""
    if not subject:
        return title
    m = _HEAD_RE.match(title or '')
    if not m:
        return title
    head, rest = m.group(1), m.group(2)
    subj = subject.replace(' ', '_')
    if subj.lower() in (title or '').lower():
        return title                      # subject already present
    if not _names_a_bare_data_field(_leading_noun_phrase(rest)):
        return title
    return '%s_%s_with_%s' % (head, subj, rest)


# ── Suite-level entry point ──────────────────────────────────────────────────

def polish_suite_names(suite, chalk=None, log: Callable = print) -> object:
    """Apply the naming conventions to every TC summary in the suite.

    Never raises: a title that cannot be polished is left exactly as it was.
    """
    try:
        titles = [getattr(s, 'title', '') for s in getattr(chalk, 'scenarios', [])] \
            if chalk is not None else []
        subject = mine_subject(titles)

        n_strip = n_subj = 0
        for tc in (getattr(suite, 'test_cases', []) or []):
            try:
                original = getattr(tc, 'summary', '') or ''
                if not original:
                    continue
                stripped = strip_implementation_detail(original)
                if stripped and stripped != original:
                    n_strip += 1
                final = apply_subject_prefix(stripped, subject) if subject else stripped
                if final != stripped:
                    n_subj += 1
                if final and final != original:
                    tc.summary = final
            except Exception:
                continue

        if log and (n_strip or n_subj):
            log('[NAME-POLISH] %d title(s) had implementation detail stripped, '
                '%d got subject prefix%s'
                % (n_strip, n_subj, (' "%s"' % subject) if subject else ''))
        elif log and subject:
            log('[NAME-POLISH] subject mined from Chalk: "%s" — no titles needed changes'
                % subject)
    except Exception as exc:
        if log:
            try:
                log('[NAME-POLISH] skipped (non-fatal): %s' % str(exc)[:90])
            except Exception:
                pass
    return suite
