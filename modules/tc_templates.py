"""
TC Templates — Single source of truth for test case construction.
=================================================================
Each feature type (UI, API, Notification, Batch) has its own template
that controls Summary, Description, Precondition, Steps, and Expected Results.

NO cross-contamination: UI TCs never mention API/NSL/HTTP.
                        API TCs never mention NBOP/portal/screen.

Based on analysis of 2,107 manual TCs from PI-49/50/51.
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class FeatureClassification:
    """Single source of truth for feature type classification."""
    feature_type: str          # 'ui_portal', 'api_crud', 'async_workflow', 'notification', 'batch_report'
    is_ui: bool = False
    is_api: bool = False
    is_notification: bool = False
    is_batch: bool = False
    channel: str = ''          # 'NBOP', 'ITMBO', 'NBOP,ITMBO'
    nbop_nav_path: str = ''    # e.g., 'NBOP → Mobile Service Management → Validate Port-In Eligibility'
    affected_sections: List[str] = field(default_factory=list)  # e.g., ['Line Information', 'Device Information']
    precondition_state: str = ''  # e.g., 'active', 'suspended', 'hotlined'


def classify_feature(feature_name: str, description: str = '', channel: str = '',
                     jira_summary: str = '', ac_text: str = '', scope: str = '') -> FeatureClassification:
    """Classify a feature into its type. This is the ONLY place where
    UI vs API vs Notification vs Batch is decided."""
    ctx = (feature_name + ' ' + description + ' ' + jira_summary + ' ' +
           ac_text + ' ' + scope + ' ' + channel).lower()

    result = FeatureClassification(feature_type='api_crud')

    # ── UI/Portal detection (highest priority) ──
    # Check if NBOP is the PRIMARY channel with NO API tags
    has_api_tags = any(kw in ctx for kw in ['nslnm', 'nenm', '[nsl', 'nsl,'])
    nbop_primary = (
        channel.strip().upper() == 'NBOP' or
        'nbop to implement' in ctx or
        'nbop screen' in ctx or 'nbop ui' in ctx or
        ('[nbop' in ctx.split('\n')[0] if ctx else False) or
        ('nbop' in ctx and any(kw in ctx for kw in ['screen', 'menu', 'navigation', 'display']))
    )
    # HYBRID: has both NBOP and API tags (NSLNM, NENM, NE)
    if nbop_primary and has_api_tags:
        result.feature_type = 'hybrid'
        result.is_ui = True
        result.is_api = True  # BOTH
        result.channel = channel or 'NBOP'
        try:
            from .nbop_ui_knowledge import get_navigation_path
            result.nbop_nav_path = get_navigation_path(feature_name, description)
        except Exception:
            result.nbop_nav_path = 'NBOP → Mobile Service Management'
        return result
    # PURE UI: NBOP only, no API tags
    if nbop_primary and not has_api_tags:
        result.feature_type = 'ui_portal'
        result.is_ui = True
        result.channel = 'NBOP'
        try:
            from .nbop_ui_knowledge import get_navigation_path
            result.nbop_nav_path = get_navigation_path(feature_name, description)
        except Exception:
            result.nbop_nav_path = 'NBOP → Mobile Service Management'
        return result

    # ── Notification / CDR / Mediation detection ──
    if any(kw in ctx for kw in ['notification', 'kafka', 'dpfo', 'suppress', 'usage event',
                                 'cdr', 'mediation', 'prr', 'ild', 'international roaming',
                                 'country code', 'country translation', 'usage metering',
                                 'tariff_plan', 'usage file']):
        result.feature_type = 'notification'
        result.is_notification = True
        return result

    # ── Batch/Report detection — use specific phrases, not just "report" or "file" ──
    _batch_phrases = ['batch processing', 'batch file', 'sftp report', 'differential report',
                      'reconcil', 'csv file', 'batch job', 'file processing', 'subscriber differential',
                      'blocklist batch', 'gsma blocklist']
    if any(kw in ctx for kw in _batch_phrases):
        result.feature_type = 'batch_report'
        result.is_batch = True
        return result

    # ── Async workflow detection ──
    if any(kw in ctx for kw in ['async', 'callback', 'webhook', 'asynchronous']):
        result.feature_type = 'async_workflow'
        result.is_api = True
        return result

    # ── Default: API/CRUD ──
    result.feature_type = 'api_crud'
    result.is_api = True
    result.channel = channel or 'ITMBO'
    return result


# ════════════════════════════════════════════════════════════════════
#  DESCRIPTION TEMPLATES
# ════════════════════════════════════════════════════════════════════

def build_description(fc: FeatureClassification, feature_name: str,
                      scenario_title: str, category: str = 'Happy Path') -> str:
    """Build a description that matches the feature type. Never cross-contaminates."""
    if fc.is_ui and not fc.is_api:
        if category == 'Negative':
            return 'Validate error handling for %s through NBOP portal.' % feature_name
        elif category == 'Edge Case':
            return 'Validate edge case behavior for %s through NBOP portal.' % feature_name
        else:
            return 'Validate %s through NBOP portal.' % feature_name

    elif fc.feature_type == 'hybrid':
        if category == 'Negative':
            return 'Validate %s rejects invalid request with appropriate error handling.' % feature_name
        else:
            return 'Validate %s operation via NBOP portal with backend system verification.' % feature_name

    elif fc.is_notification:
        if category == 'Negative':
            return 'Validate mediation handles invalid input for %s without crash or incorrect mapping.' % feature_name
        else:
            return 'Validate %s mediation/notification processing completes correctly.' % feature_name

    elif fc.is_batch:
        return 'Validate %s batch/report processing.' % feature_name

    else:  # API
        if category == 'Negative':
            return 'Validate %s API rejects invalid request with appropriate error code.' % feature_name
        else:
            return 'This API is used to %s for a subscriber.' % feature_name.lower()


# ════════════════════════════════════════════════════════════════════
#  PRECONDITION TEMPLATES
# ════════════════════════════════════════════════════════════════════

def build_precondition(fc: FeatureClassification, feature_name: str,
                       category: str = 'Happy Path', scenario_title: str = '') -> str:
    """Build preconditions that match the feature type."""
    sc = scenario_title.lower()

    if fc.is_ui and not fc.is_api:  # Pure UI only
        if 'suspend' in sc:
            return '1.\tUser must have a Suspended line.\n2.\tUser must have access to NBOP.'
        elif 'hotline' in sc:
            return '1.\tUser must have a Hotlined line.\n2.\tUser must have access to NBOP.'
        elif 'deactiv' in sc:
            return '1.\tUser must have a Deactivated line.\n2.\tUser must have access to NBOP.'
        elif 'non-existent' in sc or 'not found' in sc or 'invalid' in sc:
            return '1.\tUser must have access to NBOP.\n2.\tPrepare invalid test data.'
        elif 'empty' in sc or 'blank' in sc:
            return '1.\tUser must have access to NBOP.'
        else:
            return '1.\tUser must have an active line.\n2.\tUser must have access to NBOP.'

    elif fc.is_notification:
        if 'dpfo' in sc or 'threshold' in sc or '80' in sc or '100' in sc:
            return '1.\tUser must have an active line.\n2.\tMediation and PRR batch jobs running.\n3.\tDPFO metering below target threshold.'
        elif 'invalid' in sc or 'malformed' in sc:
            return '1.\tMediation and PRR batch jobs running.\n2.\tPrepare invalid PRR test data.'
        else:
            return '1.\tUser must have an active line.\n2.\tMediation and PRR batch jobs running.\n3.\tSFTP access available.'

    elif fc.is_batch:
        return '1.\tBatch processing system accessible.\n2.\tTest data files prepared.'

    else:  # API
        if category == 'Negative':
            return '1.\tUser must have an active line.\n2.\tPrepare invalid request data.'
        elif 'suspend' in sc:
            return '1.\tPhone line should be in Suspended state.'
        elif 'hotline' in sc:
            return '1.\tPhone line should be in Hotlined state.'
        else:
            return '1.\tPhone line should be active.'


# ════════════════════════════════════════════════════════════════════
#  STEP TEMPLATES
# ════════════════════════════════════════════════════════════════════

def build_steps(fc: FeatureClassification, feature_name: str,
                scenario_title: str, category: str = 'Happy Path') -> List[Tuple[str, str]]:
    """Build steps that match the feature type. Returns [(step_summary, expected_result)]."""
    sc = scenario_title.lower()
    nav = fc.nbop_nav_path or 'NBOP → Mobile Service Management'

    # ═══════════════════════════════════════════
    #  UI STEPS (pure UI only, not hybrid)
    # ═══════════════════════════════════════════
    if fc.is_ui and not fc.is_api:
        # Try KB first for scenario-specific steps
        try:
            from .nbop_ui_knowledge import generate_ui_steps, generate_ui_negative_steps
            if category == 'Negative':
                kb_steps = generate_ui_negative_steps(feature_name, '', scenario_title)
            else:
                kb_steps = generate_ui_steps(feature_name, '', scenario_title)
            if kb_steps:
                return kb_steps
        except Exception:
            pass

        # Fallback UI steps (manual pattern: 4 steps)
        if category == 'Negative':
            return [
                ('Login to NBOP and navigate to %s' % nav,
                 '%s screen loads' % feature_name),
                ('Perform invalid action: %s' % scenario_title[:80],
                 'NBOP displays error message'),
                ('Verify no changes made to subscriber data',
                 'Line status and profile unchanged'),
                ('Validate Line table unchanged',
                 'No data corruption'),
            ]
        else:
            return [
                ('Login to NBOP and navigate to %s' % nav,
                 '%s screen loads with all fields' % feature_name),
                ('Perform action: %s' % scenario_title[:80],
                 'Operation completed successfully'),
                ('Confirm on NSL and TMO the operation is reflected',
                 'NSL and TMO show updated state'),
                ('Validate Line table',
                 'All fields reflect correct post-operation values'),
            ]

    # ═══════════════════════════════════════════
    #  NOTIFICATION / CDR / MEDIATION STEPS
    # ═══════════════════════════════════════════
    elif fc.is_notification:
        sc = scenario_title.lower()
        if 'prr' in sc or 'derivation' in sc or 'mediation' in sc:
            return [
                ('Submit PRR records through mediation pipeline',
                 'PRR records submitted and accepted for processing'),
                ('Wait for mediation batch processing to complete',
                 'Mediation processes the usage records successfully'),
                ('Connect to SFTP and download PRR output file',
                 'PRR output file downloaded with correct content'),
                ('Verify derivation rules applied correctly to output',
                 'All fields mapped correctly per derivation rules'),
            ]
        elif '80%' in sc or '100%' in sc or 'threshold' in sc or 'dpfo' in sc:
            return [
                ('Submit PRRs through mediation until threshold is reached',
                 'Mediation usage reaches target threshold'),
                ('Validate notification is triggered at threshold',
                 'Notification sent with correct payload'),
                ('Validate NSL outbound logs show notification',
                 'Notification logged correctly'),
                ('Verify no duplicate notification sent',
                 'Single notification per threshold event'),
            ]
        elif 'duplicate' in sc or 'suppress' in sc:
            return [
                ('Trigger the notification condition',
                 'First notification sent correctly'),
                ('Re-trigger the same condition',
                 'No duplicate notification sent'),
                ('Verify notification logs show single entry',
                 'Suppression rules applied correctly'),
            ]
        elif 'invalid' in sc or 'malformed' in sc or 'unrecognized' in sc:
            return [
                ('Submit PRR with invalid/malformed input data',
                 'Mediation receives the invalid record'),
                ('Verify mediation handles gracefully — no crash',
                 'Invalid record rejected or flagged, pipeline continues'),
                ('Verify no incorrect mapping applied to output',
                 'No wrong data in PRR output'),
            ]
        else:
            return [
                ('Submit PRRs through mediation',
                 'PRR records submitted successfully'),
                ('Validate notification/processing completes',
                 'Processing completed without errors'),
                ('Verify output matches expected results',
                 'All fields correct per specification'),
            ]

    # ═══════════════════════════════════════════
    #  BATCH STEPS
    # ═══════════════════════════════════════════
    elif fc.is_batch:
        return [
            ('Prepare and submit batch file',
             'Batch file accepted for processing'),
            ('Validate batch processing completes',
             'All records processed without errors'),
            ('Verify output report/file',
             'Report contains expected data'),
        ]

    # ═══════════════════════════════════════════
    #  API STEPS (default)
    # ═══════════════════════════════════════════
    else:
        # Check if this is an inquiry feature — use inquiry-specific steps
        _is_inquiry_feature = any(kw in (feature_name + ' ' + scenario_title).lower()
                                  for kw in ['inquiry', 'enquiry', 'query', 'retrieve',
                                             'sim-info', 'sim info', 'device details',
                                             'device lock', 'event status', 'order status',
                                             'eligibility', 'biller line'])
        if _is_inquiry_feature:
            if category == 'Negative':
                return [
                    ('Trigger %s API with invalid/missing parameters' % feature_name,
                     'API returns appropriate error code (400/404)'),
                    ('Validate error response contains specific error code and message',
                     'Error code and description match expected per specification'),
                    ('Verify no data modification — read-only operation',
                     'No state changes. Subscriber profile unchanged'),
                ]
            else:
                return [
                    ('Trigger %s API with valid parameters (MDN/Reference Number/transactionId)' % feature_name,
                     'API returns HTTP 200 with query results payload'),
                    ('Validate response payload contains all required fields per specification',
                     'All expected fields present with correct values'),
                    ('Verify NBOP displays the inquiry results correctly',
                     'NBOP screen shows matching data from API response'),
                ]
        elif category == 'Negative':
            return [
                ('Hit API with invalid request data',
                 'API rejects request with appropriate error code'),
                ('Validate error response format and message',
                 'Error code and description match expected'),
                ('Verify no data corruption — line state unchanged',
                 'DB state unchanged after rejection'),
            ]
        else:
            return [
                ('Hit API: %s with valid parameters' % feature_name,
                 'API returns HTTP 200/202 with SUCC00'),
                ('Validate Century Reports',
                 'Transaction logged with correct status'),
                ('Validate Line table and subscriber profile',
                 'All fields reflect correct post-operation values'),
            ]
