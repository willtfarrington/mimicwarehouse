# EP-19 — DAG runner `mwh build`

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-18 (Loader core B: subject buckets, sort, resume) · **Blocks:** EP-20 (Stage dimensions + small hosp/icu tables), EP-23 (Stage labevents ⏱), EP-24 (Stage emar + emar_detail ⏱), EP-25 (Stage remaining hosp tables ⏱), EP-26 (Stage chartevents ⏱), EP-27 (Stage icu event tables ⏱), EP-33 (Re-plan P2), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-50 (Events spine (MEDS-compatible) ⏱), EP-141 (Linkage Wizard B (validate → coverage → commit)), EP-148 (Notes staging ⏱ (segregated lake + notes.duckdb FTS))

## Context

**D-20**: a custom, lightweight transform runner (~600 LOC we control) instead of
dbt-duckdb/SQLMesh, because provenance capture and tier switching are the point. `mwh
build` is the **only writer** of the lake and catalogs (single-writer rule, DESIGN §6); it
runs a YAML DAG of `stage` / `sql` / `python` / `catalog` steps per tier, records manifests
and snapshot ids (DESIGN §5, §11) and appends build telemetry to the benchmark ledger
`runs/benchmarks.jsonl` (append-only JSONL, **D-24**). Because foreground shell commands are
capped at ~10 min, full-tier work is always a logged background job; this brief supplies
the job launcher every ⏱ brief uses (`--background --job <name>`, log at
`%MWH_DATA_ROOT%\runs\jobs\<name>.log`). What exists: loader (EP-17/18: `stage_unpartitioned`,
`stage_partitioned`, manifests, `status.json`, `DEV_BUCKETS`), settings and free-space guard
(EP-3), schema contract (EP-9), fixtures + tier markers (EP-11/12), the typer `mwh` skeleton
(EP-2) and `mwh verify` (EP-6). Windows facts: `spawn` multiprocessing (guard `__main__`),
detached processes need `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`,
`psutil` for pid liveness and RSS.

## In scope

1. **DAG spec** (`src/mimicwarehouse/dag/spec.py`, specs under
   `src/mimicwarehouse/dag/specs/*.yaml`) — pydantic `DagSpec{version, steps[]}` and
   `Step{name, kind: stage|sql|python|catalog, depends_on[], tags[], tiers[]}` plus per-kind
   fields: `stage` → `schema, table, source` (relative to the raw root, e.g.
   `mimic-iv-3.1/hosp/patients.csv`) plus optional `size_class`, `partitioned`, `sort_by`
   overrides (defaults come from the contract's `load_class`, `partitioned`, `sort_keys`,
   EP-9 — the YAML normally omits them);
   `sql` → `file` (under `dag/sql/`), `target`; `python` → `callable` (`module:function`);
   `catalog` → none (handler registered by EP-21). Validation: unique names, known
   dependencies, acyclic (`graphlib.TopologicalSorter`). Ship `stage.yaml` with three
   steps (`stage.mimiciv_hosp.patients`, `stage.mimiciv_hosp.admissions`,
   `stage.mimiciv_hosp.d_labitems`; EP-20 adds the rest) and a `catalog` step whose handler
   raises `NotImplementedError("EP-21")` until then. Step handlers live in a registry dict
   `STEP_HANDLERS[kind]` so later EPs add kinds without touching the runner.
2. **Runner** (`src/mimicwarehouse/dag/runner.py`) —
   `run(dag, tier, *, select=None, tags=None, force=False, dry_run=False, data_root=None, job=None) -> BuildResult`:
   `build_id = <UTC yyyymmddThhmmss>-<tier>-<git short sha>`; acquires the build lock
   `warehouse/.build.lock` (`pid, build_id, started`; refuse if the pid is alive; a stale
   lock is taken over only with `--break-lock`); runs the ≥ 100 GB free-space guard; opens
   the build connection (EP-17); resolves the tier's raw root (`fixture` →
   `tests/fixtures/` (EP-11/12's `mimic-iv-3.1/{hosp,icu}/<table>.csv` tree), `dev`/`full`
   → `source material/`, `demo` → set by EP-22) and
   bucket filter (`dev` → `DEV_BUCKETS`, else all); executes selected steps in topological
   order, skipping steps whose `status.json` entry is already complete for that tier unless
   `--force`; each step runs under a `StepContext` (settings, tier, build_id, con, log,
   raw_root, lake_root, buckets) with wall time and peak RSS sampled every 2 s by a
   daemon thread; a failing step stops the run, completed work stays complete
   (resumable by rerunning). `--dry-run` prints the ordered plan only. Ends with a rich
   summary table (step, rows, bytes, wall) — never rows.
3. **Snapshot ids** (`src/mimicwarehouse/dag/snapshot.py`) — `layer_snapshot(lake_root, layer="core", tier="full") -> str`
   = sha256 over the sorted manifest lines of every table whose status is complete for
   the tier (`dev_ready` suffices for `dev`); appended to `lake/manifests/snapshots.json`
   (history list of `{layer, tier, snapshot_id, build_id, ts}`) at the end of every build
   and printed. EP-21 stores it in `meta.catalog_info`; EP-35 cites it in run manifests.
4. **Benchmark ledger** (`src/mimicwarehouse/dag/benchmarks.py`) — `append(line)` /
   `read() -> polars.DataFrame` over `runs/benchmarks.jsonl` (append-only, one JSON per
   line, `O_APPEND` + flush): `{ts, build_id, tier, step, kind, phase: pass1|pass2|total,
   wall_s, peak_rss_mb, rows, bytes_in, bytes_out, files, duckdb_version, git_sha,
   host: {cpu, ram_gb}, ok, error}` plus one `kind: build` summary line per run. Nothing
   else writes to this file; EP-28/EP-32 read it.
5. **Background jobs** (`src/mimicwarehouse/dag/jobs.py`) —
   `launch(argv: list[str], job: str) -> JobInfo` spawns `uv run --group dev mwh …`
   detached (`subprocess.Popen(creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW, stdin=DEVNULL, stdout=<log>, stderr=STDOUT, close_fds=True, cwd=<workspace>)`),
   writes `runs/jobs/<job>.json` (`job, pid, argv, started, log, state: running|done|failed,
   exit_code, finished`); the child (told its name via `--job`) rewrites `state`,
   `exit_code`, `finished` on exit (also on exceptions/lock refusal). Log
   `runs/jobs/<job>.log`: INFO lines only (steps, counts, bytes, wall, rss) — no rows.
   `mwh jobs [--job NAME] [--tail 20]` lists jobs or prints one job's state and the last N
   log lines. Fallback recipe documented in the command help for when detaching misbehaves:
   `Start-Process pwsh -ArgumentList '-NoProfile','-c','uv run --group dev mwh build … *> $env:MWH_DATA_ROOT\runs\jobs\<name>.log'`.
6. **CLI + tests** — `mwh build --tier {fixture,dev,full,demo} [--select a,b] [--tag t]
   [--force] [--dry-run] [--data-root PATH] [--background --job NAME] [--break-lock]`
   and `mwh jobs`. `tests/ep/test_ep19.py` (`@pytest.mark.ep_19`), fixture: cycle and
   unknown dependency refused; dry-run order; a fixture build of the three steps into
   `tmp_path` yields manifests, `status.json`, a snapshot entry and ledger lines; a second
   run skips completed steps and `--force` reruns; a live lock (own pid) is refused, a
   stale lock only yields to `--break-lock`; `jobs.launch` on a trivial command produces
   `state = done` and a log. `tier("dev")`-marked: `uv run --group dev mwh build --tier dev --select stage.mimiciv_hosp.patients`
   (12 MB, foreground) writes `lake/core/mimiciv_hosp/patients/subject_bucket=0…4/` in the
   real lake and a ledger line; then
   `uv run --group dev mwh build --tier full --select stage.mimiciv_hosp.patients --background --job ep19-smoke`
   completes within a few minutes and `mwh jobs --job ep19-smoke` shows `done` — proving
   the background path on Windows before the ⏱ briefs rely on it. Add a dated note to
   `DESIGN.md` §15 for the new `mwh jobs` command.

## Out of scope

- Catalog step body → EP-21; demo raw root and column-map wiring → EP-22.
- The real table lists and their sort keys → EP-20, EP-23…EP-27; concept/`sql` steps → EP-37; spine `python` steps → EP-50.
- The analysis run ledger (`runs/ledger.jsonl`, `runs.duckdb` views, `mwh runs`) → EP-30/EP-35; the benchmark ledger here is build telemetry only.
- dbt-duckdb / SQLMesh as runners — already parked in `final-roadmap.md`.

## Verification / acceptance

- `uv run poe test -m ep_19` green on fixture; `tier("dev")`-marked tests green; `uv run --group dev mwh verify EP-19` green.
- The lock **refuses** a crafted concurrent build; a step failure leaves completed steps complete and the rerun resumes.
- `%MWH_DATA_ROOT%\runs\benchmarks.jsonl` has step + build lines; `runs\jobs\ep19-smoke.json` shows `done` and `runs\jobs\ep19-smoke.log` exists; `lake\manifests\snapshots.json` has an entry.
- `mwh build --tier full …` was only ever run with `--background`; no rows in any log or tool output.
