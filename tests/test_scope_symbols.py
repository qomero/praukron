"""Changed Python symbols captured with a task's files (AC-T-CIL-SCOPE-02, ADR-092).

Every test builds its own temporary Git repository from synthetic sources;
none touches the repository the suite runs in.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import batch, scope, symbols  # noqa: E402
from test_scope_capture import GIT, ScopeCase  # noqa: E402

LEDGER = '''\
"""A synthetic ledger."""


class Ledger:
    def post(self, amount):
        return amount

    def void(self, entry):
        return None


def helper(value):
    return value * 2


async def fetch():
    def inner():
        return 1
    return inner()
'''

# post's body changes, helper goes, fresh arrives, inner changes; void and the
# rest of fetch stay as they were.
LEDGER_AFTER = '''\
"""A synthetic ledger."""


class Ledger:
    def post(self, amount):
        return amount + 1

    def void(self, entry):
        return None


async def fetch():
    def inner():
        return 2
    return inner()


def fresh():
    return "new"
'''


def defined_names(source: bytes) -> set[str]:
    """Every qualified def/class name in `source`, found independently of praukron."""
    names: set[str] = set()

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(prefix + child.name)
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return names


class SymbolCase(ScopeCase):
    def symbols(self, task: str = "T-TWO") -> dict[str, object]:
        return {f"{r.path}#{r.symbol}": r for r in self.project().references
                if r.task == task and r.symbol is not None}

    def files(self, task: str = "T-TWO") -> dict[str, object]:
        return {r.path: r for r in self.project().references if r.task == task and r.symbol is None}

    def source_at(self, reference) -> bytes:
        """The text of the reference's file at its recorded revision, or in the working tree."""
        if reference.revision:
            done = subprocess.run(["git", "-C", str(self.root), "show", f"{reference.revision}:./{reference.path}"],
                                  capture_output=True, check=True)
            return done.stdout
        return (self.root / reference.path).read_bytes()

    def absent_names(self, task: str = "T-TWO") -> list[str]:
        """The recorded symbols whose name is not in the source at their recorded revision."""
        missing = []
        for key, reference in self.symbols(task).items():
            source = self.source_at(reference)
            last = reference.symbol.rsplit(".", 1)[-1]
            textual = re.search(rb"^\s*(?:async\s+def|def|class)\s+" + re.escape(last.encode()) + rb"\b",
                                source, re.M)
            if reference.symbol not in defined_names(source) or not textual:
                missing.append(key)
        return missing


@unittest.skipIf(GIT is None, "git is not installed")
class TestChangedSymbols(SymbolCase):
    """AC-T-CIL-SCOPE-02-01."""

    def test_changed_functions_methods_and_classes_are_recorded(self):
        base = self.repository({"src/ledger.py": LEDGER, "README.md": "readme\n"})
        self.claim()
        self.write("src/ledger.py", LEDGER_AFTER)
        head = self.commit("work")
        result = self.close()
        found = self.symbols()
        expected = {
            "src/ledger.py#Ledger": ("class", "modifies", None, head),
            "src/ledger.py#Ledger.post": ("method", "modifies", None, head),
            "src/ledger.py#fetch": ("function", "modifies", None, head),
            "src/ledger.py#fetch.inner": ("function", "modifies", None, head),
            "src/ledger.py#fresh": ("function", "creates", None, head),
            # A deleted symbol is read from the base, where its name is true.
            "src/ledger.py#helper": ("function", "deletes", "deleted", base),
        }
        self.assertEqual(sorted(found), sorted(expected))
        for key, (kind, relation, lifecycle, revision) in expected.items():
            with self.subTest(symbol=key):
                reference = found[key]
                self.assertEqual((reference.kind, reference.relation, reference.lifecycle, reference.revision),
                                 (kind, relation, lifecycle, revision))
                self.assertEqual((reference.scope, reference.provenance, reference.verification,
                                  reference.authorization), ("observed", "static_analysis", "verified",
                                                              "not_applicable"))
                self.assertEqual((reference.base, reference.head), (base, head))
                self.assertTrue(reference.recorded.endswith(" · agent/builder"), reference.recorded)
                self.assertRegex(reference.lines, r"^[0-9]+-[0-9]+$")
        self.assertEqual(found["src/ledger.py#Ledger.post"].lines, "5-6")
        self.assertEqual(found["src/ledger.py#helper"].lines, "12-13")  # its range at the base
        self.assertEqual(found["src/ledger.py#fresh"].lines, "18-19")
        self.assertNotIn("src/ledger.py#Ledger.void", found)
        self.assertEqual(result["capture"]["symbols"], 6)
        self.assertEqual(result["capture"]["files"], 1)
        self.assertEqual(result["capture"]["references"], 7)
        self.assertEqual(list(self.files()), ["src/ledger.py"])

    def test_added_and_deleted_files_create_and_delete_every_symbol(self):
        base = self.repository({"src/gone.py": "class Old:\n    def run(self):\n        pass\n", "x.txt": "x\n"})
        self.claim()
        (self.root / "src/gone.py").unlink()
        self.write("src/made.py", "def one():\n    pass\n\n\nclass Two:\n    async def go(self):\n        pass\n")
        self.close()
        found = self.symbols()
        self.assertEqual({k: (r.kind, r.relation, r.revision) for k, r in found.items()}, {
            "src/gone.py#Old": ("class", "deletes", base),
            "src/gone.py#Old.run": ("method", "deletes", base),
            # Only in the working tree: no revision, as for its file.
            "src/made.py#one": ("function", "creates", None),
            "src/made.py#Two": ("class", "creates", None),
            "src/made.py#Two.go": ("method", "creates", None),
        })
        self.assertIn("the working tree", found["src/made.py#one"].evidence)

    def test_a_deleted_symbol_of_a_renamed_file_names_its_old_path(self):
        self.repository({"src/a.py": "def keep():\n    return 1\n\n\ndef drop():\n    return 2\n" + "# pad\n" * 8})
        self.claim()
        self.git("mv", "src/a.py", "src/b.py")
        self.write("src/b.py", "def keep():\n    return 1\n" + "# pad\n" * 8)
        self.commit("rename")
        self.close()
        found = self.symbols()
        self.assertEqual(sorted(found), ["src/a.py#drop"])
        self.assertEqual(self.files()["src/b.py"].was, "src/a.py")

    def test_scope_capture_reads_the_head_revision_not_the_working_tree(self):
        base = self.repository({"src/m.py": "def a():\n    return 1\n"})
        self.write("src/m.py", "def a():\n    return 2\n")
        head = self.commit("work")
        self.write("src/m.py", "def a():\n    return 2\n\n\ndef later():\n    pass\n")
        result = scope.capture(self.root, "T-THREE", base, "HEAD")
        self.assertTrue(result["ok"], result)
        found = self.symbols("T-THREE")
        self.assertEqual({k: (r.relation, r.revision) for k, r in found.items()}, {"src/m.py#a": ("modifies", head)})

    def test_a_definition_repeated_in_one_scope_is_one_symbol(self):
        text = "class P:\n    @property\n    def x(self):\n        return 1\n\n    @x.setter\n    def x(self, v):\n        pass\n"
        self.repository({"src/p.py": text})
        self.claim()
        self.write("src/p.py", text.replace("pass", "self.v = v"))
        self.close()
        found = self.symbols()
        self.assertEqual(sorted(found), ["src/p.py#P", "src/p.py#P.x"])
        self.assertEqual(found["src/p.py#P.x"].lines, "2-8")

    def test_an_unchanged_symbol_beside_a_change_is_not_recorded(self):
        self.repository({"src/q.py": "X = 1\n\n\ndef stay():\n    return X\n"})
        self.claim()
        self.write("src/q.py", "X = 2\n\n\ndef stay():\n    return X\n")
        result = self.close()
        self.assertEqual(self.symbols(), {})
        self.assertEqual(result["capture"]["symbols"], 0)
        self.assertNotIn("symbolsUnresolved", result["capture"])


class TestHunks(unittest.TestCase):
    """The changed lines come from Git's hunks, read by their counts."""

    def test_hunk_bodies_are_skipped_by_count(self):
        patch = (b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                 b"@@ -3 +3,2 @@\n-x = 1\n+++ b/not-a-file.py\n+@@ -9 +9 @@\n\\ No newline at end of file\n"
                 b"@@ -10,0 +12 @@\n+y\n"
                 b'diff --git "a/sp\\303\\274 \\"q\\".py" "b/sp\\303\\274 \\"q\\".py"\n'
                 b'--- "a/sp\\303\\274 \\"q\\".py"\n+++ "b/sp\\303\\274 \\"q\\".py"\n@@ -1,2 +0,0 @@\n-a\n-b\n')
        self.assertEqual(symbols.hunks(patch), {"a.py": ([(3, 3)], [(3, 4), (12, 12)]),
                                                'sp\u00fc "q".py': ([(1, 2)], [])})

    def test_a_symbol_is_touched_only_by_a_range_that_overlaps_it(self):
        ranges = [(3, 4), (12, 12), (40, 60)]
        cases = {(1, 2): False, (1, 3): True, (4, 11): True, (5, 11): False, (12, 12): True,
                 (13, 39): False, (45, 46): True, (61, 90): False}
        for (start, end), touched in cases.items():
            with self.subTest(start=start, end=end):
                self.assertEqual(symbols._touches(symbols.Symbol("s", "function", start, end), ranges), touched)


@unittest.skipIf(GIT is None, "git is not installed")
class TestWriteTime(SymbolCase):
    """A one-line change costs the change, not the file (review of PR #85)."""

    BOUND = 3.0  # seconds for the whole close; difflib took 17 s for the same change
    MANY_BOUND = 6.0  # seconds for forty 200 KiB files, the budget admitting ten

    def test_a_one_line_change_in_a_long_file_closes_within_the_bound(self):
        text = "".join(f"def f{i}(a):\n    return a + 1\n\n\n" for i in range(6000))
        self.repository({"src/long.py": text})
        self.claim()
        self.write("src/long.py", text.replace("def f3000(a):\n    return a + 1", "def f3000(a):\n    return a + 2"))
        start = time.perf_counter()
        result = self.close()
        elapsed = time.perf_counter() - start
        self.assertEqual(sorted(self.symbols()), ["src/long.py#f3000"])
        self.assertEqual(result["capture"]["symbols"], 1)
        self.assertLess(elapsed, self.BOUND, f"close took {elapsed:.2f} s")

    def test_many_mid_sized_files_close_within_the_bound(self):
        # Forty files of about 200 KiB, each changed in one line: 16 MiB to read in all,
        # of which the 4 MiB budget admits ten; the rest keep their file references.
        def text(k: int, value: int) -> str:
            return "".join(f"def f{k}_{i}(a):\n    return a + {value if i == 9 else 1}\n\n\n" for i in range(6000))
        files = {f"src/m{k:02d}.py": text(k, 1) for k in range(40)}
        self.repository(files)
        self.claim()
        for k in range(40):
            self.write(f"src/m{k:02d}.py", text(k, 2))
        start = time.perf_counter()
        result = self.close()
        elapsed = time.perf_counter() - start
        admitted = sorted(files)[:symbols.MAX_CAPTURE_BYTES // (2 * len(text(0, 1)))]
        self.assertEqual(sorted(self.symbols()), sorted(f"{p}#f{p[5:7].lstrip('0') or '0'}_9" for p in admitted))
        self.assertEqual(len(self.files()), 40)
        self.assertEqual(len(result["capture"]["symbolsUnresolved"]), 40 - len(admitted))
        self.assertLess(elapsed, self.MANY_BOUND, f"close took {elapsed:.2f} s")


@unittest.skipIf(GIT is None, "git is not installed")
class TestUnresolvedSymbols(SymbolCase):
    """AC-T-CIL-SCOPE-02-02."""

    def test_a_file_with_no_extractor_keeps_its_reference_and_is_unresolved(self):
        self.repository({"web/app.js": "function a() { return 1 }\n"})
        self.claim()
        self.write("web/app.js", "function a() { return 2 }\n")
        result = self.close()
        self.assertEqual(self.symbols(), {})
        reference = self.files()["web/app.js"]
        self.assertEqual(reference.relation, "modifies")
        self.assertIn("symbols unresolved: no deterministic symbol extractor", reference.evidence)
        self.assertEqual(result["capture"]["symbolsUnresolved"], [
            {"path": "web/app.js", "reason": symbols.NO_EXTRACTOR}])

    def test_a_python_file_that_does_not_parse_is_unresolved(self):
        self.repository({"src/ok.py": "def f():\n    return 1\n"})
        self.claim()
        self.write("src/ok.py", "def f(:\n    return 1\n")
        result = self.close()
        self.assertEqual(self.symbols(), {})
        self.assertIn("symbols unresolved: does not parse", self.files()["src/ok.py"].evidence)
        [entry] = result["capture"]["symbolsUnresolved"]
        self.assertEqual(entry["path"], "src/ok.py")
        self.assertTrue(entry["reason"].endswith("at the head"), entry)

    def test_a_python_file_that_did_not_parse_at_the_base_is_unresolved(self):
        self.repository({"src/ok.py": "def f(:\n"})
        self.claim()
        self.write("src/ok.py", "def f():\n    return 1\n")
        result = self.close()
        self.assertEqual(self.symbols(), {})
        self.assertTrue(result["capture"]["symbolsUnresolved"][0]["reason"].endswith("at the base revision"))

    def test_a_file_over_the_size_limit_is_not_parsed(self):
        self.repository({"src/big.py": "def f():\n    return 1\n" + "#" * 200 + "\n"})
        self.claim()
        self.write("src/big.py", "def f():\n    return 2\n" + "#" * 200 + "\n")
        with mock.patch.object(symbols, "MAX_FILE_BYTES", 100), \
                mock.patch.object(symbols, "extract", side_effect=AssertionError("parsed")):
            result = self.close()
        self.assertEqual(self.symbols(), {})
        self.assertIn("larger than 100 bytes", result["capture"]["symbolsUnresolved"][0]["reason"])
        self.assertIn("symbols unresolved: file larger than 100 bytes", self.files()["src/big.py"].evidence)

    def budget_case(self):
        """Three changed files and a tiny fourth, and the bytes each costs to read on both sides."""
        old = {name: f"def {name[4]}():\n    return 1\n" + "#" * 50 * (i + 1) + "\n"
               for i, name in enumerate(["src/a.py", "src/b.py", "src/c.py"])}
        old["src/d.py"] = "def d():\n    return 1\n"
        self.repository(old)
        self.claim()
        new = {name: text.replace("return 1", "return 2") for name, text in old.items()}
        for name, text in new.items():
            self.write(name, text)
        return {name: len(old[name].encode()) + len(new[name].encode()) for name in sorted(old)}

    def test_a_capture_just_under_the_budget_extracts_every_file(self):
        cost = self.budget_case()
        with mock.patch.object(symbols, "MAX_CAPTURE_BYTES", sum(cost.values())):
            result = self.close()
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a", "src/b.py#b", "src/c.py#c", "src/d.py#d"])
        self.assertNotIn("symbolsUnresolved", result["capture"])

    def test_a_capture_over_the_budget_extracts_the_files_that_fit(self):
        cost = self.budget_case()
        read = []
        real = symbols.read_blobs

        def recording(git, root, specs):
            read.extend(specs)
            return real(git, root, specs)

        # a and b fit; c would pass the budget, so c and the smaller d after it are not read.
        budget = cost["src/a.py"] + cost["src/b.py"] + cost["src/c.py"] - 1
        with mock.patch.object(symbols, "MAX_CAPTURE_BYTES", budget), \
                mock.patch.object(symbols, "read_blobs", side_effect=recording):
            result = self.close()
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a", "src/b.py#b"])
        self.assertEqual(sorted(self.files()), ["src/a.py", "src/b.py", "src/c.py", "src/d.py"])
        reason = f"the capture's symbol budget of {budget} bytes of Python source is spent"
        self.assertEqual(result["capture"]["symbolsUnresolved"],
                         [{"path": "src/c.py", "reason": reason}, {"path": "src/d.py", "reason": reason}])
        self.assertIn(f"symbols unresolved: {reason}", self.files()["src/c.py"].evidence)
        self.assertFalse([s for s in read if s.endswith(("c.py", "d.py"))], read)

    def test_a_capture_over_the_file_limit_extracts_no_symbol(self):
        self.repository({"src/a.py": "def a():\n    pass\n", "src/b.py": "def b():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        self.write("src/b.py", "def b():\n    return 1\n")
        with mock.patch.object(symbols, "MAX_FILES", 1), \
                mock.patch.object(symbols, "read_blobs", side_effect=AssertionError("read")):
            result = self.close()
        self.assertEqual(self.symbols(), {})
        self.assertEqual(sorted(self.files()), ["src/a.py", "src/b.py"])
        self.assertEqual([e["path"] for e in result["capture"]["symbolsUnresolved"]], ["src/a.py", "src/b.py"])
        self.assertIn("more than 1 changed Python files", result["capture"]["symbolsUnresolved"][0]["reason"])

    def test_a_failed_extraction_keeps_the_file_references(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        with mock.patch.object(symbols, "read_blobs", side_effect=RuntimeError("boom")):
            result = self.close()
        self.assertEqual(self.task().status, "DONE")
        self.assertEqual(list(self.files()), ["src/a.py"])
        self.assertEqual(self.symbols(), {})
        self.assertEqual(result["capture"]["symbolsUnresolved"],
                         [{"path": "src/a.py", "reason": "symbol extraction failed: boom"}])

    def test_source_is_parsed_never_run(self):
        self.repository({"src/a.py": "x = 1\n"})
        marker = self.root / "ran"
        self.claim()
        self.write("src/a.py", f"open({str(marker)!r}, 'w').close()\n\ndef a():\n    pass\n")
        self.close()
        self.assertFalse(marker.exists())
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a"])


@unittest.skipIf(GIT is None, "git is not installed")
class TestNamesExistInSource(SymbolCase):
    """AC-T-CIL-SCOPE-02-03."""

    def test_every_recorded_symbol_exists_at_its_recorded_revision(self):
        self.repository({"src/ledger.py": LEDGER, "src/gone.py": "def old():\n    pass\n",
                         "src/r.py": "class R:\n    def m(self):\n        pass\n" + "# r\n" * 6})
        self.claim()
        self.write("src/ledger.py", LEDGER_AFTER)
        self.git("mv", "src/r.py", "src/s.py")
        self.write("src/s.py", "class R:\n    def n(self):\n        pass\n" + "# r\n" * 6)
        self.commit("committed half")
        (self.root / "src/gone.py").unlink()
        self.write("src/wt.py", "class W:\n    class Inner:\n        def deep(self):\n            pass\n")
        self.close()
        recorded = self.symbols()
        self.assertGreaterEqual(len(recorded), 12, sorted(recorded))
        self.assertEqual(self.absent_names(), [])

    def test_the_check_fails_on_a_name_absent_from_the_source(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        invented = symbols.Changed("invented", "function", "modifies", 1, 2)
        with mock.patch.object(symbols, "changed", return_value=[invented]):
            self.close()
        self.assertEqual(self.absent_names(), ["src/a.py#invented"])


@unittest.skipIf(GIT is None, "git is not installed")
class TestCapturedAtDone(SymbolCase):
    """AC-T-CIL-SCOPE-02-04."""

    def test_done_records_symbols_in_the_same_write(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        writes = []
        real = scope.mutate.transaction

        def counting(*args, **kwargs):
            writes.append(args[1])
            return real(*args, **kwargs)

        with mock.patch.object(scope.mutate, "transaction", side_effect=counting), \
                mock.patch("praukron.task_update.mutate.transaction", side_effect=counting):
            result = self.close()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(result["capture"]["symbols"], 1)
        self.assertEqual(self.task().status, "DONE")
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a"])

    def test_done_inside_batch_records_symbols(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": self.revision(),
             "set": {"status": "DONE", "evidence": "tests pass"}},
            {"op": "journal append", "title": "Closed T-TWO", "body": "Done."}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["capture"]["symbols"], 1)
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a"])

    def test_a_refused_batch_writes_no_symbol(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": self.revision(),
             "set": {"status": "DONE", "evidence": "tests pass"}},
            {"op": "task update", "id": "T-NOPE", "expectRevision": "0" * 16, "set": {"status": "WIP"}}]})
        self.assertFalse(result["ok"])
        self.assertFalse(self.code.exists())

    def test_a_second_capture_adds_only_new_symbols(self):
        self.repository({"src/a.py": "def a():\n    pass\n\n\ndef b():\n    pass\n"})
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n\n\ndef b():\n    pass\n")
        self.close()
        self.update({"status": "WIP"})
        self.write("src/a.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        result = self.close()
        self.assertEqual((result["capture"]["references"], result["capture"]["symbols"]), (1, 1))
        self.assertEqual(result["capture"]["alreadyRecorded"], 2)  # the file and a
        self.assertEqual(sorted(self.symbols()), ["src/a.py#a", "src/a.py#b"])
        again = scope.capture(self.root, "T-TWO", self.task().claimed_at)
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["written"])
        self.assertEqual(len(self.symbols()), 2)

    def test_another_claimed_task_makes_symbols_unresolved(self):
        self.repository({"src/a.py": "def a():\n    pass\n"})
        self.claim("T-THREE", owner="agent/other")
        self.claim()
        self.write("src/a.py", "def a():\n    return 1\n")
        self.close()
        reference = self.symbols()["src/a.py#a"]
        self.assertEqual(reference.lifecycle, "unresolved")
        self.assertIn("unresolved attribution: T-THREE", reference.evidence)

    def test_a_close_without_git_still_succeeds(self):
        self.claim()  # not a repository
        result = self.close()
        self.assertEqual(result["capture"]["status"], "skipped")
        self.assertEqual(self.task().status, "DONE")


if __name__ == "__main__":
    unittest.main()
