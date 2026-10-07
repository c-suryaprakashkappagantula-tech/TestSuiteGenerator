# -*- coding: utf-8 -*-
"""Property test: grounding pass is idempotent (task 8.4).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 7: Grounding pass is idempotent
**Validates: Requirements 3.3**

Applying ``apply_crawl_grounding`` twice to a UI_Feature suite (the second call
with the *same* injected crawl info) yields the same step summaries, expecteds
and ``StepGrounding`` values as applying it once. In particular the
``[UNMATCHED-CRAWL]`` prefix is applied at most once per step.

Subtlety intentionally NOT hidden: the second ``apply_crawl_grounding`` re-runs
``strip_closer_steps`` (a no-op, closers already gone) and re-grounds the
already-rendered summaries. The design claims this is idempotent. If the second
pass changes an already-grounded / already-rendered summary, expected, or
grounding value, that is a real bug -- this test asserts equality rather than
weakening the comparison, so hypothesis will surface the counterexample.

Crawl info is injected so the test never touches the real ``nbop_ui_map.json``.
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

from modules.crawl_grounding import apply_crawl_grounding, UNMATCHED_PREFIX  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402
from crawl_grounding_strategies import suites  # noqa: E402


def _quiet(*_args, **_kwargs):
    """Swallow pass logging so test output stays readable."""


# A small fixed in-memory crawl map: one 'Line Summary' page carrying a handful
# of controls, plus a tile and a context-menu entry. Reused as the data for the
# injected CrawlMapInfo so some generated steps ground and others do not.
_FIXED_MAP = {
    'pages': {
        'Line Summary': {
            'url': 'https://nbop.example.test/line/summary',
            'tabs': [{'text': 'Line Summary'}, {'text': 'Transaction History'}],
            'buttons': [{'text': 'Suspend'}, {'text': 'Restore'}, {'text': 'Save'}],
            'links': [{'text': 'Account Details'}],
            'inputs': [{'label': 'MSISDN'}],
            'headings': [{'tag': 'h1', 'text': 'Line Summary'}],
        },
        'Device Management': {
            'url': 'https://nbop.example.test/device',
            'buttons': [{'text': 'Change SIM'}],
        },
    },
    'landing_tiles': ['Subscriber'],
    'context_menu': [{'text': 'Transaction History'}],
    'edit_menu': [],
    'nav_menus': [],
    'discovered_at': '2026-04-14T00:00:00',
}


def _info():
    """Injected crawl info; discovered_at=None keeps staleness out of scope."""
    return CrawlMapInfo(
        data=_FIXED_MAP,
        path='nbop_ui_map.json',
        error='',
        discovered_at=None,
    )


def _snapshot(suite):
    """Per-step (summary, expected, grounding) for every step of every TC."""
    snap = []
    for tc in suite.test_cases:
        for step in tc.steps:
            snap.append((
                getattr(step, 'summary', ''),
                getattr(step, 'expected', ''),
                getattr(step, '_crawl_grounding', None),
            ))
    return snap


@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_grounding_pass_is_idempotent(data):
    suite = data.draw(suites(True), label='ui_suite')

    # First application.
    apply_crawl_grounding(suite, log=_quiet, crawl_info=_info())
    first = _snapshot(suite)

    # Second application with the SAME crawl info, on the already-grounded suite.
    apply_crawl_grounding(suite, log=_quiet, crawl_info=_info())
    second = _snapshot(suite)

    # The second pass must reproduce summaries, expecteds and grounding values.
    assert second == first, (
        'apply_crawl_grounding is not idempotent: a second pass changed an '
        'already-grounded step.\n'
        f'  first  = {first!r}\n'
        f'  second = {second!r}'
    )

    # The unmatched prefix must never be doubled by re-grounding.
    for summary, _expected, _grounding in second:
        assert summary.count(UNMATCHED_PREFIX) <= 1, (
            f'[UNMATCHED-CRAWL] prefix applied more than once: {summary!r}'
        )
