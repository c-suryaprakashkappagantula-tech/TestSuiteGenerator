# Implementation Plan — TSG Mediation PRR step-1 "containing &lt;X&gt;" input-clause fix

Worktree (all paths absolute, under here):
`c:\Users\P3314665\Documents\mcp-jira-server\TestSuiteGenerator\.worktrees\tsg-mediation-inputfix`
venv python: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe`
Run tests from the worktree root. Pytest is configured in `pytest.ini` (`testpaths = tests`).

## Problem (confirmed by live probe of the exact reported titles)

`modules/step_templates.py::_prr_scenario_fields()` derives the first "containing &lt;X&gt;"
fragment by title/validation parsing. Its fallback regex `_PRR_INPUT_RE` grabs two arbitrary
adjacent tokens and renders a fake `"&lt;word&gt; = &lt;word&gt;"` pair whenever there is no real
field→value. Reproduced against the live titles:

| Scenario title | `_prr_scenario_fields` in_descr | step-1 clause (current, WRONG) |
|---|---|---|
| "No loss of existing functionality or degradation" (TC7) | `loss = of` | `...containing loss = of onto the biller SFTP` |
| "Validate the 'MT' to 'I' conversion end to end..." | `Validate = the` | `...containing Validate = the ...` |
| "input other/absent -&gt; output BAU" (TC1) | `absent` | `...containing absent ...` |

Why TC2/TC3 are correct today (DO NOT regress): the genuine mapping TCs carry **QMetry authored
steps** (`scenario.steps_hint`) and/or a **real obligation** (`input_field`/`input_value`). When
`steps_hint` is present, `tc_builder._build_obligation_steps` builds the narrative verbatim from
the authored lines (`authored=True`) and never calls `_prr_mediation_steps`, so step 1
("...containing MMS_TYPE_INDICATOR='MT'") is the authored QMetry text. The garbled path is only
reached for scenarios WITHOUT authored hints (TC1/TC7/TC8 and synthesized-obligation scenarios).

## Design decisions (rationale, one line each)

- **Source the value-pair from the obligation, not the title.** `_build_obligation_steps` already
  has the real `input_field`/`input_value`; thread it into the step chain so `_prr_mediation_steps`
  uses the authentic pair instead of regex-scraping the title. This is why TC2/TC3 stay right and
  TC1/TC7/TC8 stop fabricating. Chosen over "tighten the regex" because any title regex will keep
  mis-firing on prose scenarios (proven: it mangled the MT/MO titles too).
- **Never emit a `word = word` token in the file clause.** When no real pair exists, pick a clean
  generic clause by scenario intent (VZW / CBRS / other-absent / generic TMO), with a plain
  generic fallback. Chosen over dropping the clause so the step still reads as a concrete action.
- **Authored QMetry step summaries pass through verbatim.** `output_sanitizer.polish_suite`
  already locks authored/imperative summaries (prior fix; `test_authored_step_verbatim.py` green).
  Plan verifies this end-to-end and only extends the guard if a live repro shows a leak.

## Exact change sites

- `modules/step_templates.py`
  - `_PRR_INPUT_RE` — defined ~lines 76–78 (the fabricating fallback regex).
  - `_prr_scenario_fields(title, validation)` — ~lines 81–100 (returns garbled `in_descr`).
  - `_prr_mediation_steps(sc_title, sc_validation, t, ctx, negative=False)` — ~lines 103–163:
    regression branch step 1 ~lines 143–157; positive branch `_input_clause` ~lines 159–162;
    step-1 text `'Place/ingest the TMO PRR input file %s onto the biller SFTP' % _input_clause`.
  - `get_step_chain(...)` — ~line 165; batch_file branch calls `_prr_mediation_steps` at the
    positive site (~line 310 region) and the negative site just above it.
- `modules/tc_builder.py`
  - `_build_obligation_steps(...)` — def ~line 1021; the grounded-chain `_get_chain(...)` call
    at ~line 1090 (obligation path — pass the obligation here).
  - `_build_scenario_tc(...)` fallback `_get_chain(...)` at ~line 1455 (non-obligation path —
    pass `obligation=None`).
- `modules/output_sanitizer.py`
  - `polish_suite` authored/imperative lock at ~lines 548–553 (`_lock_summary`); `_POL_VERBS`
    ~lines 367–385 (note: "let" and "retrieve" are NOT imperative verbs — that is why an
    unflagged authored line could be prefixed).

---

- [ ] 1. Add an optional `obligation` parameter to the mediation step builder and the public
      `get_step_chain`, defaulting to `None`, and thread it to `_prr_mediation_steps`. In
      `get_step_chain` add `obligation=None` to the signature and pass it into BOTH
      `_prr_mediation_steps(...)` calls in the `batch_file` branch (positive and, for symmetry,
      the negative one — the negative branch ignores it). Do not change any other branch's
      behavior; keep the signature backward compatible (keyword-only default).
      Files: `modules/step_templates.py`
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest tests/test_mediation_steps_structural.py tests/test_mediation_classification.py tests/test_assertion_grounding.py -q` — all still pass (no behavior change yet).

- [ ] 2. Build the step-1 "containing &lt;X&gt;" clause from a new helper that implements the precise
      branching rule and NEVER fabricates a `word = word` token. Add a helper, e.g.
      `_prr_input_file_clause(obligation, sc_title, sc_validation, t)`, and use its result as
      step-1 text in both the positive and regression branches of `_prr_mediation_steps`.
      Branching rule (in order):
        1. **Real value-pair present** — `obligation` has non-empty `input_field` AND `input_value`
           (e.g. `MMS_TYPE_INDICATOR`/`MT`): clause =
           `Place/ingest the TMO PRR input file containing &lt;input_field&gt;='&lt;input_value&gt;' onto the biller SFTP.`
           (Preserves TC2/TC3 wording exactly — these also keep coming from authored hints when present.)
        2. **No real pair → generic by intent** (match against `t`, the lowercased
           `title + ' ' + validation`), each verbatim:
           - VZW/Verizon (`\b(verizon|vzw)\b`):
             `Place/ingest a Verizon (VZW) PRR input file onto the biller SFTP.`
           - CBRS (`\bcbrs\b`):
             `Place/ingest a CBRS PRR input file onto the biller SFTP.`
           - other/absent or non-MMS pass-through (`other/absent`, `\bother\b`, `absent`,
             `non-mms`, or `bau`/`output bau` without a value):
             `Place/ingest a TMO PRR input file containing records with a non-MMS / other MMS_TYPE_INDICATOR value onto the biller SFTP.`
           - generic TMO (no-loss, e2e, unchanged-fields, regression — e.g. `no loss`,
             `degradation`, `end to end`, `end-to-end`, `unchanged`, `remains`, `regression`):
             `Place/ingest a representative TMO PRR input file onto the biller SFTP.`
        3. **Fallback** (intent unclear):
           `Place/ingest a representative TMO PRR input file onto the biller SFTP.`
      Order the VZW and CBRS checks BEFORE the generic-TMO check so a VZW/CBRS regression scenario
      gets the vendor-specific clause (today the regression branch already special-cases VZW/CBRS —
      keep that intent). The clause must NEVER contain a `=` unless it is the real
      `&lt;field&gt;='&lt;value&gt;'` form from branch 1. Keep steps 2–4 of `_prr_mediation_steps`
      (run flow / retrieve Amdocs / inspect-or-compare) unchanged.
      Files: `modules/step_templates.py`
      Verify: `...python.exe -m pytest tests/test_mediation_steps_structural.py -q` — passes; and a
      scratch probe (delete after) over the three reported titles shows NO `=` in step 1 for the
      non-mapping titles and the VZW/CBRS/other/generic variants render verbatim.

- [ ] 3. Retire the fabricating fallback in `_prr_scenario_fields`. Keep `_PRR_CONV_RE`
      (genuine `X -&gt; Y` / "converts to" conversion detection) for `out_value`, but remove the
      `_PRR_INPUT_RE` `in_descr` fabrication so `in_descr` is only ever a real pair or empty —
      step-1 wording now comes solely from the step-2 helper. `_prr_scenario_fields` may still
      return `out_field`/`out_value` for the inspect step; just stop returning a scraped
      `"&lt;word&gt; = &lt;word&gt;"` as `in_descr`. If `_PRR_INPUT_RE` becomes unused, delete it.
      Files: `modules/step_templates.py`
      Verify: `...python.exe -m pytest tests/test_mediation_steps_structural.py tests/test_assertion_grounding.py -q` — passes; grep-free confirmation via the Step-5 unit test below.

- [ ] 4. Pass the real obligation into the chain from the obligation builder. In
      `tc_builder._build_obligation_steps`, at the `_get_chain(...)` call (~line 1090), pass the
      scenario's first obligation (e.g. `obligation=(scenario.obligations or [None])[0]`) so the
      mediation builder can render `&lt;input_field&gt;='&lt;input_value&gt;'`. In
      `_build_scenario_tc`'s fallback `_get_chain(...)` call (~line 1455) pass `obligation=None`
      explicitly. Do not change the authored-hint branch (steps_hint present) — it must keep
      building verbatim authored steps and never calls the chain.
      Files: `modules/tc_builder.py`
      Verify: `...python.exe -m pytest tests/test_mediation_steps_structural.py tests/test_assertion_grounding.py -q` — passes, including
      `test_obligation_mt_to_i_is_prr_flow_no_nsl` (obligation_id / expected_field / `call_direction_ind` preserved).

- [ ] 5. Add unit tests for the new input-clause rule in a new file
      `tests/test_mediation_input_clause.py`. Cover, using `step_templates.get_step_chain` /
      `_prr_mediation_steps` and `tc_builder._build_scenario_tc` (pattern: reuse the `_Jira` and
      `FIXTURE_4628_AC` helpers from `tests/test_mediation_steps_structural.py`):
        - Real value-pair (obligation `MMS_TYPE_INDICATOR`/`MT`) → step 1 contains
          `MMS_TYPE_INDICATOR='MT'` and the clause has exactly one `=` of the `field='value'` form.
        - Real value-pair `MO` → `MMS_TYPE_INDICATOR='MO'` (TC2 wording intact).
        - "No loss of existing functionality or degradation" → step 1 ==
          `Place/ingest a representative TMO PRR input file onto the biller SFTP.` and contains no `=`.
        - "Validate the 'MT' to 'I' conversion end to end..." with NO obligation → step 1 contains
          no fabricated `Validate = the`; it is the generic TMO clause (no `=`).
        - "input other/absent -&gt; output BAU" → the non-MMS/other-value clause (no fabricated `=`).
        - VZW title → the `Verizon (VZW)` clause; CBRS title → the `CBRS` clause.
        - Negative/assertion: for EVERY produced step, assert there is no standalone `"&lt;word&gt; = &lt;word&gt;"`
          fabricated token in a file clause (regex `\b[a-z]+\s*=\s*[a-z]+\b` must not match step 1
          unless it is the `field='value'` form).
      Files: `tests/test_mediation_input_clause.py`
      Verify: `...python.exe -m pytest tests/test_mediation_input_clause.py -q` — all new tests pass.

- [ ] 6. Verify-and-fix the authored-step "Verify that let Mediation run..." / "Verify that
      retrieve..." artifact. First REPRODUCE: write a scratch test that builds a TC whose
      `steps_hint` includes `"Let Mediation run the TMO_PRR_Processing_01 flow"` and
      `"Retrieve the processed Amdocs TMO billing output record"` (mirroring the live 4628 authored
      lines, `authored=True`) and run it through the FULL post-processing actually applied to a
      generated suite (at minimum `output_sanitizer.polish_suite`; if the live pipeline applies
      other passes to step summaries, include them). If any pass prefixes "Verify that " or
      re-cases the first word, fix THAT pass to honor `authored=True` (and already-imperative
      summaries) exactly as `polish_suite` does at lines 548–553 — do not special-case "let"/
      "retrieve"; respect the authored flag. If no pass reproduces the artifact (i.e. the prior
      fix already removed it and the authored flag is intact end-to-end), record that finding in
      the step report and extend `tests/test_authored_step_verbatim.py` with the two live-exact
      lines ("Let Mediation run the TMO_PRR_Processing_01 flow", "Retrieve the processed Amdocs
      TMO billing output record") to lock the behavior permanently.
      Files: `modules/output_sanitizer.py` (only if a leak is reproduced), `tests/test_authored_step_verbatim.py`
      Verify: `...python.exe -m pytest tests/test_authored_step_verbatim.py -q` — passes, including the
      new live-exact cases; the summaries stay byte-for-byte (no "Verify that", no re-casing).

- [ ] 7. Full regression + banned-phrase gate. Run the complete fast test suite and confirm the
      preserve-list holds.
      Files: (none — verification only)
      Verify: `c:\Users\P3314665\Documents\mcp-jira-server\.venv\Scripts\python.exe -m pytest -q -m "not slow"` from the worktree root — all pass
      (baseline before changes: the four targeted files are 19 passed). Confirm
      `test_mediation_steps_structural.py` banned-phrase asserts stay green and
      `test_mediation_classification.py` 17-TC / 3→TC chalk-guard is unchanged.

## Acceptance criteria (preserve-list — must all hold)

- No banned NSL phrases anywhere in mediation chains: Trigger API, NSL 200, Download Century
  Report, Service Grouping, NE Portal, TRANSACTION_HISTORY, LINE_HISTORY. The structural
  banned-phrase assert (`_assert_no_banned_mediation` / `tests/test_mediation_steps_structural.py`)
  stays green.
- TC2 (MO) and TC3 (MT) keep `containing MMS_TYPE_INDICATOR='MO'` / `='MT'` and their obligation
  Set/Verify assertion steps with `obligation_id` intact (R2/R5).
- No step-1 file clause ever contains a fabricated `"&lt;word&gt; = &lt;word&gt;"` token; the only `=`
  allowed is the real `&lt;input_field&gt;='&lt;input_value&gt;'` form.
- The four generic variants render verbatim as specified (VZW, CBRS, other/non-MMS, generic TMO),
  with the plain generic as fallback.
- Authored QMetry step summaries (TC2/TC3 and TC10–TC17) pass through verbatim: no "Verify that "
  prefix, no first-word re-casing.
- Chalk-first RULE #1 / CHALK-GUARD unchanged: 17 TCs, 3/3 scenarios→TCs.
- Genuine NSL-provisioning (non-mediation) features unaffected (only the `batch_file`/mediation
  branch changes; all other `get_step_chain` branches are byte-for-byte unchanged).
- All existing tests stay green: 11 assertion-grounding + the mediation structural/classification
  tests + authored-verbatim tests + QMetry tests, plus the new `test_mediation_input_clause.py`.

## Notes / assumptions

- `get_step_chain` gains a keyword-only `obligation=None` param; all existing callers that omit it
  keep today's behavior. Only the mediation (`batch_file`) branch consumes it.
- Step 6 is written defensively because the prior fix's `test_authored_step_verbatim.py` is green,
  yet the user reports the artifact live on 4628. The step requires an explicit reproduce-or-prove
  before any sanitizer edit, so we neither skip a real bug nor edit a path that is already correct.
