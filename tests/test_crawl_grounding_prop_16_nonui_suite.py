"""Property 16 (suite part): Non-UI suites are untouched by the grounding pass.

A suite whose ``routing_audit.classification`` is not a UI_Feature (``api``, or
``hybrid`` carrying an API signal) must be left exactly as it was: the pass
returns immediately with no attributes set. This test drives ``suites(False)``
and asserts that, after ``apply_crawl_grounding``:

  * the suite is structurally identical to a ``copy.deepcopy`` taken before the
    call (same ``feature_id``; same per-TC ``sno``/``summary``; same per-step
    ``step_num``/``summary``/``expected`` in order); and
  * the suite has no ``_grounding_result`` attribute, and no step carries a
    ``_crawl_grounding`` attribute.

# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 16: Non-UI suites are untouched (suite part)
Validates: Requirements 1.2, 6.5, 8.2
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
    """A small fixed in-memory crawl map (CrawlMapInfo) with one page.

    Built fresh per example so the pass never shares state across runs. For a
    Non-UI suite the pass returns before touching it, but we still pass a
    realistic value so the test exercises the real early-return branch rather
    than a degenerate None path.
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


def _structure(suite):
    """A hashable/comparable structural snapshot of a suite.

    Captures only the fields Property 16 pins: the suite feature id, each test
    case's sno/summary, and each step's step_num/summary/expected in order.
    """
    return (
        getattr(suite, 'feature_id', None),
        tuple(
            (
                getattr(tc, 'sno', None),
                getattr(tc, 'summary', None),
                tuple(
                    (
                        getattr(step, 'step_num', None),
                        getattr(step, 'summary', None),
                        getattr(step, 'expected', None),
                    )
                    for step in (getattr(tc, 'steps', None) or [])
                ),
            )
            for tc in (getattr(suite, 'test_cases', None) or [])
        ),
    )


@settings(max_examples=150, deadline=None)
@given(suites(False))
def test_non_ui_suite_structurally_unchanged(suite):
    before = copy.deepcopy(suite)
    before_structure = _structure(before)

    cg.apply_crawl_grounding(suite, log=lambda *a: None, crawl_info=_crawl_info())

    # Structural equality: same feature id, same TCs (sno/summary) and the same
    # steps (step_num/summary/expected) in order.
    assert _structure(suite) == before_structure

    # No grounding artefacts were attached anywhere on a Non-UI suite.
    assert not hasattr(suite, '_grounding_result')
    for tc in getattr(suite, 'test_cases', None) or []:
        for step in getattr(tc, 'steps', None) or []:
            assert not hasattr(step, '_crawl_grounding')
