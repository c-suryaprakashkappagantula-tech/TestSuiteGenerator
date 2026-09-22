# -*- coding: utf-8 -*-
"""TSG adapter for the shared execution contract.

TSG is frequently launched with TestSuiteGenerator as cwd, so the parent repository is
not guaranteed to be on sys.path. Centralise path setup here instead of duplicating it.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.execution_contract import (  # noqa: E402,F401
    CONTRACT_HEADERS,
    CONTRACT_SHEET,
    CONTRACT_VERSION,
    PROVENANCE_HEADERS,
    PROVENANCE_SHEET,
    StepContract,
    contract_enabled,
    infer_step_contract,
    relationship_for_source,
    stable_step_uid,
    stable_tc_uid,
)
