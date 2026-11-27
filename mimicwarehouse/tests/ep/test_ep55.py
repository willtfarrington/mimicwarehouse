"""EP-55 — latency marts A: the mart registry + ``marts.icustay_first_day`` (fixture; dev
where marked).

Covers: the packaged spec validates (154 columns, keys, partition, units against the EP-39
catalogue) and malformed specs are refused; the statement is generated from the spec; the
generic writer on a crafted context (one sorted file per bucket, swap-published, manifest
lines, ``manifest.json``, per-tier status, a ``kind: mart`` benchmark line, the marts
snapshot id; a DuckDB failure leaves the published mart intact and records the failure);
the DAG wiring (the step mirrors the spec's dependencies, ``--tag marts``); the session
fixture lake (every declared column with its declared type, one row per ICU stay, ages
capped at 91, temperatures inside the EP-39 bounds, the registry tables and the dictionary
section, ``safe_query`` aggregates only). ``tier("dev")``: the same invariants through
``safe_query`` and the dev build's wall time from the benchmark ledger.

The itemid rollups (``marts.itemid_daily`` / ``marts.itemid_summary``) moved to EP-175 at
this brief's pickup split (2026-11-26), so their tests are EP-175's.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
import yaml

from mimicwarehouse import config, units
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.marts import first_day, registry
from mimicwarehouse.marts.registry import MartBuildError, MartSpec, MartSpecError

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_55

NAME = "icustay_first_day"
N_COLUMNS = 154
K = 11


def _temperature_bounds() -> tuple[float, float]:
    """The widest EP-39 plausibility bounds over the vitals.temperature group (degC)."""
    items = [i for i in units.load_catalogue().items if i.concept_group == "vitals.temperature"]
    assert items, "the catalogue curates vitals.temperature"
    return min(i.plausible_low for i in items), max(i.plausible_high for i in items)


def _spec_doc() -> dict[str, Any]:
    doc = yaml.safe_load((registry.specs_dir() / f"{NAME}.yaml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _write_spec(tmp_path: Path, doc: dict[str, Any], name: str = NAME) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. The registry and the packaged spec
# ---------------------------------------------------------------------------


def test_packaged_spec_validates() -> None:
    reg = registry.load_registry()
    assert NAME in reg.names, "EP-55 shipped this mart; EP-175 added the itemid rollups"
    spec = reg.spec(NAME)
    assert spec is first_day.spec() or spec == first_day.spec()
    assert spec.grain == "icustay" and spec.version == 1
    assert spec.key == ("subject_id", "hadm_id", "stay_id")
    assert spec.partition == registry.BUCKET_COLUMN and spec.sort == ("subject_id", "stay_id")
    names = spec.column_names
    assert len(names) == N_COLUMNS and len(set(names)) == N_COLUMNS
    assert names[:4] == ("subject_id", "hadm_id", "stay_id", "subject_bucket")
    # the families expand variable by variable, statistics in declared order
    assert names[names.index("heart_rate_min") : names.index("heart_rate_min") + 3] == (
        "heart_rate_min",
        "heart_rate_max",
        "heart_rate_mean",
    )
    assert spec.column("glucose_chart_mean").expr == "fdv.glucose_mean"
    assert spec.column("glucose_max").expr == "fdl.glucose_max", "the lab glucose keeps its name"
    for column in (
        "admission_age",
        "age_capped",
        "gender",
        "race",
        "admission_type",
        "insurance",
        "first_service",
        "first_careunit",
        "era",
        "first_hosp_stay",
        "first_icu_stay",
        "icustay_seq",
        "los_icu_days",
        "los_hospital_days",
        "hours_admit_to_icu",
        "icu_intime",
        "hospital_expire_flag",
        "discharge_location",
        "ph_min",
        "lactate_max",
        "pao2fio2ratio_min",
        "gcs_min",
        "urineoutput",
        "weight_admit",
        "sofa",
        "sofa_respiration",
        "sofa_renal",
        "charlson_comorbidity_index",
        "vent_invasive_first_day",
        "vasopressor_first_day",
        "rrt_first_day",
        "sepsis3",
        "kdigo_max_stage_first_day",
    ):
        assert column in names, column
    for var in ("heart_rate", "sbp", "dbp", "mbp", "resp_rate", "temperature", "spo2"):
        for stat in ("min", "max", "mean"):
            assert f"{var}_{stat}" in names
    assert spec.column("temperature_mean").unit == "degC"
    assert spec.column("subject_bucket").expr == f"ie.subject_id % {registry.NUM_BUCKETS}"
    assert registry.check_units(spec) == []
    assert all(c.type in registry.DUCKDB_TYPES for c in spec.expanded)
    assert spec.status_key == spec.step_name == f"marts.{NAME}"
    assert len(spec.spec_sha256) == 64 and len(spec.schema_hash) == 64
    from mimicwarehouse.loader.buckets import BUCKET_COLUMN, NUM_BUCKETS
    from mimicwarehouse.timesem import AGE_CAP

    assert (registry.NUM_BUCKETS, registry.BUCKET_COLUMN) == (NUM_BUCKETS, BUCKET_COLUMN)
    assert f">= {AGE_CAP}" in spec.column("age_capped").expr
    text = (registry.specs_dir() / f"{NAME}.yaml").read_text(encoding="utf-8")
    assert text.isascii()


def test_units_follow_the_catalogue(tmp_path: Path) -> None:
    spec = first_day.spec()
    canonical = {i.concept_group: i.canonical_unit for i in units.load_catalogue().items}
    grouped = [c for c in spec.expanded if c.concept_group is not None]
    assert len(grouped) >= 30
    for c in grouped:
        assert c.concept_group is not None
        assert c.unit == canonical[c.concept_group], c.name
    doc = _spec_doc()
    vitals = next(e for e in doc["columns"] if e.get("family") == "first_day_vitalsign")
    vitals["vars"][5]["unit"] = "degF"  # temperature
    with pytest.raises(MartSpecError, match="canonical 'degC'"):
        registry.load_spec_from(_write_spec(tmp_path, doc))
    vitals["vars"][5]["unit"] = "degC"
    vitals["vars"][5]["concept_group"] = "vitals.nonsense"
    with pytest.raises(MartSpecError, match="not in the catalogue"):
        registry.load_spec_from(_write_spec(tmp_path, doc))


def _mutations() -> list[tuple[str, Any, str]]:
    def dup_column(d: dict[str, Any]) -> None:
        d["columns"].append(dict(d["columns"][0]))

    def unknown_key(d: dict[str, Any]) -> None:
        d["key"] = ["subject_id", "no_such_column"]

    def bad_grain(d: dict[str, Any]) -> None:
        d["grain"] = "fortnight"

    def base_not_first(d: dict[str, Any]) -> None:
        d["sources"] = [d["sources"][1], d["sources"][0], *d["sources"][2:]]

    def family_alias(d: dict[str, Any]) -> None:
        next(e for e in d["columns"] if "family" in e)["alias"] = "nowhere"

    def partition_type(d: dict[str, Any]) -> None:
        d["columns"][3]["type"] = "BIGINT"

    def join_without_key(d: dict[str, Any]) -> None:
        d["sources"][1].pop("join_on")

    def bad_type(d: dict[str, Any]) -> None:
        d["columns"][0]["type"] = "HUGEINT"

    def extra_field(d: dict[str, Any]) -> None:
        d["columns"][0]["colour"] = "blue"

    return [
        ("duplicate column", dup_column, "duplicate column names"),
        ("undeclared key", unknown_key, "not declared"),
        ("unknown grain", bad_grain, "not an EP-34 grain"),
        ("base not first", base_not_first, "base table"),
        ("family alias", family_alias, "is not a source"),
        ("partition type", partition_type, "must be INTEGER"),
        ("join without key", join_without_key, "needs join_on"),
        ("unknown type", bad_type, "validation error"),
        ("extra field", extra_field, "validation error"),
    ]


@pytest.mark.parametrize(("label", "mutate", "message"), _mutations(), ids=lambda v: str(v))
def test_malformed_specs_are_refused(tmp_path: Path, label: str, mutate: Any, message: str) -> None:
    doc = _spec_doc()
    mutate(doc)
    with pytest.raises(MartSpecError, match=message):
        registry.load_spec_from(_write_spec(tmp_path, doc))


def test_spec_name_must_match_its_file(tmp_path: Path) -> None:
    with pytest.raises(MartSpecError, match="must match the file name"):
        registry.load_spec_from(_write_spec(tmp_path, _spec_doc(), name="renamed"))
    with pytest.raises(MartSpecError, match="no mart"):
        registry.load_registry().spec("no_such_mart")


def test_statement_is_generated_from_the_spec() -> None:
    spec = first_day.spec()
    sql = first_day.build_sql(spec)
    assert sql.startswith("WITH cte_svc AS (")
    assert sql.count("CAST(") == N_COLUMNS
    assert sql.count("\nLEFT JOIN ") == len(spec.sources) - 1
    assert "FROM mimiciv_icu.icustays AS ie" in sql
    assert "LEFT JOIN cte_vent AS vent ON vent.stay_id = ie.stay_id" in sql
    assert "LEFT JOIN mimiciv_derived.charlson AS cci ON cci.hadm_id = ie.hadm_id" in sql
    assert "ventilation_status = 'InvasiveVent'" in sql
    for agent in first_day.VASOPRESSORS:
        assert f"va.{agent}" in sql
    assert "dobutamine" not in sql and "milrinone" not in sql, "inotropes do not count"
    assert f"INTERVAL {first_day.WINDOW_BEFORE_H} HOUR" in sql
    assert f"INTERVAL {first_day.WINDOW_AFTER_H} HOUR" in sql
    assert set(spec.cte_aliases) == set(first_day.ctes()) == {"svc", "vent", "vaso", "kd"}
    with pytest.raises(MartSpecError, match="CTEs missing"):
        registry.mart_sql(spec, {})
    core, concepts = first_day.source_relations(spec)
    assert core == [
        "mimiciv_hosp.admissions",
        "mimiciv_hosp.patients",
        "mimiciv_hosp.services",
        "mimiciv_icu.icustays",
    ]
    assert len(concepts) == 14 and "first_day_vitalsign" in concepts


def test_dag_wiring() -> None:
    dag = load_dag()
    spec = first_day.spec()
    step = dag.step(first_day.STEP)
    assert step.kind == "python" and step.callable_name == spec.builder
    assert step.qualified_table == spec.status_key, "skips when complete for the tier"
    assert tuple(step.depends_on) == spec.depends_on, "the DAG mirrors the spec"
    assert "marts" in step.tags and set(step.tiers) == {"fixture", "demo", "dev", "full"}
    names = {s.name for s in dag.steps}
    assert set(spec.depends_on) <= names
    catalog = dag.step("catalog")
    assert first_day.STEP in catalog.depends_on and "marts" in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=["marts"], tier="fixture")]
    assert first_day.STEP in ordered and "cohorts.build" in ordered and ordered[-1] == "catalog"
    from mimicwarehouse.catalog import build as build_mod

    extensions = build_mod.CATALOG_EXTENSIONS
    index = extensions.index(registry.register_marts)
    assert index > extensions.index(build_mod._spine_register)
    assert extensions[-2] is build_mod._phenotypes_register, "phenotypes stay second to last"
    assert extensions[-1] is units.register_units, "units stays last"
    from mimicwarehouse.dag.benchmarks import BENCHMARK_KINDS

    assert registry.BENCH_KIND in BENCHMARK_KINDS


# ---------------------------------------------------------------------------
# 2. The generic writer on a crafted context (synthetic rows, temp data root)
# ---------------------------------------------------------------------------

_CRAFTED = {
    "name": "crafted",
    "version": 1,
    "grain": "icustay",
    "description": "A crafted mart over synthetic rows.",
    "key": ["subject_id", "stay_id"],
    "partition": "subject_bucket",
    "sort": ["subject_id", "stay_id"],
    "builder": "tests.crafted:run",
    "sources": [{"alias": "t", "relation": "main.crafted_base", "join": "base"}],
    "columns": [
        {"name": "subject_id", "type": "INTEGER", "expr": "t.subject_id", "description": "s"},
        {"name": "stay_id", "type": "INTEGER", "expr": "t.stay_id", "description": "k"},
        {
            "name": "subject_bucket",
            "type": "INTEGER",
            "expr": "t.subject_id % 100",
            "description": "b",
        },
        {"name": "v", "type": "DOUBLE", "expr": "t.v", "description": "a value"},
    ],
}


def _crafted_ctx(settings: Settings, con: duckdb.DuckDBPyConnection) -> Any:
    from mimicwarehouse.dag.runner import StepContext, new_build_id

    lake_root = settings.lake_root("fixture")
    lake_root.mkdir(parents=True, exist_ok=True)
    return StepContext(
        settings=settings,
        tier="fixture",
        build_id=new_build_id("fixture"),
        con=con,
        log=logging.getLogger("test_ep55"),
        raw_root=settings.data_root,
        lake_root=lake_root,
        buckets=None,
    )


def test_writer_publishes_sorted_buckets_and_records_everything(tmp_path: Path) -> None:
    from mimicwarehouse.dag import benchmarks
    from mimicwarehouse.dag.snapshot import read_snapshots
    from mimicwarehouse.loader.manifest import iter_manifest, manifest_path, read_status

    settings = config.Settings(data_root=tmp_path / "root")
    spec = MartSpec.model_validate(_CRAFTED)
    con = duckdb.connect()
    try:
        # synthetic ids >= 90 000 000 in buckets 1, 42 and 99, inserted out of order
        con.execute(
            "CREATE TABLE crafted_base AS SELECT * FROM (VALUES "
            "(90000099, 2, 1.5), (90000001, 9, 2.0), (90000042, 5, NULL), "
            "(90000001, 3, 4.0), (90000099, 1, 0.5)) AS s(subject_id, stay_id, v)"
        )
        ctx = _crafted_ctx(settings, con)
        sql = registry.mart_sql(spec)
        outcome = registry.write_mart(ctx, spec, sql)
        assert outcome.rows == 5 and outcome.files == 3 and outcome.layer == "marts"
        dest = registry.mart_dir(ctx.lake_root, "fixture", "crafted")
        files = registry.mart_files(dest, partitioned=True)
        assert [f.parent.name for f in files] == [
            "subject_bucket=1",
            "subject_bucket=42",
            "subject_bucket=99",
        ]
        for f in files:
            got = con.execute(
                f"SELECT subject_id, stay_id FROM read_parquet('{f.as_posix()}')"
            ).fetchall()
            assert got == sorted(got), f"{f} is sorted by (subject_id, stay_id)"
            cols = [
                r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{f.as_posix()}'").fetchall()
            ]
            assert cols == list(spec.column_names), "subject_bucket lives inside the file"
        assert not registry.mart_dir(ctx.lake_root, "fixture", "crafted.new").exists()
        manifest = registry.read_mart_manifest(ctx.lake_root, "fixture", "crafted")
        assert manifest is not None and manifest["rows"] == 5 and manifest["files"] == 3
        assert manifest["spec_sha256"] == spec.spec_sha256
        assert manifest["sql_sha256"] == registry.sql_sha256(sql)
        assert set(manifest["snapshot_ids"]) == {"core"}
        entry = read_status(ctx.lake_root)["steps"]["marts.crafted"]
        assert entry["per_tier"] and entry["layer"] == "marts" and entry["tier_complete"] == "full"
        assert entry["tiers"]["fixture"]["status"] == "done"
        assert entry["tiers"]["fixture"]["rows"] == 5
        assert registry.mart_complete(ctx.lake_root, "fixture", spec)
        lines = [
            ln
            for ln in iter_manifest(manifest_path(ctx.lake_root, ctx.build_id))
            if ln.table == "crafted"
        ]
        assert len(lines) == 3 and {ln.schema_name for ln in lines} == {"marts"}
        assert all(ln.path.startswith("marts/fixture/crafted/subject_bucket=") for ln in lines)
        assert sum(ln.rows for ln in lines) == 5
        assert any(
            s.get("layer") == "marts" and s.get("tier") == "fixture"
            for s in read_snapshots(ctx.lake_root)
        )
        ledger = benchmarks.read(settings)
        mart_lines = ledger.filter(ledger["kind"] == "mart")
        assert mart_lines.height == 1
        assert mart_lines["step"][0] == "marts.crafted" and mart_lines["ok"][0]
        assert mart_lines["rows"][0] == 5 and mart_lines["files"][0] == 3

        # a rebuild is idempotent: same files, same bytes
        before = {f.name + f.parent.name: f.read_bytes() for f in files}
        registry.write_mart(ctx, spec, sql)
        after = {
            f.name + f.parent.name: f.read_bytes()
            for f in registry.mart_files(dest, partitioned=True)
        }
        assert after == before

        # a DuckDB failure: the published mart stays, the attempt is recorded as failed
        with pytest.raises(MartBuildError, match="crafted"):
            registry.write_mart(ctx, spec, "SELECT no_such_column FROM crafted_base")
        assert registry.mart_files(dest, partitioned=True) == files
        assert not Path(str(dest) + ".new").exists()
        entry = read_status(ctx.lake_root)["steps"]["marts.crafted"]
        assert entry["tiers"]["fixture"]["status"] == "failed"
        assert entry["tier_complete"] is None
        assert not registry.mart_complete(ctx.lake_root, "fixture", spec)
        failed = benchmarks.read(settings)
        assert failed.filter((failed["kind"] == "mart") & ~failed["ok"]).height == 1
    finally:
        con.close()


def test_catalog_extension_flags_a_stale_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = config.Settings(data_root=tmp_path / "root")
    spec = MartSpec.model_validate(_CRAFTED)
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE crafted_base AS SELECT * FROM (VALUES (90000007, 1, 1.0), "
            "(90000008, 2, 2.0)) AS s(subject_id, stay_id, v)"
        )
        ctx = _crafted_ctx(settings, con)
        registry.write_mart(ctx, spec, registry.mart_sql(spec))
        con.execute("CREATE SCHEMA meta")
        con.execute(
            "CREATE TABLE meta.catalog_info AS SELECT ? AS lake_root, 11 AS k_default",
            [str(ctx.lake_root)],
        )
        monkeypatch.setattr(registry, "packaged_registry", lambda: registry.MartRegistry((spec,)))
        registry.register_marts(con, "fixture")
        row = con.execute(
            "SELECT built, stale, rows, rows_suppressed, view FROM meta.mart_tables"
        ).fetchone()
        assert row == (True, False, None, True, "marts.crafted"), "2 rows: a small cell"
        names = [r[0] for r in con.execute("DESCRIBE marts.crafted").fetchall()]
        assert names == list(spec.column_names)
        # the packaged spec gains a column after the build: no failure, the view keeps the
        # built columns, the row says stale and a warning names the --force rebuild
        doc = {**_CRAFTED, "columns": [*_CRAFTED["columns"], dict(_CRAFTED["columns"][3])]}
        doc["columns"][-1] = {**doc["columns"][-1], "name": "w"}
        edited = MartSpec.model_validate(doc)
        monkeypatch.setattr(registry, "packaged_registry", lambda: registry.MartRegistry((edited,)))
        with caplog.at_level(logging.WARNING, logger=registry.__name__):
            registry.register_marts(con, "fixture")
        assert con.execute("SELECT stale FROM meta.mart_tables").fetchone() == (True,)
        names = [r[0] for r in con.execute("DESCRIBE marts.crafted").fetchall()]
        assert names == list(spec.column_names) and "w" not in names
        assert "--force" in caplog.text
    finally:
        con.close()


def test_binding_refuses_a_missing_concept(tmp_path: Path) -> None:
    from mimicwarehouse.loader.manifest import read_status

    settings = config.Settings(data_root=tmp_path / "root")
    con = duckdb.connect()
    try:
        ctx = _crafted_ctx(settings, con)
        with pytest.raises(MartBuildError, match="not staged for tier fixture"):
            first_day.run_first_day(load_dag().step(first_day.STEP), ctx)
        entry = read_status(ctx.lake_root)["steps"][f"marts.{NAME}"]
        assert entry["tiers"]["fixture"]["status"] == "failed"
        assert entry["tiers"]["fixture"]["error_class"] == "MartBuildError"
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 3. The session fixture lake (conftest: the whole DAG, the catalog)
# ---------------------------------------------------------------------------


def test_session_lake_mart(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    con = fixture_lake_catalog
    spec = first_day.spec()
    described = [(str(r[0]), str(r[1])) for r in con.execute(f"DESCRIBE marts.{NAME}").fetchall()]
    assert described == [(c.name, c.type) for c in spec.expanded], "the declared contract"
    n, n_stays, n_icu = con.execute(
        f"SELECT count(*), count(DISTINCT stay_id), "
        f"(SELECT count(*) FROM mimiciv_icu.icustays) FROM marts.{NAME}"
    ).fetchone() or (0, 0, -1)
    assert n == n_stays == n_icu and n > 0, "one row per ICU stay"
    lo, hi = _temperature_bounds()
    row = con.execute(
        "SELECT max(admission_age), "
        "count(*) FILTER (WHERE (admission_age >= 91) IS DISTINCT FROM age_capped), "
        f"count(*) FILTER (WHERE temperature_mean < {lo} OR temperature_mean > {hi}), "
        "count(*) FILTER (WHERE subject_bucket <> subject_id % 100), "
        "count(*) FILTER (WHERE vent_invasive_first_day IS NULL OR vasopressor_first_day IS NULL "
        "OR rrt_first_day IS NULL OR sepsis3 IS NULL), "
        "count(temperature_mean), count(heart_rate_mean), count(sofa), count(first_service), "
        "count(*) FILTER (WHERE kdigo_max_stage_first_day NOT BETWEEN 0 AND 3) "
        f"FROM marts.{NAME}"
    ).fetchone()
    assert row is not None
    max_age, age_mismatch, temp_out, bucket_bad, null_flags, n_temp, n_hr, n_sofa, n_svc, kd = row
    assert max_age is not None and max_age <= 91
    assert age_mismatch == 0 and temp_out == 0 and bucket_bad == 0 and null_flags == 0
    assert n_temp > 0 and n_hr > 0 and n_sofa > 0 and n_svc > 0 and kd == 0
    comment = con.execute(
        "SELECT comment FROM duckdb_views() WHERE schema_name = 'marts' AND view_name = ?", [NAME]
    ).fetchone()
    assert comment is not None and "EP-55" in str(comment[0])
    col_comment = con.execute(
        "SELECT comment FROM duckdb_columns() WHERE schema_name = 'marts' AND table_name = ? "
        "AND column_name = 'temperature_mean'",
        [NAME],
    ).fetchone()
    assert col_comment is not None and str(col_comment[0]).endswith("[degC]")
    # the registry tables
    tables = con.execute(
        "SELECT mart, built, stale, rows, files, n_columns, spec_sha256, view "
        "FROM meta.mart_tables WHERE mart = ?",
        [NAME],
    ).fetchall()
    files_n = tables[0][4]
    assert tables == [(NAME, True, False, n, files_n, N_COLUMNS, spec.spec_sha256, f"marts.{NAME}")]
    columns = con.execute(
        'SELECT "column", duckdb_type, is_key FROM meta.mart_columns WHERE mart = ? '
        "ORDER BY ordinal",
        [NAME],
    ).fetchall()
    assert [(c, t) for c, t, _k in columns] == [(c.name, c.type) for c in spec.expanded]
    assert {c for c, _t, key in columns if key} == {*spec.key, "subject_bucket"}
    # the files: one sorted file per bucket, the files the catalog view reads
    lake_root = fixture_lake_settings.lake_root("fixture")
    dest = registry.mart_dir(lake_root, "fixture", NAME)
    files = registry.mart_files(dest, partitioned=True)
    assert len(files) == files_n and files
    buckets = {
        int(r[0])
        for r in con.execute(f"SELECT DISTINCT subject_bucket FROM marts.{NAME}").fetchall()
    }
    assert {int(f.parent.name.split("=")[1]) for f in files} == buckets
    manifest = registry.read_mart_manifest(lake_root, "fixture", NAME)
    assert manifest is not None and set(manifest["snapshot_ids"]) == {"core", "derived"}


def test_session_lake_safe_query_and_dictionary(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    from mimicwarehouse.catalog.dictionary import render_dictionary
    from mimicwarehouse.safe import SafeQueryRefused, safe_query

    result = safe_query(
        f"SELECT count(*) AS n FROM marts.{NAME}",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep55",
    )
    assert result.n_rows == 1 and int(result.df["n"][0]) > 0
    by_era = safe_query(
        f"SELECT era, count(*) AS n, avg(heart_rate_mean) AS hr FROM marts.{NAME} GROUP BY era",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep55",
    )
    assert by_era.n_rows + by_era.rows_suppressed >= 1
    with pytest.raises(SafeQueryRefused):
        safe_query(
            f"SELECT stay_id, heart_rate_mean FROM marts.{NAME}",
            tier="fixture",
            settings=fixture_lake_settings,
            actor="test_ep55",
        )
    registry_rows = safe_query(
        f"SELECT mart, n_columns FROM meta.mart_tables WHERE mart = '{NAME}'",
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep55",
    )
    assert registry_rows.df.rows() == [(NAME, N_COLUMNS)], "a registry read needs no count"
    text, info = render_dictionary(fixture_lake_catalog)
    assert "\n## marts\n" in text and f"\n### marts.{NAME}\n" in text
    section = text.split(f"### marts.{NAME}", 1)[1]
    for column in first_day.spec().column_names:
        assert f"\n| {column} |" in section, column
    assert info.marts == len(registry.load_registry()), "marts count apart from the tables"
    assert info.columns >= N_COLUMNS


# ---------------------------------------------------------------------------
# 4. The dev tier (aggregates through safe_query only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_mart_through_safe_query(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh build --tier dev --tag marts` (EP-55) first"

    def one(sql: str) -> dict[str, Any]:
        result = safe_query(sql, tier="dev", settings=settings, actor="test_ep55")
        assert result.n_rows == 1, f"{sql!r}: a row was withheld (a small cell) or missing"
        return result.df.row(0, named=True)

    built = safe_query(
        "SELECT mart, built, rows, n_columns FROM meta.mart_tables",
        tier="dev",
        settings=settings,
        actor="test_ep55",
    ).df.rows()
    assert (NAME, True) in [(m, b) for m, b, _r, _n in built], remedy
    counts = one(f"SELECT count(*) AS n, count(DISTINCT stay_id) AS n_stays FROM marts.{NAME}")
    stays = one("SELECT count(*) AS n FROM mimiciv_icu.icustays")
    assert counts["n"] == counts["n_stays"] == stays["n"], "one row per ICU stay"
    lo, hi = _temperature_bounds()
    checks = one(
        "SELECT count(*) AS n, max(admission_age) AS max_age, "
        f"count(*) FILTER (WHERE temperature_mean < {lo} OR temperature_mean > {hi}) AS n_temp_out "
        f"FROM marts.{NAME}"
    )
    assert checks["max_age"] <= 91 and checks["n_temp_out"] == 0
    print(f"dev: marts.{NAME} {counts['n']:,} rows = mimiciv_icu.icustays")


@pytest.mark.tier("dev")
def test_dev_build_wall_time_from_the_ledger(dev_catalog: Path) -> None:
    from mimicwarehouse.dag import benchmarks

    settings = config.load_settings()
    ledger = benchmarks.read(settings)
    lines = ledger.filter(
        (ledger["kind"] == "mart")
        & (ledger["tier"] == "dev")
        & (ledger["step"] == first_day.STEP)
        & ledger["ok"]
    )
    assert lines.height >= 1, "run `mwh build --tier dev --tag marts` (EP-55) first"
    wall = float(lines["wall_s"][-1])
    assert wall < 600.0, f"the dev build took {wall:.1f} s (target < 10 min)"
    print(f"dev: {first_day.STEP} wall {wall:.1f} s, rows {lines['rows'][-1]}")


def test_methods_and_docstrings_name_the_split() -> None:
    doc = registry.__doc__ or ""
    assert "meta.mart_tables" in doc and "write_mart" in doc
    import mimicwarehouse.marts as marts_pkg

    assert "EP-175" in (marts_pkg.__doc__ or ""), "the itemid rollups moved to EP-175"
