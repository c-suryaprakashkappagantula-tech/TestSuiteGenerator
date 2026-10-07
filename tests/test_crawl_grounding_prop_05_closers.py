"""Property 5: Closer removal on UI features.

For any UI_Feature suite whose test cases contain Closer_Steps at arbitrary
positions, after ``apply_crawl_grounding``:

  * no remaining step summary is a Closer_Step (``is_closer_step`` is False for
    every surviving step);
  * each test case's ``step_num`` values are exactly ``1..n`` in order; and
  * every test case that originally had steps *all* of which were Closer_Steps
    now keeps exactly one step and appears in ``suite._grounding_result.issues``
    with reason ``empty_after_closer``.

Per-TC expectations are computed from a deep copy taken *before* the pass, by
counting non-closer vs closer steps per TC with ``modules.crawl_grounding
.is_closer_step``. This keeps the oracle independent of the pass itself.

# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 5: Closer removal on UI features
Validates: Requirements 1.1, 1.4
"""
import copy
import os
import sys

from hypothesis import given, settings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

from modules import crawl_grounding as cg  # noqa: E402
from modules.nbop_ui_knowledge import CrawlMapInfo  # noqa: E402
from crawl_grounding_strategies import suites  # noqa: E402


def _crawl_info():
    """A small fixed in-memory crawl map (CrawlMapInfo).

    Built fresh per example so the pass never shares state across runs. The map
    only needs to be a usable NBOP crawl map; closer removal and renumbering do
    not depend on what grounds, so one page with a few controls is enough.
    """
    pages = {
        'Line Summary': {
            'url': 'https://nbop.example.com/line?ticket=ST-1',
            'buttons': [{'text': 'Change Rate Plan'}],
            'tabs': [{'text': 'Usage Details'}],
            'inputs': [{'label': 'Rate Plan Code'}],
        },
    }
    return CrawlMapInfo(pages, '/tmp/nbop_ui_map.json', '', None)


@settings(max_examples=150, deadline=None)
@given(suites(True))
def test_closers_removed_on_ui_features(suite):
    # Oracle: per-TC (keyed by sno) count of non-closer vs closer steps, taken
    # from a pre-pass deep copy so the pass cannot influence the expectation.
    before = copy.deepcopy(suite)
    expected_all_closer_snos = set()
    for tc in getattr(before, 'test_cases', None) or []:
        steps = list(getattr(tc, 'steps', None) or [])
        non_closers = sum(
            1 for s in steps if not cg.is_closer_step(getattr(s, 'summary', '') or '')
        )
        closers = sum(
            1 for s in steps if cg.is_closer_step(getattr(s, 'summary', '') or '')
        )
        # Originally had steps and every one of them was a closer.
        if steps and non_closers == 0 and closers == len(steps):
            expected_all_closer_snos.add(str(getattr(tc, 'sno', '') or ''))

    cg.apply_crawl_grounding(suite, log=lambda *a: None, crawl_info=_crawl_info())

    result = getattr(suite, '_grounding_result', None)
    assert result is not None, 'UI_Feature suite must get a _grounding_result'

    # Every TC that was entirely closers is reported as empty_after_closer.
    empty_after_closer_snos = {
        str(issue.tc_number)
        for issue in result.issues
        if issue.reason == 'empty_after_closer'
    }
    assert expected_all_closer_snos <= empty_after_closer_snos, (
        'every all-closer TC must appear in issues with reason empty_after_closer'
    )

    for tc in getattr(suite, 'test_cases', None) or []:
        steps = list(getattr(tc, 'steps', None) or [])
        sno = str(getattr(tc, 'sno', '') or '')

        # No surviving step summary is a Closer_Step.
        for step in steps:
            summary = getattr(step, 'summary', '') or ''
            assert not cg.is_closer_step(summary), (
                'closer step survived the pass in TC %s: %r' % (sno, summary)
            )

        # step_num values are exactly 1..n in order.
        assert [getattr(s, 'step_num', None) for s in steps] == list(
            range(1, len(steps) + 1)
        ), 'step_num must be 1..n in TC %s' % sno

        # A TC that was entirely closers keeps exactly one (replacement) step.
        if sno in expected_all_closer_snos:
            assert len(steps) == 1, (
                'all-closer TC %s must keep exactly one replacement step, got %d'
                % (sno, len(steps))
            )
