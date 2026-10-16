"""The owner is the gh login, and VERIFY is how an owner task is verified (T-OWNER-VERIFY-01, ADR-060)."""

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

from praukron import compile as compiler, respond, serve, validate  # noqa: E402
from praukron.respond import identity as real_identity  # noqa: E402  (before any test patches it)
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
    """Make T-TWO an Authority: owner task."""
    tasks = case.authority / "TASKS.md"
    text = tasks.read_text()
    old = "## T-TWO: Build on it\n- Status: TODO\n- Module: M-FOUNDATION\n"
    assert old in text
    tasks.write_text(text.replace(old, old + "- Authority: owner\n", 1))


class OwnerCase(AssumptionCase):
    def setUp(self) -> None:
        super().setUp()
        owner_task(self)
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR)
        self.load()

    def run_cli(self, *args: str, stdin: str = "", tty: bool = False,
                answers: tuple[str, ...] = ()) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        stream = io.StringIO(stdin)
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stream), \
                mock.patch.object(stream, "isatty", return_value=tty), \
                mock.patch.object(out, "isatty", return_value=tty), \
                mock.patch("builtins.input", side_effect=list(answers)):
            from praukron import cli
            code = cli.main(["-C", str(self.dir), *args])
        return code, out.getvalue(), err.getvalue()

    def codes(self, severity: str = "error") -> list[str]:
        return [f.code for f in validate.check(self.project) if f.severity == severity]

    def verified_tasks_text(self) -> None:
        tasks = self.authority / "TASKS.md"
        tasks.write_text(tasks.read_text().replace(
            "- Authority: owner\n- Validation: UNTESTED", "- Authority: owner\n- Validation: HUMAN_VERIFIED", 1))


class TestIdentity(OwnerCase):
    def test_by_is_the_gh_login_and_no_login_refuses(self) -> None:
        """AC-T-OWNER-VERIFY-01-01."""
        code, _, err = self.run_cli("respond", "T-TWO", "guide", stdin="Use staging.")
        self.assertEqual(code, 0, err)
        self.load()
        self.assertEqual(self.project.responses[-1].by, "thien-owner")
        responses = self.authority / "RESPONSES.md"
        before = responses.read_bytes()
        code, _, err = self.run_cli("respond", "T-TWO", "guide", "--by", "Mallory", stdin="hi")
        self.assertEqual(code, 1)
        self.assertIn("--by is no longer accepted", err)
        with mock.patch("praukron.respond.identity",
                        side_effect=respond.RespondError("no GitHub login: run `gh auth login`")):
            code, _, err = self.run_cli("respond", "T-TWO", "guide", stdin="hi")
        self.assertEqual(code, 1)
        self.assertIn("no GitHub login", err)
        self.assertEqual(responses.read_bytes(), before)

    def test_identity_asks_gh_and_refuses_without_it(self) -> None:
        """AC-T-OWNER-VERIFY-01-01 (the gh call itself)."""
        with mock.patch("praukron.respond.shutil.which", return_value=None):
            with self.assertRaises(respond.RespondError) as raised:
                real_identity()
        self.assertIn("not installed", str(raised.exception))
        done = subprocess.CompletedProcess([], 0, stdout="thien-owner\n", stderr="")
        with mock.patch("praukron.respond.shutil.which", return_value="/usr/bin/gh"), \
                mock.patch("praukron.respond.subprocess.run", return_value=done) as run:
            self.assertEqual(real_identity(), "thien-owner")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/gh", "api", "user", "--jq", ".login"])
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in")
        with mock.patch("praukron.respond.shutil.which", return_value="/usr/bin/gh"), \
                mock.patch("praukron.respond.subprocess.run", return_value=failed):
            with self.assertRaises(respond.RespondError) as raised:
                real_identity()
        self.assertIn("gh auth login", str(raised.exception))


class TestServedIdentity(ServeCase):
    def test_the_page_records_the_login_not_a_typed_name(self) -> None:
        """AC-T-OWNER-VERIFY-01-01 (served page)."""
        status, page = self.request(f"/?token={self.token}")
        self.assertIn("Recording as GitHub user <strong>thien-owner</strong>", page)
        self.assertNotIn('class="review-by" aria-label="Your name"', page)
        status, result = self.post({"by": "Mallory", "items": [
            {"target": "T-TWO", "action": "GUIDE", "text": "Staging only."}]})
        self.assertEqual(status, 200, result)
        self.load()
        self.assertEqual(self.project.responses[-1].by, "thien-owner")
        with mock.patch("praukron.respond.identity", side_effect=respond.RespondError("no GitHub login")):
            page = serve.page(self.dir)
            status, result = self.post({"items": [{"target": "T-TWO", "action": "GUIDE", "text": "x"}]})
        self.assertIn("No GitHub login: run <code>gh auth login</code>", page)
        self.assertIn("<button type=\"button\" disabled>Submit review</button>", page)
        self.assertEqual(status, 400)
        self.assertIn("no GitHub login", result["error"])


class TestOwners(OwnerCase):
    def test_the_project_reports_whether_a_response_is_from_an_owner(self) -> None:
        """AC-T-OWNER-VERIFY-01-02."""
        respond.append(self.dir, [respond.Item("T-TWO", "VERIFY", "Checked on staging.")],
                       recompile=False, verify=True)
        self.load()
        data = compiler.as_json(self.project)
        self.assertEqual(data["owners"], {"logins": ["thien-owner"], "decision": "ADR-003"})
        by_id = {r["id"]: r["fromOwner"] for r in data["responses"]}
        self.assertEqual(by_id, {"R-1": False, "R-2": False, "R-3": True})
        self.assertEqual(self.codes("warning").count("owners-missing"), 0)

    def test_malformed_or_missing_owners_are_reported(self) -> None:
        """AC-T-OWNER-VERIFY-01-02 (reporting)."""
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR.replace("thien-owner", "not a login!, ok-one"))
        self.load()
        self.assertIn("invalid-owners", self.codes())
        self.assertEqual(self.project.owners, ["ok-one"])
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR.replace("- Owners: thien-owner\n", ""))
        self.load()
        self.assertIn("owners-missing", self.codes("warning"))
        self.assertNotIn("invalid-owners", self.codes())


class TestVerify(OwnerCase):
    def test_human_verified_needs_a_verify_from_an_owner(self) -> None:
        """AC-T-OWNER-VERIFY-01-03."""
        self.verified_tasks_text()
        self.load()
        self.assertIn("verification-without-owner", self.codes())
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR.replace("thien-owner", "someone-else"))
        respond.append(self.dir, [respond.Item("T-TWO", "VERIFY", "Looked at it.")], recompile=False, verify=True)
        self.load()
        self.assertIn("verification-without-owner", self.codes(), "a non-owner's VERIFY does not count")
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR)
        self.load()
        self.assertNotIn("verification-without-owner", self.codes())
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_verify_on_any_other_target_is_refused(self) -> None:
        """AC-T-OWNER-VERIFY-01-03 (targets)."""
        responses = self.authority / "RESPONSES.md"
        before = responses.read_bytes()
        for target in ("T-ONE", "A-1", "T-NOPE"):
            with self.subTest(target=target), self.assertRaises(respond.RespondError) as raised:
                respond.append(self.dir, [respond.Item(target, "VERIFY", "ok")], recompile=False, verify=True)
            self.assertIn("only to an Authority: owner task", str(raised.exception))
        self.assertEqual(responses.read_bytes(), before)
        self.write(responses=RESPONSES + "\n## R-3: T-ONE VERIFY\n- By: thien-owner\n- Date: 2026-10-09\n"
                   "- Via: dashboard\n\n> ok\n")
        self.assertIn("invalid-response-action", self.codes())


class TestInteractiveOnly(OwnerCase):
    def test_a_non_interactive_verify_is_refused(self) -> None:
        """AC-T-OWNER-VERIFY-01-04."""
        responses = self.authority / "RESPONSES.md"
        before = responses.read_bytes()
        code, _, err = self.run_cli("respond", "T-TWO", "verify", stdin="Checked.")
        self.assertEqual(code, 1)
        self.assertIn("only interactively", err)
        with self.assertRaises(respond.RespondError) as raised:
            respond.append(self.dir, [respond.Item("T-TWO", "VERIFY", "ok")], recompile=False)
        self.assertIn("never by an agent", str(raised.exception))
        batch = self.dir / "batch.json"
        batch.write_text(json.dumps([{"target": "T-TWO", "action": "VERIFY", "text": "ok"}]))
        code, _, err = self.run_cli("respond", "--batch", str(batch))
        self.assertEqual(code, 1)
        self.assertIn("only interactively", err)
        code, _, err = self.run_cli("respond", "T-TWO", "verify", tty=True, answers=("Checked.", "T-ONE"))
        self.assertEqual(code, 1)
        self.assertIn("not confirmed", err)
        self.assertEqual(responses.read_bytes(), before)

    def test_a_terminal_confirmation_records_verify(self) -> None:
        """AC-T-OWNER-VERIFY-01-04 (the terminal path)."""
        code, out, err = self.run_cli("respond", "T-TWO", "verify", tty=True,
                                      answers=("Ran it on staging.", "T-TWO"))
        self.assertEqual(code, 0, err)
        self.assertIn("as GitHub user thien-owner", out)
        self.load()
        last = self.project.responses[-1]
        self.assertEqual((last.target, last.action, last.by, last.via, last.text),
                         ("T-TWO", "VERIFY", "thien-owner", "cli (terminal)", "Ran it on staging."))

    def test_the_served_page_offers_verify_and_guidance_forbids_agents(self) -> None:
        """AC-T-OWNER-VERIFY-01-04 (served page and guidance)."""
        page = serve.page(self.dir)
        needs = page.split('<aside class="needs"', 1)[1].split("</aside>", 1)[0]
        self.assertIn('data-target="T-TWO" data-verify="1"', needs)
        self.assertIn("if (e.verify)", page)
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("Never issue `VERIFY`", agents)
        for name in ("README.md", "RESPONSES.md"):
            self.assertIn("never issues `VERIFY`" if name == "README.md" else "An agent never issues it.",
                          (ROOT / "templates" / "chronicle" / name).read_text())


class TestCompatibility(AssumptionCase):
    def test_typed_names_still_parse_and_nothing_is_rewritten(self) -> None:
        """AC-T-OWNER-VERIFY-01-05."""
        responses = self.authority / "RESPONSES.md"
        before = responses.read_bytes()
        self.load()
        self.assertEqual([r.by for r in self.project.responses], ["Thien (owner)", "Thien (owner)"])
        self.assertEqual(validate.errors(validate.check(self.project)), [])
        compiler.write(self.dir, self.project)
        self.assertEqual(responses.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
