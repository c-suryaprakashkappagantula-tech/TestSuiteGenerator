"""Real-feature behavior tests for Analyst mode (FEAT-002).

Cache-gated and slow: builds real suites from tsg_cache.db via the conftest build_suite
fixture (self-skips when the cache is absent). Verifies the gate's end-to-end behavior on
features whose analyst-matrix generators (D1/D2/A1/A2/A3) actually fire:

  (a) a feature that GAINS scenarios in Analyst mode — every added analyst TC passes
      scenario_is_valid as keep/flag against the feature's own evidence, and the suite has
      0 banned tokens / 0 duplicate summaries;
  (c) a provisioning/NSL feature with Analyst ON that KEEPS at least one analyst TC
      (D1/D2 grounded) AND DROPS-or-FLAGS at least one unsupported analyst TC;
  (d) 0 banned tokens across every built suite in BOTH modes, using the
      _BANNED_TOKENS/_assert_no_banned pattern from tests/test_banned_backstop_cr.py — on
      features that are NOT themselves grounded in those banned concepts.

Feature ids are chosen from the cache: 3985 (gains scenarios, 0 banned both modes) and
4374 (provisioning NSL feature with kept + flagged analyst TCs, 0 banned both modes).
Each test self-skips if its feature is not in the cache.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.analyst_validity import (  # noqa: E402
    is_analyst, build_feature_evidence, scenario_is_valid,
)

pytestmark = [pytest.mark.cache, pytest.mark.slow]

# A feature that gains valid analyst scenarios and is NOT century/service-grouping grounded.
GAIN_FEATURE = 'MWTGPROV-3985'
# A provisioning/NSL feature whose analyst gate keeps AND flags/drops matrix TCs.
PROVISIONING_FEATURE = 'MWTGPROV-4374'

# Zero-tolerance banned tokens (same set as tests/test_banned_backstop_cr.py).
_BANNED_TOKENS = (
    'Service Grouping', 'SERVICE_GROUPING', 'Century Report', 'NSL processes request',
    'Trigger API', 'TRANSACTION_HISTORY', 'LINE_HISTORY', 'NE Portal', 'NBOP_MIG',
)


def _blob(suite):
    out = []
    for tc in suite.test_cases:
        for s in (tc.steps or []):
            out.append('%s %s' % (getattr(s, 'summary', '') or '',
                                  getattr(s, 'expected', '') or ''))
    return '\n'.join(out).lower()


def _assert_no_banned(suite, label=''):
    blob = _blob(suite)
    for token in _BANNED_TOKENS:
        assert token.lower() not in blob, 'banned token leaked (%s): %r' % (label, token)


def _summaries(suite):
    return [tc.summary for tc in suite.test_cases]


def _feature_evidence(build_suite, feature_id):
    """Rebuild the feature's evidence blob from the same cached jira/chalk the engine used."""
    from modules.database import _conn, load_chalk_as_object
    from modules.pipeline import block_jira_fetch
    jira = block_jira_fetch(page=None, feature_id=feature_id, log=lambda *_a: None)['jira']
    if jira is None:
        pytest.skip('%s is not in the cache' % feature_id)
    conn = _conn()
    row = conn.execute(
        "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
        "AND scenarios_json != '[]' LIMIT 1", (feature_id,)).fetchone()
    conn.close()
    pi = row['pi_label'] if row else None
    chalk = load_chalk_as_object(feature_id, pi) if pi else None
    return build_feature_evidence(jira, chalk)


def test_analyst_on_gains_valid_scenarios_no_banned_no_dups(build_suite):
    default = build_suite(GAIN_FEATURE)
    analyst = build_suite(GAIN_FEATURE, {'data_only': False})

    # Gains scenarios.
    assert len(analyst.test_cases) > len(default.test_cases), \
        'Analyst ON should gain scenarios on %s' % GAIN_FEATURE

    # Every analyst TC in the built suite passes scenario_is_valid as keep/flag against
    # the feature's own evidence (drops never survive into the suite).
    evidence = _feature_evidence(build_suite, GAIN_FEATURE)
    analyst_tcs = [tc for tc in analyst.test_cases if is_analyst(tc)]
    assert analyst_tcs, 'expected at least one analyst TC on %s' % GAIN_FEATURE
    for tc in analyst_tcs:
        verdict, _reason = scenario_is_valid(tc, evidence)
        assert verdict in ('keep', 'flag'), \
            'surviving analyst TC %r classified %r' % (tc.summary, verdict)

    # 0 banned tokens and no duplicate TC summaries in the combined set.
    _assert_no_banned(analyst, label=GAIN_FEATURE)
    summaries = _summaries(analyst)
    assert len(summaries) == len(set(summaries)), \
        'duplicate TC summaries after the gate on %s' % GAIN_FEATURE


def test_provisioning_feature_keeps_and_drops_or_flags(build_suite):
    analyst = build_suite(PROVISIONING_FEATURE, {'data_only': False})
    evidence = _feature_evidence(build_suite, PROVISIONING_FEATURE)

    analyst_tcs = [tc for tc in analyst.test_cases if is_analyst(tc)]
    assert analyst_tcs, 'expected analyst TCs on provisioning feature %s' % PROVISIONING_FEATURE

    # At least one analyst TC KEPT (kept TCs are classified keep or flag).
    kept = [tc for tc in analyst_tcs
            if scenario_is_valid(tc, evidence)[0] in ('keep', 'flag')]
    assert kept, 'expected at least one kept analyst TC on %s' % PROVISIONING_FEATURE

    # At least one unsupported analyst scenario DROPPED or FLAGGED. A flag surfaces as
    # tc.needs_review on a surviving TC; a drop removes it from the built suite. Both are
    # evidence the gate rejected unsupported analyst content: assert a flag OR that the
    # engine's analyst-matrix extraction produced more matrix scenarios than survived.
    flagged = [tc for tc in analyst.test_cases if getattr(tc, 'needs_review', False)]
    dropped_some = _analyst_matrix_extracted(build_suite, PROVISIONING_FEATURE) > len(analyst_tcs)
    assert flagged or dropped_some, \
        'gate should drop or flag at least one unsupported analyst TC on %s' % PROVISIONING_FEATURE


def test_zero_banned_both_modes(build_suite):
    for feature_id in (GAIN_FEATURE, PROVISIONING_FEATURE):
        default = build_suite(feature_id)
        analyst = build_suite(feature_id, {'data_only': False})
        _assert_no_banned(default, label='%s default' % feature_id)
        _assert_no_banned(analyst, label='%s analyst' % feature_id)


def _analyst_matrix_extracted(build_suite, feature_id):
    """Count analyst-matrix scenarios the extractor produces for this feature (pre-build),
    so the test can tell whether the gate dropped any before they became TCs."""
    from modules.database import _conn, load_chalk_as_object
    from modules.pipeline import block_jira_fetch
    from modules.deep_miner import deep_mine
    from modules import dimension_extractor as DE

    def q(*_a):
        pass

    jira = block_jira_fetch(page=None, feature_id=feature_id, log=q)['jira']
    if jira is None:
        pytest.skip('%s is not in the cache' % feature_id)
    conn = _conn()
    row = conn.execute(
        "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
        "AND scenarios_json != '[]' LIMIT 1", (feature_id,)).fetchone()
    conn.close()
    pi = row['pi_label'] if row else None
    chalk = load_chalk_as_object(feature_id, pi) if pi else None
    mined = deep_mine(jira, chalk, page=None, log=q)
    ds = DE.extract_dimensions(jira, chalk, mined, log=q, data_only=False)
    prefixes = ('State-Transition-Matrix', 'Partial-Failure-Matrix',
                'Field-Validation-Matrix', 'Idempotency', 'Concurrency')
    n = 0
    for s in ds.scenarios:
        sid = getattr(getattr(s, 'source', None), 'source_id', '') or ''
        if any(sid.startswith(p) for p in prefixes):
            n += 1
    return n
