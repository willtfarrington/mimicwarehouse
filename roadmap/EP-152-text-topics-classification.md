# EP-152 — Topic discovery + classification

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-151 (Embeddings (CPU-capable, GPU-accelerated) + similarity search) · **Blocks:** EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Unsupervised structure and one supervised text task over the EP-151 sample (capability category
27), all local. Representative workflow: topic discovery over discharge `brief_hospital_course`
and radiology `impression` sections, and **classification = recovering the sepsis-3 phenotype
(EP-42) at the hadm grain from the discharge summary text**, evaluated with the ML stack (splits
EP-104, assessment EP-105, registry/model card EP-106). Discharge summaries are written after the
outcome, so this is label recovery, not prediction — the report labels it **exploratory** and says
so. Disclosure adds a vocabulary rule: exported terms are themselves aggregates and must clear a
document-frequency floor (D-33 applied to words).

## Scope sketch (refine at re-plan)

1. **Topics** (`src/mimicwarehouse/text/topics.py`) — MiniBatchKMeans on the EP-151 vectors, k from
   a grid 8–30 by silhouette on a 5 000-vector subsample (seed policy EP-36); per cluster: size,
   `note_type` mix, `anchor_year_group` mix, top-20 c-TF-IDF terms over the cluster's section text.
   **Vocabulary export rule** — implemented once as `disclose.filter_vocabulary(terms, min_notes=11,
   min_subjects=11)` (small, tested extension of EP-43): a term is exportable only if it occurs in
   ≥ 11 notes of ≥ 11 subjects, is not `___`, and is not a number of ≥ 5 digits. Figures via `viz/`:
   cluster sizes, term bars, and a hex-binned PCA projection with bins < 11 suppressed (never
   per-note points). `text/topics.py` and `text/classify.py` call `text.guard.ensure_local_only()`
   at import; no network/HF calls (`HF_HUB_OFFLINE=1`).
2. **Classification** (`text/classify.py`) — target `sepsis3@<version>` at hadm (EP-42) joined via
   note → `hadm_id`; inputs (a) TF-IDF 1–2-grams (`min_df=11`) + logistic regression, (b) EP-151
   embeddings + logistic regression; temporal split by `anchor_year_group` (`ml/splits`, EP-104);
   AUROC / AUPRC / calibration via `ml/assess` (EP-105); registered with a model card (EP-106)
   labelled **exploratory**; top coefficients exported only through the vocabulary rule.
3. **CLI + runs** — `uv run --group text mwh text topics --with-notes --tier full` and
   `uv run --group text mwh text classify --target sepsis3 --with-notes --tier full` (always as
   logged background jobs: `--background --job notes-topics` / `--job notes-classify`, logs
   `%MWH_DATA_ROOT%\runs\jobs\notes-topics.log`, `notes-classify.log`; poll with `mwh jobs`);
   run records via `run.py`; every table/figure through `disclose`.
4. **Tests on the fixture notes** (`tests/ep/test_ep152.py`) — the generator's templates act as
   latent topics → adjusted Rand ≥ 0.8; planted sepsis mentions → classifier AUROC > 0.9 on
   fixture; `filter_vocabulary` **refuses** a term that occurs in < 11 notes.

## Out of scope

- Agreement between text mentions and structured events → EP-153 (Linkage to structured events).
- BERTopic / UMAP / HDBSCAN → `final-roadmap.md` (ML-3); LLM labelling → DL-4.
- Any prediction claim; any use of note-derived features in the P7 signature models (leakage —
  discharge summaries post-date the outcomes).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_152` green on fixture; `uv run --group dev mwh verify EP-152` green;
  import with `MWH_ALLOW_REMOTE=true` is refused (test).
- Full-tier topic and classification runs recorded (run ids, wall time) in the completion note;
  job ids/log paths recorded in the completion note.
- Model card + topic tables + term lists pass `uv run --group dev mwh disclose check`; the report
  section states claim type **exploratory** and that MIMIC-IV analyses are retrospective.
- No output contains note text, per-note vectors/points, or ids.

## Parked → final-roadmap.md

- Multi-label ICD-10 coding from discharge summaries (classic benchmark) — trigger: portfolio
  breadth; hazard: thousands of labels, compute (v2 TXT-4).
- Semi-supervised / weak-supervision labelling from EP-150 rules (Snorkel-style) — trigger: a
  second supervised text task is wanted.
