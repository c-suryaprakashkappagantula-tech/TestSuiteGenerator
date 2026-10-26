"""QMetry-authored TCs must NOT receive the mined provisioning closer.

Blocking review finding (feat/qmetry-project-agnostic): the 4074 output shipped
fabricated NBOP_MIG_* / *_HISTORY DB-table verification steps because
qmetry_pattern_library.enrich_test_case appended the universal provisioning closer
to a TC whose steps were authored verbatim from the linked subtask's QMetry folder.
A QMetry-authored TC's steps ARE the ground truth, so the house-style closer
(Century-DB trace -> NBOP_MIG_* DB-table validation -> Genesis verify) must be
suppressed for it. A grounded (Chalk-generated) provisioning TC still gets it.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import qmetry_pattern_library as qpl  # noqa: E402
from modules.data_models_v8 import TestCase, TestStep  # noqa: E402

# A wearable provisioning feature context: is_provisioning_feature() is True, so a
# grounded TC would normally get the NBOP_MIG_* closer. 4074 is exactly this shape.
WEARABLE_CTX = '60.2: TMO - Wearable audit report processing MWTGPROV-4074'

AUTHORED_STEP_A = 'Upload the CHRTR wearable audit delta file to the SFTP inbound path.'
AUTHORED_STEP_B = 'Verify the wearable audit report is processed and reconciled.'


def _authored_tc():
    """A TC whose steps came verbatim from QMetry authored content (authored=True)."""
    steps = [
        TestStep(step_num=1, summary=AUTHORED_STEP_A, authored=True, origin='authored'),
        TestStep(step_num=2, summary=AUTHORED_STEP_B, authored=True, origin='authored'),
    ]
    return TestCase(sno='TC_001', summary='Process wearable audit report', steps=steps)


def _grounded_tc():
    """A generated-from-Chalk provisioning TC (no authored provenance)."""
    steps = [
        TestStep(step_num=1, summary='Trigger the wearable activation API.', origin='grounded'),
        TestStep(step_num=2, summary='Verify the line is activated.', origin='grounded'),
    ]
    return TestCase(sno='TC_002', summary='Activate wearable line', steps=steps)


def _joined_summaries(tc):
    return '\n'.join(s.summary for s in tc.steps)


def test_qmetry_authored_tc_gets_no_closer():
    tc = _authored_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=WEARABLE_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    # No closer steps appended.
    assert len(tc.steps) == before
    blob = _joined_summaries(tc)
    assert 'NBOP_MIG_' not in blob
    assert 'TRANSACTION_HISTORY' not in blob
    assert 'LINE_HISTORY' not in blob
    assert 'century DB' not in blob


def test_qmetry_authored_detector_true_when_any_step_authored():
    tc = _authored_tc()
    assert qpl._tc_is_qmetry_authored(tc) is True


def test_grounded_detector_false():
    assert qpl._tc_is_qmetry_authored(_grounded_tc()) is False


def test_grounded_provisioning_tc_still_gets_closer():
    """Regression guard: the suppression must NOT strip legitimate closers from a
    grounded NSL-provisioning TC — only authored (QMetry) / mediation TCs are exempt.
    """
    tc = _grounded_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=WEARABLE_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    assert len(tc.steps) > before  # closer steps were appended
    assert 'NBOP_MIG_' in _joined_summaries(tc)


# --- Mediation / PRR grounded TC must not get the NSL provisioning closer ----
# 4074 regression: the feature title "wearable Audit Report" matches a provisioning
# hint, but its TCs are PRR/Amdocs mediation flows. The NSL NBOP_MIG closer is
# fabricated for them and must be suppressed even though they are NOT QMetry-authored.

MEDIATION_STEP_A = 'Place/ingest a representative TMO PRR input file onto the biller SFTP.'
MEDIATION_STEP_B = 'Run the TMO_PRR_Processing mediation flow and let Mediation process the file.'
MEDIATION_STEP_C = 'Retrieve the processed Amdocs TMO billing output record(s) for the test data.'


def _mediation_grounded_tc():
    steps = [
        TestStep(step_num=1, summary=MEDIATION_STEP_A, origin='grounded'),
        TestStep(step_num=2, summary=MEDIATION_STEP_B, origin='grounded'),
        TestStep(step_num=3, summary=MEDIATION_STEP_C, origin='grounded'),
    ]
    return TestCase(sno='TC_003', summary='Verify existing report ingestion is not impacted',
                    steps=steps)


def test_mediation_grounded_tc_gets_no_nsl_closer():
    tc = _mediation_grounded_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=WEARABLE_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    assert len(tc.steps) == before
    blob = _joined_summaries(tc)
    assert 'NBOP_MIG_' not in blob
    assert 'TRANSACTION_HISTORY' not in blob
    assert 'century DB' not in blob


def test_mediation_detector_true_from_step_content():
    assert qpl._tc_is_mediation(_mediation_grounded_tc(), WEARABLE_CTX) is True


def test_mediation_detector_false_for_plain_provisioning_tc():
    assert qpl._tc_is_mediation(_grounded_tc(), WEARABLE_CTX) is False
