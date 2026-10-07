"""Task 7.1: infer_api_signal, is_closer_step, is_ui_feature (design §2, §3)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import qmetry_pattern_library as qpl  # noqa: E402
from modules.crawl_grounding import (  # noqa: E402
    infer_api_signal, is_closer_step, is_ui_feature,
)
from modules.data_models_v8 import (  # noqa: E402
    RoutingAudit, TestCase, TestStep, TestSuite,
)

UI_STEP = 'Navigate to the Subscriber tile and open the Line Summary tab.'
API_STEP = 'Trigger API POST /provisioning/v1/lines with the request payload.'


def _suite(classification, step_texts, audit=True):
    steps = [TestStep(step_num=i + 1, summary=t) for i, t in enumerate(step_texts)]
    tc = TestCase(sno='TC_001', summary='Synthetic TC', steps=steps)
    return TestSuite(
        feature_id='MWTGPROV-90001',
        test_cases=[tc],
        routing_audit=RoutingAudit(classification=classification) if audit else None,
    )


# --- infer_api_signal -------------------------------------------------------

@pytest.mark.parametrize('text', [
    'Trigger API for activation',
    'Invoke the change SIM service',
    'Send POST /lines',
    'GET /subscriber/123',
    'Validate the request payload fields',
    'NSL activation API returns success',
    'Verify response code is 200',
])
def test_api_signal_detected(text):
    assert infer_api_signal(text)


@pytest.mark.parametrize('text', [
    UI_STEP,
    'Verify the postpaid plan is shown on the page',
    'User should get the line details on screen',
    '',
])
def test_api_signal_absent(text):
    assert not infer_api_signal(text)


# --- is_closer_step ---------------------------------------------------------

def test_closer_texts_recognized():
    assert is_closer_step(qpl.century_trace_step()[0])
    assert is_closer_step(qpl.genesis_verify_step()[0])
    assert is_closer_step(qpl.db_table_validation_step('')[0])
    assert is_closer_step(qpl.db_table_validation_step('activation')[0])


def test_db_table_closer_as_seen_in_outputs():
    text = ('Verify the transaction update in the following NSL DB tables happen '
            'successfully:\nNBOP_MIG_SIM\nNBOP_MIG_DEVICE')
    assert is_closer_step(text)


@pytest.mark.parametrize('text', [
    UI_STEP,
    'Verify the transaction update in the NBOP History page',
    'User searched the transaction logs',
    '',
])
def test_non_closer_not_recognized(text):
    assert not is_closer_step(text)


# --- is_ui_feature ----------------------------------------------------------

def test_ui_classification_is_ui():
    assert is_ui_feature(_suite('ui', [API_STEP]))


def test_hybrid_without_api_is_ui():
    assert is_ui_feature(_suite('hybrid', [UI_STEP]))


def test_hybrid_with_only_closers_flagged_is_ui():
    # The db-table closer mentions NSL; closers are ignored by the API check.
    closers = [t for t, _ in qpl.provisioning_closer()]
    assert is_ui_feature(_suite('hybrid', [UI_STEP] + closers))


def test_hybrid_with_api_is_not_ui():
    assert not is_ui_feature(_suite('hybrid', [UI_STEP, API_STEP]))


def test_api_classification_is_not_ui():
    assert not is_ui_feature(_suite('api', [UI_STEP]))


def test_missing_routing_audit_is_not_ui():
    assert not is_ui_feature(_suite('ui', [UI_STEP], audit=False))
