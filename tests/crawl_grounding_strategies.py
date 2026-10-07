"""Hypothesis strategies for crawl-grounding property tests (helper module, no tests).

Provides:
    crawl_maps()          -> strategy of small synthetic NBOP crawl-map dicts
    permute_map(data, r)  -> deep copy with dict key order and list orders shuffled
    step_texts(index)     -> strategy of step texts mixing crawl labels, filler
                             prefixes, closer texts and noise words

Field shapes mirror the real ``nbop_ui_map.json`` (buttons{text,aria_label,title},
tabs{text}, links{text}, inputs{label,placeholder,name}, dropdowns{label,options},
checkboxes_radios{label}, headings{tag,text}, labels{text}, tables[{headers}]).
Maps are kept small (<=8 pages, <=6 controls per field) for speed.
"""
from __future__ import annotations

import copy
import random
from datetime import datetime
from typing import Any, List

from hypothesis import strategies as st

from modules.qmetry_pattern_library import (
    century_trace_step,
    db_table_validation_step,
    genesis_verify_step,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
_WORDS = (
    'Customer', 'Line', 'Account', 'Device', 'SIM', 'Feature', 'Plan', 'Search',
    'Details', 'Summary', 'History', 'Transaction', 'Change', 'Update', 'Status',
    'Billing', 'Port', 'Number', 'Activation', 'Suspend', 'Restore', 'Order',
    'Profile', 'Settings', 'Submit', 'Cancel', 'Save', 'Reset', 'eSIM', 'MSISDN',
)
_GROUPS = ('Line', 'Account', 'Device', 'Subscriber')
_NOISE = ('please', 'the', 'and then', 'ensure', 'successfully', 'on screen',
          'for the test line', 'as expected', 'now', 'again')
_FILLER_PREFIXES = ('Verify requirement: ', 'Perform the scenario-specific NBOP action: ')
_ACTION_VERBS = ('Click', 'Click on', 'Open', 'Navigate to', 'Select', 'Enter', 'Verify',
                 'Go to', 'Check', 'Type in')
_MAX_PAGES = 8
_MAX_CONTROLS = 6

# Occasionally emit labels the index must drop (short / MUI ids / empty).
_JUNK_LABELS = ('', 'OK', 'demo-simple-select-label', ':r1a:')


def _closers() -> List[str]:
    return [century_trace_step()[0], db_table_validation_step('')[0],
            genesis_verify_step()[0]]


# ---------------------------------------------------------------------------
# Crawl-map strategies
# ---------------------------------------------------------------------------
_phrase = st.lists(st.sampled_from(_WORDS), min_size=1, max_size=3, unique=True).map(' '.join)
_label = st.one_of(_phrase, _phrase, _phrase, st.sampled_from(_JUNK_LABELS))


def _ctrl_list(entry):
    return st.lists(entry, max_size=_MAX_CONTROLS)


_button = st.fixed_dictionaries({}, optional={
    'text': _label, 'aria_label': _label, 'title': _label})
_tab = st.fixed_dictionaries({'text': _label})
_link = st.fixed_dictionaries({'text': _label})
_input = st.fixed_dictionaries({}, optional={
    'label': _label, 'placeholder': _label, 'name': _label})
_dropdown = st.fixed_dictionaries({'label': _label}, optional={
    'options': st.lists(_phrase, max_size=4)})
_checkbox = st.fixed_dictionaries({'label': _label})
_heading = st.fixed_dictionaries({'tag': st.sampled_from(('h1', 'h2', 'h3', 'h4')),
                                  'text': _label})
_labelf = st.fixed_dictionaries({'text': _label})
_table = st.fixed_dictionaries({'headers': st.lists(_phrase, max_size=_MAX_CONTROLS)})

_url = st.builds(
    lambda path, tk: f'https://nbop.example.test/{path}' + (f'?ticket=ST-{tk}' if tk else ''),
    st.sampled_from(('home', 'line', 'account', 'device', 'search', 'history')),
    st.one_of(st.none(), st.integers(min_value=1, max_value=99999)))

_page = st.fixed_dictionaries({}, optional={
    'url': _url,
    'buttons': _ctrl_list(_button),
    'tabs': _ctrl_list(_tab),
    'links': _ctrl_list(_link),
    'inputs': _ctrl_list(_input),
    'dropdowns': _ctrl_list(_dropdown),
    'checkboxes_radios': _ctrl_list(_checkbox),
    'headings': _ctrl_list(_heading),
    'labels': _ctrl_list(_labelf),
    'tables': st.lists(_table, max_size=2),
})

_page_key = st.one_of(
    _phrase,
    _phrase.map(lambda x: f'Tile: {x}'),
    _phrase.map(lambda x: f'Context: {x}'),
    _phrase.map(lambda x: f'Button: {x}'),
    st.builds(lambda g, x: f'{g}: {x}', st.sampled_from(_GROUPS), _phrase),
    st.builds(lambda a, b: f'{a} > {b}', _phrase, _phrase),
)

_iso_ts = st.datetimes(
    min_value=datetime(2024, 1, 1),
    max_value=datetime(2026, 12, 31),
).map(lambda d: d.replace(microsecond=0).isoformat())


@st.composite
def crawl_maps(draw) -> dict:
    """Small random crawl map using the verified field shapes."""
    pages = draw(st.dictionaries(_page_key, _page, max_size=_MAX_PAGES))
    keys = list(pages)
    # Tiles / context items: sometimes twins of existing 'Tile: X'/'Context: X' keys.
    tile_twins = [k[len('Tile: '):] for k in keys if k.startswith('Tile: ')]
    ctx_twins = [k[len('Context: '):] for k in keys if k.startswith('Context: ')]
    grp_items = [k.split(': ', 1)[1] for k in keys
                 if k.startswith(_GROUPS) and ': ' in k and ' > ' not in k]
    plain = [k for k in keys if ': ' not in k and ' > ' not in k]

    def _pick(twins):
        return st.sampled_from(twins) if twins else _phrase

    data: dict = {'pages': pages}
    data['landing_tiles'] = draw(st.lists(st.one_of(_phrase, _pick(tile_twins)),
                                          max_size=4, unique=True))
    data['context_menu'] = [{'text': t} for t in draw(st.lists(
        st.one_of(_phrase, _pick(ctx_twins)), max_size=4, unique=True))]
    edit_plain = [{'text': t} for t in draw(st.lists(
        st.one_of(_phrase, _pick(grp_items), _pick(ctx_twins)), max_size=3, unique=True))]
    edit_full = []
    if keys:
        edit_full = draw(st.lists(st.builds(
            lambda t, g, p: {'text': t, 'group': g, 'page': p},
            _phrase, st.sampled_from(_GROUPS), st.sampled_from(keys)),
            max_size=2, unique_by=lambda e: e['text']))
    data['edit_menu'] = edit_plain + edit_full
    data['nav_menus'] = [{'text': t} for t in draw(st.lists(
        st.one_of(_phrase, _pick(plain)), max_size=3, unique=True))]
    if draw(st.booleans()):
        data['discovered_at'] = draw(_iso_ts)
    return data


# ---------------------------------------------------------------------------
# Permutation
# ---------------------------------------------------------------------------
def permute_map(data: Any, rnd: random.Random) -> Any:
    """Deep copy of ``data`` with every dict's key order and every list's order shuffled."""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            items = [(k, _walk(v)) for k, v in obj.items()]
            rnd.shuffle(items)
            return dict(items)
        if isinstance(obj, list):
            items = [_walk(v) for v in obj]
            rnd.shuffle(items)
            return items
        return copy.deepcopy(obj)
    return _walk(data)


# ---------------------------------------------------------------------------
# Step texts
# ---------------------------------------------------------------------------
def step_texts(index) -> st.SearchStrategy:
    """Step texts drawn from a ``CrawlIndex``: crawl labels / page displays with
    optional verbs, filler prefixes and noise words, plus closer texts and pure noise."""
    names: List[str] = []
    for page in getattr(index, 'pages', ()):
        names.append(page.display)
        names.extend(c.label for c in page.controls)
    names = sorted(set(n for n in names if n)) or list(_WORDS)

    crawl_term = st.sampled_from(names)
    noise = st.lists(st.sampled_from(_NOISE), max_size=2).map(' '.join)

    grounded = st.builds(
        lambda pre, verb, term, n1, term2: ' '.join(
            p for p in (pre.rstrip(), verb, term, n1, term2) if p),
        st.one_of(st.just(''), st.sampled_from(_FILLER_PREFIXES)),
        st.one_of(st.just(''), st.sampled_from(_ACTION_VERBS)),
        crawl_term,
        noise,
        st.one_of(st.just(''), crawl_term),
    )
    closer = st.sampled_from(_closers())
    pure_noise = st.lists(st.sampled_from(_NOISE + _WORDS), min_size=1, max_size=5).map(' '.join)
    filler_only = st.builds(lambda p, n: (p + n).strip(),
                            st.sampled_from(_FILLER_PREFIXES), pure_noise)
    return st.one_of(grounded, grounded, closer, pure_noise, filler_only)

# ---------------------------------------------------------------------------
# TestSuite strategy (task 7.3)
# ---------------------------------------------------------------------------
# UI-ish step texts drawn from a small fixed pool. None of these carry an API
# signal, so a hybrid suite built from them alone is a UI_Feature.
_UI_STEP_POOL = (
    'Navigate to the Subscriber tile and open the Line Summary tab',
    'Click on the Account Details link',
    'Open the Device Management page',
    'Select the Billing tab on the screen',
    'Verify the plan name is shown on the Line Summary page',
    'Enter the MSISDN in the search field and click Search',
    'Check that the Suspend button is visible',
    'Go to the Transaction History tab',
    'Update the profile settings and click Save',
    'Verify the status field is read-only on the page',
)

# API-signal step texts: each makes ``infer_api_signal`` True so a non-ui suite
# that includes one is NOT a UI_Feature.
_API_STEP_POOL = (
    'Trigger API POST /x with request payload',
    'Send POST /provisioning/v1/lines with the request payload',
    'Invoke the change SIM service and verify response code is 200',
    'GET /subscriber/123 and validate the response payload',
)

_CLOSER_FUNCS = (century_trace_step, db_table_validation_step, genesis_verify_step)


def _closer_text(which: int) -> str:
    fn = _CLOSER_FUNCS[which % len(_CLOSER_FUNCS)]
    return fn('')[0] if fn is db_table_validation_step else fn()[0]


@st.composite
def suites(draw, ui: bool):
    """A ``data_models_v8.TestSuite`` whose UI_Feature status equals ``ui``.

    For ``ui=True``:  classification is ``ui``, or ``hybrid`` with NO api-signal
    step. For ``ui=False``: classification is ``api``, or ``hybrid`` WITH at least
    one api-signal step. Each suite has 1-5 test cases, each with 1-6 steps drawn
    from the fixed UI pool, with closer steps dropped in at random positions, and
    synthetic ``MWTGPROV-<n>`` ids / unique TC summaries.
    """
    # Lazy import so the helper module stays usable without data models loaded.
    from modules.data_models_v8 import RoutingAudit, TestCase, TestStep, TestSuite

    fid_num = draw(st.integers(min_value=10000, max_value=99999))
    feature_id = f'MWTGPROV-{fid_num}'

    if ui:
        classification = draw(st.sampled_from(('ui', 'hybrid')))
    else:
        classification = draw(st.sampled_from(('api', 'hybrid')))
    # A hybrid suite only becomes non-UI when a non-closer step carries an API
    # signal, so force an API step into exactly these runs.
    force_api = (not ui and classification == 'hybrid')

    n_tcs = draw(st.integers(min_value=1, max_value=5))
    used_summaries: set = set()
    test_cases = []
    for idx in range(n_tcs):
        n_steps = draw(st.integers(min_value=1, max_value=6))
        texts = [draw(st.sampled_from(_UI_STEP_POOL)) for _ in range(n_steps)]
        # Sprinkle closers at random positions.
        for _ in range(draw(st.integers(min_value=0, max_value=2))):
            pos = draw(st.integers(min_value=0, max_value=len(texts)))
            texts.insert(pos, _closer_text(draw(st.integers(min_value=0, max_value=2))))
        steps = [TestStep(step_num=i + 1, summary=t) for i, t in enumerate(texts)]

        # Build a unique, descriptive TC summary like 'TC01_<fid>_<words>'.
        words = draw(st.lists(st.sampled_from(_WORDS), min_size=1, max_size=3,
                              unique=True).map('_'.join))
        summary = f'TC{idx + 1:02d}_{fid_num}_{words}'
        suffix = 1
        base = summary
        while summary in used_summaries:
            suffix += 1
            summary = f'{base}_{suffix}'
        used_summaries.add(summary)

        test_cases.append(TestCase(
            sno=f'TC_{idx + 1:03d}', summary=summary, steps=steps))

    if force_api:
        # Guarantee at least one api-signal step on a non-closer step.
        api_text = draw(st.sampled_from(_API_STEP_POOL))
        tc = test_cases[draw(st.integers(min_value=0, max_value=len(test_cases) - 1))]
        pos = draw(st.integers(min_value=0, max_value=len(tc.steps)))
        tc.steps.insert(pos, TestStep(step_num=0, summary=api_text))
        for i, s in enumerate(tc.steps):
            s.step_num = i + 1

    return TestSuite(
        feature_id=feature_id,
        test_cases=test_cases,
        routing_audit=RoutingAudit(classification=classification),
    )
