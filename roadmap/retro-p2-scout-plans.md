# EP-33 Workstream B scout plans (retro-p2-scout-plans.md)

Machine-rendered record of the read-only implementation scouting for Workstream B items B1-B6 and B8, produced during the EP-33 second execution attempt (2026-08-31; see the brief's second-attempt section). B7 was deliberately not scouted (session-guard work is handled as an owner-applied diff package per the first addendum's tuning 5); B9 already landed with Workstream A. **Nothing below is implemented** - these are anchors, caller lists and API designs for the implementing session, which still owns verification against the live tree (line numbers drift). Public-surface renames require owner approval at the triage checkpoint (brief invariant 6).

## B1 - safe_query robustness (cast-around-aggregate, set operations, registry exemptions, error taxonomy)

B1 plan. (a) CAST(sum(x) AS BIGINT) fails the class=='FUNCTION' test (safe.py:510-514, 363-370); add _unwrap_cast; closed set unchanged; unaliased cast-counts refused so suppression name-matching holds. (b) Set ops: implement — replace the refusal at safe.py:428-432 with leaf-SELECT recursion and per-leaf select-list checks; count-family items must appear positionally in the leftmost leaf. (c) REGISTRY_SCHEMAS/REGISTRY_TABLES + is_registry_ref replace exempt_ref (safe.py:482-486); VARCHAR-64 rescoped to not-exempt (safe.py:568); tier/k defaults from settings. (d) refusal exit 3; usage exit 2 (k<1, row_cap<1, unknown tier — audited via usage()); CatalogOpenError environment, exit 2, unaudited; safe_cli_errors guard for mwh sql (catalog/cli.py:351-365) and mwh tracer (tracer.py:673-680).

**Changes** (file - action):

- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1a: add _unwrap_cast(expr) descending class=='CAST' (type OPERATOR_CAST; decide TRY_CAST via the try_cast flag) into expr['child'], alias kept on the outer node. Apply in the aggregate test at safe.py:510-514 and in _is_count_family_item at safe.py:363-370. AGGREGATE_FUNCTIONS (safe.py:127-155) stays closed: CAST may wrap one allowed aggregate call only, never arithmetic (DIS-3 stays parked).
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1a suppression-name rule: an unaliased CAST-wrapped count-family item yields an output column named 'CAST(count_star() AS ...)' which _count_columns (safe.py:547-554) misses — refuse it with an 'alias the cast count column' reason so suppression cannot be bypassed.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1b: implement set ops, not re-park. Replace the refusal at safe.py:428-432 with recursive _leaf_selects over SET_OPERATION_NODE (left/right/setop_type/setop_all; non-SELECT_NODE leaf refused). Extract the select-list loop (safe.py:491-529) into _check_select_list(node, identifiers, exempt, branch_label) run per leaf; the _iter_dicts walk (safe.py:449-478) covers all branches already.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1b suppression across branches: output names come from the leftmost leaf, so if any branch's item at position i is count-family, the leftmost leaf's item at i must be too (refuse otherwise, naming branch/position); union all leaves' count_aliases. Each leaf needs its own count-family column and _is_group_key against its own node. Row-wise k-suppression then applies to the combined frame.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1c registry list: constants near safe.py:169 — REGISTRY_SCHEMAS = {'meta','information_schema'} (owner decides whether 'runs' joins), REGISTRY_TABLES = {'marts.cohorts'} for future named registries (FC-18) — plus is_registry_ref(schema, table) using them + contract dims (Table.is_dim via _contract_names, safe.py:232-242). Replaces exempt_ref at safe.py:482-486; export all three.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1c VARCHAR-64 scope: change _result_problem's condition at safe.py:568 from analysis.reads_subject_keyed to `not analysis.exempt` — dims/meta stay exempt (dim labels exceed 64 chars, FC-18); mimiciv_derived/non-registry marts become scanned (pure tightening). Drop _Analysis.reads_subject_keyed (safe.py:316).
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1c defaults: signature safe_query(sql, *, tier: Tier|str|None = None, k: int|None = None, row_cap=200, timeout_s=120, actor=None, settings=None); resolve tier or settings.default_tier (config.py:526) and k or settings.k_suppression (config.py:535) after settings resolution at safe.py:607; update docstring at safe.py:591-604.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - B1d: add EXIT_REFUSED=3 / EXIT_USAGE=2; SafeQueryError (safe.py:185-186) becomes the usage class. Add usage(reason) beside refuse() (safe.py:615-634): audits with a 'usage: ' refusal_reason, returns SafeQueryError. Route k<1 (safe.py:636), row_cap<1 (safe.py:643), unknown tier (safe.py:608, today unaudited) through it; k<K_FLOOR on dev/full (safe.py:638-642) stays a refusal.
- `mimicwarehouse/src/mimicwarehouse/catalog/cli.py` - B1d: keep EXIT_REFUSED at line 47 as a re-export of safe.EXIT_REFUSED (tests import it); add contextmanager safe_cli_errors(prefix) mapping SafeQueryRefused->Exit(3), SafeQueryError->Exit(2), CatalogOpenError->Exit(2); rewrite run() at lines 351-365 on it (SafeQueryError currently escapes to exit 1). Let mwh sql pass tier=None/k=None through instead of resolving at lines 341-343.
- `mimicwarehouse/src/mimicwarehouse/tracer.py` - B1d: replace the except block at tracer.py:673-680 with safe_cli_errors('mwh tracer'); add SafeQueryError and TracerError to the exit-2 class (both traceback to exit 1 today). Update the stale docstring claim at tracer.py:17-21 (walk 'cannot re-cast') to note B1a lifted it; the shipped count(*) FILTER statements stay untouched (no restage).
- `mimicwarehouse/tests/ep/test_ep33.py` - New module, marker ep_33, fixture tier via the fixture_lake_settings pattern; reuse the _assert_refused/_audit_lines helper pattern from tests/ep/test_ep30.py:67-76. Cases listed under tests.
- `mimicwarehouse/DESIGN.md` - Dated note under the section-12 safe-query note (near DESIGN.md:598; tracer note at 1211-1212): cast-around-aggregate verifies, set ops with per-branch checks + leftmost positional count rule, registry exemption names, settings defaults, three-way error taxonomy. No history rewrites.
- `mimicwarehouse/DECISIONS.md` - Addendum under D-31 (workstream A4): dated follow-up to the count(*)-FILTER lore at DECISIONS.md:446-447 — cast restriction lifted for casts directly around closed-set aggregates; arithmetic-over-aggregates stays refused (DIS-3); record the taxonomy and the audited 'usage:' line format (EP-35 ledger views will see it).
- `roadmap/final-roadmap.md` - Mark v2 DIS-2 (set-operation support, mirrored from EP-30 Parked, roadmap/EP-30-safe-query-audit.md:121-124) resolved by EP-33 B1b; DIS-3 stays parked with a note that bare cast-around-aggregate moved out of it.
- `roadmap/EP-33-replan-p2.md` - C4 interaction: the sum()-vs-FILTER gotcha (brief line 243, 'until B1a lands') becomes historical; the C4 page entry should say cast-around-sum now verifies. The machine-local memory note 'never sum() in safe_query' also goes stale — raise at the owner checkpoint.

**Tests:**

- test_ep33: CAST(sum(anchor_age) AS BIGINT) AS s with count(*) AS n, GROUP BY anchor_year_group, fixture tier — allowed; result dtype Int64; audit line allowed=true (the mandated sum() regression).
- test_ep33: CAST(count(DISTINCT subject_id) AS INTEGER) AS n allowed; CAST(subject_id AS BIGINT) AS x refused with the identifier reason.
- test_ep33: closed set — CAST(anchor_age AS BIGINT) (bare column) and CAST(count(*) / 2 AS DOUBLE) (arithmetic, DIS-3) refused; unaliased CAST(count(*) AS INTEGER) refused by the alias rule.
- test_ep33: aliased cast-wrapped count is still k-suppressed (fixture tier, k chosen to drop a small cell; rows_suppressed > 0).
- test_ep33 set-op probe: UNION ALL of two aggregate SELECTs allowed, audited, suppressed row-wise; EXCEPT and INTERSECT variants allowed.
- test_ep33: set-op with a bare column in the right branch refused naming the branch; positional count-family mismatch refused.
- test_ep33: is_registry_ref — meta.* true, information_schema true, a contract dim true, marts.cohorts true, mimiciv_icu.icustays false; meta-only SELECT without a count column still allowed (EP-170 regression).
- test_ep33: safe_query without tier/k resolves settings.default_tier / settings.k_suppression (settings fixture with default_tier='fixture'; assert via the audit line's tier/k).
- test_ep33 taxonomy: k=0, row_cap=0, tier='bogus' raise SafeQueryError AND append an allowed=false audit line with a 'usage: ' reason; k=5 on dev stays SafeQueryRefused; mwh sql CliRunner: usage exits 2, refusal exits 3, CatalogOpenError exits 2.
- test_ep33: mwh tracer guard — SafeQueryError/TracerError paths exit 2 (monkeypatched run_tracer).
- Regression: poe test -m ep_30 and -m ep_31 stay green (grep confirms no test pins k<1/row_cap<1 as refusals), plus mwh verify EP-33 on fixture.

**Risks / preserve-semantics notes:**

- json_serialize_sql node shapes under pinned DuckDB 1.5.5 (CAST keys, try_cast flag, SET_OPERATION_NODE left/right, UNION BY NAME serialization) are from source knowledge, not observed — pin with fixture probes in test_ep33 first; refuse unrecognized shapes.
- Unaliased cast-wrapped counts would evade _count_columns and thus k-suppression — the refuse-unless-aliased rule is load-bearing; do not soften.
- Adding 'runs' to REGISTRY_SCHEMAS (so GROUP BYs over runs.audit refusal_reason survive the tightened 64-char scan) loosens the count-column rule for runs.* — owner-checkpoint decision; default plan excludes it and flags the question.
- Auditing usage errors with 'usage: ' refusal_reason changes audit-line semantics consumed by EP-35 ledger views — record in DECISIONS so EP-35 filters deliberately.
- Rescoping the free-text heuristic to not-exempt tightens mimiciv_derived/marts scanning; future label columns there may need LABEL_COLUMN_NAMES growth (governance only tightens — satisfied).
- Set-op support is a loosening relative to the EP-30 refusal; land per-branch rules and tests in the same commit and close DIS-2 in final-roadmap.md.
- Seeding REGISTRY_TABLES with marts.cohorts pre-dates EP-47's table — harmless, but note it so EP-47 does not add a duplicate mechanism.
- Tier default moves from the literal 'dev' to settings.default_tier (same value today); the only callers found — tracer.py and catalog/cli.py — pass tier explicitly or resolve the same setting, so no behavior change now.

**Proposed public-surface renames (checkpoint approval required):**

- `mimicwarehouse.catalog.cli.EXIT_REFUSED (canonical home)` -> `mimicwarehouse.safe.EXIT_REFUSED (catalog/cli.py:47 keeps a re-export)` - cited by: mimicwarehouse/src/mimicwarehouse/catalog/cli.py, mimicwarehouse/tests/ep/test_ep21.py, mimicwarehouse/tests/ep/test_ep30.py, roadmap/EP-30-safe-query-audit.md
- `safe.SafeQueryError as 'non-governance failure (bad arguments)'` -> `safe.SafeQueryError as the usage class (exit 2, audited via usage())` - cited by: mimicwarehouse/src/mimicwarehouse/safe.py, mimicwarehouse/tests/ep/test_ep30.py
- `exempt_ref (closure in _analyze, safe.py:482-486)` -> `is_registry_ref (public, module level, with REGISTRY_SCHEMAS/REGISTRY_TABLES)` - cited by: mimicwarehouse/src/mimicwarehouse/safe.py, mimicwarehouse/DESIGN.md, roadmap/EP-33-replan-p2.md, roadmap/retro-2026-08-18-findings.md

## B2 - one publish primitive (unify swap_dir / swap_catalog / runs-db swap)

B2 plan: new src/mimicwarehouse/publish.py with one retry loop (20 tries, 0.05s linear backoff), one NEW/OLD suffix pair, new_path_for/old_path_for, an observer(event, path) seam (canary EP-171 precedent), and two variants over one core: swap_dir (paths.py:69-101 verbatim) and swap_file (catalog/build.py:192-228 with caller-supplied blocked_hint replacing the tier arg). safe.build_runs_db already calls swap_catalog (safe.py:736/757), so migration = 3 source call sites + runs_cli catch + 9 test files + DESIGN map/dated note; top-level paths.py is deleted (removes the paths/loader-paths collision). Five per-site semantic differences must be preserved (aside-rename retry, publish op, .old removal tolerance, FileNotFoundError swallow scope, error types) - detailed in changes/risks.

**Changes** (file - action):

- `mimicwarehouse/src/mimicwarehouse/publish.py` - NEW. Suffix/retry constants; new_path_for/old_path_for; SwapError (bad args) + SwapBlockedError (aside rename failed, live target intact); shared _retry with observer; events restore-old/remove-stale-old/aside/publish/rollback/remove-old; swap_dir preserving paths.py:69-101 (is_dir + new==dest checks, retried aside, rmtree that raises on exhaustion).
- `mimicwarehouse/src/mimicwarehouse/publish.py` - swap_file preserving build.py:192-228: is_file check, un-retried aside rename raising SwapBlockedError(blocked_hint), bare os.replace, warn-and-defer .old removal. Docstring merges both reader caveats: dir = readers closed first; file = sub-ms no-dest window compensated by connect.py:56-73. Keep rollback-on-OSError in both (paths.py:93-97, build.py:219-223); restore + stale-.old sweep shared.
- `mimicwarehouse/src/mimicwarehouse/loader/stage.py` - Migrate: import at :32, new_dir_for at :145 -> publish.new_path_for, swap_dir at :180; docstring cite :10. Stale-.new rmtree at :146-147 stays caller-side.
- `mimicwarehouse/src/mimicwarehouse/loader/buckets.py` - Migrate: import :53, new_dir_for :452, swap_dir :526; docstring :7-8. Keep the progress write at :524-525 and stale-.new rmtree at :453-454 caller-side: resume depends on _progress.json travelling inside .new and pass-1-only clearing.
- `mimicwarehouse/src/mimicwarehouse/catalog/build.py` - Delete _retry_os (:179-189), swap_catalog (:192-228), catalog_new_path/catalog_old_path (:92-99), suffix/retry constants (:74-79). :285 -> publish.new_path_for; :318 -> publish.swap_file with the existing close-the-app hint, translating SwapBlockedError to CatalogSwapError (subclass of CatalogBuildError + SwapBlockedError) so test_ep21.py:231 survives. Update docstring :8-13 and __all__.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - build_runs_db: replace import :736 and inline .new suffix :745 with publish.new_path_for; :757 -> publish.swap_file with a runs-specific blocked_hint naming `mwh runs refresh` (the current tier="runs" plumbing produces a wrong `mwh build --tier runs` remedy - deliberate message fix, flag to owner). Docstrings :48 and :730-733.
- `mimicwarehouse/src/mimicwarehouse/runs_cli.py` - Catch publish.SwapBlockedError instead of CatalogSwapError (:46, :52) - build_runs_db no longer routes through catalog.build's translation.
- `mimicwarehouse/src/mimicwarehouse/paths.py` - DELETE after migration (fully absorbed into publish.py).
- `mimicwarehouse/src/mimicwarehouse/loader/paths.py` - Docstring :9-10: point the 'Distinct from mimicwarehouse.paths' sentence at mimicwarehouse.publish.
- `mimicwarehouse/src/mimicwarehouse/catalog/__init__.py` - Docstring :7 cites the publish module. No module-level import (test_ep09 import budget).
- `mimicwarehouse/src/mimicwarehouse/canary.py` - No functional change: the inline sequence :409-434 deliberately rehearses raw os calls against AV with its own note() events; add a comment cross-ref to publish.py. Owner option at checkpoint: route it through swap_file(observer=note) so the canary exercises the real code path.
- `mimicwarehouse/DESIGN.md` - Add ONE dated EP-33 note under section 6, cross-referenced from section 5 (never rewrite old dated notes): both protocols now live in publish.py over one retry/crash-recovery core with an observer seam - supersedes module citations at :292, :326, :385-404. Edit the living module map row :634 (paths.py -> publish.py). Optional cite in section 12 :593-594.
- `mimicwarehouse/tests/ep/test_ep17.py` - Update imports and the swap section :337-370 (swap_dir/SwapError/new_dir_for/old_dir_for move to publish; also :2, :224, :313). Assertions unchanged: first publish, restage over existing dest, crash recovery, SwapError on missing new and new==dest.
- `mimicwarehouse/tests/ep/test_ep21.py` - Imports :33-35: catalog_new_path -> publish.new_path_for (:108, :242); CatalogSwapError import stays valid via subclass redefinition.
- `mimicwarehouse/tests/ep/test_ep18.py` - Leftover asserts paths.new_dir_for/old_dir_for -> publish: test_ep18.py:140, test_ep23.py:144, test_ep24.py:195, test_ep25.py:265, test_ep26.py:156 + :254, test_ep27.py:253, test_ep28.py:111-112 (mechanical; each file's loader_paths import is the untouched reader-glob module).
- `mimicwarehouse/tests/ep/test_ep33.py` - New: observer event order both variants; file fast-fail with open non-sharing handle (hint surfaced, live file intact); dir crash recovery; FileNotFoundError-swallow scope; build_runs_db end-to-end on fixture settings.

**Tests:**

- uv run poe test -m "ep_17 or ep_18 or ep_21 or ep_23 or ep_24 or ep_25 or ep_26 or ep_27 or ep_28 or ep_30 or ep_32 or ep_33" (cwd mimicwarehouse) - both swap variants, loader determinism/resume, catalog build, runs refresh
- uv run mwh verify for EP-17, EP-18, EP-21, EP-30 stays green (EP-33 hard invariant)
- Loader determinism: sha256-stable-across-two-runs tests (test_ep17/test_ep18) guard that the unified swap stays pure-rename and never rewrites or filters directory contents (manifest sha256 is computed post-swap at stage.py:187)
- test_ep21.py:224-233 must still fail FAST (no retry on the file aside rename) and match 'close the app/notebooks and rerun'
- Resume: test_ep26/27/28 progress assertions - _progress.json published by the swap (buckets.py:524-526), sorted_buckets honored, no .new/.old leftovers
- mwh runs refresh happy path + blocked path wording (runs_cli exit 2)
- ruff format publish.py and touched files before commit (poe check lints but does not format-check)

**Risks / preserve-semantics notes:**

- Aside-rename retry is per-kind: dirs retry ~10s for transient AV holds; files must fail fast (a non-sharing handle is not transient). One shared policy either breaks test_ep21 timing or hangs catalog swaps behind AV.
- paths.py:92 swallows FileNotFoundError on the publish rename itself (via _retry); the file variant does not. Recommend restricting the swallow to restore/cleanup ops - small behavior change (stage raises at the swap instead of later at manifest sha256). Owner yes/no at checkpoint.
- Final .old handling diverges by design: dirs raise after retry exhaustion (no-leftovers invariant), files warn-and-defer (delete-pending FILE_SHARE_DELETE readers; build.py:224-228). Keep per-variant; do not unify.
- build_runs_db blocked message changes from the misleading 'mwh build --tier runs --select catalog' remedy to 'mwh runs refresh' - improvement but user-visible; runs_cli prints exc verbatim.
- Deferred-.old warning moves from the mimicwarehouse.catalog.build logger to mimicwarehouse.publish - log-grep by logger name changes.
- Many rename-aside grep hits are shipped roadmap briefs (EP-17/18/21/35/166/170/171, retro findings) - historical records, must NOT be edited; only the DESIGN living map row and a new dated note change.
- Malwarebytes heuristic (D-42): publish.py performs the same rename/delete shapes the EP-171 canary baselined; add no burst loops. Mid-EP 'access denied / vanished' -> check Quarantine first.
- Swap must never gain content validation/filtering of the .new tree: buckets publishes _progress.json (and any heartbeat leftovers) inside it; readers are protected by the part-*.parquet glob pin, not by the swap.

**Proposed public-surface renames (checkpoint approval required):**

- `mimicwarehouse.paths (src/mimicwarehouse/paths.py)` -> `mimicwarehouse.publish (src/mimicwarehouse/publish.py)` - cited by: mimicwarehouse/src/mimicwarehouse/loader/stage.py, mimicwarehouse/src/mimicwarehouse/loader/buckets.py, mimicwarehouse/src/mimicwarehouse/loader/paths.py, mimicwarehouse/src/mimicwarehouse/catalog/build.py, mimicwarehouse/DESIGN.md, mimicwarehouse/tests/ep/test_ep17.py, mimicwarehouse/tests/ep/test_ep18.py, mimicwarehouse/tests/ep/test_ep23.py, mimicwarehouse/tests/ep/test_ep24.py, mimicwarehouse/tests/ep/test_ep25.py, mimicwarehouse/tests/ep/test_ep26.py, mimicwarehouse/tests/ep/test_ep27.py, mimicwarehouse/tests/ep/test_ep28.py, roadmap/EP-17-stage-loader-core.md (historical, no edit), roadmap/EP-18-stage-loader-buckets.md (historical, no edit), roadmap/EP-166-retro-docs-consolidation.md (historical, no edit)
- `paths.swap_dir` -> `publish.swap_dir` - cited by: mimicwarehouse/src/mimicwarehouse/loader/stage.py, mimicwarehouse/src/mimicwarehouse/loader/buckets.py, mimicwarehouse/DESIGN.md, mimicwarehouse/tests/ep/test_ep17.py, mimicwarehouse/src/mimicwarehouse/catalog/build.py
- `paths.new_dir_for / paths.old_dir_for` -> `publish.new_path_for / publish.old_path_for` - cited by: mimicwarehouse/src/mimicwarehouse/loader/stage.py, mimicwarehouse/src/mimicwarehouse/loader/buckets.py, mimicwarehouse/tests/ep/test_ep17.py, mimicwarehouse/tests/ep/test_ep18.py, mimicwarehouse/tests/ep/test_ep23.py, mimicwarehouse/tests/ep/test_ep24.py, mimicwarehouse/tests/ep/test_ep25.py, mimicwarehouse/tests/ep/test_ep26.py, mimicwarehouse/tests/ep/test_ep27.py, mimicwarehouse/tests/ep/test_ep28.py
- `catalog.build.swap_catalog(new, dest, tier)` -> `publish.swap_file(new, dest, *, blocked_hint, observer=None)` - cited by: mimicwarehouse/src/mimicwarehouse/safe.py, mimicwarehouse/src/mimicwarehouse/catalog/build.py, mimicwarehouse/src/mimicwarehouse/catalog/__init__.py, mimicwarehouse/DESIGN.md
- `catalog.build.catalog_new_path / catalog_old_path` -> `publish.new_path_for / publish.old_path_for` - cited by: mimicwarehouse/src/mimicwarehouse/catalog/build.py, mimicwarehouse/tests/ep/test_ep21.py
- `paths.SwapError` -> `publish.SwapError (moved; name kept)` - cited by: mimicwarehouse/tests/ep/test_ep17.py
- `catalog.build.CatalogSwapError (standalone)` -> `CatalogSwapError subclassing publish.SwapBlockedError (stays in catalog.build)` - cited by: mimicwarehouse/src/mimicwarehouse/runs_cli.py, mimicwarehouse/tests/ep/test_ep21.py, mimicwarehouse/src/mimicwarehouse/catalog/build.py
- `duplicated NEW_SUFFIX/OLD_SUFFIX/RETRIES/RETRY_BASE_SLEEP_S (paths.py + build.py)` -> `single definitions in publish` - cited by: mimicwarehouse/src/mimicwarehouse/catalog/build.py, mimicwarehouse/src/mimicwarehouse/paths.py, mimicwarehouse/src/mimicwarehouse/inventory.py

## B3 - engine/connection canon (per-profile factories, centralized ATTACH, call-site audit)

B3 scouting done. Eight raw duckdb.connect sites in src (inventory.py:400; safe.py:400,748; catalog/connect.py:64; catalog/build.py:244,311; fixtures/catalog.py:82; plus the ATTACH at safe.py:665) and 15 in tests; scripts/ has none. Plan: new canon module engine.py exposing open_duckdb(profile 'build'|'app', database, read_only, settings, memory_limit, retry_missing_s) and attach_read_only(con, path, alias); open_build_connection and open_catalog stay as guard/hardening layers delegating their raw connect; inventory.open_connection becomes a thin alias. Two nonconforming src sites (catalog/build.py:244, safe.py:400 — no profile config). DESIGN section 6 gains Engine gotchas (**-binds-DOUBLE; path-keyed instance cache + ATTACH lifetime); a grep-guard test enforces the canon.

**Changes** (file - action):

- `mimicwarehouse/src/mimicwarehouse/engine.py` - NEW canon module. API: open_duckdb(profile: Literal['build','app'], *, database: str|Path|None=None (None=':memory:'), read_only=False, settings=None, memory_limit: str|None=None, retry_missing_s: float=0.0). Body: lazy import duckdb; mkdir layout['tmp_duckdb'] (CFG-3); config=duckdb_settings(profile); SET memory_limit override (quote-escaped, per loader/engine.py:83-84).
- `mimicwarehouse/src/mimicwarehouse/engine.py` - Also: attach_read_only(con, path, alias) centralizing safe.py:664-665 (posix path, '' escaping, ATTACH IF NOT EXISTS ... (READ_ONLY)) with the instance-cache comment moved here once; retry_missing_s>0 reproduces catalog/connect.py:61-73's retry loop. Docstring carries ARCH-11 (one build-profile connection per machine). No top-level duckdb import (test_ep09 import budget).
- `mimicwarehouse/src/mimicwarehouse/loader/engine.py` - open_build_connection (61-85) keeps guard order (pin, free-space) but delegates the connect + memory_limit override (75-85) to engine.open_duckdb('build', ...). Optionally move DuckDBVersionError/pinned_duckdb_version/require_pinned_duckdb into the canon module with re-exports here (loader/__init__.py:15 and catalog/build.py:271 import them) — a move, not a rename.
- `mimicwarehouse/src/mimicwarehouse/inventory.py` - open_connection (387-400) becomes a thin alias over engine.open_duckdb('build') (FC-7/INV-15 direction: keep the name, one implementation). Close the INV-15 leak at line 493 (self-opened connection never closed) with contextlib.closing. Callers at line 870 and canary.py:317 stay on the alias or migrate.
- `mimicwarehouse/src/mimicwarehouse/catalog/connect.py` - _connect_with_retry (56-73) collapses onto engine.open_duckdb('app', database=target, read_only=True, retry_missing_s=OPEN_RETRY_S); the no-catalog CatalogOpenError message (68-71) stays raised at this layer verbatim. open_catalog keeps hardening SQL, meta checks, .new refusal; drop local tmp-mkdir at 102 (factory owns CFG-3).
- `mimicwarehouse/src/mimicwarehouse/catalog/build.py` - Two bypasses. Line 244 (_recorded_dev_buckets): bare duckdb.connect(read_only=True) with NO profile config and no tmp-mkdir — migrate to open_duckdb('app', read_only=True) preserving the duckdb.Error -> None tolerance at 245-250. Line 311 (build_catalog writer): -> open_duckdb('build', database=new); drop duplicate tmp-mkdir at 287.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - Three sites. Line 400 (_describe_ast): bare parser connection — migrate to open_duckdb('app') or mark as the one documented exemption; recommend migrating. Lines 660-665: replace inline ATTACH with engine.attach_read_only(con, runs_db_path(settings), 'runs'). Line 748 (build_runs_db): -> open_duckdb('app', database=new); dedupe tmp-mkdir at 744.
- `mimicwarehouse/src/mimicwarehouse/fixtures/catalog.py` - Lines 78-82 (build_fixture_catalog): duckdb.connect(':memory:', config=app settings) plus its own CFG-3 mkdir -> open_duckdb('app'); comment at 78-79 moves to the factory. Public name build_fixture_catalog unchanged (lazy re-export in fixtures/__init__.py, cited by fixtures/write.py:268).
- `mimicwarehouse/DESIGN.md` - Add an 'Engine gotchas' subsection under section 6 (C4's home). Gotcha 1 text: 'DuckDB 1.5.5 evaluates the ** power operator as floating point, so an integer spelled 10**9 reaches functions like range() as a DOUBLE and fails or binds wrongly for values above INT32 — always spell large integer literals out in full (EP-30 completion note; the tests spell the timeout probe range(1000000000)).'
- `mimicwarehouse/DESIGN.md` - Gotcha 2 text: 'The in-process instance cache is keyed on database path: reconnecting while any in-process connection holds the path returns the OLD instance (stale catalog after the rename-aside swap — section 6 note b), and an ATTACH on one connection is instance-wide and outlives it — so every attach is ATTACH IF NOT EXISTS via engine.attach_read_only; never assume a fresh instance.'
- `mimicwarehouse/tests/ep/test_ep33.py` - Regression guard: read every .py under src/mimicwarehouse (excluding concepts/vendor/) and assert 'duckdb.connect(' appears only in the canon module, and executed 'ATTACH ' SQL only there (safe.py:9/29 mentions are prose — match execute-call lines or allow-list). Marker ep_33; runs on fixture.
- `mimicwarehouse/tests/ep/ (audit of existing bypasses)` - Deliberate, keep with a comment: test_ep01.py:94 (version-pin probe), test_ep21.py:210 (writes a crafted invalid catalog on purpose). Hand-built config dicts that could use the factory: test_ep09.py:324, test_ep11.py:160, test_ep169.py:108 — migrate only if not deliberately unit-testing duckdb_settings() shape.
- `mimicwarehouse/tests/ep/ (audit, continued)` - Bare in-memory helpers, exempt from the src-only guard: test_ep22.py:422, test_ep23.py:249, test_ep24.py:341, test_ep25.py:415, test_ep26.py:338, test_ep27.py:418, test_ep29.py:94. Raw read-only opens deliberately skipping open_catalog's checks: test_ep12.py:888/:903, test_ep30.py:253. scripts/ has no DuckDB sites; concepts/vendor duckdb.sql hits are upstream vendor files.

**Tests:**

- New tests/ep/test_ep33.py grep-guard (src-only duckdb.connect/ATTACH allow-list) green on fixture.
- Re-run touched markers on fixture: ep_10/ep_17 (opener), ep_21 (open_catalog error paths — CatalogOpenError text is asserted), ep_30 (safe_query, runs ATTACH, build_runs_db), ep_31 (tracer), ep_167/ep_169 (config/CFG-3); staging markers ep_22..ep_29 unchanged but cheap to include.
- test_ep09 import-budget test still green (canon module must not import duckdb at module level; dag/__init__ and catalog/__init__ stay import-free).
- uv run mwh verify EP-33 per the brief; `uv run poe test -m ep_33` from cwd=mimicwarehouse.
- Behavioral check for the factory retry: open_duckdb(retry_missing_s>0) must surface the caller-shaped error (CatalogOpenError from catalog/connect.py, not a factory error) when the file never appears.

**Risks / preserve-semantics notes:**

- Error-text compatibility: open_catalog's no-catalog CatalogOpenError message (catalog/connect.py:68-71) and the .new refusal are user-facing and likely test-asserted; the retry move into the factory must keep the raise at the open_catalog layer.
- _recorded_dev_buckets (catalog/build.py:236-256) intentionally swallows duckdb.Error to None; a factory that wraps or re-raises differently would turn a benign locked/absent catalog into a build failure.
- Applying the app profile to safe.py:400's parser connection adds a tmp-dir mkdir to a previously filesystem-free path; harmless under test tmp roots, but note it in the commit message.
- ARCH-11 (one build-profile 36GB connection per machine) must not regress: make profile a required positional so ad-hoc callers choose consciously; never default to 'build'.
- Module naming: a top-level engine.py coexists with loader/engine.py; prose must use full dotted paths, or pick an unambiguous name (e.g. duck.py) at the owner checkpoint.
- Do not edit historical roadmap/retro files for the rename; only DESIGN.md, docstrings, and live code cite the canon.
- build_runs_db writes with the 'app' profile today (safe.py:748) while build_catalog writes with 'build' — keep the per-site profile choice explicit; unifying under 'build' would grab 36GB for a trivial view store.
- loader/engine.py's guards (pin, free-space) must stay outside the factory: fixtures/catalog and safe._describe_ast must keep working with no free-space requirement.

**Proposed public-surface renames (checkpoint approval required):**

- `mimicwarehouse.inventory.open_connection (as the canon opener)` -> `mimicwarehouse.engine.open_duckdb (inventory keeps open_connection as a thin delegating alias — no import breaks)` - cited by: mimicwarehouse/DESIGN.md (line 838, EP-167 note — update), mimicwarehouse/src/mimicwarehouse/loader/engine.py (docstring lines 3-13 + import line 75), mimicwarehouse/src/mimicwarehouse/canary.py (lines 62, 317), mimicwarehouse/src/mimicwarehouse/inventory.py (docstring 387-394, __all__ 1445), roadmap/EP-17-stage-loader-core.md (historical — do not edit), roadmap/EP-167-retro-cli-config-consolidation.md (historical — do not edit), roadmap/retro-2026-08-18-findings.md (FC-7/INV-15 — historical, do not edit)

## B4 - committed-text hygiene canon

B4 plan: one new canon page mimicwarehouse/docs/committed-text.md states all six committed-text rules with enforcing anchors (fmt_int inventory.py:259; console_safe console.py:34; guard.py:155 vs 158 - why run ids are legal in content but refused in file names; run-folder rules tracer.py:41-42/93; pragma policy guard.py:159-160 + vendoring.py:73; DOI-not-PMID reading.md:12-15), plus pointer lines from tests/README.md, docs/analyses/README.md, DESIGN.md, roadmap/README.md:27. Dedupe kept small: fold dictionary._fmt_mb into a None-widened inventory.fmt_bytes_mb; fmt_int stays in inventory.py; tracer.VALUE_MAX_CHARS keeps mirroring safe.FREE_TEXT_MAX_CHARS (import budget) with an equality test. New assertions go in tests/ep/test_ep33.py (churn rule). Guard coverage untouched.

**Changes** (file - action):

- `mimicwarehouse/docs/committed-text.md` - CREATE the one canon page (policy only, no data-derived numbers): six rules, each with enforcing anchor + test. Splits detail across the other change rows; key anchors: inventory.py:259 fmt_int; console.py:34 console_safe; guard.py:155 ID_TOKEN vs guard.py:158 PATH_ID_TOKEN (content-vs-filename asymmetry); tracer.py:41-42,93 run-folder rules; guard.py:159-160 pragma; reading.md:12-15 DOI rule.
- `mimicwarehouse/src/mimicwarehouse/inventory.py` - Widen fmt_bytes_mb (lines 265-266) to int | None returning '-' (fmt_int's None convention) so it replaces catalog/dictionary._fmt_mb; add a canon pointer to the fmt_int docstring (lines 259-262). No output change for existing int callers (e.g. line 914).
- `mimicwarehouse/src/mimicwarehouse/catalog/dictionary.py` - Delete private duplicate _fmt_mb (lines 85-86); import fmt_bytes_mb from inventory (fmt_int already imported at line 29) and use at line 150. Byte-identical output (same 1e6 divisor, '-' for None).
- `mimicwarehouse/src/mimicwarehouse/tracer.py` - Comment-only at lines 92-93: record that VALUE_MAX_CHARS deliberately mirrors safe.FREE_TEXT_MAX_CHARS (safe.py:165) instead of importing it - safe.py is off the mwh startup import path, tracer.py is on it (test_ep09 budget); equality asserted in test_ep33. Add canon pointer.
- `mimicwarehouse/src/mimicwarehouse/guard.py` - Docstring-only: pointer to docs/committed-text.md in the G4 paragraph (lines 19-30). No change to rules, regexes, pragma handling, or messages (invariant: guard coverage unchanged).
- `mimicwarehouse/src/mimicwarehouse/console.py` - Docstring-only: name docs/committed-text.md as the ASCII/console_safe rule's home. Keep the verify._console_safe alias (verify.py:685-686) - test_ep167.py:131 asserts alias identity and the churn rule forbids editing that test.
- `mimicwarehouse/tests/README.md` - One pointer line near the churn rule (lines 8-11): committed-text hygiene rules and their asserting tests are indexed in docs/committed-text.md.
- `mimicwarehouse/docs/analyses/README.md` - One pointer line in File naming (lines 14-17): NN-slug and the no-run-ids-in-names rule are instances of the canon; link docs/committed-text.md.
- `mimicwarehouse/DESIGN.md` - Dated note under section 15: hygiene canon consolidated at docs/committed-text.md. C1's rewrite folds it later; C4's gotchas home links the page rather than duplicating it.
- `roadmap/README.md` - Append a docs/committed-text.md pointer to the integers-notation row (line 27); row content otherwise unchanged (C5 owns the full audit).
- `mimicwarehouse/tests/ep/test_ep33.py` - B4's share of the EP-33 acceptance module (marker ep_33; NEW file - the EP-168 churn rule forbids extending earlier test_ep files). Assertions per the tests list.

**Tests:**

- test_ep33.py: tracer.VALUE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS (imports inside the test body; pins the deliberate constant mirror).
- test_ep33.py: fmt_bytes_mb(None) == '-' plus unchanged int rendering; fmt_int(None) == '-' (locks the shared None convention after the _fmt_mb fold).
- test_ep33.py: filename asymmetry - guard.path_id_hits on a runtime-built run-id-shaped name (compact date + 'T' + time + tier, digits assembled at runtime as in test_ep04.py:44-48) hits; guard.id_band_hits on the same token inline in text does not.
- test_ep33.py: tracked-name sweep - guard.path_id_hits over every git ls-files path returns no hits (no committed name carries a compact date or band id); paths only, no content read.
- test_ep33.py: pragma sweep - every tracked src/docs line containing guard.ALLOW_PRAGMA is vendored with the '(row count, not an id)' rationale (vendoring.GUARD_PRAGMA) or carries a parenthesized rationale; failure messages name paths only.
- test_ep33.py: DOI-not-PMID - no docs/resources/*.md line matches PMID-followed-by-digits; reading.md keeps its citation-hygiene sentence (reading.md:14).
- test_ep33.py: docs/committed-text.md exists, is ASCII, and mentions each enforcing symbol (fmt_int, console_safe, ALLOW_PRAGMA, PATH_ID_TOKEN, VALUE_MAX_CHARS, DOI).
- test_ep33.py: dag.benchmarks markdown render over a synthetic ledger is ASCII (the one committed-note renderer not yet ASCII-asserted).
- Existing coverage inventoried on the canon page, not edited: test_ep04 (pragma + compact date), test_ep09 (ASCII YAML), test_ep13/14/15 (docs dates, DOIs), test_ep30:278 (footer fmt_int), test_ep31:100-230 (run-folder sweeps), test_ep32:118 (fmt_int rows).
- Gate: uv run poe test -m ep_33 plus the full fixture suite green; no earlier test_ep file edited, so all prior verifies stay green.

**Risks / preserve-semantics notes:**

- C4 may choose docs/ for its gotchas home; docs/committed-text.md must be the single hygiene page C4 links, or land as that page's first section if C4's placement is decided first - settle once at the owner checkpoint.
- Import budget (test_ep09): tracer.py loads at mwh startup, safe.py does not; a module-level import of safe into tracer to share the 64-char constant would grow startup. Plan keeps mirrored constants plus an equality test.
- fmt_int is cited as 'inventory.fmt_int' across DESIGN.md, DECISIONS.md, roadmap/README.md and five briefs; relocating it would force history-file edits (forbidden) or a permanent alias with no dedupe gain - it stays in inventory.py.
- The pragma-sweep test must never echo matched line content (a quoted line could carry a band token into a transcript); assert on file paths and boolean rationale-presence only.
- MB-unit inconsistency out of B4 scope: demo.py:350,399 and config.py:864 use 2**20 while fmt_bytes_mb uses 1e6; changing rendered output breaks the no-behavior-change posture - hand to B8 as a note.
- docs/committed-text.md must stay policy-only (pre-EP-43 disclosure rule for docs/); illustrative tokens written as shapes ('1xxxxxxx'), never literal band integers or compact dates.
- tests/fixtures CSVs are Read-denied in sessions; no planned test reads them (name sweeps use git ls-files paths only).
- safe.py:238 and test_ep31.py:57-61 duplicate one identifier-set comprehension; a shared schema/contract helper would need a test_ep31 edit (churn rule) for marginal gain - canon documents the blessed expression instead; helper deferred.

**Proposed public-surface renames (checkpoint approval required):**

- `catalog.dictionary._fmt_mb` -> `inventory.fmt_bytes_mb (widened to int | None)` - cited by: mimicwarehouse/src/mimicwarehouse/catalog/dictionary.py

## B5 - dev-loop gates (format gate, poe from repo root, test docs)

EP-33 B5 plan. (1) Add `fmt-check = "ruff format --check ."` and set `check = ["lint", "fmt-check", "typecheck", "test"]`. Pass status is not determinable read-only, but expected diff is zero: the ruff-format pre-commit hook (since EP-4) has checked every staged .py; implementer runs it once, `poe fmt` in a separate commit if nonzero. Vendor tree is SQL-only. (2) New root `poe_tasks.toml`: `include = [{ path = "mimicwarehouse/pyproject.toml", cwd = "mimicwarehouse" }]` — supported by locked poethepoet 0.48.0; preferred over a root pyproject (uv discovery) or a `-C` alias. (3) test-fast vs test docs already shipped by EP-168; only chain text and a root-invocation line change. Blocker: tests/ep/test_ep12.py:864 pins the exact chain; relax under the churn rule.

**Changes** (file - action):

- `mimicwarehouse/pyproject.toml` - In [tool.poe.tasks]: after line 115 (`fmt`) add `fmt-check = "ruff format --check ."` with a comment citing EP-33 B5 / VT-15; change line 120 to `check = ["lint", "fmt-check", "typecheck", "test"]` (fmt-check second: fast fail before pyright).
- `poe_tasks.toml` - New file at repo root: header comment (repo-root poe entry point, EP-33 B5; example `uv run --project mimicwarehouse --group dev poe check`) plus `include = [{ path = "mimicwarehouse/pyproject.toml", cwd = "mimicwarehouse" }]`. Exposes every workspace task from the root with cwd=mimicwarehouse. The existing check-toml pre-commit hook validates it; no .pre-commit-config.yaml change.
- `mimicwarehouse/tests/ep/test_ep12.py` - Line 864: relax the exact-chain pin to the protected properties: `{"lint", "typecheck", "test"} <= set(tasks["check"])` and no `test-dev`/`test-full` in the chain (fixture-only). EP-168 churn-rule fix (exact pin is the coupling bug); flag at the owner checkpoint since it edits an earlier EP's test.
- `mimicwarehouse/tests/README.md` - Line 30: chain text becomes '(`lint` + `fmt-check` + `typecheck` + `test`)'. In the Running block add one line for the repo-root form (`uv run --project mimicwarehouse --group dev poe check`). test-fast/serial docs and D-42 rationale already present at lines 16-17/30-32; optionally name pytest-xdist explicitly at line 17.
- `mimicwarehouse/README.md` - Line 94: comment '# ruff check + pyright + pytest' becomes '# ruff check + ruff format --check + pyright + pytest'. Lines 102-103: add `fmt-check` to the task list and a clause that poe tasks also run from the repo root via poe_tasks.toml. Leave line 34 ('Gates as of EP-16') untouched — dated snapshot, refreshed by EP-33 workstream A/C.
- `mimicwarehouse/DESIGN.md` - Append a dated note (2026-08-31, EP-33 B5): format gate in poe check + repo-root poe_tasks.toml include-with-cwd entry point. Do NOT edit the historical EP-6 note at DESIGN.md:779 describing the old chain.
- `mimicwarehouse/DECISIONS.md` - No edit: the uncommitted D-44 text at DECISIONS.md:974 already says 'poe check (now with a format gate)' — B5 lands what it anticipates.
- `CLAUDE.md` - Optional, owner-gated (present at checkpoint, do not pre-edit): one clause in section 3 noting poe tasks run from the repo root via poe_tasks.toml, superseding the cwd=mimicwarehouse requirement.

**Tests:**

- Implementer, once, before edits: `ruff format --check .` from cwd=mimicwarehouse to enumerate drifted files; expected zero (ruff-format pre-commit hook has gated every commit since EP-4). If nonzero: `poe fmt`, isolate the reformat in its own commit, re-run the full suite.
- `uv run poe check` green from cwd=mimicwarehouse (new 4-step chain).
- From the repo root: `uv run --project mimicwarehouse --group dev poe check` and an arg-passthrough case, e.g. `... poe test -m ep_12` (poe 0.48 cmd tasks append extra args).
- `uv run poe test -m ep_12` and `-m ep_168` green after relaxing test_ep12.py:864 (test_ep168.py:334-346 pins only `test`/`test-fast` values and README needles — unaffected).
- `uv run mwh verify EP-12` and `EP-168` green (EP-33 invariant: all prior verifies stay green).
- `uv run --group dev pre-commit run --all-files` green (check-toml validates root poe_tasks.toml; ruff-format hook agrees with the new poe gate).
- Candidate EP-33 regression assertion (workstream F): tasks["check"] contains "fmt-check"; root poe_tasks.toml exists with the include entry.

**Risks / preserve-semantics notes:**

- tests/ep/test_ep12.py:864 asserts the exact check chain; adding fmt-check without relaxing it fails the suite, and editing an earlier EP's test needs the churn-rule justification recorded at the owner checkpoint.
- Repo-root include semantics verified against installed poethepoet 0.48.0 (config/config.py:35; config/partition.py:72-84,193-198); a future upgrade could change include/cwd behavior — locked in uv.lock, so deferred to a lock bump.
- A root pyproject.toml alternative would enter uv's upward project discovery and can break bare `uv run` from the repo root; poe_tasks.toml is invisible to uv — the reason it is the recommendation.
- `ruff format .` also covers scripts/ and any future stray .py; the vendored mimic-code tree is currently SQL-only, but a future .py under concepts/vendor/ would need a [tool.ruff] exclude first or formatting breaks VENDOR.json sha256_lf byte identity.
- If the one-time format run touches fixture-generator modules, fixture bytes do not change (formatting only), but the reformat commit should still be isolated for reviewability.
- Auto-memory notes ('poe check lints but doesn't format-check'; 'poe needs cwd=mimicwarehouse') become stale after B5 — orchestrator refreshes memory, not repo files.
- No double uv nesting: root tasks are the included originals, not uv-run wrappers, so no second uv process per invocation.

## B6 - import-budget doctrine (reusable test pattern)

B6 scouted. Mechanism: tests/ep/test_ep09.py:861 test_cli_import_budget runs a fresh interpreter, imports mimicwarehouse.cli, forbids duckdb/pandas/polars/pyarrow in sys.modules and asserts mimicwarehouse.schema.contract stayed unimported. Four near-duplicates: test_ep02.py:142, test_ep06.py:738 (identical), test_ep11.py:826, test_ep12.py:948 (these add numpy). Lazy patterns: dag/__init__.py and catalog/__init__.py are import-free (docstring only); schema/__init__.py:67 and fixtures/__init__.py:103 use __all__ + TYPE_CHECKING block + module __getattr__ + __dir__. Plan: dated doctrine note in DESIGN.md section 15; helper assert_import_budget in tests/helpers.py (EP-168 module; fresh_interpreter, line 57, is the base); ep02+ep09 become one-liners; ep06/ep11/ep12 deleted. No src renames.

**Changes** (file - action):

- `mimicwarehouse/tests/helpers.py` - After fresh_interpreter (ends line 71) add HEAVY_MODULES = ('duckdb','pandas','polars','pyarrow','numpy') and assert_import_budget(module='mimicwarehouse.cli', *, forbid=HEAVY_MODULES, lazy=(), timeout=120) -> None: build a python -c probe importing `module` and printing JSON of forbid/lazy names found in sys.modules; run via fresh_interpreter; assert rc 0 and both offender lists empty.
- `mimicwarehouse/tests/ep/test_ep09.py` - Rewrite test_cli_import_budget (lines 861-877) to helpers.assert_import_budget(lazy=('mimicwarehouse.schema.contract',)). Keeps the contract-laziness clause this test uniquely owns; drops the brittle proc.stdout.split() equality (line 876) and the local subprocess import. Add `import helpers` to module imports.
- `mimicwarehouse/tests/ep/test_ep02.py` - Rewrite test_cli_import_does_not_pull_heavy_libraries (lines 142-151) to a one-line helpers.assert_import_budget() call — the canonical CLI-owner budget test (retro at roadmap/retro-2026-08-18-findings.md:2621 proposed ep_2 as owner). Forbid set thereby gains numpy (already proven absent by ep11/ep12 today).
- `mimicwarehouse/tests/ep/test_ep06.py` - Delete test_verify_import_stays_light (lines 738-748): byte-identical probe to ep02's; verify.py is on the cli.py start-up chain (cli.py:59) so the canonical test covers it.
- `mimicwarehouse/tests/ep/test_ep11.py` - Delete test_mwh_help_does_not_import_polars_or_numpy (lines 826-836): duplicate of the canonical budget once numpy joins HEAVY_MODULES.
- `mimicwarehouse/tests/ep/test_ep12.py` - Delete test_mwh_help_still_light (lines 948-958): identical to the ep11 duplicate.
- `mimicwarehouse/DESIGN.md` - Append dated note under section 15 (~line 1179), rules 1-2: (1) start-up set = cli.py eager imports (cli.py:42-59); allowed: stdlib, typer, rich, pydantic, yaml; heavy libs only in function bodies or TYPE_CHECKING. (2) a package with an attached .cli keeps __init__ import-free (dag/catalog) or lazy via __all__ + TYPE_CHECKING + __getattr__ + __dir__ (schema/fixtures); no eager submodule imports.
- `mimicwarehouse/DESIGN.md` - Same note, rules 3-4: (3) every start-up module carries an 'Import budget:' docstring line (convention: demo.py:18, canary.py:37, tracer.py:48, theme.py:19, inventory.py:31, runs_cli.py:14, dag/cli.py:9, catalog/cli.py:18). (4) enforcement = tests/helpers.assert_import_budget, one line in each future package's (stats/, ml/, viz/) EP test; expensive singletons pinned lazy via lazy=.

**Tests:**

- uv run poe test -m "ep_2 or ep_6 or ep_9 or ep_11 or ep_12" green on fixture tier after the rewrite (cwd=mimicwarehouse for poe).
- uv run mwh verify EP-2 / EP-6 / EP-9 / EP-11 / EP-12 all green — EP-33 hard invariant; verify runs the marker, not specific test names, so the deletions are safe for it.
- Full fixture suite (uv run poe test) once, to catch any module transitively importing numpy at start-up now that it is in the canonical forbid set.
- One deliberate red run during development: temporarily add a top-level duckdb import to a start-up module and confirm assert_import_budget fails with a readable offender list (do not commit).

**Risks / preserve-semantics notes:**

- Deleting tests from shipped EP suites (ep06/ep11/ep12) thins historical acceptance evidence; alternative is rewriting all five as helper one-liners. Recommend deletion (retro-2026-08-18-findings.md:2621 proposed collapsing the five) but surface at the D-44 owner triage checkpoint.
- Grep confirms no roadmap or docs file cites any of the five test function names, so nothing dangles after deletion or rewrite.
- Adding numpy to the ep02/ep09 forbid set is a tightening; a future start-up import pulling numpy fails the canonical test rather than only ep11/ep12 — intended, but say so in the DESIGN note.
- helpers.fresh_interpreter defaults cwd=WORKSPACE while ep02's original probe ran without cwd; equivalent because the package is venv-installed — confirm in the red-run check.
- DESIGN.md section 15 edit must be an additive dated note (docs discipline); schema/__init__.py:12 and fixtures/__init__.py:22 already cite 'DESIGN section 15', so no docstring edits needed.
- The probe passes code as a subprocess argv element (python -c), not a heredoc/stdin script — D-42-compliant; keep it that way in the helper.

## B8 - paradigm sweep (one way to do each thing)

B8 scouting complete (read-only). (1) JSONL append: safe.py and dag/benchmarks.py are identical O_APPEND+fsync twins; loader/manifest.py buffered-appends without O_APPEND/fsync (torn-line window); canary emulates the shape deliberately. Fix: one stdlib-only fsio.append_jsonl. (2) Progress: 4 mechanisms (stdlib logging, inventory._Log tee, canary Observer, dag/jobs prints); unify on stdlib logging with two documented exceptions. (3) DIAGNOSTIC_COMMANDS membership matches the rule for all 15 commands; only doctrine sprawl to fix. (4) loader/__init__ is the sole eager re-exporter. (5) Errors: stdout/stderr split, 5 duplicated _fail helpers, prefixless refusals in config.py, tracer hardcodes exit 3. (6) All --json outputs keep raw ints; deviations: 3 emitter styles, guard os.linesep.

**Changes** (file - action):

- `mimicwarehouse/src/mimicwarehouse/fsio.py` - NEW stdlib-only module: append_jsonl(path, payload) -> Path (mkdir parents; json.dumps(payload, sort_keys=True)+"\n" UTF-8; os.open O_WRONLY|O_APPEND|O_CREAT 0o644; single os.write; os.fsync; close) plus a batch variant (one os.write per line, one fsync). Also new home for atomic_write_text (promoted from inventory.py:543, alias kept). Zero heavy imports so safe.py/benchmarks.py stay light.
- `mimicwarehouse/src/mimicwarehouse/safe.py` - _append_audit (lines 260-271) delegates to fsio.append_jsonl; docstring keeps the D-24/GOVERNANCE citation and points at the canon.
- `mimicwarehouse/src/mimicwarehouse/dag/benchmarks.py` - append (lines 87-98) delegates to fsio.append_jsonl; fix docstring drift at lines 4 and 88 which say 'flush' while the code fsyncs.
- `mimicwarehouse/src/mimicwarehouse/loader/manifest.py` - append_manifest (lines 113-124) switches from buffered path.open('a') text append to fsio batch append; behavior change: gains O_APPEND + fsync (closes the torn-line window this resumable ledger currently has; D-42-aligned).
- `mimicwarehouse/src/mimicwarehouse/canary.py` - Manifest-churn pass (lines 387-390) deliberately emulates the ledger shape; either switch it to fsio.append_jsonl so the canary exercises the production write path, or annotate it as the one sanctioned exception in the canon. Recommend switch; note its lines use separators=(',',':') today (helper canonical form differs harmlessly).
- `mimicwarehouse/src/mimicwarehouse/console.py` - Add the single seam: configure_progress_logging(*, to_file=None, quiet=False) on logger 'mimicwarehouse' (absorbs dag/cli.py:45-53); fail(prefix, message, *, code=2) printing '[bold red]{prefix}:[/]' to one agreed stream; emit_json(payload) via sys.stdout.write(json.dumps(..., indent=2, default=str)+'\n'); constants EXIT_OK/FINDINGS/USAGE/REFUSED = 0/1/2/3.
- `mimicwarehouse/src/mimicwarehouse/inventory.py` - Delete _Log class (lines 725-748); build_inventory's log_path/quiet params (lines 757-758, 780) wire console.configure_progress_logging + logging.getLogger('mimicwarehouse.inventory'); progress lines at 863, 893, 908, 914 become _LOG.info. _fail (1169-1171) delegates to console.fail.
- `mimicwarehouse/src/mimicwarehouse/dag/cli.py` - _configure_build_logging (45-53) becomes a call to console.configure_progress_logging; _fail (40-42) delegates to console.fail; exit 2 at line 235 unchanged.
- `mimicwarehouse/src/mimicwarehouse/dag/jobs.py` - No mechanism change (prints at 218-236 are the child wrapper whose stdout IS the job log — document as sanctioned exception in the canon).
- `mimicwarehouse/src/mimicwarehouse/catalog/cli.py` - _fail (60-62) delegates to console.fail; refusal at 361 keeps '{prefix}: refused:' form and imports shared EXIT_REFUSED; JSON writes at 132, 263, 371, 396-398, 422-433 switch to console.emit_json (263's default=str Decimal-to-string caveat documented in the canon).
- `mimicwarehouse/src/mimicwarehouse/tracer.py` - Refusal exit at line 677 imports EXIT_REFUSED instead of hardcoding 3; refusal message at 676 already matches the '{prefix}: refused:' form.
- `mimicwarehouse/src/mimicwarehouse/config.py` - Prefixless '[bold red]refused:[/]' at 897 and 903 and 'unsafe data root:' at 929 gain the 'mwh paths:' command prefix via console.fail (message text preserved after the prefix).
- `mimicwarehouse/src/mimicwarehouse/guard.py` - _emit_json (786-787) replaced by console.emit_json — fixes os.linesep, which yields CR-CR-LF through text-mode stdout on Windows because this explicit \r\n is re-translated by the text layer; every other JSON path writes '\n'. Error prints at 835, 866, 877 route through console.fail.
- `mimicwarehouse/src/mimicwarehouse/fixtures/cli.py` - _fail (35-37) delegates to console.fail; --json path (90-103) switches console.print_json to console.emit_json.
- `mimicwarehouse/src/mimicwarehouse/canary.py` - _fail (497-499) delegates to console.fail; --json path (551-552) switches to console.emit_json.
- `mimicwarehouse/src/mimicwarehouse/schema/cli.py` - --json emitters at 85-93, 127-132, 217-219 switch console.print_json to console.emit_json; err_console error prints (40, 50, 66, 195, 214, 270, 284) route through console.fail keeping the 'mwh schema...' prefixes.
- `mimicwarehouse/src/mimicwarehouse/verify.py` - --json at 787-788 (typer.echo) and script entry at 834-835 (print) switch to console.emit_json; error prints at 774, 797 route through console.fail; EXIT_OK/EXIT_USAGE (60-61) become re-exports of the console constants.
- `mimicwarehouse/src/mimicwarehouse/inventory.py` - --json paths at 1276-1290 and 1377-1389 switch console.print_json to console.emit_json (payloads already raw ints).
- `mimicwarehouse/src/mimicwarehouse/doctor.py` - --json at 957-958 switches sys.stdout.write to console.emit_json (report already raw ints); no other change.
- `mimicwarehouse/src/mimicwarehouse/loader/__init__.py` - Eager re-import block (lines 13-18) becomes the lazy module-__getattr__ pattern (fixtures/__init__.py:78-111 _HOMES form); __all__ (20-35) unchanged so the public surface is preserved (PEP 562 covers 'from mimicwarehouse.loader import X'). No in-repo consumer uses the re-exported names — all callers import submodules.
- `mimicwarehouse/src/mimicwarehouse/concepts/__init__.py` - Leave as-is; canon names it the sanctioned third __init__ form (self-contained leaf implementation, eager pydantic import, never on the mwh --help path). Alternative (bigger, not recommended): move VendorInfo et al. to concepts/pin.py with lazy re-export.
- `mimicwarehouse/src/mimicwarehouse/cli.py` - DIAGNOSTIC_COMMANDS (71-73) is CORRECT vs the rule for all 15 registered commands (179-193): every non-member touches the data root. Doctrinal change only: comment 61-70 becomes the single canonical statement + canon pointer; docstring 26-29's pending EP-16 question resolved by a DECISIONS addendum (keep the allow-list) at the owner checkpoint; retro CFG-5's inversion claim is stale (6 of 15).
- `mimicwarehouse/DESIGN.md` - Dated note: canon location, fsio module, progress-logging seam, error/exit-code/emit_json conventions; DESIGN restatements of DIAGNOSTIC_COMMANDS (771, 894, 1018, 1105) get superseded-by pointers under C1's rewrite rather than edits here.
- `roadmap/EP-33-replan-p2.md or docs/ (per C4 layout)` - One-page canon: (1) fsio.append_jsonl only; (2) progress = stdlib logging via configure_progress_logging (exceptions: canary Observer, jobs prints); (3) DIAGNOSTIC_COMMANDS rule once; (4) __init__ forms: docstring-only | lazy __getattr__ | leaf impl; (5) console.fail, 'mwh <cmd>:' prefixes, exits 0/1/2/3; (6) emit_json, raw ints, fmt_int human-only.

**Tests:**

- Full fixture battery after the sweep: uv run poe test (cwd=mimicwarehouse) — B8 touches modules covered by ep_4, ep_9, ep_10, ep_11, ep_17-28, ep_30, ep_31, ep_32, ep_167, ep_171 markers.
- test_ep09 import budget must stay green after loader/__init__ goes lazy and console.py gains helpers (console is on the mwh --help path — keep configure_progress_logging/fail/emit_json stdlib-only, no rich additions).
- test_ep167.py:281-282 asserts exact DIAGNOSTIC_COMMANDS membership — unchanged by plan; ep_4/ep_6/ep_9/ep_10/ep_11/ep_171 membership asserts likewise unchanged.
- test_ep21.py:38 and test_ep30.py:30 import EXIT_REFUSED from mimicwarehouse.catalog.cli — keep a re-export there.
- New unit tests for fsio.append_jsonl: creates parents, appends one canonical line, concurrent-append lines never torn (two-process append on fixture tier), fsync called (monkeypatch os.fsync).
- Grep tests for assertions on error-output STREAM before standardizing stdout-vs-stderr: typer CliRunner mixes streams by default so result.output asserts survive, but any capsys.err/out split assert (schema/fixtures/inventory/canary CLIs use err_console today) must be swept.
- Canary tests (test_ep171) re-run if the manifest-churn pass switches to fsio; assert PassResult counts unchanged.
- JSON regression: json.loads every --json surface and assert integer fields stay int (guard, doctor, paths, verify --roadmap, schema list/show/check, fixtures build, inventory show/reconcile, canary, catalog info, sql --format json x4) — all pass by inspection today; lock it in.
- guard --json golden: byte-level line endings become plain \n after the os.linesep fix — update any exact-bytes assert.

**Risks / preserve-semantics notes:**

- append_manifest gains O_APPEND+fsync: negligible cost per table append, but it is a semantic hardening of a resume-critical ledger — verify EP-19/EP-23 resume tests on fixture before dev.
- Switching the canary's manifest-churn pass to the shared helper changes the exact write pattern the AV canary measures; timings drift vs the 00-staging-benchmark note. Alternative: keep canary verbatim as the canon's sanctioned exception.
- Standardizing the error stream (stdout vs stderr is split roughly old-vs-new CLIs) changes observable behavior for owner shell scripts and the pre-commit hook consuming guard output; needs the owner checkpoint to pick the stream (recommend stderr) and a test sweep.
- console.print_json -> emit_json drops rich TTY colorization/re-indentation of machine output; harmless for pipes, visible interactively.
- loader/__init__ lazy conversion: dag/runner.py:52 ('from mimicwarehouse.loader import paths as loader_paths') and the many test submodule imports keep working, but any downstream 'from mimicwarehouse.loader import X' relies on PEP 562 __getattr__ — keep __all__ and the TYPE_CHECKING block for IDEs.
- sql --format json uses default=str (catalog/cli.py:263): Decimal aggregates serialize as strings today; canonizing emit_json with default=str preserves that — document the caveat rather than cast, to avoid touching safe-path semantics under invariant 'governance only tightens'.
- Docs discipline forbids rewriting DESIGN/roadmap history that cites inventory._atomic_write_text (DESIGN.md:1097, EP-10/EP-171 briefs, retro findings) — keep the private alias in inventory.py forever; only forward docs reference fsio.
- fmt_int lives in inventory.py and is imported by canary/catalog/tracer/benchmarks — a heavy-module private-ish dependency; moving it to fsio/console is tempting but expands B8 scope; flag for owner triage, default leave.

**Proposed public-surface renames (checkpoint approval required):**

- `mimicwarehouse.inventory._atomic_write_text` -> `mimicwarehouse.fsio.atomic_write_text (alias retained at old name)` - cited by: mimicwarehouse/src/mimicwarehouse/canary.py, mimicwarehouse/src/mimicwarehouse/tracer.py, mimicwarehouse/src/mimicwarehouse/loader/buckets.py, mimicwarehouse/src/mimicwarehouse/loader/manifest.py, mimicwarehouse/src/mimicwarehouse/dag/snapshot.py, mimicwarehouse/src/mimicwarehouse/dag/jobs.py, mimicwarehouse/src/mimicwarehouse/inventory.py, mimicwarehouse/DESIGN.md, roadmap/EP-10-raw-inventory.md, roadmap/EP-171-toolchain-remediation-p2.md, roadmap/retro-2026-08-18-findings.md
- `mimicwarehouse.dag.cli._configure_build_logging` -> `mimicwarehouse.console.configure_progress_logging` - cited by: mimicwarehouse/src/mimicwarehouse/dag/cli.py
- `mimicwarehouse.guard._emit_json` -> `mimicwarehouse.console.emit_json` - cited by: mimicwarehouse/src/mimicwarehouse/guard.py
- `mimicwarehouse.catalog.cli.EXIT_REFUSED (definition site)` -> `mimicwarehouse.console.EXIT_REFUSED (re-exported from catalog.cli for compat)` - cited by: mimicwarehouse/src/mimicwarehouse/catalog/cli.py, mimicwarehouse/src/mimicwarehouse/tracer.py, mimicwarehouse/tests/ep/test_ep21.py, mimicwarehouse/tests/ep/test_ep30.py
- `mimicwarehouse.verify.EXIT_OK / EXIT_USAGE (definition site)` -> `mimicwarehouse.console constants (re-exported from verify for compat)` - cited by: mimicwarehouse/src/mimicwarehouse/verify.py
- `mimicwarehouse.inventory._Log (class, deleted)` -> `logging.getLogger('mimicwarehouse.inventory') + console.configure_progress_logging` - cited by: mimicwarehouse/src/mimicwarehouse/inventory.py
