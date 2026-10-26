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
