"""The subtask-AC gate: what becomes a test case and what does not.

Guards the 19 August fix. The gate had been dropping every colon-terminated line, which
removed 14 genuine requirement headers across 12 features along with their child bullets,
and had been keeping multi-line prose blobs because a keyword matched anywhere inside them.
"""
import pytest

from modules.deep_miner import (
    _header_kind, _is_testable_ac_item,
    _HEADER_CONDITION, _HEADER_ENUMERATION, _HEADER_NONE,
)
# The report-dump filter guards attachment-derived items, so it lives with
# _build_open_item_tc in test_engine rather than with the AC gate.
from modules.test_engine import _is_report_dump


class TestConditionHeaders:
    """A condition header states a precondition and must never become a test case."""

    def test_the_4190_junk_tc_source_is_rejected(self):
        # This exact line produced 'Verify in CS is ON, and line is identified as ...'
        text = ("When \u2018MNO_TMO\u2019 permission in CS is ON, and line is identified as "
                "'TMO' based on networkProvider value:")
        assert _header_kind(text) == _HEADER_CONDITION
        assert _is_testable_ac_item(text) is False

    @pytest.mark.parametrize('text', [
        'For TMO subscribers only:',
        'When the flag is OFF:',
        'When the new CS TMO_Upate_UsageLimits menu is enabled for the user:',
    ])
    def test_condition_headers_are_not_test_cases(self, text):
        assert _header_kind(text) == _HEADER_CONDITION
        assert _is_testable_ac_item(text) is False


class TestEnumerationHeaders:
    """An enumeration header carries its requirement in the list beneath it."""

    @pytest.mark.parametrize('text', [
        'The following total usage attributes are removed from the Data Details and '
        'Historical Usage screens for TMO subscribers:',
        'Enable the Century Shield menu item called MNO_TMO for the following roles:',
        'The response fields should be populated as follows:',
        'Use the following parameters:',
    ])
    def test_enumeration_headers_are_classified_as_such(self, text):
        assert _header_kind(text) == _HEADER_ENUMERATION

    def test_4230_requirement_block_survives(self, mine_subtasks):
        """MWTGPROV-4230 lost its parent AND all six children before the fix."""
        items = mine_subtasks('MWTGPROV-4230')
        joined = ' '.join(items)
        assert 'total usage attributes are removed' in joined
        assert 'Total MNO Usage' in joined, 'the enumerated attributes must be carried'

    def test_4349_role_list_survives(self, mine_subtasks):
        items = mine_subtasks('MWTGPROV-4349')
        joined = ' '.join(items)
        assert 'Century Shield menu item called MNO_TMO' in joined
        assert 'NBOP_Admin' in joined, 'the enumerated roles must be carried'


class TestRealRequirementsAreKept:
    """The three requirements MWTGPROV-4190 must yield."""

    @pytest.mark.parametrize('text', [
        'NBOP to display "Change DPFO Reset Day" option on the Home/Line Summary',
        'On selecting the Change DPFO Reset Day option, show the same fields as with '
        'Verizon Change DPFO Reset',
        'Create a new ability in NSLNM, that allows to change Bill Cycle Day on Wholesale MDN',
    ])
    def test_kept(self, text):
        assert _is_testable_ac_item(text) is True
        assert _header_kind(text) == _HEADER_NONE


class TestJunkIsRejected:

    def test_prose_asserting_nothing(self):
        assert _is_testable_ac_item(
            "Same rules applicable for TMO though we don't have SOLO or Second line in TMO"
        ) is False

    def test_lowercase_continuation_fragment(self):
        assert _is_testable_ac_item("in the requestType field use 'TMO'.") is False

    def test_word_boundary_not_substring(self):
        """'Authentication' contains 'then'. Substring matching kept an API parameter row."""
        assert _is_testable_ac_item('Authentication:\u00a0JWT') is False

    def test_verb_inflections_still_match(self):
        """Word boundaries alone lost 'updated', which is not the listed form of 'update'."""
        assert _is_testable_ac_item(
            'NSL has updated the query esim status API to ensure it provides a network '
            'indicator in the response'
        ) is True

    def test_sprint_planning_note(self):
        assert _is_testable_ac_item('The following stories are Fast Tracked to 53.3') is False

    def test_non_functional_boilerplate(self):
        assert _is_testable_ac_item('KPIs and SLAs should be BAU.') is False


class TestReportDumpDetection:
    """A flattened Service-Grouping export is data, not a requirement."""

    def test_the_4166_dump_is_rejected(self):
        assert _is_report_dump(
            'COPY OF SERVICE GROUPING COPY OF SERVICE GROUPING FILTER CONDITIONS '
            'ROOT_TRANSACTION_ID CONTAINS 2850159462 TRANSACTION_ID TRANSACTION_NAME '
            'APPLICATION_NAME OUTBOUND_URL TRANSACTION_TYPE REQUEST_MSG REQ_SENT_DATE'
        ) is True

    def test_a_requirement_naming_one_column_survives(self):
        """'Verify ROOT_TRANSACTION_ID is populated' is a plausible requirement here."""
        assert _is_report_dump(
            'Verify ROOT_TRANSACTION_ID is populated on every outbound transaction'
        ) is False

    @pytest.mark.parametrize('text', [
        'Note: A request to add a new IPFO cannot be received when an IPFO is already active',
        'Add an exclusive gateway task to check whether the promotion needs to be deleted',
        '',
    ])
    def test_genuine_attachment_notes_survive(self, text):
        assert _is_report_dump(text) is False
