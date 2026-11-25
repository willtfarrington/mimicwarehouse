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
