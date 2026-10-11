"""Parsers for the authored documents in the chronicle.

Every parser is total: it either returns typed objects or raises ParseError
naming the file and the anchor at fault. Parsers never repair input and never
write.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import (
    Assumption,
    Contract,
    Criterion,
    Decision,
    Gate,
    Milestone,
    Module,
    Phase,
    Response,
    Schedule,
    Source,
    Task,
    TechDebt,
    Thesis,
    TraceEvent,
)


class ParseError(Exception):
    def __init__(self, file: str, anchor: str, message: str) -> None:
        super().__init__(f"{file} [{anchor}]: {message}")
        self.file = file
        self.anchor = anchor


def _fields(body: str) -> dict[str, str]:
    """Read `- Key: value` lines, joining wrapped continuation lines."""
    fields: dict[str, str] = {}
    key = None
    for line in body.splitlines():
        match = re.match(r"- ([A-Za-z][A-Za-z ]*): ?(.*)", line)
        if match:
            key = match.group(1).strip()
            fields[key] = match.group(2).strip()
        elif key and line.startswith("  ") and line.strip():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
        elif not line.strip():
            key = None
    return fields


def _list(value: str | None) -> list[str]:
    if not value or value.strip().lower() in {"none", "—", "-"}:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _named(value: str | None) -> str | None:
    """A free-text field that may be left empty or written as `none`."""
    if not value or value.strip().lower() in {"none", "—", "-"}:
        return None
    return value.strip()


def _sections(text: str, level: str) -> list[tuple[str, str]]:
    """Split Markdown into (heading, body) pairs at one heading level.

    A section ends at the next heading of the same or a higher level, so a
    nested subsection never swallows what follows its parent.
    """
    depth = len(level)
    wanted = re.compile(rf"(?m)^{level} (.+)$")
    boundary = re.compile(r"(?m)^#{1,%d} " % depth)
    out: list[tuple[str, str]] = []
    for match in wanted.finditer(text):
        following = boundary.search(text, match.end())
        end = following.start() if following else len(text)
        out.append((match.group(1).strip(), text[match.end() : end]))
    return out


def parse_thesis(path: Path) -> Thesis:
    """THESIS.md: one `Statement:` and an optional `Source:` (ADR-035)."""
    fields = _fields(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return Thesis(
        statement=fields.get("Statement", "").strip(),
        reference=fields.get("Source") or None,
        source=Source(path.name, "Product thesis"),
    )


def parse_modules(path: Path) -> list[Module]:
    """MODULES.md: `## M-<ID> — <name>` with `Phase:` and `Outcome:`."""
    if not path.is_file():
        return []
    modules: list[Module] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(M-[A-Za-z0-9.\-]+) — (.+)", heading)
        if not match:
            raise ParseError(path.name, heading, "module heading must read '## <ID> — <name>'")
        module_id = match.group(1)
        fields = _fields(body)
        for required in ("Phase", "Outcome"):
            if required not in fields:
                raise ParseError(path.name, module_id, f"missing required field '{required}'")
        modules.append(Module(
            id=module_id, name=match.group(2).strip(), phase=fields["Phase"],
            outcome=fields["Outcome"], source=Source(path.name, module_id),
        ))
    return modules


def parse_tasks(path: Path, modules: list[Module] | None = None) -> list[Task]:
    """A current task names its module and takes its phase from it; a legacy
    task names its phase directly (ADR-036). Neither form invents the other."""
    phases = {module.id: module.phase for module in modules or []}
    name = path.name
    tasks: list[Task] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(T-[A-Za-z0-9.\-]+): (.+)", heading)
        if not match:
            raise ParseError(name, heading, "task heading must read '## <ID>: <title>'")
        task_id, title = match.group(1), match.group(2)
        fields = _fields(body)
        for required in ("Status", "Validation", "Dependencies"):
            if required not in fields:
                raise ParseError(name, task_id, f"missing required field '{required}'")
        module_id = fields.get("Module") or None
        legacy_phase = fields.get("Phase") or None
        if not module_id and not legacy_phase:
            raise ParseError(name, task_id, "missing required field 'Module'")
        schedule = Schedule()
        if "Schedule" in fields:
            for part in fields["Schedule"].split():
                key, _, value = part.partition("=")
                if key not in {"start", "end", "estimate"} or not value:
                    raise ParseError(
                        name, task_id, f"schedule expects start=, end=, or estimate=, got '{part}'"
                    )
                setattr(schedule, key, value)
        tasks.append(
            Task(
                id=task_id,
                title=title,
                phase=phases.get(module_id or "", legacy_phase or ""),
                status=fields["Status"],
                validation=fields["Validation"],
                dependencies=_list(fields["Dependencies"]),
                owner=fields.get("Owner") or None,
                claimed=fields.get("Claimed") or None,
                contract=fields.get("AC") or None,
                evidence=(fields.get("Evidence") or "").strip("—").strip() or None,
                decisions=_list(fields.get("Governed by")),
                schedule=schedule,
                source=Source(name, task_id),
                declared_domain=(fields.get("Domain") or "").strip().lower() or None,
                files=_list(fields.get("Files")),
                symbols=_list(fields.get("Symbols")),
                module=module_id,
                legacy_phase=legacy_phase,
                authority=(fields.get("Authority") or "").strip().lower() or None,
            )
        )
    return tasks


_BULLET = re.compile(r"(?m)^(?=- `AC-[A-Za-z0-9.\-]+` — )")
_CRITERION = re.compile(
    r"- `(?P<id>AC-[A-Za-z0-9.\-]+)` — (?P<text>.*?)"
    r"`(?P<cls>TEST|MUTATION|INSPECTION|RUNTIME|MANUAL)`"
    r"(?:\s*·\s*`(?P<state>PASS|FAIL|NOT_RUN)`)?",
    re.S,
)


def parse_acceptance(path: Path) -> dict[str, Contract]:
    name = path.name
    text = path.read_text(encoding="utf-8")
    contracts: dict[str, Contract] = {}

    # Task contracts are '## AC-...'; inherited invariants are '### AC-GLOBAL-...'.
    sections = [(h, b, "##") for h, b in _sections(text, "##")]
    sections += [(h, b, "###") for h, b in _sections(text, "###")]

    for heading, body, _level in sections:
        match = re.match(r"(AC-[A-Za-z0-9.\-]+)(?: — (.+))?$", heading)
        if not match:
            continue
        contract_id, title = match.group(1), match.group(2) or heading
        if contract_id in contracts:
            raise ParseError(name, contract_id, "duplicate contract")
        inherits: list[str] = []
        inherit_line = re.search(r"(?m)^Inherits: (.+)$", body)
        if inherit_line:
            inherits = re.findall(r"`(AC-[A-Za-z0-9.\-]+)`", inherit_line.group(1))
        criteria: list[Criterion] = []
        seen: set[str] = set()
        # One bullet at a time. Matching across the whole body would let a
        # criterion that is missing its evidence class swallow the criteria
        # after it, quietly weakening the contract.
        for bullet in _BULLET.split(body)[1:]:
            found = _CRITERION.match(bullet)
            if not found:
                broken = re.match(r"- `(AC-[A-Za-z0-9.\-]+)`", bullet)
                raise ParseError(
                    name,
                    broken.group(1) if broken else contract_id,
                    "criterion has no evidence class; expected one of "
                    "TEST, MUTATION, INSPECTION, RUNTIME, MANUAL",
                )
            criterion_id = found.group("id")
            if criterion_id in seen:
                raise ParseError(name, criterion_id, "duplicate criterion")
            seen.add(criterion_id)
            evidence = re.match(
                r"\s*\n\s+- Evidence: (.+)", bullet[found.end() :]
            )
            criteria.append(
                Criterion(
                    id=criterion_id,
                    text=" ".join(found.group("text").split()),
                    evidence_class=found.group("cls"),
                    state=found.group("state") or "NOT_RUN",
                    evidence=evidence.group(1).strip() if evidence else None,
                    source=Source(name, criterion_id),
                )
            )
        note = re.search(r"(?m)^Evidence, [^:]+: (.+(?:\n(?!\n)[^\n]+)*)", body)
        contracts[contract_id] = Contract(
            id=contract_id,
            title=title,
            inherits=inherits,
            criteria=criteria,
            evidence=" ".join(note.group(1).split()) if note else None,
            source=Source(name, contract_id),
        )
    return contracts


def _block(body: str, label: str) -> str:
    """Read a labelled block in either authored form.

    Phases write the label on its own line and the value beneath it; gates write
    `Label: value` inline. Both are natural to read, so both are accepted.
    """
    inline = re.search(rf"(?m)^{label}: (.+)$", body)
    if inline:
        return inline.group(1).strip()
    match = re.search(rf"(?m)^{label}:\n((?:(?!^[A-Z][A-Za-z ]*:)[^\n]*\n?)*)", body)
    return match.group(1).strip() if match else ""


def _bullets(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [re.sub(r"^- ", "", line) for line in lines]


def parse_project_name(path: Path) -> str | None:
    """The project's own name for itself, if it authored one.

    Without this the name came from whatever directory the repository happened
    to be cloned into, so the same authority compiled to different output in
    two checkouts. A project fact belongs in an authored document, not in the
    filesystem.
    """
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            break
        stripped = line.strip()
        if stripped.lower().startswith("project:"):
            named = stripped.split(":", 1)[1].strip()
            return named or None
    return None


def parse_phases(path: Path) -> tuple[list[Phase], list[Gate], list[Milestone]]:
    name = path.name
    text = path.read_text(encoding="utf-8")
    phases: list[Phase] = []
    gates: list[Gate] = []
    milestones: list[Milestone] = []

    for heading, body in _sections(text, "##"):
        phase_match = re.match(r"(P[0-9]+) — (.+)", heading)
        gate_match = re.match(r"Gate ([A-Za-z0-9\-]+)(?: — (.+))?", heading)
        if phase_match:
            phase_id = phase_match.group(1)
            status = _block(body, "Status") or ""
            if not status:
                raise ParseError(name, phase_id, "phase has no Status block")
            authority = _block(body, "Exit authority")
            phases.append(
                Phase(
                    id=phase_id,
                    name=phase_match.group(2).strip(),
                    outcome=" ".join(_block(body, "Outcome").split()),
                    entry=_bullets(_block(body, "Entry")),
                    exit=_bullets(_block(body, "Exit")),
                    exit_authority=authority.split()[0] if authority else None,
                    status=status.split()[0],
                    source=Source(name, phase_id),
                )
            )
        elif gate_match:
            gate_id = f"Gate {gate_match.group(1)}"
            status = _block(body, "Status")
            if not status:
                raise ParseError(name, gate_id, "gate has no Status block")
            description = body.strip().split("\n\n")[0].strip()
            gates.append(
                Gate(
                    id=gate_id,
                    name=gate_match.group(2) or gate_match.group(1),
                    description=" ".join(description.split()),
                    blocks=_list(_block(body, "Blocks")),
                    verified_by=re.findall(
                        r"`(AC-[A-Za-z0-9.\-]+)`", _block(body, "Verified by")
                    ),
                    status=status.split()[0],
                    source=Source(name, gate_id),
                )
            )
    # A milestone bullet may wrap, and the task it depends on often sits on the
    # continuation line, so match across the whole bullet rather than one line.
    for found in re.finditer(
        r"(?m)^- `(M-[A-Za-z0-9\-]+)` — (.+(?:\n  (?!- ).+)*)", text
    ):
        task = re.search(r"(T-[A-Za-z0-9.\-]+)", found.group(2))
        milestones.append(
            Milestone(
                id=found.group(1),
                text=" ".join(found.group(2).split()),
                task=task.group(1) if task else None,
                source=Source(name, found.group(1)),
            )
        )
    return phases, gates, milestones


def parse_decisions(directory: Path) -> list[Decision]:
    decisions: list[Decision] = []
    for path in sorted(directory.glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8")
        heading = re.match(r"# (ADR-[0-9]+): (.+)", text)
        if not heading:
            raise ParseError(path.name, path.stem, "must start with '# ADR-NNN: <title>'")
        fields = _fields(text)
        if "Status" not in fields:
            raise ParseError(path.name, heading.group(1), "missing Status")
        decisions.append(
            Decision(
                id=heading.group(1),
                title=heading.group(2).strip(),
                status=fields["Status"],
                supersedes=[
                    part for part in _list(fields.get("Supersedes")) if part.startswith("ADR-")
                ],
                affects=_list(fields.get("Affects")),
                source=Source(f"ADR/{path.name}", heading.group(1)),
                origin=fields.get("Origin") or "CONTEMPORANEOUS",
                evidence=_named(fields.get("Evidence")),
                authority=_named(fields.get("Authority")),
                policy=_named(fields.get("Policy")),
                reserved=(
                    [g.lower() for g in _list(fields.get("Reserved"))]
                    if "Reserved" in fields else None
                ),
                notify=(_named(fields.get("Notify")) or "").lower() or None,
            )
        )
    return decisions


def parse_trace(path: Path) -> list[TraceEvent]:
    """Operational events from TRACE.md. The file is optional: a project that
    has never recorded an event simply has none."""
    if not path.is_file():
        return []
    name = path.name
    events: list[TraceEvent] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(EV-[A-Za-z0-9.\-]+): (.+)", heading)
        if not match:
            raise ParseError(name, heading, "event heading must read '## EV-<id>: <summary>'")
        event_id = match.group(1)
        fields = _fields(body)
        if "Type" not in fields:
            raise ParseError(name, event_id, "missing required field 'Type'")
        events.append(
            TraceEvent(
                id=event_id,
                title=match.group(2).strip(),
                type=fields["Type"].strip().lower(),
                time=_named(fields.get("Time")),
                task=_named(fields.get("Task")),
                agent=_named(fields.get("Agent")),
                tool=_named(fields.get("Tool")),
                target=_named(fields.get("Target")),
                outcome=(_named(fields.get("Outcome")) or "unknown").lower(),
                error=_named(fields.get("Error")),
                retry_of=_named(fields.get("Retry of")),
                parent=_named(fields.get("Parent")),
                affects=_list(fields.get("Affects")),
                artifact=_named(fields.get("Artifact")),
                evidence=_named(fields.get("Evidence")),
                source=Source(name, event_id),
            )
        )
    return events


def parse_debt(path: Path) -> list[TechDebt]:
    """Technical debt from TECH_DEBT.md. Optional: no file, no debt."""
    if not path.is_file():
        return []
    name = path.name
    debts: list[TechDebt] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(TD-[A-Za-z0-9.\-]+): (.+)", heading)
        if not match:
            raise ParseError(name, heading, "debt heading must read '## TD-<id>: <title>'")
        debt_id = match.group(1)
        fields = _fields(body)
        if "Status" not in fields:
            raise ParseError(name, debt_id, "missing required field 'Status'")
        debts.append(
            TechDebt(
                id=debt_id,
                title=match.group(2).strip(),
                status=fields["Status"].strip().upper(),
                introduced_by=_list(fields.get("Introduced by")),
                areas=_list(fields.get("Areas")),
                debt=_named(fields.get("Debt")),
                reason=_named(fields.get("Reason")),
                interest=_named(fields.get("Interest")),
                trigger=_named(fields.get("Trigger")),
                trigger_state=(_named(fields.get("Trigger state")) or "NOT_REACHED").upper(),
                exit_condition=_named(fields.get("Exit condition")),
                evidence=_named(fields.get("Evidence")),
                linked_tasks=_list(fields.get("Linked tasks")),
                resolution=_named(fields.get("Resolution")),
                source=Source(name, debt_id),
            )
        )
    return debts


def parse_assumptions(path: Path) -> list[Assumption]:
    """ASSUMPTIONS.md (ADR-054). Optional: no file, no assumptions."""
    if not path.is_file():
        return []
    name = path.name
    found: list[Assumption] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(A-[A-Za-z0-9.\-]+): (.+)", heading)
        if not match:
            raise ParseError(name, heading, "assumption heading must read '## A-<id>: <title>'")
        assumption_id = match.group(1)
        fields = _fields(body)
        for required in ("Status", "Tasks", "Impact", "Assumption"):
            if required not in fields:
                raise ParseError(name, assumption_id, f"missing required field '{required}'")
        found.append(Assumption(
            id=assumption_id,
            title=match.group(2).strip(),
            status=fields["Status"].strip().upper(),
            tasks=_list(fields["Tasks"]),
            impact=fields["Impact"].strip().upper(),
            recorded=_named(fields.get("Recorded")),
            assumption=_named(fields.get("Assumption")),
            basis=_named(fields.get("Basis")),
            applied_in=_named(fields.get("Applied in")),
            permissions=_named(fields.get("Permissions")),
            responses=_list(fields.get("Responses")),
            reconciled_by=_list(fields.get("Reconciled by")),
            source=Source(name, assumption_id),
        ))
    return found


def _quoted(body: str) -> str:
    """The owner's words: the blockquote, unquoted, every line kept as written."""
    lines = [line for line in body.splitlines() if line.startswith(">")]
    return "\n".join(line[2:] if line.startswith("> ") else line[1:] for line in lines)


def parse_responses(path: Path) -> list[Response]:
    """RESPONSES.md (ADR-054). Optional, append-only, and read verbatim."""
    if not path.is_file():
        return []
    name = path.name
    found: list[Response] = []
    for heading, body in _sections(path.read_text(encoding="utf-8"), "##"):
        match = re.match(r"(R-[0-9]+): (\S+) (\S+)$", heading)
        if not match:
            raise ParseError(name, heading, "response heading must read '## R-<n>: <target> <ACTION>'")
        fields = _fields(body)
        found.append(Response(
            id=match.group(1),
            target=match.group(2),
            action=match.group(3).upper(),
            by=_named(fields.get("By")),
            date=_named(fields.get("Date")),
            via=_named(fields.get("Via")),
            text=_quoted(body),
            source=Source(name, match.group(1)),
        ))
    return found


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""
