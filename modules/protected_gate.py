# -*- coding: utf-8 -*-
"""
protected_gate.py — TSG's gate on the shared do-not-touch registry.

WHY THIS EXISTS
    TSG seeds generated suites with realistic subscriber identifiers
    (``modules/test_data_injector.py``). TSE refuses to *execute* against a
    protected identifier, but until this module existed nothing stopped TSG from
    *generating* a suite that carried one — the control sat downstream of where
    the identifier enters the system. A tester reading the suite would dial it.

    This module puts the same check at the two points that matter for generation:

      1. injection  — ``test_data_injector`` asks here before handing a value out
      2. output     — ``excel_generator.generate_excel`` asks here before it
                      opens a workbook, so a violation writes no Excel, no
                      Feature Summary, no DB row and no transaction-log entry

FAIL CLOSED
    If the registry cannot be imported, generation stops. Silently skipping the
    check is precisely the condition the registry exists to prevent, so
    "unavailable" is treated as "unsafe" rather than "assume fine".
    (tsg-tse-hardening Requirement 1.4. Note this is deliberately *stricter*
    than TSE's historical Req 12.5 fail-open behaviour.)

USAGE
    from .protected_gate import assert_identifiers_safe, assert_suite_safe

    assert_identifiers_safe('test_data_injector.get_sample_data', mdn=value)
    assert_suite_safe(suite, context='generate_excel')
"""
import re
import sys
from importlib import import_module
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_REGISTRY_MODULE = 'shared.protected_registry'

# Shortest digit run worth a registry lookup. Deliberately matches the threshold in
# registry._normalize(), which treats any run of 7+ digits as a numeric identifier.
#
# An earlier version of this listed only the canonical shapes
# (MDN 10/11, IMEI+IMSI 15, ICCID 19/20, EID 32) and MISSED ACCOUNT NUMBERS, which TSG
# seeds as 9 digits ('100456789'). Requirement 1.3 names account number explicitly, so
# the rule is now "whatever the registry would treat as an identifier" rather than a
# hand-maintained list of lengths that has to be kept in step with the seed pools.
#
# Wider matching costs only extra lookups, and those are deduplicated per suite. A date
# or an HTTP code that is not in the registry simply returns no hit; the only way a
# non-identifier changes the outcome is if someone registered that exact value, in which
# case refusing to write it is the wanted behaviour.
_MIN_CANDIDATE_DIGITS = 7

_DIGIT_RUN = re.compile(r'\d+')
_SEPARATORS = re.compile(r'[\s\-\(\)\.\+]')

# Text fields on a TestCase / TestSuite that reach the workbook.
_TC_TEXT_FIELDS = ('sno', 'summary', 'description', 'preconditions',
                   'story_linkage', 'label', 'category', 'test_category')
_STEP_TEXT_FIELDS = ('summary', 'expected')
_SUITE_TEXT_FIELDS = ('feature_title', 'feature_desc', 'scope', 'rules')


class ProtectedRegistryUnavailable(RuntimeError):
    """The do-not-touch registry could not be loaded, so nothing can be cleared.

    Raised instead of proceeding unchecked (Requirement 1.4).
    """


def _candidate_roots() -> List[Path]:
    """Directories that might contain the ``shared`` package, nearest first."""
    here = Path(__file__).resolve()
    roots = list(here.parents)
    cwd = Path.cwd().resolve()
    for extra in (cwd, *cwd.parents):
        if extra not in roots:
            roots.append(extra)
    return roots


def _bootstrap_sys_path() -> Optional[Path]:
    """Put the directory holding ``shared/protected_registry`` on ``sys.path``.

    TSG is a sibling of ``shared``, not a child, so ``import shared`` only works
    once the parent project root is importable. Resolved from ``__file__`` rather
    than the cwd so it holds for the dashboard, the CLI and pytest alike.
    """
    for root in _candidate_roots():
        if (root / 'shared' / 'protected_registry' / '__init__.py').is_file():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return root
    return None


def _registry():
    """Return the registry module, or raise ProtectedRegistryUnavailable.

    Deliberately not cached: ``import_module`` already short-circuits through
    ``sys.modules``, and not caching keeps the module honest if the registry
    becomes available (or is substituted in a test) after first use.
    """
    try:
        return import_module(_REGISTRY_MODULE)
    except Exception:
        pass

    root = _bootstrap_sys_path()
    try:
        return import_module(_REGISTRY_MODULE)
    except Exception as exc:
        raise ProtectedRegistryUnavailable(
            'cannot load %s (searched from %s; resolved root=%s): %s: %s. '
            'Generation stops rather than run unchecked — a protected '
            'identifier must not be able to reach a generated suite.'
            % (_REGISTRY_MODULE, Path(__file__).resolve().parent,
               root, type(exc).__name__, exc)
        ) from exc


def registry_available() -> bool:
    """True if the registry can be loaded. For diagnostics/logging only.

    Callers enforcing the policy should call an ``assert_*`` function instead —
    branching on this would reintroduce the fail-open behaviour.
    """
    try:
        _registry()
        return True
    except ProtectedRegistryUnavailable:
        return False


def is_protected(value) -> bool:
    """True if ``value`` is in the do-not-touch registry."""
    if value is None or str(value).strip() == '':
        return False
    return bool(_registry().is_protected(value))


def assert_identifiers_safe(context: str = '', log=None, **identifiers) -> None:
    """Raise if any named identifier is protected.

    Covers MDN, IMEI, ICCID, IMSI, line id and account number (Requirement 1.3);
    matching is value-based in the registry, so the keyword names are for the
    error message rather than for the lookup.
    """
    reg = _registry()
    supplied = {k: v for k, v in identifiers.items()
                if v is not None and str(v).strip() != ''}
    if not supplied:
        return
    reg.assert_safe(context=context or 'TSG', logger_fn=log, **supplied)


def assert_mapping_safe(mapping: Dict[str, object], context: str = '',
                        log=None) -> Dict[str, object]:
    """Raise if any value in ``mapping`` is protected; otherwise return it.

    Takes a dict rather than ``**kwargs`` on purpose: the keys can be arbitrary
    API request field names, and a field called ``context`` or ``logger_fn``
    would collide with the keyword parameters of ``registry.assert_safe``.
    """
    if not mapping:
        return mapping
    reg = _registry()

    hits = []
    seen = set()
    for field_name, value in mapping.items():
        if value is None or str(value).strip() == '':
            continue
        rec = reg.find_protected(value)
        if rec is not None and rec.value_norm not in seen:
            seen.add(rec.value_norm)
            hits.append((str(field_name), rec))

    if not hits:
        return mapping

    detail = ', '.join('%s=%s' % (f, r.value) for f, r in hits)
    if log:
        try:
            log('[PROTECTED] %s: %s' % (context or 'TSG', detail))
        except Exception:
            pass
    raise reg.ProtectedEntityError(
        [r for _f, r in hits], context='%s (%s)' % (context or 'TSG', detail))


def _extract_candidates(text: str) -> List[str]:
    """Pull identifier-shaped digit runs out of free-form generated text.

    Two passes: the text as written, and the text with phone/id punctuation
    removed so ``1-414-581-0607`` is seen as one run. Matching downstream is an
    exact run comparison in the registry, so gluing unrelated digits together in
    the second pass yields a value that is not registered rather than a false hit.
    """
    if not text:
        return []
    out = []
    seen = set()
    for variant in (text, _SEPARATORS.sub('', text)):
        for run in _DIGIT_RUN.findall(variant):
            if len(run) >= _MIN_CANDIDATE_DIGITS and run not in seen:
                seen.add(run)
                out.append(run)
    return out


def _suite_text_parts(suite) -> Iterable[Tuple[str, str]]:
    """Yield ``(where, text)`` for every field of a suite that reaches output."""
    for fname in _SUITE_TEXT_FIELDS:
        val = getattr(suite, fname, '')
        if val:
            yield ('suite.%s' % fname, str(val))

    for lname in ('acceptance_criteria', 'open_items', 'data_sources', 'warnings'):
        for idx, item in enumerate(getattr(suite, lname, None) or []):
            if item:
                yield ('suite.%s[%d]' % (lname, idx), str(item))

    for tc_idx, tc in enumerate(getattr(suite, 'test_cases', None) or []):
        label = getattr(tc, 'sno', '') or 'TC#%d' % (tc_idx + 1)
        for fname in _TC_TEXT_FIELDS:
            val = getattr(tc, fname, '')
            if val:
                yield ('%s.%s' % (label, fname), str(val))
        for dk, dv in (getattr(tc, 'dimension_values', None) or {}).items():
            if dv:
                yield ('%s.dimension_values[%s]' % (label, dk), str(dv))
        for s_idx, step in enumerate(getattr(tc, 'steps', None) or []):
            for fname in _STEP_TEXT_FIELDS:
                val = getattr(step, fname, '')
                if val:
                    yield ('%s.step%d.%s' % (label, s_idx + 1, fname), str(val))


def scan_suite(suite) -> List[Dict[str, str]]:
    """Return every protected identifier found in a built suite.

    Scans the generated text rather than only the seed pools, so a value that
    arrived from the cached ``test_data_pool``, from NMNO captured traffic or
    from a Jira ticket body is caught the same as a hardcoded seed.

    Returns a list of ``{'value', 'where', 'entity_type', 'reason'}``; empty
    means the suite is clear.
    """
    reg = _registry()

    # Unique candidates first: a suite reuses the same handful of identifiers
    # across dozens of steps, so this keeps it to a few registry lookups.
    first_seen: Dict[str, str] = {}
    for where, text in _suite_text_parts(suite):
        for cand in _extract_candidates(text):
            first_seen.setdefault(cand, where)

    violations = []
    for cand, where in first_seen.items():
        rec = reg.find_protected(cand)
        if rec is not None:
            violations.append({
                'value': cand,
                'where': where,
                'entity_type': getattr(rec, 'entity_type', '') or 'OTHER',
                'reason': getattr(rec, 'reason', '') or 'protected',
            })
    return violations


def assert_suite_safe(suite, context: str = 'TSG output', log=None) -> None:
    """Raise ``ProtectedEntityError`` if a built suite carries a protected id.

    Call before writing anything. Raising here means no Excel, no Feature
    Summary, no DB row and no transaction-log entry for the run
    (Requirement 1.2).
    """
    if suite is None:
        return
    violations = scan_suite(suite)
    if not violations:
        return

    reg = _registry()
    detail = '; '.join('%s %s at %s' % (v['entity_type'], v['value'], v['where'])
                       for v in violations)
    if log:
        try:
            log('[PROTECTED] refusing to write %s: %s' % (context, detail))
        except Exception:
            pass

    records = [reg.find_protected(v['value']) for v in violations]
    records = [r for r in records if r is not None]
    raise reg.ProtectedEntityError(
        records, context='%s (%s)' % (context, detail))


def filter_protected(values: Iterable[str]) -> List[str]:
    """Return only the safe values, preserving order."""
    return _registry().filter_safe(list(values))
