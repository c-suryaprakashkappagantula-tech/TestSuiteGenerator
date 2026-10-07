# -*- coding: utf-8 -*-
"""Property test: TC count and title invariance (task 8.5).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 15: TC count and title invariance
**Validates: Requirements 8.1**

For any suite, UI or not, ``apply_crawl_grounding`` leaves ``len(suite.test_cases)``
and the ordered list of ``tc.summary`` values unchanged.

The pass is exercised with both a small in-memory crawl map (a 'Line Summary'
page carrying a couple of controls) and the missing-crawl path
(``CrawlMapInfo({}, '', 'missing', None)``), for both ``suites(True)`` (UI) and
``suites(False)`` (non-UI). Crawl info is injected so the test never touches the
real ``nbop_ui_map.json``.
"""
import os
import sys

from hypothesis import given, settings, strategies as st

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TSG_ROOT = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(_TSG_ROOT)
for _p in (_TESTS_DIR, _TSG_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.crawl_grounding import apply_crawl_grounding  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402
from crawl_grounding_strategies import suites  # noqa: E402


def _quiet(*_args, **_kwargs):
    """Swallow pass logging so test output stays readable."""


# A small fixed in-memory crawl map: one 'Line Summary' page with a couple of
# controls. Built once and reused as the data for the injected CrawlMapInfo.
_FIXED_MAP = {
    'pages': {
        'Line Summary': {
            'url': 'https://nbop.example.test/line/summary',
            'tabs': [{'text': 'Line Summary'}, {'text': 'Transaction History'}],
            'buttons': [{'text': 'Suspend'}, {'text': 'Restore'}],
            'links': [{'text': 'Account Details'}],
            'headings': [{'tag': 'h1', 'text': 'Line Summary'}],
        },
    },
    'landing_tiles': ['Subscriber'],
    'context_menu': [{'text': 'Transaction History'}],
    'edit_menu': [],
    'nav_menus': [],
    'discovered_at': '2026-04-14T00:00:00',
}


def _present_info():
    return CrawlMapInfo(
        data=_FIXED_MAP,
        path='nbop_ui_map.json',
        error='',
        discovered_at=None,
    )


def _missing_info():
    return CrawlMapInfo(data={}, path='', error='missing', discovered_at=None)


def _check_invariance(suite, crawl_info):
    before_count = len(suite.test_cases)
    before_titles = [tc.summary for tc in suite.test_cases]

    apply_crawl_grounding(suite, log=_quiet, crawl_info=crawl_info)

    after_count = len(suite.test_cases)
    after_titles = [tc.summary for tc in suite.test_cases]

    assert after_count == before_count, (
        f'TC count changed: {before_count} -> {after_count}'
    )
    assert after_titles == before_titles, (
        f'TC summaries changed:\n  before={before_titles!r}\n  after={after_titles!r}'
    )


@settings(max_examples=200, deadline=None)
@given(data=st.data(), ui=st.booleans(), missing=st.booleans())
def test_tc_count_and_title_invariance(data, ui, missing):
    # Draw both UI and non-UI suites, and exercise both the present and the
    # missing crawl-map paths.
    suite = data.draw(suites(ui), label='suite')
    crawl_info = _missing_info() if missing else _present_info()
    _check_invariance(suite, crawl_info)
