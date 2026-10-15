"""Record a criterion result without changing its frozen contract (ADR-068)."""

from __future__ import annotations

import re
from pathlib import Path

from . import mutate
from .model import CRITERION_STATES, Contract, Project

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "criterion": {"type": "string", "minLength": 1},
        "state": {"type": "string", "enum": CRITERION_STATES},
        "evidence": {"type": "string"},
        "expectRevision": {"type": "string", "minLength": 1},
    },
    "required": ["criterion", "state", "expectRevision"],
}
EXAMPLE = {
    "criterion": "AC-T-ONE-01", "state": "PASS",
    "evidence": "python -m unittest discover -s tests",
    "expectRevision": "0123456789abcdef",
}


def _criterion_span(data: bytes, criterion_id: str) -> tuple[int, int]:
    start_match = re.search(
        rb"(?m)^- `" + re.escape(criterion_id.encode("utf-8")) + rb"` \xe2\x80\x94", data
    )
    if start_match is None:
        raise mutate.WriteError("criterion-not-found", f"no criterion found for {criterion_id}", "criterion")
    following = re.search(rb"(?m)^- `AC-[A-Za-z0-9.\-]+` \xe2\x80\x94", data[start_match.end():])
    end = start_match.end() + following.start() if following else len(data)
    return start_match.start(), end


def _current_line(data: bytes, criterion_id: str) -> str:
    start, _ = _criterion_span(data, criterion_id)
    return data[start:].splitlines()[0].decode("utf-8")


def _update_criterion(data: bytes, criterion_id: str, state: str, evidence: str | None) -> bytes:
    """Change one state token and, when supplied, its one-line evidence."""
    start, end = _criterion_span(data, criterion_id)
    criterion = data[start:end]
    state_match = re.search(
        rb"`(?:TEST|MUTATION|INSPECTION|RUNTIME|MANUAL)`[ \t]*"
        rb"(?:\xc2\xb7[ \t]*`(?P<state>PASS|FAIL|NOT_RUN)`)?",
        criterion,
    )
    if state_match is None:
        raise mutate.WriteError("criterion-malformed", f"criterion has no evidence class: {criterion_id}")
    state_token = state_match.group("state")
    if state_token is None:
        state_change = (start + state_match.end(), start + state_match.end(),
                        f" · `{state}`".encode("utf-8"))
    else:
        state_change = (start + state_match.start("state"), start + state_match.end("state"),
                        state.encode("utf-8"))

    changes = [state_change]
    if evidence is not None:
        line_end = criterion.find(b"\n", state_match.end())
        if line_end < 0:
            line_end = len(criterion)
            newline = b"\r\n" if b"\r\n" in data else b"\n"
            evidence_start = start + line_end
            changes.append((evidence_start, evidence_start, newline + b"  - Evidence: "
                            + evidence.encode("utf-8")))
        else:
            line_end += 1
            newline = b"\r\n" if criterion[line_end - 2:line_end] == b"\r\n" else b"\n"
            evidence_start = start + line_end
            existing = re.match(rb"  - Evidence: [^\r\n]*(?:\r?\n)?", criterion[line_end:])
            replacement = b"  - Evidence: " + evidence.encode("utf-8")
            if existing is None:
                changes.append((evidence_start, evidence_start, replacement + newline))
            else:
                old = existing.group(0)
                ending = newline if old.endswith((b"\n", b"\r")) else b""
                changes.append((evidence_start, evidence_start + len(old), replacement + ending))

    updated = data
    for change_start, change_end, replacement in sorted(changes, reverse=True):
        updated = updated[:change_start] + replacement + updated[change_end:]
    return updated


def set_result(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    """Set a criterion state and optional evidence under the shared write lock."""
    root = root.resolve()
    operation = "criterion.set"
    try:
        mutate.validate_payload(payload, SCHEMA)
        expected = payload["expectRevision"]
        if not re.fullmatch(r"[0-9a-f]{16}", expected):
            raise mutate.WriteError("invalid-revision", "expectRevision must be 16 lowercase hexadecimal characters",
                                    "expectRevision")
        evidence = payload.get("evidence")
        if evidence is not None:
            if "\r" in evidence or "\n" in evidence or not evidence.strip():
                raise mutate.WriteError("invalid-evidence", "evidence must be one non-empty line", "evidence")
            evidence = evidence.strip()

        def prepare(project: Project) -> mutate.Plan:
            found: tuple[Contract, object] | None = None
            for contract in project.contracts.values():
                for criterion in contract.criteria:
                    if criterion.id == payload["criterion"]:
                        found = contract, criterion
                        break
                if found is not None:
                    break
            if found is None:
                raise mutate.WriteError("criterion-not-found", f"no criterion found for {payload['criterion']}",
                                        "criterion")
            contract, criterion = found
            current = mutate.working_section(root, contract.source)
            if current.revision != expected:
                raise mutate.WriteError(
                    "stale-revision", "criterion changed; fetch the current contract before retrying",
                    "expectRevision", details={"currentRevision": current.revision,
                                                "currentCriterion": _current_line(current.data, criterion.id)},
                )
            updated_section = _update_criterion(current.data, criterion.id, payload["state"], evidence)
            path = root / mutate.AUTHORITY_DIR / contract.source.file
            updated = path.read_bytes()
            changed = updated[:current.span[0]] + updated_section + updated[current.span[1]:]
            return mutate.Plan({path: changed}, criterion.id,
                               f"{contract.source.file}#{contract.source.anchor}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)
