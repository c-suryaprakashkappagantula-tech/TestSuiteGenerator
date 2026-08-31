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
import types

import pytest

from modules import eligibility_negatives as elig
from modules import test_engine


def fake_jira(summary='', description='', acceptance_criteria='', key='MWTGPROV-0000'):
    return types.SimpleNamespace(
        key=key, summary=summary, description=description,
        acceptance_criteria=acceptance_criteria, issue_type='Story')


def fake_chalk(scope='', scenario_titles=()):
    return types.SimpleNamespace(
        scope=scope,
        scenarios=[types.SimpleNamespace(title=t, validation='') for t in scenario_titles])


class TestGateSource:
    """The gate must read the ticket and Chalk, never the generated suite.

    These were source-string assertions against `test_engine`. The gate now lives in
    `eligibility_negatives.evaluate`, so they assert on data and behaviour instead - which is
    what the extraction bought.
    """

    def test_the_gate_cannot_see_generated_test_cases(self):
        """Structural guarantee: `evaluate` is not given the suite, so it cannot read it.

        Stronger than the old string check. Previously the outer keyword gate read source
        text but `has_commercial` / `has_tmo` / `is_feature_gated` still read
        `jira_text + suite_text`, so generated content decided WHICH negatives were added.
        Passing no suite at all makes that impossible rather than merely absent.
        """
        params = inspect.signature(elig.evaluate).parameters
        assert 'suite' not in params
        assert 'test_cases' not in params
        assert set(params) <= {'jira', 'chalk', 'feature_class', 'classification'}

    def test_source_text_is_only_ticket_and_chalk(self):
        text = elig.source_text(
            fake_jira(summary='Change BCD', description='desc', acceptance_criteria='ac'),
            fake_chalk(scope='scope', scenario_titles=['a chalk scenario']))
        for expected in ('change bcd', 'desc', 'ac', 'scope', 'a chalk scenario'):
            assert expected in text

    def test_the_bare_word_feature_is_not_a_signal(self):
        """'feature' fired on 48% of the cache and was the sole trigger for 71 features."""
        assert 'feature' not in elig._ELIGIBILITY_SIGNALS
        assert 'feature' not in elig._FEATURE_GATED_SIGNALS

    def test_a_feature_only_ticket_does_not_qualify(self):
        signals = elig.evaluate(fake_jira(
            summary='Feature provisioning for the feature service',
            description='the feature is provisioned as a feature'))
        assert not signals.applicable

    def test_the_function_accepts_chalk(self):
        params = inspect.signature(test_engine._synthesize_eligibility_negatives).parameters
        assert 'chalk' in params, 'Chalk scenarios are source material and must be readable'


class TestSignalStrength:
    """Regressions for three mistakes made while consolidating, each caught by measurement."""

    def test_tmo_alone_is_not_an_eligibility_signal(self):
        """Letting has_tmo qualify opened the gate on 184 extra features.

        'tmo' / 't-mobile' / 'mvno' appear in nearly every ticket in a T-Mobile integration
        project, so it says nothing about whether a feature is eligibility-gated. It selects
        WHICH negative to add, not WHETHER any apply.
        """
        signals = elig.evaluate(fake_jira(
            summary='TMO subscriber provisioning via MVNO',
            description='the T-Mobile subscriber is provisioned'))
        assert signals.has_tmo, 'the TMO signal itself should still be detected'
        assert not signals.applicable, 'but it must not open the gate on its own'

    @pytest.mark.parametrize('word', ['build', 'child', 'wildcard', 'rebuild'])
    def test_ild_does_not_match_inside_another_word(self, word):
        """Substring matching made 'ild' fire on BUILD and CHILD - 21 spurious features."""
        assert not elig._found('we %s the pipeline' % word, ('ild',))

    def test_ild_still_matches_as_a_word(self):
        assert elig._found('ILD SMS entitlement', ('ild',)) == ['ild']

    @pytest.mark.parametrize('text,signal', [
        ('the entitlements are checked', 'entitlement'),
        ('add-ons are gated', 'add-on'),
        ('rate plans differ', 'rate plan'),
    ])
    def test_plurals_still_match(self, text, signal):
        """Over-strict boundaries dropped features that did state a gating condition."""
        assert elig._found(text, (signal,))

    def test_mediation_is_judged_on_the_title_not_the_body(self):
        """Taking V7's text-wide rule excluded MWTGPROV-4373, the canonical feature.

        A line-eligibility feature legitimately MENTIONS mediation in its acceptance
        criteria without being a mediation feature. 4373 is "International Mobile Hotspot
        for commercial lines on TMO" - the feature both old implementations cited as their
        reason for existing - and the text-wide rule killed it.
        """
        mentions = elig.evaluate(fake_jira(
            summary='International Mobile Hotspot for commercial lines on TMO',
            description='downstream mediation receives the CDR record type'))
        assert mentions.applicable, 'a mere mention of mediation must not exclude'

        actual = elig.evaluate(fake_jira(
            summary='Mediation CDR record type mapping for commercial lines'))
        assert not actual.applicable, 'a mediation feature is excluded'

    def test_commercial_line_qualifies(self):
        signals = elig.evaluate(fake_jira(
            summary='Hotspot on commercial lines',
            description='commercial line eligibility is enforced'))
        assert signals.applicable and signals.has_commercial

    def test_feature_gating_qualifies_without_commercial(self):
        signals = elig.evaluate(fake_jira(
            summary='Tethering entitlement check',
            description='the subscriber tethering entitlement is validated'))
        assert signals.applicable and signals.is_feature_gated

    def test_the_reason_is_always_populated(self):
        """The caller logs this, so a silent empty string would be useless."""
        for jira in (fake_jira(summary='nothing relevant here'),
                     fake_jira(summary='Hotspot on commercial lines')):
            assert elig.evaluate(jira).reason

    def test_excluded_feature_types_are_refused(self):
        for feature_type in ('notification', 'batch_report', 'ui_portal'):
            fc = types.SimpleNamespace(is_notification=False, is_batch=False,
                                       feature_type=feature_type)
            signals = elig.evaluate(
                fake_jira(summary='Hotspot on commercial lines'), feature_class=fc)
            assert not signals.applicable, feature_type

    def test_ui_classification_is_refused(self):
        signals = elig.evaluate(fake_jira(summary='Hotspot on commercial lines'),
                                classification='ui')
        assert not signals.applicable


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
