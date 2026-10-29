# -*- coding: utf-8 -*-
"""
toggle.py -- the single ON/OFF switch for how Jira & Chalk are reached.

    legacy_direct_access = False  (DEFAULT)  -> every Jira/Chalk call goes through the
                                               approved Spectrum MCP gateway, using the
                                               GLOBAL Kiro config (~/.kiro/settings/mcp.json).
    legacy_direct_access = True              -> old path: cookie-based REST + Playwright
                                               browser fallbacks (storage_state.json).

Precedence (highest first):
    1. env var TMO_LEGACY_DIRECT_ACCESS = 1/true/on  or  0/false/off
    2. shared/mcp_gateway/data_source_config.json  -> "legacy_direct_access"
    3. built-in default: False (legacy OFF)

Flip it from anywhere:
    python -m shared.mcp_gateway legacy off      # MCP (default)
    python -m shared.mcp_gateway legacy on       # old direct path
    python -m shared.mcp_gateway status
or the toggle in The Hub sidebar.

This module is dependency-free on purpose: dashboards, the Hub and the REST clients all
import it, so it must never pull in requests/playwright/streamlit.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).resolve().with_name('data_source_config.json')
ENV_LEGACY = 'TMO_LEGACY_DIRECT_ACCESS'
ENV_QMETRY = 'TMO_QMETRY_DIRECT_ACCESS'

# Guardrail numbers mirror the ATLSUP page "AI Connections to Jira via the MCP Server":
#   1s between reads, 2s between writes, 20 events/minute, 1,440 events/day.
# daily_budget stays under 1,440 so a runaway loop stops before the gateway does.
DEFAULTS: Dict[str, Any] = {
    'legacy_direct_access': False,
    # QMetry has NO MCP equivalent yet, so its direct REST path is the one
    # sanctioned carve-out. This is SEPARATE from legacy_direct_access, which
    # governs generic Jira/Chalk and is permanently hard-blocked (MCP-only).
    'qmetry_direct_access': True,
    'mcp_config_path': '~/.kiro/settings/mcp.json',
    'jira_server': 'jira',          # key in mcpServers (use "jira_uat" to test on UAT)
    'chalk_server': 'confluence',   # Chalk = Confluence server key (confluence_uat for UAT)
    'daily_budget': 1200,
    'per_minute': 20,
    'min_read_gap_s': 1.0,
    'min_write_gap_s': 2.0,
    'cache_ttl_s': {'jira': 900, 'chalk': 86400, 'tools_list': 86400},
    'verify_ssl': True,
    'request_timeout_s': 120,
    # Optional explicit tool names, e.g. {"jira.get_issue": "get_issue"}.
    # Leave empty to auto-resolve from the server's tools/list.
    'tool_map': {},
}

_TRUE = {'1', 'true', 'yes', 'on'}
_FALSE = {'0', 'false', 'no', 'off'}


class LegacyAccessDisabled(RuntimeError):
    """Raised when code tries the old direct Jira/Chalk path while the toggle is OFF."""


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    """Defaults merged with data_source_config.json (a broken file falls back to defaults)."""
    data: Dict[str, Any] = {}
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8') or '{}')
    except Exception:
        data = {}
    return _deep_merge(DEFAULTS, data)


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')


def _env_override():
    raw = os.environ.get(ENV_LEGACY, '').strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def legacy_enabled() -> bool:
    """Generic Jira/Chalk direct access is PERMANENTLY DISABLED (MCP-only, by policy).

    Jira & Chalk must ALWAYS go through the approved Spectrum MCP gateway. There is no
    config or environment override that can re-enable the old direct REST / browser path
    for generic Jira/Chalk; this function always returns False. (QMetry is the single
    sanctioned carve-out and has its OWN gate -- see ``qmetry_enabled`` /
    ``require_qmetry_legacy`` -- because no MCP equivalent for QMetry exists yet.)
    """
    return False


def _legacy_config_flag() -> bool:
    """The raw persisted legacy flag (for status/reporting only -- NOT an access decision)."""
    env = _env_override()
    if env is not None:
        return env
    return bool(load_config().get('legacy_direct_access', False))


def _qmetry_env_override():
    raw = os.environ.get(ENV_QMETRY, '').strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def qmetry_enabled() -> bool:
    """True when the sanctioned QMetry direct-REST carve-out is allowed (default True).

    QMetry is intentionally decoupled from ``legacy_enabled`` so that locking down generic
    Jira/Chalk to MCP-only never breaks QMetry (which has no MCP tool yet). Precedence:
    env ``TMO_QMETRY_DIRECT_ACCESS`` -> config ``qmetry_direct_access`` -> default True.
    """
    env = _qmetry_env_override()
    if env is not None:
        return env
    return bool(load_config().get('qmetry_direct_access', True))


def mode() -> str:
    return 'legacy' if legacy_enabled() else 'mcp'


def set_legacy(enabled: bool) -> Dict[str, Any]:
    """Generic Jira/Chalk legacy access is permanently disabled and CANNOT be enabled.

    Kept for backward compatibility with the Hub toggle / CLI, but enabling is a no-op:
    the flag is always forced False and a warning is emitted. Use ``set_qmetry(...)`` to
    control the sanctioned QMetry carve-out instead.
    """
    cfg = load_config()
    cfg['legacy_direct_access'] = False
    save_config(cfg)
    if enabled:
        try:
            print('[mcp_gateway] REFUSED: generic Jira/Chalk legacy access is MCP-only by '
                  'policy and cannot be enabled. (QMetry has its own switch: set_qmetry.)')
        except Exception:
            pass
    return cfg


def set_qmetry(enabled: bool) -> Dict[str, Any]:
    """Persist the sanctioned QMetry direct-REST carve-out flag."""
    cfg = load_config()
    cfg['qmetry_direct_access'] = bool(enabled)
    save_config(cfg)
    return cfg


def require_legacy(what: str) -> None:
    """Tripwire for any generic Jira/Chalk direct path. ALWAYS raises (MCP-only policy)."""
    if not legacy_enabled():
        raise LegacyAccessDisabled(
            f'{what} is a generic Jira/Chalk direct path and is PERMANENTLY DISABLED. '
            f'Jira & Chalk must always come through the approved MCP gateway (global Kiro '
            f'MCP config). This cannot be re-enabled. (QMetry is the only sanctioned direct '
            f'carve-out and uses require_qmetry_legacy, not this gate.)')


def require_qmetry_legacy(what: str) -> None:
    """Tripwire for the sanctioned QMetry direct-REST carve-out.

    Raises only if the QMetry carve-out has been explicitly turned OFF (env/config); by
    default QMetry direct access is allowed because no MCP equivalent exists yet.
    """
    if not qmetry_enabled():
        raise LegacyAccessDisabled(
            f'{what} is the QMetry direct-REST carve-out and is switched OFF. '
            f'Re-enable with: python -m shared.mcp_gateway qmetry on')


def status() -> Dict[str, Any]:
    cfg = load_config()
    env = _env_override()
    mcp_path = Path(os.path.expanduser(cfg['mcp_config_path']))
    return {
        'mode': mode(),
        'legacy_direct_access': legacy_enabled(),
        'legacy_jira_chalk_policy': 'MCP-ONLY (permanently disabled)',
        'legacy_config_flag': _legacy_config_flag(),
        'qmetry_direct_access': qmetry_enabled(),
        'source': 'env' if env is not None else ('config' if CONFIG_PATH.exists() else 'default'),
        'config_file': str(CONFIG_PATH),
        'mcp_config_path': str(mcp_path),
        'mcp_config_found': mcp_path.exists(),
        'jira_server': cfg['jira_server'],
        'chalk_server': cfg['chalk_server'],
        'daily_budget': cfg['daily_budget'],
        'per_minute': cfg['per_minute'],
    }
