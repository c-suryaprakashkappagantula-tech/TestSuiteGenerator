"""
scenario_enricher.py — Universal scenario gap filler.
Based on patterns from ALL real samples:
  3941 (Port-Out, 29 TCs), 3948 (Change BCD, 20 TCs), 3926 (Activation, 24 TCs),
  4109 (Login Auth, 10 TCs), 4110 (Device Details, 16 TCs), 3782 (Hotline, 63 TCs),
  3946 (Remove Hotline, 50 TCs), 4033 (Change SIM, 32 TCs), 3864 (Usage, 48 TCs),
  Swap MDN Sample (18 steps)

Rules:
1. ALWAYS keep what Chalk/Jira provided — never touch existing TCs
2. Only add what's MISSING — check existing text before adding
3. Don't flood — cap at 50% of existing count
4. Every added TC must be justifiable from sample patterns
"""
from .step_templates import get_step_chain


def _make_tc(idx, fid, title, val, category, preconditions, ctx):
    """Import TestCase here to avoid circular import."""
    from .test_engine import TestCase, TestStep
    chain = get_step_chain(title, val, ctx)
    steps = [TestStep(i+1, s, e) for i, (s, e) in enumerate(chain)]
    return TestCase(sno=str(idx),
        summary='TC%02d_%s - %s' % (idx, fid, title),
        description=val, preconditions=preconditions,
        steps=steps, story_linkage=fid, label=fid, category=category)


def _neg(idx, fid, title, val, ctx):
    return _make_tc(idx, fid, title, val, 'Negative',
        '1.\tSystem in ready state\n2.\tPrepare error condition as per scenario', ctx)

def _pos(idx, fid, title, val, ctx):
    return _make_tc(idx, fid, title, val, 'Happy Path',
        '1.\tActive TMO subscriber line\n2.\tSystem in ready state', ctx)


def enrich_scenarios(test_cases, feature_id, feature_context, log=print):
    """Analyze existing TCs, identify gaps across 9 universal layers, fill smartly."""

    existing_text = ' '.join([
        tc.summary.lower() + ' ' + tc.description.lower() + ' ' +
        ' '.join(s.summary.lower() + ' ' + s.expected.lower() for s in tc.steps)
        for tc in test_cases
    ])

    n = len(test_cases)
    new_tcs = []
    idx = n + 1
    ctx = feature_context.lower()
    is_line = _is_line_feature(ctx, existing_text)
    is_api = _is_api_feature(ctx, existing_text)

    log('[ENRICH] Analyzing %d existing TCs across 9 layers...' % n)

    # ================================================================
    # LAYER 0 (NEW): MANDATORY NEGATIVE CHECKLIST
    # These scenarios MUST exist for any line-level API feature.
    # Unlike other layers, these check for SPECIFIC scenario presence,
    # not just keyword presence. A TC about "not in active status"
    # does NOT satisfy the "hotline" requirement.
    # ================================================================
    if is_line and is_api:
        log('[ENRICH] Layer 0: Mandatory negative checklist...')

        # Build a list of TC summaries (not full text) for precise matching
        existing_summaries = ' '.join([tc.summary.lower() for tc in test_cases])

        MANDATORY_NEGATIVES = [
            # (check_field, required_keyword_in_summary, title, description)
            ('hotline', ['hotline', 'hotlined'],
             'Negative: Verify operation rejected for Hotlined MDN',
             'Trigger the API with a Hotlined MDN. System must reject with HTTP 400 error. No state change.'),
            ('suspended', ['suspend', 'suspended'],
             'Negative: Verify operation rejected for Suspended MDN',
             'Trigger the API with a Suspended MDN. System must reject with HTTP 400 error. No state change.'),
            ('deactivated', ['deactiv', 'deactivated', 'disconnected'],
             'Negative: Verify operation rejected for Deactivated/Disconnected MDN',
             'Trigger the API with a Deactivated MDN. System must reject with HTTP 400 error. No state change.'),
            ('invalid_lineid', ['invalid line', 'invalid lineid', 'non-existent line'],
             'Negative: Verify operation rejected with invalid/non-existent LineId',
             'Trigger the API with an invalid LineId. System must reject with HTTP 400 and specific error code.'),
            ('invalid_account', ['invalid account', 'non-existent account'],
             'Negative: Verify operation rejected with invalid/non-existent AccountId',
             'Trigger the API with an invalid AccountId. System must reject with HTTP 400 and specific error code.'),
            ('mismatch_ids', ['mismatch', 'lineid and accountid'],
             'Negative: Verify operation rejected when LineId and AccountId do not match',
             'Trigger the API with mismatched LineId and AccountId. System must reject with ERR161.'),
        ]

        for check_name, required_kws, title, desc in MANDATORY_NEGATIVES:
            # Check if ANY required keyword appears in TC summaries (not full text)
            found = any(kw in existing_summaries for kw in required_kws)
            if not found:
                tc = _neg(idx, feature_id, title, desc, ctx)
                tc._mandatory = True  # tag for dedup/cap bypass
                new_tcs.append(tc)
                idx += 1
                log('[ENRICH]   L0-MANDATORY: %s' % title[:60])

    # ── Layer 0b: Mandatory rollback failure (for multi-step operations) ──
    if is_line and is_api:
        has_rollback = 'rollback' in existing_text
        # Check specifically for "rollback itself fails" — not "operation fails then rollback"
        has_rollback_failure = any(kw in existing_summaries for kw in
            ['rollback failure', 'rollback itself', 'rollback fails', 'inconsistent state',
             'failed rollback', 'rollback unsuccessful'])
        if has_rollback and not has_rollback_failure:
            tc = _neg(idx, feature_id,
                'Negative: Verify system handles rollback failure (rollback itself fails)',
                'Simulate failure during rollback. System must detect inconsistent state, '
                'log the failure, alert operations, and mark transaction for manual review. '
                'No silent data corruption.',
                ctx)
            tc._mandatory = True
            new_tcs.append(tc)
            idx += 1
            log('[ENRICH]   L0-MANDATORY: rollback failure')

    # ── Layer 0c: Per-group negative mirroring ──
    # If feature has groups (e.g., eSIM/pSIM/mixed for swap), ensure each group
    # has its own set of negatives, not just one global set.
    # This is logged as a warning, not auto-generated (to avoid explosion).
    if is_line:
        group_keywords = []
        for tc in test_cases:
            tl = tc.summary.lower()
            if 'esim to esim' in tl or '(em)' in tl: group_keywords.append('eSIM-to-eSIM')
            elif 'psim to psim' in tl or '(sm)' in tl or '(pm)' in tl: group_keywords.append('pSIM-to-pSIM')
            elif 'psim to esim' in tl or 'esim to psim' in tl or '(am)' in tl: group_keywords.append('pSIM-eSIM')
        unique_groups = set(group_keywords)
        if len(unique_groups) > 1:
            log('[ENRICH]   Note: %d swap groups detected (%s). Negatives apply to all groups.' % (
                len(unique_groups), ', '.join(sorted(unique_groups))))

    # ── Layer 6: Upstream Failures ──
    # From 3941: ITMBO rejects (Invalid PIN, Invalid Account Number)
    # From 3941: Notification timeout (PO not triggered in 50 secs)
    if is_api:
        # Layer 3 (remaining): Field validation not covered by mandatory checklist
        for kw, title, val in [
            ('invalid mdn', 'Negative: Verify request rejected with invalid MDN (less than 10 digits / special chars)',
             'HTTP 400. Invalid MDN format rejected with specific error code.'),
            ('missing required', 'Negative: Verify request rejected when mandatory fields are missing',
             'HTTP 400. Missing field identified in error response.'),
        ]:
            if kw not in existing_text:
                new_tcs.append(_neg(idx, feature_id, title, val, ctx))
                idx += 1; log('[ENRICH]   L3: %s' % title[:50])

        # Layer 6: Upstream failures
        if 'timeout' not in existing_text and 'unavailable' not in existing_text:
            new_tcs.append(_neg(idx, feature_id,
                'Verify system handles upstream timeout/unavailability gracefully',
                'System detects timeout. Error returned. No data corruption. Transaction marked failed.',
                ctx))
            idx += 1; log('[ENRICH]   L6: upstream timeout')

        if 'reject' not in existing_text or ('itmbo' not in existing_text and 'mbo' not in existing_text):
            new_tcs.append(_neg(idx, feature_id,
                'Verify system handles upstream rejection response from ITMBO/MBO',
                'Upstream rejection handled. Error code forwarded to caller. Transaction status updated.',
                ctx))
            idx += 1; log('[ENRICH]   L6: upstream rejection')

    # ── Layer 7: Lifecycle Scenarios (feature-specific) ──
    # From 3941: Cancel Port-Out
    # From 3782/3946: Hotline → Suspend, Hotline → Deactivate, Hotline → Restore
    # From 4033: Change SIM types (OE, OS, DE, DS)
    # From 4110: Change wearable, Transfer wearable
    if is_line:
        if 'cancel' not in existing_text and any(kw in ctx for kw in ['port', 'swap', 'hotline']):
            new_tcs.append(_pos(idx, feature_id,
                'Verify cancel/reversal of the operation',
                'Cancel operation processed successfully. Original state restored.',
                ctx))
            idx += 1; log('[ENRICH]   L7: cancel/reversal')

    # ── Layer 8: Verification ──
    # From ALL samples: Century Report, Transaction History, NBOP
    if is_api:
        if 'transaction history' not in existing_text and 'audit' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Verify transaction recorded in Transaction History with correct details',
                'Transaction History contains entry with timestamp, MDN, operation type, status.',
                ctx))
            idx += 1; log('[ENRICH]   L8: transaction history')

        if 'century' not in existing_text and 'service grouping' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Verify Century Report (Service Grouping) reflects the operation correctly',
                'SERVICE_GROUPING HTML shows correct post-operation state. All fields validated.',
                ctx))
            idx += 1; log('[ENRICH]   L8: century report')

        if 'nbop' not in existing_text and 'portal' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Verify NBOP/Genesis Portal reflects the operation correctly',
                'Portal shows updated state matching the operation result.',
                ctx))
            idx += 1; log('[ENRICH]   L8: portal verification')

    # ── Layer 9: E2E ──
    # From 3941: E2E from trigger to MBO update
    # From Swap MDN: E2E from NBOP to Apollo NE
    if 'end-to-end' not in existing_text and 'e2e' not in existing_text:
        if is_api:
            new_tcs.append(_pos(idx, feature_id,
                'Verify end-to-end flow from trigger through all downstream systems to final verification',
                'Complete E2E: API triggered, all downstream systems updated, Century Report verified, Transaction History recorded, Portal reflects changes.',
                ctx))
            idx += 1; log('[ENRICH]   L9: E2E')

    # ── Cap: Mandatory negatives are NEVER capped. Others capped at 50%. ──
    mandatory_tcs = [tc for tc in new_tcs if hasattr(tc, '_mandatory') and tc._mandatory]
    optional_tcs = [tc for tc in new_tcs if tc not in mandatory_tcs]

    cap = max(5, int(n * 0.5))
    if len(optional_tcs) > cap:
        # Prioritize: E2E > Verification (L8) > Field Validation (L3)
        HIGH_PRIORITY_KEYWORDS = ['end-to-end', 'e2e', 'transaction history', 'century',
                                  'portal', 'nbop', 'audit']
        high_prio = [tc for tc in optional_tcs if any(kw in tc.summary.lower() for kw in HIGH_PRIORITY_KEYWORDS)]
        low_prio = [tc for tc in optional_tcs if tc not in high_prio]
        optional_tcs = high_prio[:cap] + low_prio[:max(0, cap - len(high_prio))]
        optional_tcs = optional_tcs[:cap]
        log('[ENRICH]   Capped optional TCs at %d (50%% of %d) — mandatory negatives preserved' % (cap, n))

    new_tcs = mandatory_tcs + optional_tcs

    log('[ENRICH] Added %d enrichment TCs (total: %d -> %d)' % (len(new_tcs), n, n + len(new_tcs)))
    return new_tcs


# ── Helpers ──

def _is_line_feature(ctx, text):
    kw = ['swap', 'activation', 'activate', 'change', 'port', 'deactivat',
          'suspend', 'hotline', 'reconnect', 'mdn', 'line', 'sim', 'imei']
    return any(k in ctx or k in text for k in kw)

def _is_api_feature(ctx, text):
    kw = ['api', 'http', 'nsl', 'apollo', 'mbo', 'swap', 'activation',
          'change', 'port', 'deactivat', 'suspend', 'hotline', 'trigger']
    return any(k in ctx or k in text for k in kw)
