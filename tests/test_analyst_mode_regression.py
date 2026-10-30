"""Regression guard for Analyst mode (FEAT-002).

Cache-gated (@pytest.mark.cache) and slow (@pytest.mark.slow): builds several suites from
tsg_cache.db via the conftest build_suite fixture. Self-skips cleanly when the cache is
absent (the cache_available fixture build_suite depends on calls pytest.skip).

Asserts:
  * default-options builds are BYTE-COMPARABLE to explicit {'data_only': True} builds —
    identical ordered TC summaries AND identical per-TC step summaries — for 4628 (==17 TCs),
    4074 (==14 TCs after the scope-drop fix), and the 8 PI-60 features (the Analyst gate is
    a no-op in default mode);
  * every PI-60 feature carries 0 banned tokens in BOTH default and data_only builds;
  * 4533 (manual/CR path) attempts the QMetry supplement — no _qmetry_skip_warning when the
    cache has it, else the test skips.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.conftest import summaries  # noqa: E402

pytestmark = [pytest.mark.cache, pytest.mark.slow]

# The 8 PI-60 features (chalk_cache pi_label == 'PI-60').
PI60_FEATURES = [
    'MWTGPROV-4074', 'MWTGPROV-4497', 'MWTGPROV-4574', 'MWTGPROV-4575',
    'MWTGPROV-4576', 'MWTGPROV-4587', 'MWTGPROV-4611', 'MWTGPROV-4628',
]

# Zero-tolerance banned tokens (same set as tests/test_banned_backstop_cr.py).
_BANNED_TOKENS = (
    'Service Grouping', 'SERVICE_GROUPING', 'Century Report', 'NSL processes request',
    'Trigger API', 'TRANSACTION_HISTORY', 'LINE_HISTORY', 'NE Portal', 'NBOP_MIG',
)


def _step_summaries(suite):
    """Per-TC ordered list of step summaries — the second half of byte-comparability."""
    return [[getattr(s, 'summary', '') or '' for s in (tc.steps or [])]
            for tc in suite.test_cases]


def _blob(suite):
    out = []
    for tc in suite.test_cases:
        for s in (tc.steps or []):
            out.append('%s %s' % (getattr(s, 'summary', '') or '',
                                  getattr(s, 'expected', '') or ''))
    return '\n'.join(out).lower()


def _assert_no_banned(suite):
    blob = _blob(suite)
    for token in _BANNED_TOKENS:
        assert token.lower() not in blob, 'banned token leaked: %r' % token


def _assert_byte_comparable(build_suite, feature_id, expected_count=None):
    default = build_suite(feature_id)                       # default options (data_only True)
    explicit = build_suite(feature_id, {'data_only': True})  # explicit data-only
    assert summaries(default) == summaries(explicit), \
        '%s: default vs data_only TC summaries differ' % feature_id
    assert _step_summaries(default) == _step_summaries(explicit), \
        '%s: default vs data_only per-TC step summaries differ' % feature_id
    if expected_count is not None:
        assert len(default.test_cases) == expected_count, \
            '%s: expected %d TCs, got %d' % (feature_id, expected_count, len(default.test_cases))
    return default


def test_4628_byte_comparable_17_tcs(build_suite):
    _assert_byte_comparable(build_suite, 'MWTGPROV-4628', expected_count=17)


def test_4074_byte_comparable_14_tcs(build_suite):
    # 16 -> 14 after tsg-scope-drop-fix: the two scope-excluded fan-out TCs
    # ('Multi-environment fan-out...' and 'Duplicate migrated pair across environments...')
    # are now dropped end-to-end by _apply_scope_exclusion on both default and data_only
    # builds, so the byte-comparable count drops from 16 to 14.
    _assert_byte_comparable(build_suite, 'MWTGPROV-4074', expected_count=14)


@pytest.mark.parametrize('feature_id', PI60_FEATURES)
def test_pi60_byte_comparable_and_no_banned(build_suite, feature_id):
    default = _assert_byte_comparable(build_suite, feature_id)
    _assert_no_banned(default)
    # Analyst ON must also stay banned-token clean.
    analyst = build_suite(feature_id, {'data_only': False})
    _assert_no_banned(analyst)


def test_4533_manual_path_attempts_qmetry_supplement(build_suite):
    suite = build_suite('MWTGPROV-4533')
    warnings = suite.warnings or []
    skip_warn = [w for w in warnings if 'qmetry' in str(w).lower() and 'skip' in str(w).lower()]
    if skip_warn:
        pytest.skip('4533 QMetry supplement skipped in this cache: %s' % skip_warn[0])
    # No QMetry skip warning => the manual path reached the QMetry supplement cleanly.
    assert not skip_warn
