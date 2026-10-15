"""Lifecycle planner and stale-plan regression tests (AC-T-ARCH-PLAN-01)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import archive, cli, compile as compiler, layout, respond, validate  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402


class ArchivePlanCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.rewrite("PHASES.md", "Status:\nACTIVE", "Status:\nCOMPLETE")

    def snapshot(self, root=None):
        root = root or self.authority
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()}

    def run_cli(self, *options):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-C", str(self.root), "archive", *options, "--json"])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return code, json.loads(out.getvalue())

    def add_phase(self, phase):
        path = self.authority / "PHASES.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("---\n\n# Gates", f"## {phase} — Follow-up\n\nOutcome:\nFinish.\n\n"
                            "Entry:\n- Ready.\n\nExit:\n- Finished.\n\nExit authority:\nT-TWO\n\n"
                            "Status:\nCOMPLETE\n\n---\n\n# Gates")
        path.write_text(text, encoding="utf-8")
        path = self.authority / "MODULES.md"
        path.write_text(path.read_text(encoding="utf-8") +
                        f"\n## M-{phase} — Follow-up\n- Phase: {phase}\n- Outcome: Finish.\n", encoding="utf-8")


class TestArchivePlan(ArchivePlanCase):
    def test_plan_reports_candidates_and_canonical_inventory_without_writing(self):
        extra = self.authority / "unparsed" / "notes.bin"
        extra.parent.mkdir()
        extra.write_bytes(b"\x00\xff\r\n")
        before = self.snapshot(self.root)
        directories = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        code, result = self.run_cli("plan", "P1")
        self.assertEqual(code, 0, result)
        self.assertFalse(result["written"])
        self.assertEqual(result["mode"], "nogit")
        self.assertEqual(result["version"], layout.version())
        candidates = {c["id"]: c for c in result["candidates"]}
        self.assertEqual(candidates["T-ONE"], {
            "id": "T-ONE", "kind": "task", "source": "TASKS.md",
            "destination": "archive/P1/TASKS.md", "action": "moves", "reason": None})
        self.assertEqual(candidates["T-TWO"]["action"], "stays")
        self.assertEqual(candidates["T-TWO"]["reason"], "it is not DONE")
        self.assertEqual(candidates["AC-T-ONE"]["action"], "moves")
        self.assertIn("working task", candidates["AC-T-TWO"]["reason"])
        inventory = []
        for path in self.authority.rglob("*"):
            entry = {"path": path.relative_to(self.authority).as_posix(),
                     "type": "file" if path.is_file() else "directory"}
            if path.is_file():
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            inventory.append(entry)
        inventory += [{"path": f"archive/P1/{name}", "type": "absent"}
                      for name in ("TASKS.md", "ACCEPTANCE.md")]
        payload = {"inventory": sorted(inventory, key=lambda e: e["path"]),
                   "selection": {"phase": "P1"}, "mode": "nogit", "version": layout.version()}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=True).encode("utf-8")).hexdigest()
        self.assertEqual(result["digest"], expected)
        self.assertEqual(result["digest"], archive.preview(self.root, "P1").digest)
        self.assertEqual(self.snapshot(self.root), before)
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), directories)
        self.assertGreater(result["movedBytes"], 0)

    def test_human_plan_and_refusal_include_digest_and_reason(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["-C", str(self.root), "archive", "plan", "P1"]), 0)
        self.assertRegex(out.getvalue(), r"Plan: [a-f0-9]{64}")
        self.assertIn("kept AC-T-TWO: used by working task", out.getvalue())
        with redirect_stderr(err):
            self.assertEqual(cli.main(["-C", str(self.root), "archive", "apply", "P1",
                                       "--plan", "0" * 64]), 1)
        self.assertIn("stale-plan:", err.getvalue())

    def test_contract_reasons_cover_inheritance_invariants_and_shared_users(self):
        self.rewrite("ACCEPTANCE.md", "## AC-T-TWO — Build on it",
                     "## AC-T-TWO — Build on it\n\nInherits: `AC-T-ONE`")
        candidates = {c.id: c for c in archive.preview(self.root, "P1").candidates}
        self.assertIn("inherited by contract", candidates["AC-T-ONE"].reason)
        self.rewrite("ACCEPTANCE.md", "Inherits: `AC-T-ONE`", "")
        self.rewrite("ACCEPTANCE.md", "## AC-T-TWO — Build on it", "### AC-NESTED\n\n## AC-T-TWO — Build on it")
        candidates = {c.id: c for c in archive.preview(self.root, "P1").candidates}
        self.assertEqual(candidates["AC-T-ONE"].reason, "holds an inherited invariant")
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: AC-T-ONE")
        candidates = {c.id: c for c in archive.preview(self.root, "P1").candidates}
        self.assertIn("T-TWO", candidates["AC-T-ONE"].reason)

    def test_a_record_rule_extends_planning_and_section_moves(self):
        class NoteRule:
            kind, filename, title = "note", "NOTES.md", "Notes"
            pattern = re.compile(r"(N-[A-Z]+)(?: — |$)")

            def candidates(self, context):
                yield archive.Candidate("N-ONE", self.kind, self.filename,
                                        context.selection.destination(self.filename))

        (self.authority / "NOTES.md").write_bytes(b"# Notes\n\n## N-ONE\nPreserved.\n")
        with mock.patch.object(archive, "RECORD_RULES", (*archive.RECORD_RULES, NoteRule())):
            result = archive.preview(self.root, "P1")
            note = next(c for c in result.candidates if c.kind == "note")
            self.assertTrue(note.moves)
            writes = archive._prepare_writes(self.root, result)
            self.assertIn("## N-ONE\nPreserved.", writes[self.authority / note.destination])

    def test_eligibility_uses_phase_without_inferring_domain_or_id(self):
        self.rewrite("TASKS.md", "## T-ONE: Lay the foundation",
                     "## T-ONE: Lay the foundation\n- Domain: operations")
        result = archive.preview(self.root, "P1")
        self.assertIn("T-ONE", result.tasks)
        with self.assertRaisesRegex(archive.ArchiveError, "not a phase"):
            archive.preview(self.root, "P-NONE")


class TestArchiveApply(ArchivePlanCase):
    def test_invalid_invocations_return_coded_json(self):
        before = self.snapshot(self.root)
        for options, expected in [(("plan",), "invalid-selection"),
                                  (("P1", "extra"), "invalid-selection"),
                                  (("plan", "P1", "--plan", "0" * 64), "invalid-option")]:
            with self.subTest(options=options):
                code, result = self.run_cli(*options)
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], expected)
                self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(self.root), before)
        with tempfile.TemporaryDirectory() as outside, redirect_stdout(io.StringIO()) as out:
            code = cli.main(["-C", outside, "archive", "plan", "P1", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "invalid-project")

    def test_stale_inventory_changes_are_refused_without_writes(self):
        for change in ("added", "removed", "renamed", "changed", "required-removed", "created-target", "directory"):
            with self.subTest(change=change):
                self.setUp()
                note = self.authority / "opaque.bin"
                note.write_bytes(b"original")
                digest = archive.preview(self.root, "P1").digest
                if change == "added":
                    (self.authority / "new-file").write_bytes(b"new")
                elif change == "removed":
                    note.unlink()
                elif change == "renamed":
                    note.rename(self.authority / "renamed.bin")
                elif change == "changed":
                    note.write_bytes(b"changed")
                elif change == "required-removed":
                    (self.authority / "TASKS.md").unlink()
                elif change == "created-target":
                    target = self.authority / "archive/P1/TASKS.md"
                    target.parent.mkdir(parents=True)
                    target.write_bytes(b"# Empty archive\n")
                else:
                    (self.authority / "empty-directory").mkdir()
                before = self.snapshot(self.root)
                with mock.patch.object(archive, "_replace") as replace, \
                        mock.patch.object(archive, "_write_recovery") as recovery:
                    code, result = self.run_cli("apply", "P1", "--plan", digest)
                self.assertEqual(code, 1, result)
                self.assertEqual(result["error"]["code"], "stale-plan")
                self.assertFalse(result["written"])
                replace.assert_not_called()
                recovery.assert_not_called()
                self.assertEqual(self.snapshot(self.root), before)

    def test_other_selection_mode_and_version_are_refused(self):
        self.add_phase("P2")
        digest = archive.preview(self.root, "P1").digest
        before = self.snapshot()
        for phase, patch in [("P2", mock.patch.object(archive, "archive_mode", return_value="nogit")),
                             ("P1", mock.patch.object(archive, "archive_mode", return_value="git")),
                             ("P1", mock.patch.object(layout, "version", return_value="99.0.0"))]:
            with self.subTest(phase=phase, patch=patch), patch:
                code, result = self.run_cli("apply", phase, "--plan", digest)
                self.assertEqual(code, 1, result)
                self.assertEqual(result["error"]["code"], "stale-plan")
                self.assertEqual(self.snapshot(), before)

    def test_inventory_is_recomputed_after_acquiring_the_lock(self):
        digest = archive.preview(self.root, "P1").digest
        real_lock = archive._lock

        def changed_before_lock(root):
            (self.authority / "arrived-while-waiting").write_bytes(b"new")
            return real_lock(root)

        real_digest = archive._plan_digest

        def locked_digest(root, result):
            self.assertTrue((root / layout.COMPILED_DIR / "respond.lock").is_file())
            return real_digest(root, result)

        with mock.patch.object(archive, "_lock", changed_before_lock), \
                mock.patch.object(archive, "_plan_digest", side_effect=locked_digest) as checked:
            code, result = self.run_cli("apply", "P1", "--plan", digest)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "stale-plan")
        checked.assert_called_once()
        self.assertFalse((self.authority / "archive").exists())

    def test_matching_digest_applies_and_refreshes(self):
        digest = archive.preview(self.root, "P1").digest
        before = archive._records(compiler.load(self.root))
        code, result = self.run_cli("apply", "P1", "--plan", digest)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["applied"])
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["digest"], digest)
        self.assertEqual(archive._records(compiler.load(self.root)), before)
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])
        self.assertTrue((self.root / layout.COMPILED_DIR / "dashboard.html").is_file())
        self.assertEqual(list((self.root / layout.RECOVERY_DIR).glob("archive-*.json")), [])

    def test_apply_requires_digest_and_dry_run_does_not_move(self):
        before = self.snapshot()
        code, result = self.run_cli("apply", "P1")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "missing-plan")
        digest = archive.preview(self.root, "P1").digest
        code, result = self.run_cli("apply", "P1", "--plan", digest, "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertFalse(result["applied"])
        self.assertEqual(self.snapshot(), before)

    def test_noop_plan_can_be_applied(self):
        archive.archive(self.root, "P1")
        digest = archive.preview(self.root, "P1").digest
        before = self.snapshot()
        code, result = self.run_cli("apply", "P1", "--plan", digest)
        self.assertEqual(code, 0, result)
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)

    def test_digest_apply_keeps_durable_recovery(self):
        digest = archive.preview(self.root, "P1").digest
        before = self.snapshot()
        real_replace = archive._replace
        calls = 0

        def interrupted(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit("interrupted apply")
            self.assertTrue(list((self.root / layout.RECOVERY_DIR).glob("archive-*.json")))
            return real_replace(path, data)

        with mock.patch.object(archive, "_replace", interrupted), self.assertRaises(SystemExit):
            archive.archive(self.root, "P1", digest=digest)
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=digest)
        self.assertEqual(error.exception.code, "recovered-write")
        self.assertEqual(self.snapshot(), before)

    def test_refresh_failure_reports_written_without_reapplying(self):
        digest = archive.preview(self.root, "P1").digest
        with mock.patch.object(respond, "refresh", side_effect=OSError("refresh failed")):
            code, result = self.run_cli("apply", "P1", "--plan", digest)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "refresh-failed")
        self.assertTrue(result["written"])
        self.assertFalse(result["refreshed"])
        self.assertEqual(compiler.load(self.root).task("T-ONE").source.file, "archive/P1/TASKS.md")
        respond.refresh(self.root)
        self.assertTrue((self.root / layout.COMPILED_DIR / "dashboard.html").is_file())

    def test_shared_contract_across_three_runs_moves_only_with_last_working_user(self):
        self.add_phase("P2")
        self.add_phase("P3")
        for task, phase in (("T-TWO", "P2"), ("T-THREE", "P3")):
            path = self.authority / "TASKS.md"
            text = path.read_text(encoding="utf-8")
            start = text.index(f"## {task}:")
            end = text.find("## ", start + 3)
            end = len(text) if end < 0 else end
            section = text[start:end].replace("- Module: M-FOUNDATION", f"- Module: M-{phase}")
            section = section.replace("- Status: TODO", "- Status: DONE").replace(f"AC-{task}", "AC-T-ONE")
            path.write_text(text[:start] + section + text[end:], encoding="utf-8")
        before = archive._records(compiler.load(self.root))
        for phase in ("P1", "P2", "P3"):
            digest = archive.preview(self.root, phase).digest
            result = archive.archive(self.root, phase, digest=digest)
            self.assertEqual(result.contracts, ["AC-T-ONE"] if phase == "P3" else [])
            self.assertEqual(archive._records(compiler.load(self.root)), before)
        self.assertEqual(compiler.load(self.root).contracts["AC-T-ONE"].source.file, "archive/P3/ACCEPTANCE.md")

    def test_lf_crlf_and_unicode_survive_digest_apply(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                for name in ("TASKS.md", "ACCEPTANCE.md"):
                    path = self.authority / name
                    data = path.read_bytes().replace(b"\r\n", b"\n")
                    data += "\n<!-- Tiếng Việt -->\n".encode("utf-8")
                    path.write_bytes(data.replace(b"\n", newline))
                digest = archive.preview(self.root, "P1").digest
                archive.archive(self.root, "P1", digest=digest)
                for name in ("TASKS.md", "ACCEPTANCE.md"):
                    for path in (self.authority / name, self.authority / "archive/P1" / name):
                        data = path.read_bytes()
                        self.assertIn(newline, data)
                        if newline == b"\n":
                            self.assertNotIn(b"\r\n", data)
                        else:
                            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
                self.assertIn("Tiếng Việt", (self.authority / "TASKS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
