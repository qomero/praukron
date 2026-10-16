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
from contextvars import ContextVar
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


FAILED_ACCEPTANCE_GUIDANCE = (
    "Leave the task open and report the failing criteria until actual corrective work and supporting "
    "evidence show they hold. A request to mark DONE is not evidence; do not relabel FAIL as PASS "
    "just to close the task, including through separate writes."
)

# Agent-facing guidance for the write commands: WHEN / WHEN NOT / RETURNS / EXAMPLE.
# Shown by `--help` and carried in each `--schema` output.
GUIDANCE = {
    "batch.apply": (
        "WHEN: several structured writes belong together, such as a claim, its criteria, DONE and a journal "
        "entry; one call, one lock, one validation and one refresh instead of one per write.",
        "WHEN NOT: generic edits, owner-only acts or frozen contracts; each operation keeps its command's rules. "
        + FAILED_ACCEPTANCE_GUIDANCE,
        "RETURNS: each operation's result in order and the final revision of every section written; a refusal "
        "names the operation's index and op, and nothing is written. Each operation is a command's payload plus "
        "\"op\" (\"criterion set\"). Give every operation on one section the revision read before the batch; "
        "$previous only for a section an earlier operation created. A committed refresh failure needs only "
        "compile, graph and dashboard.",
        "EXAMPLE: praukron batch apply --input - < done.json",
    ),
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
        "invariant criterion. " + FAILED_ACCEPTANCE_GUIDANCE,
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
        "WHEN: claim a task, change its status, owner or validation (UNTESTED, SYNTHETIC, AI_REVIEWED; ADR-074), "
        "record its evidence, or link it to further governing ADRs with addGovernedBy (ADR-074).",
        "WHEN NOT: to close an Authority: owner task, set HUMAN_VERIFIED, remove or reorder Governed by "
        "entries, or change Module, Dependencies, AC or Authority; DONE needs every criterion PASS and evidence. "
        + FAILED_ACCEPTANCE_GUIDANCE,
        "RETURNS: one JSON line with ok, recordId, source, revision (and alreadyGoverned when addGovernedBy "
        "named ids already listed, which are skipped). A stale expectRevision is refused with "
        "currentRevision and the current section.",
        "EXAMPLE: echo '{\"id\":\"T-ONE\",\"expectRevision\":\"<16 hex>\",\"set\":{\"status\":\"WIP\","
        "\"owner\":\"me\"}}' | praukron task update --input -",
    ),
    "acr.add": (
        "WHEN: a criterion of a WIP or DONE task's frozen contract is wrong; record an Acceptance Change "
        "Request for the owner to decide (ADR-076).",
        "WHEN NOT: for a task that is not WIP or DONE (edit its contract directly), an inherited invariant, or to "
        "decide a request; the criterion itself never changes, and one request per criterion stays undecided.",
        "RETURNS: one JSON line with ok, recordId (the ACR id the tool assigns), source, revision. A second "
        "undecided request for the same criterion is refused as duplicate-request, naming the open one.",
        "EXAMPLE: echo '{\"task\":\"T-ONE\",\"criterion\":\"AC-T-ONE-01\",\"proposed\":\"Given x, When y, "
        "Then z.\",\"reason\":\"Untestable as written.\"}' | praukron acr add --input -",
    ),
}

# The next valid step for refusals whose message does not already carry one.
NEXT_STEP = {
    "wrong-type": "send a JSON object whose fields have the types `example` shows",
    "unknown-field": "remove it; `example` is a minimal accepted payload and `--schema` lists every field",
    "misplaced-field": "move it to where `example` shows it",
    "missing-field": "add it; `example` is a minimal accepted payload",
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
    "invalid-revision": "use the 16 hex revision from context, explain, retrieve or the last result; only for a record not yet read, send sixteen zeros and use currentRevision",
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
    "unfrozen-contract": "edit the criterion in ACCEPTANCE.md directly; no request is needed before WIP",
}


# Each command registers its payload schema so a refusal can show an accepted payload,
# with the dotted paths of the optional fields that payload needs beyond `required`.
SCHEMAS: dict[str, tuple[dict, tuple[str, ...]]] = {}
# Refusals about the payload's shape, which carry a minimal accepted payload.
SHAPE_CODES = {"wrong-type", "unknown-field", "misplaced-field", "missing-field", "invalid-json",
               "invalid-contract", "empty-set"}


def example(schema: dict, paths: tuple[str, ...] = ()) -> dict:
    """The smallest payload the schema accepts, built from the schema alone.

    It holds the required fields and each dotted path in `paths`, valued by the
    field's `examples`, its first enum value, or an empty value of its type.
    """
    properties = schema["properties"]
    nested: dict[str, list[str]] = {}
    for path in paths:
        head, _, rest = path.partition(".")
        if head in properties:
            nested.setdefault(head, []).extend([rest] if rest else [])
    names = set(schema.get("required", [])) | set(nested)
    return {name: _example_value(spec, tuple(nested.get(name, ())))
            for name, spec in properties.items() if name in names}


def _example_value(spec: dict, paths: tuple[str, ...]):
    if spec["type"] == "object" and "properties" in spec:
        return example(spec, paths)
    if "examples" in spec:
        return spec["examples"][0]
    if "enum" in spec:
        return spec["enum"][0]
    if spec.get("format") == "date":
        return dt.date.today().isoformat()
    return {"object": {}, "array": [], "boolean": False, "integer": 0, "number": 0}.get(spec["type"], "")


def _nested_path(schema: dict, name: str) -> str | None:
    """Where an unknown top-level field is defined further down, if anywhere."""
    for key, spec in schema["properties"].items():
        if spec.get("type") == "object" and "properties" in spec:
            if name in spec["properties"]:
                return f"{key}.{name}"
            deeper = _nested_path(spec, name)
            if deeper:
                return f"{key}.{deeper}"
    return None


def refusal(operation: str, error: WriteError) -> dict:
    step = NEXT_STEP.get(error.code)
    detail = {"code": error.code, "message": f"{error}; {step}" if step else str(error)}
    if error.field is not None:
        detail["field"] = error.field
    detail.update(error.details)
    if error.code in SHAPE_CODES and operation in SCHEMAS:
        schema, needed = SCHEMAS[operation]
        expected = detail.get("expected")
        detail["example"] = example(schema, (*needed, *([expected] if expected else [])))
    return {"ok": False, "operation": operation, "error": detail, "written": False}


def validate_payload(payload: object, schema: dict) -> dict:
    """Check the small JSON Schema subset used by command payloads."""
    if not isinstance(payload, dict):
        raise WriteError("wrong-type", "payload must be a JSON object")
    properties = schema["properties"]
    for name in payload:
        if name not in properties:
            nested = _nested_path(schema, name)
            if nested:
                parent = nested.rsplit(".", 1)[0]
                raise WriteError("misplaced-field", f"{name} belongs inside {parent}, not beside it", name,
                                 {"expected": nested})
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


_planning = ContextVar("structured_write_planning", default=None)


@contextmanager
def planning(overlay):
    """Route existing operation planners through one isolated batch overlay."""
    token = _planning.set(overlay)
    try:
        yield
    finally:
        _planning.reset(token)


def read_bytes(path: Path) -> bytes:
    overlay = _planning.get()
    return overlay.read_bytes(path) if overlay is not None else path.read_bytes()


def is_file(path: Path) -> bool:
    overlay = _planning.get()
    return overlay.is_file(path) if overlay is not None else path.is_file()


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
    """Find a full journal heading or an ID, retaining exact byte offsets.

    Level two comes first; level three holds records nested in a log, such as ACRs.
    """
    for level in (2, 3):
        for match in re.finditer(rb"(?m)^" + b"#" * level + rb" ([^\r\n]+)\r?$", data):
            heading = match.group(1).decode("utf-8").strip()
            parsed_id = re.split(r": | — ", heading, maxsplit=1)[0]
            if heading != record_id and parsed_id != record_id:
                continue
            following = re.search(rb"(?m)^#{1,%d} " % level, data[match.end():])
            end = match.end() + following.start() if following else len(data)
            return Section(data[match.start():end], (match.start(), end))
    raise WriteError("record-not-found", f"no '## ' section found for {record_id}")


def record_revision(root: Path, source: Source, *, data: bytes | None = None) -> str:
    """Hash exact section bytes, optionally from the snapshot that supplied content."""
    if data is None:
        data = read_bytes(root / AUTHORITY_DIR / source.file)
    return section(data, source.anchor).revision


def working_section(root: Path, source: Source) -> Section:
    ensure_mutable(source)
    return section(read_bytes(root / AUTHORITY_DIR / source.file), source.anchor)


def ensure_mutable(source: Source | str) -> None:
    name = source.file if isinstance(source, Source) else source
    if ARCHIVE_DIR in PurePosixPath(name.replace("\\", "/")).parts:
        raise WriteError("archived-record", f"archived records are immutable: {name}")


def id_exists(project: Project, record_id: str) -> bool:
    """The loader already merges working records and every archive."""
    groups = (project.phases, project.modules, project.tasks, project.contracts.values(),
              project.gates, project.milestones, project.decisions, project.events,
              project.debts, project.assumptions, project.responses, project.conventions,
              project.briefs)
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
        _discard_recovery(record)
    return True


def _discard_recovery(record: Path) -> None:
    """Remove a finished record, then whatever its own write created and nothing else.

    A committed write so leaves nothing new for git status, while a recovery
    directory or ignore file that existed before the write is kept.
    """
    try:
        created = set(json.loads(record.read_text(encoding="utf-8")).get("created", []))
    except (OSError, ValueError, AttributeError):
        created = set()
    record.unlink()
    directory = record.parent
    ignore = directory / ".gitignore"
    if ("ignore" in created and {path.name for path in directory.iterdir()} == {".gitignore"}
            and ignore.read_bytes() == _IGNORE):
        ignore.unlink()
    if "directory" in created and not any(directory.iterdir()):
        directory.rmdir()


recover_archive = recover_write
_IGNORE = b"*\n"


def _write_recovery(root: Path, originals: dict[Path, bytes | None], prefix: str = "archive", *,
                    directories: list[Path] | tuple[Path, ...] = ()) -> Path:
    """Persist originals before replacing any authoritative file."""
    root = root.resolve()
    directory = root / RECOVERY_DIR
    # What this write creates, so discarding its record removes only that.
    created = [] if directory.is_dir() else ["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.is_file():
        ignore.write_bytes(_IGNORE)
        created.append("ignore")
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
    if created:
        payload["created"] = created
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
    results: list[dict] | None = None
    revisions: dict[str, str] | None = None
    proposed: Project | None = None


def transaction(root: Path, operation: str, prepare: Callable[[Project], Plan], *, dry_run: bool = False) -> dict:
    """Plan under the lock, verify, commit or restore, then refresh once."""
    root = root.resolve()
    overlay = _planning.get()
    if overlay is not None:
        return overlay.transaction(root, operation, prepare)
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
            if plan.results is None:
                name, anchor = plan.source.split("#", 1)
                target = (root / AUTHORITY_DIR / name).resolve()
                result = {"ok": True, "operation": operation, "recordId": plan.record_id,
                          "source": plan.source, "revision": section(changes[target], anchor).revision,
                          "validation": "passed", "written": False, "refreshed": False}
            else:
                result = {"ok": True, "operation": operation, "results": plan.results,
                          "revisions": plan.revisions, "validation": "passed",
                          "written": False, "refreshed": False}
            if plan.proposed is not None:
                introduced = _errors(plan.proposed) - before
                if introduced:
                    detail = "; ".join(f"{code}: {message}" for code, _, message in sorted(introduced))
                    raise WriteError("validation-failed", f"nothing was written: {detail}",
                                     details={"stage": "validation"})
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
                introduced = (_errors(compiler.load(root), recovery) - before
                              if plan.proposed is None else set())
                if introduced:
                    detail = "; ".join(f"{code}: {message}" for code, _, message in sorted(introduced))
                    raise WriteError("validation-failed", f"nothing was written: {detail}")
                _discard_recovery(recovery)
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
        failure = error if isinstance(error, WriteError) else WriteError("write-failed", str(error))
        if result is not None and "results" in result:
            failure.details.setdefault("stage", "persistence")
        return refusal(operation, failure)
    try:
        refresh(root)
    except Exception as error:
        return {**result, "ok": False, "error": {
            "code": "refresh-failed",
            "message": f"{error}; the write is committed, run compile, graph and dashboard instead of repeating it"}}
    return {**result, "refreshed": True}
