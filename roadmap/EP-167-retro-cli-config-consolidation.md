# EP-167 — Retro C: CLI, settings & inventory consolidation

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-10 (Raw inventory manifest ⏱) · **Blocks:** EP-17 (Loader core A: typed CSV → Parquet), EP-19 (DAG runner `mwh build`), EP-21 (Catalog builder (per-tier .duckdb))

> **Origin.** Third retro brief (origin note in EP-165; ledger =
> [`retro-2026-08-18-findings.md`](retro-2026-08-18-findings.md); decisions = D-43). Code brief on the
> fixture tier; nothing reads data. Ledger ids: CFG-1 … CFG-6, ARCH-3, FXT-10, ARCH-8, FC-15, ARCH-9,
> GOV-3, INV-1, INV-2, INV-4 (+ low CFG-7 …, INV-5 …, ARCH-12 … tagged EP-167).

## Context

`config.py`/`cli.py`/`doctor.py`/`inventory.py` are sound and well tested, but five things will trip
the P2 briefs unless consolidated first: (1) the *documented* "unknown `MWH_*` keys are rejected"
safety claim is false for shell environment variables (verified: `MWH_DATA_ROOTT` is silently
ignored) — owner decision: **warn** (doctor row + one stderr line), not refuse; (2) `mwh <validated
command> --help` exits 2 on an unsafe root or a broken `.env` because the group callback validates
before click parses `--help` (typer clears `ctx.args` before the callback, so an argv check is the
only cheap route; the pending-error property is the clean one); `DIAGNOSTIC_COMMANDS` now lists 6 of 7
commands — owner decision: fix `--help` now, keep the allow-list, decide lazy validation at EP-16;
(3) console encoding is handled three ways across five `Console()` instances — owner decision:
**UTF-8 entry point + one shared console module** (`PYTHONUTF8=1` from settings.json is the session
belt, this is the braces for hooks/redirects/owner shells); (4) fixture/demo tiers have **no lake
root** and `catalog_path("fixture")` points into the credentialed data root — owner decision:
`lake/fixture` + `lake/demo` (+ `lake/rejects`) as layout keys with a hard refusal that a fixture/demo
build ever resolves the credentialed lake; (5) `inventory.build` on a no-op resume overwrites the
snapshot's job/version block (breaks EP-16's own recipe), never re-evaluates header status when the
contract changes, and offers no table→record lookup for EP-17/20.

## In scope

1. **Shared console** (CFG-6) — `src/mimicwarehouse/console.py`: `console`, `err_console` (rich, one
   instance each), `console_safe(text)` (moved from `verify._console_safe`, kept as an alias there),
   `run()` entry wrapper: `for s in (sys.stdout, sys.stderr): s.reconfigure(encoding="utf-8",
   errors="replace")` then `app()`; `pyproject` `mwh = "mimicwarehouse.console:run"` (typer's root
   callback stays `cli.main`); every command module imports `console` from here (cli, doctor, config
   `paths`, guard, verify, inventory, schema/cli, fixtures/cli); doctor's glyph switch keeps working
   under the new encoding; JSON outputs unchanged (`\n` line ends — verify no `\r\r\n`); update Risk 13
   wording (EP-166 owns the prose; here the code + one DESIGN §2 line). Test: `mwh --help` / `doctor`
   through `subprocess` with `PYTHONIOENCODING` unset and stdout piped → decodes as UTF-8, no `?`/`�`.
2. **`--help` under an unsafe root** (CFG-5) — in `cli.main`, load `load_settings(checked=False)` for
   every command, store any `ConfigError`/`ValidationError` as `CliState.pending_error`, and make
   `CliState.settings` a property that raises `typer.Exit(2)` with the message on first access for
   non-diagnostic commands (so `--help`, `--version`, `no_args_is_help` never trip); keep
   `DIAGNOSTIC_COMMANDS` (document in the module docstring that lazy validation is the EP-16 decision);
   tests: `mwh inventory --help` and `mwh inventory build --help` with `--data-root Q:\nowhere` → exit 0;
   `mwh inventory build` → exit 2 (unchanged); README(workspace) sentence "doctor and paths run anyway"
   → the six diagnostic commands + `--help` everywhere.
3. **Unknown `MWH_*` env vars** (CFG-1) — `config.unknown_env_keys() -> list[str]` (names only, never
   values; compare against `Settings.model_fields` with the `MWH_` prefix, case-insensitive);
   `doctor.check_settings` → `warn` listing them; the CLI callback prints one stderr line via
   `err_console`; correct the four doc sentences (`.env.example` header, workspace README, `tests/README`,
   DESIGN §20 — coordinate with EP-166: whichever runs first edits, the other verifies); test sets
   `MWH_DATA_ROOTT` and asserts the warning + that `Settings()` still constructs.
4. **Lake roots per tier + layout keys** (ARCH-3, FXT-10, ARCH-9) — `LAYOUT_KEYS` += `lake_fixture`
   (`lake/fixture`), `lake_demo` (`lake/demo`), `lake_rejects` (`lake/rejects`) → 18 keys (bump the pin
   in `test_ep03` (2 places), `config.py` docstrings, DESIGN §3 tree via EP-166, `mwh paths` output);
   `Settings.lake_root(tier)` → `layout["lake"]` for dev/full, `layout["lake_demo"]`, `layout["lake_fixture"]`;
   `Settings.rejects_root(tier)` → `<lake_root(tier)>/rejects` for fixture/demo, `layout["lake_rejects"]`
   for dev/full (so synthetic rejects never mix with credentialed ones); `catalog_path(tier)` unchanged
   (`warehouse/<tier>.duckdb` for all four tiers — the fixture catalog "built for keeps" lives there;
   tests keep `--data-root tmp_path`); a module-level `assert_not_credentialed_lake(tier, lake_root,
   settings)` helper for EP-19 to call (refuses `fixture`/`demo` builds whose root equals
   `layout["lake"]`); `require_free_space` policy: full 100 GB guard only for dev/full/demo lake roots,
   1 GB for `fixture` (`Settings.min_free_gb_for(tier)`); document in `mwh paths` (`tier` column optional).
5. **DuckDB temp dir + connection sites** (CFG-3, CFG-2) — do **not** mkdir inside
   `Settings.duckdb_settings()`; instead `settings.layout["tmp_duckdb"].mkdir(parents=True,
   exist_ok=True)` at the two existing connection sites (`inventory.open_connection`,
   `fixtures.catalog.build_fixture_catalog`) and state in EP-17's item 1 (EP-170 amends) that
   `open_build_connection` must ensure it too — DuckDB 1.5.5 creates the leaf temp dir but not a
   missing parent (verified IOException on first spill); `doctor.check_temp_dir` distinguishes
   "exists" / "parent exists (DuckDB will create the leaf)" / "parent missing → warn"; keep the two
   openers as they are (a generic engine factory is EP-17's `loader/engine.py`, per the verifier).
6. **`--data-root` propagation** (CFG-4) — `verify.verify()` gains `env` handling: when the CLI was
   invoked with `--data-root`, the pytest child runs with `MWH_DATA_ROOT=<resolved root>` in its
   environment (never mutate `os.environ` in the callback — 22 CliRunner tests pass `--data-root`
   in-process); one sentence in DESIGN §15 (EP-6 note) that spawn/background jobs (EP-19) must pass the
   same env; test asserts the child env.
7. **Doctor rows** (GOV-3, ledger ENV/CFG lows) — `deny_coverage`: warn when `settings.data_root` is not
   under a prefix present in `.claude/settings.json` deny rules (parse the JSON; extract `//C:/…/**` and
   `C:/…/**` prefixes; info when the file is missing); `git`: info row with `git --version` +
   `core.longpaths` already in `longpaths` — merge if trivial; keep 14 → 15 checks (update README/DESIGN
   counts; the doctor JSON shape only grows).
8. **Inventory fixes** (INV-1, INV-2, INV-4 + lows) — (a) no-op resume (`todo == []`, not `--force`):
   copy `started/finished/last_file/pid/hostname/options` from `previous` into `job` and pass
   `versions=None` so `duckdb_version/python_version/git_sha/mimic_code_sha/contract_hash` are re-used;
   still append the `runs` entry (`processed=0, skipped=41`); regression test builds twice with a
   monkeypatched `_git_sha`; (b) for `matches_stat` records recompute `header_matches_contract/missing/
   extra` from the stored `header` list against the current contract (no file I/O), rewrite that
   dataset's JSONL when changed, count in `BuildResult.refreshed` (not `processed`; the snapshot id
   excludes header, so it stays stable); (c) `rel_path_for(table) -> str` and
   `RawManifest.for_table(table) -> FileRecord | None`; use them at the three duplicated key sites +
   `test_ep10`; (d) accept `--no-resume` as advertised (alias of `--force` or remove the wording);
   (e) `render_docs` `Generated:` line uses the snapshot's `finished` timestamp, not wall clock, so a
   no-op `reconcile` leaves git clean; (f) `--json` outputs end in `\n` (CFG-6 side effect check).
   Do **not** change `raw_snapshot_id` or `FILES_EXPECTED` semantics (INV-4/ARCH lows → EP-16 note).
9. **Tests / docs** — `tests/ep/test_ep167.py` (`ep_167`, fixture) for the new behaviour, plus targeted
   edits to `test_ep02/03/06/10` where contracts changed (layout count, doctor count, console); README
   (workspace) Quick start + doctor list; `.env.example` unchanged unless a new field is added (none is);
   DESIGN §15 dated note (EP-167) listing the new API names for EP-17/19/21:
   `Settings.lake_root / rejects_root / min_free_gb_for / assert_not_credentialed_lake`,
   `console.console / err_console / console_safe`, `inventory.rel_path_for / RawManifest.for_table`.

## Out of scope

- Loader connection factory (`loader/engine.py`, EP-17), the catalog swap code (EP-21 implements the
  rename-aside protocol EP-166 documents), lazy settings validation (EP-16 decision), tier readiness
  fixtures (EP-168), contract/fixture changes (EP-169).

## Verification / acceptance

- `uv run poe test -m ep_167` green; `uv run poe check` green (all earlier markers included);
  `mwh --help` ≤ 0.6 s; `mwh doctor` 15 checks, exit 0; `mwh paths` shows 18 keys; `mwh inventory
  build` twice on the fixture-tier synthetic tree of `test_ep10` leaves the snapshot job block
  unchanged; `mwh verify EP-167` exit 0; `poe roadmap-check --strict` 0/0.
- Commit pair: `feat(mimicwarehouse): shared console + UTF-8 entry point, --help under unsafe root, unknown MWH_* warn, per-tier lake roots (18 layout keys), temp-dir policy, --data-root propagation, doctor deny_coverage, inventory resume fixes (EP-167)` then `docs(roadmap): record EP-167 commit hash`.
