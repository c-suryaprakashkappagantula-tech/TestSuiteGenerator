# -*- coding: utf-8 -*-
"""Property test: matcher determinism and order independence (task 5.3).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 1: Matcher determinism and order independence
**Validates: Requirements 2.3**

For any step text, page context and crawl map, and for any permutation of the
crawl map's dict keys and list orders, ``CrawlMatcher.match`` returns an
identical ``MatchResult`` across repeated calls and across permutations.
"""
import os
import random
import sys

from hypothesis import given, settings, strategies as st

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TSG_ROOT = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(_TSG_ROOT)
for _p in (_TESTS_DIR, _TSG_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.crawl_grounding import CrawlIndex, CrawlMatcher  # noqa: E402
from crawl_grounding_strategies import crawl_maps, permute_map, step_texts  # noqa: E402

# Control types produced by CrawlIndex (see CrawlControl.control_type).
_CONTROL_TYPES = ('button', 'link', 'tab', 'input', 'dropdown', 'checkbox',
                  'heading', 'column', 'field')


def _draw_args(data, index):
    """Draw (text, page_context, wanted_types, require_control) for ``index``."""
    text = data.draw(step_texts(index), label='text')
    names = [p.name for p in index.pages]
    ctx_strategy = st.none()
    if names:
        ctx_strategy = st.one_of(st.none(), st.sampled_from(names))
    page_context = data.draw(ctx_strategy, label='page_context')
    wanted_types = tuple(data.draw(
        st.lists(st.sampled_from(_CONTROL_TYPES), max_size=3, unique=True),
        label='wanted_types'))
    require_control = data.draw(st.booleans(), label='require_control')
    return text, page_context, wanted_types, require_control


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 1: Matcher determinism and order independence
@settings(max_examples=150, deadline=None)
@given(crawl=crawl_maps(), seeds=st.lists(st.integers(min_value=0, max_value=2**32 - 1),
                                          min_size=1, max_size=3),
       data=st.data())
def test_match_deterministic_and_order_independent(crawl, seeds, data):
    """**Validates: Requirements 2.3**"""
    index = CrawlIndex.from_map(crawl)
    matcher = CrawlMatcher(index)
    text, page_context, wanted_types, require_control = _draw_args(data, index)

    def _run(m):
        return m.match(text, page_context=page_context, wanted_types=wanted_types,
                       require_control=require_control)

    baseline = _run(matcher)

    # Repeated calls on the same matcher and on a freshly built one are identical.
    assert _run(matcher) == baseline
    assert _run(CrawlMatcher(CrawlIndex.from_map(crawl))) == baseline

    # Permuted maps (dict key order + list orders shuffled) give the same result.
    for seed in seeds:
        permuted = permute_map(crawl, random.Random(seed))
        result = _run(CrawlMatcher(CrawlIndex.from_map(permuted)))
        assert result == baseline, (
            f'permutation seed={seed} changed result:\n'
            f'  baseline={baseline!r}\n  permuted={result!r}')


# Feature: tsg-nbop-crawl-grounded-ui-steps, Property 1: Matcher determinism and order independence
@settings(max_examples=100, deadline=None)
@given(crawl=crawl_maps(), seed=st.integers(min_value=0, max_value=2**32 - 1),
       data=st.data())
def test_match_defaults_order_independent(crawl, seed, data):
    """Default arguments (no page context, no wanted types) are order independent.

    **Validates: Requirements 2.3**
    """
    index = CrawlIndex.from_map(crawl)
    text = data.draw(step_texts(index), label='text')
    baseline = CrawlMatcher(index).match(text)
    assert CrawlMatcher(index).match(text) == baseline
    permuted = CrawlIndex.from_map(permute_map(crawl, random.Random(seed)))
    assert CrawlMatcher(permuted).match(text) == baseline
