# Implementation Plan — QMetry-only unmatched TCs become NEW test cases

## Goal
Extend the TSG QMetry authored-steps supplement so that an eligible QMetry test case that
matches NO existing Chalk/TSG scenario (today TIER-3 = DROP) is ADDED to the suite as its
own NEW test case, built from its authored QMetry summary + steps, sequenced AFTER the
Chalk-derived TCs. Matched TCs (tier-1/tier-2) keep enriching their scenario and are NOT
re-added. Ineligible TCs (fail story-linkage pre-filter) are still dropped.

## Design decisions (made during exploration, grounded in the code)

1. **Where the new TCs come from — reuse the scenario pipeline, do NOT hand-build TestCases.**
   `attach_steps_to_scenarios(scenarios, authored, …)` in
   `modules/qmetry_steps_source.py` is called from the engine hook
   `data_first_engine.build_test_suite_v8` (around line 325) as
   `enrich_scenarios_with_qmetry(dimension_set.scenarios, jira, options, log)`. The
   `scenarios` argument IS the live `dimension_set.scenarios` list object (mutable, passed
   by reference). The decision: for each eligible-but-unmatched QMetry TC, construct a new
   `ExtractedScenario` (from `modules/data_models_v8.py`) carrying the QMetry summary as
   `title`, the authored step summaries as `steps_hint`, the authored last-step expected as
   `_qmetry_expected`, inferred `category`, and a QMetry traceability record as `source`, and
   APPEND it to the scenarios list. The existing `plan_combinations` →
   `build_test_cases` → `_build_scenario_tc` path then turns each appended scenario into a
   TC naturally. This reuses the exact step-splitting, derived-expected, summary/description,
   precondition, sno, and category machinery with zero duplication, and guarantees Chalk-first
   ordering because appended scenarios land after the Chalk-derived ones. Rationale: the
   current matcher mutates existing scenarios only; the minimal, lowest-risk extension is to
   add new scenario objects to the same list the engine already consumes, rather than build
   a parallel TestCase path (which would have to re-derive summary/preconditions/sno/category
   itself and could drift from the Chalk path).

2. **Reuse the existing step-splitting + expected logic — do NOT write a parallel splitter.**
   The authored steps are already split and expecteds already derived by the existing
   supplement, in this exact order:
   - Packed `"1. … 2. …"` splitting: `split_packed_steps(text)` (qmetry_steps_source.py),
     applied per-TC by `_apply_packed_rule(parsed)` which writes `tc['step_summaries']`
     (single step row → split; ≥2 rows → verbatim). `fetch_authored_steps` already runs
     `_apply_packed_rule`, so every TC dict returned carries `step_summaries`.
   - The ordered authored hint list: `_authored_hint_list(tc)` → `tc['step_summaries']`.
   - The last authored Expected Result: `_authored_expected(tc)` (last non-blank
     `steps[*]['expected']`).
   The new-TC builder MUST call `_authored_hint_list(tc)` for `steps_hint` and
   `_authored_expected(tc)` for `_qmetry_expected` — identical to what `_apply_match` does
   for matched TCs. The per-step interior expecteds are then produced for free by
   `_build_scenario_tc`'s existing hint-expected heuristic, and the last authored step's
   expected is overwritten by the existing block at tc_builder.py ~1262
   (`if getattr(scenario, '_qmetry_expected', '') and _n_authored:`).

3. **Category inference mapping** (must emit values in `_VALID_CATEGORIES` =
   `{'Happy Path','Negative','Edge Case','E2E','Regression','Rollback'}`, enforced by the
   engine's post-build normalizer): inspect the lower-cased QMetry TC summary:
   - contains `'regression'` or `'no impact'` → `'Regression'`
   - else contains `'end to end'`, `'end-to-end'`, `'e2e'`, or the phrase
     `'both '` … `' in the same file'` (mixed-file E2E) → `'E2E'`
   - else → `'Happy Path'`
   Implement as a small helper `_infer_qmetry_category(summary) -> str` in
   qmetry_steps_source.py.

4. **Traceability / source_type / grounding.** `create_traceability` in
   `modules/traceability.py` validates `source_type` against the frozen
   `ALLOWED_SOURCE_TYPES`. Decision: add `"QMetry Test Case"` to `ALLOWED_SOURCE_TYPES` and
   build the record as
   `create_traceability(source_type='QMetry Test Case', source_id=<TC key, e.g. MWTGMED-TC-1888>,
   extracted_text=<QMetry summary>, confidence=1.0)`. The TC key is the locator. For
   grounding: `modules/grounding_scorer.py` gives +20 for an authoritative source and +8
   for any other known source. A QMetry-authored new TC already scores +35 (real steps) +25
   (good expecteds) +10 (confidence≥0.8) +10 (≥3 steps) = 80 before the source term, well
   above `GATE_THRESHOLD` (40). Decision: add `"QMetry Test Case"` (and its lower-case form)
   to `_AUTHORITATIVE_SOURCES` in grounding_scorer.py so authored QMetry TCs get the full +20
   and read as first-class authored coverage — they are literally QA-authored ground truth.
   This keeps the gate decision robust and is consistent with how Chalk/Jira authored sources
   are treated.

5. **De-dup / no-duplicates.** `attach_steps_to_scenarios` already tracks matched TCs:
   tier-1 adds to `claimed_obligations` and enriches via `_apply_match`; tier-2 enriches via
   `_apply_match`. A TC is "matched" exactly when `_apply_match` returned True for it. The
   add-as-new-TC step runs AFTER both tiers and adds a scenario ONLY for eligible TCs that
   were never matched. Track matched TC keys in a `matched_tc_keys` set (add the key inside
   the tier-1 and tier-2 success branches) and, in the new final pass, iterate the same
   `eligible` list and append a new scenario for every `(tc_key, tc)` whose key is NOT in
   `matched_tc_keys`. This guarantees the mixed-file E2E (unmatched) is added once and
   MT→I / MO→O (matched) are not re-added. Also guard against a QMetry TC with no authored
   steps (`not _authored_hint_list(tc)`) — it is already filtered out when building
   `eligible`, so it can never become an empty new TC.

6. **Eligibility / no leakage.** Only TCs already in the `eligible` list (passed
   `_story_linkage_eligible` whole-key pre-filter against `validated_keys`) can become new
   TCs. Ineligible TCs are logged and skipped exactly as today — they never reach the new
   pass. Keep TIER-3 as a true DROP for any TC that fails eligibility (unchanged behavior).

7. **CHALK-FIRST / S.No sequencing.** New scenarios are appended to `dimension_set.scenarios`
   AFTER all existing (Chalk-derived + supplement) scenarios, so `plan_combinations`
   (`scenario_tcs=deduped_scenarios`, order preserved) and `build_test_cases` emit their TCs
   after the Chalk-derived ones. `_assign_serial_numbers` then numbers them in list order, so
   their `sno` follows the Chalk TCs. `_assert_chalk_coverage` (CHALK-GUARD) only verifies
   every Chalk scenario still has a TC; appended TCs never displace, reorder, or reduce Chalk
   coverage, so coverage stays 3/3 on 4628.

8. **Protect new QMetry scenarios from near-dup collapse.** `_deduplicate_scenarios`
   (combination_engine.py) Phase-2 can collapse a non-protected scenario at ≥75% word
   overlap. A genuinely-unmatched QMetry TC should survive. Decision: set
   `user_requested=True` on the new `ExtractedScenario` (the existing `_protected()` path
   exempts `user_requested` scenarios from Phase-2 near-dup collapse and from pruning/gating),
   which is the lightest-touch way to mark authored ground truth as protected. Exact-title
   dedup (Phase 1) still applies, which is correct (a true exact-title repeat should collapse).

9. **Fail-safe unchanged.** All new logic runs inside `attach_steps_to_scenarios` /
   `enrich_scenarios_with_qmetry`, which the engine hook already wraps in a try/except that
   logs one `[QMETRY] WARNING` and continues. QMetry down/absent/unknown-project →
   `fetch_authored_steps` returns `{}` → `eligible` is empty → no new scenarios → normal
   generation. No new top-level exception surface is introduced.

## Ordered implementation steps

- [ ] 1. Add the QMetry source type to the traceability allow-list.
      In `modules/traceability.py`, add `"QMetry Test Case"` to the `ALLOWED_SOURCE_TYPES`
      frozenset (and extend the module docstring's source-types list with a one-line entry).
      Files: modules/traceability.py
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/ -q`
      still passes (no test asserts the exact set contents; this is an additive change).

- [ ] 2. Treat QMetry-authored TCs as an authoritative grounding source.
      In `modules/grounding_scorer.py`, add `'QMetry Test Case'` to `_AUTHORITATIVE_SOURCES`
      and its lower-case form `'qmetry test case'` to the lower-case companion set used at
      line ~41-43.
      Files: modules/grounding_scorer.py
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/test_assertion_grounding.py tests/test_protected_gate.py -q`
      passes.

- [ ] 3. Add the category-inference helper to the QMetry module.
      In `modules/qmetry_steps_source.py`, add `_infer_qmetry_category(summary: str) -> str`
      implementing the mapping in design decision 3 (returns one of 'Regression' | 'E2E' |
      'Happy Path', all in `_VALID_CATEGORIES`). Place it near the other `_authored_*` helpers.
      Files: modules/qmetry_steps_source.py
      Verify: covered by the new unit tests added in step 6 (assert category per summary).

- [ ] 4. Add a new-scenario builder helper to the QMetry module.
      In `modules/qmetry_steps_source.py`, add
      `_build_new_scenario_from_tc(tc_key, tc, feature_key)` that returns an
      `ExtractedScenario` with: `title=tc['summary']`, `validation=tc['summary']`,
      `category=_infer_qmetry_category(tc['summary'])`,
      `steps_hint=list(_authored_hint_list(tc))`, `obligations=[]`, and
      `source=create_traceability('QMetry Test Case', tc_key, tc.get('summary') or tc_key,
      confidence=1.0)`; then set `sc._qmetry_expected = _authored_expected(tc)` and
      `sc.user_requested = True` (design decisions 2, 4, 8). Import `ExtractedScenario` from
      `.data_models_v8` and `create_traceability` from `.traceability` at module top (guard the
      imports so a bare unit import of the splitter still works — follow the existing
      lazy/top-import style already in the file). Guard against an empty summary by falling
      back to the TC key for `title`/`extracted_text` so `create_traceability` never raises on
      a blank `extracted_text`.
      Files: modules/qmetry_steps_source.py
      Verify: covered by the new unit tests in step 6.

- [ ] 5. Redirect TIER-3 from DROP to ADD-AS-NEW-TC inside the matcher.
      In `modules/qmetry_steps_source.py` `attach_steps_to_scenarios`:
      (a) introduce `matched_tc_keys = set()`; add `tc_key` to it in the tier-1 success branch
      (right after `claimed_obligations.add(pair); count += 1`) and in the tier-2 success
      branch (after `count += 1`).
      (b) after the tier-2 loop, add a final pass: for each `(tc_key, tc)` in `eligible` whose
      `tc_key not in matched_tc_keys`, call `_build_new_scenario_from_tc(tc_key, tc,
      <feature_key>)` and `scenarios.append(new_sc)`; log one
      `[QMETRY] TC %s unmatched — added as NEW test case (category=%s, %d steps)` line.
      Because `scenarios = list(scenarios or [])` rebinds a local copy, the append must
      propagate to the caller's list: change the function so it mutates the ORIGINAL list
      passed in — either take the original reference (do not wrap with `list(...)`; instead
      keep a separate immutable snapshot for iteration if needed) OR return the list of new
      scenarios and have `enrich_scenarios_with_qmetry` append them to the `scenarios` argument
      it received. Prefer the latter (return `(count, new_scenarios)` or append in
      `enrich_scenarios_with_qmetry`) because the engine hook passes the live
      `dimension_set.scenarios` object to `enrich_scenarios_with_qmetry`, which can append the
      new scenarios directly — keeping `attach_steps_to_scenarios`'s local `list(...)` copy
      for safe iteration over the match tiers. Update the `attach_steps_to_scenarios` docstring:
      TIER 3 now = ADD-AS-NEW-TC for eligible unmatched TCs; true DROP only for ineligible TCs.
      (c) In `enrich_scenarios_with_qmetry`, append the returned new scenarios to the
      `scenarios` argument (the live `dimension_set.scenarios` list) and fold their count into
      the `[QMETRY] authored steps attached to N scenario(s)` log (e.g. also log
      `… and M new QMetry-only test case(s) added`).
      Files: modules/qmetry_steps_source.py
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/test_qmetry_steps_source.py -q`
      — all existing 16 tests still pass (matched TCs still only enrich; the shared-linkage
      no-positional test's unmatched TC now becomes a NEW scenario rather than silently
      dropped, so update that test's expectation per step 6, or keep its assertion on the
      target scenario's `steps_hint` and additionally assert one scenario was appended).

- [ ] 6. Add unit tests for the add-as-new-TC behavior (mock client, no network).
      In `tests/test_qmetry_steps_source.py`, add tests following the existing hermetic
      patterns (`_scenario`, `_authored`, `attach_steps_to_scenarios` with `validated_keys`,
      the `_FakeClient` monkeypatch for the fetch/engine path). Cover:
      (a) `test_unmatched_eligible_tc_added_as_new`: a scenario list with an MT→I obligation
      scenario plus one authored TC that matches it (tier-1, enriched, not re-added) AND one
      authored TC `'Both MT and MO records in the same file convert correctly'` that matches
      nothing; assert EXACTLY ONE new scenario is appended, its `title` equals the QMetry
      summary, its `category == 'E2E'`, its `steps_hint` equals the authored steps, its
      `_qmetry_expected` equals the authored last expected, its `source.source_type ==
      'QMetry Test Case'` with `source_id` = the TC key, and the matched MT→I scenario is NOT
      duplicated (still exactly one scenario with that obligation).
      (b) `test_matched_tc_not_added_as_new`: both authored TCs match (tier-1/tier-2); assert
      NO new scenario is appended and both matched scenarios carry their authored hints.
      (c) `test_ineligible_tc_not_added`: an authored TC whose Story Linkages do NOT contain
      any `validated_keys` (wrong-story-linkage) and matches nothing; assert it is NOT added
      and NOT enriched (eligibility drop preserved).
      (d) `test_category_inference`: unit-test `_infer_qmetry_category` for 'regression',
      'no impact', 'end to end', 'both … in the same file', and a plain happy-path summary.
      (e) `test_new_tc_builds_through_scenario_pipeline`: build a new scenario via the
      add-as-new path, run it through `_build_scenario_tc(new_sc, _Jira(), 'Mediation',
      api_context={})`, and assert the resulting TC has the authored steps as its step
      summaries, the authored expected on the last authored step, a non-empty `summary`, and a
      `category` in `_VALID_CATEGORIES`.
      Decide which helper signature step 5 exposes (append-in-`enrich` vs return-list) and
      write the tests against it; if `attach_steps_to_scenarios` returns `(count,
      new_scenarios)`, assert on the returned `new_scenarios`, else pass a live list and assert
      on its growth.
      Files: tests/test_qmetry_steps_source.py
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/test_qmetry_steps_source.py -q`
      — all tests (existing + new) pass.

- [ ] 7. Full suite regression + CHALK-FIRST invariant check.
      Run the whole TSG test suite to confirm nothing regressed (CHALK-GUARD, grounding,
      dedup, snapshot, protected-gate). If `tests/test_suite_snapshot.py` or
      `tests/test_determinism.py` fail because a fixtureed feature now gains a QMetry-only TC,
      inspect whether a snapshot legitimately needs regenerating (it should NOT for cache-only
      features that have no live QMetry session — the fetch returns `{}` offline, so suite
      output is unchanged); regenerate only if the change is the intended added TC and record
      why in review.md.
      Files: (none — verification only)
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/ -q`
      passes (or any failure is a deliberately-regenerated snapshot, justified in writing).

## Notes / assumptions
- The engine hook already guards the whole supplement in try/except and only runs when
  `dimension_set.scenarios` is non-empty; no change to the hook's fail-safe is required beyond
  appending new scenarios inside `enrich_scenarios_with_qmetry` (which is inside that guard).
- Offline/CI (no QMetry session) is unaffected: `fetch_authored_steps` returns `{}`,
  `eligible` is empty, zero new scenarios — existing snapshot/determinism tests stay valid.
- Category strings are emitted exactly as `_VALID_CATEGORIES` expects, so the engine's
  post-build category normalizer is a no-op for them.
