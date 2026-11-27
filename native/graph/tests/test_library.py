"""Loads the graph library with ctypes, indexes the fixture and checks the answers.

Standard library only. The library path comes from PRAUKRON_GRAPH_LIB. When
PRAUKRON_GRAPH_REPORT names a file, a JSON summary of what the library
returned is written there.

    PRAUKRON_GRAPH_LIB=target/release/libpraukron_graph.dylib \
        python3 -I native/graph/tests/test_library.py
"""

import collections
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture", "shop")
ENGINE_COMMIT = "0dbef57e463cd7511376487146b875fb91b4050d"

# Callers the fixture defines, as (caller name, file path suffix).
EXPECTED_CALLERS = {
    "normalize": {("slug", "/pkg/util.py"), ("add", "/pkg/service.py")},
    "formatPrice": {("renderRow", "/web/format.js")},
}

REPORT = {}


def load(path):
    lib = ctypes.CDLL(path)
    lib.praukron_graph_info.restype = ctypes.c_void_p
    lib.praukron_graph_info.argtypes = []
    lib.praukron_graph_open.restype = ctypes.c_void_p
    lib.praukron_graph_open.argtypes = [ctypes.c_char_p]
    lib.praukron_graph_call.restype = ctypes.c_void_p
    lib.praukron_graph_call.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.praukron_graph_free.restype = None
    lib.praukron_graph_free.argtypes = [ctypes.c_void_p]
    lib.praukron_graph_close.restype = None
    lib.praukron_graph_close.argtypes = [ctypes.c_void_p]
    lib.praukron_graph_last_error.restype = ctypes.c_char_p
    lib.praukron_graph_last_error.argtypes = []
    return lib


def take(lib, pointer):
    try:
        return json.loads(ctypes.string_at(pointer).decode("utf-8"))
    finally:
        lib.praukron_graph_free(pointer)


class GraphLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("PRAUKRON_GRAPH_LIB")
        if not path:
            raise unittest.SkipTest("PRAUKRON_GRAPH_LIB is not set")
        cls.lib = load(path)
        cls.tmp = tempfile.mkdtemp(prefix="praukron-graph-")
        cls.root = os.path.join(cls.tmp, "shop")
        shutil.copytree(FIXTURE, cls.root)
        cls.db = os.path.join(cls.tmp, "store", "graph.db")
        cls.handle = cls.lib.praukron_graph_open(json.dumps({"db": cls.db}).encode())
        if not cls.handle:
            raise AssertionError("open failed: %r" % cls.lib.praukron_graph_last_error())
        cls.indexed = cls.call({"op": "index", "roots": [cls.root], "full": True})
        REPORT["index"] = cls.indexed

    @classmethod
    def tearDownClass(cls):
        cls.lib.praukron_graph_close(cls.handle)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def call(cls, request):
        return take(cls.lib, cls.lib.praukron_graph_call(cls.handle, json.dumps(request).encode()))

    def result(self, request):
        response = self.call(request)
        self.assertTrue(response.get("ok"), response)
        return response["result"]

    def test_info(self):
        info = take(self.lib, self.lib.praukron_graph_info())
        REPORT["info"] = info
        self.assertEqual(info["abi"], 1)
        self.assertEqual(info["engine_commit"], ENGINE_COMMIT)

    def test_index_saw_the_fixture(self):
        self.assertTrue(self.indexed.get("ok"), self.indexed)
        self.assertEqual(self.indexed["result"]["files"], 5)
        self.assertEqual(self.indexed["result"]["parse_errors"], 0)

    def test_callers_of_known_functions(self):
        found = {}
        for symbol, expected in sorted(EXPECTED_CALLERS.items()):
            tree = self.result({"op": "callers", "symbol": symbol, "depth": 1})
            callers = set()
            for root in tree["roots"]:
                self.assertEqual(root["name"], symbol)
                for child in root.get("children", []):
                    self.assertIn("name", child, child)
                    callers.add((child["name"], child["file"]))
            got = {(name, suffix) for name, suffix in expected
                   if any(n == name and f.endswith(suffix) for n, f in callers)}
            found[symbol] = sorted("%s %s" % (n, f.rsplit("/", 2)[-2] + "/" + f.rsplit("/", 1)[-1])
                                   for n, f in callers)
            self.assertEqual(got, expected, callers)
            self.assertEqual(len(callers), len(expected), callers)
        REPORT["callers"] = found

    def test_whole_graph_matches_the_engine(self):
        graph = self.result({"op": "graph"})
        status = self.result({"op": "status"})
        counts = graph["counts"]
        nodes, edges = graph["nodes"], graph["edges"]

        # The engine's own statistics.
        self.assertEqual(counts["files"], status["files"])
        self.assertEqual(counts["symbols"], status["symbols"])
        self.assertEqual(counts["non_import_edges"], status["edges"])
        self.assertEqual(counts["resolved_non_import_edges"], status["resolved"])
        by_confidence = collections.Counter(
            e["confidence"] for e in edges if e["kind"] != "import" and e["target"]
            and e["target"].startswith("s:"))
        self.assertEqual(dict(by_confidence), status["by_confidence"])

        # The engine's own store, read directly.
        con = sqlite3.connect(self.db)
        try:
            raw = {t: con.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
                   for t in ("repos", "files", "symbols", "edges")}
        finally:
            con.close()
        self.assertEqual(len(graph["repos"]), raw["repos"])
        self.assertEqual(counts["files"], raw["files"])
        self.assertEqual(counts["symbols"], raw["symbols"])
        self.assertEqual(len(edges), raw["edges"])
        self.assertEqual(counts["nodes"], len(nodes))
        self.assertEqual(counts["edges"], len(edges))

        # Every edge joins nodes that exist; every node has a path and lines.
        ids = [n["id"] for n in nodes]
        self.assertEqual(len(ids), len(set(ids)))
        known = set(ids)
        for n in nodes:
            self.assertTrue(n["path"], n)
            self.assertGreaterEqual(n["end_line"], n["start_line"], n)
        for e in edges:
            self.assertIn(e["source"], known, e)
            if e["target"] is not None:
                self.assertIn(e["target"], known, e)
            self.assertGreater(e["line"], 0, e)

        # A call the fixture makes is in the graph as an edge.
        by_name = {(n.get("name"), n["path"]): n["id"] for n in nodes if n["kind"] != "file"}
        slug = by_name[("slug", "pkg/util.py")]
        normalize = by_name[("normalize", "pkg/util.py")]
        self.assertTrue(any(e["kind"] == "call" and e["source"] == slug and e["target"] == normalize
                            for e in edges), "no call edge slug -> normalize")

        REPORT["graph"] = {
            "counts": counts,
            "status": {k: status[k] for k in ("files", "symbols", "edges", "resolved", "by_confidence")},
            "store_rows": raw,
            "node_kinds": dict(sorted(collections.Counter(n["kind"] for n in nodes).items())),
            "edge_kinds": dict(sorted(collections.Counter(e["kind"] for e in edges).items())),
            "unresolved_edges": sum(1 for e in edges if e["target"] is None),
            "sample_node": next(n for n in nodes if n.get("name") == "add"),
            "sample_edge": next(e for e in edges if e["source"] == slug and e["target"] == normalize),
        }

    def test_errors_come_back_as_data(self):
        bad = self.call({"op": "no-such-op"})
        self.assertFalse(bad["ok"])
        self.assertIn("unknown op", bad["error"])
        missing = self.call({"op": "callers"})
        self.assertFalse(missing["ok"])
        self.assertIsNone(self.lib.praukron_graph_open(b"not json") or None)
        self.assertTrue(self.lib.praukron_graph_last_error())


def main():
    program = unittest.main(exit=False, verbosity=2)
    out = os.environ.get("PRAUKRON_GRAPH_REPORT")
    if out:
        REPORT["tests"] = {"run": program.result.testsRun,
                           "failures": len(program.result.failures),
                           "errors": len(program.result.errors),
                           "skipped": len(program.result.skipped)}
        with open(out, "w") as f:
            json.dump(REPORT, f, indent=1, sort_keys=True)
    sys.exit(0 if program.result.wasSuccessful() and not program.result.skipped else 1)


if __name__ == "__main__":
    main()
