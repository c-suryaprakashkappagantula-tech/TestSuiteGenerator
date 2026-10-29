"""Unit tests for modules/analyst_derivation.derive_analyst_scenarios (FEAT-001).

Cache-free and LLM-free: tiny jira/chalk stubs carry the REAL AC/Chalk anchors from
plan.md for MWTGPROV-4575 / 4503 / 4533. Each deriver must emit the expected kind with the
correct AC-<Kind>- source_id prefix and an extracted_text that points back into the stub
evidence, and the derivation must be deterministic (same input -> identical ordered titles).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.analyst_derivation import derive_analyst_scenarios  # noqa: E402


class _Jira:
    def __init__(self, key, summary, description='', acceptance_criteria=''):
        self.key = key
        self.summary = summary
        self.description = description
        self.acceptance_criteria = acceptance_criteria


class _Chalk:
    """Minimal Chalk stub. _chalk_obligation_text reads feature_title/scope/rules and
    skips tables when owner-attribution helpers are unavailable, so scope/rules carry the
    anchors here."""
    def __init__(self, feature_title='', scope='', rules=''):
        self.feature_title = feature_title
        self.scope = scope
        self.rules = rules
        self.scenarios = []
        self.tables = []


def _ids(scenarios):
    return [s.source.source_id for s in scenarios]


def _titles(scenarios):
    return [s.title for s in scenarios]


def _kinds_present(scenarios):
    return {s.source.source_id.rsplit('-', 1)[0] for s in scenarios}


def _q(*_a, **_k):
    pass


# ── MWTGPROV-4575: negative + alternate-flow anchors ──────────────────────────────

_J4575 = _Jira(
    'MWTGPROV-4575',
    'TMO - Resend OTA Notification to Device',
    description='Resend the OTA notification to a device.',
    acceptance_criteria=(
        'An authorized agent can resend the OTA notification to a device by entering '
        'an active MDN or ICCID\n'
        'Only active MDN/ICCID is accepted - inactive or invalid inputs are rejected\n'
        'Access is restricted to authorized users\n'
        'Behavior matches the TMO Genesys portal parity.'
    ),
)
_C4575 = _Chalk(
    feature_title='Resend OTA Notification',
    scope='A device whose status is not ACTIVE - e.g. disconnected or suspended/hotlined '
          'is rejected. Inputs enumerated: MDN, ICCID.',
)


def test_4575_negative_kind_emitted_with_pointer():
    out = derive_analyst_scenarios(_J4575, _C4575, 'api', existing_titles=[], log=_q)
    negs = [s for s in out if s.source.source_id.startswith('AC-Negative-')]
    assert negs, 'expected AC-Negative scenarios for 4575'
    for s in negs:
        assert s.source.source_id == 'AC-Negative-MWTGPROV-4575'
        assert s.source.extracted_text, 'negative scenario must carry a pointer'
        assert s.category == 'Negative'
    # The "inactive or invalid inputs are rejected" AC line is a negative anchor.
    joined = '\n'.join(s.source.extracted_text.lower() for s in negs)
    assert 'rejected' in joined or 'restricted' in joined


def test_4575_alt_flow_enumerates_mdn_and_iccid():
    out = derive_analyst_scenarios(_J4575, _C4575, 'api', existing_titles=[], log=_q)
    alts = [s for s in out if s.source.source_id.startswith('AC-AltFlow-')]
    titles = ' '.join(_titles(alts)).lower()
    assert 'mdn' in titles and 'iccid' in titles, \
        'alternate-flow should enumerate the MDN and ICCID inputs'
    for s in alts:
        assert s.source.source_id == 'AC-AltFlow-MWTGPROV-4575'


# ── MWTGPROV-4503: boundary + regression anchors ──────────────────────────────────

_J4503 = _Jira(
    'MWTGPROV-4503',
    'Handle Non-Numeric Data Types in TMO PRR Usage Files',
    acceptance_criteria=(
        'NSL successfully processes 3289 and 3290 files containing IP address data '
        'without rejection.\n'
        'Existing file processing behavior for records without IP address remains unchanged'
    ),
)
_C4503 = _Chalk(
    feature_title='PRR non-numeric handling',
    rules='session_ip_addr stored as VARCHAR(45). rating_group_code VARCHAR(20). '
          'data_volume_ctr widened to 16 significant digits (was NUMERIC(15) overflow -> drop). '
          'File series 3289, 3290, 8886. Verizon PRR processing is unchanged (regression).',
)


def test_4503_boundary_mines_varchar_and_numeric():
    out = derive_analyst_scenarios(_J4503, _C4503, 'batch_file', existing_titles=[], log=_q)
    bounds = [s for s in out if s.source.source_id.startswith('AC-Boundary-')]
    assert bounds, 'expected AC-Boundary scenarios mined from VARCHAR/NUMERIC'
    joined = ' '.join(_titles(bounds))
    assert 'VARCHAR(45)' in joined or 'VARCHAR(20)' in joined or 'NUMERIC(15)' in joined
    for s in bounds:
        assert s.source.source_id == 'AC-Boundary-MWTGPROV-4503'
        assert s.source.extracted_text


def test_4503_regression_kind_from_remains_unchanged():
    out = derive_analyst_scenarios(_J4503, _C4503, 'batch_file', existing_titles=[], log=_q)
    regs = [s for s in out if s.source.source_id.startswith('AC-Regression-')]
    assert regs, 'expected an AC-Regression scenario from "remains unchanged"/"unchanged"'
    for s in regs:
        assert s.source.source_id == 'AC-Regression-MWTGPROV-4503'
        assert s.category == 'Regression'
    # At least one regression traces to the no-impact anchor ("unchanged"/"regression").
    joined = '\n'.join(s.source.extracted_text.lower() for s in regs)
    assert 'unchanged' in joined or 'regression' in joined


def test_4503_alt_flow_file_series():
    out = derive_analyst_scenarios(_J4503, _C4503, 'batch_file', existing_titles=[], log=_q)
    alts = [s for s in out if s.source.source_id.startswith('AC-AltFlow-')]
    titles = ' '.join(_titles(alts))
    # File series {3289, 3290, 8886} enumerated in the chalk rules.
    assert '3289' in titles and '3290' in titles


# ── MWTGPROV-4533: negative + alternate-flow + regression anchors ─────────────────

_J4533 = _Jira(
    'MWTGPROV-4533',
    'Fix E3812 error when syncing PSIM device on eSIM line',
    acceptance_criteria=(
        'Sync Device/SIM succeeds when PSIM device replaces eSIM on the line.\n'
        'Swap MDN (AM) does not fail with E3812 when IMEI-2 has a PSIM.\n'
        'Existing same-SIM-type Sync and Swap behavior is unaffected.'
    ),
)
_C4533 = _Chalk(feature_title='Sync/Swap SIM fix', scope='Operations: Sync Device/SIM (YD), '
                 'Swap MDN (AM). SIM types: eSIM, pSIM.')


def test_4533_negative_from_error_code_line():
    out = derive_analyst_scenarios(_J4533, _C4533, 'cr_bug_fix', existing_titles=[], log=_q)
    negs = [s for s in out if s.source.source_id.startswith('AC-Negative-')]
    assert negs
    joined = '\n'.join(s.source.extracted_text for s in negs)
    assert 'E3812' in joined, 'the E3812 error-code line should seed a negative scenario'


def test_4533_alt_flow_sim_types():
    out = derive_analyst_scenarios(_J4533, _C4533, 'cr_bug_fix', existing_titles=[], log=_q)
    alts = [s for s in out if s.source.source_id.startswith('AC-AltFlow-')]
    titles = ' '.join(_titles(alts)).lower()
    assert 'esim' in titles and 'psim' in titles, 'alt-flow should enumerate eSIM and pSIM'


def test_4533_regression_unaffected():
    out = derive_analyst_scenarios(_J4533, _C4533, 'cr_bug_fix', existing_titles=[], log=_q)
    regs = [s for s in out if s.source.source_id.startswith('AC-Regression-')]
    assert regs, 'expected a regression scenario from "unaffected"'


# ── Cross-cutting contract: allowed source_type, dedup, determinism, cap ──────────

def test_source_type_is_allowed():
    from modules.traceability import ALLOWED_SOURCE_TYPES
    for jira, chalk in ((_J4575, _C4575), (_J4503, _C4503), (_J4533, _C4533)):
        for s in derive_analyst_scenarios(jira, chalk, existing_titles=[], log=_q):
            assert s.source.source_type in ALLOWED_SOURCE_TYPES


def test_existing_titles_are_deduped():
    first = derive_analyst_scenarios(_J4575, _C4575, 'api', existing_titles=[], log=_q)
    assert first
    seed = [first[0].title]
    second = derive_analyst_scenarios(_J4575, _C4575, 'api', existing_titles=seed, log=_q)
    assert first[0].title not in _titles(second), \
        'a title present in existing_titles must not be re-emitted'


def test_determinism_same_input_same_ordered_titles():
    a = derive_analyst_scenarios(_J4503, _C4503, 'batch_file', existing_titles=[], log=_q)
    b = derive_analyst_scenarios(_J4503, _C4503, 'batch_file', existing_titles=[], log=_q)
    assert _titles(a) == _titles(b)
    assert _ids(a) == _ids(b)


def test_total_capped_at_12():
    for jira, chalk in ((_J4575, _C4575), (_J4503, _C4503), (_J4533, _C4533)):
        out = derive_analyst_scenarios(jira, chalk, existing_titles=[], log=_q)
        assert len(out) <= 12
