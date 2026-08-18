"""mimic-code concepts — the vendored upstream tree and, later, our patches and runner.

EP-8 vendors an allow-listed slice of MIT-LCP/mimic-code (MIT) at a pinned commit under
``vendor/mimic-code/`` (upstream-relative paths, LF) with ``vendor/VENDOR.json`` as the pin
(D-19; GOVERNANCE §10, §12). This package exposes that pin:

- :func:`vendor_info` → :class:`VendorInfo` (sha, dates, file count, vendor root) — what run
  manifests (EP-35) and ``mwh doctor`` cite;
- :func:`vendored_path` → the absolute path of one vendored file by its upstream-relative path
  (``vendored_path("mimic-iv/buildmimic/postgres/create.sql")``), which EP-9 / EP-10 / EP-37 read;
- :func:`vendor_manifest` → the parsed ``VENDOR.json``.

Everything resolves through :mod:`importlib.resources`, so an installed wheel (hatchling
ships every non-Python file under the package) behaves like the source checkout. EP-37 adds
the concept runner and EP-38 the ``patches/`` tree beside ``vendor/``; re-vendoring is
``poe vendor-mimic-code`` (:mod:`mimicwarehouse.concepts.vendoring`).
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

VENDOR_DIRNAME = "vendor"
TREE_DIRNAME = "mimic-code"
MANIFEST_NAME = "VENDOR.json"


class VendorInfo(BaseModel):
    """The mimic-code pin as recorded in ``vendor/VENDOR.json``."""

    model_config = ConfigDict(frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$", description="upstream commit vendored")
    upstream_url: str
    commit_date: str = Field(description="ISO 8601 committer date of the pinned commit")
    vendored_on: str = Field(description="ISO date the tree was (re-)vendored")
    mimic_iv_version: str = Field(description="MIMIC-IV release the pinned validate.sql targets")
    file_count: int = Field(ge=1)
    local_edits: tuple[str, ...] = Field(
        default=(), description="upstream-relative paths carrying the mwh-guard pragma"
    )
    root: Path = Field(description="absolute path of vendor/ (VENDOR.json + mimic-code/ tree)")

    @property
    def tree(self) -> Path:
        """``vendor/mimic-code/`` — the upstream-relative tree."""
        return self.root / TREE_DIRNAME

    @property
    def short_sha(self) -> str:
        return self.sha[:12]


def vendor_root() -> Path:
    """Absolute path of ``vendor/`` inside the installed package (source tree or wheel)."""
    return Path(str(files(__name__).joinpath(VENDOR_DIRNAME)))


@cache
def vendor_manifest() -> dict[str, Any]:
    """The parsed ``VENDOR.json`` (cached; :class:`FileNotFoundError` if not vendored)."""
    path = vendor_root() / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} missing — run `uv run poe vendor-mimic-code --sha <sha>` (EP-8)"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def vendor_info() -> VendorInfo:
    """The mimic-code pin (validated), from the installed package's ``VENDOR.json``."""
    m = vendor_manifest()
    return VendorInfo(
        sha=m["upstream_commit"],
        upstream_url=m["upstream_url"],
        commit_date=m["commit_date"],
        vendored_on=m["vendored_on"],
        mimic_iv_version=m["mimic_iv_version_targeted"],
        file_count=len(m["files"]),
        local_edits=tuple(e["path"] for e in m.get("local_edits", [])),
        root=vendor_root(),
    )


def vendored_path(rel: str) -> Path:
    """Absolute path of one vendored file by its upstream-relative posix path.

    Raises :class:`ValueError` for an absolute or ``..``-bearing path and
    :class:`FileNotFoundError` when the file was not vendored (see ``VENDOR.json``
    ``files`` / ``excluded``).
    """
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"vendored_path expects an upstream-relative posix path, got {rel!r}")
    target = vendor_root() / TREE_DIRNAME / Path(*p.parts)
    if not target.is_file():
        raise FileNotFoundError(f"not vendored: {rel!r} (see {MANIFEST_NAME} files / excluded)")
    return target


__all__ = [
    "MANIFEST_NAME",
    "TREE_DIRNAME",
    "VENDOR_DIRNAME",
    "VendorInfo",
    "vendor_info",
    "vendor_manifest",
    "vendor_root",
    "vendored_path",
]
