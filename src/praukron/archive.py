"""Archiving a closed phase: the owner's command, never an agent's (ADR-067).

A COMPLETE phase's DONE tasks and their contracts move, verbatim, from
`TASKS.md` and `ACCEPTANCE.md` into `archive/<phase>/`. Archived records stay
authority: the compiler reads them with the working files, so every command
sees the same project. Only where each record lives changes.

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
from typing import Iterable, Protocol

from . import analytics, compile as compiler, layout, validate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR
from .model import DEBT_CLOSED, Project
from .mutate import RespondError, _lock, _replace, _spans, _write_recovery, recover_archive
from .parse import ParseError
from .snapshot import Snapshot

_TASK = re.compile(r"(T-[A-Za-z0-9.\-]+): ")
_CONTRACT = re.compile(r"(AC-[A-Za-z0-9.\-]+)(?: — |$)")


class ArchiveError(Exception):
    """An archive was refused, or a move failed and was undone."""

    def __init__(self, message: str, code: str = "archive-refused"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Selection:
    phase: str

    def check(self, project: Project) -> None:
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
    kept: list[tuple[str, str]] = field(default_factory=list)  # (task, why it stays)
    moved_bytes: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    digest: str | None = None
    mode: str | None = None
    version: str | None = None
    snapshot: Snapshot | None = field(default=None, repr=False, compare=False)
    writes: dict[Path, str] = field(default_factory=dict, repr=False, compare=False)
    changed_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "phase": self.phase, "tasks": self.tasks, "contracts": self.contracts,
            "kept": [{"task": t, "why": w} for t, w in self.kept], "movedBytes": self.moved_bytes,
            "candidates": [c.as_json() for c in self.candidates],
            "digest": self.digest, "mode": self.mode, "version": self.version,
            "changedFiles": self.changed_files, "createdFiles": self.created_files,
        }

    def render(self, applied: bool) -> str:
        verb = "Moved" if applied else "Would move"
        target = f"{AUTHORITY_DIR}/{ARCHIVE_DIR}/{self.phase}/"
        lines = [f"{verb} {len(self.tasks)} tasks and {len(self.contracts)} contracts "
                 f"({self.moved_bytes:,} bytes) of {self.phase} to {target}"]
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
        for task in project.tasks:
            if task.phase != selection.phase or task.source.file != self.filename:
                continue
            why = "it is not DONE" if not task.done else _why_kept(project, task.id, unresolved, waiting)
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


RECORD_RULES: tuple[RecordRule, ...] = (TaskRule(), ContractRule())


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


def archive_mode(root: Path) -> str:
    """Mode placeholder; lifecycle recovery will replace this with Git detection."""
    return "nogit"


def _inventory(snapshot: Snapshot, destinations: Iterable[str]) -> list[dict[str, str]]:
    entries = {name: {"path": name, "type": "directory"}
               for name in snapshot.directories if name}
    for name, data in snapshot.files.items():
        entries[name] = {"path": name, "type": "file", "sha256": hashlib.sha256(data).hexdigest()}
    for name in destinations:
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
            elif candidate.kind == "task":
                result.kept.append((candidate.id, candidate.reason))
    if root is not None:
        result.writes = _prepare_writes(root, result)
        result.changed_files = sorted(p.relative_to(root / AUTHORITY_DIR).as_posix() for p in result.writes)
        result.created_files = [name for name in result.changed_files if name not in snapshot.files]
        result.mode, result.version = archive_mode(root), layout.version()
        result.digest = _plan_digest(root, result)
    return result


def _prepare_writes(root: Path, result: Plan) -> dict[Path, str]:
    writes: dict[Path, str] = {}
    cuts: dict[str, list[tuple[int, int]]] = {}
    snapshot = result.snapshot
    result.moved_bytes = 0
    for rule in RECORD_RULES:
        groups: dict[tuple[str, str], list[str]] = {}
        for candidate in result.candidates:
            if candidate.kind == rule.kind and candidate.moves:
                groups.setdefault((candidate.source, candidate.destination), []).append(candidate.id)
        for (source, destination), ids in groups.items():
            target = root / AUTHORITY_DIR / destination
            text = snapshot.text(source)
            locations = rule.sections(text)
            archived_text = writes.get(target)
            if archived_text is None and destination in snapshot.files:
                archived_text = snapshot.text(destination)
            phase = PurePosixPath(destination).parent.name
            header = (f"# {rule.title} · {phase} (archived)\n\n"
                      f"Moved verbatim from {source} by `praukron archive` (ADR-067). Still authority.\n")
            _, new_archive, size = _move(text, archived_text, ids, None, header, locations=locations)
            cuts.setdefault(source, []).extend(locations[i][0] for i in ids)
            result.moved_bytes += size
            writes[target] = new_archive
    for source, spans in cuts.items():
        working = root / AUTHORITY_DIR / source
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
    """Tasks and contracts as authored, without where they live."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "source"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return {
        "tasks": {t.id: strip(asdict(t)) for t in project.tasks},
        "contracts": {c.id: strip(asdict(c)) for c in project.contracts.values()},
    }


def archive(root: Path, phase_id: str, dry_run: bool = False, *, digest: str | None = None) -> Plan:
    authority = root / AUTHORITY_DIR
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
        writes = result.writes
        if not writes:
            return result
        if dry_run:
            return result
        before = _records(project)
        originals = {path: result.snapshot.files.get(path.relative_to(authority).as_posix()) for path in writes}
        created = set()
        for path in writes:
            directory = path.parent
            while directory != authority:
                name = directory.relative_to(authority).as_posix()
                if name in result.snapshot.directories:
                    break
                created.add(directory)
                directory = directory.parent
        created = sorted(created, key=lambda p: (len(p.parts), p.as_posix()))
        recovery = _write_recovery(root, originals, directories=created)
        try:
            for directory in created:
                directory.mkdir(exist_ok=True)
            for path, text in writes.items():
                _replace(path, text.encode("utf-8"))
            after = compiler.load(root)
            if _records(after) != before:
                raise ArchiveError("the records read back differently after the move")
            introduced = [finding for finding in validate.errors(validate.check(after))
                          if finding.code != "interrupted-archive"]
            if introduced:
                raise ArchiveError("the move introduced validation errors: "
                                   + "; ".join(f"{f.code}: {f.message}" for f in introduced))
        except Exception as failure:
            recover_archive(root)
            if isinstance(failure, ArchiveError):
                raise ArchiveError(f"nothing was moved: {failure}") from failure
            raise
        recovery.unlink()
        return result


__all__ = ["ArchiveError", "Candidate", "EligibilityContext", "Plan", "RecordRule",
           "RECORD_RULES", "IdRule", "Selection", "Snapshot", "section_locations",
           "archive", "archive_mode", "plan", "preview"]
