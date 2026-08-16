# EP-62 — Cohort Builder page

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-48 (Attrition diagram renderer) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Capability 2 (reproducible cohort construction) already exists as code: the pydantic/YAML
`CohortSpec` and registry (EP-46: grain, inclusion, exclusion, index event, observation window,
washout, follow-up, era filter, code-set/phenotype refs by version), the compiler that emits a
deterministic CTE chain, materialises `marts/cohorts/<cohort_id>@<version>/`, records per-step
attrition and a run record (EP-47, EP-35), and the disclosure-aware attrition renderer
(EP-48: Mermaid primary, Altair fallback). This brief puts a Streamlit UI over that stack (D-21; DESIGN §16
"Cohort Builder"), following the EP-61 page pattern on the EP-57 shell with EP-58 wrappers
(small cells badged in-app, D-33).
Two rules shape it: the app opens catalogs READ_ONLY (single-writer, DESIGN §6) — cohort
materialisation writes Parquet + run records through the EP-47 API and never touches a
`.duckdb` writer from the Streamlit process — and full-tier materialisation is a logged
background job, never a foreground scan. Streamlit cannot render Mermaid without a CDN, so the
Altair fallback is the in-app diagram. Era filters use `anchor_year_group` only.

## In scope

1. **Spec form** — `src/mimicwarehouse/ui/forms.py: model_form(Model, state_key)` turns a
   pydantic model's JSON schema into widgets (enum → selectbox, int/float → number_input, bool
   → checkbox, list-of-criteria → add/remove rows in `st.session_state`, refs to code sets /
   phenotypes → selectbox from the EP-40/41 registries with version pin, grain → EP-34
   registry, era → multiselect over `anchor_year_group`). Page `app/pages/20_cohort_builder.py`
   (registry id `cohort_builder`, section Cohorts): left column = form; "Load" from
   `cohort.registry.list()` or from pasted YAML text (validated; no file uploads of data);
   validation errors shown inline.
2. **Compile & preview** — `cohort.compiler.compile(spec, tier)` → CTE SQL in `st.code`
   (SQL only, no data); "Preview counts" runs the compiler's per-step counting path through
   `ui.conn.query` on demo/dev in-process; on full it is allowed in-process only if the same
   preview took < 3 s on dev in this session, otherwise it goes the job route (item 3).
3. **Run / materialise** — demo/dev: `cohort.compiler.materialize(spec, tier)` in-process with a
   spinner (writes Parquet + attrition + run record via EP-47). full:
   `src/mimicwarehouse/ui/jobs.py: launch(cmd, log_name) -> JobHandle` starts
   `mwh cohort run <spec.yaml> --tier full` (EP-47's CLI; add the thin CLI there if EP-47 only
   exposed the Python API) as a detached subprocess with log
   `%MWH_DATA_ROOT%\runs\jobs\cohort-<id>-full.log`; the page shows job status from the log's
   runner-status lines (never data) and refreshes the run ledger on completion. If EP-47
   registers cohort views through `mwh build --target cohorts`, the page shows that command
   instead of running the writer itself.
4. **Attrition + provenance** — `cohort.attrition.render(table, fmt="altair")` (EP-48) via
   `safe_altair`; Mermaid source in an expander (`st.code(language="mermaid")`); per-step table
   (n, dropped) via `safe_dataframe`; after a run: run id, manifest path, snapshot ids, cohort
   hash; "Save as new version" → `cohort.registry.save(spec)` (semver bump per EP-46);
   "Open in Table 1" handoff via `st.query_params` (`?cohort=<id>@<version>`, consumed by
   EP-71) and `st.switch_page`.
5. **Latency + screenshots** — loading a materialised cohort's attrition on full ≤ 5 s
   (`MWH_APP_RECORD_LATENCY=1`, completion note); manifest entries `cohort-builder-form`,
   `cohort-builder-attrition` captured on demo.
6. **Tests** `tests/ep/test_ep62.py` (`@pytest.mark.ep_62`, ui group): AppTest on fixture —
   load the tracer spec (`first_icu_stay_adults`, EP-31/46) → compile shows SQL starting with
   `WITH`; run → attrition table with ≥ 3 steps and final n equal to EP-47's fixture
   expectation; a spec with `age_min > age_max` shows a validation error; with `mwh.tier="full"`
   the run button calls `ui.jobs.launch` (mocked `subprocess.Popen`), not the in-process path;
   `ui_lint` passes; dev-marked — run on dev completes < 60 s; full — tracer materialisation
   job launched or EP-47's full run reused, page latency recorded.

## Out of scope

- Cohort spec semantics, compiler, registry → EP-46/47; diagram rendering → EP-48.
- Table 1 over the cohort → EP-71; phenotype browsing → EP-63; protocol freeze UI → EP-128.
- Cohort diff/versions viewer → parked (v2 COH-3, already listed).

## Verification / acceptance

- `uv run --group ui poe test -m ep_62` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-62` green (includes `ui_lint`).
- On dev: load → compile → run → attrition diagram + run id visible; saving creates a new
  registry version; the Table 1 handoff sets `?cohort=`.
- On full: run launched only as a background job (log path in the completion note); full-tier
  attrition load latency recorded (≤ 5 s).
- Demo screenshots `cohort-builder-form-*.png`, `cohort-builder-attrition-*.png` with sidecars.
