# EP-32 — Capstone #0: staging benchmark note + docs/analyses convention

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-28 (Verify full staging), EP-31 (Tracer bullet: first-ICU-stay adults → in-hospital mortality) · **Blocks:** EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Integers in `00-staging-benchmark.md` and the completion
> note are thousands-separated via `inventory.fmt_int` (guard G4 refuses bare 8-digit tokens
> starting 1–3) — build the renderer that way [FC-16]. Shorthand per the README notation table;
> header facts unchanged.

## Context

Every phase ends with a capstone (**D-8**) and the whole portfolio is read by two audiences
(**D-1**: DS/ML hiring managers and clinical-informatics readers). This brief establishes
the `docs/analyses/` convention that all later case studies follow (hupsim precedent:
"What it deliberately does not claim" + Reproduction blocks) and writes the first entry,
`00-staging-benchmark.md`, from the benchmark ledger (`runs/benchmarks.jsonl`, EP-19/EP-28).
The benchmark note contains build telemetry only (row counts that are also published in
`validate.sql`, bytes, timings, RSS, file counts) — no patient-level aggregates — so it can
be committed before `mwh disclose check` exists; it still gets a header saying its
`.disclosure.json` sidecar is added retroactively at EP-43 (GOVERNANCE §7). The tracer
bullet's results (EP-31) stay under `runs/` until EP-43/EP-53 promote them; here the
tracer is only the worked example of a Reproduction block (run id + command, no numbers).

## In scope

1. **`docs/analyses/README.md`** — the convention: file naming `NN-slug.md` (two digits,
   allocation order); required sections in order — *Question* · *Data & tier* (tier,
   snapshot ids, "MIMIC-IV analyses are retrospective") · *Method* · *Results* (aggregates
   only, post-suppression, figures as Vega-Lite spec + PNG without embedded row arrays) ·
   *What it deliberately does not claim* · *Reproduction* (run ids, exact `uv run …`
   commands, protocol hash when frozen) · *Provenance footer* (git sha, snapshot ids, env
   hash, DuckDB version) — plus a **claim-type label** line near the top (exploratory /
   confirmatory / predictive / associational / causal) and a one-line *reader guide* for
   each audience (D-1). Disclosure rule: every case study and every table/figure it embeds
   passes `mwh disclose check` and carries a `.disclosure.json` sidecar (from EP-43;
   earlier files list "sidecar pending EP-43" in their header). Index table of case
   studies (00 now; a row for the tracer marked "pending promotion at EP-43/EP-53").
2. **Ledger renderer** — `mimicwarehouse.dag.benchmarks.render_markdown(summary) -> str`
   over EP-28's `summarize()` and a `mwh runs benchmarks [--format table|md] [--out PATH]`
   subcommand (EP-35 adds the other `mwh runs` verbs); with `--out` it replaces the block
   between `<!-- benchmarks:begin -->` and `<!-- benchmarks:end -->` markers in an existing
   file, leaving the narrative untouched.
3. **`docs/analyses/00-staging-benchmark.md`** — Question (how long, how big, how fast is
   the raw → lake staging on this machine); Data & tier (full; core snapshot id; build
   ids); Method (loader/DAG design in three sentences, machine facts, DuckDB settings);
   Results — the rendered table per table (rows, CSV GB, Parquet GB, ratio, files, pass 1 s,
   pass 2 s, total s, MB/s, peak RSS) with totals, versus the DESIGN §3 estimate; the
   bucket-count and Defender observations from EP-28; What it deliberately does not claim
   (single laptop runs, no patient data, not a DuckDB benchmark, thermal effects
   unquantified); Reproduction (the exact `mwh build … --background --job …` commands and
   `mwh runs benchmarks --format md`); Provenance footer; header line
   "disclosure sidecar pending EP-43".
4. **Cross-links** — `roadmap/README.md` is not edited here (EP-33 does the ☑); add the
   `docs/analyses/` link to `mimicwarehouse/README.md`; dated `DESIGN.md` §15 note for
   `mwh runs benchmarks`.
5. **Test** (`tests/ep/test_ep32.py`, `@pytest.mark.ep_32`, fixture) — `render_markdown`
   on a synthetic ledger produces a table with the expected columns; the marker
   replacement is idempotent; `docs/analyses/README.md` and `00-staging-benchmark.md`
   exist, contain every required section heading, and pass the guard scanner (no
   real-band ids, no data-shaped content).

## Out of scope

- Disclosure sidecars and `mwh disclose check` → EP-43 (retroactive check of these two files is listed there via the P2 re-plan).
- Promoting the tracer's numbers into a case study → EP-53 (Capstone #1) after EP-43.
- Docs site build (MkDocs) → EP-160; case-study compilation → EP-161.

## Verification / acceptance

- `uv run poe test -m ep_32` green; `uv run --group dev mwh verify EP-32` green.
- `docs/analyses/README.md` and `docs/analyses/00-staging-benchmark.md` exist; every number in the benchmark table reproduces from `%MWH_DATA_ROOT%\runs\benchmarks.jsonl` via `uv run --group dev mwh runs benchmarks --format md`; links resolve; the guard hook passes on commit.
- The tracer appears in the index only as a pending row with its run id — no results copied.

> **Completion note (2026-08-30).** Executed in one session (under the S budget; power
> mode confirmed *Best performance* first). Shipped: `dag.benchmarks.render_markdown()`
> + `render_rows()` + `replace_marked_block()` (exactly-one-marker-pair check,
> idempotent, line-endings preserved) and **`mwh runs benchmarks [--tier] [--kind]
> [--format table|md] [--out PATH]`** in `runs_cli.py` (defaults `full`/`stage`, so the
> bare `--format md` reproduces the note's table; refusals exit 2);
> `docs/analyses/README.md` (convention: `NN-slug.md` naming, the seven required
> sections, claim-type label + D-1 reader guides, disclosure rule, index with the
> tracer as a pending row citing run `20260830T011037-full` — no numbers copied) and
> `docs/analyses/00-staging-benchmark.md` (telemetry only; header "disclosure sidecar
> pending EP-43"; Results table generated in place by
> `mwh runs benchmarks --out docs/analyses/00-staging-benchmark.md` — run twice,
> second run "unchanged": idempotent against the real ledger). The rendered totals
> reproduce EP-28's measurements exactly (97.19 GB CSV → 7.05 GB Parquet, 13.8x,
> 2,407 part files, peak RSS high-water 24,563 MB); core snapshot
> `b1fc5313…410eca`; the seven latest stage build ids are listed in the note's
> Data & tier. Cross-links per item 4: workspace README `docs/analyses/` row linked,
> DESIGN §15 dated note (+ one-line `runs_cli` map tweak); `roadmap/README.md`
> untouched — **the EP-32 ☑ hash is EP-33's** per item 4. Tests:
> `tests/ep/test_ep32.py`, 14 fixture tests (renderer columns/totals/G4-cleanliness on
> a synthetic ledger, marker idempotence + 5 refusal shapes, CLI md/--out/refusals via
> CliRunner, both docs' required content, `guard.scan` clean on both docs).
> `poe test -m ep_32` **14 passed** · `mwh verify EP-32` green · full `poe check`
> **720 passed** · `mwh guard --all-tracked` + `mwh guard docs/analyses` clean ·
> `poe roadmap-check --strict` 0 errors, 0 warnings.
