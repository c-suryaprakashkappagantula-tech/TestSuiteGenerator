"""
chalk_parser.py -- Extract PI links from TMO Testing Scope page,
then extract feature-specific content from the selected PI page.
Pattern reused from tmo_features_master_extractor_v2.py.
"""
import re, time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Tuple
from .config import CHALK_BASE_URL, PAGE_LOAD_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS

TMO_TESTING_SCOPE_URL = 'https://chalk.charter.com/spaces/MDA/pages/3007682647/TMO+Testing+Scope'
PI_PAT = re.compile(r'PI[-\s]?(\d+)', re.IGNORECASE)


@dataclass
class PILink:
    label: str = ''      # e.g. "PI-53"
    url: str = ''
    number: int = 0      # e.g. 53


@dataclass
class ChalkScenario:
    scenario_id: str = ''
    title: str = ''
    prereq: str = ''
    cdr_input: str = ''
    derivation_rule: str = ''
    steps: List[str] = field(default_factory=list)
    variations: List[str] = field(default_factory=list)
    validation: str = ''
    category: str = ''


@dataclass
class ChalkData:
    feature_id: str = ''
    feature_title: str = ''
    scope: str = ''
    rules: str = ''
    scenarios: List[ChalkScenario] = field(default_factory=list)
    raw_text: str = ''
    tables: List[List[List[str]]] = field(default_factory=list)
    open_items: List[str] = field(default_factory=list)


def _wait(page, timeout=NETWORK_IDLE_TIMEOUT_MS):
    try: page.wait_for_load_state('networkidle', timeout=timeout)
    except: pass


# ============================================================
# STEP 1: Discover PI links from TMO Testing Scope page
# (Reused from tmo_features_master_extractor_v2.py)
# ============================================================

def discover_pi_links(page, log=print, pi_range=range(46, 60)) -> List[PILink]:
    """Navigate to TMO Testing Scope page and collect all PI links from sidebar."""
    log('[CHALK] Navigating to TMO Testing Scope page...')
    page.goto(TMO_TESTING_SCOPE_URL, wait_until='domcontentloaded', timeout=PAGE_LOAD_TIMEOUT_MS)
    _wait(page)
    time.sleep(5)
    log(f'[CHALK] Page loaded: {page.title()[:60]}')

    # Expand sidebar tree nodes (same logic as extractor_v2)
    log('[CHALK] Expanding sidebar tree...')
    for _ in range(3):
        n = page.evaluate("""() => {
            var c=0;
            document.querySelectorAll(
              '[data-testid="page-tree"] button[aria-expanded="false"],' +
              '.ia-splitter-left button[aria-expanded="false"],' +
              'nav button[aria-expanded="false"],' +
              '.plugin_pagetree_childtoggle_container a'
            ).forEach(function(a){try{a.click();c++}catch(e){}});
            return c;
        }""")
        if n:
            log(f'[CHALK]   Expanded {n} nodes')
            time.sleep(2)
        else:
            break
    time.sleep(1)

    # Collect PI links from sidebar
    log('[CHALK] Collecting PI links...')
    raw_links = page.evaluate("""() => {
        var r=[],s=new Set();
        document.querySelectorAll('a[href*="/spaces/MDA/pages/"]').forEach(function(a){
            var t=a.textContent.trim();
            if(/^PI[-\\s]?\\d+$/i.test(t) && !s.has(a.href)){
                s.add(a.href);r.push({text:t,href:a.href})
            }
        }); return r;
    }""")

    seen = set()
    pi_links = []
    for item in raw_links:
        m = PI_PAT.search(item['text'])
        if m:
            num = int(m.group(1))
            if num in pi_range and num not in seen:
                seen.add(num)
                href = item['href'] if item['href'].startswith('http') else CHALK_BASE_URL + item['href']
                pi_links.append(PILink(label=item['text'].strip(), url=href, number=num))

    pi_links.sort(key=lambda x: x.number)
    log(f'[CHALK] [OK] Found {len(pi_links)} PI links: {", ".join(p.label for p in pi_links)}')
    return pi_links


# ============================================================
# STEP 2: Navigate to a PI page and extract feature content
# ============================================================

EXTRACT_TEXT_JS = """() => {
    var root = document.querySelector(
        '.tabs-pane.active-pane, [role="tabpanel"][aria-hidden="false"]'
    );
    if (!root) root = document.querySelector('#main-content, .wiki-content, article');
    if (!root) root = document.body;
    return root.innerText;
}"""

EXTRACT_TABLES_JS = """() => {
    var root = document.querySelector(
        '.tabs-pane.active-pane, [role="tabpanel"][aria-hidden="false"]'
    );
    if (!root) root = document.querySelector('#main-content, .wiki-content, article');
    if (!root) root = document.body;
    var result = [];
    root.querySelectorAll('table').forEach(function(tbl) {
        if (tbl.closest('table') !== tbl) return;
        var rows = [];
        tbl.querySelectorAll('tr').forEach(function(tr) {
            var cells = [];
            tr.querySelectorAll('th,td').forEach(function(td) {
                cells.push(td.textContent.trim());
            });
            if (cells.length) rows.push(cells);
        });
        if (rows.length) result.push(rows);
    });
    return result;
}"""


def discover_features_on_pi(page, pi_url: str, log=print) -> List[Tuple[str, str]]:
    """Navigate to a PI page and extract all MWTGPROV-XXXX feature IDs with titles.
    Returns list of (feature_id, title) tuples."""
    log('[CHALK] Navigating to PI page (fast mode)...')
    page.goto(pi_url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until='commit')
    try: page.wait_for_load_state('domcontentloaded', timeout=15000)
    except: pass
    time.sleep(3)

    log('[CHALK] Scanning for features (single JS pass)...')
    features = page.evaluate("""() => {
        var root = document.querySelector('#main-content, .wiki-content, article');
        if (!root) root = document.body;
        var text = root.innerText;
        var lines = text.split('\\n');
        var result = [], seen = {};
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line) continue;
            var m = line.match(/MWTGPROV-\\d{4}/gi);
            if (!m) continue;
            for (var j = 0; j < m.length; j++) {
                var fid = m[j].toUpperCase();
                if (seen[fid]) continue;
                seen[fid] = true;
                var idx = line.toUpperCase().indexOf(fid);
                var title = line.substring(idx + fid.length).replace(/^[\\s\\-:]+/, '').trim();
                if (title.length < 5) title = line.replace(new RegExp(fid,'gi'), '').replace(/^[\\s\\-:]+/, '').trim();
                if (title.length > 100) title = title.substring(0, 100) + '...';
                result.push({id: fid, title: title});
            }
        }
        return result;
    }""")

    out = [(f['id'], f['title']) for f in features]
    out.sort(key=lambda x: int(x[0].split('-')[1]))
    log('[CHALK] [OK] Found %d features (fast scan)' % len(out))
    return out


def fetch_feature_from_pi(page, pi_url: str, feature_id: str, log=print) -> ChalkData:
    """Navigate to a PI page and extract content for a specific feature ID."""
    data = ChalkData(feature_id=feature_id)

    log(f'[CHALK] Navigating to PI page: {pi_url[:80]}...')
    page.goto(pi_url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until='domcontentloaded')
    _wait(page)
    time.sleep(8)  # Chalk pages are heavy

    # Extract full text
    log(f'[CHALK] Extracting page text...')
    try:
        data.raw_text = page.evaluate(EXTRACT_TEXT_JS)
    except Exception:
        try:
            data.raw_text = page.inner_text('#main-content', timeout=30000)
        except Exception:
            data.raw_text = page.inner_text('body', timeout=30000)

    # Extract tables
    log(f'[CHALK] Extracting tables...')
    try:
        data.tables = page.evaluate(EXTRACT_TABLES_JS)
        log(f'[CHALK]   Found {len(data.tables)} tables')
    except Exception as e:
        log(f'[CHALK]   [WARN] Table extraction failed: {e}')

    # Parse text to find feature section
    lines = [ln.strip() for ln in data.raw_text.split('\n') if ln.strip()]
    if not lines:
        log(f'[CHALK] [WARN] No content found on page')
        return data

    log(f'[CHALK] Total lines: {len(lines)}. Searching for {feature_id}...')

    fid = feature_id.upper()
    feature_start = -1
    feature_end = len(lines)

    for i, ln in enumerate(lines):
        if fid in ln.upper() and feature_start == -1:
            feature_start = i
            data.feature_title = ln.strip()
            continue
        if feature_start >= 0 and i > feature_start + 2:
            if re.search(r'MWTGPROV-\d{4}', ln) and fid not in ln.upper():
                feature_end = i
                break

    if feature_start == -1:
        log(f'[CHALK] [WARN] Feature {feature_id} not found on this page')
        return data

    feature_lines = lines[feature_start:feature_end]
    log(f'[CHALK] Found feature section: lines {feature_start}-{feature_end} ({len(feature_lines)} lines)')

    _parse_feature_section(feature_lines, data, feature_id, log)

    log(f'[CHALK] [OK] Extracted: {len(data.scenarios)} scenarios, {len(data.open_items)} open items')
    return data


# ============================================================
# STEP 3: Parse feature section into structured scenarios
# ============================================================

def _parse_feature_section(lines, data: ChalkData, feature_id: str, log=print):
    """Parse feature section lines into structured scenarios.
    Handles TWO formats:
      Format A: TS_MWTGPROV-XXXX_N (tab-separated scenario blocks)
      Format B: N\tScenario\tValidation (numbered tab rows)
    """
    fid = feature_id.upper()
    ts_pattern = re.compile(rf'TS_{fid}_(\d+)', re.IGNORECASE)
    # Format B: lines starting with a number then tab
    numbered_row_pat = re.compile(r'^(\d+)\t(.+)', re.DOTALL)

    # First pass: detect which format this feature uses
    has_ts_format = any(ts_pattern.search(ln) for ln in lines)
    has_numbered_format = any(numbered_row_pat.match(ln) for ln in lines)

    if has_ts_format:
        _parse_ts_format(lines, data, fid, ts_pattern, log)
    elif has_numbered_format:
        _parse_numbered_format(lines, data, fid, numbered_row_pat, log)
    else:
        # Fallback: try to extract any structured content
        _parse_freeform(lines, data, fid, log)

    # Post-process: fix weak validations
    _post_fix_validations(lines, data, fid)

    # Auto-detect categories
    for sc in data.scenarios:
        if not sc.category:
            t = sc.title.lower()
            if 'end-to-end' in t or 'e2e' in t:
                sc.category = 'E2E'
            elif 'not in scope' in t or 'untouched' in t:
                sc.category = 'Edge Case'
            elif 'fail' in t or 'reject' in t or 'invalid' in t or 'error' in t or 'timeout' in t:
                sc.category = 'Negative'
            elif 'rollback' in t or 'restore' in t:
                sc.category = 'Negative'
            else:
                sc.category = 'Happy Path'


def _parse_numbered_format(lines, data, fid, numbered_row_pat, log=print):
    """Parse Format B: N\tScenario\tValidation rows. Skips API mapping tables."""
    API_KEYWORDS = ['get', 'post', 'put', 'delete', 'sync', 'async', 'inbound', 'outbound']
    current_scenario = None
    current_section = ''

    for ln in lines:
        ln_upper = ln.upper().strip()

        # Scope / Rules
        if ln_upper.startswith('SCOPE') or ln_upper.startswith('FEATURE:') or ln_upper.startswith('SUMMARY'):
            data.scope += ln + '\n'; continue
        if ln_upper.startswith('RULES:'):
            data.rules += ln + '\n'; continue

        # Skip header row
        if ln_upper.startswith('SNO') or ln_upper.startswith('SCENARIO NUMBER'):
            continue

        # Numbered row: N\tScenario\tValidation
        m = numbered_row_pat.match(ln)
        if m:
            num = m.group(1)
            rest = m.group(2)
            parts = [p.strip() for p in rest.split('\t')]

            # Skip API mapping tables (have HTTP methods or direction keywords in 3rd+ column)
            if len(parts) >= 2:
                third = parts[1].lower() if len(parts) > 1 else ''
                if any(kw == third for kw in API_KEYWORDS):
                    continue  # skip API row

            # Save previous scenario
            if current_scenario:
                data.scenarios.append(current_scenario)

            title = parts[0] if parts else rest
            validation = parts[1] if len(parts) > 1 else ''

            current_scenario = ChalkScenario(
                scenario_id='TS_%s_%s' % (fid, num),
                title=title,
                validation=validation,
            )
            current_section = 'scenario'
            continue

        # Continuation lines (validation text that wraps to next line)
        if current_scenario and current_section == 'scenario' and ln.strip():
            # If line doesn't start with a number+tab, it's continuation of previous
            if not numbered_row_pat.match(ln) and not ln_upper.startswith('SNO'):
                if current_scenario.validation:
                    current_scenario.validation += ' ' + ln.strip()
                else:
                    current_scenario.validation = ln.strip()

    # Last scenario
    if current_scenario:
        data.scenarios.append(current_scenario)

    log('[CHALK]   Parsed %d scenarios (numbered format)' % len(data.scenarios))


def _parse_freeform(lines, data, fid, log=print):
    """Fallback: extract Verify lines and other structured content as scenarios."""
    scenario_starters = ['verify ', 'validate ', 'check ', 'ensure ', 'test ']
    idx = 1
    current_scenario = None

    for ln in lines:
        ln_stripped = ln.strip()
        ln_low = ln_stripped.lower()

        # Scope/Summary extraction
        if ln_low.startswith('summary:') or ln_low.startswith('scope:'):
            data.scope += ln_stripped + '\n'
            continue

        # New scenario: line starts with a scenario keyword and is long enough
        if any(ln_low.startswith(kw) for kw in scenario_starters) and len(ln_stripped) > 15:
            # Save previous
            if current_scenario:
                data.scenarios.append(current_scenario)
            current_scenario = ChalkScenario(
                scenario_id='TS_%s_%d' % (fid, idx),
                title=ln_stripped,
                validation=ln_stripped,
            )
            idx += 1
            continue

        # Continuation: if current scenario exists and line is a sub-validation
        if current_scenario and ln_stripped and not any(ln_low.startswith(kw) for kw in scenario_starters):
            # Lines that look like sub-validations (short, start with Verify, or indented)
            if ln_low.startswith('verify ') and len(ln_stripped) < 100:
                # Sub-validation — append to current scenario's validation
                current_scenario.validation += '; ' + ln_stripped
            elif len(ln_stripped) > 10 and not ln_low.startswith(('summary', 'scope', 'description', 'feature')):
                # Could be description text — add to scope if no scenario yet
                if not data.scenarios and idx == 1:
                    data.scope += ln_stripped + '\n'

    if current_scenario:
        data.scenarios.append(current_scenario)

    log('[CHALK]   Parsed %d scenarios (freeform/verify-list)' % len(data.scenarios))

def _parse_ts_format(lines, data, fid, ts_pattern, log=print):
    """Parse Format A: TS_MWTGPROV-XXXX_N scenario blocks."""
    current_scenario = None
    current_section = ''


    for ln in lines:
        ln_upper = ln.upper().strip()

        # Scope / Rules
        if ln_upper.startswith('SCOPE') or ln_upper.startswith('FEATURE:'):
            data.scope += ln + '\n'; continue
        if ln_upper.startswith('RULES:'):
            data.rules += ln + '\n'; continue

        # Open items
        if 'OPEN ITEM' in ln_upper:
            current_section = 'open_items'; continue
        if current_section == 'open_items' and ln.strip():
            if ts_pattern.search(ln):
                current_section = ''
            else:
                data.open_items.append(ln.strip()); continue

        # New scenario
        m = ts_pattern.search(ln)
        if m:
            if current_scenario:
                data.scenarios.append(current_scenario)
            current_scenario = ChalkScenario(scenario_id=f'TS_{fid}_{m.group(1)}')
            parts = ln.split('\t')
            if len(parts) >= 2:
                current_scenario.title = parts[1].strip()
            else:
                current_scenario.title = ln.replace(current_scenario.scenario_id, '').strip()
            current_section = 'scenario'

            # Check for validation and category in tab-separated parts
            for p in parts:
                p_low = p.strip().lower()
                if any(cat in p_low for cat in ['happy path', 'edge case', 'negative', 'e2e', 'workflow']):
                    current_scenario.category = p.strip()
                elif len(p.strip()) > 30 and any(kw in p_low for kw in ['prr output', 'verify', 'should', 'correctly', 'ensure', 'treated']):
                    current_scenario.validation = p.strip()
            continue

        if not current_scenario:
            continue

        # Parse scenario content
        ln_low = ln.lower().strip()

        if ln_low.startswith('pre-req:') or ln_low.startswith('pre-condition:'):
            current_scenario.prereq = ln.strip(); current_section = 'prereq'
        elif ln_low.startswith('cdr input:'):
            current_scenario.cdr_input = ln.strip(); current_section = 'cdr'
        elif ln_low.startswith('derivation rule:'):
            current_scenario.derivation_rule = ln.strip(); current_section = 'derivation'
        elif ln_low.startswith('step ') and ':' in ln:
            current_scenario.steps.append(ln.strip()); current_section = 'steps'
        elif ln_low.startswith('variation') or ln.startswith(chr(8226)):
            current_scenario.variations.append(ln.strip()); current_section = 'variations'
        elif ln.startswith(chr(8226)) or ln.startswith('- '):
            current_scenario.variations.append(ln.strip())
        elif current_section == 'prereq' and not any(ln_low.startswith(p) for p in ['cdr', 'derivation', 'step', 'variation', 'note']):
            current_scenario.prereq += '\n' + ln.strip()
        elif current_section == 'cdr' and not any(ln_low.startswith(p) for p in ['derivation', 'step', 'variation', 'note', 'pre-']):
            current_scenario.cdr_input += '\n' + ln.strip()
        elif current_section == 'derivation' and not any(ln_low.startswith(p) for p in ['step', 'variation', 'note', 'pre-', 'cdr']):
            current_scenario.derivation_rule += '\n' + ln.strip()

        # Check for validation/category in tab-separated content
        # ALWAYS overwrite validation with richer text (PRR output > title repeat)
        if '\t' in ln:
            parts = [p.strip() for p in ln.split('\t') if p.strip()]
            for p in parts:
                p_low = p.lower()
                if any(cat in p_low for cat in ['happy path', 'edge case', 'negative', 'e2e', 'workflow']):
                    current_scenario.category = p.strip()
                elif len(p) > 30 and any(kw in p_low for kw in ['prr output', 'from_country', 'to_country',
                        'call_type', 'treated as domestic', 'billing impact', 'correctly identifies',
                        'enriched', 'complete e2e', 'e2e roaming', 'cdr collected', 'distributed to amdocs',
                        'passes through', 'without country', 'file arrives within']):
                    # Always take the richer validation (overwrite title-repeat)
                    current_scenario.validation = p.strip()

    # Last scenario
    if current_scenario:
        data.scenarios.append(current_scenario)

    log('[CHALK]   Parsed %d scenarios (TS format)' % len(data.scenarios))


def _post_fix_validations(lines, data: ChalkData, fid: str):
    """Post-process: fix scenarios where validation is just a title repeat.
    Scan all lines for rich validation text (PRR output, E2E flow, etc.)
    and assign to the correct scenario."""
    RICH_KEYWORDS = ['prr output:', 'from_country=', 'to_country_code=', 'call_type=',
                     'treated as domestic', 'billing impact', 'correctly identifies',
                     'complete e2e', 'e2e roaming', 'cdr collected', 'passes through',
                     'without country code', 'file arrives within', 'amdocs receives']

    ts_pattern = re.compile(rf'TS_{fid}_(\d+)', re.IGNORECASE)

    # Build a map: scenario_index -> all lines belonging to it
    scenario_lines = {}
    current_idx = -1
    for ln in lines:
        m = ts_pattern.search(ln)
        if m:
            current_idx = int(m.group(1)) - 1  # 0-based
            scenario_lines[current_idx] = []
            continue
        if current_idx >= 0:
            scenario_lines.setdefault(current_idx, []).append(ln)

    # For each scenario with weak validation, search its lines for rich text
    for idx, sc in enumerate(data.scenarios):
        # Check if current validation is weak (just title repeat or step text)
        is_weak = (not sc.validation or
                   sc.validation == sc.title or
                   sc.validation.startswith('Verify ') and 'from_country' not in sc.validation or
                   sc.validation.startswith('Step '))

        if not is_weak:
            continue

        # Search this scenario's lines for rich validation text
        sc_lines = scenario_lines.get(idx, [])
        for ln in sc_lines:
            # Check tab-separated parts
            if '\t' in ln:
                parts = [p.strip() for p in ln.split('\t') if p.strip()]
                for p in parts:
                    p_low = p.lower()
                    if p.startswith('Step '):  # skip step text
                        continue
                    if len(p) > 25 and any(kw in p_low for kw in RICH_KEYWORDS):
                        sc.validation = p
                        break
            # Check standalone line
            else:
                ln_low = ln.lower()
                if len(ln) > 25 and any(kw in ln_low for kw in RICH_KEYWORDS):
                    sc.validation = ln.strip()
            if sc.validation != sc.title and not sc.validation.startswith('Step '):
                break  # found a good one
