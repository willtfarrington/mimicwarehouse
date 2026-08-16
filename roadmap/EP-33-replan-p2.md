# EP-33 — Re-plan P2

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-17 (Loader core A: typed CSV → Parquet), EP-18 (Loader core B: subject buckets, sort, resume), EP-19 (DAG runner `mwh build`), EP-20 (Stage dimensions + small hosp/icu tables), EP-21 (Catalog builder (per-tier .duckdb)), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-23 (Stage labevents ⏱), EP-24 (Stage emar + emar_detail ⏱), EP-25 (Stage remaining hosp tables ⏱), EP-26 (Stage chartevents ⏱), EP-27 (Stage icu event tables ⏱), EP-28 (Verify full staging), EP-29 (Catalog & data dictionary (meta.*)), EP-30 (Safe-query wrapper + audit log), EP-31 (Tracer bullet: first-ICU-stay adults → in-hospital mortality), EP-32 (Capstone #0: staging benchmark note + docs/analyses convention) · **Blocks:** —

## Context

Every phase closes with a re-plan (**D-8**): retro, timings, DECISIONS addenda, ☑
reconciliation via `roadmap_check.py` (EP-6), Parked-item mirroring into
`final-roadmap.md`, and amendments to the next phase's briefs. P3's briefs already exist
in full (**D-9**: full briefs P0–P4), so this brief *amends* them where P2 changed an
interface it relies on — `open_catalog`, `read_parquet_sql`, `safe_query`'s signature and
rules, `STEP_HANDLERS`/DAG step kinds, `mwh jobs`, `mwh catalog`, `meta.*` names,
`runs.duckdb`, `dag.benchmarks` — rather than writing new ones. It also settles the P2
design questions with dated notes and records follow-ups that were deliberately deferred
(disclosure sidecars for the P2 docs at EP-43, tracer promotion at EP-53, fixture outcome
enrichment, undescribed dictionary columns). Governance check: no rows, ids or note text
have entered git, tool output or docs during P2 (`mwh guard` sweep of the P2 commits).

## In scope

1. **Reconciliation** — `uv run --group dev python roadmap_check.py` (EP-6): every P2 row
   in `roadmap/README.md` has its ☑ hash(es), table ↔ file parity holds; every ⏱ brief
   (EP-23…EP-27) carries EP-28's completion note; EP-20/21/29/31 carry their own; job
   states in `%MWH_DATA_ROOT%\runs\jobs\` are all `done`; `mwh doctor` shows ≥ 100 GB free.
2. **Retro + timings** — a `> **Retro (date).**` block appended to this brief: planned vs
   actual sizes per EP, what dragged (wheel fights, DuckDB behaviours, Windows detach),
   full-tier wall-time table from `mwh runs benchmarks`, measured core lake size and temp
   peak vs DESIGN §3, bucket-count verdict (keep 100 or open a `final-roadmap.md` item),
   the `dev-first` ordering verdict, and whether the optional "toolchain remediation" S
   slot is needed for P3.
3. **DECISIONS addenda** — under D-17/D-18/D-20/D-24/D-31 as applicable: pinned DuckDB
   version and the storage-format rule; dims materialized / subject tables as views;
   `dev-first` sorting and `dev_ready`; background-job launcher (`mwh jobs`) as the
   standard for ⏱ briefs; `safe_query`'s aggregate-only + count-column rule and row-wise
   k suppression (until EP-43); the interim `mwh sql` history; demo ED fetched-not-staged
   (D-4). Add new numbered decisions only for genuinely new choices.
4. **Amend P3 briefs** — read EP-34…EP-54 for references to P2 interfaces and update
   names/paths/signatures to what was actually built (`open_catalog`, `safe_query`,
   `SUPPRESSOR` hook for EP-43, `STEP_HANDLERS` for EP-37/EP-50, `meta.*` for EP-39/EP-44,
   `dag.benchmarks` for EP-35, `runs.duckdb` builder for EP-35); add to EP-43's acceptance
   the retroactive `mwh disclose check` of `mimicwarehouse/DATA-DICTIONARY.md` and
   `docs/analyses/00-staging-benchmark.md`; add to EP-53 the promotion of the tracer report;
   note EP-11 fixture enrichment if EP-31 reported `not_fit`. Record each amendment as
   `> **EP-33 amendment (date).**` in the affected brief.
5. **Parked → final-roadmap.md** — mirror every P2 brief's Parked items into the matching
   category tables (alternative CSV engines; bucket schemes; partition appends; parallel
   bucket sort; extra demo datasets), and any new risk into `roadmap/README.md` § Risks
   with strike-throughs for resolved ones (disk budget, Windows detach).
6. **Design notes** — `DESIGN.md`: §21 open questions resolved by P2 marked with the
   resolving EP; §15 confirms the added commands (`mwh jobs`, `mwh catalog`, `mwh tracer`,
   `mwh runs benchmarks`); capability-coverage table row 1 and 36 unchanged unless briefs
   moved. Commit as `docs(roadmap): re-plan P2 (EP-33)`.

## Out of scope

- Writing new full briefs (P3 already has them) or re-chartering P4 — the first re-plan that writes briefs is EP-74.
- Any code change beyond test/doc fixes discovered by `roadmap_check.py`; bucket-scheme changes → a new brief if the retro says so.
- Notes go/no-go → EP-127.

## Verification / acceptance

- `roadmap_check.py` passes; all P2 rows ☑ with hashes; every ⏱ brief has a completion note with timing, peak RSS and disk.
- Retro block appended here; DECISIONS addenda present; `final-roadmap.md` contains the mirrored Parked items; affected P3 briefs carry `EP-33 amendment` notes; DESIGN §21 updated.
- `mwh guard` sweep of P2 commits is clean; `mwh doctor` free space ≥ 100 GB recorded in the retro.
