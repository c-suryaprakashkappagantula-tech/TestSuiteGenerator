"""
field_extractor.py — Extract impacted response field names from Jira, Chalk,
and attachment sources. Associates fields with endpoints by proximity.

Part of TSG V6.0 — API Endpoint Extraction feature.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from .jira_fetcher import JiraIssue
from .chalk_parser import ChalkData
from .doc_parser import ParsedDoc
from .endpoint_extractor import Extracted_Endpoint, ENDPOINT_PATTERN


# ================================================================
# DATA MODEL
# ================================================================

@dataclass
class Impacted_Field:
    """A response attribute name extracted from source text."""
    field_name: str = ''           # e.g., 'mobileOriginMSO'
    field_path: str = ''           # e.g., 'response.data.mobileOriginMSO' (if JSON path found)
    source_type: str = ''          # 'description', 'comment', 'subtask', 'attachment', 'chalk'
    source_ref: str = ''           # specific source identifier
    associated_endpoint: Optional[str] = None  # method+path key, or None for global


# ================================================================
# REGEX PATTERNS
# ================================================================

# camelCase attribute names (e.g., mobileOriginMSO, accountStatus)
CAMEL_CASE_FIELD = re.compile(
    r'\b([a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*)\b'
)

# JSON path references (e.g., response.data.mobileOriginMSO, $.lines[0].status)
JSON_PATH_FIELD = re.compile(
    r'(?:response|payload|body|data|result)'
    r'(?:\.[a-zA-Z_][a-zA-Z0-9_]*|\[\d+\])+'
)

# Explicitly listed field names in quotes or backticks
QUOTED_FIELD = re.compile(
    r'[`"\']([a-zA-Z_][a-zA-Z0-9_]*)[`"\']'
)

# Common false-positive camelCase words to exclude
_CAMEL_CASE_EXCLUSIONS = {
    'streamlit', 'javascript', 'typeScript', 'innerHTML', 'className',
    'onClick', 'onChange', 'onSubmit', 'useState', 'useEffect',
    'getElementById', 'querySelector', 'addEventListener',
    'localStorage', 'sessionStorage', 'setTimeout', 'setInterval',
    'parseInt', 'parseFloat', 'toString', 'valueOf', 'hasOwnProperty',
    'isNaN', 'isFinite', 'encodeURI', 'decodeURI',
    'charAt', 'indexOf', 'lastIndexOf', 'substring', 'toLowerCase',
    'toUpperCase', 'startsWith', 'endsWith', 'forEach', 'findIndex',
    'textContent', 'nodeValue', 'childNodes', 'parentNode',
}

# Proximity window: number of lines to search for nearby endpoint
_PROXIMITY_LINES = 3


# ================================================================
# HELPERS
# ================================================================

def _extract_fields_from_text(text: str) -> List[dict]:
    """Extract field names from a text block using all three patterns.
    Returns list of dicts with field_name and field_path."""
    if not text:
        return []

    seen_names = set()
    results = []

    # 1. JSON path references (highest specificity)
    for match in JSON_PATH_FIELD.finditer(text):
        path = match.group(0)
        # Extract the leaf field name from the path
        parts = re.split(r'[.\[\]]+', path)
        leaf = parts[-1] if parts else ''
        if leaf and leaf not in seen_names:
            seen_names.add(leaf)
            results.append({'field_name': leaf, 'field_path': path})

    # 2. Quoted field names
    for match in QUOTED_FIELD.finditer(text):
        name = match.group(1)
        if name and name not in seen_names and len(name) > 2:
            seen_names.add(name)
            results.append({'field_name': name, 'field_path': ''})

    # 3. camelCase attribute names
    for match in CAMEL_CASE_FIELD.finditer(text):
        name = match.group(1)
        if (name and name not in seen_names
                and name not in _CAMEL_CASE_EXCLUSIONS
                and len(name) > 3):
            seen_names.add(name)
            results.append({'field_name': name, 'field_path': ''})

    return results


def _find_nearest_endpoint(text: str, field_pos: int, endpoints: List[Extracted_Endpoint]) -> Optional[str]:
    """Find the nearest endpoint reference in text within the proximity window.
    Returns the method+path key or None."""
    if not endpoints or not text:
        return None

    lines = text.split('\n')
    # Find which line the field is on
    char_count = 0
    field_line = 0
    for i, line in enumerate(lines):
        char_count += len(line) + 1  # +1 for newline
        if char_count > field_pos:
            field_line = i
            break

    # Search within proximity window for endpoint references
    start_line = max(0, field_line - _PROXIMITY_LINES)
    end_line = min(len(lines), field_line + _PROXIMITY_LINES + 1)
    window_text = '\n'.join(lines[start_line:end_line])

    # Check if any endpoint is referenced in the window
    best_ep = None
    best_dist = float('inf')
    for ep in endpoints:
        pattern = r'\b%s\s+%s\b' % (re.escape(ep.method), re.escape(ep.path))
        m = re.search(pattern, window_text, re.IGNORECASE)
        if m:
            # Distance from field position to endpoint match
            dist = abs(m.start() - (field_pos - sum(len(lines[j]) + 1 for j in range(start_line))))
            if dist < best_dist:
                best_dist = dist
                best_ep = '%s %s' % (ep.method, ep.path)

    return best_ep


def _associate_field_with_endpoint(text: str, field_name: str,
                                   endpoints: List[Extracted_Endpoint]) -> Optional[str]:
    """Try to associate a field with an endpoint by proximity in text.
    Returns method+path key or None (meaning associate with all)."""
    if not endpoints or not text:
        return None

    # Find the field position in text
    pos = text.find(field_name)
    if pos < 0:
        return None

    return _find_nearest_endpoint(text, pos, endpoints)


def _scan_text_for_fields(text: str, source_type: str, source_ref: str,
                          endpoints: List[Extracted_Endpoint],
                          seen: dict) -> None:
    """Scan a text block for fields and add to seen dict."""
    fields = _extract_fields_from_text(text)
    for f in fields:
        name = f['field_name']
        if name not in seen:
            assoc = _associate_field_with_endpoint(text, name, endpoints)
            seen[name] = Impacted_Field(
                field_name=name,
                field_path=f['field_path'],
                source_type=source_type,
                source_ref=source_ref,
                associated_endpoint=assoc,
            )


def _flatten_tables(tables) -> str:
    """Flatten ParsedDoc tables into a single text string."""
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

def extract_fields(
    jira: JiraIssue,
    chalk: Optional[ChalkData],
    parsed_docs: List[ParsedDoc],
    endpoints: List[Extracted_Endpoint],
    log: Callable = print
) -> List[Impacted_Field]:
    """
    Extract impacted response field names and associate with endpoints.

    Scans all text sources for camelCase attributes, JSON path references,
    and quoted field names. Deduplicates by unique field name.
    Associates fields with nearest endpoint by proximity.
    Falls back to associating unmatched fields with all endpoints.
    """
    seen = {}  # field_name -> Impacted_Field

    log('[FIELDS] Scanning for impacted fields...')

    # 1. Jira description
    _scan_text_for_fields(jira.description or '', 'description', jira.key, endpoints, seen)

    # 2. Jira comments
    for comment in (jira.comments or []):
        body = comment.get('body', '')
        author = comment.get('author', 'unknown')
        if isinstance(author, dict):
            author = author.get('displayName', 'unknown')
        _scan_text_for_fields(body, 'comment', str(author), endpoints, seen)

    # 3. Jira subtasks
    for subtask in (jira.subtasks or []):
        st_key = subtask.get('key', '')
        st_summary = subtask.get('summary', subtask.get('fields', {}).get('summary', ''))
        st_desc = subtask.get('description', subtask.get('fields', {}).get('description', ''))
        combined = '%s %s' % (st_summary, st_desc)
        _scan_text_for_fields(combined, 'subtask', str(st_key), endpoints, seen)

    # 4. Parsed attachments
    for doc in (parsed_docs or []):
        text = (doc.raw_text or '') + ' ' + _flatten_tables(doc.tables)
        _scan_text_for_fields(text, 'attachment', doc.filename, endpoints, seen)

    # 5. Chalk data
    if chalk:
        _scan_text_for_fields(chalk.scope or '', 'chalk', 'scope', endpoints, seen)
        for scenario in (chalk.scenarios or []):
            title = getattr(scenario, 'title', '')
            validation = getattr(scenario, 'validation', '')
            combined = '%s %s' % (title, validation)
            _scan_text_for_fields(combined, 'chalk',
                                  'scenario:%s' % getattr(scenario, 'scenario_id', ''),
                                  endpoints, seen)
        _scan_text_for_fields(chalk.raw_text or '', 'chalk', 'raw_text', endpoints, seen)

    # 6. Cabot Chalk DB
    try:
        from pathlib import Path
        import sqlite3 as _sqlite3
        import json as _json
        _cabot_db_path = Path(__file__).parent.parent / 'CABOT_CHALK_DB.db'
        if _cabot_db_path.exists():
            log('[FIELDS] Scanning Cabot Chalk DB...')
            _cdb = _sqlite3.connect(str(_cabot_db_path))
            _cdb.row_factory = _sqlite3.Row
            for _cr in _cdb.execute('SELECT section_name, raw_text FROM Cabot_Chalk').fetchall():
                _scan_text_for_fields(_cr['raw_text'] or '', 'cabot_chalk_db', _cr['section_name'] or '', endpoints, seen)
            _cdb.close()
    except Exception as _ce:
        log('[FIELDS] WARNING: Cabot Chalk DB scan failed: %s' % str(_ce)[:100])

    # Build result list
    fields = list(seen.values())

    # Fall back: associate unmatched fields with all endpoints (Req 6.4)
    if endpoints:
        for f in fields:
            if f.associated_endpoint is None:
                f.associated_endpoint = 'ALL'

    if not fields:
        log('[FIELDS] WARNING: No impacted fields found in any source')
        return []

    log('[FIELDS] Extracted %d unique field(s)' % len(fields))
    for f in fields:
        log('[FIELDS]   %s  (source: %s:%s, endpoint: %s)' % (
            f.field_name, f.source_type, f.source_ref,
            f.associated_endpoint or 'ALL'))

    return fields
