"""Join the engine's graph index to the task-code references (ADR-098, ADR-099, ADR-101).

`praukron engine map <task>` answers what a task touched: its references, the
engine nodes they match, the resolved edges among those nodes and to their
direct neighbours, and the edges the engine left unresolved from them.
`praukron engine map <path>` and `<path#symbol>` answer the other way: which
tasks touched that file or symbol, and its neighbours in the graph.

Every item carries its source, and the sources never mix:

- `engine`: a node or an edge the engine resolved;
- `engine-unresolved`: an edge whose target the engine could not place, with
  the name it saw. Unknown is not "no relation": an agent reads the code and
  records what it finds as a reference with source `agent` (ADR-101);
- `reference:<Source>`: a task-code reference as recorded in CODE.md or a
  task's anchors, by its `Source:` field (`git_diff` for Git capture, `agent`,
  `manual` for by hand, `static_analysis`, `graph_provider`).

The index is read with the standard library's `sqlite3` alone, opened read
only, so an index written on another machine is readable where no graph
library exists. The library is never loaded here. Without an index the same
questions are answered from the references alone, and the answer says so.
Nothing here writes: no engine node or edge reaches the chronicle.

Matching a reference to engine nodes, in order (`rule` in each match):

1. A reference to another repository, or with no path (a `Symbols:` anchor),
   matches nothing: `other-repository`, `no-path`.
2. The path is compared with the indexed file paths exactly, as recorded
   (repository relative, `/` separators). A path the index does not hold is
   `not-indexed` when the file exists in the working tree (a language or file
   the engine does not read) and `file-absent` when it does not (deleted,
   renamed, or not written yet).
3. A reference with no symbol matches the file node: rule `file`.
4. A symbol matches the symbols of that file whose name inside the file
   equals it: the chain of parent names, so a class member is
   `Class.method` and a nested function `outer.inner`. Rule `name`.
5. Else the engine's own qualification is removed: a symbol written with the
   file's module prefix (`praukron.scope.plan`, the engine's `qname`) or with
   `::` separators (`Type::method`) is read as the name inside the file.
   Rule `qualified`.
6. Else a bare member name matches when exactly one symbol in the file ends
   with `.<name>` (`relation` for `Change.relation`). Rule `member`.
7. Else the file is indexed but holds no such symbol: `symbol-absent`. The
   symbol was renamed or removed, is not written yet, or the index was built
   at another commit; `praukron engine index` refreshes it.

A name defined twice in one scope (a property's getter and setter) matches
every definition. A matched file whose size on disk differs from the indexed
one, or that is gone from the working tree, is marked `stale`.

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import compile as compiler, refs
from .model import Project, Reference

ENGINE = "engine"
UNRESOLVED = "engine-unresolved"
REFERENCE = "reference"

SOURCES = {
    ENGINE: "resolved by the engine",
    UNRESOLVED: "an edge whose target the engine could not place; unknown, not absent",
    f"{REFERENCE}:git_diff": "recorded by Git capture",
    f"{REFERENCE}:agent": "recorded by an agent, apart from the engine; read its evidence and verification",
    f"{REFERENCE}:manual": "recorded by hand",
    f"{REFERENCE}:static_analysis": "recorded by Git capture, symbols read from the changed code",
    f"{REFERENCE}:graph_provider": "recorded from a graph provider",
}

REASONS = {
    "other-repository": "the reference names another repository",
    "no-path": "no path recorded, and a bare name is never an identity",
    "not-indexed": "the file exists but the engine does not index it",
    "file-absent": "the file is neither in the working tree nor in the index",
    "symbol-absent": "the indexed file holds no such symbol: renamed, removed, not yet written, "
                     "or indexed at another commit",
}

# How many unresolved names a text line lists for one symbol before it counts the rest.
_TEXT_NAMES = 12
# Values bound in one query, below the oldest SQLite limit of 999.
_CHUNK = 500


class IndexUnreadable(Exception):
    """An index file that is not a graph store this Praukron can read."""


def reference_source(reference: Reference) -> str:
    return f"{REFERENCE}:{reference.provenance}"


# Reading the index --------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    id: int
    file_id: int
    path: str
    parent: Optional[int]
    name: str
    kind: str
    qname: str
    start: int
    end: int


class Index:
    """The engine's SQLite store, read only, through `sqlite3`."""

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self._conn = _connect(path)
        try:
            self._conn.execute("SELECT id, repo_id, path, size, module FROM files LIMIT 1").fetchall()
            self._conn.execute("SELECT id, file_id, parent_id, name, kind, qname, start_line, end_line "
                               "FROM symbols LIMIT 1").fetchall()
            self._conn.execute("SELECT from_symbol, to_symbol, to_file, name, qualifier, kind, line, "
                               "confidence FROM edges LIMIT 1").fetchall()
            self.repos = [dict(zip(("id", "name", "root", "indexed_at"), row)) for row in
                          self._conn.execute("SELECT id, name, root, indexed_at FROM repos ORDER BY id")]
            meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error as error:
            self._conn.close()
            raise IndexUnreadable(f"{path} is not a graph index this Praukron can read: {error}") from error
        self.schema = meta.get("schema")
        own = [r for r in self.repos if _same(r["root"], root)]
        # An index written on another machine names another root; with one
        # repository in it, that repository is this one.
        self.chosen = own or self.repos
        self._repo_ids = tuple(r["id"] for r in self.chosen)
        self._symbols: Dict[int, Optional[Symbol]] = {}
        self._chains: Dict[int, str] = {}
        self._files: Dict[int, str] = {}

    def close(self) -> None:
        self._conn.close()

    def summary(self) -> Dict[str, Any]:
        stamps = [r["indexed_at"] for r in self.chosen if r.get("indexed_at")]
        indexed = (dt.datetime.fromtimestamp(max(stamps), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                   if stamps else None)
        return {"present": True, "index": _relative(self.root, self.path), "schema": self.schema,
                "repositories": [{"name": r["name"], "root": r["root"]} for r in self.chosen],
                "indexedAt": indexed}

    # Files and symbols

    def file(self, path: str) -> Optional[Tuple[int, str, int, str]]:
        marks = ",".join("?" * len(self._repo_ids))
        row = self._conn.execute(
            f"SELECT id, path, size, module FROM files WHERE path = ? AND repo_id IN ({marks}) ORDER BY id LIMIT 1",
            (path, *self._repo_ids)).fetchone()
        return None if row is None else (row[0], row[1], row[2], row[3])

    def file_path(self, file_id: int) -> str:
        if file_id not in self._files:
            row = self._conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
            self._files[file_id] = row[0] if row else f"f:{file_id}"
        return self._files[file_id]

    def symbol(self, symbol_id: int) -> Optional[Symbol]:
        if symbol_id not in self._symbols:
            row = self._conn.execute(
                "SELECT s.id, s.file_id, f.path, s.parent_id, s.name, s.kind, s.qname, s.start_line, s.end_line "
                "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id = ?", (symbol_id,)).fetchone()
            self._symbols[symbol_id] = None if row is None else Symbol(*row)
        return self._symbols[symbol_id]

    def chain(self, symbol: Symbol) -> str:
        """The name inside the file: the parent names and the symbol's own."""
        if symbol.id not in self._chains:
            names, seen, current = [], set(), symbol
            while current is not None and current.id not in seen:
                seen.add(current.id)
                names.append(current.name)
                current = self.symbol(current.parent) if current.parent is not None else None
            self._chains[symbol.id] = ".".join(reversed(names))
        return self._chains[symbol.id]

    def symbols_in(self, file_id: int) -> List[Symbol]:
        rows = self._conn.execute(
            "SELECT s.id, s.file_id, f.path, s.parent_id, s.name, s.kind, s.qname, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.file_id = ? ORDER BY s.id", (file_id,))
        found = []
        for row in rows:
            symbol = Symbol(*row)
            self._symbols.setdefault(symbol.id, symbol)
            found.append(symbol)
        return found

    # Edges

    def symbol_edges(self, symbol_id: int) -> Iterable[tuple]:
        """Edge rows from and to one symbol: (direction, row)."""
        query = ("SELECT e.id, e.file_id, e.from_symbol, e.to_symbol, e.to_file, e.name, e.qualifier, e.kind, "
                 "e.line, e.confidence FROM edges e WHERE e.{} = ? ORDER BY e.id")
        for row in self._conn.execute(query.format("from_symbol"), (symbol_id,)):
            yield "out", row
        for row in self._conn.execute(query.format("to_symbol"), (symbol_id,)):
            if row[2] != symbol_id:  # a self call was given as outgoing
                yield "in", row

    def file_edges(self, file_id: int) -> Iterable[tuple]:
        """Edge rows at file level: what the file imports or refers to outside
        any symbol, and the imports of it."""
        query = ("SELECT e.id, e.file_id, e.from_symbol, e.to_symbol, e.to_file, e.name, e.qualifier, e.kind, "
                 "e.line, e.confidence FROM edges e WHERE {} ORDER BY e.id")
        for row in self._conn.execute(query.format("e.file_id = ? AND e.from_symbol IS NULL"), (file_id,)):
            yield "out", row
        for row in self._conn.execute(query.format("e.kind = 'import' AND e.to_file = ? AND e.file_id != ?"),
                                      (file_id, file_id)):
            yield "in", row

    def _rows(self, where: str, values: List[int], extra: tuple = ()) -> List[tuple]:
        """Edge rows matching `where`, whose one `({})` takes the values, in chunks
        below SQLite's variable limit."""
        found: List[tuple] = []
        for start in range(0, len(values), _CHUNK):
            part = values[start:start + _CHUNK]
            found += self._conn.execute(
                f"SELECT id, file_id, from_symbol, to_symbol, to_file, name, qualifier, kind, line, confidence "
                f"FROM edges WHERE {where.format(','.join('?' * len(part)))} ORDER BY id",
                (*part, *extra)).fetchall()
        return found

    def edges_into(self, symbol_ids: List[int], file_id: Optional[int] = None) -> List[tuple]:
        """Resolved edges into these symbols from code outside them, and, for a
        file, the imports of it from other files: what calls or depends on them."""
        rows = [row for row in self._rows("to_symbol IN ({})", symbol_ids)
                if row[2] not in symbol_ids and (file_id is None or row[1] != file_id)]
        if file_id is not None:
            rows += [row for row in self._rows("kind = 'import' AND to_file IN ({})", [file_id])
                     if row[1] != file_id and row[3] is None]
        return rows

    def unresolved_from(self, symbol_ids: List[int], file_id: Optional[int] = None) -> List[tuple]:
        """Edges from these symbols, or from the file outside any symbol, whose target is unknown."""
        rows = self._rows("from_symbol IN ({}) AND to_symbol IS NULL AND to_file IS NULL", symbol_ids)
        if file_id is not None:
            rows += self._rows("file_id IN ({}) AND from_symbol IS NULL AND to_symbol IS NULL AND to_file IS NULL",
                               [file_id])
        return rows

    def unresolved_named(self, names: List[str]) -> List[tuple]:
        """Edges anywhere whose target is unknown and whose name is one of these:
        code that may call them, which the engine could not place."""
        return self._rows("name IN ({}) AND to_symbol IS NULL AND to_file IS NULL", names)  # type: ignore[arg-type]

    def file_links(self, file_id: int) -> List[Dict[str, Any]]:
        """Resolved symbol edges between this file and each other file, counted by kind."""
        out = self._conn.execute(
            "SELECT tf.path, e.kind, COUNT(*) FROM edges e JOIN symbols t ON t.id = e.to_symbol "
            "JOIN files tf ON tf.id = t.file_id WHERE e.file_id = ? AND t.file_id != ? "
            "GROUP BY tf.path, e.kind ORDER BY tf.path, e.kind", (file_id, file_id)).fetchall()
        into = self._conn.execute(
            "SELECT f.path, e.kind, COUNT(*) FROM edges e JOIN symbols t ON t.id = e.to_symbol "
            "JOIN files f ON f.id = e.file_id WHERE t.file_id = ? AND e.file_id != ? "
            "GROUP BY f.path, e.kind ORDER BY f.path, e.kind", (file_id, file_id)).fetchall()
        return ([{"source": ENGINE, "direction": "out", "path": p, "kind": k, "count": n} for p, k, n in out]
                + [{"source": ENGINE, "direction": "in", "path": p, "kind": k, "count": n} for p, k, n in into])


def _connect(path: Path) -> sqlite3.Connection:
    """Read only. A store in WAL mode copied without its `-shm` file, or in a
    directory this user cannot write, opens as immutable instead."""
    uri = path.resolve().as_uri()
    failure: Optional[sqlite3.Error] = None
    for mode in ("mode=ro", "immutable=1"):
        conn = None
        try:
            conn = sqlite3.connect(f"{uri}?{mode}", uri=True)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
            return conn
        except sqlite3.Error as error:
            failure = error
            if conn is not None:
                conn.close()
    raise IndexUnreadable(f"cannot read {path}: {failure}")


def _same(a: object, b: Path) -> bool:
    try:
        return Path(str(a)).resolve() == b.resolve()
    except OSError:
        return False


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# Matching -----------------------------------------------------------------


def _node(index: Index, symbol: Symbol) -> Dict[str, Any]:
    return {"id": f"s:{symbol.id}", "path": symbol.path, "symbol": index.chain(symbol), "kind": symbol.kind,
            "lines": f"{symbol.start}-{symbol.end}"}


def _file_node(index: Index, file_id: int) -> Dict[str, Any]:
    return {"id": f"f:{file_id}", "path": index.file_path(file_id), "kind": "file"}


def match(index: Index, root: Path, path: Optional[str], symbol: Optional[str],
          repository: Optional[str] = None) -> Dict[str, Any]:
    """The engine nodes a (path, symbol) names, by the rules in the module docstring."""
    if repository:
        return {"matched": False, "reason": "other-repository"}
    if not path:
        return {"matched": False, "reason": "no-path"}
    row = index.file(path)
    if row is None:
        return {"matched": False, "reason": "not-indexed" if (root / path).is_file() else "file-absent"}
    file_id, _, size, module = row
    on_disk = root / path
    stale = not on_disk.is_file() or on_disk.stat().st_size != size
    found: Dict[str, Any] = {"matched": True, "stale": stale} if stale else {"matched": True}
    if not symbol:
        return {**found, "rule": "file", "nodes": [_file_node(index, file_id)]}
    symbols = index.symbols_in(file_id)
    by_chain: Dict[str, List[Symbol]] = {}
    for candidate in symbols:
        by_chain.setdefault(index.chain(candidate), []).append(candidate)
    hits = by_chain.get(symbol)
    rule = "name"
    if not hits:
        bare = symbol.replace("::", ".")
        if module and bare.startswith(module + "."):
            bare = bare[len(module) + 1:]
        hits, rule = by_chain.get(bare), "qualified"
    if not hits and "." not in symbol:
        ends = [s for chain, group in by_chain.items() if chain.endswith("." + symbol) for s in group]
        if len({index.chain(s) for s in ends}) == 1:
            hits, rule = ends, "member"
    if not hits:
        return {"matched": False, "reason": "symbol-absent", "file": f"f:{file_id}"}
    return {**found, "rule": rule, "nodes": [_node(index, s) for s in hits]}


# Edges --------------------------------------------------------------------


def _endpoint(index: Index, symbol_id: Optional[int], file_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if symbol_id is not None:
        found = index.symbol(symbol_id)
        if found is not None:
            return _node(index, found)
    if file_id is not None:
        return _file_node(index, file_id)
    return None


def _collect(index: Index, node_ids: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolved edges, unresolved edges and file links of the given nodes."""
    wanted = set(node_ids)
    edges: Dict[tuple, Dict[str, Any]] = {}
    unresolved: Dict[tuple, Dict[str, Any]] = {}
    links: List[Dict[str, Any]] = []
    seen: set = set()
    for node_id in node_ids:
        number = int(node_id[2:])
        if node_id.startswith("s:"):
            rows = index.symbol_edges(number)
        else:
            rows = index.file_edges(number)
            links += [{**link, "file": index.file_path(number)} for link in index.file_links(number)]
        for _, row in rows:
            edge_id, file_id, from_symbol, to_symbol, to_file, name, qualifier, kind, line, confidence = row
            if edge_id in seen:
                continue
            seen.add(edge_id)
            where = f"{index.file_path(file_id)}:{line}"
            origin = _endpoint(index, from_symbol, None if from_symbol is not None else file_id)
            if to_symbol is None and to_file is None:
                key = (origin["id"], kind, name, qualifier)
                item = unresolved.setdefault(key, {
                    "source": UNRESOLVED, "kind": kind, "from": origin, "name": name,
                    "qualifier": qualifier, "at": []})
                item["at"].append(where)
                continue
            target = _endpoint(index, to_symbol, to_file)
            if target is None:  # a target row gone from the store: as unknown as an unresolved name
                target = {"id": f"s:{to_symbol}", "path": "?", "kind": "missing"}
            key = (origin["id"], target["id"], kind, confidence)
            item = edges.setdefault(key, {
                "source": ENGINE, "kind": kind, "from": origin, "to": target, "confidence": confidence,
                "among": origin["id"] in wanted and target["id"] in wanted, "at": []})
            item["at"].append(where)
    return list(edges.values()), list(unresolved.values()), links


# Answers ------------------------------------------------------------------


def _graph_state(root: Path, db: Path, library: bool) -> Tuple[Optional[Index], Dict[str, Any]]:
    state: Dict[str, Any] = {"present": False, "index": _relative(root, db), "library": library}
    if not db.is_file():
        state["reason"] = (f"no graph index at {state['index']}"
                           + ("" if library else ", and no graph library is present")
                           + "; answered from the task-code references alone")
        return None, state
    try:
        index = Index(db, root)
    except IndexUnreadable as error:
        state["reason"] = f"{error}; answered from the task-code references alone"
        return None, state
    return index, {**state, **index.summary()}


def _reference_item(reference: Reference) -> Dict[str, Any]:
    item = {"source": reference_source(reference), **refs.entry(reference)}
    if reference.confidence is not None:
        item["confidence"] = reference.confidence
    if reference.legacy:
        item["anchor"] = True
    return item


def code_map(project: Project, root: Path, target: str, db: Path, library: bool) -> Dict[str, Any]:
    """Answer `praukron engine map <target>`; a `refs.TargetError` for a bad target.

    `db` is the index file and `library` whether a graph library was found;
    the `engine` command gives both, so this module never imports it."""
    every = compiler.compiled_references(project)
    superseded = refs.replaced_by(every)
    current = [r for r in every if refs.is_current(r, superseded)]
    task = project.task(target)
    if task is None and refs._TASK_ID.fullmatch(target):
        raise refs.TargetError(f"unknown-task: {target}: no such task")
    path, symbol = (None, None) if task is not None else refs.parse_target(target)
    index, graph = _graph_state(root, db, library)
    try:
        if task is not None:
            return _task_map(index, root, graph, task, [r for r in current if r.task == task.id],
                             sum(1 for r in every if r.task == task.id and not refs.is_current(r, superseded)))
        return _artifact_map(project, index, root, graph, target, path, symbol, current)
    finally:
        if index is not None:
            index.close()


def _task_map(index: Optional[Index], root: Path, graph: Dict[str, Any], task, found: List[Reference],
              dropped: int) -> Dict[str, Any]:
    items, node_ids = [], []
    for reference in found:
        item = _reference_item(reference)
        if index is not None:
            item["match"] = match(index, root, reference.path, reference.symbol, reference.repository)
            for node in item["match"].get("nodes", []):
                if node["id"] not in node_ids:
                    node_ids.append(node["id"])
        items.append(item)
    edges, unresolved, links = _collect(index, node_ids) if index is not None else ([], [], [])
    by_source: Dict[str, int] = {}
    for item in items:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    counts: Dict[str, Any] = {"references": len(items), "bySource": by_source,
                              "attributionUnresolved": sum(1 for r in found if refs.unattributed(r))}
    if index is not None:
        counts.update({
            "matched": sum(1 for i in items if i["match"]["matched"]),
            "unmatched": sum(1 for i in items if not i["match"]["matched"]),
            "nodes": len(node_ids),
            "edgesAmong": sum(1 for e in edges if e["among"]),
            "edgesToNeighbours": sum(1 for e in edges if not e["among"]),
            "unresolved": len(unresolved),
        })
    return {
        "target": task.id, "kind": "task",
        "task": {"id": task.id, "title": task.title, "status": task.status},
        "graph": graph, "sources": SOURCES, "counts": counts,
        "references": items, "notCurrent": dropped,
        "edges": edges, "unresolved": unresolved, "fileLinks": links,
    }


def _artifact_map(project: Project, index: Optional[Index], root: Path, graph: Dict[str, Any], target: str,
                  path: str, symbol: Optional[str], current: List[Reference]) -> Dict[str, Any]:
    touching = [r for r in current if r.artifact is not None and r.repository is None and r.path == path
                and (symbol is None or r.symbol == symbol)]
    tasks: List[Dict[str, Any]] = []
    for task_id in dict.fromkeys(r.task for r in touching):
        known = project.task(task_id)
        entry: Dict[str, Any] = {"id": task_id}
        if known is None:
            entry["missing"] = True
        else:
            entry.update({"title": known.title, "status": known.status})
        entry["references"] = [_reference_item(r) for r in touching if r.task == task_id]
        entry["attributionUnresolved"] = all(refs.unattributed(r) for r in touching if r.task == task_id)
        tasks.append(entry)
    result: Dict[str, Any] = {"target": target, "kind": "symbol" if symbol else "file", "path": path,
                              "symbol": symbol, "graph": graph, "sources": SOURCES, "tasks": tasks,
                              "attributionUnresolved": sum(1 for r in touching if refs.unattributed(r))}
    if index is None:
        return result
    found = match(index, root, path, symbol)
    edges, unresolved, links = _collect(index, [n["id"] for n in found.get("nodes", [])])
    result.update({"match": found, "edges": edges, "unresolved": unresolved, "fileLinks": links})
    return result


# Text ---------------------------------------------------------------------


def _name(node: Dict[str, Any]) -> str:
    return f"{node['path']}#{node['symbol']}" if node.get("symbol") else node["path"]


def _graph_line(graph: Dict[str, Any]) -> str:
    if not graph["present"]:
        return f"No graph: {graph['reason']}."
    when = f", indexed {graph['indexedAt']}" if graph.get("indexedAt") else ""
    return f"Graph: {graph['index']} (schema {graph.get('schema') or '?'}{when}); read only, never authority."


def _match_text(found: Dict[str, Any]) -> str:
    if not found["matched"]:
        return f"no node: {found['reason']}, {REASONS[found['reason']]}"
    nodes = ", ".join(f"{n['id']} {n['kind']}" + (f" lines {n['lines']}" if n.get("lines") else "")
                      for n in found["nodes"])
    return f"[{ENGINE}] {nodes} (rule {found['rule']})" + (" · stale: the file changed since it was indexed"
                                                          if found.get("stale") else "")


def _reference_text(item: Dict[str, Any]) -> List[str]:
    parts = [item.get("relation") or "—", item.get("scope") or "—", item.get("verification") or "—"]
    artifact = refs._artifact(item)
    if item.get("attribution") == refs.UNRESOLVED:
        artifact = f"{refs.UNRESOLVED_TEXT}: {artifact}"
    text = f"  [{item['source']}] {artifact} · " + " · ".join(parts)
    if item.get("confidence") is not None:
        text += f" · confidence {item['confidence']}"
    if item.get("evidence"):
        text += f" · evidence: {item['evidence']}"
    lines = [text + f" — {item['authority']}"]
    if "match" in item:
        lines.append(f"      {_match_text(item['match'])}")
    return lines


def _edge_text(edge: Dict[str, Any]) -> str:
    count = len(edge["at"])
    where = edge["at"][0] + (f" and {count - 1} more" if count > 1 else "")
    confidence = f" {edge['confidence']}" if edge.get("confidence") else ""
    return f"  {_name(edge['from'])} -> {_name(edge['to'])} · {edge['kind']}{confidence} ×{count} · {where}"


def _edges_text(result: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    among = [e for e in result["edges"] if e["among"]]
    others = [e for e in result["edges"] if not e["among"]]
    if result["kind"] == "task":
        lines += ["", f"Edges among the task's nodes [{ENGINE}] ({len(among)})"]
        lines += [_edge_text(e) for e in among] or ["  none"]
        lines += ["", f"Edges to direct neighbours [{ENGINE}] ({len(others)})"]
    else:
        lines += ["", f"Edges [{ENGINE}] ({len(result['edges'])})"]
        others = result["edges"]
    lines += [_edge_text(e) for e in others] or ["  none"]
    if result["fileLinks"]:
        lines += ["", f"Files linked through their symbols [{ENGINE}] ({len(result['fileLinks'])})"]
        for link in result["fileLinks"]:
            arrow = "->" if link["direction"] == "out" else "<-"
            lines.append(f"  {link.get('file', '')} {arrow} {link['path']} · {link['kind']} ×{link['count']}")
    lines += ["", f"Unresolved [{UNRESOLVED}] ({len(result['unresolved'])}): unknown, not absent; "
                  "read the code and record what you find with `praukron ref add`, source agent"]
    # Text names each unresolved name once per symbol, whatever its kinds; JSON keeps them apart.
    counted: Dict[str, Dict[str, int]] = {}
    for item in result["unresolved"]:
        name = f"{item['qualifier']}.{item['name']}" if item.get("qualifier") else item["name"]
        names = counted.setdefault(_name(item["from"]), {})
        names[name] = names.get(name, 0) + len(item["at"])
    grouped = {origin: [n + (f" ×{c}" if c > 1 else "") for n, c in names.items()]
               for origin, names in counted.items()}
    for origin, names in grouped.items():
        shown = ", ".join(names[:_TEXT_NAMES]) + (f", and {len(names) - _TEXT_NAMES} more" if len(names) > _TEXT_NAMES else "")
        lines.append(f"  {origin}: {shown}")
    if not grouped:
        lines.append("  none")
    return lines


def render(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    graph = result["graph"]
    if result["kind"] == "task":
        task, counts = result["task"], result["counts"]
        lines += [f"{task['id']} — {task['title']} [{task['status']}]", _graph_line(graph)]
        sources = ", ".join(f"{n} {s}" for s, n in sorted(counts["bySource"].items()))
        summary = f"{counts['references']} current references" + (f" ({sources})" if sources else "")
        if counts["attributionUnresolved"]:
            summary += f", {counts['attributionUnresolved']} of them unresolved attribution"
        if graph["present"]:
            summary += (f"; {counts['matched']} match a node, {counts['unmatched']} do not; "
                        f"{counts['edgesAmong']} edges among them, {counts['edgesToNeighbours']} to neighbours, "
                        f"{counts['unresolved']} unresolved")
        lines.append(summary)
        if result["notCurrent"]:
            lines.append(f"{result['notCurrent']} more are replaced or withdrawn: `praukron refs {task['id']}`")
        # One section per source, so agent records never sit among Git capture or engine output.
        for source in sorted({i["source"] for i in result["references"]}):
            group = [i for i in result["references"] if i["source"] == source]
            lines += ["", f"References [{source}] ({len(group)}): {SOURCES.get(source, 'recorded')}"]
            for item in group:
                lines += _reference_text(item)
        if not result["references"]:
            lines += ["", "No file or symbol is referenced."]
    else:
        lines += [result["target"], _graph_line(graph)]
        if "match" in result:
            lines.append(_match_text(result["match"]))
        lines += ["", f"Tasks whose references touch it ({len(result['tasks'])})"
                  + (f"; {result['attributionUnresolved']} of their references are unresolved attribution"
                     if result.get("attributionUnresolved") else "")]
        for task in result["tasks"]:
            title = "not found" if task.get("missing") else f"{task['title']} [{task['status']}]"
            lines.append(f"{task['id']} — {title}"
                         + (f" — {refs.UNRESOLVED_TEXT}" if task.get("attributionUnresolved") else ""))
            lines += [line for item in task["references"] for line in _reference_text(item)]
        if not result["tasks"]:
            lines.append("  none")
    if graph["present"]:
        lines += _edges_text(result)
    return "\n".join(lines) + "\n"
