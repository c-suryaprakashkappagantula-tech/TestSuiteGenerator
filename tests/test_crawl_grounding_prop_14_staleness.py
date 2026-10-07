# -*- coding: utf-8 -*-
"""Property test: crawl staleness warning (task 3.3).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
**Validates: Requirements 7.1, 7.2**
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

_TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_TSG_ROOT)
for _p in (_TSG_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules import crawl_grounding as cg  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402

_ENV = cg.STALE_DAYS_ENV


@contextmanager
def _env(value):
    """Set/unset TSG_CRAWL_STALE_DAYS for the block, then restore."""
    saved = os.environ.get(_ENV)
    try:
        if value is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = value
        yield
    finally:
        if saved is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = saved


def _info(discovered_at):
    return CrawlMapInfo(data={}, path='', error='', discovered_at=discovered_at)


def _parses_non_negative(s):
    try:
        return int(s.strip()) >= 0
    except ValueError:
        return False


_DT = st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2040, 1, 1))
_DELTA = st.timedeltas(min_value=timedelta(days=-400), max_value=timedelta(days=400))
_THRESH = st.integers(min_value=0, max_value=365)


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=200, deadline=None)
@given(discovered=_DT, delta=_DELTA, threshold=_THRESH, aware=st.booleans())
def test_staleness_warns_iff_age_exceeds_threshold(discovered, delta, threshold, aware):
    if aware:
        discovered = discovered.replace(tzinfo=timezone.utc)
    now = discovered + delta
    age = (now - discovered).days
    assert cg.crawl_age_days(discovered, now) == age

    warning = cg.staleness(_info(discovered), now, threshold)
    if age > threshold:
        assert warning == (
            f'NBOP crawl map is stale: discovered_at={discovered:%Y-%m-%d}, '
            f'age={age} days (threshold {threshold})')
        assert discovered.strftime('%Y-%m-%d') in warning
        assert f'age={age} days' in warning
    else:
        assert warning is None


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=100, deadline=None)
@given(now=_DT, threshold=_THRESH)
def test_staleness_none_when_discovered_at_unknown(now, threshold):
    assert cg.crawl_age_days(None, now) is None
    assert cg.staleness(_info(None), now, threshold) is None


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=150, deadline=None)
@given(days=st.integers(min_value=0, max_value=100000),
       pad=st.sampled_from(['', ' ', '  ']))
def test_threshold_valid_env_is_used(days, pad):
    with _env(f'{pad}{days}{pad}'):
        assert cg.stale_threshold_days() == (days, '')


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=150, deadline=None)
@given(raw=st.one_of(
    st.integers(max_value=-1).map(str),
    st.text(min_size=1).filter(lambda s: s.strip() != '' and '\x00' not in s)
    .filter(lambda s: not _parses_non_negative(s)),
))
def test_threshold_invalid_env_falls_back_to_default_with_note(raw):
    try:
        with _env(raw):
            days, note = cg.stale_threshold_days()
    except ValueError:
        # Platform rejected the env value (e.g. on Windows); nothing to test.
        return
    assert days == cg.DEFAULT_STALE_DAYS == 90
    assert note != ''
    assert _ENV in note


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=100, deadline=None)
@given(raw=st.sampled_from([None, '', ' ', '\t']))
def test_threshold_missing_or_blank_env_defaults_silently(raw):
    with _env(raw):
        assert cg.stale_threshold_days() == (90, '')


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 14: Staleness warning
@settings(max_examples=150, deadline=None)
@given(discovered=_DT, delta=_DELTA, env_days=st.integers(min_value=0, max_value=365),
       use_env=st.booleans())
def test_staleness_with_configured_or_default_threshold(discovered, delta, env_days, use_env):
    now = discovered + delta
    with _env(str(env_days) if use_env else None):
        threshold, _ = cg.stale_threshold_days()
    assert threshold == (env_days if use_env else 90)
    warning = cg.staleness(_info(discovered), now, threshold)
    assert (warning is not None) == ((now - discovered).days > threshold)

