"""Check 4009 subtask NSLNM-601 YL table vs generated TCs."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_jira, load_latest_suite

jira = load_jira('MWTGPROV-4009')
subtasks = json.loads(jira.get('subtasks_json', '[]'))

print('=== SUBTASK NSLNM-601 ===')
for st in subtasks:
    if 'NSLNM-601' in st.get('key', '') or '601' in st.get('key', ''):
        print('Key: %s' % st.get('key', ''))
        print('Summary: %s' % st.get('summary', ''))
        print('Description (%d chars):' % len(st.get('description', '') or ''))
        desc = st.get('description', '') or ''
        # Print full description to find the YL table
        print(desc[:2000])
        print()
        # Check for comments
        comments = st.get('comments', [])
        print('Comments: %d' % len(comments))
        for c in comments[:3]:
            body = c.get('body', '') if isinstance(c, dict) else str(c)
            print('  Comment: %s' % str(body)[:200])
        break
else:
    print('NSLNM-601 not found in subtasks')
    print('Available subtasks:')
    for st in subtasks:
        print('  %s: %s (%d chars desc)' % (st.get('key', '?'), st.get('summary', '?')[:50], len(st.get('description', '') or '')))

# Now check what YL scenarios our TCs cover
print()
print('=== GENERATED TCs with YL ===')
suite = load_latest_suite('MWTGPROV-4009')
if suite:
    for tc in suite.get('test_cases', []):
        s = tc.get('summary', '').lower()
        if 'yl' in s or ('active' in s and ('deactive' in s or 'hotline' in s or 'suspend' in s)):
            print('TC%s: %s' % (tc.get('sno', '?'), tc.get('summary', '')[:80]))

# The Jira subtask YL table from the screenshot:
print()
print('=== JIRA SUBTASK YL TABLE (from screenshot) ===')
yl_table = [
    ('Active', 'Active', 'No action'),
    ('Active', 'Deactive', 'Create subscriber in Syniverse'),
    ('Active', 'Suspend', 'Create subscriber in Syniverse'),
    ('Active', 'Hotline', 'No action (TMO doesnt expose hotline status)'),
    ('Deactive', 'Deactive', 'No action'),
]
print('%-15s %-15s %s' % ('TMO Status', 'NSL Status', 'Syniverse Action'))
for tmo, nsl, action in yl_table:
    print('%-15s %-15s %s' % (tmo, nsl, action))

print()
print('=== COVERAGE CHECK ===')
all_text = ' '.join(tc.get('summary', '').lower() for tc in suite.get('test_cases', []))
checks = [
    ('Active→Active (no action)', 'active and active' in all_text or 'no line status change' in all_text),
    ('Active→Deactive (Create in Syniverse)', 'active' in all_text and 'deactive' in all_text),
    ('Active→Suspend (Create in Syniverse)', 'active' in all_text and 'suspend' in all_text),
    ('Active→Hotline (No action - TMO doesnt expose)', 'hotline' in all_text),
    ('Deactive→Deactive (no action)', 'deactive and deactive' in all_text or 'no line status change' in all_text),
]
for name, covered in checks:
    print('  %-50s %s' % (name, 'COVERED' if covered else 'MISSING'))
