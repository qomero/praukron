"""Why a piece of code is as it is (ADR-103): `praukron why <path>[#symbol]`.

The answer walks a path that the records already hold and stores nothing:
a task has references to files and symbols (CODE.md and its `Files:`
anchors); a task is governed by decisions (`Governed by:`) and a decision
names the tasks it affects (`Affects:`); a task's contract holds its
criteria and inherits the project's invariants. For a file, a symbol or a
directory the answer lists the tasks whose current references reach it,
oldest first, and for each how its reference was recorded, the decisions
behind it with their standing, its criteria and its invariants. Every item
names the record it comes from.

What counts as reaching the target:

- a symbol, `path#Qualified.name`: the references to that symbol;
- a file: the references to the file and to every symbol in it;
- a directory: the references to every file below it.

A symbol no current reference names is answered plainly: no record explains
it. The answer then names the nearest enclosing target that has records, by
removing one name at a time from the qualified name (`Catalog.add`, then
`Catalog`), then the file itself (a reference to the file with no symbol
stands in for its symbols), then each enclosing directory, and gives that
target's history. A file with no record walks up its directories the same
way. Only the chronicle is read: no graph library and no index, which only
the `engine` command reads (AC-T-GRAPH-LOAD-01-05), so a bare member name
(`add` for `Catalog.add`) has no enclosing symbol here but its file.

Oldest first: a task's date is the earliest of its claim date and the dates
its references to the target were recorded. Tasks with no such date come
first, in chronicle order (archived phases first, then TASKS.md), because a
record without dates was written before dates were kept; ties keep chronicle
order.

A decision is in force only while it is `ACCEPTED` and no decision names it
in `Supersedes:`. A superseded decision is shown with the decisions that
replace it; any other status is marked as not accepted.

Standard library only. Nothing here writes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import compile as compiler, layout, refs
from .analytics import _pointer
from .model import Project, Reference

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _date(text: Optional[str]) -> Optional[str]:
    found = _DATE.search(text or "")
    return found.group(0) if found else None


def _reaches(reference: Reference, kind: str, path: str, symbol: Optional[str]) -> bool:
    # This repository only; a `Symbols:` anchor names no file and so no artifact.
    if reference.artifact is None or reference.repository is not None:
        return False
    if kind == "directory":
        return reference.path.startswith(path + "/") if path else True
    if reference.path != path:
        return False
    return symbol is None or reference.symbol == symbol


def _exact(reference: Reference, path: str, symbol: Optional[str]) -> bool:
    """A reference to exactly this file (no symbol) or this symbol."""
    return (reference.artifact is not None and reference.repository is None
            and reference.path == path and reference.symbol == symbol)


def _candidates(path: str, symbol: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """Enclosing targets, nearest first, as (path, symbol) pairs."""
    found: List[Tuple[str, Optional[str]]] = []
    if symbol:
        parts = symbol.split(".")
        found += [(path, ".".join(parts[:n])) for n in range(len(parts) - 1, 0, -1)]
        found.append((path, None))
    # The directories that hold the file, or the one above a directory.
    directories = path.split("/")[:-1]
    found += [("/".join(directories[:n]), None) for n in range(len(directories), 0, -1)]
    return found


def _decisions(project: Project, task, replaced: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    by_id = {d.id: d for d in project.decisions}
    via: Dict[str, List[str]] = {}
    for adr in task.decisions:
        via.setdefault(adr, []).append("Governed by")
    for decision in project.decisions:
        if task.id in decision.affects:
            via.setdefault(decision.id, []).append("Affects")
    found = []
    for adr, links in via.items():
        decision = by_id.get(adr)
        if decision is None:
            found.append({"id": adr, "missing": True, "via": links})
            continue
        successors = [{"id": s, "status": by_id[s].status, "authority": _pointer(by_id[s].source)}
                      for s in replaced.get(adr, []) if s in by_id]
        superseded = bool(successors) or decision.status.upper().startswith("SUPERSEDED")
        if superseded:
            standing = "superseded"
        elif decision.status.upper() == "ACCEPTED":
            standing = "in force"
        else:
            standing = "not accepted"
        entry: Dict[str, Any] = {"id": decision.id, "title": decision.title, "status": decision.status,
                                 "standing": standing, "inForce": standing == "in force", "via": links,
                                 "authority": _pointer(decision.source)}
        if superseded:
            entry["supersededBy"] = successors
        found.append(entry)
    return found


def _acceptance(project: Project, task) -> Optional[Dict[str, Any]]:
    contract = project.contracts.get(task.contract or "")
    if contract is None:
        return None
    return {"id": contract.id, "authority": _pointer(contract.source), "criteria": [
        {"id": c.id, "class": c.evidence_class, "state": c.state, "text": c.text,
         "authority": _pointer(contract.source)} for c in contract.criteria]}


def _invariants(project: Project, task) -> List[Dict[str, Any]]:
    return [{"id": c.id, "criteria": [k.id for k in c.criteria], "authority": _pointer(c.source)}
            for c in project.invariants_for(task)]


def _reference(reference: Reference) -> Dict[str, Any]:
    item = {**refs.entry(reference), "source": reference.provenance}
    if reference.recorded:
        item["recorded"] = reference.recorded
    if reference.legacy:
        item["anchor"] = True
    return item


def _history(project: Project, found: List[Reference], replaced: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    order = {t.id: n for n, t in enumerate(project.tasks)}
    tasks = []
    for task_id in dict.fromkeys(r.task for r in found):
        held = [r for r in found if r.task == task_id]
        task = project.task(task_id)
        if task is None:
            tasks.append({"id": task_id, "missing": True, "date": None,
                          "references": [_reference(r) for r in held]})
            continue
        dates = [(d, "reference " + r.id) for r in held for d in [_date(r.recorded)] if d]
        if _date(task.claimed):
            dates.append((_date(task.claimed), "claimed"))
        date, basis = min(dates) if dates else (None, None)
        entry: Dict[str, Any] = {
            "id": task.id, "title": task.title, "status": task.status,
            "archived": task.source.file.startswith(layout.ARCHIVE_DIR + "/"),
            "phase": task.phase, "module": task.module,
            "date": date, "dated": basis, "authority": _pointer(task.source),
            "references": [_reference(r) for r in held],
            "decisions": _decisions(project, task, replaced),
            "acceptance": _acceptance(project, task),
            "invariants": _invariants(project, task),
        }
        tasks.append(entry)
    return sorted(tasks, key=lambda t: (t["date"] is not None, t["date"] or "", order.get(t["id"], len(order))))


def _target_name(path: str, symbol: Optional[str]) -> str:
    return f"{path}#{symbol}" if symbol else (path or ".")


def why(project: Project, root: Path, target: str) -> Dict[str, Any]:
    """Answer `praukron why <target>`; a `refs.TargetError` for a bad target.

    `root` is read only to tell a directory from a file."""
    if project.task(target) is not None or refs._TASK_ID.fullmatch(target):
        raise refs.TargetError(f"invalid-why-target: {target}: name a file, path#symbol or directory; "
                               f"for a task use `praukron refs {target}`")
    raw = target.rstrip("/") if "#" not in target else target
    path, symbol = refs.parse_target(raw or ".")
    if path == ".":
        path = ""
    kind = "symbol" if symbol else ("directory" if (not path or (root / path).is_dir()) else "file")
    every = compiler.compiled_references(project)
    superseded = refs.replaced_by(every)
    current = [r for r in every if refs.is_current(r, superseded)]
    replaced: Dict[str, List[str]] = {}
    for decision in project.decisions:
        for earlier in decision.supersedes:
            replaced.setdefault(earlier, []).append(decision.id)

    direct = [r for r in current if _reaches(r, kind, path, symbol)]
    result: Dict[str, Any] = {
        "target": target, "kind": kind, "path": path, "symbol": symbol,
        "explained": bool(direct),
        "order": "oldest first: earliest claim or reference date; undated tasks first, in chronicle order",
    }
    explaining = direct
    if not direct:
        candidates = _candidates(path, symbol)
        nearest = None
        for at_path, at_symbol in candidates:
            hits = [r for r in current if _exact(r, at_path, at_symbol)]
            if hits:
                nearest = {"target": _target_name(at_path, at_symbol), "path": at_path, "symbol": at_symbol,
                           "kind": "symbol" if at_symbol else ("file" if at_path and not (root / at_path).is_dir()
                                                               else "directory")}
                explaining = hits
                break
        result["nearest"] = nearest
    result["tasks"] = _history(project, explaining, replaced)
    result["references"] = len(explaining)
    result["notCurrent"] = [{"id": r.id, "task": r.task, "replacedBy": superseded.get(r.id),
                             "withdrawn": r.withdrawn, "authority": f"{r.source.file}#{r.source.anchor}"}
                            for r in every if not refs.is_current(r, superseded)
                            and _reaches(r, kind, path, symbol)]
    return result


# Text ---------------------------------------------------------------------


def _reference_line(item: Dict[str, Any]) -> str:
    artifact = f"{item.get('path', '')}#{item['symbol']}" if item.get("symbol") else item.get("path", "")
    parts = [item.get("relation") or "no relation", item.get("scope") or "—",
             item.get("verification") or "unverified", f"source {item.get('source', '—')}"]
    if item.get("anchor"):
        parts.append("task anchor")
    if item.get("recorded"):
        parts.append(f"recorded {item['recorded']}")
    if item.get("evidence"):
        parts.append(f"evidence: {item['evidence']}")
    return f"  reference {item['id']} {artifact} · " + " · ".join(parts) + f" — {item['authority']}"


def _decision_line(item: Dict[str, Any]) -> List[str]:
    via = ", ".join(item["via"])
    if item.get("missing"):
        return [f"  decision {item['id']} — not found ({via})"]
    standing = item["standing"]
    if standing == "superseded":
        successors = ", ".join(f"{s['id']} [{s['status']}]" for s in item.get("supersededBy", []))
        standing = (f"superseded by {successors}, not the rule in force" if successors
                    else "superseded, not the rule in force; no decision names it in Supersedes")
    elif standing == "not accepted":
        standing = "not accepted, not the rule in force"
    lines = [f"  decision {item['id']} [{item['status']}] {standing} · {via} — {item['title']} — {item['authority']}"]
    lines += [f"    replaced by {s['id']} [{s['status']}] — {s['authority']}" for s in item.get("supersededBy", [])]
    return lines


def _task_lines(task: Dict[str, Any]) -> List[str]:
    if task.get("missing"):
        return [f"{task['id']} — no such task"] + [_reference_line(r) for r in task["references"]]
    marks = [task["status"]]
    if task["archived"]:
        marks.append("archived")
    when = f"since {task['date']} ({task['dated']})" if task["date"] else "undated"
    lineage = "/".join(filter(None, [task.get("phase"), task.get("module")]))
    lines = [f"{task['id']} — {task['title']} [{', '.join(marks)}] · {lineage} · {when} — {task['authority']}"]
    lines += [_reference_line(r) for r in task["references"]]
    for decision in task["decisions"]:
        lines += _decision_line(decision)
    if not task["decisions"]:
        lines.append(f"  no decision governs it — {task['authority']}")
    acceptance = task["acceptance"]
    if acceptance:
        lines += [f"  criterion {c['id']} [{c['state']}] {c['class']} — {c['text']} — {c['authority']}"
                  for c in acceptance["criteria"]]
    else:
        lines.append(f"  no acceptance contract — {task['authority']}")
    lines += [f"  invariant {i['id']} ({', '.join(i['criteria'])}) — {i['authority']}" for i in task["invariants"]]
    return lines


def _count(result: Dict[str, Any]) -> str:
    tasks, references = len(result["tasks"]), result["references"]
    return (f"{tasks} {'task' if tasks == 1 else 'tasks'} through {references} "
            f"{'reference' if references == 1 else 'references'}, oldest first")


def render(result: Dict[str, Any]) -> str:
    target = result["target"]
    lines: List[str] = []
    if result["explained"]:
        lines.append(f"{target} — {_count(result)}")
    else:
        lines.append(f"{target} — no record explains it: no current reference reaches it.")
        nearest = result.get("nearest")
        if nearest is None:
            lines.append("No enclosing symbol, file or directory has a record either.")
        else:
            lines.append(f"Nearest enclosing {nearest['kind']} with a record: {nearest['target']} — "
                         f"{_count(result)}")
    for task in result["tasks"]:
        lines += [""] + _task_lines(task)
    if result["notCurrent"]:
        lines += ["", "No longer current (replaced or withdrawn), not counted above:"]
        for item in result["notCurrent"]:
            state = f"replaced by {item['replacedBy']}" if item["replacedBy"] else f"withdrawn {item['withdrawn']}"
            lines.append(f"  reference {item['id']} {item['task']} · {state} — {item['authority']}")
    return "\n".join(lines) + "\n"
