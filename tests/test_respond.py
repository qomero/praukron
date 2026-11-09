"""`praukron respond`: the one governed write into the chronicle (T-RESPOND-01, ADR-055)."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
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
    def test_locks_keep_live_owners_reclaim_dead_ones_and_release_by_token(self) -> None:
        path = self.dir / layout.COMPILED_DIR / "respond.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.getpid()} live\n", encoding="utf-8")
        old = time.time() - respond._STALE_SECONDS - 1
        os.utime(path, (old, old))
        with mock.patch.object(respond.time, "monotonic", side_effect=(0, 11)):
            with self.assertRaisesRegex(respond.RespondError, "try again"):
                respond._lock(self.dir)
        self.assertTrue(path.exists())

        path.write_text("12345 dead\n", encoding="utf-8")
        with mock.patch.object(respond, "_pid_alive", return_value=False):
            lock = respond._lock(self.dir)
        self.assertNotEqual(path.read_text(encoding="utf-8"), "12345 dead\n")
        lock.release()

        lock = respond._lock(self.dir)
        path.write_text(f"{os.getpid()} another-writer\n", encoding="utf-8")
        lock.release()
        self.assertTrue(path.exists())
        path.unlink()

    def test_response_rolls_back_an_interrupted_archive_before_writing(self) -> None:
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        responses.write_text("# Broken response\n", encoding="utf-8")
        recovery = self.dir / layout.RECOVERY_DIR
        recovery.mkdir(parents=True)
        (recovery / "archive-test.json").write_text(json.dumps({"files": [{
            "path": f"{layout.AUTHORITY_DIR}/RESPONSES.md",
            "bytes": base64.b64encode(original).decode("ascii"),
        }]}), encoding="utf-8")
        with self.assertRaisesRegex(respond.RespondError, "interrupted archive was rolled back"):
            respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], recompile=False)
        self.assertEqual(responses.read_bytes(), original)
        self.assertEqual(list(recovery.glob("archive-*.json")), [])

    def test_one_response_is_appended_verbatim_and_nothing_else_changes(self) -> None:
        """AC-T-RESPOND-01-01."""
        before = self.snapshot()
        text = "Đồng ý, nhưng:\n\n  - giữ 7 ngày\n> không phải trích dẫn"
        code, out, err = self.run_cli("respond", "A-2", "confirm", "--date", "2026-10-03", stdin=text + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("Recorded R-3", out)
        responses = self.authority / "RESPONSES.md"
        self.assertTrue(responses.read_text(encoding="utf-8").startswith(RESPONSES))
        self.assertTrue(responses.read_text(encoding="utf-8").endswith(
            "\n## R-3: A-2 CONFIRM\n- By: thien-owner\n- Date: 2026-10-03\n- Via: cli\n\n"
            "> Đồng ý, nhưng:\n>\n>   - giữ 7 ngày\n> > không phải trích dẫn\n"))
        self.load()
        self.assertEqual(self.project.responses[-1].text, text)
        after = self.snapshot()
        self.assertEqual({p for p in before if before[p] != after.get(p)}, {responses})
        file = self.dir / "answer.txt"
        file.write_text("Use the staging copy.", encoding="utf-8")
        code, out, _ = self.run_cli("respond", "T-TWO", "guide", "--text-file", str(file),
                                    "--date", "2026-10-03")
        self.assertEqual(code, 0)
        self.load()
        self.assertEqual((self.project.responses[-1].id, self.project.responses[-1].text), ("R-4", "Use the staging copy."))

    def cli_process(self, *args: str, stdin: bytes) -> subprocess.CompletedProcess:
        bin_dir = self.dir / "test-bin"
        bin_dir.mkdir(exist_ok=True)
        if os.name == "nt":
            (bin_dir / "gh.cmd").write_text("@echo off\r\necho thien-owner\r\n", encoding="utf-8")
        else:
            gh = bin_dir / "gh"
            gh.write_text("#!/bin/sh\necho thien-owner\n", encoding="utf-8")
            gh.chmod(0o755)
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        return subprocess.run([sys.executable, str(Path(cli.__file__).resolve().parents[2] / ".praukron/praukron"),
                               "-C", str(self.dir), *args], input=stdin, capture_output=True, env=env)

    def test_stdin_is_read_as_utf8_whatever_the_console_encoding(self) -> None:
        """AC-T-RESPOND-STDIN-01-01."""
        text = "Đồng ý, giữ 7 ngày — tiếp tục"
        for label, raw in (("plain", text.encode("utf-8")), ("bom", text.encode("utf-8-sig"))):
            with self.subTest(label=label):
                result = self.cli_process("respond", "A-2", "confirm", "--date", "2026-10-03",
                                          stdin=raw)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.load()
                self.assertEqual(self.project.responses[-1].text, text)
        out, err = io.StringIO(), io.StringIO()
        with io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="cp1252", errors="surrogateescape") as stdin:
            with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin):
                code = cli.main(["-C", str(self.dir), "respond", "A-2", "confirm"])
        self.assertEqual(code, 0, err.getvalue())
        self.load()
        self.assertEqual(self.project.responses[-1].text, text)

    def test_invalid_utf8_stdin_is_refused_without_writing(self) -> None:
        """AC-T-RESPOND-STDIN-01-02."""
        before = self.snapshot()
        result = self.cli_process("respond", "A-2", "confirm", stdin=b"caf\xe9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid UTF-8", result.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_refusals_leave_the_file_byte_identical(self) -> None:
        """AC-T-RESPOND-01-02."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        for args, stdin, message in (
            (("A-99", "confirm"), "ok", "not an assumption"),
            (("T-NOPE", "guide"), "ok", "not a task"),
            (("T-TWO", "confirm"), "ok", "a task takes GUIDE"),
            (("A-1", "revise", "--by", "Thien"), "seven", "--by is no longer accepted"),
            (("A-1", "revise"), "  \n", "has no text"),
            (("X-1", "guide"), "hi", "neither an assumption nor a task"),
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
                respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], recompile=False)
        self.assertIn("nothing was recorded", str(raised.exception))
        self.assertEqual(responses.read_bytes(), original)
        with mock.patch.object(respond.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(respond.RespondError):
                respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], recompile=False)
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
        ids = respond.append(self.dir, [respond.Item("A-1", "REVISE", "Seven days.")], date="2026-10-03")
        self.assertEqual(ids, ["R-3"])
        self.assertTrue(responses.read_bytes().startswith(earlier))
        compiled = self.dir / layout.COMPILED_DIR
        for name in ("project.json", "task-graph.mmd"):
            self.assertTrue((compiled / name).is_file(), name)
        self.assertFalse((compiled / "dashboard.html").exists())  # ADR-086
        data = json.loads((compiled / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(data["responses"][-1]["text"], "Seven days.")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["-C", str(self.dir), "dashboard"]), 0)
        self.assertIn("Seven days.", (compiled / "dashboard.html").read_text(encoding="utf-8"))

    def test_a_batch_is_all_or_nothing(self) -> None:
        """AC-T-RESPOND-01-05."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        with self.assertRaises(respond.RespondError):
            respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok"),
                                      respond.Item("A-404", "CONFIRM", "ok")], recompile=False)
        self.assertEqual(responses.read_bytes(), original)
        batch = self.dir / "batch.json"
        batch.write_text(json.dumps([
            {"target": "A-1", "action": "confirm", "text": "OK"},
            {"target": "A-2", "action": "revise", "text": "Sales read all clients in their region."},
            {"target": "T-TWO", "action": "guide", "text": "Ask before touching production."},
        ]), encoding="utf-8")
        code, out, err = self.run_cli("respond", "--batch", str(batch),
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
        ids = respond.append(self.dir, [respond.Item("A-1", "CONFIRM", "ok")], recompile=False)
        self.assertEqual(ids, ["R-1"])
        self.load()
        self.assertEqual(self.project.responses[0].text, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
