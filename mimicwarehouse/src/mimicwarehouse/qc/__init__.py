"""Data-quality profiling (EP-44; DESIGN §15) — the ``qc/`` package.

Docstring-only package (the EP-33 B6 form): nothing is imported eagerly, so the
``mwh --help`` import budget never pays for duckdb / polars. The modules:

* :mod:`~mimicwarehouse.qc.profile` — the thresholds document (``thresholds.yaml``), the
  per-table profile + check engine (one SQL family per contract table, generated from the
  contract — no sniffing), the ``qc.profile.<schema>.<table>`` / ``qc.checks`` DAG step
  handlers that write ``meta.qc_tables`` / ``meta.qc_columns`` / ``meta.qc_topk`` /
  ``meta.qc_checks`` under ``lake/meta/<tier>/``, the catalog extension and the
  ``dag/specs/qc.yaml`` renderer;
* :mod:`~mimicwarehouse.qc.report` — the ``qc.report`` step: ``runs/<run_id>/qc_report.md``
  (+ CSV tables), every frame through ``disclose.suppress`` before rendering, the EP-35
  reproduction block at the end, and the ``docs/methods/qc.md`` generated blocks;
* :mod:`~mimicwarehouse.qc.cli` — ``mwh qc status``.

``python -m mimicwarehouse.qc`` re-renders ``dag/specs/qc.yaml`` and the generated blocks
of ``docs/methods/qc.md``. Measurement-process summaries (``qc.measurement``) arrive with
EP-45.
"""
