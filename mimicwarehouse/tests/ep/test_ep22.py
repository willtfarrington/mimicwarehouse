"""EP-22 — demo tier (MIMIC-IV Demo 2.2 + ED Demo).

Fixture tier (default, no network): the fetcher against a fake PhysioNet served by
monkeypatching :func:`mimicwarehouse.demo._urlopen` — matching checksums pass and produce
a valid ``source.yaml``, a corrupted file is refused **and deleted**, a second run skips
already-verified files; the sums parser refuses unsafe paths; the spec carries ``demo``
on every stage step and derives demo source paths; ``resolve_raw_root("demo")`` points at
``ext/demo/mimic-iv-demo-2.2`` and names ``mwh demo fetch`` when it is missing; a full
**demo-tier build** over gzipped fixture CSVs (2.2-shaped identity layout, ids >= 90M)
yields ``warehouse/demo.duckdb`` with ``meta.catalog_info.tier = "demo"``, writes only
under ``lake/demo``, and ``mwh sql --tier demo --count`` works; a **synthetic
non-identity column map** (built here — the shipped ``demo_2_2`` map is the identity,
EP-170 amendment 2) stages a 2.2-shaped ``admissions.csv.gz`` into 3.1 columns and
records ``map_notes`` in the manifest line, ``status.json`` and the catalog.

``@pytest.mark.demo`` (opt-in via ``--with-demo`` / ``PYTEST_DEMO=1``, needs the fetched
data): the real demo catalog holds every demo-tier stage table, ``mimiciv_hosp.patients``
counts exactly 100 (a published property of the demo), and the ED demo directory
re-verifies against its ``SHA256SUMS.txt``. Everything asserted or printed is file names,
counts, hashes, schemas and metadata — the only row values touched are the synthetic ones
this test writes itself (ids >= 90 000 000).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse import demo as demo_mod
from mimicwarehouse.cli import app
from mimicwarehouse.dag import runner
from mimicwarehouse.dag.spec import DagError, Step, load_dag
from mimicwarehouse.demo import DemoFetchError

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_22

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    # the demo tier carries the full free-space guard (min_free_gb_for); tests must not
    # depend on 100 GB free on the temp volume (the documented MWH_MIN_FREE_GB knob)
    monkeypatch.setenv("MWH_MIN_FREE_GB", "1")
    config.configure()
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# Fake PhysioNet — the fetcher's single network seam, monkeypatched
# ---------------------------------------------------------------------------

_BODY = b"subject_id\n90000001\n"  # synthetic, fixture-band — never a real row


def _fake_urls(corrupt: str | None = None) -> dict[str, bytes]:
    """``{url: bytes}`` for both demo datasets: SHA256SUMS + LICENSE + data files whose
    hashes match the sums; ``corrupt`` swaps one URL's served bytes without touching the
    sums (a checksum mismatch)."""
    data_files: dict[str, dict[str, bytes]] = {
        "mimic-iv-demo": {
            "hosp/admissions.csv.gz": gzip.compress(_BODY),
            "hosp/patients.csv.gz": gzip.compress(_BODY),
            "icu/icustays.csv.gz": gzip.compress(_BODY),
            "demo_subject_id.csv": _BODY,  # the top-level index file case
        },
        "mimic-iv-ed-demo": {"ed/edstays.csv.gz": gzip.compress(_BODY)},
    }
    license_body = b"Open Data Commons Open Database License (ODbL) v1.0 (fake for tests)\n"
    urls: dict[str, bytes] = {}
    for dataset in demo_mod.DEMO_DATASETS:
        listed = {**data_files[dataset.name], demo_mod.LICENSE_FILENAME: license_body}
        sums = "".join(
            f"{hashlib.sha256(body).hexdigest()}  {rel}\n" for rel, body in sorted(listed.items())
        )
        urls[dataset.url + demo_mod.CHECKSUMS_FILENAME] = sums.encode()
        for rel, body in listed.items():
            urls[dataset.url + rel] = body
    if corrupt is not None:
        urls[corrupt] = b"corrupted bytes that do not match the sums\n"
    return urls


@pytest.fixture
def fake_physionet(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``_urlopen`` and return ``(request_counts, set_urls)``."""
    counts: dict[str, int] = {}
    served: dict[str, bytes] = {}

    def set_urls(urls: dict[str, bytes]) -> None:
        served.clear()
        served.update(urls)

    def fake_urlopen(url: str) -> io.BytesIO:
        counts[url] = counts.get(url, 0) + 1
        if url not in served:
            import urllib.error

            raise urllib.error.URLError(f"fake 404: {url}")
        return io.BytesIO(served[url])

    monkeypatch.setattr(demo_mod, "_urlopen", fake_urlopen)
    monkeypatch.setattr(demo_mod, "RETRY_BASE_SLEEP_S", 0.0)
    return counts, set_urls


# ---------------------------------------------------------------------------
# 1. Fetcher: verified layout + register
# ---------------------------------------------------------------------------


def test_fetch_verifies_and_writes_register(data_root: Path, fake_physionet) -> None:
    _, set_urls = fake_physionet
    set_urls(_fake_urls())
    settings = config.get_settings()
    register, counts = demo_mod.fetch_all(settings)

    clinical = demo_mod.demo_raw_root(settings)
    assert clinical == data_root / "ext" / "demo" / "mimic-iv-demo-2.2"
    assert (clinical / "hosp" / "admissions.csv.gz").is_file()
    assert (clinical / "demo_subject_id.csv").is_file()
    assert (clinical / demo_mod.CHECKSUMS_FILENAME).is_file()
    ed = demo_mod.demo_root(settings) / demo_mod.DEMO_ED_DIRNAME
    assert (ed / "ed" / "edstays.csv.gz").is_file()
    assert (ed / demo_mod.CHECKSUMS_FILENAME).is_file()

    # the register round-trips through the pydantic model (item 1: source.yaml validates)
    path = demo_mod.register_path(settings)
    assert path == data_root / "ext" / "demo" / "source.yaml"
    loaded = demo_mod.load_register(path)
    assert loaded == register
    assert [d.name for d in loaded.datasets] == ["mimic-iv-demo", "mimic-iv-ed-demo"]
    for record in loaded.datasets:
        assert record.verified is True
        assert record.license == "ODbL-1.0"
        assert record.version == "2.2"
        assert all(f.bytes > 0 and "/" not in f.sha256 for f in record.files)
    assert len(loaded.datasets[0].files) == 5  # 4 data files + LICENSE.txt (listed in sums)
    # every data file was downloaded; the two LICENSE files were fetched up front and then
    # skip as already-verified when the sums loop reaches them
    assert sum(c.downloaded for c in counts) == 5
    assert sum(c.skipped for c in counts) == 2


def test_fetch_refuses_and_deletes_corrupted(data_root: Path, fake_physionet) -> None:
    _, set_urls = fake_physionet
    corrupt_url = demo_mod.DEMO_DATASETS[0].url + "hosp/admissions.csv.gz"
    set_urls(_fake_urls(corrupt=corrupt_url))
    settings = config.get_settings()
    with pytest.raises(DemoFetchError, match="sha256 mismatch"):
        demo_mod.fetch_all(settings)
    target = demo_mod.demo_raw_root(settings) / "hosp" / "admissions.csv.gz"
    assert not target.exists(), "the mismatching file is deleted"
    assert not target.with_name(target.name + ".part").exists(), "no .part left behind"
    assert not demo_mod.register_path(settings).exists(), "no register claims verified"


def test_second_run_skips_verified_files(data_root: Path, fake_physionet) -> None:
    counts, set_urls = fake_physionet
    set_urls(_fake_urls())
    settings = config.get_settings()
    demo_mod.fetch_all(settings)
    demo_mod.fetch_all(settings)

    sums_urls = {d.url + demo_mod.CHECKSUMS_FILENAME for d in demo_mod.DEMO_DATASETS}
    for url, n in counts.items():
        if url in sums_urls:
            assert n == 2, f"{url}: the sums are re-fetched every run (they are the truth)"
        else:
            assert n == 1, f"{url}: an already-verified file must be skipped on the second run"
    assert demo_mod.load_register(demo_mod.register_path(settings)).datasets[0].verified


def test_fetch_and_status_cli(data_root: Path, fake_physionet) -> None:
    _, set_urls = fake_physionet
    set_urls(_fake_urls())
    cli = helpers.cli_runner()

    before = cli.invoke(app, ["demo", "status"])
    assert before.exit_code == 2 and "mwh demo fetch" in before.output

    fetched = cli.invoke(app, ["demo", "fetch"])
    assert fetched.exit_code == 0, fetched.output
    assert "mimic-iv-demo 2.2" in fetched.output and "verified" in fetched.output

    status = cli.invoke(app, ["demo", "status"])
    assert status.exit_code == 0, status.output
    assert "mimic-iv-ed-demo" in status.output and "ODbL-1.0" in status.output


def test_sums_parser_refuses_bad_entries() -> None:
    sha = "0" * 64
    for line in (f"{sha}  ../evil.csv", f"{sha}  /abs/path.csv", f"{sha}  tool.exe", "not-a-line"):
        with pytest.raises(DemoFetchError):
            demo_mod.parse_sha256sums(line, where="test")
    with pytest.raises(DemoFetchError, match="no checksum entries"):
        demo_mod.parse_sha256sums("", where="test")
    entries = demo_mod.parse_sha256sums(f"{sha}  hosp/admissions.csv.gz\n", where="test")
    assert entries == [(sha, "hosp/admissions.csv.gz")]


# ---------------------------------------------------------------------------
# 2. Spec + runner wiring: demo tiers, demo source derivation, raw root
# ---------------------------------------------------------------------------


def test_every_stage_step_carries_demo_tier() -> None:
    dag = load_dag()
    for step in dag.steps:
        if step.kind == "stage":
            assert "demo" in step.tiers, f"{step.name}: hosp/icu stage steps run on demo (EP-22)"
    ordered = dag.ordered(tier="demo")
    kinds = {s.kind for s in ordered}
    assert kinds == {"stage", "catalog"}
    assert len([s for s in ordered if s.kind == "stage"]) == len(
        [s for s in dag.steps if s.kind == "stage"]
    )


def test_demo_relative_source_derivation() -> None:
    step = load_dag().step("stage.mimiciv_hosp.patients")
    assert step.source == "mimic-iv-3.1/hosp/patients.csv"
    assert step.demo_relative_source == "hosp/patients.csv"  # dataset dir stripped; .gz fallback
    explicit = Step(
        name="stage.x",
        kind="stage",
        schema="mimiciv_hosp",
        table="patients",
        source="mimic-iv-3.1/hosp/patients.csv",
        demo_source="hosp/renamed_patients.csv.gz",
    )
    assert explicit.demo_relative_source == "hosp/renamed_patients.csv.gz"
    with pytest.raises(Exception, match="relative posix"):
        Step(
            name="stage.bad",
            kind="stage",
            schema="mimiciv_hosp",
            table="patients",
            source="mimic-iv-3.1/hosp/patients.csv",
            demo_source="hosp\\patients.csv.gz",
        )


def test_resolve_raw_root_demo(data_root: Path) -> None:
    settings = config.get_settings()
    with pytest.raises(DagError, match="mwh demo fetch"):
        runner.resolve_raw_root(settings, "demo")
    root = demo_mod.demo_raw_root(settings)
    root.mkdir(parents=True)
    assert runner.resolve_raw_root(settings, "demo") == root


def test_demo_build_refuses_credentialed_lake(data_root: Path) -> None:
    settings = config.get_settings()
    with pytest.raises(config.UnsafeLocationError, match="credentialed lake"):
        config.assert_not_credentialed_lake("demo", settings.layout["lake"], settings)
    config.assert_not_credentialed_lake("demo", settings.lake_root("demo"), settings)  # fine


# ---------------------------------------------------------------------------
# 3. Demo-tier build end to end (fixture CSVs gzipped into the 2.2 demo layout)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A demo-tier lake + ``demo.duckdb`` built by the runner over a fake demo raw root:
    the committed fixture CSVs gzipped into ``ext/demo/mimic-iv-demo-2.2/{hosp,icu}/`` —
    exactly the 2.2 layout (identity headers, ids >= 90M), no network."""
    from mimicwarehouse.config import Settings
    from mimicwarehouse.fixtures.write import default_out_dir

    root = tmp_path_factory.mktemp("ep22-demo-lake")
    settings = Settings(data_root=root, min_free_gb=1)
    src = default_out_dir() / "mimic-iv-3.1"
    dest = demo_mod.demo_raw_root(settings)
    for module in ("hosp", "icu"):
        (dest / module).mkdir(parents=True, exist_ok=True)
        for csv_path in sorted((src / module).glob("*.csv")):
            with (
                csv_path.open("rb") as fin,
                gzip.open(dest / module / f"{csv_path.name}.gz", "wb") as fout,
            ):
                shutil.copyfileobj(fin, fout)
    result = runner.run(load_dag(), "demo", settings=settings)
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def test_demo_build_catalog_and_lake_root(demo_lake: Settings) -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    assert demo_lake.catalog_path("demo").is_file(), "warehouse/demo.duckdb published"
    # the demo lake root was used; the credentialed lake was never touched (ARCH-3)
    assert (demo_lake.lake_root("demo") / "manifests" / "status.json").is_file()
    assert not (demo_lake.layout["lake"] / "manifests").exists()

    con = open_catalog("demo", settings=demo_lake)
    try:
        (tier,) = con.execute("SELECT tier FROM meta.catalog_info").fetchone()  # type: ignore[misc]
        assert tier == "demo"
        rows = con.execute(
            'SELECT "schema" || \'.\' || "table", kind, map_notes FROM meta.catalog_tables'
        ).fetchall()
        listed = {qn: (kind, notes) for qn, kind, notes in rows}
        staged = {
            s.qualified_table
            for s in load_dag().steps
            if s.kind == "stage" and (not s.tiers or "demo" in s.tiers)
        }
        present = {qn for qn, (kind, _) in listed.items() if kind != "missing"}
        assert staged <= present, "every demo-tier stage table is in the demo catalog"
        # the shipped demo_2_2 map is the identity (EP-170 amendment 2): no map_notes
        assert all(notes is None for _, notes in listed.values())
        (n,) = con.execute(f"SELECT count(*) FROM {HOSP}.patients").fetchone()  # type: ignore[misc]
        assert n == 120, "the gzipped fixture ships 120 synthetic subjects"
    finally:
        con.close()


def test_demo_sql_count_cli(demo_lake: Settings) -> None:
    cli = helpers.cli_runner()
    result = cli.invoke(
        app,
        [
            "--data-root",
            str(demo_lake.data_root),
            "sql",
            "--tier",
            "demo",
            "--count",
            f"{HOSP}.patients",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "count(*) = 120" in result.output


# ---------------------------------------------------------------------------
# 4. Synthetic non-identity column map (EP-170 amendment 2): 2.2 -> 3.1 + map_notes
# ---------------------------------------------------------------------------


def _synthetic_map():
    """A deliberately lossy 2.2-shaped map for admissions, built in the test (the shipped
    demo_2_2 map is the identity): admit_provider_id absent (-> NULL), race named
    ethnicity (the real v2.0 rename), plus a 2.2-only column that is dropped."""
    from mimicwarehouse.schema.contract import ColumnMap, TableMap

    return ColumnMap(
        name="test_2_2",
        description="synthetic non-identity map for the EP-22 unit test (never shipped)",
        derivation="built inline in tests/ep/test_ep22.py",
        schemas=("mimiciv_hosp",),
        tables={
            f"{HOSP}.admissions": TableMap(
                added_in_3_1=("admit_provider_id",),
                renamed={"ethnicity": "race"},
                dropped_in_3_1=("edcharttime_v22",),
            )
        },
    )


def test_synthetic_column_map_stages_to_contract_columns(
    tmp_path: Path, contract: Contract
) -> None:
    import duckdb

    from mimicwarehouse.config import Settings
    from mimicwarehouse.loader.buckets import stage_partitioned
    from mimicwarehouse.loader.manifest import read_status
    from mimicwarehouse.loader.paths import table_dir

    table = contract.table(HOSP, "admissions")
    cmap = _synthetic_map()

    header: list[str] = []
    for name in table.column_names:  # 2.2-shaped header from the map's declared differences
        if name == "admit_provider_id":
            continue
        header.append("ethnicity" if name == "race" else name)
    header.append("edcharttime_v22")
    values = {
        "subject_id": "90000001",
        "hadm_id": "91000001",
        "admittime": "2130-01-01 10:00:00",
        "dischtime": "2130-01-03 12:00:00",
        "admission_type": "URGENT",
        "admission_location": "EMERGENCY ROOM",
        "insurance": "Other",
        "language": "English",
        "marital_status": "SINGLE",
        "ethnicity": "WHITE",
        "hospital_expire_flag": "0",
        "edcharttime_v22": "2130-01-01 08:00:00",
    }
    row = ",".join(values.get(h, "") for h in header)
    source = tmp_path / "admissions.csv.gz"
    with gzip.open(source, "wt", encoding="utf-8", newline="\n") as f:
        f.write(",".join(header) + "\n" + row + "\n")

    settings = Settings(data_root=tmp_path / "root", min_free_gb=1)
    lake_root = settings.lake_root("demo")
    dest = table_dir(lake_root, HOSP, "admissions")
    con = duckdb.connect()
    try:
        result = stage_partitioned(
            con,
            table,
            source,
            dest,
            lake_root=lake_root,
            build_id="ep22-test",
            settings=settings,
            column_map=cmap,
        )
        assert result.rows == 1 and result.rejects == 0
        expected_notes = {"dropped": ["edcharttime_v22"], "filled_null": ["admit_provider_id"]}
        assert result.manifest_lines[0].map_notes == expected_notes

        glob = (dest / "**" / "*.parquet").as_posix()
        described = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{glob}')").fetchall()
        staged_cols = [d[0] for d in described if d[0] != "subject_bucket"]
        assert staged_cols == list(table.column_names), "3.1 contract columns, in order"
        (filled,) = con.execute(  # the absent 2.2 column became a typed NULL
            f"SELECT count(*) FROM read_parquet('{glob}') WHERE admit_provider_id IS NOT NULL"
        ).fetchone()  # type: ignore[misc]
        assert filled == 0
        (renamed,) = con.execute(  # ethnicity landed in race
            f"SELECT count(*) FROM read_parquet('{glob}') WHERE race IS NOT NULL"
        ).fetchone()  # type: ignore[misc]
        assert renamed == 1
    finally:
        con.close()

    entry = read_status(lake_root)["steps"][f"{HOSP}.admissions"]
    assert entry["map_notes"] == expected_notes


def test_identity_map_records_no_notes(tmp_path: Path, contract: Contract) -> None:
    from mimicwarehouse.loader.csv import plan_csv_read, plan_map_notes

    cmap = contract.column_map("demo_2_2")
    for qn in (f"{HOSP}.admissions", f"{ICU}.icustays"):
        assert cmap.table_map(qn).is_identity, "the shipped demo map is the identity (EP-170)"
    # ...except procedureevents, whose real 2.2 header ships uppercase MetaVision names —
    # found by the first EP-22 demo build (dated note in demo_2_2_to_3_1.yaml); a pure
    # rename, so it is lossless and still records no map_notes
    pe = cmap.table_map(f"{ICU}.procedureevents")
    assert pe.renamed == {"ORIGINALAMOUNT": "originalamount", "ORIGINALRATE": "originalrate"}
    assert pe.added_in_3_1 == () and pe.dropped_in_3_1 == ()
    table = contract.table(HOSP, "admissions")
    applied = cmap.apply(table, list(table.column_names))
    assert applied == {c: c for c in table.column_names}
    # plan-level: an identity header yields no map notes (the dormant machinery stays quiet)
    source = tmp_path / "admissions.csv"
    source.write_text(",".join(table.column_names) + "\n", encoding="utf-8")
    plan = plan_csv_read(source, table, cmap)
    assert plan.dropped == () and plan.supplied == tuple(table.column_names)
    assert plan_map_notes(table, plan) is None


# ---------------------------------------------------------------------------
# 5. Opt-in demo tests (need `mwh demo fetch` + `mwh build --tier demo`)
# ---------------------------------------------------------------------------


@pytest.mark.demo
def test_real_demo_catalog() -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    settings = config.load_settings()
    con = open_catalog("demo", settings=settings)
    try:
        (tier,) = con.execute("SELECT tier FROM meta.catalog_info").fetchone()  # type: ignore[misc]
        assert tier == "demo"
        rows = con.execute(
            'SELECT "schema" || \'.\' || "table", kind FROM meta.catalog_tables'
        ).fetchall()
        present = {qn for qn, kind in rows if kind != "missing"}
        staged = {
            s.qualified_table
            for s in load_dag().steps
            if s.kind == "stage" and (not s.tiers or "demo" in s.tiers)
        }
        assert staged <= present, "every demo-tier stage table is present in demo.duckdb"
        (n,) = con.execute(f"SELECT count(*) FROM {HOSP}.patients").fetchone()  # type: ignore[misc]
        assert n == 100, "a published property of the MIMIC-IV demo (100 subjects)"
    finally:
        con.close()


@pytest.mark.demo
def test_ed_demo_fetched_and_reverified() -> None:
    settings = config.load_settings()
    register = demo_mod.load_register(demo_mod.register_path(settings))
    by_name = {d.name: d for d in register.datasets}
    ed = by_name["mimic-iv-ed-demo"]
    assert ed.verified and ed.license == "ODbL-1.0" and ed.version == "2.2"
    ed_dir = demo_mod.demo_root(settings) / demo_mod.DEMO_ED_DIRNAME
    sums = demo_mod.parse_sha256sums(
        (ed_dir / demo_mod.CHECKSUMS_FILENAME).read_text(encoding="utf-8"),
        where=demo_mod.CHECKSUMS_FILENAME,
    )
    for sha, rel in sums:  # the ED demo is ~100 KB: re-verify every checksum
        actual = hashlib.sha256((ed_dir / rel).read_bytes()).hexdigest()
        assert actual == sha, f"{rel}: on-disk file no longer matches SHA256SUMS.txt"
