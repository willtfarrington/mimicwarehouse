# Gotchas — engine, Windows/AV and session lore (the one home)

Consolidated at EP-33 (2026-09-01, item C4; D-44 item 4) from completion notes, DESIGN
dated notes, DECISIONS addenda and machine-local session memory. **This page is the
canonical home**: CLAUDE.md §3 carries the *rules* a session must obey (and stays
authoritative for them), DESIGN §6 carries the engine facts that shape the architecture,
`docs/committed-text.md` carries the committed-text hygiene rules; everything else points
here instead of repeating the lore. Machine-local auto-memory holds pointers to this
page, not copies. Each entry names where it was learned so the evidence stays findable.

## 1. DuckDB 1.5.5 semantics (pinned; DESIGN §6)

- **Large integer literals spelled with `**` bind as DOUBLE.** `range(10**9)` reaches the
  function as a floating-point value; spell such literals out in full (`1000000000`).
  *(EP-30; the timeout probe in `test_ep30`.)*
- **The in-process instance cache is keyed on the database path.** Reconnecting while any
  connection in the same process still holds the path returns the *old* instance — a
  stale catalog after a rename-aside swap — and an `ATTACH` on one connection is visible
  instance-wide and outlives it. Every attach therefore goes through
  `engine.attach_read_only` (`ATTACH IF NOT EXISTS`), readers close before a rebuild, and
  the app caches results, not connections. *(EP-30/EP-31, surfaced only in multi-module
  pytest runs; DESIGN §6 note b.)*
- **`ATTACH IF NOT EXISTS` serves a stale file after a rename-aside swap.** An attached
  READ_ONLY database lives on the instance; after `publish.swap_file` republishes the file
  the attach keeps reading the delete-pending old copy (new views "do not exist"). `DETACH
  DATABASE IF EXISTS <alias>` then `ATTACH` re-opens the current file, instance-wide — that
  is `engine.detach` followed by `engine.attach_read_only`, which `safe_query` does for
  `runs.duckdb` so `mwh runs refresh` is visible to the next call in a long-lived process
  (pytest sessions, the app). *(EP-35; the scratch probe and `test_ep35`'s rebuild test.)*
- **`COPY … (APPEND)` demands `{uuid}` file-name patterns**, which would break the
  deterministic `part-0.parquet` layout; pass-1 sweeps write `OVERWRITE_OR_IGNORE` over
  disjoint bucket ranges instead. *(EP-18; final-roadmap LOAD-3.)*
- **Pass-2 reads need `hive_partitioning=false`**, or DuckDB synthesises the
  `subject_bucket` partition column *into* the sorted file. *(EP-18.)*
- **`sum()` over an integer column returns HUGEINT.** Until EP-33 a `CAST` around it
  tripped the safe-query closed-set walk; B1a now verifies a cast directly around one
  closed-set aggregate, but `count(*) FILTER (WHERE flag = 1)` remains the sanctioned
  event-count pattern and arithmetic over aggregates stays refused (final-roadmap DIS-3).
  *(EP-31; retro-p2 ledger SGT-3.)*
- **`.print` / `.read` driver files are CLI dialect.** The vendored `concepts_duckdb`
  driver is executed file-by-file from Python (parse the `.read` lines); the `duckdb`
  executable is never installed or run (GOVERNANCE §4). *(EP-33 D2: 65/65 files clean.)*
- **An in-memory connection has no temp directory** unless the profile sets one, and
  DuckDB creates a missing *leaf* temp dir but raises on first spill when its *parent* is
  missing. `engine.open_duckdb` creates `layout["tmp_duckdb"]` on every connect.
  *(EP-167, retro CFG-3.)*
- **READ_ONLY handles share delete; plain handles do not.** `os.rename(<tier>.duckdb →
  .old)` succeeds under DuckDB readers (the file is held delete-pending until they close)
  but fails under a plain file handle — hence `publish.swap_file` fails fast on the aside
  rename with a "close the app/notebooks" hint, while `os.replace` onto an existing
  *directory* always fails on Windows — hence `publish.swap_dir`'s rename-aside two-step.
  *(EP-166 review probe; EP-17/EP-21; DESIGN §5/§6.)*
- **`json_serialize_sql` takes the statement as a bound parameter** and never executes it;
  DESCRIBE/SHOW serialize as `SELECT_NODE` + `SHOW_REF`, so no regex pre-pass is needed.
  Node shapes (CAST keys, `SET_OPERATION_NODE` left/right) are pinned by tests, never
  assumed. *(EP-30, EP-33 B1.)*
- **`os.open` without `O_BINARY` writes CRLF on Windows.** The pre-EP-33 ledger writers did;
  JSON readers tolerate the stray `\r`, and `fsio.append_jsonl` now writes exactly `\n`.
  *(EP-33 B8.)*
- **Aggregate scans are cheap; sort-shaped work is not.** `meta.profile` over 886 M rows
  took 14.1 s; the per-bucket sorts of pass 2 dominate staging (pass 2 ≥ pass 1 on most
  large tables). Size estimates by *shape*, not by CSV GB — VARCHAR-heavy tables
  (emar_detail) are CSV-parse-bound at ~14 MB/s. *(EP-24/EP-28/EP-29.)*

## 2. Windows process and file-system reality

- **A child cannot record its own hard crash.** The ⏱ standard is a *detached supervisor*
  (`dag.jobs`, `DETACHED_PROCESS`) that owns the log and the authoritative state file; job
  logs are read with `mwh jobs --job NAME --tail N`, never opened by hand. *(EP-19, D-20
  addendum.)*
- **pids recycle aggressively.** Lock and job liveness are pid **plus** process
  `create_time`; a pid match with a different create time is stale and `--break-lock` can
  clear it. The build lock is created with `O_CREAT|O_EXCL`. *(EP-33, ledger DAG-2/DAG-3.)*
- **`PermissionError` on rename/remove is usually transient** (AV/indexer holds on freshly
  written files): every rename/remove of project-written files goes through
  `publish.retry_permission` / `rmtree` / `unlink` / `replace` (~10 s linear back-off);
  a `.old` that still cannot be removed after publish is *deferred* to the next swap's
  sweep, never a failed stage. `FileNotFoundError` is tolerated only on remove/restore
  operations — a `.new` that vanished at the publish rename (quarantine) rolls `.old` back
  and raises. *(EP-33, ledger CLI-1/LDR-2, WIN-2/3/4.)*
- **The in-process rss/ctypes probe reads 0 on this host** for minutes; trust the runner's
  psutil sampler. *(EP-23.)*
- **`os.linesep` through a text-mode stdout yields CR-CR-LF.** JSON emitters write plain
  `\n` (`console.emit_json`). *(EP-167/EP-33.)*
- **Endpoint security is two products** (Defender + Malwarebytes 5.1 Premium, D-42): the
  Ransomware-Protection heuristic judges *I/O patterns* — no burst copy/`sed -i`/delete
  loops over many scratch files, no bash heredocs (Defender kills the shell as ClickFix),
  no `python -`/stdin scripts (hang). "Process killed / binary vanished / access denied" →
  check the Malwarebytes Quarantine and `mbamservice.log` **first**. Staging itself never
  tripped either product (five ⏱ jobs, EP-171 canary). *(D-42; EP-7/EP-164/EP-171.)*

## 3. Session tooling (rules in CLAUDE.md §3; the why lives here)

- **`uv` may be missing from a tool shell's PATH** (stale VS Code process). Fallback before
  `uv`, `poe`, `pre-commit` and `git commit` (the hook shells out to `uv run`):
  `export PATH="$LOCALAPPDATA/Microsoft/WinGet/Links:$PATH"` (Bash) /
  `$env:PATH="$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:PATH"` (PowerShell).
- **Bare `python`/`pip` is the system CPython 3.14** — always `uv run python …`.
- **`poe` tasks run from `mimicwarehouse/`**; since EP-33 (B5) the repo-root
  `poe_tasks.toml` includes them with `cwd = "mimicwarehouse"`, so
  `uv run --project mimicwarehouse --group dev poe check` works from the root too.
- **`poe check` = ruff check + ruff format --check + pyright + pytest** (format gate since
  EP-33); `test` is serial by design, `test-fast` is the xdist opt-in (D-42).
- **Console encoding**: `PYTHONUTF8=1` reaches the tool shells from `.claude/settings.json`;
  other hosts may be cp1252, so new CLI strings stay ASCII or go through
  `console.console_safe`. *(DESIGN §2 notes; roadmap Risk 13.)*
- **The PreToolUse hook matches command *strings***: a shell command that merely mentions
  `mimicdata` / `source material` / `.csv` / `.parquet` / `.duckdb` is refused outside the
  allow-listed launchers — use the Read/Grep tools for docs that mention those tokens, and
  put paths in script files rather than command lines.
- **Foreground commands cap at ~10 min**; `sleep` is blocked in the Bash tool; anything
  longer is a background job with a log.
- **Power mode**: the owner toggles *Best performance* off between sessions; check
  `mwh doctor` `power_scheme` before heavy work and ask rather than change it.
- **Commit messages via `git commit -F <scratch file>`**; never `--no-verify`; no
  AI-attribution trailers.

## 4. Editing the design records (DESIGN.md / DECISIONS.md / briefs)

- **Never consume a heading as an Edit anchor without re-emitting it.** Two consolidation
  passes (EP-166's DESIGN trim; EP-33's first A4 pass) each destroyed headings that way;
  the repair is a structure diff (`grep -n "^## \|^\*\*D-"` before and after) before any
  commit that touches these files. *(retro-p2 ledger DRF-1.)*
- **History is never rewritten**: dated notes/addenda are appended; superseded prose is
  dropped only by an explicit as-built consolidation (EP-33 C1) with git as the archive.
- **Integers in tracked Markdown are thousands-separated** and no committed *file name*
  carries a compact date or run id (guard G4's path scan) — see `docs/committed-text.md`.

## 5. Test-suite conventions (tests/README.md is authoritative)

- Churn rule: a new EP must not need to edit an earlier `test_ep*.py`; read counts from
  `tests/fixtures/manifest.json` / the contract / `build_plan()`.
- Markers are unpadded (`ep_8`), files zero-padded (`test_ep08.py`); `mwh verify EP-n`
  runs the marker in a fresh interpreter and requires the file to exist for code briefs.
- `tests/fixtures/` is byte-identical across sessions (`GENERATOR_VERSION` 0.2.0);
  regeneration is a deliberate, versioned act (EP-41 → 0.3.0).
- `import helpers` (tests/ is on `sys.path` via `conftest.py`); import-budget probes use
  `helpers.assert_import_budget` — heavy libraries stay out of the `mwh --help` path
  (DESIGN §15 doctrine).

## 6. One way to do each thing (the EP-33 B8 canon)

Every shipped module was audited against this list at EP-33; new code follows it, and a
deviation is a review finding, not a style choice.

| Thing | The one way | Sanctioned exceptions |
|---|---|---|
| Append a ledger line (audit, benchmarks, build manifests, future run ledgers) | `fsio.append_jsonl` / `append_jsonl_lines`; read with `fsio.read_jsonl` / `iter_jsonl` | the EP-171 canary's manifest-churn pass (it measures the raw write pattern) |
| Write a small state file atomically | `fsio.atomic_write_text` (`inventory._atomic_write_text` is an alias) | — |
| Publish a directory or file the project built | `publish.swap_dir` / `publish.swap_file`; every other rename/remove of project-written files through `publish.retry_permission` / `rmtree` / `unlink` / `replace` | the canary's swap rehearsal |
| Open DuckDB | `engine.open_duckdb(profile, …)` with `build` or `app` spelled out; attach with `engine.attach_read_only`, preceded by `engine.detach` when the file may have been republished under a live instance (`safe_query` on `runs.duckdb`, EP-35) | tests that deliberately probe a raw connection (allow-listed in the grep guard) |
| Record an analysis run | `run.start(...)` → `runs/<run_id>/manifest.json` + one `runs/ledger.jsonl` line; SQL via `r.record_sql` / `r.safe_query`, outputs via `r.save_table` / `r.save_figure`; benchmark lines via `run.bench` (still `dag.benchmarks.append` underneath); the Reproduction block via `run.reproduction_block` (EP-35, `docs/methods/provenance.md`) | the EP-31 tracer's own `runs/tracer/` report folder (kept; it cites its run id) |
| Report an error from a command | `console.fail("mwh <cmd>", message, code=console.EXIT_*)` — bold red on **stderr**, exit 0/1/2/3 = ok / findings / usage-or-environment / safe-query refusal | `roadmap_check_main` returns its code instead of raising |
| Emit machine output | `console.emit_json(payload)` — raw ints, `default=str`, plain `\n`; `inventory.fmt_int` is for humans only | — |
| Progress lines | stdlib `logging` on the `mimicwarehouse` logger through `console.configure_progress_logging` | the canary's observer; `dag/jobs`' child prints (its stdout *is* the job log) |
| A package `__init__` | docstring-only (`dag`, `catalog`), or `__all__` + `TYPE_CHECKING` block + lazy module `__getattr__`/`__dir__` (`schema`, `fixtures`, `loader`); heavy libraries never at import time | `concepts/__init__` (self-contained leaf, never on the `mwh --help` path) |
| Deciding whether a command is "diagnostic" | it never touches the data root — the six in `cli.DIAGNOSTIC_COMMANDS`, pinned by test_ep167 | — |
| Committed text | `docs/committed-text.md` | — |

## 7. Where the measurements live

`roadmap/retro-p2.md` (P2 wall-time table, lake/temp measurements, dev-first verdict,
what dragged), `docs/analyses/00-staging-benchmark.md` (the committed benchmark note),
`runs/benchmarks.jsonl` (the ledger; `mwh runs benchmarks`), the EP-23…EP-28 completion
notes (per-job timelines).
