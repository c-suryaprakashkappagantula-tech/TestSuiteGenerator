# -*- coding: utf-8 -*-
"""Hermetic unit tests for the QMetry authored-steps supplement.

No network — the QMetry client is monkeypatched and a fixture xlsx (built with
openpyxl in a tmp dir) drives the parser. Covers the design.md §Test strategy
items 2–10 (revision 6).
"""
import os
import sys

import pytest

_TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TSG_ROOT not in sys.path:
    sys.path.insert(0, _TSG_ROOT)

from modules import qmetry_steps_source as qss
from modules.qmetry_steps_source import (
    resolve_project_id, split_packed_steps, _valid_keys,
    parse_qmetry_xlsx, _apply_packed_rule, attach_steps_to_scenarios,
    fetch_authored_steps, enrich_scenarios_with_qmetry,
    QMetrySessionError,
)
from modules.data_models_v8 import ExtractedScenario, make_obligation
from modules.traceability import create_traceability
from modules.tc_builder import _build_obligation_steps, _build_scenario_tc


# ─── Fixture helpers ────────────────────────────────────────────────

def _tr(src_id='MWTGMED-5214'):
    return create_traceability('Subtask AC', src_id, 'authored-step fixture source')


def _scenario(title, validation='', category='Happy Path', obligations=None,
              steps_hint=None, src_id='MWTGMED-5214'):
    return ExtractedScenario(
        title=title, validation=validation, category=category, source=_tr(src_id),
        obligations=list(obligations or []), steps_hint=list(steps_hint or []))


# A 36-col-wide Sheet1 with the six named headers pinned at 0/1/16/17/18/26.
_HEADER = {0: 'Issue Key', 1: 'Summary', 16: 'Step Summary', 17: 'Test Data',
           18: 'Expected Result', 26: 'Story Linkages'}


def _row(cells, width=36):
    """Build a row of `width` cells; `cells` maps 0-based index → value."""
    r = [None] * width
    for idx, val in cells.items():
        r[idx] = val
    return r


def _write_fixture(tmp_path, with_header=True):
    """Build the fixture workbook. Returns the path.

    TCs:
      - MWTGMED-TC-1886: packed single step row, Story Linkages=MWTGMED-5214,
        summary names MT→I.
      - MWTGMED-TC-1887: multi-key linkage cell, single packed step row, MO→O.
      - MWTGMED-TC-2000: TWO explicit step rows, one containing an inline "2. …".
    """
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'

    rows = []
    if with_header:
        hdr = [None] * 36
        for idx, name in _HEADER.items():
            hdr[idx] = name
        # label the other cells so index fallback has something to ignore
        for i in range(36):
            if hdr[i] is None:
                hdr[i] = 'col_%d' % i
        rows.append(hdr)

    # TC-1886 — packed single step cell
    rows.append(_row({
        0: 'MWTGMED-TC-1886',
        1: "MMS_TYPE_INDICATOR value 'MT' is converted to 'I' in the call_direction_ind field",
        16: ('1. Place/ingest the TMO PRR input file containing MMS_TYPE_INDICATOR="MT". '
             '2. Let Mediation run TMO_PRR_Processing_01. '
             '3. Retrieve the processed Amdocs TMO billing output record. '
             '4. Inspect the call_direction_ind field.'),
        17: 'MMS_TYPE_INDICATOR=MT',
        18: "call_direction_ind='I'",
        26: 'MWTGMED-5214',
    }))

    # TC-1887 — multi-key linkage cell, packed single step
    rows.append(_row({
        0: 'MWTGMED-TC-1887',
        1: "MMS_TYPE_INDICATOR value 'MO' is converted to 'O' in the call_direction_ind field",
        16: '1. Ingest MO file. 2. Run Mediation. 3. Inspect call_direction_ind.',
        17: 'MMS_TYPE_INDICATOR=MO',
        18: "call_direction_ind='O'",
        26: 'MWTGMED-5214, MWTGMED-5300',
    }))

    # TC-2000 — two explicit step rows (header row + blank-key continuation row)
    rows.append(_row({
        0: 'MWTGMED-TC-2000',
        1: 'Regression check for billing output',
        16: 'Place input file into the SFTP landing zone',
        17: '',
        18: 'File accepted',
        26: 'MWTGMED-5214',
    }))
    rows.append(_row({
        0: '',   # blank Issue Key → continuation of TC-2000
        16: '2. Validate the downstream Amdocs record is unchanged',
        17: '',
        18: 'Record unchanged',
    }))

    for r in rows:
        ws.append(r)

    path = tmp_path / 'export.xlsx'
    wb.save(str(path))
    return path


# ─── 2. forward-fill grouping ───────────────────────────────────────

def test_forward_fill_grouping(tmp_path):
    parsed = parse_qmetry_xlsx(_write_fixture(tmp_path))
    assert set(parsed) == {'MWTGMED-TC-1886', 'MWTGMED-TC-1887', 'MWTGMED-TC-2000'}
    # TC-2000's blank-key row attaches to it → two step rows.
    assert len(parsed['MWTGMED-TC-2000']['steps']) == 2
    assert parsed['MWTGMED-TC-2000']['steps'][1]['expected'] == 'Record unchanged'
    # single-packed-row TCs keep one raw step row each
    assert len(parsed['MWTGMED-TC-1886']['steps']) == 1


# ─── 3. column resolution by header THEN index fallback ─────────────

def test_column_resolution_by_header_then_index(tmp_path):
    with_hdr = parse_qmetry_xlsx(_write_fixture(tmp_path, with_header=True))
    assert "'MT'" in with_hdr['MWTGMED-TC-1886']['summary']
    assert with_hdr['MWTGMED-TC-1886']['story_linkages'] == ['MWTGMED-5214']

    # build a genuinely header-less sheet (row 1 is treated as header and skipped),
    # so put a throwaway junk header row and assert fallback indices on row 2 data.
    from openpyxl import Workbook
    (tmp_path / 'nohdr').mkdir(exist_ok=True)
    wb = Workbook(); ws = wb.active; ws.title = 'Sheet1'
    ws.append(['x'] * 36)   # junk header row (no matching names → index fallback)
    ws.append(_row({0: 'MWTGMED-TC-9', 1: 'Junk summary', 16: '1. a 2. b',
                    18: 'exp', 26: 'MWTGMED-5214'}))
    p = tmp_path / 'nohdr' / 'e.xlsx'; wb.save(str(p))
    parsed = parse_qmetry_xlsx(p)
    assert 'MWTGMED-TC-9' in parsed
    assert parsed['MWTGMED-TC-9']['story_linkages'] == ['MWTGMED-5214']


# ─── 4. split_packed_steps ──────────────────────────────────────────

def test_split_packed_steps():
    assert split_packed_steps('1. a 2. b 3. c') == ['a', 'b', 'c']
    assert split_packed_steps('just one') == ['just one']
    assert split_packed_steps('') == []


# ─── 4b. ≥2-row TC is NOT re-split ──────────────────────────────────

def test_multirow_tc_not_resplit(tmp_path):
    parsed = _apply_packed_rule(parse_qmetry_xlsx(_write_fixture(tmp_path)))
    # TC-2000 has 2 rows → verbatim, even though row 2 contains an inline "2. ".
    summaries = parsed['MWTGMED-TC-2000']['step_summaries']
    assert summaries == [
        'Place input file into the SFTP landing zone',
        '2. Validate the downstream Amdocs record is unchanged',
    ]
    # single-row packed TC IS split
    assert len(parsed['MWTGMED-TC-1886']['step_summaries']) == 4


# ─── 5. attach MT→I ─────────────────────────────────────────────────

def _authored(tmp_path):
    return _apply_packed_rule(parse_qmetry_xlsx(_write_fixture(tmp_path)))


def test_attach_mt_to_i(tmp_path):
    authored = _authored(tmp_path)
    ob = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    sc = _scenario('MT conversion', obligations=[ob])
    n = attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5214'])
    assert n >= 1
    assert sc.steps_hint and sc.steps_hint[0].startswith('Place/ingest')
    assert len(sc.steps_hint) == 4
    assert sc._qmetry_expected == "call_direction_ind='I'"
    assert sc.obligations == [ob]   # never cleared


# ─── 6. obligation builder consumes the hint ────────────────────────

class _Jira:
    def __init__(self, key='MWTGMED-4628'):
        self.key = key


def test_obligation_builder_consumes_hint(tmp_path):
    authored = _authored(tmp_path)
    ob = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    sc = _scenario('MT conversion', obligations=[ob])
    attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                              validated_keys=['MWTGMED-5214'])
    steps = _build_obligation_steps(sc, {}, jira=_Jira(), feature_name='Mediation')
    # (a) first 4 narrative steps equal the authored hints, in order
    narrative = steps[:4]
    assert [s.summary for s in narrative] == sc.steps_hint
    # (b) authored Expected Result on the last authored narrative step
    assert narrative[-1].expected == "call_direction_ind='I'"
    # (c) interior authored steps carry a non-blank derived expected
    assert all(s.expected for s in narrative[:-1])
    # (d) the obligation assert step still carries obligation_id + its hard expected_value
    asserts = [s for s in steps if s.expected_value == 'I'
               and s.obligation_id == ob.get('obligation_id')]
    assert asserts, 'obligation assert step must survive'


# ─── 6b. dedup preserves the enriched instance ──────────────────────

def test_dedup_preserves_enrichment(tmp_path):
    from modules.data_models_v8 import DimensionSet
    from modules.combination_engine import plan_combinations
    authored = _authored(tmp_path)
    ob = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    sc = _scenario('MT conversion', obligations=[ob])
    attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                              validated_keys=['MWTGMED-5214'])
    ds = DimensionSet(feature_id='MWTGMED-4628', scenarios=[sc])
    plan = plan_combinations(ds, log=lambda *a: None)
    planned = [s for s in plan.scenario_tcs if getattr(s, 'title', '') == 'MT conversion']
    assert planned, 'enriched scenario must appear in the plan'
    assert planned[0] is sc                       # same instance
    assert planned[0].steps_hint == sc.steps_hint
    assert planned[0]._qmetry_expected == "call_direction_ind='I'"


# ─── 6c. tier-2/3 expected override targets the last AUTHORED step ──

def test_tier23_expected_override_filler_path(tmp_path):
    # Non-obligation scenario, TWO authored steps + a non-header validation → the
    # elif branch appends a trailing "Verify:" filler. Override must land on the
    # last AUTHORED step (index 1), NOT on the filler (steps[-1]).
    authored = {'MWTGMED-TC-2000': {
        'summary': 'Regression check for billing output',
        'story_linkages': ['MWTGMED-5214'],
        'steps': [
            {'summary': 'Place input file into the SFTP landing zone', 'test_data': '',
             'expected': 'File accepted'},
            {'summary': 'Validate the downstream Amdocs record is unchanged', 'test_data': '',
             'expected': 'AUTHORED_EXPECTED_VALUE'},
        ],
        'step_summaries': ['Place input file into the SFTP landing zone',
                           'Validate the downstream Amdocs record is unchanged'],
    }}
    sc = _scenario('Regression check for billing output',
                   validation='the billing feed stays BAU with no anomalies')
    n = attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5214'])
    assert n == 1 and len(sc.steps_hint) == 2
    tc = _build_scenario_tc(sc, _Jira(), 'Mediation', api_context={})
    # filler appended → more than 2 steps
    assert len(tc.steps) > 2
    assert tc.steps[1].summary.startswith('Validate the downstream')
    assert tc.steps[1].expected == 'AUTHORED_EXPECTED_VALUE'
    assert tc.steps[-1].expected != 'AUTHORED_EXPECTED_VALUE'   # filler untouched


def test_tier23_expected_override_no_filler_path(tmp_path):
    # ≥3 authored steps → no filler, steps[_n_authored-1] == steps[-1].
    authored = {'MWTGMED-TC-3000': {
        'summary': 'Three step regression',
        'story_linkages': ['MWTGMED-5214'],
        'steps': [
            {'summary': 'Step one here', 'test_data': '', 'expected': 'e1'},
            {'summary': 'Step two here', 'test_data': '', 'expected': 'e2'},
            {'summary': 'Step three here', 'test_data': '', 'expected': 'FINAL_AUTHORED'},
        ],
        'step_summaries': ['Step one here', 'Step two here', 'Step three here'],
    }}
    sc = _scenario('Three step regression', validation='all good')
    attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                              validated_keys=['MWTGMED-5214'])
    tc = _build_scenario_tc(sc, _Jira(), 'Mediation', api_context={})
    assert tc.steps[-1].expected == 'FINAL_AUTHORED'


# ─── 6d. value-pair TC onto an obligation scenario routes through obligation path ──

def test_valuepair_onto_obligation_routes_through_obligation_path():
    # A value-pair TC (MT→I) binds to the MT→I obligation scenario via tier-1 even
    # though the scenario TITLE does not resemble the TC summary — then the obligation
    # builder (touch-point 1) consumes the hint and still stamps obligation_id.
    authored = {'MWTGMED-TC-5000': {
        'summary': "MMS_TYPE_INDICATOR value 'MT' is converted to 'I' in call_direction_ind",
        'story_linkages': ['MWTGMED-5214'],
        'steps': [{'summary': 'Do the thing', 'test_data': '', 'expected': 'done ok'}],
        'step_summaries': ['Do the thing'],
    }}
    ob = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    sc = _scenario('MT conversion', obligations=[ob])   # empty steps_hint
    n = attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5214'])
    assert n == 1 and sc.steps_hint == ['Do the thing']
    steps = _build_obligation_steps(sc, {}, jira=_Jira(), feature_name='Mediation')
    assert steps[0].summary == 'Do the thing'
    asserts = [s for s in steps if s.obligation_id == ob.get('obligation_id')]
    assert asserts, 'obligation_id still stamped via touch-point 1'


# ─── 7. story-linkage is a PRE-FILTER; non-eligible TCs are ineligible ──

def test_story_linkage_prefilter_and_prefix_collision():
    # A TC whose linkage cell holds multiple keys is ELIGIBLE by whole-key equality;
    # with a high-similarity title it then matches via tier-2 similarity.
    authored = {'MWTGMED-TC-1887': {
        'summary': 'Regression check for billing output',
        'story_linkages': ['MWTGMED-5214', 'MWTGMED-5300'],
        'steps': [{'summary': 'do', 'test_data': '', 'expected': 'ok'}],
        'step_summaries': ['do'],
    }}
    sc = _scenario('Regression check for billing output')
    n = attach_steps_to_scenarios([sc], authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5300'])
    assert n == 1 and sc.steps_hint == ['do']

    # prefix collision: MWTGMED-521 must NOT be whole-key-eligible for MWTGMED-5214,
    # so the TC is filtered out (ineligible) and nothing attaches.
    sc2 = _scenario('Regression check for billing output')
    n2 = attach_steps_to_scenarios([sc2], {'X': {
        'summary': 'Regression check for billing output', 'story_linkages': ['MWTGMED-5214'],
        'steps': [{'summary': 'd', 'test_data': '', 'expected': 'o'}],
        'step_summaries': ['d']}}, log=lambda *a: None,
        validated_keys=['MWTGMED-521'])
    assert n2 == 0 and sc2.steps_hint == []


# ─── 8. value-pair precedence: each pair binds its own TC; no positional pairing ──

def test_valuepair_binding_no_positional():
    mt = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    mo = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MO',
                         expected_field='call_direction_ind', expected_value='O')
    # Document order deliberately puts the MO scenario first to prove binding is by
    # value pair, NOT by position.
    mo_sc = _scenario('MO scenario', obligations=[mo])
    mt_sc = _scenario('MT scenario', obligations=[mt])
    authored = {
        'TC_MT': {'summary': "value 'MT' is converted to 'I'",
                  'story_linkages': ['MWTGMED-5214'],
                  'steps': [{'summary': 'mt-step', 'test_data': '', 'expected': 'I'}],
                  'step_summaries': ['mt-step']},
        'TC_MO': {'summary': "value 'MO' is converted to 'O'",
                  'story_linkages': ['MWTGMED-5214'],
                  'steps': [{'summary': 'mo-step', 'test_data': '', 'expected': 'O'}],
                  'step_summaries': ['mo-step']},
    }
    n = attach_steps_to_scenarios([mo_sc, mt_sc], authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5214'])
    assert n == 2
    assert mt_sc.steps_hint == ['mt-step']   # MT TC bound to the MT obligation
    assert mo_sc.steps_hint == ['mo-step']   # MO TC bound to the MO obligation

    # A value pair with no matching obligation is NOT positionally attached — dropped.
    other = _scenario('unrelated', obligations=[])
    n2 = attach_steps_to_scenarios([other], {
        'TC_X': {'summary': "value 'XX' is converted to 'YY'",
                 'story_linkages': ['MWTGMED-5214'],
                 'steps': [{'summary': 'x', 'test_data': '', 'expected': 'y'}],
                 'step_summaries': ['x']}}, log=lambda *a: None,
        validated_keys=['MWTGMED-5214'])
    assert n2 == 0 and other.steps_hint == []


# ─── 8b. NO positional fallback: shared-linkage, N scenarios, M TCs ──

def test_shared_linkage_no_positional_assignment():
    # The exact bug that was found on MWTGPROV-4628: every scenario shares ONE
    # story-linkage key and several QMetry TCs link to it. Value-pair TCs must bind
    # to their exact obligation; a non-value-pair TC binds by similarity or is
    # dropped; NOTHING is assigned positionally.
    mt = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                         expected_field='call_direction_ind', expected_value='I')
    mo = make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MO',
                         expected_field='call_direction_ind', expected_value='O')
    mt_sc = _scenario("MMS_TYPE_INDICATOR value 'MT' is converted to 'I'", obligations=[mt])
    mo_sc = _scenario("MMS_TYPE_INDICATOR value 'MO' is converted to 'O'", obligations=[mo])
    cbrs_sc = _scenario('No impact to Verizon or CBRS billing records')
    noise_sc = _scenario('Totally unrelated functional check')
    scenarios = [cbrs_sc, noise_sc, mt_sc, mo_sc]   # deliberately shuffled

    authored = {
        'TC_MT': {'summary': "MMS_TYPE_INDICATOR value 'MT' is converted to 'I'",
                  'story_linkages': ['MWTGMED-5214'],
                  'steps': [{'summary': 'mt', 'test_data': '', 'expected': 'I'}],
                  'step_summaries': ['mt']},
        'TC_MO': {'summary': "MMS_TYPE_INDICATOR value 'MO' is converted to 'O'",
                  'story_linkages': ['MWTGMED-5214'],
                  'steps': [{'summary': 'mo', 'test_data': '', 'expected': 'O'}],
                  'step_summaries': ['mo']},
        'TC_CBRS': {'summary': 'No impact to Verizon or CBRS billing records',
                    'story_linkages': ['MWTGMED-5214'],
                    'steps': [{'summary': 'cbrs', 'test_data': '', 'expected': 'unchanged'}],
                    'step_summaries': ['cbrs']},
        'TC_VZW': {'summary': 'Regression sweep for some other subsystem entirely',
                   'story_linkages': ['MWTGMED-5214'],
                   'steps': [{'summary': 'vzw', 'test_data': '', 'expected': 'ok'}],
                   'step_summaries': ['vzw']},
    }
    n = attach_steps_to_scenarios(scenarios, authored, log=lambda *a: None,
                                  validated_keys=['MWTGMED-5214'])
    # Value-pair TCs land on their EXACT obligation scenario.
    assert mt_sc.steps_hint == ['mt']
    assert mo_sc.steps_hint == ['mo']
    # CBRS TC binds by similarity (title == summary).
    assert cbrs_sc.steps_hint == ['cbrs']
    # The unrelated regression TC clears no similarity bar → dropped, NOT attached
    # to the leftover noise scenario positionally.
    assert noise_sc.steps_hint == []
    assert n == 3


# ─── 9. unknown project skips ───────────────────────────────────────

def test_unknown_project_skips():
    assert resolve_project_id('FOO-1') is None
    sc = _scenario('untouched', steps_hint=[])
    jira = _Jira('FOO-1')
    jira.subtasks = [{'key': 'FOO-2'}]
    n = enrich_scenarios_with_qmetry([sc], jira, {}, log=lambda *a: None)
    assert n == 0 and sc.steps_hint == []


# ─── 9b. blank subtask key never drives a match ─────────────────────

def test_blank_subtask_key_does_not_match(monkeypatch, tmp_path):
    # Hermetic isolation: clear the per-run caches and redirect the disk cache
    # into tmp so a prior test (or prior pytest run) can never pre-seed a hit.
    qss._EXPORT_CACHE.clear()
    qss._FOLDER_MEMO.clear()
    monkeypatch.setattr(qss, '_disk_cache_path',
                        lambda pid, fid: tmp_path / ('%s_%s.json' % (pid, fid)))
    calls = {'export': 0}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_testcase_folders(self):
            # first folder is an unrelated iteration node; the valid key folder second
            return [{'id': 1, 'name': 'Iteration 60.1', 'children': [
                {'id': 2, 'name': 'MWTGMED-5214', 'children': []}]}]

        def export_testcases_v2(self, folder_id=None):
            calls['export'] += 1
            calls['folder'] = folder_id
            from modules.qmetry_steps_source import QMetryFetchError
            # return a RestExportResult-like stub pointing at our fixture
            class _R:
                status = 'success'
                file_path = _write_fixture(tmp_path)
                error = None
            return _R()

    import qmetry_rest_client
    monkeypatch.setattr(qmetry_rest_client, 'QMetryRestClient', _FakeClient)

    # (a) blank keys skipped, only MWTGMED-5214 drives the match → folder id 2
    out = fetch_authored_steps('76000', 'MWTGMED-0',
                               ['', '  ', 'MWTGMED-5214'], cookies={}, log=lambda *a: None)
    assert calls['folder'] == '2'
    assert out   # parsed TCs returned

    # (b) only blank keys + unmatched feature → {} (no export)
    calls['export'] = 0
    out2 = fetch_authored_steps('76000', 'FOO-0', ['', '  '], cookies={}, log=lambda *a: None)
    assert out2 == {} and calls['export'] == 0

    # (c) tier-2 with a blank key in the set does not match
    sc = _scenario('x')
    n = attach_steps_to_scenarios([sc], {'TC': {
        'summary': 'g', 'story_linkages': ['MWTGMED-5214'],
        'steps': [{'summary': 'd', 'test_data': '', 'expected': 'o'}],
        'step_summaries': ['d']}}, log=lambda *a: None,
        validated_keys=_valid_keys(['', '  ']))
    assert n == 0 and sc.steps_hint == []


# ─── 10. no session is fail-safe at the engine hook ─────────────────

def test_no_session_is_fail_safe(monkeypatch):
    def _boom(*a, **k):
        raise QMetrySessionError('no session')
    monkeypatch.setattr(qss, 'load_qmetry_cookies', _boom)

    sc = _scenario('MT conversion', obligations=[
        make_obligation(input_field='MMS_TYPE_INDICATOR', input_value='MT',
                        expected_field='call_direction_ind', expected_value='I')])
    jira = _Jira('MWTGMED-4628')
    jira.subtasks = [{'key': 'MWTGMED-5214'}]

    # enrich_scenarios_with_qmetry propagates QMetrySessionError; the engine hook
    # swallows it. Emulate the hook's try/except here.
    raised = False
    try:
        enrich_scenarios_with_qmetry([sc], jira, {}, log=lambda *a: None)
    except QMetrySessionError:
        raised = True
    assert raised                    # the entry point surfaces it for the hook to catch
    assert sc.steps_hint == []       # scenario untouched
