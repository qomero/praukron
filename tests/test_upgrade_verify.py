"""Upgrade diagnostics for pre-VERIFY owner tasks (T-UPGRADE-VERIFY-01)."""

from __future__ import annotations

import io
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

from praukron import cli, compile as compiler, validate  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class TestUpgradeVerify(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="praukron-upgrade-verify-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.authority = self.root / ".praukron/chronicle"
        build_fixture(self.root)
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        text = text.replace("- Validation: SYNTHETIC", "- Authority: owner\n- Validation: HUMAN_VERIFIED", 1)
        text = text.replace("- Validation: UNTESTED", "- Authority: owner\n- Validation: HUMAN_VERIFIED", 1)
        tasks.write_text(text, encoding="utf-8")
        (self.authority / "ADR/ADR-002.md").write_text(
            "# ADR-002: Project owners\n- Date: 2026-01-01\n- Status: ACCEPTED\n"
            "- Owners: upgrade-owner\n- Decision: The named login owns this project.\n",
            encoding="utf-8")
        (self.authority / "RESPONSES.md").write_text("# Responses\n", encoding="utf-8")
        runtime = self.root / ".praukron/runtime"
        runtime.mkdir()
        (runtime / "VERSION").write_text("2.0.0\n", encoding="utf-8")

    def records(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.authority)): p.read_bytes()
                for p in self.authority.rglob("*.md") if p.name != "INDEX.md"}

    def install(self) -> str:
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("installer integration requires POSIX sh")
        result = subprocess.run([shell, (ROOT / "install.sh").as_posix(),
                                 self.root.as_posix(), "--no-link"],
                                capture_output=True, text=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def run_cli(self, *args: str, tty: bool = False, login: str = "upgrade-owner") -> tuple[int, str]:
        out, err, stream = io.StringIO(), io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stream), \
                mock.patch.object(stream, "isatty", return_value=tty), \
                mock.patch.object(out, "isatty", return_value=tty), \
                mock.patch("praukron.respond.identity", return_value=login), \
                mock.patch("builtins.input", side_effect=["Checked the legacy evidence.",
                                                          args[1] if len(args) > 1 else ""]):
            code = cli.main(["-C", str(self.root), *args])
        return code, out.getvalue() + err.getvalue()

    def test_upgrade_names_every_task_and_action_with_or_without_views(self) -> None:
        before = self.records()
        for with_views in (False, True):
            with self.subTest(with_views=with_views):
                compiled = self.root / ".praukron/compiled"
                if with_views:
                    compiled.mkdir(exist_ok=True)
                    (compiled / "dashboard.html").write_text("old dashboard", encoding="utf-8")
                (self.root / ".praukron/runtime/VERSION").write_text("2.0.0\n", encoding="utf-8")
                output = self.install()
                self.assertIn("Upgrade installed; the existing chronicle needs attention", output)
                for task in ("T-ONE", "T-TWO"):
                    self.assertIn(f"TASKS.md#{task}", output)
                    self.assertIn(f".praukron/praukron respond {task} verify", output)
                    self.assertIn(f".praukron/praukron.cmd respond {task} verify", output)
                self.assertIn("interactively", output)
                self.assertIn("never an agent", output)
                for path, data in before.items():
                    self.assertEqual((self.authority / path).read_bytes(), data, path)
                # The installed runtime still refuses unverified authority.
                result = subprocess.run([sys.executable, str(self.root / ".praukron/praukron"),
                                         "-C", str(self.root), "validate", "--quiet"],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr.count("verification-without-owner"), 2)
                if with_views:
                    self.assertEqual((compiled / "dashboard.html").read_text(), "old dashboard")
                else:
                    self.assertFalse(compiled.exists())

    def test_guided_owner_actions_resolve_errors_without_hand_edits(self) -> None:
        before = (self.authority / "TASKS.md").read_bytes()
        for task in ("T-ONE", "T-TWO"):
            code, output = self.run_cli("respond", task, "verify", tty=True)
            self.assertEqual(code, 0, output)
        code, output = self.run_cli("validate")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.authority / "TASKS.md").read_bytes(), before)
        self.assertNotIn("chronicle needs attention", self.install())

    def test_noninteractive_and_unnamed_logins_do_not_resolve_verification(self) -> None:
        before = self.records()
        code, output = self.run_cli("respond", "T-ONE", "verify")
        self.assertEqual(code, 1)
        self.assertIn("only interactively", output)
        self.assertEqual(self.records(), before)
        code, output = self.run_cli("respond", "T-ONE", "verify", tty=True, login="not-the-owner")
        self.assertEqual(code, 0, output)
        findings = validate.errors(validate.check(compiler.load(self.root)))
        self.assertEqual(sum(f.code == "verification-without-owner" for f in findings), 2)

    def test_missing_owner_declaration_is_explained_during_upgrade(self) -> None:
        (self.authority / "ADR/ADR-002.md").unlink()
        output = self.install()
        self.assertIn("active gh login must be named in an ACCEPTED ADR's Owners field", output)
        self.assertIn("establish that owner declaration first if missing", output)


if __name__ == "__main__":
    unittest.main()
