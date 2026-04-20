"""
integration_contract.py — Global Integration Knowledge Base
=============================================================
THE SINGLE SOURCE OF TRUTH for:
  1. Which operations exist in the TMO/NSL ecosystem
  2. Which external systems each operation touches
  3. Which systems each operation explicitly does NOT touch
  4. What verification points are required for each operation
  5. What preconditions apply to each operation
  6. What negative/error scenarios are mandatory for each operation

Every module in the pipeline consults this contract:
  - test_engine.py     → step generation, expected results
  - test_analyst.py    → scenario thinking, gap detection
  - scenario_enricher.py → enrichment layers, mandatory assertions
  - step_templates.py  → step chain selection
  - tc_templates.py    → classification, description, preconditions

NO MORE per-feature hardcoding. If a new operation is added,
add it HERE and the entire pipeline picks it up.

Built from analysis of:
  - Chalk specifications (PI-47 through PI-53)
  - Manual test suites (TMO-Syniverse integrated with all APIs)
  - Jira acceptance criteria and subtasks
  - Production incident patterns
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set


# ════════════════════════════════════════════════════════════════════
#  EXTERNAL SYSTEM REGISTRY
# ════════════════════════════════════════════════════════════════════

@dataclass
class ExternalSystem:
    """An external system that NSL integrates with."""
    name: str                    # e.g., 'Syniverse', 'ITMBO', 'EMM'
    description: str             # What it does
    call_types: List[str]        # e.g., ['CreateSubscriber', 'RemoveSubscriber', 'SwapIMSI']
    verify_via: str              # How to verify: 'Century Report', 'KAFKA topic', 'SFTP', etc.
    error_codes: List[str]       # HTTP codes to test: ['401', '403', '404', '500']


EXTERNAL_SYSTEMS: Dict[str, ExternalSystem] = {
    'syniverse': ExternalSystem(
        name='Syniverse',
        description='International roaming and wholesale subscriber management',
        call_types=['CreateSubscriber', 'RemoveSubscriber', 'SwapIMSI'],
        verify_via='Century Report',
        error_codes=['401', '403', '404', '500', '503'],
    ),
    'itmbo': ExternalSystem(
        name='ITMBO',
        description='IT-MBO backend provisioning system',
        call_types=['Provision', 'Deprovision', 'Update', 'Notify'],
        verify_via='Century Report',
        error_codes=['400', '401', '500'],
    ),
    'emm': ExternalSystem(
        name='EMM',
        description='Enterprise Mobility Management',
        call_types=['Notify', 'Provision', 'Deprovision'],
        verify_via='Century Report',
        error_codes=['400', '500'],
    ),
    'apollo_ne': ExternalSystem(
        name='APOLLO_NE',
        description='Network Element gateway to TMO',
        call_types=['Activate', 'Deactivate', 'ChangeSIM', 'ChangeIMEI', 'Hotline',
                    'RemoveHotline', 'Suspend', 'Restore', 'ChangeRateplan', 'ChangeFeature',
                    'ChangeBCD', 'SwapMDN', 'PortIn', 'PortOut', 'ChangeMDN'],
        verify_via='NE Portal / Century Report',
        error_codes=['400', '500', '503'],
    ),
    'tmo': ExternalSystem(
        name='TMO',
        description='T-Mobile carrier backend',
        call_types=['LineEnquiry', 'Activate', 'Deactivate', 'Modify'],
        verify_via='TMO Genesis Portal',
        error_codes=['400', '404', '500'],
    ),
    'connection_manager': ExternalSystem(
        name='Connection Manager',
        description='Device connectivity management',
        call_types=['UpdateDevice', 'SwapDevice'],
        verify_via='Century Report',
        error_codes=['500'],
    ),
    'kafka': ExternalSystem(
        name='KAFKA/BI',
        description='Event streaming for BI and downstream consumers',
        call_types=['PublishEvent', 'StatusUpdate'],
        verify_via='KAFKA topic / BI dashboard',
        error_codes=[],
    ),
    'mediation': ExternalSystem(
        name='Mediation',
        description='CDR/PRR processing pipeline',
        call_types=['ProcessCDR', 'TransformPRR', 'DerivationRule'],
        verify_via='SFTP / PRR output file',
        error_codes=[],
    ),
    'amdocs_sftp': ExternalSystem(
        name='Amdocs SFTP',
        description='SFTP endpoint for PRR file delivery',
        call_types=['DeliverPRR'],
        verify_via='SFTP FileZilla',
        error_codes=[],
    ),
}


# ════════════════════════════════════════════════════════════════════
#  OPERATION CONTRACT — the heart of the system
# ════════════════════════════════════════════════════════════════════

@dataclass
class OperationContract:
    """Defines the integration contract for a single operation type."""
    operation: str                          # e.g., 'Activate Subscriber'
    aliases: List[str]                      # Keywords to match: ['activate', 'activation', 'port-in']
    category: str                           # 'line_state', 'device_change', 'inquiry', 'notification', 'mediation'

    # External system interactions
    must_call: List[str] = field(default_factory=list)      # Systems that MUST be called
    must_not_call: List[str] = field(default_factory=list)   # Systems that MUST NOT be called (explicit negative assertion)
    conditional_call: Dict[str, str] = field(default_factory=dict)  # System → condition, e.g., {'syniverse': 'only for YL with state change'}

    # Syniverse specifics
    syniverse_action: str = ''              # 'CreateSubscriber', 'RemoveSubscriber', 'SwapIMSI', 'NONE'
    syniverse_condition: str = ''           # When Syniverse IS called, e.g., 'YL with active↔hotline↔deactive state change'

    # Verification points (what MUST be checked after the operation)
    verify_points: List[str] = field(default_factory=list)

    # Precondition requirements
    required_line_state: str = ''           # 'active', 'suspended', 'hotlined', 'any'
    required_preconditions: List[str] = field(default_factory=list)

    # Transaction types this operation handles
    transaction_types: List[str] = field(default_factory=list)  # e.g., ['YL', 'YD', 'YM', 'YP', 'PL']

    # Mandatory negative scenarios
    mandatory_negatives: List[str] = field(default_factory=list)

    # Device sensitivity — does Phone vs Tablet matter?
    device_sensitive: bool = False
    sim_sensitive: bool = False


# ════════════════════════════════════════════════════════════════════
#  THE CONTRACT REGISTRY — every operation in the TMO ecosystem
# ════════════════════════════════════════════════════════════════════

OPERATION_CONTRACTS: Dict[str, OperationContract] = {}

def _register(op: OperationContract):
    OPERATION_CONTRACTS[op.operation] = op
    return op


# ── LINE STATE OPERATIONS ──

_register(OperationContract(
    operation='Activate Subscriber',
    aliases=['activate', 'activation', 'activate subscriber', 'psim activation', 'esim activation'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm', 'kafka'],
    syniverse_action='CreateSubscriber',
    syniverse_condition='YL transaction — new subscriber created in Syniverse with IMSI, MDN, wholesale plan',
    verify_points=[
        'Line status = Active in NSL DB',
        'Century Report shows all outbound calls with success',
        'Syniverse CreateSubscriber logged in Century Report',
        'NBOP MIG tables populated (Device, SIM, Line, Feature, Transaction History)',
        'TMO Genesis portal shows active subscriber',
        'KAFKA BI event published with correct payload',
    ],
    required_line_state='new/pending',
    required_preconditions=['Valid IMEI', 'Valid ICCID/IMSI', 'Account exists'],
    transaction_types=['YL'],
    mandatory_negatives=[
        'Already active line — reject duplicate activation',
        'Invalid IMEI — reject with error',
        'Invalid ICCID — reject with error',
        'Upstream TMO failure — graceful handling',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))

_register(OperationContract(
    operation='Deactivate Subscriber',
    aliases=['deactivate', 'deactivation', 'disconnect', 'deactivate subscriber'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm', 'kafka'],
    syniverse_action='RemoveSubscriber',
    syniverse_condition='YL transaction — subscriber removed from Syniverse',
    verify_points=[
        'Line status = Deactivated in NSL DB',
        'Century Report shows Syniverse RemoveSubscriber with success',
        'NBOP MIG tables updated with deactivation',
        'TMO Genesis portal shows deactivated subscriber',
        'Agent details and deactivation reason captured',
    ],
    required_line_state='active',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Already deactivated line — reject',
        'Line with pending port-out — reject',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Enable Hotline',
    aliases=['hotline', 'enable hotline', 'add hotline'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    syniverse_condition='Hotline is internal state change — Syniverse NOT affected',
    verify_points=[
        'Line status = Hotlined in NSL DB',
        'Century Report shows NO Syniverse outbound call',
        'ITMBO and EMM notified of Hotline status change',
        'NBOP reflects hotlined line',
    ],
    required_line_state='active',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Already hotlined MDN — reject',
        'Suspended MDN — reject',
        'Deactivated MDN — reject',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Remove Hotline',
    aliases=['remove hotline', 'dehotline', 'remove_hotline', 'restore from hotline'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    syniverse_condition='Remove Hotline is internal state change — Syniverse NOT affected',
    verify_points=[
        'Line status = Active in NSL DB (restored)',
        'Century Report shows NO Syniverse outbound call',
        'ITMBO and EMM notified of status change back to Active',
        'NBOP reflects active line',
    ],
    required_line_state='hotlined',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Line not in Hotlined state — reject',
        'Deactivated MDN — reject',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Suspend',
    aliases=['suspend', 'restore suspend', 'suspend subscriber'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    syniverse_condition='Suspend is internal state change — Syniverse NOT affected',
    verify_points=[
        'Line status = Suspended in NSL DB',
        'Century Report shows NO Syniverse outbound call',
        'ITMBO and EMM notified',
    ],
    required_line_state='active',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Already suspended MDN — reject',
        'Deactivated MDN — reject',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Restore/Reconnect',
    aliases=['restore', 'reconnect', 'restore suspend', 'reconnect eligibility'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    syniverse_condition='Restore is internal state change — Syniverse NOT affected',
    verify_points=[
        'Line status = Active in NSL DB (restored)',
        'Century Report shows NO Syniverse outbound call',
        'ITMBO and EMM notified',
    ],
    required_line_state='suspended',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Line not in Suspended state — reject',
        'Deactivated MDN — reject',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── DEVICE/SIM CHANGE OPERATIONS ──

_register(OperationContract(
    operation='Change SIM',
    aliases=['change sim', 'change iccid', 'swap sim', 'sim swap'],
    category='device_change',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'connection_manager'],
    syniverse_action='SwapIMSI',
    syniverse_condition='YD transaction with ICCID change on Phone/Tablet — Syniverse SwapIMSI triggered. Wholesale plan UNCHANGED',
    verify_points=[
        'New ICCID/IMSI in NSL DB',
        'Century Report shows Syniverse SwapIMSI (if ICCID changed)',
        'Wholesale plan NOT modified after SwapIMSI',
        'Connection Manager updated',
        'NBOP MIG_SIM table updated',
    ],
    required_line_state='active',
    transaction_types=['YD'],
    mandatory_negatives=[
        'Invalid ICCID format — reject',
        'ICCID already in use — reject',
        'Deactivated MDN — reject',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))

_register(OperationContract(
    operation='Change Device/IMEI',
    aliases=['change imei', 'change device', 'device change', 'change equipment'],
    category='device_change',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'connection_manager'],
    conditional_call={'syniverse': 'Only if ICCID also changes (YD with ICCID change)'},
    verify_points=[
        'New IMEI in NSL DB',
        'Device type updated correctly',
        'Connection Manager updated',
        'NBOP MIG_DEVICE table updated',
    ],
    required_line_state='active',
    transaction_types=['YD'],
    mandatory_negatives=[
        'Invalid IMEI — reject',
        'Incompatible device type — reject',
    ],
    device_sensitive=True,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Swap MDN',
    aliases=['swap mdn', 'swap device', 'mdn swap'],
    category='device_change',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm', 'connection_manager'],
    syniverse_action='SwapIMSI',
    syniverse_condition='YD transaction — SwapIMSI for both lines if ICCID changes',
    verify_points=[
        'Both lines updated in NSL DB',
        'Century Report shows SwapIMSI for each line (if ICCID changed)',
        'Connection Manager updated for both lines',
        'NBOP MIG tables updated for both lines',
        'TMO Genesis shows swapped MDNs',
    ],
    required_line_state='active',
    transaction_types=['YD'],
    mandatory_negatives=[
        'Same MDN on both lines — reject',
        'One line not active — reject',
        'Rollback if second line fails',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))

_register(OperationContract(
    operation='Change SIM Pairing ID',
    aliases=['change sim pairing', 'pairing id', 'sim pass pairing'],
    category='device_change',
    must_call=['apollo_ne', 'tmo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=[
        'Pairing ID updated in NSL DB',
        'Century Report shows no Syniverse call',
    ],
    required_line_state='active',
    transaction_types=['YD'],
    device_sensitive=False,
    sim_sensitive=True,
))

# ── PLAN/FEATURE CHANGE OPERATIONS ──

_register(OperationContract(
    operation='Change Rateplan',
    aliases=['change rateplan', 'change rate plan', 'change plan', 'rate plan change'],
    category='plan_change',
    must_call=['apollo_ne', 'tmo', 'itmbo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    syniverse_condition='YP transaction — only one wholesale plan exists, no Syniverse change needed. Only features can be changed.',
    verify_points=[
        'New plan code in NSL DB',
        'Features added/removed per new plan',
        'Century Report shows no Syniverse call',
        'NBOP MIG_FEATURE table updated',
    ],
    required_line_state='active',
    transaction_types=['YP'],
    mandatory_negatives=[
        'Invalid plan code — reject',
        'Same plan code — reject (no-op)',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Change Feature',
    aliases=['change feature', 'add feature', 'remove feature', 'reset feature', 'toggle feature',
             'scamblock', 'scam block'],
    category='plan_change',
    must_call=['apollo_ne', 'tmo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=[
        'Feature added/removed in NSL DB',
        'Feature compatibility rules enforced',
        'Century Report shows no Syniverse call',
        'NBOP MIG_FEATURE table updated',
    ],
    required_line_state='active',
    transaction_types=['YP'],
    mandatory_negatives=[
        'Incompatible feature — reject',
        'Feature already exists (add) — reject or no-op',
        'Feature does not exist (remove) — reject or no-op',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Change BCD',
    aliases=['change bcd', 'bill cycle date', 'billing cycle', 'reset day'],
    category='plan_change',
    must_call=['apollo_ne', 'tmo', 'itmbo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=[
        'New BCD in NSL DB',
        'Mediation and DPFO updated with new BCD',
        'Century Report shows no Syniverse call',
    ],
    required_line_state='active',
    transaction_types=['YM'],
    mandatory_negatives=[
        'Invalid BCD value — reject',
        'Same BCD — reject (no-op)',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── PORT OPERATIONS ──

_register(OperationContract(
    operation='Port-In',
    aliases=['port-in', 'port in', 'portin', 'port-in inquiry', 'validate portin'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    syniverse_action='CreateSubscriber',
    syniverse_condition='Port-In creates new subscriber in Syniverse (both CP and SP transactionTypes)',
    verify_points=[
        'Port-In order created in NSL DB',
        'Syniverse CreateSubscriber logged',
        'TMO Genesis shows ported-in subscriber',
    ],
    required_line_state='new/pending',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Invalid OSP account — reject',
        'Invalid PIN — reject',
        'MDN not portable — reject',
        'Port-In SP transactionType — verify Syniverse CreateSubscriber',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))

_register(OperationContract(
    operation='Port-Out',
    aliases=['port-out', 'port out', 'portout', 'unsolicited port out'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo', 'emm'],
    syniverse_action='RemoveSubscriber',
    syniverse_condition='Port-Out removes subscriber from Syniverse',
    verify_points=[
        'Line status = Ported Out in NSL DB',
        'Syniverse RemoveSubscriber logged',
    ],
    required_line_state='active',
    transaction_types=['YL'],
    mandatory_negatives=[
        'Cancel port-out — verify reversal',
        'Port-out timeout (50 sec) — verify handling',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Change MDN Port-In',
    aliases=['change mdn port', 'change port-in mdn', 'adapt change port-in'],
    category='line_state',
    must_call=['apollo_ne', 'tmo', 'itmbo'],
    syniverse_action='CreateSubscriber',
    syniverse_condition='New MDN port-in creates subscriber in Syniverse',
    verify_points=[
        'New MDN assigned in NSL DB',
        'Syniverse CreateSubscriber with new MDN',
        'Old MDN released',
    ],
    required_line_state='active',
    transaction_types=['YL'],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── SYNC OPERATIONS ──

_register(OperationContract(
    operation='Sync Subscriber',
    aliases=['sync subscriber', 'sync sub', 'subscriber sync'],
    category='sync',
    must_call=['apollo_ne', 'tmo'],
    conditional_call={
        'syniverse': 'YL with state change (active↔hotline↔deactive) → CreateSubscriber or RemoveSubscriber',
        'itmbo': 'YL/YD/YM/YP if anything changes → notify ITMBO',
        'emm': 'YL/YD/YM/YP if anything changes → notify EMM',
    },
    must_not_call=[],  # Depends on transaction type
    syniverse_action='Conditional',
    syniverse_condition='PL=NONE, YL=CreateSubscriber/RemoveSubscriber, YD=SwapIMSI (if ICCID changes), YP=NONE, YM=NONE',
    verify_points=[
        'Sync completed per transaction type rules',
        'PL: NO external system changes',
        'YL: Syniverse updated if state change, ITMBO/EMM notified',
        'YD: Syniverse SwapIMSI if ICCID changes, wholesale plan unchanged',
        'YP: No wholesale plan changes, only features changed',
        'YM: All other data synced from TMO to NSL',
    ],
    required_line_state='any',
    transaction_types=['PL', 'YL', 'YD', 'YM', 'YP'],
    mandatory_negatives=[
        'PL transaction — verify NO external system changes',
        'Invalid LineId — reject',
        'Invalid AccountId — reject',
        'LineId/AccountId mismatch — reject',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))

# ── INQUIRY OPERATIONS ──

_register(OperationContract(
    operation='Line Inquiry',
    aliases=['line inquiry', 'line info', 'subscriber inquiry'],
    category='inquiry',
    must_call=['tmo'],
    must_not_call=['syniverse', 'itmbo', 'emm'],
    syniverse_action='NONE',
    verify_points=['Correct line data returned', 'No state changes made'],
    required_line_state='any',
    mandatory_negatives=['Invalid MDN — error response', 'Non-existent line — 404'],
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Usage Inquiry',
    aliases=['usage inquiry', 'usage details', 'inquiry usage', 'usage history'],
    category='inquiry',
    must_call=['tmo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=['Usage data returned correctly', 'No state changes made'],
    required_line_state='any',
    device_sensitive=False,
    sim_sensitive=False,
))

_register(OperationContract(
    operation='Order Inquiry',
    aliases=['order inquiry', 'order status', 'get transaction status'],
    category='inquiry',
    must_call=['tmo'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=['Order data returned correctly'],
    required_line_state='any',
    device_sensitive=False,
    sim_sensitive=False,
))

# ── MEDIATION / CDR OPERATIONS ──

_register(OperationContract(
    operation='CDR Processing',
    aliases=['cdr', 'prr', 'mediation', 'usage file', 'ild', 'international roaming',
             'country code', 'country translation', 'call type mapping', 'mhs data usage'],
    category='mediation',
    must_call=['mediation', 'amdocs_sftp'],
    must_not_call=['apollo_ne', 'syniverse', 'itmbo', 'emm'],
    syniverse_action='NONE',
    syniverse_condition='CDR/PRR processing is mediation-only — no API or Syniverse calls',
    verify_points=[
        'PRR file processed through mediation pipeline',
        'Derivation rules applied correctly',
        'PRR output file available on SFTP',
        'Country codes mapped correctly',
        'Call types mapped correctly',
    ],
    required_preconditions=['Mediation and PRR batch jobs running', 'SFTP access available via FileZilla'],
    mandatory_negatives=[
        'Invalid/malformed PRR records — graceful handling',
        'Unrecognized country codes — no incorrect mapping',
        'Empty input data — no crash',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── NOTIFICATION OPERATIONS ──

_register(OperationContract(
    operation='Notification Processing',
    aliases=['notification', 'dpfo', 'data usage notification', '80%', '100%',
             'throttle', 'speed reduction', 'kafka notification'],
    category='notification',
    must_call=['kafka'],
    must_not_call=['syniverse'],
    syniverse_action='NONE',
    verify_points=[
        'Notification sent with correct payload',
        'No duplicate notifications',
        'KAFKA topic updated',
    ],
    mandatory_negatives=[
        'Duplicate notification — suppressed',
        'Inactive subscriber — suppressed',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── UI/PORTAL OPERATIONS ──

_register(OperationContract(
    operation='NBOP Portal',
    aliases=['nbop', 'portal', 'ui', 'screen', 'menu', 'navigation', 'display'],
    category='ui_portal',
    must_call=[],
    must_not_call=['syniverse', 'mediation', 'amdocs_sftp'],
    syniverse_action='NONE',
    verify_points=[
        'Screen loads with all expected fields',
        'Data displayed correctly',
        'NBOP MIG tables reflect correct state',
        'Line table updated',
    ],
    mandatory_negatives=[
        'Invalid MDN input — error message displayed',
        'Non-existent subscriber — appropriate message',
        'Session timeout — graceful handling',
    ],
    device_sensitive=False,
    sim_sensitive=False,
))

# ── SYNIVERSE INTEGRATION (meta-operation for 4152-style features) ──

_register(OperationContract(
    operation='Syniverse Integration',
    aliases=['syniverse integration', 'integration with syniverse', 'syniverse all mvp',
             'syniverse all flows'],
    category='integration',
    must_call=['syniverse', 'apollo_ne', 'tmo'],
    verify_points=[
        'CreateSubscriber for activation/port-in flows',
        'RemoveSubscriber for deactivation/port-out flows',
        'SwapIMSI for ICCID change flows (wholesale plan unchanged)',
        'NO Syniverse call for Hotline/Remove Hotline/Suspend/Restore',
        'NO Syniverse call for Change Rateplan/Change Feature/Change BCD',
        'VZW flows unaffected (regression)',
    ],
    transaction_types=['YL', 'YD', 'YM', 'YP', 'PL'],
    mandatory_negatives=[
        'Syniverse 401 Unauthorized — graceful handling',
        'Syniverse 403 Forbidden — graceful handling',
        'Syniverse 404 Not Found — graceful handling',
        'Syniverse 500 Internal Error — retry/graceful handling',
        'Syniverse timeout — retry/graceful handling',
    ],
    device_sensitive=True,
    sim_sensitive=True,
))


# ════════════════════════════════════════════════════════════════════
#  LOOKUP FUNCTIONS — used by all modules
# ════════════════════════════════════════════════════════════════════

def resolve_operation(feature_name: str, description: str = '',
                      ac_text: str = '', scope: str = '') -> Optional[OperationContract]:
    """Resolve a feature to its operation contract by matching aliases.
    Returns the BEST matching contract, or None if no match."""
    ctx = (feature_name + ' ' + description + ' ' + ac_text + ' ' + scope).lower()

    best_match = None
    best_score = 0

    for op in OPERATION_CONTRACTS.values():
        score = 0
        for alias in op.aliases:
            if alias in ctx:
                # Longer alias = more specific = higher score
                score += len(alias)
        if score > best_score:
            best_score = score
            best_match = op

    return best_match


def resolve_all_operations(feature_name: str, description: str = '',
                           ac_text: str = '', scope: str = '') -> List[OperationContract]:
    """Resolve ALL matching operation contracts for a feature.
    Used for integration features (like 4152) that span multiple operations."""
    ctx = (feature_name + ' ' + description + ' ' + ac_text + ' ' + scope).lower()
    matches = []

    for op in OPERATION_CONTRACTS.values():
        for alias in op.aliases:
            if alias in ctx:
                matches.append(op)
                break

    return matches


def get_must_call_systems(contract: OperationContract) -> List[ExternalSystem]:
    """Get the ExternalSystem objects that MUST be called for this operation."""
    return [EXTERNAL_SYSTEMS[s] for s in contract.must_call if s in EXTERNAL_SYSTEMS]


def get_must_not_call_systems(contract: OperationContract) -> List[ExternalSystem]:
    """Get the ExternalSystem objects that MUST NOT be called for this operation."""
    return [EXTERNAL_SYSTEMS[s] for s in contract.must_not_call if s in EXTERNAL_SYSTEMS]


def get_verify_steps(contract: OperationContract) -> List[str]:
    """Get the verification points for this operation."""
    return contract.verify_points


def get_mandatory_negatives(contract: OperationContract) -> List[str]:
    """Get the mandatory negative scenarios for this operation."""
    return contract.mandatory_negatives


def get_syniverse_assertion(contract: OperationContract) -> dict:
    """Get the Syniverse assertion for this operation.
    Returns {'action': 'CreateSubscriber'|'NONE'|..., 'assert_type': 'MUST_CALL'|'MUST_NOT_CALL'|'CONDITIONAL', 'condition': '...'}"""
    if contract.syniverse_action == 'NONE' or 'syniverse' in contract.must_not_call:
        return {
            'action': 'NONE',
            'assert_type': 'MUST_NOT_CALL',
            'condition': contract.syniverse_condition or 'This operation does not affect Syniverse',
        }
    elif contract.syniverse_action == 'Conditional':
        return {
            'action': 'Conditional',
            'assert_type': 'CONDITIONAL',
            'condition': contract.syniverse_condition,
        }
    else:
        return {
            'action': contract.syniverse_action,
            'assert_type': 'MUST_CALL',
            'condition': contract.syniverse_condition,
        }


def get_all_operations() -> List[OperationContract]:
    """Get all registered operation contracts."""
    return list(OPERATION_CONTRACTS.values())


def get_operations_by_category(category: str) -> List[OperationContract]:
    """Get all operations in a category."""
    return [op for op in OPERATION_CONTRACTS.values() if op.category == category]
