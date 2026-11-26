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
- **Residual risk.** The stage settings are checked against the pinned pre-commit (4.6.2) only.
  The test helper mirrors `check_card` as of Roadmap Wrangler's EP-58b, so a later change there
  needs a message here (its D-24 addendum is the contract). The preview's public standing
  depends on the tool's cached GitHub list, not on the file.
