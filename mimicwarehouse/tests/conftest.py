"""Shared pytest configuration for mimicwarehouse.

* Roadmap markers (EP-1): ``ep_<n>`` = acceptance tests of roadmap brief EP-<n>, so
  ``uv run poe test -m ep_1`` selects one brief's tests under ``--strict-markers``.
* Tier markers (EP-12, refined EP-168; DESIGN section 20):
  ``@pytest.mark.tier("fixture" | "dev" | "full", needs="catalog" | "raw" | "lake")`` names the
  data tier a test needs; unmarked tests are ``fixture``. ``--tier {fixture,dev,full}``
  (fallback: the ``PYTEST_TIER`` environment variable, then ``fixture``) selects the **maximum**
  tier to run - ``--tier dev`` runs fixture + dev tests, ``--tier full`` runs everything. Tests
  above the selected tier are **deselected**; ``dev`` / ``full`` tests inside it are **skipped
  with a reason** while the artefact they declare via ``needs=`` is missing (default
  ``catalog`` = ``get_settings().catalog_path(tier)``, EP-21; ``raw`` = the raw source dataset
  directory, EP-17..20; ``lake`` = ``lake/manifests/status.json``, EP-23+), so a fresh checkout
  is never red for lack of data. The ladder is deliberately the three-step subset
  ``fixture < dev < full`` of ``config.Tier`` and never reads ``settings.default_tier``; the
  options are not ``MWH_``-prefixed on purpose (a test knob is not a setting). See
  ``tests/README.md``.
* Demo marker (EP-168, retro ARCH-7/FC-2): ``@pytest.mark.demo`` is an **orthogonal opt-in**,
  never part of the tier ladder (``demo`` is a data tier for EP-22, not a test tier). Demo
  tests are deselected unless ``--with-demo`` (env fallback ``PYTEST_DEMO=1``) and skipped
  with a reason while the demo catalog is missing.
* Readiness fixtures (EP-168, retro VT-1/FC-1): ``dev_catalog`` / ``full_catalog`` /
  ``raw_root`` / ``dev_ready(step)`` skip the requesting test while their artefact is missing
  and return its path/entry; ``item_tier`` is the test's own tier marker name.
* Session fixtures: ``tier`` (the selected maximum tier), ``contract`` (the EP-9 schema
  contract), ``fixture_root`` (``tests/fixtures``), ``fixture_catalog`` (in-memory DuckDB over
  the committed fixture CSVs - one per contract table -
  :func:`mimicwarehouse.fixtures.catalog.build_fixture_catalog`).
* Shared helpers live in ``tests/helpers.py`` (importable module, not a plugin; EP-168).
* ``pytester`` is enabled (``pytest_plugins``) so marker-selection tests can run nested pytest
  sessions; the Hypothesis profiles are registered here too.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import settings as hypothesis_settings

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.schema.contract import Contract

pytest_plugins = ["pytester"]

# ---------------------------------------------------------------------------
# Markers & tiers
# ---------------------------------------------------------------------------

EP_MARKER_RANGE = range(0, 200)  # ep_0 … ep_199

#: The pytest tier ladder (a subset of ``mimicwarehouse.config.Tier``; ``demo`` is not a test tier).
TIERS: tuple[str, ...] = ("fixture", "dev", "full")
DEFAULT_TIER = "fixture"
TIER_ENV = "PYTEST_TIER"
TIER_OPTION = "--tier"
#: What a dev/full test may declare it needs (``tier(name, needs=...)``; default ``catalog``).
NEEDS: tuple[str, ...] = ("catalog", "raw", "lake")
DEFAULT_NEED = "catalog"
#: The raw dataset the ``raw`` need (and the ``raw_root`` fixture) checks for.
RAW_DATASET = "mimic-iv-3.1"
#: Demo opt-in (orthogonal to the ladder; EP-168).
DEMO_OPTION = "--with-demo"
DEMO_ENV = "PYTEST_DEMO"
#: The selected maximum tier / demo opt-in, resolved once in ``pytest_configure``.
TIER_KEY: pytest.StashKey[str] = pytest.StashKey()
DEMO_KEY: pytest.StashKey[bool] = pytest.StashKey()


def tier_rank(name: str) -> int:
    return TIERS.index(name)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        TIER_OPTION,
        action="store",
        default=None,
        choices=list(TIERS),
        metavar="TIER",
        help=(
            "maximum data tier to run: fixture (default; synthetic, always available), dev "
            "(adds tests marked tier('dev'); each skips while the artefact its needs= declares "
            "is missing) or full (adds tier('full')). Falls back to the "
            f"{TIER_ENV} environment variable."
        ),
    )
    parser.addoption(
        DEMO_OPTION,
        action="store_true",
        default=False,
        help=(
            "opt in the @pytest.mark.demo tests (ODbL demo tier, EP-22); they are deselected "
            f"otherwise and skipped while the demo catalog is missing. Env fallback: {DEMO_ENV}=1."
        ),
    )


def selected_tier(config: pytest.Config) -> str:
    """``--tier`` > ``PYTEST_TIER`` > ``fixture``; a bad env value is a usage error."""
    option = config.getoption(TIER_OPTION, default=None)
    if option:
        return str(option)
    env = os.environ.get(TIER_ENV, "").strip().lower()
    if not env:
        return DEFAULT_TIER
    if env not in TIERS:
        raise pytest.UsageError(
            f"{TIER_ENV}={env!r} is not a test tier; expected one of {', '.join(TIERS)}"
        )
    return env


def demo_opted_in(config: pytest.Config) -> bool:
    """``--with-demo`` > ``PYTEST_DEMO`` (1 = in, 0/unset = out; anything else is a usage
    error). Deliberately not ``MWH_``-prefixed, like ``PYTEST_TIER``."""
    if config.getoption(DEMO_OPTION, default=False):
        return True
    env = os.environ.get(DEMO_ENV, "").strip().lower()
    if not env or env == "0":
        return False
    if env == "1":
        return True
    raise pytest.UsageError(f"{DEMO_ENV}={env!r} is not a demo opt-in; set 1 (in) or 0/unset")


def tier_marker(item: pytest.Item) -> tuple[str, str]:
    """``(tier, need)`` of an item's closest ``tier`` marker (unmarked = fixture/catalog)."""
    marker = item.get_closest_marker("tier")
    if marker is None:
        return DEFAULT_TIER, DEFAULT_NEED
    if len(marker.args) != 1:
        raise pytest.UsageError(
            f"{item.nodeid}: tier marker takes exactly one positional name, "
            f"got args={marker.args!r}"
        )
    name = marker.args[0]
    if name not in TIERS:
        raise pytest.UsageError(
            f"{item.nodeid}: unknown tier {name!r}; expected one of {', '.join(TIERS)}"
        )
    unknown = sorted(set(marker.kwargs) - {"needs"})
    if unknown:
        raise pytest.UsageError(
            f"{item.nodeid}: unknown tier marker kwargs {unknown!r}; only needs= is accepted"
        )
    need = marker.kwargs.get("needs", DEFAULT_NEED)
    if need not in NEEDS:
        raise pytest.UsageError(
            f"{item.nodeid}: unknown needs {need!r}; expected one of {', '.join(NEEDS)}"
        )
    return str(name), str(need)


def tier_of(item: pytest.Item) -> str:
    """The tier a test item needs (closest ``tier`` marker; unmarked = fixture)."""
    return tier_marker(item)[0]


# ---------------------------------------------------------------------------
# Readiness probes (EP-168): ``(present, description)`` per artefact
# ---------------------------------------------------------------------------


def catalog_status(tier: str) -> tuple[bool, str]:
    """``(present, description)`` of the tier's catalog file; the fixture tier is always present
    (its catalog is built in memory from the committed CSVs)."""
    if tier == DEFAULT_TIER:
        return True, "in-memory fixture catalog"
    try:
        from mimicwarehouse.config import get_settings

        path = get_settings().catalog_path(tier)
    except Exception as exc:  # unsafe / unreadable settings: treat as absent, say why
        return False, f"settings unavailable ({type(exc).__name__}: {exc})"
    return path.is_file(), str(path)


def raw_status() -> tuple[bool, str]:
    """``(present, description)`` of the raw source dataset directory (EP-17..20 need it)."""
    try:
        from mimicwarehouse.config import get_settings
        from mimicwarehouse.inventory import dataset_dir

        path = get_settings().source_root / dataset_dir(RAW_DATASET)
    except Exception as exc:
        return False, f"settings unavailable ({type(exc).__name__}: {exc})"
    return path.is_dir(), str(path)


def lake_status() -> tuple[bool, str]:
    """``(present, description)`` of ``lake/manifests/status.json`` (EP-23+ write it)."""
    try:
        from mimicwarehouse.config import get_settings

        path = get_settings().layout["lake_manifests"] / "status.json"
    except Exception as exc:
        return False, f"settings unavailable ({type(exc).__name__}: {exc})"
    return path.is_file(), str(path)


def readiness(tier: str, need: str) -> tuple[bool, str]:
    """``(ready, skip-reason)`` for a marker-form dev/full test; the reason names the path."""
    if need == "raw":
        present, where = raw_status()
        detail = f"needs raw; not a directory ({where})"
    elif need == "lake":
        present, where = lake_status()
        detail = f"needs lake; status.json not found ({where})"
    else:
        present, where = catalog_status(tier)
        detail = f"needs catalog; not found ({where}); EP-21 builds it"
    if not present and where.startswith("settings unavailable"):
        return False, f"{tier} tier: {where}"  # the coarse skip: settings did not even load
    return present, f"{tier} tier: {detail}"


def pytest_configure(config: pytest.Config) -> None:
    for n in EP_MARKER_RANGE:
        config.addinivalue_line("markers", f"ep_{n}: acceptance tests of roadmap brief EP-{n}")
    config.addinivalue_line(
        "markers",
        "tier(name, needs='catalog'|'raw'|'lake'): data tier a test needs — 'fixture' "
        "(synthetic, the default for unmarked tests; always runs), 'dev' (5 % of subjects) or "
        f"'full'. {TIER_OPTION} / {TIER_ENV} select the maximum tier to run (fixture < dev < "
        "full); tests above it are deselected, tests inside it are skipped while the artefact "
        "they need is missing (needs= defaults to 'catalog' = <data_root>/warehouse/"
        "<tier>.duckdb; 'raw' = the raw source dataset dir; 'lake' = lake/manifests/status.json)",
    )
    config.addinivalue_line(
        "markers",
        "demo: opt-in test against the ODbL demo catalog (EP-22) — orthogonal to tier(...): "
        f"deselected unless {DEMO_OPTION} / {DEMO_ENV}=1, skipped while the demo catalog is "
        "missing",
    )
    # validate early so a bad PYTEST_TIER / PYTEST_DEMO fails before collection, not per item
    config.stash[TIER_KEY] = selected_tier(config)
    config.stash[DEMO_KEY] = demo_opted_in(config)


def max_tier_of(config: pytest.Config) -> str:
    return config.stash.get(TIER_KEY, None) or selected_tier(config)


def demo_of(config: pytest.Config) -> bool:
    return config.stash.get(DEMO_KEY, None) or False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    max_rank = tier_rank(max_tier_of(config))
    demo_on = demo_of(config)
    keep: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    ready_cache: dict[tuple[str, str], tuple[bool, str]] = {}
    demo_cache: tuple[bool, str] | None = None
    for item in items:
        is_demo = item.get_closest_marker("demo") is not None
        if is_demo and not demo_on:
            deselected.append(item)
            continue
        tier, need = tier_marker(item)
        if tier_rank(tier) > max_rank:
            deselected.append(item)
            continue
        if tier != DEFAULT_TIER:
            key = (tier, need)
            if key not in ready_cache:
                ready_cache[key] = readiness(tier, need)
            ready, reason = ready_cache[key]
            if not ready:
                item.add_marker(pytest.mark.skip(reason=reason))
        if is_demo:
            if demo_cache is None:
                demo_cache = catalog_status("demo")
            present, where = demo_cache
            if not present:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"demo: needs catalog; not found ({where}); EP-22 builds it"
                    )
                )
        keep.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = keep


def pytest_report_header(config: pytest.Config) -> list[str]:
    demo = "on" if demo_of(config) else "off"
    return [
        f"mimicwarehouse tier: {max_tier_of(config)} (max; {TIER_OPTION} / {TIER_ENV}); "
        f"demo tests {demo} ({DEMO_OPTION} / {DEMO_ENV})"
    ]


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tier(request: pytest.FixtureRequest) -> str:
    """The selected maximum tier of this session (``fixture`` / ``dev`` / ``full``)."""
    return max_tier_of(request.config)


@pytest.fixture
def item_tier(request: pytest.FixtureRequest) -> str:
    """The requesting test's **own** ``tier`` marker name (``fixture`` if unmarked) — so
    dev/full tests never hard-code their tier (EP-168)."""
    return tier_of(request.node)


@pytest.fixture(scope="session")
def contract() -> Contract:
    """The EP-9 schema contract (cached loader)."""
    from mimicwarehouse.schema.contract import load_contract

    return load_contract()


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    """``mimicwarehouse/tests/fixtures`` - the committed synthetic fixture tree."""
    from mimicwarehouse.fixtures.write import default_out_dir

    return default_out_dir()


@pytest.fixture(scope="session")
def fixture_catalog(fixture_root: Path, contract: Contract) -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory DuckDB over the committed fixture CSVs (one per contract table) with contract
    types (the ``fixture`` tier)."""
    from mimicwarehouse.fixtures.catalog import build_fixture_catalog

    con = build_fixture_catalog(fixture_root, contract=contract)
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Readiness fixtures (EP-168) — request exactly the artefact the test needs
# ---------------------------------------------------------------------------


def _catalog_or_skip(tier: str) -> Path:
    present, where = catalog_status(tier)
    if not present:
        pytest.skip(f"needs catalog; not found ({where}); EP-21 builds it")
    return Path(where)


@pytest.fixture(scope="session")
def dev_catalog() -> Path:
    """Path of ``dev.duckdb``; skips the requesting test while it is missing (EP-21+)."""
    return _catalog_or_skip("dev")


@pytest.fixture(scope="session")
def full_catalog() -> Path:
    """Path of ``full.duckdb``; skips the requesting test while it is missing (EP-21+)."""
    return _catalog_or_skip("full")


@pytest.fixture(scope="session")
def raw_root() -> Path:
    """``settings.source_root / <mimic-iv-3.1 dir>`` — what EP-17..20's dev tests need; skips
    while it is not a directory. Existence only: reading the data stays behind safe_query."""
    present, where = raw_status()
    if not present:
        pytest.skip(f"needs raw; not a directory ({where})")
    return Path(where)


@pytest.fixture(scope="session")
def dev_ready() -> Callable[[str], dict[str, Any]]:
    """Factory: ``dev_ready("<step>")`` skips unless ``lake/manifests/status.json`` marks the
    step ``dev_ready`` (shape ``{"steps": {"<step>": {"dev_ready": true, ...}}}``; EP-23/24/25
    write it) and returns the step's status entry."""

    def require(step: str) -> dict[str, Any]:
        present, where = lake_status()
        if not present:
            pytest.skip(f"needs lake; status.json not found ({where})")
        data = json.loads(Path(where).read_text(encoding="utf-8"))
        entry = data.get("steps", {}).get(step)
        if not isinstance(entry, dict) or not entry.get("dev_ready"):
            pytest.skip(f"step {step!r} is not dev_ready in {where}")
        return entry

    return require


# ---------------------------------------------------------------------------
# Hypothesis profiles — chosen by HYPOTHESIS_PROFILE (default: "default")
# ---------------------------------------------------------------------------

hypothesis_settings.register_profile("default", deadline=None, max_examples=50)
hypothesis_settings.register_profile("ci", deadline=None, max_examples=200)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

__all__: list[Any] = []
