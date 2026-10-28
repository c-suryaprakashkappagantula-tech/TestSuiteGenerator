"""AppTest coverage for the TSG dashboard QMetry session guard.

Hermetic: the QMetry candidate list points at a temp storage_state, the auth probe is a
stub (no network), ``Pipeline`` is replaced by a counting stub that raises (so no real
generation / MCP), and the headed-SSO subprocess launch is faked (no browser). The real
storage_state.json files are never read or written.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AppTest = pytest.importorskip('streamlit.testing.v1').AppTest

TSG_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(r'C:\Users\P3314665\Documents\mcp-jira-server')
DASHBOARD = TSG_ROOT / 'TSG_Dashboard_V8.0.py'
JIRA_URL = 'https://jira.charter.com/browse/MWTGPROV-4533'


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    monkeypatch.syspath_prepend(str(TSG_ROOT))
    monkeypatch.chdir(TSG_ROOT)
    import modules.qmetry_steps_source as qss
    import modules.pipeline as pipeline
    from modules import qmetry_session_guard as g

    fake = tmp_path / 'storage_state.json'
    fake.write_text(json.dumps({'cookies': [
        {'domain': '.charter.com', 'name': 'JSESSIONID', 'value': 'x'}]}), encoding='utf-8')
    state = SimpleNamespace(valid=False, probes=0, pipelines=0)

    def probe(cookies, log):
        state.probes += 1
        return state.valid

    class StubPipeline:
        def __init__(self, *a, **kw):
            state.pipelines += 1
            raise RuntimeError('STUB_PIPELINE')

    monkeypatch.setattr(qss, '_candidate_storage_states', lambda: [fake])
    monkeypatch.setattr(qss, '_probe_authenticates', probe)
    monkeypatch.setattr(pipeline, 'Pipeline', StubPipeline)
    state.g = g
    state.tmp = tmp_path
    return state


def _app():
    at = AppTest.from_file(str(DASHBOARD), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    return at


def _errors(at):
    return [e.value for e in at.error]


def _manual(at):
    at.button(key='mode_manual').click().run()
    at.text_input(key='manual_jira_input').input(JIRA_URL).run()
    assert not at.exception, at.exception
    return at


def test_expired_shows_banner_and_refresh_controls(env):
    at = _app()
    assert any('QMetry session expired' in e for e in _errors(at))
    assert at.button(key='qmetry_refresh_btn') is not None
    assert at.checkbox(key='qmetry_ack_without') is not None


def test_expired_manual_links_blocked_without_ack(env):
    at = _manual(_app())
    at.button(key='run_btn').click().run()
    assert any('Generation blocked' in e for e in _errors(at))
    assert at.session_state['_qmetry_gate_last'] == 'block'
    assert env.pipelines == 0


def test_expired_pi_scope_blocked_without_ack(env):
    at = AppTest.from_file(str(DASHBOARD), default_timeout=180)
    at.session_state['selected_pi'] = 'PI-60'
    at.session_state['feature_mode'] = 'manual'
    at.run()
    assert not at.exception, at.exception
    fid = [t for t in at.text_input if t.label == 'Jira Feature ID']
    assert fid, [t.label for t in at.text_input]
    fid[0].input('MWTGPROV-4533').run()
    at.button(key='run_btn').click().run()
    assert any('Generation blocked' in e for e in _errors(at))
    assert at.session_state['_qmetry_gate_last'] == 'block'
    assert env.pipelines == 0


def test_expired_with_ack_proceeds(env):
    at = _manual(_app())
    at.checkbox(key='qmetry_ack_without').check().run()
    at.button(key='run_btn').click().run()
    assert at.session_state['_qmetry_gate_last'] == 'allow_ack'
    assert not any('Generation blocked' in e for e in _errors(at))
    assert env.pipelines == 1


def test_valid_session_no_banner_and_proceeds(env):
    env.valid = True
    at = _manual(_app())
    assert not any('QMetry session expired' in e for e in _errors(at))
    at.button(key='run_btn').click().run()
    assert at.session_state['_qmetry_gate_last'] == 'allow'
    assert env.pipelines == 1


def test_valid_session_status_cached(env):
    env.valid = True
    at = _app()
    at.run()
    at.run()
    assert env.probes == 1


def test_refresh_button_launches_login_copies_and_reprobes(env, monkeypatch):
    g = env.g
    launched = {}
    real_start = g.start_login_subprocess

    def fake_popen(cmd, **kw):
        launched['cmd'] = cmd
        launched['cwd'] = kw.get('cwd')
        # Simulate the worker: SSO succeeded, result JSON written.
        res = cmd[cmd.index('--result') + 1]
        tgts = cmd[cmd.index('--targets') + 1].split(';')
        Path(res).write_text(json.dumps({'ok': True, 'is_logged_in': True, 'copied': tgts}), encoding='utf-8')
        env.valid = True
        return SimpleNamespace(poll=lambda: 0, terminate=lambda: None)

    monkeypatch.setattr(g, 'start_login_subprocess',
                        lambda: real_start(work_dir=env.tmp / 'login', popen=fake_popen))
    at = _app()
    assert any('QMetry session expired' in e for e in _errors(at))
    probes_before = env.probes
    at.button(key='qmetry_refresh_btn').click().run()
    assert not at.exception, at.exception

    cmd = launched['cmd']
    assert cmd[1] == str(g.WORKER)
    assert cmd[cmd.index('--mda-dir') + 1] == str(g.MDA_DIR)
    assert launched['cwd'] == str(g.MDA_DIR)
    tgts = [t.lower() for t in cmd[cmd.index('--targets') + 1].split(';')]
    assert str(REPO_ROOT / 'storage_state.json').lower() in tgts
    assert str(REPO_ROOT / 'TestSuiteGenerator' / 'storage_state.json').lower() in tgts

    assert env.probes > probes_before                     # cache invalidated → re-probed
    assert at.session_state['_qmetry_auth_status']['ok'] is True
    assert not any('QMetry session expired' in e for e in _errors(at))
    assert any('QMetry login refreshed' in s.value for s in at.success)


def test_postrun_warning_when_qmetry_skipped(env):
    env.valid = True
    xlsx = env.tmp / 'MWTGPROV-4533_suite.xlsx'
    xlsx.write_bytes(b'not really xlsx')
    at = AppTest.from_file(str(DASHBOARD), default_timeout=180)
    at.session_state['result_path'] = str(xlsx)
    at.session_state['last_feature_id'] = 'MWTGPROV-4533'
    at.session_state['suite_info'] = {
        'tc_count': 5, 'step_count': 20, 'data_source_count': 2,
        'grounding_pct': -1, 'grounding_badge': '',
        'qmetry_warnings': ['[QMETRY] QMetry supplement skipped: 401 Unauthorized']}
    at.run()
    assert not at.exception, at.exception
    assert any('Suite generated WITHOUT QMetry test cases' in e for e in _errors(at))
