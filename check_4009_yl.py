"""Check 4009 YL sync scenario from Chalk."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, load_jira, _conn

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-4009' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-4009', row['pi_label'])

print('=== 4009 CHALK SCENARIOS (YL related) ===')
print()
for i, sc in enumerate(chalk.scenarios, 1):
    tl = sc.title.lower()
    if 'yl' in tl or ('active' in tl and 'deactive' in tl) or 'line status' in tl or 'state change' in tl:
        print('SC%d: %s' % (i, sc.title))
        print('  Category: %s' % sc.category)
        print('  Validation: %s' % (sc.validation or '')[:300])
        print('  Prereq: %s' % (sc.prereq or '')[:200])
        print('  Steps (%d):' % len(sc.steps))
        for s in sc.steps:
            print('    - %s' % s[:100])
        print()

# Also check Jira AC for YL rules
print('=== 4009 JIRA AC (YL related) ===')
jira = load_jira('MWTGPROV-4009')
if jira:
    ac = jira.get('ac_text', '') or ''
    for line in ac.split('\n'):
        ll = line.lower()
        if 'yl' in ll or 'syniverse' in ll or 'state change' in ll or 'active' in ll:
            print('  %s' % line.strip()[:120])
