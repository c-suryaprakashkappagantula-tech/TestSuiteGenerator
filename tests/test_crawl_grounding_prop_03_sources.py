# -*- coding: utf-8 -*-
"""Property test: every crawl source is searchable (task 5.5).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 3: Every crawl source is searchable
**Validates: Requirements 2.2**

For any crawl map and for any entry that appears only in one of ``pages``,
``landing_tiles``, ``context_menu``, ``edit_menu`` or ``nav_menus``, a step
whose text contains that entry's name (and no other entry's name) grounds to
that entry.

Naming mirrors ``CrawlIndex.from_map``:
  pages            -> key verbatim
  landing_tiles    -> 'Tile: X'
  context_menu     -> 'Context: X'
  edit_menu + page -> the 'page' value, display = item text
  edit_menu plain  -> 'X' (no twin present)
  nav_menus        -> 'X'
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

from modules.crawl_grounding import CrawlIndex, CrawlMatcher  # noqa: E402

SOURCES = ('pages', 'landing_tiles', 'context_menu', 'edit_menu_text',
           'edit_menu_page', 'nav_menus')

# Words used in the step template; generated names must never contain them.
_FILLER = {'click', 'on', 'the', 'option', 'then', 'verify', 'and', 'tile',
           'context', 'button', 'screen'}

_word = st.text(alphabet='abcdefghijklmnopqrstuvwxyz', min_size=4, max_size=9) \
    .filter(lambda w: w not in _FILLER)


def _cased(words, style):
    text = ' '.join(words)
    return {'lower': text, 'title': text.title(), 'upper': text.upper()}[style]


@st.composite
def scenarios(draw):
    """A map with one target entry in a single source plus distractors in
    other sources. Every entry uses its own words, so no other entry's name
    can occur in the step text."""
    target_source = draw(st.sampled_from(SOURCES))
    n_distractors = draw(st.integers(min_value=0, max_value=6))
    distractor_sources = draw(st.lists(st.sampled_from(SOURCES),
                                       min_size=n_distractors, max_size=n_distractors))
    # name word counts: target + distractors, plus one extra word slot for
    # each 'page' target of edit_menu_page entries.
    sizes = [draw(st.integers(min_value=1, max_value=3))
             for _ in range(1 + n_distractors)]
    total = sum(sizes) + 2 * (1 + n_distractors)   # spare words for page/group
    words = draw(st.lists(_word, min_size=total, max_size=total, unique=True))
    styles = draw(st.lists(st.sampled_from(('lower', 'title', 'upper')),
                           min_size=1 + n_distractors, max_size=1 + n_distractors))
    pages_as_list = draw(st.booleans())

    it = iter(words)
    entries = []
    for src, size, style in zip([target_source] + distractor_sources, sizes, styles):
        name = _cased([next(it) for _ in range(size)], style)
        page_name = next(it).title() + ' Screen'
        group = next(it).title()
        entries.append((src, name, page_name, group))

    data = {'pages': [] if pages_as_list else {}, 'landing_tiles': [],
            'context_menu': [], 'edit_menu': [], 'nav_menus': []}
    for src, name, page_name, group in entries:
        if src == 'pages':
            if pages_as_list:
                data['pages'].append({'name': name, 'url': 'https://h/x?ticket=ST-1'})
            else:
                data['pages'][name] = {'url': 'https://h/x?ticket=ST-1'}
        elif src == 'landing_tiles':
            data['landing_tiles'].append({'text': name})
        elif src == 'context_menu':
            data['context_menu'].append({'text': name})
        elif src == 'edit_menu_text':
            data['edit_menu'].append({'text': name})
        elif src == 'edit_menu_page':
            data['edit_menu'].append({'text': name, 'group': group, 'page': page_name})
        else:
            data['nav_menus'].append({'text': name})
    return entries[0], data


def _expected(src, name, page_name):
    """(page.name, page.display, page.kind) per CrawlIndex naming."""
    return {
        'pages': (name, name, 'page'),
        'landing_tiles': ('Tile: ' + name, name, 'tile'),
        'context_menu': ('Context: ' + name, name, 'context_menu'),
        'edit_menu_text': (name, name, 'edit_menu'),
        'edit_menu_page': (page_name, name, 'edit_menu'),
        'nav_menus': (name, name, 'nav_menu'),
    }[src]


@settings(max_examples=150, deadline=None)
@given(scenarios())
def test_every_crawl_source_is_searchable(scenario):
    (src, name, page_name, _group), data = scenario
    text = f'Click on the {name} option then verify'
    result = CrawlMatcher(CrawlIndex.from_map(data)).match(text)

    exp_name, exp_display, exp_kind = _expected(src, name, page_name)
    assert result.status == 'grounded', (src, data, result)
    assert result.reason == ''
    assert result.page is not None
    assert result.page.name == exp_name
    assert result.page.display == exp_display
    assert result.page.kind == exp_kind
    assert result.score >= 100
