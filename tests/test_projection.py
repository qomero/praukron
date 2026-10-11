"""Metrics that say what they measure (T-PROJECTION-01, ADR-052).

The K-LionBook run showed figures that were correct and still misread:
coverage that silently excluded SYNTHETIC, acceptance divided by the whole
roadmap, UNTESTED on exercised work, and PLANNED phases full of finished work.
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import analytics, cli, compile as compiler, dashboard, layout, views  # noqa: E402

PHASES = """# Phases

## P1 — Core

Outcome:
Core works.

Entry:
- none

Exit:
- done

Exit authority:
T-EXIT

Status:
ACTIVE

## P2 — Money

Outcome:
Money works.

Entry:
- P1 complete

Exit:
- done

Exit authority:
T-P2-EXIT

Status:
PLANNED
"""

MODULES = """# Modules

## M-CORE — Core
- Phase: P1
- Outcome: Core.

## M-MONEY — Money
- Phase: P2
- Outcome: Money.
"""


def task(task_id: str, module: str, status: str, validation: str, deps: str = "none") -> str:
    return (f"## {task_id}: Work {task_id}\n- Status: {status}\n- Module: {module}\n"
            f"- Validation: {validation}\n- Dependencies: {deps}\n- AC: AC-{task_id}\n"
            + ("- Evidence: done\n" if status == "DONE" else "") + "\n")


def contract(task_id: str, *criteria: tuple[str, str]) -> str:
    lines = [f"## AC-{task_id} — Work\n"]
    for n, (cls, state) in enumerate(criteria, 1):
        lines.append(f"- `AC-{task_id}-{n:02d}` — Given work, When checked, Then it holds. `{cls}` · `{state}`")
    return "\n".join(lines) + "\n\n"


TASKS = "# Tasks\n\n" + "".join([
    task("T-A", "M-CORE", "DONE", "UNTESTED"),
    task("T-EXIT", "M-CORE", "TODO", "UNTESTED", "T-A"),
    task("T-B", "M-MONEY", "DONE", "UNTESTED"),
    task("T-C", "M-MONEY", "DONE", "AI_REVIEWED"),
    task("T-S", "M-MONEY", "DONE", "SYNTHETIC"),
    task("T-D", "M-MONEY", "WIP", "UNTESTED"),
    task("T-P2-EXIT", "M-MONEY", "TODO", "UNTESTED", "T-EXIT, T-D"),
])

ACCEPTANCE = "# Acceptance\n\n" + "".join([
    contract("T-A", ("TEST", "PASS"), ("RUNTIME", "PASS")),
    contract("T-EXIT", ("MANUAL", "NOT_RUN")),
    contract("T-B", ("INSPECTION", "PASS")),
    contract("T-C", ("TEST", "PASS")),
    contract("T-S", ("TEST", "PASS")),
    contract("T-D", ("TEST", "PASS"), ("TEST", "FAIL"), ("RUNTIME", "NOT_RUN")),
    contract("T-P2-EXIT", ("MANUAL", "NOT_RUN"), ("INSPECTION", "NOT_RUN")),
])


class ProjectionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-projection-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        authority = self.dir / layout.AUTHORITY_DIR
        (authority / "ADR").mkdir(parents=True)
        for name, text in (("PHASES.md", PHASES), ("MODULES.md", MODULES), ("TASKS.md", TASKS),
                           ("ACCEPTANCE.md", ACCEPTANCE),
                           ("THESIS.md", "# Product thesis\n\n- Statement: It works.\n"),
                           ("INTENT.md", "# Intent\n\nNone.\n"), ("HANDOFF.md", "# Handoff\n\nNone.\n")):
            (authority / name).write_text(text, encoding="utf-8")
        self.project = compiler.load(self.dir)
        self.report = analytics.report(self.project)
        self.metrics = self.report.metrics()
        self.compiled = compiler.as_json(self.project)

    def status(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            self._status_here()
        return out.getvalue()

    def _status_here(self) -> None:
        import os
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            cli.main(["status"])
        finally:
            os.chdir(cwd)

    def html(self) -> str:
        return dashboard.render(self.project, self.report, self.compiled)


class TestProjection(ProjectionCase):
    def test_coverage_is_named_for_what_it_counts(self) -> None:
        """AC-T-PROJECTION-01-01."""
        self.assertEqual(self.metrics["validationCoverage"], {"done": 1, "total": 7, "fraction": 0.1429})
        self.assertEqual(self.metrics["validationCounts"], ["AI_REVIEWED", "HUMAN_VERIFIED"])
        out = self.status()
        self.assertIn("1 / 7 execution tasks AI_REVIEWED or HUMAN_VERIFIED · SYNTHETIC 1 · UNTESTED 5", out)
        html = self.html()
        self.assertIn("Reviewed or verified tasks", html)
        self.assertIn("of all execution tasks · SYNTHETIC 1 · UNTESTED 5", html)
        self.assertNotIn(">Validation coverage<", html)

    def test_acceptance_is_split_by_started_work(self) -> None:
        """AC-T-PROJECTION-01-02."""
        breakdown = self.metrics["acceptanceBreakdown"]
        self.assertEqual(breakdown["started"], {"PASS": 6, "FAIL": 1, "NOT_RUN": 1})
        self.assertEqual(breakdown["notStarted"], {"PASS": 0, "FAIL": 0, "NOT_RUN": 3})
        self.assertIn("started work: 6 pass · 1 fail · 1 not run", self.status())
        self.assertIn("not started: 3 criteria not reached yet", self.status())
        held = analytics.describe({**self.metrics, "acceptanceBreakdown": {
            "started": {"PASS": 0, "FAIL": 0, "NOT_RUN": 0},
            "notStarted": {"PASS": 3, "FAIL": 0, "NOT_RUN": 0}}}, self.report)
        self.assertEqual(held["acceptanceLater"], "not started: 3 criteria, 3 already pass")
        html = self.html()
        self.assertIn("Criteria passed, started work", html)
        self.assertIn("1 fail · 1 not run · 3 more in work not started", html)
        state = views.state(self.project, self.report)
        self.assertIn("started work: 6 pass", state)

    def test_a_planned_phase_says_what_its_exit_waits_on(self) -> None:
        """AC-T-PROJECTION-01-03."""
        p2 = next(p for p in self.compiled["phases"] if p["id"] == "P2")
        self.assertEqual((p2["status"], p2["waitsOn"], p2["progress"]["done"]), ("PLANNED", "P1", 3))
        self.assertIsNone(next(p for p in self.compiled["phases"] if p["id"] == "P1")["waitsOn"])
        self.assertIn("P2           3 / 5 · PLANNED · exit waits on P1", self.status())
        self.assertIn("3 / 5 execution tasks · exit authority T-P2-EXIT · exit waits on P1", self.html())
        self.assertEqual([p.status for p in self.project.phases], ["ACTIVE", "PLANNED"])

    def test_work_ahead_of_phase_closure_is_described(self) -> None:
        """AC-T-PROJECTION-01-04."""
        ahead = self.compiled["execution"]["workAhead"]
        self.assertEqual(ahead, {"oldestOpenExit": {"phase": "P1", "exitAuthority": "T-EXIT"},
                                 "phases": {"P2": 3}, "tasks": 3})
        self.assertIn("3 done tasks ahead of P1's exit (T-EXIT): P2 3", self.status())
        self.assertIn('id="work-ahead"', self.html())
        self.assertIn("Work ahead of phase closure", views.state(self.project, self.report))
        before = {p.name: p.read_text(encoding="utf-8") for p in (self.dir / layout.AUTHORITY_DIR).glob("*.md")}
        compiler.write(self.dir, self.project)
        for name, text in before.items():
            self.assertEqual((self.dir / layout.AUTHORITY_DIR / name).read_text(encoding="utf-8"), text)

    def test_untested_done_work_says_whether_it_was_exercised(self) -> None:
        """AC-T-PROJECTION-01-05."""
        gaps = {o.subject: o for o in self.report.obstacles if o.type == "VALIDATION_GAP"}
        self.assertEqual(gaps["T-A"].variant, "REVIEW_MISSING")
        self.assertEqual(gaps["T-A"].detail, "T-A is DONE with RUNTIME and TEST evidence recorded; no review recorded")
        self.assertEqual(gaps["T-B"].variant, "EVIDENCE_MISSING")
        html = self.html()
        self.assertIn("Evidence recorded, review missing", html)
        self.assertIn("No exercised evidence, no review", html)
        self.assertNotIn("a task can be DONE and still UNTESTED", html)
        self.assertIn("3 done without review, 2 of them with exercised evidence", self.status())

    def test_output_is_deterministic_and_formats_are_unchanged(self) -> None:
        """AC-T-PROJECTION-01-06."""
        compiler.write(self.dir, self.project)
        first = (self.dir / layout.COMPILED_DIR / "project.json").read_text(encoding="utf-8")
        index = (self.dir / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(self.html(), self.html())
        self.assertEqual(self.status(), self.status())
        self.project = compiler.load(self.dir)
        compiler.write(self.dir, self.project)
        self.assertEqual((self.dir / layout.COMPILED_DIR / "project.json").read_text(encoding="utf-8"), first)
        self.assertEqual((self.dir / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8"), index)
        from praukron import model
        self.assertEqual(model.VALIDATIONS, ("UNTESTED", "SYNTHETIC", "AI_REVIEWED", "HUMAN_VERIFIED"))
        self.assertEqual(model.CRITERION_STATES, ("PASS", "FAIL", "NOT_RUN"))
        self.assertEqual(model.PHASE_STATUSES, ("PLANNED", "ACTIVE", "EXIT_PENDING", "COMPLETE"))
        for key in ("acceptanceCompletion", "validationCoverage", "validationBreakdown"):
            self.assertIn(key, self.metrics)


if __name__ == "__main__":
    unittest.main(verbosity=2)
