"""Hermetic tests locking the TSG mining-breadth fixes (P1 + P2).

No network, no tsg_cache.db, no `shared` package. Pure unit tests over the public
helpers in modules.dimension_extractor. Covers:

  * FIX 1 — the Jira-AC verb gate admits inflected/batch verbs that were silently
    dropped (ingest/moved, validates/corrects, supported, notifies).
  * FIX 2 — Jira wiki markup (e.g. {panel:title=...}) is stripped before line-splitting
    and is NOT admitted as a scenario.
  * FIX 3 — a Chalk `validation` packed with 'Alternate Flow A:/B:/C:' is split into one
    ExtractedScenario per branch (not collapsed into a single scenario).
"""
import re

from modules.dimension_extractor import (
    _extract_no_notification_negative_from_jira_ac,
    _extract_scenarios_from_chalk_data,
    _strip_jira_wiki_markup,
    _is_mapping_shape_line,
    _split_alt_flow_validation,
)
from modules.chalk_parser import ChalkData, ChalkScenario


class _Jira:
    def __init__(self, key='MWTGPROV-4074', ac=''):
        self.key = key
        self.acceptance_criteria = ac


# ──────────────────────────────────────────────────────────────────────────
# FIX 1 — verb gate admits inflected and batch/pipeline verbs
# ──────────────────────────────────────────────────────────────────────────

def test_fix1_inflected_and_batch_verbs_are_admitted():
    ac = (
        "{panel:title=Functional Requirements}\n"
        " * The Wearable Audit report is ingested from SFTP and moved to the reporting path\n"
        " * NSL validates primary and companion device associations and corrects any gaps\n"
        " * Weekly Friday delivery cadence is supported with up to 90 days of data per file\n"
        " * Monitoring alert notifies SWAT and WTS teams if the expected weekly file is missing\n"
        "{panel}"
    )
    scenarios = _extract_no_notification_negative_from_jira_ac(
        _Jira(ac=ac), existing_scenario_titles=set(), log=lambda *a, **k: None)
    titles = ' || '.join(s.title.lower() for s in scenarios)

    # Each inflected/batch verb bullet must have become its own scenario.
    assert 'ingested' in titles, 'batch verb "ingested" (+"moved") must be admitted'
    assert 'validates' in titles, 'inflected verb "validates" (+"corrects") must be admitted'
    assert 'supported' in titles, 'batch verb "supported" (cadence) must be admitted'
    assert 'notifies' in titles, 'inflected verb "notifies" (monitoring alert) must be admitted'
    # Four distinct testable bullets were previously dropped — expect at least four now.
    assert len(scenarios) >= 4, 'expected >=4 admitted scenarios, got %d' % len(scenarios)


def test_fix1_single_ingest_line_is_admitted():
    ac = "The CHRTR_deltas file is ingested from SFTP and moved to mobility\\MVNE reporting"
    scenarios = _extract_no_notification_negative_from_jira_ac(
        _Jira(ac=ac), existing_scenario_titles=set(), log=lambda *a, **k: None)
    assert scenarios, 'an ingest/move AC line must be admitted as a testable scenario'


# ──────────────────────────────────────────────────────────────────────────
# FIX 2 — wiki markup is stripped and {panel...} is not admitted
# ──────────────────────────────────────────────────────────────────────────

def test_fix2_panel_token_not_admitted_as_scenario():
    ac = (
        "{panel:title=Functional Requirements}\n"
        " * The process does not fail when the report is blank\n"
        "{panel}"
    )
    scenarios = _extract_no_notification_negative_from_jira_ac(
        _Jira(ac=ac), existing_scenario_titles=set(), log=lambda *a, **k: None)
    for s in scenarios:
        assert 'panel' not in s.title.lower(), \
            'wiki {panel:title=...} must not surface as a scenario: %r' % s.title
    # The real blank-report bullet still survives the strip.
    assert any('blank' in s.title.lower() for s in scenarios)


def test_fix2_strip_removes_wiki_tokens_keeps_prose():
    raw = "{panel:title=Functional Requirements}\n * ingested from SFTP\n{color:#ff0000}red{color}\n{panel}"
    out = _strip_jira_wiki_markup(raw)
    assert 'panel' not in out.lower()
    assert 'color' not in out.lower()
    assert 'ingested from SFTP' in out
    assert 'red' in out


def test_fix2_mapping_shape_vetoes_wiki_but_keeps_real_mapping():
    # Real mapping rows must still read as mapping shapes.
    assert _is_mapping_shape_line("'MT' -> call_direction_ind='I'")
    assert _is_mapping_shape_line("FIELD = VALUE")
    # A wiki token must NOT read as a mapping shape even un-stripped.
    assert not _is_mapping_shape_line("{panel:title=Functional Requirements}")


# ──────────────────────────────────────────────────────────────────────────
# FIX 3 — Chalk alternate flows split into one scenario per branch
# ──────────────────────────────────────────────────────────────────────────

_ALT_FLOW_VALIDATION = (
    "Charter SFTP is configured to pick up the file. "
    "Alternate Flow A: ENV_IDENTIFIER=30 -> ROUTEDTO=UAT, record PENDING. "
    "Alternate Flow B: ENV_IDENTIFIER in {10,20,40,50} -> LOWER_ENV_FILE, SKIPPED. "
    "Alternate Flow C: ENV_IDENTIFIER not-in-map (99) -> ROUTING_EXCEPTION, UNROUTABLE; "
    "batch does NOT fail."
)


def test_fix3_alt_flow_split_helper_returns_each_branch():
    branches = _split_alt_flow_validation(_ALT_FLOW_VALIDATION)
    ids = [bid for bid, _ in branches]
    # Lead-in + A + B + C.
    assert 'A' in ids and 'B' in ids and 'C' in ids
    assert len([b for b in ids if b in ('A', 'B', 'C')]) == 3


def test_fix3_chalk_scenario_emits_one_tc_per_alt_flow():
    chalk = ChalkData(
        feature_id='MWTGPROV-4074',
        scenarios=[ChalkScenario(
            title='Verify Charter SFTP environment routing',
            validation=_ALT_FLOW_VALIDATION,
            steps=['Place file on SFTP', 'Run the batch', 'Inspect STG routing'],
            category='Happy Path',
        )],
    )
    scenarios, _entry = _extract_scenarios_from_chalk_data(chalk, log=lambda *a, **k: None)
    # One ChalkScenario with 3 alternate flows (+lead-in) must yield at least 3 branches,
    # NOT one collapsed scenario.
    assert len(scenarios) >= 3, \
        'alt-flow validation must split into >=3 sub-scenarios, got %d' % len(scenarios)
    # Each branch carries a distinct title and branch id (so dedup cannot re-merge them).
    titles = {s.title for s in scenarios}
    assert len(titles) == len(scenarios), 'branch titles must be distinct'
    branch_ids = {getattr(s, '_branch_id', None) for s in scenarios}
    assert branch_ids >= {'A', 'B', 'C'}
    # The unroutable/exception branch is classified Negative.
    assert any(s.category == 'Negative' for s in scenarios), \
        'the ROUTING_EXCEPTION/UNROUTABLE branch must be Negative'


def test_fix3_no_alt_flow_keeps_single_scenario():
    chalk = ChalkData(
        feature_id='MWTGPROV-4074',
        scenarios=[ChalkScenario(
            title='Verify the happy path ingestion succeeds',
            validation='The file is ingested and the records are created successfully.',
            steps=['Place file', 'Run batch'],
            category='Happy Path',
        )],
    )
    scenarios, _entry = _extract_scenarios_from_chalk_data(chalk, log=lambda *a, **k: None)
    assert len(scenarios) == 1, 'a scenario with no alt-flow markers stays 1->1'
