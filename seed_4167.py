"""Seed MWTGPROV-4167 PI-59 Chalk row.

Implements v5 changelog section 4b / 6b. The v5 package seeded this row into its own
tsg_cache.db but shipped neither that DB nor the seed_4167.py it referenced, so the fix
never reached this dashboard. 4167 had only a PI-46 row, which actually held the PI-46
page content for 3641/3632/3677 and is correctly rejected by the cache-poisoning guard
in load_chalk_normalized(); with no valid row the engine silently fell through to the
generic CR-delegation path and emitted off-topic test cases.

Content is the PI-59 Chalk scenario set for 4167 - cross-network Host MDN validation on
Add Wearable, defect MWTGTEST-11222.
"""
import sqlite3, json, datetime

FID, PI = 'MWTGPROV-4167', 'PI-59'

SC = [
 dict(scenario_id='TS_MWTGPROV-4167_1', category='Happy Path',
   title='Verify TMO Add Wearable (Companion Wearable) with a TMO Host MDN passes',
   prereq='ACTIVE TMO Phone host line (NETWORK_PROVIDER=TMO), eSIM smartwatch to pair.',
   steps=['Submit add-wearables (transactionType=AW, requestType=TMO) with hostMDN = the TMO Phone MDN.',
          'NSL performs host-line subscriber lookup and compares host NETWORK_PROVIDER to requestType.',
          'NETWORK_PROVIDER=TMO == requestType(TMO) -> validation passes.',
          'Activation proceeds to TMO/NE.'],
   validation='Host MDN network (TMO) matches requestType (TMO). Validation passes; Add Wearable activation proceeds normally to the TMO/NE and completes. Wearable paired to the TMO host line. Same-network request unaffected by the new validation.'),
 dict(scenario_id='TS_MWTGPROV-4167_2', category='Happy Path',
   title='Verify VZW Add Wearable (Companion Wearable) with a VZW Host MDN passes',
   prereq='ACTIVE VZW Phone host line (NETWORK_PROVIDER=VZW), eSIM smartwatch.',
   steps=['Submit add-wearables (transactionType=AW, requestType=VZW) with hostMDN = the VZW Phone MDN.',
          'NSL host-line lookup: NETWORK_PROVIDER=VZW == requestType(VZW) -> validation passes.',
          'Activation proceeds to VZW/NE.'],
   validation='Host MDN network (VZW) matches requestType (VZW). Validation passes; existing VZW Add Wearable flow is not blocked by the new cross-network check. Activation proceeds to VZW/NE.'),
 dict(scenario_id='TS_MWTGPROV-4167_3', category='Negative',
   title='Verify TMO Add Wearable with a VZW Host MDN is REJECTED',
   prereq='ACTIVE VZW Phone host line (NETWORK_PROVIDER=VZW).',
   steps=['Submit add-wearables (transactionType=AW, requestType=TMO) with hostMDN = the VZW Phone MDN.',
          'NSL host-line lookup: NETWORK_PROVIDER=VZW, TMO != VZW -> mismatch.',
          'NSL rejects the request immediately with HTTP 400 + ERR333 (no NE call).'],
   validation='NSL rejects the request with HTTP 400 network-mismatch error. No call made to TMO NE. Defect MWTGTEST-11222 fixed. Error.'),
 dict(scenario_id='TS_MWTGPROV-4167_4', category='Negative',
   title='Verify VZW Add Wearable with a TMO Host MDN is REJECTED',
   prereq='ACTIVE TMO Phone host line (NETWORK_PROVIDER=TMO).',
   steps=['Submit add-wearables (transactionType=AW, requestType=VZW) with hostMDN = the TMO Phone MDN.',
          'NSL host-line lookup: NETWORK_PROVIDER=TMO, VZW != TMO -> mismatch.',
          'NSL rejects with HTTP 400.'],
   validation='NSL rejects the VZW request with HTTP 400 network-mismatch error. Activation does NOT proceed. Cross-network validation applies in both directions (TMO-on-VZW and VZW-on-TMO).'),
 dict(scenario_id='TS_MWTGPROV-4167_5', category='Negative',
   title='Verify NO outbound NE call is made when a VZW host MDN mismatch is detected',
   prereq='VZW host line.\nTMO Add Wearable request.',
   steps=['Submit the mismatched TMO Add Wearable (VZW host)  [POST /nsl/provisioning/wearable/tmo/v1/add-wearables]',
          'Monitor NSL outbound service calls to TMO NE / VZW NE.'],
   validation='The mismatch is caught at NSL validation (fail-fast). No outbound request sent to TMO NE / VZW NE. The 400 network-mismatch error is returned synchronously. Confirms fail-fast, no-NE-call requirement.'),
 dict(scenario_id='TS_MWTGPROV-4167_6', category='Edge Case',
   title='Verify cross-network validation is consistent across Companion Wearable and Standalone Watch flows',
   prereq='VZW host line.',
   steps=['Submit a TMO Companion Wearable (hostMDN = VZW host MDN) -> expect 400.',
          'Submit a TMO Standalone Watch Add referencing a TMO host MDN where a VZW host MDN is present -> expect 400.'],
   validation='Both Companion Wearable and Standalone Watch flows apply the same cross-network validation and reject the mismatched host MDN with HTTP 400. Validation is consistent across both flows.'),
 dict(scenario_id='TS_MWTGPROV-4167_7', category='Edge Case',
   title='Verify host line-state validation (BAU) is unaffected',
   prereq='TMO host line in a non-ACTIVE state (e.g., SUSPEND/DEACTIVE).\nSame-network TMO host MDN that is NOT ACTIVE.',
   steps=['Submit a TMO Add Wearable using the same-network TMO host MDN that is NOT ACTIVE  [POST /nsl/provisioning/wearable/tmo/v1/add-wearables]',
          'Observe that the non-ACTIVE host is still blocked per existing rules.'],
   validation='Existing host line-state validation still applies independently. Non-ACTIVE host blocked per existing rules. New network-match check is a separate, additive validation. Host state and network checks coexist without conflict.'),
]
for s in SC:
    s.setdefault('cdr_input', '')
    s.setdefault('derivation_rule', '')
    s.setdefault('variations', [])

SCOPE = ('[INSLNM, INTG]: CR - New MVNO - NSL accepting TMO Add Wearable request when '
         'VZW Host MDN provided in inbound call')
RULES = ('* When TMO Add Wearable received, NSL validates Host MDN belongs to TMO network before activation.\n'
         '* If Host MDN belongs to VZW (non-TMO), NSL rejects with HTTP 400 + ERR333.\n'
         '* Validation applies to Companion Wearable AND Standalone Watch flows.\n'
         '* Changes applied on both VZW & TMO flows.')

# raw_text MUST contain the feature id or the cache-poisoning guard rejects this row.
RAW = ['Feature: %s' % FID, 'PI: %s' % PI, SCOPE, '', 'Testing Scope', SCOPE, '',
       'Rules', RULES, '', 'Test Scenarios']
for s in SC:
    RAW += ['', '%s  [%s]' % (s['title'], s['category']),
            'Pre-req: %s' % s['prereq'].replace('\n', ' ')]
    RAW += ['Step %d: %s' % (i + 1, st) for i, st in enumerate(s['steps'])]
    RAW += ['Expected Outcome: %s' % s['validation']]
RAW_TEXT = '\n'.join(RAW)

TABLES = [[['Feature', 'Test Area', 'Scenario', 'Category', 'Expected Outcome']] +
          [[FID, 'Add Wearable cross-network Host MDN validation',
            s['title'], s['category'], s['validation']] for s in SC]]

con = sqlite3.connect('tsg_cache.db'); cur = con.cursor()
n46 = cur.execute("DELETE FROM chalk_cache WHERE feature_id=? AND pi_label='PI-46'", (FID,)).rowcount
cur.execute("DELETE FROM chalk_cache WHERE feature_id=? AND pi_label=?", (FID, PI))
cur.execute("INSERT INTO chalk_cache (feature_id, pi_label, scope, rules, scenarios_json,"
            " open_items_json, raw_text, tables_json, last_fetched) VALUES (?,?,?,?,?,?,?,?,?)",
            (FID, PI, SCOPE, RULES, json.dumps(SC), '[]', RAW_TEXT,
             json.dumps(TABLES), datetime.datetime.now().isoformat()))
con.commit()
print('[SEED] bogus PI-46 rows removed: %d' % n46)
print('[SEED] rows for %s: %s' % (FID, cur.execute(
    "SELECT pi_label, length(raw_text) FROM chalk_cache WHERE feature_id=?", (FID,)).fetchall()))
print('[SEED] guard-safe (id in raw_text):', FID in RAW_TEXT)
con.close()
