# EP-48 — Attrition diagram renderer

**Size:** S · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-54 (Re-plan P3), EP-62 (Cohort Builder page)

> **EP-33 amendment (2026-09-01).** Header facts unchanged. Every artifact this brief writes
> passes through `disclose` (EP-43) and obeys `docs/committed-text.md` (the EP-33 hygiene
> canon) — in particular rule 3: committed figure/table file names carry no run ids or compact
> dates (`attrition.mmd`, `attrition.png`, `attrition.csv`, `attrition.md` — never
> `attrition_<run_id>.png`); run ids appear only inside the footer/Markdown text. The
> suppressed frame comes from `cohort.attrition(...)` -> `disclose.suppress(mode="chain")` (the
> `SUPPRESSOR` seam is `(df, k, count_columns) -> (df, rows_suppressed)`; EP-43 amendment (c));
> files are written with `fsio.atomic_write_text`; CLI errors go to stderr via `console.fail`
> (`console.EXIT_FINDINGS` when `disclose.check` refuses a write).

## Context

Every cohort deserves a STROBE-style attrition (flow) diagram, and it must be safe to paste into a
report, a docs page or the Cohort Builder page. EP-47 materializes the attrition table (step,
label, n_units, n_subjects, dropped) and exposes it through `cohort.attrition(...)`, which already
applies `disclose.suppress(mode="chain")` (EP-43). This brief renders that table as Mermaid
(primary — GitHub and Streamlit both render it) with an Altair fallback (DESIGN §9), stores the
artifacts in the run directory, and proves with `mwh disclose check` that the outputs carry no
exact small cell (D-33, D-40). The renderer lives in `src/mimicwarehouse/cohort/attrition.py`;
Vega-Lite specs are plain JSON that EP-64+ `viz/` may later absorb. No data beyond the attrition
aggregates is touched.

## In scope

1. **Mermaid renderer** (`src/mimicwarehouse/cohort/attrition.py`) — `render_mermaid(df,
   title=None, direction="TD") -> str`: one node per step (`"Step k — label<br/>n = X units ·
   Y subjects"`), a side node per drop (`"Excluded: n = Z"`), suppressed cells rendered exactly as
   `disclose.render_cell` (`<11`, `~` bands) — the renderer never sees raw counts because it takes
   the suppressed frame from `cohort.attrition(...)`; a footer node with `id@version`, tier and
   run id; deterministic node ids; escaping of `"`/`<>` in labels.
2. **Altair fallback** — `render_altair(df) -> alt.Chart` (horizontal funnel bar of `n_units`
   per step with drop annotations; suppressed values omitted, not zeroed; theme from `theme.py`
   EP-5) and `to_vegalite(df) -> dict`; PNG export via `vl-convert-python` (BSD-3; add to the
   `core` group if not already present) so reports can embed a static image.
3. **Artifacts + CLI** — `save_attrition(run_id | id@version, tier, out_dir=None)` writes
   `runs/<run_id>/figures/attrition.mmd`, `attrition.vl.json`, `attrition.png` and the suppressed
   `attrition.csv`; `mwh cohort attrition <id@version> --tier <t> --format mermaid|altair|all
   [--out <dir>]` prints Mermaid to stdout or writes files; each written file passes
   `disclose.check` (called inside `save_attrition`; refuse to write otherwise). `save_attrition`
   also writes `attrition.md` (suppressed table + footer with `Claim type: exploratory (cohort
   description)`, the retrospective statement, id@version, tier, run id) through the same
   `disclose.check` path.
4. **Tests + docs** (`tests/ep/test_ep48.py`, `@pytest.mark.ep_48`; fixture, `dev`) — a crafted
   attrition frame with a drop of 4 renders `<11` and no literal `4` in the `.mmd`; a clean chain
   renders exact counts; Mermaid output parses (balanced arrows, unique ids; a minimal grammar
   check); the Vega-Lite JSON has only the aggregate columns; every artifact passes
   `disclose.check`; on dev, `mwh cohort attrition first_icu_adults@1.0.0 --tier dev --format all`
   writes the four files. `docs/methods/cohorts.md` gains an "attrition diagram" section with a
   rendered example from the fixture tier (synthetic data, rendered through the same suppression
   so it shows no cell < 11; `mwh disclose check` on the docs file passes).

## Out of scope

- Cohort Builder page embedding (`st.markdown` Mermaid / `st.altair_chart`) → EP-62.
- Report engine integration → EP-130; export gallery → EP-134.
- anywidget/D3 diagram components → parked (`final-roadmap.md` § 8–10).

## Verification / acceptance

- `uv run poe test -m ep_48` green on fixture and dev; `uv run --group dev mwh verify EP-48` green.
- `uv run --group dev mwh cohort attrition first_icu_adults@1.0.0 --tier dev --format all --out
  %MWH_DATA_ROOT%\runs\<run_id>\figures` writes `.mmd`, `.vl.json`, `.png`, `.csv` and
  `attrition.md` (claim-type label + retrospective statement in the footer), and
  `uv run --group dev mwh disclose check` on that directory exits 0.
- The fixture-tier example diagram in `docs/methods/cohorts.md` renders on GitHub (Mermaid block).

> **Completion note (2026-09-16).** Executed as briefed on fixture + dev (S, one session).
> Shipped: `src/mimicwarehouse/cohort/attrition.py` (the diagram model `Cell` / `StepRow` /
> `Chain` prepared from the accessor's suppressed frame by `prepare` / `from_attrition`;
> `render_mermaid` — one node per step with `units n = X` / `subjects n = Y`, a dotted
> `Excluded at <step>` node per non-zero drop, a footer node `id@version - tier - run -
> def_hash - k` pinned under the last step with an invisible `~~~` link, deterministic
> ids, entity-code escaping of user text, palette colours from `theme.py`, front-matter
> `title`, `direction`; `render_altair` / `to_vegalite` / `to_png` — the Vega-Lite funnel
> with the counts beside each bar and the exclusion between the steps, the EP-5 theme
> merged at serialisation, PNG through `vl-convert-python`; `render_markdown` — claim type
> `exploratory (cohort description)`, the retrospective sentence, a disclosure line, the
> table, the diagram, the spec's *what it does not claim*, the EP-35 reproduction block;
> `save_attrition` — the five files staged under `<data_root>/tmp`, each through
> `disclose.check`, published only when all pass, recorded in the build run's `figures`),
> `cohort/cli.py` (`mwh cohort attrition … --format table|mermaid|altair|all [--out DIR]
> [--title] [--direction] [--json]`), `cohort/build.py` (`dropped_*_small` markers from
> `suppress_attrition`), `disclose.py` (two gate amendments, below), `pyproject.toml` +
> `uv.lock` (`vl-convert-python` → core; removed from `ui`), `tests/ep/test_ep48.py` (9
> fixture + 1 dev tests), `docs/methods/cohorts.md` §8 (the fixture-tier example between
> `<!-- attrition:begin/end -->` marks; §8 → §9 renumbered; two hand-written headers
> renamed — `notes` → `remarks`, the long predicate header → `predicate`),
> `docs/methods/disclosure.md` (EP-48 note), DESIGN §9 / §15 notes, D-33 + D-40 addenda,
> README (State row, dependency paragraph, quick start), `docs/gotchas.md` §3.
>
> **Interpretation choices.** (1) **The cell forms.** `render_cell`'s three (`1,234`,
> `<11`, `~1,000`) plus two the chain needs: a drop withheld only because a neighbouring
> total is banded or below k is drawn as the **rounded difference of the released totals**
> (`~20`; derivable by any reader, and honest where `<11` would be false — on dev the
> hf cohort's hospice step drops `~20` subjects, which EP-47's table prints as `<11`),
> and `suppressed` marks a total withheld by the **pair guard**: `disclose.check` refuses
> two published nested totals whose difference lies in `(0, k)`, and a step's
> `n_units - n_subjects` is such a pair, so the subjects total and the two drops beside
> it are withheld wherever the *written* difference would be small (a banded units total
> counts with its band — the fixture example's `idx` step fires on exactly that
> artefact); `<11` therefore always means "below k". (2) **The `cohort` step** is the
> materialisation of the last criterion's result and excludes nothing by construction;
> it carries no drop cell and no exclusion node (chain mode withholds its zero drop
> beside a banded neighbour, and the first render showed it as `~10`). (3) **Drops in
> the Markdown table are `n = X` text cells** so the gate's prose rule verifies each one
> while its nested-totals heuristic — which pairs *any* two integer columns of a table —
> cannot flag a drop column against a total (a false positive that hiding a drop cannot
> cure, since the drop stays derivable from the totals). (4) **Two gate amendments**
> (`disclose.py`): `check`'s JSON walk treats a record array's nested values as structure
> while its scalar keys stay a table — before, a layered Vega-Lite spec's `layer[].encoding`
> serialised into a 473-char "free text" column and no layered spec could pass; and the
> Markdown header `default` joined `EXEMPT_HEADER_RE` beside `value` / `threshold`
> (a schema reference's `min_rows` default of `1` read as a small cell). `test_ep43`
> re-run green. (5) **`docs/methods/cohorts.md` passes the gate with `--allow-text`**
> for EP-46's prose-table headers (`meaning type default fields remarks predicate
> definition` — the acceptance command in the README); the §8 section alone passes with
> no allowance, and `test_ep48` asserts both. The page never passed before EP-48 (it was
> never checked); restructuring EP-46's generated schema tables to satisfy a heuristic
> aimed at data artefacts was not worth it. (6) **Figures in a closed run's manifest**:
> `save_attrition` rewrites the build run's `manifest.json` `figures` map atomically
> (status / ledger line untouched) so `mwh runs show` and EP-134 find the files; an
> `--out DIR` elsewhere records nothing. (7) **Altair 6.2.2 / Vega-Lite v6.4.1 /
> vl-convert 1.9.0** (not the brief's Altair 5.5): a field sort is dropped on a layered
> chart with filter transforms (explicit list sort instead), inline data is consolidated
> into `datasets` (re-inlined under `data.values` for a self-contained spec), the theme
> is enabled as a context manager; lore in `docs/gotchas.md` §3. (8) `vl-convert-python`
> moved to the **core** set as the brief says (the FC-9 placement question EP-54 was to
> settle; the `ui` group no longer lists it — the owner-decision list below).
>
> **Runs.** fixture: `mwh build --tier fixture` (the full DAG, first build of the tier at
> the data root) run `20260917T000646Z-5955d0`, 144 steps, ≈ 2 min; the cohort runs
> `20260917T000801Z-a64c79` (tracer: ~80 → ~70 → ~70 → ~70 → 65 units) and
> `20260917T000801Z-791191` (hf: 186 → 186 → 88 → 88 → 88 → 77 → 77 units) — the docs
> example is the tracer render (`test_ep48` re-renders it on the session lake, run id
> aside). dev: the artefacts were written into the EP-47 build runs
> `20260916T224707Z-ec19ba` (tracer; every cell exact) and `20260916T224732Z-2920bf` (hf;
> subjects 11,267 → 1,642 → 1,642 → ~1,620 → ~1,620 → 1,617 with the hospice drop `~20`
> and the washout drop `<11`) — ten files, `mwh disclose check` exit 0 on all ten,
> `figures` recorded in both manifests; a five-file write takes ≈ 2–3 s (the PNG
> dominates). No full-tier run (the brief's tier is fixture+dev).
>
> **Gates.** `uv run poe test-dev -m ep_48`: 10 passed (9 fixture + 1 dev);
> `uv run mwh verify EP-48`: 9 passed; `uv run poe check` green — ruff check, `ruff
> format --check`, pyright 0 errors, pytest **1,072 passed** (52 dev/full/demo probes
> deselected) in 562 s; `mwh guard` clean over the 15 changed / new files; `poe
> roadmap-check --strict` 0 errors, 0 warnings; `mwh disclose check docs/methods/cohorts.md
> --allow-text …` (the README command) exit 0. Earlier tests: `test_ep43` + `test_ep47`
> re-run green (25 passed) after the gate / accessor amendments; no earlier test module
> edited (roadmap CMP-6 rule).
>
> **Owner decisions at the interactive review (2026-09-16, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes). (2)
> `vl-convert-python` stays in the **core** set as briefed — rejected: back to the `ui`
> group (FC-9 is settled here; EP-54 records it). (3) Keep the rendering policy of the
> D-33 addendum (the pair guard's `suppressed`, withheld drops as the rounded released
> difference, no exclusion on the `cohort` step) — rejected: `<11` for every withheld
> cell as the EP-47 table prints, and the option of changing that table to match (EP-62
> may unify the two surfaces). (4) Keep both gate amendments (the nested-array walk and
> the `default` exempt header) — rejected: reverting either. (5) The docs acceptance is
> `mwh disclose check docs/methods/cohorts.md --allow-text …` (the README command) plus
> the section-wise check with no allowance — rejected: rewriting EP-46's schema-reference
> renderer to pass flag-free, and checking section 8 only. Routine choices logged above:
> the figures recorded in the closed build run's manifest, the `n = X` text drops in the
> Markdown table, the two renamed hand-written headers, the docs example's run id from
> the first fixture-tier build at the data root.
>
> **Handed on.** EP-62: `render_mermaid` (→ `st.markdown`) and `render_altair` (→
> `st.altair_chart`) take the `Attrition` result or its `--json` rows; `records()` is the
> tooltip payload. EP-59 / EP-130: `save_attrition`'s stage-check-publish shape and
> `to_png` are the figure-export precedent; the `.vl.json` is self-contained
> (`data.values`). EP-54: FC-9 settled (core); consider whether `mwh cohort attrition`'s
> rich table should adopt the renderer's drop forms; the checker's nested-totals
> heuristic over arbitrary integer columns of a Markdown table (why the drops are text
> cells) is worth a note in the P3 retro.
