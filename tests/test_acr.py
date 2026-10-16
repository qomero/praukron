"""acr add records an Acceptance Change Request (AC-T-WRITE-ACR-01, ADR-076)."""

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

from praukron import acr, cli, compile as compiler, layout, mutate  # noqa: E402
from praukron.parse import parse_acceptance  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402

TEMPLATE_LOG = "## Acceptance Change Requests\n\nNone.\n\n---\n"


class AcrCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.acceptance = self.authority / "ACCEPTANCE.md"
        # T-ONE is DONE; T-TWO becomes WIP, so both contracts are frozen.
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        self.payload = {"task": "T-TWO", "criterion": "AC-T-TWO-01",
                        "proposed": "Given a foundation, When walls go up, Then they stand for a year.",
                        "reason": "Standing is not measurable as written."}

    def run_cli(self, payload, *options):
        out, err = io.StringIO(), io.StringIO()
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(raw)):
            code = cli.main(["-C", str(self.root), "acr", "add", "--input", "-", *options])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return code, json.loads(out.getvalue())

    def snapshot(self):
        return {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}

    def contracts(self):
        return parse_acceptance(self.acceptance)

    def refused(self, payload, code):
        before = self.snapshot()
        status, result = self.run_cli(payload)
        self.assertEqual(status, 1, result)
        self.assertEqual(result["error"]["code"], code, result)
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)
        return result


class TestRecord(AcrCase):
    def test_wip_task_gets_one_pending_request_and_its_criterion_is_unchanged(self):
        before = self.contracts()
        frozen = mutate.section(self.acceptance.read_bytes(), "AC-T-TWO").data
        code, result = self.run_cli(self.payload)
        self.assertEqual(code, 0, result)
        self.assertEqual((result["recordId"], result["source"]), ("ACR-001", "ACCEPTANCE.md#ACR-001"))
        self.assertTrue(result["written"] and result["refreshed"])
        self.assertEqual(self.contracts(), before)
        self.assertEqual(mutate.section(self.acceptance.read_bytes(), "AC-T-TWO").data, frozen)
        record = mutate.section(self.acceptance.read_bytes(), "ACR-001")
        self.assertEqual(record.revision, result["revision"])
        self.assertEqual(record.data.decode("utf-8").replace("\r\n", "\n"), (
            "### ACR-001 — AC-T-TWO-01\n"
            "- Task: T-TWO\n"
            "- Criterion: `AC-T-TWO-01`\n"
            "- Current contract: Given a foundation, When walls go up, Then they stand. `TEST`\n"
            "- Proposed contract: Given a foundation, When walls go up, Then they stand for a year. `TEST`\n"
            "- Reason: Standing is not measurable as written.\n"
            "- Impact: not stated\n"
            "- Decision: PENDING — awaiting the owner\n"))
        self.assertEqual(compiler.load(self.root).contracts["AC-T-TWO"].criteria[0].state, "NOT_RUN")

    def test_done_task_and_optional_fields(self):
        payload = {"task": "T-ONE", "criterion": "AC-T-ONE-01", "proposed": "Given a site, When poured, Then level.",
                   "reason": "Shorter.", "impact": "Wording only.", "class": "INSPECTION", "title": "shorter wording"}
        code, result = self.run_cli(payload)
        self.assertEqual(code, 0, result)
        text = mutate.section(self.acceptance.read_bytes(), result["recordId"]).data.decode("utf-8").replace("\r\n", "\n")
        self.assertTrue(text.startswith("### ACR-001 — AC-T-ONE-01 shorter wording\n"))
        self.assertIn("- Proposed contract: Given a site, When poured, Then level. `INSPECTION`\n", text)
        self.assertIn("- Impact: Wording only.\n", text)

    def test_template_log_replaces_none_and_keeps_its_rule_with_crlf(self):
        text = self.acceptance.read_text(encoding="utf-8").replace("# Acceptance\n", "# Acceptance\n\n" + TEMPLATE_LOG, 1)
        self.acceptance.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        before = self.acceptance.read_bytes()
        code, result = self.run_cli(self.payload)
        self.assertEqual(code, 0, result)
        after = self.acceptance.read_bytes()
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertNotIn(b"None.", after)
        head, rest = after.split(b"### ACR-001", 1)
        self.assertTrue(head.endswith(b"## Acceptance Change Requests\r\n\r\n"))
        self.assertIn(b"- Decision: PENDING \xe2\x80\x94 awaiting the owner\r\n\r\n---\r\n", rest)
        # Everything outside the log is byte-for-byte unchanged.
        tail = before.split(b"---\r\n", 1)[1]
        self.assertTrue(after.endswith(tail))

    def test_ids_follow_the_highest_working_or_archived_request(self):
        archive = self.authority / layout.ARCHIVE_DIR / "P1"
        archive.mkdir(parents=True)
        (archive / "ACCEPTANCE.md").write_text(
            "# Acceptance\n\n## Acceptance Change Requests\n\n### ACR-007 — AC-T-OLD-01\n- Criterion: `AC-T-OLD-01`\n"
            "- Decision: ACCEPTED by owner\n", encoding="utf-8")
        code, result = self.run_cli(self.payload)
        self.assertEqual(result["recordId"], "ACR-008", result)
        self.acceptance.write_bytes(self.acceptance.read_bytes().replace(b"PENDING", b"REJECTED by owner, 2026-10-16"))
        code, result = self.run_cli(self.payload)
        self.assertEqual((code, result["recordId"]), (0, "ACR-009"))

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        code, result = self.run_cli(self.payload, "--dry-run")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["recordId"], "ACR-001")
        self.assertEqual(self.snapshot(), before)

    def test_runs_under_the_shared_lock(self):
        lock = mutate._lock(self.root)
        try:
            self.refused(self.payload, "locked")
        finally:
            lock.release()


class TestRefusals(AcrCase):
    def test_unknown_task_and_criterion(self):
        self.refused({**self.payload, "task": "T-NOPE"}, "record-not-found")
        self.refused({**self.payload, "criterion": "AC-T-TWO-09"}, "criterion-not-found")
        self.refused({**self.payload, "criterion": "AC-T-ONE-01"}, "criterion-not-found")

    def test_todo_task_edits_its_contract_instead(self):
        result = self.refused({**self.payload, "task": "T-THREE", "criterion": "AC-T-THREE-01"}, "unfrozen-contract")
        self.assertIn("edit the criterion in ACCEPTANCE.md directly", result["error"]["message"])

    def test_inherited_invariant_is_refused(self):
        self.refused({**self.payload, "task": "T-ONE", "criterion": "AC-GLOBAL-CARE-01"}, "invariant-criterion")

    def test_a_second_undecided_request_is_refused(self):
        self.assertEqual(self.run_cli(self.payload)[0], 0)
        result = self.refused({**self.payload, "reason": "Another reason."}, "duplicate-request")
        self.assertEqual(result["error"]["openRequest"], "ACR-001")

    def test_text_rules(self):
        self.refused({**self.payload, "proposed": "Two\nlines"}, "invalid-value")
        self.refused({**self.payload, "proposed": "Spaced  twice"}, "invalid-value")
        self.refused({**self.payload, "proposed": "Then it passes `TEST`"}, "invalid-value")
        self.refused({**self.payload, "class": "VIBES"}, "invalid-value")
        self.refused({**self.payload, "decision": "ACCEPTED"}, "unknown-field")

    def test_shape_refusal_carries_an_accepted_payload(self):
        result = self.refused({k: v for k, v in self.payload.items() if k != "reason"}, "missing-field")
        self.assertEqual(result["error"]["example"], acr.EXAMPLE)
        mutate.validate_payload(result["error"]["example"], acr.SCHEMA)

    def test_archived_task_is_immutable(self):
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        start = text.index("## T-ONE:")
        end = text.index("## T-TWO:")
        archive = self.authority / layout.ARCHIVE_DIR / "P1"
        archive.mkdir(parents=True)
        (archive / "TASKS.md").write_text("# Tasks\n\n" + text[start:end], encoding="utf-8")
        tasks.write_text(text[:start] + text[end:], encoding="utf-8")
        self.refused({**self.payload, "task": "T-ONE", "criterion": "AC-T-ONE-01"}, "archived-record")


class TestInterface(unittest.TestCase):
    def test_schema_needs_no_project_and_its_example_is_accepted_shape(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["acr", "add", "--schema"]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["example"], acr.EXAMPLE)
        mutate.validate_payload(result["example"], result["schema"])
        self.assertIn("never changes", result["schema"]["description"])

    def test_requests_reader_handles_hand_written_records(self):
        text = ("## Acceptance Change Requests\n\n### ACR-004 — AC-T-X-01 legacy\n\n- Task: T-X\n"
                "- Criterion: `AC-T-X-01`\n- Decision: ACCEPTED by the owner\n\n### ACR-005 — AC-T-X-02\n"
                "- Criterion: AC-T-X-02\n")
        self.assertEqual(acr.requests(text), [
            {"id": "ACR-004", "number": 4, "criterion": "AC-T-X-01", "decision": "ACCEPTED by the owner"},
            {"id": "ACR-005", "number": 5, "criterion": "AC-T-X-02", "decision": ""},
        ])


if __name__ == "__main__":
    unittest.main()
