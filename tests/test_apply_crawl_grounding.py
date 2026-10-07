"""Example tests for apply_crawl_grounding (design §7, "Placement"; task 8.2).

Covers:
  * a Non-UI suite is left byte-identical, with no _grounding_result/_crawl_grounding;
  * a UI suite with closers + a filler step + a typed subscriber-search step +
    an unmatched step produces the expected counts and issues;
  * a missing/errored crawl map marks every UI step crawl_unavailable while
    closers are still removed;
  * an exception raised while grounding one TC leaves that TC unchanged and
    records a grounding_error issue (other TCs still ground);
  * the TC count and the ordered tc.summary titles never change.

Validates: Requirements 1.1, 1.2, 1.4, 3.1, 6.2, 6.3, 7.1, 7.3, 8.1, 8.2
"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import crawl_grounding as cg  # noqa: E402
from modules import qmetry_pattern_library as qpl  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402
from modules.data_models_v8 import (  # noqa: E402
    RoutingAudit, TestCase, TestStep, TestSuite,
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

CLOSER = qpl.century_trace_step()[0]


def _crawl_info():
    """A small in-memory crawl map (CrawlMapInfo) with a Line Summary page."""
    data = {
        'pages': {
            'Line Summary': {
                'url': 'https://nbop.example.com/line?ticket=ST-1',
                'buttons': [{'text': 'Change Rate Plan'}],
                'tabs': [{'text': 'Usage Details'}],
                'inputs': [{'label': 'Rate Plan Code'}],
            },
        },
    }
    return CrawlMapInfo(data, '/tmp/nbop_ui_map.json', '', None)


def _step(summary, expected='', num=1):
    return TestStep(step_num=num, summary=summary, expected=expected)


def _suite(classification, test_cases, audit=True):
    return TestSuite(
        feature_id='MWTGPROV-90001',
        test_cases=test_cases,
        routing_audit=RoutingAudit(classification=classification) if audit else None,
    )


def _logs():
    out = []
    return out, out.append


# --------------------------------------------------------------------------
# 1. Non-UI suite is untouched
# --------------------------------------------------------------------------

def test_non_ui_suite_untouched():
    tc = TestCase(sno='TC1', summary='API flow',
                  steps=[_step('Trigger API POST /lines'), _step(CLOSER, num=2)])
    suite = _suite('api', [tc])
    before = copy.deepcopy(suite)
    _, log = _logs()

    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())

    assert not hasattr(suite, '_grounding_result')
    # steps byte-identical (closer not removed, summaries unchanged)
    assert [s.summary for s in suite.test_cases[0].steps] == \
           [s.summary for s in before.test_cases[0].steps]
    assert len(suite.test_cases[0].steps) == 2
    for s in suite.test_cases[0].steps:
        assert not hasattr(s, '_crawl_grounding')


# --------------------------------------------------------------------------
# 2. UI suite with closers + filler + typed + unmatched steps
# --------------------------------------------------------------------------

def test_ui_suite_counts_and_issues():
    tc = TestCase(sno='TC1', summary='Change rate plan on Line Summary', steps=[
        _step('Launch NBOP and search subscriber by MDN', num=1),            # typed
        _step('Verify requirement: click the Change Rate Plan button on Line Summary', num=2),  # filler grounded
        _step('Wait for the nightly batch job to complete', num=3),          # unmatched
        _step(CLOSER, num=4),                                                # closer -> removed
    ])
    suite = _suite('ui', [tc])
    logs, log = _logs()

    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())

    res = suite._grounding_result
    assert res.feature_id == 'MWTGPROV-90001'
    assert res.closers_removed == 1
    # closer gone, 3 steps remain, renumbered 1..3
    steps = suite.test_cases[0].steps
    assert len(steps) == 3
    assert [s.step_num for s in steps] == [1, 2, 3]
    assert res.ui_steps == 3
    # typed + grounded filler = 2 grounded, 1 unmatched
    assert res.grounded_steps == 2
    assert res.unmatched_steps == 1
    # Filler_Steps is a POST-PASS residual (design §10): the step 2 filler
    # ("Verify requirement: ...") was rewritten to grounded wording
    # ("On NBOP 'Line Summary', click 'Change Rate Plan'"), so it is no longer
    # filler and must NOT be counted. The pass grounds the only filler step
    # here, leaving zero residual generic-fallback text.
    assert res.filler_steps == 0
    # the one unmatched step is reported
    reasons = sorted(i.reason for i in res.issues)
    assert reasons == ['no_match']
    issue = res.issues[0]
    assert issue.tc_number == 'TC1'
    assert issue.step_number == 3
    assert 'TC1' in issue.step_uid and issue.step_uid
    # the typed step keeps its text; the filler step is rendered
    assert steps[0].summary == 'Launch NBOP and search subscriber by MDN'
    assert steps[1].summary == "On NBOP 'Line Summary', click 'Change Rate Plan'"
    assert steps[2].summary.startswith('[UNMATCHED-CRAWL] ')
    assert '[CRAWL-GROUND] MWTGPROV-90001:' in '\n'.join(logs)


def test_enter_text_reported_action_unsupported():
    tc = TestCase(sno='TC1', summary='Enter rate plan', steps=[
        _step('Navigate to the Line Summary page', num=1),
        # enter_text grounds as ui_action but is reported action_unsupported
        _step("On Line Summary, enter 'Gold' into the Rate Plan Code field", num=2),
    ])
    suite = _suite('ui', [tc])
    _, log = _logs()
    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())
    reasons = [i.reason for i in suite._grounding_result.issues]
    assert 'action_unsupported' in reasons


# --------------------------------------------------------------------------
# 3. Emptied TC gets the Req 1.4 replacement step
# --------------------------------------------------------------------------

def test_all_closer_tc_gets_replacement_step():
    tc = TestCase(sno='TC1', summary='Validate downstream traces', steps=[
        _step(CLOSER, num=1),
        _step(qpl.genesis_verify_step()[0], num=2),
    ])
    suite = _suite('ui', [tc])
    _, log = _logs()
    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())

    steps = suite.test_cases[0].steps
    assert len(steps) == 1  # exactly one replacement step (Req 1.4)
    res = suite._grounding_result
    assert res.closers_removed == 2
    assert any(i.reason == 'empty_after_closer' for i in res.issues)
    # TC title unchanged
    assert suite.test_cases[0].summary == 'Validate downstream traces'


# --------------------------------------------------------------------------
# 4. Missing / errored crawl map -> all UI steps crawl_unavailable
# --------------------------------------------------------------------------

def test_missing_crawl_marks_all_unavailable_and_removes_closers():
    tc = TestCase(sno='TC1', summary='Change rate plan', steps=[
        _step('Click the Change Rate Plan button on Line Summary', num=1),
        _step('Open the Usage Details tab', num=2),
        _step(CLOSER, num=3),
    ])
    suite = _suite('ui', [tc])
    logs, log = _logs()
    missing = CrawlMapInfo({}, '', 'missing', None)

    cg.apply_crawl_grounding(suite, log=log, crawl_info=missing)

    res = suite._grounding_result
    assert res.crawl_error == 'missing'
    assert res.closers_removed == 1          # closer still removed
    steps = suite.test_cases[0].steps
    assert len(steps) == 2
    assert res.ui_steps == 2
    assert res.unmatched_steps == 2
    assert res.grounded_steps == 0
    assert all(i.reason == 'crawl_unavailable' for i in res.issues)
    assert all(s.summary.startswith('[UNMATCHED-CRAWL] ') for s in steps)
    assert any('crawl map unavailable' in m for m in logs)


# --------------------------------------------------------------------------
# 5. Exception while grounding one TC leaves it unchanged
# --------------------------------------------------------------------------

def test_exception_in_one_tc_leaves_it_unchanged(monkeypatch):
    good = TestCase(sno='TC1', summary='Good', steps=[
        _step('Click the Change Rate Plan button on Line Summary', num=1),
    ])
    bad_steps = [_step('Click the Change Rate Plan button on Line Summary', num=1)]
    bad = TestCase(sno='TC2', summary='Bad', steps=bad_steps)
    suite = _suite('ui', [good, bad])
    bad_before = copy.deepcopy(bad)

    real_ground = cg.ground_step

    def flaky(tc, step, matcher, ctx):
        if getattr(tc, 'sno', '') == 'TC2':
            raise RuntimeError('boom in TC2')
        return real_ground(tc, step, matcher, ctx)

    monkeypatch.setattr(cg, 'ground_step', flaky)

    _, log = _logs()
    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())

    # TC2 is untouched (original steps, no _crawl_grounding attached)
    assert [s.summary for s in suite.test_cases[1].steps] == \
           [s.summary for s in bad_before.steps]
    for s in suite.test_cases[1].steps:
        assert not hasattr(s, '_crawl_grounding')
    # TC1 still grounded fine
    assert hasattr(suite.test_cases[0].steps[0], '_crawl_grounding')
    # grounding_error issue recorded for TC2
    errs = [i for i in suite._grounding_result.issues if i.reason == 'grounding_error']
    assert len(errs) == 1
    assert errs[0].tc_number == 'TC2'


# --------------------------------------------------------------------------
# 6. TC count and titles are invariant (Req 8.1)
# --------------------------------------------------------------------------

def test_tc_count_and_titles_unchanged():
    tcs = [
        TestCase(sno='TC1', summary='First TC', steps=[
            _step('Click the Change Rate Plan button on Line Summary'), _step(CLOSER, num=2)]),
        TestCase(sno='TC2', summary='Second TC', steps=[_step(CLOSER, num=1)]),
        TestCase(sno='TC3', summary='Third TC', steps=[
            _step('Wait for the nightly batch job to complete')]),
    ]
    suite = _suite('ui', tcs)
    titles_before = [tc.summary for tc in suite.test_cases]
    _, log = _logs()

    cg.apply_crawl_grounding(suite, log=log, crawl_info=_crawl_info())

    assert len(suite.test_cases) == 3
    assert [tc.summary for tc in suite.test_cases] == titles_before
