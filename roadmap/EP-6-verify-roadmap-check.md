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
