# mimicwarehouse tests

pytest + hypothesis (DESIGN §20). One acceptance module per roadmap brief under `tests/ep/`
(`test_ep<NN>.py`, marker `ep_<n>`), synthetic data only under `tests/fixtures/`
(`tests/fixtures/README.md`), and the tier machinery in `tests/conftest.py`.

## Running

```
uv run poe test                 # fixture tier: every unmarked test + tier("fixture") tests
uv run poe test -m ep_12        # one brief's acceptance tests
uv run mwh verify EP-12         # the same, in a fresh interpreter (EP-6)
uv run poe test-dev             # = pytest --tier dev   (adds tier("dev") tests)
uv run poe test-full            # = pytest --tier full  (adds tier("full") tests)
uv run mwh verify EP-17 -- --tier dev    # pytest args after `--` pass through untouched
PYTEST_TIER=dev uv run poe test          # environment fallback for --tier (bash)
```

PowerShell has no `VAR=x cmd` prefix form — the fallback there is
`$env:PYTEST_TIER='dev'; uv run poe test`.

`uv run poe check` (`lint` + `typecheck` + `test`) is fixture-only on purpose; `test-dev` /
`test-full` are separate tasks.

## Tiers (markers)

| Marker | Data | Runs when | If the catalog is missing |
|---|---|---|---|
| *(none)* / `@pytest.mark.tier("fixture")` | the committed synthetic fixture (`tests/fixtures/`, ids ≥ 90 000 000), read through the in-memory `fixture_catalog` | always | n/a — the fixture catalog is built in memory from the CSVs |
| `@pytest.mark.tier("dev")` | `dev.duckdb` (5 % of subjects, `subject_id % 100 < 5`; EP-21) | `--tier dev` or `--tier full` | **skipped** with a reason (never fails) |
| `@pytest.mark.tier("full")` | `full.duckdb` (EP-21) | `--tier full` | **skipped** with a reason |

`--tier` names the **maximum** tier to run: `fixture < dev < full`. Tests above the selected
tier are *deselected* (they do not appear as skips); tests inside it whose catalog file
(`get_settings().catalog_path(tier)`, i.e. `<data_root>/warehouse/<tier>.duckdb`) does not
exist are *skipped* with the path in the reason. The fallback for `--tier` is the environment
variable **`PYTEST_TIER`** — deliberately not `MWH_`-prefixed, to keep the test knob out of
the `Settings` namespace and the `.env.example` parity test. (Precisely: unknown keys in
`.env`/`mwh.toml` are rejected — `extra="forbid"` — but an unknown `MWH_*` *environment
variable* is ignored by pydantic-settings; `mwh doctor` gains a warn for those at EP-167,
ledger CFG-1.) The ladder is the
three-step subset of `config.Tier` (`fixture | demo | dev | full`): `demo` is a *data* tier for
the ODbL demo dataset (EP-22) and screenshots, never a *test* tier, and the pytest tier never
reads `settings.default_tier` (which defaults to `dev` for commands, not for tests).

`--strict-markers` is on (`pyproject.toml`), so an unregistered marker is a collection error;
`tier("<anything else>")` is a usage error.

Real data (dev/full) never appears in test output: dev/full tests assert on counts, schemas and
aggregates only (GOVERNANCE §4). The only dev/full-marked tests until EP-17 are EP-12's two
marker-mechanics probes (open the tier catalog read-only, `SELECT 1`), which show as skipped
until EP-21 writes the catalogs.

## Changing the synthetic fixture

*(the fixture-change protocol, D-43 item 10 / ledger FXT-3 — hand-maintained here because
`tests/fixtures/README.md` is generator-rendered and drift-tested; do not add prose there)*

Any change to `src/mimicwarehouse/fixtures/{spec,hosp,icu,write}.py`, `fixtures/vocab/*.yaml`
or the EP-9 schema contract changes fixture bytes. The protocol:

1. `uv run --group dev mwh fixtures build` — regenerates the committed tree in place
   (defaults = the committed spec; never hand-edit a fixture CSV).
2. Review `git diff mimicwarehouse/tests/fixtures/manifest.json` — the manifest's per-file
   `sha256`/`rows`/`bytes` diff **is** the review surface (`*.csv` is `binary` in
   `.gitattributes`, so CSV diffs don't render).
3. Bump `write.GENERATOR_VERSION` when any manifest sha256 changes for the default spec:
   **patch** = bytes of existing tables moved; **minor** = new tables/modules/spec keys
   (a README-/manifest-only change needs no bump). The next regeneration is **0.2.0**
   (EP-169: disjoint id floors — D-27 addendum).
4. Commit CSVs + `manifest.json` + `tests/fixtures/README.md` + the version bump in **one**
   commit; bundle generator tweaks so regenerations stay rare (a full regen adds < 1 MiB
   compressed to `.git`).

Downstream tests read counts from `manifest.json` / `fixtures.spec.build_plan()`, never
hard-coded literals; byte identity is asserted against the *locked* numpy/polars versions
(deliberately unpinned — a lock bump that moves bytes is handled as a regeneration, ledger
FXT-2).

## Session fixtures (`tests/conftest.py`)

| Fixture | Value |
|---|---|
| `tier` | the selected maximum tier (`"fixture"` / `"dev"` / `"full"`) |
| `contract` | the EP-9 schema contract (`load_contract()`) |
| `fixture_root` | `mimicwarehouse/tests/fixtures` (`fixtures.write.default_out_dir()`) |
| `fixture_catalog` | in-memory DuckDB with the 31 contract tables loaded from the fixture CSVs (`fixtures.catalog.build_fixture_catalog()`; contract types, comments); closed at session end |

Markers registered: `ep_0` … `ep_199`, `tier(name)`. Hypothesis profiles: `default`
(50 examples) and `ci` (200), chosen with `HYPOTHESIS_PROFILE`. `pytester` is enabled for
marker-selection tests.
