# EP-1 — Toolchain bootstrap (uv + CPython 3.13 + pyproject)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-0 (Baseline & hygiene) · **Blocks:** EP-2 (`mwh` CLI skeleton + `mwh doctor`), EP-5 (Visual identity), EP-7 (Re-plan P0), EP-8 (mimic-code vendoring), EP-121 (GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU))

## Context

Nothing runs yet: at planning `uv` was not installed, the only Python on PATH was the system
CPython 3.14 (`C:\Python314`, which must stay untouched), and `mimicwarehouse/` held design docs
only. D-13/D-14/D-15 fix the stack — Python throughout, native Windows (PowerShell + uv),
**uv-managed CPython 3.13, one venv** (`python-preference = "only-managed"`); dependency groups
`core / dev / ui / gpu / gpl / text` with `[tool.uv] conflicts` isolating `ui` because Streamlit
pins `pyarrow<25` (DECISIONS defaults; roadmap Risk 3); the cu130 PyTorch index declared now and
unused until EP-121 (D-16). This brief creates the nested uv project (`mimicwarehouse/pyproject.toml`,
`uv.lock`), the package skeleton, pytest with the `ep_<n>` marker convention, poethepoet tasks,
ruff + pyright, and a resolver/wheel smoke test that exercises the known traps (pandas 3 string
dtype / CoW / µs datetimes vs statsmodels and lifelines; DuckDB ↔ Polars ↔ pandas round-trip).
Machine: 64 GB RAM, one NVMe — the uv cache and `.venv` stay on C:, never G:/D:. Installing uv
(user-scope, no admin) is the one system change this brief may make (owner-approved by this
brief; CLAUDE.md §6). Commands run in `mimicwarehouse/` unless stated.

## In scope

1. **uv + managed Python.** `winget install --id astral-sh.uv -e` (fallback:
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`), reopen
   the shell, `uv --version`; `uv python install 3.13`; in `mimicwarehouse/`: `uv python pin 3.13`
   (writes `.python-version`). `uv cache dir` must be on C:.
2. **`mimicwarehouse/pyproject.toml`** (hatchling backend, src layout):
   - `[project]`: `name = "mimicwarehouse"`, `version = "0.1.0"`, `requires-python = ">=3.13,<3.14"`,
     `license = "MIT"`, `dependencies` = the **core** set: `duckdb==<current 1.5.x, pinned exactly>`,
     `polars`, `pyarrow`, `pandas>=3`, `numpy`, `scipy`, `statsmodels`, `lifelines`, `scikit-learn`,
     `pydantic>=2`, `pydantic-settings`, `typer`, `rich`, `pyyaml`, `jinja2`, `altair>=5.5`;
     `[project.scripts] mwh = "mimicwarehouse.cli:app"` (module lands in EP-2; until then
     `uv run mwh` fails with ImportError — expected).
   - `[dependency-groups]`: `dev` = pytest, pytest-xdist, hypothesis, ruff, pyright, poethepoet,
     pre-commit; `ui` = `streamlit==1.61.*`, vegafusion, `vl-convert-python`, plotly;
     `gpu = []`, `gpl = []`, `text = []` (kept empty with a comment naming EP-121, EP-93, EP-148
     as the fillers).
   - `[tool.uv]`: `package = true`, `python-preference = "only-managed"`, `default-groups = ["dev"]`,
     `conflicts = [[{group = "ui"}, {group = "gpu"}], [{group = "ui"}, {group = "text"}]]` (`ui`
     isolated from the heavy groups; `dev` stays co-installable with `ui` so page tests can run
     `--group dev --group ui`; if the installed uv rejects conflicts on still-empty groups, keep the
     lines commented with this exact content and record it for EP-7); `[[tool.uv.index]] name = "pytorch-cu130"`,
     `url = "https://download.pytorch.org/whl/cu130"`, `explicit = true`; a commented
     `[tool.uv.sources] torch = [{ index = "pytorch-cu130", marker = "sys_platform == 'win32'" }]`
     block for EP-121 to uncomment.
   - `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `addopts = "-ra --strict-markers"`,
     `xfail_strict = true`; `[tool.ruff]`: `line-length = 100`, `target-version = "py313"`,
     `lint.select = ["E","F","I","B","UP","SIM","RUF"]`; `[tool.pyright]`:
     `typeCheckingMode = "basic"`, `pythonVersion = "3.13"`, `venvPath = "."`, `venv = ".venv"`;
     `[tool.poe.tasks]`: `test = "pytest"`, `lint = "ruff check ."`, `fmt = "ruff format ."`,
     `typecheck = "pyright"`, `check = ["lint", "typecheck", "test"]`.
3. **Package skeleton + test scaffolding.** `src/mimicwarehouse/__init__.py` (`__version__` via
   `importlib.metadata`), `src/mimicwarehouse/py.typed`; `tests/conftest.py` registering markers
   `ep_0` … `ep_199` (`ep_<n>: acceptance tests of roadmap brief EP-<n>`) and a placeholder
   `tier(name)` marker (selection semantics arrive in EP-12), plus Hypothesis profiles
   (`default`: `deadline=None`, `max_examples=50`; `ci`: 200, chosen by `HYPOTHESIS_PROFILE`);
   `tests/ep/__init__.py`.
4. **Lock + wheel-availability check.** `uv lock`; then
   `uv sync --group dev --no-build --no-install-project` and
   `uv sync --group ui --no-build --no-install-project` must both succeed (every third-party
   dependency has a cp313 win_amd64 wheel — no sdist builds); then `uv sync --group dev`. Record
   `uv --version`, `uv run --group dev python -V` and the resolved versions of duckdb, polars, pyarrow,
   pandas, numpy, scipy, statsmodels, lifelines, scikit-learn, altair, streamlit, pytest, ruff,
   pyright (`uv tree --depth 1`) in the completion note; commit `uv.lock` and `.python-version`.
   First `uv run --group dev pyright` downloads a Node runtime into the user cache — allowed (user-scope).
5. **Smoke test `tests/ep/test_ep01.py`** (`@pytest.mark.ep_1`; synthetic numbers only, no ids of
   any kind): (a) `sys.version_info[:2] == (3, 13)` and `sys.executable` under `mimicwarehouse\.venv`;
   (b) `duckdb.__version__` equals the pin parsed from `pyproject.toml` (`tomllib`); (c) pandas ≥ 3:
   a `DataFrame` with a str column and a µs datetime column round-trips DuckDB → Polars
   (`duckdb.sql(...).pl()`) → pandas (`.to_pandas()`) with dtypes preserved; (d)
   `statsmodels.formula.api.ols("y ~ C(g) + x", data=df).fit()` on a 200-row synthetic frame whose
   `g` is the pandas-3 default str dtype; (e) `lifelines.KaplanMeierFitter().fit(durations,
   event_observed)` on synthetic exponential times; (f) `sklearn.linear_model.LogisticRegression().fit`
   on a synthetic (X, y); (g) `altair.Chart(pd.DataFrame(...)).mark_point().to_dict()` builds; (h)
   `pyproject.toml` declares the five groups and the `ui`↔`gpu`/`text` conflicts. Whole module < 5 s.
6. **Docs.** `mimicwarehouse/README.md` gains an "Install" section (uv install line, `uv sync
   --group dev`, `uv run poe test`); `DESIGN.md` gets dated notes under §6 (pinned DuckDB version)
   and §2 (uv/Python versions installed). `.gitignore` already covers `.venv/`, `.pytest_cache/`,
   `.ruff_cache/`, `.hypothesis/` — verify, do not edit.

## Out of scope

- `cli.py` / `mwh doctor` → EP-2; `Settings` / data root → EP-3; guard hook and
  `.pre-commit-config.yaml` → EP-4 (pre-commit is installed here, configured there).
- torch / xgboost (`gpu`) → EP-121; scikit-survival (`gpl`, GPL-3, D-34) → EP-93; medspaCy /
  sentence-transformers (`text`) → EP-148/150/151.
- Tier-marker selection (`--tier`) → EP-12; `mwh verify EP-n` → EP-6.
- DuckDB connection helpers → EP-3 (settings values), EP-17 / EP-21 (openers).

## Verification / acceptance

- `uv run --group dev python -c "import sys; print(sys.version)"` prints 3.13.x from
  `mimicwarehouse\.venv`; nothing was pip-installed into `C:\Python314`.
- `uv sync --group dev --no-build --no-install-project` and
  `uv sync --group ui --no-build --no-install-project` both exit 0.
- `uv run poe test -m ep_1` green; `uv run poe lint` and `uv run poe typecheck` green.
- `uv run --group ui python -c "import streamlit, pyarrow; print(streamlit.__version__, pyarrow.__version__)"`
  prints a 1.61.x Streamlit with pyarrow < 25; `uv run --group dev python -c "import pyarrow;
  print(pyarrow.__version__)"` prints the newest pyarrow the core set resolves to (may equal the ui
  one — record which in the completion note).
- `uv.lock`, `.python-version`, `pyproject.toml`, `tests/conftest.py`, `tests/ep/test_ep01.py`
  committed; completion note holds the versions table; `mwh verify EP-1` (available from EP-6) is
  green when EP-6 runs it.

## Parked → final-roadmap.md

- Split into a uv workspace (core package + app package, separate lockfiles) — trigger: the `ui`
  conflict set grows beyond `gpu`/`text`, or a page test needs `ui` and `gpu` together.
