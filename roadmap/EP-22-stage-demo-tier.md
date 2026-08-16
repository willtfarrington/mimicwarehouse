# EP-22 — Demo tier (MIMIC-IV Demo 2.2 + ED Demo)

**Size:** M · **Tier:** demo · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-158 (Bootstrap `mwh init` + cloner smoke test on demo tier)

## Context

**D-27**: besides the synthetic fixtures, the warehouse has an on-demand `demo` tier built
from the MIMIC-IV Clinical Database Demo 2.2 (hosp + icu, 100 subjects, ~15 MB gzipped)
and the MIMIC-IV-ED Demo 2.2 — both **ODbL 1.0**, open access, redistributable, no
credentials needed. The demo tier is what screenshots, the cloner smoke test (EP-158),
concept count-pinning (EP-37) and demo mode (EP-159) use. Caveats that bite: the demo is
**v2.2 schema** (drift vs 3.1 → EP-9's demo 2.2 → 3.1 column map), there is **no note
demo**, and demo `subject_id`s sit inside the real MIMIC id bands, so the guard (EP-4)
treats demo rows as real — demo data lives only under the data root and is never
committed (screenshots in demo mode are allowed by GOVERNANCE §3). ED demo tables are
fetched and verified here but staged into `mimiciv_ed` only by EP-142 (**D-4**: ED enters
through the Linkage Wizard). Do not confuse *demo tier* (this brief) with *demo mode*
(EP-159). What exists: loader with `.csv.gz` + column-map support (EP-17), partitioning
(EP-18), `mwh build` tiers (EP-19), catalog builder (EP-21). `mwh demo fetch` is the only
network-touching command in P2 (physionet.org only; not a text module, so the
`MWH_ALLOW_REMOTE` gate does not apply).

## In scope

1. **Fetcher** (`src/mimicwarehouse/demo.py`, `mwh demo fetch [--force]`) — downloads
   `https://physionet.org/files/mimic-iv-demo/2.2/` and
   `https://physionet.org/files/mimic-iv-ed-demo/2.2/` (stdlib `urllib` or `httpx`,
   retries, resume-safe): first `SHA256SUMS.txt` and `LICENSE.txt`, then every file listed
   in the sums (`hosp/*.csv.gz`, `icu/*.csv.gz`, `ed/*.csv.gz`, any `*.csv` index files);
   verify each sha256, refuse and delete a mismatching file, skip already-verified files.
   Layout: `C:\mimicdata\ext\demo\mimic-iv-demo-2.2\{hosp,icu}\` and
   `ext\demo\mimic-iv-ed-demo-2.2\ed\`; write `ext\demo\source.yaml` (`name, version,
   license: ODbL-1.0, url, fetched_at, files: [{path, sha256, bytes}], verified: true`) —
   the precursor of the P9 licensing register (**D-36**). `mwh demo status` prints the
   register (metadata only).
2. **Demo raw root + column map in the DAG** — `mwh build --tier demo` resolves the raw
   root to `ext\demo\mimic-iv-demo-2.2`, the lake root to `lake\demo\` (a separate lake:
   `lake\core` is the credentialed one) and applies EP-9's `Contract.column_map("demo_2_2")` per
   table (rename / `add_null` / `drop`); source paths in `stage.yaml` gain a
   `demo_source` (`hosp/<table>.csv.gz`) where the file name differs. Every hosp/icu step
   from EP-20 and EP-23…EP-27 gets `demo` in its `tiers`; the whole demo build (all 31
   tables) runs in the foreground in about a minute:
   `uv run --group dev mwh build --tier demo` then `mwh build --tier demo --select catalog`
   → `warehouse\demo.duckdb` (`meta.catalog_info.tier = "demo"`).
3. **Lossy-map handling** — where the 2.2 → 3.1 map cannot fill a 3.1 column (`add_null`)
   or drops a 2.2-only column, the loader records the affected columns in the manifest
   line (`map_notes`) and `mwh catalog info --tier demo` lists them; document them in the
   completion note so EP-37's count-pinning knows which concepts may differ on demo.
4. **Docs** — `DESIGN.md` §4 dated note (demo tier layout, `lake\demo`, ED demo fetched
   but unstaged until EP-142); `mimicwarehouse/README.md` quick-start line
   `uv run --group dev mwh demo fetch && uv run --group dev mwh build --tier demo`; the
   ODbL attribution + citation block from `source material/README.md` copied into
   `docs/resources/datasets.md` (EP-15) under a "Demo tier" heading.
5. **Tests** (`tests/ep/test_ep22.py`, `@pytest.mark.ep_22`) — fixture (no network):
   the fetcher against a local fake directory served by monkeypatching the URL opener —
   a matching checksum passes, a corrupted file is refused and deleted, a second run skips
   verified files, `source.yaml` validates; column-map staging with a tiny inline
   2.2-shaped `admissions.csv.gz` (header built from the map's declared differences, ids
   ≥ 90 000 000) yields 3.1 columns and `map_notes`. Extend EP-12's tier vocabulary with
   `demo` (`--tier demo` selects fixture + demo; skipped with a reason when
   `warehouse\demo.duckdb` is absent). `tier("demo")`-marked (opt-in, needs the fetched
   data): `open_catalog("demo")` reports every hosp/icu contract table present and
   `SELECT count(*) FROM mimiciv_hosp.patients` equals 100 (a published property of the
   demo); the ED demo directory exists with a verified `SHA256SUMS.txt`.

## Out of scope

- Demo **mode** for the app (row view/exports enabled on ODbL data) → EP-159.
- Staging ED demo into `mimiciv_ed` and ED concepts → EP-142 (D-4).
- Cloner smoke test / `mwh init` → EP-158; concept count-pinning on demo → EP-37.
- Synthetic notes for text tests → EP-148 (no note demo exists).

## Verification / acceptance

- `uv run poe test -m ep_22` green on fixture; `tier("demo")`-marked tests green after `mwh demo fetch`; `uv run --group dev mwh verify EP-22` green.
- `%MWH_DATA_ROOT%\ext\demo\source.yaml` records verified checksums for both demo datasets; `warehouse\demo.duckdb` exists; `uv run --group dev mwh sql --tier demo --count mimiciv_hosp.patients` prints 100.
- The fetcher **refuses** a corrupted checksum in a test; no demo file is inside the repository tree (`git status` clean of data files; guard hook passes).
- Completion note lists lossy-map columns per table and the demo build wall time.

## Parked → final-roadmap.md

- Other demo datasets (MEDS demo, FHIR demo, OMOP demo) as extra tiers — trigger: an extension EP needs them; already listed under linkage in `final-roadmap.md`.
