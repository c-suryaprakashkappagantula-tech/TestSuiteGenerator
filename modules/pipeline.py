"""
pipeline.py — Block-based execution pipeline with self-heal retry.
Each block runs independently, retries on failure, and stores results
in session state so successful blocks don't re-run on resume.

Blocks:
  1. Jira Fetch (+ subtasks + attachments download)
  2. Chalk DB Cache Lookup
  3. Chalk Live Page Fetch (only if Block 2 misses)
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
    """Block 1: Fetch Jira issue with subtasks and attachments list."""
    from .jira_fetcher import fetch_jira_issue, validate_jira_issue, download_attachments
    from .database import save_jira

    jira = fetch_jira_issue(page, feature_id, log=log)
    warnings = validate_jira_issue(jira, log=log)

    # Save to DB for caching (Finding #3)
    try:
        save_jira(jira)
        log('[PIPELINE] Jira data saved to DB for %s' % feature_id)
    except Exception as e:
        log('[PIPELINE] Jira DB save warning: %s' % str(e)[:60])

    # Download attachments
    att_paths = []
    if jira.attachments:
        att_paths = download_attachments(page, jira, log=log)
        log('[PIPELINE] Downloaded %d attachments' % len(att_paths))

    return {'jira': jira, 'warnings': warnings, 'att_paths': att_paths}


def block_chalk_db(feature_id, pi_label, log=print):
    """Block 2: Try to load Chalk data from DB cache."""
    from .database import load_chalk_as_object, _conn

    # Try selected PI first
    chalk = load_chalk_as_object(feature_id, pi_label)
    if chalk and chalk.scenarios:
        log('[PIPELINE] Chalk DB hit (%s): %d scenarios' % (pi_label, len(chalk.scenarios)))
        return {'chalk': chalk, 'source': 'DB cache (%s)' % pi_label}

    # Try any PI
    c = _conn()
    row = c.execute('SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != "[]" LIMIT 1',
                    (feature_id,)).fetchone()
    c.close()
    if row:
        chalk = load_chalk_as_object(feature_id, row['pi_label'])
        if chalk and chalk.scenarios:
            log('[PIPELINE] Chalk DB hit (%s): %d scenarios' % (row['pi_label'], len(chalk.scenarios)))
            return {'chalk': chalk, 'source': 'DB cache (%s)' % row['pi_label']}

    log('[PIPELINE] Chalk DB miss for %s' % feature_id)
    return {'chalk': None, 'source': 'not in DB'}


def block_chalk_live(page, feature_id, pi_url, pi_label, pi_list, log=print):
    """Block 3: Fetch Chalk data from live page (fallback when DB misses)."""
    from .chalk_parser import fetch_feature_from_pi, ChalkData
    from .database import save_chalk

    # Try selected PI
    chalk = fetch_feature_from_pi(page, pi_url, feature_id, log=log)
    if chalk and chalk.scenarios:
        save_chalk(feature_id, pi_label, chalk)
        log('[PIPELINE] Chalk live hit (%s): %d scenarios' % (pi_label, len(chalk.scenarios)))
        return {'chalk': chalk, 'source': '%s (live)' % pi_label}

    # Scan all PIs
    for sl, su in pi_list:
        if sl == pi_label:
            continue
        try:
            sc = fetch_feature_from_pi(page, su, feature_id, log=lambda m: None)
            if sc and sc.scenarios:
                save_chalk(feature_id, sl, sc)
                log('[PIPELINE] Chalk found on %s: %d scenarios' % (sl, len(sc.scenarios)))
                return {'chalk': sc, 'source': '%s (scanned)' % sl}
        except:
            pass

    log('[PIPELINE] Chalk not found on any PI for %s' % feature_id)
    return {'chalk': ChalkData(feature_id=feature_id), 'source': 'not found'}


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


def block_build_suite(jira, chalk, parsed_docs, options, log=print):
    """Block 5: Build the test suite."""
    from .test_engine import build_test_suite

    suite = build_test_suite(jira, chalk, parsed_docs, options, log=log)
    total_steps = sum(len(tc.steps) for tc in suite.test_cases)
    log('[PIPELINE] Suite built: %d TCs | %d steps' % (len(suite.test_cases), total_steps))
    return {'suite': suite, 'total_steps': total_steps}


def block_generate_output(suite, feature_id, pi, strategy, log=print):
    """Block 6: Generate Excel, save to DB, log transaction."""
    from .excel_generator import generate_excel
    from .database import save_test_suite, log_generation_db
    from .transaction_log import log_generation

    out_path = generate_excel(suite, log=log)
    total_steps = sum(len(tc.steps) for tc in suite.test_cases)

    suite_id = 0
    try:
        suite_id = save_test_suite(suite, file_path=str(out_path))
        log('[PIPELINE] Suite saved to DB (ID: %d)' % suite_id)
    except Exception as e:
        log('[PIPELINE] DB save warning: %s' % str(e)[:60])

    log_generation(feature_id, pi, len(suite.test_cases), total_steps, strategy, str(out_path))
    log_generation_db(feature_id, pi, len(suite.test_cases), total_steps, strategy, str(out_path))

    return {
        'out_path': out_path, 'suite_id': suite_id,
        'tc_count': len(suite.test_cases), 'total_steps': total_steps,
    }
