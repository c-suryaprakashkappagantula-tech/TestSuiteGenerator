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

# New-TC construction reuses the engine's own scenario object + traceability factory
# (never a hand-built TestCase). Guard the import so a bare unit import of the splitter
# helpers still works even if the package context is unusual.
try:
    from .data_models_v8 import ExtractedScenario
    from .traceability import create_traceability
except Exception:  # pragma: no cover — fallback for non-package import contexts
    try:
        from modules.data_models_v8 import ExtractedScenario
        from modules.traceability import create_traceability
    except Exception:
        ExtractedScenario = None
        create_traceability = None

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

# ADDITIVE FALLBACK ONLY — consulted *only* when the dynamic ``/projects`` lookup
# cannot resolve a key (e.g. the QMetry session cannot list projects, or a QMetry
# project whose ``key`` differs from the Jira prefix). The PRIMARY, project-agnostic
# mechanism is ``resolve_project_dynamic`` below, which queries QMetry's ``/projects``
# endpoint and matches the Jira project prefix against each project's ``key`` — so
# MWTGMED, NSLNM, and every FUTURE project are picked up with NO code change here.
# This table is NOT the source of truth; it is a safety net kept easy to extend
# (one line per entry). NEVER hardcode MDA's own 127207 here.
QMETRY_PROJECT_MAP = {
    'MWTGMED': '76000',          # Mediation features (MWTGMED-*)
    'WPAMED-Mediation': '76000',  # alias: QMetry project NAME for the same 76000
}
DEFAULT_QMETRY_PROJECT = None     # unknown → skip gracefully

# Per-run cache of the dynamic {PROJECT_KEY_UPPER: projectId} index built from
# QMetry's ``/projects`` endpoint. ``None`` = not yet loaded this run.
_PROJECT_INDEX: Optional[Dict[str, str]] = None


def resolve_project_id(feature_key: str) -> Optional[str]:
    """Map a Jira feature key to a QMetry projectId via the STATIC fallback table.

    Matches the component prefix (the token before the first ``-``) against
    ``QMETRY_PROJECT_MAP``, plus the explicit ``WPAMED-Mediation`` alias. ``None``
    means "not in the fallback table". This is the additive fallback consulted only
    after the dynamic ``/projects`` resolver (``resolve_project_dynamic``) cannot
    answer — it is NOT the primary resolution path.
    """
    fk = (feature_key or '').strip()
    if not fk:
        return DEFAULT_QMETRY_PROJECT
    # Explicit full-key aliases first (e.g. WPAMED-Mediation).
    if fk in QMETRY_PROJECT_MAP:
        return QMETRY_PROJECT_MAP[fk]
    prefix = fk.split('-', 1)[0]
    return QMETRY_PROJECT_MAP.get(prefix, DEFAULT_QMETRY_PROJECT)


def _key_prefix(jira_key: str) -> str:
    """Return the uppercased Jira project prefix (token before the first ``-``)."""
    return (jira_key or '').strip().split('-', 1)[0].upper()


def _load_project_index(cookies, log=print) -> Dict[str, str]:
    """Build (and cache for the run) ``{PROJECT_KEY_UPPER: projectId}`` from QMetry's
    project-agnostic ``/projects`` endpoint.

    The Jira project KEY returned per QMetry project (e.g. ``{"id": 121405,
    "key": "NSLNM", ...}``, ``{"id": 76000, "key": "MWTGMED", ...}``) is exactly the
    prefix of a subtask key, so this index resolves ANY project dynamically with no
    hardcoding. Constructed through the same authenticated ``QMetryRestClient`` the
    fetcher uses; any mapped/placeholder projectId is fine because ``/projects`` is
    itself project-agnostic (NEVER MDA's default 127207 — we pass a mapped id).

    FAIL-LOUD (design D5): an HTTP / transport failure raises ``QMetryFetchError``
    rather than silently returning an empty index, so a dead session surfaces as a
    visible skip warning via the engine hook instead of a silent wrong answer.
    """
    global _PROJECT_INDEX
    if _PROJECT_INDEX is not None:
        return _PROJECT_INDEX

    try:
        from qmetry_rest_client import QMetryRestClient
    except Exception as exc:
        raise QMetryFetchError('QMetryRestClient import failed: %s' % exc)

    # Any mapped placeholder pid is fine — /projects ignores the project id.
    pid = next(iter(QMETRY_PROJECT_MAP.values()), '76000')
    try:
        client = QMetryRestClient(base_url=_QMETRY_BASE_URL, cookies=(cookies or {}),
                                  project_id=str(pid), logger=log)
    except Exception as exc:
        raise QMetryFetchError('QMetry client construction failed: %s' % exc)

    try:
        resp = client.session.get(client._url('/projects'), timeout=client.timeout)
        resp.raise_for_status()
        records = resp.json()
    except Exception as exc:
        raise QMetryFetchError('project list (/projects) failed: %s' % exc)

    if isinstance(records, dict):
        records = records.get('data', records.get('projects', [])) or []
    index: Dict[str, str] = {}
    for rec in (records or []):
        if not isinstance(rec, dict):
            continue
        key = rec.get('key')
        pid_val = rec.get('id')
        if key is None or pid_val is None:
            continue
        index[str(key).strip().upper()] = str(pid_val)

    log('[QMETRY] dynamic project index loaded: %d project(s) from /projects' % len(index))
    _PROJECT_INDEX = index
    return index


def resolve_project_dynamic(jira_key: str, cookies, log=print) -> Optional[str]:
    """Resolve a Jira key's project to a QMetry projectId via the dynamic ``/projects``
    index (primary path). Returns the projectId or ``None`` when the prefix has no
    matching QMetry project. Raises ``QMetryFetchError`` (fail-loud) if ``/projects``
    cannot be reached.
    """
    prefix = _key_prefix(jira_key)
    if not prefix:
        return None
    return _load_project_index(cookies, log).get(prefix)


def resolve_project_for_feature(feature_key: str, subtask_keys=None,
                                cookies=None, log=print) -> Optional[str]:
    """Resolve the QMetry projectId for a feature — SUBTASK-FIRST and DYNAMIC-first.

    "Always go with the Jira issue": the authored QMetry test cases live under a
    feature's linked SUBTASK (e.g. ``MWTGPROV-4074`` → subtask ``NSLNM-759`` →
    QMetry project 121405; ``MWTGPROV-4628`` → subtask ``MWTGMED-5214`` → 76000),
    so each valid subtask key is tried BEFORE the feature key.

    Per-key resolution order (D2): dynamic ``/projects`` key-match first, then the
    static ``QMETRY_PROJECT_MAP`` fallback. Returns the first hit, else ``None``
    (skip, not an error).

    When ``cookies`` is ``None`` the dynamic step is skipped and only the static
    table is consulted — this keeps hermetic unit tests (that do not exercise the
    network) deterministic.
    """
    ordered = list(_valid_keys(subtask_keys or [])) + [feature_key]
    for k in ordered:
        if not k:
            continue
        if cookies is not None:
            pid = resolve_project_dynamic(k, cookies, log)
            if pid is not None:
                return pid
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


def qmetry_session_status(log=None) -> Dict[str, Any]:
    """Non-raising QMetry auth probe (dashboard pre-run guard).

    Wraps ``load_qmetry_cookies`` — same candidate list, same probe — and returns
    ``{'ok': bool, 'reason': str, 'source': Optional[str], 'checked_at': float}``.
    ``ok=True`` → ``reason=''`` and ``source`` is the authenticating storage_state path.
    A ``QMetrySessionError`` → ``ok=False`` with its message as ``reason``; any other
    exception → ``ok=False`` with ``reason='probe error: <exc>'``. Never raises.
    """
    lines: List[str] = []

    def _log(msg):
        lines.append(str(msg))
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    status: Dict[str, Any] = {'ok': False, 'reason': '', 'source': None,
                              'checked_at': time.time()}
    try:
        load_qmetry_cookies(log=_log)
        status['ok'] = True
        prefix = '[QMETRY] authenticated via '
        for ln in lines:
            if ln.startswith(prefix):
                status['source'] = ln[len(prefix):].strip()
    except QMetrySessionError as exc:
        status['reason'] = str(exc)
    except Exception as exc:
        status['reason'] = 'probe error: %s' % exc
    status['checked_at'] = time.time()
    return status


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
_FOLDER_MEMO: Dict[tuple, Optional[str]] = {}                # (pid, candidate_key) → folder_id
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
    """Resolve the QMetry folder id for a subtask within the (already-resolved) project.

    Matching is TIERED so a truncated QMetry folder name still resolves (D4). The
    project id is already pinned to the correct QMetry project before this runs, so
    the numeric pass is safely scoped and cannot collide across projects:

      PASS 1 — full-key containment: a folder whose name contains a full validated
        candidate key (e.g. ``MWTGMED-5214`` → folder ``MWTGMED-5214`` id 129316).
      PASS 2 — truncated-name / issue-number match: for a candidate of shape
        ``<PREFIX>-<NUM>``, a folder whose name contains ``<NUM>`` with a non-digit
        boundary (e.g. ``NSLNM-759`` → folder ``NM-759`` id 128648). The digit
        boundary prevents matching ``-7591`` or a different number embedded in
        another key. Scoped to the resolved project, so ``MWTGMED-3759`` in another
        project can never be reached here.

    PASS 1 runs first, so an exact full-key folder always wins and MWTGMED keeps
    resolving its ``MWTGMED-5214`` folder exactly.
    """
    try:
        folders = client.get_testcase_folders()
    except Exception as exc:
        raise QMetryFetchError('folder list failed: %s' % exc)
    flist = folders if isinstance(folders, list) else (folders.get('data', folders)
                                                       if isinstance(folders, dict) else [])
    nodes = [n for n in _iter_folders(flist) if str(n.get('name', '') or '')]

    # PASS 1 — full-key containment (exact behavior preserved; MWTGMED-5214 wins here).
    keys_lower = [k.lower() for k in candidate_keys]
    for node in nodes:
        name = str(node.get('name', '') or '').lower()
        if any(k in name for k in keys_lower):
            fid = node.get('id')
            if fid is not None:
                log('[QMETRY] folder resolved (full-key): id=%s (%s)'
                    % (fid, node.get('name')))
                return str(fid)

    # PASS 2 — truncated folder name: match the issue NUMBER with a digit boundary so
    # a folder like "NM-759" resolves for "NSLNM-759" without matching "-7591".
    num_res = []
    for k in candidate_keys:
        m = re.match(r'^[A-Z][A-Z0-9]+-(\d+)$', k)
        if m:
            num = m.group(1)
            num_res.append((k, num, re.compile(r'(?<!\d)' + re.escape(num) + r'(?!\d)')))
    for node in nodes:
        name = str(node.get('name', '') or '')
        for key, num, pat in num_res:
            if pat.search(name):
                fid = node.get('id')
                if fid is not None:
                    log('[QMETRY] folder resolved (truncated-name, issue #%s for %s): '
                        'id=%s (%s)' % (num, key, fid, node.get('name')))
                    return str(fid)
    return None


def _resolve_folders_for_keys(client, project_id, candidate_keys, log) -> List[str]:
    """Resolve EVERY candidate key's own QMetry folder within the (already-resolved)
    project and return an ordered, de-duplicated list of folder ids — never stopping at
    the first match (FEAT-002).

    Each key is resolved INDEPENDENTLY through the single-key ``_resolve_folder_id`` (the
    exact tiered PASS 1 full-key / PASS 2 truncated-number logic), constrained to that one
    key so one key's folder can never be matched via another key's number. The resolution
    is memoized per ``(project_id, candidate_key)`` (D3). The returned order follows
    ``candidate_keys``; a folder reached by two keys appears once.
    """
    folder_ids: List[str] = []
    for key in candidate_keys:
        memo_key = (str(project_id), key)
        if memo_key in _FOLDER_MEMO:
            fid = _FOLDER_MEMO[memo_key]
        else:
            # Constrain resolution to THIS key only so a different key's folder cannot win.
            fid = _resolve_folder_id(client, project_id, [key], log)
            _FOLDER_MEMO[memo_key] = fid
        if fid and fid not in folder_ids:
            folder_ids.append(fid)
    return folder_ids


def _export_and_parse_folder(client, project_id, folder_id, log) -> Dict[str, Dict[str, Any]]:
    """Export + parse ONE (project_id, folder_id), honouring the run + 6h disk caches.

    Raises ``QMetryFetchError`` (fail-loud) on export/parse failure — unchanged from the
    single-folder path; the merge above simply calls this once per distinct folder.
    """
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


def fetch_authored_steps(project_id, feature_key, subtask_keys, cookies=None,
                         log=print) -> Dict[str, Dict[str, Any]]:
    """Fetch + parse authored QMetry steps for a feature within ONE QMetry project.

    Returns ``{tc_key: {'summary', 'story_linkages': [..], 'steps': [...],
    'step_summaries': [..]}}``. Resolves EVERY candidate key's own folder within
    ``project_id`` and MERGES all parsed TCs into one dict de-duped by QMetry TC key
    (later folders never overwrite a TC key already present). Returns ``{}`` (graceful
    skip) when no candidate key is valid or no folder matches. Raises
    ``QMetryFetchError`` on export / parse failure.

    A single-key feature resolves exactly one folder and exports once, so the result is
    identical to the pre-FEAT-002 single-folder behavior. Cross-PROJECT merging is handled
    one level up by ``fetch_authored_steps_multi``.

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

    # Resolve EVERY key's folder within this project (ordered, de-duped) — never stop at
    # the first (FEAT-002). Memoized per (project_id, candidate_key).
    folder_ids = _resolve_folders_for_keys(client, project_id, candidate_keys, log)
    if not folder_ids:
        log('[QMETRY] no folder matched candidate keys %s — skipping' % candidate_keys)
        return {}

    # Export each distinct folder exactly once and MERGE parsed TCs de-duped by TC key:
    # a TC key already present is NOT overwritten by a later folder.
    merged: Dict[str, Dict[str, Any]] = {}
    for folder_id in folder_ids:
        parsed = _export_and_parse_folder(client, project_id, folder_id, log)
        for tc_key, tc in parsed.items():
            if tc_key not in merged:
                merged[tc_key] = tc
    return merged


def fetch_authored_steps_multi(project_keys, feature_key, cookies=None,
                               log=print) -> Dict[str, Dict[str, Any]]:
    """Fetch + parse authored QMetry steps across MULTIPLE QMetry projects and MERGE.

    ``project_keys`` is an ordered mapping ``{project_id: [candidate_key, ...]}`` — every
    candidate key already resolved to its OWN QMetry project. For each project this calls
    ``fetch_authored_steps`` (which resolves + exports EVERY folder in that project and
    merges its folders), then merges the per-project dicts into one, de-duped by QMetry TC
    key (an earlier project's TC key is never overwritten by a later one). The feature key
    is appended to every project's key list so a feature-named folder still resolves.

    Returns ``{}`` (graceful skip) when no project/key resolves. ``QMetryFetchError`` from
    any project's export/parse is propagated (fail-loud).
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for project_id, keys in (project_keys or {}).items():
        parsed = fetch_authored_steps(project_id, feature_key, list(keys or []),
                                      cookies=cookies, log=log)
        for tc_key, tc in parsed.items():
            if tc_key not in merged:
                merged[tc_key] = tc
    return merged


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


def _infer_qmetry_category(summary: str) -> str:
    """Infer a TC category from a QMetry TC summary.

    Mapping (all outputs are members of the engine's _VALID_CATEGORIES):
      * 'regression' / 'no impact'                         → 'Regression'
      * 'end to end' / 'end-to-end' / 'e2e', or the mixed-
        file phrase 'both … in the same file'             → 'E2E'
      * otherwise                                          → 'Happy Path'
    """
    low = (summary or '').lower()
    if 'regression' in low or 'no impact' in low:
        return 'Regression'
    if ('end to end' in low or 'end-to-end' in low or 'e2e' in low
            or ('both ' in low and 'in the same file' in low)):
        return 'E2E'
    return 'Happy Path'


def _build_new_scenario_from_tc(tc_key: str, tc: Dict[str, Any]):
    """Build a NEW ExtractedScenario from an eligible-but-unmatched QMetry TC.

    The returned scenario carries the authored summary as its title/validation, the
    authored step summaries as ``steps_hint`` and the authored last-step Expected
    Result as ``_qmetry_expected`` — IDENTICAL to what ``_apply_match`` attaches for a
    matched TC — so the existing ``_build_scenario_tc`` path turns it into a TC with the
    real QA steps, interior/last expecteds, summary, precondition, sno and category.
    ``user_requested=True`` protects this authored ground-truth from near-dup collapse.
    """
    summary = (tc.get('summary') or '').strip()
    locator = (tc_key or '').strip()
    # create_traceability requires non-empty extracted_text/source_id — fall back to the
    # TC key so a blank summary can never raise.
    title = summary or locator
    source = create_traceability(
        source_type='QMetry Test Case',
        source_id=locator or title,
        extracted_text=summary or locator or title,
        confidence=1.0,
    )
    sc = ExtractedScenario(
        title=title,
        validation=title,
        category=_infer_qmetry_category(summary),
        source=source,
        steps_hint=list(_authored_hint_list(tc)),
        obligations=[],
    )
    sc._qmetry_expected = _authored_expected(tc)
    sc.user_requested = True
    return sc


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


def attach_steps_to_scenarios(scenarios, authored, log=print, validated_keys=None):
    """Attach authored QMetry steps onto the correctly-matched ExtractedScenario,
    and build NEW scenarios for eligible-but-unmatched TCs.

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

      TIER 3 — ADD-AS-NEW-TC: an ELIGIBLE TC that matched no obligation and cleared no
        similarity bar is NOT dropped — a new ExtractedScenario is built from its
        authored summary + steps and returned to the caller to append AFTER the existing
        (Chalk-derived) scenarios. A true DROP only remains for INELIGIBLE TCs (they fail
        the story-linkage pre-filter and never reach any tier).

    Each existing scenario is enriched at most once; matched TCs only ENRICH and are NEVER
    re-added as new TCs. ``obligations`` are never cleared. Returns
    ``(enrichment_count, new_scenarios)`` where ``new_scenarios`` is the ordered list of
    ADD-AS-NEW-TC scenarios the caller must append to the live scenario list.
    """
    scenarios = list(scenarios or [])
    validated_keys = validated_keys or []
    enriched_ids = set()          # id() of scenarios already enriched
    claimed_obligations = set()   # (input, output) pairs already bound to a TC
    matched_tc_keys = set()       # TC keys that ENRICHED a scenario (tier 1/2) → not re-added
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
            matched_tc_keys.add(tc_key)
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
            matched_tc_keys.add(tc_key)
            count += 1

    # ── TIER 3: ADD-AS-NEW-TC. Every ELIGIBLE TC that never enriched a scenario
    #    (not in matched_tc_keys) becomes its own new scenario, built from its authored
    #    summary + steps, returned for the caller to append AFTER the existing scenarios
    #    (keeps Chalk-first ordering). Ineligible TCs never reach here (dropped above). ──
    # Build the feature's content vocabulary from the EXISTING (Chalk-derived) scenarios so
    # the relevance gate can require a positive overlap before promoting a TC (decision 6).
    feature_vocab = _feature_vocabulary(scenarios)
    new_scenarios = []
    for tc_key, tc in eligible:
        if tc_key in matched_tc_keys:
            continue
        if ExtractedScenario is None or create_traceability is None:
            log('[QMETRY] TC %s unmatched but new-TC builder unavailable — skipped' % tc_key)
            continue
        verdict, score = _tier3_relevance(tc, feature_vocab)
        new_sc = _build_new_scenario_from_tc(tc_key, tc)
        if verdict == 'flag':
            setattr(new_sc, 'needs_review', True)
            setattr(new_sc, 'review_reason',
                    'QMetry-only TC: low relevance to feature scope '
                    '(overlap=%.2f) — kept, flagged for review' % score)
            log('[QMETRY] TC %s unmatched, low relevance (overlap=%.2f) — '
                'FLAGGED FOR REVIEW, kept as NEW test case' % (tc_key, score))
        else:
            log('[QMETRY] TC %s unmatched, relevant (overlap=%.2f) — added as NEW test case '
                '(category=%s, %d steps)'
                % (tc_key, score, new_sc.category, len(new_sc.steps_hint)))
        new_scenarios.append(new_sc)

    return count, new_scenarios


# ── Tier-3 relevance gate (decision 6) ─────────────────────────────────────────

# Thresholds tuned so the 4628 QMetry-only set (TC10-TC17) survives: these QMetry TCs
# share the mediation vocabulary (PRR, MMS_TYPE_INDICATOR, call_direction_ind, Amdocs,
# BAU, VZW/CBRS), so even a loose overlap clears the KEEP bar. Provenance plus authored
# steps still PROMOTE the TC — the gate NEVER silently drops an eligible TC (design §9:
# default to flag-for-review, not hard-drop). A low-overlap/off-topic TC is kept as a NEW
# test case BUT flagged needs_review so a human decides, instead of being promoted silently.
_TIER3_KEEP = 0.12    # >= keep outright (relevant); below this = keep but flag for review

_STOPWORDS = frozenset((
    'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'for', 'is', 'are', 'be', 'with',
    'that', 'this', 'it', 'as', 'on', 'at', 'by', 'from', 'verify', 'validate', 'check',
    'confirm', 'ensure', 'test', 'case', 'tc', 'when', 'then', 'should', 'must', 'will',
    'value', 'values', 'field', 'fields', 'record', 'records', 'file', 'files', 'tmo',
))


def _content_tokens(text: str) -> set:
    toks = re.sub(r'[^a-z0-9_]', ' ', (text or '').lower()).split()
    return {t for t in toks if len(t) > 2 and t not in _STOPWORDS}


def _feature_vocabulary(scenarios) -> set:
    """Collect content tokens the feature is 'about' from its Chalk-derived scenarios:
    titles, validations and every obligation field/value. Used by the relevance gate.
    """
    vocab = set()
    for sc in (scenarios or []):
        vocab |= _content_tokens(getattr(sc, 'title', '') or '')
        vocab |= _content_tokens(getattr(sc, 'validation', '') or '')
        for ob in (getattr(sc, 'obligations', None) or []):
            for k in ('input_field', 'input_value', 'expected_field', 'expected_value',
                      'condition'):
                vocab |= _content_tokens(ob.get(k) if isinstance(ob, dict) else '')
    return vocab


def _tier3_relevance(tc: Dict[str, Any], feature_vocab: set):
    """Return (verdict, overlap_score) for promoting an unmatched eligible QMetry TC.

    overlap_score = |candidate_tokens ∩ feature_vocab| / |candidate_tokens|, computed over
    the TC summary + authored step summaries. verdict: 'keep' | 'flag'.

    The gate NEVER drops: an eligible-but-unmatched TC is always promoted as a NEW test
    case (provenance + authored steps earned it a place). An on-topic TC (overlap at/above
    the KEEP bar) is promoted cleanly; a low-overlap or off-topic TC is still promoted but
    tagged 'flag' so it carries a needs_review marker for a human to confirm — relevance is
    advisory, not a silent delete (design §9). When the feature exposes no vocabulary the
    gate cannot judge, so it also defaults to 'flag'.
    """
    cand = _content_tokens(tc.get('summary', '') or '')
    for s in (tc.get('step_summaries') or []):
        cand |= _content_tokens(s or '')
    if not cand:
        return 'flag', 0.0
    if not feature_vocab:
        return 'flag', 0.0
    overlap = len(cand & feature_vocab) / len(cand)
    if overlap >= _TIER3_KEEP:
        return 'keep', overlap
    return 'flag', overlap


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
    # FEAT-002: the related-key set is jira.subtasks (now carrying the epic children
    # captured by FEAT-001) AND jira.linked_issues — not subtasks alone. Build the ordered
    # union (subtasks first, then links; de-duped, blanks/non-key shapes dropped) so every
    # related key gets its own QMetry project+folder resolved and merged.
    link_keys = [k for l in (getattr(jira, 'linked_issues', None) or [])
                 if isinstance(l, dict) and (k := (l.get('key') or '').strip())]
    related_keys: List[str] = []
    for k in _valid_keys(subtask_keys + link_keys):
        if k not in related_keys:
            related_keys.append(k)

    # Load the authenticated cookies FIRST (before any project decision) so a dead
    # session fails LOUD via QMetrySessionError (preserved fail-loud), and so the
    # dynamic /projects resolver below runs through the same authenticated session.
    cookies = load_qmetry_cookies(log=log)   # raises QMetrySessionError if none authenticate

    # Resolve EACH related key (plus the feature key as a fallback) to its OWN QMetry
    # project and GROUP keys by project, preserving first-seen order. One key's cases can
    # then never be fetched from another key's project. Per-key order: dynamic /projects
    # key-match first, then the static QMETRY_PROJECT_MAP fallback (resolve_project_id).
    project_keys: Dict[str, List[str]] = {}
    for k in related_keys + [feature_key]:
        if not k:
            continue
        pid = None
        if cookies is not None:
            pid = resolve_project_dynamic(k, cookies, log)
        if pid is None:
            pid = resolve_project_id(k)
        if pid is None:
            continue
        project_keys.setdefault(str(pid), [])
        if k not in project_keys[str(pid)]:
            project_keys[str(pid)].append(k)

    if not project_keys:
        log('[QMETRY] feature %s (related keys %s) not mapped to any QMetry project — skipping'
            % (feature_key, related_keys))
        return 0

    authored = fetch_authored_steps_multi(project_keys, feature_key, cookies=cookies, log=log)

    # ── TRANSPARENCY: per-QMetry-project queried/eligible rollup ──
    # A linked issue whose folder resolved and was queried but whose cases are all
    # filtered by the story-linkage gate must be VISIBLE (not silently contributing 0).
    # Group the queried TCs by their QMetry key prefix (e.g. MWTGNBOP, NSLNM) and count
    # how many are story-link-eligible for THIS feature. Any prefix that returned cases
    # but 0 eligible is surfaced to the suite warnings (via the same options convention
    # the auth-skip path already uses) so the user sees WHY a linked issue added nothing.
    _validated = _valid_keys(related_keys)
    _queried: Dict[str, int] = {}
    _eligible_ct: Dict[str, int] = {}
    _ineligible_tcs: List[str] = []
    for _tck, _tc in (authored or {}).items():
        _pfx = _tck.split('-TC-')[0] if '-TC-' in _tck else (_tck.split('-')[0] or _tck)
        _queried[_pfx] = _queried.get(_pfx, 0) + 1
        if _authored_hint_list(_tc) and _story_linkage_eligible(
                _tc.get('story_linkages') or [], _validated):
            _eligible_ct[_pfx] = _eligible_ct.get(_pfx, 0) + 1
        else:
            _ineligible_tcs.append(_tck)
    for _pfx in sorted(_queried):
        log('[QMETRY] project %s: %d case(s) queried, %d story-link-eligible for %s'
            % (_pfx, _queried[_pfx], _eligible_ct.get(_pfx, 0), feature_key))
    # Surface EVERY queried-but-filtered case to the suite warnings (not only when a whole
    # QMetry project contributes zero) — a single Jira issue (e.g. MWTGNBOP-5791) can share
    # a QMetry project with another (MWTGNBOP-5792), so a project-level zero check would
    # hide it. Listing the excluded TC keys makes the exclusion unambiguous and visible.
    # NOTE: a precise per-ORIGINATING-Jira-key rollup (5791 vs 5792) would need the
    # folder->key association threaded through the export/merge; that finer attribution is
    # a follow-up. The per-TC 'not story-linked' log lines above already name each one.
    if _ineligible_tcs:
        _w = ('[QMETRY] %d QMetry case(s) fetched + folder-resolved but excluded from %s '
              '(not story-linked to this feature): %s'
              % (len(_ineligible_tcs), feature_key, ', '.join(sorted(_ineligible_tcs))))
        try:
            _existing = options.get('_qmetry_ineligible_warning') or ''
            options['_qmetry_ineligible_warning'] = (
                (_existing + ' | ' + _w) if _existing else _w)
        except Exception:
            pass

    n, new_scenarios = attach_steps_to_scenarios(
        scenarios, authored, log=log, validated_keys=_validated)
    # Append the ADD-AS-NEW-TC scenarios to the LIVE scenario list the engine passed in
    # (dimension_set.scenarios), AFTER the existing Chalk-derived scenarios — the engine's
    # plan/build path then emits their TCs in sequence after the Chalk-derived ones.
    if new_scenarios:
        scenarios.extend(new_scenarios)
    log('[QMETRY] authored steps attached to %d scenario(s)'
        '%s' % (n, (' and %d new QMetry-only test case(s) added' % len(new_scenarios))
                if new_scenarios else ''))
    return n
