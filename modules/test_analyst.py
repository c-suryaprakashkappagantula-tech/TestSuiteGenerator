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
                        log=print, ac_text: str = '', channel: str = '',
                        jira_summary: str = '') -> List[dict]:
    """Think like a test analyst. Return list of suggested scenarios."""
    fname = feature_name
    ctx = (scope + ' ' + description + ' ' + fname + ' ' + ac_text + ' ' + channel + ' ' + jira_summary).lower()
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
    # Skip for UI features when KB provides rich scenarios (Step 4 handles it)
    # Skip for notification/CDR features — they have their own thinking
    if ftype in ('ui_portal', 'notification', 'batch_report'):
        pass  # Handled by feature-type-specific thinking in Step 4
    else:
        suggestions.extend(_core_qa_thinking(fname, ftype, ctx, existing))

    # ── Step 4: Feature-type-specific thinking ──
    if ftype == 'api_crud':
        suggestions.extend(_api_crud_thinking(fname, ctx, existing))
    elif ftype == 'async_workflow':
        suggestions.extend(_async_workflow_thinking(fname, ctx, existing))
    elif ftype == 'notification':
        suggestions.extend(_notification_thinking(fname, ctx, existing))
        # For notification/CDR features, return immediately — skip API layers
        log('[ANALYST]   Notification/CDR feature — returning %d scenarios (skipping API layers)' % len(suggestions))
        return suggestions
    elif ftype == 'batch_report':
        suggestions.extend(_batch_report_thinking(fname, ctx, existing))
        # For batch features, return immediately — skip API layers
        log('[ANALYST]   Batch feature — returning %d scenarios (skipping API layers)' % len(suggestions))
        return suggestions
    elif ftype == 'ui_portal':
        suggestions.extend(_ui_portal_thinking(fname, ctx, existing))
        # For PURE UI features, return immediately — KB provides all scenarios.
        log('[ANALYST]   Pure UI feature — returning %d KB scenarios (skipping API layers)' % len(suggestions))
        return suggestions
    elif ftype == 'hybrid':
        # HYBRID: get BOTH UI KB scenarios AND API scenarios
        suggestions.extend(_ui_portal_thinking(fname, ctx, existing))
        suggestions.extend(_api_crud_thinking(fname, ctx, existing))
        log('[ANALYST]   Hybrid feature — %d scenarios (UI + API combined)' % len(suggestions))
        # DON'T return early — let lifecycle, line-state, verification also run

    # ── Step 5: Lifecycle-specific scenarios ──
    if lifecycle:
        suggestions.extend(_lifecycle_thinking(fname, lifecycle, ctx, existing))

    # ── Step 5b: CONTRACT-DRIVEN integration thinking ──
    # Instead of per-feature hardcoding, consult the global integration contract.
    # The contract knows which systems each operation touches and which it doesn't.
    from .integration_contract import resolve_operation, get_syniverse_assertion, get_must_not_call_systems
    _contract = resolve_operation(fname, description=ctx, ac_text=ctx)
    if _contract:
        log('[ANALYST]   Contract: "%s" (syniverse=%s)' % (_contract.operation, _contract.syniverse_action))
        suggestions.extend(_contract_driven_thinking(fname, _contract, ctx, existing))
    else:
        # Fallback: legacy Syniverse-specific thinking for unregistered operations
        if any(kw in ctx for kw in ['syniverse', 'createsubscriber', 'removesubscriber', 'swapimsi',
                                     'create subscriber', 'remove subscriber', 'swap imsi']):
            suggestions.extend(_syniverse_integration_thinking(fname, ctx, existing))
        elif any(kw in ctx for kw in ['hotline', 'remove hotline']) and 'syniverse' not in existing:
            suggestions.extend(_syniverse_no_call_thinking(fname, ctx, existing))

    # ── Step 6: Line-state scenarios (the real negative cases) ──
    if ftype in ('api_crud', 'async_workflow'):
        suggestions.extend(_line_state_thinking(fname, ctx, existing))

    # ── Step 7: Verification layer (what a QA checks AFTER the operation) ──
    if ftype in ('api_crud', 'async_workflow'):
        suggestions.extend(_verification_thinking(fname, ctx, existing))

    # ── Step 8: Deduplicate against existing scenarios ──
    # For UI features, use looser dedup (0.7) since UI scenarios share common words
    # like "verify", "NBOP", "portal", "screen", "subscriber"
    dedup_threshold = 0.7 if ftype in ('ui_portal', 'hybrid') else 0.5
    unique = []
    for s in suggestions:
        title_words = set(re.findall(r'\b\w{5,}\b', s['title'].lower()))
        # Remove common UI words from dedup check — they appear everywhere
        _common = {'verify', 'validate', 'portal', 'screen', 'subscriber', 'nbop',
                   'mobile', 'service', 'management', 'information', 'details'}
        title_words = title_words - _common
        if not title_words:
            unique.append(s)
            continue
        overlap = sum(1 for w in title_words if w in existing) / len(title_words)
        if overlap < dedup_threshold:
            unique.append(s)

    log('[ANALYST]   Generated %d analyst scenarios (%d after dedup)' % (len(suggestions), len(unique)))
    return unique


# ================================================================
# FEATURE TYPE DETECTION
# ================================================================

def _detect_feature_type(fname, ctx):
    """Detect feature type — delegates to classify_feature for consistency."""
    from .tc_templates import classify_feature
    # Extract channel from ctx if present (ctx contains all text including channel)
    channel = ''
    if 'channel: nbop' in ctx.lower() or '\nnbop\n' in ctx.lower() or ctx.lower().endswith('nbop'):
        channel = 'NBOP'
    elif 'channel: itmbo' in ctx.lower() or '\nitmbo\n' in ctx.lower() or ctx.lower().endswith('itmbo'):
        channel = 'ITMBO'
    # Also check if "nbop" appears as a standalone word (from channel field appended to ctx)
    import re
    if re.search(r'\bnbop\b', ctx.lower()) and not re.search(r'\b(nslnm|nenm|itmbo)\b', ctx.lower()):
        channel = 'NBOP'
    fc = classify_feature(
        feature_name=fname, description=ctx,
        channel=channel, jira_summary='', ac_text=ctx, scope='')
    return fc.feature_type


def _detect_lifecycle(fname, ctx):
    """Detect the feature's lifecycle pattern.
    UI features return None — they don't have API lifecycles."""
    from .tc_templates import classify_feature
    fc = classify_feature(feature_name=fname, description=ctx, ac_text=ctx)
    if fc.is_ui:
        return None

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

    # UI/Portal features get different core questions — focused on screen/navigation, not API
    if ftype == 'ui_portal':
        if 'navigation' not in existing and 'menu' not in existing:
            s.append({
                'title': 'Verify NBOP navigation to %s screen via correct menu path.' % fname,
                'description': 'Navigate to %s screen via NBOP menu. '
                               'Verify screen loads correctly with all expected fields and labels.' % fname,
                'category': 'Happy Path',
                'reasoning': 'If users cannot reach the screen via the menu, the feature is unusable.',
            })
        if 'valid mdn' not in existing and 'search' not in existing and 'lookup' not in existing:
            s.append({
                'title': 'Verify %s screen returns correct results for valid MDN.' % fname,
                'description': 'Enter a valid MDN on %s screen and submit. '
                               'Verify correct data is returned and displayed in the portal.' % fname,
                'category': 'Happy Path',
                'reasoning': 'The primary user action — search/lookup must return correct data.',
            })
        if 'invalid' not in existing or 'error' not in existing:
            s.append({
                'title': 'Negative: Verify %s screen shows error for invalid MDN.' % fname,
                'description': 'Enter invalid MDN (non-numeric, too short, too long) on %s screen. '
                               'Verify appropriate error message displayed — no crash or blank screen.' % fname,
                'category': 'Negative',
                'reasoning': 'Users type wrong data. UI must show clear error, not crash.',
            })
        return s

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
    """Generate mediation/CDR/notification-specific scenarios.
    These features deal with PRR files, mediation pipelines, DPFO thresholds,
    usage events — NOT API triggers or Century Reports."""
    s = []

    is_cdr = any(kw in ctx for kw in ['cdr', 'mediation', 'prr', 'usage', 'roaming',
                                       'ild', 'country code', 'metering'])
    is_dpfo = any(kw in ctx for kw in ['dpfo', 'data plan', 'throttle', 'threshold',
                                        '80%', '100%', 'speed reduction'])

    # ── Mediation/CDR scenarios ──
    if is_cdr:
        s.append({
            'title': 'Validate %s PRR file processing through mediation pipeline.' % fname,
            'description': 'Submit PRR records through mediation. Verify records processed correctly '
                           'and output matches expected derivation rules.',
            'category': 'Happy Path',
            'reasoning': 'Core mediation flow — PRR must be processed correctly.',
            'precondition': 'Mediation and PRR batch jobs running. SFTP access available.',
        })
        s.append({
            'title': 'Validate %s derivation rules applied correctly to PRR output.' % fname,
            'description': 'Submit PRR with specific input fields. Verify derivation rules map '
                           'input to correct output values (country codes, call types, etc.).',
            'category': 'Happy Path',
            'reasoning': 'Derivation rules are the core logic. Wrong mapping = wrong billing.',
            'precondition': 'Mediation and PRR batch jobs running.',
        })
        s.append({
            'title': 'Validate %s handles invalid/malformed PRR records gracefully.' % fname,
            'description': 'Submit PRR with null, empty, or malformed fields. '
                           'Verify mediation rejects or handles gracefully without crash.',
            'category': 'Negative',
            'reasoning': 'Bad input data must not crash the pipeline.',
            'precondition': 'Mediation and PRR batch jobs running.',
        })
        s.append({
            'title': 'Validate %s handles unrecognized codes in PRR input.' % fname,
            'description': 'Submit PRR with unrecognized country codes, call types, or prefix values. '
                           'Verify no incorrect mapping applied.',
            'category': 'Negative',
            'reasoning': 'Unrecognized codes must not silently map to wrong values.',
            'precondition': 'Mediation and PRR batch jobs running.',
        })
        s.append({
            'title': 'Validate %s PRR output file available on SFTP.' % fname,
            'description': 'After mediation processing, connect to SFTP and verify PRR output file '
                           'is available with correct filename format and content.',
            'category': 'Happy Path',
            'reasoning': 'PRR output must be accessible for downstream processing.',
            'precondition': 'Mediation processing completed. SFTP access via FileZilla.',
        })

    # ── DPFO/Notification scenarios ──
    if is_dpfo:
        s.append({
            'title': 'Validate %s 80%% usage threshold notification.' % fname,
            'description': 'Submit PRRs until mediation usage reaches 80%%. '
                           'Verify DPFO 80%% notification is sent correctly.',
            'category': 'Happy Path',
            'reasoning': '80% threshold is a critical billing notification.',
            'precondition': 'Active subscriber line. Mediation PRR metering below 80%.',
        })
        s.append({
            'title': 'Validate %s 100%% usage threshold notification.' % fname,
            'description': 'Continue submitting PRRs until mediation usage reaches 100%%. '
                           'Verify DPFO 100%% notification is sent correctly.',
            'category': 'Happy Path',
            'reasoning': '100% threshold triggers speed reduction.',
            'precondition': 'Active subscriber line. Mediation PRR metering below 100%.',
        })
        s.append({
            'title': 'Validate %s does NOT send duplicate notifications.' % fname,
            'description': 'After 80%%/100%% notification is sent, submit more PRRs. '
                           'Verify no duplicate notification is triggered.',
            'category': 'Negative',
            'reasoning': 'Duplicate notifications confuse customers and downstream systems.',
            'precondition': 'Notification already sent for current threshold.',
        })
        s.append({
            'title': 'Validate %s speed reduction flag after 100%% threshold.' % fname,
            'description': 'After 100%% threshold reached, verify speed reduction flag is set '
                           'correctly in subscriber profile.',
            'category': 'Happy Path',
            'reasoning': 'Speed reduction is the business action triggered by 100% usage.',
            'precondition': '100% usage threshold reached.',
        })

    # ── General notification scenarios ──
    if 'notification' in ctx or 'kafka' in ctx:
        if 'payload' not in existing:
            s.append({
                'title': 'Validate %s notification payload matches expected schema.' % fname,
                'description': 'Verify notification message has all required fields, correct format, valid values.',
                'category': 'Happy Path',
                'reasoning': 'Downstream consumers parse the notification. Wrong schema breaks them.',
                'precondition': 'Notification trigger condition met.',
            })
        if 'suppress' not in existing:
            s.append({
                'title': 'Validate %s notification suppression rules.' % fname,
                'description': 'Verify notifications are suppressed when business rules dictate '
                               '(e.g., duplicate events, inactive subscriber).',
                'category': 'Edge Case',
                'reasoning': 'Notification flooding is a real problem.',
                'precondition': 'Suppression condition configured.',
            })

    # Early return for notification/CDR — skip API layers
    if s:
        return s

    # Fallback
    s.append({
        'title': 'Validate %s notification processing.' % fname,
        'description': 'Verify %s notification is processed correctly end-to-end.' % fname,
        'category': 'Happy Path',
        'reasoning': 'Basic notification flow.',
        'precondition': 'System in ready state.',
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
    # Load NBOP UI knowledge for real field names and menu paths
    try:
        from .nbop_ui_knowledge import (get_navigation_path, get_context_menu_items,
                                         get_edit_menu_items, PROFILE_SECTIONS,
                                         PROFILE_BUTTONS, is_available)
        has_kb = is_available()
    except Exception:
        has_kb = False

    if has_kb:
        nav_path = get_navigation_path(fname, ctx)
        context_items = get_context_menu_items()
        edit_items = get_edit_menu_items()
        # Use the knowledge base to generate rich scenarios
        from .nbop_ui_knowledge import generate_ui_scenarios
        kb_scenarios = generate_ui_scenarios(fname, ctx)
        # KB scenarios are curated — add them all without dedup
        # They represent distinct test objectives (profile load, TH, negatives, edge cases)
        s.extend(kb_scenarios)
        return s

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
    if 'navigation' not in existing and 'menu' not in existing:
        s.append({
            'title': 'Verify NBOP navigation to %s screen: %s.' % (fname, nav_path),
            'description': 'Navigate to %s via %s. '
                           'Verify screen loads correctly with all expected fields and labels.' % (fname, nav_path),
            'category': 'Happy Path',
            'reasoning': 'Navigation path must work. If users cannot reach the screen, the feature is unusable.',
        })
    if 'field' not in existing and 'label' not in existing and 'display' not in existing:
        s.append({
            'title': 'Verify all fields and labels display correctly on %s screen.' % fname,
            'description': 'Open %s screen. Verify all field labels, input fields, dropdowns, '
                           'buttons, and status indicators are present and correctly labeled.' % fname,
            'category': 'Happy Path',
            'reasoning': 'UI field validation — missing or mislabeled fields confuse users.',
        })
    if 'search' not in existing and 'lookup' not in existing:
        s.append({
            'title': 'Verify %s screen search/lookup with valid MDN.' % fname,
            'description': 'Search subscriber by MDN using SPECTRUM CORE ACCOUNT dropdown. '
                           'Verify profile loads showing Account, MDN, IMEI1, ICCID in header cards. '
                           'Then navigate to %s.' % fname,
            'category': 'Happy Path',
            'reasoning': 'The primary user action on most NBOP screens is searching by MDN.',
        })
    if 'invalid' not in existing or 'ui' not in existing:
        s.append({
            'title': 'Negative: Verify %s screen handles invalid MDN input gracefully.' % fname,
            'description': 'Enter invalid MDN (non-numeric, too short, too long) on %s screen. '
                           'Verify appropriate error message displayed in UI — no crash or blank screen.' % fname,
            'category': 'Negative',
            'reasoning': 'Users type wrong data. UI must show clear error, not crash.',
        })
    if 'empty' not in existing and 'blank' not in existing:
        s.append({
            'title': 'Negative: Verify %s screen handles empty/blank submission.' % fname,
            'description': 'Click submit/search on %s screen without entering any data. '
                           'Verify UI shows validation message — no backend call made.' % fname,
            'category': 'Negative',
            'reasoning': 'Empty submissions should be caught by UI validation, not sent to backend.',
        })
    if 'transaction history' not in existing:
        s.append({
            'title': 'Verify Transaction History records %s operation from NBOP.' % fname,
            'description': 'After completing %s via NBOP portal, navigate to ≡ Menu → Transaction History. '
                           'Verify entry shows correct timestamp, operation type, user, and status.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Every NBOP operation must be auditable in Transaction History.',
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
        # FINE-TUNE: Explicit "no Syniverse call" assertion for Hotline
        if 'syniverse' not in existing and 'no syniverse' not in existing:
            s.append({
                'title': 'Verify Syniverse is NOT called during %s operation.' % fname,
                'description': 'Trigger %s and verify that NO Syniverse outbound call '
                               '(CreateSubscriber/RemoveSubscriber/SwapIMSI) is made. '
                               'Hotline operations do not affect Syniverse subscriber state.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Manual suite explicitly validates Syniverse is NOT called for Hotline. '
                             'This is a critical negative assertion — Hotline is an internal state change only.',
                'precondition': '1.\tPhone line should be active.\n2.\tSyniverse monitoring/logs accessible.',
            })
        # FINE-TUNE: Verify ITMBO and EMM notification for Hotline
        if 'itmbo' not in existing and 'emm' not in existing:
            s.append({
                'title': 'Verify ITMBO and EMM are notified during %s.' % fname,
                'description': 'Trigger %s and verify ITMBO and EMM receive notification '
                               'of the Hotline status change with correct payload.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Per Chalk: YL transactions involving state changes must notify ITMBO and EMM.',
                'precondition': '1.\tPhone line should be active.\n2.\tITMBO and EMM endpoints accessible.',
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


# ================================================================
# SYNIVERSE INTEGRATION THINKING
# Fine-tuned based on manual vs auto suite comparison for MWTGPROV-4152
# ================================================================

def _syniverse_integration_thinking(fname, ctx, existing):
    """Generate Syniverse-specific integration scenarios.
    Based on manual suite analysis: explicit Syniverse call assertions,
    Phone vs Tablet variants, Port-In (SP) variant, and error handling."""
    s = []

    # ── Explicit Syniverse call verification per flow type ──
    # The manual suite explicitly validates WHICH Syniverse call is made for each flow

    # CreateSubscriber for Activation flows
    if any(kw in ctx for kw in ['activat', 'port-in', 'port in', 'portin']):
        if 'createsubscriber' not in existing and 'create subscriber' not in existing:
            s.append({
                'title': 'Verify Syniverse CreateSubscriber is triggered during %s.' % fname,
                'description': 'Trigger %s for a new subscriber. Verify NSL sends '
                               'Syniverse CreateSubscriber outbound call with correct IMSI, MDN, '
                               'and wholesale plan. Verify Syniverse acknowledges success.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Manual suite explicitly validates CreateSubscriber for activation flows.',
                'precondition': '1.\tPhone line should be in New/Pending state.\n'
                                '2.\tSyniverse endpoint accessible.',
            })

    # RemoveSubscriber for Deactivation flows
    if any(kw in ctx for kw in ['deactivat', 'disconnect']):
        if 'removesubscriber' not in existing and 'remove subscriber' not in existing:
            s.append({
                'title': 'Verify Syniverse RemoveSubscriber is triggered during %s.' % fname,
                'description': 'Trigger %s for an active subscriber. Verify NSL sends '
                               'Syniverse RemoveSubscriber outbound call. '
                               'Verify subscriber removed from Syniverse system.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Manual suite explicitly validates RemoveSubscriber for deactivation.',
                'precondition': '1.\tPhone line should be active with Syniverse subscriber.\n'
                                '2.\tSyniverse endpoint accessible.',
            })

    # SwapIMSI for Change SIM/Device flows (YD transactions)
    if any(kw in ctx for kw in ['change sim', 'change iccid', 'swap', 'iccid change']):
        if 'swapimsi' not in existing and 'swap imsi' not in existing:
            s.append({
                'title': 'Verify Syniverse SwapIMSI is triggered when ICCID changes during %s.' % fname,
                'description': 'Trigger %s with a new ICCID (Phone or Tablet). Verify NSL sends '
                               'Syniverse SwapIMSI outbound call with new IMSI. '
                               'Verify wholesale plan remains UNCHANGED.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Per Chalk: YD transactions involving ICCID changes must trigger SwapIMSI. '
                             'Wholesale plan must NOT change.',
                'precondition': '1.\tPhone line should be active.\n'
                                '2.\tNew ICCID/IMSI available for swap.',
            })

    # ── "No Syniverse call" assertions for non-Syniverse flows ──
    # Hotline and Remove Hotline should NOT trigger Syniverse
    if any(kw in ctx for kw in ['hotline']):
        if 'no syniverse' not in existing and 'not called' not in existing:
            s.append({
                'title': 'Verify Syniverse is NOT called during Hotline/Remove Hotline within %s.' % fname,
                'description': 'Trigger Hotline or Remove Hotline within %s flow. '
                               'Verify NO Syniverse outbound call is made. '
                               'Hotline is an internal state change only.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Manual suite explicitly validates Syniverse is NOT called for Hotline. '
                             'This is a critical negative assertion.',
                'precondition': '1.\tPhone line should be active.\n'
                                '2.\tSyniverse monitoring/Century Report accessible.',
            })

    # ── Port-In (SP) variant ──
    # Manual suite has specific TC for SP transactionType; auto suite doesn't
    if any(kw in ctx for kw in ['port-in', 'port in', 'portin', 'change rate']):
        if 'sp ' not in existing and 'transactiontype sp' not in existing and 'port-in sp' not in existing:
            s.append({
                'title': 'Verify %s with Port-In (SP) transactionType triggers Syniverse correctly.' % fname,
                'description': 'Trigger %s with transactionType=SP (Service Provider port-in). '
                               'Verify Syniverse CreateSubscriber is called with correct parameters. '
                               'Verify wholesale plan assignment matches SP flow rules.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Manual suite has specific TC for SP transactionType. Auto suite was missing this variant.',
                'precondition': '1.\tPort-In SP scenario data prepared.\n'
                                '2.\tSyniverse endpoint accessible.',
            })

    # ── Syniverse failure handling (granular per HTTP status) ──
    # Manual suite has 401, 403, 404 each with specific assertions
    if '401' not in existing:
        s.append({
            'title': 'Negative: Verify %s handles Syniverse 401 Unauthorized response.' % fname,
            'description': 'Trigger %s where Syniverse returns HTTP 401 Unauthorized. '
                           'Verify NSL handles gracefully — logs error, does NOT corrupt subscriber state.' % fname,
            'category': 'Negative',
            'reasoning': 'Manual suite has specific 401 assertion. Token expiry is a real production issue.',
            'precondition': '1.\tSimulate Syniverse 401 response.\n'
                            '2.\tPhone line should be active.',
        })
    if '403' not in existing:
        s.append({
            'title': 'Negative: Verify %s handles Syniverse 403 Forbidden response.' % fname,
            'description': 'Trigger %s where Syniverse returns HTTP 403 Forbidden. '
                           'Verify NSL handles gracefully — logs error, returns appropriate error to caller.' % fname,
            'category': 'Negative',
            'reasoning': 'Manual suite has specific 403 assertion. Permission issues must be handled cleanly.',
            'precondition': '1.\tSimulate Syniverse 403 response.\n'
                            '2.\tPhone line should be active.',
        })
    if '404' not in existing:
        s.append({
            'title': 'Negative: Verify %s handles Syniverse 404 Not Found response.' % fname,
            'description': 'Trigger %s where Syniverse returns HTTP 404 (subscriber not found). '
                           'Verify NSL handles gracefully — logs error, does NOT create orphaned records.' % fname,
            'category': 'Negative',
            'reasoning': 'Manual suite has specific 404 assertion. Subscriber not found in Syniverse is a real scenario.',
            'precondition': '1.\tSimulate Syniverse 404 response.\n'
                            '2.\tPhone line should be active.',
        })

    # ── VZW regression scenarios ──
    if 'vzw' not in existing and 'verizon' not in existing:
        s.append({
            'title': 'Regression: Verify %s does not break existing VZW Syniverse flows.' % fname,
            'description': 'After %s changes, trigger a VZW Syniverse flow (activation/deactivation). '
                           'Verify VZW flows still work correctly — no regression.' % fname,
            'category': 'Edge Case',
            'reasoning': 'Syniverse is shared between TMO and VZW. Changes must not break VZW.',
            'precondition': '1.\tVZW test subscriber available.\n'
                            '2.\tSyniverse endpoint accessible.',
        })

    return s


def _syniverse_no_call_thinking(fname, ctx, existing):
    """Generate explicit 'Syniverse NOT called' assertions for flows that should NOT
    trigger Syniverse (Hotline, Remove Hotline, Change Feature, etc.)."""
    s = []

    fl = fname.lower()

    if 'hotline' in fl or 'remove hotline' in fl:
        s.append({
            'title': 'Verify Syniverse is NOT called during %s.' % fname,
            'description': 'Trigger %s and verify via Century Report that NO Syniverse '
                           'outbound call (CreateSubscriber/RemoveSubscriber/SwapIMSI) is made. '
                           '%s is an internal state change that does not affect Syniverse.' % (fname, fname),
            'category': 'Happy Path',
            'reasoning': 'Manual suite explicitly validates "no Syniverse call" for Hotline/Remove Hotline. '
                         'This is a critical assertion that was missing from the auto suite.',
            'precondition': '1.\tPhone line should be active.\n'
                            '2.\tCentury Report / Syniverse logs accessible for verification.',
        })

    # PL (Plan Level) transactions — no external system changes
    if 'pl ' in ctx or 'plan level' in ctx:
        if 'no changes' not in existing and 'no external' not in existing:
            s.append({
                'title': 'Verify NO external system changes for PL transaction in %s.' % fname,
                'description': 'Trigger %s with PL (Plan Level) transaction. '
                               'Verify NO changes to Syniverse, ITMBO, EMM, or any external system. '
                               'PL transactions are internal only.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Per Chalk: For PL, there must be no changes to any external systems.',
                'precondition': '1.\tPL transaction data prepared.\n'
                                '2.\tExternal system monitoring accessible.',
            })

    return s


# ================================================================
# CONTRACT-DRIVEN THINKING — the global systemic approach
# Instead of per-feature hardcoding, this function consults the
# integration contract to generate scenarios for ANY operation.
# ================================================================

def _contract_driven_thinking(fname, contract, ctx, existing):
    """Generate scenarios driven by the global integration contract.
    This replaces all per-feature hardcoded thinking with a single
    contract-aware function that works for ANY operation."""
    from .integration_contract import (get_syniverse_assertion, get_must_not_call_systems,
                                        get_must_call_systems, EXTERNAL_SYSTEMS)
    s = []

    # ── 1. "MUST CALL" assertions — verify each required external system ──
    must_call = get_must_call_systems(contract)
    for sys_obj in must_call:
        _sys_lower = sys_obj.name.lower()
        if _sys_lower not in existing:
            # For Syniverse, be specific about the action
            if _sys_lower == 'syniverse' and contract.syniverse_action not in ('NONE', 'Conditional', ''):
                s.append({
                    'title': 'Verify %s %s is triggered during %s.' % (
                        sys_obj.name, contract.syniverse_action, fname),
                    'description': 'Trigger %s and verify %s sends %s outbound call. %s' % (
                        fname, sys_obj.name, contract.syniverse_action, contract.syniverse_condition),
                    'category': 'Happy Path',
                    'reasoning': 'Contract: %s MUST call %s.%s' % (
                        contract.operation, sys_obj.name, contract.syniverse_action),
                    'precondition': '1.\tPhone line in required state.\n2.\t%s accessible.' % sys_obj.verify_via,
                })
            elif _sys_lower == 'syniverse' and contract.syniverse_action == 'Conditional':
                # For conditional Syniverse, generate per-condition scenarios
                for tx_type in contract.transaction_types:
                    _tx_lower = tx_type.lower()
                    if _tx_lower not in existing:
                        s.append({
                            'title': 'Verify Syniverse behavior for %s transaction type %s.' % (fname, tx_type),
                            'description': 'Trigger %s with transaction type %s. '
                                           'Verify Syniverse call matches contract: %s' % (
                                               fname, tx_type, contract.syniverse_condition),
                            'category': 'Happy Path',
                            'reasoning': 'Contract: %s has conditional Syniverse behavior per transaction type.' % contract.operation,
                        })
            elif _sys_lower != 'syniverse':
                # Non-Syniverse systems — generic verification
                if sys_obj.name.lower() not in existing:
                    s.append({
                        'title': 'Verify %s is notified/updated during %s.' % (sys_obj.name, fname),
                        'description': 'Trigger %s and verify %s receives correct outbound call. '
                                       'Verify via %s.' % (fname, sys_obj.name, sys_obj.verify_via),
                        'category': 'Happy Path',
                        'reasoning': 'Contract: %s MUST call %s.' % (contract.operation, sys_obj.name),
                    })

    # ── 2. "MUST NOT CALL" assertions — the critical negative assertions ──
    must_not_call = get_must_not_call_systems(contract)
    for sys_obj in must_not_call:
        _sys_lower = sys_obj.name.lower()
        _has_no_call = ('no %s' % _sys_lower in existing or
                        '%s is not' % _sys_lower in existing or
                        '%s not called' % _sys_lower in existing or
                        'not call %s' % _sys_lower in existing)
        if not _has_no_call:
            _syn_assert = get_syniverse_assertion(contract)
            _reason = _syn_assert.get('condition', '') if _sys_lower == 'syniverse' else ''
            # Use CDR-appropriate language for mediation/notification features
            _verify_method = sys_obj.verify_via
            if contract.category in ('mediation', 'notification'):
                _verify_method = 'mediation pipeline logs'
            s.append({
                'title': 'Verify %s is NOT called during %s.' % (sys_obj.name, fname),
                'description': 'Trigger %s and verify via %s that NO %s outbound call is made. %s' % (
                    fname, _verify_method, sys_obj.name, _reason),
                'category': 'Happy Path',
                'reasoning': 'Contract: %s MUST NOT call %s. This is a critical negative assertion.' % (
                    contract.operation, sys_obj.name),
                'precondition': '1.\tMediation and PRR batch jobs running.\n2.\t%s accessible for verification.' % _verify_method
                    if contract.category in ('mediation', 'notification')
                    else '1.\tPhone line in required state.\n2.\t%s accessible for verification.' % _verify_method,
            })

    # ── 3. Granular error handling for each "MUST CALL" system ──
    for sys_obj in must_call:
        if sys_obj.error_codes:
            for code in sys_obj.error_codes:
                if code not in existing:
                    s.append({
                        'title': 'Negative: Verify %s handles %s HTTP %s response.' % (fname, sys_obj.name, code),
                        'description': 'Trigger %s where %s returns HTTP %s. '
                                       'Verify NSL handles gracefully — no data corruption.' % (
                                           fname, sys_obj.name, code),
                        'category': 'Negative',
                        'reasoning': 'Contract: %s calls %s. Must handle %s error.' % (
                            contract.operation, sys_obj.name, code),
                    })

    # ── 4. Transaction type coverage (for multi-type operations like Sync Subscriber) ──
    if len(contract.transaction_types) > 1:
        for tx_type in contract.transaction_types:
            _tx_lower = tx_type.lower()
            if _tx_lower not in existing:
                s.append({
                    'title': 'Verify %s behavior for transaction type %s.' % (fname, tx_type),
                    'description': 'Trigger %s with transaction type %s. '
                                   'Verify correct external system interactions per contract.' % (fname, tx_type),
                    'category': 'Happy Path',
                    'reasoning': 'Contract: %s handles %d transaction types. Each must be tested.' % (
                        contract.operation, len(contract.transaction_types)),
                })

    # ── 5. Device/SIM sensitivity ──
    if contract.device_sensitive and 'phone' not in existing and 'tablet' not in existing:
        s.append({
            'title': 'Verify %s works correctly for both Phone and Tablet devices.' % fname,
            'description': 'Trigger %s for Phone device, then for Tablet device. '
                           'Verify both complete successfully with correct device-specific handling.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Contract: %s is device-sensitive. Phone vs Tablet may have different behavior.' % contract.operation,
        })

    if contract.sim_sensitive and 'esim' not in existing and 'psim' not in existing:
        s.append({
            'title': 'Verify %s works correctly for both eSIM and pSIM.' % fname,
            'description': 'Trigger %s for eSIM, then for pSIM. '
                           'Verify both complete successfully with correct SIM-specific handling.' % fname,
            'category': 'Happy Path',
            'reasoning': 'Contract: %s is SIM-sensitive. eSIM vs pSIM may have different flows.' % contract.operation,
        })

    return s
