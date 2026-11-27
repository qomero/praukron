"""A capture attributes only the commits this checkout made (AC-T-SCOPE-REFLOG-01).

Work of others that reaches the first-parent line without a merge commit (a
fast-forward, a pull, a rebase onto it, a reset or a checkout) is told apart
by the checkout's own HEAD reflog. Every test builds its own temporary Git
repositories; none touches the repository the suite runs in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import layout, scope  # noqa: E402
from test_scope_attribution import FILES, AttributionCase  # noqa: E402
from test_scope_capture import GIT  # noqa: E402

THEIRS = "def theirs():\n    return 2\n"


class ReflogCase(AttributionCase):
    def foreign(self, path: str = "src/other.py", text: str = THEIRS, parent: str = "HEAD",
                branch: str = "upstream") -> str:
        """A commit made elsewhere: written with plumbing onto `branch`, so no HEAD reflog entry creates it."""
        index = self.dir / "foreign-index"
        environment = {**os.environ, "GIT_INDEX_FILE": str(index)}

        def plumb(*args: str, stdin: bytes | None = None) -> str:
            done = subprocess.run(["git", "-C", str(self.root), *args], input=stdin, capture_output=True,
                                  check=True, env=environment)
            return done.stdout.decode("utf-8").strip()

        plumb("read-tree", parent)
        blob = plumb("hash-object", "-w", "--stdin", stdin=text.encode("utf-8"))
        plumb("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        tree = plumb("write-tree")
        commit = plumb("commit-tree", tree, "-p", self.git("rev-parse", parent), "-m", f"someone else's {path}")
        self.git("update-ref", f"refs/heads/{branch}", commit)
        index.unlink()
        return commit

    def own(self, path: str = "src/app.py", text: str = "def run():\n    return 2\n") -> str:
        self.write(path, text)
        return self.commit_only(f"own work on {path}", path)

    def point(self, root: Path) -> None:
        """Work in another checkout of the project from here on."""
        self.root = root
        self.authority = root / layout.AUTHORITY_DIR
        self.tasks = self.authority / "TASKS.md"
        self.code = self.authority / "CODE.md"

    def by_path(self) -> dict[str, list]:
        found: dict[str, list] = {}
        for record in self.records():
            found.setdefault(record.path, []).append(record)
        return found

    def assert_unresolved(self, records: list, *needles: str) -> None:
        self.assertTrue(records)
        for record in records:
            with self.subTest(record=record.id):
                self.assertEqual((record.lifecycle, record.verification), ("unresolved", "unverified"))
                for needle in needles:
                    self.assertIn(needle, record.evidence)

    def assert_verified(self, records: list) -> None:
        self.assertTrue(records)
        for record in records:
            with self.subTest(record=record.id):
                self.assertIsNone(record.lifecycle)
                self.assertEqual(record.verification, "verified")
                self.assertNotIn("unresolved attribution", record.evidence)


@unittest.skipIf(GIT is None, "git is not installed")
class TestCommitsThatArrived(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-01."""

    def test_a_fast_forwarded_commit_is_unresolved_and_names_how_it_arrived(self):
        self.repository(FILES)
        self.claim()
        self.own()
        theirs = self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        self.own("README.md", "own again\n")
        self.close()
        found = self.by_path()
        self.assertEqual({r.symbol for r in found["src/other.py"]}, {None, "theirs"})
        self.assert_unresolved(found["src/other.py"], theirs[:12], "arrived by `merge`",
                               "does not show as made here")
        self.assert_verified(found["src/app.py"])
        self.assert_verified(found["README.md"])

    def test_a_commit_brought_by_reset_or_checkout_names_that_action(self):
        for move, action in ((("reset", "-q", "--keep", "upstream"), "reset"),
                             (("checkout", "-q", "upstream"), "checkout")):
            with self.subTest(move=move[0]):
                self.setUp()
                self.repository(FILES)
                self.claim()
                theirs = self.foreign()
                self.git(*move)
                self.close()
                found = self.by_path()
                self.assert_unresolved(found["src/other.py"], theirs[:12], f"arrived by `{action}`")

    def test_a_path_changed_only_here_or_only_in_the_working_tree_stays_verified(self):
        self.repository(FILES)
        self.claim()
        self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        self.own()
        self.write("README.md", "uncommitted\n")
        self.close()
        found = self.by_path()
        self.assert_verified(found["src/app.py"])
        self.assert_verified(found["README.md"])

    def test_a_path_changed_both_here_and_elsewhere_is_unresolved(self):
        self.repository(FILES)
        self.claim()
        theirs = self.foreign("src/app.py", "def run():\n    return 9\n")
        self.git("merge", "-q", "--ff-only", "upstream")
        self.own("src/app.py", "def run():\n    return 10\n")
        self.close()
        self.assert_unresolved(self.by_path()["src/app.py"], theirs[:12])

    def test_a_range_of_commits_made_here_reports_nothing(self):
        self.repository(FILES)
        self.claim()
        self.own()
        self.git("commit", "-q", "--amend", "--no-edit")
        self.git("cherry-pick", "--allow-empty", self.foreign("src/other.py", THEIRS, "HEAD~0", "picked"))
        result = self.close()
        self.assertNotIn("notMadeHere", result["capture"])
        self.assertEqual(result["capture"]["unresolved"], 0)
        self.assert_verified(self.records())


@unittest.skipIf(GIT is None, "git is not installed")
class TestReplayedByARebase(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-02."""

    def rebased(self, *command: str) -> tuple[str, str]:
        self.repository(FILES)
        self.claim()
        self.own()
        onto_parent = self.foreign("README.md", "someone else's readme\n", "HEAD~1", "upstream")
        onto = self.foreign("src/other.py", THEIRS, "upstream", "upstream")
        self.git("-c", "rebase.autoStash=true", *command)
        self.own("src/third.py", "def third():\n    return 3\n")
        return onto_parent, onto

    def check(self, onto_parent: str, onto: str, step: str) -> None:
        found = self.by_path()
        self.assert_verified(found["src/app.py"])
        self.assert_verified(found["src/third.py"])
        self.assert_unresolved(found["src/other.py"], onto[:12], f"arrived by `{step}`")
        self.assert_unresolved(found["README.md"], onto_parent[:12], f"arrived by `{step}`")

    def test_a_pull_rebase_replays_the_tasks_commits_and_not_what_it_pulled(self):
        onto_parent, onto = self.rebased("pull", "-q", "--rebase", ".", "upstream")
        self.close()
        self.check(onto_parent, onto, "pull")

    def test_a_rebase_replays_the_tasks_commits_and_not_what_it_rebased_onto(self):
        onto_parent, onto = self.rebased("rebase", "-q", "upstream")
        own = self.git("rev-parse", "HEAD~1")
        self.close()
        self.check(onto_parent, onto, "rebase")
        entries = scope.reflog(self.root)
        self.assertTrue(any(e.commit == own and e.action == "rebase (pick)" for e in entries))

    def test_the_replaying_steps_count_as_created_here(self):
        for action in ("rebase (pick)", "rebase -i (reword)", "rebase (edit)", "rebase (squash)",
                       "rebase (fixup)", "rebase (continue)", "pull --rebase (pick)", "pull -q --rebase . main (pick)",
                       "commit", "commit (initial)", "commit (amend)", "commit (merge)", "cherry-pick", "revert", "am"):
            with self.subTest(action=action):
                self.assertTrue(scope.creating(action))
        for action in ("rebase (start)", "rebase (finish)", "rebase", "pull --rebase (start)", "pull", "merge main",
                       "reset", "checkout", "clone", "branch", "", "commit (unknown)", "rebased (pick)",
                       "Zusammenführen", "stash"):
            with self.subTest(action=action):
                self.assertFalse(scope.creating(action))


@unittest.skipIf(GIT is None, "git is not installed")
class TestReflogCannotAccount(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-03: nothing is guessed in either direction."""

    def assert_unshown(self, *paths: str) -> None:
        found = self.by_path()
        for path in paths:
            self.assert_unresolved(found[path], scope.UNSHOWN)

    def test_an_expired_reflog_accounts_for_nothing(self):
        self.repository(FILES)
        self.claim()
        self.own()
        self.git("reflog", "expire", "--expire=now", "--all")
        self.write("README.md", "uncommitted\n")
        result = self.close()
        self.assert_unshown("src/app.py")
        self.assert_verified(self.by_path()["README.md"])
        self.assertIn("has no entries", result["capture"]["notMadeHereInvolved"][0]["how"])

    def test_reflogs_turned_off_account_for_nothing(self):
        self.git("init", "-q")
        self.git("config", "core.logAllRefUpdates", "false")
        self.repository(FILES)
        self.claim()
        self.own()
        self.close()
        self.assert_unshown("src/app.py")

    def test_a_truncated_reflog_does_not_account_for_what_came_before_its_first_entry(self):
        self.repository(FILES)
        self.claim()
        own = self.own()
        self.git("reflog", "expire", "--expire=now", "--all")
        self.git("checkout", "-q", "--detach")
        self.git("checkout", "-q", "-")
        self.close()
        found = self.by_path()
        self.assert_unresolved(found["src/app.py"], own[:12], scope.UNSHOWN, "first entry")

    def test_a_fresh_clone_does_not_account_for_work_made_before_it(self):
        self.repository(FILES)
        self.claim()
        self.own()
        self.commit("chronicle and work")
        clone = self.dir / "clone"
        subprocess.run(["git", "clone", "-q", str(self.root), str(clone)], check=True, capture_output=True)
        self.point(clone)
        self.close()
        self.assert_unresolved(self.by_path()["src/app.py"], scope.UNSHOWN, "`clone`")

    def test_a_reflog_that_cannot_be_read_accounts_for_nothing_and_the_write_succeeds(self):
        """Also AC-T-SCOPE-REFLOG-01-05: a failure to read the reflog never blocks a write."""
        for error in (scope.GitUnavailable("git log failed: boom"), RuntimeError("boom")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.repository(FILES)
                self.claim()
                self.own()
                with mock.patch.object(scope, "reflog", side_effect=error):
                    result = self.close()
                self.assertTrue(result["ok"])
                self.assertEqual(self.task().status, "DONE")
                self.assertEqual(result["capture"]["status"], "captured")
                self.assert_unresolved(self.by_path()["src/app.py"], "could not be read")


@unittest.skipIf(GIT is None, "git is not installed")
class TestNoActionArgumentIsRecorded(ReflogCase):
    """A chronicle never records a secret: only the kind of arrival, never the reflog action's arguments."""

    SECRET = "s3cr3t-t0ken"

    def test_a_credential_in_the_reflog_action_appears_nowhere(self):
        self.repository(FILES)
        self.claim()
        self.own()
        theirs = self.foreign()
        url = f"https://builder:{self.SECRET}@git.example.invalid/private/repo.git"
        environment = {**os.environ, "GIT_REFLOG_ACTION": f"pull --ff-only {url} feature/private-branch"}
        subprocess.run(["git", "-C", str(self.root), "merge", "-q", "--ff-only", "upstream"],
                       check=True, capture_output=True, env=environment)
        self.assertIn(self.SECRET, self.git("reflog", "-1", "--format=%gs"))
        result = self.close()
        written = self.code.read_text(encoding="utf-8")
        reply = json.dumps(result)
        for text in (written, reply):
            for leak in (self.SECRET, "git.example.invalid", "private-branch", "--ff-only"):
                self.assertNotIn(leak, text)
        self.assertEqual(result["capture"]["notMadeHereInvolved"],
                         [{"commit": theirs[:12], "how": "arrived by `pull`"}])
        self.assert_unresolved(self.by_path()["src/other.py"], theirs[:12], "arrived by `pull`")

    def test_the_kind_comes_from_a_fixed_vocabulary(self):
        for action, expected in (("pull -q --rebase https://u:t@h/r (start)", "pull"), ("fetch", "fetch"),
                                 ("merge origin/main", "merge"), ("reset", "reset"), ("checkout", "checkout"),
                                 ("switch", "checkout"), ("rebase -i (start)", "rebase"), ("clone", "clone"),
                                 ("", "other"), ("my-tool https://u:t@h/r", "other")):
            with self.subTest(action=action):
                self.assertEqual(scope.kind(action), expected)


@unittest.skipIf(GIT is None, "git is not installed")
class TestANamedRangeIsVouchedFor(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-04."""

    def test_a_named_capture_does_not_consult_the_reflog(self):
        base = self.repository(FILES)
        self.claim()
        self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        head = self.own()
        with mock.patch.object(scope, "reflog", side_effect=AssertionError("the reflog was read")) as read:
            result = self.capture("T-TWO", "--base", base, "--head", head)
        read.assert_not_called()
        self.assertIs(result["capture"]["rangeNamed"], True)
        self.assertNotIn("notMadeHere", result["capture"])
        self.assertNotIn("warning", result["capture"])
        for record in self.records():
            with self.subTest(record=record.id):
                self.assertIsNone(record.lifecycle)
                self.assertEqual(record.verification, "verified")
                self.assertIn(scope.NAMED, record.evidence)

    def test_a_working_tree_capture_does_consult_it(self):
        base = self.repository(FILES)
        self.claim()
        theirs = self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        result = self.capture("T-TWO", "--base", base)
        self.assertEqual(result["capture"]["notMadeHere"], [theirs[:12]])
        self.assert_unresolved(self.by_path()["src/other.py"], theirs[:12])


@unittest.skipIf(GIT is None, "git is not installed")
class TestTheReply(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-05."""

    def test_the_reply_names_the_commits_beside_the_merges_with_one_warning(self):
        base = self.repository(FILES)
        self.claim()
        self.git("checkout", "-q", "-b", "elsewhere")
        self.write("src/merged.py", "def merged():\n    return 1\n")
        self.commit_only("someone else's merged work", "src/merged.py")
        self.git("checkout", "-q", "-")
        self.own()
        self.git("merge", "-q", "--no-ff", "--no-edit", "elsewhere")
        merge = self.git("rev-parse", "HEAD")
        theirs = self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        quiet = self.foreign("src/quiet.txt", "q\n", "HEAD", "upstream")
        self.git("merge", "-q", "--ff-only", "upstream")
        self.git("rm", "-q", "--cached", "src/quiet.txt")
        self.git("commit", "-q", "-m", "untrack it")
        (self.root / "src/quiet.txt").unlink()
        result = self.close()
        self.assertTrue(result["ok"])
        self.assertEqual(self.task().status, "DONE")
        capture = result["capture"]
        self.assertEqual(capture["mergesInvolved"], [merge[:12]])
        self.assertEqual(capture["notMadeHere"], [theirs[:12], quiet[:12]])
        self.assertEqual(capture["notMadeHereInvolved"],
                         [{"commit": theirs[:12], "how": "arrived by `merge`"}])
        self.assertEqual(capture["unresolved"], 4)
        warning = capture["warning"]
        self.assertEqual(warning.count(". "), 0, "one sentence")
        self.assertIn("merged-in commit", warning)
        self.assertIn("HEAD reflog does not show as made here", warning)
        self.assertIn("scope capture T-TWO --base <rev> --head <rev>", warning)
        self.assertNotIn(base[:12], capture["notMadeHere"])
        self.assert_verified(self.by_path()["src/app.py"])

    def test_a_failing_commit_lookup_never_blocks_the_close(self):
        self.repository(FILES)
        self.claim()
        self.foreign()
        self.git("merge", "-q", "--ff-only", "upstream")
        with mock.patch.object(scope, "not_made_here", side_effect=scope.GitUnavailable("boom")):
            result = self.close()
        self.assertTrue(result["ok"])
        self.assertEqual(self.task().status, "DONE")
        self.assertEqual(result["capture"]["status"], "skipped")


@unittest.skipIf(GIT is None, "git is not installed")
class TestALinkedWorktree(ReflogCase):
    """AC-T-SCOPE-REFLOG-01-06."""

    def test_the_worktrees_own_head_reflog_is_read(self):
        self.repository(FILES)
        main = self.root
        branch = self.git("symbolic-ref", "--short", "HEAD")
        worktree = self.dir / "worktree"
        self.git("worktree", "add", "-q", "-b", "work", str(worktree))
        self.point(worktree)
        self.claim()
        own = self.own()
        # A commit made in the main checkout: its own reflog shows it as made there, the worktree's does not.
        self.point(main)
        self.git("merge", "-q", "--ff-only", "work")
        theirs = self.own("src/other.py", THEIRS)
        self.point(worktree)
        self.git("merge", "-q", "--ff-only", branch)
        self.assertTrue(any(e.commit == theirs and e.action == "commit" for e in scope.reflog(main)))
        self.assertFalse(any(e.commit == theirs and scope.creating(e.action) for e in scope.reflog(worktree)))
        self.close()
        found = self.by_path()
        self.assert_unresolved(found["src/other.py"], theirs[:12], "arrived by `merge`")
        self.assert_verified(found["src/app.py"])
        self.assertTrue(any(e.commit == own for e in scope.reflog(worktree)))


if __name__ == "__main__":
    unittest.main()
