# EP-165 — Retro A: session & repo governance hardening

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-4 (Governance enforcement: pre-commit + `mwh guard`), EP-12 (Synthetic fixture generator B (icu) + pytest tier markers) · **Blocks:** EP-13 (Repos & awesome-lists inventory), EP-16 (Re-plan P1)

> **Origin.** First of six *retro* briefs (EP-165 … EP-170) written on 2026-08-18 by the owner-directed
> retrospective review of EP-0 … EP-12 (P0 + P1a). The review itself implemented nothing; its full
> record is [`retro-2026-08-18-findings.md`](retro-2026-08-18-findings.md) (the *ledger*: 69 verified +
> 110 minor findings, each tagged with the retro brief that implements it) and the owner decisions are
> **D-43** in `../mimicwarehouse/DECISIONS.md`. Every item below cites its ledger ids; read those entries
> (evidence + verifier's corrected fix) before coding — this brief is the plan, the ledger is the proof.
> The six briefs were sized S/M deliberately (owner usage limits): finish one, commit, stop.

## Context

Everything a Claude session may or may not do is enforced by four layers (GOVERNANCE §4): prose
(GOVERNANCE, CLAUDE.md) → repo-shared `.claude/settings.json` deny rules → `mwh guard` + pre-commit →
`safe_query` (EP-30). The review found the prose layer stale (CLAUDE.md never absorbed D-42, the uv-PATH
and cp1252 realities, or the two-product endpoint stack; GOVERNANCE §2/§4 still describe Defender only)
and the deny layer to be a *prefix blocklist* that realistic two-step actions bypass (`python -c`,
`uv run python`, `cp`/`Copy-Item` to the scratchpad, nested `sh -c`, `.NET` file APIs) — verified with
data-free probes. It also found an undocumented egress path (claude.ai MCP connectors, WebFetch), a
`.gitignore` that will silently drop planned repo files, and gaps in guard G1/G4. Owner decisions
(2026-08-18, D-43): **unpark the PreToolUse hook now**; extend the deny list; add an allow list for
read-only project commands; add `PYTHONUTF8=1`; connectors = reference lookups only + deny send/write
tools; GOVERNANCE/`.gitignore` may be edited (dated addenda, no rewriting); owner will restart VS Code
after the session (uv PATH) and add the uv cache + Claude scratchpad to the Malwarebytes allow list.

Ledger ids: DOC-1, ENV-1, ENV-2, ENV-3, GOV-1, GOV-2, GOV-4, GOV-5, GOV-6, GOV-8, DOC-6, CMP-1 (+ low:
GOV-7, GOV-9…, ENV-4…, DOC-8 — see the ledger index rows tagged EP-165).

## Decisions already taken (owner, 2026-08-18 — implement, do not re-litigate)

1. `.claude/settings.json`: add `"env": {"PYTHONUTF8": "1"}`; add literal deny rules for the two-step
   leak paths; add allow rules for read-only project commands; add deny rules for connector send/write
   tools. Recorded as a D-39 addendum (an `env` block is not a permission change).
2. PreToolUse command-string hook (parked GOV-1) is **unparked** and shipped here.
3. Connector policy: public-reference lookups only (PubMed, bioRxiv, ICD-10 Codes, Context7, GitHub/DOI
   URLs via WebFetch — EP-13/15 need them); never with fixture rows, aggregates, ids, run records or note
   text in a query; write/send-capable tools denied. Google Drive connector stays off (D-29).
4. Owner actions (not repo edits): restart VS Code after this work lands (the hosting VS Code process
   predates the uv install → stale PATH); add `%LOCALAPPDATA%\uv\cache` and
   `%LOCALAPPDATA%\Temp\claude\` (Claude scratchpad) to the Malwarebytes allow list (D-38 → nine paths).

## In scope

1. **CLAUDE.md §3 "Session tooling" bullets** (DOC-1, ENV-1, ENV-3, DOC-18) — add, verbatim facts:
   (a) `uv` may be missing from the tool shells' PATH (stale process; owner restarts VS Code): Bash
   `export PATH="$LOCALAPPDATA/Microsoft/WinGet/Links:$PATH"`, PowerShell
   `$env:PATH="$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:PATH"` — needed before `uv`, `poe`,
   `pre-commit` **and `git commit`** (the hook shells out to `uv run`); (b) bare `python`/`pip` in the
   tool shells is the system CPython 3.14 — always `uv run python …`; (c) D-42 verbatim: files via
   Write/Edit, never bash heredocs, never `python -`/stdin scripts (hangs 2 min), commit messages via
   `git commit -F <scratch file>`, no burst copy/`sed -i`/delete loops over many files in scratch dirs,
   "process killed / binary vanished" → Malwarebytes quarantine + `mbamservice.log` first; (d) foreground
   `sleep` is blocked in the Bash tool; background jobs with logs; (e) console: `PYTHONUTF8=1` comes from
   `.claude/settings.json`; CLI strings still go through the shared console helper (EP-167) —
   ASCII-safe; (f) `default-groups=["dev"]` so `uv run mwh` ≡ `uv run --group dev mwh`; (g) the auto-mode
   harness text may suggest heredocs — the project rule wins. Also §1: point to the status surface EP-166
   creates (`mimicwarehouse/README.md § State of the workspace`) and to `roadmap/README.md` phase tables
   (☑ hash = shipped); §2: one connector bullet (item 3 above); §6: "sending anything through a
   connector" joins the ask-before list. Keep CLAUDE.md under ~6 KB; move detail to GOVERNANCE/DECISIONS.
2. **`.claude/settings.json`** (GOV-1, ENV-2, CMP-1; owner-authorised) — (a) `"env": {"PYTHONUTF8": "1"}`;
   (b) deny rules (probe each with EP-0's synthetic two-line `probe.csv` in the scratchpad before
   recording): `Bash(cp *source material*)`, `Bash(cp *mimicdata*)`, `Bash(mv *mimicdata*)`,
   `Bash(python* *mimicdata*)`, `Bash(python* *source material*)`, `Bash(uv run python* *mimicdata*)`,
   `Bash(uv run python* *source material*)`, `Bash(sh -c *)`, `Bash(bash -c *)`, `Bash(perl *)`,
   `Bash(node -e *)`, `Bash(diff *.csv*)`, `Bash(od *)`, `Bash(nl *.csv*)`, `PowerShell(Copy-Item *mimicdata*)`,
   `PowerShell(Copy-Item *source material*)`, `PowerShell(*ReadAllText*)`, `PowerShell(*ReadAllLines*)`,
   `PowerShell(*ReadAllBytes*)`, `PowerShell(gc *.csv*)`, `PowerShell(cat *.csv*)`, `PowerShell(type *.csv*)`,
   `PowerShell(Get-ChildItem *mimicdata*)`, `Read(//C:/**/*.csv)`, `Read(//C:/**/*.csv.gz)`,
   `Read(//C:/**/*.parquet)`, `Read(//C:/**/*.duckdb)`; connector write/send tools present in the roster:
   `mcp__claude_ai_Gmail__send_message`, `__reply`, `__forward`, `__create_draft`, `__update_draft`,
   `__trash_*`, `mcp__claude_ai_Google_Calendar__create_event`, `__update_event`, `__delete_event`,
   `__respond_to_event`, Hugging Face write tools if any; (c) allow rules (reduce prompts, read-only):
   `Bash(uv run mwh *)`, `Bash(uv run --project mimicwarehouse * mwh *)`, `Bash(uv run pytest *)`,
   `Bash(uv run poe *)`, `Bash(uv run ruff *)`, `Bash(uv run pyright*)`, `Bash(git log*)`, `Bash(git status*)`,
   `Bash(git diff*)`, `Bash(git show*)`, `Bash(git ls-files*)`, `Bash(ls *)`, and the PowerShell twins —
   deny rules keep precedence. Update the `_readme` key. Record in GOVERNANCE §4(3) and D-39.
3. **PreToolUse hook** (GOV-2) — `mimicwarehouse/scripts/claude_pretool_guard.py`, run by the allow-listed
   `.venv\Scripts\python.exe <script>` (file script reading the hook JSON from stdin — not `python -`, not
   a heredoc; verify once against Malwarebytes/Defender, D-42(4)). Matcher `Bash|PowerShell|Read|Grep|Glob`.
   Rule: **deny** when the command / file_path matches
   `(?i)mimicdata|source material[/\\](?!README\.md)|\.csv(\.gz)?\b|\.parquet\b|\.duckdb` **and** the
   command does not start with an allow-list prefix (`uv run mwh`, `uv run --project mimicwarehouse * mwh`,
   `uv run pytest`, `uv run poe`, `git `, `ls `, `mwh `); deny `sh -c` / `bash -c` / `python -c` /
   `uv run python -c` when the argument mentions those tokens; log decisions (never the data) to
   `%LOCALAPPDATA%\Temp\claude\mwh-pretool.log`. Register under `hooks.PreToolUse` in `.claude/settings.json`;
   `mwh guard --selfcheck` gains a row "pretool-hook registered". Test (`tests/ep/test_ep165.py`, marker
   `ep_165`): drive the script with crafted hook payloads (deny on `cat C:/mimicdata/x.csv`, `python -c
   "open('…/probe.csv')"`, `cp probe.csv %TEMP%`; allow `uv run mwh inventory show`, `git status`,
   `Read CLAUDE.md`), ≤ 200 ms per call. Strike GOV-1 in `final-roadmap.md` (Resolved by EP-165).
4. **GOVERNANCE.md addenda** (GOV-8, DOC-6, CMP-1, GOV-3; owner-authorised; append, never rewrite):
   §2 — two real-time products; the seven (→ nine) Malwarebytes paths, two inside the repository
   (`source material\`, `.venv`); telemetry off; exclusions are a disclosure control; relocating
   `MWH_DATA_ROOT` requires updating the deny rules first (the rules hard-code `C:\mimicdata`);
   §4 — item 3 layer list gains the PreToolUse hook and the `env` block; new paragraph on connectors /
   WebFetch / WebSearch (item 3 of Decisions) — the deny rules cover data files, not tool egress, so the
   rule is prose + denied send tools; §4(2) note that `Read(**/*.csv)` is project-relative (EP-0 finding).
5. **`.gitignore` anchoring + guard probes** (GOV-6; owner-authorised) — replace the unanchored
   `data/ lake/ warehouse/ runs/ models/ notes/ studies/ exports/ mimicdata/` with root-anchored pairs
   (`/data/`, `/mimicwarehouse/data/`, …) so `src/mimicwarehouse/data/item_units.yaml` (EP-39),
   `docs/studies/`, `docs/exports/`, `app/models/` are not silently dropped; keep the extension guards
   and the `!mimicwarehouse/tests/fixtures/**` negation as they are; add
   `mimicwarehouse/src/mimicwarehouse/data/item_units.yaml` and `mimicwarehouse/app/models/x.py` to
   `guard.TRACKED_PROBES`, keep `mimicwarehouse/data/x.parquet` in `IGNORED_PROBES`; `mwh guard --selfcheck`
   green; also mirror the new G1 suffixes (item 6) into `.gitignore` and `.gitattributes` (binary).
6. **Guard G1/G4 gaps** (GOV-4, GOV-5) — G4 regex →
   `(?<![A-Za-z0-9.])[123]\d{7}(?:\.0+)?(?![A-Za-z0-9.])` (float-rendered ids), `id_band_hits` strips
   `.0` before `int()`, `mask()` masks the digit part; path scanning catches `stay_3xxxxxxx.parquet` /
   `stay-3xxxxxxx.png` (verifier: trailing-boundary rule, see ledger); G1 `DATA_EXTENSIONS` +=
   `.tsv .xlsx .xls .zip .7z .tar .tgz .tar.gz .gz .bz2 .zst .xz .sqlite .sqlite3 .db .orc .avro .ndjson
   .hdf5` (longest-suffix already covers `.parquet.gz`/`.csv.zst`); `.tsv` joins `TEXT_EXTENSIONS`; verify
   `mwh guard --all-tracked` stays clean (verifier ran the new regex over all 421 tracked blobs: 0 hits;
   `concepts/vendoring.py` derives its byte pattern from `guard.ID_TOKEN` — re-run `test_ep08`).
7. **DECISIONS addenda** — D-38 (nine paths; VS Code restart), D-39 (settings.json env/deny/allow +
   hook shipped), D-42 (CLAUDE.md now carries the rules; memory notes retire), D-43 pointer rows ticked.
   Update `README.md`(workspace) § Contributing hook table if G1/G4 text changes.
8. **Tests** — `tests/ep/test_ep165.py` (`ep_165`, fixture): hook script cases; guard G1/G4 new cases
   (float id, path id, new suffixes); `.gitignore` probes via `guard.selfcheck`; settings.json parses and
   contains the env block, ≥ the listed deny rules, the hook registration; CLAUDE.md contains the PATH
   line, the heredoc ban and the connector bullet (string presence, cheap).

## Out of scope

- The shared console module / UTF-8 entry point → EP-167. Docs consolidation → EP-166. Any change to
  what `safe_query` will do → EP-30. Elevated AV verification → parked DOC-1 (final-roadmap).

## Verification / acceptance

- `uv run poe test -m ep_165` green; `uv run poe check` green; `mwh guard --selfcheck` shows the new
  probes + "pretool-hook registered"; `mwh guard --all-tracked` clean; a crafted `Bash(cp probe.csv …)`
  and `python -c` read of the scratchpad probe are **refused** by the hook (recorded in the completion
  note, no data involved); `mwh verify EP-165` exit 0; `poe roadmap-check --strict` 0/0.
- Commit pair: `feat(mimicwarehouse): session/repo governance hardening — CLAUDE.md, settings.json env/deny/allow, PreToolUse hook, GOVERNANCE addenda, .gitignore anchoring, guard G1/G4 (EP-165)` then `docs(roadmap): record EP-165 commit hash`.
- Completion note: owner-review points in the verbose style (what/why, alternatives, pros/cons, recommendation).

## Parked → final-roadmap.md

- Elevated `mwh doctor --elevated` (already DOC-1). Output-scanning (post-tool) hook: only if the
  command-string hook proves insufficient (note under GOV-1's struck row).
