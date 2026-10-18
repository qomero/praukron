"""Mermaid renderers.

Generated views carry no state that is not derivable from authority. The two
timeline renderers are deliberately different: one expresses ordering, the
other expresses dates, and only real metadata produces the second.
"""

from __future__ import annotations

import re

from .analytics import Report
from .model import Project

_SHAPE = {
    "DONE": ("[", "]"),
    "WIP": ("([", "])"),
    "BLOCKED": ("{{", "}}"),
    "TODO": ("(", ")"),
}


def node_id(task_id: str) -> str:
    """A Mermaid node identifier.

    Task IDs only ever contain hyphens, but a gate is identified by its heading,
    so `Gate A` reaches here with a space in it. Mermaid ends a node identifier
    at the first space, which makes the whole diagram unparseable rather than
    only that node wrong.
    """
    return re.sub(r"[^0-9A-Za-z_]", "_", task_id)


def _label(text: str) -> str:
    return text.replace('"', "'")


def task_graph(project: Project, report: Report) -> str:
    """Execution grouped by phase, operations in one group of its own.

    Operations tasks keep their shape for status but carry a distinct class,
    so a cross-domain edge reads as one. Trace events are never drawn: the
    graph is the plan, not a record of every action (ADR-045).
    """
    lines = ["flowchart LR"]

    def node(task) -> str:
        open_bracket, close_bracket = _SHAPE.get(task.status, _SHAPE["TODO"])
        return (
            f"        {node_id(task.id)}{open_bracket}"
            f'"{_label(task.id)}<br/>{_label(task.title)}"{close_bracket}'
        )

    for phase in [*project.phases, None]:
        phase_id = phase.id if phase else "P-NONE"
        tasks = project.execution_in(phase_id)
        if not tasks:
            continue
        title = f"{phase.id} {phase.name}" if phase else "Phase-independent"
        lines.append(f'    subgraph {node_id(phase_id)}["{_label(title)}"]')
        lines.extend(node(task) for task in tasks)
        lines.append("    end")
    operations = project.operations_tasks
    if operations:
        lines.append('    subgraph project_operations["Project operations"]')
        lines.extend(node(task) for task in operations)
        lines.append("    end")
    for task in project.tasks:
        for dependency in task.dependencies:
            if project.task(dependency):
                lines.append(f"    {node_id(dependency)} --> {node_id(task.id)}")
    lines.append("    classDef done fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20;")
    lines.append("    classDef wip fill:#fff8e1,stroke:#f9a825,color:#7f6000;")
    lines.append("    classDef blocked fill:#ffebee,stroke:#c62828,color:#8e0000;")
    lines.append(
        "    classDef ops fill:#eef1f6,stroke:#5f6f86,stroke-dasharray:4 3,color:#2b3a4f;"
    )
    execution = project.execution_tasks
    for status, style in (("DONE", "done"), ("WIP", "wip")):
        members = [node_id(t.id) for t in execution if t.status == status]
        if members:
            lines.append(f"    class {','.join(members)} {style};")
    execution_ids = {t.id for t in execution}
    blocked = [node_id(task_id) for task_id in report.blocked if task_id in execution_ids]
    if blocked:
        lines.append(f"    class {','.join(blocked)} blocked;")
    if operations:
        lines.append(f"    class {','.join(node_id(t.id) for t in operations)} ops;")
    return "\n".join(lines) + "\n"


def critical_nodes(project: Project, report: Report) -> list[dict]:
    """Shared view data for the native drawing and Mermaid export."""
    return [{"id": tid, "title": project.task(tid).title if project.task(tid) else tid,
             "status": project.task(tid).status if project.task(tid) else "UNKNOWN"}
            for tid in report.critical_path]


def phase_nodes(project: Project, report: Report) -> list[dict]:
    return [{"id": p.id, "title": p.name, "status": p.status,
             "progress": str(report.phase_progress[p.id])} for p in project.phases]


def gate_data(project: Project) -> tuple[list[dict], list[tuple[str, str]]]:
    nodes = [{"id": g.id, "title": g.name, "status": g.status, "kind": "gate"}
             for g in project.gates]
    nodes.extend({"id": p.id, "title": "exit", "status": p.status, "kind": "phase"}
                 for p in project.phases)
    edges = [(g.id, p.id) for p in project.phases for g in project.gates
             if any(entry.split()[:1] == [p.id] for entry in g.blocks if entry.split())]
    return nodes, edges


def critical_path(project: Project, report: Report) -> str:
    if not report.critical_path:
        return "flowchart LR\n    none[\"No open work\"]\n"
    lines = ["flowchart LR"]
    nodes = critical_nodes(project, report)
    for item in nodes:
        task_id, title = item["id"], _label(item["title"])
        lines.append(f'    {node_id(task_id)}["{task_id}<br/>{title}"]')
    for left, right in zip(nodes, nodes[1:]):
        lines.append(f"    {node_id(left['id'])} --> {node_id(right['id'])}")
    return "\n".join(lines) + "\n"


def phase_flow(project: Project, report: Report) -> str:
    lines = ["flowchart TD"]
    nodes = phase_nodes(project, report)
    for phase in nodes:
        lines.append(
            f'    {node_id(phase["id"])}["{phase["id"]} {_label(phase["title"])}<br/>'
            f'{phase["progress"]} · {phase["status"]}"]'
        )
    for left, right in zip(nodes, nodes[1:]):
        lines.append(f"    {node_id(left['id'])} --> {node_id(right['id'])}")
    return "\n".join(lines) + "\n"


def gate_graph(project: Project) -> str:
    lines = ["flowchart LR"]
    nodes, edges = gate_data(project)
    for gate in (n for n in nodes if n["kind"] == "gate"):
        mark = "✓" if gate["status"] == "GREEN" else "✗"
        lines.append(
            f'    {node_id(gate["id"])}{{"{_label(gate["id"])}<br/>'
            f'{_label(gate["title"])} {mark}"}}'
        )
    for phase in (n for n in nodes if n["kind"] == "phase"):
        lines.append(f'    {node_id(phase["id"])}["{phase["id"]} exit"]')
        for left, right in edges:
            if right == phase["id"]:
                lines.append(f"    {node_id(left)} --> {node_id(right)}")
    lines.append("    classDef green fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20;")
    lines.append("    classDef red fill:#ffebee,stroke:#c62828,color:#8e0000;")
    for status, style in (("GREEN", "green"), ("RED", "red")):
        members = [node_id(g.id) for g in project.gates if g.status == status]
        if members:
            lines.append(f"    class {','.join(members)} {style};")
    return "\n".join(lines) + "\n"


def dependency_bands(project: Project) -> dict[int, list[str]]:
    """Ordering only; completed dependencies do not add a step."""
    depth: dict[str, int] = {}

    def level(task_id: str, seen: frozenset[str]) -> int:
        """Bands count open work only, so the first startable task is step 1."""
        if task_id in depth:
            return depth[task_id]
        task = project.task(task_id)
        if task is None or task_id in seen:
            return 0
        parents = [
            level(dependency, seen | {task_id})
            for dependency in task.dependencies
            if project.task(dependency) and not project.task(dependency).done
        ]
        depth[task_id] = max(parents, default=-1) + 1
        return depth[task_id]

    for task in project.tasks:
        level(task.id, frozenset())

    bands: dict[int, list[str]] = {}
    for task in project.tasks:
        if not task.done:
            bands.setdefault(depth.get(task.id, 0), []).append(task.id)
    return dict(sorted(bands.items()))


def dependency_timeline(project: Project, report: Report) -> str:
    """Ordering only. Every task sits in a depth band; no date is implied."""
    bands = dependency_bands(project)
    lines = [
        "---",
        "displayMode: compact",
        "---",
        "gantt",
        "    title Dependency ordering, not a calendar",
        "    dateFormat X",
        "    axisFormat step %s",
    ]
    for band in sorted(bands):
        lines.append(f"    section Step {band + 1}")
        for task_id in bands[band]:
            task = project.task(task_id)
            marker = "active, " if task and task.status == "WIP" else ""
            lines.append(f"    {task_id} : {marker}{band}, {band + 1}")
    if not bands:
        lines.append("    section Complete")
        lines.append("    No open work : 0, 1")
    return "\n".join(lines) + "\n"


def scheduled_groups(project: Project, report: Report) -> dict[str, list[dict]]:
    by_phase: dict[str, list[dict]] = {}
    for entry in report.scheduled:
        task = project.task(entry["id"])
        by_phase.setdefault(task.phase if task else "P-NONE", []).append(entry)
    return by_phase


def calendar_gantt(project: Project, report: Report) -> str:
    """Dates only where real metadata exists. Nothing here is inferred.

    Unscheduled work is counted in the title and never given a row. A Mermaid
    task or milestone without an explicit date inherits the previous entry's
    end date, so putting unscheduled work on this chart would hand it a
    calendar position it has not earned — the exact fabrication this pair of
    renderers exists to prevent.
    """
    unscheduled = len(report.unscheduled)
    tail = (
        f" ({unscheduled} unscheduled "
        f"{'task' if unscheduled == 1 else 'tasks'} not shown)"
        if unscheduled
        else ""
    )
    if not report.scheduled:
        # A gantt with no dated rows would still draw a date axis, so say it
        # in a diagram that has no axis at all.
        return (
            "flowchart LR\n"
            f'    none["No task carries schedule metadata.<br/>'
            f'{unscheduled} open {"task is" if unscheduled == 1 else "tasks are"} '
            'unscheduled."]\n'
        )
    lines = ["gantt", f"    title Scheduled work{tail}", "    dateFormat YYYY-MM-DD"]
    for phase_id, entries in scheduled_groups(project, report).items():
        lines.append(f"    section {phase_id}")
        for entry in entries:
            end_or_estimate = entry.get("end") or entry.get("estimate")
            lines.append(f"    {entry['id']} : {entry['start']}, {end_or_estimate}")
    return "\n".join(lines) + "\n"


def render_all(project: Project, report: Report) -> dict[str, str]:
    return {
        "task-graph.mmd": task_graph(project, report),
        "critical-path.mmd": critical_path(project, report),
        "phases.mmd": phase_flow(project, report),
        "gates.mmd": gate_graph(project),
        "timeline.mmd": dependency_timeline(project, report),
        "gantt.mmd": calendar_gantt(project, report),
    }
