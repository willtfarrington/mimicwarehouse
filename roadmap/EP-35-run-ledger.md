# EP-35 — Provenance run ledger

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-30 (Safe-query wrapper + audit log) · **Blocks:** EP-36 (Seed/determinism policy + resource logger), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-52 (Backup of non-reproducible state (`mwh backup`)), EP-54 (Re-plan P3), EP-59 (Export primitives), EP-106 (Model registry + model cards), EP-124 (Simulation / ablation / benchmark harness), EP-134 (Runs & Provenance browser + Reports page / export gallery)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) `psutil` is a **core** dependency since EP-19 (D-15 addendum) — no dependency
> change here [FC-9]. (2) `snapshot_ids` follows the DESIGN §11 identifier glossary (D-43
> item 11): layer snapshot ids use the logical definition; `raw_snapshot_id` and per-file
> `source_sha256` are distinct fields — cite the glossary, don't re-define. (3) Environment
> capture: the run manifest embeds `mwh doctor --json` as shipped — **15 checks** since EP-16,
> with the `antivirus` warn expected on this host; a doctor invocation costs seconds (three
> PowerShell probes, ledger CFG-16), so capture it once per run, never per step. (4)
> `runs.duckdb` rebuilds use the rename-aside two-step (DESIGN §6 note, D-43 item 6).

> **EP-33 amendment (2026-09-01).** Header facts unchanged; overrides items 2–3 where they
> differ. (a) **No second model** (ledger P3C-6): `dag.benchmarks.BenchmarkLine` (frozen
> pydantic, `extra="forbid"`; fields `ts, build_id, tier, step, kind, phase, wall_s,
> peak_rss_mb, rows, bytes_in, bytes_out, files, duckdb_version, git_sha, host: HostInfo, ok,
> error`; `kind` is already a plain `str`, no widening needed) is the ledger schema — extend it
> with optional `run_id` and `disk_delta_mb` (default `None`, so older lines still validate)
> instead of adding `BenchmarkRecord`; `run.bench(kind, name, **fields)` builds a
> `BenchmarkLine` and calls `dag.benchmarks.append` (still the only writer). The reporting verb
> is the shipped `mwh runs benchmarks [--tier full|all] [--kind stage|all] [--format table|md]
> [--out]` (EP-32) — this brief adds the `concept|mart|query|bench` kind values (and `list`/
> `show` as planned); **`mwh runs bench` never existed** and is not added. (b) **Audit ledger
> convention** (EP-33 B1d): `runs/audit.jsonl` lines carry a `usage: `-prefixed
> `refusal_reason` for argument/usage errors (`SafeQueryError`, exit 2) as distinct from gate
> refusals (`SafeQueryRefused`, exit 3); environment errors (`CatalogOpenError`) are unaudited.
> `runs.audit` keeps EP-30's definition and the ledger views this brief adds separate refusals
> from usage errors deliberately (`refusal_reason LIKE 'usage: %'`) rather than lumping them.
> (c) **`runs` is not a registry exemption** (checkpoint decision): `runs.*` reads through
> `safe_query` stay under the aggregate-only rules, so GROUP BYs over `runs.audit`/
> `runs.benchmarks`/`runs.ledger` need a real count-family node (an alias alone never
> satisfies it — EP-33 B1). Ledger label columns whose values legitimately exceed 64 chars
> (`refusal_reason`, `error`) are added to `safe.LABEL_COLUMN_NAMES` by this brief (the shipped
> set is `{description, short_description}`) so the free-text result check admits them.
> (d) **Builders and readers**: `runs.duckdb` is built by `safe.build_runs_db` and published
> with `publish.swap_file` (the one publish primitive; the EP-170 item 4 wording "rename-aside
> two-step" now names `publish.swap_file`; `mimicwarehouse.paths` is gone); `mwh runs refresh`
> calls it; readers ATTACH it through `engine.attach_read_only(con, path, alias)` (`ATTACH IF
> NOT EXISTS ... READ_ONLY`). Every JSONL append (`runs/ledger.jsonl` included) goes through
> `fsio.append_jsonl` (O_APPEND, short-write check, fsync — EP-33 LGR-2/LGR-4) and reads through
> `fsio.read_jsonl`/`iter_jsonl` (one torn trailing line tolerated — LGR-1); `run.py` has no
> local `O_APPEND` writer, and manifests are written with `fsio.atomic_write_text`. (e) **The
> `run_id` form stands** (ledger P3C-1, rejected with reason): `YYYYMMDDTHHMMSSZ-<6 hex>` is
> safe in ledger *content* because guard `ID_TOKEN` cannot match a digit run abutting `T`; it
> must never appear in a committed **file name** (`docs/committed-text.md` rule 3, the EP-32
> lesson) — `run.reproduction_block` quotes it inline only. (f) CLI errors go to stderr via
> `console.fail` with `console.EXIT_USAGE`/`EXIT_REFUSED`; `--json` output via
> `console.emit_json` (raw ints, never pasted into tracked files).

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

> **Completion note (2026-09-05).** Shipped as `src/mimicwarehouse/run.py` (`start` context
> manager, `RunManifest` + `AttritionRow` / `RefEntry` / `RunErrorInfo`, `Run.record_sql /
> record_ref / record_attrition / record_snapshot / read_layer / record_audit / safe_query /
> save_table / save_figure / warn / bench`, `bench`, `runs_db_views`, `reproduction_block`,
> `list_runs` / `read_manifest`), `docs/methods/provenance.md` (new), `mwh runs list [--tier]
> [--kind] [--last N] [--json]` + `mwh runs show <run_id> [--json]` in `runs_cli.py`,
> `safe.build_runs_db` creating the five views (`audit` · `ledger` · `benchmarks` · `manifests` ·
> `attrition`; `safe.RUNS_DB_VIEWS`), `LABEL_COLUMN_NAMES` += `refusal_reason` / `error`,
> `safe.identifier_column_names()`, `dag.benchmarks.BenchmarkLine` += optional `run_id` /
> `disk_delta_mb` + `BENCHMARK_KINDS`, `engine.detach`, the tracer retrofit (`run_tracer` runs
> inside `run.start`; `descriptive_statements`, `STEP_LABELS`, `MODEL_FRAME_SELECT`), the DESIGN
> §11 note + §15 rows, the D-24 addendum, two `docs/gotchas.md` entries, the README rows / quick
> start, and `tests/ep/test_ep35.py` (18 fixture + 2 dev tests). **Judgment calls (owner
> review):** (1) the manifest embeds the doctor checks as `{id, status, value}` — the
> machine-readable payloads `CheckResult.value` was designed to carry — not the prose `detail`
> (paths and product names over 64 chars sit in `value`; the run-folder 64-char rule stays
> scoped to the analysis artefacts, provenance.md §4), captured **once per process per data
> root** (`run.environment_block`; the probes cost 5.9 s; `start(..., doctor=False)` skips it);
> (2) the tracer keeps its `runs/tracer/<stamp>-<tier>/` report folder and cites the run under
> `ledger_run_id` — EP-31's numbers, `test_ep31` and the `docs/analyses` index that points at that
> folder are untouched; (3) the manifest carries four fields beyond the brief's list — `command`
> (the line `reproduction_block` prints; the tracer passes `mwh tracer --tier <t>`), `tables` /
> `figures` (`{name: relative path}` like `sql`), `audit_ids` (the tracer manifest had them) —
> and `git_sha` is the **full** sha (the brief's `git rev-parse HEAD` test) while audit /
> benchmark lines keep the short one; (4) the ledger views bind **explicit column types** via
> `read_json(..., columns = {...})` (dict fields as `JSON`, `MANIFEST_COLUMNS` pinned to the
> model's fields by test) rather than `read_json_auto` — a probe showed auto-inference unifies
> heterogeneous `params` into one wide STRUCT and an empty glob raises, so a run root without
> manifests gets a typed empty view and older lines read NULL for newer fields; (5) `safe_query`
> now **detaches before it attaches** `runs.duckdb` (`engine.detach` + `attach_read_only`): the
> probe confirmed an `ATTACH IF NOT EXISTS` on the path-keyed instance keeps serving the
> delete-pending pre-refresh file ("view ledger does not exist" after `mwh runs refresh` inside
> pytest or the app) — the EP-33 test `test_pre_execution_catalog_error_is_refused_and_audited`
> had already worked around the same fact; a first cut passed `refresh=True` through
> `attach_read_only` and broke that test's three-argument stub, so the detach is its own engine
> helper and **no earlier test was edited**; (6) `fsio.append_jsonl`'s `ensure_ascii` default
> stands over the brief's `ensure_ascii=False` (the canon is the writer; non-ASCII escapes are
> valid JSON); (7) `BENCHMARK_KINDS` includes the runner's `sql` / `python` / `catalog` step
> kinds beside the brief's list — the live full-tier ledger already carries `kind: catalog`;
> `kind` stays a plain `str`; (8) third-party text (exception messages, captured
> `warnings.warn`) enters a manifest only through `safe.sanitize_error_text` (literals and
> numbers masked, 200 chars), `r.warn` text is trimmed to one line; `save_table` refuses
> identifier columns by name; `mwh runs show` validates the id shape before touching a path;
> (9) `warnings` capture is `warnings.catch_warnings(record=True)` — process-global, so nested
> or threaded runs share it (documented; EP-36's sampler thread is unaffected); (10) `r.safe_query`
> (statement + `core` snapshot id + audit id in one call) is a convenience beyond the brief's API
> list. **Observation (owner FYI):** on dev, `SELECT kind, status, count(*) AS n FROM runs.ledger
> GROUP BY 1, 2` returned no rows — every run count was below k = 11 and `runs` is not a registry
> schema (EP-33 checkpoint decision c), so per-run counts through `mwh sql` stay suppressed on
> the credentialed tiers by design; `mwh runs list` reads the JSONL directly and is the way to
> see individual runs. **Gates:** `poe check` 871 passed (265 s; ruff check, ruff format --check and pyright
> clean); `mwh verify EP-35` 18 passed (50 s);
> `pytest -m ep_35 --tier dev` 20 passed (54 s); `mwh verify` EP-30 33 · EP-31 7 · EP-32 14 ·
> EP-33 115 · EP-34 17 all exit 0; `poe roadmap-check --strict` 0 errors, 0 warnings; `mwh guard`
> clean over the new and edited files. **Dev acceptance (real root, sequential):** dev test runs
> `20260905T180430Z-556846` (one `safe_query` aggregate; `core` snapshot id + `sql/patients_by_era.sql`)
> and `20260905T180436Z-d7b109` (`read_layer("core")`); the retrofitted tracer run
> `20260905T180437Z-70028a` (`mwh tracer --tier dev`: cohort n = 3,208, model fit, AUC 0.744,
> 7 audited calls, wall 2.3 s — identical to EP-31/EP-34; nine `sql/` files, five attrition rows,
> the `core` snapshot id, claim type recorded); `mwh runs refresh` then `mwh runs list --last 5`
> shows all three; `mwh runs show 20260905T180437Z-70028a` prints `snapshot_ids`, `sql`,
> `attrition`, `git_sha`, `duckdb_version` and no row-level content; `mwh sql "SELECT kind,
> count(*) FROM runs.benchmarks GROUP BY 1" --tier dev` returned build 37 · catalog 25 · stage 181
> · verify 64 (1 row suppressed). Windows power mode read *Best performance* on AC for the whole
> session (`mwh doctor` `power_scheme`). No catalog was rebuilt; core snapshot ids unchanged.
> Nothing parked.
> **Owner decisions (2026-09-05, session-end review):** commit as the standard two-step pair
> (this note included; no push — the owner pushes); keep the reduced `{id, status, value}`
> doctor block captured once per process (judgment call 1); keep the tracer's own report folder
> beside the run record (2); keep `safe_query`'s detach-before-attach (5); keep `runs` outside the
> registry exemption — per-run counts stay k-suppressed on credentialed tiers, revisit at the
> EP-54 re-plan if inconvenient (the observation above).
