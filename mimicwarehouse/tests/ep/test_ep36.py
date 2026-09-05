"""EP-36 — seed/determinism policy + resource logger.

Fixture tier only (no data is read): ``derive_seed`` is the pinned sha256 rule, stable across
two fresh interpreters and distinct across stages / salts / protocols; ``rng`` reproduces a
draw sequence; ``spawn_rngs`` yields distinct, reproducible, picklable child streams;
``seed_everything`` seeds the globals without importing torch (and seeds a torch that is
*already* imported — a stub, never the real thing); a run manifest records ``seeds``
(``{stage: seed}`` from the protocol id, or the run id when unfrozen) and ``resources``
with ``peak_rss_mb > 0`` and ``wall_s > 0``; a synthetic 200 MB numpy allocation raises
``peak_rss_mb`` measurably; the GPU fields are ``null`` without ``pynvml`` and a stub
``pynvml`` proves the per-process sampling and its silent degradation;
``ResourceLog.measure`` feeds ``run.bench(usage=)``; ``mwh runs show`` and the
``manifests`` view display the new fields; the docs exist, link and pass the guard; the
module keeps numpy / psutil / pynvml lazy. Everything asserted is seeds, timings, counts and
metadata.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pickle
import sys
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
    RESOURCE_SAMPLE_S,
    SEED_MAX,
    ResourceLog,
    ResourceUsage,
    RunLedgerError,
    derive_seed,
    random_state,
    read_manifest,
    rng,
    seed_everything,
    spawn_rngs,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_36

DOCS = helpers.WORKSPACE / "docs"
DETERMINISM_DOC = DOCS / "methods" / "determinism.md"
PROVENANCE_DOC = DOCS / "methods" / "provenance.md"
DESIGN = helpers.WORKSPACE / "DESIGN.md"

#: The brief's acceptance pair and the integer two separate ``python -c`` invocations
#: printed on 2026-09-05 (completion note) — pinning it pins the formula.
DEMO_PROTOCOL, DEMO_STAGE = "demo-protocol", "bootstrap"
DEMO_SEED = 1208429812

MB = 2**20
ALLOC_MB = 200


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


def _settings() -> Settings:
    return config.get_settings()


def _manifest_file(run: run_mod.Run) -> dict[str, Any]:
    return json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))


def _draws(generator: Any, n: int = 6) -> list[int]:
    return generator.integers(0, 10**6, size=n).tolist()


# ---------------------------------------------------------------------------
# 1. Seed derivation (brief item 1)
# ---------------------------------------------------------------------------


def test_derive_seed_is_the_pinned_rule_stable_across_processes() -> None:
    seed = derive_seed(DEMO_PROTOCOL, DEMO_STAGE)
    assert seed == DEMO_SEED
    digest = hashlib.sha256(f"{DEMO_PROTOCOL}|{DEMO_STAGE}|0".encode()).digest()
    assert seed == int.from_bytes(digest[:4], "big")
    assert SEED_MAX == 2**32 - 1 and 0 <= seed <= SEED_MAX
    code = (
        "from mimicwarehouse.run import derive_seed; "
        f"print(derive_seed({DEMO_PROTOCOL!r}, {DEMO_STAGE!r}))"
    )
    printed = set()
    for _ in range(2):  # the acceptance clause: twice, in two separate interpreters
        proc = helpers.fresh_interpreter(["-c", code])
        assert proc.returncode == 0, proc.stderr
        printed.add(int(proc.stdout.strip().splitlines()[-1]))
    assert printed == {seed}


def test_derive_seed_differs_across_stages_salts_and_protocols() -> None:
    base = derive_seed(DEMO_PROTOCOL, DEMO_STAGE)
    assert derive_seed(DEMO_PROTOCOL, "cv_split") != base
    assert derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=1) != base
    assert derive_seed("other-protocol", DEMO_STAGE) != base
    assert len({derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=i) for i in range(64)}) == 64
    for protocol, stage in (("", "s"), ("p", ""), ("a|b", "s"), ("p", "s|t")):
        with pytest.raises(RunLedgerError):
            derive_seed(protocol, stage)


def test_rng_reproduces_a_draw_sequence() -> None:
    first = _draws(rng(DEMO_PROTOCOL, DEMO_STAGE))
    assert first == _draws(rng(DEMO_PROTOCOL, DEMO_STAGE))
    assert first != _draws(rng(DEMO_PROTOCOL, DEMO_STAGE, salt=1))
    assert first != _draws(rng(DEMO_PROTOCOL, "cv_split"))
    state = random_state(rng(DEMO_PROTOCOL, DEMO_STAGE))
    assert state == random_state(rng(DEMO_PROTOCOL, DEMO_STAGE)) and 0 <= state < 2**31


def test_spawn_rngs_yields_distinct_reproducible_picklable_streams() -> None:
    first = [_draws(g) for g in spawn_rngs(DEMO_PROTOCOL, "cv_split", 5)]
    second = [_draws(g) for g in spawn_rngs(DEMO_PROTOCOL, "cv_split", 5)]
    assert first == second and len(first) == 5
    assert len({tuple(draws) for draws in first}) == 5, "children are independent streams"
    assert first[0] != _draws(rng(DEMO_PROTOCOL, "cv_split")), "a child is not the parent"
    assert first[0] != _draws(spawn_rngs(DEMO_PROTOCOL, DEMO_STAGE, 5)[0]), "stage-specific"
    assert spawn_rngs(DEMO_PROTOCOL, "cv_split", 0) == []
    with pytest.raises(RunLedgerError):
        spawn_rngs(DEMO_PROTOCOL, "cv_split", -1)
    # a child survives pickling, so a spawn worker can be handed one as an argument
    child = spawn_rngs(DEMO_PROTOCOL, "cv_split", 1)[0]
    clone = pickle.loads(pickle.dumps(child))
    assert _draws(clone) == _draws(child)


def test_seed_everything_seeds_globals_without_importing_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = (
        "import json, sys; from mimicwarehouse.run import seed_everything; "
        "r = seed_everything(7); "
        "print(json.dumps({'seeded': r, 'torch': 'torch' in sys.modules}))"
    )
    proc = helpers.fresh_interpreter(["-c", code])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"seeded": {"random": True, "numpy": True, "torch": False}, "torch": False}
    import random

    import numpy as np

    seed_everything(7)
    first = (random.random(), float(np.random.random()))
    seed_everything(7)
    assert (random.random(), float(np.random.random())) == first
    # a torch that is *already* imported is seeded — a stub module, never the real thing
    calls: list[tuple[str, int]] = []
    stub = types.ModuleType("torch")
    stub.manual_seed = lambda s: calls.append(("manual_seed", s))  # type: ignore[attr-defined]
    stub.cuda = types.SimpleNamespace(  # type: ignore[attr-defined]
        is_available=lambda: True, manual_seed_all=lambda s: calls.append(("cuda", s))
    )
    monkeypatch.setitem(sys.modules, "torch", stub)
    big = 2**40 + 5
    assert seed_everything(big)["torch"] is True
    assert calls == [("manual_seed", big % 2**32), ("cuda", big % 2**32)]


# ---------------------------------------------------------------------------
# 2. Run manifest: seeds + resources (brief item 4)
# ---------------------------------------------------------------------------


def test_run_records_seeds_and_resources(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start(
        "seeded", tier="fixture", settings=settings, doctor=False, protocol_id=DEMO_PROTOCOL
    ) as r:
        assert r.resource_log is not None and r.manifest.seeds is None
        draws = _draws(r.seed(DEMO_STAGE))
        r.seed("cv_split")
        r.seed(DEMO_STAGE, salt=3)
        r.resource_log.sample()
    manifest = _manifest_file(r)
    assert manifest["seeds"] == {
        DEMO_STAGE: DEMO_SEED,
        "cv_split": derive_seed(DEMO_PROTOCOL, "cv_split"),
        f"{DEMO_STAGE}@3": derive_seed(DEMO_PROTOCOL, DEMO_STAGE, salt=3),
    }
    assert draws == _draws(rng(DEMO_PROTOCOL, DEMO_STAGE)), "r.seed is the protocol's rng"
    resources = manifest["resources"]
    assert set(resources) == set(ResourceUsage.model_fields)
    assert resources["peak_rss_mb"] > 0 and resources["wall_s"] > 0
    assert resources["samples"] >= 3 and resources["interval_s"] == RESOURCE_SAMPLE_S == 0.5
    assert resources["peak_source"] in ("peak_wset", "sampled")
    assert resources["cpu_time_s"] >= 0 and resources["rss_start_mb"] > 0
    assert resources["rss_end_mb"] > 0 and resources["disk_delta_mb"] is not None
    for key in ("wall_s", "peak_rss_mb", "disk_delta_mb"):
        assert manifest[key] == resources[key], f"{key} mirrors resources"
    reread = read_manifest(r.run_id, settings)
    assert isinstance(reread.resources, ResourceUsage) and reread.seeds == manifest["seeds"]


def test_unfrozen_run_seeds_from_its_run_id_and_records_them(data_root: Path) -> None:
    import numpy as np

    settings = _settings()
    with run_mod.start("unfrozen", tier="fixture", settings=settings, doctor=False) as u1:
        draws = _draws(u1.seed(DEMO_STAGE))
    with run_mod.start("unfrozen", tier="fixture", settings=settings, doctor=False) as u2:
        u2.seed(DEMO_STAGE)
    s1, s2 = _manifest_file(u1)["seeds"][DEMO_STAGE], _manifest_file(u2)["seeds"][DEMO_STAGE]
    assert s1 == derive_seed(u1.run_id, DEMO_STAGE) and s2 == derive_seed(u2.run_id, DEMO_STAGE)
    assert s1 != s2, "no protocol: fresh per run"
    assert _draws(np.random.default_rng(s1)) == draws, "the recorded integer reproduces it"
    # a failed run still records its resources; nothing seeded stays None
    with (
        pytest.raises(ValueError, match="boom"),
        run_mod.start("bad", tier="fixture", settings=settings, doctor=False) as bad,
    ):
        raise ValueError("boom")
    failed = _manifest_file(bad)
    assert failed["status"] == "failed" and failed["seeds"] is None
    assert failed["resources"]["wall_s"] > 0 and failed["resources"]["peak_rss_mb"] > 0


def test_allocation_inside_a_run_raises_peak_rss(data_root: Path) -> None:
    import numpy as np

    settings = _settings()
    with run_mod.start("quiet", tier="fixture", settings=settings, doctor=False) as quiet:
        pass
    with run_mod.start("alloc", tier="fixture", settings=settings, doctor=False) as loud:
        buffer = np.ones(ALLOC_MB * MB // 8)  # pages touched; alive until the run closes
        assert buffer.nbytes == ALLOC_MB * MB
    baseline = _manifest_file(quiet)["resources"]["peak_rss_mb"]
    peak = _manifest_file(loud)["resources"]["peak_rss_mb"]
    del buffer
    assert peak - baseline >= ALLOC_MB / 2, (baseline, peak)


def test_gpu_fields_are_null_without_pynvml(data_root: Path) -> None:
    with run_mod.start("gpu", tier="fixture", settings=_settings(), doctor=False) as r:
        pass
    resources = _manifest_file(r)["resources"]
    if importlib.util.find_spec("pynvml") is None:
        assert resources["gpu_mem_peak_mb"] is None and resources["gpu_device"] is None
    else:  # EP-121 installs the gpu group: a device answers, or both stay None together
        assert (resources["gpu_device"] is None) == (resources["gpu_mem_peak_mb"] is None)


def test_gpu_probe_reads_a_present_pynvml_and_degrades_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a stub driver, never the real one: this pid holds 300 MB, then 700 MB, then 100 MB;
    # another pid's 1 GB must not count
    held = [300 * MB]
    shutdowns: list[int] = []
    stub = types.ModuleType("pynvml")
    stub.nvmlInit = lambda: None  # type: ignore[attr-defined]
    stub.nvmlShutdown = lambda: shutdowns.append(1)  # type: ignore[attr-defined]
    stub.nvmlDeviceGetCount = lambda: 1  # type: ignore[attr-defined]
    stub.nvmlDeviceGetHandleByIndex = lambda i: f"handle-{i}"  # type: ignore[attr-defined]
    stub.nvmlDeviceGetName = lambda handle: b"Stub GPU"  # type: ignore[attr-defined]
    stub.nvmlDeviceGetComputeRunningProcesses = lambda handle: [  # type: ignore[attr-defined]
        types.SimpleNamespace(pid=os.getpid(), usedGpuMemory=held[0]),
        types.SimpleNamespace(pid=1, usedGpuMemory=1024 * MB),
    ]
    monkeypatch.setitem(sys.modules, "pynvml", stub)
    log = ResourceLog(interval_s=0.05).start()
    held[0] = 700 * MB
    log.sample()
    held[0] = 100 * MB
    usage = log.stop()
    assert usage.gpu_device == "Stub GPU" and usage.gpu_mem_peak_mb == 700.0
    assert shutdowns == [1]
    # no device, or a driver that fails at init: the fields stay None and nothing warns
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stub.nvmlDeviceGetCount = lambda: 0  # type: ignore[attr-defined]
        _, none = ResourceLog.measure(lambda: 1, interval_s=0.05)
        assert none.gpu_device is None and none.gpu_mem_peak_mb is None

        def broken() -> None:
            raise RuntimeError("no driver")

        stub.nvmlInit = broken  # type: ignore[attr-defined]
        stub.nvmlDeviceGetCount = lambda: 1  # type: ignore[attr-defined]
        _, failed = ResourceLog.measure(lambda: 1, interval_s=0.05)
        assert failed.gpu_device is None and failed.gpu_mem_peak_mb is None
        assert failed.peak_rss_mb is not None and failed.peak_rss_mb > 0
    assert not caught, [str(w.message) for w in caught]


# ---------------------------------------------------------------------------
# 3. ResourceLog standalone + the bench bridge (brief item 3)
# ---------------------------------------------------------------------------


def test_resource_log_measure_feeds_bench(data_root: Path) -> None:
    settings = _settings()
    result, usage = ResourceLog.measure(
        sum, range(100_000), data_root=settings.data_root, interval_s=0.05
    )
    assert result == sum(range(100_000)) and isinstance(usage, ResourceUsage)
    assert usage.wall_s > 0 and usage.peak_rss_mb is not None and usage.peak_rss_mb > 0
    assert usage.samples >= 2 and usage.interval_s == 0.05
    assert usage.disk_delta_mb is not None, "the data-root drive was measured"
    assert usage.bench_fields() == {
        "wall_s": usage.wall_s,
        "peak_rss_mb": usage.peak_rss_mb,
        "disk_delta_mb": usage.disk_delta_mb,
    }
    with pytest.raises(ZeroDivisionError):
        ResourceLog.measure(lambda: 1 / 0, interval_s=0.05)
    with ResourceLog(interval_s=0.05) as log:
        pass
    assert log.usage is not None and log.usage.samples >= 2
    assert log.usage.disk_delta_mb is None, "no data root given: no drive measured"
    with pytest.raises(RunLedgerError, match="already"):
        log.start()
    with pytest.raises(RunLedgerError, match="not started"):
        ResourceLog().stop()
    with pytest.raises(RunLedgerError, match="interval"):
        ResourceLog(interval_s=0)
    # bench takes the usage; explicit telemetry wins; neither is refused
    with run_mod.start("bench", tier="fixture", kind="bench", settings=settings, doctor=False) as r:
        r.bench("query", "measured", usage=usage, rows=3)
        r.bench("query", "explicit", usage=usage, wall_s=9.5, peak_rss_mb=1.0)
        with pytest.raises(RunLedgerError, match="wall_s"):
            r.bench("query", "none")
    run_mod.bench("bench", "standalone", tier="fixture", usage=usage, settings=settings)
    lines = [
        BenchmarkLine.model_validate(line)
        for line in fsio.read_jsonl(benchmarks_mod.benchmarks_path(settings))
    ]
    assert [line.step for line in lines] == ["measured", "explicit", "standalone"]
    measured, explicit, standalone = lines
    assert measured.wall_s == usage.wall_s and measured.peak_rss_mb == usage.peak_rss_mb
    assert measured.disk_delta_mb == usage.disk_delta_mb
    assert measured.run_id == r.run_id and measured.rows == 3 and measured.ok
    assert explicit.wall_s == 9.5 and explicit.peak_rss_mb == 1.0
    assert standalone.run_id is None and standalone.wall_s == usage.wall_s


# ---------------------------------------------------------------------------
# 4. mwh runs show + the manifests view (acceptance clause 2)
# ---------------------------------------------------------------------------


def test_runs_show_and_manifests_view_display_seeds_and_resources(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start(
        "shown", tier="fixture", settings=settings, doctor=False, protocol_id=DEMO_PROTOCOL
    ) as r:
        r.seed(DEMO_STAGE)
    runner = helpers.cli_runner()
    root = ["--data-root", str(data_root)]
    shown = runner.invoke(app, [*root, "runs", "show", r.run_id])
    assert shown.exit_code == 0, shown.output
    for token in (
        "seeds",
        DEMO_STAGE,
        str(DEMO_SEED),
        "resources",
        "peak_rss_mb",
        "peak_source",
        "wall_s",
        "cpu_time_s",
        "gpu_mem_peak_mb",
    ):
        assert token in shown.output, token
    shown_json = runner.invoke(app, [*root, "runs", "show", r.run_id, "--json"])
    assert shown_json.exit_code == 0, shown_json.output
    payload = json.loads(shown_json.stdout)
    assert payload["seeds"] == {DEMO_STAGE: DEMO_SEED}
    assert payload["resources"]["peak_rss_mb"] > 0 and payload["resources"]["wall_s"] > 0
    # the manifests view binds seeds / resources as JSON (EP-35 column types unchanged)
    from mimicwarehouse.safe import build_runs_db, runs_db_path

    build_runs_db(settings)
    import duckdb

    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        row = con.execute(
            "SELECT json_extract(seeds, '$.bootstrap')::BIGINT, "
            "json_extract(resources, '$.peak_rss_mb')::DOUBLE, "
            "json_extract_string(resources, '$.peak_source') "
            "FROM manifests WHERE run_id = ?",
            [r.run_id],
        ).fetchone()
    finally:
        con.close()
    assert row == (
        DEMO_SEED,
        payload["resources"]["peak_rss_mb"],
        payload["resources"]["peak_source"],
    )
    config.configure()


# ---------------------------------------------------------------------------
# 5. Docs + import budget
# ---------------------------------------------------------------------------


def test_determinism_doc_exists_is_linked_and_passes_the_guard() -> None:
    assert DETERMINISM_DOC.is_file()
    text = DETERMINISM_DOC.read_text(encoding="utf-8")
    for required in (
        "derive_seed",
        "rng(",
        "spawn_rngs",
        "random_state",
        "seed_everything",
        "r.seed(",
        "Generator",
        "bootstrap",
        "cv_split",
        "model_fit",
        "imputation",
        "subsampling",
        "REPEATABLE",
        '__name__ == "__main__"',
        "git sha",
        "snapshot",
        "ResourceLog",
        "peak_wset",
        "pynvml",
        "EP-78",
        "retrospective",
    ):
        assert required in text, f"docs/methods/determinism.md lacks {required!r}"
    provenance = PROVENANCE_DOC.read_text(encoding="utf-8")
    assert "determinism.md" in provenance and "ResourceUsage" in provenance
    design = DESIGN.read_text(encoding="utf-8")
    assert "> **Note (2026-09-05, EP-36" in design, "DESIGN section 11 needs the dated note"
    violations = guard.scan([DETERMINISM_DOC, PROVENANCE_DOC], helpers.REPO_ROOT)
    assert not violations, [f"{v.rule}: {v.path}" for v in violations]


def test_run_module_keeps_numpy_psutil_and_gpu_libraries_lazy() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.run",
        lazy=("psutil", "numpy", "pynvml", "torch", "mimicwarehouse.doctor"),
    )
