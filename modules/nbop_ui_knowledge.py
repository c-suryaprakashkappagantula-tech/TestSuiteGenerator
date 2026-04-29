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
    # ── PI-52/53 features — NBOP UI paths for the 12 ui_portal features ──
    'change bcd': 'Manage Line → Change DPFO Reset Day',
    'bill cycle': 'Manage Line → Change DPFO Reset Day',
    'dpfo reset': 'Manage Line → Change DPFO Reset Day',
    'reset day': 'Manage Line → Change DPFO Reset Day',
    'reset feature': 'Manage Line → Change Features',
    'retrieve device': 'Context: Line Information',
    'device details': 'Context: Line Information',
    'get transaction status': 'Context: Transaction History',
    'transaction status': 'Context: Transaction History',
    'retrigger transaction': 'Context: Transaction History',
    'retrigger': 'Context: Transaction History',
    'usage inquiry': 'Context: Mediation Details',
    'inquiry usage': 'Context: Mediation Details',
    'usage details': 'Context: Mediation Details',
    'usage detail': 'Context: Mediation Details',
    'wearable': 'Manage Line → Change Device and SIM',
    'add wearable': 'Manage Line → Change Device and SIM',
    'device id object': 'Context: Line Information',
    'port-in': 'Tile: Validate Port-In Eligibility',
    'port in': 'Tile: Validate Port-In Eligibility',
    'new port': 'Tile: Validate Port-In Eligibility',
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
        # Feature-specific visibility checks
        if any(kw in ctx for kw in ['change bcd', 'change dpfo', 'dpfo reset', 'bill cycle', 'reset day']):
            return [
                ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
                ('Navigate to ≡ Menu → Manage Line', 'Manage Line submenu expands'),
                ('Verify "Change DPFO Reset Day" menu item is visible and clickable',
                 '"Change DPFO Reset Day" menu item is present, enabled, and clickable'),
                ('Click "Change DPFO Reset Day"', 'Change DPFO Reset Day screen loads'),
                ('Verify screen shows Current DPFO Reset Day value (read-only) and dropdown for new value',
                 'Current DPFO Reset Day displayed, new value dropdown available with options 1-28'),
            ]
        # Generic visibility — use scenario title for specificity
        import re as _re_vis
        _target = _re_vis.sub(r'^(?:Verify|Validate|Check|UI Verify\s*[-:]?\s*)', '', scenario_title, flags=_re_vis.IGNORECASE).strip()
        _target = _re_vis.sub(r'New\s+MVNO\s*[-:—]\s*', '', _target, flags=_re_vis.IGNORECASE).strip()
        _target = _target[:80] if _target else 'the feature'
        return [
            ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
            ('Navigate to the menu for: %s' % _target[:70], 'Screen loads with expected fields'),
            ('Verify %s is visible, correctly labeled, and accessible' % _target[:70],
             'Element is present, enabled, and interactive'),
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
                ('Enter invalid data to trigger: %s' % scenario_title[:60], 'NBOP shows appropriate validation error message'),
                ('Verify no data was changed or submitted', 'Subscriber profile unchanged, no operation executed'),
            ]

    if intent == 'negative_state':
        return [
            ('Launch NBOP and search subscriber in the required state', 'Subscriber profile loaded showing the expected state'),
            ('Attempt the operation: %s' % scenario_title[:80], 'NBOP displays error/rejection message'),
            ('Verify subscriber profile remains unchanged', 'All fields show pre-operation values, no data corruption'),
        ]

    if intent == 'edge_case':
        if 'duplicate' in sc or 'concurrent' in sc:
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
    elif 'change feature' in ctx or 'reset feature' in ctx:
        steps.append(('Click Features button, toggle the target feature, submit', 'Feature checkbox state updated'))
    elif 'change bcd' in ctx or 'change dpfo' in ctx or 'dpfo reset' in ctx or 'bill cycle' in ctx or 'reset day' in ctx:
        # Check if this is the wearable/paired device propagation scenario
        _is_wearable_bcd = any(kw in sc for kw in ['wearable', 'paired', 'watch', 'host mdn', 'propagat'])
        if _is_wearable_bcd:
            steps.append(('Navigate to ≡ Menu → Manage Line → Change DPFO Reset Day', 'Change DPFO Reset Day screen loads'))
            steps.append(('Verify Current DPFO Reset Day for Host MDN (read-only)', 'Current DPFO Reset Day value shown'))
            steps.append(('Select new DPFO Reset Day value (1-28) from dropdown, click Submit', 'Change BCD request submitted for Host MDN'))
            steps.append(('Verify NBOP Account Information shows new DPFO Reset Day for Host MDN',
                           'Host MDN DPFO Reset Day updated to new value'))
            steps.append(('Verify Watch symbol (⌚) is displayed next to Host MDN — confirms paired wearable exists',
                           'Watch symbol visible next to Host MDN in subscriber profile'))
            steps.append(('Click Person symbol (👤) on the left panel to expand account lines',
                           'Account lines panel expands showing all associated lines'))
            steps.append(('Scroll down to find the Watch device entry and click on it',
                           'Watch device line is visible in the account lines list'))
            steps.append(('Click on the blue MDN link for the Wearable/Watch line',
                           'Wearable device subscriber profile loads'))
            steps.append(('Verify Line Type = "Smart Watch" in the Wearable profile',
                           'Line Type field shows "Smart Watch"'))
            steps.append(('Verify Wearable device DPFO Reset Day matches the new BCD date set on Host MDN',
                           'Wearable DPFO Reset Day = Host MDN DPFO Reset Day — BCD propagated correctly'))
            steps.append(('Download Century Report for Wearable device — verify all features show new BCD date',
                           'Century Report confirms every feature on Wearable has the updated BCD date'))
            return steps
        # Standard Change BCD flow
        steps.append(('Navigate to ≡ Menu → Manage Line → Change DPFO Reset Day', 'Change DPFO Reset Day screen loads'))
        steps.append(('Verify Current DPFO Reset Day is displayed (read-only)', 'Current DPFO Reset Day value shown (e.g., 23)'))
        steps.append(('Select new DPFO Reset Day value (1-28) from dropdown, click Submit', 'Change BCD request submitted successfully'))
        steps.append(('Verify NBOP Account Information section shows new DPFO Reset Day (e.g., changed from 23 to 8)',
                       'DPFO Reset Day field in Account Information updated to new value'))
        steps.append(('Capture TransactionId and download Century Report HTML — validate Change BCD transaction with new date',
                       'Century Report shows Change BCD transaction with correct new DPFO Reset Day and timestamp'))
        steps.append(('In Century Report, verify ALL features listed have the new BCD date — no feature should retain the old date',
                       'Every feature entry in Century Report shows updated BCD date (e.g., all show 8, none show old 23)'))
        steps.append(('Validate requestType = MNO in http header', 'Header contains requestType=MNO'))
        steps.append(('Verify downstream updates complete (Syniverse, NSL DB, Mediation)',
                       'Downstream systems updated with new BCD — Syniverse Update Subscriber shows bcd=new_date'))
        steps.append(('Check Genesis Portal for updated BCD', 'Updated BCD visible in Genesis Portal'))
        steps.append(('Check audit logs (Transaction History, Line History)',
                       'Entry created in audit tables with correct timestamp'))
        steps.append(('Verify DPFO/BCD events triggered', 'DPFO notification event triggered for new BCD date'))
        return steps  # Return early — don't append generic verify/txn steps
    elif 'remove hotline' in ctx or 'dehotline' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Change Line Status', 'Change Line Status screen opens'))
        steps.append(('Select status "Active" to remove hotline, confirm', 'Remove Hotline request submitted, line status changes to Active'))
    elif 'get transaction status' in ctx or 'transaction status' in ctx:
        steps.append(('Navigate to ≡ Menu → Transaction History', 'Transaction History page loads'))
        steps.append(('Search for the target transaction by ID or filter by date', 'Transaction entry found with status, type, and timestamp'))
    elif 'retrigger' in ctx:
        steps.append(('Navigate to ≡ Menu → Transaction History', 'Transaction History page loads'))
        steps.append(('Locate the failed/pending transaction, click Retrigger', 'Retrigger request submitted, transaction re-processed'))
    elif 'retrieve device' in ctx or 'device detail' in ctx:
        steps.append(('Verify Device Information section on subscriber profile', 'Device Information shows IMEI, Make, Model, Equipment Type'))
    elif 'usage inquiry' in ctx or 'inquiry usage' in ctx or 'usage detail' in ctx:
        steps.append(('Navigate to ≡ Menu → Mediation Details → Usage tab', 'Usage details displayed with CDR records'))
        steps.append(('Verify usage records show correct fields: Type, Duration, Timestamp', 'Usage data matches expected records'))
    elif 'wearable' in ctx or 'device id' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Change Device and SIM', 'Change Device screen opens'))
        steps.append(('Enter wearable device details (IMEI, device ID), submit', 'Wearable device associated with line'))
    elif 'sync' in ctx:
        steps.append(('Navigate to ≡ Menu → Sync Line → Sync with Network, confirm', 'Subscriber data refreshed from network'))
    elif 'reset' in ctx:
        steps.append(('Navigate to ≡ Menu → Reset Line, select reset type, confirm', 'Reset operation completed'))
    elif 'reclaim' in ctx:
        steps.append(('Navigate to ≡ Menu → Manage Line → Reclaim MDN, confirm', 'MDN reclaimed successfully'))
    elif 'mediation' in ctx:
        steps.append(('Navigate to ≡ Menu → Mediation Details, review Subscriber Summary tab', 'Mediation fields displayed: MDN, Line ID, IMEI, IMSI, Plan Group'))
    else:
        import re as _re_op
        _op_name = _re_op.sub(r'^(?:Validate|Verify|Check)\s*', '', scenario_title, flags=_re_op.IGNORECASE).strip()
        _op_name = _re_op.sub(r'New\s+MVNO\s*[-:—]\s*', '', _op_name, flags=_re_op.IGNORECASE).strip()
        _op_name = _op_name[:70] if _op_name else 'the operation'
        steps.append(('Perform %s via NBOP portal' % _op_name, 'Operation completed successfully'))
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

    # ── 0. UI Visibility — verify the menu item/screen is accessible ──
    scenarios.append({
        'title': 'UI Verify: %s menu is visible and accessible in NBOP.' % feature_name,
        'description': 'Launch NBOP, navigate to the %s screen, verify it loads with all expected fields.' % feature_name,
        'category': 'Happy Path',
        'reasoning': 'Before testing the operation, confirm the UI entry point exists and is reachable.',
        'precondition': 'Subscriber line should be Active.',
    })

    # ── 1. Happy path — perform the operation via NBOP ──
    scenarios.append({
        'title': 'Validate %s through NBOP.' % feature_name,
        'description': 'Validate %s completes successfully through NBOP portal.' % feature_name,
        'category': 'Happy Path',
        'reasoning': 'Core happy path — does the feature work via the portal?',
        'precondition': 'Subscriber line should be Active.',
    })

    # ── 2. (Removed — subscriber profile display is always covered by the happy path TC) ──

    # ── 3. Verify Transaction History after operation ──
    # Skip for features where the main operation TC (SC1) already includes
    # Transaction History, Century Report, audit log, and Genesis verification
    # (Change BCD, Swap MDN, Activation, Change SIM, etc. all have 10+ step templates)
    _main_tc_has_full_verification = any(kw in ctx for kw in [
        'change bcd', 'change dpfo', 'dpfo reset', 'bill cycle', 'reset day',
        'swap mdn', 'swap device', 'activation', 'activate subscriber',
        'change sim', 'change device', 'change rateplan', 'change rate plan',
        'change feature', 'deactivat', 'hotline', 'remove hotline',
        'port-in', 'port in', 'sync subscriber', 'network reset',
        'reclaim mdn', 'change mdn', 'change line status',
    ])
    if not _main_tc_has_full_verification:
        scenarios.append({
            'title': 'Validate Transaction History after %s through NBOP.' % feature_name,
            'description': 'After %s, navigate to Transaction History and verify entry is recorded.' % feature_name,
            'category': 'Happy Path',
            'reasoning': 'Every operation must be auditable.',
            'precondition': '%s completed successfully.' % feature_name,
        })

    # ── 4. Verify Line History after operation ──
    # Same logic — skip when main TC already covers it
    if not _main_tc_has_full_verification and any(kw in ctx for kw in ['status', 'hotline', 'suspend', 'reconnect', 'activate', 'change']):
        scenarios.append({
            'title': 'Validate Line History after %s through NBOP.' % feature_name,
            'description': 'After %s, navigate to Line History and verify status change is recorded.' % feature_name,
            'category': 'Happy Path',
            'reasoning': 'Line History tracks all status changes.',
            'precondition': '%s completed successfully.' % feature_name,
        })

    # ── 5. (Removed — section-level verification is covered by the happy path steps) ──

    # ── 5b. Wearable/Paired Device BCD propagation ──
    # When changing BCD on a Host MDN, the new date must propagate to paired devices (watch/wearable)
    _is_bcd_feature = any(kw in ctx for kw in ['change bcd', 'change dpfo', 'dpfo reset', 'bill cycle', 'reset day'])
    if _is_bcd_feature:
        scenarios.append({
            'title': 'Validate %s propagates to Wearable/Paired device.' % feature_name,
            'description': 'Change BCD date on Host MDN and verify the same new BCD date is reflected on the paired Wearable device (Watch/Tablet).',
            'category': 'Happy Path',
            'reasoning': 'BCD change on Host MDN must cascade to all paired/wearable devices sharing the same billing cycle.',
            'precondition': 'Host MDN has an active paired Wearable device.',
        })

    # ── 6-8. Negatives — feature-specific, NOT generic MDN validation ──
    # Generic MDN search negatives (invalid MDN, empty, non-existent) are NBOP
    # search validation — they apply to ALL features and don't need to be
    # repeated per feature. Instead, generate negatives specific to the operation.
    if _is_bcd_feature:
        # BCD-specific negatives
        scenarios.append({
            'title': 'Negative: Validate %s rejects change for Deactivated line.' % feature_name,
            'description': 'Attempt to change BCD date for a Deactivated subscriber. Verify NBOP rejects the operation.',
            'category': 'Negative',
            'reasoning': 'BCD change should only be allowed for Active lines.',
            'precondition': 'Subscriber line is in Deactivated status.',
        })
        scenarios.append({
            'title': 'Negative: Validate %s with same date (no change).' % feature_name,
            'description': 'Select the same DPFO Reset Day that is already set. Verify system handles gracefully — no unnecessary transaction.',
            'category': 'Negative',
            'reasoning': 'Changing to the same value should not create a spurious transaction.',
            'precondition': 'Subscriber line should be Active with known DPFO date.',
        })
        scenarios.append({
            'title': 'Negative: Validate %s rejects change for Suspended/Hotlined line.' % feature_name,
            'description': 'Attempt to change BCD date for a Suspended or Hotlined subscriber. Verify NBOP rejects or warns.',
            'category': 'Negative',
            'reasoning': 'BCD change behavior for non-Active lines must be validated.',
            'precondition': 'Subscriber line is in Suspended or Hotlined status.',
        })
    else:
        # Generic operation negatives for non-BCD features
        scenarios.append({
            'title': 'Negative: Validate %s rejects operation for Deactivated line.' % feature_name,
            'description': 'Attempt %s on a Deactivated subscriber. Verify NBOP rejects the operation.' % feature_name,
            'category': 'Negative',
            'reasoning': 'Operations should validate line status before proceeding.',
            'precondition': 'Subscriber line is in Deactivated status.',
        })
        scenarios.append({
            'title': 'Negative: Validate %s handles invalid input in NBOP.' % feature_name,
            'description': 'Enter invalid data on %s screen. Verify appropriate error message.' % feature_name,
            'category': 'Negative',
            'reasoning': 'UI must validate input and show clear error.',
            'precondition': 'NBOP portal accessible, subscriber loaded.',
        })

    # ── 9. (Removed — session timeout TC not required for UI features) ──

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

    if 'change feature' in ctx or 'reset feature' in ctx or 'add feature' in ctx:
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
