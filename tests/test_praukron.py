"""Regression suite for the Praukron runtime.

The fixture project is deliberately small and deliberately broken in places:
several tests exist to prove that validation refuses to compile nonsense.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import (  # noqa: E402
    analytics, cli, compile as compiler, dashboard, index, layout, mermaid, migrate,
    retrieve, validate, views,
)
from praukron.parse import ParseError  # noqa: E402

PHASES = """# Phases

## P1 — Foundation

Outcome:
The thing works.

Entry:
- A repository exists.

Exit:
- Gate A.

Exit authority:
T-TWO

Status:
ACTIVE

---

# Gates

## Gate A — Single authority

Only one source of truth exists.

Blocks: P1 exit
Verified by: `AC-GLOBAL-CARE`
Status: RED

---

# Milestones

- `M-FIRST` — First light. Reached when T-ONE is `DONE`.
"""

THESIS = """# Product thesis

- Statement: A building people can live in.
- Source: docs/THESIS.md
"""

MODULES = """# Modules

## M-FOUNDATION — Foundation and walls
- Phase: P1
- Outcome: The structure stands.

## M-UPKEEP — Upkeep
- Phase: P-NONE
- Outcome: The site stays usable.
"""

TASKS = """# Tasks

## T-ONE: Lay the foundation
- Status: DONE
- Module: M-FOUNDATION
- Validation: SYNTHETIC
- Dependencies: none
- Owner: someone
- AC: AC-T-ONE
- Evidence: It holds weight.
- Governed by: ADR-001

## T-TWO: Build on it
- Status: TODO
- Module: M-FOUNDATION
- Validation: UNTESTED
- Dependencies: T-ONE
- Owner: unassigned
- AC: AC-T-TWO
- Evidence: —
- Governed by: ADR-001

## T-THREE: Finish later
- Status: TODO
- Module: M-FOUNDATION
- Validation: UNTESTED
- Dependencies: T-TWO
- Owner: unassigned
- AC: AC-T-THREE
- Schedule: start=2026-10-01 estimate=3d
- Evidence: —
- Governed by: ADR-001
"""

ACCEPTANCE = """# Acceptance

## Inherited contracts

### AC-GLOBAL-CARE

- `AC-GLOBAL-CARE-01` — Given anything, When it ships, Then it is careful.
  `INSPECTION`

---

# Contracts

## AC-T-ONE — Lay the foundation

Inherits: `AC-GLOBAL-CARE`

- `AC-T-ONE-01` — Given a site, When the foundation is poured, Then it is
  level. `TEST` · `PASS`
  - Evidence: Measured on site.

## AC-T-TWO — Build on it

- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.
  `TEST` · `NOT_RUN`

## AC-T-THREE — Finish later

- `AC-T-THREE-01` — Given walls, When the roof goes on, Then it keeps rain out.
  `RUNTIME` · `NOT_RUN`
"""

ADR = """# ADR-001: Do the simple thing
- Date: 2026-01-01
- Status: ACCEPTED
- Supersedes: none
- Affects: T-ONE, T-TWO
- Context: Complexity is expensive.
- Decision: Prefer the simple thing.
"""


def build_fixture(root: Path, authority: str = layout.AUTHORITY_DIR) -> Path:
    authority = root / authority
    (authority / "ADR").mkdir(parents=True)
    (authority / "PHASES.md").write_text(PHASES, encoding="utf-8")
    (authority / "THESIS.md").write_text(THESIS, encoding="utf-8")
    (authority / "MODULES.md").write_text(MODULES, encoding="utf-8")
    (authority / "TASKS.md").write_text(TASKS, encoding="utf-8")
    (authority / "ACCEPTANCE.md").write_text(ACCEPTANCE, encoding="utf-8")
    (authority / "ADR" / "ADR-001.md").write_text(ADR, encoding="utf-8")
    (authority / "INTENT.md").write_text("# Intent\n\nNo active intent.\n", encoding="utf-8")
    (authority / "HANDOFF.md").write_text("# Handoff\n\nNothing in flight.\n", encoding="utf-8")
    return root


class FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = build_fixture(self.dir)
        self.project = compiler.load(self.root)

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = self.root / layout.AUTHORITY_DIR / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        self.project = compiler.load(self.root)

    def codes(self) -> set[str]:
        return {finding.code for finding in validate.check(self.project)}


class TestParsing(FixtureCase):
    def test_tasks_parse_with_fields_and_provenance(self) -> None:
        task = self.project.task("T-ONE")
        self.assertEqual(task.title, "Lay the foundation")
        self.assertEqual(task.phase, "P1")
        self.assertEqual(task.dependencies, [])
        self.assertEqual(task.decisions, ["ADR-001"])
        self.assertEqual(task.source.file, "TASKS.md")
        self.assertEqual(task.source.anchor, "T-ONE")

    def test_dependencies_and_em_dash_evidence(self) -> None:
        self.assertEqual(self.project.task("T-TWO").dependencies, ["T-ONE"])
        self.assertIsNone(self.project.task("T-TWO").evidence)

    def test_schedule_metadata_is_optional_and_typed(self) -> None:
        self.assertFalse(self.project.task("T-TWO").schedule.known)
        schedule = self.project.task("T-THREE").schedule
        self.assertEqual(schedule.start, "2026-10-01")
        self.assertEqual(schedule.estimate, "3d")
        self.assertTrue(schedule.known)

    def test_criteria_keep_class_state_and_evidence(self) -> None:
        criterion = self.project.contracts["AC-T-ONE"].criteria[0]
        self.assertEqual(criterion.evidence_class, "TEST")
        self.assertEqual(criterion.state, "PASS")
        self.assertEqual(criterion.evidence, "Measured on site.")

    def test_global_contracts_are_invariants_not_task_criteria(self) -> None:
        self.assertEqual([c.id for c in self.project.invariants], ["AC-GLOBAL-CARE"])
        task = self.project.task("T-ONE")
        self.assertEqual([c.id for c in self.project.mandatory_criteria(task)], ["AC-T-ONE-01"])
        self.assertEqual([c.id for c in self.project.invariants_for(task)], ["AC-GLOBAL-CARE"])

    def test_a_nested_section_does_not_swallow_later_contracts(self) -> None:
        self.assertIn("AC-T-THREE", self.project.contracts)
        self.assertEqual(len(self.project.contracts["AC-GLOBAL-CARE"].criteria), 1)

    def test_phases_gates_and_milestones(self) -> None:
        phase = self.project.phase("P1")
        self.assertEqual(phase.status, "ACTIVE")
        self.assertEqual(phase.exit_authority, "T-TWO")
        self.assertEqual(self.project.gates[0].status, "RED")
        self.assertEqual(self.project.gates[0].blocks, ["P1 exit"])
        self.assertEqual(self.project.milestones[0].task, "T-ONE")

    def test_malformed_task_heading_names_the_file(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "TASKS.md").write_text("# Tasks\n\n## nonsense\n", encoding="utf-8")
        with self.assertRaises(ParseError) as caught:
            compiler.load(self.root)
        self.assertEqual(caught.exception.file, "TASKS.md")

    def test_missing_required_field_is_rejected(self) -> None:
        self.rewrite_raw("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC\n", "")
        with self.assertRaises(ParseError) as caught:
            compiler.load(self.root)
        self.assertIn("missing required field", str(caught.exception))

    def rewrite_raw(self, name: str, old: str, new: str) -> None:
        path = self.root / layout.AUTHORITY_DIR / name
        path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")


class TestValidation(FixtureCase):
    def test_clean_authority_reports_nothing(self) -> None:
        self.assertEqual(validate.check(self.project), [])

    def test_unknown_dependency(self) -> None:
        self.rewrite("TASKS.md", "- Dependencies: T-ONE", "- Dependencies: T-GHOST")
        self.assertIn("unknown-dependency", self.codes())

    def test_duplicate_task(self) -> None:
        path = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        path.write_text(path.read_text(encoding="utf-8") + TASKS.split("# Tasks\n")[1].split("## T-TWO")[0], encoding="utf-8")
        self.project = compiler.load(self.root)
        self.assertIn("duplicate-task", self.codes())

    def test_unknown_phase(self) -> None:
        self.rewrite("MODULES.md", "- Phase: P1", "- Phase: P9")
        self.assertIn("unknown-module-phase", self.codes())

    def test_missing_contract_reference(self) -> None:
        self.rewrite("TASKS.md", "- AC: AC-T-TWO\n", "")
        self.assertIn("missing-contract", self.codes())

    def test_unknown_contract_reference(self) -> None:
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: AC-T-NOWHERE")
        self.assertIn("unknown-contract", self.codes())

    def test_done_task_with_unmet_criterion(self) -> None:
        self.rewrite("ACCEPTANCE.md", "level. `TEST` · `PASS`", "level. `TEST` · `NOT_RUN`")
        self.assertIn("unresolved-acceptance", self.codes())

    def test_duplicate_criterion_within_a_contract_is_rejected_at_parse_time(self) -> None:
        with self.assertRaises(ParseError):
            self.rewrite(
                "ACCEPTANCE.md",
                "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.\n  `TEST` · `NOT_RUN`",
                "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.\n  `TEST` · `NOT_RUN`\n"
                "- `AC-T-TWO-01` — Given a copy, When it appears, Then it is caught.\n  `TEST` · `NOT_RUN`",
            )

    def test_duplicate_criterion_across_contracts_is_a_validation_error(self) -> None:
        self.rewrite(
            "ACCEPTANCE.md",
            "- `AC-T-THREE-01` — Given walls, When the roof goes on, Then it keeps rain out.\n  `RUNTIME` · `NOT_RUN`",
            "- `AC-T-TWO-01` — Given a stolen identifier, When it reappears, Then it is caught.\n  `RUNTIME` · `NOT_RUN`",
        )
        self.assertIn("duplicate-criterion", self.codes())

    def test_unknown_adr_reference(self) -> None:
        self.rewrite("TASKS.md", "- Governed by: ADR-001", "- Governed by: ADR-404")
        self.assertIn("unknown-decision", self.codes())

    def test_dependency_cycle(self) -> None:
        self.rewrite("TASKS.md", "## T-ONE: Lay the foundation\n- Status: DONE", "## T-ONE: Lay the foundation\n- Status: TODO")
        self.rewrite("TASKS.md", "- Dependencies: none", "- Dependencies: T-THREE")
        self.assertIn("dependency-cycle", self.codes())

    def test_missing_phase_exit_task(self) -> None:
        self.rewrite("PHASES.md", "Exit authority:\nT-TWO", "Exit authority:\nT-MISSING")
        self.assertIn("missing-exit-authority", self.codes())

    def test_gate_referencing_unknown_contract(self) -> None:
        self.rewrite("PHASES.md", "Verified by: `AC-GLOBAL-CARE`", "Verified by: `AC-NOT-REAL`")
        self.assertIn("unknown-gate-reference", self.codes())

    def test_invalid_status_and_validation(self) -> None:
        self.rewrite("TASKS.md", "- Status: TODO\n- Module: M-FOUNDATION\n- Validation: UNTESTED", "- Status: MAYBE\n- Module: M-FOUNDATION\n- Validation: VIBES")
        codes = self.codes()
        self.assertIn("invalid-status", codes)
        self.assertIn("invalid-validation", codes)

    def test_warnings_do_not_block(self) -> None:
        self.rewrite(
            "TASKS.md",
            "## T-TWO: Build on it\n- Status: TODO",
            "## T-TWO: Build on it\n- Schedule: start=2026-10-01\n- Status: TODO",
        )
        findings = validate.check(self.project)
        self.assertTrue(findings)
        self.assertEqual(validate.errors(findings), [])

    def test_two_active_phases_are_an_error(self) -> None:
        path = self.root / layout.AUTHORITY_DIR / "PHASES.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "---\n\n# Gates",
                "---\n\n## P2 — Second\n\nOutcome:\nMore.\n\nEntry:\n- P1 done.\n\n"
                "Exit:\n- Nothing.\n\nExit authority:\nT-THREE\n\nStatus:\nACTIVE\n\n---\n\n# Gates",
            )
        , encoding="utf-8")
        self.project = compiler.load(self.root)
        self.assertIn("multiple-active-phases", self.codes())


RECONSTRUCTED_ADR = """# ADR-002: Store records in PostgreSQL
- Date: 2026-09-23
- Status: PROPOSED
- Authority: none
- Origin: RECONSTRUCTED
- Evidence: db/schema.sql, docs/adr/0003-postgres.md
- Supersedes: none
- Affects: T-TWO
- Context: The code already depends on it.
- Decision: Records live in PostgreSQL.
"""


class TestReconstructedDecisions(FixtureCase):
    """A baseline ADR records a decision the chronicle never observed being
    made (ADR-037). It must say where it came from, and it gains authority only
    when a person confirms it."""

    def add(self, text: str = RECONSTRUCTED_ADR) -> None:
        (self.root / layout.AUTHORITY_DIR / "ADR" / "ADR-002.md").write_text(text, encoding="utf-8")
        self.project = compiler.load(self.root)

    def test_an_adr_without_origin_is_contemporaneous(self) -> None:
        decision = self.project.decisions[0]
        self.assertEqual(decision.origin, "CONTEMPORANEOUS")
        self.assertIsNone(decision.evidence)
        self.assertEqual(validate.check(self.project), [])

    def test_a_proposed_reconstruction_with_evidence_validates(self) -> None:
        self.add()
        decision = self.project.decisions[1]
        self.assertEqual(decision.origin, "RECONSTRUCTED")
        self.assertEqual(decision.evidence, "db/schema.sql, docs/adr/0003-postgres.md")
        self.assertEqual(validate.check(self.project), [])

    def test_a_reconstruction_without_evidence_is_an_error(self) -> None:
        for evidence in ("- Evidence: \n", "- Evidence: none\n", ""):
            with self.subTest(evidence=evidence):
                self.add(RECONSTRUCTED_ADR.replace(
                    "- Evidence: db/schema.sql, docs/adr/0003-postgres.md\n", evidence
                ))
                found = validate.errors(validate.check(self.project))
                self.assertEqual([f.code for f in found], ["reconstruction-without-evidence"])
                self.assertIn("ADR-002", found[0].where)

    def test_an_accepted_reconstruction_needs_a_named_authority(self) -> None:
        for authority in ("- Authority: none\n", "- Authority: \n", ""):
            with self.subTest(authority=authority):
                self.add(
                    RECONSTRUCTED_ADR.replace("- Status: PROPOSED", "- Status: ACCEPTED")
                    .replace("- Authority: none\n", authority)
                )
                self.assertIn("unconfirmed-reconstruction", self.codes())

    def test_a_confirmed_reconstruction_validates(self) -> None:
        self.add(
            RECONSTRUCTED_ADR.replace("- Status: PROPOSED", "- Status: ACCEPTED")
            .replace("- Authority: none", "- Authority: Ada Lovelace (product owner)")
        )
        self.assertEqual(validate.check(self.project), [])

    def test_an_unknown_origin_is_an_error(self) -> None:
        self.add(RECONSTRUCTED_ADR.replace("Origin: RECONSTRUCTED", "Origin: GUESSED"))
        self.assertIn("invalid-origin", self.codes())

    def test_compiled_state_carries_origin_and_evidence(self) -> None:
        self.add()
        decisions = {d["id"]: d for d in compiler.as_json(self.project)["decisions"]}
        self.assertEqual(decisions["ADR-001"]["origin"], "CONTEMPORANEOUS")
        self.assertIsNone(decisions["ADR-001"]["evidence"])
        self.assertEqual(decisions["ADR-002"]["origin"], "RECONSTRUCTED")
        self.assertEqual(
            decisions["ADR-002"]["evidence"], "db/schema.sql, docs/adr/0003-postgres.md"
        )

    def test_a_context_packet_says_which_decisions_were_reconstructed(self) -> None:
        self.add()
        self.rewrite(
            "TASKS.md",
            "- AC: AC-T-TWO\n- Evidence: —\n- Governed by: ADR-001",
            "- AC: AC-T-TWO\n- Evidence: —\n- Governed by: ADR-001, ADR-002",
        )
        packet = analytics.context(self.project, "T-TWO")
        origins = {d["id"]: d["origin"] for d in packet["decisions"]}
        self.assertEqual(origins, {"ADR-001": "CONTEMPORANEOUS", "ADR-002": "RECONSTRUCTED"})

    def test_the_dashboard_labels_reconstructed_decisions(self) -> None:
        self.add()
        html = dashboard.render(
            self.project, analytics.report(self.project), compiler.as_json(self.project)
        )
        section = html.split('id="panel-decisions"', 1)[1].split('<section class="panel"', 1)[0]
        rows = {
            re.search(r"<code>(ADR-\d+)</code>", row).group(1): row
            for row in section.split('<details class="adr"')[1:]
        }
        self.assertIn("RECONSTRUCTED", rows["ADR-002"])
        self.assertIn("db/schema.sql", rows["ADR-002"])
        self.assertNotIn("RECONSTRUCTED", rows["ADR-001"])


class TestAnalytics(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = analytics.report(self.project)

    def test_ready_and_blocked(self) -> None:
        self.assertEqual(self.report.ready, ["T-TWO"])
        self.assertEqual(self.report.blocked, ["T-THREE"])

    def test_work_in_flight_is_not_also_reported_as_ready(self) -> None:
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        report = analytics.report(self.project)
        self.assertEqual(report.wip, ["T-TWO"])
        self.assertEqual(report.ready, [])

    def test_obstacles_are_typed_and_name_their_blockers(self) -> None:
        dependency = [o for o in self.report.obstacles if o.type == "DEPENDENCY_BLOCKER"]
        self.assertEqual(dependency[0].subject, "T-THREE")
        self.assertEqual(dependency[0].blockers, ["T-TWO"])
        self.assertIn("GATE_BLOCKER", {o.type for o in self.report.obstacles})
        self.assertIn("PHASE_BLOCKER", {o.type for o in self.report.obstacles})

    def test_critical_path_follows_dependencies(self) -> None:
        self.assertEqual(self.report.critical_path, ["T-TWO", "T-THREE"])

    def test_phase_independent_work_still_counts_as_tasks(self) -> None:
        """0 / 0 reads as "nothing here", which is not what P-NONE means."""
        self.rewrite("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC", "- Module: M-UPKEEP\n- Validation: SYNTHETIC")
        metrics = analytics.report(self.project).metrics()
        self.assertEqual(metrics["taskCompletion"]["total"], 3)
        self.assertEqual(metrics["phaseIndependent"], 1)
        self.assertEqual(metrics["phaseCompletion"]["P1"]["total"], 2)

    def test_metrics_are_reported_separately(self) -> None:
        metrics = self.report.metrics()
        self.assertEqual(metrics["taskCompletion"]["done"], 1)
        self.assertEqual(metrics["taskCompletion"]["total"], 3)
        self.assertEqual(metrics["gateReadiness"], {"done": 0, "total": 1, "fraction": 0.0})
        self.assertEqual(metrics["acceptanceCompletion"]["done"], 1)
        self.assertEqual(metrics["phaseCompletion"]["P1"]["total"], 3)

    def test_schedule_splits_known_from_unknown(self) -> None:
        self.assertEqual([e["id"] for e in self.report.scheduled], ["T-THREE"])
        self.assertEqual(self.report.unscheduled, ["T-TWO"])

    def test_explain_is_deterministic_and_complete(self) -> None:
        first = analytics.explain(self.project, "T-THREE")
        second = analytics.explain(self.project, "T-THREE")
        self.assertEqual(first, second)
        self.assertEqual(first["blockers"][0]["type"], "DEPENDENCY_BLOCKER")
        self.assertEqual(first["acceptance"][0]["id"], "AC-T-THREE-01")

    def test_context_packet_carries_the_contract(self) -> None:
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["role"], "builder")
        self.assertEqual(packet["acceptance"][0]["id"], "AC-T-TWO-01")
        self.assertEqual(packet["decisions"][0]["id"], "ADR-001")
        self.assertNotIn("findingClasses", packet)

    def test_a_packet_omits_narrative_about_other_work(self) -> None:
        """A cold agent cannot tell which of two contradicting statements to believe."""
        (self.root / layout.AUTHORITY_DIR / "INTENT.md").write_text("# Intent\n\nTask: T-ONE, in flight.\n", encoding="utf-8")
        (self.root / layout.AUTHORITY_DIR / "HANDOFF.md").write_text("# Handoff\n\nT-ONE is half built.\n", encoding="utf-8")
        project = compiler.load(self.root)
        about_other = analytics.context(project, "T-TWO")
        self.assertIsNone(about_other["intent"])
        self.assertIsNone(about_other["handoff"])
        about_this = analytics.context(project, "T-ONE")
        self.assertIn("T-ONE", about_this["intent"])
        self.assertIn("T-ONE", about_this["handoff"])

    def test_a_packet_says_which_task_it_is_for(self) -> None:
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["packetFor"], "T-TWO")
        self.assertFalse(packet["task"]["closed"])
        self.assertNotIn("note", packet)

    def test_a_packet_for_closed_work_says_so(self) -> None:
        packet = analytics.context(self.project, "T-ONE")
        self.assertTrue(packet["task"]["closed"])
        self.assertIn("already DONE", packet["note"])

    def test_a_packet_states_dependency_readiness_and_blockers(self) -> None:
        packet = analytics.context(self.project, "T-THREE")
        self.assertEqual(
            packet["task"]["dependencies"], [{"id": "T-TWO", "done": False, "status": "TODO"}]
        )
        self.assertEqual(packet["blockers"][0]["type"], "DEPENDENCY_BLOCKER")

    def test_reviewer_packet_adds_finding_classes(self) -> None:
        packet = analytics.context(self.project, "T-TWO", role="reviewer")
        self.assertIn("ACCEPTANCE_FAILURE", packet["findingClasses"]["blocking"])
        self.assertIn("STYLE", packet["findingClasses"]["informative"])

    def test_unknown_task_raises(self) -> None:
        with self.assertRaises(KeyError):
            analytics.explain(self.project, "T-NOPE")


class TestScopedContext(FixtureCase):
    """`context` is a scoped projection of compiled state (ADR-050)."""

    def test_orientation_reports_the_compiled_position(self) -> None:
        report = analytics.report(self.project)
        packet = analytics.context(self.project, None)
        self.assertIsNone(packet["packetFor"])
        self.assertEqual(packet["project"], self.project.name)
        self.assertEqual(packet["phase"]["id"], "P1")
        self.assertEqual(packet["phase"]["authority"], "PHASES.md#P1")
        self.assertEqual(packet["inFlight"], [])
        self.assertEqual([t["id"] for t in packet["ready"]], ["T-TWO"])
        self.assertEqual(packet["blocked"]["count"], 1)
        self.assertEqual([t["id"] for t in packet["blocked"]["first"]], ["T-THREE"])
        self.assertEqual(packet["ready"][0]["authority"], "TASKS.md#T-TWO")
        self.assertEqual(packet["criticalPath"], report.critical_path)
        self.assertEqual(packet["mainBlocker"], report.main_blocker)
        self.assertEqual(packet["authority"]["root"], f"{layout.AUTHORITY_DIR}/")
        self.assertNotIn("role", packet)

    def test_orientation_reports_work_in_flight_and_invents_none(self) -> None:
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        report = analytics.report(self.project)
        packet = analytics.context(self.project, None)
        self.assertEqual([t["id"] for t in packet["inFlight"]], ["T-TWO"])
        self.assertEqual(packet["ready"], [])
        named = {t["id"] for key in ("inFlight", "operationsInFlight", "ready")
                 for t in packet[key]} | {t["id"] for t in packet["blocked"]["first"]} \
            | set(packet["criticalPath"])
        self.assertLessEqual(named, {*report.wip, *report.ready, *report.blocked, *report.critical_path})

    def test_a_task_packet_names_its_relationships_and_records(self) -> None:
        self.rewrite("TASKS.md", "- Governed by: ADR-001\n\n## T-THREE",
                     "- Governed by: ADR-001\n- Files: src/build.py\n\n## T-THREE")
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["task"]["dependencies"], [{"id": "T-ONE", "done": True, "status": "DONE"}])
        self.assertEqual(packet["acceptance"][0]["id"], "AC-T-TWO-01")
        self.assertEqual([d["id"] for d in packet["decisions"]], ["ADR-001"])
        self.assertEqual(packet["task"]["implementation"]["files"], ["src/build.py"])
        self.assertEqual(packet["authority"]["read"], [
            "TASKS.md#T-TWO", "ACCEPTANCE.md#AC-T-TWO", "MODULES.md#M-FOUNDATION",
            "PHASES.md#P1", "ADR/ADR-001.md", "HANDOFF.md",
        ])
        for pointer in packet["authority"]["read"]:
            with self.subTest(pointer=pointer):
                self.assertTrue((self.root / layout.AUTHORITY_DIR / pointer.split("#")[0]).is_file())
        self.assertEqual(packet["problems"], [])

    def test_broken_references_are_reported_not_dropped(self) -> None:
        self.rewrite(
            "TASKS.md",
            "- Dependencies: T-ONE\n- Owner: unassigned\n- AC: AC-T-TWO\n- Evidence: —\n- Governed by: ADR-001",
            "- Dependencies: T-ONE, T-GHOST\n- Owner: unassigned\n- AC: AC-T-NOPE\n- Evidence: —\n"
            "- Governed by: ADR-001, ADR-099",
        )
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO\n- Module: M-FOUNDATION",
                     "## T-TWO: Build on it\n- Status: TODO\n- Module: M-GHOST")
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["task"]["dependencies"][1],
                         {"id": "T-GHOST", "done": False, "status": "MISSING"})
        self.assertEqual(packet["problems"], [
            "dependency T-GHOST is not a known task",
            "decision ADR-099 has no record",
            "module M-GHOST is not in MODULES.md",
            "contract AC-T-NOPE is not in ACCEPTANCE.md",
        ])
        self.assertEqual(packet["acceptance"], [])

    def claim_t_two(self) -> None:
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        self.rewrite("TASKS.md", "- Dependencies: T-ONE\n- Owner: unassigned",
                     "- Dependencies: T-ONE\n- Owner: codex/primary\n- Claimed: 2026-09-20")

    def test_an_active_claim_is_visible_in_task_context(self) -> None:
        self.claim_t_two()
        claim = analytics.context(self.project, "T-TWO")["task"]["claim"]
        self.assertEqual(claim, {"active": True, "holder": "codex/primary", "claimed": "2026-09-20"})

    def test_an_unclaimed_task_says_so(self) -> None:
        claim = analytics.context(self.project, "T-THREE")["task"]["claim"]
        self.assertEqual(claim, {"active": False, "holder": "unassigned", "claimed": None})
        # A finished task keeps its recorded holder but is no longer claimed.
        self.assertFalse(analytics.context(self.project, "T-ONE")["task"]["claim"]["active"])

    def test_a_claimed_packet_is_deterministic_and_read_only(self) -> None:
        self.claim_t_two()
        home = self.root / layout.HOME_DIR
        before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
        first = self.run_cli("context", "T-TWO")
        self.assertEqual(first, self.run_cli("context", "T-TWO"))
        self.assertTrue(json.loads(first[1])["task"]["claim"]["active"])
        self.assertEqual(before, {p: p.read_bytes() for p in home.rglob("*") if p.is_file()})

    def test_context_is_deterministic_and_read_only(self) -> None:
        home = self.root / layout.HOME_DIR
        before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
        for args in (("context",), ("context", "T-TWO"), ("context", "T-THREE", "--role", "reviewer")):
            with self.subTest(args=args):
                first = self.run_cli(*args)
                self.assertEqual(first[0], 0)
                self.assertEqual(first, self.run_cli(*args))
                json.loads(first[1])
        self.assertEqual(before, {p: p.read_bytes() for p in home.rglob("*") if p.is_file()})

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["-C", str(self.root), *argv])
        return code, buffer.getvalue()


class TestHierarchy(FixtureCase):
    """Thesis -> phase -> module -> task (ADR-035, ADR-036)."""

    def test_typed_objects_with_provenance_and_phase_only_through_the_module(self) -> None:
        self.assertEqual(self.project.thesis.statement, "A building people can live in.")
        self.assertEqual(self.project.thesis.reference, "docs/THESIS.md")
        module = self.project.module("M-FOUNDATION")
        self.assertEqual((module.name, module.phase), ("Foundation and walls", "P1"))
        self.assertEqual((module.source.file, module.source.anchor), ("MODULES.md", "M-FOUNDATION"))
        task = self.project.task("T-TWO")
        self.assertEqual((task.module, task.phase, task.legacy_phase), ("M-FOUNDATION", "P1", None))
        # Moving the module moves its tasks; the task names no phase itself.
        self.rewrite("MODULES.md", "- Phase: P1", "- Phase: P-NONE")
        self.assertEqual(self.project.task("T-TWO").phase, "P-NONE")

    def test_structural_errors_refuse_a_normal_compile(self) -> None:
        cases = {
            "duplicate-module": ("MODULES.md", "## M-UPKEEP — Upkeep", "## M-FOUNDATION — Upkeep"),
            "unknown-module-phase": ("MODULES.md", "- Phase: P-NONE", "- Phase: P7"),
            "unknown-module": ("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC",
                               "- Module: M-NOWHERE\n- Validation: SYNTHETIC"),
            "missing-thesis": ("THESIS.md", "- Statement: A building people can live in.", ""),
            "duplicate-phase-authority": ("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC",
                                          "- Module: M-FOUNDATION\n- Phase: P1\n- Validation: SYNTHETIC"),
        }
        for code, (name, old, new) in cases.items():
            with self.subTest(code=code):
                self.setUp()
                self.rewrite(name, old, new)
                errors = [f.code for f in validate.errors(validate.check(self.project))]
                self.assertIn(code, errors)
                buffer = io.StringIO()
                with redirect_stdout(buffer), redirect_stderr(buffer):
                    self.assertEqual(cli.main(["-C", str(self.root), "compile"]), 1)
                self.assertFalse((self.root / layout.COMPILED_DIR / "project.json").exists())

    def test_a_legacy_task_stays_readable_with_a_conversion_warning(self) -> None:
        self.rewrite("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC",
                     "- Phase: P1\n- Validation: SYNTHETIC")
        task = self.project.task("T-ONE")
        self.assertEqual((task.module, task.phase, task.legacy_phase), (None, "P1", "P1"))
        findings = validate.check(self.project)
        self.assertEqual(validate.errors(findings), [])
        self.assertIn("legacy-task-phase", [f.code for f in findings])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.root), "compile"]), 0)

    def test_a_fully_legacy_chronicle_without_a_thesis_still_compiles(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "THESIS.md").unlink()
        (self.root / layout.AUTHORITY_DIR / "MODULES.md").unlink()
        path = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        path.write_text(path.read_text(encoding="utf-8").replace("- Module: M-FOUNDATION", "- Phase: P1"), encoding="utf-8")
        project = compiler.load(self.root)
        findings = validate.check(project)
        self.assertEqual(validate.errors(findings), [])
        self.assertIn("missing-thesis", [f.code for f in findings if f.severity == "warning"])

    def test_every_output_exposes_the_lineage_deterministically(self) -> None:
        data = compiler.as_json(self.project)
        self.assertEqual(data["thesis"]["statement"], "A building people can live in.")
        self.assertEqual(
            [(m["id"], m["phase"], m["tasks"]) for m in data["modules"]],
            [("M-FOUNDATION", "P1", ["T-ONE", "T-TWO", "T-THREE"]), ("M-UPKEEP", "P-NONE", [])],
        )
        self.assertEqual({t["id"]: t["module"] for t in data["tasks"]}["T-TWO"], "M-FOUNDATION")
        report = analytics.report(self.project)
        state = views.state(self.project, report)
        self.assertIn("- Product thesis: A building people can live in.", state)
        self.assertIn("  - M-FOUNDATION Foundation and walls: 3 tasks", state)
        self.assertIn("- P-NONE Phase-independent\n  - M-UPKEEP Upkeep: 0 tasks", state)
        self.assertIn("- T-TWO [TODO] Build on it\n  - module: M-FOUNDATION\n  - phase: P1",
                      views.task_graph(self.project, report))
        expected = {"thesis": "A building people can live in.", "phase": "P1",
                    "module": {"id": "M-FOUNDATION", "name": "Foundation and walls",
                               "outcome": "The structure stands."}}
        self.assertEqual(analytics.explain(self.project, "T-TWO")["lineage"], expected)
        # A task packet points to the thesis instead of repeating it (ADR-059).
        pointed = {"thesisRef": "THESIS.md", **{k: v for k, v in expected.items() if k != "thesis"}}
        self.assertEqual(analytics.context(self.project, "T-TWO")["lineage"], pointed)
        self.assertEqual(json.dumps(data, sort_keys=True),
                         json.dumps(compiler.as_json(compiler.load(self.root)), sort_keys=True))


class TestCompile(FixtureCase):
    def test_a_phase_awaiting_exit_is_still_the_current_phase(self) -> None:
        self.rewrite("PHASES.md", "Status:\nACTIVE", "Status:\nEXIT_PENDING")
        self.assertEqual(self.project.current_phase, "P1")

    def test_shape_and_provenance(self) -> None:
        compiled = compiler.as_json(self.project)
        for key in (
            "project", "phases", "tasks", "acceptance", "invariants", "gates",
            "milestones", "obstacles", "criticalPath", "schedule", "handoff", "sources",
        ):
            self.assertIn(key, compiled)
        self.assertEqual(compiled["project"]["currentPhase"], "P1")
        for task in compiled["tasks"]:
            self.assertEqual(task["source"]["file"], "TASKS.md")
            self.assertTrue(task["source"]["anchor"])

    def test_invariants_are_separate_from_contracts(self) -> None:
        compiled = compiler.as_json(self.project)
        self.assertNotIn("AC-GLOBAL-CARE", compiled["acceptance"])
        self.assertEqual(compiled["invariants"][0]["appliesTo"], ["T-ONE"])

    def test_milestone_reached_is_computed(self) -> None:
        compiled = compiler.as_json(self.project)
        self.assertTrue(compiled["milestones"][0]["reached"])

    def test_compiling_twice_is_byte_identical(self) -> None:
        first = compiler.write(self.root, self.project).read_bytes()
        second = compiler.write(self.root, compiler.load(self.root)).read_bytes()
        self.assertEqual(first, second)

    def test_deleting_compiled_state_loses_nothing(self) -> None:
        before = compiler.write(self.root, self.project).read_bytes()
        shutil.rmtree(self.root / layout.COMPILED_DIR)
        after = compiler.write(self.root, compiler.load(self.root)).read_bytes()
        self.assertEqual(before, after)

    def test_compiler_writes_nothing_outside_the_compiled_directory(self) -> None:
        """Authored records are never touched. The single generated file the
        chronicle holds is INDEX.md (ADR-047), and it is never read back."""
        authority = self.root / layout.AUTHORITY_DIR
        before = {p: p.read_bytes() for p in authority.rglob("*") if p.is_file()}
        compiler.write(self.root, self.project)
        after = {p: p.read_bytes() for p in authority.rglob("*") if p.is_file()}
        index_path = authority / "INDEX.md"
        self.assertEqual(set(after) - set(before), {index_path})
        after.pop(index_path)
        self.assertEqual(before, after)

    def test_locate_walks_up_from_a_subdirectory(self) -> None:
        nested = self.root / "src" / "deep"
        nested.mkdir(parents=True)
        self.assertEqual(compiler.locate(nested), self.root.resolve())

    def test_locate_fails_outside_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(compiler.LayoutError):
                compiler.locate(Path(empty))


class TestRenderers(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = analytics.report(self.project)

    def test_every_view_is_produced(self) -> None:
        views = mermaid.render_all(self.project, self.report)
        self.assertEqual(
            set(views),
            {"task-graph.mmd", "critical-path.mmd", "phases.mmd", "gates.mmd",
             "timeline.mmd", "gantt.mmd"},
        )
        for name, text in views.items():
            self.assertTrue(text.startswith(("flowchart", "gantt", "---")), name)

    def test_task_graph_contains_every_task_and_edge(self) -> None:
        graph = mermaid.task_graph(self.project, self.report)
        for task in self.project.tasks:
            self.assertIn(task.id.replace("-", "_"), graph)
        self.assertIn("T_ONE --> T_TWO", graph)

    def test_a_space_in_a_gate_name_never_reaches_a_node_identifier(self) -> None:
        """Gates are identified by their heading, so `Gate A` arrives with a
        space. Mermaid ends an identifier at the first space, which breaks the
        whole diagram rather than only that node."""
        gates = mermaid.gate_graph(self.project)
        self.assertIn('Gate_A{"Gate A', gates)
        for line in gates.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("flowchart", "classDef")):
                continue
            if stripped.startswith("class "):
                names = stripped[len("class "):].rsplit(" ", 1)[0]
                identifiers = names.split(",")
            else:
                identifiers = re.split(r"[{\[(]|-->", stripped, maxsplit=1)[:1]
            for identifier in identifiers:
                self.assertRegex(identifier.strip(), r"^[0-9A-Za-z_]+$")

    def test_dependency_timeline_claims_no_dates(self) -> None:
        timeline = mermaid.dependency_timeline(self.project, self.report)
        self.assertIn("not a calendar", timeline)
        self.assertNotIn("2026-", timeline)
        self.assertIn("section Step 1", timeline)

    def test_calendar_gantt_only_dates_scheduled_work(self) -> None:
        gantt = mermaid.calendar_gantt(self.project, self.report)
        self.assertIn("T-THREE : 2026-10-01, 3d", gantt)
        self.assertNotIn("T-TWO :", gantt)
        self.assertIn("1 unscheduled task not shown", gantt)

    def test_unscheduled_work_never_gets_a_row_on_the_calendar(self) -> None:
        """A dateless Mermaid entry inherits the previous one's end date."""
        gantt = mermaid.calendar_gantt(self.project, self.report)
        rows = [line for line in gantt.splitlines() if " : " in line]
        self.assertEqual(rows, ["    T-THREE : 2026-10-01, 3d"])
        self.assertNotIn("milestone", gantt)

    def test_with_nothing_scheduled_no_date_axis_is_drawn_at_all(self) -> None:
        self.rewrite("TASKS.md", "- Schedule: start=2026-10-01 estimate=3d\n", "")
        report = analytics.report(self.project)
        gantt = mermaid.calendar_gantt(self.project, report)
        self.assertTrue(gantt.startswith("flowchart"))
        self.assertNotIn("dateFormat", gantt)
        self.assertNotIn("milestone", gantt)
        self.assertIn("No task carries schedule metadata", gantt)

    def test_rendering_is_deterministic(self) -> None:
        first = mermaid.render_all(self.project, self.report)
        second = mermaid.render_all(compiler.load(self.root), self.report)
        self.assertEqual(first, second)


class TestDashboardHierarchy(FixtureCase):
    """The overview shows thesis -> phase -> module -> task (ADR-035)."""

    def render(self) -> str:
        return dashboard.render(self.project, analytics.report(self.project), compiler.as_json(self.project))

    def section(self, html: str) -> str:
        return html.split("<h2>Product hierarchy</h2>", 1)[1].split("</section>", 1)[0]

    def test_the_hierarchy_is_laid_out_in_order_from_compiled_data(self) -> None:
        self.rewrite("TASKS.md", "- Module: M-FOUNDATION\n- Validation: UNTESTED\n- Dependencies: T-TWO",
                     "- Module: M-UPKEEP\n- Validation: UNTESTED\n- Dependencies: T-TWO")
        html = self.render()
        part = self.section(html)
        order = [part.index(marker) for marker in (
            "A building people can live in.", 'data-phase="P1"', "M-FOUNDATION",
            'data-task="T-ONE"', 'data-task="T-TWO"', 'data-phase="P-NONE"', "M-UPKEEP", 'data-task="T-THREE"',
        )]
        self.assertEqual(order, sorted(order))
        self.assertIn("docs/THESIS.md", part)
        # Relationships are rendered on the server; the script never rebuilds them.
        script = html.split('<script type="application/json" id="project-data">', 1)[1]
        self.assertNotIn("DATA.modules", script)

    def test_legacy_tasks_are_shown_without_an_invented_module(self) -> None:
        self.rewrite("TASKS.md", "- Module: M-FOUNDATION\n- Validation: SYNTHETIC", "- Phase: P1\n- Validation: SYNTHETIC")
        part = self.section(self.render())
        legacy = part.split("Legacy tasks with no module", 1)[1]
        self.assertIn('data-task="T-ONE"', legacy)
        self.assertNotIn('data-task="T-ONE"', part.split("Legacy tasks with no module", 1)[0])

    def test_authored_text_is_escaped_and_data_round_trips(self) -> None:
        hostile = '<script>alert("x")</script> & </script>'
        self.rewrite("THESIS.md", "A building people can live in.", hostile)
        self.rewrite("MODULES.md", "## M-FOUNDATION — Foundation and walls", f"## M-FOUNDATION — {hostile}")
        self.rewrite("TASKS.md", "## T-TWO: Build on it", f"## T-TWO: {hostile}")
        html = self.render()
        part = self.section(html)
        self.assertNotIn("<script>alert", part)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp;", part)
        start = html.index('id="project-data">') + len('id="project-data">')
        data = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(data["thesis"]["statement"], hostile)
        self.assertEqual(data["modules"][0]["name"], hostile)
        self.assertEqual({t["id"]: t["title"] for t in data["tasks"]}["T-TWO"], hostile)

    def test_tasks_still_open_and_the_page_stays_offline_and_read_only(self) -> None:
        html = self.render()
        part = self.section(html)
        self.assertIn('href="#task-record-T-TWO" class="tasklink" data-task="T-TWO">', part)
        self.assertIn("task.module ? `<span class=\"pill\">${esc(task.module)}</span>`", html)
        for forbidden in ("fetch(", "<form", "<textarea", "contenteditable", "http://", "XMLHttpRequest"):
            self.assertNotIn(forbidden, part)
        self.assertNotIn("fetch(", html)


class TestDashboard(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = analytics.report(self.project)
        self.compiled = compiler.as_json(self.project)
        self.html = dashboard.render(self.project, self.report, self.compiled)

    def test_is_one_self_contained_file(self) -> None:
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn('<script type="application/json" id="project-data">', self.html)
        self.assertNotIn("fetch(", self.html)

    def test_embedded_data_round_trips(self) -> None:
        start = self.html.index('id="project-data">') + len('id="project-data">')
        end = self.html.index("</script>", start)
        self.assertEqual(json.loads(self.html[start:end])["project"]["name"], self.root.name)

    def test_shows_every_required_section(self) -> None:
        for heading in (
            "Phases", "Gates", "Work in progress", "Obstacles",
            "Critical path", "Views", "Schedule", "Validation", "Decisions", "All tasks",
            "Execution progress", "Active operations", "Timeline",
        ):
            self.assertIn(f">{heading}<", self.html)

    def test_is_read_only(self) -> None:
        """Search fields filter what is already on the page. Unnamed and in no
        form, they cannot submit anything anywhere (ADR-044)."""
        for control in ("<form", "<textarea", "method=\"post\"", "contenteditable"):
            self.assertNotIn(control, self.html)
        inputs = re.findall(r"<input[^>]*>", self.html)
        self.assertTrue(inputs)
        for tag in inputs:
            self.assertIn('type="search"', tag)
            self.assertNotIn(" name=", tag)

    def test_task_drill_down_data_is_present(self) -> None:
        self.assertIn('data-task="T-THREE"', self.html)
        self.assertIn("Inherited invariants", self.html)
        self.assertIn("Source", self.html)

    def _node_map(self) -> dict[str, str]:
        start = self.html.index('id="node-map">') + len('id="node-map">')
        return json.loads(self.html[start : self.html.index("</script>", start)])

    def test_the_drawing_can_be_mapped_back_to_a_task(self) -> None:
        """The page marks an element by looking a task up in this map. Two
        tasks drawn under one identifier would highlight the wrong node, and
        nothing else in the project would notice."""
        node_map = self._node_map()
        self.assertEqual(len(node_map), len(self.project.tasks))
        for task in self.project.tasks:
            self.assertEqual(node_map[mermaid.node_id(task.id)], task.id)

    def test_the_chain_is_reachable_from_compiled_dependencies_alone(self) -> None:
        """Tracing walks `deps` and `blocks` in the embedded project. If those
        two are not exact inverses, a hover reports a different graph from the
        one `explain` reports (ADR-025)."""
        forward = {t["id"]: set(t["deps"]) for t in self.compiled["tasks"]}
        backward = {t["id"]: set(t["blocks"]) for t in self.compiled["tasks"]}
        inverted: dict[str, set[str]] = {task: set() for task in forward}
        for task, dependencies in forward.items():
            for dependency in dependencies:
                if dependency in inverted:
                    inverted[dependency].add(task)
        self.assertEqual(backward, inverted)

    def test_the_graph_reaches_the_same_dialog_as_the_tables(self) -> None:
        """A task opens from the drawing and from its table row, and both read
        the one embedded project. Only tasks are offered: a gate or a phase is
        a node too, and marking it clickable would promise a dialog that does
        not exist."""
        self.assertIn("if (id) showTask(id);", self.html)
        self.assertIn("NODE_MAP[node.dataset.id]", self.html)
        self.assertIn("node.classList.toggle('opens', Boolean(NODE_MAP", self.html)
        self.assertIn("if (panned) return;", self.html)

    def test_rendering_twice_gives_the_same_page(self) -> None:
        again = dashboard.render(self.project, self.report, compiler.as_json(self.project))
        self.assertEqual(self.html, again)

    def test_the_page_survives_mermaid_being_unreachable(self) -> None:
        """The diagram library is loaded from a CDN. Every number on the page
        is computed here, so losing the library may cost the drawing and
        nothing else."""
        self.assertNotRegex(self.html, r'<script[^>]+src=')
        self.assertIn('class="offline-graph"', self.html)
        self.assertIn("canvas.classList.add('plain')", self.html)
        self.assertIn('id="offline-note"', self.html)


class TestDashboardTabs(FixtureCase):
    """The page is six views of one compiled project (ADR-044)."""

    def setUp(self) -> None:
        super().setUp()
        self.report = analytics.report(self.project)
        self.compiled = compiler.as_json(self.project)
        self.html = dashboard.render(self.project, self.report, self.compiled)

    def panel(self, name: str) -> str:
        body = self.html.split(f'id="panel-{name}"', 1)[1]
        return re.split(r'<section class="panel"|<footer>', body, maxsplit=1)[0]

    def test_four_top_level_workspaces_in_order_with_home_first(self) -> None:
        tabs = re.findall(r'role="tab" id="tab-(\w+)"[^>]*aria-selected="(\w+)"[^>]*>([^<]+)<', self.html)
        self.assertEqual(
            [(name, label) for name, _, label in tabs],
            [("home", "Home"), ("work", "Work"), ("review", "Review"), ("audit", "Audit")],
        )
        self.assertEqual([selected for _, selected, _ in tabs], ["true"] + ["false"] * 3)
        for name, _, _ in tabs:
            self.assertIn(f'id="panel-{name}" role="tabpanel" aria-labelledby="tab-{name}"', self.html)
        self.assertIn("root.setAttribute('data-tab', known.indexOf(tab) >= 0 ? tab : 'home')", self.html)

    def test_only_the_selected_panel_is_displayed(self) -> None:
        self.assertIn("html.js .panel { display: none; }", self.html)
        for name in ("home", "work", "review", "audit"):
            self.assertIn(f'html.js[data-tab="{name}"] #panel-{name}', self.html)

    def test_sections_live_under_their_tabs(self) -> None:
        placement = {
            "work": ("Execution progress", "Phases"),
            "operations": ("Summary", "Active operations", "Failures and warnings", "Tool calls",
                           "Mini-actions", "Changes and mutations", "Retries", "Timeline"),
            "execution": ("Obstacles",),
            "graph": ("Views", "Schedule"),
            "governance": ("Gates", "Validation"),
            "decisions": ("Decisions",),
            "tasks": ("All tasks",),
        }
        for name, headings in placement.items():
            for heading in headings:
                with self.subTest(tab=name, heading=heading):
                    self.assertIn(f"<h2>{heading}</h2>", self.panel(name))

    def test_the_focus_strip_reports_compiled_state(self) -> None:
        strip = self.panel("home")
        # P1 is ACTIVE, nothing is WIP, T-TWO is ready and is the exit authority.
        self.assertIn("Foundation", strip)
        self.assertIn("No work in progress", strip)
        self.assertIn('data-task="T-TWO"', self.panel("work"))
        self.assertIn("Exit authority for P1", strip)
        self.assertIn("Gate A", strip)
        # The selection comes from analytics; the page only shows it.
        blocker = self.compiled["execution"]["mainBlocker"]
        self.assertEqual((blocker["kind"], blocker["id"]), ("GATE", "Gate A"))
        self.assertIn("Most consequential blocker", strip)

    def test_progress_and_phases_show_compiled_figures(self) -> None:
        overview = self.panel("work")
        # The figures carry cross-filter hooks, so they are read as shown text.
        shown = re.sub(r"<[^>]+>", "", overview)
        done = self.compiled["metrics"]["taskCompletion"]
        self.assertIn(f'{done["done"]} / {done["total"]}', shown)
        for phase in self.compiled["phases"]:
            self.assertIn(f'{phase["progress"]["done"]} / {phase["progress"]["total"]} execution tasks', shown)
        self.assertIn('class="phase current"', overview)

    def test_the_critical_path_is_a_chain_in_compiled_order(self) -> None:
        chain = self.panel("work").split('<ol class="chain">', 1)[1].split("</ol>", 1)[0]
        self.assertEqual(re.findall(r'data-task="([^"]+)"', chain), self.compiled["criticalPath"])

    def test_dependency_blockers_waiting_on_blocked_work_are_collapsed(self) -> None:
        execution = self.panel("execution")
        downstream = [
            o for o in self.compiled["obstacles"]
            if o["type"] == "DEPENDENCY_BLOCKER"
            and all(b in self.compiled["blocked"] for b in o["blockers"])
        ]
        if downstream:
            self.assertIn('<details class="group"><summary>Waiting on work that is itself blocked', execution)
        for obstacle in self.compiled["obstacles"]:
            self.assertIn(dashboard.esc(obstacle["detail"]), execution)

    def test_every_decision_is_listed_and_expandable(self) -> None:
        decisions = self.panel("decisions")
        for decision in self.compiled["decisions"]:
            self.assertIn(f'<summary><code>{decision["id"]}</code>', decisions)
        # One status present means no filter chips are invented.
        self.assertNotIn('data-filter="', decisions)

    def test_decision_filters_come_only_from_values_present(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "ADR" / "ADR-002.md").write_text(RECONSTRUCTED_ADR, encoding="utf-8")
        project = compiler.load(self.root)
        html = dashboard.render(project, analytics.report(project), compiler.as_json(project))
        chips = re.findall(r'data-filter="([^"]*)"', html)
        self.assertEqual(chips, ["", "ACCEPTED", "PROPOSED", "reconstructed"])

    def test_the_registry_holds_every_task_with_filters(self) -> None:
        registry = self.panel("tasks")
        rows = re.findall(r'<tr data-task="([^"]+)" tabindex="0" data-phase=', registry)
        self.assertEqual(rows, [t["id"] for t in self.compiled["tasks"]])
        self.assertIn('class="tablewrap tall"', registry)
        for control in ("task-search", "task-phase", "task-status", "task-validation"):
            self.assertIn(f'id="{control}"', registry)
        self.assertIn(".tablewrap th { position: sticky; top: 0;", self.html)

    def test_the_embedded_project_is_the_compiled_project(self) -> None:
        start = self.html.index('id="project-data">') + len('id="project-data">')
        embedded = json.loads(self.html[start:self.html.index("</script>", start)].replace("<\\/", "</"))
        self.assertEqual(embedded, self.compiled)

    def test_the_theme_is_explicit_persisted_and_falls_back_to_the_os(self) -> None:
        head = self.html.split("</head>", 1)[0]
        boot = head.index("<script>")
        self.assertLess(boot, head.index("<style>"), "theme must be set before the stylesheet paints")
        self.assertIn("localStorage.getItem('praukron-theme')", head)
        self.assertIn("matchMedia('(prefers-color-scheme: dark)')", head)
        self.assertIn('data-theme-choice="light"', self.html)
        self.assertIn('data-theme-choice="dark"', self.html)
        self.assertIn("localStorage.setItem(THEME_KEY, button.dataset.themeChoice)", self.html)
        self.assertIn(':root[data-theme="dark"]', self.html)
        self.assertIn(":root:not([data-theme])", self.html)

    def test_graph_controls_survive_pointer_capture(self) -> None:
        """Found in a real browser: the canvas captures the pointer to pan, and
        a captured click lands on the canvas. Zoom buttons never fired and a
        node never opened its dialog until both were handled."""
        self.assertIn("if (event.target.closest && event.target.closest('.zoom')) return;", self.html)
        self.assertIn("document.elementFromPoint(event.clientX, event.clientY)", self.html)

    def test_a_redraw_is_measured_unscaled(self) -> None:
        """Mermaid measured labels under the zoomed stage, so every redraw
        drew them at the current zoom, 60% on a large project."""
        draw = self.html.split("function draw() {", 1)[1].split("}", 1)[0]
        self.assertIn("view.k = 1; view.x = 0; view.y = 0;", draw)
        self.assertLess(draw.index("applyView()"), draw.index("window.mermaid.run") if "window.mermaid.run" in draw else len(draw))

    def test_the_diagram_is_drawn_only_when_its_tab_is_shown(self) -> None:
        self.assertIn("if (name === 'work') graphShown();", self.html)
        self.assertNotIn("traceNote.textContent = HINT;\n  draw();", self.html)

    def test_the_footer_still_says_it_is_derived(self) -> None:
        self.assertIn("This page is derived and read-only", self.html)
        self.assertIn("it reports authority and cannot change it", self.html)


def _domain_task(task_id, status="TODO", phase="P1", domain=None, deps=(), title=None, contract=True):
    lines = [f"## {task_id}: {title or 'Work ' + task_id}", f"- Status: {status}", f"- Module: M-{phase}"]
    if domain:
        lines.append(f"- Domain: {domain}")
    lines += [
        "- Validation: SYNTHETIC" if status == "DONE" else "- Validation: UNTESTED",
        f"- Dependencies: {', '.join(deps) or 'none'}",
    ]
    if contract:
        lines.append(f"- AC: AC-{task_id}")
    lines.append("- Evidence: done" if status == "DONE" else "- Evidence:")
    return "\n".join(lines) + "\n"


def _domain_contract(task_id, state="PASS"):
    return f"\n## AC-{task_id} — Work\n\n- `AC-{task_id}-01` — Given work, When checked, Then it holds. `TEST` · `{state}`\n"


class DomainCase(unittest.TestCase):
    """Builds small projects to pin execution/operations semantics (ADR-045)."""

    GATE = ""
    EXIT = "T-1"

    def build(self, tasks: list[str], contracts: dict[str, str], trace: str | None = None,
              gate: str | None = None, exit_authority: str | None = None, debt: str | None = None,
              adrs: dict[str, str] | None = None):
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-domain-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        authority = self.dir / layout.AUTHORITY_DIR
        (authority / "ADR").mkdir(parents=True)
        phases = (
            "# Phases\n\n## P1 — Product\n\nOutcome:\nIt ships.\n\nEntry:\n- none\n\nExit:\n- done\n\n"
            f"Exit authority:\n{exit_authority or self.EXIT}\n\nStatus:\nACTIVE\n"
        )
        if gate:
            phases += f"\n---\n\n# Gates\n\n{gate}\n"
        (authority / "PHASES.md").write_text(phases, encoding="utf-8")
        (authority / "THESIS.md").write_text("# Product thesis\n\n- Statement: It ships.\n", encoding="utf-8")
        (authority / "MODULES.md").write_text(
            "# Modules\n\n## M-P1 — Product\n- Phase: P1\n- Outcome: It ships.\n\n"
            "## M-P-NONE — Around it\n- Phase: P-NONE\n- Outcome: It keeps shipping.\n"
        , encoding="utf-8")
        (authority / "TASKS.md").write_text("# Tasks\n\n" + "\n".join(tasks), encoding="utf-8")
        (authority / "ACCEPTANCE.md").write_text(
            "# Acceptance\n" + "".join(_domain_contract(t, st) for t, st in contracts.items())
        , encoding="utf-8")
        (authority / "INTENT.md").write_text("# Intent\n\nNone.\n", encoding="utf-8")
        (authority / "HANDOFF.md").write_text("# Handoff\n\nNone.\n", encoding="utf-8")
        if trace is not None:
            (authority / "TRACE.md").write_text("# Trace\n\n" + trace, encoding="utf-8")
        if debt is not None:
            (authority / "TECH_DEBT.md").write_text("# Technical debt\n\n" + debt, encoding="utf-8")
        for adr_id, title in (adrs or {}).items():
            (authority / "ADR" / f"{adr_id}.md").write_text(
                f"# {adr_id}: {title}\n- Date: 2026-01-01\n- Status: ACCEPTED\n- Decision: {title}.\n", encoding="utf-8")
        self.project = compiler.load(self.dir)
        self.report = analytics.report(self.project)
        self.compiled = compiler.as_json(self.project)
        return self.project

    def codes(self) -> list[str]:
        return [f.code for f in validate.check(self.project)]

    def html(self) -> str:
        return dashboard.render(self.project, self.report, self.compiled)


class TestDomainResolution(DomainCase):
    def test_declared_structural_and_unresolved(self) -> None:
        gate = "## Gate A — Held\n\nHeld.\n\nBlocks: P1 exit\nVerified by: `AC-T-GATE`\nStatus: GREEN\n"
        self.build([
            _domain_task("T-1", phase="P1"),
            _domain_task("T-EXIT", phase="P-NONE"),
            _domain_task("T-GATE", phase="P-NONE"),
            _domain_task("T-O1", phase="P-NONE", domain="operations"),
            _domain_task("T-LOOSE", phase="P-NONE"),
            _domain_task("T-UP", phase="P-NONE", title="Upgrade Praukron and refresh dashboard"),
        ], {t: "NOT_RUN" for t in ("T-1", "T-EXIT", "T-GATE", "T-O1", "T-LOOSE", "T-UP")},
            gate=gate, exit_authority="T-EXIT")
        resolved = {t.id: (t.domain, t.domain_source) for t in self.project.tasks}
        self.assertEqual(resolved, {
            "T-1": ("execution", "phase"),
            "T-EXIT": ("execution", "exit-authority"),
            "T-GATE": ("execution", "gate"),
            "T-O1": ("operations", "declared"),
            "T-LOOSE": ("execution", "unresolved"),
            # A title never decides: this is unresolved, not operations.
            "T-UP": ("execution", "unresolved"),
        })
        warned = [f.where for f in validate.check(self.project) if f.code == "ambiguous-domain"]
        self.assertEqual(warned, ["TASKS.md#T-LOOSE", "TASKS.md#T-UP"])

    def test_an_invalid_domain_is_an_error(self) -> None:
        self.build([_domain_task("T-1", domain="support")], {"T-1": "NOT_RUN"})
        self.assertIn("invalid-domain", [f.code for f in validate.errors(validate.check(self.project))])
        self.assertIn(self.project.task("T-1").domain, ("execution", "operations"))

    def test_the_resolver_reads_no_titles_or_ids(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src/praukron/domain.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]
        self.assertNotIn(".title", code)
        self.assertNotIn("startswith", code)


class TestExecutionOperationsCases(DomainCase):
    def test_case_a_a_side_task_does_not_take_the_focus(self) -> None:
        self.build([
            _domain_task("T-1", "WIP"),
            _domain_task("T-2", deps=["T-1"]),
            _domain_task("T-O1", "WIP", phase="P-NONE", domain="operations"),
        ], {"T-1": "NOT_RUN", "T-2": "NOT_RUN", "T-O1": "NOT_RUN"}, exit_authority="T-2")
        self.assertEqual(self.report.in_flight, ["T-1"])
        self.assertNotIn("T-O1", self.report.critical_path)
        self.assertEqual(self.compiled["metrics"]["taskCompletion"]["total"], 2)
        self.assertEqual(self.compiled["operations"]["active"], ["T-O1"])
        self.assertIn('id="op-T-O1"', self.html())

    def test_case_b_a_blocked_operations_task_is_not_the_main_blocker(self) -> None:
        self.build([
            _domain_task("T-1", "WIP"),
            _domain_task("T-O0", phase="P-NONE", domain="operations"),
            _domain_task("T-O1", phase="P-NONE", domain="operations", deps=["T-O0"]),
        ], {"T-1": "NOT_RUN", "T-O0": "NOT_RUN", "T-O1": "NOT_RUN"})
        self.assertIn("T-O1", self.report.blocked)
        self.assertIsNone(self.report.main_blocker)

    def test_case_c_operations_blocks_execution_without_becoming_it(self) -> None:
        self.build([
            _domain_task("T-1", deps=["T-O1"]),
            _domain_task("T-O1", phase="P-NONE", domain="operations"),
        ], {"T-1": "NOT_RUN", "T-O1": "NOT_RUN"})
        self.assertEqual(self.report.critical_path, ["T-1"])
        self.assertEqual(self.report.external_blockers, {"T-1": ["T-O1"]})
        blocker = self.report.main_blocker
        self.assertEqual((blocker["id"], blocker["domain"], blocker["blocking"]), ("T-O1", "operations", "T-1"))
        self.assertEqual(self.project.task("T-O1").domain, "operations")
        page = self.html()
        overview = page.split('id="panel-audit"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn("Project operations", overview)
        self.assertIn('data-focus="op-T-O1"', overview)
        self.assertIn('id="op-T-O1"', page)

    def test_case_d_phase_counts_exclude_operations(self) -> None:
        tasks = [_domain_task(f"T-{i}", "DONE" if i <= 8 else "TODO") for i in range(1, 11)]
        tasks += [_domain_task(f"T-O{i}", "TODO", phase="P1", domain="operations") for i in range(1, 4)]
        states = {f"T-{i}": "PASS" if i <= 8 else "NOT_RUN" for i in range(1, 11)}
        states.update({f"T-O{i}": "NOT_RUN" for i in range(1, 4)})
        self.build(tasks, states, exit_authority="T-10")
        self.assertEqual(str(self.report.phase_progress["P1"]), "8 / 10")
        self.assertEqual(self.compiled["phases"][0]["progress"]["total"], 10)
        self.assertEqual(self.compiled["metrics"]["taskCompletion"], {"done": 8, "total": 10, "fraction": 0.8})
        self.assertEqual(self.codes().count("operations-in-phase"), 3)
        phase_blocker = [o for o in self.report.obstacles if o.type == "PHASE_BLOCKER"][0]
        self.assertEqual(phase_blocker.blockers, ["T-9", "T-10"])

    def test_case_e_housekeeping_leaves_a_ready_gate_ready(self) -> None:
        gate = "## Gate A — Held\n\nHeld.\n\nBlocks: P1 exit\nVerified by: `AC-T-1`\nStatus: GREEN\n"
        self.build([
            _domain_task("T-1", "DONE"),
            _domain_task("T-O5", phase="P-NONE", domain="operations", title="Clean up the README"),
        ], {"T-1": "PASS", "T-O5": "NOT_RUN"}, gate=gate)
        gate_view = self.report.next_gate
        self.assertTrue(gate_view["gates"][0]["ready"])
        self.assertEqual(gate_view["externalBlockers"], [])
        self.assertTrue(gate_view["ready"])
        self.assertIsNone(self.report.main_blocker)

    def test_case_f_operations_that_verify_a_gate_block_it(self) -> None:
        gate = "## Gate A — Runtime\n\nRuntime migrated.\n\nBlocks: P1 exit\nVerified by: `AC-T-O6`\nStatus: GREEN\n"
        self.build([
            _domain_task("T-1", "DONE"),
            _domain_task("T-O6", phase="P-NONE", domain="operations", title="Runtime migration"),
        ], {"T-1": "PASS", "T-O6": "NOT_RUN"}, gate=gate)
        gate_view = self.report.next_gate
        self.assertFalse(gate_view["gates"][0]["ready"])
        self.assertFalse(gate_view["ready"])
        self.assertEqual(gate_view["externalBlockers"], [{"id": "T-O6", "domain": "operations", "via": "Gate A"}])
        self.assertEqual(str(self.report.phase_progress["P1"]), "1 / 1")
        self.assertIn('data-focus="op-T-O6"', self.html())

    def test_case_f_through_the_exit_authority(self) -> None:
        self.build([
            _domain_task("T-1", deps=["T-O6"]),
            _domain_task("T-O6", phase="P-NONE", domain="operations"),
        ], {"T-1": "NOT_RUN", "T-O6": "NOT_RUN"})
        self.assertEqual(self.report.next_gate["externalBlockers"][0]["id"], "T-O6")
        self.assertEqual(self.report.main_blocker["reason"], "phase exit")

    TRACE_G = (
        "## EV-10: Generate client plan schema\n- Type: action\n- Time: 2026-09-23T14:20:00Z\n"
        "- Task: T-O2\n- Outcome: success\n\n"
        "## EV-11: Run schema generator\n- Type: tool-call\n- Time: 2026-09-23T14:22:00Z\n- Task: T-O2\n"
        "- Tool: shell\n- Outcome: failure\n- Error: generated schema is empty\n- Parent: EV-10\n"
        "- Affects: T-8\n- Artifact: schema.json\n"
    )

    def test_case_g_a_failed_tool_call_explains_an_execution_failure(self) -> None:
        self.build([
            _domain_task("T-8", "WIP"),
            _domain_task("T-O2", "WIP", phase="P-NONE", domain="operations"),
        ], {"T-8": "FAIL", "T-O2": "NOT_RUN"}, trace=self.TRACE_G, exit_authority="T-8")
        self.assertEqual(self.project.task("T-8").domain, "execution")
        self.assertIsNone(self.project.task("EV-11"))
        self.assertNotIn("EV-11", self.report.critical_path)
        failure = self.report.execution_failures[0]
        self.assertEqual(failure["task"], "T-8")
        self.assertEqual(failure["failedEvents"], ["EV-11"])
        self.assertEqual(self.compiled["operations"]["unresolvedFailures"], ["EV-11"])
        page = self.html()
        execution_panel = page.split('id="panel-execution"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('data-focus="event-EV-11"', execution_panel)
        operations_panel = page.split('id="panel-operations"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('id="event-EV-11"', operations_panel)
        self.assertIn("generated schema is empty", operations_panel)
        self.assertNotIn("EV-11", mermaid.task_graph(self.project, self.report))
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_case_h_a_mini_action_mutation_stays_in_the_trace(self) -> None:
        trace = ("## EV-4: Edit provider config\n- Type: mutation\n- Time: 2026-09-23T09:00:00Z\n"
                 "- Target: config/provider.json\n- Outcome: success\n- Affects: T-9\n")
        self.build([_domain_task("T-9", "WIP")], {"T-9": "FAIL"}, trace=trace, exit_authority="T-9")
        self.assertEqual(self.report.execution_failures[0]["relatedEvents"], ["EV-4"])
        self.assertEqual(str(self.report.phase_progress["P1"]), "0 / 1")
        timeline = self.html().split('id="timeline"', 1)[1]
        self.assertIn('id="event-EV-4"', timeline)
        self.assertEqual(self.compiled["operations"]["metrics"]["events"]["mutations"], 1)

    def test_the_blocker_prefers_startable_critical_path_work(self) -> None:
        # T-EXIT waits on a blocked task, a ready side task, and the ready head
        # of the critical path; the critical-path head is the obstruction.
        self.build([
            _domain_task("T-A"), _domain_task("T-B", deps=["T-A"]),
            _domain_task("T-SIDE"), _domain_task("T-HEAD"),
            _domain_task("T-N1", deps=["T-HEAD"]), _domain_task("T-N2", deps=["T-N1"]),
            _domain_task("T-N3", deps=["T-N2"]),
            _domain_task("T-EXIT", deps=["T-B", "T-SIDE", "T-HEAD"]),
        ], {t: "NOT_RUN" for t in ("T-A", "T-B", "T-SIDE", "T-HEAD", "T-N1", "T-N2", "T-N3", "T-EXIT")},
            exit_authority="T-EXIT")
        self.assertEqual(self.report.critical_path[0], "T-HEAD")
        self.assertEqual(self.report.main_blocker["id"], "T-HEAD")

    def test_case_i_selection_is_deterministic(self) -> None:
        tasks = [
            _domain_task("T-1", "WIP"), _domain_task("T-2", deps=["T-1", "T-O1"]),
            _domain_task("T-O1", "WIP", phase="P-NONE", domain="operations"),
            _domain_task("T-3", "WIP", phase="P-NONE"),
        ]
        states = {"T-1": "NOT_RUN", "T-2": "NOT_RUN", "T-O1": "NOT_RUN", "T-3": "NOT_RUN"}
        self.build(tasks, states, trace=self.TRACE_G.replace("T-O2", "T-O1").replace("T-8", "T-2"),
                   exit_authority="T-2")
        first = (json.dumps(self.compiled, sort_keys=True), self.html())
        for _ in range(3):
            project = compiler.load(self.dir)
            report = analytics.report(project)
            compiled = compiler.as_json(project)
            again = (json.dumps(compiled, sort_keys=True), dashboard.render(project, report, compiled))
            self.assertEqual(first, again)
        # Current-phase execution first, then the unphased execution task.
        self.assertEqual(self.report.in_flight, ["T-1", "T-3"])


class TestTraceValidation(DomainCase):
    def trace(self, text: str) -> list[str]:
        self.build([_domain_task("T-1", "WIP")], {"T-1": "NOT_RUN"}, trace=text)
        return self.codes()

    def test_structure_errors(self) -> None:
        codes = self.trace(
            "## EV-1: One\n- Type: tool-call\n- Outcome: success\n\n"
            "## EV-1: Again\n- Type: magic\n- Outcome: maybe\n- Parent: EV-9\n- Retry of: EV-1\n"
        )
        for code in ("duplicate-event", "invalid-event-type", "invalid-event-outcome",
                     "unknown-event-reference", "self-referencing-event"):
            self.assertIn(code, codes)

    def test_reference_warnings_are_not_errors(self) -> None:
        self.trace("## EV-1: One\n- Type: note\n- Task: T-404\n- Affects: Gate Z\n- Time: yesterday\n")
        found = validate.check(self.project)
        self.assertEqual(validate.errors(found), [])
        self.assertEqual(
            sorted(f.code for f in found),
            ["invalid-event-time", "unknown-event-affects", "unknown-event-task"],
        )

    def test_an_apparent_secret_is_flagged(self) -> None:
        codes = self.trace("## EV-1: Push\n- Type: command\n- Error: remote rejected token=ghp_abcdefghijklmnop1234\n")
        self.assertIn("possible-secret", codes)

    def test_a_successful_retry_resolves_a_failure(self) -> None:
        self.build([_domain_task("T-1", "WIP")], {"T-1": "NOT_RUN"}, trace=(
            "## EV-1: Build\n- Type: command\n- Outcome: failure\n\n"
            "## EV-2: Build again\n- Type: retry\n- Retry of: EV-1\n- Outcome: failure\n\n"
            "## EV-3: Build a third time\n- Type: retry\n- Retry of: EV-2\n- Outcome: success\n\n"
            "## EV-4: Lint\n- Type: command\n- Outcome: failure\n"
        ))
        self.assertEqual(analytics.unresolved_failures(self.project), ["EV-4"])
        self.assertEqual(self.compiled["operations"]["metrics"]["events"]["retries"], 2)

    def test_a_project_without_a_trace_has_no_events(self) -> None:
        self.build([_domain_task("T-1")], {"T-1": "NOT_RUN"})
        self.assertEqual(self.project.events, [])
        self.assertIn("No operational events are recorded", self.html())


class TestDomainsReport(DomainCase):
    def test_it_classifies_and_changes_nothing(self) -> None:
        self.build([
            _domain_task("T-1"), _domain_task("T-O1", phase="P-NONE", domain="operations"),
            _domain_task("T-2", phase="P-NONE", domain="execution"), _domain_task("T-3", phase="P-NONE"),
        ], {t: "NOT_RUN" for t in ("T-1", "T-O1", "T-2", "T-3")})
        before = {p: p.read_bytes() for p in (self.dir / layout.AUTHORITY_DIR).rglob("*") if p.is_file()}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.dir), "domains", "--json"]), 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["domains"]["explicitExecution"], ["T-2"])
        self.assertEqual(report["domains"]["explicitOperations"], ["T-O1"])
        self.assertEqual(report["domains"]["inferredExecution"], ["T-1"])
        self.assertEqual(report["domains"]["inferredOperations"], [])
        self.assertEqual(report["domains"]["ambiguous"], ["T-3"])
        self.assertFalse(report["trace"]["recorded"])
        after = {p: p.read_bytes() for p in (self.dir / layout.AUTHORITY_DIR).rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_status_names_an_operations_blocker(self) -> None:
        self.build([_domain_task("T-1", deps=["T-O1"]), _domain_task("T-O1", phase="P-NONE", domain="operations")],
                   {"T-1": "NOT_RUN", "T-O1": "NOT_RUN"})
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(["-C", str(self.dir), "status"])
        self.assertIn("Blocker   T-O1 · project operations → T-1 (phase exit)", buffer.getvalue())
        self.assertIn("operations   1 open", buffer.getvalue())


DEBT = """# Technical debt

## TD-1: Schema mirrors the UI
- Status: SCHEDULED
- Introduced by: T-ONE, ADR-001
- Areas: schema
- Debt: The schema mirrors the form.
- Reason: Kept for the migration.
- Interest: Each intake path needs mapping.
- Trigger: Before chat becomes the default intake.
- Trigger state: REACHED
- Exit condition: The schema is independent of the UI.
- Evidence: src/schema.ts
- Linked tasks: T-TWO
- Resolution:
"""


class TestTechnicalDebt(FixtureCase):
    """Debt is a liability with a lineage, not work (ADR-046)."""

    def write(self, text: str = DEBT) -> None:
        (self.root / layout.AUTHORITY_DIR / "TECH_DEBT.md").write_text(text, encoding="utf-8")
        self.project = compiler.load(self.root)

    def test_a_record_parses_with_its_lineage(self) -> None:
        self.write()
        debt = self.project.debts[0]
        self.assertEqual((debt.id, debt.status, debt.trigger_state), ("TD-1", "SCHEDULED", "REACHED"))
        self.assertEqual(debt.introduced_by, ["T-ONE", "ADR-001"])
        self.assertEqual(debt.linked_tasks, ["T-TWO"])
        self.assertEqual(debt.exit_condition, "The schema is independent of the UI.")
        self.assertEqual(validate.errors(validate.check(self.project)), [])
        self.assertNotIn("domain", debt.as_json())

    def test_no_file_means_no_debt(self) -> None:
        self.assertEqual(self.project.debts, [])

    def test_lifecycle_errors(self) -> None:
        cases = {
            "invalid-debt-status": DEBT.replace("Status: SCHEDULED", "Status: MAYBE"),
            "invalid-trigger-state": DEBT.replace("Trigger state: REACHED", "Trigger state: SOON"),
            "debt-without-description": DEBT.replace("- Debt: The schema mirrors the form.\n", ""),
            "debt-without-exit": DEBT.replace("- Exit condition: The schema is independent of the UI.\n", ""),
            "scheduled-debt-without-task": DEBT.replace("Linked tasks: T-TWO", "Linked tasks: none"),
            "closed-debt-without-resolution": DEBT.replace("Status: SCHEDULED", "Status: RESOLVED"),
            "unknown-debt-reference": DEBT.replace("T-ONE, ADR-001", "T-GHOST, ADR-404"),
            "duplicate-debt": DEBT + DEBT.split("# Technical debt\n", 1)[1],
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                self.write(text)
                self.assertIn(code, [f.code for f in validate.errors(validate.check(self.project))])

    def test_lifecycle_warnings(self) -> None:
        self.write(DEBT.replace("Status: SCHEDULED", "Status: RESOLVED")
                   .replace("- Resolution:", "- Resolution: Verified by the schema split."))
        self.assertIn("resolved-debt-open-task", self.codes())
        self.write(DEBT.replace("Status: SCHEDULED", "Status: ACCEPTED"))
        self.assertIn("debt-trigger-reached", self.codes())

    def test_finishing_a_linked_task_does_not_resolve_the_debt(self) -> None:
        self.write()
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: DONE")
        self.assertEqual(self.project.debts[0].status, "SCHEDULED")

    def test_lineage_reaches_task_context_and_compiled_state(self) -> None:
        self.write()
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual([d["id"] for d in packet["debt"]], ["TD-1"])
        # T-THREE is governed by ADR-001, which introduced the debt.
        self.assertEqual([d["id"] for d in analytics.context(self.project, "T-THREE")["debt"]], ["TD-1"])
        compiled = compiler.as_json(self.project)
        self.assertEqual(compiled["debt"][0]["linkedTasks"], ["T-TWO"])
        self.assertEqual({t["id"]: t["debt"] for t in compiled["tasks"]}["T-ONE"], ["TD-1"])
        html = dashboard.render(self.project, analytics.report(self.project), compiled)
        governance = html.split('id="panel-governance"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('id="debt-TD-1"', governance)


def _debt(debt_id, state="NOT_REACHED", status="ACCEPTED", by="T-1", linked="none"):
    return (f"## {debt_id}: Debt {debt_id}\n- Status: {status}\n- Introduced by: {by}\n"
            f"- Debt: A compromise.\n- Reason: Speed.\n- Interest: Mapping cost.\n"
            f"- Trigger: Before release.\n- Trigger state: {state}\n- Exit condition: It is gone.\n"
            f"- Linked tasks: {linked}\n\n")


class TestIndex(DomainCase):
    """INDEX.md is a compiled routing layer, never authority (ADR-047)."""

    def compile(self) -> str:
        compiler.write(self.dir, self.project)
        return (self.dir / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8")

    def markers(self, text: str) -> dict[str, str]:
        current = text.split("## Current", 1)[1].split("\n## ", 1)[0]
        return dict(re.findall(r"(?m)^- ([a-z_]+): (.*)$", current))

    def standard(self) -> None:
        self.build([
            _domain_task("T-0", "DONE"),
            _domain_task("T-1", "WIP", deps=["T-0"]),
            _domain_task("T-2", deps=["T-1", "T-O1"]),
            _domain_task("T-O1", "WIP", phase="P-NONE", domain="operations"),
            _domain_task("T-OLD", "DONE", phase="P-NONE", domain="execution", title="Ancient history"),
        ], {"T-0": "PASS", "T-1": "NOT_RUN", "T-2": "NOT_RUN", "T-O1": "NOT_RUN", "T-OLD": "PASS"},
            exit_authority="T-2",
            debt=_debt("TD-1", "REACHED", by="T-1, ADR-001") + _debt("TD-9", "NOT_REACHED", by="T-OLD"),
            adrs={"ADR-001": "Keep SQLite", "ADR-002": "Unrelated choice"})

    def test_a_it_is_deterministic_and_routes_to_what_matters_now(self) -> None:
        self.standard()
        first = self.compile()
        self.assertEqual(first, self.compile())
        m = self.markers(first)
        self.assertEqual(m["phase"], "P1")
        self.assertEqual(m["execution_task"], "T-1")
        self.assertEqual(m["next_gate"], "T-2")
        self.assertEqual(m["operations_affecting_execution"], "T-O1")
        self.assertEqual(m["debt_attention"], "TD-1")
        self.assertIn("TASKS.md#T-1", first)
        self.assertIn("ACCEPTANCE.md#AC-T-2", first)
        # Old, finished, unrelated records are not routed to.
        self.assertNotIn("T-OLD", first)
        self.assertNotIn("TD-9", first.split("## Technical debt", 1)[1].split("\n## ", 1)[0].split("\n", 2)[2])
        self.assertNotIn("ADR-002", first)
        self.assertNotIn("Generated: 20", first)

    def test_b_it_stays_compact_on_a_large_project(self) -> None:
        tasks = [_domain_task(f"T-{i}", "DONE") for i in range(1, 301)]
        tasks += [_domain_task(f"T-W{i}", "WIP", deps=[f"T-{i}"]) for i in range(1, 21)]
        tasks += [_domain_task(f"T-N{i}", deps=[f"T-W{i}"]) for i in range(1, 21)]
        states = {f"T-{i}": "PASS" for i in range(1, 301)}
        states.update({f"T-W{i}": "NOT_RUN" for i in range(1, 21)})
        states.update({f"T-N{i}": "NOT_RUN" for i in range(1, 21)})
        debt = "".join(_debt(f"TD-{i}", "REACHED" if i % 3 == 0 else "NOT_REACHED", by=f"T-{i}") for i in range(1, 121))
        adrs = {f"ADR-{i:03d}": f"Decision {i}" for i in range(1, 151)}
        self.build(tasks, states, exit_authority="T-N1", debt=debt, adrs=adrs)
        text = self.compile()
        size = index.tokens(text)
        self.assertLess(size, index.PREFERRED_TOKENS, f"{size} tokens")
        self.assertLessEqual(text.count("→ TASKS.md#T-"), 12)
        self.assertLessEqual(text.count("→ TECH_DEBT.md#"), 5)
        self.assertIn("more needing attention", text)
        self.assertNotIn("Given work, When checked", text)
        self.assertEqual([f.code for f in index.findings(self.project, self.report, self.dir)], [])

    def test_c_a_reached_trigger_is_surfaced_with_its_source(self) -> None:
        self.standard()
        text = self.compile()
        self.assertIn("- TD-1 · ACCEPTED · trigger reached — Debt TD-1 → TECH_DEBT.md#TD-1", text)
        self.assertIn("ADR-001 — Keep SQLite → ADR/ADR-001.md", text)

    def test_editing_a_task_changes_the_index_and_editing_the_index_changes_nothing(self) -> None:
        self.standard()
        first = self.compile()
        path = self.dir / layout.AUTHORITY_DIR / "INDEX.md"
        path.write_text(first.replace("- execution_task: T-1", "- execution_task: T-99"), encoding="utf-8")
        edited = compiler.load(self.dir)
        self.assertEqual(analytics.report(edited).in_flight, ["T-1"])
        self.assertEqual(
            [f.code for f in index.findings(edited, analytics.report(edited), self.dir)], ["index-stale"])
        tasks = self.dir / layout.AUTHORITY_DIR / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("## T-1: Work T-1\n- Status: WIP", "## T-1: Work T-1\n- Status: DONE"), encoding="utf-8")
        self.project = compiler.load(self.dir)
        second = self.compile()
        self.assertNotEqual(first, second)
        self.assertNotIn("- execution_task: T-99", second)

    def test_it_is_neither_the_handoff_nor_the_readme(self) -> None:
        self.standard()
        (self.dir / layout.AUTHORITY_DIR / "HANDOFF.md").write_text(
            "# Handoff\n\n## Position\n- long history\n\n## Next action\nFinish T-1 before starting T-2.\n", encoding="utf-8")
        self.project = compiler.load(self.dir)
        text = self.compile()
        self.assertIn("Finish T-1 before starting T-2.", text)
        self.assertNotIn("long history", text)
        self.assertEqual((self.dir / layout.AUTHORITY_DIR / "HANDOFF.md").read_text(encoding="utf-8").count("## Next action"), 1)
        self.assertNotIn("## Read order", text)

    def test_a_missing_index_is_reported(self) -> None:
        self.standard()
        self.assertEqual([f.code for f in index.findings(self.project, self.report, self.dir)], ["index-missing"])


class TestRetrieval(DomainCase):
    """Retrieval routes through the index to the minimum canonical context (ADR-048)."""

    GATE = "## Gate A — Held\n\nHeld.\n\nBlocks: P1 exit\nVerified by: `AC-T-1`\nStatus: RED\n"

    def standard(self) -> None:
        tasks = [
            _domain_task("T-0", "DONE"),
            _domain_task("T-1", "WIP", deps=["T-0"]),
            _domain_task("T-2", deps=["T-1", "T-O1"]),
            _domain_task("T-O1", phase="P-NONE", domain="operations"),
            _domain_task("T-EXIT", deps=["T-2"]),
        ] + [_domain_task(f"T-X{i}", "DONE", phase="P-NONE", domain="execution") for i in range(1, 41)]
        states = {"T-0": "PASS", "T-1": "NOT_RUN", "T-2": "NOT_RUN", "T-O1": "NOT_RUN", "T-EXIT": "NOT_RUN"}
        states.update({f"T-X{i}": "PASS" for i in range(1, 41)})
        text = "".join(tasks)
        text = text.replace("## T-1: Work T-1\n", "## T-1: Work T-1\n", 1)
        self.build(tasks, states, gate=self.GATE, exit_authority="T-EXIT",
                   debt=_debt("TD-1", "REACHED", by="T-2") + _debt("TD-9", by="T-X3"),
                   adrs={"ADR-001": "Relevant", "ADR-002": "Unrelated"},
                   trace="## EV-1: Build fixtures\n- Type: command\n- Task: T-O1\n- Outcome: failure\n- Affects: T-2\n\n"
                         "## EV-2: Unrelated note\n- Type: note\n- Task: T-X5\n")
        tasks_md = self.dir / layout.AUTHORITY_DIR / "TASKS.md"
        tasks_md.write_text(tasks_md.read_text(encoding="utf-8").replace(
            "## T-1: Work T-1\n- Status: WIP\n- Module: M-P1\n",
            "## T-1: Work T-1\n- Status: WIP\n- Module: M-P1\n- Governed by: ADR-001\n"), encoding="utf-8")
        self.project = compiler.load(self.dir)
        self.report = analytics.report(self.project)
        compiler.write(self.dir, self.project)

    def sources(self, query: str) -> list[str]:
        return [i.source for i in retrieve.retrieve(self.project, self.report, self.dir, query).items]

    def test_f_why_is_p1_blocked(self) -> None:
        self.standard()
        got = set(self.sources("Why is P1 blocked?"))
        for expected in ("PHASES.md#P1", "PHASES.md#Gate A", "TASKS.md#T-1", "ACCEPTANCE.md#AC-T-1",
                         "TASKS.md#T-EXIT", "ACCEPTANCE.md#AC-T-EXIT", "TASKS.md#T-2",
                         "TASKS.md#T-O1", "TECH_DEBT.md#TD-1", "TRACE.md#EV-1"):
            self.assertIn(expected, got)
        for unrelated in ("TASKS.md#T-X3", "TASKS.md#T-0", "TECH_DEBT.md#TD-9", "ADR/ADR-002.md", "TRACE.md#EV-2",
                          "JOURNAL.md", "HANDOFF.md"):
            self.assertNotIn(unrelated, got)

    def test_g_a_task_pack_holds_only_its_neighbourhood(self) -> None:
        self.standard()
        got = self.sources("T-2")
        self.assertEqual(got, [
            "TASKS.md#T-2", "ACCEPTANCE.md#AC-T-2", "TECH_DEBT.md#TD-1",
            "TASKS.md#T-1", "ACCEPTANCE.md#AC-T-1", "TASKS.md#T-O1", "ACCEPTANCE.md#AC-T-O1",
            "TRACE.md#EV-1",
        ])

    def test_implicit_references_route_through_the_index(self) -> None:
        self.standard()
        pack = retrieve.retrieve(self.project, self.report, self.dir, "why is this task blocked?")
        self.assertEqual(pack.routed[0], ("T-1", "INDEX execution_task"))
        self.assertIn("ADR/ADR-001.md", [i.source for i in pack.items])
        debt_pack = retrieve.retrieve(self.project, self.report, self.dir, "what debt needs attention?")
        self.assertEqual([i.source for i in debt_pack.items], ["TECH_DEBT.md#TD-1"])
        nothing = retrieve.retrieve(self.project, self.report, self.dir, "hello")
        self.assertEqual([i.source for i in nothing.items], ["INDEX.md"])

    def test_retrieval_writes_nothing_and_is_deterministic(self) -> None:
        self.standard()
        authority = self.dir / layout.AUTHORITY_DIR
        before = {p: p.read_bytes() for p in authority.rglob("*") if p.is_file()}
        first = retrieve.retrieve(self.project, self.report, self.dir, "Why is P1 blocked?").render()
        second = retrieve.retrieve(compiler.load(self.dir), self.report, self.dir, "Why is P1 blocked?").render()
        self.assertEqual(first, second)
        self.assertEqual(before, {p: p.read_bytes() for p in authority.rglob("*") if p.is_file()})

    def test_the_pack_is_attributed_and_much_smaller_than_the_chronicle(self) -> None:
        self.standard()
        pack = retrieve.retrieve(self.project, self.report, self.dir, "T-2")
        rendered = pack.render()
        for item in pack.items:
            self.assertIn(f"— {item.source}", rendered)
        self.assertLess(pack.loaded_bytes * 4, pack.chronicle_bytes)
        self.assertIn(f"{pack.loaded_bytes:,} of {pack.chronicle_bytes:,} bytes", rendered)

    def test_a_stale_index_is_rebuilt_in_memory_not_trusted(self) -> None:
        self.standard()
        path = self.dir / layout.AUTHORITY_DIR / "INDEX.md"
        path.write_text(path.read_text(encoding="utf-8").replace("- execution_task: T-1", "- execution_task: T-X9"), encoding="utf-8")
        pack = retrieve.retrieve(self.project, self.report, self.dir, "why is this task blocked?")
        self.assertEqual(pack.routed[0][0], "T-1")
        self.assertIn("rebuilt in memory", pack.index_note)

    def test_the_cli(self) -> None:
        self.standard()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.dir), "retrieve", "why", "is", "P1", "blocked?", "--json"]), 0)
        self.assertEqual(json.loads(buffer.getvalue())["routed"][0], {"entity": "P1", "why": "named"})

    def test_a_budget_loads_a_prefix_and_lists_the_rest_as_pointers(self) -> None:
        self.standard()
        whole = retrieve.retrieve(self.project, self.report, self.dir, "why is P1 blocked?", budget=0)
        self.assertGreater(len(whole.items), 2)
        self.assertEqual(whole.omitted, [])
        first = index.tokens(whole.items[0].text)
        pack = retrieve.retrieve(self.project, self.report, self.dir, "why is P1 blocked?", budget=first)
        # The same neighbourhood in the same order, cut after the first record.
        self.assertEqual([i.source for i in pack.items], [whole.items[0].source])
        self.assertEqual([o.source for o in pack.omitted], [i.source for i in whole.items[1:]])
        rendered = pack.render()
        self.assertIn(f"over the {first:,}-token budget", rendered)
        for o in pack.omitted:
            self.assertIn(f"`{o.follow_up()}`", rendered)
        self.assertIn(f"{len(pack.omitted)} more relevant sections were not loaded", rendered)

    def test_the_first_record_loads_even_over_the_budget(self) -> None:
        self.standard()
        pack = retrieve.retrieve(self.project, self.report, self.dir, "T-2", budget=1)
        self.assertEqual(pack.items[0].id, "T-2")
        self.assertEqual(pack.items[0].kind, "TASK")
        self.assertEqual(len(pack.items), 1)
        self.assertTrue(pack.omitted)

    def test_the_cli_budget(self) -> None:
        self.standard()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.dir), "retrieve", "T-2", "--budget", "1", "--json"]), 0)
        packed = json.loads(buffer.getvalue())
        self.assertEqual(packed["budgetTokens"], 1)
        self.assertTrue(packed["omitted"])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["-C", str(self.dir), "retrieve", "T-2", "--budget", "-1"]), 1)


FAKE_CODEGRAPH = """#!{python}
import json, os, sys
log = os.environ["FAKE_CG_LOG"]
with open(log, "a") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_CG_FAIL"):
    sys.stderr.write("index is locked\\n")
    sys.exit(2)
args = sys.argv[1:]
if args[:2] == ["status", "--json"]:
    print(json.dumps({{"initialized": True, "version": "9.9", "fileCount": 12, "nodeCount": 99,
                      "index": {{"state": "complete", "reindexRecommended": False}},
                      "pendingChanges": {{"added": 0, "modified": 0, "removed": 0}}}}))
elif args[:1] == ["explore"]:
    print("FAKE EXPLORE for " + args[1])
else:
    print("ok " + " ".join(args))
"""


class TestCodeGraphIsolation(DomainCase):
    """CodeGraph is optional, comes after project routing, and never changes
    project semantics (ADR-049)."""

    def setUp(self) -> None:
        self.bin = Path(tempfile.mkdtemp(prefix="praukron-bin-"))
        self.addCleanup(shutil.rmtree, self.bin, True)
        self.log = self.bin / "calls.log"
        self.log.write_text("", encoding="utf-8")

    def path(self, with_codegraph: bool, fail: bool = False) -> dict[str, str]:
        if with_codegraph:
            fake = self.bin / "codegraph"
            fake.write_text(FAKE_CODEGRAPH.format(python=sys.executable), encoding="utf-8")
            fake.chmod(0o755)
            if os.name == "nt":
                # Windows finds commands by PATHEXT and ignores shebangs; npm
                # installs CodeGraph there as a .cmd, so the fake is one too.
                (self.bin / "codegraph.cmd").write_text(
                    f'@"{sys.executable}" "%~dp0codegraph" %*\r\n', encoding="utf-8")
        env = {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "FAKE_CG_LOG": str(self.log)}
        if fail:
            env["FAKE_CG_FAIL"] = "1"
        return env

    def standard(self) -> None:
        self.build([
            _domain_task("T-0", "DONE"), _domain_task("T-1", "WIP", deps=["T-0"]), _domain_task("T-2", deps=["T-1"]),
        ], {"T-0": "PASS", "T-1": "NOT_RUN", "T-2": "NOT_RUN"}, exit_authority="T-2",
            debt=_debt("TD-1", "REACHED", by="T-1"))
        tasks_md = self.dir / layout.AUTHORITY_DIR / "TASKS.md"
        tasks_md.write_text(tasks_md.read_text(encoding="utf-8").replace(
            "## T-1: Work T-1\n- Status: WIP\n", "## T-1: Work T-1\n- Status: WIP\n- Files: src/stt/provider.ts\n- Symbols: STTProvider\n"), encoding="utf-8")
        self.project = compiler.load(self.dir)
        self.report = analytics.report(self.project)
        compiler.write(self.dir, self.project)

    def snapshot(self) -> dict:
        return {p: p.read_bytes() for p in (self.dir / ".praukron").rglob("*") if p.is_file()}

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = cli.main(["-C", str(self.dir), *argv])
        return code, buffer.getvalue()

    def test_anchors_are_optional_task_fields(self) -> None:
        self.standard()
        self.assertEqual((self.project.task("T-1").files, self.project.task("T-1").symbols),
                         (["src/stt/provider.ts"], ["STTProvider"]))
        self.assertEqual(self.project.task("T-2").files, [])

    def test_a_without_codegraph_everything_works(self) -> None:
        self.standard()
        with mock.patch.dict(os.environ, self.path(False)):
            code, out = self.run_cli("codegraph", "status")
            self.assertEqual(code, 0)
            self.assertIn("UNAVAILABLE", out)
            code, out = self.run_cli("retrieve", "T-1", "--code")
            self.assertEqual(code, 0)
            self.assertIn("CodeGraph not used", out)
            self.assertIn("src/stt/provider.ts", out)
            self.assertEqual(self.run_cli("compile")[0], 0)

    def test_b_with_codegraph_project_context_is_identical_and_comes_first(self) -> None:
        self.standard()
        plain = retrieve.retrieve(self.project, self.report, self.dir, "T-1").render()
        before = self.snapshot()
        with mock.patch.dict(os.environ, self.path(True)):
            code, out = self.run_cli("retrieve", "T-1", "--code")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(plain), "project context must be unchanged and first")
        self.assertIn("from CodeGraph, after the project context", out[len(plain):])
        self.assertIn("FAKE EXPLORE for STTProvider src/stt/provider.ts", out)
        self.assertIn("explore STTProvider src/stt/provider.ts -p", self.log.read_text(encoding="utf-8"))
        self.assertEqual(before, self.snapshot())

    def test_c_a_failing_codegraph_degrades_to_the_fallback(self) -> None:
        self.standard()
        before = self.snapshot()
        with mock.patch.dict(os.environ, self.path(True, fail=True)):
            code, out = self.run_cli("retrieve", "T-1", "--code")
            self.assertEqual(code, 0)
            self.assertIn("CodeGraph exited 2: index is locked", out)
            self.assertIn("Continue with repository tools", out)
            self.assertIn("FAILING", self.run_cli("codegraph", "status")[1])
            self.assertEqual(self.run_cli("compile")[0], 0)
        after = self.snapshot()
        self.assertEqual({k: v for k, v in before.items() if "compiled" not in str(k)},
                         {k: v for k, v in after.items() if "compiled" not in str(k)})

    def test_d_codegraph_never_changes_project_semantics(self) -> None:
        self.standard()
        results = []
        for env in (self.path(False), self.path(True), self.path(True, fail=True)):
            with mock.patch.dict(os.environ, env):
                project = compiler.load(self.dir)
                report = analytics.report(project)
                results.append((json.dumps(compiler.as_json(project), sort_keys=True),
                                index.render(project, report, self.dir),
                                retrieve.retrieve(project, report, self.dir, "why is P1 blocked?").render()))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertNotIn("status", self.log.read_text(encoding="utf-8"))  # compile, index and retrieval never call it

    def test_setup_changes_nothing_without_consent(self) -> None:
        self.standard()
        with mock.patch.dict(os.environ, self.path(True)), \
                mock.patch("sys.stdin", io.StringIO("")):
            # The fake reports an initialized index; make it look uninitialized.
            fake = self.bin / "codegraph"
            fake.write_text(fake.read_text(encoding="utf-8").replace('"initialized": True', '"initialized": False'), encoding="utf-8")
            code, out = self.run_cli("codegraph", "setup")
            self.assertEqual(code, 1)
            self.assertIn("Nothing changed", out)
            self.assertNotIn("init", self.log.read_text(encoding="utf-8").replace("status --json", ""))
            code, out = self.run_cli("codegraph", "setup", "--yes", "--wire-agents")
            self.assertIn("init -y", self.log.read_text(encoding="utf-8"))
            self.assertNotIn("install", self.log.read_text(encoding="utf-8"))
            self.assertIn("Agent configuration left unchanged", out)

    def test_the_index_is_the_same_on_every_machine(self) -> None:
        self.standard()
        text = (self.dir / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("## Implementation intelligence", text)
        self.assertIn("- suggested: `praukron retrieve T-1 --code`", text)
        self.assertNotIn("AVAILABLE", text)


class TestViews(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = analytics.report(self.project)
        self.views = views.render_all(self.project, self.report)

    def test_compiled_directory_is_entirely_generated(self) -> None:
        compiler.write(self.root, self.project)
        written = sorted(p.name for p in (self.root / layout.COMPILED_DIR).iterdir())
        self.assertEqual(
            written, ["README.md", "STATE.md", "TASK_GRAPH.md", "project.json"]
        )

    def test_views_declare_themselves_generated(self) -> None:
        for name, text in self.views.items():
            self.assertIn("Generated by `praukron compile`", text, name)

    def test_state_reports_derived_facts_only(self) -> None:
        state = self.views["STATE.md"]
        self.assertIn("Current phase: P1", state)
        self.assertIn("Gate A Single authority: RED", state)
        self.assertIn("Ready: T-TWO", state)
        self.assertIn("HANDOFF.md", state)

    def test_task_graph_lists_dependencies_and_eligibility(self) -> None:
        graph = self.views["TASK_GRAPH.md"]
        self.assertIn("- T-TWO [TODO] Build on it", graph)
        self.assertIn("depends on: T-ONE", graph)
        self.assertIn("unlocks: T-THREE", graph)


class TestHostileAuthoredText(FixtureCase):
    """Authored prose is data. It must never become markup or break the page."""

    def poison(self, text: str) -> str:
        self.rewrite("TASKS.md", "## T-TWO: Build on it", f"## T-TWO: {text}")
        report = analytics.report(self.project)
        return dashboard.render(self.project, report, compiler.as_json(self.project))

    def json_island(self, html: str, element: str) -> dict:
        start = html.index(f'id="{element}">') + len(f'id="{element}">')
        return json.loads(html[start : html.index("</script>", start)].replace("<\\/", "</"))

    def test_a_closing_script_tag_does_not_break_the_page(self) -> None:
        html = self.poison("Ship </script><script>alert(1)</script> it")
        self.assertNotIn("</script><script>alert(1)", html)
        island = self.json_island(html, "project-data")
        self.assertIn("alert(1)", island["tasks"][1]["title"])

    def test_markup_in_a_title_renders_as_text(self) -> None:
        html = self.poison("Ship <b>bold</b> & <img> it")
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; &lt;img&gt;", html)
        self.assertNotIn("<td>Ship <b>bold</b>", html)

    def test_quotes_in_a_title_cannot_escape_an_attribute(self) -> None:
        html = self.poison('Ship " onload="alert(1)" x="')
        self.assertNotIn('onload="alert(1)"', html)

    def test_the_client_escapes_what_it_renders(self) -> None:
        html = self.poison("Ship <b>bold</b> it")
        self.assertIn("const esc = value =>", html)
        self.assertIn("${esc(task.title)}", html)


class TestContractIntegrity(FixtureCase):
    """A contract must never get quietly weaker than it reads."""

    def test_a_criterion_without_an_evidence_class_is_rejected(self) -> None:
        with self.assertRaises(ParseError) as caught:
            self.rewrite(
                "ACCEPTANCE.md",
                "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.\n  `TEST` · `NOT_RUN`",
                "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.",
            )
        self.assertIn("no evidence class", str(caught.exception))
        self.assertEqual(caught.exception.anchor, "AC-T-TWO-01")

    def test_a_broken_criterion_does_not_swallow_the_next_one(self) -> None:
        path = self.root / layout.AUTHORITY_DIR / "ACCEPTANCE.md"
        path.write_text(
            "# Acceptance\n\n# Contracts\n\n## AC-T-ONE — One\n\n"
            "- `AC-T-ONE-01` — Given a bullet, When its class is missing, Then it is caught.\n"
            "- `AC-T-ONE-02` — Given the next bullet, Then it survives. `TEST` · `PASS`\n"
        , encoding="utf-8")
        with self.assertRaises(ParseError):
            compiler.load(self.root)

    def test_a_done_task_with_an_empty_contract_is_an_error(self) -> None:
        self.rewrite(
            "ACCEPTANCE.md",
            "- `AC-T-ONE-01` — Given a site, When the foundation is poured, Then it is\n"
            "  level. `TEST` · `PASS`\n  - Evidence: Measured on site.",
            "",
        )
        findings = validate.check(self.project)
        self.assertIn("empty-contract", {f.code for f in findings})
        self.assertTrue(validate.errors(findings))

    def test_an_open_task_with_an_empty_contract_only_warns(self) -> None:
        self.rewrite(
            "ACCEPTANCE.md",
            "- `AC-T-TWO-01` — Given a foundation, When walls go up, Then they stand.\n  `TEST` · `NOT_RUN`",
            "",
        )
        findings = validate.check(self.project)
        self.assertIn("empty-contract", {f.code for f in findings})
        self.assertEqual(validate.errors(findings), [])


class TestDegenerateProject(unittest.TestCase):
    """An empty chronicle is a normal starting state, not an error."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="praukron-empty-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        authority = self.root / layout.AUTHORITY_DIR
        (authority / "ADR").mkdir(parents=True)
        (authority / "PHASES.md").write_text("# Phases\n\nNo phases yet.\n", encoding="utf-8")
        (authority / "TASKS.md").write_text("# Tasks\n\nNo tasks yet.\n", encoding="utf-8")
        (authority / "ACCEPTANCE.md").write_text("# Acceptance\n\n# Contracts\n\nNone yet.\n", encoding="utf-8")
        (authority / "INTENT.md").write_text("# Intent\n", encoding="utf-8")
        (authority / "HANDOFF.md").write_text("# Handoff\n", encoding="utf-8")
        self.project = compiler.load(self.root)
        self.report = analytics.report(self.project)

    def test_nothing_crashes_on_an_empty_chronicle(self) -> None:
        self.assertEqual(validate.check(self.project), [])
        compiler.write(self.root, self.project)
        mermaid.render_all(self.project, self.report)
        views.render_all(self.project, self.report)
        dashboard.render(self.project, self.report, compiler.as_json(self.project))

    def test_every_command_succeeds(self) -> None:
        for argv in (["validate"], ["compile"], ["status"], ["graph"], ["dashboard"]):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["-C", str(self.root), *argv]), 0, argv)

    def test_empty_views_say_so_rather_than_inventing(self) -> None:
        state = views.state(self.project, self.report)
        self.assertIn("Current phase: none", state)
        self.assertIn("None recorded.", state)


class TestGateBlocking(FixtureCase):
    def test_a_gate_blocks_only_the_phase_it_names(self) -> None:
        from praukron.model import Gate, Source

        gate = Gate("Gate X", "X", "", ["P11 exit"], [], "RED", Source("PHASES.md", "Gate X"))
        self.assertFalse(analytics._blocks(gate, "P1"))
        self.assertTrue(analytics._blocks(gate, "P11"))

    def test_a_green_gate_blocks_nothing(self) -> None:
        self.rewrite("PHASES.md", "Status: RED", "Status: GREEN")
        report = analytics.report(self.project)
        self.assertNotIn("GATE_BLOCKER", {o.type for o in report.obstacles})


class TestCli(FixtureCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["-C", str(self.root), *argv])
        return code, buffer.getvalue()

    def test_validate_passes_on_clean_authority(self) -> None:
        code, out = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertIn("consistent", out)

    def test_validate_fails_on_broken_authority(self) -> None:
        self.rewrite("TASKS.md", "- Dependencies: T-ONE", "- Dependencies: T-GHOST")
        code, _ = self.run_cli("validate")
        self.assertEqual(code, 1)

    def test_compile_refuses_broken_authority_without_force(self) -> None:
        self.rewrite("TASKS.md", "- Dependencies: T-ONE", "- Dependencies: T-GHOST")
        code, _ = self.run_cli("compile")
        self.assertEqual(code, 1)
        self.assertFalse((self.root / layout.COMPILED_DIR / "project.json").exists())
        self.assertEqual(self.run_cli("compile", "--force")[0], 0)

    def test_status_reports_position(self) -> None:
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("phase P1", out)
        self.assertIn("T-TWO", out)

    def test_graph_and_dashboard_write_only_compiled_files(self) -> None:
        self.assertEqual(self.run_cli("graph")[0], 0)
        self.assertEqual(self.run_cli("dashboard")[0], 0)
        written = {p.name for p in (self.root / layout.COMPILED_DIR).iterdir()}
        self.assertIn("dashboard.html", written)
        self.assertIn("task-graph.mmd", written)

    def test_explain_and_context(self) -> None:
        self.assertIn("AC-T-TWO-01", self.run_cli("explain", "T-TWO")[1])
        packet = json.loads(self.run_cli("context", "T-TWO")[1])
        self.assertEqual(packet["task"]["id"], "T-TWO")

    def test_unknown_task_exits_nonzero(self) -> None:
        self.assertEqual(cli.main(["-C", str(self.root), "explain", "T-NOPE"]), 1)

    def test_missing_project_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(cli.main(["-C", empty, "status"]), 1)

    def test_parse_error_is_reported_not_raised(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "TASKS.md").write_text("# Tasks\n\n## broken\n", encoding="utf-8")
        self.assertEqual(cli.main(["-C", str(self.root), "status"]), 1)

    def test_status_counts_done_work_nobody_reviewed(self) -> None:
        """`60 / 60 done` reads as finished; how much of it anyone checked is a
        separate number, and it has to be on the same screen (ADR-041)."""
        out = self.run_cli("status")[1]
        self.assertIn("1 done without review, 1 of them with exercised evidence", out)
        self.rewrite("TASKS.md", "- Validation: SYNTHETIC", "- Validation: AI_REVIEWED")
        self.assertNotIn("done without review", self.run_cli("status")[1])


class TestNewerCompiledViews(FixtureCase):
    """Two people on two releases share one chronicle. The older runtime must
    not quietly rewrite what the newer one wrote (ADR-041)."""

    def setUp(self) -> None:
        super().setUp()
        with redirect_stdout(io.StringIO()):
            cli.main(["-C", str(self.root), "dashboard"])
        self.compiled = self.root / layout.COMPILED_DIR

    def stamp(self, version: str) -> None:
        target = self.compiled / "project.json"
        target.write_text(re.sub(
            r'"generatorVersion": "[^"]*"', f'"generatorVersion": "{version}"', target.read_text(encoding="utf-8")
        ), encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(self.compiled.iterdir())}

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = cli.main(["-C", str(self.root), *argv])
        return code, buffer.getvalue()

    def test_writers_refuse_views_from_a_newer_runtime(self) -> None:
        self.stamp("999.0.0")
        before = self.snapshot()
        for command in ("compile", "graph", "dashboard"):
            with self.subTest(command=command):
                code, out = self.run_cli(command)
                self.assertEqual(code, 1)
                self.assertIn("999.0.0", out)
                self.assertEqual(self.snapshot(), before)

    def test_force_overrides_the_refusal(self) -> None:
        self.stamp("999.0.0")
        for command in ("compile", "graph", "dashboard"):
            with self.subTest(command=command):
                self.assertEqual(self.run_cli(command, "--force")[0], 0)
                self.stamp("999.0.0")

    def test_status_advises_upgrading_not_recompiling(self) -> None:
        self.stamp("999.0.0")
        out = self.run_cli("status")[1]
        self.assertIn("newer", out)
        self.assertIn("Upgrade", out)
        self.assertNotIn("to refresh them", out)

    def test_views_from_an_older_runtime_are_still_refreshed(self) -> None:
        self.stamp("0.0.1")
        self.assertIn("to refresh them", self.run_cli("status")[1])
        self.assertEqual(self.run_cli("compile")[0], 0)
        self.assertNotIn("0.0.1", (self.compiled / "project.json").read_text(encoding="utf-8"))


class TestStaleReferences(FixtureCase):
    """A handoff that sends the next agent to a moved file is wrong in a way
    no structural check saw (continuity pilot, ADR-041)."""

    def handoff(self, text: str) -> list[str]:
        (self.root / layout.AUTHORITY_DIR / "HANDOFF.md").write_text(f"# Handoff\n\n{text}\n", encoding="utf-8")
        self.project = compiler.load(self.root)
        return [f.message for f in validate.check(self.project) if f.code == "stale-reference"]

    def test_a_missing_path_is_reported(self) -> None:
        found = self.handoff("Continue in `docs/moved/PLAN.md`.")
        self.assertEqual(len(found), 1)
        self.assertIn("docs/moved/PLAN.md", found[0])

    def test_intent_is_checked_too(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "INTENT.md").write_text("# Intent\n\nSee `src/gone.py`.\n", encoding="utf-8")
        self.project = compiler.load(self.root)
        self.assertIn("stale-reference", self.codes())

    def test_real_paths_and_other_text_are_not_reported(self) -> None:
        found = self.handoff(
            "Read `.praukron/chronicle/TASKS.md` and `ADR/ADR-001.md`, then branch"
            " `feature/work`, run `praukron compile`, see `../other-repo/notes.md`,"
            " `/etc/hosts.conf`, `https://example.com/a.md`, and `src/*.py`."
        )
        self.assertEqual(found, [])

    def test_it_is_a_warning(self) -> None:
        self.handoff("Continue in `docs/moved/PLAN.md`.")
        self.assertEqual(validate.errors(validate.check(self.project)), [])


class TestMergeGuidance(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_resume_and_checkpoint_say_how_to_settle_a_merge(self) -> None:
        for name in ("praukron-resume.md", "praukron-checkpoint.md"):
            text = " ".join((self.ROOT / ".praukron/commands" / name).read_text(encoding="utf-8").split())
            with self.subTest(workflow=name):
                self.assertIn("After merging branches", text)
                self.assertIn("`.praukron/praukron compile`", text)
                self.assertIn("resolve `INTENT.md` and `HANDOFF.md` by hand", text)
                self.assertIn("whose work is current", text)


@unittest.skipUnless(
    (Path(__file__).resolve().parents[1] / layout.AUTHORITY_DIR / "TASKS.md").is_file(),
    "this repository's chronicle is kept locally and not published (ADR-042)",
)
class TestThisRepository(unittest.TestCase):
    """Praukron's own chronicle is the acceptance evidence for Phase 2 closure.

    A fixture proves the code works. These prove it works on the real project,
    which is what the Phase 2 definition of done actually claims.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.project = compiler.load(cls.root)
        cls.report = analytics.report(cls.project)

    def test_authority_is_consistent(self) -> None:
        self.assertEqual(
            [f.render() for f in validate.errors(validate.check(self.project))], []
        )

    def test_every_task_resolves_to_a_contract(self) -> None:
        for task in self.project.tasks:
            with self.subTest(task=task.id):
                self.assertIsNotNone(task.contract, f"{task.id} has no AC reference")
                self.assertIn(task.contract, self.project.contracts)
                self.assertTrue(
                    self.project.contracts[task.contract].criteria,
                    f"{task.contract} states no criteria",
                )

    def test_every_task_belongs_to_a_known_phase(self) -> None:
        known = {phase.id for phase in self.project.phases} | {"P-NONE"}
        for task in self.project.tasks:
            with self.subTest(task=task.id):
                self.assertIn(task.phase, known)

    def test_every_done_task_has_its_criteria_met(self) -> None:
        for task in self.project.tasks:
            if not task.done:
                continue
            unmet = analytics.unmet(self.project, task)
            with self.subTest(task=task.id):
                self.assertEqual(unmet, [], f"{task.id} is DONE with unmet criteria")

    def test_deleting_compiled_state_reproduces_it_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            copy = Path(workspace) / "repo"
            shutil.copytree(
                self.root / layout.AUTHORITY_DIR, copy / layout.AUTHORITY_DIR, dirs_exist_ok=False
            )
            project = compiler.load(copy)
            report = analytics.report(project)
            first = {"project.json": compiler.write(copy, project).read_bytes()}
            for name, text in mermaid.render_all(project, report).items():
                first[name] = text.encode()
            for name, text in views.render_all(project, report).items():
                first[name] = text.encode()

            shutil.rmtree(copy / layout.COMPILED_DIR)
            rebuilt = compiler.load(copy)
            rebuilt_report = analytics.report(rebuilt)
            second = {"project.json": compiler.write(copy, rebuilt).read_bytes()}
            for name, text in mermaid.render_all(rebuilt, rebuilt_report).items():
                second[name] = text.encode()
            for name, text in views.render_all(rebuilt, rebuilt_report).items():
                second[name] = text.encode()

            self.assertEqual(first, second)


LEGACY_TASKS = """# Tasks

## T-REAL-01: Ship the thing
- Status: DONE
- Validation: SYNTHETIC
- Dependencies: none
- Owner: me
- Acceptance: The thing ships and stays shipped.
- Evidence: 12 unittest cases passed.
- Governed by: ADR-001

## T-REAL-02: Ship the next thing
- Status: TODO
- Validation: UNTESTED
- Dependencies: T-REAL-01
- Owner: me
- Acceptance: The next thing ships.
- Evidence: —
- Governed by: ADR-002
"""

LEGACY_DECISIONS = """# Decisions

## ADR-001: Do the simple thing
- Date: 2026-01-01
- Status: ACCEPTED
- Supersedes: none
- Decision: Prefer the simple thing.

## ADR-002: Do the other thing instead
- Date: 2026-02-01
- Status: ACCEPTED
- Supersedes: ADR-001
- Decision: The simple thing did not survive contact.
"""

LEGACY_STATE = """# State

## Current
- Project: Legacy thing

## Risks / uncertainty
- The vendor API may change without notice.

## Next
- Call the vendor before building anything else.
"""


class TestMigration(unittest.TestCase):
    """Upgrading must move a v0.1 chronicle, not strand it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="praukron-legacy-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        legacy = self.root / layout.V01_AUTHORITY_DIR
        legacy.mkdir()
        (legacy / "TASKS.md").write_text(LEGACY_TASKS, encoding="utf-8")
        (legacy / "DECISIONS.md").write_text(LEGACY_DECISIONS, encoding="utf-8")
        (legacy / "STATE.md").write_text(LEGACY_STATE, encoding="utf-8")
        (legacy / "TASK_GRAPH.md").write_text("# Task Graph\n\n- T-REAL-01 [DONE]\n", encoding="utf-8")
        (legacy / "INTENT.md").write_text("# Intent\n\nNo intent.\n", encoding="utf-8")
        (legacy / "JOURNAL.md").write_text("# Journal\n\n## 2026-01-01\n- Did: things\n", encoding="utf-8")
        authority = self.root / layout.AUTHORITY_DIR
        (authority / "ADR").mkdir(parents=True)
        (authority / "TASKS.md").write_text("# Tasks\n\nNo tasks yet.\n", encoding="utf-8")
        (authority / "PHASES.md").write_text("# Phases\n\nNo phases yet.\n", encoding="utf-8")
        (authority / "ACCEPTANCE.md").write_text("# Acceptance\n\n# Contracts\n\nNone yet.\n", encoding="utf-8")
        (authority / "INTENT.md").write_text("# Intent\n", encoding="utf-8")
        (authority / "HANDOFF.md").write_text("# Handoff\n", encoding="utf-8")
        (authority / "JOURNAL.md").write_text("# Journal\n", encoding="utf-8")

    def test_a_legacy_chronicle_is_detected(self) -> None:
        self.assertTrue(migrate.needs_migration(self.root))

    def test_a_current_chronicle_is_not_touched(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "TASKS.md").write_text(LEGACY_TASKS, encoding="utf-8")
        self.assertFalse(migrate.needs_migration(self.root))
        with self.assertRaises(migrate.MigrationError):
            migrate.plan(self.root)

    def test_planning_changes_nothing(self) -> None:
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        plan = migrate.plan(self.root)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(plan.tasks, 2)
        self.assertEqual(plan.decisions, 2)

    def test_tasks_and_contracts_survive(self) -> None:
        migrate.apply(self.root)
        project = compiler.load(self.root)
        self.assertEqual([t.id for t in project.tasks], ["T-REAL-01", "T-REAL-02"])
        contract = project.contracts["AC-T-REAL-01"]
        self.assertEqual(contract.criteria[0].text, "The thing ships and stays shipped.")
        self.assertEqual(contract.criteria[0].state, "PASS")
        self.assertEqual(contract.criteria[0].evidence_class, "TEST")
        self.assertEqual(project.contracts["AC-T-REAL-02"].criteria[0].state, "NOT_RUN")

    def test_decisions_become_files_with_supersession(self) -> None:
        migrate.apply(self.root)
        adr = self.root / layout.AUTHORITY_DIR / "ADR"
        self.assertTrue((adr / "ADR-001.md").is_file())
        self.assertIn("Prefer the simple thing", (adr / "ADR-001.md").read_text(encoding="utf-8"))
        self.assertIn("superseded by ADR-002", (adr / "README.md").read_text(encoding="utf-8"))

    def test_state_prose_is_rescued_and_task_graph_is_not(self) -> None:
        migrate.apply(self.root)
        handoff = (self.root / layout.AUTHORITY_DIR / "HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("vendor API may change", handoff)
        self.assertIn("Call the vendor", handoff)
        self.assertFalse((self.root / layout.AUTHORITY_DIR / "TASK_GRAPH.md").exists())

    def test_originals_are_archived_not_deleted(self) -> None:
        plan = migrate.apply(self.root)
        self.assertTrue(plan.archive.is_dir())
        self.assertEqual(
            (plan.archive / "TASKS.md").read_text(encoding="utf-8"), LEGACY_TASKS
        )
        self.assertEqual((plan.archive / "TASK_GRAPH.md").read_text(encoding="utf-8").strip().splitlines()[0], "# Task Graph")
        self.assertFalse((self.root / layout.COMPILED_DIR / "TASKS.md").exists())

    def test_the_result_validates_and_compiles(self) -> None:
        migrate.apply(self.root)
        project = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(project)), [])
        compiler.write(self.root, project)
        self.assertEqual(analytics.report(project).metrics()["taskCompletion"]["total"], 2)

    def test_a_named_phase_can_be_assigned(self) -> None:
        (self.root / layout.AUTHORITY_DIR / "PHASES.md").write_text(
            "# Phases\n\n## P1 — Delivery\n\nOutcome:\nShip.\n\nEntry:\n- none\n\n"
            "Exit:\n- none\n\nExit authority:\nT-REAL-02\n\nStatus:\nACTIVE\n"
        , encoding="utf-8")
        migrate.apply(self.root, phase="P1")
        project = compiler.load(self.root)
        self.assertEqual({t.phase for t in project.tasks}, {"P1"})

    def test_cli_reports_then_applies(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.root), "migrate"]), 0)
        self.assertIn("Nothing was changed", buffer.getvalue())
        self.assertTrue(migrate.needs_migration(self.root))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["-C", str(self.root), "migrate", "--apply"]), 0)
        self.assertFalse(migrate.needs_migration(self.root))

    def test_status_says_migration_is_needed(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(["-C", str(self.root), "status"])
        self.assertIn("praukron migrate", buffer.getvalue())


class TestRelocation(unittest.TestCase):
    """A v0.2 chronicle sits at the repository root. ADR-024 moves it inside
    `.praukron/`, and that move must not rewrite a single record."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="praukron-v02-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        build_fixture(self.root, authority=layout.V02_AUTHORITY_DIR)
        self.source = self.root / layout.V02_AUTHORITY_DIR
        self.before = {
            path.relative_to(self.source): path.read_bytes()
            for path in sorted(self.source.rglob("*"))
            if path.is_file()
        }
        # Compiled output from the old layout, and a stale host pointer.
        compiled = self.root / layout.V02_COMPILED_DIR
        compiled.mkdir(parents=True, exist_ok=True)
        (compiled / "STATE.md").write_text("stale\n", encoding="utf-8")
        (compiled / "task-graph.mmd").write_text("stale\n", encoding="utf-8")
        pointer = self.root / ".claude" / "commands"
        pointer.mkdir(parents=True)
        (pointer / "prokron-work.md").write_text(
            "Follow `commands/prokron-work.md`. Use `$ARGUMENTS` as the task.\n"
        , encoding="utf-8")
        commands = self.root / layout.V02_COMMANDS_DIR
        commands.mkdir()
        (commands / "prokron-work.md").write_text("# /prokron-work\n\nCUSTOM\n", encoding="utf-8")

    def test_it_is_detected_and_the_v01_migration_is_not(self) -> None:
        self.assertTrue(migrate.needs_relocation(self.root))
        self.assertFalse(migrate.needs_migration(self.root))

    def test_every_record_moves_byte_for_byte(self) -> None:
        migrate.relocate(self.root)
        authority = self.root / layout.AUTHORITY_DIR
        after = {
            path.relative_to(authority): path.read_bytes()
            for path in sorted(authority.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(after, self.before)
        self.assertFalse(self.source.exists())

    def test_nothing_is_archived_because_nothing_is_transformed(self) -> None:
        plan = migrate.relocate(self.root)
        self.assertIsNone(plan.archive)
        self.assertEqual(plan.tasks, 3)

    def test_stale_compiled_output_is_discarded_not_carried_over(self) -> None:
        migrate.relocate(self.root)
        compiled = self.root / layout.V02_COMPILED_DIR
        self.assertFalse((compiled / "STATE.md").exists())
        self.assertFalse((compiled / "task-graph.mmd").exists())

    def test_a_host_pointer_names_the_document_that_actually_exists(self) -> None:
        migrate.relocate(self.root)
        pointer = (self.root / ".claude" / "commands" / "prokron-work.md").read_text(encoding="utf-8")
        named = pointer.split("`")[1]
        self.assertEqual(named, f"{layout.COMMANDS_DIR}/praukron-work.md")
        self.assertTrue((self.root / named).is_file())
        self.assertIn("CUSTOM", (self.root / named).read_text(encoding="utf-8"))

    def test_the_result_validates_and_compiles(self) -> None:
        migrate.relocate(self.root)
        project = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(project)), [])
        compiler.write(self.root, project)
        self.assertTrue(
            (self.root / layout.COMPILED_DIR / "project.json").is_file()
        )

    def test_relocating_twice_refuses_rather_than_half_moving(self) -> None:
        migrate.relocate(self.root)
        with self.assertRaises(migrate.MigrationError):
            migrate.relocate(self.root)


class TestSingleDirectory(unittest.TestCase):
    """Everything Praukron owns is addressed inside one directory (ADR-024)."""

    def test_every_path_praukron_chooses_lives_under_one_directory(self) -> None:
        chosen = (
            layout.AUTHORITY_DIR,
            layout.COMPILED_DIR,
            layout.COMMANDS_DIR,
            layout.RUNTIME_DIR,
            layout.ENTRY_POINT,
        )
        for path in chosen:
            with self.subTest(path=path):
                self.assertTrue(path.startswith(f"{layout.HOME_DIR}/"))

    def test_authored_and_compiled_stay_separate(self) -> None:
        self.assertNotEqual(layout.AUTHORITY_DIR, layout.COMPILED_DIR)
        self.assertFalse(layout.COMPILED_DIR.startswith(layout.AUTHORITY_DIR))
        self.assertFalse(layout.AUTHORITY_DIR.startswith(layout.COMPILED_DIR))


class TestProjectIdentity(unittest.TestCase):
    """The compiled name used to come from the directory the repository sat in,
    so two clones of one chronicle disagreed. A fixture is always made under a
    single name, which is why only CI could find it (ADR-030)."""

    def chronicle_in(self, root: Path, project_line: str = "Project: Fixture\n\n") -> Path:
        authority = root / layout.AUTHORITY_DIR
        (authority / "ADR").mkdir(parents=True)
        (authority / "PHASES.md").write_text(project_line + PHASES, encoding="utf-8")
        (authority / "TASKS.md").write_text(TASKS, encoding="utf-8")
        (authority / "ACCEPTANCE.md").write_text(ACCEPTANCE, encoding="utf-8")
        (authority / "ADR" / "ADR-001.md").write_text(ADR, encoding="utf-8")
        (authority / "INTENT.md").write_text("# Intent\n\nNone.\n", encoding="utf-8")
        (authority / "HANDOFF.md").write_text("# Handoff\n\nNone.\n", encoding="utf-8")
        return root

    def test_one_chronicle_compiles_the_same_from_any_directory(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            names = []
            for directory in ("alpha", "a-different-checkout"):
                root = self.chronicle_in(Path(workspace) / directory)
                names.append(compiler.load(root).name)
            self.assertEqual(names[0], names[1])
            self.assertEqual(names[0], "Fixture")

    def test_a_chronicle_that_names_nothing_keeps_the_directory_name(self) -> None:
        """Every installation that predates the field has no line to read, and
        must keep working without being migrated."""
        with tempfile.TemporaryDirectory() as workspace:
            root = self.chronicle_in(Path(workspace) / "unnamed", project_line="")
            self.assertEqual(compiler.load(root).name, "unnamed")

    def test_the_name_is_read_from_authority_only_above_the_first_heading(self) -> None:
        """`Project:` inside a phase body is prose, not the project's name."""
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / "elsewhere"
            self.chronicle_in(root, project_line="")
            phases = root / layout.AUTHORITY_DIR / "PHASES.md"
            phases.write_text(phases.read_text(encoding="utf-8").replace(
                "# Phases", "# Phases\n\nProject: NotTheName", 1), encoding="utf-8")
            self.assertEqual(compiler.load(root).name, "elsewhere")


class TestPublishedDocumentation(unittest.TestCase):
    """The public documents drift silently. A path that moved, a count that
    grew, a version that shipped — none of it fails anything, and a reader
    finds it before a maintainer does. Twice now this drift has published an
    instruction that no longer meant what it said."""

    ROOT = Path(__file__).resolve().parents[1]
    PUBLISHED = (
        "README.md",
        "docs/SPEC.md",
        "docs/PRODUCT-THESIS.md",
        "docs/GUIDE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
    )

    def documents(self):
        for name in self.PUBLISHED:
            path = self.ROOT / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            yield path, path.read_text(encoding="utf-8")

    def test_every_relative_link_resolves(self) -> None:
        for path, text in self.documents():
            targets = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
            targets += re.findall(r'<img src="([^"]+)"', text)
            for target in targets:
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                resolved = (path.parent / target.split("#")[0]).resolve()
                with self.subTest(document=path.name, link=target):
                    self.assertTrue(resolved.exists(), f"dead link: {target}")

    def test_no_document_tells_a_reader_to_delete_their_records(self) -> None:
        """`rm -rf .praukron` was correct until the installation moved into that
        directory. The instruction survived the move and began telling readers
        to delete their own chronicle."""
        for path, text in self.documents():
            for command in re.findall(r"rm -rf\s+(\S+)", text):
                with self.subTest(document=path.name, command=command):
                    self.assertTrue(
                        command.rstrip("/").endswith("compiled"),
                        f"{path.name} deletes {command}, which is not compiled output",
                    )

    def test_the_readme_teaches_one_way_to_install_and_one_way_to_run(self) -> None:
        """Installing took `| sh -s -- existing` and every command afterwards
        was `.praukron/praukron status`. Both are artefacts of a private tool.
        A README that shows two forms teaches neither."""
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        primary = readme.split("### 1. Install in your project", 1)[1].split("<details>", 1)[0]
        self.assertIn("install.sh | sh\n", primary)
        self.assertNotIn("sh -s --", primary)
        for block in re.findall(r"```(?:sh|console)\n(.*?)```", readme, re.S):
            for line in block.splitlines():
                command = line.lstrip("$ ").strip()
                with self.subTest(line=line):
                    self.assertFalse(
                        command.startswith(".praukron/praukron"),
                        "published examples run `praukron`, not a path",
                    )

    def test_citation_metadata_names_the_release_it_ships_with(self) -> None:
        """A deposited record is durable, so metadata that disagrees with the
        release is worse than metadata that is missing (ADR-032)."""
        version = (self.ROOT / "VERSION").read_text(encoding="utf-8").strip()
        citation = (self.ROOT / "CITATION.cff").read_text(encoding="utf-8")
        named = re.search(r"(?m)^version:\s*(\S+)\s*$", citation)
        self.assertIsNotNone(named, "CITATION.cff records no version")
        self.assertEqual(named.group(1).strip('"\''), version)

    def test_the_readme_names_the_release_it_ships_with(self) -> None:
        version = (self.ROOT / "VERSION").read_text(encoding="utf-8").strip()
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        named = set(re.findall(r"v(\d+\.\d+\.\d+)", readme))
        self.assertIn(version, named, f"README names {named or 'no release'}, not {version}")


class TestBaselineWorkflow(unittest.TestCase):
    """The baseline is the one place an agent writes about a past it did not
    watch (ADR-037). Every limit on it lives in prose, so the prose is what
    has to be pinned."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_the_workflow_states_every_limit(self) -> None:
        text = " ".join((self.ROOT / ".praukron/commands/praukron-baseline.md").read_text(encoding="utf-8").split())
        for rule in (
            "only when the project owner explicitly asks",
            "never part of `/praukron-init`",
            "at most ten",
            "`Status: PROPOSED`",
            "`Origin: RECONSTRUCTED`",
            "Every cited path must exist",
            "do not copy them into the chronicle",
            "Create no tasks, journal history, or intent",
            "never delete one",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_every_host_surface_offers_it(self) -> None:
        for path in (
            ".claude/commands/praukron-baseline.md",
            ".opencode/commands/praukron-baseline.md",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    ".praukron/commands/praukron-baseline.md", (self.ROOT / path).read_text(encoding="utf-8")
                )
        self.assertIn("baseline", (self.ROOT / ".agents/skills/praukron/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("/praukron-baseline", (self.ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn("resume baseline; do", (self.ROOT / "install.sh").read_text(encoding="utf-8"))

    def test_initialization_does_not_run_it(self) -> None:
        text = " ".join((self.ROOT / ".praukron/commands/praukron-init.md").read_text(encoding="utf-8").split())
        self.assertIn("initialization never runs it", text)


class TestBootProtocol(unittest.TestCase):
    """A new agent reads the index before anything else (ADR-047)."""

    ROOT = Path(__file__).resolve().parents[1]

    def text(self, name: str) -> str:
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def test_d_agents_md_reads_the_index_first(self) -> None:
        text = self.text("AGENTS.md")
        index_step = text.index("1. Read `.praukron/chronicle/INDEX.md`.")
        self.assertLess(index_step, text.index("3. Read only those records."))
        self.assertLess(index_step, text.index("5. Explore the code only after the project context is resolved."))
        self.assertIn("Do not load the whole chronicle by default.", text)
        # The rest of the Praukron guidance is still there.
        for kept in ("## Completion", "## Reviewer", "## Disagreement", "## Checkpoint"):
            self.assertIn(kept, text)

    def test_e_claude_md_reads_the_index_first(self) -> None:
        text = self.text("CLAUDE.md")
        self.assertTrue(text.startswith("@AGENTS.md"))
        self.assertIn("Read `.praukron/chronicle/INDEX.md` before anything else", text)
        self.assertLess(text.index("INDEX.md"), text.index("Explore code only once"))
        self.assertEqual(self.text("templates/claude/CLAUDE.md"),
                         text.split("@AGENTS.md", 1)[1].strip())

    def test_agents_md_reads_only_the_target_record_when_editing(self) -> None:
        text = self.text("AGENTS.md")
        block = text[text.index("<!-- project-praukron:start -->"):text.index("<!-- project-praukron:end -->")]
        self.assertIn("do not read the whole file. Read only the record you change", block)
        self.assertIn("locate its `## <id>` heading", block)
        self.assertIn("read that section with a ranged read", block)
        self.assertIn("When appending a new record, read only the end of the file.", block)
        self.assertIn("`.praukron/praukron retrieve <id>` returns that record, and for a task its contract and governing decisions, each with its source", block)

    def test_f_agents_md_scopes_context_before_code(self) -> None:
        """ADR-050: the task packet comes after the index and before code."""
        text = self.text("AGENTS.md")
        index_step = text.index("1. Read `.praukron/chronicle/INDEX.md`.")
        packet_step = text.index("run `.praukron/praukron context <task>`")
        self.assertLess(index_step, packet_step)
        self.assertLess(packet_step, text.index("5. Explore the code only after"))
        self.assertIn("Do not start a task merely because unrelated work is visible", text)
        self.assertIn("are derived maps, not authority; never edit them", text)
        self.assertIn("the record wins: report the inconsistency rather than reconciling it silently", text)
        # Nothing tells an agent to read the chronicle whole.
        self.assertNotRegex(text.lower().replace("do not load the whole chronicle", ""),
                            r"(read|load|scan) (all|every|the (whole|entire)) (of the )?chronicle")

    def test_g_claude_md_is_a_thin_adapter(self) -> None:
        block = self.text("templates/claude/CLAUDE.md")
        self.assertIn("Follow AGENTS.md", block)
        self.assertIn("`.praukron/praukron context <task>`", block)
        for protocol in ("## Completion", "## Reviewer", "## Disagreement", "Recognize these workflows"):
            self.assertNotIn(protocol, block)
        self.assertLess(len(block), 800)

    def test_the_chronicle_read_order_starts_with_the_index(self) -> None:
        text = (self.ROOT / "templates/chronicle/README.md").read_text(encoding="utf-8")
        order = text.split("## Read order", 1)[1]
        self.assertTrue(order.strip().startswith("1. `INDEX.md` first"))
        self.assertIn("`INDEX.md` is generated by `praukron compile` and is not authority", text)
        self.assertIn("INDEX.md", self.text(".praukron/commands/praukron-resume.md"))


class TestHierarchyGuidance(unittest.TestCase):
    """Every workflow and installed surface follows thesis -> phase -> module -> task."""

    ROOT = Path(__file__).resolve().parents[1]

    def text(self, name: str) -> str:
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def test_initialization_authors_the_thesis_first_and_in_order(self) -> None:
        text = self.text(".praukron/commands/praukron-init.md")
        self.assertIn("find the product thesis first", text)
        self.assertIn("thesis → phases → modules → tasks", text)
        self.assertLess(text.index("`THESIS.md`"), text.index("`TASKS.md`"))
        self.assertIn("Do not inspect the repository to invent a historical chronicle", text)

    def test_each_workflow_locates_and_preserves_the_lineage(self) -> None:
        for name, phrase in (
            ("work", "its `lineage` names the task's thesis, phase, and module"),
            ("work", "give it one `Module:` from `MODULES.md`"),
            ("decide", "thesis → phase → module → task lineage stays traceable"),
            ("checkpoint", "naming the active task with its module and phase"),
            ("resume", "the thesis, phase, and module the task belongs to"),
        ):
            with self.subTest(workflow=name):
                self.assertIn(phrase, self.text(f".praukron/commands/praukron-{name}.md"))

    def test_templates_and_agent_instructions_describe_one_hierarchy(self) -> None:
        self.assertIn("- Statement:", self.text("templates/chronicle/THESIS.md"))
        modules = self.text("templates/chronicle/MODULES.md")
        self.assertIn("a task never names a phase itself", modules)
        self.assertEqual(compiler.parse_modules(self.ROOT / "templates/chronicle/MODULES.md"), [])
        readme = self.text("templates/chronicle/README.md")
        for record in ("`THESIS.md` is the product thesis", "`MODULES.md` is module identity",
                       "`TASKS.md` is task identity, module,", "Give it a `Module:` from `MODULES.md`"):
            self.assertIn(record, readme)
        self.assertIn("each module in `MODULES.md` names its phase", self.text("templates/chronicle/PHASES.md"))
        self.assertIn("give it one `Module:` from `MODULES.md`", self.text("AGENTS.md"))
        self.assertIn("thesis → phase → module → task", self.text(".agents/skills/praukron/SKILL.md"))
        self.assertIn("THESIS PHASES MODULES TASKS", self.text("install.sh"))
        # Nothing still tells an agent that a task owns its phase.
        for name in ("AGENTS.md", ".agents/skills/praukron/SKILL.md", "templates/chronicle/README.md",
                     ".praukron/commands/praukron-work.md"):
            self.assertNotIn("give it a phase", self.text(name).lower(), name)

    def test_the_public_documents_state_the_hierarchy(self) -> None:
        spec = self.text("docs/SPEC.md")
        self.assertIn("**thesis → phase → module → task**", spec)
        self.assertIn("A task never owns a phase", spec)
        self.assertIn("| `MODULES.md` |", self.text("docs/PRODUCT-THESIS.md"))
        for name in ("docs/SPEC.md", "docs/PRODUCT-THESIS.md", "README.md"):
            self.assertNotRegex(self.text(name), r"\| `TASKS.md` \|[^|]*\btasks?,? (with )?phase\b", name)


class TestNoNetworkOrDependencies(unittest.TestCase):
    def test_runtime_imports_only_the_standard_library(self) -> None:
        import sysconfig

        stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
        package = Path(cli.__file__).resolve().parent
        offenders = []
        for name, module in list(sys.modules.items()):
            if not name.startswith("praukron"):
                continue
            for attribute in vars(module).values():
                origin = getattr(getattr(attribute, "__module__", None), "__str__", None)
                del origin
        for name in ("json", "argparse", "re", "pathlib", "dataclasses"):
            self.assertIn(name, sys.modules)
        for module_path in package.glob("*.py"):
            text = module_path.read_text(encoding="utf-8")
            for forbidden in ("import requests", "import yaml", "import jinja2", "pip install"):
                if forbidden in text:
                    offenders.append(f"{module_path.name}: {forbidden}")
        self.assertEqual(offenders, [])
        self.assertTrue(stdlib.is_dir())

    def test_runtime_names_utf8_for_every_text_file(self) -> None:
        # Without it Windows falls back to cp1252 and a Vietnamese response breaks compile.
        import ast

        offenders = []
        for module_path in Path(cli.__file__).resolve().parent.glob("*.py"):
            for node in ast.walk(ast.parse(module_path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and not any(k.arg == "encoding" for k in node.keywords)):
                    offenders.append(f"{module_path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


class TestArchive(FixtureCase):
    """A COMPLETE phase's closed work moves to archive/<phase>/ and stays authority (ADR-067)."""

    def setUp(self) -> None:
        super().setUp()
        self.rewrite("PHASES.md", "Status:\nACTIVE", "Status:\nCOMPLETE")
        self.authority = self.root / layout.AUTHORITY_DIR

    def close_all(self) -> None:
        for _ in range(2):
            self.rewrite("TASKS.md", "- Status: TODO", "- Status: DONE")
            self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`")

    def snapshot(self) -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}

    def test_closed_work_moves_verbatim_and_the_project_reads_the_same(self) -> None:
        from praukron import archive

        self.close_all()
        tasks_before = (self.authority / "TASKS.md").read_text(encoding="utf-8")
        before = archive._records(compiler.load(self.root))
        plan = archive.archive(self.root, "P1")
        self.assertEqual(plan.tasks, ["T-ONE", "T-TWO", "T-THREE"])
        self.assertEqual(plan.contracts, ["AC-T-ONE", "AC-T-TWO", "AC-T-THREE"])
        project = compiler.load(self.root)
        self.assertEqual(archive._records(project), before)
        self.assertEqual(project.task("T-ONE").source.file, "archive/P1/TASKS.md")
        self.assertEqual(project.contracts["AC-T-ONE"].source.file, "archive/P1/ACCEPTANCE.md")
        self.assertEqual(validate.errors(validate.check(project)), [])
        moved = (self.authority / "archive/P1/TASKS.md").read_text(encoding="utf-8")
        section = tasks_before[tasks_before.index("## T-TWO"):tasks_before.index("## T-THREE")].rstrip("\n")
        self.assertIn(section, moved)
        self.assertNotIn("## T-", (self.authority / "TASKS.md").read_text(encoding="utf-8"))
        # Inherited invariants never leave the working file.
        self.assertIn("### AC-GLOBAL-CARE", (self.authority / "ACCEPTANCE.md").read_text(encoding="utf-8"))

    def test_retrieve_and_old_pointers_reach_the_archive(self) -> None:
        from praukron import archive

        self.close_all()
        archive.archive(self.root, "P1")
        project = compiler.load(self.root)
        pack = retrieve.retrieve(project, analytics.report(project), self.root, "T-TWO")
        self.assertEqual(pack.items[0].source, "archive/P1/TASKS.md#T-TWO")
        self.assertIn("archive/P1/ACCEPTANCE.md#AC-T-TWO", [i.source for i in pack.items])
        self.assertIn("## T-ONE: Lay the foundation", retrieve.Chronicle(self.root).section("TASKS.md", "T-ONE"))

    def test_open_work_stays_and_only_a_complete_phase_moves(self) -> None:
        from praukron import archive

        plan = archive.plan(compiler.load(self.root), "P1", self.root)
        self.assertEqual(plan.tasks, ["T-ONE"])
        self.assertEqual(dict(plan.kept), {"T-TWO": "it is not DONE", "T-THREE": "it is not DONE"})
        self.rewrite("PHASES.md", "Status:\nCOMPLETE", "Status:\nACTIVE")
        with self.assertRaisesRegex(archive.ArchiveError, "only a COMPLETE phase"):
            archive.plan(compiler.load(self.root), "P1")
        with self.assertRaisesRegex(archive.ArchiveError, "not a phase"):
            archive.plan(compiler.load(self.root), "P9")

    def test_a_contract_holding_an_invariant_stays(self) -> None:
        from praukron import archive

        self.close_all()
        text = (self.authority / "ACCEPTANCE.md").read_text(encoding="utf-8")
        block = text[text.index("### AC-GLOBAL-CARE"):text.index("---")]
        # The invariant now sits at the end of AC-T-TWO's section.
        text = text.replace(block, "").replace("## AC-T-THREE", block + "## AC-T-THREE")
        (self.authority / "ACCEPTANCE.md").write_text(text, encoding="utf-8")
        plan = archive.plan(compiler.load(self.root), "P1", self.root)
        self.assertNotIn("AC-T-TWO", plan.contracts)
        self.assertIn("AC-T-THREE", plan.contracts)

    def test_a_contract_moves_after_its_last_working_task_moves(self) -> None:
        from praukron import archive

        phases = self.authority / "PHASES.md"
        phases.write_text(phases.read_text(encoding="utf-8").replace(
            "---\n\n# Gates", "## P2 — Follow-up\n\nOutcome:\nMove the shared work.\n\n"
            "Entry:\n- P1 complete.\n\nExit:\n- T-TWO complete.\n\nExit authority:\nT-TWO\n\n"
            "Status:\nACTIVE\n\n---\n\n# Gates"), encoding="utf-8")
        modules = self.authority / "MODULES.md"
        modules.write_text(modules.read_text(encoding="utf-8").replace(
            "## M-UPKEEP", "## M-FOLLOW-UP — Follow-up\n- Phase: P2\n- Outcome: The shared work moves.\n\n## M-UPKEEP"),
            encoding="utf-8")
        tasks = self.authority / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace(
            "- Module: M-FOUNDATION\n- Validation: UNTESTED\n- Dependencies: T-ONE\n- Owner: unassigned\n- AC: AC-T-TWO",
            "- Module: M-FOLLOW-UP\n- Validation: UNTESTED\n- Dependencies: T-ONE\n- Owner: unassigned\n- AC: AC-T-ONE"), encoding="utf-8")

        first = archive.archive(self.root, "P1")
        self.assertEqual(first.contracts, [])
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: DONE")
        phases.write_text(phases.read_text(encoding="utf-8").replace("Status:\nACTIVE", "Status:\nCOMPLETE"),
                          encoding="utf-8")
        before = archive._records(compiler.load(self.root))
        second = archive.archive(self.root, "P2")
        self.assertEqual(second.contracts, ["AC-T-ONE"])
        project = compiler.load(self.root)
        self.assertEqual(archive._records(project), before)
        self.assertEqual(project.contracts["AC-T-ONE"].source.file, "archive/P2/ACCEPTANCE.md")

    def test_dry_run_and_a_failed_check_leave_every_byte(self) -> None:
        from praukron import archive

        self.close_all()
        before = self.snapshot()
        plan = archive.archive(self.root, "P1", dry_run=True)
        self.assertGreater(plan.moved_bytes, 0)
        self.assertEqual(self.snapshot(), before)
        real = archive._records
        calls = []

        def drifting(project):
            calls.append(1)
            return real(project) if len(calls) == 1 else {"changed": True}

        with mock.patch.object(archive, "_records", drifting):
            with self.assertRaisesRegex(archive.ArchiveError, "nothing was moved"):
                archive.archive(self.root, "P1")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.authority / "archive").exists())

    def test_an_interrupted_archive_is_rolled_back_on_the_next_command(self) -> None:
        from praukron import archive

        self.close_all()
        before = self.snapshot()
        real = archive._replace
        calls = 0

        def interrupted(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit("simulated power loss")
            return real(path, data)

        with mock.patch.object(archive, "_replace", interrupted):
            with self.assertRaises(SystemExit):
                archive.archive(self.root, "P1")
        recovery = self.root / layout.RECOVERY_DIR
        self.assertTrue(list(recovery.glob("archive-*.json")))
        # Records hold whole chronicle files; the directory keeps itself out of Git.
        self.assertEqual((recovery / ".gitignore").read_text(encoding="utf-8"), "*\n")
        partial = compiler.load(self.root)
        self.assertIn("interrupted-archive", [f.code for f in validate.errors(validate.check(partial))])
        with self.assertRaisesRegex(archive.ArchiveError, "interrupted archive was rolled back"):
            archive.archive(self.root, "P1")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(recovery.glob("archive-*.json")), [])

    def test_validation_errors_refuse_the_archive(self) -> None:
        from praukron import archive

        self.close_all()
        self.rewrite("TASKS.md", "- Dependencies: T-ONE", "- Dependencies: T-GHOST")
        before = self.snapshot()
        with self.assertRaisesRegex(archive.ArchiveError, "fix them before archiving"):
            archive.archive(self.root, "P1")
        self.assertEqual(self.snapshot(), before)

    def test_a_contract_in_both_places_does_not_parse(self) -> None:
        target = self.authority / "archive/P1/ACCEPTANCE.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Archived\n\n## AC-T-ONE — Again\n\n- `AC-T-ONE-09` — Again. `TEST`\n", encoding="utf-8")
        with self.assertRaisesRegex(ParseError, "duplicate contract"):
            compiler.load(self.root)

    def test_the_cli(self) -> None:
        self.close_all()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["-C", str(self.root), "archive", "P1", "--json"]), 0)
        self.assertTrue(json.loads(buffer.getvalue())["applied"])
        self.assertTrue((self.root / layout.COMPILED_DIR / "dashboard.html").is_file())
        with redirect_stderr(io.StringIO()):
            self.rewrite("PHASES.md", "Status:\nCOMPLETE", "Status:\nACTIVE")
            self.assertEqual(cli.main(["-C", str(self.root), "archive", "P1"]), 1)


class TestJournalPointers(FixtureCase):
    def test_a_full_journal_heading_from_the_index_resolves_to_the_first_match(self) -> None:
        journal = self.root / layout.AUTHORITY_DIR / "JOURNAL.md"
        heading = "2026-10-12 — AuxVol live run 3"
        journal.write_text(
            f"# Journal\n\n## {heading}\nFirst entry.\n\n## {heading}\nSecond entry.\n",
            encoding="utf-8",
        )
        project = compiler.load(self.root)
        compiler.write(self.root, project)
        index_text = (self.root / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8")
        pointer = next(line.rsplit("#", 1)[1] for line in index_text.splitlines() if "JOURNAL.md#" in line)
        section = retrieve.Chronicle(self.root).section("JOURNAL.md", pointer)
        self.assertIn("First entry.", section)
        self.assertNotIn("Second entry.", section)


class TestOutputEncoding(FixtureCase):
    def test_records_print_as_utf8_through_a_cp1252_pipe(self) -> None:
        # A piped stdout on Windows is cp1252; retrieve prints records verbatim.
        handoff = self.root / layout.AUTHORITY_DIR / "HANDOFF.md"
        handoff.write_text("# Handoff\n\nTiếp tục kiểm thử bản cập nhật.\n", encoding="utf-8")
        raw = io.BytesIO()
        pipe = io.TextIOWrapper(raw, encoding="cp1252")
        with mock.patch.object(sys, "stdout", pipe):
            code = cli.main(["-C", str(self.root), "retrieve", "show", "the", "handoff"])
            pipe.flush()
        self.assertEqual(code, 0)
        self.assertIn("Tiếp tục kiểm thử bản cập nhật.", raw.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
