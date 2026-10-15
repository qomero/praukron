"""The archive documentation matches the code (AC-T-ARCH-DOCS-01-01, -03)."""

from __future__ import annotations

import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from praukron import cli, model  # noqa: E402


def doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "praukron").glob("*.py"))


class Installer(unittest.TestCase):
    def test_install_sh_lists_every_runtime_module(self):
        listed = set(re.findall(r"[a-z_]+", re.search(r"for module in (.*?); do", doc("install.sh"), re.S).group(1)))
        modules = {p.stem for p in (ROOT / "src" / "praukron").glob("*.py")}
        self.assertEqual(modules - listed, set())


class ArchiveDocs(unittest.TestCase):
    def test_help_names_the_documented_actions(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["archive", "--help"])
        text = out.getvalue()
        for word in ("plan", "apply", "restore", "--plan", "--dry-run", "--json"):
            self.assertIn(word, text)

    def test_owner_docs_document_every_action_and_option(self):
        for name in ("docs/GUIDE.md", "docs/SPEC.md"):
            text = doc(name)
            for phrase in ("archive plan", "archive apply", "archive restore", "--plan", "--dry-run", "digest",
                           "P-NONE", "RESPONSES.md", "unstaged", "staged", "committed") if name.endswith("GUIDE.md") \
                    else ("archive plan", "archive apply", "archive restore", "--plan", "digest", "P-NONE",
                          "RESPONSES.md", "ADR/", "nogit", "git revert"):
                self.assertTrue(phrase in text, f"{name} lacks {phrase!r}")
        self.assertIn("archive plan", doc("README.md"))
        self.assertIn("archive restore", doc("README.md"))

    def test_error_codes_the_docs_name_exist_in_the_code(self):
        code = source()
        text = doc("docs/GUIDE.md") + doc("docs/SPEC.md")
        named = set(re.findall(r"`((?:stale-plan|missing-plan|dirty-chronicle|id-collision|unknown-archive|"
                               r"unknown-phase|incomplete-phase|archived-record|refresh-failed))`", text))
        self.assertGreaterEqual(len(named), 8)
        for name in named:
            self.assertIn(f'"{name}"', code, name)

    def test_eligibility_statuses_match_the_code(self):
        guide = doc("docs/GUIDE.md")
        for status in model.DEBT_CLOSED:
            self.assertIn(f"`{status}`", guide)
        code = source()
        for status in ("CONFIRMED", "REJECTED", "REVISED", "WITHDRAWN"):
            self.assertIn(f'"{status}"', code)
            self.assertIn(f"`{status}`", guide)
        self.assertIn("Domain", guide)
        self.assertIn("never changes", guide)

    def test_recovery_commands_documented_are_the_ones_printed(self):
        code = source()
        guide = doc("docs/GUIDE.md")
        for fragment in ("restore --source=HEAD", "--staged", "rm", "--cached", "revert", ".praukron/backup",
                         "manifest.json"):
            self.assertIn(fragment.split()[0], guide)
        for fragment in ('"restore", "--source=HEAD"', '"--staged"', '"--cached"', '"revert"', "manifest.json"):
            self.assertIn(fragment, code)

    def test_git_recovery_names_the_exact_result_fields_and_attributes(self):
        guide, spec = doc("docs/GUIDE.md"), doc("docs/SPEC.md")
        for fragment in ("recovery.changedTrackedFiles", "recovery.createdFiles", "never use"):
            self.assertTrue(fragment in guide, fragment)
        for text in (guide, spec):
            self.assertTrue("`crlf`" in text and "`eol`" in text)
        code = source()
        for key in ('"changedTrackedFiles"', '"createdFiles"', b"crlf".decode()):
            self.assertIn(key, code)
        self.assertNotIn("eligibility is checked once, when `apply` or `restore`", guide.lower())

    def test_agents_block_states_owner_only_and_immutability_briefly(self):
        agents = doc("AGENTS.md")
        sentence = re.search(r"`archive plan\|apply\|restore <phase>`.*?\); and", agents, re.S).group(0)
        for phrase in ("owner", "verbatim", "stay authority", "RESPONSES.md", "ADRs"):
            self.assertTrue(phrase in sentence, phrase)
        self.assertLess(len(sentence) // 4, 100)  # about 100 tokens, as the task allows

    def test_thesis_moves_archive_lifecycle_to_implemented(self):
        thesis = doc("docs/PRODUCT-THESIS.md")
        bounded = thesis[thesis.index("## Bounded cost"):thesis.index("## Making disagreement decidable")]
        self.assertIn("**Implemented.**", bounded)
        self.assertNotIn("Designed, not built", bounded)
        self.assertIn("archive restore", bounded)
        self.assertIn("journal entries", bounded)

    def test_pending_verify_blocks_are_balanced_and_easy_to_find(self):
        for name in ("docs/GUIDE.md", "docs/SPEC.md"):
            text = doc(name)
            self.assertEqual(text.count("T-ARCH-VERIFY-01-PENDING start"), 1, name)
            self.assertEqual(text.count("T-ARCH-VERIFY-01-PENDING end"), 1, name)
            self.assertLess(text.index("PENDING start"), text.index("PENDING end"), name)


if __name__ == "__main__":
    unittest.main()
