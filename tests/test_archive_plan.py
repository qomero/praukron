"""Lifecycle planner and stale-plan regression tests (AC-T-ARCH-PLAN-01)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, archive, cli, compile as compiler, layout, respond, retrieve, validate  # noqa: E402
from praukron.parse import ParseError  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402


class SplitNoteRule(archive.IdRule):
    kind, filename, title = "note", "NOTES.md", "Notes"
    pattern = re.compile(r"(N-[A-Z]+)(?: — |$)")

    def candidates(self, context):
        for record_id, phase in (("N-ONE", "P1"), ("N-TWO", "P2")):
            yield archive.Candidate(record_id, self.kind, self.filename, f"archive/{phase}/{self.filename}")


class ArchivePlanCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
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

    def add_task(self, task_id, module, status="DONE", domain=None, dependencies="none"):
        fields = [
            f"## {task_id}: Archive fixture",
            f"- Status: {status}",
            f"- Module: {module}",
            f"- Validation: {'SYNTHETIC' if status == 'DONE' else 'UNTESTED'}",
            f"- Dependencies: {dependencies}",
            f"- AC: AC-{task_id}",
            f"- Evidence: {'done' if status == 'DONE' else '—'}",
        ]
        if domain:
            fields.insert(3, f"- Domain: {domain}")
        path = self.authority / "TASKS.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + "\n".join(fields) + "\n", encoding="utf-8")
        path = self.authority / "ACCEPTANCE.md"
        path.write_text(path.read_text(encoding="utf-8") +
                        f"\n## AC-{task_id} — Archive fixture\n\n"
                        f"- `AC-{task_id}-01` — Given archive, When checked, Then it holds. `TEST` · `{'PASS' if status == 'DONE' else 'NOT_RUN'}`\n",
                        encoding="utf-8")

    def write_lifecycle_records(self):
        (self.authority / "ASSUMPTIONS.md").write_text(
            "# Assumptions\n\n"
            "## A-CLOSED: Confirmed\n- Status: CONFIRMED\n- Tasks: T-CLOSED\n"
            "- Impact: LOW\n- Assumption: The closed task is ready.\n- Responses: R-1\n\n"
            "## A-OPEN: Still open\n- Status: OPEN\n- Tasks: T-HELD\n"
            "- Impact: LOW\n- Assumption: The held task needs a decision.\n",
            encoding="utf-8")
        (self.authority / "RESPONSES.md").write_text(
            "# Responses\n\n## R-1: A-CLOSED CONFIRM\n"
            "- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Confirmed.\n",
            encoding="utf-8")
        (self.authority / "TECH_DEBT.md").write_text(
            "# Technical debt\n\n"
            "## TD-CLOSED: Retired\n- Status: RESOLVED\n- Debt: Nothing remains.\n"
            "- Exit condition: Removed.\n- Linked tasks: T-CLOSED\n- Resolution: Completed.\n\n"
            "## TD-OPEN: Pending\n- Status: OPEN\n- Debt: Still waiting.\n"
            "- Exit condition: Decide.\n- Linked tasks: T-HELD\n",
            encoding="utf-8")
        (self.authority / "TRACE.md").write_text(
            "# Trace\n\n"
            "## EV-CLOSED: Completed command\n- Type: command\n- Task: T-CLOSED\n- Outcome: success\n\n"
            "## EV-FAIL: Failed command\n- Type: failure\n- Task: T-HELD\n- Outcome: failure\n\n"
            "## EV-RETRY: Incomplete retry\n- Type: retry\n- Task: T-HELD\n"
            "- Retry of: EV-FAIL\n- Outcome: partial\n",
            encoding="utf-8")


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
        class NoteRule(archive.IdRule):
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

    def test_phase_axis_moves_every_domain_and_pnone_keeps_working_dependants(self):
        self.add_task("T-PHASE-OPS", "M-FOUNDATION", domain="operations")
        self.add_task("T-PNONE-EXEC", "M-UPKEEP")
        self.add_task("T-PNONE-OPS", "M-UPKEEP", domain="operations")
        self.add_task("T-PNONE-HELD", "M-UPKEEP")
        self.add_task("T-PNONE-WORKER", "M-UPKEEP", status="TODO", dependencies="T-PNONE-HELD")
        self.add_task("T-PNONE-ROOT", "M-UPKEEP")
        self.add_task("T-PNONE-MIDDLE", "M-UPKEEP", dependencies="T-PNONE-ROOT")
        self.add_task("T-PNONE-LEAF", "M-UPKEEP", status="TODO", dependencies="T-PNONE-MIDDLE")
        phase = archive.preview(self.root, "P1")
        pnone = archive.preview(self.root, "P-NONE")
        self.assertIn("T-ONE", phase.tasks)
        self.assertIn("T-PHASE-OPS", phase.tasks)
        self.assertEqual({"T-PNONE-EXEC", "T-PNONE-OPS"} & set(pnone.tasks),
                         {"T-PNONE-EXEC", "T-PNONE-OPS"})
        held = next(c for c in pnone.candidates if c.id == "T-PNONE-HELD")
        self.assertEqual(held.reason, "working task(s) depend on it: T-PNONE-WORKER")
        root = next(c for c in pnone.candidates if c.id == "T-PNONE-ROOT")
        self.assertEqual(root.reason, "working task(s) depend on it: T-PNONE-MIDDLE")
        before = archive._records(compiler.load(self.root))
        archive.archive(self.root, "P-NONE", digest=pnone.digest)
        after = compiler.load(self.root)
        self.assertEqual(archive._records(after), before)
        self.assertEqual(validate.errors(validate.check(after)), [])
        self.assertEqual(after.task("T-PNONE-EXEC").source.file, "archive/P-NONE/TASKS.md")
        self.assertEqual(after.task("T-PNONE-OPS").source.file, "archive/P-NONE/TASKS.md")


class TestArchiveApply(ArchivePlanCase):
    @staticmethod
    def derived_facts(project):
        report = analytics.report(project)
        return {
            "ready": set(report.ready),
            "blocked": set(report.blocked),
            "wip": set(report.wip),
            "gates": {(gate.id, gate.status) for gate in project.gates},
            "phase_progress": {(phase, progress.done, progress.total)
                               for phase, progress in report.phase_progress.items()},
            "unresolved_failures": set(analytics.unresolved_failures(project)),
            "awaiting_reconciliation": {item["assumption"]
                                         for item in analytics.awaiting_reconciliation(project)},
            "open_debt": {debt.id for debt in project.debts if not debt.closed},
            "open_assumptions": {assumption.id for assumption in project.assumptions if assumption.open},
        }

    def test_closed_lifecycle_records_move_with_their_tasks_and_preserve_parity(self):
        self.add_task("T-CLOSED", "M-FOUNDATION")
        self.add_task("T-HELD", "M-FOUNDATION")
        self.write_lifecycle_records()
        before_project = compiler.load(self.root)
        before = archive._records(before_project)
        facts = self.derived_facts(before_project)
        plan = archive.preview(self.root, "P1")
        candidates = {candidate.id: candidate for candidate in plan.candidates}
        self.assertEqual(set(plan.assumptions), {"A-CLOSED"})
        self.assertEqual(set(plan.debts), {"TD-CLOSED"})
        self.assertEqual(set(plan.events), {"EV-CLOSED"})
        self.assertEqual(candidates["A-OPEN"].reason, "status is OPEN")
        self.assertEqual(candidates["TD-OPEN"].reason, "status is OPEN")
        self.assertEqual(candidates["EV-FAIL"].reason, "part of an unresolved retry chain")
        self.assertEqual(candidates["EV-RETRY"].reason, "part of an unresolved retry chain")

        archive.archive(self.root, "P1", digest=plan.digest)
        after = compiler.load(self.root)
        self.assertEqual(archive._records(after), before)
        self.assertEqual(self.derived_facts(after), facts)
        self.assertEqual(after.assumption("A-CLOSED").source.file, "archive/P1/ASSUMPTIONS.md")
        self.assertEqual(next(d for d in after.debts if d.id == "TD-CLOSED").source.file,
                         "archive/P1/TECH_DEBT.md")
        self.assertEqual(next(e for e in after.events if e.id == "EV-CLOSED").source.file,
                         "archive/P1/TRACE.md")
        chronicle = retrieve.Chronicle(self.root)
        self.assertIn("## A-CLOSED", chronicle.section("ASSUMPTIONS.md", "A-CLOSED"))
        self.assertIn("## TD-CLOSED", chronicle.section("TECH_DEBT.md", "TD-CLOSED"))
        self.assertIn("## EV-CLOSED", chronicle.section("TRACE.md", "EV-CLOSED"))
        debt_pack = retrieve.retrieve(after, analytics.report(after), self.root, "TD-CLOSED")
        event_pack = retrieve.retrieve(after, analytics.report(after), self.root, "EV-CLOSED")
        self.assertIn("archive/P1/TECH_DEBT.md#TD-CLOSED", [item.source for item in debt_pack.items])
        self.assertIn("archive/P1/TRACE.md#EV-CLOSED", [item.source for item in event_pack.items])

        self.add_task("T-LATER", "M-UPKEEP", status="TODO", dependencies="T-CLOSED")
        respond.append(self.root, [respond.Item("A-CLOSED", "REJECT", "Restore before reconciling.")],
                       "owner", date="2026-10-15", recompile=False)
        later = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(later)), [])
        self.assertEqual(analytics.awaiting_reconciliation(later), [{
            "assumption": "A-CLOSED", "response": "R-2", "action": "REJECT", "archived": True,
            "remedy": "owner must run archive restore before an agent can reconcile it",
        }])

    def test_lifecycle_candidates_report_every_reason_to_stay(self):
        self.add_task("T-CLOSED", "M-FOUNDATION")
        self.add_task("T-HELD", "M-FOUNDATION")
        self.write_lifecycle_records()
        assumptions = self.authority / "ASSUMPTIONS.md"
        assumptions.write_text(assumptions.read_text(encoding="utf-8") +
                               "\n## A-WAIT: Awaiting reconciliation\n- Status: CONFIRMED\n"
                               "- Tasks: T-HELD\n- Impact: LOW\n- Assumption: The response must land.\n"
                               "- Responses: R-2, R-3\n\n"
                               "## A-WORKING: Working task\n- Status: CONFIRMED\n"
                               "- Tasks: T-HELD\n- Impact: LOW\n- Assumption: The task remains.\n"
                               "- Responses: R-4\n", encoding="utf-8")
        responses = self.authority / "RESPONSES.md"
        responses.write_text(responses.read_text(encoding="utf-8") +
                             "\n## R-2: A-WAIT CONFIRM\n- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Yes.\n"
                             "\n## R-3: A-WAIT REJECT\n- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> No.\n"
                             "\n## R-4: A-WORKING CONFIRM\n- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Yes.\n",
                             encoding="utf-8")
        debt = self.authority / "TECH_DEBT.md"
        debt.write_text(debt.read_text(encoding="utf-8") +
                        "\n## TD-WORKING: Closed but still linked\n- Status: INVALIDATED\n"
                        "- Debt: No longer applies.\n- Exit condition: Removed.\n"
                        "- Linked tasks: T-HELD\n- Resolution: Invalidated.\n", encoding="utf-8")
        trace = self.authority / "TRACE.md"
        trace.write_text(trace.read_text(encoding="utf-8") +
                         "\n## EV-WORKING: Successful but task remains\n- Type: command\n"
                         "- Task: T-HELD\n- Outcome: success\n", encoding="utf-8")
        candidates = {candidate.id: candidate for candidate in archive.preview(self.root, "P1").candidates}
        self.assertEqual(candidates["A-OPEN"].reason, "status is OPEN")
        self.assertEqual(candidates["A-WAIT"].reason, "a response awaits reconciliation")
        self.assertEqual(candidates["A-WORKING"].reason, "task T-HELD is still working")
        self.assertEqual(candidates["TD-OPEN"].reason, "status is OPEN")
        self.assertEqual(candidates["TD-WORKING"].reason, "task T-HELD is still working")
        self.assertEqual(candidates["EV-FAIL"].reason, "part of an unresolved retry chain")
        self.assertEqual(candidates["EV-RETRY"].reason, "part of an unresolved retry chain")
        self.assertEqual(candidates["EV-WORKING"].reason, "task T-HELD is still working")

    def test_lifecycle_destination_uses_every_named_task_and_phase_order(self):
        self.add_phase("P9")
        self.add_phase("P2")
        self.add_task("T-P9", "M-P9")
        self.add_task("T-P2", "M-P2")
        self.add_task("T-NONE", "M-UPKEEP")
        (self.authority / "ASSUMPTIONS.md").write_text(
            "# Assumptions\n\n## A-SPREAD: Across phases\n- Status: CONFIRMED\n"
            "- Tasks: T-P9\n- Impact: LOW\n- Assumption: Each task is complete.\n"
            "- Applied in: T-P2, T-NONE\n- Responses: R-1\n\n"
            "## A-NONE: Only phase-independent work\n- Status: CONFIRMED\n"
            "- Tasks: T-NONE\n- Impact: LOW\n- Assumption: This work is phase-independent.\n"
            "- Responses: R-2\n\n"
            "## A-MIXED: Phase and phase-independent work\n- Status: CONFIRMED\n"
            "- Tasks: T-P9\n- Impact: LOW\n- Assumption: Both tasks are complete.\n"
            "- Applied in: T-NONE\n- Responses: R-3\n", encoding="utf-8")
        (self.authority / "RESPONSES.md").write_text(
            "# Responses\n\n## R-1: A-SPREAD CONFIRM\n"
            "- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Confirmed.\n\n"
            "## R-2: A-NONE CONFIRM\n"
            "- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Confirmed.\n\n"
            "## R-3: A-MIXED CONFIRM\n"
            "- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Confirmed.\n", encoding="utf-8")
        (self.authority / "TECH_DEBT.md").write_text(
            "# Technical debt\n\n## TD-SPREAD: Across phases\n- Status: RESOLVED\n"
            "- Introduced by: T-P9\n- Debt: Nothing remains.\n- Exit condition: Removed.\n"
            "- Linked tasks: T-P2, T-NONE\n- Resolution: Completed.\n\n"
            "## TD-NONE: Only phase-independent work\n- Status: INVALIDATED\n"
            "- Debt: Nothing remains.\n- Exit condition: Removed.\n"
            "- Linked tasks: T-NONE\n- Resolution: Invalidated.\n\n"
            "## TD-NO-TASK: No task lineage\n- Status: RESOLVED\n"
            "- Introduced by: ADR-001\n- Debt: Nothing remains.\n"
            "- Exit condition: Removed.\n- Resolution: Recorded.\n\n"
            "## TD-MIXED: Phase and phase-independent work\n- Status: RESOLVED\n"
            "- Introduced by: T-P9\n- Debt: Nothing remains.\n"
            "- Exit condition: Removed.\n- Linked tasks: T-NONE\n"
            "- Resolution: Completed.\n", encoding="utf-8")
        (self.authority / "TRACE.md").write_text(
            "# Trace\n\n## EV-NONE: No task\n- Type: command\n- Outcome: success\n", encoding="utf-8")
        before_project = compiler.load(self.root)
        before = archive._records(before_project)
        facts = self.derived_facts(before_project)

        p2 = {candidate.id: candidate for candidate in archive.preview(self.root, "P2").candidates}
        self.assertEqual(p2["A-SPREAD"].destination, "archive/P2/ASSUMPTIONS.md")
        self.assertEqual(p2["A-SPREAD"].reason, "task T-P9 is still working")
        self.assertEqual(p2["TD-SPREAD"].destination, "archive/P2/TECH_DEBT.md")
        self.assertEqual(p2["TD-SPREAD"].reason, "task T-P9 is still working")
        self.assertNotIn("A-SPREAD", {candidate.id for candidate in archive.preview(self.root, "P9").candidates})
        p9 = {candidate.id: candidate for candidate in archive.preview(self.root, "P9").candidates}
        self.assertEqual(p9["A-MIXED"].destination, "archive/P9/ASSUMPTIONS.md")
        self.assertEqual(p9["A-MIXED"].reason, "task T-NONE is still working")
        self.assertEqual(p9["TD-MIXED"].destination, "archive/P9/TECH_DEBT.md")
        self.assertEqual(p9["TD-MIXED"].reason, "task T-NONE is still working")
        pnone = {candidate.id: candidate for candidate in archive.preview(self.root, "P-NONE").candidates}
        self.assertEqual(pnone["A-NONE"].destination, "archive/P-NONE/ASSUMPTIONS.md")
        self.assertTrue(pnone["A-NONE"].moves)
        self.assertEqual(pnone["TD-NONE"].destination, "archive/P-NONE/TECH_DEBT.md")
        self.assertTrue(pnone["TD-NONE"].moves)
        self.assertEqual(pnone["TD-NO-TASK"].destination, "archive/P-NONE/TECH_DEBT.md")
        self.assertEqual(pnone["TD-NO-TASK"].reason, "names no task")
        self.assertEqual(pnone["EV-NONE"].destination, "archive/P-NONE/TRACE.md")
        self.assertTrue(pnone["EV-NONE"].moves)

        p9 = archive.preview(self.root, "P9")
        archive.archive(self.root, "P9", digest=p9.digest)
        p2 = {candidate.id: candidate for candidate in archive.preview(self.root, "P2").candidates}
        self.assertEqual(p2["A-SPREAD"].reason, "task T-NONE is still working")
        self.assertEqual(p2["TD-SPREAD"].reason, "task T-NONE is still working")

        pnone_plan = archive.preview(self.root, "P-NONE")
        moving = [candidate for candidate in pnone_plan.candidates if candidate.moves]
        self.assertIn(f"{len(moving)} records", pnone_plan.render(False))
        archive.archive(self.root, "P-NONE", digest=pnone_plan.digest)
        after_pnone = compiler.load(self.root)
        self.assertEqual(archive._records(after_pnone), before)
        self.assertEqual(self.derived_facts(after_pnone), facts)
        self.assertEqual(validate.errors(validate.check(after_pnone)), [])
        self.assertEqual(after_pnone.task("T-NONE").source.file, "archive/P-NONE/TASKS.md")
        self.assertEqual(next(event for event in after_pnone.events if event.id == "EV-NONE").source.file,
                         "archive/P-NONE/TRACE.md")
        p9_plan = archive.preview(self.root, "P9")
        self.assertIn("A-MIXED", p9_plan.assumptions)
        self.assertIn("TD-MIXED", p9_plan.debts)
        archive.archive(self.root, "P9", digest=p9_plan.digest)
        p2_plan = archive.preview(self.root, "P2")
        self.assertIn("A-SPREAD", p2_plan.assumptions)
        self.assertIn("TD-SPREAD", p2_plan.debts)
        archive.archive(self.root, "P2", digest=p2_plan.digest)
        after = compiler.load(self.root)
        self.assertEqual(archive._records(after), before)
        self.assertEqual(self.derived_facts(after), facts)
        self.assertEqual(after.assumption("A-SPREAD").source.file, "archive/P2/ASSUMPTIONS.md")
        self.assertEqual(next(debt for debt in after.debts if debt.id == "TD-SPREAD").source.file,
                         "archive/P2/TECH_DEBT.md")

    def test_lifecycle_records_preserve_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.add_task("T-CLOSED", "M-FOUNDATION")
                self.add_task("T-HELD", "M-FOUNDATION")
                self.write_lifecycle_records()
                for name in ("ASSUMPTIONS.md", "TECH_DEBT.md", "TRACE.md"):
                    path = self.authority / name
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
                plan = archive.preview(self.root, "P1")
                archive.archive(self.root, "P1", digest=plan.digest)
                for name in ("ASSUMPTIONS.md", "TECH_DEBT.md", "TRACE.md"):
                    for path in (self.authority / name, self.authority / "archive/P1" / name):
                        data = path.read_bytes()
                        self.assertIn(newline, data)
                        if newline == b"\n":
                            self.assertNotIn(b"\r\n", data)
                        else:
                            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_lifecycle_loader_refuses_duplicate_archive_ids(self):
        self.add_task("T-CLOSED", "M-FOUNDATION")
        self.write_lifecycle_records()
        duplicate = self.authority / "archive/P1"
        duplicate.mkdir(parents=True)
        (duplicate / "ASSUMPTIONS.md").write_text(
            "# Assumptions · P1 (archived)\n\n## A-CLOSED: Duplicate\n- Status: CONFIRMED\n"
            "- Tasks: T-CLOSED\n- Impact: LOW\n- Assumption: Duplicate.\n- Responses: R-1\n",
            encoding="utf-8")
        with self.assertRaisesRegex(ParseError, "duplicate assumption"):
            compiler.load(self.root)

    def test_invalid_invocations_return_coded_json(self):
        before = self.snapshot(self.root)
        for options, expected in [(("plan",), "invalid-selection"),
                                  (("P1", "extra"), "invalid-selection"),
                                  (("P1", "--plan", "0" * 64), "invalid-option"),
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
        self.assertEqual(result["digest"], digest)
        self.assertEqual(result["tasks"], ["T-ONE"])
        self.assertIn("archive/P1/TASKS.md", result["changedFiles"])
        self.assertIn("archive/P1/ACCEPTANCE.md", result["createdFiles"])
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


class TestArchiveReview(ArchivePlanCase):
    def split_fixture(self, newline=b"\n"):
        self.add_phase("P2")
        data = "# Notes\n\n## N-ONE\nMột.\n\n## N-TWO\nHai.\n\n## N-STAY\nKept.\n".encode("utf-8")
        (self.authority / "NOTES.md").write_bytes(data.replace(b"\n", newline))

    def test_preview_mutation_between_candidates_and_digest_cannot_authorize_new_moves(self):
        original_digest = archive.preview(self.root, "P1").digest
        real_digest = archive._plan_digest

        def changed_after_candidates(root, result):
            self.assertTrue((root / layout.COMPILED_DIR / "respond.lock").is_file())
            self.rewrite("TASKS.md", "- Status: TODO", "- Status: DONE")
            self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`")
            return real_digest(root, result)

        with mock.patch.object(archive, "_plan_digest", changed_after_candidates):
            result = archive.preview(self.root, "P1")
        self.assertEqual(result.tasks, ["T-ONE"])
        self.assertEqual(result.digest, original_digest)
        fresh = archive.preview(self.root, "P1")
        self.assertEqual(fresh.tasks, ["T-ONE", "T-TWO"])
        self.assertNotEqual(result.digest, fresh.digest)
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=result.digest)
        self.assertEqual(error.exception.code, "stale-plan")

    def test_each_file_is_read_once_under_lock_for_preview_and_apply_preparation(self):
        original_read, original_text = Path.read_bytes, Path.read_text
        files = self.snapshot()
        expected = {name: 1 for name in files}
        for action in ("preview", "dry-run", "apply"):
            reads = {}

            def counted(path, method, *args, **kwargs):
                if self.authority in path.parents:
                    name = path.relative_to(self.authority).as_posix()
                    reads[name] = reads.get(name, 0) + 1
                    self.assertTrue((self.root / layout.COMPILED_DIR / "respond.lock").is_file())
                return method(path, *args, **kwargs)

            real_replace = archive._replace

            def checked_replace(path, data):
                self.assertEqual(reads, expected)
                return real_replace(path, data)

            with mock.patch.object(Path, "read_bytes", lambda p: counted(p, original_read)), \
                    mock.patch.object(Path, "read_text", lambda p, *a, **k: counted(p, original_text, *a, **k)), \
                    mock.patch.object(archive, "_replace", side_effect=checked_replace) as replaced:
                if action == "apply":
                    archive.archive(self.root, "P1")
                    self.assertTrue(replaced.called)
                elif action == "dry-run":
                    archive.archive(self.root, "P1", dry_run=True)
                else:
                    archive.preview(self.root, "P1")
            if action != "apply":
                self.assertEqual(reads, expected)

    def test_apply_writes_and_recovery_originals_use_the_hashed_snapshot(self):
        digest = archive.preview(self.root, "P1").digest
        tasks = self.authority / "TASKS.md"
        original = tasks.read_bytes()
        real_digest, real_recovery = archive._plan_digest, archive._write_recovery

        def edited_after_hash(root, result):
            value = real_digest(root, result)
            tasks.write_bytes(original + b"\n<!-- editor arrived after snapshot -->\n")
            return value

        def captured_originals(root, originals, **kwargs):
            self.assertEqual(originals[tasks], original)
            record = real_recovery(root, originals, **kwargs)
            payload = json.loads(record.read_text(encoding="utf-8"))
            entry = next(e for e in payload["files"] if e["path"] == f"{layout.AUTHORITY_DIR}/TASKS.md")
            self.assertEqual(base64.b64decode(entry["bytes"]), original)
            return record

        with mock.patch.object(archive, "_plan_digest", edited_after_hash), \
                mock.patch.object(archive, "_write_recovery", captured_originals):
            result = archive.archive(self.root, "P1", digest=digest)
        self.assertEqual(tasks.read_bytes(), result.writes[tasks].encode("utf-8"))
        self.assertNotIn(b"editor arrived", tasks.read_bytes())

    def test_one_source_moves_once_to_several_initially_absent_phase_directories(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.split_fixture(newline)
                with mock.patch.object(archive, "RECORD_RULES", (SplitNoteRule(),)):
                    preview = archive.preview(self.root, "P1")
                    with mock.patch.object(archive, "_replace", wraps=archive._replace) as replace:
                        result = archive.archive(self.root, "P1", digest=preview.digest)
                source = self.authority / "NOTES.md"
                self.assertEqual(sum(c.args[0] == source for c in replace.call_args_list), 1)
                self.assertNotIn(b"## N-ONE", source.read_bytes())
                self.assertNotIn(b"## N-TWO", source.read_bytes())
                self.assertIn(b"## N-STAY", source.read_bytes())
                for record_id, phase, content in (("N-ONE", "P1", "Một."), ("N-TWO", "P2", "Hai.")):
                    path = self.authority / f"archive/{phase}/NOTES.md"
                    data = path.read_bytes()
                    self.assertIn(f"# Notes · {phase} (archived)".encode("utf-8"), data)
                    self.assertIn(f"## {record_id}\n{content}".encode("utf-8").replace(b"\n", newline), data)
                    self.assertEqual(sum(p.read_bytes().count(f"## {record_id}".encode())
                                         for p in self.authority.rglob("NOTES.md")), 1)
                self.assertEqual(len(result.changed_files), 3)
                self.assertEqual(result.created_files, ["archive/P1/NOTES.md", "archive/P2/NOTES.md"])

    def test_multi_destination_exception_restores_files_and_directory_inventory(self):
        self.split_fixture()
        before = self.snapshot()
        paths = sorted(p.relative_to(self.authority).as_posix() for p in self.authority.rglob("*"))
        real_replace = archive._replace
        calls = 0

        def failed(path, data):
            nonlocal calls
            real_replace(path, data)
            calls += 1
            if calls == 2:
                raise OSError("failed outside the selected phase")

        with mock.patch.object(archive, "RECORD_RULES", (SplitNoteRule(),)):
            digest = archive.preview(self.root, "P1").digest
            with mock.patch.object(archive, "_replace", failed), self.assertRaises(OSError):
                archive.archive(self.root, "P1", digest=digest)
            self.assertEqual(archive.preview(self.root, "P1").digest, digest)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.relative_to(self.authority).as_posix() for p in self.authority.rglob("*")), paths)

    def test_killed_apply_removes_created_directories_and_accepts_original_digest(self):
        repository = Path(__file__).resolve().parents[1]
        script = '''
import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from praukron import archive
from test_archive_plan import SplitNoteRule
archive.RECORD_RULES = (SplitNoteRule(),)
real_replace, calls = archive._replace, 0
def replaced(path, data):
    global calls
    real_replace(path, data)
    calls += 1
    if calls == int(sys.argv[5]):
        print("written", flush=True)
        sys.stdin.buffer.read(1)
archive._replace = replaced
archive.archive(Path(sys.argv[3]), "P1", digest=sys.argv[4])
'''
        for existing_archive in (False, True):
            for stop_after in (1, 2, 3):
                with self.subTest(existing_archive=existing_archive, stop_after=stop_after):
                    self.setUp()
                    self.split_fixture()
                    if existing_archive:
                        (self.authority / "archive/P0").mkdir(parents=True)
                    before = self.snapshot()
                    paths = sorted(p.relative_to(self.authority).as_posix() for p in self.authority.rglob("*"))
                    with mock.patch.object(archive, "RECORD_RULES", (SplitNoteRule(),)):
                        digest = archive.preview(self.root, "P1").digest
                        child = subprocess.Popen([sys.executable, "-c", script, str(repository / "src"),
                                                  str(repository / "tests"), str(self.root), digest,
                                                  str(stop_after)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE, text=True)
                        try:
                            self.assertEqual(child.stdout.readline().strip(), "written")
                        finally:
                            child.kill()
                            child.communicate(timeout=10)
                        with self.assertRaises(archive.ArchiveError) as error:
                            archive.archive(self.root, "P1", digest=digest)
                        self.assertEqual(error.exception.code, "recovered-write")
                        self.assertEqual(self.snapshot(), before)
                        self.assertEqual(sorted(p.relative_to(self.authority).as_posix()
                                                for p in self.authority.rglob("*")), paths)
                        self.assertEqual(archive.preview(self.root, "P1").digest, digest)
                        result = archive.archive(self.root, "P1", digest=digest)
                        self.assertEqual(result.digest, digest)

    def test_contract_can_move_after_earlier_archive_and_working_user_repoints(self):
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: AC-T-ONE")
        first = archive.archive(self.root, "P1")
        self.assertEqual(first.contracts, [])
        self.rewrite("TASKS.md", "- AC: AC-T-ONE\n- Evidence: —", "- AC: AC-T-TWO\n- Evidence: —")
        before = archive._records(compiler.load(self.root))
        result = archive.preview(self.root, "P1")
        self.assertEqual(result.tasks, [])
        self.assertEqual(result.contracts, ["AC-T-ONE"])
        archive.archive(self.root, "P1", digest=result.digest)
        self.assertEqual(archive._records(compiler.load(self.root)), before)
        self.assertEqual(compiler.load(self.root).contracts["AC-T-ONE"].source.file, "archive/P1/ACCEPTANCE.md")

    def test_full_heading_rule_keeps_and_reports_duplicate_headings(self):
        class HeadingRule:
            kind, filename, title = "journal", "JOURNAL.md", "Journal"
            sections = staticmethod(archive.section_locations)

            def candidates(self, context):
                for heading in self.sections(context.text(self.filename)):
                    yield archive.Candidate(heading, self.kind, self.filename, "archive/P2/JOURNAL.md")

        heading = "2026-10-15 — Cùng tiêu đề"
        unique = "2026-10-16 — Unique heading with spaces"
        data = f"# Journal\n\n## {heading}\nFirst.\n\n## {heading}\nSecond.\n\n## {unique}\nThird.\n"
        (self.authority / "JOURNAL.md").write_bytes(data.encode("utf-8"))
        with mock.patch.object(archive, "RECORD_RULES", (HeadingRule(),)):
            result = archive.preview(self.root, "P1")
            kept = next(c for c in result.candidates if c.id == heading)
            self.assertFalse(kept.moves)
            self.assertIn("duplicate section heading (2 occurrences)", kept.reason)
            self.assertIn(kept.reason, result.render(False))
            archive.archive(self.root, "P1", digest=result.digest)
        working = (self.authority / "JOURNAL.md").read_text(encoding="utf-8")
        self.assertEqual(working.count(f"## {heading}"), 2)
        self.assertIn("First.", working)
        self.assertIn("Second.", working)
        self.assertNotIn(unique, working)
        target = (self.authority / "archive/P2/JOURNAL.md").read_text(encoding="utf-8")
        self.assertIn(f"## {unique}\nThird.", target)
        self.assertNotIn(heading, target)

    def test_human_refresh_failure_still_prints_the_applied_plan_and_files(self):
        digest = archive.preview(self.root, "P1").digest
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(respond, "refresh", side_effect=OSError("refresh failed")), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-C", str(self.root), "archive", "apply", "P1", "--plan", digest])
        self.assertEqual(code, 1)
        self.assertIn("Moved 1 tasks", out.getvalue())
        self.assertIn("T-ONE", out.getvalue())
        self.assertIn(digest, out.getvalue())
        self.assertIn("archive/P1/TASKS.md", out.getvalue())
        self.assertIn("refresh-failed:", err.getvalue())


class TestArchiveReviewRoundTwo(ArchivePlanCase):
    @unittest.skipUnless(os.name == "posix", "POSIX symlinked archive root")
    def test_symlinked_root_uses_one_resolved_snapshot_write_and_recovery_identity(self):
        directory = tempfile.TemporaryDirectory(prefix="archive-root-alias-")
        self.addCleanup(directory.cleanup)
        alias = Path(directory.name).resolve() / "project"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(alias, self.root)
        self.assertEqual(alias.resolve(), self.root)

        def canonical_mode(root):
            self.assertEqual(root, self.root)
            return "nogit"

        with mock.patch.object(archive, "archive_mode", side_effect=canonical_mode):
            preview = archive.preview(alias, "P1")
            direct = archive.preview(self.root, "P1")
            self.assertEqual(preview.digest, direct.digest)
            self.assertEqual(preview.writes, direct.writes)
            self.assertEqual(preview.snapshot.authority, self.authority)
            self.assertEqual(archive._prepare_writes(alias, preview), direct.writes)
            self.assertEqual(archive.plan(compiler.load(alias), "P1", alias).writes, direct.writes)
            for path in preview.writes:
                self.assertEqual(path, path.resolve())
            dry = archive.archive(alias, "P1", dry_run=True, digest=preview.digest)
            self.assertEqual(dry.writes, preview.writes)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["-C", str(alias), "archive", "plan", "P1", "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["digest"], preview.digest)
            before = self.snapshot()
            real_replace = archive._replace

            def killed(path, data):
                self.assertEqual(path, path.resolve())
                real_replace(path, data)
                raise SystemExit("interrupted through a symlinked project root")

            with mock.patch.object(archive, "_replace", killed), self.assertRaises(SystemExit):
                archive.archive(alias, "P1", digest=preview.digest)
            with self.assertRaises(archive.ArchiveError) as error:
                archive.archive(self.root, "P1", digest=preview.digest)
            self.assertEqual(error.exception.code, "recovered-write")
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(archive.preview(alias, "P1").digest, preview.digest)
            result = archive.archive(alias, "P1", digest=preview.digest)
            self.assertEqual(result.writes, preview.writes)
            self.assertEqual(compiler.load(self.root).task("T-ONE").source.file, "archive/P1/TASKS.md")

    def require_case_insensitive_filesystem(self):
        try:
            insensitive = (self.authority / "tasks.md").samefile(self.authority / "TASKS.md")
        except FileNotFoundError:
            insensitive = False
        if not insensitive:
            self.skipTest("the fixture filesystem is case-sensitive")

    def rename_case(self, name, target):
        path = self.authority / name
        intermediate = path.with_name("case-rename-intermediate")
        path.rename(intermediate)
        intermediate.rename(self.authority / target)

    def assert_disk_snapshot_parity(self):
        disk = compiler.load(self.root)
        with archive._locked(self.root):
            snapshot = archive.Snapshot.capture(self.authority)
            captured = compiler.load(self.root, authority=snapshot.path())
        self.assertEqual(compiler.as_json(captured), compiler.as_json(disk))
        self.assertEqual(validate.errors(validate.check(captured)), [])
        self.assertEqual(validate.errors(validate.check(disk)), [])
        return snapshot

    def test_case_insensitive_working_names_and_existing_archive_targets(self):
        self.require_case_insensitive_filesystem()
        for mode in ("legacy", "digest"):
            with self.subTest(mode=mode):
                self.setUp()
                before_rename = archive.preview(self.root, "P1").digest
                self.rename_case("TASKS.md", "tasks.md")
                self.rename_case("ACCEPTANCE.md", "acceptance.md")
                snapshot = self.assert_disk_snapshot_parity()
                self.assertIn("tasks.md", snapshot.files)
                self.assertNotIn("TASKS.md", snapshot.files)
                self.assertEqual(snapshot.original("TASKS.md"), (self.authority / "tasks.md").read_bytes())
                preview = archive.preview(self.root, "P1")
                self.assertNotEqual(preview.digest, before_rename)
                self.assertNotIn("tasks.md", preview.created_files)
                self.assertNotIn("acceptance.md", preview.created_files)
                options = ("P1",) if mode == "legacy" else ("apply", "P1", "--plan", preview.digest)
                code, result = self.run_cli(*options)
                self.assertEqual(code, 0, result)
                self.assertTrue(result["written"])
                self.rename_case("archive/P1/TASKS.md", "archive/P1/tasks.md")
                self.rename_case("archive/P1/ACCEPTANCE.md", "archive/P1/acceptance.md")
                self.rename_case("archive/P1", "archive/p1")
                self.rewrite("TASKS.md", "- Status: TODO", "- Status: DONE")
                self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`")
                before = self.snapshot()
                digest = archive.preview(self.root, "P1").digest
                real_records, calls = archive._records, 0

                def drift(project):
                    nonlocal calls
                    calls += 1
                    return real_records(project) if calls == 1 else {}

                with mock.patch.object(archive, "_records", drift), self.assertRaises(archive.ArchiveError):
                    archive.archive(self.root, "P1", digest=digest)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(archive.preview(self.root, "P1").digest, digest)
                result = archive.archive(self.root, "P1", digest=digest if mode == "digest" else None)
                self.assertEqual(result.created_files, [])
                self.assertIn("archive/p1/tasks.md", result.changed_files)
                self.assertIn("archive/p1/acceptance.md", result.changed_files)
                self.assert_disk_snapshot_parity()

    def test_case_insensitive_adr_extension_disk_snapshot_parity(self):
        self.require_case_insensitive_filesystem()
        for mode in ("legacy", "digest"):
            with self.subTest(mode=mode):
                self.setUp()
                self.rename_case("ADR/ADR-001.md", "ADR/ADR-001.MD")
                snapshot = self.assert_disk_snapshot_parity()
                self.assertEqual([p.name for p in snapshot.path("adr").glob("ADR-*.md")], ["ADR-001.MD"])
                preview = archive.preview(self.root, "P1")
                options = ("P1",) if mode == "legacy" else ("apply", "P1", "--plan", preview.digest)
                code, result = self.run_cli(*options)
                self.assertEqual(code, 0, result)
                self.assertTrue(result["written"])
                self.assert_disk_snapshot_parity()

    def test_snapshot_preserves_native_case_distinctions(self):
        native_alias = (self.authority / "tasks.md").is_file()
        if not native_alias:
            (self.authority / "tasks.md").write_bytes(b"distinct lower-case file")
            (self.authority / "ADR/ADR-002.MD").write_bytes(b"not matched by a case-sensitive glob")
        with archive._locked(self.root):
            snapshot = archive.Snapshot.capture(self.authority)
        self.assertEqual(snapshot.path("tasks.md").is_file(), (self.authority / "tasks.md").is_file())
        if native_alias:
            self.assertEqual(snapshot.original("tasks.md"), snapshot.original("TASKS.md"))
        else:
            self.assertNotEqual(snapshot.original("tasks.md"), snapshot.original("TASKS.md"))
            self.assertFalse(snapshot.path("TaSkS.md").is_file())
            self.assertEqual([p.name for p in snapshot.path("ADR").glob("ADR-*.md")], ["ADR-001.md"])

    def test_kill_after_staging_fsync_before_rename_restores_inventory_and_digest(self):
        repository = Path(__file__).resolve().parents[1]
        script = '''
import json, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from praukron import archive, respond
from test_archive_plan import SplitNoteRule
archive.RECORD_RULES = (SplitNoteRule(),)
root = Path(sys.argv[3])
authority = root / ".praukron/chronicle"
real_replace, calls = respond.os.replace, 0
def staged(source, destination):
    global calls
    target = Path(destination)
    if authority in target.parents and target.name == "NOTES.md":
        calls += 1
        if calls == int(sys.argv[5]):
            print(json.dumps({"temporary": str(source), "target": str(target)}), flush=True)
            sys.stdin.buffer.read(1)
    return real_replace(source, destination)
respond.os.replace = staged
archive.archive(root, "P1", digest=sys.argv[4])
'''
        for existing_archive in (False, True):
            for stop_after in (1, 2, 3):
                with self.subTest(existing_archive=existing_archive, stop_after=stop_after):
                    self.setUp()
                    TestArchiveReview.split_fixture(self)
                    (self.authority / ".respond-unrelated").write_bytes(b"not owned by this transaction")
                    if existing_archive:
                        directory = self.authority / "archive/P0"
                        directory.mkdir(parents=True)
                        (directory / ".respond-another-transaction").write_bytes(b"keep this too")
                    before = self.snapshot()
                    paths = sorted(p.relative_to(self.authority).as_posix() for p in self.authority.rglob("*"))
                    with mock.patch.object(archive, "RECORD_RULES", (SplitNoteRule(),)):
                        preview = archive.preview(self.root, "P1")
                        child = subprocess.Popen([sys.executable, "-c", script, str(repository / "src"),
                                                  str(repository / "tests"), str(self.root), preview.digest,
                                                  str(stop_after)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE, text=True)
                        try:
                            staged = json.loads(child.stdout.readline())
                            temporary, target = Path(staged["temporary"]), Path(staged["target"])
                            self.assertEqual(temporary.read_bytes(), preview.writes[target].encode("utf-8"))
                            record = next((self.root / layout.RECOVERY_DIR).glob("archive-*.json"))
                            payload = json.loads(record.read_text(encoding="utf-8"))
                            self.assertIn(temporary.relative_to(self.root).as_posix(),
                                          [entry["temporary"] for entry in payload["files"]])
                        finally:
                            child.kill()
                            child.communicate(timeout=10)
                        self.assertTrue(temporary.is_file())
                        with self.assertRaises(archive.ArchiveError) as error:
                            archive.archive(self.root, "P1", digest=preview.digest)
                        self.assertEqual(error.exception.code, "recovered-write")
                        self.assertFalse(temporary.exists())
                        self.assertEqual(self.snapshot(), before)
                        self.assertEqual(sorted(p.relative_to(self.authority).as_posix()
                                                for p in self.authority.rglob("*")), paths)
                        self.assertEqual(archive.preview(self.root, "P1").digest, preview.digest)
                        archive.archive(self.root, "P1", digest=preview.digest)


if __name__ == "__main__":
    unittest.main()
