# -*- coding: utf-8 -*-
"""Property test: ui_action Parameters JSON schema and round-trip (task 2.5).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 10: Parameters JSON schema and round-trip
**Validates: Requirements 4.2, 4.7**
"""
import json
import os
import sys
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared import execution_contract as ec  # noqa: E402

# Control types consistent with each registered UI action id.
_CONTROL_TYPES_FOR = {
    "nbop.ui.open_page": ["page"],
    "nbop.ui.open_menu": ["link", "button"],
    "nbop.ui.open_tab": ["tab"],
    "nbop.ui.open_popup": ["button", "link"],
    "nbop.ui.click_button": ["button"],
    "nbop.ui.select_dropdown": ["dropdown"],
    "nbop.ui.field_readonly": ["input", "field", "dropdown", "checkbox"],
    "nbop.ui.field_editable": ["input", "field", "dropdown", "checkbox"],
    "nbop.ui.button_state": ["button"],
    "nbop.ui.element_present": sorted(ec.UI_CONTROL_TYPES - {"page"}),
}
_EXPECT_FOR = {
    "nbop.ui.field_readonly": ["readonly"],
    "nbop.ui.field_editable": ["editable"],
    "nbop.ui.button_state": ["enabled", "disabled"],
    "nbop.ui.element_present": ["present", "absent"],
}
assert set(_CONTROL_TYPES_FOR) == set(ec.UI_ACTION_IDS)

# Arbitrary unicode, pre-stripped and non-empty (build_ui_action_contract strips).
_text = st.text(min_size=1, max_size=40).map(str.strip).filter(bool)
_url_base = st.text(
    alphabet=st.characters(blacklist_characters="?"), max_size=40).map(str.strip)


@st.composite
def grounded(draw):
    action_id = draw(st.sampled_from(sorted(ec.UI_ACTION_IDS)))
    control_type = draw(st.sampled_from(_CONTROL_TYPES_FOR[action_id]))
    base = draw(_url_base)
    query = draw(st.none() | st.text(max_size=30))
    page_url = base if query is None else base + "?" + query
    g = SimpleNamespace(
        kind="ui_action", reason="", action_id=action_id,
        page=draw(_text), page_url=page_url,
        page_kind=draw(st.sampled_from(sorted(ec.UI_PAGE_KINDS))),
        control_type=control_type,
        control_label=draw(_text) if control_type != "page" else "",
        value=draw(_text) if action_id == "nbop.ui.select_dropdown" else "",
        expect=draw(st.sampled_from(_EXPECT_FOR[action_id])) if action_id in _EXPECT_FOR else "",
        source_locator="nbop_ui_map.json#" + draw(_text),
    )
    return g, base


@settings(max_examples=200, deadline=None)
@given(grounded())
def test_params_schema_and_roundtrip(case):
    g, url_base = case
    contract = ec.build_ui_action_contract(g)
    assert contract["mode"] == "typed"
    assert contract["action_id"] == g.action_id
    params = contract["parameters"]

    # Schema
    assert ec.ui_action_params_valid(params, g.action_id)
    assert params["page"] == g.page
    assert params["page_kind"] == g.page_kind
    assert params["control_type"] == g.control_type
    if url_base:
        assert params["page_url"] == url_base  # query string stripped
    else:
        assert "page_url" not in params
    if g.control_type != "page":
        assert params["control_label"] == g.control_label
    if g.value:
        assert params["value"] == g.value
    if g.expect:
        assert params["expect"] == g.expect

    # JSON round-trip
    assert json.loads(json.dumps(params, sort_keys=True)) == params

    # Excel row round-trip
    row = ec.StepContract(
        tc_uid="F-1:TC1", step_uid="F-1:TC1:STEP1", tc_number="1", step_number=1,
        mode=contract["mode"], action_type=contract["action_type"],
        action_id=contract["action_id"], target_system=contract["target_system"],
        parameters=params, requires=contract["requires"],
        source_type=contract["source_type"], source_locator=contract["source_locator"],
        grounding_percent=contract["grounding_percent"],
    ).to_excel_row()
    back = ec.contract_row_to_dict(ec.CONTRACT_HEADERS, row)
    assert back["parameters"] == params
    assert back["action_id"] == g.action_id
    assert back["mode"] == "typed"
