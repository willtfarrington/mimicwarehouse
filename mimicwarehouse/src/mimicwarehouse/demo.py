"""``mwh demo`` — fetch + verify the ODbL demo datasets (EP-22 item 1; D-27, D-36).

Downloads the **MIMIC-IV Clinical Database Demo 2.2** (hosp + icu, 100 subjects) and the
**MIMIC-IV-ED Demo 2.2** from ``https://physionet.org/files/...`` — both ODbL 1.0, open
access, no credentials — into ``<data_root>/ext/demo/<dataset-dir>/`` (DESIGN §4/§19):
first ``SHA256SUMS.txt`` and ``LICENSE.txt``, then every file the sums list, each verified
against its sha256. A mismatching file is **deleted and refused** (the fetch stops); an
already-verified file is skipped, so a rerun resumes where it left off. The result is
recorded in ``ext/demo/source.yaml`` — the licensing-register precursor (D-36) that
``mwh demo status`` prints and the P9 register (EP-137) later absorbs.

``mwh demo fetch`` is the **only** network-touching command in P2 (physionet.org only).
It is not a text module, so the ``MWH_ALLOW_REMOTE`` gate does not apply (GOVERNANCE §9;
EP-170 amendment 3). Demo data lives only under the data root and is never committed;
demo ``subject_id``\\ s sit inside the real MIMIC bands, so the EP-4 guard treats demo
rows as real (D-27) — everything printed here is file names, counts, hashes and sizes.

Import budget: stdlib + typer/rich/pydantic/yaml only (cli.py imports this module at
attach time); ``urllib`` opens exactly the URLs derived from :data:`DEMO_DATASETS`.
Tests monkeypatch :func:`_urlopen` — the single network seam.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, BinaryIO, cast

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field
from rich.markup import escape

from mimicwarehouse.console import EXIT_USAGE, console, fail

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings

#: PhysioNet's CDN answers plain library user agents inconsistently; a browser-shaped UA
#: is the documented workaround for reference fetches (memory G4) and is harmless here.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mimicwarehouse-demo-fetch"

CHECKSUMS_FILENAME = "SHA256SUMS.txt"
LICENSE_FILENAME = "LICENSE.txt"
REGISTER_FILENAME = "source.yaml"
LICENSE_ID = "ODbL-1.0"

#: Download retries per file and the (in-process) backoff between attempts.
RETRIES = 3
RETRY_BASE_SLEEP_S = 0.5
#: Suffixes a SHA256SUMS entry may carry (the 2.2 releases ship nothing else); anything
#: new upstream fails loudly instead of being fetched blindly.
ALLOWED_SUFFIXES = (".csv.gz", ".csv", ".txt")

_SUMS_LINE = re.compile(r"^(?P<sha>[0-9a-f]{64})\s+\*?(?P<path>\S.*)$")
_PART_SUFFIX = ".part"


class DemoFetchError(RuntimeError):
    """The demo fetch cannot proceed (download failure, checksum mismatch, bad sums
    entry). Carries file names, hashes and counts only — never content."""


@dataclass(frozen=True, slots=True)
class DemoDataset:
    """One open demo dataset: identity and where PhysioNet serves its files."""

    name: str  # PhysioNet project slug, e.g. mimic-iv-demo
    version: str
    dirname: str  # directory under ext/demo (matches the PhysioNet archive name)
    url: str  # base URL, trailing slash


#: The two EP-22 datasets (D-27). ED demo tables are fetched and verified here but staged
#: into ``mimiciv_ed`` only by EP-142 (D-4: ED enters through the Linkage Wizard).
DEMO_DATASETS: tuple[DemoDataset, ...] = (
    DemoDataset(
        name="mimic-iv-demo",
        version="2.2",
        dirname="mimic-iv-demo-2.2",
        url="https://physionet.org/files/mimic-iv-demo/2.2/",
    ),
    DemoDataset(
        name="mimic-iv-ed-demo",
        version="2.2",
        dirname="mimic-iv-ed-demo-2.2",
        url="https://physionet.org/files/mimic-iv-ed-demo/2.2/",
    ),
)

#: The clinical demo's directory — the ``demo`` tier's raw root (EP-22 item 2; the DAG
#: runner resolves ``resolve_raw_root(settings, "demo")`` to this).
DEMO_CLINICAL_DIRNAME = DEMO_DATASETS[0].dirname
DEMO_ED_DIRNAME = DEMO_DATASETS[1].dirname


# ---------------------------------------------------------------------------
# Register (source.yaml) — the D-36 licensing-register precursor
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DemoFile(_Frozen):
    """One verified file of a demo dataset (path relative to the dataset dir, posix)."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class DemoDatasetRecord(_Frozen):
    """One dataset's entry in ``ext/demo/source.yaml``."""

    name: str
    version: str
    license: str
    url: str
    fetched_at: str  # ISO 8601 UTC
    verified: bool
    files: tuple[DemoFile, ...]


class DemoRegister(_Frozen):
    """The whole ``source.yaml``: every fetched demo dataset, verified checksums."""

    version: int = 1
    datasets: tuple[DemoDatasetRecord, ...]


def demo_root(settings: Settings) -> Path:
    """``<data_root>/ext/demo`` — where the demo datasets and the register live."""
    return settings.layout["ext_demo"]


def demo_raw_root(settings: Settings) -> Path:
    """The ``demo`` tier's raw-CSV root: ``ext/demo/mimic-iv-demo-2.2`` (item 2)."""
    return demo_root(settings) / DEMO_CLINICAL_DIRNAME


def register_path(settings: Settings) -> Path:
    return demo_root(settings) / REGISTER_FILENAME


def load_register(path: Path) -> DemoRegister:
    """Parse and validate ``source.yaml`` (raises on a malformed register)."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DemoRegister.model_validate(doc)


def write_register(path: Path, register: DemoRegister) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = register.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
        newline="\n",
    )
    return path


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


def _urlopen(url: str) -> BinaryIO:  # tests monkeypatch this — the single network seam
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return cast("BinaryIO", urllib.request.urlopen(request, timeout=60))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    """Stream ``url`` to ``target`` via a ``.part`` temp file, with retries. The rename
    happens only after a complete read, so an interrupted fetch never leaves a truncated
    file under the final name (resume-safe)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + _PART_SUFFIX)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with _urlopen(url) as response, part.open("wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(part, target)
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            part.unlink(missing_ok=True)
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))
    raise DemoFetchError(f"cannot download {url} after {RETRIES} attempt(s): {last}")


def parse_sha256sums(text: str, *, where: str) -> list[tuple[str, str]]:
    """``[(sha256, relative posix path), ...]`` from a ``SHA256SUMS.txt`` body; refuses
    unsafe paths (absolute, ``..``, backslashes) and unexpected suffixes."""
    entries: list[tuple[str, str]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        m = _SUMS_LINE.match(line)
        if m is None:
            raise DemoFetchError(f"{where}:{line_no}: not a '<sha256>  <path>' line")
        rel = m.group("path")
        pure = PurePosixPath(rel)
        if "\\" in rel or pure.is_absolute() or ".." in pure.parts:
            raise DemoFetchError(f"{where}:{line_no}: unsafe path {rel!r}")
        if not rel.endswith(ALLOWED_SUFFIXES):
            raise DemoFetchError(
                f"{where}:{line_no}: unexpected file type {rel!r} "
                f"(expected one of {', '.join(ALLOWED_SUFFIXES)})"
            )
        entries.append((m.group("sha"), rel))
    if not entries:
        raise DemoFetchError(f"{where}: no checksum entries")
    return entries


@dataclass(slots=True)
class FetchCounts:
    """What one dataset fetch did (counts only; the CLI prints them)."""

    downloaded: int = 0
    skipped: int = 0
    bytes: int = 0


def fetch_dataset(
    dataset: DemoDataset, dest_root: Path, *, force: bool = False
) -> tuple[DemoDatasetRecord, FetchCounts]:
    """Fetch and verify one dataset into ``<dest_root>/<dataset.dirname>/`` (module
    docstring): sums + license first, then every listed file; a checksum mismatch deletes
    the file and raises :class:`DemoFetchError`; verified files are skipped."""
    dest = Path(dest_root) / dataset.dirname
    dest.mkdir(parents=True, exist_ok=True)
    counts = FetchCounts()

    sums_path = dest / CHECKSUMS_FILENAME
    _download(dataset.url + CHECKSUMS_FILENAME, sums_path)  # always fresh — it is the truth
    license_path = dest / LICENSE_FILENAME
    if force or not license_path.is_file():
        _download(dataset.url + LICENSE_FILENAME, license_path)
    entries = parse_sha256sums(
        sums_path.read_text(encoding="utf-8"), where=f"{dataset.name}/{CHECKSUMS_FILENAME}"
    )

    files: list[DemoFile] = []
    for sha, rel in entries:
        target = dest / PurePosixPath(rel)
        if target.is_file() and not force and _sha256_file(target) == sha:
            counts.skipped += 1
        else:
            target.unlink(missing_ok=True)  # stale or corrupted leftover
            _download(dataset.url + rel, target)
            actual = _sha256_file(target)
            if actual != sha:
                target.unlink(missing_ok=True)
                raise DemoFetchError(
                    f"{dataset.name}/{rel}: sha256 mismatch (expected {sha}, got {actual}) "
                    "— file deleted; rerun `mwh demo fetch` (PhysioNet download corrupted?)"
                )
            counts.downloaded += 1
        counts.bytes += target.stat().st_size
        files.append(DemoFile(path=rel, sha256=sha, bytes=target.stat().st_size))

    record = DemoDatasetRecord(
        name=dataset.name,
        version=dataset.version,
        license=LICENSE_ID,
        url=dataset.url,
        fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        verified=True,
        files=tuple(files),
    )
    return record, counts


def fetch_all(settings: Settings, *, force: bool = False) -> tuple[DemoRegister, list[FetchCounts]]:
    """Fetch both demo datasets and write ``ext/demo/source.yaml``; returns the register
    and per-dataset counts. Any failure raises before the register is (re)written."""
    from mimicwarehouse.config import require_free_space

    require_free_space(settings.data_root, settings.min_free_gb_for("demo"))
    root = demo_root(settings)
    records: list[DemoDatasetRecord] = []
    counts: list[FetchCounts] = []
    for dataset in DEMO_DATASETS:
        record, c = fetch_dataset(dataset, root, force=force)
        records.append(record)
        counts.append(c)
    register = DemoRegister(datasets=tuple(records))
    write_register(register_path(settings), register)
    return register, counts


# ---------------------------------------------------------------------------
# CLI — mwh demo fetch / mwh demo status
# ---------------------------------------------------------------------------

demo_app = typer.Typer(
    name="demo",
    help="Fetch + verify the ODbL demo datasets into ext/demo (EP-22; physionet.org only).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@demo_app.command("fetch")
def fetch_command(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Re-download every file, even verified ones.")
    ] = False,
) -> None:
    """Download MIMIC-IV Demo 2.2 + MIMIC-IV-ED Demo 2.2, verify every sha256, and record
    the result in ext/demo/source.yaml (metadata only; the data never enters git)."""
    state: CliState = ctx.obj
    settings = state.settings
    from mimicwarehouse.config import DiskGuardError

    try:
        register, counts = fetch_all(settings, force=force)
    except (DemoFetchError, DiskGuardError) as exc:
        fail("mwh demo fetch", str(exc), code=EXIT_USAGE)
    for record, c in zip(register.datasets, counts, strict=True):
        console.print(
            f"{escape(record.name)} {record.version}: {len(record.files)} file(s) verified "
            f"({c.downloaded} downloaded, {c.skipped} already verified, "
            f"{c.bytes / 2**20:,.1f} MB)",
            highlight=False,
        )
    console.print(f"register written: {escape(str(register_path(settings)))}", highlight=False)


@demo_app.command("status")
def status_command(ctx: typer.Context) -> None:
    """Print the demo licensing register (ext/demo/source.yaml) — metadata only."""
    state: CliState = ctx.obj
    settings = state.settings
    path = register_path(settings)
    if not path.is_file():
        fail("mwh demo status", f"no register at {path} - run `mwh demo fetch` first")
    try:
        register = load_register(path)
    except Exception as exc:  # malformed YAML / schema — report, never guess
        fail(
            "mwh demo status",
            f"{path} is not a valid register ({exc}) - rerun `mwh demo fetch`",
            code=EXIT_USAGE,
        )

    from rich.table import Table as RichTable

    table = RichTable(title=f"demo register - {path}", pad_edge=False)
    for col, justify in (
        ("dataset", "left"),
        ("version", "left"),
        ("license", "left"),
        ("files", "right"),
        ("MB", "right"),
        ("verified", "left"),
        ("fetched_at", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for record in register.datasets:
        total = sum(f.bytes for f in record.files)
        table.add_row(
            escape(record.name),
            record.version,
            record.license,
            str(len(record.files)),
            f"{total / 2**20:,.1f}",
            "yes" if record.verified else "no",
            record.fetched_at,
        )
    console.print(table)


__all__ = [
    "ALLOWED_SUFFIXES",
    "CHECKSUMS_FILENAME",
    "DEMO_CLINICAL_DIRNAME",
    "DEMO_DATASETS",
    "DEMO_ED_DIRNAME",
    "LICENSE_FILENAME",
    "LICENSE_ID",
    "REGISTER_FILENAME",
    "DemoDataset",
    "DemoDatasetRecord",
    "DemoFetchError",
    "DemoFile",
    "DemoRegister",
    "FetchCounts",
    "demo_app",
    "demo_raw_root",
    "demo_root",
    "fetch_all",
    "fetch_dataset",
    "load_register",
    "parse_sha256sums",
    "register_path",
    "write_register",
]
