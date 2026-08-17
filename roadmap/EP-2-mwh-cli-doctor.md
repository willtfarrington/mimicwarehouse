# EP-2 — `mwh` CLI skeleton + `mwh doctor`

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)) · **Blocks:** EP-3 (Config & data root + safety checks), EP-4 (Governance enforcement: pre-commit + `mwh guard`), EP-6 (`mwh verify EP-n` + roadmap_check.py), EP-7 (Re-plan P0)

## Context

EP-1 declared the console script `mwh = "mimicwarehouse.cli:app"` but the module does not exist.
This brief creates the typer + rich CLI that every later EP extends (DESIGN §15: `doctor paths
build sql verify demo runs protocol disclose backup app init`) and its first command, `mwh doctor`,
the health report GOVERNANCE §2 wants re-checked and recorded in every run manifest (EP-35 calls the
same functions). The checks follow D-14/D-15/D-38 and DESIGN §2–3: managed Python 3.13, uv, free
disk on the data-root and repo drives (the ≥ 100 GB rule), data-root presence, BitLocker on C:,
GPU/driver (informational until EP-121, D-16), the DuckDB version pin, `LongPathsEnabled`. Facts at
planning: `nvidia-smi` lives in `C:\Windows\System32`; `Get-BitLockerVolume` needs elevation but the
Shell COM property `System.Volume.BitLockerProtection` does not; `C:\mimicdata` does not exist yet
(EP-3 creates it). Nothing here opens a data file. Commands run in `mimicwarehouse/`.

## In scope

1. **`src/mimicwarehouse/cli.py`** — `app = typer.Typer(name="mwh", no_args_is_help=True,
   rich_markup_mode="rich", add_completion=False)`; eager `--version` printing
   `mwh <mimicwarehouse.__version__>`; a shared `console = rich.console.Console()`; commands live in
   their own modules and are attached in `cli.py` with one `app.command()` / `app.add_typer()` line
   each, so EP-3 (`paths`), EP-4 (`guard`), EP-6 (`verify`) extend without restructuring. Global
   option `--data-root PATH` (overrides `MWH_DATA_ROOT` for one invocation; until EP-3 the value is
   read from the environment with default `C:\mimicdata`). Never import duckdb / pandas / polars at
   CLI import time — `mwh --help` stays under ~0.5 s.
2. **`src/mimicwarehouse/doctor.py`** (helper of `cli.py`, DESIGN §15) — pure check functions
   returning `CheckResult(id, status: Literal["pass","warn","fail","info"], detail: str, value)` and
   `run_checks(data_root: Path) -> list[CheckResult]`, eight checks:
   - `python`: `sys.version_info[:2] == (3, 13)` and executable under a `.venv` → pass, else fail;
   - `uv`: `uv --version` via subprocess → pass / warn if not found;
   - `duckdb`: `duckdb.__version__` equals the pin read from
     `importlib.metadata.requires("mimicwarehouse")` → pass / fail (import duckdb inside the check);
   - `disk_free`: `shutil.disk_usage` on the data-root drive and on the repo drive; fail < 100 GB,
     warn < 150 GB, else pass; free/total GB in `value`;
   - `data_root`: exists and writable → pass; missing → warn ("run `mwh paths --create` (EP-3)");
     on a drive other than C: → warn (EP-3 turns this into a refusal with cloud/virtual detection);
   - `bitlocker`: PowerShell one-liner
     `(New-Object -ComObject Shell.Application).NameSpace('<drive>:\').Self.ExtendedProperty('System.Volume.BitLockerProtection')`
     for the data-root and repo drives; 1 → pass; 2 (off) → fail; 3 / 5 (encrypting / suspended) →
     warn; anything else or unavailable → warn "unknown — run `manage-bde -status C:` elevated";
   - `gpu`: `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader` → info
     with the string, or info "no NVIDIA driver on PATH"; never fail (the deep `--gpu` check is EP-121);
   - `longpaths`: registry `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled == 1`
     (`winreg`) and `git config --get core.longpaths` == `true` → pass, else warn.
   Every subprocess call has a 10 s timeout and is wrapped so a missing tool yields warn/info,
   never a traceback; non-Windows hosts get `info` for the Windows-only probes.
3. **`mwh doctor [--json]`** — rich table (check · status glyph · detail), one summary line, exit
   code 0 when no check failed, 1 otherwise; `--json` prints
   `{"timestamp", "host", "checks": [...], "ok": bool}` (the object EP-35 embeds in run manifests).
   The report never prints environment variables wholesale or any path under `source material/`.
4. **Tests `tests/ep/test_ep02.py`** (`@pytest.mark.ep_2`): `typer.testing.CliRunner` runs
   `mwh --version`, `mwh --help`, `mwh doctor --json` (parse; all eight ids present, `ok` bool);
   monkeypatched failures — `shutil.disk_usage` returning 50 GB free → `disk_free` fail and exit
   code 1; DuckDB pin mismatch → fail; `subprocess.run` raising `FileNotFoundError` for uv and
   nvidia-smi → warn / info without exception; BitLocker probe returning 2 → fail; every
   subprocess-backed check is mocked so the suite passes on any host and never shells out.
5. **Docs**: `mimicwarehouse/README.md` quick start now works as written
   (`uv run --group dev mwh doctor`); DESIGN §15 gets a dated note adding `doctor.py` (helper of
   `cli.py`, EP-2).

## Out of scope

- `Settings` / `.env` / TOML / `mwh paths` / cloud-drive refusal / Defender advice → EP-3.
- `mwh guard` → EP-4; `mwh verify` → EP-6; `--gpu` deep check
  (`torch.cuda.get_device_capability() == (12, 0)`) → EP-121.
- Embedding the doctor JSON in run manifests → EP-35.

## Verification / acceptance

- `uv run --group dev mwh --version` prints `mwh 0.1.0`; `uv run --group dev mwh doctor` renders
  the eight-row table and exits 0 on this machine (`data_root` may be warn until EP-3);
  `uv run --group dev mwh doctor --json | ConvertFrom-Json` parses.
- Crafted violation reported: `uv run --group dev mwh --data-root G:\probe doctor` shows
  `data_root` as warn (non-C: drive) — EP-3 upgrades this to a refusal.
- `uv run poe test -m ep_2` green; `uv run poe lint` and `uv run poe typecheck` green;
  `mwh verify EP-2` (available from EP-6) green when EP-6 runs it.
- Completion note records the doctor summary on this machine (statuses only, no paths beyond the
  data root).

> **Completion note (2026-08-17).** Delivered `src/mimicwarehouse/cli.py` (typer + rich; eager
> `--version`, global `--data-root` → `CliState` on `ctx.obj`; one `app.command()` line per command
> module; no duckdb/pandas/polars at import — `mwh --help` measured 0.29 s direct, 0.34 s via
> `uv run`), `src/mimicwarehouse/doctor.py` (`CheckResult`, eight checks behind mockable probe
> helpers with 10 s timeouts, `run_checks`, `doctor_report`, `mwh doctor [--json]`, exit 1 only on
> a `fail`) and `tests/ep/test_ep02.py` (27 tests, every probe mocked, unmocked `subprocess.run`
> is a test failure). `uv run poe check` (ruff + pyright + 37 tests) green.
> Doctor summary on this machine, `uv run --group dev mwh doctor` → exit 0, **7 pass · 0 warn ·
> 0 fail · 1 info**: python pass (CPython 3.13.15 in .venv) · uv pass (0.12.5) · duckdb pass
> (1.5.5 == pin) · disk_free pass (C: 415 / 951 GB free) · data_root pass (`C:\mimicdata` exists,
> writable — the owner created it at the EP-0 follow-up, so the "warn until EP-3" case did not
> arise) · bitlocker pass (C: on) · gpu info (RTX PRO 2000 Blackwell, 8 GB, driver 595.71) ·
> longpaths pass (registry 1, `core.longpaths=true`). `mwh doctor --json | ConvertFrom-Json`
> parses (8 checks, `ok=True`). Crafted violation `mwh --data-root G:\probe doctor` → `data_root`
> **warn** ("on G:, not C: … EP-3 refuses; missing"), `bitlocker` warn for G: (Shell COM returns
> nothing for the Google Drive virtual volume → "unknown — run manage-bde"), exit 0 (warns never
> fail; EP-3 turns the non-C: case into a refusal). Judgment calls: disk sizes use GiB (`2**30`)
> labelled "GB" to match the DESIGN §2 / EP-0 numbers; `data_root` writability is probed with a
> throw-away `NamedTemporaryFile` inside the root (nothing is read); the BitLocker probe uses
> Windows PowerShell (`powershell`, always present) rather than `pwsh`; `mwh verify EP-2` waits
> for EP-6. Docs: README quick start now real for EP-2 commands; DESIGN §15 dated note adds
> `doctor.py`. Nothing parked.
