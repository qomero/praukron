"""`praukron respond`: the one governed write into the chronicle (T-RESPOND-01, ADR-055)."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, compile as compiler, layout, respond, validate  # noqa: E402
from test_assumptions import AssumptionCase, RESPONSES  # noqa: E402


class RespondCase(AssumptionCase):
    def snapshot(self) -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in self.authority.rglob("*")
                if p.is_file() and p.name != "INDEX.md"}

    def run_cli(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(["-C", str(self.dir), *args])
        return code, out.getvalue(), err.getvalue()


class TestRespond(RespondCase):
    def test_one_response_is_appended_verbatim_and_nothing_else_changes(self) -> None:
        """AC-T-RESPOND-01-01."""
        before = self.snapshot()
        text = "Đồng ý, nhưng:\n\n  - giữ 7 ngày\n> không phải trích dẫn"
        code, out, err = self.run_cli("respond", "A-2", "confirm", "--by", "Thien (owner)",
                                      "--date", "2026-10-03", stdin=text + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("Recorded R-3", out)
        responses = self.authority / "RESPONSES.md"
        self.assertTrue(responses.read_text(encoding="utf-8").startswith(RESPONSES))
        self.assertTrue(responses.read_text(encoding="utf-8").endswith(
            "\n## R-3: A-2 CONFIRM\n- By: Thien (owner)\n- Date: 2026-10-03\n- Via: cli\n\n"
            "> Đồng ý, nhưng:\n>\n>   - giữ 7 ngày\n> > không phải trích dẫn\n"))
        self.load()
        self.assertEqual(self.project.responses[-1].text, text)
        after = self.snapshot()
        self.assertEqual({p for p in before if before[p] != after.get(p)}, {responses})
        file = self.dir / "answer.txt"
        file.write_text("Use the staging copy.", encoding="utf-8")
        code, out, _ = self.run_cli("respond", "T-TWO", "guide", "--by", "Thien", "--text-file", str(file),
                                    "--date", "2026-10-03")
        self.assertEqual(code, 0)
        self.load()
        self.assertEqual((self.project.responses[-1].id, self.project.responses[-1].text), ("R-4", "Use the staging copy."))

    def test_refusals_leave_the_file_byte_identical(self) -> None:
        """AC-T-RESPOND-01-02."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        for args, stdin, message in (
            (("A-99", "confirm", "--by", "Thien"), "ok", "not an assumption"),
            (("T-NOPE", "guide", "--by", "Thien"), "ok", "not a task"),
            (("T-TWO", "confirm", "--by", "Thien"), "ok", "a task takes GUIDE"),
            (("A-1", "revise"), "seven", "needs --by"),
            (("A-1", "revise", "--by", "  "), "seven", "needs --by"),
            (("A-1", "revise", "--by", "Thien"), "  \n", "has no text"),
            (("X-1", "guide", "--by", "Thien"), "hi", "neither an assumption nor a task"),
        ):
            with self.subTest(args=args):
                code, _, err = self.run_cli("respond", *args, stdin=stdin)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertEqual(responses.read_bytes(), original)

    def test_a_write_that_breaks_validation_is_undone(self) -> None:
        """AC-T-RESPOND-01-03."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        real = validate.check

        def poisoned(project):
            found = real(project)
            if len(project.responses) > 2:
                found.append(validate.Finding("error", "injected", "made up for the test", "RESPONSES.md#R-3"))
            return found

        with mock.patch.object(validate, "check", poisoned):
            with self.assertRaises(respond.RespondError) as raised:
                respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], "Thien", recompile=False)
        self.assertIn("nothing was recorded", str(raised.exception))
        self.assertEqual(responses.read_bytes(), original)
        with mock.patch.object(respond.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(respond.RespondError):
                respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], "Thien", recompile=False)
        self.assertEqual(responses.read_bytes(), original)
        self.assertEqual([p.name for p in self.authority.glob(".respond-*")], [])
        self.assertFalse((self.dir / layout.COMPILED_DIR / "respond.lock").exists())
        source = Path(respond.__file__).read_text(encoding="utf-8")
        self.assertIn("os.replace(temporary, path)", source)
        self.assertIn("os.O_CREAT | os.O_EXCL", source)
        self.assertIn("COMPILED_DIR", source.split("def _lock", 1)[1].split("def _replace", 1)[0])

    def test_success_refreshes_views_and_keeps_earlier_entries(self) -> None:
        """AC-T-RESPOND-01-04."""
        responses = self.authority / "RESPONSES.md"
        earlier = responses.read_bytes()
        ids = respond.append(self.dir, [respond.Item("A-1", "REVISE", "Seven days.")], "Thien", date="2026-10-03")
        self.assertEqual(ids, ["R-3"])
        self.assertTrue(responses.read_bytes().startswith(earlier))
        compiled = self.dir / layout.COMPILED_DIR
        for name in ("project.json", "dashboard.html", "task-graph.mmd"):
            self.assertTrue((compiled / name).is_file(), name)
        data = json.loads((compiled / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(data["responses"][-1]["text"], "Seven days.")
        self.assertIn("Seven days.", (compiled / "dashboard.html").read_text(encoding="utf-8"))

    def test_a_batch_is_all_or_nothing(self) -> None:
        """AC-T-RESPOND-01-05."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        with self.assertRaises(respond.RespondError):
            respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok"),
                                      respond.Item("A-404", "CONFIRM", "ok")], "Thien", recompile=False)
        self.assertEqual(responses.read_bytes(), original)
        batch = self.dir / "batch.json"
        batch.write_text(json.dumps([
            {"target": "A-1", "action": "confirm", "text": "OK"},
            {"target": "A-2", "action": "revise", "text": "Sales read all clients in their region."},
            {"target": "T-TWO", "action": "guide", "text": "Ask before touching production."},
        ]), encoding="utf-8")
        code, out, err = self.run_cli("respond", "--batch", str(batch), "--by", "Thien",
                                      "--via", "dashboard", "--date", "2026-10-03")
        self.assertEqual(code, 0, err)
        self.load()
        tail = self.project.responses[-3:]
        self.assertEqual([(r.id, r.target, r.action, r.via) for r in tail], [
            ("R-3", "A-1", "CONFIRM", "dashboard"), ("R-4", "A-2", "REVISE", "dashboard"),
            ("R-5", "T-TWO", "GUIDE", "dashboard")])
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_a_missing_file_is_created_and_removed_again_on_failure(self) -> None:
        (self.authority / "RESPONSES.md").unlink()
        self.write(assumptions=(self.authority / "ASSUMPTIONS.md").read_text(encoding="utf-8")
                   .replace("Responses: R-1", "Responses: none"))
        ids = respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], "Thien", recompile=False)
        self.assertEqual(ids, ["R-1"])
        self.load()
        self.assertEqual(self.project.responses[0].text, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
