# EP-67 — Patient-safe timeline viewer

**Size:** M · **Tier:** fixture+demo · **Core/Stretch:** core · **Depends on:** EP-49 (Event-aligned timeline API), EP-58 (App shell B: row-view gate + app-side small-cell enforcement) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Capability 8 (event-aligned timeline queries) exists as code: EP-49's anchors (hospital admit,
ICU intime, first culture, medication start, procedure, ventilation start, deterioration,
discharge), windows, hourly bins and ASOF/window joins over the catalog. GOVERNANCE §6 is
strict about the UI: single-stay timelines are row-level data — rendered only in the app for
the owner behind the EP-58 row-view gate (audit line, banner), developed and screenshotted only
against `fixture`/`demo`, never exported or printed by any CLI; aggregated/binned timeline
views (counts per hour bin) follow the small-cell rule instead. Plotly is the designated
library for lane/Gantt timelines (D-21); everything else stays Altair. This brief builds both
modes on the EP-57 shell with EP-58 wrappers. Time axes are hours since the anchor — never
calendar dates (per-patient date shift). Demo (ODbL) row views may be screenshotted; dev/full
never.

## In scope

1. **Aggregate mode** (default; all tiers) in `app/pages/31_timelines.py` (registry id
   `timelines`, section Explore) — controls: anchor (EP-49 anchor registry), cohort/filters
   (era, first ICU stay, materialised cohort), lanes (transfers, medications (emar/inputevents),
   procedures, labs, vitals), window (default −24 h … +168 h, 1 h bins) →
   `timeline.event_density(anchor, lanes, window, filters, tier)` (EP-49; add it there if only
   the per-stay API exists — it returns counts per (lane, hour_bin) plus `n_stays` per bin,
   never rows) → Altair heatmap/area via `src/mimicwarehouse/viz/timeline_agg.py:
   density_lanes(df)` rendered with `safe_altair` (small bins badged on dev/full).
2. **Single-stay mode** (owner-gated) — visible only while `mwh.row_view` is on (EP-58);
   otherwise the tab shows the gate instructions and nothing else. Stay selection through
   `safe.owner_rows` (audited): a filter form (era, first care unit, LOS band, phenotype flag)
   → a capped candidate list rendered inside `gate.row_context()` (opaque row number, unit,
   LOS; the chosen `stay_id` is held in session state and never echoed to logs, captions or
   audit free fields) → `timeline.stay_timeline(stay_id, anchor, tier)` (EP-49) → Plotly
   figure via `src/mimicwarehouse/viz/timeline_plotly.py: stay_lanes(events)`: transfers as
   care-unit segments, medications as start–stop bars (dose/route in hover), procedures as
   bars/markers, labs and vitals as markers with value + unit hover; x = hours since anchor;
   `config={"displayModeBar": False}` and `modeBarButtonsToRemove=["toImage"]` — no image
   download; rendered via `safe_plotly` inside `gate.row_context()`; the ROW VIEW banner stays;
   no export controls on this tab. `plotly` (MIT) added to the `ui` group; no kaleido.
3. **Development + screenshot rule** — develop on fixture; manifest entries `timelines-aggregate`
   and `timelines-stay-demo` (with an `actions` step that opens the gate — permitted on demo);
   when tier ∈ {dev, full} and the gate is on, the tab shows a reminder "never screenshot this
   view"; the EP-60 tool already refuses dev/full.
4. **Tests** `tests/ep/test_ep67.py` (`@pytest.mark.ep_67`, ui group; fixture): gate closed →
   the single-stay tab renders no figure and shows the gate notice; with the gate opened in
   AppTest → a figure with ≥ 4 lane traces for a fixture stay; the Plotly config contains no
   `toImage`; aggregate mode renders on fixture and badges a crafted small bin under
   `mwh.tier="dev"`; `ui_lint` passes (no raw `st.plotly_chart(`); after a single-stay render
   the last audit line (`safe.read_audit(tail=1)`) carries a statement hash and no `stay_id`
   value; the manifest contains the two entries.

## Out of scope

- Timeline API semantics, anchors, joins → EP-49; trajectories/exposure analyses → EP-82/86.
- Aggregate figure export → EP-59/EP-73 (aggregate mode only); single-stay figures are never
  exported.
- anywidget/D3 timeline component → parked (v2 UI-T1, already listed).

## Verification / acceptance

- `uv run --group ui poe test -m ep_67` green on fixture; `uv run --group ui mwh verify EP-67`
  green (includes `ui_lint`).
- The page **refuses** to render a single-stay figure while the gate is closed (test + manual on
  fixture); opening the gate writes the audit line and shows the banner.
- Demo screenshots `timelines-aggregate-*.png`, `timelines-stay-demo-*.png` with sidecars;
  `mwh disclose check docs/screenshots` passes; no dev/full screenshot exists under `docs/`.
- Single-stay render wall time on demo and aggregate-mode wall time on dev recorded in the
  completion note (`page_latency` entries).
