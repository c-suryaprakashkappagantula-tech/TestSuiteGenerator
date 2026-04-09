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

    # ── Layer 3: Field Validation Negatives ──
    # From 3948: Invalid MDN/LineId/Account/Format/Missing fields
    # From 3926: ICCID/IMEI length checks
    # From 4109: MDN/Username/ICCID length checks
    if is_api:
        for kw, title, val in [
            ('invalid mdn', 'Verify request rejected with invalid MDN (less than 10 digits / special chars)',
             'HTTP 400. Invalid MDN format rejected with specific error code.'),
            ('missing required', 'Verify request rejected when mandatory fields are missing',
             'HTTP 400. Missing field identified in error response.'),
            ('invalid line', 'Verify request rejected with invalid/non-existent LineId',
             'HTTP 400. Invalid LineId rejected.'),
            ('invalid account', 'Verify request rejected with invalid/non-existent AccountId',
             'HTTP 400. Invalid AccountId rejected.'),
            ('format validation', 'Verify request rejected with invalid field format (length, type, special chars)',
             'HTTP 400. Format validation error with specific field identified.'),
        ]:
            if kw not in existing_text:
                new_tcs.append(_neg(idx, feature_id, title, val, ctx))
                idx += 1; log('[ENRICH]   L3: %s' % title[:50])

    # ── Layer 4: Line Status Negatives ──
    # From 3941: Deactivated/Suspended/Hotlined
    # From 4110: Suspend/Hotline/Deactive
    # From 3782: Non-active status checks
    if is_line:
        for kw, title, val in [
            ('deactivat', 'Verify system handles request for a deactivated/disconnected line',
             'System rejects or handles gracefully. Appropriate error for deactivated line.'),
            ('suspend', 'Verify system handles request for a suspended line',
             'System rejects or handles gracefully. Appropriate error for suspended line.'),
            ('hotline', 'Verify system handles request for a hotlined line',
             'System rejects or handles gracefully. Appropriate error for hotlined line.'),
        ]:
            if kw not in existing_text:
                new_tcs.append(_neg(idx, feature_id, title, val, ctx))
                idx += 1; log('[ENRICH]   L4: %s' % title[:50])

    # ── Layer 5: Mismatch Negatives ──
    # From 3948: LineId_MDN_Mismatch, LineId_AccountId_Mismatch
    # From 3941: requestNumber_Mismatch, Lineid_Account_Id_Mismatch
    if is_api:
        for kw, title, val in [
            ('mismatch', 'Verify request rejected when LineId and AccountId do not match',
             'HTTP 400. ERR161 - accountNumber and lineId mismatch.'),
            ('mdn mismatch', 'Verify request rejected when MDN does not belong to the given account',
             'HTTP 400. MDN and Account mismatch error.'),
        ]:
            if kw not in existing_text:
                new_tcs.append(_neg(idx, feature_id, title, val, ctx))
                idx += 1; log('[ENRICH]   L5: %s' % title[:50])

    # ── Layer 6: Upstream Failures ──
    # From 3941: ITMBO rejects (Invalid PIN, Invalid Account Number)
    # From 3941: Notification timeout (PO not triggered in 50 secs)
    if is_api:
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

    # ── Cap at 50% of existing ──
    cap = max(5, int(n * 0.5))
    if len(new_tcs) > cap:
        new_tcs = new_tcs[:cap]
        log('[ENRICH]   Capped at %d (50%% of %d)' % (cap, n))

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
