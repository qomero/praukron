"""`praukron why`: from a file or symbol to the records behind it (T-GRAPH-WHY-01, ADR-103)."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, engine, layout  # noqa: E402
from test_code_map import SOURCES, write_index  # noqa: E402
from test_code_references import chronicle_bytes, reference  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

ARCHIVED_TASKS = """# Tasks

## T-ZERO: Sketch the catalogue
- Status: DONE
- Module: M-FOUNDATION
- Validation: SYNTHETIC
- Dependencies: none
- Owner: someone
- Claimed: 2025-12-01
- AC: AC-T-ZERO
- Evidence: Sketched.
- Governed by: ADR-001
"""

ARCHIVED_ACCEPTANCE = """# Acceptance

# Contracts

## AC-T-ZERO — Sketch the catalogue

- `AC-T-ZERO-01` — Given names, When added, Then they are kept. `TEST` · `PASS`
  - Evidence: Kept.
"""

ADRS = {
    "ADR-002": """# ADR-002: Keep the catalogue in memory
- Date: 2026-01-02
- Status: SUPERSEDED
- Supersedes: none
- Affects: T-TWO
- Context: Small data.
- Decision: Keep it in memory.
""",
    "ADR-003": """# ADR-003: Keep the catalogue on disk
- Date: 2026-01-05
- Status: ACCEPTED
- Supersedes: ADR-002
- Affects: T-THREE
- Context: The data grew.
- Decision: Keep it on disk.
""",
    "ADR-004": """# ADR-004: Normalize names on input
- Date: 2026-01-06
- Status: PROPOSED
- Supersedes: none
- Affects: T-THREE
- Context: Names arrive untidy.
- Decision: Normalize them when they arrive.
""",
}

CODE = "# Code references\n\n" + "\n".join([
    # Written newest first: the answer orders by date, not by the file.
    reference("CR-T-ONE-01", "T-ONE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Recorded="2026-01-09 · agent/builder"),
    reference("CR-T-TWO-01", "T-TWO", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Scope="observed", Source="git_diff", Verification="verified", Relation="creates",
              Recorded="2026-01-03 · praukron"),
    reference("CR-T-ZERO-01", "T-ZERO", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Recorded="2025-12-02 · agent/builder"),
    # A reference to the whole file, which stands in for its symbols.
    reference("CR-T-THREE-01", "T-THREE", "pkg/util.py", Relation="reads", Authorization="not_applicable",
              Recorded="2026-01-07 · agent/builder"),
    # Withdrawn: counted apart, never as an explanation.
    reference("CR-T-THREE-02", "T-THREE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Withdrawn="attributed to the wrong task"),
])


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


class WhyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-why-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir / "shop")
        for path, text in SOURCES.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        authority = self.root / layout.AUTHORITY_DIR
        archive = authority / layout.ARCHIVE_DIR / "P1"
        archive.mkdir(parents=True)
        (archive / "TASKS.md").write_text(ARCHIVED_TASKS, encoding="utf-8")
        (archive / "ACCEPTANCE.md").write_text(ARCHIVED_ACCEPTANCE, encoding="utf-8")
        for adr, text in ADRS.items():
            (authority / "ADR" / f"{adr}.md").write_text(text, encoding="utf-8")
        tasks = authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        text = text.replace("- Governed by: ADR-001\n\n## T-THREE", "- Governed by: ADR-001, ADR-002\n\n## T-THREE")
        tasks.write_text(text, encoding="utf-8")
        (authority / "CODE.md").write_text(CODE, encoding="utf-8")

    def json(self, *argv: str) -> dict:
        code, out, err = run(self.root, "why", *argv, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def text(self, *argv: str) -> str:
        code, out, err = run(self.root, "why", *argv)
        self.assertEqual(code, 0, err)
        return out

    @staticmethod
    def task(result: dict, task_id: str) -> dict:
        return next(t for t in result["tasks"] if t["id"] == task_id)

    @staticmethod
    def decision(task: dict, adr: str) -> dict:
        return next(d for d in task["decisions"] if d["id"] == adr)


class TestHistory(WhyCase):
    """AC-T-GRAPH-WHY-01-01: tasks oldest first, each with its records."""

    def test_several_tasks_on_one_symbol_oldest_first(self):
        result = self.json("pkg/service.py#Catalog.add")
        self.assertTrue(result["explained"])
        self.assertEqual(result["kind"], "symbol")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ZERO", "T-TWO", "T-ONE"])
        self.assertEqual([t["date"] for t in result["tasks"]], ["2025-12-01", "2026-01-03", "2026-01-09"])
        self.assertEqual(self.task(result, "T-ZERO")["dated"], "claimed")
        self.assertEqual(self.task(result, "T-TWO")["dated"], "reference CR-T-TWO-01")

    def test_each_task_carries_how_its_reference_was_recorded(self):
        two = self.task(self.json("pkg/service.py#Catalog.add"), "T-TWO")
        [ref] = two["references"]
        self.assertEqual((ref["id"], ref["source"], ref["verification"], ref["relation"], ref["scope"],
                          ref["recorded"], ref["authority"]),
                         ("CR-T-TWO-01", "git_diff", "verified", "creates", "observed",
                          "2026-01-03 · praukron", "CODE.md#CR-T-TWO-01"))
        self.assertIn("reference CR-T-TWO-01 pkg/service.py#Catalog.add · creates · observed · verified · "
                      "source git_diff · recorded 2026-01-03 · praukron — CODE.md#CR-T-TWO-01",
                      self.text("pkg/service.py#Catalog.add"))

    def test_decisions_criteria_and_invariants_with_their_records(self):
        result = self.json("pkg/service.py#Catalog.add")
        one = self.task(result, "T-ONE")
        adr = self.decision(one, "ADR-001")
        self.assertEqual((adr["status"], adr["standing"], adr["inForce"], adr["via"], adr["authority"]),
                         ("ACCEPTED", "in force", True, ["Governed by", "Affects"], "ADR/ADR-001.md"))
        self.assertEqual(one["acceptance"]["id"], "AC-T-ONE")
        self.assertEqual([(c["id"], c["state"], c["authority"]) for c in one["acceptance"]["criteria"]],
                         [("AC-T-ONE-01", "PASS", "ACCEPTANCE.md#AC-T-ONE")])
        self.assertEqual(one["invariants"], [{"id": "AC-GLOBAL-CARE", "criteria": ["AC-GLOBAL-CARE-01"],
                                              "authority": "ACCEPTANCE.md#AC-GLOBAL-CARE"}])
        self.assertEqual(one["authority"], "TASKS.md#T-ONE")

    def test_an_archived_task_is_part_of_the_history(self):
        zero = self.task(self.json("pkg/service.py#Catalog.add"), "T-ZERO")
        self.assertTrue(zero["archived"])
        self.assertEqual(zero["authority"], "archive/P1/TASKS.md#T-ZERO")
        self.assertEqual(zero["acceptance"]["criteria"][0]["authority"], "archive/P1/ACCEPTANCE.md#AC-T-ZERO")
        self.assertIn("T-ZERO — Sketch the catalogue [DONE, archived]", self.text("pkg/service.py#Catalog.add"))

    def test_a_withdrawn_reference_explains_nothing(self):
        result = self.json("pkg/service.py#Catalog.add")
        self.assertNotIn("T-THREE", [t["id"] for t in result["tasks"]])
        self.assertEqual([r["id"] for r in result["notCurrent"]], ["CR-T-THREE-02"])

    def test_a_file_covers_its_symbols_and_a_directory_its_files(self):
        self.assertEqual([t["id"] for t in self.json("pkg/service.py")["tasks"]], ["T-ZERO", "T-TWO", "T-ONE"])
        directory = self.json("pkg/")
        self.assertEqual(directory["kind"], "directory")
        self.assertEqual([t["id"] for t in directory["tasks"]], ["T-ZERO", "T-TWO", "T-THREE", "T-ONE"])

    def test_a_task_id_is_refused_with_the_command_to_use(self):
        code, _, err = run(self.root, "why", "T-ONE")
        self.assertEqual(code, 1)
        self.assertIn("praukron refs T-ONE", err)


class TestStanding(WhyCase):
    """AC-T-GRAPH-WHY-01-03: a superseded decision is never the rule in force."""

    def test_superseded_decision_names_its_replacement(self):
        result = self.json("pkg/service.py#Catalog.add")
        old = self.decision(self.task(result, "T-TWO"), "ADR-002")
        self.assertEqual((old["standing"], old["inForce"]), ("superseded", False))
        self.assertEqual(old["supersededBy"], [{"id": "ADR-003", "status": "ACCEPTED",
                                                "authority": "ADR/ADR-003.md"}])
        text = self.text("pkg/service.py#Catalog.add")
        [line] = [line for line in text.splitlines() if "decision ADR-002" in line]
        self.assertIn("superseded by ADR-003 [ACCEPTED], not the rule in force", line)
        self.assertNotIn("] in force", line)
        self.assertIn("replaced by ADR-003 [ACCEPTED] — ADR/ADR-003.md", text)

    def test_superseded_without_a_successor_is_still_superseded(self):
        (self.root / layout.AUTHORITY_DIR / "ADR" / "ADR-003.md").write_text(
            ADRS["ADR-003"].replace("Supersedes: ADR-002", "Supersedes: none"), encoding="utf-8")
        old = self.decision(self.task(self.json("pkg/service.py#Catalog.add"), "T-TWO"), "ADR-002")
        self.assertEqual((old["standing"], old["inForce"], old["supersededBy"]), ("superseded", False, []))

    def test_a_proposed_decision_is_marked_not_accepted(self):
        three = self.task(self.json("pkg/util.py"), "T-THREE")
        proposed = self.decision(three, "ADR-004")
        self.assertEqual((proposed["status"], proposed["standing"], proposed["inForce"]),
                         ("PROPOSED", "not accepted", False))
        self.assertIn("decision ADR-004 [PROPOSED] not accepted, not the rule in force · Affects",
                      self.text("pkg/util.py"))


class TestNoRecord(WhyCase):
    """AC-T-GRAPH-WHY-01-02: a symbol no task references gets a plain answer."""

    def test_the_file_reference_stands_in_for_its_symbol(self):
        code, out, err = run(self.root, "why", "pkg/util.py#normalize", "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertFalse(result["explained"])
        self.assertEqual(result["nearest"], {"target": "pkg/util.py", "path": "pkg/util.py", "symbol": None,
                                             "kind": "file"})
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-THREE"])
        text = self.text("pkg/util.py#normalize")
        self.assertTrue(text.startswith("pkg/util.py#normalize — no record explains it"))
        self.assertIn("Nearest enclosing file with a record: pkg/util.py — 1 task through 1 reference", text)

    def test_the_nearest_enclosing_symbol_comes_before_the_file(self):
        result = self.json("pkg/service.py#Catalog.add.inner")
        self.assertFalse(result["explained"])
        self.assertEqual(result["nearest"]["target"], "pkg/service.py#Catalog.add")
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ZERO", "T-TWO", "T-ONE"])

    def test_nothing_encloses_it(self):
        result = self.json("main.py#main")
        self.assertEqual((result["explained"], result["nearest"], result["tasks"]), (False, None, []))
        self.assertIn("No enclosing symbol, file or directory has a record either.", self.text("main.py#main"))

    def test_a_bare_member_name_falls_back_to_its_file(self):
        # The index, which could place `add` in `Catalog`, is read only by `engine`.
        sizes = {path: len(text.encode("utf-8")) for path, text in SOURCES.items()}
        write_index(engine.index_path(self.root), str(self.root), sizes)
        result = self.json("pkg/service.py#add")
        self.assertFalse(result["explained"])
        self.assertEqual((result["nearest"], result["tasks"]), (None, []))


class TestChronicleOnly(WhyCase):
    """AC-T-GRAPH-WHY-01-04 and -05: answered from the chronicle, stored nowhere."""

    def test_answered_in_full_without_a_library_or_an_index(self):
        self.assertFalse(engine.index_path(self.root).exists())
        before = self.json("pkg/service.py#Catalog.add")
        # A junk index changes nothing: it is never read.
        engine.index_path(self.root).parent.mkdir(parents=True)
        engine.index_path(self.root).write_bytes(b"T-ONE ADR-001 not a store")
        with mock.patch.object(engine, "locate", side_effect=AssertionError("library looked up")), \
                mock.patch.object(engine, "load", side_effect=AssertionError("library loaded"), create=True):
            result = self.json("pkg/service.py#Catalog.add")
        self.assertEqual(len(result["tasks"]), 3)
        self.assertEqual(result, before)

    def test_nothing_is_written_and_every_line_names_a_record(self):
        before = chronicle_bytes(self.root)
        for target in ("pkg/service.py#Catalog.add", "pkg/util.py#normalize", "pkg/", "main.py#main"):
            text = self.text(target)
            self.json(target)
            lines = [line for line in text.splitlines() if line]
            for line in lines[1:]:
                if line.startswith(("Nearest enclosing", "No enclosing", "No longer current")):
                    continue
                self.assertRegex(line, r"— (?:archive/P1/)?(?:[A-Z]+\.md|ADR/ADR-\d+\.md)(?:#[\w.-]+)?$", line)
        self.assertEqual(chronicle_bytes(self.root), before)
        self.assertFalse((self.root / layout.AUTHORITY_DIR / "CODE.md").read_text(encoding="utf-8")
                         .count("why"))

    def test_every_authority_resolves_to_a_record(self):
        authority = self.root / layout.AUTHORITY_DIR
        result = self.json("pkg/")
        pointers = re.findall(r'"authority": "([^"]+)"', json.dumps(result))
        self.assertTrue(pointers)
        for pointer in pointers:
            name, _, anchor = pointer.partition("#")
            text = (authority / name).read_text(encoding="utf-8")
            if anchor:
                self.assertIn(anchor, text, pointer)


if __name__ == "__main__":
    unittest.main()
