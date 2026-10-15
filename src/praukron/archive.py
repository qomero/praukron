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

    def as_json(self) -> dict[str, object]:
        return {
            "phase": self.phase, "tasks": self.tasks, "contracts": self.contracts,
            "assumptions": self.assumptions, "debts": self.debts, "events": self.events,
            "kept": [{"task": t, "why": w} for t, w in self.kept], "movedBytes": self.moved_bytes,
            "candidates": [c.as_json() for c in self.candidates],
            "digest": self.digest, "mode": self.mode, "version": self.version,
            "changedFiles": self.changed_files, "createdFiles": self.created_files,
            "operation": self.operation, "recovery": self.recovery,
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
        if self.digest:
            lines.append(f"Plan: {self.digest} (mode: {self.mode}, version: {self.version})")
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


RECORD_RULES: tuple[RecordRule, ...] = (
    TaskRule(), ContractRule(), AssumptionRule(), DebtRule(), TraceRule(),
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
        "inventory": _inventory(result.snapshot,
                                (c.destination for c in result.candidates if c.moves)),
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
    context = EligibilityContext(project, selection, root, result, snapshot)
    for rule in RECORD_RULES:
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
            _, new_archive, size = _move(text, archived_text, ids, None, header, locations=locations)
            cuts.setdefault(source, []).extend(locations[i][0] for i in ids)
            result.moved_bytes += size
            writes[target] = new_archive
    for source, spans in cuts.items():
        working = authority / snapshot.resolve(source)
        if working in writes:
            raise ArchiveError(f"a destination is also a working source: {source}", "invalid-target")
        writes[working] = _cut(snapshot.text(source), spans)[0]
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
            _check_valid(project)
            return result
    except (ParseError, OSError, UnicodeError) as error:
        raise ArchiveError(f"cannot plan archive: {error}", "plan-failed") from error


def _check_valid(project: Project) -> None:
    errors = validate.errors(validate.check(project))
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
    before = _records(project)
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
        introduced = [finding for finding in validate.errors(validate.check(after))
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
    # The parallel record rules can override these locators without restore
    # duplicating their section format or their apply-only eligibility logic.
    defaults = (TaskRule(), ContractRule(),
                _RestoreLocator("ASSUMPTIONS.md", "assumption", re.compile(r"(A-[A-Za-z0-9.\-]+): ")),
                _RestoreLocator("TECH_DEBT.md", "debt", re.compile(r"(TD-[A-Za-z0-9.\-]+): ")),
                _RestoreLocator("TRACE.md", "event", re.compile(r"(EV-[A-Za-z0-9.\-]+): ")),
                _RestoreLocator("JOURNAL.md", "journal"))
    rules = {rule.filename: rule for rule in (*defaults, *RECORD_RULES)}
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
            if len(spans) != 1 or record_id in working_ids:
                raise ArchiveError(f"record already exists or has duplicate sections: {record_id}", "id-collision")
            working_ids.add(record_id)
            start, end = spans[0]
            data = archived[start:end].encode("utf-8")
            restored.append((start, data))
            result.moved_bytes += len(data)
            result.candidates.append(Candidate(record_id, rule.kind, captured.key, target_name))
            if rule.kind == "task":
                result.tasks.append(record_id)
            elif rule.kind == "contract":
                result.contracts.append(record_id)
        if restored:
            newline = b"\r\n" if b"\r\n" in (working or archived_bytes) else b"\n"
            combined = working
            for _, data in sorted(restored):
                if combined and not combined.endswith(newline * 2):
                    combined += newline if combined.endswith(newline) else newline * 2
                combined += data.replace(b"\r\n", b"\n").replace(b"\n", newline)
            writes[target] = combined
            writes[source] = None
    if not writes:
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
        _check_valid(compiler.load(root, authority=proposed.path()))
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
        _check_valid(project)
        if not dry_run:
            _execute(root, project, result, {p: text.encode("utf-8") for p, text in result.writes.items()})
        return result

__all__ = ["ArchiveError", "Candidate", "EligibilityContext", "Plan", "RecordRule",
           "RECORD_RULES", "IdRule", "Selection", "Snapshot", "latest_phase", "section_locations",
           "archive", "archive_mode", "plan", "preview", "restore"]
