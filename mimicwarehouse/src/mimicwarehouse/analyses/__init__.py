"""Capstone analysis modules (``docs/analyses/`` case studies; EP-32 convention, D-8).

One module per capstone, each a ``python`` DAG step under ``dag/specs/analyses.yaml`` with a
``build(tier) -> run_id`` entry point inside ``run.start(kind="report")`` and a ``promote``
step that copies the run's tables and figures into ``docs/analyses/<NN-slug>/`` only through
the disclosure gate (EP-43). The package ``__init__`` is docstring-only (the EP-33 B6
doctrine); nothing here is on the ``mwh`` start-up path.

- :mod:`mimicwarehouse.analyses.c01_concepts_qc` — Capstone #1, concepts / QC (EP-53).
"""
