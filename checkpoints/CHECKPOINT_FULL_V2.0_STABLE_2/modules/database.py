"""
database.py — SQLite persistence layer for TSG Dashboard.
Caches PI pages, features, Jira data, and generation history.
Zero setup — single .db file, ships with Python.
"""
import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from .config import ROOT

DB_PATH = ROOT / 'tsg_cache.db'
STALE_HOURS = 24  # data older than this shows a warning


def _conn():
    """Get a connection with WAL mode for concurrent reads."""
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create tables if they don't exist."""
    c = _conn()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS pi_pages (
            label       TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            last_fetched TEXT
        );

        CREATE TABLE IF NOT EXISTS features (
            feature_id  TEXT NOT NULL,
            pi_label    TEXT NOT NULL,
            title       TEXT,
            last_fetched TEXT,
            PRIMARY KEY (feature_id, pi_label)
        );

        CREATE TABLE IF NOT EXISTS jira_cache (
            feature_id      TEXT PRIMARY KEY,
            summary         TEXT,
            description     TEXT,
            status          TEXT,
            priority        TEXT,
            assignee        TEXT,
            reporter        TEXT,
            labels_json     TEXT,
            ac_text         TEXT,
            attachments_json TEXT,
            links_json      TEXT,
            subtasks_json   TEXT,
            comments_json   TEXT,
            pi              TEXT,
            channel         TEXT,
            raw_json        TEXT,
            last_fetched    TEXT
        );

        CREATE TABLE IF NOT EXISTS chalk_cache (
            feature_id      TEXT NOT NULL,
            pi_label        TEXT NOT NULL,
            scope           TEXT,
            rules           TEXT,
            scenarios_json  TEXT,
            open_items_json TEXT,
            raw_text        TEXT,
            tables_json     TEXT,
            last_fetched    TEXT,
            PRIMARY KEY (feature_id, pi_label)
        );

        CREATE TABLE IF NOT EXISTS generations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id  TEXT,
            pi          TEXT,
            tc_count    INTEGER,
            step_count  INTEGER,
            strategy    TEXT,
            file_path   TEXT,
            file_name   TEXT,
            status      TEXT DEFAULT 'SUCCESS',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS test_data_pool (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type   TEXT NOT NULL,
            value       TEXT NOT NULL,
            environment TEXT DEFAULT 'SIT',
            status      TEXT DEFAULT 'available',
            last_used   TEXT,
            notes       TEXT,
            UNIQUE(data_type, value)
        );
    ''')
    c.commit()
    c.close()


# ================================================================
# PI PAGES
# ================================================================

def save_pi_pages(pi_list: List[Tuple[str, str]]):
    """Save PI list: [(label, url), ...]"""
    c = _conn()
    now = datetime.now().isoformat()
    for label, url in pi_list:
        c.execute('INSERT OR REPLACE INTO pi_pages (label, url, last_fetched) VALUES (?,?,?)',
                  (label, url, now))
    c.commit(); c.close()


def load_pi_pages() -> List[Tuple[str, str]]:
    """Load PI list from DB. Returns [(label, url), ...] or empty list."""
    c = _conn()
    rows = c.execute('SELECT label, url FROM pi_pages ORDER BY label').fetchall()
    c.close()
    return [(r['label'], r['url']) for r in rows]


def get_pi_last_fetched() -> Optional[str]:
    """Get the oldest last_fetched timestamp across all PIs."""
    c = _conn()
    row = c.execute('SELECT MIN(last_fetched) as oldest FROM pi_pages').fetchone()
    c.close()
    return row['oldest'] if row else None


# ================================================================
# FEATURES (dropdown cache)
# ================================================================

def save_features(pi_label: str, features: List[Tuple[str, str]]):
    """Save features for a PI: [(feature_id, title), ...]"""
    c = _conn()
    now = datetime.now().isoformat()
    # Clear old features for this PI first
    c.execute('DELETE FROM features WHERE pi_label=?', (pi_label,))
    for fid, title in features:
        c.execute('INSERT OR REPLACE INTO features (feature_id, pi_label, title, last_fetched) VALUES (?,?,?,?)',
                  (fid, pi_label, title, now))
    c.commit(); c.close()


def load_features(pi_label: str) -> List[Tuple[str, str]]:
    """Load features for a PI. Returns [(feature_id, title), ...]."""
    c = _conn()
    rows = c.execute('SELECT feature_id, title FROM features WHERE pi_label=? ORDER BY feature_id',
                     (pi_label,)).fetchall()
    c.close()
    return [(r['feature_id'], r['title']) for r in rows]


def load_all_features() -> Dict[str, List[Tuple[str, str]]]:
    """Load ALL features grouped by PI. Returns {pi_label: [(fid, title), ...]}."""
    c = _conn()
    rows = c.execute('SELECT pi_label, feature_id, title FROM features ORDER BY pi_label, feature_id').fetchall()
    c.close()
    result = {}
    for r in rows:
        result.setdefault(r['pi_label'], []).append((r['feature_id'], r['title']))
    return result


def get_features_count() -> int:
    """Total features in DB."""
    c = _conn()
    row = c.execute('SELECT COUNT(*) as cnt FROM features').fetchone()
    c.close()
    return row['cnt']


# ================================================================
# JIRA CACHE
# ================================================================

def save_jira(jira_issue):
    """Cache a JiraIssue object."""
    c = _conn()
    c.execute('''INSERT OR REPLACE INTO jira_cache
        (feature_id, summary, description, status, priority, assignee, reporter,
         labels_json, ac_text, attachments_json, links_json, subtasks_json, comments_json,
         pi, channel, raw_json, last_fetched)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        jira_issue.key, jira_issue.summary, jira_issue.description,
        jira_issue.status, jira_issue.priority, jira_issue.assignee, jira_issue.reporter,
        json.dumps(jira_issue.labels), jira_issue.acceptance_criteria,
        json.dumps([{'filename': a.filename, 'url': a.url, 'size': a.size} for a in jira_issue.attachments]),
        json.dumps(jira_issue.linked_issues), json.dumps(jira_issue.subtasks),
        json.dumps(jira_issue.comments), jira_issue.pi, jira_issue.channel,
        json.dumps(jira_issue.raw_json) if jira_issue.raw_json else '{}',
        datetime.now().isoformat(),
    ))
    c.commit(); c.close()


def load_jira(feature_id: str) -> Optional[Dict]:
    """Load cached Jira data. Returns dict or None."""
    c = _conn()
    row = c.execute('SELECT * FROM jira_cache WHERE feature_id=?', (feature_id,)).fetchone()
    c.close()
    if not row:
        return None
    return dict(row)


def is_jira_stale(feature_id: str) -> bool:
    """Check if cached Jira data is older than STALE_HOURS."""
    c = _conn()
    row = c.execute('SELECT last_fetched FROM jira_cache WHERE feature_id=?', (feature_id,)).fetchone()
    c.close()
    if not row or not row['last_fetched']:
        return True
    try:
        fetched = datetime.fromisoformat(row['last_fetched'])
        return datetime.now() - fetched > timedelta(hours=STALE_HOURS)
    except:
        return True


# ================================================================
# CHALK CACHE
# ================================================================

def save_chalk(feature_id: str, pi_label: str, chalk_data):
    """Cache ChalkData for a feature+PI combination."""
    c = _conn()
    c.execute('''INSERT OR REPLACE INTO chalk_cache
        (feature_id, pi_label, scope, rules, scenarios_json, open_items_json,
         raw_text, tables_json, last_fetched)
        VALUES (?,?,?,?,?,?,?,?,?)''', (
        feature_id, pi_label, chalk_data.scope, chalk_data.rules,
        json.dumps([{
            'scenario_id': s.scenario_id, 'title': s.title, 'prereq': s.prereq,
            'cdr_input': s.cdr_input, 'derivation_rule': s.derivation_rule,
            'steps': s.steps, 'variations': s.variations,
            'validation': s.validation, 'category': s.category,
        } for s in chalk_data.scenarios]),
        json.dumps(chalk_data.open_items),
        chalk_data.raw_text[:50000] if chalk_data.raw_text else '',
        json.dumps(chalk_data.tables[:20]) if chalk_data.tables else '[]',
        datetime.now().isoformat(),
    ))
    c.commit(); c.close()


def load_chalk(feature_id: str, pi_label: str) -> Optional[Dict]:
    """Load cached Chalk data. Returns dict or None."""
    c = _conn()
    row = c.execute('SELECT * FROM chalk_cache WHERE feature_id=? AND pi_label=?',
                    (feature_id, pi_label)).fetchone()
    c.close()
    if not row:
        return None
    return dict(row)


def load_chalk_as_object(feature_id: str, pi_label: str):
    """Load cached Chalk data and reconstruct as ChalkData object. Returns None if not cached."""
    from .chalk_parser import ChalkData, ChalkScenario
    raw = load_chalk(feature_id, pi_label)
    if not raw:
        return None
    data = ChalkData(
        feature_id=feature_id,
        scope=raw.get('scope', ''),
        rules=raw.get('rules', ''),
        raw_text=raw.get('raw_text', ''),
        open_items=json.loads(raw.get('open_items_json', '[]')),
    )
    try:
        data.tables = json.loads(raw.get('tables_json', '[]'))
    except:
        data.tables = []
    try:
        for s in json.loads(raw.get('scenarios_json', '[]')):
            data.scenarios.append(ChalkScenario(
                scenario_id=s.get('scenario_id', ''),
                title=s.get('title', ''),
                prereq=s.get('prereq', ''),
                cdr_input=s.get('cdr_input', ''),
                derivation_rule=s.get('derivation_rule', ''),
                steps=s.get('steps', []),
                variations=s.get('variations', []),
                validation=s.get('validation', ''),
                category=s.get('category', ''),
            ))
    except:
        pass
    return data


def get_chalk_cache_count() -> int:
    """Total chalk entries in DB."""
    c = _conn()
    row = c.execute('SELECT COUNT(*) as cnt FROM chalk_cache').fetchone()
    c.close()
    return row['cnt']


# ================================================================
# GENERATIONS (replaces transaction_log.json)
# ================================================================

def log_generation_db(feature_id, pi, tc_count, step_count, strategy, file_path, status='SUCCESS'):
    """Log a generation to DB."""
    c = _conn()
    c.execute('''INSERT INTO generations
        (feature_id, pi, tc_count, step_count, strategy, file_path, file_name, status)
        VALUES (?,?,?,?,?,?,?,?)''', (
        feature_id, pi, tc_count, step_count, strategy,
        str(file_path), Path(file_path).name if file_path else '', status,
    ))
    c.commit(); c.close()


def get_history_db(limit=50) -> List[Dict]:
    """Get generation history from DB."""
    c = _conn()
    rows = c.execute('''SELECT * FROM generations ORDER BY created_at DESC LIMIT ?''', (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ================================================================
# TEST DATA POOL
# ================================================================

def add_test_data(data_type: str, value: str, environment='SIT', notes=''):
    """Add a test data entry (MDN, ICCID, IMEI, etc.)."""
    c = _conn()
    c.execute('''INSERT OR IGNORE INTO test_data_pool (data_type, value, environment, notes)
        VALUES (?,?,?,?)''', (data_type, value, environment, notes))
    c.commit(); c.close()


def get_test_data(data_type: str, environment='SIT', status='available') -> List[Dict]:
    """Get available test data entries."""
    c = _conn()
    rows = c.execute('''SELECT * FROM test_data_pool
        WHERE data_type=? AND environment=? AND status=?
        ORDER BY last_used ASC NULLS FIRST LIMIT 10''',
        (data_type, environment, status)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def mark_test_data_used(data_id: int):
    """Mark a test data entry as used."""
    c = _conn()
    c.execute('UPDATE test_data_pool SET last_used=?, status=? WHERE id=?',
              (datetime.now().isoformat(), 'in_use', data_id))
    c.commit(); c.close()


# ================================================================
# UTILITY
# ================================================================

def is_data_stale(last_fetched_str: Optional[str]) -> bool:
    """Check if a timestamp is older than STALE_HOURS."""
    if not last_fetched_str:
        return True
    try:
        fetched = datetime.fromisoformat(last_fetched_str)
        return datetime.now() - fetched > timedelta(hours=STALE_HOURS)
    except:
        return True


def get_db_stats() -> Dict:
    """Get DB statistics for dashboard display."""
    c = _conn()
    stats = {
        'pi_count': c.execute('SELECT COUNT(*) FROM pi_pages').fetchone()[0],
        'feature_count': c.execute('SELECT COUNT(*) FROM features').fetchone()[0],
        'jira_cached': c.execute('SELECT COUNT(*) FROM jira_cache').fetchone()[0],
        'chalk_cached': c.execute('SELECT COUNT(*) FROM chalk_cache').fetchone()[0],
        'generations': c.execute('SELECT COUNT(*) FROM generations').fetchone()[0],
        'test_data': c.execute('SELECT COUNT(*) FROM test_data_pool').fetchone()[0],
        'db_size_kb': DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0,
    }
    c.close()
    return stats


# Initialize on import
init_db()
