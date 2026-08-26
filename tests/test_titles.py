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
        assert truncate_at_word(source, 49) == 'Verify the flow is blocked when the line is on'

    def test_untruncated_text_keeps_its_own_last_word(self):
        source = 'Verify the flow is blocked when the line is on a'
        assert truncate_at_word(source, 48) == source

    def test_falls_back_when_there_is_no_space_to_cut_on(self):
        """Underscore-joined E2E titles have no spaces and must not be mangled."""
        source = 'New_MVNO_Guarented_Delivery_flow_in_NBOP_not_working'
        assert len(truncate_at_word(source, 40)) == 40

    @pytest.mark.parametrize('value', ['', None])
    def test_empty_input_is_safe(self, value):
        assert truncate_at_word(value, 80) == value
