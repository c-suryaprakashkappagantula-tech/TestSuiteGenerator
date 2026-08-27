"""Descriptions do not double their opening verb.

Guards the 27 August fix. Every description template in `test_engine` supplies its own verb -
'Validate %s completes successfully.', 'Validate %s through NBOP portal.', 'To validate that
%s.' - and scenario titles overwhelmingly open with 'Verify'. The result read:

    Validate Verify request accepts accountType COMMERCIAL and responds normally completes
    successfully.

The code already tried to prevent this but stripped only a leading 'validate ', so the far
more common 'Verify ' sailed through. Measured before the fix: 48 of 435 CR test-case
descriptions opened with a doubled verb, 46 of them in MWTGPROV-4168. After: 4, all of them a
different shape (a numbered or section-prefixed title carrying the verb internally, e.g.
'1 Validate after ...'), which this fix deliberately does not attempt.

Worth recording how this was found, because the first attempt was aimed at the wrong place.
The doubling was assumed to come from `humanizer.humanize_descriptions` prefixing a core that
already began with a verb. A fix there measured IDENTICAL before and after across all 51 CR
features - 48 either way - because the humanizer only touches descriptions that start with
'To validate that', and these did not. The lesson: measure the fix against real output, not
against a reconstruction of the bug.
"""
import re

import pytest

from modules.test_engine import _strip_leading_verb_for_description

VERBS = r'(?:Verify|Confirm|Ensure|Check|Validate)'
DOUBLED = re.compile(r'^' + VERBS + r'[^.]{0,40}?\s+' + VERBS + r'\b', re.IGNORECASE)


class TestStripLeadingVerb:

    @pytest.mark.parametrize('title,expected', [
        ('Verify request accepts accountType COMMERCIAL',
         'request accepts accountType COMMERCIAL'),
        ('Verify that BCD changes are accepted for an active line',
         'BCD changes are accepted for an active line'),
        ('Validate NSL scans the inbound request and returns an error',
         'NSL scans the inbound request and returns an error'),
        ('Confirm the downstream throttle mapping is applied',
         'the downstream throttle mapping is applied'),
        ('Ensures the wholesale plan is derived from the retail plan',
         'the wholesale plan is derived from the retail plan'),
        ('Checks that the reset day falls in the allowed range',
         'the reset day falls in the allowed range'),
    ])
    def test_a_leading_verb_is_removed(self, title, expected):
        assert _strip_leading_verb_for_description(title) == expected

    @pytest.mark.parametrize('title', [
        'BCD changes are accepted for an active TMO line',
        'usage notification thresholds map to the correct downstream throttle',
        'NSL rejects a request with a malformed identifier',
    ])
    def test_a_title_without_a_leading_verb_is_untouched(self, title):
        assert _strip_leading_verb_for_description(title) == title

    @pytest.mark.parametrize('title', ['Verify', 'Verify BCD', 'Validate it', 'Check on'])
    def test_a_title_that_would_be_gutted_is_left_alone(self, title):
        """Better a doubled verb than a two-word description."""
        assert _strip_leading_verb_for_description(title) == title

    @pytest.mark.parametrize('value', ['', None])
    def test_empty_input_is_returned_as_is(self, value):
        assert _strip_leading_verb_for_description(value) == value

    def test_the_verb_is_only_stripped_from_the_front(self):
        """An internal verb is part of the sentence, not a duplicated opener."""
        title = 'the system must verify the token before it validates the payload'
        assert _strip_leading_verb_for_description(title) == title

    @pytest.mark.parametrize('template', [
        'Validate %s completes successfully.',
        'Validate %s through NBOP portal.',
        'To validate that %s.',
        'To validate that %s completes successfully.',
    ])
    def test_the_templates_no_longer_double(self, template):
        """The property that matters, asserted against the real templates."""
        title = 'Verify request accepts accountType COMMERCIAL and responds normally'
        assert DOUBLED.match(template % _strip_leading_verb_for_description(title)) is None


@pytest.mark.cache
@pytest.mark.parametrize('feature_id', ['MWTGPROV-4166', 'MWTGPROV-4406'])
def test_no_cr_description_opens_with_a_doubled_verb(build_suite, feature_id):
    """End to end on the two cached CR features, which route through the V7 templates."""
    suite = build_suite(feature_id)
    offenders = [(tc.sno, (tc.description or '')[:90])
                 for tc in suite.test_cases
                 if DOUBLED.match((tc.description or '').strip())]
    assert not offenders, '%s has doubled-verb descriptions: %s' % (feature_id, offenders)


@pytest.mark.cache
@pytest.mark.parametrize('feature_id', ['MWTGPROV-4166', 'MWTGPROV-4406'])
def test_descriptions_are_still_present_and_meaningful(build_suite, feature_id):
    """A fix that emptied descriptions would also pass the test above."""
    suite = build_suite(feature_id)
    for tc in suite.test_cases:
        desc = (tc.description or '').strip()
        assert len(desc) >= 10, '%s %s has no usable description: %r' % (
            feature_id, tc.sno, desc)
