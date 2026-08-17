# EP-3 — Config & data root + safety checks

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-2 (`mwh` CLI skeleton + `mwh doctor`) · **Blocks:** EP-7 (Re-plan P0), EP-10 (Raw inventory manifest ⏱), EP-17 (Loader core A: typed CSV → Parquet)

## Context

Every later module needs one answer to "where is the data root, which tier, how much memory may
DuckDB use, is this machine safe to build on". D-29 puts derived data in a short data root outside
the repository (`C:\mimicdata`, `MWH_DATA_ROOT`) and forbids anything warehouse-related on G:
(Google Drive stream), D: (Cryptomator vault) or any synced / virtual / network folder; DESIGN §3
sets the ≥ 100 GB free rule under which `mwh doctor` and `mwh build` refuse to run; DESIGN §6
requires explicit DuckDB `memory_limit` / `threads` / `temp_directory` / `max_temp_directory_size`
in every process; D-38 has the owner exclude only the data root from Defender. The planning
defaults name pydantic-settings with `MWH_` env + `.env` + TOML. EP-2's doctor read `MWH_DATA_ROOT`
straight from the environment; this brief replaces that with `mimicwarehouse.config.Settings`,
creates the data-root layout (`mwh paths --create`), and adds the safety validators. Windows
facts: `shutil.disk_usage` (no statvfs); drive type / volume label / filesystem via `ctypes`
(`GetDriveTypeW`, `GetVolumeInformationW`); at planning C: had ~415 GB free, G: was Google Drive,
D: was mounted, and `C:\mimicdata` did not exist. Nothing here reads a data file; `source material/`
is only tested for existence, never listed. Commands run in `mimicwarehouse/`.

## In scope

1. **`src/mimicwarehouse/config.py` — `Settings(BaseSettings)`** with
   `SettingsConfigDict(env_prefix="MWH_", env_file=".env", toml_file="mwh.toml", extra="forbid")`
   and precedence init kwargs > env > `.env` > `mwh.toml` (`[settings]` table via
   `TomlConfigSettingsSource` in `settings_customise_sources`) > defaults; both files are looked up
   in the workspace root (`mimicwarehouse/`, the CWD of every `mwh` command). Fields (defaults):
   `data_root: Path = C:\mimicdata` · `source_root: Path` = `<repo>/source material` resolved from
   the package location · `default_tier: Literal["fixture","demo","dev","full"] = "dev"` ·
   `duckdb_memory_limit: str = "36GB"` · `duckdb_app_memory_limit: str = "12GB"` ·
   `duckdb_threads: int = 12` · `duckdb_temp_dir: Path | None = None` (→ `data_root/tmp/duckdb`) ·
   `duckdb_max_temp_size: str = "150GB"` · `min_free_gb: int = 100` · `k_suppression: int = 11` ·
   `allow_remote: bool = False` · `forbidden_drives: list[str] = ["G", "D"]` ·
   `dev_buckets: list[int] = [0, 1, 2, 3, 4]`. Helpers: `layout -> dict[str, Path]` with exactly
   these 15 keys — `lake`, `lake_core`, `lake_derived`, `lake_marts`, `lake_manifests`, `warehouse`,
   `runs`, `runs_jobs`, `models`, `notes`, `ext`, `ext_demo`, `studies`, `tmp`, `tmp_duckdb`;
   `catalog_path(tier) -> warehouse/<tier>.duckdb`; `duckdb_settings(profile: "build" | "app")
   -> dict[str, str]` (`memory_limit`, `threads`, `temp_directory`, `max_temp_directory_size`, plus
   `preserve_insertion_order = "false"` in the build profile); `get_settings()` (cached;
   `get_settings.cache_clear()` for tests). `mimicwarehouse/.env.example` lists every key with its
   default, commented (`.env` itself stays gitignored).
2. **Safety validators (`config.py`, run as model validators and callable on their own):**
   `drive_info(path) -> DriveInfo(letter, drive_type, label, filesystem)` (ctypes; non-Windows →
   `unknown`); `check_local_fixed(path)` raises `UnsafeLocationError` (message names the reason and
   D-29) when drive type ≠ `DRIVE_FIXED`, filesystem ∉ {NTFS, ReFS}, the volume label matches
   `google drive|onedrive|dropbox|box|cryptomator|icloud` (case-insensitive), the path lies under
   `%OneDrive%` / `%OneDriveConsumer%` / `%OneDriveCommercial%`, or the drive letter is in
   `forbidden_drives`. Applied to `data_root` (refuse) and, warn-only, to the repository path.
   `duckdb_temp_dir` must be on the same volume as `data_root` (refuse otherwise).
   `check_free_space(path, min_gb) -> FreeSpace(free_gb, total_gb, ok)`; `require_free_space()`
   raises `DiskGuardError` below `min_free_gb` — called by `mwh paths --create` now and by
   `mwh build` from EP-19. All Win32 calls sit behind small functions the tests monkeypatch.
3. **`mwh paths [--create] [--json]`** — table of layout key · absolute path · exists · MB used
   (directory totals only, never file names) plus which settings source supplied `data_root`;
   `--create` runs the validators and the free-space guard, then creates the 15 directories
   idempotently (`mkdir(parents=True, exist_ok=True)`) and writes `data_root/README.txt` ("managed
   by mimicwarehouse; never sync this folder; see GOVERNANCE.md"); on any validator failure exits 2
   with the reason and creates nothing.
4. **Doctor upgrades (`doctor.py`)**: read `get_settings()` instead of the raw env var; `data_root`
   now **fails** on an unsafe location; new checks `temp_dir` (same volume as the data root; exists
   or creatable), `cloud_mounts` (info: letters + labels of mounted volumes whose label/type looks
   synced or virtual — never their contents), `defender` (elevated `Get-MpPreference` lists the
   data root → pass; not elevated → info with the owner command
   `Add-MpPreference -ExclusionPath '<data_root>'`, D-38), `settings` (info: sources used, `.env`
   present yes/no, `source_root` directory present yes/no — existence only, never a listing —
   `allow_remote` false), `power_scheme` (info: `powercfg /getactivescheme`).
5. **Tests `tests/ep/test_ep03.py`** (`@pytest.mark.ep_3`; everything under `tmp_path`,
   `MWH_DATA_ROOT` monkeypatched, `get_settings.cache_clear()` in a fixture): precedence (kwargs >
   env > `.env` > `mwh.toml` > default); all 15 layout keys resolve under the root and
   `catalog_path("dev")` ends in `warehouse\dev.duckdb`; `duckdb_settings("build")` carries the
   configured values; **refusals** — `drive_info` monkeypatched to a `Google Drive` label, to
   `DRIVE_REMOTE`, to filesystem `FAT32`, and a `data_root` of `G:\mimicdata` each raise
   `UnsafeLocationError`; a temp dir on another volume raises; free space mocked to 50 GB →
   `require_free_space` raises and `mwh paths --create` exits 2 having created nothing; happy path
   `mwh paths --create` creates exactly the 15 directories and `README.txt`, and a second run
   changes nothing; `mwh doctor --json` includes the new check ids.
6. **Docs.** DESIGN §3 gains the exact data-root tree (the 15 keys); `mimicwarehouse/README.md`
   quick start (`copy .env.example .env`, `uv run --group dev mwh paths --create`); completion note
   records `mwh paths` output on this machine (paths + exists), the free-space figure, and whether
   the owner has run the Defender exclusion (D-38 addendum only if it changed since EP-0).

## Out of scope

- Opening DuckDB connections / applying `duckdb_settings` → EP-17 (loader), EP-21 (catalog); tier
  catalogs themselves → EP-21.
- Raw inventory of `source material/` → EP-10; backup target → EP-52; `mwh init` bootstrap → EP-158.
- Secrets / keyring → none needed in v1 (parked below).

## Verification / acceptance

- `uv run --group dev mwh paths` prints the 15-row layout; `uv run --group dev mwh paths --create`
  creates `C:\mimicdata\…` (owner-approved location) and re-running is a no-op;
  `Test-Path C:\mimicdata\tmp\duckdb` is true.
- Refusal: `uv run --group dev mwh --data-root G:\mimicdata paths --create` exits 2 with an
  `UnsafeLocationError` message citing D-29 and creates nothing (`Test-Path G:\mimicdata` false).
- `uv run --group dev mwh doctor` passes on this machine (≥ 100 GB free, data root on C:, temp dir
  under it); `uv run poe test -m ep_3` green; lint / typecheck green; `mwh verify EP-3` (from EP-6)
  green when EP-6 runs it.
- `mimicwarehouse/.env.example` committed; `git check-ignore mimicwarehouse/.env` confirms `.env`
  is ignored.

## Parked → final-roadmap.md

- keyring-backed secret storage for `MWH_*` tokens — trigger: the first remote credential enters
  the project (none in v1; `MWH_ALLOW_REMOTE=false`); hazard: Windows Credential Manager quirks
  under uv-managed Python. *(Mirrored into `final-roadmap.md` § Cross-cutting as v2 CFG-1.)*

> **Completion note (2026-08-17).** Delivered `src/mimicwarehouse/config.py` (`Settings` with
> `MWH_` env · `.env` · `mwh.toml [settings]` · defaults, init kwargs first, `extra="forbid"`;
> `layout` (15 keys) · `catalog_path` · `duckdb_settings("build"|"app")` · `source_of`/`sources()`
> provenance · `get_settings()` cached + `configure()` + `load_settings(checked=False)`; safety
> validators `drive_info` (ctypes) · `location_problem` · `check_local_fixed` · `check_same_volume`
> · `check_free_space`/`require_free_space`, run as an `after` model validator and callable alone;
> `mwh paths [--create] [--json]`; `.env.example`), the `cli.py` settings wiring (callback loads
> settings once, installs `--data-root` process-wide, hands validated settings to every command and
> the unchecked instance only to `doctor`/`paths`; unsafe root → exit 2 before any other command
> runs), the doctor upgrades (`run_checks(settings)`; 13 checks — `settings`, `temp_dir`,
> `cloud_mounts`, `defender`, `power_scheme` added; `data_root` **fails** on an unsafe location;
> `disk_free` uses `min_free_gb`) and `tests/ep/test_ep03.py` (32 tests; every Win32 probe and
> subprocess mocked; nothing under `C:\mimicdata`, G: or D: is touched). `tests/ep/test_ep02.py`
> was adjusted for the EP-3 contract (13 ids, G: crafted case now `fail`/exit 1, `resolve_data_root`
> → `config.load_settings`, `powercfg`/`Get-MpPreference` in the fake runner). `uv run poe check`
> (ruff + pyright + 69 tests) green; `poe test -m ep_3` 32 passed; `-m ep_2` 27 passed.
>
> **On this machine.** `uv run --group dev mwh paths` (before) → 15 rows, all `exists=no`,
> `data_root C:\mimicdata (from default)`, free space `C:\ 415.3 / 951.5 GB (guard >= 100 GB: ok)`.
> `mwh paths --create` → **created 16 new path(s)** (the 15 directories + `README.txt`, exit 0);
> second run → `created: []`, tree and mtimes unchanged; `Test-Path C:\mimicdata\tmp\duckdb` →
> True. Layout now: `C:\mimicdata\{lake, lake\core, lake\derived, lake\marts, lake\manifests,
> warehouse, runs, runs\jobs, models, notes, ext, ext\demo, studies, tmp, tmp\duckdb}` — all
> `exists=yes`, 0.0 MB used. Refusals: `mwh --data-root G:\mimicdata paths --create` → exit 2,
> "volume G:\ is labelled 'Google Drive' — a sync client / virtual drive — … (D-29, GOVERNANCE §2)",
> "nothing was created", `Test-Path G:\mimicdata` → False; `--data-root D:\mimicdata` → exit 2
> ("labelled 'Google Cryptomator'"). Volume probes: C: fixed NTFS "Windows" · D: remote cryptoFs ·
> G: fixed FAT32 (each of D:/G: also refused by filesystem/type and by letter). `mwh doctor` → exit
> 0, **8 pass · 0 warn · 0 fail · 5 info**: python · uv (0.12.5) · duckdb (1.5.5) · disk_free (C:
> 415.2 / 951.5 GB) · data_root (`C:\mimicdata` exists, writable, fixed NTFS) · temp_dir · bitlocker
> (C: on) · longpaths pass; settings info ("sources: defaults only · .env absent · mwh.toml absent ·
> source_root present · tier=dev · k=11 · allow_remote=false"), cloud_mounts info ("D: Google
> Cryptomator (cryptoFs, remote) · G: Google Drive (FAT32, fixed) …; repository on C: (fixed)"),
> defender info (not elevated — `Get-MpPreference` prints "N/A: Must be an administrator to view
> exclusions"; the owner ran `Add-MpPreference -ExclusionPath 'C:\mimicdata'` at the EP-0
> follow-up — **unchanged since EP-0, so no D-38 addendum**), power_scheme info ("Balanced · AC
> power mode: Best performance"), gpu info. `git check-ignore mimicwarehouse/.env` → ignored
> (`.gitignore:74`); `.env.example` not ignored. `mwh --help`: 0.25 s of imports (config 0.12 s,
> half of it pydantic-settings → asyncio), 0.45 s wall direct / 0.52 s via `uv run` (EP-2:
> 0.29 / 0.34) — inside the ~0.5 s budget, recorded in DESIGN §15.
>
> **Judgment calls.** (1) `.env` / `mwh.toml` and relative paths are anchored at the workspace
> root resolved from the package location, not the shell CWD, so `uv run --project mimicwarehouse`
> from the repo root reads the same files (the brief's "CWD of every mwh command" holds and is now
> robust). (2) `env_ignore_empty=True` so `MWH_DUCKDB_TEMP_DIR=` means default rather than
> `Path("")`. (3) `UnsafeLocationError`/`DiskGuardError` subclass `RuntimeError` (not
> `ValueError`) so pydantic propagates them unwrapped from `Settings(...)`; an unchecked
> construction path (`safety_checks_disabled()` ContextVar) exists **only** for `doctor`/`paths`.
> (4) `box` in the label regex is word-bounded. (5) `duckdb_settings` returns strings (ready for
> `duckdb.connect(config=…)`); `preserve_insertion_order=false` only in the `build` profile.
> (6) `≥` replaced by `>=` in CLI output after `mwh paths` crashed on a cp1252 console (the
> table glyphs already fell back to ASCII). (7) `mwh paths` without `--create` still prints the
> table for an unsafe root (diagnosis) but exits 2. Docs: DESIGN §3 tree + §15 note, DECISIONS
> D-29 addendum, README quick start, `.env.example`; parked keyring item mirrored to
> `final-roadmap.md`. `mwh verify EP-3` waits for EP-6.
