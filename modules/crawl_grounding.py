"""Crawl-grounded UI steps for NBOP features.

Grounds generated UI test steps against the read-only NBOP crawl map
(``nbop_discovery/nbop_ui_map.json``), which is accessed through
``nbop_ui_knowledge.get_crawl_map_info()``.

Layout (later tasks extend the marked sections):
    1. Data models          (StepGrounding, GroundingIssue, GroundingResult)
    2. Text normalization   (normalize)
    3. Crawl staleness      (stale_threshold_days, staleness)
    4. Crawl index          (CrawlControl, CrawlPage, CrawlIndex)      -- later task
    5. Matcher              (CrawlMatcher)                             -- later task
    6. Action classifier    (classify_ui_action)                       -- later task
    7. Step grounding pass  (ground_step, closers, UI-feature detector) -- later task

The crawl map is never written by this module (Req 7.4).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from modules.contract_bridge import UI_ACTION_IDS, infer_step_contract


# ===========================================================================
# 1. Data models
# ===========================================================================

@dataclass
class StepGrounding:
    """Grounding outcome for a single test step."""
    kind: str                 # 'ui_action' | 'legacy_typed' | 'unmatched'
    reason: str               # '' | no_match | ambiguous | crawl_unavailable | action_unsupported
    page: str = ''            # CrawlPage.name (verbatim)
    page_url: str = ''        # query-stripped
    page_kind: str = ''
    control_type: str = ''    # 'page' when no control
    control_label: str = ''
    action_id: str = ''
    value: str = ''
    expect: str = ''
    candidates: Tuple[str, ...] = ()
    source_locator: str = ''
    was_filler: bool = False

    @property
    def grounding_percent(self) -> int:
        """100 iff kind=='ui_action' and action_id is a registered UI id, else 0."""
        if self.kind == 'ui_action' and self.action_id in UI_ACTION_IDS:
            return 100
        return 0


@dataclass
class GroundingIssue:
    """One unmatched/ambiguous step, listed in the Grounding Report."""
    tc_number: str
    step_number: int
    step_uid: str
    step_text: str
    reason: str
    candidates: Tuple[str, ...]


@dataclass
class GroundingResult:
    """Per-feature grounding summary (attached as ``suite._grounding_result``)."""
    feature_id: str
    crawl_path: str
    crawl_error: str
    discovered_at: str
    age_days: Optional[int]
    stale_warning: str
    ui_steps: int
    grounded_steps: int
    unmatched_steps: int
    filler_steps: int
    closers_removed: int
    typed_rows: int = 0       # filled from contract writer
    total_rows: int = 0       # filled from contract writer
    issues: List[GroundingIssue] = field(default_factory=list)


# ===========================================================================
# 2. Text normalization (design §4)
# ===========================================================================

# Straight and typographic quote characters removed outright.
_QUOTES_RE = re.compile(r"[\"'`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00ab\u00bb]")
# Punctuation (anything not a word char, whitespace or '/') becomes a space.
_PUNCT_RE = re.compile(r"[^\w\s/]")
_WS_RE = re.compile(r"\s+")


def normalize(s: Any) -> str:
    """Lowercase, '&'->'and', strip quotes and punctuation except '/',
    collapse whitespace. ``None`` yields ''."""
    if s is None:
        return ''
    text = str(s).lower()
    text = text.replace('&', ' and ')
    text = _QUOTES_RE.sub('', text)
    text = _PUNCT_RE.sub(' ', text)
    text = text.replace('_', ' ')
    return _WS_RE.sub(' ', text).strip()


# ===========================================================================
# 3. Crawl staleness (design §11)
# ===========================================================================

STALE_DAYS_ENV = 'TSG_CRAWL_STALE_DAYS'
DEFAULT_STALE_DAYS = 90


def stale_threshold_days() -> Tuple[int, str]:
    """Read the staleness threshold from ``TSG_CRAWL_STALE_DAYS``.

    Returns ``(days, note)``. Missing or invalid (non-integer, negative)
    values fall back to 90; ``note`` explains a fallback for invalid
    values and is '' otherwise.
    """
    raw = os.environ.get(STALE_DAYS_ENV)
    if raw is None or raw.strip() == '':
        return DEFAULT_STALE_DAYS, ''
    try:
        days = int(raw.strip())
    except ValueError:
        days = -1
    if days < 0:
        return DEFAULT_STALE_DAYS, (
            f'invalid {STALE_DAYS_ENV}={raw!r}; using default {DEFAULT_STALE_DAYS}')
    return days, ''


def _align_tz(a: datetime, b: datetime) -> Tuple[datetime, datetime]:
    """Make two datetimes comparable (naive values are treated as UTC)."""
    if (a.tzinfo is None) == (b.tzinfo is None):
        return a, b
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a, b


def crawl_age_days(discovered_at: Optional[datetime], now: datetime) -> Optional[int]:
    """Whole days between ``discovered_at`` and ``now`` (None when unknown)."""
    if discovered_at is None:
        return None
    d, n = _align_tz(discovered_at, now)
    return (n - d).days


def staleness(info: Any, now: datetime, threshold_days: int) -> Optional[str]:
    """Return the stale-crawl warning, or None.

    Warns exactly when ``(now - discovered_at).days > threshold_days``.
    ``info`` is a ``CrawlMapInfo``; None ``discovered_at`` yields None.
    """
    discovered_at = getattr(info, 'discovered_at', None)
    age = crawl_age_days(discovered_at, now)
    if age is None or age <= threshold_days:
        return None
    return (f'NBOP crawl map is stale: discovered_at={discovered_at:%Y-%m-%d}, '
            f'age={age} days (threshold {threshold_days})')


# ===========================================================================
# 4. Crawl index (design §4)
# ===========================================================================

@dataclass(frozen=True)
class CrawlControl:
    """One labelled control on a crawled page."""
    control_type: str   # button|link|tab|input|dropdown|checkbox|heading|column|field
    label: str          # verbatim from crawl
    norm: str           # normalized label used for matching
    attrs: Tuple[Tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class CrawlPage:
    """One crawl entry (page, tile, context/edit menu item, nav menu)."""
    name: str           # verbatim key / text
    display: str        # name without 'Tile: '/'Context: '/'Button: ' prefix
    kind: str           # page|tile|context_menu|edit_menu|nav_menu
    url: str            # scheme://host/path, query stripped; '' when absent
    controls: Tuple[CrawlControl, ...] = ()


def _strip_query(url: Any) -> str:
    """Return ``scheme://host/path`` with query and fragment removed.

    Non-string / empty input yields ''.
    """
    if not isinstance(url, str) or not url.strip():
        return ''
    text = url.strip()
    for sep in ('#', '?'):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    return text


_MUI_ID_RE = re.compile(r'^(demo-simple-select\b.*|:r[0-9a-z]+:)$', re.IGNORECASE)
_MIN_LABEL_LEN = 3

# crawl field -> (control_type, label keys in priority order)
_CONTROL_FIELDS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ('tabs', 'tab', ('text',)),
    ('buttons', 'button', ('text', 'aria_label', 'title')),
    ('links', 'link', ('text',)),
    ('inputs', 'input', ('label', 'placeholder', 'name')),
    ('dropdowns', 'dropdown', ('label',)),
    ('checkboxes_radios', 'checkbox', ('label', 'text')),
    ('headings', 'heading', ('text',)),
    ('labels', 'field', ('text', 'label')),
    ('field_value_map', 'field', ('label', 'text')),
)
# Boolean element state carried into CrawlControl.attrs when present.
_BOOL_ATTRS = ('disabled', 'readonly')


def _first_label(entry: Any, keys: Tuple[str, ...]) -> str:
    """First non-empty string among ``keys`` (a bare string is its own label)."""
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ''
    for key in keys:
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ''


def _keep_label(label: str) -> bool:
    return len(label) >= _MIN_LABEL_LEN and not _MUI_ID_RE.match(label)


def _option_text(opt: Any) -> str:
    if isinstance(opt, dict):
        return _first_label(opt, ('text', 'label', 'value'))
    return str(opt).strip() if opt is not None else ''


def _entry_attrs(entry: Any, control_type: str) -> Tuple[Tuple[str, Any], ...]:
    if not isinstance(entry, dict):
        return ()
    attrs: List[Tuple[str, Any]] = []
    for key in _BOOL_ATTRS:
        if isinstance(entry.get(key), bool):
            attrs.append((key, entry[key]))
    if control_type == 'dropdown':
        opts = entry.get('options')
        if isinstance(opts, (list, tuple)):
            # Sorted: option order is not meaningful for grounding, and the
            # index must not depend on source list order.
            texts = tuple(sorted(t for t in (_option_text(o) for o in opts) if t))
            attrs.append(('options', texts))
    return tuple(sorted(attrs, key=lambda kv: kv[0]))


def _extract_controls(page: Any) -> Tuple[CrawlControl, ...]:
    """Extract labelled controls from one crawl page dict.

    Drops empty/short (<3 chars) labels and MUI internal ids, collapses
    duplicate ``(control_type, norm)`` pairs to the first in sorted order,
    and returns controls sorted by ``(control_type, label)``. Missing or
    malformed fields are skipped.
    """
    if not isinstance(page, dict):
        return ()
    raw: List[CrawlControl] = []

    def _add(control_type: str, label: str, attrs: Tuple[Tuple[str, Any], ...]) -> None:
        if not _keep_label(label):
            return
        norm = normalize(label)
        if not norm:
            return
        raw.append(CrawlControl(control_type, label, norm, attrs))

    for field_name, control_type, keys in _CONTROL_FIELDS:
        entries = page.get(field_name)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            _add(control_type, _first_label(entry, keys), _entry_attrs(entry, control_type))

    tables = page.get('tables')
    if isinstance(tables, (list, tuple)):
        for table in tables:
            headers = table.get('headers') if isinstance(table, dict) else None
            if not isinstance(headers, (list, tuple)):
                continue
            for header in headers:
                _add('column', _first_label(header, ('text', 'label')), ())

    raw.sort(key=lambda c: (c.control_type, c.label, repr(c.attrs)))
    seen = set()
    out: List[CrawlControl] = []
    for ctrl in raw:
        key = (ctrl.control_type, ctrl.norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(ctrl)
    return tuple(out)


_DISPLAY_PREFIXES = ('Tile: ', 'Context: ', 'Button: ')
_NEST_SEP_RE = re.compile(r'\s+[>\u203a]\s+')
_TWIN_PREFIX = {'tile': 'Tile: ', 'context_menu': 'Context: '}


def _display_name(name: str) -> str:
    """Strip a 'Tile: '/'Context: '/'Button: ' prefix, then keep the last
    ' > ' (or ' › ') segment of a nested sub-screen key."""
    text = name.strip()
    for prefix in _DISPLAY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    parts = [p for p in _NEST_SEP_RE.split(text) if p.strip()]
    return parts[-1].strip() if parts else text


def _entry_text(entry: Any) -> str:
    return _first_label(entry, ('text', 'name', 'label'))


def _entry_str(entry: Any, key: str) -> str:
    if isinstance(entry, dict) and isinstance(entry.get(key), str):
        return entry[key].strip()
    return ''


class CrawlIndex:
    """Deterministic, read-only index of crawl pages (design §4).

    Pages come from ``pages`` plus ``landing_tiles``, ``context_menu``,
    ``edit_menu`` and ``nav_menus``. A menu/tile entry merges into its
    ``pages`` twin when one exists, else it becomes a control-less page.
    Result is independent of dict key order and list order.
    """

    def __init__(self, pages: Tuple[CrawlPage, ...]):
        self.pages: Tuple[CrawlPage, ...] = tuple(sorted(pages, key=lambda p: p.name))
        self._by_name = {p.name: p for p in self.pages}

    def get(self, name: str) -> Optional[CrawlPage]:
        return self._by_name.get(name)

    def __len__(self) -> int:
        return len(self.pages)

    @classmethod
    def from_map(cls, data: Any) -> 'CrawlIndex':
        if not isinstance(data, dict):
            return cls(())

        # name -> mutable record {display, kind, url, controls, locked}
        recs: dict = {}

        raw_pages = data.get('pages')
        items: List[Tuple[str, Any]] = []
        if isinstance(raw_pages, dict):
            items = [(str(k), v) for k, v in raw_pages.items()]
        elif isinstance(raw_pages, (list, tuple)):
            items = [(_entry_str(v, 'name'), v) for v in raw_pages]
        for name, page in sorted(items, key=lambda kv: kv[0]):
            name = name.strip()
            if not name or name in recs:
                continue
            page = page if isinstance(page, dict) else {}
            recs[name] = {
                'display': _display_name(name), 'kind': 'page',
                'url': _strip_query(page.get('url')),
                'controls': _extract_controls(page), 'locked': False,
            }

        def _merge(name: str, kind: str, display: str, force: bool) -> None:
            """Merge into an existing record (first non-page kind wins unless
            ``force``), or create a control-less page."""
            rec = recs.get(name)
            if rec is None:
                recs[name] = {'display': display, 'kind': kind, 'url': '',
                              'controls': (), 'locked': True}
                return
            if force or not rec['locked']:
                rec['kind'] = kind
                rec['locked'] = True
                if force:
                    rec['display'] = display

        def _entries(key: str) -> List[Any]:
            val = data.get(key)
            if not isinstance(val, (list, tuple)):
                return []
            return sorted(val, key=lambda e: (_entry_text(e), _entry_str(e, 'group'),
                                              _entry_str(e, 'page')))

        # Landing tiles and context menu: twin 'Tile: X' / 'Context: X'.
        for key, kind in (('landing_tiles', 'tile'), ('context_menu', 'context_menu')):
            for entry in _entries(key):
                text = _entry_text(entry)
                if text:
                    _merge(_TWIN_PREFIX[kind] + text, kind, text, False)

        # Edit menu: entries with 'page' always own that page (display = item).
        edit = _entries('edit_menu')
        covered = set()
        for entry in edit:
            text, target = _entry_text(entry), _entry_str(entry, 'page')
            if text and target:
                covered.add(text)
                _merge(target, 'edit_menu', text, True)
        group_items: dict = {}
        for name in recs:
            if ': ' in name and not name.startswith(_DISPLAY_PREFIXES) \
                    and not _NEST_SEP_RE.search(name):
                group_items.setdefault(name.split(': ', 1)[1].strip(), []).append(name)
        for entry in edit:
            text = _entry_text(entry)
            if not text or _entry_str(entry, 'page') or text in covered:
                continue
            twin = next((n for n in ('Context: ' + text, text) if n in recs), '')
            if twin:
                _merge(twin, 'edit_menu', text, False)
            elif len(group_items.get(text, [])) == 1:
                # unique '<Group>: <Item>' screen -> owns it, display = item
                _merge(group_items[text][0], 'edit_menu', text, True)
            else:
                _merge(text, 'edit_menu', text, False)

        # Nav menus: twin is the plain name.
        for entry in _entries('nav_menus'):
            text = _entry_text(entry)
            if text:
                _merge(text, 'nav_menu', text, False)

        return cls(tuple(
            CrawlPage(name, r['display'], r['kind'], r['url'], r['controls'])
            for name, r in recs.items()))


# ===========================================================================
# 5. Matcher (design §5)
# ===========================================================================

MIN_SCORE = 20


@dataclass(frozen=True)
class MatchResult:
    """Outcome of matching one step text against the crawl index."""
    status: str                     # grounded | unmatched
    reason: str                     # '' | no_match | ambiguous | crawl_unavailable
    page: Optional[CrawlPage]
    control: Optional[CrawlControl]
    candidates: Tuple[str, ...]     # tied page names when ambiguous
    score: int


def tokens(s: Any) -> Tuple[str, ...]:
    """Whitespace tokens of ``normalize(s)``."""
    return tuple(normalize(s).split())


def _phrase_norm(norm_p: str, padded_text: str) -> bool:
    """``norm_p`` occurs in the space-padded normalized text on word boundaries."""
    return bool(norm_p) and f' {norm_p} ' in padded_text


def phrase(p: Any, text: Any) -> bool:
    """True when ``normalize(p)`` occurs in ``normalize(text)`` on word
    boundaries (normalized text contains only word chars, '/' and single
    spaces, so space padding gives exact word-boundary semantics)."""
    return _phrase_norm(normalize(p), f' {normalize(text)} ')


class CrawlMatcher:
    """Deterministic integer scoring of crawl pages for a step text.

    Page score = 100*phrase(display) + 10*len(tokens(longest matched label))
    + count(distinct matched labels); only controls whose type is in
    ``wanted_types`` count when it is non-empty. Pages scoring at least
    ``MIN_SCORE`` are candidates.
    """

    def __init__(self, index: Optional[CrawlIndex]):
        self.index = index
        pages = index.pages if index is not None else ()
        # (page, normalized display, controls) precomputed in sorted page order
        self._pages = tuple((p, normalize(p.display), p.controls) for p in pages)

    def _score(self, display_norm: str, controls: Tuple[CrawlControl, ...],
               padded: str, wanted_types: Tuple[str, ...]) -> int:
        score = 100 if _phrase_norm(display_norm, padded) else 0
        matched = {c.norm for c in controls
                   if (not wanted_types or c.control_type in wanted_types)
                   and _phrase_norm(c.norm, padded)}
        if matched:
            score += 10 * max(len(n.split()) for n in matched) + len(matched)
        return score

    @staticmethod
    def _select_control(page: CrawlPage, padded: str,
                        wanted_types: Tuple[str, ...]) -> Optional[CrawlControl]:
        """Longest matched label on ``page`` (by token count, filtered by
        ``wanted_types``). Equal-length labels of different types are decided
        by ``wanted_types`` order; any remaining tie yields ``None``.
        Controls with identical (type, normalized label) count as one."""
        matched = [c for c in page.controls
                   if (not wanted_types or c.control_type in wanted_types)
                   and _phrase_norm(c.norm, padded)]
        if not matched:
            return None
        longest = max(len(c.norm.split()) for c in matched)
        best: dict = {}
        for c in matched:
            if len(c.norm.split()) == longest:
                best.setdefault((c.control_type, c.norm), c)   # first in page order
        if len(best) == 1:
            return next(iter(best.values()))
        if wanted_types:
            rank = {t: i for i, t in enumerate(wanted_types)}
            top = min(rank[t] for t, _ in best)
            best = {k: c for k, c in best.items() if rank[k[0]] == top}
            if len(best) == 1:
                return next(iter(best.values()))
        return None

    def match(self, text: str, page_context: Optional[str] = None,
              wanted_types: Tuple[str, ...] = (),
              require_control: bool = False) -> MatchResult:
        if not self._pages:
            return MatchResult('unmatched', 'crawl_unavailable', None, None, (), 0)
        wanted = tuple(wanted_types or ())
        padded = f' {normalize(text)} '
        scored = [(self._score(dn, ctrls, padded, wanted), page)
                  for page, dn, ctrls in self._pages]
        cands = [(s, p) for s, p in scored if s >= MIN_SCORE]
        if not cands:
            return MatchResult('unmatched', 'no_match', None, None, (), 0)
        top = max(s for s, _ in cands)
        tied = sorted(p.name for s, p in cands if s == top)
        if len(tied) == 1:
            chosen = tied[0]
        elif page_context is not None and page_context in tied:
            chosen = page_context
        else:
            return MatchResult('unmatched', 'ambiguous', None, None, tuple(tied), top)
        page = self.index.get(chosen)
        control = self._select_control(page, padded, wanted)
        if require_control and control is None:
            return MatchResult('unmatched', 'ambiguous', None, None, (chosen,), top)
        return MatchResult('grounded', '', page, control, (), top)


# ===========================================================================
# 6. Action classifier (design §6)
# ===========================================================================

A_FIELD_READONLY = 'nbop.ui.field_readonly'
A_FIELD_EDITABLE = 'nbop.ui.field_editable'
A_BUTTON_STATE = 'nbop.ui.button_state'
A_SELECT_DROPDOWN = 'nbop.ui.select_dropdown'
A_ENTER_TEXT = 'nbop.ui.enter_text'          # candidate only (design §8)
A_OPEN_POPUP = 'nbop.ui.open_popup'
A_OPEN_TAB = 'nbop.ui.open_tab'
A_CLICK_BUTTON = 'nbop.ui.click_button'
A_OPEN_MENU = 'nbop.ui.open_menu'
A_OPEN_PAGE = 'nbop.ui.open_page'
A_ELEMENT_PRESENT = 'nbop.ui.element_present'

_MENU_KINDS = ('context_menu', 'edit_menu')
_PAGE_ONLY_ACTIONS = frozenset({A_OPEN_PAGE, A_OPEN_MENU})
# Actions a matched tab control may turn into open_tab (rows below the tab row
# that describe activating something, not asserting it).
_TAB_REFINABLE = frozenset({A_CLICK_BUTTON, A_OPEN_MENU, A_OPEN_PAGE})

_F = re.IGNORECASE
_RE_READONLY = re.compile(
    r'read[\s-]?only|non[\s-]?editable|not\s+editable|disabled\s+field|greyed|grayed', _F)
_RE_EDITABLE = re.compile(r'\beditable\b|\bcan\s+edit\b|\benabled\s+field\b', _F)
_RE_BUTTON_STATE = re.compile(r'\bbutton\b.*?\b(enabled|disabled)\b', _F)
_RE_SELECT = re.compile(r'\b(?:select|choose)\b.*\b(?:from|in)\b.*dropdown', _F)
_RE_EQUALS = re.compile(r'=\s*\S')
# enter/type/input only as an imperative verb (clause start), then '<value> in|into'
_RE_ENTER = re.compile(
    r'(?:^|[,;.:]\s*|\b(?:and|then)\s+)(?:enter|type|input)\s+(.+?)\s+(?:in|into)\b', _F)
_RE_POPUP = re.compile(r'\bpop[\s-]?up\b|\bmodal\b|\bdialog\b|\bwindow\s+opens\b', _F)
_RE_CLICK_TAB = re.compile(r'\b(?:click|press|tap)\b.*\btab\b', _F)
_RE_CLICK = re.compile(r'\b(?:click|press|tap)\b', _F)
_RE_MENU = re.compile(r'\bmenu\b|\u2261|\bhamburger\b', _F)
_RE_NAVIGATE = re.compile(r'\b(?:navigate|open(?:s|ed)?|go\s+to|launch|tile)\b', _F)
_RE_VERIFY = re.compile(
    r'\b(?:verify|validate|check|displayed|present|removed|absent)\b|\bnot\s+displayed\b', _F)
_RE_ABSENT = re.compile(
    r'\bnot\s+(?:be\s+)?(?:displayed|present|shown|visible)\b|\bremoved\b|\babsent\b'
    r'|\bhidden\b', _F)

# Quoted spans: '...' (opening quote at a word boundary, so apostrophes like
# "user's" are not taken as quotes), "...", and typographic quotes.
_RE_QUOTED = re.compile(
    r"(?:(?<![\w])'([^']+)'(?![\w])|\"([^\"]+)\"|\u2018([^\u2019]+)\u2019|\u201c([^\u201d]+)\u201d)")
_RE_EQ_VALUE = re.compile(r'=\s*(.+)$')
_RE_EQ_CUT = re.compile(r'\s+(?:in|from|on|and|then)\s+.*$|[,;](?:\s.*)?$', _F)
_QUOTE_CHARS = '\'"\u2018\u2019\u201c\u201d'


def _quoted(text: str) -> str:
    """First non-empty quoted span in ``text`` ('' when none)."""
    for m in _RE_QUOTED.finditer(text):
        val = next((g for g in m.groups() if g is not None), '').strip()
        if val:
            return val
    return ''


def _clean_value(raw: str) -> str:
    return raw.strip().strip(_QUOTE_CHARS).strip().rstrip('.').strip()


def _equals_value(text: str) -> str:
    m = _RE_EQ_VALUE.search(text)
    if not m:
        return ''
    return _clean_value(_RE_EQ_CUT.sub('', m.group(1).strip()))


def infer_ui_family(text: Any) -> Tuple[str, Tuple[str, ...], dict]:
    """Classify a step text with the ordered design §6 table (first hit wins).

    Returns ``(action_id, wanted_types, extra)`` where ``extra`` holds
    ``value`` (select/enter rows) or ``expect`` (state/presence rows).
    """
    t = text if isinstance(text, str) else ('' if text is None else str(text))
    if _RE_READONLY.search(t):
        return A_FIELD_READONLY, ('input', 'dropdown', 'field'), {'expect': 'readonly'}
    if _RE_EDITABLE.search(t):
        return A_FIELD_EDITABLE, ('input', 'dropdown', 'field'), {'expect': 'editable'}
    m = _RE_BUTTON_STATE.search(t)
    if m:
        return A_BUTTON_STATE, ('button',), {'expect': m.group(1).lower()}
    if _RE_SELECT.search(t) or _RE_EQUALS.search(t):
        value = _quoted(t) or _equals_value(t)
        return A_SELECT_DROPDOWN, ('dropdown',), ({'value': value} if value else {})
    m = _RE_ENTER.search(t)
    if m:
        value = _quoted(m.group(1)) or _clean_value(m.group(1))
        return A_ENTER_TEXT, ('input',), ({'value': value} if value else {})
    if _RE_POPUP.search(t):
        return A_OPEN_POPUP, ('button', 'link'), {}
    if _RE_CLICK_TAB.search(t):
        return A_OPEN_TAB, ('tab',), {}
    if _RE_CLICK.search(t):
        return A_CLICK_BUTTON, ('button', 'link'), {}
    if _RE_MENU.search(t):
        return A_OPEN_MENU, (), {}
    if _RE_NAVIGATE.search(t):
        return A_OPEN_PAGE, (), {}
    if _RE_VERIFY.search(t):
        expect = 'absent' if _RE_ABSENT.search(t) else 'present'
        return (A_ELEMENT_PRESENT,
                ('column', 'field', 'heading', 'tab', 'button', 'link'),
                {'expect': expect})
    return A_OPEN_PAGE, (), {}


def needs_control(action_id: str) -> bool:
    """True unless the action targets a page/menu only."""
    return action_id not in _PAGE_ONLY_ACTIONS


def classify_ui_action(text: Any, match: Optional[MatchResult]) -> Tuple[str, dict]:
    """Refine :func:`infer_ui_family` with the matcher result.

    - matched control of type ``tab`` turns click/open-style actions into
      ``open_tab`` (verification and state rows keep their assertion);
    - a page-only match on a ``context_menu``/``edit_menu`` page is
      ``open_menu``; any other page-only match falls back to ``open_page``.
    """
    action, _wanted, extra = infer_ui_family(text)
    page = getattr(match, 'page', None) if match is not None else None
    if match is None or getattr(match, 'status', '') != 'grounded' or page is None:
        return action, dict(extra)
    control = match.control
    if control is None:
        if page.kind in _MENU_KINDS:
            return A_OPEN_MENU, {}
        if needs_control(action):
            return A_OPEN_PAGE, {}
        return action, dict(extra)
    if control.control_type == 'tab' and action in _TAB_REFINABLE:
        return A_OPEN_TAB, {}
    return action, dict(extra)


# ===========================================================================
# 7. Step grounding pass             -- implemented by a later task
# ===========================================================================

# --- 7a. UI-feature detection and closer recognition (design §2, §3) -------

# API signal (design §2). POST/GET are matched case-sensitively so ordinary
# words ("postpaid", "get the line") never count; the rest are case-insensitive.
_API_SIGNAL_CI = re.compile(
    r'\btrigger\s+(?:the\s+)?API\b'
    r'|\binvok(?:e|es|ed|ing)\b'
    r'|\brequest\s+payload\b'
    r'|\bNSL\b.*\bAPI\b'
    r'|\bresponse\s+code\b',
    re.IGNORECASE,
)
_API_SIGNAL_CS = re.compile(r'\bPOST\b|\bGET\s+/')


def infer_api_signal(text: str) -> bool:
    """True when ``text`` reads like an API step (design §2)."""
    t = text or ''
    return bool(_API_SIGNAL_CI.search(t) or _API_SIGNAL_CS.search(t))


_CLOSER_CACHE: Optional[Tuple[Tuple[str, ...], str]] = None


def _closer_texts() -> Tuple[Tuple[str, ...], str]:
    """(exact closer summaries, db-table closer prefix) from qmetry_pattern_library.

    The db-table closer appends a table block after ``':\\n'``; its fixed part is
    the text up to and including the ``':'``.
    """
    global _CLOSER_CACHE
    if _CLOSER_CACHE is None:
        from modules import qmetry_pattern_library as qpl
        exact = (qpl.century_trace_step()[0].strip(),
                 qpl.genesis_verify_step()[0].strip())
        db_text = qpl.db_table_validation_step('')[0]
        cut = db_text.find(':\n')
        prefix = (db_text[:cut + 1] if cut >= 0 else db_text).strip()
        _CLOSER_CACHE = (exact, prefix)
    return _CLOSER_CACHE


def is_closer_step(summary: str) -> bool:
    """True when ``summary`` is a generated provisioning Closer_Step (design §3)."""
    s = (summary or '').strip()
    if not s:
        return False
    exact, prefix = _closer_texts()
    return s in exact or (bool(prefix) and s.startswith(prefix))


def is_ui_feature(suite: Any) -> bool:
    """UI_Feature decision from ``routing_audit`` and step text only (design §2).

    ``ui`` -> True; ``hybrid`` -> True iff no non-closer step carries an API
    signal; ``api``, anything else, or a missing ``routing_audit`` -> False.
    """
    audit = getattr(suite, 'routing_audit', None)
    classification = str(getattr(audit, 'classification', '') or '').strip().lower()
    if classification == 'ui':
        return True
    if classification != 'hybrid':
        return False
    for tc in getattr(suite, 'test_cases', None) or []:
        for step in getattr(tc, 'steps', None) or []:
            text = getattr(step, 'summary', '') or ''
            if is_closer_step(text):
                continue
            if infer_api_signal(text):
                return False
    return True


# --- 7b. Closer-step removal (design section 3, Req 1.1/1.4) -----------------------


def strip_closer_steps(tc: Any) -> Tuple[int, bool]:
    """Remove Closer_Steps from ``tc`` and renumber the survivors.

    Drops every step whose summary ``is_closer_step`` (design section 3), then
    renumbers the remaining steps' ``step_num`` 1..n in order. ``tc.summary`` is
    never touched and no replacement step is added (task 8.2 handles the empty
    case). Returns ``(removed_count, emptied)`` where ``emptied`` is True only
    when every original step was a Closer_Step (i.e. the TC had at least one
    step and all of them were removed).
    """
    steps = list(getattr(tc, 'steps', None) or [])
    kept = [s for s in steps if not is_closer_step(getattr(s, 'summary', '') or '')]
    removed = len(steps) - len(kept)
    for i, step in enumerate(kept, start=1):
        step.step_num = i
    tc.steps = kept
    emptied = bool(steps) and not kept
    return removed, emptied

# --- 7c. Per-step grounding (design §7 steps 1-5) --------------------------

# Filler markers (design §7 step 2). Group 1 is the underlying requirement text
# that is fed to the matcher.
_RE_FILLER_REQUIREMENT = re.compile(r'^\s*Verify requirement:\s*(.+)$', re.IGNORECASE | re.DOTALL)
_RE_FILLER_SCENARIO = re.compile(
    r'^\s*Perform the scenario-specific NBOP action:\s*(.+)$', re.IGNORECASE | re.DOTALL)

UNMATCHED_PREFIX = '[UNMATCHED-CRAWL] '

# Fixed page name for each existing typed id (design §7 step 1).
_TYPED_PAGE = {
    'nbop.subscriber.search': 'Subscriber Profile',
}
_TRANSACTION_HISTORY_PAGE = 'Context: Transaction History'

# control_type -> crawl map field, for the Source Locator (design §9).
_CONTROL_FIELD_BY_TYPE = {
    'tab': 'tabs',
    'button': 'buttons',
    'link': 'links',
    'input': 'inputs',
    'dropdown': 'dropdowns',
    'checkbox': 'checkboxes_radios',
    'heading': 'headings',
    'field': 'labels',
    'column': 'tables',
}

_CRAWL_MAP_REL = "TMO DashBoard/nbop_discovery/nbop_ui_map.json"


@dataclass
class GroundCtx:
    """Mutable per-TC grounding context.

    ``page_context`` carries the page name of the previous grounded step in the
    same TC, so the matcher can break display ties deterministically (design §5).
    It is reset per TC by the suite-level pass (task 8.2).
    """
    page_context: Optional[str] = None


def _filler_requirement(summary: Any) -> Optional[str]:
    """Underlying requirement text when ``summary`` is a filler step, else None."""
    text = summary if isinstance(summary, str) else ('' if summary is None else str(summary))
    for pattern in (_RE_FILLER_REQUIREMENT, _RE_FILLER_SCENARIO):
        m = pattern.match(text)
        if m:
            return m.group(1).strip()
    return None


def _typed_page_name(action_id: str) -> str:
    """Fixed page name for an existing typed id (design §7 step 1)."""
    if action_id in _TYPED_PAGE:
        return _TYPED_PAGE[action_id]
    if action_id.startswith('nbop.transaction_history.'):
        return _TRANSACTION_HISTORY_PAGE
    return ''


def _source_locator(page: CrawlPage, control: Optional[CrawlControl]) -> str:
    """Source Locator per design §9.

    Tile-only pages point at ``#landing_tiles['<display>']``; otherwise the
    locator names the page and, when a control was matched, its crawl field and
    verbatim label.
    """
    if control is None and page.kind == 'tile':
        return f"{_CRAWL_MAP_REL}#landing_tiles['{page.display}']"
    base = f"{_CRAWL_MAP_REL}#pages['{page.name}']"
    if control is None:
        return base
    field_name = _CONTROL_FIELD_BY_TYPE.get(control.control_type, control.control_type)
    return f"{base}.{field_name}['{control.label}']"


# Grounded render templates for filler steps, keyed by action family.
def _render_filler(action_id: str, page_disp: str, label: str, extra: dict) -> str:
    """Grounded wording for a filler step (design §7 step 3)."""
    value = extra.get('value', '')
    if action_id == A_OPEN_PAGE:
        return f"On NBOP, open '{page_disp}'"
    if action_id == A_OPEN_MENU:
        return f"On NBOP, open the '{page_disp}' menu"
    if action_id == A_OPEN_TAB:
        return f"On NBOP '{page_disp}', click the '{label}' tab"
    if action_id == A_OPEN_POPUP:
        return f"On NBOP '{page_disp}', click '{label}' and verify the popup opens"
    if action_id == A_CLICK_BUTTON:
        return f"On NBOP '{page_disp}', click '{label}'"
    if action_id == A_SELECT_DROPDOWN:
        if value:
            return f"On NBOP '{page_disp}', select '{value}' in the '{label}' dropdown"
        return f"On NBOP '{page_disp}', select a value in the '{label}' dropdown"
    if action_id == A_ENTER_TEXT:
        if value:
            return f"On NBOP '{page_disp}', enter '{value}' in the '{label}' field"
        return f"On NBOP '{page_disp}', enter a value in the '{label}' field"
    if action_id == A_FIELD_READONLY:
        return f"On NBOP '{page_disp}', verify the '{label}' field is read-only"
    if action_id == A_FIELD_EDITABLE:
        return f"On NBOP '{page_disp}', verify the '{label}' field is editable"
    if action_id == A_BUTTON_STATE:
        state = extra.get('expect', 'enabled')
        return f"On NBOP '{page_disp}', verify the '{label}' button is {state}"
    # A_ELEMENT_PRESENT and any fallback
    expect = extra.get('expect', 'present')
    disp = label or page_disp
    if expect == 'absent':
        return f"On NBOP '{page_disp}', verify '{disp}' is not displayed"
    return f"On NBOP '{page_disp}', verify '{disp}' is displayed"


def _substitute_verbatim(summary: str, page_disp: str, label: str) -> str:
    """Replace case-insensitive occurrences of the page display and the control
    label with their exact crawl spelling. Returns ``(text, matched_all)``.

    When either the page display or the label is not present in the text,
    ``matched_all`` is False and the caller appends the ``[NBOP: ...]`` suffix.
    """
    text = summary
    matched = True
    for token in (page_disp, label):
        if not token:
            continue
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub(lambda _m, t=token: t, text)
        else:
            matched = False
    return text, matched


def ground_step(tc: Any, step: Any, matcher: Optional[CrawlMatcher],
                ctx: GroundCtx) -> StepGrounding:
    """Ground one UI_Feature step against the crawl map (design §7 steps 1-5).

    Mutates ``step.summary`` (and ``step.expected`` only for a filler step that
    had no expected), attaches ``step._crawl_grounding`` and returns it. The pass
    is idempotent: a second call reproduces the first result and never doubles the
    ``[UNMATCHED-CRAWL]`` prefix. ``ctx.page_context`` is advanced to the grounded
    (or typed) page so later steps in the TC inherit it.
    """
    summary = getattr(step, 'summary', '') or ''
    expected = getattr(step, 'expected', '') or ''

    # Step 1: existing typed id wins; text is left untouched (Req 4.5).
    inferred = infer_step_contract(summary, expected, getattr(tc, 'summary', '') or '')
    if isinstance(inferred, dict) and inferred.get('mode') == 'typed':
        action_id = str(inferred.get('action_id', ''))
        page_name = _typed_page_name(action_id)
        grounding = StepGrounding(
            kind='legacy_typed', reason='', page=page_name,
            page_kind=('context_menu' if page_name == _TRANSACTION_HISTORY_PAGE else 'page'),
            action_id=action_id)
        if page_name:
            ctx.page_context = page_name
        step._crawl_grounding = grounding
        return grounding

    # Step 2: filler detection; group 1 feeds the matcher.
    requirement = _filler_requirement(summary)
    was_filler = requirement is not None
    match_input = requirement if was_filler else summary
    # The source text an UNMATCHED prefix is applied to.
    source_text = requirement if was_filler else summary

    # Step 3: family + match.
    action_id, wanted_types, extra = infer_ui_family(match_input)
    result = (matcher.match(match_input, page_context=ctx.page_context,
                            wanted_types=wanted_types,
                            require_control=needs_control(action_id))
              if matcher is not None
              else MatchResult('unmatched', 'crawl_unavailable', None, None, (), 0))

    if result.status == 'grounded' and result.page is not None:
        page = result.page
        control = result.control
        final_action, final_extra = classify_ui_action(match_input, result)
        label = control.label if control is not None else ''
        page_disp = page.display

        if was_filler:
            new_summary = _render_filler(final_action, page_disp, label, final_extra)
            if not expected:
                step.expected = source_text
        else:
            substituted, matched_all = _substitute_verbatim(summary, page_disp, label)
            if matched_all:
                new_summary = substituted
            else:
                tail = f"{page.name} \u203a {label}" if label else page.name
                new_summary = f"{summary} [NBOP: {tail}]"

        grounding = StepGrounding(
            kind='ui_action', reason='',
            page=page.name, page_url=page.url, page_kind=page.kind,
            control_type=(control.control_type if control is not None else 'page'),
            control_label=label, action_id=final_action,
            value=final_extra.get('value', ''), expect=final_extra.get('expect', ''),
            candidates=(), source_locator=_source_locator(page, control),
            was_filler=was_filler)
        step.summary = new_summary
        ctx.page_context = page.name
        step._crawl_grounding = grounding
        return grounding

    # Steps 4 & 5: unmatched (any reason) and crawl unavailable.
    reason = result.reason or 'no_match'
    if source_text.startswith(UNMATCHED_PREFIX):
        new_summary = source_text
    else:
        new_summary = UNMATCHED_PREFIX + source_text
    grounding = StepGrounding(
        kind='unmatched', reason=reason,
        candidates=tuple(result.candidates), was_filler=was_filler)
    step.summary = new_summary
    if was_filler and not expected:
        step.expected = source_text
    step._crawl_grounding = grounding
    return grounding

# ===========================================================================
# 7d. Suite-level grounding pass (design §7, "Placement"; task 8.2)
# ===========================================================================

# Issue reasons that are surfaced in the Grounding Report (design §10).
_ISSUE_REASONS = frozenset({
    'no_match', 'ambiguous', 'crawl_unavailable', 'action_unsupported',
    'empty_after_closer', 'grounding_error',
})


def _step_uid(feature_id: str, tc_number: str, step_number: int) -> str:
    """Stable UID for a step, used in the Grounding Report (design §10).

    Uses the shared ``stable_tc_uid``/``stable_step_uid`` helpers from
    ``contract_bridge`` when available, else a simple ``<fid>:TC<n>:STEP<m>``.
    """
    try:
        from modules import contract_bridge as _cb
        tc_uid = _cb.stable_tc_uid(feature_id, tc_number)      # type: ignore[attr-defined]
        return _cb.stable_step_uid(tc_uid, step_number)        # type: ignore[attr-defined]
    except Exception:
        return f'{feature_id}:TC{tc_number}:STEP{step_number}'


def _issue_for(grounding: StepGrounding, feature_id: str, tc_number: str,
               step: Any) -> Optional[GroundingIssue]:
    """Build a GroundingIssue for a step whose grounding needs reporting.

    Covers unmatched steps (``no_match`` / ``ambiguous`` / ``crawl_unavailable``)
    and enter_text, which grounds as kind ``ui_action`` but is unsupported by TSE
    until a handler lands (design §8) so it is reported as ``action_unsupported``.
    ``empty_after_closer`` and ``grounding_error`` are emitted by the caller.
    """
    if grounding.kind == 'unmatched':
        reason = grounding.reason or 'no_match'
    elif grounding.kind == 'ui_action' and grounding.action_id == A_ENTER_TEXT:
        reason = 'action_unsupported'
    else:
        return None
    step_number = int(getattr(step, 'step_num', 0) or 0)
    return GroundingIssue(
        tc_number=str(tc_number),
        step_number=step_number,
        step_uid=_step_uid(feature_id, str(tc_number), step_number),
        step_text=getattr(step, 'summary', '') or '',
        reason=reason,
        candidates=tuple(grounding.candidates),
    )


def _replacement_step(tc: Any) -> Any:
    """A single filler step built from ``tc.summary`` for an emptied TC (Req 1.4).

    Built via the same ``TestStep`` class as the TC's other steps so the suite
    stays homogeneous; the summary is the ``Verify requirement:`` filler form of
    the TC title so :func:`ground_step` treats it as a filler step.
    """
    title = getattr(tc, 'summary', '') or ''
    step_cls = type(tc.steps[0]) if getattr(tc, 'steps', None) else None
    if step_cls is None:
        from modules.data_models_v8 import TestStep as step_cls  # type: ignore
    return step_cls(step_num=1, summary=f'Verify requirement: {title}', expected='')


def apply_crawl_grounding(suite: Any, log=print, now: Optional[datetime] = None,
                          crawl_info: Any = None) -> None:
    """Ground a UI_Feature suite against the NBOP crawl map (design §7).

    Non-UI_Features return immediately with no attributes set (Req 8.2 holds by
    construction). For a UI_Feature the pass loads the crawl map (or an injected
    ``crawl_info``), builds a :class:`CrawlMatcher` when the map is available,
    logs any staleness warning, and then, per TC working on a deep copy so a
    mid-TC failure never leaves a half-rewritten TC:

      * strips Closer_Steps (renumbering survivors);
      * when a TC is emptied, adds one filler step from ``tc.summary`` (Req 1.4)
        and records an ``empty_after_closer`` issue;
      * grounds each remaining step in order with a fresh :class:`GroundCtx`;
      * commits the copy's steps back onto the real TC on success, or keeps the
        original TC and records a ``grounding_error`` issue on exception.

    Attaches ``step._crawl_grounding`` (done by :func:`ground_step`) and sets
    ``suite._grounding_result`` to a :class:`GroundingResult` with the full
    counts and issue list. Follows the ``suite._coverage_scorecard`` convention
    for the dynamic attributes.
    """
    if not is_ui_feature(suite):
        return

    import copy as _copy

    feature_id = str(getattr(suite, 'feature_id', '') or '')

    if crawl_info is None:
        from modules.nbop_ui_knowledge import get_crawl_map_info
        crawl_info = get_crawl_map_info()
    if now is None:
        now = datetime.now()

    crawl_path = str(getattr(crawl_info, 'path', '') or '')
    crawl_error = str(getattr(crawl_info, 'error', '') or '')
    crawl_data = getattr(crawl_info, 'data', None)
    discovered_at_dt = getattr(crawl_info, 'discovered_at', None)
    discovered_at = discovered_at_dt.isoformat() if discovered_at_dt is not None else ''

    # Build the matcher when the map is usable; otherwise matcher stays None and
    # ground_step marks every UI step crawl_unavailable (design §7 step 5).
    matcher: Optional[CrawlMatcher] = None
    if crawl_error or not isinstance(crawl_data, dict) or not crawl_data:
        if crawl_error:
            log('[CRAWL-GROUND] %s: crawl map unavailable (%s)'
                % (feature_id, crawl_error or 'no data'))
        else:
            log('[CRAWL-GROUND] %s: crawl map unavailable (no data)' % feature_id)
    else:
        matcher = CrawlMatcher(CrawlIndex.from_map(crawl_data))

    threshold_days, threshold_note = stale_threshold_days()
    stale_warning = staleness(crawl_info, now, threshold_days) or ''
    if stale_warning:
        log('[CRAWL-GROUND] %s: %s' % (feature_id, stale_warning))
        if threshold_note:
            log('[CRAWL-GROUND] %s: %s' % (feature_id, threshold_note))
    age_days = crawl_age_days(discovered_at_dt, now)

    issues: List[GroundingIssue] = []
    ui_steps = grounded_steps = unmatched_steps = filler_steps = closers_removed = 0

    for tc in getattr(suite, 'test_cases', None) or []:
        tc_number = str(getattr(tc, 'sno', '') or '')
        try:
            tc_copy = _copy.deepcopy(tc)
            removed, emptied = strip_closer_steps(tc_copy)
            local_closers = removed
            empty_issue = None
            if emptied or not getattr(tc_copy, 'steps', None):
                tc_copy.steps = [_replacement_step(tc_copy)]
                step_number = int(getattr(tc_copy.steps[0], 'step_num', 1) or 1)
                empty_issue = GroundingIssue(
                    tc_number=tc_number, step_number=step_number,
                    step_uid=_step_uid(feature_id, tc_number, step_number),
                    step_text=getattr(tc_copy.steps[0], 'summary', '') or '',
                    reason='empty_after_closer', candidates=())

            ctx = GroundCtx()
            step_groundings: List[StepGrounding] = []
            for step in tc_copy.steps:
                g = ground_step(tc_copy, step, matcher, ctx)
                step_groundings.append(g)
        except Exception as exc:  # keep the original TC untouched (design Error Handling)
            step_number = 0
            issues.append(GroundingIssue(
                tc_number=tc_number, step_number=step_number,
                step_uid=_step_uid(feature_id, tc_number, step_number),
                step_text=getattr(tc, 'summary', '') or '',
                reason='grounding_error', candidates=(str(exc)[:120],)))
            continue

        # Success: commit the copy's steps back onto the real TC.
        tc.steps = tc_copy.steps
        closers_removed += local_closers
        if empty_issue is not None:
            issues.append(empty_issue)
        for step, g in zip(tc.steps, step_groundings):
            ui_steps += 1
            if g.kind in ('ui_action', 'legacy_typed'):
                grounded_steps += 1
            if g.kind == 'unmatched':
                unmatched_steps += 1
            # Filler_Steps is a POST-PASS residual count (design §10): a step is still
            # filler only if its rewritten summary is STILL generic fallback text. A
            # filler-origin step that was grounded or flagged [UNMATCHED-CRAWL] is no
            # longer filler (requirements §3, "flagged, not filler").
            if _filler_requirement(getattr(step, 'summary', '')) is not None:
                filler_steps += 1
            issue = _issue_for(g, feature_id, tc_number, step)
            if issue is not None:
                issues.append(issue)

    result = GroundingResult(
        feature_id=feature_id,
        crawl_path=crawl_path,
        crawl_error=crawl_error,
        discovered_at=discovered_at,
        age_days=age_days,
        stale_warning=stale_warning,
        ui_steps=ui_steps,
        grounded_steps=grounded_steps,
        unmatched_steps=unmatched_steps,
        filler_steps=filler_steps,
        closers_removed=closers_removed,
        issues=issues,
    )
    suite._grounding_result = result

    log('[CRAWL-GROUND] %s: ui_steps=%d grounded=%d unmatched=%d filler=%d '
        'closers_removed=%d'
        % (feature_id, ui_steps, grounded_steps, unmatched_steps,
           filler_steps, closers_removed))
