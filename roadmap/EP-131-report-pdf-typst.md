# EP-131 — Report engine B: PDF via Typst + export finalization

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-130 (Report engine A: Jinja2 → MD/HTML) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 33. D-23 fixes PDF via Typst; GOVERNANCE §7 fixes promotion: anything leaving `runs/` for
`reports/`, `docs/` or git passes `mwh disclose check` and carries a `.disclosure.json` sidecar
(D-40). This brief adds the third format to the EP-130 engine and the single export/promotion
command that EP-133 reviews and EP-134's gallery lists. Typst arrives as the PyPI `typst` package
(typst-py, Apache-2.0, cp313 win_amd64 wheel, permissive per D-34) with its bundled fonts — no
system-level install (CLAUDE.md §6) and no network at render time. If the wheel fails to resolve on
this machine, the P8 re-plan may allocate the optional toolchain-remediation slot rather than fall
back to browser printing.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/report/typst.py`** — `Report → .typ` via `report/templates/base.typ.j2`
   (title block with claim-type badge and `RETROSPECTIVE_STATEMENT`, sections, post-suppression
   tables as Typst `table`, figures as PNG from `FigureBlock.png` (EP-59's Vega → PNG path),
   provenance footer on every page); `typst.compile(src, output=pdf, root=out_dir)`; deterministic
   given the injected clock; the sidecar records the embedded font list and the typst-py version.
2. **`build_report(..., formats=("md", "html", "pdf"))`** — the PDF's `disclose.check` scans the
   `.typ` source and the `Report` object (the binary is not scanned; sidecar field
   `checked_via: source`).
3. **Export finalization `report/export.py`** — `mwh report export <run_id|report_dir> --slug <slug>
   [--formats md,html,pdf]`: render, check, write sidecars, copy the bundle to `reports/<slug>/`
   under the uv project root (the GOVERNANCE §7 target; the re-plan pins whether it is committed
   or mirrored under `docs/analyses/`) with `bundle.json` (files, sha256, run ids, claim type,
   formats, `review: pending`); write an "export attempt" audit line via the EP-30 writer
   (GOVERNANCE §8); refuse with non-zero exit and copy nothing if any check fails; idempotent
   (re-export rewrites only files whose hash changed and preserves EP-133's `review` state).
4. **Conventions** — `mwh report demo` (EP-130) gains `--pdf`; `docs/analyses/README.md`
   (EP-32) gains a "Report bundle: `reports/<slug>/` (formats, sidecar hashes)" line in its
   Reproduction-block template.
5. **Tests `tests/ep/test_ep131.py`** (`@pytest.mark.ep_131`, fixture): PDF exists and starts with
   `%PDF-`; the `.typ` source contains the claim type and retrospective statement; export refuses a
   crafted synthetic bundle whose table has an identifier column and copies nothing; one sidecar
   per file; exporting twice yields identical hashes; no socket is opened during compile
   (monkeypatched `socket.socket`).

## Out of scope

- Approve / deny ledger and review UI → EP-133; gallery and downloads → EP-134.
- Card / methods / executive templates → EP-132; docs site (MkDocs) → EP-160.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_131` and `uv run --group dev mwh verify EP-131` green on fixture;
  `uv run --group dev mwh report demo --pdf --out %MWH_DATA_ROOT%\runs\demo-report` yields
  MD + HTML + PDF with three sidecars, all passing `mwh disclose check`.
- Governance: the crafted identifier-column bundle is refused by `mwh report export` (exit ≠ 0,
  `reports/<slug>/` absent).
- Wall time of one PDF compile recorded in the completion note (expected well under 10 s).

## Parked → final-roadmap.md

- Typst / Quarto templates for long-form narrative case studies (v2 REP-1); DOCX (v2 REP-2).
