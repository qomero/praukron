"""Fingerprint a contract when its task becomes WIP, so validate sees a frozen criterion changed by hand (ADR-078)."""

from __future__ import annotations

import hashlib
import re

from .model import EVIDENCE_CLASSES, Criterion, Finding, Project, Task

FROZEN_STATUSES = ("WIP", "DONE")
_ENTRY = re.compile(r"(AC-[A-Za-z0-9.\-]+) ([0-9a-f]{8})")
_CONTRACT = re.compile(r"(?P<text>.*?)\s*`(?P<cls>" + "|".join(EVIDENCE_CLASSES) + r")`"
                       r"(?:\s*·\s*`(?:PASS|FAIL|NOT_RUN)`)?\s*$")


def digest(criterion_id: str, evidence_class: str, text: str) -> str:
    """Id, class and the parser's normalized text; never state or evidence."""
    return hashlib.sha256(f"{criterion_id}\n{evidence_class}\n{text}".encode("utf-8")).hexdigest()[:8]


def render(criteria: list[Criterion]) -> str:
    return ", ".join(f"{c.id} {digest(c.id, c.evidence_class, c.text)}" for c in criteria) or "none"


def parse(value: str) -> dict[str, str] | None:
    """The authored field as {criterion id: fingerprint}, or None when unreadable."""
    if value.strip().lower() == "none":
        return {}
    entries: dict[str, str] = {}
    for part in value.split(","):
        found = _ENTRY.fullmatch(" ".join(part.split()))
        if not found or found.group(1) in entries:
            return None
        entries[found.group(1)] = found.group(2)
    return entries


def _split(recorded: str | None) -> tuple[str, str | None] | None:
    """`<text> `CLASS`` as an ACR records a contract; the class may be absent."""
    if not recorded:
        return None
    found = _CONTRACT.match(recorded)
    if not found:
        return " ".join(recorded.split()), None
    return " ".join(found.group("text").split()), found.group("cls")


def _accepted_back(project: Project, criterion: Criterion, expected: str) -> bool:
    """Walk accepted requests back from the current text to the frozen one."""
    requests = sorted((r for r in project.change_requests if r.criterion == criterion.id and r.accepted),
                      key=lambda r: r.number, reverse=True)
    text, evidence_class = criterion.text, criterion.evidence_class
    while requests:
        for request in requests:
            proposed, current = _split(request.proposed), _split(request.current)
            if proposed is None or current is None:
                continue
            if (proposed[0], proposed[1] or evidence_class) == (text, evidence_class):
                text, evidence_class = current[0], current[1] or evidence_class
                requests.remove(request)
                if digest(criterion.id, evidence_class, text) == expected:
                    return True
                break
        else:
            return False
    return False


def differences(project: Project, task: Task) -> list[tuple[str, str]]:
    """(code, message) for each way the task's contract left its fingerprint."""
    if task.status not in FROZEN_STATUSES or task.frozen is None:
        return []
    frozen = parse(task.frozen)
    if frozen is None:
        return [("invalid-frozen", "Frozen must list '<criterion id> <8 hex>' entries separated by commas, or none")]
    contract = project.contracts.get(task.contract or "")
    if contract is None:
        return []
    found: list[tuple[str, str]] = []
    current = {c.id: c for c in contract.criteria}
    for criterion_id in frozen:
        if criterion_id not in current:
            found.append(("frozen-criterion-removed",
                          f"{criterion_id} was in {contract.id} when {task.id} became WIP and is gone; "
                          "a frozen contract changes only through an accepted Acceptance Change Request"))
    for criterion in contract.criteria:
        expected = frozen.get(criterion.id)
        if expected is None:
            found.append(("frozen-criterion-added",
                          f"{criterion.id} was added to {contract.id} after {task.id} became WIP; "
                          "a frozen contract changes only through an accepted Acceptance Change Request"))
        elif (digest(criterion.id, criterion.evidence_class, criterion.text) != expected
              and not _accepted_back(project, criterion, expected)):
            found.append(("frozen-criterion-changed",
                          f"{criterion.id} text or class differs from when {task.id} became WIP; "
                          "restore it, or raise an Acceptance Change Request with acr add"))
    return found


def findings(project: Project) -> list[Finding]:
    return [Finding("error", code, message, f"{task.source.file}#{task.id}")
            for task in project.tasks for code, message in differences(project, task)]
