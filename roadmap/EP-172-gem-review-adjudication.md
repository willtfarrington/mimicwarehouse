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

- (none at allocation; the session adds any it finds)
