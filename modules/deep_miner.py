"""
deep_miner.py — Deep Data Mining Engine for Test Suite Generation.

The philosophy: EXHAUST every data source before generating test cases.
If the Jira gives us Chalk URLs, subtask links, related features — we follow ALL of them.

Data Sources (in priority order):
  1. Chalk URLs embedded in Jira AC → crawl the actual API spec pages
  2. Related features in Chalk DB → find same/similar features in other PIs
  3. Subtask deep-mine → extract every testable item from subtask AC/description
  4. Linked issues → fetch and mine linked Jira issues
  5. Cross-reference → find the same feature ID in other PI Chalk data

This module returns a DeepMineResult that the test_engine uses to build
specific, actionable test cases — NOT generic templates.
"""
import re
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class APISpec:
    """Extracted API specification from Chalk page."""
    api_name: str = ''           # e.g., "retrieve-device", "retrieve-device-GET"
    http_method: str = ''        # GET, POST, PUT, DELETE
    endpoint: str = ''           # e.g., /api/provisioning/v1/line-details
    source_system: str = ''      # e.g., NBOP, ITMBO
    target_system: str = ''      # e.g., NSL, APOLLO_NE
    request_fields: List[str] = field(default_factory=list)   # field names in request
    response_fields: List[str] = field(default_factory=list)  # field names in response
    request_sample: str = ''     # raw request JSON sample
    response_sample: str = ''    # raw response JSON sample
    headers: List[str] = field(default_factory=list)          # required headers
    scenarios: List[Dict] = field(default_factory=list)       # test scenarios from Chalk
    validation_rules: List[str] = field(default_factory=list) # business rules
    error_codes: List[Dict] = field(default_factory=list)     # [{code, message, condition}]
    preconditions: List[str] = field(default_factory=list)
    # V8.0 dimension-aware fields
    products: List[str] = field(default_factory=list)         # e.g., ["Phone", "Tablet", "Smartwatch"]
    channels: List[str] = field(default_factory=list)         # e.g., ["ITMBO", "NBOP"]
    input_types: List[str] = field(default_factory=list)      # e.g., ["MDN", "IMEI", "ICCID"]


@dataclass
class SubtaskMine:
    """Extracted testable items from a Jira subtask."""
    key: str = ''
    summary: str = ''
    component: str = ''          # UI, INT, API, etc.
    ac_items: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    user_story: str = ''
    testable_rules: List[str] = field(default_factory=list)  # specific business rules


@dataclass
class DeepMineResult:
    """Complete deep-mined data for a feature."""
    feature_id: str = ''
    api_specs: List[APISpec] = field(default_factory=list)
    subtask_mines: List[SubtaskMine] = field(default_factory=list)
    related_chalk_scenarios: List[Dict] = field(default_factory=list)
    chalk_urls_found: List[str] = field(default_factory=list)
    chalk_urls_crawled: List[str] = field(default_factory=list)
    all_testable_items: List[str] = field(default_factory=list)
    data_sources_used: List[str] = field(default_factory=list)


# ================================================================
# MAIN ENTRY POINT
# ================================================================

def deep_mine(jira, chalk, page=None, log=print) -> DeepMineResult:
    """
    Exhaustively mine all available data sources for a feature.

    Args:
        jira: JiraIssue object with full data
        chalk: ChalkData object (may be empty)
        page: Playwright page object (for live crawling, optional)
        log: logging function

    Returns:
        DeepMineResult with all extracted data
    """
    result = DeepMineResult(feature_id=jira.key)

    log('[DEEP-MINE] ═══════════════════════════════════════════════')
    log('[DEEP-MINE] Starting deep mine for %s' % jira.key)
    log('[DEEP-MINE] ═══════════════════════════════════════════════')

    # ── Source 1: Extract and crawl Chalk URLs from Jira AC ──
    chalk_urls = _extract_chalk_urls(jira.acceptance_criteria or '')
    if chalk_urls:
        result.chalk_urls_found = chalk_urls
        log('[DEEP-MINE] Found %d Chalk URLs in Jira AC:' % len(chalk_urls))
        for url in chalk_urls:
            log('[DEEP-MINE]   → %s' % url[:80])

        if page:
            for url in chalk_urls:
                api_spec = _crawl_chalk_api_page(page, url, log)
                if api_spec and (api_spec.scenarios or api_spec.request_fields or api_spec.validation_rules):
                    result.api_specs.append(api_spec)
                    result.chalk_urls_crawled.append(url)
                    result.data_sources_used.append('Chalk URL: %s' % api_spec.api_name)
                    log('[DEEP-MINE]   ✓ Crawled: %s (%d scenarios, %d fields, %d rules)' % (
                        api_spec.api_name, len(api_spec.scenarios),
                        len(api_spec.request_fields) + len(api_spec.response_fields),
                        len(api_spec.validation_rules)))

        # ── FALLBACK: If live crawl failed (0 API specs), search Chalk DB by API name ──
        if not result.api_specs:
            log('[DEEP-MINE]   [FALLBACK] Live crawl returned 0 API specs — searching Chalk DB...')
            for url in chalk_urls:
                # Extract API name from URL (e.g., "T008.+retrieve-device" → "retrieve-device")
                _url_parts = url.split('/')
                _page_title = _url_parts[-1] if _url_parts else ''
                _page_title = _page_title.replace('+', ' ').replace('%20', ' ')
                _name_match = re.search(r'T\d+\.\s*(.+)', _page_title)
                _api_name = _name_match.group(1).strip() if _name_match else _page_title
                if _api_name:
                    # Search chalk_cache for any feature with this API in scope/scenarios
                    try:
                        from .database import _conn as _db_conn
                        _c = _db_conn()
                        _like = '%%%s%%' % _api_name.replace('-', '%')
                        try:
                            from .contract_bridge import contract_enabled
                            _contract_v1 = contract_enabled()
                        except Exception:
                            _contract_v1 = False
                        if _contract_v1:
                            # Contract-v1 forbids an API-name cache hit from another feature
                            # becoming a primary test scenario. Same-feature multi-PI data is
                            # already merged by block_chalk_db; this fallback may only recover
                            # scenarios owned by the requested Jira feature.
                            _db_rows = _c.execute(
                                "SELECT feature_id, pi_label, scenarios_json, scope "
                                "FROM chalk_cache WHERE feature_id=? AND scope LIKE ? "
                                "AND scenarios_json != '[]' LIMIT 3",
                                (jira.key, _like),
                            ).fetchall()
                        else:
                            _db_rows = _c.execute(
                                "SELECT feature_id, pi_label, scenarios_json, scope "
                                "FROM chalk_cache WHERE scope LIKE ? "
                                "AND scenarios_json != '[]' LIMIT 3",
                                (_like,),
                            ).fetchall()
                        _c.close()
                        for _row in _db_rows:
                            _rd = dict(_row)
                            _scenarios = json.loads(_rd.get('scenarios_json', '[]'))
                            if _scenarios:
                                # Build a synthetic APISpec from cached scenarios
                                _fallback_spec = APISpec(api_name=_api_name)
                                for _s in _scenarios:
                                    _fallback_spec.scenarios.append(_s)
                                result.api_specs.append(_fallback_spec)
                                result.data_sources_used.append('Chalk DB fallback: %s (%s)' % (_api_name, _rd['feature_id']))
                                log('[DEEP-MINE]   ✓ DB fallback: %s from %s/%s (%d scenarios)' % (
                                    _api_name, _rd['feature_id'], _rd['pi_label'], len(_scenarios)))
                                break  # Use first match per URL
                    except Exception as _db_err:
                        log('[DEEP-MINE]   [WARN] DB fallback failed: %s' % str(_db_err)[:80])
        else:
            log('[DEEP-MINE]   [WARN] No browser page available — cannot crawl Chalk URLs live')

    # ── Source 2: Find related features in Chalk DB ──
    related = _find_related_chalk_in_db(jira, log)
    if related:
        result.related_chalk_scenarios = related
        result.data_sources_used.append('Related Chalk DB (%d scenarios)' % len(related))
        log('[DEEP-MINE] Found %d related scenarios from Chalk DB' % len(related))

    # ── Source 3: Deep-mine subtasks ──
    if jira.subtasks:
        log('[DEEP-MINE] Mining %d subtasks...' % len(jira.subtasks))
        for st in jira.subtasks:
            mine = _mine_subtask(st, log)
            if mine.ac_items or mine.testable_rules:
                result.subtask_mines.append(mine)
                result.data_sources_used.append('Subtask: %s' % mine.key)

        total_items = sum(len(m.ac_items) + len(m.testable_rules) for m in result.subtask_mines)
        log('[DEEP-MINE] Mined %d subtasks → %d testable items' % (len(result.subtask_mines), total_items))

    # ── Source 4: Compile all testable items ──
    result.all_testable_items = _compile_testable_items(result, jira, log)

    log('[DEEP-MINE] ═══════════════════════════════════════════════')
    log('[DEEP-MINE] COMPLETE: %d API specs | %d subtask mines | %d related scenarios | %d testable items' % (
        len(result.api_specs), len(result.subtask_mines),
        len(result.related_chalk_scenarios), len(result.all_testable_items)))
    log('[DEEP-MINE] Data sources: %s' % ', '.join(result.data_sources_used) if result.data_sources_used else 'None')
    log('[DEEP-MINE] ═══════════════════════════════════════════════')

    return result


# ================================================================
# SOURCE 1: CHALK URL EXTRACTION AND CRAWLING
# ================================================================

def _extract_chalk_urls(ac_text: str) -> List[str]:
    """Extract Chalk page URLs from Jira AC text."""
    # Pattern: https://chalk.charter.com/spaces/MDA/pages/XXXXXXX/...
    urls = re.findall(r'https://chalk\.charter\.com/[^\s\]|,)]+', ac_text)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for url in urls:
        # Clean trailing punctuation
        url = url.rstrip('.,;:)')
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _crawl_chalk_api_page(page, url: str, log=print) -> Optional[APISpec]:
    """Crawl a Chalk API specification page and extract structured data."""
    spec = APISpec()

    # Extract API name from URL
    # e.g., "T008.+retrieve-device" → "retrieve-device"
    url_parts = url.split('/')
    page_title = url_parts[-1] if url_parts else ''
    page_title = page_title.replace('+', ' ').replace('%20', ' ')
    # Extract the API name (after "T0XX. ")
    name_match = re.search(r'T\d+\.\s*(.+)', page_title)
    if name_match:
        spec.api_name = name_match.group(1).strip()
    else:
        spec.api_name = page_title

    log('[DEEP-MINE]   Crawling Chalk page: %s' % spec.api_name)

    try:
        page.goto(url, timeout=60000, wait_until='domcontentloaded')
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except:
            pass
        time.sleep(5)  # Chalk pages are heavy

        # Expand all tabs/sections
        page.evaluate("""() => {
            document.querySelectorAll('.tabs-menu li a, [role="tab"]').forEach(t => {
                try { t.click(); } catch(e) {}
            });
            document.querySelectorAll('button[aria-expanded="false"], .expand-control').forEach(b => {
                try { b.click(); } catch(e) {}
            });
        }""")
        time.sleep(2)

        # Extract full page text
        try:
            raw_text = page.evaluate("""() => {
                const main = document.querySelector('#main-content') || document.querySelector('.wiki-content') || document.body;
                return main.innerText;
            }""")
        except:
            raw_text = page.inner_text('body', timeout=30000)

        if not raw_text or len(raw_text) < 50:
            log('[DEEP-MINE]   [WARN] Page returned minimal content (%d chars)' % len(raw_text or ''))
            return spec

        # Extract tables (API specs are usually in tables)
        tables = []
        try:
            tables = page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('table').forEach(table => {
                    const rows = [];
                    table.querySelectorAll('tr').forEach(tr => {
                        const cells = [];
                        tr.querySelectorAll('td, th').forEach(cell => {
                            cells.push(cell.innerText.trim());
                        });
                        if (cells.length > 0) rows.push(cells);
                    });
                    if (rows.length > 0) results.push(rows);
                });
                return results;
            }""")
        except:
            pass

        # Parse the extracted content
        _parse_api_spec_from_text(raw_text, tables, spec, log)

    except Exception as e:
        log('[DEEP-MINE]   [ERROR] Failed to crawl %s: %s' % (url[:50], str(e)[:100]))

    return spec


def _parse_api_spec_from_text(raw_text: str, tables: List, spec: APISpec, log=print):
    """Parse API specification from Chalk page text and tables."""
    lines = [ln.strip() for ln in raw_text.split('\n') if ln.strip()]

    # ── Extract HTTP method ──
    for line in lines:
        line_low = line.lower()
        if 'http method' in line_low or 'method:' in line_low:
            if 'get' in line_low:
                spec.http_method = 'GET'
            elif 'post' in line_low:
                spec.http_method = 'POST'
            elif 'put' in line_low:
                spec.http_method = 'PUT'
            break
    # Fallback: check API name
    if not spec.http_method:
        if 'get' in spec.api_name.lower():
            spec.http_method = 'GET'
        elif 'post' in spec.api_name.lower():
            spec.http_method = 'POST'

    # ── Extract endpoint ──
    for line in lines:
        ep_match = re.search(r'(/api/[^\s"\']+|/mbosportout/[^\s"\']+|/provisioning/[^\s"\']+)', line)
        if ep_match:
            spec.endpoint = ep_match.group(1)
            break

    # ── Extract request/response JSON samples ──
    _in_json = False
    _json_buf = []
    _json_type = ''  # 'request' or 'response'
    for i, line in enumerate(lines):
        line_low = line.lower()
        # Detect start of JSON block
        if not _in_json:
            if any(kw in line_low for kw in ['request payload', 'request body', 'request:', 'sample request']):
                _json_type = 'request'
            elif any(kw in line_low for kw in ['response payload', 'response body', 'response:', 'sample response']):
                _json_type = 'response'
            if '{' in line and _json_type:
                _in_json = True
                _json_buf = [line[line.index('{'):]]
                continue
            elif '{' in line and i > 0 and _json_type:
                _in_json = True
                _json_buf = [line]
                continue
        else:
            _json_buf.append(line)
            # Check if JSON block is complete
            full = '\n'.join(_json_buf)
            open_count = full.count('{')
            close_count = full.count('}')
            if close_count >= open_count and open_count > 0:
                if _json_type == 'request':
                    spec.request_sample = full
                    spec.request_fields = _extract_json_fields(full)
                elif _json_type == 'response':
                    spec.response_sample = full
                    spec.response_fields = _extract_json_fields(full)
                _in_json = False
                _json_buf = []
                _json_type = ''

    # ── Extract from tables ──
    for table in tables:
        if not table:
            continue
        headers = [h.lower() for h in table[0]] if table[0] else []

        # API endpoint table (S.No, API Name, HTTP Method, ...)
        if any('api name' in h or 'http method' in h for h in headers):
            for row in table[1:]:
                if len(row) >= 3:
                    if not spec.http_method and any(m in ' '.join(row).upper() for m in ['GET', 'POST', 'PUT']):
                        for cell in row:
                            if cell.upper() in ('GET', 'POST', 'PUT', 'DELETE'):
                                spec.http_method = cell.upper()
                    if not spec.endpoint:
                        for cell in row:
                            if '/' in cell and ('api' in cell.lower() or 'provisioning' in cell.lower()):
                                spec.endpoint = cell

        # Scenario/test case table
        if any('scenario' in h or 'test case' in h or 'validation' in h for h in headers):
            for row in table[1:]:
                if len(row) >= 2:
                    title = row[0] if row[0] else (row[1] if len(row) > 1 else '')
                    validation = row[-1] if row[-1] else ''
                    if title and len(title) > 10:
                        spec.scenarios.append({
                            'title': title,
                            'validation': validation,
                            'source': 'Chalk API table'
                        })

        # Error code table
        if any('error' in h or 'code' in h for h in headers):
            for row in table[1:]:
                if len(row) >= 2:
                    code = row[0] if row[0] else ''
                    msg = row[1] if len(row) > 1 else ''
                    condition = row[2] if len(row) > 2 else ''
                    if code and (re.match(r'^[A-Z]{2,}', code) or re.match(r'^\d{3}', code)):
                        spec.error_codes.append({
                            'code': code,
                            'message': msg,
                            'condition': condition
                        })

    # ── Extract validation rules from text ──
    for line in lines:
        line_s = line.strip()
        line_low = line_s.lower()
        if len(line_s) < 15 or len(line_s) > 300:
            continue
        # Look for business rules / validation statements
        if any(kw in line_low for kw in ['must ', 'shall ', 'should ', 'verify ', 'validate ',
                                          'when ', 'if the ', 'ensure ', 'the system ']):
            # Filter out navigation/UI text
            if not any(skip in line_low for skip in ['click', 'navigate to', 'menu', 'page load']):
                spec.validation_rules.append(line_s)

    # ── Extract scenarios from text (numbered lists, bullet points) ──
    _scenario_patterns = [
        r'(?:Scenario|TC|Test Case)\s*\d*[:\s]*(.+)',
        r'(?:Positive|Negative|Edge Case)[:\s]*(.+)',
    ]
    for line in lines:
        for pat in _scenario_patterns:
            m = re.match(pat, line, re.IGNORECASE)
            if m and len(m.group(1)) > 15:
                spec.scenarios.append({
                    'title': m.group(1).strip(),
                    'validation': '',
                    'source': 'Chalk text'
                })
                break

    # ── Extract headers ──
    for line in lines:
        line_low = line.lower()
        if 'requesttype' in line_low or 'request type' in line_low:
            spec.headers.append('RequestType')
        if 'messageheader' in line_low or 'message header' in line_low:
            spec.headers.append('messageHeader')
        if 'serviceid' in line_low or 'service id' in line_low:
            spec.headers.append('serviceId')

    # Deduplicate
    spec.headers = list(set(spec.headers))
    spec.validation_rules = list(dict.fromkeys(spec.validation_rules))  # preserve order, remove dupes

    # ── V8.0: Dimension-aware extraction ──
    spec.products = _extract_products_dimension(tables, raw_text)
    spec.channels = _extract_channels_dimension(tables, raw_text)
    spec.input_types = _extract_input_types_dimension(tables, raw_text)

    log('[DEEP-MINE]     Method: %s | Endpoint: %s' % (spec.http_method or '?', spec.endpoint or '?'))
    log('[DEEP-MINE]     Request fields: %d | Response fields: %d' % (len(spec.request_fields), len(spec.response_fields)))
    log('[DEEP-MINE]     Scenarios: %d | Error codes: %d | Rules: %d' % (
        len(spec.scenarios), len(spec.error_codes), len(spec.validation_rules)))
    if spec.products or spec.channels or spec.input_types:
        log('[DEEP-MINE]     V8 Dimensions: products=%s, channels=%s, inputs=%s' % (
            spec.products or '[]', spec.channels or '[]', spec.input_types or '[]'))


def _extract_json_fields(json_text: str) -> List[str]:
    """Extract field names from a JSON sample."""
    fields = re.findall(r'"(\w+)"\s*:', json_text)
    return list(dict.fromkeys(fields))  # deduplicate, preserve order


# ================================================================
# V8.0 DIMENSION-AWARE EXTRACTION
# ================================================================


# Known product names to look for in Chalk pages
_KNOWN_PRODUCTS = ['Phone', 'Tablet', 'Smartwatch', 'Wearable', 'Hotspot', 'IoT', 'Watch']
_KNOWN_CHANNELS = ['ITMBO', 'NBOP', 'IVR', 'CARE']
_KNOWN_INPUT_TYPES = ['MDN', 'IMEI', 'ICCID', 'EID', 'LineID', 'Line ID', 'MSISDN']


def _extract_products_dimension(tables: List, raw_text: str) -> List[str]:
    """Find Products list (Phone, Tablet, Smartwatch) from Chalk tables/text.

    Looks for:
      - Table headers containing 'product' or 'device type'
      - Text patterns like 'Products: Phone, Tablet, Smartwatch'
      - Bullet lists under a 'Products' heading
    """
    products = []

    # Check tables for product columns
    for table in (tables or []):
        if not table:
            continue
        headers = [h.lower() for h in table[0]] if table[0] else []
        if any('product' in h or 'device type' in h or 'device' in h for h in headers):
            prod_col = next((i for i, h in enumerate(headers) if 'product' in h or 'device' in h), -1)
            if prod_col >= 0:
                for row in table[1:]:
                    if len(row) > prod_col and row[prod_col].strip():
                        val = row[prod_col].strip()
                        if val and val not in products and len(val) < 30:
                            products.append(val)

    # Check text for product mentions — ONLY accept when near an explicit Product/Device label.
    # Scanning all raw_text causes false positives from shared PI page content (other features).
    if not products:
        # Only scan sections that have an explicit "Product:" or "Device Type:" label
        product_label_pattern = re.compile(
            r'(?:product|device\s+types?)\s*:\s*([^\n]{3,80})', re.IGNORECASE
        )
        for m in product_label_pattern.finditer(raw_text):
            label_text = m.group(1)
            for product in _KNOWN_PRODUCTS:
                if re.search(r'\b' + re.escape(product) + r'\b', label_text, re.IGNORECASE):
                    if product not in products:
                        products.append(product)

    return products


def _extract_channels_dimension(tables: List, raw_text: str) -> List[str]:
    """Find Channels list (ITMBO, NBOP) from Chalk tables/text."""
    channels = []

    # Check tables for channel columns
    for table in (tables or []):
        if not table:
            continue
        headers = [h.lower() for h in table[0]] if table[0] else []
        if any('channel' in h or 'source' in h for h in headers):
            chan_col = next((i for i, h in enumerate(headers) if 'channel' in h or 'source' in h), -1)
            if chan_col >= 0:
                for row in table[1:]:
                    if len(row) > chan_col and row[chan_col].strip():
                        val = row[chan_col].strip().upper()
                        if val in _KNOWN_CHANNELS and val not in channels:
                            channels.append(val)

    # Check text for channel mentions
    if not channels:
        for channel in _KNOWN_CHANNELS:
            if re.search(r'\b' + re.escape(channel) + r'\b', raw_text):
                if channel not in channels:
                    channels.append(channel)

    return channels


def _extract_input_types_dimension(tables: List, raw_text: str) -> List[str]:
    """Find Input Types (MDN, IMEI, ICCID, EID, LineID) from request fields and text."""
    input_types = []

    # Check text for input type mentions
    for itype in _KNOWN_INPUT_TYPES:
        pattern = r'\b' + re.escape(itype).replace(r'\ ', r'\s*') + r'\b'
        if re.search(pattern, raw_text, re.IGNORECASE):
            normalized = itype.replace(' ', '')
            if normalized not in input_types:
                input_types.append(normalized)

    return input_types


def _extract_business_rules_structured(tables: List) -> List[Dict]:
    """Extract Business Rules table rows as structured dicts.

    Returns list of dicts with keys: error_code, message, condition.
    """
    rules = []

    for table in (tables or []):
        if not table or len(table) < 2:
            continue
        headers = [h.lower() for h in table[0]] if table[0] else []

        # Identify error/business rules tables
        is_error_table = any('error' in h or 'code' in h or 'rule' in h for h in headers)
        if not is_error_table:
            continue

        # Find column indices
        code_col = next((i for i, h in enumerate(headers) if 'code' in h or 'error' in h), 0)
        msg_col = next((i for i, h in enumerate(headers) if 'message' in h or 'description' in h), 1)
        cond_col = next((i for i, h in enumerate(headers) if 'condition' in h or 'trigger' in h or 'when' in h), 2)

        for row in table[1:]:
            if len(row) < 2:
                continue
            code = row[code_col].strip() if len(row) > code_col else ''
            message = row[msg_col].strip() if len(row) > msg_col else ''
            condition = row[cond_col].strip() if len(row) > cond_col else ''

            if code or message:
                rules.append({
                    'error_code': code,
                    'message': message,
                    'condition': condition,
                })

    return rules


# ================================================================
# SOURCE 2: RELATED CHALK IN DB
# ================================================================

def _find_related_chalk_in_db(jira, log=print) -> List[Dict]:
    """Find related features in Chalk DB that have scenarios for the same API.
    STRICT: Only return scenarios that are DIRECTLY relevant to the feature's API.
    A scenario about 'Login Auth' or 'Wearable Activation' is NOT relevant to 'Retrieve Device'."""
    from .database import _conn

    related_scenarios = []
    feature_name_low = jira.summary.lower()

    # Extract the core operation name from the Jira summary
    # "[NSLNM, NENM, INTG]: New MVNO - Retrieve device (GET/POST)" → "retrieve device"
    search_terms = []
    core_match = re.search(r'(?:New MVNO\s*[-–—]\s*)?(.+?)(?:\s*\(|$)', jira.summary.split(':')[-1].strip())
    if core_match:
        core_name = core_match.group(1).strip().lower()
        if len(core_name) > 5:
            search_terms.append(core_name)

    # Also search by API name patterns
    api_names = re.findall(r'(retrieve[- ]device|activate|deactivate|change\s+\w+|swap\s+\w+|port[- ]in)',
                           feature_name_low)
    search_terms.extend(api_names)

    if not search_terms:
        return []

    log('[DEEP-MINE] Searching Chalk DB for related features: %s' % search_terms)

    # Build relevance keywords from the feature name — scenarios must contain at least one
    _relevance_keywords = set()
    for term in search_terms:
        _relevance_keywords.update(w for w in term.split() if len(w) > 3)
    # Add specific API keywords
    _relevance_keywords.update(['device', 'retrieve', 'imei', 'iccid', 'line-details', 'line details'])
    _relevance_keywords.discard('new')
    _relevance_keywords.discard('mvno')

    # STRICT relevance: require the core operation phrase (not just individual words)
    _core_phrase = search_terms[0] if search_terms else ''  # e.g., "retrieve device"

    c = _conn()
    for term in search_terms:
        # Search ONLY by scope containing the exact term (not loose scenarios_json match)
        like_term = '%%%s%%' % term
        rows = c.execute("""
            SELECT feature_id, pi_label, scope, scenarios_json
            FROM chalk_cache
            WHERE scope LIKE ?
            AND scenarios_json != '[]'
            AND feature_id != ?
            ORDER BY pi_label DESC
            LIMIT 5
        """, (like_term, jira.key)).fetchall()

        for row in rows:
            d = dict(row)
            scope_low = (d.get('scope', '') or '').lower()
            # RELEVANCE CHECK: The related feature's scope must contain the CORE PHRASE
            # (e.g., "retrieve device") — not just individual words like "device" alone.
            # This prevents Login Auth, Wearable Activation, etc. from leaking in.
            _scope_relevant = _core_phrase in scope_low or all(
                w in scope_low for w in _core_phrase.split() if len(w) > 3)
            if not _scope_relevant:
                log('[DEEP-MINE]   SKIP (irrelevant scope): %s — %s' % (d['feature_id'], scope_low[:60]))
                continue

            scenarios = json.loads(d.get('scenarios_json', '[]'))
            if scenarios:
                log('[DEEP-MINE]   Found: %s (%s) — %d scenarios' % (
                    d['feature_id'], d['pi_label'], len(scenarios)))
                for s in scenarios:
                    # SCENARIO-LEVEL RELEVANCE: scenario must mention the core API
                    s_title = (s.get('title', '') or '').lower()
                    s_validation = (s.get('validation', '') or '').lower()
                    s_text = s_title + ' ' + s_validation
                    # Must contain at least 2 relevance keywords OR the core phrase
                    _kw_hits = sum(1 for kw in _relevance_keywords if kw in s_text)
                    _scenario_relevant = (_core_phrase in s_text) or (_kw_hits >= 2)
                    if _scenario_relevant:
                        s['_source_feature'] = d['feature_id']
                        s['_source_pi'] = d['pi_label']
                        related_scenarios.append(s)
                    else:
                        log('[DEEP-MINE]     SKIP scenario (irrelevant): %s' % s_title[:50])
    c.close()

    # Deduplicate by title (V8.0: normalized — lowercased, whitespace-collapsed)
    seen_titles = set()
    unique = []
    for s in related_scenarios:
        title_norm = re.sub(r'\s+', ' ', (s.get('title', '') or '').lower().strip())
        if title_norm and title_norm not in seen_titles:
            seen_titles.add(title_norm)
            # V8.0: Add traceability metadata for each related scenario
            s['_traceability'] = {
                'source_type': 'Related Feature',
                'source_id': s.get('_source_feature', ''),
                'extracted_text': (s.get('title', '') or '')[:200],
                'pi_label': s.get('_source_pi', ''),
            }
            unique.append(s)

    return unique


# ================================================================
# SOURCE 3: SUBTASK DEEP MINING
# ================================================================

# Words that mark a line as an assertion rather than prose. Same vocabulary
# linked_fetcher._extract_content already uses, so the two agree on what "testable" means.
_TESTABLE_KEYWORDS = (
    'shall', 'must', 'should', 'verify', 'validate', 'ensure', 'check',
    'given', 'when', 'then', 'is able to', 'able to', 'displays', 'display',
    'sends', 'send', 'updates', 'update', 'rejects', 'reject', 'returns', 'return',
    'expected', 'assert', 'confirm', 'allow', 'allows', 'show', 'shows',
)

# Conjunctions/prepositions that mark a continuation line — but ONLY when they appear in
# lowercase. Case is the reliable signal: a wrapped sentence continues in lowercase
# ("in the requestType field use 'TMO'."), whereas a genuine requirement that happens to
# open with a preposition is capitalised ("On selecting the option, show the same fields").
# Matching case-insensitively discarded that second form, which is a real requirement.
_CONTINUATION_STARTS = (
    'and ', 'or ', 'but ', 'in ', 'on ', 'at ', 'to ', 'for ', 'with ', 'from ',
    'as ', 'if ', 'so ', 'than ', 'that ', 'which ', 'while ', 'though ',
    'because ', 'however ', 'also ', 'i.e', 'e.g', 'etc',
)


_NBSP = '\u00a0'
_ZWSP = '\u200b'

# Colon-terminated AC lines come in two kinds and must not be treated alike.
_ENUM_HEADER_MARKERS = (
    'the following', 'following:', 'as follows', 'below:', 'as below',
    'listed below',
)

# 'Screens impacted:' and friends are scope metadata, not an enumeration of
# requirements. Classified as an enumeration they produced a test case reading
# 'Verify Screens impacted: New Line Activation, Add Line Activation, ...', and they
# also swallowed the genuine requirement that followed them. They fall through to the
# condition default instead, which only ever drops.

# Non-functional acceptance notes. A functional suite has nothing to execute for
# 'KPIs and SLAs should be BAU' - it asserts that nothing changes, about metrics
# rather than behaviour - yet it satisfies the assertion gate through 'should'.
_NON_FUNCTIONAL_SUBJECTS = ('kpi', 'kpis', 'sla', 'slas')
_BAU_MARKERS = ('bau', 'business as usual')

_HEADER_NONE = ''
_HEADER_CONDITION = 'condition'
_HEADER_ENUMERATION = 'enumeration'

# At most this many list items are folded into one composed AC item; beyond that the
# item is summarised, so a 200-row table cannot become a 20,000-character "title".
_MAX_ENUM_CHILDREN = 20

# Requirement verbs this AC actually uses. Each was observed in the before/after diff
# dropping a real requirement that the looser substring match had been keeping by
# accident - e.g. 'In NBOP UI, add a MNO migration radio button', 'VZW commercial lines
# to be migrated to TMO', 'NBOP/NSL to include feature code'.
_REQUIREMENT_VERBS = (
    'add', 'create', 'remove', 'enable', 'disable', 'support', 'provide',
    'include', 'populate', 'migrate',
)

# Matched as stems with an optional inflection. Plain substring matching had been
# passing 'Authentication: JWT' because 'Authentication' contains 'then'; plain word
# boundaries then went too far and lost 'NSL has updated ...', because 'updated' is
# not the listed form of 'update'. Stem + inflection keeps both correct.
_KEYWORD_STEMS = tuple(sorted(
    {k.strip().rstrip('s') for k in _TESTABLE_KEYWORDS if ' ' not in k.strip()}
    | set(_REQUIREMENT_VERBS),
    key=len, reverse=True,
))
_KEYWORD_PHRASES = tuple(k.strip() for k in _TESTABLE_KEYWORDS if ' ' in k.strip())

_KEYWORD_RE = re.compile(
    r'\b(?:%s)(?:s|es|ed|ing|d)?\b|\b(?:%s)\b' % (
        '|'.join(re.escape(s) for s in _KEYWORD_STEMS),
        '|'.join(re.escape(p) for p in _KEYWORD_PHRASES),
    ),
    re.IGNORECASE,
)


def _is_table_markup(text: str) -> bool:
    """True for a Jira table row or header.

    Jira renders tables as '|cell|cell|' and headers as '||cell||'. A row is data, not
    a requirement: MWTGPROV-4086 links its related stories in a table, and its rows
    ('|MWTGNBOP-5584|NBOP - TMO - Enable EID for TMO Transaction|') are not tests.
    """
    t = (text or '').strip()
    if not t:
        return False
    return t.startswith('|') or t.count('|') >= 2


def _is_table_fragment(text: str) -> bool:
    """True when a line is part of a table, judged inside a header's list.

    Stricter than _is_table_markup on purpose. Jira wraps wide tables one cell per
    line, so MWTGPROV-4406's rows arrive as 'Default Value : 1 |' - a single pipe. Within
    a list a single pipe already means table, whereas the gate must stay conservative
    because a requirement sentence could legitimately contain one.
    """
    return '|' in (text or '')


def _is_non_functional_note(text: str) -> bool:
    """True for non-functional acceptance boilerplate.

    'KPIs and SLAs should be BAU' passes the assertion gate on 'should' but there is
    nothing for a functional suite to execute: it asserts that non-functional metrics
    are unchanged. Deliberately narrow - it needs a non-functional subject AND a
    business-as-usual claim, so a real requirement that merely mentions an SLA is
    unaffected.
    """
    low = (text or '').lower()
    words = set(re.findall(r'[a-z]+', low))
    if not words & set(_NON_FUNCTIONAL_SUBJECTS):
        return False
    return any(marker in low for marker in _BAU_MARKERS)


def _normalise_ac_text(text: str) -> str:
    """Replace the invisible characters Jira AC is full of with plain equivalents.

    Non-breaking (U+00A0) and zero-width (U+200B) spaces appear throughout this AC -
    'Mediation\u00a0Function:\u00a0usage-history', 'NBOP_Advanced_Support\u200b'.
    Left in place they survive into test-case titles and defeat word-boundary matching.
    """
    return (text or '').replace(_NBSP, ' ').replace(_ZWSP, '')


def _flatten_ac(text: str) -> str:
    """Collapse an item to a single line. Titles are derived from this."""
    return re.sub(r'\s+', ' ', (text or '').replace('\n', ' ')).strip()


def _strip_ac_bullet(text: str) -> str:
    """Strip leading bullet/number decoration from an item."""
    return re.sub(r'^\s*(?:#+|\*+|\d+[.)]|[-\u2022\u2023\u25cf])\s*', '',
                  (text or '').strip()).strip().strip('#*-\u2022 \t')


def _first_sentence_ac(text: str) -> str:
    """The first sentence of the first line - what the item is actually about.

    Gate 3 is anchored here. Judging the whole item let sprint-planning notes
    ('The following stories are Fast Tracked to 53.3', 24 lines) pass because some
    unrelated line further down happened to say 'display'.
    """
    line = _strip_ac_bullet((text or '').strip().split('\n', 1)[0])
    match = re.match(r'(.+?[.!?])(?:\s|$)', line)
    return (match.group(1) if match else line).strip()


def _asserts_something(text: str) -> bool:
    """True when the text states a checkable expectation, on word boundaries."""
    return bool(_KEYWORD_RE.search(text or ''))


def _header_kind(text: str) -> str:
    """Classify a colon-terminated AC line.

    ENUMERATION introduces the list that carries the requirement's detail
    ('... for the following roles:', 'The response fields ... as follows:'). The
    header alone is not a test; the header plus its items is one.

    CONDITION states a precondition ("When 'MNO_TMO' permission in CS is ON, and line
    is identified as 'TMO' based on networkProvider value:"). That line is exactly
    what became the junk TC07 on MWTGPROV-4190, so it must never become a title.

    Anything colon-terminated that is not an enumeration is treated as a condition.
    That is the safe default: a condition can only ever be dropped, never titled.
    """
    first = _strip_ac_bullet(_flatten_ac((text or '').strip().split('\n', 1)[0]))
    if not first.endswith(':'):
        return _HEADER_NONE
    if any(marker in first.lower() for marker in _ENUM_HEADER_MARKERS):
        return _HEADER_ENUMERATION
    return _HEADER_CONDITION


def _split_ac_items(ac_text: str):
    """Split AC text into one item per requirement.

    Splitting only on '#' and '1.' left bare '-'/'*' bullets and blank-line separated
    paragraphs glued into a single item. Runs before asterisks are stripped, because
    a leading '*' is a bullet here even though '*text*' is Jira bold.
    """
    text = _normalise_ac_text(ac_text)
    parts = re.split(
        r'(?:^|\n)[ \t]*(?:#+[ \t]*|\*+[ \t]+|\d+[.)][ \t]+|[-\u2022\u2023\u25cf][ \t]+)'
        r'|\n[ \t]*\n',
        text,
    )
    out = []
    for part in parts:
        if not part:
            continue
        part = part.replace('*', '').strip()
        if part:
            out.append(part)
    return out


# A row of a list: short, or 'Name - description' / 'Key: value' shaped. Length alone
# is not enough - 'GB_Promo_Remaining - Usage remaining across all Promos in a bill
# cycle.' is 94 characters and is unmistakably a row, while 'In NBOP UI, MNO migration
# selection will be based on EnableMNO_Migration menu access.' is a requirement that
# merely follows one.
_LIST_ENTRY_RE = re.compile(r'^[A-Za-z0-9_.%/\- ]{2,40}\s*[-\u2013:]\s+\S')
_LIST_ENTRY_MAX_PLAIN = 80


def _is_list_entry(text: str) -> bool:
    """True when the line looks like an entry in the list a header introduced."""
    t = (text or '').strip()
    if not t:
        return False
    if len(t) <= _LIST_ENTRY_MAX_PLAIN:
        return True
    return bool(_LIST_ENTRY_RE.match(t))


def _absorb_enumeration_block(items, start: int):
    """Collect the list items belonging to the enumeration header at items[start].

    Absorption stops at the next header, or at the next item that stands on its own as
    a requirement. Everything in between is detail the gate would otherwise discard -
    the point of the change is that it survives attached to its parent instead of
    vanishing along with the parent.

    Returns (children, index_after_block).
    """
    children = []
    # Lines glued into the header item itself are its first children. Table markup is
    # skipped here for the same reason it is skipped below: it is data, not a
    # requirement, and MWTGPROV-4406 reaches this function with its table already glued
    # to the header.
    for line in (items[start] or '').split('\n')[1:]:
        line = _strip_ac_bullet(_flatten_ac(line))
        if _is_table_fragment(line):
            break  # everything from here on is table, not requirement detail
        if line:
            children.append(line)

    index = start + 1
    while index < len(items):
        candidate = items[index]
        if _header_kind(candidate) != _HEADER_NONE:
            break
        if _is_testable_ac_item(candidate):
            break
        line = _strip_ac_bullet(_flatten_ac(candidate))
        if _is_table_fragment(line) or not _is_list_entry(line):
            break
        if len(line) >= 3:
            children.append(line)
        index += 1
    return children, index


# A header owns the list that follows it. Two or more consecutive list-shaped entries
# are a list; one is not. Without this, the entries of a condition header's list are
# gated individually and leak through as requirements - 'Screens impacted:' followed by
# eight screen names produced the test case 'Verify Add Line Activation', because 'add'
# is a requirement verb and 'Add Line Activation' is a screen name.
_MIN_LIST_RUN = 2


def _absorb_header_list(items, start: int):
    """Collect the run of list entries following the header at items[start].

    Unlike _absorb_enumeration_block this ignores the assertion gate, because a list
    entry that happens to read like an assertion is still a list entry. It is therefore
    restricted to genuine runs, and stops at the first item that is not list-shaped.

    Returns (entries, index_after_run) - entries is empty unless the run is long enough.
    """
    entries = []
    index = start + 1
    while index < len(items):
        candidate = items[index]
        if _header_kind(candidate) != _HEADER_NONE:
            break
        line = _strip_ac_bullet(_flatten_ac(candidate))
        if not line or _is_table_fragment(line) or not _is_list_entry(line):
            break
        entries.append(line)
        index += 1
    if len(entries) < _MIN_LIST_RUN:
        return [], start + 1
    return entries, index


def _compose_enumeration_item(header: str, children) -> str:
    """One AC item = the header plus the items it introduces.

    Deliberately a single item. The header alone would be a contentless test ('Use the
    following parameters'), and the children alone are attribute names that assert
    nothing - which is why both sides were being dropped. Together they are one
    testable requirement, and _clean_ac_title truncates on a word boundary so the
    header still reads as the title.
    """
    head = _strip_ac_bullet(_flatten_ac(header)).rstrip(':').strip()
    kept = children[:_MAX_ENUM_CHILDREN]
    composed = '%s: %s' % (head, ', '.join(kept))
    if len(children) > len(kept):
        composed += ', ... (+%d more)' % (len(children) - len(kept))
    return composed


def _is_testable_ac_item(text: str) -> bool:
    """True when a mined line is a standalone, testable AC statement.

    Previously ANY line over 15 characters became an AC item, so wrapped prose and
    notes entered the test-case pool as titles. For MWTGPROV-4190 that produced two
    nonsense test cases:
        "Verify in CS is ON, and line is identified as '_ when MNO TMO permission"
            -> a continuation fragment; the sentence started on the previous line
        "Verify # Same rules applicable for TMO though we don't have SOLO or Second line"
            -> a note, asserting nothing

    Three gates, all required:
      1. Not a CONDITION HEADER. A line ending in ':' introduces the child bullets that
         follow — it states a precondition, not a test. Keeping one produced the junk TC
         "Verify in CS is ON, and line is identified as ... when 'MNO TMO' permission":
         that is the header "When 'MNO_TMO' permission in CS is ON, and line is identified
         as 'TMO' based on networkProvider value:" with its clauses reordered by the title
         builder.
      2. Not a lowercase continuation. CASE is the signal: a wrapped sentence resumes in
         lowercase ("in the requestType field use 'TMO'."), while a real requirement that
         opens with a preposition is capitalised ("On selecting the option, show the same
         fields..."). An earlier case-INSENSITIVE version of this rule discarded that second
         form, silently dropping a genuine requirement — worse than the junk it removed.
      3. Asserts something, mirroring linked_fetcher's existing keyword gate. A line that
         asserts nothing is documentation ("Same rules applicable for TMO though we don't
         have SOLO or Second line in TMO").
    """
    raw = _normalise_ac_text(text)
    t = _strip_ac_bullet(_flatten_ac(raw))
    if len(t) < 15:
        return False
    # Gate 1: a colon-terminated line is a header, not a test in its own right.
    # The caller decides what to do with it: an enumeration header is paired with the
    # items it introduces, a condition header becomes a precondition.
    if _header_kind(raw) != _HEADER_NONE:
        return False
    # Gate 2: lowercase continuation fragment
    low = t.lower()
    first_alpha = next((c for c in t if c.isalpha()), '')
    if first_alpha and first_alpha.islower():
        if low.startswith(_CONTINUATION_STARTS) or not low.startswith(_TESTABLE_KEYWORDS):
            return False
    # Gate 4: table markup is data, not a requirement
    if _is_table_markup(t):
        return False
    # Gate 5: non-functional boilerplate is out of scope for a functional suite
    if _is_non_functional_note(t):
        return False
    # Gate 3: asserts something - judged on the first sentence, not the whole blob
    return _asserts_something(_first_sentence_ac(raw))


def _mine_subtask(subtask: Dict, log=print) -> SubtaskMine:
    """Deep-mine a single subtask for all testable content."""
    mine = SubtaskMine(
        key=subtask.get('key', ''),
        summary=subtask.get('summary', ''),
    )

    # Detect component type from summary — V8.0 improved classification
    summary_low = mine.summary.lower()
    if '- ui' in summary_low or 'ui -' in summary_low or 'nbop' in summary_low or 'portal' in summary_low or 'screen' in summary_low:
        mine.component = 'UI'
    elif '- int' in summary_low or 'integration' in summary_low or 'middleware' in summary_low:
        mine.component = 'INT'
    elif 'nslnm' in summary_low or 'nsl' in summary_low or 'api' in summary_low or 'rest' in summary_low or 'endpoint' in summary_low:
        mine.component = 'API'
    elif 'nenm' in summary_low or 'ne ' in summary_low or 'network element' in summary_low or 'apollo' in summary_low:
        mine.component = 'NE'
    elif 'db' in summary_low or 'database' in summary_low or 'schema' in summary_low:
        mine.component = 'DB'

    # Parse AC
    ac_raw = subtask.get('acceptance_criteria', '') or ''
    ac_raw = re.sub(r'\{[^}]+\}', '', ac_raw)  # Remove Jira formatting macros
    # Asterisk-free copy for the whole-text scans further down. The splitter gets the
    # raw copy, because a leading '*' is a bullet even though '*text*' is Jira bold.
    ac_text = re.sub(r'\*', '', ac_raw)

    def _add_ac_item(value):
        # Trailing ':' would otherwise leave a title ending in a dangling colon.
        value = _strip_ac_bullet(_flatten_ac(value)).rstrip(':').strip()
        if value and value not in mine.ac_items:
            mine.ac_items.append(value)

    items = _split_ac_items(ac_raw)
    i = 0
    while i < len(items):
        item = items[i]
        kind = _header_kind(item)

        if kind == _HEADER_ENUMERATION:
            children, next_index = _absorb_enumeration_block(items, i)
            if children:
                # Header + its list = one requirement. Dropping the header on its own
                # deleted whole requirement blocks (MWTGPROV-4230, 4349, 3971).
                _add_ac_item(_compose_enumeration_item(item, children))
                i = next_index
                continue
            # Nothing to absorb - the list was unusable (a raw JSON payload, say).
            # Judge the header on its own merits rather than discarding it: without
            # this, MWTGPROV-4406 loses 'The following information will be provided by
            # the API response...' entirely.
            bare = _strip_ac_bullet(_flatten_ac(item)).rstrip(':').strip()
            if _is_testable_ac_item(bare):
                _add_ac_item(bare)
            i += 1
            continue

        if kind == _HEADER_CONDITION:
            # A precondition, never a test case. This is TC07's source line on 4190.
            condition = _strip_ac_bullet(_flatten_ac(item))
            entries, next_index = _absorb_header_list(items, i)
            if entries:
                # The list belongs to the header. Left loose, its entries get gated one
                # by one and screen names surface as requirements (MWTGPROV-4349).
                condition = '%s %s' % (condition, ', '.join(entries))
            if condition and condition not in mine.preconditions:
                mine.preconditions.append(condition)
            i = max(next_index, i + 1)
            continue

        if _is_testable_ac_item(item):
            _add_ac_item(item)
        i += 1

    # If nothing was mined, try line-by-line
    if not mine.ac_items:
        for line in ac_text.split('\n'):
            line = line.strip()
            if not line.startswith('http') and _is_testable_ac_item(line):
                _add_ac_item(line)

    # Parse description for pre/post conditions and user story
    desc = subtask.get('description', '') or ''
    desc = re.sub(r'\{[^}]+\}', '', desc)
    desc = re.sub(r'\*+', '', desc)

    # User Story
    us_match = re.search(r'User Story[:\s]*(.+?)(?=Pre.?Condition|Post.?Condition|Assumption|$)',
                         desc, re.IGNORECASE | re.DOTALL)
    if us_match:
        mine.user_story = us_match.group(1).strip()[:300]

    # Pre-Conditions
    pre_match = re.search(r'Pre.?Condition[s]?[:\s]*(.+?)(?=Post.?Condition|Assumption|Dependencies|User Story|$)',
                          desc, re.IGNORECASE | re.DOTALL)
    if pre_match:
        pre_text = pre_match.group(1).strip()
        for line in re.split(r'(?:#|\d+\.)\s*', pre_text):
            line = line.strip()
            if line and len(line) > 10:
                mine.preconditions.append(line)

    # Post-Conditions
    post_match = re.search(r'Post.?Condition[s]?[:\s]*(.+?)(?=Assumption|Dependencies|Reason|$)',
                           desc, re.IGNORECASE | re.DOTALL)
    if post_match:
        post_text = post_match.group(1).strip()
        for line in re.split(r'(?:#|\d+\.)\s*', post_text):
            line = line.strip()
            if line and len(line) > 10:
                mine.postconditions.append(line)

    # Extract specific testable rules (business logic)
    all_text = (ac_text + '\n' + desc).lower()
    _rule_patterns = [
        (r"when\s+['\"]?(\w+)['\"]?\s+permission\s+is\s+(ON|OFF)", 'permission_toggle'),
        (r"(requesttype|request type)\s*(?:=|:|\s+)?\s*['\"]?(\w+)['\"]?", 'request_type'),
        (r"(messageheader|message header)\s+value\s+should\s+be\s+['\"]?(\w+)['\"]?", 'header_value'),
        (r"default\s+to\s+(\w+)", 'default_value'),
        (r"(radio button|dropdown|checkbox)\s+(?:options?|for)\s+(.+?)(?:\.|$)", 'ui_control'),
        (r"send\s+(?:the\s+)?api\s+request\s+with\s+(.+?)(?:\.|$)", 'api_request_rule'),
        (r"response\s+(?:payload\s+)?(?:remains?\s+)?same\s+for\s+(?:both\s+)?(.+)", 'response_parity'),
    ]
    for pattern, rule_type in _rule_patterns:
        matches = re.findall(pattern, all_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                rule_text = ' '.join(match)
            else:
                rule_text = match
            mine.testable_rules.append('%s: %s' % (rule_type, rule_text))

    # ── V8.0: Permission toggle detection ──
    # When subtask mentions a permission toggle (e.g., MNO_TMO), generate
    # testable items for both ON and OFF states
    permission_patterns = [
        r'(\w+_\w+)\s+permission',           # MNO_TMO permission
        r'permission\s+(\w+_\w+)',            # permission MNO_TMO
        r'(\w+)\s+toggle',                    # feature toggle
        r'toggle\s+(\w+)',                    # toggle feature
        r'feature\s+flag\s+(\w+)',            # feature flag X
    ]
    permissions_found = set()
    for pat in permission_patterns:
        perm_matches = re.findall(pat, all_text, re.IGNORECASE)
        for perm in perm_matches:
            perm_name = perm.strip()
            if perm_name and len(perm_name) > 2 and perm_name not in permissions_found:
                permissions_found.add(perm_name)
                # Generate ON state testable item
                on_item = 'When %s permission is ON: feature behavior is enabled' % perm_name
                if on_item not in mine.ac_items:
                    mine.ac_items.append(on_item)
                # Generate OFF state testable item
                off_item = 'When %s permission is OFF: feature behavior is disabled/restricted' % perm_name
                if off_item not in mine.ac_items:
                    mine.ac_items.append(off_item)
                mine.testable_rules.append('permission_toggle: %s ON/OFF' % perm_name)

    log('[DEEP-MINE]   Subtask %s [%s]: %d AC items, %d rules, %d pre, %d post' % (
        mine.key, mine.component, len(mine.ac_items), len(mine.testable_rules),
        len(mine.preconditions), len(mine.postconditions)))

    return mine


def mine_subtask(*args, **kwargs):
    """Public API wrapper for subtask mining."""
    return _mine_subtask(*args, **kwargs)


# ================================================================
# COMPILE ALL TESTABLE ITEMS
# ================================================================

def _compile_testable_items(result: DeepMineResult, jira, log=print) -> List[str]:
    """Compile all mined data into a flat list of testable items."""
    items = []

    # From API specs
    for spec in result.api_specs:
        if spec.http_method:
            items.append('API: %s %s %s' % (spec.http_method, spec.api_name, spec.endpoint))
        for s in spec.scenarios:
            items.append('Scenario [%s]: %s' % (spec.api_name, s.get('title', '')))
        for rule in spec.validation_rules:
            items.append('Rule [%s]: %s' % (spec.api_name, rule))
        for err in spec.error_codes:
            items.append('Error [%s]: %s — %s' % (err.get('code', ''), err.get('message', ''), err.get('condition', '')))

    # From subtasks
    for mine in result.subtask_mines:
        for item in mine.ac_items:
            items.append('AC [%s/%s]: %s' % (mine.key, mine.component, item))
        for rule in mine.testable_rules:
            items.append('Rule [%s]: %s' % (mine.key, rule))
        for post in mine.postconditions:
            items.append('PostCond [%s]: %s' % (mine.key, post))

    # From related Chalk
    for s in result.related_chalk_scenarios:
        title = s.get('title', '')
        if title and len(title) > 10:
            items.append('Related [%s]: %s' % (s.get('_source_feature', '?'), title))

    return items
