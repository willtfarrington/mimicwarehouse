# EP-38 — Concept fixes/ports for DuckDB 1.5.x

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱) · **Blocks:** EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-53 (Capstone #1: concepts/QC case study), EP-54 (Re-plan P3), EP-55 (Latency marts A: first-day features + itemid rollups ⏱)

## Context

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
  re-plan confirms counts stable; hazard: contributor process/time.
