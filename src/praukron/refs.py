"""Look up code from a task, and tasks from code (SPEC §2.9, ADR-088, ADR-089).

Both directions read the compiled references only: the authored records in
CODE.md and every task's `Files:` and `Symbols:` anchors. Nothing here runs or
imports a graph provider, so the answers are the same with or without one
installed. Nothing here writes.
"""

from __future__ import annotations

import re

from . import compile as compiler
from .analytics import _pointer
from .model import Project, Reference, reference_path_problem

# How many references a task packet carries before it points to `refs`.
PACKET_REFERENCES = 12


class TargetError(ValueError):
    """A `refs` target that is neither a task nor a repository-relative path."""


def _authority(reference: Reference) -> str:
    return f"{reference.source.file}#{reference.source.anchor}"


def replaced_by(references: list[Reference]) -> dict[str, str]:
    """Each superseded reference id and the record that names it in `Replaces`.

    A withdrawal or replacement leaves the earlier record unchanged (SPEC §2.9),
    so only its successor says that the earlier one no longer holds.
    """
    found: dict[str, str] = {}
    for reference in references:
        if reference.replaces and not reference.legacy:
            found.setdefault(reference.replaces, reference.id)
    return found


def is_current(reference: Reference, superseded: dict[str, str]) -> bool:
    """Neither superseded by another record nor withdrawn."""
    return reference.id not in superseded and not reference.withdrawn


# How an observed reference whose change no capture could attribute is marked
# in every listing (T-SCOPE-ATTRIB-01): it may be another line of work's change.
UNRESOLVED = "unresolved"
UNRESOLVED_TEXT = "UNRESOLVED ATTRIBUTION, not shown to be this task's change"


def unattributed(reference: Reference) -> bool:
    """True for an observed reference recorded with `Lifecycle: unresolved`."""
    return reference.scope == "observed" and reference.lifecycle == "unresolved"


def entry(reference: Reference) -> dict[str, object]:
    """One reference as an agent reads it in a packet: where to search, how the
    task relates to it, its state, and the record that says so."""
    found: dict[str, object] = {
        "id": reference.id,
        "path": reference.path,
        "symbol": reference.symbol,
        "relation": reference.relation,
        "scope": reference.scope,
        "verification": reference.verification,
        "authorization": reference.authorization,
        "revision": reference.revision,
        "evidence": reference.evidence,
        "authority": _authority(reference),
    }
    if reference.withdrawn:
        found["withdrawn"] = reference.withdrawn
    if unattributed(reference):
        found["attribution"] = UNRESOLVED
    # An absent key means not recorded: nulls would be half of every entry.
    return {key: value for key, value in found.items() if value is not None}


def detail(reference: Reference, superseded: dict[str, str] | None = None) -> dict[str, object]:
    """One reference with every compiled field and the record it comes from,
    and `replacedBy` when another record supersedes it."""
    found = {**reference.as_json(), "authority": _authority(reference)}
    if unattributed(reference):
        found["attribution"] = UNRESOLVED
    if superseded and reference.id in superseded:
        found["replacedBy"] = superseded[reference.id]
    return found


def for_task(project: Project, task_id: str,
             references: list[Reference] | None = None) -> list[Reference]:
    """The task's references, authored records first, then its anchors."""
    found = compiler.compiled_references(project) if references is None else references
    return [r for r in found if r.task == task_id]


def _counts(references: list[Reference]) -> dict[str, int]:
    counts = {scope: sum(1 for r in references if r.scope == scope) for scope in ("planned", "observed")}
    # Present only when some are: a task without any keeps the shape it always had.
    unresolved = sum(1 for r in references if unattributed(r))
    if unresolved:
        counts["unresolved"] = unresolved
    return counts


def counts_text(counts: dict[str, int]) -> str:
    """`N planned, M observed`, and how many of them are unresolved attribution."""
    text = f"{counts['planned']} planned, {counts['observed']} observed"
    if counts.get("unresolved"):
        text += f" ({counts['unresolved']} of them unresolved attribution)"
    return text


def summary(project: Project, task_id: str, references: list[Reference] | None = None,
            limit: int = PACKET_REFERENCES) -> dict[str, object]:
    """What a task packet carries: the counts by scope, the first references,
    and the command that returns all of them. Only current references count:
    a superseded or withdrawn one would send an agent to code the task dropped."""
    every = compiler.compiled_references(project) if references is None else references
    superseded = replaced_by(every)
    found = [r for r in for_task(project, task_id, every) if is_current(r, superseded)]
    return {
        "total": len(found),
        "counts": _counts(found),
        "first": [entry(r) for r in found[:limit]],
        "rest": f"praukron refs {task_id}" if len(found) > limit else None,
    }


def _criteria(project: Project, task) -> list[dict[str, object]]:
    contract = project.contracts.get(task.contract or "")
    if contract is None:
        return []
    return [{"id": c.id, "text": c.text, "class": c.evidence_class, "state": c.state,
             "authority": _pointer(contract.source)} for c in contract.criteria]


def _decisions(project: Project, task) -> list[dict[str, object]]:
    found = []
    for adr in task.decisions:
        decision = next((d for d in project.decisions if d.id == adr), None)
        if decision is not None:
            found.append({"id": decision.id, "title": decision.title, "status": decision.status,
                          "authority": _pointer(decision.source)})
    return found


def _assumptions(project: Project, task_id: str) -> list[dict[str, object]]:
    return [{"id": a.id, "title": a.title, "status": a.status, "impact": a.impact,
             "authority": _pointer(a.source)} for a in project.assumptions_for(task_id)]


_TASK_ID = re.compile(r"T-[A-Za-z0-9.\-]*[A-Za-z0-9]")


def parse_target(target: str) -> tuple[str, str | None]:
    """A path, or `path#Qualified.symbol`, checked by the reference path rule."""
    path, _, symbol = target.partition("#")
    while path.startswith("./"):
        path = path[2:]
    problem = reference_path_problem(path)
    if problem:
        raise TargetError(f"invalid-reference-path: {target}: {problem}")
    if "#" in target and not symbol:
        raise TargetError(f"invalid-reference-target: {target}: name a symbol after '#'")
    return path, symbol or None


def lookup(project: Project, target: str) -> dict[str, object]:
    """Answer `praukron refs <target>` from the compiled references.

    A task identifier returns the task's references. A path returns every
    reference to the file or to a symbol in it; `path#symbol` returns the
    references to that symbol. Either way each referencing task comes with its
    governing decisions, acceptance criteria and assumptions.
    """
    references = compiler.compiled_references(project)
    superseded = replaced_by(references)
    task = project.task(target)
    if task is not None:
        found = for_task(project, task.id, references)
        counts = _counts(found)
        counts.pop("unresolved", None)
        unresolved = sum(1 for r in found if is_current(r, superseded) and unattributed(r))
        if unresolved:
            counts["unresolved"] = unresolved
        return {
            "target": target,
            "kind": "task",
            "task": {"id": task.id, "title": task.title, "status": task.status,
                     "authority": _pointer(task.source)},
            "total": len(found),
            # Unresolved counts only what still holds: a settled or withdrawn record is history.
            "counts": counts,
            "current": sum(1 for r in found if is_current(r, superseded)),
            "references": [detail(r, superseded) for r in found],
        }
    if _TASK_ID.fullmatch(target):
        raise TargetError(f"unknown-task: {target}: no such task")
    path, symbol = parse_target(target)
    # This repository only, and a bare symbol name is never an identity, so a
    # `Symbols:` anchor, which names no file, never matches.
    found = [r for r in references
             if r.artifact is not None and r.repository is None and r.path == path
             and (symbol is None or r.symbol == symbol)]
    tasks = []
    # A task is named only while it still holds a current reference to the target;
    # its superseded and withdrawn records stay under `references`.
    for task_id in dict.fromkeys(r.task for r in found if is_current(r, superseded)):
        referencing = project.task(task_id)
        if referencing is None:
            tasks.append({"id": task_id, "missing": True,
                          "references": [r.id for r in found if r.task == task_id]})
            continue
        held = [r for r in found if r.task == task_id and is_current(r, superseded)]
        tasks.append({
            "id": referencing.id,
            "title": referencing.title,
            "status": referencing.status,
            "authority": _pointer(referencing.source),
            "references": [r.id for r in found if r.task == task_id],
            # Every current reference of the task to the target is unattributed:
            # the task is not shown to have touched it.
            "attributionUnresolved": all(unattributed(r) for r in held),
            "decisions": _decisions(project, referencing),
            "acceptance": _criteria(project, referencing),
            "assumptions": _assumptions(project, task_id),
        })
    return {
        "target": target,
        "kind": "symbol" if symbol else "file",
        "path": path,
        "symbol": symbol,
        "total": len(found),
        "current": sum(1 for r in found if is_current(r, superseded)),
        "unresolved": sum(1 for r in found if is_current(r, superseded) and unattributed(r)),
        "references": [detail(r, superseded) for r in found],
        "tasks": tasks,
    }


def _artifact(reference: dict[str, object]) -> str:
    path = reference.get("path") or ""
    symbol = reference.get("symbol")
    return f"{path}#{symbol}" if path and symbol else str(path or symbol or "")


def line(reference: dict[str, object]) -> str:
    """One reference on one line, for people and for `retrieve`'s text."""
    parts = [str(reference.get(k) or "—") for k in ("relation", "scope", "verification", "authorization")]
    text = f"- {_artifact(reference)} · " + " · ".join(parts)
    if reference.get("attribution") == UNRESOLVED:
        text = f"- {UNRESOLVED_TEXT}: {_artifact(reference)} · " + " · ".join(parts)
    if reference.get("revision"):
        text += f" · revision {reference['revision']}"
    if reference.get("replacedBy"):
        text += f" · replaced by {reference['replacedBy']}"
    if reference.get("withdrawn"):
        text += f" · withdrawn {reference['withdrawn']}"
    if reference.get("evidence"):
        text += f" · evidence: {reference['evidence']}"
    return text + f" — {reference['authority']}"


def render_summary(task_id: str, packed: dict[str, object]) -> list[str]:
    counts = packed["counts"]
    lines = [f"## REFERENCES · {task_id} — compiled from CODE.md and the task's anchors",
             f"_{packed['total']} references: {counts_text(counts)}_", ""]
    lines += [line(r) for r in packed["first"]] or ["No file or symbol is referenced."]
    if packed["rest"]:
        lines += ["", f"{packed['total'] - len(packed['first'])} more: `{packed['rest']}`"]
    return lines


def render(result: dict[str, object]) -> str:
    lines: list[str] = []
    if result["kind"] == "task":
        task = result["task"]
        counts = result["counts"]
        lines += [f"{task['id']} — {task['title']} [{task['status']}] — {task['authority']}",
                  f"  {result['total']} references: {counts_text(counts)}"]
        lines += [f"  {line(r)}" for r in result["references"]]
        return "\n".join(lines) + "\n"
    lines.append(f"{result['target']} — referenced by {len(result['tasks'])} tasks "
                 f"through {result['total']} references"
                 + (f", {result['unresolved']} of them unresolved attribution" if result.get("unresolved") else ""))
    named = {task["id"] for task in result["tasks"]}
    for task in result["tasks"]:
        if task.get("missing"):
            lines += ["", f"{task['id']} — not found"]
            continue
        lines += ["", f"{task['id']} — {task['title']} [{task['status']}] — {task['authority']}"
                  + (f" — {UNRESOLVED_TEXT}" if task.get("attributionUnresolved") else "")]
        lines += [f"  {line(r)}" for r in result["references"] if r["task"] == task["id"]]
        for decision in task["decisions"]:
            lines.append(f"  decision {decision['id']} [{decision['status']}] {decision['title']} — {decision['authority']}")
        for criterion in task["acceptance"]:
            lines.append(f"  criterion {criterion['id']} [{criterion['state']}] — {criterion['authority']}")
        for assumption in task["assumptions"]:
            lines.append(f"  assumption {assumption['id']} [{assumption['status']}] {assumption['title']} — {assumption['authority']}")
    dropped = [r for r in result["references"] if r["task"] not in named]
    if dropped:
        lines += ["", "No longer current (replaced or withdrawn):"]
        lines += [f"  {r['task']} {line(r)}" for r in dropped]
    return "\n".join(lines) + "\n"
