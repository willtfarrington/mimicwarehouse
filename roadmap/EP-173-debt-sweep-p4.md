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
