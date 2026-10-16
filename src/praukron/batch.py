"""Compose governed operation planners over one snapshot, then commit once."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Callable

from . import compile as compiler, contract, criterion, journal, mutate, task, task_update
from .layout import AUTHORITY_DIR
from .model import Project, Source
from .snapshot import Snapshot


@dataclass(frozen=True)
class Operation:
    schema: dict
    handler: Callable
    revision_target: Callable | None = None


def _task_target(field: str, key: str):
    def locate(project, payload):
        found = project.task(payload.get(key))
        return (field, found.source) if found else None
    return locate


def _criterion_target(project, payload):
    found = criterion._find(project, payload.get("criterion"))
    return ("expectRevision", found[0].source) if found and not found[0].is_global else None


OPERATIONS = {
    "journal.append": Operation(journal.SCHEMA, journal.append),
    "criterion.set": Operation(criterion.SCHEMA, criterion.set_result, _criterion_target),
    "task.add": Operation(task.SCHEMA, task.add),
    "contract.add": Operation(contract.SCHEMA, contract.add, _task_target("expectTaskRevision", "task")),
    "task.update": Operation(task_update.SCHEMA, task_update.update, _task_target("expectRevision", "id")),
}

ENTRY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"operation": {"type": "string"}, "payload": {"type": "object"}},
    "required": ["operation", "payload"],
}


def schema() -> dict:
    choices = []
    for name, operation in OPERATIONS.items():
        payload = copy.deepcopy(operation.schema)
        for field in ("expectRevision", "expectTaskRevision"):
            if field in payload["properties"]:
                payload["properties"][field] = {
                    "type": "string", "pattern": r"^(?:[0-9a-f]{16}|\$previous)$",
                    "description": "Original or staged section revision; $previous requires an earlier write to this section in this batch",
                }
        choices.append({**ENTRY_SCHEMA, "properties": {
            "operation": {"type": "string", "enum": [name]}, "payload": payload}})
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"operations": {"type": "array", "minItems": 1, "items": {"oneOf": choices}}},
        "required": ["operations"],
        "description": "\n".join(mutate.GUIDANCE["batch.apply"]),
    }


EXAMPLE = {"operations": [
    {"operation": "task.update", "payload": {"id": "T-EXAMPLE-01", "expectRevision": "0123456789abcdef",
                                             "set": {"status": "WIP", "owner": "worker"}}},
    {"operation": "journal.append", "payload": {"title": "Claimed work", "body": "Started T-EXAMPLE-01."}},
]}


def _sections(data: bytes) -> dict[str, str]:
    return {heading: mutate.section(data, heading).revision
            for match in re.finditer(rb"(?m)^## ([^\r\n]+)\r?$", data)
            for heading in [match.group(1).decode("utf-8").strip()]}


def _anchor(heading: str) -> str:
    return re.split(r": | — ", heading, maxsplit=1)[0] if re.match(r"[A-Z][A-Za-z0-9]*-", heading) else heading


class Overlay:
    def __init__(self, root: Path, snapshot: Snapshot):
        self.root, self.snapshot = root, snapshot
        self.authority = (root / AUTHORITY_DIR).resolve()
        self.files = dict(snapshot.files)
        self.touched: set[str] = set()

    def key(self, path: Path) -> str:
        try:
            return self.snapshot.resolve(path.resolve().relative_to(self.authority).as_posix())
        except ValueError as error:
            raise mutate.WriteError("invalid-target", "write target is outside the chronicle") from error

    def read_bytes(self, path: Path) -> bytes:
        key = self.key(path)
        if key not in self.files:
            raise FileNotFoundError(path)
        return self.files[key]

    def is_file(self, path: Path) -> bool:
        return self.key(path) in self.files

    def project(self) -> Project:
        view = replace(self.snapshot, files=MappingProxyType(dict(self.files)))
        return compiler.load(self.root, authority=view.path())

    def transaction(self, root: Path, operation: str, prepare) -> dict:
        try:
            if root != self.root:
                raise mutate.WriteError("invalid-target", "batch operation changed project root")
            plan = prepare(self.project())
            changes = {}
            touched = set()
            for path, data in plan.changes.items():
                key = self.key(path)
                mutate.ensure_mutable(key)
                if key in changes:
                    raise mutate.WriteError("duplicate-target", "multiple changes resolve to the same chronicle file")
                before = _sections(self.files.get(key, b""))
                for heading, revision in _sections(data).items():
                    if before.get(heading) != revision:
                        touched.add(f"{key}#{_anchor(heading)}")
                changes[key] = data
            name, anchor = plan.source.split("#", 1)
            revision = mutate.section(changes[self.snapshot.resolve(name)], anchor).revision
            self.files.update(changes)
            self.touched.update(touched)
            return {"ok": True, "operation": operation, "recordId": plan.record_id,
                    "source": plan.source, "revision": revision, "written": False, "refreshed": False}
        except mutate.WriteError as error:
            return mutate.refusal(operation, error)

    def payload(self, operation: Operation, value: dict) -> dict:
        payload = copy.deepcopy(value)
        target = operation.revision_target(self.project(), payload) if operation.revision_target else None
        if target is None:
            return payload
        field, source = target
        expected = payload.get(field)
        if not isinstance(expected, str):
            return payload
        key = self.snapshot.resolve(source.file)
        pointer = f"{key}#{source.anchor}"
        current = mutate.section(self.files[key], source.anchor).revision
        original = self.snapshot.original(key)
        try:
            initial = mutate.section(original, source.anchor).revision if original is not None else None
        except mutate.WriteError:
            initial = None
        if expected == "$previous":
            if pointer not in self.touched:
                raise mutate.WriteError("invalid-revision", "$previous needs an earlier write to this section", field)
            payload[field] = current
        elif expected == initial or expected == current:
            payload[field] = current
        return payload


def apply(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()

    def prepare(project: Project) -> mutate.Plan:
        mutate.validate_payload(payload, schema())
        if not payload["operations"]:
            raise mutate.WriteError("invalid-value", "operations must contain at least one operation", "operations")
        overlay = Overlay(root, Snapshot.capture(root / AUTHORITY_DIR))
        results = []
        with mutate.planning(overlay):
            for index, entry in enumerate(payload["operations"]):
                name = entry.get("operation") if isinstance(entry, dict) else None
                try:
                    mutate.validate_payload(entry, ENTRY_SCHEMA)
                    if name not in OPERATIONS:
                        raise mutate.WriteError("invalid-value", f"unsupported batch operation: {name}", "operation")
                    operation = OPERATIONS[name]
                    result = operation.handler(root, overlay.payload(operation, entry["payload"]))
                    if not result["ok"]:
                        error = result["error"]
                        raise mutate.WriteError(error["code"], error["message"], error.get("field"),
                                                {k: v for k, v in error.items() if k not in ("code", "message", "field")})
                    results.append({"index": index, **{k: v for k, v in result.items()
                                                      if k not in ("written", "refreshed")}, "operation": name})
                except mutate.WriteError as error:
                    error.details.update(operationIndex=index, operation=name)
                    raise
                except Exception as error:
                    raise mutate.WriteError("write-failed", str(error), details={
                        "operationIndex": index, "operation": name}) from error
            proposed = overlay.project()
        changes = {overlay.authority / name: data for name, data in overlay.files.items()
                   if overlay.snapshot.original(name) != data}
        revisions = {}
        for pointer in sorted(overlay.touched):
            name, anchor = pointer.split("#", 1)
            revisions[pointer] = mutate.section(overlay.files[name], anchor).revision
        last = results[-1]
        return mutate.Plan(changes, last["recordId"], last["source"], results, revisions, proposed)

    return mutate.transaction(root, "batch.apply", prepare, dry_run=dry_run)


def command(root: Path, args) -> int:
    if args.schema:
        result = {"schema": schema(), "example": EXAMPLE}
    else:
        try:
            result = apply(root, mutate.load_payload(args.input, schema()), dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("batch.apply", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1
