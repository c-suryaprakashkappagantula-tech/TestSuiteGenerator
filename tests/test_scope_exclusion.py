"""Focused unit tests for scope exclusion (FEAT-002 extractor + FEAT-003 drop pass).

Pure / cache-free: every subject, jira and chalk stand-in is constructed in memory
(direct-construction style of tests/test_analyst_validity.py). Covers:
  * a 'scope excluded for cross-environment fan-out' comment derives an excluded set
    that drops a multi-environment fan-out TC but KEEPS an in-scope file-pickup TC;
  * a feature with no exclusion language derives an empty set and drops nothing
    (so default/grounded output stays byte-identical).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.data_models_v8 import TestCase, TestStep, TestSuite  # noqa: E402
from modules.traceability import TraceabilityRecord  # noqa: E402
from modules import scope_exclusion  # noqa: E402
from modules.data_first_engine import _apply_scope_exclusion  # noqa: E402

FID = 'MWTGPROV-9074'


# ── Minimal in-memory jira stand-in ────────────────────────────────────────────
class _Jira:
    def __init__(self, summary='', description='', acceptance_criteria='', comments=None):
        self.key = FID
        self.summary = summary
        self.description = description
        self.acceptance_criteria = acceptance_criteria
        self.comments = comments or []
        self.priority = 'High'


def _tr(source_id='chalk_1', source_type='Chalk Scenario'):
    return TraceabilityRecord(source_type=source_type, source_id=source_id,
                              extracted_text='x', confidence=0.9)


def _tc(summary, steps=None, source_type='Chalk Scenario', category='Happy Path'):
    tc = TestCase(sno='1', summary=summary, description=summary, category=category,
                  traceability=_tr(source_type=source_type))
    tc.steps = [TestStep(step_num=i + 1, summary=s, expected='ok')
                for i, s in enumerate(steps or [])]
    return tc


# The exclusion comment verbatim from the live 4074 ticket.
_EXCL_COMMENT = ('9/10/2026 Update (per Arif & Venkat): Audit-file intake is production-only. '
                 "Lower environments will not be able to consume TMO's file. A temp folder "
                 'has to be used for manual test considerations. Scope excluded for '
                 'cross-environment fan-out on NSL.')


def _quiet(_message):
    pass


# ── (a) exclusion language present → derives subjects; drops fan-out, keeps file pickup ──
def test_exclusion_comment_derives_subjects():
    jira = _Jira(description=_EXCL_COMMENT)
    excluded = scope_exclusion.extract_excluded_subjects(jira, chalk=None, log=_quiet)
    # Distinctive compounds from the exclusion phrase are captured.
    assert 'cross-environment fan-out' in excluded or 'fan-out' in excluded
    assert any('fan' in s for s in excluded)


def test_drops_fanout_keeps_file_pickup():
    jira = _Jira(description=_EXCL_COMMENT)
    suite = TestSuite(feature_id=FID)
    fanout = _tc('Verify audit-file cross-environment fan-out across lower environments',
                 steps=['Trigger cross-environment fan-out to QA and perf'])
    pickup = _tc('Verify audit-file pickup from the production temp folder',
                 steps=['Place audit file in temp folder', 'Confirm file pickup'])
    suite.test_cases = [fanout, pickup]

    excluded, dropped = _apply_scope_exclusion(suite, jira, chalk=None, log=_quiet)

    assert excluded                                   # non-empty excluded set
    kept_summaries = [tc.summary for tc in suite.test_cases]
    assert pickup.summary in kept_summaries           # in-scope TC kept
    assert fanout.summary not in kept_summaries       # out-of-scope TC dropped
    assert fanout.summary in dropped
    # One summary warning appended for the drop.
    assert any('[SCOPE]' in w for w in suite.warnings)


def test_scope_exclusion_wins_over_user_requested_flag():
    """Regression for tsg-scope-drop-fix: an EXPLICIT scope exclusion must drop a
    matching TC even when it carries ``user_requested=True``.

    QMetry-auto-added scenarios (modules/qmetry_steps_source) set ``user_requested=True``
    only to protect them from near-dup collapse. The scope pass used to read that flag as
    "user asked for this, never drop", so the two live 4074 fan-out TCs survived despite a
    distinctive exclusion match. The pass must now test such TCs and drop a distinctive
    match. This asserts the FIXED behaviour; it fails against the pre-fix early-continue.
    """
    jira = _Jira(description=_EXCL_COMMENT)
    suite = TestSuite(feature_id=FID)
    # Mirrors the two live 4074 QMetry-only fan-out TCs (summary carries the subject,
    # title/validation are None, user_requested set by the QMetry near-dup protection).
    fanout = _tc('MWTGPROV-4074 Verify Multi-environment fan-out locates the MDN',
                 steps=['Trigger multi-environment fan-out'])
    fanout.user_requested = True
    cross = _tc('MWTGPROV-4074 Verify Duplicate migrated pair across environments resolves',
                steps=['Resolve duplicate pair across environments'])
    cross.user_requested = True
    pickup = _tc('Verify audit-file pickup from the production temp folder',
                 steps=['Place audit file in temp folder', 'Confirm file pickup'])
    suite.test_cases = [fanout, cross, pickup]

    excluded, dropped = _apply_scope_exclusion(suite, jira, chalk=None, log=_quiet)

    kept_summaries = [tc.summary for tc in suite.test_cases]
    assert fanout.summary not in kept_summaries       # dropped despite user_requested
    assert cross.summary not in kept_summaries        # dropped despite user_requested
    assert pickup.summary in kept_summaries           # in-scope TC still kept
    assert fanout.summary in dropped
    assert cross.summary in dropped
    assert any('[SCOPE]' in w for w in suite.warnings)


def test_degenerate_hyphen_subject_never_drops():
    """HARD GATE guard (tsg-scope-drop-fix / MWTGPROV-4628): a captured subject that is
    pure punctuation (a bare '-' from a markdown bullet marker) must NOT drop any TC.

    A bare '-' is 'distinctive' under the hyphen rule and substring-matches the
    'MWTGPROV-NNNN-' id present in every TC title, so without the drop-site substance
    screen it would nuke the whole suite. Here the exclusion comment uses a bullet list
    ('- Verizon ... out of scope'), which yields a '-' subject; no TC may be dropped by
    it. (Mirrors the live 4628 over-drop this fix prevents.)
    """
    bullet_comment = ('Scope Constraints:\n'
                      '- Verizon (VZW) and CBRS billing record processing are out of scope '
                      'and must be unaffected.')
    jira = _Jira(description=bullet_comment)
    suite = TestSuite(feature_id='MWTGPROV-4628')
    # Titles carry the feature-id hyphen prefix, exactly like the live suite.
    a = _tc('MWTGPROV-4628 Verify Both MT and MO records convert correctly',
            steps=['Convert MT and MO records'])
    b = _tc('MWTGPROV-4628 Verify end-to-end TMO PRR processing distribution',
            steps=['Process PRR end-to-end'])
    suite.test_cases = [a, b]

    excluded, dropped = _apply_scope_exclusion(suite, jira, chalk=None, log=_quiet)

    assert dropped == set()                            # bare '-' never justifies a drop
    assert [tc.summary for tc in suite.test_cases] == [a.summary, b.summary]
    assert not any('[SCOPE]' in w for w in (suite.warnings or []))


# ── (b) no exclusion language → empty set, nothing dropped (byte-identical path) ──
def test_no_exclusion_language_drops_nothing():
    jira = _Jira(description='Audit file is consumed by NSL and reconciled against pairing.')
    suite = TestSuite(feature_id=FID)
    a = _tc('Verify audit-file pickup from temp folder', steps=['Place file', 'Confirm pickup'])
    b = _tc('Verify pairing reconcile auto-correct', steps=['Reconcile', 'Auto-correct'])
    suite.test_cases = [a, b]

    excluded, dropped = _apply_scope_exclusion(suite, jira, chalk=None, log=_quiet)

    assert excluded == set()                          # no exclusion markers → empty
    assert dropped == set()
    assert [tc.summary for tc in suite.test_cases] == [a.summary, b.summary]
    assert not any('[SCOPE]' in w for w in (suite.warnings or []))
