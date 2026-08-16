# EP-52 — Backup of non-reproducible state (`mwh backup`)

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-35 (Provenance run ledger), EP-51 (Protocol schema + freeze registry + `mwh protocol`) · **Blocks:** EP-54 (Re-plan P3)

## Context

The lake, catalogs, derived layers and marts are rebuildable from raw + code (`mwh init` +
`mwh build`, DESIGN §3), so they are not backed up. What is **not** reproducible is the record of
what was done: `runs/ledger.jsonl`, `runs/benchmarks.jsonl`, `runs/audit.jsonl` (GOVERNANCE §8),
`runs/protocols.jsonl` + the frozen protocol copies (EP-51), per-run `manifest.json` + `sql/`
(EP-35), later the model-registry metadata (EP-106) and study workspaces
(`%MWH_DATA_ROOT%\studies\`). GOVERNANCE §11 requires `mwh backup` to copy that state to an
encrypted local target chosen by the owner — never a synced or virtual drive (G:/D:, D-29), never
inside the data root or the repository — plus a restore drill. This brief builds
`src/mimicwarehouse/backup.py` (DESIGN §15) using EP-3's cloud-sync/virtual-drive detector for the
target check. Fixture tier: tests run against a temporary data root with synthetic ledgers; no
data is read. Windows: preserve long paths via `pathlib`, copy with hashes, no symlinks.

## In scope

1. **Backup set + config** (`src/mimicwarehouse/backup.py`) — `BACKUP_SET` (globs relative
   to the data root): `runs/*.jsonl`, `runs/protocols/**`, `runs/*/manifest.json`, `runs/*/sql/**`,
   `models/registry/**` (`*.json`/`*.yaml` only), `studies/**` excluding `*.parquet|*.duckdb|*.csv`
   (specs and notes only), plus `--include-run-artifacts` to add `runs/*/tables/**` and
   `runs/*/figures/**` (off by default; may hold derived row-level tables inside the data root).
   Config `MWH_BACKUP_TARGET` (pydantic-settings, EP-3); `mwh backup run [--target <dir>]
   [--include-run-artifacts]` writes `<target>\mwh-backup-<UTC>\…` mirroring paths and a
   `backup_manifest.json` (files, sha256, bytes, data-root, git sha, timestamp, tool version).
2. **Target safety** — refuse (non-zero exit, message) when the target is: on a drive/path
   flagged cloud-sync/virtual by EP-3's detector (G:/D:, OneDrive/Google Drive/Cryptomator paths),
   inside `MWH_DATA_ROOT`, inside the repository, or not on a BitLocker-protected volume when the
   `mwh doctor` BitLocker check is available (warn if unknown); `--i-know` is **not** provided —
   the owner changes the target instead.
3. **Verify + restore drill** — `mwh backup verify <backup-dir>` re-hashes every file against
   `backup_manifest.json`; `mwh backup restore --from <backup-dir> --to <dir> [--dry-run]` copies
   back (never over an existing non-empty `runs/` without `--to` pointing elsewhere), then `mwh
   runs refresh --data-root <dir>` must rebuild `runs.duckdb` from the restored ledgers; `mwh
   backup list [--target]` shows backups with age/size; `mwh doctor` gains a "last backup age"
   line (warn > 7 days, from `MWH_BACKUP_TARGET`).
4. **Tests + docs** (`tests/ep/test_ep52.py`, `@pytest.mark.ep_52`, fixture) — temp data root
   with synthetic ledgers, two run dirs and a frozen protocol → `backup run` copies exactly the
   set (parquet under `runs/*/tables` excluded by default), manifest complete; tamper one byte →
   `verify` fails naming the file; `restore --to tmp` reproduces identical hashes and `runs
   refresh` builds views over it; a target under a path the detector flags (monkeypatched) is
   refused; a target inside the data root is refused. GOVERNANCE §11 wording checked against the
   implementation (append a dated note there only if the set differs); `docs/methods/provenance.md`
   gains a "backup & restore" section.

## Out of scope

- Backing up the lake/catalog/marts (rebuildable) — `mwh init` (EP-158) is the recovery path.
- Model weights (`models/*.pkl`, ≤ 10 GB) — registry metadata only; weights are re-trainable
  from frozen protocols (EP-106 revisits).
- Incremental/deduplicating backup tools (restic/borg) → parked (`final-roadmap.md` cross-cutting).

## Verification / acceptance

- `uv run poe test -m ep_52` green on fixture; `uv run --group dev mwh verify EP-52` green.
- `uv run --group dev mwh backup run --target <owner-chosen local encrypted path>` succeeds once
  for real (only the path and byte totals are printed; record the backup id in the completion note);
  `mwh backup verify` on it exits 0; `mwh backup run --target G:\anything` (or a temp path the
  detector flags) exits non-zero with the refusal message.
- `uv run --group dev mwh doctor` prints the last-backup-age line.
