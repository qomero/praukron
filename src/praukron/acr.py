"""Record an Acceptance Change Request without changing the frozen criterion (ADR-076)."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from . import contract, mutate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR
from .model import EVIDENCE_CLASSES, Project
from .parse import parse_acceptance

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "task": {"type": "string", "description": "A WIP or DONE task", "examples": ["T-ONE"]},
        "criterion": {"type": "string", "description": "A criterion of the task's own contract",
                      "examples": ["AC-T-ONE-01"]},
        "proposed": {"type": "string", "minLength": 1,
                     "description": "The proposed criterion text: one line, single spaces, no evidence-class token",
                     "examples": ["Given a request, When it runs, Then it succeeds within a second."]},
        "reason": {"type": "string", "minLength": 1, "description": "One line",
                   "examples": ["The original wording cannot be tested."]},
        "impact": {"type": "string", "minLength": 1, "description": "One line; recorded as 'not stated' when omitted"},
        "class": {"type": "string", "enum": list(EVIDENCE_CLASSES),
                  "description": "Proposed evidence class; defaults to the current one"},
        "title": {"type": "string", "minLength": 1, "description": "One line, added to the heading"},
    },
    "required": ["task", "criterion", "proposed", "reason"],
}
EXAMPLE = {"task": "T-ONE", "criterion": "AC-T-ONE-01",
           "proposed": "Given a request, When it runs, Then it succeeds within a second.",
           "reason": "The original wording cannot be tested."}

SCHEMA["description"] = "\n".join(mutate.GUIDANCE["acr.add"])
mutate.SCHEMAS["acr.add"] = (SCHEMA, ())

LOG = "Acceptance Change Requests"
PENDING = "PENDING — awaiting the owner"
DECIDED = ("ACCEPTED", "REJECTED", "WITHDRAWN")
_HEADING = re.compile(r"(?m)^### (ACR-([0-9]+))\b[^\r\n]*\r?$")


def requests(text: str) -> list[dict]:
    """The ACR sections of one ACCEPTANCE.md: id, number, criterion and decision."""
    found = []
    for heading in _HEADING.finditer(text):
        following = re.compile(r"(?m)^#{1,3} ").search(text, heading.end())
        body = text[heading.end():following.start() if following else len(text)]
        field = lambda name: (re.search(rf"(?m)^- {name}: ?(.*)$", body) or [None, ""])[1].strip()  # noqa: E731
        found.append({"id": heading.group(1), "number": int(heading.group(2)),
                      "criterion": field("Criterion").strip("`"), "decision": field("Decision")})
    return found


def _log_span(text: str) -> tuple[int, int] | None:
    heading = re.search(rf"(?m)^## {LOG}[ \t]*\r?$", text)
    if heading is None:
        return None
    following = re.compile(r"(?m)^#{1,2} ").search(text, heading.end())
    return heading.start(), following.start() if following else len(text)


def insert(original: bytes, entry: str) -> bytes:
    """Append the entry to the end of the log section, creating the section when absent."""
    newline = "\r\n" if b"\r\n" in original else "\n"
    text = original.decode("utf-8")
    entry = entry.replace("\n", newline)
    span = _log_span(text)
    if span is None:
        padding = "" if text.endswith(newline * 2) else newline if text.endswith(newline) else newline * 2
        return (text + padding + f"## {LOG}{newline}{newline}" + entry).encode("utf-8")
    start, end = span
    section = text[start:end]
    # Content ends before trailing blank lines and a closing `---` rule.
    content = re.match(r"(?s)(.*?)((?:\s*^---[ \t]*\r?$)?\s*)\Z", section, re.M)
    body, tail = content.group(1), content.group(2)
    lines = body.split(newline, 1)
    if len(lines) == 2 and lines[1].strip() == "None.":
        body = lines[0] + newline
    else:
        body += newline
    return (text[:start] + body + newline + entry.rstrip(newline) + (tail or newline) + text[end:]).encode("utf-8")


def render(number: int, payload: dict, task_id: str, current: str, current_class: str) -> tuple[str, str]:
    record_id = f"ACR-{number:03d}"
    title = f" {payload['title']}" if payload.get("title") else ""
    lines = [
        f"### {record_id} — {payload['criterion']}{title}",
        f"- Task: {task_id}",
        f"- Criterion: `{payload['criterion']}`",
        f"- Current contract: {current} `{current_class}`",
        f"- Proposed contract: {payload['proposed']} `{payload.get('class', current_class)}`",
        f"- Reason: {payload['reason']}",
        f"- Impact: {payload.get('impact', 'not stated')}",
        f"- Decision: {PENDING}",
    ]
    return record_id, "\n".join(lines) + "\n"


def add(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "acr.add"
    try:
        mutate.validate_payload(payload, SCHEMA)
        contract.identifier(payload["task"], "T-", "task")
        contract.identifier(payload["criterion"], "AC-", "criterion")
        for field in ("proposed", "reason", "impact", "title"):
            if field in payload:
                contract.single_line(payload[field], field)
        if (payload["proposed"] != " ".join(payload["proposed"].split())
                or re.search(r"`(?:" + "|".join(EVIDENCE_CLASSES) + r")`", payload["proposed"])):
            raise mutate.WriteError("invalid-value", "proposed must be one paragraph without evidence-class tokens",
                                    "proposed")

        def prepare(project: Project) -> mutate.Plan:
            task = next((t for t in project.tasks if t.id == payload["task"]), None)
            if task is None:
                raise mutate.WriteError("record-not-found", f"no task named {payload['task']}", "task")
            mutate.ensure_mutable(task.source)
            if task.status not in ("WIP", "DONE"):
                raise mutate.WriteError("unfrozen-contract",
                                        f"{task.id} is {task.status}, so its contract is not frozen", "task")
            owned = project.contracts.get(task.contract or "")
            if owned is None:
                raise mutate.WriteError("criterion-not-found", f"{task.id} names no contract", "criterion")
            mutate.ensure_mutable(owned.source)
            criterion = next((c for c in owned.criteria if c.id == payload["criterion"]), None)
            if criterion is None:
                inherited = any(payload["criterion"] in {c.id for c in project.contracts[i].criteria}
                                for i in owned.inherits if i in project.contracts)
                if inherited:
                    raise mutate.WriteError("invariant-criterion",
                                            f"{payload['criterion']} is inherited by {owned.id}, not its own", "criterion")
                raise mutate.WriteError("criterion-not-found",
                                        f"{payload['criterion']} is not a criterion of {owned.id}", "criterion")
            authority = root / AUTHORITY_DIR
            path = authority / "ACCEPTANCE.md"
            original = path.read_bytes()
            known = requests(original.decode("utf-8"))
            for archived in sorted((authority / ARCHIVE_DIR).glob("*/ACCEPTANCE.md")):
                known += requests(archived.read_text(encoding="utf-8"))
            for request in known:
                if request["criterion"] == criterion.id and not request["decision"].startswith(DECIDED):
                    raise mutate.WriteError("duplicate-request",
                                            f"{request['id']} for {criterion.id} is still undecided; "
                                            "wait for the owner's decision, or add to that request by hand",
                                            "criterion", {"openRequest": request["id"]})
            number = max((request["number"] for request in known), default=0) + 1
            record_id, entry = render(number, payload, task.id, criterion.text, criterion.evidence_class)
            updated = insert(original, entry)
            _same_contracts(original, updated)
            if record_id not in {request["id"] for request in requests(updated.decode("utf-8"))}:
                raise mutate.WriteError("round-trip-failed", "the request did not read back")
            return mutate.Plan({path: updated}, record_id, f"ACCEPTANCE.md#{record_id}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)


def _same_contracts(before: bytes, after: bytes) -> None:
    """The request is recorded beside the contracts, never in them."""
    parsed = []
    with tempfile.TemporaryDirectory() as directory:
        for number, data in enumerate((before, after)):
            path = Path(directory) / f"{number}.md"
            path.write_bytes(data)
            parsed.append(parse_acceptance(path, "ACCEPTANCE.md"))
    if parsed[0] != parsed[1]:
        raise mutate.WriteError("round-trip-failed", "recording the request would change a contract")


def command(root: Path, args) -> int:
    if args.schema:
        result = {"schema": SCHEMA, "example": EXAMPLE}
    else:
        try:
            result = add(root, mutate.load_payload(args.input, SCHEMA), dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("acr.add", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1
