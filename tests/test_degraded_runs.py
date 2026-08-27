"""A degraded run says so.

Guards tsg-tse-hardening Requirement 5. TSG has 137 handlers that log an exception and carry
on. That is mostly right - one failed enrichment pass should not cost a tester their whole
suite - but the run then reports success with less in it than it should have, and the only
trace is a line in a CLI log nobody opens. A partial suite looked identical to a complete one.

The load-bearing test here is `test_a_failed_pass_does_not_change_the_test_cases`: the whole
point is that visibility changed and behaviour did not (Requirement 5.5). If that ever fails,
this feature has started costing test cases and should be reverted rather than adjusted.
"""
import pytest

from modules import degraded_tracker
from modules.data_models_v8 import DataInventory
from modules.test_engine import TestSuite


@pytest.fixture(autouse=True)
def clean_tracker():
    """The tracker is process-global, so every test starts and leaves it empty."""
    degraded_tracker.reset()
    yield
    degraded_tracker.reset()


class TestTheRecord:

    def test_a_clean_run_says_nothing(self):
        """Req 5.4 - an indicator for a healthy run would train people to ignore it."""
        assert degraded_tracker.count() == 0
        assert degraded_tracker.summary_line() == ''
        assert degraded_tracker.pass_names() == []

    def test_a_failed_pass_is_counted_and_named(self):
        """Req 5.1 and 5.2 - the count alone does not tell you what you are missing."""
        degraded_tracker.record('eligibility negatives', RuntimeError('boom'))
        assert degraded_tracker.count() == 1
        assert degraded_tracker.pass_names() == ['eligibility negatives']
        line = degraded_tracker.summary_line()
        assert '1' in line
        assert 'eligibility negatives' in line

    def test_several_failures_are_all_named(self):
        degraded_tracker.record('eligibility negatives', RuntimeError('a'))
        degraded_tracker.record('degenerate-TC prune', ValueError('b'))
        line = degraded_tracker.summary_line()
        assert 'eligibility negatives' in line and 'degenerate-TC prune' in line
        assert '2' in line

    def test_the_same_pass_twice_is_named_once(self):
        """Retries should not inflate the count into something meaningless."""
        degraded_tracker.record('step/title cleanup', RuntimeError('a'))
        degraded_tracker.record('step/title cleanup', RuntimeError('b'))
        assert degraded_tracker.pass_names() == ['step/title cleanup']
        assert len(degraded_tracker.passes()) == 2, 'both failures still recorded in detail'

    def test_a_new_run_starts_clean(self):
        """Req 5.1 - a stale count would be worse than none."""
        degraded_tracker.record('priority finalization', RuntimeError('boom'))
        degraded_tracker.begin_run('MWTGPROV-1234')
        assert degraded_tracker.count() == 0
        assert degraded_tracker.summary_line() == ''

    def test_recording_never_raises(self):
        """The tracker must not become the thing that breaks the run it describes."""
        degraded_tracker.record(None, None)
        degraded_tracker.record('x', 'not an exception')
        degraded_tracker.record(object(), RuntimeError('boom'))
        assert degraded_tracker.count() == 3

    def test_detail_lines_carry_the_cause(self):
        degraded_tracker.record('coverage scorecard', KeyError('missing_field'))
        detail = degraded_tracker.detail_lines()[0]
        assert 'coverage scorecard' in detail
        assert 'KeyError' in detail


class TestAttachingToASuite:

    def test_the_warning_lands_in_suite_warnings(self):
        degraded_tracker.record('degenerate-TC prune', RuntimeError('boom'))
        suite = TestSuite(feature_id='X')
        degraded_tracker.attach_to_suite(suite)
        assert any('DEGRADED RUN' in str(w) for w in suite.warnings)

    def test_it_also_lands_where_the_dashboard_looks(self):
        """Req 5.2 and 5.3.

        The Excel renders `suite.warnings` but the dashboard renders
        `data_inventory.warnings` - a different list. Writing only the first would have left
        the indicator invisible in the UI, which is precisely what this requirement is about.
        """
        degraded_tracker.record('degenerate-TC prune', RuntimeError('boom'))
        suite = TestSuite(feature_id='X')
        suite.data_inventory = DataInventory()
        degraded_tracker.attach_to_suite(suite)
        assert any('DEGRADED RUN' in str(w) for w in suite.data_inventory.warnings)

    def test_a_clean_run_adds_no_warning(self):
        """Req 5.4."""
        suite = TestSuite(feature_id='X')
        suite.data_inventory = DataInventory()
        degraded_tracker.attach_to_suite(suite)
        assert not any('DEGRADED RUN' in str(w) for w in suite.warnings)
        assert not any('DEGRADED RUN' in str(w) for w in suite.data_inventory.warnings)

    def test_attaching_twice_does_not_duplicate(self):
        """block_generate_output refreshes the line after its own passes have run."""
        degraded_tracker.record('auto-diff vs previous suite', RuntimeError('boom'))
        suite = TestSuite(feature_id='X')
        suite.data_inventory = DataInventory()
        degraded_tracker.attach_to_suite(suite)
        degraded_tracker.record('coverage scorecard', RuntimeError('boom'))
        degraded_tracker.attach_to_suite(suite)

        lines = [w for w in suite.warnings if 'DEGRADED RUN' in str(w)]
        assert len(lines) == 1, 'refresh must replace the line, not add another'
        assert 'coverage scorecard' in lines[0], 'the refreshed line must be current'

    def test_other_warnings_are_preserved(self):
        degraded_tracker.record('step/title cleanup', RuntimeError('boom'))
        suite = TestSuite(feature_id='X', warnings=['an unrelated warning'])
        degraded_tracker.attach_to_suite(suite)
        assert 'an unrelated warning' in suite.warnings

    def test_a_suite_without_a_data_inventory_is_fine(self):
        """CR suites may not carry one."""
        degraded_tracker.record('step/title cleanup', RuntimeError('boom'))
        suite = TestSuite(feature_id='X')
        assert degraded_tracker.attach_to_suite(suite) == 1


@pytest.mark.cache
@pytest.mark.slow
class TestTheEngineReportsItsOwnDegradation:
    """End to end through the real V8 engine on a cached feature."""

    FEATURE = 'MWTGPROV-4190'

    def _build(self, monkeypatch=None):
        from modules import data_first_engine as dfe
        from modules.database import _conn, load_chalk_as_object
        from modules.deep_miner import deep_mine
        from modules.pipeline import block_jira_fetch
        from tests.conftest import DEFAULT_OPTIONS

        quiet = lambda *a, **k: None
        jira = block_jira_fetch(page=None, feature_id=self.FEATURE, log=quiet)['jira']
        if jira is None:
            pytest.skip('%s is not in the cache' % self.FEATURE)
        conn = _conn()
        row = conn.execute(
            "SELECT pi_label FROM chalk_cache WHERE feature_id=? "
            "AND scenarios_json != '[]' LIMIT 1", (self.FEATURE,)).fetchone()
        conn.close()
        chalk = load_chalk_as_object(self.FEATURE, row['pi_label']) if row else None
        mined = deep_mine(jira, chalk, page=None, log=quiet)
        return dfe.build_test_suite_v8(jira, chalk, [], dict(DEFAULT_OPTIONS),
                                       deep_mine_result=mined, log=quiet)

    def test_a_healthy_run_reports_nothing(self):
        suite = self._build()
        assert not any('DEGRADED RUN' in str(w) for w in (suite.warnings or []))

    def test_a_failed_pass_is_reported_on_the_suite(self, monkeypatch):
        from modules import data_first_engine as dfe
        monkeypatch.setattr(dfe, '_prune_degenerate_tcs',
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError('simulated prune failure')))
        suite = self._build()
        warnings = [w for w in (suite.warnings or []) if 'DEGRADED RUN' in str(w)]
        assert warnings, 'a skipped pass produced no indicator'
        assert 'degenerate-TC prune' in warnings[0]

    def test_a_failed_pass_does_not_change_the_test_cases(self, monkeypatch):
        """Req 5.5, and the reason this feature is safe to ship.

        Only visibility changed. If this fails, the instrumentation has altered
        continue-on-failure behaviour and should be reverted, not tuned.
        """
        from modules import data_first_engine as dfe
        healthy = [tc.summary for tc in self._build().test_cases]

        monkeypatch.setattr(dfe, '_prune_degenerate_tcs',
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError('simulated prune failure')))
        degraded = self._build()

        assert len(degraded.test_cases) == len(healthy), (
            'the failed pass changed the test-case count: %d -> %d'
            % (len(healthy), len(degraded.test_cases)))
