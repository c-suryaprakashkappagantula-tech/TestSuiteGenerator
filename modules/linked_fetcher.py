"""
linked_fetcher.py — V2.1: Deep-fetch linked Jira issues for cross-feature coverage.
Navigates to each linked issue and extracts AC, description, and key scenarios.
"""
import time
import re
from typing import List, Dict
from .config import JIRA_BASE_URL, JIRA_REST_V2, PAGE_LOAD_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS


def _wait(page, timeout=NETWORK_IDLE_TIMEOUT_MS):
    try: page.wait_for_load_state('networkidle', timeout=timeout)
    except: pass


def fetch_linked_ac(page, linked_issues: List[Dict], log=print, max_fetch=5) -> List[Dict]:
    """Fetch AC and description from linked Jira issues.
    Returns list of {key, summary, ac_items, description_items, link_type}."""
    results = []
    fetched = 0

    for link in linked_issues:
        if fetched >= max_fetch:
            log('[LINKED] Capped at %d linked issues' % max_fetch)
            break

        key = link.get('key', '')
        if not key:
            continue

        log('[LINKED] Fetching %s...' % key)
        try:
            result = page.evaluate('''async (url) => {
                const r = await fetch(url + '?expand=renderedFields', {
                    credentials: 'include',
                    headers: {'Accept': 'application/json'}
                });
                return {status: r.status, body: r.ok ? await r.json() : null};
            }''', '%s/issue/%s' % (JIRA_REST_V2, key))

            if result['status'] != 200 or not result['body']:
                log('[LINKED]   %s: HTTP %d — skipped' % (key, result['status']))
                continue

            f = result['body'].get('fields', {})
            summary = f.get('summary', '')
            description = f.get('description', '') or ''

            # Extract AC from custom fields
            ac_text = ''
            AC_KEYWORDS = ['acceptance criteria', 'shall be', 'must be', 'given', 'when', 'then', 'verify']
            for fkey, val in sorted(f.items()):
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

            results.append({
                'key': key,
                'summary': summary,
                'link_type': link.get('direction', ''),
                'ac_items': ac_items,
                'description_items': desc_items,
            })
            fetched += 1
            total_items = len(ac_items) + len(desc_items)
            log('[LINKED]   %s: %s (%d AC, %d desc items)' % (key, summary[:50], len(ac_items), len(desc_items)))

        except Exception as e:
            log('[LINKED]   %s: FAILED — %s' % (key, str(e)[:60]))

    log('[LINKED] Fetched %d linked issues with %d total items' % (
        len(results), sum(len(r['ac_items']) + len(r['description_items']) for r in results)))
    return results
