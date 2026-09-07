"""EP-39 — itemid dictionary curation + unit harmonization.

Fixture tier (default): the packaged catalogue validates (>= 40 curated itemids, the
brief's named vitals / labs / urine items among them, unique ids, ``plausible_low <
plausible_high``, the canonical unit accepted by identity, one canonical unit per concept
group) and crafted catalogues are refused per violation; every named formula round-trips
within 1e-9; unit strings normalise identically in Python, DuckDB and Polars; crafted
synthetic rows in mixed units harmonise to the canonical unit through the scalar, the
Polars and the SQL-macro twins, out-of-bounds values flag and unknown units pass through
flagged; the DAG spec and the catalog hook are wired; the session fixture lake carries
``meta.item_units`` / ``meta.item_unit_variants`` / ``meta.item_dictionary`` and the
``mwh_harmonize`` macro (no released cell below k, shares over released rows, raw counts
outside the catalog); the report and its CLI; the docs page is in sync; the import budget.
``tier("dev")``: the real catalog's tables through ``safe_query`` (labels equal the real
dictionary, every core itemid has a variant row, nothing below k released).

Everything asserted or printed is dictionary text, unit strings, suppressed counts and
crafted synthetic values — never a row.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pytest
import yaml

import helpers
from mimicwarehouse import config, units
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.cli import app
from mimicwarehouse.dag import spec as spec_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.units import (
    FORMULAS,
    ITEM_UNITS_COLUMNS,
    REPORT_COLUMNS,
    VARIANTS_COLUMNS,
    Affine,
    ItemCatalogue,
    UnitsError,
    UnknownItemError,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_39

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")

HR, TEMP_F, TEMP_C, WEIGHT_LB, HEIGHT_IN, FIO2 = 220045, 223761, 223762, 226531, 226707, 223835
CREATININE, GLUCOSE_LAB, INR, FOLEY = 50912, 50931, 51237, 226559
#: The brief's item-1 list (vitals, weight / height, GCS, FiO2, labs, urine) by itemid.
BRIEF_ITEMS: frozenset[int] = frozenset(
    {
        220045, 220179, 220180, 220181, 220050, 220051, 220052, 220210, 220277,
        223761, 223762, 226512, 224639, 226531, 226730, 226707, 220739, 223900, 223901,
        223835, 50912, 51006, 50983, 50971, 50902, 50882, 50931, 50809, 50813, 51301,
        51222, 51221, 51265, 51237, 50820, 50821, 50818, 50885, 50862, 51003, 50960,
        50893, 50970, 50852, 226559, 226560,
    }
)  # fmt: skip
#: Spellings of the Fahrenheit unit that must all read as one key.
F_SPELLINGS = ("F", "degF", "deg F", "DegF.", "degrees F", "°F", "ºF", " f ")


@pytest.fixture(scope="module")
def catalogue() -> ItemCatalogue:
    return units.load_catalogue()


def _close(a: float | None, b: float | None, tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=tol)


# ---------------------------------------------------------------------------
# 1. The catalogue validates; crafted catalogues are refused
# ---------------------------------------------------------------------------


def test_catalogue_validates_and_covers_the_brief(catalogue: ItemCatalogue) -> None:
    ids = catalogue.itemids()
    assert len(ids) == len(set(ids)) == len(catalogue) >= units.MIN_CURATED_ITEMS
    assert set(ids) >= BRIEF_ITEMS, sorted(BRIEF_ITEMS - set(ids))
    groups: dict[str, str] = {}
    for item in catalogue.items:
        assert item.plausible_low < item.plausible_high
        assert item.source in units.SOURCES and item.source_ref and item.label
        assert item.accepts(item.canonical_unit)
        canonical = item.conversion(item.canonical_unit)
        assert canonical is not None and canonical.is_identity
        for conversion in item.accepted_units.values():
            if isinstance(conversion, str):
                assert conversion in FORMULAS
        assert groups.setdefault(item.concept_group, item.canonical_norm) == item.canonical_norm
    assert len(catalogue.concept_groups) == len(groups)
    assert units.spec(HR) is units.load_catalogue().spec(HR)
    assert units.bounds(HR) == (0.0, 300.0)
    assert units.is_curated(HR) and not units.is_curated(999_999)
    with pytest.raises(UnknownItemError):
        units.bounds(999_999)
    labs = units.curated_itemids("labevents")
    assert labs == tuple(sorted(labs)) and CREATININE in labs and HR not in labs
    assert set(units.curated_itemids()) == set(ids)
    # the temperature and the pounds / inches items carry a real conversion
    assert not units.spec(TEMP_F).conversion("degF").is_identity  # type: ignore[union-attr]
    assert units.spec(WEIGHT_LB).conversion(None) == FORMULAS["lb_to_kg"]
    assert units.spec(HEIGHT_IN).conversion("Inch") == FORMULAS["in_to_cm"]
    assert units.spec(INR).accepts(None) and units.spec(INR).accepts("")
    # package-data hygiene: ASCII, LF, no band-shaped integers
    raw = units.catalogue_path().read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    rows = units.item_units_rows(catalogue)
    assert len(rows) == sum(len(i.accepted_units) for i in catalogue.items)
    assert {r[7] for r in rows} <= set(FORMULAS) | {units.FACTOR}


def _base_item(catalogue: ItemCatalogue) -> dict[str, Any]:
    return catalogue.spec(TEMP_F).model_dump(mode="json")


def _catalogue_doc(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "version_note": "crafted", "items": items}


def _refused(tmp_path: Path, doc: Any, match: str, name: str = "crafted.yaml") -> None:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    with pytest.raises(UnitsError, match=match):
        units.load_catalogue_from(path)


def test_crafted_catalogue_refusals(tmp_path: Path, catalogue: ItemCatalogue) -> None:
    base = _base_item(catalogue)
    other = catalogue.spec(HR).model_dump(mode="json")
    good = tmp_path / "good.yaml"
    good.write_text(
        yaml.safe_dump(_catalogue_doc([base, other]), sort_keys=False), encoding="utf-8"
    )
    loaded = units.load_catalogue_from(good)
    assert loaded.itemids() == (HR, TEMP_F)
    _refused(tmp_path, _catalogue_doc([base, dict(base)]), "duplicate itemids")
    _refused(tmp_path, _catalogue_doc([{**base, "plausible_low": 60.0}]), "plausible_low")
    _refused(tmp_path, _catalogue_doc([{**base, "canonical_unit": "kelvin"}]), "not among")
    _refused(
        tmp_path,
        _catalogue_doc([{**base, "accepted_units": {"degF": "f_to_c", "degC": 2.0}}]),
        "identity",
    )
    _refused(
        tmp_path,
        _catalogue_doc([{**base, "accepted_units": {"degF": "kelvin_to_c", "degC": "identity"}}]),
        "unknown formula",
    )
    _refused(
        tmp_path,
        _catalogue_doc(
            [{**base, "accepted_units": {"degF": "f_to_c", "F": "f_to_c", "degC": "identity"}}]
        ),
        "normalise to the same key",
    )
    _refused(
        tmp_path,
        _catalogue_doc([{**base, "accepted_units": {"degF": -1.0, "degC": "identity"}}]),
        "positive",
    )
    _refused(tmp_path, _catalogue_doc([{**base, "concept_group": "Vitals"}]), "concept_group")
    _refused(tmp_path, _catalogue_doc([{**base, "source": "noteevents"}]), "source")
    _refused(tmp_path, _catalogue_doc([{**base, "extra": 1}]), "extra")
    _refused(
        tmp_path,
        _catalogue_doc([base, {**other, "concept_group": base["concept_group"]}]),
        "one canonical unit per group",
    )
    _refused(tmp_path, ["not", "a", "mapping"], "top level")
    _refused(tmp_path, {"version": 1, "items": []}, "items")
    with pytest.raises(UnitsError, match="cannot read"):
        units.load_catalogue_from(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# 2. Formulas round-trip; unit strings normalise the same everywhere
# ---------------------------------------------------------------------------


def test_formulas_round_trip_within_tolerance() -> None:
    for name in (
        "f_to_c",
        "lb_to_kg",
        "in_to_cm",
        "mmol_to_mgdl_glucose",
        "umol_to_mgdl_creatinine",
    ):
        assert name in FORMULAS, name
    grid = (-40.0, 0.0, 0.01, 0.5, 1.0, 5.551, 37.0, 88.42, 98.6, 212.0, 1000.0)
    for name, affine in FORMULAS.items():
        for x in grid:
            assert _close(affine.invert(affine.apply(x)), x), (name, x)
            assert _close(affine.apply(affine.invert(x)), x), (name, x)
        assert name in units.FORMULA_NOTES
    assert FORMULAS[units.IDENTITY].is_identity and FORMULAS["f_to_c"].scale == 5.0 / 9.0
    assert _close(FORMULAS["f_to_c"].apply(212.0), 100.0) and _close(
        FORMULAS["f_to_c"].apply(32.0), 0.0
    )
    assert _close(FORMULAS["lb_to_kg"].apply(1.0), 0.45359237)
    assert _close(FORMULAS["in_to_cm"].apply(1.0), 2.54)
    assert math.isclose(FORMULAS["mmol_to_mgdl_glucose"].apply(5.551), 100.0, abs_tol=0.01)
    assert _close(FORMULAS["umol_to_mgdl_creatinine"].apply(88.42), 1.0)
    assert Affine(1.0).sql("x") == "x" and Affine(2.0).sql("x") == "(x) * 2.0"
    assert Affine(2.0, -1.0).sql("x") == "(x) * 2.0 + (-1.0)"
    assert units.formula_of(3) == Affine(3.0) and units.formula_name(3) == units.FACTOR
    with pytest.raises(UnitsError):
        units.formula_of("nope")
    with pytest.raises(UnitsError):
        units.formula_of(0)


NORMALISATION_CASES: tuple[tuple[str | None, str], ...] = (
    ("°F", "f"),
    ("deg F", "f"),
    ("DegF.", "f"),
    ("F", "f"),
    ("degrees C", "c"),
    ("degree c", "c"),
    (" mm Hg ", "mmhg"),
    ("mmHg", "mmhg"),
    ("µmol/L", "umol/l"),
    ("μmol/L", "umol/l"),
    ("umol/L", "umol/l"),
    ("lbs.", "lbs"),
    ("mEq/L", "meq/l"),
    ("K/uL", "k/ul"),
    ("x10^9/L", "x10^9/l"),
    ("%", "%"),
    (None, ""),
    ("", ""),
    ("   ", ""),
)


def test_unit_normalisation_python_sql_and_polars_agree() -> None:
    con = duckdb.connect()
    try:
        for text, expected in NORMALISATION_CASES:
            assert units.normalize_unit(text) == expected, text
            row = con.execute(f"SELECT {units.sql_normalize_unit('?')}", [text]).fetchone()
            assert row is not None and row[0] == expected, text
    finally:
        con.close()
    # the Polars twin, through the public frame API: every Fahrenheit spelling converts
    frame = pl.DataFrame(
        {
            "itemid": [TEMP_F] * len(F_SPELLINGS),
            "valuenum": [100.4] * len(F_SPELLINGS),
            "valueuom": list(F_SPELLINGS),
        }
    )
    out = units.harmonize_frame(frame)
    assert out.get_column("converted").all()
    assert all(_close(v, 38.0, 1e-9) for v in out.get_column("value_canonical").to_list())
    for spelling in F_SPELLINGS:
        assert units.harmonize(TEMP_F, 100.4, spelling).converted


# ---------------------------------------------------------------------------
# 3. harmonize: scalar, Polars, SQL macro (crafted synthetic values only)
# ---------------------------------------------------------------------------

#: ``(itemid, value, unit) -> (value_canonical, unit_canonical, converted, plausible)``.
CASES: tuple[
    tuple[tuple[int, float | None, str | None], tuple[float | None, str | None, bool, bool | None]],
    ...,
] = (
    ((TEMP_F, 100.4, "F"), (38.0, "degC", True, True)),
    ((TEMP_F, 100.4, "degC"), (100.4, "degC", True, False)),
    ((TEMP_F, 100.4, "kelvin"), (100.4, "degC", False, False)),
    ((TEMP_C, 38.0, "°C"), (38.0, "degC", True, True)),
    ((TEMP_C, 100.4, "degF"), (38.0, "degC", True, True)),
    ((WEIGHT_LB, 154.0, None), (69.85322498, "kg", True, True)),
    ((WEIGHT_LB, 70.0, "kg"), (70.0, "kg", True, True)),
    ((HEIGHT_IN, 70.0, "Inch"), (177.8, "cm", True, True)),
    ((HEIGHT_IN, 30.0, None), (76.2, "cm", True, False)),
    ((CREATININE, 88.42, "umol/L"), (1.0, "mg/dL", True, True)),
    ((CREATININE, 1.2, "mg/dL"), (1.2, "mg/dL", True, True)),
    ((CREATININE, 1.2, "furlongs"), (1.2, "mg/dL", False, True)),
    ((GLUCOSE_LAB, 5.551, "mmol/L"), (5.551 * 18.0156, "mg/dL", True, True)),
    ((INR, 1.1, None), (1.1, "ratio", True, True)),
    ((HR, 72.0, "bpm"), (72.0, "bpm", True, True)),
    ((HR, 350.0, "bpm"), (350.0, "bpm", True, False)),
    ((HR, 300.0, "bpm"), (300.0, "bpm", True, True)),
    ((HR, 0.0, "bpm"), (0.0, "bpm", True, True)),
    ((HR, None, "bpm"), (None, "bpm", True, None)),
    ((FIO2, 0.5, None), (0.5, "%", True, False)),
    ((FIO2, 50.0, "%"), (50.0, "%", True, True)),
    ((999_999, 5.0, "x"), (5.0, None, False, None)),
    ((999_999, None, None), (None, None, False, None)),
)


def test_harmonize_scalar_cases() -> None:
    for (itemid, value, unit), (canonical, unit_canonical, converted, plausible) in CASES:
        got = units.harmonize(itemid, value, unit)
        assert _close(got.value_canonical, canonical, 1e-6), (itemid, value, unit, got)
        assert got.unit_canonical == unit_canonical, (itemid, unit, got)
        assert got.converted is converted and got.plausible is plausible, (itemid, unit, got)


def _cases_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item": [c[0][0] for c in CASES],
            "reading": [c[0][1] for c in CASES],
            "uom": [c[0][2] for c in CASES],
            "tag": list(range(len(CASES))),
        }
    )


def test_harmonize_frame_and_plausible_mask() -> None:
    frame = _cases_frame()
    out = units.harmonize_frame(frame, itemid_col="item", value_col="reading", unit_col="uom")
    assert out.columns == [
        *frame.columns,
        "value_canonical",
        "unit_canonical",
        "converted",
        "plausible",
    ]
    assert out.get_column("tag").to_list() == list(range(len(CASES))), "row order kept"
    assert out.select(frame.columns).equals(frame), "input columns untouched"
    for row, (_inputs, (canonical, unit_canonical, converted, plausible)) in zip(
        out.rows(named=True), CASES, strict=True
    ):
        assert _close(row["value_canonical"], canonical, 1e-6), row
        assert row["unit_canonical"] == unit_canonical and row["converted"] is converted
        assert row["plausible"] is plausible, row
    mask = units.plausible_mask(frame, itemid_col="item", value_col="reading", unit_col="uom")
    assert mask.to_list() == out.get_column("plausible").to_list()
    # integer readings work too
    ints = pl.DataFrame({"itemid": [HR, HR], "valuenum": [72, 350], "valueuom": ["bpm", "bpm"]})
    assert units.plausible_mask(ints).to_list() == [True, False]


def test_macro_agrees_with_python_on_crafted_rows() -> None:
    statements = units.macro_statements()
    assert len(statements) == 6 and all(s.startswith("CREATE OR REPLACE MACRO") for s in statements)
    assert statements[-1].startswith(f"CREATE OR REPLACE MACRO {units.MACRO_NAME}(")
    con = duckdb.connect()
    try:
        units.install_macros(con)
        con.execute(
            "CREATE TABLE t (tag INTEGER, itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
        )
        con.executemany(
            "INSERT INTO t VALUES (?, ?, ?, ?)",
            [[i, *inputs] for i, (inputs, _expected) in enumerate(CASES)],
        )
        rows = con.execute(
            f"SELECT tag, {units.MACRO_NAME}(itemid, valuenum, valueuom) AS h FROM t ORDER BY tag"
        ).fetchall()
        assert len(rows) == len(CASES)
        for (tag, h), (inputs, _expected) in zip(rows, CASES, strict=True):
            twin = units.harmonize(*inputs)
            assert list(h) == ["value_canonical", "unit_canonical", "converted", "plausible"], h
            assert _close(h["value_canonical"], twin.value_canonical, 1e-9), (tag, h, twin)
            assert h["unit_canonical"] == twin.unit_canonical, (tag, h, twin)
            assert h["converted"] is twin.converted and h["plausible"] is twin.plausible, (tag, h)
        # a DECIMAL literal (the mwh sql acceptance form) must not overflow the arithmetic
        literal = con.execute(f"SELECT {units.MACRO_NAME}({TEMP_F}, 100.4, 'F') AS h").fetchone()
        assert literal is not None and _close(literal[0]["value_canonical"], 38.0, 1e-9)
        assert literal[0]["converted"] is True and literal[0]["plausible"] is True
        # re-installing is idempotent (CREATE OR REPLACE)
        units.install_macros(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4. Wiring: the DAG spec, the shared catalog step, the catalog hook
# ---------------------------------------------------------------------------


def test_dag_spec_and_catalog_hook() -> None:
    dag = load_dag()
    handlers = {
        units.STEP_ITEM_UNITS: "mimicwarehouse.units:run_item_units",
        units.STEP_VARIANTS: "mimicwarehouse.units:run_variants",
        units.STEP_DICTIONARY: "mimicwarehouse.units:run_dictionary",
    }
    for name, callable_name in handlers.items():
        step = dag.step(name)
        assert step.kind == "python" and step.callable_name == callable_name
        assert step.qualified_table is None and units.DAG_TAG in step.tags
        assert step.tiers == ("fixture", "demo", "dev", "full")
    assert set(dag.step(units.STEP_VARIANTS).depends_on) == {
        f"stage.{HOSP}.labevents",
        f"stage.{ICU}.chartevents",
        f"stage.{ICU}.outputevents",
        f"stage.{ICU}.inputevents",
    }
    assert set(dag.step(units.STEP_DICTIONARY).depends_on) == {
        units.STEP_ITEM_UNITS,
        f"stage.{ICU}.d_items",
        f"stage.{HOSP}.d_labitems",
    }
    catalog = dag.step("catalog")
    assert set(handlers) <= set(catalog.depends_on) and units.DAG_TAG in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=[units.DAG_TAG], tier="fixture")]
    assert ordered == [units.STEP_ITEM_UNITS, units.STEP_VARIANTS, units.STEP_DICTIONARY, "catalog"]
    assert "units.yaml" in [p.name for p in spec_mod.spec_paths()]
    # a single-file load cannot resolve the cross-file stage dependencies (EP-37 discovery
    # merges first) — the same holds for concepts.yaml; the merged graph is the contract
    with pytest.raises(spec_mod.DagError, match="unknown dependenc"):
        load_dag("units")
    # the extension runs after timesem's views and the concept walker
    extensions = build_mod.CATALOG_EXTENSIONS
    assert extensions[-1] is units.register_units
    assert extensions.index(units.register_units) > extensions.index(
        build_mod._concepts_register_derived
    )
    assert f"CREATE VIEW meta.itemids AS {build_mod.ITEMIDS_SELECT_SQL}"


# ---------------------------------------------------------------------------
# 5. Variants on crafted source tables; suppression follows the hook
# ---------------------------------------------------------------------------


def _crafted_sources(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"CREATE SCHEMA {ICU}")
    con.execute(f"CREATE SCHEMA {HOSP}")
    con.execute(
        f"CREATE TABLE {ICU}.chartevents (itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.execute(
        f"CREATE TABLE {HOSP}.labevents (itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    rows: list[list[Any]] = []
    rows += [[HR, 70.0, "bpm"]] * 30 + [[HR, 71.0, "BPM"]] * 5 + [[HR, 72.0, None]] * 12
    rows += [[TEMP_F, 98.6, "°F"]] * 12
    con.executemany(f"INSERT INTO {ICU}.chartevents VALUES (?, ?, ?)", rows)
    labs: list[list[Any]] = [[CREATININE, 1.0, "mg/dL"]] * 20 + [[CREATININE, 88.0, "umol/L"]] * 15
    con.executemany(f"INSERT INTO {HOSP}.labevents VALUES (?, ?, ?)", labs)


def test_variants_on_crafted_tables_and_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    from mimicwarehouse import safe

    bare = duckdb.connect()
    try:
        assert units.variants_sql(units.load_catalogue(), set()) == ""
        with pytest.raises(UnitsError, match="no source table"):
            units.compute_variants(bare)
    finally:
        bare.close()
    con = duckdb.connect()
    try:
        _crafted_sources(con)
        raw = units.compute_variants(con)
    finally:
        con.close()
    assert raw.columns == [
        "itemid",
        "source",
        "valueuom",
        "unit_norm",
        "n_rows",
        "share",
        "expected",
    ]
    # variants are keyed by the unit string as written (spellings stay visible); the
    # normalised key says which of them the catalogue accepts
    by_key = {(r["itemid"], r["valueuom"]): r for r in raw.rows(named=True)}
    assert by_key[(HR, "bpm")]["n_rows"] == 30 and by_key[(HR, "BPM")]["n_rows"] == 5
    assert by_key[(HR, "BPM")]["unit_norm"] == "bpm" and by_key[(HR, "BPM")]["expected"] is True
    assert by_key[(HR, None)]["n_rows"] == 12 and by_key[(HR, None)]["expected"] is False
    assert by_key[(HR, None)]["unit_norm"] == ""
    assert _close(by_key[(HR, "bpm")]["share"], 30 / 47) and by_key[(HR, "bpm")]["expected"]
    assert by_key[(TEMP_F, "°F")]["n_rows"] == 12 and by_key[(TEMP_F, "°F")]["unit_norm"] == "f"
    assert by_key[(TEMP_F, "°F")]["expected"] is True
    umol = by_key[(CREATININE, "umol/L")]
    assert umol["n_rows"] == 15 and umol["expected"] is True and umol["unit_norm"] == "umol/l"
    assert {r["source"] for r in raw.rows(named=True)} == {"chartevents", "labevents"}
    # only curated itemids are counted
    assert set(raw.get_column("itemid").to_list()) == {HR, TEMP_F, CREATININE}

    # shrink the NULL-unit variant under k too: both small cells must blank
    shrunk = raw.with_columns(
        pl.col("n_rows") - pl.when(pl.col("unit_norm") == "").then(5).otherwise(0)
    )
    published = units.suppress_variants(shrunk, 11)
    assert published.columns == [c for c, _t in VARIANTS_COLUMNS if c not in ("tier", "build_id")]
    rows = {(r["itemid"], r["valueuom"]): r for r in published.rows(named=True)}
    # EP-43: the hook is complementary — the two small HR cells blank, and so does the
    # (HR, "bpm") spelling, because the per-itemid normalised-unit total (bpm + BPM, one
    # harmonised count(*) away) would otherwise back the BPM cell out
    for key in ((HR, None), (HR, "BPM"), (HR, "bpm")):
        small = rows[key]
        assert small["suppressed"] is True and small["n_rows"] is None, key
        assert small["share"] is None, key
    assert rows[(TEMP_F, "°F")]["suppressed"] is False and rows[(TEMP_F, "°F")]["n_rows"] == 12
    assert rows[(CREATININE, "mg/dL")]["suppressed"] is False
    assert _close(rows[(CREATININE, "mg/dL")]["share"], 20 / 35), "share over the released rows"
    assert all(r["k"] == 11 for r in rows.values())
    released = published.filter(~pl.col("suppressed"))
    for _itemid, group in released.group_by("itemid"):
        assert _close(float(group.get_column("share").sum()), 1.0)
    assert not (released.get_column("n_rows") < 11).any()

    # the hook decides: a crafted suppressor blanks exactly what it drops
    def drop_umol(df: pl.DataFrame, k: int, cols: list[str]) -> tuple[pl.DataFrame, int]:
        kept = df.filter(pl.col("unit_norm") != "umol/l")
        return kept, df.height - kept.height

    monkeypatch.setattr(safe, "SUPPRESSOR", drop_umol)
    hooked = units.suppress_variants(raw, 11)
    flags = {(r["itemid"], r["valueuom"]): r["suppressed"] for r in hooked.rows(named=True)}
    assert flags[(CREATININE, "umol/L")] is True and flags[(CREATININE, "mg/dL")] is False
    assert flags[(HR, "BPM")] is False, "the hook, not the default rule, decided"


def test_summarize_variants_on_crafted_frames() -> None:
    variants = pl.DataFrame(
        {
            "itemid": [HR, HR, HR, TEMP_F, CREATININE],
            "source": ["chartevents"] * 4 + ["labevents"],
            "valueuom": ["bpm", "BPM", None, "°F", "mg/dL"],
            "unit_norm": ["bpm", "bpm", "", "f", "mg/dl"],
            "n_rows": [1000, None, 20, 500, 40],
            "share": [1000 / 1020, None, 20 / 1020, 1.0, 1.0],
            "suppressed": [False, True, False, False, False],
            "expected": [True, True, False, True, True],
        }
    )
    labels = pl.DataFrame(
        {
            "itemid": [HR, TEMP_F, CREATININE, INR],
            "label": ["Heart Rate", "Temperature Fahrenheit", "Creatinine", "INR(PT)"],
            "canonical_unit": ["bpm", "degC", "mg/dL", "ratio"],
        }
    )
    out = units.summarize_variants(variants, labels)
    assert out.columns == list(REPORT_COLUMNS)
    rows = {r["itemid"]: r for r in out.rows(named=True)}
    assert set(rows) == {HR, TEMP_F, CREATININE, INR}
    hr = rows[HR]
    assert hr["n_variants"] == 3 and hr["n_suppressed"] == 1 and hr["flagged"] is True
    assert hr["dominant_unit"] == "bpm" and _close(hr["dominant_share"], 1000 / 1020)
    assert hr["unexpected_units"] == [units.NULL_UNIT_LABEL]
    assert rows[TEMP_F]["flagged"] is False and rows[TEMP_F]["unexpected_units"] == []
    assert rows[CREATININE]["dominant_share"] == 1.0
    absent = rows[INR]
    assert absent["n_variants"] == 0 and absent["dominant_unit"] is None
    assert absent["unexpected_units"] == [] and absent["flagged"] is False


# ---------------------------------------------------------------------------
# 6. The session fixture lake: meta.item_* tables, the macro, safe_query, the report
# ---------------------------------------------------------------------------


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def test_fixture_lake_meta_tables_and_macro(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
    fixture_lake_settings: Settings,
    catalogue: ItemCatalogue,
) -> None:
    con = fixture_lake_catalog
    tables = {
        str(r[0]): str(r[1])
        for r in con.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'meta'"
        ).fetchall()
    }
    for table in (units.ITEM_UNITS_TABLE, units.VARIANTS_TABLE, units.DICTIONARY_TABLE):
        assert tables.get(table) == "BASE TABLE", table
        comment = _scalar(
            con,
            "SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
            f"AND table_name = '{table}'",
        )
        assert comment and "EP-39" in comment, table
    assert not any("raw" in name for name in tables), "the raw counts never enter a catalog"

    # meta.item_units = the catalogue, itemid x accepted unit
    described = [d[0] for d in con.execute(f"DESCRIBE meta.{units.ITEM_UNITS_TABLE}").fetchall()]
    assert described == [c for c, _t in ITEM_UNITS_COLUMNS]
    assert _scalar(con, f"SELECT count(DISTINCT itemid) FROM meta.{units.ITEM_UNITS_TABLE}") == len(
        catalogue
    )
    assert _scalar(con, f"SELECT count(*) FROM meta.{units.ITEM_UNITS_TABLE}") == len(
        units.item_units_rows()
    )
    assert len(catalogue) >= units.MIN_CURATED_ITEMS
    temp = con.execute(
        f"SELECT accepted_unit, unit_norm, formula, scale, intercept, plausible_low, "
        f"plausible_high FROM meta.{units.ITEM_UNITS_TABLE} WHERE itemid = {TEMP_F} "
        "ORDER BY accepted_unit"
    ).fetchall()
    assert [(u, n, f) for u, n, f, *_ in temp] == [
        ("degC", "c", "identity"),
        ("degF", "f", "f_to_c"),
    ]
    assert _close(temp[1][3], 5.0 / 9.0) and temp[0][5:] == (21.1, 48.9)

    # meta.item_dictionary = meta.itemids + curation columns (no unit_hint)
    dictionary_columns = [
        d[0] for d in con.execute(f"DESCRIBE meta.{units.DICTIONARY_TABLE}").fetchall()
    ]
    itemids_columns = [d[0] for d in con.execute("DESCRIBE meta.itemids").fetchall()]
    assert dictionary_columns == [
        *itemids_columns,
        "curated",
        "concept_group",
        "canonical_unit",
        "plausible_low",
        "plausible_high",
    ]
    assert "unit_hint" not in dictionary_columns
    assert _scalar(con, f"SELECT count(*) FROM meta.{units.DICTIONARY_TABLE}") == _scalar(
        con, "SELECT count(*) FROM meta.itemids"
    )
    dim_ids = {int(r[0]) for r in con.execute("SELECT itemid FROM meta.itemids").fetchall()}
    curated_rows = con.execute(
        f"SELECT itemid, label, concept_group, canonical_unit, plausible_low, plausible_high "
        f"FROM meta.{units.DICTIONARY_TABLE} WHERE curated ORDER BY itemid"
    ).fetchall()
    assert {int(r[0]) for r in curated_rows} == set(catalogue.itemids()) & dim_ids
    assert len(curated_rows) >= 40, "the fixture dims carry the brief's core items"
    for itemid, label, group, canonical, low, high in curated_rows:
        item = catalogue.spec(int(itemid))
        assert label == item.label, (itemid, label, item.label)
        assert (group, canonical, low, high) == (
            item.concept_group,
            item.canonical_unit,
            *item.bounds,
        )
    assert (
        _scalar(
            con,
            f"SELECT count(*) FROM meta.{units.DICTIONARY_TABLE} "
            "WHERE NOT curated AND concept_group IS NOT NULL",
        )
        == 0
    )

    # meta.item_unit_variants: nothing below k released, shares over released rows
    k = fixture_lake_settings.k_suppression
    variant_columns = [
        d[0] for d in con.execute(f"DESCRIBE meta.{units.VARIANTS_TABLE}").fetchall()
    ]
    assert variant_columns == [c for c, _t in VARIANTS_COLUMNS]
    assert (
        _scalar(
            con,
            f"SELECT count(*) FROM meta.{units.VARIANTS_TABLE} "
            f"WHERE n_rows IS NOT NULL AND n_rows < {k}",
        )
        == 0
    )
    assert (
        _scalar(
            con,
            f"SELECT count(*) FROM meta.{units.VARIANTS_TABLE} "
            "WHERE suppressed AND (n_rows IS NOT NULL OR share IS NOT NULL)",
        )
        == 0
    )
    assert (
        _scalar(
            con,
            f"SELECT count(*) FROM meta.{units.VARIANTS_TABLE} WHERE k <> {k} OR tier <> 'fixture'",
        )
        == 0
    )
    variant_ids = {
        int(r[0])
        for r in con.execute(f"SELECT DISTINCT itemid FROM meta.{units.VARIANTS_TABLE}").fetchall()
    }
    assert variant_ids and variant_ids <= set(catalogue.itemids())
    sums = con.execute(
        f"SELECT itemid, sum(share) FROM meta.{units.VARIANTS_TABLE} "
        "WHERE NOT suppressed GROUP BY itemid"
    ).fetchall()
    assert sums and all(_close(float(s), 1.0) for _i, s in sums)
    hr = con.execute(
        f"SELECT valueuom, unit_norm, expected, suppressed FROM meta.{units.VARIANTS_TABLE} "
        f"WHERE itemid = {HR}"
    ).fetchall()
    assert hr and all(e for _u, _n, e, _s in hr), "the fixture charts heart rate in bpm only"
    assert any(n == "bpm" and not s for _u, n, _e, s in hr)
    lake = fixture_lake_settings.lake_root("fixture")
    assert units.raw_variants_path(lake, "fixture").is_file()
    assert units.meta_table_path(lake, "fixture", units.VARIANTS_TABLE).is_file()

    # the macro family lives in the catalog and works on the READ_ONLY connection
    macros = {
        str(r[0])
        for r in con.execute(
            "SELECT function_name FROM duckdb_functions() WHERE function_name LIKE 'mwh_%'"
        ).fetchall()
    }
    assert {
        units.MACRO_NAME,
        units.MACRO_UNIT_NORM,
        units.MACRO_UNIT_CANONICAL,
        units.MACRO_UNIT_KNOWN,
        units.MACRO_VALUE_CANONICAL,
        units.MACRO_PLAUSIBLE,
    } <= macros
    h = _scalar(con, f"SELECT {units.MACRO_NAME}({WEIGHT_LB}, 154.0, NULL)")
    assert _close(h["value_canonical"], 69.85322498, 1e-6) and h["unit_canonical"] == "kg"


def test_macro_and_tables_through_safe_query(fixture_lake_settings: Settings) -> None:
    from mimicwarehouse.safe import safe_query

    result = safe_query(
        f"SELECT {units.MACRO_NAME}({TEMP_F}, 100.4, 'degF') AS h",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep39",
    )
    h = result.df.get_column("h")[0]
    assert _close(h["value_canonical"], 38.0, 1e-9) and h["unit_canonical"] == "degC"
    assert h["converted"] is True and h["plausible"] is True
    counted = safe_query(
        f"SELECT count(DISTINCT itemid) AS n FROM meta.{units.ITEM_UNITS_TABLE}",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep39",
    )
    assert int(counted.df["n"][0]) >= units.MIN_CURATED_ITEMS
    # the macro over a subject-keyed table still needs a real count column (aggregate-only)
    grouped = safe_query(
        f"SELECT {units.MACRO_NAME}(itemid, valuenum, valueuom).plausible AS plausible, "
        "count(*) AS n "
        f"FROM {ICU}.chartevents WHERE itemid = {HR} GROUP BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep39",
    )
    assert grouped.df.height <= 3
    runner = helpers.cli_runner()
    try:
        cli = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "sql",
                "--tier",
                "fixture",
                "--format",
                "json",
                f"SELECT {units.MACRO_NAME}({TEMP_F}, 100.4, 'F') AS h",
            ],
        )
        assert cli.exit_code == 0, cli.output
        payload = json.loads(cli.stdout)
        assert _close(payload["rows"][0]["h"]["value_canonical"], 38.0, 1e-9)
    finally:
        config.configure()


def test_report_on_fixture_and_cli(
    fixture_lake_settings: Settings, catalogue: ItemCatalogue
) -> None:
    df = units.report("fixture", settings=fixture_lake_settings, actor="test_ep39")
    assert df.columns == list(REPORT_COLUMNS) and df.height == len(catalogue)
    rows = {r["itemid"]: r for r in df.rows(named=True)}
    hr = rows[HR]
    assert hr["label"] == "Heart Rate" and hr["dominant_unit"] == "bpm"
    assert hr["dominant_share"] == 1.0 and hr["unexpected_units"] == [] and hr["flagged"] is False
    assert rows[WEIGHT_LB]["n_variants"] == 0, "not in the fixture dictionary"
    assert all(r["dominant_share"] is None or r["dominant_share"] <= 1.0 for r in rows.values())
    runner = helpers.cli_runner()
    root = str(fixture_lake_settings.data_root)
    try:
        table = runner.invoke(app, ["--data-root", root, "units", "report", "--tier", "fixture"])
        assert table.exit_code == 0, table.output
        assert "Heart Rate" in table.stdout and "curated itemid(s)" in table.stdout
        assert "bpm" in table.stdout
        as_json = runner.invoke(
            app, ["--data-root", root, "units", "report", "--tier", "fixture", "--format", "json"]
        )
        assert as_json.exit_code == 0, as_json.output
        payload = json.loads(as_json.stdout)
        assert payload["tier"] == "fixture" and len(payload["items"]) == len(catalogue)
        assert {i["itemid"]: i["dominant_unit"] for i in payload["items"]}[HR] == "bpm"
        bad_format = runner.invoke(
            app, ["--data-root", root, "units", "report", "--tier", "fixture", "--format", "xml"]
        )
        assert bad_format.exit_code == 2
        bad_tier = runner.invoke(app, ["--data-root", root, "units", "report", "--tier", "nope"])
        assert bad_tier.exit_code == 2
        missing = runner.invoke(app, ["--data-root", root, "units", "report", "--tier", "demo"])
        assert missing.exit_code == 2, missing.output
        check = runner.invoke(app, ["units", "check"])
        assert check.exit_code == 0 and "ok" in check.stdout, check.output
        check_json = runner.invoke(app, ["units", "check", "--json"])
        assert json.loads(check_json.stdout)["items"] == len(catalogue)
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 7. Docs in sync, import budget
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path, catalogue: ItemCatalogue) -> None:
    path = units.methods_doc_path()
    assert path.is_file(), "docs/methods/units.md exists"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "retrospective",
        "inclusive",
        "mimic-code",
        "EP-44",
        "EP-55",
        "EP-138",
        "safe_query",
        "(blank)",
        "mwh_harmonize",
        "released rows",
    ):
        assert needle in text, needle
    copy = tmp_path / "units.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    units.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.units`"
    formulas = units.render_formula_table()
    items = units.render_item_table()
    assert formulas in text and items in text
    for name in FORMULAS:
        assert f"`{name}`" in formulas
    for item in catalogue.items:
        assert f"| {item.itemid} | {item.source} | {item.label} |" in items
    assert items.count("\n") == len(catalogue) + 2
    assert not BAND_TOKEN.search(text)


def test_import_budget() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.units",
        lazy=(
            "mimicwarehouse.safe",
            "mimicwarehouse.catalog.build",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.concepts.patching",
        ),
    )
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe", "mimicwarehouse.catalog.build"))


# ---------------------------------------------------------------------------
# 8. Dev tier: the real catalog through safe_query
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_meta_tables_labels_and_coverage(dev_catalog: Path, catalogue: ItemCatalogue) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh build --tier dev --tag units` (EP-39) first"

    def q(sql: str) -> pl.DataFrame:
        return safe_query(
            sql, tier="dev", settings=settings, actor="test_ep39", row_cap=units.REPORT_ROW_CAP
        ).df

    n_units = int(
        q(f"SELECT count(DISTINCT itemid) AS n FROM meta.{units.ITEM_UNITS_TABLE}")["n"][0]
    )
    assert n_units == len(catalogue) >= units.MIN_CURATED_ITEMS, remedy
    small = q(
        f"SELECT count(*) AS n FROM meta.{units.VARIANTS_TABLE} "
        f"WHERE n_rows IS NOT NULL AND n_rows < {settings.k_suppression}"
    )
    assert int(small["n"][0]) == 0, "no released cell below k"
    # labels equal the real dictionary for every curated itemid (all are in the full dims)
    labels = q(f"SELECT itemid, label FROM meta.{units.DICTIONARY_TABLE} WHERE curated ORDER BY 1")
    got = dict(zip(labels["itemid"].to_list(), labels["label"].to_list(), strict=True))
    assert set(got) == set(catalogue.itemids()), remedy
    mismatched = {
        i: (got[i], catalogue.spec(i).label) for i in got if got[i] != catalogue.spec(i).label
    }
    assert not mismatched, mismatched
    # every core itemid has a variant row; overall coverage >= 95 % (a rare item may be
    # absent from the 5 % sample altogether)
    present = {
        int(i)
        for i in q(f"SELECT DISTINCT itemid FROM meta.{units.VARIANTS_TABLE}")["itemid"].to_list()
    }
    assert present >= BRIEF_ITEMS, sorted(BRIEF_ITEMS - present)
    coverage = len(present & set(catalogue.itemids())) / len(catalogue)
    assert coverage >= 0.95, coverage
    h = q(f"SELECT {units.MACRO_NAME}({TEMP_F}, 100.4, 'degF') AS h")["h"][0]
    assert _close(h["value_canonical"], 38.0, 1e-9) and h["plausible"] is True
    df = units.report("dev", settings=settings, actor="test_ep39")
    assert df.height == len(catalogue)
    rows = {r["itemid"]: r for r in df.rows(named=True)}
    assert rows[HR]["dominant_unit"] == "bpm" and rows[CREATININE]["dominant_unit"] == "mg/dL"
    print(
        f"dev: {n_units} curated itemid(s), {len(present)} with variant rows, "
        f"{int(df.get_column('flagged').sum())} flagged"
    )
