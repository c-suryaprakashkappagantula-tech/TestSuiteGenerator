"""Property 4: Ties are ambiguous.

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 4: Ties are ambiguous

*For any* crawl index where two or more pages reach the same top score for a
step, and the TC page context is not among them, the result is ``unmatched``
with reason ``ambiguous``. Its ``candidates`` equal the sorted set of tied page
names. When the page context is one of the tied pages, the step grounds to it.

**Validates: Requirements 2.6**
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.crawl_grounding import (  # noqa: E402
    MIN_SCORE, CrawlControl, CrawlIndex, CrawlMatcher, CrawlPage, normalize,
)

# Two disjoint vocabularies: words that appear in the step text, and words
# used for display names that must never match the step text.
TEXT_WORDS = ('account', 'billing', 'details', 'history', 'line', 'plan',
              'profile', 'search', 'service', 'subscriber', 'usage', 'device')
OTHER_WORDS = ('zephyr', 'quartz', 'nimbus', 'vortex', 'ember', 'tundra',
               'lagoon', 'prism')
KINDS = ('page', 'tile', 'context_menu', 'edit_menu', 'nav_menu')

_phrase_words = st.lists(st.sampled_from(TEXT_WORDS), min_size=1, max_size=3, unique=True)
_other_display = st.lists(st.sampled_from(OTHER_WORDS), min_size=1, max_size=2,
                          unique=True).map(' '.join)
_names = st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghij:>', min_size=1,
                 max_size=12).map(str.strip).filter(bool)


def _ctrl(label: str, control_type: str = 'button') -> CrawlControl:
    return CrawlControl(control_type, label, normalize(label))


def _page(name: str, display: str, kind: str, controls=()) -> CrawlPage:
    return CrawlPage(name, display, kind, f'https://nbop.example/{len(name)}', tuple(controls))


@st.composite
def tie_scenarios(draw):
    """Return (index, step_text, tied_names, top_score)."""
    n_tied = draw(st.integers(min_value=2, max_value=4))
    n_extra = draw(st.integers(min_value=0, max_value=3))
    names = draw(st.lists(_names, min_size=n_tied + n_extra,
                          max_size=n_tied + n_extra, unique=True))
    tied_names, extra_names = names[:n_tied], names[n_tied:]
    mode = draw(st.sampled_from(('display', 'controls')))
    pages = []

    if mode == 'display':
        # Tied pages share a display phrase present in the text -> score 100.
        words = draw(_phrase_words)
        display = ' '.join(words)
        text = f'Open the {display} page'
        for name in tied_names:
            pages.append(_page(name, display, draw(st.sampled_from(KINDS))))
        # Extra pages: no display match, only a control labelled with the
        # phrase -> 10*len + 1 (<= 31), strictly below 100.
        for name in extra_names:
            pages.append(_page(name, draw(_other_display), draw(st.sampled_from(KINDS)),
                               [_ctrl(display)]))
        top = 100
    else:
        # Tied pages share the same set of control labels, all in the text,
        # and have non-matching displays.
        # Labels have >= 2 words so the tie score (>= 21) clears MIN_SCORE.
        multi = st.lists(st.sampled_from(TEXT_WORDS), min_size=2, max_size=3, unique=True)
        labels = draw(st.lists(multi.map(' '.join), min_size=1, max_size=3,
                               unique_by=normalize))
        text = 'Click ' + ' and '.join(labels)
        for name in tied_names:
            ctype = draw(st.sampled_from(('button', 'tab', 'link')))
            pages.append(_page(name, draw(_other_display), draw(st.sampled_from(KINDS)),
                               [_ctrl(lbl, ctype) for lbl in labels]))
        longest = max(len(normalize(lbl).split()) for lbl in labels)
        top = 10 * longest + len(labels)
        # Extra pages: a strict subset of labels (the longest one only) when
        # that scores lower, else no matching controls at all.
        for name in extra_names:
            if len(labels) >= 2:
                best = max(labels, key=lambda lbl: len(normalize(lbl).split()))
                ctrls = [_ctrl(best)]
            else:
                ctrls = [_ctrl(draw(_other_display))]
            pages.append(_page(name, draw(_other_display), draw(st.sampled_from(KINDS)), ctrls))

    index = CrawlIndex(tuple(pages))
    return index, text, sorted(tied_names), top


@settings(max_examples=150, deadline=None)
@given(scenario=tie_scenarios(), data=st.data())
def test_tie_without_context_is_ambiguous(scenario, data):
    """Feature: tsg-nbop-crawl-grounded-ui-steps, Property 4: Ties are ambiguous"""
    index, text, tied, top = scenario
    assert top >= MIN_SCORE
    ctx = data.draw(st.one_of(st.none(), _names.filter(lambda n: n not in tied)),
                    label='page_context')
    result = CrawlMatcher(index).match(text, page_context=ctx)
    assert result.status == 'unmatched'
    assert result.reason == 'ambiguous'
    assert result.candidates == tuple(tied)
    assert result.score == top
    assert result.page is None and result.control is None


@settings(max_examples=150, deadline=None)
@given(scenario=tie_scenarios(), data=st.data())
def test_tie_with_context_in_tied_set_grounds_to_it(scenario, data):
    """Feature: tsg-nbop-crawl-grounded-ui-steps, Property 4: Ties are ambiguous"""
    index, text, tied, top = scenario
    assert top >= MIN_SCORE
    ctx = data.draw(st.sampled_from(tied), label='page_context')
    result = CrawlMatcher(index).match(text, page_context=ctx)
    assert result.status == 'grounded'
    assert result.reason == ''
    assert result.page is not None and result.page.name == ctx
    assert result.candidates == ()
    assert result.score == top
