# mimicwarehouse — DECISIONS

Architecture-decision log. Numbered decisions are **settled with the project owner**
(they/them) in the planning session of **2026-08-16** after ~60 clarifying questions
across stack, data architecture, methods, UI, governance and roadmap process, informed by
a research pass over the 2026 MIMIC-IV / Python / Windows tooling landscape. Later
sessions append addenda (`> **Addendum (date, EP-n).** …`) under the decision they refine
and add new numbered decisions at the end; nothing is rewritten.

Format: **D-n Title.** Decision. *Why.* *Alternatives considered.*

## Status index (maintained by hand at re-plan EPs; added at EP-33, 2026-09-01)

State: **settled** (no addenda) · **refined at EP-k** (addenda narrow or record how it
shipped, decision unchanged) · **superseded by D-m** (none yet). The last column is the
latest addendum's date and EP.

| D-n | Title (short) | State | Last addendum |
|---|---|---|---|
| D-1 | Tie-breaker = portfolio / employability | settled | — |
| D-2 | Horizon; EP sizes S/M/L | refined at EP-33 (split-at-pickup waived once, D-44) | — |
| D-3 | MIMIC-IV-Note = optional late track | settled | — |
| D-4 | ED enters via the Linkage Wizard | refined at EP-33 (demo ED fetched, not staged) | 2026-08-30, EP-33 |
| D-5 … D-14 | themes · signature depth · DL = tabular FM · ordering · brief depth · resource EPs · identity early · democratization · Python · native Windows | settled | — |
| D-15 | uv-managed CPython 3.13, one venv | refined at EP-7, EP-166 (wheel rule, psutil) | 2026-08-28, EP-166 |
| D-16 | CPU-first, GPU opt-in | settled | — |
| D-17 | DuckDB + Parquet lake canonical | refined at EP-9, EP-166, EP-169, EP-17, EP-33 (engine facts; NULLS LAST canonical) | 2026-09-01, EP-33 |
| D-18 | Tiers fixture / demo / dev / full | refined at EP-166, EP-33 (dev-first ordering) | 2026-08-30, EP-33 |
| D-19 | mimic-code vendored at a pin | refined at EP-8 (pin recorded) | 2026-08-17, EP-8 |
| D-20 | Custom `mwh build` runner | refined at EP-33 (⏱ job standard; lock identity; dry-run rule) | 2026-09-01, EP-33 |
| D-21 … D-23 | Streamlit app · marimo scratch only · Jinja2/Typst reporting | settled | — |
| D-24 | JSONL ledgers + `runs.duckdb` views | refined at EP-166, EP-33 (ledger practice; `fsio` canon) | 2026-09-01, EP-33 |
| D-25 | Protocol freeze by content hash | settled | — |
| D-26 | Raw provenance = local manifest | refined at EP-10, EP-166, EP-16 | 2026-08-28, EP-16 |
| D-27 | Synthetic fixture generator | refined at EP-9, EP-166, EP-16 (id floors, 0.2.0, regen protocol) | 2026-08-28, EP-16 |
| D-28 | ≤ 5 s latency via marts | settled | — |
| D-29 | Data placement (repo, data root, never G:/D:) | refined at EP-3, EP-7 | 2026-08-17, EP-7 |
| D-30 | Plain CSVs untouched | settled | — |
| D-31 | Sessions aggregate-only via safe-query | refined at EP-33 (shipped rule set; B1 robustness, taxonomy, extreme-value policy) | 2026-09-01, EP-33 |
| D-32 … D-34 | owner row view in-app · small cells n < 11 · MIT + `gpl` group | settled | — |
| D-35 | Vocabularies: free first | refined at EP-16 (register) | 2026-08-28, EP-16 |
| D-36, D-37 | future data via wizard · hupsim roadmap format | settled | — |
| D-38 | Owner-side Windows tuning | refined at EP-0, EP-6/7, EP-164, EP-165, EP-166 (two AV products, nine-path allow list) | 2026-08-28, EP-166 |
| D-39 | Enforcement chain for the Claude data policy | refined at EP-7, EP-165 (five layers) | 2026-08-28, EP-165 |
| D-40 | Remote content = code + docs + gated aggregates | refined at EP-166 (owner reading) | 2026-08-28, EP-166 |
| D-41 | MIT now; public at v1.0.0 | refined 2026-08-18 (public early, as governed WIP) | 2026-08-18, owner |
| D-42 | Endpoint security stays two products | refined at EP-165, EP-171, EP-33 (retry/publish canon) | 2026-09-01, EP-33 |
| D-43 | Retro consolidation of P0 + P1a | refined at EP-165, EP-166 (shipped), EP-33 (B9 repair) | 2026-08-31, EP-33 |
| D-44 | EP-33 = consolidation re-plan of P0–P2 | refined at EP-33 (executed over three attempts; checkpoint outcomes = D-45) | 2026-09-01, EP-33 |
| D-45 | EP-33 triage-checkpoint decisions | settled 2026-09-01 | — |

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

> **Addendum (2026-08-30, EP-33).** The P2 demo tier holds this line: EP-22 *fetched and
> verified* the ED demo files (7 files; licensing register `ext\demo\source.yaml`, both
> datasets `verified: true`) but stages and catalogs only the hosp + icu demo tables — ED
> demo staging waits for the P9 wizard (EP-142), keeping every tier's scope ≡ the core
> warehouse until then. Standing pattern: fetch-and-register may precede staging; staging
> waits for the capability's phase.

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

> **Addendum (2026-08-17, EP-7).** As shipped by EP-1 and re-verified at the P0 re-plan:
> **uv 0.12.5** (winget, user scope, `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`; cache
> `%LOCALAPPDATA%\uv\cache`), **uv-managed CPython 3.13.15** (`%APPDATA%\uv\python`;
> `mimicwarehouse/.python-version` = `3.13`; `.venv` in the workspace), system CPython 3.14.7
> untouched. `pyproject.toml` `[tool.uv]`: `package = true`, `python-preference = "only-managed"`,
> **`default-groups = ["dev"]`** — so `uv run mwh …` and `uv run --group dev mwh …` are the same
> environment and briefs may write either; `conflicts = [[ui, gpu], [ui, text]]` was accepted by
> uv 0.12.5 while `gpu`/`text` are still empty and appears in `uv.lock` `[[conflicts]]`. **Why `dev`
> stays co-installable with `ui`:** page tests must import pytest next to Streamlit
> (`--group dev --group ui`), so `dev` is not in the conflict set; `ui` is isolated only from the
> heavy groups (`gpu`: torch/xgboost, `text`: sentence-transformers) that may pull `pyarrow ≥ 25`
> against Streamlit 1.61.1's `pyarrow<25`. On the 2026-08-17 lock (100 packages) that fight did not
> happen — uv unified both resolver forks on **pyarrow 24.0.0** — so one venv serves core + dev + ui;
> the fork machinery is in place for the day they diverge (parked PY-2 = uv workspace split if `ui`
> ever needs `gpu`/`text` together). Exact pins: `duckdb==1.5.5` (one DuckDB version in every
> process, DESIGN §6; `test_ep01` asserts it) and `streamlit==1.61.*`; everything else floats in
> `uv.lock`. **Decision EP-1 asked EP-7 for:** the wheel-availability check
> (`test_ep01::test_uv_lock_every_package_has_a_wheel_for_this_interpreter`) **keeps its allow-list**
> — `autograd-gamma 0.5.0` (lifelines transitive, pure Python, no wheel ever published, builds in
> < 1 s with no compiler) is the only sdist in the lock; vendoring or replacing a transitive of a core
> dependency is not worth it. Rule for later briefs: a new dependency that is sdist-only may be
> allow-listed only if it is pure Python (say so in the completion note); a compiled sdist is refused
> — find a wheel-bearing alternative or park the item. Also recorded: pyright 1.1.411 downloads its
> Node runtime to the user cache on first `poe typecheck` (no system change); `uv python
> update-shell` was not run — everything goes through `uv run`. *Alternatives considered at EP-7:*
> vendor `autograd-gamma`; drop lifelines to an optional group (rejected — survival is core, P6).

> **Addendum (2026-08-28, EP-166 — D-43 item 14 / ledger FC-9).** `psutil` (pid liveness,
> RSS sampling for EP-19/35/36) joins the **core** dependencies at EP-19 — it ships abi3
> `win_amd64` wheels, so `test_ep01`'s wheel check stays green; EP-19 states the addition in
> its completion note per the rule above. No other new core dependency is planned for P2.

**D-16 CPU-first; GPU is an opt-in late track.** `gpu` dependency group installs torch
from `https://download.pytorch.org/whl/cu130` (`explicit=true`; PyPI torch is CPU-only on
Windows; cu126 lacks sm_120); XGBoost `device="cuda"` as comparator; LightGBM CPU is the
workhorse; PyMC + nutpie for Bayesian (JAX has no Windows CUDA). *Alternatives:* GPU from
the start; ignore GPU.

**D-17 DuckDB + Parquet lake canonical; layers raw CSV → typed Parquet → DuckDB
conformed catalog → derived concepts → marts; Polars primary, pandas at library
boundaries.** *Alternatives:* Postgres in Docker; Polars-only; ClickHouse; CSV → DuckDB
native only; pandas primary.

> **Addendum (2026-08-17, EP-9).** The typed layer's contract is the YAML under
> `src/mimicwarehouse/schema/tables/` (DESIGN §7 note), transcribed from the D-19 pin and guarded
> by `mwh schema check`. Two policy points settled while transcribing, both reversible with one
> YAML edit and both visible to the drift oracle through `upstream_type` / `upstream_nullable`:
> (1) **types follow upstream except where upstream has no DuckDB equivalent** — unbounded
> `NUMERIC` (ED vitals) is `DOUBLE`, `REAL` is DuckDB `FLOAT`, and the single `NUMERIC(10, 4)`
> column (`ed.vitalsign.resprate`) is `DOUBLE` so one table does not mix `Decimal` and float
> vitals; (2) **nullability follows upstream except where upstream's own DuckDB build relaxes it**
> (`microbiologyevents.spec_type_desc`, `prescriptions.drug`: zero-length strings load as NULL).
> Keys are metadata, not constraints: `Table.duckdb_ddl()` emits no `PRIMARY KEY` / `FOREIGN KEY`
> (EP-28/EP-44 test them per tier). *Alternatives:* `DECIMAL(18,3)` for bare NUMERIC (DuckDB's
> default; would constrain scale on load); DECIMAL(10,4) for resprate (faithful, awkward
> downstream); PK constraints in DDL (ART index over 400 M rows; rejects upstream duplicates).

> **Addendum (2026-08-18, owner — recorded by EP-166; ledger CMP-3).** Verdict on the EP-9
> completion note's deferred review points: **accepted as shipped** — `ed.vitalsign.resprate`
> as `DOUBLE`, the two `upstream_nullable` relaxations (`microbiologyevents.spec_type_desc`,
> `prescriptions.drug`), and the 13 docs-sourced ED/Note FKs (`source: docs`). No reverts;
> the contract hash the committed fixture pins stands.

> **Addendum (2026-08-28, EP-166 — D-43 item 9; code EP-169).** Contract refinements decided
> at the 2026-08-18 retro, landing in **one** hash bump at EP-169: sort-key **tie-breakers**
> adopted in one edit (`+itemid` / `+orderid` / `+emar_seq` / `+transfer_id` / `+pharmacy_id`
> / `+poe_seq` / `+microevent_id`, ED/Note uniform) so EP-18's per-bucket sort and
> determinism tests are well-defined under ties; `microbiologyevents` **stays `large`**
> (owner exception to the "> 1 GB" rule of thumb); a `Contract.structural_hash()` over the
> load-relevant facts becomes what the fixture manifest pins (the full `content_hash()`
> stays informational/provenance, so comment-only YAML edits stop forcing fixture
> regenerations); one CSV-dialect constant (`allow_quoted_nulls=true`, no
> `timestampformat`); `upstream_type: TIMESTAMP(3)` recorded on the nine columns. Details
> and evidence: ledger ARCH-4/SCH-2/FC-4.

> **Addendum (2026-08-29, EP-17 — ledger FC-5).** Identifier / free-text column flags exist
> from EP-17 on, in the shape the retro proposed: `Column.identifier` / `Column.free_text`
> booleans **stamped by the loader** from two new `keys.yaml` sections — `identifiers.names`
> (21 column names that are identifiers wherever they appear: the GOVERNANCE §3 real-band ids,
> the row/order linkage ids, the anonymised staff ids with every `*_provider_id` variant listed
> explicitly) and `free_text` (per-table map: `labevents.comments`,
> `microbiologyevents.comments`, `triage.chiefcomplaint`, the four Note text/field_value
> columns) — exactly the units.yaml → `unit_of` stamping pattern, so there is **one**
> representation, in the YAML, surfaced as `Table.identifier_columns()` /
> `free_text_columns()`. Typos refuse at load (a name matching no column, an unknown
> free-text table/column, a non-VARCHAR free-text column). The flags are **excluded** from
> `structural_hash()` (no fixture regeneration; `content_hash()` moved as designed);
> EP-23…EP-30 verify their per-table flags rather than inventing them; itemid-family codes
> (`itemid`, `spec_itemid`, `test_itemid`, `org_itemid`, `ab_itemid`) are deliberately *not*
> identifiers (dimension codes that appear in aggregates by design). *Alternatives:* a bare
> keys.yaml list without Column flags (two representations at query time); per-table YAML
> flags without a central list (21 names duplicated across 31 tables); name patterns like
> `*_provider_id` (pattern semantics on a governance list).

> **Addendum (2026-08-30, EP-33 — the P2 engine facts, recorded).** The engine rule as
> practised through P2: `duckdb==1.5.5` exact pin (`test_ep01` asserts installed == pin;
> DESIGN §6 note), the Python client as the **only** engine (no CLI, GOVERNANCE §4), and —
> because DuckDB's storage format moves between minor lines — catalogs record their build's
> DuckDB version and are **derived and disposable**: after a pin bump or data-root move the
> fix is `mwh build --select catalog` per tier, never file surgery (D-43 item 6, DESIGN §6
> note). Bumping the pin is a deliberate change: one line + re-lock + per-tier catalog
> rebuilds + a DESIGN §6 version note. Measured at EP-28 for the record: the typed-Parquet
> layer compresses the 97.19 GB raw CSVs to 7.05 GB (13.8×, ZSTD-3, sorted).

> **Addendum (2026-09-01, EP-33 — null placement under sort keys; ledger CTR-1).** The
> staged lake sorts with DuckDB's bare `ORDER BY` over the contract `sort_keys`, which is
> **NULLS LAST**; the Polars fixture writer and check sort nulls first. Decision: DuckDB's
> NULLS LAST is the canonical placement (the lake is never restaged for this); the fixture
> writer/check align at EP-41's 0.3.0 regeneration (amended there), and tests that assert
> sortedness on nullable keys compare `(col IS NULL, col)` pairs. Also recorded: every
> `duckdb.connect` in `src/` now goes through `mimicwarehouse.engine.open_duckdb(profile)`
> (`build` / `app`, a required positional), pinned by a grep guard; the engine gotchas
> (`10**9` binds DOUBLE, path-keyed instance cache → `ATTACH IF NOT EXISTS`) live in
> DESIGN §6.1 and `docs/gotchas.md`.

**D-18 Tiers fixture / demo / dev (5 %) / full.** Every EP passes tests on fixture+dev
and records a full-tier run with timing where meaningful; long full jobs run as
resumable background jobs verified by the next EP. *Alternatives:* sample only until late;
full only; full runs batched per phase.

> **Addendum (2026-08-28, EP-166 — D-43 items 7–8; code EP-167/EP-168).** Two retro
> refinements (owner, 2026-08-18). (1) **Per-tier lake roots:** `Settings.lake_root(tier)`
> — `lake/` for dev/full, `lake/demo`, `lake/fixture` (+ `lake/rejects`); layout keys
> 15 → 18; fixture/demo builds hard-refuse resolving to the credentialed lake;
> `catalog_path(tier)` stays `warehouse/<tier>.duckdb` for all four tiers so
> `mwh app`/`mwh sql --tier fixture` work (DESIGN §3/§4 notes; code EP-167).
> (2) **Test-tier readiness:** the pytest ladder keeps deselect-above-max semantics, but
> skips key on requestable readiness fixtures (`raw_root`, `dev_catalog`, `full_catalog`,
> `dev_ready(step)`, `item_tier`) via a `tier(name, needs=…)` kwarg rather than on one
> catalog file; demo tests are an **orthogonal opt-in** `@pytest.mark.demo` + `--with-demo`
> (`PYTEST_DEMO`), never a ladder step (DESIGN §20 note; code EP-168).

> **Addendum (2026-08-30, EP-33 — `dev-first` staging ordering, verdict: keep).** Every
> partitioned P2 stage shipped dev-first inside the step: pass 1 sweeps all buckets, pass 2
> sorts `settings.dev_buckets` first and flips `dev_ready: true` in `status.json` the
> moment they are sorted (EP-18), so the dev tier became queryable 31–66 s into each large
> table's build (labevents 66 s, emar 31 s, chartevents 50 s after step start) while the
> full pass continued — at zero measured cost. `dev_ready(step)` is the tier-readiness
> signal the EP-168 test fixtures consume; the dev snapshot id deliberately ignores buckets
> outside `dev_buckets` so it does not move when the full pass finishes (DESIGN §11).

**D-19 Adopt mimic-code `concepts_duckdb` (MIT), vendored at a pinned commit, tested,
fixes ported; re-derive only what is missing.** *Alternatives:* re-derive everything;
adopt as-is untested.

> **Addendum (2026-08-17, EP-8).** Pinned at **`8bcbd190ca75670cd5281f9ead3611ae1cefb73e`** (upstream `main`,
> committed 2026-08-10; `validate.sql` targets MIMIC-IV **3.1**; ED `validate.sql` v2.2). Vendored
> under `src/mimicwarehouse/concepts/vendor/mimic-code/` with `vendor/VENDOR.json` as the pin every
> run manifest cites (GOVERNANCE §12); attribution in the repo-root `NOTICE` (GOVERNANCE §10).
> 144 files (LICENSE, Postgres DDL/COPY/keys/indexes/row counts for hosp+icu, ED, Note; the DuckDB
> build script; 66 `concepts_duckdb` + 65 `concepts` SQL). Recorded, not fixed: `concepts_duckdb/`
> may lag `concepts/` (regeneration PR #2157), open concept-logic PRs (SIRS wbc, lab `valueuom`,
> Charlson, APS-III), README targets DuckDB 1.4.x LTS vs our 1.5.5, no ED/Note concepts upstream.
> Two documented local edits (`local_edits`): the row-count guard pragma in
> `mimic-iv/buildmimic/postgres/validate.sql`, and — decided during EP-8 — **in-place redaction**
> of two real-band `stay_id` values that upstream left in debugging comments of
> `mimic-iv/concepts/treatment/ventilation.sql` (`<mwh: id redacted>`; GOVERNANCE §3 — the
> "row count" pragma is never used to whitelist an identifier). Everything else is byte-identical
> to upstream (LF aside) and re-vendoring is `poe vendor-mimic-code --sha <sha>`.
> **Policy (owner-confirmed 2026-08-17, after review of the alternative "exclude the file"):**
> the row-count pragma is only ever applied to `validate.sql` files; a real-band token anywhere
> else in the vendored tree is redacted in place, never pragma'd — and a non-SQL file the guard
> would flag is refused outright. The vendoring script enforces this (`local_edit_for()`), so a
> future re-vendor that meets a new upstream debugging id handles it the same way and reports it
> under `local_edits` rather than failing or needing a per-file exclusion.

> **Addendum (2026-09-05, EP-37).** All 65 vendored `concepts_duckdb` files now execute
> through the DAG runner on every tier (fixture, demo, dev; full launched as the
> `concepts-full` job, EP-38 verifies) with **no local edit and no patch** — the runner
> refuses a vendored file whose bytes drifted from the committed inventory's `sql_sha256`,
> so "adopt as-is, tested" is enforced, and EP-38's patch mechanism is the only sanctioned
> deviation path. The inventory (`concepts/concepts.yaml`), the generated DAG spec and
> `docs/resources/concepts.md` are the three committed faces of the pin; the "failing on
> 1.5.x" list is empty, as the EP-33 D2 smoke predicted.

> **Addendum (2026-09-06, EP-38).** "Fixes ported" is now a mechanism, not an edit: a port
> is a **full-replacement** `concepts/patches/<concept>.sql` plus a registry entry in
> `concepts/patches/patches.yaml` that pins it to the vendored commit
> (`applies_to_upstream_commit`) and to its own bytes (`sql_sha256`) and cites the upstream
> PR / issue; the runner prefers a validated patch over the vendored file and **refuses every
> concept build** when the registry does not match the pin or a patch file drifted — a
> re-vendor therefore forces a review of each patch (DESIGN §8 note). Rules settled here:
> (1) a concept SQL is patched **only where an upstream fix exists** (EP-39's brief owns unit
> rules and plausibility bounds; the four lab panels the EP-38 brief listed without an
> upstream PR — `chemistry`, `blood_differential`, `enzyme`, `bg` — stay unpatched); (2) an
> unmerged upstream PR is ported as `ported-unmerged` and re-checked at each re-plan (EP-54,
> EP-74) — when it merges, the next re-vendor drops the patch; (3) `meta.concept_versions`
> and the manifest line record the sha256 of the SQL that ran (the patch's when patched)
> beside the `patch_id`, and the count-pins carry the patch map beside the upstream commit,
> so a pin is comparable only against the same commit **and** patch set; (4) the rebuild
> after a patch re-materialises the patched concepts and every concept that reads them.
> Ported at EP-38: SIRS `wbc_max` guard (PR 2146), MCHC / CRP `valueuom` filters (PR 2141),
> Charlson C4A exclusion (PR 2142), APS III equidistant arms (PR 2137); the MCHC filter is
> taken verbatim from upstream although it nulls a large minority of MCHC rows recorded
> with `%` — an owner-reviewed choice (EP-38 completion note).

**D-20 Custom lightweight transform runner (`mwh build`).** YAML DAG of SQL/Python
steps, tier-aware, manifests/snapshot ids, timings. dbt-duckdb and SQLMesh → final-roadmap.
*Why:* provenance capture and tier switching are the point; ~600 LOC we control.

> **Addendum (2026-08-30, EP-33 — the ⏱ background-job standard, recorded).** P2 settled
> how long full-tier steps run: `mwh build … --background --job <name>` launches a
> **detached supervisor** (`mimicwarehouse.dag.jobs`; `DETACHED_PROCESS` on Windows) that
> owns the child process, the log under `runs\jobs\<name>.log`, and the authoritative
> `{state, pid, exit_code, started, finished}` job file — a child-side rewrite alone cannot
> record a hard crash (EP-19). Sessions read jobs back with `mwh jobs [--job <name>
> --tail N]`, never a shell tail of a data-root log (the PreToolUse hook refuses those
> forms). All five P2 ⏱ staging jobs finished inside their launching sessions with exit 0;
> "verified by the next EP" (D-18) remained the record-keeping pattern, not a wall-time
> necessity. In-process heartbeat rss/ctypes probes read 0 for minutes on this host — the
> supervisor's psutil sampler is the trustworthy number (EP-23/EP-24 notes).

> **Addendum (2026-09-01, EP-33 — runner hardening from the P2 audit; ledger DAG-1/2/3,
> LDR-1/3/4, WIN-1).** (1) The build lock is created with `O_CREAT|O_EXCL` and records
> pid **and** process `create_time`; liveness is both, so a recycled pid reads as stale
> and `--break-lock` can clear it (job state files carry the same identity; pre-EP-33
> files fall back to pid-only). (2) `mwh build --background --dry-run` is refused — a dry
> run needs the foreground console. (3) **Owner decision (checkpoint):** a partitioned
> stage refuses a bucket request that is a strict subset of the table's recorded coverage
> (`StageCoverageError`); `mwh build --tier full --force` is the only path that rewrites a
> complete table — the runner's `--force` bypasses the completeness *skip*, never this
> guard (dev and full share one lake root; before this, `--tier dev --force` over a
> full-complete table silently discarded 95 partitions). (4) Pass 2 appends a bucket's
> manifest line **before** recording it sorted, and `_progress.json` records the source's
> identity (`source_sha256` or a name/size/mtime fingerprint) and the resolved `sort_by`,
> which a resume must match (old progress files without the fields resume as before).
> (5) `DIAGNOSTIC_COMMANDS` stays an explicit allow-list (the six commands that never touch
> the data root: `doctor`, `paths`, `guard`, `verify`, `schema`, `fixtures`), pinned by
> `test_ep167`; the EP-16 question of retiring it structurally is closed — the list *is*
> the rule's statement.

> **Addendum (2026-09-05, EP-37 — the concept runner as built; judgment calls for owner
> review).** (1) **Concept steps are `python` steps** with a `target` (their `status.json`
> key), per the EP-33 amendment; no `sql` handler was added (nothing to deduplicate).
> (2) **Per-tier completeness** — derived tables set `per_tier: true` (and `layer`) on
> their status entry and `complete_for_tier` reads dev completeness from the dev build
> alone; the shared `lake/` root otherwise makes a full-only build look dev-complete while
> no `derived/dev/` file exists (the first attempt's "runner skips every concept" hazard
> in another guise). (3) **Sources are views over the lake, not the tier catalog** — the
> concept steps recreate the catalog's own `read_parquet` relations on the build
> connection, so the catalog file is never read during a concept build (no cycle with
> the `catalog` step, no attach) and a test can run concepts against a temp lake.
> (4) **`meta.profile_*` are not re-exposed as catalog tables** by the discovery walker:
> EP-29 folds them into `meta.columns` / `meta.row_counts`, and their VARCHAR-cast
> min/max of measurement columns would sit in a registry-exempt (`meta.*`) surface
> without k-gating (D-31/D-33; SGT-2 admits extrema only inside k-gated rows). The first
> attempt had registered them; reversing that is deliberate and reviewable. (5) **Every
> `mwh build` is a provenance run** (`run.start(kind="build")`, ~6 s of doctor probes per
> process): builds are the runs GOVERNANCE §12 describes, and the concept lines /
> `meta.concept_versions` cite the run id the brief asked for; library callers opt in.
> (6) **`--force` under `--with-deps` forces the selected steps only** (ancestors skip when
> complete) — the coverage guard (LDR-1) would refuse a forced dev restage of a full table
> anyway. (7) `--keep-going` never blocks a `catalog` step: it registers what is complete.
> (8) The generated inventory carries `upstream_commit` once at the top level; each record
> inherits it on load. (9) `test_ep20`'s exact catalog-dependency pin became a superset
> check under the EP-168 churn rule (the stage-only spec keeps the exact set); no other
> earlier test was touched — but `dag.benchmarks.read` now infers the ledger schema from
> every line (a ledger opening with 65 null-`error` concept lines typed the column NULL
> and broke `test_ep19`'s crafted failure). (10) Demo count-pins are committed with cells
> below 11 as `"<11"` (the brief's rule; ODbL data, GOVERNANCE §3); dev pins stay under
> `runs/pins/`.

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

> **Addendum (2026-08-28, EP-166 — D-43 item 11).** The snapshot ids these stores cite are
> **logical** (stable across identical rebuilds), with the per-file Parquet sha256 kept for
> integrity only; the definition and the full identifier glossary (`raw_snapshot_id`,
> `source_sha256`, `build_id`, layer `snapshot_id`, catalog `build_id` + `core_snapshot_id`,
> `run_id`, `audit_id`, protocol hash) live in DESIGN §11's dated note and the D-26
> addendum below. `runs.duckdb` follows the §6 rename-aside swap protocol (D-43 item 6).

> **Addendum (2026-08-30, EP-33 — P2 ledger practice, recorded).** As shipped:
> `runs/benchmarks.jsonl` carries per-phase lines for `load_class: large` stages
> (`phase: pass1` with `bytes_in`, `phase: pass2` with `rows`, then `phase: total` —
> EP-23; what EP-26/EP-28/EP-32 read); `runs/audit.jsonl` receives one `O_APPEND` + fsync
> line per `safe_query` call (EP-30); `warehouse/runs.duckdb` is *views over the JSONL*,
> rebuilt on demand by `safe.build_runs_db()` (`mwh runs refresh`) and published by the §6
> rename-aside swap — app and CLI read the views, never the JSONL directly. The committed
> renderer for benchmark tables is `mwh runs benchmarks [--format table|md] [--out PATH]`
> (EP-32; `inventory.fmt_int` separation, raw ints only in `--json`).

> **Addendum (2026-09-01, EP-33 — one ledger canon; ledger LGR-1/2/4, DAG-4).** Every
> JSONL ledger (audit, benchmarks, the lake's build manifests) is written through
> `mimicwarehouse.fsio.append_jsonl` / `append_jsonl_lines` — one canonical line, a single
> `os.write` whose byte count is checked (a short write raises instead of leaving a torn
> line the next append would merge into), `O_APPEND|O_BINARY` (exact `\n`; the earlier
> writers let Windows write CRLF), fsync — and read through `fsio.read_jsonl` /
> `iter_jsonl`, which tolerate exactly one malformed **trailing** line (warn and skip) and
> treat any other malformed line as corruption. Concurrency is **single-writer by
> sequencing**, not OS locking: the benchmarks ledger has two sanctioned writers (the
> runner and EP-28's full-tier verify test, which appends `kind: verify` lines) that never
> run at once; `msvcrt` locking was considered and rejected (worst case is one torn line
> in telemetry). `runs.duckdb`'s audit view reads with `ignore_errors = true` and filters
> the all-NULL record DuckDB 1.5.5 emits for a torn line. Audit lines gained a second
> `allowed=false` class: `refusal_reason` starting `usage: ` for argument errors (see the
> D-31 addendum); EP-35's ledger views filter refusals vs usage deliberately.

> **Addendum (2026-09-05, EP-35 — the run ledger as built).** `mimicwarehouse.run` is the
> per-run JSON sidecar + ledger of this decision: `run.start(...)` writes
> `runs/<run_id>/manifest.json` (a `status: running` record at entry, the final record at
> exit; hashes, counts, parameters, paths, versions — never rows, never SQL text, which
> lives in `runs/<run_id>/sql/`) and appends one nine-field line to `runs/ledger.jsonl`
> through the `fsio` canon; `runs.duckdb` gains the `ledger` / `benchmarks` / `manifests`
> / `attrition` views beside `audit`, all rebuilt by `mwh runs refresh` and read through
> `mwh sql` under the aggregate-only rules (`runs` stays a non-registry schema, EP-33
> checkpoint). Three as-built choices recorded here: (1) the manifest embeds the doctor
> checks as `{id, status, value}` — the machine-readable payloads, not the prose — once per
> process (the probes cost ~6 s); (2) the tracer keeps its `runs/tracer/` report folder and
> cites its run id (`ledger_run_id`), so EP-31's numbers and the docs that point at that
> folder are unchanged; (3) `safe_query` detaches and re-attaches `runs.duckdb` on every
> call (`engine.detach` + `engine.attach_read_only`), the one exception to "attach once",
> so a refresh is never served stale from the path-keyed instance cache. Details in DESIGN §11's
> EP-35 note and `docs/methods/provenance.md`.

> **Addendum (2026-09-05, EP-36 — seeds and resources as recorded).** Every run manifest
> now fills the two slots EP-35 left optional. `seeds` = `{stage: seed}` under one
> derivation rule — `derive_seed(protocol_id, stage, salt)` = the first four bytes of
> `sha256("{protocol_id}|{stage}|{salt}")`, 32-bit — scoped to the frozen `protocol_id`
> (D-25) or, for unfrozen work, the `run_id`: a frozen protocol reproduces its numbers
> across runs, an unfrozen run reproduces from its own manifest. Library code takes a
> `numpy.random.Generator` and never seeds globals; `docs/methods/determinism.md` is the
> policy later briefs cite (stage names, `random_state=int(rng.integers(2**31))`, DuckDB
> `REPEATABLE`, spawned worker streams under `__main__` guards). `resources` = the
> `ResourceLog` measurement (wall, CPU time, run-scoped peak RSS with its method, RSS
> start/end, the process-lifetime `peak_wset`, data-root free-space delta, GPU memory only
> when `pynvml` and a device are present — `None` otherwise, D-16). Two as-built choices
> recorded: (1) `peak_rss_mb` is the sampled maximum, promoted to Windows' `peak_wset`
> only when that lifetime mark grew during the run — a probe showed the mark stays at an
> earlier peak after the memory is freed, so reading it alone (the brief's literal
> wording) would attribute a previous allocation to the run; (2) a seeded stage rewrites
> the manifest immediately, so a hard-killed run still shows what it seeded. `seeds: {}`
> means "no stochastic stage"; `null` marks a pre-EP-36 manifest. *Alternatives
> considered:* `peak_wset` alone (rejected by the probe); recording seeds only at exit
> (rejected: lost on a hard kill); a `salt` on `Run.seed` (rejected: two records for one
> stage name — distinct steps get distinct stage names instead); routing the EP-19
> runner's per-step sampler through `ResourceLog` now (deferred to EP-54: out of this
> brief's scope, and the runner is proven by five ⏱ jobs).

**D-25 Protocol freeze = YAML protocol → content hash → registry entry before run;
amendments logged; runs must cite a frozen hash.** *Alternatives:* git commit as freeze;
documentation only.

**D-26 Raw provenance = local manifest (SHA256/size/rows) + row-count reconciliation vs
mimic-code `validate.sql`.** Plain CSVs cannot be checked against PhysioNet's
`SHA256SUMS.txt` (covers `.csv.gz` only). *Alternatives:* re-download `.csv.gz` (parked);
skip.

> **Addendum (2026-08-17, EP-10).** Realised: `mimicwarehouse.inventory` + `mwh inventory build | show |
> reconcile`; manifest store `C:\mimicdata\lake\manifests\raw\{mimic-iv-3.1, mimic-iv-ed-2.2,
> mimic-iv-note-deidentified-free-text-clinical-notes-2.2}.jsonl` + `raw_snapshot.json`. First complete
> raw snapshot, computed 2026-08-18T03:50:04Z over the 41 plain CSVs (104,641,868,093 bytes,
> 902,815,672 rows): **`raw_snapshot_id = 8209301d8a06431081584e795684829b0bddeeedd49542ecf862cde712652d7a`**
> = `sha256(json(sorted (rel_path, bytes, sha256, rows)))`, with DuckDB 1.5.5, mimic-code `8bcbd190`,
> contract hash `e4cd5aa908d1…`. Reconciliation against the vendored `validate.sql` (MIMIC-IV **3.1**,
> ED 2.2): 34 match, 0 mismatch, 7 without an upstream expectation (`provider`, `caregiver`,
> `ingredientevents`, the four Note tables); every header equals the EP-9 contract. This id is the
> `source manifest id` every lake manifest (EP-17+) cites; the committed, human-readable form is
> `docs/resources/raw-inventory.md`. Each dataset's `SHA256SUMS.txt` archive hash is carried per file
> for the parked `.csv.gz` re-verification (RAW-1).

> **Addendum (2026-08-18, owner — recorded by EP-166; ledger CMP-3).** Verdict on the EP-10
> completion note's five deferred review points: **accepted as shipped** — the fourth
> reconcile status `pending`, `reconcile` exiting 1 on any mismatch, raw integers in
> `--json` output (human-readable surfaces stay thousands-separated), the docs page
> committed ahead of EP-16, and the snapshot id being issued with `rows=null` after a
> `--no-rowcount` pass. No reverts; EP-16 verifies, it does not re-litigate.

> **Addendum (2026-08-28, EP-166 — D-43 item 11; ledger ARCH-6/INV-3/FC-8).** The addendum
> above says "This id is the `source manifest id` every lake manifest (EP-17+) cites" —
> refined, not rewritten: every lake manifest line carries **two** fields, the per-file
> **`source_sha256`** (the sha256 this manifest recorded for the source CSV; `None` on the
> fixture tier) **plus** this **`raw_snapshot_id`** (the 41-file snapshot id; the demo
> tier cites the PhysioNet sha256 from `ext\demo\source.yaml` as `source_sha256`). Layer
> snapshot ids are **logical** — the EP-10 hash-of-sorted-tuples pattern over `(schema,
> table, path, rows, schema_hash, source_sha256/raw_snapshot_id, sort_keys,
> writer_version)` — with per-file Parquet sha256 kept for integrity only; the dev id
> hashes only `dev_buckets` paths + unpartitioned tables. Full glossary: DESIGN §11 note.

> **Addendum (2026-08-28, EP-16 — P1 re-plan verification).** EP-10's manifest re-verified
> with no data-root read: `mwh inventory show --timing` prints 41/41 files (0 pending,
> 0 header mismatch), job finished 2026-08-18T03:50:04Z with 0 errors, and the same
> `raw_snapshot_id 8209301d8a06…`; `mwh inventory reconcile` exits 0 with
> match=34 · mismatch=0 · no-expectation=7 · pending=0; a `--resume` no-op build reports
> "0 to process, 41 up to date" and leaves the snapshot job block untouched (the EP-167
> INV-1 fix, confirmed live). Wall time of the original run stays as recorded: ≈ 93 s
> across the two passes (hash 45.0 s + rowcount 47.3 s of engine time, 2.0–2.4 GB/s on
> the large files) against the 10–30 min planned. One committed-file consequence:
> `reconcile` now stamps the docs page's `Generated` line with the job's `finished`
> timestamp (deterministic since EP-167), so `docs/resources/raw-inventory.md` changed
> once from `03:50:27` (wall clock at first write) to `03:50:04`; future reconciles
> rewrite the page byte-identically.

**D-27 Fixtures = synthetic mini-MIMIC generator (ids ≥ 90 000 000) committed +
on-demand MIMIC-IV Demo 2.2 (+ ED Demo) tier.** *Alternatives:* demo only; synthetic only.

> **Addendum (2026-08-17, EP-9).** The planning assumption that the Demo 2.2 lacks the
> `provider` / `caregiver` tables and the `*_provider_id` / `caregiver_id` columns is **wrong**:
> mimic-code's DDL history shows all of them were added in the v2.2 commit (`db74e5d`,
> 2023-01-06), PhysioNet's public file listing for `mimic-iv-demo/2.2` includes
> `hosp/provider.csv.gz` and `icu/caregiver.csv.gz`, and the v3.0/v3.1 release notes list no
> column additions, renames or removals (only `admissions.language` widened in the DDL). The
> demo 2.2 → 3.1 column map (`schema/tables/column_maps/demo_2_2_to_3_1.yaml`) is therefore the
> **identity** for all 22 + 9 + 6 tables; MIMIC-IV-Note has no demo. Consequence for EP-22: the
> demo loader validates headers against the contract (`ColumnMap.check`, in code, never printed)
> and applies no NULL-filling or renames; if the real headers ever disagree, EP-22 amends the map
> file rather than the loader.

> **Addendum (2026-08-28, EP-166 — D-43 item 10; code EP-169, protocol prose this EP).**
> Fixture refinements decided at the 2026-08-18 retro, landing in **one** regeneration at
> EP-169: **disjoint id floors** per key space — `subject_id` from 90 000 000, `hadm_id`
> from 91 000 000, `stay_id` from 92 000 000, event/caregiver ids from 93 000 000 — so a
> wrong-key join can no longer match by accident (ledger FXT-1); that regeneration bumps
> `GENERATOR_VERSION` to **0.2.0**; the manifest additionally records numpy/polars/python
> versions and the contract's `structural_hash` (numpy/polars are deliberately **not**
> pinned — byte identity is asserted against the locked versions, ledger FXT-2); a
> `tests/fixtures/COVERAGE.md` names the vendored concepts that are empty or degraded on
> the fixture (ledger FXT-4). The **fixture-change protocol** — when to regenerate, how to
> review (manifest diff; CSVs are binary in `.gitattributes`), when to bump patch vs minor,
> one commit — is written once in `tests/README.md` § "Changing the synthetic fixture"
> (this EP) and supersedes the EP-11/12 hand-off phrasing "bump only when a hosp byte
> changes" (which was scoped to EP-12, ledger FXT-3).

> **Addendum (2026-08-28, EP-16 — P1 re-plan record).** The shipped fixture, as P2 codes
> against it: seed **2026**, 120 subjects (`subject_id % 100 < 5` keeps 10 in the dev
> buckets), **31 CSVs** (22 hosp + 9 icu) under `tests/fixtures/mimic-iv-3.1/{hosp,icu}/`
> in raw PhysioNet layout with contract column order, plus `tests/fixtures/manifest.json`
> (per-file sha256/bytes/rows; pins `contract_schema_hash`), `README.md` and
> `COVERAGE.md`; **50,974 rows, 5,370,674 bytes = 5.12 MiB** (≤ 10 MB budget);
> `GENERATOR_VERSION 0.2.0` (the one EP-169 regeneration — disjoint id floors 90/91/92/93/
> 93.9 M per the EP-166 addendum above — same totals as 0.1.0); rebuild is byte-identical
> via `uv run --group dev mwh fixtures build` (≈ 1.6 s). Change protocol:
> `tests/README.md` § "Changing the synthetic fixture".

**D-28 Latency ≤ 5 s typical on full data via marts; interactive pages default to
dev.** *Alternatives:* ≤ 2 s always; whatever DuckDB gives.

## Governance

**D-29 Data placement.** Repo + raw CSVs stay under `Documents` (local-only, BitLocker
on); derived data in a short data root outside the repo (`C:\mimicdata`, `MWH_DATA_ROOT`);
nothing on G:/D:. *Alternatives:* inside repo `data/`; inside `source material/`.

> **Addendum (2026-08-17, EP-3).** Enforced in code (`mimicwarehouse.config`): a data root
> is **refused** (`UnsafeLocationError`, exit 2, nothing created) when its volume is not
> `DRIVE_FIXED`, its filesystem is not NTFS/ReFS, its volume label matches
> `google drive|onedrive|dropbox|\bbox\b|cryptomator|icloud`, the path lies under
> `%OneDrive%`/`%OneDriveConsumer%`/`%OneDriveCommercial%`, or its drive letter is in
> `forbidden_drives` (default `["G","D"]`, configurable via `MWH_FORBIDDEN_DRIVES`); the
> DuckDB temp dir must share the data-root volume. The same test is warn-only for the
> repository tree (`mwh doctor` `cloud_mounts`). Every `mwh` command receives validated
> settings; only `doctor` and `paths` run against an unsafe root, to report it. Verified on
> this machine: D: (remote, cryptoFs, "Google Cryptomator") and G: (fixed, FAT32, "Google
> Drive") are each refused on three independent criteria; `Test-Path G:\mimicdata` stays
> false. Judgment calls: `box` is word-bounded (Toolbox ≠ Box); relative paths in `.env` /
> `mwh.toml` are anchored at the workspace root rather than the shell CWD; an empty
> `MWH_*` value means "default"; keyring/secrets storage parked (final-roadmap CFG-1).

> **Addendum (2026-08-17, EP-7).** Confirmed as shipped, nothing changed since EP-3. The
> drive-detection heuristics, in the order `location_problem` applies them: (1) `GetDriveTypeW`
> must return `DRIVE_FIXED` (remote / removable / CD / RAM disk / unknown are refused — this alone
> catches the Cryptomator vault, which mounts as a *network* drive); (2) `GetVolumeInformationW`
> filesystem must be NTFS or ReFS (catches Google Drive's FAT32 virtual volume; exFAT/FAT USB
> sticks too); (3) the volume label must not match `google drive|onedrive|dropbox|\bbox\b|
> cryptomator|icloud` (case-insensitive; `box` word-bounded); (4) the path must not lie under
> `%OneDrive%` / `%OneDriveConsumer%` / `%OneDriveCommercial%`; (5) the drive letter must not be in
> **`forbidden_drives`** (`Settings` field, default `["G","D"]`, override `MWH_FORBIDDEN_DRIVES=
> ["G","D","E"]` as JSON) — the letter rule is the belt for the day a sync client changes label or
> filesystem. `duckdb_temp_dir` must share the data-root volume (`check_same_volume`); `mwh paths
> --create` (now) and `mwh build` (EP-19) additionally require `min_free_gb` (100) free. The same
> probes are warn-only for the repository (`mwh doctor` `cloud_mounts`, `info` on this machine:
> "D: Google Cryptomator (cryptoFs, remote) · G: Google Drive (FAT32, fixed) …; repository on C:
> (fixed)"). Off Windows every probe returns `unknown` and only rules (4)–(5) apply. EP-7 doctor run:
> `data_root` pass (`C:\mimicdata`, fixed NTFS, writable), `temp_dir` pass, 414.9 / 951.5 GB free.
> New since EP-3: keeping the local copy "secured" (GOVERNANCE §1) now also means the data root and
> `source material\` are excluded from **both** real-time products (D-38 addenda) so no scanner
> ever uploads a detected object from either location.

**D-30 Keep plain CSVs untouched** (~180 GB total footprint). *Alternatives:* re-gzip;
delete after verified Parquet.

**D-31 Claude sessions: aggregate-only via a safe-query wrapper** (k = 11 suppression,
no identifiers, no note text, audit-logged) + `CLAUDE.md`. *Alternatives:* schema-only;
same access as the owner.

> **Addendum (2026-08-30, EP-33 — the shipped rule set, recorded).** EP-30's
> implementation (DESIGN §12 note carries the full pipeline) is stricter than this
> decision's floor and is the operating contract: one-SELECT parse via
> `json_serialize_sql` (statement bound, never executed), schema + function allow-lists,
> aggregate-only outer select from a closed set with no value-collecting members, ≥ 1
> count-family column unless the statement reads only `meta.*` / dims /
> `information_schema` (EP-170 amendment), identifier columns only inside count-family
> aggregates, row-wise k = 11 drop-suppression via the `safe.SUPPRESSOR` hook **until
> EP-43's complementary suppression replaces it**, row cap, one audit line per call.
> Interim history, for the record: EP-13 … EP-29 sessions ran no statements against real
> data at all (the wrapper did not exist; D-39 chain); from EP-30 on `mwh sql` is the only
> session query path, first exercised end-to-end by the EP-31 tracer — whose
> `count(*) FILTER (WHERE flag = 1)` pattern is the sanctioned way to count flagged rows
> (`sum()` returns HUGEINT and the cast a HUGEINT needs trips the closed-set walk; the
> EP-33 worklist item B1 revisits that cast case).

> **Addendum (2026-09-01, EP-33 — B1 robustness and the checkpoint's governance
> calls; ledger DKB-1/SGT-1, DKB-2, SGT-2, SGT-3, P3C-4/5).** The gate only tightened:
> (1) the mandatory count-family column must be a **real** count-family node (`count`,
> `count_star`, `approx_count_distinct`, possibly cast-wrapped) — an alias matching the
> count pattern never satisfies it, and a count-named alias on a non-count expression
> (`avg(x) AS n`) is **refused**, so the row-wise suppressor only ever tests true group
> sizes (the pre-EP-33 alias rule let a below-k group through whenever the aliased
> value fell outside 1…k−1); (2) a `CAST` directly around one closed-set aggregate now
> verifies (the `sum()`/HUGEINT case), arithmetic over aggregates stays refused
> (final-roadmap DIS-3); (3) set operations UNION / UNION ALL / EXCEPT / INTERSECT verify
> with the full select-list checks per leaf, matching widths and count-family positions
> across branches, suppression over the combined frame (`UNION BY NAME` refused;
> DIS-2 closed); (4) the registry exemptions are named — `REGISTRY_SCHEMAS` (`meta`,
> `information_schema`), `REGISTRY_TABLES` (`marts.cohorts`, pre-registered for EP-47)
> and the contract dims via `is_registry_ref`; **`runs` deliberately does not join** (owner,
> checkpoint) — EP-35 allow-lists its long label columns instead; every non-registry read
> outside the EP-9 contract (P3's derived/marts surfaces) is treated as subject-keyed for
> the 64-char free-text result check; (5) execution errors are **sanitized** before they
> reach the refusal message or the audit line (first line, quoted literals → `'...'`,
> digit runs → `#`, 120 chars) — DuckDB quotes offending cell values in conversion errors;
> (6) the three-way taxonomy: refusal = exit 3 (`SafeQueryRefused`, audited), usage =
> exit 2 (`SafeQueryError` for `k < 1`, `row_cap < 1`, unknown tier — audited with a
> `usage: ` reason), environment = exit 2 (`CatalogOpenError`, unaudited), shared by
> `mwh sql`, `mwh tracer` and every future caller via `catalog.cli.safe_cli_errors`;
> `tier`/`k` default from `settings.default_tier` / `settings.k_suppression`.
> **Owner decision (checkpoint, SGT-2):** extreme-value aggregates (min/max/mode/median/
> quantile) over subject-keyed columns stay admitted — with (1) every row that carries
> one is gated by a real count column, so such values are released only inside
> k-suppressed rows (dates are patient-shifted); EP-43 decides any per-column tightening
> in the `disclose` module (amended there). *Alternatives:* refusing extreme-value
> aggregates on subject-keyed reads now (rejected: the tracer's descriptives and P3's QC
> need them; the k-row gate is the standard release condition), or a separate brief
> (rejected: nothing to build until EP-43's module exists).

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

> **Addendum (2026-08-28, EP-16 — what EP-14 confirmed; register:
> `docs/resources/vocabularies.md`).** Every **use**-verdict vocabulary a v1 brief needs
> has a confirmed free path: ICD-9-CM (frozen v32), ICD-10-CM/PCS, CMS GEMs (2018,
> final), AHRQ CCSR + Elixhauser CSR (both v2026.1), NDC Directory, HCPCS Level II and
> MS-DRG are US public domain (or public-with-citation); the free drug path is **RxNorm
> Current Prescribable Content** (public domain, no login); LOINC is free-registration
> but non-redistributable (only the `source.yaml` hash record may be committed). Owner
> action remains for the **later**-verdict rows only: a UTS/UMLS account unlocks RxNorm
> full, SNOMED CT and the RxNorm→ATC relationship path (parked, v2 PHE-3/TXT-1); ATC bulk
> index files are a WHO CC purchase (owner decision, parked); OMOP Athena needs a free
> account (parked, v2 OMOP-1). The only *partial* coverage is EP-143's preferred ATC
> ingestion target — it picks its free fallback (Elixhauser or the vendored LOINC
> `concept_map`) at execution, as its brief already provides.

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

> **Addendum (2026-08-17, EP-0 follow-up).** Owner answers, same day: (1) **Defender
> exclusion done** — owner created `C:\mimicdata` (empty; EP-3 lays out the tree) and ran
> `Add-MpPreference -ExclusionPath 'C:\mimicdata'` in an elevated PowerShell (not readable
> non-elevated; taken on the owner's word). (2) **Power mode switched** to Best performance —
> re-probe: `ActiveOverlayAcPowerScheme = ded574b5-45a0-4f42-8737-46345c09c238` (scheme
> stays Balanced `381b4222…`; the Win11 overlay is what matters). (3) **claude.ai training
> toggle confirmed off** (GOVERNANCE §4 item 6). (4) **GOVERNANCE §1 filled**: CITI
> 2024-01-29 · MIMIC-IV 3.1 DUA 2026-08-15 · MIMIC-IV-ED 2.2 DUA 2024-02-02 · MIMIC-IV-Note 2.2
> DUA 2024-02-02 · CITI renewal due 2027-01-29. All D-38 items are now done; EP-3 re-checks
> `LongPathsEnabled`, BitLocker and free disk in `mwh doctor`.

> **Addendum (2026-08-17, EP-6 → for EP-7).** D-38 assumed Defender was the only real-time
> protection on the host. It is not: **Malwarebytes 5.1 Premium** (installed since 2026-04,
> real-time stack reconfigured 2026-08-15 23:04, licence refreshed 2026-08-16) is registered in
> `root/SecurityCenter2` next to Defender and keeps its **own** allow list. Found because its
> Ransomware Protection module (ARW) killed and quarantined the unsigned MSYS2
> `C:\Program Files\Git\usr\bin\bash.exe` at 19:28:43–51 during EP-6's scratchpad check
> (`cp -r roadmap` + `sed -i` + `rm -rf`, 166 files in seconds = its ransomware heuristic;
> `mbamservice.log`: "WinVerifyTrust failed … NOT whitelisted … kill this process …
> Quarantining"; quarantine id `64336cee-9a93-11f1-b147-38186875c8ac`; the executable was also
> uploaded to Malwarebytes' cloud). Defender was not involved that day; its own record is three
> `Trojan:Win32/ClickFix.FFQ!MTB` hits on 2026-08-16 15:34–15:35 against Claude Code heredoc
> command lines (`bash -c … cat > roadmap/EP-1… <<'EOF'`; action Remove = process killed,
> file untouched — the reason sessions write files with the Write/Edit tools, not heredocs).
> **Owner actions (2026-08-17):** bash.exe restored from quarantine and allow-listed; Malwarebytes
> folder exclusions (malware/ransomware/PUP) for `C:\Program Files\Git`,
> `%APPDATA%\uv\python`, `mimicwarehouse\.venv`, `C:\mimicdata`, `source material\`; Ransomware
> Protection left **on**. **D-38 is amended:** the owner-side list now reads "Defender exclusion
> for `C:\mimicdata` **and Malwarebytes allow-list entries for the toolchain (Git, uv, uv's
> CPython, the venv, pre-commit's hook venvs) and both data locations**"; the two remaining
> entries — `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*` (unsigned `uv.exe`) and
> `%USERPROFILE%\.cache\pre-commit` — were added by the owner the same evening (seven paths in
> total). Governance reading (GOVERNANCE §2/§4): the data-root and source-material entries are a
> **disclosure control** — a product that ships detected objects to a vendor cloud must never
> have reason to look at them; the owner confirmed Malwarebytes › Settings › usage/threat
> statistics and sample submission are **off** (2026-08-17). Neither product's exclusion list is
> readable non-elevated, so — like the Defender item — these are taken on the owner's word and
> `mwh doctor` cannot verify them. Impact on EP-0…EP-6: none (the incident post-dates every
> acceptance run; `roadmap_check` and all 194 tests are green after the restore). **Owner
> decision (2026-08-17): allocate** the P0 toolchain-remediation slot — EP-7 writes
> `EP-164-toolchain-remediation-p1.md` (S) adding a `mwh doctor` `antivirus` check
> (`root/SecurityCenter2` products; warn when a non-Defender real-time product is present;
> states that exclusions are unreadable non-elevated). Roadmap Risk 12 mirrors this note.

> **Addendum (2026-08-17, EP-7 — owner tuning status as finally recorded for P0).**
>
> | Item | Status at the P0 re-plan | Verified by |
> |---|---|---|
> | `LongPathsEnabled` = 1 + repo `core.longpaths=true` | done | `mwh doctor` `longpaths` pass |
> | BitLocker on C: | on | `mwh doctor` `bitlocker` pass |
> | Power mode "Best performance" (AC overlay `ded574b5…`) | done | `mwh doctor` `power_scheme` info: "Balanced · AC power mode: Best performance" |
> | Defender real-time exclusion `C:\mimicdata` | done (owner, EP-0 follow-up) | owner's word — not readable non-elevated (`defender` info) |
> | **Malwarebytes 5.1 Premium** allow list — seven paths: `C:\Program Files\Git`, `%APPDATA%\uv\python`, workspace `.venv`, `C:\mimicdata`, `source material\`, `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*`, `%USERPROFILE%\.cache\pre-commit`; `bash.exe` restored + allow-listed; Ransomware Protection **on**; usage/threat statistics + sample submission **off** | done (owner, 2026-08-17 evening) | owner's word — not readable non-elevated; product *presence* checked by `mwh doctor` `antivirus` from **EP-164** |
> | GOVERNANCE §1 dates (CITI 2024-01-29 · MIMIC-IV 3.1 DUA 2026-08-15 · ED 2.2 DUA 2024-02-02 · Note 2.2 DUA 2024-02-02 · renewal 2027-01-29) | filled | GOVERNANCE.md §1 (EP-0 follow-up) |
> | claude.ai "improve the model" / training toggle | off (owner, 2026-08-17) | owner's word (GOVERNANCE §4 item 6) |
> | ≥ 100 GB free on C: | 414.9 / 951.5 GB free | `mwh doctor` `disk_free` pass (EP-7 run) |
>
> D-38 therefore reads, finally: *owner-side Windows tuning = LongPathsEnabled + reboot, Best
> performance power mode, Defender exclusion for `C:\mimicdata`, **and** Malwarebytes exclusions
> for the toolchain (Git, uv, uv's CPython, the venv, pre-commit's hook venvs) and both data
> locations, with both products' telemetry/sample submission off.* `mwh doctor` verifies the first
> two, reports the exclusions on the owner's word, and (EP-164) names every real-time product it
> can see. Doctor summary at EP-7 (2026-08-17): exit 0, **8 pass · 0 warn · 0 fail · 5 info** —
> identical statuses to the EP-3 run.

> **Addendum (2026-08-17, EP-164).** Product presence is now checked (names and Security Center
> states only, never either exclusion list) by `mwh doctor` `antivirus` from EP-164: on this host
> it lists Malwarebytes (`productState 0x060000` — "real-time off" *per Security Center*, i.e. not
> the registered WSC antivirus, so Defender stays active; its own modules run regardless, D-42) next
> to Windows Defender (`0x061100`, on) and **warns**, naming the seven D-38 paths that must be
> excluded in Malwarebytes too — the allow list itself stays on the owner's word (elevated
> verification parked, `final-roadmap.md` DOC-1). Doctor summary at EP-164: exit 0, **8 pass ·
> 1 warn · 0 fail · 5 info** (the one warn is this row, by design).

> **Addendum (2026-08-18, owner — recorded by EP-166; ledger CMP-3).** Verdict on the
> EP-164 completion note's deferred review points: **accepted as shipped** — the
> presence-based `antivirus` warn rule (warn whenever a non-Defender product is listed,
> rather than keying on the Security Center real-time bit) and the taken optional item 6
> (EP-0 hash-pin relaxation that made `roadmap-check --strict` green). The permanent warn
> on this host is by design; an acknowledged-state doctor option remains a candidate for a
> later re-plan (ledger CMP-4).

> **Addendum (2026-08-28, EP-165).** Owner decisions of 2026-08-18 (D-43 item 5): the
> Malwarebytes allow list grows seven → **nine** paths — adding `%LOCALAPPDATA%\uv\cache`
> and the Claude scratchpad `%LOCALAPPDATA%\Temp\claude\` — and the owner **restarts VS
> Code** after EP-165 lands (the hosting process predates the uv install, which is why `uv`
> is missing from the tool shells' PATH; the `export PATH` fallback stays documented in
> CLAUDE.md §3; the restart also loads the newly registered PreToolUse hook, which Claude
> Code snapshots at session start). Both remain owner actions taken on the owner's word;
> `mwh doctor` `antivirus` keeps naming paths without reading either exclusion list.
> GOVERNANCE §2 now records the two-product reality as a dated amendment (was: Defender
> only, "owner's discretion").

> **Addendum (2026-08-28, owner — recorded by EP-166, post-session).** Both D-43 item 5
> owner actions are **done**: (1) VS Code restarted — `uv` now resolves natively in both
> tool shells (`%LOCALAPPDATA%\Microsoft\WinGet\Links`, verified in-session 2026-08-28;
> ENV-1's stale-process diagnosis confirmed; the CLAUDE.md §3 PATH prefix stays documented
> as the fallback only); (2) the **nine-path Malwarebytes allow list confirmed in place**,
> the two 2026-08-18 additions (`%LOCALAPPDATA%\uv\cache`, `%LOCALAPPDATA%\Temp\claude\`)
> included — this is also the re-confirmation ledger CMP-4 asked for ahead of EP-17+
> full-tier writes (previous confirmation 2026-08-17). Still on the owner's word, as
> designed; the doctor `antivirus` row is unchanged.

**D-39 Enforcement of the Claude data policy = `CLAUDE.md` + safe-query wrapper +
repo-shared `.claude/settings.json` deny rules** (reading `source material/**` except
`*.md`, `C:\mimicdata\**`, `*.csv/*.parquet/*.duckdb`, the `duckdb` executable). A
PreToolUse output-scanning hook is parked. *Alternatives:* prose only; hook.

> **Addendum (2026-08-17, EP-7).** The git-side layer of D-39 shipped in EP-4 as `mwh guard`
> (`guard.py`), rules **G1–G5 as shipped**: **G1** data-shaped extension anywhere (`.csv .csv.gz
> .parquet .duckdb .duckdb.wal .duckdb.new .duckdb.tmp .wal .jsonl .feather .arrow .pkl .joblib
> .skops .pt .safetensors .npy .npz .h5`) except under `mimicwarehouse/tests/fixtures/`, where only
> `.csv .csv.gz .parquet .jsonl .json .yaml` pass; **G2** anything under `source material/` other
> than `*.md` — refused by name, the file is never opened; **G3** `.ipynb` with outputs /
> `execution_count` (or invalid JSON) and any path with a `__marimo__` segment; **G4** in text files
> (`.py .md .yaml .yml .json .toml .sql .txt .csv .jsonl .html .svg .cff .ps1 .ini .cfg` or no
> extension, UTF-8, no NUL) an isolated 8-digit token starting 1/2/3 whose value lies in the
> `subject_id` / `hadm_id` / `stay_id` bands — exempt only when the same line carries the pragma
> **`mwh-guard: allow`** (documented examples); compact `YYYYMMDD` dates are *not* exempt (write
> ISO dates with hyphens); the message masks the token (`1*******`), names the band, never quotes
> the line, and caps at 25 rows per file; **G5** any blob > 20 000 KiB. Modes: `mwh guard [PATHS…]`
> (working tree), `--staged` (index blobs — what the commit records; the hook's mode), `--all-tracked`
> (`git ls-files` / `git ls-tree -r <rev>`, the EP-163 sweep primitive), `--selfcheck` (EP-0
> `.gitignore` / `.gitattributes` probes, `.pre-commit-config.yaml` carries `mwh-guard`, hook
> installed); exit 0 / 1 / 2; `guard` is a `DIAGNOSTIC_COMMANDS` member so a mis-set
> `MWH_DATA_ROOT` never blocks a commit. Hook order (repo-root `.pre-commit-config.yaml`):
> `mwh-guard` → `ruff-check` → `ruff-format --check` → `pre-commit-hooks v6.0.0`
> (`check-added-large-files --maxkb=20000`, merge-conflict, yaml, toml, json, end-of-file-fixer,
> trailing-whitespace `--markdown-linebreak-ext=md`, detect-private-key). Verified at EP-4 with a
> refused real commit attempt; at EP-7 `mwh guard --selfcheck` = 16 rows ok (14 probes +
> `pre-commit-config` + `hook-installed`) and every P0 commit was hook-guarded. Session-side layers unchanged: `CLAUDE.md`, the `.claude/settings.json` deny rules
> (EP-0 finding: `Read(**/*.csv)` is project-relative — the explicit `//C:/mimicdata/**` and
> `source material/…` rules are what protect the real data, so the guard and EP-30 `safe_query`
> remain necessary layers), and `safe_query` (EP-30). The PreToolUse output-scanning hook stays
> parked (final-roadmap GOV-1); secret scanning parked (GOV-3).

> **Addendum (2026-08-28, EP-165 — owner-authorised, D-43 items 2–3).** `.claude/settings.json`
> gained four parts. (1) `"env": {"PYTHONUTF8": "1"}` — an environment default, not a permission
> change — so both tool shells run Python with UTF-8 stdio (Risk 13; `doctor._run` hardened with
> `errors="replace"` because UTF-8 mode also flips `subprocess` text decoding and its
> PowerShell/powercfg children can emit OEM bytes). (2) Literal deny rules for the two-step leak
> paths the 2026-08-18 review verified (GOV-1): `cp`/`mv` on `mimicdata`/`source material`,
> `python*` and `uv run *python*` mentioning either location (the `uv run *python*` form covers
> `uv run --project … python -c`, per the ledger's corrected fix), nested `sh -c`/`bash -c`,
> `perl`/`node -e`, `od`, `nl *.csv`, `diff *.csv`, PowerShell `Copy-Item`, `.NET
> ReadAllText/Lines/Bytes`, `Get-ChildItem *mimicdata*`, `gc|cat|type *.csv`, and absolute
> `Read(//C:/**/*.csv|*.csv.gz|*.parquet|*.duckdb)` so non-repo copies (e.g. the scratchpad) are
> no longer Read-able. (3) An **allow** list for read-only project commands (`uv run
> mwh|pytest|poe|ruff|pyright`, `uv run --project mimicwarehouse * mwh`, `git
> log|status|diff|show|ls-files`, `ls`, + PowerShell twins) — convenience only; deny rules keep
> precedence. (4) Deny rules for connector send/write tools (Gmail send/reply/forward/
> create_draft/update_draft/trash_message/trash_thread, Calendar create/update/delete/
> respond_to_event, Hugging Face `hf_fs`/`dynamic_space`) — the reference-lookup-only connector
> policy is GOVERNANCE §4's new paragraph. And (5) the parked hook is **unparked, corrected**: what
> shipped is a *pre-execution command-string filter* (`mimicwarehouse/scripts/
> claude_pretool_guard.py`, matcher `Bash|PowerShell|Read|Grep|Glob`, run by the allow-listed
> workspace-venv `python.exe` reading the hook JSON from stdin; deny on `mimicdata` /
> `source material` / `.csv` / `.parquet` / `.duckdb` outside allow-listed launchers; Grep
> checked by *path* only so doc searches for the tokens stay legal; decision log — never data —
> to `%LOCALAPPDATA%\Temp\claude\mwh-pretool.log`; fail-open on internal error; ~60 ms/call).
> A PostToolUse *output-scanning* hook cannot prevent transmission and stays parked under
> GOV-1's struck row. `mwh guard --selfcheck` gained the `pretool-hook` registration row; the
> hook command is path-bound to this clone (like `.git/hooks/pre-commit`) — re-register after
> moving the repo. G1 gained 19 data-shaped extensions and G4 the float-rendered (`NNNNNNNN.0`)
> and path-token forms in the same EP (details: EP-165 brief + completion note).

**D-40 Remote content = code + docs + gated aggregates** — results committed only after
`mwh disclose check` passes and a `.disclosure.json` sidecar is recorded.
*Alternatives:* code + docs only; two repos.

> **Addendum (2026-08-28, owner — recorded by EP-166; ledger DOC-9).** Confirmed reading:
> GOVERNANCE §3's allowance for **manifests that contain only hashes/counts/schema** stands
> apart from the sidecar-gated aggregates — such manifests (EP-10's
> `docs/resources/raw-inventory.md`, future lake-manifest summaries) are committable
> **without** a `.disclosure.json` sidecar, and before EP-43 ships `mwh disclose check`,
> nothing else derived from real data enters `docs/` or git. CLAUDE.md §5's blanket
> sentence gains the one-line manifest exception with EP-167's commit (pickup note on that
> brief); no retro-fit sidecar for `raw-inventory.md`.

**D-41 MIT now; repo public at v1.0.0 after a full-history guard sweep.**
*Alternatives:* public from day one; private indefinitely.

> **Addendum (2026-08-18, owner decision between EP-12 and EP-165).** The repository goes
> **public before v1.0.0**, as a governed work-in-progress (portfolio / job-search purpose,
> D-1), because development is paused for a while after P1a. The "private until v1.0.0"
> half of D-41 is superseded; the **"after a full-history guard sweep" half is kept and was
> executed the same day**, before the flip: (a) `mwh guard --all-tracked` clean (428 tracked
> files) and `--selfcheck` all ok; (b) `guard.scan_tracked` run at **every commit reachable
> from every ref** (37 commits, `cd67743` … `f3eb115`) — 0 violations (data-shaped files,
> real-band ids, source-material paths, notebook outputs, oversize); (c) a secrets/PII regex
> sweep over every unique non-CSV/SVG/lock blob in history (518 blobs; cloud/API/VCS tokens,
> private keys, `password=`-style assignments, e-mail addresses, PhysioNet credentials) — 0
> hits apart from `PHI-report@physionet.org`, test dummies and `C:\Users\<owner>` paths;
> (d) no path ever added-then-deleted in history; (e) `origin/main` held only the planning
> commit `cd67743`, so the push is a fast-forward (no rewrite, no force). Owner-side actions
> at the flip: repo-root `LICENSE` (MIT) added now rather than at EP-163; root and workspace
> READMEs carry a dated work-in-progress status block; GOVERNANCE §3 amended in place with a
> dated note (history kept). Consequences for EP-163: its release checklist item (d)
> becomes a **re-sweep** with `mwh guard --history` (still to be built) rather than the
> first sweep, and item (h) "remote flipped to public" is already done. Standing rule from
> here on: the remote is public, so every commit is publication — the pre-commit guard, the
> Claude deny rules and the disclosure gate (D-40) are the only things between a session and
> the world; nothing may be pushed that would not pass them.

---

## Defaults assumed by the planning session (owner may veto any; say so and the
## brief-writing session updates the affected briefs)

Stack & repo: `mwh` typer + rich CLI · pydantic-settings (`MWH_` env + `.env` + TOML) and
pydantic models for cohort/phenotype/protocol specs (JSON-schema → UI forms) · poethepoet
tasks · pytest + hypothesis + DuckDB data checks · ruff + pyright(basic) · pre-commit +
`mwh guard` · semver tags + CHANGELOG + separate warehouse `build_id` · `.env` + keyring
for any future tokens (keyring parked → final-roadmap CFG-1; D-29 EP-3 addendum) ·
`MWH_ALLOW_REMOTE=false` gate · single process + engine threads +
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

*(sessions append new numbered decisions here, and refinements under the decision they refine)*

**D-42 Endpoint security stays two products, both on; sessions adapt their I/O pattern
(2026-08-17, EP-6 → recorded at EP-7).** The owner keeps Windows Defender *and* Malwarebytes 5.1
Premium real-time protection — including Malwarebytes' Ransomware Protection — enabled on the
host, and instead allow-lists the toolchain and both data locations in each product (D-38 addenda:
seven Malwarebytes paths, Defender exclusion for `C:\mimicdata`; telemetry / sample submission off
in Malwarebytes). Consequences for every Claude session and every brief: (1) sessions write files
with the Write/Edit tools, **never** shell heredocs (Defender killed three `bash -c … <<'EOF'`
command lines as `Trojan:Win32/ClickFix.FFQ!MTB` on 2026-08-16); (2) no burst copy / `sed -i` /
delete loops over hundreds of files in scratch directories from an unsigned process (that is the
ransomware heuristic that quarantined `bash.exe`); (3) long-running writers — the EP-17+ loader,
the EP-11/12 fixture generators, EP-10's hashing pass — run under the allow-listed managed
`python.exe` from the allow-listed `.venv`, log progress, and are resumable, so a killed process
costs a restart, not a corrupt lake; (4) a "process killed / binary vanished / access denied
mid-command" symptom is checked against Malwarebytes › Detection History › Quarantine and
`C:\ProgramData\Malwarebytes\MBAMService\logs\mbamservice.log` **before** Defender; (5) `mwh
doctor` names the products it can see (EP-164 `antivirus`) but takes both exclusion lists on the
owner's word. *Why:* the owner's endpoint policy is not the project's to change; the data-location
exclusions are also a GOVERNANCE §2 disclosure control (a scanner that uploads detected objects must
never have reason to look at MIMIC files). *Alternatives:* disable Ransomware Protection (rejected —
owner keeps it on); uninstall Malwarebytes (rejected); code-sign the toolchain (not possible for
uv-managed CPython / MSYS2 binaries).

> **Addendum (2026-08-28, EP-165).** CLAUDE.md §3 "Session tooling" now carries D-42's
> session-facing rules verbatim (heredoc/stdin-script ban, Write/Edit only, `git commit -F`,
> burst-loop ban, quarantine-first triage) plus the uv-PATH fallback, bare-python and console
> facts, so the machine-local auto-memory notes that duplicated them can retire to pointers.
> D-42 (4) verification for the new PreToolUse hook: the launch line
> (`.venv\Scripts\python.exe scripts\claude_pretool_guard.py`, hook JSON on stdin — data, not
> code) was exercised repeatedly against both endpoint products during EP-165 with no
> Defender/Malwarebytes reaction; the interpreter is the allow-listed workspace-venv python.

> **Addendum (2026-08-29, EP-171).** The write side of (2)/(3) is now measured, not assumed:
> `mwh canary write` rehearsed the loader's five write shapes with synthetic bytes under
> `C:\mimicdata\tmp\canary` — both products live, process survived, every sha256 re-read
> matched — at 191 MB/s (burst: 200 × ~1 MB Parquet into bucket dirs) and 239 MB/s (one
> 2.29 GB sequential Parquet, DuckDB defaults; floors, generation cost included), 13.3 s
> total (EP-171 completion note; roadmap Risk 12).

> **Addendum (2026-09-01, EP-33 — the publish/retry canon; ledger CLI-1/LDR-2,
> WIN-2/3/4).** The I/O adaptations of (2)/(3) are now one module: `mimicwarehouse.publish`
> holds the rename-aside two-step for directories (`swap_dir`, tables) and single files
> (`swap_file`, catalogs and `runs.duckdb`) over one retry core — every rename/remove of
> project-written files retries the transient `PermissionError` (AV/indexer holds,
> ~10 s linear back-off) through `retry_permission` / `rmtree` / `unlink` / `replace`;
> `FileNotFoundError` is tolerated **only** on remove/restore operations, so a `.new` that
> vanishes at the publish rename (a quarantine) rolls the previous live copy back and
> raises instead of reading as success (the pre-EP-33 swap would have deleted the only
> live copy); a `.old` that still cannot be removed after publish is deferred to the next
> swap's sweep, never a failed stage; the file variant fails fast on a plain (non
> `FILE_SHARE_DELETE`) handle with a hint naming the remedy. The EP-171 canary keeps its
> verbatim raw-OS sequence as the canon's sanctioned exception (owner, checkpoint) so its
> baselines stay comparable. Session-guard precision work (`.claude/settings.json`, the
> PreToolUse hook) is handled as an owner-applied diff package (D-45), never edited by a
> session in the same turn as the analysis motivating it.

**D-43 Retrospective consolidation of P0 + P1a (2026-08-18) — owner decisions, to be
distributed as addenda by EP-166.** After EP-12 the owner paused the roadmap for an adversarial
retrospective review of EP-0 … EP-12 (ten lenses, one verifier per material finding, completeness
critic; 81 agents; nothing implemented in the review; record = `roadmap/retro-2026-08-18-findings.md`,
69 verified + 110 minor findings). Decisions taken in four question rounds, each with the recommended
option first, all chosen as recommended unless noted; the implementing brief is in brackets:

1. *Vehicle.* Six numbered retro briefs **EP-165 … EP-170** (S/M each; five in P1 after EP-12, one at
   the head of P2) rather than a standalone document or a fold into EP-16; commit pairs as usual;
   **owner amendment during the round:** implement in *subsequent* S/M sessions (owner usage limits),
   the review session only records — briefs, ledger, this decision, memory. [all]
2. *Session tooling.* `.claude/settings.json` gains `env: {PYTHONUTF8: "1"}`, ~25 literal deny rules
   for the two-step leak paths (`python -c`, `uv run python`, `cp`/`Copy-Item`, `sh -c`, `.NET` file
   APIs, absolute `Read(//C:/**/*.csv…)`), an allow list for read-only project commands, and deny rules
   for connector send/write tools; the parked **PreToolUse command-string hook (GOV-1) is unparked**;
   CLAUDE.md §3 absorbs D-42 + the uv-PATH / bare-python / console facts. [EP-165]
3. *Connectors.* claude.ai MCP connectors and WebFetch/WebSearch are a second egress path: allowed
   for public-reference lookups only (PubMed, bioRxiv, ICD-10 Codes, Context7, GitHub/DOI), never with
   fixture rows, aggregates, ids, run records or note text; send/write tools denied; Google Drive stays
   off (D-29). Recorded in GOVERNANCE §4. [EP-165]
4. *Ask-before files.* GOVERNANCE.md (§2 two-product reality, §4 layers + connectors, data-root
   relocation ⇒ deny rules), `.gitignore` (anchor the data-shaped directory patterns) may be edited by
   the retro briefs; `.gitattributes` unchanged. [EP-165]
5. *Owner actions.* Restart VS Code after the retro lands (the hosting process predates the uv install
   → "uv not on PATH" is a stale-process artefact; the PATH prefix stays documented as the fallback);
   add `%LOCALAPPDATA%\uv\cache` and the Claude scratchpad `%LOCALAPPDATA%\Temp\claude\` to the
   Malwarebytes allow list (D-38: seven → nine paths). [D-38 addendum by EP-165/166]
6. *Catalog swap protocol (Windows).* Rename-aside two-step (`<tier>.duckdb` → `.old`, `.new` →
   `<tier>.duckdb`, remove `.old`), verified to succeed with READ_ONLY duckdb 1.5.5 readers open; readers
   keep the old snapshot; `open_catalog` retries the sub-ms window; same for `runs.duckdb`; the app
   caches results, not connections. Supersedes DESIGN §6's "atomically swaps". [EP-166 words, EP-21/30/35/57 code]
7. *Lake roots.* `Settings.lake_root(tier)`: `lake/` for dev/full, `lake/demo`, `lake/fixture`
   (+ `lake/rejects`), layout keys 15 → 18; fixture/demo builds hard-refuse the credentialed lake;
   `catalog_path(tier)` stays `warehouse/<tier>.duckdb` for all four tiers so `mwh app/sql --tier
   fixture` work. [EP-167]
8. *Test tiers.* The collection hook only deselects above the max tier; readiness = requestable
   fixtures (`raw_root`, `dev_catalog`, `full_catalog`, `dev_ready(step)`, `item_tier`) and a
   `tier(name, needs=…)` kwarg; demo tests = orthogonal opt-in `@pytest.mark.demo` + `--with-demo`
   (`PYTEST_DEMO`), never a ladder step. [EP-168]
9. *Contract.* Sort-key tie-breaks adopted in one edit (`+itemid/+orderid/+emar_seq/+transfer_id/
   +pharmacy_id/+poe_seq/+microevent_id`, ED/Note uniform); `microbiologyevents` stays `large`;
   `Contract.structural_hash()` (load-relevant facts) is what the fixture manifest pins, the full
   `content_hash()` stays informational/provenance; one CSV-dialect constant (`allow_quoted_nulls=true`,
   no `timestampformat` — DuckDB ISO cast accepts fractional seconds); `upstream_type: TIMESTAMP(3)`
   recorded on the nine columns. [EP-169]
10. *Fixture.* Disjoint id floors (subject 90 000 000 / hadm 91 000 000 / stay 92 000 000 / event +
    caregiver 93 000 000 +), one regeneration → `GENERATOR_VERSION 0.2.0`; manifest records
    numpy/polars/python versions + `contract_schema_hash`; numpy/polars are **not** pinned; the
    fixture-change protocol is written once (`tests/README.md`); a `COVERAGE.md` names the vendored
    concepts that are empty/partial on the fixture. [EP-169, prose EP-166]
11. *Snapshot ids.* Layer snapshot id = **logical** sha256 over sorted `(schema, table, path, rows,
    schema_hash, source_sha256/raw_snapshot_id, sort_keys, writer_version)` (the EP-10 pattern); the
    per-file Parquet sha256 is integrity-only; identifier glossary in DESIGN §11 (`raw_snapshot_id`,
    `source_sha256`, `build_id`, layer `snapshot_id`, catalog `build_id`+`core_snapshot_id`, `run_id`,
    `audit_id`, protocol hash); D-26's "this id is the source manifest id" is refined to "per-file
    `source_sha256` **and** `raw_snapshot_id`". [EP-166 words, EP-17/19 code]
12. *CLI.* Console = UTF-8 entry point (`console.run`) + one shared `mimicwarehouse/console.py`;
    `--help` never validates the data root (pending-error `CliState`); `DIAGNOSTIC_COMMANDS` kept, lazy
    validation decided at EP-16; unknown `MWH_*` environment variables **warn** (doctor row + one
    stderr line), never refuse; the four "unknown keys are rejected" doc sentences corrected. [EP-167]
13. *Docs.* The single living status surface is `mimicwarehouse/README.md § State of the workspace`
    (refreshed by re-plan EPs; CLAUDE.md §1 and the root README point at it); DESIGN/DECISIONS notes
    cite completion notes instead of restating; roadmap README gets a "Notation used in briefs" table
    that overrides brief text; every deferred owner review point of EP-9 (resprate DOUBLE, two
    nullability relaxations, 13 docs-sourced FKs), EP-10 (`pending`, `reconcile` exit 1, raw-int JSON,
    early docs page, snapshot id with `rows=null`) and EP-164 (presence-based antivirus warn, item 6)
    is **accepted as shipped**. [EP-166]
14. *Pending briefs.* The ~30 P2/P3 mismatches are fixed by `> **Amended at EP-170 (date).**` blocks
    after EP-16, not by each brief at pickup; `settings.dev_buckets` is the only bucket source (no
    `DEV_BUCKETS` constant); `psutil` joins core at EP-19; EP-42's disclosure dependency is fixed by
    wording, not by moving EP-43. [EP-170]

*Why:* the owner wants the remaining ~150 briefs to build on a foundation whose environment realities,
governance layers, status prose, test semantics and contract are settled once rather than re-discovered
per session; every choice above took the reviewers' recommended option after independent verification.
*Alternatives considered:* a standalone retro document (less traceable); implementing everything in the
review session (rejected by the owner for usage-limit reasons); versioned catalogs + pointer files
(more robust, more code — kept as the fallback if rename-aside misbehaves); a `fixture < demo < dev <
full` ladder; pinning numpy/polars minors; refusing on unknown env vars.

> **Correction (2026-08-30, EP-33 — mechanical repair; EP-33 worklist B9).** The
> *Why/Alternatives* tail above is restored to its original position directly under item
> 14: the EP-165/EP-166 addenda had been inserted between the item list and the tail, and
> the tail's first line ("*Why:* the owner wants … environment realities,") was lost in
> that move — recovered verbatim from `f3eb115`. Content otherwise unchanged; the
> truncated fragment that had been floating below the addenda is removed in the same edit.

> **Addendum (2026-08-28, EP-165).** Items **2** and **3** are shipped by EP-165
> (settings.json env/deny/allow + PreToolUse hook; CLAUDE.md §§1–3/6; GOVERNANCE §2/§4
> amendments; `.gitignore` anchoring; guard G1/G4; ledger ids DOC-1, ENV-1/2/3, GOV-1/2/4/5/6/8,
> DOC-6, CMP-1 struck or absorbed — completion note in the EP-165 brief). Item 4's ask-before
> pre-authorisation was used for GOVERNANCE and `.gitignore`; **deviation:** `.gitattributes`
> also gained `binary` marks for the new G1 suffixes (the brief's In-scope item 5 instructed the
> mirror although item 4 above says "unchanged" — protective-only, flagged for owner review).
> Item 5 (VS Code restart; ninth/eighth Malwarebytes paths) remains with the owner; the restart
> also loads the newly registered hook, which Claude Code snapshots at session start.

> **Addendum (2026-08-28, EP-166).** The distribution this decision asked for is done: the
> word-side of items **6** and **11** is in DESIGN §6/§11 dated notes and a D-24/D-26
> addendum; item **7** in a D-18 addendum + DESIGN §3/§4 notes; item **8** in the same D-18
> addendum + DESIGN §20 note; item **9** in a D-17 addendum; item **10** in a D-27 addendum
> + `tests/README.md` § "Changing the synthetic fixture"; item **13** is live — workspace
> `README.md` § "State of the workspace" exists, CLAUDE.md §1 (EP-165) and the root README
> point at it, roadmap README carries the "Notation used in briefs" table, and the owner
> verdicts on the EP-9/EP-10/EP-164 review points are recorded under D-17/D-26/D-38; item
> **14**'s psutil clause is a D-15 addendum. Still pending: code for items 6–8 and 11–12
> (EP-167/168 + EP-21/30/35/57), item 9–10 code (EP-169), item 14's brief amendments
> (EP-170), and item 5's two owner actions (VS Code restart; ninth/eighth Malwarebytes
> paths — see the D-38 EP-165 addendum).

**D-44 EP-33 becomes the consolidation re-plan of P0–P2, executed as one multi-agent
session (2026-08-30, owner).** The owner re-scoped EP-33 from the standard S re-plan to an
**L** episode that reconciles, simplifies and hardens everything shipped in P0–P2 before
EP-34 opens P3 — run as a **single owner-supervised multi-agent ("ultracode") session**
with one mid-session triage checkpoint (D-2's split-at-pickup rule deliberately waived for
this one episode). Scope decisions, taken in three question rounds on 2026-08-30 (the
recommended option chosen in every round; the amended `roadmap/EP-33-replan-p2.md` is the
executable charter):

1. *Refactor authority.* Full authority over shipped P0–P2 code **including public
   surfaces** (CLI, cross-module APIs, module paths), under hard invariants: the staged
   lake's bytes and snapshot ids untouched (no restage; catalogs/`runs.duckdb` are derived
   and rebuildable), every `mwh verify EP-k` (k ∈ 0…32, 164…171) green at every commit,
   fixture byte-identity kept, every rename propagated to all citing docs/briefs in-session.
2. *Audit.* An enumerated known-debt worklist **plus** a bounded discovery audit (the
   2026-08-18 lenses → adversarial-verifier method) over everything shipped since that
   review's baseline, with a verification pass over the retro's 69 findings; ledger
   `roadmap/retro-p2-findings.md`; findings triaged fix-now / allocate EP-172+ / park /
   reject at the checkpoint — discovery findings are the **only** scope allowed to spill
   past the session.
3. *Owner gates.* Pre-recorded defaults plus one triage checkpoint (defaults recorded in
   the brief, D4a–g: EP-42/EP-43 wording fix with no table move; **no** P3
   toolchain-remediation slot — the concept parse smoke feeds EP-37/38 instead; parallel
   per-bucket sort stays parked with its fired triggers recorded, re-examined before P9;
   bucket count stays 100; fixture outcome enrichment folds into EP-41's 0.3.0; retro CMP-4
   parked; EP-29's undescribed-columns follow-up closed as moot).
4. *Docs.* DESIGN.md is consolidated to **as-built truth** (dated notes folded in,
   superseded planning prose dropped, per-section history pointers; the EP-166 trim
   precedent at document scale — git is the archive); DECISIONS.md **stays append-only**
   and gains a compact status index; READMEs and CLAUDE.md are tightened; one canonical
   gotchas home replaces scattered lore so machine-local memory shrinks to pointers.
5. *Session guard.* Changes to `.claude/settings.json` + the PreToolUse hook are
   pre-authorized **as a class but precision-only** — real-data coverage stays equal or
   tighter (e.g. a Read allowance for the committed synthetic `tests/fixtures/**`,
   path-aware checks for repo-internal `.csv`/`.duckdb` mentions) — and every diff is
   individually checkpoint-approved; GOVERNANCE.md untouched.
6. *Roadmap reach.* P3 briefs may be amended **and reshaped within the phase** (reorder,
   resize, split, add an S brief; phase boundaries and capability coverage stay); P4+
   receives mechanical rename propagation only — substantive P4+ work stays with
   EP-54/EP-74 (D-9 unchanged).
7. *Validation.* The full regression battery closes the episode: fixture suite +
   `poe check` (now with a format gate) + the verify loop, catalog rebuilds on
   fixture/dev/full, tracer re-runs on dev + full diffed against EP-31, a canary re-run,
   guard sweeps, and a perf comparison against the pre-flight baseline.
8. *Overflow & commits.* Work lands as a series of green, hook-guarded checkpoint commits
   (`feat`/`docs` … `(EP-33)`, multi-hash ☑ cell); **all six workstreams (record &
   reconciliation, debt fixes + paradigm unification, docs consolidation, P3 hardening,
   audit, battery) are must-land in-session**; a handoff file is the recorded-failure
   fallback, not a plan.

*Why:* EP-33 is the last cheap moment to rename, unify or simplify anything before ~130
briefs (P3–P11) build on the P0–P2 surfaces; the owner's stated goal is to minimize
unanticipated build-time and runtime issues in the upcoming phases **without conceding
functionality, architecture or design principles**. *Alternatives considered:* keep the S
re-plan and allocate separate retro briefs (the D-43 vehicle — rejected: more session
boundaries and the debt compounds through P3); an audit-only session followed by
implementation sessions (rejected: the owner wants one session); deferring consolidation to
EP-54 (rejected: P3 would code against the un-consolidated surfaces).

> **Addendum (2026-09-01, EP-33 — how it actually ran).** Three sessions, not one: the
> first attempt (2026-08-30) was aborted by a model-safeguard refusal while editing the
> session guard alongside the audit's governance findings; the second (2026-08-31)
> adopted the recorded tunings, ran with no refusal, completed Workstream A, D2 and the
> full discovery audit, and was stopped at usage limits with its outputs salvaged; the
> third (2026-09-01) resumed at the owner triage checkpoint and landed B–F. The
> "audit-only session → owner triage → implementation session" shape the first addendum
> proposed is therefore what happened in practice, and item 8's "must-land in-session"
> held for the implementation session. The checkpoint's decisions are **D-45**; the
> record is `roadmap/retro-p2.md` and `roadmap/EP-33-replan-p2.md`.

**D-45 EP-33 triage-checkpoint decisions (2026-09-01, owner).** Two rounds of four
questions, the recommended option taken in every case:

1. *Triage.* All 45 pending verified findings of `roadmap/retro-p2-findings.md` triaged
   as proposed — 36 fix-now (folded into Workstreams B/C/D), SGD-1/SGD-2 to the B7
   owner-applied diff package, LGR-3 / P3C-1 / TST-3 rejected with reasons, SGT-2
   accepted as policy (D-31 addendum) with an EP-43 amendment; the 60 carried-low
   findings swept where B/C touched the file and otherwise parked in
   `final-roadmap.md` (AUDIT-1) for EP-54. **No new EP-172+ brief was allocated** — every
   spillable item found a home in a P3 amendment or the parked list.
2. *LDR-1.* Stage-level refusal of a strict-subset bucket request over a wider table
   (D-20 addendum); `mwh build --tier full --force` is the only rewrite path.
3. *SGT-2.* Extreme-value aggregates stay admitted, released only inside k-gated rows;
   EP-43 owns any per-column tightening (D-31 addendum).
4. *B7.* Session-guard precision changes (SGD-1, SGD-2, a Read allowance for the committed
   synthetic `tests/fixtures/**`, path-aware checks for repo-internal `.csv`/`.duckdb`
   mentions) are authored **last** as reviewed diff files in the session scratchpad and
   applied by the owner interactively — never by the session (the first attempt's
   lesson); `mwh guard --selfcheck` and `test_ep165` updates ride in the same package.
5. *Renames.* `mimicwarehouse.paths` → `mimicwarehouse.publish` (paths.py deleted;
   `catalog.build.swap_catalog` absorbed as `publish.swap_file`); `mimicwarehouse.console`
   is the single home of `EXIT_OK/EXIT_FINDINGS/EXIT_USAGE/EXIT_REFUSED` (re-exported by
   `safe`, `catalog.cli`, `verify`); `inventory.open_connection` is an alias over
   `engine.open_duckdb("build")`; `inventory._atomic_write_text` an alias of
   `fsio.atomic_write_text`. The full ledger: `roadmap/retro-p2.md` § Renames.
6. *`runs` schema.* Not a safe-query registry exemption; EP-35 allow-lists its label
   columns.
7. *Earlier-EP test edits.* test_ep12's exact `check`-chain pin relaxed; the duplicate
   import-budget tests in test_ep06/11/12 deleted (test_ep02 canonical, test_ep09 keeps
   the lazy-contract clause); fixture counts read from `manifest.json` in
   test_ep21/22/30 — all under the EP-168 churn rule (the coupling was the bug).
8. *B8.* CLI errors go to **stderr** as `mwh <cmd>: …` via `console.fail` (stdout stays
   machine output); the AV canary keeps its verbatim raw-OS sequence.

Routine calls made at the checkpoint without objection: the D4 defaults a–g kept (b now
backed by D2's 65/65 measurement); the gotchas home is `docs/gotchas.md` with a DESIGN
§6.1 pointer subsection and the hygiene canon at `docs/committed-text.md`; the engine
canon module is `mimicwarehouse.engine` (`loader/engine.py` keeps its name); `fmt_int`
stays in `inventory.py`; no agent round over the completeness critique's ten gaps.
*Why:* each option preserves functionality and architecture while removing a duplicate
paradigm or a defect class; the owner's D-44 goal. *Alternatives considered:* per item,
recorded in the checkpoint questions (`roadmap/retro-p2.md` § Checkpoint minutes).
