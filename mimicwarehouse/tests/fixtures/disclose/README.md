# disclose fixtures (EP-43)

Three hand-written, **synthetic** artefacts the disclosure gate is exercised on
(`tests/ep/test_ep43.py`; the brief's acceptance commands):

| file | what `mwh disclose check` says | why |
|---|---|---|
| `bad_ids.csv` | `ID_COL`, exit 1 | an identifier-named column (`subject_id`); its values are fixture-band ids (>= 90 000 000) so guard G4 stays clean - the *name* is the violation |
| `bad_small_cell.md` | `SMALL_CELL`, exit 1 | a Markdown table with an unmarked count of 7 |
| `good_aggregate.csv` | pass, exit 0; `--write-sidecar` writes `good_aggregate.csv.disclosure.json` | a clean two-way aggregate, every count >= 11 and every nested difference >= 11 |

The sidecar the third command writes is not committed (it carries a timestamp and the
git sha of the moment it was written); tests write theirs into a temporary copy.
