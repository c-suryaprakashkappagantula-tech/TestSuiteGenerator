"""Tests for the crawl index building blocks in modules.crawl_grounding:
CrawlControl, CrawlPage, _strip_query, _extract_controls (Req 2.1, 2.2, 2.4)."""
import json
import os
import random

import pytest

from modules import crawl_grounding as cg


def _full_page():
    """Hand-built page dict covering every crawl field the index reads."""
    return {
        'name': 'Line Summary',
        'url': 'https://nbop.example.com/app/line-summary?ticket=ST-12345-abc#top',
        'tabs': [{'text': 'Usage', 'selected': True, 'disabled': False}, {'text': 'Features'}],
        'buttons': [
            {'text': 'Submit', 'aria_label': '', 'title': ''},
            {'text': '', 'aria_label': 'Close Dialog', 'title': 'x'},
            {'text': '', 'title': 'Refresh Data'},            # no aria_label key
            {'text': 'OK'},                                     # too short
            {'text': 'Submit', 'disabled': True},               # duplicate (button, submit)
        ],
        'links': [{'text': 'Transaction History', 'href': '/th'}],
        'inputs': [
            {'label': 'MDN', 'placeholder': 'Enter MDN', 'readonly': True},
            {'label': '', 'placeholder': 'Search ICCID'},
            {'name': 'zipCode'},
            {'label': ':r6:', 'name': ':r6:'},                  # MUI id
        ],
        'dropdowns': [
            {'label': 'Reason Code', 'options': ['Customer Request', {'text': 'Fraud'}, '']},
            {'label': 'demo-simple-select-autowidth', 'options': ['A']},  # MUI id
        ],
        'checkboxes_radios': [{'label': 'Select All'}, {'text': 'Keep Number'}],
        'headings': [{'tag': 'h2', 'text': 'Line Details'}],
        'labels': [{'text': 'Account Status', 'for': 'x'}, {'text': 'ID'}],
        'field_value_map': [{'label': 'Account Status', 'value': 'Active'},
                            {'label': 'Plan Code', 'value': 'P1'}],
        'tables': [{'headers': ['TRANSACTION ID', 'ORDER DATE', '']}, {}],
    }


def _pairs(controls):
    return [(c.control_type, c.label) for c in controls]


# --- _strip_query ---------------------------------------------------------

def test_strip_query_drops_ticket_and_fragment():
    url = 'https://nbop.example.com/app/home?ticket=ST-999-xyz-cas&x=1#frag'
    assert cg._strip_query(url) == 'https://nbop.example.com/app/home'


@pytest.mark.parametrize('raw, expected', [
    ('https://h.example.com/p', 'https://h.example.com/p'),
    ('https://h.example.com/p#a?b', 'https://h.example.com/p'),
    ('', ''), (None, ''), (42, ''),
])
def test_strip_query_edge_cases(raw, expected):
    assert cg._strip_query(raw) == expected


# --- _extract_controls ----------------------------------------------------

def test_extract_controls_covers_every_field():
    controls = cg._extract_controls(_full_page())
    assert _pairs(controls) == [
        ('button', 'Close Dialog'),
        ('button', 'Refresh Data'),
        ('button', 'Submit'),
        ('checkbox', 'Keep Number'),
        ('checkbox', 'Select All'),
        ('column', 'ORDER DATE'),
        ('column', 'TRANSACTION ID'),
        ('dropdown', 'Reason Code'),
        ('field', 'Account Status'),
        ('field', 'Plan Code'),
        ('heading', 'Line Details'),
        ('input', 'MDN'),
        ('input', 'Search ICCID'),
        ('input', 'zipCode'),
        ('link', 'Transaction History'),
        ('tab', 'Features'),
        ('tab', 'Usage'),
    ]
    by_pair = {(c.control_type, c.label): c for c in controls}
    assert dict(by_pair[('dropdown', 'Reason Code')].attrs)['options'] == (
        'Customer Request', 'Fraud')
    assert dict(by_pair[('input', 'MDN')].attrs) == {'readonly': True}
    assert by_pair[('link', 'Transaction History')].norm == 'transaction history'


def test_extract_controls_drops_mui_ids_and_short_labels():
    labels = {c.label for c in cg._extract_controls(_full_page())}
    assert not any(l.startswith('demo-simple-select') or l == ':r6:' for l in labels)
    assert 'OK' not in labels and 'ID' not in labels
    assert all(len(l) >= 3 for l in labels)


def test_extract_controls_collapses_duplicates():
    controls = cg._extract_controls(_full_page())
    keys = [(c.control_type, c.norm) for c in controls]
    assert len(keys) == len(set(keys))
    # 'Account Status' appears in both labels and field_value_map -> one field
    assert sum(1 for c in controls if c.label == 'Account Status') == 1
    # duplicate Submit buttons collapse to one
    assert sum(1 for c in controls if c.label == 'Submit') == 1


def test_extract_controls_deterministic_under_shuffle():
    base = _full_page()
    expected = cg._extract_controls(base)
    rnd = random.Random(7)
    for _ in range(10):
        page = {}
        keys = list(base)
        rnd.shuffle(keys)
        for k in keys:
            v = base[k]
            if isinstance(v, list):
                v = list(v)
                rnd.shuffle(v)
            page[k] = v
        assert cg._extract_controls(page) == expected


def test_extract_controls_handles_missing_and_malformed():
    assert cg._extract_controls({}) == ()
    assert cg._extract_controls(None) == ()
    page = {'buttons': None, 'tabs': [], 'inputs': [None, 'Search Box'],
            'tables': [{'headers': None}, 'x'], 'dropdowns': [{'label': 'Status'}]}
    assert _pairs(cg._extract_controls(page)) == [('dropdown', 'Status'), ('input', 'Search Box')]


def test_dataclasses_are_frozen():
    ctrl = cg.CrawlControl('button', 'Submit', 'submit', ())
    page = cg.CrawlPage('Tile: History', 'History', 'tile', '', (ctrl,))
    with pytest.raises(Exception):
        ctrl.label = 'x'
    with pytest.raises(Exception):
        page.name = 'x'


# --- optional smoke against the real crawl map ----------------------------

_REAL_MAP = os.path.join(os.path.dirname(__file__), '..', '..', 'TMO DashBoard',
                         'nbop_discovery', 'nbop_ui_map.json')


@pytest.mark.skipif(not os.path.exists(_REAL_MAP), reason='crawl map absent')
def test_real_map_smoke():
    with open(_REAL_MAP, encoding='utf-8') as fh:
        data = json.load(fh)
    pages = data.get('pages') or {}
    items = pages.values() if isinstance(pages, dict) else pages
    for page in items:
        assert 'ticket=' not in cg._strip_query(page.get('url'))
        for c in cg._extract_controls(page):
            assert len(c.label) >= 3 and c.norm
            assert not c.label.startswith('demo-simple-select')


# --- CrawlIndex.from_map (Req 2.2, 2.3) ------------------------------------

def _synthetic_map():
    return {
        'discovered_at': '2026-04-14T10:00:00',
        'pages': {
            'Subscriber Profile': {'name': 'Subscriber Profile',
                                   'url': 'https://h.example.com/sp?ticket=ST-1',
                                   'buttons': [{'text': 'Edit Line'}]},
            'Tile: History': {'url': 'https://h.example.com/hist?ticket=ST-2',
                              'tabs': [{'text': 'Activations'}]},
            'Tile: History > Port Out History': {'url': 'https://h.example.com/po',
                                                 'headings': [{'text': 'Port Out'}]},
            'Context: Line History': {'url': 'https://h.example.com/lh'},
            'Button: Features': {'url': 'https://h.example.com/f'},
            'Manage Line: Change Features': {'url': 'https://h.example.com/cf?ticket=ST-3',
                                             'checkboxes_radios': [{'label': 'Hotspot'}]},
        },
        'landing_tiles': ['History', 'SFTP Reports'],
        'context_menu': [{'text': 'Line History', 'href': '#'}, {'text': 'Notifications'}],
        'edit_menu': [{'text': 'Change Features', 'group': 'Manage Line',
                       'page': 'Manage Line: Change Features'},
                      {'text': 'Change Features'},          # legacy duplicate, covered
                      {'text': 'Swap MDN'}],
        'nav_menus': [{'text': 'Reports'}],
    }


def _summary(index):
    return [(p.name, p.display, p.kind, p.url, len(p.controls)) for p in index.pages]


def test_from_map_one_entry_per_source():
    idx = cg.CrawlIndex.from_map(_synthetic_map())
    assert _summary(idx) == [
        ('Button: Features', 'Features', 'page', 'https://h.example.com/f', 0),
        ('Context: Line History', 'Line History', 'context_menu', 'https://h.example.com/lh', 0),
        ('Context: Notifications', 'Notifications', 'context_menu', '', 0),
        ('Manage Line: Change Features', 'Change Features', 'edit_menu',
         'https://h.example.com/cf', 1),
        ('Reports', 'Reports', 'nav_menu', '', 0),
        ('Subscriber Profile', 'Subscriber Profile', 'page', 'https://h.example.com/sp', 1),
        ('Swap MDN', 'Swap MDN', 'edit_menu', '', 0),
        ('Tile: History', 'History', 'tile', 'https://h.example.com/hist', 1),
        ('Tile: History > Port Out History', 'Port Out History', 'page',
         'https://h.example.com/po', 1),
        ('Tile: SFTP Reports', 'SFTP Reports', 'tile', '', 0),
    ]
    assert idx.get('Tile: History > Port Out History').display == 'Port Out History'
    assert idx.get('missing') is None


def test_from_map_merge_vs_standalone():
    idx = cg.CrawlIndex.from_map(_synthetic_map())
    merged = idx.get('Tile: History')
    assert merged.kind == 'tile' and [c.label for c in merged.controls] == ['Activations']
    standalone = idx.get('Tile: SFTP Reports')
    assert standalone.kind == 'tile' and standalone.controls == () and standalone.url == ''
    # edit entry with 'page' merges into its twin; no separate 'Change Features' page
    assert idx.get('Change Features') is None
    assert idx.get('Manage Line: Change Features').controls[0].label == 'Hotspot'


def test_from_map_identical_for_shuffled_copy():
    base = _synthetic_map()
    expected = cg.CrawlIndex.from_map(base).pages
    rnd = random.Random(11)
    for _ in range(10):
        data = {}
        keys = list(base)
        rnd.shuffle(keys)
        for k in keys:
            v = base[k]
            if isinstance(v, dict):
                sub = list(v.items())
                rnd.shuffle(sub)
                v = dict(sub)
            elif isinstance(v, list):
                v = list(v)
                rnd.shuffle(v)
            data[k] = v
        assert cg.CrawlIndex.from_map(data).pages == expected


def test_from_map_handles_empty_and_malformed():
    assert cg.CrawlIndex.from_map(None).pages == ()
    assert cg.CrawlIndex.from_map({}).pages == ()
    idx = cg.CrawlIndex.from_map({'pages': {'X Page': None}, 'landing_tiles': None,
                                  'edit_menu': [None, {}], 'nav_menus': ['Home']})
    assert _summary(idx) == [('Home', 'Home', 'nav_menu', '', 0),
                             ('X Page', 'X Page', 'page', '', 0)]


@pytest.mark.skipif(not os.path.exists(_REAL_MAP), reason='crawl map absent')
def test_real_map_index_smoke():
    with open(_REAL_MAP, encoding='utf-8') as fh:
        data = json.load(fh)
    idx = cg.CrawlIndex.from_map(data)
    assert len(idx.pages) >= 26
    assert all('ticket=' not in p.url and '?' not in p.url for p in idx.pages)
    names = [p.name for p in idx.pages]
    assert names == sorted(names) and len(names) == len(set(names))
    cf = idx.get('Manage Line: Change Features')
    assert cf is not None and cf.kind == 'edit_menu' and cf.display == 'Change Features'
