"""
excel_generator.py — Generate production-ready Excel test suite.
Matches the exact format of TESTPLAN MWTGPROV-3976 sample.
Sheets: Test Cases (1st — required for QMetry upload), Summary, Traceability, Combinations.
"""
import shutil
from pathlib import Path
from typing import List
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

from .config import (EXCEL_HEADERS, MERGE_COLS, NAVY, LIGHT_BLUE, WHITE,
                     CAT_COLORS, output_path, checkpoint_path)
from .test_engine import TestSuite, TestCase


# ── Shared styles ──
_hf = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
_hfill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type='solid')
_bf = Font(name='Calibri', bold=True, size=11, color=NAVY)
_nf = Font(name='Calibri', size=11)
_wrap = Alignment(wrap_text=True, vertical='top')
_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
_bdr = Border(left=Side(style='thin'), right=Side(style='thin'),
              top=Side(style='thin'), bottom=Side(style='thin'))

# ── Category row colors (very soft tints — easy on the eyes) ──
_CAT_ROW_FILLS = {
    'Happy Path':   PatternFill(start_color='F1F8E9', end_color='F1F8E9', fill_type='solid'),  # very light green
    'Positive':     PatternFill(start_color='F1F8E9', end_color='F1F8E9', fill_type='solid'),
    'Negative':     PatternFill(start_color='FBE9E7', end_color='FBE9E7', fill_type='solid'),  # very light coral
    'Edge Case':    PatternFill(start_color='FFF9C4', end_color='FFF9C4', fill_type='solid'),  # very light yellow
    'E2E':          PatternFill(start_color='E8EAF6', end_color='E8EAF6', fill_type='solid'),  # very light indigo
    'End-to-End':   PatternFill(start_color='E8EAF6', end_color='E8EAF6', fill_type='solid'),
    'Rollback':     PatternFill(start_color='F3E5F5', end_color='F3E5F5', fill_type='solid'),  # very light purple
}
_CAT_SNO_FONTS = {
    'Happy Path':   Font(name='Calibri', bold=True, size=11, color='388E3C'),   # medium green
    'Positive':     Font(name='Calibri', bold=True, size=11, color='388E3C'),
    'Negative':     Font(name='Calibri', bold=True, size=11, color='D32F2F'),   # medium red
    'Edge Case':    Font(name='Calibri', bold=True, size=11, color='F9A825'),   # amber
    'E2E':          Font(name='Calibri', bold=True, size=11, color='1976D2'),   # medium blue
    'End-to-End':   Font(name='Calibri', bold=True, size=11, color='1976D2'),
    'Rollback':     Font(name='Calibri', bold=True, size=11, color='7B1FA2'),   # medium purple
}
_lb = PatternFill(start_color=LIGHT_BLUE, end_color=LIGHT_BLUE, fill_type='solid')
_wf = PatternFill(start_color=WHITE, end_color=WHITE, fill_type='solid')


def _origin_tag(tc) -> str:
    """Option-B source tag: a compact origin label so a reviewer can see (and filter)
    which TCs came from Chalk vs were synthesized from Jira AC / subtasks / injected
    negatives. Derived from the TC's traceability.source_type."""
    tr = getattr(tc, 'traceability', None)
    st = (getattr(tr, 'source_type', '') or '').strip().lower() if tr else ''
    # Explicit injected-negative marker wins if the engine set it.
    if getattr(tc, '_injected_negative', False):
        return 'Injected-Neg'
    if st in ('chalk', 'chalk scenario', 'business rule'):
        return 'Chalk'
    if st in ('nbop ui', 'nbop'):
        return 'Chalk-UI'
    if st in ('jira ac', 'jira'):
        return 'Jira-AC'
    if st in ('subtask ac', 'subtask'):
        return 'Subtask'
    if st in ('attachment',):
        return 'Doc'
    if st in ('related feature',):
        return 'Related'
    return 'Chalk' if getattr(tc, 'from_chalk', False) else (st.title() if st else 'Other')


def _label_with_origin(tc) -> str:
    """Return the TC label with the origin tag appended (kept filterable in QMetry)."""
    base = (getattr(tc, 'label', '') or '').strip()
    tag = '[%s]' % _origin_tag(tc)
    if tag in base:
        return base
    return ('%s %s' % (base, tag)).strip() if base else tag


def generate_excel(suite: TestSuite, log=print) -> Path:
    """Generate the complete Excel workbook. Returns output path.

    Gated on the shared do-not-touch registry: this is the one function every
    write path goes through (pipeline, all dashboards, the dry-run scripts), so
    checking here means a protected identifier cannot reach a tester via a suite.
    Raising before the workbook is created also means no Feature Summary, no DB
    row and no transaction-log entry for the run — those all follow the Excel
    write in ``pipeline.block_generate_output``.
    """
    from .protected_gate import assert_suite_safe
    from .coverage_obligations import assert_suite_complete
    assert_suite_safe(suite, context='Excel output for %s'
                      % (getattr(suite, 'feature_id', '') or 'suite'), log=log)
    assert_suite_complete(suite, context='Excel output for %s'
                          % (getattr(suite, 'feature_id', '') or 'suite'), log=log)

    # One output-sanitize pass at the chokepoint every write path shares (pipeline, all
    # dashboards, headless, CR/V7 delegation). Output-only: rewrites wording, never which
    # TCs exist or which steps they have (bar moving 'Old behaviour:' context lines into
    # the Description). Then make every TC name unique. See modules/output_sanitizer.py.
    try:
        from .output_sanitizer import sanitize_suite, dedupe_names, polish_suite
        sanitize_suite(suite, log)
        polish_suite(suite, log)
        dedupe_names(suite, log)
    except Exception as _san_err:
        log('[EXCEL] output sanitize skipped: %s' % str(_san_err)[:100])

    # Output-stage crawl-grounding pass. UI_Features only: rewrites filler/UI step
    # text to crawl-grounded wording and attaches StepGrounding records. Non-UI
    # suites return immediately and are left untouched. Never changes TC count or
    # titles. Failures are non-fatal — the workbook is written regardless.
    try:
        from .crawl_grounding import apply_crawl_grounding
        apply_crawl_grounding(suite, log)
    except Exception as exc:
        log('[CRAWL-GROUND] skipped: %s' % str(exc)[:120])

    wb = openpyxl.Workbook()

    # ── Sheet 1: Summary ──
    log('[EXCEL] Building Summary sheet...')
    _build_summary_sheet(wb, suite)

    # ── Sheet 2: All Test Cases in ONE sheet ──
    log('[EXCEL] Building Test Cases sheet (%d TCs)...' % len(suite.test_cases))
    _build_testcases_sheet(wb, suite, sheet_name='Test Cases')

    # ── Sheet 3: Traceability (AC → TC mapping) ──
    if suite.ac_traceability:
        log('[EXCEL] Building Traceability sheet...')
        _build_traceability_sheet(wb, suite)

    # ── Sheet 4: Combinations (if matrix expansion was used) ──
    if hasattr(suite, 'combinations') and suite.combinations and len(suite.combinations) > 1:
        log('[EXCEL] Building Combinations sheet...')
        _build_combinations_sheet(wb, suite)

    # ── Sheet 5 (V8.0): Data Sources summary ──
    if hasattr(suite, 'data_inventory') and suite.data_inventory and hasattr(suite.data_inventory, 'sources') and suite.data_inventory.sources:
        log('[EXCEL] Building Data Sources sheet (V8.0)...')
        _build_data_sources_sheet(wb, suite)

    # ── Sheet 6 (V8.0): Coverage Scorecard ──
    _scorecard = getattr(suite, '_coverage_scorecard', None)
    if _scorecard is not None:
        log('[EXCEL] Building Coverage Scorecard sheet...')
        try:
            from .coverage_scorecard import build_scorecard_excel_sheet
            build_scorecard_excel_sheet(wb, _scorecard)
        except Exception as _sc_err:
            log('[EXCEL] Coverage Scorecard sheet skipped: %s' % str(_sc_err)[:60])

    # ── Deterministic source-obligation audit ──
    if getattr(suite, 'coverage_obligations', None):
        log('[EXCEL] Building Coverage Obligations sheet...')
        _build_coverage_obligations_sheet(wb, suite)

    # ── Contract-v1 shadow export (additive; existing Test Cases sheet is unchanged) ──
    try:
        from .contract_bridge import contract_enabled
        if contract_enabled():
            log('[EXCEL] Building Execution Contract + Source Provenance sheets...')
            _build_execution_contract_sheets(wb, suite, log=log)
    except Exception as _contract_err:
        # Non-blocking by design during shadow rollout: legacy output remains available.
        log('[EXCEL] Contract-v1 shadow export skipped: %s' % str(_contract_err)[:120])

    # ── Grounding Report (design §10): UI_Features only ──
    # Built after the contract sheets so typed_rows/total_rows are populated.
    # Non-UI suites have no _grounding_result → no sheet and no [CRAWL-GROUND]
    # summary line (Req 6.1, 6.5, 8.1, 8.2).
    _gr = getattr(suite, '_grounding_result', None)
    if _gr is not None:
        try:
            _build_grounding_report_sheet(wb, _gr)
            log('[CRAWL-GROUND] %s: ui_steps=%d grounded=%d unmatched=%d filler=%d '
                'typed=%d total=%d grounded_share=%s typed_share=%s closers_removed=%d' % (
                    getattr(_gr, 'feature_id', ''),
                    int(getattr(_gr, 'ui_steps', 0) or 0),
                    int(getattr(_gr, 'grounded_steps', 0) or 0),
                    int(getattr(_gr, 'unmatched_steps', 0) or 0),
                    int(getattr(_gr, 'filler_steps', 0) or 0),
                    int(getattr(_gr, 'typed_rows', 0) or 0),
                    int(getattr(_gr, 'total_rows', 0) or 0),
                    _pct(getattr(_gr, 'grounded_steps', 0), getattr(_gr, 'ui_steps', 0)),
                    _pct(getattr(_gr, 'typed_rows', 0), getattr(_gr, 'total_rows', 0)),
                    int(getattr(_gr, 'closers_removed', 0) or 0)))
        except Exception as _gr_err:
            log('[EXCEL] Grounding Report sheet skipped: %s' % str(_gr_err)[:120])

    # Remove default empty sheet if exists
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']

    # ── Reorder sheets: Test Cases FIRST (QMetry checks 1st sheet headers) ──
    tc_sheet_name = 'Test Cases'
    if tc_sheet_name in wb.sheetnames:
        tc_idx = wb.sheetnames.index(tc_sheet_name)
        wb.move_sheet(tc_sheet_name, offset=-tc_idx)  # move to position 0

    # Save
    out = output_path(suite.feature_id, pi=suite.pi, title=suite.feature_title)
    wb.save(str(out))
    log(f'[EXCEL] ✅ Saved: {out.name}')

    # Auto-checkpoint
    cp = checkpoint_path(suite.feature_id, pi=suite.pi, title=suite.feature_title)
    shutil.copy2(str(out), str(cp))
    log(f'[EXCEL] ✅ Checkpoint: {cp.name}')

    return out


def _tc_label(suite, tc):
    """TC label by position in the suite - the same number the Test Cases sheet shows.
    tc.sno keeps the pre-pruning number (near-dup pruning removes TCs after numbering),
    so labelling the Summary by sno showed TC06..TC12 next to a TC01..TC07 sheet."""
    for i, x in enumerate(getattr(suite, 'test_cases', None) or [], 1):
        if x is tc:
            return 'TC%02d' % i
    return 'TC%s' % str(getattr(tc, 'sno', '') or '').zfill(2)


def _build_summary_sheet(wb, suite: TestSuite):
    """Build the Summary sheet with feature info, AC, coverage, sources."""
    ws = wb.active
    ws.title = 'Summary'
    ws.freeze_panes = 'A2'  # Point 16: Freeze title row
    ws.column_dimensions['A'].width = 22
    ws.column_dimensions['B'].width = 58
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 14
    ws.column_dimensions['F'].width = 10

    # Title
    r = 1
    ws.merge_cells(f'A{r}:F{r}')
    ws.cell(row=r, column=1, value=f'TEST PLAN SUMMARY - {suite.feature_id}')
    ws.cell(row=r, column=1).font = Font(name='Calibri', bold=True, size=16, color='1A237E')
    ws.cell(row=r, column=1).alignment = Alignment(horizontal='center', vertical='center')
    ws.cell(row=r, column=1).fill = PatternFill(start_color='E8EAF6', end_color='E8EAF6', fill_type='solid')
    ws.row_dimensions[r].height = 35
    r += 2

    # Helper for section headers
    _sec_font = Font(name='Calibri', bold=True, size=12, color='1A237E')
    _sec_fill = PatternFill(start_color='E8EAF6', end_color='E8EAF6', fill_type='solid')
    def _section(row, title):
        ws.merge_cells(f'A{row}:F{row}')
        c = ws.cell(row=row, column=1, value=title)
        c.font = _sec_font; c.fill = _sec_fill
        for ci in range(1, 7):
            ws.cell(row=row, column=ci).fill = _sec_fill

    # Feature Details
    _section(r, 'SUITE GUIDE')
    r += 1
    # Only list sheets that are ACTUALLY generated — mirror the build conditions
    # in generate_excel() so the guide never advertises a sheet that isn't present.
    _guide_items = [
        ('Summary (this sheet)', 'Overview of the feature, acceptance criteria, coverage breakdown, priority distribution, and data sources.'),
        ('Test Cases', 'All test scenarios with step-by-step actions and expected results. Execute in order — P1 (critical) first.'),
    ]
    if getattr(suite, 'ac_traceability', None):
        _guide_items.append(('Traceability', 'Maps each Acceptance Criteria to the test cases that cover it. Green = covered, Red = gap.'))
    if hasattr(suite, 'combinations') and getattr(suite, 'combinations', None) and len(suite.combinations) > 1:
        _guide_items.append(('Combinations', 'Device/SIM/Network matrix showing which hardware combos each test case should be run on.'))
    if hasattr(suite, 'data_inventory') and getattr(suite, 'data_inventory', None) and getattr(suite.data_inventory, 'sources', None):
        _guide_items.append(('Data Sources', 'Inventory of every source mined (Chalk, Jira AC, subtasks) and how many testable items each yielded.'))
    if getattr(suite, '_coverage_scorecard', None) is not None:
        _guide_items.append(('Coverage Scorecard', 'Risk lenses — line-state matrix, Chalk alignment, category balance, grounding — with an overall risk rating.'))
    if getattr(suite, 'coverage_obligations', None):
        _guide_items.append(('Coverage Obligations', 'Deterministic source requirements mapped to covering test cases. Every required row must be COVERED before export.'))
    _cat_guide = [
        ('Happy Path', 'Core positive scenarios — the feature works as designed with valid inputs and expected conditions.'),
        ('Negative', 'Failure and error scenarios — invalid inputs, system failures, timeouts, rollbacks. Verifies graceful handling.'),
        ('Edge Case', 'Unusual but valid scenarios — boundary values, concurrent operations, rare device combos.'),
        ('E2E', 'Full workflow from UI through API to all downstream systems. Validates the complete chain.'),
        ('Regression', 'Ensures existing functionality is not broken by the new feature. Run after every deployment.'),
    ]
    _pri_guide = [
        ('P1 (Critical)', 'Must-run. Core happy paths, rollback, data integrity, E2E flows. Block release if failing.'),
        ('P2 (Important)', 'Should-run. Negative cases, input validation, error handling. High risk if skipped.'),
        ('P3 (Nice-to-have)', 'Good-to-run. UI checks, notifications, low-risk edge cases. Run if time permits.'),
    ]
    ws.cell(row=r, column=1, value='Sheets:').font = _bf
    r += 1
    for sn, desc in _guide_items:
        ws.cell(row=r, column=1, value=sn).font = _bf; ws.cell(row=r, column=1).border = _bdr
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(row=r, column=2, value=desc).font = _nf; ws.cell(row=r, column=2).alignment = _wrap; ws.cell(row=r, column=2).border = _bdr
        r += 1
    r += 1
    ws.cell(row=r, column=1, value='Categories:').font = _bf
    r += 1
    for cn, desc in _cat_guide:
        _cc = CAT_COLORS.get(cn, 'FFFFFF')
        ws.cell(row=r, column=1, value=cn).font = _bf; ws.cell(row=r, column=1).border = _bdr
        ws.cell(row=r, column=1).fill = PatternFill(start_color=_cc, end_color=_cc, fill_type='solid')
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(row=r, column=2, value=desc).font = _nf; ws.cell(row=r, column=2).alignment = _wrap; ws.cell(row=r, column=2).border = _bdr
        r += 1
    r += 1
    ws.cell(row=r, column=1, value='Priorities:').font = _bf
    r += 1
    _pcg = {'P1 (Critical)': 'FFC7CE', 'P2 (Important)': 'FFEB9C', 'P3 (Nice-to-have)': 'C6EFCE'}
    for pn, desc in _pri_guide:
        _pc = _pcg.get(pn, 'FFFFFF')
        ws.cell(row=r, column=1, value=pn).font = _bf; ws.cell(row=r, column=1).border = _bdr
        ws.cell(row=r, column=1).fill = PatternFill(start_color=_pc, end_color=_pc, fill_type='solid')
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(row=r, column=2, value=desc).font = _nf; ws.cell(row=r, column=2).alignment = _wrap; ws.cell(row=r, column=2).border = _bdr
        r += 1
    r += 2

    _section(r, 'FEATURE DETAILS')
    r += 1

    info = [
        ('Feature ID', suite.feature_id),
        ('Summary', suite.feature_title),
        ('Issue Type', 'Epic'),
        ('Priority', suite.jira_priority),
        ('Status', suite.jira_status),
        ('Assignee', suite.jira_assignee),
        ('Reporter', suite.jira_reporter),
        ('Labels', ', '.join(suite.jira_labels)),
        ('Linked Issues', '; '.join(f"{l['key']} - {l['summary'][:80]}" for l in suite.jira_links) if suite.jira_links else 'None'),
        ('Attachments', ', '.join(suite.attachment_names) if suite.attachment_names else 'None'),
        ('PI', suite.pi or 'N/A'),
        ('Channel', ', '.join(suite.channel) if isinstance(suite.channel, list) else suite.channel),
        ('Devices', ', '.join(suite.devices)),
        ('Networks', ', '.join(suite.networks)),
    ]
    for label, val in info:
        ws.cell(row=r, column=1, value=label).font = _bf
        ws.cell(row=r, column=1).border = _bdr
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(row=r, column=2, value=val).font = _nf
        ws.cell(row=r, column=2).alignment = _wrap
        ws.cell(row=r, column=2).border = _bdr
        r += 1

    r += 1

    # Scope & Rules
    if suite.scope or suite.rules:
        ws.merge_cells(f'A{r}:F{r}')
        ws.cell(row=r, column=1, value='SCOPE & RULES').font = _bf
        r += 1
        for text in [suite.scope, suite.rules]:
            if text and text.strip():
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
                ws.cell(row=r, column=1, value=text.strip()).font = _nf
                ws.cell(row=r, column=1).alignment = _wrap
                r += 1
        r += 1

    # Acceptance Criteria
    if suite.acceptance_criteria:
        ws.merge_cells(f'A{r}:F{r}')
        ws.cell(row=r, column=1, value='ACCEPTANCE CRITERIA (from Jira)').font = _bf
        r += 1
        for i, ac in enumerate(suite.acceptance_criteria, 1):
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.cell(row=r, column=1, value=f'{i}. {ac}').font = _nf
            ws.cell(row=r, column=1).alignment = _wrap
            r += 1
        r += 1

    # Open Items
    if suite.open_items:
        ws.merge_cells(f'A{r}:F{r}')
        ws.cell(row=r, column=1, value='OPEN ITEMS (from attachments/docs)').font = _bf
        r += 1
        for i, item in enumerate(suite.open_items, 1):
            coverage = suite.open_item_coverage.get(item[:80], 'Not covered')
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.cell(row=r, column=1, value=f'{i}. {item} → {coverage}').font = _nf
            ws.cell(row=r, column=1).alignment = _wrap
            r += 1
        r += 1

    # Test Case Summary Table — HIGH-LEVEL by group (not individual TCs)
    ws.merge_cells(f'A{r}:F{r}')
    ws.cell(row=r, column=1, value='TEST CASE SUMMARY').font = _bf
    r += 1

    # If groups exist, show group-level summary
    if hasattr(suite, 'groups') and suite.groups and len(suite.groups) > 1:
        for ci, h in enumerate(['Sheet / Group', 'TCs', 'Steps', 'Categories', '', ''], 1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr
        r += 1

        total_tcs = 0
        total_steps = 0
        for gname, gtcs in suite.groups.items():
            g_steps = sum(len(tc.steps) for tc in gtcs)
            g_cats = set(tc.category for tc in gtcs)
            ws.cell(row=r, column=1, value=gname).font = _nf
            ws.cell(row=r, column=1).alignment = _wrap; ws.cell(row=r, column=1).border = _bdr
            ws.cell(row=r, column=2, value=str(len(gtcs))).font = _nf
            ws.cell(row=r, column=2).alignment = _center; ws.cell(row=r, column=2).border = _bdr
            ws.cell(row=r, column=3, value=str(g_steps)).font = _nf
            ws.cell(row=r, column=3).alignment = _center; ws.cell(row=r, column=3).border = _bdr
            ws.cell(row=r, column=4, value=', '.join(sorted(g_cats))).font = _nf
            ws.cell(row=r, column=4).alignment = _center; ws.cell(row=r, column=4).border = _bdr
            total_tcs += len(gtcs)
            total_steps += g_steps
            r += 1

        # Totals row — use actual suite count, not group sum (groups may merge/miss)
        _actual_tcs = len(suite.test_cases)
        _actual_steps = sum(len(tc.steps) for tc in suite.test_cases)
        ws.cell(row=r, column=1, value='TOTAL').font = _bf
        ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
        ws.cell(row=r, column=2, value=str(_actual_tcs)).font = _bf
        ws.cell(row=r, column=2).alignment = _center; ws.cell(row=r, column=2).border = _bdr
        ws.cell(row=r, column=3, value=str(_actual_steps)).font = _bf
        ws.cell(row=r, column=3).alignment = _center; ws.cell(row=r, column=3).border = _bdr
        r += 1
    else:
        # Single sheet — show compact summary
        for ci, h in enumerate(['TC#', 'Test Scenario', 'Category', 'Steps', '', ''], 1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr
        r += 1
        total_steps = 0
        # Word-boundary truncation (Defect 2a): the raw short_name[:80] slice cut names
        # mid-word on the Summary sheet (e.g. '..._than_the_old_as'). Reuse the builder's
        # smart truncation. Lazy import keeps the module graph acyclic.
        from .tc_builder import _smart_truncate_title
        for tc in suite.test_cases:
            import re as _re3
            short_name = _re3.sub(r'^TC\d+[_\s-]+' + _re3.escape(suite.feature_id) + r'[_\s-]*', '', tc.summary)
            cat_color = CAT_COLORS.get(tc.category, 'FFFFFF')
            ws.cell(row=r, column=1, value=_tc_label(suite, tc)).font = _nf
            ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
            ws.cell(row=r, column=2, value=_smart_truncate_title(short_name, 80)).font = _nf
            ws.cell(row=r, column=2).alignment = _wrap; ws.cell(row=r, column=2).border = _bdr
            ws.cell(row=r, column=4, value=tc.category).font = _nf
            ws.cell(row=r, column=4).alignment = _center; ws.cell(row=r, column=4).border = _bdr
            ws.cell(row=r, column=4).fill = PatternFill(start_color=cat_color, end_color=cat_color, fill_type='solid')
            ws.cell(row=r, column=5, value=str(len(tc.steps))).font = _nf
            ws.cell(row=r, column=5).alignment = _center; ws.cell(row=r, column=5).border = _bdr
            total_steps += len(tc.steps)
            r += 1
        ws.cell(row=r, column=1, value='TOTAL').font = _bf
        ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.cell(row=r, column=2, value=f'{len(suite.test_cases)} Test Cases | {total_steps} Test Steps').font = _bf
        ws.cell(row=r, column=2).alignment = _center; ws.cell(row=r, column=2).border = _bdr
        r += 1
    r += 2

    # Coverage Breakdown
    ws.merge_cells(f'A{r}:F{r}')
    ws.cell(row=r, column=1, value='COVERAGE BREAKDOWN').font = _bf
    r += 1
    cats = {}
    for tc in suite.test_cases:
        cats.setdefault(tc.category, []).append(_tc_label(suite, tc))
    for ci, h in enumerate(['Category', 'Count', 'Test Cases'], 1):
        c = ws.cell(row=r, column=ci, value=h)
        c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr
    r += 1
    for cat, tcs in cats.items():
        ws.cell(row=r, column=1, value=cat).font = _nf
        ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
        cat_color = CAT_COLORS.get(cat, 'FFFFFF')
        ws.cell(row=r, column=1).fill = PatternFill(start_color=cat_color, end_color=cat_color, fill_type='solid')
        ws.cell(row=r, column=2, value=str(len(tcs))).font = _nf
        ws.cell(row=r, column=2).alignment = _center; ws.cell(row=r, column=2).border = _bdr
        ws.cell(row=r, column=3, value=', '.join(tcs)).font = _nf
        ws.cell(row=r, column=3).alignment = _center; ws.cell(row=r, column=3).border = _bdr
        r += 1

    r += 1

    # Priority Distribution (V4)
    pris = {}
    for tc in suite.test_cases:
        pri = getattr(tc, 'priority', None) or getattr(tc, '_priority', 'P3')
        pris.setdefault(pri, []).append(_tc_label(suite, tc))
    if pris:
        ws.merge_cells(f'A{r}:F{r}')
        ws.cell(row=r, column=1, value='PRIORITY DISTRIBUTION').font = _bf
        r += 1
        _pri_colors = {'P1': 'FFC7CE', 'P2': 'FFEB9C', 'P3': 'C6EFCE'}
        for ci, h in enumerate(['Priority', 'Count', 'Test Cases'], 1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr
        r += 1
        for pri in ['P1', 'P2', 'P3']:
            if pri in pris:
                ws.cell(row=r, column=1, value=pri).font = _nf
                ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
                _pc = _pri_colors.get(pri, 'FFFFFF')
                ws.cell(row=r, column=1).fill = PatternFill(start_color=_pc, end_color=_pc, fill_type='solid')
                ws.cell(row=r, column=2, value=str(len(pris[pri]))).font = _nf
                ws.cell(row=r, column=2).alignment = _center; ws.cell(row=r, column=2).border = _bdr
                ws.cell(row=r, column=3, value=', '.join(pris[pri][:20])).font = _nf
                ws.cell(row=r, column=3).alignment = _center; ws.cell(row=r, column=3).border = _bdr
                r += 1
        r += 1

    # Warnings
    if suite.warnings:
        ws.merge_cells(f'A{r}:F{r}')
        ws.cell(row=r, column=1, value='⚠️ WARNINGS').font = _bf
        r += 1
        for w in suite.warnings:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.cell(row=r, column=1, value=w).font = _nf
            ws.cell(row=r, column=1).alignment = _wrap
            r += 1
        r += 1

    # Data Sources
    ws.merge_cells(f'A{r}:F{r}')
    ws.cell(row=r, column=1, value='DATA SOURCES').font = _bf
    r += 1
    for src in suite.data_sources:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.cell(row=r, column=1, value=src).font = _nf
        ws.cell(row=r, column=1).alignment = _wrap
        r += 1


def _build_testcases_sheet(wb, suite: TestSuite, sheet_name=None, tc_subset=None):
    """Build a Test Cases sheet. If tc_subset provided, only those TCs are included.
    TCs are renumbered per-sheet starting from 1."""
    ws = wb.create_sheet(sheet_name or suite.feature_id)
    tcs = tc_subset if tc_subset else suite.test_cases
    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 62
    ws.column_dimensions['C'].width = 70
    ws.column_dimensions['D'].width = 55
    ws.column_dimensions['E'].width = 8
    ws.column_dimensions['F'].width = 65
    ws.column_dimensions['G'].width = 60
    ws.column_dimensions['H'].width = 20
    ws.column_dimensions['I'].width = 20
    ws.column_dimensions['J'].width = 20
    ws.column_dimensions['K'].width = 13
    ws.column_dimensions['L'].width = 45

    # Row 1: Headers — bold white on soft navy (no feature description banner)
    ws.append(EXCEL_HEADERS)
    _header_fill = PatternFill(start_color='1A237E', end_color='1A237E', fill_type='solid')  # deep indigo
    _header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
    for ci in range(1, len(EXCEL_HEADERS) + 1):
        c = ws.cell(row=1, column=ci)
        c.font = _header_font
        c.fill = _header_fill
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = _bdr

    # Freeze panes — keep Row 1 (headers) visible
    ws.freeze_panes = 'A2'

    # Write test cases (renumbered per-sheet starting from 1)
    tc_start_rows = []
    row = 2
    for sheet_idx, tc in enumerate(tcs, 1):
        tc_start_rows.append(row)
        # Per-sheet S.No
        sheet_sno = str(sheet_idx)
        # Clean summary: replace old global TC number with per-sheet number
        import re as _re2
        # Renumber an existing TCnn_ prefix, or ADD one when the summary has none -
        # otherwise a non-prefixed summary reached the sheet name unnumbered.
        if _re2.match(r'^TC\d+_', tc.summary):
            clean_summary = _re2.sub(r'^TC\d+_', 'TC%02d_' % sheet_idx, tc.summary)
        else:
            clean_summary = 'TC%02d_%s' % (sheet_idx, tc.summary)
        # Add NEW tag for auto-diff identified new TCs
        if getattr(tc, '_is_new', False):
            clean_summary = '🆕 ' + clean_summary
        for si, step in enumerate(tc.steps):
            if si == 0:
                ws.cell(row=row, column=1, value=sheet_sno).alignment = _wrap
                ws.cell(row=row, column=2, value=clean_summary).alignment = _wrap
                ws.cell(row=row, column=3, value=tc.description).alignment = _wrap
                ws.cell(row=row, column=4, value=tc.preconditions).alignment = _wrap
                ws.cell(row=row, column=8, value=tc.story_linkage).alignment = _wrap
                ws.cell(row=row, column=9, value=_label_with_origin(tc)).alignment = _wrap
                ws.cell(row=row, column=10, value=tc.story_linkage).alignment = _wrap
                # Column 11: Grounding Score (colour-coded)
                _gscore = getattr(tc, 'grounding_score', -1)
                if _gscore == -1:
                    # Score on-demand if not pre-scored
                    try:
                        from .grounding_scorer import score_tc as _score_tc
                        _gscore = _score_tc(tc)
                        tc.grounding_score = _gscore
                    except Exception:
                        _gscore = 0
                _gcell = ws.cell(row=row, column=11, value='%d%%' % _gscore)
                _gcell.alignment = _center
                # Colour: green ≥80, yellow 60–79, red <60
                if _gscore >= 80:
                    _gcell.fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
                    _gcell.font = Font(name='Calibri', bold=True, size=11, color='375623')
                elif _gscore >= 60:
                    _gcell.fill = PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid')
                    _gcell.font = Font(name='Calibri', bold=True, size=11, color='7D6608')
                else:
                    _gcell.fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
                    _gcell.font = Font(name='Calibri', bold=True, size=11, color='9C0006')
                # Column 12: Test Data sample (MDN, lineId, etc.)
                # Rotate through available SIT samples per TC to avoid identical data
                _test_data_str = ''
                try:
                    from .test_data_injector import get_varied_test_data
                    _td_ctx = getattr(tc, 'dimension_values', {}) or {}
                    _td_api_name = ''
                    if hasattr(tc, 'traceability') and tc.traceability:
                        _td_api_name = (tc.traceability.source_id or '').lower()
                    _test_data_str = get_varied_test_data(
                        tc_index=sheet_idx - 1,
                        api_name=_td_api_name,
                        category=getattr(tc, 'category', ''),
                        dimension_values=_td_ctx,
                    )
                except Exception:
                    # Fallback to old behavior
                    try:
                        from .test_data_injector import get_operation_sample_request, format_request_sample
                        _td_sample = get_operation_sample_request(_td_api_name if '_td_api_name' in dir() else '')
                        _test_data_str = format_request_sample(_td_sample, max_fields=4)
                    except Exception:
                        pass
                _tdcell = ws.cell(row=row, column=12, value=_test_data_str)
                _tdcell.alignment = _wrap
                _tdcell.font = Font(name='Calibri', size=10, italic=True)
            ws.cell(row=row, column=5, value=step.step_num).alignment = _wrap
            ws.cell(row=row, column=6, value=step.summary).alignment = _wrap
            ws.cell(row=row, column=7, value=step.expected).alignment = _wrap
            for c in range(1, len(EXCEL_HEADERS) + 1):
                ws.cell(row=row, column=c).border = _bdr
            row += 1
    tc_start_rows.append(row)  # sentinel

    # Merge columns per test case
    for i in range(len(tc_start_rows) - 1):
        sr = tc_start_rows[i]
        er = tc_start_rows[i + 1] - 1
        if er <= sr:
            continue
        for col in MERGE_COLS:
            ws.merge_cells(start_row=sr, start_column=col, end_row=er, end_column=col)
            cell = ws.cell(row=sr, column=col)
            if col in (1, 8, 9, 10):
                cell.alignment = _center
            else:
                cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)

    # Category-based row coloring + bold S.No with category color
    for i in range(len(tc_start_rows) - 1):
        sr = tc_start_rows[i]
        er = tc_start_rows[i + 1] - 1
        # Get category of this TC
        cat = tcs[i].category if i < len(tcs) else 'Happy Path'
        row_fill = _CAT_ROW_FILLS.get(cat, _lb if i % 2 == 0 else _wf)
        sno_font = _CAT_SNO_FONTS.get(cat, _bf)
        for r in range(sr, er + 1):
            for c in range(1, len(EXCEL_HEADERS) + 1):
                ws.cell(row=r, column=c).fill = row_fill
        # Bold S.No with category color
        ws.cell(row=sr, column=1).font = sno_font
        # Bold summary
        ws.cell(row=sr, column=2).font = Font(name='Calibri', bold=True, size=11)


def _build_traceability_sheet(wb, suite: TestSuite):
    """Build AC → TC traceability matrix."""
    if not suite.ac_traceability:
        return

    ws = wb.create_sheet('Traceability')
    ws.freeze_panes = 'A4'  # Point 16: Freeze header rows
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 70
    ws.column_dimensions['C'].width = 40
    ws.column_dimensions['D'].width = 15

    # Title
    ws.merge_cells('A1:D1')
    ws.cell(row=1, column=1, value=f'ACCEPTANCE CRITERIA TRACEABILITY - {suite.feature_id}')
    ws.cell(row=1, column=1).font = Font(name='Calibri', bold=True, size=14, color=NAVY)
    ws.cell(row=1, column=1).alignment = Alignment(horizontal='center', vertical='center')

    # Headers
    for ci, h in enumerate(['AC#', 'Acceptance Criteria', 'Covering Test Cases', 'Status'], 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr

    r = 4
    for i, (ac, tcs) in enumerate(suite.ac_traceability.items(), 1):
        ws.cell(row=r, column=1, value=f'AC{i}').font = _nf
        ws.cell(row=r, column=1).alignment = _center; ws.cell(row=r, column=1).border = _bdr
        ws.cell(row=r, column=2, value=ac).font = _nf
        ws.cell(row=r, column=2).alignment = _wrap; ws.cell(row=r, column=2).border = _bdr
        ws.cell(row=r, column=3, value=', '.join(tcs)).font = _nf
        ws.cell(row=r, column=3).alignment = _center; ws.cell(row=r, column=3).border = _bdr

        has_coverage = not any('NO COVERAGE' in t for t in tcs)
        status = '✅ Covered' if has_coverage else '⚠️ Gap'
        ws.cell(row=r, column=4, value=status).font = _nf
        ws.cell(row=r, column=4).alignment = _center; ws.cell(row=r, column=4).border = _bdr
        if not has_coverage:
            ws.cell(row=r, column=4).fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
        else:
            ws.cell(row=r, column=4).fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
        r += 1


def _display_device_label(device):
    """Reviewer-facing Device label for the Combinations sheet (Requirement 10.4).

    'Wearable' renders as 'Watch' while the underlying combo dict keeps its canonical value.
    Guarded import: excel export must never hard-fail if plan_matrix is unavailable.
    """
    try:
        from .plan_matrix import display_device
        return display_device(device)
    except Exception:
        return device


def _build_combinations_sheet(wb, suite: TestSuite):
    """Build the Combinations sheet listing all device matrix combinations.

    V10: nine columns — the plan pair (Retail Plan / Wholesale Plan) is now part of the
    combination identity, so a reviewer can see which plan pair each row exercises.
    Legacy V7 rows carry no plan keys; those two cells simply render empty.
    """
    ws = wb.create_sheet('Combinations')
    for _col, _w in zip('ABCDEFGHI', (15, 15, 15, 15, 15, 15, 16, 24, 30)):
        ws.column_dimensions[_col].width = _w

    # Headers
    headers = ['Combination_ID', 'Channel', 'Device', 'OS', 'SIM Type', 'Network',
               'Retail Plan', 'Wholesale Plan', 'Key']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = _hf; c.fill = _hfill; c.alignment = _center; c.border = _bdr

    # Data
    for ri, combo in enumerate(suite.combinations, 2):
        ws.cell(row=ri, column=1, value=combo.get('id', '')).border = _bdr
        ws.cell(row=ri, column=1).alignment = _center
        ws.cell(row=ri, column=2, value=combo.get('channel', '')).border = _bdr
        ws.cell(row=ri, column=2).alignment = _center
        ws.cell(row=ri, column=3, value=_display_device_label(combo.get('device', ''))).border = _bdr
        ws.cell(row=ri, column=3).alignment = _center
        ws.cell(row=ri, column=4, value=combo.get('os', '')).border = _bdr
        ws.cell(row=ri, column=4).alignment = _center
        ws.cell(row=ri, column=5, value=combo.get('sim', '')).border = _bdr
        ws.cell(row=ri, column=5).alignment = _center
        ws.cell(row=ri, column=6, value=combo.get('network', '')).border = _bdr
        ws.cell(row=ri, column=6).alignment = _center
        ws.cell(row=ri, column=7, value=combo.get('retail_plan', '')).border = _bdr
        ws.cell(row=ri, column=7).alignment = _center
        ws.cell(row=ri, column=8, value=combo.get('wholesale_plan', '')).border = _bdr
        ws.cell(row=ri, column=8).alignment = _center
        ws.cell(row=ri, column=9, value=combo.get('key', '')).border = _bdr
        ws.cell(row=ri, column=9).alignment = _center


def _build_data_sources_sheet(wb, suite):
    """Build V8.0 Data Sources summary sheet listing all sources consulted."""
    ws = wb.create_sheet('Data Sources')
    ws.freeze_panes = 'A4'
    ws.column_dimensions['A'].width = 25
    ws.column_dimensions['B'].width = 15
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 40

    # Title
    ws.merge_cells('A1:F1')
    ws.cell(row=1, column=1, value='DATA SOURCES INVENTORY - %s (V8.0)' % suite.feature_id)
    ws.cell(row=1, column=1).font = Font(name='Calibri', bold=True, size=14, color=NAVY)
    ws.cell(row=1, column=1).alignment = Alignment(horizontal='center', vertical='center')

    # Summary row
    total_items = suite.data_inventory.total_testable_items if suite.data_inventory else 0
    ws.merge_cells('A2:F2')
    ws.cell(row=2, column=1, value='Total Testable Items: %d | Engine: %s' % (
        total_items, getattr(suite, 'engine_version', '8.0.0')))
    ws.cell(row=2, column=1).font = Font(name='Calibri', italic=True, size=10)

    # Headers
    headers = ['Source Name', 'Source Type', 'Items Extracted', 'Status', 'Cache Hit', 'Details']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.font = _hf
        c.fill = _hfill
        c.alignment = _center
        c.border = _bdr

    # Data rows
    r = 4
    for source in (suite.data_inventory.sources if suite.data_inventory else []):
        ws.cell(row=r, column=1, value=getattr(source, 'source_name', '')).border = _bdr
        ws.cell(row=r, column=2, value=getattr(source, 'source_type', '')).border = _bdr
        ws.cell(row=r, column=2).alignment = _center
        ws.cell(row=r, column=3, value=getattr(source, 'items_extracted', 0)).border = _bdr
        ws.cell(row=r, column=3).alignment = _center
        ws.cell(row=r, column=4, value=getattr(source, 'status', '')).border = _bdr
        ws.cell(row=r, column=4).alignment = _center
        # Color-code status
        status = getattr(source, 'status', '')
        if status == 'success':
            ws.cell(row=r, column=4).fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
        elif status in ('empty', 'failed'):
            ws.cell(row=r, column=4).fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
        ws.cell(row=r, column=5, value='Yes' if getattr(source, 'cache_hit', False) else 'No').border = _bdr
        ws.cell(row=r, column=5).alignment = _center
        details = ', '.join(getattr(source, 'items_detail', [])[:3])
        ws.cell(row=r, column=6, value=details[:100]).border = _bdr
        r += 1

    # Warnings section
    if suite.data_inventory and suite.data_inventory.warnings:
        r += 1
        ws.cell(row=r, column=1, value='Warnings:').font = Font(name='Calibri', bold=True, color='FF0000')
        for warning in suite.data_inventory.warnings:
            r += 1
            ws.cell(row=r, column=1, value='⚠️ ' + warning)


def _build_coverage_obligations_sheet(wb, suite):
    """Write the deterministic requirement-to-test-case completeness audit."""
    ws = wb.create_sheet('Coverage Obligations')
    ws.freeze_panes = 'A4'
    audit = getattr(suite, 'coverage_audit', None) or {}
    passed = bool(audit.get('passed'))
    ws.merge_cells('A1:F1')
    ws['A1'] = 'SOURCE COVERAGE OBLIGATIONS — %s' % (suite.feature_id or '')
    ws['A1'].font = Font(name='Calibri', bold=True, size=14, color='FFFFFF')
    ws['A1'].fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type='solid')
    ws['A1'].alignment = Alignment(horizontal='center')

    ws.merge_cells('A2:F2')
    ws['A2'] = ('PASS — %d/%d required obligations covered' %
                (audit.get('covered', 0), audit.get('required', 0))
                if passed else
                'BLOCKED — missing: %s' % ', '.join(audit.get('missing', [])))
    ws['A2'].font = Font(name='Calibri', bold=True, color='006100' if passed else '9C0006')
    ws['A2'].fill = PatternFill(start_color='C6EFCE' if passed else 'FFC7CE',
                                end_color='C6EFCE' if passed else 'FFC7CE',
                                fill_type='solid')

    headers = ('Obligation ID', 'Kind', 'Source ID', 'Required', 'Status', 'Covered By')
    for column, value in enumerate(headers, 1):
        cell = ws.cell(row=3, column=column, value=value)
        cell.font = _hf
        cell.fill = _hfill
        cell.alignment = _center
        cell.border = _bdr

    by_obligation = {}
    for tc in getattr(suite, 'test_cases', []) or []:
        for obligation_id in getattr(tc, 'obligation_ids', []) or []:
            by_obligation.setdefault(obligation_id, []).append(tc.summary or tc.sno)

    missing = set(audit.get('missing', []))
    for row, obligation in enumerate(getattr(suite, 'coverage_obligations', []) or [], 4):
        item = obligation if isinstance(obligation, dict) else vars(obligation)
        oid = str(item.get('obligation_id', '') or '')
        required = bool(item.get('required', True))
        status = 'MISSING' if oid in missing else 'COVERED'
        values = (oid, item.get('kind', ''), item.get('source_id', ''),
                  'Yes' if required else 'No', status,
                  '\n'.join(by_obligation.get(oid, [])))
        for column, value in enumerate(values, 1):
            cell = ws.cell(row=row, column=column, value=value)
            cell.font = _nf
            cell.alignment = _wrap
            cell.border = _bdr
        ws.cell(row=row, column=5).fill = PatternFill(
            start_color='FFC7CE' if status == 'MISSING' else 'C6EFCE',
            end_color='FFC7CE' if status == 'MISSING' else 'C6EFCE',
            fill_type='solid')

    widths = {'A': 42, 'B': 20, 'C': 38, 'D': 12, 'E': 14, 'F': 75}
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    ws.auto_filter.ref = 'A3:F%d' % max(3, ws.max_row)


# ═══════════════════════════════════════════════════════════════════════════════
# Contract-v1 additive export
# ═══════════════════════════════════════════════════════════════════════════════
def _contract_source(tc, suite):
    """Return structured owner/provenance without changing the legacy TC model."""
    import re as _re
    target = str(getattr(suite, 'feature_id', '') or '').upper()
    target_pi = str(getattr(suite, 'pi', '') or '')
    tr = getattr(tc, 'traceability', None)
    source_type = str(getattr(tr, 'source_type', '') or 'Unknown')
    source_id = str(getattr(tr, 'source_id', '') or '')
    source_pi = str(getattr(tr, 'pi_label', '') or '')
    extracted = str(getattr(tr, 'extracted_text', '') or '')

    feature_match = _re.search(r'\b[A-Z][A-Z0-9]+-\d+\b', source_id.upper())
    source_feature = feature_match.group(0) if feature_match else target
    if source_type == 'Related Feature' and source_feature != target:
        relationship = 'supporting_only'
    elif source_type == 'Subtask AC':
        relationship = 'explicit_linked'
        source_feature = target
    elif source_feature == target and source_pi and target_pi and source_pi != target_pi:
        relationship = 'same_feature_other_pi'
    elif source_feature == target:
        relationship = 'owned'
    else:
        relationship = 'supporting_only'
    locator = source_id
    if extracted:
        locator += (': ' if locator else '') + extracted[:300]
    return source_feature, source_pi, source_type, locator, relationship


def _build_execution_contract_sheets(wb, suite, log=print):
    """Add machine-readable contract and provenance sheets.

    This is shadow-mode and non-blocking. Unknown prose is marked legacy so TSE preserves
    its existing execution path; only high-confidence contract rows route deterministically.
    """
    from .contract_bridge import (
        CONTRACT_HEADERS, CONTRACT_SHEET, PROVENANCE_HEADERS, PROVENANCE_SHEET,
        StepContract, build_ui_action_contract, infer_step_contract,
        stable_step_uid, stable_tc_uid,
    )

    grounding_result = getattr(suite, '_grounding_result', None)
    ui_typed_rows = ui_total_rows = 0

    for name in (CONTRACT_SHEET, PROVENANCE_SHEET):
        if name in wb.sheetnames:
            del wb[name]
    contract_ws = wb.create_sheet(CONTRACT_SHEET)
    provenance_ws = wb.create_sheet(PROVENANCE_SHEET)
    contract_ws.append(CONTRACT_HEADERS)
    provenance_ws.append(PROVENANCE_HEADERS)

    typed = legacy = supporting = 0
    audit_rows = []
    for tc_idx, tc in enumerate(getattr(suite, 'test_cases', []) or [], 1):
        tc_number = str(getattr(tc, 'sno', '') or tc_idx)
        tc_uid = stable_tc_uid(getattr(suite, 'feature_id', ''), tc_number)
        owner, owner_pi, source_type, locator, relationship = _contract_source(tc, suite)
        grounding = int(getattr(tc, 'grounding_score', 0) or 0)
        obligations = list(getattr(tc, 'obligation_ids', []) or [])
        if relationship == 'supporting_only':
            supporting += 1
            audit_rows.append(tc_uid)
        provenance_ws.append([
            tc_uid, tc_number, owner, owner_pi, source_type, locator,
            relationship, grounding, getattr(tc, 'summary', ''),
        ])
        for step_idx, step in enumerate(getattr(tc, 'steps', []) or [], 1):
            step_num = int(getattr(step, 'step_num', 0) or step_idx)
            inferred = infer_step_contract(
                getattr(step, 'summary', ''), getattr(step, 'expected', '') or
                getattr(step, 'expected_result', ''), getattr(tc, 'summary', ''))

            # Crawl-grounding branch (design §9): when the output-stage grounding
            # pass attached a StepGrounding to a UI step that infer_step_contract did
            # not already type, build the row from the UI action contract instead.
            # Non-UI steps and any step without ``_crawl_grounding`` fall through the
            # generic path untouched (Req 8.2: non-UI output stays byte-identical).
            g = getattr(step, '_crawl_grounding', None)
            step_source_type = source_type
            step_locator = locator
            step_grounding = grounding
            if g is not None and inferred.get('mode') != 'typed':
                ui = build_ui_action_contract(g)
                inferred = ui
                # grounding_percent: 100 (typed) / 0 (unmatched) / None (grounded but
                # unregistered). ``None`` means "keep the TC score" (design §9), so we
                # only overwrite when the builder returns a concrete value.
                ui_pct = ui.get('grounding_percent')
                if ui_pct is not None:
                    step_grounding = ui_pct
                # source_type/source_locator are None for unmatched rows ("existing" in
                # design §9) — keep the generic provenance in that case; otherwise use
                # the crawl provenance.
                if ui.get('source_type') is not None:
                    step_source_type = ui['source_type']
                if ui.get('source_locator') is not None:
                    step_locator = ui['source_locator']

            mode = inferred.get('mode', 'legacy')
            typed += int(mode == 'typed')
            legacy += int(mode != 'typed')
            if g is not None:
                ui_total_rows += 1
                ui_typed_rows += int(mode == 'typed')
            row = StepContract(
                tc_uid=tc_uid, step_uid=stable_step_uid(tc_uid, step_num),
                tc_number=tc_number, step_number=step_num, mode=mode,
                action_type=inferred.get('action_type', 'legacy'),
                action_id=inferred.get('action_id', ''),
                assertion_type=inferred.get('assertion_type', ''),
                target_system=inferred.get('target_system', ''),
                parameters=inferred.get('parameters', {}),
                requires=inferred.get('requires', {}),
                owner_feature_id=owner, owner_pi=owner_pi,
                source_type=step_source_type, source_locator=step_locator,
                relationship=relationship, grounding_percent=step_grounding,
                obligation_ids=obligations,
            )
            contract_ws.append(row.to_excel_row())

    # Feed the contract-writer counts back to the grounding result so the
    # Grounding Report (design §10) can show typed/total rows. Only UI steps that
    # carried a ``_crawl_grounding`` record are counted (design §9).
    if grounding_result is not None:
        grounding_result.typed_rows = ui_typed_rows
        grounding_result.total_rows = ui_total_rows

    # Standard readable formatting; no formulas/macros/hidden behavior.
    for ws in (contract_ws, provenance_ws):
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = _hf
            cell.fill = _hfill
            cell.alignment = _center
        for col in ws.columns:
            letter = col[0].column_letter
            width = min(55, max(12, max(len(str(c.value or '')) for c in col[:200]) + 2))
            ws.column_dimensions[letter].width = width

    # Persist a non-blocking audit for dashboards/logs. It becomes fail-closed only after
    # shadow canaries are approved.
    suite.ownership_audit = {
        'total_tcs': len(getattr(suite, 'test_cases', []) or []),
        'supporting_only_tcs': supporting,
        'supporting_only_uids': audit_rows,
        'typed_steps': typed,
        'legacy_steps': legacy,
        'mode': 'shadow',
    }
    if supporting:
        suite.warnings.append(
            'Contract-v1 ownership audit: %d TC(s) rely on supporting-only provenance: %s'
            % (supporting, ', '.join(audit_rows[:10])))
    log('[EXCEL] Contract-v1 audit: %d typed step(s), %d legacy step(s), '
        '%d supporting-only TC(s)' % (typed, legacy, supporting))


GROUNDING_REPORT_SHEET = 'Grounding Report'
_GROUNDING_ISSUE_HEADERS = [
    'TC Number', 'Step Number', 'Step UID', 'Step Text', 'Reason', 'Candidate Pages',
]


def _pct(numerator, denominator) -> str:
    """Integer-percentage string ('0%' when the denominator is 0)."""
    try:
        n = float(numerator or 0)
        d = float(denominator or 0)
    except (TypeError, ValueError):
        return '0%'
    if d <= 0:
        return '0%'
    return '%d%%' % round(100.0 * n / d)


def _join_candidates(candidates) -> str:
    """Comma-join a candidate-page list/tuple; pass through a plain string."""
    if candidates is None:
        return ''
    if isinstance(candidates, (list, tuple)):
        return ', '.join(str(c) for c in candidates)
    return str(candidates)


def _build_grounding_report_sheet(wb, result):
    """Write the 'Grounding Report' sheet from a ``GroundingResult`` (design §10).

    A metrics block (label | value) is followed by a blank row and an issue
    table: one row per ``GroundingIssue`` in ``result.issues``. Called only
    for UI_Features (``suite._grounding_result is not None``); non-UI suites
    produce no sheet (Req 6.1, 6.5). Formatting mirrors the contract sheets.
    """
    if GROUNDING_REPORT_SHEET in wb.sheetnames:
        del wb[GROUNDING_REPORT_SHEET]
    ws = wb.create_sheet(GROUNDING_REPORT_SHEET)

    ui_steps = int(getattr(result, 'ui_steps', 0) or 0)
    grounded = int(getattr(result, 'grounded_steps', 0) or 0)
    unmatched = int(getattr(result, 'unmatched_steps', 0) or 0)
    filler = int(getattr(result, 'filler_steps', 0) or 0)
    closers_removed = int(getattr(result, 'closers_removed', 0) or 0)
    typed_rows = int(getattr(result, 'typed_rows', 0) or 0)
    total_rows = int(getattr(result, 'total_rows', 0) or 0)
    grounded_share = _pct(grounded, ui_steps)
    typed_share = _pct(typed_rows, total_rows)

    age_days = getattr(result, 'age_days', None)
    metrics = [
        ('Feature', getattr(result, 'feature_id', '')),
        ('Crawl Map', getattr(result, 'crawl_path', '')),
        ('Crawl discovered_at', getattr(result, 'discovered_at', '')),
        ('Crawl age (days)', '' if age_days is None else int(age_days)),
        ('Staleness warning', getattr(result, 'stale_warning', '') or ''),
        ('UI_Steps', ui_steps),
        ('Grounded_Steps', grounded),
        ('Unmatched_Steps', unmatched),
        ('Filler_Steps', filler),
        ('Closer_Steps removed', closers_removed),
        ('Typed rows', typed_rows),
        ('Total rows', total_rows),
        ('Grounded share %', grounded_share),
        ('Typed share %', typed_share),
    ]
    for label, value in metrics:
        ws.append([label, value])
        cell = ws.cell(row=ws.max_row, column=1)
        cell.font = _bf

    # Blank separator row, then the issue table.
    ws.append([])
    header_row_idx = ws.max_row + 1
    ws.append(list(_GROUNDING_ISSUE_HEADERS))
    for cell in ws[header_row_idx]:
        cell.font = _hf
        cell.fill = _hfill
        cell.alignment = _center

    for issue in list(getattr(result, 'issues', []) or []):
        ws.append([
            getattr(issue, 'tc_number', ''),
            getattr(issue, 'step_number', ''),
            getattr(issue, 'step_uid', ''),
            getattr(issue, 'step_text', ''),
            getattr(issue, 'reason', ''),
            _join_candidates(getattr(issue, 'candidates', ())),
        ])

    # Standard readable formatting; no formulas/macros/hidden behavior.
    ws.freeze_panes = 'A%d' % (header_row_idx + 1)
    for col in ws.columns:
        letter = col[0].column_letter
        width = min(60, max(12, max(len(str(c.value or '')) for c in col[:200]) + 2))
        ws.column_dimensions[letter].width = width

    return ws
