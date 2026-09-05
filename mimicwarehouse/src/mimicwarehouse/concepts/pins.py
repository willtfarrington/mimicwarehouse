"""Concept count-pins (EP-37 item 4; GOVERNANCE §3/§5, D-33).

A pin set is a small JSON document of released aggregates over the ``mimiciv_derived``
tables of one tier — the row count of every concept table, the ``sepsis3 = true`` count,
the per-stay maximum KDIGO stage distribution and the Charlson index mean — computed
**only through** :func:`mimicwarehouse.safe.safe_query` (aggregate-only, ``k = 11``
row-wise suppression, audited). A suppressed cell (a count in ``1 .. k-1`` drops out of
the released frame) is stored as the string :func:`render_pin` returns (``"<11"``); a
zero stays ``0``. That one-line helper is what EP-43 replaces with
``disclose.render_cell()`` — EP-38 / EP-43 switch the pin writer to it. Means are computed
by the engine and rounded here (2 dp); ratios would be computed in Python from released
counts (arithmetic over aggregates is refused by the gate, final-roadmap DIS-3).

Two pin files: ``tests/ep/pins/concepts_demo.json`` (committed — the demo tier is ODbL
and every cell is suppressed or zero; ``test_ep37`` rebuilds the demo concepts and asserts
equality) and ``<data_root>/runs/pins/concepts_dev.json`` (**never committed**: written on
the first dev run, compared on later ones — a drift detector that precedes EP-43's
disclosure gate). Everything here is released aggregates and audit ids — never a row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mimicwarehouse.concepts.inventory import DERIVED_SCHEMA, load_inventory
from mimicwarehouse.config import Settings, get_settings, workspace_root

#: Pin-set keys compared by :func:`compare_pins` (the rest is provenance).
COMPARED_KEYS: tuple[str, ...] = (
    "tier",
    "k",
    "upstream_commit",
    "row_counts",
    "sepsis3_true",
    "kdigo_max_stage",
    "kdigo_stages_suppressed",
    "charlson_mean_index",
    "charlson_n",
)
DEMO_PINS_RELPATH = Path("tests") / "ep" / "pins" / "concepts_demo.json"
DEV_PINS_RELPATH = Path("pins") / "concepts_dev.json"
MEAN_DECIMALS = 2


def render_pin(value: int | float | None, k: int) -> int | float | str:
    """A released cell: the value itself, or ``"<k"`` for a suppressed one (``None`` — the
    row dropped out of the released frame — or a count in ``1 .. k-1``). EP-43 replaces
    this with ``disclose.render_cell()``."""
    if value is None or (isinstance(value, int) and 0 < value < k):
        return f"<{k}"
    return value


def row_count_sql(names: Iterable[str]) -> str:
    """One statement: ``UNION ALL`` of ``SELECT '<name>' AS concept, count(*) AS n FROM
    mimiciv_derived.<name>`` per concept (EP-33 B1: set operations verify per branch)."""
    branches = [
        f"SELECT '{name}' AS concept, count(*) AS n FROM {DERIVED_SCHEMA}.{name}" for name in names
    ]
    if not branches:
        raise ValueError("row_count_sql needs at least one concept name")
    return "\nUNION ALL\n".join(branches)


PRESENT_SQL = (
    "SELECT table_name FROM information_schema.tables "
    f"WHERE table_schema = '{DERIVED_SCHEMA}' ORDER BY 1"
)
SEPSIS3_SQL = f"SELECT count(*) AS n FROM {DERIVED_SCHEMA}.sepsis3 WHERE sepsis3"
KDIGO_SQL = (
    "SELECT max_stage, count(*) AS n FROM ("
    f"SELECT stay_id, max(aki_stage) AS max_stage FROM {DERIVED_SCHEMA}.kdigo_stages "
    "GROUP BY stay_id) GROUP BY 1 ORDER BY 1"
)
CHARLSON_SQL = (
    f"SELECT count(*) AS n, avg(charlson_comorbidity_index) AS mean_index "
    f"FROM {DERIVED_SCHEMA}.charlson"
)


def compute_pins(
    tier: str, settings: Settings | None = None, *, k: int | None = None
) -> dict[str, Any]:
    """The pin set of ``tier`` (module docstring), every number through ``safe_query``.
    Concepts without a view in the catalog (not built on this tier) are pinned ``null``."""
    from mimicwarehouse.safe import safe_query

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    inv = load_inventory()
    audit: list[str] = []

    present_result = safe_query(PRESENT_SQL, tier=tier, k=resolved_k, settings=settings)
    audit.append(present_result.audit_id)
    present = set(present_result.df.get_column("table_name").to_list())
    names = [c.name for c in inv.concepts if c.name in present]

    row_counts: dict[str, Any] = {c.name: None for c in inv.concepts}
    if names:
        counts = safe_query(
            row_count_sql(names), tier=tier, k=resolved_k, row_cap=500, settings=settings
        )
        audit.append(counts.audit_id)
        released = dict(zip(counts.df["concept"].to_list(), counts.df["n"].to_list(), strict=True))
        for name in names:
            value = released.get(name)
            row_counts[name] = render_pin(None if value is None else int(value), resolved_k)

    pins: dict[str, Any] = {
        "generated_by": "mimicwarehouse.concepts.pins (EP-37; safe_query, k-suppressed)",
        "tier": tier,
        "k": resolved_k,
        "upstream_commit": inv.upstream_commit,
        "row_counts": row_counts,
        "sepsis3_true": None,
        "kdigo_max_stage": None,
        "kdigo_stages_suppressed": None,
        "charlson_n": None,
        "charlson_mean_index": None,
    }
    if "sepsis3" in present:
        res = safe_query(SEPSIS3_SQL, tier=tier, k=resolved_k, settings=settings)
        audit.append(res.audit_id)
        value = int(res.df["n"][0]) if res.n_rows else None
        pins["sepsis3_true"] = render_pin(value, resolved_k)
    if "kdigo_stages" in present:
        res = safe_query(KDIGO_SQL, tier=tier, k=resolved_k, settings=settings)
        audit.append(res.audit_id)
        pins["kdigo_max_stage"] = {
            str(stage): render_pin(int(n), resolved_k)
            for stage, n in zip(res.df["max_stage"].to_list(), res.df["n"].to_list(), strict=True)
        }
        pins["kdigo_stages_suppressed"] = res.rows_suppressed
    if "charlson" in present:
        res = safe_query(CHARLSON_SQL, tier=tier, k=resolved_k, settings=settings)
        audit.append(res.audit_id)
        if res.n_rows:
            pins["charlson_n"] = render_pin(int(res.df["n"][0]), resolved_k)
            mean = res.df["mean_index"][0]
            pins["charlson_mean_index"] = (
                None if mean is None else round(float(mean), MEAN_DECIMALS)
            )
        else:
            pins["charlson_n"] = render_pin(None, resolved_k)
            pins["charlson_mean_index"] = render_pin(None, resolved_k)
    pins["audit_ids"] = audit
    return pins


def compare_pins(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Human-readable differences over :data:`COMPARED_KEYS` (empty = no drift)."""
    diffs: list[str] = []
    for key in COMPARED_KEYS:
        want, have = expected.get(key), actual.get(key)
        if isinstance(want, dict) and isinstance(have, dict):
            for sub in sorted(set(want) | set(have)):
                if want.get(sub) != have.get(sub):
                    diffs.append(f"{key}.{sub}: pinned {want.get(sub)!r}, now {have.get(sub)!r}")
        elif want != have:
            diffs.append(f"{key}: pinned {want!r}, now {have!r}")
    return diffs


def write_pins(path: Path, pins: dict[str, Any]) -> Path:
    """Write a pin set (sorted keys, LF) — released aggregates only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def read_pins(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a pin set is a JSON object")
    return data


def demo_pins_path() -> Path:
    """``mimicwarehouse/tests/ep/pins/concepts_demo.json`` (committed)."""
    return workspace_root() / DEMO_PINS_RELPATH


def dev_pins_path(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/pins/concepts_dev.json`` (never committed)."""
    return (settings or get_settings()).layout["runs"] / DEV_PINS_RELPATH


def check_or_write_dev_pins(settings: Settings | None = None) -> tuple[Path, list[str], bool]:
    """The dev drift detector: compute the dev pins; write them when the file is absent
    (``created = True``), else return the differences against the stored set."""
    settings = settings or get_settings()
    path = dev_pins_path(settings)
    actual = compute_pins("dev", settings)
    if not path.is_file():
        write_pins(path, actual)
        return path, [], True
    return path, compare_pins(read_pins(path), actual), False


__all__ = [
    "CHARLSON_SQL",
    "COMPARED_KEYS",
    "DEMO_PINS_RELPATH",
    "DEV_PINS_RELPATH",
    "KDIGO_SQL",
    "MEAN_DECIMALS",
    "PRESENT_SQL",
    "SEPSIS3_SQL",
    "check_or_write_dev_pins",
    "compare_pins",
    "compute_pins",
    "demo_pins_path",
    "dev_pins_path",
    "read_pins",
    "render_pin",
    "row_count_sql",
    "write_pins",
]
