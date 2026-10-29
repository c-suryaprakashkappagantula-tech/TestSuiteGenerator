# -*- coding: utf-8 -*-
"""
registry.py — SQLite-backed protected-identifier registry (the do-not-touch list).

All access goes through this module so every dashboard shares ONE database file
(protected_entities.db, stored next to this file). Thread/process safe enough for
our use: each call opens a short-lived connection.
"""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ── DB location: next to this module so ALL dashboards resolve the same file ──
_DB_PATH = Path(__file__).resolve().parent / 'protected_entities.db'
_LOCK = threading.Lock()

# Known identifier types (metadata only — matching is value-based across all types).
ENTITY_TYPES = ('MDN', 'IMEI', 'ICCID', 'IMSI', 'EID', 'ACCOUNT', 'DEVICE', 'OTHER')

# Which context/kwarg field names map to which entity type (for assert_safe/check).
_FIELD_ALIASES = {
    'mdn': 'MDN', 'msisdn': 'MDN', 'phone': 'MDN', 'phone_number': 'MDN',
    'imei': 'IMEI', 'imei1': 'IMEI', 'new_imei': 'IMEI', 'old_imei': 'IMEI', 'device': 'DEVICE',
    'iccid': 'ICCID', 'new_iccid': 'ICCID', 'old_iccid': 'ICCID', 'sim': 'ICCID',
    'imsi': 'IMSI',
    'eid': 'EID',
    'account': 'ACCOUNT', 'account_number': 'ACCOUNT', 'accountnumber': 'ACCOUNT',
}


class ProtectedEntityError(Exception):
    """Raised when a run tries to use/target a protected (do-not-touch) identifier."""

    def __init__(self, violations: List['ProtectedEntity'], context: str = ''):
        self.violations = violations
        lines = [f'  • {v.entity_type} {v.value} — {v.reason or "protected"}' for v in violations]
        msg = ('BLOCKED: attempt to use protected (do-not-touch) identifier(s)'
               + (f' in {context}' if context else '') + ':\n' + '\n'.join(lines)
               + '\nThese are registered as DO-NOT-TOUCH and must not be used in ANY environment.')
        super().__init__(msg)


@dataclass
class ProtectedEntity:
    entity_type: str = 'OTHER'
    value: str = ''
    value_norm: str = ''
    reason: str = ''
    note: str = ''
    added_by: str = ''
    added_at: str = ''

    def as_dict(self) -> dict:
        return {
            'entity_type': self.entity_type, 'value': self.value, 'value_norm': self.value_norm,
            'reason': self.reason, 'note': self.note, 'added_by': self.added_by,
            'added_at': self.added_at,
        }


# ── Normalization ──────────────────────────────────────────────────────────

def _normalize(value) -> str:
    """Normalize a value for matching.

    Numeric ids (MDN/IMEI/ICCID/IMSI/EID) -> digits only.
    Everything else -> upper-cased, stripped.
    """
    s = str(value if value is not None else '').strip()
    if not s:
        return ''
    # Strip common phone/id formatting (spaces, dashes, parens, dots, plus). If what remains is
    # all digits and long enough, it's a numeric identifier (MDN/IMEI/ICCID/IMSI/EID).
    cleaned = re.sub(r'[\s\-\(\)\.\+]', '', s)
    if cleaned.isdigit() and len(cleaned) >= 7:
        return cleaned
    return s.upper()


def _match_variants(norm: str) -> set:
    """Return equivalent forms of a normalized numeric id (US country-code tolerant).

    So 4145810607 also matches 14145810607 and vice-versa.
    """
    variants = {norm}
    if norm.isdigit():
        if len(norm) == 11 and norm.startswith('1'):
            variants.add(norm[1:])
        elif len(norm) == 10:
            variants.add('1' + norm)
    return variants


def _detect_type(value) -> str:
    """Best-effort auto-detect of an identifier type from its shape."""
    s = str(value or '').strip()
    digits = re.sub(r'\D', '', s)
    if not digits or len(re.sub(r'\d', '', s)) > 2:
        return 'ACCOUNT' if s[:3].upper() == 'ACC' else 'OTHER'
    n = len(digits)
    if n in (10, 11):
        return 'MDN'
    if n == 15:
        return 'IMEI'      # (IMSI is also 15; default to IMEI — matching is value-based anyway)
    if n in (19, 20):
        return 'ICCID'
    if n == 32:
        return 'EID'
    return 'OTHER'


# ── DB plumbing ──────────────────────────────────────────────────────────────

@contextmanager
def _connect():
    with _LOCK:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            _ensure_schema(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protected_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            value TEXT NOT NULL,
            value_norm TEXT NOT NULL,
            reason TEXT,
            note TEXT,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(value_norm)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pe_norm ON protected_entities(value_norm)")


def db_path() -> str:
    """Absolute path to the shared registry DB (for diagnostics)."""
    return str(_DB_PATH)


# ── Public API ────────────────────────────────────────────────────────────────

def add_protected(value, entity_type: str = 'AUTO', reason: str = '',
                  note: str = '', added_by: str = '') -> ProtectedEntity:
    """Add (or update) a protected identifier. Idempotent on the normalized value."""
    value = str(value).strip()
    if not value:
        raise ValueError('value is required')
    norm = _normalize(value)
    if not norm:
        raise ValueError(f'could not normalize value: {value!r}')
    etype = (entity_type or 'AUTO').upper()
    if etype in ('', 'AUTO'):
        etype = _detect_type(value)
    if etype not in ENTITY_TYPES:
        etype = 'OTHER'
    added_by = added_by or os.environ.get('USERNAME') or os.environ.get('USER') or ''
    now = datetime.now().isoformat(timespec='seconds')
    with _connect() as conn:
        conn.execute("""
            INSERT INTO protected_entities (entity_type, value, value_norm, reason, note, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(value_norm) DO UPDATE SET
                entity_type=excluded.entity_type,
                value=excluded.value,
                reason=excluded.reason,
                note=excluded.note,
                added_by=excluded.added_by
        """, (etype, value, norm, reason, note, added_by, now))
    return ProtectedEntity(entity_type=etype, value=value, value_norm=norm,
                           reason=reason, note=note, added_by=added_by, added_at=now)


def remove_protected(value) -> bool:
    """Remove a protected identifier. Returns True if a row was deleted."""
    norm = _normalize(value)
    variants = _match_variants(norm)
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM protected_entities WHERE value_norm IN ({','.join('?' * len(variants))})",
            tuple(variants))
        return cur.rowcount > 0


def find_protected(value) -> Optional[ProtectedEntity]:
    """Return the matching ProtectedEntity for a value, or None if not protected."""
    norm = _normalize(value)
    if not norm:
        return None
    variants = _match_variants(norm)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM protected_entities WHERE value_norm IN ({','.join('?' * len(variants))}) LIMIT 1",
            tuple(variants)).fetchone()
    if not row:
        return None
    return ProtectedEntity(entity_type=row['entity_type'], value=row['value'],
                           value_norm=row['value_norm'], reason=row['reason'] or '',
                           note=row['note'] or '', added_by=row['added_by'] or '',
                           added_at=str(row['added_at'] or ''))


def is_protected(value) -> bool:
    """True if the value is in the do-not-touch registry (any type)."""
    if value is None or str(value).strip() == '':
        return False
    return find_protected(value) is not None


def check_identifiers(**identifiers) -> List[ProtectedEntity]:
    """Check multiple named identifiers; return the list of protected matches (empty = safe).

    Example: check_identifiers(mdn='4145810607', imei='35...', iccid='89...')
    """
    hits: List[ProtectedEntity] = []
    seen = set()
    for _field, val in identifiers.items():
        if val is None or str(val).strip() == '':
            continue
        rec = find_protected(val)
        if rec and rec.value_norm not in seen:
            seen.add(rec.value_norm)
            hits.append(rec)
    return hits


def assert_safe(context: str = '', logger_fn=None, **identifiers) -> None:
    """Raise ProtectedEntityError if ANY given identifier is protected.

    Call this at the start of any run/operation. Example:
        assert_safe(context='E2E Lifecycle', mdn=ctx.mdn, imei=ctx.imei, iccid=ctx.iccid)
    """
    hits = check_identifiers(**identifiers)
    if hits:
        err = ProtectedEntityError(hits, context=context)
        if logger_fn:
            try:
                logger_fn('[PROTECTED] ' + str(err))
            except Exception:
                pass
        raise err


def filter_safe(values) -> List[str]:
    """Return only the values that are NOT protected (order preserved)."""
    out = []
    for v in values:
        if not is_protected(v):
            out.append(v)
    return out


def list_protected() -> List[dict]:
    """Return all protected entries as dicts (newest first)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM protected_entities ORDER BY added_at DESC, id DESC").fetchall()
    return [dict(r) for r in rows]
