"""
step_templates.py — Domain-specific step chain templates.
Based on real pipeline flows from TMO_API_Dashboard_V6.py
and actual test cases from sample files.
"""
import re


def get_step_chain(sc_title, sc_validation, feature_context):
    """Return list of (step_summary, expected_result) tuples based on scenario type."""
    t = (sc_title + ' ' + (sc_validation or '')).lower()
    ctx = feature_context.lower()

    # Check negative/error FIRST
    if _is_negative(t):
        return _negative_steps(sc_title, sc_validation, t)
    if _is_rollback(t):
        return _rollback_steps(sc_title, sc_validation, t)

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
    if _is_notification(t, ctx):
        return _notification_steps(sc_title, sc_validation)
    if _is_ui_flow(t, ctx):
        return _ui_flow_steps(sc_title, sc_validation)
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

def _is_report(t, ctx):
    return any(kw in t for kw in ['report', 'column', 'csv', 'differential', 'batch', '.gz'])

def _is_notification(t, ctx):
    return any(kw in t for kw in ['notification', 'suppress', 'kafka', 'dpfo', 'usage',
                                   'throttle', 'speed reduction'])

def _is_ui_flow(t, ctx):
    return any(kw in t for kw in ['menu', 'display', 'navigation', 'screen', 'portal',
                                   'nbop', 'visible', 'click', 'dropdown'])

def _is_api_flow(t, ctx):
    return any(kw in t for kw in ['api', 'http', 'endpoint', 'trigger', 'login auth',
                                   'retrieve', 'fetch'])


# ── Step chain templates (from V6 pipeline + real samples) ──

def _swap_mdn_steps(title, validation, t):
    """Swap MDN: Real pipeline from V6 + sample TC."""
    val = validation or title
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
    # eSIM swap has additional steps per line
    if 'esim' in t or 'em' in t:
        steps.extend([
            ('Verify Download order call with new ICCID allocation returned by NE (Line 1)',
             'New ICCID allocated and download order confirmed for Line 1'),
            ('Verify NSL triggers "Syniverse Change IMSI" outbound call (Line 1)',
             'Syniverse IMSI updated for Line 1'),
            ('Verify Confirm order call with new ICCID to TMO (Line 1)',
             'Order confirmed for Line 1'),
        ])
    steps.extend([
        ('Verify NSL triggers Change SIM with new ICCID to TMO (Line 2)',
         'Change SIM executed successfully for Line 2'),
        ('Verify NSL triggers Change IMEI to TMO (Line 2)',
         'Change IMEI executed successfully for Line 2'),
        ('Verify Async service inbound call received from APOLLO_NE (Line 2)',
         'Async callback received and processed for Line 2'),
    ])
    if 'esim' in t or 'em' in t:
        steps.extend([
            ('Verify NSL triggers "Syniverse Change IMSI" outbound call (Line 2)',
             'Syniverse IMSI updated for Line 2'),
            ('Verify Confirm order call with new ICCID to TMO (Line 2)',
             'Order confirmed for Line 2'),
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
    ])
    return steps


def _activation_steps(title, validation, t):
    """Activation: V6 pipeline - 7 steps with Comprehensive + NBOP MIG."""
    val = validation or title
    steps = [
        ('Step 1: Validate Device IMEI via API',
         'Device validated. Equipment type and compatibility confirmed'),
        ('Step 2: Trigger Activate Subscriber API (eSIM/pSIM) with device and SIM details',
         'NSL acknowledges activation with 200 OK. Transaction ID generated'),
        ('Step 3: Verify NSL sends activation request to TMO via APOLLO_NE',
         'TMO processes activation. Line status changes to Active'),
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
    ]


def _change_bcd_steps(title, validation, t):
    """Change BCD: V6 pipeline + sample 3948 (9 steps)."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Initiate Change BCD transaction with requestType = TMO',
         'NSL accepts and processes Change BCD with Response 200'),
        ('Step 3: Verify NSL fetches line/feature information from DB',
         'Correct DB details retrieved and outbound request prepared'),
        ('Step 4: Verify NSL updates BCD for applicable features',
         'Updated BCD sent for eligible features only'),
        ('Step 5: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 5b: Verify NE Portal transactions',
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
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded with deactivation transaction'),
        ('Step 4: Validate Service Grouping: verify line status = Deactivated',
         'Service Grouping shows Deactivated status'),
        ('Verify agent details and deactivation reason captured correctly',
         val),
        ('Verify NBOP reflects deactivated line',
         'NBOP shows line as deactivated'),
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


def _ui_flow_steps(title, validation):
    """UI flow: from sample 4190."""
    val = validation or title
    return [
        ('Search and open subscriber line profile in NBOP portal',
         'Subscriber profile opens successfully'),
        ('Navigate to the relevant menu section',
         'Menu section loads correctly'),
        ('Perform the UI action as per scenario',
         val),
        ('Capture TransactionId and validate in Century Reports',
         'TransactionId available and Century Reports updated'),
        ('Verify requestType = TMO in header',
         'Header contains requestType=TMO'),
        ('Verify downstream updates (NSL DB, Mediation, MBO)',
         'All downstream systems updated'),
        ('Check Genesis Portal for updated values',
         'Updated values visible in Genesis'),
        ('Check audit logs',
         'Transaction recorded in TRANSACTION_HISTORY & LINE_HISTORY'),
    ]


def _api_flow_steps(title, validation):
    """API flow: from samples 4109, 4110."""
    val = validation or title
    return [
        ('Trigger the API with valid parameters as per scenario',
         'NSL receives the request and begins processing'),
        ('Validate NSL sends outbound call to downstream system',
         'Downstream system receives request and responds'),
        ('Validate NSL processes response and prepares output',
         'NSL correctly processes downstream response'),
        ('Validate NSL sends final response to requesting system',
         val),
        ('Validate Century Report for all backend calls',
         'All backend calls displayed correctly in Century Report'),
    ]


def _negative_steps(title, validation, t):
    """Negative: specific error handling based on error type."""
    val = validation or title
    steps = [
        ('Prepare the request with invalid/error condition as per scenario',
         'Invalid request prepared'),
        ('Submit the request to the system',
         'System receives and processes the request'),
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
    """Rollback: specific restore verification."""
    val = validation or title
    return [
        ('Execute the operation that triggers failure condition',
         'Operation fails at the expected point'),
        ('Verify rollback process is triggered automatically',
         'Rollback initiated for all completed steps'),
        ('Verify original ICCID/IMEI associations are restored',
         'Original device and SIM associations restored'),
        ('Verify MBO is notified of rollback',
         'MBO receives rollback notification'),
        ('Verify transaction history reflects rollback',
         val),
        ('Verify both lines return to pre-operation state',
         'Lines return to original state. No partial changes remain.'),
    ]


def _default_workflow_steps(title, validation):
    """Default: follows standard NSL workflow pattern from V6."""
    val = validation or title
    return [
        ('Step 1: Obtain OAuth Token',
         'OAuth token generated successfully'),
        ('Step 2: Execute the operation/API call as per scenario',
         'NSL processes request with 200 OK. Transaction ID generated'),
        ('Step 3: Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Step 3b: Verify NE Portal transactions',
         'NE Portal shows transaction completed'),
        ('Step 4: Validate Service Grouping',
         val),
        ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded correctly'),
    ]
