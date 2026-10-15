"""The pure helpers of the write benchmark (tests/bench_writes.py)."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_writes as bench


class Helpers(unittest.TestCase):
    def test_append_counts_only_the_added_text(self):
        edit = bench.changed(b"# T\n\n## A\nx\n", b"# T\n\n## A\nx\n\n## B\ny\n")
        self.assertEqual((edit["kind"], edit["new"], edit["old"]), ("append", b"\n## B\ny\n", b""))

    def test_in_place_edit_counts_only_changed_lines(self):
        before = b"## A\n- Status: TODO\n- Module: M\n- Evidence: none\n"
        after = b"## A\n- Status: DONE\n- Module: M\n- Evidence: ok\n"
        edit = bench.changed(before, after)
        self.assertEqual(edit["kind"], "replace")
        self.assertEqual(len(edit["old"]), len(b"- Status: TODO\n") + len(b"- Evidence: none\n"))
        self.assertEqual(len(edit["new"]), len(b"- Status: DONE\n") + len(b"- Evidence: ok\n"))

    def test_targeted_read_is_a_section(self):
        data = b"# T\n\n## A\none\n\n## B\ntwo\n\n## C\nthree\n"
        self.assertEqual(bench.last_section(data), b"## C\nthree\n")
        self.assertEqual(bench.section_at(data, data.index(b"two")), b"## B\ntwo\n\n")

    def test_no_ratio_over_a_zero_baseline(self):
        self.assertEqual(bench.ratio(100, 0), "n/a")
        self.assertEqual(bench.ratio(0, 5), "n/a")
        self.assertEqual(bench.ratio(100, 50), "2.0x")

    def test_token_estimate_rounds_up(self):
        self.assertEqual([bench.tokens(n) for n in (0, 1, 4, 5)], [0, 1, 1, 2])


class FailureReporting(unittest.TestCase):
    """A failed preparation or command is reported first and fails the run."""

    def test_failed_archive_is_returned_not_discarded(self):
        class Stub:
            root = Path(".")
            stderr = "boom"

            def run(self, *argv, **kwargs):
                return 1, "", 0.0

        phases = [SimpleNamespace(id="P0", status="COMPLETE"), SimpleNamespace(id="P1", status="ACTIVE")]
        with mock.patch.object(bench.compiler, "load", return_value=SimpleNamespace(phases=phases)):
            moved, failures = bench.Chronicle.archive_complete_phases(Stub())
        self.assertEqual(moved, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("archive P0: exit 1: boom", failures[0])

    def test_no_complete_phase_is_a_failure(self):
        class Stub:
            root = Path(".")

        with mock.patch.object(bench.compiler, "load", return_value=SimpleNamespace(phases=[])):
            moved, failures = bench.Chronicle.archive_complete_phases(Stub())
        self.assertEqual((moved, len(failures)), ([], 1))

    def test_main_withholds_the_after_archiving_run_and_exits_nonzero(self):
        class Fake:
            def __init__(self, source, parent, name):
                self.root, self.name = Path(parent) / name, name

            def archive_complete_phases(self):
                return [], ["archive P0: exit 1: boom"] if self.name == "archived" else []

            def size(self):
                return {"working_bytes": 1, "archive_bytes": 0, "TASKS.md": 1, "ACCEPTANCE.md": 1, "JOURNAL.md": 1}

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "src"
            (source / "chronicle").mkdir(parents=True)
            report = Path(directory) / "out.md"
            with mock.patch.object(bench, "Chronicle", Fake), mock.patch.object(bench, "active_module", return_value="M"), \
                    mock.patch.object(bench, "measure", return_value=([], [])), \
                    mock.patch.object(bench, "gate", return_value=[]), \
                    mock.patch("sys.stdout", io.TextIOWrapper(io.BytesIO())):
                code = bench.main(["--source", str(source), "--markdown", str(report)])
            text = report.read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertLess(text.index("archive P0: exit 1: boom"), text.index("###"))
        self.assertNotIn("### After archiving", text)
        self.assertIn("Archiving failed", text)


if __name__ == "__main__":
    unittest.main()
