"""Git, backup, restore and forced-kill evidence (AC-T-ARCH-RECOVER-01)."""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import archive, archive_recovery, cli, compile as compiler, layout, mutate, validate
from test_archive_plan import ArchivePlanCase


class RecoveryCase(ArchivePlanCase):
    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def init_git(self, *, autocrlf="false"):
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        self.git("init")
        self.git("config", "user.name", "Archive test")
        self.git("config", "user.email", "archive@example.invalid")
        self.git("config", "core.autocrlf", autocrlf)
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("add", "--", layout.AUTHORITY_DIR)
        self.git("commit", "-m", "Fixture")

    def run_commands(self, commands):
        for argv in commands:
            result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, (argv, result.stderr))

    def add_all_record_files(self):
        records = {
            "ASSUMPTIONS.md": ("A-CLOSED", "# Assumptions\n\n## A-CLOSED: Closed assumption\n"
                "- Status: WITHDRAWN\n- Tasks: T-ONE\n- Impact: LOW\n"
                "- Assumption: Keep the Unicode café.\n- Basis: Evidence.\n- Reconciled by: T-ONE\n"),
            "TECH_DEBT.md": ("TD-CLOSED", "# Debt\n\n## TD-CLOSED: Closed debt\n"
                "- Status: RESOLVED\n- Linked tasks: T-ONE\n- Resolution: Fixed.\n"
                "- Debt: An old gap.\n- Exit condition: Gap closed.\n"),
            "TRACE.md": ("EV-DONE", "# Trace\n\n## EV-DONE: Finished\n"
                "- Type: tool-call\n- Task: T-ONE\n- Outcome: success\n"),
            "JOURNAL.md": ("2026-10-15 — Completed", "# Journal\n\n## 2026-10-15 — Completed\n"
                "T-ONE finished. Tiếng Việt.\n"),
        }
        for filename, (_, text) in records.items():
            (self.authority / filename).write_bytes(text.encode("utf-8"))
        (self.authority / "RESPONSES.md").write_bytes(b"# Responses\n")
        return records

    def extra_rules(self):
        records = self.add_all_record_files()
        rules = []
        for filename, (record_id, _) in records.items():
            if any(rule.filename == filename for rule in archive.RECORD_RULES):
                continue
            class Rule(archive.IdRule):
                def candidates(self, context):
                    yield archive.Candidate(self.record_id, self.kind, self.filename,
                                            context.selection.destination(self.filename))
            rule = Rule()
            rule.filename, rule.record_id = filename, record_id
            rule.kind = filename
            rule.title = filename
            rule.pattern = re.compile("(" + re.escape(record_id) + ")(?:[: ].*|$)")
            rules.append(rule)
        return (*archive.RECORD_RULES, *rules)


class TestGitRecovery(RecoveryCase):
    def test_autocrlf_uses_exact_backups_in_all_three_git_states(self):
        for newline in (b"\n", b"\r\n"):
            for state in ("unstaged", "staged", "committed"):
                with self.subTest(newline=newline, state=state):
                    self.setUp()
                    for path in self.authority.rglob("*.md"):
                        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                    self.init_git(autocrlf="true")
                    self.assertEqual(self.git("status", "--porcelain", "--", layout.AUTHORITY_DIR), "")
                    before = self.snapshot()
                    plan = archive.preview(self.root, "P1")
                    self.assertEqual(plan.mode, "nogit")
                    result = archive.archive(self.root, "P1", digest=plan.digest)
                    self.assertTrue(Path(result.recovery["backup"]).is_dir())
                    self.assertNotIn("unstaged", result.recovery["commands"])
                    if state != "unstaged":
                        self.git("add", "--", layout.AUTHORITY_DIR)
                    if state == "committed":
                        self.git("commit", "-m", "Archive with checkout conversion enabled")
                    self.run_commands(result.recovery["commands"]["backup"])
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse((self.authority / "archive/P1/TASKS.md").exists())

    def test_attribute_conversion_uses_backups_for_sources_and_prospective_destinations(self):
        for pattern in (f"{layout.AUTHORITY_DIR}/**", f"{layout.AUTHORITY_DIR}/archive/**"):
            for attributes in ("text eol=crlf", "text eol=lf", "filter=archive-smudge", "ident",
                               "working-tree-encoding=UTF-8"):
                with self.subTest(pattern=pattern, attributes=attributes):
                    self.setUp()
                    (self.root / ".gitattributes").write_bytes(f"{pattern} {attributes}\n".encode())
                    self.init_git()
                    self.git("add", ".gitattributes")
                    self.git("commit", "-m", "Checkout attributes")
                    before = self.snapshot()
                    plan = archive.preview(self.root, "P1")
                    self.assertEqual(plan.mode, "nogit")
                    result = archive.archive(self.root, "P1", digest=plan.digest)
                    self.run_commands(result.recovery["commands"]["backup"])
                    self.assertEqual(self.snapshot(), before)

    def test_input_conversion_and_new_configuration_invalidate_git_plan(self):
        self.init_git()
        plan = archive.preview(self.root, "P1")
        self.assertEqual(plan.mode, "git")
        self.git("config", "core.autocrlf", "input")
        self.assertEqual(archive.preview(self.root, "P1").mode, "nogit")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=plan.digest)
        self.assertEqual(error.exception.code, "stale-plan")
        self.assertEqual(self.snapshot(), before)

    def test_disabled_conversion_attributes_keep_git_recovery(self):
        (self.root / ".gitattributes").write_bytes(
            f"{layout.AUTHORITY_DIR}/** -text -filter -ident -working-tree-encoding\n".encode())
        self.init_git()
        self.assertEqual(archive.preview(self.root, "P1").mode, "git")

    def test_case_insensitive_index_lookup_uses_snapshot_aliases(self):
        probe = self.authority / "CASE-PROBE"
        probe.write_bytes(b"probe")
        insensitive = probe.with_name("case-probe").exists()
        probe.unlink()
        if not insensitive:
            self.skipTest("case-sensitive filesystem")
        self.init_git()
        self.git("config", "core.ignorecase", "true")
        original = (self.authority / "TASKS.md").read_bytes()
        path = self.authority / "TASKS.md"
        temporary = path.with_name("case-rename.tmp")
        path.rename(temporary)
        temporary.rename(path.with_name("tasks.md"))
        self.assertEqual(self.git("status", "--porcelain", "--", layout.AUTHORITY_DIR), "")
        result = archive.archive(self.root, "P1")
        self.assertEqual(result.mode, "git")
        self.run_commands(result.recovery["commands"]["unstaged"])
        self.assertEqual((self.authority / "TASKS.md").read_bytes(), original)
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_partial_ignores_select_backup_and_restore_every_byte(self):
        for ignored in ("TASKS.md", "archive/", "opaque.txt"):
            with self.subTest(ignored=ignored):
                self.setUp()
                self.init_git()
                if ignored == "TASKS.md":
                    self.git("rm", "--cached", "--", f"{layout.AUTHORITY_DIR}/TASKS.md")
                if ignored == "opaque.txt":
                    (self.authority / ignored).write_bytes(b"read by the snapshot\r\n")
                (self.root / ".gitignore").write_bytes(f"{layout.AUTHORITY_DIR}/{ignored}\n".encode())
                self.git("add", "--", ".gitignore")
                self.git("commit", "-m", "Ignore part of the chronicle")
                self.assertEqual(self.git("status", "--porcelain", "--", layout.AUTHORITY_DIR), "")
                before = self.snapshot()
                result = archive.preview(self.root, "P1")
                self.assertEqual(result.mode, "nogit")
                applied = archive.archive(self.root, "P1", digest=result.digest)
                self.assertNotIn("changedTrackedFiles", applied.recovery)
                self.assertTrue(Path(applied.recovery["backup"]).is_dir())
                self.run_commands(applied.recovery["commands"]["backup"])
                self.assertEqual(self.snapshot(), before)

    def test_mode_is_per_selection_and_mode_change_invalidates_digest(self):
        self.add_phase("P2")
        self.init_git()
        original = archive.preview(self.root, "P1")
        (self.root / ".gitignore").write_bytes(f"{layout.AUTHORITY_DIR}/archive/P1/\n".encode())
        self.assertEqual(archive.preview(self.root, "P1").mode, "nogit")
        self.assertEqual(archive.preview(self.root, "P2").mode, "git")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=original.digest)
        self.assertEqual(error.exception.code, "stale-plan")
        self.assertEqual(self.snapshot(), before)

    def test_dirty_check_sees_ignored_files_appearing_after_snapshot(self):
        self.init_git()
        real_digest = archive._plan_digest

        def ignored_after_snapshot(root, result):
            digest = real_digest(root, result)
            (root / ".gitignore").write_bytes(f"{layout.AUTHORITY_DIR}/secret.txt\n".encode())
            (self.authority / "secret.txt").write_bytes(b"ignored")
            return digest

        with mock.patch.object(archive, "_plan_digest", ignored_after_snapshot), \
                mock.patch.object(archive, "_replace") as replaced, \
                self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1")
        self.assertEqual(error.exception.code, "dirty-chronicle")
        replaced.assert_not_called()

    def test_nested_repositories_use_the_chronicle_git_context(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed")

        def git_at(directory, *args):
            result = subprocess.run(["git", "-C", str(directory), *args], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        for outer in (False, True):
            for inner in (layout.HOME_DIR, layout.AUTHORITY_DIR):
                with self.subTest(outer=outer, inner=inner):
                    self.setUp()
                    if outer:
                        git_at(self.root, "init")
                        (self.root / ".gitignore").write_bytes(f"{layout.HOME_DIR}/\n".encode())
                        git_at(self.root, "add", ".gitignore")
                        git_at(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                               "-c", "commit.gpgsign=false", "commit", "-m", "Outer")
                    nested = self.root / inner
                    git_at(nested, "init")
                    git_at(nested, "config", "core.autocrlf", "false")
                    git_at(nested, "config", "user.name", "Test")
                    git_at(nested, "config", "user.email", "test@example.invalid")
                    git_at(nested, "config", "commit.gpgsign", "false")
                    git_at(nested, "add", ".")
                    git_at(nested, "commit", "-m", "Inner fixture")
                    before = {p.name: p.read_bytes() for p in self.authority.glob("*.md")}
                    plan = archive.preview(self.root, "P1")
                    self.assertEqual(plan.mode, "git")
                    self.assertFalse(any(".git" in Path(name).parts for name in plan.snapshot.files))
                    path = self.authority / "HANDOFF.md"
                    path.write_bytes(path.read_bytes() + b"dirty\n")
                    with self.assertRaises(archive.ArchiveError) as error:
                        archive.archive(self.root, "P1")
                    self.assertEqual(error.exception.code, "dirty-chronicle")
                    path.write_bytes(before[path.name])
                    result = archive.archive(self.root, "P1", digest=plan.digest)
                    self.run_commands(result.recovery["commands"]["unstaged"])
                    self.assertEqual({p.name: p.read_bytes() for p in self.authority.glob("*.md")}, before)
                    self.assertFalse((self.authority / "archive").exists())
                    self.assertEqual(git_at(self.authority, "status", "--porcelain", "--", "."), b"")

    def test_git_exit_128_falls_back_without_crashing(self):
        inside = subprocess.CompletedProcess([], 0, b"true\n", b"")
        unavailable = subprocess.CompletedProcess([], 128, b"", b"fatal: not a git repository")
        with mock.patch.object(archive_recovery, "_git", side_effect=[inside, unavailable]):
            self.assertEqual(archive.archive_mode(self.root), "nogit")

    def test_mode_detection_missing_git_ignored_and_untracked(self):
        self.assertEqual(archive.archive_mode(self.root), "nogit")
        with mock.patch.object(archive_recovery, "_git", side_effect=FileNotFoundError):
            self.assertEqual(archive.archive_mode(self.root), "nogit")
        self.init_git()
        self.assertEqual(archive.preview(self.root, "P1").mode, "git")
        (self.root / ".gitignore").write_bytes((layout.AUTHORITY_DIR + "/\n").encode())
        self.assertEqual(archive.archive_mode(self.root), "nogit")
        self.git("rm", "-r", "--cached", "--", layout.AUTHORITY_DIR)
        self.assertEqual(archive.archive_mode(self.root), "nogit")
        (self.root / ".gitignore").unlink()
        self.assertEqual(archive.archive_mode(self.root), "git")

    def test_dirty_staged_untracked_and_restore_refused_without_writes(self):
        for change in ("modified", "staged", "untracked"):
            with self.subTest(change=change):
                self.setUp()
                self.init_git()
                path = self.authority / ("new notes.md" if change == "untracked" else "TASKS.md")
                path.write_bytes(path.read_bytes() + b"\n<!-- Changed -->\n" if path.exists() else b"notes")
                if change == "staged":
                    self.git("add", "--", layout.AUTHORITY_DIR)
                before = self.snapshot()
                with mock.patch.object(archive, "_replace") as replace, \
                        mock.patch.object(archive_recovery, "backup") as backup:
                    code, result = self.run_cli("P1")
                self.assertEqual(code, 1, result)
                self.assertEqual(result["error"]["code"], "dirty-chronicle")
                self.assertFalse(result["written"])
                replace.assert_not_called()
                backup.assert_not_called()
                self.assertEqual(self.snapshot(), before)
        self.git("add", "--", layout.AUTHORITY_DIR)
        self.git("commit", "-m", "Later edit")
        archive.archive(self.root, "P1")
        with self.assertRaises(archive.ArchiveError) as refusal:
            archive.restore(self.root, "P1")
        self.assertEqual(refusal.exception.code, "dirty-chronicle")

    def test_clean_apply_lists_new_files_and_never_commits(self):
        self.init_git()
        head = self.git("rev-parse", "HEAD")
        before = self.snapshot()
        plan = archive.preview(self.root, "P1")
        result = archive.archive(self.root, "P1", digest=plan.digest)
        self.assertEqual(result.mode, "git")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        info = result.as_json()["recovery"]
        self.assertEqual(set(info["createdFiles"]), {
            f"{layout.AUTHORITY_DIR}/archive/P1/TASKS.md",
            f"{layout.AUTHORITY_DIR}/archive/P1/ACCEPTANCE.md"})
        self.assertEqual(set(info["changedTrackedFiles"]), {
            f"{layout.AUTHORITY_DIR}/TASKS.md", f"{layout.AUTHORITY_DIR}/ACCEPTANCE.md"})
        self.assertIn("Suggested commit", result.render(True))
        self.assertNotIn("git clean", result.render(True))
        self.run_commands(info["commands"]["unstaged"])
        self.assertEqual(self.snapshot(), before)

    def test_unstaged_staged_and_committed_recovery_restore_every_byte(self):
        for state in ("unstaged", "staged", "committed"):
            with self.subTest(state=state):
                self.setUp()
                self.init_git()
                before = self.snapshot()
                directories = sorted(p.relative_to(self.authority).as_posix()
                                     for p in self.authority.rglob("*") if p.is_dir())
                result = archive.archive(self.root, "P1")
                if state != "unstaged":
                    self.git("add", "--", layout.AUTHORITY_DIR)
                if state == "committed":
                    self.git("commit", "-m", result.recovery["suggestedCommitMessage"])
                    commit = self.git("rev-parse", "HEAD")
                    commands = [[commit if v == "<archive-commit>" else v for v in argv]
                                for argv in result.recovery["commands"][state]]
                    # Avoid an editor while executing the printed revert procedure.
                    with mock.patch.dict(os.environ, {"GIT_EDITOR": "true"}):
                        self.run_commands(commands)
                else:
                    self.run_commands(result.recovery["commands"][state])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(sorted(p.relative_to(self.authority).as_posix()
                                        for p in self.authority.rglob("*") if p.is_dir()), directories)
                with self.assertRaises(archive.ArchiveError) as error:
                    archive.restore(self.root, "P1")
                self.assertEqual(error.exception.code, "unknown-archive")
                self.assertEqual(self.git("status", "--porcelain", "--", layout.AUTHORITY_DIR), "")

    def test_restore_is_one_reviewable_change_after_later_commits(self):
        self.init_git()
        archive.archive(self.root, "P1")
        self.git("add", "--", layout.AUTHORITY_DIR)
        self.git("commit", "-m", "Archive")
        path = self.authority / "TASKS.md"
        path.write_bytes(path.read_bytes() + b"\n<!-- Later work -->\n")
        self.git("add", "--", layout.AUTHORITY_DIR)
        self.git("commit", "-m", "Later work")
        head = self.git("rev-parse", "HEAD")
        before = self.snapshot()
        result = archive.restore(self.root, "P1")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertIn(b"Later work", path.read_bytes())
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])
        self.run_commands(result.recovery["commands"]["unstaged"])
        self.assertEqual(self.snapshot(), before)

    def test_ignored_chronicle_uses_backup_despite_git_and_unrelated_dirt(self):
        self.init_git()
        (self.root / ".gitignore").write_bytes((layout.AUTHORITY_DIR + "/\n").encode())
        result = archive.archive(self.root, "P1")
        self.assertEqual(result.mode, "nogit")
        self.assertTrue(Path(result.recovery["backup"]).is_dir())

    def test_dirty_chronicle_refused_even_when_no_records_move(self):
        self.init_git()
        archive.archive(self.root, "P1")
        self.git("add", "--", layout.AUTHORITY_DIR)
        self.git("commit", "-m", "Archive")
        path = self.authority / "TASKS.md"
        path.write_bytes(path.read_bytes() + b"\n<!-- Later edit -->\n")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1")
        self.assertEqual(error.exception.code, "dirty-chronicle")
        self.assertEqual(self.snapshot(), before)


class TestBackupRecovery(RecoveryCase):
    def test_private_backups_ignore_themselves_in_git(self):
        self.init_git()
        self.git("rm", "-r", "--cached", "--", layout.AUTHORITY_DIR)
        (self.root / ".gitignore").write_bytes(f"{layout.AUTHORITY_DIR}/\n".encode())
        self.git("add", ".gitignore")
        self.git("commit", "-m", "Keep the chronicle private")
        result = archive.archive(self.root, "P1")
        self.assertEqual(result.mode, "nogit")
        self.assertEqual((self.root / layout.HOME_DIR / "backup/.gitignore").read_bytes(), b"*\n")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        archive.restore(self.root, "P1")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")

    def test_backup_precedes_replace_and_restores_every_byte_and_absent_file(self):
        before = self.snapshot()
        replace = archive._replace
        paths = []

        def checked(path, data):
            manifests = list((self.root / ".praukron/backup").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_bytes())
            for entry in manifest["files"]:
                saved = manifests[0].parent / entry["path"]
                if entry["absent"]:
                    self.assertFalse(saved.exists())
                else:
                    relative = (self.root / entry["path"]).relative_to(self.authority).as_posix()
                    self.assertEqual(saved.read_bytes(), before[relative])
            paths.append(path)
            return replace(path, data)

        with mock.patch.object(archive, "_replace", checked):
            result = archive.archive(self.root, "P1")
        self.assertEqual(len(paths), 4)
        saved = self.snapshot(Path(result.recovery["backup"]))
        self.run_commands(result.recovery["commands"]["backup"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.snapshot(Path(result.recovery["backup"])), saved)
        archive.archive(self.root, "P1")
        self.assertEqual(len(list((self.root / ".praukron/backup").glob("*/manifest.json"))), 2)

    def test_backup_failure_never_replaces_authority(self):
        before = self.snapshot()
        with mock.patch.object(archive_recovery, "_replace", side_effect=OSError("disk full")), \
                mock.patch.object(archive, "_replace") as replace:
            with self.assertRaises(OSError):
                archive.archive(self.root, "P1")
        replace.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_restore_backup_recreates_deleted_archive_files(self):
        archive.archive(self.root, "P1")
        before = self.snapshot()
        result = archive.restore(self.root, "P1")
        self.run_commands(result.recovery["commands"]["backup"])
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Path(result.recovery["backup"]).is_dir())

    def test_printed_powershell_backup_command_runs(self):
        if os.name != "nt":
            self.skipTest("PowerShell command formatting is Windows-specific")
        # Exercise the actual rendered command, including PowerShell's call
        # operator and quoting; argv-only execution would miss those errors.
        before = self.snapshot()
        result = archive.archive(self.root, "P1")
        argv = result.recovery["commands"]["backup"][0]
        output = subprocess.run(["powershell", "-NoProfile", "-Command", archive_recovery.command(argv)],
                                capture_output=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(self.snapshot(), before)


class TestArchiveRestore(RecoveryCase):
    def test_restore_uses_registered_full_heading_locator_without_eligibility(self):
        archive.archive(self.root, "P1")
        heading = "2026-10-15 — Closed work — extra detail"
        journal = self.authority / "archive/P1/JOURNAL.md"
        journal.write_bytes(f"# Journal\n\n## {heading}\nT-ONE finished.\n".encode())

        class JournalRule:
            filename, kind = "JOURNAL.md", "journal"

            def sections(self, text):
                return archive.section_locations(text)

            def candidates(self, context):
                raise AssertionError("restore must not recheck eligibility")

        rule = JournalRule()
        with mock.patch.object(archive, "RECORD_RULES", (*archive.RECORD_RULES, rule)), \
                mock.patch.object(rule, "sections", wraps=rule.sections) as located:
            result = archive.restore(self.root, "P1")
        self.assertEqual(located.call_count, 2)
        self.assertIn(heading, [c.id for c in result.candidates])
        self.assertIn(f"## {heading}".encode(), (self.authority / "JOURNAL.md").read_bytes())

    def test_duplicate_journal_locator_refused_without_writes(self):
        archive.archive(self.root, "P1")
        journal = self.authority / "archive/P1/JOURNAL.md"
        journal.write_bytes(b"# Journal\n\n## 2026-10-15 - Same\nFirst.\n\n## 2026-10-15 - Same\nSecond.\n")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.restore(self.root, "P1")
        self.assertEqual(error.exception.code, "id-collision")
        self.assertEqual(self.snapshot(), before)

    def test_case_insensitive_restore_reuses_captured_file_and_directory_spellings(self):
        probe = self.authority / "CASE-PROBE"
        probe.write_bytes(b"probe")
        insensitive = probe.with_name("case-probe").exists()
        probe.unlink()
        if not insensitive:
            self.skipTest("case-sensitive filesystem")
        archive.archive(self.root, "P1")
        for directory in (self.authority, self.authority / "archive/P1"):
            for name in ("TASKS.md", "ACCEPTANCE.md"):
                path = directory / name
                intermediate = path.with_name("case-rename.tmp")
                path.rename(intermediate)
                intermediate.rename(path.with_name(name.lower()))
        directory = self.authority / "archive/P1"
        directory.rename(directory.with_name("phase-rename"))
        directory.with_name("phase-rename").rename(directory.with_name("p1"))
        before = self.snapshot()
        result = archive.restore(self.root, "P1")
        self.assertEqual(result.created_files, [])
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])
        manifest = json.loads((Path(result.recovery["backup"]) / "manifest.json").read_bytes())
        self.assertTrue(all(not entry["absent"] for entry in manifest["files"]))
        self.run_commands(result.recovery["commands"]["backup"])
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == "posix", "POSIX symlinked restore root")
    def test_restore_through_symlink_uses_resolved_snapshot_identity(self):
        archive.archive(self.root, "P1")
        alias = self.root.parent / (self.root.name + "-alias")
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        before = self.snapshot()
        result = archive.restore(alias, "P1")
        self.assertEqual(result.snapshot.authority, self.authority.resolve())
        self.assertEqual(result.created_files, [])
        self.assertEqual(validate.errors(validate.check(compiler.load(alias))), [])
        self.run_commands(result.recovery["commands"]["backup"])
        self.assertEqual(self.snapshot(), before)

    def test_restore_uses_converted_working_file_line_endings(self):
        for archived_newline, working_newline in ((b"\n", b"\r\n"), (b"\r\n", b"\n")):
            with self.subTest(working_newline=working_newline):
                self.setUp()
                for name in ("TASKS.md", "ACCEPTANCE.md"):
                    path = self.authority / name
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", archived_newline))
                archive.archive(self.root, "P1")
                for name in ("TASKS.md", "ACCEPTANCE.md"):
                    path = self.authority / name
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", working_newline))
                archive.restore(self.root, "P1")
                for name in ("TASKS.md", "ACCEPTANCE.md"):
                    data = (self.authority / name).read_bytes()
                    self.assertIn(working_newline, data)
                    if working_newline == b"\n":
                        self.assertNotIn(b"\r\n", data)
                    else:
                        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
                self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_empty_archive_is_not_silently_restored(self):
        directory = self.authority / "archive/P1"
        directory.mkdir(parents=True)
        for headers_only in (False, True):
            with self.subTest(headers_only=headers_only):
                if headers_only:
                    (directory / "TASKS.md").write_bytes(b"# Tasks\n")
                before = self.snapshot()
                with self.assertRaises(archive.ArchiveError) as error:
                    archive.restore(self.root, "P1")
                self.assertEqual(error.exception.code, "unknown-archive")
                self.assertEqual(self.snapshot(), before)

    def test_restore_preserves_later_edits_and_new_dependency(self):
        archive.archive(self.root, "P1")
        self.rewrite("TASKS.md", "- Dependencies: T-TWO", "- Dependencies: T-TWO, T-ONE")
        path = self.authority / "TASKS.md"
        path.write_bytes(path.read_bytes() + b"\n<!-- Unrelated later edit -->\n")
        later = path.read_bytes()
        archived = (self.authority / "archive/P1/TASKS.md").read_bytes()
        code, result = self.run_cli("restore", "P1")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["written"])
        self.assertTrue(result["refreshed"])
        self.assertTrue(path.read_bytes().startswith(later))
        self.assertIn(archived[archived.index(b"## T-ONE"):], path.read_bytes())
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])
        self.assertFalse((self.authority / "archive/P1/TASKS.md").exists())

    def test_restore_all_record_types_after_new_owner_response(self):
        archive.archive(self.root, "P1")
        records = self.add_all_record_files()
        saved = {}
        for filename in records:
            source = self.authority / filename
            saved[filename] = source.read_bytes().split(b"\n\n", 1)[1]
            source.replace(self.authority / "archive/P1" / filename)
            source.write_bytes(b"# Working\n\n<!-- Later unrelated edit -->\n")
        response = ("# Responses\n\n## R-2: A-CLOSED CONFIRM\n- By: Owner\n"
                    "- Date: 2026-10-15\n- Via: cli\n\n> Confirmed again.\n").encode()
        (self.authority / "RESPONSES.md").write_bytes(response)
        result = archive.restore(self.root, "P1")
        self.assertEqual(len(result.candidates), 6)
        self.assertEqual(result.assumptions, ["A-CLOSED"])
        self.assertEqual(result.debts, ["TD-CLOSED"])
        self.assertEqual(result.events, ["EV-DONE"])
        for filename, data in saved.items():
            self.assertIn(data, (self.authority / filename).read_bytes())
            self.assertIn(b"Later unrelated edit", (self.authority / filename).read_bytes())
        self.assertEqual((self.authority / "RESPONSES.md").read_bytes(), response)
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])
        self.assertEqual(len(list((self.root / ".praukron/backup").glob("*/manifest.json"))), 2)

    def test_collision_and_traversal_refused_without_writes(self):
        original = (self.authority / "TASKS.md").read_bytes()
        archive.archive(self.root, "P1")
        (self.authority / "TASKS.md").write_bytes(original)
        before = self.snapshot()
        for phase, code in (("P1", "id-collision"), ("../P1", "invalid-selection"), ("unknown", "unknown-archive")):
            with self.subTest(phase=phase), self.assertRaises(archive.ArchiveError) as error:
                archive.restore(self.root, phase)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(self.snapshot(), before)

    def test_preexisting_validation_failure_is_refused_before_backup_or_replace(self):
        archive.archive(self.root, "P1")
        self.rewrite("TASKS.md", "- Dependencies: T-TWO", "- Dependencies: T-GHOST")
        before = self.snapshot()
        with mock.patch.object(archive, "_replace") as replace, \
                mock.patch.object(archive_recovery, "backup") as backup, \
                self.assertRaises(archive.ArchiveError) as error:
            archive.restore(self.root, "P1")
        self.assertEqual(error.exception.code, "validation-failed")
        replace.assert_not_called()
        backup.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list((self.root / ".praukron/backup").glob("*/manifest.json"))), 1)

    def test_dry_run_restore_writes_no_backup_or_recovery(self):
        archive.archive(self.root, "P1")
        before = self.snapshot(self.root)
        result = archive.restore(self.root, "P1", dry_run=True)
        self.assertTrue(result.candidates)
        self.assertEqual(self.snapshot(self.root), before)

    def test_restore_lf_crlf_and_archive_cycle(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                for path in self.authority.glob("*.md"):
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                archive.archive(self.root, "P1")
                archived = self.snapshot(self.authority / "archive/P1")
                archive.restore(self.root, "P1")
                archive.archive(self.root, "P1")
                self.assertEqual(self.snapshot(self.authority / "archive/P1"), archived)

    def test_refresh_failures_preserve_written_and_recovery_instructions(self):
        for step in ("compile", "graph", "dashboard"):
            with self.subTest(step=step):
                self.setUp()
                archive.archive(self.root, "P1")
                patches = {"compile": mock.patch.object(compiler, "write", side_effect=OSError(step)),
                           "graph": mock.patch("praukron.mutate.mermaid.render_all", side_effect=OSError(step)),
                           "dashboard": mock.patch("praukron.mutate.dashboard.render", side_effect=OSError(step))}
                with patches[step]:
                    code, result = self.run_cli("restore", "P1")
                self.assertEqual(code, 1, result)
                self.assertTrue(result["written"])
                self.assertFalse(result["refreshed"])
                self.assertTrue(Path(result["recovery"]["backup"]).is_dir())
                self.assertFalse((self.authority / "archive/P1/TASKS.md").exists())
                mutate.refresh(self.root)
                self.assertTrue((self.root / layout.COMPILED_DIR / "dashboard.html").is_file())


class TestRecoveryShells(RecoveryCase):
    def test_procedures_label_both_shells_and_preserve_arguments(self):
        argv = ["git", "-C", "/a path/café's repo", "restore", "--", "quoted 'file'.md"]
        info = {"commands": {"unstaged": [argv]}}
        rendered = "\n".join(archive_recovery.describe(info))
        for shell in ("POSIX sh", "PowerShell"):
            self.assertIn(f"Recovery (unstaged, {shell}):", rendered)
        self.assertEqual(shlex.split(archive_recovery.command(argv, "POSIX sh")), argv)
        self.assertTrue(archive_recovery.command(argv, "PowerShell").startswith("& "))
        self.assertIn("café''s repo", archive_recovery.command(argv, "PowerShell"))

    def test_rendered_git_procedures_run_in_each_available_shell(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        git_path = Path(shutil.which("git"))
        sh = next((str(p) for p in (git_path.parent.parent / "usr/bin/sh.exe",)
                   if p.is_file()), shutil.which("sh"))
        shells = []
        if sh:
            shells.append(("POSIX sh", [sh, "-c"]))
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell:
            shells.append(("PowerShell", [powershell, "-NoProfile", "-Command"]))
        self.assertTrue(shells)
        for shell, runner in shells:
            for state in ("unstaged", "staged", "committed"):
                with self.subTest(shell=shell, state=state):
                    self.setUp()
                    # Both shell syntaxes must handle spaces, Unicode and quotes.
                    from test_praukron import build_fixture
                    self.root = build_fixture(self.root / "café's project with spaces")
                    self.authority = self.root / layout.AUTHORITY_DIR
                    self.rewrite("PHASES.md", "Status:\nACTIVE", "Status:\nCOMPLETE")
                    self.init_git()
                    before = self.snapshot()
                    result = archive.archive(self.root, "P1")
                    if state != "unstaged":
                        self.git("add", "--", layout.AUTHORITY_DIR)
                    if state == "committed":
                        self.git("commit", "-m", "Archive")
                        commit = self.git("rev-parse", "HEAD")
                    for argv in result.recovery["commands"][state]:
                        if state == "committed":
                            argv = [commit if arg == "<archive-commit>" else arg for arg in argv]
                        rendered = archive_recovery.command(argv, shell)
                        with mock.patch.dict(os.environ, {"GIT_EDITOR": "true"}):
                            completed = subprocess.run([*runner, rendered], capture_output=True)
                        self.assertEqual(completed.returncode, 0, (rendered, completed.stderr))
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse((self.authority / "archive").exists())


class TestKilledArchiveRecovery(RecoveryCase):
    def test_kill_between_every_pair_of_apply_and_restore_replaces(self):
        # Register synthetic rules through the planner's extension seam, as the
        # records/journal workers do, to cover all six files without their PRs.
        for operation in ("apply", "restore"):
            for boundary in range(1, 12):
                with self.subTest(operation=operation, boundary=boundary):
                    self.setUp()
                    rules = self.extra_rules()
                    if operation == "restore":
                        with mock.patch.object(archive, "RECORD_RULES", rules):
                            archive.archive(self.root, "P1")
                    before = self.snapshot()
                    script = self.root / "kill_worker.py"
                    script.write_text(self.worker_source(), encoding="utf-8")
                    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
                    process = subprocess.Popen([sys.executable, str(script), str(self.root), operation,
                                                str(boundary)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    ready = self.root / "ready"
                    deadline = time.monotonic() + 20
                    try:
                        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(.01)
                        if not ready.exists():
                            process.kill()
                            out, err = process.communicate(timeout=10)
                            self.fail(f"worker failed before boundary: {out!r} {err!r}")
                        process.kill()
                        process.communicate(timeout=10)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate(timeout=10)
                    # A different writer acquires/reclaims the lock, rolls back,
                    # and refuses this attempt rather than writing on partial data.
                    result = mutate.transaction(self.root, "test-next-writer", lambda project: self.fail("not recovered"))
                    self.assertEqual(result["error"]["code"], "interrupted-write", result)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(mutate._recovery_records(self.root), [])

    @staticmethod
    def worker_source():
        return '''import re, sys, time
from pathlib import Path
from praukron import archive
root, operation, boundary = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
rules = []
for filename, record_id in [("ASSUMPTIONS.md", "A-CLOSED"), ("TECH_DEBT.md", "TD-CLOSED"),
                            ("TRACE.md", "EV-DONE"), ("JOURNAL.md", "2026-10-15 — Completed")]:
    if any(rule.filename == filename for rule in archive.RECORD_RULES):
        continue
    class Rule(archive.IdRule):
        def candidates(self, context):
            yield archive.Candidate(self.record_id, self.kind, self.filename,
                                    context.selection.destination(self.filename))
    rule = Rule()
    rule.filename, rule.record_id, rule.kind, rule.title = filename, record_id, filename, filename
    rule.pattern = re.compile("(" + re.escape(record_id) + ")(?:[: ].*|$)")
    rules.append(rule)
archive.RECORD_RULES = (*archive.RECORD_RULES, *rules)
replace, unlink = archive._replace, Path.unlink
count = 0
def completed():
    global count
    count += 1
    if count == boundary:
        (root / "ready").write_bytes(b"ready")
        while True:
            time.sleep(1)
def replaced(path, data):
    replace(path, data)
    completed()
def deleted(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if path.parent.name == "P1" and path.suffix == ".md":
        completed()
archive._replace = replaced
Path.unlink = deleted
if operation == "apply":
    archive.archive(root, "P1")
else:
    archive.restore(root, "P1")
'''


if __name__ == "__main__":
    unittest.main()
