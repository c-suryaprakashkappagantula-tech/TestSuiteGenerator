"""Whole-suite snapshot over the review features.

This is the test that catches what unit tests cannot. The `_cut` alias collision on 20 August
broke three features with UnboundLocalError while every individual helper still behaved
correctly - only building real suites exposed it.

Expectations live in fixtures/expected_suites.json. When a change moves them, review the diff
and regenerate deliberately:

    python -m tests.regenerate_snapshot

Treat an unexplained change as a defect, not as a stale expectation.
"""
import json
import os

import pytest

from tests.conftest import REVIEW_FEATURES, summaries

EXPECTED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'expected_suites.json')


@pytest.fixture(scope='module')
def expected():
    if not os.path.exists(EXPECTED):
        pytest.skip('no snapshot recorded yet - run python -m tests.regenerate_snapshot')
    with open(EXPECTED, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.mark.cache
@pytest.mark.slow
@pytest.mark.parametrize('feature_id', REVIEW_FEATURES)
class TestSuiteSnapshot:

    def test_builds_without_error(self, build_suite, feature_id):
        """The regression the _cut collision would have tripped."""
        suite = build_suite(feature_id)
        assert suite is not None
        assert suite.test_cases, '%s produced no test cases' % feature_id

    def test_test_case_count_is_unchanged(self, build_suite, expected, feature_id):
        if feature_id not in expected:
            pytest.skip('%s is not in the recorded snapshot' % feature_id)
        suite = build_suite(feature_id)
        assert len(suite.test_cases) == expected[feature_id]['count'], (
            '%s: expected %d test cases, got %d'
            % (feature_id, expected[feature_id]['count'], len(suite.test_cases)))

    def test_titles_are_unchanged(self, build_suite, expected, feature_id):
        if feature_id not in expected:
            pytest.skip('%s is not in the recorded snapshot' % feature_id)
        suite = build_suite(feature_id)
        before = set(expected[feature_id]['titles'])
        after = set(summaries(suite))
        assert after == before, (
            '%s titles changed\n  gone : %s\n  new  : %s'
            % (feature_id, sorted(before - after)[:3], sorted(after - before)[:3]))

    def test_no_title_ends_mid_word(self, build_suite, feature_id):
        """Independent of the snapshot: a property that must always hold."""
        suite = build_suite(feature_id)
        fragments = ('non-eli', 'for dea', 'any flo', 'proces', 'respons', 'deactivat')
        offenders = [t for t in summaries(suite) if t.rstrip().endswith(fragments)]
        assert not offenders, '%s has titles cut mid-word: %s' % (feature_id, offenders[:2])
