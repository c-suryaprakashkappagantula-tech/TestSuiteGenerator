# -*- coding: utf-8 -*-
"""Versioned, additive execution contract shared by TSG and TSE.

The human-readable Test Cases sheet remains unchanged. TSG writes a separate
"Execution Contract" sheet; TSE consumes typed rows deterministically and sends
rows marked ``legacy`` through its existing NLP/regex mapper.

Only high-confidence, system-wide patterns belong here. Unknown prose is never
silently guessed into an executable action.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List

CONTRACT_VERSION = "1.0"
CONTRACT_SHEET = "Execution Contract"
PROVENANCE_SHEET = "Source Provenance"
FEATURE_FLAG = "TSG_CONTRACT_V1"


def contract_enabled(default: bool = True) -> bool:
    """Feature flag for source-governance behavior. Enabled by default after canary
    validation (4192 owns 8 TCs with no cross-feature leak; 4190 unchanged at 9).
    Set ``TSG_CONTRACT_V1=0`` (or false/off/no) to fall back to the legacy path."""
    raw = os.getenv(FEATURE_FLAG)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}

CONTRACT_HEADERS = [
    "Contract Version", "TC UID", "Step UID", "TC Number", "Step Number",
    "Mode", "Action Type", "Action ID", "Assertion Type", "Target System",
    "Parameters JSON", "Requires JSON", "Owner Feature ID", "Owner PI",
    "Source Type", "Source Locator", "Relationship", "Grounding %",
    "Obligation IDs JSON",
]

PROVENANCE_HEADERS = [
    "TC UID", "TC Number", "Owner Feature ID", "Owner PI", "Source Type",
    "Source Locator", "Relationship", "Grounding %", "Summary",
]

TYPED_ASSERTIONS = frozenset({
    "table_row_exists", "pagination_options", "text_present", "field_equals",
    "status_equals", "service_grouping_contains", "response_field_equals",
})

# Crawl-grounded NBOP UI actions executable by TSE (shared Action_Registry).
UI_ACTION_IDS = frozenset({
    "nbop.ui.open_page", "nbop.ui.open_menu", "nbop.ui.open_tab", "nbop.ui.open_popup",
    "nbop.ui.click_button", "nbop.ui.select_dropdown",
    "nbop.ui.field_readonly", "nbop.ui.field_editable",
    "nbop.ui.button_state", "nbop.ui.element_present",
})

# Ids TSG may infer. ``nbop.ui.enter_text`` has no TSE handler yet, so rows that
# infer it are written as legacy (reason ``action_unsupported``).
UI_ACTION_CANDIDATE_IDS = UI_ACTION_IDS | frozenset({"nbop.ui.enter_text"})

# Stable action identities supported by contract-first TSE execution today.
TYPED_ACTION_IDS = frozenset({
    "nbop.subscriber.search",
    "nbop.transaction_history.open",
    "nbop.transaction_history.row_exists",
    "nbop.transaction_history.pagination",
}) | UI_ACTION_IDS

# ui_action Parameters JSON schema (design: "ui_action Parameters JSON schema").
UI_PAGE_KINDS = frozenset({"page", "tile", "context_menu", "edit_menu", "nav_menu"})
UI_CONTROL_TYPES = frozenset({
    "page", "button", "link", "tab", "input", "dropdown", "checkbox",
    "heading", "column", "field",
})
UI_EXPECT_VALUES = frozenset({
    "present", "absent", "enabled", "disabled", "readonly", "editable",
})
_UI_PARAM_KEYS = frozenset({
    "page", "page_url", "page_kind", "control_type", "control_label", "value", "expect",
})
_UI_VALUE_ACTIONS = frozenset({"nbop.ui.select_dropdown", "nbop.ui.enter_text"})
_UI_EXPECT_ACTIONS = frozenset({
    "nbop.ui.field_readonly", "nbop.ui.field_editable",
    "nbop.ui.button_state", "nbop.ui.element_present",
})


def ui_action_params_valid(params: Dict[str, Any], action_id: str = "") -> bool:
    """Check ``params`` against the shared ui_action Parameters JSON schema.

    Rules: only schema keys; every value is a ``str``; ``page``, ``page_kind`` and
    ``control_type`` are required (``page`` non-empty); ``page_kind`` and
    ``control_type`` use the allowed enums; ``control_label`` (non-empty) is present
    iff ``control_type != 'page'``; ``page_url`` has no query string; ``expect`` uses
    the allowed enum. When ``action_id`` is given, ``value`` is present iff the action
    is select_dropdown/enter_text and ``expect`` is present iff the action needs it.
    """
    if not isinstance(params, dict):
        return False
    if any(k not in _UI_PARAM_KEYS for k in params):
        return False
    if any(not isinstance(v, str) for v in params.values()):
        return False
    for key in ("page", "page_kind", "control_type"):
        if key not in params:
            return False
    if not params["page"].strip():
        return False
    if params["page_kind"] not in UI_PAGE_KINDS:
        return False
    control_type = params["control_type"]
    if control_type not in UI_CONTROL_TYPES:
        return False
    has_label = "control_label" in params
    if control_type == "page":
        if has_label:
            return False
    elif not has_label or not params["control_label"].strip():
        return False
    if "page_url" in params and "?" in params["page_url"]:
        return False
    if "expect" in params and params["expect"] not in UI_EXPECT_VALUES:
        return False
    if action_id:
        if ("value" in params) != (action_id in _UI_VALUE_ACTIONS):
            return False
        if ("expect" in params) != (action_id in _UI_EXPECT_ACTIONS):
            return False
    return True


def _g_str(grounding: Any, name: str) -> str:
    value = getattr(grounding, name, "")
    return "" if value is None else str(value).strip()


def _ui_params_from_grounding(grounding: Any, action_id: str) -> Dict[str, Any]:
    """Build ui_action Parameters JSON from a StepGrounding, omitting empty optionals."""
    control_type = _g_str(grounding, "control_type") or "page"
    params: Dict[str, Any] = {
        "page": _g_str(grounding, "page"),
        "page_kind": _g_str(grounding, "page_kind"),
        "control_type": control_type,
    }
    page_url = _g_str(grounding, "page_url").split("?", 1)[0]
    if page_url:
        params["page_url"] = page_url
    label = _g_str(grounding, "control_label")
    if control_type != "page" and label:
        params["control_label"] = label
    value = _g_str(grounding, "value")
    if value and action_id in _UI_VALUE_ACTIONS:
        params["value"] = value
    expect = _g_str(grounding, "expect")
    if expect and action_id in _UI_EXPECT_ACTIONS:
        params["expect"] = expect
    return params


def build_ui_action_contract(grounding: Any) -> Dict[str, Any]:
    """Build a contract row dict from a crawl ``StepGrounding`` (design §9).

    ``grounding`` is duck-typed: attributes ``kind``, ``reason``, ``page``,
    ``page_url``, ``page_kind``, ``control_type``, ``control_label``, ``action_id``,
    ``value``, ``expect``, ``source_locator``. Returns the keys of
    ``infer_step_contract`` plus ``source_type``, ``source_locator`` and
    ``grounding_percent``. Three cases:

    * Grounded, id in ``UI_ACTION_IDS`` and params valid -> ``typed`` /
      ``ui_action``, Source Type ``nbop_crawl``, Grounding 100.
    * Grounded, id not in ``UI_ACTION_IDS`` (e.g. ``nbop.ui.enter_text``,
      reason ``action_unsupported``) -> ``legacy``; the id and params are kept for
      diagnostics only, Source Type ``nbop_crawl``. ``grounding_percent`` is
      ``None``: design §9 says "TC score (unchanged)", so the caller keeps the TC's
      existing grounding score. A registered id whose params fail
      ``ui_action_params_valid`` (e.g. missing ``expect``) is treated the same way,
      so a typed row never carries invalid params.
    * Unmatched (any reason, or no page/id) -> ``legacy``, ``action_id=''``,
      ``parameters={}``, Grounding 0. ``source_type``/``source_locator`` are
      ``None``: design §9 says "existing", so the caller keeps its current values.

    ``target_system`` is always ``NBOP``. Grounded rows get
    ``requires = {"browser": True, "subscriber_profile": page_kind != 'tile' and
    page != 'Landing'}``; unmatched rows get ``{}`` like any legacy row.
    """
    kind = _g_str(grounding, "kind")
    reason = _g_str(grounding, "reason")
    action_id = _g_str(grounding, "action_id")
    page = _g_str(grounding, "page")
    grounded = bool(page and action_id) and (
        kind == "ui_action" or reason == "action_unsupported")

    if not grounded:
        return {
            "mode": "legacy", "action_type": "legacy", "action_id": "",
            "assertion_type": "", "target_system": "NBOP",
            "parameters": {}, "requires": {},
            "source_type": None, "source_locator": None,
            "grounding_percent": 0,
        }

    params = _ui_params_from_grounding(grounding, action_id)
    requires = {
        "browser": True,
        "subscriber_profile": (_g_str(grounding, "page_kind") != "tile"
                               and page != "Landing"),
    }
    base = {
        "assertion_type": "", "target_system": "NBOP",
        "action_id": action_id, "parameters": params, "requires": requires,
        "source_type": "nbop_crawl",
        "source_locator": _g_str(grounding, "source_locator"),
    }
    if (kind == "ui_action" and action_id in UI_ACTION_IDS
            and ui_action_params_valid(params, action_id)):
        base.update(mode="typed", action_type="ui_action", grounding_percent=100)
    else:
        base.update(mode="legacy", action_type="legacy", grounding_percent=None)
    return base


@dataclass
class StepContract:
    contract_version: str = CONTRACT_VERSION
    tc_uid: str = ""
    step_uid: str = ""
    tc_number: str = ""
    step_number: int = 0
    mode: str = "legacy"  # typed | legacy
    action_type: str = "legacy"
    action_id: str = ""
    assertion_type: str = ""
    target_system: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires: Dict[str, Any] = field(default_factory=dict)
    owner_feature_id: str = ""
    owner_pi: str = ""
    source_type: str = ""
    source_locator: str = ""
    relationship: str = "owned"
    grounding_percent: int = 0
    obligation_ids: List[str] = field(default_factory=list)

    def to_excel_row(self) -> List[Any]:
        return [
            self.contract_version, self.tc_uid, self.step_uid, self.tc_number,
            self.step_number, self.mode, self.action_type, self.action_id,
            self.assertion_type, self.target_system,
            json.dumps(self.parameters, sort_keys=True),
            json.dumps(self.requires, sort_keys=True),
            self.owner_feature_id, self.owner_pi, self.source_type,
            self.source_locator, self.relationship, self.grounding_percent,
            json.dumps(self.obligation_ids),
        ]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_GENERIC_ENTRY = re.compile(
    r"\b(entry|record|row|transaction)\b.*\b(exists?|matching|timestamp|type|status)\b",
    re.IGNORECASE,
)
_PAGINATION = re.compile(
    r"\b(pagination|rows?\s+per\s+page|page\s+selector)\b", re.IGNORECASE,
)
_TRANSACTION_HISTORY = re.compile(r"\btransaction\s+history\b", re.IGNORECASE)
_LAUNCH_SEARCH = re.compile(
    r"\b(launch|open|navigate\s+to)\b.*\bnbop\b.*\b(search|subscriber|mdn|profile)\b|"
    r"\bsearch\s+subscriber\s+by\s+mdn\b",
    re.IGNORECASE,
)
_NAV_TRANSACTION_HISTORY = re.compile(
    r"\b(navigate|open|click|go)\b.*\btransaction\s+history\b", re.IGNORECASE,
)


def stable_tc_uid(feature_id: str, tc_number: Any) -> str:
    return "%s:TC%s" % (str(feature_id or "UNKNOWN").strip(), str(tc_number).strip())


def stable_step_uid(tc_uid: str, step_number: Any) -> str:
    return "%s:STEP%s" % (tc_uid, str(step_number).strip())


def infer_step_contract(step_text: str, expected: str = "", tc_summary: str = "") -> Dict[str, Any]:
    """Return typed metadata only for unambiguous global patterns.

    Unknown/ambiguous prose returns mode=legacy. This is deliberate: contract-first
    execution must never be less safe than the existing mapper.
    """
    step = str(step_text or "").strip()
    exp = str(expected or "").strip()
    combined = " ".join(x for x in (step, exp, tc_summary or "") if x)

    if _LAUNCH_SEARCH.search(step):
        return {
            "mode": "typed", "action_type": "ui_action",
            "action_id": "nbop.subscriber.search", "assertion_type": "",
            "target_system": "NBOP", "parameters": {"search_type": "MDN"},
            "requires": {"browser": True, "mdn": True},
        }
    if _NAV_TRANSACTION_HISTORY.search(step):
        return {
            "mode": "typed", "action_type": "ui_action",
            "action_id": "nbop.transaction_history.open", "assertion_type": "",
            "target_system": "NBOP", "parameters": {},
            "requires": {"browser": True, "subscriber_profile": True},
        }
    # Descriptive row assertions are structural, never literal text searches.
    if _GENERIC_ENTRY.search(step) and (
            _TRANSACTION_HISTORY.search(combined)
            or "timestamp" in step.lower()
            or "type" in step.lower() and "status" in step.lower()):
        operation = ''
        operation_patterns = (
            (r'\bremove\s+hotline\b', 'remove_hotline'),
            (r'\bhotline\b', 'hotline'),
            (r'\bchange\s+bcd\b|\bdpfo\b', 'change_bcd'),
            (r'\bdeactivat', 'deactivate'),
            (r'\bsuspend', 'suspend'),
        )
        for pattern, value in operation_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                operation = value
                break
        return {
            "mode": "typed", "action_type": "assertion",
            "action_id": "nbop.transaction_history.row_exists",
            "assertion_type": "table_row_exists", "target_system": "NBOP",
            "parameters": {
                "table": "transaction_history",
                "required_columns": ["timestamp", "type", "status"],
                "match": {"operation": operation} if operation else {},
            },
            "requires": {"browser": True, "transaction_history_page": True},
        }
    if _PAGINATION.search(step):
        values = [int(x) for x in re.findall(r"\b(10|20|30|40|50|100)\b", combined)]
        return {
            "mode": "typed", "action_type": "assertion",
            "action_id": "nbop.transaction_history.pagination",
            "assertion_type": "pagination_options", "target_system": "NBOP",
            "parameters": {"expected_options": values or [10, 20, 30, 40, 50]},
            "requires": {"browser": True, "transaction_history_page": True},
        }
    return {
        "mode": "legacy", "action_type": "legacy", "action_id": "",
        "assertion_type": "", "target_system": "",
        "parameters": {}, "requires": {},
    }


def relationship_for_source(target_feature_id: str, source_feature_id: str,
                            explicitly_linked: Iterable[str] = ()) -> str:
    target = str(target_feature_id or "").upper().strip()
    source = str(source_feature_id or "").upper().strip()
    links = {str(x).upper().strip() for x in (explicitly_linked or ()) if x}
    if not source or source == target:
        return "owned"
    if source in links:
        return "explicit_linked"
    return "supporting_only"


def parse_json_cell(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def contract_row_to_dict(headers: List[str], values: Iterable[Any]) -> Dict[str, Any]:
    raw = dict(zip(headers, list(values)))
    return {
        "contract_version": str(raw.get("Contract Version") or ""),
        "tc_uid": str(raw.get("TC UID") or ""),
        "step_uid": str(raw.get("Step UID") or ""),
        "tc_number": str(raw.get("TC Number") or ""),
        "step_number": int(raw.get("Step Number") or 0),
        "mode": str(raw.get("Mode") or "legacy").lower(),
        "action_type": str(raw.get("Action Type") or "legacy"),
        "action_id": str(raw.get("Action ID") or ""),
        "assertion_type": str(raw.get("Assertion Type") or ""),
        "target_system": str(raw.get("Target System") or ""),
        "parameters": parse_json_cell(raw.get("Parameters JSON"), {}),
        "requires": parse_json_cell(raw.get("Requires JSON"), {}),
        "owner_feature_id": str(raw.get("Owner Feature ID") or ""),
        "owner_pi": str(raw.get("Owner PI") or ""),
        "source_type": str(raw.get("Source Type") or ""),
        "source_locator": str(raw.get("Source Locator") or ""),
        "relationship": str(raw.get("Relationship") or "owned"),
        "grounding_percent": int(str(raw.get("Grounding %") or "0").replace("%", "") or 0),
        "obligation_ids": parse_json_cell(raw.get("Obligation IDs JSON"), []),
    }


def typed_contract_supported(row: Dict[str, Any]) -> bool:
    if row.get("mode") != "typed":
        return False
    if row.get("action_id") not in TYPED_ACTION_IDS:
        return False
    assertion = row.get("assertion_type") or ""
    return not assertion or assertion in TYPED_ASSERTIONS
