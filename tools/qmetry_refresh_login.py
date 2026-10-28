# -*- coding: utf-8 -*-
"""
qmetry_refresh_login.py — headed QMetry/Jira SSO refresh worker (TSG dashboard).

Launched by the TSG dashboard's "Refresh QMetry login" button in a SEPARATE process.
Reuses MDA's BrowserManager exactly like the manual fix:

    with BrowserManager(headed=True) as bm:
        bm.login_jira(force=True)
        bm.is_logged_in          # PROPERTY

which writes ``MDA-Jira-Dashboard/storage_state.json``. When logged in, that file is
copied (atomically) to every ``--targets`` path. A result JSON is ALWAYS written.

Exit codes: 0 ok · 1 not logged in / no state file · 2 exception.
The worker never probes QMetry; the dashboard re-probes after it finishes.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

TSG_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(r'C:\Users\P3314665\Documents\mcp-jira-server')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--result', required=True, help='result JSON path')
    ap.add_argument('--mda-dir', default=str(REPO_ROOT / 'MDA-Jira-Dashboard'))
    ap.add_argument('--targets', default=None, help='";"-separated copy targets')
    args = ap.parse_args(argv)

    mda_dir = Path(args.mda_dir)
    for p in (str(REPO_ROOT), str(mda_dir), str(TSG_ROOT)):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    # resulting order: TSG root, MDA dir, repo root

    result = {'ok': False, 'is_logged_in': False,
              'source': str(mda_dir / 'storage_state.json'),
              'copied': [], 'error': None, 'finished_at': None}
    code = 2
    try:
        from modules.qmetry_session_guard import distribute_storage_state, refresh_copy_targets
        if args.targets:
            targets = [Path(t) for t in args.targets.split(';') if t.strip()]
        else:
            targets = refresh_copy_targets()

        from browser_manager import BrowserManager
        print('[QMETRY-LOGIN] launching headed SSO (complete login in the Edge window)…', flush=True)
        with BrowserManager(headed=True) as bm:
            bm.login_jira(force=True)
            logged_in = bool(bm.is_logged_in)  # property, no parentheses
        result['is_logged_in'] = logged_in
        print('[QMETRY-LOGIN] is_logged_in=%s' % logged_in, flush=True)

        src = mda_dir / 'storage_state.json'
        if logged_in and src.is_file():
            result['copied'] = distribute_storage_state(src, targets)
            result['ok'] = True
            code = 0
            for c in result['copied']:
                print('[QMETRY-LOGIN] copied -> %s' % c, flush=True)
        else:
            result['error'] = ('not logged in' if not logged_in
                               else 'storage_state.json not written: %s' % src)
            code = 1
    except BaseException as exc:  # SSO timeout, import failure, Ctrl-C
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
        traceback.print_exc()
        code = 2
    finally:
        result['finished_at'] = datetime.now().isoformat(timespec='seconds')
        try:
            with open(args.result, 'w', encoding='utf-8') as fh:
                json.dump(result, fh, indent=2)
        except Exception:
            traceback.print_exc()
    print('[QMETRY-LOGIN] done ok=%s' % result['ok'], flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
