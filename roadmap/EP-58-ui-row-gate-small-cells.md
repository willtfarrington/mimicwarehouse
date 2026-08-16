# EP-58 — App shell B: row-view gate + app-side small-cell enforcement

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-67 (Patient-safe timeline viewer), EP-73 (Capstone #2: EDA case study + screenshots)

## Context

GOVERNANCE §4–§6 and D-32/D-33 fix two shell-level behaviours: the owner may view rows only in
the app behind an explicit toggle that writes an audit entry (never exported, never in tool
output), and every count below 11 is shown with a warning badge in-app on dev/full and
suppressed on export. EP-57 built the shell, the cached READ_ONLY connection and the single
query path (`ui.conn.query` → `safe_query`, which already refuses identifier columns and free
text); EP-43 built `disclose.suppress`/`check` and the sidecar writer; EP-30 built the audit
writer/reader in `safe.py`. This brief adds the gate, the `owner_rows()` path DESIGN §12
promises (audited, unreachable from the CLI), "SafeFrame"-style wrappers every page must use for
tables/metrics/charts, disabled export buttons on dev/full, and a lint that keeps later pages
honest. It is a governance brief: acceptance is the gate/wrapper *refusing* crafted violations.
Demo (ODbL) and fixture (synthetic) tiers show counts without the badge; identifier refusal
outside the gate applies on every tier.

## In scope

1. **Row-view gate** (`src/mimicwarehouse/ui/gate.py`) — sidebar expander "Row view (owner
   only)", shown when `mwh.role == "owner"`: checkbox + confirm button ("I am the DUA holder").
   On enable: `safe.audit(event="row_view_toggle", state="on", actor="owner", tier, page)`,
   `st.session_state["mwh.row_view"]=True`, `mwh.row_view_since`; auto-off after
   `MWH_ROW_VIEW_TTL_MIN` (default 30) minutes, on tier switch and on any page exception; on
   disable: audit `state="off"`. While on, `shell.page_start` renders a top-of-page
   `st.error("ROW VIEW ON — patient-level rows visible on tier <t>. Never screenshot or export.
   Auto-off at HH:MM.")` and a red sidebar badge. `gate.row_context()` context manager marks
   code allowed to render identifier columns.
   `owner_rows(sql, tier, *, gate_token, limit=200) -> pl.DataFrame` lives in `safe.py` (add it
   if EP-30 left no stub): requires a per-session token minted only by `ui.gate`; raises
   `RowViewGateClosed` when the gate is off; read-only allow-list, note tables always refused,
   `limit ≤ 500`; one audit line per call (statement hash, row count, page — never the SQL
   literals); never cached; not wired to any `mwh` command.
2. **Small-cell wrappers** (`src/mimicwarehouse/ui/cells.py`) — `safe_dataframe(df, **kw)`,
   `safe_table(df)`, `safe_metric(label, value, **kw)`, `safe_altair(chart, data, **kw)`,
   `safe_plotly(fig, **kw)`. Each: refuses frames containing identifier columns (`subject_id`,
   `hadm_id`, `stay_id`, `note_id`, `emar_id`, `pharmacy_id`, `poe_id`, `transfer_id`) unless
   inside `gate.row_context()` (`UngatedIdentifierColumns`); on dev/full runs
   `disclose.small_cells(df, k=11)` (EP-43 mask function; add `small_cells()` to `disclose.py`
   if only `suppress()` exists) on count-like columns (`n`, `count`, `*_n`, `n_*`, integer
   columns flagged by the caller via `count_cols=`) and, if any cell < 11, shows
   `st.warning("n < 11 in <k> cell(s) — shown in-app, suppressed on export (D-33)")` and
   highlights those cells (pandas Styler, EP-5 warn colour). Streamlit's dataframe toolbar
   (download) is hidden on dev/full via CSS injected by the shell
   (`[data-testid="stElementToolbar"] { display: none; }`) — best effort; the gate and audit are
   the controls (document this).
3. **Export controls** (`ui/cells.py: export_controls(obj, name)`) — dev/full: disabled button
   with help "Exports run through `mwh export` (EP-59) with suppression + a `.disclosure.json`
   sidecar"; demo/fixture: enabled, writes CSV/PNG under `%MWH_DATA_ROOT%\runs\exports\<tier>\`
   (EP-59 later routes this through `viz.export`). `st.download_button` is never used on
   dev/full (lint below).
4. **Page lint** — `tests/ep/test_ep58.py::test_pages_use_wrappers` (marker `ui_lint`, also
   included in every later UI brief's `mwh verify` set): scan `app/pages/*.py` for
   `st.dataframe(`, `st.table(`, `st.metric(`, `st.altair_chart(`, `st.plotly_chart(`,
   `st.data_editor(`, `st.download_button(` outside `ui/cells.py`; a justified exception needs a
   trailing `# mwh: raw-ok <reason>` comment. Add the same scan to `mwh guard` (EP-4) so a
   commit with an unwrapped call is refused.
5. **Shell wiring** — `shell.init()` renders the gate expander and CSS; `page_start` renders the
   banner; tier switch closes the gate; the reserved keys from EP-57 become live.
6. **Tests** (`@pytest.mark.ep_58`, ui group; fixture) — `owner_rows` raises
   `RowViewGateClosed` when off; enabling via AppTest (`at.sidebar.checkbox[…].check().run()`,
   confirm button) writes an audit line readable through `safe.read_audit(tail=5)` (event
   metadata only) with `event="row_view_toggle"`; TTL expiry (monkeypatched clock) closes the
   gate and audits `off`; `safe_dataframe` on a crafted frame with a 7-count cell under
   `mwh.tier="dev"` renders one `at.warning`; the same frame under `fixture` renders none;
   `safe_dataframe` with a `subject_id` column outside `row_context()` raises; export button
   disabled for `dev`, enabled for `fixture`; page lint passes on `app/pages/00_home.py`;
   `cli.py` source and `mwh --help` contain no `owner_rows`; `mwh guard` refuses a crafted page
   file with a raw `st.dataframe(` call.

## Out of scope

- Export pipeline (suppression + footer + sidecar) → EP-59; disclosure-review UI → EP-133.
- The timeline page (first consumer of the gate) → EP-67; audit browser → EP-134.
- Demo-mode relaxations (row view/export enabled by default on demo) → EP-159.

## Verification / acceptance

- `uv run --group ui poe test -m ep_58` green on fixture; `uv run --group ui mwh verify EP-58`
  green (includes `ui_lint`).
- The gate/wrapper/guard **refuse** crafted violations in tests (closed-gate `owner_rows`,
  ungated identifier frame, unwrapped `st.dataframe(` in a page file, `owner_rows` via CLI).
- Manual on dev: enabling row view shows the banner and adds an audit line
  (`uv run --group dev python -c "from mimicwarehouse import safe; print([e['event'] for e in
  safe.read_audit(tail=3)])"` prints event names only).
- Dated DESIGN.md note (§12/§16): `owner_rows` location, wrapper names, TTL default.
