"""
ui_mirror.py — UI Verification Mirror Layer
=============================================
For any API-triggered operation that has a matching NBOP menu/page,
generate a companion TC that verifies the result through NBOP portal.

This is ADDITIVE — it never modifies or replaces existing TCs.
It only appends new "UI Verification" TCs at the end of the suite.

Uses the NBOP UI Knowledge Base for:
  - Real navigation paths (NBOP → Subscriber Profile → ≡ Menu → ...)
  - Real field names (MDN, ICCID, IMEI, Line Status, etc.)
  - Real button/tab labels (Features, Line Summary, Transaction History)

Triggered from: test_engine.py build_test_suite() — runs after all
other enrichment layers, before quality gate.
"""

import re
from typing import List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════
#  OPERATION → UI VERIFICATION MAP
#  Maps API operations to their NBOP verification paths and fields
# ════════════════════════════════════════════════════════════════════

_UI_VERIFY_MAP = {
    # operation keyword → (nav_path, fields_to_check, verify_description)
    'change rateplan': {
        'nav': 'NBOP → Subscriber Profile → Features button',
        'fields': ['Retail Plan', 'Wholesale Plan'],
        'section': 'Add-Ons',
        'verify': 'new rate plan code reflected in Add-Ons section',
    },
    'change rate plan': {
        'nav': 'NBOP → Subscriber Profile → Features button',
        'fields': ['Retail Plan', 'Wholesale Plan'],
        'section': 'Add-Ons',
        'verify': 'new rate plan code reflected in Add-Ons section',
    },
    'change feature': {
        'nav': 'NBOP → Subscriber Profile → Features button',
        'fields': ['Feature list', 'Feature toggle state'],
        'section': 'Add-Ons',
        'verify': 'feature added/removed correctly in Features list',
    },
    'scamblock': {
        'nav': 'NBOP → Subscriber Profile → Features button',
        'fields': ['ScamBlock feature toggle'],
        'section': 'Add-Ons',
        'verify': 'ScamBlock feature toggle state matches expected',
    },
    'change sim': {
        'nav': 'NBOP → Subscriber Profile → SIM Information section',
        'fields': ['ICCID (SIM)', 'SIM Type', 'IMSI', 'SIM Status'],
        'section': 'SIM Information',
        'verify': 'new ICCID and IMSI reflected in SIM Information',
    },
    'change device': {
        'nav': 'NBOP → Subscriber Profile → Device Information section',
        'fields': ['IMEI1 (Device)', 'Model', 'Make', 'Device Type'],
        'section': 'Device Information',
        'verify': 'new IMEI and device details reflected in Device Information',
    },
    'change imei': {
        'nav': 'NBOP → Subscriber Profile → Device Information section',
        'fields': ['IMEI1 (Device)', 'Model', 'Make'],
        'section': 'Device Information',
        'verify': 'new IMEI reflected in Device Information',
    },
    'swap mdn': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['MDN', 'ICCID (SIM)', 'IMEI1 (Device)'],
        'section': 'Line Information + SIM Information + Device Information',
        'verify': 'both lines show swapped MDN, ICCID, IMEI correctly',
    },
    'change mdn': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['MDN', 'Line Status'],
        'section': 'Line Information',
        'verify': 'new MDN reflected in Line Information',
    },
    'port-in': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['MDN', 'Line Status', 'Port In Flag'],
        'section': 'Line Information',
        'verify': 'ported-in MDN shows Active status with Port In Flag = Y',
    },
    'hotline': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Hotlined with correct Last Status Change timestamp',
    },
    'remove hotline': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Active (restored) with correct Last Status Change timestamp',
    },
    'suspend': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Suspended with correct Last Status Change timestamp',
    },
    'restore': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Active (restored from Suspended)',
    },
    'reconnect': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Active (reconnected)',
    },
    'activate': {
        'nav': 'NBOP → Subscriber Profile',
        'fields': ['MDN', 'Line Status', 'ICCID (SIM)', 'IMEI1 (Device)'],
        'section': 'Line Information + SIM Information + Device Information',
        'verify': 'subscriber profile fully populated with Active status',
    },
    'deactivate': {
        'nav': 'NBOP → Subscriber Profile → Line Information section',
        'fields': ['Line Status', 'Last Status Change'],
        'section': 'Line Information',
        'verify': 'Line Status = Deactivated',
    },
    'change bcd': {
        'nav': 'NBOP → Subscriber Profile → Account Information section',
        'fields': ['DPFO Reset Day'],
        'section': 'Account Information',
        'verify': 'DPFO Reset Day updated to new BCD value',
    },
    'sync subscriber': {
        'nav': 'NBOP → Subscriber Profile → ≡ Menu → Sync Line → Sync with Network',
        'fields': ['All profile sections'],
        'section': 'Full Profile',
        'verify': 'all profile sections refreshed with latest data from network',
    },
    'network reset': {
        'nav': 'NBOP → Subscriber Profile → ≡ Menu → Reset Line → Network',
        'fields': ['Line Status'],
        'section': 'Line Information',
        'verify': 'network reset completed, line status unchanged',
    },
    'reclaim mdn': {
        'nav': 'NBOP → Subscriber Profile → ≡ Menu → Manage Line → Reclaim MDN',
        'fields': ['MDN', 'Line Status'],
        'section': 'Line Information',
        'verify': 'MDN reclaimed and available for reassignment',
    },
}


def _match_operations(tc_text: str) -> List[dict]:
    """Find all UI verification mappings that match a TC's text."""
    matches = []
    tc_lower = tc_text.lower()
    for op_key, mapping in _UI_VERIFY_MAP.items():
        if op_key in tc_lower:
            matches.append({'op': op_key, **mapping})
    return matches


# ════════════════════════════════════════════════════════════════════
#  MAIN ENTRY — generate UI mirror TCs
# ════════════════════════════════════════════════════════════════════

def generate_ui_mirror_tcs(test_cases, feature_id, feature_name, log=print):
    """Generate UI verification mirror TCs for API-triggered operations.

    For each unique operation found in existing API TCs that has an NBOP
    counterpart, generate ONE companion TC that verifies the result via NBOP.

    Returns: list of new TestCase objects (additive, never modifies existing)
    """
    from .test_engine import TestCase, TestStep
    from .nbop_ui_knowledge import is_available, get_navigation_path
    from .tc_templates import classify_feature

    if not is_available():
        log('[UI-MIRROR] NBOP UI knowledge base not available — skipping')
        return []

    # Check if this is a pure CDR/mediation feature — skip UI mirror
    # CDR features don't have NBOP UI operations, only mediation pipeline
    fc = classify_feature(feature_name)
    if fc.is_notification and not fc.is_api and not fc.is_ui:
        # Check feature name directly — is it a CDR/ILD/mediation feature?
        _fname_lower = feature_name.lower()
        _is_cdr_feature = any(kw in _fname_lower for kw in ['cdr', 'ild', 'roaming', 'country',
                                                               'mediation', 'prr', 'usage file',
                                                               'call type', 'metering', 'mhs data'])
        if _is_cdr_feature:
            log('[UI-MIRROR] Pure CDR/mediation feature "%s" — skipping UI mirror' % feature_name)
            return []

    # Collect all text from existing TCs to find operations
    existing_summaries = set()
    all_ops_text = ''
    for tc in test_cases:
        existing_summaries.add(tc.summary.lower())
        all_ops_text += ' ' + tc.summary.lower() + ' ' + (tc.description or '').lower()

    # Find which operations are covered by API TCs
    ops_found = set()
    for op_key in _UI_VERIFY_MAP:
        if op_key in all_ops_text:
            ops_found.add(op_key)

    if not ops_found:
        log('[UI-MIRROR] No API operations with NBOP counterparts found — skipping')
        return []

    # Deduplicate: merge similar operations (e.g., 'change rateplan' and 'change rate plan')
    _dedup_ops = set()
    _ops_to_remove = set()
    for op in ops_found:
        norm = op.replace(' ', '').replace('-', '')
        if norm not in _dedup_ops:
            _dedup_ops.add(norm)
        else:
            _ops_to_remove.add(op)
    ops_found -= _ops_to_remove

    # Check if UI mirror TCs already exist (don't duplicate)
    _already_has_ui = any('nbop' in s and ('verify' in s or 'validate' in s)
                          for s in existing_summaries)

    log('[UI-MIRROR] Found %d API operations with NBOP counterparts' % len(ops_found))

    new_tcs = []
    next_idx = len(test_cases) + 1

    for op_key in sorted(ops_found):
        mapping = _UI_VERIFY_MAP[op_key]

        # Skip if we already have a UI verification TC for this operation
        _op_in_existing = any(op_key in s and 'nbop' in s for s in existing_summaries)
        if _op_in_existing:
            continue

        # Try to get real navigation path from KB
        try:
            real_nav = get_navigation_path(op_key)
        except Exception:
            real_nav = mapping['nav']

        # Build the TC
        op_title = op_key.replace('-', ' ').title()
        fields_str = ', '.join(mapping['fields'][:4])

        tc = TestCase(
            sno=str(next_idx),
            summary='TC%03d_%s_UI Verify: Validate %s result in NBOP portal' % (
                next_idx, feature_id, op_title),
            description='After %s operation via API, verify the result is correctly '
                        'reflected in NBOP portal. Navigate to %s and check: %s.' % (
                            op_title, mapping['section'], mapping['verify']),
            preconditions='1.\t%s operation completed successfully via API\n'
                          '2.\tNBOP portal accessible and user logged in\n'
                          '3.\tSubscriber MDN available for search' % op_title,
            story_linkage=feature_id,
            label=feature_id,
            category='Happy Path',
            steps=[
                TestStep(1,
                    'Launch NBOP portal and search subscriber by MDN',
                    'Subscriber profile loads with header cards showing Account, MDN, IMEI, ICCID'),
                TestStep(2,
                    'Navigate to %s' % real_nav,
                    '%s screen/section loads with all expected fields' % mapping['section']),
                TestStep(3,
                    'Verify %s: check fields %s' % (mapping['verify'], fields_str),
                    'All fields show correct post-operation values matching API response'),
                TestStep(4,
                    'Navigate to ≡ Menu → Transaction History',
                    'Transaction History entry found with correct timestamp, type, and SUCC status'),
            ],
        )
        new_tcs.append(tc)
        next_idx += 1
        log('[UI-MIRROR]   Added: UI Verify %s (%s)' % (op_title, mapping['section']))

    if new_tcs:
        log('[UI-MIRROR] Generated %d UI verification mirror TCs' % len(new_tcs))
    else:
        log('[UI-MIRROR] All operations already have UI verification — no new TCs needed')

    return new_tcs
