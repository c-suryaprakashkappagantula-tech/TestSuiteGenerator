"""Tests for ``modules.qmetry_session_guard`` (dashboard QMetry guard helpers).

Hermetic: no browser, no network, and the real storage_state.json files are never
read or written (copies go to tmp_path; the worker runs against a fake BrowserManager).
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules import qmetry_session_guard as g


# ── get_cached_status / invalidate_status ─────────────────────────────────────

def test_cached_status_ttl_force_and_invalidate():
    calls = []

    def probe():
        calls.append(1)
        return {'ok': True, 'reason': '', 'source': 'x', 'checked_at': 0}

    ss, t0 = {}, 1000.0
    assert g.get_cached_status(ss, now=t0, probe=probe)['ok'] is True
    assert len(calls) == 1
    g.get_cached_status(ss, now=t0 + 299, probe=probe)
    assert len(calls) == 1                         # within TTL: cached
    g.get_cached_status(ss, now=t0 + 301, probe=probe)
    assert len(calls) == 2                         # TTL expired: re-probe
    g.get_cached_status(ss, now=t0 + 302, force=True, probe=probe)
    assert len(calls) == 3                         # forced
    g.invalidate_status(ss)
    assert g.SS_STATUS not in ss
    g.get_cached_status(ss, now=t0 + 303, probe=probe)
    assert len(calls) == 4                         # invalidated


def test_cached_status_probe_exception_is_not_ok():
    def probe():
        raise RuntimeError('kaput')
    st = g.get_cached_status({}, probe=probe)
    assert st['ok'] is False and 'kaput' in st['reason']


# ── gate_decision ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('ok,ack,expected', [
    (True, False, 'allow'), (True, True, 'allow'),
    (False, False, 'block'), (False, True, 'allow_ack')])
def test_gate_decision_matrix(ok, ack, expected):
    gate, msg = g.gate_decision({'ok': ok}, ack)
    assert gate == expected
    if expected == 'block':
        assert g.EXPIRED_MSG in msg
    if expected == 'allow':
        assert msg == ''


# ── qmetry_skip_messages ──────────────────────────────────────────────────────

W_OPT = 'QMetry supplement skipped: No QMetry session authenticated'
W_SUITE = '[QMETRY] QMetry supplement skipped: 401'


def test_skip_messages_from_options_only():
    assert g.qmetry_skip_messages(SimpleNamespace(warnings=[]), {'_qmetry_skip_warning': W_OPT}) == [W_OPT]


def test_skip_messages_from_suite_only():
    s = SimpleNamespace(warnings=['other warning', W_SUITE])
    assert g.qmetry_skip_messages(s, {}) == [W_SUITE]


def test_skip_messages_both_deduped():
    s = SimpleNamespace(warnings=[W_SUITE, W_SUITE])
    assert g.qmetry_skip_messages(s, {'_qmetry_skip_warning': W_SUITE}) == [W_SUITE]
    s2 = SimpleNamespace(warnings=[W_SUITE])
    assert g.qmetry_skip_messages(s2, {'_qmetry_skip_warning': W_OPT}) == [W_OPT, W_SUITE]


def test_skip_messages_neither_and_none():
    assert g.qmetry_skip_messages(SimpleNamespace(warnings=[]), {}) == []
    assert g.qmetry_skip_messages(None, None) == []


# ── distribute_storage_state / refresh_copy_targets ───────────────────────────

def test_distribute_storage_state(tmp_path):
    src = tmp_path / 'src' / 'storage_state.json'
    src.parent.mkdir()
    src.write_bytes(b'{"cookies": [1, 2, 3]}')
    a = tmp_path / 'a' / 'storage_state.json'
    b = tmp_path / 'b' / 'storage_state.json'
    a.parent.mkdir()
    b.parent.mkdir()
    missing = tmp_path / 'missing_dir' / 'storage_state.json'
    copied = g.distribute_storage_state(src, [a, b, a, missing])
    assert copied == [str(a), str(b)]
    assert a.read_bytes() == src.read_bytes() == b.read_bytes()
    assert not missing.exists()
    assert not list(a.parent.glob('.storage_state.*'))   # temp files cleaned


def test_refresh_copy_targets():
    t = [str(p).lower() for p in g.refresh_copy_targets()]
    assert str(g.REPO_ROOT / 'storage_state.json').lower() in t
    assert str(g.REPO_ROOT / 'TestSuiteGenerator' / 'storage_state.json').lower() in t
    assert len(t) == len(set(t))


# ── start_login_subprocess (Popen monkeypatched — no browser) ─────────────────

def test_start_login_subprocess_command(tmp_path):
    seen = {}

    def fake_popen(cmd, **kw):
        seen['cmd'] = cmd
        seen['kw'] = kw
        return SimpleNamespace(poll=lambda: 0)

    proc, result_path, log_path = g.start_login_subprocess(work_dir=tmp_path, python_exe='PY', popen=fake_popen)
    cmd = seen['cmd']
    assert cmd[0] == 'PY'
    assert cmd[1] == str(g.WORKER) and Path(cmd[1]).is_file()
    assert cmd[cmd.index('--result') + 1] == str(tmp_path / 'result.json')
    assert cmd[cmd.index('--mda-dir') + 1] == str(g.MDA_DIR)
    targets = cmd[cmd.index('--targets') + 1].split(';')
    assert targets == [str(p) for p in g.refresh_copy_targets()]
    assert seen['kw']['cwd'] == str(g.MDA_DIR)
    assert seen['kw']['env']['PYTHONIOENCODING'] == 'utf-8'
    assert result_path == tmp_path / 'result.json' and log_path.exists()


def test_read_login_result(tmp_path):
    assert g.read_login_result(tmp_path / 'none.json')['ok'] is False
    p = tmp_path / 'r.json'
    p.write_text('{"ok": true, "copied": ["x"]}', encoding='utf-8')
    assert g.read_login_result(p) == {'ok': True, 'copied': ['x']}


# ── worker end-to-end with a fake BrowserManager ──────────────────────────────

_FAKE_BM = '''
from pathlib import Path

class BrowserManager:
    def __init__(self, headed=False, **kw):
        assert headed is True
        self._ok = False
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def login_jira(self, force=False):
        assert force is True
        Path(__file__).with_name('storage_state.json').write_text('{"cookies": ["fresh"]}')
        self._ok = %s
    @property
    def is_logged_in(self):
        return self._ok
'''


def _run_worker(tmp_path, logged_in):
    mda = tmp_path / 'mda'
    mda.mkdir()
    (mda / 'browser_manager.py').write_text(textwrap.dedent(_FAKE_BM % logged_in), encoding='utf-8')
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    res = tmp_path / 'result.json'
    proc = subprocess.run([sys.executable, str(g.WORKER), '--result', str(res), '--mda-dir', str(mda),
                           '--targets', '%s;%s' % (a, b)], capture_output=True, text=True, timeout=120)
    return proc, json.loads(res.read_text(encoding='utf-8')), a, b


def test_worker_logged_in_copies(tmp_path):
    proc, result, a, b = _run_worker(tmp_path, True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert result['ok'] is True and result['is_logged_in'] is True
    assert result['copied'] == [str(a), str(b)]
    assert a.read_text() == b.read_text() == '{"cookies": ["fresh"]}'


def test_worker_not_logged_in_no_copy(tmp_path):
    proc, result, a, b = _run_worker(tmp_path, False)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert result['ok'] is False and result['copied'] == []
    assert not a.exists() and not b.exists()
