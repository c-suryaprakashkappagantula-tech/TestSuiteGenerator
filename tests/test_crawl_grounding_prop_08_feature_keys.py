# -*- coding: utf-8 -*-
"""Property test: feature classification ignores feature keys (task 7.4).

Feature: tsg-nbop-crawl-grounded-ui-steps, Property 8: Feature classification ignores feature keys
**Validates: Requirements 1.3**

For any suite, replacing ``feature_id`` and the ``MWTGPROV-<n>`` key text inside
TC summaries with any other ``MWTGPROV-<m>`` string leaves
``modules.crawl_grounding.is_ui_feature(suite)`` unchanged. The suite is
deep-copied before the ids are mutated so the original is left untouched.
"""
import copy
import os
import re
import sys

from hypothesis import given, settings, strategies as st

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TSG_ROOT = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(_TSG_ROOT)
for _p in (_TESTS_DIR, _TSG_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.crawl_grounding import is_ui_feature  # noqa: E402
from crawl_grounding_strategies import suites  # noqa: E402

_MWTGPROV_RE = re.compile(r'MWTGPROV-\d+')


@settings(max_examples=200, deadline=None)
@given(data=st.data(), ui=st.booleans(), new_num=st.integers(min_value=10000, max_value=99999))
def test_feature_classification_ignores_feature_keys(data, ui, new_num):
    # Draw both UI and non-UI suites so both branches of is_ui_feature are hit.
    suite = data.draw(suites(ui), label='suite')
    before = is_ui_feature(suite)

    new_key = f'MWTGPROV-{new_num}'

    # Deep-copy so the original suite is never mutated.
    mutated = copy.deepcopy(suite)
    mutated.feature_id = new_key
    for tc in mutated.test_cases:
        tc.summary = _MWTGPROV_RE.sub(new_key, tc.summary)

    after = is_ui_feature(mutated)

    assert after == before, (
        f'classification changed after feature-key swap: {before!r} -> {after!r}'
    )

    # The original suite must be untouched by the deep-copy mutation.
    assert is_ui_feature(suite) == before
