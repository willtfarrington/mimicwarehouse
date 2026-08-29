# EP-27 — Stage icu event tables ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The contract already carries the tie-broken `sort_keys` item 1 expects (EP-169) —
> item 1 is a *verify*, not a contract edit [FC-3]. (2) The `identifier`/`free_text` flag
> machinery ships with EP-17; item 2 only *verifies* these tables' flags (`orderid`,
> `linkorderid`, `caregiver_id` identifiers) — a missing flag is fixed with a dated note; flag
> edits move `content_hash()` only (the fixture manifest pins `structural_hash()`, EP-169)
> [FC-5]. (3) The dev-marked test requests EP-168's `dev_ready("<step>")` readiness fixture per
> step (skip-with-reason until the status entry exists), so "green once dev-ready" is real, not
> vacuous [VT-1]. (Note `ingredientevents` has no `validate.sql` expectation — see the EP-20/
> EP-28 amendments [FC-12].)

## Context

Completes `mimiciv_icu`: `inputevents.csv` (~2.7 GB; infusions/boluses with `starttime`,
`endtime`, `amount`, `rate`, `orderid`, `linkorderid`), `ingredientevents.csv` (~2.4 GB;
the ingredient breakdown of the same orders) and `datetimeevents.csv` (~1.1 GB; charted
date/time observations whose `value` is itself a shifted TIMESTAMP). Together with EP-20
(`d_items, caregiver, icustays, procedureevents, outputevents`) and EP-26 (`chartevents`)
every one of the 9 icu tables in the contract is then staged. Layout per DESIGN §5
(**D-17**, **D-18**) through the loader (EP-17/18) and `mwh build` (EP-19); one background
job with sequential steps; dev buckets sorted first. Time semantics reminder for later
briefs (DESIGN §7): all timestamps are per-patient shifted, so these tables are only ever
analysed with within-patient relative times. `orderid`, `linkorderid` and `caregiver_id`
are identifiers for `safe_query` (EP-30). EP-28 records timings and appends the completion
note.

## In scope

1. **DAG steps** (tags `[icu-events, icu]`, tiers `[fixture, dev, full, demo]`) —
   `stage.mimiciv_icu.inputevents`, `stage.mimiciv_icu.ingredientevents`,
   `stage.mimiciv_icu.datetimeevents`. Expected contract values (EP-9; fix there with a
   dated note if different): all three `load_class: large`, partitioned; `sort_keys` after
   `subject_id`: inputevents `[starttime, orderid]`, ingredientevents `[starttime, orderid]`,
   datetimeevents `[charttime, itemid]`.
2. **Contract check + flags** — typed load with `loader_reject_max = 0` (`datetimeevents.value`
   TIMESTAMP, `inputevents.amount/rate/originalamount/originalrate` DOUBLE,
   `patientweight` DOUBLE, `statusdescription`, `ordercategoryname` VARCHAR labels);
   `orderid`, `linkorderid`, `caregiver_id` in `keys.yaml`'s identifier list (dated note).
3. **Launch** —
   `uv run --group dev mwh build --tier full --tag icu-events --background --job stage-icu-events-full`;
   `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-icu-events-full.log`), build id, start time; poll with
   `mwh jobs --job stage-icu-events-full --tail 5`; expected well under an hour; EP-28
   verifies. Do not launch while EP-26's chartevents job is running (`mwh jobs` shows it) —
   the build lock will refuse and the job will show `failed`; wait or sequence.
4. **Coverage assertion** — extend the coverage test so the union of `stage.yaml` icu steps
   (`small` from EP-20, `chartevents` from EP-26, `icu-events` here) equals the 9 icu tables
   of the contract exactly once, and the whole DAG covers all 31 hosp + icu tables.
5. **Fixture tests** (`tests/ep/test_ep27.py`, `@pytest.mark.ep_27`) — fixture build of the
   three steps via the runner: layout, sortedness, manifests, status, ledger lines; on the
   fixture, every `inputevents.stay_id` exists in `icustays` and every
   `ingredientevents.orderid` exists in `inputevents` (unmatched counts 0).
6. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`: counts positive
   and equal to the dev-bucket manifest sums; unmatched-key counts for the two joins above
   reported (counts only); `datetimeevents` `count(*) WHERE value IS NULL` reported.

## Out of scope

- Timing/RSS/disk recording, completion note → EP-28.
- Vasopressor / ventilation concepts over these tables → EP-37/38; unit harmonization → EP-39; first-day marts → EP-55.
- Referential-integrity suites → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_27` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-27` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-icu-events-full.log`; job id and build id recorded here; timing verified by EP-28.
- The coverage test proves all 31 hosp + icu contract tables are staged by exactly one brief; no rows in logs or tool output.
