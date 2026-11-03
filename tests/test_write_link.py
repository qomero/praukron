"""Structured writes and archive through a linked chronicle (AC-T-WRITE-LINK-01-01, AC-T-WRITE-LINK-02-01, -02)."""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import archive, cli, compile as compiler, contract, layout, mutate, task, validate  # noqa: E402
from test_archive_plan import ArchivePlanCase  # noqa: E402
from test_assumptions import AssumptionCase  # noqa: E402


def link(source: Path, target: Path, kind: str) -> bool:
    """Make `source` a directory link to `target`; False where the OS refuses."""
    try:
        if kind == "junction":
            if os.name != "nt":
                return False
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(source), str(target)],
                                  capture_output=True)
            return done.returncode == 0
        os.symlink(target, source, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


class LinkedChronicleCase:
    kind = ""

    def setUp(self):
        super().setUp()
        self.outside = self.dir.parent / (self.dir.name + "-outside")
        self.addCleanup(shutil.rmtree, self.outside, True)
        shutil.move(str(self.authority), str(self.outside))
        if not link(self.authority, self.outside, self.kind):
            shutil.move(str(self.outside), str(self.authority))
            self.skipTest(f"cannot create a {self.kind} here")
        self.addCleanup(self.unlink)
        self.load()

    def unlink(self):
        try:
            os.rmdir(self.authority)
        except OSError:
            try:
                self.authority.unlink()
            except OSError:
                pass

    def run_cli(self, *args, stdin=""):
        """Run from the repository root without -C, as an agent does."""
        out, err = io.StringIO(), io.StringIO()
        before = Path.cwd()
        os.chdir(self.dir)
        try:
            with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
                code = cli.main(list(args))
        finally:
            os.chdir(before)
        return code, out.getvalue(), err.getvalue()

    def read(self, name):
        return (self.outside / name).read_bytes()

    def revision(self, name, anchor):
        return mutate.section(self.read(name), anchor).revision

    def write_ok(self, *args, payload):
        code, out, err = self.run_cli(*args, stdin=json.dumps(payload))
        self.assertEqual(code, 0, out + err)
        result = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(result["ok"], result)
        return result

    def assert_valid(self):
        self.assertEqual(validate.errors(validate.check(compiler.load(self.dir))), [])
        recovery = self.dir / layout.RECOVERY_DIR
        self.assertEqual(sorted(recovery.glob("*.json")) if recovery.is_dir() else [], [])

    def test_journal_append(self):
        self.write_ok("journal", "append", "--input", "-", payload={"title": "Linked", "body": "Through."})
        self.assertIn(b"Linked", self.read("JOURNAL.md"))
        self.assert_valid()

    def test_task_add(self):
        payload = copy.deepcopy(task.EXAMPLE)
        self.write_ok("task", "add", "--input", "-", payload=payload)
        self.assertIn(b"T-NEW", self.read("TASKS.md"))
        self.assert_valid()

    def test_task_update(self):
        self.write_ok("task", "update", "--input", "-", payload={
            "id": "T-TWO", "expectRevision": self.revision("TASKS.md", "T-TWO"),
            "set": {"status": "WIP", "owner": "worker"}})
        self.assertIn(b"worker", self.read("TASKS.md"))
        self.assert_valid()

    def test_criterion_set(self):
        self.write_ok("criterion", "set", "--input", "-", payload={
            "criterion": "AC-T-TWO-01", "state": "PASS", "evidence": "Synthetic check.",
            "expectRevision": self.revision("ACCEPTANCE.md", "AC-T-TWO")})
        self.assertIn(b"Synthetic check.", self.read("ACCEPTANCE.md"))
        self.assert_valid()

    def test_batch_apply(self):
        revision = self.revision("TASKS.md", "T-TWO")
        self.write_ok("batch", "apply", "--input", "-", payload={"operations": [
            {"op": "task update", "id": "T-TWO", "expectRevision": revision,
             "set": {"status": "WIP", "owner": "worker"}},
            {"op": "journal append", "title": "Batched", "body": "Through the link."}]})
        self.assertIn(b"Batched", self.read("JOURNAL.md"))
        self.assertIn(b"worker", self.read("TASKS.md"))
        self.assert_valid()

    def test_respond(self):
        code, out, err = self.run_cli("respond", "A-2", "confirm", "--date", "2026-10-03", stdin="Agreed.")
        self.assertEqual(code, 0, out + err)
        self.assertIn(b"Agreed.", self.read("RESPONSES.md"))
        self.assert_valid()

    def test_contract_add(self):
        bare = ("## T-BARE: Awaiting contract\n- Status: TODO\n- Module: M-FOUNDATION\n- Domain: execution\n"
                "- Validation: UNTESTED\n- Dependencies: none\n- Owner: unassigned\n"
                "- Evidence: —\n- Governed by: ADR-001\n")
        (self.outside / "TASKS.md").write_bytes(contract.append_section(self.outside / "TASKS.md", "Tasks", bare))
        payload = copy.deepcopy(contract.EXAMPLE)
        payload.update(task="T-BARE", expectTaskRevision=self.revision("TASKS.md", "T-BARE"))
        self.write_ok("contract", "add", "--input", "-", payload=payload)
        self.assertIn(b"AC-T-NEW", self.read("ACCEPTANCE.md"))
        self.assert_valid()

    def test_acr_add(self):
        self.write_ok("acr", "add", "--input", "-", payload={
            "task": "T-ONE", "criterion": "AC-T-ONE-01",
            "proposed": "Given a request, When it runs, Then it succeeds within a second.",
            "reason": "The original wording is not measurable."})
        self.assertIn(b"within a second", self.read("ACCEPTANCE.md"))
        self.assert_valid()

    def test_interrupted_write_is_rolled_back_through_the_link(self):
        path = self.authority / "JOURNAL.md"
        original = b"# Journal\n"
        path.write_bytes(original)
        mutate._write_recovery(self.dir.resolve(), {path.resolve(): original}, prefix="write")
        path.write_bytes(original + b"\n## half written\n")
        self.assertTrue(mutate.recover_write(self.dir))
        self.assertEqual(self.read("JOURNAL.md"), original)
        self.assertFalse(mutate.recover_write(self.dir))


class TestSymlinkChronicle(LinkedChronicleCase, AssumptionCase):
    kind = "symlink"


class TestJunctionChronicle(LinkedChronicleCase, AssumptionCase):
    kind = "junction"


class LinkedArchiveCase:
    """archive apply in backup mode names each file relative to the chronicle (AC-T-WRITE-LINK-02-01)."""
    kind = ""

    def setUp(self):
        super().setUp()
        self.outside = self.root.parent / (self.root.name + "-outside")
        self.addCleanup(shutil.rmtree, self.outside, True)
        shutil.move(str(self.authority), str(self.outside))
        if not link(self.authority, self.outside, self.kind):
            shutil.move(str(self.outside), str(self.authority))
            self.skipTest(f"cannot create a {self.kind} here")
        self.addCleanup(self.unlink)

    def unlink(self):
        try:
            os.rmdir(self.authority)
        except OSError:
            try:
                self.authority.unlink()
            except OSError:
                pass

    def test_apply_in_backup_mode_through_the_link(self):
        plan = archive.preview(self.root, "P1")
        result = archive.archive(self.root, "P1", digest=plan.digest)
        self.assertEqual(result.mode, "nogit")
        directory = Path(result.recovery["backup"])
        files = json.loads((directory / "manifest.json").read_bytes())["files"]
        self.assertTrue(files)
        for entry in files:
            self.assertTrue(entry["path"].startswith(layout.AUTHORITY_DIR + "/"), entry)
            if not entry["absent"]:
                self.assertTrue((directory / entry["path"]).is_file(), entry)
        self.assertTrue((self.outside / "archive" / "P1").is_dir())
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])


class TestSymlinkArchive(LinkedArchiveCase, ArchivePlanCase):
    kind = "symlink"


class TestJunctionArchive(LinkedArchiveCase, ArchivePlanCase):
    kind = "junction"


if __name__ == "__main__":
    unittest.main()
