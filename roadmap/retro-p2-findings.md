# EP-33 discovery-audit findings ledger (retro-p2-findings.md)

Machine-rendered record of the EP-33 bounded discovery audit (D-44 item 2; method: 13 review lenses + 6 re-verification batches over the 2026-08-18 ledger's 69 findings -> one independent verifier per material finding -> completeness critic). Baseline: commit `d88e2b5`, 2026-08-30. Per-finding blocks are deliberately constrained to id / area / file:line / class / severity / evidence / fix direction (no reproduction recipes - the EP-33 addendum's format rule); id bands are written `1xxxxxxx`-style on purpose (guard G4). Verdicts and severities are the independent verifier's. Nothing in this ledger was implemented before the owner triage checkpoint, with one recorded exception: DRF-1 is damage the session's own Workstream-A edit pass introduced into DECISIONS.md (not a P2 finding), and was repaired in the same salvage commit that landed this ledger. Each remaining finding's triage outcome (fix-now / allocate / park / reject) is recorded in the index below at the checkpoint.

## Index - verified findings

| Id | Sev | Verdict | When | Effort | Class | Title | Triage |
|---|---|---|---|---|---|---|---|
| CLI-1 | high | confirmed | now-must | S | bug | FileNotFoundError swallowed in _retry lets a failed rename-into-place complete the swap and delete the aside copy | _pending_ |
| DKB-1 | high | confirmed | now-must | S | bug | Count-family requirement satisfiable by alias alone, so k-suppression can run without any true group-size count | _pending_ |
| DKB-2 | high | confirmed | now-should | S | governance-precision | DuckDB execution-error text surfaced verbatim can embed row values in the refusal message and audit log | _pending_ |
| DRF-1 | high | confirmed | now-must | S | doc-drift | Decision headers D-18, D-19, D-21, D-25 destroyed by insertion of the 2026-08-30 EP-33 addenda | **fixed** (salvage commit — session-introduced) |
| LDR-1 | high | confirmed | owner-decision | S | robustness | Subset bucket request replaces a superset table wholesale via the directory swap | _pending_ |
| SGT-1 | high | confirmed | now-must | M | bug | Count-family requirement satisfiable by alias regex on a non-count column | _pending_ |
| WIN-1 | high | confirmed | now-should | S | bug | Resume path permanently drops the manifest line for a bucket sorted just before a crash | _pending_ |
| CLI-2 | medium | confirmed | now-should | XS | robustness | Callback's pending-error catch misses pydantic-settings SettingsError, so one class of broken .env values bypasses the CFG-5 fix | _pending_ |
| CTR-1 | medium | confirmed | next-replan | S | robustness | NULL placement under contract sort_keys differs between the Polars fixture path and the DuckDB staging ORDER BY | _pending_ |
| DAG-1 | medium | confirmed | now-should | XS | bug | --dry-run silently dropped when combined with --background, launching a real build | _pending_ |
| DAG-3 | medium | confirmed | next-replan | S | robustness | Recycled pid makes a stale lock unbreakable, even with --break-lock | _pending_ |
| DRF-2 | medium | confirmed | now-should | M | doc-drift | State of the workspace frozen at EP-16 while EP-17 through EP-32 have shipped | _pending_ |
| DRF-3 | medium | confirmed | now-should | S | doc-drift | Root README project-status block is an EP-166-era snapshot | _pending_ |
| DRF-5 | medium | confirmed | now-should | S | doc-drift | Quick-start command roster and layout package list omit the EP-30/31/32 surface | _pending_ |
| LDR-2 | medium | confirmed | now-should | XS | bug | FileNotFoundError treated as success by _retry on the publish rename in swap_dir | _pending_ |
| LDR-3 | medium | confirmed | now-should | S | bug | Crash window between per-bucket progress write and manifest append permanently omits the bucket's manifest line | _pending_ |
| LDR-4 | medium | confirmed | now-should | S | robustness | Resume precondition omits source identity and sort parameters recorded in Progress | _pending_ |
| LGR-1 | medium | confirmed | now-should | S | robustness | No reader of the append-only ledgers tolerates a torn trailing line | _pending_ |
| LGR-2 | medium | confirmed | now-should | XS | robustness | Unchecked os.write return value in both O_APPEND ledger writers | _pending_ |
| P3C-2 | medium | confirmed | next-replan | S | forward-compat | Six P3 briefs address steps in new DAG spec files via `mwh build --tag/--select`, but the CLI only loads the packaged stage spec | _pending_ |
| P3C-3 | medium | confirmed | next-replan | S | forward-compat | EP-49 requires `safe.owner_rows` attributed to EP-30, which does not exist and is deferred to EP-58 | _pending_ |
| P3C-5 | medium | confirmed-with-corrections | next-replan | S | governance-precision | Contract-derived subject-keyed/free-text classification does not extend to the mimiciv_derived and marts surfaces P3 creates | _pending_ |
| P3C-6 | medium | confirmed-with-corrections | next-replan | XS | doc-drift | EP-35/EP-38 benchmark plans drift from the shipped BenchmarkLine model and the `mwh runs benchmarks` verb | _pending_ |
| RES-1 | medium | confirmed | now-should | S | doc-drift | Elixhauser comorbidity concept described as vendored mimic-code SQL that does not exist in the vendored tree | _pending_ |
| SGD-1 | medium | downgraded | owner-decision | S | governance-precision | git-prefixed file-content readers classified as allow-listed read-only project commands | _pending_ |
| SGD-2 | medium | confirmed | owner-decision | XS | governance-precision | Grep glob field excluded from the hook's checked fields | _pending_ |
| SGD-3 | medium | confirmed | owner-decision | S | governance-precision | G4 content scan skips notebook source cells and several tracked text types | _pending_ |
| SGD-4 | medium | confirmed | owner-decision | S | robustness | pretool-hook selfcheck passes with a dead absolute hook command | _pending_ |
| SGT-2 | medium | confirmed | owner-decision | M | governance-precision | min/max/mode/quantile over subject-keyed non-count columns disclose individual raw values | _pending_ |
| TST-2 | medium | confirmed-with-corrections | now-should | S | convention | Hard-coded fixture row counts in test_ep30/21/22 contradict the suite's churn rule | _pending_ |
| WIN-2 | medium | confirmed | now-should | S | robustness | Pass-2 publish and delete operations lack the transient-PermissionError retry loop | _pending_ |
| WIN-3 | medium | confirmed | now-should | XS | robustness | swap_dir fails the whole stage when post-publish .old cleanup exhausts retries, unlike swap_catalog | _pending_ |
| WIN-4 | medium | confirmed | now-should | XS | robustness | Stale-.new sweeps use bare shutil.rmtree without the retry loop | _pending_ |
| CLI-3 | low | downgraded | now-should | XS | doc-drift | D38_ALLOW_LIST still enumerates seven paths after the D-38 addendum grew the allow list to nine | _pending_ |
| DAG-2 | low | downgraded | now-should | S | robustness | Build-lock acquisition is check-then-write, not atomic | _pending_ |
| DAG-4 | low | downgraded | next-replan | S | robustness | O_APPEND ledger append is not atomic between processes on Windows | _pending_ |
| DRF-4 | low | downgraded | now-should | XS | doc-drift | DESIGN.md framing paragraph stops the shipped inventory at the P0/P1a modules | _pending_ |
| LGR-3 | low | downgraded | owner-decision | S | governance-precision | GOVERNANCE section 8 event coverage exceeds what is implemented | _pending_ |
| LGR-4 | low | downgraded | now-should | M | convention | Six distinct append implementations behind the one-JSONL-helper unification | _pending_ |
| P01-1 | low | downgraded | now-should | XS | governance-precision | Doctor antivirus warn detail still names the seven-path D-38 allow list; D-38 was amended to nine paths | _pending_ |
| P3C-1 | low | downgraded | next-replan | XS | forward-compat | EP-35 run_id format embeds a compact date that falls inside a real id band, colliding with G4 and EP-43's id-band scan | _pending_ |
| P3C-4 | low | downgraded | next-replan | S | forward-compat | EP-47 acceptance query over marts.cohorts is refused by the shipped aggregate-only rules and depends on B1c covering marts registries | _pending_ |
| RES-3 | low | downgraded | now-should | XS | doc-drift | Landing-convention text pins a 15-key Settings.layout contract that EP-167 already grew to 18 keys | _pending_ |
| SGT-3 | low | downgraded | next-replan | M | governance-precision | CAST of an aggregate and arithmetic over aggregates are refused | _pending_ |
| TST-1 | low | downgraded | now-should | S | test-gap | Sortedness assertions degenerate to a one-column prefix for pharmacy and prescriptions | _pending_ |
| TST-3 | low | downgraded | owner-decision | M | test-gap | Dev/full acceptance can go all-skip green; stale-catalog inconsistency is skipped, not failed | _pending_ |

## Index - carried low findings (no verifier pass, 2026-08-18 precedent)

| Id | Sev | When | Effort | Class | Title |
|---|---|---|---|---|---|
| CLI-4 | low | now-should | XS | doc-drift | cli.py module docstring lists unattached future commands as attached and omits runs benchmarks |
| CLI-5 | low | next-replan | XS | dead-code | CliState.data_root property has no callers |
| CLI-6 | low | now-should | XS | dead-code | _retry's what parameter is never used |
| CLI-7 | low | now-should | XS | doc-drift | Canary docstring claims the doctor never writes, but check_data_root creates a probe file under the data root |
| CLI-8 | low | next-replan | S | convention | Two exported parse_sha256sums functions with different signatures, plus cross-module private imports |
| CTR-2 | low | now-should | XS | doc-drift | structural_hash docstring claims the EP-17 loader pins it; the loader pins a different per-table hash |
| CTR-3 | low | now-should | S | governance-precision | Per-space id floors are not validated as ordered/disjoint, and the disjointness check omits event-id columns |
| CTR-4 | low | now-should | XS | convention | frame_to_csv_bytes hard-codes dialect literals instead of the csv_dialect constants |
| CTR-5 | low | next-replan | XS | doc-drift | Manifest key id_floor still records a single floor from the pre-0.2.0 scheme |
| CTR-6 | low | next-replan | XS | forward-compat | structural_hash payload omits the table's dataset label |
| CTR-7 | low | next-replan | S | robustness | Orphaned raw-manifest lines after a contract rename are never pruned and keep feeding the snapshot id |
| CTR-8 | low | owner-decision | S | robustness | write_fixture called with one module's frames rewrites manifest.json to cover only that module |
| DAG-10 | low | owner-decision | XS | governance-precision | Lock scope is per data root, not per machine as documented |
| DAG-5 | low | next-replan | S | bug | Parent writes the initial running state file after Popen, racing the supervisor's terminal rewrite |
| DAG-6 | low | next-replan | S | robustness | Job liveness is pid-only, so a recycled supervisor pid blocks relaunch and misreports alive |
| DAG-7 | low | owner-decision | M | forward-compat | Snapshot id sources sort_keys from the live contract, not the manifest line |
| DAG-8 | low | next-replan | XS | bug | Second-resolution ts plus non-stable sort makes summarize's latest-build pick ambiguous |
| DAG-9 | low | next-replan | S | test-gap | No coverage of the failed-job path or the child-side report_job merge |
| DKB-3 | low | next-replan | S | governance-precision | Pre-execution catalog statements can raise un-audited duckdb.Error out of safe_query |
| DKB-4 | low | next-replan | XS | robustness | Interrupt timer can fire against a closing connection, raising in the timer thread |
| DKB-5 | low | next-replan | XS | robustness | dev-buckets probe opens the live catalog with no config, conflicting with the path-keyed instance cache |
| DKB-6 | low | next-replan | XS | forward-compat | INSERT placeholder count derived by splitting the DDL string on commas |
| DKB-7 | low | next-replan | S | test-gap | Version-fragile json_serialize_sql AST spellings and partitioned-COPY flag semantics lack dedicated pin tests |
| DRF-6 | low | owner-decision | XS | governance-precision | Docs-discipline rule does not name the two accepted pre-EP-43 committed aggregates |
| DRF-7 | low | now-should | XS | doc-drift | Section 15 module map lacks 'shipped' marks on catalog/ and demo.py |
| DRF-8 | low | now-should | XS | doc-drift | Section 16 tier switcher wording omits the fixture tier the EP-166 amendment added |
| DRF-9 | low | now-should | XS | convention | Wrapped H2 heading renders as two separate headings |
| LDR-5 | low | now-should | XS | robustness | Torn trailing line in a build manifest jsonl breaks every strict line parser downstream |
| LDR-6 | low | now-should | XS | robustness | Pass-2 file operations bypass the PermissionError retry policy used elsewhere |
| LDR-7 | low | next-replan | S | perf | Two crash windows discard a completed heavy pass instead of resuming it |
| LDR-8 | low | next-replan | XS | bug | Early dev_ready status signal lost when the crash lands between the progress and status writes |
| LDR-9 | low | next-replan | S | test-gap | Resume tests interrupt only at _sort_bucket boundaries, leaving the interleaved crash windows unasserted |
| LGR-5 | low | owner-decision | S | governance-precision | Executed statement can go unaudited in the crash window before the post-execution append |
| LGR-6 | low | next-replan | XS | robustness | Unbounded sql_text makes the single-write interleaving guarantee size-dependent |
| LGR-7 | low | now-should | XS | doc-drift | Benchmark ledger docstrings say flush where the code fsyncs |
| P01-2 | low | owner-decision | S | governance-precision | assert_not_credentialed_lake refuses only the exact credentialed lake root, not its subroots |
| P01-3 | low | now-should | XS | doc-drift | verify.py import-cost claim omits the module-level mimicwarehouse.config import |
| P01-4 | low | now-should | XS | doc-drift | selfcheck docstring understates the files it opens since the EP-165 pretool-hook row |
| P3C-10 | low | next-replan | XS | doc-drift | EP-52 uses a subcommand-level --data-root that has been a global mwh option since EP-167 |
| P3C-11 | low | next-replan | XS | robustness | EP-44's defect-injection test plan assumes a copied fixture catalog has writable tables, but subject-keyed tables are views over absolute-path Parquet |
| P3C-7 | low | next-replan | S | forward-compat | EP-37's generic catalog-discovery convention is underspecified against the layouts EP-29/41/42/50 actually produce |
| P3C-8 | low | next-replan | XS | forward-compat | EP-50 and EP-49 Depends-on headers omit the EPs whose APIs their items require |
| P3C-9 | low | next-replan | XS | doc-drift | Ownership of the SUPPRESSOR swap is contradictory between shipped safe.py and EP-43's scope, and the planned signature does not fit the hook contract |
| RES-4 | low | now-should | XS | doc-drift | D-33 suppression stated as implemented once in mimicwarehouse.disclose while the shipped implementation is safe_query's rowwise suppressor |
| RES-5 | low | now-should | XS | doc-drift | Category re-title instruction names test_ep15.py as the paired file instead of roadmap/README.md |
| SGD-5 | low | owner-decision | XS | governance-precision | data-root directory enumeration denied under one alias and pre-approved under others |
| SGD-6 | low | owner-decision | XS | convention | allow list omits the canonical --group and bare --project launcher forms |
| SGD-7 | low | owner-decision | S | governance-precision | hook token set not extended with the EP-165 G1 data-shaped extension classes |
| SGT-4 | low | next-replan | S | robustness | Numeric group key aliased like a count is suppressed as if it were a count |
| SGT-5 | low | next-replan | S | governance-precision | Forbidden-function list omits catalog/environment-revealing metadata functions |
| SGT-6 | low | next-replan | S | robustness | Snapshot read and runs.duckdb ATTACH execute outside the audited refuse() wrapper |
| TST-4 | low | now-should | XS | test-gap | Tracer model test accepts either fit or not_fit on the deterministic committed fixture |
| TST-5 | low | next-replan | S | forward-compat | The hosp+icu table count and ed/note staging negative are pinned in five separate modules |
| TST-6 | low | now-should | XS | convention | Committed-doc assertions pin the exact tracer run id and exact background-job names |
| TST-7 | low | owner-decision | XS | robustness | Hook subprocess tests assert a 200 ms wall-clock budget on a two-AV Windows host |
| TST-8 | low | owner-decision | XS | forward-compat | Verbatim CLAUDE.md phrase pins will collide with the EP-33 docs-consolidation workstream |
| TST-9 | low | owner-decision | S | robustness | A full-tier acceptance test appends verify lines to the persistent benchmarks ledger on every rerun |
| WIN-5 | low | next-replan | S | robustness | Job state file is written after Popen, racing the supervisor's final rewrite |
| WIN-6 | low | next-replan | XS | governance-precision | runs-db swap failure emits a recovery command with a nonexistent tier |
| WIN-7 | low | next-replan | S | robustness | _atomic_write_text uses a fixed .tmp sibling name and strands it on persistent failure |

## Refuted by the verifier (recorded, no action)

| Id | Title | Verifier note |
|---|---|---|
| RES-2 | MIT license claims reference a repository-root LICENSE file that is absent from the tree | A repository-root LICENSE exists, is git-tracked (commit 08b15e8, before head), and carries the full MIT text with owner copyright. NOTICE:34, datasets.md:39, and pyproject.toml:10 are all consistent with it. The auditor's file search likely drowned in .venv site-packages LICENSE matches. |

## Re-verification of the 2026-08-18 ledger (69 findings)

| Id | Held | Note |
|---|---|---|
| DOC-1 | held | CLAUDE.md §3 now carries the 'Session tooling (D-42, Risks 12/13)' block: uv PATH fallback for both shells incl. before git commit, bare-python=3.14 warning, Write/Edit-only files, no heredocs/stdin scripts, commit -F, Malwarebytes triage, console-encoding rule (EP-167 helper). All present today. |
| GOV-1 | held | Deny list extended per the verifier's corrected set (.claude/settings.json: python*/uv run *python* vs both roots, sh -c/bash -c, Copy-Item, *ReadAllText*, etc.) and the PreToolUse command-string hook is configured (settings.json hooks block, EP-165), documented as live in CLAUDE.md §2. |
| ARCH-1 | held | Option D landed (EP-166/D-43): catalog/build.py swap_catalog implements the rename-aside two-step with crash recovery and PermissionError retry; DESIGN §6 note (~line 382) corrects 'atomically swaps' and lists the caveats; runs.duckdb uses the same swap (DESIGN line 594). |
| ARCH-10 | held | safe.py scopes the >64-char/newline heuristic to statements reading subject-keyed tables; Table.is_dim dims, meta.*, information_schema exempt; contract free_text columns refused by name (safe.py:25-34, 227-242, citing ARCH-10/FC-18). GOVERNANCE §4.1 dictionary reads are reachable. |
| ARCH-2 | held | loader/stage.py and buckets.py document and implement the rename-aside directory publish (os.replace cannot replace an existing dir); partition_glob pinned to subject_bucket=*/part-*.parquet in loader/paths.py:20; DESIGN §5 note (~line 291) records the restage/unavailability rule. |
| ARCH-3 | held | config.py has lake_root(tier) mapping fixture/demo to layout['lake_fixture']/['lake_demo'], plus lake_rejects and rejects_root (fixture/demo rejects under their own lake root) — keys joined at EP-167 per the LAYOUT_KEYS comment; option A (data-root-relative fixture) adopted. |
| ARCH-4 | held | Reconciled in one edit at EP-169 (D-17 addendum): sort_keys carry tie-breakers (labevents +itemid, emar +emar_seq, transfers +transfer_id, etc.) per the YAML header note. Deviation is documented, not a gap: microbiologyevents stays load_class large as a recorded owner exception. |
| ARCH-5 | held | Option A implemented: dag/snapshot.py computes a logical layer snapshot id over (schema, table, path, rows, schema_hash, source_sha256/raw_snapshot_id, sort_keys, writer_version), dev-tier lines filtered to settings.dev_buckets; DESIGN §11 note marks per-file sha256 integrity-only. |
| ARCH-6 | held | loader/manifest.py ManifestLine carries both source_sha256 (per-file, None on fixture) and the 41-file raw_snapshot_id, citing the DESIGN §11 identifier glossary; the glossary note landed at EP-166 (DESIGN ~line 527) and §5 (~line 312) names both fields. |
| ARCH-7 | held | Option A landed at EP-168: tests/conftest.py keeps TIERS=(fixture,dev,full) and adds the orthogonal opt-in @pytest.mark.demo with --with-demo / PYTEST_DEMO=1, deselected unless opted in and skipped with a reason while the demo catalog is absent (catalog_status('demo')). |
| ARCH-8 | held | Option A landed: settings.dev_buckets is the single source, consumed by loader/dag/catalog modules (no DEV_BUCKETS constant in src); catalog/build.py records dev_buckets in meta.catalog_info and warns on drift against the current setting (build.py:289-296). |
| CFG-1 | held | config.unknown_env_keys() exists and is wired into doctor and the CLI (config.py/doctor.py/cli.py, tested in test_ep167); .env.example:7 reworded to the corrected sentence — file/toml keys rejected via extra=forbid, unknown MWH_* env vars warned about. Landed at EP-167. |
| CFG-5 | held | EP-167 lazy validation landed: CliState.settings defers D-29 refusals to first access, so sub-command --help/--version work over a broken/unsafe config (cli.py:85-124); README:151 corrected. Structural retirement of DIAGNOSTIC_COMMANDS deliberately deferred, as the fix proposed. |
| CMP-1 | held | EP-165 landed: GOVERNANCE.md:135-146 connector paragraph (D-43 item 3, reference-lookups only, Drive off with corrected rationale); .claude/settings.json:100-112 denies Gmail/Calendar send-write and hf_fs/dynamic_space; CLAUDE.md section 2 mirrors it. Holds today. |
| CMP-2 | held | Vehicle decided and executed: consolidation shipped as dedicated retro EPs EP-165..EP-169 plus EP-170/EP-171, each with its own row and checked hash in roadmap/README.md:94-113, keeping commit-tag/verify provenance intact; owner decisions recorded under D-43. No untagged hotfixes. |
| CMP-3 | held | Owner verdicts recorded as DECISIONS.md addenda: EP-9 points accepted as shipped under D-17 (line 151), EP-10's five points under D-26 (line 311), EP-164's under D-38 (line 587), in the db38a30 format the corrected fix specified; EP-11/EP-12 correctly dropped. |
| CMP-5 | held | 'Notation used in briefs' table exists at roadmap/README.md:17 with the override sentence. Landed at EP-166 rather than the ledger's assigned EP-170; EP-170 added the companion standing rules for re-plan briefs (README lines 30-31, 54). |
| DOC-11 | held | Single living surface exists: workspace README 'State of the workspace' (module/EP/CLI/tests table + environment realities), canonical per D-43 item 13, refreshed by re-plan EPs; CLAUDE.md section 1 item 3 and root README point at it; roadmap Risk 13 trimmed to pointer form at EP-166. |
| DOC-18 | held | Pre-EP-13 items landed: CLAUDE.md section 1 gained the read-first pointer to the now-existing State of the workspace section, section 3 carries the PATH and bare-python-3.14 facts verbatim (D-42), READMEs no longer say 'no code yet'. EP-13 later completed without the predicted misreads. |
| DOC-2 | held | Root README status rewritten as a dated pointer-shaped paragraph (README.md:9-36, dated 2026-08-28), correct brief count, pointer to State of the workspace, 'refreshed at every re-plan EP' convention. Counts lag the EPs shipped since, but that between-re-plan lag is the documented design. |
| DOC-3 | held | Workspace README rewritten: State of the workspace table replaces the 'no data code yet' intro, Quick start lists all shipped commands through EP-22, layout marked shipped-vs-planned, docs/resources row lists all six inventories, doc table says D-1..D-43. Refreshed again at EP-16 (2026-08-28). |
| DOC-6 | held | GOVERNANCE section 2 carries a dated amendment block (2026-08-28, EP-165; lines 47-63): two products, nine-path Malwarebytes allow list, disclosure-control framing, telemetry off, plus a data-root relocation caveat - the recommended dated-amendment style, original sentence preserved above it. |
| ENV-2 | held | EP-165 landed the preferred variant: settings.json sets PYTHONUTF8=1 (lines 141-142) and doctor._run gained errors='replace' (doctor.py:157-161) per the verifier's correction; roadmap Risk 13 rewritten and trimmed (roadmap/README.md:430). ASCII/_console_safe discipline retained as belt. |
| ENV-3 | held | CLAUDE.md section 3 now carries the full Session tooling (D-42, Risks 12/13) block: PATH-prefix fallback, bare python = system 3.14, Write/Edit only, git commit -F, no burst loops, Malwarebytes triage, sleep blocked. Mirrored in the workspace README environment-realities list. |
| FC-1 | held | tier(name, needs='catalog'|'raw'|'lake') landed in tests/conftest.py (EP-168): kwarg accepted ~:153-161, per-need skip reasons ~:217-223, readiness fixtures :387-429. Dev/full tests no longer vacuously skipped behind the catalog predicate. |
| FC-11 | held | EP-21 brief amended at EP-170: fixture_catalog stays in-memory (conftest.py:342); separate fixture_lake_catalog (conftest.py:375) built without --tag small so it grew as EP-23..27 added steps. EP-21 shipped on that basis; test_ep12 consumers unaffected. |
| FC-13 | held | EP-42 brief carries an EP-170 amendment reading 'passes mwh disclose check' as EP-43's retroactive check (D-43 item 14: fixed by wording, not by moving EP-43); EP-43-disclose-primitives.md:14-15 adds the matching acceptance line. Amendment overrides body text per README notation. |
| FC-15 | held | Option B (configurable) chosen: D-43 item 14 makes settings.dev_buckets the only source (no DEV_BUCKETS constant); provenance recorded in meta.catalog_info (catalog/build.py:397-413) with drift warnings in build.py:289-296 and connect.py:109-132; loader/buckets.py reads the setting. |
| FC-2 | held | Option A landed at EP-168: demo is an orthogonal opt-in @pytest.mark.demo (--with-demo / PYTEST_DEMO=1), deselected otherwise, skipped while the demo catalog is missing (conftest.py:18-21,74,243-290); TIERS ladder stays fixture<dev<full. EP-22 shipped against this. |
| FC-3 | held | One contract edit at EP-169 (D-17 addendum 2026-08-28): tie-break sort keys adopted across mimiciv_hosp/icu.yaml (+itemid/+orderid/+emar_seq/+transfer_id/+pharmacy_id/+poe_seq/+microevent_id); microbiologyevents stays load_class large as a recorded owner exception. |
| FC-4 | held | Fixed more strongly than proposed: manifest.json now pins structural_hash (load-relevant facts only); content_hash is provenance and drift tests ignore it, so comment-only YAML edits no longer force regeneration (tests/fixtures/README.md:39-42; fixture-change protocol in tests/README.md). |
| FC-5 | held | Landed at EP-17 in the proposed shape: Column.identifier/free_text (contract.py:112-120), stamped from keys.yaml identifiers.names + free_text sections (contract.py:765-791) with load-time typo refusal; Table.identifier_columns()/free_text_columns(); flags excluded from structural_hash. |
| FC-6 | held | swap_dir(new, dest) exists in src/mimicwarehouse/paths.py:69 with the .old rename-aside dance, crash recovery (restore .old when dest missing; delete stale .old) and PermissionError retries; used by loader/stage.py:180 and loader/buckets.py:526. Documented as crash-safe, not atomic. |
| FXT-1 | held | Disjoint id floors in fixtures/spec.py:81-90 (subject at 90 million, hadm 91 million, stay 92 million, event/caregiver 93 million with a first_caregiver_id knob); GENERATOR_VERSION bumped to 0.2.0 in one EP-169 regeneration; DECISIONS addendum 2026-08-28 records the layout. |
| FXT-10 | held | Fixed at EP-167: Settings.lake_root(tier) routes fixture/demo to lake_fixture/lake_demo (config.py:652-662), rejects_root likewise; assert_not_credentialed_lake (config.py:710-728) refuses fixture/demo builds into the credentialed lake; catalog_path docstring settles the fixture.duckdb home. |
| FXT-11 | held | Same resolution as FC-11 (verifier option b): EP-21 amendment keeps the in-memory fixture_catalog for test_ep12 and adds fixture_lake_settings/fixture_lake_catalog built without --tag small; with EP-23..27 shipped the fixture lake covers all 31 tables, closing the ordering conflict. |
| FXT-4 | partial | Docs half landed: tests/fixtures/COVERAGE.md documents expected-empty/partial concepts incl. crrt 0/20 and the missing Specimen Type item. The vocab regen is still pending, deferred by design to a bundled regen (COVERAGE.md names EP-41); the EP-12 completion-note FYI was not amended. |
| GOV-2 | held | scripts/claude_pretool_guard.py exists and is registered as a PreToolUse hook in .claude/settings.json:145-152, launched via the allow-listed workspace-venv python. GOVERNANCE section 4 addendum (2026-08-28, EP-165) records the five-layer chain and that mwh guard --selfcheck pins hook registration. |
| GOV-4 | held | guard.py:155 ID_TOKEN now matches float-rendered ids (optional .0+ tail, alnum-dot boundaries); guard.py:158 PATH_ID_TOKEN scans entry paths with digit-only boundaries (stay_<id>.parquet caught); docstring lines 21-25 updated; id_band_hits strips the .0 tail (line 309). |
| GOV-6 | held | .gitignore lines 64-83 anchor the data-shaped dirs to both roots (/data/, /mimicwarehouse/data/, ... /mimicdata/) with an explanatory comment naming item_units.yaml; guard.py TRACKED_PROBES (line 196) probes src/mimicwarehouse/data/item_units.yaml so selfcheck would catch regression. |
| GOV-8 | held | GOVERNANCE section 2 carries a dated 2026-08-28 (EP-165) addendum: two AV products, nine Malwarebytes allow-list paths (two inside the repo), telemetry/sample submission off, exclusions as disclosure control. Section 4 addendum lists five layers incl. hook and the project-relative scope note. |
| INV-1 | held | inventory.py ~814-818: a no-op resume build passes versions=None and omits job keys so write_snapshot (653-678) falls back to previous started/finished/last_file/pid/versions; runs still append every invocation (926). Comments cite retro INV-1; a no-op reconcile also leaves git clean. |
| SCH-2 | held | Contract.structural_hash() exists (schema/contract.py:597, cites SCH-2/FC-4); fixtures/write.py:406 pins the manifest to contract_schema_hash = structural_hash(); schema CLI reports both hashes; keys.yaml:87 notes flag edits move content_hash only. Comment edits no longer break fixture tests. |
| SCH-3 | held | All three briefs carry SCH-3 pickup notes: EP-17 lines 21-22/159 (ORDER BY sort_keys; provider/caregiver have no PK), EP-22 lines 10-14 (identity map, added_in_3_1 vocabulary, synthetic non-identity test), EP-29 line 9+ (reuse comment, identifier/free_text flags). Those EPs since shipped to them. |
| VT-1 | held | conftest.py implements the recommended option (b): requestable readiness fixtures dev_catalog / full_catalog / raw_root / dev_ready(step) (lines 399-433) plus item_tier (line 319), docstring cites EP-168 retro VT-1. The collection hook no longer makes dev acceptance vacuous for the loader EPs. |
| VT-2 | held | test_ep06.py:347-352: the probe is now a crafted-roadmap test (_make_roadmap + monkeypatched roadmap_dir/workspace_root, invokes verify EP-1 against an empty tests/ep). No rolling EP literal remains, so later code EPs need not touch the file. |
| ARCH-9 | held | Settings.min_free_gb_for(tier) (config.py:673-678, cites retro ARCH-9) returns 1 GB for fixture, full min_free_gb otherwise; dag/runner.py:425, loader/engine.py:80 and catalog/build.py:282 all guard per tier. Fixture-tier test builds no longer inherit the 100 GB floor. |
| CFG-2 | partial | Core landed: loader/engine.open_build_connection delegates to inventory.open_connection as the one build factory (retro FC-7); catalog/connect.open_catalog applies HARDENING_SQL. Remainder: test_ep12.py:888/903 still use bare read-only connects; fixtures/catalog.py:82 direct-connects. |
| CFG-3 | held | EP-167 landed the corrected fix: layout['tmp_duckdb'] mkdir at every connect site (inventory.py:398, fixtures/catalog.py:80, catalog/connect.py:102, safe.py:744) and doctor.check_temp_dir (doctor.py:517-552) warns when the parent is missing, pass when only the leaf is. |
| CFG-4 | held | verify.py:643-667 and :811-815 forward MWH_DATA_ROOT in the pytest child env only when --data-root was given (comment cites retro CFG-4; os.environ never mutated). EP-19's launcher spawns sys.executable -m mimicwarehouse.cli, closing the re-invocation hazard. |
| CFG-6 | held | Option A shipped at EP-167: src/mimicwarehouse/console.py is the single console module (only Console() instances in src) and pyproject.toml:41 points mwh at console:run, which reconfigures stdio to UTF-8/replace. Risk 13 reworded (roadmap/README.md:430); DESIGN 2 carries the note. |
| DOC-4 | held | EP-166 fixed DESIGN.md:5-8 ('nothing here existed... have since shipped', living inventory = workspace README State) and retitled the 15 map (line 618) with per-line 'shipped' ticks; cli.py line 624 lists the real command set and concepts/ (643) is tagged EP-8 shipped. |
| ENV-1 | held | CLAUDE.md 3(a) carries the corrected wording: PATH fallback prefix for both shells, needed before git commit too, framed as a stale-VS-Code symptom cleared by restart. Memory records uv now native on both tool-shell PATHs, so the owner action happened too. |
| FC-10 | held | EP-19 amendment (EP-19-stage-dag-runner.md:5-11) fixed all three sub-issues and shipped code matches: dag/jobs.py runs [sys.executable, -m, mimicwarehouse.cli] (lines 162, 221), --data-root stayed global-only, and roadmap/README.md:21 bans $env:MWH_DATA_ROOT recipes. |
| FC-12 | held | EP-170 amendments to EP-20 (lines 9-14) and EP-28 (lines 6-10) landed as corrected: reconcile only where the contract's expected_rows_source is set, provider/caregiver/ingredientevents against raw-manifest rows via reconcile() semantics, and the 3.1-delta clauses struck as moot. |
| FC-14 | held | EP-56 and EP-57 amendment blocks replace the nonexistent MWH_APP_* names with MWH_DUCKDB_APP_MEMORY_LIMIT/shared threads and require .env.example parity for new fields; parity holds today (.env.example carries MWH_LOADER_REJECT_MAX and MWH_ROLE for the shipped EP-17/EP-21 fields). |
| FC-16 | held | One standing convention in the overriding notation table (roadmap/README.md:27, cites ledger FC-16): fmt_int separators, raw JSON never pasted, no allow-pragma on data-derived numbers; EP-29/30/31-33 and EP-43 (count-like-cell exemption) amendments cite it; EP-32 shipped under it. |
| FC-7 | held | One build factory shipped: loader/engine.open_build_connection (engine.py:61-85) adds the version pin + tier-aware free-space guard and delegates to inventory.open_connection as 'the one connection factory'; catalog/connect.py:51-53 delegates catalog_path to Settings.catalog_path. |
| FC-8 | held | ManifestLine carries both fields as corrected (loader/manifest.py:56-59: per-file source_sha256 + raw_snapshot_id, None on fixture) and DESIGN.md 5/11 glossary (lines 311-312, 531-541) records the split of 'source manifest id' into the two; buckets/stage propagate both. |
| FC-9 | partial | Core held: psutil in core deps (pyproject.toml:35) via the EP-19 amendment naming the wheel-check duty; httpx/pynvml correctly left alone. The verifier's vl-convert-python placement decision is still open (EP-48/59/64 still say 'add to core' vs the ui group); deferred to the EP-33 re-plan. |
| FXT-2 | held | Landed via EP-169: manifest.json records numpy/polars/python provenance (fixtures/write.py MANIFEST_PROVENANCE_KEYS); drift tests pin contract_schema_hash = structural_hash (test_ep11.py:654, test_ep12.py:577) with version-naming messages; no-pin policy documented in tests/fixtures/README.md. |
| FXT-3 | held | Protocol lives in tests/README.md section 'Changing the synthetic fixture'; write.py:46-49 comment rewritten to the patch/minor manifest rule citing that section; GENERATOR_VERSION bumped to 0.2.0 with the EP-169 regen; DECISIONS addenda reference the protocol. |
| FXT-5 | held | Resolved as the documented-deferral option: tests/fixtures/COVERAGE.md (EP-169) records the T2DM inputs absent by design and assigns the vocab extension + generator 0.3.0 regen to EP-41, which has not run yet. Vocab itself unchanged, as intended. |
| GOV-3 | held | doctor.py:696-754 adds a deny_coverage check parsing .claude/settings.json deny prefixes, warning when the data root is uncovered (test_ep167.py); GOVERNANCE section 2 has the 'Relocating the data root' paragraph. Landed as a new check id, not folded into data_root, but delivers the fix. |
| GOV-5 | held | guard.py DATA_EXTENSIONS extended with the corrected suffix set (lines 104-122; longest-suffix covers .parquet.gz/.csv.zst); .tsv added to TEXT_EXTENSIONS:143; .gitignore/.gitattributes mirrored; selfcheck probes extended (guard.py:185-200). |
| INV-2 | held | build_inventory re-evaluates header status for stat-matched skipped records via refresh_header_status (inventory.py:798-812), with a separate BuildResult.refreshed bucket and per-dataset JSONL rewrite - exactly the corrected fix (no file I/O, hash/rows untouched). |
| INV-3 | held | Both-fields resolution shipped: loader/manifest.py:56-61 has per-file source_sha256 (None on fixture) plus raw_snapshot_id; docstring says source_manifest_id is superseded; DESIGN.md:311 aligned; dag/runner.provenance_for supplies both; nullable demo semantics documented. |
| INV-4 | held | rel_path_for (inventory.py:277) and RawManifest.for_table (inventory.py:192-195) replace the duplicated key formula at inventory.py:502/703/998, are exported in __all__, and are consumed by dag/runner.py:101 - the feared re-derivation never happened. |
| SCH-1 | held | schema/csv_dialect.py is the single dialect constant (allow_quoted_nulls=true, ISO cast, no timestampformat) imported by inventory, fixtures write/catalog, loader/csv.py and Contract.read_csv_options; TIMESTAMP(3) upstream_type notes added in table YAMLs; policy recorded in DECISIONS. |

## Per-finding evidence (verified findings)

### CLI-1 - FileNotFoundError swallowed in _retry lets a failed rename-into-place complete the swap and delete the aside copy

- **Area / anchor:** cli-config - `mimicwarehouse/src/mimicwarehouse/paths.py:65`
- **Class / severity:** bug / high (now-must, S)
- **Evidence:** _retry (paths.py:56-66) returns silently on FileNotFoundError, a concession commented as "rmtree racing a previous cleanup", but it also wraps the two os.rename steps of swap_dir (paths.py:90-92). If the staged `.new` directory vanishes between the is_dir() check (paths.py:78) and step 4 (the exact quarantine scenario D-42 documents), the rename is treated as success, the rollback branch (paths.py:93-97) never fires, and step 5 (paths.py:99-100) removes `.old` — the only remaining copy of the live table directory. swap_dir is live in the loader (loader/buckets.py:526, loader/stage.py).
- **Fix direction:** Restrict the FileNotFoundError swallow to the rmtree operations (a flag on _retry or separate helpers), and/or assert dest.is_dir() after step 4 before removing `.old`.
- **Verifier:** confirmed - The FileNotFoundError swallow (paths.py:65-66) covers the step-4 rename (line 92): a vanished .new reads as success, the OSError rollback (93-97) cannot fire, and step 5 (99-100) deletes .old, the last live copy. Fix direction (scope swallow to rmtree; assert dest.is_dir() before step 5) is right.

### DKB-1 - Count-family requirement satisfiable by alias alone, so k-suppression can run without any true group-size count

- **Area / anchor:** duckdb-semantics - `mimicwarehouse/src/mimicwarehouse/safe.py:369`
- **Class / severity:** bug / high (now-must, S)
- **Evidence:** _is_count_family_item (safe.py:363-370) accepts any select item whose alias matches COUNT_ALIAS_RE, and _analyze (safe.py:503-507) uses it to satisfy the mandatory count-family rule. `SELECT grp, avg(x) AS n ... GROUP BY 1` passes with no count column; _count_columns (safe.py:547-554) then treats the avg column as the count, so rowwise_suppress tests the avg's value against 1..k-1 instead of group size. Groups of fewer than k subjects are released whenever the aggregate's value falls outside 1..k-1.
- **Fix direction:** Require at least one item whose expression is actually a count-family FUNCTION node (alias alone insufficient), and suppress on those columns only; refuse count-named aliases wrapping non-count aggregates.
- **Verifier:** confirmed - Alias branch at safe.py:369-370 satisfies the count-family rule with no count expression; avg(x) AS n passes _analyze, and _count_columns feeds it to rowwise_suppress, which tests the avg value, not group size — below-k groups (even size 1) leak. Documented behavior, but it defeats k-suppression.

### DKB-2 - DuckDB execution-error text surfaced verbatim can embed row values in the refusal message and audit log

- **Area / anchor:** duckdb-semantics - `mimicwarehouse/src/mimicwarehouse/safe.py:674`
- **Class / severity:** governance-precision / high (now-should, S)
- **Evidence:** safe.py:673-674 turns any duckdb.Error into refuse(f"execution error: {exc}"); DuckDB 1.5.x error messages quote offending cell values (Conversion Error quoting the string it could not cast). The static walk restricts the select list but not WHERE expressions, so a failing cast over a VARCHAR column on a credentialed tier puts a real cell value into the SafeQueryRefused message shown to the session and into refusal_reason in runs/audit.jsonl. count_rows (inventory.py:417-425) and ProfileError (profile.py:231) record raw error text on owner build paths too.
- **Fix direction:** Sanitize refusal text for execution errors: keep the exception class and a truncated first clause with quoted literals stripped, or map known error types to fixed messages before auditing/raising.
- **Verifier:** confirmed - Confirmed at safe.py:674. _analyze polices only the select list; a failing cast in WHERE over a VARCHAR/free-text column executes, and DuckDB 1.5 conversion errors quote the cell value, which flows verbatim into the session-visible refusal and audit refusal_reason. Secondary cites verified.

### DRF-1 - Decision headers D-18, D-19, D-21, D-25 destroyed by insertion of the 2026-08-30 EP-33 addenda

- **Area / anchor:** docs-drift - `mimicwarehouse/DECISIONS.md:187`
- **Class / severity:** doc-drift / high (now-must, S)
- **Evidence:** The four addenda dated 2026-08-30 each replaced text spanning the next decision's header. Orphaned, un-blockquoted tails remain: D-18's tail fused mid-sentence into the D-17 addendum (lines 195-198), D-19's tail at lines 220-221 ('fixes ported; re-derive only what is missing.**'), D-21's at lines 259-261 ('(+VegaFusion) primary, Plotly for timelines...'), D-25's at lines 288-290 ('= YAML protocol -> content hash -> registry entry...'). DESIGN.md still cites all four (sections 4, 8, 13, 16) and DECISIONS.md line 256 cites D-18.
- **Fix direction:** Restore the four decision headers and lead sentences from git history (they existed before commit 148c6eb) and re-seat the four EP-33 addenda after their intended parent decisions, keeping the addendum text.
- **Verifier:** confirmed - All four headers (D-18, D-19, D-21, D-25) are gone; orphan un-blockquoted tails with stray `**`/Alternatives remain at lines 195-198, 220-221, 259-261, 288-290 exactly as reported, and DESIGN.md sections 4/13/16 plus DECISIONS.md line 256 still cite them. Fix direction is correct.
- **Triage (recorded in place):** fixed in the salvage commit that landed this ledger — the four headers were restored verbatim (the A4 edit pass had consumed them as `old_string` anchors without re-emitting them); the four EP-33 addenda stay seated under D-17/D-18/D-20/D-24 as intended. Session-introduced damage, not a P2 finding.

### LDR-1 - Subset bucket request replaces a superset table wholesale via the directory swap

- **Area / anchor:** loader - `mimicwarehouse/src/mimicwarehouse/loader/buckets.py:526`
- **Class / severity:** robustness / high (owner-decision, S)
- **Evidence:** stage_partitioned always publishes pass 1 by swapping the whole .new over dest (buckets.py:526); a request for settings.dev_buckets over a table whose dest holds all 100 buckets discards the other 95 partitions and _tier_complete_for downgrades status to "dev" (buckets.py:608-622). The only guard is the runner's complete_for_tier skip (dag/runner.py:471-480), which is bypassed by force; the stage function itself never compares the request against the existing progress/status coverage.
- **Fix direction:** Add a stage-level refusal (or an explicit destructive opt-in distinct from the runner's force) when the existing dest's recorded buckets_requested/tier_complete is a strict superset of the new request, since the completed lake must never lose staged buckets.
- **Verifier:** confirmed - Confirmed. Dev and full share one lake root (config.py:652-662), so `build --tier dev --force` restages a full-complete table with only dev_buckets; the swap_dir at buckets.py:526 discards the other 95 partitions and status downgrades to "dev". Stage-level superset guard is the right fix.

### SGT-1 - Count-family requirement satisfiable by alias regex on a non-count column

- **Area / anchor:** safe-gate - `mimicwarehouse/src/mimicwarehouse/safe.py:503`
- **Class / severity:** bug / high (now-must, M)
- **Evidence:** _is_count_family_item (safe.py:363-370) returns True when a select item's alias merely matches COUNT_ALIAS_RE, so line 503 sets found_count_family even for a group-key or non-count value column. rowwise_suppress (safe.py:292) then restricts to numeric columns, so a statement like `SELECT dod AS n FROM mimiciv_hosp.patients GROUP BY 1` passes the walk with no genuine count cell and receives zero suppression, returning distinct sensitive values (death dates, admittimes) as raw output.
- **Fix direction:** Require at least one output that is a real count-family FUNCTION call (not alias-only) and ensure the suppressor has a numeric count column to act on, else refuse.
- **Verifier:** confirmed - Verified: the alias-only branch of _is_count_family_item (safe.py:369-370) satisfies the gate at line 503; a non-identifier group key aliased "n" passes every walk, and rowwise_suppress skips non-numeric columns, so distinct raw values return with zero k-suppression on credentialed tiers.

### WIN-1 - Resume path permanently drops the manifest line for a bucket sorted just before a crash

- **Area / anchor:** windows-av - `mimicwarehouse/src/mimicwarehouse/loader/buckets.py:540`
- **Class / severity:** bug / high (now-should, S)
- **Evidence:** In pass 2 the per-bucket order is: record the bucket in _progress.json (buckets.py:546-547), sweep raws (549), then append_manifest (562). A kill in the window after the progress write but before the manifest append leaves the bucket recorded sorted; on resume the already-sorted branch (buckets.py:540-543) only sweeps raws and never appends the missing line. snapshot.py:_latest_lines/table_file_stats then silently omit that part file from layer snapshot ids and meta.tables/meta.row_counts, while catalog views (glob-based) still read it.
- **Fix direction:** Append the manifest line before recording the bucket sorted (re-appends after a re-sort are harmless — newest ts wins per path), or backfill missing lines for already-sorted buckets on resume.
- **Verifier:** confirmed - Ordering verified: progress write (546-547) precedes append_manifest (562); resume branch (540-543) only sweeps; completion never backfills. snapshot.py then omits the part file from snapshot ids/stats while globs read it. Fix direction correct.

### CLI-2 - Callback's pending-error catch misses pydantic-settings SettingsError, so one class of broken .env values bypasses the CFG-5 fix

- **Area / anchor:** cli-config - `mimicwarehouse/src/mimicwarehouse/cli.py:161`
- **Class / severity:** robustness / medium (now-should, XS)
- **Evidence:** The callback catches only (config.ConfigError, config.ValidationError) at cli.py:159-162. pydantic-settings raises SettingsError (a ValueError subclass, neither of those) when a complex field's env/.env value fails its JSON parse — e.g. an unparsable MWH_DEV_BUCKETS or MWH_FORBIDDEN_DRIVES list. That exception escapes the callback as a traceback, so `mwh <subcommand> --help` over such a .env crashes instead of taking the CliState.pending_error path that D-43 item 12 / retro CFG-5 mandate (toml breakage is covered because _toml_source wraps it in SettingsFileError, config.py:478-490).
- **Fix direction:** Add pydantic_settings SettingsError to the caught classes in the callback (and in any other load_settings(checked=False) call site that expects the pending-error contract).
- **Verifier:** confirmed - Confirmed at cli.py:161: SettingsError (ValueError subclass, raised pre-validation on complex-field env/.env JSON parse failure) is not caught, so it escapes as a traceback and defeats the pending-error path even for diagnostic commands. Fix direction correct.

### CTR-1 - NULL placement under contract sort_keys differs between the Polars fixture path and the DuckDB staging ORDER BY

- **Area / anchor:** contract-fixtures - `mimicwarehouse/src/mimicwarehouse/fixtures/hosp.py:117`
- **Class / severity:** robustness / medium (next-replan, S)
- **Evidence:** to_frame (hosp.py:117) and the fixture check (check.py:219) sort with Polars defaults (nulls first); the loader stages with a bare DuckDB ORDER BY over the same sort_keys (loader/stage.py:155, buckets.py:273), which is nulls-last. emar_detail writes NULL parent_field_ordinal on parent rows (hosp.py:1369) and microbiologyevents.charttime is a nullable sort key, so 'sorted by contract sort_keys' names two different orders and staged fixture-tier order diverges from the committed CSVs.
- **Fix direction:** Pin one null placement as canonical (DuckDB NULLS LAST, given the no-restage invariant), document it beside the D-43 item 9 sort-key decision, and align the fixture writer/check at the next regeneration.
- **Verifier:** confirmed - All anchors verify: Polars sorts nulls-first (hosp.py:117, check.py:219); DuckDB bare ORDER BY is nulls-last (stage.py:155, buckets.py:273). Nullable sort keys occur (emar_detail parent_field_ordinal hosp.py:1369; micro charttime hosp.py:799). Latent divergence; fix direction sound.

### DAG-1 - --dry-run silently dropped when combined with --background, launching a real build

- **Area / anchor:** dag - `mimicwarehouse/src/mimicwarehouse/dag/cli.py:123`
- **Class / severity:** bug / medium (now-should, XS)
- **Evidence:** The background branch of build_command (cli.py:118-147) reconstructs the child argv from tier/select/tag/force/break-lock/job only; dry_run is neither forwarded nor refused. A `--background --job X --dry-run` invocation therefore detaches a real build of the requested tier instead of printing a plan.
- **Fix direction:** Refuse the flag combination (--dry-run needs a foreground console anyway) or forward --dry-run into the reconstructed argv.
- **Verifier:** confirmed - Verified: cli.py:123-136 rebuilds the background child argv from tier/select/tag/force/break-lock/job only; dry_run is dropped without refusal, so --background --job X --dry-run detaches a real build (any tier, including full) instead of printing a plan. Refuse the combination or forward the flag.

### DAG-3 - Recycled pid makes a stale lock unbreakable, even with --break-lock

- **Area / anchor:** dag - `mimicwarehouse/src/mimicwarehouse/dag/runner.py:299`
- **Class / severity:** robustness / medium (next-replan, S)
- **Evidence:** acquire_lock treats any lock whose pid psutil.pid_exists() confirms as a live build and raises before the break_lock check (runner.py:299-303); the lock payload records only {pid, build_id, started}, no process identity. Windows recycles pids aggressively, so a lock orphaned by a crash whose pid now belongs to an unrelated process is classified as live, and the documented remedy (--break-lock, cli.py help text: 'A live build is never broken') cannot clear it — only manual file deletion can.
- **Fix direction:** Record the process creation time (psutil.Process().create_time()) in the lock payload and require pid+create-time match for the 'live' classification; a pid match with a different create time is stale.
- **Verifier:** confirmed - Verified: pid_alive (jobs.py:136-139) is bare psutil.pid_exists; the live-pid raise (runner.py:299-303) precedes the break_lock check (:304); the payload (:312) has no process identity beyond pid. A recycled pid reads as live and --break-lock cannot clear it. Pid+create_time fix is correct.

### DRF-2 - State of the workspace frozen at EP-16 while EP-17 through EP-32 have shipped

- **Area / anchor:** docs-drift - `mimicwarehouse/README.md:15`
- **Class / severity:** doc-drift / medium (now-should, M)
- **Evidence:** Stale: header (lines 15-17) says 'next: EP-170, head of P2' (P2 is complete; next is EP-33); intro (lines 5-8) says the loader, warehouse build and safe_query 'arrive with the roadmap briefs from P2 on' (shipped at EP-17/18, EP-19, EP-30); the module table (lines 19-32) lists canary (EP-171) but omits paths.py, loader/, dag/, catalog/, demo.py, safe.py, tracer.py, runs_cli.py; gates block (lines 34-38) pins 545 fixture tests and '171 rows, 23 done' (41 done per roadmap tables); doc table (line 70) says 'D-1 ... D-43' though D-44 is committed.
- **Fix direction:** Refresh the section per its D-43 item 13 charter: module-table rows for the eight shipped P2 modules, updated header/intro/gates counts, and the doc-table D-range.
- **Verifier:** confirmed - Evidence verified: stale EP-16 header/gates ("23 done" vs 45 done marks), module table missing P2 modules, doc table D-1...D-43 though D-44 is committed (DECISIONS.md:933). Section is snapshot-by-design (lines 9-11), but intro and D-range fall outside that; EP-33 charters the refresh.

### DRF-3 - Root README project-status block is an EP-166-era snapshot

- **Area / anchor:** docs-drift - `README.md:20`
- **Class / severity:** doc-drift / medium (now-should, S)
- **Evidence:** Line 20 claims '16 of 171 briefs done' and line 30 '452 fixture-tier tests' (roadmap tables show 41 briefs checked including all of P2). Lines 33-35 say next is 'EP-167 ... EP-169, the P1 resource inventories (EP-13 ... EP-15), then staging (P2)' - all checked in roadmap/README.md. Lines 52-54 describe DECISIONS.md as 'D-1 ... D-41 ... plus D-42/D-43' though D-44 exists; the CLI list (lines 23-24) omits build, jobs, catalog, sql, demo, runs, tracer, canary.
- **Fix direction:** Rewrite the status paragraph to the post-P2 state (brief count, highlights through EP-32, test count, next = EP-33/P3) and update the decisions bullet's D-range.
- **Verifier:** confirmed - Verified: README.md line 20 claims 16/171 briefs and lines 33-35 list EP-167..169, EP-13..15, P2 as next, yet all show ☑ hashes in roadmap/README.md (~41 done through P2); D-44 exists but lines 52-54 stop at D-43; CLI list omits sql/tracer/runs/build. Anchor and fix direction correct.

### DRF-5 - Quick-start command roster and layout package list omit the EP-30/31/32 surface

- **Area / anchor:** docs-drift - `mimicwarehouse/README.md:106`
- **Class / severity:** doc-drift / medium (now-should, S)
- **Evidence:** The Quick start heading (line 106) enumerates 'mwh as of EP-22: doctor ... build - jobs - catalog - sql - demo' with no 'runs' (EP-30/EP-32) or 'tracer' (EP-31), and the command block shows no example for either. The Layout section (lines 217-218) lists the package as 'cli, console, config, doctor, guard, theme, verify, schema/, inventory, fixtures/, concepts/vendor/', omitting paths, loader/, dag/, catalog/, demo, safe, tracer, runs_cli, canary - all present in src/mimicwarehouse/.
- **Fix direction:** Extend the quick-start heading and examples with mwh runs and mwh tracer, and bring the layout line's package enumeration up to the shipped set (or point it wholly at DESIGN section 15).
- **Verifier:** confirmed - Verified: quick-start heading stops at the EP-22 roster; "tracer" appears nowhere in README though cli.py registers runs and tracer; layout lines 217-218 omit many shipped modules (paths, loader, dag, catalog, demo, safe, sql, tracer, runs_cli, canary). Mitigating pointers exist but drift is real.

### LDR-2 - FileNotFoundError treated as success by _retry on the publish rename in swap_dir

- **Area / anchor:** loader - `mimicwarehouse/src/mimicwarehouse/paths.py:66`
- **Class / severity:** bug / medium (now-should, XS)
- **Evidence:** _retry returns silently on FileNotFoundError for every operation (paths.py:65-66), including step 4's os.rename(new, dest) at paths.py:92. If new vanishes there (e.g. an AV quarantine, a documented hazard under D-42), the rename is misclassified as already-done: the rollback except-OSError never fires, step 5 rmtree's the .old that holds the only live copy, and swap_dir returns success with no dest directory at all.
- **Fix direction:** Restrict the FileNotFoundError tolerance to the remove/restore operations (or postcondition-check dest.exists() after step 4 before deleting .old, raising SwapError otherwise).
- **Verifier:** confirmed - Confirmed: _retry (paths.py:65-66) swallows FileNotFoundError for every op, including step 4's rename (line 92); it never reaches the rollback except-OSError at line 93, so a vanished `new` reads as success and step 5 rmtrees the only live copy. Medium: needs AV interference; table restageable.

### LDR-3 - Crash window between per-bucket progress write and manifest append permanently omits the bucket's manifest line

- **Area / anchor:** loader - `mimicwarehouse/src/mimicwarehouse/loader/buckets.py:547`
- **Class / severity:** bug / medium (now-should, S)
- **Evidence:** Pass 2 records a bucket in _progress.json (buckets.py:546-547) before appending its manifest line (buckets.py:552-563); a crash in between leaves the bucket in sorted_buckets, and resume skips recorded buckets with only a raw sweep (buckets.py:540-543) so the line is never appended in any build. Snapshot ids and meta row counts are built from the latest line per path (dag/snapshot.py:66-126, 129-159), so the published part file is silently excluded from both.
- **Fix direction:** Append the manifest line before recording the bucket as sorted (the append is idempotent under latest-ts-wins), or reconcile recorded-but-unmanifested part files at completion.
- **Verifier:** confirmed - Progress write (547) precedes manifest append (552-562); resume skips recorded buckets (540-543), completion never reconciles, so the part file is permanently absent from snapshot ids and meta counts. Directory-scan totals still include it, so metadata undercount only; medium stands.

### LDR-4 - Resume precondition omits source identity and sort parameters recorded in Progress

- **Area / anchor:** loader - `mimicwarehouse/src/mimicwarehouse/loader/buckets.py:431`
- **Class / severity:** robustness / medium (now-should, S)
- **Evidence:** The resume decision checks only pass1_done, complete, and buckets_requested equality (buckets.py:430-437); Progress (buckets.py:97-109) records neither the source's sha256 nor the resolved sort_by/size parameters. A resumed stage therefore sorts whatever raws pass 1 left — possibly from a different source file or under a different sort_by — while stamping the current caller's source_sha256/raw_snapshot_id onto the manifest lines (buckets.py:552-561) unverified.
- **Fix direction:** Persist source_sha256 (or the source path plus size/mtime on fixture) and the resolved sort_by in Progress and require equality as part of the resume condition, restaging otherwise.
- **Verifier:** confirmed - Progress (buckets.py:97-109) stores no source identity or sort_by; the resume predicate (431-437) checks only pass1_done/complete/buckets_requested, so a resumed large stage can sort stale raws and stamp the caller's source_sha256 unverified (552-561). No guard in dag/runner.py. Medium impact.

### LGR-1 - No reader of the append-only ledgers tolerates a torn trailing line

- **Area / anchor:** ledger-audit-integrity - `mimicwarehouse/src/mimicwarehouse/safe.py:751`
- **Class / severity:** robustness / medium (now-should, S)
- **Evidence:** build_runs_db creates the audit view with read_json_auto and no ignore_errors (safe.py:750-753); benchmarks.read uses polars.read_ndjson with no error tolerance (dag/benchmarks.py:108); tracer's _snapshot_of_last_call json.loads every line bare (tracer.py:598-604). A partial final line (crash or disk-full mid-write) is permanent in an append-only file, so one torn line breaks every future audit view query, benchmark summary, and tracer run.
- **Fix direction:** Make the readers tolerate a single malformed trailing line (ignore_errors on read_json_auto, per-line try/except that warns and skips) since the writer cannot guarantee no torn line under crash.
- **Verifier:** confirmed - All three strict readers verified (safe.py:751 view lacks ignore_errors; benchmarks.py:108 read_ndjson; tracer.py:602 bare json.loads). Writers fsync per line but os.write return is unchecked; ENOSPC/crash can tear a line, and append-only policy makes it permanent. Medium stands.

### LGR-2 - Unchecked os.write return value in both O_APPEND ledger writers

- **Area / anchor:** ledger-audit-integrity - `mimicwarehouse/src/mimicwarehouse/safe.py:268`
- **Class / severity:** robustness / medium (now-should, XS)
- **Evidence:** _append_audit (safe.py:268) and benchmarks.append (dag/benchmarks.py:94) ignore the byte count os.write returns. A short write (disk-full, interrupt) is reported as a successful append; the next event then concatenates onto the truncated line, silently merging two events into one unparseable record - an audit event dropped without any error.
- **Fix direction:** Check the returned count against len(blob) and raise (or loop) on a short write, in the shared JSONL helper both writers should call.
- **Verifier:** confirmed - os.write return value is ignored at safe.py:268 and dag/benchmarks.py:94; both O_APPEND writers report success on a short write (e.g. disk-full), leaving a truncated line the next append merges into one unparseable record. Real audit-integrity gap; rare trigger, so medium stands.

### P3C-2 - Six P3 briefs address steps in new DAG spec files via `mwh build --tag/--select`, but the CLI only loads the packaged stage spec

- **Area / anchor:** p3-compat - `roadmap/EP-37-concept-runner.md:37`
- **Class / severity:** forward-compat / medium (next-replan, S)
- **Evidence:** dag/cli.py:166 calls `load_dag()` with no argument and there is no `--spec` option; spec.py:36 pins DEFAULT_SPEC="stage". EP-37 (concepts.yaml), EP-39 (units.yaml), EP-44 (qc.yaml), EP-45 (measurement.yaml), EP-50 (spine.yaml) and EP-53 (`--select analyses.c01_concepts_qc`) all assume their steps are reachable through `mwh build`; EP-37 only questions whether specs merge for the shared catalog step, not whether the spec is reachable at all.
- **Fix direction:** The D1 amendment should settle one mechanism (merge all packaged specs into one graph at load, or add `--spec`) and name it in each affected brief; EP-37, as first consumer, implements it.
- **Verifier:** confirmed - Verified: build CLI has no --spec option and cli.py:166 loads only DEFAULT_SPEC="stage" (spec.py:36); all six cited briefs route new-spec steps through mwh build --tag/--select; EP-37:50 hedges only the catalog-step merge. Anchor line 37 correct; D1 amendment (EP-33:250) is the right fix venue.

### P3C-3 - EP-49 requires `safe.owner_rows` attributed to EP-30, which does not exist and is deferred to EP-58

- **Area / anchor:** p3-compat - `roadmap/EP-49-timeline-api.md:50`
- **Class / severity:** forward-compat / medium (next-replan, S)
- **Evidence:** EP-49 item 4 gates `stay_events` on "the owner-role connection object from `safe.owner_rows` (EP-30)" and says it "writes the EP-30 audit line". Shipped safe.py:58 states owner row viewing is explicitly not in this module and belongs to the EP-58 app path; the audit writer (`_append_audit`, safe.py:260) is private with no public seam. EP-58 comes after EP-49 in the linear order, so the cited API cannot exist when EP-49 runs.
- **Fix direction:** Amend EP-49 to either define its own owner gate (open_catalog role="owner" plus a public audit-append seam added under EP-33's B-work) or move item 4 behind an EP-58 dependency.
- **Verifier:** confirmed - safe.py:58-59 explicitly defers owner_rows to EP-58's app path; no owner_rows exists anywhere in src, _append_audit (safe.py:260) is private, and roadmap ordering puts EP-58 after EP-49. Same misattribution also at brief line 16. Fix direction in the finding is sound.

### P3C-5 - Contract-derived subject-keyed/free-text classification does not extend to the mimiciv_derived and marts surfaces P3 creates

- **Area / anchor:** p3-compat - `mimicwarehouse/src/mimicwarehouse/safe.py:232`
- **Class / severity:** governance-precision / medium (next-replan, S)
- **Evidence:** safe.py:232-242 derives identifier, free-text, dim and subject-keyed sets solely from the EP-9 contract (the 31 staged hosp/icu tables). Every P3 surface — concept views (EP-37), phenotype views with `evidence_json` (EP-41), the spine view whose `text_value` may be exactly 64 chars (EP-50), marts cohort tables (EP-47) — is in ALLOWED_SCHEMAS but never classified subject-keyed, so the free-text value heuristic (safe.py:568-582) is skipped for them even though EP-41 item 4 asserts "k = 11 suppression as EP-30 already enforces". B1c addresses exemptions, not this coverage gap.
- **Fix direction:** Extend the classification source beyond the contract (e.g. treat any non-exempt derived/marts read as subject-keyed for result checks) as part of B1c, and state the rule in the amended briefs.
- **Verifier:** confirmed-with-corrections - Coverage gap is real: subject_keyed derives only from the EP-9 contract, so derived/marts reads (already in ALLOWED_SCHEMAS) skip the free-text value heuristic. Correction: the EP-50 exactly-64-char example is moot — the heuristic refuses only values strictly over 64 chars.

### P3C-6 - EP-35/EP-38 benchmark plans drift from the shipped BenchmarkLine model and the `mwh runs benchmarks` verb

- **Area / anchor:** p3-compat - `roadmap/EP-35-run-ledger.md:53`
- **Class / severity:** doc-drift / medium (next-replan, XS)
- **Evidence:** EP-35 item 2 plans a new pydantic `BenchmarkRecord` to "validate the existing line schema" with a flat field list, but dag/benchmarks.py:48 already ships a frozen `BenchmarkLine` (extra="forbid") whose `host` is a nested HostInfo object, not a scalar. EP-35 item 3 and EP-38 item 1 both invoke `mwh runs bench [--kind]`, while the shipped verb (runs_cli.py:62, EP-32) is `mwh runs benchmarks` with --tier defaulting to full and --kind to stage.
- **Fix direction:** Amend both briefs to extend BenchmarkLine (optional run_id/disk_delta_mb) instead of adding a second model, and to use `mwh runs benchmarks --kind concept`.
- **Verifier:** confirmed-with-corrections - Drift confirmed: BenchmarkLine (benchmarks.py:48) already validates the ledger yet EP-35:53 plans a new BenchmarkRecord; EP-35:65, EP-38:27, and EP-56:34 cite `mwh runs bench` vs shipped `mwh runs benchmarks`. Corrections: brief never claims host is scalar; kind is already str, no widening needed.

### RES-1 - Elixhauser comorbidity concept described as vendored mimic-code SQL that does not exist in the vendored tree

- **Area / anchor:** p1-resources - `mimicwarehouse/docs/resources/vocabularies.md:126`
- **Class / severity:** doc-drift / medium (now-should, S)
- **Evidence:** The which-EP table (vocabularies.md:126) says EP-37/38's Charlson and Elixhauser concepts are "via the vendored mimic-code SQL — yes — vendored; nothing to download", and the register rows at :39-40 repeat "concept via mimic-code" / "implemented by the vendored comorbidity/charlson.sql". VENDOR.json's complete file list has only comorbidity/charlson.sql (Charlson index); no Elixhauser SQL is vendored, and the AHRQ Elixhauser register row's own obtain-steps require a download. reading.md:39 echoes the overstatement ("Charlson/Elixhauser code lists behind the vendored charlson.sql").
- **Fix direction:** Correct the Elixhauser cells to state the free path is the AHRQ CSR download (or new SQL written at EP-37/38), reserving the "vendored, nothing to download" verdict for Charlson only, and trim the reading.md Quan entry to match.
- **Verifier:** confirmed - Vendor tree and VENDOR.json hold only comorbidity/charlson.sql (Charlson-only); vocabularies.md:126 and register rows :39-:40 claim Elixhauser is vendored with nothing to download, contradicting row :39's own AHRQ download steps; reading.md:39 repeats it. Anchor and fix direction correct.

### SGD-1 - git-prefixed file-content readers classified as allow-listed read-only project commands

- **Area / anchor:** session-guard - `mimicwarehouse/scripts/claude_pretool_guard.py:43`
- **Class / severity:** governance-precision / medium (owner-decision, S)
- **Evidence:** ALLOW_RE (claude_pretool_guard.py:42-46) rescues any command beginning with a git subcommand from a data-token deny, and .claude/settings.json:121-125 auto-approves the git log/status/diff/show families. git's no-index diff and no-index grep modes read arbitrary filesystem paths (data root included) and print their content, so this input class passes both the hook and the permission layer without a prompt, contradicting GOVERNANCE section 4 item 1 (sessions receive only aggregates). test_ep165.py's decision matrix has no git-reader case.
- **Fix direction:** Add a NESTED_RE-style refinement that withholds the git rescue when content-reading flags (no-index modes) are present, and narrow or drop the Bash/PowerShell git diff/show allow rules.
- **Verifier:** downgraded - Mechanics confirmed: ALLOW_RE's bare git prefix (line 43) rescues git no-index content readers, and settings.json auto-approves git diff*/show*, so the class runs promptless. Downgraded: the guard is documented as best-effort; safe_query and governance prose remain the primary controls.

### SGD-2 - Grep glob field excluded from the hook's checked fields

- **Area / anchor:** session-guard - `mimicwarehouse/scripts/claude_pretool_guard.py:55`
- **Class / severity:** governance-precision / medium (owner-decision, XS)
- **Evidence:** CHECKED_FIELDS (claude_pretool_guard.py:51-57) checks Glob's pattern as a filesystem path but for Grep checks only path, and the Grep tool also takes a glob field that selects files by extension. A Grep call whose path is a clean directory name and whose glob names a data-shaped extension is classified as doc work, which also sidesteps the intent behind Read(**/*.csv) that keeps sessions off the fixture CSVs (content-mode Grep returns the rows).
- **Fix direction:** Add "glob" to Grep's checked fields (it is a filesystem selector like Glob's pattern, not content).
- **Verifier:** confirmed - Grep's glob field is a filesystem selector but missing from CHECKED_FIELDS (line 55), while Glob's pattern is checked at line 56; a token-free path plus a data-shaped glob is classified as doc work. Medium stands: the hook is declared defense-in-depth. Fix: add "glob" to Grep's tuple.

### SGD-3 - G4 content scan skips notebook source cells and several tracked text types

- **Area / anchor:** session-guard - `mimicwarehouse/src/mimicwarehouse/guard.py:133`
- **Class / severity:** governance-precision / medium (owner-decision, S)
- **Evidence:** TEXT_EXTENSIONS (guard.py:133-151) omits .ipynb, so check_entry (guard.py:441-445) applies only the G3 outputs rule to notebooks: a real-band id typed into a cleared notebook's code or markdown cell commits unflagged, though GOVERNANCE section 3 bans band ids in any committed file. Shell/script types (.sh, .psm1, .bat, .cmd) and .rst/.xml are likewise unscanned. test_ep04.py:259-267 pins that non-listed extensions are skipped but no test covers the notebook-source case.
- **Fix direction:** Run id_band_hits over notebook JSON bytes (it is UTF-8 text) and add the missing script/markup extensions to TEXT_EXTENSIONS.
- **Verifier:** confirmed - Verified: is_text_candidate excludes .ipynb/.sh/.psm1/.bat/.cmd/.rst/.xml, so check_entry applies only G3 to notebooks; a band id in cleared notebook source commits unflagged, contra GOVERNANCE section 3. Anchor 133 exact. Medium stands: defense-in-depth gap; safe_query still masks ids.

### SGD-4 - pretool-hook selfcheck passes with a dead absolute hook command

- **Area / anchor:** session-guard - `mimicwarehouse/src/mimicwarehouse/guard.py:721`
- **Class / severity:** robustness / medium (owner-decision, S)
- **Evidence:** _pretool_hook_check (guard.py:721-737) verifies only that the script basename appears somewhere in a registered command string and that the script exists under the current repo_root. The registered command (settings.json:152) hard-codes this clone's absolute venv-python and script paths, so after a clone move or venv rebuild the selfcheck stays green while the hook command fails to launch — and a failed PreToolUse hook is non-blocking, leaving layer 3's hook silently inert despite the settings _readme's re-wire warning.
- **Fix direction:** Have the selfcheck resolve the registered interpreter and script paths and require both to exist and to lie under this repo_root (or move the registration to $CLAUDE_PROJECT_DIR-relative paths, an owner-gated settings.json change).
- **Verifier:** confirmed - Verified: guard.py:721-737 tests only basename-substring in the command plus repo-root-relative script existence; settings.json:152 hard-codes clone-specific absolute interpreter/script paths, so after a clone move the selfcheck stays green with a dead, non-blocking hook. Medium is apt.

### SGT-2 - min/max/mode/quantile over subject-keyed non-count columns disclose individual raw values

- **Area / anchor:** safe-gate - `mimicwarehouse/src/mimicwarehouse/safe.py:127`
- **Class / severity:** governance-precision / medium (owner-decision, M)
- **Evidence:** AGGREGATE_FUNCTIONS (safe.py:127-155) admits min, max, mode, median, quantile unconditionally. On a subject-keyed table these return a specific individual's exact value (e.g. min(dod), max(admittime)) regardless of group size, and count-based rowwise suppression (safe.py:285-297) never removes them because they are not count cells.
- **Fix direction:** Owner decision on whether extreme-value aggregates over sensitive/date columns count as safe statistics or need per-column gating.
- **Verifier:** confirmed - Confirmed: min/max/mode/median/quantile pass the walk over non-identifier columns of subject-keyed tables (safe.py:127-155, 510-515); rowwise_suppress (285-297) filters only count cells, so an extreme-value cell is one person's raw value at any group size. Medium: no id linkage, dates shifted.

### TST-2 - Hard-coded fixture row counts in test_ep30/21/22 contradict the suite's churn rule

- **Area / anchor:** test-vacuity - `mimicwarehouse/tests/ep/test_ep30.py:294`
- **Class / severity:** convention / medium (now-should, S)
- **Evidence:** test_ep30.py:294, 328 pin the labevents fixture row count (9619, also as a comma-formatted string), and test_ep30.py:173, 190 plus test_ep21.py:316 and test_ep22.py:332, 352 pin the 120/119 subject counts as literals. tests/README.md:105-107 states downstream tests read counts from manifest.json / build_plan() and never hard-code literals. The planned fixture 0.3.0 regeneration (EP-41 vocab extension) will move these counts and break three earlier modules at once.
- **Fix direction:** Read expected counts from tests/fixtures/manifest.json (as test_ep17/18/20/23-27 already do) or from fixtures.spec.build_plan(); keep only the derived small-cell arithmetic in comments.
- **Verifier:** confirmed-with-corrections - Anchor 294 correct; secondary anchors are test_ep30.py:172/:188, not 173/190. All literals verified (test_ep30, test_ep21:316, test_ep22:332/352); tests/README.md:105-107 states the churn rule. Caveat: the 120 subject count comes from the spec default and may survive a vocab-only regen.

### WIN-2 - Pass-2 publish and delete operations lack the transient-PermissionError retry loop

- **Area / anchor:** windows-av - `mimicwarehouse/src/mimicwarehouse/loader/buckets.py:278`
- **Class / severity:** robustness / medium (now-should, S)
- **Evidence:** os.replace(tmp, part) at buckets.py:278, the stale-tmp unlink at 256-257, and the raw-file unlinks in _sweep_raws (287-288) are all single-attempt, while every other publish site (paths._retry, catalog/profile._publish, inventory._atomic_write_text) wraps the same operations in the documented 20-attempt retry because AV/indexer holds are transient (D-42, DESIGN §5 note). These files are freshly written multi-GB Parquet — exactly the class scanners hold.
- **Fix direction:** Route the pass-2 replace/unlink calls through the shared retry helper in mimicwarehouse.paths.
- **Verifier:** confirmed - Verified: os.replace at buckets.py:278, tmp unlink at 256-257, and _sweep_raws unlinks at 287-288 are single-attempt, while paths._retry documents the transient AV-hold PermissionError policy and covers swap_dir. Resume design bounds impact to an aborted run (one bucket re-sorted), so medium stands.

### WIN-3 - swap_dir fails the whole stage when post-publish .old cleanup exhausts retries, unlike swap_catalog

- **Area / anchor:** windows-av - `mimicwarehouse/src/mimicwarehouse/paths.py:100`
- **Class / severity:** robustness / medium (now-should, XS)
- **Evidence:** paths.py:99-100 lets a PermissionError from rmtree of the .old aside propagate after the ~10.5 s retry budget, even though the new data is already published; a scanner walking a multi-GB just-renamed directory can exceed that budget. swap_catalog handles the identical delete-pending case by warning and deferring to the next build's stale-.old sweep (catalog/build.py:224-229), and swap_dir's own step 2 already performs that sweep.
- **Fix direction:** Catch the final cleanup failure in swap_dir, log a warning, and let the next invocation's stale-.old sweep remove it — matching the swap_catalog paradigm.
- **Verifier:** confirmed - Verified: paths.py:100 step-5 rmtree of .old propagates PermissionError after retries, failing the stage post-publish; catalog/build.py:224-228 warns and defers the same case, and swap_dir step 2 (lines 86-87) already sweeps stale .old, so catch-warn-defer is the right fix. Medium stands.

### WIN-4 - Stale-.new sweeps use bare shutil.rmtree without the retry loop

- **Area / anchor:** windows-av - `mimicwarehouse/src/mimicwarehouse/loader/stage.py:147`
- **Class / severity:** robustness / medium (now-should, XS)
- **Evidence:** stage.py:146-147 and buckets.py:453-454 remove a leftover .new directory from a crashed stage with an unretried shutil.rmtree; a transient AV/indexer hold on any file inside it fails the restage immediately with a raw PermissionError. The same module family retries every other rename/remove for exactly this reason (paths.py:56-66).
- **Fix direction:** Use a retrying rmtree (the canary's _rmtree_retry shape or paths._retry) for the stale-.new sweeps.
- **Verifier:** confirmed - stage.py:147 and buckets.py:454 sweep a stale .new via bare shutil.rmtree; paths._retry (paths.py:56-66) wraps every other rename/remove for the transient AV-hold case (D-42). Crash recovery is when fresh AV-scanned files are likeliest; retrying rmtree is the right fix.

### CLI-3 - D38_ALLOW_LIST still enumerates seven paths after the D-38 addendum grew the allow list to nine

- **Area / anchor:** cli-config - `mimicwarehouse/src/mimicwarehouse/doctor.py:85`
- **Class / severity:** doc-drift / low (now-should, XS)
- **Evidence:** doctor.py:85-93 defines seven entries, and the antivirus warn detail (doctor.py:677-681) tells the owner to exclude exactly these. D-43 item 5 (DECISIONS.md:846-849) added the uv cache and the Claude scratchpad temp path, "seven -> nine paths". The workspace README is internally split: the environment section says "nine-path Malwarebytes allow list" (README.md:57-58) while the doctor section still says "the seven D-38 paths" (README.md:163).
- **Fix direction:** Extend D38_ALLOW_LIST with the two EP-165-added paths and align the two README sentences on nine.
- **Verifier:** downgraded - Verified: D38_ALLOW_LIST (doctor.py:85-93) has seven entries vs the nine-path D-38 addendum (DECISIONS.md:846-849); README.md:163 vs 57-59 disagree. Downgraded: the constant is advisory-only and the owner confirmed all nine exclusions in place on 2026-08-28. Fix direction stands.

### DAG-2 - Build-lock acquisition is check-then-write, not atomic

- **Area / anchor:** dag - `mimicwarehouse/src/mimicwarehouse/dag/runner.py:293`
- **Class / severity:** robustness / low (now-should, S)
- **Evidence:** acquire_lock (runner.py:283-314) tests path.is_file() and later calls path.write_text(); there is no O_CREAT|O_EXCL create, and the break-lock path unlinks then rewrites with the same gap. Two builds started concurrently against the same data root can both pass the existence check and both proceed, violating the single-writer rule (DESIGN section 6) the lock exists to enforce.
- **Fix direction:** Create the lock with os.open(O_CREAT|O_EXCL|O_WRONLY) and treat EEXIST as the contended case; on break_lock, replace via the same exclusive-create after unlink and re-verify.
- **Verifier:** downgraded - Verified: runner.py:293 is_file() check then :313 write_text, no exclusive create; break-lock path shares the gap. But the race needs near-simultaneous starts on a single-owner machine, and DuckDB's own file lock partially backstops. Real defect, O_EXCL fix is right; impact is low, not medium.

### DAG-4 - O_APPEND ledger append is not atomic between processes on Windows

- **Area / anchor:** dag - `mimicwarehouse/src/mimicwarehouse/dag/benchmarks.py:92`
- **Class / severity:** robustness / low (next-replan, S)
- **Evidence:** append() (benchmarks.py:87-98) relies on O_APPEND for torn-line safety, but the MSVC runtime implements _O_APPEND as seek-to-end-then-write, which is not atomic across processes. Concurrent writers do exist as built: the build lock serializes builds per data root, but test_ep28 (test_ep28.py:391-426) appends kind: verify lines without any lock and could run while a background build appends step lines — also contradicting the module docstring's 'Nothing else writes to this file' claim (benchmarks.py:4).
- **Fix direction:** Either serialize appends with a small OS-level file lock (msvcrt.locking around the write) or document the single-writer assumption honestly and have verify runs check the build lock first; update the docstring to admit the kind: verify writer.
- **Verifier:** downgraded - Mechanism and second writer confirmed (CRT _O_APPEND seek-then-write; test_ep28 verify appends lock-free; docstring claim false; same pattern in safe.py:266). Downgraded: overlap requires violating the documented build-then-verify sequencing, and worst case is one torn line in telemetry-only JSONL.

### DRF-4 - DESIGN.md framing paragraph stops the shipped inventory at the P0/P1a modules

- **Area / anchor:** docs-drift - `mimicwarehouse/DESIGN.md:6`
- **Class / severity:** doc-drift / low (now-should, XS)
- **Evidence:** Lines 5-7 say 'the P0/P1a modules (EP-1 ... EP-12, EP-164, EP-165) have since shipped'. All of P1 (EP-13 ... EP-16, EP-166 ... EP-171) and P2 (EP-17 ... EP-32) have since shipped, as the file's own later dated notes record.
- **Fix direction:** Update the framing sentence to state that P0-P2 have shipped (or drop the enumerated EP range and defer wholly to the workspace README living inventory it already points at).
- **Verifier:** downgraded - Drift is real: DESIGN.md line 6 stops the shipped list at P0/P1a while its own dated notes (line 153: "P2 staging complete") record P1/P2 shipped. But the same sentence defers to the workspace README living inventory, so impact is cosmetic; low, not medium. Fix direction as proposed.

### LGR-3 - GOVERNANCE section 8 event coverage exceeds what is implemented

- **Area / anchor:** ledger-audit-integrity - `mimicwarehouse/GOVERNANCE.md:176`
- **Class / severity:** governance-precision / low (owner-decision, S)
- **Evidence:** Section 8 says audit.jsonl receives one line for every safe_query, row-view toggle, export attempt, protocol freeze/run, and external-source ingestion. Only safe_query writes audit lines today; the shipped EP-21 owner row-view path in catalog/connect.py (role resolution at connect.py:91-93) writes nothing to the audit trail, and the other event classes belong to future EPs with no phasing note in the section.
- **Fix direction:** Either add an audit line on owner-role catalog opens or annotate section 8 with which event classes are live versus scheduled (EP numbers), so the contract matches the implementation.
- **Verifier:** downgraded - Anchor correct but evidence fails: no row-view path is shipped — connect.py only resolves a role; safe.py line 58 defers owner row viewing to a future audited app path. Section 8 already cites future EPs, so it reads as the target contract; only a minor phasing-annotation nit remains.

### LGR-4 - Six distinct append implementations behind the one-JSONL-helper unification

- **Area / anchor:** ledger-audit-integrity - `mimicwarehouse/src/mimicwarehouse/dag/benchmarks.py:87`
- **Class / severity:** convention / low (now-should, M)
- **Evidence:** Map: (1) safe._append_audit (safe.py:260-271, O_APPEND + fsync) and (2) benchmarks.append (dag/benchmarks.py:87-98) are byte-identical duplicated logic; (3) loader.manifest.append_manifest (manifest.py:113-124, buffered 'a', no fsync); (4) canary manifest churn (canary.py:387-390, per-line write + flush, deliberately a simulation); (5) inventory._Log file appends (inventory.py:734-742, 'a' + flush); (6) jobs.launch log header (dag/jobs.py:181-183, 'ab' + flush).
- **Fix direction:** Extract one shared jsonl_append(path, payload) helper (single encoded blob, O_APPEND, short-write check, fsync, retry) for writers 1-3; leave the canary simulation and the plain-text log appenders (5, 6) as they are, documented as non-ledger.
- **Verifier:** downgraded - Evidence verified at all six sites; anchor correct. safe._append_audit and benchmarks.append are byte-identical; append_manifest is a weaker buffered variant; canary/log appenders rightly excluded. Pure duplication, no correctness or durability defect in practice — convention debt, low severity.

### P01-1 - Doctor antivirus warn detail still names the seven-path D-38 allow list; D-38 was amended to nine paths

- **Area / anchor:** p0p1-drift - `mimicwarehouse/src/mimicwarehouse/doctor.py:85`
- **Class / severity:** governance-precision / low (now-should, XS)
- **Evidence:** doctor.py:85 defines D38_ALLOW_LIST with seven entries, cited in the check_antivirus warn detail (doctor.py:679) as "the D-38 paths that must be excluded there too", and tests/ep/test_ep164.py:192 pins len == 7. The D-38 addenda of 2026-08-28 (DECISIONS.md, EP-165 addendum and the EP-166 post-session confirmation) record the Malwarebytes allow list as nine paths, adding the uv cache and the Claude scratchpad under %LOCALAPPDATA%. The doctor's operational reminder therefore omits two paths the recorded decision requires.
- **Fix direction:** Add the two 2026-08-28 paths to D38_ALLOW_LIST (bumping the test_ep164 pin), or record under D-38 that the doctor deliberately names only the toolchain/data subset.
- **Verifier:** downgraded - Evidence verified: doctor.py:85 seven-entry list, warn detail :678-679, test_ep164.py:192 pin == 7, vs nine-path D-38 EP-165 addendum. Downgraded: the nine-path allow list is owner-confirmed in place (EP-166 addendum), so this is stale reminder text, not a control gap. Fix direction as proposed.

### P3C-1 - EP-35 run_id format embeds a compact date that falls inside a real id band, colliding with G4 and EP-43's id-band scan

- **Area / anchor:** p3-compat - `roadmap/EP-35-run-ledger.md:33`
- **Class / severity:** forward-compat / low (next-replan, XS)
- **Evidence:** EP-35 item 1 fixes run_id as a compact `YYYYMMDDTHHMMSSZ-<6 hex>` string; every current-era compact date starts with 20 and is an 8-digit integer inside the "2xxxxxxx" hadm band. EP-43 item 2b (roadmap/EP-43-disclose-primitives.md:51) FAILs 8-digit band integers in inline text, and EP-44/45/48/49/53 all require `mwh disclose check` to pass on artifacts whose reproduction blocks quote run ids; guard G4 already flags compact dates in committed file names (EP-32 lesson).
- **Fix direction:** Amend EP-35 to a run_id form with separators (e.g. ISO-dashed timestamp + hex) so no 8-digit run is band-shaped, or have EP-43 define a timestamp-token exemption; settle it in the D1 amendment before EP-35 ships the format.
- **Verifier:** downgraded - Guard ID_TOKEN (guard.py:155) rejects digit runs abutting alphanumerics, so the run_id's date+T never matches in text, and EP-43 item 2b mandates reusing that scanner; run_id is also on EP-43's column allow-list. Only PATH_ID_TOKEN (names) matches — the already-documented EP-32 filename lesson.

### P3C-4 - EP-47 acceptance query over marts.cohorts is refused by the shipped aggregate-only rules and depends on B1c covering marts registries

- **Area / anchor:** p3-compat - `roadmap/EP-47-cohort-compiler-attrition.md:76`
- **Class / severity:** forward-compat / low (next-replan, S)
- **Evidence:** The acceptance runs `mwh sql "SELECT id, version, tier, rows FROM marts.cohorts ORDER BY 1,2,3"`: no count-family column and bare non-grouped columns, which safe.py:517-529 refuses; only meta.*/dims/information_schema are exempt (safe.py:482-488), and dim status is contract-derived so a marts registry table can never acquire it. EP-33's B1c names "future marts.*-registry tables" but no registration mechanism exists for tables outside the EP-9 contract.
- **Fix direction:** Amend EP-47 to register marts.cohorts in whatever named exemption registry B1c ships (and have B1c define registration for non-contract tables), or rewrite the acceptance query in count-family form.
- **Verifier:** downgraded - Evidence holds: safe.py exemption (lines 482-488, dims contract-derived) cannot cover marts.cohorts, so EP-47's line-76 query would be refused today. But EP-33 B1c already plans the exemption list naming future marts.*-registry tables and ships before EP-47; residual gap is a brief-coordination nit.

### RES-3 - Landing-convention text pins a 15-key Settings.layout contract that EP-167 already grew to 18 keys

- **Area / anchor:** p1-resources - `mimicwarehouse/docs/resources/vocabularies.md:78`
- **Class / severity:** doc-drift / low (now-should, XS)
- **Evidence:** vocabularies.md:77-81 says "the 15-key contract that `mwh paths` and `test_ep03` assert stays closed until the first brief that actually writes here (EP-40 or EP-143) decides otherwise". config.py:15/628 and test_ep03.py:190-194 now assert 18 layout keys (lake_fixture/lake_demo/lake_rejects added at EP-167), so both the count and the "stays closed until EP-40/EP-143" claim were contradicted by a shipped EP.
- **Fix direction:** Restate the paragraph against the current 18-key contract and note that EP-167 amended it, keeping the operative rule (no vocab layout key until EP-40/EP-143).
- **Verifier:** downgraded - Drift confirmed: vocabularies.md:78 pins "15-key", but config.py (15/88/628) and test_ep03.py:194 assert 18 keys since EP-167. Yet the operative rule (no vocab layout key until EP-40/EP-143) still holds; only the count and "stays closed" clause are stale — cosmetic doc-drift, so low not medium.

### SGT-3 - CAST of an aggregate and arithmetic over aggregates are refused

- **Area / anchor:** safe-gate - `mimicwarehouse/src/mimicwarehouse/safe.py:510`
- **Class / severity:** governance-precision / low (next-replan, M)
- **Evidence:** is_aggregate at safe.py:510-514 requires class==FUNCTION and not is_operator, so `CAST(count(*) AS BIGINT) AS n` (class CAST) and `sum(a)/count(*) AS rate` (operator FUNCTION) fail the check and are refused at safe.py:519 even though they compute legitimate suppressible statistics; matches the known sum()+CAST limitation.
- **Fix direction:** Admit CAST/arithmetic nodes whose leaf operands are all aggregates, GROUP BY keys or constants (recursive descent) rather than requiring a bare aggregate at the top.
- **Verifier:** downgraded - Confirmed at safe.py:510-519: only bare aggregate FUNCTION nodes pass; CAST-wrapped aggregates and operator arithmetic are refused. But the strictness is documented as deliberate (safe.py:17-26, 123-126), fails closed, and has workarounds (avg, FILTER counts) — usability friction, not a gate defect.

### TST-1 - Sortedness assertions degenerate to a one-column prefix for pharmacy and prescriptions

- **Area / anchor:** test-vacuity - `mimicwarehouse/tests/ep/test_ep25.py:70`
- **Class / severity:** test-gap / low (now-should, S)
- **Evidence:** test_ep25.py:70-75 defines SORT_PREFIX with pharmacy and prescriptions checked on (subject_id,) only, because DuckDB tuple comparison over a nullable member goes NULL; microbiologyevents is trimmed to two of four keys. test_ep24.py:61 applies the same trim to emar_detail (drops parent_field_ordinal). Both the fixture-tier checks (test_ep25.py:277-287) and dev-tier checks verify only the trimmed prefix, so the contracted tie-break ordering (starttime, pharmacy_id) is never verified anywhere on any tier — a loader regression in per-bucket ORDER BY tie-breaks would pass.
- **Fix direction:** Use a NULLS-LAST-aware comparison — build the lag tuple from (col IS NULL, col) pairs (or coalesce to type minima) — so the full contract sort key is asserted for every table.
- **Verifier:** downgraded - Prefix-trim facts confirmed (test_ep25.py:70-75, 277-287; test_ep24.py:61), but the loader ORDER BY tie-break path is generic and full-key-verified in test_ep18/23/24/26/27, so only a table-specific nullable-key ordering fault escapes. Gap real; impact overstated, so severity drops to low.

### TST-3 - Dev/full acceptance can go all-skip green; stale-catalog inconsistency is skipped, not failed

- **Area / anchor:** test-vacuity - `mimicwarehouse/tests/ep/test_ep23.py:233`
- **Class / severity:** test-gap / low (owner-decision, M)
- **Evidence:** conftest.py:280-282 skips every dev/full test whose artefact is missing, and pytest exits 0 on all-skips, so `mwh verify EP-23 -- --tier dev` (and poe test-dev/test-full with a mis-set MWH_DATA_ROOT) passes having executed nothing. Compounding this, test_ep23.py:233-237 (and the same pattern in test_ep24/25/26/27) calls pytest.skip on duckdb.CatalogException even after dev_ready(QN) confirmed status.json marks the step ready — a lake/catalog inconsistency on a provisioned machine is reported as not-ready rather than as a defect.
- **Fix direction:** Distinguish fresh-checkout absence (skip) from provisioned-machine inconsistency (fail when status.json says dev_ready but the catalog lacks the table), and consider a session-level check or verify option that reports when a tier run executed zero non-skipped tier tests.
- **Verifier:** downgraded - Mechanics confirmed (skip pattern in test_ep23-27; conftest is tests/conftest.py:276-282), but both behaviors are documented design: catalog-predates-step is a legitimate transient state, fixture tests always execute under verify, and skips print reasons. Residual gap is a minor hardening item.

## Completeness critique

- **The catalog package (src/mimicwarehouse/catalog: connect.py, build.py, profile.py, dictionary.py, cli.py) has no owning lens; duckdb-semantics (2 verified) is too thin to have absorbed it.** - This is query-generating code that runs against the real data lake (ATTACH/instance-cache gotchas were already found here in EP-30); build and profile shipped inside the owed EP-17..32 window and defects here surface as wrong catalogs or unsafe connections. (suggested: catalog-connect-build-profile: connection lifecycle, role defaulting, dictionary/profile aggregate discipline, DuckDB 1.5.5 pin interactions)
- **EP-31/32 deliverables tracer.py + sql/tracer_first_icu_mortality.sql and runs_cli.py + dag/benchmarks.py have no statistical-correctness or benchmarks-recording lens; none of the 14 lenses names them.** - These are the newest shipped surfaces in the owed window; the quasi-separation zero-cell exclusion in fit() and the k=11 interplay of the tracer SQL are exactly the class of subtle-wrong-answer defect no ran lens targets, and benchmark ledger writes feed docs/analyses. (suggested: tracer-stats + runs-benchmarks: fit() exclusion correctness, SQL/safe_query tier parity, benchmark record schema and run-id hygiene)
- **p0p1-drift returned 1 verified + 3 carried-low across seven explicitly owed surfaces (config, guard, doctor, verify, schema, inventory, fixtures) - implausibly low for that area.** - The brief owes spot-drift on all seven; one verified finding across config.py, guard.py, doctor.py, verify.py, inventory.py and the schema/fixtures packages suggests the lens sampled rather than swept, leaving owed scope effectively unassessed. (suggested: re-run p0p1-drift split per surface (doctor/verify/inventory as one pass, config/guard as another) with per-file finding accounting)
- **contract-fixtures returned 1 verified across ~8 fixtures modules, 6 schema YAMLs incl. column_maps/demo_2_2_to_3_1.yaml, and tests/fixtures manifest.json + COVERAGE.md.** - Fixtures are the synthetic-id (>= 90 million) safety boundary and the basis of every green verify; a single verified finding over ~17 files is a plausibility outlier, and the manifest/COVERAGE pair may never have been diffed against generator code. (suggested: re-run contract-fixtures with explicit per-module checklist: id-band enforcement, YAML/contract/generator three-way drift, manifest freshness)
- **concepts/vendoring.py and the ~200-file vendored mimic-code SQL tree (src/mimicwarehouse/concepts/vendor/...) are covered by no lens.** - Vendored third-party SQL carries license obligations (LICENSE file, gpl dependency group exists) and pin/hash provenance; silent drift or local edits to vendored concepts would corrupt future concept-runner EPs and the licensing story. (suggested: vendoring-provenance: vendoring.py pin/hash logic, vendored-tree immutability vs git history, license placement)
- **Packaging and environment config unassessed: pyproject.toml (dependency groups, poe tasks, ruff/pytest config), uv.lock, .python-version, .env.example.** - The pyarrow<25 ui pin, default-groups=['dev'], poe task wiring, and pre-commit hook definitions are load-bearing for D-42 session tooling and every verify command; drift here breaks sessions in ways no code lens detects. (suggested: packaging-config: pyproject group/pin audit vs DESIGN/DECISIONS claims, lock staleness, env-example vs config.py parity)
- **scripts/roadmap_check.py has no lens (scripts/claude_pretool_guard.py was plausibly covered by session-guard, but the roadmap checker was not).** - It is the automation that enforces roadmap/README ledger consistency (checkmark hashes, brief structure); a defect there lets exactly the record/reconcile drift EP-33 workstream A exists to fix pass silently. (suggested: repo-scripts: roadmap_check.py correctness against current README table format, incl. the guard-G4 date-in-filename rule)
- **UI/branding surface unassessed: theme.py, .streamlit/config.toml, docs/brand/ (4 SVGs + README).** - theme.py is imported project code with no owning lens; brand SVGs live in docs/ where the disclosure rule says non-manifest artifacts need mwh disclose sidecars - whether pure-brand assets are exempt was never checked. (suggested: theme-brand pass: theme.py vs .streamlit config consistency, brand-asset disclosure-policy classification)
- **Absent test files inside owed scope: no tests/ep/test_ep07.py, test_ep16.py, test_ep166.py, or test_ep170.py, though EP-16 and EP-165..171 are owed.** - test-vacuity inspects files that exist and cannot flag missing ones; if EP-166/EP-170/EP-16 were code-bearing rather than docs-only, their acceptance was never re-verified by any lens or the retro batches. (suggested: coverage-absence check: map each owed EP to its test/verify artifact and confirm docs-only status for the four EPs with no test file)
- **Shared test infrastructure unassessed: tests/conftest.py and tests/helpers.py sit outside the per-EP files the test-vacuity lens enumerated.** - A permissive fixture or assertion helper in shared infra can hollow out every EP test at once - the highest-leverage vacuity location - and the safe_query-based fixture-expectation pattern lives partly in these helpers. (suggested: test-infra vacuity: conftest fixture defaults, helper assertion strictness, tier selection defaults)
