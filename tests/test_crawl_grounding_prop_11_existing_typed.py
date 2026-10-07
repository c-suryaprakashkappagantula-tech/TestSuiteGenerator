# -*- coding: utf-8 -*-
"""Property-based test for the crawl-grounding pass.

# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 11: Existing typed ids are preserved

For any step text that ``modules.contract_bridge.infer_step_contract`` maps to one
of the four existing typed ids (``nbop.subscriber.search``,
``nbop.transaction_history.open`` / ``.row_exists`` / ``.pagination``), running
``ground_step`` on that step yields a ``StepGrounding`` with
``kind == 'legacy_typed'`` carrying that same ``action_id``, and the step summary
is left UNCHANGED (design §7 step 1, Req 4.5).

The @given strategies build step texts that trigger each of the four typed ids via
``infer_step_contract``'s regexes (see ``shared/execution_contract.py``), optionally
decorated with surrounding filler words that do not break the match. Each generated
text is first asserted (``assume``) to still infer the expected typed id, so the
property is only exercised on genuinely-typed steps.

**Validates: Requirements 4.5**
"""
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from modules import crawl_grounding as cg
from modules.contract_bridge import infer_step_contract
from modules.data_models_v8 import TestCase as _TestCase, TestStep as _TestStep


# --- A small fixed crawl map / matcher -----------------------------------
# A real matcher is supplied so ground_step takes its normal path; the typed
# branch (step 1) fires before the matcher is ever consulted, but we still pass
# a well-formed matcher to exercise the function exactly as production does.
def _ctrl(control_type, label, attrs=()):
    return cg.CrawlControl(control_type, label, cg.normalize(label), attrs)


_INDEX = cg.CrawlIndex.from_map({
    "pages": {
        "Subscriber Profile": {
            "url": "https://nbop.example.com/subscriber",
            "inputs": ["MDN"],
            "buttons": ["Search"],
        },
        "Line Summary": {
            "url": "https://nbop.example.com/line",
            "buttons": ["Change Rate Plan"],
            "tabs": ["Usage Details"],
        },
    },
    "context_menu": [{"text": "Transaction History"}],
    "discovered_at": "2026-01-01T00:00:00",
})
_MATCHER = cg.CrawlMatcher(_INDEX)


def _tc(summary="Verify the NBOP subscriber flow"):
    return _TestCase(sno="TC1", summary=summary)


def _step(summary, expected="", num=1):
    return _TestStep(step_num=num, summary=summary, expected=expected)


# --- Text generators per typed id ----------------------------------------
# Harmless surrounding words that never introduce another typed-id trigger
# (no 'navigate/open/click/go', no 'transaction history', no 'pagination',
# no entry+status pattern, no 'launch/search/subscriber/mdn/profile').
_NOISE = st.sampled_from([
    "", "please", "then", "as a tester", "carefully", "in step two",
    "for the active line", "and confirm the UI renders", "quickly",
])


def _wrap(core, prefix, suffix):
    parts = [p for p in (prefix, core, suffix) if p]
    return " ".join(parts).strip()


# nbop.subscriber.search  (_LAUNCH_SEARCH)
_SEARCH_CORES = st.sampled_from([
    "Launch NBOP and search subscriber by MDN",
    "Open NBOP and search for the subscriber profile",
    "Navigate to NBOP and look up the subscriber by MDN",
    "search subscriber by MDN",
])


@settings(max_examples=150, deadline=None)
@given(core=_SEARCH_CORES, prefix=_NOISE, suffix=_NOISE)
def test_subscriber_search_preserved(core, prefix, suffix):
    _assert_typed_preserved(_wrap(core, prefix, suffix), "nbop.subscriber.search")


# nbop.transaction_history.open  (_NAV_TRANSACTION_HISTORY)
_OPEN_CORES = st.sampled_from([
    "Navigate to Transaction History",
    "Open the Transaction History page",
    "Click on Transaction History",
    "Go to the Transaction History view",
])


@settings(max_examples=150, deadline=None)
@given(core=_OPEN_CORES, prefix=_NOISE, suffix=_NOISE)
def test_transaction_history_open_preserved(core, prefix, suffix):
    _assert_typed_preserved(_wrap(core, prefix, suffix),
                            "nbop.transaction_history.open")


# nbop.transaction_history.row_exists  (_GENERIC_ENTRY + timestamp / type&status)
# Must avoid the nav verbs + "transaction history" combo (that routes to .open)
# and avoid _LAUNCH_SEARCH triggers.
_ROW_CORES = st.sampled_from([
    "Verify an entry exists with a timestamp",
    "Confirm the record exists with a matching timestamp",
    "Validate the row matching the operation has a type and status",
    "A transaction entry exists with the correct timestamp",
])


@settings(max_examples=150, deadline=None)
@given(core=_ROW_CORES, prefix=_NOISE, suffix=_NOISE)
def test_transaction_history_row_exists_preserved(core, prefix, suffix):
    _assert_typed_preserved(_wrap(core, prefix, suffix),
                            "nbop.transaction_history.row_exists")


# nbop.transaction_history.pagination  (_PAGINATION)
# Avoid the entry+status pattern so it does not route to row_exists first.
_PAGINATION_CORES = st.sampled_from([
    "Verify the pagination options",
    "Check the rows per page selector",
    "Validate the page selector shows 10 20 30 40 50",
    "Confirm pagination is available",
])


@settings(max_examples=150, deadline=None)
@given(core=_PAGINATION_CORES, prefix=_NOISE, suffix=_NOISE)
def test_transaction_history_pagination_preserved(core, prefix, suffix):
    _assert_typed_preserved(_wrap(core, prefix, suffix),
                            "nbop.transaction_history.pagination")


# --- Shared invariant assertion ------------------------------------------
def _assert_typed_preserved(text, expected_id):
    tc = _tc()
    # Precondition: infer_step_contract (via contract_bridge) must still map this
    # text to the expected typed id. If a noise combination perturbs the regexes,
    # skip that example rather than exercise a non-typed step.
    inferred = infer_step_contract(text, "", tc.summary)
    assume(inferred.get("mode") == "typed")
    assume(inferred.get("action_id") == expected_id)

    original = text
    step = _step(text)
    ctx = cg.GroundCtx()
    g = cg.ground_step(tc, step, _MATCHER, ctx)

    # Property 11 invariants (narrowed to the StepGrounding per the task):
    assert g.kind == "legacy_typed", (expected_id, g.kind)
    assert g.action_id == expected_id, (expected_id, g.action_id)
    # The step summary is UNCHANGED (Req 4.5).
    assert step.summary == original, (original, step.summary)
    # The attached grounding is the returned object.
    assert step._crawl_grounding is g
