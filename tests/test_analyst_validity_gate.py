"""Tests for modules/analyst_validity.apply_analyst_validity_gate on a combined suite.

Cache-free: a TestSuite is built in memory mixing grounded TCs (Chalk / Jira source) and
analyst TCs (one valid state-transition, one unsupported concurrency). Asserts grounded TCs
survive, the unsupported analyst TC is dropped-or-flagged, duplicates collapse, and the
Chalk-grounded TCs stay present after risk ordering. Direct-construction style of
tests/test_banned_backstop_cr.py.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.data_models_v8 import TestCase, TestStep, TestSuite  # noqa: E402
from modules.traceability import TraceabilityRecord  # noqa: E402
from modules.analyst_validity import apply_analyst_validity_gate, is_analyst  # noqa: E402

FID = 'MWTGPROV-9100'


class _Jira:
    def __init__(self):
        self.key = FID
        self.summary = 'Suspend an Active subscriber line'
        self.description = 'When the line is Active, suspend it; reject when Suspended.'
        self.acceptance_criteria = ['Given an Active line, suspend succeeds.']


def _tr(source_id, source_type='Chalk Scenario'):
    return TraceabilityRecord(source_type=source_type, source_id=source_id,
                              extracted_text='x', confidence=1.0)


def _steps(*summaries):
    return [TestStep(step_num=i + 1, summary=s, expected='ok')
            for i, s in enumerate(summaries)]


def _distinct_steps(title):
    # Derive unique step text from the title so dedup_and_merge does not collapse
    # genuinely-distinct TCs that would otherwise share boilerplate steps.
    return _steps('Set up line for: %s' % title,
                  'Trigger the operation described by: %s' % title,
                  'Verify the outcome for: %s' % title)


def _grounded_tc(sno, title, source_id, source_type='Chalk Scenario', steps=None):
    tc = TestCase(sno=sno, summary=title, description=title, category='Happy Path',
                  steps=steps or _distinct_steps(title))
    tc.traceability = _tr(source_id, source_type)
    return tc


def _analyst_tc(sno, title, source_id, steps=None, category='Happy Path'):
    tc = TestCase(sno=sno, summary=title, description=title, category=category,
                  steps=steps or _distinct_steps(title))
    tc.traceability = _tr(source_id, 'Business Rule')
    return tc


def _suite(test_cases):
    return TestSuite(feature_id=FID, feature_title='Suspend an Active subscriber line',
                     feature_desc='When the line is Active, suspend it; reject when Suspended.',
                     acceptance_criteria=['Given an Active line, suspend succeeds.'],
                     scope='Suspend an active line.', test_cases=test_cases)


def _summaries(suite):
    return [tc.summary for tc in suite.test_cases]


def test_grounded_kept_unsupported_analyst_dropped_or_flagged():
    chalk_tc = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                            '%s_scenario_1' % FID)
    jira_tc = _grounded_tc('TC02', 'Verify AC: active line suspends', FID,
                           source_type='Jira AC')
    # Valid analyst: state-transition with a status token present in the evidence.
    valid_state = _analyst_tc('TC03', 'Verify suspend rejected for Suspended line',
                              'State-Transition-Matrix-%s' % FID, category='Negative')
    # Unsupported analyst: concurrency with NO concurrency evidence on the feature.
    bad_conc = _analyst_tc('TC04', 'Verify concurrent suspend requests handled',
                           'Concurrency-%s' % FID)

    suite = _suite([chalk_tc, jira_tc, valid_state, bad_conc])
    apply_analyst_validity_gate(suite, _Jira(), chalk=None, log=None)

    summaries = _summaries(suite)
    # Grounded TCs always survive.
    assert 'Verify suspend succeeds for Active line' in summaries
    assert 'Verify AC: active line suspends' in summaries
    # Valid analyst TC kept.
    assert 'Verify suspend rejected for Suspended line' in summaries
    # Unsupported concurrency TC dropped (no concurrency evidence).
    assert 'Verify concurrent suspend requests handled' not in summaries


def test_flagged_analyst_tc_marked_needs_review():
    # State-transition TC whose status token is only in its own title, not the evidence.
    flagged = _analyst_tc('TC03', 'Verify op for Hotlined line',
                          'State-Transition-Matrix-%s' % FID)
    grounded = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                            '%s_scenario_1' % FID)
    jira = _Jira()
    jira.summary = 'Generic op'
    jira.description = 'no status words here'
    jira.acceptance_criteria = ['do the op']
    suite = _suite([grounded, flagged])
    apply_analyst_validity_gate(suite, jira, chalk=None, log=None)

    kept = {tc.summary: tc for tc in suite.test_cases}
    assert 'Verify op for Hotlined line' in kept
    assert getattr(kept['Verify op for Hotlined line'], 'needs_review', False) is True
    assert getattr(kept['Verify op for Hotlined line'], 'review_reason', '')


def test_duplicate_grounded_tcs_collapsed():
    # Two near-identical grounded TCs (same category, same title, same steps) collapse.
    shared_steps = _steps('Set up subscriber line', 'Trigger suspend', 'Verify suspended')
    a = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                     '%s_scenario_1' % FID, steps=[TestStep(step_num=s.step_num,
                     summary=s.summary, expected=s.expected) for s in shared_steps])
    b = _grounded_tc('TC02', 'Verify suspend succeeds for Active line',
                     '%s_scenario_2' % FID, steps=[TestStep(step_num=s.step_num,
                     summary=s.summary, expected=s.expected) for s in shared_steps])
    suite = _suite([a, b])
    before = len(suite.test_cases)
    apply_analyst_validity_gate(suite, _Jira(), chalk=None, log=None)
    assert len(suite.test_cases) < before
    assert 'Verify suspend succeeds for Active line' in _summaries(suite)


def _derived_tc(sno, title, source_id, extracted_text, source_type='Jira AC',
                category='Edge Case'):
    """A FEAT-001 evidence-derived analyst TC carrying a traceability pointer."""
    tc = TestCase(sno=sno, summary=title, description=title, category=category,
                  steps=_distinct_steps(title))
    tc.traceability = TraceabilityRecord(source_type=source_type, source_id=source_id,
                                         extracted_text=extracted_text, confidence=1.0)
    return tc


def test_derived_boundary_with_pointer_in_evidence_is_kept():
    # extracted_text ("suspend") traces into the feature evidence (summary/description),
    # so the derived boundary TC is KEPT.
    boundary = _derived_tc('TC03', 'Verify value at the suspend boundary is accepted',
                           'AC-Boundary-%s' % FID,
                           extracted_text='suspend an Active subscriber line')
    grounded = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                            '%s_scenario_1' % FID)
    suite = _suite([grounded, boundary])
    apply_analyst_validity_gate(suite, _Jira(), chalk=None, log=None)
    assert 'Verify value at the suspend boundary is accepted' in _summaries(suite)


def test_derived_negative_with_pointer_absent_is_dropped_or_flagged():
    # extracted_text names a concept that is NOT in the feature evidence, and the subject
    # itself shares no meaningful token with it either, so the derived TC is DROPPED.
    neg = _derived_tc('TC03', 'Verify quantum teleport rejected for malformed payload',
                      'AC-Negative-%s' % FID,
                      extracted_text='quantum teleport handshake failed',
                      category='Negative')
    grounded = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                            '%s_scenario_1' % FID)
    jira = _Jira()
    jira.summary = 'Suspend a line'
    jira.description = 'suspend the active line'
    jira.acceptance_criteria = ['suspend an active line']
    suite = _suite([grounded, neg])
    apply_analyst_validity_gate(suite, jira, chalk=None, log=None)
    summaries = _summaries(suite)
    # Either dropped (removed) or flagged (kept with needs_review) — never silently kept.
    if 'Verify quantum teleport rejected for malformed payload' in summaries:
        kept = {tc.summary: tc for tc in suite.test_cases}
        assert getattr(kept['Verify quantum teleport rejected for malformed payload'],
                       'needs_review', False) is True


def test_derived_concurrency_without_evidence_still_dropped():
    # A2/A3 keep their old behavior: a Concurrency TC with no concurrency evidence drops.
    conc = _analyst_tc('TC03', 'Verify concurrent suspend requests handled',
                       'Concurrency-%s' % FID)
    grounded = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                            '%s_scenario_1' % FID)
    suite = _suite([grounded, conc])
    apply_analyst_validity_gate(suite, _Jira(), chalk=None, log=None)
    assert 'Verify concurrent suspend requests handled' not in _summaries(suite)


def test_chalk_tcs_retained_after_risk_ordering():
    chalk1 = _grounded_tc('TC01', 'Verify suspend succeeds for Active line',
                          '%s_scenario_1' % FID)
    chalk2 = _grounded_tc('TC02', 'Verify restore succeeds for Suspended line',
                          '%s_scenario_2' % FID)
    analyst = _analyst_tc('TC03', 'Verify suspend rejected for Suspended line',
                          'State-Transition-Matrix-%s' % FID, category='Negative')
    suite = _suite([chalk1, chalk2, analyst])
    apply_analyst_validity_gate(suite, _Jira(), chalk=None, log=None)

    summaries = _summaries(suite)
    # Both Chalk-grounded TCs are retained after dedup + risk ordering (the real
    # CHALK-GUARD invariant is coverage/retention based, not positional).
    assert 'Verify suspend succeeds for Active line' in summaries
    assert 'Verify restore succeeds for Suspended line' in summaries
    # Grounded TCs are never dropped by the gate.
    grounded_kept = [tc for tc in suite.test_cases if not is_analyst(tc)]
    assert len(grounded_kept) == 2
