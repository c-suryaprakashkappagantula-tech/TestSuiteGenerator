# -*- coding: utf-8 -*-
"""
custom_scenario_builder.py - Turn free-text custom instructions into extra scenarios.

This module was referenced by data_first_engine._apply_custom_instructions
(`from .custom_scenario_builder import build_custom_scenarios`) but was never
shipped, so every run with a custom instruction logged a DEGRADED run
("No module named 'modules.custom_scenario_builder'") and silently ignored the
instruction. This implementation restores that path.

WHAT IT DOES (conservative, grounded, never raises):
  1. Recognises device / SIM / channel / network keywords in the instruction
     (e.g. "include Tablet & Smart watch combinations") and returns them as
     axis_overrides via the returned scenarios' tags, AND emits one extra
     ExtractedScenario per requested device so the suite visibly covers it.
  2. Parses explicit "Add:/Include:/Also:" lines into single scenarios.
  3. Everything is tagged user_requested=True so downstream grounding/dedup
     does not prune it.

It returns a list of scenario objects compatible with dimension_set.scenarios.
If the V8 scenario model can't be imported, it returns [] (fail-safe).
"""
import re
from typing import List, Optional

# ── Device keywords: DERIVED, not hand-maintained (Requirement 10.3) ─────────
# The SINGLE device vocabulary is combinatorial_expander's KNOWN_DEVICES +
# DEVICE_ALIASES. _device_keywords() derives the keyword map from those two on
# FIRST USE and merges the literal dict below on top only for the spellings that
# vocabulary does not carry ("smart watch" with a space, "iot").
#
# WHY LAZY: importing combinatorial_expander pulls in plan_matrix -> rateplan_rule,
# whose module body resolves the shared `shared/lifecycle_validator` rule (heavy
# deps) and prints "[RATEPLAN] rule source = ...". A plain
# `import modules.custom_scenario_builder` must stay silent and side-effect free,
# so every plan/device import in this module happens inside a function and is
# cached in a module global afterwards.
#
# FALLBACK ONLY: used verbatim when that lazy import fails, so a changed import
# shape degrades to today's behaviour instead of losing device recognition.
_FALLBACK_DEVICE_KEYWORDS = {
    'tablet': 'Tablet',
    'tab': 'Tablet',
    'phone': 'Phone',
    'handset': 'Phone',
    'smartwatch': 'Wearable',
    'smart watch': 'Wearable',
    'watch': 'Wearable',
    'wearable': 'Wearable',
    'hotspot': 'Hotspot',
    'iot': 'IoT',
}

# Spellings the expander vocabulary cannot produce (multi-word, or a device class
# outside KNOWN_DEVICES). Merged INTO the derived map - never replacing it.
_EXTRA_DEVICE_KEYWORDS = {
    'smart watch': 'Wearable',
    'iot': 'IoT',
}

_SIM_KEYWORDS = {'esim': 'eSIM', 'psim': 'pSIM', 'physical sim': 'pSIM'}
_CHANNEL_KEYWORDS = {'nbop': 'NBOP', 'itmbo': 'ITMBO', 'websheet': 'WEBSHEET'}
_STATE_KEYWORDS = {'yl': 'YL', 'yd': 'YD', 'yp': 'YP', 'py': 'PY', 'pn': 'PN'}

# Retail plan keyword -> canonical plan code (Requirement 12.2). `_found` anchors
# each keyword with \b, so 'cmunl' does NOT match inside 'cmunlp'.
_PLAN_KEYWORDS = {
    'cmunl': 'CMUNL',
    'cmunlp': 'CMUNLP',
    'ctunl': 'CTUNL',
    'ctunlp': 'CTUNLP',
    'mswap': 'MSWAP',
}

_DEVICE_KEYWORDS_CACHE = None
_PLAN_VOCAB_CACHE = None


def _device_keywords(log=None):
    """Keyword -> canonical Device_Class, derived on first use and cached.

    Build order matters: KNOWN_DEVICES first (lower-cased name -> canonical name),
    then DEVICE_ALIASES on top - KNOWN_DEVICES holds BOTH 'Smartwatch' and
    'Wearable', and the alias 'smartwatch' -> 'Wearable' must win (Requirement
    10.2). _EXTRA_DEVICE_KEYWORDS is merged last for the spellings neither table
    can express. Never raises: any failure yields the literal fallback.
    """
    global _DEVICE_KEYWORDS_CACHE
    if _DEVICE_KEYWORDS_CACHE is not None:
        return _DEVICE_KEYWORDS_CACHE
    try:
        try:
            from .combinatorial_expander import KNOWN_DEVICES, DEVICE_ALIASES
        except Exception:
            from combinatorial_expander import KNOWN_DEVICES, DEVICE_ALIASES  # noqa: F401
        derived = {}
        for name in KNOWN_DEVICES:
            key = str(name).strip().lower()
            if key:
                derived[key] = name
        for alias, canon in DEVICE_ALIASES.items():
            key = str(alias).strip().lower()
            if key:
                derived[key] = canon
        derived.update(_EXTRA_DEVICE_KEYWORDS)
        if not derived:
            raise ValueError('empty device vocabulary')
        _DEVICE_KEYWORDS_CACHE = derived
    except Exception as exc:
        if log:
            try:
                log('[V8-CUSTOM]   device vocabulary import failed (%s) - using the '
                    'fallback keyword map' % str(exc)[:80])
            except Exception:
                pass
        _DEVICE_KEYWORDS_CACHE = dict(_FALLBACK_DEVICE_KEYWORDS)
    return _DEVICE_KEYWORDS_CACHE


def _plan_vocab(log=None):
    """(RETAIL_BY_DEVICE, CURRENT_PLAN_BY_DEVICE, canonical_device, compute_new_rateplan).

    Imported lazily for the same reason as the device vocabulary. Returns None when
    unavailable, in which case the caller SKIPS conflict resolution and passes the
    requested plans through untouched rather than erroring.
    """
    global _PLAN_VOCAB_CACHE
    if _PLAN_VOCAB_CACHE is not None:
        return _PLAN_VOCAB_CACHE or None
    try:
        try:
            from .plan_matrix import (RETAIL_BY_DEVICE, CURRENT_PLAN_BY_DEVICE,
                                      canonical_device)
            from .rateplan_rule import compute_new_rateplan
        except Exception:
            from plan_matrix import (RETAIL_BY_DEVICE, CURRENT_PLAN_BY_DEVICE,
                                     canonical_device)                   # noqa: F401
            from rateplan_rule import compute_new_rateplan               # noqa: F401
        _PLAN_VOCAB_CACHE = (RETAIL_BY_DEVICE, CURRENT_PLAN_BY_DEVICE,
                             canonical_device, compute_new_rateplan)
    except Exception as exc:
        if log:
            try:
                log('[V8-CUSTOM]   plan vocabulary unavailable (%s) - plan/device '
                    'conflict resolution skipped' % str(exc)[:80])
            except Exception:
                pass
        _PLAN_VOCAB_CACHE = ()
    return _PLAN_VOCAB_CACHE or None


def _new_scenario(title, description='', category='Happy Path', tags=None):
    """Build an ExtractedScenario for the V8 model; tolerant of signature drift."""
    try:
        from .data_models_v8 import ExtractedScenario
    except Exception:
        try:
            from .dimension_extractor import ExtractedScenario  # alt location
        except Exception:
            return None
    obj = None
    # Try the common constructors in order.
    for kwargs in (
        dict(title=title, description=description, category=category),
        dict(title=title, description=description),
        dict(title=title),
    ):
        try:
            obj = ExtractedScenario(**kwargs)
            break
        except Exception:
            continue
    if obj is None:
        return None
    # Best-effort attribute stamping (only if the attrs exist / are settable).
    for attr, val in (('category', category), ('user_requested', True),
                      ('source_type', 'Custom Instruction'),
                      ('description', description)):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    if tags:
        try:
            setattr(obj, 'tags', list(tags))
        except Exception:
            pass
    return obj


def _found(text, mapping):
    """Return the set of canonical values whose keyword appears in text."""
    out = []
    for kw, canon in mapping.items():
        if re.search(r'\b%s\b' % re.escape(kw), text):
            if canon not in out:
                out.append(canon)
    return out


def _substitute_plan_for(device, vocab):
    """The legal retail plan to use instead of a conflicting request, or ''.

    Mirrors plan_matrix's own substitution: take the device's assumed CURRENT plan
    and run the family rule over it, so a Tablet lands on CTUNL -> CTUNLP. A
    single-plan family (Wearable -> MSWAP) has no base/Plus pair to toggle, so its
    one legal plan is returned directly - the CM/CT rule does not reach MSWAP.
    """
    retail_by_device, current_by_device, _canon, compute = vocab
    legal = tuple(retail_by_device.get(device, ()))
    if not legal:
        return ''
    if len(legal) < 2:
        return legal[0]
    try:
        resolved = compute(current_by_device.get(device, legal[0]), device)
    except Exception:
        resolved = ''
    resolved = (resolved or '').strip().upper()
    return resolved if resolved in legal else legal[-1]


def _resolve_plan_conflicts(devices, plans, log=None):
    """Device wins; a plan illegal for EVERY requested device is replaced (Req 12.5).

    A plan legal for at least one requested device passes through untouched. A plan
    legal for none is swapped for the first requested device's own family plan and
    the substitution is logged as
    `[V8-CUSTOM] plan <x> invalid for <device> -> using <y>`.

    Never raises: with no plan vocabulary, or no devices requested, the requested
    plans are returned unchanged.
    """
    if not plans or not devices:
        return list(plans or [])
    vocab = _plan_vocab(log)
    if not vocab:
        return list(plans)

    retail_by_device, _current, canonical_device, _compute = vocab
    canon_devices = []
    for raw in devices:
        try:
            canon = canonical_device(raw)
        except Exception:
            canon = None
        if canon and canon in retail_by_device and canon not in canon_devices:
            canon_devices.append(canon)
    if not canon_devices:
        return list(plans)

    out = []
    for plan in plans:
        code = (plan or '').strip().upper()
        if not code:
            continue
        if any(code in tuple(retail_by_device.get(d, ())) for d in canon_devices):
            keep = code
        else:
            device = canon_devices[0]
            keep = _substitute_plan_for(device, vocab)
            if not keep:
                continue
            if log:
                try:
                    log('[V8-CUSTOM] plan %s invalid for %s -> using %s'
                        % (code, device, keep))
                except Exception:
                    pass
        if keep not in out:
            out.append(keep)
    return out


def parse_axis_overrides(custom_text: str, log=print) -> dict:
    """Extract device/sim/channel/state/plan requests from the instruction text.

    Returned dict is suitable to merge into options['axis_overrides'] so the
    combinatorial expander crosses exactly what the user asked for. `plans` feeds
    the plan axis (Requirement 12.2); a plan that conflicts with a requested device
    is resolved through the family rule with the device winning (Requirement 12.5).

    Safe on empty/None input: returns {}.
    """
    t = (custom_text or '').lower()
    ov = {}
    devs = _found(t, _device_keywords(log))
    if devs:
        ov['devices'] = devs
    plans = _found(t, _PLAN_KEYWORDS)
    if plans:
        plans = _resolve_plan_conflicts(devs, plans, log)
        if plans:
            ov['plans'] = plans
    sims = _found(t, _SIM_KEYWORDS)
    if sims:
        ov['sim_types'] = sims
    chs = _found(t, _CHANNEL_KEYWORDS)
    if chs:
        ov['channels'] = chs
    sts = _found(t, _STATE_KEYWORDS)
    if sts:
        ov['states'] = sts
    return ov


def build_custom_scenarios(custom_text: str, jira=None, log=print) -> List:
    """Return extra scenarios derived from the custom instruction text.

    Never raises. Returns [] when nothing is recognised or the model is
    unavailable. The engine appends these to dimension_set.scenarios.
    """
    scenarios = []
    try:
        text = (custom_text or '').strip()
        if not text:
            return []
        tl = text.lower()
        feat = ''
        if jira is not None:
            feat = (getattr(jira, 'summary', '') or getattr(jira, 'key', '') or '')
        feat_short = re.sub(r'^\[.*?\]\s*:?\s*', '', feat).strip() or 'the feature'

        # 1. Device coverage requests -> one explicit scenario per device.
        devices = _found(tl, _device_keywords(log))
        for dev in devices:
            sc = _new_scenario(
                title='Verify %s on a %s device' % (feat_short, dev),
                description=('User-requested device coverage: exercise the primary '
                             'flow on a %s and confirm provisioning + line summary.' % dev),
                category='Happy Path',
                tags=['user_requested', 'device:%s' % dev])
            if sc is not None:
                scenarios.append(sc)

        # 2. Explicit "Add:/Include:/Also:" scenario lines.
        for m in re.findall(r'(?:^|\n)\s*(?:add|include|also)\s*[:\-]\s*(.+)', text,
                            re.IGNORECASE):
            line = m.strip()
            if len(line) > 6:
                sc = _new_scenario(
                    title=line[:120],
                    description='User-requested scenario from custom instructions.',
                    category='Happy Path', tags=['user_requested'])
                if sc is not None:
                    scenarios.append(sc)

        # 3. Negative scenario coverage — when explicitly requested.
        _NEGATIVE_KEYWORDS = ['negative', 'negative scenario', 'include negative',
                               'negative test', 'error', 'invalid', 'boundary']
        _wants_negative = any(kw in tl for kw in _NEGATIVE_KEYWORDS)
        if _wants_negative:
            _neg_types = [
                ('Invalid Input', 'Submit request with invalid/missing required fields'),
                ('Boundary Exceeded', 'Submit request where a field value exceeds defined length/range'),
                ('Unauthorized Access', 'Submit request without valid credentials or permissions'),
            ]
            for _neg_title, _neg_desc in _neg_types:
                sc = _new_scenario(
                    title='Negative: Verify %s handles %s gracefully' % (feat_short, _neg_title.lower()),
                    description=_neg_desc + '. System should return appropriate error code.',
                    category='Negative',
                    tags=['user_requested', 'negative'])
                if sc is not None:
                    scenarios.append(sc)

        # 4. Lifecycle operation scenarios — when matrix/full-coverage requested.
        _MATRIX_KEYWORDS = ['device matrix', 'plan matrix', 'all device', 'all plan',
                             'every device', 'every plan', 'prepare for all', 'prepare test suite for all']
        _wants_matrix = any(kw in tl for kw in _MATRIX_KEYWORDS)
        if _wants_matrix:
            _lifecycle_ops = [
                ('Activation', 'Verify device can be activated end-to-end'),
                ('Deactivation', 'Verify device can be deactivated and line status updates'),
                ('Hotline', 'Verify hotline restriction is applied and removed correctly'),
                ('Suspend', 'Verify line suspension and restoration flow'),
            ]
            for _op_title, _op_desc in _lifecycle_ops:
                sc = _new_scenario(
                    title='Verify %s - %s flow' % (feat_short, _op_title),
                    description=_op_desc + ' Confirm downstream system updates.',
                    category='Happy Path',
                    tags=['user_requested', 'lifecycle:%s' % _op_title.lower()])
                if sc is not None:
                    scenarios.append(sc)

        if scenarios and log:
            try:
                log('[V8-CUSTOM]   Custom builder added %d scenario(s) '
                    '(devices=%s)' % (len(scenarios), devices or 'none'))
            except Exception:
                pass
        return scenarios
    except Exception as exc:
        if log:
            try:
                log('[V8-CUSTOM]   custom_scenario_builder soft-failed: %s' % str(exc)[:100])
            except Exception:
                pass
        return []
