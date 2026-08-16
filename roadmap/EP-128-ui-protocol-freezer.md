# EP-128 — Protocol Freezer page + amendments UI

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-57 (App shell A (Streamlit multipage)) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 37 (prospective-style inquiry over retrospective data) mandates a UI: the Protocol Freezer
is the owner-facing front of `mimicwarehouse.protocol` (EP-51), which already defines the pydantic
`Protocol` (cohort ref, exposure, outcome, covariates, windows, analysis plan, temporal holdout,
claim type), `mwh protocol freeze <yaml>` (content hash → `runs/protocols.jsonl` entry),
`mwh protocol run <hash>` (refuses unfrozen or modified protocols) and hash-linked amendments (D-25).
The page lives in the Streamlit "Lab" app shell (EP-57: pages registry, tier switcher defaulting to
dev, READ_ONLY cached connection, theme; D-21). YAML stays the source of truth (planning default:
YAML-first, pydantic JSON-schema → form); the page edits, validates, diffs, freezes and amends — it
never invents fields the CLI cannot round-trip. Because MIMIC-IV is date-shifted, every protocol the
page shows carries the "prospective-style over retrospective data" wording, and its temporal-holdout
block names `anchor_year_group` eras, never calendar dates.

## Scope sketch (refine at re-plan)

1. **Page `app/pages/protocol_freezer.py`** registered in the EP-57 pages registry ("Protocol
   Freezer"). Three panes: registry browser (reads `runs/protocols.jsonl` through the `runs.duckdb`
   views: hash, slug/version, status draft/frozen/amended/superseded, timestamp, git sha, claim
   type, linked run ids), editor, and history. Owner and agent roles both read; only the owner role
   (EP-58 gate) sees the *Freeze* / *Amend* controls.
2. **Editor** — draft YAML in the protocol directory fixed by EP-51, edited as text (monospace
   `st.text_area`) or through a pydantic-JSON-schema-driven form for the scalar fields (claim type,
   grain, eras, windows); cohort / code-set / phenotype references are pickers over the registries
   (EP-46, EP-40, EP-41). A live validation panel lists pydantic errors; *Freeze* stays disabled
   until validation passes and a claim type is set.
3. **Freeze & diff** — *Freeze* calls the same `protocol.freeze()` the CLI uses (CLI parity:
   identical hash for identical YAML), appends the registry line, writes the GOVERNANCE §8
   "protocol freeze/run" audit line through the EP-30 audit writer, and switches the entry to
   read-only. A side-by-side `difflib` diff against the previous frozen version is shown before
   freezing and stored as the amendment note.
4. **Amendments** — *Amend* clones a frozen protocol into a new draft with `amends: <previous hash>`
   and a mandatory reason; freezing it appends a new hash linked to its parent. The history pane
   renders the chain (table + Mermaid/Altair timeline the way EP-48 renders attrition) including
   which runs cite which hash. Unsealing a sealed era (EP-129) is only possible via an amendment.
5. **Run launcher (dev only)** — *Run on dev* invokes `protocol.run(hash, tier="dev")` in-process and
   links the resulting run id to the Runs page (EP-134); full-tier runs remain CLI background jobs
   (`uv run --group dev mwh protocol run <hash> --tier full`, log under
   `%MWH_DATA_ROOT%\runs\jobs\`), whose status the page reads from the ledger.
6. **Tests `tests/ep/test_ep128.py`** (`@pytest.mark.ep_128`, fixture) with
   `streamlit.testing.v1.AppTest` against a temporary data root: invalid YAML cannot be frozen; a
   frozen entry is read-only; page-freeze and CLI-freeze hashes agree; an amendment yields a new
   hash linked to its parent; the agent role sees no *Freeze* control.

## Out of scope

- Protocol schema, registry format, `mwh protocol` CLI → EP-51 (append an addendum there if the
  page needs a field).
- Sealed-era / one-look execution logic → EP-129 (Temporal holdout runner).
- Rendering a protocol into a report → EP-130 / EP-132; the Runs & Provenance browser → EP-134.
- Causal-language linter over protocol text → parked (below).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_128` and `uv run --group dev mwh verify EP-128` green on fixture; the page
  loads on dev via `uv run --group ui mwh app` and lists the tracer-bullet (EP-31) and signature #1
  (EP-110) protocols with correct status.
- Observable: freezing from the page and `uv run --group dev mwh protocol freeze <yaml>` yield the
  same hash; a hand-edited frozen file is flagged "hash mismatch" and cannot be run.
- One full-tier page-load latency recorded (registry read only; ≤ 5 s, D-28); a demo-tier
  screenshot taken with the EP-60 tooling for `docs/`, passing `mwh disclose check`.
- An audit line exists in `runs/audit.jsonl` for every freeze / amend made from the page.

## Parked → final-roadmap.md

- Causal-language linter over protocol and report text (claim type vs wording) — trigger: an
  external reader flags over-claiming; category 37.
- OpenTimestamps / signed-tag anchoring of protocol hashes (`protocol/<slug>/vN` tags) — trigger:
  pre-registration exports (v2 PRO-1).
