# Committed-text hygiene canon (EP-33 B4)

The one page for the rules every committed human-readable file follows - docs, tables,
CLI strings, run-folder contents and file names. Each rule names the code that enforces
it and the tests that already assert it, so a later brief cites this page instead of
rediscovering the rule. This is policy only: nothing here is derived from data, and the
illustrative tokens below are shapes (`1xxxxxxx`), never real values.

Why one canon: git history is permanent and the remote goes public at v1.0.0 (D-41), so
the pre-commit guard (`mwh guard`, EP-4; GOVERNANCE section 3, DESIGN section 15) refuses
anything shaped like a real MIMIC identifier - rule G4 flags any isolated 8-digit token
starting with 1, 2 or 3 whose value lies in a real id band. Every rule below exists so
that ordinary, legitimate text never looks like an id, or so that a legitimate token is
exempted in exactly one documented way.

## The six rules

### 1. Integers in human-facing text go through `inventory.fmt_int`

Every integer in a docs table, a case study, a console footer or a rendered Markdown block
is thousands-separated (`123,456,789`) or `-` when absent - the only way an 8-digit row
count can never match G4 (a comma is a boundary the scanner does not cross). Raw integers
stay raw only in machine outputs (`--json` payloads, `runs/` JSON), which `console.emit_json`
writes and which are never committed.

- Enforcer: `mimicwarehouse.inventory.fmt_int` (guard G4 refuses what it prevents);
  `dag.benchmarks.render_rows` and every report renderer call it.
- Asserted by: `test_ep30` (`test_sql_cli_free_form_footer_and_fmt_int`), `test_ep32`
  (`test_render_markdown_columns_and_totals`, `test_render_markdown_never_emits_bare_band_integer`),
  `test_ep33` (the rendered benchmark table is ASCII).

### 2. CLI strings are ASCII, or pass through `console.console_safe`

New command-line strings stay ASCII; a string that must carry a glyph goes through
`console.console_safe`, which replaces what the current stream cannot encode instead of
crashing on a cp1252 host. `PYTHONUTF8=1` (`.claude/settings.json`) and the `mwh` entry
point's UTF-8 reconfiguration are the belt; `console_safe` is the braces. Committed YAML
under `schema/` and `fixtures/vocab/` is ASCII for the same reason (Risk 13).

- Enforcer: `mimicwarehouse.console.console_safe` (and `console.run`); docstrings may keep
  the repository's existing typography - the rule is about what reaches a console.
- Asserted by: `test_ep09` (`test_yaml_files_are_single_document_tag_free_ascii_and_guard_clean`),
  `test_ep31` (the tracer report `isascii()`), `test_ep167` (the `verify._console_safe`
  alias and the replacement behaviour), `test_ep33` (`render_markdown` output is ASCII).

### 3. No compact dates or run ids in committed file names

Content and file names are scanned by two different patterns, deliberately:

- `guard.ID_TOKEN` (content) requires the 8-digit run to be isolated - a token abutting a
  letter, digit or dot never matches. So an inline run id such as
  `2xxxxxxxT120000-full-abc123` in prose or a reproduction block is legal: the date is
  glued to the `T`. A *bare* compact date (`2xxxxxxx` on its own) still matches and is
  refused - write ISO dates with hyphens (`2026-08-31`).
- `guard.PATH_ID_TOKEN` (names) uses digit-only boundaries, so `run-2xxxxxxxT120000.md`,
  `stay_3xxxxxxx.parquet` or `subject-1xxxxxxx.png` are all refused, and there is no pragma
  escape for names. A tracked name never carries a run id, a compact date or an id: use
  `NN-slug.md` (docs/analyses), an ordinal, a slug or a hash.

- Enforcer: `guard.PATH_ID_TOKEN` vs `guard.ID_TOKEN` (`guard.check_entry` applies both);
  since EP-33 (retro SGD-3) the content scan also covers `.ipynb` JSON (source cells
  included) and the script/markup types in `guard.TEXT_EXTENSIONS`.
- Asserted by: `test_ep04` (`test_g4_every_band_is_recognised_and_compact_dates_are_not_exempt`,
  `test_g4_only_text_files_are_scanned`), `test_ep165` (`test_g4_path_tokens_are_hits_with_masked_detail`),
  `test_ep33` (the filename asymmetry, the tracked-name sweep over `git ls-files`, the
  notebook-source and script-type scans).

### 4. Run-folder contents carry no identifier columns and no long strings

Nothing written under a run folder (`runs/<run>/...`: `attrition.json`,
`descriptives.json`, `model.json`, `report.md`, `manifest.json`) may carry an identifier
column name (`subject_id`, `hadm_id`, `stay_id`, `note_id`, ... - the contract's identifier
set) or any string value longer than 64 characters: model term labels are normalised to
`<var>=<level>` and truncated. The bound is the same one the safe-query gate uses for its
free-text heuristic.

- Enforcer: `tracer.VALUE_MAX_CHARS`, which deliberately mirrors `safe.FREE_TEXT_MAX_CHARS`
  instead of importing it (`tracer.py` is on the `mwh` start-up path, `safe.py` is not -
  the import budget of DESIGN section 15); the two are asserted equal.
- Asserted by: `test_ep31` (the run-folder sweeps: `test_run_folder_has_all_five_files`,
  `test_dev_run_completes_and_run_folder_is_clean`), `test_ep33`
  (`tracer.VALUE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS`).

### 5. The `mwh-guard: allow` pragma carries a rationale

A line that legitimately carries a band-shaped integer (an upstream row count, a documented
example) is exempted by the pragma `guard.ALLOW_PRAGMA` on the same line - and only that
line. Two forms are allowed:

- the vendoring form, appended mechanically by `concepts.vendoring` to the vendored
  mimic-code SQL: `-- mwh-guard: allow (row count, not an id)`
  (`vendoring.GUARD_PRAGMA`);
- a hand-written form with a parenthesised rationale after the pragma, e.g.
  `# mwh-guard: allow (documented fixture example)`.

A pragma without a rationale, or a pragma used to smuggle a real id, is a review finding.
The pragma never applies to file names (rule 3).

- Enforcer: `guard.ALLOW_PRAGMA` (`guard.id_band_hits` skips the line);
  `vendoring.GUARD_PRAGMA` is the only mechanical writer.
- Asserted by: `test_ep04` (`test_g4_pragma_exempts_the_line_only`), `test_ep33` (the
  pragma sweep: every tracked `src/` and `docs/` pragma line that exempts a band token
  carries one of the two rationale forms - asserted on paths only).

### 6. Cite DOIs or stable URLs, never bare PMIDs

Modern PubMed identifiers are 8-digit integers starting with 1, 2 or 3 - exactly the shape
G4 refuses - so the reading list and the resource register cite a DOI
(`https://doi.org/...`) or a stable `https://` URL per entry, never `PMID: nnnnnnnn`.
Checked-on dates are hyphenated ISO (`YYYY-MM-DD`), never compact.

- Enforcer: the citation-hygiene rule in `docs/resources/reading.md` (EP-15, amended
  EP-7) - guard G4 is what makes a bare PMID fail.
- Asserted by: `test_ep15` (every entry carries a DOI or `https://` link; category
  headings), `test_ep13` / `test_ep14` / `test_ep15` (`test_checked_on_date_is_hyphenated_iso`),
  `test_ep33` (no `docs/resources/*.md` line matches PMID-followed-by-digits).

## Where the rules are pointed to

`tests/README.md` (next to the churn rule), `docs/analyses/README.md` (file naming),
the `guard` module docstring (rule G4) and DESIGN section 15. `docs/gotchas.md` links here
rather than restating any rule.
