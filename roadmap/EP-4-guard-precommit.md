# EP-4 — Governance enforcement: pre-commit + `mwh guard`

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-2 (`mwh` CLI skeleton + `mwh doctor`) · **Blocks:** EP-7 (Re-plan P0)

## Context

GOVERNANCE §3 promises that `.gitignore`, `.gitattributes` and the `mwh guard` pre-commit hook
together refuse data-shaped files and files containing ids in the real MIMIC bands; only the first
two exist (EP-0). Git history is permanent and the remote goes public at v1.0.0 after a history
sweep (D-41), so the hook must be in place before EP-8 vendors code and long before EP-17 writes
Parquet. This brief builds `mimicwarehouse.guard` (DESIGN §15), wires it into
`.pre-commit-config.yaml` at the repo root together with ruff and the generic hygiene hooks, and
proves it by refusing crafted violations (governance-brief acceptance). Constraints: fixture ids
are ≥ 90 000 000 (D-27) so they never collide with the bands quoted in CLAUDE.md §2; violation
output must never echo file content — a real id in a hook message would land in a session
transcript, so tokens are masked. pre-commit is already in the `dev` group (EP-1). Commands run in
`mimicwarehouse/`; git commands at the repo root.

## In scope

1. **`src/mimicwarehouse/guard.py`** — `scan(paths, repo_root) -> list[Violation(rule, path, line,
   detail)]` and `scan_staged(repo_root)` (paths from
   `git diff --cached --name-only --diff-filter=ACMR -z`). Rules, each with an id used in messages
   and tests:
   - **G1 data-shaped extension**: `.csv .csv.gz .parquet .duckdb .duckdb.wal .wal .duckdb.new
     .jsonl .feather .arrow .pkl .joblib .skops .pt .safetensors .npy .npz .h5` anywhere except
     `mimicwarehouse/tests/fixtures/**`, where only `.csv .csv.gz .parquet .jsonl .json .yaml` pass;
   - **G2 source material**: any path under `source material/` other than `*.md`;
   - **G3 notebook outputs**: `.ipynb` whose JSON has a cell with non-empty `outputs` or a non-null
     `execution_count`; anything under `notebooks/**/__marimo__/`;
   - **G4 real-id band**: in text files (UTF-8-decodable, no NUL byte; extensions `.py .md .yaml
     .yml .json .toml .sql .txt .csv .jsonl .html .svg .cff .ps1 .ini .cfg` and extensionless) any
     token matching `(?<![\w.])[123]\d{7}(?![\w.])` whose integer value lies in the subject / hadm /
     stay bands (bounds as module constants `SUBJECT_BAND`, `HADM_BAND`, `STAY_BAND`, written with
     digit-group underscores so the module never trips itself) — unless the same line carries the
     pragma `mwh-guard: allow`; compact `YYYYMMDD` dates are *not* exempt (write ISO dates with
     hyphens); longer digit runs, hex hashes and decimals never match by construction;
   - **G5 oversize**: any file > 20 MB (fixtures included; the synthetic generator stays under it).
   `Violation.detail` masks the token to its first digit plus `*` (`1*******`) and never quotes the
   line. `mwh guard [PATHS…] [--staged] [--all-tracked] [--selfcheck] [--json]`: default
   `--staged`; rich table of violations; exit 0 clean / 1 violations / 2 usage; `--all-tracked`
   scans `git ls-files` (the per-commit primitive EP-163's history sweep will call — the history
   walk itself is not built here); `--selfcheck` re-runs the EP-0 probe list through
   `git check-ignore` and asserts `git check-attr binary -- x.csv x.parquet x.duckdb` are `set`.
2. **`.pre-commit-config.yaml`** (repo root; no `default_language_version` — pre-commit builds
   hook environments with the interpreter it runs under, i.e. the venv's 3.13):
   `repo: local`, `language: system` hooks in this order — `mwh-guard`
   (`entry: uv run --project mimicwarehouse --group dev mwh guard --staged`, `pass_filenames: false`,
   `always_run: true`), `ruff-check` (`uv run --project mimicwarehouse --group dev ruff check
   --force-exclude`, `types_or: [python]`), `ruff-format` (`… ruff format --check --force-exclude`);
   then `pre-commit/pre-commit-hooks` (pinned `rev`): `check-added-large-files --maxkb=20000`,
   `check-merge-conflict`, `check-yaml`, `check-toml`, `check-json`, `end-of-file-fixer`,
   `trailing-whitespace`, `detect-private-key`. Install with `uv run --group dev pre-commit install`
   (nothing touches `C:\Python314`).
3. **Tests `tests/ep/test_ep04.py`** (`@pytest.mark.ep_4`; a throw-away repo `git init`-ed under
   `tmp_path`): G1 — `x.csv` at root → violation, `mimicwarehouse/tests/fixtures/hosp/patients.csv`
   with two synthetic rows (ids `90_000_001`, `90_000_002`) → clean, `tests/fixtures/model.pt` →
   violation; G2 — `source material/x/y.txt` → violation, `source material/README.md` → clean;
   G3 — a minimal `.ipynb` with one executed cell → violation, cleared → clean; G4 — a `.md`
   containing `f"subject_id={SUBJECT_BAND[0] + 7}"` (built at runtime — a literal would trip the
   guard on this very file) → violation whose rendered output does **not** contain the id; the
   same line with `mwh-guard: allow` → clean; `90000001`, a 40-char hex sha and a 12-digit byte
   count → clean; G5 — a 21 MB file → violation; CLI — `mwh guard <paths>` exit codes 1 / 0,
   `--json` shape, `scan_staged` after `git add -A`; `--selfcheck` passes on the real repo.
4. **Docs & tasks**: poe task `guard = "mwh guard --staged"`; `mimicwarehouse/README.md`
   "Contributing" lines (`pre-commit install`, what the guard refuses, the pragma); GOVERNANCE §3
   already names the hook — add a dated DESIGN §15 note only if the rule list above changed.

## Out of scope

- Full-history sweep before going public → EP-163; PreToolUse output-scanning hook → already
  parked (v2 GOV-1, D-39).
- Small-cell / free-text scanning of aggregates → EP-43 (`disclose.check`); the guard is a
  shape-and-id filter, not a disclosure reviewer.
- Secret scanning → parked below.

## Verification / acceptance

- Refusal test in the throw-away repo: add a `probe.csv` and a `.md` with a runtime-built band id,
  `git add -A`, `uv run --group dev mwh guard --staged` → exit 1 listing G1 and G4 with masked
  tokens; after `git rm --cached` both → exit 0.
- Hook wired: `uv run --group dev pre-commit run --all-files` green on the real repo; a deliberate
  commit attempt of a `.csv` at the repo root is refused by the hook (then `git reset` — no
  violation is ever committed).
- `uv run --group dev mwh guard --selfcheck` passes; `uv run poe test -m ep_4` green; lint /
  typecheck green; `mwh verify EP-4` (from EP-6) green when EP-6 runs it.
- No test, fixture or doc written by this brief contains a plain 8-digit number in the bands (the
  hook itself proves this at commit).

## Parked → final-roadmap.md

- gitleaks-style secret scanning in the hook — trigger: the first token or credential enters the
  project (`.env` + keyring default); hazard: false positives on hashes.
