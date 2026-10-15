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
praukron archive plan P3                          # read-only: what moves, what stays and why, and a digest
praukron archive apply P3 --plan <digest>         # owner only: move exactly what that plan showed
praukron archive restore P3                       # owner only: bring the phase's archive back
praukron archive plan P-NONE                      # phase-independent work is archived the same way
praukron archive P3 --dry-run                     # the older one-step form (no digest); --dry-run writes nothing
```

`TASKS.md` and `ACCEPTANCE.md` only grow, and so do the lifecycle files beside
them. Once a phase is `COMPLETE` (or for `P-NONE`, which has no status), the
owner can move its closed records, verbatim, into `chronicle/archive/P3/`. They
stay authority: every command reads them as before, `retrieve` labels them with
their new file, and an old `TASKS.md#T-…` pointer still resolves. Agents never
run these commands on their own (ADR-067, ADR-070). The write commands treat
everything under `archive/` as immutable (`archived-record`), so an agent
cannot edit an archived record; only `restore` brings one back.

**Plan, apply, restore.**

- `archive plan <phase>` reads the chronicle and writes nothing. It lists every
  candidate record as `moves` or `stays` with the reason, the files that would
  change, the recovery mode, and a SHA-256 `digest` over every chronicle file's
  bytes, the phase, the recovery mode and the tool version. `--json` prints the
  same as one line.
- `archive apply <phase> --plan <digest>` takes the shared write lock, plans
  again, and applies only if the digest still matches; otherwise it refuses with
  `stale-plan` and writes nothing, so run `plan` again and read it. A missing
  `--plan` is refused (`missing-plan`). The older `archive <phase>` plans and
  applies in one step with no digest; `--dry-run` shows the plan without writing.
- `archive restore <phase>` returns every record in `archive/<phase>/` to its
  working file, in place of eligibility, which is not checked again. It refuses a
  record whose id already exists in a working file (`id-collision`). `--dry-run`
  shows what would return. A phase that has no archive is `unknown-archive`.
- A phase must be `COMPLETE` (`incomplete-phase`) or `P-NONE`, and must exist in
  `PHASES.md` (`unknown-phase`). `archive` refuses unless the chronicle validates
  cleanly first; `restore` validates the state it would write before writing.
- Success refreshes `INDEX.md`, the compiled views, the graphs and the dashboard.
  If the move succeeded but the refresh failed, the result says `written: true`,
  `refreshed: false` and exits nonzero. The records are already moved: re-run
  `compile && graph && dashboard`, never the archive. A move that cannot be
  verified is undone, byte for byte, before the command returns.

**What moves.** Eligibility is checked once, when `apply` (or the one-step
`archive <phase>`) runs; `restore` never checks it again, and checks only that
no id collides, that the journal order can be kept, and that the restored
chronicle validates. Eligibility looks only at the phase axis: a task's phase comes from its module (`P-NONE` for
phase-independent work), and `Domain: execution` or `operations` never changes
it. No id prefix such as `T-OPS-` means anything to it.

| Record | File | Moves when | Stays when |
|---|---|---|---|
| Task | `TASKS.md` | its phase is the selection and it is `DONE` | an `OPEN` assumption names it, a response to such an assumption awaits reconciliation, a linked debt is not `RESOLVED` or `INVALIDATED`, or an unresolved failure points at it; for `P-NONE` also while a working task depends on it |
| Contract | `ACCEPTANCE.md` | its task moves | a working task still uses it, another contract inherits it, or it holds an inherited invariant |
| Assumption | `ASSUMPTIONS.md` | `CONFIRMED`, `REJECTED`, `REVISED` or `WITHDRAWN`, no response awaits reconciliation, and every task it names is archived or moving | any other status, or a task it names still works. One that names no task is never a candidate |
| Technical debt | `TECH_DEBT.md` | `RESOLVED` or `INVALIDATED`, naming at least one task, all archived or moving | any other status, or a task it names still works; under `P-NONE`, one that names no task is reported as staying |
| Trace event | `TRACE.md` | its task is archived or moving (an event with no task belongs to `P-NONE`) | it belongs to an unresolved failure or retry chain |
| Journal entry | `JOURNAL.md` | it names at least one archivable record, and every record it names is archived or moving | it names nothing, only ADRs, an open or unknown record, or its heading is duplicated |

A record that names tasks of several phases goes to the archive of the latest of
those phases in `PHASES.md` order, with `P-NONE` last. Each file keeps its line
endings and UTF-8, and each archive file takes the line endings of the file the
records came from.

**What is never archived.** `RESPONSES.md` is the owner's words and stays whole:
a response is evidence that an assumption was answered, and nothing may move it
out from under the assumption. Decision records in `ADR/` stay too: they are
the highest-ranked authority an agent reads, and a journal entry that names only
ADRs names nothing to archive. `INTENT.md`, `HANDOFF.md`, `PHASES.md`,
`MODULES.md`, `THESIS.md` and the gates are state, not history, and also stay.

**Destinations.** `archive/<phase>/` receives `TASKS.md`, `ACCEPTANCE.md`,
`ASSUMPTIONS.md`, `TECH_DEBT.md`, `TRACE.md` and `JOURNAL.md` as the plan needs
them; the shared `archive/JOURNAL.order.json` is described below. `restore`
deletes an archive file once nothing is left in it.

**Later references never matter.** A dependency, response or journal entry written after
the archive may point at an archived record. That is allowed and never blocks
the archive or a later restore: the archived record is still authority.

#### Recovery: Git and no Git

`apply` and `restore` print a recovery procedure before they finish, and also
return it in the `recovery` field of `--json`. Which one depends on the
`mode` the plan shows.

- **Git mode** is used when the chronicle is inside a Git work tree, none of its
  paths is ignored, and no checkout conversion could change its bytes
  (`core.autocrlf`, `filter`, `eol`, `text`, `crlf`, `ident` or `working-tree-encoding`
  attributes). The chronicle must be clean first: modified, staged or untracked
  files stop the command with `dirty-chronicle`. Commit them, then apply. After
  the move, undo it according to where it is:
  - *unstaged:* from the project root, `git restore --source=HEAD --worktree --
    <recovery.changedTrackedFiles>`, then delete every file in
    `recovery.createdFiles` (and any archive directory left empty);
  - *staged:* the same with `--staged` added to the restore, and
    `git rm --cached --ignore-unmatch -- <recovery.createdFiles>` before deleting
    them;
  - *committed:* `git revert <archive-commit>`. The suggested commit message is
    `Archive <phase> chronicle records` (`Restore <phase> chronicle records`
    after a restore).

  The two lists are different and must stay apart. `recovery.changedTrackedFiles`
  holds the existing files Git knows, as paths from the project root.
  `recovery.createdFiles` holds the files the move created, which Git does not
  know yet. The plan's top-level `changedFiles` (relative to `chronicle/`) holds
  both, so passing it to `git restore` fails on the new archive paths: never use
  it for recovery. The exact commands, in `recovery.commands.unstaged`, `.staged`
  and `.committed` and printed for POSIX sh and for PowerShell, are the safest
  way; they use the right list in each place.
- **No-Git mode** is used otherwise, including when conversion could change
  bytes. Before writing, the command copies every original file byte for byte to
  `.praukron/backup/<UTC timestamp>-<id>/`, with a `manifest.json`, and prints a
  command that restores them. The backup is kept, and its folder ignores itself in Git.
  Restoring from it puts back the exact bytes, including files the move created,
  which it removes.
- **Interrupted runs.** Both modes write a durable recovery record first. If a
  process dies mid-move, the next write command rolls the files back and reports
  that an interrupted archive was rolled back; try again.

#### Checking an archive with `validate`

<!-- T-ARCH-VERIFY-01-PENDING start: written from the task brief while that PR is
unmerged. Re-check every line of this section against the code, then delete
these markers. -->

There is no separate `archive verify` command: `praukron validate` checks the
permanent invariants of the archives, and never checks eligibility again,
because eligibility is decided once, when a move is made.

- every `archive/<phase>/*.md` parses;
- no record id appears in more than one place across the working files and
  every archive, and no journal heading appears twice;
- every reference (dependencies, `AC:`, `Governed by:`, an assumption's tasks, a
  debt's tasks, an event's task, an id named in a journal entry) resolves across
  the working files and the archives;
- no recovery record is left over (`interrupted-write`);
- an archived record in a directory that is not its own phase (for records that
  belong to one phase) is a warning, not an error.

A dependency, response or journal entry written *after* an archive may point at
an archived record without error. If the move succeeded but a refresh of
`INDEX.md`, the compiled views, the graphs or the dashboard failed, the result
says `written: true`, `refreshed: false` with the error and exits nonzero;
re-running only the refresh completes it, and a second `apply` is never needed.

<!-- T-ARCH-VERIFY-01-PENDING end -->

#### Journal ordering and repair

Eligible journal entries move with the records they name. The shared
`chronicle/archive/JOURNAL.order.json` remembers the order of unique full
headings once entries are separated across files (ADR-072). Commit it with
the archive in Git mode. It is not a disposable compiled view or a cached
plan: eligibility is recomputed. Never hand-edit its JSON. Journal section
bytes, including line endings and trailing blank lines, remain unchanged.

Dates are the primary placement key. An owner may reorder same-date entries
inside one journal: the observed file order wins and the next journal move
updates the sidecar in the same transaction. Cross-file constraints that
cannot reconcile are held for repair. Backdated entries in a file that is
not chronological may return in date order rather than the original file
order; exact section bytes still match. Undated `##` sections sort before
dated entries during placement; keep journal conventions in the preamble.
The byte guarantee covers complete entry sections, not every preamble: when
all entries have left, insertion trims the preamble's trailing line breaks
to two; a headerless working journal can gain `# Journal` on restore.

`praukron validate` warns about malformed/version-mismatched metadata,
missing coverage of cross-file ties, obsolete headings, order disagreements,
and duplicate journal headings. Warnings do not block unrelated record work.
Archive and restore keep only the journal entries whose cross-file order is
unknown, with a reason and repair step; other records and unrelated dates
can move. Restore refuses a heading duplicated in any journal file.

After renaming or deleting a heading, its old sidecar identity is obsolete.
If the affected date is wholly in one journal, its observed order can rebuild
the metadata. On a split date, a heading absent from the sidecar in the
working journal is new and keeps its position relative to its same-date
working neighbours, including entries naming nothing. Only when no known
same-date working heading constrains it does it follow all known ties.
An absent heading in an archive is lost history: that date stays
pending. Recover matching metadata/headings from Git history or
`.praukron/backup/<timestamp>/`; never fill arrays by guessing. Renaming a
working heading creates a new identity under this rule; a renamed archived
heading requires history repair. Body-only edits retain identity. A rewrite
for another date preserves saved names of a held date, even stale names.

For a deleted or unusable sidecar, first run `validate` and compare against
trusted history. If all headings for every date are together in one
journal, remove the broken sidecar and the next successful journal move
rebuilds it. Otherwise recover matching ordering information from Git or the
backups, reconciling later journal edits before retrying. The first archive's
preimage backup records a new sidecar as absent: use its original journal to
recover order, not a nonexistent sidecar copy. Undoing the whole archive is
safe only when that also preserves subsequent work; metadata repair after
later edits is a separate owner reconciliation. A coherent permutation of
complete cross-file metadata cannot be detected from separated journals
alone, so retain its history and backups.

### Writing records without editing files

Agents record tasks, criterion results, status changes and journal entries with
five commands. Each reads a small JSON payload and prints one JSON line, so the
cost of a write follows the size of the record, not of `TASKS.md` or
`ACCEPTANCE.md` ([SPEC §2.6](SPEC.md#26-structured-writes), measured in
[benchmarks](benchmarks.md)). `--schema` prints a command's payload and an
example, `--dry-run` shows what would be written without writing it, and
`--help` says when to use the command and when not to.

**The starting point.** The examples below form one ordered sequence. Run them
in a scratch project whose chronicle has a thesis statement, this module in
`MODULES.md`, and nothing else (the `Phase: P-NONE` module holds work that
belongs to no phase):

```text
## M-FOUNDATION — Foundation
- Phase: P-NONE
- Outcome: The project can be built and checked.
```

and this task, written by hand at the end of `TASKS.md`. It has no contract
yet (`- AC: none`), so `praukron validate` reports `unknown-contract` until
step 1 adds one:

```text
## T-DOCS-02: Review the guide
- Status: TODO
- Module: M-FOUNDATION
- Domain: execution
- Validation: UNTESTED
- Dependencies: none
- Owner: unassigned
- AC: none
- Evidence: —
- Governed by: none
```

The outputs are what the commands print for LF files; yours differ only in
revisions (a hash of the record's bytes, so CRLF files hash differently) and
dates. JSON output escapes non-ASCII, so `—` prints as `—`. On POSIX the
payload is piped in with `echo '…' | praukron … --input -`; on PowerShell save
the JSON to a file and pass `--input payload.json`.

**1. Give an existing TODO task its contract.** `task add` always writes a
contract or names an existing one, so `contract add` is for a task that was
written by hand or is missing its contract. It needs the task's revision. If
you have never seen one, send sixteen zeros on purpose: the refusal returns it.

```console
$ echo '{"id":"AC-T-DOCS-02","task":"T-DOCS-02","title":"Review the guide","expectTaskRevision":"0000000000000000","criteria":[{"text":"Given the guide, When a reviewer follows it, Then every example runs.","class":"RUNTIME"}]}' | praukron contract add --input -
{"ok":false,"operation":"contract.add","error":{"code":"stale-revision","message":"task has changed; retry with expectTaskRevision 500d120c1d094409","field":"expectTaskRevision"},"written":false,"current":{"recordId":"T-DOCS-02","source":"TASKS.md#T-DOCS-02","revision":"500d120c1d094409","record":"## T-DOCS-02: Review the guide\n- Status: TODO\n- Module: M-FOUNDATION\n- Domain: execution\n- Validation: UNTESTED\n- Dependencies: none\n- Owner: unassigned\n- AC: none\n- Evidence: \u2014\n- Governed by: none\n"}}
$ echo $?
1

$ echo '{"id":"AC-T-DOCS-02","task":"T-DOCS-02","title":"Review the guide","expectTaskRevision":"500d120c1d094409","criteria":[{"text":"Given the guide, When a reviewer follows it, Then every example runs.","class":"RUNTIME"}]}' | praukron contract add --input -
{"ok":true,"operation":"contract.add","recordId":"AC-T-DOCS-02","source":"ACCEPTANCE.md#AC-T-DOCS-02","revision":"e004d9c19957e312","validation":"passed","written":true,"refreshed":true}
```

**2. Add a task with its contract.** One write; either both records appear or
neither does. The `revision` in the result is what the next update of this task
must quote.

```console
$ echo '{"id":"T-DOCS-01","title":"Write the retry guide","module":"M-FOUNDATION","domain":"execution","dependencies":[],"contract":{"id":"AC-T-DOCS-01","title":"Retry guide","criteria":[{"text":"Given a refused write, When the guide is read, Then the next step is clear.","class":"INSPECTION"}]}}' | praukron task add --input -
{"ok":true,"operation":"task.add","recordId":"T-DOCS-01","source":"TASKS.md#T-DOCS-01","revision":"9101852277a8233c","validation":"passed","written":true,"refreshed":true}
```

**3. Claim it.** `expectRevision` is the revision you last saw. A stale one is
refused and the refusal returns the current revision and record.

```console
$ echo '{"id":"T-DOCS-01","expectRevision":"0000000000000000","set":{"status":"WIP","owner":"worker"}}' | praukron task update --input -
{"ok":false,"operation":"task.update","error":{"code":"stale-revision","message":"T-DOCS-01 changed: expected revision 0000000000000000, current 9101852277a8233c; retry with currentRevision","field":"expectRevision","currentRevision":"9101852277a8233c","section":"## T-DOCS-01: Write the retry guide\n- Status: TODO\n- Module: M-FOUNDATION\n- Domain: execution\n- Validation: UNTESTED\n- Dependencies: none\n- Owner: unassigned\n- AC: AC-T-DOCS-01\n- Evidence: \u2014\n- Governed by: none"},"written":false}

$ echo '{"id":"T-DOCS-01","expectRevision":"9101852277a8233c","set":{"status":"WIP","owner":"worker"}}' | praukron task update --input -
{"ok":true,"operation":"task.update","recordId":"T-DOCS-01","source":"TASKS.md#T-DOCS-01","revision":"864c26d8af8f1583","validation":"passed","written":true,"refreshed":true}
```

`WIP` dates the claim. The task's contract is now frozen: `contract add` for it
is refused with `frozen-contract`, and no command changes a criterion's text or
class. A criterion's state and evidence stay writable.

**4. Record a criterion's result.** The revision is the contract's, not the
criterion's. A stale one returns the current criterion line and revision.

```console
$ echo '{"criterion":"AC-T-DOCS-01-01","state":"PASS","evidence":"reviewed docs/GUIDE.md","expectRevision":"0000000000000000"}' | praukron criterion set --input -
{"ok":false,"operation":"criterion.set","error":{"code":"stale-revision","message":"criterion changed; retry with currentRevision (currentCriterion is the current line)","field":"expectRevision","currentCriterion":"- `AC-T-DOCS-01-01` \u2014 Given a refused write, When the guide is read, Then the next step is clear. `INSPECTION` \u00b7 `NOT_RUN`","currentRevision":"385b53f179252d79"},"written":false}

$ echo '{"criterion":"AC-T-DOCS-01-01","state":"PASS","evidence":"reviewed docs/GUIDE.md","expectRevision":"385b53f179252d79"}' | praukron criterion set --input -
{"ok":true,"operation":"criterion.set","recordId":"AC-T-DOCS-01-01","source":"ACCEPTANCE.md#AC-T-DOCS-01","revision":"5d4e9c10edee6b33","validation":"passed","written":true,"refreshed":true}
```

Evidence is one line. Sending the same values again with the new revision is a
successful no-op that returns the same revision:

```console
$ echo '{"criterion":"AC-T-DOCS-01-01","state":"PASS","evidence":"reviewed docs/GUIDE.md","expectRevision":"5d4e9c10edee6b33"}' | praukron criterion set --input -
{"ok":true,"operation":"criterion.set","recordId":"AC-T-DOCS-01-01","source":"ACCEPTANCE.md#AC-T-DOCS-01","revision":"5d4e9c10edee6b33","validation":"passed","written":true,"refreshed":true}
```

**5. Close the task.** `DONE` needs every criterion `PASS` and evidence. The
refusal names the rule and the next step.

```console
$ echo '{"id":"T-DOCS-01","expectRevision":"864c26d8af8f1583","set":{"status":"DONE"}}' | praukron task update --input -
{"ok":false,"operation":"task.update","error":{"code":"evidence-required","message":"DONE needs recorded evidence; include set.evidence","field":"evidence"},"written":false}

$ echo '{"id":"T-DOCS-01","expectRevision":"864c26d8af8f1583","set":{"status":"DONE","evidence":"Guide reviewed; AC-T-DOCS-01-01 PASS."}}' | praukron task update --input -
{"ok":true,"operation":"task.update","recordId":"T-DOCS-01","source":"TASKS.md#T-DOCS-01","revision":"406567c8185533f1","validation":"passed","written":true,"refreshed":true}
```

An `Authority: owner` task is never closed this way (`owner-held`), and no
command sets `HUMAN_VERIFIED`.

**6. Append a journal entry.** The heading is `## <date> — <title>`; the date
defaults to today. Repeating the request is refused instead of duplicated.

```console
$ echo '{"date":"2026-10-15","title":"Progress","body":"Wrote the guide."}' | praukron journal append --input -
{"ok":true,"operation":"journal.append","recordId":"2026-10-15 \u2014 Progress","source":"JOURNAL.md#2026-10-15 \u2014 Progress","revision":"d6736f9a94268793","validation":"passed","written":true,"refreshed":true}

$ echo '{"date":"2026-10-15","title":"Progress","body":"Wrote the guide."}' | praukron journal append --input -
{"ok":false,"operation":"journal.append","error":{"code":"duplicate-heading","message":"journal heading already exists: 2026-10-15 \u2014 Progress; the entry is already recorded, or change the title or date"},"written":false}
```

A refusal changes nothing and exits 1. If a write succeeds but its refresh
fails (`"written":true,"refreshed":false`), do not repeat it; run
`praukron compile && praukron graph && praukron dashboard`. If another writer
holds the lock (`locked`), wait and retry.

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
`INTENT.md` naming that one task. Agents write those records with the
commands above, so the task and contract arrive in one validated step. If a material choice gets made along the way,
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
