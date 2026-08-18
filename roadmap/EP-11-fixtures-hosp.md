# EP-11 — Synthetic fixture generator A (hosp)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-9 (Schema registry (YAML contract)) · **Blocks:** EP-12 (Synthetic fixture generator B (icu) + pytest tier markers), EP-16 (Re-plan P1)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) **Pre-commit vs byte-identity (EP-4):** the repo-root `.pre-commit-config.yaml` runs `end-of-file-fixer`
> and `trailing-whitespace` on every staged file — a fixture CSV whose last byte is not `\n`, or a quoted value
> with a trailing blank, would be rewritten *after* `manifest.json` was computed and the drift test would fail
> at the next run. Item 4 now requires the writer to emit exactly what those hooks accept (final `\n`, no
> trailing blanks — CSV quoting must not depend on them), and item 6 tests it (`pre-commit run --files
> <fixture files>` is a no-op); `check-yaml`/`check-json` also parse `fixtures/vocab/*.yaml` and
> `manifest.json`. (2) **G4 scans every column**, not only ids: `.csv` is a guard text extension, so any
> isolated 8-digit value starting 1/2/3 anywhere (a `gsn`, `ndc`, `drg_code`, `orderid`, a compact date) trips
> the rule — the generator keeps every non-id numeric field out of `10 000 000–39 999 999` or gives it 9+
> digits / a prefix, and never writes compact `YYYYMMDD`. (3) `config.workspace_root()` resolves from the
> package location only in the source checkout (falls back to CWD from a wheel install) — fine for `uv run`
> here; say so in the CLI help. (4) **CLI contract (EP-3):** `mwh fixtures` is not a diagnostic command and
> will receive *validated* settings although it never touches the data root — either add `"fixtures"` to
> `cli.DIAGNOSTIC_COMMANDS` (recommended, like `schema` at EP-9) or accept the coupling; note the choice in
> the completion note. (5) Endpoint security (Risk 12, D-42): 22 CSV writes from the allow-listed managed
> `python.exe` are fine; if a run dies mid-write, check Malwarebytes Quarantine first. Command forms:
> `uv run mwh …` ≡ `uv run --group dev mwh …`.

## Context

Every later brief develops and tests on the `fixture` tier first (**D-18**): a committed synthetic
mini-MIMIC whose ids are all ≥ 90 000 000 so the guard (EP-4) can tell it from real rows (**D-27**,
GOVERNANCE §2/§3, `.gitignore` allow-lists `mimicwarehouse/tests/fixtures/**`). This brief builds the
deterministic generator for the 22 `mimiciv_hosp` tables, checked against the EP-9 contract
(`load_contract()`, `Table.read_csv_columns()`); EP-12 adds the 9 icu tables, the in-memory fixture
catalog and the pytest tier markers. The fixture must be shaped like MIMIC-IV **3.1** (provider ids,
`anchor_year_group`, ICD-9/10 by era, `hadm_id`-less outpatient labs, quoted multi-line comments) so the
loader (EP-17), the concepts (EP-37), phenotypes (EP-41/42) and the tracer bullet (EP-31) all find what
they expect — and it must mirror the MIMIC caveats (ages ≥ 89 coded 91, shifted years, `dod` within ~1 y
of last discharge, ICD-9 → ICD-10 switch) so tests exercise them. Nothing here reads real data: the
seed vocabularies are typed from public documentation (mimic.mit.edu, ICD/LOINC public lists), never
from `source material/` (denied to sessions), and no real row is ever copied. Realism target: "plausible
enough for the loader, concepts and phenotypes", not clinical fidelity.

## In scope

1. **Spec + plan** (`src/mimicwarehouse/fixtures/__init__.py`, `spec.py`): pydantic `FixtureSpec(seed=2026,
   n_subjects=120, first_subject_id=90_000_000, first_hadm_id=90_000_000, first_stay_id=90_000_000,
   admissions_per_subject_mean=1.5, icu_fraction=0.4, mortality_rate=0.08, labs_per_admission=40, …)` and a
   `FixturePlan` built once from `numpy.random.default_rng(seed)`: subjects (ids consecutive from
   `first_subject_id`, so `subject_id % 100` spans buckets 0–99 and the dev filter `< 5` keeps 10 subjects;
   never choose a seed/constant that is an 8-digit number inside the real id bands — a date-shaped seed such as
   2026-08-16 written as one integer would trip the guard's token scan),
   admissions with `admittime`/`dischtime` (LOS lognormal 1–20 d), death flags, an **ICU segment plan**
   (`plan.icu_segments`: ~40 % of admissions get one ICU careunit interval inside the stay — EP-12 derives
   `icustays`/icu events from exactly these so `transfers` and `icustays` agree). Deterministic: same spec ⇒
   byte-identical output.
2. **Seed vocabularies** (`src/mimicwarehouse/fixtures/vocab/*.yaml`, package data, hand-typed): ~40 `d_labitems`
   (real itemids/labels the concepts use: creatinine 50912, potassium 50971, sodium 50983, lactate 50813,
   WBC 51301, hemoglobin 51222, platelets 51265, bilirubin 50885, glucose 50931, pH 50820, …) with per-item
   `valueuom`, plausible range, ref range; ~40 ICD-9 + ~40 ICD-10 codes with titles incl. hypertension, T2DM
   (`25000`/`E119`), sepsis (`99591`/`A419`), AKI (`5849`/`N179`), CKD, HF, pneumonia; ~10 ICD procedures (both
   versions); ~10 `d_hcpcs`; ~30 drugs (vancomycin, piperacillin-tazobactam, norepinephrine, insulin, heparin,
   metoprolol, furosemide, propofol, …) with routes/units; admission types/locations, discharge locations,
   insurance/language/marital/race categories, careunits (incl. the MetaVision ICU names), services, DRG codes.
3. **Table generators** (`src/mimicwarehouse/fixtures/hosp.py`, one function per table, ≤ ~30 lines each, all
   returning Polars frames whose columns/dtypes come from the contract): `patients` (gender, `anchor_age` 18–91
   with a few 91s, `anchor_year` 2110–2200, `anchor_year_group` from the 5 real labels, `dod` for ~15 % within
   0–365 d after last discharge and always ≥ `deathtime` when in-hospital), `admissions` (types, locations,
   `deathtime`/`hospital_expire_flag` for the planned deaths, `edregtime`/`edouttime` for ~50 %,
   `admit_provider_id`), `transfers` (ED → admit → wards/ICU segment → discharge chain, `eventtype`,
   `careunit`, `transfer_id`), `services`, `diagnoses_icd`/`procedures_icd` (ICD-9 for admissions in the
   2008–2010/2011–2013 groups and half of 2014–2016, ICD-10 after — the switch), `drgcodes` (HCFA + APR rows),
   `hcpcsevents`, `omr` (`result_name`/`result_value` text such as blood pressure `120/80`), `labevents`
   (~40/admission inside the stay + ~20 % with `hadm_id` NULL outside it; `valuenum` from item ranges with rare
   outliers, `flag`, `ref_range_*`, `priority`, `storetime` ≥ `charttime`, `comments` mostly NULL and a few
   containing commas, quotes and **embedded newlines**), `microbiologyevents` (blood/urine cultures, mostly no
   growth, some organisms + antibiotic sensitivities), `prescriptions`, `pharmacy`, `poe`/`poe_detail`
   (`poe_id` = `<subject_id>-<poe_seq>`), `emar`/`emar_detail` (`emar_id` = `<subject_id>-<emar_seq>`, `Administered`
   events tied to prescriptions), `provider` (fake ids like `P90001`), and the four dims from the vocab. Plant
   cheap phenotype signal in a handful of admissions: creatinine doubling within 48 h (AKI), blood culture + IV
   antibiotic within 24 h (sepsis suspicion), T2DM codes + insulin + glucose (T2DM).
4. **Writer + CLI** (`src/mimicwarehouse/fixtures/write.py`; `mwh fixtures build [--out <dir>] [--seed N]
   [--subjects N]`, `--out` defaulting to the workspace `tests/fixtures/` resolved from the package location, not
   CWD): writes `tests/fixtures/mimic-iv-3.1/hosp/<table>.csv` mirroring the raw layout (so EP-17 can point
   `--source tests/fixtures/mimic-iv-3.1` at it), header order = contract order, LF endings, a final `\n` and
   no trailing blanks on any line (so the `end-of-file-fixer` / `trailing-whitespace` hooks are no-ops —
   amended EP-7), fixed float formatting, rows sorted by the contract `sort_keys`, timestamps
   `YYYY-MM-DD HH:MM:SS` (never compact `YYYYMMDD` — G4), no non-id numeric field inside `10 000 000–39 999 999`
   (G4 scans every CSV column — amended EP-7); plus
   `tests/fixtures/manifest.json` (per file: sha256, bytes, rows, seed, generator version) and
   `tests/fixtures/README.md` (synthetic, ids ≥ 90 000 000, regeneration command, MIT like the code). Budget:
   hosp ≤ 6 MB total (whole fixture ≤ 10 MB after EP-12); shrink `labs_per_admission` before shrinking subjects.
5. **Contract + integrity checks** (`mimicwarehouse.fixtures.check.validate(frames, contract)`, also run by the
   CLI): every frame has exactly the contract columns and castable dtypes; every id column ≥ 90 000 000; FK
   integrity (`hadm_id` → admissions, `subject_id` → patients, `itemid` → `d_labitems`, ICD codes → `d_icd_*`,
   `hcpcs_cd` → `d_hcpcs`); `dischtime > admittime`; `dod` ≥ last `dischtime`; every ICU segment lies inside its
   admission. Record `mwh fixtures` and the fixture layout as a dated note in `DESIGN.md` §4/§15.
6. **Tests** (`tests/ep/test_ep11.py`, `@pytest.mark.ep_11`): regeneration into `tmp_path` reproduces the committed
   files byte-for-byte (sha256 vs `manifest.json` — the "fixture drift" test); all 22 files load through DuckDB
   `read_csv(columns=contract types, header=true)` with zero rejects; integrity checks pass; the ICD-9/10 split by
   era holds; at least one `labevents.comments` value contains a newline; `mwh guard` (EP-4) accepts the fixture
   directory (no token in the real id bands 10 000 000–39 999 999 anywhere — if it flags a token, change the
   generator, never the guard).

## Out of scope

- icu tables, `d_items`, `caregiver`, chartevents-shaped signal → EP-12.
- ED and note fixtures → EP-142 / EP-148 (synthetic notes; there is no note demo).
- Loading fixtures into Parquet/catalogs, `subject_bucket` → EP-17/18/21 (EP-12 provides the in-memory catalog).
- The ODbL demo tier → EP-22.

## Verification / acceptance

- `uv run poe test -m ep_11` and `uv run --group dev mwh verify EP-11` green on fixture.
- `uv run --group dev mwh fixtures build` twice ⇒ `git status` clean after the second run (byte-identical); the
  22 CSVs + `manifest.json` + `README.md` exist under `mimicwarehouse/tests/fixtures/mimic-iv-3.1/hosp/` and total
  ≤ 6 MB.
- Pre-commit (`mwh guard`) passes with the fixture files staged; the diff contains no number in the real id bands;
  `uv run --group dev pre-commit run --files mimicwarehouse/tests/fixtures/**` modifies nothing (the fixer hooks
  are no-ops on the generated files — amended EP-7).
- Commit `feat(mimicwarehouse): synthetic hosp fixture generator (EP-11)`, then `docs(roadmap): record EP-11 commit hash`.

## Parked → final-roadmap.md

- Refreshing the fixture dims (`d_labitems`, `d_icd_*`, `d_hcpcs`) from the real dictionaries via a safe path once
  EP-20/EP-29 stage them (dictionaries are not patient data); trigger: a concept or phenotype needs an itemid the
  hand-typed seed lacks.
- Synthea-based richer synthetic cohorts; trigger: fixture realism blocks a method test (see EP-15 `datasets.md`).
