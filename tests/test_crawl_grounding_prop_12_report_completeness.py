# -*- coding: utf-8 -*-
"""Property test: Grounding Report completeness and ``[CRAWL-GROUND]`` log
consistency (task 9.4).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 12: Report completeness and log consistency
**Validates: Requirements 6.2, 6.3, 6.4, 9.6**

Two halves, both hermetic (no Jira/Chalk/cache/browser/crawl-map read):

1. **Report completeness** — for any ``GroundingResult`` (hypothesis-built over
   the integer metric fields and a list of ``GroundingIssue``s),
   ``_build_grounding_report_sheet(wb, result)`` writes a 'Grounding Report'
   sheet whose issue table has EXACTLY one row per ``GroundingIssue`` (header
   ``['TC Number','Step Number','Step UID','Step Text','Reason','Candidate Pages']``,
   candidates comma-joined) and whose metrics block carries the SAME
   ui_steps/grounded/unmatched/filler/closers_removed/typed/total values as the
   result (no drift), with the derived shares equal to ``_pct(grounded, ui_steps)``
   and ``_pct(typed, total)``.

2. **Log consistency** — the single ``[CRAWL-GROUND]`` summary line emitted for a
   suite carries the SAME numbers as the Grounding Report sheet. We drive
   ``generate_excel`` with ``apply_crawl_grounding`` monkeypatched to attach a
   hypothesis-built ``GroundingResult`` to the suite (output/checkpoint paths
   redirected to ``tmp_path``), capture the log via a list, and assert the parsed
   tokens from the ``[CRAWL-GROUND] ... ui_steps=`` line match the sheet's metrics
   in the written workbook.

Each example builds (and in part 2 writes) a workbook, so examples are modest
and ``deadline`` is disabled.
"""
import os
import re
import sys

import openpyxl
from hypothesis import given, settings, strategies as st

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TSG_ROOT = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(_TSG_ROOT)
for _p in (_TESTS_DIR, _TSG_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import modules.excel_generator as eg  # noqa: E402
import modules.crawl_grounding as cg  # noqa: E402
from modules.excel_generator import (  # noqa: E402
    GROUNDING_REPORT_SHEET, _build_grounding_report_sheet, _pct,
)
from modules.crawl_grounding import GroundingResult, GroundingIssue  # noqa: E402
from modules.data_models_v8 import TestSuite, TestCase, TestStep, RoutingAudit  # noqa: E402

_ISSUE_HEADER = [
    'TC Number', 'Step Number', 'Step UID', 'Step Text', 'Reason', 'Candidate Pages',
]
_REASONS = ('no_match', 'ambiguous', 'crawl_unavailable', 'action_unsupported',
            'empty_after_closer', 'grounding_error')


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
# Metric integers: allow 0 and realistic ranges. 0 denominators (ui_steps=0,
# total_rows=0) are reachable, so '0%' shares are exercised.
_metric = st.integers(min_value=0, max_value=500)

# Plain cell text: no surrogates, control chars, or a leading '=' (openpyxl
# would store a leading '=' as a formula and the round-trip would not be
# faithful — irrelevant to the comma-join / completeness logic under test).
_cell_text = st.text(
    st.characters(blacklist_categories=('Cs', 'Cc'), blacklist_characters='\x00'),
    min_size=0, max_size=24,
).filter(lambda s: not s.startswith('='))

# Candidate tuples: empty, single, and multiple entries so the comma-join path
# is exercised in both directions.
_candidates = st.lists(_cell_text, min_size=0, max_size=4).map(tuple)


@st.composite
def _issues(draw):
    n = draw(st.integers(min_value=0, max_value=8))
    out = []
    for _ in range(n):
        out.append(GroundingIssue(
            tc_number=draw(st.text(min_size=1, max_size=6,
                                   alphabet='0123456789')),
            step_number=draw(st.integers(min_value=1, max_value=99)),
            step_uid=draw(_cell_text),
            step_text=draw(_cell_text),
            reason=draw(st.sampled_from(_REASONS)),
            candidates=draw(_candidates),
        ))
    return out


@st.composite
def _results(draw):
    """A GroundingResult over the integer metric fields and a list of issues."""
    return GroundingResult(
        feature_id='MWTGPROV-%d' % draw(st.integers(min_value=1000, max_value=99999)),
        crawl_path=draw(st.sampled_from(
            ('nbop_discovery/nbop_ui_map.json', 'x.json', ''))),
        crawl_error=draw(st.sampled_from(('', 'missing', 'unparseable: boom'))),
        discovered_at=draw(st.sampled_from(('2026-04-14', '', 'unknown'))),
        age_days=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=4000))),
        stale_warning=draw(st.sampled_from(('', 'NBOP crawl map is stale: ...'))),
        ui_steps=draw(_metric),
        grounded_steps=draw(_metric),
        unmatched_steps=draw(_metric),
        filler_steps=draw(_metric),
        closers_removed=draw(_metric),
        typed_rows=draw(_metric),
        total_rows=draw(_metric),
        issues=draw(_issues()),
    )


# ---------------------------------------------------------------------------
# Sheet readers (mirror the sibling 9.3 test helpers)
# ---------------------------------------------------------------------------
def _find_header_row(ws):
    for r in range(1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == 'TC Number':
            return r
    raise AssertionError('issue-table header row not found')


def _metrics_map(ws, header_row_idx):
    out = {}
    for r in range(1, header_row_idx):
        label = ws.cell(row=r, column=1).value
        value = ws.cell(row=r, column=2).value
        if label:
            out[str(label)] = value
    return out


def _issue_rows(ws, header_row_idx):
    rows = []
    for r in range(header_row_idx + 1, ws.max_row + 1):
        vals = [ws.cell(row=r, column=c).value for c in range(1, 7)]
        if any(v not in (None, '') for v in vals):
            rows.append(vals)
    return rows


def _join_candidates(cands):
    return ', '.join(str(c) for c in cands)


# ===========================================================================
# Part 1: report completeness (pure, in-memory workbook)
# ===========================================================================
# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 12: Report completeness and log consistency
@settings(max_examples=40, deadline=None)
@given(result=_results())
def test_report_sheet_is_complete_and_metrics_do_not_drift(result):
    wb = openpyxl.Workbook()
    _build_grounding_report_sheet(wb, result)

    assert GROUNDING_REPORT_SHEET in wb.sheetnames
    ws = wb[GROUNDING_REPORT_SHEET]
    header_row = _find_header_row(ws)

    # Issue-table header is exactly the design §10 columns, in order.
    header = [ws.cell(row=header_row, column=c).value for c in range(1, 7)]
    assert header == _ISSUE_HEADER

    # EXACTLY one row per GroundingIssue, in order, with candidates comma-joined.
    rows = _issue_rows(ws, header_row)
    # A row whose every cell is blank (empty tc/uid/text/candidates, blank
    # reason impossible since reason is non-empty) still counts: reason anchors
    # it. Every issue here has a non-empty reason, so all are detected.
    assert len(rows) == len(result.issues)
    for got, issue in zip(rows, result.issues):
        # openpyxl stores '' as None; normalize blank text cells before comparing.
        assert got[0] == issue.tc_number
        assert got[1] == issue.step_number
        assert (got[2] or '') == issue.step_uid
        assert (got[3] or '') == issue.step_text
        assert got[4] == issue.reason
        assert (got[5] or '') == _join_candidates(issue.candidates)

    # Metrics block reports the SAME values that live on the result (no drift),
    # and the derived shares equal _pct over those values.
    metrics = _metrics_map(ws, header_row)
    assert metrics['Feature'] == result.feature_id
    assert metrics['UI_Steps'] == result.ui_steps
    assert metrics['Grounded_Steps'] == result.grounded_steps
    assert metrics['Unmatched_Steps'] == result.unmatched_steps
    assert metrics['Filler_Steps'] == result.filler_steps
    assert metrics['Closer_Steps removed'] == result.closers_removed
    assert metrics['Typed rows'] == result.typed_rows
    assert metrics['Total rows'] == result.total_rows
    assert metrics['Grounded share %'] == _pct(result.grounded_steps, result.ui_steps)
    assert metrics['Typed share %'] == _pct(result.typed_rows, result.total_rows)


# ===========================================================================
# Part 2: log consistency (driven through generate_excel)
# ===========================================================================
_SUMMARY_RE = re.compile(
    r'^\[CRAWL-GROUND\] (?P<feature>.*?): '
    r'ui_steps=(?P<ui_steps>\d+) grounded=(?P<grounded>\d+) '
    r'unmatched=(?P<unmatched>\d+) filler=(?P<filler>\d+) '
    r'typed=(?P<typed>\d+) total=(?P<total>\d+) '
    r'grounded_share=(?P<grounded_share>\d+%) typed_share=(?P<typed_share>\d+%) '
    r'closers_removed=(?P<closers_removed>\d+)$'
)


def _ui_suite(feature_id):
    """A minimal UI suite. The pass is stubbed, so step content is irrelevant
    beyond keeping the generator happy; no crawl map is read."""
    step = TestStep(step_num=1, summary='Launch NBOP', expected='NBOP opens')
    tc = TestCase(sno='1', summary='Launch NBOP', steps=[step])
    return TestSuite(
        feature_id=feature_id, feature_title='Report Fixture', pi='PI-59',
        test_cases=[tc], routing_audit=RoutingAudit(classification='ui'),
    )


def _run_generate_excel_with(result):
    """Drive ``generate_excel`` with the pass stubbed to attach ``result`` and
    output paths redirected to a per-example temp dir. Returns (written_path,
    logs). The caller is responsible for reading the workbook before the temp
    dir is cleaned up, so we hand back a copy read into memory instead.

    Patching is done manually (not via the ``monkeypatch`` fixture) because a
    function-scoped fixture is not reset between hypothesis examples.
    """
    import tempfile
    from pathlib import Path

    saved_out = eg.output_path
    saved_cp = eg.checkpoint_path
    saved_pass = cg.apply_crawl_grounding
    tmpdir = tempfile.mkdtemp(prefix='tsg_prop12_')
    # generate_excel calls out.name, so output_path must return a Path.
    out = Path(tmpdir) / 'suite.xlsx'
    cp = Path(tmpdir) / 'suite_checkpoint.xlsx'

    def _attach(suite, log, *a, **k):
        # Attach the hypothesis-built result to the suite instead of running the
        # real pass — keeps the run hermetic (no crawl map / Jira / Chalk).
        suite._grounding_result = result

    logs = []
    try:
        eg.output_path = lambda *a, **k: out
        eg.checkpoint_path = lambda *a, **k: cp
        cg.apply_crawl_grounding = _attach
        written = eg.generate_excel(_ui_suite(result.feature_id), log=logs.append)
        wb = openpyxl.load_workbook(str(written))
    finally:
        eg.output_path = saved_out
        eg.checkpoint_path = saved_cp
        cg.apply_crawl_grounding = saved_pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return wb, logs


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 12: Report completeness and log consistency
@settings(max_examples=25, deadline=None)
@given(result=_results())
def test_log_line_matches_the_grounding_report_sheet(result):
    wb, logs = _run_generate_excel_with(result)

    # Exactly one [CRAWL-GROUND] summary line (the 'skipped' except path never
    # contains 'ui_steps=').
    summaries = [str(m) for m in logs
                 if str(m).startswith('[CRAWL-GROUND]') and 'ui_steps=' in str(m)]
    assert len(summaries) == 1, 'expected one summary line, got %r' % summaries
    m = _SUMMARY_RE.match(summaries[0])
    assert m is not None, 'summary line did not parse: %r' % summaries[0]

    # Read the Grounding Report sheet from the written workbook. The contract
    # writer recomputes typed/total from each step's _crawl_grounding (the
    # fixture steps carry none → 0), so the sheet and the log line both reflect
    # the SAME (post-contract-writer) GroundingResult: they must agree.
    assert GROUNDING_REPORT_SHEET in wb.sheetnames
    ws = wb[GROUNDING_REPORT_SHEET]
    header_row = _find_header_row(ws)
    metrics = _metrics_map(ws, header_row)

    assert m.group('feature') == result.feature_id
    assert int(m.group('ui_steps')) == metrics['UI_Steps']
    assert int(m.group('grounded')) == metrics['Grounded_Steps']
    assert int(m.group('unmatched')) == metrics['Unmatched_Steps']
    assert int(m.group('filler')) == metrics['Filler_Steps']
    assert int(m.group('typed')) == metrics['Typed rows']
    assert int(m.group('total')) == metrics['Total rows']
    assert m.group('grounded_share') == metrics['Grounded share %']
    assert m.group('typed_share') == metrics['Typed share %']
    assert int(m.group('closers_removed')) == metrics['Closer_Steps removed']

    # And the shares in the log line are exactly _pct over the sheet's counts.
    assert m.group('grounded_share') == _pct(metrics['Grounded_Steps'], metrics['UI_Steps'])
    assert m.group('typed_share') == _pct(metrics['Typed rows'], metrics['Total rows'])
