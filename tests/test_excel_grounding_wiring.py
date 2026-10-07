"""Task 9.1 — wiring of ``apply_crawl_grounding`` into ``generate_excel``.

These tests do not exercise the grounding logic itself (tasks 2–8 cover that).
They only verify the chokepoint wiring the design pins in "Placement in
``generate_excel``":

  * the pass is invoked exactly once, with the suite, after
    sanitize/polish/dedupe and before the workbook is built; and
  * a failure inside the pass is swallowed — the workbook is still written and
    a ``[CRAWL-GROUND] skipped`` line is logged.

The grounding module is monkeypatched in every test, so no crawl map, Jira,
Chalk or browser is touched. ``output_path``/``checkpoint_path`` are redirected
into ``tmp_path`` so nothing lands in the real outputs directory.
"""
import openpyxl
import pytest

import modules.excel_generator as eg
import modules.crawl_grounding as cg
import modules.output_sanitizer as osan
from modules.data_models_v8 import TestSuite, TestCase, TestStep, RoutingAudit


def _minimal_suite():
    """A tiny but real suite built from data_models_v8 — one TC, one step."""
    step = TestStep(step_num=1, summary='Verify requirement: launch NBOP',
                    expected='NBOP opens')
    tc = TestCase(sno='1', summary='Launch NBOP', steps=[step])
    return TestSuite(
        feature_id='MWTGPROV-9001',
        feature_title='Wiring Fixture Feature',
        pi='PI-59',
        test_cases=[tc],
        routing_audit=RoutingAudit(classification='ui'),
    )


@pytest.fixture
def redirect_output(monkeypatch, tmp_path):
    """Make generate_excel write the workbook + checkpoint into tmp_path."""
    out = tmp_path / 'suite.xlsx'
    cp = tmp_path / 'suite_checkpoint.xlsx'
    monkeypatch.setattr(eg, 'output_path', lambda *a, **k: out)
    monkeypatch.setattr(eg, 'checkpoint_path', lambda *a, **k: cp)
    return out


def test_grounding_failure_is_non_fatal(monkeypatch, redirect_output):
    """(a) When the pass raises, the workbook is still written and logged."""
    def _boom(suite, log, *a, **k):
        raise RuntimeError('grounding exploded')

    monkeypatch.setattr(cg, 'apply_crawl_grounding', _boom)

    logs = []
    out = eg.generate_excel(_minimal_suite(), log=logs.append)

    assert out == redirect_output
    assert out.exists(), 'workbook must still be written when grounding fails'
    # The file is a valid workbook with the Test Cases sheet.
    wb = openpyxl.load_workbook(str(out))
    assert 'Test Cases' in wb.sheetnames

    assert any('[CRAWL-GROUND] skipped' in str(m) for m in logs), \
        'a [CRAWL-GROUND] skipped line must be logged on failure'


def test_grounding_called_once_with_suite_after_dedupe(monkeypatch, redirect_output):
    """(b) The pass runs exactly once, with the suite, after sanitize/dedupe."""
    calls = []
    # Marker proving dedupe_names (the last of sanitize/polish/dedupe) ran first.
    order = {'dedupe_done': False}

    real_dedupe = osan.dedupe_names

    def _recording_dedupe(suite, log=print):
        order['dedupe_done'] = True
        return real_dedupe(suite, log)

    def _recorder(suite, log, *a, **k):
        calls.append(suite)
        # Grounding must run only after sanitize/polish/dedupe.
        assert order['dedupe_done'], \
            'apply_crawl_grounding ran before dedupe_names'

    monkeypatch.setattr(osan, 'dedupe_names', _recording_dedupe)
    monkeypatch.setattr(cg, 'apply_crawl_grounding', _recorder)

    suite = _minimal_suite()
    out = eg.generate_excel(suite, log=lambda *_: None)

    assert len(calls) == 1, 'apply_crawl_grounding must be called exactly once'
    assert calls[0] is suite, 'the pass must receive the suite being written'
    assert out.exists()
