"""`praukron engine map`: the engine graph joined to task-code references (T-GRAPH-JOIN-01).

The index here is a small SQLite file written by the test itself with the
store's own schema, so the reading path is tested on every platform without a
native build. `tests/test_engine_library.py` runs the same command on an index
the real library wrote.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, codemap, engine, layout  # noqa: E402
from test_code_references import chronicle_bytes, reference  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

# The tables of the engine's store that Praukron reads, as crowbett/codegraph
# 0dbef57e creates them (schema 21); its full-text tables are left out.
SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE repos(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
    indexed_at INTEGER NOT NULL DEFAULT 0, write_started INTEGER NOT NULL DEFAULT 0,
    owner TEXT NOT NULL DEFAULT '', markers TEXT NOT NULL DEFAULT '',
    symbols INTEGER NOT NULL DEFAULT 0, edges INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0, exact INTEGER NOT NULL DEFAULT 0,
    likely INTEGER NOT NULL DEFAULT 0, name_only INTEGER NOT NULL DEFAULT 0);
CREATE TABLE files(
    id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL, lang TEXT NOT NULL, hash TEXT NOT NULL, mtime INTEGER NOT NULL,
    size INTEGER NOT NULL, lines INTEGER NOT NULL, module TEXT NOT NULL, package TEXT NOT NULL,
    pkgname TEXT, parse_errors INTEGER NOT NULL DEFAULT 0, UNIQUE(repo_id, path));
CREATE TABLE symbols(
    id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    repo_id INTEGER NOT NULL, parent_id INTEGER, name TEXT NOT NULL, kind TEXT NOT NULL,
    qname TEXT NOT NULL, qkey TEXT NOT NULL, container TEXT, start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL, end_line INTEGER NOT NULL, end_col INTEGER NOT NULL,
    signature TEXT NOT NULL, doc TEXT, exported INTEGER NOT NULL, is_default INTEGER NOT NULL,
    recv TEXT, ret TEXT, fields TEXT);
CREATE TABLE edges(
    id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    from_symbol INTEGER, from_name TEXT, name TEXT NOT NULL, qualifier TEXT, kind TEXT NOT NULL,
    line INTEGER NOT NULL, col INTEGER NOT NULL, to_symbol INTEGER, to_file INTEGER,
    confidence TEXT, extra TEXT);
"""

SOURCES = {
    "pkg/util.py": "def normalize(text):\n    return text.strip()\n\n\ndef slug(text):\n"
                   "    return normalize(text).replace(' ', '-')\n",
    "pkg/service.py": "from pkg.util import normalize\n\n\nclass Catalog:\n    def add(self, name):\n"
                      "        self.items.append(normalize(name))\n        return len(self.items)\n",
    "main.py": "from pkg.service import Catalog\n\n\ndef main():\n    Catalog().add('x')\n",
    "native/lib.rs": "pub struct Engine;\nimpl Engine { pub fn open() {} }\n",
    "docs/guide.md": "# Guide\n",
}

FILES = [  # id, path, module
    (1, "pkg/util.py", "pkg.util"),
    (2, "pkg/service.py", "pkg.service"),
    (3, "main.py", "main"),
    (4, "native/lib.rs", ""),
]
SYMBOLS = [  # id, file, parent, name, kind, start, end
    (10, 1, None, "normalize", "function", 1, 2),
    (11, 1, None, "slug", "function", 5, 6),
    (20, 2, None, "Catalog", "class", 4, 9),
    (22, 2, 20, "add", "method", 5, 7),
    (23, 2, 20, "names", "method", 8, 9),
    (24, 2, 20, "value", "method", 8, 8),
    (25, 2, 20, "value", "method", 9, 9),
    (30, 3, None, "main", "function", 4, 5),
    (40, 4, None, "Engine", "struct", 1, 1),
    (41, 4, 40, "open", "method", 2, 2),
]
EDGES = [  # id, file, from_symbol, name, qualifier, kind, line, to_symbol, to_file, confidence
    (1, 2, None, "normalize", "pkg.util", "import", 1, None, 1, None),
    (2, 2, None, "os", None, "import", 2, None, None, None),
    (3, 2, 22, "normalize", None, "call", 6, 10, 1, "exact"),
    (4, 2, 22, "append", "self.items", "call", 6, None, None, None),
    (5, 2, 22, "len", None, "call", 7, None, None, None),
    (6, 1, 11, "normalize", None, "call", 6, 10, 1, "exact"),
    (7, 1, 11, "replace", None, "call", 6, None, None, None),
    (8, 3, None, "Catalog", "pkg.service", "import", 1, None, 2, None),
    (9, 3, 30, "Catalog", None, "call", 5, 20, 2, "exact"),
    (10, 3, 30, "add", None, "call", 5, 22, 2, "likely"),
    (11, 2, 22, "normalize", None, "call", 7, 10, 1, "exact"),
]


def chain(symbol_id: int) -> str:
    rows = {s[0]: s for s in SYMBOLS}
    names, current = [], rows[symbol_id]
    while current is not None:
        names.append(current[3])
        current = rows.get(current[2]) if current[2] is not None else None
    return ".".join(reversed(names))


def write_index(db: Path, repo_root: str, sizes: dict[str, int], wal: bool = False) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta VALUES ('schema', '21')")
    con.execute("INSERT INTO repos(id, name, root, indexed_at) VALUES (1, 'shop', ?, 1795765553)", (repo_root,))
    modules = dict((f[0], f[2]) for f in FILES)
    for file_id, path, module in FILES:
        con.execute("INSERT INTO files VALUES (?, 1, ?, ?, 'h', 0, ?, 9, ?, '', NULL, 0)",
                    (file_id, path, "rust" if path.endswith(".rs") else "python", sizes[path], module))
    for symbol_id, file_id, parent, name, kind, start, end in SYMBOLS:
        separator = "::" if not modules[file_id] else "."
        qname = separator.join(filter(None, [modules[file_id], chain(symbol_id).replace(".", separator)]))
        con.execute("INSERT INTO symbols VALUES (?, ?, 1, ?, ?, ?, ?, ?, NULL, ?, 0, ?, 0, '', NULL, 1, 0, "
                    "NULL, NULL, NULL)", (symbol_id, file_id, parent, name, kind, qname, qname, start, end))
    for edge in EDGES:
        edge_id, file_id, source, name, qualifier, kind, line, to_symbol, to_file, confidence = edge
        con.execute("INSERT INTO edges VALUES (?, 1, ?, ?, NULL, ?, ?, ?, ?, 0, ?, ?, ?, NULL)",
                    (edge_id, file_id, source, name, qualifier, kind, line, to_symbol, to_file, confidence))
    con.commit()
    con.close()


AGENT_EVIDENCE = "add calls normalize on each name, read at pkg/service.py:6"

CODE = "# Code references\n\n" + "\n".join([
    reference("CR-T-ONE-01", "T-ONE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Scope="observed", Source="git_diff", Verification="verified"),
    reference("CR-T-ONE-02", "T-ONE", "pkg/util.py", Symbol="normalize", Kind="function",
              Scope="observed", Source="static_analysis", Verification="verified"),
    reference("CR-T-ONE-03", "T-ONE", "pkg/util.py", Scope="observed", Source="git_diff"),
    reference("CR-T-ONE-04", "T-ONE", "docs/guide.md", Relation="documents", Source="manual"),
    reference("CR-T-ONE-05", "T-ONE", "pkg/gone.py", Source="git_diff"),
    reference("CR-T-ONE-06", "T-ONE", "pkg/util.py", Symbol="removed_helper", Kind="function"),
    reference("CR-T-ONE-07", "T-ONE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Relation="depends_on", Source="agent", Confidence="0.8", Evidence=AGENT_EVIDENCE),
    reference("CR-T-TWO-01", "T-TWO", "pkg/service.py", Symbol="pkg.service.Catalog.names", Kind="method"),
    reference("CR-T-TWO-02", "T-TWO", "native/lib.rs", Symbol="Engine::open", Kind="method"),
    reference("CR-T-TWO-03", "T-TWO", "pkg/service.py", Symbol="names", Kind="method"),
    reference("CR-T-TWO-04", "T-TWO", "pkg/service.py", Symbol="Catalog.value", Kind="method"),
    reference("CR-T-TWO-05", "T-TWO", "pkg/util.py", Repository="billing"),
])


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


class MapCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-map-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir / "shop")
        for path, text in SOURCES.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        authority = self.root / layout.AUTHORITY_DIR
        (authority / "CODE.md").write_text(CODE, encoding="utf-8")
        tasks = authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        # T-THREE is the last task: its anchor goes at the end of the file.
        self.assertTrue(text.rstrip("\n").endswith(text[text.index("## T-THREE"):].rstrip("\n")))
        tasks.write_text(text.rstrip("\n") + "\n- Symbols: helper\n", encoding="utf-8")
        self.db = engine.index_path(self.root)
        self.sizes = {p: len(t.encode("utf-8")) for p, t in SOURCES.items()}
        # Written on another machine: the repository root it names is not this one.
        write_index(self.db, "/elsewhere/shop", self.sizes)
        environ = mock.patch.dict(os.environ, {engine.ENV_VAR: ""})
        environ.start()
        self.addCleanup(environ.stop)
        # The library is never loaded to read an index.
        loader = mock.patch.object(engine, "_cdll", side_effect=AssertionError("the library was loaded"))
        loader.start()
        self.addCleanup(loader.stop)
        self.before = chronicle_bytes(self.root)

    def tearDown(self) -> None:
        # AC-T-GRAPH-JOIN-01-05: nothing this feature computes reaches the chronicle.
        self.assertEqual(chronicle_bytes(self.root), self.before)

    def map(self, target: str) -> dict:
        code, out, err = run(self.root, "engine", "map", target, "--json")
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def text(self, target: str) -> str:
        code, out, err = run(self.root, "engine", "map", target)
        self.assertEqual((code, err), (0, ""))
        return out


class TestMatchingRules(MapCase):
    """Each rule of the module docstring, against the index directly."""

    def match(self, path, symbol=None, repository=None) -> dict:
        index = codemap.Index(self.db, self.root)
        self.addCleanup(index.close)
        return codemap.match(index, self.root, path, symbol, repository)

    def ids(self, found: dict) -> list[str]:
        return [n["id"] for n in found["nodes"]]

    def test_a_file_matches_by_its_path(self):
        found = self.match("pkg/util.py")
        self.assertEqual((found["rule"], self.ids(found)), ("file", ["f:1"]))
        self.assertNotIn("stale", found)

    def test_a_symbol_matches_by_path_and_name_inside_the_file(self):
        found = self.match("pkg/util.py", "normalize")
        self.assertEqual((found["rule"], self.ids(found)), ("name", ["s:10"]))
        self.assertEqual(found["nodes"][0]["lines"], "1-2")

    def test_a_class_member_matches_through_its_parent_chain(self):
        found = self.match("pkg/service.py", "Catalog.add")
        self.assertEqual((found["rule"], self.ids(found)), ("name", ["s:22"]))
        self.assertEqual(found["nodes"][0]["symbol"], "Catalog.add")
        # The same name in another file is another symbol.
        self.assertEqual(self.match("pkg/util.py", "Catalog.add")["reason"], "symbol-absent")

    def test_names_the_engine_qualifies_differently_are_read_as_names_inside_the_file(self):
        found = self.match("pkg/service.py", "pkg.service.Catalog.names")
        self.assertEqual((found["rule"], self.ids(found)), ("qualified", ["s:23"]))
        found = self.match("native/lib.rs", "Engine::open")
        self.assertEqual((found["rule"], self.ids(found)), ("qualified", ["s:41"]))

    def test_a_bare_member_name_matches_only_when_one_member_has_it(self):
        found = self.match("pkg/service.py", "names")
        self.assertEqual((found["rule"], self.ids(found)), ("member", ["s:23"]))
        self.assertEqual(self.match("pkg/service.py", "missing")["reason"], "symbol-absent")

    def test_a_name_defined_twice_matches_both_definitions(self):
        self.assertEqual(self.ids(self.match("pkg/service.py", "Catalog.value")), ["s:24", "s:25"])

    def test_a_symbol_absent_at_the_indexed_commit_is_unmatched_with_the_reason(self):
        found = self.match("pkg/util.py", "removed_helper")
        self.assertEqual((found["matched"], found["reason"], found["file"]), (False, "symbol-absent", "f:1"))

    def test_files_the_index_does_not_hold(self):
        self.assertEqual(self.match("docs/guide.md")["reason"], "not-indexed")
        self.assertEqual(self.match("pkg/gone.py")["reason"], "file-absent")
        self.assertEqual(self.match(None, "helper")["reason"], "no-path")
        self.assertEqual(self.match("pkg/util.py", repository="billing")["reason"], "other-repository")

    def test_a_file_changed_since_indexing_is_stale(self):
        (self.root / "pkg/util.py").write_text(SOURCES["pkg/util.py"] + "\n# edited\n", encoding="utf-8")
        self.assertTrue(self.match("pkg/util.py", "normalize")["stale"])


class TestTaskMap(MapCase):
    """AC-T-GRAPH-JOIN-01-01 and -05: a task's code map, every item labelled with its source."""

    def test_references_nodes_edges_unresolved_and_unmatched_by_source(self):
        result = self.map("T-ONE")
        self.assertTrue(result["graph"]["present"])
        self.assertEqual(result["graph"]["repositories"], [{"name": "shop", "root": "/elsewhere/shop"}])
        by_id = {r["id"]: r for r in result["references"]}
        self.assertEqual(by_id["CR-T-ONE-01"]["source"], "reference:git_diff")
        self.assertEqual(by_id["CR-T-ONE-02"]["source"], "reference:static_analysis")
        self.assertEqual(by_id["CR-T-ONE-04"]["source"], "reference:manual")
        self.assertEqual([n["id"] for n in by_id["CR-T-ONE-01"]["match"]["nodes"]], ["s:22"])
        self.assertEqual({i: by_id[i]["match"].get("reason") for i in ("CR-T-ONE-04", "CR-T-ONE-05", "CR-T-ONE-06")},
                         {"CR-T-ONE-04": "not-indexed", "CR-T-ONE-05": "file-absent",
                          "CR-T-ONE-06": "symbol-absent"})
        edges = {(e["from"]["id"], e["to"]["id"], e["kind"]): e for e in result["edges"]}
        self.assertTrue(all(e["source"] == "engine" for e in result["edges"]))
        # Among the task's nodes: add calls normalize, at two places, counted once.
        among = edges[("s:22", "s:10", "call")]
        self.assertTrue(among["among"])
        self.assertEqual(among["at"], ["pkg/service.py:6", "pkg/service.py:7"])
        # To direct neighbours, both ways: slug and main call into the task's symbols.
        self.assertFalse(edges[("s:11", "s:10", "call")]["among"])
        self.assertEqual(edges[("s:30", "s:22", "call")]["confidence"], "likely")
        # The file pkg/util.py is imported by pkg/service.py.
        self.assertIn(("f:2", "f:1", "import"), edges)
        unresolved = {(u["from"]["id"], u["name"]) for u in result["unresolved"]}
        self.assertTrue(all(u["source"] == "engine-unresolved" for u in result["unresolved"]))
        self.assertIn(("s:22", "len"), unresolved)
        self.assertIn(("s:22", "append"), unresolved)
        self.assertNotIn(("s:11", "replace"), unresolved)  # slug is a neighbour, not the task's own
        self.assertEqual(result["counts"]["unmatched"], 3)
        self.assertEqual(result["counts"]["bySource"]["reference:agent"], 2)

    def test_an_agent_recorded_relation_is_shown_apart_with_evidence_and_verification(self):
        result = self.map("T-ONE")
        agent = [r for r in result["references"] if r["id"] == "CR-T-ONE-07"][0]
        self.assertEqual((agent["source"], agent["evidence"], agent["verification"], agent["confidence"]),
                         ("reference:agent", AGENT_EVIDENCE, "unverified", "0.8"))
        self.assertFalse(any(e.get("source", "").startswith("reference") for e in result["edges"]))
        text = self.text("T-ONE")
        section = text[text.index("References [reference:agent]"):]
        section = section[:section.index("\n\n")]
        self.assertIn(f"evidence: {AGENT_EVIDENCE}", section)
        self.assertIn("unverified", section)
        self.assertNotIn("[reference:git_diff]", section)
        self.assertNotIn("[engine]  ", section)
        self.assertLess(text.index("References [reference:agent]"), text.index("Edges among the task's nodes [engine]"))
        self.assertIn("Unresolved [engine-unresolved]", text)
        self.assertIn("unknown, not absent", text)

    def test_text_labels_every_section(self):
        text = self.text("T-TWO")
        self.assertIn("T-TWO — Build on it [TODO]", text)
        self.assertIn("(rule qualified)", text)
        self.assertIn("(rule member)", text)
        self.assertIn("other-repository", text)

    def test_an_anchor_with_no_path_is_unmatched(self):
        result = self.map("T-THREE")
        self.assertEqual([(r["anchor"], r["match"]["reason"]) for r in result["references"]], [(True, "no-path")])

    def test_an_unknown_task_fails(self):
        code, out, err = run(self.root, "engine", "map", "T-NOPE")
        self.assertEqual(code, 1)
        self.assertIn("unknown-task", err)


class TestArtifactMap(MapCase):
    """AC-T-GRAPH-JOIN-01-02: a symbol or a file, its tasks and its neighbours."""

    def test_a_symbol_lists_its_tasks_and_neighbours(self):
        result = self.map("pkg/util.py#normalize")
        self.assertEqual(result["kind"], "symbol")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        self.assertEqual([r["source"] for r in result["tasks"][0]["references"]], ["reference:static_analysis"])
        callers = {(e["from"]["symbol"], e["source"]) for e in result["edges"]}
        self.assertEqual(callers, {("slug", "engine"), ("Catalog.add", "engine")})
        text = self.text("pkg/service.py#Catalog.add")
        self.assertIn("T-ONE — Lay the foundation [DONE]", text)
        self.assertIn("[reference:agent]", text)
        self.assertIn("main.py#main -> pkg/service.py#Catalog.add · call likely", text)

    def test_a_file_lists_the_tasks_of_its_symbols_and_its_linked_files(self):
        result = self.map("pkg/util.py")
        self.assertEqual(result["kind"], "file")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        self.assertEqual({r["id"] for r in result["tasks"][0]["references"]},
                         {"CR-T-ONE-02", "CR-T-ONE-03", "CR-T-ONE-06"})
        links = {(l["direction"], l["path"], l["kind"]): l["count"] for l in result["fileLinks"]}
        self.assertEqual(links, {("in", "pkg/service.py", "call"): 2})
        self.assertIn(("f:2", "f:1"), {(e["from"]["id"], e["to"]["id"]) for e in result["edges"]})

    def test_an_unindexed_file_still_lists_its_tasks(self):
        result = self.map("docs/guide.md")
        self.assertEqual(result["match"]["reason"], "not-indexed")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])


class TestWithoutGraph(MapCase):
    """AC-T-GRAPH-JOIN-01-03 and -04."""

    def test_no_index_answers_from_references_alone_and_succeeds(self):
        self.db.unlink()
        result = self.map("T-ONE")
        self.assertFalse(result["graph"]["present"])
        self.assertIn("no graph index", result["graph"]["reason"])
        self.assertIn("no graph library is present", result["graph"]["reason"])
        self.assertIn("references alone", result["graph"]["reason"])
        self.assertEqual(len(result["references"]), 7)
        self.assertTrue(all("match" not in r for r in result["references"]))
        self.assertEqual(result["edges"], [])
        text = self.text("pkg/util.py#normalize")
        self.assertIn("No graph: no graph index at .praukron/engine/graph.db", text)
        self.assertIn("T-ONE — Lay the foundation", text)

    def test_a_library_without_an_index_is_still_no_graph(self):
        self.db.unlink()
        library = self.dir / "libpraukron_graph.so"
        library.write_bytes(b"not loaded")
        with mock.patch.dict(os.environ, {engine.ENV_VAR: str(library)}):
            result = self.map("T-ONE")
        self.assertFalse(result["graph"]["present"])
        self.assertTrue(result["graph"]["library"])
        self.assertNotIn("no graph library", result["graph"]["reason"])

    def test_an_index_from_another_machine_is_read_without_the_library(self):
        # setUp names another root and makes loading the library fail the test.
        result = self.map("T-ONE")
        self.assertTrue(result["graph"]["present"])
        self.assertFalse(result["graph"]["library"])
        self.assertEqual(result["counts"]["matched"], 4)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs a directory this user cannot write")
    def test_a_wal_index_copied_without_its_shared_memory_file_into_a_read_only_place(self):
        self.db.unlink()
        write_index(self.db, "/elsewhere/shop", self.sizes, wal=True)
        for side in ("-wal", "-shm"):
            Path(str(self.db) + side).unlink(missing_ok=True)
        directory = self.db.parent
        mode = directory.stat().st_mode
        directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(directory.chmod, mode)
        result = self.map("pkg/util.py#normalize")
        self.assertTrue(result["graph"]["present"])
        self.assertEqual([n["id"] for n in result["match"]["nodes"]], ["s:10"])

    def test_an_unreadable_index_answers_from_references_and_says_why(self):
        self.db.write_bytes(b"this is not a database")
        result = self.map("T-ONE")
        self.assertFalse(result["graph"]["present"])
        self.assertIn("references alone", result["graph"]["reason"])

    def test_the_existing_refs_output_does_not_change(self):
        code, out, err = run(self.root, "refs", "T-ONE", "--json")
        self.assertEqual(code, 0, err)
        self.assertNotIn("graph", json.loads(out))


class TestGuidance(unittest.TestCase):
    """AC-T-GRAPH-JOIN-01-06: what an agent is told to do with an unresolved edge."""

    ROOT = Path(__file__).resolve().parents[1]

    def flat(self, name: str) -> str:
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def test_the_workflows_say_to_read_and_record_an_unresolved_edge(self):
        for name in (".praukron/commands/praukron-work.md", ".agents/skills/praukron/SKILL.md"):
            with self.subTest(surface=name):
                body = self.flat(name)
                self.assertIn("engine map", body)
                self.assertIn("unresolved", body)
                self.assertRegex(body, r"`ref add`.*agent.*`evidence`")
        self.assertIn("`engine map <task>` adds the graph", self.flat("AGENTS.md"))

    def test_the_map_itself_says_it_at_the_point_of_need(self):
        self.assertIn("record what you find with `praukron ref add`, source agent",
                      " ".join(Path(codemap.__file__).read_text(encoding="utf-8").split()))


if __name__ == "__main__":
    unittest.main()
