"""
endpoint_tc_generator.py — Cabot V6 Test Case Generator
Generates per-endpoint test cases (New Feature + Regression) from extracted
API endpoints, impacted fields, and DB references.

Standalone module — does NOT import from test_engine.py.
Uses its own CabotTestCase / CabotTestStep dataclasses matching the
Cabot_NMP Excel format.

TC layout matches the manual sample Cabot_NMP_MOBIT2-62376_TestCases_v3.xlsx:
  - All New Feature TCs first (TC01..TC_N), then all Regression TCs (TC_N+1..TC_2N)
  - test_type = "API"
  - product_areas = full Cabot folder path
  - user_tags = full tag chain
  - description = the long validation sentence
  - Regression TCs validate with Spectrum account (not Cox)

Part of TSG V6.0 — Cabot Test Suite Engine rebuild.
"""
from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any

from .endpoint_extractor import (
    Extracted_Endpoint,
    extract_endpoints,
)
from .field_extractor import (
    Impacted_Field,
    extract_fields,
)
from .db_reference_extractor import (
    DB_Reference,
    extract_db_references,
)
from .chalk_enricher import (
    CabotEnrichment,
    FieldMapping,
    enrich_from_chalk_db,
)


# ================================================================
# DATA MODELS — matches Cabot_NMP Excel column layout
# ================================================================

@dataclass
class CabotTestStep:
    """A single test step inside a Cabot test case."""
    step_type: str = "simple"       # "simple" or "validation"
    step_description: str = ""      # free-text step instruction


@dataclass
class CabotTestCase:
    """One row-group in the Cabot_NMP Excel export."""
    unique_id: int = 0
    tc_name: str = ""               # e.g. "MOBIT2-62376_TC01_GET /path_Validate newly added attributes available in response"
    test_type: str = "API"          # always "API" per sample
    product_areas: str = ""         # full Cabot folder path
    covered_content: str = ""       # left blank per sample (None)
    description: str = ""           # long validation sentence
    estimated_duration: int = 15
    user_tags: str = ""             # full tag chain
    steps: List[CabotTestStep] = field(default_factory=list)


# ================================================================
# HELPERS — field / DB ref association
# ================================================================

def _fields_for_endpoint(
    fields: List[Impacted_Field],
    endpoint: Extracted_Endpoint,
) -> List[Impacted_Field]:
    """Return impacted fields associated with *endpoint* (or ALL).
    Filters out junk/generic field names that aren't real API response attributes."""
    ep_key = "%s %s" % (endpoint.method, endpoint.path)
    _junk_fields = {
        'getLinesById', 'getAllLinesV3', 'getLineById', 'getLinesByAccountId',
        'getAllLines', 'getLineById_1', 'Bearer', 'ACTIVE', 'RESIDENTIAL',
        'ONLINE', 'REGULAR', 'UPGRADE_SALE', 'WEB', 'Retail', 'Change',
        'COX', 'Spectrum', 'Value1', 'Value2', 'USA', 'Date', 'ERR01',
        'string', 'phone', 'type', 'data', 'status', 'account', 'msp',
        'city', 'state', 'country', 'eid', 'mno', 'xxx', 'accountId',
        'lineId', 'accountNumber', 'coxAccountNumber', 'accountnumber',
        'portpreparation', 'cpniauthentication', 'conversioneligibility',
        'taxgeocode', 'activationstatus', 'portvalidation', 'soloAccountStatus',
        'soloservice', 'mboscoreservice', 'mbossubscriber', 'Mboslinesummaryservice',
        'lineservice', 'transferLines', 'createLine', 'deviceSales',
        'transferEligible', 'footprint', 'modemStatus', 'mdnValidation',
        'lineSummary', 'accountSummary', 'activateSubscriber', 'lineDetails',
        'customerinfo', 'getLineDetailsItmbo', 'Authentication',
    }
    raw = [
        f for f in fields
        if (f.associated_endpoint == ep_key or f.associated_endpoint == "ALL")
        and f.field_name not in _junk_fields
        and len(f.field_name) > 5
    ]
    # Prefer description/chalk sourced fields
    preferred = [f for f in raw if f.source_type in ('description', 'chalk')]
    return preferred if preferred else raw[:4]


def _db_refs_for_endpoint(
    db_refs: List[DB_Reference],
    endpoint: Extracted_Endpoint,
) -> List[DB_Reference]:
    """Return DB references associated with *endpoint* (or unassociated)."""
    ep_key = "%s %s" % (endpoint.method, endpoint.path)
    return [
        ref for ref in db_refs
        if ref.associated_endpoint == ep_key or ref.associated_endpoint is None
    ]


def _format_path_params(endpoint: Extracted_Endpoint) -> str:
    """Human-readable path-parameter placeholder string."""
    if not endpoint.path_params:
        return ""
    return " (path parameters: %s)" % ", ".join(
        "{%s}=<valid_value>" % p for p in endpoint.path_params
    )


# ================================================================
# STEP BUILDERS
# ================================================================

def _precondition_step_new_feature(
    endpoint: Extracted_Endpoint,
    db_refs: List[DB_Reference],
    feature_id: str,
    enrichment: Optional[CabotEnrichment] = None,
) -> CabotTestStep:
    """Step 1 — Pre-conditions for a **New Feature** TC.
    Uses enrichment data for precise DB table/column references.
    Matches sample: Cox account + DB table columns + API accessible in SIT."""
    lines = ["Pre-conditions:"]
    lines.append("1) Cox (Cabot) account with a valid line")

    # Use enrichment field mappings (most precise source)
    if enrichment and enrichment.field_mappings:
        db_table = enrichment.field_mappings[0].db_table
        cols = [fm.db_column for fm in enrichment.field_mappings if fm.db_column]
        if db_table and cols:
            lines.append("2) %s table has %s populated for the line" % (
                db_table, " and ".join(cols)))
        else:
            lines.append("2) DB state verified for new-feature validation")
    else:
        # Fallback to db_reference_extractor data
        _real_refs = [r for r in db_refs if r.schema in ('MSPRW', 'MSRWP') and '.' not in r.table_name]
        if _real_refs:
            ref = _real_refs[0]
            _key_cols = [c for c in ref.columns if 'ORIGIN_MOBILE' in c or 'ACQN' in c or 'MSO' in c]
            if not _key_cols:
                _key_cols = ref.columns[:2]
            lines.append("2) %s.%s table has %s populated for the line" % (
                ref.schema, ref.table_name, " and ".join(_key_cols)))
        else:
            lines.append("2) DB state verified for new-feature validation")

    lines.append("3) API endpoint is deployed and accessible in SIT")

    return CabotTestStep(step_type="simple", step_description="\n".join(lines))


def _precondition_step_regression(
    endpoint: Extracted_Endpoint,
    db_refs: List[DB_Reference],
    feature_id: str,
    enrichment: Optional[CabotEnrichment] = None,
) -> CabotTestStep:
    """Step 1 — Pre-conditions for a **Regression** TC.
    Matches sample: Spectrum account + API accessible in SIT."""
    lines = ["Pre-conditions:"]
    lines.append("1) Spectrum account with a valid line")
    lines.append("2) API endpoint is deployed and accessible in SIT")

    return CabotTestStep(step_type="simple", step_description="\n".join(lines))


def _invoke_step_new_feature(endpoint: Extracted_Endpoint) -> CabotTestStep:
    """Step 2 — Invoke the specific API for New Feature (Cox account)."""
    # Determine context type based on path
    if '{line' in endpoint.path or 'line/' in endpoint.path.lower():
        context = "with a valid Cox line ID"
    elif '{account' in endpoint.path or '{id.key}' in endpoint.path:
        context = "with a valid Cox account ID"
    else:
        context = "with a valid Cox account context"
    desc = "Invoke %s %s %s" % (endpoint.method, endpoint.path, context)
    return CabotTestStep(step_type="simple", step_description=desc)


def _invoke_step_regression(endpoint: Extracted_Endpoint) -> CabotTestStep:
    """Step 2 — Invoke the specific API for Regression (Spectrum account)."""
    if '{line' in endpoint.path or 'line/' in endpoint.path.lower():
        context = "with a valid Spectrum line ID"
    elif '{account' in endpoint.path or '{id.key}' in endpoint.path:
        context = "with a valid Spectrum account ID"
    else:
        context = "with a valid Spectrum account context"
    desc = "Invoke %s %s %s" % (endpoint.method, endpoint.path, context)
    return CabotTestStep(step_type="simple", step_description=desc)


def _verify_step_new_feature(
    endpoint: Extracted_Endpoint,
    fields: List[Impacted_Field],
    db_refs: List[DB_Reference] = None,
    enrichment: Optional[CabotEnrichment] = None,
) -> CabotTestStep:
    """Step 3 — Verify response for a **New Feature** TC.
    Uses enrichment field mappings for precise field→DB column validation.
    Matches sample: HTTP 200 + each field present + each field matches DB column."""
    lines = ["1) Verify HTTP status code is 200"]

    step_num = 2

    # PRIMARY: Use enrichment field mappings (from Chalk structured tables)
    if enrichment and enrichment.field_mappings:
        for fm in enrichment.field_mappings:
            lines.append("%d) Verify newly added attribute %s is available in the API response"
                         % (step_num, fm.response_field))
            step_num += 1
        # DB column cross-reference
        for fm in enrichment.field_mappings:
            if fm.db_table and fm.db_column:
                lines.append("%d) Verify %s value matches %s.%s"
                             % (step_num, fm.response_field, fm.db_table, fm.db_column))
                step_num += 1
    else:
        # FALLBACK: Use field_extractor + db_reference_extractor data
        _col_map = {}
        if db_refs:
            _sorted_refs = sorted(
                [r for r in db_refs if r.schema in ('MSPRW', 'MSRWP') and '.' not in r.table_name],
                key=lambda r: (0 if 'LINES_DETAILS' in r.table_name else 1, r.table_name)
            )
            for ref in _sorted_refs:
                for col in ref.columns:
                    col_lower = col.lower()
                    if col_lower not in _col_map:
                        _col_map[col_lower] = '%s.%s.%s' % (ref.schema, ref.table_name, col)

        _real_fields = fields[:4] if fields else []
        for f in _real_fields:
            lines.append("%d) Verify newly added attribute %s is available in the API response"
                         % (step_num, f.field_name))
            step_num += 1
        for f in _real_fields:
            fn = f.field_name.lower()
            matched = None
            for col_lower, col_full in _col_map.items():
                fn_clean = fn.replace('_', '')
                col_clean = col_lower.replace('_', '').replace('origin', '').replace('mobile', '')
                if fn_clean.replace('mobile', '').replace('origin', '') in col_clean or col_clean in fn_clean:
                    matched = col_full
                    break
                if 'mso' in fn and 'mso' in col_lower:
                    matched = col_full
                    break
                if 'acqn' in col_lower and ('accquis' in fn or 'acqn' in fn):
                    matched = col_full
                    break
            if matched:
                lines.append("%d) Verify %s value matches %s" % (step_num, f.field_name, matched))
                step_num += 1

    return CabotTestStep(step_type="validation", step_description="\n".join(lines))


def _verify_step_regression(
    endpoint: Extracted_Endpoint,
    fields: List[Impacted_Field],
    enrichment: Optional[CabotEnrichment] = None,
) -> CabotTestStep:
    """Step 3 — Verify response for a **Regression** TC.
    Uses enrichment new_attributes for precise field names in regression check.
    Matches sample: existing fields unchanged + new attributes present + backward compat."""
    # Build field name string from enrichment or fields
    if enrichment and enrichment.new_attributes:
        field_names = " and ".join(enrichment.new_attributes)
    elif fields:
        field_names = " and ".join(f.field_name for f in fields[:4])
    else:
        field_names = "newly added attributes"

    desc = (
        "1) Verify HTTP status code is 200\n"
        "2) Verify all existing response fields (lineId, mdn, lineStatus, ratePlan, etc.) "
        "are still present and unchanged\n"
        "3) Verify newly added attributes %s are available in the response "
        "without breaking existing fields\n"
        "4) Verify response structure backward compatibility is maintained"
        % field_names
    )
    return CabotTestStep(step_type="validation", step_description=desc)


# ================================================================
# CORE TC GENERATION
# ================================================================

def _generate_tcs_for_endpoints(
    endpoints: List[Extracted_Endpoint],
    fields: List[Impacted_Field],
    db_refs: List[DB_Reference],
    feature_id: str,
    jira_summary: str = "",
    parent_key: str = "",
    folder_path: str = "",
    tag_chain: str = "",
    enrichment: Optional[CabotEnrichment] = None,
    log: Callable = print,
) -> List[CabotTestCase]:
    """Produce TCs matching the manual sample layout:
      - All New Feature TCs first (TC01..TC_N)
      - Then all Regression TCs (TC_N+1..TC_2N)

    Each TC has exactly 3 steps:
      Step 1 — Pre-conditions
      Step 2 — Invoke the specific API
      Step 3 — Verify response (validation step)

    Metadata matches sample:
      test_type = "API"
      product_areas = full Cabot folder path
      user_tags = full tag chain
      description = long validation sentence
    """
    nf_tcs: List[CabotTestCase] = []
    reg_tcs: List[CabotTestCase] = []

    # Build field summary for description text — use enrichment if available
    if enrichment and enrichment.new_attributes:
        all_field_names = enrichment.new_attributes
    else:
        all_field_names = []
        for ep in endpoints:
            ep_fields = _fields_for_endpoint(fields, ep)
            for f in ep_fields:
                if f.field_name not in all_field_names:
                    all_field_names.append(f.field_name)
    field_summary = " and ".join(all_field_names[:4]) if all_field_names else "newly added attributes"

    for ep in endpoints:
        ep_fields = _fields_for_endpoint(fields, ep)
        ep_db_refs = _db_refs_for_endpoint(db_refs, ep)

        # Determine context for description
        if '{line' in ep.path or 'line/' in ep.path.lower():
            invoke_context = "valid Cox lineId"
            reg_invoke_context = "valid Spectrum lineId"
        elif '{account' in ep.path or '{id.key}' in ep.path:
            invoke_context = "valid Cox accountId"
            reg_invoke_context = "valid Spectrum accountId"
        else:
            invoke_context = "valid Cox accountId"
            reg_invoke_context = "valid Spectrum accountId"

        # ── New Feature TC ──
        nf_desc = (
            "Validate that newly added attributes %s are available in the response "
            "of %s %s when invoked with %s"
            % (field_summary, ep.method, ep.path, invoke_context)
        )
        nf_tc = CabotTestCase(
            unique_id=0,  # will be assigned after grouping
            tc_name="%s_TC%%02d_%s %s_Validate newly added attributes available in response" % (
                feature_id, ep.method, ep.path),
            test_type="API",
            product_areas=folder_path or "Network and Provisioning",
            covered_content="",
            description=nf_desc,
            estimated_duration=15,
            user_tags=tag_chain or feature_id,
            steps=[
                _precondition_step_new_feature(ep, ep_db_refs, feature_id, enrichment),
                _invoke_step_new_feature(ep),
                _verify_step_new_feature(ep, ep_fields, ep_db_refs, enrichment),
            ],
        )
        nf_tcs.append(nf_tc)
        log("[CABOT_TC] New Feature — %s %s" % (ep.method, ep.path))

        # ── Regression TC ──
        reg_desc = (
            "Regression: Validate existing response fields are unchanged and "
            "newly added attributes are available for Spectrum account"
        )
        reg_tc = CabotTestCase(
            unique_id=0,
            tc_name="%s_TC%%02d_Regression_%s %s_Validate response with Spectrum account" % (
                feature_id, ep.method, ep.path),
            test_type="API",
            product_areas=folder_path or "Network and Provisioning",
            covered_content="",
            description=reg_desc,
            estimated_duration=15,
            user_tags=tag_chain or feature_id,
            steps=[
                _precondition_step_regression(ep, ep_db_refs, feature_id, enrichment),
                _invoke_step_regression(ep),
                _verify_step_regression(ep, ep_fields, enrichment),
            ],
        )
        reg_tcs.append(reg_tc)
        log("[CABOT_TC] Regression — %s %s" % (ep.method, ep.path))

    # ── Combine: all New Feature first, then all Regression ──
    all_tcs = nf_tcs + reg_tcs

    # Assign sequential TC numbers and unique_ids
    for i, tc in enumerate(all_tcs):
        tc_num = i + 1
        tc.tc_name = tc.tc_name % tc_num
        tc.unique_id = tc_num

    log("[CABOT_TC] Layout: %d New Feature (TC01-TC%02d) + %d Regression (TC%02d-TC%02d)"
        % (len(nf_tcs), len(nf_tcs), len(reg_tcs), len(nf_tcs) + 1, len(all_tcs)))

    return all_tcs


# ================================================================
# CABOT CHALK DB SCANNER
# ================================================================

def _scan_cabot_chalk_db(db_path: Path, log: Callable = print) -> Optional[Dict[str, Any]]:
    """Scan the Cabot Chalk DB and return raw text + table data for extraction.

    Returns a dict with keys usable by the extractors, or None if DB not found.
    """
    if not db_path.exists():
        log("[CABOT_DB] No Cabot Chalk DB found at %s — skipping" % db_path)
        return None

    log("[CABOT_DB] Scanning Cabot Chalk DB (%s)..." % db_path.name)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT section_name, raw_text, table_data_json FROM Cabot_Chalk"
        ).fetchall()
        conn.close()

        combined_text = []
        for row in rows:
            section = row["section_name"] or ""
            raw = row["raw_text"] or ""
            table_json = row["table_data_json"] or ""
            combined_text.append(raw)
            if table_json:
                try:
                    tables = json.loads(table_json)
                    if isinstance(tables, list):
                        for tbl in tables:
                            if isinstance(tbl, dict):
                                combined_text.append(
                                    " ".join(str(v) for v in tbl.values() if v)
                                )
                            elif isinstance(tbl, list):
                                for tbl_row in tbl:
                                    if isinstance(tbl_row, dict):
                                        combined_text.append(
                                            " ".join(str(v) for v in tbl_row.values() if v)
                                        )
                                    elif isinstance(tbl_row, (list, tuple)):
                                        combined_text.append(
                                            " ".join(str(c) for c in tbl_row if c)
                                        )
                except Exception:
                    pass

        log("[CABOT_DB] Scanned %d sections from Cabot Chalk DB" % len(rows))
        return {"raw_text": "\n".join(combined_text), "row_count": len(rows)}

    except Exception as e:
        log("[CABOT_DB] WARNING: Cabot Chalk DB scan failed: %s" % str(e)[:120])
        return None


# ================================================================
# PUBLIC API — build_cabot_test_suite()
# ================================================================

def build_cabot_test_suite(
    jira_description: str,
    jira_comments: List[Dict[str, Any]],
    jira_subtasks: List[Dict[str, Any]],
    feature_id: str,
    cabot_chalk_db_path: Optional[Path] = None,
    jira=None,
    chalk=None,
    parsed_docs=None,
    jira_summary: str = "",
    parent_key: str = "",
    folder_path: str = "",
    tag_chain: str = "",
    log: Callable = print,
) -> List[CabotTestCase]:
    """Build a Cabot test suite from Jira data and Cabot Chalk DB.

    Pipeline:
      1. extract_endpoints  — find API endpoints in all sources
      2. extract_fields     — find impacted response field names
      3. extract_db_references — find DB table/column references
      4. generate per-endpoint TCs (New Feature first, then Regression)

    Parameters
    ----------
    jira_description : str
        The Jira issue description text.
    jira_comments : list[dict]
        List of Jira comment dicts (with 'body', 'author', etc.).
    jira_subtasks : list[dict]
        List of Jira subtask dicts (with 'key', 'summary', 'description').
    feature_id : str
        The Jira feature key, e.g. "MOBIT2-62376".
    cabot_chalk_db_path : Path | None
        Path to the CABOT_CHALK_DB.db file.
    jira : JiraIssue | None
        Full JiraIssue object (if available from the fetcher).
    chalk : ChalkData | None
        Full ChalkData object (if available from the parser).
    parsed_docs : list[ParsedDoc] | None
        Parsed attachment documents.
    jira_summary : str
        Jira issue summary text (used in folder path construction).
    parent_key : str
        Parent Jira key (e.g. "MOBIT2-58898").
    folder_path : str
        Full Cabot folder path for product_areas column.
    tag_chain : str
        Full tag chain for user_tags column.
    log : callable
        Logging function (default: print).

    Returns
    -------
    list[CabotTestCase]
        Generated test cases ready for Excel export.
    """
    log("[CABOT] ═══════════════════════════════════════════════════")
    log("[CABOT] Starting Cabot V6 Test Suite Generation for %s" % feature_id)
    log("[CABOT] ═══════════════════════════════════════════════════")

    # Resolve Cabot Chalk DB path
    if cabot_chalk_db_path is None:
        cabot_chalk_db_path = Path(__file__).parent.parent / "CABOT_CHALK_DB.db"

    # Scan Cabot Chalk DB (informational — extractors also scan it internally)
    _scan_cabot_chalk_db(cabot_chalk_db_path, log)

    # ── Build a minimal JiraIssue if a full object wasn't provided ──
    if jira is None:
        from .jira_fetcher import JiraIssue
        jira = JiraIssue()
        jira.key = feature_id
        jira.summary = jira_summary or feature_id
        jira.description = jira_description or ""
        jira.comments = jira_comments or []
        jira.subtasks = jira_subtasks or []

    if parsed_docs is None:
        parsed_docs = []

    # ── Step 1: Extract endpoints from JIRA FIRST (source of truth) ──
    log("[CABOT] Step 1a: Extracting API endpoints from Jira...")
    from .endpoint_extractor import extract_endpoints as _raw_extract
    import os as _os
    _cabot_env_backup = _os.environ.get('_SKIP_CABOT_DB', '')
    _os.environ['_SKIP_CABOT_DB'] = '1'

    try:
        jira_endpoints = extract_endpoints(jira, chalk, parsed_docs, log)
    except Exception as e:
        log("[CABOT] ERROR: Jira endpoint extraction failed: %s" % str(e)[:150])
        jira_endpoints = []
    finally:
        _os.environ['_SKIP_CABOT_DB'] = _cabot_env_backup

    log("[CABOT] Jira endpoints found: %d" % len(jira_endpoints))
    for ep in jira_endpoints:
        log("[CABOT]   %s %s" % (ep.method, ep.path))

    # ── Step 1b: Enrich from Cabot Chalk DB (keyword match) ──
    log("[CABOT] Step 1b: Enriching from Cabot Chalk DB...")
    enrichment = enrich_from_chalk_db(
        jira_key=feature_id,
        jira_summary=jira.summary or jira_summary or feature_id,
        jira_description=jira.description or jira_description or "",
        cabot_db_path=cabot_chalk_db_path,
        log=log,
    )

    if not jira_endpoints:
        log("[CABOT] WARNING: No API endpoints found in Jira — cannot generate TCs")
        return []

    # ── Prefix dedup: remove truncated paths that are prefixes of longer paths ──
    _all_paths = [ep.path for ep in jira_endpoints]
    _deduped = []
    for ep in jira_endpoints:
        _is_prefix = any(
            other != ep.path and (other.startswith(ep.path + '/') or other.startswith(ep.path + '/{'))
            for other in _all_paths
        )
        if _is_prefix:
            log("[CABOT]   Removed truncated prefix: %s %s" % (ep.method, ep.path))
        else:
            _deduped.append(ep)
    if len(_deduped) < len(jira_endpoints):
        log("[CABOT] Prefix dedup: %d → %d endpoints" % (len(jira_endpoints), len(_deduped)))

    # ── Typo dedup: remove endpoints that are typos of other endpoints ──
    # e.g., /mbossubscriber/api/acount/lines is a typo of /mbossubscriber/api/account/lines
    _typo_cleaned = []
    _removed_typos = set()
    for i, ep in enumerate(_deduped):
        if i in _removed_typos:
            continue
        is_typo = False
        for j, other in enumerate(_deduped):
            if i == j or j in _removed_typos:
                continue
            # Check if paths differ by only 1-2 characters (likely typo)
            if ep.method == other.method and _is_likely_typo(ep.path, other.path):
                # Keep the longer path (more likely correct) or the one with more sources
                if len(other.path) > len(ep.path) or len(other.sources) > len(ep.sources):
                    is_typo = True
                    log("[CABOT]   Removed typo endpoint: %s %s (kept: %s)" % (ep.method, ep.path, other.path))
                    break
        if not is_typo:
            _typo_cleaned.append(ep)
    if len(_typo_cleaned) < len(_deduped):
        log("[CABOT] Typo dedup: %d → %d endpoints" % (len(_deduped), len(_typo_cleaned)))
    endpoints = _typo_cleaned

    # ── Step 2: Extract impacted fields ──
    log("[CABOT] Step 2: Extracting impacted fields...")
    try:
        fields = extract_fields(jira, chalk, parsed_docs, endpoints, log)
    except Exception as e:
        log("[CABOT] WARNING: Field extraction failed: %s — continuing with empty fields" % str(e)[:100])
        fields = []

    log("[CABOT] Found %d impacted field(s)" % len(fields))

    # ── Step 3: Extract DB references ──
    log("[CABOT] Step 3: Extracting DB references...")
    try:
        db_refs = extract_db_references(jira, chalk, parsed_docs, endpoints, log)
    except Exception as e:
        log("[CABOT] WARNING: DB reference extraction failed: %s — continuing with empty refs" % str(e)[:100])
        db_refs = []

    log("[CABOT] Found %d DB reference(s)" % len(db_refs))

    # ── Step 4: Generate per-endpoint TCs ──
    log("[CABOT] Step 4: Generating per-endpoint test cases...")
    test_cases = _generate_tcs_for_endpoints(
        endpoints, fields, db_refs, feature_id,
        jira_summary=jira_summary,
        parent_key=parent_key,
        folder_path=folder_path,
        tag_chain=tag_chain,
        enrichment=enrichment,
        log=log,
    )

    nf_count = len(endpoints)
    reg_count = len(endpoints)
    log("[CABOT] ═══════════════════════════════════════════════════")
    log("[CABOT] Generated %d test case(s) (%d New Feature + %d Regression)"
        % (len(test_cases), nf_count, reg_count))
    log("[CABOT] ═══════════════════════════════════════════════════")

    return test_cases


def _is_likely_typo(path_a: str, path_b: str) -> bool:
    """Check if two paths differ by only 1-2 characters (likely a typo).
    e.g., /api/acount/lines vs /api/account/lines"""
    if abs(len(path_a) - len(path_b)) > 2:
        return False
    # Split into segments and compare
    segs_a = path_a.strip('/').split('/')
    segs_b = path_b.strip('/').split('/')
    if len(segs_a) != len(segs_b):
        return False
    diff_count = 0
    for sa, sb in zip(segs_a, segs_b):
        if sa != sb:
            diff_count += 1
            # Check edit distance of the differing segment
            if _edit_distance(sa, sb) > 2:
                return False
    return diff_count == 1


def _edit_distance(s1: str, s2: str) -> int:
    """Simple Levenshtein edit distance."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]
