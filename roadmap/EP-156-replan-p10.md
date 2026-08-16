# EP-156 — Re-plan P10

**Size:** S · **Tier:** n/a · **Core/Stretch:** stretch · **Depends on:** EP-155 (Capstone #8) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Re-plan EP closing the optional text track (D-8): retro, timings, decision addenda, ☑
reconciliation and mirroring of every P10 `Parked` section into `final-roadmap.md`. Unlike earlier
re-plans it writes no new phase briefs — P11's full briefs come from EP-147 — but it checks that
the P11 briefs still describe the text track truthfully after execution (or partial execution at
the core/stretch cutline). Docs-only; no data access.

## Scope sketch (refine at re-plan)

1. **Retro + timings** — what took longer than its Size (medspaCy / spaCy wheels on 3.13, torch
   cu130, DuckDB FTS memory, embedding throughput), the track benchmark table from EP-155, and
   whether the 5–15 GB notes disk budget held → `> **Completion note**` blocks where missing.
2. **Decision and design records** — `> **Addendum (date, EP-156).**` under D-3 (what the
   representative text workflow finally comprised), D-15/D-16 where wheels or GPU behaved
   differently, and a dated DESIGN.md note under §18/§21 (FTS engine chosen, measured sizes, any
   `notes-dev` decision); new numbered decisions only if the owner settled something new.
3. **Roadmap bookkeeping** — `roadmap_check.py` clean; ☑ hashes for EP-148–EP-155; strike Risk 10
   (notes track gated) as resolved or record the partial outcome; capability table row 27 lists the
   briefs actually executed and the six-part definition of done is audited for category 27.
4. **Mirror Parked items** — every `## Parked → final-roadmap.md` entry from EP-148–EP-155 into
   `final-roadmap.md` §27 (and cross-references in §3 PHE-2, §26 DL-3/DL-4, §32).
5. **P11 touchpoints** — confirm EP-157 (docs refresh) will describe the notes lake and guard,
   EP-161 includes the capstone #8 case study, EP-163 compiles the text-track Parked items; add
   `> **EP-n pickup note.**` lines to those briefs where P10's outcome changes them. Allocate an
   optional toolchain-remediation S brief only if the owner asks.

## Out of scope

- New analysis, code or data access; any full-tier run.
- Writing P11 briefs → already full from EP-147.

## Verification / acceptance (sketch)

- The named artifacts exist: addenda in `DECISIONS.md`, dated note in `DESIGN.md`, updated
  `final-roadmap.md` §27, README P10 table with ☑ hashes and Risk 10 status.
- `uv run --group dev python mimicwarehouse/scripts/roadmap_check.py` (or `uv run poe roadmap-check`)
  clean; links resolve; commit `docs(roadmap): re-plan P10 (EP-156)`.
