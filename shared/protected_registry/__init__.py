# -*- coding: utf-8 -*-
"""
protected_registry — a shared, local "DO-NOT-TOUCH" registry.

PURPOSE
    A single source of truth for identifiers (MDN / IMEI / ICCID / IMSI / EID /
    Account Number / Device / anything) that must NEVER be used or modified by ANY
    dashboard in ANY environment — e.g. real PROD numbers accidentally reachable
    from a test environment.

    Every dashboard (TSE, TSG, TMO API, TMO Daily, MDA, E2E Lifecycle) should consult
    this registry BEFORE it uses/targets an identifier and refuse to touch anything
    that is protected — regardless of the environment it is running against.

USAGE (dashboards)
    from shared.protected_registry import assert_safe, is_protected, filter_safe

    # Hard gate at the start of a run (raises ProtectedEntityError if any is protected):
    assert_safe(mdn=ctx.mdn, imei=ctx.imei, iccid=ctx.iccid, account=ctx.account)

    # Soft check:
    if is_protected(mdn):
        skip()

    # Filter a pool (e.g. device IMEIs) down to only safe values:
    safe_imeis = filter_safe(all_imeis)

USAGE (adding entries — CLI)
    python -m shared.protected_registry add 4145810607 --type MDN --reason "PROD - do not touch"
    python -m shared.protected_registry list
    python -m shared.protected_registry check 4145810607
    python -m shared.protected_registry remove 4145810607

The match is VALUE-based and normalized (digits-only for numeric ids, with US
country-code tolerance), so a number is blocked no matter which id-type it was filed
under or whether it is written with/without a leading '1'.
"""

from .registry import (            # noqa: F401
    ProtectedEntityError,
    ProtectedEntity,
    add_protected,
    remove_protected,
    is_protected,
    find_protected,
    check_identifiers,
    assert_safe,
    filter_safe,
    list_protected,
    db_path,
)

__all__ = [
    'ProtectedEntityError',
    'ProtectedEntity',
    'add_protected',
    'remove_protected',
    'is_protected',
    'find_protected',
    'check_identifiers',
    'assert_safe',
    'filter_safe',
    'list_protected',
    'db_path',
]
