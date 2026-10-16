"""Assumptions, owner responses, and owner-held tasks (P3, ADR-054, ADR-057).

Kept apart from test_praukron.py so the phase's behaviour can be read in one
place. The fixtures are shared.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, layout, validate  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("permission", "security", "external-disclosure", "legal", "source-of-truth")


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestAssumptionSpecification(unittest.TestCase):
    """T-ASSUME-SPEC-01: the protocol is written down before it is compiled."""

    def test_spec_and_templates_define_the_records(self) -> None:
        spec = _flat((ROOT / "docs/SPEC.md").read_text())
        for field in ("`Status`", "`Tasks`", "`Impact`", "`Recorded`", "`Assumption`", "`Basis`",
                      "`Applied in`", "`Permissions`", "`Responses`", "`Reconciled by`",
                      "`By`", "`Date`", "`Via`", "`Authority: owner`"):
            with self.subTest(field=field):
                self.assertIn(field, spec)
        for status in ("`OPEN`", "`CONFIRMED`", "`REVISED`", "`REJECTED`", "`WITHDRAWN`"):
            self.assertIn(status, spec)
        self.assertIn("holds no authority by itself", spec)
        self.assertIn("researched is not confirmed", spec)
        for name in ("ASSUMPTIONS.md", "RESPONSES.md"):
            self.assertTrue((ROOT / "templates/chronicle" / name).is_file(), name)
        self.assertIn("TECH_DEBT ASSUMPTIONS RESPONSES; do", (ROOT / "install.sh").read_text())

    def test_spec_states_the_boundary_and_the_default_policy(self) -> None:
        spec = _flat((ROOT / "docs/SPEC.md").read_text())
        for group in GROUPS:
            self.assertIn(f"`{group}`", spec)
        for rule in ("destructive or irreversible actions are reserved",
                     "Without one, all five groups are reserved and `Notify: host` applies",
                     "only by superseding its policy ADR",
                     "carries `Permissions:` and is `HIGH`",
                     "Praukron sends nothing itself"):
            with self.subTest(rule=rule):
                self.assertIn(rule, spec)

    def test_spec_separates_responded_from_reconciled(self) -> None:
        spec = _flat((ROOT / "docs/SPEC.md").read_text())
        self.assertIn("*Responded* means", spec)
        self.assertIn("*Reconciled* means", spec)
        for row in ("new → `OPEN`", "`OPEN` → `CONFIRMED`", "→ `REVISED`", "`OPEN` → `REJECTED`",
                    "`OPEN` → `WITHDRAWN`", "plus `GUIDE`"):
            self.assertIn(row, spec)

    def test_init_offers_the_optional_policy_step_on_every_host(self) -> None:
        text = _flat((ROOT / ".praukron/commands/praukron-init.md").read_text())
        for group in GROUPS:
            self.assertIn(f"`{group}`", text)
        for rule in ("offer the owner one optional step", "`- Policy: assumptions`",
                     "`- Reserved:", "`- Notify: host`", "If the owner skips the step, record nothing",
                     "always stop whatever the answer"):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        for host in (".claude/commands/praukron-init.md", ".opencode/commands/praukron-init.md"):
            self.assertIn(".praukron/commands/praukron-init.md", (ROOT / host).read_text())
        self.assertIn("`init`", (ROOT / ".agents/skills/praukron/SKILL.md").read_text())

    def test_a_chronicle_without_the_new_files_compiles_as_before(self) -> None:
        """AC-T-ASSUME-SPEC-01-04: nothing is migrated; empty templates change nothing."""
        outputs = []
        for with_templates in (False, True):
            directory = Path(tempfile.mkdtemp(prefix="praukron-assume-"))
            self.addCleanup(shutil.rmtree, directory, True)
            build_fixture(directory)
            authority = directory / layout.AUTHORITY_DIR
            before = {p.name: p.read_text() for p in authority.glob("*.md")}
            if with_templates:
                for name in ("ASSUMPTIONS.md", "RESPONSES.md"):
                    shutil.copy(ROOT / "templates/chronicle" / name, authority / name)
            project = compiler.load(directory)
            self.assertEqual(validate.errors(validate.check(project)), [])
            data = compiler.as_json(project)
            data["sources"] = None
            data["project"]["name"] = None
            outputs.append(json.dumps(data, sort_keys=True))
            for name, text in before.items():
                self.assertEqual((authority / name).read_text(), text)
        self.assertEqual(outputs[0], outputs[1])


class TestAgentGuidance(unittest.TestCase):
    """T-ASSUME-AGENT-01: every agent surface teaches the same procedure."""

    def test_agents_md_and_work_give_the_decision_procedure(self) -> None:
        agents = _flat((ROOT / "AGENTS.md").read_text())
        for rule in ("search authority first", "researched is not confirmed",
                     "authority resolves it: follow it", "`Authority: owner`",
                     "`Notify: host`", "record it in `ASSUMPTIONS.md` before relying on it",
                     "cite its id where it is applied and in any evidence that rests on it",
                     "`Permissions:` and `Impact: HIGH`",
                     "Never present an `OPEN` assumption as decided"):
            with self.subTest(rule=rule):
                self.assertIn(rule, agents)
        work = _flat((ROOT / ".praukron/commands/praukron-work.md").read_text())
        for rule in ("read the packet's `policy`", "before relying on it",
                     "in any criterion evidence that rests on it", "`Authority: owner` task",
                     "record the time and channel on that task", "continue other work"):
            with self.subTest(rule=rule):
                self.assertIn(rule, work)

    def test_checkpoint_reconciles_and_never_edits_owner_text(self) -> None:
        text = _flat((ROOT / ".praukron/commands/praukron-checkpoint.md").read_text())
        for rule in ("Reconcile every owner response", "awaiting reconciliation",
                     "through an Acceptance Change Request", "`Reconciled by`",
                     "Never edit, reorder, or clear an entry in `RESPONSES.md`",
                     "`Via: relayed by <agent> from <channel>`",
                     "the open assumptions and owner-held blockers"):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)
        agents = _flat((ROOT / "AGENTS.md").read_text())
        self.assertIn("never edit or clear one", agents)
        self.assertIn("`Via: relayed by <you> from <channel>`", agents)

    def test_every_host_carries_the_same_rules_and_the_installer_ships_them(self) -> None:
        for command in ("work", "checkpoint"):
            for host in (".claude", ".opencode"):
                with self.subTest(host=host, command=command):
                    self.assertIn(f".praukron/commands/praukron-{command}.md",
                                  (ROOT / host / "commands" / f"praukron-{command}.md").read_text())
        skill = _flat((ROOT / ".agents/skills/praukron/SKILL.md").read_text())
        self.assertIn("assumption procedure in `AGENTS.md`", skill)
        self.assertIn("never edit `RESPONSES.md`", skill)
        installer = (ROOT / "install.sh").read_text()
        for shipped in ('"$source_dir/.praukron/commands/praukron-$command.md"',
                        '"$source_dir/.claude/commands/praukron-$command.md"',
                        '"$source_dir/.opencode/commands/praukron-$command.md"',
                        '"$source_dir/.agents/skills/praukron/SKILL.md"',
                        'install_block AGENTS.md "$source_dir/AGENTS.md"'):
            self.assertIn(shipped, installer)


ASSUMPTIONS = """# Assumptions

## A-1: Payment term defaults to thirty days
- Status: OPEN
- Tasks: T-ONE, T-TWO
- Impact: HIGH
- Recorded: 2026-10-01 · claude/primary
- Assumption: Each client has one payment term in days,
  default 30.
- Basis: No term exists in the data. Researched, not confirmed.
- Applied in: ADR-001, AC-T-ONE-01, `clients.payment_terms_days`
- Responses: R-1
- Reconciled by: none

## A-2: Sales may read their own clients only
- Status: OPEN
- Tasks: T-TWO
- Impact: HIGH
- Recorded: 2026-10-02 · claude/primary
- Assumption: Sales read only clients they own.
- Basis: Smallest access that works.
- Applied in: T-TWO
- Permissions: sales read own clients
- Responses: none
- Reconciled by: none
"""

RESPONSES = """# Owner responses

## R-1: A-1 REVISE
- By: Thien (owner)
- Date: 2026-10-02
- Via: dashboard

> Mặc định 7 ngày, không phải 30.
>
> Ghi chú: **order/payment** vẫn là source of truth.

## R-2: T-TWO GUIDE
- By: Thien (owner)
- Date: 2026-10-02
- Via: relayed by claude/primary from chat

> Use the staging backup. Do not touch production.
"""

POLICY_ADR = """# ADR-002: Assumption policy
- Date: 2026-10-01
- Status: ACCEPTED
- Authority: owner
- Policy: assumptions
- Reserved: security, legal
- Notify: host
- Decision: Agents may assume permission choices here.
"""


class AssumptionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-assume-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        build_fixture(self.dir)
        self.authority = self.dir / layout.AUTHORITY_DIR
        self.write(ASSUMPTIONS, RESPONSES, POLICY_ADR)
        # Who responds is the active gh login (ADR-060); tests never ask gh.
        identity = mock.patch("praukron.respond.identity", return_value="thien-owner")
        identity.start()
        self.addCleanup(identity.stop)

    def write(self, assumptions: str | None = None, responses: str | None = None,
              policy: str | None = None) -> None:
        if assumptions is not None:
            (self.authority / "ASSUMPTIONS.md").write_text(assumptions)
        if responses is not None:
            (self.authority / "RESPONSES.md").write_text(responses)
        if policy is not None:
            (self.authority / "ADR" / "ADR-002.md").write_text(policy)
        self.load()

    def load(self) -> None:
        self.project = compiler.load(self.dir)

    def errors(self) -> list[str]:
        return [f.code for f in validate.errors(validate.check(self.project))]


class TestAssumptionModel(AssumptionCase):
    """T-ASSUME-MODEL-01: parsed exactly, validated strictly."""

    def test_records_compile_as_written_with_derived_lineage(self) -> None:
        self.assertEqual(self.errors(), [])
        data = compiler.as_json(self.project)
        first = data["assumptions"][0]
        self.assertEqual(first["assumption"], "Each client has one payment term in days, default 30.")
        self.assertEqual(first["references"], ["ADR-001", "AC-T-ONE-01"])
        self.assertEqual(first["appliedIn"], "ADR-001, AC-T-ONE-01, `clients.payment_terms_days`")
        self.assertEqual((first["phases"], first["modules"]), (["P1"], ["M-FOUNDATION"]))
        self.assertEqual(data["assumptions"][1]["permissions"], "sales read own clients")
        owner = data["responses"][0]
        self.assertEqual(owner["text"], "Mặc định 7 ngày, không phải 30.\n\n"
                                        "Ghi chú: **order/payment** vẫn là source of truth.")
        self.assertEqual((owner["target"], owner["action"], owner["by"]), ("A-1", "REVISE", "Thien (owner)"))
        self.assertEqual(data["responses"][1]["via"], "relayed by claude/primary from chat")
        self.assertEqual({t["id"]: t["assumptions"] for t in data["tasks"]}["T-TWO"], ["A-1", "A-2"])
        self.assertEqual(data["policy"], {"reserved": ["security", "legal"], "notify": "host",
                                          "decision": "ADR-002", "default": False})

    def test_unknown_references_values_and_duplicates_are_errors(self) -> None:
        block = ASSUMPTIONS.split("# Assumptions\n", 1)[1]
        cases = {
            "unknown-assumption-reference": ASSUMPTIONS.replace("Tasks: T-ONE, T-TWO", "Tasks: T-GHOST"),
            "invalid-assumption-status": ASSUMPTIONS.replace("Status: OPEN", "Status: MAYBE", 1),
            "invalid-impact": ASSUMPTIONS.replace("Impact: HIGH", "Impact: HUGE", 1),
            "duplicate-assumption": ASSUMPTIONS + block,
            "unknown-response-reference": ASSUMPTIONS.replace("Responses: R-1", "Responses: R-9"),
            "assumption-without-task": ASSUMPTIONS.replace("Tasks: T-TWO\n", "Tasks: none\n"),
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                self.write(assumptions=text)
                self.assertIn(code, self.errors())
        self.write(assumptions=ASSUMPTIONS.replace("ADR-001, AC-T-ONE-01", "ADR-404, AC-T-NOPE-09"))
        self.assertEqual(self.errors().count("unknown-assumption-reference"), 2)
        self.write(assumptions=ASSUMPTIONS)
        response_cases = {
            "unknown-response-target": RESPONSES.replace("R-2: T-TWO GUIDE", "R-2: A-77 GUIDE"),
            "invalid-response-action": RESPONSES.replace("R-1: A-1 REVISE", "R-1: A-1 MAYBE"),
            "duplicate-response": RESPONSES.replace("R-2: T-TWO", "R-1: T-TWO"),
            "incomplete-response": RESPONSES.replace("- By: Thien (owner)\n", "", 1),
        }
        for code, text in response_cases.items():
            with self.subTest(code=code):
                self.write(responses=text)
                self.assertIn(code, self.errors())
        self.write(responses=RESPONSES.replace("R-2: T-TWO GUIDE", "R-2: T-TWO CONFIRM"))
        self.assertIn("invalid-response-action", self.errors())
        self.write(responses=RESPONSES.replace("> Use the staging backup. Do not touch production.\n", ""))
        self.assertIn("incomplete-response", self.errors())

    def test_status_needs_its_response_and_reconciliation(self) -> None:
        confirmed = ASSUMPTIONS.replace("Status: OPEN", "Status: CONFIRMED", 1)
        self.write(assumptions=confirmed)
        self.assertIn("status-without-response", self.errors())
        revised = ASSUMPTIONS.replace("Status: OPEN", "Status: REVISED", 1)
        self.write(assumptions=revised)
        self.assertNotIn("status-without-response", self.errors())
        self.assertIn("missing-reconciliation", self.errors())
        self.write(assumptions=revised.replace("Reconciled by: none", "Reconciled by: ADR-001", 1))
        self.assertEqual(self.errors(), [])
        self.write(assumptions=ASSUMPTIONS.replace("Status: OPEN", "Status: REJECTED", 1)
                   .replace("Reconciled by: none", "Reconciled by: ADR-001", 1))
        self.assertIn("status-without-response", self.errors())
        withdrawn = ASSUMPTIONS.replace("Status: OPEN", "Status: WITHDRAWN", 1)
        self.write(assumptions=withdrawn)
        self.assertIn("missing-reconciliation", self.errors())
        self.write(assumptions=withdrawn.replace("Reconciled by: none", "Reconciled by: a stale key, not a rule", 1))
        self.assertEqual(self.errors(), [])

    def test_owner_held_task_is_done_only_human_verified(self) -> None:
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text()
        tasks.write_text(text.replace("- Status: DONE\n- Module: M-FOUNDATION\n",
                                      "- Status: DONE\n- Module: M-FOUNDATION\n- Authority: owner\n", 1))
        self.load()
        self.assertIn("owner-task-not-verified", self.errors())
        tasks.write_text(tasks.read_text().replace("- Validation: SYNTHETIC", "- Validation: HUMAN_VERIFIED", 1))
        self.load()
        self.assertNotIn("owner-task-not-verified", self.errors())
        self.assertTrue(self.project.task("T-ONE").owner_held)
        tasks.write_text(tasks.read_text().replace("- Authority: owner", "- Authority: anyone", 1))
        self.load()
        self.assertIn("invalid-task-authority", self.errors())

    def test_a_permission_assumption_must_be_high(self) -> None:
        self.write(assumptions=ASSUMPTIONS.replace("- Impact: HIGH\n- Recorded: 2026-10-02",
                                                   "- Impact: MEDIUM\n- Recorded: 2026-10-02"))
        self.assertIn("permission-not-high", self.errors())

    def test_the_policy_in_force_and_its_errors(self) -> None:
        self.assertEqual(self.project.policy.decision, "ADR-002")
        self.assertNotIn("reserved-assumption", self.errors())
        newer = POLICY_ADR.replace("ADR-002", "ADR-003").replace("security, legal", "permission, security")
        (self.authority / "ADR" / "ADR-003.md").write_text(newer)
        self.load()
        self.assertEqual(self.project.policy.decision, "ADR-003")
        self.assertIn("reserved-assumption", self.errors())
        (self.authority / "ADR" / "ADR-003.md").write_text(newer.replace("Status: ACCEPTED", "Status: PROPOSED"))
        self.load()
        self.assertEqual(self.project.policy.decision, "ADR-002")
        (self.authority / "ADR" / "ADR-002.md").unlink()
        (self.authority / "ADR" / "ADR-003.md").unlink()
        self.load()
        default = self.project.policy
        self.assertEqual((default.decision, default.notify), (None, "host"))
        self.assertEqual(default.reserved, list(GROUPS))
        self.assertIn("reserved-assumption", self.errors())
        self.write(policy=POLICY_ADR.replace("security, legal", "secrets").replace("Notify: host", "Notify: sms"))
        self.assertIn("invalid-policy-group", self.errors())
        self.assertIn("invalid-policy-notify", self.errors())
        self.write(policy=POLICY_ADR.replace("- Reserved: security, legal\n", ""))
        self.assertIn("incomplete-policy", self.errors())
        packet = analytics.context(self.project, "T-TWO")
        self.assertIn("policy", packet)


class TestAssumptionProjection(AssumptionCase):
    """T-ASSUME-PROJECT-01: derived diagnostics, actors, packets, one-line summaries."""

    def setUp(self) -> None:
        super().setUp()
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text()
        text = text.replace("- Status: DONE\n- Module: M-FOUNDATION\n- Validation: SYNTHETIC",
                            "- Status: DONE\n- Module: M-FOUNDATION\n- Validation: UNTESTED", 1)
        text = text.replace("## T-TWO: Build on it\n- Status: TODO\n- Module: M-FOUNDATION\n",
                            "## T-TWO: Build on it\n- Status: WIP\n- Module: M-FOUNDATION\n- Authority: owner\n", 1)
        text = text.replace("- Schedule: start=2026-10-01 estimate=3d", "- Schedule: start=2026-10-01")
        tasks.write_text(text)
        acceptance = self.authority / "ACCEPTANCE.md"
        acceptance.write_text(acceptance.read_text().replace("Measured on site.", "Measured on site under A-1."))
        self.load()
        self.report = analytics.report(self.project)
        self.data = compiler.as_json(self.project)

    def test_diagnostics_name_their_ids(self) -> None:
        """AC-T-ASSUME-PROJECT-01-01."""
        r = self.data["assumptionReport"]
        self.assertEqual(r["open"], ["A-1", "A-2"])
        self.assertEqual(r["openHigh"], ["A-1", "A-2"])
        self.assertEqual(r["openPermission"], ["A-2"])
        self.assertEqual(r["openOnCurrentWork"], ["A-1", "A-2"])
        self.assertEqual(r["awaitingReconciliation"], [{"assumption": "A-1", "response": "R-1", "action": "REVISE"}])
        self.assertEqual(r["openOnDoneTasks"], [{"assumption": "A-1", "tasks": ["T-ONE"]}])
        self.assertEqual(r["passRestingOnOpen"], [{"criterion": "AC-T-ONE-01", "assumption": "A-1"}])
        self.assertEqual(r["openOnExits"], ["A-1", "A-2"])
        self.assertEqual(r["guidanceOnOpenTasks"], [{"response": "R-2", "task": "T-TWO"}])
        self.assertEqual(r["ownerHeld"], ["T-TWO"])

    def test_reconciling_clears_the_diagnostic_but_nothing_derives_a_status(self) -> None:
        self.assertEqual(self.project.assumption("A-1").status, "OPEN")
        self.write(assumptions=ASSUMPTIONS.replace("Status: OPEN", "Status: REVISED", 1)
                   .replace("Reconciled by: none", "Reconciled by: ADR-001", 1))
        report = analytics.report(self.project)
        self.assertEqual(report.assumptions["awaitingReconciliation"], [])
        self.assertNotIn("A-1", report.assumptions["open"])

    def test_every_obstacle_names_an_actor_and_an_unblock(self) -> None:
        """AC-T-ASSUME-PROJECT-01-02."""
        kinds = {o["type"] for o in self.data["obstacles"]}
        self.assertEqual(kinds, {"DEPENDENCY_BLOCKER", "ACCEPTANCE_BLOCKER", "GATE_BLOCKER",
                                 "PHASE_BLOCKER", "VALIDATION_GAP", "SCHEDULE_BLOCKER"})
        for obstacle in self.data["obstacles"]:
            with self.subTest(obstacle=obstacle["type"]):
                self.assertIn(obstacle["actor"], ("agent", "owner", "operations"))
                self.assertTrue(obstacle["unblock"])
        dependency = next(o for o in self.data["obstacles"] if o["type"] == "DEPENDENCY_BLOCKER")
        self.assertEqual((dependency["subject"], dependency["actor"], dependency["unblock"]),
                         ("T-THREE", "owner", "the owner finishes T-TWO"))
        acceptance = next(o for o in self.data["obstacles"] if o["type"] == "ACCEPTANCE_BLOCKER")
        self.assertEqual(acceptance["actor"], "owner")
        self.assertEqual(self.data["assumptionReport"]["waitingOnOwner"],
                         [{"task": "T-THREE", "holders": ["T-TWO"], "unblock": "the owner finishes T-TWO"}])
        self.assertEqual(self.data["execution"]["mainBlocker"]["actor"], "agent")

    def test_the_task_packet_carries_its_assumptions_and_the_owners_words(self) -> None:
        """AC-T-ASSUME-PROJECT-01-03."""
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual([a["id"] for a in packet["assumptions"]], ["A-1", "A-2"])
        self.assertTrue(packet["assumptions"][0]["awaitingReconciliation"])
        self.assertEqual([r["id"] for r in packet["responses"]], ["R-1", "R-2"])
        self.assertEqual(packet["responses"][0]["text"].splitlines()[0], "Mặc định 7 ngày, không phải 30.")
        for pointer in ("ASSUMPTIONS.md#A-1", "ASSUMPTIONS.md#A-2", "RESPONSES.md#R-1", "RESPONSES.md#R-2"):
            self.assertIn(pointer, packet["authority"]["read"])
        # Guidance on a task's open dependency reaches the task it blocks.
        downstream = analytics.context(self.project, "T-THREE")
        self.assertEqual([r["id"] for r in downstream["responses"]], ["R-2"])

    def test_status_and_index_carry_one_line(self) -> None:
        """AC-T-ASSUME-PROJECT-01-04."""
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            from praukron import cli
            cli.main(["-C", str(self.dir), "status"])
        expected = ("2 open · 2 HIGH · 1 permission · 1 awaiting reconciliation · "
                    "owner-held: T-THREE (the owner finishes T-TWO)")
        self.assertIn(f"assumptions  {expected}", out.getvalue())
        compiler.write(self.dir, self.project)
        self.assertIn(f"- assumptions: {expected}", (self.authority / "INDEX.md").read_text())

    def test_deterministic_and_never_written_back(self) -> None:
        """AC-T-ASSUME-PROJECT-01-05."""
        before = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file() and p.name != "INDEX.md"}
        compiler.write(self.dir, self.project)
        first = (self.dir / layout.COMPILED_DIR / "project.json").read_text()
        self.load()
        compiler.write(self.dir, self.project)
        self.assertEqual((self.dir / layout.COMPILED_DIR / "project.json").read_text(), first)
        after = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file() and p.name != "INDEX.md"}
        self.assertEqual(after, before)
        self.assertEqual([a.status for a in self.project.assumptions], ["OPEN", "OPEN"])

    def test_a_permission_assumption_is_marked_in_the_packet(self) -> None:
        """AC-T-ASSUME-PROJECT-01-06."""
        entry = analytics.context(self.project, "T-TWO")["assumptions"][1]
        self.assertTrue(entry["permission"])
        self.assertEqual(entry["permissions"], "sales read own clients")
        self.assertFalse(analytics.context(self.project, "T-TWO")["assumptions"][0]["permission"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
