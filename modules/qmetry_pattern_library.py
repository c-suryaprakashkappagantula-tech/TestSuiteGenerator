"""
qmetry_pattern_library.py — Human-authored QA pattern library (V1).

Mined from 5 real QMetry manual provisioning suites (3,138 TCs / ~13k steps).
It captures the human "house style" that the generator does not yet reproduce,
and exposes it as clean, deterministic helpers so the engine can emit steps and
preconditions that look hand-written rather than templated.

What it provides
----------------
1. Universal provisioning CLOSERS — the 3-step tail humans append to almost
   every provisioning TC:
     a. Century DB transaction-log trace (root txn id)     — seen 718x
     b. NBOP_MIG_* DB-table validation                      — seen 2,980x
     c. Genesis-portal / NBOP line-summary UI verify        — seen 543x
2. Trigger -> Century-report PAIRING — after each "Trigger <API>" step, humans
   add "Validate the century report for <API>"              — seen 594x
3. Grounded PRECONDITIONS — a library of real preconditions keyed by operation,
   to REPLACE the humanizer's generic filler ("System in ready state / Test
   data prepared").
4. Real NEGATIVE-response vocabulary (Bad Request, Invalid MSISDN, errorCode /
   errorDetails / responseCode ...).
5. A QUALITY check: flag TCs whose expected-result is copy-pasted across >=3
   steps (a defect observed in generated output).

The mined data lives in qmetry_pattern_library.json beside this file. This
module is pure-Python, has no third-party dependencies, and is deterministic
(no randomness) so repeated runs are byte-identical.

Integration points (suggested)
------------------------------
* step_templates.py    -> call provisioning_closer() / interleave_century_reports()
                          at the end of a provisioning step chain.
* humanizer.py         -> replace filler-precondition injection with
                          grounded_precondition(); use is_filler_precondition()
                          to detect what to overwrite.
* data_first_engine.py -> call enrich_suite(suite, ...) as a post-build pass.
* grounding gate       -> use duplicate_expected_violation() as an extra rule.
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_JSON_PATH = Path(__file__).with_name('qmetry_pattern_library.json')

# ── Cached library load ───────────────────────────────────────────────────────
_LIB_CACHE: Optional[Dict] = None


def _lib() -> Dict:
    """Load and cache the mined pattern JSON. Returns {} if unavailable."""
    global _LIB_CACHE
    if _LIB_CACHE is None:
        try:
            _LIB_CACHE = json.loads(_JSON_PATH.read_text(encoding='utf-8'))
        except Exception:
            _LIB_CACHE = {}
    return _LIB_CACHE


# ── Canonical NBOP_MIG DB tables (frequency-ranked from the corpus) ───────────
# Fallback list is used if the JSON is missing; kept in sync with the mine.
_FALLBACK_TABLES = [
    'NBOP_MIG_LINE', 'NBOP_MIG_LINE_HISTORY', 'NBOP_MIG_TRANSACTION_HISTORY',
    'NBOP_MIG_FEATURE', 'NBOP_MIG_SIM', 'NBOP_MIG_DEVICE',
    'NBOP_MIG_LINE_PLAN', 'NBOP_MIG_ACCOUNT',
]

# Which tables matter for which operation family. Humans don't list all 8 every
# time — they pick the ones the operation actually touches.
_TABLES_BY_OP = {
    'sim':      ['NBOP_MIG_SIM', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY', 'NBOP_MIG_LINE_HISTORY'],
    'device':   ['NBOP_MIG_DEVICE', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY', 'NBOP_MIG_LINE_HISTORY'],
    'feature':  ['NBOP_MIG_FEATURE', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'plan':     ['NBOP_MIG_LINE_PLAN', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'rate plan': ['NBOP_MIG_LINE_PLAN', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'line status': ['NBOP_MIG_LINE', 'NBOP_MIG_LINE_HISTORY', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'mdn':      ['NBOP_MIG_LINE', 'NBOP_MIG_LINE_HISTORY', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'account':  ['NBOP_MIG_ACCOUNT', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'sync':     ['NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY', 'NBOP_MIG_LINE_PLAN'],
    'wearable': ['NBOP_MIG_DEVICE', 'NBOP_MIG_SIM', 'NBOP_MIG_LINE', 'NBOP_MIG_TRANSACTION_HISTORY'],
    'activate': ['NBOP_MIG_LINE', 'NBOP_MIG_SIM', 'NBOP_MIG_DEVICE', 'NBOP_MIG_TRANSACTION_HISTORY'],
}

# Keywords indicating a feature actually provisions on NSL/NBOP (so a DB-table
# closer is appropriate). Notification / CDR / pure-report features are excluded.
_PROVISIONING_HINTS = (
    'nbop', 'nsl', 'subscriber', 'activation', 'activate', 'line', 'sim', 'device',
    'feature', 'plan', 'mdn', 'swap', 'sync', 'wearable', 'port', 'reset', 'hotline',
    'suspend', 'restore', 'reclaim', 'transfer', 'provision',
)
_NON_PROVISIONING_HINTS = (
    'kafka', 'notification', 'cdr', 'mediation', 'ild', 'prr file', 'batch report',
    'differential report', 'email', 'sms alert',
)


# Known operation families, longest first so 'change rate plan' beats 'plan'.
_KNOWN_OPS = [
    'change rate plan', 'change device and sim', 'change line status', 'line status',
    'rate plan', 'change features', 'change sim', 'change mdn', 'swap mdn',
    'reclaim mdn', 'sync subscriber', 'sync line', 'add wearable', 'port out',
    'port in', 'network reset', 'reset plan', 'line inquiry', 'activation',
    'activate', 'suspend', 'restore', 'hotline', 'feature', 'wearable', 'device',
    'account', 'sim', 'mdn', 'plan', 'sync',
]


def operation_label(text: str) -> str:
    """Extract a short, clean operation name from a TC summary / feature context.

    Returns e.g. 'change sim', 'sync subscriber', or '' when nothing matches
    (callers render '' as a generic 'the transaction').
    """
    t = (text or '').lower()
    for op in _KNOWN_OPS:
        if op in t:
            return op
    return ''


def canonical_db_tables(operation: str = '') -> List[str]:
    """Return the NBOP_MIG_* tables a given operation touches.

    With no operation, returns the full frequency-ranked canonical set.
    """
    op = (operation or '').lower()
    for key, tables in _TABLES_BY_OP.items():
        if key in op:
            return list(tables)
    lib = _lib()
    canon = [t['table'] for t in lib.get('db_tables', {}).get('canonical', [])]
    return canon or list(_FALLBACK_TABLES)


# ── Closer steps (return (summary, expected) tuples) ──────────────────────────

def century_trace_step() -> Tuple[str, str]:
    """The 'search century DB by root txn id' step (verbatim human phrasing)."""
    return (
        'User searched the transaction logs in century DB using the root txn id.',
        'User is able to search successfully the transaction logs in century DB.',
    )


def db_table_validation_step(operation: str = '') -> Tuple[str, str]:
    """NBOP_MIG_* DB-table validation step for the given operation family."""
    tables = canonical_db_tables(operation)
    table_block = '\n'.join(tables)
    label = (operation.strip() or 'the').strip()
    return (
        'Verify the transaction update in the following NSL DB tables happen '
        'successfully:\n' + table_block,
        'NSL should update the below tables successfully for %s transaction:\n%s'
        % (label, table_block),
    )


def genesis_verify_step() -> Tuple[str, str]:
    """NBOP line-summary + TMO Genesis portal UI verification closer."""
    return (
        'Verify line details on NBOP Line Summary and TMO Genesis Portal.',
        'Line details are consistent on NBOP Line Summary and TMO Genesis Portal.',
    )


def provisioning_closer(feature_context: str = '',
                        operation: str = '',
                        include_genesis: bool = True) -> List[Tuple[str, str]]:
    """Return the standard human closer tail as (summary, expected) tuples.

    Order matches the corpus: century-DB trace -> DB-table validation ->
    Genesis/UI verify. Returns [] when the feature is clearly non-provisioning
    (notification/CDR/report), so it is safe to call unconditionally.
    """
    ctx = (feature_context or '') + ' ' + (operation or '')
    if not is_provisioning_feature(ctx):
        return []
    op = operation or feature_context
    steps = [century_trace_step(), db_table_validation_step(op)]
    if include_genesis:
        steps.append(genesis_verify_step())
    return steps


def is_provisioning_feature(feature_context: str) -> bool:
    """Heuristic: does this feature provision on NSL/NBOP (closer applies)?"""
    t = (feature_context or '').lower()
    if any(h in t for h in _NON_PROVISIONING_HINTS):
        return False
    return any(h in t for h in _PROVISIONING_HINTS)


# ── Trigger -> Century-report pairing ─────────────────────────────────────────

_TRIGGER_RE = re.compile(
    r'\btrigger\b.*?["\']?([A-Za-z][A-Za-z0-9 \-]{3,45}?)["\']?\s*(?:api|call)\b',
    re.I)


def century_pairing_step(api_name: str) -> Tuple[str, str]:
    """'Validate the century report for <API>' — the paired validation step."""
    api = (api_name or '').strip().strip('"\'') or 'the'
    return (
        'Validate the century report for "%s" api.' % api,
        'The "%s" api call is reflected correctly in the century report.' % api,
    )


def interleave_century_reports(
        steps: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Insert a Century-report validation after each 'Trigger <API>' step.

    Idempotent: if a trigger step is already followed by a century-report step,
    no duplicate is added. Input/return are (summary, expected) tuple lists.
    """
    out: List[Tuple[str, str]] = []
    for i, (summ, exp) in enumerate(steps):
        out.append((summ, exp))
        m = _TRIGGER_RE.search(summ or '')
        if not m:
            continue
        nxt = steps[i + 1][0].lower() if i + 1 < len(steps) else ''
        if 'century report' in nxt:
            continue  # already paired
        out.append(century_pairing_step(m.group(1)))
    return out


# ── Grounded preconditions (replace filler) ───────────────────────────────────

# The exact filler the humanizer currently injects — what we want to overwrite.
_FILLER_PRECON_RE = re.compile(
    r'system\s+in\s+ready\s+state|test\s+data\s+prepared|'
    r'preconditions?\s+met|environment\s+is\s+ready|as\s+needed',
    re.I)

_FALLBACK_PRECON = [
    'User must have an active TMO line.',
    'Agent should have MNO_TMO permission.',
    'User should have NBOP admin access.',
]


def is_filler_precondition(text: str) -> bool:
    """True if the precondition block is generic filler (or empty/too short)."""
    t = (text or '').strip()
    if len(t) < 12:
        return True
    return bool(_FILLER_PRECON_RE.search(t))


def grounded_precondition(context_text: str = '', n: int = 2) -> str:
    """Return up to *n* real, numbered preconditions relevant to the context.

    Strategy: lead with ONE operation-specific precondition (from the mined
    keyword buckets, if the context matches), then fill from the curated
    `safe_defaults` — high-frequency preconditions that apply to almost any
    provisioning TC ("User must have an active TMO line.", etc.). This keeps the
    result on-topic without the noise of arbitrary per-keyword lines.
    Deterministic (frequency order), never random.
    """
    lib = _lib()
    pre = lib.get('preconditions', {})
    ctx = (context_text or '').lower()
    picks: List[str] = []

    by_kw = pre.get('by_keyword', {})
    # Longer keys first so 'line status' wins over 'line'. Take just one strong,
    # operation-specific precondition to lead with.
    for kw in sorted(by_kw, key=len, reverse=True):
        if kw in ctx and by_kw[kw]:
            picks.append(by_kw[kw][0])
            break

    # Fill from curated safe defaults (clean, broadly-applicable).
    for p in pre.get('safe_defaults', []):
        if p not in picks:
            picks.append(p)
        if len(picks) >= n:
            break

    # Last-resort fallbacks if the JSON is unavailable.
    if len(picks) < n:
        for p in _FALLBACK_PRECON:
            if p not in picks:
                picks.append(p)
            if len(picks) >= n:
                break

    picks = picks[:max(1, n)]
    return '\n'.join('%d. %s' % (i, p) for i, p in enumerate(picks, 1))


# ── Negative-response vocabulary ──────────────────────────────────────────────

def negative_vocab() -> Dict:
    """Return {error_fields, error_messages, response_codes} from the corpus."""
    return dict(_lib().get('negative_vocab', {}))


def negative_expected(entity: str = 'request') -> str:
    """A grounded negative expected-result using real error-field vocabulary."""
    v = negative_vocab()
    fields = v.get('error_fields') or ['responseCode', 'errorCode', 'errorDetails', 'reason']
    msg = (v.get('error_messages') or ['Bad Request'])[0]
    shown = [f for f in fields if f in ('responseCode', 'errorCode', 'errorDetails', 'reason')] or fields[:3]
    return ('The %s is rejected. Response returns "%s" with populated %s.'
            % (entity, msg, ', '.join('"%s"' % f for f in shown[:3])))


# ── Quality check: duplicate expected-result across steps ─────────────────────

def duplicate_expected_violation(steps, min_repeat: int = 3) -> Optional[str]:
    """Flag when the same non-empty expected result repeats across >= min_repeat
    steps (a copy-paste defect). Returns a message, or None if clean.

    *steps* may be (summary, expected) tuples or objects with an `.expected`
    attribute — both are handled.
    """
    counts: Dict[str, int] = {}
    for st in (steps or []):
        exp = st[1] if isinstance(st, (tuple, list)) else getattr(st, 'expected', '')
        exp = re.sub(r'\s+', ' ', (exp or '')).strip().lower()
        if len(exp) < 8:
            continue
        counts[exp] = counts.get(exp, 0) + 1
    worst = max(counts.values()) if counts else 0
    if worst >= min_repeat:
        return ('Expected-result copy-pasted across %d steps '
                '(each step should assert its own outcome).' % worst)
    return None


# ── High-level enrichment of a TestCase / suite ───────────────────────────────

def _make_step(step_num: int, summary: str, expected: str):
    """Build a TestStep if the V8 model is importable, else a light stand-in."""
    try:
        from .data_models_v8 import TestStep
        return TestStep(step_num=step_num, summary=summary, expected=expected)
    except Exception:
        class _Step:  # pragma: no cover - fallback only
            def __init__(self, step_num, summary, expected):
                self.step_num, self.summary, self.expected, self.data_reference = \
                    step_num, summary, expected, ''
        return _Step(step_num, summary, expected)


def _tc_has_db_closer(tc) -> bool:
    for st in getattr(tc, 'steps', []):
        s = getattr(st, 'summary', '') or ''
        if 'NBOP_MIG_' in s or ('nsl' in s.lower() and 'table' in s.lower()):
            return True
    return False


def enrich_test_case(tc, feature_context: str = '',
                     add_closers: bool = True,
                     fix_preconditions: bool = True,
                     pair_century: bool = False,
                     log=None) -> object:
    """Enrich one TestCase object in place with human patterns. Returns it.

    - add_closers: append century-trace + DB-table + Genesis verify (only for
      provisioning features, and only if not already present).
    - fix_preconditions: overwrite filler/empty preconditions with grounded ones.
    - pair_century: insert Century-report validation after Trigger <API> steps.

    Duck-typed: works on the V8 TestCase (steps=List[TestStep], preconditions:str)
    without importing it, so there is no circular-import risk.
    """
    changed = []
    summary = getattr(tc, 'summary', '') or ''
    category = (getattr(tc, 'category', '') or '').lower()
    ctx = (feature_context + ' ' + summary).strip()

    # 1. Preconditions
    if fix_preconditions:
        pre = getattr(tc, 'preconditions', '') or ''
        if is_filler_precondition(pre):
            tc.preconditions = grounded_precondition(ctx, n=2)
            changed.append('preconditions')

    # 2. Century pairing (rebuild the step list with paired validations)
    steps = list(getattr(tc, 'steps', []) or [])
    if pair_century and steps:
        as_tuples = [(getattr(s, 'summary', ''), getattr(s, 'expected', '')) for s in steps]
        paired = interleave_century_reports(as_tuples)
        if len(paired) != len(as_tuples):
            steps = [_make_step(i, s, e) for i, (s, e) in enumerate(paired, 1)]
            changed.append('century-pairing')

    # 3. Closers — skip negatives and non-provisioning features
    if (add_closers and 'negative' not in category
            and is_provisioning_feature(ctx) and not _tc_has_db_closer(tc)):
        start = len(steps)
        op = operation_label(ctx)
        for j, (s, e) in enumerate(provisioning_closer(feature_context, op), 1):
            steps.append(_make_step(start + j, s, e))
        if len(steps) > start:
            changed.append('closers')

    # Renumber and write back
    for idx, st in enumerate(steps, 1):
        if hasattr(st, 'step_num'):
            st.step_num = idx
    tc.steps = steps

    if log and changed:
        try:
            log('[PATTERN-LIB] %s: +%s' % (summary[:48], ', '.join(changed)))
        except Exception:
            pass
    return tc


def enrich_suite(suite, feature_context: str = '',
                 add_closers: bool = True,
                 fix_preconditions: bool = True,
                 pair_century: bool = False,
                 log=print) -> object:
    """Apply enrich_test_case() to every TC in a suite. Returns the suite.

    Safe to call as a post-build pass in data_first_engine after the grounding
    gate. Never raises — a bad TC is skipped, not fatal.
    """
    fc = feature_context or getattr(suite, 'feature_title', '') or getattr(suite, 'feature_id', '')
    n_closers = n_pre = 0
    for tc in getattr(suite, 'test_cases', []) or []:
        try:
            before_steps = len(getattr(tc, 'steps', []) or [])
            before_pre = getattr(tc, 'preconditions', '')
            enrich_test_case(tc, feature_context=fc, add_closers=add_closers,
                             fix_preconditions=fix_preconditions,
                             pair_century=pair_century, log=None)
            if len(getattr(tc, 'steps', []) or []) > before_steps:
                n_closers += 1
            if getattr(tc, 'preconditions', '') != before_pre:
                n_pre += 1
        except Exception as exc:  # never fail the build over enrichment
            if log:
                try:
                    log('[PATTERN-LIB] skip %s: %s'
                        % (getattr(tc, 'summary', '')[:40], str(exc)[:60]))
                except Exception:
                    pass
    if log:
        try:
            log('[PATTERN-LIB] enriched suite: %d TCs got closers/steps, '
                '%d preconditions grounded' % (n_closers, n_pre))
        except Exception:
            pass
    return suite


# ── Introspection ─────────────────────────────────────────────────────────────

def library_summary() -> Dict:
    """Return a compact summary of what the mined library contains."""
    lib = _lib()
    return {
        'available': bool(lib),
        'meta': lib.get('meta', {}),
        'canonical_tables': [t['table'] for t in lib.get('db_tables', {}).get('canonical', [])],
        'precondition_keywords': sorted(lib.get('preconditions', {}).get('by_keyword', {})),
        'api_names': lib.get('api_names', []),
        'negative_messages': lib.get('negative_vocab', {}).get('error_messages', []),
    }
