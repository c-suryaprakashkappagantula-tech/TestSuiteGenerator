# -*- coding: utf-8 -*-
"""
pi_registry_local.py - Vendored, self-contained PI registry for standalone TSG.

WHY THIS EXISTS: the canonical registry lives in the monorepo's `shared/pi_registry.py`,
reached at runtime via a sibling-folder path. When TSG is deployed ALONE to a UAT/VDI/
Docker box (cloned without the parent `shared/` folder), that import isn't available.
This vendored copy keeps TSG's PI list working with ZERO external dependency.

It is API-compatible with shared.pi_registry (upsert_pi_pages, get_pi_pages, list_pis,
latest_pi, refresh, as_pi_label, as_decimal, pi_number, registry_path) so TSG can try
the shared module first and transparently fall back to this one.

CANONICAL DB LOCATION (deployment-safe precedence):
  1. env var PI_REGISTRY_DB                    (explicit, for UAT/VDI/Docker)
  2. <monorepo>/shared/pi_registry.db          (if the shared folder happens to be present,
                                                so a co-located install stays in sync)
  3. <TSG repo>/pi_registry.db                 (standalone default - always writable)

READ PRECEDENCE (fail-safe, never blank):
  registry DB -> TSG tsg_cache.db (features.pi_label) -> generated range.
"""
import os
import re
import sqlite3
from datetime import datetime
from functools import lru_cache
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../TestSuiteGenerator/modules
_TSG_ROOT = os.path.abspath(os.path.join(_HERE, '..'))      # .../TestSuiteGenerator
_MONO_ROOT = os.path.abspath(os.path.join(_TSG_ROOT, '..')) # .../mcp-jira-server (maybe absent)

_TSG_CACHE_DB = os.path.join(_TSG_ROOT, 'tsg_cache.db')


def _resolve_registry_db() -> str:
    env = os.environ.get('PI_REGISTRY_DB', '').strip()
    if env:
        return env
    shared_db = os.path.join(_MONO_ROOT, 'shared', 'pi_registry.db')
    shared_dir = os.path.join(_MONO_ROOT, 'shared')
    # Use the shared DB only if the shared folder actually exists (co-located install).
    if os.path.isdir(shared_dir):
        return shared_db
    return os.path.join(_TSG_ROOT, 'pi_registry.db')


_REGISTRY_DB = _resolve_registry_db()

_FALLBACK_START = 46
_FALLBACK_END = 60
_LABEL_RE = re.compile(r'(?:PI[-_\s]*)?(\d+)(?:\.(\d+))?', re.IGNORECASE)
_DEFAULT_MINOR = 2
_DEFAULT_BASE_URL = 'https://chalk.charter.com/spaces/MDA/pages'
_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS pi_pages ("
    "label TEXT PRIMARY KEY, page_id TEXT, url TEXT, base_url TEXT, last_synced TEXT)"
)


def pi_number(value):
    if value is None:
        return None
    m = _LABEL_RE.search(str(value).strip())
    return int(m.group(1)) if m else None


def as_pi_label(value) -> str:
    n = pi_number(value)
    return ('PI-%d' % n) if n is not None else str(value)


def as_decimal(value, minor: int = _DEFAULT_MINOR) -> str:
    if value is None:
        return ''
    m = _LABEL_RE.search(str(value).strip())
    if not m:
        return str(value)
    n = int(m.group(1))
    mn = int(m.group(2)) if m.group(2) is not None else minor
    return '%d.%d' % (n, mn)


def _sort_key(label: str):
    n = pi_number(label)
    return (0, n) if n is not None else (1, 0)


def _page_id_from_url(url: str) -> str:
    if not url:
        return ''
    m = re.search(r'/pages/(\d+)', str(url))
    return m.group(1) if m else ''


def _connect(readonly: bool = False):
    if readonly:
        if not os.path.exists(_REGISTRY_DB):
            raise FileNotFoundError(_REGISTRY_DB)
        return sqlite3.connect('file:%s?mode=ro' % _REGISTRY_DB, uri=True, timeout=5)
    con = sqlite3.connect(_REGISTRY_DB, timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    return con


def _ensure_schema(con) -> None:
    con.execute(_SCHEMA_SQL)
    con.commit()


def upsert_pi_pages(pi_list, base_url: str = _DEFAULT_BASE_URL) -> int:
    try:
        rows = []
        for item in (pi_list or []):
            if isinstance(item, dict):
                label = item.get('label') or item.get('pi_label') or ''
                url = item.get('url') or ''
                page_id = str(item.get('page_id') or _page_id_from_url(url))
            else:
                label = item[0] if len(item) > 0 else ''
                url = item[1] if len(item) > 1 else ''
                page_id = _page_id_from_url(url)
            label = as_pi_label(label)
            if not label:
                continue
            rows.append((label, page_id, url, base_url))
        if not rows:
            return 0
        con = _connect()
        try:
            _ensure_schema(con)
            now = datetime.now().isoformat()
            for label, page_id, url, burl in rows:
                con.execute(
                    'INSERT INTO pi_pages (label, page_id, url, base_url, last_synced) '
                    'VALUES (?,?,?,?,?) '
                    'ON CONFLICT(label) DO UPDATE SET '
                    'page_id=excluded.page_id, url=excluded.url, '
                    'base_url=excluded.base_url, last_synced=excluded.last_synced',
                    (label, page_id, url, burl, now))
            con.commit()
        finally:
            con.close()
        refresh()
        return len(rows)
    except Exception:
        return 0


def get_pi_pages() -> List[Tuple[str, str]]:
    try:
        con = _connect(readonly=True)
        try:
            rows = con.execute('SELECT label, url FROM pi_pages').fetchall()
        finally:
            con.close()
        pairs = [(as_pi_label(r[0]), r[1] or '') for r in rows if r and r[0]]
        return sorted(pairs, key=lambda p: _sort_key(p[0]))
    except Exception:
        return []


def _from_registry() -> List[str]:
    try:
        con = _connect(readonly=True)
        try:
            rows = con.execute(
                'SELECT label FROM pi_pages WHERE label IS NOT NULL AND label != ""'
            ).fetchall()
        finally:
            con.close()
        return [as_pi_label(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []


def _from_tsg_cache() -> List[str]:
    try:
        if not os.path.exists(_TSG_CACHE_DB):
            return []
        con = sqlite3.connect('file:%s?mode=ro' % _TSG_CACHE_DB, uri=True, timeout=5)
        try:
            rows = con.execute(
                'SELECT DISTINCT pi_label FROM features '
                'WHERE pi_label IS NOT NULL AND pi_label != ""'
            ).fetchall()
        finally:
            con.close()
        return [as_pi_label(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []


def _fallback() -> List[str]:
    return ['PI-%d' % n for n in range(_FALLBACK_START, _FALLBACK_END + 1)]


@lru_cache(maxsize=1)
def _cached_labels() -> tuple:
    labels = _from_registry()
    if not labels:
        labels = _from_tsg_cache()
    if not labels:
        labels = _fallback()
    uniq = sorted(set(labels), key=_sort_key)
    return tuple(uniq)


def list_pis(fmt: str = 'label', descending: bool = False) -> List[str]:
    labels = list(_cached_labels())
    if descending:
        labels = list(reversed(labels))
    if fmt == 'decimal':
        return [as_decimal(x) for x in labels]
    return labels


def latest_pi(fmt: str = 'label') -> str:
    pis = list_pis(fmt=fmt, descending=True)
    return pis[0] if pis else ''


def refresh():
    _cached_labels.cache_clear()


def registry_path() -> str:
    return _REGISTRY_DB
