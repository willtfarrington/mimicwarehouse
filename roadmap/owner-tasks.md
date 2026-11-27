# Owner tasks — what only the owner can do

One line per task (clauderoadmapwrangler D-10): `- [ ] OT-n (EP-k, date) text`, ending
`[before EP-m]` only when EP-m builds on it. Sessions append at completion; the owner ticks a task
in Roadmap Wrangler or here.

- [ ] OT-1 (2026-11-25) Renew the CITI "Data or Specimens Only Research" training by 2027-01-29 (raised at the house-style sweep, IN-2): a credential or CITI lapse stops all data use until it is renewed (GOVERNANCE §13). Then update GOVERNANCE §1's owner-maintained record (the CITI completion date and the next renewal due), or ask a session to.
- [ ] OT-2 (EP-174, 2026-11-26) Run the backup of the non-reproducible state (the `runs\` ledgers with the audit ledger, frozen protocols, run manifests; GOVERNANCE §11): the newest was taken on 2026-09-17, 69 days old at EP-174's pickup, and `mwh doctor` warns past 7 days. In your own shell: `cd C:\Users\willi\Documents\DATA\mimicwarehouse\mimicwarehouse`, then `uv run mwh backup run` (the target is `MWH_BACKUP_TARGET=C:\mimicbackup` from `.env`; it prints the backup's path and byte totals only), then `uv run mwh doctor`: the `last_backup` row should read pass.
