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

# Keyword -> canonical device label. "smart watch" (with space) and "watch" both
# map to Wearable, fixing the exact miss in the reported run.
_DEVICE_KEYWORDS = {
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
_SIM_KEYWORDS = {'esim': 'eSIM', 'psim': 'pSIM', 'physical sim': 'pSIM'}
_CHANNEL_KEYWORDS = {'nbop': 'NBOP', 'itmbo': 'ITMBO', 'websheet': 'WEBSHEET'}
_STATE_KEYWORDS = {'yl': 'YL', 'yd': 'YD', 'yp': 'YP', 'py': 'PY', 'pn': 'PN'}


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


def parse_axis_overrides(custom_text: str) -> dict:
    """Extract device/sim/channel/state requests from the instruction text.

    Returned dict is suitable to merge into options['axis_overrides'] so the
    combinatorial expander crosses exactly what the user asked for.
    """
    t = (custom_text or '').lower()
    ov = {}
    devs = _found(t, _DEVICE_KEYWORDS)
    if devs:
        ov['devices'] = devs
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
        devices = _found(tl, _DEVICE_KEYWORDS)
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
