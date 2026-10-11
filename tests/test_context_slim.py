"""Packets carry what the task needs and point to the rest (T-CONTEXT-SLIM-01, ADR-059)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, cli, compile as compiler, layout  # noqa: E402
from test_praukron import build_fixture  # noqa: E402


def _event(n: int, task: str, outcome: str = "success", retry_of: str | None = None) -> str:
    return (f"## EV-{n:03d}: Step {n}\n- Type: command\n- Task: {task}\n- Outcome: {outcome}\n"
            + (f"- Retry of: {retry_of}\n" if retry_of else "") + "\n")


class SlimCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-slim-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        build_fixture(self.dir)
        self.authority = self.dir / layout.AUTHORITY_DIR
        trace = "".join(_event(n, "T-TWO") for n in range(1, 8))
        trace += _event(8, "T-TWO", "failure") + _event(9, "T-TWO", "failure") + _event(10, "T-TWO", "success", "EV-009")
        (self.authority / "TRACE.md").write_text("# Trace\n\n" + trace, encoding="utf-8")
        self.project = compiler.load(self.dir)

    def cli(self, *args: str) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.dir), *args]), 0)
        return out.getvalue()


class TestContextSlim(SlimCase):
    def test_events_are_summarized_with_unresolved_failures_in_full(self) -> None:
        """AC-T-CONTEXT-SLIM-01-01."""
        events = analytics.context(self.project, "T-TWO")["events"]
        self.assertEqual((events["total"], events["failed"], events["unresolved"]), (10, 2, 1))
        self.assertEqual([e["id"] for e in events["unresolvedFailures"]], ["EV-008"])
        self.assertEqual(events["unresolvedFailures"][0]["outcome"], "failure")
        self.assertEqual(events["recent"], ["EV-006", "EV-007", "EV-008", "EV-009", "EV-010"])
        self.assertEqual(events["more"], "praukron explain T-TWO")
        self.assertEqual(len(analytics.explain(self.project, "T-TWO")["events"]), 10)

    def test_handoff_is_a_pointer_with_its_body_only_when_it_names_the_task(self) -> None:
        """AC-T-CONTEXT-SLIM-01-02."""
        (self.authority / "HANDOFF.md").write_text("# Handoff\n\nT-ONE is half built.\n", encoding="utf-8")
        self.project = compiler.load(self.dir)
        other = analytics.context(self.project, "T-TWO")
        self.assertIsNone(other["handoff"])
        self.assertIn("HANDOFF.md", other["authority"]["read"])
        named = analytics.context(self.project, "T-ONE")
        self.assertIn("T-ONE is half built.", named["handoff"])
        self.assertIn("HANDOFF.md", named["authority"]["read"])

    def test_orientation_counts_blocked_work_and_shows_the_first_ten(self) -> None:
        """AC-T-CONTEXT-SLIM-01-03."""
        tasks = self.authority / "TASKS.md"
        extra = "".join(
            f"\n## T-B{n:02d}: Blocked {n}\n- Status: TODO\n- Module: M-FOUNDATION\n- Validation: UNTESTED\n"
            f"- Dependencies: T-TWO\n- AC: AC-T-TWO\n" for n in range(1, 13))
        tasks.write_text(tasks.read_text(encoding="utf-8") + extra, encoding="utf-8")
        self.project = compiler.load(self.dir)
        report = analytics.report(self.project)
        blocked = analytics.context(self.project, None)["blocked"]
        self.assertEqual(blocked["count"], 13)
        self.assertEqual(len(blocked["first"]), 10)
        self.assertEqual(blocked["more"], "praukron status")
        on_path = [t["id"] for t in blocked["first"] if t["id"] in report.critical_path]
        self.assertEqual([t["id"] for t in blocked["first"]][:len(on_path)], on_path)

    def test_json_is_compact_by_default_and_the_thesis_appears_once(self) -> None:
        """AC-T-CONTEXT-SLIM-01-04."""
        compact = self.cli("context", "T-TWO")
        pretty = self.cli("context", "T-TWO", "--pretty")
        self.assertNotIn("\n  ", compact.strip())
        self.assertIn("\n  ", pretty)
        self.assertEqual(json.loads(compact), json.loads(pretty))
        self.assertLess(len(compact), len(pretty))
        packet = json.loads(compact)
        self.assertEqual(packet["lineage"]["thesisRef"], "THESIS.md")
        self.assertNotIn("A building people can live in.", compact)
        orientation = self.cli("context")
        self.assertEqual(orientation.count("A building people can live in."), 1)

    def test_output_is_deterministic_and_read_only(self) -> None:
        """AC-T-CONTEXT-SLIM-01-06."""
        before = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}
        for args in (("context",), ("context", "T-TWO")):
            self.assertEqual(self.cli(*args), self.cli(*args))
        self.assertEqual({p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}, before)
        self.assertFalse((self.dir / layout.COMPILED_DIR).exists())

    def test_a_packet_names_what_the_task_unlocks(self) -> None:
        """AC-T-CONTEXT-SLIM-01-07."""
        self.assertEqual(analytics.context(self.project, "T-TWO")["task"]["unlocks"],
                         [{"id": "T-THREE", "status": "TODO"}])
        self.assertEqual(analytics.context(self.project, "T-ONE")["task"]["unlocks"],
                         [{"id": "T-TWO", "status": "TODO"}])
        self.assertEqual(analytics.context(self.project, "T-THREE")["task"]["unlocks"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
