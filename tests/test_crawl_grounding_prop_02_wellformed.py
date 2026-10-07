"""Property 2: Match well-formedness.

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 2: Match well-formedness

For any crawl index and step text, a ``grounded`` result names exactly one
page that exists in the index, and its control (if any) belongs to that
page's controls. For any text whose normalized tokens share no token with any
page name or control label, the result is ``unmatched`` with reason
``no_match``.

**Validates: Requirements 2.1, 2.5**
"""
from __future__ import annotations

from hypothesis import assume, given, settings, strategies as st

from modules.crawl_grounding import CrawlIndex, CrawlMatcher, normalize, tokens
from tests.crawl_grounding_strategies import crawl_maps, step_texts

_CONTROL_TYPES = ('button', 'link', 'tab', 'input', 'dropdown', 'checkbox',
                  'heading', 'column', 'field')

# Candidate words for disjoint texts; filtered per index at draw time.
_POOL_WORDS = ('Customer', 'Line', 'Account', 'Device', 'SIM', 'Feature', 'Plan',
               'Search', 'Details', 'Summary', 'History', 'Transaction', 'Change',
               'Update', 'Status', 'Billing', 'Port', 'Number', 'Activation',
               'please', 'the', 'and', 'then', 'ensure', 'successfully', 'verify',
               'click', 'open', 'navigate', 'to', 'zebra', 'quartz', 'nebula',
               'Verify requirement:', 'Perform the scenario-specific NBOP action:')


def _index_tokens(index: CrawlIndex) -> set:
    toks: set = set()
    for page in index.pages:
        toks.update(tokens(page.name))
        toks.update(tokens(page.display))
        for c in page.controls:
            toks.update(tokens(c.label))
            toks.update(c.norm.split())
    return toks


@settings(max_examples=150, deadline=None)
@given(data=st.data())
def test_grounded_result_is_wellformed(data):
    """Property 2 (part a): grounded -> page in index, control on that page."""
    index = CrawlIndex.from_map(data.draw(crawl_maps(), label='crawl_map'))
    matcher = CrawlMatcher(index)
    text = data.draw(step_texts(index), label='text')
    names = [p.name for p in index.pages]
    page_context = data.draw(
        st.one_of(st.none(), st.sampled_from(names)) if names else st.none(),
        label='page_context')
    wanted = data.draw(st.lists(st.sampled_from(_CONTROL_TYPES), max_size=3,
                                unique=True).map(tuple), label='wanted_types')
    require_control = data.draw(st.booleans(), label='require_control')

    res = matcher.match(text, page_context=page_context, wanted_types=wanted,
                        require_control=require_control)

    assert res.status in ('grounded', 'unmatched')
    if res.status == 'grounded':
        assert res.reason == ''
        assert res.page is not None
        assert index.get(res.page.name) == res.page
        assert res.page in index.pages
        if res.control is not None:
            assert res.control in res.page.controls
            if wanted:
                assert res.control.control_type in wanted
        if require_control:
            assert res.control is not None
    else:
        assert res.page is None and res.control is None
        if len(index) == 0:
            assert res.reason == 'crawl_unavailable'
        else:
            assert res.reason in ('no_match', 'ambiguous')


@settings(max_examples=150, deadline=None)
@given(data=st.data())
def test_disjoint_text_is_no_match(data):
    """Property 2 (part b): no shared normalized token -> unmatched/no_match."""
    index = CrawlIndex.from_map(data.draw(crawl_maps(), label='crawl_map'))
    assume(len(index) > 0)   # empty index -> crawl_unavailable (separate case)
    used = _index_tokens(index)
    pool = [w for w in _POOL_WORDS if not set(tokens(w)) & used]
    assume(pool)
    words = data.draw(st.lists(st.sampled_from(pool), min_size=1, max_size=6),
                      label='words')
    text = ' '.join(words)
    assert not set(tokens(text)) & used, normalize(text)

    names = [p.name for p in index.pages]
    page_context = data.draw(st.one_of(st.none(), st.sampled_from(names)),
                             label='page_context')
    res = CrawlMatcher(index).match(text, page_context=page_context)

    assert res.status == 'unmatched'
    assert res.reason == 'no_match'
    assert res.page is None and res.control is None
    assert res.candidates == ()


def test_empty_index_is_crawl_unavailable():
    """Empty index is the separate crawl_unavailable case, not no_match."""
    res = CrawlMatcher(CrawlIndex.from_map({})).match('Click Search')
    assert (res.status, res.reason) == ('unmatched', 'crawl_unavailable')
