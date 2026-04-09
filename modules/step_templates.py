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
        return _deactivation_steps(sc_title, sc_validation)

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
    """Activation: V6 pipeline - Validate Device → Activate → Century → Validate."""
    val = validation or title
    return [
        ('Obtain OAuth Token for API authentication',
         'OAuth token generated successfully'),
        ('Trigger Validate Device API with IMEI',
         'Device validated successfully. Equipment type and compatibility confirmed'),
        ('Trigger Activate Subscriber API with device and SIM details',
         'NSL acknowledges activation with 200 OK. Transaction ID generated'),
        ('Verify NSL sends activation request to TMO via APOLLO_NE',
         'TMO processes activation. Line status changes to Active'),
        ('Download Century Report (Service Grouping)',
         'Century Report downloaded. SERVICE_GROUPING HTML available'),
        ('Validate Service Grouping: verify line status, features, device details',
         'Service Grouping shows correct activation state'),
        ('Verify NBOP MIG tables (Feature, Device, SIM, Line, Transaction History)',
         'All NBOP tables populated with correct activation data'),
        ('Verify in TMO Genesis Portal: subscriber line is active',
         val),
    ]


def _change_sim_steps(title, validation, t):
    """Change SIM: V6 pipeline."""
    val = validation or title
    return [
        ('Obtain OAuth Token',
         'Token generated'),
        ('Trigger Change SIM API with new ICCID and MDN',
         'NSL accepts Change SIM request with 200 OK'),
        ('Verify NSL sends Change SIM to APOLLO_NE/TMO',
         'TMO processes SIM change. New ICCID associated with MDN'),
        ('Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Verify NE Portal transactions',
         'NE Portal shows Change SIM transaction completed'),
        ('Validate Service Grouping: verify new ICCID, IMSI updated',
         val),
        ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded with correct details'),
    ]


def _change_bcd_steps(title, validation, t):
    """Change BCD: V6 pipeline - from sample 3948."""
    val = validation or title
    return [
        ('Initiate Change BCD transaction',
         'NSL accepts and processes Change BCD with Response 200'),
        ('Verify request routed with requestType = TMO',
         'Request routed to TMO correctly'),
        ('Verify NSL fetches line/feature information from DB',
         'Correct DB details retrieved and outbound request prepared'),
        ('Verify NSL updates BCD for applicable features',
         'Updated BCD sent for eligible features only'),
        ('Verify downstream updates complete (NSL DB, Mediation, IT-MBO)',
         'NSL DB, Mediation, IT-MBO updated'),
        ('Check Genesis Portal for new BCD',
         'New BCD reflected in TMO Genesis'),
        ('Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY)',
         'Transaction recorded correctly'),
        ('Check events/DPFO notifications',
         val),
    ]


def _change_rateplan_steps(title, validation, t):
    """Change Rateplan: V6 pipeline."""
    val = validation or title
    return [
        ('Obtain OAuth Token',
         'Token generated'),
        ('Trigger Change Rateplan API with new plan code',
         'NSL accepts Change Rateplan with 200 OK'),
        ('Verify NSL sends rateplan change to APOLLO_NE/TMO',
         'TMO processes rateplan change'),
        ('Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Verify NE Portal transactions',
         'NE Portal shows rateplan change completed'),
        ('Validate Service Grouping: verify new plan code and features',
         val),
        ('Check audit logs',
         'Transaction recorded correctly'),
    ]


def _change_feature_steps(title, validation, t):
    """Change Feature: V6 pipeline."""
    val = validation or title
    return [
        ('Obtain OAuth Token',
         'Token generated'),
        ('Trigger Change Feature API (add/remove/reset)',
         'NSL accepts Change Feature with 200 OK'),
        ('Verify NSL sends feature change to APOLLO_NE/TMO',
         'TMO processes feature change'),
        ('Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Validate Service Grouping: verify feature added/removed correctly',
         val),
    ]


def _deactivation_steps(title, validation, t):
    """Deactivation: V6 pipeline."""
    val = validation or title
    return [
        ('Trigger Deactivation API with MDN and Line ID',
         'NSL accepts deactivation request'),
        ('Verify NSL sends deactivation to TMO via APOLLO_NE',
         'TMO processes deactivation. Line status changes to Deactivated'),
        ('Download Century Report (Service Grouping)',
         'SERVICE_GROUPING HTML downloaded'),
        ('Validate Service Grouping: verify line status = Deactivated',
         val),
        ('Verify agent details and deactivation reason captured',
         'Agent details and reason recorded correctly'),
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
    """Default: generic but still follows NSL pattern."""
    val = validation or title
    return [
        ('Set up preconditions and prepare request as per scenario',
         'Preconditions met. Request prepared'),
        ('Execute the operation/API call',
         'System processes request successfully'),
        ('Verify expected output and system state',
         val),
        ('Verify downstream systems updated correctly',
         'All dependent systems reflect the change'),
        ('Check audit logs and transaction history',
         'Transaction recorded correctly'),
    ]
