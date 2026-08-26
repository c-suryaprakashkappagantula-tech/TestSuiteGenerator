"""Shared fixtures for the TSG suite.

Everything here builds suites from `tsg_cache.db` with `page=None`, so no Jira, no Chalk and
no browser. Nothing writes an Excel, a checkpoint, a Feature Summary or a DB row - the tests
call `build_test_suite_v8` directly and never `block_generate_output`.

TSG's modules resolve `tsg_cache.db` and other paths relative to the working directory, so
the session fixture changes into the project root once and restores it afterwards.
"""
import os
import sys

import pytest

TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The 16 features the 19-20 August review was verified against. Chosen for coverage of the
# paths that broke: the AC gate, the CR path, attachment items, blob-heavy AC, and the
# eligibility injector.
REVIEW_FEATURES = [
    'MWTGPROV-4166',   # CR/bug ticket: CR path, eligibility injector, attachment items
    'MWTGPROV-4190',   # the AC gate fix
    'MWTGPROV-3969', 'MWTGPROV-3970', 'MWTGPROV-3971', 'MWTGPROV-3972',
    'MWTGPROV-4086', 'MWTGPROV-4136', 'MWTGPROV-4230', 'MWTGPROV-4329',
    'MWTGPROV-4349', 'MWTGPROV-4406',   # 4406 is the second CR ticket
    'MWTGPROV-4413', 'MWTGPROV-4374', 'MWTGPROV-4323', 'MWTGPROV-4416',
]

DEFAULT_OPTIONS = {
    'include_positive': True,
    'include_negative': True,
    'include_e2e': True,
    'include_edge': True,
    'include_attachments': False,
    'custom_instructions': '',
    'engine_version': '8',
}


@pytest.fixture(scope='session', autouse=True)
def _tsg_cwd():
    """Run from the TSG root, because modules resolve paths relative to it."""
    previous = os.getcwd()
    if TSG_ROOT not in sys.path:
        sys.path.insert(0, TSG_ROOT)
    os.chdir(TSG_ROOT)
    yield TSG_ROOT
    os.chdir(previous)


@pytest.fixture(scope='session')
def cache_available(_tsg_cwd):
    """Skip cache-dependent tests rather than fail when the cache is absent."""
    db = os.path.join(TSG_ROOT, 'tsg_cache.db')
    if not os.path.exists(db):
        pytest.skip('tsg_cache.db not present - suite-level tests need a populated cache')
    return db


def _quiet(_message):
    """Swallow generator logging so test output stays readable."""


@pytest.fixture(scope='session')
def build_suite(cache_available):
    """Return a builder that produces a suite for a feature id, memoised per session.

    Building 16 suites takes roughly 90 seconds, so results are cached for the session.
    """
    from modules.database import _conn, load_chalk_as_object
    from modules.data_first_engine import build_test_suite_v8
    from modules.pipeline import block_jira_fetch
    from modules.deep_miner import deep_mine

    cache = {}

    def _build(feature_id, options=None):
        key = (feature_id, tuple(sorted((options or {}).items())))
        if key in cache:
            return cache[key]

        jira = block_jira_fetch(page=None, feature_id=feature_id, log=_quiet)['jira']
        if jira is None:
            pytest.skip('%s is not in the cache' % feature_id)

        conn = _conn()
        row = conn.execute(
            "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
            "AND scenarios_json != '[]' LIMIT 1", (feature_id,)).fetchone()
        conn.close()
        pi = row['pi_label'] if row else None
        chalk = load_chalk_as_object(feature_id, pi) if pi else None

        mined = deep_mine(jira, chalk, page=None, log=_quiet)
        opts = dict(DEFAULT_OPTIONS)
        opts.update(options or {})
        suite = build_test_suite_v8(jira, chalk, [], opts,
                                    deep_mine_result=mined, log=_quiet)
        cache[key] = suite
        return suite

    return _build


@pytest.fixture(scope='session')
def mine_subtasks(cache_available):
    """Return a callable giving the mined subtask AC items for a feature id."""
    import json
    import sqlite3

    def _mine(feature_id):
        from modules.deep_miner import _mine_subtask
        conn = sqlite3.connect(os.path.join(TSG_ROOT, 'tsg_cache.db'))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT subtasks_json FROM jira_cache WHERE feature_id=?',
            (feature_id,)).fetchone()
        conn.close()
        if row is None:
            pytest.skip('%s is not in the cache' % feature_id)

        items = []
        for subtask in json.loads(row['subtasks_json'] or '[]'):
            if isinstance(subtask, dict):
                items.extend(_mine_subtask(subtask, log=_quiet).ac_items)
        return items

    return _mine


def summaries(suite):
    """Test-case summaries for a built suite."""
    return [getattr(tc, 'summary', '') or '' for tc in suite.test_cases]
