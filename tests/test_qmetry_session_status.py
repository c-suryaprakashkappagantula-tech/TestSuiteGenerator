"""Hermetic tests for ``qmetry_session_status`` (non-raising QMetry auth probe).

No network, no real storage_state.json: the candidate list and the probe are
monkeypatched to point at a temp file.
"""
import json

import pytest

import modules.qmetry_steps_source as qss


@pytest.fixture
def fake_state(tmp_path):
    p = tmp_path / 'storage_state.json'
    p.write_text(json.dumps({'cookies': [
        {'domain': '.charter.com', 'name': 'JSESSIONID', 'value': 'x'}]}), encoding='utf-8')
    return p


def test_status_authenticated(monkeypatch, fake_state):
    monkeypatch.setattr(qss, '_candidate_storage_states', lambda: [fake_state])
    monkeypatch.setattr(qss, '_probe_authenticates', lambda cookies, log: True)
    st = qss.qmetry_session_status()
    assert st['ok'] is True
    assert st['reason'] == ''
    assert st['source'] == str(fake_state)
    assert st['checked_at'] > 0


def test_status_no_storage_state(monkeypatch, tmp_path):
    missing = tmp_path / 'nope' / 'storage_state.json'
    monkeypatch.setattr(qss, '_candidate_storage_states', lambda: [missing])
    monkeypatch.setattr(qss, '_probe_authenticates', lambda cookies, log: True)
    st = qss.qmetry_session_status()
    assert st['ok'] is False
    assert 'No QMetry session authenticated' in st['reason']
    assert st['source'] is None


def test_status_probe_rejects(monkeypatch, fake_state):
    monkeypatch.setattr(qss, '_candidate_storage_states', lambda: [fake_state])
    monkeypatch.setattr(qss, '_probe_authenticates', lambda cookies, log: False)
    seen = []
    st = qss.qmetry_session_status(log=seen.append)
    assert st['ok'] is False
    assert 'No QMetry session authenticated' in st['reason']
    assert any('did not authenticate' in m for m in seen)


def test_status_unexpected_error(monkeypatch):
    def boom(log=print):
        raise RuntimeError('boom')
    monkeypatch.setattr(qss, 'load_qmetry_cookies', boom)
    st = qss.qmetry_session_status()
    assert st['ok'] is False
    assert st['reason'].startswith('probe error')
