"""
Dry-run: Build test suites for MWTGPROV-4166 and MWTGPROV-4230 from DB cache.
Shows scenarios, steps, and compares TC count with previous generations.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from modules.database import _conn, init_db, load_chalk_as_object
from modules.test_engine import build_test_suite
from modules.jira_fetcher import JiraIssue
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.excel_generator import generate_excel

init_db()

# ════════════════════════════════════════════════════════════════════
# HELPER: Load Jira from DB cache
# ════════════════════════════════════════════════════════════════════

def load_jira_from_db(feature_id: str) -> JiraIssue:
    """Load JiraIssue from jira_cache table."""
    c = _conn()
    row = c.execute(
        "SELECT * FROM jira_cache WHERE feature_id=?", (feature_id,)
    ).fetchone()
    c.close()
    if not row:
        raise RuntimeError(f"{feature_id} not found in jira_cache!")
    d = dict(row)
    return JiraIssue(
        key=d['feature_id'],
        summary=d['summary'],
        description=d.get('description', ''),
        status=d.get('status', ''),
        priority=d.get('priority', ''),
        issue_type='Story',
        assignee=d.get('assignee', ''),
        reporter=d.get('reporter', ''),
        labels=json.loads(d.get('labels_json', '[]')),
        pi=d.get('pi', ''),
        channel=d.get('channel', ''),
        acceptance_criteria=d.get('ac_text', ''),
    )


def load_chalk_from_db(feature_id: str) -> ChalkData:
    """Load ChalkData from chalk_cache table."""
    c = _conn()
    row = c.execute(
        "SELECT * FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1",
        (feature_id,)
    ).fetchone()
    c.close()
    if row:
        return load_chalk_as_object(feature_id, row['pi_label'])
    # Return empty chalk if not found
    return ChalkData(scope='', rules='', scenarios=[], open_items=[])


def get_previous_generation(feature_id: str) -> dict:
    """Get the most recent previous generation stats."""
    c = _conn()
    row = c.execute(
        "SELECT tc_count, step_count, strategy, created_at FROM generations "
        "WHERE feature_id=? ORDER BY id DESC LIMIT 1",
        (feature_id,)
    ).fetchone()
    c.close()
    return dict(row) if row else {}


# ════════════════════════════════════════════════════════════════════
# RUN FOR EACH FEATURE
# ════════════════════════════════════════════════════════════════════

FEATURES = ['MWTGPROV-4166', 'MWTGPROV-4230']

options = {
    'channel': ['NBOP'], 'devices': ['Mobile'], 'networks': ['4G', '5G'],
    'sim_types': ['eSIM', 'pSIM'], 'os_platforms': ['iOS', 'Android'],
    'include_positive': True, 'include_negative': True, 'include_e2e': True,
    'include_edge': True, 'include_attachments': True,
    'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
}

results = {}

for fid in FEATURES:
    print('\n' + '═' * 70)
    print(f'  {fid}')
    print('═' * 70)

    # Load data
    jira = load_jira_from_db(fid)
    chalk = load_chalk_from_db(fid)
    prev = get_previous_generation(fid)

    print(f'\n  Summary: {jira.summary}')
    print(f'  PI: {jira.pi} | Channel: {jira.channel}')
    print(f'  AC: {jira.acceptance_criteria[:200]}...' if len(jira.acceptance_criteria or '') > 200 else f'  AC: {jira.acceptance_criteria}')

    # Show chalk scenarios
    print(f'\n  ── CHALK SCENARIOS ({len(chalk.scenarios)}) ──')
    for i, s in enumerate(chalk.scenarios, 1):
        print(f'    {i}. [{s.category}] {s.title[:80]}')

    # Build suite
    print(f'\n  ── BUILDING TEST SUITE ──')
    suite = build_test_suite(jira, chalk, [], options, log=lambda msg: print(f'    {msg}'))

    # Show generated test cases
    print(f'\n  ── GENERATED TEST CASES ({len(suite.test_cases)}) ──')
    total_steps = 0
    for i, tc in enumerate(suite.test_cases, 1):
        step_count = len(tc.steps)
        total_steps += step_count
        cat = getattr(tc, 'category', '?')
        print(f'    TC{i:02d} [{cat:12s}] {tc.summary[:75]} ({step_count} steps)')

    # Show steps for first 3 TCs
    print(f'\n  ── STEP DETAILS (first 3 TCs) ──')
    for i, tc in enumerate(suite.test_cases[:3], 1):
        print(f'\n    TC{i:02d}: {tc.summary[:70]}')
        for j, step in enumerate(tc.steps, 1):
            action = getattr(step, 'action', getattr(step, 'step_action', ''))[:60]
            expected = getattr(step, 'expected', getattr(step, 'expected_result', ''))[:50]
            print(f'      S{j}. {action}')
            print(f'         → {expected}')

    # Compare with previous
    print(f'\n  ── COMPARISON WITH PREVIOUS ──')
    if prev:
        print(f'    Previous: {prev["tc_count"]} TCs, {prev["step_count"]} steps ({prev["strategy"]}, {prev["created_at"]})')
        print(f'    Current:  {len(suite.test_cases)} TCs, {total_steps} steps')
        tc_diff = len(suite.test_cases) - prev['tc_count']
        step_diff = total_steps - prev['step_count']
        print(f'    Delta:    {tc_diff:+d} TCs, {step_diff:+d} steps')
    else:
        print(f'    No previous generation found.')
        print(f'    Current:  {len(suite.test_cases)} TCs, {total_steps} steps')

    results[fid] = {
        'tc_count': len(suite.test_cases),
        'step_count': total_steps,
        'prev_tc': prev.get('tc_count', 0),
        'prev_steps': prev.get('step_count', 0),
    }

    # Generate Excel
    out_path = generate_excel(suite, log=lambda msg: print(f'    {msg}'))
    print(f'\n  Output: {out_path}')

# ════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════
print('\n' + '═' * 70)
print('  SUMMARY')
print('═' * 70)
print(f'  {"Feature":<18} {"Prev TCs":>10} {"New TCs":>10} {"Delta":>8} {"Prev Steps":>12} {"New Steps":>11} {"Delta":>8}')
print(f'  {"─"*18} {"─"*10} {"─"*10} {"─"*8} {"─"*12} {"─"*11} {"─"*8}')
for fid, r in results.items():
    tc_d = r['tc_count'] - r['prev_tc']
    st_d = r['step_count'] - r['prev_steps']
    print(f'  {fid:<18} {r["prev_tc"]:>10} {r["tc_count"]:>10} {tc_d:>+8} {r["prev_steps"]:>12} {r["step_count"]:>11} {st_d:>+8}')
print()
