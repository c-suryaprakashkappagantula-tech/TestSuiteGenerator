"""Tests for nbop_ui_knowledge.get_crawl_map_info (Req 2.7, 7.3, 7.4)."""
import hashlib
import json
from datetime import datetime

import pytest

from modules import nbop_ui_knowledge as nuk


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _use(monkeypatch, *paths):
    monkeypatch.setattr(nuk, '_UI_MAP_PATHS', list(paths))


def test_missing_file(tmp_path, monkeypatch):
    _use(monkeypatch, tmp_path / 'nope.json')
    info = nuk.get_crawl_map_info()
    assert info.error == 'missing'
    assert info.data == {} and info.path == '' and info.discovered_at is None


def test_garbage_json(tmp_path, monkeypatch):
    p = tmp_path / 'map.json'
    p.write_text('{not json', encoding='utf-8')
    _use(monkeypatch, p)
    info = nuk.get_crawl_map_info()
    assert info.error.startswith('unparseable: ')
    assert info.error != 'unparseable: pages not a dict'
    assert info.data == {} and info.path == str(p)


@pytest.mark.parametrize('payload', [{'pages': []}, {'portal': 'NBOP'}, [1, 2]])
def test_wrong_shape(tmp_path, monkeypatch, payload):
    p = tmp_path / 'map.json'
    p.write_text(json.dumps(payload), encoding='utf-8')
    _use(monkeypatch, p)
    info = nuk.get_crawl_map_info()
    assert info.error == 'unparseable: pages not a dict'
    assert info.data == {}


def test_valid_map_and_file_unchanged(tmp_path, monkeypatch):
    p = tmp_path / 'map.json'
    payload = {'discovered_at': '2026-04-14T23:21:02.619343',
               'pages': {'Search': {'fields': ['MSISDN']}}}
    p.write_text(json.dumps(payload), encoding='utf-8')
    before = _sha(p)
    # first path missing -> falls through to second
    _use(monkeypatch, tmp_path / 'absent.json', p)
    info = nuk.get_crawl_map_info()
    assert info.error == ''
    assert info.path == str(p)
    assert info.data == payload
    assert info.discovered_at == datetime(2026, 4, 14, 23, 21, 2, 619343)
    assert _sha(p) == before


@pytest.mark.parametrize('stamp', ['not-a-date', 12345, None])
def test_bad_discovered_at(tmp_path, monkeypatch, stamp):
    p = tmp_path / 'map.json'
    payload = {'pages': {}}
    if stamp is not None:
        payload['discovered_at'] = stamp
    p.write_text(json.dumps(payload), encoding='utf-8')
    _use(monkeypatch, p)
    info = nuk.get_crawl_map_info()
    assert info.error == ''
    assert info.discovered_at is None


def test_frozen():
    info = nuk.CrawlMapInfo({}, '', 'missing', None)
    with pytest.raises(Exception):
        info.error = ''


def test_load_ui_map_unchanged_for_missing(tmp_path, monkeypatch):
    _use(monkeypatch, tmp_path / 'nope.json')
    monkeypatch.setattr(nuk, '_UI_MAP', None)
    assert nuk._load_ui_map() == {}


def test_real_map_loads_if_present():
    real = nuk.Path(nuk.__file__).parent.parent.parent / 'TMO DashBoard' / 'nbop_discovery' / 'nbop_ui_map.json'
    if not real.exists():
        pytest.skip('real crawl map not present')
    before = _sha(real)
    info = nuk.get_crawl_map_info()
    assert info.error == ''
    assert isinstance(info.data.get('pages'), dict)
    assert _sha(real) == before
