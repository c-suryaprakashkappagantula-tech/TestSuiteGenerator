# -*- coding: utf-8 -*-
"""
alerts.py -- the shared loud-fail helper for the MCP-only Jira/Chalk policy.

When an in-scope Chalk/Jira path finds the approved MCP gateway unavailable, it must
FAIL LOUDLY instead of silently falling back to browser/REST. Every in-scope site calls
``mcp_unavailable_alert(system, log)`` (and/or raises ``McpUnavailable``) rather than
spawning Playwright or hitting the old direct REST path.

This module is kept importable WITHOUT streamlit installed or a Streamlit script-run
context: streamlit is imported lazily and every streamlit touch is wrapped in try/except,
so plain CLI / pipeline callers never crash. It is a SIBLING of toggle.py on purpose --
toggle.py must stay dependency-free (no streamlit/requests/playwright), so the
Streamlit-touching helper lives here.
"""
from __future__ import annotations


class McpUnavailable(RuntimeError):
    """Raised when the approved MCP gateway is unavailable and no fallback is allowed."""


def mcp_unavailable_alert(system, log=print):
    """Emit the canonical MCP-unavailable alert and return the message string.

    Behaviour:
      * builds the exact canonical message for ``system`` (e.g. 'Chalk', 'Jira');
      * ALWAYS logs it via ``log`` (wrapped in try/except so a broken logger can't crash);
      * shows ``st.error(msg)`` ONLY when a Streamlit script-run context is active
        (lazy, guarded import so non-Streamlit callers never crash);
      * returns the message string.
    """
    msg = (
        f'\u274c MCP gateway unavailable \u2014 {system} access is MCP-only and cannot '
        f'fall back to browser/REST.\n'
        f'Check the MCP server (python -m shared.mcp_gateway status) and retry.'
    )

    try:
        log(msg)
    except Exception:
        pass

    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            import streamlit as st
            st.error(msg)
    except Exception:
        pass

    return msg
