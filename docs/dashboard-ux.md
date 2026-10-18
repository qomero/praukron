# Dashboard Mission Control

The dashboard is a deterministic view of the Chronicle. Its four workspaces
support a short path from project state to a decision and its evidence.

## Language

Add `- Language: vi` to the thesis in `THESIS.md` for Vietnamese, or `en` for
English. This field is included in the compiled thesis. Missing or unsupported
values render English; `validate` warns about unsupported values.
`praukron dashboard --lang en` overrides the field, including with `--serve`.

The static HTML is localized on the server and works without JavaScript. The
labelled header selector offers English and Tiếng Việt. A viewer choice saved
under this project's name in browser storage
takes precedence, followed by the explicit CLI/project language. Only when
neither is recorded does the browser language apply. Storage reads and writes
are guarded; unavailable storage does not prevent using the selector.

Both catalogues and the original UI text are embedded, so switching works
offline and never changes the chronicle. Generation does not read the clock,
browser settings or process locale: identical input and CLI options produce
identical bytes. Record titles, outcomes, criteria, evidence, responses, source
text and identifiers remain as authored. Vietnamese metadata dates use
day/month/year; English retains unambiguous ISO dates. Dates inside recorded
prose remain verbatim. Counts use language-appropriate labels, including
singular/plural search and filter messages.

### Adding a language

Add one entry to `CATALOGUES` in `src/praukron/i18n.py`, containing its native
`name`, `dateOrder` (`ymd` or `dmy`), and a complete `messages` dictionary with
the same keys as English. No selector, CLI, validator or renderer changes are
needed. The English messages are the lookup keys. Prefer complete messages;
shared labels also cover dynamic state summaries. Count messages use complete
phrases such as `{done} of {total} execution tasks done`; keep the placeholder
names unchanged, but order them naturally for the language. Every compiled
prose field is protected before the HTML boundary by default; only declared
identifiers, enums, dates and compiler-generated explanations are exempt.
Links, form values, code and embedded project data are not translated.

Run `python -m unittest discover -s tests -p test_i18n.py`. The tests enforce
catalogue parity and detect uncatalogued visible text and accessible names in
the synthetic dashboard and all client scripts, as well as server/client translation parity, fallback,
CLI overrides, deterministic output and the response pathway in both languages.
When adding UI, include its new states in the coverage fixture and check live
search, task details and review feedback as well as static markup.

| Workspace | First view | Further inspection |
| --- | --- | --- |
| Home | Phase timeline, current-phase card, next human action | Module progress, gate, root blocker, active work and separate execution/validation/operations figures |
| Work | Current work and its prerequisites, separated by operational condition | All tasks, phase/module hierarchy, dependency graph, critical path, filters and progress breakdown |
| Review | Counted groups; each item has a title, one-line why, affected task and primary action | One disclosure per item for records, reasoning, response controls, reconciliation and verification |
| Audit | Evidence and governance | ADRs, criteria, task records, technical debt, operational events, failures and journal history |

Work starts with a state board: Ready, In progress, Upcoming, Waiting for human,
Waiting for dependencies and Blocked by failure. Populated columns use the
available width; empty columns keep a compact height and single-line label and
count. Busy states preview three cards, with a native "+M more" disclosure for
the rest so blockers and inspection remain nearby. Each task card
names its ID, title, module and responsible person or owner authority. Completed
has a separate count and disclosure. Current work names the completed count in
the current phase alongside the project total; All work shows the overall count.
Current work / All work updates the cards,
counts, column widths and ready/waiting summary together. On narrow screens the
board scrolls horizontally inside a focusable strip.

Root blockers and the next gate with milestones sit beside one another below
the board, stacking on phones. Five inspection tabs open Critical path, Phases,
Task registry, Dependency graph (including drawings and sources), and Breakdown.
Arrow keys, Home and End select tabs; search results and legacy links reveal the
appropriate view before moving focus. Without JavaScript, the tabs are ordinary
anchor links and all inspection sections and task cards remain available.

Home keeps execution completion, validation strength and operations activity
separate. WIP describes a task, not a running agent: runtime is **unknown** until
authoritative session information exists. The latest recorded chronicle date
comes from journal headings or Date/Claimed/Time record metadata and links to its
source. When none exists, the page says "Not recorded". Planned dates and prose
are excluded, and ties use the source pointer deterministically. No clock value,
health score, ETA, schedule or event is invented.

Home starts with a horizontal timeline in recorded phase order. Each step names
its state (Completed, Current or Upcoming) with text and a symbol, and shows exact
execution completion counts. The current phase is distinct and links to its
full record in Work. On phones the timeline scrolls inside its own strip and
opens with the current step visible. Without JavaScript, steps wrap instead.
Following a phase link leaves its heading below the sticky navigation.

Below it, the main card groups the current phase's ID, name, outcome, completion,
modules, next gate and unmet criteria count, root blocker and work in progress.
With no current phase or active work, Home says so plainly. Module bars keep
counts on the same line and name their values for assistive technology. Zero-task
modules say "No tasks"; operations get a separate labelled row and never enter
execution totals. The side column presents the next human action, its Decide or
Verify link into Review (Resolve for recorded validation errors), downstream
impact and count of other owner actions, separately from assumptions to validate,
followed by compact project figures. Zero counts are omitted and labels pluralise.
At phone width, the action follows the timeline, then the phase card and figures.

Home contains no tables. Work retains execution completion by phase, unphased
execution, the patterned status chart and its complete accessible table, along
with milestones, full gate detail and root causes. Its root summary preserves
active roots, distinct downstream tasks (the union, not the sum of each root's
impact), and future owner obligations. Completion counts DONE tasks;
status uses the existing mission states. Validation remains its own measure.
Existing light/dark tokens and focus states apply throughout. No charting library,
external asset or JavaScript is required to read the content.

Review keeps all four groups visible, including a one-line empty state when a
group has no items. Its groups do not collapse: opening an item's primary action
reveals its full details in one step. Assumptions show their status, impact,
reasoning, references and existing responses inside that disclosure. Response
controls appear first when served. A separate disclosure holds assumption filters
and the ordering explanation. Opening an item still does not submit a response;
the existing live Submit action and authentication rules apply.

An owner task says **Decide** until its recorded contract criteria all pass;
then it says **Verify** and offers verification controls. The summary names its
task and downstream count. Compilation also carries validation findings and
explicit owner commands into Review: errors such as a HUMAN_VERIFIED task
missing its named owner's VERIFY appear in Actionable now even when the task is
already DONE. Their disclosures contain the validator's exact terminal commands,
and Home's action count includes them. Review does not infer new errors, change
recorded completion, or run those commands on the owner's behalf.

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
  not propagate to downstream agent tasks. If current-phase work depends on a
  later-phase owner task, Home and Work name it as an upcoming owner decision
  blocking current work, with its downstream count. Review keeps it under
  Upcoming decisions with the same note; it is not made actionable early.
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

Eligible Review items have a tick button: **Verify** records `VERIFY` for an owner
task with every criterion passing; **Confirm** records `CONFIRM` for an unanswered
open assumption. Home's next-action card offers the same button when eligible.
Each button records just that item immediately. The separate **Select** checkbox
only selects an item; **Approve selected** (Vietnamese: **Duyệt các mục đã chọn**)
records all selected items in one atomic response batch. Unselected items and
draft feedback are not included. Decisions and validation actions retain their
existing forms or commands. Static ticks are visible but disabled, with a
`dashboard --serve` hint.

Approval uses the existing `/respond` endpoint and session token, with `By` from
the active GitHub login. Eligibility is rechecked under the response write lock;
a stale or refused item names its target and records none of the batch. Success
refreshes the page, announces the result, and moves responses to **Previously
answered** for agent reconciliation. Responses are append-only; there is no undo.
`test_dashboard_ticks.py` covers these controls, single and batch HTTP writes,
identity, stale-state refusals, unchanged files, static mode and localization.

## Reproducible evaluation

Run `py -3 -m unittest discover -s tests` (or `python3` on Unix). The mission
tests cover DAG diamonds, independent causes, future obligations, missing
dependencies, recorded failure, empty/completed projects, deterministic output,
search ordering, destinations and accessibility markup. `test_drawings.py`
compares the nodes, edges, order and dates in each native view against its
Mermaid source on one synthetic fixture, and checks empty states, determinism,
escaped labels and keyboard view switching. Existing response,
verification, filter and contrast tests remain regression checks.
`test_home_timeline.py` maps the four timeline-first acceptance criteria to phase
order/state/counts, grouped phase details and actions, retained destinations,
mobile reading order, theme/focus markup, escaping, empty states and determinism.
`test_home_progress.py` covers phase/module prominence, cross-domain counts,
empty and unphased work, named SVG/text equivalents, escaping, determinism,
single-level Review summaries and retained records/response controls.

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
