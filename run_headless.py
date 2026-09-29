"""
run_headless.py — Headless CLI runner for TSG V8.0.
Generates test suites from DB-cached Jira/Chalk data without the browser/Streamlit.

Usage:
    python run_headless.py MWTGPROV-4503 PI-59
    python run_headless.py MWTGPROV-4167 PI-59
"""
import sys
import os
from pathlib import Path

# Ensure modules are on the path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from modules.config import OUTPUTS
from modules.database import (
    init_db, load_chalk_as_object, load_jira, get_chalk_cache_count
)
from modules.pipeline import (
    block_chalk_db, block_build_suite_v8, block_generate_output, block_deep_mine
)
from modules.chalk_parser import ChalkData


def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def run_headless(feature_id: str, pi_label: str, custom_instructions: str = '',
                 deep_mine: bool = True):
    log('\n' + '=' * 70)
    log('TSG V8.0 Headless Run: %s  [%s]' % (feature_id, pi_label))
    log('=' * 70)

    # 1. Init DB
    init_db()

    # 2. Load Jira from cache
    jira_raw = load_jira(feature_id)
    if not jira_raw:
        log('[WARN] No Jira cache for %s — proceeding CHALK-ONLY.' % feature_id)
        log('[WARN]   Degraded: no feature title/summary, no AC-derived supplementary')
        log('[WARN]   TCs, no labels/priority. Sync Jira for a complete suite.')
        jira_raw = {'feature_id': feature_id, 'pi': pi_label}

    # Reconstruct JiraIssue from the raw DB row (load_jira returns column names,
    # so ac_text/labels_json/subtasks_json must be mapped, not read as-is).
    import json as _json
    from modules.jira_fetcher import JiraIssue

    def _js(key, default):
        try:
            return _json.loads(jira_raw.get(key) or '')
        except Exception:
            return default

    jira = JiraIssue(
        key=jira_raw.get('feature_id', feature_id),
        summary=jira_raw.get('summary', ''),
        description=jira_raw.get('description', ''),
        acceptance_criteria=jira_raw.get('ac_text', '') or '',
        labels=_js('labels_json', []),
        status=jira_raw.get('status', ''),
        priority=jira_raw.get('priority', ''),
        assignee=jira_raw.get('assignee', ''),
        reporter=jira_raw.get('reporter', ''),
        linked_issues=_js('links_json', []),
        subtasks=_js('subtasks_json', []),
        comments=_js('comments_json', []),
        raw_json=_js('raw_json', {}),
        pi=jira_raw.get('pi', '') or pi_label,
        channel=jira_raw.get('channel', '') or '',
    )
    log('[JIRA]   AC=%d chars | subtasks=%d | links=%d'
        % (len(jira.acceptance_criteria or ''), len(jira.subtasks), len(jira.linked_issues)))
    log('[JIRA] Loaded from cache: %s — %s' % (jira.key, jira.summary[:80]))

    # 3. Load Chalk from DB cache
    chalk_result = block_chalk_db(feature_id, pi_label, log=log)
    chalk = chalk_result.get('chalk') if chalk_result else None
    if chalk and chalk.scenarios:
        log('[CHALK] %d scenarios loaded from DB' % len(chalk.scenarios))
    else:
        log('[CHALK] No scenarios in DB cache — will proceed with Jira AC only')
        chalk = ChalkData(feature_id=feature_id)

    # 3b. Deep Mine (Block 4b) — mirrors the dashboard so the headless path is not a
    # false safety net. This is the input that made a live 4503 run diverge from cache
    # (subtask mining), so verifying without it hides real regressions. Browserless:
    # page=None, so API-spec crawling is skipped but Jira-subtask mining still runs.
    deep_mine_result = None
    if deep_mine:
        try:
            from modules.nmno_api_lookup import (extract_api_operation_name as _ean,
                                                 lookup_api_specs as _las)
            import re as _re_dm
            _urls = _re_dm.findall(r'https?://[^\s<>"\']+chalk[^\s<>"\']*',
                                   jira.acceptance_criteria or '') if jira else []
            _api = _ean(jira.summary if jira else '', _urls)
            _nmno = _las(_api, log=lambda x: None) if _api else None
            if _nmno and _nmno.business_rules:
                from modules.deep_miner import DeepMineResult, _mine_subtask
                deep_mine_result = DeepMineResult(feature_id=feature_id)
                for _st in (jira.subtasks or []):
                    _m = _mine_subtask(_st, log=lambda x: None)
                    if _m.ac_items or _m.testable_rules:
                        deep_mine_result.subtask_mines.append(_m)
                log('[DEEP-MINE] NMNO has %d business rules — subtask-only mine (%d)'
                    % (len(_nmno.business_rules), len(deep_mine_result.subtask_mines)))
            else:
                dm = block_deep_mine(jira, chalk, page=None, log=log)
                deep_mine_result = dm.get('deep_mine_result')
        except Exception as _dm_err:
            log('[DEEP-MINE] WARNING: %s — continuing without deep data' % str(_dm_err)[:120])
            deep_mine_result = None
    else:
        log('[DEEP-MINE] skipped (--no-deep-mine)')

    # 4. Build options
    options = {
        'engine_version': '8',
        'include_negative': True,
        'include_boundary': False,
        'pi_label': pi_label,
        'custom_instructions': custom_instructions,
        'channel': [],
        'devices': [],
        'networks': [],
        'sim_types': [],
    }

    # 5. Build suite
    log('\n[BUILD] Building test suite...')
    build_result = block_build_suite_v8(
        jira=jira,
        chalk=chalk,
        parsed_docs=[],
        options=options,
        deep_mine_result=deep_mine_result,
        log=log,
    )
    suite = build_result.get('suite')
    if not suite or not suite.test_cases:
        log('[ERROR] No test cases generated')
        return None

    log('\n[RESULT] %d TCs generated (%d steps total)' % (
        len(suite.test_cases),
        sum(len(tc.steps) for tc in suite.test_cases)))

    # Print TC list
    log('\n--- TC Summary ---')
    for i, tc in enumerate(suite.test_cases, 1):
        log('  TC%02d  [%s]  %s' % (i, tc.category or 'Happy Path', tc.summary[:90]))

    # 6. Generate Excel output
    log('\n[OUTPUT] Generating Excel...')
    from modules.excel_generator import generate_excel
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    out_path = generate_excel(suite, log=log)
    log('[OUTPUT] Saved: %s' % out_path)

    return suite


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python run_headless.py <FEATURE_ID> <PI_LABEL> [custom_instructions] [--no-deep-mine]')
        print('  e.g. python run_headless.py MWTGPROV-4503 PI-59')
        sys.exit(1)

    _args = [a for a in sys.argv[1:] if a != '--no-deep-mine']
    _deep = '--no-deep-mine' not in sys.argv
    fid = _args[0]
    pi = _args[1]
    ci = _args[2] if len(_args) > 2 else ''

    suite = run_headless(fid, pi, ci, deep_mine=_deep)
    if suite:
        print('\nDone — %d test cases' % len(suite.test_cases))
    else:
        print('\nFailed — check errors above')
        sys.exit(1)
