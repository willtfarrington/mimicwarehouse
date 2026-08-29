# mimicwarehouse (workspace)

The Python workspace of the mimicwarehouse data lab — a uv project that holds the
package (`src/mimicwarehouse/`), tests, docs and design files, and will hold the Streamlit
app (`app/`). What is shipped so far is the toolchain and the data-free foundation — CLI,
host checks, governance guards, schema contract, raw inventory, synthetic fixtures (the
table below); the loader, warehouse build, concepts runner, cohorts, `safe_query` and the
Lab app arrive with the roadmap briefs from P2 on (`../roadmap/README.md`).
**§ State of the workspace** below is the single living "what exists" page (D-43 item 13):
re-plan EPs refresh it, and between re-plans the ☑ hashes in the roadmap phase tables are
the ground truth.

## State of the workspace

*(as of EP-166, 2026-08-28 — refreshed by every re-plan EP; next: EP-16)*

| Module | EP | CLI | Tests |
|---|---|---|---|
| `config.py` — `Settings` (pydantic-settings), the 18-key data-root layout (per-tier lake roots `lake/fixture`·`lake/demo`·`lake/rejects`), D-29 location refusals, per-tier free-space guard, `unknown_env_keys` | EP-3, EP-167 | `mwh paths [--create] [--json]` | `tests/ep/test_ep03.py` · `test_ep167.py` |
| `cli.py` + `doctor.py` + `console.py` — typer/rich entry point (UTF-8 `console:run` wrapper, shared consoles); lazy settings validation (`--help` works over a broken/unsafe config); 15 host checks incl. `antivirus`, `deny_coverage` | EP-2, EP-164, EP-167 | `mwh doctor [--json]` · `mwh --version` | `test_ep02.py` · `test_ep164.py` · `test_ep167.py` |
| `guard.py` — pre-commit data-leak guard G1–G5 (G1/G4 hardened at EP-165) | EP-4, EP-165 | `mwh guard [PATHS…] [--staged \| --all-tracked \| --selfcheck]` | `test_ep04.py` · `test_ep165.py` |
| `theme.py` — palette, Altair/Streamlit themes, wordmark + banner SVGs ([docs/brand/](docs/brand/README.md)) | EP-5 | — | `test_ep05.py` |
| `verify.py` + `scripts/roadmap_check.py` — per-brief test runner, roadmap consistency check | EP-6 | `mwh verify EP-n \| --list \| --roadmap` · `poe roadmap-check` | `test_ep06.py` |
| `concepts/` — vendored MIT-LCP/mimic-code at the D-19 pin (`vendor_info()`, `VENDOR.json`) | EP-8 | `poe vendor-mimic-code` | `test_ep08.py` |
| `schema/` — YAML contract for hosp/icu/ed/note (41 tables; keys, units, column maps) transcribed from the vendored DDL | EP-9 | `mwh schema list \| show \| ddl \| check \| transcribe` | `test_ep09.py` |
| `inventory.py` — hash/row-count manifest of the raw CSVs, reconciled against upstream `validate.sql`; `raw_snapshot_id` | EP-10 | `mwh inventory build \| show [--timing] \| reconcile` | `test_ep10.py` |
| `fixtures/` — deterministic **synthetic** hosp + icu generator behind `tests/fixtures/` (31 CSVs, ids ≥ 90 000 000) | EP-11/12 | `mwh fixtures build` | `test_ep11.py` · `test_ep12.py` |
| `tests/conftest.py` — pytest tier markers and ladder (`--tier`, `PYTEST_TIER`; [tests/README.md](tests/README.md)) | EP-12 | `poe test-dev` / `poe test-full` | `test_ep12.py` |
| `scripts/claude_pretool_guard.py` — PreToolUse session guard, registered in `.claude/settings.json` | EP-165 | `mwh guard --selfcheck` (`pretool-hook` row) | `test_ep165.py` |

Gates as of EP-167 (2026-08-28): **487 fixture-tier tests** green (`poe check` = ruff +
pyright + pytest) · `mwh doctor` **9 pass · 1 warn · 0 fail · 5 info**, exit 0 (the warn is
the `antivirus` row, by design — D-38/D-42) · `mwh guard --all-tracked` clean ·
`poe roadmap-check --strict` 0 errors, 0 warnings.

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

| Doc | What |
|---|---|
| [DESIGN.md](DESIGN.md) | architecture: layers, tiers, engine config, schema/time semantics, concepts & phenotypes, cohort spec, events spine, run ledger, safe-query, protocol freeze, disclosure, module map, app, reporting, notes, linkage, testing |
| [GOVERNANCE.md](GOVERNANCE.md) | the license/PHI/LLM/small-cell/export/audit contract — read before touching data |
| [DECISIONS.md](DECISIONS.md) | D-1 … D-43 owner decisions + assumed defaults + judgment calls |
| `DATA-DICTIONARY.md` | generated by EP-29 from the catalog (`meta.*`); not present yet |
| `docs/resources/` | [raw-inventory.md](docs/resources/raw-inventory.md) (EP-10); the curated P1 inventories (repos, vocabularies, reading list, datasets) arrive with EP-13 … EP-15 |
| `docs/analyses/` | capstone case studies (from EP-32), hupsim style: "what it deliberately does not claim" + Reproduction blocks |

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
uv run poe check                          # ruff check + pyright + pytest
uv sync --group ui                        # adds Streamlit & co (isolated from gpu/text; see below)
```

Dependency groups (`[dependency-groups]`): `dev` (default; pytest, hypothesis, ruff, pyright,
poethepoet, pre-commit) · `ui` (Streamlit 1.61, VegaFusion, vl-convert, Plotly — Streamlit
pins `pyarrow<25`, so `[tool.uv] conflicts` keeps `ui` apart from `gpu` and `text`) ·
`gpu` (EP-121) · `gpl` (EP-93; GPL-3 tools only here, D-34) · `text` (EP-148+). Commands in
briefs always name their groups: `uv run --group ui mwh app`. `poe` tasks: `test`, `lint`,
`fmt`, `typecheck`, `check`. Tests carry `@pytest.mark.ep_<n>` (one marker per brief) and a
`tier(...)` marker (selection from EP-12).

## Quick start (`mwh` as of EP-167: `doctor` · `paths` · `guard` · `verify` · `schema` · `inventory` · `fixtures`; `build`/`sql`/`app` land in P2+)

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
uv run poe test-dev                          # pytest --tier dev (dev-marked tests skip while dev.duckdb is absent) · poe test-full likewise
uv run poe vendor-mimic-code                 # re-vendor mimic-code at the pinned sha (no-op at the same sha; EP-8)
uv run mwh build --tier dev         # (EP-19+) typed Parquet lake + dev catalog
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
its own allow list — the seven D-38 paths — that the doctor cannot read; info when Defender is
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
│                               inventory, fixtures/, concepts/vendor/ (see DESIGN §15 for the full module → EP map)
├── scripts/                  ✓ roadmap_check.py (EP-6) · claude_pretool_guard.py (EP-165)
├── app/                      Streamlit multipage app (P4)
├── notebooks/                marimo scratch notebooks (zero-output .py)
├── tests/                    ✓ pytest; tests/ep/test_epNN.py; tests/fixtures/ (synthetic only); tests/README.md
├── docs/                     ✓ brand/ (EP-5) · resources/ (raw-inventory.md, EP-10); analyses/ + site from P2+
├── DESIGN.md · GOVERNANCE.md · DECISIONS.md   ✓ · DATA-DICTIONARY.md (generated, EP-29)
```

Data never lives here: raw CSVs stay in `../source material/` (gitignored), everything
derived in `C:\mimicdata` (`MWH_DATA_ROOT`; the 18-directory tree —
`lake/{core,derived,marts,manifests}`, `lake/fixture`, `lake/demo`, `lake/rejects` (per-tier
lake roots, EP-167), `warehouse`, `runs/jobs`, `models`, `notes`,
`ext/demo`, `studies`, `tmp/duckdb` — is drawn in DESIGN §3 and created by
`mwh paths --create`).
