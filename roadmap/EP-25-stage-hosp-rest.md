# EP-25 — Stage remaining hosp tables ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

## Context

Completes `mimiciv_hosp`: `pharmacy.csv` (~3.8 GB), `prescriptions.csv` (~3.3 GB),
`poe.csv` (~4.8 GB) and `microbiologyevents.csv` (~0.9 GB) — the four hosp tables not
covered by EP-20 (small tables), EP-23 (labevents) or EP-24 (emar). After this job every
one of the 22 hosp tables in the contract is staged. Layout per DESIGN §5 (**D-17**,
**D-18**) through the loader (EP-17/18) and `mwh build` (EP-19), one background job,
sequential steps (single writer). Facts that matter: `pharmacy` and `prescriptions` share
`pharmacy_id`; `poe` joins `poe_detail` (EP-20) on `poe_id` and carries `order_provider_id`;
`microbiologyevents.charttime` is nullable (`chartdate` always present) and its
**`comments` column is free text** (flag it for `safe_query`, EP-30); the medication
tables' `drug`/`medication`/`prod_strength` columns are labels, not notes. EP-28 records
timings and appends the completion note.

## In scope

1. **DAG steps** (tags `[hosp-rest, hosp]`, tiers `[fixture, dev, full, demo]`) —
   `stage.mimiciv_hosp.pharmacy`, `stage.mimiciv_hosp.prescriptions`,
   `stage.mimiciv_hosp.poe`, `stage.mimiciv_hosp.microbiologyevents`. Expected contract
   values (EP-9; fix there with a dated note if different): `load_class: large` for the
   first three, `small` for `microbiologyevents` (rule of thumb here and in EP-27: CSV > 1 GB
   → `large`); `sort_keys` after `subject_id`: pharmacy `[starttime, pharmacy_id]`,
   prescriptions `[starttime, pharmacy_id]`, poe `[ordertime, poe_seq]`, microbiologyevents
   `[chartdate, charttime, microevent_id]`.
2. **Contract check + flags** — mark `microbiologyevents.comments` `free_text: true`; ensure
   `pharmacy_id`, `poe_id`, `order_provider_id`, `microevent_id`, `micro_specimen_id` are in
   `keys.yaml`'s identifier list (dated note); typed load with `loader_reject_max = 0`
   (watch `prescriptions.dose_val_rx`/`form_val_disp` VARCHAR, `poe.discontinue_of_poe_id`
   nullable, `microbiologyevents.dilution_value` DOUBLE).
3. **Launch** —
   `uv run --group dev mwh build --tier full --tag hosp-rest --background --job stage-hosp-rest-full`;
   `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-hosp-rest-full.log`), build id, start time; poll with
   `mwh jobs --job stage-hosp-rest-full --tail 5`; expected under an hour in total; EP-28
   verifies.
4. **Coverage assertion** — extend the EP-20 coverage test (or add one here) so that the
   union of `stage.yaml` steps tagged `small`, `large`/`hosp-rest`/`emar` and the EP-23
   step equals the 22 hosp tables of the contract exactly once.
5. **Fixture tests** (`tests/ep/test_ep25.py`, `@pytest.mark.ep_25`) — fixture build of the
   four steps via the runner: layout, sortedness, manifests, status, ledger lines; on the
   fixture, `prescriptions.pharmacy_id` values all exist in `pharmacy` (unmatched count 0)
   and `poe_detail.poe_id` all exist in `poe`.
6. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`: counts positive
   and equal to dev-bucket manifest sums for the four tables; unmatched-key counts for the
   two joins above reported (counts only, recorded not asserted).

## Out of scope

- Timing/RSS/disk recording, completion note → EP-28.
- Drug/ATC/RxNorm code sets and mapping → EP-40, EP-143; microbiology-based phenotypes → EP-41/42.
- Referential-integrity suites and QC profiles → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_25` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-25` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-hosp-rest-full.log`; job id and build id recorded here; timing verified by EP-28.
- The coverage test proves all 22 hosp tables are assigned exactly once; no rows in logs or tool output.
