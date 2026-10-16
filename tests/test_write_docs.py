"""Agent-facing text and documentation of the structured writes (AC-T-WRITE-DOCS-01)."""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import cli, mutate

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ("journal", "append"), ("criterion", "set"), ("task", "add"), ("contract", "add"), ("task", "update"),
    ("acr", "add"), ("batch", "apply"),
)
PARTS = ("WHEN:", "WHEN NOT:", "RETURNS:", "EXAMPLE:")


def help_text(*command: str) -> str:
    out = io.StringIO()
    with redirect_stdout(out), self_exit():
        cli.main([*command, "--help"])
    return out.getvalue()


class self_exit:
    """Swallow argparse's SystemExit(0) after --help."""

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        return kind is SystemExit and value.code == 0


class AgentText(unittest.TestCase):
    def test_help_has_when_when_not_returns_and_example(self):
        for command in COMMANDS:
            text = help_text(*command)
            positions = [text.find(part) for part in PARTS]
            self.assertNotIn(-1, positions, f"{' '.join(command)}: {positions}")
            self.assertEqual(positions, sorted(positions), " ".join(command))

    def test_help_never_calls_a_redirect_blocked(self):
        for command in COMMANDS:
            self.assertNotIn("blocked", help_text(*command).lower(), " ".join(command))

    def test_example_names_the_command(self):
        for command in COMMANDS:
            example = next(line for line in help_text(*command).splitlines() if line.startswith("EXAMPLE:"))
            self.assertIn("praukron " + " ".join(command), example)


def schema_output(*command: str) -> dict:
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main([*command, "--schema"])
    return json.loads(out.getvalue())


# Codes whose message already names the next step where it is raised.
INLINE_STEP = {"stale-revision", "frozen-contract", "owner-held", "owner-required", "unresolved-acceptance",
               "evidence-required", "duplicate-heading", "locked", "interrupted-write", "missing-input",
               "duplicate-request"}


class RefusalText(unittest.TestCase):
    def test_schema_descriptions_follow_the_same_standard(self):
        for command in COMMANDS:
            description = schema_output(*command)["schema"]["description"]
            positions = [description.find(part) for part in PARTS]
            self.assertNotIn(-1, positions, f"{' '.join(command)}: {positions}")
            self.assertEqual(positions, sorted(positions), " ".join(command))
            self.assertNotIn("blocked", description.lower(), " ".join(command))

    def test_task_add_schema_keeps_its_payload_rule(self):
        self.assertIn("exactly one of contract", schema_output("task", "add")["schema"]["description"])

    def test_every_refusal_code_has_a_next_step(self):
        codes = set()
        for name in ("mutate", "journal", "criterion", "task", "contract", "task_update", "acr", "cli"):
            source = (ROOT / "src" / "praukron" / f"{name}.py").read_text(encoding="utf-8")
            codes |= set(re.findall(r'WriteError\(\s*"([a-z-]+)"', source))
        missing = codes - set(mutate.NEXT_STEP) - INLINE_STEP
        self.assertEqual(missing, set())

    def test_refusal_message_ends_with_the_step(self):
        for code, step in mutate.NEXT_STEP.items():
            message = mutate.refusal("x.y", mutate.WriteError(code, "the rule"))["error"]["message"]
            self.assertEqual(message, f"the rule; {step}", code)

    def test_invariant_criterion_names_the_authorized_route(self):
        message = mutate.refusal("criterion.set", mutate.WriteError("invariant-criterion", "no"))["error"]["message"]
        self.assertIn("Acceptance Change Request", message)

    def test_inline_steps_really_are_inline(self):
        for code in INLINE_STEP - {"missing-input"}:
            self.assertNotIn(code, mutate.NEXT_STEP)


class Documented(unittest.TestCase):
    def read(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_every_document_names_the_structured_commands(self):
        for name in ("AGENTS.md", "docs/SPEC.md", "docs/GUIDE.md", "README.md"):
            text = self.read(name)
            for command in COMMANDS:
                self.assertIn("praukron " + " ".join(command), text, f"{name}: {' '.join(command)}")

    def test_agents_block_routes_writes_through_commands(self):
        text = self.read("AGENTS.md")
        self.assertNotIn("Nothing else in the chronicle is written by the tool", text)
        self.assertIn("instead of editing", text)

    def test_agent_commands_tell_agents_to_use_the_writes(self):
        for name in ("praukron-work.md", "praukron-checkpoint.md"):
            text = self.read(f".praukron/commands/{name}")
            self.assertIn("task update", text, name)
            self.assertIn("instead of editing" if name.endswith("work.md") else "journal append", text, name)

    def test_spec_read_order_starts_with_the_index(self):
        spec = self.read("docs/SPEC.md")
        start = spec.index("### 4.1") if "### 4.1" in spec else spec.index("4.1")
        self.assertLess(spec.index("INDEX.md", start), spec.index("TASK_GRAPH", start))


if __name__ == "__main__":
    unittest.main()
