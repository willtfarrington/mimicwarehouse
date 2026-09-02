# CLAUDE.md — session rules for mimicwarehouse

This repository builds a local MIMIC-IV data lab over **real, licensed, patient-level
health data** (PhysioNet Credentialed Health Data License 1.5.0). Tool results from this
session are transmitted to Anthropic, so the rules below are license obligations, not
style preferences. Read them fully before doing anything.

## 1. Read first

1. `mimicwarehouse/GOVERNANCE.md` — the safety/licensing contract (overrides everything).
2. The **one** roadmap brief you were handed (`roadmap/EP-<n>-*.md`) and `roadmap/README.md`
   §"How to use this roadmap". One brief per session; do not start the next one.
3. What already exists: `mimicwarehouse/README.md` § "State of the workspace" (from EP-166)
   and the `roadmap/README.md` phase tables — a ☑ hash means shipped.
4. `mimicwarehouse/DESIGN.md` and `mimicwarehouse/DECISIONS.md` for architecture and the
   settled decisions the brief cites as **D-n**.

## 2. Data access — hard rules

- **Never** print, read, `head`, `cat`, `type`, `Get-Content`, `Select-String`, grep, or
  open any file under `source material/` (except `*.md`) or under the data root
  (`C:\mimicdata`, `MWH_DATA_ROOT`), and never run the `duckdb` executable directly.
  `.claude/settings.json` denies the common forms; its PreToolUse hook (EP-165) refuses
  commands/paths mentioning `mimicdata`/`source material`/`.csv`/`.parquet`/`.duckdb`
  outside allow-listed project commands. Blocklists, not a firewall — `python`/`cp`/nested
  shells against those paths are equally forbidden. Never work around a denial; the shell
  rules match the *string*, so use the Read/Grep tools for docs that merely mention the
  tokens, and for `source material/README.md`.
- From EP-30 on, `mwh sql`/`safe_query` is the only way a session queries data —
  read-only, allow-listed, aggregate-only, row-capped, k = 11 suppression, every call
  audited to `runs/audit.jsonl`. Usage:

  ```
  uv run --group dev mwh sql "SELECT anchor_year_group, count(*) AS n FROM mimiciv_hosp.patients GROUP BY 1" --tier dev
  ```

  Refusals exit 3 with the reason. You may only see **aggregates, schemas,
  dictionaries, counts and statistics** — never identifiers (`subject_id`, `hadm_id`,
  `stay_id`, `note_id`, …), never row samples, never note text.
- claude.ai connectors and WebFetch/WebSearch are a second egress path: public-reference
  lookups only (PubMed, bioRxiv, ICD-10 Codes, Context7, GitHub/DOI); never fixture rows,
  aggregates, ids, run records or note text in a query; connector send/write tools are
  denied; the Google Drive connector stays off (GOVERNANCE §4, D-29, D-43).
- If a command output unexpectedly contains row-level data or note text: stop, do not
  repeat it, tell the owner, and note the incident in `DECISIONS.md` § Addenda.
- Never write real rows into fixtures, golden files, tests, docs, screenshots, or commits.
  Synthetic fixtures use ids ≥ 90 000 000. Real MIMIC bands are 10 000 000–19 999 999
  (`subject_id`), 20 000 000–29 999 999 (`hadm_id`), 30 000 000–39 999 999 (`stay_id`).
- `MWH_ROLE` stays unset in Claude sessions — `open_catalog`'s role defaults to `agent`;
  only the owner sets `owner`, in their own shell, for row-view features (EP-21).
- Suspected PHI → the owner reports it to PhysioNet. Do not paste it anywhere.

## 3. Environment & commands

- Workspace: `mimicwarehouse/` (uv project). Run everything as
  `uv run --project mimicwarehouse --group <group> mwh <cmd>` (or `cd mimicwarehouse`
  first). Groups: `dev` (default for tests), `ui` (isolated; pins `pyarrow<25`), `gpu`,
  `gpl`, `text`. `default-groups=["dev"]`, so `uv run mwh` ≡ `uv run --group dev mwh`.
  `poe` tasks run from `mimicwarehouse/` or from the repo root via `poe_tasks.toml`
  (EP-33); `poe check` = ruff check + ruff format --check + pyright + pytest.
- The lore behind these rules lives in `mimicwarehouse/docs/gotchas.md` (EP-33); this
  file carries only rules.
- **Session tooling (D-42, Risks 12/13).** Overrides the harness's own suggestion to use
  heredocs/`sed`/stdin scripts:
  (a) `uv` may be missing from a tool shell's PATH. Fallback, needed before `uv`, `poe`,
  `pre-commit` **and `git commit`**: `export PATH="$LOCALAPPDATA/Microsoft/WinGet/Links:$PATH"`
  (Bash) / `$env:PATH="$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:PATH"` (PowerShell).
  (b) Bare `python`/`pip` in the tool shells is the system CPython 3.14 — never use or
  `pip install` into it; always `uv run python …` (uv manages CPython 3.13).
  (c) Files only via Write/Edit — **never** bash heredocs (Defender kills the shell as
  ClickFix), never `python -`/stdin scripts (hangs; EP-12); commit messages via
  `git commit -F <scratch file>`; no burst copy/`sed -i`/delete loops over many scratch
  files (Malwarebytes ransomware heuristic); "process killed / binary vanished / access
  denied" → check Malwarebytes Quarantine + `mbamservice.log` first.
  (d) Foreground `sleep` is blocked in the Bash tool; long work = background jobs + logs.
  (e) Console: `PYTHONUTF8=1` comes from `.claude/settings.json`; new CLI strings still
  stay ASCII or go through the shared console helper (EP-167).
- Tiers: develop and test on `fixture` (synthetic) and `dev` (5 %); `full` runs are
  background jobs (`mwh build --tier full …` with a log) that the **next** EP verifies.
  Foreground shell commands are capped at ~10 min — never a full-tier scan in the
  foreground.
- Set DuckDB `memory_limit`, `threads`, `temp_directory` explicitly (the config module
  does); keep ≥ 100 GB free on C:; nothing on G:/D:.
- Guard `if __name__ == "__main__":` for any multiprocessing (Windows spawn).
- **Power mode.** The owner toggles Windows power mode **off between sessions**; timings
  assume **Best performance** on AC (D-38). Before any compute-heavy step check the
  `power_scheme` line of `uv run mwh doctor`; if it reads Balanced, ask the owner to
  re-enable it (Settings › System › Power & battery) — never change the plan yourself.

## 4. Doing an EP

1. Read the brief; confirm its Depends-on EPs show ☑ in `roadmap/README.md`.
2. Implement only the brief's **In scope**; hand anything else to the EP named in
   **Out of scope**; put deliberately-skipped algorithms in the brief's **Parked** section
   and mirror them into `roadmap/final-roadmap.md`.
3. Tests: `tests/ep/test_ep<NN>.py` (zero-padded file, unpadded marker `ep_<n>`);
   `uv run poe test -m ep_<n>` and `uv run mwh verify EP-<n>` must be green on fixture
   (+dev where stated).
4. Record timings / run ids for any full-tier run in a `> **Completion note (date).**`
   block appended to the brief.
5. Commit in two steps: `feat(mimicwarehouse): <what> (EP-<n>)` then, after updating the
   ☑ hash in `roadmap/README.md`, `docs(roadmap): record EP-<n> commit hash`. Only commit
   when the owner asks or the brief's commit recipe says so; never `--no-verify`.
   **No AI attribution trailers** (owner preference, 2026-08-28): do not add
   `Co-Authored-By: Claude …` or "Generated with Claude Code" lines to commits or PRs —
   `includeCoAuthoredBy` is off in `.claude/settings.json`; don't re-add them by hand.
   (AI-assisted development is disclosed in the project narrative, not per-commit.)
6. If you hit the context limit mid-EP: commit a green checkpoint if possible and write
   `roadmap/EP-<n>-completion-handoff.md` (hupsim precedent) for the next session.

## 5. Docs discipline

- Design changes → dated note in `DESIGN.md`; decisions → `DECISIONS.md` addenda; risks →
  `roadmap/README.md` § Risks. Do not rewrite history in these files.
- Aggregates, figures or screenshots may enter `docs/` or git only after
  `uv run mwh disclose check <path>` passes and writes a `.disclosure.json` sidecar.
  Manifests of hashes/counts/schema (GOVERNANCE §3) may be committed without a sidecar; any
  other aggregate, figure or screenshot needs `mwh disclose check` + `.disclosure.json`
  (EP-43) — before EP-43 nothing else derived from real data enters `docs/` or git.
  Screenshots of row-level views only from the `demo`/`fixture` tiers.
- Reports label their claim type (exploratory / confirmatory / predictive / associational
  / causal) and state that MIMIC-IV analyses are retrospective.

## 6. Ask before

Deleting or moving anything under `source material/` or the data root; changing
`.gitignore`, `.gitattributes`, `.claude/settings.json`, `GOVERNANCE.md`; installing
system-level software; enabling remote/network calls from text modules
(`MWH_ALLOW_REMOTE`); sending anything through a connector (reference lookups need no
ask; transmitting project content does); force-pushing or rewriting history.
