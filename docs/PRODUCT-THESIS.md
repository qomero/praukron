# Praukron product thesis

> We introduce Praukron, a repository-native project-state protocol and
> reference implementation for asynchronous human–AI software development.

This is the most precise definition of Praukron. It is a protocol in a narrow
sense: a set of Markdown records and rules for keeping them, which any person
or agent can follow without the tool. The `praukron` compiler is one reference
implementation of that protocol. It is not a framework that code runs inside,
and it is not a project-management service.

This document explains why Praukron is shaped the way it is. The
[README](../README.md) shows what it does; the [specification](SPEC.md) defines
the records and the working lifecycle. This is the reasoning between them.

It is written in two layers. Layer 1 is the argument, in plain language, and
needs no knowledge of the tool. Layer 2 is how the argument turns into a
deterministic compiler over Markdown.

Every claim here is labelled:

| Label | Meaning |
|---|---|
| **Implemented** | Shipped, tested, and demonstrable with a command in this repository |
| **Designed** | Specified in an internal working document; no code exists yet |
| **Thesis** | A position this project holds, not a capability it claims |

---

# Layer 1 — Why a project needs its own memory

## The practical problem

Work on software now moves between participants constantly: session to session,
model to model, person to agent, branch to branch. Each move loses something
that was never written down.

A concrete version. Claude implements part of `T-31` and the session ends. A
week later Codex opens the same repository. Without a shared record, Codex has
to rediscover what `T-31` means, why it exists, what was already tried, which
dependency actually mattered, which decision was made along the way, and what
is still unfinished. The diff shows what changed. It does not show what was
intended, what was rejected, or what would make the work acceptable.

A human joining in March faces exactly the same wall, with the same workarounds:
read the commits, ask someone, or redo a decision that was already settled.

## Why "AI memory" is the wrong frame

Most answers to this reach for agent memory: give the assistant a longer
context, a vector store, a summary of the last conversation.

That helps one agent. It does not help the reviewer, the next agent, or the
teammate. Memory attached to a participant leaves the project in the same
position every time that participant changes — and now the project has several
private versions of its own history, none of them authoritative.

The question worth asking is different:

> Not *what should this agent remember*, but **what should the project
> remember**.

**Thesis.** Project context should belong to the project, not to any individual
human, model, agent, or session.

Most of a conversation is not worth keeping. What is worth keeping is the set of
consequences a conversation produced: a decision, a reversal, a dependency
discovered, work completed, work abandoned, evidence gathered, a stopping point.
Those outlive the session that produced them, and everyone needs them.

## One project, two interfaces

A person and an agent want the same facts in different shapes.

A person wants to look: current phase, what is in flight, what is blocked, what
was decided recently, how much of this is actually verified. A browsable page.

An agent wants to parse: this task, its dependencies and whether they are
satisfied, its acceptance criteria and their states, the decisions that govern
it, the handoff. A structured packet.

```text
                         PROJECT STATE
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
      Human projection                  Agent projection
      HTML dashboard                    INDEX.md        where to start
      dashboard --serve (local)         context <task>  the packet for one task
             │                          retrieve        only what a question needs
             ▼                                 ▼
          Humans ── owner answers ──►      Agents
                    via respond
```

**Implemented.** `praukron dashboard` writes the first. An agent starts from
`INDEX.md`, the generated routing map that says what matters now and where each
record lives; `praukron context <task>` returns the packet for one task; and
`praukron retrieve "<question>"` returns only the records that question needs,
each labelled with its source, under a token budget, with a list of what it left
out. All of them are computed from the same compiled project, by the same
deterministic code, with no model in the loop.

The human side can also answer. `praukron dashboard --serve` serves the same page
on 127.0.0.1 with its review forms enabled, and the owner's answer reaches the
chronicle through `praukron respond`, the one write route, which validates it and
appends it to `RESPONSES.md`. There is no second store.

**Thesis.** A project meeting and an AI coding session should not require two
different versions of the project. When the dashboard and the packet can
disagree, someone has to reconcile them by hand, and that person becomes the
real source of truth.

## Where governance comes in, and how little of it there is

The word is worth avoiding until the mechanism is visible, because most of what
it suggests is not here. Praukron has no workflow engine, no scheduler, and no
assignment of work. It has one role, the owner, and that role is narrow and
explicit. **Implemented:** a task marked `Authority: owner` is `DONE` only with
`HUMAN_VERIFIED`; a choice the project's policy reserves becomes an
owner-held task or a provisional assumption the owner must answer; the owner
answers (CONFIRM, REVISE, REJECT, GUIDE) through `praukron respond` or the served
dashboard, and no agent edits them; and `archive` runs only when the owner asks.
Who may start a task, in what order, and with which tool is left to the
participants.

What it does have, once the records exist, is a small set of properties that
together make disagreement resolvable:

- decisions are explicit, dated, and superseded rather than rewritten;
- dependencies are declared, so "what can start" is computed rather than argued;
- completion has conditions agreed before the work starts, not after;
- evidence is recorded against the criterion it satisfies;
- when two participants disagree, a stated order decides which authority wins;
- no single participant owns the context.

That is the whole of it. It is lightweight on purpose: a heavier process would
be abandoned, and an abandoned record is worse than none, because people still
trust it.

## What Praukron is not

**Thesis, and a boundary the implementation respects.**

> Praukron owns project understanding, not project execution.

It is not an issue tracker for AI, not an engineering manager, not an autonomous
project manager, not an agent framework, not a model router, not an IDE. It
schedules nothing, assigns nothing, and runs nothing. Agents and people execute;
Praukron keeps the record that lets them know where they are.

A useful test: if a feature would decide *what happens next on its own*, it does
not belong here. If it helps a participant understand what is already true, it
might.

---

# Layer 2 — How the model works

## Authored records, generated views

**Implemented.** There are exactly two kinds of file.

`.praukron/chronicle/` is authored — by people, by agents, by hand. It is the only
source of truth. `.praukron/compiled/` is generated and disposable: delete the
whole directory, run `praukron compile`, and every file comes back byte for byte.

The separation is the point. A generated view can never answer a question that an
authored document answers, so a stale summary cannot outrank the record it was
derived from. It also means the compiler does not rewrite authority to suit
itself: it reads the chronicle and writes `compiled/`. What the tool does write
inside `chronicle/` is a short list, and each entry is validated and governed:

- `INDEX.md`, generated, never authored and never authority (ADR-047);
- the owner's answers, appended to `RESPONSES.md` by `respond` (ADR-055);
- `archive`, which moves a closed phase's records verbatim into
  `archive/<phase>/` and back, only when the owner asks (ADR-067, ADR-070);
- six structured writes, `journal append`, `criterion set`, `task add`,
  `contract add`, `task update` and `acr add`, which check the rules before and after,
  refuse stale revisions, and undo themselves byte for byte on any new
  validation error (ADR-068, ADR-069, ADR-076).

Nothing else is written. People and agents may still edit the Markdown by hand;
the parser stays the single definition of the format.

## A deterministic compiler, not a reasoning system

**Implemented.** The compiler is standard-library Python. No dependencies, no
model provider, and it makes no network requests. The same documents produce
identical output every time. Two things sit beside it. CodeGraph is an optional
external integration (ADR-049): where it is installed, `retrieve --code` adds
code structure after the records, nothing the compiler computes reads it, and
its absence changes no result. And `dashboard --serve` binds a local server on
127.0.0.1, run only on request, to carry the owner's answers. (The page the
dashboard writes embeds its dependency drawing and diagram sources; it needs no
external resources.)

This is a deliberate division of labour. Language models are good at reading a
messy repository and proposing structure; they are bad at being the same twice.
So the model does the semantic work — writing the task, drafting the contract,
recording the decision — and a deterministic harness does the state work:
resolving dependencies, computing what is ready, deciding which gates block which
phase exits, counting what is actually verified.

If the numbers on the dashboard came from a model, two readers could get two
answers, and the shared state would be a shared guess.

## The records, and the questions they answer

**Implemented.** Each record exists because a project keeps being asked one
question.

| Record | Question it answers | Shape |
|---|---|---|
| `THESIS.md` | Why does this project exist? | One thesis statement every phase, module, and task descends from |
| `PHASES.md` | What maturity stage are we in, and what must be true to leave it? | Phases with outcome, entry, exit, exit authority; gates recorded once and referenced |
| `MODULES.md` | What coherent pieces of work deliver each phase? | Modules, each with an outcome, in exactly one phase or `P-NONE` |
| `TASKS.md` | What work exists, and what must happen first? | Tasks with module (the phase comes from it), dependencies, status, validation, evidence, governing decisions |
| `ACCEPTANCE.md` | What counts as done? | One contract per task; criteria as Given/When/Then, each with an evidence class and a state |
| `ADR/` | Why did we choose this? | One file per decision, append-only, superseded rather than edited |
| `INTENT.md` | What is being attempted right now? | Zero or one task, at its exact stopping point |
| `HANDOFF.md` | What does the next participant need? | Overwritten each checkpoint |
| `JOURNAL.md` | What happened, and what was left mid-air? | Append-only, no authority |
| `ASSUMPTIONS.md` | What was assumed because no answer existed, and has anyone confirmed it? | Provisional choices recorded before they are relied on, with status, impact and the tasks that rest on them |
| `RESPONSES.md` | What did the owner say? | The owner's words, append-only, written through `respond`: CONFIRM, REVISE, REJECT or GUIDE |
| `COORDINATION.md` | How are this project's agents run? | Optional standing conventions and one brief per task, kept so coordination survives any session; never archived |
| `TECH_DEBT.md` | What are we knowingly leaving behind? | Cost, trigger, exit condition, and the decision that created it |
| `TRACE.md` | What ran, failed, or changed along the way? | Operational events: tool calls, mutations, failures, retries. Never counts as progress |
| `INDEX.md` | Where should an agent start? | Generated, inside the chronicle; a routing map of what matters now and where each record lives. Never authority |
| `archive/<phase>/` | Where did the finished work go? | A closed phase's tasks, contracts, assumptions, debt, trace events and journal entries, moved verbatim; still authority, still resolved by their old pointers |

Two of those are worth dwelling on.

### Acceptance is a contract, and it freezes

**Implemented.** A task is `DONE` when every mandatory criterion of its contract
holds and its evidence is recorded — not when someone says so.

The contract freezes the moment the task starts. That single rule is what stops
the most common failure in reviewed work: quietly moving the bar to match what
was built. Changing a frozen contract takes an Acceptance Change Request, which
is a visible act. A reviewer's preference is not an acceptance criterion.

Completion and confidence are also kept apart. A task can be `DONE` and
`UNTESTED`. Marking something finished and believing it is solid are different
claims, and collapsing them is how a project ends up confident about nothing in
particular. What the code enforces, and what it leaves to people:

- **Implemented, enforced.** A task marked `Authority: owner` is `DONE` only with
  `HUMAN_VERIFIED` (`validate` reports it otherwise). The structured write
  commands never set `HUMAN_VERIFIED` and never close an owner-held task.
- **A rule people follow, not enforced.** `HUMAN_VERIFIED` is for a named human
  who checked manual evidence. The tool reads Markdown and cannot tell who typed
  the word; that discipline rests on the protocol and on review.

### Decisions accumulate; they do not get rewritten

**Implemented.** An ADR is appended when a material choice is made, accepted, or
acted on. Changing your mind produces a new ADR that supersedes the old one, and
the old one stays. A reader following the chain sees not just the current rule
but the reason it replaced the previous one — which is usually the thing a
newcomer actually needs.

### Execution and operations

**Implemented.** Every task has a domain. `execution` work advances the project;
`operations` work maintains the environment it is built in (upgrading Praukron,
CI, tooling). Progress, phases, gates and the critical path count execution only;
an operations task shows up only as the external blocker of an execution task
that waits on it. `TRACE.md` holds the operational events, such as tool calls,
mutations, failures and retries, that an agent records when they could explain a
later failure. They stay inspectable and never count as progress.

### Assumptions, owner responses, reconciliation

**Implemented** (1.1.0, shared memory and owner review). When a choice has no
answer in the records, an agent follows a fixed order: authority decides it; or
the project's policy reserves it, and the owner is asked through a task; or it is
assumed, and the assumption is written down before anything relies on it. The
owner answers in `RESPONSES.md`. The next agent's packet shows the unreconciled
answers that concern its task, that task's assumptions and its open dependencies
(`status` reports the project-wide count). The agent carries each out in the ADR,
contract, task or code it concerns, then marks the assumption. An open assumption is never presented as decided.

## Bounded cost

**Implemented.** A chronicle grows for as long as a project does, so what an
agent must read and write has to stay bounded by the question and not by the
history. Five mechanisms do it, and none involves a model.

- `INDEX.md` is compact: `validate` warns past about 5,000 estimated tokens. It
  is a warning and not a cap.
- `context <task>` carries one task's neighbourhood: its lineage, contract,
  decisions and evidence, plus the tasks it depends on and the ones it unlocks,
  and not the rest of the project.
- `retrieve --budget` (default 4,000 estimated tokens) bounds the selected record
  bodies, most relevant first, and lists the rest as pointers. It does not bound
  the whole output, and the first record always loads.
- `archive` moves a COMPLETE phase's, or `P-NONE`'s, closed records out of the
  working files: tasks, contracts, assumptions, debt, trace events and the
  journal entries that name them, as verbatim records. `RESPONSES.md`,
  `COORDINATION.md` and the ADRs never move. The owner plans (`archive plan`), applies exactly that plan
  (`archive apply --plan`) and can reverse it (`archive restore`); every move is
  read back, undone if it differs, and recoverable through Git or a byte-exact
  backup. Archived records stay authority and the compiler still reads them, so
  it shortens what an agent reads to edit a working file but not compile time.
  On a real chronicle whose six measured working files totalled 310 KB it cut
  `TASKS.md` by 93% and `ACCEPTANCE.md` by 91% (those files to 49 KB in all), with `compile` and `validate` unchanged within noise (see
  [benchmarks](benchmarks.md)).
- Structured writes cost the size of the record: a payload and a one-line result,
  where a direct edit first reads the file it edits. Measured figures, with their
  method and limits, are in [benchmarks](benchmarks.md).

Not claimed: a faster compile (it still follows the whole chronicle), a smaller
`context` or `retrieve` result (they already select), or figures beyond one
project on one machine.

## Making disagreement decidable

**Implemented.** Two competent reviewers will disagree. The goal is not to
prevent that; it is to make the disagreement resolvable without seniority
deciding it. Praukron ships an explicit order:

```text
1. Product and domain authority
2. The explicit acceptance contract
3. Invariants
4. Accepted decisions in ADR/
5. Reproducible tests and evidence
6. Existing code convention
7. Reviewer preference
```

This has been exercised, not just written down. Two coding agents audited the
same implementation against the same contract. They disagreed on one criterion;
one of them was right, and the disagreement was settled at level 5 by rendering
both variants and looking. The record of that sits in this repository's own
acceptance evidence.

## Evidence, and what code cannot tell you

**Thesis, partly implemented.**

Code shows that something exists. It cannot tell you why it exists, whether it is
still wanted, whether it satisfies the requirement it was written for, or whether
anyone considers it finished. A document has the opposite problem: it describes
what was intended, which the implementation may have quietly left behind.

So these are different things and a project record should not merge them:

```text
WHAT EXISTS  ≠  WHAT WAS INTENDED  ≠  WHAT IS CURRENTLY INTENDED
```

**Implemented today**, the distinction is narrow and concrete: evidence is
recorded against the specific criterion it satisfies, carries a class
(`TEST`, `MUTATION`, `INSPECTION`, `RUNTIME`, `MANUAL`), and is separate from the
validation strength of the task as a whole. The specification states that when a
product document and the chronicle disagree, the agent surfaces and reconciles
the difference rather than choosing silently.

**Designed, not built:** a fuller ladder that separates raw evidence from an
extracted claim, an interpretation, a candidate state and canonical state — so a
project can be adopted from messy existing material without anything becoming
canonical until a human confirms it. That belongs to a later phase and no command
implements it.

## Entry: how a project gets in

**Implemented.** There are exactly two entry modes.

A **new project** starts from a product specification: the agent and the
developer resolve ambiguity, then write the first phases, tasks, dependencies and
contracts before implementation begins.

An **existing project** starts empty, deliberately. Praukron does not scan the
repository and infer past tasks, decisions or intent, because an inferred history
is indistinguishable from a recorded one once it is written down, and it would be
trusted. The chronicle records from now on. Historical reconstruction happens
only if a developer explicitly asks for it.

**Designed, not built.** Meeting projects that arrive in other conditions — an
idea with no repository yet, or a long-running codebase whose documentation is
scattered, stale and contradictory — is specified in an internal working
document. The principle it follows is that the project should not have to
reorganize itself before Praukron can read it, and that nothing extracted from
messy evidence becomes canonical without human confirmation. None of it ships
today.

## What this repository proves about itself

**Implemented.** Praukron tracks its own development in its own chronicle. That
chronicle is kept private by choice (ADR-042): this public repository shows the
product, not the process, so a reader cannot open it, and what it records about
phases, gates and evidence is the maintainers' statement. What a reader can check
is public: the releases and the changelog, the CI runs, the test suite, and the
templates and workflows every installation receives.

**Partly proven.** Whether agent hosts maintain the chronicle across real
multi-session work was measured rather than assumed: the continuity pilot ran in
full against v0.4.1 with three participants across two disposable projects, and
all seven criteria pass. Two things it did not establish are recorded as not
established. No host exposed a real limit warning to observe, so checkpointing
against an actual quota boundary remains untested — Praukron cannot read a
counter it is not shown. And the human reading passed on the owner's
attestation, without a point-by-point comparison against an agent's account.

---

## Related documents

- [README](../README.md) — what Praukron does, and how to install it
- [Specification](SPEC.md) — records, lifecycle, invariants, scope, continuity pilot
- [Chronicle guide](../templates/chronicle/README.md) — the read order an agent follows
