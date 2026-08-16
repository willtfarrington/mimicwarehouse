# EP-63 — Phenotype Studio page

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Capability 3 (computable, versioned phenotypes) exists as code: the code-set registry with
semver + definition hash compiled to `meta.codeset_members` and the ICD-9→10 GEM utility
(EP-40), the phenotype engine (`phenotypes/*.yaml` → SQL, versioned) with T2DM (EP-41), and
sepsis-3 (via the mimic-code `sepsis3` concept) and KDIGO AKI stage (via `kdigo_stages`)
(EP-42, full-tier prevalence recorded). This brief adds the Streamlit UI (D-21; DESIGN §16 "Phenotype
Studio") on the EP-57 shell with EP-58 wrappers (small cells badged in-app, D-33), following the EP-61/62 page pattern: browse code sets
and phenotypes with versions, apply a phenotype to a cohort or a whole grain, preview prevalence.
MIMIC caveats visible here: dual ICD-9/10 code sets and the ~2015 switch (dx-based prevalence
steps by era), `anchor_year_group` as the only temporal axis, `dod` irrelevant (no outcomes
here). Prevalence CIs are computed inline with statsmodels' Wilson interval; EP-68 replaces the
call with `stats.rates` (leave a `# TODO(EP-68)` marker).

## In scope

1. **Browse** — page `app/pages/21_phenotype_studio.py` (registry id `phenotype_studio`,
   section Cohorts). *Code sets* tab: `codesets.registry.list()` (name, version, hash, systems,
   member counts); search by code or label over `meta.codeset_members`; for a selected set the
   GEM crosswalk view (EP-40): mapped/unmapped counts ICD-9 ↔ ICD-10. *Phenotypes* tab:
   `phenotypes.registry.list()` (T2DM, sepsis-3, KDIGO AKI + any later), definition summary
   from YAML (dx / med / lab / concept / temporal components), rendered SQL in `st.code`,
   version history with hashes and changelog.
2. **Apply** — select phenotype@version and target: a materialised cohort (EP-47 registry) or a
   whole grain on the tier (`hadm` / `icustay`); `phenotypes.prevalence(pheno, target, tier,
   by=[...])` (add to `phenotypes/` if EP-41/42 exposes only `apply()`; it must return counts,
   never per-unit rows) with `by` ∈ {none, era, gender, age_band, icd_era}; result n, N, % with
   Wilson 95 % CI (`statsmodels.stats.proportion.proportion_confint(method="wilson")`) via
   `safe_dataframe`; chart via `src/mimicwarehouse/viz/prevalence.py: prevalence_bars(df)`
   (bars + CI rules; reused by EP-69); an ICD-era caveat panel appears for dx-based phenotypes.
3. **Compare versions** — two versions of one phenotype → counts side by side and overlap
   (both / only A / only B) via `phenotypes.compare(a, b, target, tier)` (add if missing);
   small cells badged.
4. **Latency + bench + screenshots** — full-tier apply of sepsis-3 and KDIGO AKI hits the
   materialised concept tables and T2DM hits `diagnoses_icd` × `meta.codeset_members`; each
   ≤ 5 s (`MWH_APP_RECORD_LATENCY=1`, completion note); the EP-56 bench already has
   `sepsis3 prevalence by era`; add `t2dm_prevalence_icd_era`; manifest entries
   `phenotype-studio-browse`, `phenotype-studio-apply` captured on demo.
5. **Tests** `tests/ep/test_ep63.py` (`@pytest.mark.ep_63`, ui group): AppTest on fixture —
   ≥ 3 phenotypes listed with version + hash; code-set search for an ICD-10 prefix present in
   the fixture T2DM set (e.g. `E11`) returns members; apply sepsis-3 to all fixture icustays
   returns n/N/%/CI columns and a chart; compare works on a fixture-only second version of T2DM
   (create it under `tests/fixtures/phenotypes/`); no rendered frame has identifier columns;
   `ui_lint` passes; dev-marked apply on dev; full latencies recorded.

## Out of scope

- Phenotype/code-set authoring and validation logic → EP-40/41/42 (read + apply only here).
- Rates module with denominators/person-time → EP-68; rates page → EP-69.
- New phenotypes (HF, COPD, CKD, …) → parked (v2 PHE-1, already listed).

## Verification / acceptance

- `uv run --group ui poe test -m ep_63` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-63` green (includes `ui_lint`).
- On dev: browse → select → apply → prevalence with CI and chart; version compare shows overlap.
- Full-tier latencies for sepsis-3, KDIGO AKI and T2DM apply recorded (each ≤ 5 s).
- Demo screenshots `phenotype-studio-browse-*.png`, `phenotype-studio-apply-*.png` + sidecars;
  `mwh disclose check docs/screenshots` passes.
