# Dashboard Mission Control

The dashboard is a deterministic view of the Chronicle. Its four workspaces
support a short path from project state to a decision and its evidence.

| Workspace | First view | Further inspection |
| --- | --- | --- |
| Home | Current phase and outcome, consequential blocker, next human action | Work in progress, next milestones and gate |
| Work | Current work and its prerequisites, separated by operational condition | All tasks, phase/module hierarchy, dependency graph, critical path, filters and progress breakdown |
| Review | Directly actionable owner decisions and assumptions to validate | Upcoming decisions, existing responses, reconciliation and verification |
| Audit | Evidence and governance | ADRs, criteria, task records, technical debt, operational events, failures and journal history |

Home keeps execution completion, validation strength and operations activity
separate. WIP describes a task, not a running agent: runtime is **unknown** until
authoritative session information exists. The latest recorded chronicle date
comes from journal headings or Date/Claimed/Time record metadata and links to its
source. When none exists, the page says "Not recorded". Planned dates and prose
are excluded, and ties use the source pointer deterministically. No clock value,
health score, ETA, schedule or event is invented.

## Attention and dependencies

`mission.project` computes the dashboard projection in Python. It does not alter
task status or the compiler's existing readiness and governance rules.

- An owner task is actionable when its dependencies are complete and it belongs
  to current work or is an earlier-phase prerequisite of current work. An owner
  task assigned to a later phase remains Upcoming, even if its dependencies are
  met, it is WIP, or current work names it as a prerequisite. A recorded owner VERIFY
  moves it to the answered group while the agent records the resulting state;
  in the current phase, with no unmet dependencies or failures, it is Ready for
  that agent action. A future-phase verification does not make work Ready early.
- Explicit BLOCKED status, failed acceptance evidence and unresolved failure
  events are technical causes. Missing dependencies remain visible as missing
  records. Ordinary unfinished work is a dependency wait, not a failure.
- Each root appears once. Graph traversal produces its complete, sorted set of
  unfinished descendants; a set union counts shared downstream tasks once.
  Clearing one root may leave another dependency unresolved.
- Future owner work is labelled upcoming, outside the current phase. Owner responsibility does
  not propagate to downstream agent tasks.
- Review orders current impact before recorded permission or HIGH-impact
  sensitivity (the recorded proxy for irreversible consequences), then
  downstream cost and ID. Assumptions remain
  provisional; a response must be reconciled through the established pathway.

Work separates Ready, In progress, Upcoming, Waiting for human, Waiting for dependencies,
Blocked by failure and Completed. The projection describes each task's present
condition. The original status, acceptance contract and full diagnostics remain
available in Audit. Phase gates remain distinct from task-dependency roots.

## Navigation, search and accessibility

Search everything is available in the shell, with `/` and Ctrl/Cmd+K shortcuts.
Its embedded index covers tasks, ADRs, assumptions, responses, criteria, modules,
phases and journal entries. Matching ignores case; exact IDs precede other
matches with deterministic ordering. Every result includes kind, ID, title,
dashboard destination and the source `file#anchor`.

Arrow keys move among search results, Enter opens a result, Tab stays inside the
native dialog and Escape closes it. Selecting a result reveals its workspace and
enclosing disclosure, clears conflicting task filters and focuses its record.
Workspace tabs support arrow keys, Home and End. Old workspace hashes still
route to their corresponding inspection views.

Existing light/dark tokens and system-theme behavior remain. At phone width the
layout stacks and Review appears first in the workspace bar. Visible focus,
labels and reduced-motion styles support keyboard use. Content remains readable
without JavaScript; search is hidden. All six views draw natively in Python,
using inline SVG or HTML. Each retains its expandable Mermaid source alongside
the drawing. Critical path, phases and gates share node/edge selections with
their exports; dependency ordering shares depth bands, and calendar Gantt shares
phase groups and recorded schedules. Dependency ordering never implies dates.
Calendar bars use an authored end or duration (ms, s, m, h, d or w); invalid or
unsupported metadata gets no invented bar. Unscheduled work is counted separately.
Empty views explain what is missing. Undated work can still have a dependency
order; its calendar shows an empty state. Status labels accompany themed marks.
Drawings retain readable type and scroll inside a focusable region at phone width.
With JavaScript, the diagram tabs support arrows, Home and End. Without it, all
drawings and source disclosures remain available. No browser renderer is needed.
There are no external assets or network requests in static mode.

`dashboard --serve` retains the existing token-protected response pathway. It
adds response controls to Review; responses are written through `respond`.
Opening an assumption is not itself a submission. Source-file links are relative
to the generated file's location; a local server may not serve those Markdown
files, so the same records and evidence also have dashboard destinations.

## Reproducible evaluation

Run `py -3 -m unittest discover -s tests` (or `python3` on Unix). The mission
tests cover DAG diamonds, independent causes, future obligations, missing
dependencies, recorded failure, empty/completed projects, deterministic output,
search ordering, destinations and accessibility markup. `test_drawings.py`
compares the nodes, edges, order and dates in each native view against its
Mermaid source on one synthetic fixture, and checks empty states, determinism,
escaped labels and keyboard view switching. Existing response,
verification, filter and contrast tests remain regression checks.

For a synthetic project, use `tests/synth_chronicle.py` in a temporary directory,
or the fixtures in `tests/test_review.py` for owner decisions and assumptions.
Generate with this checkout's `.praukron/praukron -C <temporary-project> dashboard`.
For a real project, first copy its `.praukron` to a temporary directory outside
both repositories, then run this checkout against that copy. Never edit the
source project or include its record text in published evidence.

Evaluate a fresh static page at 1440 × 1000 and 390 × 844, in both themes, then
repeat keyboard-only and with JavaScript disabled. Use a participant who has not
seen the fixture; start each timed task from Home with no filters:

1. **5 seconds:** ask for the phase, condition and most consequential blocker.
   Stop timing when all three are identified. Check each against the projection.
2. **15 seconds:** ask for the next decision they can personally act on, why it
   matters and what work depends on it. Follow Home's action into Review.
3. **30 seconds:** ask them to open a blocker's cause, a dependency and supporting
   acceptance evidence. Follow Work's root details into the task record and its
   criterion in Audit. Record clicks, backtracking and time to correct evidence.
4. Search for an exact ID, an assumption, a criterion and journal words. Exercise
   no results, Escape, focus return, arrow navigation and selection under filters.
5. On a disposable served fixture, submit guidance and verify that RESPONSES.md
   changed through the existing endpoint. Do not verify real owner work during
   a UI test. Check future work offers no premature verification control.

Record success/failure and actual elapsed time for each participant, viewport and
theme. Automated timing or an implementer's route walkthrough is not proof of
the usability targets. Report root/impact aggregates only for private projects.

The implementation walkthrough checks these routes on a small synthetic fixture
and a temporary complex-project copy. Independent participant timing and assistive
technology testing remain separate from those implementation checks.
