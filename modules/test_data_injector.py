# -*- coding: utf-8 -*-
"""
test_data_injector.py — Real test data injection for V8.0 test suites.

Provides real SIT MDN/ICCID/IMEI values so generated test steps say:
  "Send POST /nsl/provisioning/... with MDN=3036694392, lineId=5634541190"
instead of:
  "Send POST ... with valid test data"

Data sources (priority order):
  1. test_data_pool table in tsg_cache.db (seeded from previous runs / manual)
  2. NMNO API spec request_sample (JSON from captured traffic)
  3. SIT hardcoded sample values (always available fallback)

Usage:
    from modules.test_data_injector import get_sample_data, seed_from_nmno, SIT_SAMPLES

    sample = get_sample_data('MDN', environment='SIT')
    # Returns e.g. {'type': 'MDN', 'value': '3036694392', 'source': 'pool'}

    # Seed DB from NMNO request_sample JSON
    seed_from_nmno(nmno_result, feature_id='MWTGPROV-4020')
"""
import json
import re
from typing import Dict, List, Optional, Any


# ── Protected-identifier gate ──
# Every value this module hands out is checked against the shared do-not-touch
# registry first. The pools below are not the only source: values also arrive from
# the cached `test_data_pool` table and from NMNO captured traffic, so the check is
# applied at the point a value leaves this module rather than only at the literals.
# Fails closed — see modules/protected_gate.py.

def _assert_safe_value(value, where: str) -> None:
    """Raise if a single value about to be injected is protected."""
    if value is None or str(value).strip() == '':
        return
    from .protected_gate import assert_mapping_safe
    assert_mapping_safe({'value': value}, where)


def _assert_safe_mapping(mapping: Dict[str, str], where: str) -> Dict[str, str]:
    """Raise if any value in a request-sample dict is protected; else return it."""
    from .protected_gate import assert_mapping_safe
    return assert_mapping_safe(mapping, where)


def _is_protected_quiet(value) -> bool:
    """True if ``value`` is protected, for the pool seeders.

    Returns True when the registry cannot be loaded as well: 'unknown' is treated
    as 'do not store it'. Fail-closed here costs nothing (the pool simply stays
    unseeded and generation falls back to SIT_SAMPLES), whereas fail-open would
    let a real identifier be cached for every future run.
    """
    try:
        from .protected_gate import is_protected
        return bool(is_protected(value))
    except Exception:
        return True


# ── SIT fallback samples (always available) ──
SIT_SAMPLES = {
    'MDN':      ['3036694392', '7206814569', '5551112345', '7203339999'],
    'IMEI':     ['351605722757490', '867123456789012', '490154203237518'],
    'ICCID':    ['8901240394150627188', '8901240100000123456', '89012402100000987'],
    'IMSI':     ['310240395062718', '310240100012345', '310240200099876'],
    'EID':      ['89049032004008882600001234567890', '89049032004008882600009876543210'],
    'LINE_ID':  ['5634541190', '5634541233', '5634541300'],
    'ACCOUNT':  ['100456789', '100987654', '101234567'],
}

# Operation → typical request fields with sample values
OPERATION_SAMPLES: Dict[str, Dict[str, str]] = {
    'reset-plan': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'RequestType': 'TMO',
        'accountNumber': '100456789',
    },
    'activate': {
        'IMEI': '351605722757490',
        'ICCID': '8901240394150627188',
        'MDN': '3036694392',
        'accountNumber': '100456789',
        'deviceType': 'MOBILE',
        'simType': 'ESIM',
    },
    'deactivate': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'RequestType': 'TMO',
    },
    'hotline': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'RequestType': 'TMO',
    },
    'suspend': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'RequestType': 'TMO',
    },
    'restore': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'RequestType': 'TMO',
    },
    'change-sim': {
        'MDN': '3036694392',
        'ICCID': '8901240394150627188',
        'lineId': '5634541190',
        'RequestType': 'TMO',
    },
    'change-rateplan': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'ratePlanCode': 'CMUNL',
        'RequestType': 'TMO',
    },
    'change-bcd': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'billCycleDay': '15',
        'RequestType': 'TMO',
    },
    'swap-mdn': {
        'MDN_A': '3036694392',
        'MDN_B': '7206814569',
        'lineId_A': '5634541190',
        'lineId_B': '5634541233',
        'RequestType': 'TMO',
    },
    'port-in': {
        'IMEI': '351605722757490',
        'ICCID': '8901240394150627188',
        'MDN': '3036694392',
        'accountNumber': '100456789',
        'RequestType': 'TMO',
    },
    'sync-subscriber': {
        'MDN': '3036694392',
        'lineId': '5634541190',
        'accountNumber': '100456789',
    },
    'retrieve-device': {
        'IMEI': '351605722757490',
        'MDN': '3036694392',
    },
    'line-inquiry': {
        'MDN': '3036694392',
        'lineId': '5634541190',
    },
}


def get_sample_data(data_type: str, environment: str = 'SIT') -> Dict[str, str]:
    """Get a sample value for a specific data type.

    Checks: test_data_pool DB → SIT fallback samples.

    Args:
        data_type: 'MDN', 'IMEI', 'ICCID', etc.
        environment: 'SIT' or 'UAT'

    Returns:
        {'type': data_type, 'value': '...', 'source': 'pool'|'fallback'}
    """
    # Try DB pool first.
    # NOTE: the pool lookup is wrapped in a broad except (a missing/locked cache DB
    # must not stop generation), so the protected check is deliberately OUTSIDE it.
    # Inside, a ProtectedEntityError would be swallowed by `except Exception: pass`
    # and the identifier would be handed out anyway.
    result = None
    try:
        from .database import get_test_data
        rows = get_test_data(data_type.upper(), environment=environment)
        if rows:
            result = {
                'type': data_type,
                'value': rows[0]['value'],
                'source': 'pool',
                'id': rows[0].get('id'),
            }
    except Exception:
        result = None

    if result is None:
        # Fallback to hardcoded SIT samples
        samples = SIT_SAMPLES.get(data_type.upper(), [])
        if samples:
            result = {'type': data_type, 'value': samples[0], 'source': 'fallback'}
        else:
            result = {'type': data_type, 'value': '<test_%s>' % data_type.lower(),
                      'source': 'placeholder'}

    _assert_safe_value(result['value'],
                       'test_data_injector.get_sample_data(%s, source=%s)'
                       % (data_type, result.get('source', '?')))
    return result


def get_operation_sample_request(
    api_name: str,
    endpoint: str = '',
    request_fields: List = None,
    nmno_request_sample: str = '',
) -> Dict[str, str]:
    """Get a sample request payload for an operation.

    Priority:
      1. NMNO request_sample JSON (from captured traffic — most accurate)
      2. OPERATION_SAMPLES dict (operation-level hardcoded)
      3. test_data_pool per-field lookup
      4. Generic SIT fallback values

    Returns:
        Dict of {field_name: sample_value} for the request payload.
    """
    result = {}

    # 1. Try NMNO request_sample (captured JSON)
    if nmno_request_sample:
        try:
            raw = nmno_request_sample.strip()
            # Find JSON object in the sample
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1:
                sample_data = json.loads(raw[start:end + 1])
                # Replace placeholder values with real SIT data
                for key, val in sample_data.items():
                    key_lower = key.lower()
                    str_val = str(val)
                    if 'mdn' in key_lower or 'msisdn' in key_lower:
                        result[key] = get_sample_data('MDN')['value']
                    elif 'imei' in key_lower:
                        result[key] = get_sample_data('IMEI')['value']
                    elif 'iccid' in key_lower or 'sim' in key_lower:
                        result[key] = get_sample_data('ICCID')['value']
                    elif 'imsi' in key_lower:
                        result[key] = get_sample_data('IMSI')['value']
                    elif 'lineid' in key_lower or 'line_id' in key_lower:
                        result[key] = get_sample_data('LINE_ID')['value']
                    elif 'account' in key_lower:
                        result[key] = get_sample_data('ACCOUNT')['value']
                    elif str_val and str_val not in ('null', 'None', ''):
                        result[key] = '<sample_%s>' % key.lower()[:20]  # mask unrecognised fields — PII safety
                if result:
                    return _assert_safe_mapping(
                        result,
                        'test_data_injector.get_operation_sample_request'
                        '(nmno_sample, api=%s)' % (api_name or '?'))
        except Exception as _exc:
            # Malformed captured JSON is expected and ignored, but a protected
            # identifier (or an unloadable registry) must not be swallowed here.
            if type(_exc).__name__ in ('ProtectedEntityError',
                                       'ProtectedRegistryUnavailable'):
                raise

    # 2. Try operation name match in OPERATION_SAMPLES
    api_name_lower = (api_name or '').lower().replace('_', '-').replace(' ', '-')
    endpoint_lower = (endpoint or '').lower()
    for op_key, op_sample in OPERATION_SAMPLES.items():
        if op_key in api_name_lower or op_key in endpoint_lower:
            # These are module-level literals, not routed through get_sample_data,
            # so this is the only place they are checked.
            return _assert_safe_mapping(
                dict(op_sample),
                'test_data_injector.OPERATION_SAMPLES[%s]' % op_key)

    # 3. Build from request_fields list
    if request_fields:
        for field_item in request_fields:
            # field_item can be str or dict
            if isinstance(field_item, dict):
                field_name = field_item.get('name', '') or field_item.get('field', '')
                field_type = field_item.get('type', '')
            else:
                field_name = str(field_item)
                field_type = ''

            if not field_name:
                continue

            fn_lower = field_name.lower()
            if 'mdn' in fn_lower or 'msisdn' in fn_lower:
                result[field_name] = get_sample_data('MDN')['value']
            elif 'imei' in fn_lower:
                result[field_name] = get_sample_data('IMEI')['value']
            elif 'iccid' in fn_lower:
                result[field_name] = get_sample_data('ICCID')['value']
            elif 'imsi' in fn_lower:
                result[field_name] = get_sample_data('IMSI')['value']
            elif 'lineid' in fn_lower or 'line_id' in fn_lower:
                result[field_name] = get_sample_data('LINE_ID')['value']
            elif 'account' in fn_lower:
                result[field_name] = get_sample_data('ACCOUNT')['value']
            elif 'requesttype' in fn_lower or 'request_type' in fn_lower:
                result[field_name] = 'TMO'
            elif 'token' in fn_lower or 'auth' in fn_lower:
                result[field_name] = 'Bearer <oauth_token>'
            else:
                result[field_name] = '<test_%s>' % field_name.lower()[:20]

    # 4. Absolute fallback — generic SIT values
    if not result:
        result = {
            'MDN': get_sample_data('MDN')['value'],
            'lineId': get_sample_data('LINE_ID')['value'],
            'RequestType': 'TMO',
        }

    return _assert_safe_mapping(
        result,
        'test_data_injector.get_operation_sample_request(api=%s)' % (api_name or '?'))


def format_request_sample(sample: Dict[str, str], max_fields: int = 6) -> str:
    """Format a request sample dict as a readable string for test steps.

    e.g. "MDN=3036694392, lineId=5634541190, RequestType=TMO"
    """
    if not sample:
        return 'valid SIT test data'
    items = list(sample.items())[:max_fields]
    return ', '.join('%s=%s' % (k, v) for k, v in items)


def seed_from_nmno(nmno_result, feature_id: str = '') -> int:
    """Seed test_data_pool from NMNO API spec request_sample JSON.

    Extracts MDN/IMEI/ICCID values from captured request JSON and stores
    them in the pool for future test data injection.

    Returns: number of values seeded.
    """
    if not nmno_result:
        return 0

    seeded = 0
    try:
        from .database import add_test_data
        for spec in (nmno_result.api_specs or []):
            sample = getattr(spec, 'request_sample', '') or ''
            if not sample:
                continue
            try:
                start = sample.find('{')
                end = sample.rfind('}')
                if start == -1 or end == -1:
                    continue
                data = json.loads(sample[start:end + 1])
                for key, val in data.items():
                    if not val or str(val) in ('null', 'None', ''):
                        continue
                    str_val = str(val)
                    key_lower = key.lower()
                    # Captured traffic is the most likely way a real identifier
                    # enters the pool. Skip rather than raise: this is an upstream
                    # side-effect, and refusing to store it keeps the pool clean
                    # without failing a run that never uses the value. If one ever
                    # does reach a suite, generate_excel() still fails the run.
                    if _is_protected_quiet(str_val):
                        print('[PROTECTED] not seeding %s from NMNO/%s — '
                              'registered do-not-touch'
                              % (key, feature_id or 'unknown'))
                        continue
                    if re.match(r'^\d{10}$', str_val) and ('mdn' in key_lower or 'msisdn' in key_lower):
                        add_test_data('MDN', str_val, environment='SIT',
                                     notes='From NMNO/%s' % (feature_id or spec.api_name or ''))
                        seeded += 1
                    elif re.match(r'^\d{15}$', str_val) and 'imei' in key_lower:
                        add_test_data('IMEI', str_val, environment='SIT',
                                     notes='From NMNO/%s' % (feature_id or spec.api_name or ''))
                        seeded += 1
                    elif re.match(r'^\d{19,20}$', str_val) and 'iccid' in key_lower and str_val.startswith('89'):
                        # ICCID (E.118 format starts with '89')
                        add_test_data('ICCID', str_val, environment='SIT',
                                     notes='From NMNO/%s' % (feature_id or spec.api_name or ''))
                        seeded += 1
            except Exception:
                continue
    except Exception:
        pass

    return seeded


def seed_sit_defaults() -> int:
    """Seed the test_data_pool with default SIT sample values if pool is empty.

    Called once at startup / first run. These are placeholder values that will
    be overridden when real NMNO data is available.

    Returns: number of values seeded.
    """
    seeded = 0
    try:
        from .database import add_test_data, get_test_data
        # Only seed if pool is empty
        existing = get_test_data('MDN', environment='SIT')
        if existing:
            return 0  # Already seeded

        for data_type, values in SIT_SAMPLES.items():
            for val in values[:2]:  # seed first 2 of each type
                if _is_protected_quiet(val):
                    print('[PROTECTED] not seeding %s %s — registered do-not-touch'
                          % (data_type, val))
                    continue
                add_test_data(data_type, val, environment='SIT', notes='SIT default')
                seeded += 1
    except Exception:
        pass
    return seeded


def get_varied_test_data(
    tc_index: int,
    api_name: str = '',
    category: str = '',
    dimension_values: Dict = None,
) -> str:
    """Generate varied test data per TC to avoid identical MDN/lineId across all TCs.

    Rotates through available SIT sample pools based on tc_index and adds
    context-specific fields based on category and dimensions.

    Args:
        tc_index: 0-based index of the TC in the suite (used for rotation)
        api_name: operation/api name for context
        category: TC category (Happy Path, Negative, Edge Case)
        dimension_values: dict of dimension key→value for this TC

    Returns:
        Formatted string like "MDN=7206814569, lineId=5634541233, RequestType=TMO"
    """
    dimension_values = dimension_values or {}

    # Rotate through SIT_SAMPLES using tc_index
    mdns = SIT_SAMPLES.get('MDN', ['3036694392'])
    line_ids = SIT_SAMPLES.get('LINE_ID', ['5634541190'])
    accounts = SIT_SAMPLES.get('ACCOUNT', ['100456789'])

    mdn = mdns[tc_index % len(mdns)]
    line_id = line_ids[tc_index % len(line_ids)]
    account = accounts[tc_index % len(accounts)]

    # Rotation reads SIT_SAMPLES directly rather than via get_sample_data(), so the
    # gate is applied here too. `account` is included even though it is not emitted
    # below, so a protected account number is reported rather than silently unused.
    _assert_safe_mapping({'MDN': mdn, 'lineId': line_id, 'accountNumber': account},
                         'test_data_injector.get_varied_test_data(tc_index=%d)' % tc_index)

    # Base fields
    fields = {
        'MDN': mdn,
        'lineId': line_id,
        'RequestType': 'TMO',
    }

    # NOTE: Context fields (product, plan_type, line_state, device_type, channel)
    # are NOT added to the request payload fields. They belong in description/preconditions only.
    # See BUG-TDI-1: context fields must stay separate from API request body fields.

    # Format output — only the 3 core request fields
    items = list(fields.items())
    return ', '.join('%s=%s' % (k, v) for k, v in items)
