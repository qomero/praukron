"""Reads expose the revisions and DONE rules used by structured writes."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from test_task_update import UpdateCase
from praukron import analytics, cli, criterion, index, mutate, retrieve, task_update


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
        for state in ("NOT_RUN", "FAIL", "PASS"):
            path = self.authority / "ACCEPTANCE.md"
            data = path.read_text(encoding="utf-8")
            for previous in ("NOT_RUN", "FAIL", "PASS"):
                data = data.replace(f"`TEST` Â· `{previous}`", f"`TEST` Â· `{state}`")
            path.write_text(data, encoding="utf-8")
            for owner in (False, True):
                for evidence in (False, True):
                    with self.subTest(state=state, owner=owner, evidence=evidence):
                        path = self.tasks
                        text = path.read_text(encoding="utf-8").replace("- Authority: owner\n", "")
                        text = text.replace("## T-TWO: Build on it\n", "## T-TWO: Build on it\n" +
                                            ("- Authority: owner\n" if owner else ""))
                        text = text.replace("- Evidence: Synthetic check.\n", "- Evidence: â€”\n")
                        if evidence:
                            text = text.replace("- Evidence: â€”", "- Evidence: Synthetic check.", 1)
                        path.write_text(text, encoding="utf-8")
                        packet = json.loads(self.read_cli("context", "T-TWO"))
                        closability = packet["task"]["closability"]
                        result = task_update.update(self.root, {"id": "T-TWO",
                            "expectRevision": packet["task"]["revision"], "set": {"status": "DONE"}}, dry_run=True)
                        self.assertEqual(closability["canClose"], result["ok"], result)
                        if not result["ok"]:
                            self.assertEqual(closability["reasons"][0]["code"], result["error"]["code"])
                        if state != "PASS":
                            reason = next(r for r in closability["reasons"] if r["code"] == "unresolved-acceptance")
                            self.assertEqual(reason["criteria"], ["AC-T-TWO-01"])

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
