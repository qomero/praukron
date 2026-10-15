"""task update regression tests (AC-T-WRITE-UPDATE-01)."""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, compile as compiler, layout, mutate, task_update  # noqa: E402
from praukron.parse import _fields  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402


class UpdateCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.tasks = self.authority / "TASKS.md"

    def snapshot(self):
        return {p.relative_to(self.authority): p.read_bytes()
                for p in self.authority.rglob("*") if p.is_file()}

    def revision(self, task="T-TWO"):
        return mutate.section(self.tasks.read_bytes(), task).revision

    def convert(self, newline):
        for name in ("TASKS.md", "ACCEPTANCE.md"):
            path = self.authority / name
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))

    def pass_criteria(self):
        self.rewrite("ACCEPTANCE.md", "Then they stand.\n  `TEST` · `NOT_RUN`", "Then they stand.\n  `TEST` · `PASS`")

    def run_cli(self, payload, *options):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            code = cli.main(["-C", str(self.root), "task", "update", "--input", "-", *options])
        self.assertEqual(err.getvalue(), "")
        return code, json.loads(out.getvalue())

    def update(self, changes, task="T-TWO", revision=None, *options):
        return self.run_cli({"id": task, "expectRevision": revision or self.revision(task), "set": changes}, *options)


class TestTaskUpdate(UpdateCase):
    def test_each_allowed_field_changes_only_its_lines_lf_and_crlf(self):
        cases = [
            ({"status": "BLOCKED"}, "- Status: BLOCKED", 0),
            ({"owner": "Rhea"}, "- Owner: Rhea", 0),
            ({"claimed": "2026-10-01"}, "- Claimed: 2026-10-01", 1),
            ({"evidence": "Ran it twice."}, "- Evidence: Ran it twice.", 0),
        ]
        for newline in (b"\n", b"\r\n"):
            self.convert(newline)
            original = self.snapshot()
            for changes, line, added in cases:
                with self.subTest(newline=newline, changes=changes):
                    code, result = self.update(changes)
                    self.assertEqual(code, 0, result)
                    self.assertTrue(result["written"] and result["refreshed"])
                    after = self.tasks.read_bytes()
                    self.assertEqual(result["revision"], self.revision())
                    if newline == b"\r\n":
                        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
                    old = original[Path("TASKS.md")].replace(b"\r\n", b"\n").split(b"\n")
                    new = after.replace(b"\r\n", b"\n").split(b"\n")
                    self.assertEqual([x for x in new if x not in old], [line.encode()])
                    self.assertEqual(len(new) - len(old), added)
                    for path, data in original.items():
                        if path.name not in ("TASKS.md", "INDEX.md"):
                            self.assertEqual((self.authority / path).read_bytes(), data)
                    self.tasks.write_bytes(original[Path("TASKS.md")])

    def test_wip_requires_owner_and_claims_today(self):
        before = self.snapshot()
        code, result = self.update({"status": "WIP"})
        self.assertEqual(result["error"]["code"], "owner-required")
        self.assertEqual(self.snapshot(), before)
        code, result = self.update({"status": "WIP", "owner": "Rhea"})
        self.assertEqual(code, 0, result)
        task = compiler.load(self.root).task("T-TWO")
        self.assertEqual((task.status, task.owner, task.claimed), ("WIP", "Rhea", dt.date.today().isoformat()))
        code, result = self.update({"status": "WIP", "owner": "Rhea", "claimed": "2026-10-01"}, "T-THREE")
        self.assertEqual(code, 0, result)
        self.assertEqual(compiler.load(self.root).task("T-THREE").claimed, "2026-10-01")

    def test_evidence_wraps_and_round_trips(self):
        text = " ".join(f"word{i}" for i in range(60)) + " Đã xong."
        code, result = self.update({"evidence": text})
        self.assertEqual(code, 0, result)
        self.assertEqual(compiler.load(self.root).task("T-TWO").evidence, text)
        section = mutate.section(self.tasks.read_bytes(), "T-TWO").data.decode()
        self.assertGreater(section.count("\n  word"), 0)
        self.assertEqual(_fields(section)["Governed by"], "ADR-001")
        code, result = self.update({"evidence": "first\nsecond"})
        self.assertEqual(code, 0, result)
        self.assertEqual(compiler.load(self.root).task("T-TWO").evidence, "first second")

    def test_done_with_passing_criteria_and_evidence(self):
        self.pass_criteria()
        code, result = self.update({"status": "DONE", "evidence": "Walls stand."})
        self.assertEqual(code, 0, result)
        self.assertEqual(compiler.load(self.root).task("T-TWO").status, "DONE")

    def test_refusals_change_nothing(self):
        refused = [
            ({"module": "M-OTHER"}, "T-TWO", "unknown-field"),
            ({"validation": "HUMAN_VERIFIED"}, "T-TWO", "unknown-field"),
            ({"title": "x"}, "T-TWO", "unknown-field"),
            ({"status": "DONE", "evidence": "Claimed done."}, "T-TWO", "unresolved-acceptance"),
            ({"status": "DONE"}, "T-ONE", "no-change"),
            ({"evidence": "a\n\nb"}, "T-TWO", "invalid-value"),
            ({"evidence": "—"}, "T-TWO", "invalid-value"),
            ({"evidence": "double  space"}, "T-TWO", "invalid-value"),
            ({"owner": "two\nlines"}, "T-TWO", "invalid-value"),
            ({}, "T-TWO", "empty-set"),
            ({"status": "WIP", "owner": "x"}, "T-NOPE", "record-not-found"),
        ]
        for changes, task, code_name in refused:
            with self.subTest(changes=changes):
                before = self.snapshot()
                revision = self.revision(task) if task != "T-NOPE" else "0" * 16
                code, result = self.update(changes, task, revision)
                self.assertEqual(code, 1)
                self.assertFalse(result["ok"] or result["written"])
                self.assertEqual(result["error"]["code"], code_name)
                self.assertEqual(self.snapshot(), before)

    def test_done_without_evidence_is_refused(self):
        self.pass_criteria()
        before = self.snapshot()
        code, result = self.update({"status": "DONE"})
        self.assertEqual(result["error"]["code"], "evidence-required")
        self.assertEqual(self.snapshot(), before)

    def test_owner_held_task_cannot_be_marked_done(self):
        self.pass_criteria()
        self.rewrite("TASKS.md", "- Owner: unassigned\n- AC: AC-T-TWO",
                     "- Owner: unassigned\n- Authority: owner\n- AC: AC-T-TWO")
        before = self.snapshot()
        code, result = self.update({"status": "DONE", "evidence": "Done."})
        self.assertEqual(result["error"]["code"], "owner-held")
        self.assertEqual(self.snapshot(), before)

    def test_archived_task_is_refused(self):
        target = self.authority / "archive/P1/TASKS.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"# Tasks\n\n## T-ARCH: Old work\n- Status: DONE\n- Module: M-FOUNDATION\n"
                           b"- Validation: SYNTHETIC\n- Dependencies: none\n- Owner: someone\n"
                           b"- AC: AC-X\n- Evidence: Done.\n")
        (self.authority / "archive/P1/ACCEPTANCE.md").write_bytes(
            b"# Acceptance\n\n## AC-X - Old\n\n- `AC-X-01` - Then it holds. `TEST` \xc2\xb7 `PASS`\n")
        revision = mutate.section(target.read_bytes(), "T-ARCH").revision
        before = self.snapshot()
        code, result = self.update({"owner": "Rhea"}, "T-ARCH", revision)
        self.assertEqual(result["error"]["code"], "archived-record")
        self.assertEqual(self.snapshot(), before)

    def test_a_b_a_that_changes_the_record_bytes_is_refused_with_current_section(self):
        # Revisions are content hashes: an A->B->A whose bytes differ from what the caller saw is stale.
        r0 = self.revision()
        # WIP claims the task, so returning to TODO still leaves a changed section.
        self.assertEqual(self.update({"status": "WIP", "owner": "Rhea"})[0], 0)
        self.assertEqual(self.update({"status": "TODO"})[0], 0)
        self.assertEqual(compiler.load(self.root).task("T-TWO").status, "TODO")
        before = self.snapshot()
        code, result = self.update({"owner": "Rhea"}, "T-TWO", r0)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "stale-revision")
        self.assertEqual(result["error"]["currentRevision"], self.revision())
        self.assertIn("## T-TWO: Build on it", result["error"]["section"])
        self.assertEqual(self.snapshot(), before)

    def test_byte_identical_a_b_a_is_not_a_lost_update(self):
        # The section equals what the caller saw, so its revision still matches and the update applies.
        self.assertEqual(self.update({"status": "BLOCKED"})[0], 0)
        r0 = self.revision()
        self.assertEqual(self.update({"status": "TODO"})[0], 0)
        self.assertEqual(self.update({"status": "BLOCKED"})[0], 0)
        self.assertEqual(self.revision(), r0)
        code, result = self.update({"owner": "Rhea"}, "T-TWO", r0)
        self.assertEqual(code, 0, result)

    def test_evidence_the_parser_would_alter_is_refused(self):
        for newline in (b"\n", b"\r\n"):
            self.convert(newline)
            for value in ("—Đã hoàn thành—", "Done —", "— Done", "—"):
                with self.subTest(newline=newline, value=value):
                    before = self.snapshot()
                    code, result = self.update({"evidence": value})
                    self.assertEqual(code, 1)
                    self.assertEqual(result["error"]["code"], "invalid-value")
                    self.assertEqual(self.snapshot(), before)
        self.convert(b"\n")
        text = "Đã xong — kiểm tra lại"
        self.assertEqual(self.update({"evidence": text})[0], 0)
        self.assertEqual(compiler.load(self.root).task("T-TWO").evidence, text)

    def test_done_rules_apply_only_to_the_transition_into_done(self):
        self.rewrite("TASKS.md", "- Owner: unassigned\n- AC: AC-T-TWO",
                     "- Owner: unassigned\n- Authority: owner\n- AC: AC-T-TWO")
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: DONE")
        for changes in ({"evidence": "Reworded."}, {"owner": "Rhea"}):
            with self.subTest(changes=changes):
                code, result = self.update(changes)
                self.assertEqual(code, 0, result)
        task = compiler.load(self.root).task("T-TWO")
        self.assertEqual((task.status, task.owner, task.evidence), ("DONE", "Rhea", "Reworded."))

    def test_dry_run_writes_nothing_and_schema_prints(self):
        before = self.snapshot()
        code, result = self.update({"owner": "Rhea"}, "T-TWO", None, "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(result["dryRun"] and not result["written"])
        self.assertEqual(self.snapshot(), before)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["task", "update", "--schema"]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["schema"], task_update.SCHEMA)
        self.assertEqual(printed["example"], task_update.EXAMPLE)

    def test_missing_field_is_inserted_in_order(self):
        code, result = self.update({"claimed": "2026-10-02"}, "T-ONE")
        self.assertEqual(code, 0, result)
        section = mutate.section(self.tasks.read_bytes(), "T-ONE").data.decode().replace("\r\n", "\n")
        self.assertIn("- Owner: someone\n- Claimed: 2026-10-02\n- AC: AC-T-ONE", section)


if __name__ == "__main__":
    unittest.main()
