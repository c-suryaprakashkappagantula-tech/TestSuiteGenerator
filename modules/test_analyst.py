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
        # Auto-assign test_category if not explicitly set
        if 'test_category' not in s:
            _cat = s.get('category', '').lower()
            _title = s.get('title', '').lower()
            if 'happy path' in _cat:
                s['test_category'] = 'Cat1-HappyPath'
            elif 'negative' in _cat and any(kw in _title for kw in ['auth', 'header', 'token', '401', '403']):
                s['test_category'] = 'Cat3-Auth'
            elif 'negative' in _cat and any(kw in _title for kw in ['invalid', 'missing', 'empty', 'null', 'reject']):
                s['test_category'] = 'Cat2-InputValidation'
            elif 'negative' in _cat:
                s['test_category'] = 'Cat2-InputValidation'
            elif 'edge' in _cat or 'boundary' in _cat:
                s['test_category'] = 'Cat4-EdgeCase'
            elif 'regression' in _cat:
                s['test_category'] = 'Cat1-HappyPath'
            elif any(kw in _title for kw in ['adaptor', 'downstream', 'syniverse', 'backend']):
                s['test_category'] = 'Cat7-Adaptor'
            elif any(kw in _title for kw in ['config', 'feature flag', 'cr-', 'rule']):
                s['test_category'] = 'Cat5-ConfigDriven'
            else:
                s['test_category'] = 'Cat1-HappyPath'

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
                'test_category': 'Cat1-HappyPath',
                'reasoning': 'If users cannot reach the screen via the menu, the feature is unusable.',
            })
        if 'valid mdn' not in existing and 'search' not in existing and 'lookup' not in existing:
            s.append({
                'title': 'Verify %s screen returns correct results for valid MDN.' % fname,
                'description': 'Enter a valid MDN on %s screen and submit. '
                               'Verify correct data is returned and displayed in the portal.' % fname,
                'category': 'Happy Path',
                'test_category': 'Cat1-HappyPath',
                'reasoning': 'The primary user action — search/lookup must return correct data.',
            })
        if 'invalid' not in existing or 'error' not in existing:
            s.append({
                'title': 'Negative: Verify %s screen shows error for invalid MDN.' % fname,
                'description': 'Enter invalid MDN (non-numeric, too short, too long) on %s screen. '
                               'Verify appropriate error message displayed — no crash or blank screen.' % fname,
                'category': 'Negative',
                'test_category': 'Cat2-InputValidation',
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
            'test_category': 'Cat1-HappyPath',
            'reasoning': 'Basic sanity — does the feature work on the primary channel?',
        })

    # Q2: SUPPRESSED — Charter never tests duplicate request/idempotency
    # if 'duplicate' not in existing and 'twice' not in existing and 'idempoten' not in existing:
    #     s.append({...})

    # Q3: SUPPRESSED — Charter never does DB consistency checks in manual testing
    # if 'data integrity' not in existing and 'db state' not in existing:
    #     s.append({...})

    return s


# ================================================================
# API CRUD THINKING — specific to line-level API operations
# ================================================================

def _api_crud_thinking(fname, ctx, existing):
    s = []

    # SUPPRESSED — Charter never validates raw API response payload structure
    # if 'response' not in existing or 'payload' not in existing:
    #     s.append({...})

    # "What if TMO takes too long to respond?"
    if 'timeout' not in existing and 'tmo' not in existing:
        s.append({
            'title': 'Negative: Verify %s handles TMO/Apollo-NE timeout gracefully.' % fname,
            'description': 'Simulate TMO not responding within SLA timeout during %s. '
                           'Verify NSL retries per config, then returns appropriate error. No data corruption.' % fname,
            'category': 'Negative',
            'test_category': 'Cat2-InputValidation',
            'reasoning': 'TMO timeouts are the #1 production issue. Every external call can timeout.',
        })

    # "What if it fails halfway through?"
    if 'partial' not in existing and 'mid-operation' not in existing and 'rollback' not in existing:
        s.append({
            'title': 'Negative: Verify %s rolls back cleanly on mid-operation failure.' % fname,
            'description': 'Simulate failure after NSL sends request to TMO but before receiving response during %s. '
                           'Verify rollback restores original state. No partial updates in DB.' % fname,
            'category': 'Negative',
            'test_category': 'Cat4-EdgeCase',
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

        # ── Remove Hotline (reverse operation) ──
        if 'remove hotline api' not in existing and 'remove hotline succeeds' not in existing:
            s.append({
                'title': 'Verify Remove Hotline succeeds for Hotlined MDN.',
                'description': 'Trigger Remove Hotline API for an MDN currently in Hotlined status. '
                               'Verify Hotline SLO is removed, line returns to Active status, '
                               'TMO is notified, and Syniverse is NOT called.',
                'category': 'Happy Path',
                'reasoning': 'Remove Hotline is the reverse operation — must be tested explicitly.',
                'precondition': '1.\tMDN must be in Hotlined status.\n2.\tTMO network accessible.',
                'steps': [
                    ('Trigger Remove Hotline API with Hotlined MDN and Line ID',
                     'API accepts request successfully'),
                    ('Verify system sends Remove Hotline request to TMO',
                     'TMO acknowledges removal'),
                    ('Verify Hotline SLO is removed from subscriber profile',
                     'SLO no longer present in Line table'),
                    ('Verify line status returns to Active',
                     'Line status = Active in subscriber profile'),
                    ('Verify Syniverse is NOT called',
                     'No Syniverse outbound call in Century Report'),
                ],
            })

        # ── Negative: Hotline on Deactivated MDN ──
        if 'deactivat' not in existing:
            s.append({
                'title': 'Negative: Verify %s rejected when MDN is Deactivated.' % fname,
                'description': 'Trigger %s for an MDN in Deactivated status. '
                               'System must reject — cannot hotline a deactivated line.' % fname,
                'category': 'Negative',
                'reasoning': 'Deactivated lines cannot have Hotline applied. System must validate line state.',
                'precondition': '1.\tMDN must be in Deactivated status.\n2.\tPrepare deactivated test MDN.',
                'steps': [
                    ('Identify a Deactivated MDN from test data pool',
                     'Deactivated MDN available'),
                    ('Trigger %s API with the Deactivated MDN' % fname,
                     'API rejects request'),
                    ('Verify HTTP 400/422 with error: line not in valid state for Hotline',
                     'Error response received with appropriate code'),
                    ('Verify no changes to subscriber profile',
                     'Line status remains Deactivated, no SLO added'),
                ],
            })

        # ── Negative: Hotline on Suspended MDN ──
        if 'suspend' not in existing or 'suspended' not in existing:
            s.append({
                'title': 'Negative: Verify %s rejected when MDN is Suspended.' % fname,
                'description': 'Trigger %s for an MDN in Suspended status. '
                               'System must reject — cannot hotline a suspended line.' % fname,
                'category': 'Negative',
                'reasoning': 'Suspended lines cannot have Hotline applied. Suspend takes priority over Hotline.',
                'precondition': '1.\tMDN must be in Suspended status.\n2.\tPrepare suspended test MDN.',
                'steps': [
                    ('Identify a Suspended MDN from test data pool',
                     'Suspended MDN available'),
                    ('Trigger %s API with the Suspended MDN' % fname,
                     'API rejects request'),
                    ('Verify error response: line not in valid state for Hotline',
                     'Error response with appropriate code'),
                    ('Verify no changes to subscriber profile',
                     'Line status remains Suspended'),
                ],
            })

        # ── Tablet device variant ──
        if 'tablet' not in existing:
            s.append({
                'title': 'Verify %s succeeds for Tablet device line.' % fname,
                'description': 'Trigger %s for a Tablet line (not Phone). '
                               'Verify operation completes with same flow as Phone.' % fname,
                'category': 'Happy Path',
                'reasoning': 'Feature title mentions Phone/Tablet/Smartwatch — Tablet must be tested explicitly.',
                'precondition': '1.\tActive Tablet line available.\n2.\tTablet MDN in Active status.',
                'steps': [
                    ('Trigger %s API with Tablet MDN and Line ID' % fname,
                     'API accepts request'),
                    ('Verify Hotline SLO added to Tablet subscriber profile',
                     'SLO present in Line table for Tablet line'),
                    ('Verify TMO notified of Tablet line Hotline',
                     'TMO acknowledges Hotline for Tablet'),
                    ('Verify Syniverse NOT called',
                     'No Syniverse outbound call'),
                ],
            })

        # ── Paired Smartwatch cascade ──
        if 'smartwatch' not in existing and 'paired' not in existing and 'wearable' not in existing:
            s.append({
                'title': 'Verify paired Smartwatch is also Hotlined when host MDN is Hotlined.',
                'description': 'Trigger %s on a host/primary MDN that has a paired Smartwatch line. '
                               'Verify the paired Smartwatch line is ALSO placed in Hotlined status '
                               'as a cascading effect.' % fname,
                'category': 'Happy Path',
                'reasoning': 'When host MDN is hotlined, paired wearable must also be hotlined. '
                             'This is a cascading state change.',
                'precondition': '1.\tHost MDN with paired Smartwatch line.\n2.\tBoth lines in Active status.',
                'steps': [
                    ('Identify host MDN with active paired Smartwatch',
                     'Host + Smartwatch pair confirmed'),
                    ('Trigger %s API on the host/primary MDN' % fname,
                     'API accepts request for host MDN'),
                    ('Verify host MDN is now in Hotlined status',
                     'Host line status = Hotlined'),
                    ('Verify paired Smartwatch line is ALSO in Hotlined status',
                     'Smartwatch line status = Hotlined (cascaded)'),
                    ('Verify both lines have Hotline SLO added',
                     'SLO present on both host and smartwatch profiles'),
                ],
            })

        # ── Cross-operation: Deactivate a Hotlined MDN ──
        if 'deactivat' not in existing or 'hotlined' not in existing:
            s.append({
                'title': 'Verify Deactivation succeeds on Hotlined MDN and removes Hotline SLO.',
                'description': 'Trigger Deactivation API on an MDN currently in Hotlined status. '
                               'Verify deactivation completes and Hotline SLO is removed as side-effect.',
                'category': 'Edge Case',
                'reasoning': 'Deactivation should override Hotline — the SLO must be cleaned up.',
                'precondition': '1.\tMDN must be in Hotlined status.\n2.\tDeactivation API accessible.',
                'steps': [
                    ('Identify MDN in Hotlined status',
                     'Hotlined MDN available'),
                    ('Trigger Deactivation API on the Hotlined MDN',
                     'Deactivation accepted'),
                    ('Verify line status changes to Deactivated',
                     'Line status = Deactivated'),
                    ('Verify Hotline SLO is removed from profile',
                     'No Hotline SLO in subscriber profile'),
                ],
            })

        if 'suspend after' not in existing and 'suspend' not in existing:
            s.append({
                'title': 'Verify Suspend succeeds on a Hotlined MDN after %s.' % fname,
                'description': 'After %s, trigger Suspend on the same MDN. '
                               'Verify Suspend overrides Hotline status correctly.' % fname,
                'category': 'Edge Case',
                'reasoning': 'Hotline → Suspend is a real lifecycle transition.',
            })

        # Syniverse NOT called assertion
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

        # ITMBO and EMM notification
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
    """D1 — State-Transition Matrix Generator.

    Generates one TC per applicable line state × the operation.
    Covers the full 7-state matrix: Active, Suspended, Hotlined,
    Pending Port-out, Pending Port-in, Cancelled, Pre-active.

    For each state, determines expected behaviour (Allow/Reject) based on
    the operation contract, and pulls documented error codes from the NMNO DB.
    """
    s = []

    # ── Full 7-state matrix ──
    # (state_label, state_key, typical_behaviour, fallback_error_code)
    FULL_STATE_MATRIX = [
        ('Active',              'active',         'allow',  ''),
        ('Suspended',           'suspended',      'reject', 'ERR_INVALID_STATE'),
        ('Hotlined',            'hotlined',       'reject', 'ERR_INVALID_STATE'),
        ('Pending Port-Out',    'pending port',   'reject', 'ERR_PORT_PENDING'),
        ('Pending Port-In',     'pending port-in','reject', 'ERR_PORT_PENDING'),
        ('Cancelled',           'cancelled',      'reject', 'ERR_INVALID_STATE'),
        ('Pre-active',          'pre-active',     'reject', 'ERR_INVALID_STATE'),
    ]

    # Try to get the operation contract to determine which states allow/reject
    # and to pull real error codes from NMNO DB
    from .integration_contract import resolve_operation
    contract = resolve_operation(fname, description=ctx, ac_text=ctx)
    req_state = (contract.required_line_state if contract else 'active').lower()

    # Try to get error codes from NMNO DB for this operation
    _nmno_state_codes = {}
    try:
        from .nmno_api_lookup import lookup_api_specs, extract_api_operation_name
        import re as _re_nmno
        _chalk_urls = _re_nmno.findall(r'https?://[^\s]+chalk[^\s]*', ctx)
        _api_name = extract_api_operation_name(fname, _chalk_urls)
        if _api_name:
            _nmno = lookup_api_specs(_api_name, log=lambda m: None)
            if _nmno and _nmno.business_rules:
                for _br in _nmno.business_rules:
                    # Match rules that mention line states
                    _br_text = ('%s %s %s' % (
                        _br.condition or '', _br.rule_description or '', _br.error_details or ''
                    )).lower()
                    for _state_label, _state_key, _, _ in FULL_STATE_MATRIX:
                        if _state_key in _br_text or _state_label.lower() in _br_text:
                            if _br.error_code and _br.error_code not in ('', 'N/A'):
                                _nmno_state_codes[_state_key] = _br.error_code
    except Exception:
        pass  # NMNO lookup is best-effort

    # Generate one TC per state
    for state_label, state_key, default_behaviour, fallback_code in FULL_STATE_MATRIX:
        # Determine expected behaviour for this state × operation
        if req_state == 'any':
            behaviour = 'allow'
        elif req_state in state_key or state_key in req_state:
            behaviour = 'allow'
        elif state_key == 'active' and req_state not in ('suspended', 'hotlined', 'cancelled'):
            behaviour = 'allow'
        else:
            behaviour = 'reject'

        # Get real error code if available
        error_code = _nmno_state_codes.get(state_key, fallback_code)

        # Skip if already covered in existing scenarios
        _check_kws = [state_key, state_label.lower()]
        if any(kw in existing for kw in _check_kws):
            continue

        if behaviour == 'allow':
            # Happy path for the required state
            if state_key == req_state or req_state == 'any':
                s.append({
                    'title': 'Verify %s succeeds for line in %s state.' % (fname, state_label),
                    'description': (
                        'Trigger %s for a TMO subscriber line in %s state. '
                        'Verify the operation completes successfully and all downstream systems are updated.' % (fname, state_label)
                    ),
                    'category': 'Happy Path',
                    'reasoning': 'State-transition matrix: %s state should allow %s.' % (state_label, fname),
                    'test_category': 'Cat1-HappyPath',
                    'precondition': 'TMO subscriber line in %s state in SIT environment.' % state_label,
                })
        else:
            # Negative: this state should reject the operation
            error_suffix = (' with error %s' % error_code) if error_code else ''
            s.append({
                'title': 'Negative: Verify %s rejected for line in %s state%s.' % (
                    fname, state_label, error_suffix),
                'description': (
                    'Trigger %s for a TMO subscriber line in %s state. '
                    'Verify the operation is rejected%s. No partial state changes.' % (
                        fname, state_label,
                        ' with error code %s' % error_code if error_code else ' with appropriate error'
                    )
                ),
                'category': 'Negative',
                'reasoning': 'State-transition matrix: %s state must block %s.' % (state_label, fname),
                'test_category': 'Cat2-InputValidation',
                'precondition': 'TMO subscriber line in %s state in SIT environment.' % state_label,
                'expected_error': error_code or 'ERR_INVALID_STATE',
            })

    # Add wearable state check (always relevant for provisioning operations)
    if 'wearable' not in existing and 'smartwatch' not in existing:
        s.append({
            'title': 'Verify %s behavior for Wearable/Smartwatch line.' % fname,
            'description': (
                'Trigger %s for a wearable line (not primary phone). '
                'Verify operation handles wearable-specific constraints correctly.' % fname
            ),
            'category': 'Edge Case',
            'reasoning': 'Wearable lines have different provisioning rules. Must be explicitly validated.',
            'test_category': 'Cat4-EdgeCase',
            'precondition': 'Active TMO Wearable/Smartwatch subscriber line in SIT environment.',
        })

    return s


# ================================================================
# PUBLIC API: State-Transition Matrix (callable from dimension_extractor)
# ================================================================

# ================================================================
# PUBLIC API: State-Transition Matrix (callable from dimension_extractor)
# ================================================================


def generate_partial_failure_matrix(
    feature_name: str,
    feature_id: str,
    contract=None,
    log=print,
) -> list:
    """D2 — Downstream Partial-Failure Generator.

    For each system in the contract must_call chain, generates one TC that
    fails at exactly that system and asserts the compensation/consistency outcome.

    Pattern: "NSL DB OK → EMM fails" — what should the system do?
    The test asserts:
      1. Prior systems in the chain completed (observable via Century Report)
      2. The failing system returned an error
      3. No partial state leaked (NSL DB should be consistent with what rolled back)
      4. Transaction History records the failure reason

    Args:
        feature_name: Short feature name (e.g., "Reset Plan")
        feature_id: Jira feature ID (e.g., "MWTGPROV-4020")
        contract: Optional OperationContract
        log: Logger function

    Returns:
        List of ExtractedScenario objects (one per failure point in the chain).
    """
    if not contract or not contract.must_call:
        return []

    try:
        from .data_models_v8 import ExtractedScenario
        from .traceability import create_traceability
        from .integration_contract import EXTERNAL_SYSTEMS
    except ImportError:
        return []

    fname = feature_name
    must_call_chain = contract.must_call  # ordered list of system keys

    # System display names and verification methods
    _sys_meta = {
        'apollo_ne':         ('Apollo NE',  'NE Portal / Century Report'),
        'tmo':               ('TMO',         'TMO Genesis Portal / Century Report'),
        'itmbo':             ('ITMBO',       'Century Report'),
        'emm':               ('EMM',         'Century Report'),
        'kafka':             ('KAFKA/BI',    'KAFKA topic / BI dashboard'),
        'syniverse':         ('Syniverse',   'Century Report'),
        'connection_manager':('Connection Manager', 'Century Report'),
        'mediation':         ('Mediation',   'SFTP / PRR output'),
    }

    scenarios = []
    source_id = 'Partial-Failure-Matrix-%s' % feature_id

    for failure_idx, failing_system in enumerate(must_call_chain):
        # Systems that succeed before the failing one
        systems_before = must_call_chain[:failure_idx]
        # The system that fails
        fail_name, fail_verify = _sys_meta.get(failing_system, (failing_system.upper(), 'Century Report'))

        if not systems_before:
            # First system fails immediately
            title = 'Negative: Verify %s handles %s failure at first step' % (fname, fail_name)
            before_desc = 'No prior systems in chain'
            compensation = (
                'Operation aborted cleanly. NSL DB not updated. '
                'Transaction History records %s failure with error code.' % fail_name
            )
        else:
            before_names = ', '.join(
                _sys_meta.get(s, (s.upper(), ''))[0] for s in systems_before
            )
            title = 'Negative: Verify %s partial failure — %s succeeds, %s fails' % (
                fname, before_names, fail_name)
            before_desc = '%s call(s) succeeded' % before_names
            compensation = (
                '%s call(s) completed. %s returned error. '
                'NSL compensates: rolls back or marks transaction FAILED. '
                'No partial state leaks to downstream consumers. '
                'Transaction History records exact failure point.' % (before_names, fail_name)
            )

        steps_hint = []
        if systems_before:
            steps_hint.append(
                'Set up environment so %s will succeed but %s will fail (mock/simulate failure)' % (
                    ', '.join(_sys_meta.get(s, (s.upper(), ''))[0] for s in systems_before), fail_name)
            )
        else:
            steps_hint.append('Set up environment so %s will fail immediately' % fail_name)

        steps_hint += [
            'Trigger %s operation via API with valid Active subscriber' % fname,
            'Verify %s returned error / did not complete' % fail_name,
            'Verify no partial state leaked — check NSL DB consistency',
            'Verify Transaction History records failure at %s with correct reason code' % fail_name,
            'Verify prior systems are not left in inconsistent state (%s)' % (before_desc or 'N/A'),
        ]

        try:
            tr = create_traceability(
                source_type='Business Rule',
                source_id=source_id,
                extracted_text='Partial-failure chain: %s fails at %s' % (fname, fail_name),
                confidence=0.85,
            )
        except Exception:
            continue

        scenarios.append(ExtractedScenario(
            title=title,
            validation=compensation,
            category='Negative',
            source=tr,
            steps_hint=steps_hint,
        ))

    # Add the cross-system consistency assertion (A7 from roadmap)
    if len(must_call_chain) >= 2:
        all_systems = ', '.join(
            _sys_meta.get(s, (s.upper(), ''))[0] for s in must_call_chain
        )
        try:
            tr_consist = create_traceability(
                source_type='Business Rule',
                source_id=source_id,
                extracted_text='Cross-system consistency: all systems consistent after %s' % fname,
                confidence=0.85,
            )
            scenarios.append(ExtractedScenario(
                title='Verify cross-system consistency after successful %s (NSL DB == %s)' % (
                    fname, ' == '.join(
                        _sys_meta.get(s, (s.upper(), ''))[0] for s in must_call_chain[:3]
                    )
                ),
                validation=(
                    'All downstream systems mutually consistent: NSL DB, %s — '
                    'same data, same state, no orphaned records.' % all_systems
                ),
                category='Happy Path',
                source=tr_consist,
                steps_hint=[
                    'Execute %s with valid Active subscriber' % fname,
                    'Verify NSL DB shows updated state',
                    'Verify %s all reflect the same updated state' % all_systems,
                    'Verify Century Report shows all outbound calls with success status',
                    'Verify Transaction History records COMPLETED with all system acknowledgements',
                ],
            ))
        except Exception:
            pass

    log('[D2-PARTIAL-FAIL] Generated %d partial-failure TCs for %s' % (len(scenarios), fname))
    return scenarios


def generate_state_transition_matrix(
    feature_name: str,
    feature_id: str,
    contract=None,
    nmno_result=None,
    log=print,
) -> list:
    """Generate a complete line-state × operation test matrix.

    Returns a list of ExtractedScenario objects (one per state) ready for
    direct injection into the DimensionSet.scenarios list.

    Covers: Active, Suspended, Hotlined, Pending Port-Out, Pending Port-In,
            Cancelled, Pre-active, Wearable (7 states + 1 device type).

    Args:
        feature_name: Short feature name (e.g., "Reset Plan")
        feature_id: Jira feature ID (e.g., "MWTGPROV-4020")
        contract: Optional OperationContract — determines allow/reject per state
        nmno_result: Optional NMNOLookupResult — for real error codes
        log: Logger function

    Returns:
        List of ExtractedScenario objects with steps_hint and validation set.
    """
    try:
        from .data_models_v8 import ExtractedScenario
        from .traceability import create_traceability
    except ImportError:
        return []

    fname = feature_name
    req_state = (contract.required_line_state if contract else 'active').lower()

    # Pull error codes from NMNO result if available
    _nmno_state_codes = {}
    if nmno_result and nmno_result.business_rules:
        for _br in nmno_result.business_rules:
            _br_text = ('%s %s %s' % (
                _br.condition or '', _br.rule_description or '', _br.error_details or ''
            )).lower()
            for _state_key in ('suspended', 'hotlined', 'pending port', 'cancelled', 'pre-active'):
                if _state_key in _br_text:
                    if _br.error_code and _br.error_code not in ('', 'N/A'):
                        _nmno_state_codes[_state_key] = _br.error_code

    FULL_STATE_MATRIX = [
        ('Active',           'active',         'allow',  ''),
        ('Suspended',        'suspended',       'reject', 'ERR_INVALID_STATE'),
        ('Hotlined',         'hotlined',        'reject', 'ERR_INVALID_STATE'),
        ('Pending Port-Out', 'pending port',    'reject', 'ERR_PORT_PENDING'),
        ('Pending Port-In',  'pending port-in', 'reject', 'ERR_PORT_PENDING'),
        ('Cancelled',        'cancelled',       'reject', 'ERR_INVALID_STATE'),
        ('Pre-active',       'pre-active',      'reject', 'ERR_INVALID_STATE'),
    ]

    scenarios = []
    source_id = 'State-Transition-Matrix-%s' % feature_id

    for state_label, state_key, default_behaviour, fallback_code in FULL_STATE_MATRIX:
        # Determine allow/reject from contract
        if req_state == 'any':
            behaviour = 'allow'
        elif req_state in state_key or state_key in req_state:
            behaviour = 'allow'
        elif state_key == 'active' and req_state not in ('suspended', 'hotlined', 'cancelled'):
            behaviour = 'allow'
        else:
            behaviour = 'reject'

        error_code = _nmno_state_codes.get(state_key, fallback_code)

        try:
            tr = create_traceability(
                source_type='Business Rule',
                source_id=source_id,
                extracted_text='State-transition matrix: %s state × %s' % (state_label, fname),
                confidence=0.9,
            )
        except Exception:
            continue

        if behaviour == 'allow':
            title = 'Verify %s succeeds for line in %s state' % (fname, state_label)
            validation = ('Operation completes successfully. '
                          'Line remains in %s state. All downstream systems updated.' % state_label)
            category = 'Happy Path'
            steps_hint = [
                'Set up subscriber line in %s state in SIT environment' % state_label,
                'Trigger %s operation via API' % fname,
                'Verify operation completes with HTTP 200/202 and SUCC00',
                'Verify downstream systems updated (NSL DB, Century Report, NBOP MIG tables)',
            ]
        else:
            error_suffix = ' with error %s' % error_code if error_code else ''
            title = 'Negative: Verify %s rejected for %s line%s' % (fname, state_label, error_suffix)
            validation = ('Operation rejected%s. Line state unchanged. No partial updates.' % (
                ' with error code %s' % error_code if error_code else ' with appropriate error'))
            category = 'Negative'
            steps_hint = [
                'Set up subscriber line in %s state in SIT environment' % state_label,
                'Trigger %s operation via API' % fname,
                'Verify operation rejected%s' % (
                    ' — response contains error code %s' % error_code if error_code else ' with error'),
                'Verify line remains in %s state — no state change occurred' % state_label,
            ]

        scenarios.append(ExtractedScenario(
            title=title,
            validation=validation,
            category=category,
            source=tr,
            steps_hint=steps_hint,
        ))

    # Wearable/Smartwatch edge case
    try:
        tr_wear = create_traceability(
            source_type='Business Rule',
            source_id=source_id,
            extracted_text='Wearable/Smartwatch line state: %s' % fname,
            confidence=0.85,
        )
        scenarios.append(ExtractedScenario(
            title='Verify %s behavior for Wearable/Smartwatch line' % fname,
            validation='Operation handles wearable-specific constraints. Correct error or success per wearable rules.',
            category='Edge Case',
            source=tr_wear,
            steps_hint=[
                'Set up Wearable/Smartwatch subscriber line in SIT environment',
                'Trigger %s operation via API' % fname,
                'Verify operation handles wearable rules (reject if not supported, succeed with correct parameters)',
                'Verify no Phone-line logic applied to wearable',
            ],
        ))
    except Exception:
        pass

    log('[D1-STATE-MATRIX] Generated %d state-transition TCs for %s' % (len(scenarios), fname))
    return scenarios


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

    # RemoveSubscriber for Deactivation flows — REMOVED
    # Deactivation does NOT call Syniverse. No RemoveSubscriber triggered.
    # (Previously incorrectly generated Syniverse steps for deactivation flows)

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
