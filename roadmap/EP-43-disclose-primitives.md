# EP-43 — Disclosure primitives (`disclose` module)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-30 (Safe-query wrapper + audit log) · **Blocks:** EP-44 (Data-quality profiling), EP-48 (Attrition diagram renderer), EP-54 (Re-plan P3), EP-58 (App shell B: row-view gate + app-side small-cell enforcement), EP-59 (Export primitives), EP-130 (Report engine A: Jinja2 → MD/HTML), EP-133 (Disclosure-review tool)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The committed `tests/fixtures/disclose/bad_ids.csv` must not itself carry
> real-band ids — guard **G4 scans `.csv` content** and would refuse the commit: craft it with
> `9x`-band ids (≥ 90 000 000) under an identifier-named column so the *checker's* `ID_COL` rule
> fires while the guard stays clean, pragma-free (never `mwh-guard: allow` on data-shaped
> content) [FC-25]. (2) The checker's id-band scanner (item 2b) must skip count-like cells: a
> large row count that happens to fall inside a band is not an id — scope band matching to
> identifier-named columns and inline text, not to every 8-digit integer (mirror the guard's
> count-column exemption) [FC-16]. (3) Acceptance line added at EP-170: after the module ships,
> `mwh disclose check` retroactively passes on EP-42's `runs/<run_id>/phenotype_prevalence.md`
> (see the EP-42 amendment); the EP-29/EP-32 retroactive checks arrive via the EP-33 re-plan as
> already planned.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (a) **Retroactive checks** (EP-33
> D1 queue, closing the EP-170 amendment's "arrive via the EP-33 re-plan" clause): after the
> module ships, `mwh disclose check --write-sidecar` runs on `mimicwarehouse/DATA-DICTIONARY.md`
> (EP-29) and `docs/analyses/00-staging-benchmark.md` (EP-32) — both committed pre-EP-43 with
> "sidecar pending" headers — writing their `.disclosure.json` sidecars and removing the
> pending header; both join the acceptance beside EP-42's `phenotype_prevalence.md`.
> (b) **n vs n_fit complementary suppression** (EP-31 lesson): the tracer publishes the cohort
> `n` and the fitted `n_fit`; when `fit` excludes zero-/all-event levels, their difference can
> back out a small excluded-level cell — `suppress()` must treat such paired totals as a margin
> (suppress complementarily) and `check()`'s small-cell scan must flag a derivable difference of
> two published totals in `(0, k)`; the EP-31 tracer report is the regression case (EP-53
> promotes it). (c) **`SUPPRESSOR` hook** (carried P3C-9, ownership settled here):
> `safe.SUPPRESSOR: Callable[[polars.DataFrame, int, list[str]], tuple[polars.DataFrame, int]]`
> — `(df, k, count_columns) -> (df, rows_suppressed)` — is the seam, and **this brief** swaps in
> an adapter over `disclose.suppress` (mapping `count_columns` -> `count_cols`, returning the
> suppressed-row count from `SuppressionReport`); the hook signature is fixed by safe.py, and
> item 1's richer signature is `disclose.suppress`'s own API, not the hook's. (d) **SGT-2
> policy** (owner decision; D-31 addendum): extreme-value aggregates (`min`/`max`/`mode`/
> `median`/`quantile`) stay admitted by `safe_query` and are released only inside k-gated rows;
> any per-column tightening (e.g. refusing `min`/`max` over subject-keyed non-count columns) is
> decided in this module — record the decision in `docs/methods/disclosure.md` either way.
> (e) **Error-text convention** (DKB-2): `safe_query` sanitizes DuckDB execution-error text
> before it reaches a refusal message or audit line; `check()`/`write_sidecar()` apply the same
> rule to any error text that enters a committed artifact (`CheckResult` details, sidecar
> `checks[].detail`) — never verbatim engine messages. (f) Committed-artifact hygiene defers to
> `docs/committed-text.md` (EP-33): no identifier column names, no string value > 64 chars, no
> run ids / compact dates in committed file names — `check()` encodes the first two, the guard
> covers the third. (g) Exit codes: findings -> `console.EXIT_FINDINGS`, usage ->
> `console.EXIT_USAGE`, messages on stderr via `console.fail`.

## Context

GOVERNANCE §5 and §7 (D-33, D-40) require that any count below 11 is warned in-app and
suppressed on export/commit/return-to-session, with complementary suppression, and that nothing
leaves `runs/` for `reports/`, `docs/` or git without `mwh disclose check <path>` passing and a
`.disclosure.json` sidecar. `safe_query` (EP-30) already refuses identifier columns, note text and
unsuppressed small counts on the session path; this brief implements the general primitives once
in `src/mimicwarehouse/disclose.py` (DESIGN §14, §15) so every later brief calls them and none
re-implements suppression. It is a governance brief: acceptance is the module **refusing crafted
violations**. It is fixture-tier only — every test uses synthetic frames and files; the checker's
identifier heuristics take the three real MIMIC id bands from GOVERNANCE §3 (the same constants
EP-4's guard uses) as *patterns* to refuse, never as data; crafted test values are synthetic
integers ≥ 90 000 000 or literals constructed inside the test. The attrition renderer (EP-48)
needs a chain-aware mode, so it is defined here.

## In scope

1. **`suppress()`** — `suppress(df, k=11, count_cols=None, group_cols=None, mode="table",
   complementary=True) -> (df_out, SuppressionReport)` for pandas/Polars: cells with `0 < n < k`
   in count-like columns (auto-detected: integer columns named `n`, `n_*`, `*_n`, `count*`,
   `denominator`, `numerator`, `events`, or explicitly listed) become null with a marker column
   `<col>_suppressed = True`; `complementary=True` ensures no row/column margin can back out a
   suppressed cell (if exactly one cell in a margin is suppressed, suppress the next-smallest;
   iterate to fixpoint; totals recomputed or suppressed accordingly). `mode="chain"` (attrition
   sequences `n_0 ≥ n_1 ≥ …`): a step whose drop `n_{i-1} − n_i` is in `(0, k)` has the drop
   suppressed and both adjacent totals coarsened to bands rounded to the nearest 10 with a
   `~` marker (documented as the rounding rule; still complementary). Also `render_cell(n, k)` →
   `"<11"` and `SuppressionReport` (n_primary, n_complementary, cells).
2. **`check(path)`** — `check(path, k=11) -> CheckResult` over `.csv .parquet .json .md .mmd
   .html .svg .png .txt .yaml`: (a) **identifier columns**: any column whose lower-cased name is
   in {subject_id, hadm_id, stay_id, note_id, emar_id, pharmacy_id, poe_id, transfer_id,
   caregiver_id, provider_id, order_id, microevent_id, labevent_id, chartevent_id…} or matches
   `*_id` outside an allow-list (itemid, codeset_id, phenotype_id, cohort_id, run_id, protocol_id,
   concept_id, patch_id, study_id) → FAIL; (b) **id-band literals**: 8-digit integers within the
   three real bands appearing in text/HTML/JSON/Markdown/Mermaid or in numeric columns → FAIL
   (reuse EP-4's guard scanner); (c) **free text**: string columns whose median length > 80 chars
   or with > 500 distinct values that are not in a dictionary allow-list, columns named
   `text|note|comments|value_text|narrative` → FAIL (`--allow-text <col>` for known dictionary
   labels); (d) **small cells**: count-like columns (rule from item 1) or Markdown/HTML tables
   with an integer cell in `1..k-1` not marked suppressed → FAIL; (e) **embedded arrays**: HTML
   `<script>` JSON, Vega/Vega-Lite `data.values`, Plotly `data[]` arrays with > 1 000 rows or
   with identifier keys → FAIL; > 200 rows → WARN; (f) images (`.png/.svg`): size and an SVG text
   scan only, plus a mandatory sibling `.json`/`.csv` source table check when present. Every finding
   has a code (`ID_COL`, `ID_BAND`, `FREE_TEXT`, `SMALL_CELL`, `EMBEDDED_ROWS`, `NO_SOURCE`).
3. **Sidecar + CLI** — `write_sidecar(path, result, k, reviewer="owner")` →
   `<artifact>.disclosure.json` {path, sha256, size, checks (code, status, detail), k, passed,
   reviewer, timestamp, tool_version, git_sha}; `mwh disclose check <path…> [--k 11]
   [--write-sidecar] [--allow-text col]` exits non-zero on any FAIL and prints a rich table;
   `mwh disclose verify <path>` re-hashes against the sidecar; also `disclose.assert_clean(df)`
   for in-process use by EP-59/EP-130 and `warn_badges(df, k)` returning per-cell warn flags for
   the app (EP-58).
4. **Governance tests** (`tests/ep/test_ep43.py`, `@pytest.mark.ep_43`, fixture) — the module
   **refuses**: a CSV with a `subject_id` column; a Parquet whose integer column holds a value the
   guard's band scanner flags (constructed in-test from the GOVERNANCE §3 constants); a Markdown
   table with a cell of 7; a `.mmd` attrition diagram containing `n = 4`; an HTML file with a
   5 000-row embedded Vega dataset; a Parquet with a `text` column of 200-char strings; and
   **passes**: a clean aggregate CSV, a suppressed table (marker columns present), a `.mmd` with
   `<11`. Suppression tests: 2×2 with one small cell → two suppressed and totals consistent; a 3×3
   margin case reaches fixpoint; chain mode on `[1000, 995, 400]` suppresses the drop and bands
   both neighbours; idempotence (`suppress` twice = once); sidecar JSON validates and `verify`
   fails after editing the artifact.
5. **Docs + wiring** — `docs/methods/disclosure.md` (new): rules, codes, examples of the
   suppressed rendering, the sidecar schema; GOVERNANCE §5/§7 unchanged; `CLAUDE.md` already
   points here. Register `mwh disclose` in the CLI (already listed in DESIGN §15).

## Out of scope

- App-side warn badges and export-button gating → EP-58; export primitives with provenance footer
  → EP-59; the Disclosure-review UI → EP-133; report engine integration → EP-130.
- Suppression *inside* `safe_query` results → EP-30 (already done; may switch to call `suppress`).
- Differential-privacy noise for public aggregates → parked (`final-roadmap.md` § 36–38).

## Verification / acceptance

- `uv run poe test -m ep_43` green on fixture; `uv run --group dev mwh verify EP-43` green.
- `uv run --group dev mwh disclose check tests/fixtures/disclose/bad_ids.csv` exits non-zero with
  `ID_COL`; `… bad_small_cell.md` exits non-zero with `SMALL_CELL`; `… good_aggregate.csv
  --write-sidecar` exits 0 and writes `good_aggregate.csv.disclosure.json` (all three fixture files
  are synthetic and committed).
- `mwh disclose verify` fails after a one-byte edit to the artifact.
- `docs/methods/disclosure.md` exists and lists every finding code with an example.

## Parked → final-roadmap.md

- **Functional-dependency-aware margins and attribute disclosure** (mirrored as v2 DIS-4):
  the shipped margin model treats every proper subset of the group columns as a
  publishable margin, so co-varying group columns (`valueuom` / `unit_norm` / `expected`
  in `meta.item_unit_variants`) over-protect rather than under-protect; the checker cannot
  see totals published in *other* files; a 100 % / 0 % cell over a group of ≥ k units is
  not flagged. Standard SDC tooling (tau-argus-style) is the reference when a reviewer
  (EP-133) hits a false complement or a real attribute leak.
- Banding of extreme-value aggregates (`min` / `max` to bands) stays with the DP item
  (DIS-1) — the SGT-2 decision below keeps the row gate as the release condition.

> **Completion note (2026-09-07).** Fixture tier, one session (≈ M). Shipped
> `src/mimicwarehouse/disclose.py` (≈ 2,000 lines after formatting), `mwh disclose check |
> verify`, `mwh fixtures disclose`, `tests/ep/test_ep43.py` (18 tests), the three committed
> fixtures under `tests/fixtures/disclose/`, `docs/methods/disclosure.md`, and the
> retroactive sidecars `DATA-DICTIONARY.md.disclosure.json` and
> `docs/analyses/00-staging-benchmark.md.disclosure.json`.
>
> **As built vs the brief.** (1) `suppress()` is cell-level with `<col>_suppressed`
> markers and an explicit, documented margin model (every proper subset of the group
> columns is a publishable margin; a margin with exactly one hidden cell hides its
> next-smallest published cell, preferring an already-damaged row; nested totals such as
> `n` / `n_fit` lose the smaller one when their difference is small — amendment (b);
> rate-like columns are blanked beside a hidden count; fixpoint; idempotent). The 2×2 →
> two suppressed, the 3×3 → a 2×2 rectangle at fixpoint, chain mode on `[1000, 995, 400]`
> → the drop withheld, both neighbours `~1,000`; chain mode also withholds every drop
> beside a banded total so no exact difference survives (the rounding rule, documented).
> (2) `check()` covers the ten extensions with the six brief codes **plus a seventh,
> `OVERSIZE`** (image size: warn > 5 MiB, fail at the guard's 20 000 KiB bound) — the
> brief's "(f) images: size" had no code to carry it. The band scan skips count-like and
> telemetry columns / keys (amendment 2), Markdown / HTML tables get an exempt-header list
> (levels, codes, versions, units, telemetry) plus the nested-total and attrition-chain
> rules, `n = 4` is caught in prose / Mermaid / SVG / HTML text, embedded arrays are
> extracted from whole-JSON scripts and from `"values":` / `"data":` / `Plotly.newPlot(`
> by bracket matching. Findings are value-free (guard-style masking) and parser / engine
> text is sanitised through `safe.sanitize_error_text` (amendment e). (3) The
> **`safe.SUPPRESSOR` hook is `disclose.safe_suppressor`** (amendment c): complementary
> suppression released **row-wise**, so `mwh sql`, `units.suppress_variants` and
> `concepts.pins` keep their rows-kept-or-dropped contract; this is also where the
> **SGT-2 decision** landed — no per-column tightening of extreme-value aggregates,
> recorded in `docs/methods/disclosure.md` §2 and the D-31 addendum (amendment d).
> (4) Sidecars follow the brief's schema (`mimicwarehouse.disclosure/1`; `path` = file
> name so the pair moves together; one `checks` entry per code) and are written only for
> a passing result. (5) The three fixtures are written by **`mwh fixtures disclose`**
> (`fixtures/disclose.py`, `FILES` = the bytes; `test_ep43` asserts no drift): the session
> deny rules refuse the Write tool on `*.csv`, and CLAUDE.md §2 forbids working around a
> denial, so a generator is the one sanctioned writer, as `mwh fixtures build` is for the
> hosp / icu tree (amendment 1's crafting rule is honoured: the ids are `9x`-band under an
> identifier-named column, no pragma).
>
> **Retroactive checks (amendment a + the EP-170 acceptance line).**
> `DATA-DICTIONARY.md` was regenerated from the full catalog with the new header line
> (`catalog/dictionary.py`; only the header, build id `20260907T001149-full-41fbf65` and
> build time changed — the core snapshot id is unchanged) and passes; so does
> `docs/analyses/00-staging-benchmark.md` with its header rewritten; both sidecars are
> committed and `test_ep43` re-verifies the pairs. EP-42's `phenotype_prevalence.md`
> passes on both the dev run `20260907T001135Z-132f3c` and the full run
> `20260907T001401Z-aec143` (they stay under `runs/` for EP-53; the first dev pass
> exposed a checker bug — the prose rule read `n = 3,588.` as `n = 3` before the trailing
> period — fixed and regression-tested). `docs/analyses/README.md`'s index and rule
> paragraph were updated (the tracer row now says "pending promotion at EP-53").
>
> **Earlier tests edited (roadmap CMP-6 rule — a shipped fact legitimately changed):**
> `test_ep30::test_small_group_suppressed_rowwise` and `::test_sql_cli_csv_format`,
> `test_ep33_safe::test_aliased_cast_count_still_k_suppressed` and
> `::test_union_all_allowed_audited_and_suppressed_rowwise` — the crafted small group now
> loses its complementary `rest` row too (the old assertion `n = total − 1` *was* the
> back-out this brief closes); `test_ep39::test_variants_on_crafted_tables_and_suppression`
> — the `(HR, "bpm")` spelling blanks beside the small `BPM` cell (the per-itemid
> normalised-unit total is one harmonised `count(*)` away); `test_ep29` (two pins) and
> `test_ep32` (two pins) — the "sidecar pending EP-43" header lines became the check +
> sidecar lines. Every earlier `mwh verify EP-k` still exits 0 (the full `poe check` gate
> below). Behaviour change to record for sessions: `mwh sql` results may now lose
> complementary rows as well as small ones, and a dev-tier `concepts.pins --refresh`
> may report extra suppressed cells for the same reason (the demo pins are unaffected:
> k = 1 there).
>
> **Owner decisions (end-of-session round, 2026-09-07):** (1) commit as the two-step
> pair — done, hashes in `roadmap/README.md`; (2) **keep the complementary hook** (the
> alternative, a primary-only hook with complementary suppression only on export, was
> declined: a session read is a release path); (3) the fixture sidecar
> `tests/fixtures/disclose/good_aggregate.csv.disclosure.json` that the acceptance
> command writes (timestamp + git sha of the moment) is **ignored** — one
> `.gitignore` line, `mimicwarehouse/tests/fixtures/disclose/*.disclosure.json`
> (CLAUDE.md §6 change, owner-approved); (4) the **regenerated `DATA-DICTIONARY.md`** is
> accepted as is (build id moved to the live full catalog's; the never-edit-by-hand rule
> holds).
>
> **Gates:** `uv run mwh verify EP-43` 18 passed; `uv run poe check` green — ruff check,
> ruff format --check, pyright 0 errors, pytest **1,012 passed** (44 dev/full/demo probes
> deselected) in 535 s; `uv run poe roadmap-check --strict` 0 errors, 0 warnings;
> `mwh guard` clean on every new and edited file. Docs: DESIGN §14 note + §15 lines, DECISIONS addenda under
> D-31 (SGT-2), D-33 (the one implementation, the hook swap) and D-40 (the first
> sidecars), README § State row + quick start, `final-roadmap.md` DIS-4.
