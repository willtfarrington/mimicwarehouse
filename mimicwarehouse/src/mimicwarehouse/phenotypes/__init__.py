"""Phenotype engine (EP-41; DESIGN §8, §15): declarative YAML -> deterministic SQL over a
tier catalog -> a materialised, versioned derived table.

Docstring-only package (the EP-33 B6 form): nothing is imported eagerly, so the
``mwh --help`` import budget never pays for duckdb / polars. The modules:

* :mod:`~mimicwarehouse.phenotypes.spec` — the pydantic ``Phenotype`` schema: grain,
  the boolean criteria tree over leaves (``diagnosis`` / ``procedure`` / ``medication`` /
  ``lab`` / ``microbiology`` / ``concept`` / ``temporal``), the onset rule, outputs, the
  code-set references and the definition hash;
* :mod:`~mimicwarehouse.phenotypes.registry` — the packaged ``defs/*.yaml`` and their
  lock file (the ``(id, version)`` immutability rule), reference resolution against the
  EP-40 code-set registry, validation;
* :mod:`~mimicwarehouse.phenotypes.compiler` — the deterministic CTE chain (one CTE per
  leaf, a grain mapping, a boolean reduction, the final select) and the per-``hadm``
  companion;
* :mod:`~mimicwarehouse.phenotypes.runner` — the ``phenotypes.compile`` DAG step
  (materialisation under ``lake/derived/<tier>/phenotypes/<id>@<version>/``,
  ``meta.phenotype_versions``, one ``kind: phenotype`` run per build), the catalog
  extension (``mimiciv_derived.phenotype_<id>`` = the latest version, plus the ``_hadm``
  companion), the prevalence ``summary`` and the ``docs/methods/phenotypes.md`` renderer;
* :mod:`~mimicwarehouse.phenotypes.cli` — ``mwh phenotype …``.

``python -m mimicwarehouse.phenotypes`` re-renders the generated block of
``docs/methods/phenotypes.md``.
"""
