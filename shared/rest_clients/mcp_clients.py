# -*- coding: utf-8 -*-
"""
mcp_clients.py -- drop-in, MCP-backed versions of JiraRestClient / ChalkRestClient.

Every existing caller does `JiraRestClient(logger_fn=log)` / `ChalkRestClient(...)` and then
uses fetch_issue(), get_page_html(), discover_pi_pages(), _get(url, params) ... All of those
funnel through `_get()`, so the MCP clients only override `_get()` (translating the old
REST URL into an MCP tool call and returning the same JSON shape) plus auth/health methods.
All parsing logic in the legacy classes is reused untouched.

`JiraRestClient` / `ChalkRestClient` exported from this module are toggle-aware: they
build the MCP client unless legacy direct access is switched ON. The choice is made at
construction time, so flipping the toggle takes effect for the next client created.
"""
import re
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from ._legacy_guard import legacy_enabled
from ._log_safe import safe_logger
from .chalk_rest_client import ChalkRestClient as _LegacyChalk
from .jira_rest_client import JiraRestClient as _LegacyJira

from shared.mcp_gateway import ops as _ops                      # noqa: E402
from shared.mcp_gateway.transport import McpError               # noqa: E402

_JIRA_ISSUE = re.compile(r'/rest/api/(?:2|latest)/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/?$')
_JIRA_SEARCH = re.compile(r'/rest/api/(?:2|latest)/search/?$')

_C_PAGE = re.compile(r'/rest/api/content/(\d+)/?$')
_C_CHILD = re.compile(r'/rest/api/content/(\d+)/child/page/?$')
_C_ATT = re.compile(r'/rest/api/content/(\d+)/child/attachment/?$')
_C_COMMENT = re.compile(r'/rest/api/content/(\d+)/child/comment/?$')
_C_LABEL = re.compile(r'/rest/api/content/(\d+)/label/?$')
_C_SEARCH = re.compile(r'/rest/api/(?:content/)?search/?$')
_C_CONTENT = re.compile(r'/rest/api/content/?$')

_NO_COOKIES = ('No cookie session to refresh: MCP auth is the PAT + LiteLLM key in the global '
               'Kiro MCP config (~/.kiro/settings/mcp.json).')


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ========================================================================================
# Jira
# ========================================================================================
class McpJiraClient(_LegacyJira):
    """JiraRestClient API, served by the approved Jira MCP server."""
    backend = 'mcp'

    def __init__(self, session_mgr=None, logger_fn=None):   # noqa: D401 - same signature
        self.log = safe_logger(logger_fn)
        self._request_delay = 0.0
        self._mgr = None
        self._session = None
        self.init_error: Optional[str] = None
        try:
            self._mcp = _ops.jira(self.log)
        except McpError as e:
            self._mcp = None
            self.init_error = str(e)
            self.log(f'[JIRA-MCP] {e}')

    def _get(self, url: str, params: Optional[Dict] = None, timeout: int = 30) -> Optional[Dict]:
        if self._mcp is None:
            return None
        path = urlparse(url).path
        p = params or {}
        try:
            m = _JIRA_ISSUE.search(path)
            if m:
                return self._mcp.get_issue(m.group(1).upper(), fields=p.get('fields'),
                                           expand=p.get('expand'))
            if _JIRA_SEARCH.search(path):
                return self._mcp.search(p.get('jql', ''), fields=p.get('fields'),
                                        max_results=_int(p.get('maxResults'), 50),
                                        start_at=_int(p.get('startAt'), 0))
            self.log(f'[JIRA-MCP] Not available through the MCP server: {path}')
            return None
        except McpError as e:
            self.log(f'[JIRA-MCP] {e}')
            return None

    def health_check(self) -> bool:
        if self._mcp is None:
            return False
        try:
            self._mcp.ping()
            self.log(f'[JIRA-MCP] Health check OK - approved MCP gateway '
                     f'(server "{self._mcp.client.srv["key"]}")')
            return True
        except McpError as e:
            self.log(f'[JIRA-MCP] Health check failed: {e}')
            return False

    def health_check_with_refresh(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        return self.health_check()

    def auto_refresh_session(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        self.log(f'[JIRA-MCP] {_NO_COOKIES}')
        return self.health_check()

    def _browser_refresh(self, *a, **k) -> bool:
        return False

    def _log_cookie_diagnosis(self):
        return None

    def download_attachment(self, url: str, save_path: Path) -> bool:
        self.log(f'[JIRA-MCP] Attachment download is not offered by the MCP server yet '
                 f'(ATLSUP: planned). Save it manually as {save_path}')
        return Path(save_path).exists()   # a manually placed file still counts


# ========================================================================================
# Chalk (Confluence)
# ========================================================================================
def _cql_quote(v: str) -> str:
    return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"') + '"'


class McpChalkClient(_LegacyChalk):
    """ChalkRestClient API, served by the approved Chalk MCP server."""
    backend = 'mcp'

    def __init__(self, session_mgr=None, logger_fn=None):
        self.log = safe_logger(logger_fn)
        self._request_delay = 0.0
        self._mgr = None
        self._session = None
        self._page_html_cache = {}
        self._parsed_content_cache = {}
        self.init_error: Optional[str] = None
        try:
            self._mcp = _ops.chalk(self.log)
        except McpError as e:
            self._mcp = None
            self.init_error = str(e)
            self.log(f'[CHALK-MCP] {e}')

    def _get(self, url: str, params: Optional[Dict] = None, timeout: int = 30) -> Optional[Dict]:
        if self._mcp is None:
            return None
        path = urlparse(url).path
        p = params or {}
        try:
            m = _C_PAGE.search(path)
            if m:
                return self._mcp.get_page(m.group(1), expand=p.get('expand'))
            m = _C_CHILD.search(path)
            if m:
                return self._mcp.get_children(m.group(1), limit=_int(p.get('limit'), 100))
            m = _C_ATT.search(path)
            if m:
                return self._mcp.get_attachments(m.group(1))
            m = _C_COMMENT.search(path)
            if m:
                return self._mcp.get_comments(m.group(1))
            m = _C_LABEL.search(path)
            if m:
                return self._mcp.get_labels(m.group(1))
            if _C_SEARCH.search(path):
                return self._mcp.search(p.get('cql', ''), limit=_int(p.get('limit'), 50))
            if _C_CONTENT.search(path) and p.get('title'):
                cql = f'title={_cql_quote(p["title"])} AND type=page'
                if p.get('spaceKey'):
                    cql = f'space={_cql_quote(p["spaceKey"])} AND ' + cql
                res = self._mcp.search(cql, limit=5)
                if res.get('results') and 'body' in str(p.get('expand', '')):
                    full = self._mcp.get_page(res['results'][0]['id'], expand=p.get('expand'))
                    res = {'results': [full] if full else [], 'size': 1 if full else 0}
                return res
            self.log(f'[CHALK-MCP] Not available through the MCP server: {path}')
            return None
        except McpError as e:
            self.log(f'[CHALK-MCP] {e}')
            return None

    def health_check(self) -> bool:
        if self._mcp is None:
            return False
        try:
            self._mcp.ping()
            self.log(f'[CHALK-MCP] Health check OK - approved MCP gateway '
                     f'(server "{self._mcp.client.srv["key"]}")')
            return True
        except McpError as e:
            self.log(f'[CHALK-MCP] Health check failed: {e}')
            return False

    def health_check_with_refresh(self, *a, **k) -> bool:
        return self.health_check()

    def auto_refresh_session(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        self.log(f'[CHALK-MCP] {_NO_COOKIES}')
        return self.health_check()

    def _browser_refresh(self, *a, **k) -> bool:
        return False


# ========================================================================================
# Toggle-aware entry points (what `from rest_clients import JiraRestClient` now returns)
# ========================================================================================
class JiraRestClient(_LegacyJira):
    """MCP by default; the old cookie REST client only when legacy direct access is ON."""

    def __new__(cls, *args, **kwargs):
        if cls is JiraRestClient and not legacy_enabled():
            return McpJiraClient(*args, **kwargs)
        return super().__new__(cls)


class ChalkRestClient(_LegacyChalk):
    """MCP by default; the old cookie REST client only when legacy direct access is ON."""

    def __new__(cls, *args, **kwargs):
        if cls is ChalkRestClient and not legacy_enabled():
            return McpChalkClient(*args, **kwargs)
        return super().__new__(cls)
