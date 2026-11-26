"""Look up code from a task and tasks from code (SPEC §2.9, AC-T-CIL-MAP-03)."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, layout, refs  # noqa: E402
from test_code_references import anchored, reference  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402

ASSUMPTIONS = """# Assumptions

## A-1: The ledger keeps one currency
- Status: OPEN
- Tasks: T-ONE
- Impact: LOW
- Recorded: 2026-01-02 · agent/builder
- Assumption: Every entry is in one currency.
- Basis: Nothing in the data names a second one.
- Applied in: T-ONE
- Responses: none
- Reconciled by: none
"""

CODE = "# Code references\n\n" + "\n".join([
    reference("CR-T-ONE-01", "T-ONE", "src/app/core.py", Revision="286b9a7",
              Evidence="tests/test_core.py covers it"),
    reference("CR-T-ONE-02", "T-ONE", "src/app/core.py", Symbol="Core.run", Kind="method",
              Relation="creates", Scope="observed", Source="git_diff", Verification="verified"),
    reference("CR-T-ONE-03", "T-ONE", "src/app/util.py", Symbol="Core.run", Kind="function",
              Relation="reads", Authorization="not_applicable"),
    reference("CR-T-TWO-01", "T-TWO", "src/app/core.py", Relation="reads",
              Authorization="not_applicable"),
])


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


def many(task: str, count: int) -> str:
    """`count` references from `task`, to files and symbols, half planned and half observed."""
    return "# Code references\n\n" + "\n".join(
        reference(f"CR-{task}-{n:02d}", task,
                  f"src/pkg/module_{n:03d}.py",
                  **({"Symbol": f"Module{n}.run", "Kind": "method"} if n % 2 else {}),
                  Scope="observed" if n % 2 else "planned", Revision="286b9a7")
        for n in range(1, count + 1))


class LookupCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        authority = self.root / layout.AUTHORITY_DIR
        (authority / "CODE.md").write_text(CODE, encoding="utf-8")
        (authority / "ASSUMPTIONS.md").write_text(ASSUMPTIONS, encoding="utf-8")
        anchored(self.root)

    def json(self, *argv: str) -> dict:
        code, out, err = run(self.root, *argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TestTaskPacket(LookupCase):
    """AC-T-CIL-MAP-03-01: the packet and `retrieve` list the task's references."""

    def check_entries(self, entries: list[dict]) -> None:
        by_id = {e["id"]: e for e in entries}
        first = by_id["CR-T-ONE-01"]
        # An absent key means not recorded: a file reference carries no `symbol`.
        self.assertEqual(
            first,
            {"id": "CR-T-ONE-01", "path": "src/app/core.py", "relation": "modifies", "scope": "planned",
             "verification": "unverified", "authorization": "proposed", "revision": "286b9a7",
             "evidence": "tests/test_core.py covers it", "authority": "CODE.md#CR-T-ONE-01"})
        self.assertTrue(all(None not in e.values() for e in entries))
        anchor = by_id["T-ONE#Files:docs/guide.md"]
        self.assertEqual(anchor, {"id": "T-ONE#Files:docs/guide.md", "path": "docs/guide.md",
                                  "scope": "planned", "verification": "unverified",
                                  "authority": "TASKS.md#T-ONE"})
        symbol = by_id["CR-T-ONE-02"]
        self.assertEqual((symbol["path"], symbol["symbol"], symbol["relation"], symbol["scope"],
                          symbol["verification"]),
                         ("src/app/core.py", "Core.run", "creates", "observed", "verified"))
        # The task's `Files:` and `Symbols:` anchors are planned, manual references.
        self.assertEqual(by_id["T-ONE#Files:docs/guide.md"]["authority"], "TASKS.md#T-ONE")
        self.assertEqual(by_id["T-ONE#Symbols:helper"]["symbol"], "helper")

    def test_context_packet_lists_files_and_symbols(self):
        packet = self.json("context", "T-ONE")
        listed = packet["references"]
        self.assertEqual(listed["total"], 7)
        self.assertEqual(listed["counts"], {"planned": 6, "observed": 1})
        self.assertIsNone(listed["rest"])
        self.check_entries(listed["first"])
        self.assertNotIn("CR-T-TWO-01", {e["id"] for e in listed["first"]})

    def test_retrieve_json_and_text_list_them(self):
        pack = self.json("retrieve", "T-ONE", "--json")
        [listed] = pack["references"]
        self.assertEqual(listed["task"], "T-ONE")
        self.check_entries(listed["first"])
        code, text, _ = run(self.root, "retrieve", "T-ONE")
        self.assertEqual(code, 0)
        self.assertIn("## REFERENCES · T-ONE", text)
        self.assertIn("- src/app/core.py#Core.run · creates · observed · verified · proposed — CODE.md#CR-T-ONE-02",
                      text)
        self.assertIn("revision 286b9a7 · evidence: tests/test_core.py covers it — CODE.md#CR-T-ONE-01", text)

    def test_refs_for_a_task_returns_every_field(self):
        result = self.json("refs", "T-ONE", "--json")
        self.assertEqual(result["kind"], "task")
        self.assertEqual(result["total"], 7)
        core = next(r for r in result["references"] if r["id"] == "CR-T-ONE-01")
        self.assertEqual((core["source"], core["recorded"], core["authority"]),
                         ("agent", "2026-01-02 · agent/builder", "CODE.md#CR-T-ONE-01"))
        # `refs --json` stays complete: an unrecorded field is null, not absent.
        self.assertIn("symbol", core)
        self.assertIsNone(core["symbol"])
        code, text, _ = run(self.root, "refs", "T-ONE")
        self.assertEqual(code, 0)
        self.assertIn("7 references: 6 planned, 1 observed", text)

    def test_existing_packet_fields_are_unchanged(self):
        from praukron import analytics, compile as compiler

        project = compiler.load(self.root)
        packet = analytics.context(project, "T-ONE")
        with mock.patch("praukron.refs.summary", return_value=None):
            without = analytics.context(project, "T-ONE")
        del packet["references"], without["references"]
        self.assertEqual(packet, without)

    def test_a_task_without_references_says_so(self):
        listed = self.json("context", "T-THREE")["references"]
        self.assertEqual((listed["total"], listed["first"], listed["rest"]), (0, [], None))
        _, text, _ = run(self.root, "retrieve", "T-THREE")
        self.assertIn("No file or symbol is referenced.", text)


class TestCodeToTasks(LookupCase):
    """AC-T-CIL-MAP-03-02: a path or symbol names its tasks and their records."""

    def test_a_file_names_every_task_and_their_records(self):
        result = self.json("refs", "src/app/core.py", "--json")
        self.assertEqual(result["kind"], "file")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE", "T-TWO"])
        # The file's own references, its symbols' references, and the anchors that name it.
        self.assertEqual({r["id"] for r in result["references"]},
                         {"CR-T-ONE-01", "CR-T-ONE-02", "CR-T-TWO-01",
                          "T-ONE#Files:src/app/core.py", "T-TWO#Files:src/app/core.py"})
        one, two = result["tasks"]
        self.assertEqual(one["decisions"], [{"id": "ADR-001", "title": one["decisions"][0]["title"],
                                             "status": one["decisions"][0]["status"],
                                             "authority": "ADR/ADR-001.md"}])
        self.assertTrue(one["acceptance"])
        self.assertTrue(all(c["authority"] == "ACCEPTANCE.md#AC-T-ONE" for c in one["acceptance"]))
        self.assertEqual([c["id"] for c in one["acceptance"]][:1], ["AC-T-ONE-01"])
        self.assertEqual(one["assumptions"], [{"id": "A-1", "title": "The ledger keeps one currency",
                                               "status": "OPEN", "impact": "LOW",
                                               "authority": "ASSUMPTIONS.md#A-1"}])
        self.assertEqual(two["assumptions"], [])
        self.assertEqual(two["authority"], "TASKS.md#T-TWO")
        self.assertTrue(two["decisions"] and two["acceptance"])

    def test_a_symbol_is_identified_by_its_path(self):
        result = self.json("refs", "src/app/core.py#Core.run", "--json")
        self.assertEqual(result["kind"], "symbol")
        self.assertEqual([r["id"] for r in result["references"]], ["CR-T-ONE-02"])
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        # The same name in another file is another artifact.
        other = self.json("refs", "src/app/util.py#Core.run", "--json")
        self.assertEqual([r["id"] for r in other["references"]], ["CR-T-ONE-03"])

    def test_text_names_tasks_decisions_criteria_and_assumptions(self):
        code, text, _ = run(self.root, "refs", "src/app/core.py")
        self.assertEqual(code, 0)
        self.assertIn("referenced by 2 tasks", text)
        self.assertIn("decision ADR-001", text)
        # The text names each criterion by id and state; `--json` keeps its text.
        criterion = self.json("refs", "src/app/core.py", "--json")["tasks"][0]["acceptance"][0]
        self.assertIn(f"criterion AC-T-ONE-01 [{criterion['state']}] — ACCEPTANCE.md#AC-T-ONE", text)
        self.assertTrue(criterion["text"])
        self.assertNotIn(criterion["text"], text)
        self.assertIn("assumption A-1 [OPEN] The ledger keeps one currency — ASSUMPTIONS.md#A-1", text)

    def test_an_unknown_task_is_refused(self):
        code, out, err = run(self.root, "refs", "T-NOPE")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("unknown-task: T-NOPE: no such task", err)

    def test_an_unreferenced_path_has_no_tasks(self):
        result = self.json("refs", "src/nowhere.py", "--json")
        self.assertEqual((result["total"], result["tasks"]), (0, []))

    def test_a_path_outside_the_repository_is_refused(self):
        for target, message in (("../secrets.py", "the path leaves the repository root"),
                                ("/etc/passwd", "the path is absolute"),
                                ("C:/x.py", "the path names a drive"),
                                ("src\\x.py", "the path contains a backslash; use '/'")):
            code, out, err = run(self.root, "refs", target, "--json")
            self.assertEqual((code, out), (1, ""))
            self.assertIn(f"invalid-reference-path: {target}: {message}", err)


def _provider(directory: Path, name: str, log: Path) -> None:
    script = directory / name
    script.write_text(f"#!/bin/sh\necho \"$0 $*\" >> '{log}'\necho provider-output\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


class TestNoProvider(LookupCase):
    """AC-T-CIL-MAP-03-03: the lookups are the same with and without providers."""

    def outputs(self) -> list[str]:
        found = []
        for argv in (("refs", "T-ONE", "--json"), ("refs", "T-ONE"),
                     ("refs", "src/app/core.py", "--json"), ("refs", "src/app/core.py#Core.run"),
                     ("context", "T-ONE"), ("retrieve", "T-ONE", "--json"), ("retrieve", "T-ONE")):
            code, out, err = run(self.root, *argv)
            self.assertEqual(code, 0, err)
            found.append(out)
        return found

    def test_answers_do_not_depend_on_a_graph_provider(self):
        bin_dir = Path(tempfile.mkdtemp(prefix="praukron-providers-"))
        self.addCleanup(shutil.rmtree, bin_dir, True)
        log = bin_dir / "calls.log"
        for name in ("codegraph", "codebase-memory-mcp"):
            _provider(bin_dir, name, log)
        (self.root / ".codegraph").mkdir()
        real_run = subprocess.run
        calls: list = []

        def recording(*args, **kwargs):
            calls.append(args)
            return real_run(*args, **kwargs)

        path = os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{path}"}), \
                mock.patch("subprocess.run", recording):
            self.assertEqual(shutil.which("codegraph"), str(bin_dir / "codegraph"))
            present = self.outputs()
        empty = Path(tempfile.mkdtemp(prefix="praukron-no-providers-"))
        self.addCleanup(shutil.rmtree, empty, True)
        shutil.rmtree(self.root / ".codegraph")
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertIsNone(shutil.which("codegraph"))
            self.assertIsNone(shutil.which("codebase-memory-mcp"))
            absent = self.outputs()
        self.assertEqual(present, absent)
        self.assertEqual(calls, [])
        self.assertFalse(log.exists())
        self.assertIn("CR-T-ONE-02", absent[0])


class TestHundredReferences(unittest.TestCase):
    """AC-T-CIL-MAP-03-04: one hundred references stay inside T-PERF-OPT-01's bound.

    T-PERF-OPT-01 bounds what an agent receives as the chronicle grows tenfold
    (10k to 100k lines, AC-T-PERF-OPT-01-01): no output may grow past 1.25 times.
    The same bound is applied here to a tenfold growth in one task's
    references, on the synthetic chronicle `tests/bench_scale.py` measures.
    """

    BOUND = 1.25

    @classmethod
    def setUpClass(cls):
        import bench_scale

        cls.dir = Path(tempfile.mkdtemp(prefix="praukron-refs-scale-"))
        cls.root = cls.dir / "chronicle"
        cls.shape = bench_scale.generate(cls.root, 300)
        cls.code = cls.root / layout.AUTHORITY_DIR / "CODE.md"
        cls.template = cls.code.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    def sizes(self, count: int) -> tuple[int, int, dict]:
        task = self.shape["task"]
        self.code.write_text(many(task, count) if count else self.template, encoding="utf-8")
        code, packet, err = run(self.root, "context", task)
        self.assertEqual(code, 0, err)
        code, pack, err = run(self.root, "retrieve", task)
        self.assertEqual(code, 0, err)
        return len(packet.encode()), len(pack.encode()), json.loads(packet)

    def test_the_packet_carries_counts_the_first_references_and_the_rest(self):
        task = self.shape["task"]
        none_packet, none_pack, _ = self.sizes(0)
        ten_packet, ten_pack, _ = self.sizes(10)
        hundred_packet, hundred_pack, packet = self.sizes(100)
        listed = packet["references"]
        self.assertEqual(listed["total"], 100)
        self.assertEqual(listed["counts"], {"planned": 50, "observed": 50})
        self.assertEqual(len(listed["first"]), refs.PACKET_REFERENCES)
        self.assertEqual(listed["first"][0]["path"], "src/pkg/module_001.py")
        self.assertEqual(listed["rest"], f"praukron refs {task}")
        code, out, _ = run(self.root, "refs", task, "--json")
        self.assertEqual((code, json.loads(out)["total"]), (0, 100))
        self.assertLessEqual(hundred_packet, ten_packet * self.BOUND,
                             (none_packet, ten_packet, hundred_packet))
        self.assertLessEqual(hundred_pack, ten_pack * self.BOUND, (none_pack, ten_pack, hundred_pack))
        print(f"\n[AC-T-CIL-MAP-03-04] context <task> bytes: 0 refs {none_packet}, 10 refs {ten_packet}, "
              f"100 refs {hundred_packet}; retrieve <task> bytes: 0 refs {none_pack}, 10 refs {ten_pack}, "
              f"100 refs {hundred_pack}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
