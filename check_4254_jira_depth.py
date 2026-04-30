"""Check what Jira data we have cached for MWTGPROV-4254 — subtasks, comments, attachments."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_jira

jira = load_jira('MWTGPROV-4254')
if not jira:
    print('No Jira data for 4254')
    sys.exit(1)

print('=' * 100)
print('  MWTGPROV-4254 — JIRA DATA DEPTH CHECK')
print('=' * 100)
print()

# Basic info
print('Summary: %s' % jira.get('summary', '')[:80])
print('Status: %s' % jira.get('status', ''))
print('Channel: %s' % jira.get('channel', ''))
print('PI: %s' % jira.get('pi', ''))
print('Description: %d chars' % len(jira.get('description', '') or ''))
print('AC Text: %d chars' % len(jira.get('ac_text', '') or ''))
print()

# Subtasks
subtasks = json.loads(jira.get('subtasks_json', '[]'))
print('SUBTASKS: %d found' % len(subtasks))
for i, st in enumerate(subtasks, 1):
    key = st.get('key', '?')
    summary = st.get('summary', '?')[:60]
    desc = st.get('description', '') or ''
    status = st.get('status', '?')
    print('  %d. %s | %s | %s | desc=%d chars' % (i, key, summary, status, len(desc)))
    if desc:
        print('     Desc preview: %s' % desc[:120])
    # Check if subtask has its own attachments/comments
    st_attachments = st.get('attachments', [])
    st_comments = st.get('comments', [])
    if st_attachments:
        print('     Attachments: %d' % len(st_attachments))
        for a in st_attachments[:3]:
            print('       - %s' % (a.get('filename', a) if isinstance(a, dict) else str(a)[:60]))
    if st_comments:
        print('     Comments: %d' % len(st_comments))
        for c in st_comments[:2]:
            body = c.get('body', c) if isinstance(c, dict) else str(c)
            print('       - %s' % str(body)[:80])
print()

# Comments on parent
comments = json.loads(jira.get('comments_json', '[]'))
print('COMMENTS: %d found' % len(comments))
for i, c in enumerate(comments[:5], 1):
    body = c.get('body', '') if isinstance(c, dict) else str(c)
    author = c.get('author', {}).get('displayName', '?') if isinstance(c, dict) else '?'
    print('  %d. [%s] %s' % (i, author, str(body)[:100]))
if len(comments) > 5:
    print('  ... and %d more comments' % (len(comments) - 5))
print()

# Attachments on parent
attachments = json.loads(jira.get('attachments_json', '[]'))
print('ATTACHMENTS: %d found' % len(attachments))
for i, a in enumerate(attachments, 1):
    if isinstance(a, dict):
        print('  %d. %s (%s, %s bytes)' % (i, a.get('filename', '?'), a.get('mimeType', '?'), a.get('size', '?')))
    else:
        print('  %d. %s' % (i, str(a)[:80]))
print()

# Linked issues
links = json.loads(jira.get('links_json', '[]'))
print('LINKED ISSUES: %d found' % len(links))
for i, l in enumerate(links[:5], 1):
    print('  %d. %s - %s' % (i, l.get('key', '?'), l.get('summary', '?')[:60]))
print()

# Check what the engine mines from subtasks
print('=' * 100)
print('  WHAT THE ENGINE SEES')
print('=' * 100)
print()
print('Subtask descriptions mined: %s' % ('YES' if any(st.get('description') for st in subtasks) else 'NO — descriptions empty'))
print('Subtask attachments mined: %s' % ('YES' if any(st.get('attachments') for st in subtasks) else 'NO — not fetched'))
print('Subtask comments mined: %s' % ('YES' if any(st.get('comments') for st in subtasks) else 'NO — not fetched'))
print('Parent comments mined: %s' % ('YES (%d)' % len(comments) if comments else 'NO'))
print('Parent attachments downloaded: %s' % ('YES (%d)' % len(attachments) if attachments else 'NO'))
