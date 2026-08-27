# -*- coding: utf-8 -*-
"""
degraded_tracker.py — record generation passes that raised and were skipped.

WHY THIS EXISTS
    TSG has 137 handlers that log an exception and carry on. That is mostly the right
    call: one failed enrichment pass should not lose a tester their whole suite. But the
    run then reports success with less in it than it should have, and the only trace is a
    line in the CLI log that nobody opens. A partial suite is indistinguishable from a
    complete one.

    This module counts those skipped passes so the run can say so
    (tsg-tse-hardening Requirement 5).

WHAT IT DOES NOT DO
    It does not change control flow. Every handler keeps its existing
    continue-on-failure behaviour and its existing log line (Requirement 5.5); this only
    adds a record next to it. Removing every call here would leave generation behaving
    exactly as before.

SCOPE
    Instrumented: the V8 engine's own enrichment passes, and the output-block passes in
    `pipeline.block_generate_output` (auto-diff, scorecard, Feature Summary, DB save).
    NOT instrumented: the V7 engine, which the CR path delegates to. A CR run therefore
    reports degradation only for the passes listed above. Stated here rather than implied,
    so "0 degraded" is not read as a guarantee for a CR suite.

USAGE
    from .degraded_tracker import begin_run, record, attach_to_suite

    begin_run(feature_id)                       # at the start of a generation
    ...
    try:
        _inject_eligibility_negatives(...)
    except Exception as exc:
        log('... continuing')                   # unchanged
        record('eligibility negatives', exc)    # added
    ...
    attach_to_suite(suite)                      # before the suite is written
"""
import threading
from typing import List, Optional, Tuple

_LOCK = threading.Lock()

# The marker that identifies the warning this module owns, so re-attaching replaces
# rather than duplicates it.
_WARNING_PREFIX = 'DEGRADED RUN:'

_state = {
    'feature_id': '',
    'passes': [],          # list of (pass_name, detail)
}


def begin_run(feature_id: str = '') -> None:
    """Start a fresh run. Clears anything recorded previously."""
    with _LOCK:
        _state['feature_id'] = feature_id or ''
        _state['passes'] = []


def reset() -> None:
    """Alias for begin_run with no feature id. Mainly for tests."""
    begin_run('')


def record(pass_name: str, exc: Optional[BaseException] = None,
           detail: str = '') -> None:
    """Record that ``pass_name`` raised and was skipped.

    Never raises: a tracker fault must not become the thing that breaks a run it was
    only supposed to describe.
    """
    try:
        text = detail or (('%s: %s' % (type(exc).__name__, exc)) if exc else '')
        with _LOCK:
            _state['passes'].append((str(pass_name), str(text)[:200]))
    except Exception:
        pass


def passes() -> List[Tuple[str, str]]:
    """Return the recorded (pass_name, detail) pairs, in the order they failed."""
    with _LOCK:
        return list(_state['passes'])


def count() -> int:
    with _LOCK:
        return len(_state['passes'])


def pass_names() -> List[str]:
    """Unique pass names, order preserved."""
    seen, out = set(), []
    for name, _detail in passes():
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def summary_line() -> str:
    """One line naming the count and the passes, or '' when the run was clean.

    Requirement 5.2 wants the count AND the names, and 5.4 wants nothing at all when
    there is nothing to report - hence the empty string rather than "0 degraded passes".
    """
    names = pass_names()
    if not names:
        return ''
    return ('%s %d generation pass(es) failed and were skipped, so this suite may be '
            'incomplete: %s' % (_WARNING_PREFIX, len(names), ', '.join(names)))


def detail_lines() -> List[str]:
    """One line per failure, for a log or an expandable panel."""
    return ['%s — %s' % (name, detail or 'no detail') for name, detail in passes()]


def attach_to_suite(suite) -> int:
    """Put the summary into ``suite.warnings``. Returns the number of degraded passes.

    Idempotent: an existing tracker warning is replaced, so calling this again after more
    passes have failed refreshes the line instead of adding a second one. Other warnings
    are left untouched.
    """
    if suite is None:
        return 0
    line = summary_line()
    try:
        existing = list(getattr(suite, 'warnings', None) or [])
        existing = [w for w in existing if not str(w).startswith(_WARNING_PREFIX)]
        if line:
            existing.insert(0, line)      # first, so it is seen before detail warnings
        suite.warnings = existing
        # Also expose the structured form, so a caller does not have to parse a sentence to
        # decide whether to show a badge.
        suite._degraded_passes = passes()
    except Exception:
        pass

    # `suite.warnings` reaches the Excel Summary sheet, but the dashboard renders
    # `data_inventory.warnings` instead - a different list. Writing to both is what makes
    # the indicator visible in the UI without editing a dashboard file, which matters
    # because there are 13 of them (see task 7) and the current one carries unrelated
    # uncommitted work.
    try:
        inv = getattr(suite, 'data_inventory', None)
        if inv is not None and hasattr(inv, 'warnings'):
            inv_warnings = [w for w in (list(inv.warnings or []))
                            if not str(w).startswith(_WARNING_PREFIX)]
            if line:
                inv_warnings.insert(0, line)
            inv.warnings = inv_warnings
    except Exception:
        pass

    return count()
