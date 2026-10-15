"""Create acceptance contracts for work that has not frozen (ADR-068)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import mutate
from .layout import AUTHORITY_DIR
from .model import EVIDENCE_CLASSES, Project

CRITERION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "minLength": 1,
                 "description": "Given/When/Then, one line with single spaces"},
        "class": {"type": "string", "enum": list(EVIDENCE_CLASSES)},
    },
    "required": ["text", "class"],
}
DEFINITION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": r"^AC-T-[A-Za-z0-9.\-]+$"},
        "title": {"type": "string", "minLength": 1},
        "inherits": {"type": "array", "items": {"type": "string"}},
        "criteria": {"type": "array", "minItems": 1, "items": CRITERION_SCHEMA},
    },
    "required": ["id", "title", "criteria"],
}
SCHEMA = {
    **DEFINITION_SCHEMA,
    "properties": {**DEFINITION_SCHEMA["properties"], "task": {"type": "string"}},
    "required": [*DEFINITION_SCHEMA["required"], "task"],
}
EXAMPLE = {
    "id": "AC-T-NEW", "task": "T-NEW", "title": "New work",
    "criteria": [{"text": "Given a request, When it runs, Then it succeeds.", "class": "TEST"}],
}


def single_line(value: str, field: str) -> None:
    if not value.strip() or value != value.strip() or len(value.splitlines()) != 1:
        raise mutate.WriteError("invalid-value", f"{field} must be non-empty, trimmed and one line", field)


def identifier(value: str, prefix: str, field: str) -> None:
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9.\-]+", value):
        raise mutate.WriteError("invalid-id", f"{field} must be a {prefix} identifier", field)


def string_list(values: list, field: str, prefix: str | None = None) -> None:
    for value in values:
        if not isinstance(value, str):
            raise mutate.WriteError("wrong-type", f"{field} items must be strings", field)
        single_line(value, field)
        if prefix:
            identifier(value, prefix, field)
        elif "," in value or value.lower() in ("none", "—", "-"):
            raise mutate.WriteError("invalid-value", f"{field} items must survive comma-separated storage", field)


def validate_definition(payload: dict) -> None:
    mutate.validate_payload(payload, DEFINITION_SCHEMA)
    identifier(payload["id"], "AC-T-", "id")
    single_line(payload["title"], "title")
    string_list(payload.get("inherits", []), "inherits", "AC-")
    if not payload["criteria"]:
        raise mutate.WriteError("invalid-value", "criteria must contain at least one criterion", "criteria")
    for item in payload["criteria"]:
        mutate.validate_payload(item, CRITERION_SCHEMA)
        text = item["text"]
        single_line(text, "text")
        if (text != " ".join(text.split())
                or not re.fullmatch(r"Given .+\bWhen .+\bThen .+", text)
                or re.search(r"`(?:" + "|".join(EVIDENCE_CLASSES) + r")`", text)):
            raise mutate.WriteError("invalid-value", "criterion text must round-trip as one Given/When/Then paragraph", "text")


def check_new(project: Project, payload: dict) -> None:
    ids = [payload["id"], *(f"{payload['id']}-{i:02d}" for i in range(1, len(payload["criteria"]) + 1))]
    for record_id in ids:
        if mutate.id_exists(project, record_id):
            raise mutate.WriteError("duplicate-id", f"id already exists: {record_id}", "id")
    for inherited in payload.get("inherits", []):
        if inherited not in project.contracts:
            raise mutate.WriteError("unknown-inheritance", f"no contract named {inherited}", "inherits")


def render(payload: dict) -> str:
    lines = [f"## {payload['id']} — {payload['title']}", ""]
    if payload.get("inherits"):
        lines += ["Inherits: " + ", ".join(f"`{item}`" for item in payload["inherits"]), ""]
    for i, item in enumerate(payload["criteria"], 1):
        lines.append(f"- `{payload['id']}-{i:02d}` — {item['text']} `{item['class']}` · `NOT_RUN`")
    return "\n".join(lines) + "\n"


def append_section(path: Path, heading: str, text: str) -> bytes:
    original = path.read_bytes() if path.is_file() else f"# {heading}\n".encode("utf-8")
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    padding = b"" if original.endswith(newline * 2) else newline if original.endswith(newline) else newline * 2
    return original + padding + text.encode("utf-8").replace(b"\n", newline)


def add(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "contract.add"
    try:
        mutate.validate_payload(payload, SCHEMA)
        identifier(payload["task"], "T-", "task")
        definition = {k: v for k, v in payload.items() if k != "task"}
        validate_definition(definition)

        def prepare(project: Project) -> mutate.Plan:
            task = next((t for t in project.tasks if t.id == payload["task"]), None)
            if task is None:
                raise mutate.WriteError("record-not-found", f"no task named {payload['task']}", "task")
            mutate.ensure_mutable(task.source)
            if task.status in ("WIP", "DONE") or any(
                t.contract == payload["id"] and t.status in ("WIP", "DONE") for t in project.tasks
            ):
                raise mutate.WriteError("frozen-contract", "a WIP or DONE task's contract is frozen", "task")
            if task.contract and task.contract != payload["id"]:
                raise mutate.WriteError("different-contract", f"task already names {task.contract}", "task")
            check_new(project, definition)
            authority = root / AUTHORITY_DIR
            acceptance = authority / "ACCEPTANCE.md"
            changes = {acceptance: append_section(acceptance, "Acceptance", render(definition))}
            if not task.contract:
                path = authority / task.source.file
                original = path.read_bytes()
                found = mutate.working_section(root, task.source)
                newline = b"\r\n" if b"\r\n" in original else b"\n"
                line = f"- AC: {payload['id']}".encode("utf-8")
                empty = re.search(rb"(?m)^- AC:[ \t]*(?=\r?$)", found.data)
                if empty:
                    updated = found.data[:empty.start()] + line + found.data[empty.end():]
                else:
                    fields = list(re.finditer(
                        rb"(?m)^- (?:Status|Module|Phase|Domain|Validation|Dependencies|Owner|Claimed):"
                        rb"[^\r\n]*(?:\r?\n  [^\r\n]+)*(?:\r?\n|$)", found.data))
                    offset = fields[-1].end()
                    prefix = found.data[:offset]
                    updated = prefix + (b"" if prefix.endswith(b"\n") else newline) + line + newline + found.data[offset:]
                start, end = found.span
                changes[path] = original[:start] + updated + original[end:]
            return mutate.Plan(changes, payload["id"], f"ACCEPTANCE.md#{payload['id']}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)


def command(root: Path, args) -> int:
    if args.schema:
        result = {"schema": SCHEMA, "example": EXAMPLE}
    else:
        try:
            result = add(root, mutate.load_payload(args.input, SCHEMA), dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("contract.add", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1
