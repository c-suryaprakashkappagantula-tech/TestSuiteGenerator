"""Quick check: is MWTGPROV-4348 in the DB cache?"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import _conn, init_db
init_db()
c = _conn()
row = c.execute("SELECT feature_id, summary FROM jira_cache WHERE feature_id LIKE '%4348%'").fetchone()
if row:
    print('Found: %s | %s' % (row['feature_id'], row['summary']))
else:
    print('MWTGPROV-4348 NOT FOUND in jira_cache')
    rows = c.execute('SELECT feature_id, summary FROM jira_cache ORDER BY feature_id').fetchall()
    print('\nAvailable features (%d):' % len(rows))
    for r in rows:
        print('  %s | %s' % (r['feature_id'], (r['summary'] or '')[:60]))

# Also check chalk
chalk_row = c.execute("SELECT feature_id, pi_label FROM chalk_cache WHERE feature_id LIKE '%4348%'").fetchone()
if chalk_row:
    print('\nChalk found: %s (%s)' % (chalk_row['feature_id'], chalk_row['pi_label']))
else:
    print('\nNo Chalk cache for 4348')

# Check NMNO
nmno_row = c.execute("SELECT api_name FROM nmno_api_cache WHERE api_name LIKE '%align%' OR api_name LIKE '%4348%'").fetchone()
if nmno_row:
    print('NMNO found: %s' % nmno_row['api_name'])
else:
    print('No NMNO cache for data alignment')

c.close()
