"""Typed objects for one compiled project.

Nothing here reads or writes files. Parsers build these; everything downstream
consumes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

STATUSES = ("TODO", "WIP", "DONE", "BLOCKED")
VALIDATIONS = ("UNTESTED", "SYNTHETIC", "AI_REVIEWED", "HUMAN_VERIFIED")
EVIDENCE_CLASSES = ("TEST", "MUTATION", "INSPECTION", "RUNTIME", "MANUAL")
# Evidence produced by running or exercising the work, not by reading it.
EXERCISED_CLASSES = ("TEST", "MUTATION", "RUNTIME", "MANUAL")
CRITERION_STATES = ("PASS", "FAIL", "NOT_RUN")
PHASE_STATUSES = ("PLANNED", "ACTIVE", "EXIT_PENDING", "COMPLETE")
GATE_STATUSES = ("GREEN", "RED")
# A decision is recorded when it is made, or reconstructed afterwards from what
# the repository already depends on (ADR-037). Absent means contemporaneous.
DECISION_ORIGINS = ("CONTEMPORANEOUS", "RECONSTRUCTED")
NO_PHASE = "P-NONE"
# Every task is either work that advances the project itself, or work that
# maintains the environment it is built in (ADR-045). There is no third value.
DOMAINS = ("execution", "operations")
# How a task's domain was decided: authored, or inferred from structure. A
# task with no structural evidence is `unresolved`; it counts as execution and
# validation says so, rather than a guess being made from its title.
DOMAIN_SOURCES = ("declared", "phase", "exit-authority", "gate", "unresolved")
# Operational trace events (TRACE.md). An event is evidence, never a task.
EVENT_TYPES = (
    "tool-call", "command", "action", "mutation", "failure", "retry", "validation", "note",
)
EVENT_OUTCOMES = ("success", "failure", "partial", "unknown")
# Technical debt is a liability, not work (ADR-046). It has no domain; the
# task that repays it has one.
DEBT_STATUSES = ("OPEN", "ACCEPTED", "SCHEDULED", "RESOLVED", "INVALIDATED")
DEBT_CLOSED = ("RESOLVED", "INVALIDATED")
TRIGGER_STATES = ("NOT_REACHED", "APPROACHING", "REACHED")

# Provisional agent assumptions and the owner's verbatim responses (ADR-054).
ASSUMPTION_STATUSES = ("OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN")
IMPACTS = ("LOW", "MEDIUM", "HIGH")
RESPONSE_ACTIONS = ("CONFIRM", "REVISE", "REJECT", "GUIDE", "VERIFY")
# VERIFY is the owner's verification of an `Authority: owner` task, recorded
# only interactively and counted only from a named owner (ADR-060).
VERIFY = "VERIFY"
# A GitHub login: what `Owners:` names and what `By:` records (ADR-060).
GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
# The status a decisive response asks for. GUIDE asks for none.
RESPONSE_STATUS = {"CONFIRM": "CONFIRMED", "REVISE": "REVISED", "REJECT": "REJECTED"}
# A task only a named person can finish.
TASK_AUTHORITIES = ("owner",)
# The per-project assumption policy (ADR-057): groups an agent may not assume.
POLICY_GROUPS = ("permission", "security", "external-disclosure", "legal", "source-of-truth")
NOTIFY_CHANNELS = ("host", "none")
# Coordination conventions and task briefs (ADR-075).
CONVENTION_STATUSES = ("ACTIVE", "RETIRED")
BRIEF_STATUSES = ("ACTIVE", "CLOSED")
COORDINATION_ROLES = ("builder", "reviewer", "coordinator")

OBSTACLE_TYPES = (
    "DEPENDENCY_BLOCKER",
    "ACCEPTANCE_BLOCKER",
    "GATE_BLOCKER",
    "PHASE_BLOCKER",
    "VALIDATION_GAP",
    "SCHEDULE_BLOCKER",
)


@dataclass(frozen=True)
class Source:
    """Where a compiled object came from, so a reader can reach authority."""

    file: str
    anchor: str

    def as_json(self) -> dict[str, str]:
        return {"file": self.file, "anchor": self.anchor}


@dataclass
class Criterion:
    id: str
    text: str
    evidence_class: str
    state: str
    evidence: str | None
    source: Source

    @property
    def satisfied(self) -> bool:
        return self.state == "PASS"


@dataclass
class Contract:
    """One task's completion contract, or an inherited AC-GLOBAL-* contract."""

    id: str
    title: str
    inherits: list[str]
    criteria: list[Criterion]
    evidence: str | None
    source: Source

    @property
    def is_global(self) -> bool:
        return self.id.startswith("AC-GLOBAL-")


@dataclass
class ChangeRequest:
    """An Acceptance Change Request from the log in ACCEPTANCE.md (ADR-076)."""

    id: str
    number: int
    criterion: str
    current: str | None
    proposed: str | None
    decision: str
    source: Source

    @property
    def accepted(self) -> bool:
        return self.decision.startswith("ACCEPTED")


@dataclass
class Schedule:
    start: str | None = None
    end: str | None = None
    estimate: str | None = None

    @property
    def known(self) -> bool:
        """True only when a real calendar bar can be drawn without inventing."""
        return bool(self.start and (self.end or self.estimate))

    def as_json(self) -> dict[str, str] | None:
        data = {k: v for k, v in vars(self).items() if v}
        return data or None


@dataclass
class Task:
    id: str
    title: str
    phase: str
    status: str
    validation: str
    dependencies: list[str]
    owner: str | None
    claimed: str | None
    contract: str | None
    evidence: str | None
    decisions: list[str]
    schedule: Schedule
    source: Source
    declared_domain: str | None = None
    domain: str = "execution"
    domain_source: str = "unresolved"
    # Optional implementation anchors (ADR-049): where the code for this task
    # lives, to seed a code-structure query. Never required, never derived.
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    # The authored module (ADR-035). A task's phase is derived from it. A
    # legacy task authors `Phase:` instead and has no module (ADR-036).
    module: str | None = None
    legacy_phase: str | None = None
    # `owner` marks work only a named person can finish (ADR-054).
    authority: str | None = None
    # The contract's fingerprint from when the task became WIP (ADR-078), as authored.
    frozen: str | None = None

    @property
    def owner_held(self) -> bool:
        return self.authority == "owner"

    @property
    def done(self) -> bool:
        return self.status == "DONE"

    @property
    def execution(self) -> bool:
        return self.domain == "execution"

    @property
    def phase_independent(self) -> bool:
        return self.phase == NO_PHASE


@dataclass
class Thesis:
    """The product thesis every phase, module, and task descends from."""

    statement: str
    reference: str | None
    source: Source
    language: str | None = None

    def as_json(self) -> dict[str, object]:
        return {"statement": self.statement or None, "reference": self.reference,
                "source": self.source.as_json(), "language": self.language}


@dataclass
class Module:
    """A unit of work inside exactly one phase, or `P-NONE` (ADR-035)."""

    id: str
    name: str
    phase: str
    outcome: str
    source: Source


@dataclass
class Phase:
    id: str
    name: str
    outcome: str
    entry: list[str]
    exit: list[str]
    exit_authority: str | None
    status: str
    source: Source


@dataclass
class Gate:
    id: str
    name: str
    description: str
    blocks: list[str]
    verified_by: list[str]
    status: str
    source: Source


@dataclass
class Milestone:
    id: str
    text: str
    task: str | None
    source: Source


@dataclass
class Decision:
    id: str
    title: str
    status: str
    supersedes: list[str]
    affects: list[str]
    source: Source
    origin: str = "CONTEMPORANEOUS"
    evidence: str | None = None
    authority: str | None = None
    # Set only on an assumption-policy ADR (ADR-057).
    policy: str | None = None
    reserved: list[str] | None = None
    notify: str | None = None
    # The project's owners by GitHub login (ADR-060); None when not stated.
    owners: list[str] | None = None

    @property
    def reconstructed(self) -> bool:
        return self.origin == "RECONSTRUCTED"


@dataclass
class Assumption:
    """A choice an agent made provisionally. It holds no authority itself."""

    id: str
    title: str
    status: str
    tasks: list[str]
    impact: str
    recorded: str | None
    assumption: str | None
    basis: str | None
    applied_in: str | None
    permissions: str | None
    responses: list[str]
    reconciled_by: list[str]
    source: Source

    @property
    def open(self) -> bool:
        return self.status == "OPEN"

    @property
    def references(self) -> list[str]:
        """Record ids named in `Applied in`; free text is kept, not resolved."""
        return re.findall(r"\b(?:ADR-[0-9]+|AC-[A-Za-z0-9.\-]*[A-Za-z0-9]|T-[A-Za-z0-9.\-]*[A-Za-z0-9])\b",
                          self.applied_in or "")


@dataclass
class Response:
    """What the owner said, verbatim. Append-only (ADR-054)."""

    id: str
    target: str
    action: str
    by: str | None
    date: str | None
    via: str | None
    text: str
    source: Source

    def as_json(self) -> dict[str, object]:
        return {"id": self.id, "target": self.target, "action": self.action, "by": self.by,
                "date": self.date, "via": self.via, "text": self.text,
                "source": self.source.as_json()}


@dataclass
class Convention:
    """A standing coordination rule of this project (ADR-075)."""

    id: str
    title: str
    status: str
    roles: list[str]  # empty means every role
    text: str
    source: Source

    def applies_to(self, role: str) -> bool:
        return not self.roles or role in self.roles

    def as_json(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "status": self.status, "roles": self.roles,
                "text": self.text, "authority": f"{self.source.file}#{self.id}"}


@dataclass
class Brief:
    """What a coordinator adds to one task's contract, never the contract itself (ADR-075)."""

    id: str
    title: str
    task: str
    status: str
    conventions: list[str] | None  # None: the field is missing, which validate reports
    text: str
    source: Source

    def as_json(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "task": self.task, "status": self.status,
                "conventions": self.conventions, "text": self.text,
                "authority": f"{self.source.file}#{self.id}"}


@dataclass
class Policy:
    """The assumption policy in force: groups reserved for the owner."""

    reserved: list[str]
    notify: str
    decision: str | None  # the ADR that set it; None means the default

    def as_json(self) -> dict[str, object]:
        return {"reserved": self.reserved, "notify": self.notify,
                "decision": self.decision, "default": self.decision is None}


def event_order(event_id: str, time: str | None) -> tuple:
    """The one order of events: own time, then the number ending the id, then the id.

    Where an event is stored must never decide which events are the latest, so every
    view that sorts events or takes the last N uses this key.
    """
    number = re.search(r"(\d+)$", event_id)
    return (time or "", int(number.group(1)) if number else -1, event_id)


@dataclass
class TraceEvent:
    """One operational event: a tool call, command, mini-action, mutation,
    failure, retry, or validation run. Evidence about work, not work."""

    id: str
    title: str
    type: str
    time: str | None
    task: str | None
    agent: str | None
    tool: str | None
    target: str | None
    outcome: str
    error: str | None
    retry_of: str | None
    parent: str | None
    affects: list[str]
    artifact: str | None
    evidence: str | None
    source: Source

    @property
    def failed(self) -> bool:
        return self.outcome == "failure" or self.type == "failure"

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id, "title": self.title, "type": self.type, "time": self.time,
            "task": self.task, "agent": self.agent, "tool": self.tool,
            "target": self.target, "outcome": self.outcome, "error": self.error,
            "retryOf": self.retry_of, "parent": self.parent, "affects": self.affects,
            "artifact": self.artifact, "evidence": self.evidence,
            "source": self.source.as_json(),
        }


@dataclass
class TechDebt:
    """A known compromise: what it is, why it exists, what it costs, and what
    would retire it."""

    id: str
    title: str
    status: str
    introduced_by: list[str]
    areas: list[str]
    debt: str | None
    reason: str | None
    interest: str | None
    trigger: str | None
    trigger_state: str
    exit_condition: str | None
    evidence: str | None
    linked_tasks: list[str]
    resolution: str | None
    source: Source

    @property
    def closed(self) -> bool:
        return self.status in DEBT_CLOSED

    @property
    def needs_attention(self) -> bool:
        """Open to act on: a trigger reached or approaching, or not yet decided."""
        return not self.closed and (
            self.trigger_state in ("REACHED", "APPROACHING") or self.status == "OPEN"
        )

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "introducedBy": self.introduced_by, "areas": self.areas, "debt": self.debt,
            "reason": self.reason, "interest": self.interest, "trigger": self.trigger,
            "triggerState": self.trigger_state, "exitCondition": self.exit_condition,
            "evidence": self.evidence, "linkedTasks": self.linked_tasks,
            "resolution": self.resolution, "source": self.source.as_json(),
        }


@dataclass
class Obstacle:
    type: str
    subject: str
    blockers: list[str]
    detail: str
    domain: str = "execution"
    # A finer reading of the type where one exists, e.g. a validation gap
    # with evidence recorded but no review, versus one with no evidence.
    variant: str | None = None
    # Derived, never authored: who must act (agent, owner, operations) and
    # what would clear it (ADR-054).
    actor: str = "agent"
    unblock: str = ""
    # The owner-held tasks a dependency path ends at, when the actor is the owner.
    holders: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "type": self.type,
            "subject": self.subject,
            "blockers": self.blockers,
            "detail": self.detail,
            "domain": self.domain,
            "variant": self.variant,
            "actor": self.actor,
            "unblock": self.unblock,
            "holders": self.holders,
        }


Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    where: str
    owner_action: tuple[str, ...] = ()

    def render(self) -> str:
        return f"{self.severity}: {self.code}: {self.message} ({self.where})"


@dataclass
class Project:
    """The whole compiled project. Derived, disposable, never authoritative."""

    name: str
    root: str
    current_phase: str | None
    thesis: Thesis = field(
        default_factory=lambda: Thesis("", None, Source("THESIS.md", "Product thesis"))
    )
    phases: list[Phase] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    contracts: dict[str, Contract] = field(default_factory=dict)
    gates: list[Gate] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    intent: str = ""
    handoff: str = ""
    journal: list[dict] = field(default_factory=list)
    recorded_date: dict | None = None
    events: list[TraceEvent] = field(default_factory=list)
    debts: list[TechDebt] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    responses: list[Response] = field(default_factory=list)
    conventions: list[Convention] = field(default_factory=list)
    briefs: list[Brief] = field(default_factory=list)
    change_requests: list[ChangeRequest] = field(default_factory=list)
    # Exact bytes parsed for writable records; never serialized into compiled views.
    record_bytes: dict[str, bytes] = field(default_factory=dict, repr=False, compare=False)
    journal_validation: list[Finding] | None = field(default=None, repr=False, compare=False)

    @property
    def policy(self) -> Policy:
        """The newest ACCEPTED policy ADR, or the default: everything reserved."""
        found = [d for d in self.decisions
                 if d.status == "ACCEPTED" and (d.policy or "").lower() == "assumptions"]
        if not found:
            return Policy(list(POLICY_GROUPS), "host", None)
        latest = max(found, key=lambda d: int(d.id.split("-")[1]))
        return Policy(list(latest.reserved or []), latest.notify or "host", latest.id)

    @property
    def owners_decision(self) -> Decision | None:
        """The newest ACCEPTED ADR naming `Owners:`, or None (ADR-060)."""
        found = [d for d in self.decisions if d.status == "ACCEPTED" and d.owners is not None]
        return max(found, key=lambda d: int(d.id.split("-")[1])) if found else None

    @property
    def owners(self) -> list[str]:
        decision = self.owners_decision
        return [owner for owner in decision.owners if GITHUB_LOGIN.match(owner)] if decision else []

    def is_owner(self, login: str | None) -> bool:
        """GitHub logins are case-insensitive."""
        return bool(login) and login.lower() in {owner.lower() for owner in self.owners}

    def verified_by_owner(self, task_id: str) -> list[Response]:
        return [response for response in self.responses_to(task_id)
                if response.action == VERIFY and self.is_owner(response.by)]

    def assumption(self, assumption_id: str) -> Assumption | None:
        for assumption in self.assumptions:
            if assumption.id == assumption_id:
                return assumption
        return None

    def responses_to(self, target: str) -> list[Response]:
        return [r for r in self.responses if r.target == target]

    def brief_for(self, task_id: str) -> Brief | None:
        """The task's ACTIVE brief, else its last CLOSED one (ADR-075)."""
        briefs = [b for b in self.briefs if b.task == task_id]
        active = [b for b in briefs if b.status == "ACTIVE"]
        if active:
            return active[0]
        return briefs[-1] if briefs else None

    def convention(self, convention_id: str) -> Convention | None:
        return next((c for c in self.conventions if c.id == convention_id), None)

    def assumptions_for(self, task_id: str) -> list[Assumption]:
        return [a for a in self.assumptions if task_id in a.tasks]

    def assumption_phases(self, assumption: Assumption) -> list[str]:
        """Phase and module come from the tasks, never from the record."""
        return list(dict.fromkeys(
            self.task(t).phase for t in assumption.tasks if self.task(t) is not None))

    def debts_for(self, record_id: str) -> list[TechDebt]:
        """Debt a task or decision introduced, or a task repays."""
        return [d for d in self.debts if record_id in d.introduced_by or record_id in d.linked_tasks]

    @property
    def execution_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.execution]

    @property
    def operations_tasks(self) -> list[Task]:
        return [t for t in self.tasks if not t.execution]

    def execution_in(self, phase_id: str) -> list[Task]:
        """A phase's own work: its execution tasks, never its operations."""
        return [t for t in self.tasks if t.phase == phase_id and t.execution]

    def task(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def phase(self, phase_id: str) -> Phase | None:
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        return None

    def module(self, module_id: str) -> Module | None:
        for module in self.modules:
            if module.id == module_id:
                return module
        return None

    def modules_in(self, phase_id: str) -> list[Module]:
        return [m for m in self.modules if m.phase == phase_id]

    def tasks_in_module(self, module_id: str) -> list[Task]:
        return [t for t in self.tasks if t.module == module_id]

    @property
    def uses_modules(self) -> bool:
        """True once a chronicle has adopted the hierarchy at all."""
        return bool(self.modules) or any(t.module for t in self.tasks)

    def tasks_in(self, phase_id: str) -> list[Task]:
        return [t for t in self.tasks if t.phase == phase_id]

    def mandatory_criteria(self, task: Task) -> list[Criterion]:
        """The criteria that decide whether this one task may be DONE.

        Inherited AC-GLOBAL-* contracts are project invariants, not per-task
        criteria: one task cannot carry evidence for a property of the whole
        project. They are reported separately and gate phases, not tasks.
        """
        contract = self.contracts.get(task.contract or "")
        return list(contract.criteria) if contract else []

    def invariants_for(self, task: Task) -> list[Contract]:
        contract = self.contracts.get(task.contract or "")
        if contract is None:
            return []
        return [
            self.contracts[name]
            for name in contract.inherits
            if name in self.contracts
        ]

    @property
    def invariants(self) -> list[Contract]:
        return [c for c in self.contracts.values() if c.is_global]
