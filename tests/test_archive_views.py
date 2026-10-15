"""Event order agrees in every view, and validate names paths restore would refuse (PR #30 review)."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth_chronicle  # noqa: E402
from praukron import analytics, archive, compile as compiler, dashboard, layout, retrieve, validate  # noqa: E402
from praukron.model import event_order  # noqa: E402

TASK = "T-OPS-06"


def event_block(identifier, title, task, time=None):
    lines = ["", f"## {identifier}: {title}", "- Type: command", f"- Task: {task}"]
    if time:
        lines.append(f"- Time: {time}")
    return chr(10).join([*lines, "- Outcome: success", f"- Affects: {TASK}", ""])


class EventOrderAcrossViews(unittest.TestCase):
    def build(self, scratch, blocks):
        root = synth_chronicle.build(Path(scratch).resolve())
        trace = root / layout.AUTHORITY_DIR / "TRACE.md"
        trace.write_text(trace.read_text(encoding="utf-8") + "".join(blocks), encoding="utf-8")
        return root

    def views(self, root):
        project = compiler.load(root)
        report = analytics.report(project)
        related = [e.id for e in sorted(project.events, key=lambda e: event_order(e.id, e.time))
                   if e.task == TASK or TASK in e.affects]
        pack = retrieve.retrieve(project, report, root, TASK, budget=0)
        page = dashboard.render(project, report, compiler.as_json(project))
        card = re.search(rf'id="op-{TASK}".*?latest (Extra [0-9]+|Tied [a-z]+)', page, re.S)
        timeline = page[page.index('id="timeline"'):]
        return {
            "expected": related,
            "loaded": [e.id for e in project.events if e.id in set(related)],
            "context": analytics.context(project, TASK)["events"]["recent"],
            "retrieve": [i.id for i in pack.items if i.id in set(related)],
            "timeline": [i for i in re.findall(r"<code>(EV-[A-Za-z0-9]+)</code>", timeline) if i in set(related)],
            "latest": card.group(1) if card else None,
        }

    def check(self, views, latest_title):
        expected = views["expected"]
        self.assertEqual(views["loaded"], expected)
        self.assertEqual(views["context"], expected[-5:])
        self.assertEqual(views["retrieve"][-5:], expected[-5:])
        self.assertEqual(views["timeline"][:len(expected)], expected)
        self.assertEqual(views["latest"], latest_title)

    def run_stages(self, blocks, latest_title, last_five):
        with tempfile.TemporaryDirectory(prefix="praukron-views-") as scratch:
            root = self.build(scratch, blocks)
            first = self.views(root)
            self.assertEqual(first["expected"][-5:], last_five)
            self.check(first, latest_title)
            plan = archive.preview(root, "P1")
            archive.archive(root, "P1", digest=plan.digest)
            self.assertEqual(validate.errors(validate.check(compiler.load(root))), [])
            self.assertEqual(self.views(root), first)
            archive.restore(root, "P1")
            self.assertEqual(self.views(root), first)

    def test_undated_ids_crossing_nine_and_ten_agree_everywhere(self):
        blocks = [event_block(f"EV-X{i}", f"Extra {i}", "T-P1-01" if i % 2 == 0 else "T-P6-01")
                  for i in range(12)]
        self.run_stages(blocks, "Extra 11", [f"EV-X{i}" for i in range(7, 12)])

    def test_tied_times_fall_back_to_the_id_number(self):
        stamp = "2026-10-05T10:00:00Z"
        blocks = [event_block("EV-T10", "Tied ten", "T-P1-01", stamp),
                  event_block("EV-T9", "Tied nine", "T-P6-01", stamp),
                  event_block("EV-T2", "Tied two", "T-P1-01", stamp)]
        self.run_stages(blocks, "Tied ten", ["EV-T2", "EV-T9", "EV-T10"])


class UnsupportedArchivePaths(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="praukron-paths-")
        self.addCleanup(self.scratch.cleanup)
        self.root = synth_chronicle.build(Path(self.scratch.name).resolve())
        plan = archive.preview(self.root, "P1")
        archive.archive(self.root, "P1", digest=plan.digest)
        self.directory = self.root / layout.AUTHORITY_DIR / "archive" / "P1"

    def findings(self):
        return [f for f in validate.check(compiler.load(self.root)) if f.code == "unsupported-archive-path"]

    def test_a_clean_archive_has_no_such_finding(self):
        self.assertEqual(self.findings(), [])

    def test_other_extensions_nested_files_and_directories_are_warnings_naming_restore(self):
        (self.directory / "notes.txt").write_text("x", encoding="utf-8")
        (self.directory / "nested").mkdir()
        (self.directory / "nested" / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        (self.directory / "empty.md").mkdir()
        found = self.findings()
        self.assertEqual(sorted(f.where for f in found),
                         ["archive/P1/empty.md", "archive/P1/nested", "archive/P1/notes.txt"])
        self.assertTrue(all(f.severity == "warning" and "restore refuses" in f.message for f in found))
        with self.assertRaises(archive.ArchiveError) as refused:
            archive.restore(self.root, "P1", dry_run=True)
        self.assertEqual(refused.exception.code, "invalid-archive")

    def test_known_record_files_and_the_order_file_are_never_reported(self):
        self.assertTrue((self.directory.parent / "JOURNAL.order.json").is_file())
        self.assertTrue(any(self.directory.glob("*.md")))
        self.assertEqual(self.findings(), [])


if __name__ == "__main__":
    unittest.main()
