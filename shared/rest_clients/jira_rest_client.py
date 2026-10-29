# -*- coding: utf-8 -*-
"""
Jira REST Client — Direct REST API for Jira issue fetching.

Replaces browser-based Playwright page.evaluate(fetch(...)) with direct
requests.Session calls using cookies from storage_state.json.

Usage:
    from shared.rest_clients import JiraRestClient

    client = JiraRestClient()
    issue = client.fetch_issue('MWTGPROV-4166')
"""

import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .session_manager import RESTSessionManager

logger = logging.getLogger(__name__)

JIRA_BASE_URL = 'https://jira.charter.com'
JIRA_REST_V2 = f'{JIRA_BASE_URL}/rest/api/2'


@dataclass
class JiraAttachmentREST:
    filename: str = ''
    size: int = 0
    mime_type: str = ''
    url: str = ''
    author: str = ''


@dataclass
class JiraIssueREST:
    """Mirrors the JiraIssue dataclass from jira_fetcher.py."""
    key: str = ''
    summary: str = ''
    description: str = ''
    status: str = ''
    priority: str = ''
    issue_type: str = ''
    assignee: str = ''
    reporter: str = ''
    labels: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    fix_versions: List[str] = field(default_factory=list)
    acceptance_criteria: str = ''
    attachments: List[JiraAttachmentREST] = field(default_factory=list)
    linked_issues: List[Dict] = field(default_factory=list)
    subtasks: List[Dict] = field(default_factory=list)
    comments: List[Dict] = field(default_factory=list)
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    raw_json: Dict = field(default_factory=dict)
    pi: str = ''
    channel: str = ''


class JiraRestClient:
    """Direct REST API client for Jira Server.

    Uses cookies from storage_state.json (same SSO session as browser).
    Falls back gracefully — returns None on auth failure so caller can
    use browser fallback.
    """

    def __init__(self, session_mgr: Optional[RESTSessionManager] = None, logger_fn=None):
        self._mgr = session_mgr or RESTSessionManager()
        self._session = self._mgr.get_session(JIRA_BASE_URL)
        # Encoding-proof: a failed emoji log must never fail an API call. See _log_safe.
        from ._log_safe import safe_logger
        self.log = safe_logger(logger_fn)
        # 50ms between requests. Was 300ms, which on a 217-issue sync (~1085 calls) spent
        # ~5.4 of the ~11 minutes asleep. The real rate-limit protection is the 429
        # handler in _get(), which backs off 2s/4s/6s on demand; this delay is only a
        # politeness gap. Override per instance if a server ever needs it wider.
        self._request_delay = 0.05

    def _get(self, url: str, params: Optional[Dict] = None, timeout: int = 30) -> Optional[Dict]:
        """Make authenticated GET request to Jira REST API."""
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('JIRA-REST', self.log):
            return None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                time.sleep(self._request_delay)
                resp = self._session.get(url, params=params, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    # Rate limited — back off and retry
                    wait = (attempt + 1) * 2
                    self.log(f'[JIRA-REST] Rate limited (429) — waiting {wait}s...')
                    time.sleep(wait)
                    continue
                elif resp.status_code in (401, 403):
                    self.log(f'[JIRA-REST] Auth failed ({resp.status_code}) — cookies may be expired')
                    return None
                else:
                    self.log(f'[JIRA-REST] HTTP {resp.status_code}: {url[:80]}')
                    return None
            except requests.RequestException as e:
                self.log(f'[JIRA-REST] Request failed: {e}')
                return None
        self.log(f'[JIRA-REST] Max retries exceeded for {url[:80]}')
        return None

    def health_check(self) -> bool:
        """Verify REST client can reach Jira API.

        Returns True only if we get a 200 response (authenticated).
        429 = rate limited (may still work with backoff).
        401/403 = no valid Jira session cookies.
        """
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('JIRA-REST', self.log):
            return False
        # Check if we have meaningful Jira cookies (not just ad trackers)
        from urllib.parse import urlparse
        domain = urlparse(JIRA_BASE_URL).hostname or ''
        jira_auth_cookies = [
            c for c in self._mgr._cookies
            if domain.endswith(c.get('domain', '').lstrip('.'))
            and c.get('name', '') not in ('__adroll_fpc', '_fbp', '_ga', '_gid', '_gcl_au')
            and not c.get('name', '').startswith('_')
        ]
        if not jira_auth_cookies:
            self.log('[JIRA-REST] No Jira auth cookies found — need browser login to jira.charter.com')
            self._log_cookie_diagnosis()
            return False

        data = self._get(f'{JIRA_REST_V2}/myself')
        if data and data.get('key'):
            self.log(f'[JIRA-REST] Health check OK — user: {data.get("displayName", "?")}')
            return True
        # Auth failed. Say WHY and WHAT TO DO: _get() only reports the bare status code, and this
        # runs BEFORE the caller commits to the slow browser path, so the diagnosis is actionable
        # rather than an explanation after the fact.
        self._log_cookie_diagnosis()
        return False

    def _log_cookie_diagnosis(self):
        """Log which Jira cookies are expired/valid and how to fix it. Never raises."""
        try:
            st = self._mgr.cookie_status(JIRA_BASE_URL)
        except Exception:
            return
        self.log('[JIRA-REST] Cookie diagnosis: %s' % st.get('summary', '?'))
        if st.get('storage_path'):
            self.log('[JIRA-REST]   source: %s' % st['storage_path'])
        for name, hrs in (st.get('expired') or [])[:6]:
            self.log('[JIRA-REST]   EXPIRED %-28s %.1fh ago' % (name, hrs))
        for name, hrs in (st.get('valid') or [])[:6]:
            self.log('[JIRA-REST]   valid   %-28s %.1fh left' % (name, hrs))
        if not st.get('usable'):
            self.log('[JIRA-REST]   FIX: log in to jira.charter.com in a browser so a fresh '
                     'storage_state.json is saved, then retry — otherwise this run uses the '
                     'SLOW browser path.')

    def auto_refresh_session(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        """Refresh Jira session cookies. Mirrors ChalkRestClient.auto_refresh_session.

        ONE quick headless attempt (works only where the box auto-negotiates Windows SSO with no
        prompt); if that does not yield a LIVE session we stop retrying headless - it cannot type
        credentials - and open a VISIBLE browser so the user logs in ONCE. Cookies are saved to
        storage_state.json the moment the Jira API authenticates.
        """
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            self.log('[JIRA-REST] Playwright not installed - cannot refresh session')
            return False

        storage_path = self._mgr.storage_path
        if not storage_path:
            storage_path = Path(__file__).resolve().parent.parent.parent / 'storage_state.json'

        # 1) FAST PATH - one silent headless attempt (auto SSO negotiation, no prompt).
        if headless:
            if self._browser_refresh(storage_path, headless=True, wait_secs=0) and self.health_check():
                self.log('[JIRA-REST] Session refreshed (headless SSO)')
                return True
            self.log('[JIRA-REST] headless SSO could not establish a session - opening a VISIBLE '
                     'browser so you can log in once; cookies save automatically and the run continues.')

        # 2) INTERACTIVE - visible browser; poll the real Jira API and save cookies on auth.
        return (self._browser_refresh(storage_path, headless=False, wait_secs=interactive_wait)
                and self.health_check())

    def _browser_refresh(self, storage_path, headless: bool = True, wait_secs: int = 0) -> bool:
        """Drive a browser to Jira and save cookies to storage_path.

        When wait_secs > 0 the browser is VISIBLE and we poll the live Jira API until it
        authenticates, saving the instant it works. Returns True if cookies were saved.
        """
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('JIRA-REST', self.log):
            return False
        from playwright.sync_api import sync_playwright
        import time as _t
        jira_url = f'{JIRA_BASE_URL}/secure/Dashboard.jspa'
        _args = ['--disable-blink-features=AutomationControlled', '--no-sandbox',
                 '--auth-server-allowlist=*.charter.com,*.spectrum.net']
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=headless, channel='msedge', args=_args)
                except Exception:
                    browser = pw.chromium.launch(headless=headless, args=_args)
                _ctx_kw = {'viewport': {'width': 1600, 'height': 1000}, 'ignore_https_errors': True}
                if Path(storage_path).exists():
                    _ctx_kw['storage_state'] = str(storage_path)   # reuse still-valid cookies
                context = browser.new_context(**_ctx_kw)
                page = context.new_page()
                page.set_default_timeout(30000)
                try:
                    page.goto(jira_url, wait_until='domcontentloaded', timeout=45000)
                    if not headless:
                        try:
                            from shared.browser_focus import pop_to_front
                            pop_to_front(page)
                        except Exception:
                            pass
                except Exception as exc:
                    self.log(f'[JIRA-REST] navigation warning: {exc}')

                if wait_secs and wait_secs > 0:
                    self.log(f'[JIRA-REST] Log in to jira.charter.com in the browser window - '
                             f'waiting up to {wait_secs}s (saves automatically once authenticated)...')
                    deadline = _t.time() + wait_secs
                    ok = False
                    while _t.time() < deadline:
                        try:
                            context.storage_state(path=str(storage_path))
                            self._mgr.refresh_cookies(storage_path)
                            self._session = self._mgr.get_session(JIRA_BASE_URL)
                            if self.health_check():
                                ok = True
                                break
                        except Exception:
                            pass
                        _t.sleep(5)
                    browser.close()
                    if ok:
                        self.log(f'[JIRA-REST] Logged in - cookies saved to {Path(storage_path).name}')
                    else:
                        self.log('[JIRA-REST] login window closed/timed out - session not saved.')
                    return ok

                # Headless fast path: give SSO a moment, save once, let caller verify.
                try:
                    page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    _t.sleep(3)
                Path(storage_path).parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(storage_path))
                browser.close()
                self._mgr.refresh_cookies(storage_path)
                self._session = self._mgr.get_session(JIRA_BASE_URL)
                return True
        except Exception as e:
            self.log(f'[JIRA-REST] browser refresh failed: {e}')
            return False

    def health_check_with_refresh(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        """Health check with automatic session refresh on 401.

        1. health_check() with existing cookies
        2. on failure -> auto_refresh_session() to get fresh cookies
        3. re-check

        This is what callers should use instead of health_check(): every other REST client in
        this package already does (ChalkRestClient, NbopRestClient), which is why a Jira sync
        fell back to the SLOW browser path on an expired JSESSIONID instead of re-authenticating.
        """
        if self.health_check():
            return True
        self.log('[JIRA-REST] Health check failed - attempting session refresh...')
        return self.auto_refresh_session(headless=headless, interactive_wait=interactive_wait)

    def fetch_issue(self, issue_key: str, log=None) -> Optional[JiraIssueREST]:
        """Fetch a Jira issue via REST API.

        Returns:
            JiraIssueREST if successful, None on failure.
        """
        _log = log or self.log
        _log(f'[JIRA-REST] Fetching {issue_key}...')

        data = self._get(f'{JIRA_REST_V2}/issue/{issue_key}',
                         params={'expand': 'renderedFields'})
        if not data:
            return None

        f = data.get('fields', {})
        issue = JiraIssueREST(key=issue_key)
        issue.raw_json = data
        issue.summary = f.get('summary', '')
        issue.description = f.get('description', '') or ''
        issue.status = (f.get('status') or {}).get('name', '')
        issue.priority = (f.get('priority') or {}).get('name', '')
        issue.issue_type = (f.get('issuetype') or {}).get('name', '')
        issue.assignee = (f.get('assignee') or {}).get('displayName', 'Unassigned')
        issue.reporter = (f.get('reporter') or {}).get('displayName', 'Unknown')
        issue.labels = f.get('labels', [])
        issue.components = [c.get('name', '') for c in f.get('components', [])]
        issue.fix_versions = [v.get('name', '') for v in f.get('fixVersions', [])]

        # Auto-detect PI and Channel
        for lbl in issue.labels:
            if lbl.upper().startswith('PI-'):
                issue.pi = lbl
                break
        text = (' '.join(issue.labels) + ' ' + issue.summary).upper()
        issue.channel = 'NBOP' if 'NBOP' in text else 'ITMBO'

        # Acceptance Criteria — scan custom text fields
        AC_KEYWORDS = ['acceptance criteria', 'acceptance', 'shall be', 'must be',
                       'given', 'when', 'then', 'verify that', 'requirement']
        for key, val in sorted(f.items()):
            if not key.startswith('customfield_'):
                continue
            if not val or not isinstance(val, str) or len(val) < 30:
                continue
            if any(kw in val.lower() for kw in AC_KEYWORDS):
                issue.acceptance_criteria = val
                _log(f'[JIRA-REST] ✅ AC found in {key} ({len(val)} chars)')
                break

        # Store meaningful custom fields
        for key, val in f.items():
            if not key.startswith('customfield_') or not val:
                continue
            if isinstance(val, str) and len(val) > 20:
                issue.custom_fields[key] = val
            elif isinstance(val, dict) and val.get('value'):
                issue.custom_fields[key] = val['value']

        # Attachments
        for a in f.get('attachment', []):
            issue.attachments.append(JiraAttachmentREST(
                filename=a.get('filename', ''), size=a.get('size', 0),
                mime_type=a.get('mimeType', ''), url=a.get('content', ''),
                author=(a.get('author') or {}).get('displayName', '')))

        # Linked issues
        for lnk in f.get('issuelinks', []):
            for direction in ['outwardIssue', 'inwardIssue']:
                if lnk.get(direction):
                    i = lnk[direction]
                    issue.linked_issues.append({
                        'direction': lnk.get('type', {}).get(direction.replace('Issue', ''), ''),
                        'key': i['key'],
                        'summary': i['fields']['summary']})

        # Deep-fetch linked issue descriptions
        if issue.linked_issues:
            _log(f'[JIRA-REST] 🔍 Deep-fetching {len(issue.linked_issues)} linked issues...')
            for li in issue.linked_issues:
                li_data = self._get(f'{JIRA_REST_V2}/issue/{li["key"]}')
                if li_data and li_data.get('fields'):
                    lf = li_data['fields']
                    li['description'] = lf.get('description', '') or ''
                    li['status'] = (lf.get('status') or {}).get('name', '')
                    li['issue_type'] = (lf.get('issuetype') or {}).get('name', '')
                    for fkey, val in sorted(lf.items()):
                        if fkey.startswith('customfield_') and isinstance(val, str) and len(val) > 30:
                            if any(kw in val.lower() for kw in ['acceptance', 'criteria', 'expected', 'actual']):
                                li['acceptance_criteria'] = val
                                break

        # Subtasks
        for s in f.get('subtasks', []):
            issue.subtasks.append({
                'key': s.get('key', ''),
                'summary': s.get('fields', {}).get('summary', ''),
                'status': s.get('fields', {}).get('status', {}).get('name', '')})

        # Deep-fetch subtask descriptions
        if issue.subtasks:
            _log(f'[JIRA-REST] 🔍 Deep-fetching {len(issue.subtasks)} subtasks...')
            for st in issue.subtasks:
                st_data = self._get(f'{JIRA_REST_V2}/issue/{st["key"]}')
                if st_data and st_data.get('fields'):
                    sf = st_data['fields']
                    st['description'] = sf.get('description', '') or ''
                    st['labels'] = sf.get('labels', [])
                    st['issue_type'] = (sf.get('issuetype') or {}).get('name', '')
                    for fkey, val in sorted(sf.items()):
                        if fkey.startswith('customfield_') and isinstance(val, str) and len(val) > 30:
                            if any(kw in val.lower() for kw in ['shall', 'must', 'verify', 'when', 'then']):
                                st['acceptance_criteria'] = val
                                break
                    st_attachments = sf.get('attachment', [])
                    if st_attachments:
                        st['attachments'] = [
                            {'filename': a.get('filename', ''), 'size': a.get('size', 0),
                             'mimeType': a.get('mimeType', ''), 'url': a.get('content', '')}
                            for a in st_attachments]
                    st_comments = sf.get('comment', {}).get('comments', [])
                    if st_comments:
                        st['comments'] = [
                            {'author': (c.get('author') or {}).get('displayName', ''),
                             'created': c.get('created', '')[:10],
                             'body': c.get('body', '')}
                            for c in st_comments[:10]]

        # Epic children — capture every issue under "Issues in epic" for ANY parent
        # type (not just Epic), de-duped by key against existing subtasks/links.
        _log(f'[JIRA-REST] 🔍 Fetching epic children...')
        jql = f'"Epic Link" = "{issue_key}" ORDER BY key ASC'
        search_data = self._get(f'{JIRA_REST_V2}/search',
                                params={'jql': jql, 'maxResults': 50,
                                        'fields': 'summary,description,status,issuetype,labels,attachment'})
        if search_data and search_data.get('issues'):
            existing_keys = {s.get('key', '') for s in issue.subtasks}
            existing_keys |= {l.get('key', '') for l in issue.linked_issues}
            added = 0
            for ei in search_data['issues']:
                ck = ei.get('key', '')
                if ck in existing_keys:
                    continue
                ef = ei.get('fields', {})
                child = {
                    'key': ck,
                    'summary': ef.get('summary', ''),
                    'description': ef.get('description', '') or '',
                    'status': (ef.get('status') or {}).get('name', ''),
                    'issue_type': (ef.get('issuetype') or {}).get('name', ''),
                    'labels': ef.get('labels', []),
                }
                st_attachments = ef.get('attachment', [])
                if st_attachments:
                    child['attachments'] = [
                        {'filename': a.get('filename', ''), 'size': a.get('size', 0),
                         'mimeType': a.get('mimeType', ''), 'url': a.get('content', '')}
                        for a in st_attachments]
                issue.subtasks.append(child)
                existing_keys.add(ck)
                added += 1
            _log(f'[JIRA-REST] Found {len(search_data["issues"])} epic children ({added} new)')

        # Comments (first 20)
        for c in f.get('comment', {}).get('comments', [])[:20]:
            issue.comments.append({
                'author': (c.get('author') or {}).get('displayName', ''),
                'created': c.get('created', '')[:10],
                'body': c.get('body', '')})

        _log(f'[JIRA-REST] ✅ {issue.key} | {issue.summary[:60]}')
        _log(f'[JIRA-REST]    Status={issue.status} | Priority={issue.priority} | PI={issue.pi}')
        _log(f'[JIRA-REST]    Attachments={len(issue.attachments)} | Links={len(issue.linked_issues)} | AC={bool(issue.acceptance_criteria)}')
        return issue

    def download_attachment(self, url: str, save_path: Path) -> bool:
        """Download a Jira attachment via REST.

        Args:
            url: Attachment content URL
            save_path: Local path to save the file

        Returns:
            True if download succeeded.
        """
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('JIRA-REST', self.log):
            return False
        try:
            time.sleep(self._request_delay)
            resp = self._session.get(url, timeout=60, stream=True)
            if resp.status_code == 200:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            else:
                self.log(f'[JIRA-REST] Download failed: HTTP {resp.status_code}')
                return False
        except Exception as e:
            self.log(f'[JIRA-REST] Download error: {e}')
            return False
