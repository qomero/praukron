<!-- project-praukron:start -->

# Praukron

Everything Praukron owns is inside `.praukron/`. `.praukron/chronicle/` is this
repository's project chronicle, its canonical memory. `.praukron/compiled/` is
compiled output: read it for convenience, never edit it, and never treat it as
authority.

## Project context: read the index first

Before reading broad repository context or the rest of `.praukron/chronicle/`:

1. Read `.praukron/chronicle/INDEX.md`. It is generated from the chronicle and
   says what matters now and where each record lives. If it is missing, run
   `.praukron/praukron compile`.
2. Identify your task: the one you were given, or one the index names as in
   flight or ready. Run `.praukron/praukron context` without a task only when
   the index is not enough to choose; skip it when a task was given. Then run
   `.praukron/praukron context <task>` for its lineage (thesis → phase → module),
   claim, dependencies, blockers, acceptance, invariants, decisions, evidence,
   implementation anchors, and the exact `file#anchor` records to read. Do not start a task merely because
   unrelated work is visible in the repository.
3. Read only those records. `.praukron/praukron retrieve "<question or task>"`
   returns exactly them, each labelled with its source. When the packet
   includes the handoff, do not read `HANDOFF.md` again.
4. Do not load the whole chronicle by default. Read further Markdown only to
   resolve an ambiguity or when the packet is not enough.
5. Explore the code only after the project context is resolved. Where
   CodeGraph is installed, `.praukron/praukron retrieve <task> --code` adds code
   structure after the records, never before them.

Use the narrowest projection that answers the current question, and expand
progressively: index, then the task packet, then `retrieve`, then the exact
record, then the code. Never preload the chronicle in full, `TASK_GRAPH.md`, or
`project.json`, and never re-read what the current packet already holds.

`INDEX.md`, `.praukron/compiled/`, and `context` output are derived maps, not
authority; never edit them. When one disagrees with a record, the record wins:
report the inconsistency rather than reconciling it silently. A packet's
`problems` lists references that do not resolve.
`.praukron/chronicle/README.md` explains how the records work.

The repository ships a deterministic tool. Use it rather than re-deriving state
by reading files:

```sh
.praukron/praukron status          # phase, progress, ready, blocked, gates, next
.praukron/praukron explain T-123   # one task: deps, criteria, blockers, evidence
.praukron/praukron context         # orientation: phase, in flight, ready, blocked, path
.praukron/praukron context T-123   # the minimal packet needed to start that task
.praukron/praukron retrieve "why is P1 blocked?"   # only the records a question needs
.praukron/praukron validate        # check authority before and after editing it
.praukron/praukron compile         # refresh compiled/ after changing chronicle/
.praukron/praukron dashboard       # a browsable page of the same state
```

Run `validate` after editing authority. Before finishing, refresh everything in
`.praukron/compiled/`: `compile` does not redraw the graphs or the dashboard, so
run `compile && graph && dashboard`; a stale dashboard shows people a state the
chronicle no longer holds. The tool writes only: the generated `INDEX.md`;
`respond` (the owner's responses); `archive plan|apply|restore <phase>` (owner
only: moves a COMPLETE phase's, or P-NONE's, eligible closed records, verbatim,
into `chronicle/archive/<phase>/`, where they stay authority and the write
commands cannot change them; `RESPONSES.md` and ADRs never move); and five validated, atomic structured writes (ADR-068). Use these
instead of editing `TASKS.md`, `ACCEPTANCE.md` or `JOURNAL.md`:

```sh
.praukron/praukron journal append --input -   # a dated entry; repeats are refused
.praukron/praukron criterion set --input -    # a criterion's state and evidence
.praukron/praukron task add --input -         # a task and its new contract
.praukron/praukron contract add --input -     # the contract of a TODO task
.praukron/praukron task update --input -      # status, owner, claimed, evidence
```

Each takes a JSON payload (`--schema` prints it, `--dry-run` previews), prints
one JSON line, and exits nonzero when refused. Updates need the record's revision
(`expectRevision`; `expectTaskRevision` for `contract add`); a stale one is
refused with the current revision to retry with (send 16 zeros to learn it).
`criterion set` records state and evidence on any task's criteria, `WIP`
included. The commands never set `HUMAN_VERIFIED`, close an `Authority: owner`
task, change a criterion's text or class, add a contract once the task is `WIP`
or `DONE` (raise an Acceptance Change Request), or touch `archive/`.

For any other direct edit of a chronicle file, do not read the whole file. Read
only the record you change: locate its `## <id>` heading (a search for the id),
read that section with a ranged read, and edit it. When appending a new record,
read only the end of the file. `.praukron/praukron retrieve <id>` returns that
record, and for a task its contract and governing decisions, each with its
source, when you only need to read them.

Recognize these workflows:

- `/praukron-init [new|existing]` → `.praukron/commands/praukron-init.md`
- `/praukron-work [task]` → `.praukron/commands/praukron-work.md`
- `/praukron-decide` → `.praukron/commands/praukron-decide.md`
- `/praukron-checkpoint` → `.praukron/commands/praukron-checkpoint.md`
- `/praukron-resume` → `.praukron/commands/praukron-resume.md`
- `/praukron-baseline` → `.praukron/commands/praukron-baseline.md` (only when the owner asks)

Maintain the chronicle automatically; do not wait for a Praukron command. Before
starting newly requested work, create or claim its task, give it one `Module:`
from `MODULES.md` (its phase comes from the module; a module belongs to a phase
in `PHASES.md` or to `P-NONE`) and a `Domain:` of `execution` or `operations`,
write its acceptance contract in
`ACCEPTANCE.md`, and set the single `INTENT.md` entry. When a material project
choice is made, accepted, or acted on, append its ADR to `.praukron/chronicle/ADR/`
immediately and link affected tasks. Supersede decisions instead of overwriting
them. Keep the compiled views synchronized.

## Assumptions and the owner

When a choice has no answer, search authority first: ADRs, contracts, the task
packet, the journal, documentation, and code. Research externally when that is
useful and permitted; researched is not confirmed. Then exactly one of:

- authority resolves it: follow it;
- the packet's `policy` reserves it, or it is destructive, irreversible, or
  contradicts an accepted ADR or a frozen criterion: create or name a task with
  `Authority: owner` that the work depends on, notify the owner through your
  host when the policy says `Notify: host` and record when and how on that
  task, and continue other work;
- otherwise assume: record it in `ASSUMPTIONS.md` before relying on it, cite
  its id where it is applied and in any evidence that rests on it, and mark a
  permission choice with `Permissions:` and `Impact: HIGH`.

Never present an `OPEN` assumption as decided. `RESPONSES.md` is the owner's
words: act on every response the packet shows as awaiting reconciliation, and
never edit or clear one. Feedback the owner gives anywhere else goes into
`RESPONSES.md` verbatim, `Via: relayed by <you> from <channel>`, before you act.

## Completion

A task is not done because you say it is done. It is done when every mandatory
criterion of its contract in `ACCEPTANCE.md` holds and its evidence is recorded.

A contract freezes when its task becomes `WIP`. If a criterion is wrong, raise
an Acceptance Change Request; never weaken, reinterpret, or quietly drop one.

## Builder

You receive intent, phase, task, acceptance contract, inherited invariants,
governing ADRs, and current handoff. You may implement, test, produce evidence,
raise an Acceptance Change Request, and update the handoff.

## Reviewer

You receive the same contract plus the implementation and its evidence. Classify
every finding. These block completion:

```text
ACCEPTANCE_FAILURE  INVARIANT_VIOLATION  REGRESSION  MISSING_EVIDENCE
```

These are recorded and do not block completion:

```text
RISK  MAINTAINABILITY  ARCHITECTURE_PREFERENCE  STYLE  FUTURE_IMPROVEMENT
```

Your preference is not an acceptance criterion.

## Disagreement

Agents will disagree. Resolve against project authority in this order, highest
first:

```text
1. Product and domain authority
2. The explicit acceptance contract
3. Invariants
4. Accepted decisions in .praukron/chronicle/ADR/
5. Reproducible tests and evidence
6. Existing code convention
7. Reviewer preference
```

The goal is not agreement. The goal is that a disagreement is decidable.

## Checkpoint

Checkpoint early enough to finish writing whenever the agent or host approaches
any context, token, time, session, rate, or quota limit, including five-hour and
seven-day windows, or before compaction or handoff. If no meter is exposed,
checkpoint after each meaningful milestone, before a long-running step, and
before ending. Preserve the active task, exact stopping point, unfinished work,
and next action.

<!-- project-praukron:end -->
