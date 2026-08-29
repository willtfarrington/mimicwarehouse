# mimicwarehouse tests

pytest + hypothesis (DESIGN §20). One acceptance module per roadmap brief under `tests/ep/`
(`test_ep<NN>.py`, marker `ep_<n>`), synthetic data only under `tests/fixtures/`
(`tests/fixtures/README.md`), the tier machinery in `tests/conftest.py`, and shared scaffolding
in `tests/helpers.py`.

**Churn rule (EP-168):** a new EP must not need to edit an earlier `test_ep*.py`; if it does,
the coupling is the bug — read counts from `tests/fixtures/manifest.json`, the schema contract
or `fixtures.spec.build_plan()`, probe verify/roadmap behaviour on crafted roadmaps under
`tmp_path`, and never pin a future EP number or file count into an assertion.

## Running

```
uv run poe test                 # fixture tier: every unmarked test + tier("fixture") tests (serial)
uv run poe test-fast            # the same via pytest -n auto (xdist); parallel-safe, opt-in
uv run poe test -m ep_12        # one brief's acceptance tests
uv run mwh verify EP-12         # the same, in a fresh interpreter (EP-6)
uv run poe test-dev             # = pytest --tier dev   (adds tier("dev") tests)
uv run poe test-full            # = pytest --tier full  (adds tier("full") tests)
uv run poe test --with-demo     # opts in the @pytest.mark.demo tests (EP-22)
uv run mwh verify EP-22 -- --with-demo   # pytest args after `--` pass through untouched
PYTEST_TIER=dev uv run poe test          # environment fallback for --tier (bash)
```

PowerShell has no `VAR=x cmd` prefix form — the fallback there is
`$env:PYTEST_TIER='dev'; uv run poe test` (likewise `$env:PYTEST_DEMO='1'`).

`uv run poe check` (`lint` + `typecheck` + `test`) is fixture-only on purpose; `test-dev` /
`test-full` are separate tasks, and `test` stays serial by default (`test-fast` is the xdist
opt-in; the AV heuristics of D-42 are why parallel is not the default).

## Tiers (markers)

| Marker | Data | Runs when | If its artefact is missing |
|---|---|---|---|
| *(none)* / `@pytest.mark.tier("fixture")` | the committed synthetic fixture (`tests/fixtures/`, ids ≥ 90 000 000), read through the in-memory `fixture_catalog` | always | n/a — the fixture catalog is built in memory from the CSVs |
| `@pytest.mark.tier("dev", needs=…)` | dev-tier artefacts (5 % of subjects, `subject_id % 100 < 5`) | `--tier dev` or `--tier full` | **skipped** with a reason naming the path (never fails) |
| `@pytest.mark.tier("full", needs=…)` | full-tier artefacts | `--tier full` | **skipped** with a reason |
| `@pytest.mark.demo` | the ODbL demo catalog (`demo.duckdb`, EP-22) | `--with-demo` / `PYTEST_DEMO=1` (orthogonal opt-in, any `--tier`) | **skipped** with a reason |

`--tier` names the **maximum** tier to run: `fixture < dev < full`. Tests above the selected
tier are *deselected* (they do not appear as skips); tests inside it are *skipped* while the
artefact they declare is missing. **Readiness is per-need, not one catalog file** (EP-168,
retro VT-1/FC-1): `tier(name, needs="catalog"|"raw"|"lake")` declares what the test actually
requires — `catalog` (the default; `get_settings().catalog_path(tier)`, i.e.
`<data_root>/warehouse/<tier>.duckdb`, EP-21), `raw` (the raw source dataset directory
`settings.source_root/mimic-iv-3.1`, what EP-17..20's dev tests need) or `lake`
(`lake/manifests/status.json`, EP-23+). The fallback for `--tier` is the environment variable
**`PYTEST_TIER`** — deliberately not `MWH_`-prefixed, to keep the test knob out of the
`Settings` namespace and the `.env.example` parity test (an unknown `MWH_*` environment
variable is ignored by pydantic-settings but warned about by `mwh doctor`, EP-167). The ladder
is the three-step subset of `config.Tier` (`fixture | demo | dev | full`): `demo` is a *data*
tier for the ODbL demo dataset (EP-22) and screenshots, **never** a test tier and never
"between" fixture and dev — demo tests are the orthogonal `@pytest.mark.demo` opt-in above —
and the pytest tier never reads `settings.default_tier` (which defaults to `dev` for commands,
not for tests).

`--strict-markers` is on (`pyproject.toml`), so an unregistered marker is a collection error;
`tier("<anything else>")`, an unknown `needs=` value or any other tier-marker kwarg is a usage
error.

### Which fixture do I request?

The marker form (`needs=`) is enough when the test only has to *run or skip*. Request a
readiness fixture when the test also wants the artefact's path or status — each one skips the
test with a reason while its artefact is missing:

| Fixture | Returns | Skips unless | For |
|---|---|---|---|
| `dev_catalog` / `full_catalog` | `Path` of `<tier>.duckdb` | the catalog file exists | EP-21+ warehouse tests |
| `raw_root` | `Path` of `source_root/mimic-iv-3.1` | that directory exists | EP-17..20 staging tests |
| `dev_ready` | factory: `dev_ready("<step>")` → the step's status entry | `lake/manifests/status.json` marks the step `dev_ready` (shape `{"steps": {"<step>": {"dev_ready": true}}}`) | EP-23/24/25 pipeline tests |
| `item_tier` | the test's **own** `tier` marker name | — (never skips) | dev/full tests that must not hard-code their tier |

Real data (dev/full) never appears in test output: dev/full tests assert on counts, schemas and
aggregates only (GOVERNANCE §4), and `raw_root`-style probes assert existence without opening a
file. Until EP-17 the only dev/full-marked tests are EP-12's two catalog probes and EP-168's
`raw_root` probe.

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
   (a README-/manifest-only change needs no bump). Shipped: **0.2.0** (EP-169: disjoint id
   floors + sort-key tie-breaks — D-27/D-17 addenda); next is **0.3.0** (EP-41 extends the
   vocab for its T2DM inputs — see `tests/fixtures/COVERAGE.md` for what the fixture does
   and does not cover).
4. Commit CSVs + `manifest.json` + `tests/fixtures/README.md` + the version bump in **one**
   commit; bundle generator tweaks so regenerations stay rare (a full regen adds < 1 MiB
   compressed to `.git`).

Downstream tests read counts from `manifest.json` / `fixtures.spec.build_plan()` / the schema
contract, never hard-coded literals (the churn rule above); byte identity is asserted against
the *locked* numpy/polars versions (deliberately unpinned — a lock bump that moves bytes is
handled as a regeneration, ledger FXT-2; the manifest records the versions that produced the
committed bytes, and the drift tests name them in their failure messages). The manifest pins
the contract by `contract_schema_hash` (`Contract.structural_hash()`, load-relevant facts
only); `contract_hash` and the version keys are provenance, ignored by the drift comparison —
a comment-only contract edit needs **no** regeneration (EP-169, ledger SCH-2/FC-4).

## Session fixtures (`tests/conftest.py`)

| Fixture | Value |
|---|---|
| `tier` | the selected maximum tier (`"fixture"` / `"dev"` / `"full"`) |
| `item_tier` | the requesting test's own tier marker name (function-scoped) |
| `contract` | the EP-9 schema contract (`load_contract()`) |
| `fixture_root` | `mimicwarehouse/tests/fixtures` (`fixtures.write.default_out_dir()`) |
| `fixture_catalog` | in-memory DuckDB with one table per hosp/icu contract table loaded from the fixture CSVs (`fixtures.catalog.build_fixture_catalog()`; contract types, comments); closed at session end |
| `dev_catalog` / `full_catalog` / `raw_root` / `dev_ready` | readiness fixtures — see the table above |

Markers registered: `ep_0` … `ep_199`, `tier(name, needs=…)`, `demo`. Hypothesis profiles:
`default` (50 examples) and `ci` (200), chosen with `HYPOTHESIS_PROFILE`. `pytester` is enabled
for marker-selection tests.

## Shared helpers (`tests/helpers.py`)

An importable module (not a plugin): `cli_runner()` (CliRunner with `COLUMNS=200`),
`tmp_data_root(monkeypatch, tmp_path)` (throw-away `MWH_DATA_ROOT`, cleared `MWH_*`/`PYTEST_*`
environment, rebuilt settings cache — call `config.configure()` again after the test),
`fresh_interpreter(argv)` (captured UTF-8 subprocess of `sys.executable`), plus the `WORKSPACE`
/ `REPO_ROOT` constants. Older `test_ep*.py` modules migrate onto these only when they are
being edited anyway — never as a wholesale rewrite.
