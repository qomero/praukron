"""Notion credentials and installer-ready config (T-NOTION-CRED-01, ADR-087)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from praukron import cli, notion_config  # noqa: E402

SECRET = "ntn_test_secret_value_0123456789"


class Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "project"
        (self.project / ".praukron").mkdir(parents=True)
        self.home = self.tmp / "home"
        (self.home / ".praukron").mkdir(parents=True)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, path: Path, token: str) -> None:
        path.write_text(f"# comment\nNOTION_TOKEN={token}\n", encoding="utf-8")


class ResolutionTest(Workspace):
    def test_order_is_environment_then_project_then_user(self) -> None:
        project_file = notion_config.project_file(self.project)
        user_file = notion_config.user_file()
        self.write(project_file, "from-project")
        self.write(user_file, "from-user")
        token = notion_config.resolve(self.project, {"NOTION_TOKEN": "from-env"})
        self.assertEqual((token.value, token.source), ("from-env", "environment"))
        token = notion_config.resolve(self.project, {})
        self.assertEqual((token.value, token.source), ("from-project", str(project_file)))
        project_file.unlink()
        token = notion_config.resolve(self.project, {})
        self.assertEqual((token.value, token.source), ("from-user", str(user_file)))
        user_file.unlink()
        self.assertIsNone(notion_config.resolve(self.project, {}).value)

    def test_blank_template_does_not_hide_the_user_key(self) -> None:
        notion_config.write_template(notion_config.project_file(self.project))
        self.write(notion_config.user_file(), "from-user")
        self.assertEqual(notion_config.resolve(self.project, {"NOTION_TOKEN": "  "}).value, "from-user")

    def test_quotes_and_export_are_accepted(self) -> None:
        notion_config.project_file(self.project).write_text(
            'export NOTION_TOKEN="quoted"\n', encoding="utf-8")
        self.assertEqual(notion_config.resolve(self.project, {}).value, "quoted")

    def test_repr_never_shows_the_value(self) -> None:
        self.assertNotIn(SECRET, repr(notion_config.Token(SECRET, "environment")))


class DoctorTest(Workspace):
    def run_doctor(self, state: str, **extra) -> tuple[int, str]:
        def probe(token):
            return {"state": state, "source": token.source,
                    "detail": f"echo {token.value}", **extra}
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": SECRET}):
            status, _, text = notion_config.doctor(self.project, probe=probe)
        return status, text

    def test_each_state_names_the_case_and_the_next_step(self) -> None:
        status, text = self.run_doctor("VALID", integration="Praukron Sync", workspace="Qomero")
        self.assertEqual(status, 0)
        self.assertIn("VALID · Praukron Sync in Qomero", text)
        self.assertIn("praukron notion setup", text)
        for state, hint in (("INVALID", "refused"), ("UNREACHABLE", "Could not reach")):
            status, text = self.run_doctor(state)
            self.assertEqual(status, 1)
            self.assertIn(state, text)
            self.assertIn(hint, text)

    def test_missing_key(self) -> None:
        status, _, text = notion_config.doctor(self.project, environ={})
        self.assertEqual(status, 1)
        self.assertIn("not set", text)
        self.assertIn("MISSING", text)

    def test_token_value_never_reaches_output(self) -> None:
        for state in ("VALID", "INVALID", "UNREACHABLE"):
            _, text = self.run_doctor(state)
            self.assertNotIn(SECRET, text)

    def test_real_check_redacts_network_errors(self) -> None:
        token = notion_config.Token(SECRET, "environment")
        error = OSError(f"failed with {SECRET}")
        with mock.patch("urllib.request.urlopen", side_effect=error):
            report = notion_config.check(token)
        self.assertEqual(report["state"], "UNREACHABLE")
        self.assertNotIn(SECRET, str(report))

    def test_cli_json_does_not_print_the_token(self) -> None:
        out = io.StringIO()
        missing = {"state": "INVALID", "source": "environment", "detail": "HTTP 401"}
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": SECRET}), \
                mock.patch.object(notion_config, "check", return_value=missing), \
                mock.patch("praukron.compile.locate", return_value=self.project), \
                redirect_stdout(out):
            status = cli.main(["notion", "doctor", "--json"])
        self.assertEqual(status, 1)
        self.assertNotIn(SECRET, out.getvalue())


class TemplateTest(Workspace):
    def test_env_writes_once_and_never_overwrites(self) -> None:
        path = notion_config.project_file(self.project)
        self.assertTrue(notion_config.write_template(path))
        path.write_text("NOTION_TOKEN=kept\n", encoding="utf-8")
        self.assertFalse(notion_config.write_template(path))
        self.assertEqual(path.read_text(encoding="utf-8"), "NOTION_TOKEN=kept\n")

    def test_shipped_template_matches_the_runtime(self) -> None:
        self.assertEqual((ROOT / "templates" / "notion.env").read_text(encoding="utf-8"),
                         notion_config.TEMPLATE)


@unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
class InstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.target, True)
        subprocess.run(["git", "init", "-q", str(self.target)], check=True)

    def install(self) -> None:
        subprocess.run(["sh", str(ROOT / "install.sh"), "existing", str(self.target), "--no-link"],
                       check=True, capture_output=True)

    def test_installer_leaves_an_ignored_blank_key_file_and_keeps_it(self) -> None:
        self.install()
        env = self.target / ".praukron" / "notion.env"
        self.assertEqual(env.read_text(encoding="utf-8"), notion_config.TEMPLATE)
        self.assertIn("NOTION_TOKEN=\n", env.read_text(encoding="utf-8"))
        for name in ("notion.env", "notion.json", "notion.log"):
            ignored = subprocess.run(["git", "-C", str(self.target), "check-ignore", "-q",
                                      f".praukron/{name}"])
            self.assertEqual(ignored.returncode, 0, name)
        env.write_bytes(b"NOTION_TOKEN=ntn_kept\r\n# mine\n")
        ignore = self.target / ".praukron" / ".gitignore"
        ignore.write_text("own-line\n" + ignore.read_text(encoding="utf-8"), encoding="utf-8")
        self.install()
        self.assertEqual(env.read_bytes(), b"NOTION_TOKEN=ntn_kept\r\n# mine\n")
        text = ignore.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("own-line\n"))
        self.assertEqual(text.count("# praukron:start"), 1)


if __name__ == "__main__":
    unittest.main()
