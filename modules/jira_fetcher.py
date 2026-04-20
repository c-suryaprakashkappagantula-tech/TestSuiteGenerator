"""
jira_fetcher.py — Fetch ANY Jira issue via Playwright browser session + REST API.
Downloads attachments. Extracts acceptance criteria from custom fields.
"""
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from .config import JIRA_BASE_URL, JIRA_REST_V2, ATTACHMENTS, PAGE_LOAD_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS


@dataclass
class JiraAttachment:
    filename: str = ''
    size: int = 0
    mime_type: str = ''
    url: str = ''
    author: str = ''
    local_path: Optional[Path] = None


@dataclass
class JiraIssue:
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
    attachments: List[JiraAttachment] = field(default_factory=list)
    linked_issues: List[Dict] = field(default_factory=list)
    subtasks: List[Dict] = field(default_factory=list)
    comments: List[Dict] = field(default_factory=list)
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    raw_json: Dict = field(default_factory=dict)
    pi: str = ''
    channel: str = ''


def _wait(page, timeout=NETWORK_IDLE_TIMEOUT_MS):
    try: page.wait_for_load_state('networkidle', timeout=timeout)
    except: pass


def fetch_jira_issue(page, issue_key: str, log=print) -> JiraIssue:
    """Fetch any Jira issue. Returns populated JiraIssue dataclass."""
    issue = JiraIssue(key=issue_key)

    log(f'[JIRA] Navigating to {issue_key}...')
    page.goto(f'{JIRA_BASE_URL}/browse/{issue_key}',
              timeout=PAGE_LOAD_TIMEOUT_MS, wait_until='domcontentloaded')
    _wait(page); time.sleep(3)

    log(f'[JIRA] Fetching via REST API...')
    result = page.evaluate('''async (url) => {
        const r = await fetch(url + '?expand=renderedFields', {
            credentials: 'include',
            headers: {'Accept': 'application/json'}
        });
        return {status: r.status, body: r.ok ? await r.json() : null};
    }''', f'{JIRA_REST_V2}/issue/{issue_key}')

    if result['status'] != 200:
        log(f'[JIRA] ❌ HTTP {result["status"]}')
        return issue

    data = result['body']
    f = data.get('fields', {})
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
            issue.pi = lbl; break
    text = (' '.join(issue.labels) + ' ' + issue.summary).upper()
    issue.channel = 'NBOP' if 'NBOP' in text else 'ITMBO'

    # Acceptance Criteria — scan all custom text fields
    AC_KEYWORDS = ['acceptance criteria', 'acceptance', 'shall be', 'must be',
                   'given', 'when', 'then', 'verify that', 'requirement']
    for key, val in sorted(f.items()):
        if not key.startswith('customfield_'): continue
        if not val or not isinstance(val, str) or len(val) < 30: continue
        if any(kw in val.lower() for kw in AC_KEYWORDS):
            issue.acceptance_criteria = val
            log(f'[JIRA] ✅ Acceptance Criteria found in {key} ({len(val)} chars)')
            break

    # Store all meaningful custom fields
    for key, val in f.items():
        if not key.startswith('customfield_') or not val: continue
        if isinstance(val, str) and len(val) > 20:
            issue.custom_fields[key] = val
        elif isinstance(val, dict) and val.get('value'):
            issue.custom_fields[key] = val['value']

    # Attachments
    for a in f.get('attachment', []):
        issue.attachments.append(JiraAttachment(
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
                    'key': i['key'], 'summary': i['fields']['summary']})

    # Subtasks
    for s in f.get('subtasks', []):
        issue.subtasks.append({'key': s.get('key', ''),
            'summary': s.get('fields', {}).get('summary', ''),
            'status': s.get('fields', {}).get('status', {}).get('name', '')})

    # Comments (first 20)
    for c in f.get('comment', {}).get('comments', [])[:20]:
        issue.comments.append({
            'author': (c.get('author') or {}).get('displayName', ''),
            'created': c.get('created', '')[:10],
            'body': c.get('body', '')})

    log(f'[JIRA] ✅ {issue.key} | {issue.summary[:60]}')
    log(f'[JIRA]    Status={issue.status} | Priority={issue.priority} | PI={issue.pi}')
    log(f'[JIRA]    Attachments={len(issue.attachments)} | Links={len(issue.linked_issues)} | AC={bool(issue.acceptance_criteria)}')
    log(f'[JIRA]    Subtasks={len(issue.subtasks)} | Links={len(issue.linked_issues)}')

    # Deep-fetch subtask descriptions (they contain the real test detail)
    if issue.subtasks:
        log(f'[JIRA] 🔍 Deep-fetching {len(issue.subtasks)} subtask descriptions...')
        for si, st in enumerate(issue.subtasks, 1):
            try:
                log(f'[JIRA]   [{si}/{len(issue.subtasks)}] Fetching {st["key"]}...')
                st_result = page.evaluate('''async (url) => {
                    const r = await fetch(url, {
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    return {status: r.status, body: r.ok ? await r.json() : null};
                }''', f'{JIRA_REST_V2}/issue/{st["key"]}')
                if st_result['status'] != 200:
                    log(f'[JIRA]     ❌ HTTP {st_result["status"]} for {st["key"]}')
                    continue
                if st_result['body']:
                    sf = st_result['body'].get('fields', {})
                    st['description'] = sf.get('description', '') or ''
                    st['labels'] = sf.get('labels', [])
                    st['issue_type'] = (sf.get('issuetype') or {}).get('name', '')
                    # Also grab AC from subtask
                    for fkey, val in sorted(sf.items()):
                        if fkey.startswith('customfield_') and isinstance(val, str) and len(val) > 30:
                            if any(kw in val.lower() for kw in ['shall', 'must', 'verify', 'when', 'then']):
                                st['acceptance_criteria'] = val
                                break
                    # Deep-fetch: subtask attachments
                    st_attachments = sf.get('attachment', [])
                    if st_attachments:
                        st['attachments'] = [
                            {'filename': a.get('filename', ''), 'size': a.get('size', 0),
                             'mimeType': a.get('mimeType', ''), 'url': a.get('content', '')}
                            for a in st_attachments
                        ]
                    # Deep-fetch: subtask comments
                    st_comments = sf.get('comment', {}).get('comments', [])
                    if st_comments:
                        st['comments'] = [
                            {'author': (c.get('author') or {}).get('displayName', ''),
                             'created': c.get('created', '')[:10],
                             'body': c.get('body', '')}
                            for c in st_comments[:10]  # Cap at 10 per subtask
                        ]
                    desc_len = len(st.get('description', ''))
                    ac_len = len(st.get('acceptance_criteria', ''))
                    att_count = len(st.get('attachments', []))
                    cmt_count = len(st.get('comments', []))
                    log(f'[JIRA]     ✅ {st["key"]}: {st["summary"][:50]}')
                    log(f'[JIRA]        Type={st.get("issue_type","")} | Status={st.get("status","")} | Desc={desc_len} chars | AC={ac_len} chars | Att={att_count} | Cmt={cmt_count}')
                    if desc_len > 0:
                        # Show first 100 chars of description
                        log(f'[JIRA]        Desc preview: {st["description"][:100]}')
                else:
                    log(f'[JIRA]     ⚠️ {st["key"]}: empty response')
            except Exception as e:
                log(f'[JIRA]     ❌ {st["key"]}: {str(e)[:60]}')
                log(f'[JIRA]    {st["key"]}: failed to fetch ({e})')

    # Fetch epic children — "Issues in epic" are not subtasks in REST API
    if issue.issue_type and issue.issue_type.lower() == 'epic':
        log(f'[JIRA] 🔍 Fetching epic children for {issue.key}...')
        try:
            epic_search = page.evaluate('''async (url) => {
                const jql = encodeURIComponent('"Epic Link" = ''' + f'"{issue.key}"' + ''' ORDER BY key ASC');
                const r = await fetch(url + '/search?jql=' + jql + '&maxResults=20&fields=summary,description,status,issuetype,labels,customfield_12402', {
                    credentials: 'include',
                    headers: {'Accept': 'application/json'}
                });
                return {status: r.status, body: r.ok ? await r.json() : null};
            }''', JIRA_REST_V2)
            if epic_search['status'] == 200 and epic_search['body']:
                epic_issues = epic_search['body'].get('issues', [])
                log(f'[JIRA] Found {len(epic_issues)} issues in epic')
                for ei in epic_issues:
                    ef = ei.get('fields', {})
                    child = {
                        'key': ei.get('key', ''),
                        'summary': ef.get('summary', ''),
                        'description': ef.get('description', '') or '',
                        'status': (ef.get('status') or {}).get('name', ''),
                        'issue_type': (ef.get('issuetype') or {}).get('name', ''),
                        'labels': ef.get('labels', []),
                    }
                    # Check for AC in custom fields
                    for fkey in ['customfield_12402', 'customfield_10401']:
                        val = ef.get(fkey)
                        if val and isinstance(val, str) and len(val) > 30:
                            child['acceptance_criteria'] = val
                            break
                    issue.subtasks.append(child)
                    log(f'[JIRA]   Epic child: {child["key"]} | {child["summary"][:50]} | desc={len(child["description"])} chars')
            else:
                log(f'[JIRA] Epic search returned HTTP {epic_search["status"]}')
        except Exception as e:
            log(f'[JIRA] Epic children fetch failed: {str(e)[:80]}')

    return issue


# ================================================================
# JIRA VALIDATION LAYER (Finding #3 & #4)
# ================================================================

class JiraValidationError(Exception):
    """Raised when Jira data fails validation."""
    pass


def validate_jira_issue(issue: JiraIssue, log=print) -> List[str]:
    """Validate a JiraIssue has the required fields for test generation.
    Returns list of warnings. Raises JiraValidationError for critical failures."""
    warnings = []

    # Critical — cannot generate without these
    if not issue.key:
        raise JiraValidationError('Jira issue has no key/ID')
    if not issue.summary or len(issue.summary) < 5:
        raise JiraValidationError('Jira issue %s has no summary' % issue.key)

    # Important — generation will be degraded
    if not issue.description or len(issue.description) < 20:
        warnings.append('No description — TCs will rely on Chalk/attachments only')
        log('[JIRA-VALIDATE] ⚠️ %s: No meaningful description (%d chars)' % (
            issue.key, len(issue.description or '')))

    if not issue.acceptance_criteria:
        warnings.append('No acceptance criteria found — traceability will be limited')
        log('[JIRA-VALIDATE] ⚠️ %s: No acceptance criteria detected' % issue.key)

    if not issue.status:
        warnings.append('No status field — may indicate Jira schema change')
        log('[JIRA-VALIDATE] ⚠️ %s: Missing status field' % issue.key)

    if not issue.priority:
        warnings.append('No priority field — defaulting to Medium')
        log('[JIRA-VALIDATE] ⚠️ %s: Missing priority field' % issue.key)

    # Informational
    if not issue.pi:
        log('[JIRA-VALIDATE] ℹ️ %s: No PI label detected in Jira labels' % issue.key)

    if not issue.labels:
        log('[JIRA-VALIDATE] ℹ️ %s: No labels on issue' % issue.key)

    if issue.status and issue.status.lower() in ('closed', 'done', 'cancelled'):
        warnings.append('Issue is %s — generating tests for a closed issue' % issue.status)
        log('[JIRA-VALIDATE] ⚠️ %s: Issue status is "%s"' % (issue.key, issue.status))

    if not warnings:
        log('[JIRA-VALIDATE] ✅ %s: All fields valid' % issue.key)

    return warnings


def download_attachments(page, issue: JiraIssue, log=print) -> List[Path]:
    """Download all attachments using browser session. Returns list of local paths.
    Point 2: Uses page.request API for reliable downloads instead of window.location redirect."""
    paths = []
    for att in issue.attachments:
        try:
            log(f'[JIRA] Downloading {att.filename} ({att.size//1024}KB)...')
            save = ATTACHMENTS / f'{issue.key}_{att.filename}'
            # Try API-based download first (more reliable, no navigation needed)
            try:
                response = page.request.get(att.url)
                if response.ok:
                    save.write_bytes(response.body())
                    att.local_path = save
                    paths.append(save)
                    log(f'[JIRA] ✅ Saved: {save.name}')
                    continue
                else:
                    log(f'[JIRA] ⚠️ API download returned {response.status}, falling back to navigation...')
            except Exception:
                pass  # Fall through to navigation-based download

            # Fallback: navigation-based download
            with page.expect_download(timeout=60000) as dl:
                page.evaluate(f"window.location.href = '{att.url}'")
            dl.value.save_as(str(save))
            att.local_path = save
            paths.append(save)
            log(f'[JIRA] ✅ Saved: {save.name}')
            # Navigate back for next download
            page.goto(f'{JIRA_BASE_URL}/browse/{issue.key}',
                      timeout=PAGE_LOAD_TIMEOUT_MS, wait_until='domcontentloaded')
            _wait(page); time.sleep(2)
        except Exception as e:
            log(f'[JIRA] ⚠️ Failed: {att.filename} — {e}')
    return paths
