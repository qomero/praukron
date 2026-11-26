"""The installed guidance on a task's files and symbols (AC-T-CIL-MAP-05)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- project-praukron:start -->", "<!-- project-praukron:end -->"
# The Praukron block of AGENTS.md on main before T-CIL-MAP-05 (989377c), LF-normalized,
# and the growth this task allows it (AC-T-CIL-MAP-05-04).
BLOCK_BASE_BYTES, BLOCK_BUDGET = 13106, 500


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def block() -> str:
    text = read("AGENTS.md").replace("\r\n", "\n")
    return text[text.index(START):text.index(END) + len(END)]


def flat(text: str) -> str:
    """Wrapped prose as one line, so a phrase may span a line break."""
    return re.sub(r"\s+", " ", text)


# Every surface an agent reads for its work, and what each must say.
SURFACES = {
    "AGENTS.md": block,
    ".praukron/commands/praukron-work.md": lambda: read(".praukron/commands/praukron-work.md"),
    ".praukron/commands/praukron-checkpoint.md": lambda: read(".praukron/commands/praukron-checkpoint.md"),
    ".agents/skills/praukron/SKILL.md": lambda: read(".agents/skills/praukron/SKILL.md"),
    "templates/chronicle/README.md": lambda: read("templates/chronicle/README.md"),
}


class FilesAndSymbolsGuidance(unittest.TestCase):
    """AC-T-CIL-MAP-05-01: what the tool records, and what the agent records with `ref add`."""

    def test_every_surface_says_capture_is_automatic_and_names_ref_add(self):
        for name, text in SURFACES.items():
            with self.subTest(surface=name):
                body = flat(text())
                self.assertIn("ref add", body)
                self.assertIn("`DONE`", body)
                self.assertIn("Python symbols", body)

    def test_agent_workflows_say_what_the_agent_records_and_when(self):
        for name in ("AGENTS.md", ".praukron/commands/praukron-work.md",
                     ".praukron/commands/praukron-checkpoint.md", ".agents/skills/praukron/SKILL.md"):
            with self.subTest(surface=name):
                body = flat(SURFACES[name]())
                # The reply's capture member, read and reported when it is skipped.
                self.assertIn("`capture`", body)
                self.assertRegex(body, r"skip")
                # What the tool cannot know: other languages' symbols, files related without a change.
                self.assertIn("`symbolsUnresolved`", body)
                self.assertRegex(body, r"read, verified or documented|reads, verifies or documents")
        for name in ("AGENTS.md", ".praukron/commands/praukron-work.md", ".agents/skills/praukron/SKILL.md"):
            with self.subTest(planned=name):
                body = flat(SURFACES[name]())
                self.assertRegex(body, r"planned")
                self.assertRegex(body, r"create or claim|at the start")

    def test_lookup_is_named_where_an_agent_starts_work(self):
        for name in ("AGENTS.md", ".praukron/commands/praukron-work.md", ".agents/skills/praukron/SKILL.md",
                     "templates/chronicle/README.md"):
            with self.subTest(surface=name):
                self.assertIn("praukron refs", flat(SURFACES[name]()))
        for name in ("AGENTS.md", ".praukron/commands/praukron-work.md"):
            with self.subTest(packet=name):
                self.assertIn("references", flat(SURFACES[name]()))

    def test_coordinator_capture_of_a_merged_task_is_named(self):
        for name in ("AGENTS.md", "templates/chronicle/README.md"):
            with self.subTest(surface=name):
                self.assertIn("scope capture <task> --base <rev> --head <rev>", flat(SURFACES[name]()))

    def test_chronicle_readme_lists_code_md_and_its_writes(self):
        readme = flat(read("templates/chronicle/README.md"))
        authority = readme[readme.index("## Authority"):readme.index("## Working rules")]
        self.assertIn("- `CODE.md` is the task-code references", authority)
        self.assertIn("`praukron ref add`", authority)
        self.assertIn("`praukron ref set`, never by hand", authority)
        self.assertIn("A task needs no references to be valid", authority)

    def test_installed_command_copies_still_defer_to_the_one_workflow(self):
        for host in (".claude", ".opencode"):
            for name in ("work", "checkpoint"):
                with self.subTest(host=host, workflow=name):
                    self.assertIn(f"Follow `.praukron/commands/praukron-{name}.md`.",
                                  read(f"{host}/commands/praukron-{name}.md"))


class BlockSize(unittest.TestCase):
    """AC-T-CIL-MAP-05-04: the guidance adds at most 500 bytes to the AGENTS.md block."""

    def test_block_grows_within_its_bound(self):
        growth = len(block().encode("utf-8")) - BLOCK_BASE_BYTES
        self.assertLessEqual(growth, BLOCK_BUDGET)


if __name__ == "__main__":
    unittest.main()
