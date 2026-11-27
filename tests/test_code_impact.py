"""`praukron engine impact`: what a planned change runs into (T-GRAPH-IMPACT-01, ADR-103).

The index is written by the test with `sqlite3` and the store's own schema, as
`tests/test_code_map.py` does; `tests/test_engine_library.py` asks the same
question of an index the real library wrote.

The graph of the fixture, callers first (`-[mark]->` is a resolved edge):

    pkg/service.py#Catalog.add -[call exact]-> pkg/util.py#normalize
    pkg/util.py#slug           -[call exact]-> pkg/util.py#normalize
    main.py#main               -[call likely]-> pkg/service.py#Catalog.add

T-THREE plans to change `normalize`. T-ONE produced `Catalog.add` and the
whole of `pkg/util.py`; T-TWO produced `main`. ADR-003 (accepted) affects
T-ONE and supersedes ADR-002, which affects T-TWO. ADR-001 governs every task,
T-THREE included, so it is listed apart.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, engine, impact, layout  # noqa: E402
from test_code_map import SOURCES, write_index  # noqa: E402
from test_code_references import chronicle_bytes, reference  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

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
- Affects: T-ONE
- Context: The data grew.
- Decision: Keep it on disk.
""",
}

CODE = "# Code references\n\n" + "\n".join([
    reference("CR-T-THREE-01", "T-THREE", "pkg/util.py", Symbol="normalize", Kind="function",
              Relation="modifies", Source="manual"),
    reference("CR-T-ONE-01", "T-ONE", "pkg/service.py", Symbol="Catalog.add", Kind="method",
              Scope="observed", Source="git_diff", Verification="verified", Relation="creates",
              Recorded="2026-01-03 · praukron"),
    reference("CR-T-ONE-02", "T-ONE", "pkg/util.py", Scope="observed", Source="git_diff", Relation="creates"),
    reference("CR-T-TWO-01", "T-TWO", "main.py", Symbol="main", Kind="function", Relation="creates",
              Source="agent", Evidence="main was written for T-TWO, read at main.py:4"),
])

# Words that would state a verdict (AC-T-GRAPH-IMPACT-01-04).
VERDICT = re.compile(r"conflict(s)? (found|exists?|detected)|no conflicts?\b|conflicts with|is safe|"
                     r"nothing (is )?reached|reaches nothing", re.IGNORECASE)


def run(root: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue(), err.getvalue()


class ImpactCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-impact-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir / "shop")
        for path, text in SOURCES.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        authority = self.root / layout.AUTHORITY_DIR
        for adr, text in ADRS.items():
            (authority / "ADR" / f"{adr}.md").write_text(text, encoding="utf-8")
        (authority / "CODE.md").write_text(CODE, encoding="utf-8")
        self.db = engine.index_path(self.root)
        write_index(self.db, str(self.root), {p: len(t.encode("utf-8")) for p, t in SOURCES.items()})
        environ = mock.patch.dict(os.environ, {engine.ENV_VAR: ""})
        environ.start()
        self.addCleanup(environ.stop)
        loader = mock.patch.object(engine, "_cdll", side_effect=AssertionError("the library was loaded"))
        loader.start()
        self.addCleanup(loader.stop)
        self.before = chronicle_bytes(self.root)

    def tearDown(self) -> None:
        # Nothing the answer computes reaches the chronicle.
        self.assertEqual(chronicle_bytes(self.root), self.before)

    def edges(self, *rows: tuple) -> None:
        """Add edge rows: (id, file, from_symbol, name, qualifier, kind, line, to_symbol, to_file, confidence)."""
        con = sqlite3.connect(str(self.db))
        for row in rows:
            edge_id, file_id, source, name, qualifier, kind, line, to_symbol, to_file, confidence = row
            con.execute("INSERT INTO edges VALUES (?, 1, ?, ?, NULL, ?, ?, ?, ?, 0, ?, ?, ?, NULL)",
                        (edge_id, file_id, source, name, qualifier, kind, line, to_symbol, to_file, confidence))
        con.commit()
        con.close()

    def impact(self, *argv: str) -> dict:
        code, out, err = run(self.root, "engine", "impact", "T-THREE", "--json", *argv)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def text(self, *argv: str, task: str = "T-THREE") -> str:
        code, out, err = run(self.root, "engine", "impact", task, *argv)
        self.assertEqual((code, err), (0, ""))
        return out

    @staticmethod
    def reached(result: dict) -> dict:
        return {f"{p['node']['path']}#{p['node'].get('symbol')}": p for p in result["reached"]}


class TestReach(ImpactCase):
    """AC-T-GRAPH-IMPACT-01-01: touched code, reach to a stated depth, tasks, decisions, criteria, paths."""

    def test_depth_one_reaches_the_direct_callers_and_the_tasks_on_them(self):
        result = self.impact("--depth", "1")
        self.assertEqual(result["depth"], 1)
        self.assertEqual([r["id"] for r in result["references"]], ["CR-T-THREE-01"])
        self.assertEqual([t["node"]["id"] for t in result["touched"]], ["s:10"])
        self.assertEqual(set(self.reached(result)), {"pkg/service.py#Catalog.add", "pkg/util.py#slug"})
        self.assertEqual(result["counts"]["reached"], 2)
        self.assertEqual(result["counts"]["reachedByDepth"], {"1": 2})
        # T-ONE lies on the touched file itself (depth 0) and on Catalog.add (depth 1); T-TWO is out of reach.
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        one = result["tasks"][0]
        self.assertEqual({(p["node"]["id"], p["depth"]) for p in one["through"]},
                         {("s:10", 0), ("s:11", 1), ("s:22", 1)})
        self.assertEqual(one["path"]["depth"], 0)
        # How each link was recorded, as `why` shows it.
        links = {r["id"]: r for r in one["references"]}
        self.assertEqual(links["CR-T-ONE-01"]["source"], "git_diff")
        self.assertEqual(links["CR-T-ONE-01"]["recorded"], "2026-01-03 · praukron")
        self.assertEqual(links["CR-T-ONE-02"]["source"], "git_diff")

        text = self.text("--depth", "1")
        self.assertIn("Depth 1, following every resolved edge", text)
        self.assertIn("2 nodes reached (2 at depth 1)", text)
        self.assertIn("pkg/service.py#Catalog.add -[call exact]-> pkg/util.py#normalize · depth 1 (exact) "
                      "· touched by CR-T-THREE-01", text)

    def test_depth_two_follows_the_callers_of_the_callers(self):
        result = self.impact("--depth", "2")
        reached = self.reached(result)
        self.assertEqual(set(reached), {"pkg/service.py#Catalog.add", "pkg/util.py#slug", "main.py#main"})
        main = reached["main.py#main"]
        self.assertEqual(main["depth"], 2)
        self.assertEqual(main["marks"], ["likely", "exact"])
        self.assertFalse(main["exact"])
        self.assertEqual([(s["from"]["symbol"], s["to"]["symbol"]) for s in main["steps"]],
                         [("main", "Catalog.add"), ("Catalog.add", "normalize")])
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE", "T-TWO"])
        # The default depth is the small one the module states.
        self.assertEqual(self.impact()["depth"], impact.DEFAULT_DEPTH)
        self.assertEqual(impact.DEFAULT_DEPTH, 2)
        text = self.text("--depth", "2")
        self.assertIn("main.py#main -[call likely]-> pkg/service.py#Catalog.add -[call exact]-> "
                      "pkg/util.py#normalize · depth 2 (not exact only: likely, exact)", text)

    def test_grouped_by_decision_with_the_shortest_path_then_frozen_criteria_and_invariants(self):
        result = self.impact()
        groups = {g["id"]: g for g in result["decisions"]}
        self.assertEqual(list(groups), ["ADR-003", "ADR-002"])
        self.assertEqual(groups["ADR-003"]["standing"], "in force")
        self.assertEqual([t["id"] for t in groups["ADR-003"]["tasks"]], ["T-ONE"])
        self.assertEqual(groups["ADR-003"]["path"]["depth"], 0)
        # The task's own decision is listed apart, with the reached tasks it also governs.
        self.assertEqual([d["id"] for d in result["ownDecisions"]], ["ADR-001"])
        self.assertEqual(result["ownDecisions"][0]["alsoReachedThrough"], ["T-ONE", "T-TWO"])
        self.assertNotIn("ADR-001", groups)
        tasks = {t["id"]: t for t in result["tasks"]}
        # T-ONE is DONE: its contract is frozen, and it inherits the project's invariant.
        self.assertTrue(tasks["T-ONE"]["frozen"])
        self.assertEqual([c["id"] for c in tasks["T-ONE"]["criteria"]], ["AC-T-ONE-01"])
        self.assertEqual([i["id"] for i in tasks["T-ONE"]["invariants"]], ["AC-GLOBAL-CARE"])
        # T-TWO is TODO: its criteria are counted, not presented as frozen.
        self.assertFalse(tasks["T-TWO"]["frozen"])
        self.assertEqual((tasks["T-TWO"]["criteria"], tasks["T-TWO"]["criteriaNotFrozen"]), ([], 1))

        text = self.text()
        adr3, adr2 = text.index("decision ADR-003 [ACCEPTED] in force"), text.index("decision ADR-002")
        self.assertLess(adr3, adr2)
        section = text[adr3:adr2]
        self.assertIn("shortest path: pkg/util.py#normalize · touched by CR-T-THREE-01", section)
        self.assertLess(section.index("T-ONE — Lay the foundation"),
                        section.index("frozen criterion AC-T-ONE-01 [PASS]"))
        self.assertLess(section.index("frozen criterion AC-T-ONE-01"), section.index("invariant AC-GLOBAL-CARE"))
        self.assertIn("reference CR-T-ONE-01 pkg/service.py#Catalog.add · creates · observed · verified "
                      "· source git_diff", section)
        own = text[text.index("Decisions T-THREE already holds (1), listed apart"):adr3]
        self.assertIn("decision ADR-001 [ACCEPTED] in force", own)

    def test_a_superseded_decision_is_shown_as_superseded(self):
        result = self.impact()
        group = next(g for g in result["decisions"] if g["id"] == "ADR-002")
        self.assertEqual(group["standing"], "superseded")
        self.assertFalse(group["inForce"])
        self.assertEqual([s["id"] for s in group["supersededBy"]], ["ADR-003"])
        self.assertEqual(result["counts"]["decisionsByStanding"],
                         {"in force": 1, "not accepted": 0, "superseded": 1})
        text = self.text()
        self.assertIn("decision ADR-002 [SUPERSEDED] superseded by ADR-003 [ACCEPTED], not the rule in force", text)

    def test_exact_only_leaves_the_likely_edge_unfollowed_and_says_so(self):
        result = self.impact("--exact")
        self.assertTrue(result["exactOnly"])
        self.assertEqual(set(self.reached(result)), {"pkg/service.py#Catalog.add", "pkg/util.py#slug"})
        self.assertTrue(all(p["exact"] for p in result["reached"]))
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        self.assertEqual(result["counts"]["notFollowedNotExact"], 1)
        text = self.text("--exact")
        self.assertIn("following exact edges only", text)
        self.assertIn("1 edges not marked exact were not followed.", text)

    def plan(self, path: str, symbol: str | None = None) -> None:
        """T-THREE plans to change this file or symbol instead of normalize."""
        extra = {"Symbol": symbol, "Kind": "class"} if symbol else {}
        first = reference("CR-T-THREE-01", "T-THREE", "pkg/util.py", Symbol="normalize", Kind="function",
                          Relation="modifies", Source="manual")
        self.assertIn(first, CODE)
        (self.root / layout.AUTHORITY_DIR / "CODE.md").write_text(CODE.replace(first, reference(
            "CR-T-THREE-01", "T-THREE", path, Relation="modifies", Source="manual", **extra)), encoding="utf-8")
        self.before = chronicle_bytes(self.root)

    def test_a_class_counts_its_members(self):
        self.plan("pkg/service.py", "Catalog")
        result = self.impact("--depth", "1")
        self.assertEqual([t["node"]["id"] for t in result["touched"]], ["s:20"])
        main = self.reached(result)["main.py#main"]
        # Of the two edges from main into Catalog, the exact one is the shortest path's.
        self.assertEqual([(s["to"]["symbol"], s["confidence"]) for s in main["steps"]], [("Catalog", "exact")])
        # T-ONE's reference to Catalog.add lies on the touched class.
        self.assertIn("T-ONE", [t["id"] for t in result["tasks"]])

    def test_a_file_counts_its_symbols_and_its_imports(self):
        self.plan("pkg/service.py")
        result = self.impact("--depth", "1")
        self.assertEqual([t["node"]["id"] for t in result["touched"]], ["f:2"])
        reached = self.reached(result)
        self.assertEqual(set(reached), {"main.py#main", "main.py#None"})
        self.assertEqual(reached["main.py#None"]["steps"][0]["kind"], "import")
        self.assertEqual(reached["main.py#None"]["marks"], ["unmarked"])


class TestUnknown(ImpactCase):
    """AC-T-GRAPH-IMPACT-01-03: an unresolved edge on touched code makes further reach unknown."""

    def test_unresolved_edges_on_the_touched_symbol_are_unknown_never_absent(self):
        self.edges((20, 3, 30, "normalize", None, "call", 6, None, None, None),   # a caller not placed
                   (21, 1, 10, "lower", "text", "call", 2, None, None, None))     # a callee not placed
        self.before = chronicle_bytes(self.root)
        result = self.impact("--depth", "1")
        unknown = {u["node"]["id"]: u for u in result["unknown"]}
        self.assertIn("s:10", unknown)
        self.assertEqual(unknown["s:10"]["unresolvedCallers"], {"edges": 1, "names": {"normalize": 1}})
        self.assertEqual(unknown["s:10"]["unresolvedFrom"], {"edges": 1, "names": {"text.lower": 1}})
        text = self.text("--depth", "1")
        self.assertIn("reach unknown: 1 unresolved edges elsewhere that bear its name, callers the engine "
                      "could not place: normalize", text)
        self.assertIn("reach unknown: 1 edges from it the engine left unresolved: text.lower", text)
        self.assertIsNone(VERDICT.search(text), VERDICT.search(text))

    def test_unresolved_reach_is_said_even_when_no_resolved_caller_exists(self):
        con = sqlite3.connect(str(self.db))
        con.execute("DELETE FROM edges WHERE to_symbol = 10")
        con.commit()
        con.close()
        self.edges((20, 3, 30, "normalize", None, "call", 6, None, None, None))
        result = self.impact()
        self.assertEqual(result["reached"], [])
        self.assertEqual(result["counts"]["unknown"], 1)
        text = self.text()
        self.assertIn("none through a resolved edge, and reach is unknown where marked above", text)
        self.assertIsNone(VERDICT.search(text), VERDICT.search(text))

    def test_a_reference_with_no_node_has_unknown_reach(self):
        code = self.root / layout.AUTHORITY_DIR / "CODE.md"
        code.write_text(CODE + "\n" + reference("CR-T-THREE-02", "T-THREE", "pkg/gone.py", Source="manual"),
                        encoding="utf-8")
        self.before = chronicle_bytes(self.root)
        result = self.impact()
        item = next(r for r in result["references"] if r["id"] == "CR-T-THREE-02")
        self.assertEqual((item["reach"], item["unknown"]["reason"]), ("unknown", "file-absent"))
        self.assertIn("reach unknown: with no node", self.text())


class TestWithoutIndex(ImpactCase):
    """AC-T-GRAPH-IMPACT-01-02: no index, only the task's own code and the records behind it."""

    def test_no_index_answers_from_the_references_and_says_reach_is_unknown(self):
        self.db.unlink()
        result = self.impact()
        self.assertFalse(result["graph"]["present"])
        self.assertTrue(result["reach"].startswith("unknown"))
        self.assertEqual(result["reached"], [])
        self.assertEqual([t["node"]["id"] for t in result["touched"]], ["ref:pkg/util.py#normalize"])
        # T-ONE's reference to the whole file lies on it; T-TWO's code is not the task's.
        self.assertEqual([t["id"] for t in result["tasks"]], ["T-ONE"])
        self.assertEqual([r["id"] for r in result["tasks"][0]["references"]], ["CR-T-ONE-02"])
        self.assertEqual([g["id"] for g in result["decisions"]], ["ADR-003"])
        text = self.text()
        self.assertIn("No graph: no graph index at .praukron/engine/graph.db", text)
        self.assertIn("Reach through the graph is unknown: no graph index", text)
        self.assertIsNone(VERDICT.search(text), VERDICT.search(text))


class TestNoReferences(ImpactCase):
    def test_a_task_without_references_is_told_how_to_add_planned_ones(self):
        code, out, err = run(self.root, "engine", "impact", "T-TWO")
        self.assertEqual((code, err), (0, ""))
        # T-TWO has one reference; drop it and ask again.
        path = self.root / layout.AUTHORITY_DIR / "CODE.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:text.index("## CR-T-TWO-01")], encoding="utf-8")
        self.before = chronicle_bytes(self.root)
        code, out, err = run(self.root, "engine", "impact", "T-TWO", "--json")
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual((result["references"], result["tasks"]), ([], []))
        out = self.text(task="T-TWO")
        self.assertIn("T-TWO references no file or symbol, so there is nothing to follow from.", out)
        self.assertIn("`praukron ref add` (`Scope: planned`)", out)

    def test_a_target_that_is_not_a_task_is_refused(self):
        for target, prefix in (("T-NOPE", "unknown-task"), ("pkg/util.py", "invalid-impact-target")):
            with self.subTest(target=target):
                code, out, err = run(self.root, "engine", "impact", target)
                self.assertEqual((code, out), (1, ""))
                self.assertIn(prefix, err)
        code, out, err = run(self.root, "engine", "impact", "T-THREE", "--depth", "-1")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("invalid-depth", err)


class TestWording(ImpactCase):
    """AC-T-GRAPH-IMPACT-01-04: a list to check against, never a verdict; the reader records the conclusion."""

    def test_the_answer_is_a_list_to_check_against_and_asks_for_the_conclusion(self):
        for argv in ((), ("--exact",), ("--depth", "0")):
            with self.subTest(argv=argv):
                text = self.text(*argv)
                self.assertIn("What this change must be checked against.", text)
                self.assertIn("It does not decide whether anything conflicts", text)
                self.assertIn("record your conclusion in the chronicle", text)
                self.assertTrue(text.rstrip().endswith(
                    "This is not a verdict. Record what you conclude from it in the chronicle."))
                self.assertIsNone(VERDICT.search(text), VERDICT.search(text))
        result = self.impact()
        self.assertIsNone(result["verdict"])
        self.assertIn("does not decide whether anything conflicts", result["purpose"])
        self.assertIsNone(VERDICT.search(json.dumps(result)))


class TestGuidance(unittest.TestCase):
    """AC-T-GRAPH-IMPACT-01-05: an agent that plans a change to existing code asks for the impact first."""

    def test_the_guidance_names_the_impact_before_a_change_within_its_size_limits(self):
        from test_code_guidance import BLOCK_BASE_BYTES, BLOCK_BUDGET, block, flat, read

        agents = flat(block())
        self.assertIn("Before changing existing code, ask `engine impact <task>`.", agents)
        self.assertLessEqual(len(block().encode("utf-8")) - BLOCK_BASE_BYTES, BLOCK_BUDGET)
        work = flat(read(".praukron/commands/praukron-work.md"))
        self.assertIn("Before you change existing code, run `.praukron/praukron engine impact <task>`", work)
        self.assertIn("not a verdict", work)
        self.assertIn("record your conclusion", work)
        skill = flat(read(".agents/skills/praukron/SKILL.md"))
        self.assertIn("`praukron engine impact <task>`", skill)


if __name__ == "__main__":
    unittest.main()
