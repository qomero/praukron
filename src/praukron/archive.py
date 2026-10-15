"""Archive closed lifecycle records: the owner's command, never an agent's.

DONE tasks and contracts in a COMPLETE phase or P-NONE, plus eligible closed
assumptions, technical debt, and trace events, move verbatim into
`archive/<phase>/` (ADR-070). Archived records stay authority: the compiler
reads them with the working files, so every command sees the same project.
Only where each record lives changes.

A task stays in the working files while anything open still points at it. The
move is refused unless validation is clean before it; it is made file by file
atomically, and undone byte for byte if the records read back differently or
validation finds a new error.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Protocol

from . import analytics, archive_recovery, compile as compiler, layout, validate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR
from .model import DEBT_CLOSED, NO_PHASE, Project
from .mutate import (RespondError, _lock, _replace, _spans, _write_recovery, recover_archive,
                     staged_replacements)
from .parse import ParseError
from .snapshot import Snapshot

_TASK = re.compile(r"(T-[A-Za-z0-9.\-]+): ")
_CONTRACT = re.compile(r"(AC-[A-Za-z0-9.\-]+)(?: — |$)")
_ASSUMPTION = re.compile(r"(A-[A-Za-z0-9.\-]+): ")
_DEBT = re.compile(r"(TD-[A-Za-z0-9.\-]+): ")
_EVENT = re.compile(r"(EV-[A-Za-z0-9.\-]+): ")


class ArchiveError(Exception):
    """An archive was refused, or a move failed and was undone."""

    def __init__(self, message: str, code: str = "archive-refused"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Selection:
    phase: str

    def check(self, project: Project) -> None:
        if self.phase == NO_PHASE:
            return
        phase = project.phase(self.phase)
        if phase is None:
            raise ArchiveError(f"{self.phase} is not a phase in PHASES.md", "unknown-phase")
        if phase.status != "COMPLETE":
            raise ArchiveError(f"{self.phase} is {phase.status}; only a COMPLETE phase is archived",
                               "incomplete-phase")

    def destination(self, name: str) -> str:
        return f"{ARCHIVE_DIR}/{self.phase}/{name}"


@dataclass(frozen=True)
class Candidate:
    id: str
    kind: str
    source: str
    destination: str
    reason: str | None = None

    @property
    def moves(self) -> bool:
        return self.reason is None

    def as_json(self) -> dict[str, object]:
        return {"id": self.id, "kind": self.kind, "source": self.source,
                "destination": self.destination, "action": "moves" if self.moves else "stays",
                "reason": self.reason}


@dataclass
class Plan:
    phase: str
    tasks: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    debts: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)  # (task, why it stays)
    moved_bytes: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    digest: str | None = None
    mode: str | None = None
    version: str | None = None
    operation: str = "archive"
    recovery: dict = field(default_factory=dict)
    snapshot: Snapshot | None = field(default=None, repr=False, compare=False)
    writes: dict[Path, str] = field(default_factory=dict, repr=False, compare=False)
    changed_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "phase": self.phase, "tasks": self.tasks, "contracts": self.contracts,
            "assumptions": self.assumptions, "debts": self.debts, "events": self.events,
            "kept": [{"task": t, "why": w} for t, w in self.kept], "movedBytes": self.moved_bytes,
            "candidates": [c.as_json() for c in self.candidates],
            "digest": self.digest, "mode": self.mode, "version": self.version,
            "changedFiles": self.changed_files, "createdFiles": self.created_files,
            "operation": self.operation, "recovery": self.recovery,
            "warnings": self.warnings,
        }

    def render(self, applied: bool) -> str:
        verb = "Moved" if applied else "Would move"
        target = (f"{AUTHORITY_DIR}/" if self.operation == "restore" else
                  f"{AUTHORITY_DIR}/{ARCHIVE_DIR}/{self.phase}/")
        moving = [candidate for candidate in self.candidates if candidate.moves]
        counts = ", ".join(
            f"{len(records)} {label}" for label, records in (
                ("tasks", self.tasks), ("contracts", self.contracts),
                ("assumptions", self.assumptions), ("debt", self.debts),
                ("events", self.events),
            ) if records
        ) or "no records"
        lines = [f"{verb} {counts} ({len(moving)} records; {self.moved_bytes:,} bytes) "
                 f"of {self.phase} to {target}"]
        if self.tasks:
            lines.append("  tasks:     " + ", ".join(self.tasks))
        if self.contracts:
            lines.append("  contracts: " + ", ".join(self.contracts))
        for task, why in self.kept:
            lines.append(f"  kept {task}: {why}")
        for candidate in self.candidates:
            if candidate.kind != "task" and not candidate.moves:
                lines.append(f"  kept {candidate.id}: {candidate.reason}")
            elif candidate.kind not in ("task", "contract") and candidate.moves:
                lines.append(f"  {candidate.kind} {candidate.id}: {candidate.source} -> {candidate.destination}")
        if self.changed_files:
            lines.append("  files: " + ", ".join(self.changed_files))
        if self.created_files:
            lines.append("  created: " + ", ".join(self.created_files))
        if any(name.casefold() == _JOURNAL_ORDER.casefold() for name in self.changed_files):
            lines.append(f"  ordering metadata: {_JOURNAL_ORDER} (ADR-072)")
        if self.digest:
            lines.append(f"Plan: {self.digest} (mode: {self.mode}, version: {self.version})")
        lines.extend(f"  warning: {message}" for message in self.warnings)
        lines.extend(archive_recovery.describe(self.recovery))
        return "\n".join(lines) + "\n"


@dataclass
class EligibilityContext:
    project: Project
    selection: Selection
    root: Path | None
    result: Plan
    snapshot: Snapshot | None = None

    def text(self, name: str) -> str:
        """Exact captured text for a rule's section and eligibility checks."""
        if self.snapshot is None:
            raise ArchiveError("section eligibility needs a chronicle snapshot")
        return self.snapshot.text(name)


def section_locations(text: str, pattern: re.Pattern[str] | None = None) -> dict[str, list[tuple[int, int]]]:
    """All locations per ID, or per full heading when pattern is None; duplicates survive."""
    found: dict[str, list[tuple[int, int]]] = {}
    for heading in re.finditer(r"(?m)^## (.+)$", text):
        title = heading.group(1).strip()
        match = pattern.match(title) if pattern is not None else None
        if pattern is not None and match is None:
            continue
        key = match.group(1) if match is not None else title
        following = re.search(r"(?m)^#{1,2} ", text[heading.end():])
        end = heading.end() + following.start() if following else len(text)
        found.setdefault(key, []).append((heading.start(), end))
    return found


class IdRule:
    """Default locator for ID-keyed rule implementations."""

    pattern: re.Pattern[str]

    def sections(self, text: str) -> dict[str, list[tuple[int, int]]]:
        return section_locations(text, self.pattern)


class RecordRule(Protocol):
    """One record type's eligibility and section format; rules run in dependency order."""

    kind: str
    filename: str
    title: str

    def sections(self, text: str) -> dict[str, list[tuple[int, int]]]:
        """Exact-text offsets by ID/full heading; repeated locators must retain every span."""
        ...

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]: ...


class TaskRule(IdRule):
    kind, filename, pattern, title = "task", "TASKS.md", _TASK, "Tasks"

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection = context.project, context.selection
        unresolved = set(analytics.unresolved_failures(project))
        waiting = {w["assumption"] for w in analytics.awaiting_reconciliation(project)}
        selected = [t for t in project.tasks
                    if t.phase == selection.phase and t.source.file == self.filename]
        eligible = {t.id for t in selected
                    if t.done and _why_kept(project, t.id, unresolved, waiting) is None}
        if selection.phase == NO_PHASE:
            # Keep every task that a remaining working task depends on.  A
            # dependant can itself be held by another dependant, so calculate
            # the fixed point rather than moving only the first layer.
            while True:
                remaining = {
                    task_id for task_id in eligible
                    if all(task.id in eligible for task in project.tasks
                           if task.source.file == self.filename and task_id in task.dependencies)
                }
                if remaining == eligible:
                    break
                eligible = remaining
        for task in selected:
            why = "it is not DONE" if not task.done else _why_kept(project, task.id, unresolved, waiting)
            if why is None and selection.phase == NO_PHASE:
                dependants = [t.id for t in project.tasks
                              if t.source.file == self.filename and task.id in t.dependencies
                              and t.id not in eligible]
                if dependants:
                    why = "working task(s) depend on it: " + ", ".join(dependants)
            yield Candidate(task.id, self.kind, self.filename, selection.destination(self.filename), why)


class ContractRule(IdRule):
    kind, filename, pattern, title = "contract", "ACCEPTANCE.md", _CONTRACT, "Acceptance"

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection = context.project, context.selection
        moving = set(context.result.tasks)
        staying = [t for t in project.tasks if t.source.file == "TASKS.md" and t.id not in moving]
        relevant = {t.contract for t in project.tasks if t.phase == selection.phase}
        nested: set[str] = set()
        if context.root is not None:
            text = context.text(self.filename)
            nested = {i for i, spans in self.sections(text).items() for s, e in spans
                      if re.search(r"(?m)^### AC-", text[s:e])}
        # Follow moving-task order for compatibility with the original planner.
        order = list(dict.fromkeys(project.task(i).contract for i in context.result.tasks))
        order += [i for i in project.contracts if i in relevant and i not in order]
        for contract_id in order:
            contract = project.contracts.get(contract_id)
            if contract is None or contract.source.file != self.filename:
                continue
            why = None
            users = [t.id for t in staying if t.contract == contract.id]
            inheritors = [c.id for c in project.contracts.values()
                          if c.id != contract.id and contract.id in c.inherits]
            if users:
                why = "used by working task(s): " + ", ".join(users)
            elif inheritors:
                why = "inherited by contract(s): " + ", ".join(inheritors)
            elif contract.id in nested:
                why = "holds an inherited invariant"
            yield Candidate(contract.id, self.kind, self.filename, selection.destination(self.filename), why)


def latest_phase(project: Project, phases: Iterable[str]) -> str | None:
    """Latest named phase in PHASES.md order, with P-NONE ordered last."""
    named = list(dict.fromkeys(phases))
    if not named:
        return None
    order = {phase.id: index for index, phase in enumerate(project.phases)}
    return max(named, key=lambda phase: (phase == NO_PHASE, order.get(phase, -1), phase))


def _task_destination(project: Project, task_ids: Iterable[str]) -> str | None:
    return latest_phase(project, (task.phase for task_id in task_ids
                                  if (task := project.task(task_id)) is not None))


def _all_archived_or_moving(project: Project, task_ids: Iterable[str], moving: set[str]) -> str | None:
    for task_id in task_ids:
        task = project.task(task_id)
        if task is None:
            return f"task {task_id} does not exist"
        if task.source.file == "TASKS.md" and task_id not in moving:
            return f"task {task_id} is still working"
    return None


class AssumptionRule(IdRule):
    kind, filename, pattern, title = "assumption", "ASSUMPTIONS.md", _ASSUMPTION, "Assumptions"

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection = context.project, context.selection
        waiting = {w["assumption"] for w in analytics.awaiting_reconciliation(project)}
        moving = set(context.result.tasks)
        for assumption in project.assumptions:
            if assumption.source.file != self.filename:
                continue
            task_ids = list(dict.fromkeys(
                [*assumption.tasks, *(reference for reference in assumption.references
                                      if reference.startswith("T-"))]
            ))
            destination_phase = _task_destination(project, task_ids)
            if destination_phase != selection.phase:
                continue
            why = None
            if assumption.status not in ("CONFIRMED", "REJECTED", "REVISED", "WITHDRAWN"):
                why = f"status is {assumption.status}"
            elif assumption.id in waiting:
                why = "a response awaits reconciliation"
            else:
                why = _all_archived_or_moving(project, task_ids, moving)
            yield Candidate(assumption.id, self.kind, self.filename,
                            selection.destination(self.filename), why)


class DebtRule(IdRule):
    kind, filename, pattern, title = "debt", "TECH_DEBT.md", _DEBT, "Technical debt"

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection = context.project, context.selection
        moving = set(context.result.tasks)
        for debt in project.debts:
            if debt.source.file != self.filename:
                continue
            task_ids = [reference for reference in (*debt.introduced_by, *debt.linked_tasks)
                        if reference.startswith("T-")]
            destination_phase = _task_destination(project, task_ids)
            if destination_phase is None and selection.phase != NO_PHASE:
                continue
            if destination_phase is not None and destination_phase != selection.phase:
                continue
            why = None
            if debt.status not in DEBT_CLOSED:
                why = f"status is {debt.status}"
            elif not task_ids:
                why = "names no task"
            else:
                why = _all_archived_or_moving(project, task_ids, moving)
            yield Candidate(debt.id, self.kind, self.filename,
                            selection.destination(self.filename), why)


def _unresolved_retry_chain(project: Project) -> set[str]:
    """Every event connected through retries to an unresolved failure."""
    chain = set(analytics.unresolved_failures(project))
    changed = True
    while changed:
        changed = False
        for event in project.events:
            if event.id in chain or event.retry_of in chain:
                if event.id not in chain:
                    chain.add(event.id)
                    changed = True
                if event.retry_of and event.retry_of not in chain:
                    chain.add(event.retry_of)
                    changed = True
    return chain


class TraceRule(IdRule):
    kind, filename, pattern, title = "event", "TRACE.md", _EVENT, "Trace"

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection = context.project, context.selection
        moving = set(context.result.tasks)
        unresolved = _unresolved_retry_chain(project)
        for event in project.events:
            if event.source.file != self.filename:
                continue
            task = project.task(event.task) if event.task else None
            destination_phase = task.phase if task is not None else NO_PHASE
            if destination_phase != selection.phase:
                continue
            why = None
            if event.id in unresolved:
                why = "part of an unresolved retry chain"
            elif event.task and task is None:
                why = f"task {event.task} does not exist"
            elif task is not None:
                why = _all_archived_or_moving(project, [task.id], moving)
            yield Candidate(event.id, self.kind, self.filename,
                            selection.destination(self.filename), why)


_JOURNAL = re.compile(r"(\d{4}-\d{2}-\d{2} — .+)")
_NAMED = re.compile(r"(?<![A-Za-z0-9_\-])((?:AC|ADR|TD|EV|A|T)-[A-Za-z0-9.\-]*[A-Za-z0-9])")
_JOURNAL_ORDER = f"{ARCHIVE_DIR}/JOURNAL.order.json"


class JournalRule:
    """A dated entry moves with the records it names (ADR-070).

    It moves when it names at least one archivable record and every one it names
    is archived or moving in this run, to the archive of the latest phase among
    them (PHASES.md order, P-NONE last). ADR ids never count: ADRs are not
    archived, so an entry that names only ADRs names nothing to archive and
    stays. An id that resolves to no record keeps the entry, as does a heading
    that is not unique across the working file and every archive, because a
    move would be ambiguous. Entries move as exact byte spans and are placed in
    the archive by entry date and the persisted heading order for ties. The
    shared archive/JOURNAL.order.json (ADR-072) records working and archived headings,
    including entries that stay, before the first move can lose their order.
    It is ordering metadata, never an eligibility or plan cache. An entry whose
    last line has no line ending stays unless it lands at the end of an archive
    whose last entry is untouched.
    """

    kind, filename, title = "journal", "JOURNAL.md", "Journal"
    after_records = True  # plan() runs it after every other rule: it sees all their moves

    def sections(self, text: str) -> dict[str, list[tuple[int, int]]]:
        return section_locations(text)

    def candidates(self, context: EligibilityContext) -> Iterable[Candidate]:
        project, selection, snapshot = context.project, context.selection, context.snapshot
        if snapshot is None or not snapshot.is_file(self.filename):
            return
        text = snapshot.text(self.filename)
        locations = self.sections(text)
        counts = {h: len(spans) for h, spans in locations.items()}
        for name, other in _journal_files(snapshot).items():
            if name != snapshot.resolve(self.filename):
                for heading, spans in self.sections(other).items():
                    counts[heading] = counts.get(heading, 0) + len(spans)
        located = _locations(project)
        moving = {c.id for c in context.result.candidates if c.moves}
        entries: list[list] = []  # [heading, exact section, destination, reason], in working order
        for heading, spans in locations.items():
            if not _JOURNAL.match(heading):
                continue
            start, end = spans[0]
            phases, open_records, unknown = [], [], []
            for named in dict.fromkeys(_NAMED.findall(text[start:end])):
                if named.startswith("ADR-"):
                    continue
                if named not in located:
                    unknown.append(named)
                    continue
                file, owner = located[named]
                if owner in moving or named in moving:
                    phases.append(selection.phase)
                elif file.startswith(f"{ARCHIVE_DIR}/"):
                    phases.append(file.split("/")[1])
                else:
                    open_records.append(named)
            reason = None
            if counts[heading] > 1:
                reason = "duplicate heading; it is not archived ambiguously"
            elif unknown:
                reason = "names id(s) that resolve to no record: " + ", ".join(unknown)
            elif open_records:
                reason = "names record(s) not archived: " + ", ".join(open_records)
            elif not phases:
                reason = "names no archivable record"
            destination = (f"{ARCHIVE_DIR}/{latest_phase(project, phases)}/{self.filename}"
                           if reason is None else selection.destination(self.filename))
            entries.append([heading, text[start:end], destination, reason])
        newline = "\r\n" if "\r\n" in text else "\n"
        _settle(entries, snapshot, newline)
        for heading, _, destination, reason in entries:
            yield Candidate(heading, self.kind, self.filename, destination, reason)

    def place(self, archived: str, entries: list[tuple[str, str]], newline: str,
              order: dict[str, int]) -> str:
        """Insert whole sections, including trailing breaks, by the captured order."""
        for heading, section in entries:
            try:
                archived = _insert_entry(archived, heading, section, newline, order)
            except _Unplaceable as problem:
                raise ArchiveError(f"cannot place journal entry {heading}: {problem}") from problem
        return archived


    def restore(self, working: str, entries: list[tuple[str, str]], newline: str,
                order: dict[str, int]) -> str:
        """Return sections using the same stable order as archive placement."""
        text = working if working else f"# Journal{newline}"
        try:
            for heading, section in entries:
                text = _insert_entry(text, heading, section, newline, order)
        except _Unplaceable as problem:
            raise ArchiveError(f"cannot restore journal entry: {problem}", "restore-refused") from problem
        return text


def _journal_files(snapshot: Snapshot) -> dict[str, str]:
    """The working journal and every archived one, by captured name."""
    found = {}
    for name in snapshot.files:
        parts = PurePosixPath(name).parts
        if name.casefold() == "journal.md" or (
                len(parts) == 3 and parts[0].casefold() == ARCHIVE_DIR and parts[2].casefold() == "journal.md"):
            found[name] = snapshot.text(name)
    return found


def _journal_sections(text: str) -> list[tuple[str, int, int]]:
    return sorted(((h, s, e) for h, spans in section_locations(text).items() for s, e in spans), key=lambda e: e[1])


def _entry_date(heading: str) -> str:
    return heading[:10] if re.match(r"\d{4}-\d{2}-\d{2}", heading) else ""


_ORDER_REPAIR = ("Repair: recover the matching order file and journal headings from Git history or "
                 ".praukron/backup/<timestamp>/. After later edits, reconcile against that history; "
                 "do not undo unrelated work. If every date is wholly in one journal, "
                 "remove the sidecar and the next journal move rebuilds it. Never hand-edit the JSON.")


@dataclass
class _JournalOrderState:
    order: dict[str, int] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _journal_order_state(snapshot: Snapshot) -> _JournalOrderState:
    """ADR-072: files govern observed ties; metadata remembers only separated ties.

    Diagnose drift without stopping unrelated moves. Incomplete metadata is
    never evidence for a cross-file tie, even when its JSON schema is valid.
    """
    state = _JournalOrderState()
    sequences = [[h for h, _, _ in _journal_sections(text)]
                 for text in _journal_files(snapshot).values()]
    counts = state.counts
    for sequence in sequences:
        for heading in sequence:
            counts[heading] = counts.get(heading, 0) + 1
    # Duplicate headings stay and are reported by eligibility, not ordered as
    # if they identified one entry.
    sequences = [[h for h in sequence if counts[h] == 1] for sequence in sequences]
    saved = []
    if snapshot.is_file(_JOURNAL_ORDER):
        try:
            data = json.loads(snapshot.text(_JOURNAL_ORDER))
            if (not isinstance(data, dict) or set(data) != {"version", "headings"}
                    or type(data["version"]) is not int or data["version"] != 1
                    or not isinstance(data["headings"], list)
                    or any(not isinstance(h, str) or not h or "\n" in h or "\r" in h
                           for h in data["headings"])
                    or len(set(data["headings"])) != len(data["headings"])):
                raise ValueError("expected version 1 and unique full headings")
            stale = [h for h in data["headings"] if h not in counts]
            if stale:
                state.warnings.append("order file names headings absent from every journal "
                                      "(renamed or removed): " + ", ".join(stale) + ". " + _ORDER_REPAIR)
            saved = [h for h in data["headings"] if counts.get(h) == 1]
        except (ValueError, UnicodeError) as error:
            state.warnings.append(f"invalid {_JOURNAL_ORDER}: {error}. {_ORDER_REPAIR}")
    state.saved = saved
    owners = {h: i for i, sequence in enumerate(sequences) for h in sequence}
    dates: dict[str, list[str]] = {}
    for sequence in sequences:
        for heading in sequence:
            dates.setdefault(_entry_date(heading), []).append(heading)
    positions = {h: i for i, h in enumerate(saved)}
    for date, headings in sorted(dates.items()):
        missing = [h for h in headings if h not in positions]
        if date and len({owners[h] for h in headings}) > 1 and missing:
            state.blocked[date] = (f"same-date journal order is unavailable across files for {date}; "
                                   f"missing headings: {', '.join(missing)}. {_ORDER_REPAIR}")
            continue
        priority = {h: i for i, h in enumerate(dict.fromkeys([*(h for h in saved if h in headings), *headings]))}
        following: dict[str, set[str]] = {h: set() for h in headings}
        incoming = dict.fromkeys(headings, 0)

        def edge(before, after):
            if after not in following[before]:
                following[before].add(after)
                incoming[after] += 1

        for sequence in sequences:
            local = [h for h in sequence if _entry_date(h) == date]
            for before, after in zip(local, local[1:]):
                edge(before, after)
            known = [h for h in local if h in positions]
            if any(positions[before] > positions[after] for before, after in zip(known, known[1:])):
                state.warnings.append(f"journal order for {date} differs from the sidecar; "
                                      "the file wins and the next journal move refreshes the sidecar (ADR-072)")
        # All cross-file relations survive; saved relations within a file yield
        # to that file. A cycle now means separated relations cannot reconcile.
        tied = [h for h in saved if _entry_date(h) == date]
        for i, before in enumerate(tied):
            for after in tied[i + 1:]:
                if owners[before] != owners[after]:
                    edge(before, after)
        ready = [(priority[h], h) for h in headings if not incoming[h]]
        heapq.heapify(ready)
        ordered = []
        while ready:
            _, heading = heapq.heappop(ready)
            ordered.append(heading)
            for after in following[heading]:
                incoming[after] -= 1
                if not incoming[after]:
                    heapq.heappush(ready, (priority[after], after))
        if len(ordered) != len(headings):
            state.blocked[date] = f"journal order for {date} has conflicting cross-file ties. {_ORDER_REPAIR}"
        else:
            for heading in ordered:
                state.order[heading] = len(state.order)
    state.warnings.extend(state.blocked.values())
    return state


def _journal_order(snapshot: Snapshot, required: Iterable[str] | None = None) -> dict[str, int]:
    state = _journal_order_state(snapshot)
    dates = {_entry_date(h) for h in required} if required is not None else set(state.blocked)
    for date in dates:
        if date in state.blocked:
            raise ArchiveError(state.blocked[date], "invalid-journal-order")
    return state.order


def _journal_order_text(snapshot: Snapshot, order: dict[str, int]) -> str:
    state = _journal_order_state(snapshot)
    # An unrelated date can move while a broken tie stays. Preserve whatever
    # history that tie still has, without inventing its missing headings.
    headings = [*order, *(h for h in state.saved if _entry_date(h) in state.blocked)]
    headings.sort(key=_entry_date)
    original = (snapshot.text(_JOURNAL_ORDER) if snapshot.is_file(_JOURNAL_ORDER)
                else snapshot.text("JOURNAL.md") if snapshot.is_file("JOURNAL.md") else "")
    newline = "\r\n" if "\r\n" in original else "\n"
    return (json.dumps({"version": 1, "headings": headings}, ensure_ascii=False, indent=2)
            + "\n").replace("\n", newline)


class _Unplaceable(Exception):
    """Placing a section would alter authored bytes."""


def _insert_entry(text: str, heading: str, section: str, newline: str, order: dict[str, int]) -> str:
    """Insert verbatim by date and merged ties (ADR-072).

    Backdated files may return in date order; only placement changes, never
    section bytes. Observed same-date order wins over saved metadata.
    """
    existing = _journal_sections(text)
    if not existing:
        return text.rstrip("\r\n") + newline * 2 + section
    key = lambda h: (_entry_date(h), order.get(h, -1))
    earlier = [end for h, _, end in existing if key(h) < key(heading)]
    point = earlier[-1] if earlier else existing[0][1]
    if not text[:point].endswith("\n"):
        raise _Unplaceable("the archived entry before it has no final line ending")
    if not section.endswith("\n") and point < len(text):
        raise _Unplaceable("its last line has no line ending, so it can only be the last entry of an archive")
    return text[:point] + section + text[point:]


def _settle(entries: list[list], snapshot: Snapshot, newline: str) -> None:
    """Keep back entries whose placement would alter authored bytes."""
    state = _journal_order_state(snapshot)
    order = state.order
    placed: dict[str, str] = {}
    for entry in entries:
        if entry[3] is not None:
            continue
        if _entry_date(entry[0]) in state.blocked:
            entry[3] = state.blocked[_entry_date(entry[0])]
            continue
        name = entry[2]
        if name not in placed:
            placed[name] = (snapshot.text(name) if snapshot.is_file(name)
                            else f"# Journal{newline}{newline}")
        try:
            placed[name] = _insert_entry(placed[name], entry[0], entry[1], newline, order)
        except _Unplaceable as problem:
            entry[3] = str(problem)


def _journal_entries(snapshot: Snapshot) -> list[tuple[str, str]]:
    """Every journal section in the working file and every archive, to compare around a move."""
    found = []
    for text in _journal_files(snapshot).values():
        for heading, start, end in _journal_sections(text):
            found.append((heading, text[start:end]))
    return sorted(found)


def _locations(project: Project) -> dict[str, tuple[str, str]]:
    """Every archivable record id (and a contract's criteria) -> (file, owning id)."""
    found: dict[str, tuple[str, str]] = {}
    for group in (project.tasks, project.contracts.values(), project.debts, project.events,
                  project.assumptions):
        for item in group:
            found[item.id] = (item.source.file, item.id)
    for contract in project.contracts.values():
        for criterion in contract.criteria:
            found.setdefault(criterion.id, (contract.source.file, contract.id))
    return found


RECORD_RULES: tuple[RecordRule, ...] = (
    TaskRule(), ContractRule(), AssumptionRule(), DebtRule(), TraceRule(), JournalRule(),
)


def _why_kept(project: Project, task_id: str, unresolved: set[str], waiting: set[str]) -> str | None:
    for a in project.assumptions:
        if task_id in a.tasks or task_id in a.references:
            if a.open:
                return f"assumption {a.id} is OPEN"
            if a.id in waiting:
                return f"a response to {a.id} awaits reconciliation"
    for d in project.debts_for(task_id):
        if d.status not in DEBT_CLOSED:
            return f"debt {d.id} is {d.status}"
    for e in project.events:
        if e.id in unresolved and (e.task == task_id or task_id in e.affects):
            return f"failure {e.id} is unresolved"
    return None


def archive_mode(root: Path, snapshot: Snapshot | None = None, destinations: Iterable[str] = ()) -> str:
    """Git recovery requires every captured and prospective file to be trackable."""
    names = snapshot.files if snapshot is not None else None
    return archive_recovery.mode(root, names, destinations)


def _inventory(snapshot: Snapshot, destinations: Iterable[str]) -> list[dict[str, str]]:
    entries = {name: {"path": name, "type": "directory"}
               for name in snapshot.directories if name}
    for name, data in snapshot.files.items():
        entries[name] = {"path": name, "type": "file", "sha256": hashlib.sha256(data).hexdigest()}
    for name in destinations:
        name = snapshot.resolve(name)
        entries.setdefault(name, {"path": name, "type": "absent"})
    return [entries[name] for name in sorted(entries)]


def _plan_digest(root: Path, result: Plan) -> str:
    payload = {
        "inventory": _inventory(result.snapshot, result.changed_files),
        "selection": asdict(Selection(result.phase)), "mode": result.mode, "version": result.version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan(project: Project, phase_id: str, root: Path | None = None, *, snapshot: Snapshot | None = None) -> Plan:
    if root is not None and snapshot is None:
        root = root.resolve()
        with _locked(root):
            return _captured_plan(root, phase_id)[1]
    selection = Selection(phase_id)
    selection.check(project)
    result = Plan(phase_id, snapshot=snapshot)
    if snapshot is not None:
        result.warnings = _journal_order_state(snapshot).warnings
    context = EligibilityContext(project, selection, root, result, snapshot)
    for rule in sorted(RECORD_RULES, key=lambda r: getattr(r, "after_records", False)):
        locations = {}
        for candidate in rule.candidates(context):
            if snapshot is not None:
                if candidate.source not in locations:
                    locations[candidate.source] = rule.sections(context.text(candidate.source))
                count = len(locations[candidate.source].get(candidate.id, []))
                if count > 1:
                    candidate = replace(candidate, reason=f"duplicate section heading ({count} occurrences)")
            result.candidates.append(candidate)
            if candidate.moves:
                if candidate.kind == "task":
                    result.tasks.append(candidate.id)
                elif candidate.kind == "contract":
                    result.contracts.append(candidate.id)
                elif candidate.kind == "assumption":
                    result.assumptions.append(candidate.id)
                elif candidate.kind == "debt":
                    result.debts.append(candidate.id)
                elif candidate.kind == "event":
                    result.events.append(candidate.id)
            elif candidate.kind == "task":
                result.kept.append((candidate.id, candidate.reason))
    if root is not None:
        result.writes = _prepare_writes(root, result)
        result.changed_files = sorted(p.relative_to(snapshot.authority).as_posix() for p in result.writes)
        result.created_files = [name for name in result.changed_files if not snapshot.is_file(name)]
        result.mode, result.version = archive_mode(root, snapshot, result.changed_files), layout.version()
        result.digest = _plan_digest(root, result)
    return result


def _prepare_writes(root: Path, result: Plan) -> dict[Path, str]:
    writes: dict[Path, str] = {}
    cuts: dict[str, list[tuple[int, int]]] = {}
    snapshot = result.snapshot
    authority = snapshot.authority
    result.moved_bytes = 0
    journal_moves = [c.id for c in result.candidates if c.kind == "journal" and c.moves]
    journal_order = _journal_order(snapshot, journal_moves) if journal_moves else None
    for rule in RECORD_RULES:
        groups: dict[tuple[str, str], list[str]] = {}
        for candidate in result.candidates:
            if candidate.kind == rule.kind and candidate.moves:
                groups.setdefault((candidate.source, candidate.destination), []).append(candidate.id)
        for (source, destination), ids in groups.items():
            target = authority / snapshot.resolve(destination)
            text = snapshot.text(source)
            locations = rule.sections(text)
            archived_text = writes.get(target)
            if archived_text is None and snapshot.is_file(destination):
                archived_text = snapshot.text(destination)
            phase = PurePosixPath(destination).parent.name
            header = (f"# {rule.title} · {phase} (archived)\n\n"
                      f"Moved verbatim from {source} by `praukron archive` (ADR-067). Still authority.\n")
            if hasattr(rule, "place"):
                for record_id in ids:
                    if len(locations.get(record_id, [])) != 1:
                        raise ArchiveError(f"no unique '## ' section found for {record_id}")
                newline = "\r\n" if "\r\n" in text else "\n"
                base = archived_text if archived_text is not None else header.replace("\n", newline)
                entries = [(i, text[slice(*locations[i][0])]) for i in ids]
                new_archive = rule.place(base, entries, newline, journal_order)
                size = sum(len(section.encode()) for _, section in entries)
            else:
                _, new_archive, size = _move(text, archived_text, ids, None, header, locations=locations)
            cuts.setdefault(source, []).extend(locations[i][0] for i in ids)
            result.moved_bytes += size
            writes[target] = new_archive
    for source, spans in cuts.items():
        working = authority / snapshot.resolve(source)
        if working in writes:
            raise ArchiveError(f"a destination is also a working source: {source}", "invalid-target")
        writes[working] = _cut(snapshot.text(source), spans)[0]
    if journal_order is not None:
        writes[authority / snapshot.resolve(_JOURNAL_ORDER)] = _journal_order_text(snapshot, journal_order)
    return writes


@contextmanager
def _locked(root: Path):
    compiled = root / layout.COMPILED_DIR
    existed = compiled.exists()
    try:
        lock = _lock(root)
    except RespondError as busy:
        raise ArchiveError(str(busy), "lock-busy") from busy
    try:
        yield
    finally:
        lock.release()
        if not existed:
            try:
                compiled.rmdir()
            except OSError:
                pass


def _captured_plan(root: Path, phase_id: str) -> tuple[Project, Plan]:
    snapshot = Snapshot.capture(root / AUTHORITY_DIR)
    project = compiler.load(root, authority=snapshot.path())
    return project, plan(project, phase_id, root, snapshot=snapshot)


def preview(root: Path, phase_id: str) -> Plan:
    """Capture under the lock; leave no recovery record, manifest or projections."""
    root = root.resolve()
    try:
        with _locked(root):
            project, result = _captured_plan(root, phase_id)
            _check_valid(project, result.snapshot)
            return result
    except (ParseError, OSError, UnicodeError) as error:
        raise ArchiveError(f"cannot plan archive: {error}", "plan-failed") from error


def _check_valid(project: Project, snapshot: Snapshot | None = None) -> None:
    errors = validate.errors(validate.check(project, snapshot=snapshot))
    if errors:
        raise ArchiveError(f"validate reports {len(errors)} errors; fix them before archiving",
                           "validation-failed")


def _cut(working: str, spans: list[tuple[int, int]]) -> tuple[str, list[str]]:
    kept, moved, last = [], [], 0
    for start, end in sorted(spans):
        if start < last:
            raise ArchiveError("moving sections overlap", "overlapping-sections")
        kept.append(working[last:start])
        moved.append(working[start:end].rstrip("\r\n"))
        last = end
    kept.append(working[last:])
    return "".join(kept), moved


def _move(working: str, archived: str | None, ids: list[str], pattern, header: str, *,
          locations: dict[str, list[tuple[int, int]]] | None = None) -> tuple[str, str, int]:
    """Cut uniquely located sections and append them to one destination."""
    locations = section_locations(working, pattern) if locations is None else locations
    for record_id in ids:
        if len(locations.get(record_id, [])) != 1:
            raise ArchiveError(f"no unique '## ' section found for {record_id}")
    kept, moved = _cut(working, [locations[i][0] for i in ids])
    newline = "\r\n" if "\r\n" in working else "\n"
    base = archived if archived is not None else header.replace("\n", newline)
    sections = (newline * 2).join(moved)
    archive_text = base.rstrip("\r\n") + newline * 2 + sections + newline
    return kept, archive_text, sum(len(m.encode()) for m in moved)


def _records(project: Project) -> dict[str, object]:
    """All archived record types as authored, without where they live."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "source"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return {
        "tasks": {t.id: strip(asdict(t)) for t in project.tasks},
        "contracts": {c.id: strip(asdict(c)) for c in project.contracts.values()},
        "assumptions": {a.id: strip(asdict(a)) for a in project.assumptions},
        "debts": {d.id: strip(asdict(d)) for d in project.debts},
        "events": {e.id: strip(asdict(e)) for e in project.events},
    }


def _execute(root: Path, project: Project, result: Plan, writes: dict[Path, bytes | None]) -> None:
    """Archive and restore share the same byte-exact, write-ahead transaction."""
    if result.mode == "git":
        archive_recovery.require_clean(root)
    if not writes:
        return
    authority = result.snapshot.authority
    originals = {path: result.snapshot.original(path.relative_to(authority).as_posix()) for path in writes}
    created = set()
    for path in writes:
        directory = path.parent
        while directory != authority:
            name = directory.relative_to(authority).as_posix()
            if result.snapshot.is_dir(name):
                break
            created.add(directory)
            directory = directory.parent
    created = sorted(created, key=lambda p: (len(p.parts), p.as_posix()))
    if result.mode == "git":
        result.recovery = archive_recovery.instructions(
            root, originals, result.phase, result.operation, directories=created, snapshot=result.snapshot)
    else:
        result.recovery = archive_recovery.backup(root, originals)
    before, journal_before = _records(project), _journal_entries(result.snapshot)
    order_path = authority / result.snapshot.resolve(_JOURNAL_ORDER)
    journal_moves = [c.id for c in result.candidates if c.kind == "journal" and c.moves]
    order_before = _journal_order(result.snapshot, journal_moves) if order_path in writes else None
    recovery = _write_recovery(root, originals, directories=created)
    try:
        for directory in created:
            directory.mkdir(exist_ok=True)
        with staged_replacements(root, recovery):
            for path, data in writes.items():
                if data is None:
                    path.unlink()
                else:
                    _replace(path, data)
        after = compiler.load(root)
        if _records(after) != before:
            raise ArchiveError("the records read back differently after the move")
        read_back = Snapshot.capture(result.snapshot.authority)
        if _journal_entries(read_back) != journal_before:
            raise ArchiveError("the journal entries read back differently after the move")
        if order_before is not None and (not read_back.is_file(_JOURNAL_ORDER)
                                         or _journal_order(read_back, journal_moves) != order_before):
            raise ArchiveError("the journal order read back differently after the move")
        introduced = [finding for finding in validate.errors(validate.check(after, snapshot=read_back))
                      if finding.code != "interrupted-archive"]
        if introduced:
            raise ArchiveError("the move introduced validation errors: "
                               + "; ".join(f"{f.code}: {f.message}" for f in introduced))
        recovery.unlink()
    except Exception as failure:
        recover_archive(root)
        if isinstance(failure, ArchiveError):
            raise ArchiveError(f"nothing was moved: {failure}") from failure
        raise


@dataclass(frozen=True)
class _RestoreLocator:
    filename: str
    kind: str
    pattern: re.Pattern[str] | None = None

    def sections(self, text: str) -> dict[str, list[tuple[int, int]]]:
        return section_locations(text, self.pattern)


def _restore_rules() -> list:
    # Lifecycle records use the production rules; journal history only needs
    # a full-heading locator until its eligibility rule is installed.
    rules = {rule.filename: rule for rule in (_RestoreLocator("JOURNAL.md", "journal"), *RECORD_RULES)}
    return list(rules.values())


def _restore_writes(root: Path, phase_id: str, snapshot: Snapshot) -> tuple[Plan, dict[Path, bytes | None]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]*", phase_id) or ".." in phase_id:
        raise ArchiveError("invalid archive phase", "invalid-selection")
    authority = snapshot.authority
    directory = f"{ARCHIVE_DIR}/{phase_id}"
    if not snapshot.is_dir(directory):
        raise ArchiveError(f"no archive for {phase_id}", "unknown-archive")
    rules = {snapshot.resolve(f"{directory}/{rule.filename}"): rule for rule in _restore_rules()}
    result = Plan(phase_id, snapshot=snapshot, version=layout.version(), operation="restore")
    order_state = _journal_order_state(snapshot)
    result.warnings = order_state.warnings
    writes = {}
    for captured in snapshot.path(directory).iterdir():
        source = authority / captured.key
        rule = rules.get(captured.key)
        if rule is None or not captured.is_file():
            raise ArchiveError(f"unsupported archive path: {source.name}", "invalid-archive")
        target_name = snapshot.resolve(rule.filename)
        target = authority / target_name
        archived_bytes = captured.read_bytes()
        archived = archived_bytes.decode("utf-8")
        working = snapshot.original(rule.filename) or b""
        working_ids = set(rule.sections(working.decode("utf-8")))
        restored = []
        for record_id, spans in rule.sections(archived).items():
            if (len(spans) != 1 or record_id in working_ids
                    or rule.kind == "journal" and order_state.counts.get(record_id, 0) != 1):
                raise ArchiveError(f"record already exists or has duplicate sections: {record_id}", "id-collision")
            working_ids.add(record_id)
            if rule.kind == "journal" and _entry_date(record_id) in order_state.blocked:
                result.candidates.append(Candidate(record_id, rule.kind, captured.key, target_name,
                                                   order_state.blocked[_entry_date(record_id)]))
                continue
            start, end = spans[0]
            data = archived[start:end].encode("utf-8")
            restored.append((start, data, record_id))
            result.moved_bytes += len(data)
            result.candidates.append(Candidate(record_id, rule.kind, captured.key, target_name))
            if rule.kind == "task":
                result.tasks.append(record_id)
            elif rule.kind == "contract":
                result.contracts.append(record_id)
            elif rule.kind == "assumption":
                result.assumptions.append(record_id)
            elif rule.kind == "debt":
                result.debts.append(record_id)
            elif rule.kind == "event":
                result.events.append(record_id)
        if restored and hasattr(rule, "restore"):
            newline = "\r\n" if b"\r\n" in (working or archived_bytes) else "\n"
            entries = [(heading, data.decode("utf-8")) for _, data, heading in sorted(restored)]
            order = _journal_order(snapshot, [heading for heading, _ in entries])
            writes[target] = rule.restore(working.decode("utf-8"), entries, newline, order).encode("utf-8")
            spans = [rule.sections(archived)[heading][0] for heading, _ in entries]
            kept = _cut(archived, spans)[0]
            writes[source] = kept.encode("utf-8") if rule.sections(kept) else None
            writes[authority / snapshot.resolve(_JOURNAL_ORDER)] = _journal_order_text(snapshot, order).encode("utf-8")
        elif restored:
            newline = b"\r\n" if b"\r\n" in (working or archived_bytes) else b"\n"
            combined = working
            for _, data, _ in sorted(restored):
                if combined and not combined.endswith(newline * 2):
                    combined += newline if combined.endswith(newline) else newline * 2
                combined += data.replace(b"\r\n", b"\n").replace(b"\n", newline)
            writes[target] = combined
            writes[source] = None
    if not writes and not result.candidates:
        raise ArchiveError(f"no records in archive for {phase_id}", "unknown-archive")
    result.changed_files = sorted(p.relative_to(authority).as_posix() for p in writes)
    result.created_files = [name for name in result.changed_files if not snapshot.is_file(name)]
    result.mode = archive_mode(root, snapshot, result.changed_files)
    return result, writes


def restore(root: Path, phase_id: str, dry_run: bool = False) -> Plan:
    """Owner-only return of archived records, without rechecking eligibility."""
    root = root.resolve()
    with _locked(root):
        if recover_archive(root):
            raise ArchiveError("an interrupted archive was rolled back; try again", "recovered-write")
        snapshot = Snapshot.capture(root / AUTHORITY_DIR)
        result, writes = _restore_writes(root, phase_id, snapshot)
        project = compiler.load(root, authority=snapshot.path())
        # A returned record may repair a reference the current loader cannot
        # resolve yet. Validate the proposed state before backing up or writing.
        files = dict(snapshot.files)
        for path, data in writes.items():
            name = path.relative_to(snapshot.authority).as_posix()
            if data is None:
                files.pop(name, None)
            else:
                files[name] = data
        proposed = replace(snapshot, files=MappingProxyType(files))
        _check_valid(compiler.load(root, authority=proposed.path()), proposed)
        if not dry_run:
            _execute(root, project, result, writes)
        return result


def archive(root: Path, phase_id: str, dry_run: bool = False, *, digest: str | None = None) -> Plan:
    root = root.resolve()
    with _locked(root):
        if recover_archive(root):
            raise ArchiveError("an interrupted archive was rolled back; try again", "recovered-write")
        try:
            project, result = _captured_plan(root, phase_id)
        except (ArchiveError, ParseError, OSError, UnicodeError) as error:
            if digest is not None:
                raise ArchiveError("plan no longer matches the chronicle; run archive plan again",
                                   "stale-plan") from error
            raise
        if digest is not None and digest != result.digest:
            raise ArchiveError("plan no longer matches the chronicle; run archive plan again", "stale-plan")
        _check_valid(project, result.snapshot)
        if not dry_run:
            _execute(root, project, result, {p: text.encode("utf-8") for p, text in result.writes.items()})
        return result

__all__ = ["ArchiveError", "Candidate", "EligibilityContext", "Plan", "RecordRule",
           "RECORD_RULES", "IdRule", "Selection", "Snapshot", "latest_phase", "section_locations",
           "archive", "archive_mode", "plan", "preview", "restore"]
