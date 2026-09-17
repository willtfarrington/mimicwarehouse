"""Protocol freeze (EP-51; DESIGN §13, §15; GOVERNANCE §7/§8/§12; D-25): a YAML analysis
protocol is content-hashed and registered **before** it runs, runs cite the frozen hash,
amendments append a new hash linked to the previous one, and an unfrozen or modified
protocol cannot run.

Docstring-only package (the EP-33 B6 form): nothing is imported eagerly, so the
``mwh --help`` import budget never pays for duckdb / polars / the run ledger. The modules:

* :mod:`~mimicwarehouse.protocol.spec` — the pydantic ``Protocol`` schema: claim type,
  the cohort reference (an EP-46 spec by ``id@version``), unit of analysis, exposure,
  outcomes (censoring rules from ``timesem``), covariates, feature windows, the analysis
  plan, the temporal holdout over ``anchor_year_group`` eras, the fixed seeds policy and
  retrospective statement, references and the amendment link; the content hash; YAML
  loading and the JSON schema;
* :mod:`~mimicwarehouse.protocol.registry` — reference resolution against the cohort /
  code-set / phenotype registries and the concept inventory, ``freeze`` / ``verify`` /
  ``amend`` over ``runs/protocols.jsonl`` and the read-only frozen copies under
  ``runs/protocols/<hash>.yaml``, the ``runs.protocols`` view columns and the
  ``docs/methods/protocols.md`` renderer;
* :mod:`~mimicwarehouse.protocol.runners` — the runner registry (v1: ``cohort_only``) and
  ``run_protocol``: the refusal rules, the ``kind: protocol`` run and
  ``runs/<run_id>/protocol_summary.md``;
* :mod:`~mimicwarehouse.protocol.cli` — ``mwh protocol freeze | verify | amend | list |
  show | run``.

``python -m mimicwarehouse.protocol`` re-renders the generated blocks of
``docs/methods/protocols.md``. The seed protocol is ``specs/tracer_mortality.yaml``
(``tracer_mortality@1.0.0``, the tracer question over ``first_icu_adults@1.0.0``).
"""
