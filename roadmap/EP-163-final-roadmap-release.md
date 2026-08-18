# EP-163 — final-roadmap.md compilation + release v1.0.0 + final retro

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-157 (Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths)), EP-158 (Bootstrap `mwh init` + cloner smoke test on demo tier), EP-159 (Demo mode for the app), EP-160 (Docs site (MkDocs Material)), EP-161 (Case studies compilation (3–5)), EP-162 (Executive one-pager + demo script + screenshots) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

> **Amended 2026-08-18 (D-41 addendum, before EP-165).** The repo went **public early** as a
> governed work-in-progress after a first full-history guard + secrets sweep, and the MIT
> `LICENSE` now exists at the repo root. For this brief: scope item 2(c) verifies the existing
> `LICENSE` instead of adding it; 2(d) is a **re-sweep** with `mwh guard --history` (still to be
> built here); 2(h) "remote flipped to public" is already done — replace with "visibility and
> repo settings re-checked".

## Context

The last brief of v1: compile the extension roadmap, release v1.0.0 and write the final retro. Owner
decisions it implements: the repo goes public at v1.0.0 only after a **full-history guard sweep**
(D-41, GOVERNANCE §3); MIT `LICENSE` at the repo root lands here (D-34, GOVERNANCE §10) beside the
`NOTICE` for mimic-code (D-19); `final-roadmap.md` is the compiled, ordered backlog (D-37 format,
seeded 2026-08-16 and grown by every re-plan); semver tag + `CHANGELOG.md` + separate warehouse
`build_id` (DECISIONS defaults); the retro closes the loop on D-2 sizes and D-9 charter depth. Tools:
`roadmap_check.py` (EP-6), `mwh guard` (EP-4), `mwh disclose check` (EP-43), `mwh doctor` (EP-2).
Docs-only (tier `n/a`): no data access. Publishing actions (public flip, `mkdocs gh-deploy` from EP-160,
GitHub release) are owner actions; any push, force-push or history rewrite requires the owner's explicit
go (CLAUDE.md §6).

## Scope sketch (refine at re-plan)

1. **`roadmap/final-roadmap.md` compilation** — extend `roadmap_check.py` (`--parked`) to collect every
   `## Parked → final-roadmap.md` item from every `roadmap/EP-*.md`, diff against the tables, and report
   what is missing; insert missing rows under the right category with trigger / hazard / candidate v2 id;
   add stretch briefs dropped at the cutline (P10 if the EP-127 go/no-go was *no*; EP-123, EP-145) as
   parked items; order each table by trigger proximity; prepend a "v2 planning inputs" block (hours by
   phase, coverage gaps, open risks).
2. **Release checklist `docs/release-v1.0.0.md`, executed and recorded** — (a) `roadmap_check.py`: every
   core brief ☑ with hash; the 38-row capability table re-audited against the six-part definition of done
   with resolving links; (b) `uv run poe test` (all fixture markers) and `uv run --group dev mwh verify`
   for every EP green; (c) `LICENSE` (MIT) at the repo root, `NOTICE` (mimic-code + vocabularies) verified,
   `CHANGELOG.md` v1.0.0 entry, `pyproject.toml` version 1.0.0, `mwh --version`, `uv.lock` committed;
   (d) full-history sweep `uv run --group dev mwh guard --history` — iterate every blob reachable from
   every ref with the same data-shape / real-id-band / identifier-column checks as the pre-commit hook
   (add the mode to `guard.py` if EP-4 did not ship it) plus an in-house secrets regex sweep — must be
   clean; (e) `mwh disclose check` re-run over every committed aggregate/figure under `docs/**` and
   `reports/**`, sidecar hashes matching; (f) `mwh doctor` output recorded; (g) annotated tag `v1.0.0`;
   (h) owner-action list: GOVERNANCE §1 record filled, claude.ai training toggle checked, remote flipped
   to public, `mkdocs gh-deploy`, GitHub release from the CHANGELOG.
3. **Final retro** appended to `roadmap/README.md` (`## Retro v1.0.0 (<date>)`): planned vs actual hours
   per phase (completion notes, `runs/benchmarks.jsonl`), briefs added / dropped / moved across the
   cutline, Risks table strike-throughs, top lessons; `DECISIONS.md` addenda under D-2 (sizes), D-9
   (charter depth), D-18 (tiers), and a new numbered decision recording the release and naming
   `final-roadmap.md` as the v2 planning input.
4. **Public-release gate** — the public flip and any push happen only after (d) is clean **and** the
   owner says so; if the sweep finds data: stop, no push, follow GOVERNANCE §3 (`git filter-repo`, rotate
   the remote, incident in `DECISIONS.md`) with owner approval per CLAUDE.md §6.
5. **Tests `tests/ep/test_ep163.py`** (`@pytest.mark.ep_163`) — `mwh guard --history` refuses a throwaway
   temporary git repo whose history contains a crafted CSV row with an id in a real MIMIC band and passes
   on a clean one (governance-class); every Parked item from every brief appears in `final-roadmap.md`;
   version / tag / CHANGELOG consistency; `LICENSE` present and MIT.

## Out of scope

- Any new feature or analysis; the v2 planning session itself (starts from `final-roadmap.md`).
- PyPI packaging, GitHub Actions CI, Docker portability → `final-roadmap.md` (Cross-cutting).

## Verification / acceptance (sketch)

- `roadmap_check.py` clean; `uv run poe test -m ep_163` and `uv run --group dev mwh verify EP-163` green.
- `uv run --group dev mwh guard --history` clean on the real repo and its refusal test green.
- Every committed aggregate re-passes `mwh disclose check`; `final-roadmap.md` contains every parked item.
- `docs/release-v1.0.0.md` filled in; retro and DECISIONS addenda present; annotated tag `v1.0.0` on a
  green commit; owner confirmations (public flip, gh-deploy) recorded in the completion note.
- Commit pair `feat(mimicwarehouse): release v1.0.0 (EP-163)` / `docs(roadmap): record EP-163 commit hash`.

## Parked → final-roadmap.md

- Signed tags / provenance attestations for releases · Zenodo DOI for v1.0.0 · PyPI packaging + CI
  (already under Cross-cutting).
