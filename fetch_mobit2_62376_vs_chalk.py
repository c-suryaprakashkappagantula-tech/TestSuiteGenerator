"""
fetch_mobit2_62376_vs_chalk.py — Fetch MOBIT2-62376 from Jira via Playwright,
then cross-reference with CABOT_CHALK_DB to show what Chalk coverage exists.

Usage:  python fetch_mobit2_62376_vs_chalk.py
"""
import sys, os, time, json, sqlite3, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from playwright.sync_api import sync_playwright
from modules.config import (get_browser_channel, JIRA_BASE_URL, JIRA_REST_V2,
                            PAGE_LOAD_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS, ROOT)

ISSUE_KEY = 'MOBIT2-62376'
DB_PATH = ROOT / 'CABOT_CHALK_DB.db'


def log(msg):
    print(msg, flush=True)


def _wait(page, timeout=NETWORK_IDLE_TIMEOUT_MS):
    try:
        page.wait_for_load_state('networkidle', timeout=timeout)
    except Exception:
        pass


# ================================================================
# JIRA FETCH
# ================================================================

def fetch_issue(page, issue_key):
    """Fetch a Jira issue via REST API using browser session."""
    log('[JIRA] Navigating to %s...' % issue_key)
    page.goto('%s/browse/%s' % (JIRA_BASE_URL, issue_key),
              timeout=PAGE_LOAD_TIMEOUT_MS, wait_until='domcontentloaded')
    _wait(page)
    time.sleep(3)

    log('[JIRA] Fetching via REST API...')
    result = page.evaluate('''async (url) => {
        const r = await fetch(url + '?expand=renderedFields', {
            credentials: 'include',
            headers: {'Accept': 'application/json'}
        });
        return {status: r.status, body: r.ok ? await r.json() : null};
    }''', '%s/issue/%s' % (JIRA_REST_V2, issue_key))

    if result['status'] != 200:
        log('[JIRA] ERROR: HTTP %d' % result['status'])
        return None
    return result['body']


def fetch_subtask_detail(page, subtask_key):
    """Fetch a subtask's full details."""
    result = page.evaluate('''async (url) => {
        const r = await fetch(url + '?expand=renderedFields', {
            credentials: 'include',
            headers: {'Accept': 'application/json'}
        });
        return {status: r.status, body: r.ok ? await r.json() : null};
    }''', '%s/issue/%s' % (JIRA_REST_V2, subtask_key))
    if result['status'] == 200 and result['body']:
        return result['body']
    return None


# ================================================================
# CHALK DB CROSS-REFERENCE
# ================================================================

def load_chalk_db():
    """Load all Cabot Chalk data from DB."""
    if not DB_PATH.exists():
        log('[CHALK] WARNING: %s not found!' % DB_PATH)
        return []
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    rows = c.execute('SELECT * FROM Cabot_Chalk ORDER BY depth, id').fetchall()
    c.close()
    return [dict(r) for r in rows]


def search_chalk_for_term(chalk_data, term):
    """Search all Chalk sections for a term. Returns matching sections."""
    matches = []
    term_lower = term.lower().strip()
    if not term_lower or len(term_lower) < 3:
        return matches
    for section in chalk_data:
        score = 0
        matched_in = []
        # Check section name
        if term_lower in (section.get('section_name') or '').lower():
            score += 10
            matched_in.append('section_name')
        # Check raw text
        if term_lower in (section.get('raw_text') or '').lower():
            score += 5
            matched_in.append('raw_text')
        # Check table data
        table_json = section.get('table_data_json') or ''
        if term_lower in table_json.lower():
            score += 7
            matched_in.append('table_data')
        # Check tab data
        tab_json = section.get('tab_data_json') or ''
        if term_lower in tab_json.lower():
            score += 6
            matched_in.append('tab_data')
        # Check links
        links_json = section.get('links_json') or ''
        if term_lower in links_json.lower():
            score += 3
            matched_in.append('links')
        if score > 0:
            matches.append({
                'section_name': section.get('section_name'),
                'section_url': section.get('section_url'),
                'parent_section': section.get('parent_section'),
                'depth': section.get('depth'),
                'score': score,
                'matched_in': matched_in
            })
    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches


# ================================================================
# MAIN
# ================================================================

def main():
    log('=' * 70)
    log('  MOBIT2-62376 JIRA FETCH + CABOT CHALK CROSS-REFERENCE')
    log('=' * 70)

    # ---- Step 1: Load Chalk DB ----
    log('\n[1/3] Loading Cabot Chalk DB...')
    chalk_data = load_chalk_db()
    log('  Loaded %d Chalk sections from %s' % (len(chalk_data), DB_PATH))
    for s in chalk_data:
        log('    [D%d] %s > %s' % (s.get('depth', 0), s.get('parent_section', ''), s.get('section_name', '')))

    # ---- Step 2: Fetch Jira issue ----
    log('\n[2/3] Fetching %s from Jira...' % ISSUE_KEY)
    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True, channel=get_browser_channel())
    ctx = br.new_context(viewport={'width': 1920, 'height': 1080})
    page = ctx.new_page()

    data = fetch_issue(page, ISSUE_KEY)
    if not data:
        log('FATAL: Could not fetch %s' % ISSUE_KEY)
        ctx.close(); br.close(); pw.stop()
        return

    f = data.get('fields', {})
    summary = f.get('summary', '')
    description = f.get('description', '') or ''
    status = (f.get('status') or {}).get('name', '')
    priority = (f.get('priority') or {}).get('name', '')
    issue_type = (f.get('issuetype') or {}).get('name', '')
    assignee = (f.get('assignee') or {}).get('displayName', 'Unassigned')
    reporter = (f.get('reporter') or {}).get('displayName', 'Unknown')
    labels = f.get('labels', [])
    components = [c.get('name', '') for c in f.get('components', [])]
    fix_versions = [v.get('name', '') for v in f.get('fixVersions', [])]
    subtasks = f.get('subtasks', [])
    linked_issues = f.get('issuelinks', [])
    attachments = f.get('attachment', [])
    comments = f.get('comment', {}).get('comments', [])

    log('\n' + '-' * 70)
    log('  JIRA ISSUE: %s' % ISSUE_KEY)
    log('-' * 70)
    log('  Summary:      %s' % summary)
    log('  Type:         %s' % issue_type)
    log('  Status:       %s' % status)
    log('  Priority:     %s' % priority)
    log('  Assignee:     %s' % assignee)
    log('  Reporter:     %s' % reporter)
    log('  Labels:       %s' % ', '.join(labels) if labels else '(none)')
    log('  Components:   %s' % ', '.join(components) if components else '(none)')
    log('  Fix Versions: %s' % ', '.join(fix_versions) if fix_versions else '(none)')
    log('  Attachments:  %d' % len(attachments))
    log('  Comments:     %d' % len(comments))
    log('  Linked Issues: %d' % len(linked_issues))
    log('  Subtasks:     %d' % len(subtasks))

    if description:
        log('\n  Description (first 500 chars):')
        log('  ' + description[:500].replace('\n', '\n  '))

    # Print linked issues
    if linked_issues:
        log('\n  Linked Issues:')
        for lnk in linked_issues:
            for direction in ['outwardIssue', 'inwardIssue']:
                if lnk.get(direction):
                    i = lnk[direction]
                    rel = lnk.get('type', {}).get(direction.replace('Issue', ''), '')
                    log('    %s %s — %s' % (rel, i['key'], i['fields']['summary'][:60]))

    # Print subtasks
    if subtasks:
        log('\n  Subtasks:')
        for st in subtasks:
            sf = st.get('fields', {})
            st_key = st.get('key', '')
            st_summary = sf.get('summary', '')
            st_status = (sf.get('status') or {}).get('name', '')
            log('    %s | %s | %s' % (st_key, st_status, st_summary[:70]))

    # Deep-fetch subtask details
    subtask_details = []
    if subtasks:
        log('\n  Deep-fetching %d subtask details...' % len(subtasks))
        for si, st in enumerate(subtasks, 1):
            st_key = st.get('key', '')
            log('    [%d/%d] Fetching %s...' % (si, len(subtasks), st_key))
            st_data = fetch_subtask_detail(page, st_key)
            if st_data:
                sf = st_data.get('fields', {})
                detail = {
                    'key': st_key,
                    'summary': sf.get('summary', ''),
                    'description': sf.get('description', '') or '',
                    'status': (sf.get('status') or {}).get('name', ''),
                    'type': (sf.get('issuetype') or {}).get('name', ''),
                    'labels': sf.get('labels', []),
                    'attachments': len(sf.get('attachment', [])),
                    'comments': len(sf.get('comment', {}).get('comments', []))
                }
                subtask_details.append(detail)
                log('      %s | %s | desc=%d chars | att=%d | cmt=%d' % (
                    detail['key'], detail['status'],
                    len(detail['description']), detail['attachments'], detail['comments']))
            else:
                log('      %s: fetch failed' % st_key)

    # Also check if this is an Epic — fetch epic children
    epic_children = []
    if issue_type and issue_type.lower() == 'epic':
        log('\n  Fetching Epic children...')
        epic_result = page.evaluate('''async (args) => {
            const jql = encodeURIComponent('"Epic Link" = "' + args.key + '" ORDER BY key ASC');
            const r = await fetch(args.url + '/search?jql=' + jql + '&maxResults=50&fields=summary,description,status,issuetype,labels', {
                credentials: 'include',
                headers: {'Accept': 'application/json'}
            });
            return {status: r.status, body: r.ok ? await r.json() : null};
        }''', {'key': ISSUE_KEY, 'url': JIRA_REST_V2})
        if epic_result['status'] == 200 and epic_result['body']:
            for ei in epic_result['body'].get('issues', []):
                ef = ei.get('fields', {})
                epic_children.append({
                    'key': ei.get('key', ''),
                    'summary': ef.get('summary', ''),
                    'status': (ef.get('status') or {}).get('name', ''),
                    'type': (ef.get('issuetype') or {}).get('name', ''),
                    'description': ef.get('description', '') or ''
                })
            log('  Found %d epic children' % len(epic_children))
            for ec in epic_children:
                log('    %s | %s | %s | %s' % (ec['key'], ec['type'], ec['status'], ec['summary'][:60]))

    # Cleanup browser
    ctx.close()
    br.close()
    pw.stop()

    # ---- Step 3: Cross-reference with Chalk DB ----
    log('\n' + '=' * 70)
    log('  CROSS-REFERENCE: JIRA vs CABOT CHALK DB')
    log('=' * 70)

    if not chalk_data:
        log('  No Chalk data to cross-reference!')
        return

    # Build search terms from Jira data
    search_terms = set()
    # From summary
    search_terms.add(summary)
    # Key terms from summary
    for word in re.split(r'[\s\-,;:]+', summary):
        if len(word) > 3:
            search_terms.add(word)
    # From subtask summaries
    for st in subtasks:
        sf = st.get('fields', {})
        st_sum = sf.get('summary', '')
        if st_sum:
            search_terms.add(st_sum)
    for sd in subtask_details:
        if sd.get('summary'):
            search_terms.add(sd['summary'])
    # From epic children
    for ec in epic_children:
        if ec.get('summary'):
            search_terms.add(ec['summary'])
    # Common API-related terms
    api_terms = ['cabot', 'transfer line', 'create line', 'line details',
                 'line summary', 'NSL', 'NMP', 'data pack', 'roaming',
                 'activate', 'subscriber', 'LLD', 'HLD', 'NBO',
                 'downstream', 'bolt-on', 'DBO']
    for t in api_terms:
        search_terms.add(t)

    # Also extract terms from description
    if description:
        # Look for API names, service names, etc.
        for pattern in [r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', r'[A-Z]{2,}[\-_][A-Z]{2,}']:
            for m in re.finditer(pattern, description):
                if len(m.group()) > 3:
                    search_terms.add(m.group())

    log('\n  Search terms extracted from Jira (%d terms):' % len(search_terms))
    for t in sorted(search_terms):
        if len(t) > 3:
            log('    - %s' % t[:80])

    log('\n  Searching Chalk DB for matches...\n')

    all_matches = {}
    for term in search_terms:
        if len(term) < 4:
            continue
        matches = search_chalk_for_term(chalk_data, term)
        for m in matches:
            key = m['section_name']
            if key not in all_matches:
                all_matches[key] = {
                    'section_name': m['section_name'],
                    'section_url': m['section_url'],
                    'parent_section': m['parent_section'],
                    'depth': m['depth'],
                    'total_score': 0,
                    'matched_terms': [],
                    'matched_in': set()
                }
            all_matches[key]['total_score'] += m['score']
            all_matches[key]['matched_terms'].append(term[:50])
            all_matches[key]['matched_in'].update(m['matched_in'])

    if all_matches:
        sorted_matches = sorted(all_matches.values(), key=lambda x: x['total_score'], reverse=True)
        log('  CHALK SECTIONS WITH JIRA MATCHES:')
        log('  %s' % ('-' * 65))
        for i, m in enumerate(sorted_matches, 1):
            log('  %d. [Score: %d] %s' % (i, m['total_score'], m['section_name']))
            log('     URL: %s' % (m['section_url'] or 'N/A'))
            log('     Parent: %s | Depth: %d' % (m['parent_section'], m['depth']))
            log('     Matched in: %s' % ', '.join(m['matched_in']))
            terms_preview = ', '.join(m['matched_terms'][:10])
            if len(m['matched_terms']) > 10:
                terms_preview += ' ... +%d more' % (len(m['matched_terms']) - 10)
            log('     Terms: %s' % terms_preview)
            log('')
    else:
        log('  NO MATCHES FOUND between Jira and Chalk DB.')

    # Summary table
    log('\n' + '=' * 70)
    log('  SUMMARY')
    log('=' * 70)
    log('  Jira Issue:     %s — %s' % (ISSUE_KEY, summary[:60]))
    log('  Jira Type:      %s' % issue_type)
    log('  Jira Status:    %s' % status)
    log('  Subtasks:       %d' % len(subtasks))
    log('  Epic Children:  %d' % len(epic_children))
    log('  Chalk Sections: %d in DB' % len(chalk_data))
    log('  Chalk Matches:  %d sections matched' % len(all_matches))
    log('=' * 70)


if __name__ == '__main__':
    main()
