# EP-151 — Embeddings (CPU-capable, GPU-accelerated) + similarity search

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-149 (Note search + sectioning) · **Blocks:** EP-152 (Topic discovery + classification), EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Local sentence-transformers embeddings over note **sections** (capability category 27; standing
decision: embeddings on samples first — the full 2.65 M-document corpus is parked as TXT-2). CPU is
the default; the `gpu` group from EP-121 (torch from the cu130 index, D-16; 8 GB VRAM, sm_120)
accelerates the same code path. Model weights are fetched once by an explicit owner command and
their licence is recorded (GOVERNANCE §10); afterwards the text modules run offline. Note text is
consumed only inside the embedding job; what leaves the notes lake is vectors, aggregates and
throughput numbers (GOVERNANCE §9). Builds on the sectioning offsets of EP-149.

## Scope sketch (refine at re-plan)

1. **Text model registry** (`src/mimicwarehouse/text/models.py`;
   `%MWH_DATA_ROOT%\models\text\<name>\model.yaml` with source, licence, sha256, dim, max_seq_len)
   — default `sentence-transformers/all-MiniLM-L6-v2` (Apache-2.0, 384-d, CPU-fast); a biomedical
   alternative with a research-permitting licence is chosen at re-plan and recorded in the brief.
   `uv run --group text mwh text models fetch <name> --allow-download` is the **only** network
   call (weights only, owner-run, audited); the modules then set `HF_HUB_OFFLINE=1` /
   `TRANSFORMERS_OFFLINE=1` and `text.guard.ensure_local_only()` refuses `MWH_ALLOW_REMOTE=true`.
2. **Embedding job** (`text/embed.py`, DAG `notes-embed`) — stratified sample (e.g. 20 000
   discharge `brief_hospital_course` + 20 000 radiology `impression` sections drawn first from
   buckets 0–4; seed policy EP-36), chunk ≤ 256 tokens, mean-pool per section, float16 vectors →
   `notes\lake\derived\embeddings\<model>@<sha>\subject_bucket=NN\*.parquet (note_id, section_name,
   chunk_idx, vector)`; device auto (`cuda` when `torch.cuda.is_available()` and the `gpu` group is
   installed, batch ≤ 256 within a ≤ 6 GB working set; else CPU batch 32); docs/s for both devices
   and peak VRAM into the benchmark ledger. Launch:
   `uv run --group text --group gpu mwh build --tier full --dag notes-embed --with-notes`
   (drop `--group gpu` for the CPU run), log `%MWH_DATA_ROOT%\runs\jobs\notes-embed.log`.
3. **Similarity search** (`text/similarity.py`) — brute-force cosine over the sample (numpy or
   DuckDB `array_cosine_similarity`); `similar_notes(query_text | note_id, k)`; the ids path is
   owner-only and audited (like `owner_hits`); DuckDB VSS HNSW behind an opt-in flag only
   (persistence experimental — roadmap Risk 5).
4. **Evaluation aggregate** — neighbour concept agreement: for each EP-150 concept, the share of
   top-10 neighbours of concept-affirmed sections that are also affirmed vs the base rate (lift),
   by model × device; a suppressed table proving the vectors carry clinical signal. Run record via
   `run.py`.
5. **Tests on the fixture notes** (`tests/ep/test_ep151.py`) — deterministic CPU embeddings (shape,
   dim, seed); planted near-duplicate synthetic notes rank first; guard refuses
   `MWH_ALLOW_REMOTE=true` and refuses `fetch` without `--allow-download`; GPU tests skipped when
   CUDA is absent.

## Out of scope

- Topics and classification → EP-152 (Topic discovery + classification).
- Full-corpus embeddings + LanceDB / VSS index → `final-roadmap.md` (TXT-2); clinical encoder
  fine-tune → DL-3.
- Semantic search on the app page → EP-154 is search-only (BM25); parked in EP-149.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_151` green on fixture; `uv run --group dev mwh verify EP-151` green.
- Full-tier sample embedding job launched in the background; run id, docs/s (CPU and, if the
  `gpu` group is installed, GPU), peak VRAM and disk recorded in the completion note.
- `model.yaml` records source, licence and sha256 for every weight set used.
- Neighbour-agreement table passes `uv run --group dev mwh disclose check`; no note text or ids
  in any output.

## Parked → final-roadmap.md

- Cross-encoder re-ranking over the bi-encoder candidates — trigger: retrieval quality matters
  for a v2 semantic-search page.
- Biomedical embedding models under non-permissive licences — trigger: owner accepts the licence.
