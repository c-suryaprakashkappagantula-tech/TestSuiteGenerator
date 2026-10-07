# -*- coding: utf-8 -*-
"""Property 9: typed iff grounded with a registered id (task 2.3).

Validates: Requirements 3.4, 4.1, 4.3, 4.4, 5.6
"""
import os
import sys
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared import execution_contract as ec  # noqa: E402

_VALUE_ACTIONS = {"nbop.ui.select_dropdown", "nbop.ui.enter_text"}
_EXPECT_ACTIONS = {
    "nbop.ui.field_readonly", "nbop.ui.field_editable",
    "nbop.ui.button_state", "nbop.ui.element_present",
}
_KINDS = ["ui_action", "legacy_typed", "unmatched"]
_REASONS = ["", "no_match", "ambiguous", "action_unsupported", "no_crawl_map"]

_word = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                min_size=1, max_size=10)
_name = st.lists(_word, min_size=1, max_size=3).map(" ".join)
_action_ids = st.one_of(
    st.sampled_from(sorted(ec.UI_ACTION_CANDIDATE_IDS)),
    st.sampled_from(sorted(ec.TYPED_ACTION_IDS - ec.UI_ACTION_IDS)),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz._", min_size=1, max_size=20),
)


@st.composite
def groundings(draw):
    """StepGrounding-like objects whose params are schema-consistent for their id."""
    kind = draw(st.sampled_from(_KINDS))
    reason = draw(st.sampled_from(_REASONS))
    action_id = draw(_action_ids)
    page = draw(_name)
    page_kind = draw(st.sampled_from(sorted(ec.UI_PAGE_KINDS)))
    control_type = draw(st.sampled_from(sorted(ec.UI_CONTROL_TYPES)))
    control_label = "" if control_type == "page" else draw(_name)
    page_url = draw(st.one_of(
        st.just(""),
        _word.map(lambda w: "https://nbop.example/" + w),
        _word.map(lambda w: "https://nbop.example/" + w + "?msisdn=123"),
    ))
    value = draw(_name) if action_id in _VALUE_ACTIONS else ""
    expect = (draw(st.sampled_from(sorted(ec.UI_EXPECT_VALUES)))
              if action_id in _EXPECT_ACTIONS else "")
    return SimpleNamespace(
        kind=kind, reason=reason, action_id=action_id, page=page,
        page_url=page_url, page_kind=page_kind, control_type=control_type,
        control_label=control_label, value=value, expect=expect,
        source_locator="nbop_crawl:%s#%s" % (page, control_label or page),
    )


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 9: Typed iff grounded with a registered id
@settings(max_examples=200, deadline=None)
@given(g=groundings())
def test_typed_iff_grounded_with_registered_id(g):
    row = ec.build_ui_action_contract(g)
    action_id = g.action_id.strip()
    grounded = bool(g.page.strip() and action_id) and (
        g.kind == "ui_action" or g.reason == "action_unsupported")
    expect_typed = g.kind == "ui_action" and action_id in ec.UI_ACTION_IDS

    assert (row["mode"] == "typed") == expect_typed
    assert row["target_system"] == "NBOP"

    if expect_typed:
        assert row["action_type"] == "ui_action"
        assert row["action_id"] == action_id
        assert row["source_type"] == "nbop_crawl"
        assert g.page.strip() in row["source_locator"]
        assert row["grounding_percent"] == 100
        assert ec.ui_action_params_valid(row["parameters"], action_id)
        assert row["parameters"]["page"] == g.page.strip()
    elif grounded:
        # Grounded but unregistered id: legacy, page/control params kept.
        assert row["mode"] == "legacy"
        assert row["action_type"] == "legacy"
        assert row["action_id"] == action_id
        assert row["source_type"] == "nbop_crawl"
        assert row["grounding_percent"] is None
        assert row["parameters"]["page"] == g.page.strip()
        assert row["parameters"]["page_kind"] == g.page_kind
        assert row["parameters"]["control_type"] == g.control_type
        if g.control_type != "page":
            assert row["parameters"]["control_label"] == g.control_label
    else:
        assert row["mode"] == "legacy"
        assert row["action_type"] == "legacy"
        assert row["action_id"] == ""
        assert row["parameters"] == {}
        assert row["grounding_percent"] == 0
