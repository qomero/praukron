# Using Praukron

A working guide: the commands, the workflows your agent runs, and what to do
on an ordinary day. The [README](../README.md) explains why Praukron exists and
[`SPEC.md`](SPEC.md) defines the records; this is how you drive it.

Every command and flag below was run against a real installation before it was
written down.

---

## Install

From the root of your project:

```sh
curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh | sh
```

That adds one directory, `.praukron/`, and the files an agent host reads by a
fixed address — `AGENTS.md`, `CLAUDE.md`, and command files for Codex, Claude
Code and OpenCode. It also puts a `praukron` launcher on your `PATH`, so the
command works from any subdirectory.

| You want | Run |
|---|---|
| An existing project, recording from now on | `… \| sh` |
| A new project, from a product specification | `… \| sh -s -- new` |
| Install into another directory | `sh install.sh /path/to/project` |
| Skip the `PATH` launcher | `… \| sh -s -- --no-link` |
| A specific release, or unreleased `main` | `… \| sh -s -- --ref v0.5.0` · `--ref main` |
| Go back to an older release on purpose | `… \| sh -s -- --ref v0.4.6 --allow-downgrade` |

Without the launcher, every `praukron` below is `.praukron/praukron`. Both always
work; the launcher just saves typing.

The one-line install fetches the latest published release, not `main`, and
runs that release's own installer, so everyone who installs on the same day
gets the same thing.

**Upgrading** is the same command. It replaces the tool, preserves every
record, and refreshes the generated views. Guidance — workflows, host
commands, the skill, the chronicle README, and the Praukron block in
`AGENTS.md` — is replaced if you never edited it; if you did, yours is kept and
the new version is written under `.praukron/upgrade/` for you to merge. The
output lists both. It refuses to install an older runtime over a newer one
unless you pass `--allow-downgrade`.

In a Git repository the installer also keeps a marked block in `.gitattributes`
so `JOURNAL.md` and the ADR index merge cleanly when two branches both append
to them.

---

## The commands

### Asking where the project stands

```console
$ praukron status
App — phase P1
  tasks        1 / 2
  acceptance   1 / 2 criteria passing
  validation   1 / 2 reviewed or verified
  gates        0 / 0 green
  P1           1 / 2 · ACTIVE

  WIP       none
  Ready     T-002
  Blocked   none
  Next      T-002 (critical path)
```

`--limit N` caps how many obstacles it prints.

This is the command to run first, every time. If it disagrees with what you
believe, one of the two is wrong and it is worth finding out which.

### Asking about one task

```console
$ praukron explain T-002
T-002 — Next step
  phase P1 · TODO · UNTESTED

  Dependencies
    ✓ T-001

  Acceptance
    ○ AC-T-002-01 [TEST] Given foundation, When extended, Then it holds.

  Evidence: none recorded
  Source:   TASKS.md → T-002
```

`--json` gives the same thing as data. A `✓` is a satisfied dependency, a `○`
is a criterion with no evidence yet.

### Handing an agent exactly what it needs

```sh
praukron context T-002                   # the builder's packet
praukron context T-002 --role reviewer   # the reviewer's packet, with the finding taxonomy
```

Emits the intent, phase, task, dependencies and whether they are met, the
acceptance criteria and their states, inherited invariants, governing
decisions, evidence and handoff — and nothing about any other task. Paste it
into an agent that has never seen the repository and it can start. It also
carries the task's domain, any operations work it waits on, and the trace
events that name it.

```sh
praukron domains          # how every task's domain was decided
praukron domains --json   # the same, for tools
```

Lists tasks as explicit or inferred execution, explicit operations, or
ambiguous, names the ambiguous ones, and says whether `TRACE.md` holds any
tool calls, mini-actions, failures, retries, or mutations. It reads only.

### Starting from the index

```sh
cat .praukron/chronicle/INDEX.md                  # what matters now, and where it lives
praukron retrieve "why is P1 blocked?"            # only the records that question needs
praukron retrieve T-095                           # one task's neighbourhood
praukron retrieve T-095 --code                    # then code structure, if CodeGraph is installed
praukron retrieve T-095 --budget 0                # every record, however large the neighbourhood
```

`INDEX.md` is written by `compile`. Never edit it: edits are overwritten and
never read; `validate` says when it is stale. `retrieve` resolves the ids a
question names — or, for "this task", "the phase", "blocked", "debt", the ones
the index marks as current — and prints each record it needs with its source,
and how little of the chronicle that was. It loads records up to about 4000
tokens (`--budget`), most relevant first, and lists the rest as pointers with
the command that fetches each.

### Archiving a closed phase

```sh
praukron archive P3 --dry-run                     # what would move, and what stays and why
praukron archive P3                               # owner only: move it
```

`TASKS.md` and `ACCEPTANCE.md` only grow. Once a phase is `COMPLETE`, the
owner can move its `DONE` tasks and their contracts, verbatim, into
`chronicle/archive/P3/`. They stay authority: every command reads them as
before, `retrieve` labels them with their new file, and an old
`TASKS.md#T-…` pointer still resolves. A task stays in the working file while
an open assumption, unresolved debt or failure, or an awaiting response points
at it; a contract stays while it holds an inherited invariant. Agents never
run it on their own (ADR-067).

### CodeGraph (optional)

```sh
praukron codegraph status     # available? index healthy?
praukron codegraph setup      # asks, then builds the project-local .codegraph/ index
praukron codegraph setup --wire-agents   # also offers `codegraph install`, which edits user-level agent config
praukron codegraph doctor
praukron codegraph uninit
```

Praukron works the same without it. Give a task `- Files:` and `- Symbols:`
lines to seed its code query.

### Checking and rebuilding

```sh
praukron validate     # authority is internally consistent
praukron compile      # rebuild project.json and the Markdown views
praukron graph        # rebuild the six Mermaid views
praukron dashboard    # rebuild the browsable page
```

`validate` takes `--quiet` for errors only. `compile` takes `--force` to
compile despite validation errors, which you want roughly never. `compile`,
`graph` and `dashboard` also refuse to overwrite views a newer release wrote;
`--force` overrides that too.

`compile` does not write the diagrams or the page — `graph` and `dashboard`
do. After editing the chronicle by hand, the full refresh is:

```sh
praukron compile && praukron graph && praukron dashboard
```

`praukron dashboard --open` opens it in a browser.

The page has seven tabs. **Overview** opens first: the current phase, what is
in flight, the main blocker, and the next gate, then execution progress and
phases. **Execution** holds in-flight and ready work, obstacles grouped by type,
execution failures with the operational evidence behind them, and the critical
path. **Graph** has the six diagrams and the schedule; dashed nodes are
operations. **Operations** is the review surface for support work: open
operations tasks and their traces, failures and warnings, tool calls,
mini-actions, changes, retries, and a timeline. **Governance** has gates and
validation strength. **Decisions** lists every ADR, searchable.
**All Tasks** is the full registry with search and filters. The URL hash names
the tab (`#execution`), so a reload or a shared link opens the same view. The
Light / Dark control remembers your choice in the browser; until you choose,
the page follows your system setting. None of this is project state: the page
still writes nothing.

### Execution and operations

Every task is either **execution** — work that advances the project itself —
or **operations** — upgrading Praukron, CI, tooling, housekeeping, anything that
maintains the environment the project is built in. Declare it on the task:

```text
## T-205: Upgrade Praukron and refresh the dashboard
- Status: TODO
- Module: M-TOOLING
- Domain: operations
```

A task names its module; the module in `MODULES.md` names its phase, here
`P-NONE`. The project reads thesis → phase → module → task.

Progress, phases, gates, the critical path, and the Overview describe execution
only. An operations task that an execution task depends on shows up as that
task's external blocker, labelled as operations, with a link to its trace.

Tasks whose module sits in a phase count as execution without saying so. A phase-independent task
with no `Domain:` is counted as execution and `validate` warns about it.
`praukron domains` lists every task by how its domain was decided and names
the ones that need a `Domain:`.

`TRACE.md` holds operational events — tool calls, commands, mini-actions,
mutations, failures, retries — that an agent records when they could explain a
problem later. They appear under Operations and in the task dialog, and never
count as progress.

### Moving an older installation

```sh
praukron migrate           # report what it would do
praukron migrate --apply   # do it
```

Handles a v0.1 chronicle (a rewrite, originals archived) and a v0.2 one (a
relocation, records moved byte for byte). It exits non-zero when there is
nothing to migrate, which is the normal case.

### Running from elsewhere

`-C /path/to/project` runs as if started there. `--version` reports the
runtime's version, never the version of the project you are tracking.

---

## The workflows your agent runs

These are prompts, not code. Each one is a Markdown file in
`.praukron/commands/`, and any capable agent can follow it directly.

| Purpose | Claude Code / OpenCode | Codex | Anything else |
|---|---|---|---|
| Initialize | `/praukron-init [new\|existing]` | `$praukron init [new\|existing]` | Read `AGENTS.md`, follow `.praukron/commands/praukron-init.md` |
| Start or continue a task | `/praukron-work [task]` | `$praukron work [task]` | `praukron-work.md` |
| Record or change a decision | `/praukron-decide [decision]` | `$praukron decide [decision]` | `praukron-decide.md` |
| Save a handoff | `/praukron-checkpoint` | `$praukron checkpoint` | `praukron-checkpoint.md` |
| Recover current work | `/praukron-resume` | `$praukron resume` | `praukron-resume.md` |
| Record decisions an existing codebase already depends on | `/praukron-baseline` | `$praukron baseline` | `praukron-baseline.md` |

**You will not need these most of the time.** The rules installed into
`AGENTS.md` ask an agent to create the task, write its contract, record a
decision and keep progress current as part of ordinary work. The commands exist
for when you want to invoke a step deliberately — most often `/praukron-resume`
at the start of a session and `/praukron-checkpoint` before you stop.

---

## An ordinary day

**Starting.** Open a session and ask it to resume, or just read the dashboard
yourself. A cold agent should be able to state the goal, the active task, the
governing decisions and the next action before it reads any code. If it cannot,
the chronicle is missing something — that is worth fixing before the work.

**Working.** Ask for what you want in plain language. You should see, before
implementation: a task in `TASKS.md`, a contract in `ACCEPTANCE.md`, and
`INTENT.md` naming that one task. If a material choice gets made along the way,
an ADR should appear without you asking for one.

**Checking.** `praukron status`, or open the dashboard. Click a task to see its
contract, every criterion, and the evidence behind each. `DONE` with a criterion
still `NOT_RUN` is a validation error, and the tool will say so.

**Stopping.** Ask for a checkpoint, or let the rules fire. What you want in
`HANDOFF.md` is the exact stopping point and the next action — not a summary of
the conversation.

**Changing your mind.** Say so. The old ADR stays and a new one supersedes it.
The reason the previous decision was replaced is usually what the next person
actually needs.

---

## When something looks wrong

| Symptom | Cause | Fix |
|---|---|---|
| A new feature seems missing from the dashboard | The page was written by an older release | `praukron dashboard` |
| `status` says views were written by another version | Same | `praukron compile && praukron graph && praukron dashboard` |
| `Refusing to overwrite views written by praukron …` | A newer release compiled them; this installation is older | Reinstall to upgrade; `--force` only if you mean to downgrade them |
| The installer lists files under `.praukron/upgrade/` | You had edited that guidance, so the new version was staged beside it | Merge what you want, then delete `.praukron/upgrade/` |
| `index-stale` or `index-missing` warning | The records changed, or `INDEX.md` was edited by hand | `praukron compile` |
| `debt-trigger-reached` warning | A debt's trigger is reached and no repayment is scheduled | Schedule a task and link it, or record why the debt is still acceptable |
| `ambiguous-domain` warning | A phase-independent task has no `Domain:` | Add `- Domain: execution` or `- Domain: operations`; `praukron domains` lists them |
| A maintenance task disappeared from the Overview | It is declared `operations`, so it no longer competes with product work | Find it under Operations; it appears on the Overview only if execution waits on it |
| `stale-reference` warning | `HANDOFF.md` or `INTENT.md` names a file that moved or was deleted | Update the path in the handoff |
| Merge conflict in `.praukron/compiled/` | Both branches recompiled | Take either side, then `praukron compile && praukron graph && praukron dashboard` |
| Merge conflict in `INTENT.md` or `HANDOFF.md` | Both branches changed current state | Resolve by hand toward the branch whose work is current |
| `praukron: command not found` | No launcher on `PATH` | `.praukron/praukron …`, or reinstall without `--no-link` |
| `No .praukron/ in … or any parent directory` | Running outside an installed project | `cd` into it, or install there |
| `status` reports an empty project | `existing` mode starts empty on purpose | Work normally; it records from now on. To capture decisions the code already depends on, ask for `/praukron-baseline` |
| The compiler refuses to compile | Validation errors | `praukron validate` names the file and anchor |
| The diagram is unreadable | It opened fitted, or the library is unreachable | Zoom, drag, hover to trace a chain; offline it falls back to diagram source |
| Something disagrees with the code | The chronicle records intent, the code records what exists | Reconcile deliberately; do not silently trust either |

---

## Rules worth knowing before you fight them

- **`.praukron/chronicle/` is authority. `.praukron/compiled/` is output.** Delete
  the second and rebuild it; never hand-edit it.
- **A contract freezes when its task starts.** Changing it takes an Acceptance
  Change Request, not an edit. This is the rule that makes "done" mean anything.
- **`DONE` is not `HUMAN_VERIFIED`.** Completion and confidence are separate
  columns, and only a named human can record the top one.
- **Nothing is invented.** No duration unless someone recorded one, no date
  unless someone set one, `UNKNOWN` the rest of the time.
- **Praukron owns project understanding, not execution.** It schedules nothing
  and runs nothing. Your agents and you do the work.

---

## Further

- [README](../README.md) — what it is and why
- [`SPEC.md`](SPEC.md) — records, lifecycle, invariants, scope
- [`PRODUCT-THESIS.md`](PRODUCT-THESIS.md) — the argument behind the design
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — how a change is accepted here
- [continuity pilot](SPEC.md#handoff-pilot) — the procedure for testing a host
