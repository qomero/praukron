"""GitHub-login identity and owner VERIFY (T-OWNER-VERIFY-PORT-01)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, compile as compiler, respond, serve, validate  # noqa: E402
from praukron.respond import identity as real_identity  # noqa: E402
from test_assumptions import AssumptionCase, RESPONSES  # noqa: E402
from test_serve import ServeCase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OWNERS_ADR = """# ADR-003: Owners
- Date: 2026-10-09
- Status: ACCEPTED
- Authority: owner
- Owners: thien-owner
- Decision: The owner is the GitHub user thien-owner.
"""


def owner_task(case: AssumptionCase) -> None:
    tasks = case.authority / "TASKS.md"
    old = "## T-TWO: Build on it\n- Status: TODO\n- Module: M-FOUNDATION\n"
    tasks.write_text(tasks.read_text(encoding="utf-8").replace(old, old + "- Authority: owner\n", 1),
                     encoding="utf-8")


class OwnerCase(AssumptionCase):
    def setUp(self) -> None:
        super().setUp()
        owner_task(self)
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        self.load()

    def run_cli(self, *args: str, stdin: str = "", tty: bool = False,
                answers: tuple[str, ...] = ()) -> tuple[int, str, str]:
        out, err, stream = io.StringIO(), io.StringIO(), io.StringIO(stdin)
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stream), \
                mock.patch.object(stream, "isatty", return_value=tty), \
                mock.patch.object(out, "isatty", return_value=tty), \
                mock.patch("builtins.input", side_effect=list(answers)):
            code = cli.main(["-C", str(self.dir), *args])
        return code, out.getvalue(), err.getvalue()

    def codes(self, severity: str = "error") -> list[str]:
        return [finding.code for finding in validate.check(self.project) if finding.severity == severity]


class TestIdentity(OwnerCase):
    def test_by_is_login_and_typed_by_is_refused(self) -> None:
        code, _, error = self.run_cli("respond", "T-TWO", "guide", stdin="Use staging.")
        self.assertEqual(code, 0, error)
        self.load()
        self.assertEqual(self.project.responses[-1].by, "thien-owner")
        before = (self.authority / "RESPONSES.md").read_bytes()
        code, _, error = self.run_cli("respond", "T-TWO", "guide", "--by", "Mallory", stdin="hi")
        self.assertEqual(code, 1)
        self.assertIn("--by is no longer accepted", error)
        self.assertEqual((self.authority / "RESPONSES.md").read_bytes(), before)

    def test_identity_uses_gh_and_refuses_missing_login(self) -> None:
        with mock.patch("praukron.respond.shutil.which", return_value=None):
            with self.assertRaisesRegex(respond.RespondError, "not installed"):
                real_identity()
        done = subprocess.CompletedProcess([], 0, stdout="thien-owner\n", stderr="")
        with mock.patch("praukron.respond.shutil.which", return_value="/usr/bin/gh"), \
                mock.patch("praukron.respond.subprocess.run", return_value=done) as run:
            self.assertEqual(real_identity(), "thien-owner")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/gh", "api", "user", "--jq", ".login"])
        bad = subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in")
        with mock.patch("praukron.respond.shutil.which", return_value="/usr/bin/gh"), \
                mock.patch("praukron.respond.subprocess.run", return_value=bad):
            with self.assertRaisesRegex(respond.RespondError, "gh auth login"):
                real_identity()


class TestOwnersAndVerify(OwnerCase):
    def test_owners_compile_and_invalid_or_missing_are_reported(self) -> None:
        respond.append(self.dir, [respond.Item("T-TWO", "VERIFY", "Checked.")], recompile=False, verify=True)
        self.load()
        data = compiler.as_json(self.project)
        self.assertEqual(data["owners"], {"logins": ["thien-owner"], "decision": "ADR-003"})
        self.assertTrue(data["responses"][-1]["fromOwner"])
        (self.authority / "ADR" / "ADR-003.md").write_text(
            OWNERS_ADR.replace("thien-owner", "not a login!, ok-one"), encoding="utf-8")
        self.load()
        self.assertIn("invalid-owners", self.codes())
        self.assertEqual(self.project.owners, ["ok-one"])
        (self.authority / "ADR" / "ADR-003.md").write_text(
            OWNERS_ADR.replace("- Owners: thien-owner\n", ""), encoding="utf-8")
        self.load()
        self.assertIn("owners-missing", self.codes("warning"))

    def test_human_verified_requires_named_owner_verify_and_verify_target_is_limited(self) -> None:
        tasks = self.authority / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace(
            "- Authority: owner\n- Validation: UNTESTED", "- Authority: owner\n- Validation: HUMAN_VERIFIED", 1),
                         encoding="utf-8")
        self.load()
        self.assertIn("verification-without-owner", self.codes())
        for target in ("T-ONE", "A-1", "T-NOPE"):
            with self.subTest(target=target), self.assertRaisesRegex(respond.RespondError, "only to an Authority"):
                respond.append(self.dir, [respond.Item(target, "VERIFY", "ok")], recompile=False, verify=True)
        (self.authority / "ADR" / "ADR-003.md").write_text(
            OWNERS_ADR.replace("thien-owner", "someone-else"), encoding="utf-8")
        respond.append(self.dir, [respond.Item("T-TWO", "VERIFY", "Checked.")], recompile=False, verify=True)
        self.load()
        self.assertIn("verification-without-owner", self.codes())
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        self.load()
        self.assertNotIn("verification-without-owner", self.codes())
        self.write(responses=RESPONSES + "\n## R-3: T-ONE VERIFY\n- By: thien-owner\n- Date: 2026-10-09\n"
                   "- Via: dashboard\n\n> ok\n")
        self.assertIn("invalid-response-action", self.codes())

    def test_verify_requires_interactive_confirmation_and_keeps_relay_via(self) -> None:
        responses = self.authority / "RESPONSES.md"
        before = responses.read_bytes()
        code, _, error = self.run_cli("respond", "T-TWO", "verify", stdin="Checked.")
        self.assertEqual(code, 1)
        self.assertIn("only interactively", error)
        code, _, error = self.run_cli("respond", "T-TWO", "verify", tty=True, answers=("Checked.", "T-ONE"))
        self.assertEqual(code, 1)
        self.assertIn("not confirmed", error)
        self.assertEqual(responses.read_bytes(), before)
        code, out, error = self.run_cli("respond", "T-TWO", "verify", tty=True, answers=("Checked.", "T-TWO"))
        self.assertEqual(code, 0, error)
        self.assertIn("as GitHub user thien-owner", out)
        self.load()
        self.assertEqual((self.project.responses[-1].action, self.project.responses[-1].via),
                         ("VERIFY", "cli (terminal)"))
        code, _, error = self.run_cli("respond", "T-TWO", "guide", "--via", "relayed by coordinator from chat",
                                      stdin="Owner said staging only.")
        self.assertEqual(code, 0, error)
        self.load()
        self.assertEqual((self.project.responses[-1].by, self.project.responses[-1].via),
                         ("thien-owner", "relayed by coordinator from chat"))


class TestServedIdentity(ServeCase):
    def test_page_uses_login_ignores_posted_name_and_offers_verify(self) -> None:
        owner_task(self)
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        status, page = self.request(f"/?token={self.token}")
        self.assertEqual(status, 200)
        self.assertIn("Recording as GitHub user <strong>thien-owner</strong>", page)
        self.assertNotIn('<input type="text" class="review-by"', page)
        self.assertIn('data-target="T-TWO" data-verify="1"', page)
        status, result = self.post({"by": "Mallory", "items": [
            {"target": "T-TWO", "action": "VERIFY", "text": "Checked."}]})
        self.assertEqual(status, 200, result)
        self.load()
        self.assertEqual((self.project.responses[-1].by, self.project.responses[-1].action),
                         ("thien-owner", "VERIFY"))
        with mock.patch("praukron.respond.identity", side_effect=respond.RespondError("no GitHub login")):
            page = serve.page(self.dir)
        self.assertIn("No GitHub login", page)
        self.assertIn('<button type="button" disabled>Submit review</button>', page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
