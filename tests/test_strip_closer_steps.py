"""Task 7.2: strip_closer_steps removes Closer_Steps and renumbers (design section 3)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules import qmetry_pattern_library as qpl  # noqa: E402
from modules.crawl_grounding import strip_closer_steps  # noqa: E402
from modules.data_models_v8 import TestCase, TestStep  # noqa: E402

CENTURY = qpl.century_trace_step()[0]
GENESIS = qpl.genesis_verify_step()[0]
DBTABLE = qpl.db_table_validation_step('activation')[0]

UI_A = 'Navigate to the Subscriber tile.'
UI_B = 'Open the Line Summary tab.'
UI_C = 'Verify the plan name is displayed.'


def _tc(summary, step_texts):
    steps = [TestStep(step_num=i + 1, summary=t) for i, t in enumerate(step_texts)]
    return TestCase(sno='TC_001', summary=summary, steps=steps)


def _summaries(tc):
    return [s.summary for s in tc.steps]


def _nums(tc):
    return [s.step_num for s in tc.steps]


def test_closers_at_start_removed_and_renumbered():
    tc = _tc('TC title', [CENTURY, GENESIS, UI_A, UI_B])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 2
    assert emptied is False
    assert _summaries(tc) == [UI_A, UI_B]
    assert _nums(tc) == [1, 2]


def test_closers_in_middle_removed_and_renumbered():
    tc = _tc('TC title', [UI_A, CENTURY, DBTABLE, UI_B])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 2
    assert emptied is False
    assert _summaries(tc) == [UI_A, UI_B]
    assert _nums(tc) == [1, 2]


def test_closers_at_end_removed_and_renumbered():
    tc = _tc('TC title', [UI_A, UI_B, UI_C, GENESIS])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 1
    assert emptied is False
    assert _summaries(tc) == [UI_A, UI_B, UI_C]
    assert _nums(tc) == [1, 2, 3]


def test_all_closer_tc_is_emptied_no_replacement_added():
    tc = _tc('TC title', [CENTURY, GENESIS, DBTABLE])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 3
    assert emptied is True
    assert tc.steps == []


def test_no_closer_tc_unchanged():
    tc = _tc('TC title', [UI_A, UI_B, UI_C])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 0
    assert emptied is False
    assert _summaries(tc) == [UI_A, UI_B, UI_C]
    assert _nums(tc) == [1, 2, 3]


def test_empty_tc_not_emptied():
    tc = _tc('TC title', [])
    removed, emptied = strip_closer_steps(tc)
    assert removed == 0
    assert emptied is False
    assert tc.steps == []


def test_tc_summary_untouched():
    tc = _tc('My important TC summary', [CENTURY, UI_A])
    strip_closer_steps(tc)
    assert tc.summary == 'My important TC summary'


def test_step_num_renumbered_from_arbitrary_values():
    steps = [
        TestStep(step_num=5, summary=CENTURY),
        TestStep(step_num=9, summary=UI_A),
        TestStep(step_num=14, summary=UI_B),
    ]
    tc = TestCase(sno='TC_001', summary='TC title', steps=steps)
    removed, emptied = strip_closer_steps(tc)
    assert removed == 1
    assert emptied is False
    assert _summaries(tc) == [UI_A, UI_B]
    assert _nums(tc) == [1, 2]
