# EP-40 — Code-set registry + ICD-9→10 GEM utility

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-41 (Phenotype engine + T2DM phenotype), EP-46 (Cohort spec + registry), EP-54 (Re-plan P3)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) Item 4's GEM landing path follows EP-14's convention:
> `ext/vocab/gem/<version>/` (e.g. `ext\vocab\gem\2018\` with `source.yaml` beside it), not a
> flat `ext\vocab\gem\` [FC-24]. (2) `mwh codeset gem fetch` (public CMS zip) needs no
> `MWH_ALLOW_REMOTE` gate — that gate covers text modules only (GOVERNANCE §9) [FC-22].

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **GEM landing** (EP-33 retro
> ledger item; D-36 precursor): the download / manual-placement target is spelled through the
> layout key — `settings.layout["ext"] / "vocab" / "gem" / <version>/` (the EP-170 path, not
> `%MWH_DATA_ROOT%`) — with a `source.yaml` beside it per EP-14's landing template; the EP-22
> demo register (`layout["ext_demo"] / source.yaml`: name, url, sha256, license, accessed,
> files) is the shipped precedent to copy field-for-field. (2) `meta.codesets`,
> `meta.codeset_members` and `meta.gem_*` join the EP-29 `meta.*` family under
> `lake/meta/<tier>/<table>.parquet`, written by `python` steps (`dag.runner.STEP_HANDLERS`)
> and registered by the `catalog` step; any spec file is discovered by `dag.spec.load_dag()`
> per EP-37 (no `--spec`). (3) Dictionary validation reads (`d_icd_diagnoses`,
> `d_icd_procedures`, `meta.itemids`) are contract dims / `meta.*` registry exemptions under
> `safe_query` (EP-33 B1c), so `mwh codeset validate` may print declared/matched/unmatched
> freely; the acceptance's members query already has a real `count(*)`. (4)
> `CodeSetFrozenError` refusals exit through `console.fail(..., code=console.EXIT_REFUSED)` on
> stderr; registry index and review-file writes use `fsio.atomic_write_text` /
> `publish.swap_file`, never ad-hoc rename code.

## Context

Phenotypes (EP-41/42) and cohort specs (EP-46) reference diagnosis, procedure, itemid, drug and
ATC code sets **by id and version**, and every run records the definition hash (GOVERNANCE §12).
This brief builds that registry (`src/mimicwarehouse/codesets/`, DESIGN §8, §15): YAML code sets
with semver + content hash, compiled into `meta.codeset_members` per tier, validated against the
dictionary tables (`d_icd_diagnoses`, `d_icd_procedures`, `d_items`, `d_labitems` — dictionary
data, safe to inspect), plus a CMS GEM crosswalk utility. MIMIC caveat: ICD-9 → ICD-10 switched
around 2015, so every diagnosis/procedure code set is **dual** (`icd9` and `icd10` lists) and
compiled members carry `icd_version`; the GEM utility helps *author* the counterpart list but
never silently applies it — a human reviews the expansion. D-35: free vocabularies first (ICD
public; GEMs public from CMS); LOINC/RxNorm/SNOMED sets are only names here (no table download).
Sessions inspect code sets and dictionary matches freely; patient-level hits stay in the catalog.

## In scope

1. **Schema + registry** (`src/mimicwarehouse/codesets/spec.py`, `registry.py`) — pydantic
   `CodeSet`: `id` (slug), `version` (semver), `name`, `description`, `kind`
   (`icd_dx|icd_px|itemid|drug|atc|loinc|hcpcs`), `members` by system: `icd9: [{code, match:
   exact|prefix}]`, `icd10: [...]`, `itemids: [int]`, `drugs: {names: [...], match:
   contains|regex|exact, rxnorm: [...]}`, `atc: [class]`, `loinc: [...]`; `provenance` (source:
   `mimic-code|AHRQ-CCSR|Charlson|Elixhauser|hand`, url, accessed), `references`, `notes`.
   `def_hash` = sha256 of canonical JSON of `kind + members` (key-sorted, whitespace-free,
   codes normalized: upper-case, no dots). Registry rule: an `(id, version)` pair is immutable —
   loading a YAML whose hash differs from the recorded one raises `CodeSetFrozenError` (bump the
   version). Registry index `meta.codesets` (id, version, def_hash, kind, n_members, path).
   Built-in YAMLs live under `src/mimicwarehouse/codesets/defs/`; user/study YAMLs may be passed
   by path (`%MWH_DATA_ROOT%\studies\<study_id>\codesets\`).
2. **Compile + validate** — `mwh codeset compile [--tier dev] [id@version …]` expands
   prefix rules against the dictionary tables and writes `meta.codeset_members` (codeset_id,
   version, def_hash, system, code, match_kind, label, matched_in_dictionary bool);
   `mwh codeset validate <id@version>` prints coverage: n codes declared / matched / unmatched
   (dictionary-level, printable), unmatched codes listed; `mwh codeset list|show`. Add `mwh
   codeset` to the CLI and a dated note to DESIGN.md §15.
3. **Seed code sets** (`defs/*.yaml`, ≥ 12, each with a provenance line) — dual ICD-dx: `t2dm`,
   `t1dm`, `sepsis_explicit` (septicemia/severe sepsis/septic shock), `aki`, `ckd`,
   `heart_failure`, `mi`, `copd`, `hypertension`, `atrial_fibrillation`; `charlson_groups` (one
   YAML with the 17 Charlson categories transcribed from the vendored `charlson.sql`); itemid sets:
   `labs_creatinine`, `labs_lactate`, `labs_glucose` (from EP-39's YAML if present, else the
   concept SQL); drug sets: `vasopressors` (norepinephrine, epinephrine, phenylephrine,
   vasopressin, dopamine, dobutamine — names from mimic-code medication concepts), `insulin`,
   `antibiotics` (mimic-code `antibiotic.sql` name list), `noninsulin_antidiabetics` (metformin,
   sulfonylureas, DPP-4, SGLT2, GLP-1 agonists, thiazolidinediones); ATC: `atc_a10a_insulins`,
   `atc_c01ca_adrenergics`.
4. **GEM utility** (`src/mimicwarehouse/codesets/gem.py`) — `mwh codeset gem fetch` downloads
   the public CMS 2018 GEM zip into `%MWH_DATA_ROOT%\ext\vocab\gem\` (record URL, sha256, license
   in `source.yaml` per EP-14's landing convention; the owner may place the files manually
   instead), loads `meta.gem_i9_to_i10` / `meta.gem_i10_to_i9` (source, target, approximate,
   no_map, combination, scenario, choice_list); `gem.forward(codes)`, `gem.backward(codes)`;
   `mwh codeset expand <id@version> --via-gem` writes `<id>@<version>.gem-review.md` (proposed
   counterpart codes with flags + dictionary labels) for the owner to fold into a new version.
5. **Tests + docs** — `tests/ep/test_ep40.py` (`@pytest.mark.ep_40`; fixture, `dev`): hash
   is invariant to YAML key order/whitespace/code dots; frozen-version refusal; prefix expansion
   against the fixture dictionaries; every seed YAML validates and has ≥ 1 member per system it
   declares; GEM round-trip on a tiny committed GEM sample (`tests/fixtures/gem_sample.txt`,
   public data); on dev, `mwh codeset compile` populates `meta.codeset_members` and `validate`
   reports ≥ 90 % dictionary match for the ICD sets. `docs/methods/codesets.md` (new): schema,
   versioning rule, dual-era rule, GEM review workflow.

## Out of scope

- Phenotype logic that combines code sets with labs/meds/time → EP-41.
- Cohort criteria referencing code sets → EP-46. Phenotype Studio UI → EP-63.
- OMOP Athena / SNOMED / UMLS concept sets → parked (`final-roadmap.md` § 3).
- Reference-table ingestion via the Linkage Wizard (ATC/Elixhauser/LOINC maps) → EP-143.

## Verification / acceptance

- `uv run poe test -m ep_40` green on fixture and dev; `uv run --group dev mwh verify EP-40` green.
- `uv run --group dev mwh codeset list` shows ≥ 12 sets with versions and hashes; `mwh codeset
  validate t2dm@1.0.0 --tier dev` prints declared/matched/unmatched counts.
- `uv run --group dev mwh sql "SELECT codeset_id, system, count(*) FROM meta.codeset_members GROUP
  BY 1,2 ORDER BY 1,2"` works on dev; `meta.gem_i9_to_i10` exists (row count printed).
- Editing a member in a seed YAML without bumping `version` makes `mwh codeset compile` refuse
  with `CodeSetFrozenError` (demonstrated in a test).

## Parked → final-roadmap.md

- AHRQ CCSR full category YAML generation from the CCSR reference file (public) — trigger:
  P4/P5 subgroup or utilization analyses need broad dx groupings; hazard: file size/versioning.
  *(Mirrored into `final-roadmap.md` § 3 as v2 PHE-5 at execution, 2026-09-06.)*

> **Completion note (2026-09-06).** Executed on fixture + dev as briefed; nothing ran on
> full (the owner's next full rebuild folds `--tag codesets` in, as EP-39's `--tag units`).
>
> **Shipped.** `src/mimicwarehouse/codesets/` — `spec.py` (pydantic `CodeSet`, the seven
> kinds with their member systems, code / name normalisation, the canonical-JSON
> `def_hash`, `id@version` references), `registry.py` (the packaged `defs/` + the
> committed `codesets.lock.json`; `CodeSetFrozenError` when a recorded pair's hash moved;
> dictionary expansion with a bisect over the sorted dictionary codes; the
> `codesets.compile` DAG step → `meta.codesets` + `meta.codeset_members`; `validate`
> through `safe_query`; the `register_codesets` catalog extension, placed between the
> concept walker and `units` so EP-34's and EP-39's order pins hold; the
> `docs/methods/codesets.md` renderer), `gem.py` (the CMS 2018 GEM fetch with pinned
> sha256 per text file + `source.yaml`, the parser, `GemTable.forward` / `backward`, the
> `codesets.gem` step → `meta.gem_i9_to_i10` / `meta.gem_i10_to_i9`, the
> `.gem-review.md` builder), `cli.py` (`mwh codeset list | show | lock | validate |
> compile | expand --via-gem | gem fetch | gem status`), `dag/specs/codesets.yaml`, 20
> seed YAMLs (the brief's 20 ids), `tests/fixtures/gem_sample.txt` (102 public GEM lines
> in the four files' native format, split by the tests), `tests/ep/test_ep40.py` (14
> fixture + 1 dev tests), `docs/methods/codesets.md`; `units.write_meta_parquet` made
> public (the shared meta writer); DESIGN §8 + §15 notes, README state row + quick start,
> `docs/gotchas.md` (YAML octal codes; `executemany` throughput), the vocabularies
> register's GEM landing path (`gem`, per the EP-170 amendment, not `gems`),
> `final-roadmap.md` PHE-5.
>
> **Design calls (routine, logged here).** (1) The "recorded" hash of the immutability
> rule is a per-directory lock file (`codesets.lock.json`, `mwh codeset lock`), so study
> directories get the same rule as the packaged seeds and a brand-new pair loads as
> *unlocked* rather than being refused. (2) Grouped sets (`charlson_groups`) are one
> `CodeSet` whose ICD entries carry a `group` label → `member_group` column; no second
> schema. (3) A prefix that matches nothing still compiles to one unmatched row, so
> `validate` can list it. (4) `mwh codeset compile` runs through the DAG runner (build
> lock, benchmark lines, `run.start`); the `id@version` selection reaches the step through
> a ContextVar set around the runner call and keeps the other sets' rows. (5) The GEM
> fetch pins the sha256 of the four **text files** (the zips' hashes are recorded, a
> re-packaged zip only warns). (6) The `codesets.gem` step is a no-op with a warning while
> nothing is landed, so the session fixture lake and `mwh build --tier fixture` need no
> download. (7) The fetched-hash verification found the CMS archive URLs live on
> 2026-09-06 (`/medicare/coding/icd10/downloads/2018-icd-10-{cm,pcs}-general-equivalence-mappings.zip`).
>
> **Dev tier (2026-09-06).** `mwh codeset gem fetch`: 4 files verified (5.6 MB) into
> `ext\vocab\gem\2018\` with `source.yaml`. `mwh build --tier dev --tag codesets` (build
> `20260906T210007-dev-c165fdf`, run `20260906T210007Z-a95588`): `codesets.gem` 281,071
> rows in 67.2 s (24,860 + 73,593 forward, 81,593 + 101,025 backward — the four files'
> line counts), `codesets.compile` 5,433 member rows in 3.3 s, catalog 2.7 s. Dictionary
> coverage (`mwh codeset validate … --tier dev`, declared entries matched per system):
> t2dm 20/20 + 1/1 (136 compiled rows), t1dm 20/20 + 1/1, sepsis_explicit 9/9 + 13/13,
> aki 1/1 + 1/1, ckd 1/1 + 1/1, mi 1/1 + 2/2, copd 3/3 + 4/4, hypertension 5/5 + 6/6,
> atrial_fibrillation 1/1 + 8/8 (both the FY2020 split and the pre-split I48.1 / I48.2
> exist in the dictionary), heart_failure 16/17 + 14/14 (425.6 is not an ICD-9-CM code —
> Quan's range), charlson_groups 202/224 + 252/289: the 22 unmatched ICD-9 prefixes are
> unused categories inside Quan's ranges (043, 044, 166–169, 177, 178, 425.6, 443.3–443.7,
> 497–499, 572.5–572.7, 583.3, 583.5) and the 37 unmatched ICD-10 rules are WHO-only
> codes with no ICD-10-CM counterpart (B21, B22, B24, C97, E10.0, E10.7, E11.7, E12.x,
> E13.7, E14.x, F00, F05.1, I64, I79.2, I85.9, I98.2, J46, Z49.1, Z49.2). The itemid
> sets match 7/7; the drug / ATC systems have no dictionary here (D-35) and read `n/a`.
> **Acceptance deviation, documented:** the brief asks for ≥ 90 % dictionary match for
> the ICD sets; every hand-typed set reaches 100 % and Charlson's ICD-9 arm 90.2 %, but
> Charlson's ICD-10 arm sits at 87.2 % *by construction* (WHO codes kept for fidelity to
> Quan and to the executed concept); `test_ep40` floors that one arm at 85 % and every
> other ICD arm at 90 %, and the seed's `notes` say so. `mwh sql "SELECT codeset_id,
> system, count(*) AS n FROM meta.codeset_members GROUP BY 1, 2 ORDER BY 1, 2" --tier dev`
> works (19 rows shown, 13 small (set, system) groups suppressed — the standing `k` rule
> applies to a `count(*)` even over a registry table; EP-43 owns any registry-aware
> relaxation; `validate` prints the full numbers); `meta.gem_i9_to_i10` = 98,453 rows
> (dx 24,860 / px 73,593), `meta.gem_i10_to_i9` = 182,618. `mwh codeset expand … --via-gem
> --tier dev` wrote `studies\codesets\reviews\t2dm@1.0.0.gem-review.md` (1 ICD-10 and 19
> ICD-9 proposals, 136 mappings already covered — the ICD-9 proposals are the
> manifestation codes of E11 combination entries, exactly what a human review should
> reject) and `sepsis_explicit@1.0.0.gem-review.md` (4 + 10 proposals, 70 covered).
>
> **Gates.** `uv run poe test -m ep_40`: 14 passed (fixture); with `--tier dev`: 15
> passed (dev summary: 20 sets compiled, 32 (set, system) pairs, ICD coverage min 87.2 %,
> GEM 98,453 / 182,618); `uv run mwh verify EP-40`: 14 passed; `mwh guard` clean over the
> new files; `poe check` green — ruff check, `ruff format --check`, pyright (0 errors) and
> the full fixture suite: 968 passed, 41 deselected (the dev / full / demo probes), 412 s
> (run before two test-only edits to `test_ep40.py`; the module was re-linted and re-run
> after them, 14 passed); `poe roadmap-check`: 0 errors, 0 warnings. The frozen-version
> refusal is demonstrated in
> `test_frozen_version_refused_and_bump_allowed` (library, `mwh codeset list`, `compile`,
> `validate` → exit 3). No earlier `test_ep*.py` was edited.
>
> **Owner decisions at the interactive review (2026-09-06).** (1) Commit in the standard
> two steps, no push (the owner pushes) — done, hashes in `README.md`. (2) Keep the
> faithful Charlson transcription with the documented 85 % floor on its ICD-10 arm
> (rejected: pruning the 37 WHO-only codes). (3) Leave the `k` rule on `count(*)` over
> registry tables as it stands; a registry-aware relaxation belongs to EP-43 (rejected:
> a `safe_query` exemption in this brief). (4) Defer the full-tier `--tag codesets` run to
> the next full rebuild, as with EP-39's `--tag units` (rejected: a background job now).
> Still open for the owner: the two GEM review files under the data root await a human
> read (dictionary text only).
