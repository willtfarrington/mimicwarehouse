# EP-164 — Toolchain remediation (P1)

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks) · **Blocks:** EP-16 (Re-plan P1)

## Context

The per-phase optional "toolchain remediation" slot (roadmap README § How to use; DECISIONS
judgment calls), allocated by the owner at the P0 re-plan (EP-7, 2026-08-17) after roadmap Risk 12 /
the D-38 addendum: the host runs **two** real-time endpoint products — Windows Defender and
**Malwarebytes 5.1 Premium** — and Malwarebytes' Ransomware Protection module judges *processes* by
I/O pattern (it killed and quarantined the unsigned Git `bash.exe` during a burst `cp -r` /
`sed -i` / `rm -rf` in a session scratchpad on 2026-08-17). D-38's owner-side list was widened to
"Defender exclusion for `C:\mimicdata` **and Malwarebytes allow-list entries for the toolchain and
both data locations**" (seven paths: `C:\Program Files\Git`, `%APPDATA%\uv\python`, the workspace
`.venv`, `C:\mimicdata`, `source material\`, `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*`,
`%USERPROFILE%\.cache\pre-commit`), but — like the Defender exclusion — none of it is readable
non-elevated, and `mwh doctor` (EP-2/EP-3, 13 checks) knows only about Defender. Before the loader
briefs (EP-17+) write thousands of Parquet files from an unsigned `python.exe`, the doctor must at
least **name** every real-time product it can see and remind the owner which paths must be
allow-listed. Windows exposes that list without elevation through WMI/CIM:
`Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct` (rows: `displayName`,
`productState`, `pathToSignedProductExe`, `timestamp`); `productState` is a bit field whose second
byte encodes real-time protection (`0x10` on / `0x00` off) and whose third byte encodes
signature status (`0x00` up to date / `0x10` out of date). This brief adds that one check. It is
Windows-only (`info` elsewhere), non-elevated, ≤ 2 s, and uses the same `_run` / `_powershell`
subprocess seam as the other probes so tests fake it. No data is touched; commands run in
`mimicwarehouse/`.

## In scope

1. **`doctor.py` — `antivirus` check** (`check_antivirus()`), inserted after `defender` in the
   check order: run `Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct |
   Select-Object displayName, productState, pathToSignedProductExe | ConvertTo-Json -Compress`
   through `_powershell` (10 s timeout like every probe; parse one object or a list); decode
   `productState` into `enabled` (real-time on/off) and `up_to_date`; `value` =
   `{"products": [{"name", "state", "enabled", "up_to_date", "exe"}], "non_defender_realtime":
   [names]}` — nothing else (no paths beyond `pathToSignedProductExe`).
   - Status: **warn** when a real-time-enabled product other than Windows Defender is present —
     detail names it and reminds in one line that the D-38 allow-list paths (the seven above,
     spelled out) must be excluded in *that* product too and that its exclusion list is not
     readable non-elevated (same wording pattern as `defender`); **info** when Defender is the only
     product, or the query returns nothing / fails (detail says why: "SecurityCenter2 not
     available", "not a Windows host — antivirus probe skipped"); never **fail** — the check
     reports, the owner decides.
   - Cost ≤ 2 s (one PowerShell process); no admin; the JSON report shape of `mwh doctor --json`
     is unchanged except for the added check object (`{"id","status","detail","value"}`).
2. **`cli.py` / `mwh doctor`** need no change beyond the check registering in `run_checks`; the
   summary line and exit-code rules stay (warn never fails).
3. **Tests `tests/ep/test_ep164.py`** (`@pytest.mark.ep_164`, fixture tier): the fake `_run` /
   `_powershell` returns (a) Defender only → `info`; (b) Defender + a second real-time product →
   `warn`, detail names the product and mentions the allow list, `value["non_defender_realtime"]`
   = that name; (c) second product present but real-time **off** (`productState` second byte
   `0x00`) → `info`; (d) empty result / non-JSON / `FileNotFoundError` → `info` with the reason,
   no exception; (e) non-Windows (`IS_WINDOWS` monkeypatched) → `info` skipped; (f) `mwh doctor
   --json` still parses and lists 14 check ids; the existing EP-2/EP-3 doctor tests keep passing
   (their fake runner gains one more command pattern; a stray unmocked `subprocess.run` is still a
   test failure).
4. **Docs**: DESIGN §15 dated note (`doctor.py` now 14 checks, `antivirus`); DECISIONS D-38 gets a
   one-line addendum "checked (names only) by `mwh doctor antivirus` from EP-164"; workspace README
   doctor section lists the new row; roadmap Risk 12 gets `→ EP-164 done (date)`.
5. **Recorded on this machine** in the completion note: the `antivirus` row as printed (product
   names + enabled/up-to-date flags only), the run time of the probe, and confirmation that the
   seven-path allow list is still in place (owner's word).

> **Optional item 6 (added by EP-7 for the owner to confirm or strike).** `uv run poe roadmap-check
> --strict` has been red since EP-6 by exactly one *warning*: the planning commit `cd67743`, ticked
> in the EP-0 ☑ cell, has no `(EP-0)` in its subject (it predates the convention). EP-7 chose to keep
> the three-hash EP-0 cell rather than break `tests/ep/test_ep06.py`, which pins
> `len(rows[0].hashes) == 3` (a re-plan may not change code). If the owner wants strict green from
> P1 on, this brief — the phase's only code slot before EP-8 — relaxes that pin to `>= 2`, and the
> same session edits the EP-0 row to `☑ 707e9b4 + 795a044` (the planning commit stays cited in prose
> under the P0 table and in EP-0's completion note). Alternatively teach `roadmap_check` a
> `pre-convention` allow list; EP-7 recommends the test relaxation (one line).

## Out of scope

- Reading either product's exclusion list, elevating, or changing any endpoint-security setting —
  owner-only (D-38); the check reports names and states only.
- Malwarebytes-specific APIs / log parsing (`mbamservice.log`) → not built; the Risk 12 note tells
  the owner where to look after a "process killed" symptom.
- The deep GPU check → EP-121; embedding the doctor JSON in run manifests → EP-35 (it inherits the
  new row automatically).

## Verification / acceptance

- `uv run --group dev mwh doctor` on this machine shows an `antivirus` row (expected on 2026-08-17:
  **warn**, naming Malwarebytes next to Windows Defender, both real-time on) and still exits 0;
  `uv run --group dev mwh doctor --json | ConvertFrom-Json` parses with 14 checks; probe wall time
  ≤ 2 s.
- `uv run poe test -m ep_164` green; `uv run poe test -m ep_2` and `-m ep_3` still green;
  `uv run poe check` green; `uv run --group dev mwh verify EP-164` exits 0.
- Completion note records the row, timing and docs touched; DESIGN §15 / D-38 / README updated.
- If optional item 6 is taken: `uv run poe roadmap-check --strict` exits 0 and `mwh verify EP-6`
  stays green.

## Parked → final-roadmap.md

- Elevated `mwh doctor --elevated` mode that reads both products' exclusion lists
  (`Get-MpPreference`, Malwarebytes settings) and verifies the D-38 paths — trigger: a second
  "process killed" incident, or a collaborator machine; hazard: prompting for elevation from a CLI
  used by hooks.
