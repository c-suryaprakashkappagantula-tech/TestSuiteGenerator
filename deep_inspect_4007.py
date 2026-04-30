"""Deep inspection of MWTGPROV-4007 — Chalk, Jira, and generated suite."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, load_jira, load_latest_suite, _conn

fid = 'MWTGPROV-4007'

# ═══ CHALK ═══
print('=' * 100)
print('  CHALK DATA for %s' % fid)
print('=' * 100)
c = _conn()
row = c.execute("SELECT pi_label, scope, scenarios_json, raw_text FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
c.close()

if row:
    print('PI: %s' % row['pi_label'])
    print('Scope: %s' % (row['scope'] or '')[:200])
    print()
    scenarios = json.loads(row['scenarios_json'])
    print('Chalk Scenarios: %d' % len(scenarios))
    for i, sc in enumerate(scenarios, 1):
        print()
        print('  SC%d: %s' % (i, sc.get('title', '')[:80]))
        print('    Category: %s' % sc.get('category', ''))
        print('    Prereq: %s' % (sc.get('prereq', '') or '')[:100])
        print('    Validation: %s' % (sc.get('validation', '') or '')[:100])
        steps = sc.get('steps', [])
        if steps:
            print('    Steps (%d):' % len(steps))
            for s in steps[:5]:
                print('      - %s' % s[:80])
        cdr = sc.get('cdr_input', '')
        if cdr:
            print('    CDR Input: %s' % cdr[:80])
    print()
    raw = row['raw_text'] or ''
    print('Raw text length: %d chars' % len(raw))
    if raw:
        print('Raw text preview (first 500):')
        print(raw[:500])
else:
    print('NO CHALK DATA FOUND')

# ═══ JIRA ═══
print()
print('=' * 100)
print('  JIRA DATA for %s' % fid)
print('=' * 100)
jira = load_jira(fid)
if jira:
    print('Summary: %s' % jira.get('summary', ''))
    print('Status: %s' % jira.get('status', ''))
    print('Channel: %s' % jira.get('channel', ''))
    print('PI: %s' % jira.get('pi', ''))
    print('Description: %d chars' % len(jira.get('description', '') or ''))
    if jira.get('description'):
        print('  Preview: %s' % jira['description'][:300])
    print('AC: %d chars' % len(jira.get('ac_text', '') or ''))
    if jira.get('ac_text'):
        print('  Preview: %s' % jira['ac_text'][:300])
    subtasks = json.loads(jira.get('subtasks_json', '[]'))
    print('Subtasks: %d' % len(subtasks))
    for st in subtasks:
        print('  %s: %s (%d chars desc)' % (st.get('key', '?'), st.get('summary', '?')[:50], len(st.get('description', '') or '')))
    comments = json.loads(jira.get('comments_json', '[]'))
    print('Comments: %d' % len(comments))
    attachments = json.loads(jira.get('attachments_json', '[]'))
    print('Attachments: %d' % len(attachments))
else:
    print('NO JIRA DATA FOUND')

# ═══ GENERATED SUITE ═══
print()
print('=' * 100)
print('  GENERATED SUITE for %s' % fid)
print('=' * 100)
suite = load_latest_suite(fid)
if suite:
    tcs = suite.get('test_cases', [])
    print('TCs: %d | Steps: %d' % (len(tcs), sum(len(tc.get('steps', [])) for tc in tcs)))
    print('Created: %s' % suite.get('created_at', ''))
    print()
    for tc in tcs:
        sno = tc.get('sno', '?')
        summary = tc.get('summary', '')
        cat = tc.get('category', '')
        steps = tc.get('steps', [])
        name = summary[summary.find('_', summary.find('_') + 1) + 1:] if '_' in summary else summary
        print('  TC%-3s [%-10s] %s' % (sno, cat[:10], name[:75]))
        print('        Desc: %s' % (tc.get('description', '') or '')[:80])
        print('        Precon: %s' % (tc.get('preconditions', '') or '').split('\n')[0][:60])
        print('        Steps: %d' % len(steps))
        for s in steps[:3]:
            print('          S%s: %s' % (s.get('step_num', '?'), s.get('summary', '')[:65]))
            print('              Exp: %s' % s.get('expected', '')[:65])
        if len(steps) > 3:
            print('          ... +%d more steps' % (len(steps) - 3))
        print()
else:
    print('NO SUITE FOUND')
