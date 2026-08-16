# EP-102 — Model-ready dataset A: feature spec, windows, normalization, indicators

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-55 (Latency marts A: first-day features + itemid rollups ⏱), EP-47 (Cohort compiler, materialization, attrition, snapshot) · **Blocks:** EP-103 (Model-ready dataset B: patient-safe partitions + feature dictionary), EP-123 (Bounded sequence model (GRU/GRU-D on 48 h) (stretch)), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Opens P7 and capability category 34 (model-ready dataset generation): a declarative, versioned
feature specification that turns a materialised cohort (EP-47) plus the first-day marts (EP-55) and
the itemid/unit curation (EP-39) into a leakage-safe feature matrix under `marts/ml/`. It implements
D-17 (DuckDB does the heavy lifting, Polars at the boundary), D-18 (fixture/dev/full tiers) and D-6
(the signature trio EP-110/111/112 all consume this module). Time semantics come from EP-34: features
are computed on within-patient relative time from an index event; `anchor_year_group` is carried as a
column because EP-103/104 split on it; ages ≥ 89 appear as 91 and are kept as-is (documented, not
"fixed"). Labels are joined by name from the endpoint library (EP-75/76).

## Scope sketch (refine at re-plan)

1. **`FeatureSpec` pydantic model + YAML** (`src/mimicwarehouse/ml/datasets.py`; specs under
   `ml/specs/*.yaml`): `dataset_id`, `version`, `grain` (EP-34 registry key), `cohort_ref`
   (`<cohort_id>@<version>`), `index_time` (e.g. `icu_intime`), `observation_window` (`[start_h, end_h)`,
   default `[0, 24)`), `label_ref` (endpoint name; its label window lies strictly after the observation
   window), `feature_groups` (demographics; first-day vitals/labs min/max/mean/first/last/count from the
   EP-55 marts; comorbidity scores from concepts; ventilation/vasopressor flags), `normalization`
   (`zscore` | `robust` | `none` — parameters are fitted later on the training partition only; this EP
   ships the transform spec and `fit_transform(train)/transform(other)`), `indicators`
   (`<feature>__measured` per aggregated measurement), `clip_to_plausible` (EP-39 ranges), `categorical`
   (one-hot with an explicit level list).
2. **Builder** — `build_dataset(spec, tier)`: deterministic SQL (one CTE per feature group) via the
   catalog; asserts the leakage invariant (every contributing event time < index + window end; label
   events ≥ window end); writes `marts/ml/<dataset_id>@<version>/features.parquet`, `labels.parquet`,
   `spec.yaml`, `manifest.json` (snapshot ids, row count, spec hash); records a run (EP-35). Registered
   as a DAG step: `uv run --group dev mwh build --tier dev --select ml_dataset_<id>`.
3. **Representative dataset** — the tracer-bullet cohort (first ICU stay adults, EP-31/EP-47 spec),
   first-24 h feature groups, label `in_hospital_mortality`; built on fixture and dev in-session, full
   tier as a logged background job.
4. **Tests** (`tests/ep/test_ep102.py`, `@pytest.mark.ep_102`): a spec whose observation window overlaps
   the label window is *refused*; an indicator column exists for every measurement feature; fixture row
   count equals the cohort's final attrition count; the build is deterministic (manifest hash).

## Out of scope

- Patient-safe partitions and the feature dictionary → EP-103; split iterators → EP-104.
- Hourly sequence tensors (48 h) → EP-123; endpoint definitions → EP-75/76.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_102` green on fixture (+dev); `uv run --group dev mwh verify EP-102` green.
- Full-tier build as a background job (`mwh build --tier full --select ml_dataset_<id>`; log at
  `%MWH_DATA_ROOT%\runs\jobs\ep102.log`); run id, wall time and peak RSS in the completion note and the
  benchmark ledger; `manifest.json` (counts/hashes only) passes `mwh disclose check`.

## Parked → final-roadmap.md

- FIDDLE-style automatic featurisation and MEDS/meds-tab export of the matrix — trigger: an external
  benchmark comparison is wanted (see final-roadmap 34–35).
