"""
test_analyst.py — Test Analyst Reasoning Engine V2.
Thinks like a senior QA engineer who has been testing TMO/NSL for 3 years.

V2 changes (think-like-a-human rewrite):
- Detects LIFECYCLE of a feature (not just type) — e.g., Port-in has CP→PU/PD→PC flow
- Generates scenarios a human would write: specific, actionable, with real error codes
- Understands that every API feature has a WEARABLE angle, a LINE-STATE angle, an IMSI angle
- Knows that "negative" isn't just "invalid input" — it's specific business rule violations
- Generates TC names that read like a test plan, not a template

This is NOT data extraction. This is a QA engineer's BRAIN.
"""
import re
from typing import List


def analyze_and_suggest(feature_name: str, feature_id: str,
                        scope: str = '', description: str = '',
                        existing_scenarios: List[str] = None,
                        log=print) -> List[dict]:
    """Think like a test analyst. Return list of suggested scenarios."""
    fname = feature_name
    ctx = (scope + ' ' + description + ' ' + fname).lower()
    existing = ' '.join(existing_scenarios or []).lower()
    suggestions = []

    # Sanitize fname — remove any % characters that would break string formatting
    fname = fname.replace('%', '')
    # Also strip if too long
    if len(fname) > 50:
        fname = fname[:47] + '...'

    log('[ANALYST] Analyzing "%s" as a test architect...' % fname)

    # ── Step 1: Identify feature type ──
    ftype = _detect_feature_type(fname, ctx)
    log('[ANALYST]   Feature type: %s' % ftype)

    # ── Step 2: Detect feature lifecycle ──
    lifecycle = _detect_lifecycle(fname, ctx)
    if lifecycle:
        log('[ANALYST]   Lifecycle detected: %s' % lifecycle)

    # ── Step 3: Core thinking — what does a QA engineer ask? ──
    suggestions.extend(_core_qa_thinking(fname, ftype, ctx, existing))

    # ── Step 4: Feature-type-specific thinking ──
    if ftype == 'api_crud':
        suggestions.extend(_api_crud_thinking(fname, ctx, existing))
    elif ftype == 'async_workflow':
        suggestions.extend(_async_workflow_thinking(fname, ctx, existing))
    elif ftype == 'notification':
        suggestions.extend(_notification_thinking(fname, ctx, existing))
    elif ftype == 'batch_report':
        suggestions.extend(_batch_report_thinking(fname, ctx, existing))
    elif ftype == 'ui_portal':
        suggestions.extend(_ui_portal_thinking(fname, ctx, existing))

    # ── Step 5: Lifecycle-specific scenarios ──
    if lifecycle:
        suggestions.extend(_lifecycle_thinking(fname, lifecycle, ctx, existing))

    # ── Step 6: Line-state scenarios (the real negative cases) ──
    if ftype in ('api_crud', 'async_workflow'):
        suggestions.extend(_line_state_thinking(fname, ctx, existing))

    # ── Step 7: Verification layer (what a QA checks AFTER the operation) ──
    if ftype in ('api_crud', 'async_workflow'):
        suggestions.extend(_verification_thinking(fname, ctx, existing))

    # ── Step 8: Deduplicate against existing scenarios ──
    unique = []
    for s in suggestions:
        title_words = set(re.findall(r'\b\w{5,}\b', s['title'].lower()))
        if not title_words:
            unique.append(s)
            continue
        overlap = sum(1 for w in title_words if w in existing) / len(title_words)
        if overlap < 0.5:
            unique.append(s)

    log('[ANALYST]   Generated %d analyst scenarios (%d after dedup)' % (len(suggestions), len(unique)))
    return unique


# ================================================================
# FEATURE TYPE DETECTION
# ================================================================

def _detect_feature_type(fname, ctx):
    fl = fname.lower()
    if any(kw in ctx for kw in ['async', 'callback', 'webhook', 'asynchronous']):
        return 'async_workflow'
    if any(kw in fl for kw in ['swap', 'change', 'activate', 'deactivat', 'port',
                                'hotline', 'suspend', 'reconnect', 'reset', 'inquiry']):
        return 'api_crud'
    if any(kw in ctx for kw in ['notification', 'kafka', 'dpfo', 'suppress', 'usage event']):
        return 'notification'
    if any(kw in ctx for kw in ['report', 'batch', 'file', 'csv', 'differential', 'reconcil']):
        return 'batch_report'
    if any(kw in ctx for kw in ['nbop', 'portal', 'menu', 'display', 'screen', 'ui']):
        return 'ui_portal'
    return 'api_crud'


def _detect_lifecycle(fname, ctx):
    """Detect the lifecycle pattern of this feature.
    Port-in: CP/CE → PU/PD → PC (create → update → cancel)
    Activation: Activate → Change → Deactivate
    Hotline: Enable → Remove → Suspend → Reconnect
    Swap: Initiate → Confirm → Rollback
    """
    fl = fname.lower()
    if any(kw in fl for kw in ['port-in', 'port in', 'portin', 'change mdn port']):
        return 'port_in'
    if any(kw in fl for kw in ['swap mdn', 'swap device']):
        return 'swap'
    if any(kw in fl for kw in ['activat']):
        return 'activation'
    if any(kw in fl for kw in ['hotline', 'enable hotline']):
        return 'hotline'
    if any(kw in fl for kw in ['suspend']):
        return 'suspend'
    if any(kw in fl for kw in ['deactivat']):
        return 'deactivation'
    if any(kw in fl for kw in ['change sim', 'change iccid']):
        return 'change_sim'
    if any(kw in fl for kw in ['change imei', 'change device']):
        return 'change_device'
    if any(kw in fl for kw in ['change feature', 'optional feature']):
        return 'change_feature'
    if any(kw in fl for kw in ['change rate', 'change bcd']):
        return 'change_rateplan'
    return None


# ================================================================
# CORE QA THINKING — what a real QA engineer asks for ANY feature
# ================================================================

def _core_qa_thinking(fname, ftype, ctx, existing):
    """The first 5 questions a QA engineer asks in a test planning meeting."""
    s = []

    # Q1: "Does it work at all?" — but phrased like a real TC
    # CDR/Mediation features don't use ITMBO/NBOP channels
    _is_cdr = any(kw in (ctx + ' ' + fname).lower() for kw in ['cdr', 'mediation', 'prr', 'ild', 'roaming', 'country code'])
    _channel = 'Mediation pipeline' if _is_cdr else 'ITMBO channel'
    if 'happy path' not in existing or 'successful' not in existing:
        s.append({
            'title': 'Verify %s completes successfully with valid inputs via %s.' % (fname, _channel),
            'description': 'Trigger %s with all valid parameters via %s. '
                           'Verify NSL accepts request, TMO responds, and operation completes end-to-end.' % (fname, _channel),
            'category': 'Happy Path',
            'reasoning': 'Basic sanity — does the feature work on the primary channel?',
        })

    # Q2: "What if someone does it twice?" — real scenario, not theoretical
    if 'duplicate' not in existing and 'twice' not in existing and 'idempoten' not in existing:
        s.append({
            'title': 'Verify %s rejects duplicate request for same MDN within processing window.' % fname,
            'description': 'Trigger %s for an MDN, then immediately trigger again before first completes. '
                           'Second request should be rejected or queued, not create duplicate records.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Duplicate requests happen in production from retries and network glitches.',
        })

    # Q3: "What does the data look like after?" — the most important check
    if 'data integrity' not in existing and 'db state' not in existing:
        s.append({
            'title': 'Verify NSL DB state is consistent after successful %s.' % fname,
            'description': 'After %s, query NSL DB directly. Verify no orphaned records, '
                           'no mismatched foreign keys, all timestamps correct.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Inconsistent DB state is the #1 production bug. Every operation must leave clean data.',
        })

    return s


# ================================================================
# API CRUD THINKING — specific to line-level API operations
# ================================================================

def _api_crud_thinking(fname, ctx, existing):
    s = []

    # "What does the API response actually contain?"
    if 'response' not in existing or 'payload' not in existing:
        s.append({
            'title': 'Verify %s API response payload contains all required fields.' % fname,
            'description': 'Verify %s response includes transactionId, rootTransactionId, status, '
                           'timestamp, and all fields per API spec. No null values for required fields.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Downstream consumers parse the response. Missing fields break integrations silently.',
        })

    # "What if TMO takes too long to respond?"
    if 'timeout' not in existing and 'tmo' not in existing:
        s.append({
            'title': 'Negative: Verify %s handles TMO/Apollo-NE timeout gracefully.' % fname,
            'description': 'Simulate TMO not responding within SLA timeout during %s. '
                           'Verify NSL retries per config, then returns appropriate error. No data corruption.' % fname,
            'category': 'Negative',
            'reasoning': 'TMO timeouts are the #1 production issue. Every external call can timeout.',
        })

    # "What if it fails halfway through?"
    if 'partial' not in existing and 'mid-operation' not in existing and 'rollback' not in existing:
        s.append({
            'title': 'Negative: Verify %s rolls back cleanly on mid-operation failure.' % fname,
            'description': 'Simulate failure after NSL sends request to TMO but before receiving response during %s. '
                           'Verify rollback restores original state. No partial updates in DB.' % fname,
            'category': 'Negative',
            'reasoning': 'Multi-step operations can fail at any point. Each failure point needs rollback.',
        })

    return s


# ================================================================
# ASYNC WORKFLOW THINKING
# ================================================================

def _async_workflow_thinking(fname, ctx, existing):
    s = []

    if 'callback' not in existing and 'async' not in existing:
        s.append({
            'title': 'Verify %s async callback from TMO is received and processed correctly.' % fname,
            'description': 'Trigger %s, wait for async callback from TMO/Apollo-NE. '
                           'Verify callback payload matches request, status updated in DB.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Async callbacks are where things go wrong. The callback IS the operation.',
        })

    if 'delayed' not in existing and 'late callback' not in existing:
        s.append({
            'title': 'Verify %s handles delayed async callback arriving after timeout.' % fname,
            'description': 'Trigger %s, let callback timeout (>50 sec), then send late callback. '
                           'Verify no inconsistent state — system should either accept or reject cleanly.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Late callbacks after timeout create the worst bugs — two conflicting states.',
        })

    if 'out of order' not in existing and 'wrong order' not in existing:
        s.append({
            'title': 'Verify %s handles out-of-order async callbacks.' % fname,
            'description': 'Send %s callbacks in wrong sequence. '
                           'Verify system detects ordering issue and handles gracefully.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Network does not guarantee message order.',
        })

    return s


# ================================================================
# NOTIFICATION THINKING
# ================================================================

def _notification_thinking(fname, ctx, existing):
    s = []
    if 'notification' not in existing or 'payload' not in existing:
        s.append({
            'title': 'Verify %s KAFKA notification payload matches expected schema.' % fname,
            'description': 'Verify %s notification message has all required fields, correct format, valid values.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Downstream consumers parse the notification. Wrong schema breaks them silently.',
        })
    if 'suppress' not in existing:
        s.append({
            'title': 'Verify %s notification suppression rules applied correctly.' % fname,
            'description': 'Verify %s notifications are suppressed when business rules dictate (e.g., duplicate events).' % fname,
            'category': 'Edge Case',
            'reasoning': 'Notification flooding is a real problem. Suppression logic needs testing.',
        })
    return s


# ================================================================
# BATCH/REPORT THINKING
# ================================================================

def _batch_report_thinking(fname, ctx, existing):
    s = []
    s.append({
        'title': 'Verify %s output file format, headers, and row count.' % fname,
        'description': 'Verify %s file has correct headers, delimiters, encoding, and row count matches expected.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Downstream systems parse the file. Wrong format = silent data loss.',
    })
    if 'empty' not in existing:
        s.append({
            'title': 'Verify %s handles empty input data without crash.' % fname,
            'description': 'Run %s with no input records. Verify empty file or appropriate message, no crash.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Empty data is a common edge case that crashes batch jobs.',
        })
    return s


# ================================================================
# UI/PORTAL THINKING
# ================================================================

def _ui_portal_thinking(fname, ctx, existing):
    s = []
    if 'portal' not in existing or 'reflect' not in existing:
        s.append({
            'title': 'Verify NBOP portal reflects %s changes immediately.' % fname,
            'description': 'After %s, refresh NBOP portal. Verify updated values display without cache delay.' % fname,
            'category': 'Happy Path',
            'reasoning': 'UI caching can show stale data. Users need to see changes immediately.',
        })
    if 'session' not in existing:
        s.append({
            'title': 'Verify %s handles NBOP session timeout during operation.' % fname,
            'description': 'Start %s in NBOP, let session expire mid-operation. Verify no partial state.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Session timeouts during multi-step UI flows cause partial operations.',
        })
    return s


# ================================================================
# LIFECYCLE THINKING — the real QA differentiator
# A senior QA knows that Port-in isn't just "trigger API".
# It's CP → PU → PD → PC, and each step has its own test surface.
# ================================================================

def _lifecycle_thinking(fname, lifecycle, ctx, existing):
    """Generate scenarios based on the feature's lifecycle.
    This is what separates a junior QA from a senior one."""
    s = []

    if lifecycle == 'port_in':
        # Port-in lifecycle: CP/CE → PU/PD → PC
        # A real QA tests the TRANSITIONS, not just individual operations

        if 'update' not in existing and 'pu' not in existing:
            s.append({
                'title': 'Verify Update Port-in (PU) succeeds over active %s.' % fname,
                'description': 'After successful %s, trigger Update Port-in (PU) to correct '
                               'OSP account, PIN, lnpName, lnpAddress. Verify update accepted.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Update Port-in is the most common follow-up to CP/CE. Customers correct their info.',
            })

        if 'cancel' not in existing and 'pc' not in existing:
            s.append({
                'title': 'Verify Cancel Port-in (PC) succeeds over active %s.' % fname,
                'description': 'After successful %s, trigger Cancel Port-in (PC). '
                               'Verify cancellation accepted and original state restored.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Cancel is a critical lifecycle step. Customer changes their mind.',
            })

        if 'cancel' not in existing or 'failed' not in existing:
            s.append({
                'title': 'Negative: Verify Cancel Port-in (PC) fails with incorrect MDN.',
                'description': 'Trigger Cancel Port-in with incorrect MDN over active %s. '
                               'Verify rejection with appropriate error code.' % fname,
                'category': 'Negative',
                'reasoning': 'Cancel with wrong MDN is a real scenario — typos happen.',
            })

        if 'cancel' not in existing or 'update' not in existing:
            s.append({
                'title': 'Verify Cancel Port-in (PC) succeeds after failed Update Port-in.',
                'description': 'Trigger %s, then trigger Update Port-in that fails, '
                               'then trigger Cancel Port-in. Verify cancel still works despite failed update.' % fname,
                'category': 'Edge Case',
                'reasoning': 'Real-world: update fails, customer wants to cancel. Must still work.',
            })

        if 'imsi' not in existing and 'line enquiry' not in existing:
            s.append({
                'title': 'Negative: Verify %s fails when IMSI LineEnquiry call fails.' % fname,
                'description': 'Simulate IMSI LineEnquiry returning error during %s. '
                               'Verify %s is rejected gracefully, no partial state.' % (fname, fname),
                'category': 'Negative',
                'reasoning': 'IMSI lookup is a prerequisite. If it fails, the whole operation must fail cleanly.',
            })

    elif lifecycle == 'swap':
        if 'confirm' not in existing:
            s.append({
                'title': 'Verify %s confirmation step completes after initial request.' % fname,
                'description': 'After initial %s request accepted, trigger confirmation. '
                               'Verify swap finalized and all systems updated.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Swap is two-phase: request + confirm. Both must work.',
            })

    elif lifecycle == 'activation':
        if 'already active' not in existing:
            s.append({
                'title': 'Negative: Verify %s rejected for already-active line.' % fname,
                'description': 'Trigger %s for a line that is already Active. '
                               'Verify rejection — cannot activate an active line.' % fname,
                'category': 'Negative',
                'reasoning': 'Double-activation is a common mistake. System must reject.',
            })

    elif lifecycle == 'hotline':
        if 'already hotlined' not in existing:
            s.append({
                'title': 'Negative: Verify %s rejected for already-hotlined MDN.' % fname,
                'description': 'Trigger %s for MDN already in Hotlined status. '
                               'Verify rejection with appropriate error.' % fname,
                'category': 'Negative',
                'reasoning': 'Cannot hotline a line that is already hotlined.',
            })
        if 'suspend after' not in existing and 'suspend' not in existing:
            s.append({
                'title': 'Verify Suspend succeeds on a Hotlined MDN after %s.' % fname,
                'description': 'After %s, trigger Suspend on the same MDN. '
                               'Verify Suspend overrides Hotline status correctly.' % fname,
                'category': 'Edge Case',
                'reasoning': 'Hotline → Suspend is a real lifecycle transition.',
            })

    elif lifecycle in ('change_sim', 'change_device'):
        if 'wearable' not in existing:
            s.append({
                'title': 'Verify %s succeeds for primary line with active wearable.' % fname,
                'description': 'Trigger %s on a primary line that has an associated active wearable. '
                               'Verify wearable association is maintained after operation.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Wearable association is easily broken by SIM/device changes.',
            })

    return s


# ================================================================
# LINE-STATE THINKING — the REAL negative scenarios
# A junior QA writes "invalid input". A senior QA writes
# "Hotlined MDN", "Suspended MDN", "Line with pending port-out".
# ================================================================

def _line_state_thinking(fname, ctx, existing):
    """Generate scenarios based on line states that should block the operation."""
    s = []

    # These are the REAL negative scenarios — not "invalid input" but specific business states
    LINE_STATES = [
        ('not in active status', 'not active', 'not in active',
         'Negative: Verify %s rejected when line is not in Active status.' % fname,
         'Trigger %s for a line that is NOT Active (e.g., New, Pending). '
         'Verify rejection with appropriate error code.' % fname),
        ('pending port-out', 'port-out', 'pending port',
         'Negative: Verify %s rejected for MDN with pending Port-Out.' % fname,
         'Trigger %s for an MDN that has a pending Port-Out request. '
         'Verify rejection — cannot modify a line being ported out.' % fname),
        ('wearable line', 'wearable', 'smartwatch',
         'Verify %s behavior for wearable/smartwatch line.' % fname,
         'Trigger %s for a wearable line (not primary phone line). '
         'Verify operation handles wearable-specific constraints.' % fname),
    ]

    for check_kw1, check_kw2, check_kw3, title, desc in LINE_STATES:
        if check_kw1 not in existing and check_kw2 not in existing and check_kw3 not in existing:
            cat = 'Negative' if 'Negative' in title else 'Edge Case'
            s.append({
                'title': title,
                'description': desc,
                'category': cat,
                'reasoning': 'Line state validation — the system must check line status before proceeding.',
            })

    return s


# ================================================================
# VERIFICATION THINKING — what a QA checks AFTER the operation
# This is the difference between "it worked" and "it REALLY worked"
# ================================================================

def _verification_thinking(fname, ctx, existing):
    """Post-operation verification scenarios.
    A real QA doesn't just check the API response — they check 5 systems."""
    s = []

    # Century Report — the audit trail
    if 'century' not in existing and 'century report' not in existing:
        s.append({
            'title': 'Verify Century Report logs all %s backend calls correctly.' % fname,
            'description': 'After %s, download Century Report using Root Transaction ID. '
                           'Verify all inbound/outbound calls logged with correct status codes.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Century Report is the single source of truth for debugging production issues.',
        })

    # NBOP MIG tables — the operational database
    if 'nbop mig' not in existing and 'mig table' not in existing and 'mig_' not in existing:
        s.append({
            'title': 'Verify NBOP MIG tables reflect correct state after %s.' % fname,
            'description': 'After %s, check NBOP_MIG_DEVICE, NBOP_MIG_SIM, NBOP_MIG_LINE, '
                           'NBOP_MIG_FEATURE, and TRANSACTION_HISTORY tables.' % fname,
            'category': 'Happy Path',
            'reasoning': 'NBOP MIG tables drive the portal display. Wrong data = wrong portal.',
        })

    # TMO Genesis — the carrier portal
    if 'genesis' not in existing and 'tmo portal' not in existing:
        s.append({
            'title': 'Verify TMO Genesis portal reflects %s correctly.' % fname,
            'description': 'After %s, check TMO Genesis portal for updated subscriber details. '
                           'Verify MDN, ICCID, IMEI, line status all match expected.' % fname,
            'category': 'Happy Path',
            'reasoning': 'If TMO Genesis does not reflect the change, the operation failed from TMO perspective.',
        })

    # Transaction History — the user-facing audit
    if 'transaction history' not in existing:
        s.append({
            'title': 'Verify Transaction History records %s with correct details.' % fname,
            'description': 'After %s, check Transaction History for entry with correct '
                           'timestamp, transaction type, MDN, status, and user details.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Transaction History is what support agents see. Must be accurate.',
        })

    # Regression — does anything else break?
    if 'regression' not in existing:
        s.append({
            'title': 'Regression: Verify Line Inquiry and Service Grouping unaffected by %s.' % fname,
            'description': 'After %s, run Line Inquiry and Service Grouping for the same MDN. '
                           'Verify both return correct data — no regression.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Every change can break something else. Regression is non-negotiable.',
        })

    return s
