"""Universal banned-phrase backstop — CR / manual-link authored-shaped leak (fix/banned-backstop).

The narrow-CR / manual-link path returns from ``_build_cr_suite_v8`` BEFORE the only
banned-step strip (``StepAssembler.reconcile``) ever runs, and the leaking step is default
``origin='grounded'`` template content from the V7 CR builder (``test_engine.py`` TC4
"Regression: Normal Deactivation flow unaffected by CR fix"), which no closer gate inspects.

The fix runs the EXISTING grounded-gated strip as a universal final backstop via the
module-level ``strip_ungrounded_banned_steps`` (called both inside ``reconcile`` and at the
shared export chokepoint ``generate_excel``). These tests pin the backstop directly against
the real MWTGPROV-4533 leak shape:

  (a) an ungrounded CR feature gets its Century/Service-Grouping step stripped while its
      grounded sibling steps (5 of 6) survive — the whole TC is NOT dropped;
  (b) the surviving blob carries NONE of the zero-tolerance banned tokens;
  (c) a feature genuinely about Century Report / Service Grouping KEEPS the step;
  (d) an authored (QMetry ground-truth) banned step is NEVER stripped.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.data_models_v8 import TestCase, TestStep, TestSuite  # noqa: E402
from modules.step_assembler import strip_ungrounded_banned_steps  # noqa: E402

# Zero-tolerance banned token list (full set from the task).
_BANNED_TOKENS = (
    'Service Grouping', 'SERVICE_GROUPING', 'Century Report', 'NSL processes request',
    'Trigger API', 'TRANSACTION_HISTORY', 'LINE_HISTORY', 'NE Portal', 'NBOP_MIG',
)


def _blob(suite):
    out = []
    for tc in suite.test_cases:
        for s in tc.steps:
            out.append('%s %s' % (s.summary or '', s.expected or ''))
    return '\n'.join(out)


def _assert_no_banned(suite):
    blob = _blob(suite).lower()
    for token in _BANNED_TOKENS:
        assert token.lower() not in blob, 'banned token leaked: %r' % token


def _cr_regression_tc():
    """The V7 CR-template Regression TC: 5 Genesis/NBOP-portal steps (no banned token)
    plus the Century/Service-Grouping step 6 — ALL default origin='grounded', authored=False,
    mirroring test_engine.py's TC4 construction (no origin=/authored= kwargs)."""
    steps = [
        TestStep(step_num=1,
                 summary='Trigger a normal Deactivation for the test subscriber line.',
                 expected='Deactivation request is accepted and submitted to provisioning.'),
        TestStep(step_num=2,
                 summary='Verify the order reflects the Deactivation request.',
                 expected='The order shows the Deactivation in a submitted state.'),
        TestStep(step_num=3,
                 summary='Verify the line state transitions as expected for the Deactivation.',
                 expected='Line state moves to the deactivated state.'),
        TestStep(step_num=4,
                 summary='Confirm no guaranteed-delivery retry is scheduled for this flow.',
                 expected='No guaranteed-delivery retry entry is created.'),
        TestStep(step_num=5,
                 summary='Verify the subscriber record reflects the completed Deactivation.',
                 expected='Subscriber record shows the line as deactivated.'),
        TestStep(step_num=6,
                 summary='Open Century Report (Service Grouping) - Verify transaction name shows '
                         'normal Deactivation — no guaranteed delivery retry entry present',
                 expected='Century Report confirms normal deactivation path used — '
                          'no GD retry transaction found'),
    ]
    return TestCase(sno='TC05', summary='Regression: Normal Deactivation flow unaffected by CR fix',
                    steps=steps)


def _cr_suite():
    """MWTGPROV-4533 narrow CR — evidence names NONE of the banned concepts."""
    return TestSuite(
        feature_id='MWTGPROV-4533',
        feature_title='CR - Sync Device/SIM - Fix E3812',
        feature_desc='Fix the E3812 error on the Sync Device/SIM change request.',
        test_cases=[_cr_regression_tc()],
        acceptance_criteria=['Given the E3812 condition, the Sync Device/SIM CR completes '
                             'without error and the normal Deactivation flow is unaffected.'],
        scope='Fix E3812 on the Sync Device/SIM change request.',
    )


def test_cr_authored_shaped_century_step_stripped_siblings_kept():
    # The exact leak: a Century/Service-Grouping step inside a grounded-origin CR regression
    # TC on a feature NOT grounded in that concept. Backstop strips THAT step only.
    suite = _cr_suite()
    tc = suite.test_cases[0]
    assert len(tc.steps) == 6
    removed = strip_ungrounded_banned_steps(suite, log=None)
    assert removed == 1, 'exactly the one Century/Service-Grouping step must be stripped'
    assert len(tc.steps) == 5, 'the other 5 grounded steps must be retained (no TC collapse)'
    _assert_no_banned(suite)
    # Surviving steps are renumbered 1..5 and remain coherent.
    assert [s.step_num for s in tc.steps] == [1, 2, 3, 4, 5]


def test_cr_backstop_idempotent():
    # Running the backstop twice removes nothing the second time (pure/idempotent).
    suite = _cr_suite()
    assert strip_ungrounded_banned_steps(suite, log=None) == 1
    assert strip_ungrounded_banned_steps(suite, log=None) == 0
    assert len(suite.test_cases[0].steps) == 5


def test_grounded_century_feature_keeps_step():
    # A feature genuinely about Century Report / Service Grouping KEEPS the step.
    suite = _cr_suite()
    suite.feature_title = 'Century Report Service Grouping reconciliation'
    suite.feature_desc = 'Validate the Century Report and Service Grouping transaction view.'
    suite.scope = 'Century Report / Service Grouping transaction validation.'
    suite.acceptance_criteria = ['The Century Report Service Grouping shows the transaction.']
    tc = suite.test_cases[0]
    before = len(tc.steps)
    removed = strip_ungrounded_banned_steps(suite, log=None)
    assert removed == 0, 'a genuinely-grounded Century/Service-Grouping feature keeps the step'
    assert len(tc.steps) == before
    assert 'Century Report' in _blob(suite)


def test_authored_banned_step_never_stripped_on_cr():
    # An authored (QMetry ground-truth) banned step is NEVER stripped even on the ungrounded CR.
    suite = _cr_suite()
    tc = suite.test_cases[0]
    tc.steps[5].authored = True
    tc.steps[5].origin = 'authored'
    removed = strip_ungrounded_banned_steps(suite, log=None)
    assert removed == 0, 'authored ground-truth step must be preserved'
    assert len(tc.steps) == 6
    assert 'Century Report' in _blob(suite)
