# -*- coding: utf-8 -*-
"""
qmetry_steps_source.py — TSG QMetry Authored-Steps Supplement
=============================================================

For a feature whose QA has ALREADY authored test-case steps in QMetry, those
authored steps are the ground truth. This module pulls them and injects them as
the ``steps_hint`` of the matching mined ``ExtractedScenario`` objects so the
existing ``tc_builder`` emits the real QA steps instead of generated boilerplate.

Design: ``.agents/tasks/design.md`` (revision 6). This is a best-effort, fail-safe
SUPPLEMENT:

  * It never creates, drops, reorders, or renames a scenario / test case.
  * It only edits ``steps_hint`` and the plain ``_qmetry_expected`` attribute.
  * ``obligations`` are never cleared (R2/R5 accounting preserved).
  * Any auth / network / export / parse failure is non-fatal — the engine hook
    swallows it and normal generation continues.

The QMetry client is the MDA ``QMetryRestClient``, imported by ``sys.path``
insertion — NEVER copied/forked. ``require_qmetry_legacy()`` runs inside its
``__init__`` so constructing the client enforces gateway policy.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

# ── MDA client path (sys.path import only — never forked) ──
_MDA = r'c:\Users\P3314665\Documents\mcp-jira-server\MDA-Jira-Dashboard'
if _MDA not in sys.path:
    sys.path.insert(0, _MDA)


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class QMetrySessionError(Exception):
    """Raised by ``load_qmetry_cookies`` when no candidate session authenticates."""


class QMetryFetchError(Exception):
    """Raised inside the fetcher when folder resolve / export / parse fails."""


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT RESOLVER
# ═══════════════════════════════════════════════════════════════════════════════

# Jira project prefix / component  →  QMetry projectId. Single onboarding point
# for a new project. NEVER hardcode MDA's own 127207 here.
QMETRY_PROJECT_MAP = {
    'MWTGMED': '76000',          # Mediation features (MWTGMED-*)
    'WPAMED-Mediation': '76000',
}
DEFAULT_QMETRY_PROJECT = None     # unknown → skip gracefully


def resolve_project_id(feature_key: str) -> Optional[str]:
    """Map a Jira feature key to a QMetry projectId, or ``None`` when unknown.

    Matches the component prefix (the token before the first ``-``) against
    ``QMETRY_PROJECT_MAP``, plus the explicit ``WPAMED-Mediation`` alias. ``None``
    means "not a known QMetry project → supplement skipped, not an error".
    """
    fk = (feature_key or '').strip()
    if not fk:
        return DEFAULT_QMETRY_PROJECT
    # Explicit full-key aliases first (e.g. WPAMED-Mediation).
    if fk in QMETRY_PROJECT_MAP:
        return QMETRY_PROJECT_MAP[fk]
    prefix = fk.split('-', 1)[0]
    return QMETRY_PROJECT_MAP.get(prefix, DEFAULT_QMETRY_PROJECT)


def resolve_project_for_feature(feature_key: str, subtask_keys=None) -> Optional[str]:
    """Resolve the QMetry projectId for a feature, considering its subtask keys.

    A Mediation feature often carries a non-QMetry feature key (e.g.
    ``MWTGPROV-4628``) while the authored QMetry test cases live under a subtask in
    a QMetry-mapped project (e.g. ``MWTGMED-5214`` → 76000). Resolution order:
      1. the feature key itself (``resolve_project_id``),
      2. then each VALID subtask key, in order.
    Returns the first mapped projectId, else ``None`` (skip, not an error).
    """
    pid = resolve_project_id(feature_key)
    if pid is not None:
        return pid
    for k in _valid_keys(subtask_keys or []):
        pid = resolve_project_id(k)
        if pid is not None:
            return pid
    return DEFAULT_QMETRY_PROJECT


# ═══════════════════════════════════════════════════════════════════════════════
# JIRA-KEY SHAPE GATE
# ═══════════════════════════════════════════════════════════════════════════════

# A real Jira key: a letter, more letters/digits, a dash, then digits
# (e.g. MWTGMED-5214, WPAMED-4628). This also enforces len >= 5, so a blank or
# whitespace-only subtask key can never drive a containment / tier-2 match.
_JIRA_KEY_RE = re.compile(r'^[A-Z][A-Z0-9]+-\d+$')


def _valid_keys(keys) -> List[str]:
    """Drop blanks/whitespace, require a real Jira-key shape."""
    return [k for k in ((s or '').strip() for s in (keys or [])) if _JIRA_KEY_RE.match(k)]


# ═══════════════════════════════════════════════════════════════════════════════
# STEP SPLITTER (dedicated — does NOT reuse the builder's _STEP_NUM_PREFIX_RE)
# ═══════════════════════════════════════════════════════════════════════════════

_PACKED_SPLIT_RE = re.compile(r'(?:^|\s)(?=\d+[.)]\s)')   # split BEFORE each "N." / "N)"
_LEAD_NUM_RE = re.compile(r'^\s*\d+[.)]\s*')              # strip the enumerator


def split_packed_steps(text: str) -> List[str]:
    """Split a packed ``"1. … 2. … 3. …"`` string into ordered step summaries.

    A single non-enumerated string yields a one-element list; empty → ``[]``.
    """
    if not text:
        return []
    pieces = _PACKED_SPLIT_RE.split(text)
    out = [_LEAD_NUM_RE.sub('', p).strip() for p in pieces]
    return [p for p in out if p]


# ═══════════════════════════════════════════════════════════════════════════════
# COOKIE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

_REPO_ROOT = r'c:\Users\P3314665\Documents\mcp-jira-server'
_QMETRY_BASE_URL = 'https://jira.charter.com'


def _candidate_storage_states() -> List[Path]:
    """Candidate ``storage_state.json`` paths, in documented search order."""
    # Worktree TestSuiteGenerator/ resolved from __file__ (…/modules/this → parent.parent).
    tsg_root = Path(__file__).resolve().parent.parent
    return [
        tsg_root / 'storage_state.json',
        Path(_REPO_ROOT) / 'storage_state.json',
        Path(_REPO_ROOT) / 'MDA-Jira-Dashboard' / 'storage_state.json',
        Path(_REPO_ROOT) / 'TMO DashBoard' / 'storage_state.json',
    ]


def _charter_cookies(path: Path) -> Dict[str, str]:
    """Parse a Playwright storage_state and return charter.com cookies as name→value.

    Returns ``{}`` on any read/parse problem or when no charter.com cookie exists.
    """
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return {}
    out = {}
    for c in (data.get('cookies') or []):
        try:
            if 'charter.com' in (c.get('domain') or ''):
                name = c.get('name')
                if name:
                    out[name] = c.get('value', '')
        except Exception:
            continue
    return out


def _probe_authenticates(cookies: Dict[str, str], log) -> bool:
    """One concrete auth rule (design NIT #3): constructor returns without raising
    AND ``health_check()`` is truthy. Both wrapped in one try/except so a
    toggle-disabled / legacy-policy ctor failure is "skip this candidate".
    """
    try:
        from qmetry_rest_client import QMetryRestClient
        # Any mapped pid is fine for the throwaway probe; /projects is project-agnostic.
        pid = next(iter(QMETRY_PROJECT_MAP.values()), '76000')
        client = QMetryRestClient(base_url=_QMETRY_BASE_URL, cookies=cookies,
                                  project_id=pid, logger=log)
        # health_check() returns a real bool (verified in MDA client).
        return bool(client.health_check()) is True
    except Exception as exc:
        log('[QMETRY] candidate probe failed: %s' % str(exc)[:160])
        return False


def load_qmetry_cookies(log=print) -> Dict[str, str]:
    """Return the freshest ``storage_state.json`` cookie dict that authenticates.

    Search order (existing files only, newest mtime first within that order):
      1. ``TestSuiteGenerator/storage_state.json`` (worktree root)
      2. repo-root ``storage_state.json``
      3. ``MDA-Jira-Dashboard/storage_state.json``
      4. ``TMO DashBoard/storage_state.json``

    Raises ``QMetrySessionError`` (naming the paths + the MDA headed-login
    remediation) only when every candidate fails. Never opens a browser.
    """
    candidates = [p for p in _candidate_storage_states() if p.is_file()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    for path in candidates:
        cookies = _charter_cookies(path)
        if not cookies:
            log('[QMETRY] %s has no charter.com cookies — skipping' % path)
            continue
        if _probe_authenticates(cookies, log):
            log('[QMETRY] authenticated via %s' % path)
            return cookies
        log('[QMETRY] %s did not authenticate — trying next' % path)

    searched = ', '.join(str(p) for p in _candidate_storage_states())
    raise QMetrySessionError(
        'No QMetry session authenticated. Searched: %s. '
        'Run the one-time MDA headed login to regenerate storage_state.json: '
        "with BrowserManager(headed=True) as bm: bm.login_jira(force=True) "
        '(note is_logged_in is a property). The headless TSG run never opens a browser.'
        % searched)


# ═══════════════════════════════════════════════════════════════════════════════
# XLSX PARSING — confirmed layout (sheet 'Sheet1')
# ═══════════════════════════════════════════════════════════════════════════════

# Field → (header name, 0-based index fallback). Resolve by HEADER NAME at runtime,
# fall back to the pinned index.
_COLS = {
    'issue_key':      ('Issue Key', 0),
    'summary':        ('Summary', 1),
    'step_summary':   ('Step Summary', 16),
    'test_data':      ('Test Data', 17),
    'expected':       ('Expected Result', 18),
    'story_linkages': ('Story Linkages', 26),
}

_LINKAGE_SPLIT_RE = re.compile(r'[,;\s]+')


def _split_linkages(cell: str) -> List[str]:
    """Split a Story Linkages cell on ``,``/``;``/whitespace into a key list."""
    if not cell:
        return []
    return [p for p in _LINKAGE_SPLIT_RE.split(str(cell).strip()) if p]


def _resolve_columns(header_row) -> Dict[str, int]:
    """Map each field to a column index by header name, else the pinned fallback."""
    by_name = {}
    for idx, cell in enumerate(header_row or []):
        if cell is not None:
            by_name[str(cell).strip()] = idx
    resolved = {}
    for field, (hname, fallback) in _COLS.items():
        resolved[field] = by_name.get(hname, fallback)
    return resolved


def _cell(row, idx) -> str:
    try:
        v = row[idx]
    except (IndexError, TypeError):
        return ''
    return '' if v is None else str(v).strip()


def parse_qmetry_xlsx(file_path) -> Dict[str, Dict[str, Any]]:
    """Parse an exported QMetry xlsx into
    ``{tc_key: {'summary', 'story_linkages': [..], 'steps': [{'summary','test_data','expected'}]}}``.

    Forward-fills a BLANK ``Issue Key`` to group multi-row steps under their TC.
    Raises ``QMetryFetchError`` when the sheet / columns cannot be read.
    """
    try:
        from openpyxl import load_workbook
    except Exception as exc:
        raise QMetryFetchError('openpyxl unavailable: %s' % exc)

    try:
        wb = load_workbook(filename=str(file_path), read_only=True, data_only=True)
    except Exception as exc:
        raise QMetryFetchError('cannot open xlsx %s: %s' % (file_path, exc))

    try:
        if 'Sheet1' in wb.sheetnames:
            ws = wb['Sheet1']
        else:
            ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        try:
            wb.close()
        except Exception:
            pass

    if not rows:
        raise QMetryFetchError('empty xlsx (no rows)')

    cols = _resolve_columns(rows[0])
    result: "Dict[str, Dict[str, Any]]" = {}
    current_key = None

    for row in rows[1:]:
        issue_key = _cell(row, cols['issue_key'])
        step_summary = _cell(row, cols['step_summary'])
        test_data = _cell(row, cols['test_data'])
        expected = _cell(row, cols['expected'])

        if issue_key:
            # New TC header row.
            current_key = issue_key
            result[current_key] = {
                'summary': _cell(row, cols['summary']),
                'story_linkages': _split_linkages(_cell(row, cols['story_linkages'])),
                'steps': [],
            }

        if current_key is None:
            # Step row before any header row — skip defensively.
            continue

        # Skip all-blank step rows.
        if step_summary or test_data or expected:
            result[current_key]['steps'].append({
                'summary': step_summary,
                'test_data': test_data,
                'expected': expected,
            })

    return result


def _apply_packed_rule(parsed: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Apply the packed-vs-multi-row decision (design Finding 3) by TC row count.

    One step row → ``split_packed_steps`` on that row's ``Step Summary``.
    ≥2 step rows → each row's ``Step Summary`` verbatim, never re-split.

    Returns a new dict with a flat ``step_summaries`` list added per TC while
    leaving the original ``steps`` rows intact (the last step's Expected Result is
    read from ``steps``).
    """
    for tc in parsed.values():
        steps = tc.get('steps') or []
        if len(steps) == 1:
            summaries = split_packed_steps(steps[0].get('summary', ''))
        else:
            summaries = [s.get('summary', '') for s in steps if s.get('summary', '')]
        tc['step_summaries'] = summaries
    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════════

_EXPORT_CACHE: Dict[tuple, Dict[str, Dict[str, Any]]] = {}   # (pid, folder_id) → parsed
_FOLDER_MEMO: Dict[tuple, Optional[str]] = {}                # (pid, feature_key) → folder_id
_DISK_TTL_SECS = 6 * 3600                                    # 6h default


def _disk_cache_path(project_id: str, folder_id: str) -> Path:
    base = Path(__file__).resolve().parent.parent / 'outputs' / '.qmetry_cache'
    return base / ('%s_%s.json' % (project_id, folder_id))


def _disk_cache_read(project_id: str, folder_id: str, log) -> Optional[Dict[str, Dict[str, Any]]]:
    path = _disk_cache_path(project_id, folder_id)
    try:
        if not path.is_file():
            return None
        with open(path, 'r', encoding='utf-8') as fh:
            blob = json.load(fh)
        ts = blob.get('ts')
        parsed = blob.get('parsed')
        if not isinstance(ts, (int, float)) or parsed is None:
            return None
        if (time.time() - ts) > _DISK_TTL_SECS:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception as exc:
        log('[QMETRY] disk cache read skipped (%s)' % str(exc)[:120])
        return None


def _disk_cache_write(project_id: str, folder_id: str, parsed: Dict[str, Dict[str, Any]], log) -> None:
    path = _disk_cache_path(project_id, folder_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'ts': time.time(), 'parsed': parsed}, fh)
    except Exception as exc:
        log('[QMETRY] disk cache write skipped (%s)' % str(exc)[:120])


# ═══════════════════════════════════════════════════════════════════════════════
# FETCHER
# ═══════════════════════════════════════════════════════════════════════════════

def _iter_folders(nodes):
    """Yield every folder dict in a (possibly nested) folder tree."""
    for node in (nodes or []):
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get('children')
        if children:
            yield from _iter_folders(children)


def _resolve_folder_id(client, project_id, candidate_keys, log) -> Optional[str]:
    """Return the id of the folder whose name CONTAINS one validated candidate key."""
    try:
        folders = client.get_testcase_folders()
    except Exception as exc:
        raise QMetryFetchError('folder list failed: %s' % exc)
    flist = folders if isinstance(folders, list) else (folders.get('data', folders)
                                                       if isinstance(folders, dict) else [])
    keys_lower = [k.lower() for k in candidate_keys]
    for node in _iter_folders(flist):
        name = str(node.get('name', '') or '').lower()
        if not name:
            continue
        if any(k in name for k in keys_lower):
            fid = node.get('id')
            if fid is not None:
                log('[QMETRY] folder resolved: id=%s (%s)' % (fid, node.get('name')))
                return str(fid)
    return None


def fetch_authored_steps(project_id, feature_key, subtask_keys, cookies=None,
                         log=print) -> Dict[str, Dict[str, Any]]:
    """Fetch + parse authored QMetry steps for a feature.

    Returns ``{tc_key: {'summary', 'story_linkages': [..], 'steps': [...],
    'step_summaries': [..]}}``. Returns ``{}`` (graceful skip) when no candidate
    key is valid or no folder matches. Raises ``QMetryFetchError`` on export /
    parse failure.

    ``cookies`` is the authenticated cookie dict from ``load_qmetry_cookies``;
    it may be ``None`` in tests that monkeypatch the client (construction is then
    a no-op mock).
    """
    candidate_keys = _valid_keys(list(subtask_keys or []) + [feature_key])
    if not candidate_keys:
        log('[QMETRY] no valid Jira keys among subtasks/feature — skipping')
        return {}

    # Lazy client construction (import fails gracefully as QMetryFetchError).
    try:
        from qmetry_rest_client import QMetryRestClient
    except Exception as exc:
        raise QMetryFetchError('QMetryRestClient import failed: %s' % exc)

    cookies = cookies or {}
    try:
        client = QMetryRestClient(base_url=_QMETRY_BASE_URL, cookies=cookies,
                                  project_id=str(project_id), logger=log)
    except Exception as exc:
        raise QMetryFetchError('QMetry client construction failed: %s' % exc)

    # Folder resolution (memoized per feature per run).
    memo_key = (str(project_id), feature_key)
    if memo_key in _FOLDER_MEMO:
        folder_id = _FOLDER_MEMO[memo_key]
    else:
        folder_id = _resolve_folder_id(client, project_id, candidate_keys, log)
        _FOLDER_MEMO[memo_key] = folder_id
    if not folder_id:
        log('[QMETRY] no folder matched candidate keys %s — skipping' % candidate_keys)
        return {}

    # Export/parse cache (per project_id, folder_id) for the run.
    cache_key = (str(project_id), str(folder_id))
    if cache_key in _EXPORT_CACHE:
        return _EXPORT_CACHE[cache_key]

    disk = _disk_cache_read(str(project_id), str(folder_id), log)
    if disk is not None:
        _EXPORT_CACHE[cache_key] = disk
        return disk

    try:
        result = client.export_testcases_v2(folder_id=str(folder_id))
    except Exception as exc:
        raise QMetryFetchError('export failed: %s' % exc)
    if getattr(result, 'status', '') != 'success' or not getattr(result, 'file_path', None):
        raise QMetryFetchError('export did not succeed: status=%s error=%s'
                               % (getattr(result, 'status', '?'), getattr(result, 'error', '')))

    parsed = parse_qmetry_xlsx(result.file_path)
    parsed = _apply_packed_rule(parsed)

    _EXPORT_CACHE[cache_key] = parsed
    _disk_cache_write(str(project_id), str(folder_id), parsed, log)
    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO MATCH
# ═══════════════════════════════════════════════════════════════════════════════

_MATCH_THRESHOLD = 0.6   # Jaccard over normalized tokens


def _tokens(text: str) -> set:
    return {t for t in re.split(r'[^A-Za-z0-9]+', (text or '').lower()) if t}


# ── Value-pair extraction from a QMetry TC summary (tier-1 primary) ──
# Matches a quoted input value "converted to" a quoted output value, e.g.
#   MMS_TYPE_INDICATOR value 'MT' is converted to 'I' (capital letter I) ...
# Both single and double quotes are accepted. Returns (input, output) upper-cased,
# or None when the summary carries no clean single value pair.
# A quoted input value, then a short connective ending in "to"/"into", then a quoted
# output value. Covers both authored phrasings:
#   Convert MMS_TYPE_INDICATOR 'MT' to 'I' in call_direction_ind
#   MMS_TYPE_INDICATOR value 'MT' is converted to 'I' (capital letter I) ...
# The connective allows up to a few words ("is converted", "maps", "", ...) but MUST
# end at the word to/into, so unrelated quoted tokens elsewhere never pair up.
_VALUE_PAIR_RE = re.compile(
    r"['\"]([A-Za-z0-9/_-]{1,24})['\"]\s+(?:\w+\s+){0,3}?(?:to|into)\s+"
    r"['\"]([A-Za-z0-9/_-]{1,24})['\"]",
    re.IGNORECASE)


def _extract_value_pair(tc_summary: str):
    """Extract a single (input_value, output_value) pair from a TC summary, or None.

    Returns None when zero OR more-than-one distinct pair is found — an ambiguous
    summary is treated as "no clean value pair" (per decision) so it falls to the
    similarity tier rather than guessing.
    """
    matches = _VALUE_PAIR_RE.findall(tc_summary or '')
    pairs = {(m[0].strip().upper(), m[1].strip().upper()) for m in matches
             if m[0].strip() and m[1].strip()}
    if len(pairs) != 1:
        return None
    return next(iter(pairs))


def _obligation_pair(ob: dict):
    iv = (ob.get('input_value') or '').strip().upper()
    ev = (ob.get('expected_value') or '').strip().upper()
    if not iv or not ev:
        return None
    return (iv, ev)


def _title_similarity(a: str, b: str) -> float:
    ta = set(re.sub(r'[^a-z0-9 ]', ' ', (a or '').lower()).split())
    tb = set(re.sub(r'[^a-z0-9 ]', ' ', (b or '').lower()).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _story_linkage_eligible(story_linkages: list, validated_keys: list) -> bool:
    """Pre-filter: the TC's story linkages contain a validated subtask key (whole-key
    equality). This only decides whether a TC is ELIGIBLE for this feature — it is
    NOT a matcher and never picks a scenario.
    """
    linkset = set(story_linkages or [])
    return any(k in linkset for k in validated_keys)


def _authored_hint_list(tc: Dict[str, Any]) -> List[str]:
    """The ordered authored step summaries (packed rule already applied by parser)."""
    return list(tc.get('step_summaries') or [])


def _authored_expected(tc: Dict[str, Any]) -> str:
    """The last authored step's Expected Result."""
    steps = tc.get('steps') or []
    for st in reversed(steps):
        exp = (st.get('expected') or '').strip()
        if exp:
            return exp
    return ''


def _apply_match(sc, tc, tier, tc_key, enriched_ids, log) -> bool:
    """Attach a TC's authored steps to scenario ``sc``. Returns True on attach.

    A non-empty extraction ``steps_hint`` is overwritten ONLY by a value-pair
    (tier-1) match; a similarity (tier-2) match leaves an existing non-empty hint
    untouched. ``obligations`` are never cleared.
    """
    existing = getattr(sc, 'steps_hint', None) or []
    if existing and tier != 1:
        log('[QMETRY] TC %s (%s) skipped — scenario "%s" already has a %d-step hint'
            % (tc_key, tier_name(tier), getattr(sc, 'title', ''), len(existing)))
        return False
    if existing and tier == 1:
        log('[QMETRY] value-pair overwrite of %d-step extraction hint on "%s"'
            % (len(existing), getattr(sc, 'title', '')))
    sc.steps_hint = list(_authored_hint_list(tc))
    sc._qmetry_expected = _authored_expected(tc)
    enriched_ids.add(id(sc))
    log('[QMETRY] TC %s (%s) → scenario "%s" (%d authored steps)'
        % (tc_key, tier_name(tier), getattr(sc, 'title', ''), len(sc.steps_hint)))
    return True


def tier_name(tier: int) -> str:
    return {1: 'value-pair', 2: 'similarity'}.get(tier, 'tier-%d' % tier)


def attach_steps_to_scenarios(scenarios, authored, log=print, validated_keys=None) -> int:
    """Attach authored QMetry steps onto the correctly-matched ExtractedScenario.

    Matching rule (per decision — NO positional/document-order fallback, EVER):

      PRE-FILTER — story linkage: a TC is eligible for this feature only when its
        story linkages contain a validated subtask key (whole-key equality). Linkage
        narrows the candidate set; it never picks a scenario.

      TIER 1 — value-pair (primary, exact, 1 TC ↔ 1 obligation): extract the single
        (input, output) value pair from the TC summary (e.g. 'MT'→'I'); bind it to
        the scenario whose obligation has input_value==input AND expected_value==output.
        Each obligation is claimed by at most one TC. Ambiguous/absent pair → not a
        tier-1 match (fall through, never guess).

      TIER 2 — title/summary similarity (fallback): the single remaining scenario
        whose Jaccard ≥ _MATCH_THRESHOLD and is strictly the highest scorer. A tie for
        the top score leaves the TC UNMATCHED (no arbitrary/positional pick).

      TIER 3 — DROP: a TC that matches no obligation and clears no similarity bar is
        dropped (authored steps discarded); the affected scenarios keep normal
        generation. Logged at INFO.

    Each scenario is enriched at most once. ``obligations`` are never cleared.
    Returns the enrichment count.
    """
    scenarios = list(scenarios or [])
    validated_keys = validated_keys or []
    enriched_ids = set()          # id() of scenarios already enriched
    claimed_obligations = set()   # (input, output) pairs already bound to a TC
    count = 0

    # Eligible TCs only (story-linkage pre-filter), in document order.
    eligible = []
    for tc_key, tc in (authored or {}).items():
        if not _authored_hint_list(tc):
            continue
        if not _story_linkage_eligible(tc.get('story_linkages') or [], validated_keys):
            log('[QMETRY] TC %s not story-linked to this feature — ineligible' % tc_key)
            continue
        eligible.append((tc_key, tc))

    # ── TIER 1: value-pair (primary). Run across ALL eligible TCs first so an exact
    #    obligation match is never crowded out by a similarity match. ──
    deferred = []   # TCs with no clean value pair → similarity/drop
    for tc_key, tc in eligible:
        pair = _extract_value_pair(tc.get('summary', '') or '')
        if pair is None:
            deferred.append((tc_key, tc))
            continue
        if pair in claimed_obligations:
            log('[QMETRY] TC %s value-pair %s already claimed — dropped' % (tc_key, pair))
            continue
        # Among ALL non-enriched scenarios carrying this exact obligation pair, pick the
        # one whose title best matches the TC summary (tie → document order). This keeps
        # tier-1 exact on the obligation while landing the steps on the most relevant,
        # readable scenario when several carry the same pair.
        tc_summary = tc.get('summary', '') or ''
        matches = []
        for idx, sc in enumerate(scenarios):
            if id(sc) in enriched_ids:
                continue
            if any(_obligation_pair(ob) == pair for ob in (getattr(sc, 'obligations', None) or [])):
                matches.append((_title_similarity(tc_summary, getattr(sc, 'title', '') or ''),
                                -idx, sc))
        target = max(matches, key=lambda m: (m[0], m[1]))[2] if matches else None
        if target is None:
            log('[QMETRY] TC %s value-pair %s matches no obligation — deferring to similarity'
                % (tc_key, pair))
            deferred.append((tc_key, tc))
            continue
        if _apply_match(target, tc, 1, tc_key, enriched_ids, log):
            claimed_obligations.add(pair)
            count += 1

    # ── TIER 2: title/summary similarity (fallback). Single strictly-highest scorer
    #    ≥ threshold; a tie at the top leaves the TC unmatched. ──
    for tc_key, tc in deferred:
        tc_summary = tc.get('summary', '') or ''
        scored = []
        for sc in scenarios:
            if id(sc) in enriched_ids:
                continue
            s = _title_similarity(tc_summary, getattr(sc, 'title', '') or '')
            if s >= _MATCH_THRESHOLD:
                scored.append((s, sc))
        if not scored:
            log('[QMETRY] TC %s — no scenario ≥ similarity threshold %.2f — dropped'
                % (tc_key, _MATCH_THRESHOLD))
            continue
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[0][0]
        winners = [sc for s, sc in scored if s == top]
        if len(winners) != 1:
            log('[QMETRY] TC %s — similarity tie at %.2f among %d scenarios — left unmatched'
                % (tc_key, top, len(winners)))
            continue
        if _apply_match(winners[0], tc, 2, tc_key, enriched_ids, log):
            count += 1

    return count


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT (called from the engine hook)
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_scenarios_with_qmetry(scenarios, jira, options, log=print) -> int:
    """Gate + fetch + attach. Raises QMetrySessionError/QMetryFetchError on failure
    (the engine hook swallows them). Returns the enrichment count.
    """
    feature_key = getattr(jira, 'key', '') if jira else ''
    subtask_keys = [k for st in (getattr(jira, 'subtasks', None) or [])
                    if isinstance(st, dict) and (k := (st.get('key') or '').strip())]

    pid = resolve_project_for_feature(feature_key, subtask_keys)
    if pid is None:
        log('[QMETRY] feature %s (subtasks %s) not mapped to a QMetry project — skipping'
            % (feature_key, subtask_keys))
        return 0

    cookies = load_qmetry_cookies(log=log)   # raises QMetrySessionError if none authenticate
    authored = fetch_authored_steps(pid, feature_key, subtask_keys, cookies=cookies, log=log)

    n = attach_steps_to_scenarios(scenarios, authored, log=log,
                                  validated_keys=_valid_keys(subtask_keys))
    log('[QMETRY] authored steps attached to %d scenario(s)' % n)
    return n
