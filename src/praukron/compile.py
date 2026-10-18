"""Compile authored documents into one normalized project.

The compiler reads only the chronicle and writes only the compiled directory.
It never modifies authority, and its output is disposable: deleting the
compiled directory and running again reproduces it exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import analytics, domain, index, views
from . import layout
from .layout import AUTHORITY_DIR, COMPILED_DIR
from .model import Project, event_order
from .snapshot import Snapshot, SnapshotPath
from .parse import (
    ParseError,
    parse_acceptance,
    parse_change_requests,
    parse_assumptions,
    parse_coordination,
    parse_decisions,
    parse_modules,
    parse_phases,
    parse_project_name,
    parse_responses,
    parse_tasks,
    parse_thesis,
    parse_debt,
    parse_trace,
    read_text,
)


class LayoutError(Exception):
    """Raised when a repository has no authored chronicle to compile."""


def locate(start: Path | None = None) -> Path:
    """Find the repository root by walking up to the authored directory."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / AUTHORITY_DIR / "TASKS.md").is_file():
            return candidate
    raise LayoutError(
        f"No {AUTHORITY_DIR}/TASKS.md found in {current} or any parent directory."
    )


def archived(authority: Path, phases: list) -> list[str]:
    """Archived phase directories, in PHASES.md order, then any others by name."""
    base = authority / layout.ARCHIVE_DIR
    found = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    order = [p.id for p in phases]
    return sorted(found, key=lambda d: (order.index(d) if d in order else len(order), d))


def _tasks_and_contracts(authority: Path | SnapshotPath, phases: list, modules: list) -> tuple[list, dict, dict[str, bytes]]:
    """The working files and every archived phase's, read as one authority
    (ADR-067). Archived records come first, in phase order."""
    tasks: list = []
    contracts: dict = {}
    record_bytes: dict[str, bytes] = {}

    def captured(name: str) -> SnapshotPath:
        # Parse and later hash this read, even if a writer replaces the live file.
        data = (authority / name).read_bytes()
        record_bytes[name] = data
        return Snapshot({name: data}, frozenset({""})).path(name)

    files = [f"{layout.ARCHIVE_DIR}/{d}/" for d in archived(authority, phases)] + [""]
    for prefix in files:
        # The working files are required; an archived phase may hold either.
        name = f"{prefix}TASKS.md"
        if not prefix or (authority / name).is_file():
            tasks += parse_tasks(captured(name), modules, name)
        name = f"{prefix}ACCEPTANCE.md"
        if prefix and not (authority / name).is_file():
            continue
        for contract_id, contract in parse_acceptance(captured(name), name).items():
            if contract_id in contracts:
                raise ParseError(name, contract_id, f"duplicate contract; also in {contracts[contract_id].source.file}")
            contracts[contract_id] = contract
    return tasks, contracts, record_bytes


def _change_requests(authority: Path | SnapshotPath, phases: list) -> list:
    """Acceptance Change Requests from every archived and the working ACCEPTANCE.md."""
    found: list = []
    for prefix in [f"{layout.ARCHIVE_DIR}/{d}/" for d in archived(authority, phases)] + [""]:
        name = f"{prefix}ACCEPTANCE.md"
        if (authority / name).is_file():
            found += parse_change_requests(authority / name, name)
    return found


def _archived_records(authority: Path | SnapshotPath, phases: list) -> tuple[list, list, list]:
    """Read lifecycle records from archive directories and the working files."""
    assumptions: list = []
    debts: list = []
    events: list = []
    files = [f"{layout.ARCHIVE_DIR}/{d}/" for d in archived(authority, phases)] + [""]
    for prefix in files:
        for filename, parser, collected in (
            ("ASSUMPTIONS.md", parse_assumptions, assumptions),
            ("TECH_DEBT.md", parse_debt, debts),
            ("TRACE.md", parse_trace, events),
        ):
            name = f"{prefix}{filename}"
            path = authority / name
            if path.is_file():
                collected.extend(parser(path, name))
    for label, records in (("assumption", assumptions), ("debt", debts), ("event", events)):
        seen = {}
        for record in records:
            if record.id in seen and record.source.file != seen[record.id].source.file:
                raise ParseError(record.source.file, record.id,
                                 f"duplicate {label}; also in {seen[record.id].source.file}")
            seen.setdefault(record.id, record)
    return assumptions, debts, _chronological(events)


def _chronological(events: list) -> list:
    """Order events by their own time, then the number in their id, then the id.

    Where an event lives (a working file or an archive) must not decide which events
    are "the last N" in any view, so the order cannot follow load order. Ids are
    unique, so the order is total and file order never decides. An event without a
    time sorts before every timed one.
    """
    return sorted(events, key=lambda event: event_order(event.id, event.time))


def load(root: Path, *, authority: Path | SnapshotPath | None = None) -> Project:
    """Load disk authority, or a captured read-only view through the same parsers."""
    if authority is None:
        authority = root / AUTHORITY_DIR
    from . import mission
    phases, gates, milestones = parse_phases(authority / "PHASES.md")
    modules = parse_modules(authority / "MODULES.md")
    tasks, contracts, record_bytes = _tasks_and_contracts(authority, phases, modules)
    assumptions, debts, events = _archived_records(authority, phases)
    # Coordination records never move to an archive (ADR-075).
    conventions, briefs = parse_coordination(authority / "COORDINATION.md")
    # A project that names itself compiles the same in every checkout. One that
    # does not falls back to its directory, which is where the name used to
    # come from always.
    project = Project(
        name=parse_project_name(authority / "PHASES.md") or root.name,
        root=str(root),
        current_phase=None,
        thesis=parse_thesis(authority / "THESIS.md"),
        phases=phases,
        modules=modules,
        tasks=tasks,
        contracts=contracts,
        record_bytes=record_bytes,
        gates=gates,
        milestones=milestones,
        decisions=parse_decisions(authority / "ADR"),
        intent=read_text(authority / "INTENT.md"),
        handoff=read_text(authority / "HANDOFF.md"),
        journal=mission.journal(read_text(authority / "JOURNAL.md")) + [
            entry for phase_id in archived(authority, phases)
            for entry in mission.journal(read_text(authority / layout.ARCHIVE_DIR / phase_id / "JOURNAL.md"),
                                         f"{layout.ARCHIVE_DIR}/{phase_id}/JOURNAL.md")],
        events=events,
        debts=debts,
        assumptions=assumptions,
        responses=parse_responses(authority / "RESPONSES.md"),
        conventions=conventions,
        briefs=briefs,
        change_requests=_change_requests(authority, phases),
    )
    domain.resolve(project)
    # Reuse these loaded diagnostics in validate/status and structured writes;
    # a captured authority must never be checked against later live disk.
    from .validate import journal_findings
    project.journal_validation = journal_findings(
        project, authority.snapshot if isinstance(authority, SnapshotPath) else None)
    # A phase whose work is finished but whose exit has not been accepted is
    # still where the project stands. Reporting "none" would lose that.
    for status in ("ACTIVE", "EXIT_PENDING"):
        standing = [phase for phase in project.phases if phase.status == status]
        if standing:
            project.current_phase = standing[-1].id
            break
    return project


def as_json(project: Project) -> dict[str, object]:
    """The normalized shape. Every object keeps provenance where practical."""
    report = analytics.report(project)
    from . import mission
    data = {
        "project": {
            "name": project.name,
            "currentPhase": project.current_phase,
            "generator": "praukron compile",
            # Which version produced this, so a stale installation is visible
            # to someone who never reinstalls (ADR-031).
            "generatorVersion": layout.version(),
            "authority": AUTHORITY_DIR,
        },
        "thesis": project.thesis.as_json(),
        "phases": [
            {
                "id": phase.id,
                "name": phase.name,
                "outcome": phase.outcome,
                "entry": phase.entry,
                "exit": phase.exit,
                "exitAuthority": phase.exit_authority,
                "status": phase.status,
                "progress": report.phase_progress[phase.id].as_json(),
                # The earlier phase whose exit this one waits on, if open.
                "waitsOn": report.phase_waits.get(phase.id),
                "source": phase.source.as_json(),
            }
            for phase in project.phases
        ],
        # Phase -> module -> task, authored once and projected here (ADR-035).
        "modules": [
            {
                "id": module.id,
                "name": module.name,
                "phase": module.phase,
                "outcome": module.outcome,
                "tasks": [task.id for task in project.tasks_in_module(module.id)],
                "source": module.source.as_json(),
            }
            for module in project.modules
        ],
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "module": task.module,
                "phase": task.phase,
                "domain": task.domain,
                "domainSource": task.domain_source,
                "status": task.status,
                "validation": task.validation,
                "deps": task.dependencies,
                "blocks": report.downstream.get(task.id, []),
                "ac": task.contract,
                "owner": task.owner,
                "claimed": task.claimed,
                "evidence": task.evidence,
                "decisions": task.decisions,
                "schedule": task.schedule.as_json(),
                "ready": task.id in report.ready,
                "blocked": task.id in report.blocked,
                "externalBlockers": report.external_blockers.get(task.id, []),
                "events": analytics.events_for(project, task.id),
                "debt": [debt.id for debt in project.debts_for(task.id)],
                "implementation": {"files": task.files, "symbols": task.symbols},
                "authority": task.authority,
                "assumptions": [a.id for a in project.assumptions_for(task.id)],
                "source": task.source.as_json(),
            }
            for task in project.tasks
        ],
        "acceptance": {
            contract.id: {
                "title": contract.title,
                "inherits": contract.inherits,
                "evidence": contract.evidence,
                "criteria": [
                    {
                        "id": criterion.id,
                        "text": criterion.text,
                        "evidenceClass": criterion.evidence_class,
                        "state": criterion.state,
                        "evidence": criterion.evidence,
                        "source": criterion.source.as_json(),
                    }
                    for criterion in contract.criteria
                ],
                "source": contract.source.as_json(),
            }
            for contract in project.contracts.values()
            if not contract.is_global
        },
        "invariants": [
            {
                "id": contract.id,
                "title": contract.title,
                "criteria": [
                    {
                        "id": criterion.id,
                        "text": criterion.text,
                        "evidenceClass": criterion.evidence_class,
                        "state": criterion.state,
                    }
                    for criterion in contract.criteria
                ],
                "appliesTo": [
                    task.id
                    for task in project.tasks
                    if contract.id
                    in getattr(project.contracts.get(task.contract or ""), "inherits", [])
                ],
                "source": contract.source.as_json(),
            }
            for contract in project.invariants
        ],
        "gates": [
            {
                "id": gate.id,
                "name": gate.name,
                "description": gate.description,
                "blocks": gate.blocks,
                "verifiedBy": gate.verified_by,
                "status": gate.status,
                "source": gate.source.as_json(),
            }
            for gate in project.gates
        ],
        "milestones": [
            {
                "id": milestone.id,
                "text": milestone.text,
                "task": milestone.task,
                "reached": bool(
                    milestone.task
                    and project.task(milestone.task)
                    and project.task(milestone.task).done
                ),
                "source": milestone.source.as_json(),
            }
            for milestone in project.milestones
        ],
        "decisions": [
            {
                "id": decision.id,
                "title": decision.title,
                "status": decision.status,
                "supersedes": decision.supersedes,
                "affects": decision.affects,
                "origin": decision.origin,
                "evidence": decision.evidence,
                "source": decision.source.as_json(),
            }
            for decision in project.decisions
        ],
        "obstacles": [obstacle.as_json() for obstacle in report.obstacles],
        "debt": [debt.as_json() for debt in project.debts],
        # Assumptions hold no authority; responses are the owner's words as
        # written; the policy in force is read from its ADR (ADR-054, ADR-057).
        "assumptions": [assumption_json(project, a) for a in project.assumptions],
        "responses": [{**response.as_json(), "fromOwner": project.is_owner(response.by)}
                      for response in project.responses],
        "owners": {"logins": project.owners,
                   "decision": project.owners_decision.id if project.owners_decision else None},
        "policy": project.policy.as_json(),
        # Derived diagnostics; nothing here changes a status (ADR-054).
        "assumptionReport": report.assumptions,
        "criticalPath": report.critical_path,
        "schedule": {
            "scheduled": report.scheduled,
            "unscheduled": report.unscheduled,
        },
        "metrics": report.metrics(),
        # What the report leads with, selected here rather than in any
        # renderer, so every view agrees on it (ADR-045).
        "execution": {
            "inFlight": report.in_flight,
            "mainBlocker": report.main_blocker,
            "nextGate": report.next_gate,
            "externalBlockers": report.external_blockers,
            "failures": report.execution_failures,
            "workAhead": report.work_ahead,
        },
        "operations": {
            "metrics": report.operations_metrics(),
            "tasks": [t.id for t in project.operations_tasks],
            "active": [t.id for t in project.operations_tasks if t.status == "WIP"],
            "events": [e.as_json() for e in project.events],
            "unresolvedFailures": analytics.unresolved_failures(project),
        },
        "ready": report.ready,
        "blocked": report.blocked,
        "wip": report.wip,
        "handoff": {"intent": project.intent, "handoff": project.handoff},
        "journal": project.journal,
        "sources": {
            "authority": AUTHORITY_DIR,
            "documents": [
                "THESIS.md",
                "PHASES.md",
                "MODULES.md",
                "TASKS.md",
                "ACCEPTANCE.md",
                f"{layout.ARCHIVE_DIR}/",
                "ADR/",
                "INTENT.md",
                "HANDOFF.md",
                "TRACE.md",
                "TECH_DEBT.md",
                "ASSUMPTIONS.md",
                "RESPONSES.md",
                "COORDINATION.md",
            ],
        },
    }
    data["mission"] = mission.project(data)
    data["searchIndex"] = mission.search_index(data)
    return data


def assumption_json(project: Project, assumption) -> dict[str, object]:
    """One assumption as authored, with its phase and module derived from its tasks."""
    tasks = [project.task(t) for t in assumption.tasks if project.task(t) is not None]
    return {
        "id": assumption.id,
        "title": assumption.title,
        "status": assumption.status,
        "tasks": assumption.tasks,
        "phases": project.assumption_phases(assumption),
        "modules": list(dict.fromkeys(t.module for t in tasks if t.module)),
        "impact": assumption.impact,
        "recorded": assumption.recorded,
        "assumption": assumption.assumption,
        "basis": assumption.basis,
        "appliedIn": assumption.applied_in,
        "references": assumption.references,
        "permissions": assumption.permissions,
        "responses": assumption.responses,
        "reconciledBy": assumption.reconciled_by,
        "source": assumption.source.as_json(),
    }


def write(root: Path, project: Project) -> Path:
    """Write project.json, the Markdown views, and the chronicle's INDEX.md.

    INDEX.md is the one generated file inside the chronicle (ADR-047). It is
    written here and nowhere else, and nothing ever reads it as authority.
    """
    compiled = root / COMPILED_DIR
    compiled.mkdir(parents=True, exist_ok=True)
    target = compiled / "project.json"
    target.write_text(json.dumps(as_json(project), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    report = analytics.report(project)
    for name, text in views.render_all(project, report).items():
        (compiled / name).write_text(text, encoding="utf-8")
    (root / AUTHORITY_DIR / index.FILE).write_text(index.render(project, report, root), encoding="utf-8")
    return target


__all__ = [
    "AUTHORITY_DIR",
    "COMPILED_DIR",
    "LayoutError",
    "ParseError",
    "as_json",
    "load",
    "locate",
    "write",
]
