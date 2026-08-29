"""DAG runner — ``mwh build``, snapshot ids, benchmark ledger, background jobs (EP-19).

The custom transform runner of D-20 (~600 LOC we control, DESIGN §6/§11): ``mwh build``
is the **only** writer of the lake and catalogs. Modules: :mod:`.spec` (pydantic DAG spec
+ YAML specs), :mod:`.runner` (the per-tier step runner under the build lock),
:mod:`.snapshot` (logical layer snapshot ids), :mod:`.benchmarks` (append-only build
telemetry, D-24), :mod:`.jobs` (detached background jobs + ``mwh jobs``), :mod:`.cli`
(the ``build`` / ``jobs`` typer commands).

No eager submodule imports here: ``cli.py`` imports :mod:`.cli` at start-up and the
runner's chain reaches the schema contract, which the ``mwh --help`` import budget
excludes (test_ep09) — import ``mimicwarehouse.dag.runner`` / ``.spec`` / ``.jobs``
directly where needed.

Everything logged, printed or returned is counts, schemas, hashes, paths and timings —
never a row (GOVERNANCE §4).
"""
