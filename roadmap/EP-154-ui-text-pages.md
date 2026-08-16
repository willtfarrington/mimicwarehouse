# EP-154 — Text pages in app (search only)

**Size:** S · **Tier:** fixture · **Core/Stretch:** stretch · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-149 (Note search + sectioning) · **Blocks:** EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

The only UI the text track gets in v1 is a **search-only** page (DESIGN §16; category 27 mandates
no more). It renders the aggregate `SearchSummary` of EP-149 inside the app shell (EP-57) with the
shell's small-cell badges and owner row-view gate (EP-58, D-32, D-33). Note ids and section text
appear only for the owner behind the row-view toggle, audited and never exportable (GOVERNANCE §4,
§6, §9). The page is developed and screenshotted on the fixture notes only — the ODbL Demo has no
note module, so the page is unavailable in demo mode and says so. `ui` dependency group.

## Scope sketch (refine at re-plan)

1. **Page** (`app/pages/text_search.py`) — reachable only when the app was launched as
   `uv run --group ui mwh app --with-notes` by the owner actor; otherwise shows a "notes not
   attached" notice with the search box disabled. Query box + `note_type` / section / cohort
   filters → `text.search.search_notes` → counts by `note_type`, section and `anchor_year_group`
   as `viz/` charts with EP-58 small-cell badges; tier badge; latency badge.
2. **Owner-gated hit list** — behind the EP-58 row-view toggle: note keys, `note_type`, relative
   time; selecting a hit shows the matched section (from `text.search.owner_hits` + section
   offsets) in a read-only panel under the row-view banner; each view writes an audit line (EP-30);
   no download, copy or export control exists on the page.
3. **Tests** (`tests/ep/test_ep154.py`, Streamlit `AppTest` on the fixture notes) — agent actor:
   no ids or text in the rendered element tree; owner + toggle: an audit line is written; no export
   widget; "notes not attached" state renders when launched without `--with-notes`.
4. **Screenshot** — fixture tier via the EP-60 tooling → `docs/screenshots/text-search-fixture.png`
   after `uv run --group dev mwh disclose check` (fixture screenshots are permitted; dev/full row
   views are never screenshotted).

## Out of scope

- Concept-highlighting note viewer / annotation UI, semantic search, topic browsing → Parked.
- Text-track narrative and full-tier numbers → EP-155 (Capstone #8).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_154` green on fixture; `uv run --group dev mwh verify EP-154` green.
- Observable behaviour on the fixture notes as in items 1–3; one full-tier search latency (owner,
  local) recorded in the completion note (≤ 5 s target, D-28).
- Screenshot exists at the named path with a `.disclosure.json` sidecar.

## Parked → final-roadmap.md

- Owner-only concept-highlighting note viewer / annotation UI — trigger: EP-150's optional
  owner-labelling step outgrows a marimo notebook.
