# EP-159 — Demo mode for the app

**Size:** S · **Tier:** demo · **Core/Stretch:** core · **Depends on:** EP-158 (Bootstrap `mwh init` + cloner smoke test on demo tier), EP-57 (App shell A (Streamlit multipage)) · **Blocks:** EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

DESIGN §4 separates the *demo tier* (the ODbL MIMIC-IV Demo 2.2 + ED Demo loaded as a tier, EP-22)
from *demo mode* = the app launched with `--tier demo` and export/row-view features enabled because
that data is redistributable (D-12). This brief builds demo mode on the app shell (EP-57), the row-view
gate and app-side small-cell enforcement (EP-58), the export primitives (EP-59) and `mwh init --tier
demo` (EP-158). Governance stays exact: relaxations apply only to in-app viewing and local exports;
nothing about what may be committed changes (GOVERNANCE §3 — no identifier tables in git regardless of
tier, `mwh guard` still enforces), and screenshots of row-level views are permitted only from
demo/fixture (§6), which is precisely what demo mode is for. Demo caveats: v2.2 schema mapped to 3.1,
100 subjects (small cohorts, empty panels), no note demo (the Text page shows an explanatory empty
state).

## Scope sketch (refine at re-plan)

1. **Shell flag + guard** (`app/` shell components from EP-57/58; tier→path resolution in `config.py`) —
   `uv run --group ui mwh app --tier demo` sets `demo_mode`; refuse with a clear error if the resolved
   catalog is not `demo.duckdb`; tier switcher locked; persistent banner "Demo mode — MIMIC-IV Clinical
   Database Demo 2.2 + ED Demo (ODbL 1.0), 100 subjects — not credentialed data"; page-title suffix.
2. **Unlocks** — row-view toggle usable without the owner-role prompt (still writes the audit line with
   `tier=demo`); EP-59 exports allow row-level / unsuppressed output to the local export folder with an
   ODbL attribution footer; small-cell warn badges remain (informative) but do not block. On every other
   tier behaviour is unchanged (D-32, D-33): row view gated, exports suppressed at k = 11.
3. **Page walk on demo** — drive every page with the EP-60 page-walk tooling against the demo tier; each
   page renders; pages that get empty results on 100 subjects show a graceful empty state; record any
   page that errors and fix or hand off in the completion note.
4. **Tests `tests/ep/test_ep159.py`** (`@pytest.mark.ep_159`, fixture) — demo mode refused for
   fixture/dev/full catalog paths; banner text present in shell state; export in demo mode carries the
   attribution footer; export on a non-demo tier still suppressed at k = 11 (regression guard).
5. **`docs/demo-mode.md`** — launch command, what is unlocked and why (ODbL), what does *not* change (git
   rules, guard), screenshot conventions (GOVERNANCE §6). Consumed by EP-160/162.

## Out of scope

- `mwh init` / `mwh demo fetch` → EP-158.
- Showcase screenshots and the demo script → EP-162 (this brief takes one screenshot to prove the path).
- Docs site → EP-160.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_159` green on fixture; `uv run --group dev mwh verify EP-159` green.
- `uv run --group ui mwh app --tier demo` shows the banner and the unlocks; `--tier dev` behaviour is
  unchanged (row view gated, exports suppressed) — checked by hand and by the regression test.
- Page walk on the demo tier: every page renders; empty-state pages listed in the completion note; one
  page timing recorded (latency target n/a at 100 subjects).
- One demo-mode screenshot at `docs/screenshots/demo-mode-banner.png` with a `.disclosure.json` sidecar
  from `uv run --group dev mwh disclose check`.
