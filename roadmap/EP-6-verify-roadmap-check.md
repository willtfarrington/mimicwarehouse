# EP-6 — `mwh verify EP-n` + roadmap_check.py

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-2 (`mwh` CLI skeleton + `mwh doctor`) · **Blocks:** EP-7 (Re-plan P0)

## Context

Every code brief's acceptance says "`uv run poe test -m ep_<n>` green and `uv run mwh verify EP-<n>`
green", and every re-plan EP reconciles the ☑ hashes in `roadmap/README.md` against `git log` "via
`roadmap_check.py`" (planning defaults; D-37 roadmap format, D-8 re-plan per phase). Neither exists
yet. `mimicwarehouse.verify` (DESIGN §15) provides both: `mwh verify` runs one brief's pytest marker
set (markers `ep_<n>` registered by EP-1's `conftest.py`; tier selection arrives with EP-12 and is
passed through untouched), and `roadmap_check` parses the master roadmap tables and the briefs'
header lines — the formats fixed by the planning session: table rows
`| EP-n | [Title](EP-n-slug.md) | Size | Depends | core/stretch | ☐ or ☑ \`hash\` (+ \`hash2\`) |`,
brief header `**Size:** … · **Tier:** … · **Core/Stretch:** … · **Depends on:** EP-a (name), … ·
**Blocks:** …`. Docs-only briefs (Tier `n/a`, e.g. EP-0, EP-7) have no test module and must still
verify cleanly. Commands run in `mimicwarehouse/`; the roadmap lives at `../roadmap/`.

## In scope

1. **`src/mimicwarehouse/verify.py` — `verify`**: `resolve_ep("EP-6" | "ep6" | "6") -> int`;
   `verify(ep, pytest_args=()) -> int` runs `[sys.executable, "-m", "pytest", "-m", f"ep_{n}",
   "-p", "no:cacheprovider", *pytest_args]` as a subprocess (fresh interpreter; Windows spawn-safe)
   with `cwd` = the workspace root, and returns pytest's exit code. Before running it looks up the
   brief `../roadmap/EP-<n>-*.md`; if the brief's header Tier is `n/a` and no `tests/ep/test_ep<NN>.py`
   exists it prints "docs-only brief — nothing to run" and returns 0; if a code brief has no test
   module it returns 2. Exit code 5 (pytest "no tests collected") is reported as failure 2 with the
   hint that the marker is missing. CLI: `mwh verify EP-n [-- <extra pytest args>]` prints the
   brief title, tier and marker before delegating; `mwh verify --list` prints EP · title · tier ·
   test module present.
2. **`src/mimicwarehouse/verify.py` — `roadmap_check(roadmap_dir, repo_root, strict=False) ->
   Report`**, exposed as `mwh verify --roadmap [--strict] [--json]` and as the thin script
   `mimicwarehouse/scripts/roadmap_check.py` (poe task `roadmap-check`). Checks:
   - **parity**: every table row links a file that exists; every `roadmap/EP-*.md` (excluding
     `*-completion-handoff.md`, `*-completion-report.md`) appears in exactly one row; row number =
     file-name number;
   - **header ↔ table**: brief H1 (`# EP-n — Title`) equals the row title exactly (backticks and ⏱
     included); Size and Core/Stretch match; the Depends-on set — tokens `EP-\d+` taken with
     `re.findall(r"EP-(\d+)(?= \()", segment)` from the header, bare `EP-\d+` from the table cell —
     equals the row's Depends set;
   - **☑ hashes**: each `☑ \`hash\`` (one or two hashes joined by `+`) resolves via
     `git cat-file -e <hash>^{commit}`; warn when the commit message lacks `(EP-n)`; warn when a ☑
     brief depends on a ☐ brief;
   - **charters**: rows under a phase heading that says `charter briefs` link briefs carrying the
     `> **Charter.**` line naming a re-plan EP that exists (error otherwise); rows under
     `full briefs` headings must not carry it.
   Errors → exit 1; warnings → exit 0 unless `--strict`. Output: rich table grouped by check + a
   one-line summary; `--json` for the re-plan session. Mirroring `## Parked` items into
   `final-roadmap.md` stays a manual re-plan step (EP-7 onward), not a check.
3. **Tests `tests/ep/test_ep06.py`** (`@pytest.mark.ep_6`): `resolve_ep` variants and rejects
   `EP-x`; `verify` argument construction with `subprocess.run` mocked; end-to-end
   `mwh verify EP-2 -- -q` in a subprocess exits 0 and `mwh verify EP-0` prints the docs-only line
   and exits 0; `roadmap_check` on the real `../roadmap/` returns zero errors (if a
   planning-session brief disagrees with its table row, fix the brief header — never the table —
   and list the fix in the completion note); on a crafted
   `tmp_path` roadmap (README with two rows + briefs) each fault is detected — missing file,
   H1 mismatch, Depends mismatch, unresolvable hash, ☑ depending on ☐ (mock `git cat-file`) —
   with the expected error/warning class; `--strict` flips warnings to exit 1.
4. **Docs**: `roadmap/README.md` "How to use" is already worded for these commands — do not edit
   it; `mimicwarehouse/README.md` quick start line `uv run --group dev mwh verify EP-<n>` now works; add
   `uv run poe roadmap-check` beside it.

## Out of scope

- Tier selection (`--tier fixture|dev|full`) and the tier marker semantics → EP-12 (`mwh verify`
  passes extra pytest args through unchanged).
- Editing ☑ boxes or hashes automatically → never; the session ticks boxes by hand and
  `roadmap_check` only reports.
- Coverage-matrix / capability-table audits → the re-plan EPs (EP-7 onward) read the JSON output.

## Verification / acceptance

- `uv run --group dev mwh verify EP-1`, `EP-2`, `EP-3`, `EP-4`, `EP-5` each exit 0 (their marker
  sets green); `uv run --group dev mwh verify EP-0` exits 0 with the docs-only line;
  `uv run --group dev mwh verify --list` shows EP-0 … EP-6.
- `uv run poe roadmap-check` exits 0 on the current roadmap (0 errors; the ☑ hashes recorded so
  far resolve); a crafted mismatch in a scratch copy of `roadmap/` (session scratchpad — never the
  real one) is reported with the right class.
- `uv run poe test -m ep_6` green; lint / typecheck green; `uv run --group dev mwh verify EP-6`
  exits 0.

## Parked → final-roadmap.md

- `roadmap_check --fix` (auto-insert ☑ hashes from `git log` messages) — trigger: after ≥ 3
  phases of hand-ticking prove tedious; hazard: rewriting the master table by script.

> **Completion note (2026-08-17).** Delivered as briefed; `uv run poe check` green (ruff, pyright
> basic, 194 tests of which 47 are `ep_6`); `uv run --group dev mwh verify EP-0` prints the docs-only
> line and exits 0, `EP-1` … `EP-6` each exit 0 (10 / 27 / 32 / 44 / 34 / 47 tests), `mwh verify
> --list` shows EP-0 … EP-163 with the six test modules present; `uv run poe roadmap-check` exits 0
> on the real roadmap — **0 errors**, 164 rows = 164 briefs, 6 ☑, 1 warning (`cd67743`, the planning
> commit ticked under EP-0, has no `(EP-0)` in its subject — expected, predates the convention;
> `--strict` therefore exits 1). No planning-session brief disagreed with its table row, so no brief
> header was edited. A scratchpad copy of `roadmap/` with three planted faults (EP-6 header Size S→M
> and Depends EP-2→EP-1; `EP-8-*.md` deleted; EP-5's hash replaced by `0000bad`) reported exactly
> `parity` 1 / `header` 2 / `hashes` 1 error(s) and exit 1.
> - `src/mimicwarehouse/verify.py`: `resolve_ep`, `find_brief` / `parse_brief` (`Brief`),
>   `parse_roadmap_table` (`Row`, tagged with the enclosing `## Phase …` heading → `charter` /
>   `full`), `roadmap_check(roadmap, repo, strict) -> Report` (`Finding(level, check, ep,
>   message)`; `errors` / `warnings` / `exit_code(strict)` / `summary()` / `as_dict()`), git only
>   through `_run_git` (`cat-file -e <hash>^{commit}`, `log -1 --format=%s`; tests replace it),
>   `ep_test_module`, `pytest_argv`, `verify(ep, pytest_args, workspace=, roadmap=, echo=)`,
>   `verify_command` (`mwh verify EP-n [-- …] | --list | --roadmap [--strict] [--json]`; exactly
>   one mode, else exit 2) and `roadmap_check_main(argv)` for the script. `verify` joined
>   `DIAGNOSTIC_COMMANDS` (never touches the data root). Deltas from the brief: ☑ cells accept one
>   **or more** hashes (EP-0 carries three); a charter naming an EP that exists but is not a
>   re-plan brief is a warning, not an error; the `--json` report embeds every row (+ brief tier and
>   charter EP) for the re-plan sessions; a `☐` cell that carries hashes, a `☑` without a hash, a
>   Done cell that is neither, duplicate rows / duplicate links / duplicate brief files, a brief
>   without H1 or header line, and an H1 number ≠ file-name number are all `parity`/`hashes`
>   errors. Console strings pass through `_console_safe` (⏱ / ☑ become `?` on a cp1252 console
>   instead of crashing rich; PowerShell/Windows Terminal render them).
> - `mimicwarehouse/scripts/roadmap_check.py` (thin) + poe task `roadmap-check`
>   (`python scripts/roadmap_check.py [--strict] [--json] [--roadmap DIR]`); `pyright` now also
>   includes `scripts/`. `cli.py`: one `app.command("verify", context_settings=…)` line
>   (`allow_extra_args` + `ignore_unknown_options` so `-- -q -k x --tier dev` reaches pytest
>   untouched). Workspace `README.md` quick start: `mwh verify EP-<n>`, `--list`, `--roadmap`,
>   `poe roadmap-check`. `roadmap/README.md` untouched (as instructed). DESIGN §15 note added.
> - Tests (`tests/ep/test_ep06.py`, 47): `resolve_ep` accepts `EP-6`/`ep6`/`6`/`EP6`/int and rejects
>   `EP-x`/`""`/`-1`/`6a`/bool; `verify` argv + cwd with `subprocess.run` mocked, exit-code
>   pass-through, 5 → 2 with the marker hint, docs-only, code brief without module → 2, `n/a`
>   tier *with* a module still runs (EP-5 case), unknown brief → 2; end-to-end `python -m
>   mimicwarehouse.cli verify EP-2 -- -q` in a subprocess exits 0; CliRunner `verify EP-0`,
>   usage errors, `--list`, extra-arg pass-through; real roadmap: zero errors, all 164 briefs have
>   H1 + header, both phase kinds present, EP-6/EP-7 rows and briefs parse as expected; crafted
>   `tmp_path` roadmaps with a fake `_run_git`: clean baseline, missing README, missing file /
>   brief without row / H1 number mismatch / handoff file ignored, duplicate rows + links + wrong
>   row number, H1-title / Size / Core / Depends mismatches, header regex takes only `EP-n (`
>   tokens, unresolvable hash + subject-lacks-tag warning + ☑-depends-on-☐ warning, ☑ without
>   hash / ☐ with hash, `--strict` flips exit 0 → 1, four charter fault classes + a clean charter
>   phase, JSON shape; `--roadmap` / `--strict` / `--json` via CliRunner; `roadmap_check_main`
>   + the script in a fresh interpreter; poe task + README wording; `import mimicwarehouse.cli`
>   still pulls no duckdb / pandas / polars / pyarrow.
> - Timing: `mwh verify EP-6` ≈ 5 s (it spawns EP-2's marker set once); full `poe test` ≈ 14 s.
> - Nothing parked beyond the brief's `--fix` item. `poe roadmap-check --strict` stays red until
>   EP-7 decides whether the planning-commit warning on EP-0 is acceptable or the EP-0 row should
>   cite only the two `(EP-0)` commits.
