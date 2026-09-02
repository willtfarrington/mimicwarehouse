# mimicwarehouse (workspace)

The Python workspace of the mimicwarehouse data lab — a uv project that holds the
package (`src/mimicwarehouse/`), tests, docs and design files, and will hold the Streamlit
app (`app/`). Shipped through P2 (EP-0 … EP-33): the toolchain and governance layers, the
schema contract, raw inventory and synthetic fixtures (P0/P1), and the staging chapter —
the typed CSV → Parquet loader with subject buckets and resume, the `mwh build` DAG runner
with detached background jobs, per-tier DuckDB catalogs over the complete core lake (all
31 hosp + icu tables, full tier), the `meta.*` data dictionary, the `safe_query` gate with
its audit ledger, and the first end-to-end analysis (tracer bullet). The concepts runner,
cohorts, phenotypes, disclosure primitives and the Lab app arrive with P3/P4
(`../roadmap/README.md`). **§ State of the workspace** below is the single living "what
exists" page (D-43 item 13): re-plan EPs refresh it, and between re-plans the ☑ hashes in
the roadmap phase tables are the ground truth.

## State of the workspace

*(as of EP-33, the P2 consolidation re-plan, 2026-09-01 — refreshed by every re-plan EP;
**P2 is closed**: all 19 P2 briefs ☑, the core lake staged full-tier and verified (EP-28),
the P0–P2 surfaces consolidated under D-44 (one publish primitive, one connection factory,
one JSONL canon, one error/exit-code convention); next: EP-34, head of P3)*

| Module | EP | CLI | Tests |
|---|---|---|---|
| `config.py` — `Settings` (pydantic-settings), the 18-key data-root layout (per-tier lake roots `lake/fixture`·`lake/demo`·`lake/rejects`), D-29 location refusals, per-tier free-space guard, `unknown_env_keys`, `duckdb_settings(profile)` | EP-3, EP-167 | `mwh paths [--create] [--json]` | `tests/ep/test_ep03.py` · `test_ep167.py` |
| `cli.py` + `doctor.py` + `console.py` — typer/rich entry point (UTF-8 `console:run` wrapper, shared consoles); lazy settings validation (`--help` works over a broken/unsafe config, incl. pydantic-settings parse errors); 15 host checks incl. `antivirus` (nine-path D-38 list), `deny_coverage`; **the CLI conventions** (EP-33 B8): exit codes `EXIT_OK/FINDINGS/USAGE/REFUSED` = 0/1/2/3, errors as `mwh <cmd>: …` on **stderr** via `console.fail`, `--json` via `console.emit_json` (raw ints), progress logging via `console.configure_progress_logging` | EP-2, EP-164, EP-167, EP-33 | `mwh doctor [--json]` · `mwh --version` | `test_ep02.py` · `test_ep164.py` · `test_ep167.py` · `test_ep33.py` |
| `guard.py` — pre-commit data-leak guard G1–G5 (G1/G4 hardened at EP-165; G4 scans notebook source and shell/markup types since EP-33; selfcheck resolves the registered hook paths) | EP-4, EP-165, EP-33 | `mwh guard [PATHS…] [--staged \| --all-tracked \| --selfcheck]` | `test_ep04.py` · `test_ep165.py` |
| `theme.py` — palette, Altair/Streamlit themes, wordmark + banner SVGs ([docs/brand/](docs/brand/README.md)) | EP-5 | — | `test_ep05.py` |
| `verify.py` + `scripts/roadmap_check.py` — per-brief test runner, roadmap consistency check | EP-6 | `mwh verify EP-n \| --list \| --roadmap` · `poe roadmap-check` | `test_ep06.py` |
| `concepts/` — vendored MIT-LCP/mimic-code at the D-19 pin (`vendor_info()`, `VENDOR.json`); all 65 `concepts_duckdb` files execute cleanly on DuckDB 1.5.5 (EP-33 D2) | EP-8 | `poe vendor-mimic-code` | `test_ep08.py` |
| `schema/` — YAML contract for hosp/icu/ed/note (41 tables; keys, units, column maps, tie-broken sort keys, identifier/free-text flags) transcribed from the vendored DDL | EP-9, EP-169 | `mwh schema list \| show \| ddl \| check \| transcribe` | `test_ep09.py` · `test_ep169.py` |
| `inventory.py` — hash/row-count manifest of the raw CSVs, reconciled against upstream `validate.sql`; `raw_snapshot_id`; `fmt_int`/`fmt_bytes_mb` (the committed-text canon, [docs/committed-text.md](docs/committed-text.md)) | EP-10 | `mwh inventory build [--log] [--quiet] \| show [--timing] \| reconcile` | `test_ep10.py` |
| `fixtures/` — deterministic **synthetic** hosp + icu generator behind `tests/fixtures/` (31 CSVs, ids ≥ 90 000 000; generator 0.2.0, next regeneration EP-41) | EP-11/12, EP-169 | `mwh fixtures build` | `test_ep11.py` · `test_ep12.py` |
| `tests/conftest.py` + `tests/helpers.py` — pytest tier markers and ladder (`--tier`, `PYTEST_TIER`, per-need readiness; [tests/README.md](tests/README.md)), shared helpers incl. `assert_import_budget` | EP-12, EP-168, EP-33 | `poe test-dev` / `poe test-full` / `poe test-fast` | `test_ep12.py` · `test_ep168.py` |
| `scripts/claude_pretool_guard.py` — PreToolUse session guard, registered in `.claude/settings.json` | EP-165 | `mwh guard --selfcheck` (`pretool-hook` row) | `test_ep165.py` |
| `canary.py` — write canary: rehearses the loader's five write shapes (Parquet burst, large sequential, manifest churn, rename-aside swap, delete loop) with **synthetic bytes** under `<data_root>\tmp\canary`, sha256 re-read verification (a silent quarantine = hard error); write-side baseline; keeps its raw-OS sequence as the canon's sanctioned exception | EP-171 | `mwh canary write [--small N] [--large-mb N] [--keep] [--json]` | `test_ep171.py` |
| `fsio.py` — the JSONL ledger canon: `append_jsonl` / `append_jsonl_lines` (O_APPEND, short-write check, fsync), `read_jsonl` / `iter_jsonl` (tolerate one torn trailing line), `atomic_write_text` | EP-33 | — | `test_ep33.py` |
| `publish.py` — the one rename-aside publish primitive: `swap_dir` (tables), `swap_file` (catalogs, `runs.duckdb`), `new_path_for`/`old_path_for`, the `PermissionError` retry helpers (`retry_permission`, `rmtree`, `unlink`, `replace`), an observer seam; supersedes `paths.py` and `catalog.build.swap_catalog` | EP-17/21 → EP-33 | — | `test_ep33.py` · `test_ep17.py` · `test_ep21.py` |
| `engine.py` — the one DuckDB connection factory `open_duckdb(profile, …)` (`build` / `app` profiles, temp-dir parent, retry while a swapped file is absent) + `attach_read_only` (`ATTACH IF NOT EXISTS`); every `duckdb.connect` in `src/` goes through it (grep-guarded) | EP-33 | — | `test_ep33.py` |
| `loader/` — typed CSV → Parquet staging: `csv.py` (contract-driven `read_csv`, reject accounting), `stage.py` (small tables), `buckets.py` (100 subject buckets, two-pass sort, dev-first, resume with source/sort identity, coverage guard), `manifest.py` (build manifests + `status.json`), `engine.py` (build connection guards), `paths.py` (lake layout + `read_parquet_sql`) | EP-17, EP-18, EP-23 … EP-27, EP-33 | via `mwh build` | `test_ep17.py` · `test_ep18.py` · `test_ep23.py` … `test_ep28.py` |
| `dag/` — `spec.py` (YAML DAG, `specs/stage.yaml`), `runner.py` (`STEP_HANDLERS` stage/catalog/python, build lock with pid + create_time identity, snapshot ids), `jobs.py` (detached ⏱ supervisor, state files, logs), `benchmarks.py` (ledger lines, `summarize`, Markdown renderer), `snapshot.py` (layer snapshot ids, manifest stats) | EP-19, EP-28, EP-29, EP-32, EP-33 | `mwh build --tier t [--select a,b] [--tag t] [--force] [--break-lock] [--dry-run] [--background --job N]` · `mwh jobs [--job N] [--tail N]` | `test_ep19.py` · `test_ep20.py` · `test_ep28.py` · `test_ep32.py` |
| `catalog/` — `build.py` (per-tier `.duckdb`: dims materialized, subject-keyed tables as views, `meta.*` population, published via `publish.swap_file`), `connect.py` (`open_catalog`, READ_ONLY + hardening + version/dev-bucket checks), `profile.py` (`meta.profile` step), `dictionary.py` (`DATA-DICTIONARY.md`), `cli.py` (`mwh catalog`, `mwh sql`) | EP-21, EP-29, EP-30, EP-33 | `mwh catalog info \| dictionary [--tier t] [--out]` · `mwh sql "<stmt>" [--tier t] [--k n] [--format table\|csv\|json] [--describe]` | `test_ep21.py` · `test_ep29.py` · `test_ep30.py` |
| `demo.py` — ODbL MIMIC-IV Demo 2.2 (+ ED demo) fetch with sha256 verification into `ext/demo` + `source.yaml` register | EP-22 | `mwh demo fetch [--force] \| status` | `test_ep22.py` |
| `safe.py` — `safe_query` (read-only, allow-listed, aggregate-only, real count-family column required, k = 11 row-wise suppression, registry exemptions `meta`/`information_schema`/dims/`marts.cohorts`, casts around aggregates and set operations verified per branch, sanitized execution errors, three-way error taxonomy) + `runs/audit.jsonl` + `build_runs_db` (`runs.duckdb` audit view) | EP-30, EP-33 | `mwh sql` · `mwh runs refresh` | `test_ep30.py` · `test_ep33.py` |
| `tracer.py` + `sql/tracer_first_icu_mortality.sql` — the tracer bullet: first-ICU-stay adults → in-hospital mortality (attrition + descriptives through `safe_query`, logistic fit with zero-cell level exclusion, run folders under the committed-text canon) | EP-31 | `mwh tracer --tier t [--background --job N]` | `test_ep31.py` |
| `runs_cli.py` — `mwh runs` verbs over the ledgers | EP-30, EP-32 | `mwh runs refresh` · `mwh runs benchmarks [--tier full\|all] [--kind stage\|all] [--format table\|md] [--out PATH]` | `test_ep30.py` · `test_ep32.py` |

Gates as of EP-33 (2026-09-01): **832 fixture-tier tests** green (`poe check` = ruff check +
ruff format --check + pyright + pytest; 861 collected, the dev/full/demo probes deselect by
default; 248 s) · the 42-brief `mwh verify` loop 0 failures · `mwh doctor` **9 pass · 1 warn ·
0 fail · 5 info**, exit 0 (the warn is the `antivirus` row, by design — D-38/D-42) ·
`mwh guard --all-tracked` clean (500 files) · `poe roadmap-check --strict` 0 errors,
0 warnings (172 rows, 42 done) · the full core lake, catalogs on all tiers (31 tables/views,
0 missing) and the tracer re-runs reproduce EP-28/EP-31's numbers (`../roadmap/retro-p2.md`
§ Workstream F).

**Environment realities** (session-facing; `CLAUDE.md` §3 and D-42 are authoritative —
later re-plans refresh this list):

- Files via the Write/Edit tools only — never bash heredocs (Defender kills them as
  ClickFix) or `python -`/stdin scripts (hang); commit messages via `git commit -F <file>`;
  no burst copy/`sed -i`/delete loops over many scratch files (Malwarebytes ransomware
  heuristic); "process killed / binary vanished / access denied" → check the Malwarebytes
  Quarantine and `mbamservice.log` before anything else.
- `uv` resolves natively in both tool shells since the owner's VS Code restart (verified
  2026-08-28; the miss was a stale-process artefact). If a stale process recurs, the
  CLAUDE.md §3 fallback still applies: prefix `%LOCALAPPDATA%\Microsoft\WinGet\Links`
  before `uv`, `poe`, `pre-commit` **and** `git commit` (the hook shells out to `uv run`).
- Bare `python`/`pip` in the tool shells is the **system CPython 3.14** — never use it or
  `pip install` into it; always `uv run python …` (uv manages CPython 3.13).
- Console: `PYTHONUTF8=1` comes from `.claude/settings.json` (EP-165); new CLI strings
  still stay ASCII or go through the shared console helper (EP-167) — some hosts run
  cp1252 (DESIGN §2, roadmap Risk 13).
- Endpoint security is **two** real-time products (Windows Defender + Malwarebytes 5.1
  Premium), both on, with a nine-path Malwarebytes allow list (D-38 addenda; all nine
  confirmed in place by the owner on 2026-08-28).
- The owner toggles Windows power mode off between sessions — confirm **Best performance**
  (`mwh doctor` `power_scheme`) before any compute-heavy step (D-38, CLAUDE.md §3).
- No `MWH_*` environment variables, `.env` or `mwh.toml` exist on this machine — settings
  are all defaults; `%MWH_DATA_ROOT%\…` in briefs means `get_settings().layout[…]`
  (roadmap README § "Notation used in briefs").
- `poe` tasks run from `mimicwarehouse/`, or from the repository root through
  `poe_tasks.toml` (`uv run --project mimicwarehouse --group dev poe check`, EP-33);
  `poe check` includes the `ruff format --check` gate since EP-33.
- The engine / Windows / AV / session lore that used to live in completion notes and
  machine-local memory has one home: [docs/gotchas.md](docs/gotchas.md) (EP-33).

| Doc | What |
|---|---|
| [DESIGN.md](DESIGN.md) | architecture: layers, tiers, engine config, schema/time semantics, concepts & phenotypes, cohort spec, events spine, run ledger, safe-query, protocol freeze, disclosure, module map, app, reporting, notes, linkage, testing |
| [GOVERNANCE.md](GOVERNANCE.md) | the license/PHI/LLM/small-cell/export/audit contract — read before touching data |
| [DECISIONS.md](DECISIONS.md) | D-1 … D-45 owner decisions (status index at the top, EP-33) + assumed defaults + judgment calls |
| [docs/gotchas.md](docs/gotchas.md) · [docs/committed-text.md](docs/committed-text.md) | the one home for engine / Windows-AV / session lore, and the committed-text hygiene canon (EP-33) |
| [DATA-DICTIONARY.md](DATA-DICTIONARY.md) | generated from the full catalog's `meta.*` by `mwh catalog dictionary --tier full` (EP-29); regenerate after a catalog rebuild — never edit by hand; disclosure sidecar pending EP-43 |
| `docs/resources/` | all six P1 inventories shipped ([index](docs/resources/README.md)): [raw-inventory.md](docs/resources/raw-inventory.md) (EP-10) · [repos.md](docs/resources/repos.md) (EP-13) · [vocabularies.md](docs/resources/vocabularies.md) (EP-14) · [reading.md](docs/resources/reading.md), [datasets.md](docs/resources/datasets.md), [methods-notes.md](docs/resources/methods-notes.md) (EP-15) |
| [`docs/analyses/`](docs/analyses/README.md) | capstone case studies (convention + index: EP-32), hupsim style: claim-type labels, "what it deliberately does not claim" + Reproduction blocks; first entry [00-staging-benchmark.md](docs/analyses/00-staging-benchmark.md) (build telemetry; table rendered by `mwh runs benchmarks`) |

## Install (EP-1)

Native Windows, PowerShell 7, **uv-managed CPython 3.13** in one `.venv`; the system
Python (`C:\Python314`) is never touched (`python-preference = "only-managed"`, D-15).
uv, its cache (`uv cache dir`) and `.venv` all live on C: — never on a synced drive.

```powershell
# 1. uv (user scope, no admin) — one-time; then reopen the shell
winget install --id astral-sh.uv -e
#    fallback: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version                              # EP-1 recorded 0.12.5

# 2. managed interpreter (the workspace pins 3.13 in .python-version)
uv python install 3.13

# 3. the workspace
cd mimicwarehouse
uv sync --group dev                       # core + dev tools; builds .venv from uv.lock
uv run poe test                           # pytest (all briefs)  · uv run poe test -m ep_1 (one brief)
uv run poe check                          # ruff check + ruff format --check + pyright + pytest
uv sync --group ui                        # adds Streamlit & co (isolated from gpu/text; see below)
```

Dependency groups (`[dependency-groups]`): `dev` (default; pytest, hypothesis, ruff, pyright,
poethepoet, pre-commit) · `ui` (Streamlit 1.61, VegaFusion, vl-convert, Plotly — Streamlit
pins `pyarrow<25`, so `[tool.uv] conflicts` keeps `ui` apart from `gpu` and `text`) ·
`gpu` (EP-121) · `gpl` (EP-93; GPL-3 tools only here, D-34) · `text` (EP-148+). Commands in
briefs always name their groups: `uv run --group ui mwh app`. `poe` tasks: `test`,
`test-fast`, `test-dev`, `test-full`, `lint`, `fmt`, `fmt-check`, `typecheck`, `check`,
`guard`, `roadmap-check`, `vendor-mimic-code`; they also run from the repository root via
`poe_tasks.toml` (EP-33). Tests carry `@pytest.mark.ep_<n>` (one marker per brief) and a
`tier(...)` marker (selection from EP-12).

## Quick start (`mwh` as of EP-33: `doctor` · `paths` · `guard` · `verify` · `schema` · `inventory` · `fixtures` · `canary` · `build` · `jobs` · `catalog` · `sql` · `demo` · `runs` · `tracer`; `app` lands in P4)

```powershell
# from the repository root, after "Install" above
cd mimicwarehouse
uv sync --group dev                 # CPython 3.13 managed by uv; system Python untouched
uv run --group dev mwh --version    # mwh 0.1.0
copy .env.example .env              # optional: every MWH_* key with its default, commented; .env is gitignored
uv run --group dev mwh doctor       # 15 host checks (below); exit 0 unless one fails
uv run --group dev mwh paths        # the 18-directory data-root layout (incl. lake/fixture · lake/demo · lake/rejects, EP-167): path · exists · MB used, + which source set data_root
uv run --group dev mwh paths --create   # safety validators + free-space guard, then creates C:\mimicdata\… (idempotent)
uv run --group dev mwh doctor --json | ConvertFrom-Json   # {timestamp, host, checks[15], ok}
uv run --group dev mwh --data-root G:\mimicdata paths --create   # refused: exit 2, nothing created (D-29)
uv run --group dev mwh guard        # = --staged: what `git commit` would record, read from the index; exit 0 clean / 1 refused / 2 usage
uv run --group dev mwh guard --all-tracked   # every tracked path (also `mwh guard <paths…>` for working-tree files/dirs, `--json`)
uv run --group dev mwh guard --selfcheck     # EP-0 .gitignore/.gitattributes probes + hook wiring + PreToolUse-hook registration (EP-165)
uv run --group dev mwh schema list           # the EP-9 contract: 41 tables (also: show <s.t> | ddl <s.t>|--all | check | transcribe)
uv run --group dev mwh schema check          # re-parse the vendored DDL at the pin; any drift = findings + exit 1
uv run --group dev mwh inventory show        # raw-inventory manifest + job lines (counts/hashes only; also: reconcile; `build` touches the real CSVs — background job, EP-10 recipe)
uv run --group dev mwh fixtures build        # regenerate tests/fixtures/ (synthetic; byte-identical for the same spec/generator version)
uv run --group dev mwh canary write          # EP-171 write canary: rehearse the loader's write shapes under <data_root>\tmp\canary with synthetic bytes, verify every re-read (also: --small N, --large-mb N, --keep, --json)
uv run poe test-dev                          # pytest --tier dev (dev-marked tests skip while dev.duckdb is absent) · poe test-full likewise
uv run poe vendor-mimic-code                 # re-vendor mimic-code at the pinned sha (no-op at the same sha; EP-8)
uv run mwh build --tier fixture --dry-run    # EP-19 DAG runner: print the ordered plan (also: --select a,b · --tag t · --force · --break-lock)
uv run mwh build --tier dev --select stage.mimiciv_hosp.patients   # stage into the lake (the only lake writer; catalogs land at EP-21)
uv run mwh build --tier full --background --job <name>             # full tier is always a detached job; log under runs\jobs\<name>.log
uv run mwh jobs --job <name> --tail 20       # job state (running|done|failed, pid, exit) + last log lines (counts only)
uv run --group dev mwh demo fetch && uv run --group dev mwh build --tier demo   # EP-22: fetch + verify the ODbL MIMIC-IV Demo 2.2 (+ ED demo) from physionet.org into ext\demo, then stage it into lake\demo + warehouse\demo.duckdb (~1 min; `mwh demo status` prints the licensing register, `mwh sql --tier demo --count mimiciv_hosp.patients` = 100)
uv run mwh sql "SELECT anchor_year_group, count(*) AS n FROM mimiciv_hosp.patients GROUP BY 1" --tier dev   # EP-30 safe_query: aggregate-only, k = 11 suppression, audited; refusals exit 3, usage errors exit 2 (stderr)
uv run mwh catalog info --tier dev           # tables/views/missing per catalog; `mwh catalog dictionary --tier full --out DATA-DICTIONARY.md` regenerates the data dictionary (EP-29)
uv run mwh runs refresh                      # rebuild warehouse\runs.duckdb (audit view over runs\audit.jsonl; EP-30/EP-35)
uv run mwh runs benchmarks --format md       # the staging benchmark table from runs\benchmarks.jsonl (EP-32; `--out` splices it into docs/analyses/00-staging-benchmark.md)
uv run mwh tracer --tier dev                 # EP-31 tracer bullet: attrition + descriptives through safe_query, logistic fit; `--background --job NAME` on full
uv run --group ui mwh app           # (EP-57+) Streamlit "Lab" app on 127.0.0.1
uv run --group dev mwh verify EP-<n>         # one brief's acceptance tests (marker ep_<n>) in a fresh interpreter; `-- <pytest args>` pass through
uv run --group dev mwh verify --list         # EP · title · tier · test module present
uv run --group dev mwh verify --roadmap      # roadmap/README.md vs briefs: parity · header · hashes · charters (--strict, --json)
uv run poe roadmap-check                     # = mwh verify --roadmap (scripts/roadmap_check.py); what the re-plan EPs run
```

**Settings** (`mimicwarehouse.config.Settings`, pydantic-settings): `mwh --data-root` >
`MWH_*` environment > `mimicwarehouse/.env` > `mimicwarehouse/mwh.toml` `[settings]` > defaults
(`data_root=C:\mimicdata`, `default_tier=dev`, DuckDB `36GB`/`12GB` memory · 12 threads ·
temp `<data_root>\tmp\duckdb` · `150GB` max temp, `min_free_gb=100`, `k_suppression=11`,
`allow_remote=false`, `forbidden_drives=["G","D"]`, `dev_buckets=[0..4]`). Unknown keys in
`.env`/`mwh.toml` are rejected (`extra="forbid"`); unknown `MWH_*` *environment variables*
are ignored by pydantic-settings and reported by `mwh doctor` (EP-167). Relative paths are
anchored at this folder. Every command refuses to run when the
data root is not a local fixed NTFS/ReFS volume (sync-client label, FAT32/cryptoFs, OneDrive,
G:/D:); the six diagnostic commands — `doctor`, `paths`, `guard`, `verify`, `schema`,
`fixtures` — run anyway and *report* it, and since EP-167 the refusal fires on the first
settings access, so `--help`/`--version` work everywhere, even over a broken `.env` or an
unsafe root (`mwh inventory build --help` exits 0; `mwh inventory build` still exits 2).

`mwh doctor` exits 0 when no check **fails** (warn/info are allowed) and 1 otherwise:
`python` · `uv` · `duckdb` (pin) · `settings` (sources in use, `.env`/`mwh.toml`/`source_root`
present yes/no, `allow_remote`) · `disk_free` (fail < 100 GB, DESIGN §3) · `data_root`
(**fails** on an unsafe location, warns when missing → `mwh paths --create`) · `temp_dir`
(same volume as the data root) · `cloud_mounts` (letters + labels of synced/virtual volumes;
warns if the repo is on one) · `defender` (exclusion for the data root; info when not
elevated, D-38) · `antivirus` (EP-164: every product Windows Security Center lists — names +
real-time/up-to-date flags; **warns** when one besides Defender is present, because it keeps
its own allow list — the nine D-38 paths — that the doctor cannot read; info when Defender is
alone or the query fails) · `deny_coverage` (EP-167: warns when the data root is under no
`.claude/settings.json` deny-rule prefix — a relocated `MWH_DATA_ROOT` silently loses its
coverage, GOVERNANCE §2 / retro GOV-3; info when the file is missing) · `bitlocker` (fails
when off, GOVERNANCE §2) · `power_scheme` (info) · `gpu` (info) · `longpaths` (+ the git
version). The `settings` check also warns on unknown `MWH_*` environment variables (names
only). The doctor never opens a data file; the `--json` object is what EP-35 embeds in run
manifests.

## Contributing (EP-4: pre-commit + `mwh guard`)

Install the hooks once per clone (they run under the workspace venv; `C:\Python314` is never
touched) and let them refuse what must never reach git (GOVERNANCE §3):

```powershell
cd mimicwarehouse
uv run --group dev pre-commit install          # writes .git/hooks/pre-commit
uv run --group dev pre-commit run --all-files  # on demand; also runs ruff check/format --check
uv run --group dev poe guard                   # = mwh guard --staged, without pre-commit
```

Hook order (`.pre-commit-config.yaml` at the repo root, `repo: local` + `pre-commit-hooks`
v6.0.0): `mwh-guard` → `ruff-check` → `ruff-format` → `check-added-large-files` (20 000 KB) →
`check-merge-conflict` → `check-yaml` → `check-toml` → `check-json` → `end-of-file-fixer` →
`trailing-whitespace` → `detect-private-key`. `mwh guard` reads the **index** (what would be
committed), so an unstaged edit cannot hide a staged violation, and it never quotes file
content: an id token appears only masked (`1*******`). It refuses, per rule id:

| Rule | Refuses |
|---|---|
| **G1** data-shaped extension | `.csv .csv.gz .parquet .duckdb .duckdb.wal .duckdb.new .duckdb.tmp .wal .jsonl .feather .arrow .pkl .joblib .skops .pt .safetensors .npy .npz .h5` + (EP-165) `.tsv .xlsx .xls .zip .7z .tar .tgz .tar.gz .gz .bz2 .zst .xz .sqlite .sqlite3 .db .orc .avro .ndjson .hdf5` anywhere — except under `mimicwarehouse/tests/fixtures/`, where only `.csv .csv.gz .parquet .jsonl .json .yaml` pass (synthetic, ids ≥ 90 000 000). Longest-suffix, so `.gz`/`.zst` also cover `.parquet.gz`/`.csv.zst` |
| **G2** source material | anything under `source material/` other than `*.md` (refused by name; the guard never opens files there) |
| **G3** notebook outputs | `.ipynb` with a non-empty `outputs` or non-null `execution_count` (or invalid JSON); anything under a `__marimo__/` directory |
| **G4** real-id band | in text files (`.py .md .yaml .yml .json .toml .sql .txt .csv .tsv .jsonl .html .svg .cff .ps1 .ini .cfg` and extensionless; UTF-8, no NUL) an isolated 8-digit token in the `subject_id` (1xxxxxxx), `hadm_id` (2xxxxxxx) or `stay_id` (3xxxxxxx) band — including compact `YYYYMMDD` dates (write `2026-08-17`), the float rendering `NNNNNNNN.0` (pandas nullable BIGINT; EP-165) and `_`-bordered tokens; hex hashes, longer digit runs, non-`.0` decimals and `10_000_000`-style constants never match. Since EP-165 file **paths** are scanned too (digit boundaries, so `stay_3xxxxxxx.parquet` is caught; no pragma escape for names). A line carrying the pragma **`mwh-guard: allow`** is exempt (documented examples only) |
| **G5** oversize | any blob > 20 000 KiB (fixtures included) |

Fixture ids are ≥ 90 000 000 (D-27), so synthetic rows never trip G4. To document a band
boundary in prose, use spaces or underscores (`10 000 000`, `10_000_000`) rather than a plain
8-digit literal. If a real row-level file is ever committed: stop, do not push, follow
GOVERNANCE §3/§13.

## Tiers (see DESIGN §4)

`fixture` (synthetic, committed; ids ≥ 90 000 000) · `demo` (ODbL MIMIC-IV Demo 2.2,
downloaded on demand) · `dev` (5 % of subjects: `subject_id % 100 < 5`) · `full`.
Develop and test on fixture + dev; full-tier runs are background jobs recorded in the
benchmark ledger and verified by the next brief.

## Layout (✓ = shipped; the rest planned)

```
mimicwarehouse/
├── pyproject.toml            ✓ uv project; groups core/dev/ui/gpu/gpl/text (EP-1)
├── .env.example              ✓ every MWH_* setting with its default (copy to .env; .env is gitignored)
├── src/mimicwarehouse/       ✓ package: cli, console, config, doctor, guard, theme, verify, schema/,
│                               inventory, fixtures/, concepts/vendor/, fsio, publish, engine, loader/,
│                               dag/, catalog/, demo, safe, tracer (+ sql/), runs_cli, canary
│                               (see § State of the workspace above and DESIGN §15 for the module → EP map)
├── scripts/                  ✓ roadmap_check.py (EP-6) · claude_pretool_guard.py (EP-165)
├── app/                      Streamlit multipage app (P4)
├── notebooks/                marimo scratch notebooks (zero-output .py)
├── tests/                    ✓ pytest; tests/ep/test_epNN.py; tests/fixtures/ (synthetic only); tests/README.md; tests/helpers.py
├── docs/                     ✓ brand/ (EP-5) · resources/ (P1 inventories) · analyses/ (EP-32) · gotchas.md + committed-text.md (EP-33); site from P11
├── DESIGN.md · GOVERNANCE.md · DECISIONS.md   ✓ · DATA-DICTIONARY.md (generated, EP-29)
```
(`../poe_tasks.toml` at the repository root re-exports the poe tasks with `cwd = mimicwarehouse`, EP-33.)

Data never lives here: raw CSVs stay in `../source material/` (gitignored), everything
derived in `C:\mimicdata` (`MWH_DATA_ROOT`; the 18-directory tree —
`lake/{core,derived,marts,manifests}`, `lake/fixture`, `lake/demo`, `lake/rejects` (per-tier
lake roots, EP-167), `warehouse`, `runs/jobs`, `models`, `notes`,
`ext/demo`, `studies`, `tmp/duckdb` — is drawn in DESIGN §3 and created by
`mwh paths --create`).
