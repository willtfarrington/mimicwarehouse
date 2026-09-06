# Concept coverage of the synthetic fixture

*(hand-maintained; EP-169, ledger FXT-4/FXT-5. The generated `README.md` and DESIGN
section 4 link here. Every number below comes from reading the vendored concept SQL
(`src/mimicwarehouse/concepts/vendor/mimic-code/mimic-iv/concepts/`) against the fixture
vocabularies (`src/mimicwarehouse/fixtures/vocab/*.yaml`) - counted as distinct
itemid-shaped tokens in the SQL that the vocab supplies - never from data.)*

The fixture plants enough signal for the loader, the tier machinery and the three
phenotype probes (aki / sepsis / t2dm), not for every vendored concept. When a concept
below matters to an EP, extend `vocab/*.yaml` (and the generators that draw from it) and
regenerate - that is the norm, not a workaround: EP-41 extends the vocab for its T2DM
inputs and regenerates as generator 0.3.0 (see `tests/README.md` section "Changing the
synthetic fixture").

## Expected empty on the fixture

These concepts reference itemids the vocab does not carry at all, so they return zero
rows on the fixture (0/N = vocab itemids / itemids the SQL names):

- `measurement/icp` (0/2), `measurement/rhythm` (0/5)
- `medication/dobutamine` (0/1), `medication/milrinone` (0/1),
  `medication/neuroblock` (0/2)
- `treatment/code_status` (0/1)
- `treatment/crrt` (0/20): the CRRT *settings* chartevents itemids are absent; the
  fixture's CRRT signal is the procedureevents item 225802 only
- `firstday/first_day_bg_art` and everything downstream of it: `labevents` lacks itemid
  52033 (`Specimen Type`), so no blood gas can be classified arterial - the PaO2/FiO2
  branch of `score/sofa`, `score/apsiii`, `score/sapsii` and `score/lods` therefore
  contributes nothing (the other branches of those scores still fire)

## Expected partial

The vocab supplies some, not all, of the itemids these concepts look up; they return
rows for the covered items only:

- `treatment/invasive_line` 1/24 (225752 arterial line only)
- `measurement/urine_output` 2/12 (226559 Foley, 226560 void)
- `measurement/ventilator_setting` 4/15 (220339 PEEP, 223835 FiO2, 223849 mode,
  224684 tidal volume)
- `measurement/vitalsign` 12/20 (HR, SBP/DBP/MBP invasive + non-invasive, RR, SpO2,
  temperature F/C, glucose 225664; no site/o2-flow items)
- `measurement/bg` 8/30 (pH, pO2, pCO2, base excess, bicarbonate, lactate via 50813,
  SpO2/FiO2 from chartevents; no calculated/venous panel items)
- `measurement/enzyme` 5/11 (ALT, AST, ALP, bilirubin total, amylase; no CK/CK-MB/GGT/LD
  or direct/indirect bilirubin)
- `measurement/blood_differential` 3/29 (WBC 51301, absolute neutrophils 51256,
  lymphocytes 51244; no monocytes/eosinophils/basophils/bands or percent forms)
- `measurement/oxygen_delivery` 1/13 (226732 O2 delivery device only)

## Caveats

- `organfailure/kdigo_stages`: the planted AKI admissions carry a creatinine doubling
  and a CRRT procedureevents row (225802), but `treatment/crrt` matches by chartevents
  settings itemids (all absent), so the CRRT route to stage 3 never fires on the
  fixture; staging comes from the creatinine/urine-output criteria only.
- EP-41's T2DM phenotype inputs are absent by design: HbA1c (itemid 50852), any
  non-insulin antidiabetic (`drugs.yaml` carries insulin/glargine but no metformin or
  sulfonylurea), and the T1DM exclusion ICD codes. EP-41 extends `vocab/d_labitems.yaml`
  / `vocab/drugs.yaml` / the ICD vocab and regenerates (GENERATOR_VERSION 0.3.0).
- ED / Note fixture modules do not exist yet (EP-142 / EP-148 own them); every ED/Note
  concept or query is out of the fixture's reach until then.
- `measurement/complete_blood_count` under the EP-38 patch (`complete_blood_count-mchc-
  unit-pr2141`): the vocab records MCHC (itemid 51249) with `valueuom: "%"`, exactly the
  unit the upstream fix excludes, so `mchc` is NULL on every fixture specimen (the other
  nine CBC items are unaffected; `inflammation`'s CRP carries `mg/L` and passes). Extend
  the vocab with a `g/dL` MCHC when a later EP needs a fixture MCHC value.
