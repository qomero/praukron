# Write cost: structured commands against direct edits

What does it cost an agent, in bytes and estimated tokens, to record a task, a
criterion result, a status change or a journal entry? This page reports one
measurement of that, by three methods, at two chronicle sizes. It describes a
method and one machine on one day. It is not a guarantee, and every token figure
is an estimate.

[write-tokens-2026-10-16.md](write-tokens-2026-10-16.md) measures the other side: whole
agent sessions, where each turn's fixed cost dominates, on 1.3.0, 2.0.0 and `main`, across
four models.

## Method

[`tests/bench_writes.py`](../tests/bench_writes.py) copies a real chronicle into
a temporary folder (the original is never touched) and, for each operation,
measures what an agent must read and write:

- **(a) Direct edit, full read.** The agent reads each file it edits in full, then edits it.
- **(b) Direct edit, targeted read.** The agent reads only the target section, or
  the file's last section when appending, as `AGENTS.md` instructs.
- **(c) Structured command.** The command line, the JSON payload, and the complete
  JSON output, including any follow-up call needed to finish the same work.

Rules the numbers follow:

- Bytes are UTF-8 bytes. Tokens are `bytes / 4`, an estimate and nothing more.
- The baselines are conservative. An append counts only the appended text; an
  in-place edit counts only the changed lines; the searches an agent makes to find a
  section are not counted; and a direct edit gets no validation, which the
  structured command does. Calls count a search, a read and an edit as three for (b).
- Every method finishes the same work. The script checks afterwards that the task
  is `DONE`, its three criteria are `PASS` and the journal entry exists, and runs
  `validate` after each write. Any failed archive or command is listed before any
  figure; a run is labelled "after archiving" only if archiving succeeded (else it
  is withheld); and the script then exits 1.
- Refreshed compiled views and `INDEX.md` are infrastructure and are in no figure.
- A ratio is shown only where both sides are above zero. Selection is not cost:
  these figures compare what is communicated to do one write, not how much of the
  chronicle is relevant.
- Wall time is the structured commands only, including Python start-up, the
  whole-chronicle validation and the refresh of every compiled view. Archiving
  did not change it here, because the compiler still reads archived records.

The source was a private project's chronicle (about 349 KB of Markdown across 12
phases), so it is not published. The script takes any `.praukron` folder:
`python tests/bench_writes.py --source <folder>/.praukron`.

## Results

Machine: Windows-11-10.0.26200-SP0, 12 logical CPUs. Python 3.14.7. Measured 2026-10-15 on a local copy of a real chronicle. Tokens are estimates (UTF-8 bytes / 4), not counts from a tokenizer.

Failures, reported before any aggregate: none

### As copied

Working chronicle 348,662 bytes (`TASKS.md` 54,979, `ACCEPTANCE.md` 187,709, `JOURNAL.md` 27,522); archived 0 bytes. Archived phases: none.

| Operation | Method | Read | Written | Total bytes | ~Tokens | Calls |
|---|---|---:|---:|---:|---:|---:|
| add a task with its contract | (a) direct edit, full read | 242,688 | 693 | 243,381 | 60,846 | 4 |
|  | (b) direct edit, targeted read | 2,052 | 693 | 2,745 | 687 | 6 |
|  | (c) structured command | 175 | 595 | 770 | 193 | 1 |
| record one criterion result | (a) direct edit, full read | 188,216 | 364 | 188,580 | 47,145 | 2 |
|  | (b) direct edit, targeted read | 506 | 364 | 870 | 218 | 3 |
|  | (c) structured command | 623 | 362 | 985 | 247 | 2 |
| update a task's status and evidence | (a) direct edit, full read | 55,199 | 170 | 55,369 | 13,843 | 2 |
|  | (b) direct edit, targeted read | 219 | 170 | 389 | 98 | 3 |
|  | (c) structured command | 178 | 232 | 410 | 103 | 1 |
| append a journal entry | (a) direct edit, full read | 27,522 | 248 | 27,770 | 6,943 | 2 |
|  | (b) direct edit, targeted read | 1,123 | 248 | 1,371 | 343 | 3 |
|  | (c) structured command | 229 | 275 | 504 | 126 | 1 |

| Operation | (a) / (c) | (b) / (c) | Structured wall time | `validate` after |
|---|---:|---:|---:|---|
| add a task with its contract | 316.1x | 3.6x | 0.28 s | passed |
| record one criterion result | 191.5x | 0.9x | 0.52 s | passed |
| update a task's status and evidence | 135.0x | 0.9x | 0.31 s | passed |
| append a journal entry | 55.1x | 2.7x | 0.27 s | passed |

### After archiving every COMPLETE phase

Working chronicle 150,552 bytes (`TASKS.md` 10,899, `ACCEPTANCE.md` 33,679, `JOURNAL.md` 27,522); archived 200,757 bytes. Archived phases: P0, P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11.

| Operation | Method | Read | Written | Total bytes | ~Tokens | Calls |
|---|---|---:|---:|---:|---:|---:|
| add a task with its contract | (a) direct edit, full read | 44,578 | 691 | 45,269 | 11,318 | 4 |
|  | (b) direct edit, targeted read | 2,019 | 691 | 2,710 | 678 | 6 |
|  | (c) structured command | 175 | 595 | 770 | 193 | 1 |
| record one criterion result | (a) direct edit, full read | 34,185 | 364 | 34,549 | 8,638 | 2 |
|  | (b) direct edit, targeted read | 506 | 364 | 870 | 218 | 3 |
|  | (c) structured command | 623 | 362 | 985 | 247 | 2 |
| update a task's status and evidence | (a) direct edit, full read | 11,118 | 170 | 11,288 | 2,822 | 2 |
|  | (b) direct edit, targeted read | 219 | 170 | 389 | 98 | 3 |
|  | (c) structured command | 178 | 232 | 410 | 103 | 1 |
| append a journal entry | (a) direct edit, full read | 27,522 | 248 | 27,770 | 6,943 | 2 |
|  | (b) direct edit, targeted read | 1,123 | 248 | 1,371 | 343 | 3 |
|  | (c) structured command | 229 | 275 | 504 | 126 | 1 |

| Operation | (a) / (c) | (b) / (c) | Structured wall time | `validate` after |
|---|---:|---:|---:|---|
| add a task with its contract | 58.8x | 3.5x | 0.29 s | passed |
| record one criterion result | 35.1x | 0.9x | 0.52 s | passed |
| update a task's status and evidence | 27.5x | 0.9x | 0.30 s | passed |
| append a journal entry | 55.1x | 2.7x | 0.28 s | passed |

Measurement contract for (c): `bodyBytes` is the payload, `outputBytes` the complete UTF-8 output received (JSON wrapper included), `metadataBytes` the command line; the figures are their sum over every call needed. Refreshed compiled views and `INDEX.md` are infrastructure and are excluded.

## What it shows, and what it does not

- **Against the full read, the gap is the chronicle.** Adding a task cost about
  61,000 estimated tokens by full reads before archiving and 193 by command. After
  archiving it is about 11,300 and 193: the command did not change, because its
  cost follows the record.
- **Against a careful targeted read, the command is not smaller.** For a criterion
  result the command costs about 247 tokens and the targeted edit 218; for a status
  and evidence update, 103 and 98. A criterion result takes two calls, because a
  caller who has not seen the contract's revision first sends a stale one to learn
  it. For appends (a task with its contract, a journal entry) the command is about
  3.6 and 2.7 times smaller than a targeted read.
- **So the commands are chosen for more than bytes.** They check the rules (a
  frozen contract, `DONE` needing passing criteria, owner-held tasks, duplicate
  ids), refuse a stale record instead of overwriting it, validate before and after,
  and undo themselves on failure. A targeted edit that is as cheap has none of that.
- **Archiving matters to the full read, not to the command or to the targeted
  read.** It shrinks `TASKS.md` and `ACCEPTANCE.md` for people and tools that read
  them whole. In this run `JOURNAL.md` was not archived, because that measurement
  predates journal archiving, so its figure is the same in both runs; the archive
  measurement below moves it too.
- Not measured: real agent sessions, which vary from run to run, and read-side
  commands (`context`, `retrieve`), which are unchanged by structured writes.


# Archive cost: what archiving changes for the reader

The write benchmark above asks what one write costs. This one asks what
`praukron archive` does to the files an agent has to read in order to edit
them, to `compile` and `validate`, and to what `context` and `retrieve` return.
It is a different question, so it is a separate measurement and a separate
script.

## Method

[`tests/bench_archive.py`](../tests/bench_archive.py) copies a real chronicle
into a temporary folder twice and never touches the original: once with a Git
repository initialised around the copy (`core.autocrlf` off, so Git recovery
applies), and once with none (backup recovery). On each copy it runs
`archive plan <phase>` and then `archive apply <phase> --plan <digest>` for every
COMPLETE phase and then for `P-NONE`, committing between runs in the Git copy
because Git mode requires a clean chronicle. It measures:

- the size of each working file before and after;
- what an agent reads to edit that file: the whole file, or only the target. The
  target is the file's last section when appending, and the median section when
  changing one record in place. The search to find a section is not counted;
- the wall time of `compile` and `validate`, a median of nine runs with the
  range, Python start-up included;
- parity: authored records equal except `source`; derived facts (ready,
  blocked, work in progress, gates, phase progress, unresolved failures, owner
  responses awaiting reconciliation, open debt and assumptions) equal as sets;
  and `context <task>` and `retrieve` results for a sample of 25 records equal
  record by record, apart from the `source` paths. The script exits 1 and lists
  the failure before any figure if any of them differs, or if an archive step
  fails.

Parity is also a unit test on a synthetic chronicle that is committed with the
tests ([`tests/synth_chronicle.py`](../tests/synth_chronicle.py), checked by
`tests/test_archive_parity.py`): every record type, six phases, `P-NONE` work,
Vietnamese text, a journal date shared by every phase, and LF and CRLF. It
archives every eligible phase and `P-NONE`, compares records, facts, `context`
and `retrieve`, then restores them in another order and compares again. No real
project's text is in a test or in this page, only aggregate numbers.

## Results

Machine: Windows-11-10.0.26200-SP0, 12 logical CPUs. Python 3.14.7. Measured 2026-10-16 on local copies of a real chronicle (Git and no-Git). Tokens are estimates (UTF-8 bytes / 4), not counts from a tokenizer.

Failures, reported before any aggregate: none

### Git mode

Archived 13 runs (248 records moved; recovery mode `git`); apply took a median 0.53 s per run including the refresh. Archive files: 268,437 bytes. The six measured working files total 309,954 bytes before and 49,042 after.

| Working file | Bytes before | Bytes after | Change | Whole-file read: bytes (~tokens) | Targeted append read: bytes (~tokens) | Targeted edit read: bytes (~tokens) |
|---|---:|---:|---:|---:|---:|---:|
| `TASKS.md` | 54,979 | 3,654 | -93% | 54,979 → 3,654 (13,745 → 914) | 809 → 619 (203 → 155) | 466 → 555 (117 → 139) |
| `ACCEPTANCE.md` | 187,709 | 16,481 | -91% | 187,709 → 16,481 (46,928 → 4,121) | 1,243 → 1,555 (311 → 389) | 1,438 → 657 (360 → 165) |
| `JOURNAL.md` | 27,522 | 13,536 | -51% | 27,522 → 13,536 (6,881 → 3,384) | 1,123 → 745 (281 → 187) | 743 → 749 (186 → 188) |
| `ASSUMPTIONS.md` | 8,420 | 6,085 | -28% | 8,420 → 6,085 (2,105 → 1,522) | 726 → 726 (182 → 182) | 682 → 932 (171 → 233) |
| `TECH_DEBT.md` | 19,534 | 6,684 | -66% | 19,534 → 6,684 (4,884 → 1,671) | 1,529 → 1,110 (383 → 278) | 1,201 → 1,196 (301 → 299) |
| `TRACE.md` | 11,790 | 2,602 | -78% | 11,790 → 2,602 (2,948 → 651) | 745 → 610 (187 → 153) | 485 → 633 (122 → 159) |

| Command | Before (range) | After (range) | Change in median |
|---|---:|---:|---:|
| `compile` (median of 9) | 0.21 s (0.21–0.28) | 0.22 s (0.22–0.23) | +4% |
| `validate` (median of 9) | 0.18 s (0.18–0.20) | 0.19 s (0.19–0.19) | +3% |

### No-Git mode

Archived 13 runs (248 records moved; recovery mode `nogit`); apply took a median 0.43 s per run including the refresh. Archive files: 268,437 bytes. The six measured working files total 309,954 bytes before and 49,042 after.

| Working file | Bytes before | Bytes after | Change | Whole-file read: bytes (~tokens) | Targeted append read: bytes (~tokens) | Targeted edit read: bytes (~tokens) |
|---|---:|---:|---:|---:|---:|---:|
| `TASKS.md` | 54,979 | 3,654 | -93% | 54,979 → 3,654 (13,745 → 914) | 809 → 619 (203 → 155) | 466 → 555 (117 → 139) |
| `ACCEPTANCE.md` | 187,709 | 16,481 | -91% | 187,709 → 16,481 (46,928 → 4,121) | 1,243 → 1,555 (311 → 389) | 1,438 → 657 (360 → 165) |
| `JOURNAL.md` | 27,522 | 13,536 | -51% | 27,522 → 13,536 (6,881 → 3,384) | 1,123 → 745 (281 → 187) | 743 → 749 (186 → 188) |
| `ASSUMPTIONS.md` | 8,420 | 6,085 | -28% | 8,420 → 6,085 (2,105 → 1,522) | 726 → 726 (182 → 182) | 682 → 932 (171 → 233) |
| `TECH_DEBT.md` | 19,534 | 6,684 | -66% | 19,534 → 6,684 (4,884 → 1,671) | 1,529 → 1,110 (383 → 278) | 1,201 → 1,196 (301 → 299) |
| `TRACE.md` | 11,790 | 2,602 | -78% | 11,790 → 2,602 (2,948 → 651) | 745 → 610 (187 → 153) | 485 → 633 (122 → 159) |

| Command | Before (range) | After (range) | Change in median |
|---|---:|---:|---:|
| `compile` (median of 9) | 0.23 s (0.22–0.26) | 0.22 s (0.22–0.23) | -2% |
| `validate` (median of 9) | 0.19 s (0.18–0.21) | 0.20 s (0.19–0.23) | +4% |

Reads are UTF-8 bytes of what an agent must read to edit the file: the whole file; the last section when appending; the median section when changing one record in place. Searches to locate a section are not counted. Times include Python start-up, the whole-chronicle load and, for `compile`, writing every compiled view. Parity compared authored records (except `source`), derived facts as sets, and `context` and `retrieve` results on a sample of records, before and after archiving.

## What it shows, and what it does not

- **The files an agent reads whole shrink by what was finished.** On this
  chronicle `TASKS.md` and `ACCEPTANCE.md` fell by about nine tenths, which
  matters to anyone or any tool that reads them in full. `JOURNAL.md` fell by
  half, because entries that name only open records, or only ADRs, stay. The
  moved records are in the archive files, so the project did not get smaller:
  the working set did.
- **A targeted read does not follow file size.** The figures for a targeted
  append or edit move a little in either direction, because the last section or
  the median section is now a different record, not because the cost of reading
  one record changed. An agent that follows `AGENTS.md` reads one section, before
  and after.
- **No regression in `compile` or `validate`.** Both read the whole chronicle, the
  archives included, so they do the same work before and after. The differences in
  the table are within the run-to-run range of the nine measurements and are noise
  at this size (a quarter of a second, most of it interpreter start-up). The
  expectation was no change, and none was found. (An earlier run of this script showed `compile` 9% slower for one pass; the event ordering added in this change sorts a few dozen events in about 0.01 ms, and a rerun showed -2% to +4%, so that was machine noise, not a cost.) A saving in compile time is not
  claimed.
- **`context` is unchanged, which needed a fix.** `events.recent` is the last five
  related events. It used to follow load order (each archive first, the working file
  last), so archiving a phase whose events were mixed with retained ones changed which
  five were listed; a synthetic case showed it (`EV-X7…X11` became `EV-X3, X5, X7, X9,
  X11`) and this review found it. Events are now ordered when the project is loaded
  by their own time, then the number in their id, then the id, so where an event lives
  cannot decide and every view that takes the last N agrees (context, retrieve,
  analytics and the dashboard, checked by `tests/test_archive_views.py`, including ids
  that cross 9 and 10 and tied times). `tests/test_archive_parity.py`
  checks the list before archiving, after it and after restore, and the parity check on
  the real copy compares `context` exactly.
- Not measured: real agent sessions; a chronicle much larger than this one; how
  the cost grows with many more phases; the Git mode on a repository with
  conversion attributes, which uses backups instead. Figures are one project on
  one machine on one day.

# Scale: what an agent reads, and how long a write takes, as a chronicle grows

Owners run Praukron on chronicles of tens of thousands of lines. This page measures
whether the cost of using it grows with the chronicle (T-PERF-SCALE-01), and what
T-PERF-OPT-01 changed. It is one machine on one day, and every token figure is an estimate.

## Method

[`tests/bench_scale.py`](../tests/bench_scale.py) generates synthetic chronicles
deterministically: 100 tasks per phase in four modules, each task with a three-criterion
contract and, once done, a journal entry; every phase but the last is COMPLETE, and the
last is half done with three tasks in progress. Nothing is archived, so these are worst
cases for a project that never archives. For each size it records the bytes an agent
receives from `INDEX.md`, `context`, `context <task>`, `retrieve <task>`, `retrieve
"<question>"`, `explain <task>`, `status` and one `task update` (payload and reply), and
the wall time of `compile`, `graph`, `dashboard`, `validate` and two structured writes,
each including validation and refresh. Times are the median of three fresh processes,
interpreter start included. Tokens are `bytes / 4`.

Before: praukron 2.2.5. After: `main` with T-PERF-OPT-01. Windows-11-10.0.26200-SP0, Python 3.14.7, 2026-10-28.

## Results

### What an agent receives (~tokens)

|  | 10,274 lines | 40,194 lines | 100,034 lines |
|---|---:|---:|---:|
| `INDEX.md` | 692 | 707 | 699 |
| `context` | 775 | 829 | 885 |
| `context <task>` | 610 | 611 | 611 |
| `retrieve <task>` | 956 | 958 | 958 |
| `retrieve "<question>"` | 1,288 | 1,293 | 1,294 |
| `explain <task>` | 214 | 215 | 215 |
| `status` | 375 | 502 → 388 | 754 → 450 |
| one `task update` | 74 | 74 | 74 |

### Tool time, seconds (before → after)

|  | 10,274 lines | 40,194 lines | 100,034 lines |
|---|---:|---:|---:|
| `compile` | 0.27 → 0.23 | 0.96 → 0.48 | 4.79 → 1.04 |
| `graph` | 0.24 → 0.21 | 0.82 → 0.40 | 4.81 → 0.77 |
| `dashboard` | 0.61 → 0.60 | 2.83 → 2.11 | 12.93 → 6.27 |
| `validate` | 0.23 → 0.20 | 0.79 → 0.34 | 3.93 → 0.68 |
| `task update`, with validation and refresh | 0.84 → **0.39** | 4.38 → **1.03** | 21.10 → **2.15** |
| `journal append`, with validation and refresh | 0.78 → **0.37** | 4.25 → **0.93** | 23.37 → **1.97** |

### Compiled output, bytes

|  | 10,274 lines | 40,194 lines | 100,034 lines |
|---|---:|---:|---:|
| `project.json` | 2,080,211 | 8,380,285 | 20,994,746 |
| `dashboard.html` | 4,774,240 | 18,510,737 | 46,034,569 |
| `TASK_GRAPH.md` | 70,054 | 280,071 | 702,353 |
| `STATE.md` | 3,781 | 6,857 | 13,051 |

## What it shows, and what it does not

- What an agent reads does not grow with the chronicle. From 10k to 100k lines, `INDEX.md`,
  `context <task>`, `retrieve` and `explain` stay within a few tokens, `context` grows 1.14x
  and one write costs the same 74 tokens. `status` listed every phase and grew 2x; it now folds
  COMPLETE phases into one line and grows 1.2x, the rest being this fixture's longer list of
  blocked tasks.
- Time did grow, faster than the chronicle. Two quadratic scans in journal-order checking and
  section location, a quadratic orphan-contract check and a linear task lookup made `compile`
  and `validate` 5x slower for 2.5x the lines; they are now linear. Most of a write was
  rendering a page no agent reads: since ADR-086 only `praukron dashboard` (and `--serve`, per
  request) renders it, and a write marks an existing page as out of date instead.
- The page itself is still large: about 46 MB at 100k lines, because it holds the project data,
  every text node for the language switcher and the rendered HTML. Rendering it takes about
  6 seconds there. That cost is now paid only when a person asks for the page.
- These are synthetic chronicles. Real ones have longer evidence and journal entries, and
  archiving COMPLETE phases (see above) shrinks the working files further.
