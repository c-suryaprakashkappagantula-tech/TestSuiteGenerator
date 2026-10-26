# Review — QMetry-only unmatched TCs become NEW test cases

Branch: `feat/tsg-qmetry-newtcs` · Commit: `ebbab9a`
Verdict: **APPROVED** (no blocking findings)

## Scope read
- Plan: `.agents/tasks/plan.md`
- Verification note: `.agents/tasks/verification.md`
- Commit message `ebbab9a` (recorded py_compile / pytest / live-4628 / fail-safe evidence)
- Full diff of `modules/traceability.py`, `modules/grounding_scorer.py`,
  `modules/qmetry_steps_source.py`, `tests/test_qmetry_steps_source.py`
- Unchanged callers to establish consequences: `modules/data_first_engine.py` (the hook at
  line 323-328 that passes the live `dimension_set.scenarios` list and re-reads it for
  `plan_combinations`/`build_test_cases`), `modules/combination_engine.py`
  (`_deduplicate_scenarios` path), `modules/data_models_v8.py` (`ExtractedScenario` fields).

## Evidence relied on (not re-run, per instructions)
- `py_compile` of the 4 edited files → OK (commit §1).
- `pytest tests/test_qmetry_steps_source.py tests/test_assertion_grounding.py
  tests/test_protected_gate.py` → 61 passed (56 baseline + 5 new) (commit §2/§3).
- Oracle `tests/test_titles.py + tests/test_determinism.py` → 35 passed == baseline; full
  runnable suite 35 failed/239 passed/4 skipped identical with and without the edits
  (Compare-Object empty) → zero new regressions (commit §4).
- Live `run_headless.py MWTGPROV-4628 PI-60`: 9 TCs → 17 TCs, CHALK-GUARD 3/3, before/after
  TC list, engine tally "2 scenario(s) enriched and 8 new QMetry-only test case(s) added"
  (commit §5 / verification.md §5 (a)-(e)).
- Fail-safe forced `QMetrySessionError`: no crash, `[QMETRY] WARNING`, scenarios before==after
  (commit §6 / verification.md §6).

## Blocking checks

1. **CHALK-FIRST preserved — PASS.** New scenarios are returned by
   `attach_steps_to_scenarios` and `enrich_scenarios_with_qmetry` appends them with
   `scenarios.extend(new_scenarios)` onto the live `dimension_set.scenarios` list the engine
   hook passes by reference (data_first_engine.py:326), i.e. AFTER the Chalk-derived
   scenarios. `plan_combinations`/`build_test_cases` then emit their TCs in list order.
   Live run: TC01-TC09 identical and in the same order, CHALK-GUARD `3/3` before and after;
   the 8 QMetry TCs are TC10-TC17. No displacement/reorder/reduction of Chalk coverage.

2. **NO DUPLICATES — PASS.** A `matched_tc_keys` set is populated in both the tier-1 and
   tier-2 success branches; the tier-3 add-pass iterates `eligible` and skips any
   `tc_key in matched_tc_keys`. Matched TCs therefore only ENRICH and are never re-added.
   Live: MT->I (TC-1887) and MO->O (TC-1886) enriched via value-pair, not duplicated; the
   unmatched mixed-file E2E MWTGMED-TC-1888 added exactly once (TC17). Engine tally
   "2 enrich + 8 add" confirms no overlap. Unit test `test_matched_tc_not_added_as_new`
   asserts `new == []` when both TCs match; `test_unmatched_eligible_tc_added_as_new` asserts
   exactly one new scenario and the matched scenario not duplicated.

3. **NO LEAKAGE — PASS.** Only TCs in the `eligible` list reach tier-3, and `eligible` is
   built behind the unchanged `_story_linkage_eligible` whole-key pre-filter; ineligible TCs
   log "not story-linked … ineligible" and never reach any tier. Unit test
   `test_ineligible_tc_not_added` (wrong `validated_keys`) asserts not enriched and not added.
   Live: only MWTGMED-5214-linked TCs considered.

4. **FAIL-SAFE unchanged — PASS.** All new logic lives inside `attach_steps_to_scenarios`/
   `enrich_scenarios_with_qmetry`, which the engine hook still wraps in the same try/except
   (data_first_engine.py:324-328). No new top-level exception surface. QMetry
   down/absent/unknown-project → `fetch_authored_steps` returns `{}` → `eligible` empty →
   zero new scenarios. Recorded fail-safe run: crashed=False, one `[QMETRY] WARNING`,
   scenarios before==after; hermetic `test_no_session_is_fail_safe` also covers it.

5. **Tier-1/tier-2 matcher semantics UNCHANGED — PASS.** The diff adds only two
   `matched_tc_keys.add(tc_key)` lines inside the existing success branches and changes the
   return to `(count, new_scenarios)`. The value-pair extraction, obligation claiming,
   similarity threshold, strict-highest-scorer and tie-leaves-unmatched logic are byte-for-byte
   unchanged. Only the tier-3 outcome moved from DROP to ADD-for-eligible.

6. **Step splitting / expected logic REUSED — PASS.** `_build_new_scenario_from_tc` calls the
   existing `_authored_hint_list(tc)` for `steps_hint` and `_authored_expected(tc)` for
   `_qmetry_expected` — identical to what `_apply_match` attaches for matched TCs — and builds
   a standard `ExtractedScenario` that flows through the engine's own
   `plan_combinations`/`_build_scenario_tc`. No parallel splitter. `test_new_tc_builds_through_
   scenario_pipeline` runs a new scenario through `_build_scenario_tc` and asserts authored
   steps/expected survive.

7. **New unit tests — PASS.** `tests/test_qmetry_steps_source.py` adds
   `test_unmatched_eligible_tc_added_as_new` (one matched enrich-only + one unmatched added;
   exactly one new TC; title/validation/category=E2E/steps_hint/_qmetry_expected/source_type
   'QMetry Test Case'/source_id=TC key; matched not duplicated),
   `test_matched_tc_not_added_as_new` (no new TCs), `test_ineligible_tc_not_added`
   (wrong-linkage not added/not enriched), `test_category_inference` (regression/no-impact →
   Regression, end-to-end/mixed-file → E2E, else Happy Path), and
   `test_new_tc_builds_through_scenario_pipeline`. The QMetry client is mocked via the existing
   `attach_steps_to_scenarios(authored_dict, …)` path and `_FakeClient` — no network. Existing
   tests were updated to unpack the new `(n, new)` return and assert `new == []` where no add
   is expected.

8. **4628 live-run evidence supports (a)-(e) — PASS.** verification.md §5 records: (a)
   MWTGMED-TC-1888 now TC17 [E2E] with 4 authored steps; (b) MT->I/MO->O enriched via
   value-pair, not duplicated; (c) CHALK-GUARD 3/3; (d) only MWTGMED-5214-linked TCs, no
   leakage; (e) grounding gate all 17 TCs passed at 99.7%, authored steps not boilerplate.

## Non-blocking observations (not findings, no action required)
- A second eligible TC sharing an already-claimed value pair hits the tier-1 "already claimed
  — dropped" branch, is not added to `matched_tc_keys`, and so becomes its own new TC in
  tier-3. This matches the plan's stated rule (eligible + did-not-enrich → ADD) and the user
  intent (don't silently drop eligible authored TCs); it is intended behavior, not a defect.
- `_qmetry_expected` is set as an ad-hoc attribute on `ExtractedScenario` (not a declared
  dataclass field). This mirrors how the pre-existing `_apply_match` sets it on matched
  scenarios, so it is consistent with the established pattern.

## Conclusion
All eight blocking checks pass against the diff, the unchanged caller/guard paths, and the
recorded py_compile/pytest/live-4628/fail-safe evidence. No reachable correctness, security,
or data regression introduced. **APPROVED.**
