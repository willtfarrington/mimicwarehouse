"""Concept patches — local replacements for vendored ``concepts_duckdb`` files, each tied
to an upstream reference and to the vendored commit it was written against (EP-38 item 2;
D-19 "port fixes, record every local deviation"; GOVERNANCE §10; DESIGN §8).

The vendored tree under ``concepts/vendor/`` is **never edited**. A fix ported from an
upstream mimic-code pull request (or written locally) lives here instead:

* ``patches.yaml`` — the registry: one entry per patched concept with ``patch_id``,
  ``concept``, ``reason``, ``upstream_ref`` (PR / issue / commit URL),
  ``applies_to_upstream_commit`` (the EP-8 pin the replacement was diffed against),
  ``sql_sha256`` (of the patch file), ``date``, ``status`` (``ported-unmerged`` while the
  upstream PR is open — re-checked at the P4 re-plan — ``ported-merged`` once it landed,
  ``local`` for a fix of our own) and ``semantics`` (``changed`` when demo counts or
  values can move, ``unchanged`` for a syntax / intent-only port);
* ``<concept>.sql`` — the full replacement file in the vendored shape (a ``--`` header
  citing the patch id, the upstream path + commit, the MIT attribution and the upstream
  reference; then upstream's ``DROP TABLE … ; CREATE TABLE … AS`` header and the SELECT
  body), so :func:`~mimicwarehouse.concepts.inventory.split_header` reads it exactly like
  a vendored file.

:func:`effective_sql` is what the runner calls: the patch file when the registry names the
concept **and** the entry's ``applies_to_upstream_commit`` equals the pinned vendor commit
and the file's sha256 equals the registered one; the vendored file otherwise. A registry
entry whose commit differs from the pin — the tree was re-vendored — is a hard
:class:`PatchError`, raised by :func:`validate_registry` before any concept runs, so every
patch is reviewed against the new upstream before a build proceeds (the brief's
"refuses to start"). ``python -m mimicwarehouse.concepts.patches`` refreshes the
``sql_sha256`` fields from the files, rewrites the registry deterministically and
validates it. Import budget: stdlib + yaml + pydantic + the inventory (no DuckDB).
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mimicwarehouse.concepts import vendor_info, vendored_path
from mimicwarehouse.concepts.inventory import (
    Concept,
    Inventory,
    load_inventory,
    split_header,
    sql_sha256,
)

#: The registry file beside the patch SQL files.
REGISTRY_FILENAME = "patches.yaml"
PatchStatus = Literal["ported-unmerged", "ported-merged", "local"]
PatchSemantics = Literal["changed", "unchanged"]
_PR_RE = re.compile(r"/pull/(\d+)(?:$|[/#?])")
_ISSUE_RE = re.compile(r"/issues/(\d+)(?:$|[/#?])")
_COMMIT_RE = re.compile(r"/commit/([0-9a-f]{7,40})(?:$|[/#?])")


class PatchError(RuntimeError):
    """The registry or a patch file is not what a build may run (module docstring)."""


class Patch(BaseModel):
    """One registry entry (``patches.yaml``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patch_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    concept: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    reason: str = Field(min_length=1)
    upstream_ref: str = Field(description="PR / issue / commit URL the port follows")
    applies_to_upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    sql_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: PatchStatus = "ported-unmerged"
    semantics: PatchSemantics = "changed"

    @field_validator("upstream_ref")
    @classmethod
    def _https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("upstream_ref must be an https:// URL (PR, issue or commit)")
        return value

    @property
    def filename(self) -> str:
        return f"{self.concept}.sql"

    @property
    def short_ref(self) -> str:
        """``PR #2146`` / ``issue #1922`` / ``commit abc1234`` / the URL."""
        if m := _PR_RE.search(self.upstream_ref):
            return f"PR #{m.group(1)}"
        if m := _ISSUE_RE.search(self.upstream_ref):
            return f"issue #{m.group(1)}"
        if m := _COMMIT_RE.search(self.upstream_ref):
            return f"commit {m.group(1)[:12]}"
        return self.upstream_ref


class PatchRegistry(BaseModel):
    """``patches.yaml``: every patch, at most one per concept."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    maintained_by: str
    patches: tuple[Patch, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> PatchRegistry:
        ids = [p.patch_id for p in self.patches]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate patch_id in the registry: {sorted(ids)}")
        concepts = [p.concept for p in self.patches]
        if len(concepts) != len(set(concepts)):
            raise ValueError(f"a concept may carry one patch: {sorted(concepts)}")
        return self

    @property
    def by_concept(self) -> dict[str, Patch]:
        return {p.concept: p for p in self.patches}

    def patch_for(self, concept: str) -> Patch | None:
        return self.by_concept.get(concept)

    @property
    def concepts(self) -> tuple[str, ...]:
        return tuple(p.concept for p in self.patches)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def patches_root() -> Path:
    """This package's directory inside the installed package (source tree or wheel)."""
    return Path(str(files(__name__)))


def registry_path() -> Path:
    return patches_root() / REGISTRY_FILENAME


def patch_path(patch: Patch) -> Path:
    return patches_root() / patch.filename


def patch_text(patch: Patch) -> str:
    path = patch_path(patch)
    if not path.is_file():
        raise PatchError(f"{patch.patch_id}: patch file missing: {path}")
    return path.read_text(encoding="utf-8")


def _parse_registry(path: Path) -> PatchRegistry:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise PatchError(f"{path.name}: top level must be a mapping")
    try:
        return PatchRegistry.model_validate(doc)
    except ValueError as exc:
        raise PatchError(f"{path.name}: {exc}") from None


@cache
def load_registry() -> PatchRegistry:
    """The committed registry (validated; cached per process). An absent file is an
    empty registry — the runner then behaves exactly as at EP-37."""
    path = registry_path()
    if not path.is_file():
        return PatchRegistry(maintained_by="mimicwarehouse.concepts.patches (EP-38)")
    return _parse_registry(path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_patch(
    patch: Patch, *, upstream_commit: str | None = None, inventory: Inventory | None = None
) -> str:
    """Check one entry against the pin and its file; return the patch file's text.

    :class:`PatchError` when the concept is not in the inventory, the entry's
    ``applies_to_upstream_commit`` is not the vendored pin (re-vendored tree: review the
    patch first), the file is missing, its sha256 differs from the registered one, its
    header creates another table, or its body is byte-identical to the vendored one (a
    patch that changes nothing is a registry mistake)."""
    inv = inventory or load_inventory()
    pin = upstream_commit or vendor_info().sha
    if patch.concept not in inv.by_name:
        raise PatchError(
            f"{patch.patch_id}: unknown concept {patch.concept!r} (not in the inventory)"
        )
    if patch.applies_to_upstream_commit != pin:
        raise PatchError(
            f"{patch.patch_id}: written against upstream {patch.applies_to_upstream_commit[:12]} "
            f"but the vendored tree is at {pin[:12]} — re-vendored: review the patch against "
            "the new upstream, then update applies_to_upstream_commit (EP-38)"
        )
    text = patch_text(patch)
    actual = sql_sha256(text)
    if actual != patch.sql_sha256:
        raise PatchError(
            f"{patch.patch_id}: {patch.filename} sha256 {actual[:12]} differs from the "
            f"registered {patch.sql_sha256[:12]} — run "
            "`uv run python -m mimicwarehouse.concepts.patches`"
        )
    table, body = split_header(text)
    if table != patch.concept:
        raise PatchError(
            f"{patch.patch_id}: {patch.filename} creates {table!r}, registry says {patch.concept!r}"
        )
    concept = inv.concept(patch.concept)
    _, vendored_body = split_header(vendored_path(concept.path).read_text(encoding="utf-8"))
    if body == vendored_body:
        raise PatchError(f"{patch.patch_id}: body identical to the vendored {concept.path}")
    return text


def validate_registry(
    registry: PatchRegistry | None = None,
    *,
    upstream_commit: str | None = None,
    inventory: Inventory | None = None,
) -> PatchRegistry:
    """Validate every entry (:func:`validate_patch`); the runner calls this once per build
    before the first concept runs."""
    reg = registry if registry is not None else load_registry()
    inv = inventory or load_inventory()
    pin = upstream_commit or vendor_info().sha
    for patch in reg.patches:
        validate_patch(patch, upstream_commit=pin, inventory=inv)
    return reg


def effective_sql(
    concept: Concept, registry: PatchRegistry | None = None
) -> tuple[str, Patch | None]:
    """``(sql text, patch)`` the runner executes for ``concept``: the validated patch file
    when one is registered, else the vendored file (sha-checked against the inventory)."""
    reg = registry if registry is not None else load_registry()
    patch = reg.patch_for(concept.name)
    if patch is not None:
        return validate_patch(patch, upstream_commit=concept.upstream_commit), patch
    text = vendored_path(concept.path).read_text(encoding="utf-8")
    if sql_sha256(text) != concept.sql_sha256:
        raise PatchError(
            f"{concept.step_name}: vendored {concept.path} differs from the committed inventory "
            "— regenerate with `uv run python -m mimicwarehouse.concepts.inventory`"
        )
    return text, None


# ---------------------------------------------------------------------------
# Rendering (deterministic YAML; the __main__ refreshes sha256s)
# ---------------------------------------------------------------------------


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_registry_yaml(reg: PatchRegistry) -> str:
    lines = [
        "# concepts/patches/patches.yaml -- the concept patch registry (EP-38; D-19).",
        "# One entry per patched concept; the vendored tree is never edited. Refresh the",
        "# sql_sha256 fields and re-validate: uv run python -m mimicwarehouse.concepts.patches",
        f"maintained_by: {_scalar(reg.maintained_by)}",
        "patches:" + (" []" if not reg.patches else ""),
    ]
    for p in sorted(reg.patches, key=lambda p: p.concept):
        lines.append(f"  - patch_id: {_scalar(p.patch_id)}")
        lines.append(f"    concept: {p.concept}")
        lines.append(f"    reason: {_scalar(p.reason)}")
        lines.append(f"    upstream_ref: {_scalar(p.upstream_ref)}")
        # quoted: an all-digit hex string would otherwise load as an int
        lines.append(f"    applies_to_upstream_commit: {_scalar(p.applies_to_upstream_commit)}")
        lines.append(f"    sql_sha256: {_scalar(p.sql_sha256)}")
        lines.append(f"    date: {_scalar(p.date)}")
        lines.append(f"    status: {p.status}")
        lines.append(f"    semantics: {p.semantics}")
    return "\n".join(lines) + "\n"


def refresh_shas(reg: PatchRegistry) -> PatchRegistry:
    """The registry with every ``sql_sha256`` recomputed from its file."""
    return reg.model_copy(
        update={
            "patches": tuple(
                p.model_copy(update={"sql_sha256": sql_sha256(patch_text(p))}) for p in reg.patches
            )
        }
    )


def write_registry(reg: PatchRegistry, path: Path | None = None) -> Path:
    target = path or registry_path()
    target.write_text(render_registry_yaml(reg), encoding="utf-8", newline="\n")
    return target


def stale_registry() -> bool:
    """True when the committed file differs from a deterministic re-render of itself with
    refreshed sha256s (the ``test_ep38`` pin)."""
    path = registry_path()
    if not path.is_file():
        return False
    return path.read_text(encoding="utf-8") != render_registry_yaml(
        refresh_shas(_parse_registry(path))
    )


def main() -> int:
    """``python -m mimicwarehouse.concepts.patches``: refresh the sha256s, rewrite the
    registry deterministically, validate, list."""
    current = _parse_registry(registry_path())
    refreshed = refresh_shas(current)
    print(write_registry(refreshed))
    validate_registry(refreshed)
    for entry in refreshed.patches:
        print(f"{entry.patch_id}: {entry.concept} <- {entry.short_ref} [{entry.status}]")
    print(f"{len(refreshed.patches)} patch(es) valid against {vendor_info().short_sha}")
    return 0


__all__ = [
    "REGISTRY_FILENAME",
    "Patch",
    "PatchError",
    "PatchRegistry",
    "effective_sql",
    "load_registry",
    "main",
    "patch_path",
    "patch_text",
    "patches_root",
    "refresh_shas",
    "registry_path",
    "render_registry_yaml",
    "stale_registry",
    "validate_patch",
    "validate_registry",
    "write_registry",
]
