"""Check 3949 TC01 — should be UI visibility, not swap steps."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_chalk_as_object, _conn
from modules.step_templates import get_step_chain, _is_ui_flow, _is_swap_mdn, _is_inquiry

c = _conn()
row = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id='MWTGPROV-3949' AND scenarios_json != '[]' LIMIT 1").fetchone()
c.close()
chalk = load_chalk_as_object('MWTGPROV-3949', row['pi_label'])
sc = chalk.scenarios[0]

print('SC1 Title: %s' % sc.title[:120])
print('SC1 Category: %s' % sc.category)
print('SC1 Steps: %d' % len(sc.steps))
print('SC1 Validation: %s' % (sc.validation or '')[:200])
print()

t = (sc.title + ' ' + (sc.validation or '')).lower()
ctx = 'swap mdn'
print('is_ui_flow:', _is_ui_flow(t, ctx))
print('is_swap_mdn:', _is_swap_mdn(t, ctx))
print('is_inquiry:', _is_inquiry(t, ctx))
print()
print('Keywords in title: menu=%s visible=%s accessible=%s swap=%s mdn=%s' % (
    'menu' in t, 'visible' in t, 'accessible' in t, 'swap' in t, 'mdn' in t))
print()

# What step chain does it get?
steps = get_step_chain(sc.title, sc.validation, ctx)
print('Step chain: %d steps' % len(steps))
print('S1: %s' % steps[0][0][:70])
