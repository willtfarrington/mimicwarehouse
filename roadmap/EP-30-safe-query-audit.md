# EP-30 — Safe-query wrapper + audit log

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-31 (Tracer bullet: first-ICU-stay adults → in-hospital mortality), EP-33 (Re-plan P2), EP-35 (Provenance run ledger), EP-43 (Disclosure primitives (`disclose` module)), EP-57 (App shell A (Streamlit multipage))

## Context

The governance choke point (**D-31**, **D-32**, **D-33**, **D-39**; GOVERNANCE §4, §8;
DESIGN §12): from this brief on, *every* result a Claude session or an export can see comes
through `mimicwarehouse.safe.safe_query` or the `mwh sql` CLI built on it — read-only,
allow-listed statements, aggregate-only, no identifiers, no free text, row cap, k = 11
suppression, every call audited to the append-only `runs/audit.jsonl` exposed through
`runs.duckdb` views (**D-24**). Enforcement is layered: this module + `CLAUDE.md` + the
repo `.claude/settings.json` deny rules. What exists: per-tier catalogs and
`open_catalog(tier, read_only=True)` (EP-21), identifier and free-text flags in the schema
contract (EP-9/EP-23/EP-24/EP-25/EP-29), the interim metadata-only `mwh sql` (EP-21), the
fixture catalog fixture (EP-21). Owner row viewing is **not** here — it is the app's
audited `owner_rows()` path (EP-58); `mwh sql` behaves the same for every role.
Complementary suppression for tables/exports is EP-43's; this brief applies a strict
row-wise k rule and exposes a hook EP-43 replaces. Acceptance is governance-style: the
wrapper must **refuse** crafted violations in tests.

## In scope

1. **`safe_query`** (`src/mimicwarehouse/safe.py`) —
   `safe_query(sql: str, *, tier: str = "dev", k: int = 11, row_cap: int = 200, timeout_s: float = 120, actor: str | None = None) -> SafeResult`
   (`df: polars.DataFrame`, `n_rows`, `rows_suppressed`, `statement_sha256`, `audit_id`,
   `tier`, `k`, `duckdb_version`, `snapshot_id`). Pipeline: (a) **parse** with DuckDB's
   `json_serialize_sql` — exactly one statement, of type SELECT (CTEs allowed) or one of the
   metadata forms `DESCRIBE <schema.table>` / `SHOW TABLES` / `SHOW ALL TABLES`; anything
   else (COPY, ATTACH, INSTALL, LOAD, PRAGMA, SET, CREATE, INSERT, UPDATE, DELETE, EXPORT,
   IMPORT, CALL, BEGIN, multi-statement) is refused; (b) **allow-list** — refuse table
   functions that touch files or the environment (`read_csv*`, `read_parquet`,
   `parquet_scan`, `read_json*`, `read_text`, `read_blob`, `glob`, `sniff_csv`, `getenv`,
   `current_setting`, `duckdb_settings`) and any schema outside `mimiciv_hosp, mimiciv_icu,
   mimiciv_derived, meta, marts, runs, information_schema` (+ `duckdb_tables()`/
   `duckdb_columns()`); notes schemas never exist in these catalogs; (c) **aggregate-only**
   — walk the outermost select list: every result column must be an aggregate call, a
   GROUP BY key or a metadata column; identifier columns (contract flags) may appear
   **only inside COUNT / COUNT(DISTINCT) / approx_count_distinct**, never as output,
   inside arithmetic, or in MIN/MAX/string aggregates; every SELECT must include at least
   one count-family column (`count(*)`, `count(x)`, `count(DISTINCT x)`, or an alias
   matching `^(n|n_.*|.*_n|count|.*_count|cnt|.*_cnt|num_.*)$`) unless it reads only
   `meta.*`, `d_*` dims or `information_schema`; (d) **execute** on `open_catalog(tier)`
   with `runs.duckdb` ATTACHed read-only as `runs` when it exists, and a `threading.Timer`
   that calls `con.interrupt()` at `timeout_s`; (e) **result checks** — refuse if any output
   column is named like an identifier, if any VARCHAR column has a value longer than 64
   characters or containing a newline (free-text heuristic; contract `free_text` columns
   are refused by name too), or if the (post-suppression) row count exceeds `row_cap`;
   (f) **k-suppression** (row-wise): drop every row in which any count-family column is in
   `1 … k-1`, count them in `rows_suppressed`; on `dev`/`full` `k < 11` is refused; on
   `fixture`/`demo` (synthetic / ODbL) `k` may be lowered by the caller. Refusals raise
   `SafeQueryRefused(reason)` after auditing.
2. **Suppression hook** — `safe.SUPPRESSOR: Callable[[pl.DataFrame, int, list[str]], tuple[pl.DataFrame, int]]`
   defaults to the row-wise rule; EP-43 replaces it with `disclose.suppress` (complementary
   suppression). Document the contract in the module docstring.
3. **Audit** — `runs/audit.jsonl` (append-only, `O_APPEND`, flush + fsync, one JSON per
   line): `{audit_id, ts, actor (MWH_ROLE or "agent"; "tracer"/"app" when passed), tier,
   statement_sha256, sql_text, allowed, refusal_reason, n_rows, rows_suppressed, k, wall_ms,
   duckdb_version, snapshot_id, git_sha}` — never result values.
   `safe.build_runs_db()` creates `warehouse/runs.duckdb.new` with view `audit` over
   `read_json_auto('<data_root>/runs/audit.jsonl', format = 'newline_delimited')` and swaps
   it in; `mwh runs refresh` calls it (EP-35 adds the ledger views); readers open it
   read-only. Directory `runs/` and the file are created on first use.
4. **`mwh sql` (final body)** — `mwh sql "<statement>" --tier {fixture,demo,dev,full}
   [--k 11] [--row-cap 200] [--format table|csv|json]` prints the suppressed aggregate
   through rich (or CSV/JSON to stdout), a footer `k=<k>: <rows_suppressed> rows
   suppressed · audit <id> · tier <t> · snapshot <id>`, and exit 3 with the reason on
   refusal. `--tables`, `--describe`, `--count` (EP-21) are kept and routed through
   `safe_query`. No `--out` (exports are EP-59).
5. **Docs wiring** — `CLAUDE.md` §2: a three-line usage example (`uv run --group dev mwh
   sql "SELECT anchor_year_group, count(*) AS n FROM mimiciv_hosp.patients GROUP BY 1" --tier dev`)
   and the sentence "from EP-30 on, `mwh sql`/`safe_query` is the only way a session
   queries data"; `DESIGN.md` §12 dated note with the final rule set; GOVERNANCE.md is
   **not** edited (ask the owner if a rule needs changing).
6. **Tests** (`tests/ep/test_ep30.py`, `@pytest.mark.ep_30`, fixture catalog) — each
   crafted violation is refused **and** produces an audit line with `allowed = false` and
   the reason: `COPY (SELECT 1) TO 'x.csv'`; `ATTACH 'x.duckdb'`; `INSTALL httpfs`;
   `SELECT * FROM read_csv('x.csv')`; `SELECT 1; SELECT 2`;
   `SELECT subject_id, count(*) FROM mimiciv_hosp.admissions GROUP BY 1`;
   `SELECT subject_id AS s, count(*) AS n … GROUP BY 1` (aliased identifier);
   `SELECT max(subject_id) AS m, count(*) AS n FROM …` (identifier in MIN/MAX);
   `SELECT gender FROM mimiciv_hosp.patients LIMIT 5` (row-level, no count);
   `SELECT comments, count(*) AS n FROM mimiciv_hosp.labevents GROUP BY 1` (free text);
   a statement on `mimiciv_note.x`; a result wider than `row_cap`; `timeout_s = 0.001` on
   `SELECT count(*) AS n FROM range(10**9)`; `k = 5` on `dev`. Allowed cases:
   `SELECT anchor_year_group, count(*) AS n FROM mimiciv_hosp.patients GROUP BY 1` with a
   crafted small group → that row dropped, `rows_suppressed = 1`;
   `SELECT count(DISTINCT subject_id) AS n_subjects FROM mimiciv_hosp.admissions`;
   `SELECT count(*) AS n FROM information_schema.tables`; `DESCRIBE mimiciv_hosp.patients`.
   `runs.duckdb`'s `audit` view row count equals the number of lines. `tier("dev")`-marked: one
   allowed aggregate on `dev` via `mwh sql`; the audit line count increases by one.

## Out of scope

- Complementary suppression, `disclose.check`, `.disclosure.json` sidecars → EP-43.
- Run ledger (`runs/ledger.jsonl`), full `mwh runs` (`refresh` beyond `audit`, `list`, `show`) → EP-35.
- Owner row view (`owner_rows()`, row-view toggle + banner) → EP-58; export primitives → EP-59; audit browser page → EP-134.
- PreToolUse output-scanning hook — parked in `final-roadmap.md` (D-39).

## Verification / acceptance

- `uv run poe test -m ep_30` green on fixture; `tier("dev")`-marked test green; `uv run --group dev mwh verify EP-30` green.
- The wrapper **refuses** every crafted violation listed above in tests, and each refusal is audited (`allowed = false`, reason) — the governance acceptance for this brief.
- `%MWH_DATA_ROOT%\runs\audit.jsonl` exists after the dev test; `warehouse\runs.duckdb` has the `audit` view; `mwh sql --tier dev` prints suppressed aggregates with the footer and exits 3 on refusal.
- `CLAUDE.md` §2 and `DESIGN.md` §12 carry the wiring notes; the interim EP-21 `mwh sql` body is gone.
