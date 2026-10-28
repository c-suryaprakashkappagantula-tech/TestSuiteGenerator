"""Hermetic test for FEAT-001 — Jira fetch captures EVERY "Issues in epic" child
for a non-Epic parent (issue_type 'Feature'), across any project, de-duped by key.

No network: JiraRestClient._get is monkeypatched to route by URL — the issue
endpoint returns a Feature-type parent with two subtasks and two links, and the
/search endpoint returns three epic children (two MWTGNBOP + one cross-project
NSLNM). Mirrors the hermetic monkeypatch style of test_qmetry_steps_source.py.
"""
import os
import sys

# Importing modules.jira_fetcher performs the sys.path insert that makes the
# shared rest_clients package importable from the worktree.
_TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TSG_ROOT not in sys.path:
    sys.path.insert(0, _TSG_ROOT)

# The single shared rest_clients package lives at the monorepo root, outside the
# worktree. jira_fetcher adds a path relative to its own location, which does not
# resolve inside the nested worktree, so point at the canonical shared dir here.
_SHARED_PATH = r'C:\Users\P3314665\Documents\mcp-jira-server\shared'
if _SHARED_PATH not in sys.path:
    sys.path.insert(0, _SHARED_PATH)

from rest_clients import JiraRestClient


PARENT_KEY = 'MWTGPROV-4575'


def _parent_payload():
    """A Feature-type parent with two subtasks and two linked issues."""
    return {
        'key': PARENT_KEY,
        'fields': {
            'summary': 'Parent feature',
            'description': 'desc',
            'status': {'name': 'In Progress'},
            'priority': {'name': 'High'},
            'issuetype': {'name': 'Feature'},  # NOT an Epic
            'assignee': {'displayName': 'A'},
            'reporter': {'displayName': 'R'},
            'labels': ['PI-60.2', 'NBOP'],
            'components': [],
            'fixVersions': [],
            'attachment': [],
            'issuelinks': [{
                'type': {'outward': 'relates to'},
                'outwardIssue': {
                    'key': 'MWTGNBOP-5791',  # also appears as an epic child → must de-dup
                    'fields': {'summary': 'Linked + epic child'},
                },
            }],
            'subtasks': [{
                'key': 'MWTGNBOP-9001',
                'fields': {'summary': 'A real subtask',
                           'status': {'name': 'Open'}},
            }],
            'comment': {'comments': []},
        },
    }


def _search_payload():
    """Three epic children; MWTGNBOP-5791 duplicates the linked issue above."""
    def child(key, summary):
        return {'key': key, 'fields': {
            'summary': summary, 'description': 'd',
            'status': {'name': 'Open'}, 'issuetype': {'name': 'Story'},
            'labels': [], 'attachment': []}}
    return {'issues': [
        child('MWTGNBOP-5791', 'Epic child 1 (also linked)'),
        child('MWTGNBOP-5792', 'Epic child 2'),
        child('NSLNM-784', 'Epic child 3 cross-project'),
    ]}


def _make_client(monkeypatch):
    """Build a JiraRestClient without touching disk/network, with _get stubbed."""
    client = JiraRestClient.__new__(JiraRestClient)
    client.log = lambda *a, **k: None
    client._request_delay = 0

    def fake_get(url, params=None, timeout=30):
        if '/search' in url:
            return _search_payload()
        if url.endswith('/issue/%s' % PARENT_KEY):
            return _parent_payload()
        # Deep-fetch of any individual child/subtask/link issue → minimal fields.
        return {'fields': {'description': 'd', 'labels': [],
                           'issuetype': {'name': 'Story'}, 'attachment': []}}

    monkeypatch.setattr(client, '_get', fake_get)
    return client


def test_feature_parent_captures_all_epic_children(monkeypatch):
    client = _make_client(monkeypatch)
    issue = client.fetch_issue(PARENT_KEY)

    assert issue is not None
    assert issue.issue_type == 'Feature'

    subtask_keys = [s.get('key', '') for s in issue.subtasks]
    link_keys = [l.get('key', '') for l in issue.linked_issues]
    all_keys = subtask_keys + link_keys

    # Epic children that are not already present land in subtasks, even though
    # the parent is a Feature (not an Epic).
    assert 'MWTGNBOP-5792' in subtask_keys, 'MWTGNBOP-5792 missing from subtasks'
    # Cross-project child is captured regardless of its project prefix.
    assert 'NSLNM-784' in subtask_keys, 'NSLNM-784 missing from subtasks'

    # None dropped: every epic-child key is captured somewhere (subtask or link).
    for key in ('MWTGNBOP-5791', 'MWTGNBOP-5792', 'NSLNM-784'):
        assert key in all_keys, '%s dropped entirely' % key

    # De-dup by key: MWTGNBOP-5791 is both a link and an epic child, so it must
    # NOT be re-added to subtasks — it appears exactly once overall.
    assert 'MWTGNBOP-5791' not in subtask_keys
    assert all_keys.count('MWTGNBOP-5791') == 1
    assert len(subtask_keys) == len(set(subtask_keys)), 'duplicate keys in subtasks'

    # The genuine subtask is preserved alongside the epic children.
    assert 'MWTGNBOP-9001' in subtask_keys
