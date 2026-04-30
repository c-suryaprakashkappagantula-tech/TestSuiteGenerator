"""Inspect what's in the NMNO transaction flow DB table."""
import sqlite3, json
c = sqlite3.connect('TestSuiteGenerator/tsg_cache.db')
c.row_factory = sqlite3.Row

print('=== OVERVIEW ===')
rows = c.execute('SELECT pi_label, tab_name, block_type, COUNT(*) as cnt FROM TMO_NMNO_Transaction_Flow_Chalk GROUP BY pi_label, tab_name, block_type ORDER BY pi_label').fetchall()
for r in rows:
    print('  %-50s %-15s %-10s %d' % (r['pi_label'][:50], r['tab_name'][:15], r['block_type'], r['cnt']))

print()
print('=== HEADINGS (all) ===')
headings = c.execute("SELECT pi_label, content FROM TMO_NMNO_Transaction_Flow_Chalk WHERE block_type='heading' ORDER BY pi_label, id").fetchall()
for h in headings:
    print('  [%-20s] %s' % (h['pi_label'][:20], h['content'][:90]))

print()
print('=== TABLES (first 5) ===')
tables = c.execute("SELECT pi_label, content FROM TMO_NMNO_Transaction_Flow_Chalk WHERE block_type='table' LIMIT 5").fetchall()
for t in tables:
    data = json.loads(t['content'])
    print('  [%s] %d rows' % (t['pi_label'][:20], len(data)))
    for row in data[:3]:
        print('    %s' % ' | '.join(str(cell)[:35] for cell in row[:5]))
    if len(data) > 3:
        print('    ... +%d more rows' % (len(data) - 3))
    print()

print('=== API/TRANSACTION TEXT SAMPLES ===')
api_texts = c.execute("""SELECT pi_label, content FROM TMO_NMNO_Transaction_Flow_Chalk 
    WHERE block_type='text' AND (
        content LIKE '%API%' OR content LIKE '%payload%' OR content LIKE '%endpoint%' 
        OR content LIKE '%transaction%' OR content LIKE '%request%' OR content LIKE '%response%'
        OR content LIKE '%activate%' OR content LIKE '%deactivat%' OR content LIKE '%swap%'
    ) LIMIT 20""").fetchall()
for t in api_texts:
    print('  [%-20s] %s' % (t['pi_label'][:20], t['content'][:100]))

c.close()
