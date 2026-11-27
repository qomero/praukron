"""A capture that cannot be attributed is never recorded as verified (AC-T-SCOPE-ATTRIB-01).

Every test builds its own temporary Git repository; none touches the
repository the suite runs in.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, mutate, ref, scope, validate  # noqa: E402
from test_scope_capture import GIT, ScopeCase  # noqa: E402

FILES = {"src/app.py": "def run():\n    return 1\n", "src/other.py": "def theirs():\n    return 1\n",
         "README.md": "readme\n"}


def run(root: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = cli.main(["-C", str(root), *argv])
    return code, out.getvalue()


class AttributionCase(ScopeCase):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.dir / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)

    def commit_only(self, message: str, *paths: str) -> str:
        """Commit only `paths`, so the chronicle's own changes stay out of a side branch."""
        self.git("add", "--", *paths)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def merged_work(self) -> tuple[str, str, str]:
        """T-TWO claimed at `base`; its own commit on the first-parent line, then a merge of
        another line of work (src/other.py) that no claimed task holds."""
        base = self.repository(FILES)
        self.claim()
        self.git("checkout", "-q", "-b", "elsewhere")
        self.write("src/other.py", "def theirs():\n    return 2\n")
        self.commit_only("someone else's pull request", "src/other.py")
        self.git("checkout", "-q", "-")
        self.write("src/app.py", "def run():\n    return 2\n")
        own = self.commit_only("the task's own work", "src/app.py")
        self.git("merge", "-q", "--no-ff", "--no-edit", "elsewhere")
        merge = self.git("rev-parse", "HEAD")
        return base, own, merge

    def records(self, task: str = "T-TWO") -> list:
        return [r for r in self.project().references if r.task == task and r.scope == "observed"]

    def capture(self, *args: str) -> dict:
        code, out = run(self.root, "scope", "capture", *args)
        result = json.loads(out)
        self.assertEqual(code, 0, result)
        return result

    def json(self, *argv: str) -> dict:
        code, out = run(self.root, *argv)
        self.assertEqual(code, 0, out)
        return json.loads(out)

    def text(self, *argv: str) -> str:
        code, out = run(self.root, *argv)
        self.assertEqual(code, 0, out)
        return out


@unittest.skipIf(GIT is None, "git is not installed")
class TestOverlapIsNeverVerified(AttributionCase):
    """AC-T-SCOPE-ATTRIB-01-01."""

    def test_a_close_with_another_claimed_task_writes_unresolved_and_unverified(self):
        self.repository(FILES)
        self.claim("T-THREE", owner="agent/other")
        self.claim()
        self.write("src/app.py", "def run():\n    return 2\n")
        self.commit("work")
        self.close()
        found = self.records()
        self.assertEqual({r.symbol for r in found}, {None, "run"})
        for record in found:
            with self.subTest(record=record.id):
                self.assertEqual(record.lifecycle, "unresolved")
                self.assertEqual(record.verification, "unverified")
                self.assertIn("unresolved attribution: T-THREE was also claimed", record.evidence)

    def test_a_working_tree_capture_with_another_claimed_task_writes_unverified(self):
        base = self.repository(FILES)
        self.claim("T-THREE", owner="agent/other")
        self.claim()
        self.write("src/app.py", "def run():\n    return 3\n")
        self.capture("T-TWO", "--base", base)
        found = self.records()
        self.assertTrue(found)
        for record in found:
            with self.subTest(record=record.id):
                self.assertEqual((record.lifecycle, record.verification), ("unresolved", "unverified"))
                self.assertIn("T-THREE", record.evidence)

    def test_a_close_with_no_doubt_stays_verified(self):
        self.repository(FILES)
        self.claim()
        self.write("src/app.py", "def run():\n    return 2\n")
        self.close()
        for record in self.records():
            with self.subTest(record=record.id):
                self.assertIsNone(record.lifecycle)
                self.assertEqual(record.verification, "verified")


@unittest.skipIf(GIT is None, "git is not installed")
class TestMergedInWork(AttributionCase):
    """AC-T-SCOPE-ATTRIB-01-02, -03 and -04."""

    def test_files_and_symbols_merged_in_through_a_second_parent_are_unresolved(self):
        """AC-T-SCOPE-ATTRIB-01-02: no other task is claimed; Git's ancestry alone decides."""
        _, _, merge = self.merged_work()
        result = self.close()
        found = self.records()
        theirs = [r for r in found if r.path == "src/other.py"]
        self.assertEqual({r.symbol for r in theirs}, {None, "theirs"})
        for record in theirs:
            with self.subTest(record=record.id):
                self.assertEqual((record.lifecycle, record.verification), ("unresolved", "unverified"))
                self.assertIn(merge[:12], record.evidence)
                self.assertIn("merged in", record.evidence)
        mine = [r for r in found if r.path == "src/app.py"]
        self.assertEqual({r.symbol for r in mine}, {None, "run"})
        for record in mine:
            with self.subTest(record=record.id):
                self.assertIsNone(record.lifecycle)
                self.assertEqual(record.verification, "verified")
                self.assertNotIn("unresolved", record.evidence)
        self.assertEqual(result["capture"]["unresolved"], 2)

    def test_a_working_tree_change_beside_a_merge_stays_as_today(self):
        """AC-T-SCOPE-ATTRIB-01-02: a path changed only in the working tree is the task's."""
        self.merged_work()
        self.write("README.md", "uncommitted\n")
        self.close()
        record = next(r for r in self.records() if r.path == "README.md")
        self.assertIsNone(record.lifecycle)
        self.assertEqual(record.verification, "verified")

    def test_a_range_without_merges_reports_no_merges(self):
        """AC-T-SCOPE-ATTRIB-01-02: first-parent, non-merge commits stay as today."""
        self.repository(FILES)
        self.claim()
        self.write("src/app.py", "def run():\n    return 2\n")
        self.commit("one")
        self.write("src/other.py", "def theirs():\n    return 2\n")
        self.commit("two")
        result = self.close()
        self.assertEqual(result["capture"]["unresolved"], 0)
        self.assertNotIn("merges", result["capture"])
        self.assertNotIn("warning", result["capture"])
        self.assertTrue(all(r.verification == "verified" and r.lifecycle is None for r in self.records()))

    def test_merged_in_returns_the_merges_and_the_paths_they_brought(self):
        base, own, merge = self.merged_work()
        merges, paths = scope.merged_in(self.root, base, merge)
        self.assertEqual(merges, [merge])
        self.assertEqual(paths, {"src/other.py": [merge]})
        self.assertEqual(scope.merged_in(self.root, base, own), ([], {}))

    def test_a_named_capture_over_merges_is_verified(self):
        """AC-T-SCOPE-ATTRIB-01-03: the caller vouches for the range, merges or not."""
        base, _, merge = self.merged_work()
        result = self.capture("T-TWO", "--base", base, "--head", merge)
        self.assertIs(result["capture"]["rangeNamed"], True)
        self.assertEqual(result["capture"]["unresolved"], 0)
        self.assertNotIn("merges", result["capture"])
        self.assertNotIn("warning", result["capture"])
        found = self.records()
        self.assertEqual({r.path for r in found}, {"src/app.py", "src/other.py"})
        for record in found:
            with self.subTest(record=record.id):
                self.assertIsNone(record.lifecycle)
                self.assertEqual(record.verification, "verified")
                self.assertIn(scope.NAMED, record.evidence)

    def test_the_reply_carries_count_merges_and_one_warning_and_the_write_succeeds(self):
        """AC-T-SCOPE-ATTRIB-01-04."""
        _, _, merge = self.merged_work()
        result = self.close()
        self.assertTrue(result["ok"])
        self.assertEqual(self.task().status, "DONE")
        capture = result["capture"]
        self.assertEqual(capture["status"], "captured")
        self.assertEqual(capture["unresolved"], 2)
        self.assertEqual(capture["merges"], [merge[:12]])
        self.assertEqual(capture["mergesInvolved"], [merge[:12]])
        warning = capture["warning"]
        self.assertIsInstance(warning, str)
        self.assertEqual(warning.count(". "), 0, "one sentence")
        self.assertIn("not attributed", warning)
        self.assertIn("scope capture T-TWO --base <rev> --head <rev>", warning)
        self.assertIn("ref set", warning)

    def test_a_failing_merge_lookup_never_blocks_the_close(self):
        """AC-T-SCOPE-ATTRIB-01-04: a capture never blocks a write."""
        self.merged_work()
        with mock.patch.object(scope, "merged_in", side_effect=scope.GitUnavailable("boom")):
            result = self.close()
        self.assertTrue(result["ok"])
        self.assertEqual(self.task().status, "DONE")
        self.assertEqual(result["capture"]["status"], "skipped")


@unittest.skipIf(GIT is None, "git is not installed")
class TestViewsMarkUnresolved(AttributionCase):
    """AC-T-SCOPE-ATTRIB-01-05: every listing marks unresolved attribution and counts it."""

    def setUp(self):
        super().setUp()
        self.merged_work()
        self.close()
        self.theirs = {r.id for r in self.records() if r.path == "src/other.py"}
        self.mine = {r.id for r in self.records() if r.path == "src/app.py"}
        self.assertEqual(len(self.theirs), 2)

    def check_entries(self, entries: list) -> None:
        for item in entries:
            if item["id"] in self.theirs:
                self.assertEqual(item.get("attribution"), "unresolved", item)
            elif item["id"] in self.mine:
                self.assertNotIn("attribution", item)

    def test_refs_of_a_task(self):
        result = self.json("refs", "T-TWO", "--json")
        self.assertEqual(result["counts"]["unresolved"], 2)
        self.check_entries(result["references"])
        text = self.text("refs", "T-TWO")
        self.assertIn("(2 of them unresolved attribution)", text)
        self.assertIn("- UNRESOLVED ATTRIBUTION, not shown to be this task's change: src/other.py ·", text)
        self.assertIn("- src/app.py · modifies", text)

    def test_refs_of_a_file(self):
        result = self.json("refs", "src/other.py", "--json")
        self.assertEqual(result["unresolved"], 2)
        [task] = result["tasks"]
        self.assertIs(task["attributionUnresolved"], True)
        self.check_entries(result["references"])
        self.assertIs(self.json("refs", "src/app.py", "--json")["tasks"][0]["attributionUnresolved"], False)
        text = self.text("refs", "src/other.py")
        self.assertIn("2 of them unresolved attribution", text)
        self.assertIn("— UNRESOLVED ATTRIBUTION", text)

    def test_why(self):
        result = self.json("why", "src/other.py", "--json")
        self.assertEqual(result["unresolved"], 2)
        [task] = result["tasks"]
        self.assertIs(task["attributionUnresolved"], True)
        self.check_entries(task["references"])
        text = self.text("why", "src/other.py")
        self.assertIn("(2 of them unresolved attribution)", text)
        self.assertIn("unresolved attribution only", text)
        self.assertIn("UNRESOLVED ATTRIBUTION, not shown to be this task's change: src/other.py", text)
        mine = self.json("why", "src/app.py", "--json")
        self.assertEqual(mine["unresolved"], 0)
        self.assertIs(mine["tasks"][0]["attributionUnresolved"], False)

    def test_engine_map(self):
        result = self.json("engine", "map", "T-TWO", "--json")
        self.assertEqual(result["counts"]["attributionUnresolved"], 2)
        self.check_entries(result["references"])
        text = self.text("engine", "map", "T-TWO")
        self.assertIn("2 of them unresolved attribution", text)
        self.assertIn("UNRESOLVED ATTRIBUTION, not shown to be this task's change: src/other.py", text)
        artifact = self.json("engine", "map", "src/other.py", "--json")
        self.assertEqual(artifact["attributionUnresolved"], 2)
        self.assertIs(artifact["tasks"][0]["attributionUnresolved"], True)
        self.assertIn("— UNRESOLVED ATTRIBUTION", self.text("engine", "map", "src/other.py"))

    def test_context_and_retrieve(self):
        packet = self.json("context", "T-TWO")["references"]
        self.assertEqual(packet["counts"]["unresolved"], 2)
        self.check_entries(packet["first"])
        [listed] = self.json("retrieve", "T-TWO", "--json")["references"]
        self.assertEqual(listed["counts"]["unresolved"], 2)
        self.check_entries(listed["first"])
        text = self.text("retrieve", "T-TWO")
        self.assertIn("(2 of them unresolved attribution)", text)
        self.assertIn("- UNRESOLVED ATTRIBUTION, not shown to be this task's change: src/other.py", text)


@unittest.skipIf(GIT is None, "git is not installed")
class TestValidateWarnsOnVerifiedUnresolved(AttributionCase):
    """AC-T-SCOPE-ATTRIB-01-06."""

    def findings(self) -> list:
        return [f for f in validate.check(self.project()) if f.code == "unattributed-verified-references"]

    def make_old_records(self) -> list:
        """What a capture wrote before this change: unresolved, yet verified."""
        self.merged_work()
        self.close()
        text = self.code.read_text(encoding="utf-8")
        self.code.write_text(text.replace("- Verification: unverified", "- Verification: verified"),
                             encoding="utf-8")
        return [r for r in self.records() if r.lifecycle == "unresolved"]

    def test_one_warning_per_task_with_count_and_the_way_to_settle(self):
        old = self.make_old_records()
        self.assertEqual(len(old), 2)
        [finding] = self.findings()
        self.assertEqual(finding.severity, "warning")
        self.assertIn("T-TWO: 2 current references", finding.message)
        self.assertIn("scope capture T-TWO --base <rev> --head <rev>", finding.message)
        self.assertIn("ref set", finding.message)
        self.assertFalse([f for f in validate.check(self.project()) if f.severity == "error"])

    def test_a_withdrawn_or_settled_record_no_longer_counts(self):
        old = self.make_old_records()
        for record in old:
            self.withdraw(record.id)
        self.assertEqual(self.findings(), [])

    def test_a_chronicle_without_such_records_gets_no_finding(self):
        self.merged_work()
        self.close()
        self.assertEqual(self.findings(), [])

    def withdraw(self, reference_id: str) -> dict:
        result = ref.set_reference(self.root, {
            "id": reference_id, "expectRevision": mutate.section(self.code.read_bytes(), reference_id).revision,
            "recordedBy": "agent/builder", "source": "agent", "withdraw": {"reason": "another task's change"}})
        self.assertTrue(result["ok"], result)
        return result


@unittest.skipIf(GIT is None, "git is not installed")
class TestSettling(AttributionCase):
    """AC-T-SCOPE-ATTRIB-01-07: `ref set` and a named capture settle an unresolved reference."""

    def setUp(self):
        super().setUp()
        self.base, self.own, self.merge = self.merged_work()
        self.close()
        self.file = next(r.id for r in self.records() if r.path == "src/other.py" and r.symbol is None)

    def section_revision(self, reference_id: str) -> str:
        return mutate.section(self.code.read_bytes(), reference_id).revision

    def set(self, reference_id: str, change: dict, **extra) -> dict:
        return ref.set_reference(self.root, {"id": reference_id, "expectRevision": self.section_revision(reference_id),
                                             "recordedBy": "agent/coordinator", **extra, **change})

    def test_confirming_with_evidence_appends_a_successor_and_keeps_the_record(self):
        before = mutate.section(self.code.read_bytes(), self.file).data
        result = self.set(self.file, {"set": {"lifecycle": "active", "verification": "verified",
                                              "evidence": "the task's pull request made this change"}})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["replaces"], self.file)
        self.assertEqual(mutate.section(self.code.read_bytes(), self.file).data, before)
        successor = next(r for r in self.project().references if r.replaces == self.file)
        self.assertEqual((successor.lifecycle, successor.verification), ("active", "verified"))
        self.assertEqual(successor.provenance, "git_diff")
        self.assertEqual(successor.path, "src/other.py")
        history = ref.history(self.root, successor.id)
        self.assertEqual([h["id"] for h in history], [self.file, successor.id])
        listed = self.json("refs", "T-TWO", "--json")
        self.assertEqual(listed["counts"]["unresolved"], 1)
        entry = next(r for r in listed["references"] if r["id"] == successor.id)
        self.assertNotIn("attribution", entry)
        why = self.json("why", "src/other.py#theirs", "--json")
        self.assertIs(why["tasks"][0]["attributionUnresolved"], True)
        self.assertIs(self.json("why", "src/other.py", "--json")["tasks"][0]["attributionUnresolved"], False)

    def test_settling_needs_evidence(self):
        result = self.set(self.file, {"set": {"lifecycle": "active", "verification": "verified"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "settle-without-evidence")

    def test_an_unresolved_reference_is_never_set_verified_in_place(self):
        result = self.set(self.file, {"set": {"verification": "verified"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "unresolved-attribution")

    def test_withdrawing_keeps_the_record_and_the_views_follow(self):
        result = self.set(self.file, {"withdraw": {"reason": "another pull request's change"}}, source="agent")
        self.assertTrue(result["ok"], result)
        listed = self.json("refs", "T-TWO", "--json")
        self.assertEqual(listed["counts"]["unresolved"], 1)
        self.assertIn(self.file, {r["id"] for r in listed["references"]})
        why = self.json("why", "src/other.py", "--json")
        self.assertEqual(why["unresolved"], 1)
        self.assertIn(self.file, {r["id"] for r in why["notCurrent"]})

    def test_a_named_capture_settles_what_it_records_and_names_the_rest(self):
        """The named capture of the warning replaces the matching unresolved records."""
        siblings = self.records()
        unresolved = sorted(r.id for r in siblings if r.lifecycle == "unresolved")
        result = self.capture("T-TWO", "--base", self.base, "--head", self.own)
        # The task's own range does not hold src/other.py: nothing settles it but ref set.
        self.assertNotIn("settled", result["capture"])
        self.assertEqual(result["capture"]["stillUnresolved"], unresolved)
        result = self.capture("T-TWO", "--base", self.base, "--head", self.merge)
        self.assertEqual(sorted(result["capture"]["settled"]), unresolved)
        self.assertNotIn("stillUnresolved", result["capture"])
        self.assertNotIn("unresolved", self.json("refs", "T-TWO", "--json")["counts"])
        for reference_id in unresolved:
            self.assertEqual(ref.history(self.root, next(
                r.id for r in self.project().references if r.replaces == reference_id))[0]["id"], reference_id)


if __name__ == "__main__":
    unittest.main()
