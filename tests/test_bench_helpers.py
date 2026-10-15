"""The pure helpers of the write benchmark (tests/bench_writes.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
