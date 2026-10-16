"""Reads expose the revisions and DONE rules used by structured writes."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest import mock

from test_task_update import UpdateCase
from praukron import analytics, cli, compile as compiler, criterion, index, mutate, retrieve, task_update


class TestRevisionReads(UpdateCase):
    def read_cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["-C", str(self.root), *args]), 0)
        return output.getvalue()

    def test_all_reads_provide_exact_revisions_for_first_writes_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            for command in ("context", "explain", "retrieve"):
                with self.subTest(newline=newline, command=command):
                    self.convert(newline)
                    raw = self.read_cli(command, "T-TWO", *([] if command == "context" else ["--json"]))
                    packet = json.loads(raw)
                    if command == "retrieve":
                        task = next(i for i in packet["items"] if i["kind"] == "TASK")
                        contract = next(i for i in packet["items"] if i["kind"] == "ACCEPTANCE")
                        criterion_revision = contract["criterionRevisions"]["AC-T-TWO-01"]
                        self.assertEqual(criterion_revision, contract["revision"])
                    else:
                        task = packet["task"] if command == "context" else packet
                        criterion_revision = packet["acceptance"][0]["revision"]
                    self.assertEqual(task["revision"], self.revision())
                    result = task_update.update(self.root, {"id": "T-TWO", "expectRevision": task["revision"],
                                                "set": {"owner": f"worker-{command}-{newline.hex()}"}})
                    self.assertTrue(result["ok"], result)
                    result = criterion.set_result(self.root, {"criterion": "AC-T-TWO-01",
                        "expectRevision": criterion_revision, "state": "PASS", "evidence": "Synthetic check."})
                    self.assertTrue(result["ok"], result)
                    current = json.loads(self.read_cli("context", "T-TWO"))["acceptance"][0]["revision"]
                    self.assertEqual(current, result["revision"])
                    self.assertIn(task["revision"], raw)

    def test_plain_explain_and_retrieve_show_revisions(self):
        task_revision = self.revision()
        contract_revision = mutate.section((self.authority / "ACCEPTANCE.md").read_bytes(), "AC-T-TWO").revision
        for command in ("explain", "retrieve"):
            text = self.read_cli(command, "T-TWO")
            self.assertIn(task_revision, text)
            self.assertIn(contract_revision, text)

    def test_closability_and_writer_use_the_same_rule(self):
        acceptance_path = self.authority / "ACCEPTANCE.md"
        acceptance = acceptance_path.read_text(encoding="utf-8")
        tasks = self.tasks.read_text(encoding="utf-8")
        original_section = mutate.section(tasks.encode(), "T-TWO").data.decode()
        state_line = "`TEST` · `NOT_RUN`"
        self.assertEqual(acceptance.count(state_line), 1)
        for state in ("NOT_RUN", "FAIL", "PASS"):
            data = acceptance.replace(state_line, f"`TEST` · `{state}`", 1)
            if state != "NOT_RUN":
                self.assertNotEqual(data, acceptance)
            acceptance_path.write_text(data, encoding="utf-8")
            for owner in (False, True):
                for evidence in (False, True):
                    with self.subTest(state=state, owner=owner, evidence=evidence):
                        section = original_section.replace("- Evidence: —", "- Evidence: Synthetic check.", 1)
                        self.assertNotEqual(section, original_section)
                        if not evidence:
                            cleared = section.replace("- Evidence: Synthetic check.", "- Evidence: —", 1)
                            self.assertNotEqual(cleared, section)
                            section = cleared
                        if owner:
                            section = section.replace("## T-TWO: Build on it\n",
                                                      "## T-TWO: Build on it\n- Authority: owner\n", 1)
                        self.tasks.write_text(tasks.replace(original_section, section, 1), encoding="utf-8")
                        project = compiler.load(self.root)
                        task = project.task("T-TWO")
                        self.assertEqual(project.mandatory_criteria(task)[0].state, state)
                        self.assertEqual(task.owner_held, owner)
                        self.assertEqual(bool(task.evidence), evidence)
                        packet = json.loads(self.read_cli("context", "T-TWO"))
                        closability = packet["task"]["closability"]
                        self.assertEqual(closability["canClose"], state == "PASS" and not owner and evidence)
                        if state == "PASS" and not owner and not evidence:
                            self.assertIn({"code": "evidence-required"}, closability["reasons"])
                        for reason in closability["reasons"]:
                            self.assertNotIn("message", reason)
                        result = task_update.update(self.root, {"id": "T-TWO",
                            "expectRevision": packet["task"]["revision"], "set": {"status": "DONE"}}, dry_run=True)
                        self.assertEqual(closability["canClose"], result["ok"], result)
                        if not result["ok"]:
                            self.assertEqual(closability["reasons"][0]["code"], result["error"]["code"])
                        if state != "PASS":
                            reason = next(r for r in closability["reasons"] if r["code"] == "unresolved-acceptance")
                            self.assertEqual(reason["criteria"], ["AC-T-TWO-01"])

    def test_unlocatable_contract_degrades_reads_without_traceback(self):
        # Main now locates level-three ACR/contract sections too. Exercise an
        # actual locator refusal rather than relying on that formerly unsupported heading.
        original = mutate.record_revision

        def unavailable(root, source, **kwargs):
            if source.anchor == "AC-T-TWO":
                raise mutate.WriteError("record-not-found", "missing section")
            return original(root, source, **kwargs)

        patch = mock.patch.object(mutate, "record_revision", side_effect=unavailable)
        patch.start()
        self.addCleanup(patch.stop)
        packet = json.loads(self.read_cli("context", "T-TWO"))
        self.assertIsNone(packet["acceptance"][0]["revision"])
        self.assertFalse(packet["task"]["closability"]["canClose"])
        self.assertIn({"code": "record-not-found", "records": ["AC-T-TWO"]},
                      packet["task"]["closability"]["reasons"])
        detail = json.loads(self.read_cli("explain", "T-TWO", "--json"))
        self.assertIsNone(detail["acceptance"][0]["revision"])
        self.assertIn("revision unavailable", self.read_cli("explain", "T-TWO"))
        self.assertIn("T-TWO", self.read_cli("retrieve", "T-TWO"))

    def test_retrieve_keeps_null_revision_when_locator_refuses(self):
        original = mutate.record_revision

        def unavailable(root, source, **kwargs):
            if source.anchor == "AC-T-TWO":
                raise mutate.WriteError("record-not-found", "missing section")
            return original(root, source, **kwargs)

        with mock.patch.object(mutate, "record_revision", side_effect=unavailable):
            packet = json.loads(self.read_cli("retrieve", "T-TWO", "--json"))
            contract = next(i for i in packet["items"] if i["kind"] == "ACCEPTANCE")
            self.assertIsNone(contract["revision"])
            self.assertEqual(contract["criterionRevisions"], {"AC-T-TWO-01": None})
            self.assertIn("Revision: unavailable", self.read_cli("retrieve", "T-TWO"))

    def test_archived_records_have_revisions_and_cannot_close(self):
        archive = self.authority / "archive" / "P1"
        archive.mkdir(parents=True)
        for name in ("TASKS.md", "ACCEPTANCE.md"):
            path = self.authority / name
            (archive / name).write_bytes(path.read_bytes())
            path.write_bytes(f"# {name}\n".encode())
        packet = json.loads(self.read_cli("context", "T-TWO"))
        self.assertFalse(packet["task"]["closability"]["canClose"])
        self.assertEqual(packet["task"]["closability"]["reasons"][0]["code"], "archived-record")
        self.assertEqual(packet["task"]["revision"], mutate.section((archive / "TASKS.md").read_bytes(), "T-TWO").revision)
        raw = json.loads(self.read_cli("retrieve", "T-TWO", "--json"))
        self.assertEqual(raw["items"][0]["revision"], packet["task"]["revision"])

    def test_missing_contract_is_named_and_retrieval_budget_counts_metadata(self):
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: AC-MISSING")
        packet = json.loads(self.read_cli("context", "T-TWO"))
        self.assertEqual(packet["task"]["closability"]["reasons"][0]["code"], "unresolved-acceptance")
        whole = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "T-ONE", budget=0)
        first = whole.items[0]
        budget = index.tokens(first.text + first.metadata())
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "T-ONE", budget=budget)
        self.assertEqual(len(pack.items), 1)
        self.assertTrue(pack.omitted)

    def test_multiple_criteria_share_contract_revision_and_stale_reads_are_refused(self):
        self.rewrite("ACCEPTANCE.md", "## AC-T-THREE", "- `AC-T-TWO-02` — Given walls, When checked, Then they stand.\n  `TEST` · `NOT_RUN`\n\n## AC-T-THREE")
        packet = json.loads(self.read_cli("context", "T-TWO"))
        revisions = [c["revision"] for c in packet["acceptance"]]
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0], revisions[1])
        for number, expected_ok in ((1, True), (2, False)):
            result = criterion.set_result(self.root, {"criterion": f"AC-T-TWO-0{number}",
                "expectRevision": revisions[number - 1], "state": "PASS"})
            self.assertEqual(result["ok"], expected_ok)
            if not expected_ok:
                self.assertEqual(result["error"]["code"], "stale-revision")

    def test_guidance_prefers_packet_revisions_and_keeps_revision_required(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Take the task's", text)
        self.assertIn("Only for a record not", text)
        self.assertIn("expectRevision", task_update.SCHEMA["required"])
        self.assertIn("expectRevision", criterion.SCHEMA["required"])
        hint = mutate.NEXT_STEP["invalid-revision"]
        self.assertLess(hint.index("context"), hint.index("sixteen zeros"))
        for name in ("docs/GUIDE.md", "docs/SPEC.md"):
            text = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
            self.assertIn("record not yet read", " ".join(text.split()))

    def test_interleaved_writes_cannot_pair_old_content_with_new_revisions(self):
        baseline = {name: (self.authority / name).read_bytes()
                    for name in ("TASKS.md", "ACCEPTANCE.md")}
        commands = (("context", ()), ("explain", ("--json",)), ("explain", ()),
                    ("retrieve", ("--json",)), ("retrieve", ()))
        for newline in (b"\n", b"\r\n"):
            for record in ("T-TWO", "AC-T-TWO"):
                for command, options in commands:
                    with self.subTest(newline=newline, record=record, command=command, options=options):
                        for name, data in baseline.items():
                            (self.authority / name).write_bytes(data)
                        self.convert(newline)
                        name = "TASKS.md" if record == "T-TWO" else "ACCEPTANCE.md"
                        before = mutate.section((self.authority / name).read_bytes(), record).revision
                        writes = []

                        def intervene():
                            if record == "T-TWO":
                                result = task_update.update(self.root, {
                                    "id": record, "expectRevision": before,
                                    "set": {"evidence": "Worker B evidence."}})
                            else:
                                result = criterion.set_result(self.root, {
                                    "criterion": "AC-T-TWO-01", "expectRevision": before,
                                    "state": "PASS", "evidence": "Worker B evidence."})
                            self.assertTrue(result["ok"], result)
                            self.assertNotEqual(result["revision"], before)
                            writes.append(result)

                        if command == "retrieve":
                            original = retrieve.Chronicle.section

                            def interleave(chronicle, name, anchor):
                                text = original(chronicle, name, anchor)
                                if anchor == record and not writes:
                                    intervene()
                                return text

                            patch = mock.patch.object(retrieve.Chronicle, "section", interleave)
                        else:
                            original = cli._load

                            def interleave(root):
                                project = original(root)
                                intervene()
                                return project

                            patch = mock.patch.object(cli, "_load", interleave)
                        with patch:
                            raw = self.read_cli(command, "T-TWO", *options)
                        self.assertEqual(len(writes), 1)
                        self.assertNotIn("Worker B evidence.", raw)
                        if command == "context" or options:
                            packet = json.loads(raw)
                            if command == "retrieve":
                                item = next(i for i in packet["items"] if i["id"] == record)
                                revision = item["revision"]
                                if record == "AC-T-TWO":
                                    self.assertIn("`NOT_RUN`", item["text"])
                                    self.assertEqual(item["criterionRevisions"], {"AC-T-TWO-01": before})
                            elif record == "T-TWO":
                                item = packet["task"] if command == "context" else packet
                                revision = item["revision"]
                                self.assertIsNone(packet["evidence"])
                            else:
                                item = packet["acceptance"][0]
                                revision = item["revision"]
                                self.assertEqual(item["state"], "NOT_RUN")
                            self.assertEqual(revision, before)
                        else:
                            self.assertIn(before, raw)
                            self.assertNotIn(writes[0]["revision"], raw)
                            revision = before
                        committed = self.snapshot()
                        if record == "T-TWO":
                            result = task_update.update(self.root, {
                                "id": record, "expectRevision": revision,
                                "set": {"evidence": "Worker A overwrite."}})
                            self.assertEqual(compiler.load(self.root).task(record).evidence,
                                             "Worker B evidence.")
                        else:
                            result = criterion.set_result(self.root, {
                                "criterion": "AC-T-TWO-01", "expectRevision": revision, "state": "FAIL"})
                            self.assertEqual(compiler.load(self.root).contracts[record].criteria[0].state, "PASS")
                        self.assertFalse(result["ok"], result)
                        self.assertEqual(result["error"]["code"], "stale-revision")
                        self.assertEqual(self.snapshot(), committed)

    def test_done_task_cannot_close_again_but_can_edit_completed_evidence_and_validation(self):
        packet = json.loads(self.read_cli("context", "T-ONE"))
        self.assertEqual(packet["task"]["status"], "DONE")
        self.assertEqual(packet["task"]["closability"], {
            "canClose": False, "reasons": [{"code": "already-done"}]})
        result = task_update.update(self.root, {
            "id": "T-ONE", "expectRevision": packet["task"]["revision"], "set": {"status": "DONE"}})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "no-change")
        for changes in ({"status": "DONE", "evidence": "Additional completed-task evidence."},
                        {"validation": "AI_REVIEWED"}):
            result = task_update.update(self.root, {
                "id": "T-ONE", "expectRevision": self.revision("T-ONE"), "set": changes})
            self.assertTrue(result["ok"], result)
        task = compiler.load(self.root).task("T-ONE")
        self.assertEqual(task.status, "DONE")
        self.assertEqual(task.evidence, "Additional completed-task evidence.")
        self.assertEqual(task.validation, "AI_REVIEWED")

    def test_write_between_file_capture_and_parsing_keeps_captured_revision(self):
        baseline = {name: (self.authority / name).read_bytes()
                    for name in ("TASKS.md", "ACCEPTANCE.md")}
        for record, parser_name, name in (("T-TWO", "parse_tasks", "TASKS.md"),
                                           ("AC-T-TWO", "parse_acceptance", "ACCEPTANCE.md")):
            for newline in (b"\n", b"\r\n"):
                with self.subTest(record=record, newline=newline):
                    for filename, data in baseline.items():
                        (self.authority / filename).write_bytes(data)
                    self.convert(newline)
                    before = mutate.section((self.authority / name).read_bytes(), record).revision
                    original = getattr(compiler, parser_name)
                    writes = []
                    armed = True

                    def interleave(*args, **kwargs):
                        nonlocal armed
                        if armed:
                            armed = False  # The real writer also loads the compiler.
                            if record == "T-TWO":
                                result = task_update.update(self.root, {"id": record,
                                    "expectRevision": before, "set": {"evidence": "Worker B evidence."}})
                            else:
                                result = criterion.set_result(self.root, {"criterion": "AC-T-TWO-01",
                                    "expectRevision": before, "state": "PASS", "evidence": "Worker B evidence."})
                            self.assertTrue(result["ok"], result)
                            writes.append(result)
                        return original(*args, **kwargs)

                    with mock.patch.object(compiler, parser_name, interleave):
                        project = compiler.load(self.root)
                    self.assertEqual(len(writes), 1)
                    packet = analytics.context(project, "T-TWO")
                    if record == "T-TWO":
                        self.assertIsNone(packet["evidence"])
                        self.assertEqual(packet["task"]["revision"], before)
                    else:
                        self.assertEqual(packet["acceptance"][0]["state"], "NOT_RUN")
                        self.assertEqual(packet["acceptance"][0]["revision"], before)
                    self.assertNotEqual(before, writes[0]["revision"])
