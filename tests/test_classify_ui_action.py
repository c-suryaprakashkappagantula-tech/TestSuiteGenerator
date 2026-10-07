"""Unit tests for the design §6 action-family classifier
(infer_ui_family / classify_ui_action / needs_control)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.crawl_grounding import (  # noqa: E402
    CrawlControl, CrawlPage, MatchResult, classify_ui_action, infer_ui_family,
    needs_control, normalize,
)


# --- one case per table row (first hit wins) -------------------------------
@pytest.mark.parametrize('text, action, wanted, extra', [
    ("Verify the MSISDN field is read-only", 'nbop.ui.field_readonly',
     ('input', 'dropdown', 'field'), {'expect': 'readonly'}),
    ("Verify the Notes field is editable", 'nbop.ui.field_editable',
     ('input', 'dropdown', 'field'), {'expect': 'editable'}),
    ("Verify the Submit button is disabled", 'nbop.ui.button_state',
     ('button',), {'expect': 'disabled'}),
    ("Select 'Lost' from the Reason dropdown", 'nbop.ui.select_dropdown',
     ('dropdown',), {'value': 'Lost'}),
    ("Enter 'abc123' into the IMEI input", 'nbop.ui.enter_text',
     ('input',), {'value': 'abc123'}),
    ("Verify the confirmation popup appears", 'nbop.ui.open_popup',
     ('button', 'link'), {}),
    ("Click the Usage tab", 'nbop.ui.open_tab', ('tab',), {}),
    ("Click Save", 'nbop.ui.click_button', ('button', 'link'), {}),
    ("Use the hamburger to reach Line Details", 'nbop.ui.open_menu', (), {}),
    ("Navigate to Subscriber Profile", 'nbop.ui.open_page', (), {}),
    ("Verify the Plan column is displayed", 'nbop.ui.element_present',
     ('column', 'field', 'heading', 'tab', 'button', 'link'), {'expect': 'present'}),
    ("Subscriber Profile", 'nbop.ui.open_page', (), {}),
])
def test_table_rows(text, action, wanted, extra):
    assert infer_ui_family(text) == (action, wanted, extra)


# --- precedence --------------------------------------------------------------
@pytest.mark.parametrize('text, action', [
    ("Verify field is read-only", 'nbop.ui.field_readonly'),         # not element_present
    ("Verify the IMEI is non-editable", 'nbop.ui.field_readonly'),   # not field_editable
    ("Click the Submit button once it is enabled", 'nbop.ui.button_state'),  # state before click
    ("Verify Submit button is enabled", 'nbop.ui.button_state'),     # not element_present
    ("Click OK in the dialog", 'nbop.ui.open_popup'),                # popup row before click
    ("Open the menu", 'nbop.ui.open_menu'),                          # menu before navigate
    ("Open Usage tile and verify it is displayed", 'nbop.ui.open_page'),
    ("Verify account type is shown in profile", 'nbop.ui.element_present'),  # 'type' not a verb
])
def test_rule_order_precedence(text, action):
    assert infer_ui_family(text)[0] == action


def test_absent_expectation():
    for text in ("Verify the Plan column is not displayed",
                 "Verify the legacy field is removed",
                 "Check the banner is absent"):
        _, _, extra = infer_ui_family(text)
        assert extra == {'expect': 'absent'}, text


# --- value extraction --------------------------------------------------------
@pytest.mark.parametrize('text, value', [
    ('Select "Port Out" in the Reason dropdown', 'Port Out'),
    ("Choose \u2018Lost\u2019 from Reason dropdown", 'Lost'),
    ("Reason = Stolen", 'Stolen'),
    ("Set Reason = 'Device Lost' in the dropdown", 'Device Lost'),
    ("Reason = Stolen, then click Save", 'Stolen'),
])
def test_select_value_extraction(text, value):
    action, _, extra = infer_ui_family(text)
    assert action == 'nbop.ui.select_dropdown'
    assert extra['value'] == value


@pytest.mark.parametrize('text, value', [
    ("Enter 4045551234 into the MSISDN field", '4045551234'),
    ("Type 'hello world' in Notes", 'hello world'),
    ("Open the page, then input \"X-1\" into Code", 'X-1'),
])
def test_enter_value_extraction(text, value):
    action, wanted, extra = infer_ui_family(text)
    assert action == 'nbop.ui.enter_text'
    assert wanted == ('input',)
    assert extra == {'value': value}


def test_apostrophe_is_not_a_quote():
    _, _, extra = infer_ui_family("Select the user's plan from the Plan dropdown")
    assert 'value' not in extra


# --- needs_control -----------------------------------------------------------
@pytest.mark.parametrize('action, expected', [
    ('nbop.ui.open_page', False), ('nbop.ui.open_menu', False),
    ('nbop.ui.open_tab', True), ('nbop.ui.click_button', True),
    ('nbop.ui.element_present', True), ('nbop.ui.select_dropdown', True),
    ('nbop.ui.enter_text', True), ('nbop.ui.field_readonly', True),
    ('nbop.ui.field_editable', True), ('nbop.ui.button_state', True),
    ('nbop.ui.open_popup', True),
])
def test_needs_control(action, expected):
    assert needs_control(action) is expected


# --- classify_ui_action refinement -------------------------------------------
def _ctrl(ctype, label):
    return CrawlControl(ctype, label, normalize(label))


def _grounded(kind='page', control=None, name='Subscriber Profile'):
    page = CrawlPage(name, name.split(': ', 1)[-1], kind, '', ())
    return MatchResult('grounded', '', page, control, (), 120)


def test_tab_control_refines_click_to_open_tab():
    m = _grounded(control=_ctrl('tab', 'Usage'))
    assert classify_ui_action("Click Usage", m) == ('nbop.ui.open_tab', {})
    assert classify_ui_action("Go to Usage", m) == ('nbop.ui.open_tab', {})


def test_tab_control_keeps_verification():
    m = _grounded(control=_ctrl('tab', 'Usage'))
    assert classify_ui_action("Verify Usage is displayed", m) == (
        'nbop.ui.element_present', {'expect': 'present'})


@pytest.mark.parametrize('kind', ['edit_menu', 'context_menu'])
def test_menu_page_only_is_open_menu(kind):
    m = _grounded(kind=kind, name='Manage Line: Change Features')
    assert classify_ui_action("Click Change Features", m) == ('nbop.ui.open_menu', {})
    assert classify_ui_action("Change Features", m) == ('nbop.ui.open_menu', {})


def test_page_only_fallback_is_open_page():
    m = _grounded()
    assert classify_ui_action("Verify Subscriber Profile is displayed", m) == (
        'nbop.ui.open_page', {})
    assert classify_ui_action("Navigate to Subscriber Profile", m) == (
        'nbop.ui.open_page', {})


def test_control_match_keeps_inferred_action_and_extra():
    m = _grounded(control=_ctrl('dropdown', 'Reason'))
    assert classify_ui_action("Select 'Lost' from the Reason dropdown", m) == (
        'nbop.ui.select_dropdown', {'value': 'Lost'})


def test_unmatched_or_none_returns_inferred():
    um = MatchResult('unmatched', 'no_match', None, None, (), 0)
    assert classify_ui_action("Click Save", um) == ('nbop.ui.click_button', {})
    assert classify_ui_action("Click Save", None) == ('nbop.ui.click_button', {})
