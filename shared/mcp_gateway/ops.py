# -*- coding: utf-8 -*-
"""
ops.py -- Jira / Chalk operations over MCP, returning the SAME JSON shapes the old
REST code expected (so TSG / TSE / MDA / Tracker parsers keep working unchanged).

Tool names are resolved at runtime from the server's tools/list, so we do not hard-code
the gateway's naming. Override any mapping in data_source_config.json -> "tool_map", e.g.
    "tool_map": {"jira.get_issue": "get_issue", "chalk.get_page": "confluence_get_page"}

Inspect what the gateway exposes:  python -m shared.mcp_gateway tools jira
"""
from __future__ import annotations

import html as _html
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from .toggle import load_config
from .transport import McpHttpClient, McpToolError

CHALK_BASE_URL = 'https://chalk.charter.com'

# --- operation -> tool discovery rules ------------------------------------------------
OPS: Dict[str, Dict[str, Any]] = {
    'jira.get_issue': {
        'names': ['get_issue', 'jira_get_issue', 'get_issue_details', 'getissue', 'read_issue'],
        'must': ['issue'], 'any': ['get', 'detail', 'read', 'fetch', 'view'],
        'not': ['search', 'create', 'update', 'transition', 'comment', 'link', 'worklog',
                'watch', 'delete', 'type', 'field', 'sprint'],
    },
    'jira.search': {
        'names': ['search_issues', 'jira_search', 'search', 'jql_search', 'searchissues'],
        'must': ['search'], 'not': ['user', 'page', 'confluence', 'filter', 'field', 'project'],
    },
    'chalk.get_page': {
        'names': ['get_page', 'confluence_get_page', 'get_page_by_id', 'get_content',
                  'getpage', 'read_page', 'chalk_get_page'],
        'must': ['page|content'], 'any': ['get', 'read', 'fetch', 'view'],
        'not': ['child', 'search', 'create', 'update', 'comment', 'attach', 'label',
                'history', 'version', 'delete', 'space', 'title', 'ancestor'],
    },
    'chalk.get_children': {
        'names': ['get_page_children', 'get_child_pages', 'confluence_get_page_children',
                  'get_children', 'list_child_pages', 'chalk_get_page_children'],
        'must': ['child'], 'not': ['create', 'delete', 'comment', 'attach'],
    },
    'chalk.search': {
        'names': ['search', 'confluence_search', 'search_content', 'cql_search',
                  'search_pages', 'chalk_search'],
        'must': ['search'], 'not': ['user', 'jira', 'issue', 'label'],
    },
    # Spectrum gateway: one tool serves children / attachments / comments / labels via
    # a "resource" argument (get_page_details). Note: its get_labels is a GLOBAL prefix
    # search, not per-page labels, so it is deliberately not used.
    'jira.get_issue_details': {
        'names': ['get_issue_details'],
        'must': ['issue', 'detail'], 'not': ['create', 'update', 'delete', 'add'],
    },
    'chalk.get_page_details': {
        'names': ['get_page_details', 'get_content_details'],
        'must': ['detail'], 'not': ['create', 'update', 'delete', 'add'],
    },
}

# The gateway auto-appends "updated/lastModified within 30 days" when a query has no such
# clause. Dashboards need full history (e.g. every defect for an iteration), so add a wide
# explicit clause instead of silently losing older items.
_WIDE_SINCE = '2000-01-01'


def _ensure_since(query: str, field: str) -> str:
    q = (query or '').strip()
    if not q or re.search(r'\b%s\b' % field, q, re.I):
        return q
    m = re.search(r'\border\s+by\b', q, re.I)
    head, tail = (q[:m.start()].rstrip(), ' ' + q[m.start():]) if m else (q, '')
    clause = f'{field} >= "{_WIDE_SINCE}"'
    return (f'({head}) AND {clause}' if head else clause) + tail

# semantic argument -> candidate property names in a tool's inputSchema
ARG_ALIASES: Dict[str, List[str]] = {
    'issue_key': ['issue_key', 'issueKey', 'issue_id_or_key', 'issueIdOrKey', 'key',
                  'issue', 'issue_id', 'issueId', 'id'],
    'jql': ['jql', 'query', 'jql_query', 'jqlQuery'],
    'fields': ['fields', 'field_list', 'fieldList'],
    'expand': ['expand'],
    'max_results': ['max_results', 'maxResults', 'limit', 'max'],
    'start_at': ['start_at', 'startAt', 'offset', 'start'],
    'page_id': ['page_id', 'pageId', 'content_id', 'contentId', 'id'],
    'cql': ['cql', 'query', 'cql_query'],
    'limit': ['limit', 'max_results', 'maxResults', 'max'],
    'detail_level': ['detail_level', 'detailLevel'],
    'resource': ['resource'],
    'start': ['start', 'start_at', 'startAt', 'offset'],
}
_FORMAT_PROPS = ('representation', 'body_format', 'bodyFormat', 'format', 'content_format')


def _norm(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '_', name.lower())


def _match(name: str, rule: Dict[str, Any]) -> bool:
    n = _norm(name)
    for tok in rule.get('must', []):
        if not any(alt in n for alt in tok.split('|')):
            return False
    if any(t in n for t in rule.get('not', [])):
        return False
    anys = rule.get('any')
    return not anys or any(t in n for t in anys)


class _Resolver:
    def __init__(self, client: McpHttpClient, cfg: Dict[str, Any]):
        self.client = client
        self.cfg = cfg
        self._cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def tool(self, op: str, required: bool = True) -> Optional[Dict[str, Any]]:
        if op in self._cache:
            t = self._cache[op]
        else:
            tools = self.client.list_tools()
            by_name = {t['name']: t for t in tools}
            t = None
            override = (self.cfg.get('tool_map') or {}).get(op)
            if override:
                t = by_name.get(override)
                if t is None:
                    raise McpToolError(f'tool_map["{op}"]="{override}" is not exposed by '
                                       f'{self.client.srv["key"]}. Available: {", ".join(by_name)}')
            if t is None:
                norm_map = {_norm(n): n for n in by_name}
                for cand in OPS[op]['names']:
                    for nn, real in norm_map.items():
                        # exact, or exact after stripping a server prefix (jira_ / confluence_)
                        if nn == cand or nn.split('_', 1)[-1] == cand or nn.endswith('_' + cand):
                            t = by_name[real]
                            break
                    if t:
                        break
            if t is None:
                hits = sorted((n for n in by_name if _match(n, OPS[op])), key=len)
                t = by_name[hits[0]] if hits else None
            self._cache[op] = t
        if t is None and required:
            names = ', '.join(x['name'] for x in self.client.list_tools())
            raise McpToolError(f'No MCP tool found for "{op}" on {self.client.srv["key"]}. '
                               f'Available: {names}. Map it in data_source_config.json -> tool_map.')
        return t


def build_args(tool: Dict[str, Any], wanted: Dict[str, Any]) -> Dict[str, Any]:
    """Map semantic args onto the tool's real property names and types."""
    schema = tool.get('inputSchema') or {}
    props: Dict[str, Any] = schema.get('properties') or {}
    out: Dict[str, Any] = {}
    used = set()
    for sem, val in wanted.items():
        if val is None or val == '':
            continue
        target = None
        if props:
            for cand in ARG_ALIASES.get(sem, [sem]):
                if cand in props and cand not in used:
                    target = cand
                    break
            if target is None:
                continue          # tool does not take this argument
        else:
            target = ARG_ALIASES.get(sem, [sem])[0]
        used.add(target)
        ptype = (props.get(target) or {}).get('type')
        if ptype == 'array' and isinstance(val, str):
            val = [v.strip() for v in val.split(',') if v.strip()]
        elif ptype == 'string' and isinstance(val, (list, tuple)):
            val = ','.join(str(v) for v in val)
        elif ptype == 'integer':
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        out[target] = val
    # Spectrum gateway tools default to a trimmed "standard" view; bodies/descriptions only
    # come back with detail_level=full, which is what the legacy parsers need.
    for dp in ('detail_level', 'detailLevel'):
        if dp in props and dp not in out:
            enum = [str(e) for e in (props[dp].get('enum') or [])]
            if not enum or 'full' in enum:
                out[dp] = 'full'
    # Ask for storage-format HTML when the tool lets us choose.
    for fp in _FORMAT_PROPS:
        if fp in props and fp not in out:
            enum = [str(e).lower() for e in (props[fp].get('enum') or [])]
            for pref in ('storage', 'view', 'html', 'export_view'):
                if not enum or pref in enum:
                    out[fp] = pref
                    break
    return out


# --- normalisers ------------------------------------------------------------------------
def _unwrap(d: Any, *keys: str) -> Any:
    for k in keys:
        if isinstance(d, dict) and k in d and len(d) <= 3:
            d = d[k]
    return d


def _named(v: Any, attr: str = 'name') -> Any:
    """Gateway returns flat strings ("Accepted"); REST parsers expect {"name": "Accepted"}."""
    if isinstance(v, str):
        return {attr: v}
    return v


# gateway flat key -> (REST field name, attribute used when wrapping a string)
_FLAT_MAP = {
    'status': ('status', 'name'),
    'priority': ('priority', 'name'),
    'issue_type': ('issuetype', 'name'),
    'issuetype': ('issuetype', 'name'),
    'resolution': ('resolution', 'name'),
    'assignee': ('assignee', 'displayName'),
    'reporter': ('reporter', 'displayName'),
    'creator': ('creator', 'displayName'),
}


def normalize_issue(d: Any) -> Optional[Dict[str, Any]]:
    d = _unwrap(d, 'issue', 'result', 'data')
    if not isinstance(d, dict):
        return None
    if isinstance(d.get('fields'), dict):
        return d
    if 'key' not in d:
        return None
    fields: Dict[str, Any] = {}
    for k, v in d.items():
        if k in ('key', 'id', 'self', 'detail_level'):
            continue
        if k in _FLAT_MAP:
            name, attr = _FLAT_MAP[k]
            fields[name] = _named(v, attr)
        elif k in ('components', 'fix_versions', 'fixVersions') and isinstance(v, list):
            fields['components' if k == 'components' else 'fixVersions'] = \
                [_named(x) for x in v]
        else:
            fields[k] = v
    return {'key': d['key'], 'id': d.get('id'), 'fields': fields}


def normalize_comments(d: Any) -> List[Dict[str, Any]]:
    d = _unwrap(d, 'result', 'data')
    items = d.get('comments') if isinstance(d, dict) else d
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        a = c.get('author')
        out.append({'author': _named(a, 'displayName') if a else {'displayName': ''},
                    'created': c.get('created', '') or '',
                    'body': c.get('body', '') or c.get('text', '') or ''})
    return out


def normalize_attachments(d: Any) -> List[Dict[str, Any]]:
    d = _unwrap(d, 'result', 'data')
    items = d.get('attachments') if isinstance(d, dict) else d
    out = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        out.append({'filename': a.get('filename') or a.get('name', ''),
                    'size': a.get('size', 0) or 0,
                    'mimeType': a.get('mimeType') or a.get('mime_type', ''),
                    'content': a.get('content') or a.get('url', ''),
                    'author': _named(a.get('author'), 'displayName') or {}})
    return out


# Fields the legacy parsers (TSG AC extraction, linked/subtask deep-fetch) rely on.
# Custom-field IDs are Charter Jira PROD (from jira list_fields):
#   12402 Acceptance Criteria, 37210 Business Requirements, 14102 Expected Result,
#   27901 Parent Link, 10300 Epic Link.
ISSUE_FIELDS = [
    'summary', 'description', 'status', 'issuetype', 'priority', 'assignee', 'reporter',
    'labels', 'components', 'fixVersions', 'issuelinks', 'subtasks', 'created', 'updated',
    'customfield_12402', 'customfield_37210', 'customfield_14102',
    'customfield_27901', 'customfield_10300',
]

_SCOPE_RE = re.compile(r'\b(project|assignee|status)\b', re.I)


def _ensure_scope(jql: str) -> str:
    """Gateway rejects JQL without a project/assignee/status clause. Add a no-op one."""
    q = (jql or '').strip()
    if _SCOPE_RE.search(q):
        return q
    m = re.search(r'\border\s+by\b', q, re.I)
    head, tail = (q[:m.start()].rstrip(), ' ' + q[m.start():]) if m else (q, '')
    return (f'({head}) AND status is not EMPTY' if head else 'status is not EMPTY') + tail


def normalize_search(d: Any, start_at: int = 0) -> Dict[str, Any]:
    d = _unwrap(d, 'result', 'data')
    meta: Dict[str, Any] = {}
    if isinstance(d, list):
        issues = d
        total = len(d)
    elif isinstance(d, dict):
        meta = d.get('_meta') if isinstance(d.get('_meta'), dict) else {}
        issues = d.get('issues') or d.get('results') or d.get('items') or []
        total = meta.get('total', d.get('total', d.get('totalCount', len(issues))))
    else:
        issues, total = [], 0
    norm = [x for x in (normalize_issue(i) for i in issues) if x]
    return {'startAt': start_at, 'maxResults': len(norm), 'total': int(total or 0),
            'issues': norm, 'has_more': bool(meta.get('has_more', False)),
            'next_start_at': meta.get('next_start_at')}


_MD_TABLE_SEP = re.compile(r'^\|?\s*:?-{2,}')


def text_to_html(text: str) -> str:
    """Markdown/plain text -> simple HTML so BeautifulSoup table parsers still work."""
    if re.search(r'<(table|p|div|h\d|ul|ac:)\b', text or '', re.I):
        return text
    out: List[str] = []
    rows: List[List[str]] = []

    def flush():
        if rows:
            out.append('<table>' + ''.join(
                '<tr>' + ''.join(f'<td>{_html.escape(c)}</td>' for c in r) + '</tr>'
                for r in rows) + '</table>')
            rows.clear()

    for line in (text or '').splitlines():
        s = line.strip()
        if s.startswith('|'):
            if _MD_TABLE_SEP.match(s.replace(' ', '')):
                continue
            rows.append([c.strip() for c in s.strip('|').split('|')])
            continue
        flush()
        if not s:
            continue
        m = re.match(r'^(#{1,6})\s+(.*)', s)
        if m:
            lvl = len(m.group(1))
            out.append(f'<h{lvl}>{_html.escape(m.group(2))}</h{lvl}>')
        else:
            out.append(f'<p>{_html.escape(s)}</p>')
    flush()
    return '\n'.join(out)


def normalize_page(d: Any, page_id: str = '') -> Optional[Dict[str, Any]]:
    d = _unwrap(d, 'page', 'content', 'result', 'data')
    if isinstance(d, dict) and set(d) == {'text'}:
        d = {'id': page_id, 'title': '', 'body': d['text']}
    if not isinstance(d, dict):
        return None
    page = dict(d)
    page['id'] = str(page.get('id') or page.get('page_id') or page.get('pageId') or page_id)
    page.setdefault('title', page.get('name', ''))
    body = page.get('body')
    html_val = None
    if isinstance(body, dict):
        for k in ('storage', 'view', 'export_view', 'styled_view'):
            v = body.get(k)
            if isinstance(v, dict) and v.get('value'):
                html_val = v['value']
                break
            if isinstance(v, str) and v:
                html_val = v
                break
    elif isinstance(body, str):
        html_val = body
    if html_val is None:
        for k in ('body_storage', 'storage', 'html', 'body_html', 'content', 'markdown',
                  'text', 'value'):
            if isinstance(page.get(k), str) and page[k]:
                html_val = page[k]
                break
    html_val = text_to_html(html_val or '')
    page['body'] = {'storage': {'value': html_val, 'representation': 'storage'},
                    'view': {'value': html_val, 'representation': 'view'}}
    links = page.get('_links') if isinstance(page.get('_links'), dict) else {}
    if not links.get('webui'):
        url = page.get('url') or page.get('webui') or ''
        if url.startswith(CHALK_BASE_URL):
            url = url[len(CHALK_BASE_URL):]
        links = dict(links, webui=url or f'/pages/viewpage.action?pageId={page["id"]}')
    page['_links'] = links
    if isinstance(page.get('space'), str):
        page['space'] = {'key': page['space']}
    return page


def normalize_list(d: Any) -> Dict[str, Any]:
    d = _unwrap(d, 'result', 'data')
    if isinstance(d, dict):
        items = (d.get('results') or d.get('children') or d.get('pages') or d.get('items')
                 or d.get('attachments') or d.get('comments') or d.get('labels') or [])
    elif isinstance(d, list):
        items = d
    else:
        items = []
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get('content'), dict) and 'id' in it['content']:
            it = it['content']            # CQL search wraps pages in {"content": {...}}
        if isinstance(it, dict):
            p = dict(it)
            # CQL search wraps matches in @@@hl@@@ ... @@@endhl@@@ highlight markers.
            for tk in ('title', 'name'):
                if isinstance(p.get(tk), str):
                    p[tk] = p[tk].replace('@@@hl@@@', '').replace('@@@endhl@@@', '')
            if 'id' in p or 'page_id' in p or 'pageId' in p:
                p['id'] = str(p.get('id') or p.get('page_id') or p.get('pageId'))
            if not isinstance(p.get('_links'), dict) or not p['_links'].get('webui'):
                url = p.get('url') or ''
                if url.startswith(CHALK_BASE_URL):
                    url = url[len(CHALK_BASE_URL):]
                p['_links'] = {'webui': url or (f'/pages/viewpage.action?pageId={p["id"]}'
                                                if p.get('id') else '')}
            out.append(p)
        elif isinstance(it, str):
            out.append({'name': it, 'title': it})
    return {'results': out, 'size': len(out)}


# --- facades ------------------------------------------------------------------------------
_SINGLETONS: Dict[str, Any] = {}
_SLOCK = threading.Lock()


class JiraMcp:
    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.cfg = load_config()
        self.client = McpHttpClient(self.cfg['jira_server'], log=log, cfg=self.cfg)
        self.r = _Resolver(self.client, self.cfg)

    def ping(self) -> bool:
        self.r.tool('jira.get_issue')
        self.r.tool('jira.search')
        return True

    def get_issue(self, key: str, fields: Optional[str] = None,
                  expand: Optional[str] = None, with_details: bool = True) -> Optional[Dict[str, Any]]:
        t = self.r.tool('jira.get_issue')
        # The gateway's default/full view omits issuelinks, subtasks and ALL custom fields;
        # "*all" does not bring them back either. Ask for them by name.
        if not fields or fields in ('*all', '*navigable'):
            fields = ','.join(ISSUE_FIELDS + list(self.cfg.get('extra_issue_fields') or []))
        raw = self.client.call_tool(t['name'], build_args(
            t, {'issue_key': key, 'fields': fields, 'expand': expand}))
        issue = normalize_issue(raw)
        if issue and with_details:
            f = issue['fields']
            # Comments / attachment metadata live behind get_issue_details; only spend a
            # call when the issue actually has some.
            # Counts are absent when fields are named explicitly -> unknown means "fetch".
            if (f.get('comment_count', 1) or 0) > 0 and 'comment' not in f:
                f['comment'] = {'comments': self.get_details(key, 'comments')}
            if (f.get('attachment_count', 1) or 0) > 0 and 'attachment' not in f:
                f['attachment'] = self.get_details(key, 'attachments')
        return issue

    def get_details(self, key: str, resource: str) -> List[Dict[str, Any]]:
        t = self.r.tool('jira.get_issue_details', required=False)
        if t is None:
            return []
        raw = self.client.call_tool(t['name'], build_args(
            t, {'issue_key': key, 'resource': resource, 'max_results': 50}))
        return normalize_comments(raw) if resource == 'comments' else normalize_attachments(raw)

    def search(self, jql: str, fields: Optional[str] = None, max_results: int = 100,
               start_at: int = 0) -> Dict[str, Any]:
        t = self.r.tool('jira.search')
        # Gateway bug: search_issues crashes when fields == "key" alone.
        if fields is not None:
            flist = [f.strip() for f in (fields.split(',') if isinstance(fields, str)
                                         else fields) if f.strip()]
            if not [f for f in flist if f != 'key']:
                flist.append('summary')
            fields = ','.join(flist)
        raw = self.client.call_tool(t['name'], build_args(
            t, {'jql': _ensure_since(_ensure_scope(jql), 'updated'), 'fields': fields,
                'max_results': max_results, 'start_at': start_at}))
        return normalize_search(raw, start_at)

    def search_all(self, jql: str, fields: Optional[str] = None, page_size: int = 100,
                   cap: int = 1000, log: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        """Paginate a JQL search. MCP caps a search at 1,000 issues."""
        log = log or (lambda m: None)
        out: List[Dict[str, Any]] = []
        seen = set()
        start = 0
        while len(out) < cap:
            page = self.search(jql, fields=fields, max_results=page_size, start_at=start)
            new = [i for i in page['issues'] if i.get('key') not in seen]
            # Stop when a page adds nothing new (also guards tools that ignore start_at).
            if not new:
                break
            seen.update(i.get('key') for i in new)
            out.extend(new)
            log(f'   [MCP] Fetched {len(out)}/{page["total"]} issues...')
            # Gateway paging: trust _meta.has_more; its next_start_at can be 0 (bogus),
            # so only accept it when it actually moves forward.
            if not page.get('has_more') and len(out) >= page['total']:
                break
            nxt = page.get('next_start_at')
            start = nxt if isinstance(nxt, int) and nxt > start else start + len(page['issues'])
        return out[:cap]


class ChalkMcp:
    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.cfg = load_config()
        self.client = McpHttpClient(self.cfg['chalk_server'], log=log, cfg=self.cfg)
        self.r = _Resolver(self.client, self.cfg)

    def ping(self) -> bool:
        self.r.tool('chalk.get_page')
        return True

    def get_page(self, page_id: str, expand: Optional[str] = None) -> Optional[Dict[str, Any]]:
        t = self.r.tool('chalk.get_page')
        raw = self.client.call_tool(t['name'], build_args(
            t, {'page_id': str(page_id), 'expand': expand}))
        return normalize_page(raw, str(page_id))

    def _list(self, op: str, wanted: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        t = self.r.tool(op, required=False)
        if t is None:
            return None
        return normalize_list(self.client.call_tool(t['name'], build_args(t, wanted)))

    def _details(self, page_id: str, resource: str, limit: int = 100) -> Optional[Dict[str, Any]]:
        """get_page_details(resource=children|attachments|comments|labels), paginated."""
        t = self.r.tool('chalk.get_page_details', required=False)
        if t is None:
            return None
        items: List[Dict[str, Any]] = []
        start = 0
        page_size = max(1, min(int(limit), 100))      # tool accepts 1-100
        while len(items) < limit:
            res = normalize_list(self.client.call_tool(t['name'], build_args(t, {
                'page_id': str(page_id), 'resource': resource,
                'limit': page_size, 'start': start})))
            batch = res['results']
            items.extend(batch)
            if len(batch) < page_size:
                break
            start += len(batch)
        return {'results': items[:limit], 'size': len(items[:limit])}

    def get_children(self, page_id: str, limit: int = 100) -> Dict[str, Any]:
        res = self._details(page_id, 'children', limit)
        if res is None:
            res = self._list('chalk.get_children', {'page_id': str(page_id), 'limit': limit})
        if res is None:   # no dedicated tool -> CQL
            res = self.search(f'parent={page_id} AND type=page', limit=limit)
        return res

    def search(self, cql: str, limit: int = 50) -> Dict[str, Any]:
        # "standard" = metadata only; callers fetch bodies with get_page (cached).
        res = self._list('chalk.search', {'cql': _ensure_since(cql, 'lastModified'),
                                          'limit': limit, 'detail_level': 'standard'})
        if res is None:
            raise McpToolError(f'{self.client.srv["key"]} exposes no search tool.')
        return res

    def get_attachments(self, page_id: str) -> Dict[str, Any]:
        return self._details(page_id, 'attachments') or {'results': [], 'size': 0}

    def get_comments(self, page_id: str) -> Dict[str, Any]:
        return self._details(page_id, 'comments') or {'results': [], 'size': 0}

    def get_labels(self, page_id: str) -> Dict[str, Any]:
        return self._details(page_id, 'labels') or {'results': [], 'size': 0}


def jira(log: Optional[Callable[[str], None]] = None) -> JiraMcp:
    """Process-wide JiraMcp (one MCP session per process)."""
    with _SLOCK:
        if 'jira' not in _SINGLETONS:
            _SINGLETONS['jira'] = JiraMcp(log)
        elif log:
            _SINGLETONS['jira'].client.log = log
        return _SINGLETONS['jira']


def chalk(log: Optional[Callable[[str], None]] = None) -> ChalkMcp:
    with _SLOCK:
        if 'chalk' not in _SINGLETONS:
            _SINGLETONS['chalk'] = ChalkMcp(log)
        elif log:
            _SINGLETONS['chalk'].client.log = log
        return _SINGLETONS['chalk']
