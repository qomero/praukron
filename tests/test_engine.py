"""The optional embedded graph library, without a native build (T-GRAPH-LOAD-01).

These tests run in the main suite, where no graph library exists. They cover
absence, a file that is not a library, a library that reports another
interface version or lacks a symbol, failures inside a call, and the whole
command flow against a library simulated in Python behind the same ctypes
calls. They also prove that every command that existed in 4.0.0 behaves the
same with or without a library, and that only `praukron engine` loads it.

`test_engine_library.py` drives the real library in the `Graph library`
workflow.
"""

from __future__ import annotations

import ast
import ctypes
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, engine, layout  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

ABSENT = "No graph library is present"

# Every top-level command of 4.0.0, read from its parser.
COMMANDS_4_0_0 = {
    "acr", "archive", "batch", "codegraph", "compile", "context", "contract", "criterion",
    "dashboard", "domains", "explain", "graph", "journal", "migrate", "ref", "refs", "respond",
    "retrieve", "scope", "status", "task", "validate",
}

# Commands that existed in 4.0.0 and do not change authority, in an order that
# lets each see the views the one before wrote.
EXISTING = [
    ["compile"],
    ["validate"],
    ["status"],
    ["graph"],
    ["explain", "T-ONE"],
    ["explain", "T-ONE", "--json"],
    ["domains", "--json"],
    ["context"],
    ["context", "T-ONE"],
    ["retrieve", "T-ONE"],
    ["retrieve", "why is T-TWO blocked?"],
    ["refs", "T-ONE"],
    ["journal", "append", "--schema"],
]

WRAPPER = r"""
import io, json, sys
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, sys.argv[1])
from praukron import cli
results = []
for argv in json.loads(sys.argv[2]):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exit:
            code = exit.code
    results.append({"argv": argv, "code": code, "out": out.getvalue(), "err": err.getvalue()})
sys.stdout.write(json.dumps({"results": results, "loaded": "praukron.engine" in sys.modules}))
"""


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in sorted(directory.rglob("*")) if p.is_file()}


class FakeLibrary:
    """The six C functions of the graph library, in Python.

    Each returns what the real one returns through ctypes: text as the address
    of a NUL-terminated buffer, handles as integers. Functions are plain
    closures so that the loader can set `restype` and `argtypes` on them.
    """

    def __init__(self, abi: int = 1, omit: tuple[str, ...] = (), info: object = None,
                 fail: dict[str, str] | None = None, raw: dict[str, bytes] | None = None,
                 raise_in: str | None = None, open_fails: bool = False) -> None:
        self.keep: list = []
        self.freed: list[int] = []
        self.requests: list[dict] = []
        self.repos: list[dict] = []
        self.files = 0
        self.closed = 0
        handles = {}
        info = {"library": "praukron_graph", "version": "0.1.0", "abi": abi,
                "engine": "crowbett/codegraph", "engine_commit": "0" * 40,
                "schema_version": 9} if info is None else info

        def text(value: object) -> int:
            data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
            buffer = ctypes.create_string_buffer(data)
            self.keep.append(buffer)
            return ctypes.addressof(buffer)

        def praukron_graph_info():
            return text(info)

        error = ctypes.create_string_buffer(b"cannot open the store: disk is read-only")

        def praukron_graph_last_error():
            return error.value

        def praukron_graph_open(config):
            if open_fails:
                return None
            db = Path(json.loads(config.decode("utf-8"))["db"])
            db.write_bytes(db.read_bytes() if db.is_file() else b"SQLite format 3\0")
            handles[7] = db
            return 7

        def praukron_graph_call(handle, request):
            request = json.loads(request.decode("utf-8"))
            self.requests.append(request)
            op = request["op"]
            if raise_in == op:
                raise RuntimeError("boom\nin the boundary")
            if raw and op in raw:
                return text(raw[op])
            if fail and op in fail:
                return text({"ok": False, "error": fail[op]})
            return text({"ok": True, "result": self.answer(request)})

        def praukron_graph_free(pointer):
            self.freed.append(pointer)

        def praukron_graph_close(handle):
            self.closed += 1

        for function in (praukron_graph_info, praukron_graph_last_error, praukron_graph_open,
                         praukron_graph_call, praukron_graph_free, praukron_graph_close):
            if function.__name__ not in omit:
                setattr(self, function.__name__, function)

    def answer(self, request: dict) -> object:
        op = request["op"]
        if op == "repos":
            return self.repos
        if op == "index":
            root = request["roots"][0]
            self.repos = [{"id": 1, "name": Path(root).name, "root": root, "indexed_at": 1}]
            self.files = 2
            return {"repos": 1, "files": 2, "parsed": 2, "unchanged": 0, "removed": 0}
        if op == "refresh":
            self.files = 3
            return {"repos": 1, "files": 3, "parsed": 1, "unchanged": 2, "removed": 0}
        if op == "status":
            return {"files": self.files, "symbols": 2, "edges": 1, "resolved": 1, "by_confidence": {"exact": 1}}
        if op == "graph":
            nodes = [{"id": "f:1", "kind": "file", "repo": 1, "path": "app.py", "lang": "python",
                      "start_line": 1, "end_line": 9},
                     {"id": "s:1", "kind": "function", "repo": 1, "file": "f:1", "path": "app.py",
                      "parent": None, "name": "run", "qname": "app.run", "start_line": 2,
                      "end_line": 4, "exported": True},
                     {"id": "s:2", "kind": "function", "repo": 1, "file": "f:1", "path": "app.py",
                      "parent": None, "name": "go", "qname": "app.go", "start_line": 6,
                      "end_line": 8, "exported": True}]
            edges = [{"id": 1, "kind": "call", "source": "s:2", "target": "s:1", "name": "run",
                      "qualifier": None, "path": "app.py", "line": 7, "col": 4, "confidence": "exact"}]
            return {"engine": "crowbett/codegraph", "engine_commit": "0" * 40, "repos": self.repos,
                    "nodes": nodes, "edges": edges,
                    "counts": {"files": 1, "symbols": 2, "nodes": 3, "edges": 1, "import_edges": 0,
                               "non_import_edges": 1, "resolved_non_import_edges": 1}}
        raise AssertionError(op)


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-engine-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir / "repo")
        self.chronicle = self.root / layout.AUTHORITY_DIR
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(engine.ENV_VAR, None)

    def library_file(self, data: bytes = b"\x7fELF not really a library\n") -> Path:
        path = self.dir / "libs" / "libpraukron_graph.so"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def with_fake(self, fake: FakeLibrary) -> FakeLibrary:
        os.environ[engine.ENV_VAR] = str(self.library_file())
        patch = mock.patch.object(engine, "_cdll", lambda path: fake)
        patch.start()
        self.addCleanup(patch.stop)
        return fake

    def assert_one_line_failure(self, code: int, out: str, err: str, *needles: str) -> None:
        self.assertEqual(code, 1, (out, err))
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("Traceback", err)
        for needle in needles:
            self.assertIn(needle, err)


class TestAbsence(EngineCase):
    def test_every_engine_command_says_absent_and_succeeds(self) -> None:
        before = snapshot(self.chronicle)
        for argv in (["status"], ["index"], ["index", "--full"]):
            code, out, err = run(self.root, "engine", *argv)
            self.assertEqual((code, err), (0, ""), argv)
            self.assertIn(ABSENT, out)
            self.assertIn(engine.ENV_VAR, out)
        code, out, err = run(self.root, "engine", "export")
        self.assertEqual((code, out), (0, ""))
        self.assertIn(ABSENT, err)
        code, out, _ = run(self.root, "engine", "status", "--json")
        state = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(state["present"])
        self.assertEqual(state["source"], "graph-library")
        self.assertFalse(state["index"]["present"])
        self.assertEqual(snapshot(self.chronicle), before)
        self.assertFalse((self.root / engine.INDEX_DIR).exists())

    def test_the_documented_places(self) -> None:
        self.assertEqual(engine.platform_tag("darwin", "arm64"), "macos-arm64")
        self.assertEqual(engine.platform_tag("darwin", "x86_64"), "macos-x86_64")
        self.assertEqual(engine.platform_tag("linux", "x86_64"), "linux-x86_64")
        self.assertEqual(engine.platform_tag("linux", "aarch64"), "linux-arm64")
        self.assertIsNone(engine.platform_tag("win32", "AMD64"))
        self.assertIsNone(engine.platform_tag("linux", "riscv64"))
        self.assertEqual(engine.default_path(self.root, "macos-arm64"),
                         self.root / ".praukron/lib/macos-arm64/libpraukron_graph.dylib")
        self.assertEqual(engine.default_path(self.root, "linux-x86_64"),
                         self.root / ".praukron/lib/linux-x86_64/libpraukron_graph.so")
        self.assertEqual(engine.index_path(self.root), self.root / ".praukron/engine/graph.db")

    def test_the_environment_wins_over_the_repository(self) -> None:
        tag = engine.platform_tag()
        if tag is None:
            self.skipTest("no default place on this platform")
        default = engine.default_path(self.root, tag)
        default.parent.mkdir(parents=True)
        default.write_bytes(b"x")
        where = engine.locate(self.root, environ={})
        self.assertEqual((where.path, where.source), (default, "repository"))
        other = self.library_file()
        where = engine.locate(self.root, environ={engine.ENV_VAR: str(other)})
        self.assertEqual((where.path, where.source), (other, "environment"))
        where = engine.locate(self.root, environ={engine.ENV_VAR: ""})
        self.assertEqual(where.source, "repository")
        # A name that does not exist is an error, not a reason to look elsewhere.
        where = engine.locate(self.root, environ={engine.ENV_VAR: str(self.dir / "nowhere.so")})
        self.assertFalse(where.present)
        self.assertIn("is not a file", where.missing)


class TestUnusableLibrary(EngineCase):
    def test_a_file_that_is_not_a_library(self) -> None:
        before = snapshot(self.chronicle)
        os.environ[engine.ENV_VAR] = str(self.library_file())
        for argv in (["status"], ["index"], ["export"]):
            code, out, err = run(self.root, "engine", *argv)
            self.assert_one_line_failure(code, out, err, "cannot load")
        self.assertEqual(snapshot(self.chronicle), before)

    def test_a_file_that_is_not_a_library_in_the_repository(self) -> None:
        default = engine.default_path(self.root)
        if default is None:
            self.skipTest("no default place on this platform")
        default.parent.mkdir(parents=True)
        default.write_bytes(b"not a library")
        code, out, err = run(self.root, "engine", "status")
        self.assert_one_line_failure(code, out, err, "cannot load", str(default))

    def test_the_environment_names_nothing(self) -> None:
        os.environ[engine.ENV_VAR] = str(self.dir / "missing.dylib")
        code, out, err = run(self.root, "engine", "index")
        self.assert_one_line_failure(code, out, err, "is not a file")

    def test_another_interface_version(self) -> None:
        before = snapshot(self.chronicle)
        fake = self.with_fake(FakeLibrary(abi=2))
        for argv in (["status"], ["index"], ["export"]):
            code, out, err = run(self.root, "engine", *argv)
            self.assert_one_line_failure(code, out, err, "interface version 2", "needs version 1")
        self.assertEqual(fake.requests, [])
        self.assertEqual(snapshot(self.chronicle), before)

    def test_no_interface_version_or_malformed_info(self) -> None:
        self.with_fake(FakeLibrary(info=["not", "an", "object"]))
        code, out, err = run(self.root, "engine", "status")
        self.assert_one_line_failure(code, out, err, "did not report an interface version")

    def test_a_missing_symbol(self) -> None:
        self.with_fake(FakeLibrary(omit=("praukron_graph_call",)))
        code, out, err = run(self.root, "engine", "status")
        self.assert_one_line_failure(code, out, err, "not a Praukron graph library", "praukron_graph_call")

    def test_open_fails_with_the_library_reason(self) -> None:
        self.with_fake(FakeLibrary(open_fails=True))
        code, out, err = run(self.root, "engine", "index")
        self.assert_one_line_failure(code, out, err, "could not open", "disk is read-only")

    def test_errors_and_panics_inside_a_call(self) -> None:
        before = snapshot(self.chronicle)
        for fake, needle in ((FakeLibrary(fail={"index": "index root does not exist"}), "index root does not exist"),
                             (FakeLibrary(fail={"repos": "panic"}), "failed in `repos`: panic"),
                             (FakeLibrary(raw={"repos": b"{not json"}), "malformed JSON"),
                             (FakeLibrary(raw={"repos": b"[1, 2]"}), "not a response"),
                             (FakeLibrary(raise_in="repos"), "unexpected failure: boom in the boundary")):
            with self.subTest(needle=needle):
                with mock.patch.object(engine, "_cdll", lambda path, fake=fake: fake):
                    os.environ[engine.ENV_VAR] = str(self.library_file())
                    code, out, err = run(self.root, "engine", "index")
                self.assert_one_line_failure(code, out, err, needle)
                self.assertEqual(fake.closed, 1, "the store is closed after a failure")
        self.assertEqual(snapshot(self.chronicle), before)

    def test_every_returned_text_is_freed(self) -> None:
        fake = self.with_fake(FakeLibrary(fail={"status": "nope"}))
        run(self.root, "engine", "index")
        self.assertEqual(sorted(fake.freed), sorted(ctypes.addressof(b) for b in fake.keep))


class TestSimulatedLibrary(EngineCase):
    def test_index_then_refresh_then_export(self) -> None:
        before = snapshot(self.chronicle)
        fake = self.with_fake(FakeLibrary())
        code, out, err = run(self.root, "engine", "index")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(f"Indexed {self.root}: 2 files"), out)
        self.assertIn(".praukron/engine/graph.db", out)
        self.assertEqual(fake.requests[1], {"op": "index", "roots": [str(self.root)], "full": True})
        self.assertTrue(engine.index_path(self.root).is_file())
        self.assertEqual((self.root / engine.INDEX_DIR / ".gitignore").read_bytes(), b"*\n")

        code, out, err = run(self.root, "engine", "index", "--json")
        result = json.loads(out)
        self.assertEqual((code, result["action"]), (0, "refreshed"))
        self.assertEqual(fake.requests[-2], {"op": "refresh", "root": str(self.root), "name": "repo"})
        self.assertEqual(result["status"]["files"], 3)

        run(self.root, "engine", "index", "--full")
        self.assertEqual(fake.requests[-2], {"op": "index", "roots": [str(self.root)], "full": True})

        code, out, err = run(self.root, "engine", "export")
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)
        self.assertEqual(document["source"], "graph-library")
        self.assertEqual(document["library"]["abi"], 1)
        self.assertEqual(document["counts"]["nodes"], len(document["nodes"]))
        self.assertEqual(document["counts"]["edges"], len(document["edges"]))
        self.assertEqual(document["edges"][0]["line"], 7)

        target = self.dir / "out" / "graph.json"
        code, out, err = run(self.root, "engine", "export", "-o", str(target))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), f"Wrote 3 nodes and 1 edges to {target}")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), document)
        self.assertEqual(snapshot(self.chronicle), before)
        self.assertEqual(fake.closed, 5)

    def test_export_needs_an_index_and_never_writes_into_the_chronicle(self) -> None:
        self.with_fake(FakeLibrary())
        code, out, err = run(self.root, "engine", "export")
        self.assert_one_line_failure(code, out, err, "no index", "praukron engine index")
        self.assertFalse(engine.index_path(self.root).exists())
        run(self.root, "engine", "index")
        before = snapshot(self.chronicle)
        code, out, err = run(self.root, "engine", "export", "-o", str(self.chronicle / "graph.json"))
        self.assert_one_line_failure(code, out, err, "refusing to write")
        self.assertEqual(snapshot(self.chronicle), before)

    def test_status_reports_versions_and_the_index(self) -> None:
        self.with_fake(FakeLibrary())
        code, out, err = run(self.root, "engine", "status")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("interface 1", out)
        self.assertIn("0" * 40, out)
        self.assertIn("none yet", out)
        run(self.root, "engine", "index")
        state = json.loads(run(self.root, "engine", "status", "--json")[1])
        self.assertTrue(state["present"])
        self.assertEqual(state["from"], "environment")
        self.assertEqual(state["library"]["engine_commit"], "0" * 40)
        self.assertEqual(state["index"], {"path": ".praukron/engine/graph.db", "present": True,
                                          "status": {"files": 2, "symbols": 2, "edges": 1, "resolved": 1,
                                                     "by_confidence": {"exact": 1}}})

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_the_index_is_ignored_by_git(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.with_fake(FakeLibrary())
        self.assertEqual(run(self.root, "engine", "index")[0], 0)
        for name in (".praukron/engine/graph.db", ".praukron/engine/.gitignore"):
            ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", name])
            self.assertEqual(ignored.returncode, 0, name)
        listed = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn(".praukron/engine", listed)


class TestExistingCommandsUnchanged(unittest.TestCase):
    """AC-T-GRAPH-LOAD-01-01: the 4.0.0 commands do not notice the library."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-engine-cmp-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def outputs(self, src: Path, prepare=None, environ: dict | None = None) -> dict:
        root = self.dir / "repo"
        shutil.rmtree(root, ignore_errors=True)
        build_fixture(root)
        if prepare:
            prepare(root)
        env = {k: v for k, v in os.environ.items() if k != engine.ENV_VAR}
        env.update(environ or {})
        argvs = [["-C", str(root), *argv] for argv in EXISTING]
        done = subprocess.run([sys.executable, "-I", "-c", WRAPPER, str(src), json.dumps(argvs)],
                              capture_output=True, text=True, env=env, check=True)
        return json.loads(done.stdout)

    def test_the_command_set_only_grows_by_engine(self) -> None:
        action = next(a for a in cli.build_parser()._actions if a.dest == "command")
        # `why` (T-GRAPH-WHY-01) reads the chronicle alone and never the library.
        self.assertEqual(set(action.choices) - COMMANDS_4_0_0, {"engine", "why"})
        self.assertEqual(COMMANDS_4_0_0 - set(action.choices), set())

    def test_output_and_exit_status_do_not_depend_on_a_library(self) -> None:
        def junk_in_the_repository(root: Path) -> None:
            tag = engine.platform_tag() or "linux-x86_64"
            library = engine.default_path(root, tag)
            library.parent.mkdir(parents=True)
            library.write_bytes(b"not a library")
            (root / engine.INDEX_DIR).mkdir(parents=True)
            (root / engine.INDEX_DIR / engine.INDEX_FILE).write_bytes(b"not a store")

        junk = self.dir / "libpraukron_graph.so"
        junk.write_bytes(b"not a library")
        absent = self.outputs(SRC)
        self.assertFalse(absent["loaded"], "a 4.0.0 command imported the engine module")
        for label, other in (("environment", self.outputs(SRC, environ={engine.ENV_VAR: str(junk)})),
                             ("repository", self.outputs(SRC, prepare=junk_in_the_repository))):
            with self.subTest(label):
                self.assertFalse(other["loaded"])
                self.assertEqual(other["results"], absent["results"])
        for result in absent["results"]:
            self.assertNotIn("Traceback", result["err"], result["argv"])

    def test_output_and_exit_status_are_those_of_4_0_0(self) -> None:
        """Runs the 4.0.0 runtime from its tag beside this one, where Git can reach the tag."""
        if not (shutil.which("git") and (ROOT / ".git").exists()):
            self.skipTest("not a Git checkout")
        tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", "v4.0.0^{commit}"],
                             capture_output=True, text=True)
        if tag.returncode != 0:
            self.skipTest("tag v4.0.0 is not fetched")
        old = self.dir / "v4.0.0"
        old.mkdir()
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "v4.0.0", "src", "VERSION"],
                                 capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(old)], input=archive, check=True)
        current = self.outputs(SRC)
        previous = self.outputs(old / "src")

        def same(results: list) -> list:
            # Only the version string may differ between the two runtimes.
            version = layout.version()
            return [{**r, "out": r["out"].replace(version, "<v>").replace("4.0.0", "<v>"),
                     "err": r["err"].replace(version, "<v>").replace("4.0.0", "<v>")} for r in results]

        self.assertEqual(same(current["results"]), same(previous["results"]))


class TestStandardLibraryOnlyAndNeverAuthority(unittest.TestCase):
    """AC-T-GRAPH-LOAD-01-05, as an inspection the suite repeats."""

    PACKAGE = SRC / "praukron"
    STANDARD = {"__future__", "ctypes", "dataclasses", "json", "os", "pathlib", "platform", "sys", "typing"}

    def imports(self, tree: ast.AST) -> list[tuple[ast.AST, str]]:
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [(node, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    found += [(node, "." + (node.module or alias.name)) for alias in node.names] \
                        if node.module is None else [(node, "." + node.module)]
                else:
                    found.append((node, node.module or ""))
        return found

    def test_the_runtime_module_uses_the_standard_library_only(self) -> None:
        tree = ast.parse((self.PACKAGE / "engine.py").read_text(encoding="utf-8"))
        names = {name for _, name in self.imports(tree)}
        self.assertEqual({n for n in names if not n.startswith(".")} - self.STANDARD, set())
        self.assertEqual({n for n in names if n.startswith(".")}, {".layout"})

    def test_only_the_engine_command_imports_it(self) -> None:
        for path in sorted(self.PACKAGE.glob("*.py")):
            if path.name == "engine.py":
                continue
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            hits = [node for node, name in self.imports(tree) if name in (".engine", "praukron.engine")]
            if path.name != "cli.py":
                self.assertEqual(hits, [], path.name)
                self.assertNotIn("engine/graph.db", text, path.name)
                continue
            handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_engine")
            inside = {id(n) for n in ast.walk(handler)}
            self.assertTrue(hits)
            self.assertTrue(all(id(n) in inside for n in hits), "cli imports engine outside cmd_engine")

    def test_validate_compile_context_and_retrieve_ignore_the_index(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="praukron-engine-auth-")).resolve()
        self.addCleanup(shutil.rmtree, directory, True)
        root = build_fixture(directory / "repo")
        argvs = (["validate"], ["compile"], ["context", "T-ONE"], ["retrieve", "T-ONE"])
        run(root, "compile")
        before = [run(root, *argv) for argv in argvs]
        index = root / engine.INDEX_DIR
        index.mkdir(parents=True)
        (index / engine.INDEX_FILE).write_bytes(b"T-ONE ADR-001 not a store")
        (index / ".gitignore").write_bytes(b"*\n")
        self.assertEqual([run(root, *argv) for argv in argvs], before)


if __name__ == "__main__":
    unittest.main()
