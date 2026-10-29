"""Unit tests for modules/analyst_validity — build_feature_evidence + scenario_is_valid.

Pure / cache-free: every subject is constructed in memory (direct-construction style of
tests/test_banned_backstop_cr.py). Covers keep/drop/flag across the five analyst subject
kinds (D1 state-transition, D2 partial-failure, A1 field-validation, A2 idempotency,
A3 concurrency) plus the grounded always-keep case.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.data_models_v8 import ExtractedScenario, TestStep  # noqa: E402
from modules.traceability import TraceabilityRecord  # noqa: E402
from modules.analyst_validity import (  # noqa: E402
    build_feature_evidence, scenario_is_valid, is_analyst,
)

FID = 'MWTGPROV-9001'


# ── Minimal in-memory jira / chalk stand-ins ───────────────────────────────────
class _Jira:
    def __init__(self, summary='', description='', acceptance_criteria=''):
        self.key = FID
        self.summary = summary
        self.description = description
        self.acceptance_criteria = acceptance_criteria


def _tr(source_id, source_type='Business Rule'):
    return TraceabilityRecord(source_type=source_type, source_id=source_id,
                              extracted_text='x', confidence=0.9)


def _scn(source_id, title='', validation='', steps=None, source_type='Business Rule'):
    s = ExtractedScenario(title=title, validation=validation, category='Happy Path',
                          source=_tr(source_id, source_type))
    s.steps_hint = steps or []
    return s


# ── build_feature_evidence ─────────────────────────────────────────────────────
def test_build_feature_evidence_is_lowercased_and_includes_ac():
    jira = _Jira(summary='Change Line Status', description='Suspend an ACTIVE line',
                 acceptance_criteria=['Given Pending Port, reject the request'])
    blob = build_feature_evidence(jira, chalk=None)
    assert blob == blob.lower()
    assert 'change line status' in blob
    assert 'active line' in blob
    assert 'pending port' in blob


def test_build_feature_evidence_tolerates_none():
    assert build_feature_evidence(None, None) == ''


# ── grounded always-keep ────────────────────────────────────────────────────────
def test_grounded_scenario_always_kept():
    scn = _scn('%s_scenario_3' % FID, source_type='Chalk Scenario',
               title='Verify activation succeeds')
    assert not is_analyst(scn)
    verdict, _ = scenario_is_valid(scn, evidence_blob='anything')
    assert verdict == 'keep'


# ── D1 State-Transition-Matrix ───────────────────────────────────────────────────
def test_state_transition_keep_when_state_in_evidence():
    scn = _scn('State-Transition-Matrix-%s' % FID,
               title='Verify op succeeds for line in Active state')
    assert is_analyst(scn)
    verdict, _ = scenario_is_valid(scn, 'feature suspends an active line')
    assert verdict == 'keep'


def test_state_transition_flag_when_only_subject_has_state():
    scn = _scn('State-Transition-Matrix-%s' % FID,
               title='Verify op rejected for Suspended line')
    verdict, _ = scenario_is_valid(scn, 'generic feature with no status words')
    assert verdict == 'flag'


def test_state_transition_drop_when_no_state_anywhere():
    scn = _scn('State-Transition-Matrix-%s' % FID, title='Verify op behavior')
    verdict, _ = scenario_is_valid(scn, 'a feature about report formatting only')
    assert verdict == 'drop'


# ── D2 Partial-Failure-Matrix ────────────────────────────────────────────────────
def test_partial_failure_keep_when_downstream_in_evidence():
    scn = _scn('Partial-Failure-Matrix-%s' % FID,
               title='Verify rollback when Kafka publish fails')
    # Evidence names both a downstream token AND a provisioning concept (concept_grounded).
    verdict, _ = scenario_is_valid(
        scn, 'nsl provisions the subscriber line; kafka event emitted downstream')
    assert verdict == 'keep'


def test_partial_failure_drop_when_no_downstream():
    scn = _scn('Partial-Failure-Matrix-%s' % FID, title='Verify partial failure handling')
    verdict, _ = scenario_is_valid(scn, 'a simple field formatting feature')
    assert verdict == 'drop'


# ── A1 Field-Validation-Matrix ───────────────────────────────────────────────────
def test_field_validation_keep_when_field_in_evidence():
    scn = ExtractedScenario(
        title="Verify 'MMS_TYPE_INDICATOR' rejects invalid value",
        validation='invalid value rejected', category='Negative',
        source=_tr('Field-Validation-Matrix-%s' % FID))
    verdict, _ = scenario_is_valid(
        scn, 'the mms type indicator field must be validated on input')
    assert verdict == 'keep'


def test_field_validation_drop_when_field_absent():
    scn = ExtractedScenario(
        title="Verify 'HOST_MDN_NETWORK' rejects invalid value",
        validation='invalid value rejected', category='Negative',
        source=_tr('Field-Validation-Matrix-%s' % FID))
    verdict, _ = scenario_is_valid(scn, 'the mms type indicator field is validated')
    assert verdict == 'drop'


# ── A2 Idempotency ───────────────────────────────────────────────────────────────
def test_idempotency_keep_when_evidence_implies_idempotency():
    scn = _scn('Idempotency-%s' % FID, title='Verify duplicate request is idempotent')
    verdict, _ = scenario_is_valid(scn, 'a retry of the same request must be idempotent')
    assert verdict == 'keep'


def test_idempotency_drop_when_no_idempotency_evidence():
    scn = _scn('Idempotency-%s' % FID, title='Verify idempotent behavior')
    verdict, _ = scenario_is_valid(scn, 'a plain one-shot activation feature')
    assert verdict == 'drop'


# ── A3 Concurrency ───────────────────────────────────────────────────────────────
def test_concurrency_keep_when_evidence_implies_concurrency():
    scn = _scn('Concurrency-%s' % FID, title='Verify concurrent requests handled')
    verdict, _ = scenario_is_valid(scn, 'two simultaneous parallel requests may race')
    assert verdict == 'keep'


def test_concurrency_drop_when_no_concurrency_evidence():
    scn = _scn('Concurrency-%s' % FID, title='Verify concurrency handling')
    verdict, _ = scenario_is_valid(scn, 'a single sequential provisioning flow')
    assert verdict == 'drop'
