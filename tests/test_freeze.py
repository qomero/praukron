"""Frozen-contract fingerprint regression tests (AC-T-WRITE-FREEZE-01, ADR-078)."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import acr, analytics, cli, compile as compiler, freeze, validate  # noqa: E402
from praukron.parse import _fields  # noqa: E402
from test_task_update import UpdateCase  # noqa: E402

FROZEN_CODES = {"frozen-criterion-changed", "frozen-criterion-added", "frozen-criterion-removed",
                "invalid-frozen"}
CRITERION = "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.\n  `TEST` · `NOT_RUN`"


class FreezeCase(UpdateCase):
    def start(self, task="T-TWO"):
        code, result = self.update({"status": "WIP", "owner": "Rhea"}, task)
        self.assertEqual(code, 0, result)
        self.project = compiler.load(self.root)

    def frozen(self, task="T-TWO"):
        return [f for f in validate.check(compiler.load(self.root)) if f.code in FROZEN_CODES]

    def section(self, task="T-TWO"):
        from praukron import mutate
        return mutate.section(self.tasks.read_bytes(), task).data.decode("utf-8")


class TestFingerprintWritten(FreezeCase):
    def test_wip_records_one_entry_per_criterion_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.convert(newline)
                self.start()
                task = compiler.load(self.root).task("T-TWO")
                criterion = compiler.load(self.root).contracts["AC-T-TWO"].criteria[0]
                expected = f"AC-T-TWO-01 {freeze.digest(criterion.id, 'TEST', criterion.text)}"
                self.assertEqual(task.frozen, expected)
                self.assertEqual(_fields(self.section())["Frozen"], expected)
                data = self.tasks.read_bytes()
                if newline == b"\r\n":
                    self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
                self.assertEqual(self.frozen(), [])

    def test_long_fingerprint_wraps_and_reads_back(self):
        criteria = "".join(f"- `AC-T-TWO-{n:02d}` — Given {n}, When it runs, Then it holds.\n  `TEST`\n"
                           for n in range(2, 9))
        self.rewrite("ACCEPTANCE.md", CRITERION, CRITERION + "\n" + criteria.rstrip("\n"))
        self.start()
        section = self.section()
        self.assertTrue(all(len(line) <= 100 for line in section.splitlines()))
        self.assertEqual(len(freeze.parse(compiler.load(self.root).task("T-TWO").frozen)), 8)
        self.assertEqual(self.frozen(), [])

    def test_state_or_evidence_change_is_not_reported(self):
        self.start()
        self.rewrite("ACCEPTANCE.md", "`TEST` · `NOT_RUN`", "`TEST` · `PASS`\n  - Evidence: Stood up.")
        self.assertEqual(self.frozen(), [])
        self.rewrite("ACCEPTANCE.md", "Then they stand.\n  `TEST`", "Then they\n  stand. `TEST`")
        self.assertEqual(self.frozen(), [], "re-wrapping the text is not a change")

    def test_done_and_reopen_keep_the_fingerprint(self):
        self.start()
        first = compiler.load(self.root).task("T-TWO").frozen
        self.pass_criteria()
        self.assertEqual(self.update({"status": "DONE", "evidence": "Stood."})[0], 0)
        self.assertEqual(compiler.load(self.root).task("T-TWO").frozen, first)
        self.rewrite("ACCEPTANCE.md", "Then they stand.", "Then they mostly stand.")
        code, result = self.update({"status": "WIP"})
        self.assertEqual(code, 0, result)
        self.assertEqual(compiler.load(self.root).task("T-TWO").frozen, first)
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"])

    def test_returning_through_todo_records_a_new_fingerprint(self):
        self.start()
        self.assertEqual(self.update({"status": "TODO"})[0], 0)
        self.rewrite("ACCEPTANCE.md", "Then they stand.", "Then they stand straight.")
        self.assertEqual(self.frozen(), [], "a TODO contract is not checked")
        self.start()
        self.assertEqual(self.frozen(), [])

    def test_payload_cannot_set_frozen(self):
        code, result = self.update({"frozen": "AC-T-TWO-01 00000000"})
        self.assertEqual((code, result["error"]["code"]), (1, "unknown-field"))


class TestHandEditsReported(FreezeCase):
    def test_text_class_added_and_removed_are_errors_naming_task_and_criterion(self):
        edits = [
            ("Then they stand.", "Then they mostly stand.", "frozen-criterion-changed", "AC-T-TWO-01"),
            ("Then they stand.\n  `TEST`", "Then they stand.\n  `INSPECTION`", "frozen-criterion-changed",
             "AC-T-TWO-01"),
            ("\n\n## AC-T-THREE", "\n- `AC-T-TWO-02` — Given more, When added, Then caught.\n  `TEST`\n\n"
             "## AC-T-THREE", "frozen-criterion-added", "AC-T-TWO-02"),
            ("`AC-T-TWO-01` — Given", "`AC-T-TWO-02` — Given", "frozen-criterion-removed", "AC-T-TWO-01"),
        ]
        for old, new, code, criterion in edits:
            for status in ("WIP", "DONE"):
                with self.subTest(code=code, new=new, status=status):
                    self.setUp()
                    self.start()
                    if status == "DONE":
                        self.pass_criteria()
                        self.assertEqual(self.update({"status": "DONE", "evidence": "Stood."})[0], 0)
                    self.rewrite("ACCEPTANCE.md", old, new)
                    found = self.frozen()
                    self.assertIn(code, [f.code for f in found])
                    finding = next(f for f in found if f.code == code)
                    self.assertEqual(finding.severity, "error")
                    self.assertEqual(finding.where, "TASKS.md#T-TWO")
                    self.assertIn(criterion, finding.message)
                    self.assertIn("T-TWO", finding.message)

    def test_message_does_not_print_the_expected_fingerprint(self):
        self.start()
        frozen = compiler.load(self.root).task("T-TWO").frozen.split()[1]
        self.rewrite("ACCEPTANCE.md", "Then they stand.", "Then they mostly stand.")
        self.assertNotIn(frozen, self.frozen()[0].message)

    def test_unreadable_fingerprint_is_an_error(self):
        self.start()
        self.rewrite("TASKS.md", "- Frozen: AC-T-TWO-01 ", "- Frozen: AC-T-TWO-01 zz")
        self.assertEqual([f.code for f in self.frozen()], ["invalid-frozen"])

    def test_validate_compile_and_context_surface_it(self):
        self.start()
        self.rewrite("ACCEPTANCE.md", "Then they stand.", "Then they mostly stand.")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(["-C", str(self.root), "validate"]), 1)
            self.assertEqual(cli.main(["-C", str(self.root), "compile"]), 1)
        self.assertIn("frozen-criterion-changed", err.getvalue())
        self.assertIn("AC-T-TWO-01", err.getvalue())
        problems = analytics.context(compiler.load(self.root), "T-TWO")["problems"]
        self.assertTrue(any("frozen-criterion-changed" in p and "AC-T-TWO-01" in p for p in problems), problems)

    def test_an_unrelated_write_still_succeeds_while_it_stands(self):
        self.start()
        self.rewrite("ACCEPTANCE.md", "Then they stand.", "Then they mostly stand.")
        code, result = self.update({"evidence": "Partial."})
        self.assertEqual(code, 0, result)
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"])


class TestAcceptedChangeRequests(FreezeCase):
    def request(self, proposed, **extra):
        result = acr.add(self.root, {"task": "T-TWO", "criterion": "AC-T-TWO-01", "proposed": proposed,
                                     "reason": "Too vague.", **extra})
        self.assertTrue(result["ok"], result)
        return result["recordId"]

    def decide(self, record, decision):
        path = self.authority / "ACCEPTANCE.md"
        text = path.read_text(encoding="utf-8")
        pending = f"- Decision: {acr.PENDING}"
        at = text.index(pending, text.index(f"### {record}"))
        path.write_text(text[:at] + f"- Decision: {decision}" + text[at + len(pending):], encoding="utf-8")

    def test_accepted_request_applied_in_place_is_not_reported(self):
        self.start()
        record = self.request("Given a foundation, When walls go up, Then they stand plumb.")
        self.rewrite("ACCEPTANCE.md", "Then they stand.\n", "Then they stand plumb.\n")
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"], "pending is not accepted")
        self.decide(record, "ACCEPTED by owner, 2026-10-16 (R-1)")
        self.assertEqual(self.frozen(), [])

    def test_rejected_request_does_not_excuse_the_edit(self):
        self.start()
        record = self.request("Given a foundation, When walls go up, Then they stand plumb.")
        self.rewrite("ACCEPTANCE.md", "Then they stand.\n", "Then they stand plumb.\n")
        self.decide(record, "REJECTED by owner, 2026-10-16")
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"])

    def test_chain_of_accepted_requests_and_class_change(self):
        self.start()
        first = self.request("Given a foundation, When walls go up, Then they stand plumb.")
        self.decide(first, "ACCEPTED by owner")
        self.rewrite("ACCEPTANCE.md", "Then they stand.\n", "Then they stand plumb.\n")
        self.assertEqual(self.frozen(), [])
        second = self.request("Given a foundation, When walls go up, Then an inspector signs them off.",
                              **{"class": "INSPECTION"})
        self.decide(second, "ACCEPTED by owner")
        self.rewrite("ACCEPTANCE.md", "Then they stand plumb.\n  `TEST`",
                     "Then an inspector signs them off.\n  `INSPECTION`")
        self.assertEqual(self.frozen(), [])
        self.rewrite("ACCEPTANCE.md", "an inspector signs", "anyone signs")
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"])


class TestUpgrade(FreezeCase):
    def test_existing_chronicle_without_fingerprints_validates_as_before(self):
        before = {(f.code, f.where) for f in validate.check(self.project)}
        self.assertIsNone(self.project.task("T-ONE").frozen)
        self.rewrite("ACCEPTANCE.md", "Then it is\n  level.", "Then it is\n  roughly level.")
        self.assertEqual({(f.code, f.where) for f in validate.check(self.project)}, before)

    def test_legacy_task_gains_a_fingerprint_on_its_next_status_change(self):
        code, result = self.update({"status": "WIP"}, "T-ONE")
        self.assertEqual(code, 0, result)
        self.assertIsNotNone(compiler.load(self.root).task("T-ONE").frozen)
        self.assertEqual(self.frozen(), [])
        self.rewrite("ACCEPTANCE.md", "Then it is\n  level.", "Then it is\n  roughly level.")
        self.assertEqual([f.code for f in self.frozen()], ["frozen-criterion-changed"])

    def test_change_requests_are_parsed_from_the_log(self):
        self.start()
        record = self.request_for_parse()
        requests = compiler.load(self.root).change_requests
        self.assertEqual([(r.id, r.criterion, r.accepted) for r in requests], [(record, "AC-T-TWO-01", False)])
        self.assertIn("`TEST`", requests[0].current)

    def request_for_parse(self):
        result = acr.add(self.root, {"task": "T-TWO", "criterion": "AC-T-TWO-01",
                                     "proposed": "Given x, When y, Then z.", "reason": "Vague."})
        self.assertTrue(result["ok"], result)
        return result["recordId"]


if __name__ == "__main__":
    unittest.main()
