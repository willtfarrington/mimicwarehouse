"""EP-20 — stage dimensions + small hosp/icu tables.

Fixture tier (default): the contract facts the brief pins (load_class small,
partitioning, sort keys), the coverage assertion (every mimiciv_hosp/mimiciv_icu
contract table is assigned to exactly one staging brief; mimiciv_ed / mimiciv_note have
no stage step until EP-142 / EP-148 — DESIGN §5 note), the spec shape of the 20 steps,
and one real ``mwh build --tier fixture --tag small`` into a temp data root (rows equal
the committed fixture manifest's rows, rejects 0, EP-18 layout, ledger lines).

``tier("dev")`` / ``tier("full")``-marked: reconciliation of the real lake's
``status.json`` counts against mimic-code ``validate.sql`` (EP-10 ``expected_counts``)
and the EP-10 raw manifest. Everything printed or asserted is counts, paths and status
— never a row, never an identifier value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.fixtures import write as fixtures_write
from mimicwarehouse.inventory import expected_counts, fmt_int, load_raw_manifest
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.schema.contract import SUBJECT_KEY, Contract

pytestmark = pytest.mark.ep_20

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"

#: Brief item 1 — the 20 EP-20 tables with the sort keys that must follow ``subject_id``
#: in the contract; ``None`` marks the seven unpartitioned dimension tables.
EP20_TABLES: dict[str, tuple[str, ...] | None] = {
    f"{HOSP}.patients": (),
    f"{HOSP}.admissions": ("admittime",),
    f"{HOSP}.transfers": ("intime", "transfer_id"),
    f"{HOSP}.services": ("transfertime",),
    f"{HOSP}.diagnoses_icd": ("hadm_id", "seq_num"),
    f"{HOSP}.procedures_icd": ("chartdate", "hadm_id", "seq_num"),
    f"{HOSP}.drgcodes": ("hadm_id",),
    f"{HOSP}.hcpcsevents": ("chartdate", "hadm_id", "seq_num"),
    f"{HOSP}.omr": ("chartdate", "seq_num"),
    f"{HOSP}.poe_detail": ("poe_id", "field_name"),
    f"{HOSP}.d_labitems": None,
    f"{HOSP}.d_hcpcs": None,
    f"{HOSP}.d_icd_diagnoses": None,
    f"{HOSP}.d_icd_procedures": None,
    f"{HOSP}.provider": None,
    f"{ICU}.icustays": ("intime",),
    f"{ICU}.procedureevents": ("starttime", "orderid"),
    f"{ICU}.outputevents": ("charttime", "itemid"),
    f"{ICU}.d_items": None,
    f"{ICU}.caregiver": None,
}

#: The large hosp/icu tables the later stage briefs own (EP-20 Out of scope).
LATER_BRIEFS: dict[str, frozenset[str]] = {
    "EP-23": frozenset({f"{HOSP}.labevents"}),
    "EP-24": frozenset({f"{HOSP}.emar", f"{HOSP}.emar_detail"}),
    "EP-25": frozenset(
        {
            f"{HOSP}.pharmacy",
            f"{HOSP}.prescriptions",
            f"{HOSP}.poe",
            f"{HOSP}.microbiologyevents",
        }
    ),
    "EP-26": frozenset({f"{ICU}.chartevents"}),
    "EP-27": frozenset({f"{ICU}.inputevents", f"{ICU}.ingredientevents", f"{ICU}.datetimeevents"}),
}


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. Contract facts the brief pins (item 1: verify, fix with a dated note if wrong)
# ---------------------------------------------------------------------------


def test_contract_matches_brief(contract: Contract) -> None:
    for qn, sort_after in EP20_TABLES.items():
        table = contract.table(qn)
        assert table.load_class == "small", f"{qn}: load_class {table.load_class!r}"
        if sort_after is None:
            assert not table.partitioned, f"{qn}: brief says unpartitioned"
        else:
            assert table.partitioned, f"{qn}: brief says partitioned"
            assert table.sort_keys == (SUBJECT_KEY, *sort_after), (
                f"{qn}: sort_keys {table.sort_keys!r} != ({SUBJECT_KEY!r}, *{sort_after!r})"
            )


# ---------------------------------------------------------------------------
# 2. Coverage: every hosp/icu contract table belongs to exactly one staging brief;
#    ed/note have no stage step (the negative — DESIGN §5 note, EP-142 / EP-148)
# ---------------------------------------------------------------------------


def test_every_hosp_icu_table_staged_exactly_once(contract: Contract) -> None:
    dag = load_dag()
    staged = [s.qualified_table for s in dag.steps if s.kind == "stage"]
    assert len(staged) == len(set(staged)), "a table appears in two stage steps"
    assert set(staged) == set(EP20_TABLES), "the spec declares exactly the 20 EP-20 steps"

    assignments = [frozenset(EP20_TABLES), *LATER_BRIEFS.values()]
    assigned = [qn for group in assignments for qn in group]
    assert len(assigned) == len(set(assigned)), "a table is assigned to two briefs"
    hosp_icu = {t.qualified_name for t in contract.tables if t.schema_name in (HOSP, ICU)}
    assert len(hosp_icu) == 31
    assert set(assigned) == hosp_icu, "every hosp/icu table belongs to exactly one brief"

    # the negative: the 6 ed + 4 note tables have no stage step in this spec
    excluded = {t.qualified_name for t in contract.tables} - hosp_icu
    assert len(excluded) == 10
    assert not set(staged) & excluded


# ---------------------------------------------------------------------------
# 3. Spec shape: tiers, tags, sources; the contract stays the authority (no overrides)
# ---------------------------------------------------------------------------


def test_spec_steps_carry_contract_defaults(contract: Contract) -> None:
    dag = load_dag()
    for step in (s for s in dag.steps if s.kind == "stage"):
        qn = step.qualified_table
        assert qn is not None
        table = contract.table(qn)
        assert step.tiers == ("fixture", "dev", "full"), f"{step.name}: tiers {step.tiers!r}"
        assert {"stage", "small", table.schema_name} <= set(step.tags), f"{step.name}: tags"
        assert ("dims" in step.tags) == (not table.partitioned), f"{step.name}: dims tag"
        assert step.source == f"mimic-iv-3.1/{table.csv_path}", f"{step.name}: source"
        # size_class / partitioned / sort_by come from the contract, never the spec
        assert step.size_class is None and step.partitioned is None and step.sort_by is None
    catalog = dag.step("catalog")
    assert set(catalog.depends_on) == {s.name for s in dag.steps if s.kind == "stage"}


# ---------------------------------------------------------------------------
# 4. Fixture build: all 20 staged, rows = fixture manifest rows, rejects 0, EP-18 layout
# ---------------------------------------------------------------------------


def test_fixture_build_stages_all_20(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--tag", "small"])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    status = manifest_mod.read_status(lake)["steps"]
    fixture_rows = {
        rel: info["rows"]
        for rel, info in fixtures_write.load_manifest(fixtures_write.default_out_dir())[
            "files"
        ].items()
    }
    for qn, sort_after in EP20_TABLES.items():
        table = contract.table(qn)
        entry = status[qn]
        assert entry["rows"] == fixture_rows[f"mimic-iv-3.1/{table.csv_path}"], qn
        assert entry["rejects"] == 0, qn
        assert entry["tier_complete"] == "full", qn  # fixture stages all 100 buckets
        assert entry["dev_ready"] is True, qn
        tdir = loader_paths.table_dir(lake, table.schema_name, table.name)
        if sort_after is None:  # dim: exactly one file, no subject_bucket level
            assert sorted(p.name for p in tdir.iterdir()) == ["part-0.parquet"], qn
        else:
            bucket_dirs = [
                p for p in tdir.iterdir() if p.is_dir() and p.name.startswith("subject_bucket=")
            ]
            assert bucket_dirs, qn
            for bdir in bucket_dirs:
                assert sorted(p.name for p in bdir.iterdir()) == ["part-0.parquet"], qn

    # benchmark ledger: one line per stage step + the build summary (item 5)
    ledger = benchmarks_mod.read(settings)
    steps = ledger.filter(ledger["kind"] == "stage")
    assert steps.height == len(EP20_TABLES) and all(steps["ok"].to_list())
    assert ledger.filter(ledger["kind"] == "build").height == 1


# ---------------------------------------------------------------------------
# 5. Dev tier: the dev partitions of every partitioned table exist in the real lake
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="lake")
def test_dev_partitions_present() -> None:
    settings = config.load_settings()
    lake = settings.lake_root("dev")

    # manifest rows per (table, lake-relative path); last line wins per path
    rows_by_path: dict[tuple[str, str], int] = {}
    for path in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rows_by_path[(f"{rec['schema']}.{rec['table']}", rec["path"])] = rec["rows"]

    for qn, sort_after in sorted(EP20_TABLES.items()):
        if sort_after is None:
            continue
        schema, table = qn.split(".", 1)
        tdir = loader_paths.table_dir(lake, schema, table)
        manifest_rows = 0
        for b in settings.dev_buckets:
            bdir = tdir / f"subject_bucket={b}"
            assert bdir.is_dir(), f"{qn}: dev bucket {b} missing"
            assert (bdir / "part-0.parquet").is_file(), f"{qn}: dev bucket {b} has no part"
            manifest_rows += sum(
                rows
                for (t, p), rows in rows_by_path.items()
                if t == qn and f"subject_bucket={b}/" in p
            )
        assert manifest_rows > 0, f"{qn}: no manifest rows recorded for the dev buckets"


# ---------------------------------------------------------------------------
# 6. Full tier: status.json rows == validate.sql expectation == raw manifest rows
# ---------------------------------------------------------------------------


@pytest.mark.tier("full", needs="lake")
def test_full_counts_reconcile(contract: Contract) -> None:
    settings = config.load_settings()
    lake = settings.lake_root("full")
    status = manifest_mod.read_status(lake)["steps"]
    expected = expected_counts("mimic-iv-3.1", contract)
    raw = load_raw_manifest(settings)

    failures: list[str] = []
    print(f"\n{'table':<32} {'expected':>12} {'actual':>12} ok")
    for qn in sorted(EP20_TABLES):
        table = contract.table(qn)
        entry = status.get(qn)
        assert entry is not None, f"{qn}: no status.json entry"
        assert entry.get("tier_complete") == "full", f"{qn}: tier_complete != full"
        actual = entry["rows"]
        rec = raw.for_table(table)
        raw_rows = rec.rows if rec is not None else None
        # provider / caregiver have no validate.sql count: raw-manifest rows only
        exp = expected.get(table.name) if table.expected_rows_source else None
        ok = (
            entry["rejects"] == 0
            and raw_rows is not None
            and actual == raw_rows
            and (exp is None or actual == exp)
        )
        print(f"{qn:<32} {fmt_int(exp):>12} {fmt_int(actual):>12} {'ok' if ok else 'FAIL'}")
        if not ok:
            failures.append(qn)
    assert not failures, f"count reconciliation failed for {failures}"
