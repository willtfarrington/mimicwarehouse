"""Concept count-pins (EP-37 item 4; GOVERNANCE §3/§5, D-33).

A *pin file* records, for one tier, what the concept layer counts: the row count of every
``mimiciv_derived`` concept view, the ``sepsis3`` true count, the KDIGO max-stage
distribution (stays by their maximum ``aki_stage``) and the mean Charlson index (2 dp,
computed in Python from the released aggregate — arithmetic over aggregates stays refused
in ``safe_query``, DIS-3). Every number is read through
:func:`mimicwarehouse.safe.safe_query` (audited; the per-concept counts are **one** set
operation statement, EP-33 B1) and every count below the small-cell threshold is stored as
the string ``"<11"`` — :func:`render_cell` is the one-line helper EP-43 replaces with
``disclose.render_cell()`` (EP-38 / EP-43 switch the writer to it). On the ``fixture`` /
``demo`` tiers the query runs with ``k = 1`` and the helper suppresses afterwards; on
``dev`` / ``full`` the gate keeps ``k = 11`` and a suppressed (dropped) row *is* the
``"<11"`` cell.

Two files: ``tests/ep/pins/concepts_demo.json`` (committed — the demo is ODbL and the
cells are suppressed) and ``<data_root>/runs/pins/concepts_dev.json`` (written on the
first dev run, compared on later ones — a drift detector, **not** committed because it
precedes EP-43's disclosure gate). :func:`write_or_compare` implements both.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mimicwarehouse.concepts.inventory import DERIVED_SCHEMA, Inventory, load_inventory
from mimicwarehouse.config import Settings, get_settings

#: The small-cell threshold (GOVERNANCE §5; ``Settings.k_suppression`` defaults to it).
K = 11
SUPPRESSED = "<11"
PINS_DIRNAME = "pins"
DEMO_PINS_FILENAME = "concepts_demo.json"
DEV_PINS_FILENAME = "concepts_dev.json"
ACTOR = "concepts.pins"
GENERATED_BY = "mimicwarehouse.concepts.pins (EP-37)"


def render_cell(n: int | None, k: int = K) -> int | str | None:
    """A count as it may be committed: ``None`` stays ``None``, ``0`` stays ``0`` (no
    individual behind an empty cell — the same rule as ``safe.rowwise_suppress``),
    ``1 .. k-1`` becomes :data:`SUPPRESSED`, ``k`` and above is released. EP-43 replaces
    this helper with ``disclose.render_cell()``."""
    if n is None:
        return None
    if 0 < n < k:
        return SUPPRESSED
    return int(n)


def demo_pins_path() -> Path:
    """``mimicwarehouse/tests/ep/pins/concepts_demo.json`` (committed)."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / "tests" / "ep" / PINS_DIRNAME / DEMO_PINS_FILENAME


def dev_pins_path(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/pins/concepts_dev.json`` (never committed)."""
    settings = settings or get_settings()
    return settings.layout["runs"] / PINS_DIRNAME / DEV_PINS_FILENAME


# ---------------------------------------------------------------------------
# Computing
# ---------------------------------------------------------------------------


def _q(sql: str, tier: str, k: int, settings: Settings) -> Any:
    from mimicwarehouse.safe import safe_query

    return safe_query(sql, tier=tier, k=k, actor=ACTOR, settings=settings, row_cap=500)


def raw_k(tier: str) -> int:
    """The ``k`` the pin queries run with: 1 on the synthetic / ODbL tiers (the helper
    suppresses afterwards), the :data:`K` floor on the credentialed ones."""
    return 1 if tier in ("fixture", "demo") else K


def present_concepts(
    tier: str, settings: Settings, inventory: Inventory | None = None
) -> list[str]:
    """The inventory concepts that are views in the tier catalog (registry read)."""
    inventory = inventory or load_inventory()
    result = _q(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{DERIVED_SCHEMA}' AND table_type = 'VIEW' ORDER BY 1",
        tier,
        raw_k(tier),
        settings,
    )
    present = set(result.df["table_name"].to_list())
    return [c.name for c in inventory.concepts if c.name in present]


def counts_statement(names: list[str]) -> str:
    """One ``UNION ALL`` over the concept views: ``(concept, n)`` per branch."""
    return "\nUNION ALL\n".join(
        f"SELECT '{name}' AS concept, count(*) AS n FROM {DERIVED_SCHEMA}.{name}" for name in names
    )


def compute_pins(tier: str, settings: Settings | None = None) -> dict[str, Any]:
    """The pin document for ``tier`` (module docstring). Missing concept views yield no
    ``counts`` entry (a later comparison reports them); a missing ``sepsis3`` /
    ``kdigo_stages`` / ``charlson`` view leaves its pin ``None``."""
    settings = settings or get_settings()
    inventory = load_inventory()
    k = raw_k(tier)
    names = present_concepts(tier, settings, inventory)
    counts: dict[str, int | str | None] = {}
    if names:
        result = _q(counts_statement(names), tier, k, settings)
        released = dict(zip(result.df["concept"].to_list(), result.df["n"].to_list(), strict=True))
        for name in names:
            # a row the k = 11 gate dropped (dev/full) is exactly a suppressed cell
            counts[name] = render_cell(released[name]) if name in released else SUPPRESSED

    sepsis3: int | str | None = None
    if "sepsis3" in names:
        r = _q(
            f"SELECT count(*) AS n FROM {DERIVED_SCHEMA}.sepsis3 WHERE sepsis3",
            tier,
            k,
            settings,
        )
        sepsis3 = render_cell(int(r.df["n"][0])) if r.n_rows else SUPPRESSED

    kdigo: dict[str, int | str | None] | None = None
    if "kdigo_stages" in names:
        r = _q(
            "SELECT max_stage, count(*) AS n FROM (SELECT stay_id, max(aki_stage) AS max_stage "
            f"FROM {DERIVED_SCHEMA}.kdigo_stages GROUP BY stay_id) GROUP BY 1 ORDER BY 1",
            tier,
            k,
            settings,
        )
        kdigo = {
            ("null" if stage is None else str(int(stage))): render_cell(int(n))
            for stage, n in zip(r.df["max_stage"].to_list(), r.df["n"].to_list(), strict=True)
        }

    charlson: dict[str, Any] | None = None
    if "charlson" in names:
        r = _q(
            f"SELECT avg(charlson_comorbidity_index) AS mean_index, count(*) AS n "
            f"FROM {DERIVED_SCHEMA}.charlson",
            tier,
            k,
            settings,
        )
        if r.n_rows:
            n = int(r.df["n"][0])
            mean = r.df["mean_index"][0]
            charlson = {
                "n": render_cell(n),
                "mean_index": None if mean is None or 0 < n < K else round(float(mean), 2),
            }
        else:
            charlson = {"n": SUPPRESSED, "mean_index": None}

    return {
        "generated_by": GENERATED_BY,
        "tier": tier,
        "upstream_commit": inventory.upstream_commit,
        "k": K,
        "concepts": len(names),
        "counts": counts,
        "sepsis3_true": sepsis3,
        "kdigo_max_stage": kdigo,
        "charlson": charlson,
    }


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

_COMPARED_KEYS = ("upstream_commit", "counts", "sepsis3_true", "kdigo_max_stage", "charlson")


def write_pins(pins: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(pins, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def read_pins(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_pins(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    """Differences between two pin documents on the compared keys (empty = equal)."""
    out: list[str] = []
    for key in _COMPARED_KEYS:
        a, e = actual.get(key), expected.get(key)
        if key == "counts":
            a_counts, e_counts = dict(a or {}), dict(e or {})
            for name in sorted(set(a_counts) | set(e_counts)):
                if a_counts.get(name, "missing") != e_counts.get(name, "missing"):
                    out.append(
                        f"counts.{name}: {a_counts.get(name, 'missing')} != "
                        f"{e_counts.get(name, 'missing')}"
                    )
        elif a != e:
            out.append(f"{key}: {a!r} != {e!r}")
    return out


def write_or_compare(
    tier: str, path: Path, settings: Settings | None = None
) -> tuple[dict[str, Any], list[str], bool]:
    """Compute the pins for ``tier``; write them to ``path`` when it does not exist
    (``created`` True), else compare (``diffs``). Returns ``(pins, diffs, created)``."""
    pins = compute_pins(tier, settings)
    if not path.is_file():
        write_pins(pins, path)
        return pins, [], True
    return pins, compare_pins(pins, read_pins(path)), False


__all__ = [
    "ACTOR",
    "DEMO_PINS_FILENAME",
    "DEV_PINS_FILENAME",
    "GENERATED_BY",
    "PINS_DIRNAME",
    "SUPPRESSED",
    "K",
    "compare_pins",
    "compute_pins",
    "counts_statement",
    "demo_pins_path",
    "dev_pins_path",
    "present_concepts",
    "read_pins",
    "render_cell",
    "write_or_compare",
    "write_pins",
]
