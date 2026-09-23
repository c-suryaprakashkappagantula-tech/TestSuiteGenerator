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
Knowledge: TestSuiteGenerator/nbop_knowledge_base.json
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from .step_templates import strip_dangling_tail, truncate_at_word  # word-boundary truncation, not raw slicing

logger = logging.getLogger(__name__)

# ── Load the UI map (crawl discovery data) ──
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


# ── Load the knowledge base (externalized constants) ──
_KNOWLEDGE_BASE = None
_KNOWLEDGE_BASE_PATHS = [
    Path(__file__).parent.parent / 'nbop_knowledge_base.json',
]


def _load_knowledge_base() -> dict:
    """Load nbop_knowledge_base.json. Returns empty dict if missing."""
    global _KNOWLEDGE_BASE
    if _KNOWLEDGE_BASE is not None:
        return _KNOWLEDGE_BASE
    for p in _KNOWLEDGE_BASE_PATHS:
        if p.exists():
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    _KNOWLEDGE_BASE = json.load(f)
                logger.info("NBOP knowledge base loaded from %s", p)
                return _KNOWLEDGE_BASE
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to load knowledge base from %s: %s", p, exc)
    _KNOWLEDGE_BASE = {}
    return _KNOWLEDGE_BASE


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

def _get_feature_to_page() -> Dict[str, str]:
    """Load feature-to-page mapping from JSON, with hardcoded fallback."""
    kb = _load_knowledge_base()
    if kb.get('feature_to_page'):
        return kb['feature_to_page']
    # Hardcoded fallback in case JSON is missing
    return {
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
        'line history': 'Context: Line History',
        'transaction history': 'Context: Transaction History',
        'notification': 'Context: Notifications',
        'voice detail': 'Context: Voice Details',
        'data detail': 'Context: Data Details',
        'sms detail': 'Context: SMS/MMS Details',
        'mms detail': 'Context: SMS/MMS Details',
        'mediation detail': 'Context: Mediation Details',
        'mediation subscriber': 'Context: Mediation Details',
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
        'line summary': 'Button: Line Summary(MNO)',
        'features': 'Button: Features',
        'qr code': 'Button: View QR CODE',
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


# Cached reference — loaded once on first access
_FEATURE_TO_PAGE: Optional[Dict[str, str]] = None


def _feature_to_page() -> Dict[str, str]:
    """Get the feature-to-page map (cached after first call)."""
    global _FEATURE_TO_PAGE
    if _FEATURE_TO_PAGE is None:
        _FEATURE_TO_PAGE = _get_feature_to_page()
    return _FEATURE_TO_PAGE


def find_nbop_page(feature_name: str, description: str = '') -> Optional[str]:
    """Given a feature name/description, find the matching NBOP page."""
    ctx = (feature_name + ' ' + description).lower()
    for keyword, page_name in _feature_to_page().items():
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

    # ── Hand-maintained fallback: feature→page map ──
    # Used when the live crawl (nbop_ui_map.json) is absent, so navigation still
    # resolves to a real page (e.g. 'Manage Line → Change SIM') instead of the
    # bare default. Longest phrase first for the most specific match.
    f2p = _feature_to_page()
    for phrase in sorted(f2p, key=len, reverse=True):
        if phrase in ctx:
            page = (f2p[phrase] or '').strip()
            if not page:
                continue
            low = page.lower()
            if low.startswith('tile:'):
                return 'NBOP → Mobile Service Management → %s' % page.split(':', 1)[1].strip()
            if '→' in page or 'manage line' in low or 'menu' in low:
                return 'NBOP → Subscriber Profile → ≡ Menu → %s' % page
            return 'NBOP → Mobile Service Management → %s' % page

    # Default
    return 'NBOP → Mobile Service Management'


# ════════════════════════════════════════════════════════════════════
#  FIELD KNOWLEDGE — what fields exist on each page
# ════════════════════════════════════════════════════════════════════

# ── Fallback constants (used when JSON is missing) ──
_FALLBACK_PROFILE_SECTIONS = {
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

_FALLBACK_CARD_HEADERS = ['Account (ACC...)', 'MDN', 'IMEI1', 'ICCID']
_FALLBACK_PROFILE_BUTTONS = ['View All', 'View', 'View QR CODE', 'Line Summary(MNO)',
                             'Service Plan', 'Features']
_FALLBACK_HISTORY_TABS = ['Port In Activation', 'New MDN Activation', 'Wearable Activation',
                          'HMNO Activation', 'Port Out History', 'MDN/SIM/Device History']
_FALLBACK_MEDIATION_TABS = ['Subscriber Summary', 'Subscriber History']
_FALLBACK_MEDIATION_FIELDS = [
    'Biller Account Indicator', 'Mobile Solo Account ID', 'Line Status',
    'DPFO Reset Day', 'MDN', 'Line ID', 'IMEI (Device)', 'IMSI',
    'HMNO IMEI (Device)', 'HMNO IMSI', 'Plan Group', 'Wholesale Plan',
    'Start Date', 'End Date', 'Speed Reduction Flag',
]
_FALLBACK_GSMA_TABS = ['Manage Blocklist', 'GSMA Blocklist Inquiry', 'Charter Blocklist History']
_FALLBACK_ACTIVATION_TABS = ['Subscriber Line', 'Network only Line']
_FALLBACK_NOTIFICATION_TABS = ['DPFO Notifications']
_FALLBACK_SFTP_REPORTS = ['MDN Swap', 'Aging Port-in', 'Subscriber Differential Report',
                          'CBU Subscriber differential', 'Delayed Port-in', 'eSIM Errors']


def _kb_get(key: str, fallback):
    """Get a value from the knowledge base JSON, falling back to hardcoded."""
    kb = _load_knowledge_base()
    return kb.get(key, fallback)


# ── Public accessors (JSON-first, fallback-safe) ──


def _get_profile_sections() -> Dict[str, List[str]]:
    return _kb_get('profile_sections', _FALLBACK_PROFILE_SECTIONS)


def _get_card_headers() -> List[str]:
    return _kb_get('card_headers', _FALLBACK_CARD_HEADERS)


def _get_profile_buttons() -> List[str]:
    return _kb_get('profile_buttons', _FALLBACK_PROFILE_BUTTONS)


def _get_history_tabs() -> List[str]:
    return _kb_get('history_tabs', _FALLBACK_HISTORY_TABS)


def _get_mediation_tabs() -> List[str]:
    return _kb_get('mediation_tabs', _FALLBACK_MEDIATION_TABS)


def _get_mediation_fields() -> List[str]:
    return _kb_get('mediation_fields', _FALLBACK_MEDIATION_FIELDS)


def _get_gsma_tabs() -> List[str]:
    return _kb_get('gsma_tabs', _FALLBACK_GSMA_TABS)


def _get_activation_tabs() -> List[str]:
    return _kb_get('activation_tabs', _FALLBACK_ACTIVATION_TABS)


def _get_notification_tabs() -> List[str]:
    return _kb_get('notification_tabs', _FALLBACK_NOTIFICATION_TABS)


def _get_sftp_reports() -> List[str]:
    return _kb_get('sftp_reports', _FALLBACK_SFTP_REPORTS)


# Module-level constants — loaded from JSON on first access, cached thereafter
PROFILE_SECTIONS = _get_profile_sections()
CARD_HEADERS = _get_card_headers()
PROFILE_BUTTONS = _get_profile_buttons()
HISTORY_TABS = _get_history_tabs()
MEDIATION_TABS = _get_mediation_tabs()
MEDIATION_FIELDS = _get_mediation_fields()
GSMA_TABS = _get_gsma_tabs()
ACTIVATION_TABS = _get_activation_tabs()
NOTIFICATION_TABS = _get_notification_tabs()
SFTP_REPORTS = _get_sftp_reports()


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

    # Context-menu histories use the dedicated history route, not the tile-level
    # activation-history branch or generic element visibility routing.
    if any(kw in sc for kw in ['transaction history', 'line history', 'service history']):
        return 'history'

    # ── Priority intents: element removal / element verification ──
    # These take priority over generic intents because they indicate specific
    # element-level assertions rather than general visibility checks.
    if any(kw in sc for kw in ['removed', 'hidden', 'not displayed', 'hide']):
        return 'element_removal'
    # Element verification: scenario mentions a specific element name with a
    # positive display/visibility state (e.g., "Port Status is displayed")
    if any(kw in sc for kw in ['is displayed', 'is visible', 'should be displayed']):
        return 'element_verification'

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


def _extract_element_names(scenario_title: str) -> List[str]:
    """Extract specific element names from a scenario title.

    Looks for names in parentheses like "Port Status (Syniverse)" or
    quoted names like '"Transaction Id"'.
    """
    import re
    elements = []
    # Match patterns like "Element Name (Detail)" — full phrase including parens
    paren_matches = re.findall(r'([\w\s/\-]+\([^)]+\))', scenario_title)
    for m in paren_matches:
        elements.append(m.strip())
    # If no parenthesized elements, look for quoted element names
    if not elements:
        quoted = re.findall(r'"([^"]+)"', scenario_title)
        elements.extend(quoted)
    # If still nothing, look for capitalized multi-word phrases that look like
    # UI element names (e.g., "Port Status", "Transaction Id")
    if not elements:
        caps = re.findall(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', scenario_title)
        elements.extend(caps)
    return elements


def _detect_history_tab(scenario_title: str) -> Optional[str]:
    """Map only explicit activation/history subjects to a specific History tab."""
    sc = scenario_title.lower()
    if 'port in activation' in sc or 'port-in activation' in sc or 'update portin' in sc:
        return 'Port In Activation'
    if 'new mdn activation' in sc:
        return 'New MDN Activation'
    if 'wearable activation' in sc:
        return 'Wearable Activation'
    if 'mno activation history' in sc or 'hmno activation' in sc:
        return 'HMNO Activation'
    if 'port out history' in sc or 'port-out history' in sc:
        return 'Port Out History'
    if any(term in sc for term in ('mdn/sim/device history', 'mdn sim device history')):
        return 'MDN/SIM/Device History'
    return None


def _is_history_scenario(scenario_title: str) -> bool:
    """True only for scenarios that explicitly target a tile-level History tab."""
    sc = scenario_title.lower()
    if any(term in sc for term in ('transaction history', 'line history', 'service history')):
        return False
    return _detect_history_tab(scenario_title) is not None


def _normalize_provider(provider: str, text: str = '') -> str:
    """Normalize caller-supplied provider metadata with a conservative fallback."""
    import re
    normalized = (provider or '').strip().upper()
    if normalized in ('TMO', 'VZW', 'MIXED'):
        return normalized
    has_tmo = bool(re.search(r'\b(?:tmo|t-mobile|tmobile)\b', text or '', re.IGNORECASE))
    has_vzw = bool(re.search(r'\b(?:vzw|vz|verizon)\b', text or '', re.IGNORECASE))
    if has_vzw and not has_tmo:
        return 'VZW'
    return 'TMO'


def _is_zip_location_flow(text: str) -> bool:
    """Recognize ZIP-driven activation/Change MDN requirements without NPANXX wording."""
    import re
    return (
        bool(re.search(r'\b(?:zip\s*code|zip-code|zipcode)\b', text or '', re.IGNORECASE)) and
        any(term in (text or '').lower() for term in (
            'change mdn', 'mdn screen', 'mdn-screen',
            'activation screen', 'activation flow'))
    )


def _mentions_port_in(text: str) -> bool:
    """Recognize Port-In as a word/phrase, never as part of words such as support."""
    import re
    return bool(re.search(r'\bport(?:-|\s+)?in\b', text or '', re.IGNORECASE))


def _history_lookup_step(tab_name: str) -> Tuple[str, str]:
    """Use the Port Status filter only on Port In Activation history."""
    if tab_name == 'Port In Activation':
        return ('Select Port Status = IN PROGRESS from dropdown, click Search',
                'IN PROGRESS Port-In records are filtered and displayed')
    return ('Search for the activation record by MDN or Transaction ID',
            'The matching record is displayed with the expected provider and status')


def _change_features_steps(ctx: str, sc: str,
                           provider: str = '') -> Optional[List[Tuple[str, str]]]:
    """Return concrete Change Features navigation/actions for matching scenarios."""
    import re as _re
    named_change_features = any(term in ctx for term in (
        'change feature', 'change features', 'optional feature', 'included feature',
        'required feature', 'pdl data', 'feature list'))
    conflict_requirement = (
        'feature' in ctx and any(term in ctx for term in ('conflict', 'incompatible')))
    feature_operation = bool(_re.search(
        r'\b(?:add(?:ing)?|remov(?:e|ing))\b.{0,40}\bfeatures?\b', ctx))
    if not (named_change_features or conflict_requirement or feature_operation):
        return None

    provider = _normalize_provider(provider, sc)
    provider_label = 'TMO and VZW' if provider == 'MIXED' else provider

    positive_menu_visibility = (
        'manage line' in sc and
        any(term in sc for term in (
            'visible', 'visibility', 'display', 'displayed', 'accessible', 'available')) and
        not any(term in sc for term in (
            'not displayed', 'not visible', 'hidden', 'disabled', 'unavailable')))
    if positive_menu_visibility:
        has_mno_tmo_on = (
            'mno_tmo' in sc and 'permission' in sc and
            bool(_re.search(r'\b(?:is\s+)?on\b', sc)))
        has_active_line = bool(_re.search(
            r'\b(?:line\s+status\s+is|status\s*=?)\s*active\b', sc))
        if has_mno_tmo_on and has_active_line:
            return [
                ('Launch NBOP and load the target TMO subscriber profile',
                 'Subscriber profile loads for a TMO network line'),
                ('Verify the subscriber MNO is TMO and Line Status is Active',
                 'The profile identifies a TMO line with Line Status=Active'),
                ('Verify the logged-in agent has MNO_TMO permission set to ON',
                 'The active session includes enabled MNO_TMO access'),
                ('Click ≡ Menu on the subscriber profile',
                 'Subscriber action menu opens for the eligible line'),
                ('Select Manage Line without starting a line operation',
                 'Manage Line submenu displays its available operations'),
                ('Verify Change Features is displayed and enabled under Manage Line',
                 'Change Features is visible for MNO_TMO permission ON and the Active TMO line'),
            ]
        return [
            ('Launch NBOP and load a subscriber profile eligible for Manage Line actions',
             'Subscriber profile loads with the action menu available'),
            ('Click ≡ Menu on the subscriber profile',
             'Subscriber action menu opens'),
            ('Select Manage Line without opening an operation screen',
             'Manage Line submenu displays its available entries'),
            ('Verify the Change Features entry is displayed in the Manage Line menu',
             'Change Features is visible and selectable; no feature-change transaction is submitted'),
        ]

    steps = [
        ('Launch NBOP and search an active %s subscriber by MDN' % provider_label,
         'Subscriber profile loads for the required provider'),
        ('Click ≡ Menu on the subscriber profile',
         'Subscriber action menu opens'),
        ('Select Manage Line',
         'Manage Line submenu displays available line operations'),
        ('Select Change Features',
         'Change Features screen loads for the subscriber'),
    ]

    if 'menu' in sc and any(term in sc for term in (
            'not displayed', 'not visible', 'hidden', 'disabled', 'unavailable')):
        steps.append(('Verify Change Features is not available under Manage Line',
                      'Change Features is hidden or disabled for the scenario state'))
        return steps

    if 'conflict' in sc or 'incompatible' in sc:
        steps.extend([
            ('Record the currently selected Required, Included, and Optional features',
             'A baseline feature state is available for comparison'),
            ('Select an Optional feature that conflicts with an existing feature and click Submit',
             'NBOP displays the configured conflicting-feature error'),
            ('Verify the error identifies the conflicting feature combination',
             'The user receives a specific conflict message and the request is rejected'),
            ('Refresh Change Features and verify no feature state changed',
             'Required, Included, and Optional selections match the recorded baseline'),
            ('Open ≡ Menu → Transaction History and verify no completed Change Features transaction was created',
             'No successful transaction exists for the rejected feature change'),
        ])
        return steps

    if any(term in sc for term in ('optional list', 'list of optional', 'from response', 'api response', 'response optional')):
        steps.extend([
            ('Capture the backend response used to populate Change Features',
             'Response evidence contains the eligible Optional feature list'),
            ('Verify every Optional feature returned in the response is listed on the screen',
             'NBOP Optional features exactly match the response with no missing or extra entries'),
            ('Verify Required and Included features remain clearly separated from Optional features',
             'Feature categories are rendered under the correct headings'),
        ])
        return steps

    if any(term in sc for term in ('required', 'included', 'pdl data')) and not any(term in sc for term in ('add', 'remove', 'toggle')):
        steps.extend([
            ('Verify the screen contains separate Required, Included, and Optional feature sections',
             'All three feature categories are visible and correctly labeled'),
            ('Verify PDL Data is displayed with the subscriber feature information',
             'PDL Data label and displayed content exactly match the captured backend feature response'),
            ('Compare displayed selections with the current backend feature response',
             'Required, Included, Optional, and PDL Data values match the response'),
        ])
        return steps

    is_remove = bool(_re.search(r'\b(?:remove|removing|deselect|turn\s+off|disable)\b', sc))
    is_add = (bool(_re.search(r'\b(?:add|adding|turn\s+on|enable)\b', sc)) or
              bool(_re.search(r'\bselect\b', sc)))
    is_operation = is_add or is_remove or any(term in sc for term in ('completed transaction', 'complete transaction', 'submit'))

    if provider == 'VZW':
        steps.append(('Verify the VZW subscriber sees Required, Included, Optional, and PDL Data using VZW response values',
                      'VZW Change Features layout and values have parity with the supported TMO behavior'))
        if not is_operation:
            return steps

    if is_operation:
        steps.append(('Record the current state of the target Optional feature',
                      'The pre-change feature state is captured'))
        if is_add:
            steps.extend([
                ('Select an eligible target Optional feature and click Submit',
                 'The add request is accepted and an add Transaction ID is returned'),
                ('Reload Change Features and verify the target Optional feature is selected',
                 'The added Optional feature state is persisted'),
            ])
        if is_remove:
            steps.extend([
                ('Clear the selected target Optional feature and click Submit',
                 'The remove request is accepted and a remove Transaction ID is returned'),
                ('Reload Change Features and verify the target Optional feature is not selected',
                 'The removed Optional feature state is persisted'),
            ])
        steps.append(('Open ≡ Menu → Transaction History and locate each returned Transaction ID',
                      'Completed Change Features transactions record the correct add/remove actions'))
        return steps

    steps.extend([
        ('Verify Required, Included, and Optional sections plus PDL Data are displayed',
         'All feature categories and PDL Data load from the subscriber response'),
        ('Select an eligible Optional feature toggle and click Submit',
         'Change Features request succeeds and returns a Transaction ID'),
        ('Verify the Optional feature state is updated on the Change Features screen',
         'The selected Optional feature reflects the submitted state'),
        ('Open ≡ Menu → Transaction History and locate the Transaction ID',
         'The Change Features transaction is recorded as Completed'),
    ])
    return steps


def _port_in_operation_steps(ctx: str, sc: str,
                             provider: str = '') -> Optional[List[Tuple[str, str]]]:
    """Return concrete Update or Cancel/Update flows for in-progress Port-In records."""
    is_port_in = _mentions_port_in(sc)
    has_update = 'update' in sc
    has_cancel = 'cancel' in sc
    if not (is_port_in and has_update):
        return None

    provider = _normalize_provider(provider, sc)
    provider_label = 'TMO and VZW' if provider == 'MIXED' else provider
    zip_only = 'npanxx' in ctx or _is_zip_location_flow(ctx)

    if has_cancel:
        update_action = 'Click Update PortIn, enter valid changed data, and submit'
        update_result = 'Update returns a Transaction ID and the changed Port-In data persists'
        if zip_only:
            update_action = 'Click Update PortIn, enter valid changed ZIP-code data without NPANXX, and submit'
            update_result = 'Update returns a Transaction ID and persists ZIP-code-only Port-In data'
        return [
            ('Launch NBOP with two suitable %s Port-In records in IN PROGRESS status' % provider_label,
             'Separate eligible records are available for Update and Cancel validation'),
            ('Navigate to History → Port In Activation',
             'Port In Activation history screen loads'),
            ('Set Port Status to IN PROGRESS and click Search',
             'IN PROGRESS Port-In records are displayed'),
            ('Open the first suitable record and verify both Update PortIn and Cancel PortIn actions',
             'Both actions are available for an eligible in-progress record'),
            (update_action,
             update_result),
            ('Return to the IN PROGRESS results and open a second suitable Port-In record',
             'A separate eligible record opens for cancellation without reusing the updated record'),
            ('Click Cancel PortIn, confirm cancellation, and capture the Transaction ID',
             'Cancel is accepted and the selected Port-In record leaves the in-progress state'),
            ('Validate both Transaction IDs and reopen both records',
             'The update retains its changed values and the cancelled record shows the correct cancelled status'),
        ]

    update_action = 'Click Verify/Update PortIn and enter the required update data'
    update_result = 'Verify/Update PortIn accepts the scenario data'
    if zip_only:
        update_action += ' using ZIP code without an NPANXX value'
        update_result += ' without an NPANXX UI dependency'

    return [
        ('Launch NBOP with an eligible %s Port-In record' % provider_label,
         'NBOP loads with provider-appropriate Port-In test data'),
        ('Navigate to History → Port In Activation',
         'Port In Activation history screen loads'),
        ('Set Port Status to IN PROGRESS and click Search',
         'IN PROGRESS Port-In records are displayed'),
        ('Open the required Port-In record',
         'Port-In details display the Verify/Update PortIn action'),
        (update_action + ' and submit',
         update_result + ' and returns a transaction result'),
        ('Verify the record and Transaction History show the updated Port-In result',
         'The Port-In update is persisted for the correct provider'),
    ]


def _transaction_history_detail_steps(ctx: str, sc: str,
                                      provider: str = '') -> Optional[List[Tuple[str, str]]]:
    """Open a Transaction ID and validate provider details independently of NPANXX."""
    if 'transaction history' not in sc:
        return None
    if not any(term in sc for term in (
            'transaction id', 'detail', 'mno', 'provider', 'networkprovider',
            'network provider')):
        return None

    provider = _normalize_provider(provider, sc)
    provider_label = 'TMO and VZW' if provider == 'MIXED' else provider
    steps = [
        ('Launch NBOP and search an active %s subscriber by MDN' % provider_label,
         'Subscriber profile loads with the expected provider identity'),
        ('Open ≡ Menu → Transaction History',
         'Transaction History loads for the subscriber'),
        ('Locate the target operation and click its Transaction ID',
         'Transaction detail view opens for the selected operation'),
        ('Verify the transaction detail shows the expected MNO/provider',
         'MNO/provider matches the subscriber and operation route'),
    ]
    if any(term in sc for term in ('api', 'route', 'routing', 'endpoint')):
        steps.append(('Compare the detail provider with the captured provider API route',
                      'Transaction detail and provider-route evidence match'))
    else:
        steps.append(('Verify the transaction type, status, and provider details match the operation',
                      'Transaction detail records the correct completed operation'))
    return steps


def _npanxx_steps(ctx: str, sc: str,
                  provider: str = '') -> Optional[List[Tuple[str, str]]]:
    """Return exact NBOP flows for NPANXX and ZIP-only provider requirements."""
    if 'npanxx' not in ctx and not _is_zip_location_flow(ctx):
        return None

    provider = _normalize_provider(provider, sc)
    provider_label = 'TMO and VZW' if provider == 'MIXED' else provider
    launch = ('Launch NBOP and search an active %s subscriber by MDN' % provider_label,
              '%s subscriber profile loads with the correct provider' % provider_label)

    is_vzw_regression = (
        provider == 'VZW' and any(term in sc for term in (
            'regression', 'no change', 'unchanged', 'parity', 'not affected',
            'does not affect', 'must not affect', 'should not affect'))
    )
    if is_vzw_regression:
        return [
            launch,
            ('Open each NPANXX-affected NBOP screen named by the requirement',
             'VZW screens load using RequestType=VZW with their existing supported fields'),
            ('Verify VZW retains its current ZIP/provider behavior',
             'No TMO-only NPANXX change alters the VZW flow'),
            ('Perform the supported VZW lookup or operation and submit',
             'VZW operation completes through the VZW route'),
            ('Open ≡ Menu → Transaction History and click the operation Transaction ID',
             'Transaction detail records VZW as the MNO/provider'),
            ('Verify the VZW UI result and backend evidence are unchanged',
             'Regression evidence confirms the existing VZW behavior'),
        ]

    if _mentions_port_in(sc) and 'update' in sc:
        return [
            launch,
            ('Navigate to History → Port In Activation',
             'Port In Activation history screen loads'),
            ('Set Port Status to IN PROGRESS and click Search',
             'IN PROGRESS Port-In records are displayed'),
            ('Open the required Port-In record',
             'Port-In details display the Update PortIn action'),
            ('Click Update PortIn, enter the required ZIP code without NPANXX, and submit',
             'Update PortIn request is accepted using ZIP-code-only input'),
            ('Verify the record and Transaction History show the updated Port-In result',
             'Port-In update completes without an NPANXX UI field or request dependency'),
        ]

    if 'transaction history' in sc and any(term in sc for term in ('mno', 'provider', 'network')):
        return [
            launch,
            ('Open ≡ Menu → Transaction History',
             'Transaction History loads for the subscriber'),
            ('Locate the target operation and click its Transaction ID',
             'Transaction detail view opens'),
            ('Verify the transaction detail shows the expected MNO/provider',
             'MNO/provider matches the subscriber and operation route'),
            ('Verify ZIP/provider details are present and NPANXX is not required from the UI',
             'Transaction evidence reflects ZIP-code-only processing'),
        ]

    if 'activation history' in sc and 'mno' in sc:
        return [
            launch,
            ('Navigate to History → HMNO Activation',
             'HMNO Activation History records load with MDN and Transaction ID search fields'),
            ('Search for the activation record by MDN or activation Transaction ID',
             'The matching activation record is displayed'),
            ('Open the activation record and verify MNO/provider and operation status',
             'Activation History records the expected provider and status for the selected record'),
            ('Verify activation used ZIP-code-only input and no UI NPANXX value',
             'Activation evidence contains supported ZIP/provider data without NPANXX input'),
        ]

    activation_required = 'activation' in sc or 'activate' in sc
    change_mdn_required = ('change mdn' in sc or
                           (any(term in sc for term in ('mdn screen', 'mdn-screen')) and
                            _is_zip_location_flow(sc)))
    both_required = ('both' in sc and ('screen' in sc or 'flow' in sc)) or (activation_required and change_mdn_required)

    if both_required:
        return [
            launch,
            ('Open the activation screen and enter the required ZIP code without NPANXX',
             'Activation screen accepts ZIP-code-only input'),
            ('Submit activation and verify the subscriber activates successfully',
             'Activation completes without an NPANXX UI field'),
            ('Return to the subscriber profile and open ≡ Menu → Manage Line → Change MDN',
             'Change MDN screen loads'),
            ('Enter the new MDN and required ZIP code without NPANXX, then submit',
             'Change MDN request is accepted using ZIP-code-only input'),
            ('Verify Transaction History and Line Summary(MNO) for both operations',
             'Both transactions complete with the correct provider and no NPANXX dependency'),
        ]

    activation_screen_absence = (
        activation_required and
        any(term in sc for term in ('activation screen', 'activation form')) and
        any(term in sc for term in ('not displayed', 'hidden', 'absent')) and
        any(term in sc for term in ('option', 'control', 'label', 'field', 'npanxx')))
    if activation_screen_absence:
        return [
            ('Launch NBOP and begin a new %s network activation' % provider,
             'The activation workflow opens for the expected provider without submitting a transaction'),
            ('Open the subscriber activation screen and inspect its location inputs',
             'Activation form controls and labels are rendered for UI validation'),
            ('Verify no NPANXX input control or NPANXX label is displayed',
             'NPANXX is absent from the activation-screen UI'),
            ('Verify the ZIP Code input is displayed and marked required',
             'ZIP Code is the visible required location field'),
            ('Validate the form requires ZIP Code and accepts a valid ZIP value without revealing NPANXX',
             'Screen validation enforces ZIP Code while NPANXX remains absent; activation is not submitted'),
        ]

    zip_only_activation_e2e = (
        activation_required and
        'activation flow' in sc and
        any(term in sc for term in ('removed', 'removal', 'remove', 'no longer')))
    if zip_only_activation_e2e:
        return [
            ('Launch NBOP and start a new %s network activation' % provider,
             'Activation workflow opens for the expected provider'),
            ('Enter subscriber, device, SIM, and required ZIP Code data with no NPANXX input',
             'The activation form accepts the complete ZIP-code-only request'),
            ('Submit activation and capture the returned Transaction ID',
             'Activation request is accepted and completes successfully'),
            ('Verify the subscriber profile and Line Summary(MNO) show the activated line and provider',
             'The resulting profile is Active and records the expected %s provider' % provider),
            ('Open Transaction History and validate the captured activation Transaction ID',
             'Completed transaction evidence confirms ZIP-code-only activation with no NPANXX dependency'),
        ]

    if change_mdn_required:
        return [
            launch,
            ('Click ≡ Menu → Manage Line → Change MDN',
             'Change MDN screen loads'),
            ('Enter the new MDN and required ZIP code; leave NPANXX absent',
             'The form accepts ZIP-code-only routing input'),
            ('Click Submit and capture the returned Transaction ID',
             'Change MDN request is accepted'),
            ('Verify the subscriber profile shows the new MDN',
             'Line Information reflects the completed MDN change'),
            ('Open Transaction History and Line Summary(MNO) for the Transaction ID',
             'Provider details are correct and no NPANXX request dependency is present'),
        ]

    if activation_required:
        return [
            launch,
            ('Open the applicable subscriber activation screen',
             'Activation form loads for the %s route' % provider),
            ('Enter all required activation data and ZIP code without NPANXX',
             'The activation form accepts ZIP-code-only location input'),
            ('Submit the activation and capture the Transaction ID',
             'Activation request is accepted for the %s subscriber' % provider),
            ('Verify the subscriber profile and Line Summary(MNO) show the activated line',
             'Activation result and provider match the submitted subscriber'),
            ('Open Transaction History and verify the completed activation record',
             'Transaction evidence confirms no NPANXX UI/request dependency'),
        ]

    if provider == 'VZW':
        return [
            launch,
            ('Open each NPANXX-affected NBOP screen named by the requirement',
             'VZW screens load with their existing supported fields'),
            ('Verify VZW retains its current ZIP/provider behavior',
             'No TMO-only NPANXX change alters the VZW flow'),
            ('Perform the supported VZW lookup or operation and submit',
             'VZW operation completes through the VZW route'),
            ('Verify Transaction History records VZW as the MNO/provider',
             'Regression evidence confirms VZW behavior is unchanged'),
        ]

    return [
        launch,
        ('Open the NPANXX-affected NBOP screen named by the requirement',
         'The required screen loads'),
        ('Enter the required ZIP code without entering NPANXX and submit',
         'NBOP accepts ZIP-code-only input'),
        ('Verify the operation completes for the TMO subscriber',
         'TMO result is returned without an NPANXX UI dependency'),
        ('Verify Transaction History or Line Summary(MNO) records the correct provider',
         'Backend evidence confirms successful TMO routing'),
    ]


def generate_ui_steps(feature_name: str, description: str = '',
                      scenario_title: str = '',
                      network_provider: str = '') -> List[Tuple[str, str]]:
    """Generate NBOP UI test steps based on scenario INTENT.

    ENHANCED: When scenario_title contains specific element names
    (e.g., "Port Status (Syniverse) removed"), the function now:
      1. Extracts element names from scenario_title
      2. Determines the page where the element lives
      3. Generates navigate → filter → verify-element sequence

    Priority: scenario_title > feature_name for step specificity.
    """
    ctx = (feature_name + ' ' + description + ' ' + scenario_title).lower()
    sc = scenario_title.lower()
    scenario_detail = ('%s %s' % (scenario_title, description)).lower()
    nav_path = get_navigation_path(feature_name, description)
    intent = _classify_scenario_intent(scenario_title)

    # Concrete feature families take precedence over generic visibility/action routing.
    provider = _normalize_provider(network_provider, scenario_detail)
    change_feature_steps = _change_features_steps(ctx, scenario_detail, provider)
    if change_feature_steps:
        return change_feature_steps

    port_in_steps = _port_in_operation_steps(ctx, scenario_detail, provider)
    if port_in_steps:
        return port_in_steps

    transaction_detail_steps = _transaction_history_detail_steps(
        ctx, scenario_detail, provider)
    if transaction_detail_steps:
        return transaction_detail_steps

    npanxx_steps = _npanxx_steps(ctx, scenario_detail, provider)
    if npanxx_steps:
        return npanxx_steps

    # ── Element-name-aware step generation (Task 2.2) ──
    # When scenario_title contains specific element names, generate targeted steps
    if scenario_title and intent in ('element_removal', 'element_verification'):
        elements = _extract_element_names(scenario_title)
        if elements:
            element_name = elements[0]
            steps = []

            # Determine if this is a History-related scenario (Task 2.3)
            if _is_history_scenario(scenario_title):
                tab_name = _detect_history_tab(scenario_title)
                steps.append(('Launch NBOP and search subscriber by MDN',
                              'Subscriber profile loaded'))
                steps.append(('Navigate to Tile: History',
                              'History page loads with tab options'))
                steps.append(('Click %s tab' % tab_name,
                              '%s tab content loads with search filters' % tab_name))
                # Port status is specific to Port In Activation; other activation
                # histories are searched by subscriber/transaction identity.
                steps.append(_history_lookup_step(tab_name))
            else:
                # Non-history element scenario — navigate to relevant page
                page = find_nbop_page(feature_name, description) or nav_path
                steps.append(('Launch NBOP and search subscriber by MDN',
                              'Subscriber profile loaded'))
                steps.append(('Navigate to %s' % page,
                              'Page loads with expected content'))

            # Generate verification step based on intent
            # Extract condition from scenario title (e.g., "for TMO")
            import re
            condition_match = re.search(r'\bfor\s+(\w+)', scenario_title)
            condition = 'for %s' % condition_match.group(1) if condition_match else ''

            if intent == 'element_removal':
                steps.append(("Verify '%s' is NOT displayed on the screen %s" % (element_name, condition),
                              "'%s' element is absent from the page" % element_name))
                steps.append(('Verify all other information remains visible and unchanged',
                              'Remaining page elements display correctly'))
            else:  # element_verification
                steps.append(("Verify '%s' IS displayed on the screen %s" % (element_name, condition),
                              "'%s' element is present and correctly rendered" % element_name))

            return steps

    # ── History-tab-aware step generation (Task 2.3) ──
    # Even without specific element names, if scenario references History page
    if scenario_title and _is_history_scenario(scenario_title):
        tab_name = _detect_history_tab(scenario_title)
        steps = [
            ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
            ('Navigate to Tile: History', 'History page loads with tab options'),
            ('Click %s tab' % tab_name,
             '%s tab content loads with search filters' % tab_name),
            _history_lookup_step(tab_name),
            ('Verify search results display expected records',
             'Records show the expected provider, operation status, and subscriber identity'),
        ]
        return steps

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
        # Cut any trailing validation phrase so only the menu/feature NAME remains
        # (prevents 'Navigate to the menu for: X menu is visible and accessible').
        _target = _re_vis.split(
            r'\s+(?:menu\s+is|is|are|should|displays?|loads?|reflects?|must)\b',
            _target, maxsplit=1)[0].strip()
        _target = strip_dangling_tail(truncate_at_word(_target, 80)) if _target else 'the feature'
        _nav = get_navigation_path(_target)
        return [
            ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
            ('Navigate: %s' % _nav, 'Screen loads with the expected fields and controls'),
            ('Verify %s is visible, correctly labeled, and accessible' % truncate_at_word(_target, 70),
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
                ('Enter invalid data to trigger: %s' % truncate_at_word(scenario_title, 60), 'NBOP shows appropriate validation error message'),
                ('Verify no data was changed or submitted', 'Subscriber profile unchanged, no operation executed'),
            ]

    if intent == 'negative_state':
        return [
            ('Launch NBOP and search subscriber in the required state', 'Subscriber profile loaded showing the expected state'),
            ('Attempt the operation: %s' % truncate_at_word(scenario_title, 80), 'NBOP displays error/rejection message'),
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
        _op_name = strip_dangling_tail(truncate_at_word(_op_name, 70)) if _op_name else 'the operation'
        steps.append(('Perform %s via NBOP portal' % _op_name, 'Operation completed successfully'))
    steps.append(('Verify subscriber profile reflects the operation result', 'Affected fields show correct post-operation values'))
    steps.append(('Navigate to ≡ Menu → Transaction History, verify entry recorded', 'Transaction logged with correct timestamp and status'))

    # ── Fallback guarantee (Task 2.4) ──
    # Ensure function NEVER returns an empty list — always at least 3 steps
    if not steps:
        steps = [
            ('Launch NBOP and search subscriber by MDN', 'Subscriber profile loaded'),
            ('Navigate to %s' % nav_path, 'Page loads successfully'),
            ('Verify expected content is displayed', 'Page shows correct information'),
        ]
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
        ('Perform the negative action: %s' % truncate_at_word(scenario_title, 100),
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
