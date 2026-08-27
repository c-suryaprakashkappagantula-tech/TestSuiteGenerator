"""Generation does not depend on global RNG state.

Guards the fix for a defect found on 27 August. `humanize_descriptions` picked its phrasing
with `random.choice` on the module-level generator, which is seeded from OS entropy at
interpreter start, so descriptions differed between runs.

That was not cosmetic. The CR cap in `test_engine` scores each test case on text that
includes its description, sorts on the score, and stamps the TC number into the title. Two
MWTGPROV-4406 cases scored within a point of each other, so the random wording decided which
became TC014 and which TC015 - regenerating a suite reassigned TC numbers and any external
reference to a TC id silently moved.

Measured before the fix: descriptions varied across processes in both CR features (4166 and
4406) on every run, and 4406's titles flipped in roughly 5 runs in 8. Non-CR features never
reach the humanizer, so they were never affected.

The first test here is the load-bearing one: it fails on the old code deterministically
rather than probabilistically, because it varies the global seed directly instead of hoping
two random draws disagree.
"""
import random

import pytest

from modules.humanizer import (
    _NEGATIVE_PREFIXES, _POSITIVE_PREFIXES, humanize_descriptions,
)
from modules.test_engine import TestCase

QUIET = lambda *a, **k: None

# Descriptions must start with 'To validate that' and the remainder must be at least 15
# characters and not start with a preposition, or humanize_descriptions skips the rewrite.
SOURCES = [
    ('Verify BCD change succeeds for an active TMO line',
     'To validate that bill cycle day changes are accepted for an active line', 'Happy Path'),
    ('Verify BCD change is rejected for a suspended line',
     'To validate that suspended lines refuse a bill cycle day change', 'Negative'),
    ('Verify usage notification threshold maps to the throttle',
     'To validate that usage notification thresholds map to the correct downstream throttle',
     'Happy Path'),
    ('Verify existing Change Feature behaviour is not impacted',
     'To validate that existing Change Feature behaviour for TMO lines is preserved',
     'Regression'),
    ('Verify BCD change at the month boundary',
     'To validate that day 31 requests are handled correctly at the month boundary',
     'Edge Case'),
]


def make_cases():
    """Fresh TestCase objects each call - humanize_descriptions mutates in place."""
    return [TestCase(sno=str(i + 1), summary=summary, description=description,
                     category=category)
            for i, (summary, description, category) in enumerate(SOURCES)]


def descriptions_with_global_seed(seed):
    random.seed(seed)
    cases = make_cases()
    humanize_descriptions(cases, log=QUIET)
    return [tc.description for tc in cases]


class TestHumanizerIsDeterministic:

    @pytest.mark.parametrize('seed', [0, 1, 42, 999, 123456])
    def test_global_rng_state_does_not_change_the_output(self, seed):
        """The rule this encodes: phrasing is a function of the test case, not of RNG state.

        `random.choice` on the module generator would return different prefixes for
        different global seeds, so this fails on the old code every time rather than
        depending on two draws happening to differ.
        """
        assert descriptions_with_global_seed(seed) == descriptions_with_global_seed(0)

    def test_repeated_calls_agree(self):
        first = descriptions_with_global_seed(7)
        second = descriptions_with_global_seed(7)
        third = descriptions_with_global_seed(7)
        assert first == second == third

    def test_the_rewrite_actually_happens(self):
        """Determinism is worthless if the pass silently stopped doing anything."""
        cases = make_cases()
        humanize_descriptions(cases, log=QUIET)
        assert all(not tc.description.startswith('To validate that')
                   for tc in cases), 'no description was rewritten'

    def test_phrasing_is_still_varied(self):
        """The pools exist to avoid every description opening the same way.

        A fix that pinned every test case to one prefix would be deterministic and would
        defeat the point, so this pins the intent as well as the mechanism.
        """
        cases = make_cases()
        humanize_descriptions(cases, log=QUIET)
        openings = {tc.description.split(' that ')[0].split(' the ')[0]
                    for tc in cases}
        assert len(openings) > 1, 'every description now opens identically'

    def test_a_prefix_from_the_right_pool_is_used(self):
        """Category still selects the pool; the seeding change must not cross the wires."""
        cases = make_cases()
        humanize_descriptions(cases, log=QUIET)
        negative = next(tc for tc in cases if tc.category == 'Negative')
        positive = next(tc for tc in cases if tc.summary.endswith('active TMO line'))

        def matches(pool, text):
            return any(text.startswith(p.split('%s')[0].strip())
                       for p in pool if p.split('%s')[0].strip())

        assert matches(_NEGATIVE_PREFIXES, negative.description)
        assert matches(_POSITIVE_PREFIXES, positive.description)

    def test_the_same_summary_always_gets_the_same_phrasing(self):
        """Stability is per test case, so a suite that gains a case does not rephrase the rest.

        Ordering still matters through the 'avoid repetition' bookkeeping, so this checks the
        first case specifically: it is chosen from the full pool in both runs.
        """
        one = make_cases()[:1]
        humanize_descriptions(one, log=QUIET)

        full = make_cases()
        humanize_descriptions(full, log=QUIET)

        assert one[0].description == full[0].description
