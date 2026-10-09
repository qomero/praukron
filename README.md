<p align="center">
  <img src="docs/assets/praukron.svg" alt="Praukron means Project Chronicle: a common language for people and AI. Shared purpose, historical decisions, current state, and future work connect everyone to the same project story." width="1200">
</p>

# Praukron

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22897871.svg)](https://doi.org/10.5281/zenodo.22897871)

**Praukron keeps people and AI agents working from the same project state.**

A repository-native project-state protocol and reference implementation for
asynchronous human–AI software development. Praukron is short for Project
Chronicle.

Previously published as **Prokron**. Existing DOI identifiers and Git history are preserved.

[The problem](#the-problem) · [Get started](#get-started) ·
[Guide](docs/GUIDE.md) · [Specification](docs/SPEC.md) ·
[Thesis](docs/PRODUCT-THESIS.md)

## The problem

A coding session ends. The next one starts somewhere else — a new session, a
different model, another person, a parallel branch. The code survives. What the
code meant does not.

The next participant cannot tell from the repository which task was half
finished, which decision was reversed last month and why, what has to happen
before this work can start, or what would have to be true for anyone to call it
done. So they reconstruct it: read the diff, guess the intent, ask someone, or
quietly redo a decision that was already made.

This is usually described as an AI memory problem. It is not, or not only.
A single agent remembering its own conversation does not help the person
reviewing the work, the second agent picking it up, or the teammate who joins in
March. The useful question is not *what should this agent remember* but **what
should the project remember** — and the answer has to be readable by everyone
who touches it.

Praukron's answer: keep that in the repository, as Markdown, and compile it.

## One state, two readers

Your project's phases, tasks, dependencies, decisions and definition of done
live as Markdown inside your repository. A small deterministic tool compiles
those documents into project state, and that state is then projected two ways.

A person opens a page:

<p align="center">
  <img src="docs/assets/dashboard-state.png" alt="The dashboard's Overview tab for an illustrative expense-tracking project: a focus strip naming current phase P2, the task in flight, the main blocker and the next gate with one gate green and one red; a line showing an operations task holding up a product task; execution progress metrics; and three phase cards with the current phase highlighted." width="900">
</p>

<p align="center"><em><code>praukron dashboard</code> on Ledgerly, an illustrative project — the expense tool from the story below.<br>
Every figure is computed from the Markdown files in <code>.praukron/chronicle/</code>. No model, no service, no network.</em></p>

An agent asks for a packet, and gets the same state as structured text:

```console
$ praukron context T-PILOT-01
{
  "role": "builder",
  "packetFor": "T-PILOT-01",
  "phase": { "id": "P1", "status": "COMPLETE", "outcome": "..." },
  "task": {
    "id": "T-PILOT-01",
    "status": "DONE",
    "validation": "HUMAN_VERIFIED",
    "dependencies": [ { "id": "T-READINESS-01", "done": true } ]
  },
  "blockers": [],
  "acceptance": [
    { "id": "AC-T-PILOT-01-01", "class": "RUNTIME", "state": "PASS",
      "text": "Given a disposable project and a small specification, When ..." }
  ],
  "decisions": [ { "id": "ADR-008", "title": "Make Praukron an agent working convention" } ],
  "handoff": "..."
}
```

Same project. Different interface. A project meeting and an agent session do not
need two versions of reality, and neither side has to translate for the other.

That is also the whole of it: **Praukron owns project understanding, not project
execution.** Claude, Codex, a person in an editor — they do the work. Praukron
keeps the record that lets each of them know where they are.

### A project story a newcomer can follow

A team is building an expense tool for freelancers. Months in, a new teammate —
or an AI advisor — asks why bank synchronization is absent:

| Question | Recorded answer |
|---|---|
| Why are we building this? | Help freelancers prepare expense records without maintaining a spreadsheet. |
| What did we originally choose? | `ADR-002` proposed bank synchronization to reduce manual entry. |
| Why did the direction change? | `ADR-007` superseded it: launch with CSV import, because supported banks did not cover the first users. Revisit when coverage improves. |
| Where is the project now? | Import is complete; duplicate detection is in progress; validation evidence is linked from the tasks. |
| What comes next? | Finish duplicate detection before starting monthly summaries. The task graph records that dependency. |

The teammate can explain the tradeoff. The advisor can question whether the
constraint still holds. The coding agent can choose work consistent with the
current decision. Nobody has to reconstruct it from commit messages.

*Illustrative example; these are not claims about a deployed project.*

## What the project remembers

Each record answers one question a project keeps being asked.

| The question | What answers it | Where it lives |
|---|---|---|
| What are we trying to do right now? | **Intent** — zero or one active task, at its exact stopping point | `INTENT.md` |
| Why does the project exist? | **Thesis** — the statement every phase, module, and task descends from | `THESIS.md` |
| What work exists? | **Tasks** — module (and through it, phase), owner, status, validation, evidence | `TASKS.md`, `MODULES.md` |
| What has to happen first? | **Dependencies**, resolved into a graph of ready and blocked work | computed from `TASKS.md` |
| What counts as done? | **Acceptance** — a frozen contract per task, criterion by criterion | `ACCEPTANCE.md` |
| What phase are we in, and what must be true to leave it? | **Phases** and **gates** — outcome, entry, exit, exit authority | `PHASES.md` |
| What is stopping progress? | **Obstacles**, computed — dependency, acceptance, gate, phase, validation | computed |
| Why did we choose this? | **Decisions** — append-only, superseded rather than rewritten | `ADR/` |
| What happened recently? | **Journal** — progress, what was left mid-air, and why | `JOURNAL.md` |
| Is this product work or support work? | **Domain** — execution advances the project; operations maintains its environment and never counts as progress | `TASKS.md` |
| What ran, failed, or changed along the way? | **Trace** — tool calls, mini-actions, mutations, failures, retries | `TRACE.md` |
| What are we knowingly leaving behind? | **Technical debt** — cost, trigger, exit condition, and the decision that created it | `TECH_DEBT.md` |
| Where should an agent start? | **Index** — generated: what matters now and where each record lives | `INDEX.md` |
| What does the next participant need? | **Handoff** — the baton, overwritten each checkpoint | `HANDOFF.md` |
| Why do we believe any of this? | **Evidence**, recorded against the criterion it satisfies | `ACCEPTANCE.md` |

Nothing here is a status report someone wrote by hand. It is computed from the
authored documents, so it cannot drift away from them.

<p align="center">
  <img src="docs/assets/dashboard-next.png" alt="The Execution tab: one task in flight, nothing ready, obstacles grouped into acceptance, gate, phase and dependency blockers with the transitively blocked ones collapsed, execution failures linked to their operational events, and the critical path as a vertical chain with an operations task marked as an external blocker." width="900">
</p>

<p align="center"><em>What is in flight, what is ready, what blocks the project, and what lies on the critical path.<br>
Obstacles are grouped by kind, and an operations task that holds up the path is named as an external blocker rather than joining it.</em></p>

<p align="center">
  <img src="docs/assets/dashboard-operations.png" alt="The Operations tab: summary counts of active and blocked operations, events, failures, retries and mutations; an open operations task that blocks execution, expanded to show its tool call and its failed, unresolved command; and a list of failures and warnings, each linked to the task it came from and the work it affects." width="900">
</p>

<p align="center"><em>Operations has its own tab: the support work around the project, the tool calls and commands behind it, what failed, and what was retried.<br>
None of it counts as progress. All of it stays reviewable, because it can explain why execution is stuck.</em></p>

## Done is a contract, not an opinion

A task is not done because someone says it is done. It is done when every
mandatory criterion of its contract holds and its evidence is recorded.

Each criterion is written as Given / When / Then, carries an evidence class
(`TEST`, `MUTATION`, `INSPECTION`, `RUNTIME`, `MANUAL`), and holds a state. A
contract freezes the moment its task starts. Changing it afterwards takes an
Acceptance Change Request, not a quiet edit — and a reviewer's preference is not
an acceptance criterion.

Completion and confidence stay separate. A task can be `DONE` and still be
`UNTESTED`; only a named human may record `HUMAN_VERIFIED`, and only against
manual evidence.

<p align="center">
  <img src="docs/assets/dashboard-contract.png" alt="A task drill-down for T-203, Detect duplicate transactions, in progress: its dependency, the tasks it blocks, three acceptance criteria marked PASS, FAIL and NOT_RUN with their evidence classes, its acceptance obstacle, its domain, and the three operational events recorded against it." width="640">
</p>

<p align="center"><em>Click any task for its contract, its criteria, and the evidence behind each one.<br>
One criterion passes, one fails, one has not run — and the test runs and the fix recorded against the task sit beside them.</em></p>

That last part matters more than it looks. When two people — or two agents —
disagree about whether something is finished, the contract makes the
disagreement **decidable** instead of an argument. Praukron ships an explicit
arbitration order: product authority, then the contract, then invariants, then
accepted decisions, then reproducible evidence, then existing convention, and
only last, reviewer preference.

Taken together, that is as much governance as Praukron has, and it is deliberately
small: decisions are explicit and dated, dependencies are visible, completion has
conditions agreed in advance, conflicting evidence can be surfaced and settled by
a stated order — and no single participant owns the project's context.

### What it refuses to make up

A project tool that invents numbers is worse than no tool. Praukron reports a
duration only when someone recorded one, a date only when someone set one, and
`UNKNOWN` the rest of the time. Dependency ordering and calendar scheduling are
deliberately kept in separate views so one never quietly becomes the other.

Completion is also kept apart from confidence. The validation table counts how
strongly each finished task is actually backed, which is a different question
from whether it is done.

<p align="center">
  <img src="docs/assets/dashboard-views.png" alt="The Graph tab's task graph: tasks grouped by phase P1, P2 and P3 and coloured by status, with a separate dashed group of project operations tasks, one of which feeds a product task, and the six view selectors above." width="900">
</p>

<p align="center"><em>Six views of the same state: task graph, critical path, phases, gates, dependency ordering, and calendar Gantt.<br>
Product work is grouped by phase; operations sits apart in dashed nodes, joined only where execution waits on it.</em></p>

## Get started

### 1. Install in your project

From the root of your project:

```sh
curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh | sh
```

Read the script before you pipe it to a shell, as you should with any installer
delivered this way.

The installer adds one directory, `.praukron/`, holding the chronicle, the
compiled views, the tool itself and the portable workflows. Outside it, it
writes only what an agent host reads by fixed address: `AGENTS.md`,
`CLAUDE.md`, and the command files for Codex, Claude Code, and OpenCode. It
preserves existing records and custom instructions, restores missing files, and
prints what to run next.

It also puts a small `praukron` launcher in a directory already on your `PATH`,
so the command is `praukron` from anywhere in the project. The launcher runs
each project's own copy, so two repositories on different releases stay
independent. It creates no directories, changes no shell configuration, and
never replaces a `praukron` it did not write — if there is nowhere to put it,
the installer says so and `.praukron/praukron` works exactly the same. Pass
`--no-link` to skip it.

<details>
<summary>Starting a new project, installing from a checkout, or using the GitHub CLI</summary>

Installing defaults to `existing`, which records from now on. A **new project**
starts from your product specification instead:

```sh
curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh | sh -s -- new
```

From a downloaded or cloned Praukron checkout, installation works offline:

```sh
sh ./install.sh /path/to/your/project
```

With an authenticated [GitHub CLI](https://cli.github.com/):

```sh
gh api -H 'Accept: application/vnd.github.raw+json' 'repos/qomero/praukron/contents/install.sh?ref=main' | sh
```

Every form takes an optional `new` or `existing`, an optional target
directory, and `--no-link`. A download installs the latest published release;
`--ref <tag|branch>` picks another, and `--allow-downgrade` permits installing
an older runtime over a newer one.

</details>

### 2. Start in your agent chat

| Agent host | Existing project | New project |
|---|---|---|
| Codex | `$praukron init existing` | `$praukron init new` |
| Claude Code / OpenCode | `/praukron-init existing` | `/praukron-init new` |
| Other capable coding agents | Read `AGENTS.md`, then follow `.praukron/commands/praukron-init.md` in existing mode. | Same instruction, in new mode. |

**New project:** the agent works from your product specification with you to
derive the initial phases, tasks, dependencies, and contracts.
**Existing project:** the chronicle starts empty and records from now on.
Earlier history is reconstructed only if you explicitly ask; Praukron does not
invent it. A populated chronicle is preserved if you initialize again.

### 3. Work normally

Ask for the work you want. The installed rules instruct the agent to create the
task before implementing, write its contract, append a decision when one is
made, and keep progress current — without a slash command for every update.

Then check it yourself:

```sh
praukron status
praukron dashboard && open .praukron/compiled/dashboard.html
```

A session then looks like this, and the loop is the point:

```text
you ask for work  →  agent claims a task, writes its contract, implements
                  →  agent records evidence, decisions, and where it stopped
                  →  praukron compile          state is recomputed
                  →  you read the dashboard   the next agent reads the packet
```

Use the chronicle in discussion and review, too: ask why a decision was made,
what changed, or which work actually serves the current goal.

## Asking the project questions

The tool is standard-library Python. No dependencies, no package to install, no
model provider, no network. Output for a given set of documents is identical
every time, which is what lets two agents and a person agree on the numbers.

```console
$ praukron status
Praukron — phase none
  tasks        53 / 53
  acceptance   146 / 146 criteria passing
  validation   20 / 53 reviewed or verified
  gates        6 / 6 green
  P0           14 / 14 · COMPLETE
  P1           10 / 10 · COMPLETE
  P2           17 / 17 · COMPLETE
  no phase     12 tasks

  WIP       none
  Ready     none
  Blocked   none
```

That is this repository at v0.4.6, reporting its own unfinished work.

```console
$ praukron explain T-PILOT-01
T-PILOT-01 — Run the continuity pilot
  phase P1 · DONE · HUMAN_VERIFIED

  Dependencies
    ✓ T-READINESS-01

  Acceptance
    ✓ AC-T-PILOT-01-01 [RUNTIME] Given a disposable project and a small specification, When Praukron is installed in `new` m
    ✓ AC-T-PILOT-01-02 [RUNTIME] Given an ordinary work request carrying no Praukron command, When an agent acts on it, Then
    ✓ AC-T-PILOT-01-03 [RUNTIME] Given a material choice that is later changed, When the agent records it without `/praukron
    ✓ AC-T-PILOT-01-04 [RUNTIME] Given work paused partway, When the chronicle is read, Then state, intent, and journal car
    ✓ AC-T-PILOT-01-05 [RUNTIME] Given a fresh agent session with no prior chat, When it is asked to resume, Then it states
    ✓ AC-T-PILOT-01-06 [RUNTIME] Given a populated chronicle, When initialization runs again, Then tasks, ADRs, and journal
    ✓ AC-T-PILOT-01-07 [MANUAL] Given a person who did not do the work, When they read the same chronicle, Then they can e

  Evidence: All seven criteria pass, recorded in `docs/pilot-2026-09-22.md`. The pilot was run in full against v0.4.0 across two disposable projects: Codex CLI 0.155.1 for install, work without a command, a decision and its reversal, stopping partway, and re-initializing over populated history; a Gemini 3.8 Flash session with no prior chat for a cold resume; and the product owner for the human reading. It supersedes the partial run of 2026-09-21, which was measured against v0.2.1 and cites an entry point and a directory that no longer exist. Two things remain unproven and are recorded as unproven: no host exposed a real limit warning to observe, and nothing was simulated in its place; and step 7 passed on the owner's attestation without a point-by-point account or a comparison against an agent's reading.
  Source:   TASKS.md → T-PILOT-01
```

| Command | Answers |
|---|---|
| `praukron status` | Where the project stands, what is ready, what blocks it. |
| `praukron explain <task>` | Why one task exists, its criteria, blockers, and evidence. |
| `praukron context` | Orientation for a fresh agent: phase, work in flight, ready, blocked, the critical path, and where authority lives. |
| `praukron context <task>` | The minimal packet an agent needs to start that task, with the exact records to read and any references that do not resolve. |
| `praukron domains` | How each task's execution/operations domain was decided, and which need one. |
| `praukron retrieve "<question or id>"` | Only the chronicle records a question needs, routed by `INDEX.md`, each with its source. |
| `praukron codegraph status` | Optional: whether CodeGraph is available to add code structure after the records. |
| `praukron validate` | Broken dependencies, dangling references, authority conflicts. |
| `praukron compile` | Rebuilds `.praukron/compiled/` from the authored documents. |
| `praukron graph` | The six Mermaid views of the same state. |
| `praukron dashboard` | The local page above, in six tabs: overview, execution, graph, governance, decisions, all tasks. |
| `praukron migrate` | Moves a chronicle written under an earlier layout into the current one. |

## How it works

```mermaid
flowchart TD
    P["People: plan, decide, review"] <--> C["PROJECT CHRONICLE: authored Markdown"]
    A["AI agents: implement, record, report"] <--> C
    C --> K["praukron compile"]
    K --> S["Project state: phases, gates, ready work, blockers, critical path"]
    S --> D["Dashboard: the human projection"]
    S --> X["Context packet, project.json: the agent projection"]
    classDef participants fill:#192b38,color:#f3f6f7,stroke:#7b919f
    classDef chronicle fill:#203a36,color:#f3f6f7,stroke:#8be0bd
    classDef derived fill:#382e24,color:#f3f6f7,stroke:#efb373
    class P,A participants
    class C,K chronicle
    class S,D,X derived
```

People set direction, decide tradeoffs, and accept or reject completion. Agents
implement, record what they did, and produce evidence. Both maintain the same
files, and the compiler turns those files into the same state for everyone.

Because the compiler is deterministic, two agents reading the same project
return the same answer, and a person checking their work reads the same figures.
That is what "same language" means here: not a shared summary, but a shared
source and a shared way of computing from it.

### What lives where

Everything Praukron installs is inside one directory, and only one part of it is
authoritative.

```text
.praukron/
├── chronicle/   written by people and agents; the only source of truth
├── compiled/    generated; safe to delete and rebuild
├── commands/    the workflows an agent follows
├── runtime/     the tool's own code
└── praukron      the command
```

| File in `.praukron/chronicle/` | What it holds |
|---|---|
| **`ACCEPTANCE.md`** | The bar: what must be demonstrated before work counts as done. |
| **`ADR/`** | The reasoning: append-only decisions and their supersession chain. |
| `THESIS.md` | The purpose: the product thesis every phase, module, and task descends from. |
| `PHASES.md` | The arc: maturity stages, exit conditions, exit authority, and gates. |
| `MODULES.md` | The decomposition: modules, each inside one phase or `P-NONE`. |
| `TASKS.md` | The work: module, domain (execution or operations), owner, dependencies, status, validation, evidence. |
| `INTENT.md` | The focus: zero or one active task and its exact execution point. |
| `HANDOFF.md` | The baton: what the next person or agent needs right now. |
| `JOURNAL.md` | The diary: progress, validation, what was left mid-air, and why. |
| `TRACE.md` | The operations trace: tool calls, commands, mini-actions, changes, failures, retries. |
| `TECH_DEBT.md` | The liabilities: known compromises, what they cost, their trigger, and what would retire them. |
| `INDEX.md` | Generated, never edited: the map an agent reads first, pointing to the records that matter now. |

`.praukron/compiled/` holds the generated views: a state snapshot, the task
graph, a `project.json` for other tools, Mermaid diagrams, and the dashboard.
Delete that directory and `praukron compile` rebuilds it byte for byte. Nothing
in it is authority, and nothing in it decides a question the authored files
answer.

Praukron is built with Praukron; the dashboard images above use an illustrative Ledgerly
chronicle. That chronicle is kept by the maintainers and not published, so a
clone carries the tool and the [chronicle template](templates/chronicle/) but
not the project's working records.

The [specification](docs/SPEC.md) defines the record format, the working
lifecycle, and the invariants. The [product thesis](docs/PRODUCT-THESIS.md) goes
into why the model is shaped this way.

## What is verified, and what is not

**Implemented and tested.** The compiler and its twelve commands, acceptance
contracts with evidence, phases, gates, computed obstacles, the critical path,
the dashboard, the execution/operations split, technical debt, the index and retrieval, optional CodeGraph, and the context packet, assumptions, owner responses and the served review page. 297 unit tests plus an installer
regression suite, and deleting the compiled directory reproduces every generated
file byte for byte:

```sh
rm -rf .praukron/compiled && praukron compile && praukron graph
```

Two different coding agents have audited the same implementation against the
same contract and reached the same verdicts, after a real disagreement that the
arbitration order settled.

**Not yet proven.** Praukron's rules ask an agent to checkpoint before a handoff,
compaction, session end, or any known or estimated context, token, time, rate,
or quota limit. **Praukron cannot read hidden quota counters or guarantee a
final write after an abrupt cutoff.** The
[continuity pilot](docs/SPEC.md#handoff-pilot) has now been run in full against
the earlier Prokron continuity workflow and all seven of its criteria pass
([results](docs/pilot-2026-09-22.md)) — but no host exposed a real limit
warning to observe, and nothing was simulated in its place. Checkpointing
against an actual quota boundary is therefore still untested rather than
passed. The human reading also passed on the owner's attestation, without a
point-by-point comparison against an agent's account of the same chronicle.

**Designed, not built.** GitHub-account-based owner identity and interactive
verification remain planned. This release records owner guidance; it does not
claim those identity checks are implemented.

Run the checks yourself from a checkout:

```sh
sh tests/install.sh
python3 -m unittest discover -s tests
```

## Commands

Use these when you want to invoke a workflow explicitly.

| Purpose | Claude Code / OpenCode | Codex |
|---|---|---|
| Initialize | `/praukron-init [new\|existing]` | `$praukron init [new\|existing]` |
| Start or continue a task | `/praukron-work [task]` | `$praukron work [task]` |
| Record or supersede a decision | `/praukron-decide [decision]` | `$praukron decide [decision]` |
| Save a handoff | `/praukron-checkpoint` | `$praukron checkpoint` |
| Recover current work | `/praukron-resume` | `$praukron resume` |
| Record decisions an existing codebase already depends on | `/praukron-baseline` | `$praukron baseline` |

All workflows also install as portable Markdown prompts in
`.praukron/commands/`, which any capable agent can follow directly.

### Use the model you prefer

Praukron configures the **agent host** that reads files and does the work. GLM,
MiniMax, Mistral, Grok, and other models use the same chronicle through a
compatible host; provider setup and model selection stay with that host.

Codex receives a project skill. Claude Code and OpenCode receive project
commands. Hosts that load [`AGENTS.md`](https://agents.md/) can follow the shared
rules; for other capable agents, ask them explicitly to read it and follow the
relevant file in `.praukron/commands/`.

For OpenCode setup, see its [providers](https://opencode.ai/docs/providers),
[instructions](https://opencode.ai/docs/rules/), and
[custom commands](https://opencode.ai/docs/commands/) documentation.

## Releases

The current release is v1.2.0. `VERSION` holds it and [`CHANGELOG.md`](CHANGELOG.md) summarises
what changed. Every release from v0.4.6 is archived and citable:
[`10.5281/zenodo.22897871`](https://doi.org/10.5281/zenodo.22897871) always resolves to the latest one, and
[`CITATION.cff`](CITATION.cff) is what GitHub's *Cite this repository* reads.

### Upgrading from an earlier layout

Prokron v0.2 moved authority out of `.prokron/` and into `prokron/` at the repository
root. Prokron v0.3 moved everything it owns back inside `.prokron/`, as
`chronicle/` and `compiled/`. Either way your records are intact but in the old
place, and the tool reports an empty project until you move them:

```sh
praukron migrate           # shows what it would do
praukron migrate --apply   # performs it
```

Coming from v0.2 this is a relocation: every record moves byte for byte and
nothing is rewritten. Coming from v0.1 it is a rewrite — prose acceptance
becomes contracts and one decisions file becomes an ADR directory — so the
originals are archived untouched beside the new ones.

### Updating an installation

Run the same install command again. It installs the latest release and
never touches a chronicle record. For guidance — the workflows in
`.praukron/commands/`, the Claude Code and OpenCode commands, the skill,
`.praukron/chronicle/README.md`, and the Praukron block in `AGENTS.md`:

- anything unedited since the last install is replaced with the new version;
- anything you edited is kept exactly, and the new version is written beside
  it under `.praukron/upgrade/`. Merge what you want, then delete that folder.

The installer lists every file it replaced or staged. It will not install an
older runtime over a newer one unless you pass `--allow-downgrade`, and
`--ref <tag>` installs a specific release.

The rename uses `praukron`, `.praukron/`, and the `praukron` Python package.
Upgrade an existing Prokron installation with the Praukron installer: its
chronicle moves unchanged, and a compatibility alias keeps old paths and
launchers usable. Edited guidance stays intact; review the new guidance staged
under `.praukron/upgrade/`. If both `.prokron/` and `.praukron/` already exist
as separate installations, reconcile them before upgrading.

Published tags from before v1.1.0 still contain Prokron. Use `--ref v1.1.0`
for the first Praukron release; the new installer refuses a pre-rename source
archive rather than reinstalling the old product name.


## Documentation

| Document | What it covers |
|---|---|
| [`docs/GUIDE.md`](docs/GUIDE.md) | Using it day to day: every command, the agent workflows, and what to do when something looks wrong. |
| [`docs/SPEC.md`](docs/SPEC.md) | The record format, working lifecycle, invariants, scope, and the continuity pilot. |
| [`docs/PRODUCT-THESIS.md`](docs/PRODUCT-THESIS.md) | Why shared project state, and how the model is built. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How a change is accepted here: a task, a contract, evidence. |
| [`SECURITY.md`](SECURITY.md) | What is in scope, and how to report privately. |
| [`templates/chronicle/README.md`](templates/chronicle/README.md) | The read order and working rules an agent follows, installed as `.praukron/chronicle/README.md`. |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each release. |

## Help improve it

Try the [continuity pilot](docs/SPEC.md#handoff-pilot) in a disposable project.
When reporting a gap, include the host and model, the request, what was
recorded, and what a person or agent could not understand from it. Remove
private project details before sharing.

Changes should keep Praukron small and readable.
[`CONTRIBUTING.md`](CONTRIBUTING.md) describes how a change is accepted — a
task, a frozen contract, and evidence, which is the same workflow the tool
installs. The [specification](docs/SPEC.md) defines the working agreement and
scope.

## Qomero

Built by Qomero, and used to run Qomero's own projects. Praukron is the record of
how we think a human and an AI should share one project.

## License

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[TRADEMARKS.md](TRADEMARKS.md).
