"""Check 3949 TC02 eSIM swap scenario from Chalk."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, _conn
c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-3949' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-3949', row['pi_label'])
for i, sc in enumerate(chalk.scenarios, 1):
    if 'esim to esim' in sc.title.lower() or ('em)' in sc.title.lower() and 'swap' in sc.title.lower()):
        print('SC%d: %s' % (i, sc.title[:100]))
        print('  Steps: %d' % len(sc.steps))
        for s in sc.steps[:5]:
            print('    - %s' % s[:80])
        print('  Validation: %s' % (sc.validation or '')[:150])
        print('  Category: %s' % sc.category)
        print()
