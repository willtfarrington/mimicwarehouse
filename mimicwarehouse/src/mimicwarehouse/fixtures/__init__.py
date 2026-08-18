"""Synthetic mini-MIMIC fixture generator - the ``fixture`` tier (EP-11 hosp, EP-12 icu; D-18/D-27).

- :mod:`mimicwarehouse.fixtures.spec` - :class:`FixtureSpec` (pydantic knobs) and
  :func:`build_plan` -> :class:`FixturePlan` (subjects, admissions, ADT segment chains, ICU
  segments, planted phenotype traits, providers) from one seeded generator;
- :mod:`mimicwarehouse.fixtures.vocab` - the hand-typed seed vocabularies (package data
  ``fixtures/vocab/*.yaml``): lab items, ICD codes, HCPCS, drugs, categories;
- :mod:`mimicwarehouse.fixtures.hosp` - one generator per ``mimiciv_hosp`` table ->
  contract-typed Polars frames (:func:`build_hosp_frames`);
- :mod:`mimicwarehouse.fixtures.check` - :func:`validate` / :func:`assert_valid`
  (contract columns + dtypes, id floor, FKs, PKs, time sanity, ICU segments);
- :mod:`mimicwarehouse.fixtures.write` - hook-clean CSV writer, ``manifest.json``,
  ``README.md``, :func:`build_and_write`;
- :mod:`mimicwarehouse.fixtures.cli` - ``mwh fixtures build [--out] [--seed] [--subjects]``.

Public names are re-exported **lazily** (module ``__getattr__``): ``mimicwarehouse.cli`` imports
:mod:`mimicwarehouse.fixtures.cli` at start-up and must not drag numpy / polars / pydantic models
into ``mwh --help`` (import budget, DESIGN section 15). Nothing here reads data: inputs are the
seed, the packaged vocab and the schema contract; every id is >= 90 000 000.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = [
    "FIXTURE_ID_FLOOR",
    "GENERATOR_VERSION",
    "AdmissionPlan",
    "FixtureError",
    "FixturePlan",
    "FixtureSpec",
    "IcuSegment",
    "SubjectPlan",
    "Vocab",
    "assert_valid",
    "build_and_write",
    "build_hosp_frames",
    "build_plan",
    "default_out_dir",
    "load_vocab",
    "validate",
    "write_fixture",
]

if TYPE_CHECKING:  # pragma: no cover - static names for type checkers / IDEs only
    from mimicwarehouse.fixtures.check import FixtureError, assert_valid, validate
    from mimicwarehouse.fixtures.hosp import build_hosp_frames
    from mimicwarehouse.fixtures.spec import (
        FIXTURE_ID_FLOOR,
        AdmissionPlan,
        FixturePlan,
        FixtureSpec,
        IcuSegment,
        SubjectPlan,
        build_plan,
    )
    from mimicwarehouse.fixtures.vocab import Vocab, load_vocab
    from mimicwarehouse.fixtures.write import (
        GENERATOR_VERSION,
        build_and_write,
        default_out_dir,
        write_fixture,
    )

_HOMES: dict[str, str] = {
    "FIXTURE_ID_FLOOR": "spec",
    "AdmissionPlan": "spec",
    "FixturePlan": "spec",
    "FixtureSpec": "spec",
    "IcuSegment": "spec",
    "SubjectPlan": "spec",
    "build_plan": "spec",
    "Vocab": "vocab",
    "load_vocab": "vocab",
    "build_hosp_frames": "hosp",
    "FixtureError": "check",
    "assert_valid": "check",
    "validate": "check",
    "GENERATOR_VERSION": "write",
    "build_and_write": "write",
    "default_out_dir": "write",
    "write_fixture": "write",
}


def __getattr__(name: str) -> Any:
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{home}"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
