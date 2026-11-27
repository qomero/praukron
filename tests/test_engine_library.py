"""Praukron drives the real graph library (T-GRAPH-LOAD-01).

Runs only where PRAUKRON_GRAPH_LIB names a built library: the `Graph library`
workflow sets it after the build. Elsewhere every test is skipped, so the main
suite needs no native build.

    PRAUKRON_GRAPH_LIB=native/graph/target/release/libpraukron_graph.dylib \
        python3 -m unittest tests.test_engine_library
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, engine, layout  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

FIXTURE = ROOT / "native" / "graph" / "tests" / "fixture" / "shop"
ENGINE_COMMIT = "0dbef57e463cd7511376487146b875fb91b4050d"


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(os.environ.get(engine.ENV_VAR), f"{engine.ENV_VAR} is not set")
class RealLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-engine-real-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir / "shop")
        for item in FIXTURE.iterdir():
            target = self.root / item.name
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, target)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
        self.chronicle = self.root / layout.AUTHORITY_DIR
        self.before = snapshot(self.chronicle)

    def tearDown(self) -> None:
        self.assertEqual(snapshot(self.chronicle), self.before, "a chronicle record changed")

    def library_status(self) -> dict:
        library = engine.load(Path(os.environ[engine.ENV_VAR]))
        with library.open(engine.index_path(self.root)) as store:
            return store.status()

    def test_status_reports_the_engine_commit_and_interface(self) -> None:
        code, out, err = run(self.root, "engine", "status", "--json")
        self.assertEqual((code, err), (0, ""))
        state = json.loads(out)
        self.assertTrue(state["present"])
        self.assertEqual(state["from"], "environment")
        self.assertEqual(state["library"]["abi"], engine.ABI_VERSION)
        self.assertEqual(state["library"]["engine_commit"], ENGINE_COMMIT)
        self.assertFalse(state["index"]["present"])
        code, out, _ = run(self.root, "engine", "status")
        self.assertIn(ENGINE_COMMIT, out)

    def test_index_is_derived_ignored_and_refreshed_after_an_edit(self) -> None:
        code, out, err = run(self.root, "engine", "index", "--json")
        self.assertEqual(code, 0, err)
        first = json.loads(out)
        self.assertEqual(first["action"], "indexed")
        self.assertEqual(first["stats"]["files"], 5)
        self.assertEqual(first["stats"]["parse_errors"], 0)
        db = engine.index_path(self.root)
        self.assertTrue(db.is_file())
        self.assertEqual(db.relative_to(self.root).as_posix(), ".praukron/engine/graph.db")
        # Ignored by Git: nothing under the index directory is untracked or staged.
        self.assertEqual(git(self.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(git(self.root, "check-ignore", ".praukron/engine/graph.db").strip(),
                         ".praukron/engine/graph.db")
        # No record cites it.
        for name, data in snapshot(self.chronicle).items():
            self.assertNotIn(b".praukron/engine", data, name)
        symbols = first["status"]["symbols"]

        util = self.root / "pkg" / "util.py"
        util.write_text(util.read_text(encoding="utf-8")
                        + "\n\ndef shout(text):\n    return normalize(text).upper()\n", encoding="utf-8")
        code, out, err = run(self.root, "engine", "index", "--json")
        self.assertEqual(code, 0, err)
        second = json.loads(out)
        self.assertEqual(second["action"], "refreshed")
        self.assertEqual(second["stats"]["parsed"], 1)
        self.assertEqual(second["status"]["symbols"], symbols + 1)
        now = self.library_status()
        for key in ("files", "symbols", "edges", "resolved", "by_confidence"):
            self.assertEqual(second["status"][key], now[key], key)

        graph = json.loads(run(self.root, "engine", "export")[1])
        shout = [n for n in graph["nodes"] if n.get("name") == "shout"]
        self.assertEqual(len(shout), 1)
        self.assertEqual(shout[0]["path"], "pkg/util.py")
        self.assertTrue(any(e["source"] == shout[0]["id"] and e["kind"] == "call" for e in graph["edges"]))

        code, out, err = run(self.root, "engine", "index")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(f"Refreshed {self.root}: "), out)
        self.assertIn("0 parsed", out)

    def test_export_holds_every_node_and_edge_with_the_library_counts(self) -> None:
        self.assertEqual(run(self.root, "engine", "index")[0], 0)
        code, out, err = run(self.root, "engine", "export")
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)
        self.assertEqual(document["source"], "graph-library")
        self.assertEqual(document["engine_commit"], ENGINE_COMMIT)
        counts, nodes, edges = document["counts"], document["nodes"], document["edges"]
        self.assertEqual(counts["nodes"], len(nodes))
        self.assertEqual(counts["edges"], len(edges))
        self.assertEqual(counts["files"], sum(1 for n in nodes if n["kind"] == "file"))
        status = self.library_status()
        self.assertEqual(counts["files"], status["files"])
        self.assertEqual(counts["symbols"], status["symbols"])
        self.assertEqual(counts["non_import_edges"], status["edges"])
        self.assertEqual(counts["resolved_non_import_edges"], status["resolved"])
        con = sqlite3.connect(str(engine.index_path(self.root)))
        try:
            rows = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("files", "symbols", "edges")}
        finally:
            con.close()
        self.assertEqual((counts["files"], counts["symbols"], counts["edges"]),
                         (rows["files"], rows["symbols"], rows["edges"]))
        for node in nodes:
            self.assertTrue(node["kind"] and node["path"], node)
            self.assertGreaterEqual(node["end_line"], node["start_line"], node)
        for edge in edges:
            self.assertTrue(edge["kind"] and edge["path"], edge)
            self.assertGreater(edge["line"], 0, edge)

        target = self.dir / "export" / "graph.json"
        code, out, err = run(self.root, "engine", "export", "--output", str(target))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), document)
        self.assertIn(f"{counts['nodes']} nodes and {counts['edges']} edges", out)

    def test_map_joins_the_index_to_task_references_by_source(self) -> None:
        """AC-T-GRAPH-JOIN-01-01, -02 and -04 on an index the library wrote."""
        from test_code_references import reference

        (self.chronicle / "CODE.md").write_text("# Code references\n\n" + "\n".join([
            reference("CR-T-ONE-01", "T-ONE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
                      Scope="observed", Source="git_diff", Verification="verified"),
            reference("CR-T-ONE-02", "T-ONE", "pkg/util.py", Symbol="normalize", Kind="function",
                      Scope="observed", Source="static_analysis", Verification="verified"),
            reference("CR-T-ONE-03", "T-ONE", "web/format.js", Symbol="renderRow", Kind="function"),
            reference("CR-T-ONE-04", "T-ONE", "pkg/util.py", Symbol="removed_helper", Kind="function"),
        ]), encoding="utf-8")
        self.before = snapshot(self.chronicle)
        self.assertEqual(run(self.root, "engine", "index")[0], 0)

        code, out, err = run(self.root, "engine", "map", "T-ONE", "--json")
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertTrue(result["graph"]["present"])
        match = {r["id"]: r["match"] for r in result["references"]}
        self.assertEqual({i: m["matched"] for i, m in match.items()},
                         {"CR-T-ONE-01": True, "CR-T-ONE-02": True, "CR-T-ONE-03": True, "CR-T-ONE-04": False})
        self.assertEqual(match["CR-T-ONE-04"]["reason"], "symbol-absent")
        among = {(e["from"].get("symbol"), e["to"].get("symbol"), e["kind"]) for e in result["edges"] if e["among"]}
        self.assertIn(("Catalog.add", "normalize", "call"), among)
        self.assertTrue(all(e["source"] == "engine" for e in result["edges"]))
        self.assertTrue(all(u["source"] == "engine-unresolved" for u in result["unresolved"]))
        self.assertTrue(result["unresolved"], "the fixture calls names the engine cannot place")

        code, out, err = run(self.root, "engine", "map", "pkg/util.py#normalize", "--json")
        self.assertEqual((code, err), (0, ""))
        symbol = json.loads(out)
        self.assertEqual([t["id"] for t in symbol["tasks"]], ["T-ONE"])
        callers = {e["from"].get("symbol") for e in symbol["edges"] if e["to"].get("symbol") == "normalize"}
        self.assertTrue({"Catalog.add", "slug"} <= callers, callers)

        # The same index, read where no library is: only sqlite3 is needed.
        with mock.patch.dict(os.environ, {engine.ENV_VAR: ""}), \
                mock.patch.object(engine, "_cdll", side_effect=AssertionError("the library was loaded")):
            code, again, err = run(self.root, "engine", "map", "T-ONE", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(again)["edges"], result["edges"])

    def test_a_failing_call_is_one_line(self) -> None:
        library = engine.load(Path(os.environ[engine.ENV_VAR]))
        with library.open(self.dir / "scratch.db") as store:
            with self.assertRaises(engine.EngineError) as caught:
                store.call({"op": "no-such-op"})
            self.assertIn("unknown op", str(caught.exception))
            with self.assertRaises(engine.EngineError) as caught:
                store.index([self.dir / "does-not-exist"])
            self.assertNotIn("\n", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
