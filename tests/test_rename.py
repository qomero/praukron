"""Installation rename preserves authority and refuses ambiguous destinations."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestRename(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="praukron-rename-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / ".prokron"
        shutil.copytree(ROOT / "templates/chronicle", self.old / "chronicle")
        (self.old / "runtime").mkdir()
        (self.old / "runtime/VERSION").write_text((ROOT / "VERSION").read_text(encoding="utf-8"), encoding="utf-8")
        self.guidance = "# My rules\n<!-- project-prokron:start -->\nCUSTOM Prokron guidance\n<!-- project-prokron:end -->\n"
        (self.root / "AGENTS.md").write_text(self.guidance, encoding="utf-8")
        (self.old / "commands").mkdir()
        (self.old / "commands/prokron-work.md").write_text("CUSTOM WORK\n", encoding="utf-8")

    def install(self):
        return subprocess.run(
            ["sh", str(ROOT / "install.sh"), str(self.root), "--no-link"],
            capture_output=True, text=True,
        )

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_upgrade_preserves_records_edited_guidance_and_old_entry(self):
        records = {str(p.relative_to(self.old / "chronicle")): p.read_bytes()
                   for p in (self.old / "chronicle").rglob("*") if p.is_file()}
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.old.is_symlink())
        for name, data in records.items():
            self.assertEqual((self.root / ".praukron/chronicle" / name).read_bytes(), data)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), self.guidance)
        self.assertEqual((self.old / "commands/prokron-work.md").read_text(encoding="utf-8"), "CUSTOM WORK\n")
        self.assertTrue((self.root / ".praukron/upgrade/AGENTS.md").is_file())
        self.assertTrue((self.root / ".praukron/commands/praukron-work.md").is_file())
        old_command = subprocess.run([str(self.old / "prokron"), "--version"],
                                     capture_output=True, text=True, cwd=self.root)
        self.assertEqual(old_command.returncode, 0, old_command.stderr)
        self.assertTrue(old_command.stdout.startswith("praukron "))
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), self.guidance)

    def test_unmodified_old_agent_block_is_replaced_using_its_manifest(self):
        # A real old manifest checksums just the owned block, not surrounding rules.
        block = "<!-- project-prokron:start -->\nOld rules\n<!-- project-prokron:end -->\n"
        (self.root / "AGENTS.md").write_text("KEEP BEFORE\n" + block + "KEEP AFTER\n", encoding="utf-8")
        checksum = subprocess.run(["cksum"], input=block, text=True,
                                  capture_output=True, check=True).stdout.split()
        (self.old / "runtime/GUIDANCE").write_text(f"{checksum[0]}-{checksum[1]} AGENTS.md#prokron\n", encoding="utf-8")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("KEEP BEFORE", text)
        self.assertIn("KEEP AFTER", text)
        self.assertIn("project-praukron:start", text)
        self.assertNotIn("project-prokron:start", text)
        self.assertIn("AGENTS.md#praukron", (self.root / ".praukron/runtime/GUIDANCE").read_text(encoding="utf-8"))

    def test_two_installations_are_refused_without_mutation(self):
        (self.root / ".praukron").mkdir()
        before = self.snapshot()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both .prokron and .praukron", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_downgrade_is_refused_before_relocation(self):
        (self.old / "runtime/VERSION").write_text("999.0.0\n", encoding="utf-8")
        before = self.snapshot()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.old.is_symlink())
        self.assertFalse((self.root / ".praukron").exists())

    def test_external_legacy_link_is_refused(self):
        moved = self.root / "external"
        self.old.rename(moved)
        self.old.symlink_to(moved, target_is_directory=True)
        before = self.snapshot()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / ".praukron").exists())

    def test_linked_record_is_refused_before_relocation(self):
        record = self.old / "chronicle/TASKS.md"
        saved = self.root / "saved.md"
        record.rename(saved)
        record.symlink_to(saved)
        before = self.snapshot()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.old.is_symlink())
        self.assertFalse((self.root / ".praukron").exists())


if __name__ == "__main__":
    unittest.main()
