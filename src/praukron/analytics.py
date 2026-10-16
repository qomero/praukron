"""Deterministic project analytics.

Nothing here consults a model. Given the same compiled project, every function
returns the same answer, which is what makes a disagreement about project state
decidable rather than negotiable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import layout
import re

from .model import EXERCISED_CLASSES, RESPONSE_STATUS, Obstacle, Project, Task


@dataclass
class Progress:
    done: int
    total: int

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    def as_json(self) -> dict[str, float | int]:
        return {"done": self.done, "total": self.total, "fraction": round(self.fraction, 4)}

    def __str__(self) -> str:
        return f"{self.done} / {self.total}"


@dataclass
class Report:
    project: Project
    ready: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    wip: list[str] = field(default_factory=list)
    obstacles: list[Obstacle] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    downstream: dict[str, list[str]] = field(default_factory=dict)
    phase_progress: dict[str, Progress] = field(default_factory=dict)
    scheduled: list[dict[str, str]] = field(default_factory=list)
    unscheduled: list[str] = field(default_factory=list)
    # Execution impact of operations work: execution task -> the open
    # operations tasks it waits on. The operations task keeps its domain.
    external_blockers: dict[str, list[str]] = field(default_factory=dict)
    in_flight: list[str] = field(default_factory=list)
    main_blocker: dict[str, object] | None = None
    next_gate: dict[str, object] | None = None
    execution_failures: list[dict[str, object]] = field(default_factory=list)
    # Phase -> the earlier phase whose exit it waits on, when that is open.
    phase_waits: dict[str, str] = field(default_factory=dict)
    work_ahead: dict[str, object] | None = None
    assumptions: dict[str, object] = field(default_factory=dict)

    def _criteria(self, tasks: list[Task]) -> list:
        referenced = {t.contract for t in tasks if t.contract}
        return [
            criterion
            for contract in self.project.contracts.values()
            if not contract.is_global and contract.id in referenced
            for criterion in contract.criteria
        ]

    def metrics(self) -> dict[str, object]:
        # Progress is execution progress (ADR-045). Operations work is counted
        # separately in operations_metrics and never moves these figures.
        tasks = self.project.execution_tasks
        # Phase-independent execution still counts: excluding it made a project
        # of nothing but unphased work report 0 / 0.
        counted = tasks
        in_phases = [t for t in tasks if not t.phase_independent]
        criteria = self._criteria(tasks)
        validated = ("AI_REVIEWED", "HUMAN_VERIFIED")
        return {
            "taskCompletion": Progress(
                sum(1 for t in counted if t.done), len(counted)
            ).as_json(),
            "phaseCompletion": {
                phase_id: progress.as_json()
                for phase_id, progress in self.phase_progress.items()
            },
            "criticalPathCompletion": Progress(
                sum(1 for task_id in self.critical_path if self._done(task_id)),
                len(self.critical_path),
            ).as_json(),
            "validationCoverage": Progress(
                sum(1 for t in counted if t.validation in validated), len(counted)
            ).as_json(),
            "phaseIndependent": len(tasks) - len(in_phases),
            "gateReadiness": Progress(
                sum(1 for g in self.project.gates if g.status == "GREEN"),
                len(self.project.gates),
            ).as_json(),
            # Across every planned contract, including work not yet started.
            "acceptanceCompletion": Progress(
                sum(1 for c in criteria if c.state == "PASS"), len(criteria)
            ).as_json(),
            # The same criteria split by whether their task has started, so
            # future work never reads as a quality shortfall.
            "acceptanceBreakdown": {
                "started": _states(self._criteria([t for t in tasks if t.status in ("WIP", "DONE")])),
                "notStarted": _states(self._criteria([t for t in tasks if t.status not in ("WIP", "DONE")])),
            },
            "validationBreakdown": _counts(t.validation for t in tasks),
            # Validation coverage counts AI_REVIEWED and HUMAN_VERIFIED only,
            # over every execution task. Said here so no view has to guess.
            "validationCounts": list(validated),
            "statusBreakdown": _counts(t.status for t in tasks),
            "domainBreakdown": {
                "execution": len(tasks),
                "operations": len(self.project.operations_tasks),
            },
        }

    def operations_metrics(self) -> dict[str, object]:
        """Operational state and evidence. None of it is project progress."""
        operations = self.project.operations_tasks
        events = self.project.events
        blocked = set(self.blocked)
        affecting = sorted({o for blockers in self.external_blockers.values() for o in blockers})
        on_path = set(self.critical_path)
        return {
            "tasks": {
                "total": len(operations),
                "done": sum(1 for t in operations if t.done),
                "active": sum(1 for t in operations if t.status == "WIP"),
                "blocked": sum(1 for t in operations if t.id in blocked),
                "open": sum(1 for t in operations if not t.done),
            },
            "acceptance": Progress(
                sum(1 for c in self._criteria(operations) if c.state == "PASS"),
                len(self._criteria(operations)),
            ).as_json(),
            "events": {
                "total": len(events),
                "byType": _counts(e.type for e in events),
                "failures": sum(1 for e in events if e.failed),
                "unresolvedFailures": len(unresolved_failures(self.project)),
                "retries": sum(1 for e in events if e.retry_of or e.type == "retry"),
                "mutations": sum(1 for e in events if e.type == "mutation"),
            },
            "affectingExecution": affecting,
            "affectingCriticalPath": sorted({
                o for task, blockers in self.external_blockers.items()
                if task in on_path for o in blockers
            }),
            "affectingGate": sorted(
                {b["id"] for b in (self.next_gate or {}).get("externalBlockers", [])}
            ),
        }

    def _done(self, task_id: str) -> bool:
        task = self.project.task(task_id)
        return bool(task and task.done)


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def describe(metrics: dict[str, object], report: "Report") -> dict[str, str]:
    """One wording of the headline figures, shared by status, STATE.md, and the dashboard."""
    started = metrics["acceptanceBreakdown"]["started"]
    later = metrics["acceptanceBreakdown"]["notStarted"]
    coverage = metrics["validationCoverage"]
    counts = metrics["validationBreakdown"]
    project = report.project
    done = [t for t in project.execution_tasks if t.done and t.validation not in ("AI_REVIEWED", "HUMAN_VERIFIED")]
    with_evidence = sum(1 for t in done if exercised(project, t))
    lines = {
        "acceptanceStarted": (
            f"started work: {started['PASS']} pass · {started['FAIL']} fail · {started['NOT_RUN']} not run"
        ),
        # A task not yet WIP or DONE (TODO or BLOCKED) can still hold passing
        # criteria; say so rather than call them "not reached" (exit run).
        "acceptanceLater": (
            f"not started: {sum(later.values())} criteria"
            + (f", {later['PASS']} already pass" if later["PASS"] else " not reached yet")
        ),
        "reviewed": (
            f"{coverage['done']} / {coverage['total']} execution tasks AI_REVIEWED or HUMAN_VERIFIED"
            f" · SYNTHETIC {counts.get('SYNTHETIC', 0)} · UNTESTED {counts.get('UNTESTED', 0)}"
        ),
        "unreviewed": (
            f"{len(done)} done without review, {with_evidence} of them with exercised evidence"
            if done else ""
        ),
        "workAhead": "",
    }
    ahead = report.work_ahead
    if ahead:
        exit_ = ahead["oldestOpenExit"]
        phases = ", ".join(f"{k} {v}" for k, v in ahead["phases"].items())
        lines["workAhead"] = (
            f"{ahead['tasks']} done task{'' if ahead['tasks'] == 1 else 's'} ahead of "
            f"{exit_['phase']}'s exit ({exit_['exitAuthority'] or 'no exit authority'}): {phases}"
        )
    return lines


def _states(criteria) -> dict[str, int]:
    return {state: sum(1 for c in criteria if c.state == state) for state in ("PASS", "FAIL", "NOT_RUN")}


def exercised(project: Project, task: Task) -> list[str]:
    """Evidence classes of this task's passing criteria that exercised the work."""
    return sorted({
        c.evidence_class for c in project.mandatory_criteria(task)
        if c.state == "PASS" and c.evidence_class in EXERCISED_CLASSES
    })


def _phase_waits(project: Project) -> dict[str, str]:
    """Each unfinished phase whose predecessor's exit is still open."""
    waits: dict[str, str] = {}
    for earlier, later in zip(project.phases, project.phases[1:]):
        if earlier.status != "COMPLETE" and later.status != "COMPLETE":
            waits[later.id] = earlier.id
    return waits


def _work_ahead(project: Project, waits: dict[str, str]) -> dict[str, object] | None:
    """Execution finished in phases whose predecessor has not exited. Descriptive only."""
    ahead = {
        phase_id: sum(1 for t in project.execution_in(phase_id) if t.done)
        for phase_id in waits
    }
    ahead = {k: v for k, v in ahead.items() if v}
    if not ahead:
        return None
    oldest = next(p for p in project.phases if p.status != "COMPLETE")
    return {
        "oldestOpenExit": {"phase": oldest.id, "exitAuthority": oldest.exit_authority},
        "phases": ahead,
        "tasks": sum(ahead.values()),
    }


def unmet(project: Project, task: Task) -> list[str]:
    """Criteria that stand between this task and DONE."""
    return [
        criterion.id
        for criterion in project.mandatory_criteria(task)
        if criterion.state != "PASS"
    ]


def _dependency_blockers(project: Project, task: Task) -> list[str]:
    """Dependencies that are not done. An unknown dependency always blocks."""
    blockers = []
    for dependency in task.dependencies:
        known = project.task(dependency)
        if known is None or not known.done:
            blockers.append(dependency)
    return blockers


def _critical_path(project: Project) -> list[str]:
    """Longest chain of not-yet-done execution tasks, following dependencies forward.

    Ordering only. This says nothing about dates; see the schedule split.
    """
    # The critical path runs through execution only. An operations task that
    # holds up a node is reported as that node's external blocker instead.
    open_tasks = {t.id: t for t in project.execution_tasks if not t.done}
    memo: dict[str, list[str]] = {}

    def chain(task_id: str, seen: frozenset[str]) -> list[str]:
        if task_id in seen:
            return []
        if task_id in memo:
            return memo[task_id]
        best: list[str] = []
        for candidate in open_tasks.values():
            if task_id in candidate.dependencies:
                found = chain(candidate.id, seen | {task_id})
                if len(found) > len(best):
                    best = found
        result = [task_id, *best]
        if not seen:
            memo[task_id] = result
        return result

    longest: list[str] = []
    for task_id, task in open_tasks.items():
        if any(dependency in open_tasks for dependency in task.dependencies):
            continue  # start only from chains that can begin now
        found = chain(task_id, frozenset())
        if len(found) > len(longest):
            longest = found
    return longest


def report(project: Project) -> Report:
    result = Report(project=project)

    for task in project.tasks:
        for dependency in task.dependencies:
            result.downstream.setdefault(dependency, []).append(task.id)

    for task in project.tasks:
        if task.status == "WIP":
            result.wip.append(task.id)
        if task.done:
            continue
        blockers = _dependency_blockers(project, task)
        if blockers:
            result.blocked.append(task.id)
            result.obstacles.append(
                Obstacle(
                    type="DEPENDENCY_BLOCKER",
                    subject=task.id,
                    blockers=blockers,
                    detail=f"{task.id} waits on {', '.join(blockers)}",
                    domain=task.domain,
                )
            )
            if task.execution:
                external = [
                    b for b in blockers
                    if project.task(b) is not None and not project.task(b).execution
                ]
                if external:
                    result.external_blockers[task.id] = external
        elif task.status == "TODO":
            # Ready means "can start now". Work already in flight is reported as
            # WIP; listing it as ready too would double-count it.
            result.ready.append(task.id)

        if task.status == "WIP":
            outstanding = unmet(project, task)
            if outstanding:
                result.obstacles.append(
                    Obstacle(
                        type="ACCEPTANCE_BLOCKER",
                        subject=task.id,
                        blockers=outstanding,
                        detail=(
                            f"{task.id} cannot complete while "
                            f"{len(outstanding)} criteria are unmet"
                        ),
                        domain=task.domain,
                    )
                )

    for task in project.tasks:
        if task.done and task.validation == "UNTESTED":
            # UNTESTED records no review. Whether the work was exercised is a
            # different fact, read from the criteria, and said separately.
            classes = exercised(project, task)
            result.obstacles.append(
                Obstacle(
                    type="VALIDATION_GAP",
                    subject=task.id,
                    blockers=[],
                    detail=(
                        f"{task.id} is DONE with {' and '.join(classes)} evidence recorded; "
                        "no review recorded"
                        if classes else
                        f"{task.id} is DONE with no exercised evidence and no review recorded"
                    ),
                    domain=task.domain,
                    variant="REVIEW_MISSING" if classes else "EVIDENCE_MISSING",
                )
            )

    for phase in project.phases:
        # A phase's progress is its execution work only. Operations tasks that
        # name the phase are associated with it but never counted (ADR-045).
        tasks = project.execution_in(phase.id)
        result.phase_progress[phase.id] = Progress(
            sum(1 for task in tasks if task.done), len(tasks)
        )
        if phase.status in {"ACTIVE", "EXIT_PENDING"}:
            red = [gate.id for gate in project.gates if _blocks(gate, phase.id)]
            unfinished = [task.id for task in tasks if not task.done]
            if red:
                result.obstacles.append(
                    Obstacle(
                        type="GATE_BLOCKER",
                        subject=phase.id,
                        blockers=red,
                        detail=(
                            f"{phase.id} cannot exit while {', '.join(red)} "
                            f"{'is' if len(red) == 1 else 'are'} red"
                        ),
                    )
                )
            if unfinished:
                result.obstacles.append(
                    Obstacle(
                        type="PHASE_BLOCKER",
                        subject=phase.id,
                        blockers=unfinished,
                        detail=(
                            f"{phase.id} has {len(unfinished)} unfinished "
                            f"task{'s' if len(unfinished) != 1 else ''}"
                        ),
                    )
                )

    for task in project.tasks:
        if task.done:
            continue
        if task.schedule.known:
            result.scheduled.append(
                {
                    "id": task.id,
                    **{k: v for k, v in vars(task.schedule).items() if v},
                }
            )
        else:
            result.unscheduled.append(task.id)
            if task.schedule.start or task.schedule.estimate:
                result.obstacles.append(
                    Obstacle(
                        type="SCHEDULE_BLOCKER",
                        subject=task.id,
                        blockers=[],
                        detail=(
                            f"{task.id} has partial schedule metadata; a calendar "
                            "bar needs a start plus an estimate or end"
                        ),
                        domain=task.domain,
                    )
                )

    result.phase_waits = _phase_waits(project)
    result.work_ahead = _work_ahead(project, result.phase_waits)
    result.critical_path = _critical_path(project)
    result.ready.sort()
    result.blocked.sort()
    result.in_flight = _in_flight(project, result)
    result.next_gate = _next_gate(project, result)
    result.main_blocker = _main_blocker(project, result)
    _assign_actors(project, result)
    if result.main_blocker:
        blocker = result.main_blocker
        holder = project.task(blocker["id"]) if blocker["kind"] == "TASK" else None
        blocker["actor"] = (
            "owner" if holder and holder.owner_held
            else "operations" if holder and not holder.execution else "agent")
        blocker["unblock"] = (
            f"{'the owner finishes' if blocker['actor'] == 'owner' else 'finish'} {blocker['id']}"
            if holder else f"record the evidence that turns {blocker['id']} green")
    result.assumptions = assumption_report(project, result)
    result.execution_failures = _execution_failures(project)
    return result


def _open_leaves(project: Project, task: Task) -> list[str]:
    """The open work at the far end of a task's unfinished dependencies."""
    leaves: list[str] = []
    seen: set[str] = set()

    def walk(task_id: str) -> None:
        if task_id in seen:
            return
        seen.add(task_id)
        current = project.task(task_id)
        if current is None or current.done:
            return
        deeper = [d for d in current.dependencies if project.task(d) and not project.task(d).done]
        if not deeper or current.status == "WIP":
            leaves.append(task_id)
        for dependency in deeper:
            walk(dependency)

    for dependency in task.dependencies:
        walk(dependency)
    return leaves


def _assign_actors(project: Project, result: Report) -> None:
    """Who must act on each obstacle, and what would clear it. Derived only."""
    for obstacle in result.obstacles:
        task = project.task(obstacle.subject)
        if obstacle.type == "DEPENDENCY_BLOCKER":
            leaves = _open_leaves(project, task)
            owners = [t for t in leaves if project.task(t).owner_held]
            operations = [t for t in obstacle.blockers if project.task(t) and not project.task(t).execution]
            if owners:
                obstacle.actor = "owner"
                obstacle.holders = owners
                obstacle.unblock = f"the owner finishes {', '.join(owners)}"
            elif operations:
                obstacle.actor = "operations"
                obstacle.unblock = f"operations work finishes {', '.join(operations)}"
            else:
                obstacle.unblock = f"finish {', '.join(obstacle.blockers)}"
        elif obstacle.type == "ACCEPTANCE_BLOCKER":
            obstacle.actor = "owner" if task and task.owner_held else "agent"
            shown = obstacle.blockers[:3]
            more = len(obstacle.blockers) - len(shown)
            obstacle.unblock = f"record passing evidence for {', '.join(shown)}" + (f" and {more} more" if more else "")
        elif obstacle.type == "GATE_BLOCKER":
            obstacle.unblock = f"record the evidence that turns {', '.join(obstacle.blockers)} green"
        elif obstacle.type == "PHASE_BLOCKER":
            owners = [t for t in obstacle.blockers if project.task(t) and project.task(t).owner_held]
            obstacle.actor = "owner" if owners and len(owners) == len(obstacle.blockers) else "agent"
            obstacle.unblock = f"finish {len(obstacle.blockers)} open task{'s' if len(obstacle.blockers) != 1 else ''}"
        elif obstacle.type == "VALIDATION_GAP":
            obstacle.unblock = (
                "record a review: AI_REVIEWED by an independent agent, or HUMAN_VERIFIED by the owner"
                if obstacle.variant == "REVIEW_MISSING" else
                "record TEST, RUNTIME, MUTATION, or MANUAL evidence, then a review"
            )
        elif obstacle.type == "SCHEDULE_BLOCKER":
            obstacle.unblock = "add a start plus an estimate or an end"
        if obstacle.domain == "operations" and obstacle.actor == "agent":
            obstacle.actor = "operations"


def assumption_line(report: Report) -> str:
    """One line for status and INDEX.md; empty when there is nothing to say."""
    a = report.assumptions
    project = report.project
    if not (project.assumptions or project.responses or a["ownerHeld"]):
        return ""
    held = ", ".join(
        f"{w['task']} ({w['unblock']})" for w in a["waitingOnOwner"][:3]
    ) or ", ".join(a["ownerHeld"][:3]) or "none"
    return (
        f"{len(a['open'])} open · {len(a['openHigh'])} HIGH · {len(a['openPermission'])} permission · "
        f"{len(a['awaitingReconciliation'])} awaiting reconciliation · owner-held: {held}"
    )


def awaiting_reconciliation(project: Project) -> list[dict[str, object]]:
    """Owner responses the records do not yet reflect, including archived ones."""
    waiting = []
    for assumption in project.assumptions:
        decisive = [r for r in project.responses_to(assumption.id) if r.action in RESPONSE_STATUS]
        if not decisive:
            continue
        latest = decisive[-1]
        wanted = RESPONSE_STATUS[latest.action]
        needs_record = latest.action in ("REVISE", "REJECT") and not assumption.reconciled_by
        if assumption.status != wanted or needs_record:
            item: dict[str, object] = {
                "assumption": assumption.id, "response": latest.id, "action": latest.action,
            }
            if assumption.source.file.startswith(f"{layout.ARCHIVE_DIR}/"):
                item["archived"] = True
                item["remedy"] = "owner must run archive restore before an agent can reconcile it"
            waiting.append(item)
    return waiting


def assumption_report(project: Project, result: Report) -> dict[str, object]:
    """What the owner needs to review and what the agent needs to reconcile."""
    opened = [a for a in project.assumptions if a.open]
    path = set(result.critical_path)
    current = project.current_phase
    on_current = [
        a.id for a in opened
        if (current and current in project.assumption_phases(a)) or path & set(a.tasks)
    ]
    on_done = [
        {"assumption": a.id, "tasks": [t for t in a.tasks if project.task(t) and project.task(t).done]}
        for a in opened if any(project.task(t) and project.task(t).done for t in a.tasks)
    ]
    resting = []
    for contract in project.contracts.values():
        for criterion in contract.criteria:
            if criterion.state != "PASS" or not criterion.evidence:
                continue
            for a in opened:
                if re.search(rf"(?<![A-Za-z0-9-]){re.escape(a.id)}(?![A-Za-z0-9])", criterion.evidence):
                    resting.append({"criterion": criterion.id, "assumption": a.id})
    exits = {p.exit_authority for p in project.phases if p.exit_authority}
    exit_criteria = {
        c.id for t in project.tasks if t.id in exits
        for c in project.mandatory_criteria(t)
    }
    on_exits = [
        a.id for a in opened
        if exits & set(a.tasks) or exits & set(a.references) or exit_criteria & set(a.references)
    ]
    guidance = [
        {"response": r.id, "task": r.target}
        for r in project.responses
        if r.action == "GUIDE" and project.task(r.target) is not None and not project.task(r.target).done
    ]
    owner_blocked = [
        {"task": o.subject, "holders": o.holders, "unblock": o.unblock}
        for o in result.obstacles if o.type == "DEPENDENCY_BLOCKER" and o.actor == "owner"
    ]
    return {
        "open": [a.id for a in opened],
        "openHigh": [a.id for a in opened if a.impact == "HIGH"],
        "openPermission": [a.id for a in opened if a.permissions],
        "openOnCurrentWork": on_current,
        "awaitingReconciliation": awaiting_reconciliation(project),
        "openOnDoneTasks": on_done,
        "passRestingOnOpen": resting,
        "openOnExits": on_exits,
        "guidanceOnOpenTasks": guidance,
        "ownerHeld": [t.id for t in project.tasks if t.owner_held and not t.done],
        "waitingOnOwner": owner_blocked,
    }


def events_for(project: Project, task_id: str) -> list[str]:
    """Trace events that originate in, or name, this task."""
    return [e.id for e in project.events if e.task == task_id or task_id in e.affects]


def unresolved_failures(project: Project) -> list[str]:
    """Failed events that no successful retry, direct or chained, resolved."""
    retries: dict[str, list] = {}
    for event in project.events:
        if event.retry_of:
            retries.setdefault(event.retry_of, []).append(event)

    def resolved(event_id: str, seen: frozenset[str]) -> bool:
        for retry in retries.get(event_id, []):
            if retry.id in seen:
                continue
            if retry.outcome == "success" or resolved(retry.id, seen | {retry.id}):
                return True
        return False

    return [e.id for e in project.events if e.failed and not resolved(e.id, frozenset({e.id}))]


def _order(project: Project) -> dict[str, int]:
    return {task.id: index for index, task in enumerate(project.tasks)}


def _in_flight(project: Project, result: Report) -> list[str]:
    """Execution work in progress, most relevant first.

    Current phase, then critical-path position, then the phase exit authority,
    then authored order. Operations work in progress is never listed here,
    however recent or busy it is.
    """
    current = project.phase(project.current_phase or "")
    path = {task_id: index for index, task_id in enumerate(result.critical_path)}
    order = _order(project)
    wip = [t for t in project.execution_tasks if t.status == "WIP"]
    return [
        t.id
        for t in sorted(
            wip,
            key=lambda t: (
                not (current and t.phase == current.id),
                path.get(t.id, len(path)),
                not (current and current.exit_authority == t.id),
                order[t.id],
            ),
        )
    ]


def _verifiers(project: Project, gate) -> list:
    """Open tasks whose contract a gate is verified by."""
    refs = set(gate.verified_by)
    found = []
    for task in project.tasks:
        if task.done or not task.contract:
            continue
        contract = project.contracts.get(task.contract)
        if contract and (contract.id in refs or any(c.id in refs for c in contract.criteria)):
            found.append(task)
    return found


def _next_gate(project: Project, result: Report) -> dict[str, object] | None:
    """What stands between the current phase and its exit."""
    current = project.phase(project.current_phase or "")
    if current is None:
        return None
    authority = project.task(current.exit_authority or "")
    gates = []
    external: dict[str, dict[str, str]] = {}
    for gate in project.gates:
        if not any(entry.split()[:1] == [current.id] for entry in gate.blocks if entry.split()):
            continue
        verifiers = _verifiers(project, gate)
        for task in verifiers:
            if not task.execution:
                external[task.id] = {"id": task.id, "domain": task.domain, "via": gate.id}
        gates.append({
            "id": gate.id,
            "status": gate.status,
            "ready": gate.status == "GREEN" and not verifiers,
            "openVerifiers": [{"id": t.id, "domain": t.domain} for t in verifiers],
        })
    unmet_criteria: list[str] = []
    if authority is not None:
        unmet_criteria = unmet(project, authority)
        for dependency in _dependency_blockers(project, authority):
            known = project.task(dependency)
            if known is not None and not known.execution:
                external.setdefault(dependency, {"id": dependency, "domain": known.domain, "via": authority.id})
    unfinished = [t.id for t in project.execution_in(current.id) if not t.done]
    return {
        "phase": current.id,
        "exitAuthority": current.exit_authority,
        "exitAuthorityStatus": authority.status if authority else None,
        "unmetCriteria": unmet_criteria,
        "unfinishedExecution": unfinished,
        "gates": gates,
        "externalBlockers": [external[k] for k in sorted(external)],
        "ready": bool(
            gates is not None
            and all(g["ready"] for g in gates)
            and not external
            and all(t == current.exit_authority for t in unfinished)
        ),
    }


def _main_blocker(project: Project, result: Report) -> dict[str, object] | None:
    """The unresolved dependency most directly obstructing important execution.

    A blocked task is not a blocker, and work in progress with unmet criteria
    is work, not an obstruction. What is chosen is a dependency that an
    important execution task waits on, examined in this order: the current
    phase's exit (its authority and its red gates), the critical path, the rest
    of the current phase, then the rest of execution. The dependency may be an
    operations task; it keeps its domain (ADR-045).
    """
    current = project.phase(project.current_phase or "")
    order = _order(project)
    phase_rank = {p.id: i for i, p in enumerate(project.phases)}
    blocked = set(result.blocked)
    path = set(result.critical_path)

    def obstruction(task, reason: str) -> dict[str, object] | None:
        if task is None or task.done:
            return None
        open_dependencies = [
            d for d in task.dependencies
            if project.task(d) is not None and not project.task(d).done
        ]
        if not open_dependencies:
            return None
        # What can be acted on now comes first, and among that, the work on the
        # critical path; declaration order breaks ties.
        chosen = sorted(open_dependencies, key=lambda d: (
            d in blocked, d not in path, task.dependencies.index(d),
        ))[0]
        blocker = project.task(chosen)
        return {
            "kind": "TASK",
            "id": chosen,
            "title": blocker.title,
            "domain": blocker.domain,
            "status": blocker.status,
            "blocking": task.id,
            "blockingTitle": task.title,
            "reason": reason,
            "detail": f"{task.id} waits on {chosen}",
        }

    if current is not None:
        found = obstruction(project.task(current.exit_authority or ""), "phase exit")
        if found:
            return found
        for gate in (result.next_gate or {}).get("gates", []):
            if gate["status"] != "GREEN":
                return {
                    "kind": "GATE", "id": gate["id"], "title": gate["id"],
                    "domain": "execution", "status": gate["status"],
                    "blocking": current.id, "blockingTitle": current.name,
                    "reason": "phase exit",
                    "detail": f"{gate['id']} is {gate['status']}",
                }
    for task_id in result.critical_path:
        found = obstruction(project.task(task_id), "critical path")
        if found:
            return found
    candidates = [t for t in project.execution_tasks if not t.done]
    candidates.sort(key=lambda t: (
        not (current and t.phase == current.id),
        phase_rank.get(t.phase, len(phase_rank)),
        order[t.id],
    ))
    for task in candidates:
        reason = "current phase" if current and task.phase == current.id else "execution"
        found = obstruction(task, reason)
        if found:
            return found
    return None


def _execution_failures(project: Project) -> list[dict[str, object]]:
    """Execution tasks with a failed criterion or a failed event against them,
    each with the operational evidence that names it."""
    failures = []
    for task in project.execution_tasks:
        contract = project.contracts.get(task.contract or "")
        failed_criteria = [c.id for c in (contract.criteria if contract else []) if c.state == "FAIL"]
        related = [e for e in project.events if e.task == task.id or task.id in e.affects]
        failed_events = [e.id for e in related if e.failed]
        if failed_criteria or failed_events:
            failures.append({
                "task": task.id,
                "failedCriteria": failed_criteria,
                "failedEvents": failed_events,
                "relatedEvents": [e.id for e in related],
            })
    return failures


def _blocks(gate, phase_id: str) -> bool:
    """A gate blocks a phase when its Blocks entry names that phase exactly.

    Substring matching is wrong here: a gate blocking `P11 exit` would
    otherwise also block `P1`.
    """
    if gate.status == "GREEN":
        return False
    return any(entry.split()[:1] == [phase_id] for entry in gate.blocks if entry.split())


def explain(project: Project, task_id: str) -> dict[str, object]:
    """Everything decidable about one task, with no model in the loop."""
    task = project.task(task_id)
    if task is None:
        raise KeyError(task_id)
    result = report(project)
    contract = project.contracts.get(task.contract or "")
    return {
        "id": task.id,
        "title": task.title,
        "lineage": lineage(project, task),
        "phase": task.phase,
        "module": task.module,
        "domain": task.domain,
        "domainSource": task.domain_source,
        "status": task.status,
        "validation": task.validation,
        "owner": task.owner,
        "dependencies": [
            {
                "id": dependency,
                "done": bool(
                    project.task(dependency) and project.task(dependency).done
                ),
            }
            for dependency in task.dependencies
        ],
        "blocks": result.downstream.get(task.id, []),
        "acceptance": [
            {"id": c.id, "state": c.state, "class": c.evidence_class, "text": c.text}
            for c in (contract.criteria if contract else [])
        ],
        "invariants": [c.id for c in project.invariants_for(task)],
        "blockers": [o.as_json() for o in result.obstacles if o.subject == task.id],
        "externalBlockers": result.external_blockers.get(task.id, []),
        "events": events_for(project, task.id),
        "debt": [d.id for d in project.debts_for(task.id)],
        "evidence": task.evidence,
        "decisions": task.decisions,
        "schedule": task.schedule.as_json(),
        "source": task.source.as_json(),
    }


def _relevant(narrative: str, task_id: str) -> str | None:
    """Return project narrative only when it actually concerns this task.

    `INTENT.md` and `HANDOFF.md` describe whatever is in flight, which is
    usually something else. Pasting them into every packet hands the reader
    statements that contradict the task it was given, and a cold agent cannot
    tell which to believe. Silence is better than a confident irrelevance.
    """
    return narrative if narrative and task_id in narrative else None


def lineage(project: Project, task) -> dict[str, object]:
    """Thesis -> phase -> module for one task, as authored (ADR-035)."""
    module = project.module(task.module or "")
    return {
        "thesis": project.thesis.statement or None,
        "phase": task.phase or None,
        "module": (
            {"id": module.id, "name": module.name, "outcome": module.outcome}
            if module else None
        ),
    }


def _pointer(source) -> str:
    """A `file#anchor` pointer into the chronicle, as INDEX.md writes them."""
    if source.file.endswith(f"/{source.anchor}.md"):
        return source.file  # an ADR is its own file
    return f"{source.file}#{source.anchor}"


def orientation(project: Project) -> dict[str, object]:
    """What a fresh agent needs before choosing a task, from the report alone.

    Every task named here is one the report already names; nothing is
    suggested that the chronicle and its deterministic rules do not say.
    """
    result = report(project)
    execution = {t.id for t in project.execution_tasks}
    current = project.phase(project.current_phase or "")

    def brief(task_id: str) -> dict[str, object]:
        task = project.task(task_id)
        return {"id": task.id, "title": task.title, "status": task.status,
                "authority": _pointer(task.source)}

    return {
        "packetFor": None,
        "project": project.name,
        "thesis": project.thesis.as_json(),
        "phase": (
            {"id": current.id, "status": current.status,
             "progress": str(result.phase_progress[current.id]),
             "authority": _pointer(current.source)}
            if current else None
        ),
        "inFlight": [brief(t) for t in result.in_flight],
        "operationsInFlight": [brief(t) for t in result.wip if t not in execution],
        "ready": [brief(t) for t in result.ready if t in execution],
        # A count and the first few, critical path first; the full list is
        # `praukron status`. Dumping every blocked task cost more than the
        # rest of the packet on a real project (ADR-059).
        "blocked": _first(result, [t for t in result.blocked if t in execution], brief),
        "criticalPath": result.critical_path,
        "mainBlocker": result.main_blocker,
        "nextGate": result.next_gate,
        "authority": {
            "root": f"{layout.AUTHORITY_DIR}/",
            "read": ["INDEX.md", "INTENT.md", "HANDOFF.md"],
        },
    }


_BLOCKED_SHOWN = 10
_RECENT_EVENTS = 5


def _first(result: Report, task_ids: list[str], brief) -> dict[str, object]:
    path = {task_id: index for index, task_id in enumerate(result.critical_path)}
    ordered = sorted(task_ids, key=lambda t: (path.get(t, len(path)), task_ids.index(t)))
    return {
        "count": len(task_ids),
        "first": [brief(t) for t in ordered[:_BLOCKED_SHOWN]],
        "more": "praukron status" if len(task_ids) > _BLOCKED_SHOWN else None,
    }


def _event_summary(project: Project, task_id: str) -> dict[str, object]:
    """Counts, every unresolved failure in full, and the most recent ids."""
    related = set(events_for(project, task_id))
    events = [e for e in project.events if e.id in related]
    unresolved = set(unresolved_failures(project)) & related
    return {
        "total": len(events),
        "failed": sum(1 for e in events if e.failed),
        "unresolved": len(unresolved),
        "unresolvedFailures": [e.as_json() for e in events if e.id in unresolved],
        "recent": [e.id for e in events[-_RECENT_EVENTS:]],
        "more": f"praukron explain {task_id}" if len(events) > _RECENT_EVENTS else None,
    }


def _problems(project: Project, task) -> list[str]:
    """Every reference in a task that does not resolve, so none is dropped."""
    problems = [
        f"dependency {d} is not a known task"
        for d in task.dependencies if project.task(d) is None
    ]
    known = {d.id for d in project.decisions}
    problems += [f"decision {d} has no record" for d in task.decisions
                 if d.startswith("ADR-") and d not in known]
    if task.module and project.module(task.module) is None:
        problems.append(f"module {task.module} is not in MODULES.md")
    elif task.phase != "P-NONE" and project.phase(task.phase) is None:
        problems.append(f"phase {task.phase} is not in PHASES.md")
    contract = project.contracts.get(task.contract or "")
    if not task.contract:
        problems.append("no acceptance contract is named")
    elif contract is None:
        problems.append(f"contract {task.contract} is not in ACCEPTANCE.md")
    else:
        problems += [f"inherited contract {name} is not in ACCEPTANCE.md"
                     for name in contract.inherits if name not in project.contracts]
    return problems


def context(project: Project, task_id: str | None, role: str = "builder") -> dict[str, object]:
    """The minimal packet an agent needs to start work on one task."""
    if task_id is None:
        return orientation(project)
    task = project.task(task_id)
    if task is None:
        raise KeyError(task_id)
    contract = project.contracts.get(task.contract or "")
    phase = project.phase(task.phase)
    report_now = report(project)
    blockers = [o.as_json() for o in report_now.obstacles if o.subject == task_id]
    decisions = [d for d in project.decisions if d.id in task.decisions]
    debts = [
        d for d in project.debts
        if d in project.debts_for(task_id)
        or any(adr in d.introduced_by for adr in task.decisions)
    ]
    # The records to open, in reading order, when the packet is not enough.
    module = project.module(task.module or "")
    authority = [
        _pointer(task.source),
        *([_pointer(contract.source)] if contract else []),
        *([_pointer(module.source)] if module else []),
        *([_pointer(phase.source)] if phase else []),
        *[_pointer(d.source) for d in decisions],
        *[_pointer(d.source) for d in debts],
    ]
    assumptions = project.assumptions_for(task_id)
    waiting = {w["assumption"] for w in awaiting_reconciliation(project)}
    open_dependencies = [d for d in task.dependencies if project.task(d) and not project.task(d).done]
    targets = {task_id, *(a.id for a in assumptions), *open_dependencies}
    responses = [r for r in project.responses if r.target in targets]
    authority += [_pointer(a.source) for a in assumptions]
    authority += [_pointer(r.source) for r in responses]
    # The coordinator's brief for this task and the conventions it rests on,
    # so a relaunched agent needs nothing outside the chronicle (ADR-075).
    brief = project.brief_for(task_id)
    conventions = [
        c for c in (project.convention(i) for i in ((brief.conventions or []) if brief else []))
        if c is not None and c.status == "ACTIVE" and c.applies_to(role)
    ]
    authority += [_pointer(b.source) for b in ([brief] if brief else []) + conventions]
    authority.append("HANDOFF.md")
    trail = lineage(project, task)
    # The orientation packet and INDEX.md carry the thesis; a task packet
    # points to it rather than repeating it (ADR-059).
    trail = {"thesisRef": _pointer(project.thesis.source).split("#")[0], **{
        k: v for k, v in trail.items() if k != "thesis"}}
    packet: dict[str, object] = {
        "role": role,
        "packetFor": task_id,
        "lineage": trail,
        "intent": _relevant(project.intent, task_id),
        "phase": (
            {"id": phase.id, "outcome": phase.outcome, "status": phase.status}
            if phase
            else {"id": task.phase}
        ),
        "task": {
            "id": task.id,
            "title": task.title,
            "module": task.module,
            "domain": task.domain,
            "domainSource": task.domain_source,
            "implementation": {"files": task.files, "symbols": task.symbols},
            "status": task.status,
            "validation": task.validation,
            "dependencies": [
                {
                    "id": dependency,
                    "done": bool(
                        project.task(dependency) and project.task(dependency).done
                    ),
                    "status": (
                        project.task(dependency).status
                        if project.task(dependency) else "MISSING"
                    ),
                }
                for dependency in task.dependencies
            ],
            # What finishing this task makes startable, with its status.
            "unlocks": [
                {"id": d, "status": project.task(d).status}
                for d in report_now.downstream.get(task.id, []) if project.task(d) is not None
            ],
            "closed": task.done,
            # The chronicle's working rule: a task is claimed by marking it
            # WIP with its owner and claim date. Holder and date are as
            # recorded; nothing here assigns or locks anything.
            "claim": {
                "active": task.status == "WIP",
                "holder": task.owner,
                "claimed": task.claimed,
            },
        },
        "authority": {"root": f"{layout.AUTHORITY_DIR}/", "read": authority},
        "problems": _problems(project, task),
        "blockers": blockers,
        "acceptance": [
            {"id": c.id, "text": c.text, "class": c.evidence_class, "state": c.state}
            for c in (contract.criteria if contract else [])
        ],
        "invariants": [
            {"id": inherited.id, "criteria": [c.text for c in inherited.criteria]}
            for inherited in project.invariants_for(task)
        ],
        "decisions": [
            {"id": d.id, "title": d.title, "origin": d.origin} for d in decisions
        ],
        "evidence": task.evidence,
        # Operations work this task waits on, kept in its own domain.
        "externalBlockers": [
            {"id": o, "title": project.task(o).title, "domain": project.task(o).domain,
             "status": project.task(o).status}
            for o in report_now.external_blockers.get(task_id, [])
        ],
        "events": _event_summary(project, task_id),
        # Debt this task introduced or repays, and debt its decisions created.
        "debt": [d.as_json() for d in debts],
        "handoff": _relevant(project.handoff, task_id),
        # What this agent may assume, and whether it notifies when it may not.
        "policy": project.policy.as_json(),
        # Provisional choices this task rests on. None of them is authority.
        "assumptions": [
            {
                "id": a.id, "title": a.title, "status": a.status, "impact": a.impact,
                "assumption": a.assumption,
                "permission": bool(a.permissions),
                **({"permissions": a.permissions} if a.permissions else {}),
                "awaitingReconciliation": a.id in waiting,
                "authority": _pointer(a.source),
            }
            for a in assumptions
        ],
        # The owner's words about this task, its assumptions, or what blocks
        # it, verbatim. Act on them; never edit them (ADR-054).
        "responses": [
            {**{k: v for k, v in r.as_json().items() if k != "source"}, "authority": _pointer(r.source)}
            for r in responses
        ],
        # What the coordinator adds to the contract, verbatim. The contract,
        # invariants and ADRs above win where they differ (ADR-075).
        "brief": brief.as_json() if brief else None,
        "conventions": [c.as_json() for c in conventions],
    }
    if task.done:
        packet["note"] = (
            f"{task.id} is already DONE. This packet is for review or audit, "
            "not for fresh implementation."
        )
    if role == "reviewer":
        packet["findingClasses"] = {
            "blocking": [
                "ACCEPTANCE_FAILURE",
                "INVARIANT_VIOLATION",
                "REGRESSION",
                "MISSING_EVIDENCE",
            ],
            "informative": [
                "RISK",
                "MAINTAINABILITY",
                "ARCHITECTURE_PREFERENCE",
                "STYLE",
                "FUTURE_IMPROVEMENT",
            ],
        }
    return packet
