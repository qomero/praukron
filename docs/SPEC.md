# Praukron specification

## 1. Purpose

Praukron is short for Project Chronicle. It is a working agreement that gives
people and AI a common language for understanding a project: why it exists,
the historical decisions that shaped it, its present state, and its future work.
A newcomer should be able to understand that story before reading implementation
code.

The task graph answers **what can happen next**. Decision lineage answers **why
the project has its current shape**. Together they form the spine of the project
chronicle.

The same record supports discussion, advice, review, and continuity among
people and agents. Session handoff is one use of this shared understanding.

Praukron consists of authored Markdown records, agent instructions, reusable
command prompts, and a deterministic compiler over those records. The compiler
reads `.praukron/chronicle/` and writes only `.praukron/compiled/` and the
generated `INDEX.md`; it uses the Python standard library and needs no network
or model provider. The one command that writes a record is `praukron respond`,
and it only appends the owner's words to `RESPONSES.md`, validated first and
undone if it introduces an error (ADR-055). `praukron archive <phase>`, run
only at the owner's request, moves a COMPLETE phase's closed tasks and
contracts verbatim into `archive/<phase>/`; they remain authority, read with
the working files, and the move is undone unless the records read back
identical (ADR-066). See ADR-013, ADR-016, ADR-017,
ADR-024, and `docs/PRODUCT-THESIS.md`.

Phase specifications are internal working documents and are not published
(ADR-026). Decisions taken against them cite them by name; the record of what
was decided, what it had to satisfy and what evidence was produced lives in
this repository's own chronicle.

## 2. Chronicle

Everything Praukron owns lives in one `.praukron/` directory (ADR-024). Within
it, every participating repository has one authored `chronicle/` directory and
one compiled `compiled/` directory. Only the first is authoritative.

| File in `.praukron/chronicle/` | Role |
|---|---|
| `THESIS.md` | The product thesis every phase, module, and task descends from, with an optional source document |
| `PHASES.md` | Phase outcome, entry, exit, exit authority, status, and gates; optionally the project's own name |
| `MODULES.md` | Modules: each a unit of work with an outcome, inside exactly one phase or `P-NONE` |
| `TASKS.md` | Canonical tasks, module, domain, dependencies, ownership, contract reference, and evidence |
| `ACCEPTANCE.md` | Completion contracts, evidence classes, and change requests |
| `ADR/` | Append-only decision history and supersession lineage, one file per ADR |
| `INTENT.md` | Zero or one task currently being attempted |
| `HANDOFF.md` | Current implementation continuity, overwritten each checkpoint |
| `JOURNAL.md` | Append-only session diary and handoff history |
| `TRACE.md` | Append-only operational events: tool calls, commands, mini-actions, mutations, failures, retries, validation runs |
| `TECH_DEBT.md` | Known technical debt: what each compromise costs, its trigger, its exit condition, and its lineage |
| `INDEX.md` | **Generated.** The routing layer an agent reads first: what matters now and where each record lives |

Project management follows one fixed hierarchy: **thesis → phase → module →
task** (ADR-035). A task never owns a phase: it names one module, and its phase
is derived from that module. Phase-independent work belongs to a module in
`P-NONE`. A legacy task that names `Phase:` directly still compiles, with no
module and a conversion warning, and nothing invents a module for it (ADR-036).

| File in `.praukron/compiled/` | Role |
|---|---|
| `project.json` | The whole compiled project, with provenance for every object |
| `STATE.md` | Short snapshot of the project now |
| `TASK_GRAPH.md` | Current dependency and eligibility view derived from tasks |
| `*.mmd` | Mermaid views: task graph, critical path, phases, gates, timelines |
| `dashboard.html` | The same state as one browsable page |

Every compiled file is disposable. Deleting the directory and compiling again
reproduces all of them byte for byte.

A project may name itself with a `Project:` line above the first heading of
`PHASES.md`. Without one the directory name is used, which is what every
chronicle written before the field did. Naming the project in authority is what
makes compiled output identical in two differently named checkouts (ADR-030).

The product specification records intended behavior. The chronicle records
current project truth. When they disagree, the agent surfaces and reconciles
the difference.

### 2.1 Execution and operations

Every task belongs to exactly one of two domains (ADR-045).

**Project execution** is work that directly advances the project toward its
intended deliverables and gates: phase work, product tasks, research a product
decision needs, acceptance, the critical path, gates, and phase exit
authorities. It answers *what must happen for the project itself to advance?*

**Project operations** is supporting activity that maintains, operates,
inspects, debugs, or changes the environment execution happens in: upgrading
Praukron, CI and tooling, repository housekeeping, dashboard refreshes, internal
automation, migrations unrelated to product capability. It answers *what
happened around execution, and could any of it explain the current state?*

A task declares its domain with `- Domain: execution` or `- Domain: operations`.
Without one, structure decides only in execution's favour: a task that belongs
to a phase, is a phase's exit authority, or has a contract a gate is verified
by is execution. Anything else resolves to execution and validation reports it
as `ambiguous-domain` until someone declares it. Nothing is ever inferred from a
title or an identifier. `praukron domains` lists how every task was resolved.

Phases, gates, the critical path, completion, acceptance and validation figures,
work in flight, and the main blocker are all computed from execution. An
operations task whose module sits in a phase is associated with it but never
counted in it.

**Operations can block execution without becoming execution.** When an
execution task depends on an operations task, or a gate is verified by an
operations task's contract, the operations task is reported as that work's
external blocker and keeps its domain. It never becomes a critical-path node.

A few consequences are deliberate:

- Not every task that matters belongs on the critical path.
- Not every active task represents project progress.
- Not every blocked task is a blocker: the main blocker is a dependency that
  important execution work waits on, not a task that happens to be blocked.
- Not every operational action deserves to become a task.
- Tool calls and mini-actions may be essential evidence even when they do not
  count toward progress.

### 2.2 Technical debt

`TECH_DEBT.md` records `## TD-<id>` liabilities the project knowingly carries
(ADR-046): `Status` (`OPEN`, `ACCEPTED`, `SCHEDULED`, `RESOLVED`,
`INVALIDATED`), `Introduced by` (tasks and ADRs), `Areas`, `Debt`, `Reason`,
`Interest`, `Trigger`, `Trigger state` (`NOT_REACHED`, `APPROACHING`,
`REACHED`), `Exit condition`, `Evidence`, `Linked tasks`, and `Resolution`.

A debt is not a task. It may exist with no repayment work at all; when its
trigger is reached, the work that repays it is an ordinary task, execution or
operations, and the debt links to it. The record itself has no domain.
`SCHEDULED` needs a linked task. `RESOLVED` and `INVALIDATED` need a recorded
resolution, and finishing a linked task never resolves a debt on its own:
verifying the exit condition does. The lineage — decision, debt, trigger,
task, resolution — is carried into compiled state and every task's context.

### 2.3 Operational trace

`TRACE.md` holds structured operational events, `## EV-<id>: <summary>`, with a
required `Type` (`tool-call`, `command`, `action`, `mutation`, `failure`,
`retry`, `validation`, `note`) and optional `Time`, `Task`, `Agent`, `Tool`,
`Target`, `Outcome`, `Error`, `Retry of`, `Parent`, `Affects`, `Artifact`, and
`Evidence`. An event is evidence about work, never a task: it is not planned,
not counted, and never drawn as a graph node. A failed event is unresolved
until a successful retry, direct or chained, answers it.

Praukron records nothing on its own. Agents append events under the work and
checkpoint workflows when an action could later explain a failure, a blocked
gate, or a regression. Events summarize; they never carry secrets, tokens,
credentials, hidden instructions, or private reasoning, and validation warns
when an event looks like it does. A project has operational history only from
when it starts writing `TRACE.md`; nothing is reconstructed from the journal.

### 2.4 Assumptions, owner responses, and owner-held tasks

`ASSUMPTIONS.md` holds `## A-<id>: <title>` records of choices an agent made
provisionally so work could continue (ADR-054): `Status` (`OPEN`,
`CONFIRMED`, `REVISED`, `REJECTED`, `WITHDRAWN`), `Tasks`, `Impact` (`LOW`,
`MEDIUM`, `HIGH`), `Recorded` (date · agent), `Assumption`, `Basis`,
`Applied in`, optional `Permissions`, `Responses`, and `Reconciled by`. An
assumption holds no authority by itself; the ADR, task, criterion, or
document it is applied in does. Its phase and module come from its tasks.
`HIGH` means the choice changes money, permissions, data shape or authority,
or external behaviour, or reverting it needs a data migration; `MEDIUM`
changes behaviour reversibly in code; `LOW` is presentation or wording.

`RESPONSES.md` holds the owner's input, `## R-<n>: <target> <ACTION>` with
`By`, `Date`, `Via`, and the owner's text verbatim as a blockquote. It is
append-only: no agent edits, reorders, normalizes, or clears an entry.
`CONFIRM`, `REVISE`, and `REJECT` target assumptions; `GUIDE` targets an
assumption or a task and never changes a status, a criterion, or an ADR.
Feedback the owner gives elsewhere is recorded here verbatim, `Via: relayed
by <agent> from <channel>`, before an agent acts on it.

A task with `Authority: owner` is work only a named person can finish. It may
be `DONE` only with `Validation: HUMAN_VERIFIED`.

**The boundary.** Before assuming, an agent searches authority: ADRs,
contracts, its task packet, the journal, documentation, and code. It
researches externally when that is useful and permitted; researched is not
confirmed. If authority resolves the choice, there is nothing to assume. If
the choice is reserved, the agent creates or names an `Authority: owner` task
that the work depends on, notifies the owner when the policy says so, and
continues other work. Otherwise it records the assumption before relying on
it, cites its id where it is applied and in any criterion evidence resting on
it, and never presents an `OPEN` assumption as decided.

**The policy** is per project (ADR-057): the newest `ACCEPTED` ADR carrying
`Policy: assumptions`, with `Reserved:` (any of `permission`, `security`,
`external-disclosure`, `legal`, `source-of-truth`, or `none`) and `Notify:`
(`host` or `none`). `/praukron-init` offers to record it. Without one, all five
groups are reserved and `Notify: host` applies. `permission` covers who may
see, create, change, approve, or delete what; `security` covers
authentication, secrets and credentials, and exposing a service or data
beyond the project. Whatever the policy says, destructive or irreversible
actions are reserved, and so is contradicting an accepted ADR or a frozen
criterion, which needs a superseding ADR or an Acceptance Change Request. A
project may change the policy only by superseding its policy ADR. An allowed
permission assumption carries `Permissions:` and is `HIGH`. With
`Notify: host`, a stopped agent notifies the owner through its host's own
channel and records the time and channel on the owner task; Praukron sends
nothing itself.

**Lifecycle.**

| Transition | Who | Authority that must change | History kept |
|---|---|---|---|
| new → `OPEN` | agent | the record; its id cited where applied | the record |
| `OPEN` → `CONFIRMED` | owner `CONFIRM`; agent sets the status | none required | the response |
| `OPEN`/`CONFIRMED` → `REVISED` | owner `REVISE`; agent reconciles | superseding ADR, ACR, task, document, or code, named in `Reconciled by` | response and reconciliation |
| `OPEN` → `REJECTED` | owner `REJECT`; agent reconciles | as for `REVISED`; applied work reverted or superseded | response and reconciliation |
| `OPEN` → `WITHDRAWN` | agent, when authority or a fact resolves it | `Reconciled by` names the record | the record |
| any, plus `GUIDE` | owner | none | the response |

*Responded* means a decisive response targets the assumption. *Reconciled*
means its status matches that response and, for `REVISE` or `REJECT`,
`Reconciled by` is set. A response not yet reconciled is reported, never
resolved, by the compiler; no status is ever derived into authority.

### 2.5 The index and retrieval

`INDEX.md` is compiled into the chronicle by `praukron compile` (ADR-047). It is
the one generated file there: never authored, never parsed as authority, and
when it disagrees with a record the record wins. It is compact — a few
thousand tokens at most, with a warning past 5,000 — and names the current
phase, execution work, next gate, main blocker, operations affecting
execution, debt needing attention, the decisions that matter now, the handoff,
and recent changes, as `key: value` markers and `file#anchor` pointers rather
than record bodies. It carries no timestamp, so identical records give an
identical index. `validate` warns when it is missing, stale, or hand-edited.

`praukron retrieve "<question or id>"` routes through the index (ADR-048). It
resolves the entities a question names, and for "this task", "the phase",
"blocked", or "debt" the entities the index marks as current; expands each
into its neighbourhood (a task's contract, decisions, debt, blocking chain,
operations blockers and failed events; a phase's gates, exit authority and
blockers); and returns only those canonical sections, verbatim, each labelled
with its source, with a count of how much of the chronicle it did not load. It
loads them most relevant first up to a token budget (`--budget`, default 4000,
0 for none); the first record always loads, and every record after the first
that does not fit is listed instead, by source, size and the command that
fetches it, so the cut is a clean prefix and nothing relevant goes unmentioned.
It writes nothing and consults no model.

An agent's order is fixed: `INDEX.md`, then the records it points to, then
code. Where CodeGraph is installed, `praukron retrieve <task> --code` adds code
structure — symbols, callers, callees — after the project context, seeded by a
task's optional `Files:` and `Symbols:` anchors (ADR-049). CodeGraph is
optional implementation intelligence: nothing Praukron computes reads it, and
its absence or failure degrades to a note naming the files to inspect.
`praukron codegraph status|setup|doctor|uninit` manage it; setup asks before
building the index and never changes agent configuration without an explicit
flag and a confirmation.

```text
INDEX.md        routing layer (generated)
chronicle/      canonical project memory: WHAT, WHY, STATE, NEXT, DEBT
CodeGraph       optional implementation intelligence: WHERE, DEPENDS-ON, BLAST RADIUS
the agent       HOW
```

## 3. Entry modes

Praukron has exactly two entry modes.

### 3.1 New repository

The developer supplies a product specification. The agent works with the
developer to:

1. resolve material ambiguity;
2. create the initial tasks and hard dependencies;
3. render the task graph;
4. record material product and implementation decisions as ADRs;
5. write the current state;
6. leave intent empty until work starts; and
7. append the first journal entry.

The chronicle must explain the project before implementation begins.

### 3.2 Existing repository

The agent installs an empty chronicle. It does not scan the repository to infer
past tasks, decisions, or intent. The current request becomes the first task
when work starts, and later sessions maintain the chronicle as part of normal
work.

A developer may explicitly request a baseline of the decisions the code already
depends on (ADR-037). It is a separate workflow, `/praukron-baseline`, that
initialization never runs. It proposes at most ten ADRs, each marked
`Origin: RECONSTRUCTED` with an `Evidence:` field naming the paths it was
inferred from, and creates no tasks, journal history, or intent. Proposals are
`PROPOSED` until the owner confirms them and is named in `Authority`.
Validation rejects a reconstructed ADR with no evidence, or one that is
`ACCEPTED` with no confirming authority, and the dashboard labels every
reconstructed decision.

### 3.3 Bootstrap

A single POSIX shell command installs the static chronicle, workflows, and agent
adapters into the current repository. It takes no arguments: `existing` is the
default mode and `new` remains available, as does an optional target directory.
It preserves an existing chronicle and project instructions, and prints the
matching command to start in the agent chat. The bootstrap is installation
tooling, not a project runtime. Downloading, it installs the latest published
release and runs that release's own installer; `--ref <tag|branch>` selects
another. It refuses to replace a newer installed runtime with an older one
unless given `--allow-downgrade` (ADR-040).

The installer also writes a launcher named `praukron` into a directory already
on the reader's `PATH`, so the command is `praukron` rather than a path
(ADR-027). The launcher carries no behaviour: it finds the nearest
`.praukron/praukron` by walking up from the working directory and execs it, so
every repository runs its own runtime. It creates no directory, edits no shell
configuration, and never replaces a `praukron` it did not write; `--no-link`
skips it. `.praukron/praukron` remains valid and is what the installed agent
instructions use, because an agent may run with a different `PATH`.

Repeated initialization preserves populated records and resumes. Reinstallation
restores missing files and never resets project history. It records a checksum
of every guidance file it writes in `.praukron/runtime/GUIDANCE`; on the next
install, guidance that still matches is replaced with the new version, and
guidance that was edited is kept byte for byte with the new version staged
under `.praukron/upgrade/` for review (ADR-040). The Praukron block in
`AGENTS.md` is treated the same way, and text around it is never touched.

## 4. Working lifecycle

### 4.1 Resume

Read, in order:

1. `STATE.md`;
2. `TASK_GRAPH.md`;
3. the active or selected entry in `TASKS.md`;
4. its governing entries in `ADR/`;
5. `INTENT.md`; and
6. only the recent journal entries needed for the handoff.

State the current goal and exact next action. Read specifications or code only
after the chronicle points to the relevant work.

### 4.2 Work

Create a task immediately when new work appears if the request has none; do not
wait for a Praukron command. Work on one task at a time. Mark it `WIP`, record its
owner and claim date, and overwrite `INTENT.md` with the exact execution point.
Refresh intent after meaningful progress and before long-running work.

Update the chronicle whenever project truth changes. `DONE` means the acceptance
condition is met and evidence is recorded. Validation is separate and uses one
of these values:

- `UNTESTED`
- `SYNTHETIC`
- `AI_REVIEWED`
- `HUMAN_VERIFIED`

Only a named human may record `HUMAN_VERIFIED`.

### 4.3 Decide

As soon as a material choice is made, accepted, or acted on, append an ADR with
its date, authority, context, decision, consequences, affected tasks, and any
decision it supersedes. Do not wait for a Praukron command. Never edit or delete
an earlier ADR to change its meaning. Follow the supersession chain for the
current rule.

### 4.4 Checkpoint

Before another person or agent takes over:

1. update task status, validation, evidence, and governing ADRs;
2. synchronize `TASK_GRAPH.md` and `STATE.md`;
3. update the single active intent, or clear it if the task is complete; and
4. append a journal entry with work done, validation, learning, work left
   mid-air, and the exact next action.

## 5. Automatic continuity

Chronicle maintenance happens during work. The agent checkpoints early enough
to finish writing, without waiting for the developer, when the agent or host
reports or estimates that:

- any context, input, output, or token budget is nearly exhausted;
- any time, session, rate, or quota window is nearly exhausted, including
  five-hour and seven-day windows;
- context compaction is approaching; or
- the session is stopping, pausing, or handing off.

Agent instructions cannot read quota counters that the host does not expose.
Without a host signal, the agent checkpoints after meaningful milestones,
before long-running work, and before ending so an abrupt cutoff loses little
project state.

This is an instruction-based contract, not a quota monitor. A host must expose
a warning early enough for the agent to write. Sudden termination can lose
changes since the last checkpoint; installation tests cannot prove agent compliance.

## 6. Commands

The portable workflows are:

- `/praukron-init [new|existing]`
- `/praukron-work [task]`
- `/praukron-decide`
- `/praukron-checkpoint`
- `/praukron-resume`
- `/praukron-baseline` (existing repositories, on request)

`praukron retrieve` and `praukron codegraph` are commands of the tool, not
workflows; the workflows use them.

Claude Code and OpenCode expose these as project slash commands. Codex exposes
the same workflows through `$praukron <mode>`. Every installation provides
`AGENTS.md` and the portable Markdown files in `.praukron/commands/`, which any
capable agent can follow directly. Adapters select workflows, never model providers;
provider credentials and model selection remain in the agent host.

## 7. Invariants

- `TASKS.md` is the source of hard task dependencies.
- `TASK_GRAPH.md` mirrors tasks; suggested order never becomes a hidden dependency.
- At most one intent exists.
- Every `DONE` task has acceptance evidence.
- ADRs and journal entries remain in history.
- A changed decision gets a new, superseding ADR.
- State and intent describe the present and are overwritten as truth changes.
- Entries use stable IDs and absolute dates.
- **Report salience is domain-aware.** A task's urgency, recency, blocked
  state, acceptance status, operational activity, or local priority does not
  make it prominent in an execution report unless it belongs to execution or
  has an explicit dependency that materially affects execution.
- **Operational activity remains inspectable** even though it never counts as
  progress: operations tasks, tool calls, mini-actions, failures, retries, and
  mutations stay reviewable because they can explain defects, regressions,
  blocked execution, or incorrect outcomes.
- The chronicle is the canonical project memory.
- `INDEX.md` is a generated routing layer, not a source of truth.
- New agents read `INDEX.md` before broad chronicle or repository exploration.
- Retrieval uses the index to select the minimum relevant canonical context.
- `TECH_DEBT.md` records known liabilities separately from tasks.
- CodeGraph is consulted only after the project context is resolved.
- Praukron remains fully usable without CodeGraph.
- CodeGraph may enrich implementation understanding but never owns project state.
- Changing a task's domain is a recorded change to `TASKS.md`, explained in
  the journal or, when it changes what counts as progress, an ADR.

## 8. Scope

Praukron owns project understanding, not project execution. It provides the
chronicle format, the agent workflow, and a deterministic compiler and CLI over
the records, including the generated dashboard. The working agent does the
reasoning and file editing; Git keeps file history.

Outside this specification: a service, a database, a semantic repository
scanner, an inference engine, a schema framework, a locking system, a model API,
and automatic historical migration. Nothing here schedules work, assigns work,
or decides what happens next.

ADR-008 removed an earlier runtime and made Praukron a workflow convention;
ADR-013 reinstated a deterministic compiler under the constraint that it reason
about nothing and invent nothing. A CLI and a dashboard are therefore in scope
as projections of authored records, and out of scope as sources of fact.

## 9. Acceptance

1. Given a product specification, an agent and developer can create a chronicle
   another agent can use before reading code.
2. In an existing repository, initialization starts empty and records the first
   session without fabricated history.
3. A fresh agent can recover current work, hard dependencies, governing
   decisions, validation, unfinished work, and the exact next action from the
   chronicle.
4. A superseding decision leaves the earlier ADR intact and discoverable.
5. A host limit or session-ending signal triggers a checkpoint; without a
   signal, milestone checkpoints preserve continuity.
6. A human reading the same files reaches the same project-level understanding.

### Handoff pilot

Run in a disposable project with the host and model you intend to use:

1. Install in `new` mode and supply a small specification; check that the first
   tasks and dependencies reflect it. Separately install in `existing` mode;
   verify it creates no invented history.
2. Make an ordinary work request without a Praukron command. Check that a task
   and single intent appear before implementation, with the graph kept in sync.
3. Make a material choice, then change it. Check that both ADRs remain and the
   newer one supersedes the earlier one, without requiring `/praukron-decide`.
4. Pause partway through a task. Check state, intent, and journal for the exact
   stopping point and next action. Try an exposed limit warning if available;
   label any simulated warning as simulated, not proof of quota detection.
5. Start a fresh agent session without the previous chat. Ask it to resume and
   explain the project goal, active task, governing decisions, and next action
   before reading code. Compare with the saved handoff.
6. Run init again; check that tasks, ADRs, and journal history remain intact.
7. Ask a person who did not do the work to read the same chronicle and explain
   the project's purpose, a changed decision and its reason, current state,
   and next priority. Compare their account with the agent's; record gaps or
   disagreements for clarification. Only the participating human can mark this
   check `HUMAN_VERIFIED`.

Record host/model, date, observed results, and any gaps. Repeat for each host
you claim to have verified. This pilot is not covered by `tests/install.sh`.
