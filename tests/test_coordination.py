"""Coordination conventions and task briefs in the chronicle (ADR-075, AC-T-COORD-BRIEF-01)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, archive, cli, compile as compiler, layout, retrieve, validate  # noqa: E402
from praukron.parse import ParseError  # noqa: E402
from test_archive_plan import ArchivePlanCase  # noqa: E402
from test_praukron import FixtureCase, build_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

COORDINATION = """# Coordination

Intro prose is not a record.

## CV-01: Worker rules
- Status: ACTIVE
- Roles: builder

Run the whole suite before opening a pull request.
- Status: this list item is prose, not a field.

## CV-02: Reviewer rules
- Status: ACTIVE
- Roles: reviewer

Classify every finding; do not merge.

## CV-03: Launch recipe
- Status: ACTIVE
- Roles: coordinator

Start one session per worktree.

## CV-04: Unrelated
- Status: ACTIVE

Never shown for T-TWO.

## BR-01: Build on it
- Task: T-TWO
- Status: ACTIVE
- Conventions: CV-01, CV-02, CV-03
- Branch: build-on-it
- Review: round 2, changes requested

Keep the old entry point working.
"""


def _flat(text: str) -> str:
    return " ".join(text.split())


class CoordinationCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / layout.AUTHORITY_DIR / "COORDINATION.md"
        self.write(COORDINATION)

    def write(self, text: str, newline: str = "\n") -> None:
        self.path.write_bytes(text.replace("\n", newline).encode("utf-8"))
        self.project = compiler.load(self.root)

    def run_cli(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-C", str(self.root), *args])
        return code, out.getvalue() + err.getvalue()


class TestParse(CoordinationCase):
    def test_records_parse_with_fields_text_and_source(self) -> None:
        self.assertEqual([c.id for c in self.project.conventions], ["CV-01", "CV-02", "CV-03", "CV-04"])
        worker = self.project.convention("CV-01")
        self.assertEqual((worker.status, worker.roles), ("ACTIVE", ["builder"]))
        self.assertEqual(self.project.convention("CV-04").roles, [])
        # Fields are the block under the heading; a list in the notes is prose.
        self.assertIn("- Status: this list item is prose", worker.text)
        self.assertEqual(worker.source.file, "COORDINATION.md")
        brief = self.project.brief_for("T-TWO")
        self.assertEqual((brief.id, brief.task, brief.status), ("BR-01", "T-TWO", "ACTIVE"))
        self.assertEqual(brief.conventions, ["CV-01", "CV-02", "CV-03"])
        self.assertTrue(brief.text.startswith("- Task: T-TWO"))
        self.assertTrue(brief.text.endswith("Keep the old entry point working."))

    def test_crlf_reads_the_same(self) -> None:
        lf = [(c.id, c.text) for c in self.project.conventions]
        self.write(COORDINATION, "\r\n")
        self.assertEqual([(c.id, c.text) for c in self.project.conventions], lf)
        self.assertEqual(self.project.brief_for("T-TWO").conventions, ["CV-01", "CV-02", "CV-03"])
        self.assertNotIn("\r", self.project.brief_for("T-TWO").text)

    def test_malformed_heading_or_missing_field_fails_validate(self) -> None:
        for text, needle in (
            ("# C\n\n## Notes about briefs\n- Status: ACTIVE\n", "coordination heading"),
            ("# C\n\n## CV-09: No status\n\nprose\n", "'Status'"),
            ("# C\n\n## BR-09: No task\n- Status: ACTIVE\n", "'Task'"),
        ):
            with self.subTest(needle=needle):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ParseError) as raised:
                    compiler.load(self.root)
                self.assertIn(needle, str(raised.exception))
                code, output = self.run_cli("validate")
                self.assertNotEqual(code, 0)
                self.assertIn("COORDINATION.md", output)


class TestValidate(CoordinationCase):
    def findings(self) -> dict[str, list]:
        found: dict[str, list] = {}
        for f in validate.coordination_findings(self.project):
            found.setdefault(f.code, []).append(f)
        return found

    def test_consistent_records_are_clean(self) -> None:
        self.assertEqual(self.findings(), {})
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_dangling_and_malformed_records_are_reported(self) -> None:
        self.write(COORDINATION + """
## CV-01: Duplicate
- Status: ACTIVE

## CV-05: Odd
- Status: DRAFT
- Roles: builder, janitor

## CV-06: Old
- Status: RETIRED

## BR-02: Ghost task
- Task: T-GHOST
- Status: OPEN
- Conventions: CV-99

## BR-03: Second active brief
- Task: T-TWO
- Status: ACTIVE
- Conventions: CV-06

## BR-04: Closed brief may name a retired convention
- Task: T-THREE
- Status: CLOSED
- Conventions: CV-06
""")
        found = self.findings()
        expected = {
            "duplicate-coordination-record": "COORDINATION.md#CV-01",
            "invalid-convention-status": "COORDINATION.md#CV-05",
            "invalid-convention-role": "COORDINATION.md#CV-05",
            "unknown-brief-task": "COORDINATION.md#BR-02",
            "invalid-brief-status": "COORDINATION.md#BR-02",
            "unknown-brief-convention": "COORDINATION.md#BR-02",
            "multiple-active-briefs": "COORDINATION.md#BR-03",
            "retired-brief-convention": "COORDINATION.md#BR-03",
        }
        for code, where in expected.items():
            with self.subTest(code=code):
                self.assertEqual([f.where for f in found[code]], [where])
                self.assertEqual(found[code][0].severity, "error")
        self.assertEqual(set(found), set(expected))
        code, output = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("unknown-brief-task", output)

    def test_active_brief_on_done_task_and_long_records_warn(self) -> None:
        self.write(COORDINATION + "\n## BR-02: Finished\n- Task: T-ONE\n- Status: ACTIVE\n\n"
                   + "word " * 1700 + "\n")
        found = self.findings()
        self.assertEqual(found["active-brief-done-task"][0].severity, "warning")
        self.assertEqual(found["long-coordination-record"][0].where, "COORDINATION.md#BR-02")
        self.assertEqual(validate.errors(validate.check(self.project)), [])


class TestPacket(CoordinationCase):
    def test_context_carries_the_brief_and_the_conventions_for_the_role(self) -> None:
        packet = analytics.context(self.project, "T-TWO")
        brief = packet["brief"]
        self.assertEqual(brief["id"], "BR-01")
        self.assertEqual(brief["authority"], "COORDINATION.md#BR-01")
        self.assertIn("Branch: build-on-it", brief["text"])
        self.assertIn("Keep the old entry point working.", brief["text"])
        # A builder gets the builder's convention; the coordinator's and the
        # reviewer's stay out, and an unnamed convention is never added.
        self.assertEqual([c["id"] for c in packet["conventions"]], ["CV-01"])
        self.assertEqual(packet["conventions"][0]["authority"], "COORDINATION.md#CV-01")
        self.assertIn("Run the whole suite", packet["conventions"][0]["text"])
        self.assertIn("COORDINATION.md#BR-01", packet["authority"]["read"])
        self.assertIn("COORDINATION.md#CV-01", packet["authority"]["read"])
        reviewer = analytics.context(self.project, "T-TWO", "reviewer")
        self.assertEqual([c["id"] for c in reviewer["conventions"]], ["CV-02"])

    def test_cli_context_alone_resumes_a_relaunched_agent(self) -> None:
        code, output = self.run_cli("context", "T-TWO")
        self.assertEqual(code, 0, output)
        packet = json.loads(output)
        # Contract, brief and conventions all come from the chronicle.
        self.assertTrue(packet["acceptance"])
        self.assertEqual(packet["brief"]["task"], "T-TWO")
        self.assertIn("round 2, changes requested", packet["brief"]["text"])
        self.assertEqual(packet["conventions"][0]["id"], "CV-01")

    def test_closed_brief_is_shown_when_no_brief_is_active_and_retired_conventions_are_not(self) -> None:
        self.write(COORDINATION.replace("- Status: ACTIVE\n- Conventions", "- Status: CLOSED\n- Conventions")
                   .replace("## CV-01: Worker rules\n- Status: ACTIVE", "## CV-01: Worker rules\n- Status: RETIRED"))
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["brief"]["status"], "CLOSED")
        self.assertEqual(packet["conventions"], [])

    def test_no_file_means_no_brief_and_no_error(self) -> None:
        self.path.unlink()
        self.project = compiler.load(self.root)
        packet = analytics.context(self.project, "T-TWO")
        self.assertIsNone(packet["brief"])
        self.assertEqual(packet["conventions"], [])
        self.assertEqual(validate.coordination_findings(self.project), [])

    def test_retrieve_task_returns_brief_and_conventions_with_their_sources(self) -> None:
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "T-TWO")
        items = [(i.kind, i.id, i.source) for i in pack.items]
        self.assertIn(("BRIEF", "BR-01", "COORDINATION.md#BR-01"), items)
        for convention in ("CV-01", "CV-02", "CV-03"):
            self.assertIn(("CONVENTION", convention, f"COORDINATION.md#{convention}"), items)
        self.assertNotIn("CV-04", [i.id for i in pack.items])
        brief = next(i for i in pack.items if i.id == "BR-01")
        self.assertTrue(brief.text.startswith("## BR-01: Build on it"))
        self.assertIn("BRIEF · BR-01 — COORDINATION.md#BR-01", pack.render())

    def test_retrieve_names_one_record(self) -> None:
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "CV-04")
        self.assertEqual([(i.kind, i.id) for i in pack.items], [("CONVENTION", "CV-04")])
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "BR-01")
        self.assertEqual([i.id for i in pack.items][:2], ["BR-01", "T-TWO"])

    def test_over_budget_records_are_listed_with_a_follow_up(self) -> None:
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.root, "T-TWO", budget=1)
        omitted = {o.id: o.follow_up() for o in pack.omitted}
        self.assertEqual(omitted["BR-01"], "praukron retrieve BR-01")


class TestArchive(ArchivePlanCase):
    def brief(self, status: str) -> None:
        (self.authority / "COORDINATION.md").write_text(
            "# Coordination\n\n## CV-01: Rules\n- Status: ACTIVE\n\n"
            f"## BR-01: Foundation\n- Task: T-ONE\n- Status: {status}\n- Conventions: CV-01\n",
            encoding="utf-8")

    def test_an_active_brief_keeps_its_task(self) -> None:
        self.brief("ACTIVE")
        candidates = {c.id: c for c in archive.preview(self.root, "P1").candidates}
        self.assertFalse(candidates["T-ONE"].moves)
        self.assertEqual(candidates["T-ONE"].reason, "brief BR-01 is ACTIVE")

    def test_coordination_never_moves_and_a_closed_brief_still_resolves(self) -> None:
        self.brief("CLOSED")
        before = (self.authority / "COORDINATION.md").read_bytes()
        plan = archive.preview(self.root, "P1")
        self.assertIn("T-ONE", plan.tasks)
        code, result = self.run_cli("apply", "P1", "--plan", plan.digest)
        self.assertEqual(code, 0, result)
        self.assertEqual((self.authority / "COORDINATION.md").read_bytes(), before)
        self.assertFalse(any(p.name == "COORDINATION.md" for p in (self.authority / "archive").rglob("*")))
        project = compiler.load(self.root)
        self.assertEqual(project.task("T-ONE").source.file, "archive/P1/TASKS.md")
        self.assertEqual(validate.errors(validate.check(project)), [])
        self.assertEqual(analytics.context(project, "T-ONE")["brief"]["id"], "BR-01")


class TestUpgradeAndGuidance(unittest.TestCase):
    def test_the_template_is_valid_and_changes_nothing(self) -> None:
        outputs = []
        for with_template in (False, True):
            directory = Path(tempfile.mkdtemp(prefix="praukron-coord-"))
            self.addCleanup(shutil.rmtree, directory, True)
            build_fixture(directory)
            if with_template:
                shutil.copy(ROOT / "templates/chronicle/COORDINATION.md",
                            directory / layout.AUTHORITY_DIR / "COORDINATION.md")
            project = compiler.load(directory)
            self.assertEqual((project.conventions, project.briefs), ([], []))
            self.assertEqual(validate.errors(validate.check(project)), [])
            data = compiler.as_json(project)
            data["sources"] = None
            data["project"]["name"] = None
            outputs.append(json.dumps(data, sort_keys=True))
        self.assertEqual(outputs[0], outputs[1])

    def test_installer_ships_the_template(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertEqual(installer.count(" COORDINATION "), 2)
        self.assertIn("COORDINATION", (ROOT / "tests/install.sh").read_text(encoding="utf-8"))

    def test_docs_describe_the_records_and_the_coordinator(self) -> None:
        spec = _flat((ROOT / "docs/SPEC.md").read_text(encoding="utf-8"))
        readme = _flat((ROOT / "templates/chronicle/README.md").read_text(encoding="utf-8"))
        agents = _flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        template = _flat((ROOT / "templates/chronicle/COORDINATION.md").read_text(encoding="utf-8"))
        for text in (spec, readme, agents, template):
            for phrase in ("COORDINATION.md" if text is not template else "Coordination", "CV-", "BR-",
                           "context <task>"):
                self.assertIn(phrase, text.replace("`", ""))
        for code in ("unknown-brief-task", "unknown-brief-convention", "multiple-active-briefs",
                     "retired-brief-convention", "long-coordination-record"):
            self.assertIn(code, spec)
        self.assertIn("never archived", readme)
        self.assertIn("## Coordinator", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertIn("`brief`", _flat((ROOT / ".praukron/commands/praukron-work.md").read_text(encoding="utf-8")))

    def test_product_text_names_no_host_terminal_manager_or_model(self) -> None:
        for name in ("templates/chronicle/COORDINATION.md",):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            for word in ("claude", "codex", "orca", "tmux", "opus", "sonnet", "gpt"):
                self.assertNotIn(word, text, name)


if __name__ == "__main__":
    unittest.main()
