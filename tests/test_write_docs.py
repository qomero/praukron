"""Agent-facing text and documentation of the structured writes (AC-T-WRITE-DOCS-01)."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import cli

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ("journal", "append"), ("criterion", "set"), ("task", "add"), ("contract", "add"), ("task", "update"),
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


class Documented(unittest.TestCase):
    def read(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_every_document_names_the_five_commands(self):
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
