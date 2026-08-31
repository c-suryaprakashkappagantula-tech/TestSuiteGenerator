"""The near-duplicate pruner must not collapse unrelated test cases.

Guards the 31 August fix for the worst defect found so far. `_prune_near_duplicate_tcs`
was deleting most of every V8 suite:

    [V8-ENGINE]   Near-dup pruning: 23 -> 9 TCs

All 14 removals for MWTGPROV-4136 were reported as near-duplicates of the SAME test case,
TC01, including "Call CPC API for TMO", "Remove the NPANXX option from the activation flow"
and "PortIn Verify Port Status Removed". None of those is a duplicate of anything.

TWO FAULTS COMBINED, and the fingerprint was effectively blind to the title:

1. The tokeniser was `\\b[a-z0-9]{4,}\\b`. Underscore is a WORD character in regex, so `\\b`
   never fires inside underscore-joined text. On a V8 title like
   'MWTGPROV-4136_TC01_NBOP_Verify_Attributes_Displayed_TMO' the ONLY token extracted was
   'mwtgprov' - every other word failed the trailing boundary.
2. The prefix strip expected 'TC01_MWTGPROV-4136_...' but V8 emits
   'MWTGPROV-4136_TC01_...', so the feature id and TC number were never removed.

With the title contributing one shared token, and the first two step summaries blended in -
identical navigation boilerplate on every UI test case - every fingerprint came out the same
and Jaccard overlap was 1.00.

Measured impact of the fix across the 16 snapshot features: 277 -> 307 test cases, +30
recovered, none lost. MWTGPROV-4136 went 9 -> 22. Every recovered test case traced to Jira AC
or Subtask AC, because Chalk-derived cases were protected by `_is_chalk_ground_truth` while
everything else collapsed - which is why the output looked Chalk-only.

Note the shared root cause with `truncate_at_word`: helpers written for V7's space-separated
titles silently misbehaving on V8's underscore-joined ones. Worth checking for elsewhere.
"""
import types

import pytest

from modules.data_first_engine import _prune_near_duplicate_tcs


def tc(summary, steps=2, category='Happy Path', source='Jira AC', sno=''):
    """A test case shaped like the V8 engine's, with underscore-joined title."""
    return types.SimpleNamespace(
        sno=sno, summary=summary, description='', category=category,
        user_requested=False, from_chalk=False,
        traceability=types.SimpleNamespace(source_type=source, confidence=0.8),
        steps=[types.SimpleNamespace(
            summary='Launch NBOP portal and search subscriber by MDN',
            expected='Subscriber found') for _ in range(steps)],
    )


class TestUnrelatedTestCasesSurvive:
    """The regression that mattered: 61% of a suite deleted as 'duplicates'."""

    def test_the_real_4136_titles_are_not_collapsed(self):
        cases = [
            tc('MWTGPROV-4136_TC01_NBOP_Verify_Attributes_Displayed_TMO'),
            tc('MWTGPROV-4136_TC14_NBOP_Call_CPC_API_for_TMO_to_fetch_the_wholesale_plan'),
            tc('MWTGPROV-4136_TC17_NBOP_Remove_the_NPANXX_option_from_the_activation_flow'),
            tc('MWTGPROV-4136_TC15_NBOP_PortIn_Verify_Port_Status_Removed'),
            tc('MWTGPROV-4136_TC18_NBOP_Update_the_MDN_screen_to_support_only_zipcode'),
            tc('MWTGPROV-4136_TC20_NBOP_should_be_able_to_call_TMO_APIs_based_on_network'),
        ]
        kept = _prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)
        assert len(kept) == len(cases), (
            'unrelated test cases were pruned: kept %d of %d'
            % (len(kept), len(cases)))

    def test_identical_step_boilerplate_does_not_make_them_duplicates(self):
        """Every UI test case starts with the same navigation steps.

        Blending those into the fingerprint was the second half of the defect, so this
        pins that shared steps alone cannot merge two test cases.
        """
        cases = [
            tc('MWTGPROV-1_TC01_NBOP_Change_Features_option_under_Manage_Line_menu'),
            tc('MWTGPROV-1_TC02_NBOP_Transaction_History_Details_displays_MNO_information'),
        ]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2

    def test_the_title_actually_contributes_to_the_fingerprint(self):
        """Two test cases differing ONLY in title must not merge.

        This is the direct assertion that the tokeniser sees underscore-joined words. With
        the old `\\b[a-z0-9]{4,}\\b` both fingerprints were {'mwtgprov'} plus step words.
        """
        cases = [
            tc('MWTGPROV-1_TC01_NBOP_wholesale_plan_derivation_from_retail_catalog'),
            tc('MWTGPROV-1_TC02_NBOP_zipcode_validation_on_the_activation_screen'),
        ]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2


class TestGenuineDuplicatesAreStillRemoved:
    """A fix that pruned nothing would also pass the tests above."""

    def test_the_same_test_twice_is_deduplicated(self):
        cases = [
            tc('MWTGPROV-1_TC03_NBOP_PortIn_Verify_Port_Status_Removed_TMO', steps=3),
            tc('MWTGPROV-1_TC15_NBOP_PortIn_Verify_Port_Status_Removed', steps=2),
        ]
        kept = _prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)
        assert len(kept) == 1, 'a genuine duplicate survived'
        assert len(kept[0].steps) == 3, 'the richer test case should be the one kept'

    def test_a_thin_fingerprint_is_not_treated_as_a_match(self):
        """Below three meaningful words Jaccard is binary - one shared word scores 1.00.

        That is how a broken fingerprint collapsed a whole suite, so the pruner now declines
        to judge. Keeping an uncertain test case costs a review; dropping a real one loses
        coverage silently.
        """
        cases = [tc('MWTGPROV-1_TC01_NBOP_Verify_Attributes'),
                 tc('MWTGPROV-1_TC02_NBOP_Verify_Eligibility')]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2


class TestProtections:

    def test_chalk_ground_truth_is_never_pruned(self):
        cases = [
            tc('MWTGPROV-1_TC01_NBOP_Submit_the_change_request', source='Chalk Scenario'),
            tc('MWTGPROV-1_TC02_NBOP_Submit_the_change_request', source='Chalk Scenario'),
        ]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2

    def test_a_negative_never_merges_into_a_happy_path(self):
        cases = [
            tc('MWTGPROV-1_TC01_NBOP_wholesale_plan_derivation_from_retail_catalog',
               category='Happy Path'),
            tc('MWTGPROV-1_TC02_NBOP_wholesale_plan_derivation_from_retail_catalog',
               category='Negative'),
        ]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2

    def test_a_user_requested_test_case_is_never_pruned(self):
        keep = tc('MWTGPROV-1_TC02_NBOP_PortIn_Verify_Port_Status_Removed')
        keep.user_requested = True
        cases = [tc('MWTGPROV-1_TC01_NBOP_PortIn_Verify_Port_Status_Removed'), keep]
        assert len(_prune_near_duplicate_tcs(cases, log=lambda *a, **k: None)) == 2

    @pytest.mark.parametrize('value', [None, []])
    def test_empty_input_is_safe(self, value):
        assert _prune_near_duplicate_tcs(value, log=lambda *a, **k: None) == []


@pytest.mark.cache
def test_4136_recovers_its_jira_and_subtask_coverage(build_suite):
    """End to end: the feature that exposed this shipped 9 test cases from 15 Chalk
    scenarios plus Jira and subtask mining. Chalk-only output was the visible symptom."""
    suite = build_suite('MWTGPROV-4136')
    assert len(suite.test_cases) >= 20, (
        'expected the recovered coverage, got %d test cases' % len(suite.test_cases))
    sources = {str(getattr(getattr(tc_, 'traceability', None), 'source_type', '') or '')
               for tc_ in suite.test_cases}
    assert any('Jira' in s or 'Subtask' in s for s in sources), (
        'only Chalk-derived test cases survived; sources were %s' % sorted(sources))
