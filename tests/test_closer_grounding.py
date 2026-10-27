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


# ── Integration-contract downstream-step gate (root-cause fix, iter 2) ────────
# The banned Service-Grouping / Century-Report / NBOP-MIG / Syniverse / Genesis
# content on the 5 dirty features is fabricated by the integration-contract
# downstream producer (tc_builder -> build_downstream_verification_steps) and the
# dimension_extractor D1/D2 matrices — NOT by the closer path. ground_downstream_steps
# applies the SAME grounded-only policy to that producer's output.

# Representative downstream-verification steps as build_downstream_verification_steps
# emits them (space-form 'NBOP MIG tables' / 'Service Grouping' / 'TMO Genesis').
_DOWNSTREAM_STEPS = [
    ('Verify Service Grouping (Century Report) shows outbound calls to: Apollo NE, TMO',
     'Century Report Service Grouping confirms successful calls with SUCCESS status.'),
    ('Verify Syniverse CreateSubscriber call is triggered and logged in Service Grouping',
     'Syniverse CreateSubscriber call present in Century Report.'),
    ('Verify NBOP MIG tables reflect the change (Device, SIM, Line, Feature, Transaction History)',
     'NBOP MIG tables updated correctly — Transaction History row present.'),
    ('Verify TMO Genesis portal reflects the updated subscriber state',
     'TMO Genesis portal shows the subscriber with the correct post-operation state.'),
]


def test_ground_downstream_steps_suppressed_when_ungrounded():
    # A plain API feature whose evidence never mentions NSL/NBOP/Service-Grouping/
    # Century/Syniverse → the whole downstream block is suppressed.
    evidence = ('rest wired update key info send a request update the key info '
                'field verify the response returns 200 with the updated value')
    out = qpl.ground_downstream_steps(_DOWNSTREAM_STEPS, evidence)
    assert out == [], 'ungrounded downstream steps must be fully suppressed'


def test_ground_downstream_steps_kept_when_grounded():
    # A genuinely grounded NSL-provisioning feature keeps its legitimate steps.
    evidence = ('nbop line activation provisioning the line is provisioned on nsl '
                'and nbop_mig tables are updated, syniverse createsubscriber, century report')
    out = qpl.ground_downstream_steps(_DOWNSTREAM_STEPS, evidence)
    assert len(out) == len(_DOWNSTREAM_STEPS), 'grounded downstream steps must survive'


def test_ground_downstream_steps_per_step_backstop():
    # Primary gate passes via the 'service grouping' + 'century' concepts, but the
    # NSL/NBOP/Syniverse/Genesis concepts are NOT grounded → the Service-Grouping and
    # Century-Report step survives while the NBOP-MIG / Syniverse / Genesis steps are
    # dropped by the per-step backstop. (Note: the NSL-provisioning stack groups nbop/
    # nsl/provision/syniverse/genesis together, so a feature grounded on 'nbop' keeps
    # its Syniverse step by design — this test isolates a concept that does NOT.)
    evidence = ('service grouping and century report are validated for this '
                'reconciliation feature')
    out = qpl.ground_downstream_steps(_DOWNSTREAM_STEPS, evidence)
    joined = '\n'.join('%s %s' % (s, e) for s, e in out)
    assert 'NBOP MIG tables' not in joined, 'ungrounded NBOP MIG step must be dropped by backstop'
    assert 'Genesis' not in joined, 'ungrounded Genesis step must be dropped by backstop'
    assert 'Service Grouping' in joined, 'grounded Service Grouping step must survive'


def test_concept_grounded_public_predicate():
    assert qpl.concept_grounded('line provisioned on nsl and nbop') is True
    assert qpl.concept_grounded('rest request update key info field') is False
    assert qpl.concept_grounded('') is False


def test_ground_downstream_steps_empty_input():
    assert qpl.ground_downstream_steps([], 'line provisioned on nbop') == []
    assert qpl.ground_downstream_steps(None, 'line provisioned on nbop') == []


# ── StepAssembler single-choke-point backstop (root-cause fix, iter 2) ────────
# The banned content on the 5 dirty features is fabricated by SEVERAL producers
# (integration-contract downstream steps, step_templates chains, test_analyst D1/D2
# matrices). StepAssembler.reconcile() runs AFTER every producer and strips any banned
# step whose NSL/NBOP provisioning concept is NOT grounded in the feature's own evidence —
# the comprehensive net that makes a non-grounded suite ship with banned_total == 0.
from modules.step_assembler import StepAssembler  # noqa: E402
from modules.tc_builder import FeatureClassification  # noqa: E402


def _reconcile_suite(suite, classification='api'):
    StepAssembler(log=None).reconcile(
        suite, FeatureClassification(classification=classification), log=None)
    return suite


def test_step_assembler_strips_banned_on_ungrounded_feature():
    # A plain API feature whose evidence never mentions NSL/NBOP but whose steps were
    # fabricated with Century / Service Grouping / NBOP MIG content (as any upstream
    # producer might emit). The backstop drops them all.
    steps = [
        TestStep(step_num=1, summary='Send a REST request to update the key info field.',
                 expected='The field is updated.', origin='grounded'),
        TestStep(step_num=2,
                 summary='Verify Service Grouping (Century Report) shows outbound calls.',
                 expected='Century Report Service Grouping confirms calls.', origin='grounded'),
        TestStep(step_num=3,
                 summary='Verify NBOP MIG tables reflect the change (Transaction History).',
                 expected='NBOP MIG tables updated; Transaction History row present.',
                 origin='grounded'),
    ]
    tc = TestCase(sno='TC_SA1', summary='Update key info via REST', steps=steps)
    suite = TestSuite(feature_id='MWTGPROV-4574', feature_title='REST wired update key info',
                      feature_desc='Update subscriber key info over the wired REST API.',
                      test_cases=[tc],
                      acceptance_criteria=['A valid request updates the key info and returns 200.'],
                      scope='Update key info field via the wired REST endpoint.')
    _reconcile_suite(suite)
    assert len(tc.steps) == 1, 'all fabricated banned steps must be stripped'
    _assert_no_banned(tc)


def test_step_assembler_keeps_banned_on_grounded_feature():
    # A genuinely grounded NSL-provisioning feature keeps its legitimate downstream steps.
    steps = [
        TestStep(step_num=1, summary='Trigger the activation API to provision the line on NSL.',
                 expected='Line provisioned on NSL.', origin='grounded'),
        TestStep(step_num=2,
                 summary='Verify NBOP MIG tables reflect the change (Transaction History).',
                 expected='NBOP MIG tables updated; Transaction History row present.',
                 origin='grounded'),
    ]
    tc = TestCase(sno='TC_SA2', summary='Activate line via NSL provisioning', steps=steps)
    suite = TestSuite(feature_id='MWTGPROV-9999',
                      feature_title='NBOP line activation provisioning on NSL',
                      feature_desc='Provision a new line on the NSL/NBOP stack.',
                      test_cases=[tc],
                      acceptance_criteria=['The line is provisioned on NSL and NBOP_MIG tables updated.'],
                      scope='Line activation through the NSL provisioning flow.')
    before = len(tc.steps)
    _reconcile_suite(suite)
    assert len(tc.steps) == before, 'legitimate grounded steps must survive'
    assert 'NBOP MIG' in _blob(tc), 'grounded NBOP MIG step must survive'


def test_step_assembler_never_strips_authored_banned_step():
    # An authored (QMetry ground-truth) step that names a banned table is NEVER stripped,
    # even on an otherwise-ungrounded feature — authored content is ground truth.
    steps = [
        TestStep(step_num=1, summary='Send a REST request to update the key info field.',
                 expected='The field is updated.', origin='grounded'),
        TestStep(step_num=2,
                 summary='Verify NBOP_MIG_LINE table is updated per the authored suite.',
                 expected='NBOP_MIG_LINE reflects the change.',
                 authored=True, origin='authored'),
    ]
    tc = TestCase(sno='TC_SA3', summary='Update key info via REST', steps=steps)
    suite = TestSuite(feature_id='MWTGPROV-4574', feature_title='REST wired update key info',
                      feature_desc='Update subscriber key info over the wired REST API.',
                      test_cases=[tc],
                      acceptance_criteria=['A valid request updates the key info and returns 200.'],
                      scope='Update key info field via the wired REST endpoint.')
    _reconcile_suite(suite)
    blob = _blob(tc)
    assert 'NBOP_MIG_LINE' in blob, 'authored banned step must be preserved (ground truth)'
    assert len(tc.steps) == 2
