# -*- coding: utf-8 -*-
"""contract_bridge re-exports the UI action contract symbols from shared.execution_contract.

**Validates: Requirements 5.6**
"""
import importlib
import sys
from pathlib import Path

import pytest

_TSG_ROOT = Path(__file__).resolve().parents[1]
if str(_TSG_ROOT) not in sys.path:
    sys.path.insert(0, str(_TSG_ROOT))

UI_EXPORTS = [
    "UI_ACTION_IDS",
    "UI_ACTION_CANDIDATE_IDS",
    "TYPED_ACTION_IDS",
    "build_ui_action_contract",
    "ui_action_params_valid",
]


@pytest.mark.parametrize("name", UI_EXPORTS)
def test_name_reexported_as_same_object(name):
    bridge = importlib.import_module("modules.contract_bridge")
    shared = importlib.import_module("shared.execution_contract")
    assert hasattr(bridge, name), f"modules.contract_bridge missing {name}"
    assert getattr(bridge, name) is getattr(shared, name)


def test_from_import_works():
    from modules.contract_bridge import (  # noqa: F401
        TYPED_ACTION_IDS,
        UI_ACTION_CANDIDATE_IDS,
        UI_ACTION_IDS,
        build_ui_action_contract,
        ui_action_params_valid,
    )

    assert callable(build_ui_action_contract)
    assert callable(ui_action_params_valid)
