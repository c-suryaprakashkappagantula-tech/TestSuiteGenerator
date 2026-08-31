"""Titles are cut on word boundaries.

Guards the 20 August fix. Roughly 35 raw character slices built summaries with no regard for
word boundaries, producing titles that ended 'for dea', 'non-eli', 'any flo'.

A repair pass at the sanitiser chokepoint was tried and reverted: it could not tell a cut
fragment from a genuine final word and removed 'workflow' from 'Verify CR fix applies to
Reconnect workflow'. Prevention at the slice is the only approach that cannot damage a good
title, and the last two tests here encode that.
"""
import re
import pytest

from modules.step_templates import truncate_at_word


class TestTruncateAtWord:

    @pytest.mark.parametrize('text,limit', [
        ('The front-end system should receive a response indicating that any flow has been '
         'processed successfully', 80),
        ('NSL has ensure that if it receives and error response from TMO for deactivating a '
         'line it should retry', 80),
        ('nslnm intg: guarente d delivery flow is blocked when the line is on a non-eligible '
         'rate plan', 90),
    ])
    def test_never_ends_mid_word(self, text, limit):
        out = truncate_at_word(text, limit)
        assert len(out) <= limit
        # the character following the cut must be a space, i.e. a whole word was kept
        assert text[len(out):len(out) + 1] in (' ', ''), out

    def test_short_text_is_untouched(self):
        assert truncate_at_word('Verify BCD updates', 80) == 'Verify BCD updates'

    def test_drops_a_dangling_connective(self):
        source = 'Verify the flow is blocked when the line is on a non-eligible plan'
        # The tail is now stripped repeatedly. This previously expected
        # '... when the line is on', which still ended on a dangling 'on' - a single pass
        # removed only the trailing 'a'. Connectives arrive in runs, so one pass is not enough.
        assert truncate_at_word(source, 49) == 'Verify the flow is blocked when the line'

    def test_untruncated_text_keeps_its_own_last_word(self):
        source = 'Verify the flow is blocked when the line is on a'
        assert truncate_at_word(source, 48) == source

    def test_underscore_joined_titles_cut_on_a_word_boundary(self):
        """An underscore is a word boundary, so these must not be cut mid-word.

        This test previously asserted the OPPOSITE - that the result was exactly 40
        characters - while its docstring said the title "must not be mangled". A hard slice
        to exactly the limit IS the mangling, so the assertion encoded the defect.

        It matters because the V8 engine joins every title with underscores. Searching only
        for a space found nothing, treated the whole title as one long token and fell back to
        the raw slice this function exists to prevent. Measured: MWTGPROV-4416 shipped
        '..._SMS/MMS=Messa'. V7 uses spaces and was unaffected, which is why the original fix
        looked complete.
        """
        source = 'New_MVNO_Guarented_Delivery_flow_in_NBOP_not_working'
        out = truncate_at_word(source, 40)
        assert len(out) <= 40
        assert not out.endswith('_')
        # Cut on a boundary: every retained token is whole.
        assert source.startswith(out)
        assert source[len(out)] == '_', 'cut landed inside a word: %r' % out

    def test_a_single_long_token_still_falls_back_to_a_hard_cut(self):
        """With no boundary at all there is nothing to cut on, so the slice stands."""
        source = 'A' * 60
        assert len(truncate_at_word(source, 40)) == 40


class TestStripDanglingTail:
    """Text can arrive already ending on a connective, without ever being truncated.

    An acceptance-criteria line spliced into a step template gave 'Navigate to the menu for:
    that the Period dropdown shows values 1-20 months and'. That fits inside the limit, so
    truncation never fired and the tail survived - which is why this is separate from
    truncate_at_word.
    """

    @pytest.mark.parametrize('source,expected', [
        ('that the Period dropdown shows values 1-20 months and',
         'that the Period dropdown shows values 1-20 months'),
        ('When the TMO radio button is selected, the NPANXX method should not',
         'When the TMO radio button is selected, the NPANXX method'),
        ('For wearable activation NOP should display MDNs that belong to',
         'For wearable activation NOP should display MDNs that belong'),
        ('Verify_the_permission_is_OFF_when_CS_access_to',
         'Verify_the_permission_is_OFF_when_CS_access'),
    ])
    def test_a_trailing_connective_is_removed(self, source, expected):
        from modules.step_templates import strip_dangling_tail
        assert strip_dangling_tail(source) == expected

    @pytest.mark.parametrize('source', [
        'Verify CR fix applies to Reconnect workflow',
        'Verify the BCD change is applied to the account',
        'Navigate to NBOP > Mobile Service Management > Network Inquiry',
    ])
    def test_a_genuine_final_word_is_kept(self, source):
        """Only a TRAILING connective goes. An earlier attempt matched anywhere and ate
        real words, which is why this is anchored to the end."""
        from modules.step_templates import strip_dangling_tail
        assert strip_dangling_tail(source) == source

    @pytest.mark.parametrize('value', ['', None])
    def test_empty_input_is_safe(self, value):
        from modules.step_templates import strip_dangling_tail
        assert strip_dangling_tail(value) == value

    @pytest.mark.parametrize('value', ['', None])
    def test_empty_input_is_safe(self, value):
        assert truncate_at_word(value, 80) == value


class TestTransformToScenarioTitle:
    """Titles built from raw Chalk/AC prose must read as whole sentences.

    Two defects found during the pre-live-run audit, both in
    `tc_builder._transform_to_scenario_title`:

    1. A multi-sentence Chalk scenario was truncated into a run-on. MWTGPROV-4416's
       185-character scenario shipped '...SMS/MMS=Messages. Validation ranges apply per',
       ending on a dangling 'per'. tc_builder HAS a first-sentence trim, but it is gated on
       len > 140 and runs AFTER this function, which had already shortened the text to 136 -
       so the gate never fired.
    2. The 'When X, Y' -> 'Verify Y when X' transform used a non-greedy `(.{10,80}?)` for the
       condition, so it stopped at the first 10 characters that let the rest of the pattern
       match, splitting the clause in the wrong place, then hard-sliced both halves with
       `[:40]`. MWTGPROV-4086 shipped
       "Verify the 'MNO_TMO' permission is OFF, NBOP to when CS access to" - garbled rather
       than merely truncated.
    """

    def test_a_multi_sentence_scenario_keeps_its_first_sentence(self):
        from modules.tc_builder import _transform_to_scenario_title
        source = ('Bucket Value unit dynamically changes based on Type selection: Data=MB, '
                  'Voice=Mins, SMS/MMS=Messages. Validation ranges apply per unit type. '
                  'Correct unit included in activation request.')
        out = _transform_to_scenario_title(source, 'Optional Feature Provisioning')
        assert out.endswith('SMS/MMS=Messages'), out
        assert 'Validation ranges apply per' not in out, 'run-on was not trimmed'

    def test_a_short_single_sentence_is_left_alone(self):
        from modules.tc_builder import _transform_to_scenario_title
        source = 'Verify the BCD change is rejected for a suspended line'
        assert _transform_to_scenario_title(source, 'Change BCD') == source

    def test_the_when_clause_keeps_the_whole_condition(self):
        from modules.tc_builder import _transform_to_scenario_title
        source = ("When CS access to the 'MNO_TMO' permission is OFF, NBOP to display the "
                  "New Line Activation page without any MNO options")
        out = _transform_to_scenario_title(source, 'MNO Migration')
        assert "when CS access to the 'MNO_TMO' permission is OFF" in out, out
        # The garbled form put the action's tail before 'when'.
        assert not out.endswith(('to', 'when', 'access to')), out

    def test_a_when_clause_without_a_comma_is_not_rearranged(self):
        """Without a comma there is no reliable clause boundary, so guessing is worse."""
        from modules.tc_builder import _transform_to_scenario_title
        source = 'When the subscriber line is suspended the change must be rejected'
        out = _transform_to_scenario_title(source, 'Change Feature')
        assert ' when ' not in out.replace('When ', ''), out

    def test_a_violation_title_is_still_shortened(self):
        """The pre-existing violation-code path must keep working."""
        from modules.tc_builder import _transform_to_scenario_title
        source = ('Verify data-alignment corrects ANDROID_AS_IOS violation by NSL triggers '
                  'CM event and OS differs between systems')
        out = _transform_to_scenario_title(source, 'Data Alignment')
        assert out == 'Verify data-alignment corrects ANDROID_AS_IOS violation', out

    @pytest.mark.parametrize('source', [
        'Bucket Value unit dynamically changes based on Type selection: Data=MB, '
        'Voice=Mins, SMS/MMS=Messages. Validation ranges apply per unit type.',
        "When CS access to the 'MNO_TMO' permission is OFF, NBOP to display the page "
        'without any MNO options',
        'Verify that the Type field affects the Bucket Value unit label dynamically.',
    ])
    def test_no_title_ends_on_a_dangling_connective(self, source):
        from modules.tc_builder import _transform_to_scenario_title
        out = _transform_to_scenario_title(source, 'Feature')
        last = re.findall(r"[A-Za-z0-9'\-]+", out)[-1].lower()
        assert last not in {'and', 'or', 'the', 'a', 'to', 'for', 'with', 'per', 'that',
                            'is', 'of', 'in', 'on', 'when', 'not', 'should'}, out
