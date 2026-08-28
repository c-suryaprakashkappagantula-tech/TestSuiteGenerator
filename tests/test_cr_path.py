"""The CR/bug-fix path: title numbering and honest cap reporting.

Guards the 20 August fix. The CR scope filter renumbers tc.sno but had left the TCnnn_ prefix
baked into each summary at construction time, so both CR features carried duplicate title
numbers - MWTGPROV-4166 had two TC001_ and two TC009_, MWTGPROV-4406 two TC002_ and TC003_.
sno was always unique, so this only bit anything keying on the title.

The cap warning also quoted the base cap while the filter applies
_effective_cap = _CR_TC_CAP + len(_workflow_tcs), which made a suite sitting exactly on its
limit look like it had breached one.
"""
import os
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


# ════════════════════════════════════════════════════════════════════
#  The grounding gate on the CR path (tsg-tse-hardening task 4a)
# ════════════════════════════════════════════════════════════════════
#
# `_build_cr_suite_v8` used to return without ever calling `gate_suite`, so CR test cases kept
# `grounding_score = -1` (the dataclass default). That is not merely a missing control: because
# `suite_grounding_pct` reads `getattr(tc, 'grounding_score', score_tc(tc))` and the attribute
# ALWAYS exists, the fallback never fired and every CR suite reported its grounding as -1.0%.
#
# What the gate does NOT do matters as much. Measured across the whole cache pre-gate: CR 51
# features / 435 TCs with a minimum score of 63, non-CR 30 features / 758 TCs with a minimum of
# 88, against a threshold of 40. Nothing is dropped, so these tests assert the gate is a NO-OP
# on counts. If a future change makes it start dropping CR test cases, that is a decision to
# take deliberately (task 4b), not a silent side effect.

@pytest.mark.cache
@pytest.mark.parametrize('feature_id', CR_FEATURES)
class TestCRGroundingGate:

    def test_every_cr_test_case_is_scored(self, build_suite, feature_id):
        """The display bug: -1 was reaching the CR Coverage Scorecard."""
        suite = build_suite(feature_id)
        unscored = [tc.sno for tc in suite.test_cases
                    if getattr(tc, 'grounding_score', -1) < 0]
        assert not unscored, (
            '%s left these test cases unscored, so the scorecard shows -1%%: %s'
            % (feature_id, unscored))

    def test_reported_grounding_is_a_real_percentage(self, build_suite, feature_id):
        from modules.grounding_scorer import suite_grounding_pct
        suite = build_suite(feature_id)
        pct = suite_grounding_pct(suite.test_cases)
        assert 0.0 <= pct <= 100.0, '%s reports grounding of %.1f%%' % (feature_id, pct)

    def test_the_gate_drops_nothing_at_the_current_threshold(self, build_suite, feature_id):
        """Measured no-op. A change here is a deliberate decision, not a side effect.

        Asserted on the scores rather than on a count taken before and after, because the
        suite is memoised per session and cannot be rebuilt ungated.
        """
        from modules.grounding_scorer import GATE_THRESHOLD, score_tc
        suite = build_suite(feature_id)
        below = [(tc.sno, score_tc(tc)) for tc in suite.test_cases
                 if score_tc(tc) < GATE_THRESHOLD]
        assert not below, (
            '%s now has test cases below the threshold of %d, so the gate has started '
            'dropping things: %s' % (feature_id, GATE_THRESHOLD, below))


def test_the_gate_is_structural_and_not_a_quality_check():
    """Pins the limitation, so nobody reads a passing grade as "these are sound".

    `score_tc` measures step non-fillerness, expected non-fillerness, source type,
    confidence and step count. All four are structural, so a well-formed but meaningless
    test case is indistinguishable from a well-formed meaningful one. This is why task 4a
    does not close Requirement 3.
    """
    from modules.grounding_scorer import GATE_THRESHOLD, score_tc
    from modules.test_engine import TestCase, TestStep

    class _Trace:
        source_type = 'chalk_scenario'
        confidence = 0.95

    junk = TestCase(
        sno='TC07', summary='Verify Variations: Positive Negative Edge',
        description='To validate that the table of contents renders',
        steps=[
            TestStep(1, 'Navigate to the report page and observe the summary table',
                     'The report page displays the summary table with all columns present'),
            TestStep(2, 'Review the Variations column in the exported document',
                     'The Variations column lists Positive, Negative and Edge as written'),
            TestStep(3, 'Confirm the heading row matches the source document heading',
                     'The heading row matches the source document heading exactly'),
        ])
    junk.traceability = _Trace()

    assert score_tc(junk) >= GATE_THRESHOLD, (
        'If this now fails the gate, the scorer has learned to judge meaning and '
        'task 4b can be reconsidered - update this test deliberately')


# ════════════════════════════════════════════════════════════════════
#  The CR wrapper must not drop what V7 computed (task 6 enumeration)
# ════════════════════════════════════════════════════════════════════
#
# `_build_cr_suite_v8` rebuilds a fresh TestSuite from the V7 result rather than returning it,
# and five fields were never copied across. V7 computed them and the tester never saw them.
#
# Measured on MWTGPROV-4406: V7 produced groups=2, ac_traceability=1, combinations=4,
# data_sources=9; the delivered suite carried 0 of each. In the workbook the sheets went from
# ['Test Cases', 'Summary', 'Traceability', 'Combinations'] down to ['Test Cases', 'Summary'],
# because excel_generator builds the Traceability sheet only `if suite.ac_traceability` and
# the Combinations sheet only when `suite.combinations` has more than one entry.
#
# Test-case counts were never affected, which is exactly why it went unnoticed - the suite
# looked complete and only its cross-reference sheets were missing. Verified across all 51
# cached CR features after the fix: 0 test-case count changes, 0 fields still lost.

CARRIED_FIELDS = ['groups', 'ac_traceability', 'combinations', 'data_sources',
                  'open_items', 'open_item_coverage']


def _build_v7_directly(feature_id):
    """Build the same feature straight through V7, to compare against the wrapper."""
    from modules.database import _conn, load_chalk_as_object
    from modules.deep_miner import deep_mine
    from modules.pipeline import block_jira_fetch
    from modules.test_engine import build_test_suite
    from tests.conftest import DEFAULT_OPTIONS

    quiet = lambda *a, **k: None
    jira = block_jira_fetch(page=None, feature_id=feature_id, log=quiet)['jira']
    if jira is None:
        pytest.skip('%s is not in the cache' % feature_id)
    conn = _conn()
    row = conn.execute(
        "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
        "AND scenarios_json != '[]' LIMIT 1", (feature_id,)).fetchone()
    conn.close()
    chalk = load_chalk_as_object(feature_id, row['pi_label']) if row else None
    mined = deep_mine(jira, chalk, page=None, log=quiet)
    return build_test_suite(jira, chalk, [], dict(DEFAULT_OPTIONS), log=quiet,
                            deep_mine_result=mined)


@pytest.mark.cache
@pytest.mark.slow
@pytest.mark.parametrize('feature_id', CR_FEATURES)
class TestTheCRWrapperCarriesV7Data:

    def test_nothing_v7_produced_is_dropped(self, build_suite, feature_id):
        delivered = build_suite(feature_id)
        computed = _build_v7_directly(feature_id)

        lost = []
        for field in CARRIED_FIELDS:
            had = len(getattr(computed, field, None) or [])
            got = len(getattr(delivered, field, None) or [])
            if got < had:
                lost.append('%s: V7 computed %d, delivered %d' % (field, had, got))
        assert not lost, '%s drops what V7 computed - %s' % (feature_id, lost)

    def test_the_test_cases_themselves_are_unchanged(self, build_suite, feature_id):
        """Carrying metadata must not touch the test cases. This is the safety assertion."""
        delivered = build_suite(feature_id)
        computed = _build_v7_directly(feature_id)
        assert len(delivered.test_cases) == len(computed.test_cases)

    def test_the_traceability_sheet_is_produced(self, build_suite, feature_id):
        """The user-visible consequence: excel_generator skips the sheet on a falsy value."""
        suite = build_suite(feature_id)
        if not suite.ac_traceability:
            pytest.skip('%s has no AC traceability to render' % feature_id)

        import openpyxl

        from modules.excel_generator import generate_excel
        path = generate_excel(suite, log=lambda *a, **k: None)
        try:
            workbook = openpyxl.load_workbook(path)
            sheets = workbook.sheetnames
            workbook.close()
        finally:
            os.remove(path)
        assert 'Traceability' in sheets, (
            '%s produced no Traceability sheet; sheets were %s' % (feature_id, sheets))
