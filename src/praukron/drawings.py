"""Native, offline drawings over the same selections as the Mermaid exports."""

from __future__ import annotations

import html
import re
import textwrap
from datetime import datetime, timedelta

from . import mermaid


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def empty(message: str) -> str:
    return f'<p class="note drawing-empty">{esc(message)}</p>'


def frame(name: str, body: str) -> str:
    return (f'<div class="native-drawing" role="region" aria-label="{esc(name)}; scroll to explore" '
            f'tabindex="0">{body}</div>')


def tone(status: str) -> str:
    return {"DONE": "good", "COMPLETE": "good", "GREEN": "good",
            "WIP": "wip", "ACTIVE": "wip", "BLOCKED": "bad", "RED": "bad",
            "EXIT_PENDING": "warn"}.get(status, "todo")


def graph(name: str, key: str, nodes: list[dict], edges: list[tuple[str, str]],
          columns: list[list[str]], message: str) -> str:
    if not nodes:
        return empty(message)
    by_id = {n["id"]: n for n in nodes}
    width = max(260, max(len(n["id"]) * 9 + 32 for n in nodes))
    positions, labels = {}, {}
    for column, ids in enumerate(columns):
        y = 20
        for nid in ids:
            node = by_id[nid]
            lines = textwrap.wrap(node["title"], width=max(28, (width - 32) // 8)) or [""]
            height = 62 + 20 * len(lines)
            positions[nid] = (20 + column * (width + 72), y, height)
            labels[nid] = lines
            y += height + 48
    svg_width = 40 + len(columns) * width + max(0, len(columns) - 1) * 72
    svg_height = max(y + h + 20 for x, y, h in positions.values())
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="{key}-title {key}-desc" '
           f'width="{svg_width}" height="{svg_height}">',
           f'<title id="{key}-title">{esc(name)}</title>',
           f'<desc id="{key}-desc">' + esc('; '.join(
               f'{n["id"]}: {n["title"]}, {n["status"]}' for n in nodes)
               + '. ' + '; '.join(f'{a} → {b}' for a, b in edges)) + '</desc>',
           f'<defs><marker id="{key}-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
           'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path class="arrow-head" d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>']
    for left, right in edges:
        x1, y1, h1 = positions[left]
        x2, y2, h2 = positions[right]
        if x1 == x2:
            path = f'M {x1 + width // 2} {y1 + h1} L {x2 + width // 2} {y2 - 4}'
        else:
            middle = (x1 + width + x2) // 2
            path = (f'M {x1 + width} {y1 + h1 // 2} C {middle} {y1 + h1 // 2}, '
                    f'{middle} {y2 + h2 // 2}, {x2 - 4} {y2 + h2 // 2}')
        out.append(f'<path class="drawing-edge" data-from="{esc(left)}" data-to="{esc(right)}" '
                   f'd="{path}" marker-end="url(#{key}-arrow)"/>')
    for node in nodes:
        nid, status = node["id"], node["status"]
        x, y, height = positions[nid]
        out.append(f'<g data-node="{esc(nid)}" class="drawing-node tone-{tone(status)}">'
                   f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8"/>'
                   f'<text class="drawing-id" x="{x + 16}" y="{y + 25}">{esc(nid)}</text>')
        for i, line in enumerate(labels[nid]):
            out.append(f'<text x="{x + 16}" y="{y + 47 + i * 20}">{esc(line)}</text>')
        label = status + (f' · {node["progress"]}' if "progress" in node else '')
        out.append(f'<text class="drawing-status" x="{x + 16}" y="{y + height - 14}">{esc(label)}</text></g>')
    return frame(name, ''.join(out) + '</svg>')


def critical_path(project, report) -> str:
    nodes = mermaid.critical_nodes(project, report)
    ids = [n["id"] for n in nodes]
    return graph("Critical path · dependency order", "critical", nodes, list(zip(ids, ids[1:])),
                 [[nid] for nid in ids], "No open work on the critical path.")


def phase_flow(project, report) -> str:
    nodes = mermaid.phase_nodes(project, report)
    ids = [n["id"] for n in nodes]
    return graph("Phase flow · top to bottom", "phases", nodes, list(zip(ids, ids[1:])),
                 [ids], "No phases recorded.")


def gate_graph(project, report) -> str:
    nodes, edges = mermaid.gate_data(project)
    columns = [[n["id"] for n in nodes if n["kind"] == kind] for kind in ("gate", "phase")]
    return graph("Gates → phase exits", "gates", nodes, edges,
                 [c for c in columns if c], "No gates or phase exits recorded.")


def dependency_timeline(project, report) -> str:
    bands = mermaid.dependency_bands(project)
    if not bands:
        return empty("No open work to order.")
    out = ['<ol class="ordering-bands">']
    for band, ids in bands.items():
        out.append(f'<li data-step="{band + 1}"><h3>Step {band + 1}</h3><ul>')
        for nid in ids:
            task = project.task(nid)
            out.append(f'<li data-node="{esc(nid)}"><a href="#task-record-{esc(nid)}">{esc(nid)}</a>'
                       f'<span>{esc(task.title)}</span><span class="drawing-status tone-{tone(task.status)}">'
                       f'{esc(task.status)}</span></li>')
        out.append('</ul></li>')
    return frame("Dependency ordering · steps, not dates", ''.join(out) + '</ol>')


def interval(entry: dict) -> tuple[datetime, datetime] | None:
    """Resolve only an authored end or a supported Mermaid duration."""
    try:
        start = datetime.strptime(entry["start"], "%Y-%m-%d")
        if entry.get("end"):
            end = datetime.strptime(entry["end"], "%Y-%m-%d")
        else:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w)", entry.get("estimate", ""))
            if not match:
                return None
            seconds = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
            end = start + timedelta(seconds=float(match[1]) * seconds[match[2]])
        return (start, end) if end >= start else None
    except (ValueError, OverflowError):
        return None


def calendar_gantt(project, report) -> str:
    groups = mermaid.scheduled_groups(project, report)
    if not groups:
        return empty(f'No task carries schedule metadata. {len(report.unscheduled)} open tasks are unscheduled.')
    rows = [(phase, entry, interval(entry)) for phase, entries in groups.items() for entry in entries]
    dated = [span for _, _, span in rows if span]
    if not dated:
        return empty("No drawable dates. Check the recorded start, end or duration in the Mermaid source.")
    start, end = min(s[0] for s in dated), max(s[1] for s in dated)
    span = max((end - start).total_seconds(), 1)
    left = max(180, max(max(len(e["id"]), len(phase) + 12) * 9 + 24 for phase, e, _ in rows))
    plot = 700
    height = 70 + 86 * len(rows)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="calendar-title calendar-desc" '
           f'width="{left + plot + 28}" height="{height}">',
           '<title id="calendar-title">Calendar Gantt · recorded schedules</title>',
           '<desc id="calendar-desc">Bars use recorded starts and ends or durations. '
           'Unscheduled tasks have no calendar position. Exact metadata appears on every row.</desc>']
    for fraction in (0, .5, 1):
        x = left + plot * fraction
        tick = start + (end - start) * fraction
        label = tick.strftime("%Y-%m-%d" if (end - start).days >= 2 else "%Y-%m-%d %H:%M")
        anchor = "start" if fraction == 0 else "end" if fraction == 1 else "middle"
        out.append(f'<text x="{x:g}" y="24" text-anchor="{anchor}">{label}</text>'
                   f'<path class="drawing-grid" d="M {x:g} 34 V {height - 8}"/>')
    for i, (phase, entry, dates) in enumerate(rows):
        nid = entry["id"]
        status = project.task(nid).status if project.task(nid) else "UNKNOWN"
        y = 58 + i * 86
        finish = entry.get("end") or entry.get("estimate")
        out.append(f'<g data-node="{esc(nid)}" data-phase="{esc(phase)}" data-start="{esc(entry["start"])}" '
                   f'data-finish="{esc(finish)}" class="tone-{tone(status)}">'
                   f'<text class="drawing-id" x="12" y="{y + 4}">{esc(nid)}</text>'
                   f'<text x="12" y="{y + 24}">{esc(phase)} · {esc(status)}</text>')
        if dates:
            x = left + (dates[0] - start).total_seconds() / span * plot
            width = (dates[1] - dates[0]).total_seconds() / span * plot
            out.append(f'<rect class="calendar-bar" x="{x:g}" y="{y - 12}" width="{max(width, 2):g}" height="24" rx="3"/>')
        else:
            out.append(f'<text x="{left}" y="{y + 4}">Invalid or unsupported schedule; no bar drawn</text>')
        out.append(f'<text x="{left}" y="{y + 42}">{esc(entry["start"])} → {esc(finish)}'
                   + (' (duration)' if not entry.get("end") else '') + '</text></g>')
    count = len(report.unscheduled)
    return (frame("Calendar Gantt · recorded schedules", ''.join(out) + '</svg>')
            + f'<p class="note">{count} unscheduled {"task" if count == 1 else "tasks"} not shown. '
            'No dates or durations are inferred.</p>')


def render_all(project, report) -> dict[str, str]:
    return {"critical-path.mmd": critical_path(project, report),
            "phases.mmd": phase_flow(project, report), "gates.mmd": gate_graph(project, report),
            "timeline.mmd": dependency_timeline(project, report), "gantt.mmd": calendar_gantt(project, report)}
