"""Titles are cut on word boundaries.

Guards the 20 August fix. Roughly 35 raw character slices built summaries with no regard for
word boundaries, producing titles that ended 'for dea', 'non-eli', 'any flo'.

A repair pass at the sanitiser chokepoint was tried and reverted: it could not tell a cut
fragment from a genuine final word and removed 'workflow' from 'Verify CR fix applies to
Reconnect workflow'. Prevention at the slice is the only approach that cannot damage a good
title, and the last two tests here encode that.
"""
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
