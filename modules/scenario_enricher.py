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
    """Build a negative TC with real QA-quality steps."""
    from .test_engine import TestCase, TestStep
    # Determine what kind of negative this is for better steps
    title_low = title.lower()
    if 'hotline' in title_low:
        steps = [
            TestStep(1, 'Identify an MDN in Hotlined status from test data pool', 'Hotlined MDN identified'),
            TestStep(2, 'Trigger the API with the Hotlined MDN and valid remaining parameters', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with HTTP 400 and appropriate error code', 'Error response received with clear message'),
            TestStep(4, 'Verify line status unchanged — still Hotlined in DB', 'No data modification occurred'),
        ]
    elif 'suspend' in title_low:
        steps = [
            TestStep(1, 'Identify an MDN in Suspended status from test data pool', 'Suspended MDN identified'),
            TestStep(2, 'Trigger the API with the Suspended MDN and valid remaining parameters', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with HTTP 400 and appropriate error code', 'Error response received with clear message'),
            TestStep(4, 'Verify line status unchanged — still Suspended in DB', 'No data modification occurred'),
        ]
    elif 'deactivat' in title_low:
        steps = [
            TestStep(1, 'Identify a Deactivated/Disconnected MDN from test data pool', 'Deactivated MDN identified'),
            TestStep(2, 'Trigger the API with the Deactivated MDN and valid remaining parameters', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with HTTP 400 and appropriate error code', 'Error response received with clear message'),
            TestStep(4, 'Verify no records created in Transaction History for rejected request', 'No audit trail for rejected operation'),
        ]
    elif 'invalid line' in title_low or 'lineid' in title_low:
        steps = [
            TestStep(1, 'Prepare request with non-existent LineId (e.g., 999999999)', 'Invalid LineId prepared'),
            TestStep(2, 'Trigger the API with invalid LineId and valid remaining parameters', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with ERR20 — Line Id not found', 'ERR20 returned with descriptive message'),
            TestStep(4, 'Verify no partial records created in DB', 'DB state clean — no orphaned records'),
        ]
    elif 'invalid account' in title_low or 'accountid' in title_low:
        steps = [
            TestStep(1, 'Prepare request with non-existent AccountId', 'Invalid AccountId prepared'),
            TestStep(2, 'Trigger the API with invalid AccountId and valid remaining parameters', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with appropriate error code', 'Error response received'),
            TestStep(4, 'Verify no data corruption in DB', 'DB state unchanged'),
        ]
    elif 'mismatch' in title_low:
        steps = [
            TestStep(1, 'Prepare request where LineId belongs to Account A but AccountId is Account B', 'Mismatched IDs prepared'),
            TestStep(2, 'Trigger the API with mismatched LineId and AccountId', 'API request sent'),
            TestStep(3, 'Verify NSL rejects with ERR161 — LineId and AccountId mismatch', 'ERR161 returned'),
            TestStep(4, 'Verify neither account is modified', 'Both accounts unchanged in DB'),
        ]
    elif 'rollback' in title_low:
        # Feature-specific rollback steps
        if 'swap' in title_low:
            _restore = 'original ICCID/IMEI associations'
        elif 'bcd' in title_low or 'dpfo' in title_low:
            _restore = 'original BCD/DPFO reset day'
        elif 'sync' in title_low or 'key info' in title_low:
            _restore = 'original subscriber/account data'
        elif 'port' in title_low:
            _restore = 'original MDN and port status'
        elif 'hotline' in title_low:
            _restore = 'original line status'
        else:
            _restore = 'original subscriber state'
        _op_name = re.sub(r'(?i)^.*?rollback\s*(?:failure\s*)?(?:for\s*)?(?:of\s*)?', '', title).strip()
        _op_name = _op_name[:50] if _op_name else fname
        steps = [
            TestStep(1, 'Trigger %s and simulate mid-operation failure' % _op_name[:50], 'Operation fails during processing'),
            TestStep(2, 'Verify NSL detects failure and initiates rollback', 'Rollback triggered — inconsistency detected'),
            TestStep(3, 'Verify %s are restored to pre-operation values' % _restore, '%s restored correctly' % _restore.capitalize()),
            TestStep(4, 'Verify Transaction History shows rollback entry', 'Rollback logged with correct status and timestamp'),
        ]
    else:
        chain = get_step_chain(title, val, ctx)
        steps = [TestStep(i+1, s, e) for i, (s, e) in enumerate(chain)]

    return TestCase(sno=str(idx),
        summary='TC%03d_%s_%s' % (idx, fid, title),
        description=val, preconditions='1.\tSystem in ready state\n2.\tPrepare error condition as per scenario',
        steps=steps, story_linkage=fid, label=fid, category='Negative')

def _pos(idx, fid, title, val, ctx):
    """Build a positive TC with real QA-quality steps."""
    from .test_engine import TestCase, TestStep
    title_low = title.lower()
    if 'century' in title_low or 'service grouping' in title_low:
        steps = [
            TestStep(1, 'Complete the primary operation successfully', 'Operation completed with SUCC00'),
            TestStep(2, 'Navigate to Century Report / Service Grouping', 'Report accessible'),
            TestStep(3, 'Search using Root Transaction ID or MDN', 'Records found'),
            TestStep(4, 'Verify all fields match expected post-operation state', 'All data correct'),
        ]
    elif 'transaction history' in title_low:
        steps = [
            TestStep(1, 'Complete the primary operation successfully', 'Operation completed'),
            TestStep(2, 'Query Transaction History for the MDN', 'Transaction History accessible'),
            TestStep(3, 'Verify entry exists with correct timestamp, type, MDN, status', 'Entry found with correct details'),
            TestStep(4, 'Verify no duplicate or orphaned entries', 'Clean transaction log'),
        ]
    elif 'nbop' in title_low or 'portal' in title_low:
        steps = [
            TestStep(1, 'Complete the primary operation successfully', 'Operation completed'),
            TestStep(2, 'Login to NBOP Portal and navigate to subscriber details', 'Portal accessible'),
            TestStep(3, 'Verify subscriber details reflect post-operation state', 'Portal shows correct data'),
            TestStep(4, 'Verify no stale/cached data displayed', 'Data is fresh and accurate'),
        ]
    elif 'e2e' in title_low or 'end-to-end' in title_low:
        steps = [
            TestStep(1, 'Set up subscriber with active line in TMO', 'Subscriber ready'),
            TestStep(2, 'Trigger the complete operation flow from API to TMO response', 'Full flow executed'),
            TestStep(3, 'Verify all downstream systems updated (MBO, Syniverse, KAFKA)', 'Downstream systems consistent'),
            TestStep(4, 'Verify Century Report, NBOP MIG tables, Transaction History', 'Full audit trail verified'),
            TestStep(5, 'Verify TMO Genesis portal reflects the change', 'Carrier portal updated'),
        ]
    elif 'cancel' in title_low or 'reversal' in title_low:
        steps = [
            TestStep(1, 'Complete the primary operation successfully', 'Operation completed'),
            TestStep(2, 'Trigger cancel/reversal with valid parameters', 'Cancel request sent'),
            TestStep(3, 'Verify system accepts cancellation and restores original state', 'Original state restored'),
            TestStep(4, 'Verify Century Report logs the cancellation', 'Cancellation audit trail complete'),
        ]
    else:
        chain = get_step_chain(title, val, ctx)
        steps = [TestStep(i+1, s, e) for i, (s, e) in enumerate(chain)]

    return TestCase(sno=str(idx),
        summary='TC%03d_%s_%s' % (idx, fid, title),
        description=val, preconditions='1.\tActive TMO subscriber line\n2.\tSystem in ready state',
        steps=steps, story_linkage=fid, label=fid, category='Happy Path')


def enrich_scenarios(test_cases, feature_id, feature_context, log=print, feature_name=''):
    """Analyze existing TCs, identify gaps across 9 universal layers, fill smartly."""
    fname = feature_name or feature_id  # short feature name for TC titles
    # Sanitize: strip % to prevent string formatting crashes
    fname = fname.replace('%', '')

    existing_text = ' '.join([
        tc.summary.lower() + ' ' + tc.description.lower() + ' ' +
        ' '.join(s.summary.lower() + ' ' + s.expected.lower() for s in tc.steps)
        for tc in test_cases
    ])

    n = len(test_cases)
    new_tcs = []
    idx = n + 1
    ctx = feature_context.lower()

    # Detect if this is a UI/portal feature — suppress API-heavy enrichment
    _is_ui_feature = (
        'nbop to implement' in ctx or
        'nbop screen' in ctx or
        'nbop ui' in ctx or
        ('nbop' in ctx and 'screen' in ctx) or
        ('nbop' in ctx and 'menu' in ctx) or
        ('nbop' in ctx and 'navigation' in ctx) or
        ('nbop' in ctx and 'display' in ctx)
    )

    if _is_ui_feature:
        is_line = False  # Suppress line-level API negatives for UI features
        is_api = False   # Suppress API-level enrichment for UI features
        log('[ENRICH] UI/Portal feature detected — suppressing API-heavy enrichment')
    else:
        is_line = _is_line_feature(ctx, existing_text)
        is_api = _is_api_feature(ctx, existing_text)

    log('[ENRICH] Analyzing %d existing TCs across 9 layers...' % n)

    # ════════════════════════════════════════════════════════════════
    # CONTRACT-DRIVEN ENRICHMENT — consult global integration contract
    # This replaces hardcoded per-feature checks with contract lookups
    # ════════════════════════════════════════════════════════════════
    from .integration_contract import resolve_operation, get_must_not_call_systems, get_syniverse_assertion
    _contract = resolve_operation(fname, description=ctx, ac_text=ctx)
    if _contract:
        log('[ENRICH] Contract: "%s" (category=%s)' % (_contract.operation, _contract.category))

        # ── Contract Layer: "MUST NOT CALL" assertions (mandatory) ──
        _no_call_systems = get_must_not_call_systems(_contract)
        # Use TC SUMMARIES only for dedup — not full text. Full text has too many
        # false positives (e.g., "Syniverse" appears in descriptions of features
        # that DO call Syniverse, making "no syniverse" match incorrectly)
        _existing_summaries_lower = ' '.join(tc.summary.lower() for tc in test_cases)
        for sys_obj in _no_call_systems:
            _sys_lower = sys_obj.name.lower()
            _has_no_call = ('no %s' % _sys_lower in _existing_summaries_lower or
                            '%s is not' % _sys_lower in _existing_summaries_lower or
                            '%s not called' % _sys_lower in _existing_summaries_lower or
                            'not call %s' % _sys_lower in _existing_summaries_lower)
            if not _has_no_call:
                _syn_assert = get_syniverse_assertion(_contract)
                _reason = _syn_assert.get('condition', '') if _sys_lower == 'syniverse' else ''
                tc = _pos(idx, feature_id,
                    'Verify %s is NOT called during %s' % (sys_obj.name, fname),
                    'Trigger %s and verify via %s that NO %s outbound call is made. %s' % (
                        fname, sys_obj.verify_via, sys_obj.name, _reason),
                    ctx)
                tc._mandatory = True
                new_tcs.append(tc)
                idx += 1
                log('[ENRICH]   CONTRACT-MANDATORY: No %s call for %s' % (sys_obj.name, _contract.operation))

        # ── Contract Layer: Granular error handling for "MUST CALL" systems ──
        from .integration_contract import get_must_call_systems
        _must_call = get_must_call_systems(_contract)
        for sys_obj in _must_call:
            for code in sys_obj.error_codes[:3]:  # Cap at 3 most important codes per system
                if code not in existing_text:
                    new_tcs.append(_neg(idx, feature_id,
                        'Negative: Verify %s handles %s HTTP %s' % (fname, sys_obj.name, code),
                        'Trigger %s where %s returns HTTP %s. '
                        'Verify NSL handles gracefully — no data corruption.' % (fname, sys_obj.name, code),
                        ctx))
                    idx += 1
                    log('[ENRICH]   CONTRACT: %s %s error handling' % (sys_obj.name, code))

        # ── Contract Layer: Transaction type coverage ──
        if len(_contract.transaction_types) > 1:
            existing_summaries = ' '.join(tc.summary.lower() for tc in test_cases)
            for tx_type in _contract.transaction_types:
                if tx_type.lower() not in existing_summaries:
                    new_tcs.append(_pos(idx, feature_id,
                        'Verify %s for transaction type %s' % (fname, tx_type),
                        'Trigger %s with transaction type %s. Verify correct behavior per contract.' % (fname, tx_type),
                        ctx))
                    idx += 1
                    log('[ENRICH]   CONTRACT: Transaction type %s coverage' % tx_type)

        # ── Contract Layer: Mandatory negatives that dedup often misses ──
        # These are P2 gaps that the contract mandates but keyword dedup filters out
        _contract_mandatory_negs = _contract.mandatory_negatives
        _existing_summaries_for_neg = ' '.join(tc.summary.lower() for tc in test_cases + new_tcs)
        for neg_desc in _contract_mandatory_negs:
            _neg_key_words = [w.lower() for w in neg_desc.replace('—', ' ').split() if len(w) > 3][:4]
            _covered = sum(1 for w in _neg_key_words if w in _existing_summaries_for_neg)
            if _covered < len(_neg_key_words) * 0.5:
                tc = _neg(idx, feature_id,
                    'Negative: %s for %s' % (neg_desc[:70], fname),
                    'Contract-mandated: %s' % neg_desc,
                    ctx)
                tc._mandatory = True
                new_tcs.append(tc)
                idx += 1
                log('[ENRICH]   CONTRACT-MANDATORY-NEG: %s' % neg_desc[:60])

        # ── Contract Layer: Audit-History Invariant Checklist ──
        # Every line-state or device-change operation MUST have an audit trail TC
        if _contract.category in ('line_state', 'device_change', 'sync', 'integration'):
            _has_audit = any(kw in _existing_summaries_for_neg
                            for kw in ['audit', 'transaction history', 'history table',
                                       'audit trail', 'invariant'])
            if not _has_audit:
                from .test_engine import TestCase as _TC, TestStep as _TS
                _audit_tc = _TC(
                    sno=str(idx),
                    summary='TC%03d_%s_Audit-History Invariant: Verify complete audit trail for %s' % (idx, feature_id, fname),
                    description='After %s, verify the audit-history invariant checklist:\n'
                                '1. TRANSACTION_HISTORY has entry with correct type, timestamp, MDN, status\n'
                                '2. LINE_HISTORY reflects state change (if any)\n'
                                '3. EVENT_STATUS updated for async operations\n'
                                '4. No orphaned or duplicate audit records\n'
                                '5. Root Transaction ID links all related sub-transactions' % fname,
                    preconditions='1.\t%s operation completed successfully\n2.\tDB access available for verification' % fname,
                    story_linkage=feature_id, label=feature_id, category='Happy Path',
                    steps=[
                        _TS(1, 'Complete %s operation successfully' % fname,
                            'Operation returns success response'),
                        _TS(2, 'Query TRANSACTION_HISTORY table for the Root Transaction ID',
                            'Entry found with correct transaction type, timestamp, MDN, and SUCC status'),
                        _TS(3, 'Query LINE_HISTORY table for the MDN',
                            'State change recorded with before/after values matching operation'),
                        _TS(4, 'Verify no duplicate or orphaned audit records exist',
                            'Exactly one TRANSACTION_HISTORY entry per operation. No orphans'),
                        _TS(5, 'Verify Root Transaction ID links all sub-transactions in Century Report',
                            'All inbound/outbound calls traceable via single Root Transaction ID'),
                    ])
                _audit_tc._mandatory = True
                new_tcs.append(_audit_tc)
                idx += 1
                log('[ENRICH]   CONTRACT-MANDATORY: Audit-History Invariant Checklist')

    # ════════════════════════════════════════════════════════════════
    # LAYER 0b: Explicit "NO Syniverse" for Hotline/Remove Hotline
    # This runs REGARDLESS of feature type — even for integration features
    # that mention Syniverse heavily, Hotline flows must NOT call Syniverse
    # ════════════════════════════════════════════════════════════════
    _all_summaries = ' '.join(tc.summary.lower() for tc in test_cases + new_tcs)
    if any(kw in ctx for kw in ['hotline', 'remove hotline', 'suspend', 'restore']):
        _has_explicit_no_syn = ('no syniverse' in _all_summaries or
                                'syniverse is not' in _all_summaries or
                                'syniverse not called' in _all_summaries)
        if not _has_explicit_no_syn:
            from .test_engine import TestCase as _TC2, TestStep as _TS2
            _no_syn_tc = _TC2(
                sno=str(idx),
                summary='TC%03d_%s_Verify Syniverse is NOT called for Hotline/Suspend/Restore in %s' % (idx, feature_id, fname),
                description='For Hotline, Remove Hotline, Suspend, and Restore operations within %s, '
                            'verify that NO Syniverse outbound call (CreateSubscriber/RemoveSubscriber/SwapIMSI) '
                            'is triggered. These are internal state changes only.' % fname,
                preconditions='1.\tActive TMO subscriber line\n2.\tCentury Report / logs accessible',
                story_linkage=feature_id, label=feature_id, category='Happy Path',
                steps=[
                    _TS2(1, 'Trigger Hotline/Suspend/Restore operation for an active subscriber',
                         'Operation accepted and processed successfully'),
                    _TS2(2, 'Check Century Report for Syniverse outbound calls',
                         'NO Syniverse CreateSubscriber/RemoveSubscriber/SwapIMSI call found'),
                    _TS2(3, 'Verify ITMBO and EMM are notified (but NOT Syniverse)',
                         'ITMBO/EMM received notification. Syniverse NOT contacted'),
                    _TS2(4, 'Verify line status changed correctly without Syniverse involvement',
                         'Line status updated. Syniverse subscriber state unchanged'),
                ])
            _no_syn_tc._mandatory = True
            new_tcs.append(_no_syn_tc)
            idx += 1
            log('[ENRICH]   MANDATORY: Explicit NO Syniverse for Hotline/Suspend/Restore')

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
            ('hotline', ['hotline', 'hotlined'],
             'Negative: Validate %s rejected for Hotlined MDN' % fname,
             'Trigger %s API with a Hotlined MDN. System must reject with HTTP 400 error.' % fname),
            ('suspended', ['suspend', 'suspended'],
             'Negative: Validate %s rejected for Suspended MDN' % fname,
             'Trigger %s API with a Suspended MDN. System must reject with HTTP 400 error.' % fname),
            ('deactivated', ['deactiv', 'deactivated', 'disconnected'],
             'Negative: Validate %s rejected for Deactivated MDN' % fname,
             'Trigger %s API with a Deactivated MDN. System must reject with HTTP 400 error.' % fname),
            ('invalid_lineid', ['invalid line', 'invalid lineid', 'non-existent line'],
             'Negative: Validate %s rejected with invalid LineId' % fname,
             'Trigger %s API with an invalid LineId. System must reject with HTTP 400.' % fname),
            ('invalid_account', ['invalid account', 'non-existent account'],
             'Negative: Validate %s rejected with invalid AccountId' % fname,
             'Trigger %s API with an invalid AccountId. System must reject with HTTP 400.' % fname),
            ('mismatch_ids', ['mismatch', 'lineid and accountid'],
             'Negative: Validate %s rejected when LineId and AccountId mismatch' % fname,
             'Trigger %s API with mismatched LineId and AccountId. System must reject with ERR161.' % fname),
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
                'Negative: Validate %s rollback failure (rollback itself fails)' % fname,
                'Simulate failure during %s rollback. System must detect inconsistent state, '
                'log the failure, and mark transaction for manual review.' % fname,
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
    # FINE-TUNE: Syniverse-specific enrichment for integration features
    _is_syniverse = any(kw in ctx for kw in ['syniverse', 'createsubscriber', 'removesubscriber',
                                              'swapimsi', 'create subscriber', 'remove subscriber'])
    _is_hotline = any(kw in ctx for kw in ['hotline', 'remove hotline'])

    # Layer 5b: Syniverse "no call" assertions for Hotline/Remove Hotline
    if _is_hotline and 'no syniverse' not in existing_text and 'syniverse is not' not in existing_text:
        tc = _pos(idx, feature_id,
            'Verify Syniverse is NOT called during %s' % fname,
            'Trigger %s and verify via Century Report that NO Syniverse outbound call '
            '(CreateSubscriber/RemoveSubscriber/SwapIMSI) is made. '
            'Hotline operations do not affect Syniverse subscriber state.' % fname,
            ctx)
        tc._mandatory = True  # This is a critical assertion from manual suite
        new_tcs.append(tc)
        idx += 1
        log('[ENRICH]   L5b-MANDATORY: No Syniverse call for Hotline')

    # Layer 5c: Port-In (SP) variant for Change Rate Plan / Port-In features
    _is_portin = any(kw in ctx for kw in ['port-in', 'port in', 'portin', 'change rate'])
    if _is_portin and _is_syniverse and 'sp ' not in existing_text and 'transactiontype sp' not in existing_text:
        new_tcs.append(_pos(idx, feature_id,
            'Verify %s with Port-In (SP) transactionType' % fname,
            'Trigger %s with transactionType=SP. Verify Syniverse CreateSubscriber '
            'is called with correct parameters for SP flow.' % fname,
            ctx))
        idx += 1
        log('[ENRICH]   L5c: Port-In SP variant')

    # Layer 5d: Syniverse granular error handling (401, 403, 404)
    if _is_syniverse:
        for code, desc in [('401', 'Unauthorized'), ('403', 'Forbidden'), ('404', 'Not Found')]:
            if code not in existing_text:
                new_tcs.append(_neg(idx, feature_id,
                    'Negative: Verify %s handles Syniverse HTTP %s %s' % (fname, code, desc),
                    'Trigger %s where Syniverse returns HTTP %s. '
                    'Verify NSL handles gracefully — no data corruption.' % (fname, code),
                    ctx))
                idx += 1
                log('[ENRICH]   L5d: Syniverse %s handling' % code)

    if is_api:
        # Layer 3 (remaining): Field validation not covered by mandatory checklist
        for kw, title, val in [
            ('invalid mdn', 'Negative: Validate %s rejected with invalid MDN format' % fname,
             'HTTP 400. Invalid MDN format rejected with specific error code.'),
            ('missing required', 'Negative: Validate %s rejected when mandatory fields missing' % fname,
             'HTTP 400. Missing field identified in error response.'),
        ]:
            if kw not in existing_text:
                new_tcs.append(_neg(idx, feature_id, title, val, ctx))
                idx += 1; log('[ENRICH]   L3: %s' % title[:50])

        # Layer 6: Upstream failures
        if 'timeout' not in existing_text and 'unavailable' not in existing_text:
            new_tcs.append(_neg(idx, feature_id,
                'Negative: Validate %s handles upstream timeout gracefully' % fname,
                'System detects timeout during %s. Error returned. No data corruption.' % fname,
                ctx))
            idx += 1; log('[ENRICH]   L6: upstream timeout')

        if 'reject' not in existing_text or ('itmbo' not in existing_text and 'mbo' not in existing_text):
            # Skip ITMBO/MBO rejection for CDR/Mediation features — they don't use channels
            _is_cdr = any(kw in ctx.lower() for kw in ['cdr', 'mediation', 'prr', 'ild', 'roaming', 'country code'])
            if not _is_cdr:
                new_tcs.append(_neg(idx, feature_id,
                    'Negative: Validate %s handles upstream rejection from ITMBO/MBO' % fname,
                    'Upstream rejection during %s handled. Error code forwarded.' % fname,
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
                'Validate cancel/reversal of %s' % fname,
                'Cancel %s processed successfully. Original state restored.' % fname,
                ctx))
            idx += 1; log('[ENRICH]   L7: cancel/reversal')

    # ── Layer 8: Verification ──
    # From ALL samples: Century Report, Transaction History, NBOP
    if is_api:
        if 'transaction history' not in existing_text and 'audit' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Validate %s recorded in Transaction History' % fname,
                'Transaction History contains %s entry with timestamp, MDN, status.' % fname,
                ctx))
            idx += 1; log('[ENRICH]   L8: transaction history')

        if 'century' not in existing_text and 'service grouping' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Validate Century Report reflects %s correctly' % fname,
                'SERVICE_GROUPING HTML shows correct post-%s state.' % fname,
                ctx))
            idx += 1; log('[ENRICH]   L8: century report')

        if 'nbop' not in existing_text and 'portal' not in existing_text:
            new_tcs.append(_pos(idx, feature_id,
                'Validate NBOP Portal reflects %s correctly' % fname,
                'Portal shows updated state after %s.' % fname,
                ctx))
            idx += 1; log('[ENRICH]   L8: portal verification')

    # ── Layer 9: E2E ──
    # From 3941: E2E from trigger to MBO update
    # From Swap MDN: E2E from NBOP to Apollo NE
    if 'end-to-end' not in existing_text and 'e2e' not in existing_text:
        if is_api:
            new_tcs.append(_pos(idx, feature_id,
                'E2E: Validate complete %s flow from trigger to final verification' % fname,
                'Complete E2E %s: API triggered, downstream systems updated, Century Report verified.' % fname,
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
