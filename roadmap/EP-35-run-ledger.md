# EP-35 — Provenance run ledger

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-30 (Safe-query wrapper + audit log) · **Blocks:** EP-36 (Seed/determinism policy + resource logger), EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-52 (Backup of non-reproducible state (`mwh backup`)), EP-54 (Re-plan P3), EP-59 (Export primitives), EP-106 (Model registry + model cards), EP-124 (Simulation / ablation / benchmark harness), EP-134 (Runs & Provenance browser + Reports page / export gallery)

## Context

Every analysis from here on must be reproducible from a run id (GOVERNANCE §12, D-24). P2 left
three pieces in place: the DAG runner writes layer manifests whose hash is the snapshot id and
appends staging timings to `runs/benchmarks.jsonl` (EP-19); `safe_query` appends
`runs/audit.jsonl` and EP-30 created the first `runs.duckdb` views over it; the tracer bullet
(EP-31) produced a report without a formal run record. This brief builds
`src/mimicwarehouse/run.py` (DESIGN §11, §15): a context manager that assigns a `run_id`,
captures provenance, writes `runs/<run_id>/manifest.json`, appends `runs/ledger.jsonl`, and
formalizes the benchmark ledger schema; `mwh runs refresh` rebuilds `runs.duckdb` views over the
three JSONL ledgers plus the per-run manifests. Manifests contain hashes, counts, parameters and
SQL — never rows — so they are safe to show in tool output; attrition counts inside them are raw
inside the data root and pass through `disclose` (EP-43) on any export. `runs.duckdb` is written
only by `mwh runs refresh` (single-writer rule, DESIGN §6); the app opens it `READ_ONLY`.

## In scope

1. **`RunManifest` + `Run` context manager** (`src/mimicwarehouse/run.py`) — pydantic
   `RunManifest` (flat): `run_id` (`YYYYMMDDTHHMMSSZ-<6 hex>`), `name`, `kind`
   (`analysis|cohort|build|qc|phenotype|protocol|report|bench`), `tier`, `status`
   (`running|ok|failed`), `started/finished` UTC, `git_sha` + `git_dirty`, `uv_lock_sha256`,
   `duckdb_version`, `python_version`, `package_version`, `params` (JSON-able dict),
   `snapshot_ids` (layer → id, from `lake/manifests/` via the EP-19 helpers), `refs`
   (code-set / phenotype / cohort / protocol ids with version + hash; filled by later EPs),
   `sql` (name → relative path under `runs/<run_id>/sql/`), `attrition` (list of {step, label,
   n_units, n_subjects}), `seeds` and `resources` (populated by EP-36; optional here), `warnings`
   (captured `warnings.warn` + `r.warn()`), `wall_s`, `peak_rss_mb` (psutil, coarse; EP-36
   refines), `disk_delta_mb` (data-root drive free before/after), `protocol_id` /
   `protocol_hash` / `claim_type` (optional; filled by EP-51), `error` (type + one-line
   message on failure, no traceback bodies). API: `with run.start(name, tier=..., kind=...,
   params=...) as r:` → `r.run_id`, `r.dir`, `r.record_sql(name, sql)`, `r.record_ref(...)`,
   `r.record_attrition(rows)`, `r.read_layer(layer)` (records snapshot id), `r.save_table(name,
   df)` → `runs/<run_id>/tables/<name>.parquet`, `r.save_figure(name, obj)` →
   `runs/<run_id>/figures/`, `r.warn(msg)`. Exceptions inside the block mark `status=failed`,
   still write the manifest and ledger line, then re-raise.
2. **Ledgers** — append one JSON line per run to `runs/ledger.jsonl` (subset of the manifest:
   run_id, name, kind, tier, status, started, wall_s, git_sha, protocol_hash) with
   `O_APPEND` + UTF-8, `ensure_ascii=False`; formalize `runs/benchmarks.jsonl` by adding a
   pydantic `BenchmarkRecord` to EP-19's `dag/benchmarks.py` that validates its existing line
   schema (`ts, build_id, tier, step, kind, phase, wall_s, peak_rss_mb, rows, bytes_in, bytes_out,
   files, duckdb_version, git_sha, host, ok, error`) extended with optional `run_id` and
   `disk_delta_mb`, and widen `kind` to `stage|build|concept|mart|query|page|bench`;
   `run.bench(kind, name, **fields)` builds such a line and calls `dag.benchmarks.append` — the
   EP-19 module stays the only writer; older lines remain readable (`read_json_auto`,
   `union_by_name=true`).
3. **`runs.duckdb` views + `mwh runs`** — `mwh runs refresh` rebuilds `%MWH_DATA_ROOT%\
   warehouse\runs.duckdb` (build-to-`.new`-and-swap) with views `runs.ledger`,
   `runs.benchmarks`, `runs.audit` (keep EP-30's definition), `runs.manifests`
   (`read_json_auto('runs/*/manifest.json', union_by_name=true)`), `runs.attrition`
   (unnested); `mwh runs list [--tier] [--kind] [--last N]` (rich table), `mwh runs show
   <run_id>` (manifest, pretty), `mwh runs bench [--kind]`. `runs.duckdb` is never opened for
   writing by anything else.
4. **Reproduction block helper** — `run.reproduction_block(run_id) -> str` renders the Markdown
   block used by `docs/analyses/*` (EP-32 convention): run id, git sha, tier, snapshot ids,
   protocol hash, command line; the capstones (EP-53+) call it instead of hand-writing.
5. **Retrofit the tracer** — wrap the EP-31 tracer-bullet entry point in `run.start(...)`
   so it records SQL, attrition and snapshot ids (no change to its numbers); document in
   `docs/methods/provenance.md` (new): what a run records, where files live, retention (never
   auto-deleted; backed up by EP-52), and the "manifests never contain rows" rule.
6. **Tests** — `tests/ep/test_ep35.py` (`@pytest.mark.ep_35`): a run against a temp data
   root writes manifest + ledger line with all required fields; failure path sets
   `status=failed` and re-raises; `git_sha` matches `git rev-parse HEAD`; `record_attrition`
   round-trips; `mwh runs refresh` builds the views and `mwh runs list` shows the run;
   `BenchmarkRecord` validation rejects a malformed line; on dev (`dev` marker) a run wrapping
   one `safe_query` aggregate records the `core` snapshot id and a `sql/` file.

## Out of scope

- Seed derivation, numpy Generator plumbing, psutil peak-RSS sampling thread, GPU memory → EP-36.
- Suppression of attrition counts on export and `.disclosure.json` sidecars → EP-43 / EP-59.
- Protocol hashes and `mwh protocol run` → EP-51 (it fills `RunManifest.protocol_hash`).
- Backup of `runs/` ledgers → EP-52. Runs & Provenance page → EP-134. Model registry → EP-106.
- MLflow mirror and `mwh reproduce <run_id>` → already parked in `final-roadmap.md`.

## Verification / acceptance

- `uv run poe test -m ep_35` green on fixture and dev; `uv run --group dev mwh verify EP-35` green.
- `uv run --group dev mwh runs refresh` then `uv run --group dev mwh runs list --last 5` shows the
  dev test run and the retrofitted tracer run; `mwh runs show <run_id>` prints a manifest with
  `snapshot_ids`, `sql`, `attrition`, `git_sha`, `duckdb_version` and no row-level content.
- `runs/benchmarks.jsonl` lines written after this EP validate against `BenchmarkRecord`;
  `SELECT kind, count(*) FROM runs.benchmarks GROUP BY 1` works via `mwh sql`.
- `docs/methods/provenance.md` exists; DESIGN.md §11 gets a dated note if any field name differs
  from the list there.
