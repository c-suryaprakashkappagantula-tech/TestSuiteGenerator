"""Regenerate the suite snapshot expectations.

    python -m tests.regenerate_snapshot

Run this deliberately, after reviewing why the output moved. Overwriting the snapshot to make
a red build green is how a real regression gets absorbed.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TSG_ROOT = os.path.dirname(HERE)
sys.path.insert(0, TSG_ROOT)
os.chdir(TSG_ROOT)

from tests.conftest import DEFAULT_OPTIONS, REVIEW_FEATURES   # noqa: E402

OUT = os.path.join(HERE, 'fixtures', 'expected_suites.json')


def _quiet(_message):
    pass


def build(feature_id):
    from modules.database import _conn, load_chalk_as_object
    from modules.data_first_engine import build_test_suite_v8
    from modules.pipeline import block_jira_fetch
    from modules.deep_miner import deep_mine

    jira = block_jira_fetch(page=None, feature_id=feature_id, log=_quiet)['jira']
    if jira is None:
        return None
    conn = _conn()
    row = conn.execute(
        "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
        "AND scenarios_json != '[]' LIMIT 1", (feature_id,)).fetchone()
    conn.close()
    pi = row['pi_label'] if row else None
    chalk = load_chalk_as_object(feature_id, pi) if pi else None
    mined = deep_mine(jira, chalk, page=None, log=_quiet)
    return build_test_suite_v8(jira, chalk, [], dict(DEFAULT_OPTIONS),
                              deep_mine_result=mined, log=_quiet)


def main():
    snapshot = {}
    for feature_id in REVIEW_FEATURES:
        try:
            suite = build(feature_id)
        except Exception as exc:
            print('  %-16s SKIPPED (%s: %s)' % (feature_id, type(exc).__name__, exc))
            continue
        if suite is None:
            print('  %-16s SKIPPED (not in cache)' % feature_id)
            continue
        titles = [getattr(tc, 'summary', '') or '' for tc in suite.test_cases]
        snapshot[feature_id] = {
            'count': len(titles),
            'steps': sum(len(getattr(tc, 'steps', []) or []) for tc in suite.test_cases),
            'titles': titles,
        }
        print('  %-16s %3d test cases' % (feature_id, len(titles)))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(snapshot, fh, indent=1, ensure_ascii=False, sort_keys=True)
    print('\nwrote %s  (%d features)' % (OUT, len(snapshot)))


if __name__ == '__main__':
    main()
