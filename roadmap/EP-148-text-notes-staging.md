# EP-148 — Notes staging ⏱ (segregated lake + notes.duckdb FTS)

**Size:** M · **Tier:** fixture (full ⏱ → verified by EP-149) · **Core/Stretch:** stretch · **Depends on:** EP-19 (DAG runner `mwh build`), EP-127 (Re-plan P7 (writes full P8, re-charters P9; notes-track go/no-go)) · **Blocks:** EP-149 (Note search + sectioning), EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution. EP-127 decides the notes-track go/no-go and
> EP-136 re-charters this phase; on a *no* at EP-127 this brief and the rest of P10 stay unexecuted
> and their content is mirrored into `final-roadmap.md` (category 27).

## Context

MIMIC-IV-Note 2.2 (≈ 0.33 M discharge summaries + ≈ 2.32 M radiology reports, ≈ 6 GB of CSV under
`source material/mimic-iv-note-deidentified-free-text-clinical-notes-2.2/note/`) is a separate DUA
and the highest-risk asset in the repository (D-3, GOVERNANCE §9). This brief opens the optional
clinical-text track (capability category 27) by staging the four note tables into a **segregated**
lake and catalog — `%MWH_DATA_ROOT%\notes\` + `notes.duckdb` with a full-text index — that only the
owner role can attach (`--with-notes`), that `safe_query` can never reach, and whose text never
enters run records, reports, tool output, fixtures or git (DESIGN §18). It reuses the loader
(EP-17/18), the DAG runner (EP-19), the `note` schema contract from mimic-code `create.sql` (EP-9)
and the raw manifest (EP-10). The full-tier staging job is ⏱ (background, resumable) and EP-149
records its timing. Synthetic notes are the only text any test, screenshot or Claude session ever
sees — the ODbL Demo has no note module (D-27). There is no notes dev tier in v1 (parked below),
so for category 27 the six-part definition of done reads 'tests on fixture; full-tier runs as
logged background jobs' — EP-156 audits the row on that basis.

## Scope sketch (refine at re-plan)

1. **Segregated notes lake** (`src/mimicwarehouse/text/lake.py`, DAG `dags/notes.yaml` in the EP-19
   convention) — stage `discharge`, `discharge_detail`, `radiology`, `radiology_detail` into
   `%MWH_DATA_ROOT%\notes\lake\mimiciv_note\<table>\subject_bucket=NN\*.parquet` with the core
   lake's Hive/ZSTD/sort conventions (DESIGN §5), types from the EP-9 contract (`text` VARCHAR,
   `note_id` string key in the shipped `<subject_id>-<DS|RR>-<seq>` pattern, naive
   `charttime`/`storetime`), manifests in `notes\manifests\<build_id>.jsonl` and a **separate**
   notes snapshot id. Never written under `lake\core`. Resumable per bucket; `store_rejects` —
   note-table rejects are written only under `%MWH_DATA_ROOT%\notes\rejects\<table>\<build_id>.parquet`
   (segregated; never `lake\rejects\`, never under `runs/`); manifests and logs carry the reject
   *count* only.
2. **`notes.duckdb` + FTS** (`text/fts.py`) — read-only catalog of views over the notes lake plus a
   DuckDB `fts` index (`PRAGMA create_fts_index` on `note_id`/`text`, English stemmer, stopwords)
   built inside `mwh build` with explicit `memory_limit`/`temp_directory`; if the FTS build exceeds
   the memory limit or ~2 h, fall back to SQLite FTS5 (`notes_fts.sqlite`) as DESIGN §21 anticipates
   and record the choice in DESIGN.md. `meta.note_stats` (n_notes / n_subjects / n_hadm per
   `note_type`, char-length quantiles, notes-per-hadm distribution, share of note
   `subject_id`/`hadm_id` present in the core 3.1 catalog — Note 2.2 was released against
   MIMIC-IV 2.2, so version skew is measured, not assumed) is computed in-build and passed through
   `disclose.suppress` (EP-43).
3. **Attach gate + local-only guard** (`text/guard.py`, `text/lake.py::attach_notes`) — the notes
   catalog attaches only when the audit actor is `owner` (EP-30) **and** the command carries
   `--with-notes`; every attach writes an audit line. `safe.py` gains explicit refusals of `ATTACH`,
   of the `mimiciv_note` schema and of any path containing `notes.duckdb`; text modules call
   `text.guard.ensure_local_only()` at import and refuse to run when `MWH_ALLOW_REMOTE=true`.
4. **Synthetic notes fixture** (`src/mimicwarehouse/fixtures/notes.py`, output committed under
   `tests/fixtures/notes/`) — templated discharge summaries and radiology reports for fixture
   subjects (ids ≥ 90 000 000) with realistic section headers, `___` de-identification placeholders
   and a small planted vocabulary (sepsis / AKI / intubation / endotracheal-tube mentions in
   affirmed, negated, historical and hypothetical variants, each with a ground-truth label, planted
   at parameterised rates) that EP-149/150/153 tests consume.
5. **`note` grain** — flip the EP-34 placeholder (`timesem.py`, registered `available=False`) to a
   real grain: key `note_id`, time anchor `charttime`, relative time to `admittime`/`intime` via the
   core catalog. Per-patient date
   shift is identical for a subject's notes and structured events, so within-patient alignment is
   valid; `anchor_year_group` remains the only cross-patient temporal axis.
6. **Launch the full-tier job** in the background —
   `uv run --group dev mwh build --tier full --dag notes --with-notes`, log at
   `%MWH_DATA_ROOT%\runs\jobs\notes-stage.log`; job id, log path and start time recorded in this
   brief's completion note; wall time / peak RSS / disk delta recorded by EP-149. Disk budget
   5–15 GB for lake + FTS (DESIGN §3); `mwh doctor`'s ≥ 100 GB free rule applies.

## Out of scope

- Search API, sectioning and the staging timing → EP-149 (Note search + sectioning).
- Concept extraction, embeddings, topics, linkage → EP-150–EP-153; app page → EP-154.
- Re-downloading `.csv.gz` for checksum-verifiable notes raw → `final-roadmap.md` (RAW-1).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_148` green on fixture: the fixture notes lake builds into a temporary data
  root, `notes.duckdb` answers an FTS count query, `note_stats` is suppressed, and the guard tests
  **refuse** crafted violations (`safe_query` on `mimiciv_note.*` and on `ATTACH`; `attach_notes()`
  with actor = agent; import with `MWH_ALLOW_REMOTE=true`); a crafted bad note row is rejected into
  the notes root, not the core lake (test). `uv run --group dev mwh verify EP-148` green.
- No test, log line, CLI output or fixture file contains real note text or ids in the real bands;
  `mwh guard` passes on the commit.
- Full-tier job launched in the background; job id / log path recorded in the completion note;
  timing verified by EP-149 (benchmark ledger row + `> **Completion note**` appended here).

## Parked → final-roadmap.md

- Notes dev tier (`notes-dev.duckdb` over buckets 0–4) — trigger: any P10 fixture+full brief
  exceeds its Size because iteration against the full notes lake is too slow.
- Column map for a future MIMIC-IV-Note release aligned to core 3.x — trigger: PhysioNet release.
