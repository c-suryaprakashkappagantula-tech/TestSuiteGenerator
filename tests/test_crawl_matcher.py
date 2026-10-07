"""Example tests for CrawlMatcher page scoring and selection
(design §5; Req 2.1, 2.3, 2.5, 2.6)."""
import pytest

from modules import crawl_grounding as cg


def _ctrl(control_type, label):
    return cg.CrawlControl(control_type, label, cg.normalize(label))


@pytest.fixture
def index():
    """Small fixed index; two pages share the display 'Validate Device/SIM'
    (a tile and a nested sub-screen), as on the real merged map."""
    return cg.CrawlIndex((
        cg.CrawlPage('Line Summary', 'Line Summary', 'page',
                     'https://nbop.example.com/line', (
                         _ctrl('button', 'Change Rate Plan'),
                         _ctrl('tab', 'Usage'),
                     )),
        cg.CrawlPage('Tile: Validate Device/SIM', 'Validate Device/SIM', 'tile', ''),
        cg.CrawlPage('Device > Validate Device/SIM', 'Validate Device/SIM', 'page', '', (
            _ctrl('input', 'IMEI Number'),
        )),
        cg.CrawlPage('Account Search', 'Account Search', 'page', '', (
            _ctrl('input', 'Search Text'),
            _ctrl('button', 'Search'),
        )),
    ))


@pytest.fixture
def matcher(index):
    return cg.CrawlMatcher(index)


def test_tokens_and_phrase_word_boundaries():
    assert cg.tokens('  Change  Rate-Plan ') == ('change', 'rate', 'plan')
    assert cg.phrase('Rate Plan', 'Click "Change Rate Plan" button')
    assert not cg.phrase('Rate Plan', 'Click Change Rate Plans')
    assert not cg.phrase('', 'anything')


def test_page_name_hit_grounds(matcher):
    r = matcher.match('Navigate to the Line Summary page')
    assert (r.status, r.reason) == ('grounded', '')
    assert r.page.name == 'Line Summary'
    assert r.control is None
    assert r.score == 100


def test_two_token_label_hit_grounds(matcher):
    r = matcher.match('Click the change rate plan button')
    assert r.status == 'grounded'
    assert r.page.name == 'Line Summary'
    assert r.score == 10 * 3 + 1


def test_single_token_label_below_min_score(matcher):
    # 'Usage' alone scores 10 + 1 = 11 < MIN_SCORE
    r = matcher.match('Verify usage')
    assert (r.status, r.reason) == ('unmatched', 'no_match')
    assert r.page is None and r.candidates == ()


def test_wanted_types_filters_controls(matcher):
    assert matcher.match('Click change rate plan', wanted_types=('button',)).status == 'grounded'
    r = matcher.match('Click change rate plan', wanted_types=('input',))
    assert r.reason == 'no_match'


def test_shared_display_tie_is_ambiguous_with_sorted_candidates(matcher):
    r = matcher.match('Open Validate Device/SIM')
    assert (r.status, r.reason) == ('unmatched', 'ambiguous')
    assert r.page is None
    assert r.candidates == ('Device > Validate Device/SIM', 'Tile: Validate Device/SIM')
    assert r.score == 100


def test_tie_broken_by_page_context(matcher):
    r = matcher.match('Open Validate Device/SIM',
                      page_context='Tile: Validate Device/SIM')
    assert r.status == 'grounded'
    assert r.page.name == 'Tile: Validate Device/SIM'


def test_page_context_outside_tie_stays_ambiguous(matcher):
    r = matcher.match('Open Validate Device/SIM', page_context='Line Summary')
    assert r.reason == 'ambiguous'


def test_control_hit_breaks_shared_display_tie(matcher):
    r = matcher.match('On Validate Device/SIM enter the IMEI Number')
    assert r.status == 'grounded'
    assert r.page.name == 'Device > Validate Device/SIM'
    assert r.score == 100 + 20 + 1


def test_no_match(matcher):
    r = matcher.match('Wait for the batch job to finish')
    assert (r.status, r.reason, r.page, r.control, r.candidates) == (
        'unmatched', 'no_match', None, None, ())


def test_empty_index_is_crawl_unavailable():
    r = cg.CrawlMatcher(cg.CrawlIndex(())).match('Open Line Summary')
    assert (r.status, r.reason) == ('unmatched', 'crawl_unavailable')


def test_repeated_calls_identical(matcher):
    texts = ['Open Validate Device/SIM', 'Click change rate plan', 'nothing here']
    assert [matcher.match(t) for t in texts] == [matcher.match(t) for t in texts]


# --- control selection (task 5.2; design §5; Req 2.1, 2.4, 2.6) -----------

@pytest.fixture
def ctrl_matcher():
    """Page with overlapping labels and same-label controls of different
    types, to exercise control selection."""
    return cg.CrawlMatcher(cg.CrawlIndex((
        cg.CrawlPage('Line Details', 'Line Details', 'page', '', (
            _ctrl('button', 'Change'),
            _ctrl('button', 'Change Rate Plan'),
            _ctrl('tab', 'Features'),
            _ctrl('link', 'Features'),
            _ctrl('column', 'Status'),
            _ctrl('field', 'Status'),
            _ctrl('button', 'Save Changes'),
            _ctrl('button', 'Cancel Order'),
        )),
        cg.CrawlPage('Other Page', 'Other Page', 'page', '', (
            _ctrl('button', 'Change Rate Plan'),
        )),
    )))


def test_two_token_label_hit_returns_control(matcher):
    r = matcher.match('Click the change rate plan button')
    assert r.control == _ctrl('button', 'Change Rate Plan')


def test_longest_label_wins(ctrl_matcher):
    r = ctrl_matcher.match('On Line Details click Change Rate Plan')
    assert r.page.name == 'Line Details'
    assert r.control.label == 'Change Rate Plan'


def test_wanted_types_order_breaks_equal_length_tie(ctrl_matcher):
    text = 'On Line Details open Features'
    assert ctrl_matcher.match(text, wanted_types=('tab', 'link')).control == _ctrl('tab', 'Features')
    assert ctrl_matcher.match(text, wanted_types=('link', 'tab')).control == _ctrl('link', 'Features')
    # filter alone (one wanted type) also resolves it
    assert ctrl_matcher.match(text, wanted_types=('link',)).control == _ctrl('link', 'Features')


def test_unresolved_tie_gives_none(ctrl_matcher):
    # different types, no wanted_types order -> None
    r = ctrl_matcher.match('On Line Details verify Features')
    assert r.status == 'grounded' and r.control is None
    # same type, different labels of equal length -> None
    r = ctrl_matcher.match('On Line Details click Save Changes then Cancel Order',
                           wanted_types=('button',))
    assert r.status == 'grounded' and r.control is None


def test_require_control_without_control_is_ambiguous(ctrl_matcher, matcher):
    r = ctrl_matcher.match('On Line Details verify Features', require_control=True)
    assert (r.status, r.reason) == ('unmatched', 'ambiguous')
    assert r.page is None and r.control is None
    # page-only hit with no control at all
    r = matcher.match('Navigate to the Line Summary page', require_control=True)
    assert (r.status, r.reason) == ('unmatched', 'ambiguous')
    # require_control is a no-op when a control was found
    r = ctrl_matcher.match('On Line Details click Change Rate Plan', require_control=True)
    assert r.status == 'grounded' and r.control.label == 'Change Rate Plan'


def test_control_belongs_to_chosen_page(ctrl_matcher, matcher):
    for m, t in ((ctrl_matcher, 'On Other Page click Change Rate Plan'),
                 (ctrl_matcher, 'On Line Details click Change Rate Plan'),
                 (matcher, 'On Validate Device/SIM enter the IMEI Number')):
        r = m.match(t)
        assert r.status == 'grounded'
        assert r.control in r.page.controls
    r = ctrl_matcher.match('On Other Page click Change Rate Plan')
    assert r.page.name == 'Other Page'


def test_control_selection_keeps_page_selection(ctrl_matcher):
    # page scoring unchanged: Line Details matches 'Change Rate Plan' and
    # 'Change' (30 + 2) and beats Other Page (30 + 1)
    r = ctrl_matcher.match('Click Change Rate Plan')
    assert (r.status, r.page.name, r.score) == ('grounded', 'Line Details', 32)
    assert r.control.label == 'Change Rate Plan'
    # with only the shared label counted -> page tie stays ambiguous
    r = ctrl_matcher.match('Click Change Rate Plan', wanted_types=('button',))
    assert r.score == 32
    r = cg.CrawlMatcher(cg.CrawlIndex((
        cg.CrawlPage('A', 'A', 'page', '', (_ctrl('button', 'Change Rate Plan'),)),
        cg.CrawlPage('B', 'B', 'page', '', (_ctrl('button', 'Change Rate Plan'),)),
    ))).match('Click Change Rate Plan')
    assert (r.status, r.reason, r.candidates) == ('unmatched', 'ambiguous', ('A', 'B'))


# --- smoke against the real crawl map (skipped when absent) ---------------

def _real_matcher():
    from modules.nbop_ui_knowledge import get_crawl_map_info
    info = get_crawl_map_info()
    if info.error or not info.data:
        pytest.skip(f'crawl map unavailable: {info.error}')
    return cg.CrawlMatcher(cg.CrawlIndex.from_map(info.data))


def test_real_map_matcher_smoke():
    m = _real_matcher()
    r = m.match('Open Change Features')
    assert r.status == 'grounded'
    assert r.page.name == 'Manage Line: Change Features'
    r = m.match('Click the Transaction History')
    assert r.status == 'grounded'
    assert r.page.display == 'Transaction History'
