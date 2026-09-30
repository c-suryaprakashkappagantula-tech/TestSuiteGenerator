# -*- coding: utf-8 -*-
"""output_sanitizer.py - one place that makes generated TC text reviewer-clean.

Every rule here is OUTPUT-ONLY: it rewrites text that is about to be written to the
workbook. It never changes which scenarios exist, how many TCs are built, or which
step template is chosen. That keeps the blast radius to wording, so fixing a leak
can't break coverage somewhere else.

Leak classes handled (found by a sweep of all 194 buildable cached features):
  - Confluence macros: {{field}} keeps its text; {panel}/{code}/{color:..} are dropped
    WITH their macro word (the old scrub dropped only the braces -> 'unchangedpanel').
  - Jira wiki markup: *bold*, **, h3., ||table||, +_underline_+, [~user].
  - Section headers glued from the next Chalk table ("Negative Scenarios: Scenario #
    Test Scenario Validations ...").
  - Mis-decoded characters (\u00e2\u20ac\u201c for an en dash, \u00e1 / \xa0 for a space).
  - 'Old behaviour:' context lines written as steps -> moved to the Description.
  - TC names: every write path (including the CR/V7 path that skipped the name polish)
    gets the same short, punctuation-free name.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

# ---------------------------------------------------------------- characters
_MOJIBAKE = [
    ('\u00e2\u20ac\u201c', '-'), ('\u00e2\u20ac\u201d', '-'), ('\u00e2\u20ac\u2122', "'"),
    ('\u00e2\u20ac\u02dc', "'"), ('\u00e2\u20ac\u0153', '"'), ('\u00e2\u20ac\u009d', '"'),
    ('\u00e2\u20ac\u00a6', '...'), ('\u00c2\u00a0', ' '), ('\u0393\u00c7\u00f6', '-'),
    ('\u0393\u00c7\u00f4', '-'), ('\u00f9', '-'), ('\xa0', ' '), ('\ufffd', ''),
]

# ---------------------------------------------------------------- confluence macros
# {{monospace}} and the Jira-exported {{{}X{}}} form -> keep the inner text.
_MONO_RE = re.compile(r'\{\{\{\}([^{}]{0,80})\{\}\}\}|\{\{([^{}]{0,80})\}\}')
_MACRO_NAMES = (r'panel|code|noformat|quote|color|info|note|warning|tip|expand|anchor|'
                r'status|section|column|toc|html|jira|excerpt|div|span|cloak')
# {panel}, {panel:title=NSL}, {color:#ff0000}, {code:java} -> removed with the word
_MACRO_RE = re.compile(r'\{(?:' + _MACRO_NAMES + r')(?::[^{}\n]{0,80})?\}', re.I)
_MACRO_PARAM_RE = re.compile(r'\|\s*(?:language|title|linenumbers|borderStyle)\s*=[^|}\n]{0,40}', re.I)
# A macro word already glued on by an older brace-only scrub, at the end of a string.
_GLUED_MACRO_RE = re.compile(r'(?<=[a-z0-9.)])(?:panel|noformat)\s*$', re.I)
_STRAY_BRACE_RE = re.compile(r'[{}]')

# ---------------------------------------------------------------- wiki markup
_WIKI_HEADING_RE = re.compile(r'(?:(?<=\s)|^)h[1-6]\.\s+')
_WIKI_TABLE_RE = re.compile(r'\|\|')
_WIKI_UNDERLINE_RE = re.compile(r'\+_|_\+')
_WIKI_USER_RE = re.compile(r'\[~([^\]]+)\]')
# Asterisks are wiki bold/emphasis - except a single capital letter followed by
# asterisks, which Chalk uses as an error-code placeholder ('E***' = E<nnn>).
_STAR_PLACEHOLDER_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z])(\*{2,})')

# ---------------------------------------------------------------- section headers
_HDR = (r'(?:(?:NBOP|API|UI|NSL|ITMBO|E2E)\s+)?'
        r'(?:Positives?|Negatives?|Edge(?:\s+Case)?|Regression|Failure|Boundary|Happy\s+Path)\s+Scenarios?')
_TABLE_HDR = r'Scenario\s*(?:#|Number|No\.?|ID)\s*Test\s+Scenario'
# Header followed by the next table's column header: everything from here is junk.
_HDR_TABLE_RE = re.compile(_HDR + r'(?:\s+' + _HDR + r')?\s*:?\s*' + _TABLE_HDR + r'.*$', re.S)
# The next table's column header alone, trailing the text.
_TABLE_TAIL_RE = re.compile(r'\s*\b' + _TABLE_HDR + r'.*$', re.S)
# A section header that starts a new sentence mid-text: the rest belongs to the next
# Chalk section ("... for VZW lines. NBOP Positive Scenarios: Through NBOP, go to ...").
_HDR_SENTENCE_RE = re.compile(r'(?<=[.;!?])\s+' + _HDR + r'\s*:.*$', re.S)
# The same header twice in a row ("Edge Case Scenarios Edge Case Scenarios:")
_HDR_REPEAT_RE = re.compile(r'(' + _HDR + r')\s+\1\s*:?', re.I)
# A bare trailing header at the very end of the text.
# ...optionally followed by a truncated fragment of the next header ("... Edge Case Sc").
_HDR_TRAIL_RE = re.compile(r'(?<=[.;!?])?\s*\b' + _HDR + r'(?:\s+[A-Z][A-Za-z ]{0,40})?\s*:?\s*$')

# ---------------------------------------------------------------- context-not-step
_CONTEXT_STEP_RE = re.compile(
    r'^\s*(?:(?:old|previous|prior|legacy)\s+behaviou?r|previously|before\s+the\s+fix|'
    r'today\s*\(before\s+fix\))\s*[:\-]\s*', re.I)


def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean one body string (description / precondition / step / expected)."""
    if not text:
        return text
    t = str(text)
    for bad, good in _MOJIBAKE:
        if bad in t:
            t = t.replace(bad, good)
    t = _MONO_RE.sub(lambda m: (m.group(1) or m.group(2) or ''), t)
    t = _MACRO_PARAM_RE.sub('', t)
    t = _MACRO_RE.sub(' ', t)
    t = _GLUED_MACRO_RE.sub('', t)
    t = _STRAY_BRACE_RE.sub('', t)
    t = _WIKI_USER_RE.sub(r'\1', t)
    t = _WIKI_HEADING_RE.sub('', t)
    t = _WIKI_TABLE_RE.sub(' ', t)
    t = _WIKI_UNDERLINE_RE.sub('', t)
    if '*' in t:
        t = _STAR_PLACEHOLDER_RE.sub(lambda m: m.group(1) + '\x00' * len(m.group(2)), t)
        # dial/feature codes (*228, *#06#) and arithmetic (5 * 3) keep their asterisk
        t = re.sub(r'\*(?=[#\d])|(?<=\d)\s\*\s(?=\d)', lambda m: m.group(0).replace('*', '\x00'), t)
        t = t.replace('*', '').replace('\x00', '*')
    t = _HDR_TABLE_RE.sub('', t)
    t = _TABLE_TAIL_RE.sub('', t)
    t = _HDR_SENTENCE_RE.sub('', t)
    t = _HDR_REPEAT_RE.sub(r'\1', t)
    t = _HDR_TRAIL_RE.sub('', t)
    # tidy whitespace without flattening intentional line breaks
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r' +([.,;:])', r'\1', t)
    return t.strip()


def clean_name(summary: str) -> str:
    """Short, punctuation-free TC name for every path (V8 and CR/V7 alike)."""
    if not summary:
        return summary
    s = str(summary)
    for bad, good in _MOJIBAKE:
        if bad in s:
            s = s.replace(bad, good)
    s = _MONO_RE.sub(lambda m: (m.group(1) or m.group(2) or ''), s)
    s = _MACRO_RE.sub(' ', s)
    s = _GLUED_MACRO_RE.sub('', s)
    s = re.sub(r'[\u2013\u2014]', '-', s)
    s = re.sub(r'[\u2022\u00b7\u25cf\u25aa\u25e6\u2023]', ' ', s)   # bullets
    s = re.sub(r'\s+', '_', s.strip())           # names are underscore-joined
    s = re.sub(r'_-_|_-|-_', '_', s)
    try:
        from .tc_name_polish import clean_summary_name
        # Punctuation/detail cleanup only - no word cap here. The V8 path already caps its
        # own names in polish_suite_names; capping again at the chokepoint cut CR/V7 names
        # mid-thought ('...invalid msi no longer occurs' -> '...no longer').
        s = clean_summary_name(s, max_words=40)
    except Exception:
        s = re.sub(r"[()\[\]{}<>+!\"'`,:;/\\|=&%@#$?*~^]", '', s)
    return re.sub(r'_{2,}', '_', s).strip('_-. ')


def sanitize_suite(suite, log: Callable = print) -> dict:
    """Apply every output rule to every TC. Returns counts per rule for the log."""
    counts = {'text': 0, 'names': 0, 'context_steps': 0}
    for tc in (getattr(suite, 'test_cases', None) or []):
        # 1. 'Old behaviour: X' written as a step. A tester cannot re-run pre-fix
        #    behaviour, but they CAN check it no longer happens - so the step becomes an
        #    executable log check that names the old failure (reviewer's rewrite on 4503).
        for st in (getattr(tc, 'steps', None) or []):
            summ = getattr(st, 'summary', '') or ''
            if not _CONTEXT_STEP_RE.match(summ):
                continue
            old = _CONTEXT_STEP_RE.sub('', summ).strip().rstrip(' .')
            if not old:
                continue
            low = (old + ' ' + (getattr(tc, 'description', '') or '') + ' ' +
                   (getattr(tc, 'summary', '') or '')).lower()
            where = ('ingestion' if re.search(r'ingest|prr|batch|file|record', low) else 'processing')
            o = old.lower()
            if 'overflow' in o:
                err = 'parse/overflow error'
            elif 'numberformat' in o or 'number format' in o:
                err = 'parse/NumberFormat error'
            elif 'parse' in o:
                err = 'parse error'
            else:
                err = 'error'
            obj = 'record' if 'record' in low else 'transaction'
            first = old.split(' ', 1)[0]
            if re.fullmatch(r'[A-Z][a-z]+', first):      # 'The ...' -> 'the ...'; keep NumberFormat/NUMERIC
                old = old[0].lower() + old[1:]
            st.summary = 'Check the NSL %s log for the %s (pre-fix, %s).' % (where, obj, old)
            if 'drop' in o:
                st.expected = 'No %s logged; the %s is not dropped.' % (err, obj)
            else:
                st.expected = 'No %s logged; the pre-fix failure does not occur.' % err
            counts['context_steps'] += 1
        # 2. body text
        for attr in ('description', 'preconditions'):
            v = getattr(tc, attr, None)
            if v:
                nv = clean_text(v)
                if nv != v:
                    setattr(tc, attr, nv)
                    counts['text'] += 1
        for st in (getattr(tc, 'steps', None) or []):
            for attr in ('summary', 'expected'):
                v = getattr(st, attr, None)
                if v:
                    nv = clean_text(v)
                    if nv != v:
                        setattr(st, attr, nv)
                        counts['text'] += 1
        # 3. name
        n = getattr(tc, 'summary', '') or ''
        nn = clean_name(n)
        if nn and nn != n:
            tc.summary = nn
            counts['names'] += 1
    if log and any(counts.values()):
        log('[SANITIZE] cleaned %(text)d text field(s), %(names)d name(s), moved '
            '%(context_steps)d context line(s) from steps to Description' % counts)
    return counts


# ---------------------------------------------------------------- unique names
_STOP = {'the', 'a', 'an', 'is', 'are', 'of', 'to', 'and', 'or', 'on', 'in', 'at', 'for',
         'with', 'that', 'then', 'when', 'be', 'via', 'as', 'it', 'its', 'by', 'from',
         'this', 'these', 'those', 'will', 'should', 'must', 'validate', 'verify',
         'ensure', 'confirm', 'check', 'test', 'scenario', 'displayed', 'screen', 'page'}


def _name_key(s):
    s = re.sub(r'(?:^|_)(?:TC\d+_)+', '_', s or '')
    s = re.sub(r'^_?[A-Z][A-Z0-9]+-\d+_', '', s)
    return re.sub(r'_{2,}', '_', s).strip('_').lower()


def _words(text):
    t = re.sub(r'^\s*(?:UI verification:|To validate:|Validate:|Verify:)\s*', '', text or '', flags=re.I)
    return re.findall(r'[A-Za-z0-9]+', t)


def dedupe_names(suite, log: Callable = print) -> int:
    """Give every TC in a name-collision group a unique suffix made of the words that
    set ITS description (then its steps) apart from the rest of the group. Falls back
    to Variant_N only when the TCs are textually identical."""
    tcs = getattr(suite, 'test_cases', None) or []
    groups = {}
    for tc in tcs:
        groups.setdefault(_name_key(getattr(tc, 'summary', '')), []).append(tc)
    fixed = 0
    taken = {_name_key(getattr(tc, 'summary', '')) for tc in tcs}
    for key, grp in groups.items():
        if len(grp) < 2 or not key:
            continue
        name_words = set(key.split('_'))

        def distinct(get):
            bags = [[w for w in _words(get(tc))] for tc in grp]
            common = set(w.lower() for w in bags[0])
            for b in bags[1:]:
                common &= set(w.lower() for w in b)
            out = []
            for b in bags:
                picked, seen = [], set()
                for w in b:
                    wl = w.lower()
                    if wl in common or wl in _STOP or wl in name_words or wl in seen:
                        continue
                    seen.add(wl)
                    picked.append(w)
                    if len(picked) >= 4:
                        break
                out.append('_'.join(picked))
            return out

        by_desc = distinct(lambda t: getattr(t, 'description', '') or '')
        by_step = distinct(lambda t: ' '.join((getattr(st, 'summary', '') or '') + ' ' +
                                              (getattr(st, 'expected', '') or '')
                                              for st in (getattr(t, 'steps', None) or [])))
        used = set()
        for i, tc in enumerate(grp):
            tok = by_desc[i] or by_step[i]
            if not tok or tok.lower() in used:
                tok = (tok + '_' if tok else '') + 'Variant_%d' % (i + 1)
            used.add(tok.lower())
            base = (getattr(tc, 'summary', '') or '').rstrip('_')
            new = '%s_%s' % (base, tok)
            n = 2
            while _name_key(new) in taken:
                new = '%s_%s_%d' % (base, tok, n); n += 1
            taken.add(_name_key(new))
            tc.summary = new
            fixed += 1
    if log and fixed:
        log('[SANITIZE] %d colliding TC name(s) made unique' % fixed)
    return fixed
