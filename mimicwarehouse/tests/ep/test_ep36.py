"""EP-36 — seed/determinism policy + resource logger.

Fixture tier only (nothing reads data): ``derive_seed`` matches the brief's formula, is
stable across processes (two fresh interpreters agree with this one) and differs across
stages, salts and protocols; ``rng`` reproduces a draw sequence; ``spawn_rngs`` yields
distinct, reproducible streams; ``seed_everything`` seeds ``random`` + numpy's legacy state
and touches ``torch`` only when it is already imported (a fresh interpreter proves it never
imports it); ``sql_sample_clause`` renders the policy's ``REPEATABLE`` clause and DuckDB
reproduces the sample on synthetic in-memory rows; ``Run.seed`` derives from the run id (or
the frozen protocol id), records ``seeds`` at once and is idempotent; every run manifest
carries ``seeds`` and ``resources`` with ``peak_rss_mb > 0`` and ``wall_s > 0``; a synthetic
200 MB numpy allocation inside a run raises ``peak_rss_mb`` measurably; the peak-RSS rule
promotes ``peak_wset`` only when it grew (a stubbed psutil pins every branch); GPU fields are
``None`` without pynvml and are sampled through a stubbed ``pynvml`` (per-process and
device-level); ``ResourceLog.measure`` / ``Run.measure`` feed ``run.bench``; ``mwh runs
show`` displays ``seeds`` and ``resources`` and the ``manifests`` view exposes them;
``reproduction_block`` renders both through ``fmt_int``; the docs exist, are linked and
guard-clean; the module keeps numpy / psutil / pynvml lazy.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import types
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, fsio, guard
from mimicwarehouse import run as run_mod
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.benchmarks import BenchmarkLine
from mimicwarehouse.run import (
    DUCKDB_SEED_MAX,
    SAMPLE_INTERVAL_S,
    SEED_MAX,
    ResourceLog,
    ResourceUsage,
    RunLedgerError,
    SeedError,
    derive_seed,
    read_manifest,
    reproduction_block,
    rng,
    seed_everything,
    seed_key,
    spawn_rngs,
    sql_sample_clause,
)
from mimicwarehouse.safe import runs_db_path

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_36

DOCS = helpers.WORKSPACE / "docs"
DETERMINISM_DOC = DOCS / "methods" / "determinism.md"
PROVENANCE_DOC = DOCS / "methods" / "provenance.md"
DESIGN = helpers.WORKSPACE / "DESIGN.md"
README = helpers.WORKSPACE / "README.md"
GOTCHAS = DOCS / "gotchas.md"

#: The brief's acceptance probe (its value is recorded in the completion note).
DEMO_PROTOCOL = "demo-protocol"
DEMO_STAGE = "bootstrap"
MB = 2**20
#: A tick long enough that the sampler thread never fires inside a stubbed measurement.
IDLE_INTERVAL_S = 10.0

RESOURCE_FIELDS = frozenset(ResourceUsage.model_fields)


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


def _settings() -> Settings:
    return config.get_settings()


def _manifest_file(run: run_mod.Run) -> dict[str, Any]:
    return json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))


def _draws(generator: Any, n: int = 6) -> list[int]:
    return generator.integers(0, 1_000_000, size=n).tolist()


# ---------------------------------------------------------------------------
# 1. Seed derivation (brief item 1)
# ---------------------------------------------------------------------------


def test_derive_seed_matches_the_brief_formula_and_fits_32_bits() -> None:
    expected = int.from_bytes(hashlib.sha256(b"demo-protocol|bootstrap|0").digest()[:4], "big")
    assert derive_seed(DEMO_PROTOCOL, DEMO_STAGE) == expected
    assert seed_key(DEMO_PROTOCOL, DEMO_STAGE) == "demo-protocol|bootstrap|0"
    assert seed_key("p", "s", 3) == "p|s|3"
    assert 0 <= expected <= SEED_MAX and SEED_MAX == 2**32 - 1
    # the integer the completion note records for `derive_seed("demo-protocol", "bootstrap")`
    assert expected == 1_208_429_812
    for stage in ("bootstrap", "cv_split", "model_fit.lgbm", "x" * 100):
        assert 0 <= derive_seed(DEMO_PROTOCOL, stage) <= SEED_MAX


def test_derive_seed_is_stable_across_processes() -> None:
    code = (
        "from mimicwarehouse.run import derive_seed; "
        "print(derive_seed('demo-protocol', 'bootstrap'))"
    )
    first = helpers.fresh_interpreter(["-c", code])
    second = helpers.fresh_interpreter(["-c", code])
    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    in_process = str(derive_seed(DEMO_PROTOCOL, DEMO_STAGE))
    assert first.stdout.strip() == second.stdout.strip() == in_process


def test_derive_seed_differs_across_stages_salts_and_protocols() -> None:
    base = derive_seed(DEMO_PROTOCOL, DEMO_STAGE)
    assert derive_seed(DEMO_PROTOCOL, "cv_split") != base
    assert derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=1) != base
    salted = derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=1)
    assert salted != derive_seed(DEMO_PROTOCOL, DEMO_STAGE, 2)
    assert derive_seed("other-protocol", DEMO_STAGE) != base
    assert derive_seed("20260905T120000Z-abc123", DEMO_STAGE) != base
    assert len({derive_seed(DEMO_PROTOCOL, f"stage{i}") for i in range(200)}) == 200
    assert len({derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=s) for s in range(200)}) == 200


@pytest.mark.parametrize(
    ("protocol_id", "stage", "salt"),
    [
        ("", "bootstrap", 0),
        ("   ", "bootstrap", 0),
        ("a|b", "bootstrap", 0),
        ("a\nb", "bootstrap", 0),
        ("p", "", 0),
        ("p", "boot strap", 0),
        ("p", "boot|strap", 0),
        ("p", "../x", 0),
        ("p", "s", -1),
        ("p", "s", True),
        ("p", "s", 1.5),
    ],
)
def test_derive_seed_refuses_bad_arguments(protocol_id: Any, stage: Any, salt: Any) -> None:
    with pytest.raises(SeedError):
        derive_seed(protocol_id, stage, salt)


def test_rng_reproduces_a_draw_sequence() -> None:
    import numpy as np

    first = _draws(rng(DEMO_PROTOCOL, DEMO_STAGE))
    assert first == _draws(rng(DEMO_PROTOCOL, DEMO_STAGE))
    assert _draws(rng(DEMO_PROTOCOL, "cv_split")) != first
    assert _draws(rng(DEMO_PROTOCOL, DEMO_STAGE, salt=1)) != first
    assert isinstance(rng(DEMO_PROTOCOL, DEMO_STAGE), np.random.Generator)
    # the Generator is seeded with exactly derive_seed(...)
    assert _draws(np.random.default_rng(derive_seed(DEMO_PROTOCOL, DEMO_STAGE))) == first


def test_spawn_rngs_yields_distinct_reproducible_streams() -> None:
    workers = spawn_rngs(DEMO_PROTOCOL, "cv_split", 4)
    again = spawn_rngs(DEMO_PROTOCOL, "cv_split", 4)
    draws = [_draws(g) for g in workers]
    assert draws == [_draws(g) for g in again], "reproducible for the same scope + stage"
    assert len({tuple(d) for d in draws}) == 4, "distinct streams"
    assert _draws(rng(DEMO_PROTOCOL, "cv_split")) not in draws, "children are never the parent"
    assert [_draws(g) for g in spawn_rngs(DEMO_PROTOCOL, "cv_split", 4, salt=1)] != draws
    assert len(spawn_rngs(DEMO_PROTOCOL, "cv_split", 1)) == 1
    for bad in (0, -1, True, 2.0):
        with pytest.raises(SeedError):
            spawn_rngs(DEMO_PROTOCOL, "cv_split", bad)  # type: ignore[arg-type]


def test_seed_everything_seeds_globals_and_never_imports_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import random

    import numpy as np

    # sys.modules["torch"] = None makes any `import torch` raise; the function must not try
    monkeypatch.setitem(sys.modules, "torch", None)
    assert seed_everything(123) == {"random": True, "numpy": True, "torch": False}
    first = (random.random(), float(np.random.random()))
    seed_everything(123)
    assert (random.random(), float(np.random.random())) == first
    seed_everything(124)
    assert (random.random(), float(np.random.random())) != first
    # torch already imported -> manual_seed(seed) is called on it
    calls: list[int] = []
    fake_torch = types.ModuleType("torch")
    fake_torch.manual_seed = calls.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert seed_everything(5)["torch"] is True and calls == [5]
    for bad in (-1, SEED_MAX + 1, True, 1.5, "7"):
        with pytest.raises(SeedError):
            seed_everything(bad)  # type: ignore[arg-type]
    # a fresh interpreter: the call leaves torch out of sys.modules altogether
    code = (
        "import sys; from mimicwarehouse.run import seed_everything; "
        "r = seed_everything(1); print(r['torch'], 'torch' in sys.modules)"
    )
    proc = helpers.fresh_interpreter(["-c", code])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False False"


def test_sql_sample_clause_renders_and_duckdb_reproduces_the_sample() -> None:
    import duckdb

    assert sql_sample_clause(42, rows=10) == "USING SAMPLE reservoir(10) REPEATABLE (42)"
    assert sql_sample_clause(42, percent=2.5) == "USING SAMPLE reservoir(2.5%) REPEATABLE (42)"
    assert sql_sample_clause(7, percent=5, method="bernoulli") == (
        "USING SAMPLE bernoulli(5%) REPEATABLE (7)"
    )
    assert sql_sample_clause(7, percent=50.0, method="system") == (
        "USING SAMPLE system(50%) REPEATABLE (7)"
    )
    assert sql_sample_clause(1, percent=0.001) == "USING SAMPLE reservoir(0.001%) REPEATABLE (1)"
    # DuckDB parses the seed as an int32 literal: a 32-bit seed folds to seed % 2**31
    assert DUCKDB_SEED_MAX == 2**31 - 1
    assert sql_sample_clause(DUCKDB_SEED_MAX, rows=5).endswith(f"REPEATABLE ({DUCKDB_SEED_MAX})")
    assert sql_sample_clause(2**31, rows=5).endswith("REPEATABLE (0)")
    assert sql_sample_clause(SEED_MAX, rows=5).endswith(f"REPEATABLE ({DUCKDB_SEED_MAX})")
    bad_calls: list[dict[str, Any]] = [
        {"rows": 10, "percent": 1},
        {},
        {"rows": 0},
        {"rows": 2.5},
        {"rows": True},
        {"percent": 0},
        {"percent": 101},
        {"percent": "5"},
        {"rows": 5, "method": "bernoulli"},
        {"rows": 5, "method": "system"},
        {"rows": 5, "method": "mystery"},
    ]
    for kwargs in bad_calls:
        with pytest.raises(SeedError):
            sql_sample_clause(1, **kwargs)
    for bad_seed in (-1, SEED_MAX + 1, True):
        with pytest.raises(SeedError):
            sql_sample_clause(bad_seed, rows=5)  # type: ignore[arg-type]

    con = duckdb.connect()  # in-memory; synthetic integers, never a data file
    try:
        con.execute("CREATE TABLE t AS SELECT range AS i FROM range(100000)")

        def sample(clause: str) -> list[int]:
            statement = f"SELECT list(i ORDER BY i) FROM (SELECT i FROM t {clause})"
            row = con.execute(statement).fetchone()
            assert row is not None
            return list(row[0])

        # this derived seed exceeds 2**31 - 1 (it is 3,085,034,244): the fold is what
        # makes it parse at all
        subsampling_seed = derive_seed(DEMO_PROTOCOL, "subsampling")
        assert subsampling_seed > DUCKDB_SEED_MAX
        seeded = sql_sample_clause(subsampling_seed, rows=25)
        first = sample(seeded)
        assert len(first) == 25 and first == sample(seeded), "REPEATABLE reproduces the rows"
        assert sample(sql_sample_clause(DUCKDB_SEED_MAX, rows=25)) == sample(
            sql_sample_clause(DUCKDB_SEED_MAX, rows=25)
        ), "the top of DuckDB's seed range parses and reproduces"
        salted = sql_sample_clause(derive_seed(DEMO_PROTOCOL, "subsampling", salt=1), rows=25)
        assert sample(salted) != first, "a salted seed draws a different sample"
        pct = sql_sample_clause(11, percent=1, method="bernoulli")
        assert sample(pct) == sample(pct)
        for method in ("reservoir", "bernoulli", "system"):
            clause = sql_sample_clause(3, percent=10, method=method)
            assert sample(clause) == sample(clause), method
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 2. Run.seed / Run.spawn_rngs (brief item 1, last clause)
# ---------------------------------------------------------------------------


def test_run_seed_derives_from_run_id_records_at_once_and_is_idempotent(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start("seeded", tier="fixture", settings=settings, doctor=False) as r:
        assert _manifest_file(r)["seeds"] == {}, "an open run starts with an empty seed record"
        assert r.seed_scope == r.run_id, "unfrozen work derives from the run id"
        draws = _draws(r.seed("bootstrap"))
        assert _manifest_file(r)["seeds"] == {"bootstrap": derive_seed(r.run_id, "bootstrap")}, (
            "recorded before the stochastic work starts, so a killed run keeps it"
        )
        assert _draws(r.seed("bootstrap")) == draws, "the same stage yields the same stream"
        workers = r.spawn_rngs("cv_split", 3)
        assert len(workers) == 3 and len({tuple(_draws(g)) for g in workers}) == 3
        assert [_draws(g) for g in spawn_rngs(r.run_id, "cv_split", 3)] == [
            _draws(g) for g in r.spawn_rngs("cv_split", 3)
        ]
        with pytest.raises(SeedError):
            r.seed("bad stage")
        with pytest.raises(SeedError):
            r.spawn_rngs("cv_split", 0)
    manifest = _manifest_file(r)
    assert manifest["seeds"] == {
        "bootstrap": derive_seed(r.run_id, "bootstrap"),
        "cv_split": derive_seed(r.run_id, "cv_split"),
    }
    assert _draws(rng(r.run_id, "bootstrap")) == draws, "reproducible from the manifest's run id"
    assert read_manifest(r.run_id, settings).seeds == manifest["seeds"]
    # a run that seeds nothing records {} — never None, which marks a pre-EP-36 manifest
    with run_mod.start("unseeded", tier="fixture", settings=settings, doctor=False) as r2:
        pass
    assert _manifest_file(r2)["seeds"] == {}


def test_run_seed_derives_from_the_protocol_id_when_frozen(data_root: Path) -> None:
    settings = _settings()
    protocol_hash = "e" * 64
    with run_mod.start(
        "frozen",
        tier="fixture",
        settings=settings,
        doctor=False,
        protocol_id=DEMO_PROTOCOL,
        protocol_hash=protocol_hash,
    ) as r:
        assert r.seed_scope == DEMO_PROTOCOL
        draws = _draws(r.seed(DEMO_STAGE))
    assert _manifest_file(r)["seeds"] == {DEMO_STAGE: derive_seed(DEMO_PROTOCOL, DEMO_STAGE)}
    assert draws == _draws(rng(DEMO_PROTOCOL, DEMO_STAGE))
    # a second run under the same protocol reproduces the stream — the point of freezing
    with run_mod.start(
        "again",
        tier="fixture",
        settings=settings,
        doctor=False,
        protocol_id=DEMO_PROTOCOL,
        protocol_hash=protocol_hash,
    ) as r2:
        assert _draws(r2.seed(DEMO_STAGE)) == draws
    assert _manifest_file(r2)["seeds"] == _manifest_file(r)["seeds"]
    assert r2.run_id != r.run_id


# ---------------------------------------------------------------------------
# 3. Resources in the run manifest (brief items 3 + 4)
# ---------------------------------------------------------------------------


def test_run_manifest_carries_resources_with_positive_peak_and_wall(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start("resources", tier="fixture", settings=settings, doctor=False) as r:
        assert r.resource_log is not None and r.resource_log.running
        assert _manifest_file(r)["resources"] is None, "filled at exit"
        total = sum(range(200_000))
    assert r.resource_log is None and total > 0
    manifest = _manifest_file(r)
    res = manifest["resources"]
    assert set(res) == RESOURCE_FIELDS
    assert res["peak_rss_mb"] > 0 and res["wall_s"] > 0
    assert res["cpu_time_s"] is not None and res["cpu_time_s"] >= 0
    assert res["rss_start_mb"] > 0 and res["rss_end_mb"] > 0
    assert res["peak_rss_mb"] >= res["rss_start_mb"] and res["peak_rss_mb"] >= res["rss_end_mb"]
    assert res["peak_rss_method"] in ("peak_wset", "sampled")
    if sys.platform == "win32":
        assert res["peak_wset_mb"] is not None and res["peak_wset_mb"] >= res["peak_rss_mb"]
    assert res["samples"] >= 2 and res["sample_errors"] == 0
    assert res["interval_s"] == SAMPLE_INTERVAL_S == 0.5
    assert res["disk_delta_mb"] is not None
    # the top-level EP-35 fields mirror the block, so earlier readers keep working
    assert manifest["wall_s"] == res["wall_s"]
    assert manifest["peak_rss_mb"] == res["peak_rss_mb"]
    assert manifest["disk_delta_mb"] == res["disk_delta_mb"]
    reread = read_manifest(r.run_id, settings)
    assert isinstance(reread.resources, ResourceUsage)
    assert reread.resources.model_dump(mode="json") == res
    assert reread.resources.bench_fields() == {
        "wall_s": res["wall_s"],
        "peak_rss_mb": res["peak_rss_mb"],
        "disk_delta_mb": res["disk_delta_mb"],
    }


def test_failed_run_still_records_seeds_and_resources(data_root: Path) -> None:
    with (
        pytest.raises(ValueError, match="boom"),
        run_mod.start("bad", tier="fixture", settings=_settings(), doctor=False) as r,
    ):
        r.seed("bootstrap")
        raise ValueError("boom")
    manifest = _manifest_file(r)
    assert manifest["status"] == "failed"
    assert manifest["seeds"] == {"bootstrap": derive_seed(r.run_id, "bootstrap")}
    assert manifest["resources"]["wall_s"] > 0 and manifest["resources"]["peak_rss_mb"] > 0
    assert r.resource_log is None


def test_allocation_inside_a_run_raises_peak_rss_measurably(data_root: Path) -> None:
    import numpy as np

    with run_mod.start("alloc", tier="fixture", settings=_settings(), doctor=False) as r:
        block = np.ones(200 * MB // 8)  # 200 MB of touched pages (np.ones commits them)
        checksum = float(block.sum())
    res = _manifest_file(r)["resources"]
    assert checksum == 200 * MB // 8
    # measured within the run: the block is still alive when the final sample is taken
    assert res["peak_rss_mb"] - res["rss_start_mb"] >= 150, res
    del block


# ---------------------------------------------------------------------------
# 4. ResourceLog in isolation (stubbed probes pin every branch)
# ---------------------------------------------------------------------------


class _FakeProcess:
    """psutil.Process stand-in: ``memory_info()`` returns the scripted samples in order
    (the last one repeats); ``cpu_times()`` is constant."""

    def __init__(self, samples: list[Any]) -> None:
        self._samples = list(samples)
        self._i = 0

    def memory_info(self) -> Any:
        sample = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return sample

    @staticmethod
    def cpu_times() -> Any:
        return types.SimpleNamespace(user=1.0, system=0.5)


def _stub_psutil(monkeypatch: pytest.MonkeyPatch, samples: list[Any]) -> None:
    fake = types.ModuleType("psutil")
    fake.Process = lambda: _FakeProcess(samples)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psutil", fake)


def _stub_pynvml(
    monkeypatch: pytest.MonkeyPatch,
    *,
    devices: list[int],
    per_process: list[tuple[int, int | None]] | None = None,
    fail_init: bool = False,
) -> dict[str, Any]:
    """A ``pynvml`` stand-in: ``devices`` = used bytes per device (mutable through the
    returned state), ``per_process`` = ``(pid, usedGpuMemory)`` rows of
    ``nvmlDeviceGetComputeRunningProcesses`` (None = the call is unsupported)."""
    state: dict[str, Any] = {"init": 0, "shutdown": 0, "used": list(devices)}
    fake = types.ModuleType("pynvml")

    def nvml_init() -> None:
        state["init"] += 1
        if fail_init:
            raise RuntimeError("driver not loaded")

    def running(handle: int) -> list[Any]:
        if per_process is None:
            raise RuntimeError("not supported")
        return [types.SimpleNamespace(pid=pid, usedGpuMemory=used) for pid, used in per_process]

    fake.nvmlInit = nvml_init  # type: ignore[attr-defined]
    fake.nvmlShutdown = lambda: state.__setitem__("shutdown", state["shutdown"] + 1)  # type: ignore[attr-defined]
    fake.nvmlDeviceGetCount = lambda: len(state["used"])  # type: ignore[attr-defined]
    fake.nvmlDeviceGetHandleByIndex = lambda i: i  # type: ignore[attr-defined]
    fake.nvmlDeviceGetMemoryInfo = lambda h: types.SimpleNamespace(  # type: ignore[attr-defined]
        used=state["used"][h], total=8 * 2**30
    )
    fake.nvmlDeviceGetComputeRunningProcesses = running  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    return state


def test_peak_rss_promotes_peak_wset_only_when_it_grew(monkeypatch: pytest.MonkeyPatch) -> None:
    # (a) the process-lifetime mark stays flat (an earlier allocation set it): sampled max
    flat = [
        types.SimpleNamespace(rss=50 * MB, peak_wset=900 * MB),
        types.SimpleNamespace(rss=80 * MB, peak_wset=900 * MB),
    ]
    _stub_psutil(monkeypatch, flat)
    log = ResourceLog(interval_s=IDLE_INTERVAL_S, gpu=False)
    log.start()
    usage = log.stop()
    assert usage.peak_rss_mb == 80.0 and usage.peak_rss_method == "sampled"
    assert usage.rss_start_mb == 50.0 and usage.rss_end_mb == 80.0
    assert usage.peak_wset_mb == 900.0, "the lifetime mark is still reported, separately"
    assert usage.samples == 2 and usage.sample_errors == 0 and usage.cpu_time_s == 0.0
    assert usage.disk_delta_mb is None, "no data root given: not measured"
    assert usage.gpu_mem_peak_mb is None and usage.gpu_mem_method is None
    # (b) the mark grew during the measurement: it is exact and above every sample
    grew = [
        types.SimpleNamespace(rss=50 * MB, peak_wset=60 * MB),
        types.SimpleNamespace(rss=70 * MB, peak_wset=260 * MB),
    ]
    _stub_psutil(monkeypatch, grew)
    usage = ResourceLog.measure(lambda: None, gpu=False, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.peak_rss_mb == 260.0 and usage.peak_rss_method == "peak_wset"
    assert usage.peak_wset_mb == 260.0 and usage.rss_end_mb == 70.0
    # (c) no peak_wset attribute (a non-Windows psutil): the sampled maximum, no mark
    posix = [types.SimpleNamespace(rss=50 * MB), types.SimpleNamespace(rss=90 * MB)]
    _stub_psutil(monkeypatch, posix)
    usage = ResourceLog.measure(lambda: None, gpu=False, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.peak_rss_mb == 90.0 and usage.peak_rss_method == "sampled"
    assert usage.peak_wset_mb is None
    # (d) a mark that grew but stayed below a sampled reading never lowers the peak
    odd = [
        types.SimpleNamespace(rss=50 * MB, peak_wset=60 * MB),
        types.SimpleNamespace(rss=300 * MB, peak_wset=100 * MB),
    ]
    _stub_psutil(monkeypatch, odd)
    usage = ResourceLog.measure(lambda: None, gpu=False, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.peak_rss_mb == 300.0


def test_gpu_fields_are_null_without_pynvml(
    monkeypatch: pytest.MonkeyPatch, data_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "pynvml", None)  # not importable, whatever the host has
    caplog.set_level(logging.DEBUG, logger="mimicwarehouse")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with run_mod.start("nogpu", tier="fixture", settings=_settings(), doctor=False) as r:
            pass
    res = _manifest_file(r)["resources"]
    assert res["gpu_mem_peak_mb"] is None and res["gpu_mem_start_mb"] is None
    assert res["gpu_mem_method"] is None and res["sample_errors"] == 0
    assert not caught, "no warning spam without a GPU stack"
    assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
    # importable but no device, or an init failure: the same nulls, silently
    state = _stub_pynvml(monkeypatch, devices=[])
    usage = ResourceLog.measure(lambda: None, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.gpu_mem_peak_mb is None and state["init"] == 1 and state["shutdown"] == 1
    _stub_pynvml(monkeypatch, devices=[MB], fail_init=True)
    usage = ResourceLog.measure(lambda: None, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.gpu_mem_peak_mb is None and usage.sample_errors == 0


def test_gpu_memory_is_sampled_through_pynvml_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # device-level totals (Windows WDDM: the driver attributes nothing per process)
    state = _stub_pynvml(monkeypatch, devices=[300 * MB, 100 * MB])
    log = ResourceLog(interval_s=IDLE_INTERVAL_S)
    log.start()
    state["used"][0] = 700 * MB  # "the run allocated"
    log.sample()
    state["used"][0] = 500 * MB
    usage = log.stop()
    assert usage.gpu_mem_start_mb == 400.0 and usage.gpu_mem_peak_mb == 800.0
    assert usage.gpu_mem_method == "device" and usage.samples == 3
    assert state["init"] == 1 and state["shutdown"] == 1, "NVML opened once, closed once"
    # per-process attribution when the driver reports it: this pid only
    state = _stub_pynvml(
        monkeypatch, devices=[900 * MB], per_process=[(os.getpid(), 120 * MB), (1, 500 * MB)]
    )
    usage = ResourceLog.measure(lambda: None, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.gpu_mem_peak_mb == 120.0 and usage.gpu_mem_method == "process"
    # the driver lists the process but reports N/A: fall back to the device total
    state = _stub_pynvml(monkeypatch, devices=[900 * MB], per_process=[(os.getpid(), None)])
    usage = ResourceLog.measure(lambda: None, interval_s=IDLE_INTERVAL_S)[1]
    assert usage.gpu_mem_peak_mb == 900.0 and usage.gpu_mem_method == "device"
    # gpu=False skips the probe entirely
    state = _stub_pynvml(monkeypatch, devices=[900 * MB])
    usage = ResourceLog.measure(lambda: None, interval_s=IDLE_INTERVAL_S, gpu=False)[1]
    assert usage.gpu_mem_peak_mb is None and state["init"] == 0


def test_resource_log_lifecycle_context_manager_and_measure(tmp_path: Path) -> None:
    for bad in (0, -1, True, "0.5"):
        with pytest.raises(RunLedgerError):
            ResourceLog(interval_s=bad)  # type: ignore[arg-type]
    log = ResourceLog(data_root=tmp_path, interval_s=0.05, gpu=False)
    assert not log.started and not log.running and log.usage is None
    with pytest.raises(RunLedgerError, match="before start"):
        log.stop()
    assert log.start() is log and log.started and log.running
    with pytest.raises(RunLedgerError, match="twice"):
        log.start()
    time.sleep(0.3)
    usage = log.stop()
    assert log.stop() is usage and log.usage is usage and not log.running
    assert usage.samples >= 4, "the daemon thread sampled at its interval"
    assert usage.interval_s == 0.05 and usage.wall_s >= 0.3
    assert usage.disk_delta_mb is not None, "a data root: its drive's free delta is measured"
    assert not [t for t in threading.enumerate() if t.name == "mwh-resource-log"]
    with ResourceLog(gpu=False, interval_s=IDLE_INTERVAL_S) as inner:
        assert inner.running
    assert inner.usage is not None and inner.usage.samples == 2
    result, measured = ResourceLog.measure(lambda: 41 + 1, gpu=False, interval_s=IDLE_INTERVAL_S)
    assert result == 42 and measured.bench_fields() == {
        "wall_s": measured.wall_s,
        "peak_rss_mb": measured.peak_rss_mb,
        "disk_delta_mb": None,
    }
    with pytest.raises(ZeroDivisionError):  # the callable's error propagates, log stopped
        ResourceLog.measure(lambda: 1 / 0, gpu=False, interval_s=IDLE_INTERVAL_S)
    assert not [t for t in threading.enumerate() if t.name == "mwh-resource-log"]


# ---------------------------------------------------------------------------
# 5. measure -> run.bench -> the benchmark ledger (brief item 3, last clause)
# ---------------------------------------------------------------------------


def test_measure_feeds_the_benchmark_ledger_through_run_bench(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start("bench", tier="fixture", kind="bench", settings=settings, doctor=False) as r:
        result, usage = r.measure("query", "probe", lambda: 6 * 7, rows=42)
    assert result == 42 and usage.peak_rss_mb is not None and usage.disk_delta_mb is not None
    _, standalone = ResourceLog.measure(lambda: None, data_root=settings.data_root)
    run_mod.bench(
        "bench", "standalone", tier="fixture", settings=settings, **standalone.bench_fields()
    )
    lines = [
        BenchmarkLine.model_validate(line)
        for line in fsio.read_jsonl(benchmarks_mod.benchmarks_path(settings))
    ]
    assert len(lines) == 2
    first, second = lines
    assert first.run_id == r.run_id and first.kind == "query" and first.step == "probe"
    assert first.rows == 42 and first.ok
    assert first.wall_s == usage.wall_s and first.peak_rss_mb == usage.peak_rss_mb
    assert first.disk_delta_mb == usage.disk_delta_mb
    assert second.run_id is None and second.kind == "bench"
    assert second.peak_rss_mb == standalone.peak_rss_mb
    assert second.disk_delta_mb == standalone.disk_delta_mb is not None


# ---------------------------------------------------------------------------
# 6. mwh runs show / the manifests view / the reproduction block
# ---------------------------------------------------------------------------


def test_runs_show_displays_seeds_and_resources_and_the_view_exposes_them(
    data_root: Path,
) -> None:
    settings = _settings()
    with run_mod.start("shown", tier="fixture", settings=settings, doctor=False) as r:
        r.seed("bootstrap")
    seed = derive_seed(r.run_id, "bootstrap")
    runner = helpers.cli_runner()
    root = ["--data-root", str(data_root)]
    shown = runner.invoke(app, [*root, "runs", "show", r.run_id])
    assert shown.exit_code == 0, shown.output
    for token in (
        "seeds",
        "bootstrap",
        str(seed),
        "resources",
        "peak_rss_mb",
        "peak_rss_method",
        "cpu_time_s",
        "disk_delta_mb",
        "gpu_mem_peak_mb",
        "wall_s",
    ):
        assert token in shown.output, token
    shown_json = runner.invoke(app, [*root, "runs", "show", r.run_id, "--json"])
    assert shown_json.exit_code == 0, shown_json.output
    payload = json.loads(shown_json.stdout)
    assert payload["seeds"] == {"bootstrap": seed}
    assert payload["resources"]["peak_rss_mb"] > 0 and payload["resources"]["wall_s"] > 0
    if importlib.util.find_spec("pynvml") is None:
        assert payload["resources"]["gpu_mem_peak_mb"] is None
    refreshed = runner.invoke(app, [*root, "runs", "refresh"])
    assert refreshed.exit_code == 0, refreshed.output
    import duckdb

    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        row = con.execute(
            "SELECT json_extract(seeds, '$.bootstrap')::BIGINT, "
            "json_extract(resources, '$.peak_rss_mb')::DOUBLE, "
            "json_extract_string(resources, '$.peak_rss_method') FROM manifests"
        ).fetchone()
    finally:
        con.close()
    resources = payload["resources"]
    assert row == (seed, resources["peak_rss_mb"], resources["peak_rss_method"])
    config.configure()


def test_reproduction_block_lists_seeds_and_resources_via_fmt_int(data_root: Path) -> None:
    from mimicwarehouse.inventory import fmt_int

    settings = _settings()
    with run_mod.start(
        "repro",
        tier="fixture",
        kind="report",
        settings=settings,
        doctor=False,
        protocol_id=DEMO_PROTOCOL,
        protocol_hash="c" * 64,
    ) as r:
        r.seed(DEMO_STAGE)
        r.seed("cv_split")
    block = reproduction_block(r.run_id, settings)
    assert f"bootstrap {fmt_int(derive_seed(DEMO_PROTOCOL, DEMO_STAGE))}" in block
    assert f"cv_split {fmt_int(derive_seed(DEMO_PROTOCOL, 'cv_split'))}" in block
    assert f"derive_seed(`{DEMO_PROTOCOL}`, stage)" in block and "determinism.md" in block
    for token in ("peak RSS", "CPU time", "disk delta", "GPU memory peak"):
        assert token in block, token
    assert block.isascii() and not guard.id_band_hits(block.encode("utf-8"))
    with run_mod.start("plain", tier="fixture", settings=settings, doctor=False) as r2:
        pass
    plain = reproduction_block(r2.run_id, settings)
    assert "Seeds: none (no stochastic stage)" in plain
    assert f"derive_seed(`{r2.run_id}`" not in plain
    # a manifest written before EP-36 (seeds / resources null) still validates and renders
    path = run_mod.manifest_path(r2.run_id, settings)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["seeds"] = None
    data["resources"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_manifest(r2.run_id, settings).resources is None
    old = reproduction_block(r2.run_id, settings)
    assert "predates EP-36" in old and "not measured" in old


# ---------------------------------------------------------------------------
# 7. Docs + import budget (brief item 2; acceptance)
# ---------------------------------------------------------------------------


def test_determinism_doc_exists_is_linked_and_guard_clean() -> None:
    text = DETERMINISM_DOC.read_text(encoding="utf-8")
    for required in (
        "derive_seed",
        "sha256",
        "protocol_id",
        "run_id",
        "salt",
        "rng(",
        "spawn_rngs",
        "seed_everything",
        "Generator",
        "random_state=int(rng.integers(2**31))",
        "REPEATABLE",
        "sql_sample_clause",
        '__name__ == "__main__"',
        "spawn",
        "bootstrap",
        "cv_split",
        "model_fit",
        "imputation",
        "subsampling",
        "snapshot ids",
        "git sha",
        "uv.lock",
        "ResourceLog",
        "peak_wset",
        "peak_rss_method",
        "gpu_mem_peak_mb",
        "pynvml",
        "retrospective",
    ):
        assert required in text, f"docs/methods/determinism.md lacks {required!r}"
    provenance = PROVENANCE_DOC.read_text(encoding="utf-8")
    assert "determinism.md" in provenance, "provenance.md links the determinism page (EP-35 -> 36)"
    assert "resources" in provenance and "seeds" in provenance
    design = DESIGN.read_text(encoding="utf-8")
    assert "> **Note (2026-09-05, EP-36" in design, "DESIGN section 11 needs the dated EP-36 note"
    assert "ResourceLog" in design and "derive_seed" in design
    assert "derive_seed" in README.read_text(encoding="utf-8")
    assert "peak_wset" in GOTCHAS.read_text(encoding="utf-8")
    violations = guard.scan([DETERMINISM_DOC, PROVENANCE_DOC], helpers.REPO_ROOT)
    assert not violations, [f"{v.rule}: {v.path}" for v in violations]


def test_run_module_keeps_numpy_psutil_and_pynvml_lazy() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.run", forbid=(*helpers.HEAVY_MODULES, "pynvml"), lazy=("psutil",)
    )
