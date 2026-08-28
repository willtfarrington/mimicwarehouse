# mimicwarehouse — GOVERNANCE

The safety, licensing and disclosure contract for everyone and everything (owner,
collaborators, Claude sessions, scripts) that touches this repository or the data it is
built from. It overrides `DESIGN.md`, every roadmap brief, and convenience. Owner
decisions are cited as **D-n** ([`DECISIONS.md`](DECISIONS.md)).

Read this whole file before running anything against the data. Read
[`../source material/README.md`](../source%20material/README.md) for the datasets, their
licenses and citations.

---

## 1. What the data is and what we agreed to

MIMIC-IV 3.1, MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2 are de-identified but real
patient-level EHR data from Beth Israel Deaconess Medical Center, distributed by
PhysioNet under the **PhysioNet Credentialed Health Data License 1.5.0**, one Data Use
Agreement per dataset, granted to the owner personally. In brief, the owner agreed to:

- not attempt to re-identify any individual or institution;
- not share access with anyone (no redistribution in any form, including git, cloud
  sync, chat tools, LLM prompts, screenshots of rows, or attachments);
- keep the local copy secured;
- report suspected PHI to <PHI-report@physionet.org>, not to anyone else;
- use the data only for lawful scientific research;
- keep human-subjects/HIPAA training current (CITI "Data or Specimens Only Research");
- release code arising from publications back to the community.

Owner-maintained record (fill in / keep current): CITI completion date: 2024-01-29 ·
MIMIC-IV DUA accepted: 2026-08-15 (v3.1) · MIMIC-IV-ED DUA accepted: 2024-02-02 (v2.2) · MIMIC-IV-Note DUA
accepted: 2024-02-02 (v2.2) · credential renewal due: 2027-01-29 (CITI).

## 2. Where data may live (D-29, D-30)

| Location | Allowed content |
|---|---|
| `source material/` (repo dir, gitignored except `*.md`) | the raw CSVs exactly as downloaded/decompressed; never edited |
| `C:\mimicdata\` (`MWH_DATA_ROOT`) | lake, catalogs, derived data, marts, runs, models, notes, external sources, temp |
| repository working tree (tracked) | code, docs, configs, synthetic fixtures (ids ≥ 90 000 000), aggregates that passed disclosure review (§7) |
| G: (Google Drive stream), D: (Cryptomator vault), any synced/virtual/network folder | **nothing** related to this project — not data, not catalogs, not caches, not `.venv` |

BitLocker on C: is required (owner verified on 2026-08-16; `mwh doctor` re-checks and
records the result in every run manifest). The data root is excluded from Windows Defender
real-time scanning at the owner's discretion (D-38); the repository is not.

> **Amended 2026-08-28 (EP-165; D-38 addenda, D-42, D-43 item 5).** Endpoint security is
> **two** real-time products, both on: Windows Defender (excludes `C:\mimicdata`; the
> repository is not Defender-excluded) and **Malwarebytes 5.1 Premium**, whose allow list
> is now **nine** paths — the seven of D-38 (`C:\Program Files\Git`; uv's CPython under
> `%APPDATA%\uv\python`; the unsigned `uv.exe` WinGet package dir; the workspace `.venv`;
> `%USERPROFILE%\.cache\pre-commit`; `C:\mimicdata`; `source material\`) plus, decided
> 2026-08-18, `%LOCALAPPDATA%\uv\cache` and the Claude scratchpad
> `%LOCALAPPDATA%\Temp\claude\`. Two of the nine are **inside the repository directory**
> (`source material\` and the `.venv`), so the sentence above holds for Defender only.
> Both products' usage/threat statistics and sample submission are **off**. The
> data-location exclusions are a **disclosure control**, part of keeping the local copy
> "secured" (§1): a scanner that ships detected objects to a vendor cloud must never have
> reason to open MIMIC files. Neither exclusion list is readable non-elevated — `mwh
> doctor` `antivirus` names the products and required paths on the owner's word (D-42).
> **Relocating the data root:** the `.claude/settings.json` deny rules and both products'
> exclusions hard-code `C:\mimicdata`, so a relocated `MWH_DATA_ROOT` has no prefix
> coverage until the deny rules, both exclusion lists and `.env`/`mwh.toml` are updated —
> update them **before** moving anything (retro GOV-3).

## 3. What may be committed to git (D-40, D-41)

- **Yes**: code, docs, YAML specs, DDL, schema contracts, synthetic fixtures, manifests
  that contain only hashes/counts/schema, aggregate tables/figures/model cards **that
  passed `mwh disclose check` and carry a `.disclosure.json` sidecar**, screenshots taken
  in **demo mode** (ODbL demo data) or of aggregate views that pass the same check.
- **No**: any file under `source material/` other than `*.md`; any `.csv`, `.parquet`,
  `.duckdb`, `.wal`, JSONL ledgers; notebooks with outputs; any table with identifier
  columns (`subject_id`, `hadm_id`, `stay_id`, `note_id`, `emar_id`, …) or note text;
  any count below the small-cell threshold without suppression; screenshots of row-level
  views on dev/full tiers.
- Enforced by `.gitignore`, `.gitattributes` and the `mwh guard` pre-commit hook (EP-4),
  which refuses data-shaped files and files containing ids in the real MIMIC bands
  (10 000 000–19 999 999 for `subject_id`; 20 000 000–29 999 999 for `hadm_id`;
  30 000 000–39 999 999 for `stay_id`).
- The remote is private until v1.0.0; going public requires a **full-history guard
  sweep** (release EP). If row-level data is ever committed: stop, do not push, rewrite
  history (`git filter-repo`), rotate any pushed remote, and record the incident in
  `DECISIONS.md`.

  > **Amended 2026-08-18 (D-41 addendum).** The remote is **public from 2026-08-18**, before
  > v1.0.0, as a governed work-in-progress. The full-history guard sweep was run first
  > (every commit reachable from every ref through `guard.scan_tracked`, plus a secrets/PII
  > regex sweep over every blob in history — both clean; details in the D-41 addendum).
  > From now on **every push is a publication**: the pre-commit `mwh guard` hook, the
  > Claude Code deny rules (§4) and the disclosure gate (§7) are the release gate for each
  > commit, and the "if row-level data is ever committed" rule above applies with the remote
  > already public — stop, do not push; if already pushed, rewrite and force-push with the
  > owner's explicit go, then treat the leak as disclosed and report per §1. The v1.0.0
  > release (EP-163) re-runs the sweep with `mwh guard --history`.

## 4. LLM / Claude Code policy (D-31, D-32, D-39)

PhysioNet's policy prohibits sending credentialed data through non-compliant online
services. Claude Code transmits tool results (file reads, shell output, search hits) to
Anthropic. Therefore, in this repository:

1. **Claude sessions may only receive k-suppressed aggregates** (k = 11), schemas,
   dimension/dictionary tables, counts, statistics, code and docs. Never row-level data,
   never identifiers, never note text.
2. All data access from a session goes through `mimicwarehouse.safe.safe_query`
   (EP-30) or the `mwh sql` CLI built on it — read-only, allow-listed statements,
   row cap, suppression, audit-logged. Never `pandas.read_csv`, never `duckdb` CLI,
   never `head`/`type`/`Get-Content` on data files.
3. Enforcement is layered: this document → `CLAUDE.md` (session rules) → repo-shared
   `.claude/settings.json` deny rules on reading `source material/**` (except `*.md`),
   `C:\mimicdata\**`, `*.csv/*.parquet/*.duckdb`, and the `duckdb` executable → the
   `safe` module itself.
4. The owner may view rows **in the app only**, behind an explicit row-view toggle that
   writes an audit entry; those views are never exported and never appear in tool output.
5. Suspected PHI encountered by anyone → report to PhysioNet; do not paste it anywhere.
6. Owner action: check that the claude.ai "improve the model" / training toggle is off.

> **Amended 2026-08-28 (EP-165; D-39, D-43 items 2–3).** Item 3's chain is now **five**
> layers: this document → `CLAUDE.md` → repo-shared `.claude/settings.json`, which since
> EP-165 carries deny rules **plus** an `env` block (`PYTHONUTF8=1`), an allow list for
> read-only project commands (deny keeps precedence), and a registered **PreToolUse
> command-string hook** (`mimicwarehouse/scripts/claude_pretool_guard.py`, run by the
> allow-listed workspace-venv python) that denies any command/path mentioning
> `mimicdata` / `source material` / `.csv` / `.parquet` / `.duckdb` outside allow-listed
> project launchers (`mwh guard --selfcheck` pins its registration) → `mwh guard` +
> pre-commit (the git-side layer, §3) → `safe_query` (EP-30; until it ships, sessions run
> no queries against the real data at all). Scope note (EP-0 finding, D-39 addendum):
> `Read(**/*.csv)`-style extension rules are **project-relative**; the real data is
> protected by the absolute `//C:/mimicdata/**` and `source material/…` rules, the
> absolute `Read(//C:/**/*.csv|.csv.gz|.parquet|.duckdb)` rules added at EP-165, and the
> hook. Deny rules and hook are blocklists of common forms, not a data-path firewall —
> prose and `safe_query` remain the primary controls.
>
> **Connectors (owner decision 2026-08-18, D-43 item 3).** claude.ai MCP connectors and
> the WebFetch/WebSearch tools are an egress path **beyond** Anthropic that the deny rules
> on data files do not cover; the control is therefore prose plus denied send/write tools.
> Sessions may use connectors for **public-reference lookups only** — PubMed, bioRxiv,
> ICD-10 Codes, Context7, GitHub/DOI URLs via WebFetch (EP-13/EP-15 need them) — and must
> never place fixture rows, aggregates, ids, run records or note text in a request.
> Send/write-capable connector tools (Gmail send/reply/forward/draft/trash, Calendar
> create/update/delete/respond, Hugging Face `hf_fs`/`dynamic_space`) are denied in
> `.claude/settings.json`: they would bypass the D-40 disclosure gate, which only guards
> git. The Google Drive connector stays off (no project content in Google-hosted storage;
> distinct from, but adjacent to, D-29's ban on the G: sync client). The connector roster
> is claude.ai account state, invisible to git — re-check it at every re-plan EP.

## 5. Small-cell rule (D-33)

Any count, denominator or cell in a table, figure, attrition diagram, run record or model
card is a small cell if **n < 11**. In-app (dev/full tiers): show with a warning badge.
On export, commit, report, or return to a Claude session: suppress (with complementary
suppression so totals cannot back out the cell). Implemented once in
`mimicwarehouse.disclose` (EP-43) and used everywhere; briefs never re-implement it.

## 6. Row display, timelines and screenshots

Single-stay timelines and row samples are row-level data. They render only in the app for
the owner (owner role, row-view toggle, audit line, banner), are developed and
screenshotted only against `fixture`/`demo` tiers, and are never exported, printed, or
returned by any CLI. Aggregated/binned timeline views (counts per hour bin) follow the
small-cell rule instead.

## 7. Export & disclosure review (D-40)

Promotion of anything from `C:\mimicdata\runs\` into `reports/`, `docs/`, or git requires
`mwh disclose check <path>` to pass (no identifier columns, no free text, no cell < 11,
no embedded data arrays in HTML/Vega specs beyond aggregates) and writes
`<artifact>.disclosure.json` (checks run, k, hash, reviewer = owner, timestamp). The
Disclosure-review tool (EP-133) is the UI over the same module. Reports label their claim
type (exploratory / confirmatory / predictive / associational / causal) and state that all
MIMIC-IV analyses are retrospective.

## 8. Audit trail

`C:\mimicdata\runs\audit.jsonl` receives one line for every `safe_query`, row-view toggle,
export attempt, protocol freeze/run, and external-source ingestion, with timestamp, actor
(`owner` / `agent`), tier, statement hash, row counts. It is append-only, backed up (EP-52),
never committed, and browsable read-only in the app (EP-134).

## 9. Notes (MIMIC-IV-Note) — separate DUA, highest-risk asset (D-3)

Notes are staged only in P10 (EP-148) into a **segregated** lake and catalog that only the
owner role can attach (`--with-notes`). Note text never enters `safe_query` results, run
records, reports, fixtures, git, tool output, or any LLM prompt (local or remote). Text
analysis runs entirely on this machine (local models only; `MWH_ALLOW_REMOTE=false` is
the default and text modules refuse to run when it is true). Synthetic notes are used for
tests and screenshots.

## 10. Dependency & vocabulary licensing (D-34, D-35)

- Code license: **MIT** (`LICENSE` at the repo root — added 2026-08-18 at the public flip
  rather than at EP-163, D-41 addendum; header not required per file).
- Runtime dependencies in the core groups must be permissive (MIT/BSD/Apache-2.0/PSF/
  ISC/MPL-2.0). GPL-licensed tools (e.g. scikit-survival, GPL-3) may only be used through
  the optional `gpl` dependency group and are named in the brief that uses them.
- mimic-code (MIT) is vendored with attribution in `NOTICE`; concept SQL keeps its
  upstream header.
- Vocabularies carry their own licenses and are recorded in `docs/resources/vocabularies.md`
  and each `ext/<source>/source.yaml`: ICD-9/10 (public), LOINC (registration; do not
  redistribute the table), RxNorm/SNOMED (UMLS/UTS license; not redistributable), ATC
  (WHO), AHRQ CCSR/Elixhauser (public), CMS GEMs (public), OMOP Athena bundle (per-vocab).
  MIMIC-IV Demo datasets are ODbL 1.0 (attribution + share-alike; redistributable).
- Pretrained model weights (P7/P10) are used only under licenses that permit research
  use; the brief records the license.

## 11. Backup & recovery

The lake, catalogs, derived data and marts are rebuildable from raw + code and are not
backed up. `mwh backup` (EP-52) copies the non-reproducible state — `runs/` ledgers,
`protocols.jsonl`, `audit.jsonl`, model registry metadata, study workspaces — to an
encrypted local target chosen by the owner (never a synced drive). Recovery of the
warehouse = `mwh init` (EP-158) + `mwh build --tier full`.

## 12. Reproducibility obligations

Every run records: git sha, `uv.lock` hash, DuckDB version, snapshot ids of every layer
read, generated SQL, parameters, code-set/phenotype/protocol versions and hashes, cohort
attrition, seeds, warnings, wall time, peak RSS, disk delta, tier. Reports and case studies
cite run ids and reproduce from them (`docs/analyses/README.md` Reproduction blocks).

## 13. Incident response

Accidental commit of data → §3. Accidental paste of rows into a Claude session → end the
session, note it in `DECISIONS.md` (date, what, mitigation); it cannot be un-sent, which is
why the layered enforcement in §4 exists. Suspected PHI in the data → PhysioNet only.
Credential/CITI lapse → stop using the data until renewed.
