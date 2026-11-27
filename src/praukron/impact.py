"""What a planned change runs into (ADR-103): `praukron engine impact <task>`.

The forward question of the code layer. From a task's own files and symbols,
the answer follows the graph to the code that calls or depends on them, to
the tasks whose references lie on that code, and to the decisions, frozen
criteria and invariants attached to those tasks. It is the list of what the
change must be checked against, never a verdict: whether anything conflicts
is for the reader, who records the conclusion in the chronicle.

The path is derived from records that already exist and is never stored:

- The starting set is the task's current references, planned and observed.
  Each is matched to engine nodes by the rules of `codemap`.
- Reach follows resolved edges backwards, from a node to the code that calls,
  imports, inherits or refers to it, one level per step, to the depth asked
  (`DEFAULT_DEPTH` unless given). A symbol's members count as the symbol: a
  call to `Catalog.add` reaches `Catalog`. A file counts as all its symbols.
  The engine marks each resolved edge `exact`, `likely` or `name_only`
  (imports carry no mark); every path carries its marks, and `exact_only`
  follows `exact` edges alone. Breadth first, so each node keeps its shortest
  path; among edges of the same step an `exact` one is preferred.
- A reference lies on a reached node when it names the same file and either
  side is the whole file, or its symbol is the node's, a member of it, or
  encloses it.
- Unknown is never absent. A reference that matches no node, an edge the
  engine left unresolved from a touched or followed node, and an unresolved
  edge elsewhere that bears a touched or followed name (a caller the engine
  could not place) each mark further reach as unknown on that item. Nodes at
  the depth limit with callers not followed are counted.
- Without an index the answer covers only the code the task references and
  the tasks whose references lie on it, and says that reach through the graph
  is unknown. That is a normal state.

Decisions come from `why`: a decision is in force while it is `ACCEPTED` and
no decision supersedes it; a superseded one is shown as superseded with its
successors. The task's own decisions are listed apart. A contract is frozen
once its task is `WIP` or `DONE`.

Reads the index with `sqlite3` alone, through `codemap`; only the `engine`
command calls this module. Standard library only. Nothing here writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import codemap, compile as compiler, freeze, refs, why
from .model import Project, Reference

DEFAULT_DEPTH = 2
EXACT = "exact"

PURPOSE = ("What this change must be checked against. It does not decide whether anything conflicts: "
           "read each item against the change and record your conclusion in the chronicle "
           "(a journal entry, a decision, or an Acceptance Change Request).")

# How many reached nodes and unknown names a text answer lists before it counts the rest.
_TEXT_NODES = 40
_TEXT_NAMES = 8

_STANDING_ORDER = {"in force": 0, "not accepted": 1, "superseded": 2}


def _mark(confidence: Optional[str]) -> str:
    return confidence or "unmarked"


# The graph -----------------------------------------------------------------


class _Graph:
    """Nodes of one index, with the members of a symbol and the names it bears."""

    def __init__(self, index: codemap.Index) -> None:
        self.index = index
        self._members: Dict[str, Tuple[List[int], Optional[int], List[str]]] = {}

    def node(self, symbol_id: Optional[int], file_id: int) -> Dict[str, Any]:
        found = codemap._endpoint(self.index, symbol_id, file_id)
        return found if found is not None else {"id": f"s:{symbol_id}", "path": "?", "kind": "missing"}

    def members(self, node: Dict[str, Any]) -> Tuple[List[int], Optional[int], List[str]]:
        """(symbol ids, file id or None, own names) a node stands for."""
        if node["id"] not in self._members:
            number = int(node["id"][2:])
            if node["id"].startswith("f:"):
                symbols = self.index.symbols_in(number)
                found = ([s.id for s in symbols], number, sorted({s.name for s in symbols}))
            else:
                symbol = self.index.symbol(number)
                if symbol is None:
                    found = ([number], None, [])
                else:
                    chain = self.index.chain(symbol)
                    inside = [s for s in self.index.symbols_in(symbol.file_id)
                              if s.id == symbol.id or self.index.chain(s).startswith(chain + ".")]
                    found = ([s.id for s in inside], None, [symbol.name])
            self._members[node["id"]] = found
        return self._members[node["id"]]

    def callers(self, node: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbol_ids, file_id, _ = self.members(node)
        steps = []
        for row in self.index.edges_into(symbol_ids, file_id):
            _, edge_file, from_symbol, to_symbol, to_file, _, _, kind, line, confidence = row
            origin = self.node(from_symbol, edge_file)
            if origin["id"] == node["id"]:
                continue
            target = self.node(to_symbol, to_file) if to_symbol is not None else node
            steps.append({"from": origin, "to": target, "kind": kind, "confidence": confidence,
                          "at": f"{self.index.file_path(edge_file)}:{line}"})
        return steps

    def unknown(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """The unresolved edges that leave further reach from this node unknown."""
        symbol_ids, file_id, names = self.members(node)
        outgoing = self.index.unresolved_from(symbol_ids, file_id)
        named = [row for row in (self.index.unresolved_named(names) if names else [])
                 if row[2] not in symbol_ids]
        found: Dict[str, Any] = {}
        if outgoing:
            found["unresolvedFrom"] = _names(outgoing)
        if named:
            found["unresolvedCallers"] = _names(named)
        return found


def _names(rows: List[tuple]) -> Dict[str, Any]:
    counted: Dict[str, int] = {}
    for row in rows:
        name = f"{row[6]}.{row[5]}" if row[6] else row[5]
        counted[name] = counted.get(name, 0) + 1
    return {"edges": len(rows), "names": counted}


def _reach(graph: _Graph, starts: Dict[str, Dict[str, Any]], depth: int,
           exact_only: bool) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
    """Breadth first from the touched nodes: each reached node with its depth and shortest path."""
    reached = {node_id: {**start, "depth": 0, "steps": []} for node_id, start in starts.items()}
    frontier = list(starts)
    counts = {"notExact": 0, "beyond": 0}
    for level in range(1, depth + 1):
        candidates = []
        for node_id in frontier:
            for step in graph.callers(reached[node_id]["node"]):
                if exact_only and step["confidence"] != EXACT:
                    counts["notExact"] += 1
                    continue
                candidates.append((node_id, step))
        candidates.sort(key=lambda c: c[1]["confidence"] != EXACT)
        frontier = []
        for node_id, step in candidates:
            origin = step["from"]
            if origin["id"] in reached:
                continue
            behind = reached[node_id]
            reached[origin["id"]] = {"node": origin, "touchedBy": behind["touchedBy"], "depth": level,
                                     "steps": [step] + behind["steps"]}
            frontier.append(origin["id"])
        if not frontier:
            break
    else:
        for node_id in frontier:
            followed = [s for s in graph.callers(reached[node_id]["node"])
                        if not exact_only or s["confidence"] == EXACT]
            if any(s["from"]["id"] not in reached for s in followed):
                counts["beyond"] += 1
    return reached, counts


# Records -------------------------------------------------------------------


def _chains(index: Optional[codemap.Index], root: Path, reference: Reference) -> Optional[List[str]]:
    """The names inside the file a reference stands for; None for the whole file."""
    if not reference.symbol:
        return None
    if index is not None:
        found = codemap.match(index, root, reference.path, reference.symbol)
        if found.get("matched"):
            return [n["symbol"] for n in found["nodes"] if n.get("symbol")] or [reference.symbol]
    return [reference.symbol]


def _lies_on(node: Dict[str, Any], path: str, chains: Optional[List[str]]) -> bool:
    if node.get("path") != path:
        return False
    symbol = node.get("symbol")
    if chains is None or not symbol:
        return True
    return any(c == symbol or c.startswith(symbol + ".") or symbol.startswith(c + ".") for c in chains)


def _link(reference: Reference) -> Dict[str, Any]:
    return {**why._reference(reference), "label": codemap.reference_source(reference)}


def _path(entry: Dict[str, Any]) -> Dict[str, Any]:
    marks = [_mark(s["confidence"]) for s in entry["steps"]]
    return {"node": entry["node"], "depth": entry["depth"], "touchedBy": entry["touchedBy"],
            "steps": entry["steps"], "marks": marks, "exact": all(m == EXACT for m in marks)}


def _shortest(paths: List[Dict[str, Any]]) -> Dict[str, Any]:
    return min(paths, key=lambda p: (p["depth"], not p["exact"]))


def _tasks(project: Project, index: Optional[codemap.Index], root: Path, own: str,
           reached: Dict[str, Dict[str, Any]], current: List[Reference],
           replaced: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    paths = {e["node"].get("path") for e in reached.values()}
    found: Dict[str, Dict[str, Any]] = {}
    for reference in current:
        if (reference.task == own or reference.artifact is None or reference.repository is not None
                or reference.path not in paths):
            continue
        chains = _chains(index, root, reference)
        on = [e for e in reached.values() if _lies_on(e["node"], reference.path, chains)]
        if not on:
            continue
        entry = found.setdefault(reference.task, {"id": reference.task, "references": [], "through": {}})
        entry["references"].append(_link(reference))
        for item in on:
            entry["through"][item["node"]["id"]] = _path(item)
    order = {t.id: n for n, t in enumerate(project.tasks)}
    tasks = []
    for task_id, entry in found.items():
        through = sorted(entry["through"].values(), key=lambda p: (p["depth"], not p["exact"], p["node"]["id"]))
        result: Dict[str, Any] = {"id": task_id, "references": entry["references"], "through": through,
                                  "path": _shortest(through),
                                  # Reached only through unattributed captures: not shown to be its code.
                                  "attributionUnresolved": all(r.get("attribution") == refs.UNRESOLVED
                                                               for r in entry["references"])}
        task = project.task(task_id)
        if task is None:
            result["missing"] = True
        else:
            acceptance = why._acceptance(project, task)
            frozen = task.status in freeze.FROZEN_STATUSES
            result.update({
                "title": task.title, "status": task.status, "authority": why._pointer(task.source),
                "decisions": why._decisions(project, task, replaced),
                "frozen": frozen,
                "criteria": (acceptance["criteria"] if acceptance and frozen else []),
                "criteriaNotFrozen": (len(acceptance["criteria"]) if acceptance and not frozen else 0),
                "contract": acceptance["id"] if acceptance else None,
                "invariants": why._invariants(project, task),
            })
        tasks.append(result)
    return sorted(tasks, key=lambda t: (t["path"]["depth"], order.get(t["id"], len(order))))


def _groups(tasks: List[Dict[str, Any]], own_ids: set) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Reached decisions other than the task's own, each with its tasks and shortest path."""
    groups: Dict[str, Dict[str, Any]] = {}
    ungoverned = []
    for task in tasks:
        others = [d for d in task.get("decisions", []) if d["id"] not in own_ids]
        if not others:
            ungoverned.append(task["id"])
        for decision in others:
            group = groups.setdefault(decision["id"], {
                **{k: v for k, v in decision.items() if k != "via"}, "tasks": [], "paths": []})
            group["tasks"].append({"id": task["id"], "via": decision["via"]})
            group["paths"].append(task["path"])
    found = []
    for group in groups.values():
        group["path"] = _shortest(group.pop("paths"))
        found.append(group)
    found.sort(key=lambda g: (_STANDING_ORDER.get(g.get("standing", ""), 3), g["path"]["depth"], g["id"]))
    return found, ungoverned


# The answer ----------------------------------------------------------------


def impact(project: Project, root: Path, target: str, db: Path, library: bool,
           depth: int = DEFAULT_DEPTH, exact_only: bool = False) -> Dict[str, Any]:
    """Answer `praukron engine impact <task>`; a `refs.TargetError` for a bad target."""
    task = project.task(target)
    if task is None:
        if refs._TASK_ID.fullmatch(target):
            raise refs.TargetError(f"unknown-task: {target}: no such task")
        raise refs.TargetError(f"invalid-impact-target: {target}: name a task; for a file or symbol "
                               f"use `praukron why {target}` or `praukron engine map {target}`")
    if depth < 0:
        raise refs.TargetError(f"invalid-depth: {depth}: the depth is 0 or more")
    every = compiler.compiled_references(project)
    superseded = refs.replaced_by(every)
    current = [r for r in every if refs.is_current(r, superseded)]
    replaced: Dict[str, List[str]] = {}
    for decision in project.decisions:
        for earlier in decision.supersedes:
            replaced.setdefault(earlier, []).append(decision.id)
    own = [r for r in current if r.task == task.id]
    own_decisions = why._decisions(project, task, replaced)
    index, graph_state = codemap._graph_state(root, db, library)
    try:
        result = _answer(project, root, index, graph_state, task, own, current, replaced, depth, exact_only)
    finally:
        if index is not None:
            index.close()
    own_ids = {d["id"] for d in own_decisions}
    groups, ungoverned = _groups(result["tasks"], own_ids)
    for decision in own_decisions:
        decision["alsoReachedThrough"] = [t["id"] for t in result["tasks"]
                                          if any(d["id"] == decision["id"] for d in t.get("decisions", []))]
    result.update({"ownDecisions": own_decisions, "decisions": groups, "ungoverned": ungoverned})
    result["counts"].update({
        "tasks": len(result["tasks"]), "decisions": len(groups),
        "decisionsByStanding": {s: sum(1 for g in groups if g.get("standing") == s)
                                for s in ("in force", "not accepted", "superseded")},
    })
    return result


def _answer(project: Project, root: Path, index: Optional[codemap.Index], graph_state: Dict[str, Any], task,
            own: List[Reference], current: List[Reference], replaced: Dict[str, List[str]],
            depth: int, exact_only: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "target": task.id, "kind": "impact",
        "task": {"id": task.id, "title": task.title, "status": task.status,
                 "authority": why._pointer(task.source)},
        "purpose": PURPOSE, "verdict": None, "graph": graph_state, "sources": codemap.SOURCES,
        "depth": depth, "follows": "exact edges only" if exact_only else "every resolved edge, each with its mark",
        "exactOnly": exact_only,
    }
    touched: List[Dict[str, Any]] = []
    starts: Dict[str, Dict[str, Any]] = {}
    for reference in own:
        item = codemap._reference_item(reference)
        if reference.recorded:
            item["recorded"] = reference.recorded
        if index is not None:
            found = codemap.match(index, root, reference.path, reference.symbol, reference.repository)
            item["match"] = found
            for node in found.get("nodes", []):
                start = starts.setdefault(node["id"], {"node": node, "touchedBy": []})
                start["touchedBy"].append(reference.id)
            if not found["matched"]:
                item["reach"] = "unknown"
                item["unknown"] = {"reason": found["reason"], "text": codemap.REASONS[found["reason"]]}
        else:
            item["reach"] = "unknown"
        touched.append(item)
    unknown: List[Dict[str, Any]] = []
    if index is None:
        # Only the task's own code: each reference stands for its file or symbol.
        reached = {}
        for reference in own:
            if reference.artifact is None or reference.repository is not None:
                continue
            node = {"id": f"ref:{reference.path}" + (f"#{reference.symbol}" if reference.symbol else ""),
                    "path": reference.path, "kind": "symbol" if reference.symbol else "file"}
            if reference.symbol:
                node["symbol"] = reference.symbol
            entry = reached.setdefault(node["id"], {"node": node, "touchedBy": [], "depth": 0, "steps": []})
            entry["touchedBy"].append(reference.id)
        counts: Dict[str, int] = {"notExact": 0, "beyond": 0}
        result["reach"] = ("unknown: no graph index, so the code that calls or depends on the task's code "
                           "is not known; only the code the task references and the records behind it are listed")
    else:
        graph = _Graph(index)
        reached, counts = _reach(graph, starts, depth, exact_only)
        for node_id, entry in reached.items():
            if entry["depth"] >= depth and entry["depth"] > 0:
                continue  # its callers are not followed: counted as beyond, not as unknown
            found = graph.unknown(entry["node"])
            if found:
                entry["unknown"] = found
                unknown.append({"node": entry["node"], "depth": entry["depth"], **found})
        result["reach"] = "graph"
    tasks = _tasks(project, index, root, task.id, reached, current, replaced)
    by_depth: Dict[str, int] = {}
    for entry in reached.values():
        if entry["depth"] > 0:
            by_depth[str(entry["depth"])] = by_depth.get(str(entry["depth"]), 0) + 1
    result.update({
        "references": touched,
        "touched": [_path(e) for e in reached.values() if e["depth"] == 0],
        "reached": sorted((_path(e) for e in reached.values() if e["depth"] > 0),
                          key=lambda p: (p["depth"], p["node"]["id"])),
        "unknown": unknown,
        "tasks": tasks,
        "counts": {
            "references": len(touched),
            "attributionUnresolved": sum(1 for t in touched if t.get("attribution") == refs.UNRESOLVED),
            "touchedNodes": sum(1 for e in reached.values() if e["depth"] == 0),
            "reached": sum(by_depth.values()), "reachedByDepth": by_depth,
            "unknown": len(unknown) + sum(1 for t in touched if t.get("reach") == "unknown"),
            "notFollowedNotExact": counts["notExact"], "beyondDepth": counts["beyond"],
        },
    })
    return result


# Text ----------------------------------------------------------------------


def _name(node: Dict[str, Any]) -> str:
    return codemap._name(node) if node.get("path") else node["id"]


def _path_text(path: Dict[str, Any]) -> str:
    touched = ", ".join(path["touchedBy"])
    if not path["steps"]:
        return f"{_name(path['node'])} · touched by {touched}"
    parts = [_name(path["steps"][0]["from"])]
    for step in path["steps"]:
        parts.append(f"-[{step['kind']} {_mark(step['confidence'])}]-> {_name(step['to'])}")
    marks = "exact" if path["exact"] else "not exact only: " + ", ".join(dict.fromkeys(path["marks"]))
    return " ".join(parts) + f" · depth {path['depth']} ({marks}) · touched by {touched}"


def _unknown_text(found: Dict[str, Any]) -> List[str]:
    lines = []
    for key, what in (("unresolvedFrom", "edges from it the engine left unresolved"),
                      ("unresolvedCallers", "unresolved edges elsewhere that bear its name, callers the "
                                            "engine could not place")):
        if key in found:
            names = [n + (f" ×{c}" if c > 1 else "") for n, c in found[key]["names"].items()]
            shown = ", ".join(names[:_TEXT_NAMES]) + (f", and {len(names) - _TEXT_NAMES} more"
                                                       if len(names) > _TEXT_NAMES else "")
            lines.append(f"      reach unknown: {found[key]['edges']} {what}: {shown}")
    return lines


def _task_lines(task: Dict[str, Any], indent: str = "  ") -> List[str]:
    if task.get("missing"):
        return [f"{indent}{task['id']} — no such task"]
    mark = f" — {refs.UNRESOLVED_TEXT}" if task.get("attributionUnresolved") else ""
    lines = [f"{indent}{task['id']} — {task['title']} [{task['status']}] — {task['authority']}{mark}",
             f"{indent}  path: {_path_text(task['path'])}"]
    if len(task["through"]) > 1:
        lines.append(f"{indent}  through {len(task['through'])} nodes: "
                     + ", ".join(_name(p["node"]) for p in task["through"][:_TEXT_NAMES])
                     + (" and more" if len(task["through"]) > _TEXT_NAMES else ""))
    lines += [indent + line for line in (why._reference_line(r) for r in task["references"])]
    for criterion in task["criteria"]:
        lines.append(f"{indent}  frozen criterion {criterion['id']} [{criterion['state']}] {criterion['class']} — "
                     f"{criterion['text']} — {criterion['authority']}")
    if task["criteriaNotFrozen"]:
        lines.append(f"{indent}  {task['criteriaNotFrozen']} criteria of {task['contract']}, not frozen "
                     f"(the task is {task['status']})")
    if not task["contract"]:
        lines.append(f"{indent}  no acceptance contract")
    lines += [f"{indent}  invariant {i['id']} ({', '.join(i['criteria'])}) — {i['authority']}"
              for i in task["invariants"]]
    return lines


def render(result: Dict[str, Any]) -> str:
    task, counts, graph = result["task"], result["counts"], result["graph"]
    lines = [f"{task['id']} — {task['title']} [{task['status']}]", result["purpose"]]
    if not result["references"]:
        lines += ["", f"{task['id']} references no file or symbol, so there is nothing to follow from. "
                      "Record the files and symbols it will touch with `praukron ref add` (`Scope: planned`), "
                      "then ask again."]
    else:
        lines.append(codemap._graph_line(graph))
        if result["reach"] != "graph":
            lines.append(f"Reach through the graph is {result['reach']}.")
        follows = ("exact edges only" if result["exactOnly"]
                   else "every resolved edge (exact, likely, name_only, unmarked)")
        summary = (f"Depth {result['depth']}, following {follows}: {counts['references']} references, "
                   f"{counts['touchedNodes']} touched nodes, {counts['reached']} nodes reached")
        if counts["reachedByDepth"]:
            summary += " (" + ", ".join(f"{n} at depth {d}" for d, n in sorted(counts["reachedByDepth"].items())) + ")"
        summary += (f"; {counts['tasks']} tasks and {counts['decisions']} decisions reached; "
                    f"{counts['unknown']} items with unknown reach")
        lines.append(summary + ".")
        if counts["notFollowedNotExact"]:
            lines.append(f"{counts['notFollowedNotExact']} edges not marked exact were not followed.")
        if counts["beyondDepth"]:
            lines.append(f"{counts['beyondDepth']} nodes at depth {result['depth']} have callers not followed: "
                         "ask with a larger --depth to see them.")
        unresolved = (f", {counts['attributionUnresolved']} of them unresolved attribution"
                      if counts.get("attributionUnresolved") else "")
        lines += ["", f"Touched code ({counts['references']} references{unresolved})"]
        for item in result["references"]:
            lines += codemap._reference_text(item)
            if item.get("reach") == "unknown" and "unknown" in item:
                lines.append("      reach unknown: with no node, what calls or depends on it cannot be followed")
        for entry in result["touched"]:
            if entry["node"]["id"] in {u["node"]["id"] for u in result["unknown"]}:
                lines.append(f"    {_name(entry['node'])}")
                lines += _unknown_text(next(u for u in result["unknown"] if u["node"]["id"] == entry["node"]["id"]))
        if result["reach"] == "graph":
            lines += ["", f"Code that calls or depends on it ({counts['reached']}), shortest path each"]
            lines += [f"  {_path_text(p)}" for p in result["reached"][:_TEXT_NODES]]
            if len(result["reached"]) > _TEXT_NODES:
                lines.append(f"  and {len(result['reached']) - _TEXT_NODES} more: --json lists every one")
            if not result["reached"]:
                lines.append("  none through a resolved edge" + (", and reach is unknown where marked above"
                                                                 if result["unknown"] else ""))
            followed = [u for u in result["unknown"] if u["depth"] > 0]
            if followed:
                lines.append(f"  {len(followed)} of these have unknown reach too: --json lists their unresolved edges")
    own = result["ownDecisions"]
    lines += ["", f"Decisions {task['id']} already holds ({len(own)}), listed apart"]
    for decision in own:
        lines += why._decision_line(decision)
        if decision.get("alsoReachedThrough"):
            lines.append(f"    also on the tasks reached: {', '.join(decision['alsoReachedThrough'])}")
    if not own:
        lines.append("  none")
    tasks = {t["id"]: t for t in result["tasks"]}
    listed: Dict[str, str] = {}
    lines += ["", f"To check the change against, by decision ({len(result['decisions'])})"]
    for group in result["decisions"]:
        lines += why._decision_line({**group, "via": sorted({v for t in group["tasks"] for v in t["via"]})})
        lines.append(f"    shortest path: {_path_text(group['path'])}")
        for member in group["tasks"]:
            shown = listed.get(member["id"])
            if shown:
                found = tasks[member["id"]]
                lines += [f"    {member['id']} — {found.get('title', 'no such task')}: listed in full under {shown}",
                          f"      path: {_path_text(found['path'])}"]
                continue
            listed[member["id"]] = group["id"]
            lines += _task_lines(tasks[member["id"]], "    ")
    if not result["decisions"]:
        lines.append("  none reached" + (" through a resolved edge" if result["references"] else ""))
    if result["ungoverned"]:
        lines += ["", f"Tasks reached under no other decision ({len(result['ungoverned'])})"]
        for task_id in result["ungoverned"]:
            lines += _task_lines(tasks[task_id])
    lines += ["", "This is not a verdict. Record what you conclude from it in the chronicle."]
    return "\n".join(lines) + "\n"
