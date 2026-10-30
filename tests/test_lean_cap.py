"""Focused unit tests for the opt-in lean cap (FEAT-004) in data_first_engine.

Pure / cache-free: suites are built in memory (direct-construction style of
tests/test_analyst_validity.py). Covers:
  * max_test_cases=N returns exactly N highest-priority TCs with category balance
    (>= 1 Negative retained when present);
  * a Chalk-grounded core TC is not evicted for a lower-priority analyst/edge TC;
  * max_test_cases=None (and the key absent) returns the full suite unchanged.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.data_models_v8 import TestCase, TestStep, TestSuite  # noqa: E402
from modules.traceability import TraceabilityRecord  # noqa: E402
from modules.data_first_engine import _apply_lean_cap  # noqa: E402

FID = 'MWTGPROV-9074'


class _Jira:
    def __init__(self, priority='High'):
        self.key = FID
        self.priority = priority


def _tr(source_id='chalk_1', source_type='Chalk Scenario'):
    return TraceabilityRecord(source_type=source_type, source_id=source_id,
                              extracted_text='x', confidence=0.9)


def _tc(sno, summary, category='Happy Path', source_type='Chalk Scenario',
        source_id='chalk_1', steps=None):
    tc = TestCase(sno=str(sno), summary=summary, description=summary, category=category,
                  traceability=_tr(source_id=source_id, source_type=source_type))
    tc.steps = [TestStep(step_num=i + 1, summary=s, expected='ok')
                for i, s in enumerate(steps or [summary])]
    return tc


def _quiet(_message):
    pass


def _distinct_suite():
    """A suite of 10 distinct TCs across categories, no near-duplicates to merge."""
    suite = TestSuite(feature_id=FID, jira_priority='High')
    suite.test_cases = [
        _tc(1, 'Verify audit file pickup from temp folder', 'Happy Path',
            steps=['Place audit file in folder', 'Confirm file is picked up by NSL']),
        _tc(2, 'Verify pairing reconcile auto-correct flow', 'Happy Path',
            steps=['Load mismatched pairing', 'Confirm auto-correct updates record']),
        _tc(3, 'Verify already-in-sync records produce a no-op', 'Happy Path',
            steps=['Load in-sync pairing', 'Confirm no update is made']),
        _tc(4, 'Verify empty audit file is handled gracefully', 'Edge Case',
            steps=['Supply an empty file', 'Confirm graceful empty handling']),
        _tc(5, 'Verify malformed audit file is rejected', 'Negative',
            steps=['Supply a malformed file', 'Confirm rejection with error code']),
        _tc(6, 'Verify missing file raises a late alert', 'Negative',
            steps=['Omit the audit file', 'Confirm a late/missing alert fires']),
        _tc(7, 'Verify duplicate pairing rows are deduped', 'Regression',
            steps=['Load duplicate pairing rows', 'Confirm dedup leaves one record']),
        _tc(8, 'Verify historical records remain unchanged', 'Regression',
            steps=['Inspect historical records', 'Confirm they remain unchanged']),
        _tc(9, 'Verify dashboard menu stays visible after sync', 'Happy Path',
            steps=['Open dashboard menu', 'Confirm menu remains visible']),
        _tc(10, 'Verify notification is emitted on reconcile', 'Happy Path',
            steps=['Trigger reconcile', 'Confirm notification is emitted']),
    ]
    return suite


def test_cap_returns_exactly_n_with_category_balance():
    suite = _distinct_suite()
    _apply_lean_cap(suite, {'max_test_cases': 6}, _Jira(), log=_quiet)

    assert len(suite.test_cases) == 6
    cats = {(tc.category or '').lower() for tc in suite.test_cases}
    # At least one Negative is retained because the full suite had Negatives.
    assert 'negative' in cats
    # The '[LEAN]' summary warning is emitted only when the cap applied.
    assert any('[LEAN]' in w for w in suite.warnings)


def test_cap_keeps_at_least_one_of_each_present_major_category():
    suite = _distinct_suite()
    _apply_lean_cap(suite, {'max_test_cases': 4}, _Jira(), log=_quiet)

    assert len(suite.test_cases) == 4
    buckets = set()
    for tc in suite.test_cases:
        c = (tc.category or '').lower()
        if c in ('happy path', 'positive'):
            buckets.add('Positive')
        elif c == 'negative':
            buckets.add('Negative')
        elif c == 'regression':
            buckets.add('Regression')
    # All three present major categories keep a representative within the 4-slot cap.
    assert {'Positive', 'Negative', 'Regression'} <= buckets


def test_chalk_core_not_evicted_for_lower_priority_analyst_edge():
    """A Chalk-grounded core TC must survive over a lower-priority analyst/edge TC."""
    suite = TestSuite(feature_id=FID, jira_priority='High')
    # Two Happy Path TCs at similar rank: one Chalk core, one analyst-derived edge.
    chalk_core = _tc(1, 'Verify audit file pickup from temp folder', 'Happy Path',
                     source_type='Chalk Scenario', source_id='chalk_pickup',
                     steps=['Place audit file', 'Confirm pickup by NSL'])
    analyst_edge = _tc(2, 'Verify concurrency window edge during reconcile', 'Edge Case',
                       source_type='Business Rule',
                       source_id='Concurrency-%s' % FID,
                       steps=['Fire two concurrent reconciles', 'Confirm edge handling'])
    suite.test_cases = [chalk_core, analyst_edge]

    _apply_lean_cap(suite, {'max_test_cases': 1}, _Jira(), log=_quiet)

    assert len(suite.test_cases) == 1
    assert suite.test_cases[0].summary == chalk_core.summary


def test_no_cap_returns_full_suite_unchanged_none():
    suite = _distinct_suite()
    before = [tc.summary for tc in suite.test_cases]
    before_warnings = list(suite.warnings)

    _apply_lean_cap(suite, {'max_test_cases': None}, _Jira(), log=_quiet)

    assert [tc.summary for tc in suite.test_cases] == before
    assert suite.warnings == before_warnings
    assert not any('[LEAN]' in w for w in suite.warnings)


def test_no_cap_returns_full_suite_unchanged_key_absent():
    suite = _distinct_suite()
    before = [tc.summary for tc in suite.test_cases]

    _apply_lean_cap(suite, {}, _Jira(), log=_quiet)

    assert [tc.summary for tc in suite.test_cases] == before
    assert not any('[LEAN]' in w for w in (suite.warnings or []))


def test_cap_larger_than_suite_is_noop():
    suite = _distinct_suite()
    before = [tc.summary for tc in suite.test_cases]

    _apply_lean_cap(suite, {'max_test_cases': 99}, _Jira(), log=_quiet)

    assert [tc.summary for tc in suite.test_cases] == before
    assert not any('[LEAN]' in w for w in (suite.warnings or []))
