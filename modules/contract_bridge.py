# -*- coding: utf-8 -*-
"""TSG adapter for the shared execution contract.

TSG is frequently launched with TestSuiteGenerator as cwd, so the parent repository is
not guaranteed to be on sys.path. Centralise path setup here instead of duplicating it.
"""
import sys
from pathlib import Path


def _find_repo_root():
    """Locate the repository root that holds the ``shared`` package.

    A normal checkout has it at ``parents[2]``; a nested git worktree
    (``TestSuiteGenerator/.worktrees/<branch>/``) pushes it further up. Walk the
    parents and pick the first that contains ``shared``, falling back to the
    historical ``parents[2]`` so a normal checkout resolves identically.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'shared').is_dir():
            return parent
    return here.parents[2]


_REPO_ROOT = _find_repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.execution_contract import (  # noqa: E402,F401
    CONTRACT_HEADERS,
    CONTRACT_SHEET,
    CONTRACT_VERSION,
    PROVENANCE_HEADERS,
    PROVENANCE_SHEET,
    StepContract,
    TYPED_ACTION_IDS,
    UI_ACTION_CANDIDATE_IDS,
    UI_ACTION_IDS,
    build_ui_action_contract,
    contract_enabled,
    infer_step_contract,
    relationship_for_source,
    stable_step_uid,
    stable_tc_uid,
    ui_action_params_valid,
)
