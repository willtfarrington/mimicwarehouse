# EP-57 — App shell A (Streamlit multipage)

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-5 (Visual identity), EP-30 (Safe-query wrapper + audit log) · **Blocks:** EP-58 (App shell B: row-view gate + app-side small-cell enforcement), EP-60 (Screenshot tooling), EP-61 (Catalog & QC browser page), EP-62 (Cohort Builder page), EP-63 (Phenotype Studio page), EP-64 (Explorer A: server-side aggregation service + VegaFusion), EP-69 (Prevalence/incidence page), EP-73 (Capstone #2: EDA case study + screenshots), EP-88 (Analysis pages wave 1), EP-99 (Survival / causal app pages), EP-125 (ML pages in app), EP-128 (Protocol Freezer page + amendments UI), EP-134 (Runs & Provenance browser + Reports page / export gallery), EP-140 (Linkage Wizard A (profile → map)), EP-154 (Text pages in app (search only)), EP-159 (Demo mode for the app)

## Context

The Lab app is a single Streamlit 1.61 multipage process bound to `127.0.0.1`, with a READ_ONLY
catalog connection cached per tier, a tier switcher defaulting to `dev` (D-21, D-28, DESIGN §16).
Nothing under `app/` exists yet. This brief builds the shell every later page plugs into: the
`mwh app` command, the page registry, the tier switcher, the cached connection, and the single
in-app data path — every page query goes through `mimicwarehouse.safe.safe_query` (EP-30, D-31)
so identifier columns and note text can never reach the browser and every query is audited.
The `ui` dependency group is isolated by `[tool.uv] conflicts` because Streamlit pins
`pyarrow<25` (EP-1); UI code and UI tests run only under `--group ui`. Visual identity (wordmark,
light+dark palette, Altair/Streamlit themes) comes from `theme.py` (EP-5, D-11). Row-view gate,
small-cell wrappers and export buttons are EP-58; this brief only reserves their session-state
keys. Catalog tiers are `demo`/`dev`/`full` (`fixture` only under tests). App-side DuckDB limits
are explicit (`memory_limit` 8–16 GB, threads 8, temp dir under the data root — DESIGN §6).

## In scope

1. **Groups + `mwh app` command** — confirm `uv run --group ui python -c "import streamlit,
   altair"` resolves (Streamlit 1.61.x, `pyarrow<25`); make `uv run --group ui poe test -m ep_57`
   work: if EP-1's conflict table forbids `dev` + `ui` together, add `pytest`, `pytest-*` plugins
   used by `tests/conftest.py` and `poethepoet` to the `ui` group and record a dated DESIGN.md
   note. Add `mwh app` to `cli.py` (typer group `app` with `invoke_without_command=True`, so
   `mwh app` runs the server and `mwh app screenshot` can be added by EP-60):
   `mwh app [--tier demo|dev|full] [--port 8501] [--headless]` → `streamlit run <app_dir>/lab.py
   --server.address 127.0.0.1 --server.port <port> --server.headless <bool>
   --browser.gatherUsageStats false`; `app_dir` from `config.py` (`settings.app_dir`, default
   `<project>/app`); the CLI hard-codes the address and accepts no Streamlit pass-through args;
   env forwarded: `MWH_APP_TIER`, `MWH_APP_MEMORY_LIMIT` (default `12GB`), `MWH_APP_THREADS`
   (8), `MWH_APP_ROLE` (default `owner`), `MWH_APP_RECORD_LATENCY`; refuses to start when the
   config safety check finds the data root on a synced/virtual drive (EP-3). Theme:
   `app/.streamlit/config.toml` generated from `theme.py` (light + dark).
2. **Entry point + registry** — `app/lab.py` calls `shell.init()` then builds `st.navigation`
   from `src/mimicwarehouse/ui/registry.py`: `PAGES: list[PageSpec(id, title, icon, section,
   path, ep, status)]`, sections Home · Data (Catalog & QC, EP-61) · Cohorts (Cohort Builder
   EP-62, Phenotype Studio EP-63) · Explore (Explorer EP-64–66, Timelines EP-67, Prevalence &
   Rates EP-69, Subgroups EP-70, Table 1 EP-71, Missingness EP-72) · Analysis (EP-88, EP-99,
   EP-125) · Provenance (Runs, Reports EP-134) · Tools (Protocol Freezer EP-128, Linkage Wizard
   EP-140, Text EP-154). `status="planned"` entries render a shared stub ("Planned — EP-n");
   later EPs flip status and point `path` at `app/pages/NN_<name>.py`.
3. **Shell** (`src/mimicwarehouse/ui/shell.py`) — `init()`: `st.set_page_config(layout="wide",
   page_title="mimicwarehouse Lab", page_icon=<EP-5 asset>)`; sidebar wordmark; tier selectbox
   (`demo`, `dev`, `full`; default `dev`; a tier is offered only if its catalog file exists;
   `fixture` only when `MWH_APP_TIER=fixture`); governance line ("aggregate views · n < 11
   warned on dev/full · row view owner-only"); header badges: tier, catalog snapshot ids
   (core/derived/marts from manifests), git sha, DuckDB version. Session-state schema in the
   module docstring: `mwh.tier`, `mwh.role`, `mwh.row_view` (False here; EP-58),
   `mwh.page_t0`. `page_start(page_id)` / `page_end()` measure render wall time, show a latency
   caption and, when `MWH_APP_RECORD_LATENCY=1`, append `kind:"page_latency"` to
   `runs/benchmarks.jsonl` (`marts.bench.record_page_latency`, EP-56 / EP-35 writer).
4. **Connection + query path** (`src/mimicwarehouse/ui/conn.py`) — `get_conn(tier)`
   (`@st.cache_resource`) opens the tier catalog with the EP-21 READ_ONLY opener and the app
   DuckDB settings; `query(sql, tier, params=None, *, k=11) -> pl.DataFrame` is the ONLY data
   path pages may use: it calls `safe.safe_query(sql, tier=tier, params=params,
   conn=get_conn(tier), actor="owner", suppress=False)` — allow-list, row cap, identifier /
   free-text refusal and audit line as in EP-30, but returning the small-cell mask instead of
   suppressing (in-app rule, D-33). If EP-30's signature lacks `conn=`/`actor=`/`suppress=`,
   extend `safe.py` (verifying the passed connection is read-only) — never bypass it. Results
   cached with `@st.cache_data(ttl=600)` keyed on (tier, sql, params, snapshot id).
5. **Home page** `app/pages/00_home.py` — status cards from `meta.*` (tables count, top-10 row
   counts from `meta.row_counts`, snapshot ids, last build time from manifests), tier semantics
   (D-18), section links, "what this app never shows" note; all numbers via `ui.conn.query`.
6. **Tests** `tests/ep/test_ep57.py` (`@pytest.mark.ep_57`, ui group) —
   `AppTest.from_file("app/lab.py", default_timeout=60)` with `MWH_APP_TIER=fixture` and the
   fixture data root: no exception, sidebar has the tier selectbox, header shows a snapshot id,
   Home renders ≥ 1 metric; `mwh app --help` lists the options; the argument builder always
   emits `--server.address 127.0.0.1` and refuses a crafted `--server.address 0.0.0.0`;
   `query()` refuses `SELECT subject_id FROM mimiciv_hosp.patients` (EP-30 refusal); import
   isolation under the dev group: `import mimicwarehouse` does not import `streamlit`;
   dev-marked: `query("SELECT count(*) FROM mimiciv_hosp.admissions", "dev")` succeeds.

## Out of scope

- Row-view gate, small-cell wrappers, export buttons, page lint → EP-58.
- Screenshot tooling → EP-60; each page → its own EP; demo-mode features → EP-159.
- Authentication / multi-user → final-roadmap (v2 GOV-2).

## Verification / acceptance

- `uv run --group ui poe test -m ep_57` green on fixture (dev-marked test green with the dev
  catalog); `uv run --group ui mwh verify EP-57` green.
- `uv run --group ui mwh app` serves `http://127.0.0.1:8501` on the dev tier; switching to
  `full` re-renders Home with full snapshot ids; `Get-NetTCPConnection -LocalPort 8501` shows
  only a `127.0.0.1` listener.
- Home render wall time on dev and full recorded in the completion note via `page_latency`
  ledger entries (≤ 5 s).
- Dated DESIGN.md note: `src/mimicwarehouse/ui/` package, `mwh app` options, any `ui`-group
  test dependency change.
