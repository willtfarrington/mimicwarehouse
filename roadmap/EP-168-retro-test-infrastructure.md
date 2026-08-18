# EP-168 — Retro D: test-infrastructure consolidation (tier readiness, demo marker, churn)

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-12 (Synthetic fixture generator B (icu) + pytest tier markers) · **Blocks:** EP-17 (Loader core A: typed CSV → Parquet), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo))

> **Origin.** Fourth retro brief (origin note in EP-165; ledger =
> [`retro-2026-08-18-findings.md`](retro-2026-08-18-findings.md); decisions = D-43). Ledger ids: VT-1,
> FC-1, VT-2, ARCH-7, FC-2, FXT-11, FC-11 (+ low VT-3 … VT-15 tagged EP-168: xdist, hypothesis, helpers
> duplication, pinned skip strings, `tests/README` bash-only example).

## Context

EP-12's tier gate skips every `tier("dev")`/`tier("full")` test until `<data_root>/warehouse/dev.duckdb`
exists (EP-21), but EP-17 … EP-20 define dev tests that need only the raw CSVs / a lake / a `status.json`
predicate — their "`--tier dev` green" acceptance would be vacuous for four EPs and the first real-data
staging bug would surface at EP-23's background job instead of EP-17's 12 MB test. Owner decision
(2026-08-18): the collection hook only **deselects** above the maximum tier; **readiness moves into
requestable fixtures**. EP-22 wants `--tier demo` in the ladder, which EP-12 shipped and tests the opposite
of — owner decision: an **orthogonal opt-in marker** (`@pytest.mark.demo` + `--with-demo`). Cross-EP
churn is real (`test_ep06` was edited by six of six code EPs after it because of a rolling "code brief
without a test module" literal; `test_ep11` file counts by EP-12) — fix the mechanism.

## In scope

1. **Readiness fixtures + `needs=`** (VT-1, FC-1) — `tests/conftest.py`: `pytest_collection_modifyitems`
   keeps deselection above the max tier and the coarse "settings unavailable" skip only; new session
   fixtures `dev_catalog` (skip unless `get_settings().catalog_path("dev").is_file()` — EP-21+ tests),
   `full_catalog`, `raw_root` (skip unless `settings.source_root / inventory.dataset_dir("mimic-iv-3.1")`
   is a directory — what EP-17/18/19/20 need; reason names the path), `dev_ready(step)` factory (skip
   unless `lake/manifests/status.json` marks the step `dev_ready`; EP-23/24/25) and `item_tier` (the
   test's own `tier` marker name, so dev tests stop hard-coding `"dev"` — `test_ep12.py:867` today);
   accept `tier(name, needs="catalog"|"raw"|"lake")` (relax the kwargs check at `tier_of`; default
   `catalog` keeps EP-12 semantics for existing tests) resolved in the hook for briefs that prefer the
   marker form; update the pinned reason strings in `test_ep12` (two), the marker registration text and
   `--tier` help, `tests/README.md` table + a "which fixture do I request" paragraph, DESIGN §20 dated
   note (words may already be drafted by EP-166 — verify, do not duplicate), pyproject task comments.
2. **Demo opt-in marker** (ARCH-7, FC-2) — `@pytest.mark.demo` registered in `pytest_configure`;
   `--with-demo` option (env fallback `PYTEST_DEMO=1`, deliberately not `MWH_`-prefixed); demo tests
   deselected unless opted in and skipped with a reason while `catalog_path("demo")` is missing (reuse
   `catalog_status`); `mwh verify EP-22 -- --with-demo` passes through EP-6's `--`; `TIERS` and the
   maximum-tier semantics unchanged; `test_ep12`'s poe/docs needle list updated; EP-170 amends EP-22
   item 5 + acceptance line to this vocabulary.
3. **Rolling-literal churn** (VT-2) — `test_ep06::test_mwh_verify_usage_errors`: replace the `verify
   EP-17` literal with the crafted-roadmap probe already used elsewhere in the file (`_make_roadmap(tmp_path)`
   + empty `ws/tests/ep`, `monkeypatch.setattr(verify, "roadmap_dir"/"workspace_root", …)`, invoke
   `verify EP-1`, assert exit 2 + `test_ep01.py` + "code brief"); grep every `tests/ep/test_ep*.py` for
   other forward-pointing literals ("EP-17", "31 files", "22 hosp + 9 icu") and replace with values read
   from `tests/fixtures/manifest.json` / `build_plan()` / the roadmap parser; add a one-line rule to
   `tests/README.md`: "a new EP must not need to edit an earlier `test_ep*.py`; if it does, the coupling
   is the bug".
4. **Shared test helpers** (VT lows) — `tests/helpers.py` (or `tests/_util.py`, importable, not a
   plugin): `cli_runner()` with `COLUMNS=200`, `tmp_data_root(monkeypatch, tmp_path)` that clears
   `MWH_*` env + `config.configure()`, `fresh_interpreter(argv)`; migrate the duplicated setups in
   `test_ep02/03/04/09/10/164` opportunistically (only where a test is touched anyway — no wholesale
   rewrite); note `pytest -n auto` is safe (verifier: 139 tests / 7.2 s on four modules) but keep `poe
   test` serial by default; add `poe test-fast = pytest -n auto`.
5. **Docs** — `tests/README.md` (fixtures table, demo marker, helper module, churn rule); DESIGN §20
   dated note; `pyproject` task comments.

## Out of scope

- The fixture-catalog re-pointing at EP-21 item 4 (FXT-11/FC-11 → EP-170 amends EP-20/21: option B —
  keep the in-memory `fixture_catalog` for unit tests, add a separate `fixture_lake_catalog` fixture
  when EP-21 lands, and either declare all 31 stage steps in EP-20 or drop `--tag small`); the
  fixture regeneration (EP-169); the CLI console (EP-167).

## Verification / acceptance

- `uv run poe test` green; `uv run poe test-dev` and `poe test-full` on this machine show EP-12's
  probes **skipped with the new fixture-based reasons** (`raw_root` present → a crafted `raw_root` test
  passes without reading data: it asserts only that the directory exists); `pytester` cases for
  `needs="raw"` and `--with-demo`; `uv run poe test -m ep_168` and `mwh verify EP-168` exit 0;
  `poe roadmap-check --strict` 0/0.
- Commit pair: `feat(mimicwarehouse): tier readiness fixtures + needs=, demo opt-in marker, test_ep06 probe de-coupled, tests/helpers (EP-168)` then `docs(roadmap): record EP-168 commit hash`.
