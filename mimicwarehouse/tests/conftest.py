"""Shared pytest configuration for mimicwarehouse.

* Roadmap markers (EP-1): ``ep_<n>`` = acceptance tests of roadmap brief EP-<n>, so
  ``uv run poe test -m ep_1`` selects one brief's tests under ``--strict-markers``.
* Tier markers (EP-12, DESIGN section 20): ``@pytest.mark.tier("fixture" | "dev" | "full")``
  names the data tier a test needs; unmarked tests are ``fixture``. ``--tier {fixture,dev,full}``
  (fallback: the ``PYTEST_TIER`` environment variable, then ``fixture``) selects the **maximum**
  tier to run - ``--tier dev`` runs fixture + dev tests, ``--tier full`` runs everything. Tests
  above the selected tier are **deselected**; ``dev`` / ``full`` tests inside it are **skipped
  with a reason** when that tier's catalog (``get_settings().catalog_path(tier)``, EP-21) does
  not exist yet, so a fresh checkout is never red for lack of data. The ladder is deliberately
  the three-step subset ``fixture < dev < full`` of ``config.Tier`` (``demo`` is a data tier for
  EP-22, never a test tier) and never reads ``settings.default_tier``; the option is not
  ``MWH_``-prefixed on purpose (``Settings`` is ``extra="forbid"`` on that prefix). See
  ``tests/README.md``.
* Session fixtures: ``tier`` (the selected maximum tier), ``contract`` (the EP-9 schema
  contract), ``fixture_root`` (``tests/fixtures``), ``fixture_catalog`` (in-memory DuckDB over
  the 31 fixture CSVs, :func:`mimicwarehouse.fixtures.catalog.build_fixture_catalog`).
* ``pytester`` is enabled (``pytest_plugins``) so marker-selection tests can run nested pytest
  sessions; the Hypothesis profiles are registered here too.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
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
#: The selected maximum tier, resolved once in ``pytest_configure``.
TIER_KEY: pytest.StashKey[str] = pytest.StashKey()


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
            "(adds tests marked tier('dev'); skipped while dev.duckdb is missing) or full (adds "
            f"tier('full')). Falls back to the {TIER_ENV} environment variable."
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


def tier_of(item: pytest.Item) -> str:
    """The tier a test item needs (closest ``tier`` marker; unmarked = fixture)."""
    marker = item.get_closest_marker("tier")
    if marker is None:
        return DEFAULT_TIER
    if len(marker.args) != 1 or marker.kwargs:
        raise pytest.UsageError(
            f"{item.nodeid}: tier marker takes exactly one positional name, "
            f"got args={marker.args!r} kwargs={marker.kwargs!r}"
        )
    name = marker.args[0]
    if name not in TIERS:
        raise pytest.UsageError(
            f"{item.nodeid}: unknown tier {name!r}; expected one of {', '.join(TIERS)}"
        )
    return str(name)


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


def pytest_configure(config: pytest.Config) -> None:
    for n in EP_MARKER_RANGE:
        config.addinivalue_line("markers", f"ep_{n}: acceptance tests of roadmap brief EP-{n}")
    config.addinivalue_line(
        "markers",
        "tier(name): data tier a test needs — 'fixture' (synthetic, the default for unmarked "
        "tests; always runs), 'dev' (5 % of subjects, dev.duckdb) or 'full' (full.duckdb). "
        f"{TIER_OPTION} / {TIER_ENV} select the maximum tier to run (fixture < dev < full); "
        "tests above it are deselected, tests whose catalog is missing are skipped",
    )
    # validate early so a bad PYTEST_TIER fails before collection, not per item
    config.stash[TIER_KEY] = selected_tier(config)


def max_tier_of(config: pytest.Config) -> str:
    return config.stash.get(TIER_KEY, None) or selected_tier(config)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    max_tier = max_tier_of(config)
    max_rank = tier_rank(max_tier)
    keep: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    catalog_cache: dict[str, tuple[bool, str]] = {}
    for item in items:
        tier = tier_of(item)
        if tier_rank(tier) > max_rank:
            deselected.append(item)
            continue
        if tier != DEFAULT_TIER:
            if tier not in catalog_cache:
                catalog_cache[tier] = catalog_status(tier)
            present, where = catalog_cache[tier]
            if not present:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"{tier} tier: catalog not found ({where}); EP-21 builds it"
                    )
                )
        keep.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = keep


def pytest_report_header(config: pytest.Config) -> list[str]:
    return [f"mimicwarehouse tier: {max_tier_of(config)} (max; {TIER_OPTION} / {TIER_ENV})"]


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tier(request: pytest.FixtureRequest) -> str:
    """The selected maximum tier of this session (``fixture`` / ``dev`` / ``full``)."""
    return max_tier_of(request.config)


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
    """In-memory DuckDB over the 31 fixture CSVs with contract types (the ``fixture`` tier)."""
    from mimicwarehouse.fixtures.catalog import build_fixture_catalog

    con = build_fixture_catalog(fixture_root, contract=contract)
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Hypothesis profiles — chosen by HYPOTHESIS_PROFILE (default: "default")
# ---------------------------------------------------------------------------

hypothesis_settings.register_profile("default", deadline=None, max_examples=50)
hypothesis_settings.register_profile("ci", deadline=None, max_examples=200)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

__all__: list[Any] = []
