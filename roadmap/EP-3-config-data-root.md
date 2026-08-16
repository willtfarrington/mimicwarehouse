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
  under uv-managed Python.
