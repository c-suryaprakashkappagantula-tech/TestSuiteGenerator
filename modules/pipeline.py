"""
pipeline.py — Block-based execution pipeline with self-heal retry.
Each block runs independently, retries on failure, and stores results
in session state so successful blocks don't re-run on resume.

Blocks:
  1. Jira Fetch (+ subtasks + attachments download)
  2. Chalk DB Cache Lookup
  3. Chalk Live Page Fetch (only if Block 2 misses)
  3b. Deep Mine (crawl Chalk URLs, mine subtasks, find related features)
  4. Document Parsing (uploads + attachments)
  5. Test Engine (build suite)
  6. Excel Generation + DB Save
"""
import time
import traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds


def _record_degraded(pass_name: str, exc: BaseException = None) -> None:
    """Note an output-block pass that raised and was skipped.

    Sits beside the existing log line; the handler still continues exactly as before
    (tsg-tse-hardening Req 5.5).
    """
    try:
        from .degraded_tracker import record
        record(pass_name, exc)
    except Exception:
        pass


def _attach_degraded(suite, log=print) -> int:
    """Refresh the degraded-run warning on the suite. Returns the count."""
    try:
        from .degraded_tracker import attach_to_suite, summary_line
        n = attach_to_suite(suite)
        if n:
            log('[PIPELINE] %s' % summary_line())
        return n
    except Exception:
        return 0

# ── MCP-only hardening (FEAT-002): the Chalk/Jira auto-spawn browser fallback
# (_auto_spawn_browser / cleanup_auto_browser) has been removed. Chalk and Jira are
# MCP-only; when REST(MCP) is unavailable and no usable cache exists, the pipeline
# hard-fails via mcp_unavailable_alert / McpUnavailable instead of launching a browser.


@dataclass
class BlockResult:
    """Result of a pipeline block execution."""
    name: str = ''
    success: bool = False
    data: Any = None
    error: str = ''
    attempts: int = 0
    duration: float = 0.0


class PipelineError(Exception):
    """Raised when a block fails after all retries."""
    def __init__(self, block_name, error_msg, attempts):
        self.block_name = block_name
        self.error_msg = error_msg
        self.attempts = attempts
        super().__init__(
            'Pipeline block "%s" failed after %d attempts.\n'
            'Please Contact Dashboard Admin with below error message:\n'
            '  Block: %s\n'
            '  Error: %s' % (block_name, attempts, block_name, error_msg))


def run_block(name: str, func: Callable, log=print, max_retries=MAX_RETRIES) -> BlockResult:
    """Run a pipeline block with retry logic.
    func() should return the block's output data.
    On success: returns BlockResult with success=True and data.
    On failure after retries: raises PipelineError."""
    result = BlockResult(name=name)

    for attempt in range(1, max_retries + 1):
        result.attempts = attempt
        t0 = time.time()
        try:
            log('[PIPELINE] Block "%s" — attempt %d/%d' % (name, attempt, max_retries))
            data = func()
            result.duration = time.time() - t0
            result.success = True
            result.data = data
            log('[PIPELINE] Block "%s" — OK (%.1fs)' % (name, result.duration))
            return result
        except Exception as e:
            result.duration = time.time() - t0
            result.error = str(e)
            log('[PIPELINE] Block "%s" — FAILED attempt %d: %s' % (name, attempt, str(e)[:100]))
            if attempt < max_retries:
                log('[PIPELINE] Retrying in %ds...' % RETRY_DELAY)
                time.sleep(RETRY_DELAY)

    # All retries exhausted
    raise PipelineError(name, result.error, result.attempts)


class Pipeline:
    """Orchestrates block-based execution with self-heal retry."""

    def __init__(self, log=print):
        self.log = log
        self.results: Dict[str, BlockResult] = {}
        self.timings: List[tuple] = []

    def run(self, name: str, func: Callable, skip_if_cached=False, cache_key=None) -> Any:
        """Run a named block. Returns the block's output data.
        If skip_if_cached=True and cache_key has data in session, skip execution."""
        if skip_if_cached and cache_key:
            # Check if we already have this data from a previous run
            if name in self.results and self.results[name].success:
                self.log('[PIPELINE] Block "%s" — using cached result' % name)
                return self.results[name].data

        result = run_block(name, func, self.log)
        self.results[name] = result
        self.timings.append((name, result.duration))
        return result.data

    def get_timing_summary(self) -> List[tuple]:
        """Return list of (block_name, duration_secs)."""
        return list(self.timings)

    def get_total_duration(self) -> float:
        return sum(d for _, d in self.timings)

    def get_summary(self) -> str:
        """Return a human-readable summary of all blocks."""
        lines = []
        for name, dur in self.timings:
            result = self.results.get(name)
            status = 'OK' if result and result.success else 'FAILED'
            attempts = result.attempts if result else 0
            lines.append('  %s: %.1fs (%s, %d attempt%s)' % (
                name, dur, status, attempts, 's' if attempts > 1 else ''))
        return '\n'.join(lines)


# ================================================================
# PRE-BUILT BLOCK FUNCTIONS
# ================================================================

def block_jira_fetch(page, feature_id, log=print):
    """Block 1: Fetch Jira issue — DB cache first, browser fallback.

    V7: Checks the local DB cache first. If the Jira data is cached and
    not stale (< 24h), uses it directly — NO browser needed.
    Falls back to live Jira fetch via browser only when cache is missing or stale.
    """
    from .jira_fetcher import fetch_jira_issue, validate_jira_issue, download_attachments, JiraIssue, JiraAttachment
    from .database import save_jira, load_jira, is_jira_stale
    # MCP gateway may return long text fields as {'text', 'truncated', ...} dicts.
    from .text_utils import normalize_jira_issue as _normalize_jira_issue
    import json

    # ── V7: Try DB cache first ──
    # Path A: Fresh cache hit → use directly (no browser needed)
    # When page=None (REST-only mode), also accept stale cache to avoid crashing
    _cache_check = not is_jira_stale(feature_id) or (page is None)
    if _cache_check:
        cached = load_jira(feature_id)
        if cached and cached.get('summary'):
            log('[PIPELINE] ⚡ Jira DB cache hit for %s (skipping browser)' % feature_id)
            # Reconstruct JiraIssue from cached dict
            jira = JiraIssue(
                key=cached.get('feature_id', feature_id),
                summary=cached.get('summary', ''),
                description=cached.get('description', ''),
                status=cached.get('status', ''),
                priority=cached.get('priority', ''),
                assignee=cached.get('assignee', ''),
                reporter=cached.get('reporter', ''),
                labels=json.loads(cached.get('labels_json', '[]')),
                acceptance_criteria=cached.get('ac_text', ''),
                attachments=[JiraAttachment(filename=a.get('filename', ''), url=a.get('url', ''), size=a.get('size', 0))
                             for a in json.loads(cached.get('attachments_json', '[]'))],
                linked_issues=json.loads(cached.get('links_json', '[]')),
                subtasks=json.loads(cached.get('subtasks_json', '[]')),
                comments=json.loads(cached.get('comments_json', '[]')),
                pi=cached.get('pi', ''),
                channel=cached.get('channel', ''),
                raw_json=json.loads(cached.get('raw_json', '{}')) if cached.get('raw_json') else {},
            )
            warnings = validate_jira_issue(_normalize_jira_issue(jira, log=log), log=log)
            log('[PIPELINE]   %s | %s | %d subtasks | %d comments | AC=%s' % (
                jira.key, jira.summary[:50], len(jira.subtasks), len(jira.comments), bool(jira.acceptance_criteria)))

            # Attachments: check if already downloaded locally
            att_paths = []
            from .config import ATTACHMENTS
            from pathlib import Path
            for att in jira.attachments:
                local = ATTACHMENTS / ('%s_%s' % (feature_id, att.filename))
                if local.exists():
                    att_paths.append(local)

            # Subtask attachments: check if already downloaded locally
            for st in jira.subtasks:
                st_attachments = st.get('attachments', [])
                st_key = st.get('key', 'unknown')
                for att in st_attachments:
                    fname = att.get('filename', '')
                    if not fname:
                        continue
                    ext = Path(fname).suffix.lower()
                    if ext not in ('.docx', '.xlsx', '.pdf', '.txt', '.html', '.zip', '.csv'):
                        continue
                    local = ATTACHMENTS / ('%s_%s' % (st_key, fname))
                    if local.exists():
                        att_paths.append(local)
                    elif page and att.get('url'):
                        # Download if not cached locally but URL available
                        try:
                            response = page.request.get(att['url'])
                            if response.ok:
                                local.write_bytes(response.body())
                                att_paths.append(local)
                                log('[PIPELINE]   Subtask %s: downloaded %s' % (st_key, fname))
                        except Exception:
                            pass  # Skip if download fails

            if att_paths:
                log('[PIPELINE]   %d attachments found/downloaded (parent + subtask)' % len(att_paths))

            # Apply post-fetch enrichment even on cache hit (AC-from-description, comment rules, noise filter)
            from .jira_fetcher import enrich_issue_post_fetch
            enrich_issue_post_fetch(jira, log=log)

            return {'jira': jira, 'warnings': warnings, 'att_paths': att_paths}

    # ── Path B: REST succeeds → use and cache ──
    log('[PIPELINE] Jira DB cache miss or stale for %s — trying REST...' % feature_id)

    # Try REST API first (no browser needed)
    from .jira_fetcher import fetch_jira_issue_rest, download_attachments_rest
    jira = fetch_jira_issue_rest(feature_id, log=log)

    if jira and jira.summary:
        log('[PIPELINE] ✅ Jira fetched via REST for %s' % feature_id)
        warnings = validate_jira_issue(_normalize_jira_issue(jira, log=log), log=log)
        try:
            save_jira(jira)
            log('[PIPELINE] Jira data saved to DB for %s' % feature_id)
        except Exception as e:
            log('[PIPELINE] Jira DB save warning: %s' % str(e)[:60])

        # Download attachments via REST
        att_paths = []
        if jira.attachments:
            att_paths = download_attachments_rest(jira, log=log)
            log('[PIPELINE] Downloaded %d attachments (REST)' % len(att_paths))

        # Download subtask attachments via REST
        if jira.subtasks:
            from .config import ATTACHMENTS
            from pathlib import Path
            from rest_clients import JiraRestClient
            _jira_client = JiraRestClient(logger_fn=lambda m: None)
            subtask_att_count = 0
            for st in jira.subtasks:
                st_attachments = st.get('attachments', [])
                if not st_attachments:
                    continue
                st_key = st.get('key', 'unknown')
                for att in st_attachments:
                    fname = att.get('filename', '')
                    url = att.get('url', '')
                    if not fname or not url:
                        continue
                    size = att.get('size', 0)
                    if size > 10 * 1024 * 1024:
                        continue
                    ext = Path(fname).suffix.lower()
                    if ext not in ('.docx', '.xlsx', '.pdf', '.txt', '.html', '.zip', '.csv'):
                        continue
                    save_path = ATTACHMENTS / ('%s_%s' % (st_key, fname))
                    if save_path.exists():
                        att_paths.append(save_path)
                        subtask_att_count += 1
                        continue
                    if _jira_client.download_attachment(url, save_path):
                        att_paths.append(save_path)
                        subtask_att_count += 1
                        log('[PIPELINE]   Subtask %s: downloaded %s (REST)' % (st_key, fname))
            if subtask_att_count:
                log('[PIPELINE] Downloaded %d subtask attachments (REST)' % subtask_att_count)

        return {'jira': jira, 'warnings': warnings, 'att_paths': att_paths}

    # ── Path C: REST fails — try stale local DB cache (local-only read, no network) ──
    log('[PIPELINE] REST(MCP) failed for %s — attempting stale cache load' % feature_id)
    stale_cached = load_jira(feature_id)
    if stale_cached and stale_cached.get('summary'):
        import json as _json
        log('[PIPELINE] ⚡ Using stale cache for %s (REST/MCP unavailable)' % feature_id)
        jira = JiraIssue(
            key=stale_cached.get('feature_id', feature_id),
            summary=stale_cached.get('summary', ''),
            description=stale_cached.get('description', ''),
            status=stale_cached.get('status', ''),
            priority=stale_cached.get('priority', ''),
            assignee=stale_cached.get('assignee', ''),
            reporter=stale_cached.get('reporter', ''),
            labels=_json.loads(stale_cached.get('labels_json', '[]')),
            acceptance_criteria=stale_cached.get('ac_text', ''),
            attachments=[],
            linked_issues=_json.loads(stale_cached.get('links_json', '[]')),
            subtasks=_json.loads(stale_cached.get('subtasks_json', '[]')),
            comments=_json.loads(stale_cached.get('comments_json', '[]')),
            pi=stale_cached.get('pi', ''),
            channel=stale_cached.get('channel', ''),
        )
        warnings = validate_jira_issue(_normalize_jira_issue(jira, log=log), log=log)
        # Apply post-fetch enrichment even on stale cache (AC-from-description, etc.)
        from .jira_fetcher import enrich_issue_post_fetch
        enrich_issue_post_fetch(jira, log=log)
        return {'jira': jira, 'warnings': warnings + ['⚠️ Using stale Jira cache — run Sync from Jira to refresh'], 'att_paths': []}

    # ── MCP-only hard-fail: REST(MCP) down and no usable cache. Jira is MCP-only;
    # there is NO browser/Playwright fallback. Alert loudly and raise. ──
    from shared.mcp_gateway.alerts import mcp_unavailable_alert, McpUnavailable
    mcp_unavailable_alert('Jira', log=log)
    raise McpUnavailable(
        'Jira data unavailable for %s: MCP gateway is down and no cache exists. '
        'Jira access is MCP-only — check `python -m shared.mcp_gateway status` and retry.' % feature_id
    )


def block_chalk_db(feature_id, pi_label, log=print):
    """Block 2: load Chalk data from DB cache.

    Legacy mode preserves the selected-PI-first behavior. Contract-v1 mode merges all
    available PIs for the SAME feature (selected PI first), preserving scenario ownership
    and preventing thin current-PI data from triggering unrelated cross-feature retrieval.
    """
    from .database import load_chalk_as_object, _conn
    try:
        from .contract_bridge import contract_enabled
        _contract_v1 = contract_enabled()
    except Exception:
        _contract_v1 = False

    # Legacy behavior remains untouched until contract-v1 canaries pass.
    if not _contract_v1:
        c = _conn()
        _row_exists = c.execute(
            'SELECT pi_label FROM chalk_cache WHERE feature_id=? AND pi_label=?',
            (feature_id, pi_label)).fetchone()
        c.close()
        chalk = load_chalk_as_object(feature_id, pi_label)
        if chalk and chalk.scenarios:
            log('[PIPELINE] Chalk DB hit (%s): %d scenarios' % (pi_label, len(chalk.scenarios)))
            return {'chalk': chalk, 'source': 'DB cache (%s)' % pi_label}
        if _row_exists and not chalk:
            # Row was in the DB for this exact PI but rejected by the cache-poisoning guard.
            # Fall through is LOUD — wrong TCs are worse than no TCs.
            log('[PIPELINE] *** CHALK POISONED: %s/%s row exists in DB but was rejected '
                '(feature id absent from raw_text — row holds a different feature\'s content). '
                'Proceeding Jira-only will produce INCORRECT test cases. '
                'Fix: re-crawl %s from its correct PI page. ***' % (feature_id, pi_label, feature_id))
            return {'chalk': None, 'source': 'POISONED (%s) — Jira-only; TCs may be wrong' % pi_label,
                    'poisoned': True}
        c = _conn()
        row = c.execute(
            'SELECT pi_label FROM chalk_cache WHERE feature_id=? '
            'AND scenarios_json != "[]" LIMIT 1', (feature_id,)).fetchone()
        c.close()
        if row:
            chalk = load_chalk_as_object(feature_id, row['pi_label'])
            if chalk and chalk.scenarios:
                log('[PIPELINE] Chalk DB hit (%s): %d scenarios' %
                    (row['pi_label'], len(chalk.scenarios)))
                return {'chalk': chalk, 'source': 'DB cache (%s)' % row['pi_label']}
            if not chalk:
                log('[PIPELINE] *** CHALK POISONED (fallback PI %s): %s row also rejected. '
                    'Re-crawl %s from its correct PI page. ***'
                    % (row['pi_label'], feature_id, feature_id))
                return {'chalk': None, 'source': 'POISONED (fallback %s) — Jira-only' % row['pi_label'],
                        'poisoned': True}
        log('[PIPELINE] Chalk DB miss for %s' % feature_id)
        return {'chalk': None, 'source': 'not in DB'}

    # Contract-v1: selected PI first, then all other PIs for this exact feature.
    from .chalk_parser import ChalkData
    c = _conn()
    rows = c.execute(
        'SELECT pi_label FROM chalk_cache WHERE feature_id=? '
        'AND scenarios_json != "[]"', (feature_id,)).fetchall()
    c.close()
    labels = [str(r['pi_label']) for r in rows if r and r['pi_label']]
    labels = ([pi_label] if pi_label in labels else []) + sorted(
        [p for p in labels if p != pi_label], reverse=True)
    if not labels:
        log('[PIPELINE] Chalk DB miss for %s' % feature_id)
        return {'chalk': None, 'source': 'not in DB'}

    merged = ChalkData(feature_id=feature_id)
    seen = set()
    source_parts = []
    # Every label here came from a row whose scenarios_json is non-empty, so a None
    # result means the cache-poisoning guard rejected it - not that the row is absent.
    # Keep the two apart: a rejected row must never fall through silently.
    rejected = []
    emptied = []
    for label in labels:
        item = load_chalk_as_object(feature_id, label)
        if item is None:
            rejected.append(label)
            continue
        if not item.scenarios:
            emptied.append(label)
            continue
        source_parts.append('%s:%d' % (label, len(item.scenarios)))
        if not merged.feature_title:
            merged.feature_title = item.feature_title
        if item.scope:
            merged.scope += ('\n' if merged.scope else '') + '[%s] %s' % (label, item.scope)
        if item.rules:
            merged.rules += ('\n' if merged.rules else '') + '[%s] %s' % (label, item.rules)
        if item.raw_text:
            merged.raw_text += ('\n' if merged.raw_text else '') + '[%s]\n%s' % (label, item.raw_text)
        merged.tables.extend(item.tables or [])
        merged.open_items.extend(item.open_items or [])
        for scenario in item.scenarios:
            key = ' '.join((scenario.title or '').lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            scenario.owner_feature_id = feature_id
            scenario.owner_pi = label
            scenario.relationship = 'owned' if label == pi_label else 'same_feature_other_pi'
            merged.scenarios.append(scenario)

    if not merged.scenarios:
        if rejected:
            # Wrong TCs are worse than no TCs: say so plainly rather than degrading
            # to Jira-only generation behind a bland "miss" line.
            log('[PIPELINE] *** CHALK POISONED: %s has %d cached row(s) (%s) and every '
                'one was rejected - each holds a different feature\'s content (its own '
                'id never appears in raw_text). Proceeding Jira-only will produce '
                'INCORRECT test cases. Fix: re-crawl %s from its correct PI page. ***'
                % (feature_id, len(rejected), ', '.join(rejected), feature_id))
            return {'chalk': None,
                    'source': 'POISONED (%s) - Jira-only; TCs may be wrong'
                              % ', '.join(rejected),
                    'poisoned': True, 'poisoned_labels': list(rejected)}
        log('[PIPELINE] Chalk DB miss for %s' % feature_id)
        return {'chalk': None, 'source': 'not in DB',
                'empty_labels': list(emptied)}
    if rejected:
        log('[PIPELINE] WARNING: %s - %d cached Chalk row(s) rejected as poisoned (%s); '
            'suite built from the %d scenario(s) that survived. Coverage is PARTIAL.'
            % (feature_id, len(rejected), ', '.join(rejected), len(merged.scenarios)))
    log('[PIPELINE] Contract-v1 Chalk merge for %s: %d owned scenarios (%s)' %
        (feature_id, len(merged.scenarios), ', '.join(source_parts)))
    return {'chalk': merged, 'source': 'DB cache merged (%s)' % ', '.join(labels),
            'poisoned': bool(rejected), 'poisoned_labels': list(rejected)}


def _persist_and_normalize(feature_id, pi_label, chalk, source, log):
    """Save a freshly crawled row, then RE-READ it through load_chalk_as_object so the
    live path gets the exact same treatment as the cached path: fragmented step-less
    scenarios are collapsed (a browser crawl of a big page flattens ~10 real scenarios
    into 80+ step lines), and the poison guard runs. Without this the engine received the
    raw pre-collapse crawl (e.g. 4515: 82 fragment 'scenarios' -> 85 TCs, 54 of them
    'Verify_Step_1:' fragments) even though a later cache read would have collapsed it.
    Returns a block result dict."""
    from .database import save_chalk, load_chalk_as_object
    save_chalk(feature_id, pi_label, chalk)
    normalized = load_chalk_as_object(feature_id, pi_label)
    if normalized is None:
        # save_chalk refused (poison) or the re-read rejected it.
        log('[PIPELINE] *** CHALK POISONED (live %s): crawled row for %s rejected on '
            're-read. Not using it. ***' % (pi_label, feature_id))
        return {'chalk': None, 'source': 'POISONED (live %s)' % pi_label, 'poisoned': True}
    log('[PIPELINE] Chalk %s (%s): %d scenarios (normalized from %d crawled)'
        % (source, pi_label, len(normalized.scenarios), len(chalk.scenarios)))
    return {'chalk': normalized, 'source': '%s (%s)' % (pi_label, source)}


def block_chalk_live(page, feature_id, pi_url, pi_label, pi_list, log=print):
    """Block 3: Fetch Chalk data from live page (fallback when DB misses).
    V8.1: REST-first — tries REST API before browser scraping."""
    # MCP-only (FEAT-002): only the REST(MCP) fetch is used; the browser scraper
    # fetch_feature_from_pi is no longer imported — there is no browser fallback.
    from .chalk_parser import fetch_feature_rest, ChalkData
    from .database import save_chalk
    import re as _re_chalk

    def _page_id_from_url(url):
        m = _re_chalk.search(r'/pages/(\d+)/', url)
        return m.group(1) if m else ''

    # ── REST-FIRST PATH ──
    _pid = _page_id_from_url(pi_url)
    if _pid:
        chalk = fetch_feature_rest(_pid, feature_id, log=log)
        if chalk and chalk.scenarios:
            return _persist_and_normalize(feature_id, pi_label, chalk, 'REST', log)

        # REST didn't find it on selected PI — scan all PIs via REST
        for sl, su in pi_list:
            if sl == pi_label:
                continue
            _spid = _page_id_from_url(su)
            if not _spid:
                continue
            try:
                sc = fetch_feature_rest(_spid, feature_id, log=lambda m: None)
                if sc and sc.scenarios:
                    return _persist_and_normalize(feature_id, sl, sc, 'REST scanned', log)
            except:
                pass

    # ── MCP-only hard-fail: REST(MCP) could not find the feature on the selected PI
    # or any scanned PI. Chalk is MCP-only — there is NO browser/Playwright fallback.
    # Alert loudly and raise rather than returning a soft 'not found'. ──
    from shared.mcp_gateway.alerts import mcp_unavailable_alert, McpUnavailable
    mcp_unavailable_alert('Chalk', log=log)
    raise McpUnavailable(
        'Chalk data unavailable for %s: MCP gateway REST returned nothing on the selected '
        'PI or any scanned PI. Chalk access is MCP-only — check '
        '`python -m shared.mcp_gateway status` and retry.' % feature_id
    )


def block_parse_docs(att_paths, uploaded_files, inputs_dir, log=print):
    """Block 4: Parse all documents (attachments + uploads)."""
    from .doc_parser import parse_file

    parsed = []
    for ap in att_paths:
        parsed.append(parse_file(ap, log=log, source='Jira Attachment'))

    if uploaded_files:
        for uf in uploaded_files:
            save_path = inputs_dir / uf.name
            save_path.write_bytes(uf.getvalue())
            parsed.append(parse_file(save_path, log=log, source='Upload'))

    log('[PIPELINE] Parsed %d documents' % len(parsed))
    return parsed


def block_deep_mine(jira, chalk, page=None, log=print):
    """Block 3b: Deep mine all data sources — Chalk URLs, subtasks, related features.
    This is the exhaustive data gathering step that ensures no information is missed."""
    from .deep_miner import deep_mine

    result = deep_mine(jira, chalk, page=page, log=log)
    log('[PIPELINE] Deep mine complete: %d API specs | %d subtask mines | %d related | %d total items' % (
        len(result.api_specs), len(result.subtask_mines),
        len(result.related_chalk_scenarios), len(result.all_testable_items)))
    return {'deep_mine_result': result}


def block_build_suite(jira, chalk, parsed_docs, options, log=print, deep_mine_result=None):
    """Block 5: Build the test suite (V7 — template-based)."""
    from .test_engine import build_test_suite

    suite = build_test_suite(jira, chalk, parsed_docs, options, log=log, deep_mine_result=deep_mine_result)
    total_steps = sum(len(tc.steps) for tc in suite.test_cases)
    log('[PIPELINE] Suite built: %d TCs | %d steps' % (len(suite.test_cases), total_steps))
    return {'suite': suite, 'total_steps': total_steps}


def block_build_suite_v8(jira, chalk, parsed_docs, options, deep_mine_result=None, log=print):
    """Block 5 (V8): Build the test suite using Data-First Engine.

    Selects V8.0 engine when options.get('engine_version') == '8'.
    Reports timing for each sub-step within the Build block.
    """
    import time as _time
    from .data_first_engine import build_test_suite_v8
    from .database import save_traceability_log, save_data_inventory_log

    log('[PIPELINE] V8.0 Data-First Engine selected')

    _t0 = _time.time()
    suite = build_test_suite_v8(
        jira=jira,
        chalk=chalk,
        parsed_docs=parsed_docs,
        options=options,
        deep_mine_result=deep_mine_result,
        log=log,
    )
    _duration = _time.time() - _t0

    total_steps = sum(len(tc.steps) for tc in suite.test_cases)
    log('[PIPELINE] V8 Suite built: %d TCs | %d steps | %.1fs' % (
        len(suite.test_cases), total_steps, _duration))

    # A rejected Chalk row has to be visible in the workbook, not just the console -
    # the Data Sources sheet is what a reviewer actually reads. Stamped here because
    # every engine path (CR delegation, data-alignment, main planning) returns through
    # this block, and the CR path builds an empty inventory of its own.
    try:
        from .database import get_chalk_rejections
        _rej = get_chalk_rejections(getattr(jira, 'key', '') or '')
        if _rej and suite is not None and getattr(suite, 'data_inventory', None) is not None:
            from .data_models_v8 import DataSourceEntry
            for (_fid, _pi), _reason in sorted(_rej.items()):
                suite.data_inventory.sources.append(DataSourceEntry(
                    source_name='Chalk %s (REJECTED)' % _pi,
                    source_type='chalk',
                    items_extracted=0,
                    items_detail=[_reason],
                    status='failed',
                ))
                _w = ('Chalk row %s/%s rejected as poisoned (%s). These test cases are '
                      'NOT grounded in Chalk - re-crawl %s from its correct PI page and '
                      'regenerate before trusting them.' % (_fid, _pi, _reason, _fid))
                if _w not in suite.data_inventory.warnings:
                    suite.data_inventory.warnings.append(_w)
                _sw = getattr(suite, 'warnings', None)
                if isinstance(_sw, list) and _w not in _sw:
                    _sw.append(_w)
            log('[PIPELINE] Stamped %d poisoned-Chalk warning(s) into suite provenance '
                '(Data Sources sheet).' % len(_rej))
    except Exception as _pe:
        log('[PIPELINE] Could not stamp Chalk rejection provenance: %s' % str(_pe)[:120])

    # Log data sources processed
    if suite.data_inventory and suite.data_inventory.sources:
        for src in suite.data_inventory.sources:
            log('[PIPELINE]   Source: %s (%s) — %d items' % (
                src.source_name, src.status, src.items_extracted))

    return {'suite': suite, 'total_steps': total_steps, 'duration': _duration}


def empty_suite_reason(suite):
    """Best-effort, generic reason for a 0-TC suite, taken from the suite's own warnings."""
    for w in (getattr(suite, 'warnings', None) or []):
        lw = str(w).lower()
        if 'poison' in lw or 'reject' in lw:
            return 'Chalk rejected as poisoned' if 'poison' in lw else str(w)[:100]
    return 'no testable items from Jira/Chalk'


def block_generate_output(suite, feature_id, pi, strategy, jira=None, chalk=None, log=print):
    """Block 6: Generate Excel + Feature Doc, save to DB, log transaction."""
    from .excel_generator import generate_excel
    from .doc_generator import generate_feature_doc
    from .database import save_test_suite, log_generation_db, load_latest_suite, get_suite_history
    from .transaction_log import log_generation
    from .coverage_obligations import assert_suite_complete

    # Empty suite: write nothing (no empty Excel / Feature Summary / demo doc).
    if not getattr(suite, 'test_cases', None):
        reason = empty_suite_reason(suite)
        log('[OUTPUT] SKIPPED %s: 0 test cases generated (%s) — no files written' % (feature_id, reason))
        return {
            'skipped': True, 'reason': reason,
            'out_path': None, 'doc_path': None, 'demo_doc_path': None,
            'suite_id': 0, 'tc_count': 0, 'total_steps': 0,
            'diff_report': None, 'scorecard': None,
            'degraded_count': 0, 'degraded_passes': [], 'degraded_details': [],
        }

    # Fail closed before diffing, workbook creation, DB persistence, or success logs.
    assert_suite_complete(suite, context='Pipeline output for %s' % feature_id, log=log)

    # ── Auto-diff: load previous suite file before overwriting DB ──
    diff_report = None
    try:
        _history = get_suite_history(feature_id, limit=2)
        if len(_history) >= 1:
            _prev = _history[0]
            _prev_path = _prev.get('file_path', '')
            if _prev_path and Path(_prev_path).exists():
                try:
                    from .diff_engine import compare_suites as _compare
                    diff_report = _compare(Path(_prev_path), suite, feature_id, log=log)
                except FileNotFoundError:
                    log('[PIPELINE] Previous suite file not found — skipping diff')
                if diff_report:
                    log('[DIFF] vs previous: +%d new, ~%d changed, -%d removed' % (
                        diff_report['new'], diff_report['changed'], diff_report['removed']))
                    # Tag NEW TCs on the suite object so Excel can mark them
                    new_summaries = set()
                    for d in diff_report.get('details', []):
                        if d.startswith('NEW: '):
                            new_summaries.add(d[5:].strip()[:120])
                    for tc in suite.test_cases:
                        if any((tc.summary or '')[:120].startswith(ns[:60]) for ns in new_summaries):
                            tc._is_new = True
    except Exception as _diff_err:
        # Auto-diff is an optional convenience (mark NEW-since-last-run TCs), NOT part
        # of suite correctness. If there is no comparable previous suite, or the diff
        # engine is unavailable, skip quietly - do NOT flag the run as DEGRADED, which
        # falsely warns the user their suite may be incomplete.
        log('[DIFF] Auto-diff skipped (optional, non-fatal): %s' % str(_diff_err)[:80])

    # Defensive: ensure groups dict is stable before Excel generation
    if hasattr(suite, 'groups') and suite.groups:
        suite.groups = dict(suite.groups)  # snapshot to avoid iteration issues

    # ── Attach diff report and scorecard to suite for Excel generation ──
    suite._diff_report = diff_report

    # ── Compute and attach Coverage Scorecard ──
    try:
        from .coverage_scorecard import compute_scorecard
        from .integration_contract import resolve_operation as _rop
        _contract = None
        if jira:
            _ctx = ('%s %s' % (jira.summary or '', jira.description or '')).lower()
            _contract = _rop(jira.summary or '', description=_ctx)
        _nmno = getattr(suite, '_nmno_result', None)
        scorecard = compute_scorecard(suite, jira=jira, contract=_contract, nmno_result=_nmno, log=log)
        suite._coverage_scorecard = scorecard
        from .coverage_scorecard import format_scorecard_text
        log('[SCORECARD]\n' + format_scorecard_text(scorecard))
    except Exception as _sc_err:
        log('[SCORECARD] Skipped: %s' % str(_sc_err)[:80])
        _record_degraded('coverage scorecard', _sc_err)
        scorecard = None
        suite._coverage_scorecard = None

    # Refresh the degraded-run warning now that the output-block passes above have run, so
    # the Excel carries the final count rather than only what the engine recorded.
    # Must be before generate_excel: the workbook is built from suite.warnings.
    _attach_degraded(suite, log)

    out_path = generate_excel(suite, log=log)
    total_steps = sum(len(tc.steps) for tc in suite.test_cases)

    # Generate Feature Objective doc
    doc_path = None
    try:
        doc_path = generate_feature_doc(suite, jira, chalk, log=log)
    except Exception as e:
        log('[PIPELINE] Feature doc warning: %s' % str(e)[:60])
        _record_degraded('Feature Summary document', e)

    # Test Suite Demo document - same content as the workbook, written for a walkthrough.
    demo_doc_path = None
    try:
        from .demo_doc_generator import generate_demo_doc
        demo_doc_path = generate_demo_doc(suite, jira, chalk, log=log)
    except Exception as e:
        log('[PIPELINE] Demo doc warning: %s' % str(e)[:60])

    suite_id = 0
    try:
        suite_id = save_test_suite(suite, file_path=str(out_path))
        log('[PIPELINE] Suite saved to DB (ID: %d)' % suite_id)
    except Exception as e:
        log('[PIPELINE] DB save warning: %s' % str(e)[:60])
        _record_degraded('suite saved to DB', e)

    log_generation(feature_id, pi, len(suite.test_cases), total_steps, strategy, str(out_path))
    log_generation_db(feature_id, pi, len(suite.test_cases), total_steps, strategy, str(out_path))

    # The Feature Summary and the DB save happen AFTER the Excel is written, so a failure in
    # either cannot appear in the workbook. Report them through the result instead, which is
    # what the dashboard renders (Requirement 5.2). Everything earlier is in both places.
    from .degraded_tracker import detail_lines as _degraded_details
    from .degraded_tracker import pass_names as _degraded_names

    return {
        'out_path': out_path, 'doc_path': doc_path, 'demo_doc_path': demo_doc_path,
        'suite_id': suite_id,
        'tc_count': len(suite.test_cases), 'total_steps': total_steps,
        'diff_report': diff_report,
        'scorecard': scorecard,
        'degraded_count': len(_degraded_names()),
        'degraded_passes': _degraded_names(),
        'degraded_details': _degraded_details(),
    }
