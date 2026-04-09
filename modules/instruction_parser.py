"""
instruction_parser.py — Parse custom user instructions into engine adjustments.
Interprets free text like 'Focus on eSIM only, skip 4G' into actionable config.
"""
import re


def parse_instructions(text, options, log=print):
    """Parse custom instructions and return adjusted options + directives.
    Returns dict of adjustments to apply to the suite generation."""
    if not text or not text.strip():
        return {}

    t = text.lower().strip()
    adjustments = {
        'filter_devices': None,      # list of devices to keep, or None
        'filter_sim': None,           # list of SIM types to keep
        'filter_channels': None,      # list of channels to keep
        'filter_networks': None,      # list of networks to keep
        'skip_4g': False,
        'skip_psim': False,
        'skip_esim': False,
        'extra_negatives': [],        # extra negative scenario types to add
        'extra_scenarios': [],        # extra scenario descriptions to add
        'focus_features': [],         # specific feature types to focus on
        'max_per_group': None,        # limit TCs per group
        'include_boundary': False,
        'include_rollback_all': False,
        'include_data_integrity': False,
        'include_auth_failure': False,
        'prioritize_e2e': False,
        'raw_instructions': text,
    }

    log('[CUSTOM] Parsing instructions: %s' % text[:100])

    # ── Device filters ──
    if 'skip tablet' in t or 'no tablet' in t or 'only mobile' in t:
        adjustments['filter_devices'] = ['Mobile']
        log('[CUSTOM]   Filter: Mobile only (skip Tablet)')
    if 'skip mobile' in t or 'only tablet' in t:
        adjustments['filter_devices'] = ['Tablet']
        log('[CUSTOM]   Filter: Tablet only')
    if 'wearable' in t or 'smartwatch' in t or 'watch' in t:
        current = adjustments['filter_devices'] or list(options.get('devices', []))
        if 'Smartwatch' not in current:
            current.append('Smartwatch')
        adjustments['filter_devices'] = current
        log('[CUSTOM]   Added: Smartwatch')

    # ── SIM filters ──
    if 'skip psim' in t or 'no psim' in t or 'esim only' in t or 'only esim' in t:
        adjustments['filter_sim'] = ['eSIM']
        adjustments['skip_psim'] = True
        log('[CUSTOM]   Filter: eSIM only')
    if 'skip esim' in t or 'no esim' in t or 'psim only' in t or 'only psim' in t:
        adjustments['filter_sim'] = ['pSIM']
        adjustments['skip_esim'] = True
        log('[CUSTOM]   Filter: pSIM only')

    # ── Network filters ──
    if 'skip 4g' in t or 'no 4g' in t or '5g only' in t or 'only 5g' in t:
        adjustments['filter_networks'] = ['5G']
        adjustments['skip_4g'] = True
        log('[CUSTOM]   Filter: 5G only (skip 4G)')
    if 'skip 5g' in t or '4g only' in t:
        adjustments['filter_networks'] = ['4G']
        log('[CUSTOM]   Filter: 4G only')

    # ── OS/Platform filters ──
    if 'ios only' in t or 'only ios' in t or 'skip android' in t or 'no android' in t:
        adjustments['filter_os'] = ['iOS']
        log('[CUSTOM]   Filter: iOS only')
    if 'android only' in t or 'only android' in t or 'skip ios' in t or 'no ios' in t:
        adjustments['filter_os'] = ['Android']
        log('[CUSTOM]   Filter: Android only')

    # ── Channel filters ──
    if 'only nbop' in t or 'nbop only' in t or 'skip itmbo' in t:
        adjustments['filter_channels'] = ['NBOP']
        log('[CUSTOM]   Filter: NBOP only')
    if 'only itmbo' in t or 'itmbo only' in t or 'skip nbop' in t:
        adjustments['filter_channels'] = ['ITMBO']
        log('[CUSTOM]   Filter: ITMBO only')

    # ── Extra scenarios ──
    if 'timeout' in t and ('extra' in t or 'add' in t or 'include' in t):
        adjustments['extra_negatives'].append('timeout')
        log('[CUSTOM]   Extra: timeout scenarios')
    if 'rollback' in t and ('every' in t or 'all' in t or 'each' in t):
        adjustments['include_rollback_all'] = True
        log('[CUSTOM]   Extra: rollback for every operation type')
    if 'boundary' in t or 'edge' in t and 'test' in t:
        adjustments['include_boundary'] = True
        log('[CUSTOM]   Extra: boundary testing')
    if 'auth' in t and ('fail' in t or 'invalid' in t):
        adjustments['include_auth_failure'] = True
        log('[CUSTOM]   Extra: auth failure scenarios')
    if 'data integrity' in t or 'integrity check' in t:
        adjustments['include_data_integrity'] = True
        log('[CUSTOM]   Extra: data integrity checks')
    if 'prioritize e2e' in t or 'focus e2e' in t or 'e2e' in t and 'priorit' in t:
        adjustments['prioritize_e2e'] = True
        log('[CUSTOM]   Priority: E2E scenarios')

    # ── Focus on specific features ──
    feature_keywords = {
        'change bcd': 'Change BCD', 'bcd': 'Change BCD',
        'change rateplan': 'Change Rateplan', 'rateplan': 'Change Rateplan',
        'change sim': 'Change SIM', 'change feature': 'Change Feature',
        'swap': 'Swap MDN', 'activation': 'Activation',
        'deactivation': 'Deactivation', 'hotline': 'Hotline',
        'suspend': 'Suspend', 'reconnect': 'Reconnect',
    }
    if 'focus on' in t or 'only' in t:
        for kw, feat in feature_keywords.items():
            if kw in t:
                adjustments['focus_features'].append(feat)
        if adjustments['focus_features']:
            log('[CUSTOM]   Focus: %s' % ', '.join(adjustments['focus_features']))

    # ── Limit per group ──
    limit_match = re.search(r'limit\s+(?:to\s+)?(\d+)\s+(?:test\s*cases?|tcs?)\s+per', t)
    if limit_match:
        adjustments['max_per_group'] = int(limit_match.group(1))
        log('[CUSTOM]   Limit: %d TCs per group' % adjustments['max_per_group'])

    # ── Parse any extra scenario descriptions ──
    # Lines starting with "Add:" or "Include:" become extra scenarios
    for line in text.split('\n'):
        line = line.strip()
        if line.lower().startswith(('add:', 'include:', 'also:')):
            desc = line.split(':', 1)[1].strip()
            if desc and len(desc) > 10:
                adjustments['extra_scenarios'].append(desc)
                log('[CUSTOM]   Extra scenario: %s' % desc[:60])

    return adjustments


def apply_adjustments(options, adjustments):
    """Apply parsed adjustments to the options dict."""
    if not adjustments:
        return options

    opts = dict(options)

    if adjustments.get('filter_devices'):
        opts['devices'] = adjustments['filter_devices']
    if adjustments.get('filter_sim'):
        opts['sim_types'] = adjustments['filter_sim']
    if adjustments.get('filter_channels'):
        opts['channel'] = adjustments['filter_channels']
    if adjustments.get('filter_networks'):
        opts['networks'] = adjustments['filter_networks']
    if adjustments.get('filter_os'):
        opts['os_platforms'] = adjustments['filter_os']

    return opts
