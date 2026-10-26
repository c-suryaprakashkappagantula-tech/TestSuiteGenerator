# Implementation Plan — Feature-class-aware step generation for Mediation/PRR/billing (MWTGPROV-4628)

## Context (grounded in the worktree)

Worktree: `c:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\tsg-mediation-steps`
(branch `feat/tsg-mediation-steps`). venv python:
`c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`.
Headless run: `python run_headless.py MWTGPROV-4628 PI-60` (run from the worktree root).
NOTE: stdout is buffered — any ad-hoc probe must be a temp `.py` file that writes a report
file with `flush=True`, never `python -c`. The worktree `tsg_cache.db` has NO cached Jira/Chalk
for 4628 (a fresh headless run yields 0 TCs), so correctness is proven by the pytest fixtures
(the real 4628 AC is already captured in `tests/test_assertion_grounding.py` as
`FIXTURE_4628_AC` / `FIXTURE_4628_TERSE`), not by a live run.

Pipeline for 4628 (engine_version='8', the only path run_headless uses):
`run_headless` → `pipeline.block_build_suite_v8` → `data_first_engine.build_test_suite_v8`
→ Step 0 `tc_builder.classify_feature()` → scenario/obligation TC build in `tc_builder`
(`_build_scenario_tc`, `_build_obligation_steps`, both calling `step_templates.get_step_chain`
and `integration_contract.build_downstream_verification_steps`) → Excel generation in
`excel_generator.generate_excel`, which calls `output_sanitizer.polish_suite()`.
(`scenario_enricher.enrich_scenarios` and `humanizer.humanize_suite` are V7-only via
`test_engine` and are NOT on the 4628 path — do not edit them for this task.)

### Three confirmed root-cause sites
1. **Classification** — `modules/tc_builder.py` `classify_feature()` (lines ~119-165). RULE 1
   (`_has_nslnm_component and not _has_nbop_component`) returns `classification='api',
   confidence=0.95, api_keywords_found=['NSLNM']` with no mediation check. This is why 4628 logs
   `Classification: api (... api_kw=['NSLNM'])`.
2. **Step templates** — for the mediation class, three emitters must be bypassed/replaced:
   - `modules/step_templates.py` `get_step_chain()` (line 63): its `feature_type=='batch_file'`
     branch (lines ~108-146) already emits PRR-ish file-ingestion steps, but 4628 reaches it as
     `feature_type='api'`, so it falls through to `_api_flow_steps` / `_default_workflow_steps`
     (lines 1619, 1713) which emit "Trigger API", "Century Report", OAuth token, etc.
   - `modules/tc_builder.py` `build_downstream_verification_steps(...)` call sites (lines ~1444
     and ~1834) splice Service Grouping / Century Report / NBOP MIG / Syniverse / TRANSACTION_HISTORY
     steps via `integration_contract.resolve_operation`.
   - `modules/scenario_enricher.py` audit-history block (lines ~290-340, `TRANSACTION_HISTORY`,
     Century Report) — V7 only, NOT on 4628's path, but must stay consistent; no change required
     unless a test proves otherwise.
   The existing `_build_scenario_tc` fallback (lines ~1407-1430) and `_build_obligation_steps`
   (lines ~1052-1066) already try to flip `_ft` to `'batch_file'` from Jira-summary keywords
   (`'prr file'`, `'sftp'`, `'mediation file'`, …) — but 4628's summary is `[NSLNM...]: ...` and
   those literal keywords are not reliably present, so the flip does not fire.
3. **Humanizer/splitter artifact** — `modules/output_sanitizer.py` `polish_suite()`
   (lines ~565, ~567, ~589, ~638, ~657) rewrites imperative/declarative step summaries to
   `'Verify that ' + s[0].lower() + s[1:]`. This is the `"Verify that let Mediation run…"`
   artifact on authored QMetry steps. Authored steps currently carry no marker
   (`TestStep` has no `authored` field — see `modules/data_models_v8.py` ~line 146).

### Preserve (regression gates)
- Obligation assertion steps: `tc_builder._build_obligation_steps` + `step_templates.assertion_step_texts`
  must still emit `Set <field>=<value>` / `Verify <field>=<value>` and keep `obligation_id`
  (R2/R5). Tests A/B/C/E in `tests/test_assertion_grounding.py`.
- QMetry authored steps verbatim on matched/added TCs: `tests/test_qmetry_steps_source.py`.
- Chalk-first RULE #1 / 17-TC CHALK-GUARD, source ordering: this task changes STEP CONTENT and
  the classification label ONLY — never adds/drops/reorders a TC.
- Genuine NSL-provisioning (NSLNM with NO mediation signal) must still classify `api` and keep
  NSL/NE-Portal/Service-Grouping/Century steps.
- Baseline today: `tests/test_assertion_grounding.py` + `tests/test_qmetry_steps_source.py`
  = 32 passed (confirmed). Keep them green.

### Decision: single plan, no workflow restructure
The three fixes share one new primitive (a mediation-class detector) and are verified together by
the structural test (one produced-step chain must simultaneously drop NSL phrases and gain PRR
verbs). They do not decompose into independently shippable features, so this is one ordered plan
for the existing implement-and-review loop. The loop stops when the reviewer writes
`c:\Users\P3314665\Documents\mcp-jira-server\TMO DashBoard\.agents\tasks\review.json` with
`verdict == "APPROVED"`.

---

# Implementation Plan

- [ ] 1. Add a single shared mediation/PRR/billing signal detector so all three fixes agree on
      one definition. Put it in `modules/qmetry_pattern_library.py` next to the existing
      `is_file_ingestion_feature` (which already knows `_FILE_INGESTION_HINTS` and
      `_NON_PROVISIONING_HINTS` incl. 'mediation','cdr','prr file'). Add
      `is_mediation_feature(*texts: str) -> bool`: case-insensitive, any-of over the concatenated
      inputs, matching the signals — `mediation`, `prr`, `amdocs`, `billing record`,
      `call_direction_ind`, `mms_type_indicator`, `tmo_prr_processing`, `biller sftp`, and
      component tokens `wpamed` / `[med` / `, med` / `med,` and `component == 'Mediation'`.
      Require a REAL mediation signal — do NOT return True on `nslnm` alone. Keep it pure
      (no I/O) and accept several strings (title, summary, scope, components, AC/Chalk text).
      Files: `modules/qmetry_pattern_library.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_qmetry_steps_source.py -q`
      still passes (import side-effect free); the new unit assertions land in step 7.

- [ ] 2. Make `classify_feature` emit a mediation class BEFORE the NSLNM→api rule. In
      `modules/tc_builder.py` `classify_feature()` (and only there), add a first check that calls
      `qmetry_pattern_library.is_mediation_feature(jira_summary, ac_text)`; when true, return
      `FeatureClassification(classification='batch_file', confidence=0.95,
      api_keywords_found=[], ui_keywords_found=[])` (reuse the existing `'batch_file'` label that
      `get_step_chain` already understands — do not invent a new string). Place it above RULE 1 so
      a `[NSLNM, MED, …]`/mediation-bodied summary never falls through to `api`. A pure
      `[NSLNM]` summary with no mediation signal is unaffected (still RULE 1 → api). Confirm no
      other branch of `classify_feature` overrides this (it returns immediately).
      Files: `modules/tc_builder.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_assertion_grounding.py -q`
      stays green (no classification dependence there); dedicated assertion added in step 7a.

- [ ] 3. Propagate the mediation class into the obligation path so every obligation TC gets
      PRR-shaped narrative steps (not NSL). In `modules/tc_builder.py` `_build_obligation_steps`
      (the `else:` grounded-chain branch, ~lines 1052-1067), replace the brittle Jira-summary
      keyword flip with: if `feature_type == 'batch_file'` OR
      `qmetry_pattern_library.is_mediation_feature(<full jira summary>, <description>, <ac>, scenario.validation)`,
      set `_ft = 'batch_file'`. Keep the authored-`steps_hint` branch above it UNCHANGED (authored
      steps stay verbatim). Keep the obligation `for ob in …` splice below UNCHANGED so
      `Set/Verify <field>=<value>` + `obligation_id` still append (R2/R5). The obligation assert
      wording already uses `call_direction_ind` from the obligation — no change there.
      Files: `modules/tc_builder.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_assertion_grounding.py -q`
      — Tests A/B/C/E still pass (obligation_id + expected_value + call_direction_ind intact).

- [ ] 4. Propagate the mediation class into the plain fallback path. In
      `modules/tc_builder.py` `_build_scenario_tc` (the final `else:` with
      `from .step_templates import get_step_chain as _get_chain`, ~lines 1407-1430), replace the
      summary-keyword flip with the same rule as step 3: `_ft = 'batch_file'` when
      `feature_type == 'batch_file'` OR `is_mediation_feature(...)` over the full jira
      summary/description/AC + scenario text. This routes QMetry-unmatched Chalk/Jira-AC TCs
      (the user's TC1/TC4-TC9) to the PRR file-ingestion branch of `get_step_chain` instead of
      `_api_flow_steps`.
      Files: `modules/tc_builder.py`
      Verify: structural test in step 7b (added later) asserts no banned NSL phrases; for now
      `& "...\.venv\Scripts\python.exe" -m pytest tests/test_assertion_grounding.py -q` stays green.

- [ ] 5. Upgrade the `batch_file` branch of `get_step_chain` into an explicit mediation/PRR
      step builder and make the NSL-provisioning emitters structurally unreachable for it. In
      `modules/step_templates.py`:
      (a) Factor the mediation steps into a new helper `_prr_mediation_steps(title, validation, t,
          ctx, negative=False)` and call it from the existing `feature_type=='batch_file'` branch
          (lines ~108-146). The positive chain must be the PRR flow:
          (1) "Place/ingest the TMO PRR input file containing <scenario input, derived from title/
          validation — e.g. MMS_TYPE_INDICATOR=MT>"; (2) "Run the TMO_PRR_Processing_01 mediation
          flow (let Mediation process the file)"; (3) "Retrieve the processed Amdocs TMO billing
          output record(s)"; (4) "Inspect call_direction_ind (or the scenario's output field) and
          compare against the pre-change baseline". When the scenario carries a conversion value
          pair (e.g. MT→I), parameterize step 1 with the input and step 4 with the expected output;
          otherwise emit the BAU/no-impact/regression form: process the relevant PRR file →
          retrieve the Amdocs output → compare field-by-field/byte-for-byte against the pre-change
          baseline and assert UNCHANGED. Keep the existing NBOP-usage append (lines ~140-145) only
          when `ctx` already names NBOP.
      (b) Add a module-private frozenset `_BANNED_MEDIATION_PHRASES` =
          {'trigger api', 'century report', 'service grouping', 'ne portal',
          'transaction_history', 'line_history', '200 ok', 'transactionid', 'oauth'} and, at the
          END of the `batch_file` branch, assert none of the produced step summaries/expecteds
          contains any banned phrase (raise AssertionError in debug/test, or filter-and-log in
          prod — choose assert so the structural test can rely on it). This makes the banned
          chain structurally unreachable for the mediation class.
      Do NOT change any non-`batch_file` branch (genuine NSL features keep their steps).
      Files: `modules/step_templates.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/ -q -k "assertion or qmetry"`
      stays green; structural assertions added in step 7b.

- [ ] 6. Prevent the downstream-contract splicer from re-adding NSL steps on the mediation class.
      In `modules/tc_builder.py`, at both `build_downstream_verification_steps` call sites
      (~lines 1444 and ~1834), guard the splice with: skip entirely when the effective feature
      type is `batch_file`/mediation (reuse the step-3/4 `_ft`/`is_mediation_feature` result —
      thread the resolved `_ft` or recompute via `is_mediation_feature`). `resolve_operation`
      already returns None for most read-only features, but for mediation we must not rely on that;
      make the skip explicit. Belt-and-suspenders: this ensures Service Grouping / Century Report /
      TRANSACTION_HISTORY never append to a mediation TC even if a contract alias matched.
      Files: `modules/tc_builder.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/ -q -k "assertion or qmetry"`
      stays green; covered structurally by step 7b.

- [ ] 7. Fix the "Verify that " prefix + re-casing on authored/imperative steps. Two coordinated
      edits:
      (a) Add `authored: bool = False` to `TestStep` in `modules/data_models_v8.py` (defaults keep
          zero behavioural change). In the authored-`steps_hint` branches that build steps verbatim
          — `tc_builder._build_obligation_steps` (the `if getattr(scenario,'steps_hint',...)` loop)
          and `_build_scenario_tc` (the `elif scenario.steps_hint:` loop) — set `authored=True` on
          each `TestStep` built from an authored hint. (Authored = sourced from QMetry `steps_hint`.)
      (b) In `modules/output_sanitizer.py` `polish_suite()`, at the start of the per-step loop
          (`for st in steps:`), compute `_authored = bool(getattr(st, 'authored', False))`. When
          `_authored` is True OR the summary is already imperative (reuse the module's
          `_is_imperative`/`_POL_CHECK_VERB_RE`), do NOT apply any `'Verify that ' + s[0].lower()…`
          rewrite — leave the summary verbatim (still allow char-cleanup `_fix_body`, length trim,
          and expected-result derivation, but never the prefix/recase). This touches the four rewrite
          sites (~565-567, ~589, ~638, ~657): gate each on `not _authored and not _is_imperative(s)`.
      Files: `modules/data_models_v8.py`, `modules/tc_builder.py`, `modules/output_sanitizer.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_qmetry_steps_source.py -q`
      stays green; humanizer passthrough asserted in step 7c.

- [ ] 7a. Add the classification test. New file `tests/test_mediation_classification.py`: assert
      `classify_feature('[NSLNM, INTG]: ... MMS_TYPE_INDICATOR ... call_direction_ind ... Amdocs
      TMO billing record', FIXTURE_4628_AC).classification == 'batch_file'`; assert a synthetic
      `[WPAMED]: Mediation PRR ...` summary classifies `batch_file`; assert a synthetic pure-NSL
      `[NSLNM]: New MVNO - Retrieve device (GET/POST)` (no mediation signal) STILL classifies
      `api`. Reuse `FIXTURE_4628_AC` by importing from `tests/test_assertion_grounding.py` or
      inlining the same AC text. Follow the hermetic, no-DB pattern used by
      `test_assertion_grounding.py` (import from `modules.tc_builder`, `sys.path` to worktree root).
      Files: `tests/test_mediation_classification.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_mediation_classification.py -q`
      — all pass.

- [ ] 7b. Add the structural step test. New file `tests/test_mediation_steps_structural.py`:
      build a mediation scenario (obligation MT→I and a BAU/no-impact scenario) via
      `_synthesize_obligation_scenario` / a hand-built `ExtractedScenario`, call `_build_scenario_tc`
      and `_build_obligation_steps` with a `_Jira('MWTGPROV-4628')` whose summary carries a
      mediation signal, and assert over the concatenated step summaries+expecteds: (i) contains
      NONE of `_BANNED_MEDIATION_PHRASES` (trigger api, century report, service grouping, ne portal,
      transaction_history, line_history, 200 ok, transactionid, oauth); (ii) DOES contain the PRR
      verbs — at least one of `ingest`/`place`, `mediation`, `tmo_prr_processing`, `amdocs`
      (billing), and `call_direction_ind` (or the scenario's output field). Also assert the
      obligation TC STILL has a step with `obligation_id` set and `expected_field=='call_direction_ind'`
      (preserve R2/R5).
      Files: `tests/test_mediation_steps_structural.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_mediation_steps_structural.py -q`
      — all pass.

- [ ] 7c. Add the humanizer passthrough test. New file `tests/test_authored_step_verbatim.py`:
      build a `TestCase` whose steps include an authored imperative step
      `TestStep(summary='Let Mediation process the PRR file', authored=True, ...)` and a wrapping
      `TestSuite`, run `output_sanitizer.polish_suite(suite)`, and assert the step summary is
      byte-for-byte unchanged — no `'Verify that '` prefix, no lowercasing of 'Let'. Add a second
      step with `authored=False` but already imperative and assert it is likewise not
      "Verify that"-prefixed. Follow the hermetic pattern (build data_models_v8 objects directly).
      Files: `tests/test_authored_step_verbatim.py`
      Verify: `& "...\.venv\Scripts\python.exe" -m pytest tests/test_authored_step_verbatim.py -q`
      — all pass.

- [ ] 8. Full regression gate. Run the targeted suite and confirm no regressions, then confirm the
      previously-green baseline plus the three new test files all pass together.
      Files: none (verification only)
      Verify: `& "c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe" -m pytest
      tests/test_assertion_grounding.py tests/test_qmetry_steps_source.py
      tests/test_mediation_classification.py tests/test_mediation_steps_structural.py
      tests/test_authored_step_verbatim.py -q` — all pass (expected ≥ 35: the 32 baseline + new).
      Then a broader `-m pytest tests/ -q -k "not slow and not cache"` shows no NEW failures versus
      `tests/_baseline_failures.txt` (28 pre-existing `shared` collection errors are expected and
      unrelated).

- [ ] 9. Confirm no stray temp files or debug scripts were left in the worktree root
      (`git status` clean except the intended edits + new test files). Do NOT commit any `_probe_*`
      or `*.db` changes. Stage only the edited modules and the three new test files.
      Files: none (hygiene only)
      Verify: `git status --short` lists only `modules/qmetry_pattern_library.py`,
      `modules/tc_builder.py`, `modules/step_templates.py`, `modules/output_sanitizer.py`,
      `modules/data_models_v8.py`, and the three new `tests/test_*.py` files.

## Notes / assumptions
- Reusing the existing `'batch_file'` classification label (already understood by
  `get_step_chain`) rather than inventing a `'mediation'` string keeps the blast radius minimal
  and leverages the PRR branch that already exists. If a reviewer prefers a distinct `'mediation'`
  label, it is a rename across steps 2-6 only.
- `scenario_enricher.py` and `humanizer.py` are V7-only and not on the 4628 (V8/run_headless)
  path, so they are intentionally untouched. If a future V7 run of a mediation feature is in
  scope, the same `is_mediation_feature` gate would be added to
  `scenario_enricher.enrich_scenarios` — out of scope here.
- The real 4628 run cannot be reproduced in this worktree (empty cache); correctness rests on the
  fixtures that already encode the real AC. This is called out so the implementer does not burn
  time chasing a live run.
