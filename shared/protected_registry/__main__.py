# -*- coding: utf-8 -*-
"""
CLI for the protected (do-not-touch) registry.

    python -m shared.protected_registry add 4145810607 --type MDN --reason "PROD - do not touch"
    python -m shared.protected_registry list
    python -m shared.protected_registry check 4145810607
    python -m shared.protected_registry remove 4145810607
"""

import argparse
import sys

# UTF-8 stdout (Windows cp1252 consoles choke on emojis/symbols).
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from .registry import (
    add_protected, remove_protected, find_protected, list_protected, db_path,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='shared.protected_registry',
                                description='Manage the do-not-touch identifier registry.')
    sub = p.add_subparsers(dest='cmd', required=True)

    pa = sub.add_parser('add', help='Add/update a protected identifier')
    pa.add_argument('value', help='the identifier value (e.g. an MDN like 4145810607)')
    pa.add_argument('--type', default='AUTO',
                    help='MDN|IMEI|ICCID|IMSI|EID|ACCOUNT|DEVICE|OTHER (default: auto-detect)')
    pa.add_argument('--reason', default='', help='why it is protected (e.g. "PROD - do not touch")')
    pa.add_argument('--note', default='', help='optional free-form note')
    pa.add_argument('--by', default='', help='who added it')

    pr = sub.add_parser('remove', help='Remove a protected identifier')
    pr.add_argument('value')

    pc = sub.add_parser('check', help='Check whether a value is protected')
    pc.add_argument('value')

    sub.add_parser('list', help='List all protected identifiers')
    sub.add_parser('path', help='Print the registry DB path')

    args = p.parse_args(argv)

    if args.cmd == 'add':
        e = add_protected(args.value, entity_type=args.type, reason=args.reason,
                          note=args.note, added_by=args.by)
        print(f'[OK] Protected {e.entity_type} {e.value} (norm={e.value_norm})'
              + (f' — {e.reason}' if e.reason else ''))
        return 0

    if args.cmd == 'remove':
        ok = remove_protected(args.value)
        print('[OK] Removed' if ok else '[--] Not found')
        return 0 if ok else 1

    if args.cmd == 'check':
        rec = find_protected(args.value)
        if rec:
            print(f'[BLOCKED] {args.value} is PROTECTED as {rec.entity_type} '
                  f'({rec.reason or "no reason"}) — do NOT touch in any environment.')
            return 2
        print(f'[SAFE] {args.value} is not in the do-not-touch registry.')
        return 0

    if args.cmd == 'list':
        rows = list_protected()
        if not rows:
            print('(registry is empty)')
            return 0
        print(f'{len(rows)} protected identifier(s) — DB: {db_path()}')
        print('-' * 90)
        for r in rows:
            print(f"  {r['entity_type']:<8} {r['value']:<24} {r.get('reason') or ''}"
                  + (f"  [{r.get('added_by')}]" if r.get('added_by') else ''))
        return 0

    if args.cmd == 'path':
        print(db_path())
        return 0

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
