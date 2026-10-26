"""Hermetic structural tests for mediation/PRR step generation.

Build mediation scenarios (obligation MT->I and a BAU/no-impact regression) and assert the
produced steps contain NONE of the banned NSL-provisioning phrases and DO contain PRR-flow
verbs. Also assert the obligation TC preserves obligation_id + expected_field (R2/R5).
"""
from modules.dimension_extractor import (
    _extract_obligations_from_text, _synthesize_obligation_scenario,
)
from modules.tc_builder import _build_scenario_tc
from modules.data_models_v8 import ExtractedScenario
from modules.traceability import create_traceability
from modules.step_templates import _BANNED_MEDIATION_PHRASES

from tests.test_assertion_grounding import FIXTURE_4628_AC

# A Jira-like carrier whose summary carries a real mediation signal so the batch_file
# class is selected everywhere (classify + fallback flip).
_MEDIATION_SUMMARY = ('[NSLNM, INTG]: TMO PRR Mediation - MMS_TYPE_INDICATOR to '
                      'call_direction_ind in the Amdocs TMO billing record')


class _Jira:
    def __init__(self, key='MWTGPROV-4628'):
        self.key = key
        self.summary = _MEDIATION_SUMMARY
        self.description = ''
        self.acceptance_criteria = FIXTURE_4628_AC


def _all_text(tc):
    return ' '.join(('%s %s' % (s.summary or '', s.expected or '')) for s in tc.steps).lower()


def _assert_no_banned(blob):
    for banned in _BANNED_MEDIATION_PHRASES:
        assert banned not in blob, 'banned NSL phrase leaked into mediation steps: %r' % banned
    # belt-and-suspenders: the literal user-reported artifacts too
    for extra in ('trigger api', 'download century report', 'ne portal',
                  'transaction_history', 'line_history'):
        assert extra not in blob, 'banned phrase leaked: %r' % extra


def test_obligation_mt_to_i_is_prr_flow_no_nsl():
    obs = _extract_obligations_from_text(FIXTURE_4628_AC, 'Jira AC')
    mt = next(o for o in obs if o.get('input_value') == 'MT')
    sc = _synthesize_obligation_scenario(mt, 'Jira AC', 'MWTGPROV-4628')
    tc = _build_scenario_tc(sc, _Jira(), 'TMO PRR Mediation', api_context={})
    blob = _all_text(tc)

    _assert_no_banned(blob)
    # PRR-flow verbs present
    assert ('ingest' in blob or 'place' in blob)
    assert 'mediation' in blob
    assert 'tmo_prr_processing' in blob
    assert 'amdocs' in blob
    assert 'call_direction_ind' in blob

    # R2/R5 preserved: an asserting step carries obligation_id + expected_field
    asserting = [s for s in tc.steps
                 if (s.obligation_id or '') == mt.get('obligation_id')
                 and (s.expected_value or '') == mt.get('expected_value')]
    assert asserting, 'obligation assert step missing'
    assert asserting[0].expected_field == 'call_direction_ind'


def test_bau_no_impact_regression_is_baseline_compare_no_nsl():
    # A plain (no-obligation) regression scenario on the mediation feature.
    tr = create_traceability('Jira AC', 'MWTGPROV-4628',
                             'No impact to Verizon or CBRS billing records; remains BAU')
    sc = ExtractedScenario(
        title='Verify VZW/CBRS billing records remain unchanged (BAU)',
        validation='Verizon and CBRS billing output records remain unchanged versus baseline.',
        category='Regression',
        source=tr,
    )
    tc = _build_scenario_tc(sc, _Jira(), 'TMO PRR Mediation', api_context={})
    blob = _all_text(tc)

    _assert_no_banned(blob)
    assert ('ingest' in blob or 'place' in blob)
    assert 'mediation' in blob
    assert 'amdocs' in blob
    # baseline-compare / unchanged assertion for a no-impact scenario
    assert 'baseline' in blob
    assert ('unchanged' in blob or 'byte-for-byte' in blob or 'field-by-field' in blob)
