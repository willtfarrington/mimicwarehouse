# Inbox — messages from other projects

Read right after the roadmap at the start of a session. Triage rule (clauderoadmapwrangler D-13):
for each open message, roll it into this session if it fits the brief and is at most S-sized;
otherwise add it to the brief notes of a named later EP or propose a new row in the right phase,
confirming the choice with the owner; or decline with a reason. Write the disposition on the
message's line and tick its box. One message per line,
`- [ ] IN-n (from <project> EP-k, date) text`, delivered by the tool.

- [x] IN-1 (from the environment repository EP-8, 2026-11-23) The user-level house style
  (`~\.claude\rules\house-style.md`) was rewritten on 2026-11-23 from the owner's conventions review
  (the environment repository's EP-8, its D-6 addendum): rules 1–7 keep their numbers, rules 8 (the roadmap) and 9 (the
  owner's records) are new, and its preamble says rule files carry rules, not context or history.
  For this project's next re-plan, in its `CLAUDE.md`: (1) three history clauses go: § 1, line 13,
  "(from EP-166)" after § "State of the workspace"; § 2, line 29, "From EP-30 on, `mwh
  sql`/`safe_query` is the only way" becomes "`mwh sql`/`safe_query` is the only way"; § 5, line
  119, "— before EP-43 nothing else derived from real data enters `docs/` or git"; (2) § 4, line 99,
  writes the completion note's heading as `Completion note (date)`, which house style 2 now writes
  `Completion note (date; model/effort; duration)`, as crw's opener does. Where a rule there repeats
  the house style it may go then too (D-6's trim); where it differs, the project's rule wins as the
  more specific. **Disposition (2026-11-25):** rolled into the house-style sweep (IN-2) on the
  owner's word: `CLAUDE.md` lost the three history clauses (§1, §2, §5) and §4 writes the
  heading `Completion note (date; model/effort; duration)`; the optional trim of rules that
  repeat the house style was not taken (they are this repository's license-obligation rules,
  and `tests/ep/test_ep165.py` pins several verbatim). The sender's name was reworded before
  the commit: Roadmap Wrangler flagged it as a private word, and this repository is public.
- [x] IN-2 (from the owner, 2026-11-25) Sweep (Roadmap Wrangler EP-55): in one session I open here,
  /roadmap-wrangler:status first, settle with me: (1) State: active; paused, with a reason and a
  pickup note; maintenance, and what that means here; done; or abandoned and archived. I set it on
  its row in Roadmap Wrangler. (2) House style: its tables read: /roadmap-wrangler:formalise (or
  Formalise... on its row) adds the rule block at the end of CLAUDE.md, its lines kept, and writes
  owner-tasks.md and the brief template. (3) Sizes: a brief is S (about 30 min of active work) or M
  (about 45), no L, an open L split at pickup, as the rule block's Sizes bullet says. (4) Manual:
  whether it works well enough to owe one (Markdown built to PDF, a convention still to come); note
  my answer. Record the outcome (a dated note in its roadmap where it has one) and tick this.
  **Disposition (2026-11-25):** settled with the owner in this session: (1) state **active**,
  development resuming at EP-55 (the owner sets it on the row); (2) formalised with
  `crw formalise` (the rule block at the end of `CLAUDE.md`, `owner-tasks.md`, `_TEMPLATE.md`;
  `test_ep165`'s size pin now counts `CLAUDE.md` without the block); (3) sizes S ≈ 30 / M ≈ 45
  min of active work, no L (D-2 addendum; both L rows were already done); (4) no manual owed
  yet, asked again at Re-plan P4 (EP-74). Recorded in the roadmap README's dated note; OT-1
  (the CITI renewal) added.
- [x] IN-3 (from the owner, 2026-11-25) From prime's session of 2026-11-25 (its IN-1): the site's
  project cards now take their figures from Roadmap Wrangler's site export, which leaves a data
  repository out unread, so mimicwarehouse's card is the one bar still typed by hand (65 of 174,
  November 2026). The owner wants it drawn too without breaking that rule: settle with the owner how
  mimicwarehouse publishes its own card's figures (done, total, and the next one or two undone rows
  with id, title and size, all already in its public roadmap) to a file outside this folder at each
  record commit, in the form and place Roadmap Wrangler defines for its export (asked of it the same
  day), so that nothing ever reads inside this repository. prime's IN-3 wires the card once the
  export carries it.
  **Disposition (2026-11-25): settled** with the owner, every answer the recommended option
  (D-48). The build waits for Roadmap Wrangler's definition of the file's form and place (its
  IN-19, open): when its answer reaches this inbox, the session that triages it adds EP-174 (S,
  core) at the head of the open P4 rows and builds it, so no row sits blocked meanwhile and the
  total stays 174. Trigger: a local hook at pre-commit's post-commit stage runs an `mwh` command
  after every commit and rewrites the file only when the figures change, computed by this
  repository's own parser from the committed `roadmap/README.md`, so nothing of Roadmap
  Wrangler's reads inside. A note went to Roadmap Wrangler (`crw send`, its IN-20): this side
  settled, the place outside every project folder, every card field carried (the stack badge's
  text included), the definition handed back here. The bar stays hand-typed until then.
- [x] IN-4 (from clauderoadmapwrangler EP-58b, 2026-11-25) For your IN-3 and D-48: the
  published-card file, defined with the owner in clauderoadmapwrangler's EP-58b (2026-11-25, its
  D-24 addendum of that day), for your EP-174 (S) to build the writer. (1) Place:
  `%LOCALAPPDATA%\clauderoadmapwrangler\cards\mimicwarehouse.json`, named by your registered name;
  create the `cards` folder when it is missing; the hook writes nothing else there and edits no
  project's files. (2) Form: one JSON object in UTF-8 (a byte-order mark allowed, at most 64 KiB)
  with `as_of`, the local day it is written as `YYYY-MM-DD`, never a later day; `name`, exactly
  `mimicwarehouse`; `badge`, your stack badge's text, its chips joined with ", " (it may be empty);
  `done` and `total`, the checked rows and all the rows of your committed roadmap's phase tables,
  the total above nought; `percent`, done × 100 / total rounded down; `next`, the first undone rows
  in table order, exactly as many as the undone rows allow up to two, each `{"id": "EP-n", "title":
  the Brief cell without its link markup and its decisions, "size": "S", "M", "L" or null}`, the id
  and the title not blank. Other fields are ignored; a missing one makes the file malformed. (3)
  Write it whole (a temporary file beside it, then a rename) whenever the figures change; there is
  no age limit, since the card carries its own day. (4) Its public standing is not the file's: crw
  carries the card only when the GitHub list its window caches names a public repository called
  mimicwarehouse (by the registered name or the folder's), and otherwise does not open the file. (5)
  To check a file: `crw export site` (a preview, it writes nothing) prints mimicwarehouse's line
  with "its own card of <day>", or why it is left out ("no card file", "not public (…)", or "its
  card file is malformed: …" naming what failed). The first export that carries the card is the
  owner's (crw's OT-46); prime's IN-3 then wires it into the page.
  **Disposition (2026-11-25): rolled into EP-174's brief** (`EP-174-site-card-figures.md`, S,
  core), its row added at the head of the open P4 rows as D-48 placed it. The brief carries the
  place, the form, the write and the check above, so its session needs no inbox line. The
  owner's choices at this triage (D-48 addendum, the recommended option each time): the brief
  written now and the build in its own session; the badge `Python · 3.13 · uv`, the text Roadmap
  Wrangler's stack rule gives for the workspace's files, derived from them so a pin change
  follows.
- [x] IN-5 (from the owner, 2026-11-27) Screenshots for the site (from prime): when the row that
  completes your v1, or your roadmap's equivalent (the phase or milestone it calls the first
  complete version), is done, make two screenshots on hupsim's precedent: n=2, landscape PNG,
  2698x1429 px or larger at 1.888:1 (so prime's 1280x678 thumbnails pair like hupsim's), two
  distinct views a reader would want, each with a few-word caption and a one-sentence alt text. Show
  the real thing if nothing on screen has a provenance, licence or privacy issue; otherwise a mock
  or smoke build on synthetic data, never patient, credentialed or personal records or a private
  project's name. Keep the files on this machine, untracked in a folder you name, never pushed, and
  hand on to prime with paths, captions and alt text: the owner decides there whether they go on the
  site (for now, public cards only). Park this in your v1-closing row's brief notes; the
  real-or-mock call goes to the owner then.
  **Disposition (2026-11-27, at EP-56's pickup): parked** as a brief note on **EP-163** (final
  `final-roadmap.md` compilation + release v1.0.0 + final retro, the v1-closing row), the
  owner's choice (recommended) over EP-162's note or a new row: a `**Brief notes.**` block
  under the P11 table of `roadmap/README.md` carries the request in full, which EP-163's
  launch prompt carries verbatim; the real-or-mock call goes to the owner then.
