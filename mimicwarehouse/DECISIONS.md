# mimicwarehouse — DECISIONS

Architecture-decision log. Numbered decisions are **settled with the project owner**
(they/them) in the planning session of **2026-08-16** after ~60 clarifying questions
across stack, data architecture, methods, UI, governance and roadmap process, informed by
a research pass over the 2026 MIMIC-IV / Python / Windows tooling landscape. Later
sessions append addenda (`> **Addendum (date, EP-n).** …`) under the decision they refine
and add new numbered decisions at the end; nothing is rewritten.

Format: **D-n Title.** Decision. *Why.* *Alternatives considered.*

---

## Purpose & scope

**D-1 Tie-breaker purpose = portfolio / employability.** When depth, breadth, polish
and speed conflict, prefer the demonstrable, documented, reproducible "pre-employment
v1". Audience: **both** data-science/ML hiring managers and clinical-informatics readers →
docs carry two reading paths. *Why:* the owner's stated goal for the DATA portfolio.
*Alternatives:* personal research instrument; open-source community tool; learning
vehicle.

**D-2 Horizon ≈ 3 months at several sessions/week; EP sizes S ≈ 30 min, M ≈ 1 h,
L ≈ 2 h.** Anything larger is split. Yields ~160 briefs, tagged core/stretch. *Why:* the
owner's split-when-in-doubt rule and hupsim's lesson that long sessions die uncommitted.
*Alternatives:* S/M/L = 1/2/4 h with ~110 briefs; 6-month horizon.

**D-3 MIMIC-IV-Note = optional late track.** Loaded into a segregated store late (P10,
after linkage), one representative text workflow (search + concept/negation extraction +
linkage to structured events); everything else → `final-roadmap.md`; go/no-go at the P7
re-plan. *Why:* separate DUA, highest-risk asset, GPU/time pressure. *Alternatives:*
first-class module in v1; excluded from v1.

**D-4 MIMIC-IV-ED enters through the Linkage Wizard.** Core warehouse = hosp + icu; ED
is ingested later (EP-142) as the real-data test of the additional-data ingestion &
linkage capability. *Why:* proves the wizard on real data with shared keys. *Alternatives:*
stage ED on day one; both.

**D-5 Clinical themes vary per category.** Each capability's representative workflow
picks its own best-fit clinical theme (portfolio variety); the tracer bullet is
first-ICU-stay adults → in-hospital mortality. *Alternatives:* a single sepsis-3 anchor;
AKI; ventilation.

**D-6 Signature depth = prediction + assessment + leakage/drift.** Three representative
workflows and the most polish; every other category exactly one. *Alternatives:* causal /
target-trial; cohort-tooling; survival/longitudinal.

**D-7 Deep-learning workflow = pretrained tabular foundation model (TabPFN-class) on
structured features vs GBM.** VRAM-bounded, licensed weights only, CPU fallback; a small
sequence model (GRU/GRU-D) is stretch. *Alternatives:* clinical text encoder (depends on
notes), time-series FM, EHR event-sequence FM (MEDS/FEMR).

**D-8 Ordering = foundation → early tracer bullet → breadth; re-plan EP at every phase
boundary; capstone/showcase EP per phase + final showcase phase.** *Alternatives:* strict
foundation-first; vertical slices from the start; ad-hoc addenda only.

**D-9 Brief depth = full briefs for P0–P4 now, charter briefs for P5–P11.** Each re-plan
writes full briefs for phase N+1 and re-charters N+2 (cross-phase edges pin two phases
ahead). *Alternatives:* everything full now; full P0–P2 only.

**D-10 Explicit resource-gathering EPs** for repos/awesome lists, ontologies/vocabularies,
papers/chapters reading list, open companion datasets (owner template steps 2–3).

**D-11 Visual identity early** — one S brief (wordmark, light+dark chart-safe palette,
Altair/Streamlit themes, README banner) so every later screenshot is consistent.

**D-12 Democratization = bootstrap script + docs site + demo mode** on the ODbL
MIMIC-IV Demo. *Alternatives:* README only; full PyPI packaging + CI.

## Stack

**D-13 Python throughout.** No Rust or JS toolchain in v1. *Why:* ~30 of 38 categories
exist only as Python libraries; DuckDB/Polars give compiled-engine speed from Python.
*Alternatives:* Python + Rust hot paths (PyO3); Rust-first (owner's hupsim precedent).

**D-14 Native Windows** (PowerShell + uv). Docker only for optional future services.
*Why:* full RAM/NVMe bandwidth to 98 GB of CSVs; CUDA via the installed driver.
*Alternatives:* WSL2 (3–10× slower on /mnt/c or duplicate the data); Docker Compose.

**D-15 uv-managed CPython 3.13, one venv** (`python-preference = only-managed`; system
3.14 untouched). scispaCy (requires < 3.13) only via a separate 3.12 uv project if ever
needed — never a workspace member (requires-python is intersected). *Why:* verified
cp313 Windows wheels for every library in the stack; spaCy has no cp314 wheels; dowhy caps
< 3.14. *Alternatives:* system 3.14 + 3.12 sidecar; 3.12 everywhere.

**D-16 CPU-first; GPU is an opt-in late track.** `gpu` dependency group installs torch
from `https://download.pytorch.org/whl/cu130` (`explicit=true`; PyPI torch is CPU-only on
Windows; cu126 lacks sm_120); XGBoost `device="cuda"` as comparator; LightGBM CPU is the
workhorse; PyMC + nutpie for Bayesian (JAX has no Windows CUDA). *Alternatives:* GPU from
the start; ignore GPU.

**D-17 DuckDB + Parquet lake canonical; layers raw CSV → typed Parquet → DuckDB
conformed catalog → derived concepts → marts; Polars primary, pandas at library
boundaries.** *Alternatives:* Postgres in Docker; Polars-only; ClickHouse; CSV → DuckDB
native only; pandas primary.

**D-18 Tiers fixture / demo / dev (5 %) / full.** Every EP passes tests on fixture+dev
and records a full-tier run with timing where meaningful; long full jobs run as
resumable background jobs verified by the next EP. *Alternatives:* sample only until late;
full only; full runs batched per phase.

**D-19 Adopt mimic-code `concepts_duckdb` (MIT), vendored at a pinned commit, tested,
fixes ported; re-derive only what is missing.** *Alternatives:* re-derive everything;
adopt as-is untested.

**D-20 Custom lightweight transform runner (`mwh build`).** YAML DAG of SQL/Python
steps, tier-aware, manifests/snapshot ids, timings. dbt-duckdb and SQLMesh → final-roadmap.
*Why:* provenance capture and tier switching are the point; ~600 LOC we control.

**D-21 App = Streamlit 1.61 multipage "Lab" app, one process; Altair/Vega-Lite
(+VegaFusion) primary, Plotly for timelines; linked brushing essential on Explorer.**
*Alternatives:* marimo apps (ranked first by the research panel for a solo builder —
see Judgment calls), Panel/HoloViz, Dash, notebook-first, CLI-only.

**D-22 marimo for scratch notebooks only** (zero-output `.py`); canonical logic lives in
the package. *Alternatives:* Jupyter + nbstripout; none.

**D-23 Reporting = Jinja2 → Markdown + self-contained HTML; PDF via Typst.** Formats
MD + HTML + PDF (no DOCX). *Alternatives:* Quarto (parked for narrative case studies);
notebook export.

**D-24 Run/provenance store = DuckDB `runs` views over per-run JSON sidecars +
append-only JSONL ledgers.** *Alternatives:* MLflow (parked as mirror); plain files.

**D-25 Protocol freeze = YAML protocol → content hash → registry entry before run;
amendments logged; runs must cite a frozen hash.** *Alternatives:* git commit as freeze;
documentation only.

**D-26 Raw provenance = local manifest (SHA256/size/rows) + row-count reconciliation vs
mimic-code `validate.sql`.** Plain CSVs cannot be checked against PhysioNet's
`SHA256SUMS.txt` (covers `.csv.gz` only). *Alternatives:* re-download `.csv.gz` (parked);
skip.

**D-27 Fixtures = synthetic mini-MIMIC generator (ids ≥ 90 000 000) committed +
on-demand MIMIC-IV Demo 2.2 (+ ED Demo) tier.** *Alternatives:* demo only; synthetic only.

**D-28 Latency ≤ 5 s typical on full data via marts; interactive pages default to
dev.** *Alternatives:* ≤ 2 s always; whatever DuckDB gives.

## Governance

**D-29 Data placement.** Repo + raw CSVs stay under `Documents` (local-only, BitLocker
on); derived data in a short data root outside the repo (`C:\mimicdata`, `MWH_DATA_ROOT`);
nothing on G:/D:. *Alternatives:* inside repo `data/`; inside `source material/`.

**D-30 Keep plain CSVs untouched** (~180 GB total footprint). *Alternatives:* re-gzip;
delete after verified Parquet.

**D-31 Claude sessions: aggregate-only via a safe-query wrapper** (k = 11 suppression,
no identifiers, no note text, audit-logged) + `CLAUDE.md`. *Alternatives:* schema-only;
same access as the owner.

**D-32 Row display allowed in-app for the owner** behind an explicit toggle with audit
entry; never exported; never in tool output. *Alternatives:* aggregate-only everywhere;
unrestricted.

**D-33 Small cells: warn at n < 11 in-app; suppress n < 11 on export/commit** with
complementary suppression. *Alternatives:* suppress everywhere; n < 5; none.

**D-34 MIT license; permissive-only imports; GPL tools only in the optional `gpl`
extra** (e.g. scikit-survival for one EP). *Alternatives:* Apache-2.0; allow GPL freely;
no exceptions.

**D-35 Vocabularies: free first** (ICD-9/10 dims, LOINC, RxNorm, ATC, AHRQ CCSR/
Elixhauser/Charlson code sets, CMS GEMs); UMLS/SNOMED/OMOP Athena as later optional EPs
(owner has no UTS account yet). *Alternatives:* Athena early; MIMIC dims only.

**D-36 Future data = reference/knowledge tables + other PhysioNet datasets.** Wizard =
profile → map concepts/units → validate keys/cardinality → measure linkage coverage →
commit, with a license register. *Alternatives:* external context datasets; generic only.

**D-37 Roadmap format = hupsim verbatim** (flat `EP-n-slug.md`, S/M/L, Depends-on/Blocks,
Context / In scope / Out of scope / Verification, ☑ commit-hash tables, two commits per
EP) with additions (Tier, Core/Stretch, ⏱, Parked). Design docs `DESIGN.md`,
`GOVERNANCE.md`, `DECISIONS.md` in `mimicwarehouse/`; `CLAUDE.md` at the repo root.
*Alternatives:* phase-prefixed ids; zero-padded ids.

**D-38 Owner-side Windows tuning** (owner performs; EP-0/EP-3 check and record):
Defender real-time exclusion for `C:\mimicdata` only, `LongPathsEnabled` (registry +
reboot), "Best performance" power plan when plugged in. *Alternatives:* none.

> **Addendum (2026-08-17, EP-0).** Non-elevated status probes run from the EP-0 session
> (repo root `C:\Users\willi\Documents\DATA\mimicwarehouse`, on C:):
>
> | Item | Probe | Status 2026-08-17 |
> |---|---|---|
> | `LongPathsEnabled` | `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem` | **1 (done)** |
> | BitLocker on C: | `System.Volume.BitLockerProtection` via Shell COM | **1 (on)** — matches owner check of 2026-08-16 |
> | Power plan / mode | `powercfg /getactivescheme`; registry `ActiveOverlayAcPowerScheme` | **Balanced** (scheme `381b4222…`, overlay `00000000…` = default) → **not yet** "Best performance"; owner: Settings › System › Power & battery › Power mode › *Best performance* (plugged in), or elevated `powercfg /overlaysetactive ded574b5-45a0-4f42-8737-46345c09c238` |
> | Cloud / virtual mounts | `Win32_LogicalDisk` | C: = Windows NTFS fixed (951 GB, 416 GB free); D: = Cryptomator vault (`cryptoFs`, network); G: = Google Drive (FAT32 virtual). Repo is on C:. `C:\mimicdata` does not exist yet (EP-3) |
> | Defender exclusion `C:\mimicdata` | `Get-MpPreference` → "Must be an administrator to view exclusions" | **unknown / pending owner** — owner runs elevated `Add-MpPreference -ExclusionPath 'C:\mimicdata'` (before or at EP-3) |
> | GOVERNANCE §1 dates (CITI, 3 DUAs, renewal) + claude.ai training toggle | owner-only | **pending owner input** — blanks left untouched; fill at EP-0 follow-up or EP-3/EP-7 |
> | `core.longpaths` (git, repo-local) | `git config --get core.longpaths` | set to `true` by EP-0 |
>
> Also recorded by EP-0: the `.claude/settings.json` deny rules refused a synthetic
> `probe.csv` placed **inside the repo** via Read, Bash `cat` and PowerShell `Get-Content`,
> but the same probe under `%TEMP%` (session scratchpad) was readable — `Read(**/*.csv)`
> patterns without a leading `//` are project-relative. `C:\mimicdata` is covered by the
> explicit `//C:/mimicdata/**` rules; `source material/` by path rules. No rule loosened.

**D-39 Enforcement of the Claude data policy = `CLAUDE.md` + safe-query wrapper +
repo-shared `.claude/settings.json` deny rules** (reading `source material/**` except
`*.md`, `C:\mimicdata\**`, `*.csv/*.parquet/*.duckdb`, the `duckdb` executable). A
PreToolUse output-scanning hook is parked. *Alternatives:* prose only; hook.

**D-40 Remote content = code + docs + gated aggregates** — results committed only after
`mwh disclose check` passes and a `.disclosure.json` sidecar is recorded.
*Alternatives:* code + docs only; two repos.

**D-41 MIT now; repo public at v1.0.0 after a full-history guard sweep.**
*Alternatives:* public from day one; private indefinitely.

---

## Defaults assumed by the planning session (owner may veto any; say so and the
## brief-writing session updates the affected briefs)

Stack & repo: `mwh` typer + rich CLI · pydantic-settings (`MWH_` env + `.env` + TOML) and
pydantic models for cohort/phenotype/protocol specs (JSON-schema → UI forms) · poethepoet
tasks · pytest + hypothesis + DuckDB data checks · ruff + pyright(basic) · pre-commit +
`mwh guard` · semver tags + CHANGELOG + separate warehouse `build_id` · `.env` + keyring
for any future tokens · `MWH_ALLOW_REMOTE=false` gate · single process + engine threads +
joblib for CV · `if __name__ == "__main__"` guards (Windows spawn) · dependency groups
`core / dev / ui / gpu / gpl / text` with `[tool.uv] conflicts` isolating `ui` (Streamlit
pins `pyarrow<25`) — commands in briefs always name their groups · commit `uv.lock` ·
env-export hash in every run manifest · `roadmap_check.py` · `mwh verify EP-n` · commit
pairs `feat(mimicwarehouse): … (EP-n)` + `docs(roadmap): record EP-n commit hash` · slug
scope tokens (`stage-`, `ui-`, `cohort-`, `surv-`, `ml-`, `text-`, `report-`, `link-`,
`replan-`, `capstone-`).

Data: Hive `subject_bucket = subject_id % 100` (dims unpartitioned), sorted
`(subject_id, time)`, ZSTD-3, ~1 M-row groups; two-pass bucketed load for the large
tables, resumable per bucket, `store_rejects`; DuckDB `memory_limit` 36–40 GB, `threads`
12, `temp_directory` under the data root, `max_temp_directory_size` explicit,
`preserve_insertion_order=false`; one pinned DuckDB version; ≥ 100 GB free during builds;
single-writer rule with build-to-`.new`-and-swap catalogs opened `READ_ONLY`; audit / run
ledger / benchmark ledger as append-only JSONL under `runs/` with `runs.duckdb` views;
schemas `mimiciv_hosp/icu/ed/derived`, `meta`, `marts`; notes in a separate lake +
`notes.duckdb` attached on demand; naive timestamps + `anchor_year_group` era + relative
times; `dod` censoring rule; ICD-9→10 dual code sets; snapshot id = hash(manifests);
loader accepts `.csv`/`.csv.gz` + column maps (demo 2.2 → 3.1); MEDS-shaped events spine
excluding raw chartevents.

Methods: statsmodels + scipy (cluster-robust SEs by `subject_id` by default); lifelines
(+ hand-rolled Aalen–Johansen and cause-specific Cox; Fine–Gray has no lifelines/scikit-survival
implementation → hand-rolled IPCW/Geskus weighting or R `cmprsk`, parked in final-roadmap;
scikit-survival (GPL-3, `gpl` group) only for survival-ML/IPCW metrics); PyMC + nutpie +
ArviZ (+ Bambi); statsforecast + statsmodels; scikit-learn + LightGBM (CPU) + XGBoost
(CUDA comparator after the GPU EP); SHAP tree/linear only; statsmodels MICE (inference) +
sklearn imputers (prediction); own `boot`, `assess`, `causal`, `disclose`, `run` modules;
medspaCy + regex baseline; local sentence-transformers (CPU-capable); unit-of-analysis
registry (subject / hadm / icustay / edstay / icu_day / hour_bin / person_time / note);
`docs/analyses/NN-slug.md` case studies with "What it deliberately does not claim" +
Reproduction blocks (hupsim precedent).

## Judgment calls made during planning (owner saw these at plan approval)

- The research panel ranked **marimo-as-app** first for a solo builder; the owner chose
  **Streamlit** knowingly (employer recognition, conventional multipage/wizard shape).
  Streamlit is the app, marimo is scratch; a "marimo app lane" is parked in
  `final-roadmap.md`.
- The panel recommends **staging notes early (cheap) but analysing late**; the owner chose
  to load notes late → notes staging stays in P10 (EP-148); the P7 re-plan may pull the
  staging brief forward if disk/time allow.
- The panel recommends **re-downloading `.csv.gz` and deleting plain CSVs**; the owner
  chose to keep CSVs → parked as an optional EP ("checksum-verifiable raw").
- Bucket scheme `subject_id % 100`, dev = buckets 0–4, fixture ids ≥ 90 000 000 — chosen
  over a hash for SQL simplicity and guard recognisability.
- `.gitattributes` and `.claude/settings.json` are written in the planning session (docs/
  config), not deferred to EP-0, because they must exist before any data code.
- Numbering = planned execution order (allocation order, hupsim); a per-phase optional
  "toolchain remediation" S slot may be allocated at re-plan for wheel/version fights.
- The extension roadmap file is named `roadmap/final-roadmap.md` (owner wrote "final
  roadmap.md"; hyphenated for shell-friendliness).
- CLI name `mwh`; data root default `C:\mimicdata`; environment prefix `MWH_`.

## Addenda

*(none yet — sessions append here or under the decision they refine)*
