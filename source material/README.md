# Source material

This directory is the landing zone for the **raw, unmodified source datasets** that
`mimicwarehouse` is built from. The warehouse ingests from here; nothing in this
directory is ever edited in place.

> **The data is not in this repository — and never will be.**
>
> Everything under `source material/` except markdown files is excluded from git
> by the repository's [`.gitignore`](../.gitignore) (`source material/*` with an
> allow-list for `*.md`). If you cloned this repo, this directory will contain
> only this README. To reproduce the warehouse you must obtain the datasets
> yourself, as described below.

Two reasons for the exclusion, either of which would be sufficient on its own:

1. **Licensing / sensitivity.** All of the source data is distributed by
   [PhysioNet](https://physionet.org/) under the
   *PhysioNet Credentialed Health Data License 1.5.0*. Although the data is
   de-identified, it is real patient-level electronic health record data from
   Beth Israel Deaconess Medical Center. The license is granted per-person to
   credentialed researchers and expressly prohibits sharing access with anyone
   else. Committing it — even to a private remote — would breach that agreement.
2. **Size.** The three datasets total roughly **98 GB decompressed** (single
   files up to 40 GB). This is well beyond what git, GitHub, or git-LFS handle
   sensibly.

---

## Datasets

All three are part of the MIMIC-IV family maintained by the MIT Laboratory for
Computational Physiology (MIT-LCP). They share the same `subject_id` /
`hadm_id` key space and are designed to be joined.

| Dataset | Version | Local directory | Decompressed size | Released | PhysioNet page |
|---|---|---|---|---|---|
| **MIMIC-IV** (core: `hosp` + `icu` modules) | 3.1 | `mimic-iv-3.1/` | ~91 GB | Oct 2024 | <https://physionet.org/content/mimiciv/3.1/> |
| **MIMIC-IV-ED** (emergency department) | 2.2 | `mimic-iv-ed-2.2/` | ~0.7 GB | Jan 2023 | <https://physionet.org/content/mimic-iv-ed/2.2/> |
| **MIMIC-IV-Note** (deidentified free-text clinical notes) | 2.2 | `mimic-iv-note-deidentified-free-text-clinical-notes-2.2/` | ~6.3 GB | Jan 2023 | <https://physionet.org/content/mimic-iv-note/2.2/> |

Local directory names are exactly the names PhysioNet uses for the download
archives, so the layout below is what you get from a straight download +
decompress with no renaming.

Approximate scale of the core dataset (from the MIMIC-IV `CHANGELOG.txt`,
v3.0 figures; v3.1 removed two orphaned `subject_id`s and fixed lab `itemid`
regressions): **~364,600 patients, ~546,000 hospital admissions, ~94,500 ICU
stays**, covering 2008–2022.

---

## Obtaining the data

Access is gated. As of the current PhysioNet policy, each dataset requires:

1. A [PhysioNet account](https://physionet.org/register/) that has been
   **credentialed** (identity verification plus a reference).
2. Completion of the **CITI "Data or Specimens Only Research"** training course,
   with the certificate uploaded to your PhysioNet profile.
3. Signing the **PhysioNet Credentialed Health Data Use Agreement 1.5.0** for
   *each* dataset individually (MIMIC-IV, MIMIC-IV-ED, and MIMIC-IV-Note are
   separate projects with separate DUAs).

Once approved, download from each project page above (the pages offer a
`wget` command and a zip download). PhysioNet ships the tables as gzipped CSV
(`*.csv.gz`); the local copies here have been **decompressed in place to plain
`*.csv`**, so loaders in this repo should expect uncompressed CSV (the loader
also accepts `*.csv.gz`). Each dataset ships a `SHA256SUMS.txt` — verify your
download against it *before* decompressing.

> **Provenance note (2026-08-16).** `SHA256SUMS.txt` lists only the `.csv.gz`
> archives, and the archives were deleted after decompression, so the plain CSVs
> here **cannot be re-verified against PhysioNet's checksums**. The warehouse
> therefore treats a locally computed manifest — SHA-256, byte size and row count
> of every plain CSV, reconciled against the row counts published in mimic-code's
> `validate.sql` — as the raw snapshot id (roadmap EP-10; decision D-26 in
> `../mimicwarehouse/DECISIONS.md`). Re-downloading the `.csv.gz` archives to
> restore checksum-verifiable raw is an optional extension item. The CSVs stay
> untouched (D-30); everything derived from them lives outside this repository
> in `C:\mimicdata` (`MWH_DATA_ROOT`) — see `../mimicwarehouse/GOVERNANCE.md`.

The official schema documentation lives at <https://mimic.mit.edu/docs/iv/>.

---

## Expected local layout

After download and decompression, `source material/` should look like this
(sizes are decompressed, rounded):

```
source material/
├── README.md                                   ← the only file tracked by git
│
├── mimic-iv-3.1/                               ~91 GB
│   ├── CHANGELOG.txt
│   ├── LICENSE.txt
│   ├── SHA256SUMS.txt
│   ├── hosp/                                   hospital-wide EHR module
│   │   ├── admissions.csv            90 MB
│   │   ├── d_hcpcs.csv               3 MB     (dimension)
│   │   ├── d_icd_diagnoses.csv       9 MB     (dimension)
│   │   ├── d_icd_procedures.csv      7 MB     (dimension)
│   │   ├── d_labitems.csv            64 KB    (dimension)
│   │   ├── diagnoses_icd.csv         174 MB
│   │   ├── drgcodes.csv              53 MB
│   │   ├── emar.csv                  5.9 GB
│   │   ├── emar_detail.csv           8.1 GB
│   │   ├── hcpcsevents.csv           12 MB
│   │   ├── labevents.csv             18 GB
│   │   ├── microbiologyevents.csv    868 MB
│   │   ├── omr.csv                   307 MB
│   │   ├── patients.csv              12 MB
│   │   ├── pharmacy.csv              3.8 GB
│   │   ├── poe.csv                   4.8 GB
│   │   ├── poe_detail.csv            405 MB
│   │   ├── prescriptions.csv         3.3 GB
│   │   ├── procedures_icd.csv        33 MB
│   │   ├── provider.csv              292 KB
│   │   ├── services.csv              25 MB
│   │   └── transfers.csv             196 MB
│   └── icu/                                    ICU (MetaVision) module
│       ├── caregiver.csv             104 KB
│       ├── chartevents.csv           40 GB     ← largest single file
│       ├── d_items.csv               368 KB    (dimension)
│       ├── datetimeevents.csv        1.1 GB
│       ├── icustays.csv              15 MB
│       ├── ingredientevents.csv      2.4 GB
│       ├── inputevents.csv           2.7 GB
│       ├── outputevents.csv          442 MB
│       └── procedureevents.csv       144 MB
│
├── mimic-iv-ed-2.2/                            ~0.7 GB
│   ├── LICENSE.txt
│   ├── README.txt                              (PhysioNet's own table descriptions)
│   ├── SHA256SUMS.txt
│   └── ed/
│       ├── diagnosis.csv             50 MB
│       ├── edstays.csv               38 MB
│       ├── medrecon.csv              360 MB
│       ├── pyxis.csv                 106 MB
│       ├── triage.csv                37 MB
│       └── vitalsign.csv             115 MB
│
└── mimic-iv-note-deidentified-free-text-clinical-notes-2.2/   ~6.3 GB
    ├── LICENSE.txt
    ├── SHA256SUMS.txt
    └── note/
        ├── discharge.csv             3.3 GB
        ├── discharge_detail.csv      7 MB
        ├── radiology.csv             2.7 GB
        └── radiology_detail.csv      293 MB
```

---

## Handling obligations

Anyone working with a local copy of this data is bound by the license they
signed. In brief (see each dataset's `LICENSE.txt` for the full text):

- Do not attempt to re-identify any individual or institution.
- Do not share access with anyone — this includes not committing it, not
  pushing it to any remote, not attaching it to issues, and not pasting raw
  rows into chat tools or LLM prompts.
- Keep the local copy secured (encrypted disk, no shared/public folders).
- If you find something that looks like PHI, report it to
  <PHI-report@physionet.org> rather than posting about it.
- Use is limited to lawful scientific research.
- Maintain current human-subjects / HIPAA training.
- Code arising from publications using this data should be released to the
  research community (this repository is intended to satisfy that spirit).

Derived artifacts (aggregates, synthetic data, schema/DDL, code) may be
committed provided they contain no row-level source data; if in doubt, treat it
as source data.

---

## Citations

If you publish work built on this warehouse, cite the datasets and PhysioNet:

**MIMIC-IV v3.1**
Johnson, A., Bulgarelli, L., Pollard, T., Gow, B., Moody, B., Horng, S.,
Celi, L. A., & Mark, R. (2024). MIMIC-IV (version 3.1). PhysioNet.
RRID:SCR_007345. <https://doi.org/10.13026/kpb9-mt58>

Johnson, A. E. W., Bulgarelli, L., Shen, L., Gayles, A., Shammout, A.,
Horng, S., Pollard, T. J., Hao, S., Moody, B., Gow, B., Lehman, L. H.,
Celi, L. A., & Mark, R. G. (2023). MIMIC-IV, a freely accessible electronic
health record dataset. *Scientific Data*, 10, 1.
<https://doi.org/10.1038/s41597-022-01899-x>

**MIMIC-IV-ED v2.2**
Johnson, A., Bulgarelli, L., Pollard, T., Celi, L. A., Mark, R., & Horng, S.
(2023). MIMIC-IV-ED (version 2.2). PhysioNet.
<https://doi.org/10.13026/5ntk-km72>

**MIMIC-IV-Note v2.2**
Johnson, A., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023).
MIMIC-IV-Note: Deidentified free-text clinical notes (version 2.2). PhysioNet.
<https://doi.org/10.13026/1n74-ne17>

**PhysioNet**
Goldberger, A., Amaral, L., Glass, L., Hausdorff, J., Ivanov, P. C., Mark, R.,
Mietus, J. E., Moody, G. B., Peng, C. K., & Stanley, H. E. (2000). PhysioBank,
PhysioToolkit, and PhysioNet: Components of a new research resource for complex
physiologic signals. *Circulation*, 101(23), e215–e220.
