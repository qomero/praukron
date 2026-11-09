"""Temporal Memory: state_at_commit and the temporal state diff (T-PTM-STATE-01, T-PTM-DIFF-01, ADR-093)."""

from __future__ import annotations

import io
import json
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

from praukron import cli, compile as compiler, layout, temporal  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

class HistoryCase(unittest.TestCase):
    """A Git repository whose chronicle changes commit by commit."""

    def setUp(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        self.base = Path(tempfile.mkdtemp(prefix="praukron-history-test-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "project"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "History test"), ("user.email", "history@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git("config", key, value)
        (self.root / "README.md").write_text("A project.\n", encoding="utf-8")
        self.rev0 = self.commit("Before the chronicle")
        build_fixture(self.root)
        self.rev1 = self.commit("Add the chronicle")
        self.repo = temporal.Repository(self.root)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(["git", "-C", str(cwd or self.root), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / layout.AUTHORITY_DIR / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def observe(self) -> dict[str, object]:
        index = self.root / ".git" / "index"
        return {"head": self.git("rev-parse", "HEAD"), "branch": self.git("symbolic-ref", "HEAD"),
                "status": self.git("status", "--porcelain", "--untracked-files=all"),
                "index": index.read_bytes()}


class TestStateAtCommit(HistoryCase):
    def test_extracts_into_a_fresh_directory_and_leaves_the_repository_unchanged(self) -> None:
        # AC-T-PTM-STATE-01-01
        (self.root / "untracked.txt").write_text("left alone\n", encoding="utf-8")
        self.edit("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        before = self.observe()
        made = []
        real = tempfile.mkdtemp

        def record(*args, **kwargs):
            made.append(Path(real(*args, **kwargs)))
            return str(made[-1])

        with mock.patch.object(temporal.tempfile, "mkdtemp", side_effect=record):
            found = temporal.state_at_commit(self.repo, "HEAD")
        self.assertEqual(found["status"], temporal.STATE)
        self.assertEqual(self.observe(), before)
        self.assertEqual(len(made), 1)
        self.assertFalse(made[0].exists())
        self.assertNotIn(self.root.resolve(), made[0].resolve().parents)
        # The committed chronicle was read, not the edited working tree.
        two = next(t for t in found["state"]["tasks"] if t["id"] == "T-TWO")
        self.assertEqual(two["status"], "TODO")

    def test_result_carries_its_provenance(self) -> None:
        # AC-T-PTM-STATE-01-02
        found = temporal.state_at_commit(self.repo, self.rev1[:7])
        self.assertEqual(found["repository"], str(self.root.resolve()))
        self.assertEqual(found["revision"], self.rev1)
        self.assertEqual((found["chronicle"], found["layout"]), (layout.AUTHORITY_DIR, "current"))
        self.assertEqual(found["compiler"]["version"], layout.version())
        self.assertIn("commit", found["compiler"])
        self.assertIn("clean", found["compiler"])
        if found["compiler"]["commit"] is not None:
            self.assertRegex(found["compiler"]["commit"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(found["compiler"]["clean"], bool)

    def test_layouts_are_tried_in_order_and_unreadable_revisions_fail_explicitly(self) -> None:
        # AC-T-PTM-STATE-01-03
        found = temporal.state_at_commit(self.repo, self.rev0)
        self.assertEqual(found["status"], temporal.UNSUPPORTED)
        self.assertIn("no chronicle", found["reason"])

        build_fixture(self.root, layout.V02_AUTHORITY_DIR)
        both = self.commit("A v0.2 copy beside the current chronicle")
        self.assertEqual(temporal.state_at_commit(self.repo, both)["layout"], "current")

        shutil.rmtree(self.root / layout.HOME_DIR)
        v02 = self.commit("Only the v0.2 layout")
        found = temporal.state_at_commit(self.repo, v02)
        self.assertEqual((found["status"], found["chronicle"], found["layout"]), (temporal.STATE, "prokron", "v0.2"))

        shutil.rmtree(self.root / layout.V02_AUTHORITY_DIR)
        build_fixture(self.root, layout.V01_AUTHORITY_DIR)
        (self.root / layout.V01_AUTHORITY_DIR / "PHASES.md").unlink()
        v01 = self.commit("A v0.1 layout with no PHASES.md")
        found = temporal.state_at_commit(self.repo, v01)
        self.assertEqual((found["status"], found["layout"]), (temporal.UNSUPPORTED, "v0.1"))
        self.assertIn("PHASES.md", found["reason"])
        self.assertIsNone(found["state"])

        shutil.rmtree(self.root / layout.V01_AUTHORITY_DIR)
        build_fixture(self.root)
        self.edit("TASKS.md", "## T-ONE: Lay the foundation", "## T-ONE Lay the foundation")
        broken = self.commit("An unreadable task heading")
        found = temporal.state_at_commit(self.repo, broken)
        self.assertEqual(found["status"], temporal.UNSUPPORTED)
        self.assertIn("ParseError", found["reason"])
        self.assertIn("TASKS.md", found["reason"])
        self.assertNotIn("Traceback", found["reason"])
        self.assertNotIn(tempfile.gettempdir(), found["reason"])
        # Nothing was migrated: the commit still holds the unreadable heading.
        self.assertIn("## T-ONE Lay the foundation",
                      self.git("show", f"{broken}:{layout.AUTHORITY_DIR}/TASKS.md"))

    def test_validation_findings_are_labelled_and_compile_failures_kept_apart(self) -> None:
        # AC-T-PTM-STATE-01-04
        self.edit("TASKS.md", "- Dependencies: T-ONE", "- Dependencies: T-MISSING")
        invalid = self.commit("Depend on a task that does not exist")
        found = temporal.state_at_commit(self.repo, invalid)
        self.assertEqual(found["status"], temporal.STATE)
        self.assertGreater(found["validation"]["errors"], 0)
        self.assertIn("current rules", found["validation"]["rules"])
        self.assertTrue(any("T-MISSING" in item for item in found["validation"]["findings"]))
        self.assertIsNone(found["compileError"])

        with mock.patch.object(temporal.compiler, "as_json", side_effect=RuntimeError("boom")):
            failed = temporal.state_at_commit(self.repo, invalid)
        self.assertEqual(failed["status"], temporal.UNSUPPORTED)
        self.assertEqual(failed["compileError"], "RuntimeError: boom")
        self.assertIn("compilation failed", failed["reason"])
        self.assertGreater(failed["validation"]["errors"], 0)

    def test_state_equals_compile_of_the_same_files_checked_out(self) -> None:
        # AC-T-PTM-STATE-01-05
        self.edit("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        head = self.commit("Start T-TWO")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["-C", str(self.root), "compile"]), 0)
        compiled = json.loads((self.root / layout.COMPILED_DIR / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(temporal.state_at_commit(self.repo, head)["state"], compiled)

    def test_a_separate_repository_reads_the_same_way(self) -> None:
        # AC-T-PTM-STATE-01-06
        home = self.base / "other" / "project" / layout.HOME_DIR
        home.mkdir(parents=True)
        shutil.copytree(self.root / layout.AUTHORITY_DIR, home / "chronicle")
        git_dir = self.base / "backup.git"
        subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True, capture_output=True)
        separate = ["git", "--git-dir", str(git_dir), "--work-tree", str(home)]
        subprocess.run(["git", "--git-dir", str(git_dir), "config", "core.bare", "false"], check=True)
        subprocess.run(["git", "--git-dir", str(git_dir), "config", "core.worktree", str(home)], check=True)
        subprocess.run([*separate, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*separate, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", "Back up"], check=True, capture_output=True)
        backup = temporal.Repository(git_dir=git_dir)
        found = temporal.state_at_commit(backup, "HEAD")
        self.assertEqual((found["status"], found["mode"], found["chronicle"]),
                         (temporal.STATE, "separate-repository", "chronicle"))
        self.assertEqual(found["state"], temporal.state_at_commit(self.repo, self.rev1)["state"])

    def test_cli_prints_state_and_exits_nonzero_when_unsupported(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "history", "state", self.rev1]), 0)
        self.assertIn(f"STATE at {self.rev1}", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "history", "--json", "state", self.rev0]), 1)
        self.assertEqual(json.loads(out.getvalue())["status"], temporal.UNSUPPORTED)


class TestTemporalDiff(HistoryCase):
    def setUp(self) -> None:
        super().setUp()
        self.edit("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        self.rev2 = self.commit("Start T-TWO")

    def transitions(self, result: dict, record_id: str) -> list[dict]:
        return [t for t in result["transitions"] if t["id"] == record_id]

    def test_authoritative_transitions_by_stable_id_with_pinned_sources(self) -> None:
        # AC-T-PTM-DIFF-01-01
        result = temporal.diff(self.repo, self.rev1, self.rev2)
        self.assertEqual(result["status"], temporal.OBSERVED)
        self.assertEqual(result["kind"], "OBSERVED_STATE_TRANSITIONS")
        [change] = result["transitions"]
        self.assertEqual((change["entity"], change["id"], change["transition"], change["field"]),
                         ("task", "T-TWO", "CHANGED", "status"))
        self.assertEqual((change["before"], change["after"]), ("TODO", "WIP"))
        self.assertEqual(change["from"], {"revision": self.rev1, "chronicle": layout.AUTHORITY_DIR,
                                          "source": "TASKS.md#T-TWO"})
        self.assertEqual(change["to"]["revision"], self.rev2)
        # T-TWO going WIP changes derived views (ready, blocked); none is a transition.
        self.assertTrue(all(t["field"] in temporal.FIELDS[t["entity"]] for t in result["transitions"]))

    def test_relocation_deletion_reuse_and_chronicle_moves(self) -> None:
        # AC-T-PTM-DIFF-01-02
        path = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        text = path.read_text(encoding="utf-8")
        three = text[text.index("## T-THREE:"):]
        path.write_text(text.replace(three, ""), encoding="utf-8")
        removed = self.commit("Remove T-THREE")
        result = temporal.diff(self.repo, self.rev2, removed)
        self.assertEqual([t["transition"] for t in self.transitions(result, "T-THREE")], ["REMOVED"])

        path.write_text(path.read_text(encoding="utf-8") + three.replace(
            "Finish later", "Paint the walls"), encoding="utf-8")
        returned = self.commit("T-THREE names new work")
        result = temporal.diff(self.repo, self.rev2, returned)
        [reuse] = self.transitions(result, "T-THREE")
        self.assertEqual(reuse["transition"], "REUSED")
        self.assertEqual((reuse["before"], reuse["after"]), ("Finish later", "Paint the walls"))
        self.assertEqual(reuse["removedAt"], removed)
        self.assertEqual([t["transition"] for t in self.transitions(
            temporal.diff(self.repo, removed, returned), "T-THREE")], ["ADDED"])

        self.edit("TASKS.md", "## T-ONE: Lay the foundation", "## T-ONE: Pour the foundation")
        renamed = self.commit("Retitle T-ONE")
        [title] = self.transitions(temporal.diff(self.repo, returned, renamed), "T-ONE")
        self.assertEqual((title["transition"], title["field"]), ("CHANGED", "title"))

        shutil.move(str(self.root / layout.AUTHORITY_DIR), str(self.root / layout.V02_AUTHORITY_DIR))
        shutil.rmtree(self.root / layout.HOME_DIR)
        moved = self.commit("Move the whole chronicle")
        result = temporal.diff(self.repo, renamed, moved)
        self.assertEqual(result["status"], temporal.OBSERVED)
        self.assertEqual(result["transitions"], [])

        span = {"after": "a" * 40, "atOrBefore": "b" * 40, "commits": 1, "confidence": "ONE_COMMIT"}
        before = temporal.state_at_commit(self.repo, renamed)
        after = json.loads(json.dumps(before))
        for task in after["state"]["tasks"]:
            if task["id"] == "T-ONE":
                task["source"] = {"file": "archive/P1/TASKS.md", "anchor": "T-ONE"}
        [relocated] = temporal.compare(before, after, span, entity="T-ONE")
        self.assertEqual((relocated["transition"], relocated["before"], relocated["after"]),
                         ("RELOCATED", "TASKS.md", "archive/P1/TASKS.md"))

    def test_revisions_on_different_lines_are_not_a_history(self) -> None:
        # AC-T-PTM-DIFF-01-03
        self.git("checkout", "-q", "-b", "side", self.rev1)
        self.edit("TASKS.md", "## T-THREE: Finish later", "## T-THREE: Finish sooner")
        side = self.commit("Side work")
        self.git("checkout", "-q", "main")
        result = temporal.diff(self.repo, side, self.rev2)
        self.assertEqual(result["status"], temporal.NOT_ANCESTRAL)
        self.assertEqual(result["transitions"], [])
        reversed_order = temporal.diff(self.repo, self.rev2, self.rev1)
        self.assertEqual(reversed_order["status"], temporal.NOT_ANCESTRAL)
        self.assertIn("earlier revision first", reversed_order["reason"])

        self.git("merge", "-q", "--no-ff", "-m", "Merge side", "side")
        merged = self.git("rev-parse", "HEAD").strip()
        span = temporal.diff(self.repo, self.rev2, merged)["interval"]
        self.assertEqual(span["chronicleCommits"], [merged])
        self.assertEqual(span["confidence"], "ONE_COMMIT_MERGE")

    def test_intervals_confidence_and_insufficient_evidence(self) -> None:
        # AC-T-PTM-DIFF-01-04
        one = temporal.diff(self.repo, self.rev1, self.rev2)["transitions"][0]["interval"]
        self.assertEqual(one, {"after": self.rev1, "atOrBefore": self.rev2, "commits": 1, "confidence": "ONE_COMMIT"})

        (self.root / "README.md").write_text("Code only.\n", encoding="utf-8")
        self.commit("A code commit")
        self.edit("TASKS.md", "## T-ONE: Lay the foundation\n- Status: DONE",
                  "## T-ONE: Lay the foundation\n- Status: WIP")
        rev4 = self.commit("Reopen T-ONE")
        result = temporal.diff(self.repo, self.rev1, rev4)
        span = result["transitions"][0]["interval"]
        self.assertEqual((span["after"], span["atOrBefore"], span["commits"], span["confidence"]),
                         (self.rev1, rev4, 3, "SEVERAL_COMMITS"))
        self.assertTrue(any("only its two ends" in limit for limit in result["limits"]))

        unsupported = temporal.diff(self.repo, self.rev0, self.rev2)
        self.assertEqual(unsupported["status"], temporal.INSUFFICIENT)
        self.assertEqual(unsupported["transitions"], [])

        shutil.rmtree(self.root / layout.HOME_DIR)
        gone = self.commit("The chronicle is deleted")
        build_fixture(self.root)
        back = self.commit("The chronicle comes back")
        gap = temporal.diff(self.repo, rev4, back)
        self.assertEqual(gap["status"], temporal.INSUFFICIENT)
        self.assertEqual(gap["gaps"], [{"revision": gone, "reason": "no chronicle"}])
        self.assertEqual(gap["transitions"], [])

    def test_adr_lineage_in_both_directions(self) -> None:
        # AC-T-PTM-DIFF-01-05
        def adr(number: int, supersedes: list[int]) -> dict:
            return {"id": f"ADR-{number:03d}", "title": f"Decision {number}", "status": "ACCEPTED",
                    "supersedes": [f"ADR-{n:03d}" for n in supersedes],
                    "source": {"file": f"ADR/ADR-{number:03d}.md", "anchor": f"ADR-{number:03d}"}}
        found = {"state": {"decisions": [adr(1, []), adr(2, [1]), adr(3, [2]), adr(4, [])]}}
        middle = temporal.lineage(found, "ADR-002")
        self.assertEqual([(d["id"], d["via"]) for d in middle["supersedes"]], [("ADR-001", "ADR-002")])
        self.assertEqual([(d["id"], d["via"]) for d in middle["supersededBy"]], [("ADR-003", "ADR-002")])
        self.assertEqual(middle["supersededBy"][0]["source"], {"file": "ADR/ADR-003.md", "anchor": "ADR-003"})
        self.assertEqual([d["id"] for d in temporal.lineage(found, "ADR-003")["supersedes"]], ["ADR-002", "ADR-001"])
        alone = temporal.lineage(found, "ADR-004")
        self.assertEqual((alone["supersedes"], alone["supersededBy"]), ([], []))

    def test_linked_evidence_is_matched_by_id_never_by_timing(self) -> None:
        # AC-T-PTM-DIFF-01-06
        def state(responses: list, journal: list, requests: list) -> dict:
            return {"state": {"decisions": [{"id": "ADR-001", "affects": ["T-TWO"], "supersedes": [],
                                             "source": {"file": "ADR/ADR-001.md", "anchor": "ADR-001"}}],
                              "responses": responses, "journal": journal}, "changeRequests": requests}

        def response(number: int, target: str, text: str) -> dict:
            return {"id": f"R-{number}", "target": target, "text": text,
                    "source": {"file": "RESPONSES.md", "anchor": f"R-{number}"}}

        def entry(title: str, text: str) -> dict:
            return {"id": f"2026-01-01 — {title}", "title": title, "text": text,
                    "source": {"file": "JOURNAL.md", "anchor": title}}

        old = state([response(1, "T-ONE", "Fine.")], [], [])
        new = state([response(1, "T-ONE", "Fine."), response(2, "T-TWO", "Go."), response(3, "A-1", "See T-TWO.")],
                    [entry("Started T-TWO", "Walls."), entry("Same day", "Nothing named."),
                     entry("Other", "T-TWOX is different.")],
                    [{"id": "ACR-001", "criterion": "AC-T-TWO-01", "source": {"file": "ACCEPTANCE.md", "anchor": "ACR-001"}}])
        linked = temporal.linked_evidence(old, new, "task", "T-TWO", {"ac": "AC-T-TWO", "decisions": ["ADR-001"]})
        self.assertEqual([(i["kind"], i["id"], i["matchedBy"]) for i in linked], [
            ("acr", "ACR-001", "criterion"), ("decision", "ADR-001", "affects"),
            ("journal", "2026-01-01 — Started T-TWO", "text"),
            ("response", "R-2", "target"), ("response", "R-3", "text")])
        self.assertEqual({i["id"]: i["inInterval"] for i in linked}["ADR-001"], False)
        self.assertTrue({i["id"]: i["inInterval"] for i in linked}["R-2"])
        self.assertTrue(all(i["source"] for i in linked))

    def test_an_id_that_returns_with_the_same_title_is_still_reused(self) -> None:
        path = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        text = path.read_text(encoding="utf-8")
        three = text[text.index("## T-THREE:"):]
        path.write_text(text.replace(three, ""), encoding="utf-8")
        removed = self.commit("Remove T-THREE")
        path.write_text(path.read_text(encoding="utf-8") + three.replace("- Status: TODO", "- Status: WIP"),
                        encoding="utf-8")
        returned = self.commit("T-THREE returns, same title")
        [reuse] = self.transitions(temporal.diff(self.repo, self.rev2, returned), "T-THREE")
        self.assertEqual((reuse["transition"], reuse["before"], reuse["after"], reuse["removedAt"]),
                         ("REUSED", "Finish later", "Finish later", removed))

    def test_a_declared_domain_is_an_authored_field(self) -> None:
        self.edit("TASKS.md", "## T-ONE: Lay the foundation\n- Status: DONE",
                  "## T-ONE: Lay the foundation\n- Status: DONE\n- Domain: operations")
        declared = self.commit("Declare T-ONE's domain")
        [change] = temporal.diff(self.repo, self.rev2, declared)["transitions"]
        self.assertEqual((change["id"], change["field"], change["before"], change["after"]),
                         ("T-ONE", "declaredDomain", None, "operations"))

    def test_an_unknown_revision_is_named(self) -> None:
        with self.assertRaisesRegex(temporal.HistoryError, "unknown revision: nosuchrev"):
            temporal.state_at_commit(self.repo, "nosuchrev")

    def test_cli_diff_and_lineage(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "history", "diff", self.rev1, self.rev2]), 0)
        self.assertIn("CHANGED   task T-TWO status: TODO -> WIP  [TASKS.md#T-TWO]", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "history", "lineage", "ADR-001"]), 0)
        self.assertIn("no Supersedes: link in either direction", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "history", "lineage", "ADR-001", "--at", self.rev0]), 1)
        self.assertIn("INSUFFICIENT_EVIDENCE", out.getvalue())


if __name__ == "__main__":
    unittest.main()
