"""Unit tests for modules/step_assembler.py (FEAT-002, findings.md §8.1-8.5).

Pure in-memory tests: they build TestSuite/TestCase/TestStep objects directly and call
StepAssembler.reconcile() — no cache DB, no Jira, no Chalk, no browser. These guard the
assembler contract the engine relies on:

  * semantic step dedup keeps the higher-origin step and drops a redundant restatement
    (the TC01-shaped spliced BAU assert collapses against the grounded inspect step, and
    its orphaned obligation SET goes too);
  * a Chalk-authored precondition (precond_from_chalk=True) is never overwritten;
  * a batch_file classification stamps the SFTP/batch preconditions once per TC;
  * authored step order is preserved (no reordering, no drops);
  * each TC's obligation_ids are recomputed to match only the surviving steps.
"""
from modules.data_models_v8 import TestStep, TestCase, TestSuite
from modules.tc_builder import FeatureClassification
from modules.step_assembler import StepAssembler, _BATCH_PRECONDITIONS


def _quiet(*_a, **_k):
    """Swallow assembler logging so test output stays readable."""


def _reconcile(tc, classification=None):
    """Reconcile a one-TC suite and return the (possibly mutated) TC."""
    suite = TestSuite(feature_id='TEST', test_cases=[tc])
    StepAssembler(log=_quiet).reconcile(
        suite, classification or FeatureClassification(classification='api'), log=_quiet)
    return suite.test_cases[0]


# ── (a) semantic step dedup ────────────────────────────────────────────────

def test_tc01_shaped_input_collapses_to_grounded_assert_only():
    """Two steps assert call_direction_ind=BAU: a grounded inspect step and a weaker
    spliced obligation restatement. The grounded step wins; the obligation assert AND its
    paired (same-obligation) SET step are both dropped."""
    steps = [
        TestStep(step_num=1, summary='Prepare PRR batch file', origin='grounded'),
        TestStep(step_num=2, summary='Ingest PRR file over SFTP', origin='grounded'),
        TestStep(step_num=3, summary='Retrieve mediation output', origin='grounded'),
        TestStep(step_num=4, summary='Inspect call_direction_ind on the CDR',
                 expected='call_direction_ind is BAU (unchanged)',
                 expected_field='call_direction_ind', expected_value='BAU',
                 origin='grounded'),
        # weaker-origin spliced obligation pair (SET then VERIFY) — the redundant restatement
        TestStep(step_num=5, summary='Set MMS_TYPE_INDICATOR = other/absent',
                 input_field='MMS_TYPE_INDICATOR', input_value='other/absent',
                 obligation_id='MMS_TYPE_INDICATOR:other/absent', origin='obligation'),
        TestStep(step_num=6, summary='Verify call_direction_ind = BAU',
                 expected='call_direction_ind = BAU',
                 expected_field='call_direction_ind', expected_value='BAU',
                 obligation_id='MMS_TYPE_INDICATOR:other/absent', origin='obligation'),
    ]
    tc = TestCase(summary='TC01 BAU default', steps=list(steps))
    tc = _reconcile(tc)

    summaries = [s.summary for s in tc.steps]
    # the grounded 4-step PRR chain survives intact
    assert 'Inspect call_direction_ind on the CDR' in summaries
    # the redundant obligation VERIFY is gone, and so is its orphaned SET partner
    assert 'Verify call_direction_ind = BAU' not in summaries
    assert 'Set MMS_TYPE_INDICATOR = other/absent' not in summaries
    assert len(tc.steps) == 4
    # steps renumbered contiguously
    assert [s.step_num for s in tc.steps] == [1, 2, 3, 4]


def test_grounded_step_never_dropped_in_favour_of_obligation():
    """Even if the obligation step comes first in the list, the grounded step wins the tie
    and is the one kept."""
    tc = TestCase(summary='order independence', steps=[
        TestStep(step_num=1, summary='Verify field X = V', expected='X = V',
                 expected_field='X', expected_value='V', origin='obligation'),
        TestStep(step_num=2, summary='Inspect X grounded', expected='X is V',
                 expected_field='X', expected_value='V', origin='grounded'),
    ])
    tc = _reconcile(tc)
    assert len(tc.steps) == 1
    assert tc.steps[0].origin == 'grounded'


def test_steps_with_no_concrete_expected_value_are_never_deduped():
    """Setup/action steps that assert nothing concrete are always preserved."""
    tc = TestCase(summary='no-assert steps', steps=[
        TestStep(step_num=1, summary='Prepare environment', origin='grounded'),
        TestStep(step_num=2, summary='Trigger ingest', origin='grounded'),
        TestStep(step_num=3, summary='Retrieve output', origin='grounded'),
    ])
    tc = _reconcile(tc)
    assert len(tc.steps) == 3


# ── (c) preconditions from classification ───────────────────────────────────

def test_batch_file_classification_sets_sftp_preconditions():
    tc = TestCase(summary='mediation scenario', category='Happy Path',
                  preconditions='1. Generic placeholder',
                  steps=[TestStep(step_num=1, summary='do thing', origin='grounded')])
    tc = _reconcile(tc, FeatureClassification(classification='batch_file'))
    for line in _BATCH_PRECONDITIONS:
        assert line in tc.preconditions
    assert 'SFTP server is accessible' in tc.preconditions


def test_chalk_authored_precondition_is_never_overwritten():
    """precond_from_chalk=True is ground truth (Rule #1) — the assembler leaves it alone
    even for a batch_file feature."""
    tc = TestCase(summary='chalk-authored precond', category='Happy Path',
                  preconditions='1. An ACTIVE TMO Phone host line (NETWORK_PROVIDER=TMO).',
                  steps=[TestStep(step_num=1, summary='do thing', origin='grounded')])
    setattr(tc, 'precond_from_chalk', True)
    original = tc.preconditions
    tc = _reconcile(tc, FeatureClassification(classification='batch_file'))
    assert tc.preconditions == original


def test_non_batch_classification_leaves_preconditions_untouched():
    tc = TestCase(summary='api feature', category='Happy Path',
                  preconditions='1. API endpoint accessible',
                  steps=[TestStep(step_num=1, summary='call api', origin='grounded')])
    tc = _reconcile(tc, FeatureClassification(classification='api'))
    assert tc.preconditions == '1. API endpoint accessible'


# ── (b) authored order preserved ────────────────────────────────────────────

def test_authored_step_order_is_preserved():
    """Authored sequences are kept verbatim and in order — no reordering, no drops."""
    tc = TestCase(summary='authored sequence', steps=[
        TestStep(step_num=1, summary='Authored step one', authored=True, origin='authored'),
        TestStep(step_num=2, summary='Authored step two', authored=True, origin='authored'),
        TestStep(step_num=3, summary='Authored step three', authored=True, origin='authored'),
    ])
    tc = _reconcile(tc)
    assert [s.summary for s in tc.steps] == [
        'Authored step one', 'Authored step two', 'Authored step three']


# ── (d) obligation_ids recomputed to surviving steps ────────────────────────

def test_obligation_ids_reflect_only_surviving_steps():
    """After the redundant obligation pair is collapsed, the TC should no longer claim the
    collapsed obligation id via any surviving step."""
    tc = TestCase(summary='obligation survivors', steps=[
        TestStep(step_num=1, summary='Inspect X grounded', expected='X is V',
                 expected_field='X', expected_value='V', origin='grounded'),
        TestStep(step_num=2, summary='Set X = V', input_field='X', input_value='V',
                 obligation_id='X:V', origin='obligation'),
        TestStep(step_num=3, summary='Verify X = V', expected='X = V',
                 expected_field='X', expected_value='V', obligation_id='X:V',
                 origin='obligation'),
    ])
    tc = _reconcile(tc)
    surviving_oids = {s.obligation_id for s in tc.steps if s.obligation_id}
    assert 'X:V' not in surviving_oids
    assert len(tc.steps) == 1
