"""Compose governed operation planners over one snapshot, then commit once."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Callable

from . import acr, compile as compiler, contract, criterion, journal, mutate, task, task_update
from .layout import AUTHORITY_DIR
from .model import Project
from .snapshot import Snapshot


@dataclass(frozen=True)
class Operation:
    schema: dict
    handler: Callable
    revision_target: Callable | None = None


def _task_target(field: str, key: str):
    def locate(project, payload):
        found = project.task(payload.get(key)) if isinstance(payload.get(key), str) else None
        return (field, found.source) if found else None
    return locate


def _criterion_target(project, payload):
    found = criterion._find(project, payload.get("criterion"))
    return ("expectRevision", found[0].source) if found and not found[0].is_global else None


# Keyed by the command name, so an entry is that command's payload plus "op".
OPERATIONS = {
    "journal append": Operation(journal.SCHEMA, journal.append),
    "criterion set": Operation(criterion.SCHEMA, criterion.set_result, _criterion_target),
    "task add": Operation(task.SCHEMA, task.add),
    "contract add": Operation(contract.SCHEMA, contract.add, _task_target("expectTaskRevision", "task")),
    "task update": Operation(task_update.SCHEMA, task_update.update, _task_target("expectRevision", "id")),
    "acr add": Operation(acr.SCHEMA, acr.add),
}
PREVIOUS = "$previous"
# Details of a staged section, which a caller retrying the whole batch cannot use.
STAGED_DETAILS = ("section", "record", "currentCriterion")


def schema() -> dict:
    choices = []
    for name, operation in OPERATIONS.items():
        payload = copy.deepcopy(operation.schema)
        payload.pop("description", None)
        for field in ("expectRevision", "expectTaskRevision"):
            if field in payload["properties"]:
                payload["properties"][field] = {
                    "type": "string", "pattern": r"^(?:[0-9a-f]{16}|\$previous)$",
                    "description": "The revision read before the batch, reused by every operation on that "
                                   "section; $previous only for a section an earlier operation created",
                }
        payload["properties"] = {"op": {"type": "string", "enum": [name]}, **payload["properties"]}
        payload["required"] = ["op", *payload.get("required", [])]
        choices.append(payload)
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"operations": {"type": "array", "minItems": 1, "items": {"oneOf": choices}}},
        "required": ["operations"],
        "description": "\n".join(mutate.GUIDANCE["batch.apply"]),
    }


EXAMPLE = {"operations": [
    {"op": "criterion set", "criterion": "AC-T-EXAMPLE-01", "state": "PASS", "evidence": "unit tests pass",
     "expectRevision": "0123456789abcdef"},
    {"op": "criterion set", "criterion": "AC-T-EXAMPLE-02", "state": "PASS", "evidence": "unit tests pass",
     "expectRevision": "0123456789abcdef"},
    {"op": "journal append", "title": "Checked T-EXAMPLE-01", "body": "Both criteria pass."},
]}


def _sections(data: bytes) -> dict[str, str]:
    return {heading: mutate.section(data, heading).revision
            for match in re.finditer(rb"(?m)^## ([^\r\n]+)\r?$", data)
            for heading in [match.group(1).decode("utf-8").strip()]}


def _anchor(heading: str) -> str:
    return re.split(r": | — ", heading, maxsplit=1)[0] if re.match(r"[A-Z][A-Za-z0-9]*-", heading) else heading


class Overlay:
    """The captured chronicle plus every change staged by earlier operations."""

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
        """Stage one operation's plan; nothing is locked, validated, written or refreshed here."""
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

    def payload(self, operation: Operation, value: dict) -> tuple[dict, tuple[str, str] | None]:
        """Map the caller's revision onto the staged section; return the revision to retry with."""
        payload = copy.deepcopy(value)
        target = operation.revision_target(self.project(), payload) if operation.revision_target else None
        if target is None:
            return payload, None
        field, source = target
        expected = payload.get(field)
        if not isinstance(expected, str):
            return payload, None
        key = self.snapshot.resolve(source.file)
        pointer = f"{key}#{source.anchor}"
        current = mutate.section(self.files[key], source.anchor).revision
        original = self.snapshot.original(key)
        try:
            initial = mutate.section(original, source.anchor).revision if original is not None else None
        except mutate.WriteError:
            initial = None
        if expected == PREVIOUS:
            if pointer not in self.touched:
                raise mutate.WriteError("invalid-revision",
                                        "$previous needs an earlier operation in this batch to have written "
                                        "this section; give the revision read before the batch", field)
            payload[field] = current
        elif expected in (initial, current):
            payload[field] = current
        retry = (field, initial) if initial is not None and initial != current else None
        return payload, retry


def _entry(entry: object) -> tuple[str, dict]:
    if not isinstance(entry, dict):
        raise mutate.WriteError("wrong-type", "each operation must be a JSON object", "operations")
    name = entry.get("op")
    if "op" not in entry:
        raise mutate.WriteError("missing-field", "required field: op", "op")
    if not isinstance(name, str) or name not in OPERATIONS:
        raise mutate.WriteError("invalid-value", f"op must be one of {', '.join(OPERATIONS)}", "op")
    return name, {key: value for key, value in entry.items() if key != "op"}


def _staged_refusal(error: mutate.WriteError, retry: tuple[str, str]) -> mutate.WriteError:
    """A stale revision is reported against the chronicle, not against this batch's staged bytes."""
    field, initial = retry
    details = {key: value for key, value in error.details.items() if key not in STAGED_DETAILS}
    details["currentRevision"] = initial
    return mutate.WriteError("stale-revision",
                             f"the record changed before this batch; retry the batch with {field} {initial}",
                             field, details)


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
                name = entry.get("op") if isinstance(entry, dict) else None
                retry = None
                try:
                    name, body = _entry(entry)
                    operation = OPERATIONS[name]
                    body, retry = overlay.payload(operation, body)
                    result = operation.handler(root, body)
                    if not result["ok"]:
                        error = result["error"]
                        raise mutate.WriteError(error["code"], error["message"], error.get("field"),
                                                {k: v for k, v in error.items() if k not in ("code", "message", "field")})
                    # Closure must not erase a failure present before this batch.
                    # Use the original project even after intermediate criterion resets.
                    if name == "task update" and body.get("set", {}).get("status") == "DONE":
                        original = project.task(body["id"])
                        failed = [c.id for c in project.mandatory_criteria(original) if c.state == "FAIL"] if original else []
                        if failed:
                            raise mutate.WriteError(
                                "failing-criterion-closure",
                                f"cannot set {original.id} DONE in the same batch that clears failing criteria: "
                                f"{', '.join(failed)}; {mutate.FAILED_ACCEPTANCE_GUIDANCE}",
                                "status", {"criteria": failed})
                    results.append({"index": index, "op": name, **{
                        k: v for k, v in result.items() if k not in ("ok", "operation", "written", "refreshed")}})
                except mutate.WriteError as error:
                    if error.code == "stale-revision" and retry:
                        error = _staged_refusal(error, retry)
                    if isinstance(error.details.get("example"), dict):
                        error.details["example"] = {"op": name, **error.details["example"]}
                    error.details.update(index=index, op=name)
                    raise error
                except Exception as error:
                    raise mutate.WriteError("write-failed", str(error), details={"index": index, "op": name}) from error
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
