# /praukron-work

Run `.praukron/praukron status` for the current position, or read
`.praukron/chronicle/README.md` and the compiled views if the tool is
unavailable. Select the requested task, or one ready task if none was named.
Use `.praukron/praukron context <task>` rather than re-deriving its state by
hand; its `lineage` names the task's thesis, phase, and module. If the
requested work has no task yet, create it before implementation without
waiting for a Praukron command, give it one `Module:` from `MODULES.md` (its
phase comes from the module; author a module first if none fits) and a
`Domain:` (`execution` or `operations`), and write its acceptance contract:
`.praukron/praukron task add --input -` writes the task and its contract in one
validated step (`--schema` prints the payload).
Never move a task to another module or a module to another phase as a side
effect of implementation; that is a decision. Read that
task, its contract, and its governing ADRs, then inspect only the specification
and code needed for the task. When the packet carries a `brief` and
`conventions`, follow them as the coordinator's instructions for this task
(branch, base, reviewers, review round); the contract, invariants and ADRs win
where they differ. A coordinator records them in `COORDINATION.md` before
starting an agent, never only in its own session (ADR-075).

Mark it `WIP`, record the owner and claim date (`.praukron/praukron task update
--input -`, which claims and dates it), and set `INTENT.md` to that one task
before implementation. Its contract freezes at that moment; change it only
through an accepted Acceptance Change Request (`acr add` records one for the
owner). When you create or claim it, record with `.praukron/praukron ref add
--input -` the files and symbols you already know it will touch
(`"scope":"planned"`). Keep intent at the exact execution point, especially
before a long-running step. When a material choice is made, accepted, or acted
on, append its ADR immediately and link affected tasks. As truth changes, update the
task (`task update`), its criterion states and evidence (`criterion set`), the
compiled views, the handoff, and the journal (`journal append`) rather than
postponing record keeping. Use those commands instead of editing `TASKS.md`,
`ACCEPTANCE.md` or `JOURNAL.md`; a refused write names the rule and the next
valid step. Append an event to `TRACE.md`
for a tool call, command, mutation, failure, or retry that could later explain
an execution failure, a blocked gate, or a regression, naming the task it came
from and what it affects. Summarize; never record secrets, credentials, or
private reasoning.

The files and Python symbols a task changes need nothing from you: in a Git
repository, the claim notes the commit and the write that sets the task `DONE`
records them as observed references. Read that reply's `capture` and say in
your report when it was skipped, and why. Then `ref add` what it cannot know,
as observed references: the symbols you changed in each file it lists in
`symbolsUnresolved` (files that are not Python), and any file the task reads,
verifies or documents without changing it. To find a task's code, or the tasks
of a file or symbol, run `.praukron/praukron refs <task|path|path#symbol>` or
read `references` in `context <task>` and `retrieve <task>`;
`.praukron/praukron engine map <task>` adds the graph where an index exists.
An edge it leaves unresolved on your task's symbol is unknown, not absent: read
the code and `ref add` what you find with `"source":"agent"` and `evidence`.
Never edit `CODE.md` by hand; `ref set` changes or withdraws a reference.

When a choice has no answer in authority, follow the assumption procedure in
`AGENTS.md` and `docs/SPEC.md` §2.4: read the packet's `policy`; resolve it
from authority, assume it, or stop for the owner. Record an assumption in
`ASSUMPTIONS.md` before relying on it and cite its id where it is applied and
in any criterion evidence that rests on it; mark a permission choice with
`Permissions:` and `Impact: HIGH`. A reserved choice becomes an
`Authority: owner` task the work depends on; notify the owner through the host
when the policy says `Notify: host`, record the time and channel on that task,
and continue other work. Before building on an assumption, reconcile any owner
response the packet shows as awaiting reconciliation.
