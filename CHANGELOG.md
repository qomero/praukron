# Changelog

Praukron records its own history in `.praukron/chronicle/JOURNAL.md` and its
decisions in `.praukron/chronicle/ADR/`. This file is the short version, for
people arriving from a release page.

## 2.2.0 — 2026-10-16

- Fixed: upgrades now show validation failures even when no compiled views exist.
  Owner-held tasks marked `HUMAN_VERIFIED` before `VERIFY` existed are each named
  with the interactive owner command that resolves the missing verification.
  Historical records and owner identity requirements are preserved.
- Refuse a batch that clears a task's failing criterion and marks the task DONE
  in the same transaction, naming the failing criteria and leaving all files unchanged.
- Clarify failed-criterion context, closure refusals and write guidance: leave the task
  open until actual corrective work and supporting evidence exist; a request to mark
  DONE does not justify relabelling FAIL as PASS or splitting a refused batch.
  Legitimate standalone corrections and later closure remain supported.

## 2.1.0 — 2026-10-16

### Upgrading to 2.1

2.1 cuts the turns an agent needs to write the chronicle. In isolated agent sessions on one machine, a set of
five common writes cost 1.5–1.9× fewer input tokens than on 1.3.0, across four models; see
[docs/write-tokens-2026-10-16.md](docs/write-tokens-2026-10-16.md). Most of it is additive. These changes can
affect an existing 2.0 chronicle:

- **Owner verification is enforced.** `HUMAN_VERIFIED` on an `Authority: owner` task now needs a `VERIFY`
  response from a login named in an ACCEPTED ADR's `Owners:` field (`verification-without-owner`). A malformed
  `Owners:` field is `invalid-owners`, and an unknown response action is `invalid-response-action`. Owner-held
  work with no ADR naming owners is the warning `owners-missing`. Add `Owners: <your gh login>` to the ADR that
  governs ownership, then verify each owner-held task with `praukron respond <task> VERIFY` or from
  `praukron dashboard --serve`. See Known issues for tasks verified before 2.1.
- **`COORDINATION.md` is validated when present.** Its errors are `duplicate-coordination-record`,
  `brief-without-conventions`, `invalid-brief-status`, `invalid-convention-status`, `invalid-convention-role`,
  `unknown-brief-task`, `unknown-brief-convention`, `retired-brief-convention` and `multiple-active-briefs`. A
  chronicle without the file is unchanged.
- **Frozen criteria are fingerprinted.** A task that becomes `WIP` through `task update` gains a `Frozen:` field.
  From then on, a hand change to that task's criterion text or class, or an added or removed criterion, is
  `frozen-criterion-changed`, `-added` or `-removed`, and a malformed field is `invalid-frozen`. Tasks without the
  field are not checked, so existing chronicles upgrade without these errors. Change a frozen criterion with
  `acr add`, then let the owner decide.

Run `praukron validate` after upgrading and fix what it names. To install 2.1, run the install command again so
agents get the new `AGENTS.md` block, with the payload examples and the `acr add` and `batch apply` commands.

### Known issues

- **Owner-held tasks verified before `VERIFY` existed fail `validate`** (T-UPGRADE-VERIFY-01). A 2.0 chronicle
  with an `Authority: owner` task already set `HUMAN_VERIFIED` and no `VERIFY` response reports
  `verification-without-owner` after the upgrade, so `compile` and every write command refuse to run. Until a fix
  ships, the owner records a `VERIFY` for each named task (after adding `Owners:` as above), or stays on 2.0.0.
- **A batch can pass a failing criterion and close its task** (T-WRITE-FLIP-01). `criterion set` may turn `FAIL`
  into `PASS`, and `batch apply` can then set the task `DONE` in the same call. One of four models did so when
  asked to close a task with a failing criterion. Review a `FAIL` → `PASS` change before accepting the work. A
  fix will refuse that combination.

### Changes

- Owner identity (ADR-060, ADR-066): `praukron respond` and the served
  dashboard record `By:` as the active `gh` login and refuse typed `--by`.
  `VERIFY` records an owner's interactive verification of an `Authority: owner`
  task; `HUMAN_VERIFIED` requires a VERIFY from a login named in an ADR's
  `Owners:` field.
- Added: coordination conventions and task briefs live in the chronicle (ADR-075). An optional
  `COORDINATION.md` holds `## CV-<id>` conventions and one `## BR-<id>` brief per task; `context <task>`
  carries the task's `brief` and the conventions it rests on (filtered by `--role`, which now also takes
  `coordinator`), `retrieve <task>` adds them as labelled `BRIEF` and `CONVENTION` items after the task's ADRs,
  and `retrieve CV-…`/`BR-…` returns one. `validate` reports dangling, duplicate and malformed records, a brief
  without its required `Conventions` field, and text that looks like a credential. `archive` never moves the file and keeps a task with an `ACTIVE`
  brief. The installer ships the template and never overwrites an existing file; a chronicle without it is
  unchanged.
- Journal references understand range shorthand. `T-P0-01..05`, `T-P0-01..T-P0-05` and `ADR-003..005` name each
  id they span; `unknown-journal-reference` names only the members that do not exist, and a range that cannot be
  read (different stems or tail widths, backwards, non-numeric, or more than 100 ids) is one warning naming it.
  Archive journal eligibility reads the same expansion, so an entry moves only when every member is archived.
- **Writes take fewer turns.** `AGENTS.md` now gives a one-line minimal payload for each write command,
  tested against a synthetic chronicle. It says the commands validate and refresh every view themselves, so
  `validate` and `compile && graph && dashboard` follow only a direct edit, and that a write naming its record
  may go straight to `context <task>`. A payload refused for its shape carries `example`, a minimal accepted
  payload built from the command's schema. A field beside the object it belongs in, such as `status` beside
  `set`, is refused as `misplaced-field` and shown in its place. `--schema` gains the `examples` annotation.
  Writes no longer leave `__pycache__` under `.praukron/runtime`, and a committed write removes its recovery
  record and only the recovery directory and ignore file it created (T-WRITE-GUIDE-01, T-WRITE-REFUSAL-01).
- **`acr add` records an Acceptance Change Request** (ADR-076). For a criterion of a `WIP` or `DONE` task's
  own contract, it appends a `### ACR-<n>` record to the `## Acceptance Change Requests` log of `ACCEPTANCE.md`,
  with the template's seven fields and `Decision: PENDING`. The tool assigns the id. The criterion never changes, and
  the owner still decides the request. A `TODO` task, an inherited invariant, an unknown task or criterion, and a
  second undecided request for the same criterion are refused (T-WRITE-ACR-01).
- `batch apply` runs several structured writes in one call: each entry is a write command's payload plus `op`, applied
  in order under one lock with one validation and one refresh, all or nothing. A record's revision read before the batch
  serves every operation on it, so recording several criteria needs no revision round trip per criterion (T-WRITE-BATCH-01).
- Task context, explain and retrieve carry write revisions; task packets report whether DONE is allowed and why.
- **`validate` detects a frozen criterion changed by hand** (ADR-078). `task update` records a `Frozen:`
  fingerprint of each criterion's id, class and text when a task becomes `WIP`. `validate`, `compile`, the
  write commands and `context` report a `WIP` or `DONE` task whose criterion text or class changed, or whose
  criteria were added or removed, naming the task and criterion; state and evidence are not covered. Accepted
  Acceptance Change Requests are replayed, so an accepted change applied in place passes. Tasks without a
  fingerprint are not checked, so existing chronicles upgrade without errors (T-WRITE-FREEZE-01).
- Changelog fragments: a pull request now records its change in `changelog.d/<task id>.md` instead of editing
  `CHANGELOG.md`, so pull requests stop conflicting. `scripts/changelog.py assemble <version> <date>` merges the
  fragments into the release section at release time. A repository tool; it is not installed.

## 2.0.0 — 2026-10-16

### Upgrading to 2.0

2.0 is the largest Praukron release so far. It adds structured writes for agents and a full archive lifecycle,
and it redesigns the dashboard. Most of it is additive, but these changes can affect an existing 1.x chronicle
or script:

- **`validate` reports new errors.** `invalid-journal-heading`, `invalid-archive-input` and
  `invalid-archive-file` are errors. New warnings cover the journal order file, duplicate journal headings
  across files, unresolved journal references, records in the wrong phase archive, and paths under `archive/`
  that `restore` would refuse. A chronicle that validated under 1.3 can fail now. Run `praukron validate`
  after upgrading and fix what it names.
- **`archive` moves more.** `archive <phase>` and the new `archive plan` / `archive apply --plan <digest>`
  cover more than a COMPLETE phase's tasks and contracts:
  - `P-NONE` tasks;
  - closed assumptions, debt and trace events;
  - journal entries whose named records are all archived.

  The first journal move creates `archive/JOURNAL.order.json`. Commit it with the archive and never edit it by
  hand.
- **Git-tracked chronicles must be clean to archive.** In Git mode, `archive apply` and `restore` refuse a
  dirty or partly untracked chronicle. Commit or stash first. Chronicles outside Git, or ones where Git would
  convert newlines, get byte-exact backups instead.
- **stdin is strict UTF-8.** `respond` and every `--input -` command read stdin as UTF-8 bytes, with an
  optional BOM, whatever the console code page is. Input that is not valid UTF-8 is refused instead of being
  decoded with the locale.
- **Dashboard links changed.** The location hash is `#tab?dim=v1,v2`. Old `#tab` links still open the tab. The
  Overview opens on the attention band.

Agents should use the five structured write commands instead of editing `TASKS.md`, `ACCEPTANCE.md` or
`JOURNAL.md`: `journal append`, `criterion set`, `task add`, `contract add` and `task update`. To install 2.0,
run the install command again. It updates the `AGENTS.md` block and the workflows; anything you edited is
staged under `.praukron/upgrade/` for you to merge, so agents learn the new commands.

### Changes

- Fixed: the dashboard sorted events and chose an operation's latest event lexically (`Extra 9` after
  `Extra 11`); every view now uses one event order (`model.event_order`), checked across context, retrieve,
  analytics and the dashboard before archive, after archive and after restore. `validate` now warns
  (`unsupported-archive-path`) about any path in `archive/<phase>/` that `restore` would refuse.
- Fixed: `respond` and every `--input -` write command now read stdin as strict UTF-8 bytes (optional BOM)
  instead of the console code page, so non-ASCII text no longer fails with UnicodeEncodeError on Windows;
  invalid UTF-8 is refused with a clear error.

- `task update` (ADR-074, amending ADR-068) sets a task's validation level (`set.validation`: `UNTESTED`, `SYNTHETIC` or `AI_REVIEWED`;
  `HUMAN_VERIFIED` is refused) and links it to further governing ADRs (`addGovernedBy`: existing ids are
  appended once in order, ids already listed are skipped and reported, unknown ids are refused, and entries
  are never removed or reordered).
- Validate permanent archive invariants: archive paths and records, task phase placement, journal headings,
  and journal-named references now validate across working and archived files; archive apply and restore both
  report a committed authoritative move when any projection refresh step fails.

- Fixed: `context`'s `events.recent` (and every view that takes the last N events) depended on which file held
  an event, so archiving or restoring a phase could change it. Events are now ordered at load by their own
  time, then the number in their id, then the id; an event without a time sorts first.
- Document the archive lifecycle in the owner guide, specification (§2.7), README, `AGENTS.md`, the product
  thesis and the chronicle README template: `archive plan`, `apply --plan` and `restore`; eligibility per record
  type on the phase axis; destinations; what is never archived and why; and the exact Git (unstaged, staged,
  committed) and no-Git (byte-exact backup) recovery procedures. The permanent archive invariants (with their findings and severities) and the
  refresh contract of T-ARCH-VERIFY-01 are documented from the merged code.
- Add archive parity tests on a synthetic large chronicle (every record type, many phases, `P-NONE`, Vietnamese
  text, LF and CRLF) comparing records, derived facts, `context` and `retrieve` before and after archive and
  restore, an `install.sh` runtime-module check, and `tests/bench_archive.py`, whose Git and no-Git results on a
  real chronicle copy are added to the benchmarks page.
- Add Git-aware archive recovery with a clean-chronicle guard and exact undo commands,
  persistent byte-exact backups outside Git, and `archive restore <phase>` after later edits;
  apply and restore share durable rollback across all record files. Detect partial ignores and nested
  repositories, keep backups private, and print POSIX sh and PowerShell recovery procedures; restore
  validates the proposed restored snapshot before writing and follows the working file's line endings.
  Use exact backups when Git newline or smudge conversion could change recovery bytes.

- Archive eligible P-NONE tasks and closed, reconciled assumptions, technical debt and trace events;
  load and retrieve those lifecycle records from phase archives while preserving their provenance.

- Dashboard review fixes: every figure that counts tasks (progress cards, phase progress, module
  counts, the Governance validation table, operations counts, the attention band and group counts)
  now follows the cross-filters; the live Needs you count uses the submit rule, so Feedback without
  words is not answered and an owner item with no form no longer holds it above zero; filtered-out
  chart marks keep their colour as an outline at 3:1 instead of fading; long chart labels no longer
  scroll the page sideways on phones; the submit bar stays pinned on phones; removing the last filter
  keeps focus visible; and in-page links keep the filters in the address. A contract shared by
  several tasks counts once while any of them passes the filters; Clear all, Escape and Show all
  keep focus visible; shares round half up in both the compiler and the page.
- Archive journal entries with the records they name: a dated `JOURNAL.md` entry moves, as an exact byte
  span placed by entry date, to the latest phase's archive when it names at least one archivable record and
  all are archived or moving; entries naming nothing, only ADRs, an open or unknown record, or a duplicate
  heading stay and are reported; a read-back check covers journal sections; `retrieve` resolves
  `JOURNAL.md#<heading>` pointers into archives.
- Preserve same-date journal order across phase archive and restore operations with shared
  `archive/JOURNAL.order.json` heading metadata, written with each move and covered by plan digests,
  backups and durable recovery; preserve journal sections verbatim and retain the exact-span parity check.
- Validate journal ordering metadata and duplicate headings (ADR-072); hold only journal entries
  with unknowable cross-file ties when metadata is missing or incomplete, preserve record moves,
  and reconcile owner reorders from the journals. Document metadata recovery and date-first placement.
- Keep unrelated journal and record moves working with undecodable ordering metadata; hold missing
  archive history, retain stale names on held dates, and validate only journal inputs, reusing loaded
  project diagnostics.
- Preserve new or renamed working journal headings' same-date neighbour order on split dates;
  follow saved ties only when no known working neighbour constrains placement, so archive and restore
  agree with read-back even for retained entries and unrelated-date moves.
- Redesign the dashboard to put attention first: a "Needs attention" band (blocked tasks, failing
  work, owner input, review gaps, debt at its trigger, unresolved operational failures) above progress;
  cross-filtering Status, Domain, Validation, Phase and Module charts that narrow every chart, table,
  obstacle list and the Needs you panel, with removable filter chips, Clear all, and the filters kept in
  the URL hash; a served Needs you panel marked live, with a submit bar that stays in reach and a count
  that falls as cards are answered; a System / Light / Dark control; WCAG AA contrast tokens for both
  themes, checked by a test; and reduced-motion support. The page stays one deterministic,
  self-contained, read-only file.
- Add `archive plan <phase>` with per-record move/stay reasons and a SHA-256 plan digest,
  and `archive apply <phase> --plan <digest>` with stale-plan refusal under the shared lock;
  preserve legacy archive/dry-run, shared-contract eligibility and durable recovery. Plans,
  writes and recovery originals share one captured byte inventory; split destinations,
  full-heading section locators and directory recovery support later lifecycle rules.
  Preserve native filesystem case aliases in captured lookups, and record exact replacement
  staging paths so recovery also handles a kill after the temporary file is flushed.
  Resolve the project and captured authority roots consistently for symlinked temp paths.

- Document the structured writes: a "Structured writes" section in the specification (pipeline,
  field permissions, freeze and owner rules, revisions, results, exit codes and retry semantics),
  copy-paste examples in the guide, a README command row for each, and the commands in the `AGENTS.md`
  block, `praukron-work` and `praukron-checkpoint`. Agents are told to use them instead of editing
  `TASKS.md`, `ACCEPTANCE.md` or `JOURNAL.md`.
- The five write commands' `--help` and `--schema` descriptions state WHEN, WHEN NOT, RETURNS and EXAMPLE,
  and every refusal names the rule and the next valid step. The `task update` evidence description no longer calls line breaks
  wrap points.
- Add `tests/bench_writes.py` and `docs/benchmarks.md`: bytes and estimated tokens to add a task, record a
  criterion result, update a task and append a journal entry, by full read, targeted read and command,
  before and after archiving.
- Correct the specification: the resume read order starts with `INDEX.md`; `retrieve --budget` bounds
  selected record bodies, not the whole output; `INDEX.md`'s token budget warns and does not cap.
- Bring the product thesis up to date: the tool's small governed write set, one owner role, what
  `HUMAN_VERIFIED` enforces, the full records table, the index, `retrieve` and served dashboard, bounded
  cost, domains and the owner loop, CodeGraph and the local server, and the private chronicle (ADR-042).
- Contract creation refuses IDs named by other tasks; contract attachment requires the task revision.
  An unfrozen target may resolve its own already-named missing contract without rewriting the task.
  Accept criteria without prescribed wording and empty AC sentinels; align task field order and payload schemas.
- Add `praukron task add` and `contract add` with strict payloads, frozen-contract checks,
  and recoverable transactions for task and contract creation.
- Add `praukron criterion set` for revision-guarded criterion results and one-line evidence.
- Structured transactions normalize symlinked project paths before target lookup and recovery.
- Add `praukron task update` to change a task's status, owner, claim or evidence under a revision check,
  refusing DONE without passing criteria and evidence, owner-held DONE, archived tasks and stale revisions.
- Structured writes now preserve committed results on lock-release failure, resolve full journal headings exactly,
  keep revisions stable across appends, and accept UTF-8 BOM payloads.
- Add `praukron journal append` with strict JSON payloads, dry runs, and shared recoverable write transactions.
- Locks no longer reclaim live writers or release another writer's lock.
- Archiving now moves a shared contract once every working task that uses it has moved.
- Journal pointers rendered in the index resolve to their complete headings.
- Interrupted archives are rolled back from a durable recovery record before later writes.
- Agents are told to read only the target section (or the file's end) when editing a large chronicle file directly.

## 1.3.0 — 2026-10-13

- Windows: the command prints as UTF-8. Piped, as agents run it, stdout
  defaulted to cp1252 and `retrieve` crashed with `UnicodeEncodeError` on
  the first record that held text such as Vietnamese.
- `retrieve` keeps to a token budget (`--budget`, default 4000, 0 for none).
  Records load most relevant first; those past the budget are listed as
  pointers with the command that fetches each, instead of growing the pack
  without bound on a long chronicle.
- `praukron archive <phase>` (ADR-067), run only when the owner asks, moves a
  COMPLETE phase's DONE tasks and their contracts, verbatim, from `TASKS.md`
  and `ACCEPTANCE.md` into `chronicle/archive/<phase>/`. Archived records stay
  authority and every command reads them as before; only `source` changes,
  and old `TASKS.md#…` pointers still resolve. A task stays while an open
  assumption, unresolved debt or failure, or awaiting response points at it;
  a contract stays while it holds an invariant. The move is refused on any
  validation error and undone byte for byte unless the records read back
  identical. On Qopilot, archiving P0–P8 takes `TASKS.md` from 48 KB to 17 KB
  and `ACCEPTANCE.md` from 167 KB to 57 KB.
- Windows installer: run as `sh E:\path\install.sh`, it now recognises the
  checkout instead of silently downloading the latest release. Upgrading a
  Prokron installation without the symlink privilege makes the `.prokron`
  alias a junction; before, `ln -s` copied the directory, leaving two
  chronicles and refusing the next install.
- The test suite passes on Windows: symlink-only cases skip where links
  cannot exist, and the fakes and checksums no longer assume POSIX.

## 1.2.2 — 2026-10-12

- Windows: `praukron` now runs from PowerShell and cmd, not only Git Bash.
  The installer puts a `praukron.cmd` beside the project entry and, on
  Windows, beside the `praukron` launcher; both find the nearest project as
  the POSIX launcher does. Before, PowerShell opened the shell script as a
  text file.
- The project entry `.praukron/praukron` picks a working Python 3.9 or newer
  itself instead of trusting `python3`, which on Windows is often a Microsoft
  Store placeholder. It says so plainly when there is none.

## 1.2.1 — 2026-10-11

- Windows: every chronicle file is read and written as UTF-8. The locale
  default (cp1252) broke `compile`, `respond` and the dashboard as soon as a
  record held text such as Vietnamese, and failed the test suite on setup.
  A test now refuses any runtime text read or write that names no encoding.

## 1.2.0 — 2026-10-09

- Dashboard: a "Needs you" side panel on the right, visible on every tab,
  holds everything waiting on the owner at once: open assumptions
  (permission first) and every obstacle only the owner can clear, with
  Guide. Under `--serve` all forms and the one submit bar live there
  (ADR-065).

## 1.1.0 — 2026-10-04

- Rename the project, repository, commands, package and owned directory to
  Praukron / `praukron` / `.praukron/`.
- Preserve DOI identifiers and Git history. Upgrade existing Prokron
  installations without rewriting their chronicle; keep compatibility aliases
  for old launchers and preserve edited guidance.

- Record provisional assumptions and owner responses explicitly; expose owner-held
  blockers and an Assumptions review queue in the dashboard.
- Add `praukron respond` and `praukron dashboard --serve` for validated,
  append-only owner guidance. GitHub-account-based owner verification remains
  planned and is not claimed by this release.
- Reduce task/context packets and make progress metrics state what they measure.

### Upgrading from Prokron 1.0.0

Run the published Praukron installer. It moves `.prokron/` to `.praukron/`
without rewriting project records, keeps an alias for old launchers, and stages
edited guidance for review. Use `praukron` for current commands. Existing
release tags and DOI identifiers remain intact.

## 1.0.0 — 2026-09-24

Praukron 1.0 freezes its core scope (ADR-051): Praukron knows the project; it
does not run the project. A fresh agent can enter a repository, find
legitimate work, get the minimum authoritative context, see unresolved state
and other actors' claims, and avoid conflicting work without reading the
repository wholesale.

### Upgrading from 0.8

- Run the installer as usual. It adds `THESIS.md` and `MODULES.md` to the
  chronicle and never rewrites existing records.
- Existing tasks that name `Phase:` keep compiling. `validate` warns
  `legacy-task-phase` for each one, and `missing-thesis` until `THESIS.md` has a
  `Statement:` (ADR-036).
- To convert: author the thesis, author modules in `MODULES.md` (each with one
  `Phase:` or `P-NONE`), then replace each task's `Phase:` line with the
  `Module:` it belongs to. Once any module exists, a missing thesis, an unknown
  module, or a task naming both `Module:` and `Phase:` is an error.

### Added

- The project hierarchy thesis → phase → module → task (ADR-035). `THESIS.md`
  holds the product thesis; `MODULES.md` authors modules, each in one phase or
  `P-NONE`; a task names one `Module:` and takes its phase from it. The lineage
  appears in `project.json`, `STATE.md`, `TASK_GRAPH.md`, `explain`, `context`,
  and a new Product hierarchy section on the dashboard Overview.
- Init, work, decide, checkpoint, and resume follow the hierarchy, and the
  chronicle README, templates, AGENTS.md, the skill, and the public documents
  describe it.

- `praukron context` without a task prints an orientation packet projected from
  the compiled report: phase, execution in flight, ready and blocked work, the
  critical path, the main blocker, the next gate, and where authority lives
  (ADR-050).
- `praukron context <task>` adds `authority` (the exact `file#anchor` records to
  read), `problems` (dependencies, decisions, phases, and contracts that do not
  resolve), and each dependency's status.
- `praukron context <task>` exposes `task.claim` (`active`, `holder`, `claimed`),
  read from the existing `WIP`, `Owner:`, and `Claimed:` fields, so an agent
  can see that another actor holds a task (ADR-051).

### Scope

- Core scope is frozen (ADR-051): Praukron knows the project; it does not run
  the project. Later changes come from failures demonstrated while dogfooding
  real repositories.

### Changed

- AGENTS.md makes `praukron context <task>` the step after `INDEX.md`, says that
  compiled output and packets are derived maps whose disagreement with a record
  is reported rather than reconciled, and forbids starting a task merely
  because unrelated work is visible. The CLAUDE.md block is now a thin adapter
  that defers to AGENTS.md.

## 0.8.0 — 2026-09-24

### Added

- **`TECH_DEBT.md`**: known technical debt with lifecycle (`OPEN`,
  `ACCEPTED`, `SCHEDULED`, `RESOLVED`, `INVALIDATED`), trigger and trigger
  state, interest, exit condition, evidence, and lineage from the decision
  that created it to the task that repays it. Validated, compiled, shown under
  Governance, and carried into task context. ADR-046.
- **`INDEX.md`**, generated into the chronicle by `compile`: a compact,
  deterministic map of what matters now with `file#anchor` pointers. The one
  generated file in the chronicle; never read as authority, and `validate`
  warns when it is stale. ADR-047.
- **Agent boot protocol**: AGENTS.md and a managed CLAUDE.md block tell
  agents to read `INDEX.md` first, then only the records it points to, then
  code. The chronicle read order starts with it.
- **`praukron retrieve`**: the records a question or id needs, routed by the
  index, each labelled with its source, with nothing written. ADR-048.
- **Optional CodeGraph**: `praukron codegraph status|setup|doctor|uninit`,
  `praukron retrieve <task> --code`, and optional `Files:`/`Symbols:` task
  anchors. Never required, never part of project state, and never allowed to
  change agent configuration without consent. ADR-049.

## 0.7.0 — 2026-09-23

### Changed

- **Execution and operations are separate domains.** Every task resolves to
  `execution` or `operations`, declared with `Domain:` or inferred as
  execution from phase membership, exit authority, or gate verification —
  never from a title. Phases, gates, the critical path, completion,
  acceptance and validation figures, work in flight, and the main blocker are
  computed from execution only, so completion and validation figures can
  differ from 0.6.0 for the same records. An operations task that execution
  depends on is reported as an external blocker and keeps its domain.
  ADR-045.
- The main blocker, work in flight, and next gate are selected by analytics
  and carried in `project.json` under `execution`; the dashboard no longer
  chooses them.
- `validate` reports "consistent" when there are warnings but no errors.

### Added

- **`TRACE.md`**, an append-only record of operational events — tool calls,
  commands, mini-actions, mutations, failures, retries — with validation of
  its structure and a warning for anything that looks like a credential.
- **An Operations tab**: summary, open operations with their traces, failures
  and warnings, tool calls, mini-actions, changes, retries, and a timeline.
  Execution failures link to the events that name them.
- `praukron domains`: how each task's domain was decided, which tasks need a
  `Domain:`, and what operational history exists. Read-only.
- The task graph groups operations tasks in their own dashed group; All Tasks
  filters by domain; the task dialog shows domain and related events.

## 0.6.0 — 2026-09-23

### Changed

- **The dashboard is six tabs.** Overview (the default) opens with the current
  phase, the work in flight, the main blocker and the next gate; Execution,
  Graph, Governance, Decisions and All Tasks hold the rest. Obstacles are
  grouped, with dependency blockers waiting on blocked work collapsed; the
  critical path is a vertical chain; decisions and tasks are searchable and
  filterable. The tab lives in the URL hash. The embedded project data is
  unchanged. ADR-044.
- **Light and Dark themes**, chosen explicitly and remembered in the browser,
  with the system setting as the fallback.

### Fixed

- In the graph, the zoom buttons and clicking a task node did nothing in
  Chromium browsers: the canvas captured the pointer and the click never
  reached them.
- Redrawing a diagram (switching views, or now the theme) measured labels at
  the current zoom and drew them that much too small.

## 0.5.0 — 2026-09-23

### Changed

- **Reinstalling upgrades guidance.** The installer records a checksum of each
  guidance file it writes. Unedited guidance is replaced on the next install;
  edited guidance is kept and the new version staged under
  `.praukron/upgrade/`. The Praukron block in `AGENTS.md` is handled the same
  way without touching the rules around it. ADR-040.
- **The one-line install installs the latest release**, not `main`, by running
  that release's own installer. `--ref <tag|branch>` picks another, and an
  older runtime no longer replaces a newer one without `--allow-downgrade`.

- **An older runtime no longer rewrites a newer one's views.** `compile`,
  `graph` and `dashboard` refuse unless given `--force`, and `status` says to
  upgrade rather than recompile. ADR-041.
- `status` counts done tasks that nobody has reviewed or verified.
- A migrated v0.1 handoff now names `.praukron/compiled/STATE.md`, where
  generated state has lived since ADR-024.

### Added

- **Branch-safe records.** In a Git repository the installer maintains a
  marked `.gitattributes` block: `JOURNAL.md` and the ADR index merge by
  union, and compiled output is marked generated. The resume and checkpoint
  workflows say how to settle `INTENT.md` and `HANDOFF.md` after a merge.
- A `stale-reference` warning for backticked file paths in `HANDOFF.md` or
  `INTENT.md` that no longer exist.
- **`/praukron-baseline`**, an opt-in workflow for existing repositories. On
  the owner's request it proposes at most ten ADRs for decisions the code
  already depends on, each marked `Origin: RECONSTRUCTED` with an `Evidence:`
  field naming what it was inferred from. It creates no tasks, journal history
  or intent, and `existing` initialization still starts empty. ADR-037.
- Validation errors `reconstruction-without-evidence`,
  `unconfirmed-reconstruction` (an `ACCEPTED` reconstruction naming no
  authority) and `invalid-origin`.
- Compiled decisions carry `origin` and `evidence`; context packets carry
  `origin`. The dashboard gains a Decisions section and labels reconstructed
  decisions there and in the task drill-down.

## 0.4.6 — 2026-09-22

### Added

- **Archive and citation metadata.** `.zenodo.json` and `CITATION.cff` record
  the title, description, licence, keywords and authorship, so the archived
  deposit says what the project is rather than what could be inferred from the
  repository on the day it was read. Releases from here on are preserved
  independently of the code host and carry a persistent identifier. ADR-032.

  Authorship is recorded as the organisation. A personal name and an ORCID are
  the author's to give; the deposited record can be edited.

- A test asserting `CITATION.cff` names the version in `VERSION`, the same
  drift guard the README already has.

## 0.4.5 — 2026-09-22

### Added

- **[`docs/GUIDE.md`](docs/GUIDE.md)** — using Praukron day to day: every
  command and flag, the agent workflows and when you actually need them, what
  an ordinary session looks like, and a table of symptoms with their causes.
  Written from an audit, so every command in it is one that was run against a
  fresh installation.

### Fixed

- **Claude Code's slash menu showed the plumbing.** `/praukron-work` read
  `Follow .praukron/commands/praukron-work.md` where the same command in OpenCode
  read `Start or continue one Praukron task`. All five now carry a description
  and an argument hint.

### Checked

- Every command, flag and documented failure mode, run in a freshly installed
  project. Every agent-host pointer — ten command files, the skill, and the
  workflow references in `AGENTS.md` — resolves to a file that exists.

## 0.4.4 — 2026-09-22

Upgrading left the old dashboard in place. Reported from real use.

### Fixed

- **Upgrading now refreshes the generated views.** Installing replaced the
  runtime but left `.praukron/compiled/` alone, so an upgraded project ran a
  current tool beside a page the previous release had written — and a shipped
  feature looked missing. If you hit this, `praukron dashboard` fixes it; the
  installer now does it for you.

  The refresh runs only when an installation already has generated views and
  the chronicle holds work, so a fresh install still creates compiled state
  only when you compile. If authority does not validate, installation still
  succeeds, says the refresh failed, and names the command. ADR-031.

### Added

- **Compiled output records the version that wrote it** (`generatorVersion` in
  `project.json`), and `praukron status` reports when it differs from the
  running tool. That covers the reader who never reinstalls.

### Note

No test could have caught this: every fixture installs into a directory with no
generated output, so the upgrade path had never been exercised in any release.
The suite now covers it.

## 0.4.3 — 2026-09-22

CI found a determinism defect on its first run.

### Fixed

- **Compiled output no longer depends on the directory the repository sits in.**
  The project name came from `root.name`, so the same chronicle compiled to
  `Praukron` in one checkout and `praukron` in another, and three generated files
  differed. The regeneration claim had therefore only ever held within one
  machine's directory naming.

  A project can now name itself with a `Project:` line above the first heading
  of `PHASES.md`. A chronicle without one keeps using the directory name, so
  **nothing existing needs migrating**, and the shipped template carries the
  field empty. ADR-030.

  The fix was to make the record more complete, not to make the check less
  strict — checking out into a fixed directory name would have turned a green
  pipeline into a lie.

## 0.4.2 — 2026-09-22

Nothing ran the tests except a person typing the command. Now something does.

### Added

- **Continuous integration** ([`ci.yml`](.github/workflows/ci.yml)) that
  enforces this project's own claims rather than only running `unittest`:
  - `tests` — the unit and installer suites on Ubuntu and macOS, Python 3.9
    through 3.13, with no dependency installed.
  - `invariants` — validates the real chronicle, then deletes
    `.praukron/compiled/`, rebuilds it, and fails if the result differs from
    what is committed. This makes Gate B and `AC-T-P2-14-01` continuously
    enforced instead of periodically remembered.
  - `no network, no dependencies` — asserts the runtime imports nothing outside
    the standard library.
- **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — how a change is accepted here: a
  task, a contract frozen before the work, an ADR for a material choice, and
  evidence before `DONE`. The same workflow the tool installs.
- **[`SECURITY.md`](SECURITY.md)** — the four parts of the surface worth
  attacking, and three things that are not vulnerabilities.
- **[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)**, issue templates for a
  chronicle gap and a bug, and a pull request template that asks for the task,
  the contract and the evidence.

Reports route through GitHub's private advisory form; no maintainer address is
published. ADR-029.

### Decided

- `.praukron/compiled/` stays committed. The diff is now the mechanism that
  proves regeneration, which settles an open question the handoff had carried.

## 0.4.1 — 2026-09-22

The continuity pilot ran in full. Every phase is closed and every gate is green.

### Changed

- **Gate P1-CONTINUITY is green and P1 is COMPLETE.** The pilot ran end to end
  against v0.4.0 across two disposable projects and three participants — Codex
  CLI 0.155.1 for install, uncommanded work, a reversed decision, a mid-task
  stop and re-initialization; a cold Gemini 3.8 Flash session for resume; the
  product owner for the human reading. All seven criteria pass. Results in
  [`docs/pilot-2026-09-22.md`](docs/pilot-2026-09-22.md). ADR-028.
- The partial run of 2026-09-21 is superseded rather than reused: it was
  measured against v0.2.1 and cites an entry point and a directory that no
  longer exist.
- **The documentation no longer points at a red gate as proof of honesty**,
  because there isn't one. The README and the product thesis were corrected and
  the four dashboard figures regenerated.

### Still not proven, and still said so

- No host exposed a real limit warning to observe during the pilot, and nothing
  was simulated in its place. Checkpointing against an actual quota boundary is
  untested, not passed.
- The human reading passed on the owner's attestation across two projects,
  without a point-by-point account or a comparison against an agent's reading
  of the same chronicle.

### Fixed

- `HANDOFF.md` directed the next agent at a path that moved when phase
  specifications became internal. Found by a cold agent during the pilot, which
  silently used the correct location — a prose path is not a reference the
  compiler resolves, so no test could have caught it.

## 0.4.0 — 2026-09-22

Installing and running Praukron both got shorter.

### Changed

- **Installing takes one line with no arguments.**

  ```sh
  curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh | sh
  ```

  `existing` is the default mode; `new` and a target directory are still
  accepted. A misspelled mode is now an error rather than being read as a
  target directory.
- **The command is `praukron`.** The installer writes a small launcher into a
  directory already on your `PATH`, so it works from anywhere in the project
  instead of `.praukron/praukron` from the root only. The launcher carries no
  behaviour: it finds the nearest project and runs that project's own runtime,
  so two repositories on different releases stay independent. ADR-027.

  It creates no directories, changes no shell configuration, and never replaces
  a `praukron` it did not write. `--no-link` skips it, and `.praukron/praukron`
  keeps working — which is still what the installed agent instructions use,
  since an agent may run with a different `PATH`.

### Fixed

- The README showed `praukron status` in its examples while telling readers to
  type `.praukron/praukron status`. A test now holds the published examples to
  one form.

## 0.3.3 — 2026-09-22

Documentation only. No behaviour changed.

### Fixed

- **A destructive instruction in the README.** It told readers to run
  `rm -rf .praukron && praukron compile` to demonstrate that compiled output
  regenerates. That was accurate until v0.3.0 moved the whole installation into
  `.praukron/`; afterwards it deleted the chronicle, the runtime and the command.
  It now names `.praukron/compiled/`.
- **`docs/SPEC.md` contradicted the product.** §8 listed a CLI and a dashboard
  as out of scope, both shipped in P2. §4.1 cited `DECISIONS.md`, removed in
  v0.2. §6 named a directory that moved in v0.3. The compiled-file table was
  missing `project.json`, the Mermaid views and the dashboard.
- A dead link to `commands/`, a stale test count, and a stale release number.

### Changed

- **The README leads with the problem.** A session ends and the next
  participant — another agent, another model, a teammate — loses what the code
  meant. That comes before any record, command or file is named.
- **Both projections of project state are shown rather than asserted.** The
  dashboard for a person, and real `praukron context` output for an agent, from
  this repository.
- **What is implemented, what is designed, and what is unproven are separated.**
  Nothing specified-only is described as available.
- Phase specifications are internal working documents and are no longer
  published (ADR-026).

### Added

- [`docs/PRODUCT-THESIS.md`](docs/PRODUCT-THESIS.md) — why shared project state,
  and how the model is built. Every claim labelled implemented, designed, or
  thesis.
- Three tests holding the published documents to their own facts: every
  relative link resolves, no `rm -rf` names anything but the compiled
  directory, and the release the README cites matches `VERSION`.

Every existing image is preserved, unmodified and unrenamed.

## 0.3.2 — 2026-09-22

### Added

- **Clicking a task in a diagram opens its detail.** The same dialog the task
  tables open, reading the same embedded project, so the two routes cannot
  show different things. Panning does not count as a click, and nodes that are
  not tasks — gates, phases — are neither offered as clickable nor respond to
  one.

## 0.3.1 — 2026-09-22

### Added

- **The task graph is legible.** Every diagram opens at a readable scale
  instead of being shrunk to fit its panel, with zoom, pan, trackpad pinch, a
  percentage readout and a Fit button. A graph that only fits below 60% opens
  at 60% centred on what is in flight, then what is ready, then the critical
  path.
- **Hovering a task traces its chain.** The hovered task, everything it
  transitively depends on, and everything that transitively depends on it stay
  lit; the rest dims. The chain is walked over the compiled dependencies
  embedded in the page, never over the drawing, so it cannot disagree with
  `praukron explain`. ADR-025.

### Fixed

- Resizing the window no longer collapses the diagram back to a fit-to-panel
  scale.

The page remains one deterministic file, and still falls back to diagram
source with every number intact when the diagram library cannot be loaded.

## 0.3.0 — 2026-09-22

Installing Praukron used to put five entries at the root of a repository it does
not own. It now puts one.

### Changed

- **One directory.** Everything Praukron installs lives under `.praukron/`:
  `chronicle/` for the authored records, `compiled/` for generated state,
  `runtime/` for the tool's code, `commands/` for the workflows, and `praukron`
  as the command. The separation between authored authority and compiled output
  is unchanged — it is two subdirectories now instead of two root directories,
  and the compiler still writes only into `compiled/`. ADR-024.
- **Outside that directory, only fixed addresses.** `AGENTS.md`, `CLAUDE.md`,
  `.claude/commands/`, `.opencode/commands/` and `.agents/skills/praukron/` are
  read by agent hosts at paths Praukron does not choose. Nothing else is written.
- **The command moved** from `./bin/praukron` to `.praukron/praukron`.

### Added

- **`praukron migrate` relocates a v0.2 installation.** Coming from v0.2 the
  records move byte for byte and nothing is archived, because nothing is
  transformed. Coming from v0.1 it still rewrites and archives as before. Host
  command files are repointed at the workflows' new location.

### Removed

- `templates/.praukron/README.md`, a template nothing installed. The compiled
  directory's README is generated, and the unused copy had already drifted from
  what the compiler writes.

## 0.2.5 — 2026-09-22

Rewrites the README around what Praukron actually is now: project tracking and
project management, read by a person in a browser and by an agent as text.

### Fixed

- **The Gates view never rendered.** Gates are identified by their heading, so
  `Gate A` reached Mermaid with a space in it, and Mermaid ends an identifier
  at the first space — failing the whole diagram rather than one node. Node
  identifiers now drop every character outside `[0-9A-Za-z_]`. Task identifiers
  are unchanged. Found by opening every dashboard tab while capturing the
  README screenshots, which no test had ever done.

### Changed

- **The README leads with project management.** Phase 2 shipped contracts,
  phases, gates, obstacles, a critical path and a dashboard, and the previous
  README still described a six-file Markdown convention for agent continuity.
  People and AI sharing one record is now presented as the mechanism that makes
  the state trustworthy, rather than as the headline. ADR-022 records the
  reframing and what it costs.
- **It shows the product instead of describing it.** Four screenshots of the
  generated dashboard: project metrics with phases and gates, the ready and
  blocked view with the critical path, a task drill-down with its contract and
  recorded evidence, and the dependency graph.
- **Every figure is current and real.** The `status` and `explain` blocks are
  copied from runs against the committed chronicle. The previous README quoted
  37 of 39 tasks from an earlier release.
- A new section separates what is verified — the deterministic compiler, 104
  tests, byte-for-byte regeneration, two agents auditing one contract — from
  what is not, which is cross-session agent behaviour. This repository's own red
  gate and unfinished task stay visible in both the text and the screenshots.

## 0.2.4 — 2026-09-22

Stops installation depending on a GitHub rename redirect.

### Fixed

- **Installation addresses the owner by its current name.** The account was
  renamed from `qomerovn` to `qomero`, and every old URL kept working through
  GitHub's redirect, which is why it went unnoticed. A freed GitHub username is
  claimable by anyone, and the README's headline command pipes that URL into
  `sh`, so a stale owner name in an installer hands code execution to whoever
  claims the abandoned name. `install.sh` and `README.md` now use the current
  name. ADR-021 records the rule: redirects are a migration convenience with an
  expiry nobody controls, never an address.

### Changed

- The anonymous `curl` install is now the documented default, since the
  repository is public. The GitHub CLI command remains as an alternative. The
  README no longer says the repository is private or that anonymous delivery is
  unverified; it was verified against a disposable repository.

## 0.2.3 — 2026-09-22

Closes Phase 2 and writes down what it left unfinished. No code changed; this
release is a governance record, and it is versioned because the chronicle
shipped beside the runtime is part of what a user installs.

### Changed

- **Phase 2 is `COMPLETE`.** All 17 tasks done, Gates A through E green on
  recorded evidence, every exit criterion met. The exit was accepted by the
  owner rather than computed, so it is recorded as ADR-020 rather than as a
  status edit.
- **The continuity pilot is parked, not dropped.** T-PILOT-01 returns to `TODO`
  at step 3 of 7 with its contract still frozen and its two passing criteria
  intact. Gate P1-CONTINUITY stays RED and P1 stays `EXIT_PENDING`; the gate
  never governed P2's exit. ADR-020 names where the debt is paid: the Phase 3
  exit audit runs the pilot procedure against the Phase 3 build itself, in a
  real repository across real sessions, instead of restarting it in a
  disposable project.
- The accepted risk is stated in the decision rather than implied: Phase 3 gets
  built on a workflow whose cross-session agent compliance is unverified, and a
  pilot failure at that audit lands against work already written.

### Fixed

- Restored `docs/Phase 2.md`, which had been deleted from the working tree
  while ADR-013, ADR-014, ADR-015 and the P2 phase record all cite it as
  governing authority.

### Added

- `docs/Phase 3.md` — the Phase 3 specification, Execution Intelligence. Not
  yet accepted as governing authority; that is a decision P3 opens with.

## 0.2.2 — 2026-09-21

Fixes the upgrade path. 0.2.0 moved authority from `.praukron/` to `praukron/`
and shipped without a migration, so a real project that updated kept every
record and reported an empty project. Preserving files is not the same as
carrying a project forward.

### Added

- **`praukron migrate`.** Moves a v0.1 chronicle into the v0.2 layout: tasks,
  decisions, intent and journal to `praukron/`, prose acceptance converted into
  contracts with their original wording, `DECISIONS.md` split into
  `praukron/ADR/` with a supersession index. It reports by default and changes
  nothing without `--apply`. Every original file is archived unchanged; nothing
  is deleted. `--phase` assigns a phase when you have one.
- Installation and `praukron status` both detect a stranded v0.1 chronicle and
  name the command to run.

### Fixed

- Task completion counts every task. Excluding phase-independent work made a
  freshly migrated project report `0 / 0`, which reads as "nothing here" rather
  than "none of this belongs to a phase yet". Phase-independent work now has its
  own line, and the phase split stays in phase completion.

### Notes

`TASK_GRAPH.md` is not carried over: it is wholly derivable and the compiler
regenerates it. `STATE.md` is — its risks and next steps are judgement rather
than derivation, so that prose moves into `HANDOFF.md`. That is the one thing a
careless migration would actually have destroyed.

## 0.2.1 — 2026-09-21

Phase 2 closes. Every task resolves to a contract, every criterion passes, and
five of six gates are green on recorded evidence.

### Fixed

- **The calendar Gantt no longer dates unscheduled work.** A dateless Mermaid
  milestone inherits the previous entry's end date, so the "unscheduled" row was
  quietly given a calendar position — the exact fabrication the two-renderer
  split exists to prevent. Unscheduled work is now counted in the title and
  never drawn; with nothing scheduled, the output has no date axis at all. Found
  by an independent Codex audit and confirmed by rendering both variants in a
  browser.
- **Context packets are scoped to their own task** (ADR-018). `INTENT.md` and
  `HANDOFF.md` are included only when they mention the task. A packet that
  carried narrative about other work handed a cold agent contradictory
  statements, and it refused to start — correctly. Packets now also state which
  task they are for, whether it is closed, which dependencies are met, and what
  blocks it.
- A phase awaiting exit is reported as the current phase rather than "none".

### Changed

- `AC-T-P2-13-03` and `AC-T-P2-14-03` reclassified from `MANUAL` to `RUNTIME`
  through the change-request mechanism (ACR-001, ACR-002). Both name an agent as
  their subject, so a human's report was never the right evidence. The
  requirements are unchanged.

### Notes

Gate E is green: two different coding agents audited the same implementation
against the same contract, disagreed once, and the disagreement was settled by
reproducible evidence rather than by seniority. Gate P1-CONTINUITY remains red —
it needs a real multi-session pilot, and nothing else will do.

## 0.2.0 — 2026-09-21

Phase 2: the chronicle becomes machine-readable. Project state, completion
authority, progression, blockers, and evidence can now be compiled and reported
deterministically, without a model in the loop.

### Added

- **Acceptance contracts.** `praukron/ACCEPTANCE.md` is the completion authority.
  A task is done when its frozen contract has sufficient evidence, not when
  someone says so. Criteria carry stable identifiers, one of five evidence
  classes, and a state. Contracts freeze when work starts and change only
  through an accepted change request.
- **Phases and gates.** `praukron/PHASES.md` records each phase's outcome, entry,
  exit, exit authority, and status. Gates are explicit release conditions that
  can block a phase exit. Every task names a phase or is marked `P-NONE`.
- **A deterministic runtime.** `praukron validate | compile | status | graph |
  dashboard | explain | context`. Standard-library Python, no dependencies, no
  package to install, no network, no model provider.
- **Compiled project state.** `.praukron/project.json` with provenance on every
  object, plus generated Markdown views, six Mermaid diagrams, and a
  self-contained HTML dashboard with task drill-down.
- **Analytics.** Ready work, blockers, a typed obstacle taxonomy, critical path,
  and six progress metrics reported separately rather than rolled into one
  invented number.
- **Agent context packets.** `praukron context <task>` emits the minimal packet an
  agent needs to start, with a reviewer variant carrying the finding taxonomy.
- **A builder and reviewer contract** in `AGENTS.md`, including which findings
  block completion and a seven-level hierarchy for settling disagreements.

### Changed

- **Authored and compiled state are now separate.** Authority lives in
  `praukron/`; `.praukron/` is generated and safe to delete. Deleting it and
  recompiling reproduces every file byte for byte.
- `DECISIONS.md` became one file per ADR under `praukron/ADR/`, with an index
  that records supersession without editing history.
- `STATE.md` and `TASK_GRAPH.md` are generated rather than hand-maintained.
- Task acceptance moved from prose in `TASKS.md` to a contract reference.

### Notes

Two gates are deliberately red. Agent interoperability and the continuity pilot
need a real multi-session run, not a test, and neither is claimed until then.

## 0.1.1 — 2026-09-15

Relicensed to Apache-2.0 with a separate trademark policy. The v0.1 MIT grant
and its artifacts remain unchanged.

## 0.1 — 2026-09-15

First release: the Markdown chronicle, agent instructions, host adapters, and a
one-command installer.
