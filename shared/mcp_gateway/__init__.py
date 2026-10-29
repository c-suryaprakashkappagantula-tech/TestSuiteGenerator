# -*- coding: utf-8 -*-
"""
shared.mcp_gateway -- approved-MCP access to Jira & Chalk, plus the legacy ON/OFF toggle.

    from shared.mcp_gateway import legacy_enabled, require_legacy, jira, chalk
    if not legacy_enabled():
        issues = jira(log=print).search_all('project = WPATEST AND labels = X', fields='key,summary')

Legacy (old direct REST / browser) is OFF by default. See toggle.py.
"""
from .toggle import (  # noqa: F401
    LegacyAccessDisabled, legacy_enabled, mode, require_legacy, set_legacy, status,
    load_config, CONFIG_PATH,
    qmetry_enabled, require_qmetry_legacy, set_qmetry,
)
from .alerts import McpUnavailable, mcp_unavailable_alert  # noqa: F401


def jira(log=None):
    from .ops import jira as _j
    return _j(log)


def chalk(log=None):
    from .ops import chalk as _c
    return _c(log)
