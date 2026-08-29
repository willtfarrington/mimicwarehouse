# EP-13 — Repos & awesome-lists inventory

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** — · **Blocks:** EP-16 (Re-plan P1)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) `mwh verify EP-13` (EP-6): because `tests/ep/test_ep13.py` *exists*, verify does not take the docs-only
> shortcut — it runs `pytest -m ep_13`, and "nothing collected" (pytest 5) maps to exit 2, so the module must
> carry `pytestmark = pytest.mark.ep_13` (EP-5 precedent: tier `n/a` with a test module). (2) **G4 hygiene for
> a docs table:** the guard scans `.md`; write "Last activity" and "checked on" cells as hyphenated ISO dates
> (compact `YYYYMMDD` is refused, no exemption) and never quote a bare 8-digit issue/PR number starting 1/2/3
> (or use the `mwh-guard: allow` pragma on that line for a documented example). (3) Dependency-group facts
> as shipped (D-15 addendum): `gpl` and `text` are opt-in groups, still empty; only `ui`↔`gpu` and
> `ui`↔`text` are in `[tool.uv] conflicts` — a GPL tool is isolated *by being opt-in*, not by a conflict, and
> a text-track dependency can never be co-installed with Streamlit; word the verdict/notes columns
> accordingly. (4) `mimicwarehouse/docs/resources/` does not exist yet (only `docs/brand/`); whichever of
> EP-10/13/14/15 runs first creates it and `README.md`. Command forms: `uv run mwh …` ≡ `uv run --group dev
> mwh …`.

> **EP-166 pickup note (2026-08-28; ledger DOC-15).** Amendment item (4) above is overtaken:
> `mimicwarehouse/docs/resources/` **exists** — EP-10 committed `raw-inventory.md` there — but has
> **no `README.md` yet**: create it in this session (index rows for `raw-inventory.md` and the new
> `repos.md`, plus placeholders for EP-14/15's files) so EP-16 item 2's "add its row" has a file to
> land in. The mimic-code row in `repos.md` cites the D-19 pin (`vendor_info().sha`,
> `concepts/vendor/VENDOR.json`) and the repo-root `NOTICE` attribution — the entry EP-8's
> completion note handed to this brief. The status prose a cold session reads first is current
> again since EP-166 (workspace `README.md` § "State of the workspace"); the "Notation used in
> briefs" table in `README.md` (this folder) § How to use overrides brief shorthand.

## Context

**D-10** makes resource gathering explicit (owner template steps 2–3): before the staging phase we
record, in one cited markdown file, every repository, pipeline and awesome-list the planning research
found around MIMIC-IV, with its license, activity, MIMIC/Python version targets, and an
**adopt / port / ignore** verdict tied to the EP that uses it — so later sessions borrow deliberately
(mimic-code build script for EP-17, ACES's YAML predicate shape for EP-46, MEDS 0.4 columns for EP-50,
PyHealth task definitions for EP-110/111, jaanli's suppression UI pattern for EP-58) and never rebuild
on dormant MIMIC-III tooling. Verdicts must respect **D-19** (mimic-code adopted, vendored by EP-8),
**D-20** (custom runner; dbt-duckdb parked), **D-34** (permissive licenses only in core groups; GPL only
via `gpl`), and the parked items already in `final-roadmap.md` (MEDS/ACES lane, OMOP, FHIR). This is a
docs-only brief (tier n/a): web research is allowed, no data is touched, nothing is installed. Do not paste
code from any repo; link and summarise.

## In scope

1. **`mimicwarehouse/docs/resources/repos.md`** — a header (purpose, verdict vocabulary, "checked on"
   date, how to add a row) and one table: `Resource | URL | License | Last activity (as of <date>) | MIMIC target |
   Python | What it offers | Verdict | Used by / borrowed by | Notes`. Verdict vocabulary: **adopt** (depend on
   or vendor it), **port** (re-implement the idea/logic in our package, cite it), **ignore** (record why —
   dormant, MIMIC-III-only, R-dependent, license, scope). Minimum rows (all named in the planning research;
   verify each URL live and correct names/paths as found): MIT-LCP/mimic-code (adopt; EP-8/37/38; also its
   `mimic_utils` transpiler and `concept_map/*.csv` → EP-138); MEDS (Medical-Event-Data-Standard) + ACES /
   `es-aces` (port the YAML predicate/trigger/window shape → EP-46; optional validation lane over the EP-50 spine);
   `MIMIC_IV_MEDS` ETL (ignore as ETL, port the column set → EP-50; note Python < 3.14 and `.csv.gz` inputs);
   PyHealth (reference only: task definitions → EP-110/111; check MIMIC-IV 3.1 support); healthylaife
   MIMIC-IV data pipeline (reference: feature/cohort steps → EP-102); philipdarke/mimic4 (as named in the research —
   confirm what it is; likely reference); jaanli MIMIC-IV visualization (port: small-cell suppression pattern and
   aggregate-only views → EP-58/EP-64); CogStack `dbt_mimic_omop` (ignore in v1; parked v2 OMOP-1); kind-lab
   `mimic-fhir` (ignore in v1; parked v2 FHIR-1); OHDSI/MIMIC ETL (ignore: BigQuery/2.2); the dormant MIMIC-III
   set — MIMIC-Extract, YerevaNN mimic3-benchmarks, FIDDLE, TemporAI/clairvoyance, pyicu, YAIB (R `ricu`) —
   each **ignore** with the reason and the one idea worth borrowing (e.g. benchmark task definitions);
   medspaCy (adopt in the `text` group, P10); PhysioNet demo repositories (adopt via EP-22).
2. **Awesome-lists section** — at least three curated lists (e.g. `awesome-healthcare`, `awesome-clinical-nlp`,
   any `awesome-mimic*` list found) with URL, license, last activity, and whether they carry a MIMIC-tooling
   section (an open question in the research); pull ≥ 3 additional MIMIC-IV-relevant repos discovered through
   them into the main table (total ≥ 15 rows).
3. **"Borrow map"** — a short second table `Our EP | Resource | Exactly what we borrow` (≥ 8 rows) so the
   loader, cohort, spine, ML and UI briefs can cite one line instead of re-researching. Include the mimic-code
   `build_mimic.sh` COPY options + progress table (EP-17/18), `validate.sql` (EP-10/28), `concepts_duckdb`
   (EP-37), ACES spec shape (EP-46), MEDS 0.4 columns (EP-50), PyHealth readmission/mortality task windows
   (EP-110/111), jaanli suppression UI (EP-58), and `concept_map` (EP-138/143).
4. **Index** — create `mimicwarehouse/docs/resources/README.md` if absent (one paragraph + a table
   `File | What | Owner EP`), add the `repos.md` row (EP-14/15 add theirs; whichever runs first creates the file).
5. **Test** (`tests/ep/test_ep13.py`, `@pytest.mark.ep_13`): `repos.md` exists; the main table has the ten
   required column headers and ≥ 15 rows; every row's Verdict ∈ {adopt, port, ignore}; every URL cell starts with
   `https://`; the file contains no token in the real MIMIC id bands and no `subject_id =`-style row fragments
   (docs hygiene); the borrow map has ≥ 8 rows.

## Out of scope

- Vocabularies/ontologies (LOINC, RxNorm, ATC, CCSR, GEMs, Athena) → EP-14.
- Papers, chapters, companion datasets, methods notes → EP-15.
- Vendoring or installing anything from the inventory → EP-8 (mimic-code), EP-1/re-plans (dependency groups),
  EP-138 (`concept_map`), P10 (medspaCy).
- Editing `final-roadmap.md` → EP-16 (mirror new parked items there at the re-plan).

## Verification / acceptance

- `uv run poe test -m ep_13` and `uv run --group dev mwh verify EP-13` green (docs test).
- Every URL in `repos.md` was fetched during the session (HTTP 200 or a GitHub redirect); the "checked on" date
  is today's; rows for dormant tools carry an explicit reason.
- `docs/resources/README.md` lists `repos.md`; the borrow map names at least the eight EPs above.
- Commit `feat(mimicwarehouse): repos & awesome-lists inventory (EP-13)`, then `docs(roadmap): record EP-13 commit hash`.

## Parked → final-roadmap.md

- Any repo judged "port later" (e.g. meds-tab baselines, MEDS-DEV tasks, healthylaife feature pipeline) — list each
  with its trigger; EP-16 mirrors them into the matching category tables.

> **Parked at execution (2026-08-28).** Five "port later" items for EP-16 to mirror (all
> carry an ignore verdict in `repos.md` today):
> 1. **MEDS_transforms stage catalog** (mmcdermott/MEDS_transforms, MIT) — trigger: the D-20
>    custom runner ever needing a reusable transform-stage library.
> 2. **MEDS-DEV task definitions** (Medical-Event-Data-Standard/MEDS-DEV, MIT) — trigger: a
>    MEDS validation lane over the EP-50 spine (the lane itself is already parked, D-20/D-25).
> 3. **meds-tab tabularized baselines** (mmcdermott/MEDS_Tabular_AutoML, MIT) — trigger:
>    EP-110/111 wanting an external baseline comparator.
> 4. **healthylaife feature pipeline, full port** (MIT) — trigger: EP-102 needing more than
>    the cohort/feature step ordering the borrow map already cites.
> 5. **saywurdson/mimic-iv-dbt** (Apache-2.0, dbt-duckdb → OMOP CDM 5.4) — trigger: v2
>    OMOP-1; the strongest local-OMOP reference found (the OHDSI ETL is BigQuery-only).

> **Completion note (2026-08-28).** Shipped `docs/resources/repos.md` (27 main-table rows —
> all minimum rows plus five discovered during the awesome-list sweep; 4 awesome lists; a
> 10-row borrow map naming EP-10/17/22/28/37/38/46/50/58/64/102/110/111/138/143),
> `docs/resources/README.md` (index with EP-14/15 placeholders), and `tests/ep/test_ep13.py`
> (7 tests; `poe test -m ep_13` and `mwh verify EP-13` green, 0.9 s). Every GitHub row was
> verified live via the GitHub REST API (`gh api repos/<owner>/<name>`, HTTP 200; license,
> last-push date and description recorded as returned); the two PhysioNet demo pages were
> fetched directly (HTTP 200). Findings a later session should know: (1) the "jaanli"
> MIMIC-IV visualization named in the planning research lives at
> `altosaar/mimic-iv-visualization` — the `jaanli` account no longer exists; (2) **no
> `awesome-mimic*` curated list exists** on GitHub, and none of the four awesome lists
> checked carries a MIMIC-tooling section (the research's open question — answered no);
> (3) `philipdarke/mimic4` is confirmed as a small dormant personal MIMIC-IV → DuckDB
> loader (ignore); (4) CogStack `dbt_mimic_omop` has **no license file**, so D-34 would
> block adoption regardless of the OMOP park; (5) PyHealth is at 2.0.1
> (`>= 3.12, < 3.14`, adds MEDS-based tasks) and its MIMIC-IV loader has no explicit 3.1
> pin — re-check at EP-110; (6) `MIMIC_IV_MEDS` confirms the brief's `>= 3.11.4, < 3.14`
> cap and `.csv.gz` inputs; (7) pyicu resolved to `aidh-ms/pyICU` (MIT, dormant 2024).
> MEDS release 0.4.1 (2025-11-05) confirms the 0.4 column set EP-50 borrows.
