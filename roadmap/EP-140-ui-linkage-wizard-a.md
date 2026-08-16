# EP-140 — Linkage Wizard A (profile → map)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-137 (Importer profiler + provenance/licensing register), EP-138 (Concept/unit mapping guide + mapping YAML) · **Blocks:** EP-141 (Linkage Wizard B (validate → coverage → commit)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

The Linkage Wizard is the UI the category-35 definition of done mandates (README six-part DoD, part 6)
and the shape D-36 fixes: profile → map → validate → coverage → commit. This brief builds the first
two steps as a page of the Streamlit "Lab" app (D-21, EP-57 shell) over the EP-137 profiler/register
and the EP-138 mapping engine. Streamlit's rerun model is a known risk for wizard pages (DESIGN §21):
every step persists its artifact to `%MWH_DATA_ROOT%\ext\<source_id>\` and the page rebuilds its
state from disk, so a rerun never loses work. The page shows aggregates only — profile statistics,
suggested mappings, never source rows (D-31); free-text and id columns show flags, not values.

## Scope sketch (refine at re-plan)

1. **`app/pages/<nn>_linkage_wizard.py`** (numbering per the EP-57 shell) + `src/mimicwarehouse/linkage/wizard.py`
   backend: pydantic `WizardState` (source_id, current step, artifact paths, timestamps) persisted as
   `ext/<source_id>/wizard.json`; a step indicator (Profile · Map · Validate · Coverage · Commit) with
   steps 3–5 disabled until EP-141; source picker restricted to registered sources
   (`ext/registry.yaml`) or a directory under `source material/` / `ext/`; `--tier` follows the shell's
   tier switcher (default dev; fixture in tests).
2. **Step 1 Profile** — button runs `profiler.profile_source` (spinner, wall time shown), renders per-table
   cards and per-column tables (type, null %, distinct, flags), top-k values only where the profiler
   emitted them (already k-suppressed), and the register form (license, DUA date, citation, URL/DOI,
   keys) that writes `source.yaml`; a licence banner repeats the redistribution rule for
   PhysioNet-credentialed sources.
3. **Step 2 Map** — mapping editor over `suggest_mapping`: per table target schema/table selector,
   per column target/role/cast/unit dropdowns pre-filled with suggestions and confidence badges,
   unresolved columns highlighted, `check_mapping` results inline; "Save mapping" writes
   `mapping.yaml` (+ hash into `source.yaml`); "Reset to suggestions" and "Load existing" buttons.
4. **Shell integration** — uses EP-57 components (tier badge, small-cell badge from EP-58 where a
   count < 11 is shown in-app, theme from `theme.py`); page latency for the profile step on the ED-like
   fixture recorded; screenshot tooling (EP-60) hook for the capstone.
5. **Tests** `tests/ep/test_ep140.py` (`@pytest.mark.ep_140`, fixture): `streamlit.testing.v1.AppTest`
   smoke — page loads, profiling the ED-like fixture populates the tables without any value list for
   `chiefcomplaint`, saving a mapping produces a `mapping.yaml` that `check_mapping` accepts, and a
   rerun restores the state from `wizard.json`.

## Out of scope

- Validate / coverage / commit steps and any write to lake or catalog → EP-141.
- Running the wizard on real ED or reference data → EP-142 / EP-143; demo-tier screenshots → EP-146.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_140` and `uv run --group dev mwh verify EP-140` green on fixture
  (`--group ui` for the AppTest run: `uv run --group ui poe test -m ep_140`).
- `uv run --group ui mwh app` → Linkage Wizard page: profile the ED-like fixture, edit and save a
  mapping; artifacts at `ext/edlike/{profile.json,source.yaml,mapping.yaml,wizard.json}`.
- Profile-step latency on the fixture recorded in the completion note; no full-tier run here.

## Parked → final-roadmap.md

- marimo-app variant of the wizard if Streamlit reruns fight the multi-step form — trigger: state loss
  observed despite disk persistence (final-roadmap UI-1).
