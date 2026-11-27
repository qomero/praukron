---
name: praukron
description: Maintain or resume a repository's Praukron project chronicle. Use for Praukron init, work, decision, checkpoint, resume, and baseline requests.
---

# Praukron

Everything Praukron owns is inside `.praukron/`. Read
`.praukron/chronicle/README.md`. Run `.praukron/praukron status` for compiled
state and `.praukron/praukron validate` after editing authority. Interpret the
first argument as `init`, `work`, `decide`, `checkpoint`, `resume`, or `baseline`, then
follow the matching `.praukron/commands/praukron-<argument>.md` workflow. Pass
remaining arguments through:
`init` receives the entry mode, `work` the requested task, and `decide` the
decision details. Repeated `init` preserves populated records and resumes work. Run `baseline`
only when the owner explicitly asks for it.

Maintain the chronicle without waiting for an explicit Praukron request. Create
or claim every new task before implementation, give it one `Module:` from
`MODULES.md` (the project follows thesis → phase → module → task, and a task
takes its phase from its module), a `Domain:`
(`execution` or `operations`), and an acceptance contract, and append an ADR to `.praukron/chronicle/ADR/` as soon as a material
decision is made or acted on. Keep the single intent at the exact execution point. A task is
done only when its frozen contract in `ACCEPTANCE.md` has sufficient evidence.
Claim a task before changing code: in Git, setting it `DONE` then records its
changed files and Python symbols; report a skipped `capture`. Record the rest
with `praukron ref add`: planned scope when you know it at the start, symbols
in the reply's `symbolsUnresolved` files, and files only read, verified or
documented. `praukron refs` finds a task's code or a file's tasks, and
`praukron engine map` adds the graph: `ref add` what you read behind an edge it
leaves unresolved, with source `agent` and `evidence`.
Before changing existing code, `praukron engine impact <task>` lists what the
change must be checked against; it is not a verdict, so record your conclusion.
When a choice has no answer in authority, follow the assumption procedure in
`AGENTS.md`: record an assumption before relying on it, stop for the owner on a
reserved choice, and never edit `RESPONSES.md`.

Checkpoint automatically before a handoff, compaction, session ending, or any
known or estimated context, token, time, rate, or quota limit. Without telemetry,
checkpoint after meaningful milestones and before long-running work.
