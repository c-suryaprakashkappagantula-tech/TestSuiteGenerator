"""
nmno_api_lookup.py — NMNO API Chalk DB Lookup for V8.0 Data-First Engine.

Queries the TMO_API_Chalk table (pre-crawled NMNO API spec pages) to extract:
  - Business Rules (error codes → negative TCs)
  - API specifications (endpoints, request/response fields → dimension TCs)

Data flow:
  1. Extract API operation name from Jira summary or Chalk URLs
  2. Query TMO_API_Chalk.section_name LIKE '%api-name%'
  3. Parse table_data_json → Business Rules + API specs
  4. Return structured NMNOLookupResult

This is the LOCAL-FIRST data source — queried BEFORE any live browser crawl.
"""
import re
import json
import time
import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from .config import ROOT

DB_PATH = ROOT / 'tsg_cache.db'


# ================================================================
# DATA MODELS
# ================================================================


@dataclass
class NMNOBusinessRule:
    """A single business rule extracted from TMO_API_Chalk."""
    error_code: str = ''         # e.g., "ERR06", "404"
    rule_name: str = ''          # e.g., "IMEI Check"
    rule_description: str = ''   # e.g., "Verify if IMEI value is present in the request"
    condition: str = ''          # e.g., "If not"
    expected_result: str = ''    # e.g., "throw error"
    error_details: str = ''     # e.g., "ERR01- deviceId-value is empty"
    source_section: str = ''     # e.g., "T008. retrieve-device"
    source_url: str = ''         # section_url from DB row


@dataclass
class NMNOAPISpec:
    """API specification data extracted from TMO_API_Chalk."""
    api_name: str = ''           # e.g., "retrieve-device"
    endpoint: str = ''           # e.g., "/nsl/provisioning/mno/tmo/v1/retrieve-device"
    http_method: str = ''        # e.g., "GET", "POST"
    request_fields: List[Dict[str, str]] = field(default_factory=list)
    response_fields: List[Dict[str, str]] = field(default_factory=list)
    source_section: str = ''
    source_url: str = ''


@dataclass
class NMNOLookupResult:
    """Complete result from NMNO API lookup."""
    api_name: str = ''
    business_rules: List[NMNOBusinessRule] = field(default_factory=list)
    api_specs: List[NMNOAPISpec] = field(default_factory=list)
    sections_matched: List[str] = field(default_factory=list)
    source_type: str = 'TMO_API_Chalk'
    query_time_ms: float = 0.0


# ================================================================
# API OPERATION NAME EXTRACTION
# ================================================================


def extract_api_operation_name(jira_summary: str, chalk_urls: List[str] = None) -> Optional[str]:
    """Extract normalized API operation name from Jira summary or Chalk URLs.

    Priority: Chalk URL-derived name > Jira summary-derived name.
    Returns None if no name can be extracted.

    Examples:
      "[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)" → "retrieve-device"
      Chalk URL ".../T008.+retrieve-device" → "retrieve-device"
    """
    chalk_names = []
    jira_name = None

    # ── Extract from Chalk URLs (highest priority) ──
    if chalk_urls:
        for url in chalk_urls:
            # Pattern: .../T###.+api-name or .../T###. api-name
            url_parts = url.split('/')
            page_segment = url_parts[-1] if url_parts else ''
            page_segment = page_segment.replace('+', ' ').replace('%20', ' ')
            # Match T###. followed by the API name
            match = re.search(r'T\d+\.\s*(.+)', page_segment)
            if match:
                name = match.group(1).strip()
                # Normalize: lowercase, replace spaces with hyphens, strip trailing junk
                name = re.sub(r'[^a-z0-9\-]', '', name.lower().replace(' ', '-'))
                name = name.strip('-')
                if name and len(name) > 3:
                    chalk_names.append(name)

    # ── Extract from Jira summary ──
    summary = jira_summary or ''
    # Pattern: "[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)"
    # Extract the operation part after "New MVNO - " or after ": "
    op_match = re.search(r'(?:New MVNO\s*[-–—]\s*)(.+?)(?:\s*\(|$)', summary)
    if not op_match:
        # Try after ": " prefix
        op_match = re.search(r':\s*(.+?)(?:\s*\(|$)', summary)
    if op_match:
        op_text = op_match.group(1).strip()
        # Normalize: lowercase, replace spaces with hyphens, keep only alphanumeric + hyphens
        jira_name = re.sub(r'[^a-z0-9\-]', '', op_text.lower().replace(' ', '-'))
        # Collapse multiple hyphens
        jira_name = re.sub(r'-{2,}', '-', jira_name).strip('-')
        if jira_name and len(jira_name) < 4:
            jira_name = None

    # Priority: Chalk URL name > Jira name
    if chalk_names:
        return chalk_names[0]
    return jira_name


# ================================================================
# BUSINESS RULES PARSING
# ================================================================


def parse_business_rules(table_data_json: str, section_name: str, section_url: str) -> List[NMNOBusinessRule]:
    """Parse table_data_json to extract Business Rules rows.

    Identifies Business Rules tables by header patterns:
      - Contains "Rule Name" or "Rule Description"
      - Contains "Condition" and "Expected Result"
      - Contains "Error" in any header

    Returns list of NMNOBusinessRule for each valid row.
    """
    if not table_data_json or table_data_json == 'None':
        return []

    try:
        tables = json.loads(table_data_json)
    except (json.JSONDecodeError, TypeError):
        return []

    rules = []

    for table in tables:
        if not isinstance(table, dict):
            continue
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        if not headers or not rows:
            continue

        # Identify Business Rules tables by headers
        headers_lower = [h.lower() for h in headers]
        is_business_rules = (
            any('rule' in h for h in headers_lower) and
            any('condition' in h or 'expected' in h or 'error' in h for h in headers_lower)
        )

        if not is_business_rules:
            continue

        # Map header positions
        header_map = {h.lower(): i for i, h in enumerate(headers)}

        for row in rows:
            if isinstance(row, dict):
                # Dict-style row
                rule = NMNOBusinessRule(
                    source_section=section_name,
                    source_url=section_url,
                )
                for key, val in row.items():
                    key_lower = key.lower()
                    val_str = str(val).strip() if val else ''
                    if 'rule name' in key_lower or key_lower == 'rule name':
                        rule.rule_name = val_str
                    elif 'description' in key_lower:
                        rule.rule_description = val_str
                    elif 'condition' in key_lower:
                        rule.condition = val_str
                    elif 'expected' in key_lower:
                        rule.expected_result = val_str
                    elif 'error' in key_lower:
                        rule.error_details = val_str

                # Extract error code from error_details or rule_name
                _extract_error_code(rule)

                if rule.rule_name or rule.error_details or rule.rule_description:
                    rules.append(rule)

            elif isinstance(row, list):
                # List-style row
                rule = NMNOBusinessRule(
                    source_section=section_name,
                    source_url=section_url,
                )
                for i, val in enumerate(row):
                    if i >= len(headers):
                        break
                    val_str = str(val).strip() if val else ''
                    h = headers_lower[i] if i < len(headers_lower) else ''
                    if 'rule name' in h:
                        rule.rule_name = val_str
                    elif 'description' in h:
                        rule.rule_description = val_str
                    elif 'condition' in h:
                        rule.condition = val_str
                    elif 'expected' in h:
                        rule.expected_result = val_str
                    elif 'error' in h:
                        rule.error_details = val_str

                _extract_error_code(rule)

                if rule.rule_name or rule.error_details or rule.rule_description:
                    rules.append(rule)

    return rules


def _extract_error_code(rule: NMNOBusinessRule):
    """Extract error code from error_details or rule_name fields.

    Also cleans up concatenated error codes in error_details — when a Chalk table
    cell contains two rules merged together like 'ERR12 - desc1ERR13 - desc2',
    we keep only the first rule's text (matching the extracted error_code).
    """
    # Look for ERR## pattern or HTTP status codes
    all_text = ' '.join([rule.error_details, rule.rule_name, rule.rule_description])
    err_match = re.search(r'(ERR\d+)', all_text, re.IGNORECASE)
    if err_match:
        rule.error_code = err_match.group(1).upper()
    else:
        # Try HTTP status codes (3 digits at start)
        http_match = re.search(r'\b(4\d{2}|5\d{2}|3\d{2})\b', all_text)
        if http_match:
            rule.error_code = http_match.group(1)

    # Guard: reject codes that look like labels rather than codes
    # A valid error code: ERR12, 400, GENS-0001, etc. — NO spaces, short, has digits
    if rule.error_code:
        ec = rule.error_code.strip()
        _is_label = (
            ' ' in ec                          # has spaces → label, not code
            or len(ec) > 25                    # too long for a code
            or (len(ec.split()) > 2)           # multi-word
            or ec.lower() in ('if not', 'if', 'when', 'not', 'none', 'n/a', 'return error')
            or (not any(c.isdigit() for c in ec) and len(ec) > 10)  # no digits and long
        )
        if _is_label:
            # Try to derive a short slug from the condition/error_details words
            _source = rule.condition or rule.error_details or rule.rule_description or ''
            _words = re.findall(r'\b[A-Za-z0-9_]{2,}\b', _source)
            _slug = '_'.join(_words[:4])[:30] if _words else ''
            # Only use the slug if it looks like a real code (has uppercase/digits, no pure words)
            if _slug and (any(c.isdigit() for c in _slug) or re.search(r'ERR|CODE|MSG', _slug.upper())):
                rule.error_code = _slug
            else:
                # Derive from original code's key words (capitalize first letters)
                _code_words = re.findall(r'\b[A-Za-z0-9]{2,}\b', ec)
                if _code_words and len(_code_words) <= 3:
                    # e.g. "Line Status validation" → "LINE_STATUS_VALIDATION"
                    rule.error_code = '_'.join(w.upper() for w in _code_words[:3])[:30]
                else:
                    rule.error_code = ''  # no usable code — will generate generic negative

    # Clean concatenated error_details: split on second ERR occurrence
    # e.g. "ERR12 - MDN expected length: 10ERR13- MDN expected length: 10"
    # → keep only "ERR12 - MDN expected length: 10"
    if rule.error_details and rule.error_code:
        # Find all error code positions in the string
        all_matches = list(re.finditer(r'ERR\d+', rule.error_details, re.IGNORECASE))
        if len(all_matches) >= 2:
            # Truncate at the start of the second error code
            second_start = all_matches[1].start()
            rule.error_details = rule.error_details[:second_start].rstrip('- \n')
        # Also clean error_details if it's just repeating the code at start
        if rule.error_details.upper().startswith(rule.error_code.upper()):
            # Trim the redundant "ERR12 - " prefix from condition if present
            suffix = rule.error_details[len(rule.error_code):].lstrip(' -–:')
            if suffix:
                rule.condition = rule.condition or suffix[:100]


# ================================================================
# API SPEC PARSING
# ================================================================


def parse_api_spec_tables(table_data_json: str, raw_text: str, section_name: str, section_url: str) -> NMNOAPISpec:
    """Parse table_data_json and raw_text to extract API specification.

    Looks for:
      - Endpoint/path info from tables with "Function"/"Path" headers
      - Request/response field tables
      - HTTP method from raw_text
    """
    spec = NMNOAPISpec(
        api_name=re.sub(r'^T\d+\.\s*', '', section_name).strip(),
        source_section=section_name,
        source_url=section_url,
    )

    # Extract endpoint from raw_text
    if raw_text:
        ep_match = re.search(r'(/nsl/[^\s"\']+|/nbo/[^\s"\']+|/api/[^\s"\']+)', raw_text)
        if ep_match:
            spec.endpoint = ep_match.group(1)

    if not table_data_json or table_data_json == 'None':
        return spec

    try:
        tables = json.loads(table_data_json)
    except (json.JSONDecodeError, TypeError):
        return spec

    for table in tables:
        if not isinstance(table, dict):
            continue
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        if not headers or not rows:
            continue

        headers_lower = [h.lower() for h in headers]

        # ── API Specification table (Function/Value pairs) ──
        # This is the FIRST table — has rows like "Method" → "HTTPS POST", "Path" → "/nsl/..."
        # Only use the 1st Inbound call (first table with Function/Path headers)
        if any('function' in h for h in headers_lower) and any('path' in h or 'detail' in h for h in headers_lower):
            for row in rows:
                if isinstance(row, dict):
                    func_key = ''
                    func_val = ''
                    for key, val in row.items():
                        if 'function' in key.lower():
                            func_key = str(val).strip().lower()
                        else:
                            func_val = str(val).strip()
                    # Extract Method (HTTPS POST, HTTPS GET, etc.)
                    if func_key == 'method' and func_val and not spec.http_method:
                        if 'post' in func_val.lower():
                            spec.http_method = 'POST'
                        elif 'get' in func_val.lower():
                            spec.http_method = 'GET'
                        elif 'put' in func_val.lower():
                            spec.http_method = 'PUT'
                        elif 'delete' in func_val.lower():
                            spec.http_method = 'DELETE'
                    # Extract Path/Endpoint
                    if ('path' in func_key or 'inbound' in func_key) and '/nsl/' in func_val and not spec.endpoint:
                        spec.endpoint = func_val
                elif isinstance(row, list) and len(row) >= 2:
                    func_key = str(row[0]).strip().lower()
                    func_val = str(row[1]).strip()
                    if func_key == 'method' and func_val and not spec.http_method:
                        if 'post' in func_val.lower():
                            spec.http_method = 'POST'
                        elif 'get' in func_val.lower():
                            spec.http_method = 'GET'
                    if ('path' in func_key or 'inbound' in func_key) and '/nsl/' in func_val and not spec.endpoint:
                        spec.endpoint = func_val

        # Endpoint/path table (legacy fallback)
        if any('path' in h or 'function' in h for h in headers_lower):
            for row in rows:
                if isinstance(row, dict):
                    for key, val in row.items():
                        val_str = str(val).strip() if val else ''
                        if '/nsl/' in val_str or '/nbo/' in val_str:
                            if not spec.endpoint:
                                spec.endpoint = val_str
                elif isinstance(row, list):
                    for val in row:
                        val_str = str(val).strip() if val else ''
                        if '/nsl/' in val_str or '/nbo/' in val_str:
                            if not spec.endpoint:
                                spec.endpoint = val_str

        # Request/response field tables
        if any('field' in h or 'name' in h for h in headers_lower) and any('type' in h for h in headers_lower):
            for row in rows:
                field_info = {}
                if isinstance(row, dict):
                    for key, val in row.items():
                        field_info[key.lower()] = str(val).strip() if val else ''
                elif isinstance(row, list):
                    for i, val in enumerate(row):
                        if i < len(headers):
                            field_info[headers_lower[i]] = str(val).strip() if val else ''

                if field_info:
                    spec.request_fields.append(field_info)

    return spec


# ================================================================
# MAIN LOOKUP FUNCTION
# ================================================================


def lookup_api_specs(api_name: str, log: Callable = print) -> NMNOLookupResult:
    """Query TMO_API_Chalk table and parse results.

    Args:
        api_name: Normalized API operation name (e.g., "retrieve-device")
        log: Logging function

    Returns:
        NMNOLookupResult with business rules and API specs extracted.
    """
    result = NMNOLookupResult(api_name=api_name)

    if not api_name:
        return result

    t0 = time.time()
    log('[NMNO-LOOKUP] Searching TMO_API_Chalk for: "%s"' % api_name)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # LIKE match on section_name
        like_pattern = '%%%s%%' % api_name
        rows = cursor.execute(
            "SELECT section_name, section_url, table_data_json, raw_text FROM TMO_API_Chalk WHERE section_name LIKE ?",
            (like_pattern,)
        ).fetchall()

        conn.close()

        if not rows:
            log('[NMNO-LOOKUP]   No matches found for "%s"' % api_name)
            result.query_time_ms = (time.time() - t0) * 1000
            return result

        log('[NMNO-LOOKUP]   Found %d matching sections' % len(rows))

        for row in rows:
            d = dict(row)
            section_name = d.get('section_name', '')
            section_url = d.get('section_url', '')
            table_data_json = d.get('table_data_json', '')
            raw_text = d.get('raw_text', '')

            result.sections_matched.append(section_name)
            log('[NMNO-LOOKUP]   Processing: %s' % section_name)

            # Parse Business Rules
            rules = parse_business_rules(table_data_json, section_name, section_url)
            if rules:
                result.business_rules.extend(rules)
                log('[NMNO-LOOKUP]     Business Rules: %d extracted' % len(rules))

            # Parse API Spec
            spec = parse_api_spec_tables(table_data_json, raw_text, section_name, section_url)
            if spec and (spec.endpoint or spec.request_fields):
                result.api_specs.append(spec)
                log('[NMNO-LOOKUP]     API Spec: endpoint=%s, %d fields' % (
                    spec.endpoint or '?', len(spec.request_fields)))

    except sqlite3.OperationalError as e:
        log('[NMNO-LOOKUP]   [WARN] TMO_API_Chalk table not found: %s' % str(e)[:60])
    except Exception as e:
        log('[NMNO-LOOKUP]   [ERROR] Lookup failed: %s' % str(e)[:100])

    result.query_time_ms = (time.time() - t0) * 1000
    log('[NMNO-LOOKUP] Complete: %d rules, %d specs (%.0fms)' % (
        len(result.business_rules), len(result.api_specs), result.query_time_ms))

    return result
