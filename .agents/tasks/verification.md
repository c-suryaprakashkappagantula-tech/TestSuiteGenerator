# Verification — TSG Mediation PRR step-1 input-clause fix

Branch: `feat/tsg-mediation-inputfix`
Worktree: `c:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\tsg-mediation-inputfix`
venv python: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`

## What changed

- `modules/step_templates.py`
  - Retired the fabricating fallback regex `_PRR_INPUT_RE`. `_prr_scenario_fields` now returns a
    real conversion pair (via `_PRR_CONV_RE`) or empty for `in_descr` — it never scrapes two
    adjacent tokens into a fake `"<word> = <word>"`.
  - Added `_prr_input_file_clause(obligation, sc_title, sc_validation, t)` implementing the precise
    branching rule: real `FIELD='VALUE'` from the obligation → authentic clause; otherwise a clean
    generic clause by intent (VZW / CBRS / other-absent/non-MMS / generic TMO) with a plain generic
    fallback. The result never contains a fabricated `=`.
  - `_prr_mediation_steps(...)` gained an `obligation=None` kwarg; both the regression and positive
    branches build step 1 from `_prr_input_file_clause`.
  - `get_step_chain(...)` gained a keyword-only `obligation=None`, threaded into the `batch_file`
    positive `_prr_mediation_steps` call. No other branch changed.
- `modules/tc_builder.py`
  - `_build_obligation_steps`: threads the scenario's first obligation into `_get_chain(...)` so
    the real `MMS_TYPE_INDICATOR='MT'`/`='MO'` pair is rendered (TC2/TC3 preserved).
  - `_build_scenario_tc` fallback: passes `obligation=None` explicitly.
- `tests/test_mediation_input_clause.py` (NEW): real-pair, no-pair (no-loss / e2e / other-absent),
  VZW, CBRS, synthesized-other-absent obligation, full-builder negative guard, and the
  `get_step_chain` obligation-threading case.
- `tests/test_authored_step_verbatim.py`: extended with the live-exact authored line
  `"Let Mediation run the TMO_PRR_Processing_01 flow"` to lock verbatim pass-through.
- `modules/output_sanitizer.py`: NOT edited. See "Authored-step artifact" below.

## Authored-step "Verify that let/retrieve" artifact — root cause & finding

`polish_suite` is the only pass that can prefix a step summary with "Verify that " (verified by
grepping all of `modules/*.py`). It already locks a summary when `authored=True` OR the first word
is an imperative verb (`_lock_summary`). A scratch reproduction (`_scratch_repro.py`, now deleted)
built an obligation TC whose `steps_hint` included the live-exact lines
`"Let Mediation run the TMO_PRR_Processing_01 flow"` and
`"Retrieve the processed Amdocs TMO billing output record"`, ran it through
`_build_scenario_tc` + `polish_suite`, and the lines came out byte-for-byte unchanged — the
`authored=True` flag set in `_build_obligation_steps` is honored end-to-end. No leak reproduced, so
`output_sanitizer.py` needed no edit. The behavior is now locked permanently by the extended
`test_authored_step_verbatim.py`. The live 4628 re-run confirms zero artifacts (counts below).

## 1. py_compile

```
python -m py_compile modules/step_templates.py modules/tc_builder.py modules/output_sanitizer.py
```
Result: EXIT=0.

## 2. Unit + hermetic tests

```
python -m pytest tests/test_mediation_input_clause.py tests/test_mediation_steps_structural.py \
  tests/test_mediation_classification.py tests/test_assertion_grounding.py \
  tests/test_authored_step_verbatim.py -q
```
Result: **29 passed** (0 failed). Includes the 11 assertion-grounding tests, the 3 mediation tests
from the prior fix (structural + classification 17-TC / 3→TC chalk-guard), the authored-verbatim
tests, and the new input-clause suite.

## 2b. Broader non-cache set — git-stash baseline delta (zero NEW regressions)

```
python -m pytest -q -m "not slow" --continue-on-collection-errors
```
- WITH changes:  26 failed, 257 passed, 4 skipped, 73 deselected, 28 collection errors.
- BASELINE (git stash of ONLY the edited source files `modules/step_templates.py` +
  `modules/tc_builder.py` + `tests/test_authored_step_verbatim.py`, and the new test files hidden):
  identical — 54 FAILED/ERROR node ids.
- `Compare-Object before_fails.txt after_fails.txt` → **IDENTICAL — zero new regressions.**
- The 26 failures + 28 collection errors are pre-existing environment issues (e.g.
  `ModuleNotFoundError: No module named 'shared'` for the contract-bridge/crawl tests, and
  CR-path/descriptions/eligibility tests that need live DB fixtures). They fail the same way on the
  clean baseline. No snapshots were regenerated to mask anything.

## 3. Live re-run (real generator)

Caches `tsg_cache.db` and `CABOT_CHALK_DB.db` were copied READ-ONLY from
`...\TestSuiteGenerator` into the worktree, then removed/restored afterward (worktree left clean —
`git status` shows only the four intended edits + the new test file).

```
python run_headless.py MWTGPROV-4628 PI-60
```
Produced: `outputs/MWTGPROV-4628_60.2_-_New_MVNO_-_Update_TMO_MMS_TYPE_INDICATOR_Values_for_Amdocs_call_direction_ind_20261026_165322.xlsx`
Result line: `[RESULT] 17 TCs generated (70 steps total)`; `[PIPELINE] Chalk DB Scenarios ... 3 items`
→ **17 TCs, CHALK-GUARD 3/3 scenarios→TCs. Unchanged.**

### Acceptance checks read from the produced xlsx

- **Banned NSL phrases:** trigger api=0, nsl 200=0, download century report=0, service grouping=0,
  ne portal=0, transaction_history=0, line_history=0 → **TOTAL 0 hits.**
- **"Verify that let" = 0, "Verify that retrieve" = 0** → artifact gone.
- **Fabricated `<word> = <word>` in any Place/ingest step = 0.**
- **TC2/TC3 clause:** `containing MMS_TYPE_INDICATOR='MT'` present (2), `='MO'` present (2) — kept,
  with the obligation Set/Verify assertion steps intact.
- Distinct Place/ingest step-1 texts produced (all clean):
  - `Place/ingest the TMO PRR input file containing MMS_TYPE_INDICATOR='MO'.`
  - `Place/ingest the TMO PRR input file containing MMS_TYPE_INDICATOR='MT'.`
  - `Place/ingest a Verizon (VZW) PRR input file onto the biller SFTP.`
  - `Place/ingest a TMO PRR input file containing records with a non-MMS / other MMS_TYPE_INDICATOR value onto the biller SFTP.`
  - `Place/ingest a representative TMO PRR input file onto the biller SFTP.`

### Per-TC step-1 evidence (AFTER). BEFORE = the fabricated artifact from the prior live 4628.

| TC | BEFORE (prior build, fabricated) | AFTER (this build) |
|---|---|---|
| TC01 (input other/absent → BAU) | `...containing absent ...` | `Place/ingest a TMO PRR input file containing records with a non-MMS / other MMS_TYPE_INDICATOR value onto the biller SFTP.` |
| TC04 (VZW/CBRS processing active) | `...the VZW/CBRS PRR input file (...)` | `Place/ingest a Verizon (VZW) PRR input file onto the biller SFTP.` |
| TC07 (No loss of existing functionality) | `...containing loss = of ...` | `Place/ingest a representative TMO PRR input file onto the biller SFTP.` |
| TC08 (MT→I conversion end to end) | `...containing conversion = end ...` (a.k.a. `Validate = the`) | `Place/ingest a representative TMO PRR input file onto the biller SFTP.` |

QMetry-authored TC10–TC17 step-1 lines pass through verbatim and clean (e.g. TC14
`Process a CBRS PRR file.`, TC15 `Process a Verizon PRR file through the VZW processing flow.`,
TC10 `Ingest the TMO PRR file with the malformed record.`). No fabricated pairs, no
"Verify that" prefixes.

## Cleanup

- Copied read-only caches removed; `CABOT_CHALK_DB.db` restored to the committed version;
  gitignored `tsg_cache.db` restored from backup; run-created `outputs/` artifacts and all
  `_scratch_*` probes deleted. `git status` shows only: `modules/step_templates.py`,
  `modules/tc_builder.py`, `tests/test_authored_step_verbatim.py`, `tests/test_mediation_input_clause.py`
  (plus a pre-existing, untouched `.agents/tasks/plan.md` modification that is not part of this fix).

---

# Cross-FEAT Integration Verification — Step-Assembly (Iteration 1)

Branch: `feat/step-assembler` · Worktree: `.worktrees/step-assembler`
FEAT commits under test (all present on top of base merge `42ef69b`):
`b3d0f46` FEAT-001, `014308a` FEAT-002, `b936078` FEAT-003.
venv: `C:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`

## Environment prep (verification-only, not committed)
- Copied `tsg_cache.db`, `tsg_data.db` into the worktree root. `CABOT_CHALK_DB.db` was already
  present and is a never-touch file — left untouched and never staged.
- Installed the pinned dev dependency the suite needs: `hypothesis==6.112.2`
  (declared in `requirements-dev.txt`, missing from the shared venv).
- Ran pytest with `PYTHONPATH=C:\Users\P3314665\Documents\mcp-jira-server` so the `shared`
  package (imported by `modules/contract_bridge.py`) resolves. The TSG app puts that repo root
  on `sys.path` at runtime; PYTHONPATH reproduces it for the test process.

## Commands + results
```
python -m pytest -q
=> 37 failed, 561 passed, 7 skipped, 34 warnings
```
```
python -m pytest tests/test_step_assembler.py tests/test_mediation_steps_structural.py \
  tests/test_assertion_grounding.py tests/test_qmetry_steps_source.py tests/test_degraded_runs.py -q
=> 65 passed    (the step-assembly deliverable's own tests)
```
```
python run_headless.py MWTGPROV-4628 PI-60
```
Regenerated workbook (absolute path):
```
...\.worktrees\step-assembler\outputs\MWTGPROV-4628_60.2_-_New_MVNO_-_Update_TMO_MMS_TYPE_INDICATOR_Values_for_Amdocs_call_direction_ind_20261026_203950.xlsx
```

## openpyxl confirmations (concrete numbers)
- **TC01 dedup — PASS.** TC01 = **4 steps** (grounded PRR chain only); redundant steps 5-8 gone.
  Log: `[STEP-ASSEMBLER] reconciled suite: 6 redundant step(s) collapsed`.
- **Batch/SFTP preconditions on EVERY scenario TC — PASS: 9/9** carry SFTP/PRR/batch
  preconditions (was 3/17). Log: `9 precondition(s) set from classification (batch_file=True)`.
- **0 banned mediation phrases — PASS** across all step summaries + expected results.
- **Fabricated FIELD='VALUE' pairs — 0 genuine.** Raw regex flagged `MMS_TYPE_INDICATOR='MO'`
  and `='MT'`, but both are real grounded obligation values (MT proven a genuine obligation by
  `test_mediation_steps_structural`). No placeholder `input = word` fabrications. Genuine count: 0.
- **Chalk coverage — PASS 3/3.** Log: `[CHALK-GUARD] Chalk coverage OK: 3/3 scenarios -> TCs`.
- **Fail-loud QMetry skip — PASS.** Summary sheet carries the visible
  `[QMETRY] QMetry supplement skipped — authored QMetry test cases were NOT added. Reason: No
  QMetry session authenticated…` warning (FEAT-003).

## Two gate items NOT fixable by FEAT seams (escalated)

### A. TC count = 9, not ~17 — QMetry TC10-TC17 unfetchable (environment blocker)
All candidate `storage_state.json` files failed the live QMetry auth probe
(`… did not authenticate → trying next` ×3, then `supplement skipped (No QMetry session
authenticated…)`). This is the findings §7 UI-vs-headless divergence: the 8 QMetry-only TCs
require a live authenticated QMetry REST session this headless environment does not have. The
prior `feat/tsg-mediation-inputfix` live run above (timestamp `165322`) got 17 TCs because a
valid session existed then — confirming the current 9 is a session-expiry environment issue,
not a FEAT regression. No code change can mint valid cookies.

### B. 37 pytest failures are PRE-EXISTING drift, not FEAT regressions (proven)
All 37 failures are in `test_suite_snapshot.py`, `test_ac_gate.py`, `test_cr_path.py`,
`test_near_dup_pruning.py` — none touched by the FEATs.
- Base commit `42ef69b` vs FEAT branch over all 16 snapshot features: **byte-identical TC
  counts AND titles** (`TOTAL BASE-vs-FEAT DIFFS: 0`). e.g. MWTGPROV-3969 = 6 TCs on both;
  committed `expected_suites.json` expects 9.
- Running the 4 modules at the base commit reproduces the **same 37 failures**
  (`37 failed, 78 passed, 3 skipped`).
So `expected_suites.json` is stale against its own base branch. Regenerating a committed
regression fixture changes what the guard pins — a product decision escalated rather than taken
inside the loop.

## Net
FEAT-001/002/003 integrate cleanly (65 deliverable tests pass; output byte-identical to base for
the 16 snapshot features, so zero regression). Every real-xlsx check that does not depend on a
live QMetry session holds. Items A (QMetry auth / ~17 count) and B (stale snapshot fixture) each
need a human decision.

## Parent decision (both items resolved — proceed to finalize)
The workflow owner WAIVED both items for this headless environment:
- **A — WAIVED:** 9-vs-17 is the findings §7 UI-vs-headless divergence (QMetry 401 / no
  authenticated session), an environment limitation, not a regression. In-scope correct behavior
  for THIS change is fail-loud + Summary-sheet warning + `suite.warnings`, which is verified.
  Acceptance is verified on the **9-TC headless output only**; full 17 requires a live QMetry
  session.
- **B — KNOWN PRE-EXISTING, leave as-is:** the 37 failures fail identically at base `42ef69b`
  with byte-identical counts/titles (diff=0) → zero NEW regressions. `expected_suites.json` is
  NOT regenerated inside this loop (repinning a committed regression guard is a separate product
  decision not requested). The `shared`-module collection errors are the same pre-existing
  environment gap.

## Final acceptance on the 9-TC headless output (all PASS)
Workbook: `outputs\MWTGPROV-4628_60.2_..._20261026_203950.xlsx`
- **TC01 = 4 steps, asserts `call_direction_ind = BAU`** (expected text:
  "call_direction_ind = BAU in the Amdocs TMO billing output record; all other fields …"). PASS.
- **0 leaked placeholder values** — no `= output`, `= result`, `= value` anywhere. PASS.
- **Grounding intact** — all 9 TCs report **100%** grounding (no TC regressed below its prior
  gate). PASS.
- **Batch/SFTP preconditions on 9/9 scenario TCs.** PASS.
- **0 banned mediation phrases; 0 genuine fabricated FIELD='VALUE' pairs** (MO/MT are real
  obligation values). PASS.
- **Chalk coverage 3/3.** PASS.
- **QMetry-skip warning visible** in the Summary sheet + `suite.warnings` (fail-loud). PASS.

Acceptance bar for this change — "zero NEW regressions + real-xlsx acceptance on the 9-TC
output" — is MET. The ~17 / TC10-TC17 path is explicitly WAIVED here (verified on 9-TC headless
output; full 17 requires a live QMetry session — environment limitation, not a regression).
