"""
test_analyst.py — Test Analyst Reasoning Engine.
Thinks like a senior QA engineer / Test Architect.
Given a feature name + context, derives test scenarios the way a human would.

This is NOT data extraction. This is REASONING about what to test.

Approach:
1. Identify the feature TYPE (API, UI, batch, notification, etc.)
2. Apply universal test thinking patterns
3. Apply domain-specific TMO/NSL patterns
4. Generate scenarios with human-quality names
"""
import re
from typing import List, Tuple


def analyze_and_suggest(feature_name: str, feature_id: str,
                        scope: str = '', description: str = '',
                        existing_scenarios: List[str] = None,
                        log=print) -> List[dict]:
    """Think like a test analyst. Return list of suggested scenarios.
    Each scenario: {title, description, category, reasoning}
    """
    fname = feature_name
    ctx = (scope + ' ' + description + ' ' + fname).lower()
    existing = ' '.join(existing_scenarios or []).lower()
    suggestions = []

    log('[ANALYST] Analyzing "%s" as a test architect...' % fname)

    # ── Step 1: Identify feature type ──
    ftype = _detect_feature_type(fname, ctx)
    log('[ANALYST]   Feature type: %s' % ftype)

    # ── Step 2: Universal test thinking (applies to ALL features) ──
    suggestions.extend(_universal_thinking(fname, ftype, ctx, existing))

    # ── Step 3: Feature-type-specific thinking ──
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

    # ── Step 4: Domain-specific TMO/NSL thinking ──
    suggestions.extend(_tmo_domain_thinking(fname, ftype, ctx, existing))

    # ── Step 5: Deduplicate against existing scenarios ──
    unique = []
    for s in suggestions:
        title_words = set(re.findall(r'\b\w{5,}\b', s['title'].lower()))
        # Require >50% of title words to be in existing to consider it a duplicate
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
    """Detect what kind of feature this is."""
    fl = fname.lower()
    if any(kw in ctx for kw in ['async', 'callback', 'webhook', 'event']):
        return 'async_workflow'
    if any(kw in fl for kw in ['swap', 'change', 'activate', 'deactivat', 'port',
                                'hotline', 'suspend', 'reconnect', 'reset']):
        return 'api_crud'
    if any(kw in ctx for kw in ['notification', 'kafka', 'dpfo', 'suppress', 'usage event']):
        return 'notification'
    if any(kw in ctx for kw in ['report', 'batch', 'file', 'csv', 'differential', 'reconcil']):
        return 'batch_report'
    if any(kw in ctx for kw in ['nbop', 'portal', 'menu', 'display', 'screen', 'ui']):
        return 'ui_portal'
    if any(kw in ctx for kw in ['inquiry', 'search', 'lookup', 'fetch', 'retrieve']):
        return 'api_crud'
    return 'api_crud'  # default


# ================================================================
# UNIVERSAL TEST THINKING (applies to every feature)
# ================================================================

def _universal_thinking(fname, ftype, ctx, existing):
    """What every test engineer thinks about for ANY feature."""
    s = []

    # 1. Happy path — does it work at all?
    s.append({
        'title': 'Validate %s completes successfully with valid inputs.' % fname,
        'description': 'Trigger %s with all valid parameters and verify successful completion.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Basic sanity — does the feature work?',
    })

    # 2. Idempotency — what if I do it twice?
    if 'idempoten' not in existing and 'twice' not in existing and 'duplicate' not in existing:
        s.append({
            'title': 'Validate %s handles duplicate/repeat request gracefully.' % fname,
            'description': 'Trigger %s twice with same parameters. Second request should not cause data corruption.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Idempotency — real systems get duplicate requests from retries, network issues.',
        })

    # 3. Concurrent requests
    if 'concurrent' not in existing and 'simultaneous' not in existing:
        s.append({
            'title': 'Validate %s handles concurrent requests for same MDN.' % fname,
            'description': 'Trigger %s simultaneously for the same subscriber. No race condition or data corruption.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Concurrency — multiple channels (ITMBO + NBOP) could trigger at the same time.',
        })

    # 4. Boundary — edge of valid input
    if 'boundary' not in existing and 'max length' not in existing:
        s.append({
            'title': 'Validate %s with boundary input values (max length MDN, special chars).' % fname,
            'description': 'Test %s with 10-digit MDN, 15-digit IMEI, max-length account ID, special characters.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Boundary testing — systems break at the edges of valid input.',
        })

    # 5. Data integrity — is the DB consistent after?
    if 'data integrity' not in existing and 'db consistent' not in existing:
        s.append({
            'title': 'Validate data integrity in NSL DB after %s.' % fname,
            'description': 'After successful %s, verify all DB tables are consistent. No orphaned records.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Data integrity — the most common production bug is inconsistent DB state.',
        })

    return s


# ================================================================
# API CRUD THINKING (Change, Swap, Activate, Port, etc.)
# ================================================================

def _api_crud_thinking(fname, ctx, existing):
    """How a test engineer thinks about API-based CRUD operations."""
    s = []

    # Pre-operation state verification
    s.append({
        'title': 'Validate pre-%s state is captured correctly before operation.' % fname,
        'description': 'Before triggering %s, capture current MDN, ICCID, IMEI, line status. After operation, compare.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Pre/post comparison is how you prove the operation actually changed something.',
    })

    # Post-operation downstream verification
    if 'downstream' not in existing and 'external system' not in existing:
        s.append({
            'title': 'Validate all downstream systems updated after %s.' % fname,
            'description': 'After %s, verify MBO, Syniverse, Connection Manager, KAFKA all received correct updates.' % fname,
            'category': 'Happy Path',
            'reasoning': 'NSL talks to 5+ downstream systems. Each one needs verification.',
        })

    # API response payload validation
    if 'response payload' not in existing and 'response body' not in existing:
        s.append({
            'title': 'Validate %s API response contains all expected fields.' % fname,
            'description': 'Verify %s response includes transactionId, status, timestamp, and all required fields per API spec.' % fname,
            'category': 'Happy Path',
            'reasoning': 'API consumers depend on specific response fields. Missing fields break integrations.',
        })

    # Partial failure / mid-operation crash
    if 'partial' not in existing and 'mid-operation' not in existing:
        s.append({
            'title': 'Negative: Validate %s handles partial failure at each step.' % fname,
            'description': 'Simulate failure at step 2, step 3, etc. of %s. Verify rollback or graceful degradation.' % fname,
            'category': 'Negative',
            'reasoning': 'Multi-step operations can fail at any point. Each failure point needs a test.',
        })

    # Timeout at each integration point
    if 'timeout' not in existing:
        s.append({
            'title': 'Negative: Validate %s handles timeout from Apollo-NE/TMO.' % fname,
            'description': 'Simulate Apollo-NE not responding during %s. Verify retry logic and error handling.' % fname,
            'category': 'Negative',
            'reasoning': 'Network timeouts are the #1 production issue. Every external call can timeout.',
        })

    return s


# ================================================================
# ASYNC WORKFLOW THINKING
# ================================================================

def _async_workflow_thinking(fname, ctx, existing):
    """How a test engineer thinks about async callback flows."""
    s = []

    s.append({
        'title': 'Validate %s async callback received and processed correctly.' % fname,
        'description': 'Trigger %s, wait for async callback from TMO/Apollo-NE. Verify callback payload and processing.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Async flows are the hardest to test. The callback is where things go wrong.',
    })

    if 'retry' not in existing and 'multiple' not in existing:
        s.append({
            'title': 'Validate %s handles multiple async retries without data corruption.' % fname,
            'description': 'Simulate %s async callback arriving 2-3 times (retry scenario). Verify idempotent processing.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Async retries are common. The system must handle duplicates gracefully.',
        })

    if 'delayed' not in existing and 'late' not in existing:
        s.append({
            'title': 'Validate %s handles delayed async callback (arrives after timeout).' % fname,
            'description': 'Trigger %s, let callback timeout, then send late callback. Verify no inconsistent state.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Late callbacks after timeout create the worst bugs — two conflicting states.',
        })

    if 'out of order' not in existing:
        s.append({
            'title': 'Validate %s handles out-of-order async callbacks.' % fname,
            'description': 'Send %s callbacks in wrong order (step 3 before step 2). Verify system handles gracefully.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Network doesn\'t guarantee order. Out-of-order callbacks are a real scenario.',
        })

    return s


# ================================================================
# NOTIFICATION THINKING
# ================================================================

def _notification_thinking(fname, ctx, existing):
    s = []
    s.append({
        'title': 'Validate %s notification payload matches expected schema.' % fname,
        'description': 'Verify %s KAFKA/notification message has all required fields, correct format, valid values.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Downstream consumers parse the notification. Wrong schema breaks them silently.',
    })
    if 'suppress' not in existing:
        s.append({
            'title': 'Validate %s notification suppression rules applied correctly.' % fname,
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
        'title': 'Validate %s output file format and structure.' % fname,
        'description': 'Verify %s file has correct headers, delimiters, encoding, and row count matches expected.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Downstream systems parse the file. Wrong format = silent data loss.',
    })
    s.append({
        'title': 'Validate %s handles empty input data gracefully.' % fname,
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
    s.append({
        'title': 'Validate %s UI reflects changes immediately after operation.' % fname,
        'description': 'After %s, refresh NBOP portal. Verify updated values display without cache delay.' % fname,
        'category': 'Happy Path',
        'reasoning': 'UI caching can show stale data. Users need to see changes immediately.',
    })
    s.append({
        'title': 'Validate %s UI handles session timeout during operation.' % fname,
        'description': 'Start %s in NBOP, let session expire mid-operation. Verify no partial state.' % fname,
        'category': 'Edge Case',
        'reasoning': 'Session timeouts during multi-step UI flows cause partial operations.',
    })
    return s


# ================================================================
# TMO/NSL DOMAIN-SPECIFIC THINKING
# ================================================================

def _tmo_domain_thinking(fname, ftype, ctx, existing):
    """Domain knowledge specific to TMO/NSL integration."""
    s = []

    # Century Report verification — ALWAYS for any API feature
    if ftype in ('api_crud', 'async_workflow') and 'century' not in existing:
        s.append({
            'title': 'Validate Century Report shows all %s backend calls.' % fname,
            'description': 'Download Century Report after %s. Verify all inbound/outbound calls logged with correct status.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Century Report is the single source of truth for debugging. Every call must be logged.',
        })

    # NBOP MIG tables — for any line-level operation
    if ftype in ('api_crud', 'async_workflow') and 'nbop mig' not in existing and 'mig table' not in existing:
        s.append({
            'title': 'Validate NBOP MIG tables updated correctly after %s.' % fname,
            'description': 'Check NBOP_MIG_DEVICE, NBOP_MIG_SIM, NBOP_MIG_LINE, NBOP_MIG_FEATURE, TRANSACTION_HISTORY after %s.' % fname,
            'category': 'Happy Path',
            'reasoning': 'NBOP MIG tables are the operational database. Incorrect data = wrong portal display.',
        })

    # TMO Genesis portal verification
    if ftype in ('api_crud', 'async_workflow') and 'genesis' not in existing and 'tmo portal' not in existing:
        s.append({
            'title': 'Validate TMO Genesis portal reflects %s correctly.' % fname,
            'description': 'After %s, check TMO Genesis portal for updated subscriber details.' % fname,
            'category': 'Happy Path',
            'reasoning': 'TMO Genesis is the carrier portal. If it doesn\'t reflect the change, the operation failed from TMO\'s perspective.',
        })

    # Regression on related features
    if 'regression' not in existing:
        s.append({
            'title': 'Regression: Validate related features unaffected by %s.' % fname,
            'description': 'After %s, verify Line Inquiry, Service Grouping, and Transaction History still work correctly.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Every change can break something else. Regression testing is non-negotiable.',
        })

    return s
