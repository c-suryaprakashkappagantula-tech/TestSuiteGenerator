"""Task 9.3 — ``_build_grounding_report_sheet`` and the ``[CRAWL-GROUND]`` line.

Covers the design §10 Grounding Report sheet:
  * the sheet is created and carries the metrics block (label/value rows) with
    the derived grounded/typed shares;
  * the issue table header is exactly the six design §10 columns, with one row
    per ``GroundingIssue`` and candidate pages comma-joined; and
  * when a suite has no ``_grounding_result`` (non-UI features), the generate_
    excel flow produces no "Grounding Report" sheet and no ``[CRAWL-GROUND]``
    summary line.

Everything is built straight from the dataclasses + a fresh openpyxl workbook,
so no crawl map, Jira, Chalk, cache or browser is touched.
"""
import openpyxl
import pytest

import modules.excel_generator as eg
import modules.crawl_grounding as cg
from modules.excel_generator import (
    GROUNDING_REPORT_SHEET, _build_grounding_report_sheet,
)
from modules.crawl_grounding import GroundingResult, GroundingIssue
from modules.data_models_v8 import TestSuite, TestCase, TestStep, RoutingAudit


def _result_with_issues():
    """A populated GroundingResult with two issues (list + tuple candidates)."""
    issues = [
        GroundingIssue(
            tc_number='1', step_number=2, step_uid='TC1-S2',
            step_text='Verify the Foo button is displayed', reason='no_match',
            candidates=(),
        ),
        GroundingIssue(
            tc_number='2', step_number=1, step_uid='TC2-S1',
            step_text='Open the History tab', reason='ambiguous',
            candidates=('History', 'Transaction History'),
        ),
    ]
    return GroundingResult(
        feature_id='MWTGPROV-9003',
        crawl_path='nbop_discovery/nbop_ui_map.json',
        crawl_error='',
        discovered_at='2026-04-14',
        age_days=12,
        stale_warning='',
        ui_steps=10,
        grounded_steps=8,
        unmatched_steps=2,
        filler_steps=3,
        closers_removed=4,
        typed_rows=6,
        total_rows=10,
        issues=issues,
    )


def _metrics_map(ws, header_row_idx):
    """Collect the label→value metrics block above the issue header row."""
    out = {}
    for r in range(1, header_row_idx):
        label = ws.cell(row=r, column=1).value
        value = ws.cell(row=r, column=2).value
        if label:
            out[str(label)] = value
    return out


def _find_header_row(ws):
    """Row index whose first cell is the issue-table header 'TC Number'."""
    for r in range(1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == 'TC Number':
            return r
    raise AssertionError('issue-table header row not found')


def test_sheet_exists_metrics_and_issue_table():
    result = _result_with_issues()
    wb = openpyxl.Workbook()
    _build_grounding_report_sheet(wb, result)

    assert GROUNDING_REPORT_SHEET in wb.sheetnames
    ws = wb[GROUNDING_REPORT_SHEET]

    header_row = _find_header_row(ws)
    metrics = _metrics_map(ws, header_row)

    # Metrics block carries the key field set + derived shares (design §10).
    assert metrics['Feature'] == 'MWTGPROV-9003'
    assert metrics['UI_Steps'] == 10
    assert metrics['Grounded_Steps'] == 8
    assert metrics['Unmatched_Steps'] == 2
    assert metrics['Filler_Steps'] == 3
    assert metrics['Closer_Steps removed'] == 4
    assert metrics['Typed rows'] == 6
    assert metrics['Total rows'] == 10
    # 8/10 and 6/10.
    assert metrics['Grounded share %'] == '80%'
    assert metrics['Typed share %'] == '60%'

    # Issue-table header is exactly the design §10 columns, in order.
    header = [ws.cell(row=header_row, column=c).value for c in range(1, 7)]
    assert header == [
        'TC Number', 'Step Number', 'Step UID', 'Step Text', 'Reason',
        'Candidate Pages',
    ]

    # One row per GroundingIssue, candidates comma-joined.
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        vals = [ws.cell(row=r, column=c).value for c in range(1, 7)]
        if any(v not in (None, '') for v in vals):
            rows.append(vals)
    assert len(rows) == 2
    assert rows[0] == ['1', 2, 'TC1-S2',
                       'Verify the Foo button is displayed', 'no_match', '']
    assert rows[1] == ['2', 1, 'TC2-S1', 'Open the History tab', 'ambiguous',
                       'History, Transaction History']


def test_empty_issue_list_still_builds_sheet_and_shares():
    """No issues → sheet + metrics still present; 0/0 shares read as 0%."""
    result = GroundingResult(
        feature_id='MWTGPROV-9004', crawl_path='x.json', crawl_error='',
        discovered_at='', age_days=None, stale_warning='',
        ui_steps=0, grounded_steps=0, unmatched_steps=0, filler_steps=0,
        closers_removed=0, typed_rows=0, total_rows=0, issues=[],
    )
    wb = openpyxl.Workbook()
    _build_grounding_report_sheet(wb, result)
    ws = wb[GROUNDING_REPORT_SHEET]
    header_row = _find_header_row(ws)
    metrics = _metrics_map(ws, header_row)
    assert metrics['Grounded share %'] == '0%'
    assert metrics['Typed share %'] == '0%'
    assert metrics['Crawl age (days)'] in ('', None)
    # No issue rows beyond the header.
    assert ws.max_row == header_row


def _ui_suite(classification='ui'):
    step = TestStep(step_num=1, summary='Launch NBOP', expected='NBOP opens')
    tc = TestCase(sno='1', summary='Launch NBOP', steps=[step])
    return TestSuite(
        feature_id='MWTGPROV-9005', feature_title='Report Fixture',
        pi='PI-59', test_cases=[tc],
        routing_audit=RoutingAudit(classification=classification),
    )


@pytest.fixture
def redirect_output(monkeypatch, tmp_path):
    out = tmp_path / 'suite.xlsx'
    cp = tmp_path / 'suite_checkpoint.xlsx'
    monkeypatch.setattr(eg, 'output_path', lambda *a, **k: out)
    monkeypatch.setattr(eg, 'checkpoint_path', lambda *a, **k: cp)
    return out


def test_none_grounding_result_produces_no_sheet_and_no_log(monkeypatch, redirect_output):
    """Non-UI path: _grounding_result stays None → no sheet, no summary line.

    The grounding pass is stubbed to a no-op so it never attaches
    ``_grounding_result`` (mirroring a Non_UI_Feature), with no crawl map read.
    """
    monkeypatch.setattr(cg, 'apply_crawl_grounding', lambda suite, log, *a, **k: None)

    logs = []
    suite = _ui_suite()
    out = eg.generate_excel(suite, log=logs.append)

    assert getattr(suite, '_grounding_result', None) is None
    wb = openpyxl.load_workbook(str(out))
    assert GROUNDING_REPORT_SHEET not in wb.sheetnames
    # The only [CRAWL-GROUND] lines allowed are the 9.1 "skipped" except path,
    # never a summary line (which starts "[CRAWL-GROUND] <feature>: ui_steps=").
    assert not any('ui_steps=' in str(m) for m in logs if '[CRAWL-GROUND]' in str(m))


def test_guard_invokes_builder_only_when_result_present(monkeypatch, redirect_output):
    """When the pass attaches a _grounding_result, the builder is called and
    a single [CRAWL-GROUND] summary line is emitted."""
    built = []
    real_builder = eg._build_grounding_report_sheet

    def _spy(wb, result):
        built.append(result)
        return real_builder(wb, result)

    def _attach(suite, log, *a, **k):
        suite._grounding_result = _result_with_issues()

    monkeypatch.setattr(eg, '_build_grounding_report_sheet', _spy)
    monkeypatch.setattr(cg, 'apply_crawl_grounding', _attach)

    logs = []
    out = eg.generate_excel(_ui_suite(), log=logs.append)

    assert len(built) == 1
    wb = openpyxl.load_workbook(str(out))
    assert GROUNDING_REPORT_SHEET in wb.sheetnames
    summary = [m for m in logs
               if str(m).startswith('[CRAWL-GROUND]') and 'ui_steps=' in str(m)]
    assert len(summary) == 1
    line = str(summary[0])
    # The summary line is emitted from the SAME GroundingResult that the
    # Grounding Report reads. typed/total rows come from the contract writer,
    # which recomputes them from each step's _crawl_grounding; these fixture
    # steps carry none, so the writer resets both to 0 (typed_share=0%). The
    # pass-set metrics (ui_steps/grounded/closers_removed) are unchanged.
    assert 'MWTGPROV-9003' in line
    assert 'ui_steps=10' in line and 'grounded=8' in line
    assert 'unmatched=2' in line and 'filler=3' in line
    assert 'typed=0' in line and 'total=0' in line
    assert 'grounded_share=80%' in line and 'typed_share=0%' in line
    assert 'closers_removed=4' in line
