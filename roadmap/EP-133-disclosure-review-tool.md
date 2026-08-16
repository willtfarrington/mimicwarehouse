# EP-133 — Disclosure-review tool

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-43 (Disclosure primitives (`disclose` module)), EP-130 (Report engine A: Jinja2 → MD/HTML) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 36 governance brief. GOVERNANCE §7 names this tool as the UI over `mimicwarehouse.disclose`
(EP-43: `suppress`, `check`, sidecar; D-33, D-40) and §8 requires every export attempt to be
audited. This brief upgrades EP-43 with the checks that report bundles (EP-130/131) need and adds
the approve/deny ledger that gates promotion into `reports/`, `docs/` and git; the pre-commit guard
(EP-4) starts requiring an approved sidecar. Acceptance is governance-class: the tool must *refuse*
crafted violations, and approval is an owner act — a Claude session (actor `agent`, D-31) can
review but never approve.

## Scope sketch (refine at re-plan)

1. **`disclose.check` v2** (in `src/mimicwarehouse/disclose.py`) — scans single files and report
   bundles (MD, HTML, `.typ`, JSON/CSV/Parquet aggregate tables, Vega specs; PNGs only via their
   sidecar tier). New checks beside the existing identifier-column scan: integers inside the real
   id bands anywhere in text (bands taken from the GOVERNANCE §3 constants, never spelled as
   literals in tests or goldens); timestamps in MIMIC's shifted-year range (21xx) as a row-level
   signal; note-like free text (long text cells, note-table names); embedded data arrays above an
   aggregate ceiling or at row grain (an id-shaped column); small cells n < 11 with a
   complementary-suppression check across marginals. The result object holds per-check status and
   *locations only* — never the offending values — so it is safe to print in a session.
2. **Review ledger** — `runs/disclosure_reviews.jsonl` (append-only, D-24 style): {timestamp,
   reviewer, actor, artifact, sha256, checks summary, verdict approve/deny, reason}; sidecar
   schema v2 adds a `review` block. CLI: `mwh disclose review <path|dir>` (rich table of checks),
   `mwh disclose approve <path> --reason "…"` (only after a passing check; refuses when the
   EP-30-resolved actor is `agent`; writes the sidecar review block, the ledger line and an audit
   line), `mwh disclose deny <path> --reason "…"`.
3. **Gate wiring** — `mwh guard` (EP-4) refuses staged files under `reports/` or `docs/` that lack an
   approved sidecar or whose sha256 differs from it; `mwh report export` (EP-131) leaves bundles
   `review: pending`; the Reports page (EP-134) reads these statuses. Representative workflow
   (category 36): the signature #1 report bundle from the EP-129 full-tier run is reviewed with
   `mwh disclose review`, approved by the owner with `mwh disclose approve --reason`, and committed
   under `reports/` past `mwh guard`; the bundle path, run id and review-ledger line are recorded
   in the EP-135 completion note.
4. **Owner-only page `app/pages/disclosure_review.py`** — the EP-57 shell precedes P8 (the re-plan
   may fold this into EP-134's Reports page instead): queue of pending bundles (sidecar missing or
   pending), check results, approve/deny with reason; the agent role sees read-only status; the
   page never renders the contents of an artifact that failed a check.
5. **Tests `tests/ep/test_ep133.py`** (`@pytest.mark.ep_133`, fixture) — crafted violations refused:
   a frame with an `hadm_id` column; a table with a cell of 7; HTML embedding a 5 000-row array;
   text containing an in-band integer built arithmetically from the band constants; a 21xx
   timestamp; `approve` from actor `agent`; `approve` before a passing check; `mwh guard` on a
   staged `reports/x.md` without an approved sidecar. Positive path: a clean synthetic bundle
   passes, `approve` writes ledger + sidecar + audit lines.

## Out of scope

- The suppression algorithm itself → EP-43; rendering / export → EP-130 / EP-131.
- Gallery UI and downloads → EP-134; in-app small-cell badge → EP-58.
- PreToolUse output-scanning hook (v2 GOV-1) and differential-privacy noise (v2 DIS-1) → parked.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_133` and `uv run --group dev mwh verify EP-133` green on fixture; every
  crafted violation above is refused in a test.
- `uv run --group dev mwh disclose review <EP-130 demo bundle>` passes and
  `mwh disclose approve … --reason` (owner) writes the sidecar review block; a `git commit` of an
  unapproved `reports/` file is blocked by the guard.
- Demo-tier screenshot of the page via EP-60 tooling, itself passing `mwh disclose check`.

## Parked → final-roadmap.md

- Quasi-identifier / k-anonymity risk scoring for released aggregates — trigger: a public
  aggregate site (v2 DIS-1); PreToolUse hook (v2 GOV-1).
