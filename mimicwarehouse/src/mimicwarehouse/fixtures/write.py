"""Fixture writer - CSVs mirroring the raw layout, ``manifest.json``, ``README.md`` (EP-11, EP-12).

Layout under ``<out>`` (default ``mimicwarehouse/tests/fixtures/``, resolved from the package
location like :func:`mimicwarehouse.config.workspace_root`, so ``uv run mwh fixtures build``
from anywhere writes into the checkout)::

    <out>/mimic-iv-3.1/hosp/<table>.csv     the 22 hosp tables (EP-11)
    <out>/mimic-iv-3.1/icu/<table>.csv      the 9 icu tables (EP-12)
    <out>/manifest.json                     per file: sha256, bytes, rows, seed, generator version
    <out>/README.md                         what this is, how to regenerate, license

:func:`write_fixture` takes either one module's frames (``{table: frame}`` + ``module=``, the
EP-11 form) or several modules at once (``{"hosp": {...}, "icu": {...}}``) and writes one
manifest + README covering everything it wrote.

Byte discipline (amended EP-7): header order = contract order; LF line endings; a final ``\\n``
and no line - inside quoted multi-line values included - ending in a blank, so the repo's
``end-of-file-fixer`` / ``trailing-whitespace`` hooks are no-ops on the generated files and the
sha256s in ``manifest.json`` stay valid; timestamps ``YYYY-MM-DD HH:MM:SS`` and dates
``YYYY-MM-DD`` (never compact ``YYYYMMDD`` - guard G4); floats never in scientific notation;
NULL = empty field; fields are quoted only when they contain a comma, a quote or a newline
(quotes doubled). :func:`check_bytes` verifies all of that before anything touches disk, and
the same function backs the EP-11 test. Same frames => identical bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from mimicwarehouse.guard import id_band_hits

if TYPE_CHECKING:  # pragma: no cover
    import polars as pl

    from mimicwarehouse.fixtures.spec import FixturePlan, FixtureSpec
    from mimicwarehouse.schema.contract import Contract

#: Bumped when the generator changes output for the same spec (recorded per file in the manifest).
GENERATOR_VERSION = "0.1.0"
GENERATOR_NAME = "mimicwarehouse.fixtures"
MANIFEST_NAME = "manifest.json"
README_NAME = "README.md"
DATASET_DIR = "mimic-iv-3.1"
HOSP_DIR = "hosp"
ICU_DIR = "icu"
#: Module directory -> contract schema, in the order the README lists them.
MODULE_SCHEMAS: dict[str, str] = {HOSP_DIR: "mimiciv_hosp", ICU_DIR: "mimiciv_icu"}
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"
#: The regeneration command recorded in README / manifest.
REGENERATE_COMMAND = "uv run --group dev mwh fixtures build"


class WriteError(RuntimeError):
    """The bytes to be written violate the byte discipline above."""


def default_out_dir() -> Path:
    """``<workspace>/tests/fixtures`` resolved from the package location (CWD-independent in the
    source checkout; falls back to CWD from a wheel install, like ``config.workspace_root``)."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / "tests" / "fixtures"


def rel_path(table: str, module: str = HOSP_DIR, dataset_dir: str = DATASET_DIR) -> PurePosixPath:
    return PurePosixPath(dataset_dir) / module / f"{table}.csv"


# ---------------------------------------------------------------------------
# CSV bytes
# ---------------------------------------------------------------------------


def frame_to_csv_bytes(frame: pl.DataFrame) -> bytes:
    """Contract-order CSV bytes of one frame (LF, header, fixed formats)."""
    buf = io.BytesIO()
    frame.write_csv(
        buf,
        include_header=True,
        separator=",",
        line_terminator="\n",
        quote_char='"',
        datetime_format=TIMESTAMP_FORMAT,
        date_format=DATE_FORMAT,
        float_scientific=False,
        null_value="",
        quote_style="necessary",
    )
    return buf.getvalue()


def check_bytes(data: bytes, *, name: str = "<csv>") -> list[str]:
    """Problems with CSV bytes under the byte discipline (empty = clean)."""
    problems: list[str] = []
    if not data.endswith(b"\n"):
        problems.append(f"{name}: does not end with a newline")
    if data.endswith(b"\n\n"):
        problems.append(f"{name}: ends with a blank line")
    if b"\r" in data:
        problems.append(f"{name}: contains a carriage return")
    if b"\0" in data:
        problems.append(f"{name}: contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"{name}: not UTF-8")
        return problems
    for no, line in enumerate(text.split("\n")[:-1], start=1):
        if line != line.rstrip(" \t"):
            problems.append(f"{name}:{no}: trailing whitespace")
            break
    hits = id_band_hits(data)  # the guard's own G4 rule (EP-4): masked, never quotes content
    if hits:
        no, band, count, example = hits[0]
        problems.append(
            f"{name}:{no}: {count} token(s) in the {band} band ({example}) - guard G4 would refuse "
            f"this file ({len(hits)} line(s) in total)"
        )
    return problems


# ---------------------------------------------------------------------------
# Manifest / README
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileEntry:
    rel_path: str
    sha256: str
    bytes: int
    rows: int
    seed: int
    generator_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "bytes": self.bytes,
            "rows": self.rows,
            "seed": self.seed,
            "generator_version": self.generator_version,
        }


def _manifest_payload(
    entries: list[FileEntry], spec: FixtureSpec, contract_hash: str
) -> dict[str, Any]:
    modules = sorted({PurePosixPath(e.rel_path).parts[1] for e in entries})
    return {
        "generator": GENERATOR_NAME,
        "generator_version": GENERATOR_VERSION,
        "seed": spec.seed,
        "spec": spec.canonical(),
        "contract_hash": contract_hash,
        "regenerate": REGENERATE_COMMAND,
        "id_floor": spec.first_subject_id,
        "modules": modules,
        "files": {e.rel_path: e.as_dict() for e in sorted(entries, key=lambda e: e.rel_path)},
        "total_bytes": sum(e.bytes for e in entries),
        "total_rows": sum(e.rows for e in entries),
    }


def render_manifest(entries: list[FileEntry], spec: FixtureSpec, contract_hash: str) -> bytes:
    payload = _manifest_payload(entries, spec, contract_hash)
    return (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = Path(out_dir) / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def _tables_by_module(
    tables: Sequence[str] | Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    """``["patients", ...]`` (hosp, the EP-11 form) or ``{"hosp": [...], "icu": [...]}``."""
    if isinstance(tables, Mapping):
        return {str(m): list(t) for m, t in tables.items()}
    return {HOSP_DIR: list(tables)}


def render_readme(spec: FixtureSpec, tables: Sequence[str] | Mapping[str, Sequence[str]]) -> bytes:
    by_module = _tables_by_module(tables)
    layout = [
        f"{DATASET_DIR}/{module}/<table>.csv"
        + " " * (max(0, 12 - len(module)))
        + f"{len(names)} {MODULE_SCHEMAS.get(module, module)} tables, contract order"
        for module, names in by_module.items()
    ]
    total = sum(len(n) for n in by_module.values())
    lines = [
        "# Synthetic fixture (mimicwarehouse `fixture` tier)",
        "",
        "Everything under this directory is **synthetic** - generated by `mimicwarehouse.fixtures`",
        "(EP-11 hosp, EP-12 icu) from a seed and hand-typed public vocabularies. It contains no",
        "MIMIC-IV row, no MIMIC-IV value and no identifier: every `subject_id` / `hadm_id` /",
        f"`stay_id` / row id is >= {spec.first_subject_id:_} (real MIMIC ids live in",
        "10 000 000-39 999 999, which the pre-commit guard `mwh guard` refuses; see GOVERNANCE.md",
        "section 3, D-27). Real `itemid`s / codes / drug names are dictionary values typed from",
        "public documentation, not data.",
        "",
        "The layout mirrors the raw PhysioNet tree so the loader can point at it:",
        "",
        "```",
        *layout,
        f"{MANIFEST_NAME}                   per file: sha256, bytes, rows, seed, generator version",
        "```",
        "",
        f"{total} CSVs in total. `icustays` is derived from the same ICU segments as",
        "`transfers`, so the two agree; every icu event lies inside its stay; every icu `itemid`",
        "is in `d_items` with the `linksto` of its table.",
        "",
        "Regenerate (byte-identical for the same seed / spec / generator version):",
        "",
        "```",
        f"{REGENERATE_COMMAND}",
        f"# = mwh fixtures build --seed {spec.seed} --subjects {spec.n_subjects}",
        "```",
        "",
        "`tests/ep/test_ep11.py` (hosp) and `tests/ep/test_ep12.py` (icu) fail if the committed",
        "files drift from what the generator produces (`manifest.json` sha256s), so change the",
        "generator and rebuild rather than editing a CSV by hand. Tests read the tree through",
        "`mimicwarehouse.fixtures.catalog.build_fixture_catalog()` (in-memory DuckDB, contract",
        "types) - the `fixture` pytest tier (see `tests/README.md`).",
        "",
        "License: MIT, like the code (the fixture is not derived from MIMIC-IV data; the",
        "vocabularies are public code lists typed from documentation).",
        "",
    ]
    return ("\n".join(lines)).encode("utf-8")


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WriteResult:
    out_dir: Path
    entries: tuple[FileEntry, ...]
    manifest_path: Path
    readme_path: Path

    @property
    def total_bytes(self) -> int:
        return sum(e.bytes for e in self.entries)

    @property
    def total_rows(self) -> int:
        return sum(e.rows for e in self.entries)


FramesByModule = Mapping[str, Mapping[str, "pl.DataFrame"]]


def _as_modules(frames: Mapping[str, Any], module: str) -> dict[str, dict[str, pl.DataFrame]]:
    """Normalise the two accepted shapes to ``{module: {table: frame}}``."""
    if frames and all(isinstance(v, Mapping) for v in frames.values()):
        return {str(m): dict(tables) for m, tables in frames.items()}
    return {module: dict(frames)}


def write_fixture(
    frames: Mapping[str, pl.DataFrame] | FramesByModule,
    out_dir: Path,
    *,
    spec: FixtureSpec,
    contract_hash: str,
    module: str = HOSP_DIR,
    dataset_dir: str = DATASET_DIR,
) -> WriteResult:
    """Write ``frames`` as ``<out>/<dataset_dir>/<module>/<table>.csv`` + manifest + README.

    ``frames`` is either ``{table: frame}`` (written under ``module``) or
    ``{module: {table: frame}}`` for several modules at once. Every CSV is rendered to bytes and
    checked (:func:`check_bytes`) before the first file is written, so a violation leaves the
    directory untouched.
    """
    out_dir = Path(out_dir)
    by_module = _as_modules(frames, module)
    rendered: list[tuple[str, str, PurePosixPath, bytes, int]] = []
    problems: list[str] = []
    for mod, tables in by_module.items():
        for name, frame in tables.items():
            rel = rel_path(name, mod, dataset_dir)
            data = frame_to_csv_bytes(frame)
            problems += check_bytes(data, name=str(rel))
            rendered.append((mod, name, rel, data, frame.height))
    if problems:
        raise WriteError("\n".join(problems))
    entries: list[FileEntry] = []
    for _mod, _name, rel, data, rows in rendered:
        path = out_dir / Path(*rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append(
            FileEntry(
                rel_path=str(rel),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
                rows=rows,
                seed=spec.seed,
                generator_version=GENERATOR_VERSION,
            )
        )
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_bytes(render_manifest(entries, spec, contract_hash))
    readme_path = out_dir / README_NAME
    tables_by_module = {mod: [name for name in tables] for mod, tables in by_module.items()}
    readme_path.write_bytes(render_readme(spec, tables_by_module))
    return WriteResult(out_dir, tuple(entries), manifest_path, readme_path)


def build_frames(
    spec: FixtureSpec | None = None, *, contract: Contract | None = None
) -> tuple[FixturePlan, dict[str, dict[str, pl.DataFrame]]]:
    """``(plan, {"hosp": frames, "icu": frames})`` for ``spec`` - the whole fixture in memory."""
    from mimicwarehouse.fixtures.hosp import build_hosp_frames
    from mimicwarehouse.fixtures.icu import build_icu_frames
    from mimicwarehouse.fixtures.spec import FixtureSpec, build_plan
    from mimicwarehouse.schema.contract import load_contract

    spec = spec or FixtureSpec()
    contract = contract or load_contract()
    plan = build_plan(spec)
    hosp = build_hosp_frames(plan, contract=contract)
    icu = build_icu_frames(plan, contract=contract)
    return plan, {HOSP_DIR: hosp, ICU_DIR: icu}


def build_and_write(
    out_dir: Path | None = None,
    *,
    spec: FixtureSpec | None = None,
    check: bool = True,
) -> WriteResult:
    """plan -> hosp + icu frames -> (validate) -> write. What ``mwh fixtures build`` runs."""
    from mimicwarehouse.fixtures.check import assert_valid
    from mimicwarehouse.fixtures.spec import FixtureSpec
    from mimicwarehouse.schema.contract import load_contract

    spec = spec or FixtureSpec()
    contract = load_contract()
    plan, frames = build_frames(spec, contract=contract)
    if check:
        assert_valid(frames[HOSP_DIR], contract, plan, icu=frames[ICU_DIR])
    return write_fixture(
        frames,
        out_dir if out_dir is not None else default_out_dir(),
        spec=spec,
        contract_hash=contract.content_hash(),
    )


__all__ = [
    "DATASET_DIR",
    "DATE_FORMAT",
    "GENERATOR_NAME",
    "GENERATOR_VERSION",
    "HOSP_DIR",
    "ICU_DIR",
    "MANIFEST_NAME",
    "MODULE_SCHEMAS",
    "README_NAME",
    "REGENERATE_COMMAND",
    "TIMESTAMP_FORMAT",
    "FileEntry",
    "FramesByModule",
    "WriteError",
    "WriteResult",
    "build_and_write",
    "build_frames",
    "check_bytes",
    "default_out_dir",
    "frame_to_csv_bytes",
    "load_manifest",
    "rel_path",
    "render_manifest",
    "render_readme",
    "write_fixture",
]
