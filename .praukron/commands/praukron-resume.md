# /praukron-resume

Recover the project from the chronicle alone. Start with
`.praukron/chronicle/INDEX.md` (run `.praukron/praukron compile` if it is
missing), `.praukron/praukron status`, and `.praukron/praukron context <task>`
for the active or selected task, then read only what they point to among `.praukron/compiled/STATE.md`,
`.praukron/chronicle/PHASES.md`, `.praukron/compiled/TASK_GRAPH.md`, the active
or selected task, its contract in `ACCEPTANCE.md`, its governing ADRs,
`INTENT.md`, `HANDOFF.md`, the task's brief and conventions in
`COORDINATION.md` when the packet carries them, and only the recent journal
entries needed for continuity. State the current goal with its lineage — the thesis, phase, and module the
task belongs to, as `context <task>` reports them — and the exact next
action, then continue. Read implementation code only after the chronicle points
to the work that requires it. Treat the chronicle as current project truth and
the product specification as intended behavior that may require reconciliation.

After merging branches, run `.praukron/praukron compile` (and `graph` and
`dashboard` if they are kept) rather than resolving conflicts in
`.praukron/compiled/`; it is regenerated from authority. `JOURNAL.md` and the
ADR index merge by union, so both sides' entries survive. If `INTENT.md` or
`HANDOFF.md` conflict, resolve `INTENT.md` and `HANDOFF.md` by hand toward the
branch whose work is current: keep at most one intent, and describe the other
branch's unfinished work in the handoff rather than dropping it.
