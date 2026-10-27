"""UI-visibility step grounding — ROOT fix for generic filler + truncated summary.

Covers the two required cases (plus state-grounding and the zero-generic backstop):
  (a) a UI-visibility scenario produces a verify step naming its SPECIFIC target
      (not generic "visible, correctly labeled, and accessible" filler);
  (b) an over-length title is completed/trimmed at a word boundary, never cut
      mid-phrase with a filler phrase grafted on.

Hermetic: no network, no live DB. The step generators are called directly on
hand-built scenario titles, mirroring tests/test_ui_feature_detect.py.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import nbop_ui_knowledge as nk  # noqa: E402
from modules import step_templates as st  # noqa: E402
from modules.data_models_v8 import (  # noqa: E402
    TestCase, TestStep, TestSuite, TraceabilityRecord,
)
from modules.zero_generic_validator import validate_suite  # noqa: E402

# The three banned UI-visibility filler phrases that must never be the whole
# assertion of any step, anywhere.
BANNED_PHRASES = [
    'expected fields and controls',
    'present, enabled, and interactive',
    'visible, correctly labeled, and accessible',
    'sends outbound call to downstream',
]

# Real MWTGPROV-4575 Chalk scenario titles.
TC01_TITLE = ('A TMO subscriber line is ACTIVE. The agent is authenticated in NBOP '
              'with a permitted Century Shield role.')
TC02_TITLE = ("A new Century Shield group 'Resend OTA Notification' exists under Reset "
              "Line. NBOP users exist with NBOP_Admin and with non-Admin roles.")


def _all_text(steps):
    return ' | '.join('%s %s' % (s, e) for s, e in steps).lower()


def _no_banned(steps):
    txt = _all_text(steps)
    return [b for b in BANNED_PHRASES if b in txt]


# --- (a) grounded visibility, no generic filler ----------------------------

def test_tc02_visibility_names_specific_target_both_paths():
    """TC02 (genuine visibility) names the specific option AND its parent, with no
    generic filler — through BOTH the KB-absent fallback and the KB path."""
    for gen in (lambda: st._ui_flow_steps(TC02_TITLE, ''),
                lambda: nk.generate_ui_steps(TC02_TITLE, '', TC02_TITLE)):
        steps = gen()
        assert _no_banned(steps) == [], 'banned filler leaked: %r' % _no_banned(steps)
        verify = [s for s in steps if s[0].lower().startswith('verify')]
        assert verify, 'no verify step produced'
        summary, expected = verify[0]
        # Names the SPECIFIC target and its parent.
        assert 'Resend OTA Notification' in summary
        assert 'Reset Line' in summary or 'Reset Line' in expected
        # Expected asserts the specific role-gated condition.
        assert 'NBOP_Admin' in expected or 'non-Admin' in expected.replace('-', '')


# --- (b) over-length title completed/trimmed at a boundary ------------------

def test_tc02_summary_is_complete_sentence_not_truncated():
    """The TC02 verify summary must be a complete, coherent sentence — never the
    "...exists under is visible..." graft the old double-truncate produced."""
    for gen in (lambda: st._ui_flow_steps(TC02_TITLE, ''),
                lambda: nk.generate_ui_steps(TC02_TITLE, '', TC02_TITLE)):
        steps = gen()
        summary = [s[0] for s in steps if s[0].lower().startswith('verify')][0]
        assert 'exists under is visible' not in summary.lower()
        assert not summary.rstrip().lower().endswith(' under')
        assert not summary.rstrip().lower().endswith(' exists')


def test_over_length_title_trimmed_at_boundary_no_filler_graft():
    """A synthetic 120-char visibility title is trimmed at a word boundary and never
    has a filler phrase grafted onto a cut fragment."""
    long_target = 'Century Shield Advanced Configuration Management Panel Option Group ' + ('Alpha ' * 12)
    title = "Verify the '%s' option exists under Reset Line Settings" % long_target.strip()
    steps = st._ui_flow_steps(title, '')
    assert _no_banned(steps) == []
    summary = [s[0] for s in steps if s[0].lower().startswith('verify')][0]
    # No dangling connective at the end (never cut mid-phrase onto a connective).
    assert not summary.rstrip().lower().endswith((' under', ' exists', ' the', ' and', ' of', ' to'))
    # No generic filler grafted on.
    for b in BANNED_PHRASES:
        assert b not in summary.lower()


# --- (c) state scenario grounds the STATE, not "is visible" ------------------

def test_tc01_state_scenario_asserts_active_status():
    """TC01 asserts the line status reads ACTIVE on the specific screen — not a
    bare "is visible" visibility phrase."""
    for gen in (lambda: st._ui_flow_steps(TC01_TITLE, ''),
                lambda: nk.generate_ui_steps(TC01_TITLE, '', TC01_TITLE)):
        steps = gen()
        assert _no_banned(steps) == []
        verify = [s for s in steps if s[0].lower().startswith('verify')][0]
        summary, expected = verify
        assert 'ACTIVE' in summary or 'ACTIVE' in expected
        assert 'visible, correctly labeled, and accessible' not in (summary + expected).lower()


# --- (d) zero-generic gate hard-fails on the filler phrases -----------------

def _suite_with_expected(expected_text):
    tc = TestCase(
        sno='TC_001',
        summary='Verify a specific NBOP option',
        steps=[TestStep(step_num=1, summary='Verify the Reset Line option appears',
                        expected=expected_text)],
        traceability=TraceabilityRecord(
            source_type='chalk', source_id='TS_X_1', extracted_text='scenario text'),
    )
    return TestSuite(feature_id='MWTGPROV-90002', test_cases=[tc])


def test_zero_generic_gate_flags_present_enabled_interactive():
    result = validate_suite(_suite_with_expected('Element is present, enabled, and interactive'),
                            log=lambda *a, **k: None)
    assert result.passed is False
    assert any('filler' in v.lower() or 'generic' in v.lower() for v in result.violations)


def test_zero_generic_gate_flags_visible_correctly_labeled():
    result = validate_suite(
        _suite_with_expected('The option is visible, correctly labeled, and accessible'),
        log=lambda *a, **k: None)
    assert result.passed is False


def test_zero_generic_gate_passes_grounded_expected():
    result = validate_suite(
        _suite_with_expected("Resend OTA Notification is listed specifically under Reset Line"),
        log=lambda *a, **k: None)
    assert result.passed is True, result.violations
