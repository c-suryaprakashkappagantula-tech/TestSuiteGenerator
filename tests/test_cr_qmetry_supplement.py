# -*- coding: utf-8 -*-
"""Hermetic proof that the narrow-CR path reaches the QMetry authored-steps supplement.

The bug: a narrow CR/bug ticket with no Chalk scenarios (exactly the MANUAL-LINKS case for
MWTGPROV-4533) delegates to `_build_cr_suite_v8`, which used to return WITHOUT ever invoking
the QMetry supplement — so authored QMetry test cases on the ticket's subtask were never
pulled. These tests drive `build_test_suite_v8` down the narrow-CR branch with the QMetry
client monkeypatched (no network) and assert:

  1. the supplement IS invoked and its returned authored TC is merged into the CR suite; and
  2. a dead QMetry session (QMetrySessionError) does NOT crash the run and leaves the
     `_qmetry_skip_warning` behind (fail-loud-but-safe, same contract as the main path).
"""
import os
import sys

import pytest

_TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TSG_ROOT not in sys.path:
    sys.path.insert(0, _TSG_ROOT)

from modules import qmetry_steps_source as qss
from modules.data_first_engine import build_test_suite_v8
from modules.jira_fetcher import JiraIssue
from modules.data_models_v8 import ExtractedScenario
from modules.traceability import create_traceability
from modules.qmetry_steps_source import QMetrySessionError


# A genuinely narrow CR with NO Chalk scenarios → engine takes the line-204 delegation
# branch (_is_cr and not _broad_cr and not _has_chalk_scenarios). One subtask carries the
# QMetry linkage the resolver would use, exactly as MWTGPROV-4533 did in manual mode.
def _narrow_cr_jira():
    return JiraIssue(
        key='MWTGPROV-4533',
        summary='[NSL, INTG]: CR - Sync Device/SIM - Fix E3812 error on activation',
        description='Defect: activation fails with E3812. Fix the sync so it no longer errors.',
        issue_type='Change Request',
        acceptance_criteria='The E3812 error no longer occurs when syncing device/SIM.',
        subtasks=[{'key': 'MWTGMED-5214', 'summary': 'Sync fix implementation'}],
    )


_OPTIONS = {
    'include_positive': True,
    'include_negative': True,
    'include_e2e': True,
    'include_edge': True,
    'include_attachments': False,
    'custom_instructions': '',
    'engine_version': '8',
}


def _authored_scenario():
    """A single Tier-3 ADD-AS-NEW-TC result, shaped exactly like what the real supplement
    appends: an ExtractedScenario carrying authored `steps_hint`."""
    tr = create_traceability('Subtask AC', 'MWTGMED-5214', 'authored QMetry test case')
    return ExtractedScenario(
        title='Authored QMetry regression for device/SIM sync',
        validation='The sync completes without the E3812 error.',
        category='Regression',
        source=tr,
        steps_hint=[
            'Trigger a device/SIM sync for an affected account',
            'Observe the activation response and the call outcome',
        ],
    )


def test_narrow_cr_invokes_qmetry_and_merges_authored_tc(monkeypatch):
    """The supplement is reached on the CR path and its authored TC lands in the suite."""
    seen = {'invoked': False}

    def _fake_enrich(scenarios, jira, options, log=print):
        # The CR path seeds an EMPTY list; the supplement appends Tier-3 new scenarios.
        seen['invoked'] = True
        assert scenarios == []          # CR path seeds empty (plan decision)
        scenarios.append(_authored_scenario())
        return 0

    # Patch where the engine looks it up: it does `from .qmetry_steps_source import ...`
    # inside the function, so the attribute on the module is what gets bound.
    monkeypatch.setattr(qss, 'enrich_scenarios_with_qmetry', _fake_enrich)

    suite = build_test_suite_v8(_narrow_cr_jira(), None, [], dict(_OPTIONS),
                                log=lambda *a, **k: None)

    assert seen['invoked'], 'the narrow-CR path never reached the QMetry supplement'
    titles = [getattr(tc, 'summary', '') or '' for tc in suite.test_cases]
    # tc_builder normalises the title (slashes/spaces → underscores, feature-id prefix),
    # so match on a stable token from the authored scenario title rather than verbatim text.
    assert any('Authored_QMetry' in t or 'Authored QMetry' in t for t in titles), (
        'the authored QMetry test case was not merged into the CR suite; got: %s' % titles)


def test_narrow_cr_qmetry_auth_failure_is_non_fatal(monkeypatch):
    """A dead QMetry session must NOT crash the run; the skip warning must be set."""
    def _raise(scenarios, jira, options, log=print):
        raise QMetrySessionError('no authenticated QMetry session')

    monkeypatch.setattr(qss, 'enrich_scenarios_with_qmetry', _raise)

    options = dict(_OPTIONS)
    suite = build_test_suite_v8(_narrow_cr_jira(), None, [], options,
                                log=lambda *a, **k: None)

    # Run survived (a suite came back) and the fail-loud skip warning is stashed.
    assert suite is not None
    assert options.get('_qmetry_skip_warning'), (
        'auth failure must leave a visible _qmetry_skip_warning on options'
    )
    assert 'QMetry supplement skipped' in options['_qmetry_skip_warning']
