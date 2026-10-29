# -*- coding: utf-8 -*-
"""
Chalk REST Client — Confluence REST API for TSG data extraction.

Replaces browser-based Playwright scraping of Chalk (Confluence) pages
with direct REST API calls. ~12x faster than browser path.

Confluence REST API docs: https://developer.atlassian.com/server/confluence/confluence-rest-api-examples/

Usage:
    from shared.rest_clients import ChalkRestClient

    client = ChalkRestClient()
    # Get page content by ID
    html = client.get_page_html(page_id='3007682647')
    # Search for pages
    results = client.search('space=MDA AND title~"PI-54"')
    # Get child pages
    children = client.get_child_pages(page_id='3007682647')
"""

import re
import time
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from .session_manager import RESTSessionManager

logger = logging.getLogger(__name__)

CHALK_BASE_URL = 'https://chalk.charter.com'
CHALK_SPACE = 'MDA'
TMO_TESTING_SCOPE_PAGE_ID = '3007682647'

# Confluence REST API base
_API_BASE = f'{CHALK_BASE_URL}/rest/api'


@dataclass
class ChalkPage:
    """Represents a Confluence page."""
    page_id: str = ''
    title: str = ''
    url: str = ''
    body_html: str = ''


@dataclass
class PIPageInfo:
    """PI page discovered from TMO Testing Scope."""
    label: str = ''      # e.g., "PI-54"
    page_id: str = ''
    url: str = ''
    number: int = 0      # e.g., 54


class ChalkRestClient:
    """Direct REST API client for Chalk (Confluence Server).

    Uses the Confluence REST API to fetch page content, search pages,
    and list child pages — eliminating browser automation entirely.

    Auth: Uses cookies from storage_state.json (same SSO session).
    Fallback: Returns None/empty on failure (caller should fall back to browser).
    """

    def __init__(self, session_mgr: Optional[RESTSessionManager] = None, logger_fn=None):
        """Initialize Chalk REST client.

        Args:
            session_mgr: RESTSessionManager instance. Creates one if None.
            logger_fn: Optional logging function (defaults to print).
        """
        self._mgr = session_mgr or RESTSessionManager()
        self._session = self._mgr.get_session(CHALK_BASE_URL)
        # Encoding-proof: a failed emoji log must never fail an API call. See _log_safe.
        from ._log_safe import safe_logger
        self.log = safe_logger(logger_fn)
        self._request_delay = 0.2  # 200ms between requests (rate limiting)
        # One sync reuses a client, so cache successful page fetches and soup extraction.
        self._page_html_cache: Dict[str, str] = {}
        self._parsed_content_cache: Dict[str, Tuple[str, List[List[List[str]]]]] = {}

    def _get(self, url: str, params: Optional[Dict] = None, timeout: int = 30) -> Optional[Dict]:
        """Make authenticated GET request to Confluence API.

        Returns:
            JSON response dict, or None on failure.
        """
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('CHALK-REST', self.log):
            return None
        try:
            time.sleep(self._request_delay)
            resp = self._session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code in (401, 403):
                self.log(f'[CHALK-REST] Auth failed ({resp.status_code}) — cookies may be expired')
                return None
            else:
                self.log(f'[CHALK-REST] HTTP {resp.status_code}: {url[:80]}')
                return None
        except requests.RequestException as e:
            self.log(f'[CHALK-REST] Request failed: {e}')
            return None

    # ─── Core API Methods ─────────────────────────────────────────────────────

    def get_page_content(self, page_id: str, expand: str = 'body.storage') -> Optional[ChalkPage]:
        """Get a Confluence page by ID with expanded body content.

        Args:
            page_id: Confluence page ID (numeric string)
            expand: Fields to expand (default: body.storage for HTML)

        Returns:
            ChalkPage with HTML body, or None on failure.
        """
        url = f'{_API_BASE}/content/{page_id}'
        data = self._get(url, params={'expand': expand})
        if not data:
            return None

        page = ChalkPage(
            page_id=str(data.get('id', page_id)),
            title=data.get('title', ''),
            url=f"{CHALK_BASE_URL}{data.get('_links', {}).get('webui', '')}",
        )

        # Extract HTML body
        body = data.get('body', {})
        if 'storage' in body:
            page.body_html = body['storage'].get('value', '')
        elif 'view' in body:
            page.body_html = body['view'].get('value', '')

        return page

    def get_page_html(self, page_id: str) -> Optional[str]:
        """Get just the HTML body of a page.

        Args:
            page_id: Confluence page ID

        Returns:
            HTML string, or None on failure.
        """
        if page_id in self._page_html_cache:
            return self._page_html_cache[page_id]

        page = self.get_page_content(page_id)
        if page is None:
            return None
        self._page_html_cache[page_id] = page.body_html
        return page.body_html

    def get_child_pages(self, page_id: str, limit: int = 100) -> List[ChalkPage]:
        """Get child pages of a given page.

        Args:
            page_id: Parent page ID
            limit: Max results

        Returns:
            List of ChalkPage objects (without body content).
        """
        url = f'{_API_BASE}/content/{page_id}/child/page'
        data = self._get(url, params={'limit': limit, 'expand': 'version'})
        if not data:
            return []

        results = []
        for item in data.get('results', []):
            results.append(ChalkPage(
                page_id=str(item.get('id', '')),
                title=item.get('title', ''),
                url=f"{CHALK_BASE_URL}{item.get('_links', {}).get('webui', '')}",
            ))
        return results

    def search(self, cql: str, limit: int = 50) -> List[ChalkPage]:
        """Search Confluence using CQL (Confluence Query Language).

        Args:
            cql: CQL query string (e.g., 'space=MDA AND title~"PI-54"')
            limit: Max results

        Returns:
            List of ChalkPage objects matching the query.
        """
        url = f'{_API_BASE}/content/search'
        data = self._get(url, params={'cql': cql, 'limit': limit})
        if not data:
            return []

        results = []
        for item in data.get('results', []):
            results.append(ChalkPage(
                page_id=str(item.get('id', '')),
                title=item.get('title', ''),
                url=f"{CHALK_BASE_URL}{item.get('_links', {}).get('webui', '')}",
            ))
        return results

    def get_page_by_title(self, title: str, space: str = CHALK_SPACE) -> Optional[ChalkPage]:
        """Find a page by exact title in a space.

        Args:
            title: Page title to search for
            space: Confluence space key (default: MDA)

        Returns:
            ChalkPage if found, None otherwise.
        """
        url = f'{_API_BASE}/content'
        data = self._get(url, params={
            'spaceKey': space,
            'title': title,
            'expand': 'body.storage',
        })
        if not data or not data.get('results'):
            return None

        item = data['results'][0]
        page = ChalkPage(
            page_id=str(item.get('id', '')),
            title=item.get('title', ''),
            url=f"{CHALK_BASE_URL}{item.get('_links', {}).get('webui', '')}",
        )
        body = item.get('body', {})
        if 'storage' in body:
            page.body_html = body['storage'].get('value', '')
        return page

    # ─── TSG-Specific Methods ─────────────────────────────────────────────────

    def discover_pi_pages(self, pi_range: range = range(46, 100)) -> List[PIPageInfo]:
        """Discover PI pages from the TMO Testing Scope page tree.

        Equivalent to chalk_parser.discover_pi_links() but via REST.

        Args:
            pi_range: Range of PI numbers to look for

        Returns:
            List of PIPageInfo objects sorted by PI number.
        """
        self.log('[CHALK-REST] Discovering PI pages...')

        # Get child pages of TMO Testing Scope
        children = self.get_child_pages(TMO_TESTING_SCOPE_PAGE_ID, limit=200)
        if not children:
            # Fallback: search by title pattern
            self.log('[CHALK-REST] No children found — trying CQL search...')
            children = self.search(f'space={CHALK_SPACE} AND ancestor={TMO_TESTING_SCOPE_PAGE_ID}', limit=200)

        pi_pages = []
        pi_pat = re.compile(r'PI[-\s]?(\d+)', re.IGNORECASE)

        for child in children:
            m = pi_pat.search(child.title)
            if m:
                num = int(m.group(1))
                if num in pi_range:
                    pi_pages.append(PIPageInfo(
                        label=child.title.strip(),
                        page_id=child.page_id,
                        url=child.url,
                        number=num,
                    ))

        pi_pages.sort(key=lambda x: x.number)
        self.log(f'[CHALK-REST] Found {len(pi_pages)} PI pages: {", ".join(p.label for p in pi_pages)}')
        return pi_pages

    def _get_parsed_page_content(
            self, page_id: str) -> Optional[Tuple[str, List[List[List[str]]]]]:
        """Fetch and parse a page once for feature discovery and extraction."""
        if page_id in self._parsed_content_cache:
            return self._parsed_content_cache[page_id]

        html = self.get_page_html(page_id)
        if html is None:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        full_text = soup.get_text(separator='\n')
        tables = []
        for table in soup.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['th', 'td'])]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)

        parsed = (full_text, tables)
        self._parsed_content_cache[page_id] = parsed
        self.log(f'[CHALK-REST] Extracted {len(full_text)} chars, {len(tables)} tables')
        return parsed

    def discover_features_on_pi(self, page_id: str) -> Optional[List[Tuple[str, str]]]:
        """Get all MWTGPROV-XXXX features from a PI page.

        Equivalent to chalk_parser.discover_features_on_pi() but via REST.

        Args:
            page_id: PI page ID

        Returns:
            List of (feature_id, title) tuples.
        """
        self.log(f'[CHALK-REST] Fetching features from page {page_id}...')
        parsed = self._get_parsed_page_content(page_id)
        if parsed is None:
            return None
        text, _tables = parsed

        features = []
        seen = set()
        feature_pat = re.compile(r'(MWTGPROV-\d{3,5})', re.IGNORECASE)

        # Skip lines that are clearly not titles
        _skip_prefixes = ('TS_', 'Scenario', 'SNO', 'Pre-req', 'Step ', 'Scope', 'Rules',
                          'CDR', 'Validation', 'Test Scenario', 'Device Type')

        lines_list = text.split('\n')
        for line_idx, line in enumerate(lines_list):
            matches = feature_pat.findall(line)
            for fid in matches:
                fid_upper = fid.upper()
                if fid_upper in seen:
                    continue
                seen.add(fid_upper)

                # Extract title — text after feature ID on same line
                idx = line.upper().find(fid_upper)
                title = line[idx + len(fid_upper):].strip().lstrip('-: ').strip()

                # Multi-line lookahead: same logic as browser path
                # Feature IDs on Chalk often appear alone on a line; the title is on the next line
                if len(title) < 10:
                    for k in range(1, 6):
                        if line_idx + k >= len(lines_list):
                            break
                        next_line = lines_list[line_idx + k].strip()
                        if (next_line
                                and len(next_line) > 5
                                and not feature_pat.search(next_line)
                                and not next_line.isdigit()
                                and not next_line.startswith('TS_')
                                and not any(next_line.startswith(p) for p in _skip_prefixes)):
                            title = next_line
                            break

                if len(title) > 120:
                    title = title[:120] + '...'
                if not title or title.isdigit():
                    title = '(no title)'
                features.append((fid_upper, title))

        features.sort(key=lambda x: int(x[0].split('-')[1]))
        self.log(f'[CHALK-REST] Found {len(features)} features')
        return features

    def fetch_feature_content(self, page_id: str, feature_id: str) -> Tuple[str, List[List[List[str]]]]:
        """Fetch page content and extract text + tables for a feature.

        Equivalent to chalk_parser.fetch_feature_from_pi() data extraction,
        but without browser. Returns raw text and tables for the existing
        parser to process.

        Args:
            page_id: PI page ID
            feature_id: Feature ID to extract (e.g., 'MWTGPROV-4166')

        Returns:
            Tuple of (full_text, tables) where tables is list of list of list of strings.
        """
        self.log(f'[CHALK-REST] Fetching content for {feature_id} from page {page_id}...')
        parsed = self._get_parsed_page_content(page_id)
        return parsed if parsed is not None else ('', [])

    # ─── Health Check ─────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Verify REST client can reach Chalk API.

        Returns:
            True if authenticated and Chalk responds.
        """
        data = self._get(f'{_API_BASE}/content/{TMO_TESTING_SCOPE_PAGE_ID}',
                         params={'expand': 'version'})
        if data and data.get('id'):
            self.log(f'[CHALK-REST] Health check OK — page: {data.get("title", "?")}')
            return True
        return False

    def auto_refresh_session(self, headless: bool = True, interactive_wait: int = 240) -> bool:
        """Refresh session cookies. ONE quick headless attempt (works only if the box can
        auto-negotiate Windows SSO with no prompt); if that doesn't yield a LIVE session we STOP
        retrying headless — it can't type credentials — and open a VISIBLE browser so the user
        logs in ONCE. Cookies are saved to storage_state.json the moment the Chalk API authenticates.

        Args:
            headless: try a silent headless pass first (default True).
            interactive_wait: seconds to wait for the visible login (default 240).

        Returns:
            True if a live session was established.
        """
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            self.log('[CHALK-REST] ❌ Playwright not installed — cannot refresh session')
            return False

        storage_path = self._mgr.storage_path
        if not storage_path:
            storage_path = Path(__file__).resolve().parent.parent.parent / 'storage_state.json'

        # 1) FAST PATH — one silent headless attempt (auto SSO negotiation, no prompt).
        if headless:
            if self._browser_refresh(storage_path, headless=True, wait_secs=0) and self.health_check():
                self.log('[CHALK-REST] ✅ Session refreshed (headless SSO)')
                return True
            self.log('[CHALK-REST] headless SSO could not establish a session — opening a VISIBLE '
                     'browser so you can log in once; cookies save automatically and the run continues.')

        # 2) INTERACTIVE — visible browser; poll the real Chalk API and save cookies on auth.
        return (self._browser_refresh(storage_path, headless=False, wait_secs=interactive_wait)
                and self.health_check())

    def _browser_refresh(self, storage_path, headless: bool = True, wait_secs: int = 0) -> bool:
        """Drive a browser to Chalk and save cookies to storage_path. When wait_secs > 0 the
        browser is VISIBLE and we poll the live Chalk API until it authenticates (user completes
        SSO/creds), saving the instant it works. Returns True if cookies were saved."""
        from ._legacy_guard import legacy_blocked
        if legacy_blocked('CHALK-REST', self.log):
            return False
        from playwright.sync_api import sync_playwright
        chalk_url = f'{CHALK_BASE_URL}/spaces/MDA/pages/{TMO_TESTING_SCOPE_PAGE_ID}'
        _args = ['--disable-blink-features=AutomationControlled', '--no-sandbox',
                 '--auth-server-allowlist=*.charter.com,*.spectrum.net']
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=headless, channel='msedge', args=_args)
                except Exception:
                    browser = pw.chromium.launch(headless=headless, args=_args)   # bundled chromium
                _ctx_kw = {'viewport': {'width': 1600, 'height': 1000}, 'ignore_https_errors': True}
                if Path(storage_path).exists():
                    _ctx_kw['storage_state'] = str(storage_path)   # reuse any still-valid cookies
                context = browser.new_context(**_ctx_kw)
                page = context.new_page()
                page.set_default_timeout(30000)
                try:
                    page.goto(chalk_url, wait_until='domcontentloaded', timeout=45000)
                    if not headless:
                        from shared.browser_focus import pop_to_front
                        pop_to_front(page)
                except Exception as exc:
                    self.log(f'[CHALK-REST] navigation warning: {exc}')

                import time as _t
                if wait_secs and wait_secs > 0:
                    self.log(f'[CHALK-REST] 🌐 Log in to Chalk in the browser window — waiting up '
                             f'to {wait_secs}s (saves automatically once authenticated)...')
                    deadline = _t.time() + wait_secs
                    ok = False
                    while _t.time() < deadline:
                        try:
                            context.storage_state(path=str(storage_path))
                            self._mgr.refresh_cookies(storage_path)
                            self._session = self._mgr.get_session(CHALK_BASE_URL)
                            if self.health_check():
                                ok = True
                                break
                        except Exception:
                            pass
                        _t.sleep(5)
                    browser.close()
                    if ok:
                        self.log(f'[CHALK-REST] ✅ Logged in — cookies saved to {storage_path.name}')
                    else:
                        self.log('[CHALK-REST] login window closed/timed out — session not saved.')
                    return ok
                else:
                    # Headless fast path: give SSO a moment, save once, let caller verify.
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                    except Exception:
                        _t.sleep(3)
                    Path(storage_path).parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(storage_path))
                    browser.close()
                    self._mgr.refresh_cookies(storage_path)
                    self._session = self._mgr.get_session(CHALK_BASE_URL)
                    return True
        except Exception as e:
            self.log(f'[CHALK-REST] browser refresh failed: {e}')
            return False

    def health_check_with_refresh(self) -> bool:
        """Health check with automatic session refresh on 401.

        Strategy:
          1. Try health_check() with existing cookies
          2. If that fails (401) → call auto_refresh_session() to get fresh cookies
          3. Retry health_check() once with the new cookies

        Returns:
            True if healthy (after refresh if needed).
        """
        if self.health_check():
            return True

        self.log('[CHALK-REST] Session expired — attempting auto-refresh...')
        if self.auto_refresh_session():
            # Retry with fresh cookies
            if self.health_check():
                self.log('[CHALK-REST] ✅ Session refreshed and validated')
                return True
            else:
                self.log('[CHALK-REST] ❌ Still failing after refresh — cookies may need manual login')
                return False
        else:
            self.log('[CHALK-REST] ❌ Auto-refresh failed — falling back to browser')
            return False
