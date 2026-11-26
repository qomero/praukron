"""Observed task scope captured from Git at claim and close (AC-T-CIL-SCOPE-01, ADR-092).

Every test builds its own temporary Git repository; none touches the
repository the suite runs in.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import batch, cli, compile as compiler, layout, mutate, ref, scope, task_update  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

GIT = shutil.which("git")


def git_env(ceiling: Path) -> dict[str, str]:
    return {"GIT_CEILING_DIRECTORIES": str(ceiling), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


class ScopeCase(unittest.TestCase):
    """A fixture project; `repository` makes it a Git repository with one commit."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-scope-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        environment = mock.patch.dict(os.environ, git_env(self.dir))
        environment.start()
        self.addCleanup(environment.stop)
        self.root = build_fixture(self.dir / "repo")
        self.authority = self.root / layout.AUTHORITY_DIR
        self.tasks = self.authority / "TASKS.md"
        self.code = self.authority / "CODE.md"
        for name in ("T-TWO", "T-THREE"):
            self.pass_criteria(name)

    # -- Git -------------------------------------------------------------
    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=True)
        return done.stdout.decode("utf-8").strip()

    def repository(self, files: dict[str, str] | None = None) -> str:
        self.git("init", "-q")
        self.git("config", "commit.gpgsign", "false")
        for name, text in (files or {"src/app.py": "print('one')\n", "src/old.py": "old\n",
                                     "src/gone.py": "gone\n", "README.md": "readme\n"}).items():
            self.write(name, text)
        return self.commit("initial")

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    # -- Chronicle -------------------------------------------------------
    def pass_criteria(self, task: str) -> None:
        acceptance = self.authority / "ACCEPTANCE.md"
        text = acceptance.read_text(encoding="utf-8")
        old = {"T-TWO": "Then they stand.\n  `TEST` · `NOT_RUN`",
               "T-THREE": "keeps rain out.\n  `RUNTIME` · `NOT_RUN`"}[task]
        acceptance.write_text(text.replace(old, old.replace("NOT_RUN", "PASS")), encoding="utf-8")

    def project(self):
        return compiler.load(self.root)

    def revision(self, task: str = "T-TWO") -> str:
        return mutate.section(self.tasks.read_bytes(), task).revision

    def update(self, changes: dict, task: str = "T-TWO") -> dict:
        result = task_update.update(self.root, {"id": task, "expectRevision": self.revision(task), "set": changes})
        self.assertTrue(result["ok"], result)
        return result

    def claim(self, task: str = "T-TWO", owner: str = "agent/builder") -> dict:
        return self.update({"status": "WIP", "owner": owner}, task)

    def close(self, task: str = "T-TWO") -> dict:
        return self.update({"status": "DONE", "evidence": "tests pass"}, task)

    def task(self, task: str = "T-TWO"):
        return self.project().task(task)

    def references(self, task: str = "T-TWO") -> dict:
        return {r.path: r for r in self.project().references if r.task == task}


@unittest.skipIf(GIT is None, "git is not installed")
class TestClaimRecordsCommit(ScopeCase):
    """AC-T-CIL-SCOPE-01-05."""

    def test_claim_records_the_current_commit(self):
        head = self.repository()
        result = self.claim()
        self.assertEqual(result["capture"], {"status": "recorded", "commit": head})
        self.assertEqual(self.task().claimed_at, head)
        self.assertIn(f"- Claimed at: {head}\n", self.tasks.read_text(encoding="utf-8"))

    def test_claim_inside_batch_records_the_current_commit(self):
        head = self.repository()
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": self.revision(),
             "set": {"status": "WIP", "owner": "agent/builder"}}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["capture"], {"status": "recorded", "commit": head})
        self.assertEqual(self.task().claimed_at, head)

    def test_a_task_claimed_again_keeps_its_first_commit(self):
        first = self.repository()
        self.claim()
        self.update({"status": "BLOCKED"})
        self.write("src/app.py", "print('two')\n")
        self.assertNotEqual(self.commit("second"), first)
        result = self.claim()
        self.assertEqual(result["capture"], {"status": "kept", "commit": first})
        self.assertEqual(self.task().claimed_at, first)

    def test_a_task_claimed_without_a_commit_gains_one_on_its_next_claim(self):
        self.claim()  # not yet a repository
        self.assertIsNone(self.task().claimed_at)
        self.update({"status": "BLOCKED"})
        head = self.repository()
        self.claim()
        self.assertEqual(self.task().claimed_at, head)

    def test_other_updates_do_not_record_a_commit(self):
        self.repository()
        result = self.update({"owner": "someone"})
        self.assertNotIn("capture", result)
        self.assertIsNone(self.task().claimed_at)


@unittest.skipIf(GIT is None, "git is not installed")
class TestCloseCapturesChanges(ScopeCase):
    """AC-T-CIL-SCOPE-01-06 and -01."""

    def change_everything(self):
        self.write("src/app.py", "print('changed')\n")            # modified, committed below
        self.git("mv", "src/old.py", "src/new name ü.py")         # renamed, staged
        self.commit("work")
        (self.root / "src/gone.py").unlink()                       # deleted, working tree only
        self.write("README.md", "readme, edited\n")               # modified, working tree only
        self.write("docs/notes ü.md", "new\n")                     # untracked, added
        self.write("src/staged.py", "staged\n")
        self.git("add", "src/staged.py")                           # staged, added

    def test_close_records_each_change_as_an_observed_reference(self):
        base = self.repository()
        self.claim()
        self.change_everything()
        head = self.git("rev-parse", "HEAD")
        result = self.close()
        found = self.references()
        self.assertEqual(result["capture"]["status"], "captured")
        self.assertEqual(result["capture"]["references"], 6)
        self.assertEqual(sorted(found), sorted([
            "src/app.py", "src/new name ü.py", "src/gone.py", "README.md", "docs/notes ü.md", "src/staged.py"]))
        expected = {"src/app.py": ("modifies", None, None), "src/new name ü.py": ("modifies", None, "src/old.py"),
                    "src/gone.py": ("deletes", "deleted", None), "README.md": ("modifies", None, None),
                    "docs/notes ü.md": ("creates", None, None), "src/staged.py": ("creates", None, None)}
        for path, (relation, lifecycle, was) in expected.items():
            with self.subTest(path=path):
                reference = found[path]
                self.assertEqual((reference.relation, reference.lifecycle, reference.was), (relation, lifecycle, was))
                self.assertEqual((reference.scope, reference.provenance, reference.verification,
                                  reference.authorization), ("observed", "git_diff", "verified", "not_applicable"))
                self.assertEqual((reference.base, reference.head), (base, head))
                self.assertTrue(reference.recorded.endswith(" · agent/builder"), reference.recorded)
        # A change in the head commit is true at it; a working-tree change is not yet in any commit.
        self.assertEqual(found["src/app.py"].revision, head)
        self.assertIsNone(found["README.md"].revision)
        self.assertIn("working tree", found["README.md"].evidence)
        self.assertEqual(self.task().status, "DONE")

    def test_close_inside_batch_writes_task_and_references_together(self):
        self.repository()
        self.claim()
        self.write("src/app.py", "print('changed')\n")
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": self.revision(),
             "set": {"status": "DONE", "evidence": "tests pass"}},
            {"op": "journal append", "title": "Closed T-TWO", "body": "Done."}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["capture"]["references"], 1)
        self.assertEqual(list(self.references()), ["src/app.py"])

    def test_claim_and_close_in_one_batch_capture_the_working_tree(self):
        self.repository()
        self.write("src/app.py", "print('changed')\n")
        revision = self.revision()
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": revision,
             "set": {"status": "WIP", "owner": "agent/builder"}},
            {"op": "task update", "id": "T-TWO", "expectRevision": revision,
             "set": {"status": "DONE", "evidence": "tests pass"}}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][1]["capture"]["references"], 1)

    def test_a_refused_batch_writes_neither_the_task_nor_its_references(self):
        self.repository()
        self.claim()
        self.write("src/app.py", "print('changed')\n")
        before = (self.tasks.read_bytes(), self.code.exists())
        result = batch.apply(self.root, {"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": self.revision(),
             "set": {"status": "DONE", "evidence": "tests pass"}},
            {"op": "task update", "id": "T-NOPE", "expectRevision": "0" * 16, "set": {"status": "WIP"}}]})
        self.assertFalse(result["ok"])
        self.assertEqual((self.tasks.read_bytes(), self.code.exists()), before)

    def test_a_close_with_no_change_records_nothing(self):
        self.repository()
        self.claim()
        result = self.close()
        self.assertEqual(result["capture"]["references"], 0)
        self.assertFalse(self.code.exists())

    def test_crlf_code_file_keeps_its_line_endings(self):
        self.repository()
        self.code.write_bytes(ref.HEADER.encode("utf-8").replace(b"\n", b"\r\n"))
        self.claim()
        self.write("src/app.py", "print('changed')\n")
        self.close()
        data = self.code.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"- Source: git_diff\r\n", data)

    def test_scope_capture_between_two_named_revisions(self):
        base = self.repository()
        self.write("src/app.py", "print('changed')\n")
        self.git("mv", "src/old.py", "src/renamed.py")
        (self.root / "src/gone.py").unlink()
        self.write("src/added.py", "added\n")
        head = self.commit("work")
        self.write("README.md", "after head, not in the range\n")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["-C", str(self.root), "scope", "capture", "T-THREE", "--base", base, "--head", "HEAD"])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["capture"]["references"], 4)
        found = self.references("T-THREE")
        self.assertEqual({p: (r.relation, r.was) for p, r in found.items()}, {
            "src/app.py": ("modifies", None), "src/renamed.py": ("modifies", "src/old.py"),
            "src/gone.py": ("deletes", None), "src/added.py": ("creates", None)})
        for reference in found.values():
            self.assertEqual((reference.base, reference.head, reference.revision), (base, head, head))
        self.assertEqual(found["src/gone.py"].lifecycle, "deleted")

    def test_capture_is_not_repeated(self):
        self.repository()
        self.claim()
        self.write("src/app.py", "print('changed')\n")
        self.close()
        first = self.code.read_bytes()
        self.update({"status": "WIP"})
        self.write("src/more.py", "more\n")
        result = self.close()
        self.assertEqual(result["capture"]["references"], 1)
        self.assertEqual(result["capture"]["alreadyRecorded"], 1)
        self.assertTrue(self.code.read_bytes().startswith(first.rstrip(b"\n")))
        self.assertEqual(sorted(self.references()), ["src/app.py", "src/more.py"])
        again = scope.capture(self.root, "T-TWO", self.task().claimed_at)
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["written"])
        self.assertEqual(again["capture"]["references"], 0)
        self.assertEqual(len(self.references()), 2)


@unittest.skipIf(GIT is None, "git is not installed")
class TestAttribution(ScopeCase):
    """AC-T-CIL-SCOPE-01-02."""

    def test_changes_before_the_claim_are_not_attributed(self):
        self.repository()
        self.write("src/app.py", "print('before the claim')\n")
        self.commit("someone else's work")
        self.claim()
        self.write("src/mine.py", "mine\n")
        self.close()
        self.assertEqual(list(self.references()), ["src/mine.py"])

    def test_another_task_claimed_over_the_range_makes_changes_unresolved(self):
        self.repository()
        self.claim("T-THREE", owner="agent/other")
        self.claim()
        self.write("src/app.py", "print('who?')\n")
        result = self.close()
        reference = self.references()["src/app.py"]
        self.assertEqual(reference.lifecycle, "unresolved")
        self.assertIn("unresolved attribution", reference.evidence)
        self.assertIn("T-THREE", reference.evidence)
        self.assertEqual(result["capture"]["unresolved"], 1)

    def test_a_task_claimed_on_another_line_of_history_does_not_overlap(self):
        self.repository()
        self.git("checkout", "-q", "-b", "elsewhere")
        self.write("src/other.py", "other\n")
        self.commit("elsewhere")
        self.claim("T-THREE", owner="agent/other")
        self.git("checkout", "-q", "-")
        self.claim()
        self.write("src/app.py", "print('mine')\n")
        self.close()
        self.assertIsNone(self.references()["src/app.py"].lifecycle)

    def test_a_task_closed_and_committed_before_the_claim_does_not_overlap(self):
        self.repository()
        self.claim("T-THREE", owner="agent/other")
        self.write("src/app.py", "print('three')\n")
        self.commit("three")
        self.close("T-THREE")
        self.claim()
        self.write("src/app.py", "print('two')\n")
        self.close()
        reference = self.references()["src/app.py"]
        self.assertIsNone(reference.lifecycle)
        self.assertNotIn("unresolved", reference.evidence)

    def test_a_task_closed_with_uncommitted_changes_overlaps_only_on_its_files(self):
        self.repository()
        self.claim("T-THREE", owner="agent/other")
        self.write("src/app.py", "print('three, uncommitted')\n")
        self.close("T-THREE")
        self.claim()
        self.write("src/mine.py", "mine\n")
        self.close()
        found = self.references()
        self.assertEqual(found["src/app.py"].lifecycle, "unresolved")
        self.assertIn("T-THREE", found["src/app.py"].evidence)
        self.assertIsNone(found["src/mine.py"].lifecycle)


@unittest.skipIf(GIT is None, "git is not installed")
class TestIgnored(ScopeCase):
    """AC-T-CIL-SCOPE-01-03."""

    def test_ignored_generated_dependency_cache_and_own_files_are_not_recorded(self):
        self.repository({".gitignore": "node_modules/\nbuild/\n*.pyc\n.cache/\n", "src/app.py": "one\n",
                         "dist/bundle.js": "tracked\n"})
        self.write(".gitignore", "node_modules/\nbuild/\n*.pyc\n.cache/\ndist/\n")
        self.commit("ignore dist")
        self.claim()
        self.write("node_modules/lib/index.js", "dependency\n")
        self.write("build/out.o", "generated\n")
        self.write("src/__pycache__/app.cpython-312.pyc", "cache\n")
        self.write(".cache/data", "cache\n")
        self.write("dist/bundle.js", "a tracked file Git's rules ignore\n")
        self.write(".praukron/notes.txt", "Praukron's own\n")
        self.write("src/app.py", "two\n")
        self.close()
        self.assertEqual(list(self.references()), ["src/app.py"])


@unittest.skipIf(GIT is None, "git is not installed")
class TestPlannedUnchanged(ScopeCase):
    """AC-T-CIL-SCOPE-01-04."""

    def test_planned_references_are_unchanged_and_nothing_observed_is_authorized(self):
        self.repository()
        planned = ref.add(self.root, {"task": "T-TWO", "title": "Change the app", "path": "src/app.py",
                                      "relation": "modifies", "scope": "planned", "source": "agent",
                                      "recordedBy": "agent/builder", "authorizedBy": "ADR-001"})
        self.assertTrue(planned["ok"], planned)
        before = mutate.section(self.code.read_bytes(), planned["recordId"]).data.rstrip(b"\n")
        self.claim()
        self.write("src/app.py", "print('changed')\n")
        self.write("src/extra.py", "extra\n")
        result = self.close()
        self.assertEqual(result["capture"]["references"], 2)
        self.assertEqual(mutate.section(self.code.read_bytes(), planned["recordId"]).data.rstrip(b"\n"), before)
        observed = [r for r in self.project().references if r.scope == "observed"]
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(r.authorization == "not_applicable" and r.authorized_by is None for r in observed))
        self.assertNotIn(b"authorized", self.code.read_bytes().split(before)[1])


class TestNeverBlocks(ScopeCase):
    """AC-T-CIL-SCOPE-01-07."""

    def assert_plain_close(self, reason: str):
        result = self.claim()
        self.assertEqual(result["capture"]["status"], "skipped")
        self.assertIn(reason, result["capture"]["reason"])
        self.assertNotIn("Claimed at", self.tasks.read_text(encoding="utf-8"))
        result = self.close()
        self.assertEqual(result["capture"]["status"], "skipped")
        self.assertTrue(result["written"] and result["refreshed"])
        self.assertEqual(self.task().status, "DONE")
        self.assertFalse(self.code.exists())

    def test_outside_a_repository(self):
        self.assert_plain_close("not a Git repository")

    def test_without_git_on_path(self):
        empty = self.dir / "empty-bin"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertIsNone(shutil.which("git"))
            self.assert_plain_close("git is not installed")

    def test_the_writes_are_those_of_release_2_3_1(self):
        """Without Git the chronicle and the reply are those of a write with no capture code at all."""
        payloads = [{"status": "WIP", "owner": "agent/builder"}, {"status": "DONE", "evidence": "tests pass"}]
        results = []
        for name in ("no-capture", "no-git"):
            self.root = build_fixture(self.dir / name)
            self.authority = self.root / layout.AUTHORITY_DIR
            self.tasks = self.authority / "TASKS.md"
            self.pass_criteria("T-TWO")
            if name == "no-capture":
                with mock.patch.object(scope, "claim_commit", return_value=(None, {})), \
                        mock.patch.object(scope, "at_close", return_value=({}, {})):
                    replies = [self.update(p) for p in payloads]
                self.assertTrue(all("capture" not in r for r in replies))
            else:
                replies = [self.update(p) for p in payloads]
                self.assertTrue(all(r["capture"]["status"] == "skipped" for r in replies))
            results.append((self.tasks.read_bytes(), sorted(p.name for p in self.authority.iterdir()),
                            [{k: v for k, v in r.items() if k != "capture"} for r in replies]))
        self.assertEqual(results[0], results[1])

    @unittest.skipIf(GIT is None, "git is not installed")
    def test_no_commit_yet(self):
        self.git("init", "-q")
        self.assert_plain_close("no commit yet")

    @unittest.skipIf(GIT is None, "git is not installed")
    def test_a_missing_claim_commit_skips_capture(self):
        self.repository()
        self.claim()
        text = self.tasks.read_text(encoding="utf-8")
        self.tasks.write_text(text.replace(self.task().claimed_at, "f" * 40), encoding="utf-8")
        self.write("src/app.py", "changed\n")
        result = self.close()
        self.assertEqual(result["capture"]["status"], "skipped")
        self.assertIn("is not a commit", result["capture"]["reason"])
        self.assertFalse(self.code.exists())

    @unittest.skipIf(GIT is None, "git is not installed")
    def test_a_failing_or_hanging_git_skips_capture(self):
        self.repository()
        self.claim()
        self.write("src/app.py", "changed\n")
        real = subprocess.run

        def hang(command, *args, **kwargs):
            if "diff" in command:
                raise subprocess.TimeoutExpired(command, scope.TIMEOUT)
            return real(command, *args, **kwargs)

        with mock.patch.object(scope.subprocess, "run", side_effect=hang):
            result = self.close()
        self.assertEqual(result["capture"]["status"], "skipped")
        self.assertIn("timed out", result["capture"]["reason"])
        self.assertEqual(self.task().status, "DONE")
        self.assertFalse(self.code.exists())

    def test_task_without_a_claim_commit_closes(self):
        result = self.close()
        self.assertEqual(result["capture"], {"status": "skipped", "reason": "the task has no Claimed at commit"})


if __name__ == "__main__":
    unittest.main()
