# EP-157 — Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-136 (Re-plan P8 (writes full P9, re-charters P10/P11)) · **Blocks:** EP-160 (Docs site (MkDocs Material)), EP-161 (Case studies compilation (3–5)), EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

The governing documents — root `README.md`, `mimicwarehouse/README.md`, `DESIGN.md`, `GOVERNANCE.md`,
`DECISIONS.md`, `source material/README.md`, `CLAUDE.md` — were written on 2026-08-16 when nothing
existed as code; by P11 they describe the shipped system only through appended dated notes and
addenda. This is the docs half of democratization (D-12): reconcile the documents against the code
and the run ledger, and give the root README the **two reading paths** the owner asked for (D-1:
DS/ML hiring managers and clinical-informatics readers). Docs-only (tier `n/a`): no data access; every
number quoted comes from a recorded run id and already carries a `.disclosure.json` sidecar (D-40),
PhysioNet's public dataset scale excepted. CLAUDE.md §5/§6 apply: DESIGN/DECISIONS get dated notes and
addenda, never rewrites; GOVERNANCE.md and CLAUDE.md changes are a proposed diff, applied only with the
owner's explicit approval.

## Scope sketch (refine at re-plan)

1. **Root `README.md`** — replace "Planning complete; no code yet" with the v1.0.0-rc state; two reading
   paths as numbered link lists: *DS/ML path* (capability coverage table → model-ready datasets EP-102/103
   → signature workflows EP-110/111/112 + model cards → leakage/drift audit EP-119 → benchmark note EP-32
   → docs site) and *clinical-informatics path* (GOVERNANCE → cohort/phenotype specs EP-46/EP-41 →
   attrition diagrams EP-48 → concepts/QC case study EP-53 → protocol freeze EP-51/EP-128 → disclosure
   review EP-133); quick start via `mwh init` (EP-158) and demo mode (EP-159); the EP-5 banner; a
   screenshot-gallery placeholder EP-162 fills; a licence line saying MIT `LICENSE` lands in EP-163 (D-34).
2. **`mimicwarehouse/README.md`** — the "planned" quick start becomes real; every command is run once in
   this session (`uv sync --group dev`, `uv run --group dev mwh doctor`, `uv run --group dev mwh paths`,
   `uv run --group ui mwh app --tier demo`, `uv run --group dev mwh verify EP-<n>`); layout tree
   regenerated from the actual tree; tier table checked against DESIGN §4.
3. **`DESIGN.md`** — dated `## State at v1.0.0 (<date>)` section: every §15 module line is a real path
   (or marked *parked → final-roadmap.md*) with the right EP tag; changed design facts summarised with
   pointers to earlier dated notes; §21 open questions closed with `~~…~~ **Resolved by EP-n (date)**`
   or carried into `final-roadmap.md`.
4. **`DECISIONS.md`** — one addendum per "Defaults assumed" item (adopted / changed / vetoed, citing the
   EP); numbering continuity of re-plan-added decisions checked; dated `## Status of decisions at
   v1.0.0` pointer block. Nothing rewritten.
5. **`GOVERNANCE.md` + `CLAUDE.md` (proposed diff only)** — every command/EP reference (EP-4 guard, EP-30
   `safe_query`/`mwh sql`, EP-43 `mwh disclose check`, EP-52 `mwh backup`, EP-133, EP-134, EP-148
   `--with-notes`, EP-163 sweep) resolves to shipped code with its exact CLI name; §1 owner-record
   placeholders listed for the owner; diff applied by the owner or with recorded approval.
6. **Reference check** — a `mwh verify EP-157` step in `src/mimicwarehouse/verify.py` resolving every
   relative link and `EP-n` reference in the seven documents against the tree and `roadmap/`, plus every
   DESIGN §15 module path; `tests/ep/test_ep157.py` (`@pytest.mark.ep_157`) runs it on the repo tree
   only — no tier catalog is opened.

## Out of scope

- Building/serving the docs site → EP-160 (Docs site).
- Selecting, re-verifying and PDF-ing case studies → EP-161; screenshots and the one-pager → EP-162.
- `LICENSE`, `CHANGELOG.md`, `final-roadmap.md` compilation, final retro → EP-163.
- Changing any governance *rule* — only the owner changes GOVERNANCE.md; this brief reconciles references.

## Verification / acceptance (sketch)

- The refreshed documents exist; `uv run --group dev mwh verify EP-157` and `uv run poe test -m ep_157`
  are green (repo tree only, no tier).
- Root README shows both reading paths, each with ≥ 5 links that resolve to existing files.
- DESIGN §21 has no open item without a strike-through or a `final-roadmap.md` pointer; every §15 module
  line is a real path or marked parked.
- Every number quoted in the READMEs traces to a run id (`uv run --group dev mwh runs show <run_id>`) and
  a sidecar under `docs/`; no new aggregate is computed here.
- GOVERNANCE/CLAUDE diff reviewed by the owner; approval or deferral recorded in the completion note.
- Commit pair `feat(mimicwarehouse): docs refresh, two reading paths (EP-157)` /
  `docs(roadmap): record EP-157 commit hash`.

## Parked → final-roadmap.md

- Automated DESIGN §15 ↔ code drift check as a `mwh guard` pre-commit step (trigger: first post-v1.0.0
  module added without a design note).
