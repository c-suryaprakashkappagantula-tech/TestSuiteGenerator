"""
db_reference_extractor.py — Extract database table and column references from
Jira, Chalk, and attachment sources. Associates DB references with endpoints
by co-occurrence in the same text block.

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
class DB_Reference:
    """A database table/column reference extracted from source text."""
    schema: str = ''               # e.g., 'MSPRW'
    table_name: str = ''           # e.g., 'LINES_DETAILS'
    columns: List[str] = field(default_factory=list)  # e.g., ['ORIGIN_MOBILE_MSO_CD']
    source_type: str = ''          # 'description', 'comment', 'subtask', 'attachment', 'chalk'
    source_ref: str = ''           # specific source identifier
    associated_endpoint: Optional[str] = None  # method+path key, or None for global


# ================================================================
# REGEX PATTERNS
# ================================================================

# SCHEMA.TABLE_NAME pattern (e.g., MSPRW.LINES_DETAILS, ACCT.SUBSCRIBER_INFO)
DB_TABLE_PATTERN = re.compile(
    r'\b([A-Z][A-Z0-9_]{1,30})\.([A-Z][A-Z0-9_]{2,50})\b'
)

# Column names: UPPER_SNAKE_CASE words with standard suffixes
# Applied within a proximity window around each table match
DB_COLUMN_PATTERN = re.compile(
    r'\b([A-Z][A-Z0-9_]{2,50}_(?:CD|ID|NM|DT|TS|FL|AMT|QTY|TXT|NBR|IND|DESC|KEY|VAL|TYPE|STATUS|CODE))\b'
)

# Proximity window for column association (characters around table reference)
_COLUMN_PROXIMITY_CHARS = 500


# ================================================================
# HELPERS
# ================================================================

def _dedup_key(schema: str, table_name: str) -> str:
    """Create a deduplication key from schema + table name."""
    return '%s.%s' % (schema.upper(), table_name.upper())


def _find_columns_near_table(text: str, table_pos: int) -> List[str]:
    """Find column names within the proximity window of a table reference."""
    start = max(0, table_pos - _COLUMN_PROXIMITY_CHARS)
    end = min(len(text), table_pos + _COLUMN_PROXIMITY_CHARS)
    window = text[start:end]

    columns = []
    seen = set()
    for match in DB_COLUMN_PATTERN.finditer(window):
        col = match.group(1)
        if col not in seen:
            seen.add(col)
            columns.append(col)
    return columns


def _find_associated_endpoint(text: str, table_pos: int,
                              endpoints: List[Extracted_Endpoint]) -> Optional[str]:
    """Find an endpoint co-occurring in the same text block as a table reference.
    Returns method+path key or None."""
    if not endpoints or not text:
        return None

    # Search the entire text block for endpoint references
    for ep in endpoints:
        pattern = r'\b%s\s+%s\b' % (re.escape(ep.method), re.escape(ep.path))
        if re.search(pattern, text, re.IGNORECASE):
            return '%s %s' % (ep.method, ep.path)

    return None


def _scan_text_for_db_refs(text: str, source_type: str, source_ref: str,
                           endpoints: List[Extracted_Endpoint],
                           seen: dict) -> None:
    """Scan a text block for DB table references and add to seen dict."""
    if not text:
        return

    for match in DB_TABLE_PATTERN.finditer(text):
        schema = match.group(1)
        table_name = match.group(2)
        key = _dedup_key(schema, table_name)

        if key in seen:
            # Add any new columns found near this occurrence
            new_cols = _find_columns_near_table(text, match.start())
            existing_cols = set(seen[key].columns)
            for col in new_cols:
                if col not in existing_cols:
                    seen[key].columns.append(col)
                    existing_cols.add(col)
        else:
            columns = _find_columns_near_table(text, match.start())
            assoc = _find_associated_endpoint(text, match.start(), endpoints)
            seen[key] = DB_Reference(
                schema=schema,
                table_name=table_name,
                columns=columns,
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

def extract_db_references(
    jira: JiraIssue,
    chalk: Optional[ChalkData],
    parsed_docs: List[ParsedDoc],
    endpoints: List[Extracted_Endpoint],
    log: Callable = print
) -> List[DB_Reference]:
    """
    Extract database table/column references and associate with endpoints.

    Scans all text sources for SCHEMA.TABLE_NAME patterns.
    Associates columns within proximity window of table references.
    Associates DB references with endpoints by co-occurrence in same text block.
    """
    seen = {}  # key -> DB_Reference

    log('[DB_REF] Scanning for database references...')

    # 1. Jira description
    _scan_text_for_db_refs(jira.description or '', 'description', jira.key, endpoints, seen)

    # 2. Jira comments
    for comment in (jira.comments or []):
        body = comment.get('body', '')
        author = comment.get('author', 'unknown')
        if isinstance(author, dict):
            author = author.get('displayName', 'unknown')
        _scan_text_for_db_refs(body, 'comment', str(author), endpoints, seen)

    # 3. Jira subtasks
    for subtask in (jira.subtasks or []):
        st_key = subtask.get('key', '')
        st_summary = subtask.get('summary', subtask.get('fields', {}).get('summary', ''))
        st_desc = subtask.get('description', subtask.get('fields', {}).get('description', ''))
        combined = '%s %s' % (st_summary, st_desc)
        _scan_text_for_db_refs(combined, 'subtask', str(st_key), endpoints, seen)

    # 4. Parsed attachments
    for doc in (parsed_docs or []):
        text = (doc.raw_text or '') + ' ' + _flatten_tables(doc.tables)
        _scan_text_for_db_refs(text, 'attachment', doc.filename, endpoints, seen)

    # 5. Chalk data
    if chalk:
        _scan_text_for_db_refs(chalk.scope or '', 'chalk', 'scope', endpoints, seen)
        for scenario in (chalk.scenarios or []):
            title = getattr(scenario, 'title', '')
            validation = getattr(scenario, 'validation', '')
            combined = '%s %s' % (title, validation)
            _scan_text_for_db_refs(combined, 'chalk',
                                   'scenario:%s' % getattr(scenario, 'scenario_id', ''),
                                   endpoints, seen)
        _scan_text_for_db_refs(chalk.raw_text or '', 'chalk', 'raw_text', endpoints, seen)

    # 6. Cabot Chalk DB
    try:
        from pathlib import Path
        import sqlite3 as _sqlite3
        import json as _json
        _cabot_db_path = Path(__file__).parent.parent / 'CABOT_CHALK_DB.db'
        if _cabot_db_path.exists():
            log('[DB_REF] Scanning Cabot Chalk DB...')
            _cdb = _sqlite3.connect(str(_cabot_db_path))
            _cdb.row_factory = _sqlite3.Row
            for _cr in _cdb.execute('SELECT section_name, raw_text FROM Cabot_Chalk').fetchall():
                _scan_text_for_db_refs(_cr['raw_text'] or '', 'cabot_chalk_db', _cr['section_name'] or '', endpoints, seen)
            _cdb.close()
    except Exception as _ce:
        log('[DB_REF] WARNING: Cabot Chalk DB scan failed: %s' % str(_ce)[:100])

    # Build result list
    db_refs = list(seen.values())

    if not db_refs:
        log('[DB_REF] No database references found in any source')
        return []

    log('[DB_REF] Extracted %d unique DB reference(s)' % len(db_refs))
    for ref in db_refs:
        log('[DB_REF]   %s.%s  columns: [%s]  (source: %s:%s, endpoint: %s)' % (
            ref.schema, ref.table_name,
            ', '.join(ref.columns) if ref.columns else 'none',
            ref.source_type, ref.source_ref,
            ref.associated_endpoint or 'unassociated'))

    return db_refs
