# -*- coding: utf-8 -*-
"""Property test: complete classification with verbatim crawl labels (task 8.3).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 6: Complete classification with verbatim crawl labels
**Validates: Requirements 2.4, 3.1, 3.2, 9.1, 9.2**

For any UI_Feature suite (``suites(True)``) and an injected crawl map, after
``apply_crawl_grounding``:

  * every remaining step carries a ``_crawl_grounding`` whose ``kind`` is one of
    ``{'ui_action', 'legacy_typed', 'unmatched'}``;
  * no remaining step summary starts with ``Verify requirement:`` or
    ``Perform the scenario-specific NBOP action:`` (filler is gone);
  * every ``ui_action`` step whose grounding resolved a page/control carries the
    verbatim crawl page display and control label somewhere in the summary -- or
    the ``[NBOP: {page.name} > {label}]`` suffix form the renderer appends when
    the wording did not already contain them.

The injected map is a fixed, small map whose page displays and control labels
overlap the words in the ``suites()`` UI step pool, so some steps ground to a
``ui_action`` and others fall through to ``unmatched`` -- both outcomes are fine;
the invariants above must hold for every step regardless. Crawl info is injected
so the test never touches the real ``nbop_ui_map.json``.
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

from modules.contract_bridge import UI_ACTION_IDS  # noqa: E402
from modules.crawl_grounding import apply_crawl_grounding  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402
from crawl_grounding_strategies import suites  # noqa: E402

# Filler prefixes that must never survive the pass (design §7 step 2).
_FILLER_PREFIXES = (
    'Verify requirement:',
    'Perform the scenario-specific NBOP action:',
)
_VALID_KINDS = {'ui_action', 'legacy_typed', 'unmatched'}


def _quiet(*_args, **_kwargs):
    """Swallow pass logging so test output stays readable."""


# A fixed, small crawl map. Page displays ('Line Summary', 'Device Management',
# 'Transaction History') and control labels ('Account Details', 'Suspend',
# 'Save', 'Search', 'Billing', ...) overlap the words used by the suites() UI
# step pool, so a good share of generated steps ground to a real ui_action while
# the rest stay unmatched. Both outcomes are exercised.
_FIXED_MAP = {
    'pages': {
        'Line Summary': {
            'url': 'https://nbop.example.test/line/summary',
            'tabs': [{'text': 'Line Summary'}, {'text': 'Transaction History'},
                     {'text': 'Billing'}],
            'buttons': [{'text': 'Suspend'}, {'text': 'Restore'}, {'text': 'Save'}],
            'links': [{'text': 'Account Details'}],
            'inputs': [{'label': 'Search'}],
            'headings': [{'tag': 'h1', 'text': 'Line Summary'}],
        },
        'Device Management': {
            'url': 'https://nbop.example.test/device',
            'buttons': [{'text': 'Save'}],
            'labels': [{'text': 'Profile Settings'}],
        },
    },
    'landing_tiles': ['Subscriber'],
    'context_menu': [{'text': 'Transaction History'}],
    'edit_menu': [],
    'nav_menus': [],
    'discovered_at': '2026-04-14T00:00:00',
}


def _info():
    # discovered_at left None so the pass never emits a staleness warning; the
    # map itself still carries its own discovered_at string for completeness.
    return CrawlMapInfo(data=_FIXED_MAP, path='nbop_ui_map.json', error='',
                        discovered_at=None)


@settings(max_examples=150, deadline=None)
@given(data=st.data())
def test_complete_classification_with_verbatim_labels(data):
    suite = data.draw(suites(True), label='ui_suite')

    apply_crawl_grounding(suite, log=_quiet, crawl_info=_info())

    for tc in suite.test_cases:
        for step in tc.steps:
            summary = step.summary or ''
            grounding = getattr(step, '_crawl_grounding', None)

            # (1) every step is classified.
            assert grounding is not None, (
                f'step has no _crawl_grounding: {summary!r}'
            )
            assert grounding.kind in _VALID_KINDS, (
                f'unexpected grounding kind {grounding.kind!r} for {summary!r}'
            )

            # (2) no filler prefix survives.
            for prefix in _FILLER_PREFIXES:
                assert not summary.startswith(prefix), (
                    f'filler prefix survived the pass: {summary!r}'
                )

            # (3) ui_action steps carry the verbatim page display and control
            #     label (either inline or via the appended suffix form).
            if grounding.kind != 'ui_action':
                continue

            page = grounding.page
            display = _display_for(page)
            # The suffix form the renderer appends when the wording did not
            # already contain page/label: ' [NBOP: {page.name} > {label}]'.
            has_suffix = '[NBOP:' in summary and page and page in summary

            if display:
                assert display in summary or has_suffix, (
                    f'page display {display!r} (page={page!r}) missing from '
                    f'ui_action summary {summary!r}'
                )

            label = grounding.control_label
            if grounding.control_type != 'page' and label:
                assert label in summary, (
                    f'control label {label!r} missing from ui_action '
                    f'summary {summary!r}'
                )

            # A typed ui_action id round-trips to a 100% grounding share.
            if grounding.action_id in UI_ACTION_IDS:
                assert grounding.grounding_percent == 100


def _display_for(page_name):
    """Verbatim crawl *display* for a resolved page name.

    The grounding stores ``page`` as ``CrawlPage.name``; the renderer embeds the
    page *display* (name without a 'Tile: '/'Context: ' prefix). Mirror the
    display derivation so the assertion checks the exact spelling the renderer
    used.
    """
    if not page_name:
        return ''
    for prefix in ('Tile: ', 'Context: ', 'Button: '):
        if page_name.startswith(prefix):
            return page_name[len(prefix):]
    return page_name
