# docs/resources — index

The resource inventories of phase P1 (D-10, owner template steps 2-3): cited markdown files
recording what exists around MIMIC-IV — provenance manifests, repositories, vocabularies,
reading — each with licenses and an explicit verdict, so later briefs cite one line here
instead of re-researching. Files land with their owning EP; all P1 resource files have shipped.
`reading.md`'s 38 category headings are pinned to the roadmap README coverage table — a
re-plan that re-titles a category updates `reading.md` and `tests/ep/test_ep15.py` in the
same commit.

| File | What | Owner EP |
|---|---|---|
| [raw-inventory.md](raw-inventory.md) | Locally computed provenance of the raw PhysioNet CSVs: hashes, sizes, header checks, row counts reconciled against the vendored `validate.sql` | EP-10 |
| [repos.md](repos.md) | Repos & awesome-lists inventory: adopt / port / ignore verdicts per resource + the borrow map the later briefs cite | EP-13 |
| [vocabularies.md](vocabularies.md) | Ontologies & vocabularies inventory: register with licenses/versions/redistribution verdicts, the `ext/vocab/` landing convention + `source.yaml` template, and the which-EP-needs-what table | EP-14 |
| [reading.md](reading.md) | Reading list: ≥ 1 cited, link-checked entry per capability category (all 38), anchored on the MIMIC-IV papers and the methods canon each representative workflow stands on | EP-15 |
| [datasets.md](datasets.md) | Open companion datasets register: demo family, eICU, Synthea, imaging/waveform links — license, access class, size and a may-it-enter-git verdict per row | EP-15 |
| [methods-notes.md](methods-notes.md) | MIMIC caveats catalogue (12 entries), the default analytic choices (D-n), and the how-to-cite-runs stub EP-32 extends | EP-15 |
| [concepts.md](concepts.md) | mimic-code `concepts_duckdb` inventory as the warehouse runs it: concept · group · reads · depends on · upstream commit · status on DuckDB 1.5.x (generated block from `concepts/concepts.yaml`; `KNOWN_FAILURES` stayed empty at EP-38) + § Deviations: the EP-38 patch registry (`concepts/patches/patches.yaml`) with upstream PR references and the demo count effect of each port | EP-37, EP-38 |
