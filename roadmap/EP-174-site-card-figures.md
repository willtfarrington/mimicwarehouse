---
ep: 174
title: "Site card: publish the roadmap's figures at each commit"
size: S
model: claude-opus-5-5
effort: max
status: done
---

# EP-174 — Site card: publish the roadmap's figures at each commit

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-4 (Governance enforcement: pre-commit + `mwh guard`), EP-6 (`mwh verify EP-n` + roadmap_check.py) · **Blocks:** —

## Context

The owner's site draws each public project's card (done, total, the next undone rows, a stack
badge) from Roadmap Wrangler's site export, which reads nothing inside a data repository (its
D-24 addenda of its EP-27 and EP-58b), so this repository's card is the one bar still typed by
hand. D-48 (inbox IN-3) settled that the repository publishes its own card to a file outside its
folder, written by a post-commit hook from its own roadmap parser. Roadmap Wrangler defined the
file at its EP-58b and handed the definition here as IN-4; this brief carries it in full (In
scope 1 and 2), so the session needs neither inbox line. With this row the parser
(`verify.parse_roadmap_table`, the one `poe roadmap-check --strict` gates) reads 175 rows and 65
done; after this brief's record commit the file should read 66 of 175 (37 %), next EP-55 and
EP-56, both M. The figures are roadmap counts and titles already public in the tracked README,
not derived from data, so no disclosure check applies (D-48; GOVERNANCE §3). Tier `fixture` in
EP-173's sense: nothing reads the data root or `source material/`, and the tests use synthetic
roadmaps in temp folders, never the owner's real `cards` folder.

**Decided at triage (2026-11-25, owner; D-48 addendum).** (1) The badge reads
`Python · 3.13 · uv`: the text Roadmap Wrangler's stack rule gives for the workspace's
`pyproject.toml`, `.python-version` and `uv.lock` (phillysim's card has the same form), derived
from those files so a pin change follows. (2) This brief was written at IN-4's triage and the
build is its own session. It is the first session after the November gap, so the roadmap
README's pickup (`uv run mwh doctor`, a green `poe check`) runs here, before the code. (3) S ≈ 30
min of active work (D-2 addendum): if it proves larger, stop and split it at pickup.

**Session start (2026-11-25).** Launched from Roadmap Wrangler into VS Code (claude-opus-5-5,
max effort). Read first: GOVERNANCE, this brief, the roadmap README (§ How to use, the P4
table), the workspace README (§ State of the workspace, Quick start, Contributing), D-48 and its
addendum, EP-173's completion note, `owner-tasks.md` (OT-1 open) and `inbox.md` (nothing open);
then Roadmap Wrangler's `check_card` (`crates/core/src/export.rs`) and stack rule
(`crates/core/src/badges/stack.rs`), read only. Pickup: `uv run mwh doctor` 9 pass · 2 warn ·
0 fail · 5 info (`antivirus` by design; `last_backup` 69 days old), AC power mode Best
performance; the baseline `poe check` ran as a background job before the code.

## In scope

1. **The card** (a small module, e.g. `mimicwarehouse/card.py`), from the committed roadmap only:
   `git show HEAD:roadmap/README.md` through `verify._run_git`, parsed by `parse_roadmap_table`
   split into a text parser and its file wrapper (no change for `roadmap_check`). Exactly these
   fields, as Roadmap Wrangler's `check_card` validates them:
   - `as_of`: the local day it is written, `YYYY-MM-DD`, never a later day;
   - `name`: exactly `mimicwarehouse`;
   - `badge`: the derived text, its chips joined with ", " (it may be empty);
   - `done`: the ☑ rows; `total`: every row of the phase tables, above nought;
   - `percent`: done × 100 // total;
   - `next`: the first undone rows in table order, exactly min(2, total − done), each
     `{"id": "EP-n", "title": <the Brief cell's link text>, "size": "S" | "M" | "L" | null}`,
     null for any other Size cell, the id and the title never blank.

   The reader ignores other fields, so add none. With no HEAD or no rows nothing is written
   (exit 0 and one ASCII line).
2. **The write.** The place is `%CRW_HOME%\cards\mimicwarehouse.json` when `CRW_HOME` is set
   (the tool's trial home), else `%LOCALAPPDATA%\clauderoadmapwrangler\cards\mimicwarehouse.json`;
   `--dest <folder>` overrides it for tests. Create `cards` when it is missing. When the tool's
   own folder is missing (not installed, another machine), do nothing, quietly: D-48's no-op,
   which IN-4 leaves open. Rewrite only when the figures (every field but `as_of`) differ from
   the file's, or the file is missing, unreadable or malformed. Write it whole with
   `fsio.atomic_write_text` (a temp sibling, then `os.replace`, with the Windows retries), in
   UTF-8, far under the reader's 64 KiB cap. Nothing else is written in `cards`, and no
   project's files are touched.
3. **The command.** `mwh card` previews the card the committed roadmap gives (ASCII lines, or
   `--json` through `console.emit_json`; CLAUDE.md §3 (e) applies to the badge's `·`).
   `mwh card --write` is the hook's call; `--dest` as above. One line in `cli.py`'s alphabetical
   list (between `canary` and `catalog`), inside `mimicwarehouse.cli`'s import budget
   (`test_ep45`'s `test_import_budget`).
4. **The hook.** In `.pre-commit-config.yaml`: a local hook `mwh-card` with
   `stages: [post-commit]`, `always_run: true` (that stage passes no files),
   `pass_filenames: false` and the entry `uv run --project mimicwarehouse --group dev mwh card
   --write`; top-level `default_install_hook_types: [pre-commit, post-commit]`; and
   `default_stages: [pre-commit]`, so the existing hooks do not also run after each commit.
   Check that with `uv run --group dev pre-commit run --hook-stage post-commit --all-files`,
   which should run the card hook alone. The file's header comment names the install. Then run
   `uv run --group dev pre-commit install` once in this clone; it writes
   `.git/hooks/post-commit` beside the existing hook.
5. **Records.** A D-48 addendum as built (the command, the place with `CRW_HOME`, the no-op, the
   stage settings). In `mimicwarehouse/README.md`: a row for the module in § State of the
   workspace, `card` in Quick start's command list, and the post-commit hook in § Contributing.

## Out of scope

- The first export that carries the card is the owner's (Roadmap Wrangler's OT-46,
  `crw export site --yes`), and wiring it into the site's page is prime's IN-3. Neither is this
  repository's, and no file of another project is edited.
- A `public` field: the tool takes the public standing from the GitHub list its window caches,
  and it ignores extra fields.
- Anything under the data root or `source material/`.

## Verification / acceptance

- `uv run poe test -m ep_174` and `uv run mwh verify EP-174` green on fixture, over synthetic
  roadmaps in a temp git repository whose working-tree README differs from HEAD's (the card
  follows HEAD):
  - every field against `check_card`'s rules, through a test helper that mirrors them: the name,
    a day not after today, a total above nought, done at most the total, the rounded-down
    percent, the number of next rows, no blank id or title, the sizes;
  - zero, one and two undone rows; a Size cell other than S, M or L gives null;
  - a second run with unchanged figures leaves the file's bytes and mtime alone, and a
    difference in `as_of` alone does not rewrite it;
  - a missing `cards` folder is created; a missing tool folder is a no-op with exit 0; a
    malformed file is rewritten;
  - the badge from a synthetic workspace (`.python-version` first, else `requires-python`; `uv`
    only with `uv.lock`).
- `uv run poe roadmap-check --strict` and `uv run poe check` green (the latter ≈ 12 min, as a
  background job with a log).
- Live, after `pre-commit install`: the feat commit's hook writes the real file (65 of 175, next
  EP-174 and EP-55), and `crw export site` (the preview, which writes nothing) prints
  mimicwarehouse's line with "its own card of <day>". After the record commit the file reads 66
  of 175, next EP-55 and EP-56.
- Commits on the owner's word, in the two standard steps (`feat(mimicwarehouse): … (EP-174)`,
  then `docs(roadmap): record EP-174 commit hash`); no push.

> **Completion note (2026-11-26; claude-opus-5-5/max; ≈ 50 min active).** Built as briefed on
> fixture in one session, S as planned (no split): 2026-11-25 22:49–23:27 (the code, the gates
> and both background checks), then 2026-11-26 09:19–09:30 after the owner's answers, against
> the S budget of 30 min. **Pickup:** `mwh doctor` 9 pass · 2 warn · 0 fail · 5 info, exit 0
> (`antivirus` by design; `last_backup` 69.3 days old: decision 1), AC power mode Best
> performance; the baseline `poe check` before the code: ruff clean, 218 files formatted,
> pyright 0 errors, **1,233 passed, 58 deselected, 1 warning** (the crafted torn-ledger test)
> in 852 s. **Built.** (1) `mimicwarehouse/card.py`: `build_card` reads `git show
> HEAD:roadmap/README.md` through `verify._run_git` and parses it with
> `verify.parse_roadmap_text`, the text half of `parse_roadmap_table` (split for it;
> `roadmap_check` unchanged); the badge is Roadmap Wrangler's stack rule (`badges/stack.rs`)
> over the workspace's committed `pyproject.toml`, `.python-version` and lock file (`git
> ls-tree` + `git show` at `HEAD`); exactly `check_card`'s seven fields in its order; `next` is
> the first min(2, total − done) undone rows, size S / M / L or null, titles with `\|`
> unescaped as Roadmap Wrangler's parser does. `plan_write` / `write_card`: `%CRW_HOME%\cards`
> else `%LOCALAPPDATA%\clauderoadmapwrangler\cards` (`--dest` for tests); `skip` without the
> tool's folder (the parent of `cards`), `keep` when the file holds the same figures
> (canonical JSON of every field but `as_of`) under a valid day, `write` when it is missing,
> unreadable, over 64 KiB, malformed or carries a foreign field; `cards` is created, the tool's
> folder never; `fsio.atomic_write_text`, UTF-8 without a byte-order mark. (2) `mwh card
> [--write] [--dest DIR] [--json]`, one line in `cli.py` between `canary` and `catalog`: the
> preview prints ASCII labels (the badge and titles through `console_safe`) and whether a
> write would change the file; `--json` is the outcome plus the card through `emit_json`; no
> `HEAD`, no rows or no tool folder exit 0 with one ASCII line; a write error exits 1. (3)
> `.pre-commit-config.yaml`: the local hook `mwh-card` (the brief's entry, `stages:
> [post-commit]`, `always_run: true`, `pass_filenames: false`),
> `default_install_hook_types: [pre-commit, post-commit]`, `default_stages: [pre-commit]`, the
> header naming the install and the stage check. (4) Records: the D-48 addendum as built and
> its status-index row; the README's State row, Quick start (`card`, the heading as of EP-174)
> and Contributing (the install line, the stage check, an after-each-commit paragraph); the
> `cli.py` docstring. **Routine choices** (the session's call, not objected to): the badge read
> from `HEAD` like the figures, so the whole card follows the commit; `card` added to
> `DIAGNOSTIC_COMMANDS`, keeping `cli.py`'s rule "diagnostic iff it never touches the data
> root" true (the behaviour is the same: `card` reads no settings, and a test shows
> `--data-root G:\…` cannot block it); the outcome-shaped `--json`; IN-4's "without its
> decisions" title trim not built (no row title carries a decision group, and the brief names
> the link text). **Churn-rule edits:** `test_ep04` (the local hook-id pin gains `mwh-card`),
> `test_ep167` (the diagnostic set gains `card`); both briefs re-verified below. **Gates:**
> `poe test -m ep_174` 43 passed (26 s); `mwh verify EP-174` 43 passed; `mwh verify` EP-2 27 ·
> EP-4 45 · EP-6 46 · EP-167 35, every exit 0; `poe roadmap-check --strict` OK, 175 rows, 175
> briefs, 65 done, 0 errors, 0 warnings (the new front matter leaves the header check alone);
> `mwh guard` clean over the 10 touched files; the final `poe check` exit 0: ruff clean, 220
> files formatted, pyright 0 errors, **1,276 passed, 58 deselected, 1 warning** in 869 s.
> **Live:** `pre-commit install` wrote `.git\hooks\pre-commit` and `.git\hooks\post-commit`;
> `pre-commit run --hook-stage post-commit --all-files` ran `mwh card` alone (Passed), which
> created `cards` and wrote the real file: 65 of 175 (37 %), next EP-174 (S) and EP-55 (M),
> badge `Python · 3.13 · uv`, `as_of` 2026-11-25; `crw export site` (the preview) printed
> "mimicwarehouse 65 of 175 (37 %), next EP-174, EP-55 · Python · 3.13 · uv · its own card of
> 2026-11-25"; a second `mwh card --write` left the file unchanged. The checks after the
> commits are in the residual-risk paragraph. **Owner decisions** (one round, the recommended
> option each time): (1) *the 69-day-old backup*: owner task **OT-2** added (the owner runs
> `mwh backup run`); rejected: the session running it now, an FYI only. (2) *a note to another
> project*: one block to **Roadmap Wrangler**, below; rejected: notes to it and prime, no
> note. *Commits:* none; per the launch's rule 5 the session stopped before committing, and
> the two standard steps are the owner's.

> **Handed on (to clauderoadmapwrangler).** For your IN-4 (EP-58b) and OT-46: mimicwarehouse's
> EP-174 (its D-48) built the card writer to IN-4's definition. `mwh card --write`, run by a
> pre-commit post-commit hook after every commit, writes
> `%LOCALAPPDATA%\clauderoadmapwrangler\cards\mimicwarehouse.json` (`CRW_HOME\cards\` in a
> trial) whenever the figures change, from the committed roadmap only, with exactly the seven
> fields `check_card` reads, and does nothing when your folder is missing. The file exists: the
> session's stage check wrote it on 2026-11-25 (65 of 175, next EP-174 and EP-55, badge
> `Python · 3.13 · uv`), and `crw export site` (the preview) printed mimicwarehouse's line with
> "its own card of 2026-11-25". OT-46 can run once the owner has committed EP-174; its record
> commit rewrites the file to 66 of 175, next EP-55 and EP-56. The tests mirror `check_card` as
> of your EP-58b, so a later change to the reader's rules needs a message back here.
- **Residual risk.** The stage settings are checked against the pinned pre-commit (4.6.2) only.
  The test helper mirrors `check_card` as of Roadmap Wrangler's EP-58b, so a later change there
  needs a message here (its D-24 addendum is the contract). The preview's public standing
  depends on the tool's cached GitHub list, not on the file. *(Added at completion.)* The
  checks after the commits wait for the owner's two commits, since the session stopped before
  committing: the feat commit's hook should find the figures unchanged and keep the file the
  stage check wrote (65 of 175, `as_of` 2026-11-25), and the record commit's should rewrite it
  to 66 of 175, next EP-55 and EP-56. Another clone or machine gets the post-commit hook only
  after its own `pre-commit install`.
