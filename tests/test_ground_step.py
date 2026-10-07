"""Example tests for ground_step (design §7 steps 1-5; task 8.1).

Covers: existing typed id kept as legacy_typed, filler grounded with a
template, non-filler verbatim spelling substitution and the ' [NBOP: ...]'
suffix path, idempotent [UNMATCHED-CRAWL] prefix across two calls,
crawl-unavailable, enter_text -> kind ui_action, and page_context
propagation across steps in a TC.
"""
import pytest

from modules import crawl_grounding as cg
from modules.data_models_v8 import TestCase as _TestCase, TestStep as _TestStep


def _ctrl(control_type, label, attrs=()):
    return cg.CrawlControl(control_type, label, cg.normalize(label), attrs)


@pytest.fixture
def index():
    return cg.CrawlIndex((
        cg.CrawlPage('Line Summary', 'Line Summary', 'page',
                     'https://nbop.example.com/line', (
                         _ctrl('button', 'Change Rate Plan'),
                         _ctrl('tab', 'Usage Details'),
                     )),
        cg.CrawlPage('Account Search', 'Account Search', 'page',
                     'https://nbop.example.com/search', (
                         _ctrl('input', 'Search Text'),
                     )),
        cg.CrawlPage('Context: Transaction History', 'Transaction History',
                     'context_menu', '', (
                         _ctrl('column', 'Transaction Type'),
                     )),
    ))


@pytest.fixture
def matcher(index):
    return cg.CrawlMatcher(index)


def _tc(summary='Verify the line summary flow'):
    return _TestCase(sno='TC1', summary=summary)


def _step(summary, expected='', num=1):
    return _TestStep(step_num=num, summary=summary, expected=expected)


# --- Step 1: existing typed ids ------------------------------------------

def test_subscriber_search_kept_as_legacy_typed(matcher):
    tc = _tc()
    step = _step('Launch NBOP and search subscriber by MDN')
    ctx = cg.GroundCtx()
    g = cg.ground_step(tc, step, matcher, ctx)
    assert g.kind == 'legacy_typed'
    assert g.action_id == 'nbop.subscriber.search'
    assert g.page == 'Subscriber Profile'
    # text unchanged
    assert step.summary == 'Launch NBOP and search subscriber by MDN'
    assert step._crawl_grounding is g
    # page_context set to the fixed page
    assert ctx.page_context == 'Subscriber Profile'
    assert g.grounding_percent == 0  # not a UI_ACTION_ID


def test_transaction_history_typed_page(matcher):
    tc = _tc()
    step = _step('Navigate to Transaction History')
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'legacy_typed'
    assert g.action_id == 'nbop.transaction_history.open'
    assert g.page == 'Context: Transaction History'
    assert g.page_kind == 'context_menu'


# --- Step 3: filler grounded with a template -----------------------------

def test_filler_grounded_with_template(matcher):
    tc = _tc()
    step = _step('Verify requirement: click the Change Rate Plan button on Line Summary')
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'ui_action'
    assert g.was_filler is True
    assert g.action_id == cg.A_CLICK_BUTTON
    assert g.page == 'Line Summary'
    assert g.control_label == 'Change Rate Plan'
    # rendered via the per-family template
    assert step.summary == "On NBOP 'Line Summary', click 'Change Rate Plan'"
    # expected captured from the source requirement (step had none)
    assert step.expected == 'click the Change Rate Plan button on Line Summary'
    assert g.grounding_percent == 100
    assert g.source_locator == (
        "TMO DashBoard/nbop_discovery/nbop_ui_map.json"
        "#pages['Line Summary'].buttons['Change Rate Plan']")


def test_filler_tab_template(matcher):
    tc = _tc()
    step = _step('Perform the scenario-specific NBOP action: open the Usage Details tab on Line Summary')
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'ui_action'
    assert g.action_id == cg.A_OPEN_TAB
    assert step.summary == "On NBOP 'Line Summary', click the 'Usage Details' tab"


# --- Step 3: non-filler verbatim substitution and suffix -----------------

def test_non_filler_verbatim_substitution(matcher):
    tc = _tc()
    # lowercase spelling in the step gets corrected to the crawl spelling
    step = _step('Click the change rate plan button on line summary')
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'ui_action'
    assert g.was_filler is False
    assert 'Change Rate Plan' in step.summary
    assert 'Line Summary' in step.summary
    # original wording kept (still starts with 'Click the')
    assert step.summary.startswith('Click the Change Rate Plan button on Line Summary')


def test_non_filler_suffix_when_page_absent(matcher):
    tc = _tc()
    # control label present (grounds via the control) but the page display
    # name 'Line Summary' is absent from the text -> ' [NBOP: ...]' suffix
    step = _step('Click the Change Rate Plan button')
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'ui_action'
    assert g.page == 'Line Summary' and g.control_label == 'Change Rate Plan'
    assert step.summary == (
        'Click the Change Rate Plan button [NBOP: Line Summary \u203a Change Rate Plan]')


# --- Step 4: unmatched prefix is idempotent ------------------------------

def test_unmatched_prefix_applied_once_across_two_calls(matcher):
    tc = _tc()
    step = _step('Wait for the nightly batch job to complete')
    ctx = cg.GroundCtx()
    g1 = cg.ground_step(tc, step, matcher, ctx)
    assert g1.kind == 'unmatched'
    assert g1.reason == 'no_match'
    first = step.summary
    assert first == '[UNMATCHED-CRAWL] Wait for the nightly batch job to complete'
    g2 = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert step.summary == first  # prefix not doubled
    assert g2.kind == 'unmatched' and g2.reason == 'no_match'


def test_unmatched_filler_uses_requirement_text(matcher):
    tc = _tc()
    step = _step('Verify requirement: reconcile the billing ledger offline')
    cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert step.summary == '[UNMATCHED-CRAWL] reconcile the billing ledger offline'
    assert step.expected == 'reconcile the billing ledger offline'


# --- Step 5: crawl unavailable -------------------------------------------

def test_crawl_unavailable_no_matcher():
    tc = _tc()
    step = _step('Click the Change Rate Plan button on Line Summary')
    g = cg.ground_step(tc, step, None, cg.GroundCtx())
    assert g.kind == 'unmatched'
    assert g.reason == 'crawl_unavailable'
    assert step.summary == (
        '[UNMATCHED-CRAWL] Click the Change Rate Plan button on Line Summary')


def test_crawl_unavailable_empty_index():
    tc = _tc()
    step = _step('Click the Change Rate Plan button')
    empty = cg.CrawlMatcher(cg.CrawlIndex(()))
    g = cg.ground_step(tc, step, empty, cg.GroundCtx())
    assert (g.kind, g.reason) == ('unmatched', 'crawl_unavailable')
    assert step.summary.startswith('[UNMATCHED-CRAWL] ')


# --- enter_text stays kind ui_action -------------------------------------

def test_enter_text_is_ui_action(matcher):
    tc = _tc()
    step = _step("On Account Search, enter 'John' into the Search Text field")
    g = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert g.kind == 'ui_action'
    assert g.action_id == cg.A_ENTER_TEXT
    assert g.page == 'Account Search'
    assert g.control_label == 'Search Text'
    assert g.value == 'John'
    # enter_text is a candidate id only -> grounding 0 (contract builder makes it legacy)
    assert g.grounding_percent == 0


# --- page_context propagation across steps -------------------------------

def test_page_context_propagates_across_steps(matcher):
    tc = _tc()
    ctx = cg.GroundCtx()
    assert ctx.page_context is None
    s1 = _step('Navigate to the Line Summary page', num=1)
    cg.ground_step(tc, s1, matcher, ctx)
    assert ctx.page_context == 'Line Summary'
    # a second step with no page name inherits the context through the matcher
    s2 = _step('Click the Change Rate Plan button', num=2)
    g2 = cg.ground_step(tc, s2, matcher, ctx)
    assert g2.kind == 'ui_action'
    assert g2.page == 'Line Summary'
    assert ctx.page_context == 'Line Summary'


# --- idempotency on a grounded filler step -------------------------------

def test_grounded_filler_idempotent(matcher):
    tc = _tc()
    step = _step('Verify requirement: click the Change Rate Plan button on Line Summary')
    g1 = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    first_summary = step.summary
    g2 = cg.ground_step(tc, step, matcher, cg.GroundCtx())
    assert step.summary == first_summary
    assert g2.kind == 'ui_action'
    assert g2.page == g1.page and g2.control_label == g1.control_label
