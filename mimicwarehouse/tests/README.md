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
PYTEST_TIER=dev uv run poe test          # environment fallback for --tier
```

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
variable **`PYTEST_TIER`** — deliberately not `MWH_`-prefixed, because `MWH_*` is the
`Settings` env prefix and `Settings` is `extra="forbid"` (a stray `MWH_TEST_TIER` would break
every `Settings()` construction and would need an `.env.example` line). The ladder is the
three-step subset of `config.Tier` (`fixture | demo | dev | full`): `demo` is a *data* tier for
the ODbL demo dataset (EP-22) and screenshots, never a *test* tier, and the pytest tier never
reads `settings.default_tier` (which defaults to `dev` for commands, not for tests).

`--strict-markers` is on (`pyproject.toml`), so an unregistered marker is a collection error;
`tier("<anything else>")` is a usage error.

Real data (dev/full) never appears in test output: dev/full tests assert on counts, schemas and
aggregates only (GOVERNANCE §4), and there are none until EP-17 — EP-12 only makes the markers
work.

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
