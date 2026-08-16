# CLAUDE.md — session rules for mimicwarehouse

This repository builds a local MIMIC-IV data lab over **real, licensed, patient-level
health data** (PhysioNet Credentialed Health Data License 1.5.0). Tool results from this
session are transmitted to Anthropic, so the rules below are license obligations, not
style preferences. Read them fully before doing anything.

## 1. Read first

1. `mimicwarehouse/GOVERNANCE.md` — the safety/licensing contract (overrides everything).
2. The **one** roadmap brief you were handed (`roadmap/EP-<n>-*.md`) and `roadmap/README.md`
   §"How to use this roadmap". One brief per session; do not start the next one.
3. `mimicwarehouse/DESIGN.md` and `mimicwarehouse/DECISIONS.md` for architecture and the
   settled decisions the brief cites as **D-n**.

## 2. Data access — hard rules

- **Never** print, read, `head`, `cat`, `type`, `Get-Content`, `Select-String`, grep, or
  open any file under `source material/` (except `*.md`) or under the data root
  (`C:\mimicdata`, `MWH_DATA_ROOT`), and never run the `duckdb` executable directly.
  Repo `.claude/settings.json` denies these; do not work around a denial.
- All queries go through `mimicwarehouse.safe.safe_query(...)` or `uv run mwh sql`
  (read-only, allow-listed, row-capped, k = 11 suppression, audit-logged). You may only
  see **aggregates, schemas, dictionaries, counts and statistics** — never identifiers
  (`subject_id`, `hadm_id`, `stay_id`, `note_id`, …), never row samples, never note text.
- If a command output unexpectedly contains row-level data or note text: stop, do not
  repeat it, tell the owner, and note the incident in `DECISIONS.md` § Addenda.
- Never write real rows into fixtures, golden files, tests, docs, screenshots, or commits.
  Synthetic fixtures use ids ≥ 90 000 000. Real MIMIC bands are 10 000 000–19 999 999
  (`subject_id`), 20 000 000–29 999 999 (`hadm_id`), 30 000 000–39 999 999 (`stay_id`).
- Suspected PHI → the owner reports it to PhysioNet. Do not paste it anywhere.

## 3. Environment & commands

- Workspace: `mimicwarehouse/` (uv project). Run everything as
  `uv run --project mimicwarehouse --group <group> mwh <cmd>` (or `cd mimicwarehouse` first).
  Groups: `dev` (default for tests), `ui` (Streamlit; isolated because it pins
  `pyarrow<25`), `gpu`, `gpl`, `text`. Never `pip install` into the system Python 3.14;
  uv manages CPython 3.13.
- Tiers: develop and test on `fixture` (synthetic) and `dev` (5 %); `full` runs are
  background jobs (`mwh build --tier full …` with a log) that the **next** EP verifies.
  Foreground shell commands are capped at ~10 min — never run a full-tier scan in the
  foreground.
- Set DuckDB `memory_limit`, `threads`, `temp_directory` explicitly (the config module
  does); keep ≥ 100 GB free on C:; nothing on G:/D:.
- Guard `if __name__ == "__main__":` for any multiprocessing (Windows spawn).

## 4. Doing an EP

1. Read the brief; confirm its Depends-on EPs show ☑ in `roadmap/README.md`.
2. Implement only the brief's **In scope**; hand anything else to the EP named in
   **Out of scope**; put deliberately-skipped algorithms in the brief's **Parked** section
   and mirror them into `roadmap/final-roadmap.md`.
3. Tests: `tests/ep/test_ep<NN>.py` with tier markers; `uv run poe test -m ep_<NN>` and
   `uv run mwh verify EP-<n>` must be green on fixture (+dev where stated).
4. Record timings / run ids for any full-tier run in a `> **Completion note (date).**`
   block appended to the brief.
5. Commit in two steps: `feat(mimicwarehouse): <what> (EP-<n>)` then, after updating the
   ☑ hash in `roadmap/README.md`, `docs(roadmap): record EP-<n> commit hash`. Only commit
   when the owner asks or the brief's commit recipe says so; never `--no-verify`.
6. If you hit the context limit mid-EP: commit a green checkpoint if possible and write
   `roadmap/EP-<n>-completion-handoff.md` (hupsim precedent) for the next session.

## 5. Docs discipline

- Design changes → dated note in `DESIGN.md`; decisions → `DECISIONS.md` addenda; risks →
  `roadmap/README.md` § Risks. Do not rewrite history in these files.
- Aggregates, figures or screenshots may enter `docs/` or git only after
  `uv run mwh disclose check <path>` passes and writes a `.disclosure.json` sidecar.
  Screenshots of row-level views only from the `demo`/`fixture` tiers.
- Reports label their claim type (exploratory / confirmatory / predictive / associational
  / causal) and state that MIMIC-IV analyses are retrospective.

## 6. Ask before

Deleting or moving anything under `source material/` or the data root; changing
`.gitignore`, `.gitattributes`, `.claude/settings.json`, `GOVERNANCE.md`; installing
system-level software; enabling remote/network calls from text modules
(`MWH_ALLOW_REMOTE`); force-pushing or rewriting history.
