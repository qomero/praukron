"""Record a criterion result without changing its frozen contract (ADR-068)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import mutate
from .model import CRITERION_STATES, Contract, Criterion, Project
from .parse import ParseError, _BULLET, _CRITERION, _EVIDENCE, parse_acceptance

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "criterion": {"type": "string", "minLength": 1, "examples": ["AC-T-ONE-01"]},
        "state": {"type": "string", "enum": CRITERION_STATES, "examples": ["PASS"]},
        "evidence": {"type": "string", "description": "One non-empty line; omit to keep the current evidence"},
        "expectRevision": {"type": "string", "minLength": 1,
                           "description": "16 hex revision of the contract section, from context/explain/retrieve, the last result or a stale-revision refusal",
                           "examples": ["0123456789abcdef"]},
    },
    "required": ["criterion", "state", "expectRevision"],
}
EXAMPLE = {
    "criterion": "AC-T-ONE-01", "state": "PASS",
    "evidence": "python -m unittest discover -s tests",
    "expectRevision": "0123456789abcdef",
}


SCHEMA["description"] = "\n".join(mutate.GUIDANCE["criterion.set"])
mutate.SCHEMAS["criterion.set"] = (SCHEMA, ())


def _criterion_location(text: str, criterion_id: str) -> tuple[int, int, re.Match[str], re.Match[str] | None]:
    """Locate parser-valid state and evidence syntax inside one contract section."""
    bullets = list(_BULLET.finditer(text))
    for index, boundary in enumerate(bullets):
        start = boundary.start()
        end = bullets[index + 1].start() if index + 1 < len(bullets) else len(text)
        found = _CRITERION.match(text[start:end])
        if found is None or found.group("id") != criterion_id:
            continue
        evidence = _EVIDENCE.match(text[start + found.end():end])
        return start, end, found, evidence
    raise mutate.WriteError("criterion-not-found", f"no criterion found for {criterion_id}", "criterion")


def _current_line(data: bytes, criterion_id: str) -> str:
    text = data.decode("utf-8")
    start, _, _, _ = _criterion_location(text, criterion_id)
    return text[start:].splitlines()[0]


def _update_criterion(data: bytes, criterion_id: str, state: str, evidence: str | None) -> bytes:
    """Use parse_acceptance's grammar to replace one state and evidence value."""
    text = data.decode("utf-8")
    start, end, found, evidence_match = _criterion_location(text, criterion_id)
    state_span = found.span("state") if found.group("state") is not None else None
    state_end = start + found.end()
    newline = "\r\n" if "\r\n" in text else "\n"
    edits: list[tuple[int, int, str]] = []

    direct_evidence_start = None
    if evidence_match is not None:
        value_start = start + found.end() + evidence_match.start("value")
        direct_evidence_start = text.rfind("\n", 0, value_start) + 1
    for candidate in re.finditer(r"(?m)^[^\S\r\n]*- Evidence:[^\r\n]*", text[start:end]):
        if start + candidate.start() != direct_evidence_start:
            raise mutate.WriteError(
                "ambiguous-evidence",
                f"criterion has non-direct Evidence line: {candidate.group(0).strip()}",
            )

    def insert_evidence() -> tuple[int, str]:
        """Place evidence after the complete physical line containing the state."""
        line_end = text.find("\n", state_end)
        if line_end == -1:
            return len(text), f"{newline}  - Evidence: {evidence}"
        return line_end + 1, f"  - Evidence: {evidence}{newline}"

    if state_span is None:
        insertion = f" · `{state}`"
        if evidence is not None and evidence_match is None:
            evidence_start, evidence_insertion = insert_evidence()
            if evidence_start == state_end:
                insertion += evidence_insertion
            else:
                edits.append((evidence_start, evidence_start, evidence_insertion))
        edits.append((state_end, state_end, insertion))
    else:
        edits.append((start + state_span[0], start + state_span[1], state))

    if evidence is not None:
        if evidence_match is None:
            if state_span is not None:
                evidence_start, evidence_insertion = insert_evidence()
                edits.append((evidence_start, evidence_start, evidence_insertion))
        else:
            value_start = start + found.end() + evidence_match.start("value")
            value_end = start + found.end() + evidence_match.end("value")
            ending = "\r" if evidence_match.group("value").endswith("\r") else ""
            edits.append((value_start, value_end, evidence + ending))

    updated = text
    for edit_start, edit_end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        updated = updated[:edit_start] + replacement + updated[edit_end:]
    return updated.encode("utf-8")


def _find(project: Project, criterion_id: object) -> tuple[Contract, Criterion] | None:
    if not isinstance(criterion_id, str):
        return None
    for contract in project.contracts.values():
        for criterion in contract.criteria:
            if criterion.id == criterion_id:
                return contract, criterion
    return None


def _contract_section(root: Path, contract: Contract) -> tuple[bytes, mutate.Section]:
    path = root / mutate.AUTHORITY_DIR / contract.source.file
    data = path.read_bytes()
    if not contract.is_global:
        return data, mutate.section(data, contract.source.anchor)
    heading = rb"### " + re.escape(contract.source.anchor.encode("utf-8")) + rb"(?: \xe2\x80\x94 [^\r\n]+)?\r?$"
    found = re.search(rb"(?m)^" + heading, data)
    if found is None:
        raise mutate.WriteError("record-not-found", f"no contract section found for {contract.id}")
    following = re.search(rb"(?m)^#{1,3} ", data[found.end():])
    end = found.end() + following.start() if following else len(data)
    return data, mutate.Section(data[found.start():end], (found.start(), end))


def _parsed_contract(data: bytes, source: str, contract_id: str) -> Contract:
    with tempfile.TemporaryDirectory(prefix="praukron-criterion-") as directory:
        path = Path(directory) / "ACCEPTANCE.md"
        path.write_bytes(data)
        return parse_acceptance(path, source)[contract_id]


def _signature(criterion: Criterion) -> tuple[str, str, str, str, str | None]:
    return (criterion.id, criterion.text, criterion.evidence_class, criterion.state, criterion.evidence)


def _verify(updated: bytes, source: str, contract: Contract, criterion: Criterion,
            state: str, evidence: str | None) -> None:
    """Refuse a byte edit unless it parses exactly as the requested record update."""
    try:
        parsed = _parsed_contract(updated, source, contract.id)
    except (KeyError, ParseError, UnicodeDecodeError) as error:
        raise mutate.WriteError("round-trip-failed", f"criterion update does not parse: {error}") from error
    expected_evidence = criterion.evidence if evidence is None else evidence
    expected = (criterion.id, criterion.text, criterion.evidence_class, state, expected_evidence)
    found = {item.id: item for item in parsed.criteria}
    actual = _signature(found[criterion.id]) if criterion.id in found else None
    if actual != expected:
        raise mutate.WriteError("round-trip-failed", "criterion update did not preserve the requested result")
    original = {item.id: _signature(item) for item in contract.criteria if item.id != criterion.id}
    if {item.id: _signature(item) for item in parsed.criteria if item.id != criterion.id} != original:
        raise mutate.WriteError("round-trip-failed", "criterion update changed another criterion")


def _attach_revision(error: mutate.WriteError, section: mutate.Section | None) -> mutate.WriteError:
    error.details.setdefault("currentRevision", section.revision if section else None)
    return error


def refuse(root: Path, error: mutate.WriteError) -> dict:
    """Report malformed payloads with a revision when they name a criterion."""
    payload = getattr(error, "payload", None)

    def prepare(project: Project) -> mutate.Plan:
        section = None
        try:
            found = _find(project, payload.get("criterion") if isinstance(payload, dict) else None)
            if found:
                _, section = _contract_section(root, found[0])
            raise error
        except mutate.WriteError as refusal:
            raise _attach_revision(refusal, section)

    if not isinstance(payload, dict):
        return mutate.refusal("criterion.set", _attach_revision(error, None))
    return mutate.transaction(root.resolve(), "criterion.set", prepare)


def set_result(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    """Set a criterion state and optional evidence under the shared write lock."""
    root = root.resolve()
    operation = "criterion.set"

    def prepare(project: Project) -> mutate.Plan:
        section = None
        try:
            found = _find(project, payload.get("criterion") if isinstance(payload, dict) else None)
            document, section = _contract_section(root, found[0]) if found else (None, None)
            mutate.validate_payload(payload, SCHEMA)
            if found is None:
                raise mutate.WriteError("criterion-not-found", f"no criterion found for {payload['criterion']}",
                                        "criterion")
            contract, criterion = found
            if contract.is_global:
                raise mutate.WriteError("invariant-criterion",
                                        "inherited invariant criteria are not set through this command")
            mutate.ensure_mutable(contract.source)
            expected = payload["expectRevision"]
            if not re.fullmatch(r"[0-9a-f]{16}", expected):
                raise mutate.WriteError("invalid-revision",
                                        "expectRevision must be 16 lowercase hexadecimal characters",
                                        "expectRevision")
            if section is None:
                raise mutate.WriteError("record-not-found", f"no contract section found for {contract.id}")
            if section.revision != expected:
                raise mutate.WriteError(
                    "stale-revision", "criterion changed; retry with currentRevision (currentCriterion is the current line)",
                    "expectRevision", details={"currentCriterion": _current_line(section.data, criterion.id)},
                )
            evidence = payload.get("evidence")
            if evidence is not None:
                if "\r" in evidence or "\n" in evidence or not evidence.strip():
                    raise mutate.WriteError("invalid-evidence", "evidence must be one non-empty line", "evidence")
                evidence = evidence.strip()
            updated_section = _update_criterion(section.data, criterion.id, payload["state"], evidence)
            path = root / mutate.AUTHORITY_DIR / contract.source.file
            changed = document[:section.span[0]] + updated_section + document[section.span[1]:]
            _verify(changed, contract.source.file, contract, criterion, payload["state"], evidence)
            return mutate.Plan({path: changed}, criterion.id,
                               f"{contract.source.file}#{contract.source.anchor}")
        except mutate.WriteError as error:
            raise _attach_revision(error, section)

    return mutate.transaction(root, operation, prepare, dry_run=dry_run)
