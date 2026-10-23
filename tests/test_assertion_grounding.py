"""Hermetic tests for the R1-R5 assertion-grounding redesign.

Pure unit tests: hand-built data_models_v8 / ChalkData fixtures, no tsg_cache.db, no
`shared` package, no network. They collect and run regardless of the 28 pre-existing
`shared` collection errors documented in tests/_baseline_failures.txt.

Tests A, A-Chalk, B, B-terse, C, D, E, F per design.md §8.
"""
import os
import re
import glob

import pytest

from modules.dimension_extractor import (
    _extract_obligations_from_text, _chalk_obligation_text,
    _synthesize_obligation_scenario,
)
from modules.chalk_parser import ChalkData, ChalkScenario
from modules.tc_builder import _build_scenario_tc
from modules.grounding_scorer import score_tc, gate_suite, suite_grounding_pct, GATE_THRESHOLD
from modules.data_models_v8 import TestStep, TestCase
from modules.traceability import create_traceability


_MODULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'modules')


FIXTURE_4628_AC = """{panel:title=Functional Requirements}
 * MMS_TYPE_INDICATOR value 'MT' is converted to 'I' (capital letter I) in the call_direction_ind field of the Amdocs TMO billing record.
 * MMS_TYPE_INDICATOR value 'MO' is converted to 'O' (capital letter O) in the call_direction_ind field of the Amdocs TMO billing record.
 * All other fields and values in the TMO billing records remain as BAU.
 * No impact to Verizon or CBRS billing records.
{panel}
"""

# The terse source form carried by the feature summary.
FIXTURE_4628_TERSE = """'MT' -> call_direction_ind='I'
'MO' -> call_direction_ind='O'
any other/absent value is BAU"""

FIXTURE_4167_AC = """* When TMO Add Wearable received, NSL validates Host MDN belongs to TMO network before activation.
* If Host MDN belongs to VZW (non-TMO), NSL rejects with HTTP 400 + ERR333.
"""


class _Jira:
    def __init__(self, key='MWTGPROV-4628'):
        self.key = key


# ── Test A — one obligation per mapping row incl. MO->O (Jira path) ──

def test_A_jira_path_one_obligation_per_row_incl_mo():
    obs = _extract_obligations_from_text(FIXTURE_4628_AC, 'Jira AC')
    mapping = [o for o in obs if o.get('kind') == 'mapping']
    assert len(mapping) == 3
    assert {o.get('input_value') for o in mapping} >= {'MT', 'MO', 'other/absent'}
    mo = next(o for o in mapping if o.get('input_value') == 'MO')
    assert mo.get('expected_value') == 'O'
    assert mo.get('expected_field') == 'call_direction_ind'


# ── Test A-Chalk — MO->O produced through the Chalk path ──

def test_A_chalk_path_produces_mo_obligation():
    chalk = ChalkData(feature_id='MWTGPROV-4628', scope='', rules=FIXTURE_4628_AC)
    obs = _extract_obligations_from_text(_chalk_obligation_text(chalk), 'Chalk Scenario')
    mapping = [o for o in obs if o.get('kind') == 'mapping']
    assert len(mapping) == 3
    assert {o.get('input_value') for o in mapping} >= {'MT', 'MO', 'other/absent'}


def test_A_chalk_per_scenario_derivation_rule_is_mined():
    chalk = ChalkData(
        feature_id='MWTGPROV-4628',
        scenarios=[ChalkScenario(derivation_rule="'MT' -> call_direction_ind='I'")],
    )
    obs = _extract_obligations_from_text(_chalk_obligation_text(chalk), 'Chalk Scenario')
    mapping = [o for o in obs if o.get('kind') == 'mapping']
    assert mapping, 'per-scenario derivation_rule carrier must be mined'
    mt = next(o for o in mapping if o.get('input_value') == 'MT')
    assert mt.get('expected_field') == 'call_direction_ind'
    assert mt.get('expected_value') == 'I'


# ── Test B — each generated TC asserts its obligation's expected output ──

def test_B_generated_tc_asserts_expected_value_with_field():
    obs = _extract_obligations_from_text(FIXTURE_4628_AC, 'Jira AC')
    mapping = [o for o in obs if o.get('kind') == 'mapping'
               and o.get('input_value') in ('MT', 'MO')]
    assert len(mapping) == 2
    for ob in mapping:
        sc = _synthesize_obligation_scenario(ob, 'Jira AC', 'MWTGPROV-4628')
        tc = _build_scenario_tc(sc, _Jira(), 'feature', api_context={})
        matching = [s for s in tc.steps
                    if (s.expected_value or '') == ob.get('expected_value')
                    and (s.obligation_id or '') == ob.get('obligation_id')]
        assert matching, 'TC must carry an asserting step for %s' % ob.get('obligation_id')
        assert matching[0].expected_field == 'call_direction_ind'
        assert ob.get('obligation_id') in tc.obligation_ids


# ── Test B-terse — the terse source form splits correctly ──

def test_B_terse_inline_field_value_split():
    obs = _extract_obligations_from_text(FIXTURE_4628_TERSE, 'Jira AC')
    mt = next(o for o in obs if o.get('input_value') == 'MT')
    mo = next(o for o in obs if o.get('input_value') == 'MO')
    assert mt.get('expected_field') == 'call_direction_ind'
    assert mt.get('expected_value') == 'I'      # NOT the un-split "call_direction_ind='I'"
    assert mo.get('expected_field') == 'call_direction_ind'
    assert mo.get('expected_value') == 'O'


# ── Test C — flag ON caps an unasserted-obligation TC below the gate ──

def _unasserted_tc():
    tr = create_traceability('Jira AC', 'MWTGPROV-4628', 'MMS_TYPE_INDICATOR MO -> O')
    steps = [
        TestStep(step_num=1, summary='Prepare the system and submit the request',
                 expected='Request reaches the component under test and is accepted.'),
        TestStep(step_num=2, summary='Observe the produced record downstream',
                 expected='A record is produced in the TMO billing feed for inspection.'),
        TestStep(step_num=3, summary='Review the processing log for anomalies',
                 expected='No processing anomalies are logged during the run.'),
    ]
    tc = TestCase(summary='Verify MMS_TYPE_INDICATOR=MO transform', steps=steps,
                  traceability=tr, category='Happy Path')
    tc.obligation_ids = ['MMS_TYPE_INDICATOR:MO']
    return tc


def test_C_flag_on_caps_unasserted_tc_and_gate_drops_it(monkeypatch):
    monkeypatch.setenv('TSG_STRICT_ASSERTION_GATE', '1')
    assert score_tc(_unasserted_tc()) < GATE_THRESHOLD
    assert score_tc(_unasserted_tc(), strict_override=True) < GATE_THRESHOLD
    assert gate_suite([_unasserted_tc()], threshold=GATE_THRESHOLD, log=lambda *a: None) == []
    # strict_override=True gates identically even if the env were unset.
    monkeypatch.delenv('TSG_STRICT_ASSERTION_GATE', raising=False)
    assert gate_suite([_unasserted_tc()], threshold=GATE_THRESHOLD, log=lambda *a: None,
                      strict_override=True) == []


# ── Test D — flag OFF is byte-for-byte unchanged (no-obligation suite) ──

def _no_obligation_tc():
    tr = create_traceability('Jira AC', 'MWTGPROV-9999',
                             'some real ac line about provisioning a line')
    steps = [
        TestStep(step_num=1, summary='Set up TMO subscriber line in SIT',
                 expected='Subscriber line is ready and provisioned for the test scenario.'),
        TestStep(step_num=2, summary='Trigger the provisioning API with a valid request',
                 expected='API accepts the request and returns HTTP 200 with a success body.'),
        TestStep(step_num=3, summary='Verify the downstream record reflects the change',
                 expected='Downstream TRANSACTION_HISTORY row shows the expected provisioned state.'),
    ]
    return TestCase(summary='Verify provisioning succeeds for an active line',
                    steps=steps, traceability=tr, category='Happy Path')


def test_D_flag_off_no_obligation_identity(monkeypatch):
    """A failure here means the OFF path is no longer identity — a release blocker.

    Frozen golden integers/floats computed from the pre-change formula (+35/+25/+20/+10/+10).
    """
    monkeypatch.delenv('TSG_STRICT_ASSERTION_GATE', raising=False)
    tc = _no_obligation_tc()
    assert score_tc(tc) == 100                      # frozen golden
    assert score_tc(_no_obligation_tc(), strict_override=False) == 100
    # Advisory coverage attached, but it does not change the score.
    assert getattr(tc, '_assertion_coverage', None) == (0, 0)
    kept = gate_suite([_no_obligation_tc(), _no_obligation_tc()],
                      threshold=GATE_THRESHOLD, log=lambda *a: None)
    assert len(kept) == 2                            # identical kept-set
    assert suite_grounding_pct(kept) == 100.0        # frozen golden float


# ── Test E — error-rule obligation (4167) ──

def test_E_error_rule_obligation_and_tc_step():
    obs = _extract_obligations_from_text(FIXTURE_4167_AC, 'Jira AC')
    err = [o for o in obs if o.get('kind') == 'error_rule']
    assert err, 'expected an error_rule obligation'
    assert 'ERR333' in (err[0].get('expected_value') or '')
    sc = _synthesize_obligation_scenario(err[0], 'Jira AC', 'MWTGPROV-4167')
    tc = _build_scenario_tc(sc, _Jira('MWTGPROV-4167'), 'feature', api_context={})
    assert any('ERR333' in (s.expected_value or '') for s in tc.steps)


# ── Test F — single flag read-point (env var AND option key) ──

def _module_sources():
    out = {}
    for path in glob.glob(os.path.join(_MODULES_DIR, '*.py')):
        with open(path, 'r', encoding='utf-8') as f:
            out[os.path.basename(path)] = f.read()
    return out


def test_F_single_flag_read_point():
    sources = _module_sources()
    env_hits = [name for name, text in sources.items() if 'TSG_STRICT_ASSERTION_GATE' in text]
    assert env_hits == ['config.py'], 'env var must be read only in config.py: %s' % env_hits

    opt_hits = [name for name, text in sources.items()
                if re.search(r"""['"]strict_assertion_gate['"]""", text)]
    assert opt_hits == ['data_first_engine.py'], \
        "'strict_assertion_gate' option key read in exactly one module: %s" % opt_hits
