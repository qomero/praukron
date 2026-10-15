"""Task and contract creation regressions (AC-T-WRITE-TASK-01)."""

from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import cli, compile as compiler, contract, layout, mutate, task, validate
from praukron.model import EVIDENCE_CLASSES
from test_praukron import FixtureCase


class CreationCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.tasks = self.authority / "TASKS.md"
        self.acceptance = self.authority / "ACCEPTANCE.md"
        self.payload = copy.deepcopy(task.EXAMPLE)

    def snapshot(self):
        return {p.relative_to(self.authority): p.read_bytes()
                for p in self.authority.rglob("*") if p.is_file()}

    def run_cli(self, group="task", *options, payload=None, raw=None):
        out, err = io.StringIO(), io.StringIO()
        data = json.dumps(self.payload if payload is None else payload) if raw is None else raw
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(data)):
            code = cli.main(["-C", str(self.root), group, "add", *options])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return code, json.loads(out.getvalue())

    def assert_valid(self):
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def uncontracted_task(self, status="TODO", ac=None):
        text = (f"## T-BARE: Awaiting contract\n- Status: {status}\n"
                "- Module: M-FOUNDATION\n- Domain: execution\n- Validation: UNTESTED\n"
                "- Dependencies: none\n- Owner: unassigned\n"
                + (f"- AC: {ac}\n" if ac is not None else "")
                + "- Evidence: —\n- Governed by: ADR-001\n")
        self.tasks.write_bytes(contract.append_section(self.tasks, "Tasks", text))
        return {**copy.deepcopy(contract.EXAMPLE), "task": "T-BARE",
                "expectTaskRevision": mutate.section(self.tasks.read_bytes(), "T-BARE").revision}


class TestTaskAdd(CreationCase):
    def test_embedded_contract_appends_both_and_round_trips_lf_crlf(self):
        self.assert_valid()
        initial = self.snapshot()
        for task_nl, ac_nl in ((b"\n", b"\n"), (b"\r\n", b"\r\n"), (b"\r\n", b"\n"), (b"\n", b"\r\n")):
            with self.subTest(task_newline=task_nl, contract_newline=ac_nl):
                for p, data in initial.items():
                    newline = task_nl if p.name == "TASKS.md" else ac_nl
                    (self.authority / p).write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", newline))
                before = self.snapshot()
                self.assertFalse(mutate.id_exists(compiler.load(self.root), self.payload["id"]))
                self.assertFalse(mutate.id_exists(compiler.load(self.root), self.payload["contract"]["id"]))
                self.payload.update(title="Công việc: " + "rất dài " * 100 + "xong", dependencies=["T-ONE", "T-TWO"],
                                    governedBy=["ADR-001"], files=["src/công việc.py", "src/other.py"],
                                    symbols=["Worker.run", "other"], authority="owner", validation="AI_REVIEWED")
                self.payload["contract"].update(title="Hợp đồng — " + "dài " * 100 + "xong", inherits=["AC-GLOBAL-CARE"],
                    criteria=[{"text": "Given công việc, When " + "kiểm tra " * 100 + "xong, Then ổn.", "class": cls}
                              for cls in EVIDENCE_CLASSES])
                with mock.patch.object(mutate, "transaction", wraps=mutate.transaction) as transaction:
                    code, result = self.run_cli("task", "--input", "-")
                self.assertEqual(code, 0, result)
                transaction.assert_called_once()
                self.assertTrue(result["written"])
                self.assertTrue(result["refreshed"])
                self.assertEqual(result["source"], "TASKS.md#T-NEW")
                after = self.snapshot()
                self.assertIn(b"- Domain: execution" + task_nl + b"- Authority: owner" + task_nl,
                              self.tasks.read_bytes())
                for p, old in before.items():
                    if p.name in ("TASKS.md", "ACCEPTANCE.md"):
                        self.assertTrue(after[p].startswith(old))
                        newline = task_nl if p.name == "TASKS.md" else ac_nl
                        self.assertNotIn(b"\n", after[p][len(old):].replace(newline, b""))
                    else:
                        self.assertEqual(after[p], old)
                project = compiler.load(self.root)
                added = project.task("T-NEW")
                for field, key in (("title", "title"), ("module", "module"), ("domain", "domain"),
                                   ("dependencies", "dependencies"), ("decisions", "governedBy"),
                                   ("files", "files"), ("symbols", "symbols"), ("authority", "authority"),
                                   ("validation", "validation")):
                    self.assertEqual(getattr(added, field), self.payload[key])
                self.assertEqual((added.status, added.owner, added.evidence, added.phase),
                                 ("TODO", "unassigned", None, "P1"))
                ac = project.contracts[added.contract]
                self.assertEqual(ac.title, self.payload["contract"]["title"])
                self.assertEqual(ac.inherits, ["AC-GLOBAL-CARE"])
                for i, (actual, expected) in enumerate(zip(ac.criteria, self.payload["contract"]["criteria"]), 1):
                    self.assertEqual((actual.id, actual.text, actual.evidence_class, actual.state, actual.evidence),
                                     (f"AC-T-NEW-{i:02d}", expected["text"], expected["class"], "NOT_RUN", None))
                self.assertEqual(result["revision"], mutate.section(self.tasks.read_bytes(), added.id).revision)
                self.assert_valid()
                self.assertTrue((self.root / layout.COMPILED_DIR / "dashboard.html").is_file())

    def test_payload_and_reference_refusals_leave_every_byte(self):
        cases = [({"phase": "P1"}, "unknown-field"), ({"Phase": "P1"}, "unknown-field"),
                 ({"module": "M-MISSING"}, "unknown-module"), ({"domain": "other"}, "invalid-value"),
                 ({"id": "T-TWO"}, "duplicate-id"), ({"dependencies": ["T-MISSING"]}, "unknown-dependency"),
                 ({"governedBy": ["ADR-999"]}, "unknown-decision"),
                 ({"validation": "HUMAN_VERIFIED"}, "invalid-value"), ({"status": "DONE"}, "unknown-field"),
                 ({"files": [7]}, "wrong-type"), ({"symbols": ["a,b"]}, "invalid-value"),
                 ({"title": "injected\n## T-EVIL: title"}, "invalid-value"), ({"id": "T-bad: id"}, "invalid-id"),
                 ({"ac": "AC-T-TWO"}, "invalid-contract")]
        for patch, expected in cases:
            with self.subTest(patch=patch):
                before = self.snapshot()
                code, result = self.run_cli("task", "--input", "-", payload={**self.payload, **patch})
                self.assertEqual(code, 1, result)
                self.assertEqual(result["error"]["code"], expected)
                self.assertFalse(result["written"])
                self.assertEqual(self.snapshot(), before)
        for missing in ("module", "domain", "dependencies", "contract"):
            with self.subTest(missing=missing):
                payload = {k: v for k, v in self.payload.items() if k != missing}
                before = self.snapshot()
                code, result = self.run_cli("task", "--input", "-", payload=payload)
                self.assertEqual(code, 1, result)
                self.assertEqual(self.snapshot(), before)

    def test_nested_contract_refusals_and_malformed_json(self):
        cases = [{"id": "AC-T-TWO"}, {"inherits": ["AC-UNKNOWN"]}, {"criteria": []},
                 {"extra": 1}, {"criteria": [None]}, {"criteria": [{"text": "Given x,\nWhen y, Then z.", "class": "TEST"}]},
                 {"criteria": [{"text": "Given x, When y, Then z.", "class": "UNKNOWN"}]},
                 {"criteria": [{"text": "Given x, When y, Then z.", "class": "TEST", "state": "PASS"}]},
                 {"criteria": [{"text": "Given x, When `TEST` runs, Then z.", "class": "TEST"}]},
                 {"criteria": [{"text": "Given  x, When y, Then z.", "class": "TEST"}]},
                 {"inherits": [None]}]
        before = self.snapshot()
        for patch in cases:
            with self.subTest(patch=patch):
                payload = {**self.payload, "contract": {**self.payload["contract"], **patch}}
                code, result = self.run_cli("task", "--input", "-", payload=payload)
                self.assertEqual(code, 1, result)
                self.assertFalse(result["written"])
                self.assertEqual(self.snapshot(), before)
        for raw in ("{", "[]", '{"id":"T-X","id":"T-Y"}'):
            self.assertEqual(self.run_cli("task", "--input", "-", raw=raw)[0], 1)
            self.assertEqual(self.snapshot(), before)

    def test_existing_working_contract_and_defaults(self):
        payload = {k: v for k, v in self.payload.items() if k != "contract"}
        payload.update(ac="AC-T-TWO", domain="operations", module="M-UPKEEP")
        before = self.acceptance.read_bytes()
        code, result = self.run_cli("task", "--input", "-", payload=payload)
        self.assertEqual(code, 0, result)
        self.assertEqual(self.acceptance.read_bytes(), before)
        added = compiler.load(self.root).task("T-NEW")
        self.assertEqual((added.contract, added.validation, added.phase, added.domain),
                         ("AC-T-TWO", "UNTESTED", "P-NONE", "operations"))
        self.assert_valid()
        payload["id"] = "T-ANOTHER"
        payload["ac"] = "AC-MISSING"
        before = self.snapshot()
        self.assertEqual(self.run_cli("task", "--input", "-", payload=payload)[0], 1)
        self.assertEqual(self.snapshot(), before)

    def test_archived_ids_and_archived_contract_reference_are_refused(self):
        archive = self.authority / "archive" / "P0"
        archive.mkdir(parents=True)
        archived_task = mutate.section(self.tasks.read_bytes(), "T-ONE").data.replace(b"T-ONE", b"T-OLD")
        (archive / "TASKS.md").write_bytes(b"# Tasks\n\n" + archived_task)
        archived_ac = mutate.section(self.acceptance.read_bytes(), "AC-T-ONE").data.replace(b"AC-T-ONE", b"AC-T-OLD")
        (archive / "ACCEPTANCE.md").write_bytes(b"# Acceptance\n\n" + archived_ac)
        payloads = [{**self.payload, "id": "T-OLD"},
                    {**self.payload, "contract": {**self.payload["contract"], "id": "AC-T-OLD"}},
                    {**{k: v for k, v in self.payload.items() if k != "contract"}, "ac": "AC-T-OLD"}]
        for payload in payloads:
            before = self.snapshot()
            code, result = self.run_cli("task", "--input", "-", payload=payload)
            self.assertEqual(code, 1, result)
            self.assertEqual(self.snapshot(), before)

    def test_second_replace_failure_and_new_validation_error_restore_both(self):
        before = self.snapshot()
        real = mutate._replace
        calls = 0

        def failing(path, data):
            nonlocal calls
            if path == self.acceptance:
                calls += 1
                if calls == 1:
                    raise OSError("second authoritative swap failed")
            return real(path, data)

        with mock.patch.object(mutate, "_replace", failing):
            code, result = self.run_cli("task", "--input", "-")
        self.assertEqual(code, 1, result)
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)
        real_check = validate.check

        def poisoned(project):
            findings = real_check(project)
            if project.task("T-NEW") is not None:
                findings.append(validate.Finding("error", "injected", "new error", "TASKS.md#T-NEW"))
            return findings

        with mock.patch.object(validate, "check", side_effect=poisoned):
            code, result = self.run_cli("task", "--input", "-")
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(mutate._recovery_records(self.root), [])

    def test_killed_between_replaces_next_command_restores_both_byte_for_byte(self):
        before = self.snapshot()
        real = mutate._replace

        def killed(path, data):
            real(path, data)
            if path == self.tasks:
                raise SystemExit("killed after task replace")

        with mock.patch.object(mutate, "_replace", killed):
            with self.assertRaises(SystemExit):
                task.add(self.root, self.payload)
        self.assertNotEqual(self.tasks.read_bytes(), before[Path("TASKS.md")])
        self.assertEqual(self.acceptance.read_bytes(), before[Path("ACCEPTANCE.md")])
        self.assertEqual(len(mutate._recovery_records(self.root)), 1)
        code, result = self.run_cli("task", "--input", "-")
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "interrupted-write")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(mutate._recovery_records(self.root), [])
        self.assertEqual(self.run_cli("task", "--input", "-")[0], 0)
        self.assert_valid()


class TestContractAdd(CreationCase):
    def test_referenced_missing_contract_is_refused_from_both_commands(self):
        original = self.tasks.read_bytes()
        archive = self.authority / "archive" / "P0" / "TASKS.md"
        for archived in (False, True):
            for status in ("TODO", "WIP", "DONE", "BLOCKED"):
                with self.subTest(archived=archived, status=status):
                    self.tasks.write_bytes(original)
                    archive.unlink(missing_ok=True)
                    payload = self.uncontracted_task()
                    reference = mutate.section(self.tasks.read_bytes(), "T-BARE").data.replace(b"\r\n", b"\n")
                    reference = reference.replace(b"T-BARE", b"T-REF").replace(b"- Status: TODO", f"- Status: {status}".encode())
                    reference += b"- AC: AC-T-NEW\n"
                    if archived:
                        archive.parent.mkdir(parents=True, exist_ok=True)
                        archive.write_bytes(b"# Tasks\n\n" + reference)
                    else:
                        self.tasks.write_bytes(contract.append_section(self.tasks, "Tasks", reference.decode("utf-8")))
                    project = compiler.load(self.root)
                    self.assertNotIn("AC-T-NEW", project.contracts)
                    self.assertEqual(project.task("T-REF").contract, "AC-T-NEW")
                    before = self.snapshot()
                    for group, request in (("task", self.payload), ("contract", payload)):
                        code, result = self.run_cli(group, "--input", "-", payload=request)
                        self.assertEqual(code, 1, result)
                        self.assertEqual(result["error"]["code"], "referenced-contract")
                        self.assertIn("T-REF", result["error"]["message"])
                        self.assertEqual(self.snapshot(), before)

    def test_todo_target_already_naming_missing_contract_preserves_task_bytes(self):
        original = self.tasks.read_bytes()
        acceptance = self.acceptance.read_bytes()
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.tasks.write_bytes(original)
                self.acceptance.write_bytes(acceptance)
                payload = self.uncontracted_task(ac="AC-T-NEW")
                for path in (self.tasks, self.acceptance):
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                payload["expectTaskRevision"] = mutate.section(self.tasks.read_bytes(), "T-BARE").revision
                self.assertIn("unknown-contract", {f.code for f in validate.errors(validate.check(compiler.load(self.root)))})
                before = self.snapshot()
                code, result = self.run_cli("contract", "--input", "-", "--dry-run", payload=payload)
                self.assertEqual(code, 0, result)
                self.assertEqual([item["file"] for item in result["wouldWrite"]], ["ACCEPTANCE.md"])
                self.assertEqual(self.snapshot(), before)
                with mock.patch.object(mutate, "_replace", wraps=mutate._replace) as replace:
                    code, result = self.run_cli("contract", "--input", "-", payload=payload)
                self.assertEqual(code, 0, result)
                self.assertTrue(result["written"])
                self.assertNotIn(self.tasks, [call.args[0] for call in replace.call_args_list])
                self.assertEqual(self.tasks.read_bytes(), before[Path("TASKS.md")])
                self.assertTrue(self.acceptance.read_bytes().startswith(before[Path("ACCEPTANCE.md")]))
                self.assertEqual(compiler.load(self.root).task("T-BARE").contract, "AC-T-NEW")
                self.assertIn("AC-T-NEW", compiler.load(self.root).contracts)
                self.assert_valid()

    def test_frozen_target_naming_missing_contract_is_refused_from_both_commands(self):
        original = self.tasks.read_bytes()
        for status in ("WIP", "DONE"):
            with self.subTest(status=status):
                self.tasks.write_bytes(original)
                payload = self.uncontracted_task(status=status, ac="AC-T-NEW")
                before = self.snapshot()
                for group, request, error in (("task", self.payload, "referenced-contract"),
                                               ("contract", payload, "frozen-contract")):
                    code, result = self.run_cli(group, "--input", "-", payload=request)
                    self.assertEqual(code, 1, result)
                    self.assertEqual(result["error"]["code"], error)
                    self.assertEqual(self.snapshot(), before)

    def test_attachment_requires_current_task_revision_and_returns_current(self):
        original = self.tasks.read_bytes()
        acceptance = self.acceptance.read_bytes()
        for change in ("owner", "evidence", "aba"):
            with self.subTest(change=change):
                self.tasks.write_bytes(original)
                self.acceptance.write_bytes(acceptance)
                payload = self.uncontracted_task()
                original_section = mutate.section(self.tasks.read_bytes(), "T-BARE")
                changed = original_section.data
                if change == "owner":
                    changed = changed.replace(b"- Owner: unassigned", b"- Owner: another-worker")
                elif change == "evidence":
                    changed = changed.replace("- Evidence: —".encode(), b"- Evidence: New observation")
                else:
                    changed = changed.replace(b"- Status: TODO", b"- Status: WIP")
                    changed = changed.replace(b"- Owner: unassigned", b"- Owner: worker\n- Claimed: 2026-10-15")
                    self.tasks.write_bytes(self.tasks.read_bytes()[:original_section.span[0]] + changed)
                    self.assertEqual(compiler.load(self.root).task("T-BARE").status, "WIP")
                    changed = changed.replace(b"- Status: WIP", b"- Status: TODO")
                self.tasks.write_bytes(self.tasks.read_bytes()[:original_section.span[0]] + changed)
                self.assertEqual(compiler.load(self.root).task("T-BARE").status, "TODO")
                current = mutate.section(self.tasks.read_bytes(), "T-BARE")
                before = self.snapshot()
                for options in ((), ("--dry-run",)):
                    code, result = self.run_cli("contract", "--input", "-", *options, payload=payload)
                    self.assertEqual(code, 1, result)
                    self.assertEqual(result["error"]["code"], "stale-revision")
                    self.assertEqual(result["error"]["field"], "expectTaskRevision")
                    self.assertIn(current.revision, result["error"]["message"])
                    self.assertEqual(result["current"], {"recordId": "T-BARE", "source": "TASKS.md#T-BARE",
                                                        "revision": current.revision, "record": current.data.decode("utf-8")})
                    self.assertEqual(self.snapshot(), before)
                payload["expectTaskRevision"] = current.revision
                code, result = self.run_cli("contract", "--input", "-", payload=payload)
                self.assertEqual(code, 0, result)
                self.assertEqual(compiler.load(self.root).task("T-BARE").contract, "AC-T-NEW")
                self.assert_valid()

    def test_attachment_revision_is_checked_after_lock_acquisition(self):
        payload = self.uncontracted_task()
        real_lock = mutate._lock
        raced = None

        def lock_and_race(root):
            nonlocal raced
            held = real_lock(root)
            found = mutate.section(self.tasks.read_bytes(), "T-BARE")
            self.tasks.write_bytes(self.tasks.read_bytes()[:found.span[0]]
                                   + found.data.replace(b"- Owner: unassigned", b"- Owner: changed-under-lock"))
            raced = self.snapshot()
            return held

        with mock.patch.object(mutate, "_lock", side_effect=lock_and_race):
            code, result = self.run_cli("contract", "--input", "-", payload=payload)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "stale-revision")
        self.assertEqual(result["current"]["revision"], mutate.section(self.tasks.read_bytes(), "T-BARE").revision)
        self.assertEqual(self.snapshot(), raced)

    def test_attachment_requires_well_formed_expect_task_revision(self):
        payload = self.uncontracted_task()
        before = self.snapshot()
        missing = {k: v for k, v in payload.items() if k != "expectTaskRevision"}
        for request, error in ((missing, "missing-field"), ({**payload, "expectTaskRevision": "bad"}, "invalid-value"),
                               ({**payload, "expectTaskRevision": 1}, "wrong-type")):
            code, result = self.run_cli("contract", "--input", "-", payload=request)
            self.assertEqual(code, 1, result)
            self.assertEqual(result["error"]["code"], error)
            self.assertEqual(result["error"]["field"], "expectTaskRevision")
            self.assertEqual(self.snapshot(), before)

    def test_todo_contract_add_sets_missing_or_empty_ac_and_round_trips(self):
        original = self.tasks.read_bytes()
        acceptance = self.acceptance.read_bytes()
        for ac in (None, "", "\t", "none", "NONE", "—", "-", " \tNoNe\t "):
            for newline in (b"\n", b"\r\n"):
                with self.subTest(ac=ac, newline=newline):
                    self.tasks.write_bytes(original)
                    self.acceptance.write_bytes(acceptance)
                    payload = self.uncontracted_task(ac=ac)
                    for path in (self.tasks, self.acceptance):
                        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                    before = self.tasks.read_bytes()
                    old_section = mutate.section(before, "T-BARE")
                    payload["expectTaskRevision"] = old_section.revision
                    code, result = self.run_cli("contract", "--input", "-", payload=payload)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(compiler.load(self.root).task("T-BARE").contract, "AC-T-NEW")
                    self.assertEqual(result["source"], "ACCEPTANCE.md#AC-T-NEW")
                    self.assertEqual(result["revision"], mutate.section(self.acceptance.read_bytes(), "AC-T-NEW").revision)
                    new_section = mutate.section(self.tasks.read_bytes(), "T-BARE")
                    self.assertEqual(self.tasks.read_bytes()[:new_section.span[0]], before[:old_section.span[0]])
                    self.assertNotIn(b"\n", self.tasks.read_bytes().replace(newline, b""))
                    self.assertNotIn(b"\n", self.acceptance.read_bytes().replace(newline, b""))
                    self.assert_valid()

    def test_attachment_preserves_wrapped_fields_and_following_sections(self):
        payload = self.uncontracted_task()
        data = self.tasks.read_bytes().replace(b"\r\n", b"\n")
        start = mutate.section(data, "T-BARE").span[0]
        bare = data[start:].replace(b"- Owner: unassigned\n", b"- Owner: long name\n  continued here\n")
        data = data[:start] + bare + b"\n## T-AFTER: Following\n- Status: TODO\n- Module: M-FOUNDATION\n- Validation: UNTESTED\n- Dependencies: none\n- AC: AC-T-TWO\n"
        self.tasks.write_bytes(data)
        payload["expectTaskRevision"] = mutate.section(data, "T-BARE").revision
        before = compiler.load(self.root).task("T-BARE")
        after_section = mutate.section(data, "T-AFTER").data
        code, result = self.run_cli("contract", "--input", "-", payload=payload)
        self.assertEqual(code, 0, result)
        added = compiler.load(self.root).task("T-BARE")
        self.assertEqual(added.owner, before.owner)
        self.assertEqual(added.owner, "long name continued here")
        self.assertEqual(mutate.section(self.tasks.read_bytes(), "T-AFTER").data, after_section)
        self.assertTrue(self.tasks.read_bytes().startswith(data[:start]))
        self.assert_valid()

    def test_frozen_wip_done_and_different_contract_are_refused(self):
        original = self.tasks.read_bytes()
        for status in ("WIP", "DONE"):
            self.tasks.write_bytes(original)
            payload = self.uncontracted_task(status=status)
            before = self.snapshot()
            code, result = self.run_cli("contract", "--input", "-", payload=payload)
            self.assertEqual(code, 1, result)
            self.assertEqual(result["error"]["code"], "frozen-contract")
            self.assertEqual(self.snapshot(), before)
        self.tasks.write_bytes(original)
        payload = self.uncontracted_task(ac="AC-T-TWO")
        before = self.snapshot()
        code, result = self.run_cli("contract", "--input", "-", payload=payload)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "different-contract")
        self.assertEqual(self.snapshot(), before)

    def test_missing_archived_task_and_duplicate_contract_are_refused(self):
        payload = self.uncontracted_task()
        archive = self.authority / "archive" / "P0"
        archive.mkdir(parents=True)
        (archive / "TASKS.md").write_bytes(b"# Tasks\n\n" + mutate.section(self.tasks.read_bytes(), "T-ONE").data.replace(b"T-ONE", b"T-OLD"))
        for patch, error in (({"task": "T-UNKNOWN"}, "record-not-found"), ({"task": "T-OLD"}, "archived-record"),
                             ({"id": "AC-T-TWO"}, "duplicate-id")):
            before = self.snapshot()
            code, result = self.run_cli("contract", "--input", "-", payload={**payload, **patch})
            self.assertEqual(code, 1, result)
            self.assertEqual(result["error"]["code"], error)
            self.assertEqual(self.snapshot(), before)

    def test_attachment_and_contract_rollback_together(self):
        payload = self.uncontracted_task()
        before = self.snapshot()
        real = validate.check

        def poisoned(project):
            findings = real(project)
            if "AC-T-NEW" in project.contracts:
                findings.append(validate.Finding("error", "injected", "refused", "ACCEPTANCE.md"))
            return findings

        with mock.patch.object(validate, "check", poisoned):
            code, result = self.run_cli("contract", "--input", "-", payload=payload)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertEqual(self.snapshot(), before)


class TestCreationCLI(CreationCase):
    def test_criteria_need_no_prescribed_wording(self):
        original = self.tasks.read_bytes()
        acceptance = self.acceptance.read_bytes()
        criteria = [{"text": "All file replacements succeed or the originals are restored.", "class": "TEST"},
                    {"text": "Dữ liệu được giữ nguyên.", "class": "INSPECTION"}]
        for group in ("task", "contract"):
            with self.subTest(group=group):
                self.tasks.write_bytes(original)
                self.acceptance.write_bytes(acceptance)
                if group == "task":
                    payload = copy.deepcopy(self.payload)
                    payload["contract"]["criteria"] = criteria
                else:
                    payload = self.uncontracted_task()
                    payload["criteria"] = criteria
                code, result = self.run_cli(group, "--input", "-", payload=payload)
                self.assertEqual(code, 0, result)
                actual = compiler.load(self.root).contracts["AC-T-NEW"].criteria
                self.assertEqual([(c.text, c.evidence_class) for c in actual], [(c["text"], c["class"]) for c in criteria])
                self.assert_valid()

    def test_published_schemas_only_use_supported_validation_keywords(self):
        supported = {"type", "properties", "additionalProperties", "required", "enum", "minLength", "description"}

        def check(schema):
            self.assertLessEqual(set(schema), supported)
            for child in schema.get("properties", {}).values():
                check(child)

        for schema in (task.SCHEMA, contract.SCHEMA, contract.CRITERION_SCHEMA):
            check(schema)

    def test_schemas_examples_dry_runs_and_file_input(self):
        before = self.snapshot()
        for group, schema in (("task", task.SCHEMA), ("contract", contract.SCHEMA)):
            code, result = self.run_cli(group, "--schema")
            self.assertEqual(code, 0)
            self.assertEqual(result["schema"], schema)
            mutate.validate_payload(result["example"], schema)
            example = result["example"]
            if group == "contract":
                example["expectTaskRevision"] = self.uncontracted_task()["expectTaskRevision"]
                example["task"] = "T-BARE"
            baseline = self.snapshot()
            code, result = self.run_cli(group, "--input", "-", "--dry-run", payload=example)
            self.assertEqual(code, 0, result)
            self.assertTrue(result["dryRun"])
            self.assertFalse(result["written"])
            self.assertEqual(len(result["wouldWrite"]), 2)
            self.assertEqual(self.snapshot(), baseline)
            filename = self.root / f"{group}.json"
            filename.write_text(json.dumps(example), encoding="utf-8")
            code, result = self.run_cli(group, "--input", str(filename))
            self.assertEqual(code, 0, result)
            self.assert_valid()
            for name, data in before.items():
                (self.authority / name).write_bytes(data)

    def test_schema_without_project_missing_input_and_piped_utf8(self):
        launcher = Path(__file__).resolve().parents[1] / ".praukron" / "praukron"
        for group in ("task", "contract"):
            result = subprocess.run([sys.executable, str(launcher), "-C", str(self.root / "absent"), group, "add", "--schema"],
                                    capture_output=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("example", json.loads(result.stdout))
            self.assertEqual(self.run_cli(group)[1]["error"]["code"], "missing-input")
        payload = copy.deepcopy(self.payload)
        payload["title"] = "Công việc tiếng Việt"
        result = subprocess.run([sys.executable, str(launcher), "-C", str(self.root), "task", "add", "--input", "-"],
                                input=json.dumps(payload, ensure_ascii=False), capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertEqual(compiler.load(self.root).task("T-NEW").title, payload["title"])
