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
    label = (operation or '').strip()
    # A caller may pass the whole feature title as the 'operation' (provisioning_closer
    # falls back to feature_context). Rendered verbatim that gave 'for [NBOP, NSL, INTG]:
    # NBOP History ... MWTGPROV-4526 transaction:'. Reduce anything that is not already a
    # short operation name to its known operation family, else the generic wording.
    if len(label) > 30 or re.search(r'[\[\]:]|MWTGPROV-\d', label):
        label = operation_label(label) or ''
    label = label or 'the'
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
    """Heuristic: does this feature provision on NSL/NBOP (closer applies)?

    File-ingestion features are checked FIRST and always lose: a PRR/batch feature
    carries NSL/subscriber/line words in its title, which would otherwise match a
    provisioning hint and bolt Century-DB / NBOP_MIG / Genesis closers onto a test
    that never touches a provisioning API.
    """
    t = (feature_context or '').lower()
    if is_file_ingestion_feature(t):
        return False
    if any(h in t for h in _NON_PROVISIONING_HINTS):
        return False
    return any(h in t for h in _PROVISIONING_HINTS)


def is_file_ingestion_feature(feature_context: str) -> bool:
    """True when the feature ingests batch/PRR files (not an API feature)."""
    t = (feature_context or '').lower()
    return any(h in t for h in _FILE_INGESTION_HINTS)


# ── Mediation / PRR / billing signal detector ─────────────────────────────────
# Any-of, case-insensitive, over the concatenated inputs (title, summary, scope,
# components, AC/Chalk text). Require a REAL mediation/PRR/billing signal — an
# NSLNM-only feature with no mediation signal must NOT match (it stays api/NSL).
_MEDIATION_SIGNALS = (
    'mediation', 'prr', 'amdocs', 'billing record', 'call_direction_ind',
    'mms_type_indicator', 'tmo_prr_processing', 'biller sftp',
    # component tokens for a Mediation/WPAMED component prefix like [WPAMED, ...]
    'wpamed', '[med', ', med', 'med,',
)


def is_mediation_feature(*texts: str) -> bool:
    """True when ANY input carries a real Mediation/PRR/billing signal.

    Case-insensitive, any-of over the concatenated inputs. Accepts several strings
    (feature title, Jira summary, scope, components, AC/Chalk text). Pure — no I/O.

    Deliberately requires a concrete mediation/PRR/billing signal: a bare 'NSLNM'
    component (pure NSL provisioning, no mediation) does NOT match, so such features
    continue to classify as api and keep their NSL/NE-Portal/Service-Grouping steps.
    """
    blob = ' '.join(t for t in texts if t).lower()
    if not blob:
        return False
    return any(sig in blob for sig in _MEDIATION_SIGNALS)


def file_ingestion_precondition(context_text: str = '') -> str:
    """Return preconditions appropriate for PRR/batch file-ingestion TCs."""
    picks = list(_FILE_INGESTION_PRECON)
    return '\n'.join('%d. %s' % (i, p) for i, p in enumerate(picks, 1))


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
    r'preconditions?\s+met|environment\s+is\s+ready|as\s+needed|'
    # Generic API 3-liner template emitted by tc_builder._build_scenario_tc
    r'active\s+tmo\s+(?:subscriber\s+line|mdn)\s+(?:available\s+)?in\s+sit(?:\s+environment)?|'
    r'api\s+endpoint\s+accessible|'
    r'line\s+status\s*:\s*active',
    re.I)

_FALLBACK_PRECON = [
    'User must have an active TMO line.',
    'Agent should have MNO_TMO permission.',
    'User should have NBOP admin access.',
]

# Preconditions for batch/PRR file-ingestion features (not API-based).
_FILE_INGESTION_PRECON = [
    'SIT environment is ready and SFTP server is accessible.',
    'Valid PRR/batch file is prepared with test subscriber data (correct format, field lengths).',
    'Subscriber line exists in NSL DB with matching MDN for file processing.',
]

_FILE_INGESTION_HINTS = ('prr', 'batch file', 'sftp', 'file ingest', 'differential report',
                          'batch report', 'usage file', 'data volume')


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


def _tc_is_qmetry_authored(tc) -> bool:
    """True when this TC's steps come VERBATIM from QMetry-authored test cases.

    The engine (tc_builder) tags every step it builds from a scenario's authored
    ``steps_hint`` with ``authored=True`` / ``origin='authored'``. When ANY step
    carries that provenance, the TC's step list IS the QMetry ground truth — the
    universal provisioning CLOSER (Century-DB trace → NBOP_MIG_* DB-table validation
    → Genesis verify) must NOT be bolted on, because those closers are mined house-
    style boilerplate, not authored content, and injecting them fabricates DB-table
    verification steps (NBOP_MIG_*/*_HISTORY) that are absent from the authored suite.
    Only grounded (generated-from-Chalk) provisioning TCs keep their closers.
    """
    for st in (getattr(tc, 'steps', []) or []):
        if getattr(st, 'authored', False) or (getattr(st, 'origin', '') == 'authored'):
            return True
    return False


def _tc_is_mediation(tc, feature_context: str = '') -> bool:
    """True when THIS TC is a Mediation/PRR/billing test (per its own step content).

    A mediation TC validates the Amdocs billing-output / PRR-processing flow — it
    never touches the NSL provisioning stack, so the universal NSL provisioning
    CLOSER (Century-DB trace → NBOP_MIG_* DB-table validation → Genesis verify) is
    fabricated boilerplate for it and must be suppressed. The feature title alone can
    hide the signal (e.g. a "wearable Audit Report" feature whose TCs are PRR/Amdocs
    mediation), so the detector reads the TC summary + every step summary/expected —
    reusing the module's own ``is_mediation_feature`` signal list (no new vocabulary).
    """
    parts = [feature_context or '', getattr(tc, 'summary', '') or '']
    for st in (getattr(tc, 'steps', []) or []):
        parts.append(getattr(st, 'summary', '') or '')
        parts.append(getattr(st, 'expected', '') or '')
    return is_mediation_feature(*parts)


# ── Grounded-only closer gate (ROOT-CAUSE fix) ────────────────────────────────
# The universal provisioning CLOSER (Century-DB trace → NBOP_MIG_* DB-table
# validation → Genesis/NE-Portal verify) asserts a set of NSL/NBOP provisioning
# CONCEPTS. Historically the closer was injected whenever a feature's *title*
# matched a provisioning keyword (is_provisioning_feature), with authored/mediation
# TCs carved out as exceptions. That is default-ON with a blocklist, and it
# fabricated NBOP_MIG/TRANSACTION_HISTORY/Service-Grouping/Century/NE-Portal steps
# onto plain API features (4497/4574/4575/4576/4611) whose own evidence never
# mentions those concepts.
#
# The fix inverts the policy to GROUNDED-ONLY: a canned closer may be injected into
# a TC ONLY when the NSL/NBOP provisioning concept is actually present in THIS
# feature's own evidence (Chalk scope/rules, Jira AC, QMetry-authored/generated
# steps for this feature). Grounding is the PRIMARY mechanism; a per-phrase banned
# backstop is only a final safety net. Both predicates are pure, case-insensitive
# and deterministic (no I/O, no randomness).

# Concept keyword groups that ground the canned closer at the FEATURE level. These
# are the SPECIFIC NSL/NBOP data-plane concepts the closer/downstream steps assert —
# the DB-table family, the Century/Service-Grouping/NE-Portal/Genesis verification
# surfaces and the Syniverse/"NSL processes request"/"Trigger API" flow markers.
#
# ROOT-CAUSE FIX (iter-3): the generic tokens 'provision', bare 'nbop' and bare 'nsl'
# were REMOVED from this set. Every MWTGPROV feature's Jira/Chalk evidence says
# "provisioning" (and usually "nbop"/"nsl"), so those generic tokens made
# concept_grounded() return True for every provisioning feature and the StepAssembler
# backstop then kept the fabricated NBOP_MIG/Service-Grouping/Century/NE-Portal steps
# on plain API features (4574/4575/4576). A feature is now "grounded" only when its own
# evidence references one of these SPECIFIC data-plane concepts — not a generic
# "provisioning" word. All keywords are stored in normalized form (underscores ->
# spaces) and matched against the normalized evidence blob, so 'nbop mig' catches both
# 'NBOP_MIG' and 'NBOP MIG' in the evidence.
_CLOSER_CONCEPT_KEYWORDS = (
    'nbop mig', 'nbop_mig', 'transaction history', 'line history',
    'service grouping', 'century', 'ne portal', 'ne-portal', 'genesis',
    'syniverse', 'trigger api', 'nsl processes request', 'nbop line summary',
    'nsl db tables',
)

# Per-phrase banned backstop. Each entry maps a banned surface phrase (as it would
# appear in an emitted step) to the set of concept keywords that would GROUND it. A
# step carrying a banned phrase is only allowed when at least one of its grounding
# keywords appears in the feature's own evidence blob.
#
# ROOT-CAUSE FIX (iter-3): each banned phrase is now grounded ONLY by its OWN specific
# data-plane concept — the generic 'provision'/'nbop'/'nsl' tokens were removed from
# every grounding set. Previously those generic tokens re-admitted exactly the
# fabricated NBOP_MIG/Service-Grouping/Century/NE-Portal content on MWTGPROV features
# (whose evidence always contains "provisioning"/"nbop"/"nsl"). Now an NBOP_MIG step
# survives only when the feature's evidence actually names the NBOP-MIG tables, a
# Century step only when the evidence names Century, and so on.
#
# Phrases, step text AND the grounding keywords are all normalized (underscores ->
# spaces, whitespace collapsed) before matching, so a single entry catches BOTH the
# pattern-library closer surface ('NBOP_MIG_LINE', 'TRANSACTION_HISTORY') and the
# integration-contract downstream surface ('NBOP MIG tables', 'Transaction History'),
# and a grounding keyword like 'nbop mig' matches evidence written as either
# 'NBOP_MIG' or 'NBOP MIG'.
_BANNED_CLOSER_PHRASES = (
    ('nbop mig', ('nbop mig',)),
    # TRANSACTION_HISTORY / LINE_HISTORY are member tables of the NBOP_MIG family, so a
    # feature grounded on the NBOP-MIG tables legitimately carries them inside the single
    # NBOP_MIG DB-table-validation step. They are ALSO grounded standalone by their own
    # name — but NOT by a generic 'provision'/'nbop'/'nsl' word.
    ('transaction history', ('transaction history', 'nbop mig')),
    ('line history', ('line history', 'nbop mig')),
    ('service grouping', ('service grouping',)),
    ('century report', ('century',)),
    ('century db', ('century',)),
    ('ne portal', ('ne portal', 'ne-portal')),
    ('genesis portal', ('genesis',)),
    ('tmo genesis', ('genesis',)),
    ('nbop line summary', ('nbop line summary', 'nbop mig')),
    ('nsl processes request', ('nsl processes request',)),
    ('trigger api', ('trigger api',)),
    ('nsl db tables', ('nsl db tables', 'nbop mig')),
    ('syniverse', ('syniverse',)),
)


def _normalize_phrase(text: str) -> str:
    """Lowercase and collapse underscores/whitespace so 'NBOP_MIG_LINE' and
    'NBOP MIG tables' both normalize to a space-delimited form for matching."""
    return re.sub(r'[\s_]+', ' ', (text or '').lower()).strip()


def _closer_concept_grounded(evidence_blob: str) -> bool:
    """PRIMARY gate: True when the feature's own evidence references a SPECIFIC
    NSL/NBOP data-plane concept the canned closer asserts.

    *evidence_blob* is the concatenation of this feature's Chalk scope/rules, Jira AC,
    feature text and the TC's own summary/step text. It is normalized (underscores ->
    spaces, whitespace collapsed) so underscore table tokens like 'NBOP_MIG' match the
    'nbop mig' concept keyword. A generic "provisioning" word no longer grounds the
    closer — only the specific DB-table / Century / Service-Grouping / NE-Portal /
    Genesis / Syniverse / "Trigger API" concepts do (see _CLOSER_CONCEPT_KEYWORDS).
    """
    blob = _normalize_phrase(evidence_blob)
    if not blob:
        return False
    return any(kw in blob for kw in _CLOSER_CONCEPT_KEYWORDS)


def _step_is_banned_and_ungrounded(step_text: str, evidence_blob: str) -> bool:
    """BACKSTOP: True when *step_text* carries a banned provisioning phrase whose
    SPECIFIC grounding concept is absent from *evidence_blob*.

    Used as a final per-step safety net AFTER the primary grounding gate: even if the
    gate passes on some concept, an individual closer/downstream step that asserts a
    DIFFERENT banned concept not present in the evidence is still skipped. Both the step
    text and the evidence blob are normalized before matching so an 'NBOP_MIG' step is
    grounded only when the evidence itself names the NBOP-MIG tables (not by a generic
    "provisioning" word).
    """
    s = _normalize_phrase(step_text)
    blob = _normalize_phrase(evidence_blob)
    for phrase, grounding_kws in _BANNED_CLOSER_PHRASES:
        if phrase in s and not any(kw in blob for kw in grounding_kws):
            return True
    return False


def concept_grounded(evidence_blob: str) -> bool:
    """Public PRIMARY grounding predicate: True when *evidence_blob* references the
    NSL/NBOP provisioning concept the canned closer / downstream-verification steps
    assert. Thin public wrapper over ``_closer_concept_grounded`` so other producers
    (e.g. the dimension_extractor D1/D2 state/partial-failure matrix injection) can gate
    on the SAME grounding signal. Pure, case-insensitive, deterministic.
    """
    return _closer_concept_grounded(evidence_blob)


def ground_downstream_steps(steps, evidence_blob: str):
    """Grounding gate for the integration-contract downstream-verification producer.

    The integration-contract step generator (``integration_contract.build_downstream_
    verification_steps``, resolved via ``dimension_extractor.resolve_operation`` and the
    per-system ``must_call`` chain) fabricates Service-Grouping / Century-Report /
    Syniverse / NBOP-MIG / TMO-Genesis verification steps onto a TC purely from an
    operation-alias title match — the SAME default-ON-with-exceptions policy the closer
    path used to have. For plain API features (4497/4574/4575/4576/4611) whose own
    evidence never mentions the NSL/NBOP provisioning concept, those steps are fabricated
    garbage.

    This applies the SAME grounded-only policy as the closer gate:

    - PRIMARY gate (``_closer_concept_grounded``): if the feature's own evidence blob
      (Chalk scope/rules + Jira AC + feature text + the TC's own step/validation text)
      does NOT reference the NSL/NBOP provisioning concept, return ``[]`` — emit NO
      downstream verification steps. This is the root-cause fix for the dirty features.
    - BACKSTOP (``_step_is_banned_and_ungrounded``): even when the primary gate passes
      on some concept, drop any individual step whose banned phrase (NBOP_MIG / Service
      Grouping / Century / Transaction/Line History / NE Portal / Genesis / Syniverse)
      is NOT itself grounded in the evidence.

    *steps* is a list of ``(summary, expected)`` tuples (the shape
    ``build_downstream_verification_steps`` returns). Returns the grounded subset, same
    shape. A genuinely grounded NSL-provisioning feature keeps its legitimate steps.
    Pure, case-insensitive, deterministic — no I/O.
    """
    if not steps:
        return []
    if not _closer_concept_grounded(evidence_blob):
        return []
    kept = []
    for item in steps:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            summ, exp = item[0], item[1]
        else:
            summ, exp = str(item), ''
        if _step_is_banned_and_ungrounded('%s %s' % (summ or '', exp or ''), evidence_blob):
            continue
        kept.append(item)
    return kept


def enrich_test_case(tc, feature_context: str = '',
                     add_closers: bool = True,
                     fix_preconditions: bool = True,
                     pair_century: bool = False,
                     feature_evidence: str = '',
                     log=None) -> object:
    """Enrich one TestCase object in place with human patterns. Returns it.

    - add_closers: append century-trace + DB-table + Genesis verify, but ONLY when
      the NSL/NBOP provisioning concept is grounded in THIS feature's own evidence
      (and not already present). See _closer_concept_grounded.
    - fix_preconditions: overwrite filler/empty preconditions with grounded ones.
    - pair_century: insert Century-report validation after Trigger <API> steps.
    - feature_evidence: lowercased-able blob of the feature's own evidence (Chalk
      scope/rules + Jira AC + feature text), built once by enrich_suite and passed
      down. Defaults empty → the grounding blob then falls back to ctx + the TC's
      own step text only (zero behavioural change for callers that pass evidence on
      the TC/context, which is how every in-repo caller works).

    Duck-typed: works on the V8 TestCase (steps=List[TestStep], preconditions:str)
    without importing it, so there is no circular-import risk.
    """
    changed = []
    summary = getattr(tc, 'summary', '') or ''
    category = (getattr(tc, 'category', '') or '').lower()
    ctx = (feature_context + ' ' + summary).strip()

    # 1. Preconditions — never touch one authored in Chalk (ground truth, Rule #1).
    #    The batch/PRR/file-ingestion keyword guess has been removed: batch-feature
    #    preconditions are now owned solely by StepAssembler.reconcile() from the feature
    #    classification. Here we only ground generic filler for NON-batch features; the
    #    assembler runs AFTER enrichment and overwrites batch_file TCs authoritatively.
    if fix_preconditions and not getattr(tc, 'precond_from_chalk', False):
        pre = getattr(tc, 'preconditions', '') or ''
        if is_filler_precondition(pre) and not is_file_ingestion_feature(ctx):
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

    # 3. Closers — GROUNDED-ONLY policy (ROOT-CAUSE fix).
    #    The canned NSL provisioning CLOSER (Century-DB trace → NBOP_MIG_* DB-table
    #    validation → Genesis/NE-Portal verify) is injected ONLY when the NSL/NBOP
    #    provisioning concept is actually grounded in THIS feature's own evidence
    #    (Chalk scope/rules + Jira AC + feature text, threaded in as feature_evidence,
    #    plus the TC's own summary/step text). A plain API feature whose evidence never
    #    mentions NSL/NBOP/Service-Grouping/Century/NE-Portal gets NO closer — this is
    #    the fix for 4497/4574/4575/4576/4611, which previously passed the title-only
    #    is_provisioning_feature heuristic and had DB-table steps fabricated onto them.
    #    The prior guards stay in the AND-chain:
    #    - negative category and non-provisioning features are excluded;
    #    - QMetry-authored TCs keep their authored steps (ground truth, no closer);
    #    - Mediation/PRR TCs validate the Amdocs billing flow, not NSL, so no NSL closer.
    #    _closer_concept_grounded is the PRIMARY gate; _step_is_banned_and_ungrounded is
    #    a final per-step backstop that skips any individual banned step whose specific
    #    concept is not grounded, even if the primary gate passed on a different concept.
    step_text = ' '.join(
        '%s %s' % (getattr(st, 'summary', '') or '', getattr(st, 'expected', '') or '')
        for st in steps)
    evidence_blob = (' '.join([
        feature_evidence or '', ctx, summary, step_text,
    ])).lower()
    if (add_closers and 'negative' not in category
            and is_provisioning_feature(ctx) and not _tc_has_db_closer(tc)
            and not _tc_is_qmetry_authored(tc)
            and not _tc_is_mediation(tc, feature_context)
            and _closer_concept_grounded(evidence_blob)):
        start = len(steps)
        op = operation_label(ctx)
        for s, e in provisioning_closer(feature_context, op):
            # Backstop: skip any closer step asserting a banned concept that is not
            # grounded in this feature's evidence.
            if _step_is_banned_and_ungrounded('%s %s' % (s, e), evidence_blob):
                continue
            _cl = _make_step(len(steps) + 1, s, e)
            if hasattr(_cl, 'origin'):
                _cl.origin = 'closer'
            steps.append(_cl)
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
    # Build the feature-evidence blob ONCE from the suite's own Chalk/Jira evidence.
    # This is the feature's own evidence (Chalk scope/rules via scope/rules, Jira AC,
    # feature text); the grounded-only closer gate reads it per TC. Every attribute is
    # guarded so a partially-populated suite never raises.
    _ac = getattr(suite, 'acceptance_criteria', None) or []
    try:
        _ac_text = '\n'.join(str(a) for a in _ac)
    except Exception:
        _ac_text = ''
    feature_evidence = ' '.join([
        fc or '',
        _ac_text,
        getattr(suite, 'scope', '') or '',
        getattr(suite, 'rules', '') or '',
        getattr(suite, 'feature_desc', '') or '',
    ])
    n_closers = n_pre = 0
    for tc in getattr(suite, 'test_cases', []) or []:
        try:
            before_steps = len(getattr(tc, 'steps', []) or [])
            before_pre = getattr(tc, 'preconditions', '')
            enrich_test_case(tc, feature_context=fc, add_closers=add_closers,
                             fix_preconditions=fix_preconditions,
                             pair_century=pair_century,
                             feature_evidence=feature_evidence, log=None)
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
