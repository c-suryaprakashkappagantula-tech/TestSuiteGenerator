# Verification — QMetry-only unmatched TCs become NEW test cases

Feature: **MWTGPROV-4628** (PI-60). All runs via the worktree venv
`c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe` from inside the
worktree `…\.worktrees\tsg-qmetry-newtcs`.

## 1. py_compile
`python -m py_compile modules/traceability.py modules/grounding_scorer.py
modules/qmetry_steps_source.py tests/test_qmetry_steps_source.py` → **PY_COMPILE OK**.

## 2. Existing hermetic tests (16 QMetry + 11 assertion-grounding) + protected-gate
`python -m pytest tests/test_qmetry_steps_source.py tests/test_assertion_grounding.py
tests/test_protected_gate.py -q`
- Baseline (before edits): **56 passed**.
- After edits: **61 passed** (56 + 5 new tests). Zero regressions.
- `tests/test_qmetry_steps_source.py` alone → **21 passed** (16 original + 5 new).
- `tests/test_assertion_grounding.py` alone → **11 passed**.

## 3. New unit tests (mock client, no network)
Added to `tests/test_qmetry_steps_source.py`:
- `test_unmatched_eligible_tc_added_as_new` — one matched TC (MT→I, tier-1, enriched, NOT
  re-added) + one unmatched eligible TC (mixed-file "Both MT and MO") → exactly **one** new
  scenario appended; title/validation = QMetry summary; `category == 'E2E'`; `steps_hint` =
  authored steps; `_qmetry_expected` = authored last expected; `source.source_type ==
  'QMetry Test Case'` with `source_id` = TC key; matched scenario not duplicated.
- `test_matched_tc_not_added_as_new` — both TCs match (tier-1 + tier-2) → **no** new TCs.
- `test_ineligible_tc_not_added` — wrong story-linkage TC → not enriched AND not added.
- `test_category_inference` — 'regression'/'no impact'→Regression, 'end to end'/mixed-file
  →E2E, else→Happy Path.
- `test_new_tc_builds_through_scenario_pipeline` — a new scenario runs through the engine's
  own `_build_scenario_tc`: authored steps survive, authored expected lands on the last
  authored step, non-empty summary, category in `_VALID_CATEGORIES`.
All pass (part of the 21 above).

## 4. Broader non-cache regression — zero NEW regressions
Oracle (fast hermetic): `python -m pytest tests/test_titles.py tests/test_determinism.py -q`
→ **35 passed** (matches the documented baseline of 35 exactly).

Full runnable suite (ignoring the 28 documented `shared`-import collection-error files from
`tests/_baseline_failures.txt` and the slow `test_suite_snapshot.py`):
`python -m pytest tests/ -q -p no:cacheprovider @ignore`
- With my edits: **35 failed, 239 passed, 4 skipped**.
- With my 4 edited files git-stashed (baseline): **35 failed, 239 passed, 4 skipped**.
- `Compare-Object` of the two FAILED node-id lists → **empty** (no `=>` entries): the 35
  failures are PRE-EXISTING (cache/DB-backed suite builds) and identical before/after. **Zero
  new regressions.** No snapshots were regenerated.

## 5. Live end-to-end re-run — MWTGPROV-4628 PI-60
`python run_headless.py MWTGPROV-4628 PI-60` (QMetry session valid; authenticated via
`MDA-Jira-Dashboard\storage_state.json`; folder resolved id=129316 for MWTGMED-5214;
read-only export; per-run cache reused).

Note on cache: the clean worktree's `tsg_cache.db` (gitignored, 168 KB) carries no cached
Jira/Chalk for 4628, so a straight run yields 0 TCs. For this live evidence the main repo's
`tsg_cache.db` (which DOES hold 4628's Jira+Chalk) was temporarily copied into the worktree
and the worktree's original restored afterward. `tsg_cache.db` is gitignored, so git stayed
clean; `CABOT_CHALK_DB.db` (a parent dirty file) was NOT touched — 4628's data lives only in
`tsg_cache.db`.

### (a) Previously-dropped mixed-file TC now a NEW test case
`[QMETRY] TC MWTGMED-TC-1888 — no scenario ≥ similarity threshold 0.60 — dropped` is now
followed by `[QMETRY] TC MWTGMED-TC-1888 unmatched — added as NEW test case (category=E2E,
4 steps)`. In the suite it is **TC17 [E2E] Verify_Both_MT_and_MO_records_in_the_same_file_
convert_correctly**, carrying its 4 authored steps (sensible E2E category).

### (b) MT→I and MO→O are Chalk-derived TCs ENRICHED, NOT duplicated
`[QMETRY] TC MWTGMED-TC-1887 (value-pair) → scenario "Mediation TMO PRR processing…"
(4 authored steps)` and `TC MWTGMED-TC-1886 (value-pair) → scenario "…"` — both ENRICH their
matched Chalk scenarios (tier-1) and are NOT emitted as new TCs. Similarity-skip lines
(`TC-1890`, `TC-1891` "already has a 5-step hint") confirm matched TCs enrich-only.

### (c) CHALK-GUARD full coverage
`[CHALK-GUARD] Chalk coverage OK: 3/3 scenarios -> TCs` (unchanged, after the additions).

### (d) No unrelated/other-feature TCs leaked in
Only TCs story-linked to MWTGMED-5214 (eligible) were considered; the 8 added TCs
(MWTGMED-TC-1888…1895) all belong to this feature's QMetry folder (id=129316, MWTGMED-5214).
Ineligible/other-folder TCs were never fetched or added.

### (e) No generic-garbage steps on the new TCs
`[GROUND] Gate threshold=40 — all 17 TCs passed`, suite grounding **99.7%**. Each new TC
carries its authored QMetry steps (2–4 steps each), not boilerplate.

### Before / after TC list (source)
BEFORE (my 4 files git-stashed, same real cache) → **9 TCs**, CHALK-GUARD 3/3:
```
TC01 [Regression] input_other_absent_output_BAU                (Chalk/AC)
TC02 [Happy Path] Mediation_TMO_PRR_processing_is_active_01     (Chalk, MO→O enriched)
TC03 [Happy Path] Mediation_TMO_PRR_processing_is_active        (Chalk, MT→I enriched)
TC04 [Happy Path] Mediation_processing_active_VZW_and_CBRS       (Chalk/AC)
TC05 [Happy Path] MMS_TYPE_INDICATOR_value_MT_is_converted_to_I  (Chalk/AC)
TC06 [Happy Path] No_impact_to_Verizon_or_CBRS_billing_records   (Chalk/AC)
TC07 [Happy Path] No_loss_of_existing_functionality              (Chalk/AC)
TC08 [E2E]        The_MT_to_I_conversion_end_to_end_in_the_TMO    (Chalk/AC)
TC09 [Regression] Unchanged_field_values_elsewhere               (Chalk/AC)
```
AFTER (with the change) → **17 TCs**, CHALK-GUARD 3/3. TC01–TC09 identical and in the same
order (Chalk-first preserved; MT→I / MO→O still enriched, not duplicated). The 8 ADD-AS-NEW-TC
entries, all `source_type = "QMetry Test Case"` with the TC key as locator, follow:
```
TC10 [Negative]   Negative_malformed_empty_MMS_TYPE_INDICATOR        (QMetry TC MWTGMED-TC-1889)
TC11 [Regression] Regression_end-to-end_TMO_PRR_processing            (QMetry TC MWTGMED-TC-1890)
TC12 [Happy Path] Field_position_format_of_call_direction_ind         (QMetry TC MWTGMED-TC-1891/1892)
TC13 [Happy Path] Case_sensitivity_of_output_value                    (QMetry TC …)
TC14 [Regression] No_impact_to_CBRS_billing_records                   (QMetry TC …)
TC15 [Regression] No_impact_to_Verizon_billing_records                (QMetry TC …)
TC16 [Regression] Non-MMS_other_MMS_TYPE_INDICATOR_values_pass_through (QMetry TC MWTGMED-TC-1894)
TC17 [E2E]        Both_MT_and_MO_records_in_the_same_file_convert      (QMetry TC MWTGMED-TC-1888)
```
(The TC10–16 ↔ TC-key mapping is per the `[QMETRY] … added as NEW test case` log lines:
TC-1889 Happy Path/3, TC-1890 Regression/3, TC-1891 Regression/3, TC-1892 Happy Path/2,
TC-1893 Happy Path/3, TC-1894 Regression/4, TC-1895 Happy Path/3, TC-1888 E2E/4.)

**De-dup proof:** the engine log shows the matched value-pair TCs (TC-1886, TC-1887) only as
`→ scenario "…" (enriched)` lines and NOT in the `added as NEW test case` list; the final
tally `authored steps attached to 2 scenario(s) and 8 new QMetry-only test case(s) added`
confirms 2 enrich + 8 add, with no overlap.

## 6. Fail-safe (bad/empty cookies)
Method: forced `load_qmetry_cookies` to raise `QMetrySessionError` (exactly how a bad/empty
`storage_state.json` behaves when no candidate authenticates), then drove
`enrich_scenarios_with_qmetry` through the engine hook's try/except.
Result (`_tmp_failsafe.py`): `crashed=False`, hook caught the error, a `[QMETRY] WARNING:
supplement skipped …` line was logged, **scenarios before == after (no new TCs)**, and the
seeded scenario was untouched → **FAILSAFE_OK = True**. Normal generation continues.
(Also covered hermetically by `test_no_session_is_fail_safe`.)
