"""
endpoint_extractor.py — Extract API endpoints from Jira, Chalk, and attachment sources.
Scans description, comments, subtasks, parsed docs, and Chalk data for
HTTP method + URL path patterns. Deduplicates by (method, path).

Part of TSG V6.0 — API Endpoint Extraction feature.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from .jira_fetcher import JiraIssue
from .chalk_parser import ChalkData
from .doc_parser import ParsedDoc


# ================================================================
# DATA MODELS
# ================================================================

@dataclass
class EndpointSource:
    """Tracks where an endpoint was found."""
    source_type: str = ''      # 'description', 'comment', 'subtask', 'attachment', 'chalk'
    source_ref: str = ''       # comment author, subtask key, filename, etc.
    timestamp: str = ''        # comment timestamp (if applicable)


@dataclass
class Extracted_Endpoint:
    """A single API endpoint extracted from Jira/Chalk sources."""
    method: str = ''                                        # GET, POST, PUT, PATCH, DELETE
    path: str = ''                                          # /api/accounts/{id.key}/lines
    path_params: List[str] = field(default_factory=list)    # ['id.key', 'line.id.key']
    sources: List[EndpointSource] = field(default_factory=list)
    swagger_url: str = ''                                   # Optional Swagger/OpenAPI URL
    impacted_fields: List = field(default_factory=list)     # populated later by field_extractor
    db_references: List = field(default_factory=list)       # populated later by db_reference_extractor


# ================================================================
# REGEX PATTERNS
# ================================================================

# Primary endpoint pattern: HTTP_METHOD followed by URL path
# Handles both /path and path (Cabot Chalk often omits leading slash)
ENDPOINT_PATTERN = re.compile(
    r'\b(GET|POST|PUT|PATCH|DELETE)\s+'          # HTTP method
    r'(/?[a-zA-Z][a-zA-Z0-9_\-./{}]+)',          # URL path (optional leading /)
    re.IGNORECASE
)

# Fallback pattern: API paths WITHOUT HTTP method prefix
# Matches known service name patterns like Mboslinesummaryservice/api/...
# These are assumed to be GET endpoints
BARE_API_PATH_PATTERN = re.compile(
    r'\b([A-Za-z]+(?:service|subscriber|Service|Subscriber)'        # service name
    r'/api/[a-zA-Z0-9_\-./{}v]+)',                                  # /api/... path
    re.MULTILINE
)

# Swagger/OpenAPI URL pattern
SWAGGER_PATTERN = re.compile(
    r'(https?://[^\s]+(?:swagger|openapi|api-docs)[^\s]*)',
    re.IGNORECASE
)

# Path parameter pattern: {param_name}
_PATH_PARAM_PATTERN = re.compile(r'\{([^}]+)\}')


# ================================================================
# HELPERS
# ================================================================

def _normalize_path(path: str) -> str:
    """Normalize a URL path for deduplication: ensure leading slash, strip trailing slashes and periods."""
    path = path.rstrip('/').rstrip('.')
    if not path.startswith('/'):
        path = '/' + path
    return path


def _extract_path_params(path: str) -> List[str]:
    """Extract path parameter names from {param} placeholders."""
    return _PATH_PARAM_PATTERN.findall(path)


def _find_endpoints_in_text(text: str) -> List[dict]:
    """Find all METHOD /path matches in a text block. Returns list of dicts.
    Also finds bare API paths without HTTP method (assumed GET)."""
    if not text:
        return []
    results = []
    seen_paths = set()

    # Primary: METHOD /path
    for match in ENDPOINT_PATTERN.finditer(text):
        method = match.group(1).upper()
        path = match.group(2)
        results.append({'method': method, 'path': path})
        seen_paths.add(_normalize_path(path))

    # Fallback: bare API paths (no HTTP method) — assume GET
    for match in BARE_API_PATH_PATTERN.finditer(text):
        path = match.group(1)
        norm = _normalize_path(path)
        if norm not in seen_paths:
            results.append({'method': 'GET', 'path': path})
            seen_paths.add(norm)

    return results


def _find_swagger_url(text: str) -> str:
    """Find the first Swagger/OpenAPI URL in text."""
    if not text:
        return ''
    m = SWAGGER_PATTERN.search(text)
    return m.group(1) if m else ''


def _dedup_key(method: str, path: str) -> str:
    """Create a deduplication key from method + normalized path."""
    return '%s %s' % (method.upper(), _normalize_path(path))


def _add_endpoint(seen: dict, method: str, path: str, source: EndpointSource,
                  swagger_url: str = '') -> None:
    """Add an endpoint to the seen dict, deduplicating by method+path.
    Earliest source wins; later sources are appended to sources list."""
    key = _dedup_key(method, path)
    if key in seen:
        seen[key].sources.append(source)
        # Capture swagger URL if not already set
        if swagger_url and not seen[key].swagger_url:
            seen[key].swagger_url = swagger_url
    else:
        ep = Extracted_Endpoint(
            method=method.upper(),
            path=_normalize_path(path),
            path_params=_extract_path_params(path),
            sources=[source],
            swagger_url=swagger_url,
        )
        seen[key] = ep


def _scan_text_block(text: str, source_type: str, source_ref: str,
                     timestamp: str, seen: dict) -> None:
    """Scan a text block for endpoints and add them to the seen dict."""
    endpoints = _find_endpoints_in_text(text)
    swagger_url = _find_swagger_url(text)
    for ep in endpoints:
        src = EndpointSource(
            source_type=source_type,
            source_ref=source_ref,
            timestamp=timestamp,
        )
        _add_endpoint(seen, ep['method'], ep['path'], src, swagger_url=swagger_url)


def _flatten_tables(tables) -> str:
    """Flatten ParsedDoc tables into a single text string for scanning."""
    if not tables:
        return ''
    parts = []
    for table in tables:
        for row in table:
            for cell in row:
                if cell:
                    parts.append(str(cell))
    return ' '.join(parts)


# ================================================================
# MAIN EXTRACTION FUNCTION
# ================================================================

def extract_endpoints(
    jira: JiraIssue,
    chalk: Optional[ChalkData],
    parsed_docs: List[ParsedDoc],
    log: Callable = print
) -> List[Extracted_Endpoint]:
    """
    Extract API endpoints from all available sources.

    Scans in order:
      1. jira.description
      2. jira.comments (all, chronological)
      3. jira.subtasks (summary + description)
      4. parsed_docs (raw_text + tables)
      5. chalk (scope, scenario titles, scenario validation)

    Returns deduplicated list sorted by extraction order.
    """
    seen = {}  # key -> Extracted_Endpoint (preserves insertion order in Python 3.7+)

    log('[ENDPOINT] Scanning Jira description...')

    # 1. Jira description
    _scan_text_block(
        jira.description, 'description', jira.key, '', seen
    )

    # 2. Jira comments
    for comment in (jira.comments or []):
        author = comment.get('author', comment.get('updateAuthor', {}).get('displayName', 'unknown'))
        if isinstance(author, dict):
            author = author.get('displayName', 'unknown')
        ts = comment.get('created', comment.get('updated', ''))
        body = comment.get('body', '')
        _scan_text_block(body, 'comment', str(author), str(ts), seen)

    # 3. Jira subtasks
    for subtask in (jira.subtasks or []):
        st_key = subtask.get('key', '')
        st_summary = subtask.get('summary', subtask.get('fields', {}).get('summary', ''))
        st_desc = subtask.get('description', subtask.get('fields', {}).get('description', ''))
        combined = '%s %s' % (st_summary, st_desc)
        _scan_text_block(combined, 'subtask', str(st_key), '', seen)

    # 4. Parsed attachments
    for doc in (parsed_docs or []):
        try:
            text = doc.raw_text or ''
            table_text = _flatten_tables(doc.tables)
            combined = '%s %s' % (text, table_text)
            _scan_text_block(combined, 'attachment', doc.filename, '', seen)
        except Exception as e:
            log('[ENDPOINT] WARNING: Could not parse attachment "%s": %s' % (
                getattr(doc, 'filename', '?'), str(e)[:100]))

    # 5. Chalk data
    if chalk:
        log('[ENDPOINT] Scanning Chalk data...')
        # Scope text
        _scan_text_block(chalk.scope or '', 'chalk', 'scope', '', seen)
        # Scenario titles and validation text
        for scenario in (chalk.scenarios or []):
            title = getattr(scenario, 'title', '')
            validation = getattr(scenario, 'validation', '')
            combined = '%s %s' % (title, validation)
            _scan_text_block(combined, 'chalk', 'scenario:%s' % getattr(scenario, 'scenario_id', ''), '', seen)
        # Raw text
        _scan_text_block(chalk.raw_text or '', 'chalk', 'raw_text', '', seen)
    else:
        log('[ENDPOINT] No Chalk data provided — proceeding with Jira-only endpoints')

    # 6. Cabot Chalk DB — scan for API endpoints in the local Cabot DB
    # Skip if _SKIP_CABOT_DB env var is set (used by build_cabot_test_suite
    # to extract Jira-only endpoints first)
    import os as _os_mod
    if _os_mod.environ.get('_SKIP_CABOT_DB'):
        log('[ENDPOINT] Skipping Cabot Chalk DB (Jira-only extraction mode)')
    else:
      try:
        from pathlib import Path
        import sqlite3 as _sqlite3
        import json as _json
        _cabot_db_path = Path(__file__).parent.parent / 'CABOT_CHALK_DB.db'
        if _cabot_db_path.exists():
            log('[ENDPOINT] Scanning Cabot Chalk DB (%s)...' % _cabot_db_path.name)
            _cdb = _sqlite3.connect(str(_cabot_db_path))
            _cdb.row_factory = _sqlite3.Row
            _cabot_rows = _cdb.execute('SELECT section_name, raw_text, table_data_json FROM Cabot_Chalk').fetchall()
            for _cr in _cabot_rows:
                _section = _cr['section_name'] or ''
                _raw = _cr['raw_text'] or ''
                _table_json = _cr['table_data_json'] or ''
                # Scan raw text
                _scan_text_block(_raw, 'cabot_chalk_db', _section, '', seen)
                # Scan table data (flatten JSON tables to text)
                if _table_json:
                    try:
                        _tables = _json.loads(_table_json)
                        if isinstance(_tables, list):
                            for _tbl in _tables:
                                if isinstance(_tbl, dict):
                                    _flat = ' '.join(str(v) for v in _tbl.values() if v)
                                    _scan_text_block(_flat, 'cabot_chalk_db', _section, '', seen)
                                elif isinstance(_tbl, list):
                                    for _row in _tbl:
                                        if isinstance(_row, dict):
                                            _flat = ' '.join(str(v) for v in _row.values() if v)
                                        elif isinstance(_row, (list, tuple)):
                                            _flat = ' '.join(str(c) for c in _row if c)
                                        else:
                                            _flat = str(_row)
                                        _scan_text_block(_flat, 'cabot_chalk_db', _section, '', seen)
                    except Exception:
                        pass
            _cdb.close()
            log('[ENDPOINT] Cabot Chalk DB scan complete')
        else:
            log('[ENDPOINT] No Cabot Chalk DB found at %s — skipping' % _cabot_db_path)
      except Exception as _ce:
        log('[ENDPOINT] WARNING: Cabot Chalk DB scan failed: %s' % str(_ce)[:100])

    # Build result list
    endpoints = list(seen.values())

    if not endpoints:
        log('[ENDPOINT] No API endpoints found in any source')
        return []

    log('[ENDPOINT] Extracted %d unique endpoint(s) from %s' % (
        len(endpoints),
        ', '.join(sorted({s.source_type for ep in endpoints for s in ep.sources}))
    ))
    for ep in endpoints:
        log('[ENDPOINT]   %s %s  (sources: %s)' % (
            ep.method, ep.path,
            ', '.join('%s:%s' % (s.source_type, s.source_ref) for s in ep.sources)
        ))

    return endpoints
