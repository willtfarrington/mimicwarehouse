"""Code-set registry + ICD-9 <-> ICD-10 GEM utility (EP-40; DESIGN §8, §15).

Docstring-only package (the EP-33 B6 form): nothing is imported eagerly, so the
``mwh --help`` import budget never pays for duckdb / polars. The modules:

* :mod:`~mimicwarehouse.codesets.spec` — the pydantic ``CodeSet`` schema, code
  normalisation, the definition hash and ``id@version`` references;
* :mod:`~mimicwarehouse.codesets.registry` — the packaged ``defs/*.yaml`` seeds and their
  lock file (the ``(id, version)`` immutability rule), dictionary expansion, the
  ``codesets.compile`` DAG step that writes ``meta.codesets`` / ``meta.codeset_members``,
  validation and the catalog extension;
* :mod:`~mimicwarehouse.codesets.gem` — the CMS 2018 General Equivalence Mappings:
  fetch + ``source.yaml`` register, the parser, ``forward`` / ``backward``, the
  ``codesets.gem`` DAG step (``meta.gem_i9_to_i10`` / ``meta.gem_i10_to_i9``) and the
  ``.gem-review.md`` author aid;
* :mod:`~mimicwarehouse.codesets.cli` — ``mwh codeset …``.

``python -m mimicwarehouse.codesets`` re-renders the generated blocks of
``docs/methods/codesets.md``.
"""
