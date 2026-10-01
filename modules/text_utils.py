"""text_utils.py — tiny, generic text-coercion helpers shared across the pipeline.

Why: the MCP gateway returns long Jira text fields as a dict
    {'text': str, 'truncated': bool, 'full_length': int, 'hint': str}
instead of a plain str. Downstream regex code (deep_miner, demo_doc_generator)
crashes on a dict. Everything here is shape-only: no feature-specific logic.
"""
import re
import unicodedata

_MOJIBAKE_MARKERS = ('\u00e2', '\u00c3', '\u00c2')  # â Ã Â — lead bytes of mis-decoded UTF-8


def as_text(v):
    """Coerce any Jira field value to str.

    str -> str; dict with 'text' -> its text; other dict -> join of str values;
    list/tuple -> newline-join of as_text(item); None -> ''; anything else -> str(v).
    """
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if 'text' in v:
            return as_text(v.get('text'))
        return ' '.join(x for x in (as_text(x) for x in v.values()
                                    if isinstance(x, (str, dict, list, tuple))) if x)
    if isinstance(v, (list, tuple)):
        return '\n'.join(x for x in (as_text(i) for i in v) if x)
    return str(v)


def repair_mojibake(s):
    """Undo UTF-8-decoded-as-latin1/cp1252 mojibake (e.g. 'â\\x80\\x94' -> '—').

    Only applied when the text carries the tell-tale lead chars AND the repair
    round-trips cleanly; otherwise the input is returned unchanged.
    """
    if not isinstance(s, str) or not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    for _ in range(2):  # handles double-encoded text too
        fixed = None
        for enc in ('latin-1', 'cp1252'):
            try:
                cand = s.encode(enc).decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if cand != s:
                fixed = cand
                break
        if fixed is None:
            break
        s = fixed
        if not any(m in s for m in _MOJIBAKE_MARKERS):
            break
    return s


def ascii_slug_text(s):
    """Filename-safe ASCII form: repair mojibake, dashes -> '-', NFKD fold, drop non-ASCII."""
    s = repair_mojibake(as_text(s))
    s = re.sub('[\u2010-\u2015\u2212]', '-', s)       # hyphen/en/em dashes, minus
    s = s.replace('\u2018', "'").replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return s


_ISSUE_STR_FIELDS = ('summary', 'description', 'acceptance_criteria', 'status', 'priority',
                     'assignee', 'reporter', 'pi', 'channel', 'issue_type')
_CHILD_STR_FIELDS = ('summary', 'description', 'body', 'status', 'issue_type', 'acceptance_criteria')


def _is_truncated(v):
    return isinstance(v, dict) and bool(v.get('truncated'))


def normalize_jira_issue(jira, log=None):
    """Coerce JiraIssue text fields (and child dicts) to str in place. Returns jira.

    Logs once if any field was truncated by the MCP gateway. Repairs mojibake in
    the summary only when the repair round-trips cleanly.
    """
    if jira is None:
        return jira
    truncated = []

    for f in _ISSUE_STR_FIELDS:
        if hasattr(jira, f):
            v = getattr(jira, f)
            if _is_truncated(v):
                truncated.append(v)
            if not isinstance(v, str):
                setattr(jira, f, as_text(v))

    for attr in ('subtasks', 'linked_issues', 'comments'):
        items = getattr(jira, attr, None)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for f in _CHILD_STR_FIELDS:
                if f in item and not isinstance(item[f], str):
                    if _is_truncated(item[f]):
                        truncated.append(item[f])
                    item[f] = as_text(item[f])

    if getattr(jira, 'summary', None):
        jira.summary = repair_mojibake(jira.summary)

    if truncated and log:
        hint = as_text(truncated[0].get('hint', ''))
        log('[JIRA] NOTE: %d field(s) truncated by MCP gateway (hint: %s)' % (len(truncated), hint[:80]))
    return jira
