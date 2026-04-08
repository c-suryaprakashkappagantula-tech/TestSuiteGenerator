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
    return issue


def download_attachments(page, issue: JiraIssue, log=print) -> List[Path]:
    """Download all attachments using browser session. Returns list of local paths."""
    paths = []
    for att in issue.attachments:
        try:
            log(f'[JIRA] Downloading {att.filename} ({att.size//1024}KB)...')
            save = ATTACHMENTS / f'{issue.key}_{att.filename}'
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
