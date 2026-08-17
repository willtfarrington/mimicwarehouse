"""EP-1 — Toolchain bootstrap smoke test.

Exercises the resolver/wheel traps named in the brief (pandas 3 string dtype / CoW /
microsecond datetimes vs statsmodels and lifelines; DuckDB <-> Polars <-> pandas
round-trip) on **synthetic numbers only** — no identifiers of any kind. The whole module
must run in < 5 s.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.ep_1

WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
PYPROJECT = WORKSPACE / "pyproject.toml"
UV_LOCK = WORKSPACE / "uv.lock"

# Pure-Python packages that have never published a wheel on PyPI; they build in well under
# a second with no compiler and are the only sdists tolerated by the wheel-availability
# check (brief item 4). Anything else without a wheel is a resolver trap to record.
SDIST_ONLY_ALLOWLIST = frozenset({"autograd-gamma"})  # lifelines transitive


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# (a) interpreter
# ---------------------------------------------------------------------------


def test_python_313_from_workspace_venv() -> None:
    assert sys.version_info[:2] == (3, 13), sys.version
    exe = Path(sys.executable).resolve()
    venv = (WORKSPACE / ".venv").resolve()
    assert exe.is_relative_to(venv), f"{exe} is not under {venv}"


# ---------------------------------------------------------------------------
# (b) duckdb pin
# ---------------------------------------------------------------------------


def test_duckdb_version_matches_pyproject_pin() -> None:
    import duckdb

    deps = _pyproject()["project"]["dependencies"]
    pins = [d for d in deps if d.startswith("duckdb==")]
    assert len(pins) == 1, f"duckdb must be pinned exactly once, got {pins}"
    pinned = pins[0].removeprefix("duckdb==").strip()
    assert duckdb.__version__ == pinned, (duckdb.__version__, pinned)


# ---------------------------------------------------------------------------
# (c) pandas 3 str + microsecond datetimes: DuckDB -> Polars -> pandas round-trip
# ---------------------------------------------------------------------------


def test_pandas3_roundtrip_duckdb_polars_pandas() -> None:
    import duckdb
    import polars as pl

    assert int(pd.__version__.split(".")[0]) >= 3, pd.__version__

    df = pd.DataFrame(
        {
            "k": np.arange(5, dtype=np.int64),
            "s": ["alpha", "beta", "gamma", "delta", "epsilon"],
            "t": pd.to_datetime(
                [
                    "2150-01-01 00:00:00.000001",
                    "2150-01-02 06:30:00.250000",
                    "2150-01-03 12:00:00.000000",
                    "2150-01-04 18:45:30.999999",
                    "2150-01-05 23:59:59.000001",
                ],
                format="ISO8601",
            ).as_unit("us"),
        }
    )
    # pandas-3 defaults: str dtype (not object) and microsecond datetimes.
    assert str(df["s"].dtype) == "str", df["s"].dtype
    assert str(df["t"].dtype) == "datetime64[us]", df["t"].dtype

    con = duckdb.connect()  # in-memory; synthetic data only
    try:
        rel = con.sql("SELECT k, s, t FROM df ORDER BY k")
        assert rel.types[1] == "VARCHAR" and rel.types[2] == "TIMESTAMP", rel.types
        pldf = rel.pl()
    finally:
        con.close()

    assert isinstance(pldf, pl.DataFrame)
    assert pldf.schema["s"] == pl.String
    assert pldf.schema["t"] == pl.Datetime("us")

    out = pldf.to_pandas()
    assert out["s"].dtype == df["s"].dtype, (out["s"].dtype, df["s"].dtype)
    assert out["t"].dtype == df["t"].dtype, (out["t"].dtype, df["t"].dtype)
    pd.testing.assert_frame_equal(out, df)


# ---------------------------------------------------------------------------
# (d) statsmodels formula OLS with a str-dtype categorical
# ---------------------------------------------------------------------------


def test_statsmodels_ols_formula_with_str_categorical() -> None:
    import statsmodels.formula.api as smf

    rng = np.random.default_rng(1)
    n = 200
    g = pd.Series(rng.choice(["a", "b", "c"], size=n))
    assert str(g.dtype) == "str"
    x = rng.normal(size=n)
    y = 1.0 + 0.5 * x + (g == "b") * 0.7 - (g == "c") * 0.3 + rng.normal(scale=0.1, size=n)
    df = pd.DataFrame({"y": y, "g": g, "x": x})

    fit = smf.ols("y ~ C(g) + x", data=df).fit()
    assert fit.nobs == n
    assert set(fit.params.index) == {"Intercept", "C(g)[T.b]", "C(g)[T.c]", "x"}
    assert fit.params["x"] == pytest.approx(0.5, abs=0.05)


# ---------------------------------------------------------------------------
# (e) lifelines Kaplan-Meier on synthetic exponential times
# ---------------------------------------------------------------------------


def test_lifelines_kaplan_meier() -> None:
    from lifelines import KaplanMeierFitter

    rng = np.random.default_rng(2)
    n = 300
    durations = rng.exponential(scale=10.0, size=n)
    censor = rng.exponential(scale=30.0, size=n)
    observed = durations <= censor
    durations = np.minimum(durations, censor)

    kmf = KaplanMeierFitter().fit(durations, event_observed=observed)
    assert kmf.event_observed.sum() == observed.sum()
    sf = kmf.survival_function_.iloc[:, 0]
    assert sf.iloc[0] == pytest.approx(1.0)
    assert sf.is_monotonic_decreasing
    assert 3.0 < kmf.median_survival_time_ < 20.0


# ---------------------------------------------------------------------------
# (f) scikit-learn logistic regression
# ---------------------------------------------------------------------------


def test_sklearn_logistic_regression() -> None:
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, 3))
    logits = 1.5 * X[:, 0] - 1.0 * X[:, 1]
    y = (rng.uniform(size=400) < 1 / (1 + np.exp(-logits))).astype(int)

    clf = LogisticRegression().fit(X, y)
    assert clf.coef_.shape == (1, 3)
    assert clf.coef_[0, 0] > 0 > clf.coef_[0, 1]
    assert clf.score(X, y) > 0.7


# ---------------------------------------------------------------------------
# (g) altair spec builds
# ---------------------------------------------------------------------------


def test_altair_chart_to_dict() -> None:
    import altair as alt

    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 8.0], "c": ["u", "v", "w"]})
    spec = alt.Chart(df).mark_point().encode(x="x:Q", y="y:Q", color="c:N").to_dict()
    assert spec["mark"] == {"type": "point"} or spec["mark"] == "point"
    assert spec["encoding"]["x"]["field"] == "x"
    assert "$schema" in spec


# ---------------------------------------------------------------------------
# (h) pyproject declares the five groups and the ui <-> gpu/text conflicts
# ---------------------------------------------------------------------------


def test_pyproject_groups_and_conflicts() -> None:
    py = _pyproject()
    groups = py["dependency-groups"]
    assert set(groups) == {"dev", "ui", "gpu", "gpl", "text"}, set(groups)
    assert groups["gpu"] == [] and groups["gpl"] == [] and groups["text"] == []
    assert any(d.startswith("streamlit==1.61") for d in groups["ui"]), groups["ui"]

    uv = py["tool"]["uv"]
    assert uv["package"] is True
    assert uv["python-preference"] == "only-managed"
    assert uv["default-groups"] == ["dev"]
    conflict_sets = {frozenset(item["group"] for item in cs) for cs in uv["conflicts"]}
    assert conflict_sets == {frozenset({"ui", "gpu"}), frozenset({"ui", "text"})}

    indexes = {ix["name"]: ix for ix in py["tool"]["uv"]["index"]}
    assert indexes["pytorch-cu130"]["url"] == "https://download.pytorch.org/whl/cu130"
    assert indexes["pytorch-cu130"]["explicit"] is True

    assert py["project"]["scripts"]["mwh"] == "mimicwarehouse.cli:app"
    assert py["project"]["requires-python"] == ">=3.13,<3.14"


# ---------------------------------------------------------------------------
# (i) every locked third-party package ships a wheel this interpreter can install
#     (durable form of `uv sync --no-build --no-install-project`)
# ---------------------------------------------------------------------------


def test_uv_lock_every_package_has_a_wheel_for_this_interpreter() -> None:
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    with UV_LOCK.open("rb") as fh:
        lock = tomllib.load(fh)

    supported = set(sys_tags())
    missing: list[str] = []
    for pkg in lock["package"]:
        name = pkg["name"]
        if name == "mimicwarehouse" or name in SDIST_ONLY_ALLOWLIST:
            continue
        # Only wheels whose filename encodes a compatible tag count. Wheels for other
        # platforms/interpreters in the universal lock are irrelevant here.
        ok = False
        for wheel in pkg.get("wheels", []):
            filename = wheel["url"].rsplit("/", 1)[-1]
            try:
                _, _, _, tags = parse_wheel_filename(filename)
            except Exception:  # malformed filename counts as unusable
                continue
            if tags & supported:
                ok = True
                break
        if not ok:
            missing.append(f"{name}=={pkg['version']}")

    assert not missing, f"no cp313/win_amd64-compatible wheel for: {missing}"


# ---------------------------------------------------------------------------
# conftest scaffolding: ep_<n> markers registered, hypothesis profile active
# ---------------------------------------------------------------------------


def test_conftest_registers_markers_and_hypothesis_profile(request: pytest.FixtureRequest) -> None:
    from hypothesis import settings as hs

    markers = "\n".join(request.config.getini("markers"))
    assert "ep_0:" in markers and "ep_1:" in markers and "ep_199:" in markers
    assert "tier(name):" in markers
    active = hs()  # a settings object inheriting the currently loaded profile
    assert active.max_examples in (50, 200)
    assert active.deadline is None
