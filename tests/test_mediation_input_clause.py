"""Hermetic tests for the PRR/mediation step-1 input-file clause rule.

Covers the precise branching in step_templates._prr_input_file_clause / _prr_mediation_steps:
  - A real obligation value-pair -> clause names the authentic FIELD='VALUE' (TC2/TC3 wording).
  - No value-pair (no-loss / e2e / other-absent) -> clean generic clause, NO fabricated '=' pair.
  - VZW / CBRS -> the vendor-specific clause.
And the negative guard: no produced step-1 contains a fabricated "<word> = <word>" token.
"""
import re

from modules.step_templates import (
    get_step_chain, _prr_mediation_steps, _prr_input_file_clause,
)
from modules.tc_builder import _build_scenario_tc
from modules.data_models_v8 import ExtractedScenario
from modules.traceability import create_traceability

from tests.test_assertion_grounding import FIXTURE_4628_AC

_MEDIATION_SUMMARY = ('[NSLNM, INTG]: TMO PRR Mediation - MMS_TYPE_INDICATOR to '
                      'call_direction_ind in the Amdocs TMO billing record')

# A fabricated "<word> = <word>" token: lowercase word, spaced '=', lowercase word.
# The only legitimate '=' is the real FIELD='VALUE' form (uppercase field, quoted value).
_FABRICATED_PAIR_RE = re.compile(r'\b[a-z]+\s*=\s*[a-z]+\b')
# The real pair form, e.g. MMS_TYPE_INDICATOR='MT'
_REAL_PAIR_RE = re.compile(r"[A-Z][A-Z0-9_]+='[^']+'")


class _Jira:
    def __init__(self, key='MWTGPROV-4628'):
        self.key = key
        self.summary = _MEDIATION_SUMMARY
        self.description = ''
        self.acceptance_criteria = FIXTURE_4628_AC


def _ob(field, value, exp_field='call_direction_ind', exp_value='I'):
    return {'input_field': field, 'input_value': value, 'expected_field': exp_field,
            'expected_value': exp_value, 'kind': 'mapping',
            'obligation_id': '%s:%s' % (field, value), 'condition': '', 'source_ref': 'Jira AC'}


def _chain(title, validation, obligation=None):
    t = (title + ' ' + (validation or '')).lower()
    return _prr_mediation_steps(title, validation, t, 'tmo prr mediation', obligation=obligation)


def _assert_no_fabricated_pair(step1_text):
    """step-1 may contain the real FIELD='VALUE' form but never a fabricated 'word = word'."""
    stripped = _REAL_PAIR_RE.sub('', step1_text)
    assert not _FABRICATED_PAIR_RE.search(stripped), (
        'fabricated "<word> = <word>" token in step 1: %r' % step1_text)


# ── Real value-pair cases (TC2/TC3 wording preserved) ──────────────────────────

def test_real_pair_mt_names_field_value():
    steps = _chain("MMS_TYPE_INDICATOR 'MT' to call_direction_ind 'I'",
                   "call_direction_ind = I in the produced record.",
                   obligation=_ob('MMS_TYPE_INDICATOR', 'MT'))
    s1 = steps[0][0]
    assert "MMS_TYPE_INDICATOR='MT'" in s1, s1
    # exactly one '=' and it is the real field='value' form
    assert s1.count('=') == 1
    assert _REAL_PAIR_RE.search(s1)
    _assert_no_fabricated_pair(s1)


def test_real_pair_mo_names_field_value():
    steps = _chain("MMS_TYPE_INDICATOR 'MO' to call_direction_ind 'O'",
                   "call_direction_ind = O in the produced record.",
                   obligation=_ob('MMS_TYPE_INDICATOR', 'MO', exp_value='O'))
    s1 = steps[0][0]
    assert "MMS_TYPE_INDICATOR='MO'" in s1, s1
    _assert_no_fabricated_pair(s1)


# ── No value-pair → clean generic clause, no fabricated '=' ────────────────────

def test_no_loss_generic_tmo_clause_no_pair():
    steps = _chain("No loss of existing functionality or degradation",
                   "Output records unchanged versus baseline.")
    s1 = steps[0][0]
    assert s1 == 'Place/ingest a representative TMO PRR input file onto the biller SFTP.', s1
    assert '=' not in s1
    _assert_no_fabricated_pair(s1)


def test_e2e_conversion_title_no_obligation_no_fabricated_pair():
    steps = _chain("Validate the 'MT' to 'I' conversion end to end for billing",
                   "Conversion applied end to end.")
    s1 = steps[0][0]
    # Must NOT fabricate 'Validate = the' (the old regex artifact).
    assert 'validate = the' not in s1.lower(), s1
    assert '=' not in s1
    _assert_no_fabricated_pair(s1)


def test_other_absent_non_mms_clause():
    steps = _chain("Handle input other/absent -> output BAU",
                   "BAU preserved for other/absent input.")
    s1 = steps[0][0]
    assert 'non-MMS / other MMS_TYPE_INDICATOR value' in s1, s1
    assert '=' not in s1
    _assert_no_fabricated_pair(s1)


# ── Vendor-specific clauses ────────────────────────────────────────────────────

def test_vzw_clause():
    steps = _chain("Verizon (VZW) billing records remain unchanged",
                   "VZW records unchanged versus baseline.")
    s1 = steps[0][0]
    assert s1 == 'Place/ingest a Verizon (VZW) PRR input file onto the biller SFTP.', s1
    _assert_no_fabricated_pair(s1)


def test_cbrs_clause():
    steps = _chain("CBRS billing records remain unchanged",
                   "CBRS records unchanged versus baseline.")
    s1 = steps[0][0]
    assert s1 == 'Place/ingest a CBRS PRR input file onto the biller SFTP.', s1
    _assert_no_fabricated_pair(s1)


# ── Helper rule unit (direct) ──────────────────────────────────────────────────

def test_synthesized_other_absent_obligation_is_not_a_real_pair():
    # A synthesized no-impact obligation carries input_value='other/absent' — not a real pair.
    clause = _prr_input_file_clause(
        {'input_field': 'MMS_TYPE_INDICATOR', 'input_value': 'other/absent'},
        'some title', 'some validation', 'some title some validation')
    assert '=' not in clause, clause
    _assert_no_fabricated_pair(clause)


# ── Full-builder negative guard: no fabricated pair in any produced step ───────

def test_no_fabricated_pair_in_full_builder_tcs():
    titles = [
        ("No loss of existing functionality or degradation", "Output unchanged versus baseline."),
        ("Validate the 'MT' to 'I' conversion end to end", "Conversion applied end to end."),
        ("Handle input other/absent -> output BAU", "BAU preserved for other/absent."),
        ("Verizon (VZW) billing records remain unchanged", "VZW unchanged."),
        ("CBRS billing records remain unchanged", "CBRS unchanged."),
    ]
    for ttl, val in titles:
        tr = create_traceability('Jira AC', 'MWTGPROV-4628', val)
        sc = ExtractedScenario(title=ttl, validation=val, category='Regression', source=tr)
        tc = _build_scenario_tc(sc, _Jira(), 'TMO PRR Mediation', api_context={})
        for st in tc.steps:
            summ = st.summary or ''
            if summ.lower().startswith('place/ingest'):
                _assert_no_fabricated_pair(summ)


def test_get_step_chain_threads_obligation():
    # The public entrypoint must forward the obligation to the mediation builder.
    chain = get_step_chain(
        "MMS_TYPE_INDICATOR 'MT' to call_direction_ind 'I'",
        "call_direction_ind = I in the produced record.",
        'tmo prr mediation', feature_type='batch_file',
        obligation=_ob('MMS_TYPE_INDICATOR', 'MT'))
    assert "MMS_TYPE_INDICATOR='MT'" in chain[0][0], chain[0][0]
