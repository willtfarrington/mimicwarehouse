# Disclosure primitives: `disclose.suppress`, `mwh disclose check`, sidecars (EP-43)

The one written answer to "how is a small cell suppressed here, what does the release
gate refuse, and what does a `.disclosure.json` sidecar say?" - the prose twin of
`src/mimicwarehouse/disclose.py` (GOVERNANCE sections 5 and 7, DESIGN section 14,
D-31 / D-33 / D-40). Every later brief calls these primitives instead of re-implementing
suppression (GOVERNANCE section 5): the safe-query hook (EP-30), the QC profiles (EP-44),
the attrition renderer (EP-48), the app's warn badges (EP-58), the export primitives
(EP-59), the report engine (EP-130) and the disclosure-review tool (EP-133). Nothing on
this page is derived from patient data: every example is a crafted synthetic frame. All
MIMIC-IV analyses in this repository are retrospective.

## 1. The rule in one paragraph

A cell is **small** when its count `n` satisfies `0 < n < k`, with `k = 11`
(`Settings.k_suppression`, D-33). In the app a small cell shows a warn badge
(`disclose.warn_badges`, EP-58). Everywhere else - export, commit, report, return to a
Claude session - it is suppressed **complementarily**: the cell becomes null, a marker
column says so, and any other published number that would let a reader back the cell out
goes with it. Zero is never small (an empty cell says nothing about an individual), and
`k = 1` (the synthetic `fixture` / `demo` tiers may lower it) suppresses nothing.

## 2. `suppress(df, k=11, count_cols=None, group_cols=None, mode="table", complementary=True)`

Takes a pandas or Polars aggregate frame and returns `(df_out, SuppressionReport)` in the
same library. Count columns are auto-detected as **integer columns with count-like
names** - `n`, `n_*`, `*_n`, `count*`, `*_count`, `cnt`, `num_*`, `events`, `deaths`,
`numerator`, `denominator`, `subjects`, `patients`, `admissions`, `stays`, `rows`,
`distinct` and DuckDB's generated `count(...)` names - or listed explicitly. Group
columns (the keys a margin sums over) are every other non-float, non-marker, non-rate
column, or listed explicitly.

### Table mode

1. **Primary suppression.** Every count cell in `(0, k)` becomes null and the marker
   column `<col>_suppressed` is `true` on that row.
2. **Column margins.** Within one count column, every marginal total of the group
   columns is assumed publishable - the whole column when there is at most one group
   column (a `GROUP BY era` total is one query away), every proper subset of the group
   columns otherwise (row margins, column margins, the grand total). A margin with
   **exactly one** hidden cell also hides its next-smallest published cell, preferring
   a row that already lost a cell (fewer rows damaged, the same protection) and a
   non-zero value. The loop runs to a fixpoint, so a complement that opens another
   margin closes it too.
3. **Nested totals in a row.** Two count columns where the smaller never exceeds the
   larger on any row (`n` / `n_fit`, `n_units` / `n_positive`, `denominator` /
   `numerator`) are a total and a part of it; their difference is a cell nobody
   published. If both are published and the difference lies in `(0, k)`, the smaller one
   is hidden (`kind = derived`) - the EP-31 lesson, where the cohort `n` and the fitted
   `n_fit` differed by a handful of zero-cell rows (EP-33 amendment b).
4. **Rates.** A rate-like column (`share`, `rate`, `pct`, `percent`, `proportion`,
   `fraction`, `ratio`, `*_share`, `*_rate` ...) is blanked on every row that lost a
   count, because `share * n_units` restores `n_positive`.
5. **Idempotence.** Marker columns and nulls on input count as already hidden, so
   `suppress(suppress(df)) == suppress(df)`.

Example - the 2x2 with one small cell (`k = 11`):

| group | n_yes | n_no | -> | group | n_yes | n_yes_suppressed | n_no | n_no_suppressed |
|---|---:|---:|---|---|---:|---|---:|---|
| a | 5 | 100 | -> | a | | true | 100 | false |
| b | 50 | 60 | -> | b | | true | 60 | false |

`n_yes = 5` is primary; the `n_yes` column has one hidden cell and a publishable total,
so its other cell (50) is the complement. The report says `n_primary = 1`,
`n_complementary = 1`, `rows_suppressed = 2`. In a 3x3 grid the same rule hides a
2x2 rectangle (one primary, three complementary cells) and stops.

### Chain mode (attrition sequences; EP-48)

`mode="chain"` takes one count column holding a non-increasing sequence
`n_0 >= n_1 >= ...` (the row order is the step order) and protects the **drops**
`n_(i-1) - n_i`, which a reader derives from consecutive totals:

- a total in `(0, k)` is suppressed (null, `<col>_suppressed`);
- a drop in `(0, k)` is withheld, and **both** adjacent totals are coarsened to bands
  rounded to the nearest 10 (`995 -> ~1,000`; `<col>_banded = true`; the value in the
  frame *is* the rounded one - consumers never see the exact count);
- a `drop` column carries a drop only when both of its totals stay exact; every other
  drop is withheld (`drop_suppressed = true`), so no exact difference survives next to a
  band. This is the rounding rule the brief asked to document: bands leak a drop only to
  within about twice the band width, never exactly.

Example - `[1000, 995, 400]`, `k = 11`:

| step | n | n_banded | drop | drop_suppressed | rendered |
|---|---:|---|---:|---|---|
| all | 1000 | true | | false | `~1,000` |
| adults | 1000 | true | | true | `~1,000` |
| first stay | 400 | false | | true | `400` |

`render_cell(n, k, banded=...)` renders `<11` for a suppressed cell, `~1,000` for a
banded total and `1,234` (thousands-separated, `inventory.fmt_int`) otherwise; the
attrition renderer (EP-48) and the Mermaid diagrams use exactly these three forms.

### The safe-query hook

`safe.SUPPRESSOR` is `disclose.safe_suppressor`: the table-mode suppression above,
released **row-wise** - a row with any hidden cell is withheld from the result, so
`mwh sql` and every `safe_query` caller receive complete rows only. The row count
withheld is what `rows_suppressed` reports. Compared with EP-30's primary-only rule a
result may now lose complementary rows as well (the crafted small group in
`tests/ep/test_ep30.py` loses its `rest` row, whose published total was one query away).

**SGT-2 decision (D-31 addendum, decided here).** Extreme-value aggregates (`min`,
`max`, `mode`, `median`, quantiles) over subject-keyed columns stay admitted by
`safe_query` and receive **no per-column tightening** in this module: the row-wise
release form already ties every such value to a k-gated, complementarily suppressed
row, so a `max(anchor_age)` can only leave with a group of at least `k` subjects that
no margin can shrink. The residual risk - an extreme value that identifies one member of
a group of eleven - is the D-33 policy's accepted one, and dates are patient-shifted.

## 3. `check(path, k=11, allow_text=())` and the finding codes

The release gate over one artefact (`.csv .parquet .json .yaml .md .mmd .html .svg .png
.txt`). Every finding has a code, a status (`fail` or `warn`), a location and a
**value-free** detail (ids are masked the way `mwh guard` masks them - `2*******` -
lengths and row numbers only; any parser or engine error text is sanitised before it
enters a result, the DKB-2 rule). A result passes when no finding is `fail`.

| code | fails when | example (fails) | example (passes) |
|---|---|---|---|
| `ID_COL` | a column name / table header / JSON key is an identifier: the GOVERNANCE section 4 list (`subject_id`, `hadm_id`, `stay_id`, `note_id`, `emar_id`, `pharmacy_id`, `poe_id`, `transfer_id`, `caregiver_id`, `provider_id`, `order_id`, `microevent_id`, `labevent_id`, `chartevent_id` ...), the contract's identifier flags, or any `id` / `*_id` outside the allow-list (`itemid`, `codeset_id`, `phenotype_id`, `cohort_id`, `run_id`, `protocol_id`, `concept_id`, `patch_id`, `study_id`, `build_id`, `audit_id`, `snapshot_id`, `job_id` ...) | example: `tests/fixtures/disclose/bad_ids.csv` - a `subject_id` column (its values are fixture-band ids; the *name* is the violation) | example: `phenotype_id,version,n` |
| `ID_BAND` | an 8-digit integer inside a real MIMIC id band (10-19 million `subject_id`, 20-29 million `hadm_id`, 30-39 million `stay_id`; the guard's scanner, bands as patterns) appears in text, Markdown, Mermaid, HTML, SVG, a string value, a JSON / YAML value under a non-count key, or a non-count integer column; count-like and telemetry columns (`n_rows`, `total_bytes`, `size`, `wall_ms` ...) are exempt because a large row count is not an id (EP-170 amendment 2) | example: a Parquet column `value` holding `20 000 005` (the test builds it from the guard constants) | example: `n_rows = 12 345 678` (a count), any fixture id `>= 90 000 000` |
| `FREE_TEXT` | a string column / header is named like free text (`text`, `note`, `comments`, `value_text`, `narrative`, `*_text` ...) or a contract free-text column; or, unless allow-listed, its longest value exceeds 64 characters (the committed-text canon rule 4), its median length exceeds 80, or it has more than 500 distinct values. `--allow-text <col>` (or `allow_text=`) admits dictionary labels; `description`, `short_description`, `refusal_reason` and `error` are allow-listed by default (the safe-query label columns) | example: a Parquet `text` column of 200-character strings | example: a `label` column of unit strings; `DATA-DICTIONARY.md`'s `description` column |
| `SMALL_CELL` | an unmarked count cell in `1..k-1` in a frame or a Markdown / HTML table (headers naming levels, codes, versions, units of measure or telemetry - `level`, `stage`, `code`, `version`, `files`, `MB`, `s`, `%` ... - are exempt unless count-named); a cell marked suppressed that still carries a value; `n = 4` / `events: 3` in prose, a Mermaid label or SVG text; two published nested totals whose difference lies in `1..k-1`; an attrition-shaped table (first header `step` / `criterion` / `stage` ...) whose consecutive totals differ by `1..k-1` | example: `tests/fixtures/disclose/bad_small_cell.md` - a table cell of 7; a `.mmd` with `n = 4`; the EP-31 tracer report's cohort table (its chain drops; EP-53 promotes it through chain mode) | example: a `.mmd` with `n = <11` or `n = ~1,000`; a table whose small cells read `<11` or `suppressed (< 11)` |
| `EMBEDDED_ROWS` | an HTML `<script>` JSON block, a Vega / Vega-Lite `data.values` or `datasets` array, a Plotly `newPlot` trace or any JSON / YAML record or scalar array holds more than 1 000 rows, or its records carry identifier keys (also `ID_COL`); more than 200 rows is a **warn** | example: an HTML file with a 5 000-row embedded Vega dataset | example: a Vega-Lite spec whose `values` are the 40 aggregate rows it plots |
| `NO_SOURCE` | a `.png` / `.svg` figure has no sibling source table (`<stem>.vl.json`, `.json`, `.csv` or `.parquet`); when one exists it is checked and its findings become the figure's (`where` = `source <name>`) | example: `fig.png` alone | example: `fig.png` beside a clean `fig.csv` |
| `OVERSIZE` | an image exceeds the 20 000 KiB commit bound (guard G5); above 5 MiB is a **warn** (a rendered aggregate figure is small - does the image embed data?) | example: a 25 MiB PNG | example: a 40 KiB PNG |

What the gate deliberately does **not** do: it does not scan prose for numbers other
than the `n = <count>` shapes above (a "3 tables" in a sentence is not a cell), it does
not know which published totals live in *other* files, and it cannot see attribute
disclosure (a 100 % cell over a group of eleven) - those stay the reviewer's job
(EP-133) and are parked in `roadmap/final-roadmap.md` (DIS-4).

In-process forms: `check_frame(df, k, allow_text=)` returns the findings for a frame;
`assert_clean(df, k)` raises `DisclosureError` naming them (EP-59 / EP-130 call it before
writing); `check_table(header, rows, k)` is the text-table rule set on its own.

## 4. The sidecar and `mwh disclose verify`

`write_sidecar(path, result, k, reviewer="owner")` writes `<artefact>.disclosure.json`
beside a **passing** artefact (a failing result never gets one):

```json
{
  "schema": "mimicwarehouse.disclosure/1",
  "path": "00-staging-benchmark.md",
  "sha256": "<sha256 of the artefact bytes>",
  "size": 12345,
  "k": 11,
  "passed": true,
  "checks": [{"code": "ID_COL", "status": "pass", "detail": "clean"}, "..."],
  "n_warn": 0,
  "allow_text": [],
  "reviewer": "owner",
  "timestamp": "2026-09-07T00:00:00+00:00",
  "tool_version": "0.1.0",
  "git_sha": "abc1234"
}
```

`checks` holds one entry per code (`pass` / `warn` / `fail` with a count and the first
finding's detail); `path` is the artefact's file name, so the pair can move together.
`verify(path)` / `mwh disclose verify <path>` re-hashes the artefact: it passes only when
the sidecar exists, records `passed = true` and its `sha256` equals the bytes on disk -
a one-byte edit fails it (exit 1; no sidecar is exit 2). GOVERNANCE section 3 admits an
aggregate table, figure or model card into `docs/` or git only with this sidecar; the
manifests-of-hashes-and-counts exception (D-40 addendum) needs none.

## 5. The CLI

```powershell
cd mimicwarehouse
uv run --group dev mwh disclose check tests/fixtures/disclose/bad_ids.csv          # exit 1, ID_COL
uv run --group dev mwh disclose check tests/fixtures/disclose/bad_small_cell.md   # exit 1, SMALL_CELL
uv run --group dev mwh disclose check tests/fixtures/disclose/good_aggregate.csv --write-sidecar   # exit 0 + sidecar
uv run --group dev mwh disclose check DATA-DICTIONARY.md docs/analyses/00-staging-benchmark.md --write-sidecar
uv run --group dev mwh disclose check runs/<run_id>/phenotype_prevalence.md --k 11 --allow-text label --json
uv run --group dev mwh disclose verify DATA-DICTIONARY.md                          # exit 0 / 1 / 2
```

`check` prints one rich table per artefact (code, status, detail) plus a findings table,
and exits `EXIT_OK` 0 when every artefact passes, `EXIT_FINDINGS` 1 on any failing
finding, `EXIT_USAGE` 2 on a missing path, an unsupported type or an unreadable file
(the message on stderr through `console.fail`). `--k` defaults to
`settings.k_suppression`; `--write-sidecar` writes sidecars for the passing artefacts
only; `--allow-text` repeats. The regenerated `DATA-DICTIONARY.md` needs a fresh sidecar
(`mwh disclose check DATA-DICTIONARY.md --write-sidecar`), as does the staging benchmark
note after `mwh runs benchmarks --out`; `test_ep43` verifies both committed pairs.

## 6. Where the rules come from

- GOVERNANCE section 5 (D-33): the small-cell rule, warn in-app, suppress on release,
  complementary suppression, one implementation.
- GOVERNANCE section 7 (D-40): the release gate and the sidecar.
- EP-30 / EP-33 (D-31 and its SGT-2 addendum): the `SUPPRESSOR` hook, the k floor on the
  credentialed tiers, the sanitised error texts.
- EP-31 / EP-33 amendment b: nested totals as a margin.
- EP-170 amendment 2 and `docs/committed-text.md`: band scanning skips counts; no
  identifier column names and no strings over 64 characters in committed artefacts.
- EP-33 amendment a: the retroactive checks on `DATA-DICTIONARY.md` (EP-29),
  `docs/analyses/00-staging-benchmark.md` (EP-32) and EP-42's
  `runs/<run_id>/phenotype_prevalence.md`.
