"""Inspect the real cached Jira data for MWTGPROV-4166."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import _conn, init_db

init_db()
c = _conn()
row = c.execute("SELECT * FROM jira_cache WHERE feature_id='MWTGPROV-4166'").fetchone()
c.close()

if not row:
    print('Not in Jira cache!')
    sys.exit(1)

print('Summary:', row['summary'])
print('Status:', row['status'])
print('Priority:', row['priority'])
print('PI:', row['pi'])
print('Channel:', row['channel'])
print()

print('=== DESCRIPTION ===')
print((row['description'] or '')[:1000])
print()

print('=== ACCEPTANCE CRITERIA ===')
print((row['ac_text'] or '')[:1000])
print()

print('=== SUBTASKS ===')
subs = json.loads(row['subtasks_json'] or '[]')
for s in subs:
    print('  %s | %s | %s' % (s.get('key', '?'), s.get('status', '?'), s.get('summary', '?')[:90]))
    if s.get('description'):
        print('    desc: %s' % s['description'][:200])
    if s.get('acceptance_criteria'):
        print('    AC: %s' % s['acceptance_criteria'][:200])
print('  Total: %d subtasks' % len(subs))
print()

print('=== LINKED ISSUES ===')
links = json.loads(row['links_json'] or '[]')
for l in links:
    print('  %s | %s' % (l.get('key', '?'), l.get('summary', '?')[:90]))
print()

print('=== LABELS ===')
print(json.loads(row['labels_json'] or '[]'))
