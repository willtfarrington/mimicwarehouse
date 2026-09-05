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
  re-plan confirms counts stable; hazard: contributor process/time. *(Mirrored into
  `final-roadmap.md` CONC-2 on 2026-09-05: the five ports follow open upstream PRs, so the
  first contribution is a review on those threads.)*

> **Completion note (2026-09-05).** Shipped: `src/mimicwarehouse/concepts/patches/`
> (`__init__.py` — `Patch` / `PatchRegistry` models, `load_registry`, `validate_patch` /
> `validate_registry`, `effective_sql`, deterministic `render_registry_yaml` +
> `refresh_shas`, `python -m mimicwarehouse.concepts.patches` via `__main__.py`;
> `patches.yaml`; five full-replacement files `sirs.sql`, `complete_blood_count.sql`,
> `inflammation.sql`, `charlson.sql`, `apsiii.sql`, each derived from the vendored body
> by applying the upstream PR's diff as exact string replacements), `concepts/runner.py`
> (`ensure_registry` once per build — refuses to start on a foreign
> `applies_to_upstream_commit`, a drifted sha or a no-op patch; `build_concept` prefers
> the patch, records the patch file's sha256 as `source_sha256` / status `sql_sha256`
> beside `vendored_sha256` and the `patch_id`; `run_concept_versions` reads `patch_id` /
> `sql_sha256` back from `status.json` and adds `params.patches` / `params.patched` to
> the run), `concepts/inventory.py` (`patched_concepts`, status cell names the patch),
> `tests/ep/test_ep38.py` (19 fixture tests + 1 dev), `docs/resources/concepts.md`
> § Deviations (+ regenerated table), DESIGN §3 / §8 / §15 notes, D-19 addendum, README
> rows, `docs/resources/README.md`, `final-roadmap.md` CONC-2, and the EP-37 brief's
> full-run verification note (item 1). **Ported** (all four upstream PRs open at
> 2026-09-05 → `status: ported-unmerged`, re-check at EP-54): `sirs-wbc-guard` (#2146),
> `cbc-mchc-valueuom` + `inflammation-crp-valueuom` (#2141 — MCHC and CRP are the only
> panels upstream touched; the brief's wider list has no upstream fix and per-itemid
> units are EP-39's), `charlson-exclude-c4a` (#2142, chosen over #2043), `apsiii-
> equidistant-arms` (#2137). **Not ported, recorded:** #2043 (Charlson C7A/C7B) and #2046
> (APS-III axillary temperature) — see concepts.md § Deviations. `KNOWN_FAILURES` stays
> empty: nothing failed on 1.5.x on any tier, so the "failed on 1.5.x / unfixable-1.5.x"
> lists are empty by measurement, not omission.
>
> **Acceptance.** `uv run poe test -m ep_38`: 19 passed on fixture; with `--tier dev
> --with-demo` the ep_37 + ep_38 set = 46 passed (the dev `count(patch_id)` acceptance
> test, the demo pin rebuild, the dev select-with-deps and dev drift tests). `uv run mwh
> verify EP-38`: exit 0. `uv run poe check`: ruff / format / pyright clean, **927 passed**
> (964 collected, 37 tier probes deselected) in 315 s. `poe roadmap-check --strict` 0/0;
> `mwh guard` clean over every changed path.
> Brief acceptance "count of `patch_id IS NOT NULL` on dev = registry size": the count is
> 5, a small cell the gate suppresses (k = 11), so it is verified as the released
> complement — `mwh sql "SELECT count(*) AS n FROM meta.concept_versions WHERE patch_id IS
> NULL" --tier dev|full` = **60** of 65 → 5 = `len(patches.yaml)`; the dev test also
> asserts the `status.json` `patch_id` set equals the registry's concepts.
>
> **Item 1 (verify the EP-37 full run):** recorded in `EP-37-concept-runner.md`'s second
> completion note (65/65 manifest lines and status entries, `meta.concept_versions` = ok
> 65, 12 min 18 s wall, concept steps 725.0 s, peak RSS 7,460 MB, 95,777,751 rows,
> 1,338.0 MB of Parquet; per-group table). Nothing to re-launch.
>
> **Item 5 (rebuild + re-pin).** Rebuild set = the five patched steps + `meta.concept_versions`
> + `catalog` (`--select …,meta.concept_versions,catalog --force`; no other concept reads
> `mchc` or `crp`, and `charlson`/`sirs`/`apsiii` have no consumers, so no dependent is
> stale). **Demo**: build `20260905T204845-demo-2be0990`, 9.7 s; `compare_pins` against
> the committed `tests/ep/pins/concepts_demo.json` = **0 differences** (every patched
> concept's row count and the Charlson mean unchanged: `complete_blood_count` 2,959,
> `inflammation` 42, `charlson` 275 / mean 4.66, `sirs` 140, `apsiii` 140) — the committed
> pin file is byte-identical before and after. **Dev**: job `concepts-dev-patched` (pid
> 6060, 2026-09-05T20:49:47Z → 20:49:59Z, 12 s; build `20260905T204949-dev-2be0990`); the
> drift detector reported **one** difference, `inflammation` rows 8,671 → 8,667 (four
> specimens whose only CRP row lacked the `mg/L` unit), and `runs\pins\concepts_dev.json`
> was rewritten to the new set (before/after recorded here; the file is never committed).
> **Full**: job `concepts-full-patched` (pid 35076, log `runs\jobs\concepts-full-patched.log`,
> 2026-09-05T20:51:36Z → 20:51:52Z — **16 s** wall; build `20260905T205137-full-2be0990`,
> provenance run `20260905T205143Z-6698f5`, `params.patched = 5`), all seven steps done,
> `kind: concept` lines written for the five: `complete_blood_count` 4,377,900 →
> **4,377,899** rows (1.9 s, peak 114 MB), `inflammation` 174,269 → **174,213** rows
> (0.4 s), `charlson` 546,028 → 546,028 (2.5 s, peak 324 MB — the job's high-water mark;
> 14.1 s on the cold first build), `apsiii` and `sirs` 94,458 → 94,458 (identical bytes
> for `sirs`). Disk delta of the five files: 114,793,375 → 113,594,304 bytes (−1.2 MB).
> `meta.concept_versions` on `full.duckdb`: 65 rows, `patch_id IS NULL` = 60.
>
> **Earlier tests touched (roadmap README rule, CMP-6):** `tests/ep/test_ep37.py`
> `test_concept_versions_has_one_row_per_attempted_concept` — its `patch_id is None for
> every row` pin was EP-37's explicit placeholder for this brief; it now asserts
> `patch_id` is set for exactly the registry's concepts. Nothing else changed; every earlier
> `mwh verify` stays green (`poe check`).
>
> **As-built choices (routine, recorded here).** (1) The registry is validated once per
> build (`ensure_registry`, keyed by build id) inside the first concept step rather than
> in `mwh build` itself, so a foreign patch refuses the build with the default
> stop-at-first-failure semantics and `--keep-going` blocks every concept — "refuses to
> start" without a new CLI hook. (2) `sql_sha256` in manifests / status / versions is the
> sha256 of the SQL that ran (the patch file's), `vendored_sha256` keeps upstream's; the
> run manifest keeps EP-37's one-`concept`-ref-per-concept shape (hash = effective sha)
> and lists patch ids in `params.patches` — a separate `concept_patch` ref kind was tried
> and dropped because it broke an EP-37 pin for no provenance gain. (3) `patch_id` in
> `meta.concept_versions` comes from `status.json` (what the file was built from), the
> registry only for a *failed* concept. (4) A patched concept is rebuilt explicitly
> (`--select … --force`); the completeness skip does not compare hashes (a design note in
> DESIGN §8 says so). (5) Patch files are generated from the vendored body + the PR's exact
> replacements (scratch script, asserting each replacement count), never transcribed; the
> upstream "AUTOMATICALLY GENERATED" header line is kept verbatim inside our header. (6)
> Two of the four ports (`sirs`, `apsiii`) are provably value-neutral on real data
> (`semantics: unchanged`) — ported for upstream parity and intent, documented so nobody
> hunts for a count effect. (7) `patches/` is a package (`__init__.py` + `__main__.py`):
> `python -m` needs the latter; hash fields are rendered quoted because an all-digit
> placeholder loaded as an int. (8) Power mode: `mwh doctor` read `Balanced · AC power
> mode: Best performance` (the D-38 overlay) at session start; timings above were taken
> under it, with the demo pin test and the dev/full jobs run sequentially on the shared
> build lock.
>
> **Owner decisions at close (session-end prompt, 2026-09-05, all as recommended):**
> two-step commit (`feat(mimicwarehouse): concept patch registry + upstream ports (EP-38)`
> then `docs(roadmap): record EP-38 commit hash`, no AI trailers) — done; **no push** by
> the session (the owner pushes); Charlson stays on **PR #2142** (C4A exclusion only;
> #2043's C7A/C7B additions remain "considered, not ported", re-checked at EP-54);
> APS-III **#2046 deferred to EP-39** (temperature-site curation), recorded in
> `concepts.md` § Deviations.
