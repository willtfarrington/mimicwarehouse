# EP-171 — Toolchain remediation (P2)

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-167 (Retro C: CLI, settings & inventory consolidation) · **Blocks:** EP-17 (Loader core A)

> **Allocated at the P1 re-plan follow-up (owner decision, 2026-08-29).** EP-16's completion
> note recommended "not needed"; the owner directed allocation anyway (the EP-164 precedent:
> prove the toolchain against the phase's workload *before* the phase codes against it). Row
> inserted before EP-17; EP-17's own header is untouched — this brief declares `Blocks: EP-17`
> and its table position carries the ordering (the EP-164 convention).

## Context

The per-phase optional "toolchain remediation" slot (roadmap README § Re-plan EPs; DECISIONS
judgment calls), pointed at P2's one named toolchain hazard. P1 proved the **read** side of this
machine: EP-10 hashed and row-counted 98 GB from `source material\` at 2.0–2.4 GB/s with both
endpoint-security products live, and nothing was killed (EP-10 completion note; EP-16 lesson 1).
The **write** side is unproven, and it is exactly where the risk sits: roadmap Risk 12 calls the
Malwarebytes Ransomware Protection (ARW) heuristic "the likeliest killer of EP-17+ full-tier
Parquet writes" — it judges *processes* by I/O pattern and killed the unsigned `bash.exe` during
a burst copy/edit/delete loop on 2026-08-17 (D-42). EP-17…EP-27 will make the allow-listed
`.venv` `python.exe` do precisely the things the heuristic watches for: burst-create thousands
of Parquet files under `C:\mimicdata`, rewrite manifests in place, and rename-aside/replace
catalog files (DESIGN §5/§6 protocols). If that pattern trips ARW, the right time to find out is
a two-minute synthetic rehearsal, not 17 minutes into a 40 GB `chartevents` stage. The same
rehearsal yields the missing planning number: a measured MB/s for Parquet **writes** into the
data root (EP-16 lesson 1 says loader estimates should assume writes, not reads, are the
bottleneck — this brief measures instead of assuming). Everything written is synthetic bytes
generated in-process (ids, where any table content exists, ≥ 90 000 000); no MIMIC data is read
or written; the canary runs in the foreground well inside the ~10 min cap. Dependency-wise P2
adds nothing: EP-17…EP-33 code against duckdb/polars/pyarrow/pydantic already in the lock, so
the preflight re-asserts the EP-1 lock invariants rather than resolving anything new.

## In scope

1. **Write canary module** (`src/mimicwarehouse/canary.py`) + CLI (`mwh canary write
   [--small N] [--large-mb N] [--keep] [--json]`; attach with one `add_typer` line; **not** in
   `DIAGNOSTIC_COMMANDS` — it writes under the data root, the `mwh inventory` precedent). Against
   validated settings it creates `layout["tmp"] / "canary"` (the `canary` leaf is not a layout
   key — the module creates it, the EP-10 `raw\` precedent) and rehearses, in order, the I/O
   shapes P2's briefs plan (DESIGN §5/§6):
   - **burst small-file pass** — N (default 200) small Parquet files (~1 MB each of synthetic
     rows via DuckDB `COPY`, contract-true types not required) into Hive-style
     `subject_bucket=<n>/` subdirectories — the EP-18 bucketed-staging shape;
   - **large sequential pass** — one Parquet of `--large-mb` (default 2,048 MB) — the EP-23/26
     event-table shape; record wall time and MB/s separately for both passes;
   - **manifest churn** — append + atomic-rewrite of a JSONL manifest beside the files
     (`_atomic_write_text` retry pattern from `inventory.py`) — the EP-19 ledger shape;
   - **rename-aside swap** — write `x.duckdb.new`, rename the live file aside, `os.replace` the
     new one in — the DESIGN §6 catalog protocol;
   - **cleanup** — delete the whole canary tree (unless `--keep`) — the delete-loop shape that
     triggered the 2026-08-17 kill.
   After each phase it re-reads what it wrote (sha256 spot checks) so a silent quarantine
   surfaces as a hard error, and it prints counts/bytes/seconds/MB-per-s only (no cell values —
   trivially true, everything is synthetic). Free-space guard via `require_free_space` before
   starting; refuse `--large-mb` above a sane cap (default ≤ 8,192).
2. **Preflight assertions** (same session, no new code beyond tests): `uv run poe test -m ep_1`
   green (lock invariants: every package wheel-backed for this interpreter, the
   `autograd-gamma` allow-list unchanged) and a one-table versions line for the completion note
   (python / duckdb / pyarrow / polars / pandas as locked). P2 briefs that add a dependency keep
   `test_ep01::test_uv_lock_every_package_has_a_wheel_for_this_interpreter` green and say so in
   their completion notes (standing Risk 3 rule — restated here so the phase starts with it).
3. **Tests** (`tests/ep/test_ep171.py`, marker `ep_171`, fixture tier, against a temp data root
   via `tests/helpers.tmp_data_root` — never `C:\mimicdata`): the five phases run in order and
   leave nothing behind by default; `--keep` keeps the tree; file counts / sizes / bucket layout
   as specified; the swap sequence really is write-new → rename-aside → replace (assert on the
   directory between steps via a seam); re-read verification catches a deleted/altered file
   (delete one mid-run through the seam → hard error); refusal on unsafe root and on
   insufficient free space; CLI/`--json` output leak-free by assertion (the `id_band_hits`
   pattern from EP-10's tests).
4. **Live run on this machine, recorded in the completion note:** `uv run --group dev mwh canary
   write` (defaults) — total wall time, per-pass MB/s (the write-side baseline EP-17/18 estimates
   cite), and the explicit statement that the process survived and every re-read matched
   (i.e. neither Defender nor Malwarebytes interfered), or — if something is killed or vanishes —
   the Malwarebytes-Quarantine/`mbamservice.log` triage result (CLAUDE.md §3, D-42) and a README
   Risk note instead. Nine-path allow list re-confirmed on the owner's word if the last
   confirmation (2026-08-28, D-38 addendum) is no longer current.
5. **Docs**: DESIGN §15 dated note (`canary.py`, the five shapes, why not a doctor check — it
   writes); workspace README § State row + Quick start line; roadmap Risk 12 gets
   "→ EP-171 write canary passed (date)" (or the finding); D-42 gets a one-line addendum citing
   the measured baseline.

## Out of scope

- Any real-data read or write; anything under `source material\` — the canary is synthetic-only.
- Reading either product's exclusion list or changing endpoint-security settings — owner-only
  (D-38); `mwh doctor --elevated` stays parked (v2 DOC-1).
- Tuning DuckDB/Parquet write settings (row-group size, compression) → EP-17 owns the loader's
  write configuration; the canary uses defaults so the baseline is a floor, not a tuned figure.
- A background/⏱ variant — the canary is deliberately foreground-sized; full-tier stages get
  their own ⏱ briefs (EP-23…EP-27).

## Verification / acceptance

- `uv run poe test -m ep_171` green on fixture; `uv run poe test -m ep_1` still green;
  `uv run poe check` green; `uv run --group dev mwh verify EP-171` exits 0.
- `uv run --group dev mwh canary write` completes on this machine with every re-read matching,
  and by default leaves `<data_root>\tmp\canary\` absent afterwards; the completion note records
  wall time and both MB/s figures (thousands separators, G4).
- `uv run poe roadmap-check --strict` stays 0 errors / 0 warnings (172 rows, 172 briefs).
- Commit `feat(mimicwarehouse): write canary — Parquet burst/large/manifest/swap rehearsal +
  write baseline (EP-171)`, then tick ☑ EP-171 in `roadmap/README.md` and commit
  `docs(roadmap): record EP-171 commit hash`.
