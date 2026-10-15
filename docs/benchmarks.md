# Write cost: structured commands against direct edits

What does it cost an agent, in bytes and estimated tokens, to record a task, a
criterion result, a status change or a journal entry? This page reports one
measurement of that, by three methods, at two chronicle sizes. It describes a
method and one machine on one day. It is not a guarantee, and every token figure
is an estimate.

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
  them whole; `JOURNAL.md` is not archived yet, so its figure is the same in both
  runs.
- Not measured: real agent sessions, which vary from run to run, and read-side
  commands (`context`, `retrieve`), which are unchanged by structured writes.
