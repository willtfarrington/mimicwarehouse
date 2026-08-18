# EP-10 — Raw inventory manifest ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-16) · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-9 (Schema registry (YAML contract)) · **Blocks:** EP-16 (Re-plan P1)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) `require_free_space` / `check_free_space` are **module-level** functions of `mimicwarehouse.config`
> (`require_free_space(path, min_gb) -> FreeSpace`, raising `DiskGuardError`), not members of
> `get_settings()`; call `require_free_space(settings.data_root, settings.min_free_gb)`. (2) There is **no
> `MWH_DATA_ROOT` in the environment** and no `.env`/`mwh.toml` in the workspace (only `.env.example`; and a
> `.env` is read by pydantic-settings, never exported to the shell), so `"$env:MWH_DATA_ROOT\runs\jobs"`
> would expand to `\runs\jobs` — the PowerShell recipe in item 5 now derives the job directory from
> `mwh paths --json` (`.data_root`, or the `layout` row `runs_jobs`), and the `%MWH_DATA_ROOT%` paths in
> items 2/verification are read as "under `settings.data_root`" (`C:\mimicdata` by default). (3)
> `lake/manifests/raw/` is **not** a `Settings.layout` key (the 15 keys stop at `lake_manifests`) — the module
> creates it itself (`mkdir(parents=True, exist_ok=True)`), likewise `mimicwarehouse/docs/resources/` if
> EP-13/14/15 have not run yet. (4) Tier vocabulary: pytest tier *selection* arrives with EP-12, which runs
> after this brief; here "dev" in the header means the bounded `--max-bytes` foreground pass, and the test
> module is fixture-only (`tmp_path` CSVs) — no `tier("dev")` marker in `test_ep10.py`. (5) Endpoint
> security (Risk 12, D-38, D-42): Defender still excludes only the data root, but **Malwarebytes** now
> excludes both `C:\mimicdata` and `source material\`, and its Ransomware Protection judges processes by I/O
> pattern — the full pass runs from the allow-listed managed `python.exe`/`.venv`, but the wall-time
> estimate is unverified with two engines; if the background job vanishes, check Malwarebytes Quarantine /
> `mbamservice.log` before Defender. (6) `mwh inventory` correctly receives *validated* settings (it writes
> under the data root); `mwh doctor` at EP-7: 414.9 GB free. Command forms: `uv run mwh …` ≡ `uv run --group
> dev mwh …`.

## Context

The 41 raw CSVs under `source material/` (~98 GB; `chartevents.csv` alone 40 GB) were decompressed
in place and the `.csv.gz` archives deleted, so PhysioNet's `SHA256SUMS.txt` (which covers only the
archives) cannot verify them (README Risk 1). **D-26** therefore makes a locally computed manifest —
SHA-256, byte size, header, row count per file, reconciled against the row counts in mimic-code's
vendored `validate.sql` (EP-8) — the **raw snapshot id** that every lake manifest (EP-17+, DESIGN §5)
cites as its `source manifest id`. EP-3 gives us `mimicwarehouse.config` (`get_settings()`: `source_root`, `layout["lake_manifests"]`,
`layout["runs_jobs"]`, `duckdb_settings("build")`; module-level `require_free_space(path, min_gb)` — amended EP-7); EP-9 gives the contract
(`load_contract()`, `Table.csv_path`, `read_csv_columns()`). Machine facts: single NVMe; Defender's
real-time exclusion covers the data root, **not** `source material/`, so hashing runs at Defender speed;
the `source material` path contains a space (always `pathlib`, never string-built shell commands);
foreground shell commands are capped at ~10 min, so the full pass is a logged **background job**
(**D-18**). Governance: the module reads the files (that is its job) but its outputs are hashes, byte
counts, column names and row counts only — never a row, never a value (GOVERNANCE §4; the header line
is schema, not data). `mwh sql`/`safe_query` do not exist yet (EP-30); nothing here needs them.

## In scope

1. **Inventory module** (`src/mimicwarehouse/inventory.py`): `inventory_file(path, table) -> FileRecord` with
   `dataset, module, table, rel_path, bytes, mtime, sha256, header: list[str], header_matches_contract: bool,
   missing_columns, extra_columns, rows: int | None, rowcount_method, csv_parallel_fallback: bool, seconds_hash,
   seconds_rows`. SHA-256 via `hashlib.file_digest(f, "sha256")` (streaming); header via reading the first line
   only; rows via DuckDB `SELECT count(*) FROM read_csv(?, header=true, all_varchar=true, …)` on a connection
   configured with `get_settings().duckdb_settings("build")` (explicit `memory_limit`, `threads`, `temp_directory`,
   `max_temp_directory_size`, `preserve_insertion_order=false`) — quoted embedded newlines (`labevents.comments`, note text) make newline
   counting wrong, so DuckDB is the counter; if the parallel reader fails on a file, retry with `parallel=false`
   and record `csv_parallel_fallback=true`. Also parse each dataset's `SHA256SUMS.txt` (hashes + `.csv.gz` names)
   into `physionet_gz_sha256` per file for the parked `.csv.gz` re-verification (v2 RAW-1).
2. **Manifest store + snapshot id**: one JSONL line per file at
   `<data_root>\lake\manifests\raw\<dataset-dir>.jsonl` (`mimic-iv-3.1`, `mimic-iv-ed-2.2`, `mimic-iv-note-…`;
   `settings.layout["lake_manifests"] / "raw"` — the `raw\` level is not a layout key, the module creates it;
   amended EP-7) and `<data_root>\lake\manifests\raw\raw_snapshot.json` with `raw_snapshot_id = sha256` over the sorted
   canonical `(rel_path, bytes, sha256, rows)` tuples, `files_expected=41`, `files_done`, `started/finished`,
   `duckdb_version`, `git_sha`, `mimic_code_sha` (EP-8 `vendor_info()`), per-dataset totals. `raw_snapshot_id` is
   `None` until all 41 files are present. Expose `load_raw_manifest()` and `raw_snapshot_id()` for the loader.
3. **CLI** (`mwh inventory build [--dataset <dir>] [--max-bytes N] [--resume] [--force] [--no-rowcount] [--log <path>]`,
   `mwh inventory show`, `mwh inventory reconcile`): `build` walks the contract's `csv_path`s under the source root
   from config, skips files whose `(bytes, mtime)` already match a manifest line unless `--force` (`--resume` is
   the default), processes files sequentially (one NVMe; DuckDB threads do the parallelism), logs one line per
   file with wall time and MB/s, refuses to start when free space is < 100 GB (config guard) or the source root
   is missing. `show [--timing]` prints a table of `table, bytes, rows, header ok, sha256[:12]` (with `--timing`:
   seconds and MB/s per file, per-dataset totals) plus the job status kept in `raw_snapshot.json`
   (`started, finished, last_file, files_done, errors`) — no data. This matters because sessions cannot read
   anything under the data root (deny rules), including the job log: `show` is how EP-16 verifies the job.
   Record the `mwh inventory` addition as a dated note in `DESIGN.md` §15.
4. **Reconciliation** (`mimicwarehouse.inventory.parse_validate_sql(path) -> dict[table, int]`, tolerant regex over
   the upstream `'table' … <int>` pairs, ignoring the EP-8 `-- mwh-guard: allow` suffixes; unit-tested on a synthetic
   snippet; plus `expected_counts(dataset="mimic-iv-3.1") -> dict[table, int]` reading the vendored file — the
   name EP-20/EP-28 call) and `mwh inventory reconcile`:
   `table, expected (validate.sql), observed, delta, status ∈ {match, mismatch, no-expectation}` for hosp/icu (and
   ED if the vendored ED `validate.sql` exists). MIMIC-IV 3.1 removed two orphan `subject_id`s vs 3.0 — if the pinned
   `validate.sql` targets 3.0, small deltas in `patients`/`admissions`-linked tables are expected; record them.
   Write the reconciliation table + per-file hashes/bytes/rows/header status into
   `mimicwarehouse/docs/resources/raw-inventory.md` (a manifest of hashes/counts/schema — allowed in git by
   GOVERNANCE §3 without a disclosure sidecar; every count here is in the thousands or more). Format every integer
   in that file and in completion notes with thousands separators (`123,456,789`): the guard's G4 rule (EP-4)
   refuses any bare 8-digit token starting with 1–3, and byte sizes / row counts routinely are.
5. **Runs**: (a) **dev-like foreground pass** — `uv run --group dev mwh inventory build --max-bytes 1000000000`
   (every file < 1 GB, ~28 files, a few minutes) in the session — this foreground pass is deliberate: it is
   bounded by `--max-bytes`, finishes well inside the ~10 min foreground cap, and emits hashes, counts and column
   names only (never rows), so D-18's background rule for long full jobs does not bite; (b) **full ⏱
   background job** for the rest:
   ```powershell
   # from the workspace dir (cd mimicwarehouse); the data root comes from settings, not from an env var
   # (no MWH_DATA_ROOT is set on this machine; default C:\mimicdata) — amended EP-7
   $paths = uv run --group dev mwh paths --json | ConvertFrom-Json
   $jobs = ($paths.layout | Where-Object key -eq 'runs_jobs').path   # = <data_root>\runs\jobs (exists since EP-3)
   $mwhArgs = @('run','--group','dev','mwh','inventory','build','--resume','--log',"$jobs\ep10-raw-inventory.log")
   (Start-Process -FilePath uv -ArgumentList $mwhArgs -WorkingDirectory (Get-Location).Path -NoNewWindow -PassThru `
      -RedirectStandardOutput "$jobs\ep10-raw-inventory.out" -RedirectStandardError "$jobs\ep10-raw-inventory.err").Id
   ```
   Expect 10–30 min (hash + parse of 98 GB; Defender scans `source material\` in real time, Malwarebytes
   excludes it — the estimate is unverified with two engines; amended EP-7). Record the PID, start time and log
   path in the completion note; **do not wait for it** — EP-16 verifies. If the process vanishes, check
   Malwarebytes Quarantine / `mbamservice.log` first (Risk 12, D-42); the job is resumable.
6. **Tests** (`tests/ep/test_ep10.py`, `@pytest.mark.ep_10`, fixture tier = synthetic CSVs written to `tmp_path`
   with contract headers and ids ≥ 90 000 000): sha256 equals `hashlib` over the bytes; row count correct with quoted
   embedded newlines and CRLF; header mismatch detected (extra/missing column names); `parse_validate_sql` on a
   snippet; `raw_snapshot_id` deterministic and independent of processing order; `--resume` skips unchanged files
   and `--force` recomputes; the CLI output of `show`/`reconcile` contains no fixture id and no cell value (assert
   on captured stdout).

## Out of scope

- Loading anything into Parquet/DuckDB → EP-17/18; `lake/manifests/<build_id>.jsonl` → EP-19.
- Re-downloading `.csv.gz` to restore checksum-verifiable raw → parked (v2 RAW-1).
- Timing/RSS/disk to the benchmark ledger (`runs/benchmarks.jsonl` is EP-19's) → EP-16 records timing in this
  brief's completion note instead.
- Data-quality profiling (null %, ranges) → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_10` and `uv run --group dev mwh verify EP-10` green on fixture.
- Foreground pass done: `uv run --group dev mwh inventory show` lists every file < 1 GB with `header ok = True`
  (any `False` is a finding: record the column-name diff in the completion note and in README Risks — it means the
  contract or the local files differ from the pinned DDL).
- Full pass launched in the background; log at `<data_root>\runs\jobs\ep10-raw-inventory.log`
  (`settings.layout["runs_jobs"]`; amended EP-7); PID and start
  time recorded in the `> **Completion note (date).**` block of this brief; timing, `raw_snapshot_id`,
  reconciliation status and `docs/resources/raw-inventory.md` are completed by **EP-16**.
- Commit `feat(mimicwarehouse): raw inventory manifest + reconcile (EP-10)` (code, tests, DESIGN note; the docs
  table lands with EP-16), then `docs(roadmap): record EP-10 commit hash`.

> **Completion note (2026-08-17).** Executed as one autonomous session (≈ 1 h against M ≈ 1 h), tiers
> fixture (tests) + the bounded foreground pass + the full ⏱ background job — which **finished inside the
> session** (see item 5), so everything the acceptance list hands to EP-16 is already recorded here and EP-16
> only re-verifies. No row, value or identifier from the data entered this session: every command output was
> hashes, byte counts, column-name status, row counts and job status (GOVERNANCE §4).
>
> **Items 1–4, 6 — as specified.** `src/mimicwarehouse/inventory.py` (module + `inventory_app`; attached with
> one `app.add_typer` line in `cli.py`; **not** in `DIAGNOSTIC_COMMANDS` because `build` writes under the data
> root), `tests/ep/test_ep10.py` (28 tests, marker `ep_10`, fixture tier: the contract's 41 CSVs synthesised
> under `tmp_path` with contract headers, ids ≥ 90 000 000 and a sentinel cell value that must never surface;
> quoted embedded newlines + CRLF; header missing/extra/order; `parse_validate_sql` on a snippet; snapshot id
> deterministic + order-independent; `--resume` skips / `--force` recomputes; `show` / `reconcile` /
> `build --log` outputs leak-free by assertion incl. the guard's own `id_band_hits`), DESIGN §15 note,
> `docs/resources/raw-inventory.md` (complete — see item 5). `poe check` green (ruff, pyright, **319 tests**),
> `uv run mwh verify EP-10` green. `mwh --help` +≈ 9 ms of imports (pydantic model + csv/hashlib; duckdb,
> the contract and the vendor pin are imported inside functions), 0.50–0.55 s wall here (noise-bound).
> `test_ep06::test_mwh_verify_usage_errors` now probes **EP-11** as its "code brief without a test module".
>
> **Item 5 — runs (all timings from the manifest lines / `raw_snapshot.json` as printed by `mwh inventory
> show --timing`; the log itself is under the data root and was never read by the session):**
> - **(a) Foreground pass** `uv run --group dev mwh inventory build --max-bytes 1000000000 --log
>   C:\mimicdata\runs\jobs\ep10-raw-inventory.log`: **29 files** (not ~28 — the 1 GB cut leaves
>   `datetimeevents` 1.09 GB to the background job), 3,974,150,546 bytes, started 2026-08-18T03:48:02Z,
>   finished 03:48:06Z — **4.6 s** wall (hash 0.9–2.2 GB/s, DuckDB count at the same rate; the first
>   `read_csv` paid ~0.4 s of connection warm-up), 0 errors, **29/29 `header ok = True`**.
> - **(b) Full ⏱ background job** launched by the item-5 PowerShell recipe from the workspace dir:
>   `Start-Process uv run --group dev mwh inventory build --resume --log C:\mimicdata\runs\jobs\ep10-raw-inventory.log`,
>   **PID 31608** (uv.exe; the managed `python.exe` it spawned recorded `pid 6700` in `raw_snapshot.json`),
>   **started 2026-08-18T03:48:36Z (23:48:36 EDT 2026-08-17)**, stdout/stderr redirected to
>   `C:\mimicdata\runs\jobs\ep10-raw-inventory.out` / `.err`, log appended to the same
>   `ep10-raw-inventory.log` as the foreground pass. It **finished at 03:50:04Z — 88 s wall** for the 12
>   remaining files (100,667,717,547 bytes): `chartevents` 41,935,806,083 bytes hashed in 17.4 s (2,417 MB/s)
>   and counted in 17.7 s; `labevents` 18.4 GB in 7.9 s + 7.4 s; `emar_detail` 8.7 GB in 3.7 s + 4.2 s;
>   every large file at 2.0–2.4 GB/s for both passes. **0 errors, no `parallel=false` fallback needed
>   anywhere, 41/41 `header ok = True`** (so no README-Risk finding, no column-name diff to record).
>   Neither Malwarebytes nor Defender interfered (the process was never killed; Defender's real-time scan of
>   `source material\` did not measurably throttle the read).
> - **Totals:** 41 files, **104,641,868,093 bytes** (= 97.5 GiB, the README's "~98 GB"),
>   **902,815,672 rows** (mimic-iv-3.1 886,043,036 · ed 7,887,229 · note 8,885,407); hash 45.0 s + rowcount
>   47.3 s of engine time; ≈ 93 s of wall across the two passes — against the brief's 10–30 min estimate.
> - **`raw_snapshot_id = 8209301d8a06431081584e795684829b0bddeeedd49542ecf862cde712652d7a`**
>   (`sha256(json(sorted (rel_path, bytes, sha256, rows)))`), recorded with `duckdb 1.5.5`, `python 3.13.15`,
>   `git_sha 5e27a154435e…` (HEAD at run time — the EP-9 tick), `mimic_code_sha 8bcbd190ca75…`,
>   `contract_hash e4cd5aa908d1…`. This is the `source manifest id` EP-17/18/19 cite.
> - **Reconciliation** (`mwh inventory reconcile`, exit 0): **34 match · 0 mismatch · 7 no-expectation ·
>   0 pending** — every hosp/icu count equals the pinned `validate.sql` (which targets **3.1**, so the
>   3.0-vs-3.1 orphan-subject deltas the brief warned about do not arise) and every ED count equals the ED
>   `validate.sql`; the seven without an upstream expectation are `provider` 42,244, `caregiver` 17,984,
>   `ingredientevents` 14,253,480, `discharge` 331,793, `radiology` 2,321,355, `discharge_detail` 186,138,
>   `radiology_detail` 6,046,121. Every dataset's `SHA256SUMS.txt` was found and parsed, so all 41 lines carry
>   a `physionet_gz_sha256` for the parked RAW-1 re-verification.
>
> **Deltas from the brief / owner review points** (each with the alternative and a recommendation, since the
> owner reviews these cold):
> 1. **The docs page is committed with this EP, not with EP-16.** The brief deferred
>    `docs/resources/raw-inventory.md` to EP-16 only because the full job was expected to outlive the session;
>    it finished 88 s after launch, `reconcile` exits 0 and `mwh guard` passes on the page, so the page in this
>    commit is the complete one (41 files, hashes/counts/column status only — GOVERNANCE §3 needs no
>    disclosure sidecar for a manifest). *Alternative:* leave it untracked and let EP-16 commit it after its own
>    `show`; that costs nothing but a `git add` and buys an independent second look. *Recommendation:* keep;
>    EP-16 re-runs `mwh inventory reconcile` (regenerates the page byte-for-byte except the `Generated`
>    timestamp) and confirms `git diff` is timestamp-only.
> 2. **A fourth reconcile status, `pending`.** The brief's set was `{match, mismatch, no-expectation}`; a
>    partial manifest (the foreground pass, or a file whose rows were not counted) needs a state that is neither
>    a match nor a mismatch, so `pending` exists and is counted separately in the table, the JSON summary and
>    the docs page. *Alternative:* report those rows as `mismatch` (noisy: 12 false alarms after the
>    foreground pass) or drop them from the table (hides incompleteness). *Recommendation:* keep.
> 3. **`mwh inventory reconcile` exits 1 on any `mismatch`** (0 for pending/no-expectation) so EP-16 and later
>    CI-like checks get a signal without parsing the table; `--json` and the docs page are written either
>    way. *Alternative:* always exit 0 (one line in `reconcile_command`). *Recommendation:* keep — today it
>    exits 0, and a future re-download that changes a count should be loud.
> 4. **`--json` outputs keep raw integers** (`show --json`, `reconcile --json`, and the manifest JSONL /
>    `raw_snapshot.json` under the data root) — machine consumers (EP-16/19) want ints, not `"17,847,567"`.
>    Consequence: pasting such JSON into a tracked `.md`/`.py` would trip the guard's G4 rule (upstream counts
>    like `pharmacy`, `prescriptions`, `inputevents` and several byte sizes are 8-digit tokens starting with
>    1–3). Every *human-readable* surface (tables, log lines, the docs page) is thousands-separated and the
>    tests assert G4-cleanliness on those. *Recommendation:* keep; sessions quote the docs page or the
>    rendered tables, never `--json`, in committed text.
> 5. **`raw_snapshot_id` after a `--no-rowcount` pass.** The brief defines the id as `None` until all 41 files
>    are present; a `--no-rowcount` build that covers all 41 therefore *does* get an id, with `rows=null` in
>    the canonical tuples — a different id from the counted manifest. The manifest lines, `show` and the docs
>    page make the missing counts visible, and a following default `build` completes the rows **without
>    re-hashing** (the hash is reused when `(bytes, mtime)` still match) and re-derives the id.
>    *Alternative:* require `rows` for the id too (one condition in `compute_snapshot_id`), at the cost of a
>    manifest that can never get an id if DuckDB cannot count one file. *Recommendation:* keep the brief's
>    rule; `--no-rowcount` is an explicit operator choice and today's id was computed with all 41 counts.
> 6. **The MIMIC-IV-Note manifest file carries PhysioNet's long directory name.** It is
>    `mimic-iv-note-deidentified-free-text-clinical-notes-2.2.jsonl` (the brief's `<dataset-dir>`), while the
>    contract label stays `mimic-iv-note-2.2`; `DATASET_DIRS` maps one to the other and `--dataset` accepts
>    either. No action needed — just so the long file name is not a surprise in
>    `C:\mimicdata\lake\manifests\raw\` (`mimic-iv-3.1.jsonl`, `mimic-iv-ed-2.2.jsonl`, `raw_snapshot.json`
>    sit beside it).
> 7. **A retry around the atomic manifest rewrite** (`_atomic_write_text`: up to 20 short retries on
>    `PermissionError`) was added *after* the background job had started, because on Windows `os.replace`
>    fails if `mwh inventory show` happens to hold the JSONL open for the microseconds it takes to read it.
>    The job therefore ran on the pre-retry code — with no incident (its `show` reads were kept to two). Future
>    runs carry the retry; the job is resumable in any case.
> 8. **The wall-time estimate was very conservative:** ≈ 93 s in total against 10–30 min. Reads from
>    `source material\` sustained 2.0–2.4 GB/s in both the `hashlib` pass and the DuckDB pass with Defender
>    real-time on and Malwarebytes excluding the tree; the two-engine caveat in the amendment did not bite.
>    Worth carrying into the EP-17/18 loader estimates (their bottleneck will be Parquet *writing* into the
>    data root, not reading raw). Not a review point — an FYI.

> **Verification recipe for EP-16** (no data root reads needed): `uv run mwh inventory show --timing` must
> print `41/41 files in manifest (0 pending, 0 header mismatch)`, `job: … finished 2026-08-18T03:50:04+00:00`,
> `errors 0` and `raw_snapshot_id 8209301d8a06…`; `uv run mwh inventory reconcile` must exit 0 with
> `match=34, mismatch=0, no-expectation=7, pending=0` and rewrite `docs/resources/raw-inventory.md` with a
> timestamp-only diff; `uv run mwh inventory build` must report `0 to process, 41 up to date`.
