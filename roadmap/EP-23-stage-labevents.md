# EP-23 — Stage labevents ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

## Context

`labevents.csv` (~18 GB, on the order of 1.5 × 10^8 rows — `validate.sql` has the exact
count) is the second-largest table and the **dress rehearsal for chartevents** (EP-26): the
first real run of the two-pass bucketed loader (EP-18) at scale through `mwh build`
(EP-19), as a logged background job because foreground commands are capped at ~10 min.
Layout per DESIGN §5 (**D-17**, **D-18**): `lake/core/mimiciv_hosp/labevents/subject_bucket=<n>/part-0.parquet`,
sorted `(subject_id, charttime)`, ZSTD-3, ~1 M-row groups; buckets 0–4 are sorted first so
the dev tier becomes usable early. Contract facts to check (EP-9): `labevent_id` PK,
`hadm_id` nullable (outpatient labs), `specimen_id`, `itemid` → `d_labitems`,
`order_provider_id` (identifier), `charttime`/`storetime` (per-patient shifted timestamps),
`value` VARCHAR, `valuenum` DOUBLE, `valueuom`, `ref_range_lower/upper`, `flag`,
`priority`, and **`comments` — free text**, which must be flagged so `safe_query` (EP-30)
refuses it. Timing, peak RSS and disk are recorded by EP-28, which appends this brief's
completion note. Machine facts: 64 GB RAM (`memory_limit` 36–40 GB), one NVMe, ≥ 100 GB
free rule, laptop thermals.

## In scope

1. **DAG step** — add `stage.mimiciv_hosp.labevents` to `stage.yaml`
   (`source: mimic-iv-3.1/hosp/labevents.csv`, tags `[large, hosp]`, tiers
   `[fixture, dev, full, demo]`); the contract (EP-9) must say `load_class: large`,
   `partitioned: true`, `sort_keys: [subject_id, charttime, itemid]` — fix it there with a
   dated note if not (the YAML step carries no overrides).
2. **Contract flags** — in EP-9's YAML mark `labevents.comments` `free_text: true` and
   confirm `order_provider_id` is in `keys.yaml`'s identifier list (dated note in the
   YAML header). Verify the declared types load with `loader_reject_max = 0`; a parse
   failure is fixed in the contract, never by widening to VARCHAR blindly.
3. **Launch the full job first thing** —
   `uv run --group dev mwh build --tier full --select stage.mimiciv_hosp.labevents --background --job stage-labevents-full`;
   append a `> **Launch note (date).**` to this brief with the job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-labevents-full.log`), start time and build id. Poll
   with `uv run --group dev mwh jobs --job stage-labevents-full --tail 5` every 5–10 min
   while doing items 4–6. Expected: pass 1 in the 10–25 min range, `dev-ready` a few
   minutes later, completion inside the hour — do not wait past the session; EP-28
   verifies and records the numbers.
4. **Fixture tests** (`tests/ep/test_ep23.py`, `@pytest.mark.ep_23`) — fixture
   `labevents` through the large path via the runner
   (`mwh build --tier fixture --select stage.mimiciv_hosp.labevents --data-root <tmp>`):
   layout, one sorted `part-0.parquet` per partition, no `raw_*`/`_sorting.tmp`, manifest
   lines, `status.json` complete, ledger lines with `phase = pass1|pass2|total`; the
   contract shows `comments` as free text.
5. **Dev-marked test** (runs when `status.json` shows `dev_ready`; otherwise skips with a
   clear reason) — through `open_catalog("dev")` (EP-21): `SELECT count(*) FROM
   mimiciv_hosp.labevents` is positive and equals the sum of manifest rows for buckets 0–4;
   per-file `parquet_metadata()` row-group `subject_id` min/max are non-decreasing (sorted);
   `count(*) WHERE hadm_id IS NULL` is positive (outpatient labs exist — a count only).
6. **Rehearsal notes for EP-26** — while the job runs, capture from the log: throughput
   (MB of CSV per second in pass 1), peak RSS, `tmp\duckdb` high-water mark, per-bucket
   sort time; write them into the launch note as "chartevents planning inputs" (EP-26
   scales them ×2.2 and decides on `--sweeps`).

## Out of scope

- Timing/RSS/disk recording, completion note, count reconciliation → EP-28.
- `chartevents` → EP-26; `emar*` → EP-24; other hosp tables → EP-25.
- Lab unit harmonization / plausibility → EP-39; LOINC mapping → EP-143; QC profiles → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_23` green on fixture; the `tier("dev")`-marked test green once `dev-ready` (or documented as pending for EP-28); `uv run --group dev mwh verify EP-23` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-labevents-full.log`; job id and build id recorded in the launch note; timing verified by EP-28.
- `mwh jobs --job stage-labevents-full` shows `running` or `done` — never a foreground scan; no rows in the log or tool output.
