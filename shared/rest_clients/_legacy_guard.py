# -*- coding: utf-8 -*-
"""
_legacy_guard.py -- bridge from the REST clients to the Jira/Chalk ON/OFF toggle.

This package is imported both as `shared.rest_clients` and as a top-level `rest_clients`
(TSG puts shared/ on sys.path), so we make sure the repo root is importable and always
reach the toggle through ONE module identity: `shared.mcp_gateway.toggle`.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from shared.mcp_gateway.toggle import (  # noqa: E402,F401
    LegacyAccessDisabled, legacy_enabled, require_legacy,
)


def legacy_blocked(tag: str, log) -> bool:
    """True (and logs why) when the old direct path must not run."""
    if legacy_enabled():
        return False
    try:
        log(f'[{tag}] Legacy direct access is OFF - request skipped. Jira/Chalk go through the '
            f'approved MCP gateway (python -m shared.mcp_gateway status).')
    except Exception:
        pass
    return True
