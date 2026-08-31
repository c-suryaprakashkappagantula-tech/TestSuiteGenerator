"""Patterns this team does not test are suppressed by BOTH engines.

`test_engine._quality_gate` held a "global suppress" list - auth and token validity at API
level, DB-state assertions, raw payload structure, idempotency, NANP area codes - as a LOCAL
VARIABLE inside a 433-line function. Only V7 applied it, and V7 runs only for CR and bug
tickets, so a documented decision about what this team tests was enforced on roughly 8% of
the cache.

Measured across 45 non-CR features and 441 generated test cases, V8 shipped 2 that V7 would
have suppressed, both "Verify with invalid token of api activation payload returns failure".

The same measurement is why nothing else moved out of `_quality_gate`. Of its four rejection
checks only this one fired on V8 output (junk titles 0, generic steps 0, vague titles 0,
placeholder steps 0), and every repair condition was 0 - no empty descriptions or
preconditions, no over-long titles, no doubled verbs, no mostly-generic expected results, no
test cases with fewer than two steps. The "V8 has no quality gate" gap is real in structure
and nearly empty in practice, which is what these tests pin.
"""
import types

import pytest

from modules import tc_quality_rules as rules


def tc(summary):
    return types.SimpleNamespace(summary=summary, description='', steps=[])


class TestSuppressedPatterns:

    @pytest.mark.parametrize('title', [
        'MWTGPROV-3641_Verify_with_invalid_token_of_api_activation_payload_returns_failure',
        'Verify with invalid token of api activation payload returns failure',
        'Verify expired auth is rejected',
        'Verify authentication failure returns 401',
        'Verify NSL DB state consistent after activation',
        'Verify api response payload structure matches the schema',
        'Verify rejects duplicate request',
        'Verify idempotency of the activate call',
        'Verify NANP countries are handled',
        'Verify only the first 10 digits are used',
    ])
    def test_these_are_suppressed(self, title):
        assert rules.is_suppressed(title), title

    @pytest.mark.parametrize('title', [
        'MWTGPROV-4190_Verify_BCD_change_succeeds_for_an_active_line',
        'Verify BCD change is rejected for a suspended line',
        'Verify usage notification thresholds map to the correct downstream throttle',
        'Verify migration blocked for non-commercial (Residential) account',
        'Verify the wholesale plan is derived from the retail plan',
    ])
    def test_legitimate_test_cases_are_kept(self, title):
        assert not rules.is_suppressed(title), title

    def test_both_naming_conventions_behave_the_same(self):
        """V8 joins titles with underscores, V7 with spaces.

        Without normalisation the rules would fire differently depending on which engine
        produced the test case - the exact inconsistency this module exists to remove.
        """
        spaced = 'Verify expired auth is rejected'
        underscored = 'Verify_expired_auth_is_rejected'
        assert rules.is_suppressed(spaced)
        assert rules.is_suppressed(underscored)
        assert rules.suppression_reason(spaced) == rules.suppression_reason(underscored)

    def test_the_reason_is_the_pattern_that_matched(self):
        """A surprising removal must be traceable to the rule that caused it."""
        assert rules.suppression_reason(
            'Verify with invalid token returns failure') == 'invalid.*token'


class TestFilterSuppressed:

    def test_it_keeps_order_and_drops_only_matches(self):
        cases = [tc('Verify BCD change succeeds'),
                 tc('Verify with invalid token returns failure'),
                 tc('Verify BCD change is rejected for a suspended line')]
        kept = rules.filter_suppressed(cases)
        assert [c.summary for c in kept] == [
            'Verify BCD change succeeds',
            'Verify BCD change is rejected for a suspended line']

    def test_it_logs_the_reason_for_each_removal(self):
        logged = []
        rules.filter_suppressed([tc('Verify with invalid token returns failure')],
                               log=logged.append, engine='[V8-ENGINE]')
        assert len(logged) == 1
        assert 'invalid.*token' in logged[0]
        assert '[V8-ENGINE]' in logged[0]

    def test_a_broken_logger_does_not_break_generation(self):
        def bad(_msg):
            raise RuntimeError('logger exploded')
        kept = rules.filter_suppressed(
            [tc('Verify BCD change succeeds'),
             tc('Verify with invalid token returns failure')], log=bad)
        assert [c.summary for c in kept] == ['Verify BCD change succeeds']

    @pytest.mark.parametrize('value', [None, []])
    def test_empty_input_is_safe(self, value):
        assert rules.filter_suppressed(value) == []

    def test_a_missing_summary_is_kept(self):
        """An absent title is a different problem; this rule must not silently eat it."""
        assert len(rules.filter_suppressed([types.SimpleNamespace(summary=None)])) == 1


class TestBothEnginesApplyIt:
    """The point of the extraction: neither engine may quietly skip the rule."""

    def test_v7_quality_gate_uses_the_shared_module(self):
        import inspect

        from modules.test_engine import _quality_gate
        source = inspect.getsource(_quality_gate)
        assert 'tc_quality_rules' in source
        assert '_GLOBAL_SUPPRESS_PATTERNS' not in source, (
            'the local copy is back; it would drift from the shared rules')

    def test_v8_engine_uses_the_shared_module(self):
        import inspect

        from modules.data_first_engine import build_test_suite_v8
        assert 'tc_quality_rules' in inspect.getsource(build_test_suite_v8)


@pytest.mark.cache
def test_a_real_v8_suite_carries_no_suppressed_test_case(build_suite):
    """End to end on a cached non-CR feature."""
    suite = build_suite('MWTGPROV-4086')
    offenders = [tc_.summary for tc_ in suite.test_cases
                 if rules.is_suppressed(tc_.summary or '')]
    assert not offenders, 'V8 shipped suppressed test cases: %s' % offenders
