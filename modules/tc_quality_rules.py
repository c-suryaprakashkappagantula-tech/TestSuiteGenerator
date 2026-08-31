# -*- coding: utf-8 -*-
"""
tc_quality_rules.py — test-case patterns this team does not test, shared by both engines.

WHY THIS EXISTS
    `test_engine._quality_gate` carried a "global suppress" list of patterns described as
    things Charter never tests: auth and token validity at API level, DB-state consistency
    assertions, raw payload-structure checks, idempotency, NANP area-code distinctions. It
    was a local variable inside a 433-line function, so only the V7 engine applied it - and
    V7 only runs for CR and bug tickets.

    Every other feature goes through V8, which had no equivalent. Measured across 45 non-CR
    features and 441 generated test cases, V8 shipped 2 test cases that V7 would have
    suppressed, both of the form "Verify with invalid token of api activation payload
    returns failure". Small in number, but it is a documented decision about what this team
    tests, and it was being applied to 8% of tickets rather than all of them.

WHAT THE SAME MEASUREMENT FOUND ABOUT THE REST OF `_quality_gate`
    Nothing else needed moving. Of its four rejection checks, only this one fired on V8
    output: junk titles 0, generic steps 0, vague titles 0, placeholder steps 0. Of its
    repair conditions, all were 0 - no empty descriptions, no empty preconditions, no
    over-long titles, no doubled verbs, no mostly-generic expected results, no test cases
    with fewer than two steps.

    So the often-cited "V8 has no quality gate" gap is real in structure but almost empty in
    practice: V8's own passes (`step_templates` sanitisers, `_prune_degenerate_tcs`,
    `zero_generic_validator`, and grounded construction in `tc_builder`) already produce
    output that passes V7's checks. That matters for planning - the blockers to retiring V7
    are missing CAPABILITIES (device-matrix expansion, E2E synthesis, mediation negatives,
    open items, comment mining, grouping, AC traceability), not missing quality repair.
"""
import re
from typing import List, Optional

# Patterns this team does not write test cases for. Matched against the test-case title with
# underscores flattened, so both engines' naming conventions are handled.
#
# These are product decisions, not code smells - each says "we do not test this here":
GLOBAL_SUPPRESS_PATTERNS = (
    # Auth and tokens are not exercised at API level.
    'expired auth',
    'invalid.*auth',
    'expired.*token',
    'invalid.*token',
    'authentication',
    # Direct database state is not asserted.
    'db state.*consistent',
    'nsl db state',
    'db consistent',
    # Raw response structure is not asserted.
    'api response payload',
    'response payload structure',
    # Idempotency is not exercised.
    'duplicate request',
    'rejects duplicate',
    'idempoten',
    # Area-code and country-prefix distinctions are not standalone test cases.
    'nanp countries',
    'area code distinction',
    'country.*prefix',
    'only the first.*digits',
)

_COMPILED = tuple(re.compile(p, re.IGNORECASE) for p in GLOBAL_SUPPRESS_PATTERNS)


def _normalize(title: str) -> str:
    """Lower-case and flatten underscores.

    V8 titles are underscore-joined ('Verify_with_invalid_token_of_api'), V7's are
    space-separated. Without flattening, a space-bearing pattern like 'invalid.*token'
    would still match through `.*`, but 'expired auth' would not. Normalising makes the
    rules behave the same for both engines rather than accidentally differently.
    """
    return re.sub(r'[_]+', ' ', (title or '').lower())


def suppression_reason(title: str) -> Optional[str]:
    """Return the pattern that suppresses this title, or None to keep it."""
    text = _normalize(title)
    for pattern in _COMPILED:
        if pattern.search(text):
            return pattern.pattern
    return None


def is_suppressed(title: str) -> bool:
    return suppression_reason(title) is not None


def filter_suppressed(test_cases, log=None, engine: str = '') -> List:
    """Return the test cases worth keeping, logging each suppression with its reason.

    Reports the reason rather than a bare count, so a surprising removal can be traced to
    the rule that caused it instead of looking like an unexplained drop.
    """
    kept = []
    for tc in (test_cases or []):
        reason = suppression_reason(getattr(tc, 'summary', '') or '')
        if reason is None:
            kept.append(tc)
            continue
        if log:
            try:
                log('%s   Suppressed (not tested here: %s): %s'
                    % (engine or '[QUALITY]', reason,
                       (getattr(tc, 'summary', '') or '')[:70]))
            except Exception:
                pass
    return kept
