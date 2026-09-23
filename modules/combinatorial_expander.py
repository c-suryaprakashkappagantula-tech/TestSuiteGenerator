"""
combinatorial_expander.py — Matrix expansion for V8.0 (opt-in).

The default V8 combination engine is ADDITIVE: it emits ~one TC per Chalk
scenario and deliberately avoids the cartesian product (combination_engine.py).
Hand-written suites, however, are combinatorial — e.g. MWTGPROV-4482 crosses
  Scenario × SubscriberState(YL/YD/YP/PY) × Device(Phone/Tablet/Wearable)
     × Channel(NBOP/ITMBO) × Carrier(TMO/VZW)
into 34 TCs. This module closes that breadth gap by expanding the eligible
Happy-Path TCs of an already-built suite across the applicable axes, producing
named variant TCs with specialised preconditions and a channel-aware step.

Design goals (why this is a safe post-pass, not a rewrite of the planner):
  * Opt-in — nothing changes unless options['expand_matrix'] is truthy.
  * Bounded — a hard cap (max_expanded_tcs) prevents cartesian explosion;
    axes are dropped in priority order (carrier → channel → state → device)
    when the cap would be exceeded.
  * Targeted — only Happy-Path provisioning TCs expand; negatives, E2E,
    CR-boilerplate and already-specialised TCs pass through untouched.
  * Grounded — each variant differs meaningfully (device/state/channel in the
    preconditions and a channel-specific step), so variants are real device/
    state coverage, not renamed duplicates.

Naming: {feature_id}_TC{NN}_{base-desc}_{STATE}_{CHANNEL}_{DEVICE}[_{CARRIER}]
"""
import copy
import re
from itertools import product
from typing import Callable, Dict, List, Optional, Tuple

# ── Axis vocabularies ─────────────────────────────────────────────────────────
# Subscriber-state 2-letter codes used across MDA/NSL provisioning. Discovery
# scans feature text for these; DEFAULT_SYNC_STATES seeds sync/inquiry features
# (mirrors the states step_templates already hardcodes).
KNOWN_STATES = ['YL', 'YD', 'YP', 'PY', 'PN', 'YN', 'NY', 'PP', 'LL', 'DD']
DEFAULT_SYNC_STATES = ['YL', 'YD', 'YP', 'PY']

KNOWN_DEVICES = ['Phone', 'Tablet', 'Wearable', 'Smartwatch', 'Hotspot']
DEVICE_ALIASES = {'smartwatch': 'Wearable', 'watch': 'Wearable', 'tab': 'Tablet',
                  'handset': 'Phone', 'mobile': 'Phone'}

KNOWN_CHANNELS = ['NBOP', 'ITMBO']
KNOWN_CARRIERS = ['TMO', 'VZW']

# Feature families whose flow legitimately varies by subscriber state.
_STATE_VARYING_HINTS = (
    'sync subscriber', 'sync line', 'line inquiry', 'inquiry response',
    'subscriber status', 'data limit', 'reset plan', 'subscriber',
)
_PROVISIONING_HINTS = (
    'nbop', 'nsl', 'subscriber', 'activation', 'activate', 'line', 'sim',
    'device', 'feature', 'plan', 'mdn', 'swap', 'sync', 'wearable', 'port',
    'reset', 'hotline', 'suspend', 'restore', 'reclaim', 'transfer', 'inquiry',
)
_NON_PROVISIONING_HINTS = (
    'kafka', 'notification', 'cdr', 'mediation', 'ild', 'prr file',
    'batch report', 'differential report',
)


# ── Discovered axis container ─────────────────────────────────────────────────
class Axis:
    """One combinatorial axis: a name, a slug role, and its ordered values."""
    __slots__ = ('name', 'values', 'priority')

    def __init__(self, name: str, values: List[str], priority: int):
        self.name = name
        self.values = values
        self.priority = priority  # higher = dropped first when over cap

    def __repr__(self):
        return 'Axis(%s=%s)' % (self.name, self.values)


def _find_all(vocab: List[str], text: str, word_boundary: bool = False) -> List[str]:
    """Return vocab entries present in *text*, preserving vocab order."""
    found = []
    for v in vocab:
        pat = r'\b%s\b' % re.escape(v) if word_boundary else re.escape(v)
        if re.search(pat, text, re.I):
            found.append(v)
    return found


def discover_axes(feature_text: str,
                  suite_text: str = '',
                  overrides: Optional[Dict] = None) -> List[Axis]:
    """Discover which axes apply to this feature and their values.

    feature_text: Jira summary/description + Chalk scope (the requirement text).
    suite_text:   concatenated TC summaries/steps (what was actually generated).
    overrides:    optional {states|devices|channels|carriers: [...]} to force values.
    """
    overrides = overrides or {}
    text = ((feature_text or '') + ' ' + (suite_text or '')).strip()
    tl = text.lower()

    # Devices (map aliases first)
    devices = list(overrides.get('devices') or [])
    if not devices:
        devices = _find_all(KNOWN_DEVICES, text)
        for alias, canon in DEVICE_ALIASES.items():
            if re.search(r'\b%s\b' % alias, tl) and canon not in devices:
                devices.append(canon)
        # de-dup preserving canonical order
        devices = [d for d in KNOWN_DEVICES if d in set(devices)]

    # Channels
    channels = list(overrides.get('channels') or []) or _find_all(KNOWN_CHANNELS, text, True)

    # Carriers
    carriers = list(overrides.get('carriers') or []) or _find_all(KNOWN_CARRIERS, text, True)

    # Subscriber states
    states = list(overrides.get('states') or [])
    if not states:
        states = _find_all(KNOWN_STATES, text, True)
        if any(h in tl for h in _STATE_VARYING_HINTS):
            for s in DEFAULT_SYNC_STATES:
                if s not in states:
                    states.append(s)
            states = [s for s in KNOWN_STATES if s in set(states)]

    axes: List[Axis] = []
    # priority: HIGHER number = dropped FIRST when the cap is hit.
    # State is the signature axis of state-driven features (YL/YD/YP/PY), so it
    # is kept last; device next; channel/carrier are spot-checks, dropped first.
    if states:
        axes.append(Axis('state', states, priority=1))
    if len(devices) > 1:
        axes.append(Axis('device', devices, priority=2))
    if channels and len(channels) > 1:
        axes.append(Axis('channel', channels, priority=3))
    if carriers and len(carriers) > 1:
        axes.append(Axis('carrier', carriers, priority=4))
    return axes


# ── Eligibility ───────────────────────────────────────────────────────────────

def is_provisioning(text: str) -> bool:
    t = (text or '').lower()
    if any(h in t for h in _NON_PROVISIONING_HINTS):
        return False
    return any(h in t for h in _PROVISIONING_HINTS)


_ALREADY_SPECIALISED = re.compile(
    r'_(?:%s)_' % '|'.join(KNOWN_STATES) + r'|_(?:NBOP|ITMBO)_|_(?:Phone|Tablet|Wearable)\b',
    re.I)

# Per-scenario expansion profiles — mirrors how hand-written suites cross
# different scenario groups on different axes (observed in MWTGPROV-4482):
#   single      → no expansion (the status/variant IS the test dimension)
#   device_only → cross device only (state is fixed by the scenario)
#   default     → state × device (the core state-varying flow)
_SINGLE_HINTS = (
    'subscriberstatus', 'subscriber status', 'no hotline', 'hotline feature',
    'status_active', 'status_deactive', 'status_suspend', '_suspend_', '_deactive_',
)
_DEVICE_ONLY_HINTS = (
    'reset plan', 'reset line', 'network reset', 'pdl mismatch', 'already in sync',
    'plan,features,pdl', 'plan features and pdl', 'features and pdl',
)


def _scenario_profile(text: str) -> str:
    t = (text or '').lower()
    if any(h in t for h in _SINGLE_HINTS):
        return 'single'
    if any(h in t for h in _DEVICE_ONLY_HINTS):
        return 'device_only'
    return 'default'


def _tc_is_expandable(tc) -> bool:
    cat = (getattr(tc, 'category', '') or '').lower()
    if cat and cat != 'happy path':
        return False
    summ = getattr(tc, 'summary', '') or ''
    low = summ.lower()
    # Skip CR-boilerplate / E2E lifecycle / regression scaffolding.
    if any(k in low for k in ('regression', 'e2e', 'lifecycle', 'cr fix', 'unrelated workflow')):
        return False
    if not is_provisioning(summ + ' ' + (getattr(tc, 'description', '') or '')):
        return False
    return True


def _applicable_axes(tc, axes: List[Axis]) -> List[Axis]:
    """Return the axes that apply to one base TC.

    - Skip an axis the TC already pins (its name already contains a value).
    - The STATE axis only applies to state-varying scenarios (sync / line
      inquiry / subscriber-status / data-limit) — a Century-report or generic
      display TC is not multiplied across subscriber states.
    """
    summ = (getattr(tc, 'summary', '') or '')
    txt = (summ + ' ' + (getattr(tc, 'description', '') or '')).lower()

    profile = _scenario_profile(txt)
    if profile == 'single':
        return []   # status/variant scenarios are not multiplied

    out = []
    for ax in axes:
        # device_only scenarios (Reset Plan, PDL mismatch, already-in-sync) fix
        # the subscriber state and channel — cross device (and carrier) only.
        if profile == 'device_only' and ax.name in ('state', 'channel'):
            continue
        if ax.name == 'state' and not any(h in txt for h in _STATE_VARYING_HINTS):
            continue
        # If the TC name already pins a value on this axis, don't cross it.
        pinned = any(re.search(r'\b%s\b' % re.escape(v), summ, re.I) for v in ax.values)
        if pinned:
            continue
        out.append(ax)
    return out


# ── Slug / naming ─────────────────────────────────────────────────────────────

def _base_desc(summary: str, feature_id: str) -> str:
    """Reduce a summary to its scenario description, dropping the feature id and
    any TCnn token wherever they appear (they can be in the middle, e.g.
    'TC001_MWTGPROV-4482_Verify ...')."""
    s = summary or ''
    if feature_id:
        s = re.sub(re.escape(feature_id), '', s, flags=re.I)
    s = re.sub(r'\bTC\d+', '', s, flags=re.I)          # 'TC006_' has no \b before '_'
    s = re.sub(r'[_\s]{2,}', '_', s).strip(' _-:')
    s = re.sub(r'^(Verify|Validate|Test|Check)\s*:?\s*(that\s+)?', '', s, flags=re.I)
    return _clean_cut(s.strip(' _-:'), 90) or 'scenario'


def _clean_cut(s: str, limit: int) -> str:
    """Trim to <= limit chars WITHOUT cutting a word in half. Never leaves a
    dangling partial word or trailing connector (so names never end abruptly)."""
    s = (s or '').strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    # back up to the last word boundary
    if ' ' in cut:
        cut = cut[:cut.rstrip().rfind(' ')]
    # drop a trailing connector/preposition so it doesn't read as cut-off
    cut = re.sub(r'[\s_]+(?:and|or|from|to|with|the|of|in|for|by|when|that|a|an)$',
                 '', cut, flags=re.I)
    return cut.strip(' _-:')


def _variant_slug(combo: Dict[str, str], axes_order: List[str]) -> str:
    parts = [combo[a] for a in axes_order if a in combo]
    return '_'.join(parts)


# ── Variant construction ──────────────────────────────────────────────────────

_ACTIVE_LINE_RE = re.compile(r'active (?:tmo )?line', re.I)


def _specialise_preconditions(pre: str, combo: Dict[str, str]) -> str:
    """Fold the variant's device/state/channel into the preconditions."""
    dev = combo.get('device')
    lines = [ln for ln in (pre or '').split('\n') if ln.strip()]
    if not lines:
        lines = ['1. User must have an active TMO line.']
    # Make the active-line line device-specific.
    if dev:
        for i, ln in enumerate(lines):
            if _ACTIVE_LINE_RE.search(ln):
                lines[i] = _ACTIVE_LINE_RE.sub('active TMO %s line' % dev, ln)
                break
        else:
            lines.append('%d. Line provisioned on a %s device.' % (len(lines) + 1, dev))
    extra = []
    if combo.get('state'):
        extra.append('Subscriber sync state = %s.' % combo['state'])
    if combo.get('channel'):
        extra.append('Transaction submitted via %s channel.' % combo['channel'])
    if combo.get('carrier') and combo['carrier'] != 'TMO':
        extra.append('Carrier / network = %s.' % combo['carrier'])
    for e in extra:
        lines.append('%d. %s' % (len(lines) + 1, e))
    # renumber
    out = []
    for i, ln in enumerate(lines, 1):
        ln = re.sub(r'^\s*\d+[\.\)]\s*', '', ln).strip()
        out.append('%d. %s' % (i, ln))
    return '\n'.join(out)


def _channel_step(combo: Dict[str, str], make_step):
    ch = combo.get('channel')
    if not ch:
        return None
    return make_step(0, 'Submit the transaction via %s for the %s subscriber.'
                     % (ch, combo.get('state', 'target')),
                     'Request accepted through %s and routed to NSL successfully.' % ch)


def _make_variant(base_tc, combo: Dict[str, str], axes_order: List[str],
                  feature_id: str, make_step):
    """Deep-copy the base TC and specialise it for one axis combination."""
    tc = copy.deepcopy(base_tc)
    desc = _base_desc(getattr(base_tc, 'summary', ''), feature_id)
    slug = _variant_slug(combo, axes_order)
    # summary is renamed here; the TCnn number is assigned later by the caller.
    tc.summary = '%s :: %s_%s' % (desc, slug, '')  # placeholder, finalised in expand_suite
    tc._variant_desc = desc
    tc._variant_slug = slug
    tc._variant_combo = dict(combo)
    tc.preconditions = _specialise_preconditions(getattr(base_tc, 'preconditions', ''), combo)
    # Insert a channel-specific step near the top (after any OAuth/token step).
    cs = _channel_step(combo, make_step)
    if cs is not None:
        steps = list(getattr(tc, 'steps', []) or [])
        insert_at = 0
        for i, s in enumerate(steps[:2]):
            if 'oauth' in (getattr(s, 'summary', '') or '').lower() or 'token' in (getattr(s, 'summary', '') or '').lower():
                insert_at = i + 1
        steps.insert(insert_at, cs)
        tc.steps = steps
    return tc


def _make_step_factory():
    try:
        from .data_models_v8 import TestStep
        return lambda n, s, e: TestStep(step_num=n, summary=s, expected=e)
    except Exception:
        class _Step:  # pragma: no cover
            def __init__(self, n, s, e):
                self.step_num, self.summary, self.expected, self.data_reference = n, s, e, ''
        return lambda n, s, e: _Step(n, s, e)


# ── Planning + expansion ──────────────────────────────────────────────────────

def _combos_for_tc(tc, axes: List[Axis], carrier_sparse: bool
                   ) -> Tuple[List[Dict[str, str]], List[str]]:
    """Build the axis combinations for a single base TC.

    Carrier is handled sparsely: rather than a full cross, the non-default
    carrier (e.g. VZW) adds ONE extra variant (its first device/state), matching
    how hand-written suites include a couple of carrier spot-checks — not a full
    carrier × everything product.
    """
    ax = _applicable_axes(tc, axes)
    cross_axes = [a for a in ax if a.name != 'carrier']
    carrier_axis = next((a for a in ax if a.name == 'carrier'), None)
    order = [a.name for a in cross_axes]

    value_lists = [a.values for a in cross_axes]
    combos: List[Dict[str, str]] = []
    if value_lists:
        for tup in product(*value_lists):
            combos.append({order[i]: tup[i] for i in range(len(order))})
    else:
        combos.append({})

    # Sparse carrier spot-check(s)
    if carrier_axis and carrier_sparse:
        non_default = [c for c in carrier_axis.values if c.upper() != 'TMO']
        if non_default and combos:
            spot = dict(combos[0])
            spot['carrier'] = non_default[0]
            combos.append(spot)
            order = order + ['carrier']
    elif carrier_axis:  # full carrier cross (only if not sparse)
        new_combos = []
        for c in carrier_axis.values:
            for combo in combos:
                nc = dict(combo); nc['carrier'] = c; new_combos.append(nc)
        combos = new_combos
        order = order + ['carrier']

    return combos, order


def plan_expansion(suite, feature_text: str, options: Optional[Dict] = None,
                   log: Callable = print) -> Dict:
    """Dry-run: report how many TCs expansion would produce (no mutation)."""
    options = options or {}
    axes = discover_axes(feature_text,
                         ' '.join((getattr(tc, 'summary', '') or '') for tc in getattr(suite, 'test_cases', [])),
                         overrides=options.get('axis_overrides'))
    carrier_sparse = options.get('carrier_sparse', True)
    base = [tc for tc in getattr(suite, 'test_cases', []) if _tc_is_expandable(tc)]
    passthrough = len(getattr(suite, 'test_cases', [])) - len(base)
    planned = 0
    for tc in base:
        combos, _ = _combos_for_tc(tc, axes, carrier_sparse)
        planned += max(1, len(combos))
    return {
        'axes': {a.name: a.values for a in axes},
        'expandable_tcs': len(base),
        'passthrough_tcs': passthrough,
        'planned_variant_tcs': planned,
        'projected_total': planned + passthrough,
    }


def expand_suite(suite, feature_text: str = '', options: Optional[Dict] = None,
                 log: Callable = print) -> object:
    """Expand eligible Happy-Path TCs across discovered axes. Returns the suite.

    options:
      max_expanded_tcs (int, default 80) — hard cap on total TCs after expansion.
      carrier_sparse   (bool, default True) — VZW/non-TMO adds a spot-check, not
                        a full cross.
      axis_overrides   (dict) — force {states|devices|channels|carriers: [...]}.
    Never raises — on any error the original suite is returned unchanged.
    """
    options = options or {}
    try:
        make_step = _make_step_factory()
        cap = int(options.get('max_expanded_tcs', 80))
        carrier_sparse = options.get('carrier_sparse', True)
        tcs = list(getattr(suite, 'test_cases', []) or [])
        suite_text = ' '.join((getattr(t, 'summary', '') or '') for t in tcs)
        axes = discover_axes(feature_text, suite_text, overrides=options.get('axis_overrides'))

        if not axes:
            log('[EXPAND] No combinatorial axes discovered — suite unchanged')
            return suite

        expandable = [t for t in tcs if _tc_is_expandable(t)]
        passthrough = [t for t in tcs if t not in expandable]
        log('[EXPAND] Axes: %s | %d expandable, %d passthrough'
            % ({a.name: a.values for a in axes}, len(expandable), len(passthrough)))

        # Drop axes (highest priority number first) until projected total fits cap.
        working_axes = list(axes)
        while working_axes:
            projected = len(passthrough)
            for tc in expandable:
                combos, _ = _combos_for_tc(tc, working_axes, carrier_sparse)
                projected += max(1, len(combos))
            if projected <= cap or len(working_axes) == 1:
                break
            drop = max(working_axes, key=lambda a: a.priority)
            working_axes.remove(drop)
            log('[EXPAND]   Over cap (%d>%d) — dropping axis "%s"' % (projected, cap, drop.name))

        # Build variants
        variants = []
        seen_slugs = set()
        for tc in expandable:
            combos, order = _combos_for_tc(tc, working_axes, carrier_sparse)
            if not combos or combos == [{}]:
                variants.append(tc)   # nothing to cross → keep base
                continue
            for combo in combos:
                v = _make_variant(tc, combo, order, _feature_id(suite), make_step)
                key = (v._variant_desc.lower(), v._variant_slug.lower())
                if key in seen_slugs:
                    continue
                seen_slugs.add(key)
                variants.append(v)
                if len(variants) + len(passthrough) >= cap:
                    break
            if len(variants) + len(passthrough) >= cap:
                log('[EXPAND]   Hit cap of %d — stopping expansion' % cap)
                break

        new_tcs = variants + passthrough
        _finalise_names_and_numbers(new_tcs, _feature_id(suite))
        suite.test_cases = new_tcs
        log('[EXPAND] Expanded suite: %d TCs (was %d)' % (len(new_tcs), len(tcs)))
        return suite
    except Exception as exc:
        log('[EXPAND] Expansion failed (%s) — suite unchanged' % str(exc)[:120])
        return suite


def _feature_id(suite) -> str:
    return getattr(suite, 'feature_id', '') or getattr(suite, 'feature_title', '') or 'TC'


def _finalise_names_and_numbers(tcs, feature_id: str) -> None:
    """Assign TCnn and the final combinatorial summary to every TC."""
    for i, tc in enumerate(tcs, 1):
        num = 'TC%02d' % i
        tc.sno = str(i)
        if getattr(tc, '_variant_slug', ''):
            tc.summary = '%s_%s_%s_%s' % (
                feature_id, num, getattr(tc, '_variant_desc', 'scenario'), tc._variant_slug)
        else:
            # Passthrough / un-crossed — keep description, refresh number/prefix.
            base = _base_desc(getattr(tc, 'summary', ''), feature_id)
            tc.summary = '%s_%s_%s' % (feature_id, num, base)
        # renumber steps
        for j, s in enumerate(getattr(tc, 'steps', []) or [], 1):
            if hasattr(s, 'step_num'):
                s.step_num = j
