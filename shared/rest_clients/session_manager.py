# -*- coding: utf-8 -*-
"""
REST Session Manager — Cookie-based auth from storage_state.json.

Shared across all REST clients (Chalk, Genesis, Century, NBOP).
Loads Playwright's storage_state.json and builds a requests.Session
with the same cookies — enabling REST API calls without a browser.

Usage:
    from shared.rest_clients import RESTSessionManager

    mgr = RESTSessionManager()
    session = mgr.get_session('https://chalk.charter.com')
    resp = session.get('https://chalk.charter.com/rest/api/content/12345')
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

import requests

logger = logging.getLogger(__name__)

# Known storage_state.json locations (searched in order)
_STORAGE_PATHS = [
    Path(__file__).resolve().parent.parent.parent / 'MDA-Jira-Dashboard' / 'storage_state.json',
    Path(__file__).resolve().parent.parent.parent / 'storage_state.json',
    Path(__file__).resolve().parent.parent.parent / 'TMO DashBoard' / 'storage_state.json',
    Path(__file__).resolve().parent.parent.parent / 'TestSuiteExecutor' / 'storage_state.json',
    Path(__file__).resolve().parent.parent.parent / 'TestSuiteGenerator' / 'storage_state.json',
]


# Cookie names that actually carry the authenticated session. An expired one of these means a
# REST call WILL fail with 401, regardless of what other cookies survive.
_AUTH_COOKIE_HINTS = ('jsessionid', 'rememberme', 'smsession', 'tgc', 'session_id',
                      'sessionid', 'auth_token', 'access_token')


def _is_auth_cookie(name: str) -> bool:
    n = (name or '').strip().lower()
    return any(h in n for h in _AUTH_COOKIE_HINTS)


class RESTSessionManager:
    """Manages authenticated HTTP sessions using saved browser cookies.

    Loads cookies from Playwright's storage_state.json and creates
    requests.Session objects pre-configured with those cookies.

    Supports multiple domains (Chalk, Jira, Genesis, etc.) from the
    same storage_state.json file.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize with optional explicit storage_state.json path.

        Args:
            storage_path: Path to storage_state.json. If None, searches known locations.
        """
        self.storage_path = storage_path or self._find_storage_state()
        self._cookies: List[Dict] = []
        self._sessions: Dict[str, requests.Session] = {}

        if self.storage_path and self.storage_path.exists():
            self._load_cookies()
            logger.info(f"[REST-SESSION] Loaded {len(self._cookies)} cookies from {self.storage_path.name}")
        else:
            logger.warning("[REST-SESSION] No storage_state.json found — sessions will be unauthenticated")

    def _find_storage_state(self) -> Optional[Path]:
        """Find the most recent storage_state.json from known paths."""
        existing = [p for p in _STORAGE_PATHS if p.exists()]
        if not existing:
            return None
        return max(existing, key=lambda p: p.stat().st_mtime)

    def _load_cookies(self):
        """Load cookies from storage_state.json."""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self._cookies = state.get('cookies', [])
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[REST-SESSION] Failed to load cookies: {e}")
            self._cookies = []

    def cookie_status(self, base_url: str) -> Dict:
        """Diagnose the cookies available for `base_url`'s domain.

        Returns a dict describing WHY an authenticated call will or will not work, so a caller
        can say "JSESSIONID expired 155.7h ago" instead of "cookies may be expired". Pure
        inspection - makes no network call.

        Keys:
            domain        hostname taken from base_url
            storage_path  the storage_state.json these cookies came from (str, '' if none)
            total         cookies matching the domain
            expired       [(name, hours_ago), ...] cookies whose expiry has passed
            valid         [(name, hours_left), ...] cookies still in date
            session       [name, ...] session cookies (no expiry)
            usable        False when there are no domain cookies at all, or every
                          expiry-bearing cookie has expired
            summary       one-line human-readable diagnosis
        """
        import time
        from urllib.parse import urlparse

        domain = urlparse(base_url).hostname or ''
        now = time.time()
        expired, valid, session = [], [], []

        for c in self._cookies:
            cdom = (c.get('domain', '') or '').lstrip('.')
            if not cdom or not domain.endswith(cdom):
                continue
            name = c.get('name', '?')
            exp = c.get('expires', 0) or 0
            try:
                exp = float(exp)
            except (TypeError, ValueError):
                exp = 0
            if exp <= 0:
                session.append(name)
            elif exp < now:
                expired.append((name, (now - exp) / 3600.0))
            else:
                valid.append((name, (exp - now) / 3600.0))

        total = len(expired) + len(valid) + len(session)

        # An expired SESSION AUTHENTICATOR is decisive: JSESSIONID is what actually authenticates
        # a Jira/Confluence REST call, so once it lapses the call 401s no matter how many other
        # cookies are still in date. Counting "any valid cookie" as usable was wrong - a broad
        # .charter.com analytics cookie masked an expired JSESSIONID and reported a healthy
        # session for a run that was about to fall back to the slow browser path.
        dead_auth = [(n, h) for n, h in expired if _is_auth_cookie(n)]
        live_auth = [n for n, _h in valid if _is_auth_cookie(n)]
        usable = bool(total) and not dead_auth

        if not total:
            summary = 'no cookies for %s - browser login required' % domain
        elif dead_auth:
            worst = max(dead_auth, key=lambda t: t[1])
            summary = ('%s expired %.1fh ago - refresh the session (REST will 401)'
                       % (worst[0], worst[1]))
        elif expired:
            summary = ('%d non-auth cookie(s) expired, %d valid - session still usable'
                       % (len(expired), len(valid) + len(session)))
        else:
            summary = '%d cookie(s) in date%s' % (
                len(valid) + len(session),
                ' (auth: %s)' % ', '.join(live_auth) if live_auth else '')

        return {'domain': domain,
                'storage_path': str(self.storage_path) if self.storage_path else '',
                'total': total, 'expired': expired, 'valid': valid,
                'session': session, 'usable': usable, 'summary': summary}


    def get_session(self, base_url: str) -> requests.Session:
        """Get an authenticated requests.Session for a given base URL.

        Cookies are filtered to match the domain of the base_url.
        Sessions are cached per domain.

        Args:
            base_url: The base URL (e.g., 'https://chalk.charter.com')

        Returns:
            requests.Session with cookies set
        """
        from urllib.parse import urlparse
        domain = urlparse(base_url).hostname or ''

        if domain in self._sessions:
            return self._sessions[domain]

        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0',
            'Accept': 'application/json',
        })

        # Apply cookies matching this domain
        matched = 0
        for cookie in self._cookies:
            cookie_domain = cookie.get('domain', '')
            # Match if cookie domain is a suffix of the target domain
            if domain.endswith(cookie_domain.lstrip('.')):
                session.cookies.set(
                    cookie['name'],
                    cookie['value'],
                    domain=cookie_domain,
                    path=cookie.get('path', '/'),
                )
                matched += 1

        logger.info(f"[REST-SESSION] Session for {domain}: {matched} cookies applied")
        self._sessions[domain] = session
        return session

    def is_authenticated(self, base_url: str) -> bool:
        """Quick check if we have cookies for a domain.

        Args:
            base_url: The base URL to check

        Returns:
            True if cookies exist for this domain
        """
        from urllib.parse import urlparse
        domain = urlparse(base_url).hostname or ''
        return any(
            domain.endswith(c.get('domain', '').lstrip('.'))
            for c in self._cookies
        )

    def refresh_cookies(self, storage_path: Optional[Path] = None):
        """Reload cookies from storage_state.json (after a fresh login).

        Args:
            storage_path: Optional new path. Uses existing path if None.
        """
        if storage_path:
            self.storage_path = storage_path
        self._sessions.clear()
        self._load_cookies()
        logger.info(f"[REST-SESSION] Refreshed: {len(self._cookies)} cookies")
