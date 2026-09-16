# EP-172 — GEM review adjudication: t2dm + sepsis_explicit (owner-supervised)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-40 (Code-set registry + ICD-9→10 GEM utility), EP-41 (Phenotype engine + T2DM phenotype), EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage) · **Blocks:** EP-46 (Cohort spec + registry), EP-54 (Re-plan P3)

> **Allocated 2026-09-16 by the owner at EP-45 completion** (the house convention for a
> mid-phase addition: the next free number, slotted where it runs — between EP-45 and EP-46,
> because EP-46's pickup gate needs its result). It replaces the "owner reviews the two files
> alone" step that EP-41 silenced until EP-45: the review becomes one supervised session in
> which the owner rules on every proposed code after the agent's explanation. The roadmap
> tooling parses integer brief numbers only, which is why this is EP-172 and not "EP-45b".

## Context

EP-40 built the code-set registry (`id@version`, frozen pairs in `codesets.lock.json`) and the
CMS 2018 GEM utility, and `mwh codeset expand <ref> --via-gem --tier dev` wrote two review
files under the data root, `studies\codesets\reviews\t2dm@1.0.0.gem-review.md` and
`sepsis_explicit@1.0.0.gem-review.md` (EP-40 completion note: t2dm proposes 1 ICD-10 and 19
ICD-9 counterparts, mostly manifestation codes of E11 combination entries; sepsis_explicit
proposes 4 + 10 with 70 mappings already covered). A GEM is not a crosswalk — approximate,
one-to-many and combination entries are proposals for a human — so the registry never applies
a review automatically (`docs/methods/codesets.md` §6). The files carry **public vocabulary
text only** (proposed codes, dictionary titles, GEM flags, source codes; no patient data), so
the owner may paste their contents into the session: that is the one way a data-root file
reaches a session here, and it is admissible under GOVERNANCE §4 because it is dictionary
text (D-35). Sessions still never open the files themselves. EP-46 seeds cohort specs that
reference code sets by `id@version`, so the reviewed versions must exist first; EP-46's pickup
gate now depends on this brief.

## In scope

1. **Session protocol (owner-supervised).** The owner pastes the two review files at the
   start of the session. For **each proposed code**, one at a time, the agent explains before
   the owner rules: what the code means (public ICD-10-CM / ICD-9-CM text; the ICD-10 Codes
   connector is a public-reference lookup and may be used), the GEM entry kind (exact /
   approximate / combination with its scenario flags) and which source code(s) proposed it,
   whether the code fits the set's stated intent and its deliberate exclusions (`t2dm`: type
   2 or unspecified type only; no type 1 fifth digits, no secondary / drug-induced /
   gestational diabetes, no long-term insulin use; `sepsis_explicit`: Angus explicit codes
   extended to ICD-10-CM; no bacteremia), and a recommendation. The ruling is taken through
   one `AskUserQuestion` per code with the options accept / reject / defer (defer = leave out
   of this version, note it). Every ruling and its reason go into a **decision ledger** in
   the completion note (public codes and titles only). No-map and GEM-absent sources are
   listed for the record, not ruled on.
2. **New code-set versions.** If any code is accepted for a set, copy its YAML to the next
   minor version (`t2dm@1.1.0` / `sepsis_explicit@1.1.0` — the released pair is frozen), add
   the accepted codes under the right system with the same `{code, match}` shape, cite the
   ledger in `notes`, keep provenance and references, then `mwh codeset lock` (the new pairs
   only — `--check` must stay green), `mwh codeset validate <ref> --tier dev`, `mwh codeset
   compile --tier dev <ref …>` and the full tier as a background job
   (`mwh build --tier full --tag codesets --background --job codesets-full`; poll with `mwh
   jobs`). If every proposal of a set is rejected, no new version is written and the ledger
   says so — that is the "all rejected" outcome EP-46's gate accepts.
3. **Phenotype bumps.** For each set that gained a version, bump the phenotype that references
   it — `t2dm@1.0.0` → `t2dm@1.1.0` (references `t2dm@1.1.0`; the other references
   unchanged) and `sepsis_explicit@1.0.0` → `sepsis_explicit@1.1.0` — as a new phenotype
   version file (the pair is frozen; `def_hash` pins the code-set hashes), `mwh phenotype
   validate` / `lock`, the golden SQL `tests/ep/golden/<id>@1.1.0.sql`, `mwh phenotype compile
   --tier dev`, full as a background job (`--background --job phenotypes-full-1-1`), and `mwh
   phenotype summary <refs> --tier dev --report` for the new versions. The catalog views
   `mimiciv_derived.phenotype_<id>` follow the latest built version (EP-41), so EP-46 and
   later read the reviewed definitions by default; the 1.0.0 pairs stay locked and valid.
4. **Docs + decisions.** Re-render the generated blocks of `docs/methods/codesets.md` (§7
   seed sets) and `docs/methods/phenotypes.md` (§5 definitions) with `python -m
   mimicwarehouse.codesets` / `python -m mimicwarehouse.phenotypes`; record the review policy
   the rulings settle (for example "manifestation codes of combination entries are rejected
   as a rule") as a dated D-35 addendum in `DECISIONS.md`; the README state rows for
   `codesets/` and `phenotypes/` note the new versions; EP-46's gate is then satisfied by this
   brief's ☑.
5. **Tests** (`tests/ep/test_ep172.py`, `@pytest.mark.ep_172`; fixture, `dev`) — every packaged
   pair is locked (`mwh codeset lock --check`, `mwh phenotype lock --check` exit 0); each new
   code-set version is a superset of its 1.0.0 members and its `def_hash` differs; each new
   phenotype version references the new code-set pair and compiles to its committed golden
   SQL; the ledger in the brief lists every proposed code exactly once (parsed from the
   completion note: the set of codes ruled equals the set of codes proposed, per the counts in
   the pasted files); EP-40 / EP-41 / EP-42 tests unchanged; on dev, `meta.codesets` and
   `meta.phenotype_versions` carry the new pairs (through `safe_query`).

## Out of scope

- Any code set other than `t2dm` and `sepsis_explicit`; a second GEM release; the ICD
  procedure sets → later reviews, one brief each when a study needs them.
- Cohort specs over the new versions → EP-46. Re-running the EP-42 agreement cross-tab on the
  new versions → EP-53 (capstone) if it wants it.
- Changing the GEM review file format or its writer (`codesets.gem`) → only if the session
  finds a defect; record it in the completion note and hand it to EP-54.

## Verification / acceptance

- `uv run poe test -m ep_172` green on fixture and dev; `uv run --group dev mwh verify EP-172`
  green.
- `uv run mwh codeset lock --check` and `uv run mwh phenotype lock --check` exit 0; `uv run mwh
  codeset list` shows the new pairs locked (or the ledger records "all rejected" for a set).
- On dev: `uv run mwh codeset validate t2dm@1.1.0 --tier dev` and the sepsis counterpart report
  every accepted code matched in the dictionaries; `mwh phenotype summary` runs for the new
  phenotype versions; the full-tier compile jobs' ids and timings are in the completion note.
- The completion note carries the decision ledger (one row per proposed code: set, direction,
  code, title, GEM flag, ruling, reason) and the owner's confirmation that the EP-46 gate is
  satisfied.

## Parked -> final-roadmap.md

- Sibling codes the 2018 GEM does not propose but the accepted twins imply — `A39.2` /
  `A39.3` (acute / chronic meningococcemia beside the accepted `A39.4`) and `670.20`
  (puerperal sepsis, episode unspecified, beside `670.22` / `670.24`) — trigger: the next
  `sepsis_explicit` review, or a study that needs the full meningococcal / puerperal
  families; hazard: adding un-proposed codes in a GEM-review version blurs what the
  ledger vouches for. *(Mirrored into `final-roadmap.md` § 3 as v2 PHE-10 at execution,
  2026-09-16.)*

> **Completion note (2026-09-16).** Executed as briefed: the owner pasted both EP-40
> review files at the start of the session (dictionary text only), ruled on all **34**
> proposed codes through one `AskUserQuestion` each (every recommended option taken), and
> the accepted codes became `sepsis_explicit@1.1.0` + the phenotype bump; `t2dm` is the
> "all rejected" outcome. Fixture + dev in the foreground, full as two background jobs.
>
> **Decision ledger (EP-172, 2026-09-16).** One row per proposed code, in the order of
> the pasted files; public codes and titles only (the GEM's dictionary titles).
>
> | set | direction | code | title | GEM flags | ruling | reason |
> |---|---|---|---|---|---|---|
> | t2dm | icd9->icd10 | `E1310` | Other specified diabetes mellitus with ketoacidosis without coma | approximate; combination 1/1; from 25010, 25012 | reject | E13 (other specified / secondary diabetes) is a stated exclusion; the GEM target is a pre-FY2017 artefact — E11.10 / E11.11 exist and are covered by the E11 prefix |
> | t2dm | icd10->icd9 | `34989` | Other specified disorders of nervous system | approximate; combination 1/2; from E1149 | reject | manifestation half of a combination entry; no diabetes meaning alone, the 250.6x half is already a member |
> | t2dm | icd10->icd9 | `3535` | Neuralgic amyotrophy | approximate; combination 1/2; from E1144 | reject | manifestation half; not diabetes-specific, the 250.6x half is already a member |
> | t2dm | icd10->icd9 | `3559` | Mononeuritis of unspecified site | approximate; combination 1/2; from E1141 | reject | manifestation half; not diabetes-specific, the 250.6x half is already a member |
> | t2dm | icd10->icd9 | `3572` | Polyneuropathy in diabetes | approximate; combination 1/2; from E1140, E1142 | reject | diabetes-titled manifestation code never billed without 250.6x / 249.6x; type 2 admissions already captured, the rest are type 1 / secondary (excluded) |
> | t2dm | icd10->icd9 | `36100` | Retinal detachment with retinal defect, unspecified | approximate; combination 1/4; from E113541-E113549 | reject | generic retinal-detachment code, overwhelmingly non-diabetic; the 250.5x half is already a member |
> | t2dm | icd10->icd9 | `36181` | Traction detachment of retina | approximate; combination 1/3; from E113521-E113549 (12) | reject | generic traction-detachment code; the 250.5x half is already a member |
> | t2dm | icd10->icd9 | `36201` | Background diabetic retinopathy | approximate; combination 1/2; from E11311, E11319 | reject | diabetes-titled manifestation code riding on 250.5x / 249.5x; type 2 admissions already captured, the rest are excluded types |
> | t2dm | icd10->icd9 | `36202` | Proliferative diabetic retinopathy | approximate; combination 1/2; from E1135xx (24) | reject | same rule as 36201 |
> | t2dm | icd10->icd9 | `36204` | Mild nonproliferative diabetic retinopathy | approximate; combination 1/2; from E1132xx (8) | reject | same rule as 36201 |
> | t2dm | icd10->icd9 | `36205` | Moderate nonproliferative diabetic retinopathy | approximate; combination 1/2; from E1133xx (8) | reject | same rule as 36201 |
> | t2dm | icd10->icd9 | `36206` | Severe nonproliferative diabetic retinopathy | approximate; combination 1/2; from E1134xx (8) | reject | same rule as 36201 |
> | t2dm | icd10->icd9 | `36207` | Diabetic macular edema | approximate; combination 1/3; from E113x1x (17) | reject | same rule as 36201 (billed with 362.0x and 250.5x / 249.5x) |
> | t2dm | icd10->icd9 | `36641` | Diabetic cataract | approximate; combination 1/2; from E1136 | reject | diabetes-titled manifestation code riding on 250.5x / 249.5x; same rule as 36201 |
> | t2dm | icd10->icd9 | `44381` | Peripheral angiopathy in diseases classified elsewhere | approximate; combination 1/2; from E1151, E1152 | reject | manifestation code of any systemic disease; the 250.7x half is already a member |
> | t2dm | icd10->icd9 | `5238` | Other specified periodontal diseases | approximate; combination 1/2; from E11630 | reject | generic dental code; the 250.8x half is already a member |
> | t2dm | icd10->icd9 | `5363` | Gastroparesis | approximate; combination 1/2; from E1143 | reject | not diabetes-specific (idiopathic / post-surgical forms); the 250.6x half is already a member |
> | t2dm | icd10->icd9 | `7135` | Arthropathy associated with neurological disorders | approximate; combination 1/2; from E11610 | reject | neuropathic arthropathy of any cause; the 250.6x half is already a member |
> | t2dm | icd10->icd9 | `71680` | Other specified arthropathy, site unspecified | approximate; combination 1/2; from E11618 | reject | generic arthropathy code; the 250.8x half is already a member |
> | t2dm | icd10->icd9 | `7854` | Gangrene | approximate; combination 1/3; from E1152 | reject | generic gangrene code (any cause); the 250.7x half is already a member |
> | sepsis_explicit | icd9->icd10 | `A207` | Septicemic plague | exact; from 0202 | accept | exact ICD-10-CM twin of member 020.2; organism-specific septicemia is the set's intent (verified in the public ICD-10-CM reference) |
> | sepsis_explicit | icd9->icd10 | `A394` | Meningococcemia, unspecified | approximate; from 0362 | accept | ICD-10-CM twin of member 036.2 (approximate only because ICD-10 splits acute / chronic / unspecified); the un-proposed siblings A39.2 / A39.3 are recorded, not added |
> | sepsis_explicit | icd9->icd10 | `B007` | Disseminated herpesviral disease | exact; from 0545 | accept | exact ICD-10-CM twin of member 054.5 (herpetic septicemia) |
> | sepsis_explicit | icd9->icd10 | `I76` | Septic arterial embolism | exact; from 449 | accept | exact ICD-10-CM twin of member 449 |
> | sepsis_explicit | icd10->icd9 | `0031` | Salmonella septicemia | approximate; combination 1/1; from A021 | accept | ICD-9-CM twin of member A02.1; organism-specific septicemia |
> | sepsis_explicit | icd10->icd9 | `0270` | Listeriosis | approximate; combination 1/1; from A327 | reject | the whole disease, not its septicaemic form (ICD-9 has none); septicaemic cases carry 038.x |
> | sepsis_explicit | icd10->icd9 | `0271` | Erysipelothrix infection | approximate; combination 1/1; from A267 | reject | the whole disease (mostly localized skin infection), not its septicaemic form |
> | sepsis_explicit | icd10->icd9 | `09889` | Gonococcal infection of other specified sites | approximate; combination 1/1; from A5486 | reject | a site catch-all, not a septicemia code; septicaemic cases carry 038.x |
> | sepsis_explicit | icd10->icd9 | `1125` | Disseminated candidiasis | approximate; combination 1/1; from B377 | accept | ICD-9-CM twin of member B37.7 and on the Martin et al. 2003 explicit list the set cites |
> | sepsis_explicit | icd10->icd9 | `67022` | Puerperal sepsis, delivered, with mention of postpartum complication | approximate; from O85 | accept | ICD-9-CM twin of member O85 (delivered episode); 670.2x exists from FY2009 |
> | sepsis_explicit | icd10->icd9 | `67024` | Puerperal sepsis, postpartum condition or complication | approximate; from O85 | accept | ICD-9-CM twin of member O85 (postpartum episode); the un-proposed sibling 670.20 is recorded, not added |
> | sepsis_explicit | icd10->icd9 | `9093` | Late effect of complications of surgical and medical care | approximate; from T8112XS | reject | generic late-effect code; nothing sepsis-specific |
> | sepsis_explicit | icd10->icd9 | `99802` | Postoperative shock, septic | approximate; from T8112XA | accept | ICD-9-CM twin of member T81.12- (approximate only for the missing encounter axis); exists from FY2011 |
> | sepsis_explicit | icd10->icd9 | `V5889` | Other specified aftercare | approximate; from T8112XD | reject | generic aftercare V-code; nothing sepsis-specific |
>
> Totals: t2dm 0 accepted / 20 rejected / 0 deferred (**all rejected** — no
> `t2dm@1.1.0`, the phenotype `t2dm@1.0.0` is unchanged); sepsis_explicit 9 accepted /
> 5 rejected / 0 deferred. For the record, not ruled on: no-map sources 0 + 0; sources
> absent from the 2018 GEM — t2dm 30 ICD-10 codes (`E11`, `E110`-`E116` and the
> `E113x` / `E1131`-`E1137` / `E1161`-`E1164` parents: category and sub-category headers
> the prefix rule expands past, never billable), sepsis_explicit 11 (`A40`, `A41`,
> `A410`, `A415`, `A418`, `R652`, `T8112`, `T8144` headers and the FY2020 `T8144XA` /
> `T8144XD` / `T8144XS`, newer than the GEM). Review policy → D-35 addendum
> (2026-09-16); `docs/methods/codesets.md` §6 carries the prose.
>
> **Shipped.** `codesets/defs/sepsis_explicit_1_1_0.yaml` (`sepsis_explicit@1.1.0`,
> def_hash `c9aed261d647`: 1.0.0 + 5 ICD-9 + 4 ICD-10 exact codes, 31 declared; notes
> cite the ledger) + `codesets.lock.json` (21 pairs); `phenotypes/defs/
> sepsis_explicit_1_1_0.yaml` (`sepsis_explicit@1.1.0`, def_hash `034f3d6bd7e2`, the
> 1.0.0 tree over the new pair) + `phenotypes.lock.json` (5 pairs); `tests/ep/golden/
> sepsis_explicit@1.1.0.sql`; `tests/ep/test_ep172.py` (5 fixture + 1 dev tests; the
> ledger above is parsed from this note); `docs/methods/codesets.md` §6 + the re-rendered
> seed table, `docs/methods/phenotypes.md` §2 + the re-rendered cards; the D-35 addendum
> and status-index row; README state rows for `codesets/` and `phenotypes/`;
> `final-roadmap.md` PHE-10. The 1.0.0 pairs are untouched (their lock hashes are pinned
> by `test_ep172`).
>
> **Earlier test edited (churn rule — a shipped fact changed).** `test_ep40.py` only: the
> per-seed pin `version == "1.0.0" and accessed == "2026-09-06"` became membership in
> `SEED_VERSIONS` (the EP-40 pair plus `("1.1.0", "2026-09-16")`); nothing else in
> EP-40 / EP-41 / EP-42 needed a change (their registry pins were already membership
> checks since EP-42).
>
> **Dev tier (2026-09-16).** `mwh codeset validate sepsis_explicit@1.1.0 --tier dev`:
> icd9 14/14 declared matched (28 rows), icd10 17/17 (47 rows) — 75 compiled rows, 100 %.
> `mwh codeset compile --tier dev sepsis_explicit@1.1.0` (build
> `20260916T202934-dev-7f3d2b2`, run `20260916T202934Z-7defce`): `codesets.compile`
> 5,508 member rows in 2.1 s, catalog 2.9 s, build 8.7 s. `mwh phenotype compile
> sepsis_explicit@1.1.0 --tier dev` (build `20260916T202958-dev-7f3d2b2`, run
> `20260916T202958Z-e59010`; phenotype run `20260916T203002Z-554af3`, 0.1 s):
> `phenotypes.compile` 27,263 rows in 0.8 s, catalog 3.0 s, build 7.5 s. `mwh phenotype
> summary sepsis_explicit@1.1.0 --tier dev --report` (k = 11, 0 rows suppressed;
> analysis run `20260916T203020Z-b38a21`,
> `runs\20260916T203020Z-b38a21\phenotype_prevalence.md`): **27,263 admissions, 1,174
> positive, 4.3 %** (1.0.0 at EP-42: 1,158, 4.2 % — the nine codes add 16 admissions on
> dev); by era 3.6 / 4.1 / 4.5 / 4.9 / 7.1 % over 2008–2010 … 2020–2022.
>
> **Full tier (2026-09-16).** Two background jobs, one after the other (the build lock is
> per warehouse, not per tier). `mwh build --tier full --tag codesets --background --job
> codesets-full` (job `codesets-full`, log `runs\jobs\codesets-full.log`; build
> `20260916T203019-full-7f3d2b2`, run `20260916T203019Z-2e0d09`; **68 s** end to end,
> 20:30:17 → 20:31:25 UTC): `codesets.gem` 281,071 rows in 56.3 s (re-read, as at
> EP-40), `codesets.compile` 5,508 member rows in 2.9 s, catalog 2.9 s. `mwh phenotype
> compile sepsis_explicit@1.1.0 --tier full --background --job phenotypes-full-1-1` (job
> `phenotypes-full-1-1`, log `runs\jobs\phenotypes-full-1-1.log`; build
> `20260916T203427-full-7f3d2b2`, run `20260916T203429Z-836490`; phenotype run
> `20260916T203433Z-28a895`, 0.26 s; **10 s** end to end, 20:34:27 → 20:34:37 UTC):
> `phenotypes.compile` 546,028 rows in 0.9 s, catalog 2.9 s. `mwh phenotype summary
> sepsis_explicit@1.1.0 --tier full --report` (k = 11, 0 rows suppressed; analysis run
> `20260916T203829Z-e43885`, `runs\20260916T203829Z-e43885\phenotype_prevalence.md`):
>
> | phenotype | unit | n | positive | share |
> |---|---|---|---|---|
> | `sepsis_explicit@1.1.0` | admission | 546,028 | 22,701 | 4.2 % |
> | `sepsis_explicit@1.0.0` (EP-42, for comparison) | admission | 546,028 | 22,533 | 4.1 % |
>
> By era: 3.2 / 3.9 / 4.8 / 5.3 / 6.6 % over 2008–2010 … 2020–2022 (1.0.0: 3.2 / 3.9 /
> 4.8 / 5.3 / 6.5 %) — the nine accepted codes add **168 admissions** on full (+0.7 % of
> the positives), most of them in the ICD-10 era. No cell below 11 anywhere. The full
> catalog's `mimiciv_derived.phenotype_sepsis_explicit` now reads 1.1.0; the 1.0.0
> parquet and `meta.phenotype_versions` row stay beside it.
>
> **Gates.** `uv run poe test -m ep_172`: 5 passed (fixture); `--tier dev`: 6 passed;
> `uv run mwh verify EP-172`: 5 passed; `mwh codeset lock --check` 21 locked / 0
> unlocked and `mwh phenotype lock --check` 5 / 0 (exit 0); `mwh codeset list` shows
> `sepsis_explicit@1.1.0` locked. Earlier briefs after the churn-rule edits: `mwh verify
> EP-40` 14 passed, `EP-41` 14 passed, `EP-42` 12 passed (fresh interpreters, exit 0);
> `pytest --tier dev -m "ep_40 or ep_41 or ep_42 or ep_172"` 21 + 26 + 13 passed across
> the three runs. `poe check` green — ruff check, `ruff format --check` (172 files),
> pyright (0 errors) and the full fixture suite **1,045 passed, 48 deselected** (the dev
> / full / demo probes), 513 s; `mwh guard` clean over the 14 changed files (paths
> mode); `poe roadmap-check --strict` 0 errors, 0 warnings (173 rows, 54 done before
> this ☑). The GEM review file format and its writer (`codesets.gem`) needed no change —
> nothing to hand to EP-54.
>
> **Earlier tests edited (churn rule — a shipped fact changed: a second packaged version
> and the session view that follows it).** `test_ep40.py` (the per-seed version /
> accessed pin → `SEED_VERSIONS` membership) and `test_ep42.py` (the session-lake
> `latest_versions` pin reads the registry's latest per id instead of a literal 1.0.0;
> `meta.phenotype_versions` rows keyed by `id@version` so both `sepsis_explicit`
> rows are checked; the dev / full probe summarises the *latest built* version of each
> id, because `summary` refuses a version that is no longer the tier's session view).
> `test_ep41.py` untouched.
>
> **Owner decisions at the interactive review (2026-09-16, every recommended option
> taken).** (1) **EP-46 gate confirmed satisfied**: cohort specs seed over `t2dm@1.0.0`
> (all rejected) and `sepsis_explicit@1.1.0`. (2) Commit in the standard two steps, no
> push (the owner pushes) — done, hashes in `README.md`. (3) Accept the churn-rule edits
> to `test_ep40.py` / `test_ep42.py` (rejected: an isolated 1.0.0-only registry fixture
> for those modules). (4) Keep the un-proposed siblings `A39.2` / `A39.3` / `670.20`
> parked as PHE-10 (rejected: folding them into 1.1.0 outside the ledger).
