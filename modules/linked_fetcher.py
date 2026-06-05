"""
linked_fetcher.py — V2.2: Deep-fetch linked Jira issues for cross-feature coverage.
REST-first: uses JiraRestClient directly — no browser/page object needed.
Browser path kept as fallback when REST is unavailable.
"""
import logging
import re
import time
from typing import List, Dict, Optional

from .config import JIRA_BASE_URL, JIRA_REST_V2, PAGE_LOAD_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS

logger = logging.getLogger(__name__)

# Shared lazy-loaded REST client
_jira_rest_client = None


def _get_jira_rest_client():
    """Lazy-load a shared JiraRestClient instance."""
    global _jira_rest_client
    if _jira_rest_client is None:
        try:
            import sys
            from pathlib import Path
            _shared = str(Path(__file__).resolve().parent.parent.parent / 'shared')
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            from rest_clients import JiraRestClient
            _client = JiraRestClient(logger_fn=lambda m: None)
            if _client.health_check():
                _jira_rest_client = _client
        except Exception:
            _jira_rest_client = False  # sentinel
    return _jira_rest_client if _jira_rest_client is not False else None


def _wait(page, timeout=NETWORK_IDLE_TIMEOUT_MS):
    try: page.wait_for_load_state('networkidle', timeout=timeout)
    except: pass


def _extract_content(fields: Dict, log=print, key: str = '') -> tuple:
    """Extract AC items and description items from a Jira fields dict."""
    summary = fields.get('summary', '')
    description = fields.get('description', '') or ''

    AC_KEYWORDS = ['acceptance criteria', 'shall be', 'must be', 'given', 'when', 'then', 'verify']

    # Extract AC from custom fields
    ac_text = ''
    for fkey, val in sorted(fields.items()):
        if not fkey.startswith('customfield_') or not val or not isinstance(val, str):
            continue
        if len(val) < 30:
            continue
        if any(kw in val.lower() for kw in AC_KEYWORDS):
            ac_text = val
            break

    # Parse AC into items
    ac_items = []
    if ac_text:
        for line in ac_text.split('\n'):
            line = re.sub(r'\{[^}]+\}', '', line).strip(' *-#')
            if line and len(line) > 10:
                if any(kw in line.lower() for kw in ['shall', 'must', 'should', 'verify', 'ensure',
                                                       'given', 'when', 'then']):
                    ac_items.append(line)

    # Parse description for testable statements
    desc_items = []
    if description:
        for line in description.split('\n'):
            line = re.sub(r'\{[^}]+\}', '', line).strip(' *-#')
            if line and len(line) > 15:
                line_low = line.lower()
                if any(kw in line_low for kw in ['shall ', 'must ', 'should ', 'verify ',
                                                  'the system ', 'api ', 'trigger ']):
                    desc_items.append(line)

    return summary, ac_items, desc_items


def fetch_linked_ac(page, linked_issues: List[Dict], log=print, max_fetch=5) -> List[Dict]:
    """Fetch AC and description from linked Jira issues.

    V2.2: REST-first — uses JiraRestClient.fetch_issue() directly.
    Falls back to page.evaluate(fetch(...)) only when REST is unavailable.

    Args:
        page: Playwright page (used only as fallback; may be None for REST path)
        linked_issues: List of {key, direction, ...} dicts
        log: Logger function
        max_fetch: Max number of linked issues to fetch

    Returns:
        List of {key, summary, ac_items, description_items, link_type}
    """
    results = []
    fetched = 0

    # Try to get REST client upfront
    rest_client = _get_jira_rest_client()

    for link in linked_issues:
        if fetched >= max_fetch:
            log('[LINKED] Capped at %d linked issues' % max_fetch)
            break

        key = link.get('key', '')
        if not key:
            continue

        log('[LINKED] Fetching %s...' % key)
        summary, ac_items, desc_items = '', [], []
        fetched_ok = False

        # ── Primary: JiraRestClient (pure REST, no browser) ──
        if rest_client is not None:
            try:
                issue = rest_client.fetch_issue(key, expand='renderedFields')
                if issue:
                    fields = issue if isinstance(issue, dict) else (issue.raw_json or {})
                    # fetch_issue may return a JiraIssue object
                    if hasattr(issue, 'raw_json'):
                        fields = issue.raw_json.get('fields', {}) if issue.raw_json else {}
                        summary = getattr(issue, 'summary', '') or fields.get('summary', '')
                        ac_text = getattr(issue, 'acceptance_criteria', '') or ''
                        description = getattr(issue, 'description', '') or ''
                        # Parse from structured fields
                        AC_KW = ['shall', 'must', 'should', 'verify', 'ensure', 'given', 'when', 'then']
                        ac_items = [
                            line.strip(' *-#')
                            for line in re.sub(r'\{[^}]+\}', '', ac_text).split('\n')
                            if len(line.strip()) > 10 and any(kw in line.lower() for kw in AC_KW)
                        ]
                        desc_items = [
                            line.strip(' *-#')
                            for line in re.sub(r'\{[^}]+\}', '', description).split('\n')
                            if len(line.strip()) > 15 and any(
                                kw in line.lower() for kw in ['shall ', 'must ', 'should ', 'verify ', 'the system ', 'api ', 'trigger ']
                            )
                        ]
                    else:
                        # Raw dict response
                        _f = fields.get('fields', fields)
                        summary, ac_items, desc_items = _extract_content(_f, log=log, key=key)

                    fetched_ok = True
                    log('[LINKED-REST]   %s: %s (%d AC, %d desc)' % (key, summary[:50], len(ac_items), len(desc_items)))
            except Exception as _re:
                log('[LINKED-REST]   %s: REST failed (%s) — trying browser' % (key, str(_re)[:60]))

        # ── Fallback: browser page.evaluate (only when REST unavailable or failed) ──
        if not fetched_ok and page is not None:
            try:
                result = page.evaluate('''async (url) => {
                    const r = await fetch(url + '?expand=renderedFields', {
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    return {status: r.status, body: r.ok ? await r.json() : null};
                }''', '%s/issue/%s' % (JIRA_REST_V2, key))

                if result['status'] == 200 and result['body']:
                    f = result['body'].get('fields', {})
                    summary, ac_items, desc_items = _extract_content(f, log=log, key=key)
                    fetched_ok = True
                    log('[LINKED-BROWSER]   %s: %s (%d AC, %d desc)' % (key, summary[:50], len(ac_items), len(desc_items)))
                else:
                    log('[LINKED]   %s: HTTP %d — skipped' % (key, result['status']))
            except Exception as e:
                log('[LINKED]   %s: FAILED — %s' % (key, str(e)[:60]))

        if fetched_ok:
            results.append({
                'key': key,
                'summary': summary,
                'link_type': link.get('direction', ''),
                'ac_items': ac_items,
                'description_items': desc_items,
            })
            fetched += 1

    log('[LINKED] Fetched %d linked issues with %d total items' % (
        len(results), sum(len(r['ac_items']) + len(r['description_items']) for r in results)))
    return results
