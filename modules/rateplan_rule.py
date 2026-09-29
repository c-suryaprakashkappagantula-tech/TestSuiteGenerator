# -*- coding: utf-8 -*-
"""
rateplan_rule.py - Facade for the rate-plan family rule. IMPORT THIS, NOTHING ELSE.

The authoritative rule DOCUMENT is `.kiro/steering/rateplan-rules.md`. The canonical
IMPLEMENTATION is
`shared/lifecycle_validator/orchestrator.py::LifecycleOrchestrator._compute_new_rateplan`,
reached at runtime through a sibling-folder path. `modules/rateplan_rule_local.py` is a
VENDORED MIRROR of that implementation, used when the parent `shared/` folder is absent
(TSG cloned ALONE to a UAT/VDI/Docker box).

Every TSG caller (plan_matrix, custom_scenario_builder, ...) imports `compute_new_rateplan`
from THIS module and never from the shared module or the vendored copy directly, so the
resolution decision is made in exactly one place.

RESOLUTION ORDER (decided ONCE at import, logged ONCE, never raises):
  1. env var TSG_RATEPLAN_RULE == 'local'   -> vendored copy (UAT/VDI escape hatch)
  2. shared.lifecycle_validator.orchestrator.LifecycleOrchestrator._compute_new_rateplan
  3. .rateplan_rule_local.compute_new_rateplan

Structure and the sibling-folder `sys.path` technique follow the `modules/pi_registry_local.py`
precedent (_HERE -> _TSG_ROOT -> _MONO_ROOT). The shared module pulls in heavier deps
(playwright/requests/...), which on a VDI box may be missing or broken, so step 2 catches
Exception - not just ImportError. If every step fails the vendored function is used, so
`compute_new_rateplan` is always callable and import never raises.

`RULE_SOURCE` exposes which source won ('shared' or 'vendored') for tests and diagnostics.

THE RULE (see `.kiro/steering/rateplan-rules.md` for the full statement):
  Mobile/Phone  ->  CMUNL  <->  CMUNLP     (CM* plans)
  Tablet        ->  CTUNL  <->  CTUNLP     (CT* plans)
The LINE's current plan family is authoritative; device type is only the fallback.
"""
import os
import sys

__all__ = ['compute_new_rateplan', 'RULE_SOURCE', 'rule_source']

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../TestSuiteGenerator/modules
_TSG_ROOT = os.path.abspath(os.path.join(_HERE, '..'))      # .../TestSuiteGenerator
_MONO_ROOT = os.path.abspath(os.path.join(_TSG_ROOT, '..')) # .../mcp-jira-server (maybe absent)

_ENV_VAR = 'TSG_RATEPLAN_RULE'

SOURCE_SHARED = 'shared'
SOURCE_VENDORED = 'vendored'


def _load_vendored():
    """The vendored mirror. Relative import first (modules is a package); absolute
    fallback for the scripts that put `modules/` itself on sys.path."""
    try:
        from .rateplan_rule_local import compute_new_rateplan as _fn
    except Exception:
        from rateplan_rule_local import compute_new_rateplan as _fn   # noqa: F401
    return _fn


def _load_shared():
    """The canonical implementation from the monorepo's sibling `shared/` folder.

    Same sys.path technique as pi_registry_local.py: the monorepo root is the parent of
    the TSG repo root, so inserting it makes `shared.*` importable. Only inserted when the
    folder actually exists, so a standalone clone does not pollute sys.path.
    """
    shared_dir = os.path.join(_MONO_ROOT, 'shared')
    if not os.path.isdir(shared_dir):
        raise ImportError('shared folder not present at %s' % shared_dir)
    if _MONO_ROOT not in sys.path:
        sys.path.insert(0, _MONO_ROOT)
    from shared.lifecycle_validator.orchestrator import LifecycleOrchestrator
    fn = LifecycleOrchestrator._compute_new_rateplan
    if not callable(fn):
        raise ImportError('LifecycleOrchestrator._compute_new_rateplan is not callable')
    return fn


def _resolve_rule():
    """Return (callable, source). Never raises: the vendored copy is the floor."""
    forced = os.environ.get(_ENV_VAR, '').strip().lower()
    if forced != 'local':
        try:
            return _load_shared(), SOURCE_SHARED
        except Exception:
            # Shared module missing OR its own heavy deps missing/broken -> vendored.
            pass
    try:
        return _load_vendored(), SOURCE_VENDORED
    except Exception:
        pass
    # Last resort: both loaders failed (should be impossible - the vendored copy ships
    # beside this file). Keep the contract "always callable" with an inline mirror of the
    # rule rather than letting an import error escape into generation.
    def _inline(current, device_type='Phone'):
        cur = (current or '').upper().strip()
        if cur.startswith('CT'):
            base = 'CT'
        elif cur.startswith('CM'):
            base = 'CM'
        else:
            base = 'CT' if (device_type or '').upper().startswith('TAB') else 'CM'
        std, plus = base + 'UNL', base + 'UNLP'
        if cur == plus:
            return std
        if cur == std:
            return plus
        return plus
    return _inline, SOURCE_VENDORED


_RULE_FN, RULE_SOURCE = _resolve_rule()

try:
    print('[RATEPLAN] rule source = %s' % RULE_SOURCE)
except Exception:
    pass


def compute_new_rateplan(current, device_type='Phone'):
    """Target retail plan for a line, given its current plan and device type.

    Delegates to the resolved rule (shared implementation when the monorepo `shared/`
    folder is reachable, vendored mirror otherwise). Never raises on a resolution
    problem - resolution already happened at import.
    """
    return _RULE_FN(current, device_type)


def rule_source():
    """'shared' or 'vendored' - which implementation this process resolved to."""
    return RULE_SOURCE
