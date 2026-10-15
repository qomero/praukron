# /praukron-checkpoint

Prepare the chronicle for another person or agent:

1. Update task status and evidence (`.praukron/praukron task update --input -`),
   criterion state and evidence (`criterion set`), validation strength, and
   governing ADRs. Use the commands instead of editing `TASKS.md` or
   `ACCEPTANCE.md`.
2. Reconcile every owner response that `status` or the packet shows as awaiting
   reconciliation: carry it out in the ADR, contract (through an Acceptance
   Change Request), task, document, or code it concerns, then set the
   assumption's status and `Reconciled by`. Never edit, reorder, or clear an
   entry in `RESPONSES.md`. Record any feedback the owner gave outside it
   verbatim first, with `Via: relayed by <agent> from <channel>`.
3. Run `.praukron/praukron validate`, fix anything it reports, then
   `.praukron/praukron compile && .praukron/praukron graph && .praukron/praukron dashboard`
   to refresh every view in `.praukron/compiled/`.
4. Update `INTENT.md` with the exact stopping point and next action, or clear it
   when the task is complete.
5. Rewrite `HANDOFF.md` with the current position, naming the active task with
   its module and phase, what is true now, what is not done, the open
   assumptions and owner-held blockers, and the next action.
6. Append a journal entry (`.praukron/praukron journal append --input -`)
   containing work done, validation, learning,
   unfinished work, and the exact next action, and any `TRACE.md` events
   not yet recorded for failures, retries, or mutations that matter.

Run this automatically, early enough to complete it, before a handoff,
interruption, compaction, or any known or estimated agent or host context, token,
time, session, rate, or quota limit, including five-hour and seven-day windows.
If the host exposes no meter, run it after meaningful milestones, before a
long-running step, and before ending the session.

After merging branches, run `.praukron/praukron compile` (and `graph` and
`dashboard` if they are kept) rather than resolving conflicts in
`.praukron/compiled/`; it is regenerated from authority. `JOURNAL.md` and the
ADR index merge by union, so both sides' entries survive. If `INTENT.md` or
`HANDOFF.md` conflict, resolve `INTENT.md` and `HANDOFF.md` by hand toward the
branch whose work is current: keep at most one intent, and describe the other
branch's unfinished work in the handoff rather than dropping it.
