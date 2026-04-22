"""
NBOP UI Knowledge Base
=======================
Loads the NBOP UI discovery map and provides intelligent lookup for:
- Menu paths (feature name → exact NBOP navigation path)
- Field names per page (what fields exist on each screen)
- Expected UI elements (buttons, tabs, dropdowns)
- Step generation with real NBOP field names and menu paths

Used by: step_templates.py, test_analyst.py, test_engine.py
Source:  TMO DashBoard/nbop_discovery/nbop_ui_map.json
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# ── Load the UI map ──
_UI_MAP = None
_UI_MAP_PATHS = [
    Path(__file__).parent.parent.parent / 'TMO DashBoard' / 'nbop_discovery' / 'nbop_ui_map.json',
    Path(__file__).parent.parent / 'nbop_ui_map.json',
]


def _load_ui_map() -> dict:
    global _UI_MAP
    if _UI_MAP is not None:
        return _UI_MAP
    for p in _UI_MAP_PATHS:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                _UI_MAP = json.load(f)
            return _UI_MAP
    _UI_MAP = {}
    return _UI_MAP


def is_available() -> bool:
    """Check if the NBOP UI knowledge base is loaded."""
    return bool(_load_ui_map())


# ════════════════════════════════════════════════════════════════════
#  LANDING TILES — the main NBOP menu
# ════════════════════════════════════════════════════════════════════

def get_landing_tiles() -> List[str]:
    """Return all NBOP landing page tiles (main menu items)."""
    return _load_ui_map().get('landing_tiles', [])


def get_edit_menu_items() -> List[str]:
    """Return all items from the edit/manage dropdown menu."""
    return [m['text'] for m in _load_ui_map().get('edit_menu', [])]


def get_context_menu_items() -> List[str]:
    """Return context menu items (Line History, Transaction History, etc.)."""
    return [m['text'] for m in _load_ui_map().get('context_menu', [])]


# ════════════════════════════════════════════════════════════════════
#  FEATURE → NBOP PAGE MAPPING
# ════════════════════════════════════════════════════════════════════

# Map feature keywords to NBOP pages/tiles/menus
_FEATURE_TO_PAGE = {
    # Landing tiles
    'validate device': 'Tile: Validate Device/SIM',
    'validate sim': 'Tile: Validate Device/SIM',
    'validate port': 'Tile: Validate Port-In Eligibility',
    'port-in eligibility': 'Tile: Validate Port-In Eligibility',
    'portin eligibility': 'Tile: Validate Port-In Eligibility',
    'port in eligibility': 'Tile: Validate Port-In Eligibility',
    'hmno inquiry': 'Tile: HMNO Inquiry',
    'network inquiry': 'Tile: Network Inquiry',
    'service plan': 'Tile: View Service Plan',
    'batch processing': 'Tile: Batch Processing',
    'history': 'Tile: History',
    'new line activation': 'Tile: New Line Activation',
    'gsma blocklist': 'Tile: GSMA Blocklist Batch',
    'sftp report': 'Tile: SFTP Reports',
    'apollo portal': 'Tile: Apollo Portal',
    # Context menu pages
    'line history': 'Context: Line History',
    'transaction history': 'Context: Transaction History',
    'notification': 'Context: Notifications',
    'voice detail': 'Context: Voice Details',
    'data detail': 'Context: Data Details',
    'sms detail': 'Context: SMS/MMS Details',
    'mms detail': 'Context: SMS/MMS Details',
    'mediation detail': 'Context: Mediation Details',
    'mediation subscriber': 'Context: Mediation Details',
    # Edit menu actions
    'change line status': 'Manage Line → Change Line Status',
    'change device': 'Manage Line → Change Device and SIM',
    'change sim': 'Manage Line → Change SIM',
    'change feature': 'Manage Line → Change Features',
    'change mdn': 'Manage Line → Change MDN',
    'reclaim mdn': 'Manage Line → Reclaim MDN',
    'swap mdn': 'Manage Line → Swap MDN',
    'change dpfo': 'Manage Line → Change DPFO Reset Day',
    'sync subscriber': 'Sync Line → Sync with Network',
    'sync line': 'Sync Line → Sync with Network',
    'sync key': 'Sync Line → Sync with Network',
    'reset line': 'Reset Line',
    'voice mail': 'Reset Line → Voice Mail',
    'network reset': 'Reset Line → Network',
    'add line': 'Add Line',
    'hotline': 'Manage Line → Change Line Status',
    'remove hotline': 'Manage Line → Change Line Status',
    'suspend': 'Manage Line → Change Line Status',
    'reconnect': 'Manage Line → Change Line Status',
    'reconnect eligibility': 'Manage Line → Change Line Status',
    # Profile buttons
    'line summary': 'Button: Line Summary(MNO)',
    'features': 'Button: Features',
    'qr code': 'Button: View QR CODE',
}


def find_nbop_page(feature_name: str, description: str = '') -> Optional[str]:
    """Given a feature name/description, find the matching NBOP page."""
    ctx = (feature_name + ' ' + description).lower()
    for keyword, page_name in _FEATURE_TO_PAGE.items():
        if keyword in ctx:
            return page_name
    return None


def get_page_data(page_name: str) -> Optional[dict]:
    """Get the full scan data for a specific NBOP page."""
    ui_map = _load_ui_map()
    return ui_map.get('pages', {}).get(page_name)


def get_navigation_path(feature_name: str, description: str = '') -> str:
    """Get the exact NBOP navigation path for a feature.
    Returns something like: 'NBOP → Mobile Service Management → Validate Port-In Eligibility'
    """
    ctx = (feature_name + ' ' + description).lower()

    # Check landing tiles first — match longer/more specific tiles first
    tiles_sorted = sorted(get_landing_tiles(), key=len, reverse=True)
    for tile in tiles_sorted:
        if tile.lower().replace('-', ' ') in ctx.replace('-', ' ') or \
           any(w in ctx for w in tile.lower().split() if len(w) > 4):
            return f'NBOP → Mobile Service Management → {tile}'

    # Check edit menu (Manage Line actions)
    edit_items = get_edit_menu_items()
    for item in edit_items:
        if item.lower() in ctx:
            # Determine parent menu
            manage_items = ['Change Line Status', 'Change Device and SIM', 'Change SIM',
                           'Change Features', 'Change MDN', 'Reclaim MDN', 'Swap MDN',
                           'Change DPFO Reset Day']
            if item in manage_items:
                return f'NBOP → Subscriber Profile → ≡ Menu → Manage Line → {item}'
            elif item in ['Sync with Network']:
                return f'NBOP → Subscriber Profile → ≡ Menu → Sync Line → {item}'
            elif item in ['Voice Mail', 'Network']:
                return f'NBOP → Subscriber Profile → ≡ Menu → Reset Line → {item}'
            else:
                return f'NBOP → Subscriber Profile → ≡ Menu → {item}'

    # Check context menu
    for item in get_context_menu_items():
        if item.lower().replace('/', ' ') in ctx.replace('/', ' '):
            return f'NBOP → Subscriber Profile → ≡ Menu → {item}'

    # Default
    return 'NBOP → Mobile Service Management'


# ════════════════════════════════════════════════════════════════════
#  FIELD KNOWLEDGE — what fields exist on each page
# ════════════════════════════════════════════════════════════════════

# Subscriber profile sections with their fields
PROFILE_SECTIONS = {
    'Account Information': [
        'Mobile Solo Account ID', 'Spectrum Core Account', 'Account Type',
        'DPFO Reset Day', 'Billing Account Name', 'Mobile Account Number',
        'Device Nickname', 'Division ID',
    ],
    'Line Information': [
        'MDN', 'Channel', 'Line ID', 'Line Status', 'Line Type', 'MIN',
        'LTE Status', 'Initial Service Date', 'Activated Network', 'MNO',
        'Last Status Change', 'Port In Flag', 'Wifi Address',
    ],
    'Device Information': [
        'IMEI1 (Device)', 'Model', 'Make', 'Mode', 'Device Type',
        'CDMA Less', 'Serial Number',
    ],
    'SIM Information': [
        'ICCID (SIM)', 'SIM Type', 'SIM Profile Type', 'First Activated Network',
        'SIM Status', 'IMSI', 'Charter IMSI', 'RCS Status',
        'Activation Date', 'Deactivation Date', 'Last Update Date', 'Activation Code',
    ],
    'Add-Ons': [
        'Global Day Pass', 'Retail Plan', 'Wholesale Plan',
        'PDL Data', 'Data Limit', 'MHS Data Limit',
    ],
}

# Card header fields (the top bar)
CARD_HEADERS = ['Account (ACC...)', 'MDN', 'IMEI1', 'ICCID']

# Profile action buttons
PROFILE_BUTTONS = ['View All', 'View', 'View QR CODE', 'Line Summary(MNO)',
                   'Service Plan', 'Features']

# History page tabs
HISTORY_TABS = ['Port In Activation', 'New MDN Activation', 'Wearable Activation',
                'HMNO Activation', 'Port Out History', 'MDN/SIM/Device History']

# Mediation Details tabs
MEDIATION_TABS = ['Subscriber Summary', 'Subscriber History']

# Mediation fields
MEDIATION_FIELDS = [
    'Biller Account Indicator', 'Mobile Solo Account ID', 'Line Status',
    'DPFO Reset Day', 'MDN', 'Line ID', 'IMEI (Device)', 'IMSI',
    'HMNO IMEI (Device)', 'HMNO IMSI', 'Plan Group', 'Wholesale Plan',
    'Start Date', 'End Date', 'Speed Reduction Flag',
]

# GSMA Blocklist tabs
GSMA_TABS = ['Manage Blocklist', 'GSMA Blocklist Inquiry', 'Charter Blocklist History']

# New Line Activation tabs
ACTIVATION_TABS = ['Subscriber Line', 'Network only Line']

# Notifications tabs
NOTIFICATION_TABS = ['DPFO Notifications']

# SFTP Report types
SFTP_REPORTS = ['MDN Swap', 'Aging Port-in', 'Subscriber Differential Report',
                'CBU Subscriber differential', 'Delayed Port-in', 'eSIM Errors']


def get_profile_fields(section: str = None) -> List[str]:
    """Get subscriber profile field names, optionally filtered by section."""
    if section:
        return PROFILE_SECTIONS.get(section, [])
    all_fields = []
    for fields in PROFILE_SECTIONS.values():
        all_fields.extend(fields)
    return all_fields


def get_page_fields(page_name: str) -> List[str]:
    """Get field labels for a specific NBOP page from the discovery data."""
    page_data = get_page_data(page_name)
    if not page_data:
        return []
    labels = page_data.get('labels', [])
    return [l.get('text', l) if isinstance(l, dict) else l for l in labels]


def get_page_tabs(page_name: str) -> List[str]:
    """Get tab names for a specific NBOP page."""
    page_data = get_page_data(page_name)
    if not page_data:
        return []
    return [t['text'] for t in page_data.get('tabs', [])]


def get_page_buttons(page_name: str) -> List[str]:
    """Get button names for a specific NBOP page."""
    page_data = get_page_data(page_name)
    if not page_data:
        return []
    return list(set(b['text'] for b in page_data.get('buttons', []) if b.get('text', '').strip()))


# ════════════════════════════════════════════════════════════════════
#  SMART STEP GENERATION — real NBOP steps with real field names
# ════════════════════════════════════════════════════════════════════

def _classify_scenario_intent(scenario_title: str) -> str:
    """Classify what the scenario is trying to test."""
    sc = scenario_title.lower()
    if any(kw in sc for kw in ['screen load', 'navigate', 'navigation', 'page load']):
        return 'navigation'
    if any(kw in sc for kw in ['visible', 'accessible', 'display', 'permission',
                                'role', 'access', 'menu is', 'shows', 'present',
                                'hidden', 'disabled', 'enabled', 'read-only']):
        return 'visibility'
    if any(kw in sc for kw in ['profile', 'subscriber profile', 'section', 'fields',
                                'displays correctly', 'data is correct', 'values match',
                                'reflects', 'populated']):
        return 'data_verify'
    if any(kw in sc for kw in ['transaction history', 'line history', 'service history',
                                'history', 'audit', 'logged', 'recorded']):
        return 'history'
    if any(kw in sc for kw in ['invalid', 'empty', 'blank', 'non-existent', 'not found',
                                'malformed', 'wrong format']):
        return 'negative_input'
    if any(kw in sc for kw in ['reject', 'fail', 'error', 'denied', 'not allowed',
                                'suspended', 'hotlined', 'deactivated', 'already']):
        return 'negative_state'
    if any(kw in sc for kw in ['session', 'timeout', 'refresh', 'browser', 'concurrent',
                                'duplicate', 'back button']):
        return 'edge_case'
    return 'action'


def generate_ui_steps(feature_name: str, description: str = '',
                      scenario_title: str = '') -> List[Tuple[str, str]]:
    """Generate NBOP UI test steps based on scenario INTENT."""
    ctx = (feature_name + ' ' + description + ' ' + scenario_title).lower()
    sc = scenario_title.lower()
    nav_path = get_navigation_path(feature_name, description)
    intent = _classify_scenario_intent(scenario_title)

    if intent == 'navigation':
        return [
            ('Launch NBOP and navigate to %s' % nav_path, 'Screen loads without errors'),
            ('Verify all expected fields, labels, and buttons are present', 'All UI elements render correctly'),
            ('Verify header cards show: Account, MDN, IMEI1, ICCID', 'Header cards display with correct data'),
        ]

    if intent == 'visibility':
        return [
            ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
            ('Navigate to the relevant screen/section as per scenario', 'Screen loads correctly'),
            ('Verify the target element is visible and accessible: %s' % scenario_title[:80],
             'Element is visible, correctly labeled, and interactive'),
        ]

    if intent == 'data_verify':
        sections_to_check = [s for s in PROFILE_SECTIONS if s.lower() in sc] or list(PROFILE_SECTIONS.keys())
        steps = [('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded with header cards')]
        for sect in sections_to_check[:4]:
            fields = PROFILE_SECTIONS[sect][:5]
            steps.append(('Verify %s section: %s' % (sect, ', '.join(fields)),
                          '%s fields populated with correct values' % sect))
        return steps

    if intent == 'history':
        ht = 'Transaction History'
        if 'line history' in sc: ht = 'Line History'
        elif 'service history' in sc: ht = 'Service History'
        return [
            ('Launch NBOP, search subscriber by MDN, load profile', 'Subscriber profile loaded'),
            ('Navigate to ≡ Menu → %s' % ht, '%s page loads with paginated table' % ht),
            ('Verify entry exists with correct timestamp, type, and status', 'Record found matching expected operation'),
            ('Verify pagination and rows per page selector (10/20/30/40/50)', 'Table pagination works correctly'),
        ]

    if intent == 'negative_input':
        if 'empty' in sc or 'blank' in sc:
            return [
                ('Launch NBOP and navigate to %s' % nav_path, 'Screen loads'),
                ('Click Submit/Search without entering required fields', 'NBOP shows validation message for required fields'),
                ('Verify no data was submitted or changed', 'No operation executed, screen remains in input state'),
            ]
        elif 'non-existent' in sc or 'not found' in sc:
            return [
                ('Launch NBOP and navigate to %s' % nav_path, 'Screen loads'),
                ('Enter non-existent MDN (e.g., 0000000000) and search', 'NBOP shows "Subscriber not found" message'),
                ('Verify no subscriber data is displayed', 'Screen shows error state, no profile sections loaded'),
            ]
        else:
            return [
                ('Launch NBOP and navigate to %s' % nav_path, 'Screen loads'),
                ('Enter invalid data as per scenario: %s' % scenario_title[:60], 'NBOP shows appropriate validation error message'),
                ('Verify no data was changed or submitted', 'Subscriber profile unchanged, no operation executed'),
            ]

    if intent == 'negative_state':
        return [
            ('Launch NBOP and search subscriber in the required state', 'Subscriber profile loaded showing the expected state'),
            ('Attempt the operation: %s' % scenario_title[:80], 'NBOP displays error/rejection message'),
            ('Verify subscriber profile remains unchanged', 'All fields show pre-operation values, no data corruption'),
        ]

    if intent == 'edge_case':
        if 'session' in sc or 'timeout' in sc:
            return [
                ('Launch NBOP, search subscriber, load profile', 'Profile loaded successfully'),
                ('Wait for session to expire (or invalidate session cookie)', 'Session expires'),
                ('Attempt to perform the operation', 'NBOP redirects to login page, no partial operation'),
                ('Re-login and verify subscriber data unchanged', 'Profile shows same data as before session expiry'),
            ]
        elif 'duplicate' in sc or 'concurrent' in sc:
            return [
                ('Launch NBOP, search subscriber, start the operation', 'Operation in progress'),
                ('Immediately trigger the same operation again', 'Second request is rejected or queued'),
                ('Verify no duplicate records or data corruption', 'Single operation recorded, data consistent'),
            ]
        else:
            return [
                ('Launch NBOP, search subscriber, navigate to operation screen', 'Screen loaded'),
                ('Press F5 (Refresh) or browser Back button', 'Page reloads or navigates back without error'),
                ('Verify no duplicate submission or data corruption', 'Subscriber profile shows consistent state'),
            ]

    # ACTION intent — performing an operation
    steps = [('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded')]
    if 'validate port' in ctx or 'portin eligibility' in ctx:
        steps.append(('Navigate to Validate Port-In Eligibility, select MNO, enter MDN, click Search', 'Port-In Eligibility result displayed'))
    elif 'validate device' in ctx or 'validate sim' in ctx:
        steps.append(('Navigate to Validate Device/SIM, select MNO, enter IMEI, click Search', 'Device/SIM validation result displayed'))
    elif 'change line status' in ctx or 'hotline' in ctx or 'suspend' in ctx or 'reconnect' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Change Line Status, select target status, confirm', 'Line Status field updates to new value'))
    elif 'swap' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Swap MDN, select target MDN, confirm', 'Swap operation submitted, both MDNs updated'))
    elif 'change mdn' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Change MDN, enter new MDN, confirm', 'MDN updated in Line Information section'))
    elif 'change sim' in ctx or 'change device' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Change Device and SIM, enter new details, confirm', 'Device/SIM Information sections updated'))
    elif 'change feature' in ctx:
        steps.append(('Click Features button, toggle the target feature, submit', 'Feature checkbox state updated'))
    elif 'sync' in ctx:
        steps.append(('Navigate to ≡ Menu → Sync Line → Sync with Network, confirm', 'Subscriber data refreshed from network'))
    elif 'reset' in ctx:
        steps.append(('Navigate to ≡ Menu → Reset Line, select reset type, confirm', 'Reset operation completed'))
    elif 'reclaim' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Reclaim MDN, confirm', 'MDN reclaimed successfully'))
    elif 'mediation' in ctx:
        steps.append(('Navigate to ≡ Menu → Mediation Details, review Subscriber Summary tab', 'Mediation fields displayed: MDN, Line ID, IMEI, IMSI, Plan Group'))
    else:
        steps.append(('Perform the operation as per scenario: %s' % scenario_title[:80], 'Operation completed successfully via NBOP'))
    steps.append(('Verify subscriber profile reflects the operation result', 'Affected fields show correct post-operation values'))
    steps.append(('Navigate to ≡ Menu → Transaction History, verify entry recorded', 'Transaction logged with correct timestamp and status'))
    return steps


def generate_ui_negative_steps(feature_name: str, description: str = '',
                                scenario_title: str = '') -> List[Tuple[str, str]]:
    """Generate scenario-specific NBOP UI negative test steps."""
    nav_path = get_navigation_path(feature_name, description)
    sc = scenario_title.lower()

    if 'invalid mdn' in sc or ('invalid' in sc and 'mdn' in sc):
        return [
            ('Launch NBOP portal and navigate to: %s' % nav_path, 'Screen loads'),
            ('Enter invalid MDN: non-numeric (abc), too short (123), too long (12345678901234)',
             'NBOP shows validation error — "Invalid MDN format" or equivalent'),
            ('Verify no backend call made — no loading spinner', 'UI validation caught error'),
            ('Verify no subscriber data displayed', 'Screen remains on search state'),
        ]
    if 'empty' in sc or 'blank' in sc:
        return [
            ('Launch NBOP portal and navigate to: %s' % nav_path, 'Screen loads'),
            ('Click Search/Submit without entering any data',
             'NBOP shows "Required field" validation message'),
            ('Verify no backend call made', 'No server call triggered'),
        ]
    if 'non-existent' in sc or 'not found' in sc:
        return [
            ('Launch NBOP portal and navigate to: %s' % nav_path, 'Screen loads'),
            ('Enter non-existent MDN (e.g., 0000000000) and search',
             'NBOP shows "Subscriber not found" message'),
            ('Verify no profile data displayed', 'No subscriber sections loaded'),
        ]

    # Default negative
    return [
        ('Launch NBOP portal and navigate to: %s' % nav_path, 'Screen loads'),
        ('Perform the negative action: %s' % scenario_title[:100],
         'NBOP displays appropriate error message — no crash or blank screen'),
        ('Verify subscriber profile fields unchanged',
         'All sections (Account, Line, Device, SIM) show pre-operation values'),
        ('Verify no new entry in Transaction History',
         'No transaction record for the rejected operation'),
    ]


# ════════════════════════════════════════════════════════════════════
#  SCENARIO GENERATION — generate test scenario IDEAS from UI knowledge
# ════════════════════════════════════════════════════════════════════

def generate_ui_scenarios(feature_name: str, description: str = '') -> List[dict]:
    """Generate NBOP UI test scenario suggestions matching the manual test suite pattern.
    Pattern from MWTGPROV-4192 manual suite:
    - Summary: short, action-focused with device combo
    - Description: one sentence describing the UI action
    - Precondition: required subscriber state
    - Steps: 4 concise steps (Launch NBOP → Perform → Confirm NSL/TMO → Validate tables)
    """
    ctx = (feature_name + ' ' + description).lower()
    nav_path = get_navigation_path(feature_name, description)
    scenarios = []

    # ── 1. Happy path — perform the operation via NBOP ──
    scenarios.append({
        'title': 'Validate %s through NBOP.' % feature_name,
        'description': 'Validate %s completes successfully through NBOP portal.' % feature_name,
        'category': 'Happy Path',
        'reasoning': 'Core happy path — does the feature work via the portal?',
        'precondition': 'Subscriber line should be Active.',
    })

    # ── 2. Verify subscriber profile loads with correct data ──
    scenarios.append({
        'title': 'Validate subscriber profile displays correctly for %s.' % feature_name,
        'description': 'Search subscriber by MDN in NBOP. Verify Account, Line, Device, SIM sections display correct data.',
        'category': 'Happy Path',
        'reasoning': 'Profile must load correctly before any operation.',
        'precondition': 'Subscriber line should be Active.',
    })

    # ── 3. Verify Transaction History after operation ──
    scenarios.append({
        'title': 'Validate Transaction History after %s through NBOP.' % feature_name,
        'description': 'After %s, navigate to Transaction History and verify entry is recorded.' % feature_name,
        'category': 'Happy Path',
        'reasoning': 'Every operation must be auditable.',
        'precondition': '%s completed successfully.' % feature_name,
    })

    # ── 4. Verify Line History after operation ──
    if any(kw in ctx for kw in ['status', 'hotline', 'suspend', 'reconnect', 'activate', 'change']):
        scenarios.append({
            'title': 'Validate Line History after %s through NBOP.' % feature_name,
            'description': 'After %s, navigate to Line History and verify status change is recorded.' % feature_name,
            'category': 'Happy Path',
            'reasoning': 'Line History tracks all status changes.',
            'precondition': '%s completed successfully.' % feature_name,
        })

    # ── 5. Verify affected profile sections update ──
    affected = []
    if any(kw in ctx for kw in ['device', 'imei']): affected.append('Device Information')
    if any(kw in ctx for kw in ['sim', 'iccid', 'esim']): affected.append('SIM Information')
    if any(kw in ctx for kw in ['line', 'status', 'mdn', 'hotline', 'suspend']): affected.append('Line Information')
    if any(kw in ctx for kw in ['account', 'dpfo']): affected.append('Account Information')
    if any(kw in ctx for kw in ['feature', 'plan']): affected.append('Add-Ons')
    for section in affected:
        fields = PROFILE_SECTIONS.get(section, [])
        scenarios.append({
            'title': 'Validate %s section updates after %s.' % (section, feature_name),
            'description': 'After %s, verify %s fields: %s.' % (feature_name, section, ', '.join(fields[:5])),
            'category': 'Happy Path',
            'reasoning': '%s must reflect the operation result.' % section,
            'precondition': '%s completed successfully.' % feature_name,
        })

    # ── 6. Negative — invalid MDN ──
    scenarios.append({
        'title': 'Negative: Validate %s rejects invalid MDN in NBOP.' % feature_name,
        'description': 'Enter invalid MDN on %s screen. Verify error message displayed.' % feature_name,
        'category': 'Negative',
        'reasoning': 'UI must validate input and show clear error.',
        'precondition': 'NBOP portal accessible.',
    })

    # ── 7. Negative — empty submission ──
    scenarios.append({
        'title': 'Negative: Validate %s rejects empty submission in NBOP.' % feature_name,
        'description': 'Submit %s screen without entering required fields. Verify validation message.' % feature_name,
        'category': 'Negative',
        'reasoning': 'Empty submissions must be caught by UI validation.',
        'precondition': 'NBOP portal accessible.',
    })

    # ── 8. Negative — non-existent subscriber ──
    scenarios.append({
        'title': 'Negative: Validate %s handles non-existent MDN in NBOP.' % feature_name,
        'description': 'Search non-existent MDN on %s screen. Verify "not found" message.' % feature_name,
        'category': 'Negative',
        'reasoning': 'Non-existent subscribers must show clear error.',
        'precondition': 'NBOP portal accessible.',
    })

    # ── 9. Edge case — session timeout ──
    scenarios.append({
        'title': 'Edge Case: Validate %s handles session timeout in NBOP.' % feature_name,
        'description': 'Let NBOP session expire during %s. Verify redirect to login, no partial state.' % feature_name,
        'category': 'Edge Case',
        'reasoning': 'Session timeouts must not cause partial operations.',
        'precondition': 'Subscriber profile loaded in NBOP.',
    })

    # ── 10. Edge case — browser refresh ──
    scenarios.append({
        'title': 'Edge Case: Validate %s handles browser refresh in NBOP.' % feature_name,
        'description': 'Press F5 during %s. Verify no duplicate submission or data corruption.' % feature_name,
        'category': 'Edge Case',
        'reasoning': 'Browser refresh must not cause duplicate operations.',
        'precondition': 'Subscriber profile loaded in NBOP.',
    })

    # ── 11. Feature-specific scenarios ──
    if 'mediation' in ctx:
        for tab in MEDIATION_TABS:
            scenarios.append({
                'title': 'Validate Mediation Details %s tab displays correctly.' % tab,
                'description': 'Navigate to Mediation Details, click %s tab. Verify fields load correctly.' % tab,
                'category': 'Happy Path',
                'reasoning': 'Each Mediation tab must display correct data.',
                'precondition': 'Subscriber line should be Active.',
            })

    if 'notification' in ctx or 'dpfo' in ctx:
        scenarios.append({
            'title': 'Validate DPFO Notifications tab displays records.',
            'description': 'Navigate to Notifications, verify DPFO Notifications tab shows records.',
            'category': 'Happy Path',
            'reasoning': 'DPFO notifications must be visible.',
            'precondition': 'Subscriber line should be Active.',
        })

    if 'voice' in ctx or 'data detail' in ctx or 'sms' in ctx:
        scenarios.append({
            'title': 'Validate %s date range filter works correctly.' % feature_name,
            'description': 'Set Start Date and End Date. Verify results filter to selected range.',
            'category': 'Happy Path',
            'reasoning': 'Date filters must work correctly.',
            'precondition': 'Subscriber line should be Active.',
        })

    if 'feature' in ctx:
        scenarios.append({
            'title': 'Validate Features page shows all toggles with correct state.',
            'description': 'Click Features button. Verify all checkboxes/toggles display correctly.',
            'category': 'Happy Path',
            'reasoning': 'Feature toggles must reflect current state.',
            'precondition': 'Subscriber line should be Active.',
        })

    if 'history' in ctx and 'line' not in ctx and 'transaction' not in ctx:
        for tab in HISTORY_TABS:
            scenarios.append({
                'title': 'Validate History %s tab displays data.' % tab,
                'description': 'Navigate to History, click %s tab. Verify table loads.' % tab,
                'category': 'Happy Path',
                'reasoning': 'Each History tab must display data.',
                'precondition': 'NBOP portal accessible.',
            })

    return scenarios
