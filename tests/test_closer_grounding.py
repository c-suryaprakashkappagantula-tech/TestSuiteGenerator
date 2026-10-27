"""GROUNDED-ONLY closer injection (fix/closer-grounding) — root-cause fix.

The universal NSL provisioning CLOSER (Century-DB trace -> NBOP_MIG_* DB-table
validation -> Genesis/NE-Portal verify) must be injected into a TC ONLY when the
NSL/NBOP provisioning concept is actually grounded in THIS feature's own evidence
(Chalk scope/rules, Jira AC, QMetry-authored/generated steps for this feature).

Previously the closer was injected whenever a feature's *title* matched a
provisioning keyword (is_provisioning_feature), which fabricated
NBOP_MIG/TRANSACTION_HISTORY/Service-Grouping/Century/NE-Portal steps onto plain
API features (4497/4574/4575/4576/4611) whose own evidence never mentions those
concepts. These three tests pin the inverted policy:

  (a) a non-grounded API feature gets NO NBOP/SG/Century closer;
  (b) a genuinely grounded NSL-provisioning TC still gets its legitimate closer;
  (c) mediation + QMetry-authored paths stay clean (no closer, no banned content).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import qmetry_pattern_library as qpl  # noqa: E402
from modules.data_models_v8 import TestCase, TestStep, TestSuite  # noqa: E402

_BANNED = ('NBOP_MIG_', 'TRANSACTION_HISTORY', 'LINE_HISTORY',
           'Service Grouping', 'Century', 'NE Portal', 'century DB',
           'Genesis Portal')


def _blob(tc):
    return '\n'.join(
        '%s %s' % (s.summary or '', s.expected or '') for s in tc.steps)


def _assert_no_banned(tc):
    blob = _blob(tc)
    for token in _BANNED:
        assert token not in blob, 'fabricated closer content leaked: %r' % token


# ── (a) Non-grounded API feature gets NO NBOP/SG/Century closer ───────────────
# A line/activation feature (title matches is_provisioning_feature) whose context,
# AC, scope and TC steps never mention NSL/NBOP/Service-Grouping/Century/NE-Portal.
API_CTX = '80.2: TMO - REST wired update key info MWTGPROV-4574'


def _non_grounded_api_tc():
    steps = [
        TestStep(step_num=1, summary='Send a REST request to update the key info field.',
                 origin='grounded'),
        TestStep(step_num=2, summary='Verify the response returns HTTP 200 with the updated value.',
                 origin='grounded'),
    ]
    return TestCase(sno='TC_A', summary='Update key info via REST', steps=steps)


def test_non_grounded_api_feature_gets_no_closer():
    # Suite-level evidence (AC/scope) also carries NO NSL/NBOP concept.
    tc = _non_grounded_api_tc()
    suite = TestSuite(
        feature_id='MWTGPROV-4574',
        feature_title='REST wired update key info',
        feature_desc='Update the subscriber key info over the wired REST API.',
        test_cases=[tc],
        acceptance_criteria=['Given a valid request, the key info is updated and 200 returned.'],
        scope='Update key info field via the wired REST endpoint.',
    )
    before = len(tc.steps)
    qpl.enrich_suite(suite, add_closers=True, fix_preconditions=False,
                     pair_century=False, log=None)
    assert len(tc.steps) == before, 'no closer steps should have been appended'
    _assert_no_banned(tc)


def test_non_grounded_api_feature_direct_call_gets_no_closer():
    # Same via enrich_test_case directly (empty feature_evidence → gate uses TC text).
    tc = _non_grounded_api_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=API_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    assert len(tc.steps) == before
    _assert_no_banned(tc)


# ── (b) Grounded NSL-provisioning TC still gets its legitimate closer ─────────
NSL_CTX = '80.2: TMO - NBOP line activation provisioning MWTGPROV-4600'


def _grounded_nsl_tc():
    steps = [
        TestStep(step_num=1, summary='Trigger the activation API to provision the line on NSL.',
                 origin='grounded'),
        TestStep(step_num=2, summary='Verify the line is provisioned and reflected in NBOP.',
                 origin='grounded'),
    ]
    return TestCase(sno='TC_B', summary='Activate line via NSL provisioning', steps=steps)


def test_grounded_nsl_provisioning_tc_still_gets_closer():
    tc = _grounded_nsl_tc()
    suite = TestSuite(
        feature_id='MWTGPROV-4600',
        feature_title='NBOP line activation provisioning',
        feature_desc='Provision a new line on the NSL/NBOP stack.',
        test_cases=[tc],
        acceptance_criteria=['The line is provisioned on NSL and NBOP_MIG tables are updated.'],
        scope='Line activation through the NSL provisioning flow.',
    )
    before = len(tc.steps)
    qpl.enrich_suite(suite, add_closers=True, fix_preconditions=False,
                     pair_century=False, log=None)
    assert len(tc.steps) > before, 'legitimate grounded closer must still be appended'
    assert 'NBOP_MIG_' in _blob(tc), 'legitimate NBOP_MIG closer must survive'


# ── (c) Mediation + QMetry-authored paths stay clean ──────────────────────────
WEARABLE_CTX = '60.2: TMO - Wearable audit report processing MWTGPROV-4074'


def _mediation_grounded_tc():
    steps = [
        TestStep(step_num=1,
                 summary='Place/ingest a representative TMO PRR input file onto the biller SFTP.',
                 origin='grounded'),
        TestStep(step_num=2,
                 summary='Run the TMO_PRR_Processing mediation flow and let Mediation process the file.',
                 origin='grounded'),
        TestStep(step_num=3,
                 summary='Retrieve the processed Amdocs TMO billing output record(s) for the test data.',
                 origin='grounded'),
    ]
    return TestCase(sno='TC_C1', summary='Verify existing report ingestion is not impacted',
                    steps=steps)


def _authored_tc():
    steps = [
        TestStep(step_num=1,
                 summary='Upload the CHRTR wearable audit delta file to the SFTP inbound path.',
                 authored=True, origin='authored'),
        TestStep(step_num=2,
                 summary='Verify the wearable audit report is processed and reconciled.',
                 authored=True, origin='authored'),
    ]
    return TestCase(sno='TC_C2', summary='Process wearable audit report', steps=steps)


def test_mediation_path_stays_clean():
    tc = _mediation_grounded_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=WEARABLE_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    assert len(tc.steps) == before
    _assert_no_banned(tc)


def test_qmetry_authored_path_stays_clean():
    tc = _authored_tc()
    before = len(tc.steps)
    qpl.enrich_test_case(tc, feature_context=WEARABLE_CTX, add_closers=True,
                         fix_preconditions=False, pair_century=False)
    assert len(tc.steps) == before
    _assert_no_banned(tc)


# ── Unit coverage for the new grounding predicates ────────────────────────────
def test_closer_concept_grounded_predicate():
    assert qpl._closer_concept_grounded('line is provisioned on nsl and nbop') is True
    assert qpl._closer_concept_grounded('send a rest request to update key info') is False
    assert qpl._closer_concept_grounded('') is False


def test_step_banned_and_ungrounded_backstop():
    # NBOP_MIG step with NO nbop/nsl grounding → banned.
    assert qpl._step_is_banned_and_ungrounded(
        'Verify NBOP_MIG_LINE updated', 'rest request update key info') is True
    # Same step, grounded by nbop in evidence → allowed.
    assert qpl._step_is_banned_and_ungrounded(
        'Verify NBOP_MIG_LINE updated', 'line provisioned on nbop') is False
    # A step with no banned phrase is never flagged.
    assert qpl._step_is_banned_and_ungrounded(
        'Send a REST request.', 'rest request update key info') is False
