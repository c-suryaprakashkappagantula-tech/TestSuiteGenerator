# -*- coding: utf-8 -*-
"""client_text.py — shared, deterministic client-facing text helpers.

These helpers turn a raw TestCase into the short, plain-English label and the
single-clause "Verify that ..." one-liner used by the client-facing artifacts
(the Excel "High Level Scenarios" sheet and the Word "Test Case Presenter
Guide"). Keeping them in one place guarantees the two artifacts agree and never
drift. No LLM; purely rule-based.
"""
from __future__ import annotations


_HLS_BANNED = (
    'service grouping', 'service_grouping', 'century report', 'nbop_mig',
    'transaction_history', 'line_history', 'ne portal', 'trigger api',
    'nsl processes request',
)


def _hls_scenario_name(tc, idx):
    """Derive a short, human scenario label from a TC summary.

    Strips the 'TCnn_<FEATURE-ID>_Verify_' prefix, turns underscores into spaces,
    drops a leading 'Verify'/'Validate', and Title-Cases the result. Falls back to
    a generic label when nothing usable remains.
    """
    import re as _re
    s = tc.summary or ''
    s = _re.sub(r'^\s*🆕\s*', '', s)
    s = _re.sub(r'^TC\d+[_\s]*', '', s)                      # drop TCnn_ prefix
    s = _re.sub(r'^[A-Z][A-Z0-9]+-\d+[_\s]*', '', s)         # drop FEATURE-ID prefix
    s = s.replace('_', ' ').strip()
    s = _re.sub(r'^(verify|validate|ensure|check)\s+(that\s+)?', '', s, flags=_re.IGNORECASE).strip()
    s = _re.sub(r'\s+', ' ', s)
    if not s:
        return 'Scenario %02d' % idx
    # Title-case but keep ALL-CAPS tokens (field names / acronyms) intact.
    words = []
    for w in s.split(' '):
        words.append(w if (w.isupper() and len(w) > 1) else (w[:1].upper() + w[1:]))
    return (' '.join(words))[:80]


def _hls_description(tc, name=''):
    """One-line plain-English 'Verify that ...' summary for a TC.

    Prefers the TC's own scenario/description; otherwise builds from the first
    grounded step; otherwise falls back to the cleaned scenario `name`. Kept
    non-technical, single clause, no raw TC-id / flow-matrix dumps.
    """
    import re as _re
    src = (getattr(tc, 'description', '') or '').strip()
    # The description often reads "To validate: <intent>" — lift the intent.
    m = _re.match(r'(?is)^\s*to\s+validate[:\-]\s*(.+)$', src)
    if m:
        src = m.group(1).strip()
    src = _re.sub(r'\s+', ' ', src).strip()
    # Multi-flow Chalk descriptions dump "Alternate Flow A: ... Alternate Flow B: ..."
    # (or "Scenario:/Flow:/Case:") — cut at the first such marker so the high-level
    # line stays a single readable clause, not the full internal flow matrix.
    src = _re.split(r'(?i)\b(?:alternate\s+flow|flow|scenario|case|step)\s+[A-Z0-9]\s*[:\-]', src, maxsplit=1)[0].strip()
    # Collapse to one sentence.
    src = _re.split(r'(?<=[a-z0-9])\.\s', src)[0].strip().rstrip('.;,-> ')
    # If the description was ONLY a flow-matrix (nothing usable before the first
    # marker) or empty, fall back to the first grounded step, then the clean name.
    if not src and getattr(tc, 'steps', None):
        src = _re.sub(r'\s+', ' ', (getattr(tc.steps[0], 'summary', '') or '')).strip().rstrip('.')
    if not src:
        src = (name or '').strip()
    if len(src) > 160:
        src = src[:160].rsplit(' ', 1)[0].strip()
    if not src:
        return 'Verify the scenario behaves as expected'
    if not _re.match(r'(?i)^(verify|validate|ensure|confirm|check)\b', src):
        src = 'Verify that ' + src[:1].lower() + src[1:]
    return src[:200]
