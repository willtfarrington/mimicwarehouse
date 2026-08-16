# EP-24 — Stage emar + emar_detail ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

## Context

The electronic medication administration record: `emar.csv` (~6 GB) and `emar_detail.csv`
(~8 GB), ~14 GB combined, both subject-keyed and joined on the composite key
`(emar_id, emar_seq)`. `emar_id` is a VARCHAR that embeds the subject id
(`'<subject_id>-<n>'`), so it is an **identifier column** for `safe_query` (EP-30) and
must be listed as such in `keys.yaml`. `emar_detail` has **no timestamp of its own** — its
time is `emar.charttime` via the join — so its sort key is the natural key rather than
`(subject_id, time)`; DESIGN §7 allows that (natural keys). Both load through the two-pass
bucketed path (EP-18) via `mwh build` (EP-19) as one background job (single writer:
sequential steps). Layout per DESIGN §5 (**D-17**, **D-18**); dev buckets first. EP-28
records timings and appends this brief's completion note. Free-text-ish columns
(`emar_detail.reason_for_no_barcode`, `product_description*`) are drug/administration
labels, not notes; `safe_query`'s long-string heuristic covers them.

## In scope

1. **DAG steps** — `stage.mimiciv_hosp.emar` and `stage.mimiciv_hosp.emar_detail`
   (`depends_on: [stage.mimiciv_hosp.emar]`), tags `[large, hosp, emar]`, tiers
   `[fixture, dev, full, demo]`, sources `mimic-iv-3.1/hosp/emar.csv` / `emar_detail.csv`.
   Expected contract values (EP-9; fix there with a dated note if different): both
   `load_class: large`, partitioned; `sort_keys` `[subject_id, charttime, emar_seq]` for
   `emar` and `[subject_id, emar_id, emar_seq, parent_field_ordinal]` for `emar_detail`.
2. **Contract check** — types per EP-9 (`emar_seq` INTEGER, `pharmacy_id`, `poe_id`,
   `enter_provider_id` identifiers, `charttime`/`scheduletime`/`storetime` TIMESTAMP,
   `emar_detail.parent_field_ordinal` DOUBLE, dose/rate columns as declared); add
   `emar_id`, `poe_id`, `pharmacy_id`, `enter_provider_id` to the identifier list in
   `keys.yaml` if missing (dated note). `loader_reject_max = 0`.
3. **Launch** —
   `uv run --group dev mwh build --tier full --tag emar --background --job stage-emar-full`
   at the start of the session; `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-emar-full.log`), build id, start time; poll with
   `mwh jobs --job stage-emar-full --tail 5`. Expected: both tables complete within about an
   hour; do not wait past the session (EP-28 verifies).
4. **Fixture tests** (`tests/ep/test_ep24.py`, `@pytest.mark.ep_24`) — fixture build of both
   steps via the runner into `tmp_path`: layout/sortedness/manifests/status as in EP-18;
   ledger lines; `emar_detail` rows all join to an `emar` row on `(emar_id, emar_seq)`
   (a `count(*)` of unmatched rows equals 0 — on fixture data, in-process).
5. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`: both counts
   positive and equal to the dev-bucket manifest sums; unmatched `emar_detail` rows on the
   dev tier counted and reported (a count; expected 0 or a small documented number — record
   it, do not fail); sortedness of `emar` by `subject_id, charttime` from row-group stats.
6. **Notes for EP-28** — throughput, RSS and `tmp\duckdb` high-water for a VARCHAR-heavy
   table (emar_detail) captured from the log into the launch note.

## Out of scope

- Timing/RSS/disk recording and completion note → EP-28.
- Medication code sets, drug-name/RxNorm mapping → EP-40, EP-143; antibiotics-timing workflows → EP-86/EP-144.
- Referential-integrity suites across all tables → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_24` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-24` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-emar-full.log`; job id and build id recorded here; timing verified by EP-28.
- `keys.yaml` lists `emar_id` as an identifier (checked by a test); no rows in logs or tool output.
