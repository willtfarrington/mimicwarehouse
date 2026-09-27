# EP-173 — Debt sweep (P4): carried-low fixes

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-33 (Re-plan P2), EP-54 (Re-plan P3) · **Blocks:** EP-55 (Latency marts A: first-day features + itemid rollups ⏱)

## Context

Allocated at the P3 re-plan (EP-54, 2026-09-18; owner decision **D-47** item 4) as P4's
per-phase slot, re-purposed from wheel fights — none is open (D-15 addendum) — to debt. The
EP-33 discovery audit left 60 carried-low findings (`retro-p2-findings.md` § "Index -
carried low findings", parked as `final-roadmap.md` AUDIT-1), and EP-54 re-triaged them
against the P3 code with file:line anchors (`retro-p3.md` § Carried-low re-triage): seven
are closed as fixed, two obsolete, one by docs; this brief lands the **fix-now** rows — one
governance hole first — and *authors* (never applies) the session-guard package. Tier
`fixture`: nothing here reads the data root; the safe-query refusal tests run against the
fixture catalog. Sized S with a hard order: item 1 must land; at ≈ 1 h stop and park whatever
is left back to AUDIT-1 with a note (the P3 lesson: S briefs that touch a governance or
diagnostic surface run 2–4×, roadmap Risk 11). The P3 → P4 name corrections of the roadmap
README apply; every fix is a code change under the EP-33 canons (`docs/gotchas.md` §6).

## In scope

1. **SGT-5 — the safe-query forbidden-function list** (`safe.py`: `FORBIDDEN_FUNCTION_NAMES`
   / `FORBIDDEN_FUNCTION_PREFIXES`, enforced in the AST walk). Today only `duckdb_settings`
   is blocked, so `duckdb_databases()` (on-disk paths of the attached lake and
   `runs.duckdb`), `duckdb_tables()`, `duckdb_views()`, `duckdb_columns()`,
   `duckdb_extensions()`, `duckdb_secrets()`, `duckdb_temporary_files()`,
   `pragma_database_list()`, `pragma_table_info()`, `version()`, `current_database()` pass
   the gate — and since EP-35 every safe session attaches a second database. Add the
   prefixes `duckdb_` and `pragma_` and the names `version`, `current_database`,
   `current_schema`; `information_schema` stays the sanctioned metadata surface
   (`safe.REGISTRY_SCHEMAS`) and `mwh sql --describe` keeps working. Tests: each named
   statement is refused (`SafeQueryRefused` in-process; `mwh sql … --tier fixture` exit 3;
   the audit line carries `allowed = false` and the sanitized reason), the
   `information_schema.tables` count and a `DESCRIBE` still verify. Record: DESIGN §12 note
   + a D-31 addendum.
2. **XS correctness fixes, one test each.** DKB-6 — `catalog/profile.py` derives the INSERT
   placeholder count by splitting the DDL on commas: pass the column list (a
   `DECIMAL(18,3)` column in the test DDL proves it). P01-2 —
   `config.assert_not_credentialed_lake` tests equality: make it containment in both
   directions (`os.path.commonpath`). LGR-6 — `safe.AuditLine.sql_text` is unbounded: cap
   it (8,192 chars) with a `sql_truncated: bool` field (the `statement_sha256` keeps full
   identity; `runs.audit` gains the column; older lines read `NULL`). WIN-7 —
   `fsio.atomic_write_text` uses a fixed `.tmp` sibling: pid/uuid-suffixed temp name and the
   cleanup wrapped around `write_text` too. DAG-10 — the build lock is per data root while
   `dag/runner.py`'s docstring and refusal message say "per machine": reword both and the
   DESIGN §6 sentence (the machine-scoped lock stays parked). P3C-7 —
   `concepts.runner.register_derived` skips a derived directory without `part-0.parquet`
   silently: log a warning and state the single-file rule in the docstring.
3. **S-sized provenance and test items** (in this order; stop at the hour). CTR-7 —
   `inventory build` never prunes raw-manifest records whose `rel_path` matches no contract
   table, and they feed `raw_snapshot_id`, which every lake and derived layer id inherits:
   drop them before `write_dataset_manifest` / `write_snapshot` (logged) and count only
   current tables in the completeness gate. CLI-8 — two exported `parse_sha256sums` with
   different signatures (`inventory`, `demo`) and two cross-module private imports
   (`disclose` → `safe._contract_names`, `backup` → `doctor._bitlocker_protection`): rename to
   `parse_sums_file` / `parse_sums_text` (aliases kept for one phase) and promote the two
   names. DAG-9 — no test covers the failed-job path or `report_job`: a fixture-tier job
   whose `mwh` child exits non-zero → `state == "failed"`, `exit_code`, `finished` set and
   the build's provenance run `status: failed`; a second test calls `update_job` beside the
   supervisor rewrite. DKB-7 — pin tests for the `json_serialize_sql` spellings the walk
   relies on (`SET_OPERATION_NODE`, `UNION_BY_NAME`, the `SHOW` names) and for
   `_partition_copy_options`' `OVERWRITE_OR_IGNORE` semantics, so a DuckDB pin bump fails
   loudly. TST-5 — the literal 31 is asserted in thirteen test modules (five added in P3):
   one `tests/helpers.STAGED_TABLE_COUNT` derived from the contract, imported everywhere (a
   churn-rule edit — list the modules in the completion note). TST-7 — `test_ep165` asserts
   a 200 ms wall budget on a cold-interpreter subprocess: time `decide()` in-process against
   200 ms and give the subprocess a 3 s ceiling.
4. **Session-guard package (SGD-5 / SGD-6 / SGD-7) — authored, not applied** (D-45 item 4;
   the B7 / EP-54 connector-package shape): a reviewed diff in the session scratchpad plus a
   one-page README for the owner. SGD-5 — `ls` is an allow-listed launcher in the hook's
   `ALLOW_RE` while `Get-ChildItem *mimicdata*` is denied: drop `ls` from the launcher list
   *or* add the enumeration aliases (`dir`, `gci`, `Get-Item`, `Resolve-Path`, `find`,
   `tree`, `stat`) to `deny` — one of the two, consistently. SGD-6 — the settings allow list
   lacks the `--group` and bare `--project` launcher forms the hook accepts: mirror the
   hook's four forms (Bash and PowerShell), keeping `test_ep165`'s "no data tokens in allow
   rules" invariant. SGD-7 — the hook's `DATA_RE` lacks guard G1's extension classes:
   extend it with `.tsv`, `.xlsx`, `.xls`, `.zip`, `.jsonl`, `.ndjson`, `.feather`,
   `.arrow`, `.pkl`, `.joblib`, `.skops`, `.pt`, `.safetensors`, `.npy`, `.npz`, `.h5` and
   the bare `.gz` / `.zst` / `.xz` / `.bz2` as a literal copy with a comment pointing at
   `guard.DATA_EXTENSIONS` (the hook stays stdlib-only and under its 200 ms budget); the
   `mwh guard --selfcheck` and `test_ep165` updates ride in the package.
5. **Concept-group tags** (optional, only after items 1–3 landed): prefix the mimic-code
   group tags `concept-<group>` in `concepts/inventory.py`'s generated spec so `--tag
   measurement` selects EP-45's four steps only (D-20 addendum 2026-09-17, EP-45's
   deferral); regenerate `dag/specs/concepts.yaml` and `docs/resources/concepts.md`,
   update `docs/gotchas.md` §3 and `test_ep37`'s tag pins under the churn rule.

## Out of scope

- The rows EP-54 parked (LDR-7 / LDR-9, DAG-5 / WIN-5, DAG-7, LGR-5, TST-6 / TST-8 / TST-9,
  CTR-3 / CTR-5 / CTR-6 / CTR-8) stay AUDIT-1 or wait for the next fixture regeneration; if
  items 1–3 finish early, **LDR-7 + LDR-9 are the named overflow (S)** — a second brief at the
  next free number, never a scope widening here.
- Applying the session-guard package → the owner (D-45 item 4).
- Any change to the staged lake, to DAG specs beyond item 5, or to the P4 briefs.

## Parked → final-roadmap.md

- None new — every row this brief does not land is already `final-roadmap.md` AUDIT-1; the
  completion note names what went back.

## Verification / acceptance

- `uv run poe test -m ep_173` green on fixture; `uv run mwh verify EP-173` green;
  `tests/ep/test_ep173.py` carries the SGT-5 refusal tests (`uv run mwh sql "SELECT count(*)
  AS n FROM duckdb_databases()" --tier fixture` exits 3; the audit line has `allowed =
  false`) and one test per landed item.
- `uv run mwh verify EP-k` still exits 0 for k ∈ {10, 19, 29, 30, 33, 37, 165, 167} (the
  modules touched); `uv run poe check` green; `uv run poe roadmap-check --strict` 0 errors.
- The completion note lists every AUDIT-1 id landed or parked back, the churn-rule edits
  (TST-5's thirteen modules), the wall time against the S budget, and the scratchpad
  package's file names; `final-roadmap.md` AUDIT-1 row updated; DESIGN §12 note + D-31
  addendum for SGT-5.
- Commit `feat(mimicwarehouse): debt sweep - safe-query metadata functions + carried-low
  fixes (EP-173)` then `docs(roadmap): record EP-173 commit hash`; the owner applies the
  session-guard package as its own commit (`chore(session-guard): … (EP-173,
  owner-applied)`).

> **Completion note (2026-09-26).** Executed as briefed on fixture, one session, items 1–4
> landed or authored, item 5 parked. **Item 1 — SGT-5 (governance, first).** A pre-flight
> probe confirmed the hole: `SELECT count(*) AS n FROM duckdb_databases()` verified and ran
> on the fixture catalog (exit 0, only its small count suppressed). `safe.py` now refuses the
> prefixes `duckdb_` / `pragma_` and the names `version` / `current_database` /
> `current_schema` wherever the tree pass meets them (CTEs and subqueries included);
> `information_schema` stays the sanctioned metadata surface, and `mwh sql --describe`
> reads its column comments from `information_schema.columns.column_comment` (the same
> `COMMENT ON` text `duckdb_columns()` carried) — `catalog/cli.py`. Tests: fifteen
> statements refused in-process with `allowed = false` and a sanitized reason that names
> no path, the CLI exits 3, `information_schema.tables` / `.columns`, `DESCRIBE` and
> `--describe --format json` still verify. Record: DESIGN §12 (rule-set corrections + dated
> note), D-31 addendum. **Item 2 (XS fixes).** DKB-6 — `catalog.profile._write_parquet`
> takes a `(name, type)` column list (`TABLES_COLUMNS` / `COLUMNS_COLUMNS`); placeholders
> are `len(columns)` (a `DECIMAL(18,3)` column proves it). P01-2 —
> `config.assert_not_credentialed_lake` refuses a synthetic root that equals, contains or
> lies inside a credentialed layer via `os.path.commonpath` (`CREDENTIALED_LAKE_LAYERS` =
> `core` / `derived` / `marts` / `manifests`): the check cannot use `layout["lake"]` itself
> for the inside direction because the default fixture / demo roots are `lake/fixture` /
> `lake/demo` beneath it (the brief's literal reading would have refused every fixture
> build). LGR-6 — `AuditLine.sql_text` is cut at `SQL_TEXT_MAX_CHARS` = 8,192 with
> `sql_truncated`; `statement_sha256` stays over the full text; the `runs.audit` view
> samples the whole ledger (`sample_size = -1`) because a probe showed DuckDB 1.5.5's
> default sample silently omits a key that first appears after the sampled lines — older
> lines read `NULL` (the live `runs.duckdb` picks the new view text up at the next
> `mwh runs refresh`). WIN-7 — `fsio.temp_sibling` (`<name>.<pid>.<uuid8>.tmp`) and the
> cleanup wrapped around the write itself. DAG-10 — "per data root" in the runner
> docstring, the refusal message and DESIGN §6. P3C-7 — `register_derived` warns on a
> table directory without `part-0.parquet` (naming what it holds) and on Parquet beside
> it; the single-file rule is in the module docstring. **Item 3 (S items).** CTR-7 —
> `inventory.prune_orphan_records` runs before the first manifest / snapshot write
> (`BuildResult.pruned`, one `pruned:` log line per record); the test shows 40 current
> lines + 1 orphan used to produce a snapshot id and now yield `None`. CLI-8 —
> `inventory.parse_sums_file` / `demo.parse_sums_text` (`parse_sha256sums` aliases kept one
> phase), `safe.contract_names` + `free_text_column_names` and `doctor.bitlocker_protection`
> promoted (private aliases kept one phase), `disclose` / `backup` on the public names.
> DAG-9 — a detached `mwh build --tier fixture --select meta.profile --job …` over an empty
> temp lake fails inside the run (`ProfileError`): state `failed`, exit code 1, `finished`
> set, the provenance run `status: failed`, `mwh jobs` shows it; a second test drives
> `update_job` and the supervisor's own rewrite (`_supervise` over a child that exits 2).
> DKB-7 — pins on `SET_OPERATION_NODE` / `UNION_BY_NAME` / `setop_all`, the `SHOW_REF`
> names (`"tables"`, `__show_tables_expanded`; `show_type` `SHOW_UNQUALIFIED` / `DESCRIBE`),
> `CAST` → `child`, `FORCE_AGGREGATES`, `cte_map`, `TABLE_FUNCTION` → `FUNCTION`, and the
> partitioned `COPY`: a non-empty root without `OVERWRITE_OR_IGNORE` is refused ("not
> empty"), with it disjoint buckets land beside byte-identical earlier files. TST-5 —
> `tests/helpers.STAGED_TABLE_COUNT` (`staged_table_count()` over `STAGED_SCHEMAS` from the
> contract) replaces the literal in **ten** modules — `test_ep08`, `09`, `10`, `20`, `21`,
> `27`, `28`, `29`, `34`, `44` — not the thirteen the re-triage counted: the other three
> `\b31\b` hits were EP-31 references and `2**31` seeds (`test_ep36`), never the table
> count; `tests/README.md` names the constant under the churn rule. TST-7 — `test_ep165`
> times `decide()` in-process over the whole decision matrix against 200 ms and gives the
> cold-interpreter subprocess a 3 s ceiling. **Item 4 — session-guard package (authored,
> not applied; D-45 item 4).** In the session scratchpad
> `%LOCALAPPDATA%\Temp\claude\c--Users-willi-Documents-DATA-mimicwarehouse\0619e6be-f068-4ab2-8dfc-62f4c253c3d8\scratchpad\ep173-session-guard\`:
> `ep173-session-guard.diff` (395 lines, 20 hunks over `.claude/settings.json`,
> `mimicwarehouse/scripts/claude_pretool_guard.py`, `mimicwarehouse/src/mimicwarehouse/guard.py`,
> `mimicwarehouse/tests/ep/test_ep165.py`; `git apply --check` clean against this tree),
> `README.md` (what / why / the SGD-5 decision / apply recipe / rollback), the four modified
> files in repository layout, pristine copies under `orig/`. SGD-5 takes option (a): `ls`
> leaves the hook's `ALLOW_RE` and two `ls` deny rules mirror the existing `Get-ChildItem`
> one; SGD-6 adds the three missing `uv run … mwh` launcher forms per shell; SGD-7 gives
> the hook a literal `DATA_EXTENSIONS` tuple (the brief's list: the EP-165 tokens plus
> `.tsv .xlsx .xls .zip .jsonl .ndjson .feather .arrow .pkl .joblib .skops .pt .safetensors
> .npy .npz .h5` and the bare `.gz .zst .xz .bz2` — `.7z / .tar / .tgz / .sqlite / .db /
> .orc / .avro / .hdf5` deliberately left out as the brief named them not), builds
> `DATA_RE` from it with a word boundary per suffix, and adds `guard.PRETOOL_HOOK_EXTENSIONS`
> + the selfcheck row `pretool-hook-tokens` (loads the tracked script; subset of G1, every
> required class present, `DATA_RE` matches each) with the `test_ep165` pins. Validated on
> the copies: ruff clean with the project config; 47 of 48 package tests pass against the
> package's hook + settings (the 48th reads the installed `guard.PRETOOL_HOOK_EXTENSIONS`
> and passes once applied); the probe reports 24 suffixes mirroring G1 and flags the
> pristine hook as "cannot be loaded"; 200 `decide()` calls in 0.6 ms. **Item 5 — parked**
> (optional; not started at the hour): the recipe stays on the `final-roadmap.md` AUDIT-1
> row. **AUDIT-1 ledger:** landed SGT-5, DKB-6, P01-2, LGR-6, WIN-7, DAG-10, P3C-7, CTR-7,
> CLI-8, DAG-9, DKB-7, TST-5, TST-7; authored SGD-5 / SGD-6 / SGD-7; parked back the
> concept-tag prefix; untouched and still parked LDR-7 + LDR-9 (the named overflow),
> DAG-5 / WIN-5, DAG-7, LGR-5, TST-6 / TST-8 / TST-9, CTR-3 / CTR-5 / CTR-6 / CTR-8.
> **Churn-rule edits** (earlier `test_ep*.py` touched, with the reason): `test_ep02` (the
> two BitLocker monkeypatch targets follow the promoted public name), `test_ep08` / `09` /
> `10` / `20` / `21` / `27` / `28` / `29` / `34` / `44` (the TST-5 constant), `test_ep165`
> (the TST-7 timing split); every touched brief re-verified below. **Gates (end of
> session):** `mwh verify EP-173` 35 passed (82 s); the verify loop EP-2 27 · EP-10 28 ·
> EP-19 8 · EP-29 13 · EP-30 33 · EP-33 115 · EP-37 17 · EP-165 62 · EP-167 35, every exit
> 0 (fresh interpreter each, ≈ 8 min in total, concurrent with the suite); `uv run poe
> check` exit 0 — ruff check + `ruff format --check` clean, pyright 0 errors, **1,219
> passed, 58 deselected, 1 warning** (the crafted torn-ledger test, as at EP-54) in 821 s
> (13 min 41 s under that concurrency; EP-54's baseline 729 s); `poe roadmap-check
> --strict` 0 errors / 0 warnings (174 rows, 64 done before this tick); `mwh guard` clean
> over the 30 changed files; `mwh doctor` at pre-flight 9 pass · 2 warn · 0 fail · 5 info
> — the `antivirus` warn by design, plus `last_backup` (the 2026-09-17 backup is 9 days
> old: owner FYI, `mwh backup run`), `power_scheme` AC mode Best performance. **Wall
> time:** ≈ 1 h from the first tool call (≈ 20:50 local) to this note (21:50) against the
> S budget of 30 min — ≈ 2×, the Risk 11 pattern for a brief that touches a governance
> surface (thirteen rows, a package and nine re-verifications); the four judgment calls
> above (the containment layers, `sample_size = -1`, the one-phase aliases, the hook's
> extension subset) were made without the owner and are listed for the review. **Owner
> review:** the interactive checkpoint follows this note; its outcomes are recorded in the
> checkpoint minutes below.

> **Checkpoint minutes (2026-09-26).** Four questions after the gates had run, the
> recommended option first each time; **every recommended option was taken but one**:
>
> 1. **Commit — the two standard steps, no push** (the owner pushes). Rejected: the feat
>    commit only; no commit.
> 2. **SGD-5 — option (a) as authored**: `ls` leaves the hook's launcher list and two `ls`
>    deny rules mirror the existing `Get-ChildItem` one. Rejected: option (b) (keep `ls`,
>    deny the seven enumeration aliases); deciding at apply time.
> 3. **Session-guard package — applied by the session now on the owner's one-time
>    instruction** (the EP-54 precedent; the D-45 item 4 rule stands for every later
>    package): `git apply` of the diff, the static gates, `mwh guard --selfcheck`,
>    `mwh verify EP-165`, then its own `chore(session-guard): … (EP-173, owner-applied)`
>    commit for the owner's review. Rejected (the recommended option): the owner applies
>    it later in their own shell; not now.
> 4. **Item 5 (the `concept-<group>` tag prefix) — stays parked in AUDIT-1** with its
>    recipe. Rejected: doing it in this session (≈ 45 min + re-verification); naming it in
>    the LDR-7 + LDR-9 overflow brief.
>
> Routine calls stated in the completion note and not objected to: the credentialed-layer
> containment, `sample_size = -1` on the audit view, the one-phase aliases, the hook's
> extension subset, the `test_ep02` monkeypatch targets. FYI to the owner: the last backup
> is nine days old (`mwh backup run`).
