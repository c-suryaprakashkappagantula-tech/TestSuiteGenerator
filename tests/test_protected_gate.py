"""Generation is gated on the shared do-not-touch registry.

Guards tsg-tse-hardening Requirement 1. TSE has refused to *execute* against a protected
identifier for a while, but nothing stopped TSG from *generating* a suite carrying one - the
control sat downstream of where the identifier enters the system, and a tester reading the
suite would dial the number.

The registry entry is faked through `sys.modules` rather than written to the real
`shared/protected_registry/protected_entities.db`: these tests must not mutate a database
that every other dashboard consults. Matching still runs the registry's own `_normalize` and
`_match_variants`, so the country-code tolerance under test is the real implementation and
not a restatement of it.
"""
import glob
import os
import sys
import types

import pytest

from modules.test_engine import TestCase, TestStep, TestSuite

REAL_REGISTRY = 'shared.protected_registry'

# A value that is not in the real registry, used as the "protected" one throughout.
FAKE_PROTECTED_MDN = '9995550143'


def _real_registry_module():
    """Import the real registry, skipping the test if it is genuinely absent."""
    from modules.protected_gate import _bootstrap_sys_path
    _bootstrap_sys_path()
    try:
        import shared.protected_registry as reg
        return reg
    except ImportError:                                    # pragma: no cover
        pytest.skip('the real protected_registry is not importable')


def fake_registry(protected=()):
    """A stand-in registry holding `protected` in memory.

    Reuses the real `_normalize` / `_match_variants` so digit formatting and the US
    country-code equivalence behave exactly as in production.
    """
    real = _real_registry_module()
    from shared.protected_registry.registry import _match_variants, _normalize

    blocked = {}
    for value in protected:
        norm = _normalize(value)
        for variant in _match_variants(norm):
            blocked[variant] = norm

    module = types.ModuleType(REAL_REGISTRY)
    module.ProtectedEntityError = real.ProtectedEntityError
    module.ProtectedEntity = real.ProtectedEntity

    def find_protected(value):
        norm = _normalize(value)
        for variant in _match_variants(norm):
            if variant in blocked:
                return real.ProtectedEntity(
                    entity_type='MDN', value=blocked[variant],
                    value_norm=blocked[variant], reason='TEST - do not touch')
        return None

    def check_identifiers(**identifiers):
        hits, seen = [], set()
        for value in identifiers.values():
            if value is None or str(value).strip() == '':
                continue
            rec = find_protected(value)
            if rec and rec.value_norm not in seen:
                seen.add(rec.value_norm)
                hits.append(rec)
        return hits

    def assert_safe(context='', logger_fn=None, **identifiers):
        hits = check_identifiers(**identifiers)
        if hits:
            raise real.ProtectedEntityError(hits, context=context)

    module.find_protected = find_protected
    module.check_identifiers = check_identifiers
    module.assert_safe = assert_safe
    module.is_protected = lambda value: find_protected(value) is not None
    module.filter_safe = lambda values: [v for v in values if find_protected(v) is None]
    return module


@pytest.fixture
def with_protected(monkeypatch):
    """Install a fake registry holding the given values."""
    def _install(*values):
        monkeypatch.setitem(sys.modules, REAL_REGISTRY, fake_registry(values))
    return _install


def suite_carrying(value, where='step'):
    """A minimal suite with `value` planted in one of its text fields."""
    step_text = 'Send POST /nsl/provisioning/activate with MDN=%s, RequestType=TMO' % (
        value if where == 'step' else '3036694392')
    tc = TestCase(
        sno='TC01',
        summary='Verify activation succeeds',
        description='Activate a line' if where != 'description' else 'Use MDN=%s' % value,
        preconditions='Line exists' if where != 'preconditions' else 'MDN %s is active' % value,
        steps=[TestStep(1, step_text, 'HTTP 200 returned')],
    )
    return TestSuite(feature_id='MWTGPROV-GATE', feature_title='Gate probe',
                     test_cases=[tc])


# ═════════════════════════════════════════════════════════════════════════════
#  The output chokepoint  (Req 1.1, 1.2)
# ═════════════════════════════════════════════════════════════════════════════
class TestOutputIsGated:

    def test_protected_identifier_stops_the_write_and_leaves_no_file(
            self, with_protected, tmp_path):
        """Req 1.2: no Excel, no Feature Summary, no DB row, no transaction-log entry.

        generate_excel is the single function every write path calls - the pipeline, all
        the dashboards and the dry-run scripts - and in `block_generate_output` the doc,
        the DB save and the transaction log all follow it. Raising here stops all four.
        """
        from modules.excel_generator import generate_excel
        with_protected(FAKE_PROTECTED_MDN)

        before = set(glob.glob(os.path.join('outputs', '*')))
        with pytest.raises(Exception) as err:
            generate_excel(suite_carrying(FAKE_PROTECTED_MDN), log=lambda m: None)

        assert type(err.value).__name__ == 'ProtectedEntityError'
        assert FAKE_PROTECTED_MDN in str(err.value), 'the identifier must be named'
        assert set(glob.glob(os.path.join('outputs', '*'))) == before, (
            'a refused run must not leave a file behind')

    def test_a_clean_suite_is_not_blocked(self, with_protected):
        """The gate must not disturb the normal path."""
        from modules.protected_gate import assert_suite_safe, scan_suite
        with_protected(FAKE_PROTECTED_MDN)

        clean = suite_carrying('3036694392')
        assert scan_suite(clean) == []
        assert_suite_safe(clean, context='test')          # must not raise

    @pytest.mark.parametrize('where', ['step', 'description', 'preconditions'])
    def test_it_is_caught_wherever_it_sits(self, with_protected, where):
        """Scanning the generated text, not just the seed pools, is what makes this hold.

        A value can arrive from the cached test_data_pool, from NMNO captured traffic or
        from the Jira body, so checking only the hardcoded seeds would miss it.
        """
        from modules.protected_gate import scan_suite
        with_protected(FAKE_PROTECTED_MDN)

        found = scan_suite(suite_carrying(FAKE_PROTECTED_MDN, where=where))
        assert len(found) == 1, 'not caught in %s' % where
        assert found[0]['value'] == FAKE_PROTECTED_MDN

    def test_a_country_code_variant_is_caught(self, with_protected):
        """1XXXXXXXXXX must not slip past an entry filed as XXXXXXXXXX."""
        from modules.protected_gate import scan_suite
        with_protected(FAKE_PROTECTED_MDN)

        found = scan_suite(suite_carrying('1' + FAKE_PROTECTED_MDN))
        assert len(found) == 1

    def test_report_style_digits_are_not_false_positives(self, with_protected):
        """Bill cycle days, HTTP codes and dates must not be treated as identifiers."""
        from modules.protected_gate import scan_suite
        with_protected(FAKE_PROTECTED_MDN)

        tc = TestCase(sno='TC01', summary='Verify BCD change to 15',
                      description='Expect HTTP 200 on 2026-08-19 for account 100456789',
                      steps=[TestStep(1, 'Set billCycleDay=15, retry after 30 seconds',
                                      'HTTP 200')])
        assert scan_suite(TestSuite(feature_id='X', test_cases=[tc])) == []


# ═════════════════════════════════════════════════════════════════════════════
#  Identifier type coverage  (Req 1.3)
# ═════════════════════════════════════════════════════════════════════════════
class TestEveryIdentifierTypeIsCovered:

    @pytest.mark.parametrize('field,value', [
        ('mdn', '9995550143'),
        ('imei', '359999000011112'),
        ('iccid', '8901240999900001111'),
        ('imsi', '310249999000111'),
        ('line_id', '5639990001'),
        ('account', '109999001'),
    ])
    def test_each_type_is_refused(self, with_protected, field, value):
        """Req 1.3 names MDN, IMEI, ICCID, IMSI, line id and account number."""
        from modules.protected_gate import assert_mapping_safe
        with_protected(value)

        with pytest.raises(Exception) as err:
            assert_mapping_safe({field: value}, context='test')
        assert type(err.value).__name__ == 'ProtectedEntityError'

    @pytest.mark.parametrize('field,value', [
        ('mdn', '9995550143'),
        ('imei', '359999000011112'),
        ('iccid', '8901240999900001111'),
        ('imsi', '310249999000111'),
        ('line_id', '5639990001'),
        ('account', '109999001'),
    ])
    def test_each_type_is_found_in_generated_text(self, with_protected, field, value):
        """The same coverage through the text scan, since that is the output gate."""
        from modules.protected_gate import scan_suite
        with_protected(value)

        tc = TestCase(sno='TC01', summary='Verify request',
                      steps=[TestStep(1, 'Send %s=%s' % (field, value), 'HTTP 200')])
        found = scan_suite(TestSuite(feature_id='X', test_cases=[tc]))
        assert [f['value'] for f in found] == [value], 'missed %s' % field


# ═════════════════════════════════════════════════════════════════════════════
#  Fail closed  (Req 1.4)
# ═════════════════════════════════════════════════════════════════════════════
class TestFailsClosed:

    @pytest.fixture
    def registry_absent(self, monkeypatch):
        from modules import protected_gate
        monkeypatch.setattr(protected_gate, '_REGISTRY_MODULE',
                            'shared.protected_registry_absent_for_test')

    def test_output_refuses_when_the_registry_is_unavailable(self, registry_absent):
        """Req 1.4: proceeding unchecked is the condition the registry exists to prevent."""
        from modules.excel_generator import generate_excel
        from modules.protected_gate import ProtectedRegistryUnavailable

        before = set(glob.glob(os.path.join('outputs', '*')))
        with pytest.raises(ProtectedRegistryUnavailable):
            generate_excel(suite_carrying('3036694392'), log=lambda m: None)
        assert set(glob.glob(os.path.join('outputs', '*'))) == before

    def test_injection_refuses_when_the_registry_is_unavailable(self, registry_absent):
        from modules.protected_gate import ProtectedRegistryUnavailable
        from modules.test_data_injector import get_sample_data

        with pytest.raises(ProtectedRegistryUnavailable):
            get_sample_data('MDN')

    def test_registry_available_reports_false_rather_than_raising(self, registry_absent):
        from modules.protected_gate import registry_available
        assert registry_available() is False

    def test_the_real_registry_is_reachable_from_tsg(self):
        """The canary for the fail-closed rule above.

        Because an unavailable registry now stops generation, TSG being unable to import
        `shared` would take the generator down completely. TSG is a sibling of `shared`
        rather than a child, so this asserts the path bootstrap in protected_gate keeps
        working - if this test fails, every generation run fails.
        """
        from modules.protected_gate import registry_available
        assert registry_available() is True, (
            'TSG can no longer import shared.protected_registry - with the fail-closed '
            'gate this stops all generation')


# ═════════════════════════════════════════════════════════════════════════════
#  Injection points  (Req 1.1)
# ═════════════════════════════════════════════════════════════════════════════
class TestInjectionIsGated:

    def test_get_sample_data_refuses_a_protected_pool_value(self, with_protected,
                                                            monkeypatch):
        """The pool lookup sits inside a broad `except Exception`, so the check has to
        live outside it or the refusal would be swallowed and the value handed out."""
        import modules.test_data_injector as tdi
        with_protected(FAKE_PROTECTED_MDN)
        monkeypatch.setitem(tdi.SIT_SAMPLES, 'MDN', [FAKE_PROTECTED_MDN])
        monkeypatch.setattr('modules.database.get_test_data',
                            lambda *a, **k: [], raising=False)

        with pytest.raises(Exception) as err:
            tdi.get_sample_data('MDN')
        assert type(err.value).__name__ == 'ProtectedEntityError'

    def test_operation_samples_literals_are_checked(self, with_protected, monkeypatch):
        """OPERATION_SAMPLES bypasses get_sample_data, so it needs its own check."""
        import modules.test_data_injector as tdi
        with_protected(FAKE_PROTECTED_MDN)
        monkeypatch.setitem(tdi.OPERATION_SAMPLES, 'activate',
                            {'MDN': FAKE_PROTECTED_MDN, 'RequestType': 'TMO'})

        with pytest.raises(Exception) as err:
            tdi.get_operation_sample_request('activate')
        assert type(err.value).__name__ == 'ProtectedEntityError'

    def test_varied_test_data_rotation_is_checked(self, with_protected, monkeypatch):
        """get_varied_test_data reads SIT_SAMPLES directly rather than via get_sample_data."""
        import modules.test_data_injector as tdi
        with_protected(FAKE_PROTECTED_MDN)
        monkeypatch.setitem(tdi.SIT_SAMPLES, 'MDN', ['3036694392', FAKE_PROTECTED_MDN])

        tdi.get_varied_test_data(0)                        # index 0 -> safe value
        with pytest.raises(Exception) as err:
            tdi.get_varied_test_data(1)                    # index 1 -> protected value
        assert type(err.value).__name__ == 'ProtectedEntityError'

    def test_the_seeders_do_not_store_a_protected_value(self, with_protected):
        """Captured traffic is the likeliest way a real number enters the pool.

        The seeders skip rather than raise: refusing to store keeps the pool clean without
        failing a run that never uses the value, and the output gate still catches it if
        one ever reaches a suite.
        """
        import modules.test_data_injector as tdi
        with_protected(FAKE_PROTECTED_MDN)
        assert tdi._is_protected_quiet(FAKE_PROTECTED_MDN) is True
        assert tdi._is_protected_quiet('3036694392') is False

    def test_the_seeders_refuse_when_the_registry_is_unavailable(self, monkeypatch):
        """Fail closed here means 'do not cache it', which costs only an unseeded pool."""
        import modules.test_data_injector as tdi
        from modules import protected_gate
        monkeypatch.setattr(protected_gate, '_REGISTRY_MODULE',
                            'shared.protected_registry_absent_for_test')
        assert tdi._is_protected_quiet('3036694392') is True


# ═════════════════════════════════════════════════════════════════════════════
#  The shipped seeds  (Req 1.5)
# ═════════════════════════════════════════════════════════════════════════════
class TestShippedSeeds:

    def test_no_shipped_seed_is_currently_protected(self):
        """Against the REAL registry, so a new entry that collides with a seed shows up here.

        Requirement 1.5 asks whether the seven seeded MDNs are synthetic. Until that is
        answered, this at least fails loudly the moment someone registers one of them,
        rather than letting every generation run start failing with no explanation.
        """
        _real_registry_module()
        from modules.protected_gate import is_protected
        from modules.test_data_injector import OPERATION_SAMPLES, SIT_SAMPLES

        offenders = []
        for data_type, values in SIT_SAMPLES.items():
            for value in values:
                if is_protected(value):
                    offenders.append('SIT_SAMPLES[%s] %s' % (data_type, value))
        for op, sample in OPERATION_SAMPLES.items():
            for field, value in sample.items():
                if is_protected(value):
                    offenders.append('OPERATION_SAMPLES[%s].%s %s' % (op, field, value))

        assert offenders == [], (
            'these seeded identifiers are registered do-not-touch, so every suite using '
            'them now fails to generate: %s' % offenders)
