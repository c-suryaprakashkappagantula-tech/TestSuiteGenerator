# -*- coding: utf-8 -*-
"""
plan_matrix.py - Plan-matrix vocabulary and the CONSTRAINED device x network x SIM x plan cross.

WHAT THIS IS: the single place that knows which retail plan and which wholesale plan belong to a
device/network combination, and the generator that turns those tables into pre-validated
`PlanCombo` values. `combinatorial_expander` consumes those values as ONE axis (task 4.1), so an
invalid device/plan pair is never CREATED and therefore never has to be filtered out again
(Requirement 3.1 "constrained cross rather than free cross product").

PROVENANCE OF THE PLAN CODES: harvested from real request payloads captured in the crawled Chalk
pages (`Chalk_NMNO_Services.db`, pages "T025. add-wearable" and "T034. transfer-watch"). The
wholesale catalog itself lives OUTSIDE Chalk, so `WHOLESALE_BY_DEVICE_NETWORK` is evidence-based
and MAY NOT BE EXHAUSTIVE - it is what was observed on the wire, not a vendor-published list.
Wearable has only ONE plan (`MSWAP`): there is no base/Plus pair for a watch.

TABLE SHAPE IS THE ENFORCEMENT MECHANISM (Requirement 2.8): `CH_5G_TAB_CB_DCL36` is listed only
under a `Tablet` key and `CHARTER_WEARABLE_AW` only under `Wearable` keys, so the restriction holds
structurally. There is no post-filter to forget to call.

THE FAMILY RULE IS NOT RE-ENCODED HERE. `retail_plan` is produced by
`rateplan_rule.compute_new_rateplan(current_plan, device)` - the facade that resolves to the
canonical `shared/lifecycle_validator/orchestrator.py` implementation, with a vendored mirror as the
fallback (Requirement 2.9/2.10). The authoritative rule DOCUMENT is
`.kiro/steering/rateplan-rules.md`. `RETAIL_BY_DEVICE` below is the VALIDATION authority (which
plans are legal for a device) and the source for the exclusion logic - never the source of the
target plan itself.

ASSUMED CURRENT PLAN: the family rule only means something relative to a starting plan, so every
`PlanCombo` carries an explicit `current_plan` (Phone -> `CMUNL`, Tablet -> `CTUNL`,
Wearable -> `MSWAP`) and `retail_plan` is the rule's answer for it. Both plans of a CM/CT family are
reached by using each of them as the current plan in turn, because the rule toggles base <-> Plus:
`CMUNL -> CMUNLP` and `CMUNLP -> CMUNL`. So the second plan value per family arrives from the RULE,
not from a literal read out of `RETAIL_BY_DEVICE`.

WEARABLE IS OUTSIDE THE CM/CT RULE. `compute_new_rateplan('MSWAP', 'Wearable')` would fall through
to the CM fallback and answer `CMUNLP`, which Requirements 2.4 and 3.4 forbid for a watch. The rule
governs the CM/CT base<->Plus toggle only, so `Wearable` resolves to `MSWAP` directly (see
`_resolve_retail`). This is a documented carve-out, not a second copy of the rule.

DEVICE VOCABULARY: `KNOWN_DEVICES` + `DEVICE_ALIASES` from `combinatorial_expander` are the ONLY
device vocabulary (Requirement 10.3). This module defines no alias table of its own; every incoming
spelling is canonicalised through them by `canonical_device()`. Note `KNOWN_DEVICES` contains BOTH
`Smartwatch` and `Wearable`, so aliases are consulted BEFORE `KNOWN_DEVICES` to make
`Smartwatch` -> `Wearable`.

CIRCULAR-IMPORT APPROACH: task 4.1 makes `combinatorial_expander` import from THIS module, so the
reference becomes two-way. The device vocabulary is therefore imported LAZILY, inside
`_device_vocab()`, and cached in module globals after the first successful call - never at module
import time. A hard-coded fallback pair is used only if that import fails, so a changed import shape
degrades instead of crashing generation. The fallback is NOT a competing alias table: it is the same
data, reached only when the real one is unreachable.

Log lines all carry the `[PLAN-MATRIX]` tag.

TASK SCOPE: this file implements the tables, `PlanCombo`, `plan_combinations()`,
`excluded_pairs()`, and the budget/precondition/ERR07/label helpers `trim_to_budget()`,
`compose_preconditions()`, `build_err07_cases()` and `display_device()`.
"""
import re
from dataclasses import dataclass

# The family-rule facade. Imported at module level (NOT lazily): rateplan_rule has no dependency on
# this module, so there is no cycle - only the DEVICE vocabulary needs the lazy treatment. Relative
# import first because `modules` is a package; absolute fallback for the scripts that put `modules/`
# itself on sys.path, the same pattern rateplan_rule uses for its own vendored copy. Import ONLY
# from this facade, never from rateplan_rule_local or the shared module.
try:
    from .rateplan_rule import compute_new_rateplan
except Exception:                                                    # pragma: no cover
    from rateplan_rule import compute_new_rateplan                   # noqa: F401

__all__ = [
    'RETAIL_BY_DEVICE', 'WHOLESALE_BY_DEVICE_NETWORK', 'NETWORKS', 'SIM_TYPES',
    'PLAN_FAMILIES', 'ERR07_MESSAGE', 'CANONICAL_DEVICES', 'CURRENT_PLAN_BY_DEVICE',
    'WEARABLE_PLAN', 'FAMILY_WEARABLE', 'NETWORK_AGNOSTIC_WHOLESALE', 'ALL_WHOLESALE_PLANS',
    'PlanCombo', 'plan_combinations', 'excluded_pairs',
    'canonical_device', 'canonical_network', 'canonical_sim',
    'retail_plans_for_device', 'plan_family', 'is_valid_pair', 'wholesale_network_marker',
    'trim_to_budget', 'compose_preconditions', 'build_err07_cases', 'display_device',
    'DEVICE_LABELS', 'FAMILY_STICKINESS_TEMPLATE', 'ERR07_VARIANT_DESC', 'ERR07_SLUG_TEMPLATE',
]

LOG_TAG = '[PLAN-MATRIX]'


# ── Plan tables ───────────────────────────────────────────────────────────────
# Retail (customer-facing) plans that are LEGAL for a Device_Class. This is the VALIDATION
# authority and the input to the exclusion logic; the target plan itself always comes from
# compute_new_rateplan(). Wearable holds a single value on purpose - no base/Plus pair.
RETAIL_BY_DEVICE = {
    'Phone':    ('CMUNL', 'CMUNLP'),
    'Tablet':   ('CTUNL', 'CTUNLP'),
    'Wearable': ('MSWAP',),
}

# (Device_Class, network) -> ordered wholesale candidates.
# Device-specific codes appear ONLY under their device key, which is how Requirement 2.8 is
# enforced by table shape rather than by a post-filter.
WHOLESALE_BY_DEVICE_NETWORK = {
    ('Phone',    '5G'): ('CH_5G_CUSTOM_DCAL_3', 'CH_5G_CB_DCL36'),
    ('Phone',    '4G'): ('CH_4G_CUSTOM_DCAL_3', 'CH_4GHD_DYNCAL_AQCI'),
    ('Tablet',   '5G'): ('CH_5G_TAB_CB_DCL36',),
    ('Tablet',   '4G'): ('CH_4G_CUSTOM_DCAL_3',),
    ('Wearable', '5G'): ('CHARTER_WEARABLE_AW',),
    ('Wearable', '4G'): ('CHARTER_WEARABLE_AW',),
}

NETWORKS = ('5G', '4G')
SIM_TYPES = ('eSIM', 'pSIM')
PLAN_FAMILIES = ('CM', 'CT')
ERR07_MESSAGE = 'Invalid RetailPlan for a line'

# The three canonical Device_Class values the plan matrix fans out over (Requirement 2.1).
# Derived from the table so the two can never disagree.
CANONICAL_DEVICES = ('Phone', 'Tablet', 'Wearable')

# Assumed starting plan per device - what the preconditions will state (Requirement 5.1) and
# what the family rule is applied to.
CURRENT_PLAN_BY_DEVICE = {
    'Phone':    'CMUNL',
    'Tablet':   'CTUNL',
    'Wearable': 'MSWAP',
}

WEARABLE_PLAN = 'MSWAP'
FAMILY_WEARABLE = 'WEARABLE'

# CHARTER_WEARABLE_AW carries no network marker: the same code serves a 5G and a 4G watch.
# Requirement 2.4 mandates it for every Wearable combination, which outranks the 5G/4G code
# enumeration of Requirements 2.6/2.7. Consult wholesale_network_marker() rather than pattern
# matching on the code, so the exemption lives in exactly one place.
NETWORK_AGNOSTIC_WHOLESALE = ('CHARTER_WEARABLE_AW',)

# Flat enumerated wholesale set (Requirement 2.5), derived from the table - never hand-listed.
ALL_WHOLESALE_PLANS = tuple(sorted({
    code for codes in WHOLESALE_BY_DEVICE_NETWORK.values() for code in codes
}))

# Fallback device vocabulary, used ONLY if the lazy import of the real one fails. Same data as
# combinatorial_expander's, deliberately not a competing table (see the module docstring).
_FALLBACK_KNOWN_DEVICES = ['Phone', 'Tablet', 'Wearable', 'Smartwatch', 'Hotspot']
_FALLBACK_DEVICE_ALIASES = {'smartwatch': 'Wearable', 'watch': 'Wearable', 'tab': 'Tablet',
                            'handset': 'Phone', 'mobile': 'Phone'}

_VOCAB_CACHE = None


def _device_vocab(log=None):
    """(KNOWN_DEVICES, DEVICE_ALIASES) from combinatorial_expander, imported LAZILY.

    Lazy + cached because task 4.1 makes combinatorial_expander import this module: resolving the
    vocabulary at module-import time would create a genuine import cycle. Called on every
    canonicalisation, so the result is cached after the first success.
    """
    global _VOCAB_CACHE
    if _VOCAB_CACHE is not None:
        return _VOCAB_CACHE
    try:
        try:
            from .combinatorial_expander import KNOWN_DEVICES, DEVICE_ALIASES
        except Exception:
            from combinatorial_expander import KNOWN_DEVICES, DEVICE_ALIASES  # noqa: F401
        _VOCAB_CACHE = (list(KNOWN_DEVICES), dict(DEVICE_ALIASES))
    except Exception as exc:
        if log:
            log('%s device vocabulary import failed (%s) - using the fallback copy' % (LOG_TAG, exc))
        _VOCAB_CACHE = (list(_FALLBACK_KNOWN_DEVICES), dict(_FALLBACK_DEVICE_ALIASES))
    return _VOCAB_CACHE


# ── Canonicalisation helpers ──────────────────────────────────────────────────

def canonical_device(device, log=None):
    """Canonical Device_Class for any incoming spelling, or None when unrecognised.

    Aliases are checked FIRST because KNOWN_DEVICES holds both 'Smartwatch' and 'Wearable' and
    'Smartwatch' must resolve to 'Wearable' (Requirement 10.2).
    """
    raw = (device or '').strip()
    if not raw:
        return None
    key = raw.lower()
    known, aliases = _device_vocab(log)
    for alias, canon in aliases.items():
        if key == str(alias).strip().lower():
            return canon
    for name in known:
        if key == str(name).strip().lower():
            # A KNOWN_DEVICES entry that is itself an alias target of another spelling still has
            # to survive the alias pass above; reaching here means it is already canonical.
            return name
    if log:
        log('%s unrecognised device "%s" - skipped' % (LOG_TAG, raw))
    return None


def canonical_network(network):
    """'5g' / ' 5G ' -> '5G'. Unrecognised values are returned upper-cased and unchanged."""
    raw = (network or '').strip().upper()
    for n in NETWORKS:
        if raw == n.upper():
            return n
    return raw


def canonical_sim(sim):
    """'esim' -> 'eSIM', 'PSIM' -> 'pSIM'. Unrecognised values are returned stripped."""
    raw = (sim or '').strip()
    for s in SIM_TYPES:
        if raw.lower() == s.lower():
            return s
    return raw


def _canonical_plan(plan):
    return (plan or '').strip().upper()


def plan_family(plan):
    """'CM' | 'CT' | 'WEARABLE' for a retail plan code."""
    code = _canonical_plan(plan)
    for fam in PLAN_FAMILIES:
        if code.startswith(fam):
            return fam
    return FAMILY_WEARABLE


def retail_plans_for_device(device, log=None):
    """The retail plans LEGAL for a device, canonicalising the device first. () when unknown."""
    canon = canonical_device(device, log)
    return RETAIL_BY_DEVICE.get(canon, ())


def is_valid_pair(device, plan, log=None):
    """True when `plan` is legal for `device` under the family rule (Requirements 3.2-3.4)."""
    return _canonical_plan(plan) in retail_plans_for_device(device, log)


def wholesale_network_marker(code):
    """'5G' | '4G' | None - the network a wholesale code denotes.

    None means network-agnostic (CHARTER_WEARABLE_AW), which is legitimately paired with either
    network; see NETWORK_AGNOSTIC_WHOLESALE.
    """
    raw = (code or '').strip().upper()
    if raw in tuple(c.upper() for c in NETWORK_AGNOSTIC_WHOLESALE):
        return None
    if '5G' in raw:
        return '5G'
    if '4G' in raw:
        return '4G'
    return None


# ── PlanCombo ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanCombo:
    """One pre-validated device x network x SIM x retail plan x wholesale plan combination.

    Frozen so combos are hashable and safe to use as axis values / dict keys in the expander.
    A PlanCombo only ever exists for a LEGAL pair: `plan_combinations()` constructs it from a
    device's own family plans, so there is no such thing as an invalid instance to filter out.
    """
    device: str          # canonical: Phone | Tablet | Wearable
    network: str         # 5G | 4G
    sim: str             # eSIM | pSIM
    current_plan: str    # assumed starting plan - the family rule's authoritative input
    retail_plan: str     # target plan, from compute_new_rateplan(current_plan, device)
    wholesale_plan: str

    @property
    def family(self):
        """'CM' | 'CT' | 'WEARABLE' - the retail plan's family."""
        return plan_family(self.retail_plan)

    @property
    def key(self):
        """Axis value / slug, e.g. 'Phone_5G_eSIM_CMUNLP'."""
        return '%s_%s_%s_%s' % (self.device, self.network, self.sim, self.retail_plan)

    def __str__(self):
        return self.key


# ── Combination generation ────────────────────────────────────────────────────

def _resolve_retail(current_plan, device):
    """Target retail plan for a line, THROUGH THE FACADE - never a literal.

    Wearable is the documented carve-out: the CM/CT base<->Plus rule does not reach MSWAP (the
    rule's device fallback would answer CMUNLP, which Requirements 2.4/3.4 forbid for a watch),
    and a watch plan has no base/Plus pair to toggle.
    """
    if device == 'Wearable':
        return WEARABLE_PLAN
    return compute_new_rateplan(current_plan, device)


def _current_for_target(device, target):
    """The current plan whose rule answer is `target`.

    The rule toggles base <-> Plus inside a family, so the current plan for a two-plan family is
    simply the OTHER plan (CMUNLP is reached from CMUNL and vice versa). Single-plan families
    (Wearable) are their own current plan.
    """
    plans = RETAIL_BY_DEVICE.get(device, ())
    if len(plans) < 2:
        return plans[0] if plans else target
    others = [p for p in plans if p != target]
    return others[0] if others else plans[0]


def _wholesale_for(device, network, retail_plan):
    """Wholesale code for a combination, or None when the device/network pair has no entry.

    The candidate is picked by the retail plan's position within the device's family, so a
    two-candidate cell (Phone) exercises BOTH of its observed codes across the family's two plans
    instead of always emitting the first one.
    """
    candidates = WHOLESALE_BY_DEVICE_NETWORK.get((device, network))
    if not candidates:
        return None
    plans = RETAIL_BY_DEVICE.get(device, ())
    idx = plans.index(retail_plan) if retail_plan in plans else 0
    return candidates[idx % len(candidates)]


def _currents_for_device(device, plan_overrides, log):
    """Ordered list of assumed current plans to build combos from for one device.

    With no override: every plan of the device's family, the assumed current plan first, so the
    rule's two answers cover the whole family. With an override: only the requested plans that are
    LEGAL for this device; a conflicting request is resolved through the family rule and logged
    (Requirement 12.5 groundwork).
    """
    plans = RETAIL_BY_DEVICE.get(device, ())
    if not plans:
        return []

    if not plan_overrides:
        primary = CURRENT_PLAN_BY_DEVICE.get(device)
        ordered = ([primary] if primary in plans else []) + [p for p in plans if p != primary]
        return ordered

    wanted, rejected = [], []
    for raw in plan_overrides:
        code = _canonical_plan(raw)
        if not code:
            continue
        (wanted if code in plans else rejected).append(code)

    for code in rejected:
        log('%s excluded %s + %s: %s' % (LOG_TAG, device, code,
                                         _mismatch_reason(device, code)))

    if not wanted:
        # Every requested plan conflicts with this device. Device wins; the plan is resolved
        # through the family rule and the substitution is logged.
        substitute = _resolve_retail(CURRENT_PLAN_BY_DEVICE.get(device, ''), device)
        log('%s plan %s invalid for %s -> using %s' % (
            LOG_TAG, ', '.join(rejected) or '?', device, substitute))
        wanted = [substitute]

    # Deduplicate while preserving request order, then turn each requested TARGET into the
    # current plan that produces it.
    seen, currents = set(), []
    for target in wanted:
        if target in seen:
            continue
        seen.add(target)
        currents.append(_current_for_target(device, target))
    return currents


def _mismatch_reason(device, plan):
    """Human-readable reason a device/plan pair is rejected (logged, Requirement 3.5)."""
    legal = RETAIL_BY_DEVICE.get(device)
    if not legal:
        return 'no retail plans are defined for device "%s"' % device
    if device == 'Wearable':
        return ('Wearable accepts only %s (no base/Plus pair); %s is %s-family'
                % (WEARABLE_PLAN, plan, plan_family(plan)))
    return ('%s requires a %s-family retail plan (%s); %s is %s-family'
            % (device, plan_family(legal[0]), ', '.join(legal), plan, plan_family(plan)))


def plan_combinations(devices=None, networks=None, sims=None,
                      plan_overrides=None, log=print):
    """The CONSTRAINED cross: device -> network -> SIM, each device taking ONLY its family plans.

    An invalid device/plan pair is never CONSTRUCTED, so nothing has to be generated and then
    removed (Requirement 3.1). `retail_plan` always comes from the family-rule facade via
    `_resolve_retail`, never from a literal.

    devices / networks / sims: optional iterables of any spelling; canonicalised here. Default to
        the three canonical Device_Class values, both NETWORKS and both SIM_TYPES.
    plan_overrides: optional retail plan codes to restrict to. A code that conflicts with a device
        is resolved through the family rule and the substitution is logged.

    Returns list[PlanCombo], de-duplicated by key, in generation order.
    """
    log = log or (lambda *_a, **_k: None)

    raw_devices = list(devices) if devices else list(CANONICAL_DEVICES)
    canon_devices = []
    for d in raw_devices:
        canon = canonical_device(d, log)
        if canon and canon not in canon_devices:
            canon_devices.append(canon)

    nets = [canonical_network(n) for n in (networks or NETWORKS)]
    nets = [n for n in dict.fromkeys(nets) if n]
    sim_list = [canonical_sim(s) for s in (sims or SIM_TYPES)]
    sim_list = [s for s in dict.fromkeys(sim_list) if s]

    combos, seen_keys = [], set()
    for device in canon_devices:
        if device not in RETAIL_BY_DEVICE:
            log('%s no retail plans for device "%s" - skipped' % (LOG_TAG, device))
            continue
        currents = _currents_for_device(device, plan_overrides, log)
        if not currents:
            continue
        for network in nets:
            for sim in sim_list:
                for current in currents:
                    retail = _resolve_retail(current, device)
                    if retail not in RETAIL_BY_DEVICE[device]:
                        # Defensive: only reachable if the shared rule drifts from the table.
                        log('%s excluded %s + %s: %s' % (LOG_TAG, device, retail,
                                                         _mismatch_reason(device, retail)))
                        continue
                    wholesale = _wholesale_for(device, network, retail)
                    if not wholesale:
                        log('%s no wholesale plan for %s/%s - combination skipped'
                            % (LOG_TAG, device, network))
                        continue
                    combo = PlanCombo(device=device, network=network, sim=sim,
                                      current_plan=current, retail_plan=retail,
                                      wholesale_plan=wholesale)
                    if combo.key in seen_keys:
                        continue
                    seen_keys.add(combo.key)
                    combos.append(combo)

    log('%s %d combination(s): %d device(s) x %d network(s) x %d SIM(s)'
        % (LOG_TAG, len(combos), len(canon_devices), len(nets), len(sim_list)))
    return combos


def excluded_pairs(devices, plans, log=print):
    """(device, plan, reason) for every device/plan pair rejected by the family rule.

    Called when the caller supplies explicit plan values, so exclusions are visible in the log
    rather than silently absent from the suite (Requirement 3.5).
    """
    log = log or (lambda *_a, **_k: None)
    out = []
    for raw_device in (devices or []):
        device = canonical_device(raw_device, log)
        if not device:
            continue
        legal = RETAIL_BY_DEVICE.get(device, ())
        for raw_plan in (plans or []):
            plan = _canonical_plan(raw_plan)
            if not plan or plan in legal:
                continue
            reason = _mismatch_reason(device, plan)
            log('%s excluded %s + %s: %s' % (LOG_TAG, device, plan, reason))
            out.append((device, plan, reason))
    return out


# ── User-facing labels ────────────────────────────────────────────────────────
# Requirement 10.4: a watch is RENDERED as 'Watch' while the INTERNAL Device_Class stays
# 'Wearable'. Only display paths (precondition text, TC names, the Combinations sheet Device
# cell) call display_device(); dimension_values / suite.combinations keep the canonical value.
DEVICE_LABELS = {'Wearable': 'Watch'}


def display_device(device):
    """User-facing label for a Device_Class. 'Wearable' -> 'Watch'; everything else unchanged.

    The incoming spelling is canonicalised only to DECIDE whether it is a watch, so
    'Smartwatch' / 'watch' also render as 'Watch'. Any other value is returned exactly as it
    arrived (no case folding, no canonical substitution) - callers that want the canonical form
    should use canonical_device().
    """
    raw = device or ''
    label = DEVICE_LABELS.get(canonical_device(raw))
    return label if label else raw


def _is_wearable(device):
    return canonical_device(device) == 'Wearable'


# ── Preconditions ─────────────────────────────────────────────────────────────
# Requirement 5.2 mandates this sentence VERBATIM - do not reword, reorder or punctuate it.
# The three slots are (current plan, family, family).
FAMILY_STICKINESS_TEMPLATE = ('Line is provisioned on %s (%s family); target plan must stay in '
                              'the %s family regardless of device type')

# Same default the expander falls back to when a base TC carries no preconditions
# (combinatorial_expander._specialise_preconditions).
_DEFAULT_ACTIVE_LINE = 'User must have an active TMO line.'

# Mirrors the expander's regex and substitution so an existing active-line precondition becomes
# device-specific in exactly the same way ('active TMO Watch line').
_ACTIVE_LINE_RE = re.compile(r'active (?:tmo )?line', re.I)

# The expander strips a leading '1. ' / '1) ' before renumbering; reuse the identical pattern.
_PRE_NUMBER_RE = re.compile(r'^\s*\d+[\.\)]\s*')


def _renumber(lines):
    """Strip any existing numbering and renumber 1..n - the expander's exact behaviour.

    combinatorial_expander._specialise_preconditions ends with the same strip-then-'%d. %s'
    loop, so plan-matrix preconditions are indistinguishable in style from variant ones.
    """
    out = []
    for i, ln in enumerate([l for l in lines if str(l).strip()], 1):
        out.append('%d. %s' % (i, _PRE_NUMBER_RE.sub('', str(ln)).strip()))
    return '\n'.join(out)


def compose_preconditions(base_pre, combo):
    """Preconditions for one plan-matrix combination (Requirements 5.1-5.4).

    Layout, top to bottom:
      1. the verbatim Requirement 5.2 family-stickiness sentence, which also carries the assumed
         current retail plan and its family (Requirement 5.1) - PREPENDED, so the executor reads
         the starting state first;
      2. the base TC's own preconditions, with an active-line line made device-specific the way
         the expander does it;
      3. the combination facts - Device_Class label, network, SIM type, wholesale plan and the
         target retail plan (Requirement 5.3);
      4. for a watch only, the no-base/Plus-pair note (Requirement 5.4).

    The whole list is renumbered contiguously by `_renumber`. An empty `base_pre` falls back to
    the expander's default active-line precondition rather than producing a gap.

    The device is rendered through display_device() ('Watch'), because preconditions are
    user-facing text; `combo.device` itself stays 'Wearable' (Requirement 10.4).
    """
    current = combo.current_plan
    family = plan_family(current)
    label = display_device(combo.device)

    lines = [FAMILY_STICKINESS_TEMPLATE % (current, family, family)]

    base_lines = [ln for ln in str(base_pre or '').split('\n') if ln.strip()] or \
                 [_DEFAULT_ACTIVE_LINE]
    for ln in base_lines:
        if _ACTIVE_LINE_RE.search(ln):
            ln = _ACTIVE_LINE_RE.sub('active TMO %s line' % label, ln)
        lines.append(ln)

    lines.append('Device type = %s; network = %s; SIM type = %s.'
                 % (label, combo.network, combo.sim))
    lines.append('Wholesale plan = %s; target retail plan = %s.'
                 % (combo.wholesale_plan, combo.retail_plan))
    if _is_wearable(combo.device):
        lines.append('The %s watch plan has no base/Plus pair, so there is no Plus variant to '
                     'toggle to.' % WEARABLE_PLAN)
    return _renumber(lines)


# ── ERR07 negatives ───────────────────────────────────────────────────────────
# Naming markers for the ERR07 cases. build_err07_cases() SETS THESE ITSELF on every case it
# returns, so the cases arrive naming-ready and flow through the expander's
# _finalise_names_and_numbers() unchanged. Task 4.3 must therefore NOT set _variant_desc /
# _variant_slug again - it only appends the returned list. (Re-setting them to these same values
# would be harmless, but there is exactly one owner of the values: this module.)
ERR07_VARIANT_DESC = 'Invalid_RetailPlan_wrong_family'
ERR07_SLUG_TEMPLATE = 'ERR07_%s'


def _make_tc_factory():
    """The TestCase constructor, resolved lazily - mirrors the expander's _make_step_factory.

    plan_matrix stays decoupled from the data model: steps arrive via the injected `make_step`
    callable, and the TC container is imported here only when ERR07 cases are actually built. A
    minimal stand-in is used when the import fails, so a changed data-model shape degrades
    instead of crashing generation.
    """
    try:
        try:
            from .data_models_v8 import TestCase
        except Exception:
            from data_models_v8 import TestCase                      # noqa: F401
        return TestCase
    except Exception:                                                # pragma: no cover
        class _TC(object):
            def __init__(self, **kw):
                self.sno = ''
                self.summary = ''
                self.description = ''
                self.preconditions = ''
                self.steps = []
                self.story_linkage = ''
                self.label = ''
                self.category = 'Happy Path'
                self.priority = 'P2'
                self.dimension_values = {}
                for k, v in kw.items():
                    setattr(self, k, v)
        return _TC


def _base_plan_for_family(family):
    """(device, base retail plan) for a plan family, read off CURRENT_PLAN_BY_DEVICE.

    Derived from the tables rather than hard-coded, so 'CM' -> ('Phone', 'CMUNL') and
    'CT' -> ('Tablet', 'CTUNL') stay correct if the tables move. Deterministic: dict order.
    """
    for device, plan in CURRENT_PLAN_BY_DEVICE.items():
        if plan_family(plan) == family:
            return device, plan
    return None, None


def _other_family(family, families):
    """The family whose plan is submitted to trigger ERR07 - the first OTHER family available."""
    for other in list(families or ()) + list(PLAN_FAMILIES):
        if other != family:
            return other
    return family


def build_err07_cases(feature_id, make_step, families=PLAN_FAMILIES, log=print):
    """One negative TC per plan family: submit a wrong-family plan, expect ERR07.

    Requirements 4.1-4.5: the CM case submits a CT-family plan to a line whose current plan is
    CM, the CT case does the mirror, every case's expected result carries ERR07_MESSAGE
    ('Invalid RetailPlan for a line'), and every case is `category='Negative'` so the dashboard's
    'Negative scenarios' option gates it.

    `make_step` is the expander's injected step factory, called as `make_step(n, summary,
    expected)`; step numbers are re-assigned later by _finalise_names_and_numbers().

    Each returned case already carries `_variant_desc` = ERR07_VARIANT_DESC and `_variant_slug`
    = 'ERR07_<family>' (see the constants above) - the CALLER MUST NOT SET THEM AGAIN. It also
    carries a provisional `summary` so the object is readable on its own; the expander overwrites
    it during finalisation.

    ERR07 cases deliberately carry NO `_variant_combo`: they are not a device x network x SIM
    combination and must not become a `suite.combinations` row (which would render with an empty
    Wholesale Plan cell). Combination population should skip TCs without `_variant_combo`.
    """
    log = log or (lambda *_a, **_k: None)
    fid = feature_id or 'TC'
    TestCase = _make_tc_factory()
    cases = []

    for family in list(families or ()):
        device, current = _base_plan_for_family(family)
        other = _other_family(family, families)
        _other_device, wrong = _base_plan_for_family(other)
        if not current or not wrong or current == wrong:
            log('%s ERR07 skipped for family %s: no wrong-family plan available'
                % (LOG_TAG, family))
            continue

        label = display_device(device)
        slug = ERR07_SLUG_TEMPLATE % family
        pre = _renumber([
            FAMILY_STICKINESS_TEMPLATE % (current, family, family),
            'Device type = %s; the line is active and eligible for a plan change.' % label,
            'Retail plan %s (%s family) is used deliberately to violate the family rule.'
            % (wrong, other),
        ])
        steps = [
            make_step(1, 'Confirm the line is provisioned on %s (%s family).' % (current, family),
                      'Line reports %s as its current retail plan.' % current),
            make_step(2, 'Submit the plan change with RetailPlan = %s (%s family) for the %s line.'
                      % (wrong, other, family),
                      'Request is rejected with ERR07 - %s.' % ERR07_MESSAGE),
            make_step(3, 'Re-read the line after the rejection.',
                      'Line still reports %s; no plan change is applied.' % current),
        ]

        tc = TestCase(
            summary='%s_%s_%s' % (fid, slug, ERR07_VARIANT_DESC),
            description=('Negative: submit a %s-family retail plan (%s) to a line whose current '
                         'plan is %s (%s family) and confirm the rejection "%s".'
                         % (other, wrong, current, family, ERR07_MESSAGE)),
            preconditions=pre,
            steps=steps,
            story_linkage=fid,
            label=fid,
            category='Negative',
        )
        tc.priority = 'P2'
        tc.dimension_values = {'device': device, 'current_plan': current, 'retail_plan': wrong}
        tc._variant_desc = ERR07_VARIANT_DESC
        tc._variant_slug = slug
        cases.append(tc)
        log('%s ERR07 %s family: submit %s (%s) to a %s line -> "%s"'
            % (LOG_TAG, family, wrong, other, current, ERR07_MESSAGE))

    return cases


# ── Budget trimming ───────────────────────────────────────────────────────────

def _collapse(combos, attr, preference):
    """Keep one value of `attr` across all combos, never emptying a Device_Class.

    The kept value is the first member of `preference` present in the input (so eSIM before
    pSIM, 5G before 4G), which makes the reduction deterministic. A device that has no combo
    with the chosen value keeps its FIRST combo instead, which is what "at least one combo per
    Device_Class" means in practice. Generation order is preserved.
    """
    values = []
    for c in combos:
        v = getattr(c, attr, '')
        if v not in values:
            values.append(v)
    chosen = next((p for p in preference if p in values), values[0] if values else '')

    keep, by_device = set(), {}
    for i, c in enumerate(combos):
        by_device.setdefault(getattr(c, 'device', ''), []).append(i)
        if getattr(c, attr, '') == chosen:
            keep.add(i)
    for idxs in by_device.values():
        if not any(i in keep for i in idxs):
            keep.add(idxs[0])
    return [c for i, c in enumerate(combos) if i in keep], chosen


def _one_per_device(combos):
    """First combo of each Device_Class, in generation order - the reduction floor."""
    keep, seen = [], set()
    for c in combos:
        dev = getattr(c, 'device', '')
        if dev in seen:
            continue
        seen.add(dev)
        keep.append(c)
    return keep


def trim_to_budget(combos, budget, log=print):
    """Reduce the plan axis to `budget` values. Returns (kept, reduction_notes).

    Requirement 8.1: the plan matrix has no cap control of its own, so when the axis alone would
    blow the existing TC_Cap it is trimmed here instead of being dropped whole.

    Reduction order - least discriminating sub-dimension first, stopping as soon as the budget
    fits:
      1. SIM      - collapse to one SIM type (eSIM preferred);
      2. network  - collapse to one network (5G preferred);
      3. plans    - one retail plan per device, i.e. no base AND Plus of the same family.
    Every stage keeps at least one combination per Device_Class, so stage 3 lands exactly on the
    one-per-device floor. If that floor still exceeds the budget it is returned anyway with a
    note: a non-empty input never yields an empty result, because losing a Device_Class entirely
    is worse than overshooting a cosmetic cap.

    Deterministic - same input always yields the same output and the same notes. Notes are
    human-readable and go straight into `suite.combination_plan.reduction_notes`.
    """
    log = log or (lambda *_a, **_k: None)
    kept = list(combos or [])
    notes = []
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = 0

    if not kept or len(kept) <= budget:
        return kept, notes

    for attr, label, preference in (('sim', 'SIM', SIM_TYPES),
                                    ('network', 'network', NETWORKS)):
        if len(kept) <= budget:
            break
        before = len(kept)
        reduced, chosen = _collapse(kept, attr, preference)
        if len(reduced) < before:
            kept = reduced
            notes.append('plan matrix: collapsed %s axis to %s to fit budget %d (%d -> %d)'
                         % (label, chosen, budget, before, len(kept)))

    if len(kept) > budget:
        before = len(kept)
        reduced = _one_per_device(kept)
        if len(reduced) < before:
            kept = reduced
            notes.append('plan matrix: kept one retail plan per device family to fit budget %d '
                         '(%d -> %d)' % (budget, before, len(kept)))

    if len(kept) > budget:
        notes.append('plan matrix: budget %d is below the one-combination-per-device floor of %d '
                     '- keeping %d to preserve every Device_Class'
                     % (budget, len(kept), len(kept)))

    for note in notes:
        log('%s %s' % (LOG_TAG, note))
    return kept, notes
