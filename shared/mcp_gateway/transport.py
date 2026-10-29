# -*- coding: utf-8 -*-
"""
transport.py -- minimal MCP "streamable HTTP" client for the Spectrum AI gateway.

Reads server definitions from the GLOBAL Kiro config (~/.kiro/settings/mcp.json), the same
file Kiro itself uses, so there is exactly one place holding the LiteLLM key and PAT:

    {"mcpServers": {"jira":  {"type": "http", "url": "https://ai-gateway.../mcp/",
                              "headers": {"X-LiteLLM-API-Key": "Bearer ...",
                                          "X-Jira-Token": "...", "X-MCP-Servers": "jira"}}}}

Guardrails enforced client-side (shared by every process via a small SQLite file):
    * min gap between calls (1s reads / 2s writes)
    * sliding 20 calls / minute
    * daily budget (default 1,200 < the gateway's 1,440/day)
    * response cache with TTL, so repeat reads of structural data cost nothing

No MCP SDK dependency -- JSON-RPC 2.0 over HTTP POST with requests, handling both
application/json and text/event-stream responses.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .toggle import load_config

STATE_DB = Path(__file__).resolve().parent.parent / 'state' / 'mcp_gateway.db'
PROTOCOL_VERSION = '2025-06-18'
CLIENT_INFO = {'name': 'tmo-qa-dashboards', 'version': '1.0'}


class McpError(RuntimeError):
    pass


class McpConfigError(McpError):
    pass


class McpAuthError(McpError):
    pass


class McpBudgetExceeded(McpError):
    pass


class McpToolError(McpError):
    pass


# ----------------------------------------------------------------------------------------
# Global MCP config
# ----------------------------------------------------------------------------------------
_ENV_PAT = re.compile(r'\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}')


def _expand(value):
    if isinstance(value, str):
        return _ENV_PAT.sub(lambda m: os.environ.get(m.group(1), ''), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def mcp_config_path(cfg: Optional[Dict[str, Any]] = None) -> Path:
    cfg = cfg or load_config()
    return Path(os.path.expanduser(cfg['mcp_config_path']))


def list_servers(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    path = mcp_config_path(cfg)
    if not path.exists():
        raise McpConfigError(
            f'Global MCP config not found: {path}. Set it up in Kiro by attaching '
            f'README-Kiro-Jira-MCP-Setup.md and choosing GLOBAL scope.')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise McpConfigError(f'Cannot parse {path}: {e}')
    return data.get('mcpServers', {}) or {}


def load_server(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Find a server by key, or by its X-MCP-Servers header value."""
    servers = list_servers(cfg)
    entry = servers.get(name)
    key = name
    if entry is None:
        for k, v in servers.items():
            # HTTP header names are case-insensitive (ATLSUP's Chalk sample uses lowercase).
            hdrs = {str(hk).lower(): hv for hk, hv in (v.get('headers') or {}).items()}
            hdr = str(hdrs.get('x-mcp-servers', ''))
            if name.lower() in [h.strip().lower() for h in hdr.split(',')]:
                entry, key = v, k
                break
    if entry is None:
        raise McpConfigError(
            f'MCP server "{name}" not found in {mcp_config_path(cfg)}. '
            f'Available: {", ".join(servers) or "(none)"}.')
    if entry.get('disabled'):
        raise McpConfigError(f'MCP server "{key}" is disabled in {mcp_config_path(cfg)}.')
    url = entry.get('url')
    if not url:
        raise McpConfigError(f'MCP server "{key}" has no "url" (only HTTP servers are supported).')
    return {
        'key': key,
        'url': _expand(url),
        'headers': _expand(entry.get('headers') or {}),
        # Kiro's "timeout" is in milliseconds.
        'timeout_s': (max(10, int(entry['timeout']) // 1000) if entry.get('timeout') else None),
    }


# ----------------------------------------------------------------------------------------
# Shared state: rate limiter, daily budget, response cache
# ----------------------------------------------------------------------------------------
_DB_LOCK = threading.Lock()


def _db() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(STATE_DB), timeout=30, isolation_level=None)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS calls (ts REAL, server TEXT, method TEXT, tool TEXT)')
    con.execute('CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls(ts)')
    con.execute('CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, ts REAL)')
    return con


def _midnight() -> float:
    now = datetime.now()
    return datetime(now.year, now.month, now.day).timestamp()


def usage_today() -> Dict[str, int]:
    with _DB_LOCK:
        con = _db()
        try:
            rows = con.execute('SELECT server, COUNT(*) FROM calls WHERE ts >= ? GROUP BY server',
                               (_midnight(),)).fetchall()
        finally:
            con.close()
    out = {s: n for s, n in rows}
    out['total'] = sum(out.values())
    return out


def _acquire_slot(server: str, method: str, tool: str, cfg: Dict[str, Any],
                  write: bool, log: Callable[[str], None]) -> None:
    """Block until a call is allowed; record it. Raises McpBudgetExceeded at the daily cap."""
    gap = float(cfg['min_write_gap_s'] if write else cfg['min_read_gap_s'])
    per_min = int(cfg['per_minute'])
    budget = int(cfg['daily_budget'])
    while True:
        with _DB_LOCK:
            con = _db()
            try:
                con.execute('BEGIN IMMEDIATE')
                now = time.time()
                today = con.execute('SELECT COUNT(*) FROM calls WHERE ts >= ?',
                                    (_midnight(),)).fetchone()[0]
                if today >= budget:
                    con.execute('ROLLBACK')
                    raise McpBudgetExceeded(
                        f'Daily MCP budget reached ({today}/{budget}). Gateway allows 1,440/day; '
                        f'>2,500 triggers an ATLSUP investigation. Resume tomorrow or raise '
                        f'"daily_budget" in shared/mcp_gateway/data_source_config.json.')
                last = con.execute('SELECT MAX(ts) FROM calls').fetchone()[0] or 0.0
                window = con.execute('SELECT ts FROM calls WHERE ts >= ? ORDER BY ts',
                                     (now - 60.0,)).fetchall()
                wait = 0.0
                if now - last < gap:
                    wait = gap - (now - last)
                if len(window) >= per_min:
                    wait = max(wait, window[0][0] + 60.0 - now)
                if wait <= 0:
                    con.execute('INSERT INTO calls VALUES (?,?,?,?)', (now, server, method, tool))
                    con.execute('COMMIT')
                    return
                con.execute('ROLLBACK')
            finally:
                con.close()
        if wait > 5:
            log(f'[MCP] rate limit: waiting {wait:.0f}s ({per_min}/min cap)')
        time.sleep(min(wait, 30.0))


def cache_get(key: str, ttl_s: float):
    if ttl_s <= 0:
        return None
    with _DB_LOCK:
        con = _db()
        try:
            row = con.execute('SELECT v, ts FROM cache WHERE k=?', (key,)).fetchone()
        finally:
            con.close()
    if row and time.time() - row[1] <= ttl_s:
        return json.loads(row[0])
    return None


def cache_put(key: str, value: Any) -> None:
    with _DB_LOCK:
        con = _db()
        try:
            con.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                        (key, json.dumps(value, default=str), time.time()))
        finally:
            con.close()


def cache_clear(prefix: str = '') -> int:
    with _DB_LOCK:
        con = _db()
        try:
            cur = con.execute('DELETE FROM cache WHERE k LIKE ?', (prefix + '%',))
            return cur.rowcount
        finally:
            con.close()


# ----------------------------------------------------------------------------------------
# JSON-RPC over streamable HTTP
# ----------------------------------------------------------------------------------------
def _parse_sse(text: str, want_id) -> Optional[Dict[str, Any]]:
    """Return the JSON-RPC message with id == want_id from an SSE body."""
    data_lines: List[str] = []
    found = None
    for line in text.splitlines() + ['']:
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
        elif line.strip() == '' and data_lines:
            try:
                msg = json.loads('\n'.join(data_lines))
            except Exception:
                msg = None
            data_lines = []
            if isinstance(msg, dict) and msg.get('id') == want_id:
                found = msg
    return found


def parse_tool_result(result: Dict[str, Any]) -> Any:
    """tools/call result -> Python object (structuredContent, else JSON-in-text, else text)."""
    sc = result.get('structuredContent')
    if isinstance(sc, (dict, list)) and sc:
        if isinstance(sc, dict) and set(sc) == {'result'}:
            return sc['result']
        return sc
    texts = [c.get('text', '') for c in result.get('content', []) or []
             if isinstance(c, dict) and c.get('type') == 'text']
    joined = '\n'.join(t for t in texts if t)
    if not joined:
        return None
    try:
        return json.loads(joined)
    except Exception:
        pass
    if len(texts) > 1:
        parsed = []
        for t in texts:
            try:
                parsed.append(json.loads(t))
            except Exception:
                parsed = None
                break
        if parsed is not None:
            return parsed
    return {'text': joined}


class McpHttpClient:
    """One MCP server (e.g. "jira" or "chalk") behind the gateway."""

    def __init__(self, server: str, log: Optional[Callable[[str], None]] = None,
                 cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or load_config()
        self.log = log or (lambda m: None)
        self.server_name = server
        self.srv = load_server(server, self.cfg)
        _n = (server + self.srv['key']).lower()
        self.kind = 'chalk' if ('chalk' in _n or 'confluence' in _n) else 'jira'
        self._http = requests.Session()
        self._http.verify = bool(self.cfg.get('verify_ssl', True))
        self._session_id: Optional[str] = None
        self._proto: Optional[str] = None
        self._next_id = 1
        self._tools: Optional[List[Dict[str, Any]]] = None
        self._lock = threading.Lock()

    # -- low level --------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        h = {'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream'}
        h.update({k: str(v) for k, v in self.srv['headers'].items()})
        if self._session_id:
            h['Mcp-Session-Id'] = self._session_id
        if self._proto:
            h['MCP-Protocol-Version'] = self._proto
        return h

    def _post(self, method: str, params: Optional[Dict[str, Any]] = None,
              notify: bool = False, _retry: bool = True) -> Any:
        payload: Dict[str, Any] = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        if not notify:
            payload['id'] = self._next_id
            self._next_id += 1
        timeout = self.srv.get('timeout_s') or int(self.cfg.get('request_timeout_s', 120))
        try:
            resp = self._http.post(self.srv['url'], json=payload, headers=self._headers(),
                                   timeout=timeout)
        except requests.RequestException as e:
            raise McpError(f'[{self.srv["key"]}] gateway unreachable: {e}. Check VPN and that '
                           f'the URL ends in /mcp/.')
        sid = resp.headers.get('Mcp-Session-Id') or resp.headers.get('mcp-session-id')
        if sid:
            self._session_id = sid
        if resp.status_code == 404 and self._session_id and _retry and method != 'initialize':
            # Session expired on the server: re-initialise once and retry.
            self._session_id, self._proto = None, None
            self._initialize()
            return self._post(method, params, notify, _retry=False)
        if resp.status_code in (401, 403):
            raise McpAuthError(f'[{self.srv["key"]}] HTTP {resp.status_code}: PAT expired, wrong '
                               f'environment token, or LiteLLM key invalid. Update the headers '
                               f'in {mcp_config_path(self.cfg)}.')
        if resp.status_code == 406:
            raise McpError(f'[{self.srv["key"]}] HTTP 406 from gateway (known ATLSUP networking '
                           f'issue, see page section 14.3).')
        if resp.status_code == 429:
            raise McpError(f'[{self.srv["key"]}] HTTP 429 rate limited by gateway.')
        if notify:
            return None
        if resp.status_code >= 400:
            raise McpError(f'[{self.srv["key"]}] HTTP {resp.status_code}: {resp.text[:300]}')
        ctype = resp.headers.get('Content-Type', '')
        if 'text/event-stream' in ctype:
            msg = _parse_sse(resp.text, payload['id'])
        else:
            try:
                msg = resp.json()
            except ValueError:
                msg = None
        if not isinstance(msg, dict):
            raise McpError(f'[{self.srv["key"]}] unreadable response to {method}: {resp.text[:300]}')
        if msg.get('error'):
            err = msg['error']
            raise McpError(f'[{self.srv["key"]}] {method} error {err.get("code")}: {err.get("message")}')
        return msg.get('result')

    def _initialize(self) -> None:
        _acquire_slot(self.srv['key'], 'initialize', '', self.cfg, False, self.log)
        res = self._post('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': CLIENT_INFO,
        }) or {}
        self._proto = res.get('protocolVersion') or PROTOCOL_VERSION
        self._post('notifications/initialized', notify=True)

    def _ensure(self) -> None:
        if self._proto is None:
            self._initialize()

    # -- public -----------------------------------------------------------------------
    def list_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            if self._tools is not None and not refresh:
                return self._tools
            ck = f'tools:{self.srv["key"]}:{self.srv["url"]}'
            ttl = float(self.cfg['cache_ttl_s'].get('tools_list', 86400))
            cached = None if refresh else cache_get(ck, ttl)
            if cached is not None:
                self._tools = cached
                return cached
            self._ensure()
            tools: List[Dict[str, Any]] = []
            cursor = None
            while True:
                _acquire_slot(self.srv['key'], 'tools/list', '', self.cfg, False, self.log)
                res = self._post('tools/list', {'cursor': cursor} if cursor else {}) or {}
                tools.extend(res.get('tools', []) or [])
                cursor = res.get('nextCursor')
                if not cursor:
                    break
            cache_put(ck, tools)
            self._tools = tools
            return tools

    def call_tool(self, name: str, arguments: Dict[str, Any], write: bool = False,
                  cache_ttl_s: Optional[float] = None) -> Any:
        ttl = 0.0 if write else float(
            cache_ttl_s if cache_ttl_s is not None
            else self.cfg['cache_ttl_s'].get(self.kind, 0))
        ck = 'call:%s:%s:%s' % (self.srv['key'], name, hashlib.sha1(
            json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest())
        if ttl > 0:
            hit = cache_get(ck, ttl)
            if hit is not None:
                return hit
        attempts = 1 if write else 3
        for attempt in range(1, attempts + 1):
            with self._lock:
                self._ensure()
                _acquire_slot(self.srv['key'], 'tools/call', name, self.cfg, write, self.log)
                res = self._post('tools/call', {'name': name, 'arguments': arguments}) or {}
            # The gateway intermittently fails a read with an internal Python error
            # ("Error executing tool ...: 'NoneType' object has no attribute 'get'") that
            # succeeds on retry. Retry reads only; each retry counts against the budget.
            txt0 = ' '.join(c.get('text', '') for c in res.get('content') or []
                            if isinstance(c, dict))
            transient = res.get('isError') and 'Error executing tool' in txt0 and \
                'AUTH' not in txt0.upper()
            # Gateway-side network blips come back as a structured error, e.g.
            # {"error": {"code": "JIRA_UNREACHABLE", "message": "... ConnectTimeout ...",
            #  "hint": "Retry after a brief delay."}}
            if not transient and not res.get('isError'):
                _e = parse_tool_result(res)
                _e = _e.get('error') if isinstance(_e, dict) else None
                if isinstance(_e, dict):
                    _c = str(_e.get('code', '')).upper()
                    transient = ('UNREACHABLE' in _c or 'TIMEOUT' in _c or 'UNAVAILABLE' in _c
                                 or 'retry' in str(_e.get('hint', '')).lower())
                    transient = transient and 'AUTH' not in _c
            if not transient or attempt == attempts:
                break
            self.log(f'[MCP] {name}: transient gateway error, retry {attempt}/{attempts - 1}')
            time.sleep(5 * attempt)
        if res.get('isError'):
            txt = parse_tool_result(res)
            raise McpToolError(f'[{self.srv["key"]}] tool {name} failed: '
                               f'{txt.get("text") if isinstance(txt, dict) else txt}')
        out = parse_tool_result(res)
        # The Spectrum gateway reports failures as a normal result: {"error": {code, message,
        # hint}} with isError=false. Surface them (and never cache them).
        err = out.get('error') if isinstance(out, dict) else None
        if isinstance(err, dict) and (err.get('code') or err.get('message')):
            code = str(err.get('code', ''))
            msg = (f'[{self.srv["key"]}] {name}: {code} {err.get("message", "")}'
                   f'{" - " + err["hint"] if err.get("hint") else ""}')
            if 'AUTH' in code.upper() or re.search(r'\bPAT\b', str(err.get('message', ''))):
                raise McpAuthError(msg + f'. Fix the token in {mcp_config_path(self.cfg)}.')
            raise McpToolError(msg)
        if ttl > 0 and out is not None:
            cache_put(ck, out)
        return out
