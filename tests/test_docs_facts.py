"""The published documents hold to the product they describe (T-DOCS-SYNC-02).

README, the guide and the dashboard document are read as text: every relative
link and image resolves, the parts the dashboard no longer has are not
described as present, what is not built is said to be not built, and the
command count README states is the number of commands the parser has.
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from praukron import cli  # noqa: E402

DOCUMENTS = ("README.md", "docs/GUIDE.md", "docs/dashboard-ux.md")
NUMBERS = {"twenty": 20, "twenty-one": 21, "twenty-two": 22, "twenty-three": 23, "twenty-four": 24,
           "twenty-five": 25, "twenty-six": 26, "twenty-seven": 27, "twenty-eight": 28,
           "twenty-nine": 29, "thirty": 30}


def text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def flat(name: str) -> str:
    return re.sub(r"\s+", " ", text(name))


def without_code(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.S)


class TestLinks(unittest.TestCase):
    def test_every_relative_link_and_image_resolves(self) -> None:
        for name in DOCUMENTS:
            base = (ROOT / name).parent
            body = without_code(text(name))
            targets = re.findall(r"\]\(([^)\s]+)\)", body) + re.findall(r'<img[^>]+src="([^"]+)"', body)
            if name == "README.md":
                self.assertTrue(targets, name)
            for target in targets:
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                path = unquote(target.split("#", 1)[0])
                with self.subTest(document=name, link=target):
                    self.assertTrue((base / path).exists(), f"{name} links to {target}, which does not exist")

    def test_every_image_has_alternative_text(self) -> None:
        for name in DOCUMENTS:
            for tag in re.findall(r"<img[^>]*>", text(name)):
                with self.subTest(document=name, tag=tag[:60]):
                    alt = re.search(r'alt="([^"]*)"', tag)
                    self.assertTrue(alt and len(alt.group(1).split()) >= 5, "an image needs alternative text")


class TestFacts(unittest.TestCase):
    def test_removed_parts_of_the_dashboard_are_not_described_as_present(self) -> None:
        for name in DOCUMENTS:
            body = flat(name)
            for removed in ("Complete dependency graph", "task dependency list", "Dependency ordering view",
                            "Task graph view", "the task dialog", "Work starts with a state board"):
                with self.subTest(document=name, removed=removed):
                    self.assertNotIn(removed, body)

    def test_the_shell_views_and_their_keys_are_described(self) -> None:
        for name in DOCUMENTS:
            body = flat(name)
            for phrase in ("Home", "Work", "Review", "Audit", "lanes", "force", "3D", "Waiting for you",
                           "`1`", "`2`", "`3`", "`p`", "`m`", "two axes", "Shift+W", "`f`", "upcoming",
                           "not recorded"):
                with self.subTest(document=name, phrase=phrase):
                    self.assertIn(phrase, body)

    def test_authority_delegation_and_impact_are_described(self) -> None:
        for name in ("README.md", "docs/GUIDE.md"):
            body = flat(name)
            for phrase in ("praukron authority", "VERIFY --delegate", "DELEGATE_VERIFIED",
                           "engine impact", "--base", "--head", "unresolved"):
                with self.subTest(document=name, phrase=phrase):
                    self.assertIn(phrase, body)

    def test_what_is_not_built_is_said_to_be_not_built(self) -> None:
        for name in DOCUMENTS:
            body = flat(name)
            with self.subTest(document=name):
                self.assertRegex(body, r"(?i)(view of the code|code view)[^.]*architecture layers[^.]*not built"
                                       r"|not built\.\*\* There is no view of the code and no view of architecture layers")

    def test_the_command_count_is_the_parsers(self) -> None:
        parser = cli.build_parser()
        commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        stated = re.search(r"compiler and its ([a-z-]+) commands", flat("README.md"))
        self.assertIsNotNone(stated)
        self.assertEqual(NUMBERS[stated.group(1)], len(commands.choices))


if __name__ == "__main__":
    unittest.main()
