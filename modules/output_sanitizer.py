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


# ================================================================ reviewer polish
# Second output-only pass (PI-59 bulk audit, 2026-10-01). Each rule fixes one class of
# text a reviewer flagged; none adds coverage. The only structural edits are dropping a
# step that repeats an earlier step of the same TC verbatim, or a one-word fragment step
# ('Integration') - both renumber step_num so the sheet stays 1..N.
_POL_STEP_PREFIX_RE = re.compile(r'^\s*Step\s*\d+[a-z]?\s*[:.)\-]?\s+(?=\S)', re.I)
_POL_THAT_RE = re.compile(r'\bThat:\s*')
_POL_DOUBLE_RE = re.compile(r'\b([A-Za-z]{2,})\s+\1\b', re.I)
_POL_SRC_TAG_RE = re.compile(r'\s*\[\d{4}(?:\s*/\s*\d{4})*\]\s*')
# A Chalk review note ('[DQ-1] Resolved: the HLD is ...') to the end of its sentence,
# with the 'Alternate Flow C:' label that introduced it.
_POL_DQ_RE = re.compile(r'\s*(?:Alternate\s+Flow\s+[A-Z]\s*:\s*)?\[DQ-\d+\][^.\n]*(?:\.(?=\s|$)|$)', re.I)
_POL_PLACEHOLDER_RE = re.compile(r'\bX{4,}\b')
_POL_NONE_PRE_RE = re.compile(r'^\s*\d+\.\s*(?:none|n/a)\b[^\n]*$', re.I | re.M)
_POL_ECHO_RE = re.compile(r'^(.*?)\s*[\u2014\-]+\s*confirmed\.?\s*$', re.S)
_POL_VERIFY_EXP_RE = re.compile(r'^\s*Verify(?:\s+that)?\s*:?\s+', re.I)
_POL_CHECK_VERB_RE = re.compile(r'^\s*(?:verify|validate|check|confirm|ensure)\b(?:\s+that)?\s*:?\s*', re.I)
_POL_ALT_FLOW_RE = re.compile(r'\s+(?=Alternate\s+Flow\s+[A-Z]\s*:)')
_POL_LONE_A_RE = re.compile(r'(?<=[\s_])\u00e2(?=[\s_])')
_POL_STRAY_A_RE = re.compile(r'\u00c2(?=\s|$|[.,;:])')
_POL_IMPERATIVE = {'enter': 'entered', 'search': 'completed', 'inspect': 'reviewed',
                   'select': 'selected', 'open': 'opened', 'click': 'clicked',
                   'navigate': 'completed', 'set': 'applied', 'submit': 'submitted'}
_POL_GENERIC_ACCEPT = 'API accepts the request and responds successfully'


def _norm(s):
    return re.sub(r'[\s.;:,]+$', '', re.sub(r'\s+', ' ', s or '')).strip().lower()


def _ucfirst(s):
    # Leave identifiers alone: 'eSIM', 'whl-activate-subscriber', 'contextId==NO'.
    if not s:
        return s
    first = re.split(r'[\s(]', s, 1)[0]
    if not re.fullmatch(r'[a-z]+', first):
        return s
    return s[0].upper() + s[1:]


def _fix_chars(t, title=False):
    if not t:
        return t
    try:
        from .text_utils import repair_mojibake
        t = repair_mojibake(t)
    except Exception:
        pass
    # UTF-8 bytes read as Latin-1 that kept their C1 control chars ('\u00e2\x80\x94' for an
    # em dash): decode each run back to the real character.
    def _redecode(m):
        try:
            return m.group(0).encode('latin-1').decode('utf-8')
        except Exception:
            return m.group(0)
    t = re.sub('[\u00c2-\u00f4][\u0080-\u00bf]{1,3}', _redecode, t)
    if title:
        t = re.sub('[\u2013\u2014]', '_', t)
    t = _POL_LONE_A_RE.sub('' if title else '\u2014', t)
    t = _POL_STRAY_A_RE.sub('', t)
    if title:
        t = re.sub(r'_{2,}', '_', t)
    return t


def _fix_body(t):
    if not t:
        return t
    t = _fix_chars(t)
    t = _POL_THAT_RE.sub('', t)
    t = _POL_DQ_RE.sub('', t)
    t = _POL_SRC_TAG_RE.sub(' ', t)
    t = _POL_DOUBLE_RE.sub(r'\1', t)
    t = _POL_PLACEHOLDER_RE.sub('a valid test value', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'(?<=[A-Za-z])-\s*$', '.', t.strip())   # 'without disruption-' -> '.'
    return t.strip()


def _trim_long(t, limit):
    if not t or len(t) <= limit:
        return t
    t = _POL_ALT_FLOW_RE.sub('\n', t)          # one alternate flow per line reads better
    if len(t) <= limit:
        return t
    try:
        from .step_templates import truncate_at_word
        out = truncate_at_word(t, limit)
    except Exception:
        out = t[:limit]
    return out.rstrip() + ('' if out.rstrip().endswith(('.', ')')) else '.')


_POL_VERBS = {
    'enter': 'entered', 'search': 'completed', 'inspect': 'reviewed', 'select': 'selected',
    'open': 'opened', 'click': 'clicked', 'navigate': 'completed', 'set': 'applied',
    'submit': 'submitted', 'trigger': 'triggered', 'run': 'completed', 'execute': 'executed',
    'monitor': 'observed', 'observe': 'observed', 'attempt': 'attempted', 'apply': 'applied',
    'wait': 'completed', 'export': 'exported', 'send': 'sent', 'place': 'placed',
    'ingest': 'ingested', 'upload': 'uploaded', 'download': 'downloaded', 'create': 'created',
    'update': 'updated', 'add': 'added', 'remove': 'removed', 'delete': 'deleted',
    'change': 'changed', 'configure': 'configured', 'launch': 'launched', 'log': 'completed',
    'query': 'completed', 'call': 'completed', 'invoke': 'invoked', 'perform': 'performed',
    'repeat': 'repeated', 'retry': 'retried', 'refresh': 'refreshed', 'save': 'saved',
    'cancel': 'cancelled', 'edit': 'edited', 'type': 'entered', 'use': 'used',
    'locate': 'located', 'switch': 'completed', 'return': 'completed', 'review': 'reviewed',
    'reopen': 'reopened', 'go': 'completed', 'scroll': 'completed', 'expand': 'expanded',
    'sort': 'sorted', 'filter': 'filtered', 'clear': 'cleared', 'reset': 'reset',
    'provide': 'provided', 'pass': 'passed', 'choose': 'selected', 'fill': 'filled',
    'access': 'accessed', 'login': 'completed', 'insert': 'inserted', 'modify': 'modified',
}
_POL_VERDICT_RE = re.compile(r'^(.{3,120}?)\s*:\s*(accepted|rejected|allowed|blocked|denied|'
                             r'succeeds?|fails?|returned|displayed|hidden)\s*\.?$', re.I)


def _expected_for_step(step, negative=False):
    """A step-specific expected result derived from the step's own text."""
    s = re.sub(r'\s+', ' ', step or '').strip().rstrip(' .')
    s = re.sub(r'\s*\[(?:GET|POST|PUT|PATCH|DELETE)\s+/[^\]]*\]', '', s).strip()
    if not s:
        return 'Step completes without error.'
    v = _POL_VERDICT_RE.match(s)
    if v:                                   # 'make >50: rejected' -> 'make >50 is rejected.'
        return '%s is %s.' % (_ucfirst(v.group(1).strip()), v.group(2).lower())
    if '->' in s:                           # 'Run 4 -> retry=max, Marked Failed' -> outcome
        left, out = s.split('->', 1)
        out = _POL_CHECK_VERB_RE.sub('', out.strip(' ,;')).strip()
        if len(out) > 3 and re.match(r'[A-Za-z]', out):
            return _ucfirst(out) + '.'
        s = left.strip(' ,;') or s
    if _POL_CHECK_VERB_RE.match(s):
        rest = _POL_CHECK_VERB_RE.sub('', s).strip()
        if len(rest) > 8:
            return _ucfirst(rest) + '.'
    m = re.match(r'^([A-Za-z]+)\s+(.*)$', s)
    if m and m.group(1).lower() in _POL_VERBS and len(m.group(2)) > 3:
        verb, obj = m.group(1).lower(), m.group(2)
        if verb == 'log' and obj.lower().startswith('in'):
            return 'User is logged in successfully.'
        if verb == 'attempt':
            return ('%s is blocked with the documented error.' % _ucfirst(re.sub(r'^to\s+', '', obj))
                    if negative else '%s attempted.' % _ucfirst(re.sub(r'^to\s+', '', obj)))
        if negative:
            return '%s %s.' % (_ucfirst(obj), _POL_VERBS[verb])
        return '%s %s successfully.' % (_ucfirst(obj), _POL_VERBS[verb])
    # Declarative statement ('NBOP compares ... and detects a mismatch'): it IS the outcome.
    if re.match(r'^[A-Za-z]', s) and len(s.split()) >= 3:
        return _ucfirst(s) + '.'
    return 'Step completes without error.'


_POL_ENDPOINT_RE = re.compile(r'\[(GET|POST|PUT|PATCH|DELETE)\s+(/[^\]\s]+)\]')
_POL_NEG_TRIGGER = re.compile(r'200 OK|accepts the request|responds successfully|'
                              r'returns HTTP 200', re.I)


def _generic_expected(step, exp, negative, desc):
    """Replace a constant expected result with one tied to this step / TC."""
    e = (exp or '').strip()
    low = e.lower()
    if negative:
        if _POL_NEG_TRIGGER.search(e):
            return ('Request is received; NSL returns the documented validation error and '
                    'does not process it.')
        if low.startswith('transaction recorded correctly'):
            return ('The rejected request is logged with its error code; no line, plan or '
                    'feature change is recorded.')
        if low.startswith('operation completed. all systems updated'):
            return 'No downstream system is updated for the rejected request.'
        if low.startswith('ne portal shows transaction completed'):
            return 'NE Portal shows no completed transaction for the rejected request.'
        return None
    if e == _POL_GENERIC_ACCEPT:
        m = _POL_ENDPOINT_RE.search(step or '')
        if m:
            api = m.group(2).rstrip('/').rsplit('/', 1)[-1]
            return '%s request is accepted; NSL returns a success response with a transaction ID.' % api
        return 'Request is accepted; NSL returns a success response with a transaction ID.'
    if low.startswith('transaction recorded correctly'):
        return ('TRANSACTION_HISTORY and LINE_HISTORY show the transaction with the correct '
                'type and a completed status.')
    if low.startswith('operation completed. all systems updated'):
        d = re.sub(r'^\s*(?:To validate|UI verification|Validate)\s*:\s*', '', desc or '', flags=re.I)
        d = re.split(r'(?<=[.;])\s', d.strip(), 1)[0].strip().rstrip(' .;')
        if 12 < len(d) < 260:
            return _ucfirst(d) + '.'
        return 'Service Grouping shows every expected outbound call completed successfully.'
    if e == 'Step completes without error.':
        x = _expected_for_step(step)
        return x if x != e else None
    return None


_POL_WEAK_EXP = re.compile(r'^(Test environment configured and ready|Data displayed correctly matches '
                          r'expected values|Request submitted to API endpoint|Portal/screen loads '
                          r'successfully and is ready for input|Step completed successfully|'
                          r'Element is (?:NOT )?visible on the page)\.?$', re.I)
_POL_TITLE_TAIL = {'and', 'or', 'the', 'a', 'an', 'to', 'of', 'with', 'for', 'in', 'on', 'is',
                   'are', 'by', 'from', 'that', 'when', 'if', 'as', 'via', 'at', 'not', 'no',
                   'its', 'only', 'than', 'be', 'but', 'does', 'do', 'which', 'while'}


def _is_imperative(s):
    m = re.match(r'^([A-Za-z]+)\b', s or '')
    return bool(m and m.group(1).lower() in _POL_VERBS)


def _fix_title(n, desc):
    """Name fixes: embedded engine TC number, doubled tokens, connector-cut tail."""
    if not n:
        return n
    n = re.sub(r'^((?:TC\d+_)?[A-Z][A-Z0-9]+-\d+_)TC\d+_', r'\1', n)      # 4526_TC07_ -> 4526_
    n = re.sub(r'(?<=_)([A-Za-z0-9]+)_\1(?=_|$)', r'\1', n)                # SSPP_SSPP -> SSPP
    toks = n.split('_')
    if toks and toks[-1].lower() in _POL_TITLE_TAIL:
        # Cut at the 80-char name limit mid-phrase ('..._does_not'). Finish the phrase from
        # the description (full text), at most 5 words, until a content word.
        dw = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", desc or '')
        low = [w.lower() for w in dw]
        tail = [w.lower() for w in toks[-3:]]
        ext = []
        for i in range(len(low) - len(tail) + 1):
            if low[i:i + len(tail)] == tail:
                for w in dw[i + len(tail):i + len(tail) + 5]:
                    ext.append(w.replace("'", ''))
                    if w.lower() not in _POL_TITLE_TAIL:
                        break
                break
        if ext and ext[-1].lower() not in _POL_TITLE_TAIL:
            toks += ext
        else:
            while len(toks) > 4 and toks[-1].lower() in _POL_TITLE_TAIL:
                toks.pop()
    return '_'.join(toks)


def _fix_short_expected(e):
    x = (e or '').strip().rstrip('.')
    m = re.fullmatch(r'(?:Expect(?:ed)?\s*:?\s*)?(?:HTTP\s*)?([1-5]\d\d)', x, re.I)
    if m:
        return 'NSL returns HTTP %s.' % m.group(1)
    m = re.fullmatch(r'([A-Z]{2,5}\d{2,4})', x)
    if m:
        return 'NSL returns error code %s.' % m.group(1)
    return e


def polish_suite(suite, log: Callable = print) -> dict:
    """Reviewer-polish pass over every TC. Returns counts per rule for the log."""
    c = dict.fromkeys(('chars', 'prefix', 'echo', 'title_exp', 'dup_exp', 'dup_step',
                       'fragment', 'none_pre', 'long', 'neg_accept', 'generic', 'body'), 0)
    for tc in (getattr(suite, 'test_cases', None) or []):
        # title
        n = getattr(tc, 'summary', '') or ''
        nn = _fix_title(_fix_chars(n, title=True), getattr(tc, 'description', '') or '')
        if nn != n:
            tc.summary = nn; c['chars'] += 1
        # description / preconditions
        for attr, lim in (('description', 900), ('preconditions', 900)):
            v = getattr(tc, attr, '') or ''
            nv = _trim_long(_fix_body(v), lim)
            if attr == 'preconditions' and nv and _POL_NONE_PRE_RE.search(nv):
                lines = [ln for ln in nv.split('\n') if not _POL_NONE_PRE_RE.match(ln)]
                nv = '\n'.join(re.sub(r'^\s*\d+\.\s*', '%d. ' % (i + 1), ln)
                               for i, ln in enumerate(lines))
                c['none_pre'] += 1
            if nv != v:
                setattr(tc, attr, nv); c['body'] += 1
        steps = list(getattr(tc, 'steps', None) or [])
        neg = (getattr(tc, 'category', '') or '').lower() == 'negative'
        for st in steps:
            s0, e0 = getattr(st, 'summary', '') or '', getattr(st, 'expected', '') or ''
            s, e = _fix_body(s0), _fix_body(e0)
            if _POL_STEP_PREFIX_RE.match(s):
                s = _POL_STEP_PREFIX_RE.sub('', s); c['prefix'] += 1
            m = _POL_ECHO_RE.match(e)
            if m:
                stmt = m.group(1).strip().rstrip(' .')
                if stmt and _norm(stmt) == _norm(s):
                    # Declarative Chalk line written as both step and '<same> - confirmed'.
                    # Make the step the check and the statement its expected result.
                    _v = _POL_VERDICT_RE.match(s.rstrip(' .'))
                    if _v:                     # "make 'X' (17 chars): accepted"
                        s = 'Verify %s is %s.' % (_v.group(1).strip(), _v.group(2).lower())
                        stmt = '%s is %s' % (_ucfirst(_v.group(1).strip()), _v.group(2).lower())
                    elif _POL_CHECK_VERB_RE.match(s):
                        pass
                    elif re.search(r'[=<>]|->', s) or not re.match(r'^[A-Za-z]', s):
                        s = 'Verify: ' + s.rstrip(' .') + '.'          # technical assertion
                    elif re.match(r'^[A-Z][a-z]', s):
                        s = 'Verify that ' + s[0].lower() + s[1:].rstrip(' .') + '.'
                    else:                                           # 'NSL ...', 'make ...'
                        s = 'Verify that ' + s.rstrip(' .') + '.'
                if stmt:
                    e = _ucfirst(stmt) + '.'
                    c['echo'] += 1
            if _POL_VERIFY_EXP_RE.match(e) and len(e) > 20:
                e = _ucfirst(_POL_VERIFY_EXP_RE.sub('', e)); c['title_exp'] += 1
            if s and (_norm(e) == _norm(s) or _POL_WEAK_EXP.match(e.strip())):
                if _POL_CHECK_VERB_RE.match(s):
                    x = _expected_for_step(s, neg)
                    if _norm(x) != _norm(s) and not _POL_WEAK_EXP.match(x):
                        e = x; c['generic'] += 1
                elif _is_imperative(s):
                    x = _expected_for_step(s, neg)
                    if _norm(x) != _norm(s) and not _POL_WEAK_EXP.match(x):
                        e = x; c['generic'] += 1
                elif re.match(r'^[A-Za-z]', s) and len(s.split()) >= 3:
                    # Declarative outcome written as the step ('NSL rejects ... ERR333'):
                    # it is the expected result; the step becomes the check.
                    e = _ucfirst(s.rstrip(' .')) + '.'
                    if not _POL_CHECK_VERB_RE.match(s):
                        _first = s.split(' ', 1)[0]
                        _s = s if re.fullmatch(r'[A-Z][a-z]+', _first) is None else s[0].lower() + s[1:]
                        s = 'Verify that ' + _s.rstrip(' .') + '.'
                    c['generic'] += 1
            e = _fix_short_expected(e)
            g = _generic_expected(s, e, neg, getattr(tc, 'description', '') or '')
            if g and g != e:
                e = g
                c['neg_accept' if neg else 'generic'] += 1
            s2, e2 = _trim_long(s, 500), _trim_long(e, 600)
            if (s2, e2) != (s, e):
                c['long'] += 1
            s, e = s2, e2
            if s != s0:
                st.summary = s
            if e != e0:
                st.expected = e
            if (s, e) != (s0, e0):
                c['body'] += 1
        # drop exact repeats of an earlier step, and one-word fragment steps
        keep, seen = [], set()
        for st in steps:
            k = _norm(getattr(st, 'summary', ''))
            summ = (getattr(st, 'summary', '') or '').strip()
            if k and k in seen and len(steps) > 3:
                c['dup_step'] += 1; continue
            if summ and len(summ.split()) == 1 and not re.search(r'[\d=]', summ) and len(steps) > 2:
                c['fragment'] += 1; continue
            if summ and len(summ) < 15 and re.search(r'[\d=]', summ):
                st.summary = 'Use test value: %s' % summ.rstrip(' .')
                c['fragment'] += 1
            seen.add(k)
            keep.append(st)
        # same expected on several steps: keep it on the last, derive the others
        last = {}
        for i, st in enumerate(keep):
            e = getattr(st, 'expected', '') or ''
            if len(e) > 30:
                last[_norm(e)] = i
        used = {_norm(getattr(st, 'expected', '') or '') for st in keep}
        for i, st in enumerate(keep):
            e = getattr(st, 'expected', '') or ''
            if len(e) > 30 and last.get(_norm(e)) != i:
                summ = getattr(st, 'summary', '') or ''
                x = _expected_for_step(summ, neg)
                if _norm(x) in used and '->' in summ:      # same outcome as a sibling
                    x = _expected_for_step(summ.split('->', 1)[0], neg)
                if _norm(x) == _norm(summ) and not _is_imperative(summ) \
                        and re.match(r'^[A-Za-z]', summ) and not _POL_CHECK_VERB_RE.match(summ):
                    _f = summ.split(' ', 1)[0]
                    _s = summ if re.fullmatch(r'[A-Z][a-z]+', _f) is None else summ[0].lower() + summ[1:]
                    st.summary = 'Verify that ' + _s.rstrip(' .') + '.'
                st.expected = _fix_short_expected(x)
                used.add(_norm(x))
                c['dup_exp'] += 1
        for st in keep:
            summ = getattr(st, 'summary', '') or ''
            e = getattr(st, 'expected', '') or ''
            if summ and _norm(e) == _norm(summ):
                if _POL_CHECK_VERB_RE.match(summ):
                    x = _expected_for_step(summ, neg)
                    if _norm(x) != _norm(summ):
                        st.expected = x; c['generic'] += 1
                elif _is_imperative(summ):
                    x = _expected_for_step(summ, neg)
                    if _norm(x) != _norm(summ):
                        st.expected = x; c['generic'] += 1
                elif re.match(r'^[A-Za-z]', summ) and not _POL_CHECK_VERB_RE.match(summ):
                    _f = summ.split(' ', 1)[0]
                    _s = summ if re.fullmatch(r'[A-Z][a-z]+', _f) is None else summ[0].lower() + summ[1:]
                    st.summary = 'Verify that ' + _s.rstrip(' .') + '.'
                    st.expected = _ucfirst(summ.rstrip(' .')) + '.'
                    c['generic'] += 1
            st.expected = _fix_short_expected(getattr(st, 'expected', '') or '')
        if len(keep) != len(steps):
            for i, st in enumerate(keep, 1):
                st.step_num = i
            tc.steps = keep
    if log and any(c.values()):
        log('[POLISH] ' + ', '.join('%s=%d' % kv for kv in c.items() if kv[1]))
    return c
