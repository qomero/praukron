"""Bounded write cost on a large chronicle (T-PERF-OPT-01, ADR-086)."""

from __future__ import annotations

import io
import json
import sys
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import archive, cli, compile as compiler, dashboard, layout  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402


def run(root: Path, *argv: str, stdin: str | None = None) -> tuple[int, str]:
    out = io.StringIO()
    old = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with redirect_stdout(out):
            code = cli.main(["-C", str(root), *argv])
    finally:
        sys.stdin = old
    return code, out.getvalue()


class TestDashboardOnDemand(FixtureCase):
    def setUp(self):
        super().setUp()
        self.page = self.root / layout.COMPILED_DIR / "dashboard.html"

    def journal(self, title: str) -> dict:
        code, out = run(self.root, "journal", "append", "--input", "-",
                        stdin=json.dumps({"title": title, "body": "Recorded."}))
        self.assertEqual(code, 0, out)
        return json.loads(out)

    def test_a_write_flags_a_rendered_page_once_and_leaves_its_content(self):
        self.assertEqual(run(self.root, "compile")[0], 0)
        self.assertEqual(run(self.root, "dashboard")[0], 0)
        fresh = self.page.read_text(encoding="utf-8")
        self.assertIn(dashboard.STALE_MARKER, fresh)
        self.assertNotIn("stale-notice", fresh)
        result = self.journal("First")
        self.assertTrue(result["refreshed"])
        stale = self.page.read_text(encoding="utf-8")
        self.assertNotIn(dashboard.STALE_MARKER, stale)
        self.assertEqual(stale.count('class="stale-notice"'), 1)
        self.assertIn("This page is out of date", stale)
        self.assertIn("praukron dashboard --serve", stale)
        # Everything but the notice is the page as it was rendered.
        notice = stale[stale.index('<p class="stale-notice"'):stale.index("</p>", stale.index("stale-notice")) + 4]
        self.assertEqual(stale.replace(notice, dashboard.STALE_MARKER), fresh)
        self.journal("Second")
        self.assertEqual(self.page.read_text(encoding="utf-8"), stale)
        self.assertEqual(run(self.root, "dashboard")[0], 0)
        rebuilt = self.page.read_text(encoding="utf-8")
        self.assertIn(dashboard.STALE_MARKER, rebuilt)
        self.assertNotIn("stale-notice", rebuilt)

    def test_the_notice_follows_the_page_language(self):
        self.assertEqual(run(self.root, "dashboard", "--lang", "vi")[0], 0)
        self.journal("Ghi lại")
        self.assertIn("Trang này đã cũ", self.page.read_text(encoding="utf-8"))

    def test_a_write_never_creates_the_page(self):
        self.journal("No page")
        self.assertFalse(self.page.exists())

    def test_compile_flags_the_page_only_when_the_state_changed(self):
        self.assertEqual(run(self.root, "compile")[0], 0)
        self.assertEqual(run(self.root, "dashboard")[0], 0)
        self.assertEqual(run(self.root, "compile")[0], 0)
        self.assertIn(dashboard.STALE_MARKER, self.page.read_text(encoding="utf-8"))
        self.rewrite("TASKS.md", "## T-THREE: Finish later", "## T-THREE: Finish much later")
        self.assertEqual(run(self.root, "compile")[0], 0)
        self.assertIn('class="stale-notice"', self.page.read_text(encoding="utf-8"))

    def test_a_page_from_an_older_release_is_left_alone(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text("<html><body>old</body></html>", encoding="utf-8")
        self.journal("Old page")
        self.assertEqual(self.page.read_text(encoding="utf-8"), "<html><body>old</body></html>")


class TestBoundedStatus(FixtureCase):
    def add_complete_phases(self, count: int) -> None:
        phases = self.root / layout.AUTHORITY_DIR / "PHASES.md"
        text = phases.read_text(encoding="utf-8")
        extra = "".join(f"## P0{i} — Earlier {i}\n\nOutcome:\nDone.\n\nStatus:\nCOMPLETE\n\n---\n\n"
                        for i in range(count))
        phases.write_text(text.replace("## P1 — Foundation", extra + "## P1 — Foundation", 1), encoding="utf-8")

    def test_many_complete_phases_fold_into_one_line(self):
        self.add_complete_phases(5)
        code, out = run(self.root, "status")
        self.assertEqual(code, 0, out)
        self.assertIn("P00–P04", out)
        self.assertIn("5 phases COMPLETE", out)
        self.assertNotIn("P02          ", out)
        self.assertIn("P1 ", out)

    def test_three_complete_phases_are_still_listed(self):
        self.add_complete_phases(3)
        _, out = run(self.root, "status")
        self.assertNotIn("phases COMPLETE", out)
        self.assertIn("P02", out)


class TestTaskIndex(FixtureCase):
    def test_lookup_follows_mutation_of_the_task_list(self):
        project = self.project
        self.assertEqual(project.task("T-TWO").id, "T-TWO")
        moved = project.tasks.pop(0)
        self.assertEqual(project.task("T-TWO").id, "T-TWO")
        self.assertIsNone(project.task("T-ONE"))
        project.tasks.insert(0, replace(moved, id="T-ZERO"))
        self.assertEqual(project.task("T-ZERO").id, "T-ZERO")
        project.tasks[0] = replace(moved, id="T-ONE")
        self.assertEqual(project.task("T-ONE").id, "T-ONE")
        self.assertIsNone(project.task("T-ZERO"))
        self.assertIsNone(project.task("T-MISSING"))


class TestLinearHelpers(unittest.TestCase):
    def test_section_locations_scale_linearly(self):
        def timed(n: int) -> float:
            text = "".join(f"## T-{i}: title\n- body line\n- another line\n\n" for i in range(n))
            started = time.perf_counter()
            found = archive.section_locations(text)
            elapsed = time.perf_counter() - started
            self.assertEqual(len(found), n)
            return elapsed
        timed(2000)  # warm up
        small, large = timed(4000), timed(32000)
        # Eight times the sections; a quadratic scan would take ~64 times as long.
        self.assertLess(large, small * 24 + 0.05)


if __name__ == "__main__":
    unittest.main()
