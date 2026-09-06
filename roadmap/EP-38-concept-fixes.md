# EP-38 — Concept fixes/ports for DuckDB 1.5.x

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱) · **Blocks:** EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-53 (Capstone #1: concepts/QC case study), EP-54 (Re-plan P3), EP-55 (Latency marts A: first-day features + itemid rollups ⏱)

> **EP-33 amendment (2026-08-31) — charter shift after the D2 smoke.** EP-33's
> pre-flight smoke executed all 65 vendored `concepts_duckdb` files cleanly on DuckDB
> 1.5.5 (zero failures; see the EP-37 amendment of this date for the method), so the
> Context's "plus whatever the 1.5.x run surfaced (function renames, integer-division/
> `date_diff` semantics, `regexp_matches` flags, epoch functions)" class is expected to
> be **empty**. This brief's charter shifts accordingly: (a) the ⏱ verification loop of
> item 1 stands unchanged; (b) the substantive work is porting the four known upstream
> concept-logic PRs (SIRS `wbc` guard, lab `valueuom` filters, Charlson, APS-III —
> Risk 2's still-open half; semantic fixes, not 1.5.x breakage) and (c) any count-pin
> mismatches item 5 surfaces that execution success alone cannot see. The patch
> mechanism (item 2) is unchanged — it exists for semantic ports either way.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) Item 1's ledger pull is
> `mwh runs benchmarks --kind concept` — the shipped EP-32 verb; `mwh runs bench` never
> existed (ledger P3C-6) — reading `dag.benchmarks.BenchmarkLine` rows (with EP-35's optional
> `run_id`/`disk_delta_mb`). (2) Charter in one sentence: the executability half of Risk 2 is
> retired by the D2 smoke (65/65 clean on DuckDB 1.5.5), so this brief's substance is the
> count-pin comparison plus the upstream concept-logic PR ports (SIRS `wbc`, lab `valueuom`,
> Charlson, APS-III) through the patch mechanism — no "failed on 1.5.x" list is expected.
> (3) Patched rebuilds obey the EP-33 stage-level rule that `--tier dev --force` never
> replaces a full-complete table (LDR-1): patched concepts are rebuilt per tier and `--tier
> full --force` is the only destructive path; `--select concept.<group>.<name>` is reachable
> through EP-37's spec discovery (no `--spec`); job peeks via `mwh jobs --job ... --tail N`.
> (4) Count-pin comparisons may use `safe_query` set operations (EP-33 B1); demo pins < 11
> stay `"<11"` until EP-43; refusals print on stderr via `console.fail`.

EP-37 ran the vendored mimic-code concepts per tier and launched the full-tier build as a
background job; it recorded, but did not fix, concepts that fail on DuckDB 1.5.x or lag upstream.
D-19 says: adopt, port fixes, count-pin, and record every local deviation as a patch with its
upstream reference. Known upstream concept-logic issues open at planning time (README Risks 2):
a SIRS `wbc` guard (null/unit handling in `sirs`), lab `valueuom` filtering in the lab panels
(`chemistry`, `complete_blood_count`, `blood_differential`, `enzyme`, `bg`), a Charlson coding fix
(`charlson`), and an APS-III fix (`apsiii`); plus whatever the 1.5.x run surfaced (function
renames, integer-division/`date_diff` semantics, `regexp_matches` flags, epoch functions). This
brief first closes the ⏱ loop (verify the EP-37 full run: log, manifests, benchmark ledger,
timing, peak RSS, disk), then ports fixes as **patches over vendored files that are never
edited**, re-pins counts, and rebuilds only the patched concepts on dev and full. Full staging is
complete (EP-28), so full-tier rebuilds here are bounded (minutes to tens of minutes) but still
run as background jobs polled from the session (foreground cap ~10 min).

## In scope

1. **Verify the EP-37 full run** — `uv run --group dev mwh jobs --job concepts-full --tail 40`
   (state + INFO lines only, no data; log at `%MWH_DATA_ROOT%\runs\jobs\concepts-full.log`),
   confirm every concept's manifest line
   and `meta.concept_versions` rows on `full.duckdb`, list failures, and pull wall/peak-RSS/disk
   from `runs.benchmarks` (`mwh runs bench --kind concept`). Append `> **Completion note
   (date).**` to `EP-37-concept-runner.md` with a table (concept group · wall s · peak RSS ·
   rows) and disk used by `lake/derived/full/concepts/`. Re-launch failed-but-fixable concepts
   only after step 3.
2. **Patch mechanism** (`src/mimicwarehouse/concepts/patches/`) — `patches.yaml` registry
   (patch_id, concept, reason, upstream_ref URL (PR/issue/commit), applies_to_upstream_commit,
   sql_sha256, date) and one `<concept>.sql` per patched concept (full replacement body with a
   header comment citing upstream, keeping mimic-code's MIT header). The runner (EP-37) prefers
   `patches/<concept>.sql` over the vendored file when the registry entry matches the vendored
   commit, records `patch_id` in `meta.concept_versions`, and refuses to start if a patch's
   `applies_to_upstream_commit` differs from EP-8's pinned commit (forces review on re-vendoring).
3. **Port the known fixes + 1.5.x breakages** — for each of: SIRS `wbc` guard; lab `valueuom`
   filters (accept only the expected unit per itemid, coordinate with EP-39's `meta.item_units`
   where it already exists — do not duplicate factors); Charlson; APS-III; every concept that
   failed in EP-37's log — read the upstream PR/issue, port the SQL, and write the patch. Where an
   upstream PR is not merged, mark `status: ported-unmerged` and re-check at the P4 re-plan.
   Deviations that change semantics (not just syntax) are listed in `docs/resources/concepts.md`
   § Deviations with the effect on demo counts (before/after).
4. **Regression tests per patch** — `tests/ep/test_ep38.py` (`@pytest.mark.ep_38`; fixture,
   `dev`, `full` opt-in): a crafted synthetic case per patch demonstrating the fix (e.g. a `sirs`
   row with null `wbc` produces no false criterion; a lab row with a wrong `valueuom` is excluded
   from `chemistry`; a Charlson case with the affected code group scores correctly; an APS-III
   input at the boundary scores per the paper); the patch registry validates (every entry has an
   upstream_ref and a matching file); the runner refuses a patch whose upstream commit mismatches.
5. **Rebuild + re-pin** — `uv run --group dev mwh build --tier demo --select <patched concept
   steps> --force` and `--tier dev`; update `tests/ep/pins/concepts_demo.json` (record before/after
   in the completion note); rebuild the patched set on full as a background job (`uv run --group
   dev mwh build --tier full --select <patched steps> --force --background --job
   concepts-full-patched`; log `%MWH_DATA_ROOT%\runs\jobs\concepts-full-patched.log`), poll with
   `mwh jobs --job concepts-full-patched` until `done`, and record wall time, peak RSS, disk delta
   and build id in this brief's completion note.
6. **Docs** — DESIGN.md §8 dated note (patch mechanism, semantics deviations); `NOTICE` unchanged
   (patches keep upstream attribution); `docs/resources/concepts.md` gains the deviations table and
   the "status on DuckDB 1.5.x" column filled for all concepts.

## Out of scope

- New concepts of our own (ED, Note, extra severity scores) → EP-142 / P10 / `final-roadmap.md`.
- Unit harmonization tables and plausibility bounds → EP-39 (this brief only reuses them).
- Marts over concepts → EP-55. Phenotypes → EP-41/42.

## Verification / acceptance

- `uv run poe test -m ep_38` green on fixture and dev; `uv run --group dev mwh verify EP-38` green.
- EP-37's brief carries a completion note with the full-run table; `SELECT count(*) FROM
  meta.concept_versions WHERE patch_id IS NOT NULL` on dev equals the number of `patches.yaml`
  entries; every previously failing concept now builds or is documented as `unfixable-1.5.x`
  with the error class.
- Full-tier rebuild of the patched concepts completed (background job, log path + run id + timing
  in this brief's completion note); `runs.benchmarks` has `kind='concept'` rows for them.
- `docs/resources/concepts.md` § Deviations lists every patch with upstream reference and count
  effect on demo.

## Parked → final-roadmap.md

- Upstreaming our patches as mimic-code PRs once semantics are validated on full — trigger: P4
  re-plan confirms counts stable; hazard: contributor process/time. *(Mirrored as
  `final-roadmap.md` CONC-2 on 2026-09-06, reworded: the five EP-38 ports are upstream's own
  open PRs, so the parked work is retiring each patch when its PR lands in a re-vendored pin;
  CONC-1 re-parked — no port needed the sqlglot regeneration.)*

> **Completion note (2026-09-06).** Second execution of this brief, written fresh from its
> text after the revert `798141c` (nothing consulted or reused from `9465fe2`); EP-37 was ☑
> `9966645` before starting; Windows power mode read *Best performance* on AC throughout.
>
> **Item 1 — the ⏱ verification.** Record-keeping, as EP-37's pickup note anticipated: the
> `concepts-full` job had finished inside EP-37's session (pid 48132, 2026-09-06T00:35:09 →
> 00:47:03 UTC, exit 0; run `20260906T003510Z-9564c0`: wall 707.0 s, peak RSS 7,479.6 MB,
> disk delta 1,427.4 MB, **65 / 65 done, 0 failed, 0 blocked**, 95,777,751 rows in
> 1,337,952,596 bytes / 65 files). Every concept carries its manifest line and per-tier
> status entry, the ledger holds 65 `kind: concept` lines (all `ok`), and
> `meta.concept_versions` on `full.duckdb` read `done 65` with `patch_id` NULL for all 65
> through `mwh sql` (audits `d244e46e…`, `d6983c82…`). The per-group table (wall · peak RSS ·
> rows · bytes from `mwh runs benchmarks --tier full --kind concept`) and the disk figures
> are appended to `EP-37-concept-runner.md`; nothing failed, so nothing was re-launched.
>
> **Item 2 — the patch mechanism (shipped).** `src/mimicwarehouse/concepts/patching.py`
> (`Patch` / `PatchRegistry` models; `validate_registry` / `check_registry`;
> `load_patch_sql`; `rebuild_steps`; `render_patch_table`; `python -m … --check |
> --select-list [--no-dependents] | --table`) over `concepts/patches/patches.yaml` and one
> full-replacement `<concept>.sql` per patch (upstream header kept: the vendored DuckDB body
> under the same `DROP TABLE …; CREATE TABLE … AS` line, a header comment citing the PR /
> issue and the MIT attribution). `concepts/runner.py` validates the registry once per build
> (`checked_registry`, cached in `ctx.state`) and refuses every concept step while an entry's
> `applies_to_upstream_commit` differs from the EP-8 pin or a file is missing / drifted /
> orphaned (a `PatchError` surfaces as the step's sanitized `ConceptError`), then resolves the
> SQL through `resolve_concept_sql` (patch first, else the vendored file); the executed SQL's
> sha256 and the `patch_id` go into the per-tier status entry (`vendored_sha256` kept
> beside), the manifest line's `source_sha256`, `meta.concept_versions` (`sql_sha256`,
> `patch_id`), the run's refs (`concept` + `concept_patch`) and the catalog view comment.
> `Inventory.dependents_of` gives the rebuild closure; `render_inventory_table` gained the
> `DuckDB 1.5.5` (`ok` for all 65) and `patch` columns; `concepts/pins.py` records the patch
> map (`patches`, a compared key), `write_or_compare(refresh=True)` re-pins with a
> before/after record, and `python -m mimicwarehouse.concepts.pins --tier <t> [--refresh]
> [--path FILE]` is its CLI.
>
> **Item 3 — the ports (all `ported-unmerged`; the PRs were open on 2026-09-06).**
> `sirs-wbc-guard-pr2146` (upstream PR #2146: `COALESCE(wbc_min, wbc_max, bands_max)` in the
> missing-data arm; intent-only on MIMIC-IV because `first_day_lab` yields `wbc_min` and
> `wbc_max` together), `complete_blood_count-mchc-unit-pr2141` and
> `inflammation-crp-unit-pr2141` (PR #2141, fixes issue #1922: MCHC 51249 kept only with
> `valueuom = 'g/dL'`, CRP 50889 only with `'mg/L'`; both change values),
> `charlson-c4a-exclusion-pr2142` (PR #2142, fixes issue #2017: the `C45`..`C58` range split
> around the ICD-10-CM extension `C4A`; C7A / C7B stay unmapped as upstream documents),
> `apsiii-equidistant-arms-pr2137` (PR #2137: the six tie arms compare `ABS(x_max - mid)`
> with `ABS(x_min - mid)`; intent-only — the tautology was only reached once the distances
> were equal). The brief's "lab `valueuom` filters for `chemistry`, `complete_blood_count`,
> `blood_differential`, `enzyme`, `bg`" turned out to be one upstream PR touching
> `complete_blood_count` and `inflammation` only; the other four panels carry a single unit
> per itemid on dev and full (rare variants below k aside; audits `23930cda…`, `7917ebee…`,
> `0c781816…`, `2fc64618…` on dev, `23f07f10…`, `cbb4e7c4…`, `740bc31b…`, `6d7e332c…` on
> full) and EP-39's brief reserves unit rules for `meta.item_units` ("EP-38 patches concept
> SQL only where an upstream fix exists"), so they are **not** patched. Evaluated and not
> ported: PR #2046 (APS III axillary + 1 °C, `mergeable_state` unknown) and PR #2043 (Charlson
> C7A / C7B, superseded by #2142) — both listed in `docs/resources/concepts.md` § Deviations
> for the P4 re-plan. No concept failed on 1.5.x, so no port of that kind exists.
>
> **Item 4 — tests.** `tests/ep/test_ep38.py` (`ep_38`): 19 fixture tests — the committed
> registry validates and each crafted violation (commit mismatch, drifted / missing / orphan
> file, unknown concept, foreign reference, wrong target, an added dependency, duplicates,
> malformed YAML, bad field values) is refused; the runner refuses to start on a commit
> mismatch and prefers a patch (status entry, manifest line, run refs,
> `meta.concept_versions`, the session fixture lake's view comments); one crafted synthetic
> case per patch on in-memory DuckDB tables with ids ≥ 90 000 000 (SIRS: a lone normal
> `wbc_max` scores 0 where the vendored SQL yields NULL; MCHC `'%'` rows dropped, CRP rows
> without `mg/L` dropped; `C4A` no longer counts as `malignant_cancer` while `C45`, `C50`,
> `C43`, ICD-9 `174` still do; APS III equidistant inputs score the larger component — 19
> points on the crafted stay — and the vendored arms give identical rows); the rebuild
> closure and `--select` list; the pins' patch map and the refresh round trip; the docs
> tables; the CLI entry points; hygiene; the import budget. Plus `tier("dev")` /
> `tier("full")` probes (the patched concepts named in `meta.concept_versions` through
> `safe_query`) and a `@pytest.mark.demo` pin comparison. Gates: `mwh verify EP-38` 19 passed
> (49 s); `pytest -m "ep_38 or ep_37" --tier full --with-demo` **42 passed** (84 s).
>
> **Item 5 — rebuild + re-pin.** The selection (`patching --select-list`) is the five patched
> concepts plus the seven that read them (`first_day_lab`, `meld`, `lods`, `sapsii`, `sofa`,
> `first_day_sofa`, `sepsis3`), then `meta.concept_versions` and `catalog` — 14 steps.
> *demo* (foreground): run `20260906T154251Z-f6f0ef`, 14 / 14 done; demo pins refreshed —
> the only difference is the new `patches` map: **no demo count moved** (`complete_blood_count`
> 2,959, `inflammation` 42, `charlson` 275 / mean 4.66, `sirs` 140, `apsiii` 140, `sepsis3`
> 62 true, the KDIGO distribution unchanged; the demo carries no `C4A` code and every demo
> CRP row is `mg/L`; the MCHC values recorded with `'%'` are NULL from now on). *dev*
> (foreground): run `20260906T154303Z-df70da`, 14 / 14 done; dev pins refreshed under
> `runs/pins/` — `patches` plus `counts.inflammation` (fewer than 11 specimens fewer: CRP
> rows without a unit); the dev fingerprints before / after are identical for `apsiii`
> (count, score sum, nulls; audits `41189cbf…` → `dcf5ef1b…`), `sirs` (`wbc_score`
> distribution; `c03a51f1…` → `5701ba7b…`) and `charlson` (`malignant_cancer` distribution;
> `52d97a03…` → `f144987e…`), and the MCHC filter nulls `mchc` on a large share of CBC
> specimens (`3d14acd3…`). *full* (background): job **`concepts-full-patched`**, pid 24288,
> 2026-09-06T15:45:29 → 15:46:01 UTC, exit 0, run **`20260906T154530Z-fca779`** (wall 25.0 s,
> peak RSS 7,081.3 MB — `sofa` —, disk delta −0.3 MB; 12 concepts, 14,016,426 rows in
> 230,857,671 bytes, summed concept wall 21.5 s, the slowest `sofa` 12.7 s), `derived/full`
> snapshot `c04b1bffca87…`; `inflammation` on full holds 174,213 rows (174,269 before — the
> 56 CRP rows without a unit), `complete_blood_count` fewer than 11 specimens fewer; the
> other ten tables are row-for-row the same size. `meta.concept_versions` on full names the
> five patch ids (audit `6a7c54e7…`) and the released complement `count(*) WHERE patch_id IS
> NULL` = 60 (`1840de11…`) — the brief's `count(*) WHERE patch_id IS NOT NULL` (= 5) sits
> below k = 11, so the acceptance check reads the listing plus the complement. The derived
> layer now holds 1,336,919,249 bytes on full (1,275.0 MB) and 62,620,619 on dev.
>
> **Item 6 — docs.** DESIGN §8 dated note (the mechanism, the ports, what was not ported,
> the measured sizes) + the §15 `concepts/` row; D-19 addendum (the four settled rules);
> `docs/resources/concepts.md` (patches bullet, full status, the generated deviations table
> between `<!-- patches:begin/end -->`, the evaluated-not-ported list, the inventory table's
> two new columns); `docs/resources/README.md` row; workspace README (module row, quick
> start); `tests/fixtures/COVERAGE.md` (the fixture's MCHC is `'%'`, so `mchc` is NULL on the
> fixture); roadmap README Risk 2 (upstream-PR half ported, lag still open); `final-roadmap.md`
> CONC-1 re-parked + CONC-2; `NOTICE` unchanged.
>
> **Earlier tests touched (churn rule, EP-168).** `tests/ep/test_ep37.py::
> test_fixture_lake_carries_every_concept` — the "`patch_id` NULL and `sql_sha256` = the
> vendored hash for every row" pin became "the registry's id and the patch file's hash for a
> patched concept, unchanged otherwise" (dated comment; plus the import line). No other
> earlier test or module changed; every earlier `mwh verify EP-k` stays green through
> `poe check`.
>
> **Judgment calls (owner review).** (1) `meta.concept_versions.sql_sha256` and the manifest's
> `source_sha256` hash the SQL that actually ran (the patch's), with `vendored_sha256` kept
> in the status entry — provenance names the executed text; (2) a patched rebuild
> re-materialises the readers of a patched concept too (`--select-list`), so the derived
> layer never mixes a new input with a stale consumer; (3) the registry carries `semantics`
> and `demo_effect` so the docs deviations table is generated, not hand-kept; (4) the
> refusal lives in the first concept step of a build (cached per build connection), not in
> a CLI pre-flight — `mwh build` needs no new option; (5) `render_inventory_table` grew two
> columns and the docs block was regenerated (test_ep37's drift test stays green); (6) the
> dev pins were refreshed in place — the before/after is this note, no `.bak`; (7) only
> upstream-backed ports (EP-39's rule) — the four unpatched panels and the two evaluated PRs
> are recorded, not silently skipped; (8) `demo_effect` is pin-level ODbL text, dev / full
> effects are described qualitatively with the audit ids (the pre-EP-43 disclosure rule);
> (9) the MCHC port is verbatim upstream although it nulls a large minority of MCHC rows —
> presented to the owner below.
>
> **Deviations from the brief text.** The lab-panel scope (item 3) narrowed to the two
> concepts upstream's PR touches (above); the acceptance `count(*) … patch_id IS NOT NULL`
> is read as the listing plus the released complement (small-cell rule); the brief's
> `lake/derived/full/concepts/` is the as-built `lake/derived/full/mimiciv_derived/`.
>
> **Owner decisions (end of session, all the recommended option).** Keep the verbatim MCHC
> port (an accept-both-units rule, if ever wanted, belongs to EP-39's harmonisation); leave
> `chemistry`, `blood_differential`, `enzyme` and `bg` unpatched for EP-39; keep PR #2046
> (APS III axillary temperature) and PR #2043 (Charlson C7A / C7B) out — re-check at the P4
> re-plan; commit as the two-step pair, no push.
>
> **Gates.** `poe check` **939 passed**, 39 deselected, 338.6 s (ruff check, ruff format
> --check, pyright clean; 920 → 939 = the 19 new fixture tests); `mwh verify EP-38` 19
> passed (49 s); `pytest -m "ep_38 or ep_37" --tier full --with-demo` 42 passed (84 s);
> `poe roadmap-check --strict` 0 errors, 0 warnings (172 rows, 46 done before this brief's
> tick); `mwh guard` clean over every touched path (one G4 hit — a compact date used as a
> crafted bad value in a test — fixed on the spot); `python -m
> mimicwarehouse.concepts.inventory --check` in sync; `patching --check` 5 patches ok. No
> dependency change; Windows power mode read *Best performance* on AC throughout.
