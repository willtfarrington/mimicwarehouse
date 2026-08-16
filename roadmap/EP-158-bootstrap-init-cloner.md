# EP-158 — Bootstrap `mwh init` + cloner smoke test on demo tier

**Size:** M · **Tier:** demo · **Core/Stretch:** core · **Depends on:** EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-28 (Verify full staging) · **Blocks:** EP-159 (Demo mode for the app), EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

Democratization = bootstrap script + docs site + demo mode (D-12). This brief is the bootstrap: one
`mwh init` that takes a fresh clone from nothing to a working tier, and proof that a stranger without
PhysioNet credentials can do it on the ODbL MIMIC-IV Demo 2.2 (+ ED Demo). It composes commands that
already exist — `mwh doctor` (EP-2), `mwh paths` (EP-3), `mwh demo fetch` (EP-22, applies the 2.2 → 3.1
column map; the demo has no note module), `mwh build --tier <tier>` (EP-19; loader EP-17/18, catalog
EP-21, concepts EP-37), `mwh verify` (EP-6) — and is the documented recovery recipe (DESIGN §3;
GOVERNANCE §11: `mwh init` + `mwh build --tier full`). Constraints: data root on local NVMe, never G:/D:
(D-29); refuse under 100 GB free (DESIGN §3); explicit DuckDB memory/threads/temp (DESIGN §6); full-tier
work is a logged background job, never foreground (D-18). Tier `demo`: the smoke test runs on the demo
tier; the full-tier path is a dry-run only (the real full rebuild was the P2 ⏱ briefs verified by EP-28
and the EP-135 full-tier regression).

## Scope sketch (refine at re-plan)

1. **`mwh init` (`cli.py` → new `src/mimicwarehouse/bootstrap.py`; add the module to DESIGN §15 with a
   dated note)** — `mwh init --tier {demo|dev|full} [--data-root PATH] [--dry-run] [--from STEP] [--yes]`.
   Ordered, idempotent steps (each skips when its manifest/marker exists), logged to
   `%MWH_DATA_ROOT%\runs\jobs\init-<tier>.log`: (1) `doctor` preflight — uv-managed 3.13, ≥ 100 GB free,
   data root local and writable, BitLocker result recorded, drive not G:/D:; (2) data-root layout
   (`mwh paths --create`) and `.env` from `.env.example` if missing; (3) source check — demo: `mwh demo
   fetch` into `ext\demo\` (public URL pinned + checksummed by EP-22); dev/full: raw CSVs present under
   `source material/` per the EP-10 inventory manifest, else stop and print the `source material/README.md`
   instructions; (4) `mwh build --tier <tier>` — demo/dev foreground with progress, full launched as a
   background job whose job id is printed; (5) tier checks (row-count pins, key integrity, EP-37 concept
   count pins on demo); (6) print next steps. `--dry-run` prints the plan and touches nothing.
2. **`bootstrap.ps1` (workspace root, PowerShell 7)** — the cloner's entry point: checks `uv` and prints the
   official install command if missing (never installs silently), `uv sync --group dev`,
   `uv run --group dev mwh init --tier demo`, then `uv run --group ui mwh app --tier demo`. Windows only
   (D-14); a bash equivalent is parked.
3. **Cloner smoke test** — `git clone --depth 1` of the local repo into the session scratchpad,
   `MWH_DATA_ROOT` pointed at a throwaway local root (e.g. `C:\mimicdata-clonetest`), no raw CSVs
   under `source material/`; run `bootstrap.ps1` end to end; assert `demo.duckdb` exists, EP-37 demo
   count pins pass, `uv run --group dev mwh sql "SELECT count(*) FROM mimiciv_hosp.admissions"` answers
   through `safe_query`, the app starts. Record wall time, peak RSS and disk delta as an `init_demo` line
   in `runs/benchmarks.jsonl` and in the completion note; delete the throwaway root afterwards (ask the
   owner).
4. **Tests `tests/ep/test_ep158.py`** (`@pytest.mark.ep_158`, fixture) — `--dry-run` plan snapshot
   (ordered steps, zero writes); second run skips every completed step; refusals: free disk < 100 GB
   (monkeypatched), data root on a G:/D:-style path, `--tier full` without raw CSVs; `--tier full` returns
   a job id without running (job runner mocked).
5. **`docs/getting-started.md`** — three paths: *cloner* (demo, no credentials, ODbL attribution),
   *credentialed researcher* (dev then full; where CSVs go; ≥ 100 GB rule; expected hours from the
   benchmark ledger), *recovery* (`mwh init` + `mwh build --tier full` + `mwh backup` restore of ledgers,
   EP-52). Consumed by the docs site (EP-160).

## Out of scope

- Demo-mode banner/unlocks in the app → EP-159; docs site → EP-160.
- Any real full-tier rebuild in this brief (dry-run only; timings come from P2 ⏱ briefs and EP-135).
- CI on the demo tier, Docker portability, `.csv.gz` re-download → `final-roadmap.md` (already listed).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_158` green on fixture; `uv run --group dev mwh verify EP-158` green.
- Cloner smoke test passed from a fresh clone with no raw CSVs; `init_demo` benchmark line and
  wall time in the completion note; demo catalog passes the EP-37 count pins.
- `mwh init --dry-run` for all three tiers prints the plan and creates no file; refusal tests (disk,
  drive letter, missing raw) green.
- `docs/getting-started.md` exists and its commands were executed verbatim in the smoke test.

## Parked → final-roadmap.md

- `bootstrap.sh` (Linux/macOS path; needs a non-Windows test box) · `mwh init --with-notes` for the
  segregated notes lake (only if the P10 track ships) · GitHub Actions running `mwh init --tier demo`
  (after v1.0.0 goes public; already under Cross-cutting).
