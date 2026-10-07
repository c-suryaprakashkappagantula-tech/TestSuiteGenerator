# -*- coding: utf-8 -*-
"""UI action-id registry and ui_action Parameters JSON schema (task 2.1).

Validates: Requirements 4.2, 4.6, 5.1, 5.6
"""
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared import execution_contract as ec  # noqa: E402

EXPECTED_UI_IDS = {
    "nbop.ui.open_page", "nbop.ui.open_menu", "nbop.ui.open_tab", "nbop.ui.open_popup",
    "nbop.ui.click_button", "nbop.ui.select_dropdown",
    "nbop.ui.field_readonly", "nbop.ui.field_editable",
    "nbop.ui.button_state", "nbop.ui.element_present",
}
ORIGINAL_TYPED_IDS = {
    "nbop.subscriber.search",
    "nbop.transaction_history.open",
    "nbop.transaction_history.row_exists",
    "nbop.transaction_history.pagination",
}
# Snapshot of the pre-change contract shape; must not drift (Req 4.6).
HEADERS_SNAPSHOT = [
    "Contract Version", "TC UID", "Step UID", "TC Number", "Step Number",
    "Mode", "Action Type", "Action ID", "Assertion Type", "Target System",
    "Parameters JSON", "Requires JSON", "Owner Feature ID", "Owner PI",
    "Source Type", "Source Locator", "Relationship", "Grounding %",
    "Obligation IDs JSON",
]
VERSION_SNAPSHOT = "1.0"


def test_ui_action_ids_exactly_ten():
    assert isinstance(ec.UI_ACTION_IDS, frozenset)
    assert set(ec.UI_ACTION_IDS) == EXPECTED_UI_IDS
    assert len(ec.UI_ACTION_IDS) == 10


def test_candidate_ids_add_enter_text_only():
    assert ec.UI_ACTION_CANDIDATE_IDS == ec.UI_ACTION_IDS | {"nbop.ui.enter_text"}
    assert "nbop.ui.enter_text" not in ec.UI_ACTION_IDS
    assert "nbop.ui.enter_text" not in ec.TYPED_ACTION_IDS


def test_typed_ids_union():
    assert isinstance(ec.TYPED_ACTION_IDS, frozenset)
    assert ec.UI_ACTION_IDS <= ec.TYPED_ACTION_IDS
    assert ORIGINAL_TYPED_IDS <= ec.TYPED_ACTION_IDS
    assert set(ec.TYPED_ACTION_IDS) == ORIGINAL_TYPED_IDS | EXPECTED_UI_IDS


def test_contract_headers_and_version_unchanged():
    assert ec.CONTRACT_HEADERS == HEADERS_SNAPSHOT
    assert ec.CONTRACT_VERSION == VERSION_SNAPSHOT


def test_typed_contract_supported_accepts_ui_ids():
    row = {"mode": "typed", "action_id": "nbop.ui.open_tab", "assertion_type": ""}
    assert ec.typed_contract_supported(row)
    row["action_id"] = "nbop.ui.enter_text"
    assert not ec.typed_contract_supported(row)


VALID_TAB = {
    "page": "Subscriber Profile",
    "page_url": "https://nbop.example.com/subscriber/profile",
    "page_kind": "page",
    "control_type": "tab",
    "control_label": "Usage Details",
}


@pytest.mark.parametrize("params,action_id", [
    (VALID_TAB, ""),
    (VALID_TAB, "nbop.ui.open_tab"),
    ({"page": "Context: Transaction History", "page_kind": "context_menu",
      "control_type": "page"}, "nbop.ui.open_menu"),
    ({"page": "Subscriber Profile", "page_kind": "page", "control_type": "dropdown",
      "control_label": "Rows per page", "value": "50"}, "nbop.ui.select_dropdown"),
    ({"page": "Subscriber Profile", "page_kind": "page", "control_type": "field",
      "control_label": "MSISDN", "expect": "readonly"}, "nbop.ui.field_readonly"),
])
def test_valid_params(params, action_id):
    assert ec.ui_action_params_valid(dict(params), action_id)


def _with(**changes):
    p = dict(VALID_TAB)
    for k, v in changes.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    return p


@pytest.mark.parametrize("params,action_id", [
    (_with(page=None), ""),                                   # missing page
    (_with(page_kind=None), ""),                              # missing page_kind
    (_with(control_type=None), ""),                           # missing control_type
    (_with(page=""), ""),                                     # empty page
    (_with(value=50), ""),                                    # non-str value
    (_with(control_label=None), ""),                          # label required for tab
    ({"page": "Home", "page_kind": "page", "control_type": "page",
      "control_label": "Home"}, ""),                          # label with control_type=page
    (_with(page_url="https://nbop.example.com/p?ticket=ST-1"), ""),  # query string
    (_with(page_kind="screen"), ""),                          # bad page_kind enum
    (_with(control_type="slider"), ""),                       # bad control_type enum
    (_with(expect="visible"), ""),                            # bad expect enum
    (_with(extra="x"), ""),                                   # unknown key
    (VALID_TAB, "nbop.ui.select_dropdown"),                   # value missing for select
    (_with(value="x"), "nbop.ui.open_tab"),                   # value on non-value action
    (VALID_TAB, "nbop.ui.element_present"),                   # expect missing
])
def test_invalid_params(params, action_id):
    assert not ec.ui_action_params_valid(params, action_id)


def test_non_dict_rejected():
    assert not ec.ui_action_params_valid(None)
    assert not ec.ui_action_params_valid([("page", "x")])


# --- build_ui_action_contract (task 2.2) -------------------------------------
# Validates: Requirements 3.4, 4.1, 4.2, 4.3, 4.4
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_INFER_KEYS = set(ec.infer_step_contract("").keys())
_LOCATOR = ("TMO DashBoard/nbop_discovery/nbop_ui_map.json"
            "#pages['Subscriber Profile'].tabs['Usage Details']")


def _grounding(**over):
    base = dict(kind="ui_action", reason="", page="Subscriber Profile",
                page_url="https://nbop.example.com/subscriber/profile",
                page_kind="page", control_type="tab", control_label="Usage Details",
                action_id="nbop.ui.open_tab", value="", expect="",
                source_locator=_LOCATOR)
    base.update(over)
    return SimpleNamespace(**base)


def _assert_keys(row):
    assert set(row) == _INFER_KEYS | {"source_type", "source_locator", "grounding_percent"}


def test_build_typed_row_grounded_registered():
    row = ec.build_ui_action_contract(_grounding())
    _assert_keys(row)
    assert row["mode"] == "typed" and row["action_type"] == "ui_action"
    assert row["action_id"] == "nbop.ui.open_tab"
    assert row["target_system"] == "NBOP"
    assert row["source_type"] == "nbop_crawl" and row["source_locator"] == _LOCATOR
    assert row["grounding_percent"] == 100
    assert row["requires"] == {"browser": True, "subscriber_profile": True}
    assert row["parameters"] == {
        "page": "Subscriber Profile",
        "page_url": "https://nbop.example.com/subscriber/profile",
        "page_kind": "page", "control_type": "tab", "control_label": "Usage Details",
    }  # empty value/expect omitted
    assert ec.ui_action_params_valid(row["parameters"], row["action_id"])
    assert json.loads(json.dumps(row["parameters"], sort_keys=True)) == row["parameters"]
    assert ec.typed_contract_supported(row)


def test_build_typed_tile_page_no_subscriber_profile():
    row = ec.build_ui_action_contract(_grounding(
        page="Device Lookup", page_url="", page_kind="tile", control_type="page",
        control_label="", action_id="nbop.ui.open_page"))
    assert row["mode"] == "typed"
    assert row["parameters"] == {"page": "Device Lookup", "page_kind": "tile",
                                 "control_type": "page"}
    assert row["requires"] == {"browser": True, "subscriber_profile": False}
    assert ec.ui_action_params_valid(row["parameters"], row["action_id"])


def test_build_legacy_row_grounded_unregistered():
    row = ec.build_ui_action_contract(_grounding(
        kind="unmatched", reason="action_unsupported", control_type="input",
        control_label="MSISDN", action_id="nbop.ui.enter_text", value="5551234567"))
    _assert_keys(row)
    assert row["mode"] == "legacy" and row["action_type"] == "legacy"
    assert row["action_id"] == "nbop.ui.enter_text"      # informational
    assert row["parameters"]["value"] == "5551234567"   # params kept
    assert row["source_type"] == "nbop_crawl" and row["source_locator"] == _LOCATOR
    assert row["grounding_percent"] is None              # keep TC score
    assert not ec.typed_contract_supported(row)


def test_build_legacy_row_unmatched():
    row = ec.build_ui_action_contract(_grounding(
        kind="unmatched", reason="no_match", page="", page_url="", page_kind="",
        control_type="", control_label="", action_id="", source_locator=""))
    _assert_keys(row)
    assert row["mode"] == "legacy" and row["action_type"] == "legacy"
    assert row["action_id"] == "" and row["parameters"] == {}
    assert row["grounding_percent"] == 0
    assert row["source_type"] is None and row["source_locator"] is None  # keep existing
    assert not ec.typed_contract_supported(row)
