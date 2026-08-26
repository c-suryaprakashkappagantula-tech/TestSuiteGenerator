"""The CR/bug-fix path: title numbering and honest cap reporting.

Guards the 20 August fix. The CR scope filter renumbers tc.sno but had left the TCnnn_ prefix
baked into each summary at construction time, so both CR features carried duplicate title
numbers - MWTGPROV-4166 had two TC001_ and two TC009_, MWTGPROV-4406 two TC002_ and TC003_.
sno was always unique, so this only bit anything keying on the title.

The cap warning also quoted the base cap while the filter applies
_effective_cap = _CR_TC_CAP + len(_workflow_tcs), which made a suite sitting exactly on its
limit look like it had breached one.
"""
import re

import pytest

CR_FEATURES = ['MWTGPROV-4166', 'MWTGPROV-4406']
PREFIX = re.compile(r'^(TC\d+)_')


@pytest.mark.cache
@pytest.mark.parametrize('feature_id', CR_FEATURES)
class TestCRTitleNumbering:

    def test_no_duplicate_title_numbers(self, build_suite, feature_id):
        suite = build_suite(feature_id)
        numbers = [PREFIX.match(getattr(tc, 'summary', '') or '').group(1)
                   for tc in suite.test_cases
                   if PREFIX.match(getattr(tc, 'summary', '') or '')]
        duplicates = {n for n in numbers if numbers.count(n) > 1}
        assert not duplicates, '%s has colliding title numbers: %s' % (feature_id, duplicates)

    def test_title_number_matches_sno(self, build_suite, feature_id):
        suite = build_suite(feature_id)
        for tc in suite.test_cases:
            match = PREFIX.match(getattr(tc, 'summary', '') or '')
            if not match:
                continue          # the E2E case is named differently by design
            assert int(match.group(1)[2:]) == int(tc.sno), (
                'title %s does not match sno %s' % (match.group(1), tc.sno))


@pytest.mark.cache
@pytest.mark.parametrize('feature_id', CR_FEATURES)
def test_cap_warning_reports_the_limit_actually_applied(build_suite, feature_id):
    suite = build_suite(feature_id)
    warnings = [w for w in (getattr(suite, 'warnings', None) or []) if 'CR/Bug fix' in str(w)]
    if not warnings:
        pytest.skip('%s did not trip the CR scope filter' % feature_id)
    reported = int(re.search(r'max (\d+)', warnings[0]).group(1))
    assert len(suite.test_cases) <= reported, (
        '%s reports max %d but delivered %d test cases'
        % (feature_id, reported, len(suite.test_cases)))
