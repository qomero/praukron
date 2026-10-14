"""Archiving a closed phase: the owner's command, never an agent's (ADR-066).

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

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import analytics, compile as compiler, validate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR
from .model import DEBT_CLOSED, Project
from .respond import RespondError, _lock, _replace

_TASK = re.compile(r"(T-[A-Za-z0-9.\-]+): ")
_CONTRACT = re.compile(r"(AC-[A-Za-z0-9.\-]+)(?: — |$)")


class ArchiveError(Exception):
    """An archive was refused, or a move failed and was undone."""


@dataclass
class Plan:
    phase: str
    tasks: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)  # (task, why it stays)
    moved_bytes: int = 0

    def as_json(self) -> dict[str, object]:
        return {
            "phase": self.phase, "tasks": self.tasks, "contracts": self.contracts,
            "kept": [{"task": t, "why": w} for t, w in self.kept], "movedBytes": self.moved_bytes,
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
        return "\n".join(lines) + "\n"


def _spans(text: str, pattern: re.Pattern[str]) -> dict[str, tuple[int, int]]:
    """Each `## ` section whose heading matches, from its heading to the next
    heading of level one or two, as `parse._sections` bounds it."""
    spans: dict[str, tuple[int, int]] = {}
    for heading in re.finditer(r"(?m)^## (.+)$", text):
        found = pattern.match(heading.group(1).strip())
        if not found:
            continue
        following = re.compile(r"(?m)^#{1,2} ").search(text, heading.end())
        spans[found.group(1)] = (heading.start(), following.start() if following else len(text))
    return spans


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


def plan(project: Project, phase_id: str, root: Path | None = None) -> Plan:
    phase = project.phase(phase_id)
    if phase is None:
        raise ArchiveError(f"{phase_id} is not a phase in PHASES.md")
    if phase.status != "COMPLETE":
        raise ArchiveError(f"{phase_id} is {phase.status}; only a COMPLETE phase is archived")
    result = Plan(phase_id)
    unresolved = set(analytics.unresolved_failures(project))
    waiting = {w["assumption"] for w in analytics.awaiting_reconciliation(project)}
    for task in project.tasks:
        if task.phase != phase_id or task.source.file != "TASKS.md":
            continue
        why = "it is not DONE" if not task.done else _why_kept(project, task.id, unresolved, waiting)
        if why:
            result.kept.append((task.id, why))
        else:
            result.tasks.append(task.id)
    moving = set(result.tasks)
    staying = [t for t in project.tasks if t.id not in moving]
    # A contract section that holds an inherited invariant (`### AC-…`) stays,
    # so no invariant is ever filed away with one task's contract.
    nested: set[str] = set()
    if root is not None:
        text = (root / AUTHORITY_DIR / "ACCEPTANCE.md").read_text(encoding="utf-8")
        nested = {i for i, (s, e) in _spans(text, _CONTRACT).items() if re.search(r"(?m)^### AC-", text[s:e])}
    for task_id in result.tasks:
        contract = project.contracts.get(project.task(task_id).contract or "")
        if contract is None or contract.source.file != "ACCEPTANCE.md" or contract.id in result.contracts:
            continue
        # A contract another working task uses, or another contract inherits, stays with them.
        if any(t.contract == contract.id for t in staying):
            continue
        if any(contract.id in c.inherits for c in project.contracts.values() if c.id != contract.id):
            continue
        if contract.id in nested:
            continue
        result.contracts.append(contract.id)
    return result


def _move(working: str, archived: str | None, ids: list[str], pattern, header: str) -> tuple[str, str, int]:
    """Cut the sections out of the working text and append them to the archive."""
    spans = _spans(working, pattern)
    cut = sorted(spans[i] for i in ids if i in spans)
    missing = [i for i in ids if i not in spans]
    if missing:
        raise ArchiveError(f"no '## ' section found for {', '.join(missing)}")
    kept, moved, last = [], [], 0
    for start, end in cut:
        kept.append(working[last:start])
        moved.append(working[start:end].rstrip("\r\n"))
        last = end
    kept.append(working[last:])
    newline = "\r\n" if "\r\n" in working else "\n"
    base = archived if archived is not None else header.replace("\n", newline)
    sections = (newline * 2).join(moved)
    archive_text = base.rstrip("\r\n") + newline * 2 + sections + newline
    return "".join(kept), archive_text, sum(len(m.encode()) for m in moved)


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


def archive(root: Path, phase_id: str, dry_run: bool = False) -> Plan:
    authority = root / AUTHORITY_DIR
    try:
        lock = _lock(root)
    except RespondError as busy:
        raise ArchiveError(str(busy)) from busy
    try:
        project = compiler.load(root)
        errors = validate.errors(validate.check(project))
        if errors:
            raise ArchiveError(f"validate reports {len(errors)} errors; fix them before archiving")
        result = plan(project, phase_id, root)
        if not result.tasks:
            return result
        directory = authority / ARCHIVE_DIR / phase_id
        files = {
            "TASKS.md": (result.tasks, _TASK, f"# Tasks · {phase_id} (archived)\n\n"
                         "Moved verbatim from TASKS.md by `praukron archive` (ADR-066). Still authority.\n"),
            "ACCEPTANCE.md": (result.contracts, _CONTRACT, f"# Acceptance · {phase_id} (archived)\n\n"
                              "Moved verbatim from ACCEPTANCE.md by `praukron archive` (ADR-066). Still authority.\n"),
        }
        originals: dict[Path, bytes | None] = {}
        writes: dict[Path, str] = {}
        for name, (ids, pattern, header) in files.items():
            if not ids:
                continue
            working, target = authority / name, directory / name
            archived_text = target.read_bytes().decode("utf-8") if target.is_file() else None
            new_working, new_archive, size = _move(
                working.read_bytes().decode("utf-8"), archived_text, ids, pattern, header)
            result.moved_bytes += size
            writes[working], writes[target] = new_working, new_archive
        if dry_run:
            return result
        before = _records(project)
        created = [d for d in (directory.parent, directory) if not d.exists()]
        directory.mkdir(parents=True, exist_ok=True)
        try:
            for path, text in writes.items():
                originals[path] = path.read_bytes() if path.is_file() else None
                _replace(path, text.encode("utf-8"))
            after = compiler.load(root)
            if _records(after) != before:
                raise ArchiveError("the records read back differently after the move")
            introduced = validate.errors(validate.check(after))
            if introduced:
                raise ArchiveError("the move introduced validation errors: "
                                   + "; ".join(f"{f.code}: {f.message}" for f in introduced))
        except BaseException as failure:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _replace(path, original)
            for made in reversed(created):
                if made.is_dir() and not any(made.iterdir()):
                    made.rmdir()
            if isinstance(failure, ArchiveError):
                raise ArchiveError(f"nothing was moved: {failure}") from failure
            raise
        return result
    finally:
        lock.unlink(missing_ok=True)


__all__ = ["ArchiveError", "Plan", "archive", "plan"]
