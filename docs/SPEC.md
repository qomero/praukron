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
undone if it introduces an error (ADR-055). `praukron archive`, run only at
the owner's request, plans (`archive plan <phase>`), applies (`archive apply
<phase> --plan <digest>`) and reverses (`archive restore <phase>`) the move of a
COMPLETE phase's, or `P-NONE`'s, closed records verbatim into `archive/<phase>/`;
they remain authority, read with the working files, and the move is undone
unless the records read back identical (ADR-067, ADR-070; §2.7). See ADR-013, ADR-016, ADR-017,
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
| `COORDINATION.md` | Optional. A coordinator's standing conventions and per-task briefs (§2.8); never archived |
| `CODE.md` | Task–code references: the files and symbols each task planned to touch and was observed to touch (§2.9); never archived |
| `AUTHORITY.md` | Optional. What the owner lets agents do on the owner's authority: one grant per capability given, and its revocation (§2.11); written only by `praukron authority`, never archived |
| `archive/<phase>/` | Closed records moved verbatim by `praukron archive` (§2.7); authority, immutable to the write commands |
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
thousand tokens — `validate` warns past 5,000, but nothing truncates the file,
so the budget is a warning and not a cap — and names the current
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
0 for none). The budget bounds the selected record bodies, not the whole output:
the first record always loads, and the labels, the omission list and any code
section are extra. Every record after the first that does not fit is listed
instead, by source, size and the command that fetches it, so the cut is a clean
prefix and nothing relevant goes unmentioned. For each task the question
names, the pack also carries the task's code references (§2.9), as `context
<task>` does, outside the budget.
It writes nothing and consults no model.

`praukron context <task>` returns the task packet as JSON: the task with its
lineage, closability and claim, its contract, invariants, decisions, debt,
blockers, assumptions, owner responses, brief and conventions, the `file#anchor`
records to read, and its code references (§2.9): counts, the first twelve, and
the command that returns the rest.

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

### 2.6 Structured writes

Agents write tasks, contracts, criterion results, journal entries, change
requests and code references through commands instead of editing Markdown
(ADR-068, ADR-069). An editing tool must read a file before changing it, so a
direct edit costs about the size of the file; a structured write costs about the
size of the record. The Markdown stays the only source of truth: the commands
write the same sections a person would, and `parse.py` remains the single
definition of the format. Direct editing stays valid for people and for any
record these commands do not cover.

| Command | Writes | Changes |
|---|---|---|
| `praukron journal append` | `JOURNAL.md` | appends one `## <date> — <title>` section |
| `praukron criterion set` | `ACCEPTANCE.md` | one criterion's state and its one-line evidence |
| `praukron task add` | `TASKS.md`, optionally `ACCEPTANCE.md` | appends a task, and its new contract in the same write |
| `praukron contract add` | `ACCEPTANCE.md`, `TASKS.md` | appends a contract and sets the task's `AC:` |
| `praukron task update` | `TASKS.md`, `CODE.md` at `DONE` | `Status`, `Owner`, `Claimed`, `Evidence`, `Validation` of one task; appends to `Governed by` (ADR-068, ADR-074); `Claimed at` when it claims, and the observed references it captures when it closes (§2.9, ADR-092) |
| `praukron acr add` | `ACCEPTANCE.md` | appends one `### ACR-<n>` request to the `## Acceptance Change Requests` log (ADR-076) |
| `praukron ref add` | `CODE.md` | appends one task–code reference, creating the file from its template header when absent (§2.9) |
| `praukron ref set` | `CODE.md` | one reference's verification, lifecycle or evidence in place, or an appended record that withdraws or replaces it; settling an unresolved attribution always appends (§2.9) |
| `praukron batch apply` | the files its operations write | runs several of the writes above in order under one lock, one validation and one refresh; all or nothing (ADR-077) |

With `INDEX.md` (generated), `respond` (`RESPONSES.md`, ADR-055), `scope
capture` (`CODE.md`, §2.9) and `archive` (ADR-067, only when the owner asks),
these are the only chronicle writes the tool makes.

**Interface.** Each command takes a JSON object from `--input FILE` or
`--input -` (stdin); unknown fields, duplicate keys and wrong types are refused.
`--schema` prints the payload schema with a minimal example and needs no
project. `--dry-run` plans the write and reports `wouldWrite` without writing;
the whole-chronicle check runs only on a real write. `--help`, and the
`description` in `--schema`, state WHEN, WHEN NOT, RETURNS and EXAMPLE. Output is one compact JSON line.

**Pipeline.** Payload schema → governance rules → the target resolved by ID → one
section built → the shared write lock (owner-checked, never reclaimed from a live
holder) → a durable recovery record of the original bytes, removed once the
write commits with only the directory and ignore file it created → atomic replacement →
`compile.load` and `validate` again → refresh of `INDEX.md`, the compiled views,
the graphs and the dashboard. A write that introduces a validation error that
was not already present is undone byte for byte and reported as
`validation-failed`. Line endings (CRLF or LF) and UTF-8 are preserved, and
unrelated bytes are untouched. If a process dies mid-write, the next write
restores the recorded originals first and reports `interrupted-write`; try
again.

**Field permissions and rules.**

- IDs are supplied by the caller and refused if they exist in a working file or
  in any archive (`duplicate-id`); they are never inferred. The exceptions
  are an ACR id, a log sequence `acr add` assigns under the lock (ADR-076),
  and a reference's `CR-<task>-<nn>` id, the task's next sequence, which
  `ref add` and `ref set` assign under the lock (§2.9).
- `task add` needs an existing `module`, an explicit `domain` and
  `dependencies`; it has no `phase` field (a task's phase comes from its
  module). Give exactly one of `contract` (a new definition, written with the
  task) and `ac` (an existing contract). `validation` may not be
  `HUMAN_VERIFIED`; `authority` may be `owner`.
- A contract freezes when its task is `WIP`. `contract add` is refused for a
  `WIP` or `DONE` task (`frozen-contract`). No command changes a criterion's
  text or class, or an inherited invariant criterion: that is an Acceptance
  Change Request, decided by a person.
- `task update` records a `Frozen:` fingerprint of the contract's criteria (id,
  class and text, never state or evidence) when it moves a task to `WIP` from
  `TODO` or `BLOCKED`, and on the first status change of a `WIP` or `DONE`
  task that has none; a frozen task keeps it (ADR-078). `validate` reports a
  `WIP` or `DONE` task whose criterion was since changed, added or removed by
  hand (`frozen-criterion-changed`, `-added`, `-removed`), unless accepted
  Acceptance Change Requests lead from the current text back to the frozen
  one. A task without the field is not checked.
- `criterion set` changes a criterion's state and optionally its evidence, which
  is a single line because the parser reads one.
- `task update` changes only `Status`, `Owner`, `Claimed` and `Evidence` (ADR-068),
  and, as ADR-074 amended that rule, `Validation`; it also appends existing ADR ids to `Governed by` (`addGovernedBy`;
  ids already listed are skipped and reported in `alreadyGoverned`, entries are
  never removed or reordered, an unknown id is refused as `unknown-decision`).
  `set.validation` takes `UNTESTED`, `SYNTHETIC` or `AI_REVIEWED`; `HUMAN_VERIFIED`
  is refused. `WIP`
  needs an owner and sets `Claimed` to today unless given. `DONE` needs every
  mandatory criterion of the task's contract at `PASS` and recorded evidence. An
  `Authority: owner` task is never set `DONE` (`owner-held`), and no command sets
  `HUMAN_VERIFIED`.
- In a Git repository, `task update` (alone or inside `batch apply`) that moves
  a task to `WIP` also writes the optional task field `- Claimed at: <full
  commit id>`, the commit checked out at the claim (ADR-092). A task that
  already has one keeps it when claimed again; one without gains it. No
  payload field sets it. When the update moves a task with `Claimed at` to
  `DONE`, the same write captures its observed files into `CODE.md` (§2.9); the
  task and its references are written together or not at all.
- Capture never refuses or delays a write. The reply of a claim or a close
  carries a `capture` member: at a claim `{"status":"recorded","commit":…}`,
  `{"status":"kept","commit":…}` for a task claimed before, or
  `{"status":"skipped","reason":…}`; at a close `{"status":"captured",
  "references":<n written>,"base":…,"head":…,"workingTree":true,
  "alreadyRecorded":<n>,"unresolved":<n>}`, with `merges`, `notMadeHere` and one `warning`
sentence when a reference is unresolved (§2.9), `skippedPaths` for a path a
  record cannot hold, or `skipped` with its reason. Capture is skipped, and the
  write is otherwise exactly as without it, when `git` is not on the path, the
  project is not in a Git working tree, the repository has no commit, the task
  has no `Claimed at`, the claim commit is not in the repository, or a Git
  command fails or exceeds its 20-second timeout. Inside `batch apply` the
  member is on that operation's entry in `results`.
- Records under `chronicle/archive/` are immutable to every command
  (`archived-record`).
- `journal append` keeps its full heading unique across the working file and
  every archive, so a retry is refused, not duplicated.

**Revisions.** An update names the revision it was written against
(`expectRevision`; `expectTaskRevision` for `contract add`): the first 16 hex
characters of the SHA-256 of the record's section bytes with the trailing run of
CR and LF removed (ADR-069). Appending or archiving other records never changes
it; any change inside the record does. A criterion's revision is that of its
contract's section. The check runs under the lock. A mismatch changes nothing
and returns `currentRevision` with the current record, inside `error` (`section` for a
task, `currentCriterion` for a criterion), or beside it (`current`, for
`contract add`). Every success
returns the record's new `revision`, so a chain of writes needs no re-read. To
learn a revision, first read `context <task>`, `explain <task>` or
`retrieve <task>`; sixteen zeros are only a fallback for a record not yet read.

**Results.** A success is `{"ok":true,"operation","recordId","source",
"revision","validation":"passed","written":true,"refreshed":true}`; a dry run
adds `dryRun` and `wouldWrite` and has `written:false`. A refusal is
`{"ok":false,"operation","error":{"code","message","field"?,…},"written":false}`;
the message names the rule and the next valid step. A refusal of the payload's
shape (`unknown-field`, `missing-field`, `misplaced-field` for a field beside
the object it belongs in, with `expected` naming its place, `wrong-type`,
`invalid-json`, `invalid-contract`, `empty-set`) also carries `example`, a
minimal accepted payload built from the command's schema (the two failures after a
commit, `lock-release-failed` and `refresh-failed`, carry `written:true`: the write
stands and is not repeated). The exit status is 0 for a
success (and for `--schema`), 1 for any refusal or failure, and 2 for a usage
error. If the write committed but the refresh failed, the result has
`written:true`, `refreshed:false` and error `refresh-failed`: do not repeat the
write; run `compile && graph && dashboard`.

**Retry semantics.** Nothing is assumed idempotent; every retry is decidable.

| Situation | Result |
|---|---|
| `journal append` repeated | refused: `duplicate-heading` |
| `task add` or `contract add` repeated | refused: `duplicate-id` |
| `task update` repeated with the revision it used before | refused: `stale-revision`; the refusal shows the record as it now is |
| `criterion set` repeated with the old revision | refused: `stale-revision` |
| `criterion set` repeated with the new revision and the same values | succeeds again as a no-op: `written:true` and the same `revision` |
| `task update` with the current revision and the values already set | refused: `no-change` (with `alreadyGoverned` when ADR ids were already listed) |
| lock held by another writer | refused: `locked`; wait and retry |
| an earlier write was interrupted | refused once: `interrupted-write`, after the restore; retry |

**Batch composition (ADR-077).** `batch apply` takes a non-empty `operations`
array. Each entry is one write command's payload plus `op`, the command's name:
`journal append`, `criterion set`, `task add`, `contract add`, `task update`,
`acr add`, `ref add` or `ref set`. An unknown `op` and a nested batch are refused. A registry pairs each
name with the command's schema, handler and revision target, so a later governed
write joins without a second transaction engine.

The batch takes the write lock once, captures the chronicle, and plans every
operation in array order against a byte overlay, so each sees what earlier ones
staged: a claim, a new task or contract, a criterion state, a journal heading,
an open request. Each keeps its command's governance at the point it runs:
frozen contracts, `DONE` only with every criterion `PASS`, owner authority,
archive immutability, duplicate ids and headings. Intermediate states are not
validated. The baseline and final validation compare once, over the combined
proposed state, before anything is persisted; `--dry-run` runs the same check.

Revision fields stay required. The revision read before the batch serves every
operation on that section: it is checked against the captured original and
then mapped to the staged section for the command's own check. A current staged
revision is also accepted. `$previous` is accepted only for a section an earlier
operation in this batch wrote, such as a task or contract it created; it never
authorizes the first touch of an existing section. Criteria in one contract
share its revision. A stale revision on a section the batch already changed is
refused with the revision read before the batch as `currentRevision`, which is
what a retried batch needs.

The combined bytes are persisted with one durable recovery record, the existing
atomic replacements and byte-exact rollback. A refusal, a new validation error
or a persistence failure writes nothing and refreshes nothing. After commit and
lock release, `INDEX.md`, compiled data, graphs and dashboard refresh once; a
refresh failure reports `written:true, refreshed:false`, to be repaired by a
refresh alone.

The result is one JSON line: ordered `results` (`index`, `op`, `recordId`,
`source`, `revision` after that operation), final `revisions` keyed by
`file#anchor` for every changed section, and shared `written` and `refreshed`.
A refusal's `error` carries the operation's `index` and `op` with the command's
own code and message, and a shape refusal its minimal accepted entry; final
validation and persistence failures name their `stage`. Dry-run returns the
proposed results and `wouldWrite`. The CLI exits nonzero on any refusal.

### 2.7 Archive lifecycle

`TASKS.md`, `ACCEPTANCE.md` and the lifecycle files beside them only grow;
`archive` keeps what an agent reads bounded without losing history. It is the
owner's command and agents never run it (ADR-067, ADR-070).

| Command | Effect |
|---|---|
| `archive plan <phase>` | Read-only. Per-record `moves`/`stays` with the reason, `changedFiles`, `createdFiles`, `mode`, a `recovery` that is empty until `apply` or `restore` fills it, and a SHA-256 `digest` over every chronicle file, the phase, the mode and the version. |
| `archive apply <phase> --plan <digest>` | Under the shared write lock, plans again and applies only if the digest matches (`stale-plan`); `missing-plan` without `--plan`. |
| `archive <phase> [--dry-run]` | The one-step form: plan and apply without a digest. |
| `archive restore <phase> [--dry-run]` | Returns every record in `archive/<phase>/` to the working files without checking eligibility; `id-collision` if an id already exists there, `unknown-archive` if there is nothing to return. |

`--json` prints one line: the plan fields plus `ok`, `applied`, `written` and
`refreshed`, or `ok:false` with `error.code` and `error.message`.

**Selection.** A phase is `P-NONE` or a phase in `PHASES.md` whose status is
`COMPLETE` (`unknown-phase`, `incomplete-phase`). `archive` is refused unless the
chronicle validates cleanly; `restore` validates the state it would write.

**Eligibility** is evaluated once, when `apply` (or the one-step form) runs, and not
again by `restore` or `validate`, on the phase axis only: a
task's phase is its module's, and its `Domain` never changes eligibility. No
identifier prefix is ever interpreted.

| Record | Moves when | Stays when |
|---|---|---|
| Task | `DONE`, in the selected phase | an `ACTIVE` brief names it (§2.8); an `OPEN` assumption names it; a response to such an assumption awaits reconciliation; a linked debt is not `RESOLVED`/`INVALIDATED`; an unresolved failure points at it; under `P-NONE`, a working task depends on it |
| Contract | its task moves | a working task uses it; another contract inherits it; it holds an inherited invariant |
| Assumption | `CONFIRMED`, `REJECTED`, `REVISED` or `WITHDRAWN`; no response awaits reconciliation; every task it names is archived or moving | otherwise; it names no task |
| Technical debt | `RESOLVED` or `INVALIDATED`; names a task; all its tasks are archived or moving | otherwise |
| Trace event | its task is archived or moving (no task: `P-NONE`) | part of an unresolved failure or retry chain |
| Journal entry | names at least one archivable record, and every record it names is archived or moving | names nothing, only ADRs, an open or unknown record, or its heading is not unique |

**Journal references.** An entry names an id by writing it (`T-`, `AC-`, `A-`,
`TD-`, `EV-` or `ADR-` followed by letters, digits, dots and hyphens, ending in a
letter or digit, so trailing punctuation is not part of it). A range names every
id it spans: `T-P0-01..05`, `T-P0-01..T-P0-05` and `ADR-003..005` each stand for
the ids from the lower end to the upper. Both ends share the stem before a
numeric tail, the tails have the same width (`ADR-003..005` keeps three digits;
`T-01..5` and `T-9..10` are refused), the lower end is not above the upper, and a
range spans at most 100 ids. `Closed T-1..T-3.` names `T-1`, `T-2` and `T-3`.
Validation names only the members that do not exist; a range that breaks a rule
stands for no id and is one `unknown-journal-reference` warning naming the range.
Archive eligibility reads the same expansion, so an entry naming a range moves
only when every member is archived or moving.

A record naming tasks of several phases goes to the latest of them in `PHASES.md`
order, `P-NONE` last. `RESPONSES.md`, `COORDINATION.md` and `ADR/` are never archived. Records move
verbatim, journal entries as exact byte spans placed by date and the saved order
in `archive/JOURNAL.order.json` (ADR-072, ADR-073); each file keeps its line
endings and UTF-8, and an archive file takes those of its source.

**Transaction.** The move is written file by file atomically under a durable
recovery record, then read back: authored records must equal their originals
except `source`, journal sections must equal byte for byte, and `validate` must
introduce no error; otherwise everything is restored and the command reports
`nothing was moved`. A process that dies mid-move is rolled back by the next
write command. Derived facts equal the originals as sets, and `context` is unchanged:
events are ordered when loaded by their own time, then the number in their id, then
the id, so the last five related events do not depend on which file holds them.

**Recovery.** `mode` is `git` when the chronicle is in a Git work tree, none of
its paths is ignored and no checkout conversion (`core.autocrlf`, `filter`,
`eol`, `text`, `crlf`, `ident`, `working-tree-encoding`) applies; the chronicle must then
be clean (`dirty-chronicle`) and the result carries commands for the unstaged,
staged and committed states (`git restore --source=HEAD`, `git rm --cached` for
new files, `git revert <archive-commit>`), for POSIX sh and PowerShell. Otherwise
`mode` is `nogit`: a byte-exact copy of every original goes to
`.praukron/backup/<UTC timestamp>-<id>/` with a manifest before any write, and the
result carries the command that restores it.

**Permanent invariants (`validate`).** Archives are checked for what must always
hold, never for eligibility, and there is no separate `archive verify`. Across the
working files and every archive: Markdown files directly in `archive/<phase>/` are
known record files (`invalid-archive-file`, `invalid-archive-input` errors;
`case-mismatched-archive-filename` warning); any other path there (another extension,
a nested path, a directory) is an `unsupported-archive-path` warning because restore
refuses it, found from directory listings alone; records parse; ids are unique (`duplicate-task`, `duplicate-event`,
`duplicate-assumption`, `duplicate-debt`; a duplicate contract fails the load);
references resolve (`unknown-dependency`, `unknown-contract`, `unknown-decision`,
`unknown-assumption-reference`, `unknown-debt-reference` errors; an event's missing
task or affected record is a warning); archived journal headings are well formed
(`invalid-journal-heading` error), unique (`duplicate-journal-heading` warning) and
name existing ids (`unknown-journal-reference` warning, see journal references below); no recovery record is left
(`interrupted-archive` error); a task in the wrong phase directory is a warning
(`wrong-archive-phase`). Later references to archived records are never findings.
A successful `apply` or `restore` refreshes `INDEX.md` and the compiled data
(`compile`), then the graphs, then the dashboard. If only that refresh fails the
result is `ok:false`, `error.code` `refresh-failed`, `written:true`, `refreshed:false`
with exit 1; run `compile`, `graph` and `dashboard`, never `apply` again.

### 2.8 Coordination conventions and task briefs

A project that runs several agents keeps how it runs them in the chronicle, not
in any agent's session (ADR-075), so a relaunched agent resumes from
`context <task>` alone. `COORDINATION.md` is optional and holds two record
kinds; an absent file means no conventions and no briefs, never an error.

- A **convention** is `## CV-<id>: <title>` with `Status` (`ACTIVE` or
  `RETIRED`), optional `Roles` (any of `builder`, `reviewer`, `coordinator`;
  absent or `all` means every role), and free Markdown after a blank line: a
  standing rule such as the rules every worker or reviewer follows, which agent
  builds or reviews, how a session is started, or how review rounds run.
- A **brief** is `## BR-<id>: <title>` with `Task` (one task), `Status`
  (`ACTIVE` or `CLOSED`), `Conventions` (required: the `CV-` ids it rests on,
  or `none`; a missing or misspelled field is an error, never read as `none`),
  any further one-line fields the project uses (for example `Branch`, `Base`,
  `PR`, `Builder`, `Reviewers`, `Review` for the current round), and notes after
  a blank line. A brief adds what the contract does not hold; it never restates,
  narrows or widens a criterion.

Fields are the block directly under the heading; a list in the notes is prose.
Both kinds are authority, written by the coordinator (a person, or the agent
acting as coordinator) by editing one section and running `validate`; no write
command covers them. Where a brief or convention differs from an acceptance
contract, an invariant or an accepted ADR, those win; it ranks with existing
code convention. Records never hold secrets: a convention names where a secret
is found, never its value. A project's own conventions may name its host,
launcher or models; Praukron's templates and documentation name none.

`context <task>` carries `brief` (the task's `ACTIVE` brief, else its last
`CLOSED` one, else `null`) and `conventions` (the `ACTIVE` conventions that
brief names and that apply to the packet's `--role`: `builder`, the default,
`reviewer`, or `coordinator`, which a coordinator uses to recover its own
launch and review-round conventions), each verbatim with an
`authority` pointer `COORDINATION.md#<id>`, also listed under `authority.read`.
`retrieve <task>` adds them, for every role, as `BRIEF` and `CONVENTION` items
after the task's governing ADRs and within its budget;
`retrieve CV-<id>` and `retrieve BR-<id>` return one record. A packet never
carries the whole file.

`validate` reports a malformed heading or a missing `Status` or `Task` (the
load fails, as for every record file); `duplicate-coordination-record`,
`invalid-convention-status`, `invalid-convention-role`, `invalid-brief-status`,
`unknown-brief-task` (in neither the working files nor an archive),
`brief-without-conventions`, `unknown-brief-convention`, `retired-brief-convention`
(an `ACTIVE` brief resting on a convention that is not `ACTIVE`) and
`multiple-active-briefs` errors; and `active-brief-done-task`,
`long-coordination-record` (over 2,000 estimated tokens) and `possible-secret`
(text that looks like a credential) warnings. `archive` never moves `COORDINATION.md`, and keeps a task in
the working files while an `ACTIVE` brief names it; a `CLOSED` brief keeps
resolving to its archived task.

### 2.9 Task–code references

This section defines how a task is linked to the code it touches: the record
format in `CODE.md`, how references are read (`praukron refs`, the task
packet, the dashboard), how they are written by hand (`ref add`, `ref set`),
and how the tool records them by itself from Git when a task closes (ADR-088,
ADR-089, ADR-092). Everything in it is built.

None of it needs a graph provider. A provider is optional and only adds code
structure to `retrieve --code` (§2.5). A provider is a third-party program:
Praukron does not control or inspect what it sends over the network,
telemetry included, and handling that is the user's responsibility. Praukron
itself sends nothing (ADR-090).

A task–code reference says that one task relates to one code artifact: a file,
or a symbol inside a file. References are authored records in
`.praukron/chronicle/CODE.md`, one reference per record. They are authority
like every other record: a graph provider never writes them, and removing a
provider changes none of them. A task needs no references to be valid.

```text
## CR-T-PAY-03-01: Post a ledger entry
- Task: T-PAY-03
- Path: src/pay/ledger.py
- Symbol: Ledger.post
- Kind: method
- Class: production
- Relation: modifies
- Scope: planned
- Lifecycle: active
- Verification: unverified
- Authorization: proposed
- Source: agent
- Recorded: 2026-11-26 · claude/primary
- Revision: 286b9a7
```

The identifier is `CR-` followed by the task's identifier and a two-digit
sequence within that task, as a criterion's identifier follows its contract.
The heading's title is free text on one line.

| Field | Required | Value |
| --- | --- | --- |
| `Task` | yes | One task identifier, in the working files or an archive. |
| `Path` | yes | A path relative to the repository root, with `/` separators. Absolute paths, a `..` segment, a backslash and a drive letter are errors. The file need not exist. |
| `Repository` | no | Another repository's name, when the artifact is not in this one. Absent means this repository. |
| `Symbol` | no | The symbol's qualified name inside `Path`, as the language writes it (`Ledger.post`). Absent means the reference is to the file. |
| `Kind` | with `Symbol` | `function`, `method`, `class`, `interface`, `type`, `variable`, `constant`, `module`, `route`, `model`, `schema` or `other`. |
| `Class` | no | `production`, `test`, `config`, `docs`, `migration` or `generated`. Absent means not classified. |
| `Relation` | yes | `creates`, `modifies`, `deletes`, `reads`, `depends_on`, `verifies`, `documents` or `affects`. |
| `Scope` | yes | `planned` (what the task expects to touch) or `observed` (what it did touch). |
| `Lifecycle` | no | `planned`, `active`, `renamed`, `deleted` or `unresolved`. Absent means not stated. |
| `Verification` | yes | `unverified`, `verified`, `disputed` or `stale`: whether the reference matches the code. |
| `Authorization` | yes | `not_applicable`, `proposed`, `authorized`, `rejected` or `revoked`: whether the task may make the change. |
| `Authorized by` | with `authorized` | The owner response (`R-`) or accepted ADR that grants it. |
| `Source` | yes | `manual`, `agent`, `git_diff`, `static_analysis` or `graph_provider`. |
| `Provider` | with `graph_provider` | The provider's name and version. |
| `Recorded` | yes | A date, `·`, and who recorded it. |
| `Updated` | no | A date, `·`, and who made the latest in-place change (`ref set` with `set`). `Source`, `Provider` and `Recorded` keep the reference's origin. |
| `Revision` | no | The commit the reference was true at. |
| `Base`, `Head` | no | The revisions an observed reference was captured between. |
| `Lines` | no | `start-end`, a hint only; never an identity. |
| `Confidence` | no | A number from 0 to 1. Absent means unknown; no default is ever supplied. |
| `Was` | no | The earlier path, or `path#symbol`, of a renamed or moved artifact. |
| `Replaces` | no | The reference this one replaces. The replaced record stays in the file. |
| `Withdrawn` | no | A date and a reason. A withdrawn record stays in the file. |
| `Evidence` | no | What shows the reference is true. |

`Verification` and `Authorization` are independent: a verified reference is not
thereby authorized, and an observed change is not thereby approved. `Relation`
grants nothing. `Scope: observed` never replaces a `planned` reference; both
stay. A reference is never deleted: it is replaced or withdrawn, so the history
of an artifact survives its rename or deletion.

An artifact is identified by its repository, its path and, for a symbol, its
qualified name. A line range and a bare symbol name are never an identity. Two
artifacts that share only a name stay two artifacts.

A task's `Files:` and `Symbols:` anchors stay valid and are never rewritten.
Each compiles as a planned reference with `Source: manual` and no relation; a
`Symbols:` anchor names no file, so its artifact is `unresolved`.

The compiled model lists every reference and, for each artifact, the tasks that
reference it, so both directions are answered without reading `CODE.md`:

```sh
praukron refs T-PAY-03                    # the task's files and symbols
praukron refs src/pay/ledger.py           # the tasks, decisions, criteria and assumptions for a file
praukron refs src/pay/ledger.py#Ledger.post
```

`praukron why <path>[#symbol]` (ADR-103) walks the same records the other
way for a reader asking why code is as it is: the tasks whose current
references reach a symbol, a file or a directory, oldest first by claim or
reference date, each with how its reference was recorded, the decisions that
reach it through `Governed by:` or `Affects:` with their standing (in force,
superseded with the decisions that supersede it, or not accepted), its
criteria and inherited invariants, each with its `file#anchor`. A target no
current reference reaches is answered as unexplained, with the history of the
nearest enclosing symbol, file or directory that has a record. It reads only
the chronicle, never a graph library or index, and writes nothing.

For a task, `refs` lists every reference with each compiled field and its
`file#anchor`. For a path it returns every reference to the file and to a
symbol in it; for `path#symbol`, the references to that symbol. Either way each
referencing task comes with its governing ADRs, acceptance criteria and
assumptions, each with its `file#anchor`. A path is read in this repository; a
`Symbols:` anchor names no file, so it is found from its task, never from a
path. A target written as a task identifier that names no task is refused as
`unknown-task`; any other target that is not a task and breaks the path rule
above is refused with that rule's message. `--json` prints the answer as JSON,
every field included, with null for one not recorded. The text names each
criterion by its identifier and state; `--json` also carries its text. `refs` reads the
compiled references only: it writes nothing and runs no graph provider, so its
answer is the same with or without one installed.

A reference is superseded when another record names it in `Replaces`, and it
is current when it is neither superseded nor withdrawn: a withdrawal or a
replacement leaves the earlier record unchanged, so only its successor says it
no longer holds. `refs` still lists every record, marking a superseded one
`replaced by CR-…` (`replacedBy` in `--json`) and a withdrawn one with its
`Withdrawn` value, and gives `current`, the number of current records. A path
lookup's `tasks` names a task only while it has at least one current reference
to the target; the other tasks' records stay under `references`, and the text
lists them as no longer current.

`context <task>` and `retrieve <task>` carry the task's current references in a
`references` field: `total`, `counts` by scope (`planned`, `observed`), the
`first` twelve (id, path, symbol, relation, scope, verification, authorization,
revision, evidence and `authority`, the record's `file#anchor`; a key that is
absent was not recorded), and `rest`,
the `praukron refs <task>` command that returns them all, or null when none are
left out. `retrieve` lists them in a `REFERENCES` section after the records,
outside the token budget. No other packet field depends on references. With
one hundred references the packet grows by no more than 1.25 times, the growth
allowed for a chronicle ten times larger. The paths and symbol names are the search
terms an agent starts from.

Agents write references through structured writes (§2.6), never by editing
`CODE.md`:

```sh
praukron ref add --input -      # one reference; a planned change is `proposed` unless `authorizedBy` names its authority
praukron ref set --input -      # verification, lifecycle, withdrawal, or a replacement
praukron ref history CR-T-PAY-03-02   # the reference and every record it replaces, oldest first, unchanged
```

Both writes are also `batch apply` operations. No write sets `Authorization:
authorized` without `Authorized by`. `archive` never moves `CODE.md`.

**`ref add`** takes `task`, `title`, `path`, `relation`, `scope`, `source` and
`recordedBy`, and optionally the other fields of the table in camel case
(`commit` is written as `Revision`, `authorizedBy` as `Authorized by`). It
assigns the identifier: `CR-`, the task, and the task's highest sequence plus
one, two digits at least. `Verification` defaults to `unverified`. The payload
has no `authorization` field: a `planned` reference whose relation is
`creates`, `modifies` or `deletes` is written `proposed`, or `authorized` with
`Authorized by` when `authorizedBy` names an owner response (`R-`) or an
`ACCEPTED` ADR (`unknown-authority` otherwise); every other reference is
`not_applicable`, and `authorizedBy` on one is refused
(`authorization-not-applicable`). When `CODE.md` is absent the write creates
it from the template's header.

**`ref set`** takes `id`, `expectRevision` (the reference section's revision,
§2.6), `recordedBy`, and exactly one of:

- `set`: `verification`, `lifecycle` or `evidence`, changed in place. It never
  touches `Source`, `Provider` or `Recorded`, which say how the reference was
  first obtained and by whom; it writes `Updated: <date> · <recordedBy>` after
  `Recorded`, rewritten by each later `set`. `source` and `provider` are
  refused with `set` (`provenance-kept`). A value already present is
  `no-change`.
- `withdraw` (needs `source`): `reason` and an optional `date`. A new record is appended that
  copies the reference, adds `Replaces:` and `Withdrawn: <date> · <reason>`,
  and carries this write's `Source` and `Recorded`.
- `replace` (needs `source`): the fields that change. A new record is appended with
  `Replaces:`; it inherits path, repository, symbol, kind, class, relation,
  scope, lifecycle and title unless given, starts `unverified` unless given,
  and takes its authorization by the `ref add` rule, so an earlier
  `authorized` is not carried over without `authorizedBy`.

A withdrawal or replacement leaves the earlier record byte for byte and
returns the new record's id with `replaces`. A reference already replaced
(`replaced-reference`, naming `replacedBy`) or withdrawn
(`withdrawn-reference`) is refused; act on its latest record. A stale
`expectRevision` is refused with `currentRevision` and the current section.

Every record a write appends carries `Source` and `Recorded: <date> ·
<recordedBy>`, and every in-place change `Updated: <date> · <recordedBy>`;
`recordedOn` gives the date and defaults to today. `source:
graph_provider` needs `provider`, and `provider` is refused with any other
source.

**`ref history <CR-id>`** follows `Replaces:` back from the named reference
and prints each record, oldest first, exactly as it stands in `CODE.md`,
separated by a blank line; `--json` returns `records` with each one's `id`,
`source`, `revision` and `record`. It does not list records that replace the
named one.

The dashboard (`praukron dashboard`) reads the same compiled references. The
task registry counts each task's current references in a Files column and a
Symbols column, anchors included, and its search matches an exact or partial
path or a symbol name of a current reference. Each task record carries a Code
References section, shown also in the task's side panel, grouped into planned files,
planned symbols, observed files and observed symbols, with relation,
authorization, verification, revision, provenance and the other tasks holding a
current reference to the same artifact (three, then a link to the rest);
unresolved, stale, replaced and withdrawn references are listed and marked.
Audit's Code artifacts group gives each artifact its current path, its earlier
paths from `Was`, the tasks that reference it and their decisions. The page
embeds the reference list once, in its project data, plus the ids of the
references that are no longer current.

**Observed files, captured from Git (ADR-092).** When a structured write sets a
task `DONE` and the task has `Claimed at` (§2.6), the same write records every
file changed from that commit to the working tree, staged, unstaged and
untracked changes included, as observed references. No command is needed.
`praukron scope capture <task> --base <rev> [--head <rev>]` records the same
between two named revisions (to the working tree when `--head` is absent), for
a task that has no `Claimed at` or was worked elsewhere; it plans through the
same code and commits through the same transaction and validation, and prints
one JSON line with the same `capture` member (`--dry-run` reports
`wouldWrite`). A capture that finds nothing new succeeds without writing.

Each changed file is one record, `Recorded` by the task's owner:

```text
## CR-T-PAY-03-04: src/pay/ledger_v2.py (renamed from src/pay/ledger.py)
- Task: T-PAY-03
- Path: src/pay/ledger_v2.py
- Relation: modifies
- Scope: observed
- Verification: verified
- Authorization: not_applicable
- Source: git_diff
- Recorded: 2026-11-26 · claude/primary
- Revision: 9c1e0f…
- Base: 286b9a7…
- Head: 9c1e0f…
- Was: src/pay/ledger.py
- Evidence: git diff 286b9a7c0d1e to 9c1e0f2a3b4c: renamed
```

An added file `creates`; a modified file `modifies`; a deleted file `deletes`
with `Lifecycle: deleted`; a renamed file (Git's rename detection, `-M`)
`modifies` its new path with `Was` the old one. A change of type or mode is a
modification, and a rename whose new file is still untracked is a deletion and
an addition. `Base` is the claim commit (or `--base`) and `Head` the commit
checked out at the close (or `--head`), both in full. A change already in
`Head` also carries `Revision: <Head>`; a change only in the working tree has
no `Revision`, and its `Evidence` says "the working tree at". Paths are
relative to the Praukron project root; in a repository where that root is a
subdirectory, only changes below it are captured.

Not recorded: anything Git's ignore rules ignore (`.gitignore`,
`.git/info/exclude`, `core.excludesFile`), a tracked file they match
included; anything under `.praukron/`; and a path a record cannot hold (not
UTF-8, leading or trailing whitespace, a line break or a backslash), which the
reply lists in `skippedPaths`. Besides Git's rules, Praukron keeps one short
built-in list, applied to untracked files only, so that a repository with no
ignore file still keeps caches out of a capture: a file under a directory
named `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox` or
`node_modules` at any depth, and a file named `.DS_Store` or `Thumbs.db` or
ending in `.pyc` or `.pyo`. The reply's `capture` member gives how many
untracked files the list left out as `untrackedLeftOut`, omitted when none
were. The list holds nothing else (`build`, `dist`, `target` and `vendor` are
source directories in some projects), and a tracked file is always recorded,
whatever its path, unless Git's own rules ignore it.

A change is attributed to the task only when it lies between `Base` and
`Head`, the working tree included. When another task may also have made it,
Git alone cannot tell the two apart, and the record says so instead of
guessing: `Lifecycle: unresolved` and `Verification: unverified`, never
`verified`, and `Evidence` ends `unresolved attribution: <task> was also
claimed over this range`. Another task overlaps
when it is `WIP` or `BLOCKED` with a `Claimed at` that is an ancestor of, or
equal to, `Head` (then every file of the capture is unresolved), or when it is
`DONE` and its own captured records end inside this range, that is their
`Head` is after `Base`, or is `Base` with no `Revision` (then only the files it
recorded are unresolved). A task with no `Claimed at` overlaps nothing. This
rule holds for a close and for `scope capture` without `--head`, which read the
working tree, where several tasks may share one directory. When `scope capture`
names both `--base` and `--head`, the caller vouches that the range is the
task's own: no overlap is looked for, no record is unresolved, each record's
`Evidence` says `range named by the caller`, and the reply's `capture` member
has `rangeNamed: true`. A coordinator whose builders work in other worktrees
claims their tasks at one commit, so their claims overlap; it captures each
merged task this way, naming that task's own merged range (T-CIL-SCOPE-06).

**Merged-in work (T-SCOPE-ATTRIB-01).** The same two captures, a close and
`scope capture` without `--head`, also read the range's shape from Git's
ancestry alone: the first-parent line of `Base..Head` is the task's line of
work, and a commit that entered the range only through a merge's second or
later parent is another line's. Every file and symbol whose path such a
commit changed (the merge commit's own resolution not counted) is recorded
`Lifecycle: unresolved` and `Verification: unverified`, whether or not any
other task is claimed, and its `Evidence` ends `unresolved attribution:
changed by commits merged in through <merge>, …, not on this range's
first-parent line`, naming each merge by its first twelve hex characters. A
path changed only by first-parent, non-merge commits, or only in the working
tree, is recorded as above, unless the rule below makes it unresolved.
Commit messages and authors are never read. A named capture is unchanged:
merges or not, its records are verified.

**Commits this checkout did not make (T-SCOPE-REFLOG-01).** Work of others
that arrives with no merge commit (a squash or rebase merge pulled by
fast-forward, `git pull --rebase`, `git reset` to a remote tip, a checkout)
lies on the first-parent line, and Git's shape cannot tell it apart. The
checkout's own HEAD reflog can: the same two captures read it with Git (`git
log --walk-reflogs` on `HEAD`; a linked worktree reads its own), never from
files under `.git`. A non-merge commit of the first-parent line is made here
when a reflog entry whose action creates a commit moved HEAD to it: `commit`,
`commit (initial)`, `commit (amend)`, `commit (merge)`, `cherry-pick`,
`revert`, `am`, and a rebase step that writes a commit (`pick`, `reword`,
`edit`, `squash`, `fixup`, `continue`) of a `rebase` or of a `pull` that
rebases (`pull --rebase (pick)`). So a task's own commits replayed by a rebase
here count, and the commits they were replayed onto do not. Any other action,
an unknown one included, creates nothing. Git writes these actions in English
whatever the locale; a translated one would create nothing, so it can only
make a record unresolved. Every file and symbol whose path a commit not made
here changed is recorded `Lifecycle: unresolved` and `Verification:
unverified`, and its `Evidence` ends `unresolved attribution: changed by
commits this checkout's HEAD reflog does not show as made here: <commit>
(<how>), …`, naming each commit by its first twelve hex characters and how it
arrived: `arrived by` and, in backticks, the kind of the reflog entry that
brought it, from a fixed vocabulary read from its action's first word
(`pull`, `fetch`, `merge`, `reset`, `checkout` for a checkout or switch,
`rebase`, `clone`, or `other`). Never the action's arguments nor the entry's
message: they can hold a remote URL with a credential, a private path or a
branch name, and `CODE.md` is committed. Or it says `the HEAD reflog could not show
where it was made: <why>` when the reflog cannot account for it: no reflog,
an expired or truncated one, reflogs turned off, a fresh clone, or a commit
already there at the reflog's first entry. Nothing is guessed in either
direction, and a reflog that cannot be read accounts for nothing. A path
changed only by commits made here, or only in the working tree, stays
verified. A named capture does not read the reflog.

When a capture writes an unresolved reference, its reply's `capture` member
carries `unresolved` (the count), `merges` (every merge on the first-parent
line, short ids) and `mergesInvolved` (those that brought in a recorded path),
`notMadeHere` (every first-parent commit not made here, short ids) and
`notMadeHereInvolved` (those that changed a recorded path, each `{"commit",
"how"}`), and one `warning` sentence covering every cause: the references are not attributed to the task, and
they are settled by a named capture over the task's own range or by `ref set`
on each record. The write still succeeds; capture never blocks it. A listing
never presents an unresolved observed reference as a plain reference of its
task: `refs`, `why`, `engine map`, `engine impact` and the task packet
(`context`, `retrieve`)
mark it `"attribution": "unresolved"` in JSON and `UNRESOLVED ATTRIBUTION` in
text, give their count (`counts.unresolved` in `refs` and the packet, present
when not zero; `unresolved` in `why`; `attributionUnresolved` in `engine
map` and `engine impact`), and mark a task that reaches the target only through such references
with `attributionUnresolved: true`.

Settling. A named capture over the task's range writes, for each file or
symbol it records with the same relation, a record that `Replaces` the
unresolved one; its reply lists those as `settled` and what stays unresolved as
`stillUnresolved`. `ref set` with `set.lifecycle` other than `unresolved`
settles one record: it needs `set.evidence` (`settle-without-evidence`), and is
written as a successor that `Replaces` the record, which stays unchanged as
history, with the record's `Source` and the new `Recorded`. `ref set` never
sets an unresolved observed reference `verified` while it stays unresolved
(`unresolved-attribution`); `withdraw` works as for any reference. `validate`
warns once per task (`unattributed-verified-references`), never errs, about
current `git_diff` or `static_analysis` references an earlier capture wrote
with `Lifecycle: unresolved` and `Verification: verified`, with their count
and the two ways to settle them.

The limits that stay, settled by a named capture over the task's own commits
or by `ref set`: a foreign patch applied and committed here by hand (`git
merge --squash` then `git commit`, `git cherry-pick`, `git am`, `git commit
--amend` of a commit made elsewhere, a `git stash pop` of others' changes
then committed or left in the working tree) counts as made here; and a range
the reflog cannot account for is unresolved even when it is the task's own,
as is a task claimed in one checkout and closed in another, whose commits the
closing checkout's reflog does not show as made there.

**Observed symbols, from the standard library's `ast` (ADR-092).** The same
capture, in the same write, records the changed symbols of every changed
Python file (`.py`, `.pyi`). The old text is read at `Base` and the new text
at `Head`, or in the working tree when the capture runs to it; both are
parsed, never imported or run. The changed lines on each side come from one
`git diff -U0` for the capture's Python files, compared as text with hunks
never merged across unchanged lines, so the cost grows with the change and not
with the file; an added file's lines are all new and a deleted file's all old.
A function, async
function, class or method whose source range, its decorators included,
holds a changed line on its side is changed: present only in the new text it
`creates`, only in the old text it `deletes` with `Lifecycle: deleted`, in
both it `modifies`. A class whose method changed is itself changed. Its
`Symbol` is the qualified name inside the file (`Ledger.post`, nested names
joined with `.`), its `Kind` `function`, `method` (defined directly in a
class body) or `class`; a name defined twice in one scope, such as a property
and its setter, is one symbol whose range spans both.

```text
## CR-T-PAY-03-05: src/pay/ledger.py#Ledger.post (modified)
- Task: T-PAY-03
- Path: src/pay/ledger.py
- Symbol: Ledger.post
- Kind: method
- Relation: modifies
- Scope: observed
- Verification: verified
- Authorization: not_applicable
- Source: static_analysis
- Recorded: 2026-11-26 · claude/primary
- Revision: 9c1e0f…
- Base: 286b9a7…
- Head: 9c1e0f…
- Lines: 12-19
- Evidence: git diff 286b9a7c0d1e to 9c1e0f2a3b4c: method Ledger.post modified (Python ast)
```

`Base`, `Head`, `Recorded`, `Revision` and unresolved attribution follow the
file's rules, with one difference: a deleted symbol's name is read from the
base, so its `Revision` is `Base`, and in a renamed file its `Path` is the old
path. Every recorded name is therefore in the source at the recorded
revision, or in the working tree when there is no `Revision`. `Lines` is the
range on the side the name was read from, a hint only. A renamed symbol is
one deletion and one creation. A symbol already recorded as observed for
the task with the same path, name and relation is counted in
`alreadyRecorded` and not recorded again.

A symbol is never recorded without a real name. A changed file whose symbols
cannot be resolved keeps its file record, whose `Evidence` ends `symbols
unresolved: <reason>`, and the reply lists it in `symbolsUnresolved` with
the reason: any file but Python (`no deterministic symbol extractor for this
file type`); a Python file that does not parse on a side it needs (`does not
parse: <message> at line <n> at the base revision` or `at the head`); a file
larger than 524288 bytes (512 KiB) on a side it needs, which is never read
(`file larger than 524288 bytes`); every Python file of a capture with more
than 200 changed Python files, none of which is read; the files past the
capture's budget of 4194304 bytes (4 MiB) of Python source, both sides of
every file counted (the text at `Base` and the text at `Head` or in the
working tree): files are taken in path order, and once the next file would
pass the budget, it and every later file are not read (`the capture's symbol
budget of 4194304 bytes of Python source is spent`); the files past the
capture's cap of 200 symbol records, applied after the size and byte limits
in path order: a file's symbols are written all or none, and once a file's
changed symbols would pass what is left of the cap, it and every later file
get no symbol record (`the capture's cap of 200 symbol records is reached`,
and for the first file that did not fit `: this file has <n> changed symbols
and <m> records were left`). File references are not capped; and, when extraction
fails, `symbol extraction failed: <error>`; a changed text for which Git reports
no changed line (`git reported no changed lines for a changed text`). Only
changed files are read, and sizes are taken before any text: three Git calls
(`cat-file --batch-check`, `cat-file --batch`, then one `diff -U0`) whatever
their number, and the working tree from disk. The reply's `capture` member also
gives `files` and `symbols`, the records of each kind it wrote, besides
`references`, their total.

Measured on 2026-11-26 (Python 3.14, macOS, median of nine runs): closing a
task with fifty changed Python files, each of three classes of five methods
and ten functions with ten of its symbols changed, takes 0.23 s and
writes 550 records (261 KB of `CODE.md`); capturing the same fifty files
without their symbols takes 0.08 s and writes 50 records (21 KB). Symbol
extraction is 0.10 s of the difference; the rest is writing and validating 500 more
records. One file of 6,000 small functions (192 KiB) changed in one line
costs 0.18 s of extraction, and one of 15,700 (510 KiB, just under the
limit) 0.51 s, nearly all of it parsing both sides; a whole close of the
first stays under the three seconds `TestWriteTime` allows. Forty such files
of 200 KiB, each changed in one line, close in 1.5 s: the budget admits ten
and leaves thirty unresolved. The budget bounds reading and parsing (about
2 s for 4 MiB of the densest source) and the cap the records: eight added
files of 512 KiB holding 171,304 one-line functions, which without the cap
would write 85 MB of `CODE.md` in 21 s, close in 2.6 s, and `CODE.md` grows by
5,282 bytes, the eight file records, since the first file alone passes the
cap.

Capture adds observed references only: it changes no planned or earlier
record and writes `Authorization: not_applicable`, never `authorized`. It
never records the same file twice for a task: a file that already has a
current observed record of the task with the same relation is counted in
`alreadyRecorded` and skipped, so a second close, or `scope capture` over a
range already captured, adds only what is new.

`validate` reports a malformed heading or a missing required field (the load
fails, as for every record file); `duplicate-reference`,
`unknown-reference-task`, `invalid-reference-path`, `invalid-reference-value`
(a field outside its allowed values), `symbol-without-kind`,
`authorized-without-authority`, `provider-missing` and `unknown-replaced-reference`
errors; and `repeated-reference` (the same task, artifact, relation and scope
recorded twice and neither replaced) and `possible-secret` warnings.

### 2.10 The code graph

This section defines the optional embedded graph library and its index. It
states only what `main` does. The README and the guide describe the planned
layers; nothing here depends on them.

**Optional.** The library is a native file built from `native/graph/` and
loaded in process with `ctypes` by `praukron engine status|index|export`. It
is looked for at the path in `PRAUKRON_GRAPH_LIB` when that is set and not
empty, and then nowhere else; otherwise at
`.praukron/lib/<platform>/libpraukron_graph.dylib` on macOS or
`libpraukron_graph.so` on Linux. Only the `engine` commands import the loader. Absence is a normal state: every
other command behaves as without the feature, and the `engine` commands say
that no library is present and exit 0. A library that cannot be used is
reported in one line with exit status 1 and changes no record.

**Derived, never authority.** The index is the library's SQLite store at
`.praukron/engine/graph.db`, in a directory whose own `.gitignore` ignores
everything in it. It is derived from the code and can be deleted and rebuilt
at any time. No record cites it. `validate`, `compile`, `context`, `retrieve`
and `refs` never read it, and nothing in the index decides a question a record
answers. `engine export` refuses to write its output inside
`.praukron/chronicle/`.

**Sources kept apart.** The export names its `source`, `graph-library`, and
the library, engine and engine commit it came from. Task–code references
(§2.9) keep their own `Source`. Nothing merges the two: the engine never writes
a record, and a record never takes a value from the index. `praukron engine map
<task|path|path#symbol>` reads the index with `sqlite3` alone, read only, and
labels every item with its source: `engine`, `engine-unresolved`, or
`reference:<Source>`. It writes nothing. Without an index it answers from the
references alone and says that no graph is present.

**Impact.** `praukron engine impact <task>` (ADR-103) reads the index the same
way to answer what a planned change runs into. From the task's current
references it follows resolved edges backwards to the code that calls or
depends on them, to a stated depth (`--depth`, default 2), carrying each
edge's mark on every path; `--exact` follows `exact` edges only. It lists the
tasks whose current references lie on the code reached and their decisions,
frozen criteria and inherited invariants, grouped by decision with the
shortest path to each; the task's own decisions are listed apart. A reference
with no node and an unresolved edge on followed code make further reach
unknown on that item; the answer never presents unknown reach as none.
Without an index it covers only the code the task references and says that
reach through the graph is unknown. It is the list of what the change must be
checked against, not a verdict: it makes no statement that a conflict exists,
asks the reader to record the conclusion, and writes nothing.

**Unknown is not absence.** An edge whose target the engine could not resolve
is exported with `target: null` and the referenced `name`. A reader must treat
it as an unknown relation, never as the absence of one.

### 2.11 Agent authority

One setting says what agents may do on the owner's authority. `praukron
authority` (and `--json`) lists every capability an agent can be given, its
present state, who set it and when; the dashboard's Review tab carries the same
list under **Agent authority**. Capabilities are a table in the runtime
(`authority.CAPABILITIES`), so a new one is a table entry, not a new mechanism.
There are two:

| Capability | Kind | Switched by | What an agent may do under it |
|---|---|---|---|
| `verify` | grant | `praukron authority grant` / `revoke` | record `VERIFY` of an `Authority: owner` task it did not build |
| `assumptions` | policy | the ACCEPTED ADR with `Policy: assumptions` (§2.4) | assume a choice unless its group is reserved |

The assumption policy is only listed here: its `Reserved:` groups and
`Notify:` are read from its ADR, unchanged, and it is changed only by
superseding that ADR.

**Grants.** A capability of kind `grant` is on for a task while a grant in
`AUTHORITY.md` covers it, and off once that grant ends or is revoked. Each
grant is one record:

```markdown
## AG-01: verify for reviewer-agent
- Capability: verify
- Scope: M-CORE, T-EXPORT-01
- Delegate: reviewer-agent
- Granted by: github-login
- Date: 2026-11-27
- Until: none
- Via: cli
```

It names one capability; a scope that is always stated, either `project` for
the whole project or one or more task, module and phase ids (a task is covered
when its id, its module or its phase is named); the delegate, the agent the
grant is for; the granting login, the active `gh` login, read and never typed,
which must be named in an ACCEPTED ADR's `Owners:`; the date; and its end,
`Until: <YYYY-MM-DD>` or `Until: none` for a grant that holds until it is
revoked. `Via` is `cli` or `dashboard`. A revocation adds `Revoked:`,
`Revoked by:` and `Revoked via:` to the grant and changes nothing else, so the
earlier state stays readable. The list makes a grant for the whole project and
a grant with no end stand out. A chronicle without `AUTHORITY.md` is valid and
grants nothing. Grants are never archived.

```sh
praukron authority                                       # the list
praukron authority grant verify --scope T-EXPORT-01 --delegate "reviewer-agent" --until 2026-12-31
praukron authority grant verify --scope project --delegate "reviewer-agent"   # whole project, no end
praukron authority revoke AG-01
```

**Only the owner, in person.** `praukron authority grant` and `revoke` run
only at an interactive terminal, and ask the owner to confirm; the served
dashboard (`praukron dashboard --serve`) offers the same two writes. Nothing
else records or widens a grant: no structured write or batch reaches
`AUTHORITY.md`, a write relayed by an agent is refused, and `validate` reports
a grant whose `Via` is anything but `cli` or `dashboard`. This matters because
the owner's `gh` login is active on the machine where agents run: a relayed
write would carry the owner's name. The interactive check is the one `VERIFY`
uses (both standard input and output are terminals). It does not stop a
program that imitates a terminal, and it does not stop an agent that edits
`AUTHORITY.md` by hand or serves the dashboard itself; the record makes such a
grant visible, as any edit to the chronicle is, but cannot prevent it. A grant
that `validate` rejects (a granter outside `Owners:`, a `Via` other than in
person) never switches a capability on.

**Verifying under a grant.** An agent named by a grant of `verify` in force
that covers an `Authority: owner` task may verify it, unless it built the task:

```sh
praukron respond T-EXPORT-01 VERIFY --delegate "reviewer-agent" < what-i-verified.txt
```

The one who verifies is never the one who built: the write is refused when the
delegate's name equals the task's `Owner:` or appears in it, compared without
case and surrounding space. Names are declared by agents; the record makes a
false name visible and does not prevent one. Without a grant in force, after
its end, or after its revocation, the write is refused with the capability's
name and the command that switches it on; verifications made while it was in
force keep their record. Without `--delegate`, `VERIFY` behaves as before: the
owner, at a terminal or from the served dashboard.

The response keeps `By:` as the active `gh` login and adds who acted
(`Delegate:`), on whose authority (`On authority of:`, the grant's granting
login) and under which grant (`Grant:`), with a `Via:` that starts with
`delegate`, so it cannot be read as the owner's own verification:

```markdown
## R-12: T-EXPORT-01 VERIFY
- By: github-login
- Date: 2026-11-28
- Via: delegate (cli)
- Delegate: reviewer-agent
- On authority of: github-login
- Grant: AG-01

> Ran the export against the staging data; the files match.
```

A delegated `VERIFY` is dated today. The agent then sets the task's
`Validation: DELEGATE_VERIFIED`, a state of its own that no command sets, as
for `HUMAN_VERIFIED`. It satisfies what owner verification is required for: an
`Authority: owner` task may be `DONE` with `HUMAN_VERIFIED` or
`DELEGATE_VERIFIED`, and so may the gate it verifies. It is shown and counted
apart from `HUMAN_VERIFIED` everywhere (`explain`, `context`, `retrieve`, the
dashboard, `status`); validation coverage still counts `AI_REVIEWED` and
`HUMAN_VERIFIED` only. When the owner later verifies in person, `validate`
warns until the task's validation is set to `HUMAN_VERIFIED`.

**Packets.** `context <task>` carries `capabilities`: each capability, whether
it is on for the task, and the grants in force; and, for an owner task,
`task.verifications`, each marked `owner` or `delegate`. `retrieve <task>`
adds the grants in force and the task's `VERIFY` records, each labelled with
who verified on whose authority.

**Validation.** Errors: a grant with an unknown capability, no scope, a scope
naming no task, module or phase, a granter or revoker not named in `Owners:`,
an end before its date, or a `Via` other than `cli` or `dashboard`; a
delegated `VERIFY` whose grant does not exist, grants another capability or
another delegate, does not cover the task, or was not in force on its date
(on the day of a revocation it still counts, since a date cannot order the
two); a delegated `VERIFY` whose delegate built the task; and
`DELEGATE_VERIFIED` without a delegated `VERIFY`.

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

1. `INDEX.md` (run `praukron compile` if it is missing);
2. the task packet, `praukron context <task>`, or the active or selected entry
   in `TASKS.md`;
3. its contract and governing entries in `ADR/`, as the packet lists them;
4. `INTENT.md`; and
5. only the recent journal entries needed for the handoff.

State the current goal and exact next action. Read specifications or code only
after the chronicle points to the relevant work.

### 4.2 Work

Create a task immediately when new work appears if the request has none; do not
wait for a Praukron command. Work on one task at a time. Mark it `WIP`, record its
owner and claim date, and overwrite `INTENT.md` with the exact execution point.
Refresh intent after meaningful progress and before long-running work. Record
tasks, criterion results, task status and journal entries with the structured
write commands (§2.6), not by editing `TASKS.md`, `ACCEPTANCE.md` or `JOURNAL.md`.

Update the chronicle whenever project truth changes. `DONE` means the acceptance
condition is met and evidence is recorded. Validation is separate and uses one
of these values:

- `UNTESTED`
- `SYNTHETIC`
- `AI_REVIEWED`
- `HUMAN_VERIFIED`

Only a named human may record `HUMAN_VERIFIED`. The tool enforces part of
this: an `Authority: owner` task is `DONE` only with `HUMAN_VERIFIED`, and no
structured write sets it. Who the human is stays a rule people follow.

### 4.3 Decide

As soon as a material choice is made, accepted, or acted on, append an ADR with
its date, authority, context, decision, consequences, affected tasks, and any
decision it supersedes. Do not wait for a Praukron command. Never edit or delete
an earlier ADR to change its meaning. Follow the supersession chain for the
current rule.

### 4.4 Checkpoint

Before another person or agent takes over:

1. update task status, validation, evidence, and governing ADRs;
2. refresh the compiled views (`compile && graph && dashboard`);
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

`praukron retrieve`, `praukron refs` and `praukron scope capture` (§2.9), `praukron codegraph` and the structured writes
(`praukron journal append`, `praukron criterion set`, `praukron task add`,
`praukron contract add`, `praukron task update`, `praukron acr add`; §2.6) are commands of the tool,
not workflows; the workflows use them. `praukron archive` (§2.7) is the owner's
command and belongs to no workflow.

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
- `CODE.md` records which code each task planned to touch and was observed to
  touch; a reference is replaced or withdrawn, never deleted, and grants no
  authorization by being observed.
- Capturing a task's files and symbols never refuses or delays a write.
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
