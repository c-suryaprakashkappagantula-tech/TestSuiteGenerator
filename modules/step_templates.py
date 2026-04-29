"""
step_templates.py — Domain-specific step chain templates.
Based on real pipeline flows from TMO_API_Dashboard_V6.py
and actual test cases from sample files.
"""
import re


def get_step_chain(sc_title, sc_validation, feature_context, feature_type=''):
    """Return list of (step_summary, expected_result) tuples based on scenario type.

    When feature_type='ui_portal', ALL scenarios route to UI step templates.
    This prevents API steps (OAuth, Century Report, APOLLO_NE) from leaking
    into pure NBOP UI features like Change BCD, Remove Hotline, etc.
    """
    t = (sc_title + ' ' + (sc_validation or '')).lower()
    ctx = feature_context.lower()

    # ════════════════════════════════════════════════════════════════
    # GATE: ui_portal features ALWAYS get UI steps — no exceptions.
    # The classification in tc_templates.classify_feature() is the
    # single source of truth.  When it says ui_portal, we obey.
    # ════════════════════════════════════════════════════════════════
    if feature_type == 'ui_portal':
        if _is_negative(t):
            return _ui_negative_steps(sc_title, sc_validation)
        # UI-based Sync Subscriber (via NBOP portal)
        if _is_ui_sync(t, ctx):
            return _ui_sync_subscriber_steps(sc_title, sc_validation, t)
        # UI-based Network Reset (via NBOP portal)
        if _is_ui_network_reset(t, ctx):
            return _ui_network_reset_steps(sc_title, sc_validation, t)
        # All other ui_portal scenarios → UI flow steps
        return _ui_flow_steps(sc_title, sc_validation)

    # If feature is CDR/notification type, route to notification steps by default
    if feature_type in ('notification',):
        if _is_negative(t):
            return _negative_steps(sc_title, sc_validation, t)
        return _notification_steps(sc_title, sc_validation)

    # Check negative/error FIRST — but Sync Key Info negatives get their own handler
    if _is_negative(t):
        # Sync Key Info error scenarios should use Sync Key Info steps, not generic negative
        if _is_sync_key_info(t, ctx):
            return _sync_key_info_steps(sc_title, sc_validation, t)
        if _is_ui_flow(t, ctx):
            return _ui_negative_steps(sc_title, sc_validation)
        return _negative_steps(sc_title, sc_validation, t)
    if _is_rollback(t):
        return _rollback_steps(sc_title, sc_validation, t)

    # Inquiry/query features — check BEFORE UI flow
    # because inquiry scenarios mention "query", "inquiry" which should get
    # API inquiry steps, not UI visibility steps
    if _is_inquiry(t, ctx) and not _is_ui_flow(t, ctx):
        return _inquiry_steps(sc_title, sc_validation, t)

    # UI-based Sync Subscriber (via NBOP portal) — check BEFORE generic
    # _is_ui_flow because titles contain "NBOP" which would match UI flow
    if _is_ui_sync(t, ctx):
        return _ui_sync_subscriber_steps(sc_title, sc_validation, t)

    # UI-based Network Reset (via NBOP portal) — check BEFORE generic UI flow
    if _is_ui_network_reset(t, ctx):
        return _ui_network_reset_steps(sc_title, sc_validation, t)

    # UI flow takes priority — prevents API templates from firing on
    # NBOP features that mention "activate", "port", "hotline" etc.
    if _is_ui_flow(t, ctx):
        return _ui_flow_steps(sc_title, sc_validation)

    # Kafka/BI event features — check BEFORE specific operations
    # because titles like "activate-subscriber API includes networkProvider in BI Kafka"
    # contain "activate" which would match activation template
    if _is_kafka_event(t, ctx):
        return _kafka_event_steps(sc_title, sc_validation)

    # Sync Key Info — must check BEFORE Sync Subscriber because both contain "sync"
    # Sync Key Info is about account/key data sync (externalAccountNumber, MBO, NE, EMM, CM)
    if _is_sync_key_info(t, ctx):
        return _sync_key_info_steps(sc_title, sc_validation, t)

    # Sync Subscriber — must check BEFORE activation/deactivation/hotline/syniverse
    # because sync scenarios mention state changes that would match those templates
    if _is_sync_subscriber(t, ctx):
        return _sync_subscriber_steps(sc_title, sc_validation, t)

    # Network Reset — API-based (must check before generic API flow)
    if _is_network_reset(t, ctx):
        return _network_reset_steps(sc_title, sc_validation, t)

    # Syniverse integration flows (must check before generic API)
    if _is_syniverse_flow(t, ctx):
        return _syniverse_integration_steps(sc_title, sc_validation, t)
    if _is_hotline_flow(t, ctx):
        return _hotline_steps(sc_title, sc_validation, t)
    if _is_remove_hotline_flow(t, ctx):
        return _remove_hotline_steps(sc_title, sc_validation, t)

    # Then specific feature types
    if _is_swap_mdn(t, ctx):
        return _swap_mdn_steps(sc_title, sc_validation, t)
    if _is_activation(t, ctx):
        return _activation_steps(sc_title, sc_validation, t)
    if _is_change_sim(t, ctx):
        return _change_sim_steps(sc_title, sc_validation, t)
    if _is_change_bcd(t, ctx):
        return _change_bcd_steps(sc_title, sc_validation, t)
    if _is_change_rateplan(t, ctx):
        return _change_rateplan_steps(sc_title, sc_validation, t)
    if _is_change_feature(t, ctx):
        return _change_feature_steps(sc_title, sc_validation, t)
    if _is_report(t, ctx):
        return _report_steps(sc_title, sc_validation)
    if _is_inquiry(t, ctx):
        return _inquiry_steps(sc_title, sc_validation, t)
    if _is_notification(t, ctx):
        return _notification_steps(sc_title, sc_validation)
    if _is_api_flow(t, ctx):
        return _api_flow_steps(sc_title, sc_validation)
    if _is_deactivation(t, ctx):
        return _deactivation_steps(sc_title, sc_validation, t)

    return _default_workflow_steps(sc_title, sc_validation)


# ── Detection helpers ──

def _is_negative(t):
    return any(kw in t for kw in ['fail', 'invalid', 'reject', 'error', 'unauthorized',
                                   'mismatch', 'null', 'not in active', 'not exist',
                                   'expires', 'schema validation'])

def _is_rollback(t):
    return any(kw in t for kw in ['rollback', 'restore original', 'revert'])

def _is_swap_mdn(t, ctx):
    return 'swap' in t and ('mdn' in t or 'esim' in t or 'psim' in t or 'swap' in ctx)

def _is_activation(t, ctx):
    return any(kw in t for kw in ['activat', 'port-in']) and 'deactivat' not in t

def _is_deactivation(t, ctx):
    return 'deactivat' in t or 'disconnect' in t

def _is_change_sim(t, ctx):
    return 'change sim' in t or ('change' in t and ('iccid' in t or 'sim' in ctx))

def _is_change_bcd(t, ctx):
    return 'bcd' in t or 'dpfo' in t or 'bill cycle' in t or 'reset day' in t

def _is_change_rateplan(t, ctx):
    return 'rateplan' in t or 'rate plan' in t or 'plan code' in t

def _is_change_feature(t, ctx):
    return 'change feature' in t or 'add feature' in t or 'remove feature' in t or 'reset feature' in t

def _is_syniverse_flow(t, ctx):
    """Detect Syniverse integration scenarios — CreateSubscriber, RemoveSubscriber, SwapIMSI, etc."""
    return any(kw in t for kw in ['syniverse', 'createsubscriber', 'removesubscriber',
                                   'swapimsi', 'create subscriber', 'remove subscriber',
                                   'swap imsi']) or \
           ('syniverse' in ctx and any(kw in t for kw in ['activat', 'deactivat', 'change',
                                                           'port-in', 'port in', 'swap']))

def _is_hotline_flow(t, ctx):
    """Detect Hotline flows — these should NOT trigger Syniverse calls."""
    return ('hotline' in t and 'remove' not in t and 'remove_hotline' not in t and
            'dehotline' not in t)

def _is_remove_hotline_flow(t, ctx):
    """Detect Remove Hotline flows — these should NOT trigger Syniverse calls."""
    return ('remove hotline' in t or 'remove_hotline' in t or 'dehotline' in t or
            ('hotline' in t and 'remove' in t))

def _is_report(t, ctx):
    return any(kw in t for kw in ['report', 'column', 'csv', 'differential', 'batch', '.gz'])

def _is_sync_subscriber(t, ctx):
    """Detect Sync Subscriber (YL/YD/YM/YP/PL) scenarios — NOT Sync Key Info."""
    return any(kw in t for kw in ['sync subscriber', 'sync sub', 'yl sync', 'yd sync',
                                   'ym sync', 'yp sync', 'pl sync', 'sync line',
                                   'sync with network']) or \
           (('yl ' in t or 'yd ' in t or 'ym ' in t or 'yp ' in t or 'pl ' in t) and
            'sync' in (t + ' ' + ctx))

def _is_sync_key_info(t, ctx):
    """Detect Sync Key Info (YK) scenarios — account/key data sync, NOT state change."""
    # Check in title+validation text
    in_text = any(kw in t for kw in ['sync key info', 'sync key', 'yk sync', 'yk_sync',
                                      'sync_key_info', 'key info', 'key_info',
                                      'externalaccountnumber', 'external account',
                                      'accountnumber', 'account update',
                                      'account creation', 'account number',
                                      'get line details', 'mbo retrieve'])
    # Also check if the feature context is Sync Key Info (covers error scenarios
    # where the title only mentions ERR codes, not the API name)
    in_ctx = any(kw in ctx for kw in ['sync key', 'key info', '4019', 'yk sync', 'yk_sync'])
    return in_text or in_ctx

def _is_network_reset(t, ctx):
    """Detect Network Reset scenarios (API-based)."""
    return any(kw in t for kw in ['network reset', 'reset network', 'network-reset',
                                   'reset-network']) or \
           ('network' in ctx and 'reset' in t)

def _is_ui_network_reset(t, ctx=''):
    """Detect UI-based Network Reset scenarios (trigger via NBOP portal)."""
    return any(kw in t for kw in [
        'network reset via nbop', 'network reset through nbop',
        'reset via nbop', 'reset through nbop',
        'nbop network reset', 'ui: network reset',
        'ui verify: network reset', 'validate network reset through nbop',
        'perform network reset via nbop', 'perform network reset operation via nbop',
    ])

def _is_inquiry(t, ctx):
    """Detect inquiry/query features — read-only operations that return data."""
    return any(kw in t for kw in ['inquiry', 'enquiry', 'query subscriber', 'retrieve device',
                                   'get transaction', 'get order', 'order status',
                                   'sim-info', 'sim info api', 'line info api', 'biller line info',
                                   'device lock status', 'event status check',
                                   'reconnect eligibility', 'esim status query',
                                   'reference number'])

def _is_notification(t, ctx):
    return any(kw in t for kw in ['notification', 'suppress', 'dpfo', 'usage',
                                   'throttle', 'speed reduction'])

def _is_kafka_event(t, ctx):
    """Detect Kafka/BI event features — verified via Century Report EVENT_MESSAGES table."""
    return any(kw in t for kw in ['kafka', 'bi kafka', 'event message', 'networkprovider',
                                   'network provider', 'bi event', 'tmo indicator'])

def _is_ui_flow(t, ctx):
    # Strong UI indicators in the title — always route to UI regardless of channel
    # These are explicit UI visibility/accessibility checks
    _strong_ui = any(kw in t for kw in ['menu is visible', 'menu is accessible', 'menu is displayed',
                                         'visible and accessible', 'screen load', 'page load',
                                         'navigation to', 'navigate to', 'launch nbop',
                                         'login to nbop', 'nbop portal'])
    if _strong_ui:
        return True
    # Standard UI indicators — only route to UI if channel includes NBOP
    _has_nbop = 'nbop' in ctx
    _has_api_only = any(kw in ctx for kw in ['itmbo']) and not _has_nbop
    if _has_api_only:
        return False
    return any(kw in t for kw in ['menu', 'display', 'navigation', 'screen', 'portal',
                                   'nbop', 'visible', 'click', 'dropdown'])

def _is_api_flow(t, ctx):
    return any(kw in t for kw in ['api', 'http', 'endpoint', 'trigger', 'login auth',
                                   'retrieve', 'fetch'])


# ── Step chain templates (from V6 pipeline + real samples) ──

def _swap_mdn_steps(title, validation, t):
    """Swap MDN: Real pipeline from V6 + sample TC.
    PSIM (SM): triggers Syniverse Deregister + Register (company ID 14619 for TMO)
    ESIM (EM/AM): triggers Syniverse Change IMSI only — no Deregister/Register"""
    val = validation or title
    is_esim = 'esim' in t or 'em' in t or 'am' in t
    is_psim = 'psim' in t or 'sm' in t or 'pm' in t

    steps = [
        ('Trigger Swap MDN API request with valid parameters',
         'NSL acknowledges with 200 OK success response'),
        ('Verify NSL triggers "Validate device" outbound call to APOLLO_NE for both lines',
         'APOLLO_NE validates device details for both lines successfully'),
        ('Verify NSL triggers Change SIM with new ICCID to TMO (Line 1)',
         'Change SIM executed successfully for Line 1'),
        ('Verify NSL triggers Change IMEI to TMO (Line 1)',
         'Change IMEI executed successfully for Line 1'),
        ('Verify Async service inbound call received from APOLLO_NE (Line 1)',
         'Async callback received and processed for Line 1'),
    ]
    if is_esim:
        steps.extend([
            ('Verify Download order call with new ICCID allocation returned by NE (Line 1)',
             'New ICCID allocated and download order confirmed for Line 1'),
            ('Verify NSL triggers Syniverse Change IMSI only (Line 1) — no Deregister/Register',
             'Syniverse Change IMSI invoked for Line 1 using company ID 14619. '
             'Deregister and Register NOT invoked — eSIM swap uses Change IMSI only'),
            ('Verify Confirm order call with new ICCID to TMO (Line 1)',
             'Order confirmed for Line 1'),
        ])
    elif is_psim:
        steps.extend([
            ('Verify NSL triggers Syniverse Deregister for old IMSI (Line 1) using company ID 14619',
             'Syniverse Deregister executed for Line 1. Old subscriber removed'),
            ('Verify NSL triggers Syniverse Register for new IMSI (Line 1) using company ID 14619',
             'Syniverse Register executed for Line 1. New subscriber created. '
             'Change IMSI NOT invoked — pSIM swap uses Deregister+Register only'),
        ])
    steps.extend([
        ('Verify NSL triggers Change SIM with new ICCID to TMO (Line 2)',
         'Change SIM executed successfully for Line 2'),
        ('Verify NSL triggers Change IMEI to TMO (Line 2)',
         'Change IMEI executed successfully for Line 2'),
        ('Verify Async service inbound call received from APOLLO_NE (Line 2)',
         'Async callback received and processed for Line 2'),
    ])
    if is_esim:
        steps.extend([
            ('Verify NSL triggers Syniverse Change IMSI only (Line 2) — no Deregister/Register',
             'Syniverse Change IMSI invoked for Line 2 using company ID 14619'),
            ('Verify Confirm order call with new ICCID to TMO (Line 2)',
             'Order confirmed for Line 2'),
        ])
    elif is_psim:
        steps.extend([
            ('Verify NSL triggers Syniverse Deregister + Register for Line 2 using company ID 14619',
             'Syniverse Deregister+Register executed for Line 2'),
        ])
    steps.extend([
        ('Verify NSL updates external systems (Connection Manager, MBO, Syniverse)',
         'All external systems updated with new device/SIM details'),
        ('Verify NSL updates device and SIM details in DB',
         'NSL database reflects swapped ICCID/IMEI for both lines'),
        ('Verify Century Report: Service Grouping + NBOP MIG tables',
         'Service Grouping and NBOP tables show correct post-swap state'),
        ('Verify in TMO Portal: MDN swap reflected correctly',
         'MDN changed successfully in TMO Portal'),
        ('Verify in NBOP: MDN swap reflected correctly',
         val),
        ('Verify Syniverse state via NBOP Line Summary(MNO) for both lines — confirm IMSI and Wholesale Plan',
         'Both lines show correct IMSI in NBOP Line Summary. Wholesale Plan unchanged. '
         'Company ID 14619 used for TMO (not VZW 03930)'),
    ])
    return steps


def _activation_steps(title, validation, t):
    """Activation: V6 pipeline - 7 steps with Comprehensive + NBOP MIG + Syniverse."""
    val = validation or title
    steps = [
        ('Step 1: Validate Device IMEI via API',
         'Device validated. Equipment type and compatibility confirmed'),
        ('Step 2: Trigger Activate Subscriber API (eSIM/pSIM) with device and SIM details',
         'NSL acknowledges activation with 200 OK. Transaction ID generated'),
        ('Step 3: Verify NSL sends activation request to TMO via APOLLO_NE',
         'TMO processes activation. Line status changes to Active'),
        ('Step 3b: Verify NSL triggers Syniverse CreateSubscriber outbound call',
         'Syniverse CreateSubscriber executed with correct IMSI, MDN, and wholesale plan'),
        ('Step 4: Download Century Report (Service Grouping + NBOP Tables)',
         'Century Report downloaded. SERVICE_GROUPING HTML and NBOP tables available'),
        ('Step 4b: Verify NE Portal transactions (APOLLO-NE outbound calls)',
         'NE Portal shows activation transaction completed'),
        ('Step 5: Analyze and Validate Service Grouping HTML',
         'Service Grouping shows correct line status, features, device/SIM details'),
        ('Step 5b: Run Comprehensive Validation (12-layer)',
         'Comprehensive validation PASS. All 12 layers verified'),
        ('Step 6: Validate NBOP MIG Tables (Feature, Device, SIM, Line, Transaction History, Events)',
         'All NBOP MIG tables populated with correct activation data'),
        ('Step 7: Verify in TMO Genesis Portal: subscriber line is active',
         val),
        ('Step 8: Verify Syniverse state via NBOP Line Summary(MNO) — confirm IMSI and Wholesale Plan',
         'NBOP Line Summary shows correct IMSI matching Syniverse CreateSubscriber call. Wholesale Plan assigned correctly'),
    ]
    return steps


def _change_sim_steps(title, validation, t):
    """Change SIM: V6 pipeline - 4 steps + NE Portal + Validate SG."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Trigger Change SIM API with new ICCID and MDN',
         'NSL accepts Change SIM request with 200 OK. Transaction ID generated'),
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded with Change SIM transaction'),
        ('Step 3b: Verify NE Portal transactions (APOLLO-NE outbound calls)',
         'NE Portal shows Change SIM transaction completed'),
        ('Step 4: Validate Service Grouping: verify new ICCID, IMSI updated',
         val),
        ('Verify audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded with correct Change SIM details'),
        ('Verify Syniverse state via NBOP Line Summary(MNO) — confirm new IMSI after SwapIMSI',
         'NBOP Line Summary shows updated IMSI matching new ICCID. Wholesale Plan unchanged'),
    ]


def _change_bcd_steps(title, validation, t):
    """Change BCD: V7 pipeline + sample 3948 (11 steps)."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Initiate Change BCD transaction with requestType = TMO',
         'NSL accepts and processes Change BCD with Response 200'),
        ('Step 3: Verify NSL fetches line/feature information from DB',
         'Correct DB details retrieved and outbound request prepared'),
        ('Step 4: Verify NSL updates BCD for ALL applicable features',
         'Updated BCD sent for every eligible feature — not just the first'),
        ('Step 5: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 5b: In Century Report, verify ALL features listed have the new BCD date',
         'Every feature entry shows updated BCD date — no feature retains the old date'),
        ('Step 5c: Verify NE Portal transactions',
         'NE Portal shows Change BCD transaction completed'),
        ('Step 6: Verify downstream updates complete (NSL DB, Mediation, IT-MBO)',
         'NSL DB, Mediation, IT-MBO updated with new BCD'),
        ('Step 7: Check Genesis Portal for new BCD',
         'New BCD reflected in TMO Genesis'),
        ('Step 8: Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded correctly'),
        ('Step 9: Check events/DPFO notifications triggered for new BCD date',
         val),
    ]


def _change_rateplan_steps(title, validation, t):
    """Change Rateplan: V6 pipeline - 4 steps + NE Portal + Validate."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Trigger Change Rateplan API with new plan code',
         'NSL accepts Change Rateplan with 200 OK. Transaction ID generated'),
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 3b: Verify NE Portal transactions',
         'NE Portal shows rateplan change completed'),
        ('Step 4: Validate Service Grouping: verify new plan code and features updated',
         val),
        ('Verify features added/removed per new plan (mandatory + optional)',
         'Feature set matches new rateplan configuration'),
        ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded correctly'),
    ]


def _change_feature_steps(title, validation, t):
    """Change Feature: V6 pipeline - 3 steps + NE Portal."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Trigger Change Feature API (add/remove/reset) with feature code',
         'NSL accepts Change Feature with 200 OK. Transaction ID generated'),
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 3b: Verify NE Portal transactions',
         'NE Portal shows feature change completed'),
        ('Step 4: Validate Service Grouping: verify feature added/removed correctly',
         val),
        ('Verify feature compatibility rules enforced (non-compatible features blocked)',
         'Feature compatibility validated per business rules'),
    ]


def _deactivation_steps(title, validation, t):
    """Deactivation: V6 pipeline."""
    val = validation or title
    return [
        ('Step 1: Trigger Deactivation API with MDN, Line ID, and agent details',
         'NSL accepts deactivation request with 200 OK'),
        ('Step 2: Verify NSL sends deactivation to TMO via APOLLO_NE',
         'TMO processes deactivation. Line status changes to Deactivated'),
        ('Step 3: Verify Syniverse RemoveSubscriber call is triggered',
         'Syniverse RemoveSubscriber executed — subscriber removed from Syniverse'),
        ('Step 4: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded with deactivation transaction'),
        ('Step 5: Validate Service Grouping: verify line status = Deactivated',
         'Service Grouping shows Deactivated status'),
        ('Verify agent details and deactivation reason captured correctly',
         val),
        ('Verify NBOP reflects deactivated line',
         'NBOP shows line as deactivated'),
        ('Verify Syniverse state via NBOP Line Summary(MNO) — confirm subscriber removed',
         'NBOP Line Summary shows Deactivated status. Syniverse subscriber no longer active'),
    ]


def _hotline_steps(title, validation, t):
    """Hotline: Explicit dual assertion — what happens AND what does NOT happen."""
    val = validation or title
    return [
        ('Step 1: Trigger Hotline API (transactionType=SH) with MDN and Line ID',
         'NSL accepts Hotline request with 200 OK'),
        ('Step 2: Verify NSL sends Hotline request to TMO via APOLLO_NE',
         'TMO processes Hotline. Line status changes to Hotlined'),
        ('Step 3: Verify Syniverse is NOT called — no Register, Deregister, Update, or Change IMSI',
         'NO Syniverse API invoked. Century Report confirms absence of Syniverse interaction. '
         'Hotline is internal state change only — does not affect Syniverse subscriber state'),
        ('Step 4: Verify ITMBO and EMM are notified of Hotline status change',
         'ITMBO and EMM receive Hotline notification with correct payload'),
        ('Step 5: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded with Hotline transaction'),
        ('Step 6: Validate Service Grouping: verify line status = Hotlined',
         'Service Grouping shows Hotlined status'),
        ('Verify NBOP reflects hotlined line — Line Status = HOTLINE',
         val),
    ]


def _remove_hotline_steps(title, validation, t):
    """Remove Hotline: Explicit dual assertion — what happens AND what does NOT happen."""
    val = validation or title
    return [
        ('Step 1: Trigger Remove Hotline API with MDN and Line ID',
         'NSL accepts Remove Hotline request with 200 OK'),
        ('Step 2: Verify NSL sends Remove Hotline request to TMO via APOLLO_NE',
         'TMO processes Remove Hotline. Line status changes back to Active'),
        ('Step 3: Verify Syniverse is NOT called — no Register, Deregister, Update, or Change IMSI',
         'NO Syniverse API invoked. Century Report confirms absence of Syniverse interaction. '
         'Remove Hotline is internal state change only'),
        ('Step 4: Verify ITMBO and EMM are notified of status change back to Active',
         'ITMBO and EMM receive status change notification'),
        ('Step 5: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded with Remove Hotline transaction'),
        ('Step 6: Validate Service Grouping: verify line status = Active',
         'Service Grouping shows Active status restored'),
        ('Verify NBOP reflects active line after Remove Hotline',
         val),
    ]


def _syniverse_integration_steps(title, validation, t):
    """Syniverse integration: CreateSubscriber, RemoveSubscriber, SwapIMSI flows."""
    val = validation or title
    # Determine which Syniverse operation
    if 'createsubscriber' in t or 'create subscriber' in t or ('activat' in t and 'syniverse' in t):
        return [
            ('Step 1: Trigger Activation API with valid subscriber details',
             'NSL accepts activation with 200 OK. Transaction ID generated'),
            ('Step 2: Verify NSL sends activation to TMO via APOLLO_NE',
             'TMO processes activation. Line status changes to Active'),
            ('Step 3: Verify NSL triggers Syniverse CreateSubscriber outbound call',
             'Syniverse CreateSubscriber executed with correct IMSI, MDN, and wholesale plan'),
            ('Step 4: Verify Syniverse responds with success acknowledgment',
             'Syniverse returns success. Subscriber created in Syniverse system'),
            ('Step 5: Download Century Report and verify Syniverse call logged',
             'Century Report shows Syniverse CreateSubscriber call with success status'),
            ('Step 6: Validate Service Grouping shows active line with Syniverse sync',
             val),
            ('Step 7: Verify Syniverse state via NBOP Line Summary(MNO) — confirm IMSI and Wholesale Plan',
             'NBOP Line Summary shows correct IMSI and Wholesale Plan matching CreateSubscriber call'),
        ]
    elif 'removesubscriber' in t or 'remove subscriber' in t or ('deactivat' in t and 'syniverse' in t):
        return [
            ('Step 1: Trigger Deactivation API with MDN and Line ID',
             'NSL accepts deactivation with 200 OK'),
            ('Step 2: Verify NSL sends deactivation to TMO via APOLLO_NE',
             'TMO processes deactivation. Line status changes to Deactivated'),
            ('Step 3: Verify NSL triggers Syniverse RemoveSubscriber outbound call',
             'Syniverse RemoveSubscriber executed — subscriber removed from Syniverse'),
            ('Step 4: Verify Syniverse responds with success acknowledgment',
             'Syniverse returns success. Subscriber removed from Syniverse system'),
            ('Step 5: Download Century Report and verify Syniverse call logged',
             'Century Report shows Syniverse RemoveSubscriber call with success status'),
            ('Step 6: Validate Service Grouping shows deactivated line',
             val),
            ('Step 7: Verify Syniverse state via NBOP Line Summary(MNO) — confirm subscriber removed',
             'NBOP Line Summary shows Deactivated status. IMSI no longer active in Syniverse'),
        ]
    elif 'swapimsi' in t or 'swap imsi' in t or ('change' in t and 'iccid' in t and 'syniverse' in t):
        return [
            ('Step 1: Trigger Change SIM/Device API with new ICCID',
             'NSL accepts Change SIM with 200 OK. Transaction ID generated'),
            ('Step 2: Verify NSL sends Change SIM to TMO via APOLLO_NE',
             'TMO processes Change SIM. New ICCID associated'),
            ('Step 3: Verify NSL triggers Syniverse SwapIMSI outbound call with new IMSI',
             'Syniverse SwapIMSI executed with new IMSI derived from new ICCID'),
            ('Step 4: Verify Syniverse responds with success acknowledgment',
             'Syniverse returns success. IMSI updated in Syniverse system'),
            ('Step 5: Verify wholesale plan remains unchanged after SwapIMSI',
             'Wholesale plan NOT modified — only IMSI changed per Syniverse contract'),
            ('Step 6: Download Century Report and verify Syniverse call logged',
             'Century Report shows Syniverse SwapIMSI call with success status'),
            ('Step 7: Validate Service Grouping shows updated ICCID/IMSI',
             val),
            ('Step 8: Verify Syniverse state via NBOP Line Summary(MNO) — confirm new IMSI, Wholesale Plan unchanged',
             'NBOP Line Summary shows updated IMSI matching new ICCID. Wholesale Plan remains unchanged'),
        ]
    else:
        # Generic Syniverse integration flow
        return [
            ('Step 1: Trigger the API operation as per scenario',
             'NSL accepts request with 200 OK'),
            ('Step 2: Verify NSL processes the operation via APOLLO_NE',
             'TMO processes the operation successfully'),
            ('Step 3: Verify Syniverse outbound call is triggered (or explicitly NOT triggered)',
             'Syniverse call status matches expected behavior per flow type'),
            ('Step 4: Verify TMO company ID 14619 used (not VZW 03930)',
             'Correct tenant company ID used in Syniverse call. No cross-tenant contamination'),
            ('Step 5: Download Century Report and verify all backend calls',
             'Century Report shows correct Syniverse call status'),
            ('Step 6: Validate Service Grouping and line state',
             val),
        ]


def _sync_subscriber_steps(title, validation, t):
    """Sync Subscriber: YL/YD/YM/YP/PL state change flows.
    Based on Chalk matrix for MWTGPROV-4009."""
    val = validation or title
    t_low = t.lower()

    # Determine the state change direction and Syniverse action
    if 'active' in t_low and 'deactive' in t_low and 'deactive' in t_low[t_low.index('active'):]:
        # Active → Deactive
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Active on NSL)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Active → Deactive',
             'NSL DB updated — line status changed to Deactive'),
            ('Step 3: Verify ITMBO and EMM are notified of the status change',
             'ITMBO and EMM receive deactivation notification with correct payload'),
            ('Step 4: Verify Syniverse RemoveSubscriber is called (except for Smartwatches)',
             'Syniverse RemoveSubscriber executed. Subscriber removed from Syniverse. '
             'Smartwatch lines skip Syniverse call'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             'Century Report shows sync transaction with RemoveSubscriber call'),
            ('Step 6: Verify NBOP reflects Deactive status',
             val),
        ]
    elif 'deactive' in t_low and 'active' in t_low and 'port' not in t_low:
        # Deactive → Active (no port-out)
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Deactive on NSL)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Deactive → Active',
             'NSL DB updated — line status changed to Active'),
            ('Step 3: Verify ITMBO and EMM are notified of the reactivation',
             'ITMBO and EMM receive activation notification'),
            ('Step 4: Verify Syniverse CreateSubscriber is called (except for Smartwatches)',
             'Syniverse CreateSubscriber executed with correct IMSI, MDN, wholesale plan'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             'Century Report shows sync transaction with CreateSubscriber call'),
            ('Step 6: Verify NBOP reflects Active status',
             val),
        ]
    elif 'deactive' in t_low and 'active' in t_low and 'port' in t_low:
        # Deactive → Active with In Progress Port Out
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line Deactive, port-out in progress)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL detects In Progress Port Out and syncs accordingly',
             'NSL processes sync with port-out priority — subscriber to be deleted'),
            ('Step 3: Verify ITMBO and EMM are notified to delete the subscriber',
             'ITMBO and EMM receive delete notification'),
            ('Step 4: Verify Syniverse RemoveSubscriber is called (port-out takes priority)',
             'Syniverse RemoveSubscriber executed despite Active status — port-out overrides'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             val),
        ]
    elif 'active' in t_low and 'hotline' in t_low:
        # Active → Hotlined
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Active)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Active → Hotlined',
             'NSL DB updated — line status changed to Hotlined'),
            ('Step 3: Verify ITMBO and EMM are notified of the Hotline status change',
             'ITMBO and EMM receive Hotline notification'),
            ('Step 4: Verify Syniverse RemoveSubscriber is called (except for Smartwatches)',
             'Syniverse RemoveSubscriber executed for Hotlined state change'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             val),
        ]
    elif 'hotline' in t_low and 'active' in t_low:
        # Hotlined → Active
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Hotlined)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Hotlined → Active',
             'NSL DB updated — line status changed to Active'),
            ('Step 3: Verify ITMBO and EMM are notified of the status restoration',
             'ITMBO and EMM receive reactivation notification'),
            ('Step 4: Verify Syniverse CreateSubscriber is called (except for Smartwatches)',
             'Syniverse CreateSubscriber executed — subscriber re-registered'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             val),
        ]
    elif 'active' in t_low and 'suspend' in t_low:
        # Active → Suspended
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Active)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Active → Suspended',
             'NSL DB updated — line status changed to Suspended'),
            ('Step 3: Verify ITMBO and EMM are notified of the Suspend',
             'ITMBO and EMM receive Suspend notification'),
            ('Step 4: Verify Syniverse RemoveSubscriber is called (except for Smartwatches)',
             'Syniverse RemoveSubscriber executed for Suspended state change'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             val),
        ]
    elif 'suspend' in t_low and 'active' in t_low:
        # Suspended → Active
        return [
            ('Step 1: Trigger YL Sync Subscriber API with valid LineId and MDN (line currently Suspended)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs line status from TMO: Suspended → Active',
             'NSL DB updated — line status changed to Active'),
            ('Step 3: Verify ITMBO and EMM are notified of the Restore',
             'ITMBO and EMM receive restoration notification'),
            ('Step 4: Verify Syniverse CreateSubscriber is called (except for Smartwatches)',
             'Syniverse CreateSubscriber executed — subscriber re-registered'),
            ('Step 5: Download Century Report and verify all backend calls logged',
             val),
        ]
    elif 'yd ' in t_low or ('iccid' in t_low and 'change' in t_low):
        # YD — device/SIM change sync (ICCID changes → SwapIMSI)
        # But NOT if "does not change" or "no changes"
        if 'does not change' in t_low or 'no changes' in t_low or 'iccid does not' in t_low:
            return [
                ('Step 1: Trigger YD Sync Subscriber API with valid LineId and MDN (ICCID unchanged)',
                 'NSL accepts sync request with 200 OK'),
                ('Step 2: Verify NSL detects no ICCID change — no device sync needed',
                 'NSL confirms ICCID is same on TMO and NSL — no update'),
                ('Step 3: Verify NO Syniverse SwapIMSI call triggered',
                 'No Syniverse call — ICCID unchanged means no IMSI swap needed'),
                ('Step 4: Verify NO changes to NSL DB, ITMBO, or EMM',
                 'No outbound calls. System state unchanged'),
                ('Step 5: Verify Century Report shows YD sync with no-op result',
                 val),
            ]
        return [
            ('Step 1: Trigger YD Sync Subscriber API with valid LineId and MDN (ICCID changed on TMO)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs new ICCID/IMSI from TMO to NSL DB',
             'NSL DB updated with new ICCID and IMSI'),
            ('Step 3: Verify Syniverse SwapIMSI is called with new IMSI',
             'Syniverse SwapIMSI executed with new IMSI derived from new ICCID'),
            ('Step 4: Verify wholesale plan remains UNCHANGED after SwapIMSI',
             'Wholesale plan NOT modified — only IMSI changed per Syniverse contract'),
            ('Step 5: Verify ITMBO and EMM are notified of the device/SIM change',
             'ITMBO and EMM receive device change notification with new ICCID'),
            ('Step 6: Download Century Report and verify SwapIMSI call logged',
             'Century Report shows YD sync with Syniverse SwapIMSI call'),
            ('Step 7: Verify NBOP SIM Information section shows new ICCID',
             val),
        ]
    elif 'yp ' in t_low or ('feature' in t_low and 'sync' in t_low):
        # YP — plan/feature change sync
        return [
            ('Step 1: Trigger YP Sync Subscriber API with valid LineId and MDN',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs feature changes from TMO to NSL DB',
             'Features updated in NSL DB. No wholesale plan changes'),
            ('Step 3: Verify Syniverse is NOT called for YP transactions',
             'No Syniverse call — YP only changes features, not subscriber state'),
            ('Step 4: Download Century Report and verify sync logged',
             val),
        ]
    elif 'pl ' in t_low:
        # PL — plan level, no external changes
        return [
            ('Step 1: Trigger PL Sync Subscriber API with valid LineId and MDN',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NO external system changes for PL transaction',
             'No calls to Syniverse, ITMBO, EMM, or any external system'),
            ('Step 3: Verify NSL DB unchanged — PL is internal only',
             'No DB updates. PL transaction logged but no action taken'),
            ('Step 4: Download Century Report and verify PL no-op logged',
             val),
        ]
    elif ('no line status change' in t_low or 'no changes' in t_low or
          'active and active' in t_low or 'deactive and deactive' in t_low or
          'hotlined and hotlined' in t_low or
          ('iccid' in t_low and 'does not change' in t_low)):
        # No state change — should do nothing
        return [
            ('Step 1: Trigger Sync Subscriber API with valid LineId and MDN (same status on both TMO and NSL)',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL detects no state change',
             'NSL confirms status is already in sync — no update needed'),
            ('Step 3: Verify NO changes made to NSL DB, ITMBO, EMM, or Syniverse',
             'No outbound calls triggered. No DB updates. System state unchanged'),
            ('Step 4: Verify Century Report shows sync with no-op result',
             val),
        ]
    else:
        # Generic sync subscriber
        return [
            ('Step 1: Trigger Sync Subscriber API with valid LineId, MDN, and transaction type',
             'NSL accepts sync request with 200 OK'),
            ('Step 2: Verify NSL syncs data from TMO per transaction type rules',
             'NSL DB updated according to transaction type (YL/YD/YM/YP/PL)'),
            ('Step 3: Verify correct external systems notified per contract',
             'ITMBO, EMM, Syniverse called or NOT called per transaction type rules'),
            ('Step 4: Download Century Report and verify all backend calls logged',
             'Century Report shows sync transaction with correct outbound calls'),
            ('Step 5: Verify NBOP reflects synced data',
             val),
        ]


def _sync_key_info_steps(title, validation, t):
    """Sync Key Info (YK): account key data sync — externalAccountNumber, MBO, NE, EMM, CM.
    Based on manual QMetry test patterns for MWTGPROV-4019."""
    val = validation or title
    t_low = t.lower()

    # Negative: error scenarios (ERR20, ERR162, ERR165, etc.)
    if any(kw in t_low for kw in ['err20', 'err162', 'err165', 'not found',
                                    'deactive', 'deactivated', 'invalid',
                                    'does not exist', 'not match']):
        # Extract the specific error code if present
        import re
        err_match = re.search(r'(ERR\d+)', t, re.IGNORECASE)
        err_code = err_match.group(1).upper() if err_match else 'appropriate error'

        # Build scenario-specific trigger step to survive dedup
        if 'err20' in t_low or ('not found' in t_low and 'iccid' not in t_low):
            trigger = 'Step 1: Trigger the Sync Key Info API with MDN that does NOT exist in NSL DB'
            trigger_exp = 'Sync Key Info API request sent with non-existent MDN'
            verify = 'Step 2: Validate NSL returns ERR20 - MDN not found'
            verify_exp = 'ERR20 returned: MDN not found in NSL DB'
        elif 'err162' in t_low or 'deactiv' in t_low or 'not in active' in t_low:
            trigger = 'Step 1: Trigger the Sync Key Info API with a Deactivated MDN'
            trigger_exp = 'Sync Key Info API request sent with deactivated MDN'
            verify = 'Step 2: Validate NSL returns ERR162 - MDN is not in active status'
            verify_exp = 'ERR162 returned: MDN is not in active status'
        elif 'err165' in t_low or 'does not exist' in t_low or 'not match' in t_low or 'combination' in t_low:
            trigger = 'Step 1: Trigger the Sync Key Info API with MDN where ICCID/MDN combination does not match'
            trigger_exp = 'Sync Key Info API request sent with mismatched MDN/ICCID'
            verify = 'Step 2: Validate NSL returns ERR165 - MDN and ICCID combination does not exist'
            verify_exp = 'ERR165 returned: MDN and ICCID combination does not exist'
        else:
            trigger = 'Step 1: Trigger the Sync Key Info API with the MDN per error scenario'
            trigger_exp = 'Sync Key Info API request sent'
            verify = 'Step 2: Validate NSL returns %s with descriptive error message' % err_code
            verify_exp = '%s should be returned: %s' % (err_code, val[:150])

        return [
            (trigger, trigger_exp),
            (verify, verify_exp),
            ('Step 3: Verify no data corruption — line state and account data unchanged',
             'NSL DB unchanged. No outbound calls to NE, EMM, or CM'),
        ]

    # Happy path: IB request WITH externalAccountNumber that MATCHES NSL DB (no-op)
    if ('match' in t_low and 'not' not in t_low) or 'no-op' in t_low or 'suc00' in t_low:
        return [
            ('Step 1: Trigger the Sync Key Info API with CORRECT externalAccountNumber and externalAccountStatus for the MDN',
             'Sync Key Info API should get triggered successfully'),
            ('Step 2: Validate that the "Sync Key Info" and "Get Line Details" call is happened in the backend',
             'The "Get Line Details" call should NOT be triggered (data already matches)'),
            ('Step 3: Validate that NSL does NOT update the accountNumber and no calls triggered to NE, EMM and CM',
             'No unnecessary account change; response code is SUC00; downstream systems remain consistent'),
        ]

    # Happy path: IB request WITH DIFFERENT externalAccountNumber (update without MBO)
    if 'diff' in t_low and 'externalaccount' in t_low:
        return [
            ('Step 1: Trigger the Sync Key Info API with DIFFERENT externalAccountNumber and externalAccountStatus for the MDN',
             'Sync Key Info API should get triggered successfully'),
            ('Step 2: Validate that the "Sync Key Info" and "Get Line Details" call is happened in the backend',
             'The "Get Line Details" call should NOT be triggered (externalAccountNumber provided)'),
            ('Step 3: Validate that NSL updates the accountNumber as needed without calling MBO, then calls NE Update Label and updates EMM and CM',
             'NSL should update the accountNumber, acct_id, elineid and mark the transaction status as "COMPLETED" in transaction history'),
        ]

    # Happy path: IB request WITHOUT externalAccountNumber, NSL DB HAS account (MBO call needed)
    if ('without' in t_low or 'does not contain' in t_low or 'not contain' in t_low) and 'exist' in t_low:
        return [
            ('Step 1: Trigger the Sync Key Info API without externalAccountNumber and externalAccountStatus for the MDN',
             'Sync Key Info API should get triggered successfully'),
            ('Step 2: Validate that the "Sync Key Info" and "Get Line Details" call is happened in the backend',
             'The "Sync Key Info" and "Get Line Details" call should get triggered and mark the transaction status as "IN-PROGRESS"'),
            ('Step 3: Validate that NSL updates the accountNumber as needed, then calls NE Update Label and updates EMM and CM with the account data',
             'NSL should update the accountNumber, acct_id, elineid and mark the transaction status as "COMPLETED" in transaction history'),
        ]

    # Happy path: IB request WITHOUT externalAccountNumber, NSL DB does NOT have account (create new)
    if ('without' in t_low or 'does not contain' in t_low or 'not contain' in t_low) and ('not have' in t_low or 'no existing' in t_low or 'does not' in t_low):
        return [
            ('Step 1: Trigger the Sync Key Info API without externalAccountNumber and externalAccountStatus for the MDN',
             'Sync Key Info API should get triggered successfully'),
            ('Step 2: Validate that the "Sync Key Info" and "Get Line Details" call is happened in the backend',
             'The "Sync Key Info" and "Get Line Details" call should get triggered and mark the transaction status as "IN-PROGRESS"'),
            ('Step 3: Validate that NSL creates a new account number and the line is linked',
             'The new account number should be created and the line should be linked'),
            ('Step 4: Validate that NSL updates the accountNumber as needed, then calls NE Update Label and updates EMM and CM',
             'NSL should update the accountNumber, acct_id, elineid and mark the transaction status as "COMPLETED" in transaction history'),
        ]

    # Happy path: IB request with externalAccountNumber NOT in NSL DB (create new)
    if 'not in nsl' in t_low or 'not_in_nsl' in t_low:
        return [
            ('Step 1: Trigger the Sync Key Info API with externalAccountNumber and externalAccountStatus for the MDN',
             'Sync Key Info API should get triggered successfully'),
            ('Step 2: Validate that the "Sync Key Info" and "Get Line Details" call is happened in the backend',
             'The "Get Line Details" call should NOT be triggered (externalAccountNumber provided)'),
            ('Step 3: Validate that NSL creates a new account number and the line is linked',
             'The new account number should be created and the line should be linked'),
            ('Step 4: Validate that NSL updates the accountNumber as needed, then calls NE Update Label and updates EMM and CM',
             'NSL should update the accountNumber, acct_id, elineid and mark the transaction status as "COMPLETED" in transaction history'),
        ]

    # Generic Sync Key Info happy path (fallback)
    return [
        ('Step 1: Trigger the Sync Key Info API with valid MDN, LineId, and account parameters',
         'Sync Key Info API should get triggered successfully'),
        ('Step 2: Validate backend calls: "Sync Key Info" and "Get Line Details" processing',
         'Backend calls executed per business rules. Transaction status updated to "IN-PROGRESS"'),
        ('Step 3: Validate NSL updates accountNumber, acct_id, elineid and calls NE, EMM, CM as needed',
         'NSL should update account data and mark the transaction status as "COMPLETED" in transaction history'),
        ('Step 4: Verify Century Report shows Sync Key Info transaction with all backend calls logged',
         val[:200] if val else 'Sync Key Info completed successfully with correct account data'),
    ]


def _is_ui_sync(t, ctx=''):
    """Detect UI-based Sync Subscriber scenarios (trigger via NBOP portal)."""
    return any(kw in t for kw in [
        'sync subscriber through nbop', 'sync subscriber via nbop',
        'sync via nbop', 'sync through nbop', 'nbop sync',
        'sync with network', 'sync line via', 'ui verify: sync',
        'ui: sync subscriber', 'validate sync subscriber through nbop',
    ])


def _ui_sync_subscriber_steps(title, validation, t=''):
    """UI-based Sync Subscriber — trigger via NBOP portal, verify Transaction History + Century Report."""
    val = validation or title

    # Determine expected Syniverse behavior from title
    _expects_syniverse = any(kw in t for kw in [
        'removesubscriber', 'createsubscriber', 'swapimsi',
        'syniverse action=remove', 'syniverse action=create',
    ])
    _expects_no_syniverse = any(kw in t for kw in [
        'no action', 'not called', 'no syniverse',
        'syniverse action=no', 'doesn\'t expose',
    ])

    steps = [
        ('Step 1: Trigger Sync Subscriber via NBOP UI (≡ Menu → Sync Line → Sync with Network → Click Sync)',
         'Sync triggered successfully via NBOP portal — confirmation displayed'),
        ('Step 2: Wait 20s for backend processing, then verify Transaction History in NBOP shows Sync Line Status entry',
         'Transaction History shows latest Sync Line Status entry with Order Status = COMPLETED'),
        ('Step 3: Click Transaction ID in Transaction History and verify Order Status = COMPLETED',
         'Transaction detail page shows Order Status = COMPLETED, MNO = correct carrier'),
        ('Step 4: Download Century Report (SERVICE_GROUPING) and verify Sync Subscriber transaction logged',
         'Century Report shows Sync Subscriber transaction with correct ROOT TRANSACTION ID'),
    ]

    if _expects_no_syniverse:
        steps.append(
            ('Step 5: Verify Syniverse is NOT called in Century Report — No action expected',
             'No Syniverse outbound call in SERVICE_GROUPING. No action per contract')
        )
    elif _expects_syniverse:
        steps.append(
            ('Step 5: Verify Syniverse call in Century Report per expected action',
             'Syniverse outbound call found in SERVICE_GROUPING with correct action')
        )
    else:
        steps.append(
            ('Step 5: Verify external system calls in Century Report per sync rules',
             'Correct outbound calls logged per transaction type rules')
        )

    steps.append(
        ('Step 6: Verify NBOP Line Information reflects synced status',
         val)
    )

    return steps


def _network_reset_steps(title, validation, t=''):
    """API-based Network Reset — trigger via API, verify Century Report + NBOP."""
    val = validation or title
    return [
        ('Step 1: Trigger Network Reset API with valid parameters (MDN, OSP Account Number, PIN)',
         'NSL accepts Network Reset request with 200 OK. Transaction ID generated'),
        ('Step 2: Verify NSL sends Network Reset request to TMO via APOLLO_NE',
         'APOLLO_NE receives Network Reset outbound call with correct parameters'),
        ('Step 3: Verify TMO sends asynchronous response with reset confirmation',
         'Async callback received with success status'),
        ('Step 4: Download Century Report (SERVICE_GROUPING) and verify Network Reset transaction logged',
         'Century Report shows Network Reset transaction with correct ROOT TRANSACTION ID'),
        ('Step 5: Verify NBOP MIG tables updated correctly (MIG_DEVICE, MIG_SIM, MIG_LINE)',
         'NBOP MIG tables reflect correct IMEI, ICCID, MDN, and line status'),
        ('Step 6: Verify Transaction History records the Network Reset operation',
         val),
    ]


def _ui_network_reset_steps(title, validation, t=''):
    """UI-based Network Reset — trigger via NBOP portal, verify Transaction History + Century Report."""
    val = validation or title
    return [
        ('Step 1: Trigger Network Reset via NBOP UI (≡ Menu → Reset Line → Network → Click Reset)',
         'Network Reset triggered successfully via NBOP portal — confirmation displayed'),
        ('Step 2: Wait 20s for backend processing, then verify Transaction History in NBOP shows Network Reset entry',
         'Transaction History shows latest Network Reset entry with Order Status = COMPLETED'),
        ('Step 3: Click Transaction ID in Transaction History and verify Order Status = COMPLETED',
         'Transaction detail page shows Order Status = COMPLETED'),
        ('Step 4: Download Century Report (SERVICE_GROUPING) and verify Network Reset transaction logged',
         'Century Report shows Network Reset transaction with correct ROOT TRANSACTION ID'),
        ('Step 5: Verify NBOP Line Information reflects reset status — MDN, IMEI, ICCID unchanged',
         val),
    ]


def _report_steps(title, validation):
    """Report validation: from sample 4085."""
    val = validation or title
    return [
        ("Generate/obtain previous day's report file; decompress and open",
         'Report file generated and accessible'),
        ('Validate report structure and content per scenario',
         val),
    ]


def _inquiry_steps(title, validation, t):
    """Inquiry/Query: Read-only operations that retrieve and return data.
    Used for Order Inquiry, Line Inquiry, Usage Inquiry, Sim-Info, Device Details, etc.
    Steps focus on request parameters, response payload validation, and NBOP display."""
    val = validation or title
    t_low = t.lower()

    # Determine inquiry type for specific steps
    if 'order' in t_low or 'reference number' in t_low:
        return [
            ('Step 1: Trigger Order Inquiry API with valid parameters (requestType, MDN/Reference Number)',
             'NSL accepts request and returns HTTP 200 with order status payload'),
            ('Step 2: Validate response payload contains: orderStatus, referenceNumber, transactionId, timestamps',
             'All required fields present in response with correct values'),
            ('Step 3: Validate TMO vs VZW subscriber differentiation in response',
             'requestType=TMO returns TMO-specific order data. VZW returns VZW-specific data'),
            ('Step 4: Verify NBOP Network Inquiry screen displays the query results correctly',
             val),
        ]
    elif 'esim' in t_low or 'sim-info' in t_low or 'sim info' in t_low:
        return [
            ('Step 1: Trigger eSIM/SIM Info inquiry API with valid MDN',
             'NSL accepts request and returns HTTP 200 with SIM details payload'),
            ('Step 2: Validate response contains: ICCID, IMSI, SIM Type, SIM Status, EID (for eSIM)',
             'All SIM fields present and match subscriber profile'),
            ('Step 3: Verify NBOP SIM Information section matches API response',
             val),
        ]
    elif 'usage' in t_low:
        return [
            ('Step 1: Trigger Usage Inquiry API with valid MDN and date range',
             'NSL accepts request and returns HTTP 200 with usage data'),
            ('Step 2: Validate response contains usage records: Voice, Data, SMS with correct fields',
             'Usage records present with callType, duration/bytes, timestamps'),
            ('Step 3: Verify NBOP Usage Details screen displays matching records',
             val),
        ]
    elif 'line' in t_low and ('info' in t_low or 'inquiry' in t_low):
        return [
            ('Step 1: Trigger Line Inquiry API with valid MDN',
             'NSL accepts request and returns HTTP 200 with line details'),
            ('Step 2: Validate response contains: MDN, lineStatus, ICCID, IMEI, planCode, features',
             'All line fields present and match current subscriber state'),
            ('Step 3: Verify NBOP Line Information section matches API response',
             val),
        ]
    elif 'device' in t_low and ('detail' in t_low or 'lock' in t_low):
        return [
            ('Step 1: Trigger Device Details/Lock Status API with valid IMEI or MDN',
             'NSL accepts request and returns HTTP 200 with device information'),
            ('Step 2: Validate response contains: IMEI, make, model, deviceType, lockStatus',
             'Device fields present and match subscriber profile'),
            ('Step 3: Verify NBOP Device Information section matches API response',
             val),
        ]
    elif 'eligibility' in t_low or 'reconnect' in t_low:
        return [
            ('Step 1: Trigger Eligibility/Reconnect inquiry API with valid MDN',
             'NSL accepts request and returns HTTP 200 with eligibility result'),
            ('Step 2: Validate response contains eligibility status and reason codes',
             'Eligibility result matches subscriber current state'),
            ('Step 3: Verify result displayed correctly in NBOP',
             val),
        ]
    elif 'transaction' in t_low or 'event' in t_low or 'status' in t_low:
        return [
            ('Step 1: Trigger Transaction/Event Status inquiry API with valid transactionId or MDN',
             'NSL accepts request and returns HTTP 200 with status details'),
            ('Step 2: Validate response contains: transactionId, status, timestamps, eventType',
             'Transaction/event details match expected state'),
            ('Step 3: Verify NBOP Transaction History displays matching record',
             val),
        ]
    else:
        # Generic inquiry
        return [
            ('Step 1: Trigger inquiry API with valid parameters as per scenario',
             'NSL accepts request and returns HTTP 200 with query results'),
            ('Step 2: Validate response payload contains all required fields per specification',
             'All expected fields present with correct values and data types'),
            ('Step 3: Validate response for TMO vs VZW subscriber differentiation (if applicable)',
             'Correct data returned based on subscriber type'),
            ('Step 4: Verify NBOP displays the inquiry results correctly',
             val),
        ]


def _notification_steps(title, validation):
    """Notification: from V6 DPFO/KAFKA flow."""
    val = validation or title
    return [
        ('Trigger the notification event from TMO',
         'Notification event received by NSL'),
        ('Validate NSL processes notification per business rules',
         'NSL applies correct suppression/forwarding logic'),
        ('Verify notification forwarded to MBO or suppressed as expected',
         val),
        ('Check KAFKA/BI topic for status update',
         'KAFKA topic updated with correct event status and payload'),
    ]


def _kafka_event_steps(title, validation):
    """Kafka/BI Event: Verified via Century Report → EVENT_MESSAGES table.
    Used for features like MWTGPROV-4195 (BI Kafka updates with TMO Indicator).
    The Kafka payload = EVENT_MESSAGES rows with networkProvider field."""
    val = validation or title
    return [
        ('Step 1: Trigger the API operation (activate/change/reconnect) with valid parameters',
         'NSL processes the operation successfully with 200 OK. Transaction ID generated'),
        ('Step 2: Download Century Report using Root Transaction ID',
         'Century Report loaded with all transaction details'),
        ('Step 3: Navigate to Century Report → EVENT section → EVENT_MESSAGES table',
         'EVENT_MESSAGES table visible with columns: EVENT_MESSAGE_ID, EVENT_TYPE, '
         'EVENT_NAME, EVENT_DESC, EVENT_GROUP, EVENT_STATUS, REQUEST_MSG'),
        ('Step 4: Filter EVENT_MESSAGES by ROOT_TRANSACTION_ID',
         'EVENT_MESSAGES rows found matching the Root Transaction ID'),
        ('Step 5: Validate EVENT_STATUS = Success for all event messages',
         'All EVENT_MESSAGES show EVENT_STATUS = Success. No Failed rows'),
        ('Step 6: Validate REQUEST_MSG contains "networkProvider":"TMO" for TMO transactions',
         'REQUEST_MSG JSON payload includes networkProvider field with value "TMO". '
         'VZW transactions should NOT contain TMO indicator — verify tenant isolation'),
        ('Step 7: Validate EVENT_NAME and EVENT_GROUP match expected values per operation type',
         val),
    ]


def _ui_flow_steps(title, validation):
    """UI flow: NBOP portal-driven test steps using real UI knowledge base."""
    val = validation or title
    # Try to use the NBOP UI knowledge base for real menu paths and field names
    try:
        from .nbop_ui_knowledge import generate_ui_steps, is_available
        if is_available():
            steps = generate_ui_steps(title, validation or '', title)
            if steps:
                return steps
    except Exception:
        pass
    # Fallback if knowledge base not available — use feature name for specificity
    import re as _re_ui
    _action = _re_ui.sub(r'^(?:Validate|Verify|Check|Ensure|UI Verify\s*[-:]?\s*)', '', title, flags=_re_ui.IGNORECASE).strip()
    _action = _re_ui.sub(r'New\s+MVNO\s*[-:—]\s*', '', _action, flags=_re_ui.IGNORECASE).strip()
    _action = _action[:80] if _action else 'the feature'
    return [
        ('Launch NBOP portal and search subscriber by MDN',
         'Subscriber profile loaded with header cards showing Account, MDN, IMEI, ICCID'),
        ('Navigate to the menu for: %s' % _action[:70],
         'Screen loads with all expected fields and controls'),
        ('Perform %s via NBOP portal' % _action[:70],
         'Operation submitted successfully — confirmation displayed'),
        ('Verify subscriber profile reflects the change',
         'All affected fields show correct post-operation values'),
        ('Navigate to Transaction History and verify entry logged',
         'Transaction History shows entry with correct timestamp, type, and SUCC status'),
    ]


def _ui_negative_steps(title, validation):
    """UI negative flow: error handling via NBOP portal using real UI knowledge base."""
    val = validation or title
    try:
        from .nbop_ui_knowledge import generate_ui_negative_steps, is_available
        if is_available():
            steps = generate_ui_negative_steps(title, validation or '', title)
            if steps:
                return steps
    except Exception:
        pass
    # Fallback
    import re as _re_uin
    _action = _re_uin.sub(r'^(?:Negative\s*[-:]?\s*|Validate|Verify|Check)\s*', '', title, flags=_re_uin.IGNORECASE).strip()
    _action = _re_uin.sub(r'New\s+MVNO\s*[-:—]\s*', '', _action, flags=_re_uin.IGNORECASE).strip()
    _action = _action[:70] if _action else 'the operation'
    return [
        ('Launch NBOP portal and search subscriber by MDN',
         'Subscriber profile loaded'),
        ('Navigate to the menu for: %s' % _action,
         'Screen loads'),
        ('Enter invalid/malformed data and attempt: %s' % _action,
         'NBOP displays appropriate error message to the user'),
        ('Verify no changes were made to subscriber data in NSL',
         'System state unchanged — no partial updates. Line table unmodified'),
    ]


def _api_flow_steps(title, validation):
    """API flow: from samples 4109, 4110."""
    val = validation or title
    import re as _re_api
    _action = _re_api.sub(r'^(?:Validate|Verify|Check|Ensure|Step\s*\d+\s*[-:]?\s*)', '', title, flags=_re_api.IGNORECASE).strip()
    _action = _re_api.sub(r'New\s+MVNO\s*[-:—]\s*', '', _action, flags=_re_api.IGNORECASE).strip()
    _action = _action[:80] if _action else 'the API operation'
    return [
        ('Trigger API: %s with valid parameters' % _action[:70],
         'NSL receives the request and begins processing'),
        ('Validate NSL sends outbound call to downstream system',
         'Downstream system receives request and responds'),
        ('Validate NSL processes response and prepares output',
         'NSL correctly processes downstream response'),
        ('Validate NSL sends final response with correct status',
         val),
        ('Validate Century Report for all backend calls',
         'All backend calls displayed correctly in Century Report'),
    ]


def _negative_steps(title, validation, t):
    """Negative: specific error handling based on error type."""
    val = validation or title
    # Extract a short action name from the title for step specificity
    import re as _re
    _action = _re.sub(r'^(?:Negative|Verify|Validate|Check|Ensure)\s*[-:]\s*', '', title, flags=_re.IGNORECASE).strip()
    _action = _action[:80] if _action else 'the operation'
    steps = [
        ('Prepare request with invalid/error data to trigger: %s' % _action[:70],
         'Invalid request prepared with error condition'),
        ('Send API request to NSL',
         'NSL receives and processes the request'),
    ]
    # Add specific error verification based on type
    if 'http 400' in t or 'schema' in t or 'invalid' in t:
        steps.append(('Verify system returns HTTP 400 with specific error code and message',
                       val))
    elif 'timeout' in t:
        steps.append(('Verify system handles timeout gracefully with retry/abort',
                       val))
    elif 'unavailable' in t:
        steps.append(('Verify system detects unavailability and fails gracefully',
                       val))
    else:
        steps.append(('Verify system returns appropriate error response',
                       val))
    steps.append(('Verify no data corruption or unintended side effects',
                   'No data modified. System state unchanged. Error logged appropriately.'))
    return steps


def _rollback_steps(title, validation, t):
    """Rollback: feature-specific restore verification."""
    val = validation or title
    import re as _re_rb
    _action = _re_rb.sub(r'^(?:Validate|Verify|Negative\s*[-:]?\s*)', '', title, flags=_re_rb.IGNORECASE).strip()
    _action = _re_rb.sub(r'New\s+MVNO\s*[-:—]\s*', '', _action, flags=_re_rb.IGNORECASE).strip()
    _action = _action[:70] if _action else 'the operation'

    # Determine what gets rolled back based on feature context
    if 'swap' in t:
        restore_item = 'original ICCID/IMEI associations'
    elif 'bcd' in t or 'dpfo' in t or 'bill cycle' in t:
        restore_item = 'original BCD/DPFO reset day value'
    elif 'rateplan' in t or 'rate plan' in t or 'feature' in t:
        restore_item = 'original rate plan and feature assignments'
    elif 'hotline' in t:
        restore_item = 'original line status (pre-Hotline state)'
    elif 'port' in t:
        restore_item = 'original MDN and port status'
    elif 'activate' in t or 'deactivat' in t:
        restore_item = 'original line activation status'
    elif 'sync' in t:
        restore_item = 'original subscriber/account data'
    elif 'account' in t:
        restore_item = 'original account number and line association'
    else:
        restore_item = 'original subscriber state and data'

    return [
        ('Trigger %s and simulate mid-operation failure' % _action[:60],
         'Operation fails at the expected point during processing'),
        ('Verify NSL initiates rollback automatically',
         'Rollback triggered for all completed steps'),
        ('Verify %s are restored' % restore_item,
         '%s restored to pre-operation values' % restore_item.capitalize()),
        ('Verify MBO and downstream systems notified of rollback',
         'MBO receives rollback notification. No partial state in downstream systems'),
        ('Verify Transaction History reflects rollback with correct status',
         val),
    ]


def _default_workflow_steps(title, validation):
    """Default: follows standard NSL workflow pattern from V6.
    Enhanced with contract-aware system assertions."""
    val = validation or title

    # Try to consult the integration contract for system-specific steps
    try:
        from .integration_contract import resolve_operation
        _contract = resolve_operation(title)
        if _contract:
            import re as _re3
            _action = _re3.sub(r'^(?:Validate|Verify|Check|Ensure|Step\s*\d+\s*[-:]?\s*)', '', title, flags=_re3.IGNORECASE).strip()
            _action = _action[:70] if _action else 'the operation'
            steps = [
                ('Step 1: Obtain OAuth Token',
                 'OAuth token generated successfully'),
                ('Step 2: Trigger API: %s' % _action,
                 'NSL processes request with 200 OK. Transaction ID generated'),
            ]
            step_num = 3

            # Add "MUST CALL" system verification steps from contract
            for sys_name in _contract.must_call[:3]:  # Cap at 3 to avoid bloat
                from .integration_contract import EXTERNAL_SYSTEMS
                if sys_name in EXTERNAL_SYSTEMS:
                    sys_obj = EXTERNAL_SYSTEMS[sys_name]
                    if sys_name == 'syniverse' and _contract.syniverse_action not in ('NONE', 'Conditional', ''):
                        steps.append(('Step %d: Verify %s %s outbound call' % (step_num, sys_obj.name, _contract.syniverse_action),
                                      '%s %s executed successfully' % (sys_obj.name, _contract.syniverse_action)))
                    elif sys_name != 'syniverse':
                        steps.append(('Step %d: Verify %s is notified/updated' % (step_num, sys_obj.name),
                                      '%s updated correctly' % sys_obj.name))
                    step_num += 1

            # Add "MUST NOT CALL" assertions from contract
            for sys_name in _contract.must_not_call[:2]:  # Cap at 2
                from .integration_contract import EXTERNAL_SYSTEMS
                if sys_name in EXTERNAL_SYSTEMS:
                    sys_obj = EXTERNAL_SYSTEMS[sys_name]
                    steps.append(('Step %d: Verify %s is NOT called' % (step_num, sys_obj.name),
                                  'No %s outbound call triggered — confirmed via %s' % (sys_obj.name, sys_obj.verify_via)))
                    step_num += 1

            steps.extend([
                ('Step %d: Download Century Report (Service Grouping)' % step_num,
                 'SERVICE_GROUPING HTML downloaded'),
                ('Step %d: Validate Service Grouping' % (step_num + 1),
                 val),
                ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
                 'Transaction recorded correctly'),
            ])
            return steps
    except Exception:
        pass

    # Fallback: standard workflow without contract
    # Extract feature action from title for step specificity
    import re as _re2
    _action = _re2.sub(r'^(?:Validate|Verify|Check|Ensure|Step\s*\d+\s*[-:]?\s*)', '', title, flags=_re2.IGNORECASE).strip()
    _action = _action[:80] if _action else 'the operation'
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Trigger API: %s' % _action[:70],
         'NSL processes request with 200 OK. Transaction ID generated'),
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 3b: Verify NE Portal transactions',
         'NE Portal shows transaction completed'),
        ('Step 4: Validate Service Grouping for: %s' % _action[:60],
         val),
        ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded correctly'),
    ]
