"""Basic tests for modules.crawl_grounding: normalize, staleness, grounding_percent
(Req 7.1, 7.2)."""
from datetime import datetime, timedelta, timezone

import pytest

from modules import crawl_grounding as cg
from modules.nbop_ui_knowledge import CrawlMapInfo


# --- normalize -------------------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('Add & Remove', 'add and remove'),
    ('Click "Submit"', 'click submit'),
    ("Open \u2018Usage\u2019 tab", 'open usage tab'),
    ('Manage Line: Change Features', 'manage line change features'),
    ('Save, Next!', 'save next'),
    ('SIM/eSIM Details', 'sim/esim details'),
    ('  Tile:   History \t\n ', 'tile history'),
    ('', ''),
    (None, ''),
])
def test_normalize_examples(raw, expected):
    assert cg.normalize(raw) == expected


def test_normalize_idempotent():
    s = cg.normalize('Line & "Device" -- Info/Status')
    assert cg.normalize(s) == s


# --- stale_threshold_days --------------------------------------------------

def test_threshold_missing(monkeypatch):
    monkeypatch.delenv('TSG_CRAWL_STALE_DAYS', raising=False)
    assert cg.stale_threshold_days() == (90, '')


def test_threshold_valid(monkeypatch):
    monkeypatch.setenv('TSG_CRAWL_STALE_DAYS', '30')
    assert cg.stale_threshold_days() == (30, '')


@pytest.mark.parametrize('bad', ['abc', '-5', '1.5'])
def test_threshold_invalid(monkeypatch, bad):
    monkeypatch.setenv('TSG_CRAWL_STALE_DAYS', bad)
    days, note = cg.stale_threshold_days()
    assert days == 90
    assert 'TSG_CRAWL_STALE_DAYS' in note


# --- staleness -------------------------------------------------------------

def _info(discovered_at):
    return CrawlMapInfo(data={'pages': {}}, path='x.json', error='',
                        discovered_at=discovered_at)


def test_staleness_at_threshold_no_warning():
    d = datetime(2026, 4, 14, 10, 0, 0)
    assert cg.staleness(_info(d), d + timedelta(days=90), 90) is None


def test_staleness_over_threshold_warns():
    d = datetime(2026, 4, 14, 10, 0, 0)
    msg = cg.staleness(_info(d), d + timedelta(days=91), 90)
    assert msg == ('NBOP crawl map is stale: discovered_at=2026-04-14, '
                   'age=91 days (threshold 90)')


def test_staleness_none_discovered_at():
    assert cg.staleness(_info(None), datetime(2026, 1, 1), 90) is None


def test_staleness_mixed_timezones():
    d = datetime(2026, 4, 14, tzinfo=timezone.utc)
    msg = cg.staleness(_info(d), datetime(2026, 4, 14) + timedelta(days=11), 10)
    assert msg is not None and 'age=11 days' in msg


# --- grounding_percent -----------------------------------------------------

def test_grounding_percent_registered_ui_action():
    sg = cg.StepGrounding(kind='ui_action', reason='', action_id='nbop.ui.open_tab')
    assert sg.grounding_percent == 100


@pytest.mark.parametrize('kind, action_id', [
    ('ui_action', 'nbop.ui.not_registered'),
    ('ui_action', ''),
    ('legacy_typed', 'nbop.ui.open_tab'),
    ('unmatched', ''),
])
def test_grounding_percent_zero(kind, action_id):
    sg = cg.StepGrounding(kind=kind, reason='no_match', action_id=action_id)
    assert sg.grounding_percent == 0


def test_grounding_result_defaults():
    r = cg.GroundingResult(feature_id='F-1', crawl_path='', crawl_error='missing',
                           discovered_at='', age_days=None, stale_warning='',
                           ui_steps=0, grounded_steps=0, unmatched_steps=0,
                           filler_steps=0, closers_removed=0)
    assert r.typed_rows == 0 and r.total_rows == 0 and r.issues == []
