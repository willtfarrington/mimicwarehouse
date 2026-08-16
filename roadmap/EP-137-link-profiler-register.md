# EP-137 — Importer profiler + provenance/licensing register

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-17 (Loader core A: typed CSV → Parquet) · **Blocks:** EP-138 (Concept/unit mapping guide + mapping YAML), EP-139 (Key validation, join cardinality, linkage coverage), EP-140 (Linkage Wizard A (profile → map)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

First step of the additional-data ingestion & linkage capability (category 35): before any
external table can be mapped, validated or committed, the warehouse needs to *describe* it and
*register* where it came from and under which license. This brief builds the profiler and the
provenance/licensing register that every later P9 step reads (D-36: wizard = profile → map →
validate → coverage → commit with a license register). It reuses the EP-17 loader's typed CSV
reader (DuckDB `read_csv` with declared or sniffed types, `.csv`/`.csv.gz`) and lands its outputs
under `%MWH_DATA_ROOT%\ext\<source_id>\` (DESIGN §19); raw files are never moved or edited (D-30).
MIMIC-IV-ED 2.2 (EP-142) is the first real source, so the profiler must already recognise the ED
hazards: `subject_id`/`hadm_id`/`stay_id` columns in the real id bands, `triage.chiefcomplaint` as
free text, `hadm_id` null for non-admitted stays. Every profile is aggregate-only (D-31).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/linkage/profiler.py`** — `profile_source(path, source_id, contract=None) -> SourceProfile`
   (pydantic): per table → row count, byte size, sha256; per column → inferred DuckDB type,
   null rate, distinct count, min/max for numeric/timestamp columns, length stats for strings,
   `id_band` flag (values inside 10 000 000–19 999 999 / 20 000 000–29 999 999 / 30 000 000–39 999 999 →
   treated as identifier: no min/max, no values), `free_text` flag (distinct ratio and mean length
   above thresholds, or name in a configurable list seeded with `chiefcomplaint`), `candidate_key`
   flags (unique non-null), and top-k values **only** for columns with ≤ 50 distinct values, each
   count passed through `mimicwarehouse.disclose.suppress` (k = 11). Writes `ext/<source_id>/profile.json`.
2. **`src/mimicwarehouse/linkage/register.py`** — the provenance/licensing register:
   `ext/<source_id>/source.yaml` (name, version, url/DOI, license, DUA accepted date, citation,
   download date, file manifest sha256/bytes/rows, declared keys, status: profiled/mapped/validated/
   committed, snapshot ids) plus the roll-up `ext/registry.yaml`; `mwh link register --md` renders
   `docs/resources/external-sources.md` (metadata only, committable). Pre-seed entries for
   MIMIC-IV-ED 2.2 (PhysioNet Credentialed Health Data License 1.5.0, own DUA) and the ODbL demo.
3. **CLI** — add the `mwh link` group to `cli.py`: `mwh link profile <path> --source-id <id> [--contract ed]`,
   `mwh link register list|show|add`; output is the profile as rich tables (never rows); append a
   dated note to DESIGN.md §15 for the new group.
4. **Synthetic external fixtures** — extend `mimicwarehouse.fixtures` with an ED-shaped generator
   (`edstays`, `triage`, `vitalsign`, `pyxis`, `diagnosis`; ids ≥ 90 000 000; `subject_id`/`hadm_id`
   drawn from the EP-11 hosp fixture so keys resolve, plus a few planted orphans; `chiefcomplaint`
   drawn from a fixed synthetic phrase list) written to `tests/fixtures/ext/edlike/`, and a tiny
   reference-table fixture (`itemid → code` map with invented codes) in `tests/fixtures/ext/reflike/`;
   both are reused by EP-138–EP-141 and EP-143 tests.
5. **Tests** `tests/ep/test_ep137.py` (`@pytest.mark.ep_137`, fixture tier; `MWH_DATA_ROOT` pointed
   at `tmp_path`): profile of the ED-like fixture flags `subject_id`/`stay_id` as ids,
   `chiefcomplaint` as free text, `stay_id` as a candidate key of `edstays`; no value list appears
   for free-text or id columns; a crafted CSV whose id column sits inside a real band yields
   `id_band = true`; register round-trips YAML.

## Out of scope

- Column/concept/unit mapping and its YAML → EP-138 (Concept/unit mapping guide + mapping YAML).
- Key validation, join cardinality, coverage → EP-139.
- Any Streamlit UI → EP-140/EP-141; any real-data run → EP-142/EP-143.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_137` and `uv run --group dev mwh verify EP-137` green on fixture.
- `uv run --group dev mwh link profile tests/fixtures/ext/edlike --source-id edlike` prints only
  aggregates; `ext/edlike/profile.json` and `source.yaml` exist; `docs/resources/external-sources.md`
  regenerates deterministically.
- No full-tier run in this brief (first real profile is recorded by EP-142).

## Parked → final-roadmap.md

- Automatic PHI/free-text classifiers beyond thresholds + name lists (e.g. Presidio) — trigger: a
  source with many unlabelled string columns.
