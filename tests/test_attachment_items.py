"""Attachment-derived "open items" become test cases only when they are requirements.

Guards the 20 August fix. MWTGPROV-4166 generated a test case named after the column headers
of a Service-Grouping HTML report attached to subtask NSLNM-646.

Measured against every attachment-derived test case in the cache when the fix was written -
four in total:

    feature       len   caps_ratio  column_ids
    3779 TC18      95   0.11        0    legitimate note
    3779 TC19     236   0.10        0    legitimate note
    3779 TC20     197   0.00        0    legitimate requirement
    4166 TC08    2906   0.45        14   the report dump
"""
import re
import sqlite3

import pytest

from modules.test_engine import _build_open_item_tc, _is_report_dump

DUMP = (
    'COPY OF SERVICE GROUPING COPY OF SERVICE GROUPING FILTER CONDITIONS '
    'ROOT_TRANSACTION_ID CONTAINS 2850159462 TRANSACTION_ID ROOT_TRANSACTION_ID '
    'TRANSACTION_NAME APPLICATION_NAME OUTBOUND_URL TRANSACTION_TYPE EXT TRANSACTION ID '
    'STATUS REQUEST_MSG REQ_SENT_DATE RESPONSE_MSG RESP_RECEIVED_DATE CHANNEL'
)

GENUINE = [
    'Note: A request to add a new IPFO cannot be received when an IPFO is already in an '
    'active state',
    'To address this dependency, EMM is rescheduling their job so that the dump is generated '
    'before the NSL reconciliation job begins.',
    'Add an exclusive gateway task to check whether the Active/In Use speed-pass promotion '
    'needs to be deleted',
]


class TestBuilderRejectsDumps:

    def test_a_report_dump_builds_no_test_case(self):
        assert _build_open_item_tc(DUMP, 1, 'MWTGPROV-0000', 'report.html') is None

    @pytest.mark.parametrize('text', GENUINE)
    def test_a_genuine_note_still_builds_one(self, text):
        assert _build_open_item_tc(text, 1, 'MWTGPROV-0000', 'notes.docx') is not None

    def test_a_bare_header_row_is_rejected(self):
        assert _is_report_dump(
            'TRANSACTION_ID ROOT_TRANSACTION_ID TRANSACTION_NAME APPLICATION_NAME'
        ) is True

    def test_capitalised_acronyms_without_column_ids_survive(self):
        assert _is_report_dump(
            'NSL must send requestType as TMO in the HTTP header'
        ) is False


class TestAgainstEverythingInTheCache:
    """Whatever is stored, the classification must still separate the two kinds."""

    def test_stored_attachment_items_classify_correctly(self, cache_available):
        conn = sqlite3.connect(cache_available)
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute(
            "SELECT summary, description FROM test_cases WHERE description LIKE 'Per %'"))
        conn.close()
        if not rows:
            pytest.skip('no attachment-derived test cases stored in this cache')

        for row in rows:
            body = re.sub(r'^Per [^:]+:\s*', '', row['description'] or '')
            is_dump = _is_report_dump(body)
            looks_like_a_dump = 'COPY OF SERVICE GROUPING' in body
            assert is_dump == looks_like_a_dump, (
                'misclassified: %s' % (row['summary'] or '')[:80])
