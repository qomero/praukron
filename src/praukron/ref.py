"""Write task-code references to CODE.md through validated structured commands (SPEC §2.9, ADR-088).

`ref add` appends one reference and allocates its identifier. `ref set` changes
a reference's verification, lifecycle or evidence in place, or appends a record
that withdraws or replaces it; the earlier record then stays byte for byte.
`ref history` prints a reference and every record it replaces, unchanged.

A `set` that settles an observed reference recorded as unresolved attribution
(T-SCOPE-ATTRIB-01), by giving it any other lifecycle, needs evidence and is
written as a successor that replaces it, so the unresolved record and its
evidence stay as history. Such a reference is never set verified while it
stays unresolved.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

from . import compile as compiler, mutate
from .layout import AUTHORITY_DIR
from .model import (
    REFERENCE_CLASSES,
    REFERENCE_KINDS,
    REFERENCE_LIFECYCLES,
    REFERENCE_RELATIONS,
    REFERENCE_SCOPES,
    REFERENCE_SOURCES,
    REFERENCE_VERIFICATIONS,
    Project,
    Reference,
    reference_path_problem,
)
from .parse import _fields

FILE = "CODE.md"
# The header of templates/chronicle/CODE.md without its "No references recorded
# yet." line; a test keeps the two in step. A write creates CODE.md from it.
HEADER = """# Code references

Which files and symbols each task relates to, one reference per record
(SPEC §2.9). A reference is authority like every other record: a graph
provider never writes one, and removing a provider changes none. A task needs
no references to be valid, and its `Files:` and `Symbols:` anchors keep
working beside them.

A reference is never deleted. Record a replacement with `Replaces:`, or mark
it `Withdrawn:` with a date and a reason; the earlier record stays, so the
history of a file survives its rename or deletion. Never record a secret.

Format:

    ## CR-T-FIN-01-01: Import payment terms
    - Task: T-FIN-01
    - Path: src/fin/terms.py
    - Symbol: Terms.load
    - Kind: method
    - Relation: modifies
    - Scope: planned
    - Verification: unverified
    - Authorization: proposed
    - Source: agent
    - Recorded: 2026-01-15 · agent/builder

The identifier is `CR-`, the task's identifier, and a two-digit sequence
within that task. `Path` is relative to the repository root with `/`
separators; the file need not exist yet. `Symbol` is the qualified name inside
that file, and needs `Kind`. `Relation` is `creates`, `modifies`, `deletes`,
`reads`, `depends_on`, `verifies`, `documents` or `affects`; `Scope` is
`planned` or `observed`. `Verification` (whether the reference matches the
code) and `Authorization` (whether the task may make the change) are separate.
`praukron validate` lists every field and refuses values outside them.
"""

# Record field order, as SPEC §2.9 lists the fields.
ORDER = ("Task", "Repository", "Path", "Symbol", "Kind", "Class", "Relation", "Scope", "Lifecycle",
         "Verification", "Authorization", "Authorized by", "Source", "Provider", "Recorded", "Updated", "Revision",
         "Base", "Head", "Lines", "Confidence", "Was", "Replaces", "Withdrawn", "Evidence")
# A planned reference with one of these relations is a change the task may need permission for.
CHANGES = ("creates", "modifies", "deletes")

# Payload field -> record label, for the descriptive fields a payload may give.
DESCRIPTIVE = {
    "repository": "Repository", "path": "Path", "symbol": "Symbol", "kind": "Kind", "class": "Class",
    "relation": "Relation", "scope": "Scope", "lifecycle": "Lifecycle", "verification": "Verification",
    "commit": "Revision", "base": "Base", "head": "Head", "lines": "Lines", "confidence": "Confidence",
    "was": "Was", "evidence": "Evidence",
}
# What a replacement inherits from the record it replaces unless the payload gives it.
INHERITED = ("repository", "path", "symbol", "kind", "class", "relation", "scope", "lifecycle")

_LINE = {"type": "string", "minLength": 1}
_PROVENANCE = {
    "source": {"type": "string", "enum": list(REFERENCE_SOURCES), "examples": ["agent"],
               "description": "How this write's claim was obtained; written as Source"},
    "provider": {**_LINE, "description": "The provider's name and version; required with source graph_provider"},
    "recordedBy": {**_LINE, "examples": ["agent/builder"],
                   "description": "Who writes the record; written as Recorded: <date> · <recordedBy>"},
    "recordedOn": {"type": "string", "format": "date", "description": "YYYY-MM-DD; defaults to today"},
}
_FIELDS = {
    "title": {**_LINE, "description": "One line", "examples": ["Post a ledger entry"]},
    "repository": {**_LINE, "description": "Another repository's name; absent means this one"},
    "path": {**_LINE, "description": "Repository-relative path with / separators; need not exist",
             "examples": ["src/pay/ledger.py"]},
    "symbol": {**_LINE, "description": "Qualified name inside path; needs kind"},
    "kind": {"type": "string", "enum": list(REFERENCE_KINDS)},
    "class": {"type": "string", "enum": list(REFERENCE_CLASSES)},
    "relation": {"type": "string", "enum": list(REFERENCE_RELATIONS), "examples": ["modifies"]},
    "scope": {"type": "string", "enum": list(REFERENCE_SCOPES), "examples": ["planned"]},
    "lifecycle": {"type": "string", "enum": list(REFERENCE_LIFECYCLES)},
    "verification": {"type": "string", "enum": list(REFERENCE_VERIFICATIONS),
                     "description": "Defaults to unverified"},
    "authorizedBy": {**_LINE, "description": "An owner response (R-) or ACCEPTED ADR granting a planned "
                                             "creates, modifies or deletes; without it such a reference "
                                             "is written proposed"},
    "commit": {**_LINE, "description": "The commit the reference was true at; written as Revision"},
    "base": {**_LINE, "description": "Revision an observed reference was captured from"},
    "head": {**_LINE, "description": "Revision an observed reference was captured to"},
    "lines": {**_LINE, "description": "start-end, a hint only"},
    "confidence": {"type": "number", "description": "0 to 1; absent means unknown"},
    "was": {**_LINE, "description": "The earlier path, or path#symbol, of a renamed or moved artifact"},
    "evidence": {**_LINE, "description": "One line: what shows the reference is true"},
}

ADD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "task": {**_LINE, "description": "T- identifier", "examples": ["T-EXAMPLE-01"]},
        **_FIELDS, **_PROVENANCE,
    },
    "required": ["task", "title", "path", "relation", "scope", "source", "recordedBy"],
}
ADD_EXAMPLE = {"task": "T-EXAMPLE-01", "title": "Post a ledger entry", "path": "src/pay/ledger.py",
               "symbol": "Ledger.post", "kind": "method", "relation": "modifies", "scope": "planned",
               "source": "agent", "recordedBy": "agent/builder"}

SET_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {**_LINE, "description": "CR- identifier", "examples": ["CR-T-EXAMPLE-01-01"]},
        "expectRevision": {"type": "string", "minLength": 16, "examples": ["0123456789abcdef"],
                           "description": "16 hex revision of the reference's section"},
        "set": {
            "type": "object", "additionalProperties": False,
            "description": "Change the record in place",
            "properties": {
                "verification": {**_FIELDS["verification"], "description": "unverified, verified, disputed or stale",
                                 "examples": ["verified"]},
                "lifecycle": _FIELDS["lifecycle"],
                "evidence": _FIELDS["evidence"],
            },
        },
        "withdraw": {
            "type": "object", "additionalProperties": False,
            "description": "Append a record that withdraws this one; this one stays unchanged",
            "properties": {
                "reason": {**_LINE, "description": "One line", "examples": ["the task no longer touches it"]},
                "date": {"type": "string", "format": "date", "description": "YYYY-MM-DD; defaults to today"},
            },
            "required": ["reason"],
        },
        "replace": {
            "type": "object", "additionalProperties": False,
            "description": "Append a record that replaces this one; this one stays unchanged. Path, repository, "
                           "symbol, kind, class, relation, scope, lifecycle and title are inherited unless given",
            "properties": dict(_FIELDS),
        },
        **_PROVENANCE,
    },
    "required": ["id", "expectRevision", "recordedBy"],
}
SET_EXAMPLE = {"id": "CR-T-EXAMPLE-01-01", "expectRevision": "0123456789abcdef",
               "recordedBy": "agent/builder", "set": {"verification": "verified", "evidence": "tests pass"}}

ADD_SCHEMA["description"] = "\n".join(mutate.GUIDANCE["ref.add"])
SET_SCHEMA["description"] = "\n".join(mutate.GUIDANCE["ref.set"])
mutate.SCHEMAS["ref.add"] = (ADD_SCHEMA, ())
mutate.SCHEMAS["ref.set"] = (SET_SCHEMA, ("set.verification",))


def _line(value: str, field: str) -> str:
    if value != value.strip() or "\n" in value or "\r" in value or not value.strip():
        raise mutate.WriteError("invalid-value", f"{field} must be non-empty, trimmed and one line", field)
    return value


def _check_fields(values: dict) -> None:
    """Refuse what the record could not hold or validate would reject."""
    for name, value in values.items():
        if isinstance(value, str):
            _line(value, name)
    if "path" in values:
        problem = reference_path_problem(values["path"])
        if problem:
            raise mutate.WriteError("invalid-reference-path", f"path '{values['path']}': {problem}", "path")
    if "confidence" in values and not 0 <= values["confidence"] <= 1:
        raise mutate.WriteError("invalid-value", "confidence must be a number from 0 to 1", "confidence")
    if "lines" in values and not re.fullmatch(r"[0-9]+-[0-9]+", values["lines"]):
        raise mutate.WriteError("invalid-value", "lines must be start-end", "lines")


def _check_provenance(payload: dict) -> None:
    _line(payload["recordedBy"], "recordedBy")
    if payload["source"] == "graph_provider" and "provider" not in payload:
        raise mutate.WriteError("missing-field", "source graph_provider needs provider", "provider")
    if payload["source"] != "graph_provider" and "provider" in payload:
        raise mutate.WriteError("invalid-value", "provider is given only with source graph_provider", "provider")
    if "provider" in payload:
        _line(payload["provider"], "provider")


def _provenance(payload: dict) -> dict[str, str]:
    fields = {"Source": payload["source"]}
    if "provider" in payload:
        fields["Provider"] = payload["provider"]
    date = payload.get("recordedOn", dt.date.today().isoformat())
    fields["Recorded"] = f"{date} · {payload['recordedBy']}"
    return fields


def _authority(project: Project, authorized_by: str) -> None:
    if authorized_by in {r.id for r in project.responses}:
        return
    decision = next((d for d in project.decisions if d.id == authorized_by), None)
    if decision is not None and decision.status == "ACCEPTED":
        return
    raise mutate.WriteError(
        "unknown-authority",
        f"authorizedBy {authorized_by} names no owner response or ACCEPTED ADR", "authorizedBy")


def authorization(project: Project, scope: str, relation: str, authorized_by: str | None) -> dict[str, str]:
    """The only way a write sets Authorization (AC-T-CIL-MAP-02-02).

    A planned creates, modifies or deletes is `proposed` unless an owner
    response or an accepted ADR is named; every other reference is
    `not_applicable`.
    """
    if scope == "planned" and relation in CHANGES:
        if authorized_by is None:
            return {"Authorization": "proposed"}
        _authority(project, authorized_by)
        return {"Authorization": "authorized", "Authorized by": authorized_by}
    if authorized_by is not None:
        raise mutate.WriteError(
            "authorization-not-applicable",
            f"a {scope} {relation} reference needs no authorization", "authorizedBy")
    return {"Authorization": "not_applicable"}


def _next_id(project: Project, task_id: str) -> str:
    pattern = re.compile(rf"CR-{re.escape(task_id)}-([0-9]{{2,}})")
    used = [int(m.group(1)) for r in project.references if (m := pattern.fullmatch(r.id))]
    return f"CR-{task_id}-{max(used, default=0) + 1:02d}"


def _render(reference_id: str, title: str, fields: dict[str, str]) -> str:
    lines = [f"## {reference_id}: {title}"]
    lines += [f"- {label}: {fields[label]}" for label in ORDER if fields.get(label) is not None]
    return "\n".join(lines) + "\n"


def _append(path: Path, text: str) -> bytes:
    original = mutate.read_bytes(path) if mutate.is_file(path) else HEADER.encode("utf-8")
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    padding = b"" if original.endswith(newline * 2) else newline if original.endswith(newline) else newline * 2
    return original + padding + text.encode("utf-8").replace(b"\n", newline)


def _confidence(value: float | int) -> str:
    return repr(float(value)) if isinstance(value, float) else str(value)


def _descriptive(values: dict) -> dict[str, str]:
    out = {}
    for key, label in DESCRIPTIVE.items():
        if key in values:
            out[label] = _confidence(values[key]) if key == "confidence" else values[key]
    return out


def _check_symbol(fields: dict[str, str]) -> None:
    if fields.get("Symbol") and not fields.get("Kind"):
        raise mutate.WriteError("symbol-without-kind", "a symbol needs kind", "kind")


def _task(project: Project, task_id: str):
    task = project.task(task_id)
    if task is None:
        raise mutate.WriteError("unknown-reference-task", f"no task named {task_id}", "task")
    return task


def add(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "ref.add"
    try:
        mutate.validate_payload(payload, ADD_SCHEMA)
        _check_fields({k: v for k, v in payload.items() if k in _FIELDS})
        _check_provenance(payload)
        if not re.fullmatch(r"T-[A-Za-z0-9.\-]+", payload["task"]):
            raise mutate.WriteError("invalid-id", "task must be a T- identifier", "task")

        def prepare(project: Project) -> mutate.Plan:
            _task(project, payload["task"])
            fields = {"Task": payload["task"], **_descriptive(payload)}
            fields.setdefault("Verification", "unverified")
            fields.update(authorization(project, payload["scope"], payload["relation"],
                                        payload.get("authorizedBy")))
            fields.update(_provenance(payload))
            _check_symbol(fields)
            reference_id = _next_id(project, payload["task"])
            path = root / AUTHORITY_DIR / FILE
            return mutate.Plan({path: _append(path, _render(reference_id, payload["title"], fields))},
                               reference_id, f"{FILE}#{reference_id}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)


def _record_fields(section: bytes) -> dict[str, str]:
    text = section.decode("utf-8")
    return _fields(text.split("\n", 1)[1] if "\n" in text else "")


def _edit(section: bytes, changes: dict[str, str]) -> bytes:
    """Rewrite or insert only the named field lines of one reference section."""
    text = section.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    if re.search(r"\r|\n", text.replace(newline, "")):
        raise mutate.WriteError("mixed-newlines", "the reference section mixes line endings")
    lines = text.split(newline)
    field = re.compile(r"- ([A-Za-z][A-Za-z ]*): ?(.*)")

    def where(label: str) -> list[int]:
        return [i for i, line in enumerate(lines) if (m := field.match(line)) and m.group(1).strip() == label]

    def end_of(start: int) -> int:
        end = start + 1
        while end < len(lines) and lines[end].startswith("  ") and lines[end].strip():
            end += 1
        return end

    for label, value in changes.items():
        found = where(label)
        if len(found) > 1:
            raise mutate.WriteError("duplicate-field", f"{label} appears more than once", label.lower())
        line = f"- {label}: {value}"
        if found:
            lines[found[0]:end_of(found[0])] = [line]
            continue
        at = 1
        for earlier in reversed(ORDER[:ORDER.index(label)]):
            if where(earlier):
                at = end_of(where(earlier)[0])
                break
        lines[at:at] = [line]
    updated = newline.join(lines)
    fields = _record_fields(updated.encode("utf-8"))
    for label, value in changes.items():
        if fields.get(label) != value:
            raise mutate.WriteError("invalid-value", f"{label} would not read back as given", label.lower())
    return updated.encode("utf-8")


def _find(project: Project, reference_id: str) -> Reference:
    found = next((r for r in project.references if r.id == reference_id), None)
    if found is None:
        raise mutate.WriteError("record-not-found", f"no reference {reference_id}", "id")
    return found


def set_reference(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "ref.set"
    stale: dict = {}
    settled: dict = {}
    try:
        mutate.validate_payload(payload, SET_SCHEMA)
        reference_id, expected = payload["id"], payload["expectRevision"]
        if not re.fullmatch(r"[0-9a-f]{16}", expected):
            raise mutate.WriteError("invalid-value", "expectRevision must be 16 lowercase hex characters",
                                    "expectRevision")
        modes = [mode for mode in ("set", "withdraw", "replace") if mode in payload]
        if len(modes) != 1:
            raise mutate.WriteError("invalid-change", "give exactly one of set, withdraw and replace",
                                    modes[1] if len(modes) > 1 else "set")
        mode = modes[0]
        change = payload[mode]
        if not change and mode != "replace":
            raise mutate.WriteError("invalid-change", f"{mode} must name at least one field", mode)
        _check_fields({k: v for k, v in change.items() if k != "date"})
        if mode == "set":
            # The record's origin stays as first recorded; the change is noted in Updated.
            for name in ("source", "provider"):
                if name in payload:
                    raise mutate.WriteError(
                        "provenance-kept", f"set never changes Source or Provider; remove {name}", name)
            _line(payload["recordedBy"], "recordedBy")
        else:
            if "source" not in payload:
                raise mutate.WriteError("missing-field", f"{mode} writes a new record and needs source", "source")
            _check_provenance(payload)

        def prepare(project: Project) -> mutate.Plan:
            earlier = _find(project, reference_id)
            mutate.ensure_mutable(earlier.source)
            path = root / AUTHORITY_DIR / earlier.source.file
            data = mutate.read_bytes(path)
            current = mutate.section(data, earlier.source.anchor)
            if current.revision != expected:
                stale.update(currentRevision=current.revision,
                             section=current.data.decode("utf-8").rstrip("\r\n"))
                raise mutate.WriteError(
                    "stale-revision", f"{reference_id} changed: expected revision {expected}, current "
                    f"{current.revision}; retry with currentRevision", "expectRevision")
            successor = next((r.id for r in project.references if r.replaces == reference_id), None)
            if successor:
                raise mutate.WriteError("replaced-reference",
                                        f"{reference_id} is replaced by {successor}", "id",
                                        {"replacedBy": successor})
            if earlier.withdrawn:
                raise mutate.WriteError("withdrawn-reference",
                                        f"{reference_id} is withdrawn", "id")
            fields = _record_fields(current.data)
            unattributed = earlier.scope == "observed" and earlier.lifecycle == "unresolved"
            if mode == "set" and unattributed:
                if change.get("lifecycle", "unresolved") != "unresolved":
                    return _settle(project, path, earlier, fields, change, payload)
                if change.get("verification") == "verified":
                    raise mutate.WriteError(
                        "unresolved-attribution", f"{reference_id} is unresolved attribution and is never "
                        "verified while it stays so; settle it with a lifecycle and evidence, or withdraw it",
                        "set.verification")
            if mode == "set":
                edits = _descriptive(change)
                if all(fields.get(label) == value for label, value in edits.items()):
                    raise mutate.WriteError("no-change", "the update would change nothing")
                date = payload.get("recordedOn", dt.date.today().isoformat())
                edits["Updated"] = f"{date} · {payload['recordedBy']}"
                start, end = current.span
                return mutate.Plan({path: data[:start] + _edit(current.data, edits) + data[end:]},
                                   reference_id, f"{earlier.source.file}#{reference_id}")
            kept = {label: value for label, value in fields.items()
                    if label in ORDER and label not in ("Source", "Provider", "Recorded", "Updated", "Replaces", "Withdrawn")}
            title = earlier.title
            if mode == "withdraw":
                new = kept
                date = change.get("date", dt.date.today().isoformat())
                new["Withdrawn"] = f"{date} · {change['reason']}"
            else:
                new = {"Task": earlier.task}
                new.update({DESCRIPTIVE[key]: kept[DESCRIPTIVE[key]] for key in INHERITED
                            if DESCRIPTIVE[key] in kept})
                new.update(_descriptive(change))
                new.setdefault("Verification", "unverified")
                title = change.get("title", title)
                new.update(authorization(project, new["Scope"], new["Relation"], change.get("authorizedBy")))
            new.update(_provenance(payload))
            new["Replaces"] = reference_id
            _check_symbol(new)
            successor_id = _next_id(project, earlier.task)
            return mutate.Plan({path: _append(path, _render(successor_id, title, new))},
                               successor_id, f"{earlier.source.file}#{successor_id}")

        def _settle(project: Project, path: Path, earlier: Reference, fields: dict, change: dict,
                    payload: dict) -> mutate.Plan:
            if "evidence" not in change:
                raise mutate.WriteError(
                    "settle-without-evidence", f"{reference_id} is unresolved attribution; settling it needs "
                    "evidence that the task made the change", "set.evidence")
            new = {label: value for label, value in fields.items()
                   if label in ORDER and label not in ("Recorded", "Updated", "Replaces", "Withdrawn")}
            new.update(_descriptive(change))
            new["Verification"] = change.get("verification", "unverified")
            date = payload.get("recordedOn", dt.date.today().isoformat())
            new["Recorded"] = f"{date} · {payload['recordedBy']}"
            new["Replaces"] = reference_id
            successor_id = _next_id(project, earlier.task)
            settled["id"] = successor_id
            return mutate.Plan({path: _append(path, _render(successor_id, earlier.title, new))},
                               successor_id, f"{earlier.source.file}#{successor_id}")

        result = mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)
    if result.get("ok") and (mode != "set" or settled):
        result["replaces"] = reference_id
    if stale and not result.get("ok", True) and result.get("error", {}).get("code") == "stale-revision":
        result["error"].update(stale)
    return result


def history(root: Path, reference_id: str) -> list[dict]:
    """The reference and every record it replaces, oldest first, each as written."""
    project = compiler.load(root)
    by_id = {r.id: r for r in project.references}
    if reference_id not in by_id:
        raise mutate.WriteError("record-not-found", f"no reference {reference_id}", "id")
    chain, seen = [], set()
    current: str | None = reference_id
    while current and current in by_id and current not in seen:
        seen.add(current)
        chain.append(by_id[current])
        current = by_id[current].replaces
    records = []
    for reference in reversed(chain):
        data = (root / AUTHORITY_DIR / reference.source.file).read_bytes()
        found = mutate.section(data, reference.source.anchor)
        records.append({"id": reference.id, "revision": found.revision,
                        "source": f"{reference.source.file}#{reference.id}",
                        "record": found.data.rstrip(b"\r\n")})
    return records


def command_add(root: Path, args) -> int:
    return _command(root, args, "ref.add", ADD_SCHEMA, ADD_EXAMPLE, add)


def command_set(root: Path, args) -> int:
    return _command(root, args, "ref.set", SET_SCHEMA, SET_EXAMPLE, set_reference)


def _command(root: Path, args, operation: str, schema: dict, example: dict, handler) -> int:
    if args.schema:
        result = {"schema": schema, "example": example}
    else:
        try:
            result = handler(root, mutate.load_payload(args.input, schema), dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal(operation, error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1


def command_history(root: Path, args) -> int:
    try:
        records = history(root.resolve(), args.id)
    except mutate.WriteError as error:
        if args.json:
            print(json.dumps(mutate.refusal("ref.history", error), separators=(",", ":")))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "id": args.id, "records": [
            {**r, "record": r["record"].decode("utf-8")} for r in records]}, separators=(",", ":"),
            ensure_ascii=False))
        return 0
    sys.stdout.flush()
    out = getattr(sys.stdout, "buffer", None)
    text = b"\n\n".join(r["record"] for r in records) + b"\n"
    if out is None:
        sys.stdout.write(text.decode("utf-8"))
    else:
        out.write(text)
        out.flush()
    return 0
