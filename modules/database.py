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

        CREATE TABLE IF NOT EXISTS test_suites (
            suite_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id      TEXT NOT NULL,
            feature_title   TEXT,
            pi              TEXT,
            strategy        TEXT,
            tc_count        INTEGER,
            step_count      INTEGER,
            scope           TEXT,
            acceptance_criteria TEXT,
            data_sources    TEXT,
            warnings        TEXT,
            file_path       TEXT,
            engine_version  TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS test_cases (
            tc_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_id        INTEGER NOT NULL,
            sno             TEXT,
            summary         TEXT,
            description     TEXT,
            preconditions   TEXT,
            category        TEXT,
            story_linkage   TEXT,
            label           TEXT,
            FOREIGN KEY (suite_id) REFERENCES test_suites(suite_id)
        );

        CREATE TABLE IF NOT EXISTS test_steps (
            step_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tc_id           INTEGER NOT NULL,
            step_num        INTEGER,
            summary         TEXT,
            expected        TEXT,
            FOREIGN KEY (tc_id) REFERENCES test_cases(tc_id)
        );

        CREATE INDEX IF NOT EXISTS idx_suites_feature ON test_suites(feature_id);
        CREATE INDEX IF NOT EXISTS idx_cases_suite ON test_cases(suite_id);
        CREATE INDEX IF NOT EXISTS idx_steps_tc ON test_steps(tc_id);

        CREATE TABLE IF NOT EXISTS artifact_hashes (
            feature_id      TEXT NOT NULL,
            artifact_type   TEXT NOT NULL,
            content_hash    TEXT NOT NULL,
            source          TEXT DEFAULT '',
            last_seen       TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (feature_id, artifact_type, source)
        );

        -- Transaction log: every action performed via TSG Dashboard
        CREATE TABLE IF NOT EXISTS tsg_transactions (
            txn_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action          TEXT NOT NULL,
            feature_id      TEXT,
            pi_label        TEXT,
            details         TEXT,
            tc_count        INTEGER DEFAULT 0,
            step_count      INTEGER DEFAULT 0,
            file_path       TEXT,
            status          TEXT DEFAULT 'SUCCESS',
            duration_sec    REAL DEFAULT 0,
            user_session    TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_txn_feature ON tsg_transactions(feature_id);
        CREATE INDEX IF NOT EXISTS idx_txn_action ON tsg_transactions(action);

        -- Audit log: system events, errors, config changes
        CREATE TABLE IF NOT EXISTS tsg_audit_log (
            audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT NOT NULL,
            severity        TEXT DEFAULT 'INFO',
            message         TEXT NOT NULL,
            feature_id      TEXT,
            details_json    TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_audit_type ON tsg_audit_log(event_type);
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
        'suites_stored': c.execute('SELECT COUNT(*) FROM test_suites').fetchone()[0],
        'tcs_stored': c.execute('SELECT COUNT(*) FROM test_cases').fetchone()[0],
        'db_size_kb': DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0,
    }
    c.close()
    return stats


# Initialize on import
init_db()


# ================================================================
# TEST SUITE STORAGE (V4)
# ================================================================

def save_test_suite(suite, file_path='') -> int:
    """Save a complete test suite (suite + TCs + steps) to DB.
    Returns the suite_id for reference."""
    c = _conn()
    try:
        cur = c.execute('''
            INSERT INTO test_suites (feature_id, feature_title, pi, strategy, tc_count, step_count,
                                     scope, acceptance_criteria, data_sources, warnings, file_path, engine_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            suite.feature_id, suite.feature_title, suite.pi,
            ','.join(getattr(suite, 'channel', '')) if isinstance(getattr(suite, 'channel', ''), list) else getattr(suite, 'channel', ''),
            len(suite.test_cases),
            sum(len(tc.steps) for tc in suite.test_cases),
            suite.scope or '',
            json.dumps(suite.acceptance_criteria),
            json.dumps(suite.data_sources),
            json.dumps(suite.warnings),
            str(file_path),
            getattr(suite, 'engine_version', ''),
        ))
        suite_id = cur.lastrowid

        for tc in suite.test_cases:
            tc_cur = c.execute('''
                INSERT INTO test_cases (suite_id, sno, summary, description, preconditions,
                                        category, story_linkage, label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (suite_id, tc.sno, tc.summary, tc.description,
                  tc.preconditions, tc.category, tc.story_linkage, tc.label))
            tc_id = tc_cur.lastrowid

            for step in tc.steps:
                c.execute('''
                    INSERT INTO test_steps (tc_id, step_num, summary, expected)
                    VALUES (?, ?, ?, ?)
                ''', (tc_id, step.step_num, step.summary, step.expected))

        c.commit()
        return suite_id
    except Exception as e:
        c.rollback()
        raise e
    finally:
        c.close()


def load_test_suite(suite_id: int) -> Optional[Dict]:
    """Load a complete test suite by suite_id. Returns dict with suite info, TCs, and steps."""
    c = _conn()
    row = c.execute('SELECT * FROM test_suites WHERE suite_id = ?', (suite_id,)).fetchone()
    if not row:
        c.close()
        return None

    suite = dict(row)
    suite['acceptance_criteria'] = json.loads(suite.get('acceptance_criteria', '[]'))
    suite['data_sources'] = json.loads(suite.get('data_sources', '[]'))
    suite['warnings'] = json.loads(suite.get('warnings', '[]'))

    tcs = []
    tc_rows = c.execute('SELECT * FROM test_cases WHERE suite_id = ? ORDER BY CAST(sno AS INTEGER)', (suite_id,)).fetchall()
    for tc_row in tc_rows:
        tc = dict(tc_row)
        steps = c.execute('SELECT * FROM test_steps WHERE tc_id = ? ORDER BY step_num', (tc['tc_id'],)).fetchall()
        tc['steps'] = [dict(s) for s in steps]
        tcs.append(tc)

    suite['test_cases'] = tcs
    c.close()
    return suite


def load_latest_suite(feature_id: str) -> Optional[Dict]:
    """Load the most recent test suite for a feature."""
    c = _conn()
    row = c.execute(
        'SELECT suite_id FROM test_suites WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1',
        (feature_id,)).fetchone()
    c.close()
    if row:
        return load_test_suite(row['suite_id'])
    return None


def get_suite_history(feature_id: str, limit=10) -> List[Dict]:
    """Get generation history for a feature with suite IDs."""
    c = _conn()
    rows = c.execute('''
        SELECT suite_id, feature_id, feature_title, pi, tc_count, step_count, file_path, created_at
        FROM test_suites WHERE feature_id = ? ORDER BY created_at DESC LIMIT ?
    ''', (feature_id, limit)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_all_suite_history(limit=50) -> List[Dict]:
    """Get all suite generation history across all features."""
    c = _conn()
    rows = c.execute('''
        SELECT suite_id, feature_id, feature_title, pi, tc_count, step_count, file_path, created_at
        FROM test_suites ORDER BY created_at DESC LIMIT ?
    ''', (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_tcs_for_feature(feature_id: str) -> List[Dict]:
    """Get all TCs from the latest suite for a feature. Useful for LLM prompts."""
    suite = load_latest_suite(feature_id)
    if not suite:
        return []
    return suite.get('test_cases', [])


def build_ai_review_prompt(feature_id: str) -> str:
    """Build a ready-to-paste LLM prompt for AI review of a feature's test suite."""
    suite = load_latest_suite(feature_id)
    if not suite:
        return 'No test suite found for %s in DB.' % feature_id

    # Build the prompt
    lines = []
    lines.append('You are a senior QA architect specializing in telecom provisioning systems (T-Mobile/Sprint MVNO).')
    lines.append('')
    lines.append('## Inputs')
    lines.append('')
    lines.append('### 1. Feature Description')
    lines.append('Feature ID: %s' % suite['feature_id'])
    lines.append('Title: %s' % suite['feature_title'])
    lines.append('PI: %s' % suite.get('pi', 'N/A'))
    if suite.get('scope'):
        lines.append('Scope: %s' % suite['scope'][:500])
    lines.append('')

    lines.append('### 2. Acceptance Criteria')
    ac = suite.get('acceptance_criteria', [])
    if ac:
        for i, item in enumerate(ac, 1):
            lines.append('%d. %s' % (i, item))
    else:
        lines.append('None extracted.')
    lines.append('')

    lines.append('### 3. Data Sources')
    for ds in suite.get('data_sources', []):
        lines.append('- %s' % ds)
    lines.append('')

    lines.append('### 4. Existing Test Cases (%d total)' % suite['tc_count'])
    lines.append('')
    for tc in suite.get('test_cases', []):
        lines.append('**TC%s [%s]: %s**' % (tc['sno'], tc['category'], tc['summary'][:100]))
        lines.append('Description: %s' % (tc['description'] or '')[:150])
        steps = tc.get('steps', [])
        if steps:
            for s in steps:
                lines.append('  Step %s: %s → Expected: %s' % (s['step_num'], s['summary'][:80], s['expected'][:60]))
        lines.append('')

    lines.append('## Tasks')
    lines.append('- Identify MISSING test scenarios that are not covered above')
    lines.append('- Focus on: edge cases, failure scenarios, integration gaps, rollback scenarios')
    lines.append('- Do NOT rewrite existing tests — only suggest NEW ones')
    lines.append('- For each suggestion provide: Title, Category (Negative/Edge Case/E2E/Regression), Description, Rationale')
    lines.append('- Output as a structured numbered list')

    return '\n'.join(lines)


def search_tcs(keyword: str, feature_id: str = None, category: str = None, limit=50) -> List[Dict]:
    """Search test cases across all suites by keyword, feature, or category."""
    c = _conn()
    query = '''
        SELECT tc.tc_id, tc.sno, tc.summary, tc.description, tc.category, tc.story_linkage,
               s.feature_id, s.feature_title, s.pi, s.created_at
        FROM test_cases tc
        JOIN test_suites s ON tc.suite_id = s.suite_id
        WHERE 1=1
    '''
    params = []
    if keyword:
        query += ' AND (tc.summary LIKE ? OR tc.description LIKE ?)'
        params.extend(['%%%s%%' % keyword, '%%%s%%' % keyword])
    if feature_id:
        query += ' AND s.feature_id = ?'
        params.append(feature_id)
    if category:
        query += ' AND tc.category = ?'
        params.append(category)
    query += ' ORDER BY s.created_at DESC LIMIT ?'
    params.append(limit)

    rows = c.execute(query, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ================================================================
# ARTIFACT STALENESS DETECTION (Finding #11)
# ================================================================

def check_suite_staleness(feature_id: str, current_chalk_hash: str = '', current_jira_hash: str = '') -> Dict:
    """Check if the latest suite for a feature is stale based on source data changes.
    Returns dict with is_stale flag and reasons."""
    c = _conn()
    row = c.execute(
        'SELECT suite_id, created_at, engine_version FROM test_suites WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1',
        (feature_id,)).fetchone()
    c.close()

    if not row:
        return {'is_stale': False, 'reason': 'No existing suite', 'suite_id': None}

    reasons = []
    suite_created = row['created_at']

    # Check if Chalk data is newer than suite
    c2 = _conn()
    chalk_row = c2.execute(
        'SELECT last_fetched FROM chalk_cache WHERE feature_id = ? ORDER BY last_fetched DESC LIMIT 1',
        (feature_id,)).fetchone()
    if chalk_row and chalk_row['last_fetched'] and chalk_row['last_fetched'] > suite_created:
        reasons.append('Chalk data updated since last generation')

    # Check if Jira data is newer than suite
    jira_row = c2.execute(
        'SELECT last_fetched FROM jira_cache WHERE feature_id = ?',
        (feature_id,)).fetchone()
    if jira_row and jira_row['last_fetched'] and jira_row['last_fetched'] > suite_created:
        reasons.append('Jira data updated since last generation')

    # Check engine version
    from .test_engine import ENGINE_VERSION
    if row['engine_version'] and row['engine_version'] != ENGINE_VERSION:
        reasons.append('Engine version changed (%s → %s)' % (row['engine_version'], ENGINE_VERSION))

    c2.close()

    return {
        'is_stale': len(reasons) > 0,
        'reasons': reasons,
        'suite_id': row['suite_id'],
        'suite_created': suite_created,
    }


# ================================================================
# ARTIFACT HASH & STALENESS DETECTION (Finding #2 & #11)
# ================================================================

def save_artifact_hash(feature_id: str, artifact_type: str, content_hash: str, source: str = ''):
    """Store a content hash for staleness detection.
    artifact_type: 'chalk' | 'jira' | 'attachment' | 'upload'"""
    c = _conn()
    c.execute('''
        INSERT OR REPLACE INTO artifact_hashes (feature_id, artifact_type, content_hash, source, last_seen)
        VALUES (?, ?, ?, ?, datetime('now','localtime'))
    ''', (feature_id, artifact_type, content_hash, source))
    c.commit()
    c.close()


def check_staleness(feature_id: str) -> List[Dict]:
    """Check if any artifacts have changed since last suite generation.
    Returns list of stale artifacts with details."""
    c = _conn()
    stale = []

    # Get the latest suite generation time for this feature
    row = c.execute(
        'SELECT created_at FROM test_suites WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1',
        (feature_id,)).fetchone()

    if not row:
        c.close()
        return []  # No previous suite — nothing to compare

    last_gen = row['created_at']

    # Find artifacts updated after last generation
    rows = c.execute('''
        SELECT artifact_type, content_hash, source, last_seen
        FROM artifact_hashes
        WHERE feature_id = ? AND last_seen > ?
    ''', (feature_id, last_gen)).fetchall()

    for r in rows:
        stale.append({
            'type': r['artifact_type'],
            'hash': r['content_hash'],
            'source': r['source'],
            'updated': r['last_seen'],
        })

    c.close()
    return stale


def get_artifact_hashes(feature_id: str) -> List[Dict]:
    """Get all stored artifact hashes for a feature."""
    c = _conn()
    rows = c.execute(
        'SELECT artifact_type, content_hash, source, last_seen FROM artifact_hashes WHERE feature_id = ?',
        (feature_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ================================================================
# TSG TRANSACTION LOG
# ================================================================

def log_transaction(action: str, feature_id: str = '', pi_label: str = '',
                    details: str = '', tc_count: int = 0, step_count: int = 0,
                    file_path: str = '', status: str = 'SUCCESS', duration_sec: float = 0):
    """Log a TSG dashboard transaction (generate, sync, export, etc.)."""
    c = _conn()
    c.execute('''INSERT INTO tsg_transactions
        (action, feature_id, pi_label, details, tc_count, step_count, file_path, status, duration_sec)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (action, feature_id, pi_label, details, tc_count, step_count, file_path, status, duration_sec))
    c.commit(); c.close()


def get_transactions(feature_id: str = None, action: str = None, limit: int = 50) -> List[Dict]:
    """Get transaction history, optionally filtered."""
    c = _conn()
    query = 'SELECT * FROM tsg_transactions WHERE 1=1'
    params = []
    if feature_id:
        query += ' AND feature_id = ?'; params.append(feature_id)
    if action:
        query += ' AND action = ?'; params.append(action)
    query += ' ORDER BY created_at DESC LIMIT ?'; params.append(limit)
    rows = c.execute(query, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_transaction_stats() -> Dict:
    """Get transaction statistics."""
    c = _conn()
    total = c.execute('SELECT COUNT(*) FROM tsg_transactions').fetchone()[0]
    by_action = c.execute('SELECT action, COUNT(*) as cnt FROM tsg_transactions GROUP BY action ORDER BY cnt DESC').fetchall()
    by_status = c.execute('SELECT status, COUNT(*) as cnt FROM tsg_transactions GROUP BY status').fetchall()
    recent = c.execute('SELECT * FROM tsg_transactions ORDER BY created_at DESC LIMIT 5').fetchall()
    c.close()
    return {
        'total': total,
        'by_action': {r['action']: r['cnt'] for r in by_action},
        'by_status': {r['status']: r['cnt'] for r in by_status},
        'recent': [dict(r) for r in recent],
    }


# ================================================================
# TSG AUDIT LOG
# ================================================================

def log_audit(event_type: str, message: str, severity: str = 'INFO',
              feature_id: str = '', details_json: str = ''):
    """Log an audit event (error, warning, config change, etc.)."""
    c = _conn()
    c.execute('''INSERT INTO tsg_audit_log
        (event_type, severity, message, feature_id, details_json)
        VALUES (?, ?, ?, ?, ?)''',
        (event_type, severity, message, feature_id, details_json))
    c.commit(); c.close()


def get_audit_log(event_type: str = None, severity: str = None, limit: int = 100) -> List[Dict]:
    """Get audit log entries, optionally filtered."""
    c = _conn()
    query = 'SELECT * FROM tsg_audit_log WHERE 1=1'
    params = []
    if event_type:
        query += ' AND event_type = ?'; params.append(event_type)
    if severity:
        query += ' AND severity = ?'; params.append(severity)
    query += ' ORDER BY created_at DESC LIMIT ?'; params.append(limit)
    rows = c.execute(query, params).fetchall()
    c.close()
    return [dict(r) for r in rows]
