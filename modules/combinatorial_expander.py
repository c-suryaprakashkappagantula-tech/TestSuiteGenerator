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
    axes are dropped in priority order (carrier → channel → device → plan →
    state) when the cap would be exceeded.
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

# ── plan_matrix (the opt-in plan axis) ────────────────────────────────────────
# Imported at MODULE level, which is safe even though the reference is two-way:
# plan_matrix pulls KNOWN_DEVICES / DEVICE_ALIASES out of THIS module lazily
# (inside its own _device_vocab(), cached), never at its module body, so neither
# import order produces a partially-initialised module.
# Aliased as _plan_matrix_mod, not `plan_matrix`, because discover_axes takes a
# `plan_matrix` boolean parameter that would otherwise shadow the module.
# Guarded + retried by _plan_matrix_module() so a missing/broken plan_matrix
# degrades to "no plan axis" instead of breaking ordinary expansion.
try:
    from . import plan_matrix as _plan_matrix_mod
except Exception:                                                    # pragma: no cover
    try:
        import plan_matrix as _plan_matrix_mod
    except Exception:
        _plan_matrix_mod = None


def _plan_matrix_module():
    """The plan_matrix module, or None. Retries the import once per call if it failed."""
    global _plan_matrix_mod
    if _plan_matrix_mod is None:
        try:
            try:
                from . import plan_matrix as _pm
            except Exception:
                import plan_matrix as _pm                            # noqa: F401
            _plan_matrix_mod = _pm
        except Exception:
            return None
    return _plan_matrix_mod

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
    """One combinatorial axis: a name, a slug role, and its ordered values.

    `meta` is an optional side-channel for axes whose VALUES are not self-describing.
    The plan axis uses it to stash {axis value -> PlanCombo}, so a later stage can
    recover the full device/network/SIM/retail/wholesale combination from a bare axis
    value. Defaults to None and every pre-existing construction site leaves it unset.
    """
    __slots__ = ('name', 'values', 'priority', 'meta')

    def __init__(self, name: str, values: List[str], priority: int,
                 meta: Optional[Dict] = None):
        self.name = name
        self.values = values
        self.priority = priority  # higher = dropped first when over cap
        self.meta = meta

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


def _build_plan_axis(overrides: Dict, plan_overrides: List[str],
                     log: Callable = print) -> Optional[Axis]:
    """Axis('plan', [combo.key ...], priority=2) with {key: PlanCombo} in .meta, or None.

    The axis VALUES are pre-validated composite combinations from
    plan_matrix.plan_combinations(), so an invalid device/plan pair is never created and
    therefore never has to be filtered out (Requirement 3.1). The {key: PlanCombo} map on
    .meta is how later stages recover the full combination from an axis value.

    NEVER raises (Requirement 8.6): on ANY failure the plan axis is omitted, the reason is
    logged with the [EXPAND] tag, and the caller's other axes go on expanding normally.

    SIM overrides are accepted under BOTH spellings: custom_scenario_builder's
    parse_axis_overrides emits 'sim_types', while the design text refers to 'sims'.
    """
    try:
        pm = _plan_matrix_module()
        if pm is None:
            raise ImportError('modules.plan_matrix could not be imported')
        combos = pm.plan_combinations(
            devices=list(overrides.get('devices') or []) or None,
            networks=list(overrides.get('networks') or []) or None,
            sims=list(overrides.get('sim_types') or overrides.get('sims') or []) or None,
            plan_overrides=plan_overrides or None,
            log=log)
        if not combos:
            raise ValueError('plan_combinations() produced no combinations')
        return Axis('plan', [c.key for c in combos], priority=2,
                    meta={c.key: c for c in combos})
    except Exception as exc:
        log('[EXPAND] plan axis unavailable (%s) — continuing without it' % str(exc)[:120])
        return None


def discover_axes(feature_text: str,
                  suite_text: str = '',
                  overrides: Optional[Dict] = None,
                  plan_matrix: bool = False,
                  log: Callable = print) -> List[Axis]:
    """Discover which axes apply to this feature and their values.

    feature_text: Jira summary/description + Chalk scope (the requirement text).
    suite_text:   concatenated TC summaries/steps (what was actually generated).
    overrides:    optional {states|devices|channels|carriers|plans: [...]} to force values.
    plan_matrix:  opt-in plan axis. Threaded down from options['plan_matrix'] by
                  expand_suite / plan_expansion; explicit overrides['plans'] values request
                  the axis on their own, so either signal is sufficient (Requirement 12.2).
    log:          only ever written to when the plan axis is REQUESTED and fails, so a run
                  without a plan signal stays byte-identical to the pre-plan-matrix
                  behaviour (Requirement 1.4).
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

    # Plan axis — opt-in, built only when requested, so nothing changes for a run with
    # neither overrides['plans'] nor plan_matrix=True.
    plan_overrides = list(overrides.get('plans') or [])
    plan_axis = (_build_plan_axis(overrides, plan_overrides, log)
                 if (plan_overrides or plan_matrix) else None)

    axes: List[Axis] = []
    # priority: HIGHER number = dropped FIRST when the cap is hit. Values are a purely
    # relative, in-memory ordering (read only by the max() in expand_suite's drop loop) and
    # are persisted nowhere, so they are renumbered freely to seat `plan` between state and
    # device: state=1, plan=2, device=3, channel=4, carrier=5.
    # State is the signature axis of state-driven features (YL/YD/YP/PY), so it
    # is kept last; the plan axis next (it carries the feature's whole point when on);
    # device after that; channel/carrier are spot-checks, dropped first.
    if states:
        axes.append(Axis('state', states, priority=1))
    if plan_axis is not None:
        axes.append(plan_axis)
    if len(devices) > 1:
        axes.append(Axis('device', devices, priority=3))
    if channels and len(channels) > 1:
        axes.append(Axis('channel', channels, priority=4))
    if carriers and len(carriers) > 1:
        axes.append(Axis('carrier', carriers, priority=5))
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


# ── Plan-combo unpacking ──────────────────────────────────────────────────────
# Key under which the whole PlanCombo is stashed on an emitted combo dict, so later stages
# (task 6.2's suite.combinations population, task 8.1's Combinations sheet) read the object
# instead of re-parsing 'Phone_5G_eSIM_CMUNLP' back into fields.
PLAN_COMBO_KEY = '_plan_combo'

# combo dict key -> PlanCombo attribute. combo['plan'] itself deliberately stays the AXIS
# VALUE (PlanCombo.key), NOT the bare retail plan: _variant_slug composes the TC name from the
# axis order, and that single token is what carries Device_Class + network + SIM type + retail
# plan into the name (Requirement 6.2) and into expand_suite's seen_slugs dedup. The individual
# fields below are added ALONGSIDE it so nothing downstream has to split the key apart.
_PLAN_COMBO_FIELDS = ('device', 'network', 'sim', 'current_plan', 'retail_plan',
                      'wholesale_plan')


def _plan_combo_for(plan_axis: Axis, value):
    """The PlanCombo behind one plan axis value, or None. Never raises."""
    try:
        meta = getattr(plan_axis, 'meta', None)
        if not meta:
            return None
        return meta.get(value)
    except Exception:
        return None


def _apply_plan_fields(combo: Dict, plan_axis: Axis) -> Dict:
    """Fold the PlanCombo behind combo['plan'] into `combo`, in place.

    Populates combo['device'] from the PlanCombo — which is why the device axis is absorbed
    rather than crossed — plus the network, SIM, current/retail/wholesale plans, and the
    PlanCombo itself under PLAN_COMBO_KEY.

    Degrades gracefully (Requirement 8.6): when `meta` is missing or the value is absent from
    it, the axis value is left as an opaque string and NOTHING is added, so the plan axis
    behaves like any other string axis instead of crashing the run.
    """
    pc = _plan_combo_for(plan_axis, combo.get('plan'))
    if pc is None:
        return combo
    for field in _PLAN_COMBO_FIELDS:
        val = getattr(pc, field, None)
        if val:
            combo[field] = val
    combo[PLAN_COMBO_KEY] = pc
    return combo


# ── Planning + expansion ──────────────────────────────────────────────────────

def _combos_for_tc(tc, axes: List[Axis], carrier_sparse: bool
                   ) -> Tuple[List[Dict[str, str]], List[str]]:
    """Build the axis combinations for a single base TC.

    Carrier is handled sparsely: rather than a full cross, the non-default
    carrier (e.g. VZW) adds ONE extra variant (its first device/state), matching
    how hand-written suites include a couple of carrier spot-checks — not a full
    carrier × everything product.

    DEVICE-AXIS ABSORPTION (Requirement 3.1). A plan axis value is a whole PlanCombo and
    already pins a Device_Class, so crossing the device axis as well would emit impossible
    device/plan pairs (Tablet × CMUNL). Whenever a plan axis applies to this TC the device axis
    is therefore skipped entirely and `combo['device']` comes from the PlanCombo: the cross is
    `state × plan-combo`, never `state × device × network × SIM × plan`. This matters because
    the plan axis is inherently multi-device — even `overrides['plans'] = ['CMUNLP']` yields
    Tablet and Wearable combos, since plan_combinations() resolves a conflicting plan per device
    through the family rule instead of dropping the device.

    With no plan axis present the behaviour is unchanged.
    """
    ax = _applicable_axes(tc, axes)
    # Tested against the APPLICABLE axes, not the caller's list: if a TC filtered the plan axis
    # out, it keeps its ordinary device coverage rather than losing both axes.
    plan_axis = next((a for a in ax if a.name == 'plan'), None)
    if plan_axis is not None:
        ax = [a for a in ax if a.name != 'device']
    cross_axes = [a for a in ax if a.name != 'carrier']
    carrier_axis = next((a for a in ax if a.name == 'carrier'), None)
    order = [a.name for a in cross_axes]

    value_lists = [a.values for a in cross_axes]
    combos: List[Dict[str, str]] = []
    if value_lists:
        for tup in product(*value_lists):
            combo = {order[i]: tup[i] for i in range(len(order))}
            # Before the carrier spot-check below, so its dict(combos[0]) copy inherits the
            # plan fields too.
            if plan_axis is not None:
                _apply_plan_fields(combo, plan_axis)
            combos.append(combo)
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


def _record_reduction_notes(suite, notes, log: Callable = print) -> None:
    """Append plan-axis reduction notes to suite.combination_plan.reduction_notes.

    Requirement 7.6 wants the reduction visible in the dashboard's Combination Plan panel,
    which renders `suite.combination_plan.reduction_notes`. Requirement 8.6 says never raise, so
    a suite whose `combination_plan` is absent, None or read-only simply loses the notes — they
    are already in the [EXPAND] / [PLAN-MATRIX] log — instead of failing the run. No
    CombinationPlan is fabricated here: an engine that never built one is not given a partial.
    """
    if not notes:
        return
    try:
        plan = getattr(suite, 'combination_plan', None)
        if plan is None:
            return
        existing = getattr(plan, 'reduction_notes', None)
        if existing is None:
            plan.reduction_notes = list(notes)
        else:
            existing.extend(notes)
    except Exception as exc:
        log('[EXPAND] could not record reduction notes (%s)' % str(exc)[:120])


def _trim_plan_axis(suite, plan_axis: Axis, budget: int, log: Callable = print) -> None:
    """Reduce a plan axis to `budget` values via plan_matrix.trim_to_budget(). Never raises.

    The axis is mutated IN PLACE — `values` and `meta` are rebuilt together from the kept
    PlanCombos — so the axis list already handed to the drop loop needs no reconstruction, and
    the notes returned by trim_to_budget are carried onto the suite's combination_plan.

    trim_to_budget collapses SIM, then network, then extra plans within a family, and never
    takes a Device_Class below one combination, so the trim reduces breadth without losing a
    device. On ANY failure the axis is left exactly as it was: the existing drop loop then
    treats it like any other over-cap axis, which is a worse outcome than a trim but never a
    broken run (Requirement 8.6).
    """
    try:
        pm = _plan_matrix_module()
        if pm is None:
            return
        meta = dict(getattr(plan_axis, 'meta', None) or {})
        combos = [meta[v] for v in plan_axis.values if v in meta]
        if not combos or len(combos) != len(plan_axis.values):
            # meta out of sync with values — trimming would drop values that cannot be rebuilt.
            return
        kept, notes = pm.trim_to_budget(combos, budget, log)
        kept = [c for c in kept if getattr(c, 'key', None) in meta]
        if not kept or len(kept) >= len(plan_axis.values):
            return
        before = len(plan_axis.values)
        plan_axis.values = [c.key for c in kept]
        plan_axis.meta = {c.key: c for c in kept}
        log('[EXPAND]   Plan axis trimmed %d -> %d value(s) to fit budget %d'
            % (before, len(plan_axis.values), budget))
        _record_reduction_notes(suite, notes, log)
    except Exception as exc:
        log('[EXPAND] plan axis trim failed (%s) — axis left unchanged' % str(exc)[:120])


def _cap_projection(expandable, n_passthrough: int, working_axes: List[Axis],
                    carrier_sparse: bool, cap: int, plan_run: bool) -> Tuple[int, int]:
    """(projected_total, budget) for one turn of the cap drop loop: drop while projected > budget.

    The projection has to model the emission strategy that will ACTUALLY run, because the two
    strategies produce different totals from the same axes.

    NON-PLAN RUNS — `_emit_depth_first`. Unchanged: it walks the full cross base TC by base TC and
    only stops when the cap is reached, so the full cross IS the projection and the budget IS the
    cap. Same arithmetic, same numbers, same log line as before this amendment (Requirement 1.4).

    PLAN RUNS — `_emit_breadth_first`. The emitter ALREADY self-limits: pass 0 emits one variant per
    expandable base TC unconditionally, then the round-robin passes stop the moment the running
    total reaches the cap. It can therefore only ever produce

        min(full cross, max(cap, one-per-base-TC floor))

    and never the full cross. Projecting the full cross made the loop drop axes to avoid breadth
    that would never be emitted. Measured consequences of that pessimism:

      * plan-axis survival was NON-MONOTONIC in the cap — on a 4-expandable-base-TC feature cap 60
        gave 57 TCs but cap 90 gave only 10, because above the per-base-TC budget the pre-loop
        `_trim_plan_axis` stops firing and the drop loop then removed the whole plan axis instead;
      * on an 8-expandable-base-TC feature with a 4-value state axis the plan axis was dropped at
        EVERY cap up to 640 — ticking Plan_Matrix_Control silently produced no plan variants.

    The budget is `max(cap, floor)` rather than `cap` because pass 0 is unconditional: when
    len(expandable) + len(passthrough) exceeds the cap the total legitimately exceeds it
    (Requirement 9.4 outranks 11.4) and dropping axes CANNOT bring it down — every base TC still
    contributes its one TC. Measuring against the bare cap there would drop every droppable axis
    for no reduction at all. The floor overage is not hidden by this: `_emit_breadth_first` still
    logs it as exceeded-by-necessity when it happens.

    Drop ORDER is untouched (Requirement 8.4): this function only decides WHETHER the loop drops,
    the caller still picks the victim by `max(priority)` — carrier, channel, device, plan, state.
    """
    projected = n_passthrough
    for tc in expandable:
        combos, _ = _combos_for_tc(tc, working_axes, carrier_sparse)
        projected += max(1, len(combos))
    if not plan_run:
        return projected, cap
    budget = max(cap, n_passthrough + len(expandable))
    return min(projected, budget), budget


def _emit_depth_first(expandable, working_axes: List[Axis], carrier_sparse: bool,
                      feature_id: str, make_step, cap: int, n_passthrough: int,
                      log: Callable = print) -> List:
    """The ORIGINAL variant loop, lifted verbatim out of expand_suite.

    Used whenever NO plan axis is present, which is what keeps existing `expand_matrix`-only runs
    byte-identical down to the log stream (Requirement 1.4). Nothing in here changed: same
    `_combos_for_tc` call, same base-TC passthrough for an empty cross, same `seen_slugs` dedup
    keyed on (desc, slug), same two cap checks and the same 'Hit cap' line.
    """
    variants = []
    seen_slugs = set()
    for tc in expandable:
        combos, order = _combos_for_tc(tc, working_axes, carrier_sparse)
        if not combos or combos == [{}]:
            variants.append(tc)   # nothing to cross → keep base
            continue
        for combo in combos:
            v = _make_variant(tc, combo, order, feature_id, make_step)
            key = (v._variant_desc.lower(), v._variant_slug.lower())
            if key in seen_slugs:
                continue
            seen_slugs.add(key)
            variants.append(v)
            if len(variants) + n_passthrough >= cap:
                break
        if len(variants) + n_passthrough >= cap:
            log('[EXPAND]   Hit cap of %d — stopping expansion' % cap)
            break
    return variants


def _emit_breadth_first(expandable, working_axes: List[Axis], carrier_sparse: bool,
                        feature_id: str, make_step, cap: int, n_passthrough: int,
                        log: Callable = print) -> List:
    """Round-robin variant emission — used ONLY on plan-matrix runs (see the gate in expand_suite).

    WHY: depth-first fills base TC 1's variants, then TC 2's, and `break`s at the cap. With a plan
    axis each base TC has many variants (state × plan-combo), so a late base TC can lose its ONLY
    representative — and with it the Chalk scenario behind it. Requirement 9.4 forbids displacing
    Chalk coverage, so:

        pass 0 : the first surviving variant of EVERY expandable base TC, cap ignored
        pass k : the k-th variant of each base TC, in turn, while the cap allows

    Pass 0 being unconditional is the cap carve-out: when the cap is smaller than the number of
    expandable base TCs the total legitimately exceeds it, and that is LOGGED as
    exceeded-by-necessity rather than silently dropping a base TC (Requirement 11.4 yields to 9.4).

    Everything else matches the depth-first loop: variants come from `_make_variant`, a base TC
    with nothing to cross contributes itself, and the same (desc, slug) dedup applies GLOBALLY
    across base TCs.
    """
    # [tc, combos, order, cursor]; combos == [] marks a base-only task (nothing to cross), which
    # emits the base TC itself once and is then exhausted.
    tasks = []
    for tc in expandable:
        combos, order = _combos_for_tc(tc, working_axes, carrier_sparse)
        if not combos or combos == [{}]:
            combos, order = [], []
        tasks.append([tc, list(combos), order, 0])

    variants = []
    seen_slugs = set()

    def _next(task):
        """The task's next non-duplicate variant, or None once its combos are exhausted."""
        tc, combos, order, cursor = task
        if not combos:
            task[3] = cursor + 1
            return tc if cursor == 0 else None
        while cursor < len(combos):
            combo = combos[cursor]
            cursor += 1
            task[3] = cursor
            v = _make_variant(tc, combo, order, feature_id, make_step)
            key = (v._variant_desc.lower(), v._variant_slug.lower())
            if key in seen_slugs:
                continue
            seen_slugs.add(key)
            return v
        task[3] = cursor
        return None

    # ── pass 0 — one TC per base TC, unconditionally ──────────────────────────
    for task in tasks:
        v = _next(task)
        if v is not None:
            variants.append(v)
    if len(variants) + n_passthrough > cap:
        log('[EXPAND]   Cap %d exceeded by necessity: %d base TC(s) each keep one TC to preserve '
            'Chalk coverage (total %d)' % (cap, len(variants), len(variants) + n_passthrough))

    # ── pass k >= 1 — round-robin while under cap ────────────────────────────
    while True:
        if len(variants) + n_passthrough >= cap:
            log('[EXPAND]   Hit cap of %d — stopping expansion' % cap)
            break
        emitted = 0
        for task in tasks:
            if len(variants) + n_passthrough >= cap:
                break
            v = _next(task)
            if v is not None:
                variants.append(v)
                emitted += 1
        if emitted == 0:
            break
    return variants


def _append_err07_cases(suite, variants: List, n_passthrough: int, cap: int, make_step,
                        log: Callable = print) -> List:
    """Append the plan-matrix ERR07 negatives to `variants` (in place) and return it. Never raises.

    Requirements 4.1/4.5: one wrong-family negative per plan family (CM, CT), gated by the caller
    on `options['include_negative']` so clearing the dashboard's 'Negative scenarios' option omits
    them.

    BUDGET RULE: a negative is appended only while the running total (variants + passthrough +
    negatives so far) is still UNDER the cap. The pass-0 carve-out above exists to protect
    CHALK-DERIVED base TCs; an ERR07 case is a plan-matrix addition, not Chalk coverage, so it must
    not widen an already over-cap total. Whatever does not fit is dropped with a log line.

    `_variant_desc` / `_variant_slug` are OWNED by plan_matrix.build_err07_cases() — it sets them
    on every case it returns and exports the values as ERR07_VARIANT_DESC / ERR07_SLUG_TEMPLATE —
    so they are deliberately NOT set again here; the cases arrive naming-ready and flow through
    `_finalise_names_and_numbers` like any other variant. They also carry NO `_variant_combo` on
    purpose: an ERR07 case is not a device × network × SIM combination and must not become a
    `suite.combinations` row.
    """
    try:
        pm = _plan_matrix_module()
        if pm is None:
            raise ImportError('modules.plan_matrix could not be imported')
        cases = pm.build_err07_cases(_feature_id(suite), make_step, log=log) or []
        if not cases:
            return variants
        added = 0
        for case in cases:
            if len(variants) + n_passthrough >= cap:
                log('[EXPAND]   No budget left for %d ERR07 negative(s) at cap %d'
                    % (len(cases) - added, cap))
                break
            variants.append(case)
            added += 1
        if added:
            log('[EXPAND]   Appended %d ERR07 negative(s)' % added)
        return variants
    except Exception as exc:
        log('[EXPAND] ERR07 negatives unavailable (%s) — continuing without them' % str(exc)[:120])
        return variants


def plan_expansion(suite, feature_text: str, options: Optional[Dict] = None,
                   log: Callable = print) -> Dict:
    """Dry-run: report how many TCs expansion would produce (no mutation)."""
    options = options or {}
    axes = discover_axes(feature_text,
                         ' '.join((getattr(tc, 'summary', '') or '') for tc in getattr(suite, 'test_cases', [])),
                         overrides=options.get('axis_overrides'),
                         plan_matrix=bool(options.get('plan_matrix')),
                         log=log)
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
      axis_overrides   (dict) — force {states|devices|channels|carriers|plans: [...]}.
      plan_matrix      (bool, default False) — opt-in plan axis (device × network ×
                        SIM × retail plan × wholesale plan). Set by the engine from
                        the dashboard control; axis_overrides['plans'] also turns it
                        on by itself. Shares max_expanded_tcs — there is no second cap.
    Never raises — on any error the original suite is returned unchanged.
    """
    options = options or {}
    try:
        make_step = _make_step_factory()
        cap = int(options.get('max_expanded_tcs', 80))
        carrier_sparse = options.get('carrier_sparse', True)
        tcs = list(getattr(suite, 'test_cases', []) or [])
        suite_text = ' '.join((getattr(t, 'summary', '') or '') for t in tcs)
        axes = discover_axes(feature_text, suite_text,
                             overrides=options.get('axis_overrides'),
                             plan_matrix=bool(options.get('plan_matrix')),
                             log=log)

        if not axes:
            log('[EXPAND] No combinatorial axes discovered — suite unchanged')
            return suite

        expandable = [t for t in tcs if _tc_is_expandable(t)]
        passthrough = [t for t in tcs if t not in expandable]
        log('[EXPAND] Axes: %s | %d expandable, %d passthrough'
            % ({a.name: a.values for a in axes}, len(expandable), len(passthrough)))

        working_axes = list(axes)

        # Is this a plan-matrix run? Computed HERE, above the cap loop, because it now selects the
        # projection mode as well as the emission strategy further down — the two must agree, or the
        # loop would drop axes for breadth the emitter would never produce. The test is "a plan axis
        # was discovered, or options['plan_matrix'] asked for one", NOT "a plan axis survived the
        # drop loop"; see the emission gate below for why.
        plan_run = any(a.name == 'plan' for a in axes) or bool(options.get('plan_matrix'))

        # ── Plan-axis trim, BEFORE the drop loop (Requirements 8.1, 8.2, 7.6) ──
        # The plan axis has no cap control of its own, so when it alone would blow the cap it is
        # REDUCED here rather than left for the drop loop to remove whole.
        #
        # "The plan axis alone would exceed the cap" is measured as the per-base-TC share of the
        # remaining budget: budget = (cap - passthrough) // expandable — how many plan values
        # each expandable base TC may contribute while every base TC still gets a representative.
        # Comparing len(values) against the RAW cap was the other option, but it would almost
        # never fire (20 combos against a cap of 60) and the drop loop would then remove the plan
        # axis entirely, losing the whole matrix instead of one SIM type or one network. Other
        # axes multiply on top of this share; the existing drop loop below still handles that.
        plan_axis = next((a for a in working_axes if a.name == 'plan'), None)
        if plan_axis is not None and expandable:
            budget = (cap - len(passthrough)) // max(1, len(expandable))
            if len(plan_axis.values) > budget:
                _trim_plan_axis(suite, plan_axis, budget, log)

        # Drop axes (highest priority number first) until the projected total fits the budget.
        # `_cap_projection` models the emission strategy that will actually run: the full cross for
        # depth-first (non-plan runs, unchanged), the self-limited min(full cross, budget) for the
        # breadth-first plan-matrix path. On a non-plan run budget == cap, so both the comparison
        # and the log line below print exactly what they printed before.
        while working_axes:
            projected, budget = _cap_projection(expandable, len(passthrough), working_axes,
                                                carrier_sparse, cap, plan_run)
            if projected <= budget or len(working_axes) == 1:
                break
            drop = max(working_axes, key=lambda a: a.priority)
            working_axes.remove(drop)
            log('[EXPAND]   Over cap (%d>%d) — dropping axis "%s"' % (projected, budget, drop.name))

        # ── Build variants ───────────────────────────────────────────────────
        # Emission strategy is gated on whether this run IS a plan-matrix run — a plan axis was
        # discovered, or options['plan_matrix'] asked for one — NOT on whether the axis survived
        # the drop loop. Measured reason: at a cap of 20-100 with a 4-value state axis the drop
        # loop removes the whole plan axis, and the starvation breadth-first exists to prevent is
        # WORST exactly there (cap 20, 8 base TCs, state only: depth-first covered 1 of 8 base TCs
        # and left 3 with no TC at all). Gating on the surviving axis would therefore switch
        # breadth-first off in the very runs that need it. Requirement 1.4 is about runs with NO
        # plan signal, and those discover no plan axis, so they stay on the byte-identical
        # `_emit_depth_first` path — verified log-for-log against the pre-feature module.
        # `plan_run` is computed ABOVE the cap loop, which selects its projection mode from the
        # same flag: the projection must model whichever emitter runs here, or the loop drops axes
        # for breadth that is never emitted.
        emit = _emit_breadth_first if plan_run else _emit_depth_first
        variants = emit(expandable, working_axes, carrier_sparse, _feature_id(suite), make_step,
                        cap, len(passthrough), log)

        # ── ERR07 negatives, before finalisation so naming flows through one path ──
        # Same gate: a run whose plan axis was dropped to fit the cap still ASKED for the plan
        # matrix, and the wrong-family rejection is a fixed pair of cases, not a combinatorial cost.
        if plan_run and options.get('include_negative', True):
            variants = _append_err07_cases(suite, variants, len(passthrough), cap, make_step, log)

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
