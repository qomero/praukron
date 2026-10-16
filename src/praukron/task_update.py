"""Change a task's status, owner, claim, evidence, validation or governing ADRs in place (ADR-068, amended by ADR-074)."""

from __future__ import annotations

import datetime as dt
import re
import textwrap
from pathlib import Path

from . import contract, mutate
from .layout import AUTHORITY_DIR
from .model import VALIDATIONS, Project
from .parse import _fields

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1, "description": "Task id, e.g. T-EXAMPLE-01", "examples": ["T-EXAMPLE-01"]},
        "expectRevision": {"type": "string", "minLength": 16,
                           "description": "16 hex revision of the task section, from the last result or a stale-revision refusal",
                           "examples": ["0123456789abcdef"]},
        "set": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["TODO", "WIP", "DONE", "BLOCKED"], "examples": ["WIP"]},
                "owner": {"type": "string", "minLength": 1, "description": "One line", "examples": ["worker"]},
                "claimed": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
                "evidence": {"type": "string", "minLength": 1,
                             "description": "Single spaces, no blank line, no leading or trailing em dash; caller line breaks become spaces; the text is re-wrapped",
                             "examples": ["unit tests pass"]},
                "validation": {"type": "string", "enum": [v for v in VALIDATIONS if v != "HUMAN_VERIFIED"],
                               "description": "Validation strength; HUMAN_VERIFIED is never set by a command"},
            },
        },
        "addGovernedBy": {"type": "array", "items": {"type": "string"},
                          "description": "Existing ADR ids to append to Governed by, in order; ids already listed are skipped and reported in alreadyGoverned; entries are never removed or reordered"},
    },
    "required": ["id", "expectRevision"],
}
EXAMPLE = {"id": "T-EXAMPLE-01", "expectRevision": "0123456789abcdef", "set": {"status": "WIP", "owner": "worker"}}

_LABELS = {"status": "Status", "owner": "Owner", "claimed": "Claimed", "evidence": "Evidence",
           "validation": "Validation"}
# Where an absent field goes: after the nearest earlier field in this order.
_ORDER = ["Status", "Module", "Phase", "Domain", "Authority", "Validation", "Dependencies",
          "Owner", "Claimed", "AC", "Schedule", "Evidence", "Governed by"]
_FIELD = re.compile(r"- ([A-Za-z][A-Za-z ]*): ?(.*)")
_WIDTH = 100


SCHEMA["description"] = "\n".join(mutate.GUIDANCE["task.update"])
mutate.SCHEMAS["task.update"] = (SCHEMA, ("set.status", "set.owner"))


def _lines(name: str, value: str, single: bool) -> list[str]:
    """Normalize a value so the parser reads back exactly what was given."""
    parts = value.splitlines()
    if single and len(parts) != 1:
        raise mutate.WriteError("invalid-value", f"{name} must be one line", name)
    for part in parts:
        if not part.strip():
            raise mutate.WriteError("invalid-value", f"{name} may not contain a blank line", name)
        if part != part.strip() or "  " in part or re.search(r"[^\S ]", part):
            raise mutate.WriteError(
                "invalid-value", f"{name} must use single spaces and no leading or trailing whitespace", name)
    if not parts:
        raise mutate.WriteError("invalid-value", f"{name} may not be empty", name)
    return parts


def _render(label: str, text: str) -> list[str]:
    return textwrap.wrap(text, width=_WIDTH, initial_indent=f"- {label}: ", subsequent_indent="  ",
                         break_long_words=False, break_on_hyphens=False)


def _edit(section: bytes, changes: dict[str, str]) -> bytes:
    """Rewrite or insert only the named field lines of one task section."""
    text = section.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    if re.search(r"\r|\n", text.replace(newline, "")):
        raise mutate.WriteError("mixed-newlines", "the task section mixes line endings")
    lines = text.split(newline)
    for label, value in changes.items():
        starts = [i for i, line in enumerate(lines)
                  if (m := _FIELD.match(line)) and m.group(1).strip() == label]
        if len(starts) > 1:
            raise mutate.WriteError("duplicate-field", f"{label} appears more than once", label.lower())
        block = _render(label, value)
        if starts:
            start = starts[0]
            end = start + 1
            while end < len(lines) and lines[end].startswith("  ") and lines[end].strip():
                end += 1
            lines[start:end] = block
            continue
        at = 1
        for earlier in reversed(_ORDER[:_ORDER.index(label)]):
            found = [i for i, line in enumerate(lines)
                     if (m := _FIELD.match(line)) and m.group(1).strip() == earlier]
            if found:
                at = found[0] + 1
                while at < len(lines) and lines[at].startswith("  ") and lines[at].strip():
                    at += 1
                break
        lines[at:at] = block
    updated = newline.join(lines)
    fields = _fields(updated)
    for label, value in changes.items():
        if fields.get(label) != value:
            raise mutate.WriteError("invalid-value", f"{label} would not read back as given", label.lower())
    return updated.encode("utf-8")


def _usable(owner: str | None) -> bool:
    return bool(owner) and owner.strip().lower() not in {"unassigned", "none", "—", "-"}


def done_blockers(project: Project, task, evidence: str | None) -> list[mutate.WriteError]:
    """The shared rule for setting DONE, evaluated against the proposed evidence."""
    try:
        mutate.ensure_mutable(task.source)
    except mutate.WriteError as error:
        return [error]
    if task.done:
        return []
    blockers = []
    if task.owner_held:
        blockers.append(mutate.WriteError(
            "owner-held", f"{task.id} is Authority: owner; only the owner closes it; record evidence and leave Status for the owner", "status"))
    criteria = project.mandatory_criteria(task)
    if not criteria:
        blockers.append(mutate.WriteError(
            "unresolved-acceptance", f"{task.id} has no contract criteria; add a contract with contract add while the task is TODO", "status"))
    open_ids = [c.id for c in criteria if c.state != "PASS"]
    if open_ids:
        blockers.append(mutate.WriteError(
            "unresolved-acceptance", f"criteria not PASS: {', '.join(open_ids)}; record each with criterion set first",
            "status", details={"criteria": open_ids}))
    if not (evidence or "").strip():
        blockers.append(mutate.WriteError(
            "evidence-required", "DONE needs recorded evidence; include set.evidence", "evidence"))
    return blockers


def closability(project: Project, task) -> dict:
    blockers = done_blockers(project, task, task.evidence)
    if task.done and not blockers:
        return {"canClose": False, "reasons": [{"code": "already-done"}]}
    return {"canClose": not blockers, "reasons": [
        {"code": b.code, **b.details} for b in blockers]}


def update(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "task.update"
    stale: dict = {}
    skipped: list[str] = []
    try:
        mutate.validate_payload(payload, SCHEMA)
        task_id, expected, changes = payload["id"], payload["expectRevision"], payload.get("set", {})
        added = payload.get("addGovernedBy", [])
        contract.string_list(added, "addGovernedBy", "ADR-")
        if not re.fullmatch(r"[0-9a-f]{16}", expected):
            raise mutate.WriteError("invalid-value", "expectRevision must be 16 lowercase hex characters",
                                    "expectRevision")
        if not changes and not added:
            raise mutate.WriteError("empty-set", "set or addGovernedBy must change at least one field",
                                    "addGovernedBy" if "addGovernedBy" in payload and "set" not in payload else "set")

        def prepare(project: Project) -> mutate.Plan:
            task = next((t for t in project.tasks if t.id == task_id), None)
            if task is None:
                raise mutate.WriteError("record-not-found", f"no task {task_id}", "id")
            current = mutate.working_section(root, task.source)
            if current.revision != expected:
                stale.update(currentRevision=current.revision,
                             section=current.data.decode("utf-8").rstrip("\r\n"))
                raise mutate.WriteError(
                    "stale-revision", f"{task_id} changed: expected revision {expected}, current {current.revision}; retry with currentRevision",
                    "expectRevision")
            status = changes.get("status", task.status)
            owner = changes.get("owner", task.owner)
            evidence = changes.get("evidence", task.evidence)
            edits: dict[str, str] = {}
            for key in ("status", "owner", "claimed", "validation"):
                if key in changes:
                    edits[_LABELS[key]] = _lines(key, changes[key], True)[0]
            if "evidence" in changes:
                edits["Evidence"] = " ".join(_lines("evidence", changes["evidence"], False))
                if edits["Evidence"].strip("—").strip() != edits["Evidence"]:
                    raise mutate.WriteError(
                        "invalid-value", "evidence may not start or end with an em dash (the parser strips it)",
                        "evidence")
            if added:
                known = {d.id for d in project.decisions}
                for decision in added:
                    if decision not in known:
                        raise mutate.WriteError("unknown-decision", f"no ADR named {decision}", "addGovernedBy")
                fresh = list(dict.fromkeys(d for d in added if d not in task.decisions))
                skipped[:] = [d for d in dict.fromkeys(added) if d in task.decisions]
                if fresh:
                    edits["Governed by"] = ", ".join([*task.decisions, *fresh])
            if status == "WIP" and task.status != "WIP":
                if not _usable(owner):
                    raise mutate.WriteError("owner-required", "WIP needs an owner; include set.owner in the same update", "owner")
                edits.setdefault("Claimed", dt.date.today().isoformat())
            if status == "DONE":
                blockers = done_blockers(project, task, evidence)
                if blockers:
                    raise blockers[0]
            if all(_current(task, label) == value for label, value in edits.items()):
                raise mutate.WriteError("no-change", "the update would change nothing",
                                        details={"alreadyGoverned": list(skipped)} if skipped else None)
            path = root / AUTHORITY_DIR / task.source.file
            data = mutate.read_bytes(path)
            located = mutate.section(data, task.source.anchor)
            start, end = located.span
            return mutate.Plan({path: data[:start] + _edit(located.data, edits) + data[end:]},
                               task_id, f"{task.source.file}#{task.source.anchor}")

        result = mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)
    if skipped and result.get("ok"):
        result["alreadyGoverned"] = list(skipped)
    if stale and not result.get("ok", True) and result.get("error", {}).get("code") == "stale-revision":
        result["error"].update(stale)
    return result


def _current(task, label: str) -> str | None:
    return {"Status": task.status, "Owner": task.owner, "Claimed": task.claimed,
            "Evidence": task.evidence, "Validation": task.validation,
            "Governed by": ", ".join(task.decisions)}[label]
