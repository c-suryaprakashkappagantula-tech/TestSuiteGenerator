# Verification — Feature-class-aware step generation for Mediation/PRR/billing (MWTGPROV-4628)

Branch: `feat/tsg-mediation-steps`
Worktree: `c:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\tsg-mediation-steps`
venv python: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`

## What changed (root causes fixed)

1. **Classification.** `modules/qmetry_pattern_library.py` gained a pure, no-I/O detector
   `is_mediation_feature(*texts)` (signals: `mediation`, `prr`, `amdocs`, `billing record`,
   `call_direction_ind`, `mms_type_indicator`, `tmo_prr_processing`, `biller sftp`, and the
   component tokens `wpamed` / `[med` / `, med` / `med,`). A bare `nslnm` does NOT match.
   `modules/tc_builder.py::classify_feature` now runs a **RULE 0** mediation check BEFORE the
   `[NSLNM]→api` rule and returns `classification='batch_file'` for a mediation feature.
   4628 previously logged `Classification: api (confidence=0.95, api_kw=['NSLNM'])`; it now
   logs `Classification: batch_file`.

2. **Step template.** `modules/step_templates.py` `get_step_chain` `batch_file` branch now calls
   a new `_prr_mediation_steps(...)` helper that emits the PRR flow —
   (1) place/ingest the TMO PRR input file → (2) run `TMO_PRR_Processing_01` mediation flow →
   (3) retrieve the processed Amdocs TMO billing output record(s) → (4) inspect
   `call_direction_ind` / the scenario output field and compare against the pre-change baseline.
   No-impact/regression scenarios (BAU / VZW / CBRS / "unchanged") emit the baseline-compare
   form (process → retrieve Amdocs → compare field-by-field/byte-for-byte, assert UNCHANGED).
   A module-private `_BANNED_MEDIATION_PHRASES` frozenset + `_assert_no_banned_mediation()` makes
   the NSL-provisioning chain **structurally unreachable** for the mediation class (asserts at the
   end of the batch branch). The obligation path (`_build_obligation_steps`) and the plain
   fallback path (`_build_scenario_tc`) both route to `batch_file` via `is_mediation_feature`.
   Both `build_downstream_verification_steps` splice sites (positive ~line 1444 and negative
   `_build_negative_tc` ~line 1834) are guarded to skip entirely for a mediation feature, so
   Service Grouping / Century Report / TRANSACTION_HISTORY can never re-append.

3. **Splitter/humanizer artifact.** `TestStep` gained `authored: bool = False`
   (`modules/data_models_v8.py`). Authored QMetry steps (`steps_hint`) are now stamped
   `authored=True` in both builders. `modules/output_sanitizer.py::polish_suite` computes
   `_lock_summary = authored or _is_imperative(summary)` and suppresses the `'Verify that ' +
   lower()` prefix/recase on those steps (the `"Verify that let Mediation…"` artifact). Expected-
   result derivation, char-cleanup and length-trim still apply.

## Preserved
- Obligation assertion steps still emit `Set/Verify <field>=<value>` with `obligation_id`
  (R2/R5) — verified in the live run (TC1 steps 5-8) and `test_assertion_grounding.py`.
- QMetry authored steps verbatim (TC2/TC3 and TC10-17) — now WITHOUT the "Verify that" artifact.
- CHALK-GUARD: live run logged `Chalk coverage OK: 3/3 scenarios -> TCs`; 17 TCs produced,
  source ordering unchanged (step CONTENT only).
- Genuine non-mediation NSL features stay `api` (classification test
  `test_pure_nsl_provisioning_stays_api`).

## Commands run and results

- **py_compile** (all edited modules + 3 new test files): exit 0.
- **New tests** `tests/test_mediation_classification.py` + `tests/test_mediation_steps_structural.py`
  + `tests/test_authored_step_verbatim.py`: **8 passed**.
- **Targeted gate** `test_assertion_grounding.py` + `test_qmetry_steps_source.py` + the 3 new files:
  **40 passed** (32 baseline + 8 new).
- **Broad hermetic set** `pytest tests/ -k "not slow and not cache"` (ignoring the 28 documented
  `shared`-ModuleNotFoundError collection files + the slow/pre-RED `test_suite_snapshot.py`):
  **250 passed, 1 skipped, 7 failed**.
  - The 7 failures are all PRE-EXISTING (confirmed by re-running them against a `git stash` of
    only the 5 edited modules — identical 7 failures on pre-change code):
    - 6 × `test_contract_bridge_ui_exports.py` → `ModuleNotFoundError: No module named 'shared'`
      (same environment/path gap as the 28 baseline collection errors; `shared/` lives at the
      MAIN repo root, absent from the worktree).
    - 1 × `test_ac_gate.py::TestEnumerationHeaders::test_4349_role_list_survives` — unrelated to
      this work (AC enumeration-header behaviour for feature 4349).
  - **ZERO new regressions.** No snapshots were regenerated.

## Live headless run — `python run_headless.py MWTGPROV-4628 PI-60`

NOTE on data: the worktree's checked-in `tsg_cache.db` (168 KB stub) and
`CABOT_CHALK_DB.db` (950 KB stub) contain NO 4628 data, so a run against them yields 0 TCs and
no xlsx (as plan.md warned). To perform a genuine live run entirely inside the worktree, the
MAIN checkout's caches were COPIED into the worktree (read-only from main; `tsg_cache.db` is
gitignored). `CABOT_CHALK_DB.db` is git-TRACKED, so after the runs it was restored with
`git checkout -- CABOT_CHALK_DB.db` and the stub `tsg_cache.db` was restored — neither is part
of this commit, and the MAIN checkout was never written to.

Result: **17 TCs | 70 steps**, `Classification: batch_file`, `CHALK-GUARD ... 3/3 scenarios -> TCs`.

Produced xlsx (AFTER):
`outputs/MWTGPROV-4628_60.2_-_New_MVNO_-_Update_TMO_MMS_TYPE_INDICATOR_Values_for_Amdocs_call_direction_ind_20261026_154229.xlsx`
(absolute: the worktree `outputs/` folder; untracked build artifact.)

### Banned-phrase grep across all 17 TCs (Test Cases sheet)
`Trigger API`, `NSL processes request with 200 OK`, `Transaction ID generated`,
`Download Century Report`, `Service Grouping`, `NE Portal`, `TRANSACTION_HISTORY`, `LINE_HISTORY`

- BEFORE (pre-change code, same cache): **61 hits**
  (`Trigger API`=6, `NSL...200 OK`=5, `Transaction ID generated`=5, `Download Century Report`=5,
  `Service Grouping`=10, `NE Portal`=10, `TRANSACTION_HISTORY`=10, `LINE_HISTORY`=10)
- AFTER (this change): **0 hits** — ZERO across all 17 TCs.

PRR-flow verb counts AFTER: ingest=19, mediation=41, tmo_prr_processing=20, amdocs=42,
call_direction_ind=24, baseline=13.

### TC1 before/after (evidence)
BEFORE:
```
1 | Trigger API: input=other/absent -> output=BAU | NSL processes request with 200 OK. Transaction ID generated
2 | Download Century Report (Service Grouping) | SERVICE_GROUPING HTML downloaded
3 | Verify NE Portal transactions | NE Portal shows transaction completed
4 | Validate Service Grouping for: input=other/absent -> output=BAU | other/absent value
5 | Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY) | TRANSACTION_HISTORY and LINE_HISTORY show ...
6 | Set input = other/absent | Input is set to other/absent.
7 | Verify output = BAU | output equals BAU in the produced record.
8 | Set MMS_TYPE_INDICATOR = other/absent | Input MMS_TYPE_INDICATOR is set to other/absent.
9 | Verify call_direction_ind = BAU | call_direction_ind equals BAU in the produced record.
```
AFTER:
```
1 | Place/ingest the TMO PRR input file containing absent onto the biller SFTP | PRR input file is staged and picked up by Mediation for processing
2 | Run the TMO_PRR_Processing_01 mediation flow (let Mediation process the file) | Mediation processes the PRR file and emits the Amdocs TMO billing record(s)
3 | Retrieve the processed Amdocs TMO billing output record(s) for the test data | Amdocs TMO billing output record(s) are produced and available
4 | Inspect the call_direction_ind field in the Amdocs output and compare against the pre-change baseline | call_direction_ind = output in the Amdocs TMO billing output record; all other fields remain as BAU
5 | Set input = other/absent | Input is set to other/absent.
6 | Verify output = BAU | output equals BAU in the produced record.
7 | Set MMS_TYPE_INDICATOR = other/absent | Input MMS_TYPE_INDICATOR is set to other/absent.
8 | Verify call_direction_ind = BAU | call_direction_ind equals BAU in the produced record.
```

### TC8 before/after (evidence)
BEFORE:
```
1 | Trigger API: The 'MT' to 'I' conversion end to end ... | NSL processes request with 200 OK. Transaction ID generated
2 | Download Century Report (Service Grouping) | SERVICE_GROUPING HTML downloaded
3 | Verify NE Portal transactions | NE Portal shows transaction completed
4 | Validate Service Grouping for: The 'MT' to 'I' conversion ... | Validate the 'MT' to 'I' conversion end to end ...
5 | Check audit logs (TRANSACTION_HISTORY & LINE_HISTORY) | TRANSACTION_HISTORY and LINE_HISTORY show ...
```
AFTER:
```
1 | Place/ingest the TMO PRR input file containing conversion = end onto the biller SFTP | PRR input file is staged and picked up by Mediation for processing
2 | Run the TMO_PRR_Processing_01 mediation flow (let Mediation process the file) | Mediation processes the PRR file and emits the Amdocs TMO billing record(s)
3 | Retrieve the processed Amdocs TMO billing output record(s) for the test data | Amdocs TMO billing output record(s) are produced and available
4 | Inspect the call_direction_ind field (scenario output) in the Amdocs output and compare against the pre-change baseline | Validate the 'MT' to 'I' conversion end to end in the TMO billing record delivered to Amdocs.
```

### Authored QMetry TCs — artifact removed
TC2 (MO→O) / TC3 (MT→I) step 2 now reads verbatim `Let Mediation run the TMO_PRR_Processing_01
flow.` — previously `Verify that let Mediation run the TMO_PRR_Processing_01 flow.`. The
"Verify that let Mediation…" artifact is gone. TC10-17 (QMetry Test Case) are clean and PRR-shaped.

## Known cosmetic imperfections (NOT NSL boilerplate, NOT the reported artifacts)
- The crude input-field extractor for plain Jira-AC fallback TCs occasionally yields awkward
  phrases in step 1, e.g. TC7 `...containing loss = of...`, TC8 `...containing conversion = end...`.
  These are PRR-flow steps (ingest → TMO_PRR_Processing_01 → retrieve Amdocs → baseline) with
  imperfect value parsing — no banned phrase, no NSL step.
- TC6 (`No impact to Verizon or CBRS billing records`) routed to a notification-content template
  (VZW notification-format comparison) rather than the PRR baseline-compare form, because its
  Jira-AC text matched the notification-content heuristic. Still contains zero banned NSL phrases.
- A couple of authored steps show expected==summary echo (step 2/3 of TC2/TC3) — an expected-
  result derivation detail, not the banned summary artifact.

These are reported for the reviewer's judgement; the root-cause mandate (no NSL steps for the
mediation class, PRR-flow shaped, authored verbatim without the "Verify that" artifact) is met.
