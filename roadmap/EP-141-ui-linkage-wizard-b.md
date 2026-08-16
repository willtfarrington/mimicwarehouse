# EP-141 — Linkage Wizard B (validate → coverage → commit)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-140 (Linkage Wizard A (profile → map)), EP-139 (Key validation, join cardinality, linkage coverage), EP-19 (DAG runner `mwh build`) · **Blocks:** EP-142 (ED ingestion via wizard → mimiciv_ed + ED concepts), EP-143 (Reference-table ingestion via wizard (ATC / Elixhauser / LOINC map)), EP-145 (Second subject-keyed PhysioNet source via wizard (stretch)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Completes the Linkage Wizard (D-36) with the validate → coverage → commit steps. Commit is the
sensitive part: the wizard must **never** write the lake or a catalog itself. `mwh build` is the
only writer (single-writer rule, DESIGN §6), so commit means "emit a DAG step for this source and
launch `mwh build`" — in the foreground for fixture/dev, as a logged background job for full
(CLAUDE.md §3). Committed subject-keyed sources land in `lake/core/mimiciv_ed/<table>/subject_bucket=NN/`
(EP-18 bucketing, dev = buckets 0–4 stays a partition filter), reference tables in `lake/ref/<source>/`
unpartitioned; the catalog builder (EP-21) exposes them as `mimiciv_ed.*` / `ref.*` views with a
new snapshot id recorded in the register. Category 35; builds on EP-139 for the checks and EP-140
for the page/state.

## Scope sketch (refine at re-plan)

1. **Step 3 Validate** (page + `wizard.py`) — runs `validation.validate_keys` and `join_cardinality`
   for the selected tier; renders pass/warn/fail per check with thresholds, orphan counts (suppressed
   badge < 11), cardinality matrix; a failing PK check blocks the next step; a warn requires an
   explicit "acknowledge" checkbox recorded in `validation.json`.
2. **Step 4 Coverage** — runs `validation.coverage`; renders source-side/core-side coverage tables and
   the coverage-by-`anchor_year_group` chart (Altair via `viz/`), the temporal-consistency share, and
   the disclosure-checked `linkage_report.md` preview; "Export report" goes through the EP-59 export
   primitives.
3. **Step 5 Commit** — `wizard.emit_dag_step(source_id, mapping)` writes a DAG step file in the EP-19
   format (name `ext_<source_id>`: load via the mapped view → bucketed or unpartitioned Parquet with
   manifests → catalog attach → snapshot id) under the DAG directory EP-19 fixed; the page shows the
   generated YAML, then launches `uv run --group dev mwh build --tier <tier> --select ext_<source_id>`
   (selector flag as named by EP-19): foreground with live log for fixture/dev, background job with
   log at `%MWH_DATA_ROOT%\runs\jobs\ext_<source_id>_<tier>.log` for full; job id, snapshot id and
   status `committed` written to `source.yaml` / `registry.yaml`.
4. **Post-commit hooks** — `safe_query` allow-list extended to `mimiciv_ed.*` and `ref.*` schemas with
   `free_text`-flagged columns (from the mapping) added to its refusal list; catalog `COMMENT`s from
   the mapping; a `mwh link status <source_id>` verb summarising the five steps.
5. **Tests** `tests/ep/test_ep141.py` (`@pytest.mark.ep_141`, fixture): end-to-end on the ED-like
   fixture — validate → coverage → commit produces `mimiciv_ed.edstays` etc. in the fixture catalog
   with bucket partitions and a manifest line; the reference-table fixture lands in `ref.*`
   unpartitioned; a fixture with a duplicated PK is *refused* at step 3 (no DAG step emitted);
   `safe_query("SELECT chiefcomplaint …")` is refused after commit; AppTest smoke for steps 3–5.

## Out of scope

- Real ED / reference-table / second-source runs → EP-142 / EP-143 / EP-145.
- ED-specific derived concepts and the `edstay` grain → EP-142.
- Any direct DuckDB write from the app (forbidden by design).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_141` (+ `uv run --group ui poe test -m ep_141` for AppTest) and
  `uv run --group dev mwh verify EP-141` green on fixture.
- Wizard on the ED-like fixture completes all five steps; `mwh link status edlike` shows `committed`
  with a snapshot id; `mwh sql "SELECT count(*) FROM mimiciv_ed.edstays" --tier fixture` returns a count.
- The crafted PK violation is refused in a test; the emitted DAG YAML is committed as a golden file
  (contains only schema/paths, no data).
- No full-tier run here; the full-tier commit path is exercised and timed by EP-142.
