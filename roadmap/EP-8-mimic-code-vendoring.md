# EP-8 — mimic-code vendoring

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)) · **Blocks:** EP-9 (Schema registry (YAML contract)), EP-16 (Re-plan P1), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) The build backend is **hatchling only** (no setuptools) — item 3 reworded: hatchling ships every
> non-Python file under `src/mimicwarehouse/` by default but honours `.gitignore`, so no include rule
> is needed and nothing gitignored can ever ship. (2) `mwh guard` G1 knows `.csv` and `.csv.gz`, **not
> bare `.gz`** — the vendoring script's own refusal is the only enforcement for `.gz`/binary; item 2
> and test 5 say so. (3) G4 flags only *isolated 8-digit tokens starting 1/2/3* (`10 000 000–39 999 999`);
> 9-digit counts (`chartevents`, `labevents`) and 8-digit counts starting 4–9 pass — item 2's
> `validate.sql` sentence now says "the lines `mwh guard` flags", not "tables in the tens of millions".
> (4) The repo-root `.pre-commit-config.yaml` (EP-4) runs `end-of-file-fixer` and `trailing-whitespace`
> on every staged file with no `exclude:`, which would rewrite upstream SQL and break `sha256_lf` /
> "byte-identical" — item 2 now adds an `exclude:` for the vendor tree to those two hooks. (5) Endpoint
> security (roadmap Risk 12, D-42): `%TEMP%` is **not** on the Malwarebytes allow list; if the clone or
> the copy dies mid-way, check Malwarebytes Quarantine / `mbamservice.log` before anything else. (6) EP-164
> (`mwh doctor antivirus`) now runs before this brief; `poe vendor-mimic-code` is a new task outside
> `poe check`. Command forms: `uv run mwh …` ≡ `uv run --group dev mwh …` (`default-groups = ["dev"]`).

## Context

MIT-LCP/mimic-code (MIT) is the backbone the warehouse reuses instead of re-deriving (**D-19**):
its Postgres `create.sql` is the typed DDL that EP-9 transcribes into the YAML schema contract, its
`validate.sql` row counts are what EP-10 reconciles the raw CSVs against (**D-26**), and its
`concepts_duckdb/` scripts (~65 sqlglot-transpiled concepts: SOFA, sepsis-3/suspicion of infection,
KDIGO, ventilation, vasoactives, Charlson, severity scores, first-day panels) are what EP-37 executes
into `mimiciv_derived`. Nothing of it exists in the repo yet; EP-1 has produced the uv project
(`mimicwarehouse/pyproject.toml`, `src/mimicwarehouse/`) and nothing else under `src/`. This brief pins
one upstream commit, copies an explicit allow-list of files into the package, and records the pin so
every later run manifest can cite it (GOVERNANCE §10, §12). Known upstream caveats to record, not fix:
`concepts_duckdb/` is auto-generated and may lag `concepts/` (open regeneration PR #2157 as of Aug 2026;
open concept-logic PRs on the SIRS wbc guard, lab `valueuom`, Charlson, APS-III), its README targets
DuckDB 1.4 LTS while we pin 1.5.x, and no ED or Note concepts exist upstream. No MIMIC data is touched.

## In scope

1. **Pin the upstream commit** — `git clone --filter=blob:none https://github.com/MIT-LCP/mimic-code.git "$env:TEMP\mimic-code"`
   (a scratch dir on C:, outside the repo, never on G:/D:; leave the clone in place — EP-9 reuses it;
   `%TEMP%` is not on the Malwarebytes allow list, so a clone/copy that dies mid-way is checked against
   Malwarebytes Quarantine / `mbamservice.log` first — Risk 12, D-42; amended EP-7),
   record `git rev-parse HEAD` of the default branch on the day (or a sha the owner names) as the pin,
   plus the commit date and the upstream `mimic-iv/CHANGELOG`/`validate.sql` header's MIMIC-IV version.
   If the pinned `validate.sql` does not target MIMIC-IV **3.1**, walk back/forward to the nearest commit
   whose `validate.sql` does and pin that one instead — say so in `VENDOR.json`.
2. **Vendoring script** (`src/mimicwarehouse/concepts/vendoring.py`, runnable as
   `uv run --group dev python -m mimicwarehouse.concepts.vendoring --sha <sha> --src "$env:TEMP\mimic-code"`,
   plus a `poe vendor-mimic-code` task) that copies the allow-list below into
   `src/mimicwarehouse/concepts/vendor/mimic-code/`, **preserving upstream relative paths** (so EP-38 patches
   are upstream-relative diffs), normalising line endings to LF, refusing any `.csv`/`.gz`/binary file (the
   script's own check — `mwh guard` G1 refuses `.csv`/`.csv.gz` but not bare `.gz`; amended EP-7), and
   writing `src/mimicwarehouse/concepts/vendor/VENDOR.json`:
   `{upstream_url, upstream_commit, commit_date, vendored_on, mimic_iv_version_targeted, duckdb_version_upstream_readme,
   files: [{path, sha256_lf, bytes}], known_upstream_issues: [...], excluded: [...]}`.
   Allow-list: `LICENSE` (root); `mimic-iv/buildmimic/postgres/{create,load,constraint,index,validate}.sql`;
   the `mimic-iv/buildmimic/duckdb/` build script (loader precedent for EP-17: COPY options, resumable progress
   table); `mimic-iv-ed/buildmimic/postgres/{create,load,index,validate}.sql` where present;
   `mimic-iv-note/buildmimic/postgres/create.sql` (+ `load.sql`); `mimic-iv/concepts_duckdb/**/*.sql` (executed by
   EP-37); `mimic-iv/concepts/**/*.sql` (BigQuery source, reference only — EP-38 ports fixes from it). Excluded and
   listed under `excluded` with their upstream URLs: every README/notebook (they may contain demo ids), `concepts_postgres/`,
   `mimic-iii/`, and every `concept_map/*.csv` (`.gitignore`/`mwh guard` refuse `*.csv` outside `tests/fixtures/`;
   EP-138 fetches them into `ext/` at the same sha). **Guard interplay (EP-4 rule G4):** some `validate.sql` row
   counts are isolated 8-digit integers starting 1, 2 or 3 — exactly what G4 flags (9-digit counts and 8-digit
   counts starting 4–9 pass; amended EP-7); the guard is never weakened — instead the script appends the pragma
   ` -- mwh-guard: allow (row count, not an id)` to each line `mwh guard` flags (the pragma exempts the whole
   line), records those files under `local_edits: [{path, upstream_sha256_lf, sha256_lf, reason}]`, and every
   other vendored file stays byte-identical to upstream (LF aside). **Pre-commit interplay (amended EP-7):**
   add `exclude: ^mimicwarehouse/src/mimicwarehouse/concepts/vendor/` to the `end-of-file-fixer` and
   `trailing-whitespace` hooks in the repo-root `.pre-commit-config.yaml` (they would otherwise rewrite
   upstream SQL at commit time and invalidate `sha256_lf`); `mwh guard`, `check-added-large-files` and
   `detect-private-key` keep running over the vendor tree.
3. **Package plumbing** — `src/mimicwarehouse/concepts/__init__.py` exposing `vendor_info() -> VendorInfo`
   (pydantic: sha, date, file count, path to the vendor root via `importlib.resources`) and
   `vendored_path(rel) -> Path`; make sure the vendored `.sql`/`.sh`/`.json`/`LICENSE` ship as package data — the
   backend is **hatchling** (`[tool.hatch.build.targets.wheel] packages = ["src/mimicwarehouse"]`), which includes
   every non-Python file under the package by default but honours `.gitignore`, so no include rule is expected;
   verify with `uv build` + a wheel listing rather than adding one (amended EP-7) — so `uv run` and tests see them
   from the installed package, not only the source tree.
4. **Attribution** — repository-root `NOTICE` (beside `CLAUDE.md`): "This product includes software developed by the
   MIT Laboratory for Computational Physiology (MIT-LCP/mimic-code, MIT License, commit <sha>)", the upstream
   copyright line, and the rule that vendored SQL keeps its upstream header (GOVERNANCE §10). Cite the mimic-code
   paper (Johnson et al., *J Am Med Inform Assoc* 2018, doi:10.1093/jamia/ocx084) in `NOTICE` and in
   `docs/resources/repos.md` if EP-13 has already created it (otherwise EP-13 picks the entry up).
5. **Tests** (`tests/ep/test_ep08.py`, `@pytest.mark.ep_8`): `VENDOR.json` parses and its `upstream_commit` is a
   40-hex sha; every listed file exists and its LF-normalised sha256 matches; no file under `vendor/` has a
   `.csv`/`.gz` suffix (this test, not the guard, is the `.gz` enforcement — amended EP-7); the two fixer hooks
   carry the vendor-tree `exclude:` (parse `.pre-commit-config.yaml`); the hosp/icu `create.sql` contains
   `CREATE TABLE` for all 22 hosp + 9 icu tables (list the
   31 names in the test — they are schema, not data); `vendor_info()` works from the installed package; every
   vendored concept `.sql` still starts with its upstream header comment; for each `local_edits` entry the file
   differs from upstream only by trailing `-- mwh-guard: allow` comments; `mwh guard` over the vendored tree is clean.
6. **Docs** — dated note in `DESIGN.md` §8/§15 ("mimic-code vendored at <sha> on <date> under
   `concepts/vendor/`; `poe vendor-mimic-code` re-vendors"), and a **D-19** addendum in `DECISIONS.md` recording the sha.

## Out of scope

- Transcribing `create.sql` into YAML → EP-9 (Schema registry).
- Executing or patching any concept, DuckDB 1.5 compatibility fixes → EP-37 / EP-38.
- Fetching `concept_map/*.csv` or any vocabulary → EP-138 / EP-14 / EP-40.
- Adding the MIT `LICENSE` for our own code → EP-163 (NOTICE only here).

## Verification / acceptance

- `uv run poe test -m ep_8` and `uv run --group dev mwh verify EP-8` green on fixture.
- `git status` shows only files under `src/mimicwarehouse/concepts/`, `NOTICE`, `pyproject.toml`, `DESIGN.md`,
  `DECISIONS.md`, `roadmap/`, plus the repo-root `.pre-commit-config.yaml` (fixer-hook `exclude:` for the vendor
  tree; amended EP-7); the pre-commit `mwh guard` (EP-4) passes on the staged tree (no `.csv` vendored) and
  `pre-commit run --all-files` leaves the vendor tree unmodified.
- `uv run --group dev python -c "from mimicwarehouse.concepts import vendor_info; print(vendor_info().sha)"` prints
  the pinned sha; re-running the vendoring script with the same sha is a no-op (`git diff --stat` empty).
- Commit `feat(mimicwarehouse): vendor mimic-code at <sha> (EP-8)`, then tick ☑ in `roadmap/README.md` and commit
  `docs(roadmap): record EP-8 commit hash`.

## Parked → final-roadmap.md

- Re-transpiling `concepts/` → DuckDB locally with upstream `mimic_utils` (sqlglot) instead of waiting for the
  upstream regeneration bot; trigger: EP-38 finds `concepts_duckdb/` lagging a fix we need.
