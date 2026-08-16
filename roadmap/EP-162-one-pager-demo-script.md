# EP-162 — Executive one-pager + demo script + screenshots

**Size:** M · **Tier:** demo · **Core/Stretch:** core · **Depends on:** EP-161 (Case studies compilation (3–5)), EP-60 (Screenshot tooling) · **Blocks:** EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

The showcase step (D-1 portfolio, D-12): a one-page executive summary, a rehearsed live-demo script and
a consistent screenshot set. It reuses the executive-summary template (EP-132) rendered through the
report engine (EP-130) and Typst PDF (EP-131), the screenshot tooling (EP-60: page walk, fixed viewport,
EP-5 theme — D-11), demo mode (EP-159) and the compiled case studies (EP-161). Tier `demo`: every
screenshot is taken in demo mode, because screenshots of row-level views are allowed only from
demo/fixture (GOVERNANCE §6) and each image needs a `.disclosure.json` sidecar before it enters `docs/`
or git (D-40, GOVERNANCE §3). Every number on the one-pager traces to a recorded run id or model card.

## Scope sketch (refine at re-plan)

1. **Executive one-pager `docs/one-pager.md` → `docs/one-pager.pdf`** (EP-132 template) — what the lab is
   (three sentences, both audiences), an architecture strip (Mermaid: raw → lake → catalog → derived →
   marts → app / reports), the 38-category coverage bar with links, headline numbers from recorded run
   ids (rows staged and full-build wall time from `runs/benchmarks.jsonl`; signature #1 AUROC and
   calibration slope with CIs from its model card, EP-110; count of frozen protocols), governance in five
   bullets, links to docs site and case studies. Exactly one page.
2. **Demo script `docs/demo-script.md`** — a 10–12 minute walkthrough in demo mode
   (`uv run --group ui mwh app --tier demo`): Catalog & QC → Cohort Builder (the tracer cohort spec) +
   attrition → Phenotype Studio (sepsis-3 / KDIGO) → Explorer linked brush → Timeline (row view, demo
   only) → Protocol Freezer (freeze; show `mwh protocol run` refusing a modified protocol) → Models
   (signature #1 card, calibration) → Runs & Provenance → Reports / export gallery → Disclosure-review tool
   refusing an unsuppressed table. Per step: page, action, talking point, expected screen, fallback.
   Rehearsed twice; timings recorded.
3. **Screenshots** — 10–14 PNGs at `docs/screenshots/<NN>-<page>-<state>.png` via the EP-60 tooling in
   demo mode (fixed viewport, light theme; dark variants for two or three hero shots), each passed through
   `uv run --group dev mwh disclose check` → sidecar; the root README gallery placeholder (EP-157) and the
   docs-site gallery (EP-160) are filled from this folder.
4. **Tests `tests/ep/test_ep162.py`** (`@pytest.mark.ep_162`) — every PNG under `docs/screenshots/` has a
   sidecar whose recorded tier is demo or fixture; `docs/one-pager.pdf` exists and has one page; every
   link in the one-pager and demo script resolves.

## Out of scope

- Docs site build → EP-160; case-study content → EP-161; release, tag, history sweep → EP-163.
- Animated GIF / screen recording / slide deck → parked.

## Verification / acceptance (sketch)

- Artifacts exist at the exact paths above; `uv run poe test -m ep_162` and `uv run --group dev mwh verify
  EP-162` green.
- `mwh disclose check` passes on `docs/screenshots/` and `docs/one-pager.pdf`; every screenshot has a
  sidecar; none taken on dev/full.
- One-pager is one page; its numbers cite run ids / model-card ids in a footer.
- Demo script rehearsed end to end in demo mode; per-step and total timings in the completion note.

## Parked → final-roadmap.md

- Animated GIF of the Explorer linked brush and a narrated screen recording (trigger: first external
  demo request) · slide deck derived from the one-pager.
