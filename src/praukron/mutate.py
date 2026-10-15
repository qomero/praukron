"""Shared persistence and validation for structured chronicle writes (ADR-068)."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import analytics, compile as compiler, dashboard, mermaid, validate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR, COMPILED_DIR, RECOVERY_DIR
from .model import Project, Source
# Keep the low-level primitives at their existing import and inspection surface.
# respond's public writers delegate recovery and refresh here through lazy imports.
from .respond import RespondError, _lock, _replace, _staging_paths


class WriteError(Exception):
    """A structured write was refused."""

    def __init__(self, code: str, message: str, field: str | None = None,
                 details: dict | None = None):
        super().__init__(message)
        self.code, self.field, self.details = code, field, details or {}


# Agent-facing guidance for the five write commands: WHEN / WHEN NOT / RETURNS / EXAMPLE.
# Shown by `--help` and carried in each `--schema` output.
GUIDANCE = {
    "journal.append": (
        "WHEN: record a dated progress note, finding or handoff detail in JOURNAL.md.",
        "WHEN NOT: to change a task, criterion or decision (use task update, criterion set or an ADR); "
        "the body may not hold level one or two headings.",
        "RETURNS: one JSON line with ok, recordId, source, revision, written. A repeated title on the same "
        "date is refused as duplicate-heading, never appended twice.",
        "EXAMPLE: echo '{\"title\":\"Progress\",\"body\":\"Recorded the result.\"}' | praukron journal append --input -",
    ),
    "criterion.set": (
        "WHEN: record that one criterion passed, failed or was not run, with its one-line evidence; "
        "allowed on any task, WIP included.",
        "WHEN NOT: to change a criterion's text or class (an Acceptance Change Request), or an inherited "
        "invariant criterion.",
        "RETURNS: one JSON line with ok, recordId, source, revision (the contract's new revision). A stale "
        "expectRevision is refused with currentRevision and the current criterion line; retry with that revision.",
        "EXAMPLE: echo '{\"criterion\":\"AC-T-ONE-01\",\"state\":\"PASS\",\"evidence\":\"unittest green\","
        "\"expectRevision\":\"<16 hex>\"}' | praukron criterion set --input -",
    ),
    "task.add": (
        "WHEN: create a task, with its new contract (contract) or an existing one (ac).",
        "WHEN NOT: to change an existing task (task update), or to give it a Phase (a task takes its phase "
        "from its module); the id must not exist in any archive.",
        "RETURNS: one JSON line with ok, recordId, source, revision (use it as the first expectRevision). "
        "Task and contract are written together or not at all.",
        "EXAMPLE: praukron task add --schema   (prints the payload schema and an example)",
    ),
    "contract.add": (
        "WHEN: give an existing TODO task its acceptance contract before work starts; "
        "needs expectTaskRevision.",
        "WHEN NOT: once the task is WIP or DONE (its contract is frozen; raise an Acceptance Change "
        "Request), or to edit an existing contract.",
        "RETURNS: one JSON line with ok, recordId, source, revision. A stale expectTaskRevision is refused "
        "with the task's current record and revision.",
        "EXAMPLE: praukron contract add --schema   (prints the payload schema and an example)",
    ),
    "task.update": (
        "WHEN: claim a task, change its status, owner or validation (UNTESTED, SYNTHETIC, AI_REVIEWED), "
        "record its evidence, or link it to further governing ADRs with addGovernedBy.",
        "WHEN NOT: to close an Authority: owner task, set HUMAN_VERIFIED, remove or reorder Governed by "
        "entries, or change Module, Dependencies, AC or Authority; DONE needs every criterion PASS and evidence.",
        "RETURNS: one JSON line with ok, recordId, source, revision (and alreadyGoverned when addGovernedBy "
        "named ids already listed, which are skipped). A stale expectRevision is refused with "
        "currentRevision and the current section.",
        "EXAMPLE: echo '{\"id\":\"T-ONE\",\"expectRevision\":\"<16 hex>\",\"set\":{\"status\":\"WIP\","
        "\"owner\":\"me\"}}' | praukron task update --input -",
    ),
}

# The next valid step for refusals whose message does not already carry one.
NEXT_STEP = {
    "wrong-type": "send a JSON object whose fields have the types `--schema` shows",
    "unknown-field": "remove it; `--schema` lists the supported fields",
    "missing-field": "add it; `--schema` lists the required fields",
    "invalid-value": "correct the named field (see `--schema`) and retry",
    "invalid-date": "use a real date written YYYY-MM-DD",
    "invalid-json": "send one valid JSON object without duplicate keys",
    "invalid-project": "run it inside a Praukron project, or pass -C <project>",
    "record-not-found": "check the id with `praukron retrieve <id>`",
    "archived-record": "archived records are immutable; record the change in a new record instead",
    "invalid-target": "report this as a bug; nothing was written",
    "duplicate-target": "report this as a bug; nothing was written",
    "validation-failed": "fix what `praukron validate` reports, then retry",
    "write-failed": "run `praukron validate`, fix what it reports, then retry",
    "invalid-title": "give a non-empty one-line title",
    "invalid-body": "remove the level one and two headings; level three and below are allowed",
    "criterion-not-found": "check the id with `praukron retrieve <contract id>`",
    "ambiguous-evidence": "tidy the criterion's Evidence lines by hand, then retry",
    "round-trip-failed": "nothing was written; edit the criterion by hand and retry",
    "invariant-criterion": "an inherited invariant changes only through an Acceptance Change Request, made by a person",
    "invalid-revision": "use the 16 hex revision from the last result or from a stale-revision refusal",
    "invalid-evidence": "give one non-empty line without line breaks",
    "invalid-id": "use an identifier with the required prefix",
    "invalid-contract": "give contract (a new definition) or ac (an existing contract id), not both and not neither",
    "duplicate-id": "choose an id no record or archive uses",
    "unknown-module": "use a module from MODULES.md, or author the module first",
    "unknown-dependency": "name an existing task id",
    "unknown-decision": "name an existing ADR id",
    "unknown-contract": "name an existing contract id, or give contract to write a new one",
    "referenced-contract": "choose a contract id no other task names",
    "unknown-inheritance": "inherit an existing contract id",
    "different-contract": "the task already names a contract; change it through an Acceptance Change Request",
    "mixed-newlines": "fix the task section's line endings by hand, then retry",
    "duplicate-field": "remove the repeated field from the task by hand, then retry",
    "empty-set": "set at least one of status, owner, claimed, evidence or validation, or give addGovernedBy",
    "no-change": "nothing to do; the record already has these values",
}


def refusal(operation: str, error: WriteError) -> dict:
    step = NEXT_STEP.get(error.code)
    detail = {"code": error.code, "message": f"{error}; {step}" if step else str(error)}
    if error.field is not None:
        detail["field"] = error.field
    detail.update(error.details)
    return {"ok": False, "operation": operation, "error": detail, "written": False}


def validate_payload(payload: object, schema: dict) -> dict:
    """Check the small JSON Schema subset used by command payloads."""
    if not isinstance(payload, dict):
        raise WriteError("wrong-type", "payload must be a JSON object")
    properties = schema["properties"]
    for name in payload:
        if name not in properties:
            raise WriteError("unknown-field", f"unsupported field: {name}", name)
    for name in schema.get("required", []):
        if name not in payload:
            raise WriteError("missing-field", f"required field: {name}", name)
    types = {"string": str, "object": dict, "array": list, "boolean": bool,
             "integer": int, "number": (int, float)}
    for name, value in payload.items():
        spec = properties[name]
        expected = types[spec["type"]]
        if not isinstance(value, expected) or (isinstance(value, bool) and spec["type"] in ("integer", "number")):
            raise WriteError("wrong-type", f"{name} must be {spec['type']}", name)
        if spec["type"] == "string":
            minimum = spec.get("minLength", 0)
            if len(value) < minimum:
                raise WriteError("invalid-value", f"{name} must contain at least {minimum} characters", name)
            if spec.get("format") == "date":
                try:
                    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                        raise ValueError("expected YYYY-MM-DD")
                    dt.date.fromisoformat(value)
                except ValueError as error:
                    raise WriteError("invalid-date", f"{name} must be a valid YYYY-MM-DD date", name) from error
        if "enum" in spec and value not in spec["enum"]:
            raise WriteError("invalid-value", f"{name} must be one of {', '.join(spec['enum'])}", name)
        if spec["type"] == "object" and "properties" in spec:
            validate_payload(value, spec)
    return payload


def read_stdin_text() -> str:
    """Read stdin as strict UTF-8 (optional BOM) whatever the console code page."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return sys.stdin.read().removeprefix("\ufeff")
    try:
        return stream.read().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"stdin is not valid UTF-8: {error}") from error


def load_payload(path: str | None, schema: dict) -> dict:
    if path is None:
        raise WriteError("missing-input", "give --input FILE or --input -")

    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate field: {key}")
            out[key] = value
        return out

    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        if path == "-":
            text = read_stdin_text()
        else:
            text = Path(path).read_text(encoding="utf-8-sig")
        payload = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, OSError) as error:
        raise WriteError("invalid-json", f"cannot read JSON payload: {error}") from error
    try:
        return validate_payload(payload, schema)
    except WriteError as error:
        error.payload = payload
        raise


def revision(section_bytes: bytes) -> str:
    """Hash record content without its trailing CR/LF separators."""
    return hashlib.sha256(section_bytes.rstrip(b"\r\n")).hexdigest()[:16]


@dataclass(frozen=True)
class Section:
    data: bytes
    span: tuple[int, int]

    @property
    def revision(self) -> str:
        return revision(self.data)


def section(data: bytes, record_id: str) -> Section:
    """Find a full journal heading or an ID, retaining exact byte offsets."""
    for match in re.finditer(rb"(?m)^## ([^\r\n]+)\r?$", data):
        heading = match.group(1).decode("utf-8").strip()
        parsed_id = re.split(r": | — ", heading, maxsplit=1)[0]
        if heading != record_id and parsed_id != record_id:
            continue
        following = re.search(rb"(?m)^#{1,2} ", data[match.end():])
        end = match.end() + following.start() if following else len(data)
        return Section(data[match.start():end], (match.start(), end))
    raise WriteError("record-not-found", f"no '## ' section found for {record_id}")


def working_section(root: Path, source: Source) -> Section:
    ensure_mutable(source)
    return section((root / AUTHORITY_DIR / source.file).read_bytes(), source.anchor)


def ensure_mutable(source: Source | str) -> None:
    name = source.file if isinstance(source, Source) else source
    if ARCHIVE_DIR in PurePosixPath(name.replace("\\", "/")).parts:
        raise WriteError("archived-record", f"archived records are immutable: {name}")


def id_exists(project: Project, record_id: str) -> bool:
    """The loader already merges working records and every archive."""
    groups = (project.phases, project.modules, project.tasks, project.contracts.values(),
              project.gates, project.milestones, project.decisions, project.events,
              project.debts, project.assumptions, project.responses)
    return (any(item.id == record_id for group in groups for item in group)
            or any(c.id == record_id for contract in project.contracts.values() for c in contract.criteria))


def _recovery_records(root: Path) -> list[Path]:
    directory = root.resolve() / RECOVERY_DIR
    return sorted([*directory.glob("archive-*.json"), *directory.glob("write-*.json")])


def _replacement_paths(root: Path, payload: dict) -> dict[Path, Path]:
    paths = {}
    for entry in payload["files"]:
        if "temporary" not in entry:
            continue
        target, temporary = (root / entry["path"]).resolve(), (root / entry["temporary"]).resolve()
        if (root not in target.parents or temporary.parent != target.parent
                or not temporary.name.startswith(".respond-")):
            raise ValueError("invalid recovery staging path")
        paths[target] = temporary
    return paths


@contextmanager
def staged_replacements(root: Path, record: Path):
    payload = json.loads(record.read_text(encoding="utf-8"))
    with _staging_paths(_replacement_paths(root.resolve(), payload)):
        yield


def recover_write(root: Path) -> bool:
    """Restore any interrupted mutation before another writer proceeds."""
    root = root.resolve()
    records = _recovery_records(root)
    if not records:
        return False
    for record in records:
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
            staging = _replacement_paths(root, payload)
            for temporary in staging.values():
                temporary.unlink(missing_ok=True)
            with _staging_paths(staging):
                for entry in payload["files"]:
                    path = (root / entry["path"]).resolve()
                    if root not in path.parents:
                        raise ValueError("recovery path is outside the project")
                    if entry.get("absent"):
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        _replace(path, base64.b64decode(entry["bytes"], validate=True))
            directories = sorted(payload.get("directories", []), key=lambda name: (len(Path(name).parts), name),
                                 reverse=True)
            for name in directories:
                path = (root / name).resolve()
                if root not in path.parents:
                    raise ValueError("recovery directory is outside the project")
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
        except (KeyError, TypeError, ValueError, OSError) as failure:
            raise RespondError(f"cannot restore interrupted write: {failure}") from failure
        record.unlink()
    return True


recover_archive = recover_write


def _write_recovery(root: Path, originals: dict[Path, bytes | None], prefix: str = "archive", *,
                    directories: list[Path] | tuple[Path, ...] = ()) -> Path:
    """Persist originals before replacing any authoritative file."""
    root = root.resolve()
    directory = root / RECOVERY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("*\n", encoding="utf-8")
    record = directory / f"{prefix}-{time.time_ns()}.json"
    files = []
    for number, (path, original) in enumerate(originals.items()):
        temporary = path.parent / f".respond-{record.stem}-{number}"
        item = {"path": path.resolve().relative_to(root).as_posix(),
                "temporary": temporary.resolve().relative_to(root).as_posix()}
        if original is None:
            item["absent"] = True
        else:
            item["bytes"] = base64.b64encode(original).decode("ascii")
        files.append(item)
    payload = {"files": files}
    if directories:
        payload["directories"] = [path.resolve().relative_to(root).as_posix() for path in directories]
    _replace(record, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return record


def _spans(text: str, pattern: re.Pattern[str]) -> dict[str, tuple[int, int]]:
    """Sections bounded by level one or two headings, like parse._sections."""
    spans = {}
    for heading in re.finditer(r"(?m)^## (.+)$", text):
        found = pattern.match(heading.group(1).strip())
        if not found:
            continue
        following = re.compile(r"(?m)^#{1,2} ").search(text, heading.end())
        spans[found.group(1)] = (heading.start(), following.start() if following else len(text))
    return spans


def _errors(project: Project, recovery: Path | None = None) -> set[tuple[str, str, str]]:
    own = recovery.resolve().relative_to(Path(project.root).resolve()).as_posix() if recovery else None
    return {(f.code, f.where, f.message) for f in validate.errors(validate.check(project))
            if not (f.where == own and f.code in ("interrupted-archive", "interrupted-write"))}


def refresh(root: Path) -> None:
    """Recompile every projection after releasing the write lock."""
    project = compiler.load(root)
    report = analytics.report(project)
    compiler.write(root, project)
    compiled = root / COMPILED_DIR
    for name, text in mermaid.render_all(project, report).items():
        (compiled / name).write_text(text, encoding="utf-8")
    (compiled / "dashboard.html").write_text(
        dashboard.render(project, report, compiler.as_json(project)), encoding="utf-8")


@dataclass
class Plan:
    changes: dict[Path, bytes]
    record_id: str
    source: str


def transaction(root: Path, operation: str, prepare: Callable[[Project], Plan], *, dry_run: bool = False) -> dict:
    """Plan under the lock, verify, commit or restore, then refresh once."""
    root = root.resolve()
    result = None
    try:
        try:
            lock = _lock(root)
        except RespondError as error:
            raise WriteError("locked", "another write is in progress; try again") from error
        try:
            if recover_write(root):
                raise WriteError("interrupted-write", "an interrupted write was rolled back; try again")
            project = compiler.load(root)
            before = _errors(project)
            plan = prepare(project)
            authority = (root / AUTHORITY_DIR).resolve()
            changes = {}
            for path, data in plan.changes.items():
                resolved = path.resolve()
                if authority not in resolved.parents:
                    raise WriteError("invalid-target", "write target is outside the chronicle")
                ensure_mutable(resolved.relative_to(authority).as_posix())
                if resolved in changes:
                    raise WriteError("duplicate-target", "multiple changes resolve to the same chronicle file")
                changes[resolved] = data
            name, anchor = plan.source.split("#", 1)
            target = (root / AUTHORITY_DIR / name).resolve()
            result = {"ok": True, "operation": operation, "recordId": plan.record_id,
                      "source": plan.source, "revision": section(changes[target], anchor).revision,
                      "validation": "passed", "written": False, "refreshed": False}
            if dry_run:
                return {**result, "dryRun": True, "wouldWrite": [
                    {"file": p.relative_to(authority).as_posix(), "bytes": len(data)}
                    for p, data in changes.items()]}
            originals = {p: p.read_bytes() if p.is_file() else None for p in changes}
            recovery = _write_recovery(root, originals, prefix="write")
            try:
                with staged_replacements(root, recovery):
                    for path, data in changes.items():
                        _replace(path, data)
                introduced = _errors(compiler.load(root), recovery) - before
                if introduced:
                    detail = "; ".join(f"{code}: {message}" for code, _, message in sorted(introduced))
                    raise WriteError("validation-failed", f"nothing was written: {detail}")
                recovery.unlink()
                result["written"] = True
            except Exception:
                recover_write(root)
                raise
        finally:
            lock.release()
    except Exception as error:
        if result is not None and result["written"]:
            return {**result, "ok": False,
                    "error": {"code": "lock-release-failed", "message": str(error)}}
        return refusal(operation, error if isinstance(error, WriteError)
                       else WriteError("write-failed", str(error)))
    try:
        refresh(root)
    except Exception as error:
        return {**result, "ok": False, "error": {
            "code": "refresh-failed",
            "message": f"{error}; the write is committed, run compile, graph and dashboard instead of repeating it"}}
    return {**result, "refreshed": True}
