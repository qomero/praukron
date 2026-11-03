"""AC-T-DASH-SCHED-01-01: a task schedule reads as labelled text, not raw JSON.

The task dialog is built by the page's script, so its helper runs under node
against synthetic schedules; the Audit record is rendered server side and is
checked in both languages."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard, i18n  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402

NODE = shutil.which("node")


def schedule_html(page: str, schedules: list) -> list[str]:
    """What the page's own scheduleHtml returns for each schedule."""
    if NODE is None:
        raise unittest.SkipTest("node is not installed")
    esc = re.search(r"^const esc = .*?\n  /\[&<>.*?\)\);\n", page, re.S | re.M)[0]
    helper = re.search(r"^function scheduleHtml\(schedule\) \{.*?^\}\n", page, re.S | re.M)[0]
    script = (
        "const protectedText = v => '\ue000' + String(v) + '\ue001';\n" + esc + helper
        + f"console.log(JSON.stringify({json.dumps(schedules)}.map(scheduleHtml)));"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def plain(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", markup.replace(i18n.START, "").replace(i18n.END, ""))).strip()


class TestScheduleText(FixtureCase):
    def render(self, lang: str | None = None) -> str:
        compiled = compiler.as_json(self.project)
        return dashboard.render(self.project, analytics.report(self.project), compiled, lang=lang)

    def record(self, page: str, task: str) -> str:
        return page.split(f'id="task-record-{task}"', 1)[1].split("</details>", 1)[0]

    def test_dialog_reads_start_and_estimate_labelled_not_json(self) -> None:
        page = self.render()
        self.assertNotIn("JSON.stringify(task.schedule)", page)
        shown, = schedule_html(page, [{"start": "2026-10-01", "estimate": "3d"}])
        self.assertEqual(plain(shown), "Start 2026-10-01 · Estimate 3d")
        self.assertNotIn("{", shown)
        self.assertNotIn("&quot;", shown)

    def test_dialog_shows_every_supported_field_and_hides_absent_ones(self) -> None:
        shown = schedule_html(self.render(), [
            {"start": "2026-10-01", "end": "2026-10-09", "estimate": "3d"},
            {"end": "2026-10-09"},
            {"start": "2026-10-01"},
            {"estimate": "3d"},
        ])
        self.assertEqual([plain(s) for s in shown], [
            "Start 2026-10-01 · End 2026-10-09 · Estimate 3d", "End 2026-10-09",
            "Start 2026-10-01", "Estimate 3d"])
        self.assertNotIn("Start", shown[1])
        self.assertNotIn("Estimate", shown[1])

    def test_dialog_keeps_unscheduled_when_nothing_is_recorded(self) -> None:
        for none in (None, {}):
            with self.subTest(schedule=none):
                shown, = schedule_html(self.render(), [none])
                self.assertEqual(plain(shown), "Unscheduled")

    def test_dialog_escapes_schedule_values(self) -> None:
        shown, = schedule_html(self.render(), [{"estimate": "<b>3d</b>"}])
        self.assertNotIn("<b>", shown)
        self.assertIn("&lt;b&gt;3d", shown)

    def test_dialog_uses_the_helper_for_the_schedule_row(self) -> None:
        self.assertIn("<dt>Schedule</dt><dd>${scheduleHtml(task.schedule)}</dd>", self.render())

    def test_audit_record_reads_the_same_labels(self) -> None:
        self.assertEqual(self.project.task("T-THREE").schedule.as_json(), {"start": "2026-10-01", "estimate": "3d"})
        page = self.render()
        self.assertIn("<p>Schedule: Start 2026-10-01 · Estimate 3d</p>", self.record(page, "T-THREE"))
        self.assertNotIn("&quot;start&quot;", page)
        self.assertIn("<p>Schedule: No schedule recorded</p>", self.record(page, "T-ONE"))

    def test_audit_record_shows_end_and_hides_absent_fields(self) -> None:
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO",
                     "## T-TWO: Build on it\n- Schedule: start=2026-10-01 end=2026-10-09\n- Status: TODO")
        record = self.record(self.render(), "T-TWO")
        self.assertIn("<p>Schedule: Start 2026-10-01 · End 2026-10-09</p>", record)
        self.assertNotIn("Estimate", record)

    def test_vietnamese_labels_keep_recorded_values(self) -> None:
        messages = i18n.CATALOGUES["vi"]["messages"]
        self.assertEqual([messages[k] for k in ("Start", "End", "Estimate")], ["Bắt đầu", "Kết thúc", "Ước tính"])
        record = self.record(self.render("vi"), "T-THREE")
        self.assertIn("<p>Lịch trình: Bắt đầu 2026-10-01 · Ước tính 3d</p>", record)
        self.assertNotIn("Start", record)
        self.assertNotIn("Estimate", record)


if __name__ == "__main__":
    unittest.main()
