"""Change a task's status, owner, claim or evidence in place (ADR-068)."""

from __future__ import annotations

import datetime as dt
import re
import textwrap
from pathlib import Path

from . import mutate
from .layout import AUTHORITY_DIR
from .model import Project
from .parse import _fields

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1, "description": "Task id, e.g. T-EXAMPLE-01"},
        "expectRevision": {"type": "string", "minLength": 16,
                           "description": "16 hex revision of the task section, from a prior read or refusal"},
        "set": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["TODO", "WIP", "DONE", "BLOCKED"]},
                "owner": {"type": "string", "minLength": 1, "description": "One line"},
                "claimed": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
                "evidence": {"type": "string", "minLength": 1,
                             "description": "Single spaces, no blank line, no leading or trailing em dash; line breaks are wrap points"},
            },
        },
    },
    "required": ["id", "expectRevision", "set"],
}
EXAMPLE = {"id": "T-EXAMPLE-01", "expectRevision": "0123456789abcdef", "set": {"status": "WIP", "owner": "worker"}}

_LABELS = {"status": "Status", "owner": "Owner", "claimed": "Claimed", "evidence": "Evidence"}
# Where an absent field goes: after the nearest earlier field in this order.
_ORDER = ["Status", "Module", "Phase", "Domain", "Authority", "Validation", "Dependencies",
          "Owner", "Claimed", "AC", "Schedule", "Evidence", "Governed by"]
_FIELD = re.compile(r"- ([A-Za-z][A-Za-z ]*): ?(.*)")
_WIDTH = 100


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


def update(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "task.update"
    stale: dict = {}
    try:
        mutate.validate_payload(payload, SCHEMA)
        task_id, expected, changes = payload["id"], payload["expectRevision"], payload["set"]
        if not re.fullmatch(r"[0-9a-f]{16}", expected):
            raise mutate.WriteError("invalid-value", "expectRevision must be 16 lowercase hex characters",
                                    "expectRevision")
        if not changes:
            raise mutate.WriteError("empty-set", "set must change at least one field", "set")

        def prepare(project: Project) -> mutate.Plan:
            task = next((t for t in project.tasks if t.id == task_id), None)
            if task is None:
                raise mutate.WriteError("record-not-found", f"no task {task_id}", "id")
            current = mutate.working_section(root, task.source)
            if current.revision != expected:
                stale.update(currentRevision=current.revision,
                             section=current.data.decode("utf-8").rstrip("\r\n"))
                raise mutate.WriteError(
                    "stale-revision", f"{task_id} changed: expected revision {expected}, current {current.revision}",
                    "expectRevision")
            status = changes.get("status", task.status)
            owner = changes.get("owner", task.owner)
            evidence = changes.get("evidence", task.evidence)
            edits: dict[str, str] = {}
            for key in ("status", "owner", "claimed"):
                if key in changes:
                    edits[_LABELS[key]] = _lines(key, changes[key], True)[0]
            if "evidence" in changes:
                edits["Evidence"] = " ".join(_lines("evidence", changes["evidence"], False))
                if edits["Evidence"].strip("—").strip() != edits["Evidence"]:
                    raise mutate.WriteError(
                        "invalid-value", "evidence may not start or end with an em dash (the parser strips it)",
                        "evidence")
            if status == "WIP" and task.status != "WIP":
                if not _usable(owner):
                    raise mutate.WriteError("owner-required", "WIP needs an owner", "owner")
                edits.setdefault("Claimed", dt.date.today().isoformat())
            if status == "DONE" and task.status != "DONE":
                if task.owner_held:
                    raise mutate.WriteError("owner-held", f"{task_id} is Authority: owner; only the owner closes it",
                                            "status")
                criteria = project.mandatory_criteria(task)
                if not criteria:
                    raise mutate.WriteError("unresolved-acceptance", f"{task_id} has no contract criteria", "status")
                open_ids = [c.id for c in criteria if c.state != "PASS"]
                if open_ids:
                    raise mutate.WriteError("unresolved-acceptance",
                                            f"criteria not PASS: {', '.join(open_ids)}", "status")
                if not (evidence or "").strip():
                    raise mutate.WriteError("evidence-required", "DONE needs recorded evidence", "evidence")
            if all(_current(task, label) == value for label, value in edits.items()):
                raise mutate.WriteError("no-change", "the update would change nothing")
            path = root / AUTHORITY_DIR / task.source.file
            data = path.read_bytes()
            located = mutate.section(data, task.source.anchor)
            start, end = located.span
            return mutate.Plan({path: data[:start] + _edit(located.data, edits) + data[end:]},
                               task_id, f"{task.source.file}#{task.source.anchor}")

        result = mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)
    if stale and not result.get("ok", True) and result.get("error", {}).get("code") == "stale-revision":
        result["error"].update(stale)
    return result


def _current(task, label: str) -> str | None:
    return {"Status": task.status, "Owner": task.owner, "Claimed": task.claimed,
            "Evidence": task.evidence}[label]
