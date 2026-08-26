"""Eligibility negatives are gated on source material, not on generated test cases.

Guards the 20 August fix. MWTGPROV-4166 is a guaranteed-delivery/retry defect that received
four injected eligibility negatives - non-commercial line, non-TMO network, non-eligible rate
plan, prerequisite entitlement - none of which relate to its acceptance criteria or to its
single Chalk scenario.

Two causes. The gate accepted the bare word 'feature', which fired on 48% of the cache and
was the sole trigger for 71 features. And it read the suite built so far, so 'subscriber' in
a regression test case and 'eligible' in a negative produced moments earlier were enough to
authorise four more.
"""
import inspect

from modules import test_engine


class TestGateSource:
    """The gate must read the ticket and Chalk, never the generated suite."""

    def test_the_gate_does_not_read_generated_test_cases(self):
        source = inspect.getsource(test_engine._synthesize_eligibility_negatives)
        gate = [l for l in source.splitlines() if 'commercial line' in l and 'kw in' in l]
        assert gate, 'could not locate the eligibility keyword gate'
        assert 'source_text' in gate[0], (
            'the gate must read source_text (ticket + Chalk), not the suite built so far')

    def test_the_bare_word_feature_is_not_a_signal(self):
        source = inspect.getsource(test_engine._synthesize_eligibility_negatives)
        start = source.index('Only apply to actual line/subscriber')
        gate_block = source[start:start + 900]
        assert "'feature'," not in gate_block, (
            "'feature' fired on 48% of the cache and signals nothing about eligibility "
            'gating in a system about feature provisioning')

    def test_the_function_accepts_chalk(self):
        params = inspect.signature(test_engine._synthesize_eligibility_negatives).parameters
        assert 'chalk' in params, 'Chalk scenarios are source material and must be readable'


class TestNoUngroundedNegativesOn4166:
    """The four injected negatives must not come back."""

    UNGROUNDED = [
        'non-eligible rate plan',
        'prerequisite entitlement',
        'non-commercial',
        'err06',
    ]

    def test_4166_has_no_injected_eligibility_negatives(self, build_suite):
        suite = build_suite('MWTGPROV-4166')
        titles = ' '.join((getattr(tc, 'summary', '') or '').lower()
                          for tc in suite.test_cases)
        for phrase in self.UNGROUNDED:
            assert phrase not in titles, (
                '%r is not grounded in 4166 acceptance criteria or its Chalk scenario'
                % phrase)

    def test_4166_keeps_its_real_coverage(self, build_suite):
        suite = build_suite('MWTGPROV-4166')
        titles = ' '.join((getattr(tc, 'summary', '') or '').lower()
                          for tc in suite.test_cases)
        assert 'cr fix applies' in titles, 'the defect-fix test case must survive'
        assert 'no longer occurs' in titles, 'the old-error check must survive'
