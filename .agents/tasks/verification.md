# Verification — project-agnostic QMetry resolver (feat/qmetry-project-agnostic)

Branch: `feat/qmetry-project-agnostic`  ·  Base: `1e5e6ae`
venv python: `C:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`
Runs executed from the WORKTREE root.

## What changed (ONE integrated change)

Production: `modules/qmetry_steps_source.py`
Tests: `tests/test_qmetry_steps_source.py`

The hardcoded MWTGMED-only project assumption is generalized into a **dynamic,
project-agnostic resolver**:

- `_load_project_index(cookies, log)` — queries QMetry's project-agnostic
  `/projects` endpoint through the authenticated `QMetryRestClient` and builds a
  per-run `{PROJECT_KEY_UPPER: projectId}` index. Jira project KEY == QMetry
  `project["key"]`, so EVERY project (MWTGMED, NSLNM, and any future one) resolves
  with no code change. Fail-loud: an HTTP/transport failure raises `QMetryFetchError`
  (never a silent `None`).
- `resolve_project_dynamic(jira_key, cookies, log)` — primary resolution via the
  dynamic index.
- `resolve_project_for_feature(feature_key, subtasks, cookies, log)` — now
  **subtask-first** ("always go with the Jira issue") and **dynamic-then-static**:
  each valid subtask key, then the feature key, resolved via dynamic `/projects`
  first, then the additive static `QMETRY_PROJECT_MAP` fallback. `cookies=None`
  skips the dynamic step (keeps hermetic unit tests deterministic).
- `QMETRY_PROJECT_MAP` kept as an **ADDITIVE fallback only** (documented as such),
  NOT the source of truth. No new per-project hardcode added for NSLNM.
- `_resolve_folder_id` — tiered folder match: PASS 1 full-key containment
  (`MWTGMED-5214` → folder `MWTGMED-5214`), PASS 2 truncated-name / issue-number
  with a digit-boundary guard (`NSLNM-759` → folder `NM-759`; never `-7591`/`-3759`).
- `enrich_scenarios_with_qmetry` — loads cookies FIRST (fail-loud preserved), then
  resolves the project dynamically with those cookies.

Invariants preserved (unchanged code paths): Chalk-first / CHALK-GUARD, value-pair
tier-1 + 0.60 similarity tier-2, Tier-3 relevance gate (promote-or-flag, never
silent drop), fail-loud on auth, QMetry carve-out (MCP not required).

## Live resolver probe (authenticated, confirms dynamic resolution)

- Auth OK via WORKTREE `storage_state.json` (12 charter.com cookies).
- `/projects` returned 268 projects.
- `NSLNM-759` → project **121405** (MWTGNSLNM); folder `NM-759` id **128648** (13 TCs).
- `MWTGMED-5214` → project **76000**; folder `MWTGMED-5214` id **129316**.
- `resolve_project_for_feature('MWTGPROV-4074', ['NSLNM-759'])` → **121405**
  (resolved through the SUBTASK, not the feature — the feature MWTGPROV itself
  dynamically maps to 75700, but the subtask is tried first per "always go with the
  Jira issue").

## Run 1 — MWTGPROV-4074 PI-60 (the project-agnostic win)

xlsx: `C:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\qmetry-project-agnostic\outputs\MWTGPROV-4074_60.2_Enable_intake_of_TMO_wearable_Audit_Report_CHRTR_deltas_YYYYMMDD_20261026_235005.xlsx`

- Log NO LONGER says "not mapped to a QMetry project". It resolves
  `folder resolved (truncated-name, issue #759 for NSLNM-759): id=128648 (NM-759)`
  and exports **13 Test Cases from project 'MWTGNSLNM' (121405)**.
- **Before: 4 TCs. After: 16 TCs** (12 QMetry-only TCs promoted; 1 ineligible —
  `NSLNM-TC-1302` not story-linked to this feature; the engine's Chalk-derived TCs
  make up the rest). Per-source: 4 Chalk/AC-derived + 12 QMetry-only authored.
- QMetry TCs carry REAL authored execution steps (e.g. "Open the applicable NSL
  process specification", "Verify the SFTP path and file naming convention are
  documented", 2–4 authored steps each).

### TRANSACTION_HISTORY status on 4074 (reported, NOT hidden)

`TRANSACTION_HISTORY` appears **9 times** on 4074 (TC04/05/08/09 Chalk-derived +
TC10/11/12/14/16), as the step "Verify the transaction update in the following NSL
DB tables happen successfully: NBOP_MIG_DEVICE / NBOP_MIG_SIM / NBOP_MIG_LINE /
NBOP_MIG_TRANSACTION_HISTORY".

**Provenance determination (cross-checked against the live QMetry export):**
- The NSLNM authored QMetry steps contain `TRANSACTION_HISTORY`/`NBOP_MIG` in
  **0 of 13** TCs (verified by pulling folder 128648 and scanning every authored
  step summary/test-data/expected). So it is **NOT** a legitimate QMetry authored
  step.
- Source is the engine log line `[PATTERN-LIB] enriched suite: 9 TCs got
  closers/steps` → `modules/qmetry_pattern_library.py`, which injects canonical
  `NBOP_MIG_*` DB-table verification CLOSER steps (corpus-derived) for
  device/sim/line-type features. The 4074 wearable/device-pairing feature triggers
  this; the 4628 feature does not (its xlsx is clean).
- Verdict: this is a **GENERATED FALLBACK (garbage per the task's definition), not
  an authored step.** `qmetry_pattern_library.py` is UNCHANGED from base `1e5e6ae`
  — this closer injection is pre-existing engine behavior, entirely independent of
  the QMetry project-agnostic resolver (my change touches only
  `qmetry_steps_source.py` + its tests).

**DECISION (user, Option A) — DEFERRED to a dedicated follow-up; NOT fixed here.**
Removing these NBOP_MIG closers means altering `qmetry_pattern_library.py`, which
changes what every device/sim/line feature's TCs show (including Chalk-derived TCs)
— a cross-feature product/behavior change outside this task's single-file resolver
scope. The user confirmed Option A: keep `qmetry_pattern_library.py` UNTOUCHED in
this task and track the 4074 `TRANSACTION_HISTORY`/`LINE_HISTORY` leak as a
separately-tracked PRE-EXISTING garbage item (same class as the pre-existing
snapshot test failures).

This phrase is confirmed GARBAGE (a generated fallback, absent from all 13 real
QMetry authored steps) and is NOT acceptable long-term — a dedicated follow-up will
fix the NBOP_MIG closer injection PROPERLY, with per-step "real/grounded vs
fabricated" scrutiny rather than a blanket strip that would also kill legitimate
Chalk-derived DB-verification steps. That cross-feature decision belongs in its own
task with its own verification, not inside this resolver loop.

**Honest status of 4074:** 4074's QMetry-AUTHORED steps are CLEAN (the resolver
pulled real authored steps from folder 128648). 4074 is NOT fully banned-phrase
clean, however — the pre-existing pattern-library closers still leak
`TRANSACTION_HISTORY`/`LINE_HISTORY` into 9 TCs. **Fix pending** in the dedicated
follow-up. See the "WAIVABLE / deferred item" note below.

## Run 2 — MWTGPROV-4628 PI-60 (regression, must stay green)

xlsx: `C:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\qmetry-project-agnostic\outputs\MWTGPROV-4628_60.2_-_New_MVNO_-_Update_TMO_MMS_TYPE_INDICATOR_Values_for_Amdocs_call_direction_ind_20261026_235428.xlsx`

- `folder resolved (full-key): id=129316 (MWTGMED-5214)` — MWTGMED → 76000 /
  folder 129316 still resolves exactly (full-key PASS 1).
- **17 TCs** total. Per-source: **3 Chalk + 8 QMetry + 6 Jira-AC** (provenance tally
  Chalk 4 / Jira-AC 5 / QMetry 8 ≈ 17; 2 QMetry value-pair TCs enriched Chalk
  scenarios + 8 QMetry-only promoted).
- **CHALK-GUARD: 3/3 scenarios → TCs OK.**
- **TC01 = 4 steps.**
- **Batch preconditions present on ALL 17 TCs** (0 without a precondition).
- Step-assembler ran: `[STEP-ASSEMBLER] reconciled suite`.

## Banned / placeholder / Chalk checks (both xlsx)

Banned set checked: Service Grouping, SERVICE_GROUPING, Century Report, "NSL
processes request", Trigger API, TRANSACTION_HISTORY, LINE_HISTORY, NE Portal.
Placeholder set: "= output", "= result".

- **4628:** banned phrases **0**, placeholders **0**, Chalk coverage intact (3/3).
- **4074:** placeholders **0**, Chalk coverage intact. Banned:
  `TRANSACTION_HISTORY`×9 (+`LINE_HISTORY` in the same NBOP_MIG closer) — origin
  established above (pre-existing `qmetry_pattern_library.py` generated closer, NOT
  the QMetry authored steps, NOT introduced by this change).

## New unit tests added (tests/test_qmetry_steps_source.py)

- `test_resolve_project_dynamic_nslnm` — NSLNM-759 → 121405, MWTGMED-5214 → 76000
  dynamically; unknown prefix → None.
- `test_resolve_project_for_feature_subtask_first` — MWTGPROV-4074 resolves via
  subtask NSLNM-759 → 121405.
- `test_mwtgmed_still_resolves_static_when_no_cookies` — cookies=None → 76000 from
  the static fallback.
- `test_mwtgmed_still_resolves_dynamic` — MWTGMED-5214 → 76000 via the dynamic path.
- `test_folder_resolution_truncated_name` — NM-759 → 128648 for NSLNM-759.
- `test_folder_resolution_full_key_mwtgmed` — MWTGMED-5214 → 129316 (full-key).
- `test_numeric_folder_match_no_false_positive` — NSLNM-759 does NOT match
  MWTGMED-3759 / NM-7591.
- `test_unknown_project_skips` updated to stay hermetic under cookies-first ordering.

## pytest new-vs-pre-existing failure delta

Run with `--continue-on-collection-errors` from the WORKTREE:

| | failed | passed | skipped | collection errors |
|---|---|---|---|---|
| base `1e5e6ae` (both files reverted via stash) | 43 | 328 | 4 | 28 |
| this branch | 43 | 335 | 4 | 28 |

- **NO new failures** — identical 43 failures and identical 28 collection errors at
  base and on the branch. The 28 collection errors are the known missing-`shared`-
  module family; the 43 failures are the known stale-snapshot set.
- **+7 passed** on the branch = the 7 new resolver unit tests.
- Module suite in isolation: `pytest tests/test_qmetry_steps_source.py -q` →
  **29 passed** (22 pre-existing + 7 new).

## WAIVABLE / deferred item (user-confirmed Option A)

- **Phrase:** `TRANSACTION_HISTORY` (and `LINE_HISTORY` in the same step).
- **Occurrences on 4074:** 9 TCs — TC04, TC05, TC08, TC09 (Chalk-derived) and
  TC10, TC11, TC12, TC14, TC16 (QMetry-only).
- **Originating step text:** "Verify the transaction update in the following NSL DB
  tables happen successfully: NBOP_MIG_DEVICE / NBOP_MIG_SIM / NBOP_MIG_LINE /
  NBOP_MIG_TRANSACTION_HISTORY".
- **Origin:** `modules/qmetry_pattern_library.py` NBOP_MIG closer injection
  (engine log: `[PATTERN-LIB] enriched suite: 9 TCs got closers/steps`). UNCHANGED
  from base `1e5e6ae`; independent of this resolver change.
- **Provenance:** NOT a QMetry authored step — present in 0 of 13 live NSLNM
  authored TCs (folder 128648). It is a generated fallback (garbage).
- **Disposition:** DEFERRED to a dedicated follow-up task that will fix the closer
  injection properly (per-step grounded-vs-fabricated scrutiny, not a blanket strip
  that would also remove legitimate Chalk-derived DB-verification steps). Tracked
  separately as pre-existing garbage, like the snapshot test failures. NOT fixed in
  this single-file resolver task.

**This task's acceptance gate is MET:** dynamic resolution path exercised end-to-end
(NSLNM resolved live to 121405 / folder 128648, 4→16 with real authored steps),
MWTGMED regression green (17 TCs, CHALK-GUARD 3/3, TC01=4 steps, batch preconditions
on all, step-assembler ran), 4628 xlsx 0 banned / 0 placeholder / Chalk intact,
clean QMetry-authored output on both, no new pytest failures (+7 resolver tests).
The 4074 pattern-library TRANSACTION_HISTORY leak is explicitly waived here with a
fix pending in the dedicated follow-up.
