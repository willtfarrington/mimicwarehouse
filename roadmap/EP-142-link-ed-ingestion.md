# EP-142 — ED ingestion via wizard → mimiciv_ed + ED concepts

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-141 (Linkage Wizard B (validate → coverage → commit)) · **Blocks:** EP-144 (ED-enabled workflow (ED triage → admission; time-to-antibiotics)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

The real-data test of the wizard (D-4: ED enters the warehouse only here, through the Linkage
Wizard). MIMIC-IV-ED 2.2 (`source material/mimic-iv-ed-2.2/ed/`: `edstays`, `triage`, `vitalsign`,
`pyxis`, `medrecon`, `diagnosis`; ~0.7 GB) is profiled, mapped, validated, coverage-measured and
committed into `mimiciv_ed` on the dev and full tiers. The `ed` schema contract already exists
(EP-9) and the ED Demo already sits in the demo tier (EP-22), so the mapping is mostly confirmation;
the value here is the recorded validation/coverage trail and the ED-specific hazards: ED 2.2 spans
2011–2019 (partial linkage by design, README standing decision), `hadm_id` is null for
non-admitted stays, `edstays.stay_id` is an ED stay (grain `edstay`, not ICU), `chiefcomplaint` is
free text, `pain` is mixed text/number, timestamps carry the same per-patient shift as hosp so
ED → admission intervals are valid within patient. No ED concepts exist upstream in mimic-code
(README risk 2), so the derived ED tables are ours (D-19 applies only if the pinned commit turns out
to ship `mimic-iv-ed` concepts — then adopt them and note it). Category 35.

## Scope sketch (refine at re-plan)

1. **Wizard run on real ED** — register entry (`ext/mimic_iv_ed_2_2/source.yaml`: PhysioNet
   Credentialed Health Data License 1.5.0, own DUA date, DOI 10.13026/5ntk-km72, file manifest);
   profile → map (`mimiciv_ed.<table>`, `subject_bucket` partitioning, `chiefcomplaint` role
   `free_text`, `pain` cast `TRY_CAST` + raw text kept as `pain_text` free_text, triage/vitalsign
   units via `units.py`); validate → coverage on **dev** first, then commit **dev**; then commit
   **full** as the background job `ext_mimic_iv_ed_2_2_full` (log under `runs\jobs\`), which for
   0.7 GB should finish within the session — record wall time, peak RSS, disk delta in the benchmark
   ledger.
2. **Coverage findings recorded** — the coverage-by-`anchor_year_group` table (suppressed) is the
   headline: ~0 for 2008–2010 and 2020–2022, partial for 2011–2019; temporal consistency of
   `edstays.outtime` vs `admissions.admittime`; share of admissions with a linked ED stay; ED-stay
   → admission cardinality. Numbers go into the completion note only after `mwh disclose check`.
3. **ED derived tables (ours)** — DAG steps in `concepts/ed/` producing `mimiciv_derived.ed_stays`
   (edstay → hadm link, `ed_los_hours`, admitted flag, disposition/arrival categories, era),
   `mimiciv_derived.ed_triage` (harmonized triage vitals + acuity as ordinal, plausibility bounds),
   `mimiciv_derived.ed_meds` (pyxis dispensations with `charttime`, drug name normalized, GSN, joined
   to EP-40 drug-name code sets — antibiotic flag using the same list as mimic-code `antibiotic`);
   count-pinned on demo and dev.
4. **Grain + governance wiring** — flip the `edstay` placeholder in the unit-of-analysis registry
   (`timesem.py`, EP-34) to `available=True` (DESIGN §7: key `stay_id` in `mimiciv_ed`, anchor
   `intime`, index-event rule `first_ed_stay`); `keys.yaml` documents that
   `mimiciv_ed.*.stay_id` ≠ `mimiciv_icu.*.stay_id`; `safe_query` refuses `chiefcomplaint`/`pain_text`;
   `disclose.check` knows both as free text; `guard.py` unchanged (same id bands).
5. **Tests** `tests/ep/test_ep142.py` (`@pytest.mark.ep_142`; fixture + dev, full opt-in):
   fixture — the ED-like fixture flows through the same DAG steps and derived tables; dev — key
   integrity, `ed_stays` row count = `edstays` row count, no `ed_los_hours` < 0, safe_query refusal
   of `chiefcomplaint`; demo count-pins for the three derived tables.

## Out of scope

- The ED-enabled analysis (time-to-antibiotics) → EP-144.
- Chief-complaint categorization / text features → parked; notes track → P10.
- Reference tables → EP-143.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_142` green on fixture + dev; `uv run --group dev mwh verify EP-142` green.
- Full-tier commit job id, log path, wall time and snapshot id recorded in the completion note;
  `mwh link status mimic_iv_ed_2_2` = `committed` on dev and full;
  `mwh sql "SELECT anchor_year_group, count(*) …" --tier full` era coverage reproduces the report.
- `ext/mimic_iv_ed_2_2/linkage_report.md` passes `uv run --group dev mwh disclose check` and is
  promoted to `docs/analyses/` assets by EP-146.

## Parked → final-roadmap.md

- Chief-complaint normalization (HCUP reason-for-visit style categories) — trigger: an ED workflow
  needs presenting-complaint covariates.
- ED `vitalsign` hourly bins in the latency marts — trigger: an ED page needing ≤ 5 s latency.
