"""Cohort engine (EP-46 spec + registry; EP-47 compiler; EP-48 attrition; DESIGN §9, §15):
a declarative, versioned cohort specification shared by the compiler, the Cohort Builder
page (EP-62, through the JSON schema) and the protocol schema (EP-51, by reference).

Docstring-only package (the EP-33 B6 form): nothing is imported eagerly, so the
``mwh --help`` import budget never pays for duckdb / polars. The modules:

* :mod:`~mimicwarehouse.cohort.spec` — the pydantic ``CohortSpec`` schema: grain, index
  event, ordered inclusion / exclusion criteria (``age`` / ``demographic`` / ``codeset`` /
  ``phenotype`` / ``concept`` / ``los`` / ``data_availability`` / ``prior_admissions`` /
  ``custom_sql``), observation window, washout, follow-up, era filter, the degeneracy
  probe, the references and the definition hash; YAML I/O and the JSON schema;
* :mod:`~mimicwarehouse.cohort.registry` — the packaged ``specs/*.yaml`` and their lock
  file (the ``(id, version)`` immutability rule), reference resolution against the EP-40
  code-set and EP-41 phenotype registries, static validation, the ``cohorts.specs`` DAG
  step (``meta.cohort_specs``), the catalog extension and the ``docs/methods/cohorts.md``
  renderer;
* :mod:`~mimicwarehouse.cohort.probe` — the tier-level checks through ``safe_query``:
  references against ``meta.codesets`` / ``meta.phenotype_versions`` and the
  level-degeneracy probe (EP-31 policy, spec-level half);
* :mod:`~mimicwarehouse.cohort.cli` — ``mwh cohort …``.

``python -m mimicwarehouse.cohort`` re-renders the generated blocks of
``docs/methods/cohorts.md``.
"""
