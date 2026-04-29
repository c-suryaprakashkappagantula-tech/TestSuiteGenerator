"""
chalk_enricher.py — Smart Cabot Chalk DB enrichment for V6 pipeline.

Given a Jira summary, finds the best-matching Chalk DB section and extracts:
  - New response attributes (field name → DB table.column mapping)
  - DB table/column prerequisites
  - API endpoints listed in the Chalk page
  - Any additional validation context

This is the "intelligence layer" that makes V6 work for ANY Jira ticket,
not just MOBIT2-62376. The flow:
  1. Extract keywords from Jira summary (e.g., "Line Summary APIs")
  2. Score each Chalk DB section by keyword overlap
  3. Parse the matched section's structured tables for field→DB mappings
  4. Return a CabotEnrichment object used by the TC generator

Part of TSG V6.0 — Cabot Test Suite Engine rebuild.
"""
from __future__ import annotations

import re
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Dict, Tuple


# ================================================================
# DATA MODELS
# ================================================================

@dataclass
class FieldMapping:
    """A single response attribute → DB column mapping from Chalk."""
    response_field: str = ""      # e.g., "mobileOriginMSO"
    db_table: str = ""            # e.g., "MSPRW.LINES_DETAILS"
    db_column: str = ""           # e.g., "ORIGIN_MOBILE_MSO_CD"
    notes: str = ""               # e.g., 'Original Mobile Multi-System Operator Code'


@dataclass
class CabotEnrichment:
    """All enrichment data extracted from the matched Chalk DB section."""
    matched_section: str = ""             # Chalk section name that matched
    match_score: float = 0.0              # How well it matched (0-1)
    jira_key_in_chalk: str = ""           # Jira key found in Chalk text (confirms match)
    field_mappings: List[FieldMapping] = field(default_factory=list)
    db_tables: List[str] = field(default_factory=list)        # e.g., ["MSPRW.LINES_DETAILS"]
    db_columns: List[str] = field(default_factory=list)       # e.g., ["ORIGIN_MOBILE_MSO_CD", ...]
    chalk_endpoints: List[str] = field(default_factory=list)  # endpoints found in Chalk section
    new_attributes: List[str] = field(default_factory=list)   # response field names
    raw_context: str = ""                 # raw text from matched section (for fallback)


# ================================================================
# KEYWORD EXTRACTION
# ================================================================

# Words to ignore when matching Jira summary to Chalk sections
_STOP_WORDS = {
    'the', 'a', 'an', 'to', 'in', 'of', 'for', 'and', 'or', 'is', 'are',
    'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
    'did', 'will', 'would', 'could', 'should', 'may', 'might', 'shall',
    'can', 'with', 'at', 'by', 'from', 'on', 'as', 'into', 'through',
    'during', 'before', 'after', 'above', 'below', 'between', 'out',
    'new', 'add', 'update', 'change', 'modify', 'create', 'delete',
    'nmp', 'dev', 'sdit', 'int', 'cabot', 'integration', 'placeholder',
    'cox', 'migration', 'attributes', 'attribute',
}

# Jira key pattern
_JIRA_KEY_PATTERN = re.compile(r'[A-Z]+-\d+')


def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful keywords from text for matching.
    Returns lowercased keywords, excluding stop words and Jira keys."""
    # Remove Jira keys like MOBIT2-62376, MOBIT2-58899
    cleaned = _JIRA_KEY_PATTERN.sub('', text)
    # Remove special chars, split into words
    words = re.findall(r'[a-zA-Z]+', cleaned)
    keywords = []
    for w in words:
        wl = w.lower()
        if wl not in _STOP_WORDS and len(wl) > 2:
            keywords.append(wl)
    return keywords


def _extract_jira_keys(text: str) -> List[str]:
    """Extract all Jira keys from text."""
    return _JIRA_KEY_PATTERN.findall(text)


# ================================================================
# SECTION MATCHING
# ================================================================

def _score_section(section_name: str, section_text: str,
                   jira_keywords: List[str], jira_key: str) -> Tuple[float, str]:
    """Score how well a Chalk section matches the Jira ticket.

    Returns (score, reason) where score is 0.0-1.0.

    Scoring:
      - Jira key found in section text: +0.5 (strong signal)
      - Keyword overlap with section name: up to +0.3
      - Keyword overlap with section text: up to +0.2
    """
    score = 0.0
    reasons = []

    section_lower = section_text.lower()
    name_lower = section_name.lower()

    # 1. Jira key match (strongest signal)
    if jira_key.upper() in section_text:
        score += 0.5
        reasons.append("Jira key %s found in section" % jira_key)

    # 2. Keyword overlap with section NAME (high value — section names are curated)
    name_keywords = _extract_keywords(section_name)
    if jira_keywords and name_keywords:
        overlap = len(set(jira_keywords) & set(name_keywords))
        name_score = min(overlap / max(len(jira_keywords), 1), 1.0) * 0.3
        score += name_score
        if overlap > 0:
            reasons.append("%d/%d keywords match section name" % (overlap, len(jira_keywords)))

    # 3. Keyword overlap with section TEXT (lower value — text is noisy)
    if jira_keywords:
        text_hits = sum(1 for kw in jira_keywords if kw in section_lower)
        text_score = min(text_hits / max(len(jira_keywords), 1), 1.0) * 0.2
        score += text_score
        if text_hits > 0:
            reasons.append("%d/%d keywords found in text" % (text_hits, len(jira_keywords)))

    return score, "; ".join(reasons) if reasons else "no match"


# ================================================================
# TABLE PARSING — extract field→DB column mappings
# ================================================================

def _parse_field_mappings_from_tables(table_data_json: str) -> List[FieldMapping]:
    """Parse structured table data from Chalk to extract field→DB column mappings.

    Looks for tables with columns like:
      - Request Body / Response Body / field / attribute → response field name
      - table / db_table → DB table (e.g., MSPRW.LINES_DETAILS)
      - column / db_column → DB column (e.g., ORIGIN_MOBILE_MSO_CD)
      - notes / description → additional context
    """
    if not table_data_json:
        return []

    mappings = []
    try:
        tables = json.loads(table_data_json)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(tables, list):
        return []

    for tbl in tables:
        if isinstance(tbl, dict):
            # Table with headers and rows
            headers = tbl.get('headers', [])
            rows = tbl.get('rows', [])

            if not headers or not rows:
                continue

            # Normalize headers — strip whitespace and non-breaking spaces
            clean_headers = [h.strip().replace('\xa0', '').lower() for h in headers]

            # Identify column roles
            field_col = None
            table_col = None
            column_col = None
            notes_col = None

            for i, h in enumerate(clean_headers):
                if h in ('request body', 'response body', 'field', 'attribute', 'response field',
                         'new attribute', 'new field', 'api field', 'response attribute'):
                    field_col = headers[i]
                elif h in ('table', 'db table', 'db_table', 'database table', 'source table'):
                    table_col = headers[i]
                elif h in ('column', 'db column', 'db_column', 'database column', 'source column'):
                    column_col = headers[i]
                elif h in ('notes', 'description', 'note', 'comments', 'details'):
                    notes_col = headers[i]

            if not field_col or not table_col:
                continue

            for row in rows:
                if not isinstance(row, dict):
                    continue
                field_name = str(row.get(field_col, '')).strip().replace('\xa0', '')
                db_table = str(row.get(table_col, '')).strip().replace('\xa0', '')
                db_column = str(row.get(column_col, '')).strip().replace('\xa0', '') if column_col else ''
                notes = str(row.get(notes_col, '')).strip().replace('\xa0', '') if notes_col else ''

                # Skip header rows that leaked into data
                if field_name.lower() in ('request body', 'response body', 'field', 'table', 'column'):
                    continue
                if not field_name or not db_table:
                    continue

                mappings.append(FieldMapping(
                    response_field=field_name,
                    db_table=db_table,
                    db_column=db_column,
                    notes=notes,
                ))

    return mappings


def _parse_field_mappings_from_text(raw_text: str) -> List[FieldMapping]:
    """Fallback: extract field→DB column mappings from raw text patterns.

    Looks for patterns like:
      - "mobileOriginMSO ... MSPRW.LINES_DETAILS ... ORIGIN_MOBILE_MSO_CD"
      - "Maps to MSPRW.LINES_DETAILS.ORIGIN_MOBILE_MSO_CD"
    """
    if not raw_text:
        return []

    mappings = []
    # Pattern: camelCase field followed by SCHEMA.TABLE and COLUMN on nearby lines
    # Split into logical blocks (double newline or section headers)
    blocks = re.split(r'\n\s*\n|\n(?=\d+\.)', raw_text)

    for block in blocks:
        # Find camelCase field names
        fields = re.findall(r'\b([a-z][a-zA-Z]*[A-Z][a-zA-Z]*)\b', block)
        # Find SCHEMA.TABLE references
        tables = re.findall(r'\b(MSPRW|MSRWP)\.([\w]+)\b', block)
        # Find COLUMN references (UPPER_SNAKE_CASE with 3+ parts)
        columns = re.findall(r'\b([A-Z][A-Z_]{5,}(?:_CD|_RSN_CD|_IND|_TS|_ID|_DESC|_CODE)?)\b', block)

        if fields and tables:
            # Try to pair fields with their DB references
            for f in fields:
                if len(f) < 6:
                    continue
                # Find the closest table/column pair
                for schema, table in tables:
                    db_table = "%s.%s" % (schema, table)
                    # Find columns that semantically relate to the field
                    matched_col = _match_field_to_column(f, columns)
                    if matched_col:
                        mappings.append(FieldMapping(
                            response_field=f,
                            db_table=db_table,
                            db_column=matched_col,
                        ))
                        break

    return mappings


def _match_field_to_column(field_name: str, columns: List[str]) -> Optional[str]:
    """Try to match a camelCase field name to an UPPER_SNAKE_CASE column.

    e.g., mobileOriginMSO → ORIGIN_MOBILE_MSO_CD
          mobileOriginAccquistion → ORIGIN_MOBILE_ACQN_RSN_CD
    """
    fn = field_name.lower()
    best_col = None
    best_score = 0

    for col in columns:
        cl = col.lower().replace('_', '')
        # Score by how many field name fragments appear in the column
        score = 0
        # Split camelCase into parts
        parts = re.findall(r'[a-z]+|[A-Z][a-z]*', field_name)
        for part in parts:
            pl = part.lower()
            if len(pl) >= 3 and pl in cl:
                score += 1
        if score > best_score:
            best_score = score
            best_col = col

    return best_col if best_score >= 2 else None


# ================================================================
# NEW ATTRIBUTE EXTRACTION FROM RAW TEXT
# ================================================================

def _extract_new_attributes_from_text(raw_text: str) -> List[str]:
    """Extract new response attribute names mentioned in Chalk text.

    Looks for patterns like:
      - "new attribute(s) X and Y"
      - "pass through new attribute X"
      - "Add new attributes X and Y"
    """
    if not raw_text:
        return []

    attrs = []
    # Pattern: "attribute(s) <camelCase> and <camelCase>"
    patterns = [
        r'(?:new\s+)?attribute[s]?\s+(\w+)\s+and\s+(\w+)',
        r'pass\s+through\s+(?:new\s+)?attribute[s]?\s+(\w+)\s+and\s+(\w+)',
        r'(?:add|include|return)\s+(?:new\s+)?attribute[s]?\s+(\w+)\s+and\s+(\w+)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, raw_text, re.IGNORECASE):
            for g in m.groups():
                if g and re.match(r'^[a-z][a-zA-Z]+$', g) and len(g) > 5:
                    if g not in attrs:
                        attrs.append(g)

    # Also check field mappings table for field names
    # (handled separately via _parse_field_mappings_from_tables)

    return attrs


# ================================================================
# MAIN ENRICHMENT FUNCTION
# ================================================================

def enrich_from_chalk_db(
    jira_key: str,
    jira_summary: str,
    jira_description: str = "",
    cabot_db_path: Optional[Path] = None,
    log: Callable = print,
) -> Optional[CabotEnrichment]:
    """Find the best-matching Chalk DB section for a Jira ticket and extract
    all enrichment data (field mappings, DB refs, new attributes, endpoints).

    Parameters
    ----------
    jira_key : str
        Jira issue key (e.g., "MOBIT2-62376").
    jira_summary : str
        Jira issue summary text.
    jira_description : str
        Jira issue description (optional, used for additional keyword matching).
    cabot_db_path : Path | None
        Path to CABOT_CHALK_DB.db.
    log : callable
        Logging function.

    Returns
    -------
    CabotEnrichment | None
        Enrichment data if a matching section was found, None otherwise.
    """
    if cabot_db_path is None:
        cabot_db_path = Path(__file__).parent.parent / "CABOT_CHALK_DB.db"

    if not cabot_db_path.exists():
        log("[ENRICH] No Cabot Chalk DB found at %s" % cabot_db_path)
        return None

    # Extract keywords from Jira summary + description
    combined_text = "%s %s" % (jira_summary, jira_description[:500] if jira_description else "")
    jira_keywords = _extract_keywords(combined_text)
    log("[ENRICH] Jira keywords: %s" % ", ".join(jira_keywords[:10]))

    # Score each Chalk DB section
    conn = sqlite3.connect(str(cabot_db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT section_name, raw_text, table_data_json FROM Cabot_Chalk").fetchall()

    best_section = None
    best_score = 0.0
    best_reason = ""
    best_row = None

    for row in rows:
        section = row['section_name'] or ''
        raw = row['raw_text'] or ''
        score, reason = _score_section(section, raw, jira_keywords, jira_key)
        log("[ENRICH]   Section '%s': score=%.2f (%s)" % (section, score, reason))
        if score > best_score:
            best_score = score
            best_section = section
            best_reason = reason
            best_row = row

    conn.close()

    if not best_row or best_score < 0.15:
        log("[ENRICH] No matching Chalk section found (best score: %.2f)" % best_score)
        return None

    log("[ENRICH] ✅ Best match: '%s' (score=%.2f — %s)" % (best_section, best_score, best_reason))

    # Extract enrichment data from the matched section
    raw_text = best_row['raw_text'] or ''
    table_json = best_row['table_data_json'] or ''

    # 1. Field mappings from structured tables (primary source)
    field_mappings = _parse_field_mappings_from_tables(table_json)
    if not field_mappings:
        # Fallback: parse from raw text
        field_mappings = _parse_field_mappings_from_text(raw_text)
    log("[ENRICH] Field mappings found: %d" % len(field_mappings))
    for fm in field_mappings:
        log("[ENRICH]   %s → %s.%s" % (fm.response_field, fm.db_table, fm.db_column))

    # 2. New attributes from text
    new_attrs = _extract_new_attributes_from_text(raw_text)
    # Also add from field mappings
    for fm in field_mappings:
        if fm.response_field and fm.response_field not in new_attrs:
            new_attrs.append(fm.response_field)
    log("[ENRICH] New attributes: %s" % ", ".join(new_attrs) if new_attrs else "[ENRICH] No new attributes found")

    # 3. DB tables and columns
    db_tables = list(set(fm.db_table for fm in field_mappings if fm.db_table))
    db_columns = list(set(fm.db_column for fm in field_mappings if fm.db_column))

    # 4. Endpoints from Chalk section
    endpoint_pattern = re.compile(
        r'\b(GET|POST|PUT|PATCH|DELETE)\s+(/?[a-zA-Z][a-zA-Z0-9_\-./{}]+)',
        re.IGNORECASE
    )
    bare_pattern = re.compile(
        r'\b([A-Za-z]+(?:service|subscriber|Service|Subscriber)/api/[a-zA-Z0-9_\-./{}v]+)'
    )
    chalk_endpoints = []
    seen_eps = set()
    for m in endpoint_pattern.finditer(raw_text):
        ep = "%s %s" % (m.group(1).upper(), m.group(2))
        if ep not in seen_eps:
            chalk_endpoints.append(ep)
            seen_eps.add(ep)
    for m in bare_pattern.finditer(raw_text):
        ep = "GET /%s" % m.group(1)
        if ep not in seen_eps:
            chalk_endpoints.append(ep)
            seen_eps.add(ep)

    # Check if Jira key appears in the section (confirms match)
    jira_key_in_chalk = jira_key if jira_key.upper() in raw_text.upper() else ""

    enrichment = CabotEnrichment(
        matched_section=best_section,
        match_score=best_score,
        jira_key_in_chalk=jira_key_in_chalk,
        field_mappings=field_mappings,
        db_tables=db_tables,
        db_columns=db_columns,
        chalk_endpoints=chalk_endpoints,
        new_attributes=new_attrs,
        raw_context=raw_text[:2000],
    )

    log("[ENRICH] Enrichment complete: %d field mappings, %d DB tables, %d endpoints, %d new attributes"
        % (len(field_mappings), len(db_tables), len(chalk_endpoints), len(new_attrs)))

    return enrichment
