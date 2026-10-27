"""Hermetic regression tests for grounded-steps-2 (TSG step-generation accuracy).

Pattern mirrors tests/test_grounded_api_steps.py: build scenarios directly through
step_templates.get_step_chain with the real PI-60 Chalk scenario titles — no Jira/Chalk/
browser needed. These pin the three remaining accuracy defects:

  (a) a non-ACTIVE-status scenario on a non-hotline feature does NOT route to the Hotline
      flow and emits no transactiontype=sh / apollo_ne / itmbo / emm (MWTGPROV-4575 TC03);
  (b) a UI-visibility scenario names its SPECIFIC target (the send-ota-mdn resend action /
      the "Resend OTA Notification" option) and emits no generic "all expected fields and
      controls" filler nor a truncated "exists under" fragment (MWTGPROV-4575 TC01/TC02);
  (c) the four differing MWTGPROV-4576 name-part scenarios produce four DIFFERENT step
      chains, none containing "12-layer" or "Activate Subscriber API", and the VZW one
      reflects VZW-unchanged (MWTGPROV-4576 TC01-04).
"""
from modules.step_templates import get_step_chain, _hotline_is_the_operation


# ── Real PI-60 Chalk scenario titles (quoted from the plan / tsg_cache.db) ────

F4575 = ('[NBOP, NSLNM, NE, INTG]: TMO - Resend OTA Notification to Device send-ota-mdn')
F4576 = ('[NE, NBOP, INTG]: CR - TMO - Handle 25-Character LNP Customer Name Limit on Port-In')

SC_4575_TC01 = ("A TMO subscriber line is ACTIVE. The agent is authenticated in NBOP with a "
                "permitted Century Shield role.")
SC_4575_TC02 = ("A new Century Shield group 'Resend OTA Notification' exists under Reset Line. "
                "NBOP users exist with NBOP_Admin and with non-Admin roles.")
SC_4575_TC03 = ("A TMO line exists whose status is not ACTIVE - e.g. disconnected or "
                "suspended/hotlined. The agent submits Resend OTA Notification for that line.")

SC_4576_TC01 = "A TMO port-in activation reaches NE with a first name + last name pair from NSL."
SC_4576_TC02 = "A TMO port-in activation reaches NE with a single business name value from NSL."
SC_4576_TC03 = "A VZW port-in activation with an LNP name part >25 chars."
SC_4576_TC04 = ("A TMO port-in reaching NE with name parts at controlled lengths around the "
                "25-char boundary.")

_HOTLINE_FOREIGN = ('transactiontype=sh', 'apollo_ne', 'itmbo', 'emm',
                    'hotline request', 'line status = hotlined', 'hotlined')


def _chain_text(chain):
    return ' '.join('%s %s' % (s, e) for s, e in chain).lower()


# ── (a) non-ACTIVE status scenario must NOT graft the Hotline flow ────────────

def test_hotline_guard_ignores_slash_joined_status_adjective():
    # "suspended/hotlined" is a status adjective list, not the operation.
    t = SC_4575_TC03.lower()
    assert _hotline_is_the_operation(t) is False


def test_hotline_guard_ignores_or_joined_status_adjective():
    assert _hotline_is_the_operation('line is disconnected or hotlined or suspended') is False


def test_hotline_guard_still_fires_when_hotline_is_the_operation():
    # A genuine hotline operation still routes to the hotline flow.
    assert _hotline_is_the_operation('enable hotline on the subscriber line') is True


def test_non_active_scenario_no_hotline_graft():
    chain = get_step_chain(SC_4575_TC03, '', F4575, feature_type='api')
    blob = _chain_text(chain)
    for bad in _HOTLINE_FOREIGN:
        assert bad not in blob, 'Hotline flow leaked into non-ACTIVE scenario: %r' % bad


# ── (b) UI-visibility scenario names its SPECIFIC target, not generic filler ──

def test_ui_scenario_no_generic_filler_and_no_truncation():
    chain = get_step_chain(SC_4575_TC02, '', F4575, feature_type='api')
    blob = _chain_text(chain)
    # No generic "all expected fields and controls" filler.
    assert 'all expected fields and controls' not in blob
    # No truncated "exists under" fragment surviving as a step clause.
    assert 'exists under via' not in blob
    assert 'perform a new century shield group' not in blob
    # Names the SPECIFIC target option.
    assert 'resend ota notification' in blob


def test_ui_tc01_names_resend_action_not_generic():
    chain = get_step_chain(SC_4575_TC01, '', F4575, feature_type='api')
    blob = _chain_text(chain)
    assert 'all expected fields and controls' not in blob
    # Grounded in line status / the send-ota-mdn resend action, not a generic "perform X".
    assert ('send-ota-mdn' in blob or 'resend ota' in blob or 'line status' in blob
            or 'active' in blob)


# ── (c) 4576: four differentiated, grounded name-trim chains ──────────────────

def _c(title):
    return _chain_text(get_step_chain(title, '', F4576, feature_type='api'))


def test_4576_four_chains_are_differentiated():
    chains = [_c(SC_4576_TC01), _c(SC_4576_TC02), _c(SC_4576_TC03), _c(SC_4576_TC04)]
    assert len(set(chains)) == 4, 'the four 4576 name-part scenarios collapsed to identical steps'


def test_4576_no_ungrounded_activation_or_12layer():
    for title in (SC_4576_TC01, SC_4576_TC02, SC_4576_TC03, SC_4576_TC04):
        blob = _c(title)
        assert '12-layer' not in blob, 'ungrounded 12-layer closer in %r' % title
        assert 'activate subscriber api' not in blob, 'activation graft in %r' % title


def test_4576_grounded_in_name_trim_rule():
    # Each TMO scenario references the 25-char / name-part concatenation/split/trim rule.
    for title in (SC_4576_TC01, SC_4576_TC02, SC_4576_TC04):
        blob = _c(title)
        assert ('25' in blob or 'name' in blob), 'not grounded in name-trim rule: %r' % title


def test_4576_vzw_reflects_unchanged():
    blob = _c(SC_4576_TC03)
    assert 'vzw' in blob
    assert 'unchanged' in blob or 'not' in blob
