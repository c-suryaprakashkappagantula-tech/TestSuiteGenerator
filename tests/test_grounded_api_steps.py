"""Hermetic tests for grounded NSL/API step generation (engine-wide accuracy fix).

Pattern mirrors tests/test_mediation_steps_structural.py: build scenarios directly through
step_templates.get_step_chain / tc_builder._build_scenario_tc with a tiny Jira-like carrier —
no Jira/Chalk/browser needed. These pin the ROOT accuracy defect:

  (7a) an API/NSL scenario names its OWN operation and NO foreign operation;
  (7b) the 3 MWTGPROV-4574 cancel-device-location scenarios -> 3 distinct TCs, the
       request-variant one classified Negative (the 3->2 fix);
  (7c) the generic "sends outbound call to downstream system" filler never appears for an
       operation-named scenario, and the operation name IS present;
  (7d) a Mediation scenario still yields PRR steps with 0 banned NSL phrases.
"""
from modules.step_templates import (
    get_step_chain, _operation_from_scenario, _grounded_api_steps,
    _is_hotline_flow, _BANNED_MEDIATION_PHRASES,
)
from modules.tc_builder import _build_scenario_tc
from modules.data_models_v8 import ExtractedScenario
from modules.traceability import create_traceability
from modules.dimension_extractor import _extract_obligations_from_text, _synthesize_obligation_scenario

from tests.test_assertion_grounding import FIXTURE_4628_AC


# The three real MWTGPROV-4574 Chalk scenario titles (NSL cancel-device-location API).
_SC0 = ('The NSL cancel-device-location API is deployed. A TMO subscriber line is ACTIVE and '
        'its SIM & MDN are active. fetch-line-details returns status=ACTIVE, and the NE '
        'cancel-device-location stub is primed to return SUCCESS.')
_SC1 = ('The NSL cancel-device-location API is deployed. Prepare TMO lines in each non-ACTIVE '
        'status (DEACTIVE, HOTLINE, SUSPEND) plus an MDN/ICCID for which no line exists.')
_SC2 = ('Prepare request variants: (a) non-TMO requestType header; (b) schema/field '
        'violations; (c) a request omitting both MDN and ICCID.')

_FOREIGN = ('hotline', 'transactiontype=sh', 'apollo_ne', 'itmbo', 'emm',
            'syniverse', 'century report', 'service grouping', 'ne portal',
            'sends outbound call to downstream system')


class _Jira:
    def __init__(self, key='MWTGPROV-4574',
                 summary='[NSLNM, INTG]: NSL cancel-device-location API'):
        self.key = key
        self.summary = summary
        self.description = 'cancel-device-location'
        self.acceptance_criteria = ''
        self.channel = 'NSL'
        self.priority = 'Medium'
        self.pi = 'PI-60'


def _chain_text(chain):
    return ' '.join('%s %s' % (s, e) for s, e in chain).lower()


# ── 7a — a scenario names its OWN operation, never a foreign one ──────────────

def test_operation_extraction_reads_hyphenated_operation():
    assert _operation_from_scenario(_SC0) == 'cancel-device-location'
    assert _operation_from_scenario('Verify fetch-line-details returns status') == 'fetch-line-details'
    # a bare status enumeration must not be read as an operation
    assert _operation_from_scenario('non-ACTIVE status (DEACTIVE, HOTLINE, SUSPEND)') == ''


def test_api_scenario_names_own_operation_not_foreign():
    chain = get_step_chain(_SC0, '', 'cancel-device-location NSL', feature_type='api')
    blob = _chain_text(chain)
    assert 'cancel-device-location' in blob
    for bad in _FOREIGN:
        assert bad not in blob, 'foreign operation leaked into cancel-device-location steps: %r' % bad


def test_status_list_scenario_not_routed_to_hotline():
    # SC1 mentions HOTLINE only inside a non-ACTIVE status enumeration — it must NOT be a
    # hotline flow, and its steps must name cancel-device-location, not a Hotline/SH flow.
    t = (_SC1 + ' ').lower()
    assert not _is_hotline_flow(t, '')
    chain = get_step_chain(_SC1, '', 'cancel-device-location NSL', feature_type='api')
    blob = _chain_text(chain)
    assert 'cancel-device-location' in blob
    # SC1 legitimately NAMES 'HOTLINE' as a required line STATE to prepare — that word is
    # grounded in the scenario's own data. What must NOT appear is the Hotline FLOW template
    # (SH transaction / APOLLO_NE / ITMBO-EMM / Century Service-Grouping), i.e. a foreign API.
    for bad in ('transactiontype=sh', 'apollo_ne', 'itmbo', 'emm', 'syniverse',
                'century report', 'service grouping', 'ne portal',
                'sends outbound call to downstream system'):
        assert bad not in blob, 'foreign Hotline/API flow leaked: %r' % bad


# ── 7b — three distinct scenarios -> three distinct, correctly-typed TCs ──────

def test_three_4574_scenarios_yield_three_distinct_tcs():
    jira = _Jira()
    cats = []
    summaries = []
    step_blobs = []
    for i, title in enumerate((_SC0, _SC1, _SC2)):
        tr = create_traceability(source_type='Chalk Scenario', source_id='MWTGPROV-4574',
                                 extracted_text=title)
        # SC2's rejection vocabulary -> Negative (mirrors the extractor's re-labelling).
        import re as _re
        _cat = 'Happy Path'
        if _re.search(r'\b(?:violation|violations|schema\s*/?\s*field|omit|omitting|non-tmo|'
                      r'invalid|reject)\b', title.lower()):
            _cat = 'Negative'
        sc = ExtractedScenario(title=title, validation='', category=_cat, source=tr)
        tc = _build_scenario_tc(sc, jira, 'cancel-device-location', None, {}, {},
                                feature_type='api')
        cats.append(tc.category)
        summaries.append(tc.summary)
        step_blobs.append(' '.join((s.summary or '') for s in tc.steps).lower())

    # SC2 is the rejection scenario -> Negative; the other two stay positive.
    assert cats[2] == 'Negative'
    assert cats[0] != 'Negative' and cats[1] != 'Negative'
    # every TC's steps name the operation under test
    for blob in step_blobs:
        assert 'cancel-device-location' in blob
    # the three TCs are distinct tests (step content differs; SC2 asserts rejection)
    assert 'reject' in step_blobs[2]
    assert len(set(step_blobs)) == 3, 'the three scenarios collapsed to identical steps'


def test_4574_preconditions_name_operation_not_generic_boilerplate():
    jira = _Jira()
    tr = create_traceability(source_type='Chalk Scenario', source_id='MWTGPROV-4574',
                             extracted_text=_SC0)
    sc = ExtractedScenario(title=_SC0, validation='', category='Happy Path', source=tr)
    tc = _build_scenario_tc(sc, jira, 'cancel-device-location', None, {}, {}, feature_type='api')
    pc = (tc.preconditions or '').lower()
    assert 'cancel-device-location' in pc
    # the generic boilerplate is gone for an operation-named API feature
    assert 'api endpoint accessible' not in pc
    assert 'mno_tmo permission' not in pc


# ── 7c — no generic filler for an operation-named scenario ────────────────────

def test_no_generic_downstream_filler_for_operation_named_scenario():
    chain = get_step_chain(_SC0, '', 'cancel-device-location NSL', feature_type='api')
    blob = _chain_text(chain)
    assert 'sends outbound call to downstream system' not in blob
    assert 'processes response and prepares output' not in blob
    # and the operation IS named (semantic, not just a banned-phrase scan)
    assert 'cancel-device-location' in blob


def test_rejection_scenario_is_grounded_rejection_not_default_boilerplate():
    # SC2 carries no specific negative keyword that _is_negative matches, yet it must become a
    # grounded rejection chain for the feature's operation, not OAuth/Century/NE-Portal default.
    chain = get_step_chain(_SC2, '', 'cancel-device-location NSL', feature_type='api')
    blob = _chain_text(chain)
    assert 'cancel-device-location' in blob
    assert 'reject' in blob
    for bad in ('oauth', 'century report', 'ne portal', 'transaction_history'):
        assert bad not in blob, 'default boilerplate leaked into rejection steps: %r' % bad


# ── 7d — mediation still yields PRR steps, 0 banned NSL phrases ───────────────

def test_mediation_scenario_still_prr_no_banned():
    class _JiraMed:
        key = 'MWTGPROV-4628'
        summary = ('[NSLNM, INTG]: TMO PRR Mediation - MMS_TYPE_INDICATOR to '
                   'call_direction_ind in the Amdocs TMO billing record')
        description = ''
        acceptance_criteria = FIXTURE_4628_AC

    obs = _extract_obligations_from_text(FIXTURE_4628_AC, 'Jira AC')
    mt = next(o for o in obs if o.get('input_value') == 'MT')
    sc = _synthesize_obligation_scenario(mt, 'Jira AC', 'MWTGPROV-4628')
    tc = _build_scenario_tc(sc, _JiraMed(), 'TMO PRR Mediation', api_context={})
    blob = ' '.join('%s %s' % (s.summary or '', s.expected or '') for s in tc.steps).lower()

    for banned in _BANNED_MEDIATION_PHRASES:
        assert banned not in blob, 'banned NSL phrase leaked into mediation steps: %r' % banned
    assert ('ingest' in blob or 'place' in blob)
    assert 'mediation' in blob
    assert 'amdocs' in blob
    # and definitely NOT the grounded-API operation-call phrasing
    assert 'cancel-device-location' not in blob
