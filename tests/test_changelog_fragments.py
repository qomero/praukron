"""scripts/changelog.py: fragments assemble into CHANGELOG.md."""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "scripts", "changelog.py")
spec = importlib.util.spec_from_file_location("changelog_tool", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

BASE = (
    "# Changelog\n\nIntro.\n\n## Unreleased\n\n- Old bullet\n  continued.\n\n"
    "## 1.0.0 — 2026-01-01\n\n- First\n"
)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "changelog.d"))

    def write(self, rel, data):
        with open(os.path.join(self.root, rel), "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)

    def read(self, rel):
        with open(os.path.join(self.root, rel), "rb") as f:
            return f.read()

    def frags(self):
        return sorted(os.listdir(os.path.join(self.root, "changelog.d")))


class Assemble(Fixture):
    def test_orders_by_task_id_and_removes_fragments(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-B-02.md", "- Second\n")
        self.write("changelog.d/T-A-01.md", "- First\n  more\n")
        self.write("changelog.d/README.md", "# not assembled\n")
        done = tool.assemble("2.1.0", "2026-11-01", self.root)
        self.assertEqual(done, ["T-A-01.md", "T-B-02.md"])
        self.assertEqual(self.frags(), ["README.md"])
        self.assertEqual(
            self.read("CHANGELOG.md").decode(),
            "# Changelog\n\nIntro.\n\n## 2.1.0 — 2026-11-01\n\n- Old bullet\n  continued.\n"
            "- First\n  more\n- Second\n\n## 1.0.0 — 2026-01-01\n\n- First\n",
        )

    def test_crlf_preserved(self):
        self.write("CHANGELOG.md", BASE.replace("\n", "\r\n"))
        self.write("changelog.d/T-A-01.md", "- New\n")  # LF fragment
        tool.assemble("2.1.0", "2026-11-01", self.root)
        out = self.read("CHANGELOG.md")
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))
        self.assertIn(b"- New\r\n\r\n## 1.0.0", out)

    def test_lf_stays_lf_for_crlf_fragment(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-A-01.md", "- New\r\n")
        tool.assemble("2.1.0", "2026-11-01", self.root)
        self.assertNotIn(b"\r", self.read("CHANGELOG.md"))

    def test_existing_text_kept_byte_for_byte(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-A-01.md", "- New\n")
        tool.assemble("2.1.0", "2026-11-01", self.root)
        out = self.read("CHANGELOG.md").decode()
        self.assertTrue(out.startswith("# Changelog\n\nIntro.\n\n## 2.1.0"))
        self.assertIn("\n\n- Old bullet\n  continued.\n- New\n\n", out)
        self.assertTrue(out.endswith("## 1.0.0 — 2026-01-01\n\n- First\n"))

    def test_creates_section_without_unreleased(self):
        self.write("CHANGELOG.md", BASE.replace("Unreleased", "0.9.0 — 2025-12-01"))
        self.write("changelog.d/T-A-01.md", "- New\n")
        tool.assemble("2.1.0", "2026-11-01", self.root)
        out = self.read("CHANGELOG.md").decode()
        self.assertIn("Intro.\n\n## 2.1.0 — 2026-11-01\n\n- New\n\n## 0.9.0", out)

    def test_empty_unreleased(self):
        self.write("CHANGELOG.md", "# C\n\n## Unreleased\n\n## 1.0.0\n\n- x\n")
        self.write("changelog.d/T-A-01.md", "- New\n")
        tool.assemble("2.0.0", "d", self.root)
        self.assertEqual(
            self.read("CHANGELOG.md").decode(),
            "# C\n\n## 2.0.0 — d\n\n- New\n\n## 1.0.0\n\n- x\n",
        )

    def test_inserts_before_subsections(self):
        self.write("CHANGELOG.md", "# C\n\n## Unreleased\n\n- a\n\n### Notes\n\ntext\n")
        self.write("changelog.d/T-A-01.md", "- New\n")
        tool.assemble("2.0.0", "d", self.root)
        self.assertEqual(
            self.read("CHANGELOG.md").decode(),
            "# C\n\n## 2.0.0 — d\n\n- a\n- New\n\n### Notes\n\ntext\n",
        )


class Refusal(Fixture):
    def check_refused(self, bad):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-A-01.md", "- Good\n")
        self.write("changelog.d/T-B-02.md", bad)
        before = {n: self.read("changelog.d/" + n) for n in self.frags()}
        with self.assertRaises(tool.Refusal):
            tool.assemble("2.1.0", "2026-11-01", self.root)
        self.assertEqual(self.read("CHANGELOG.md").decode(), BASE)
        self.assertEqual(
            {n: self.read("changelog.d/" + n) for n in self.frags()}, before
        )

    def test_no_bullet(self):
        self.check_refused("just prose\n")

    def test_empty(self):
        self.check_refused("\n")

    def test_heading_inside(self):
        self.check_refused("- ok\n\n## Sneaky\n")

    def test_cli_exit_code(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-B-02.md", "prose\n")
        self.assertEqual(
            tool.main(["assemble", "2.1.0", "d", "--root", self.root]), 1
        )
        self.assertEqual(self.read("CHANGELOG.md").decode(), BASE)


class Idempotent(Fixture):
    def test_no_fragments_changes_nothing(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/README.md", "# r\n")
        self.assertEqual(tool.assemble("2.1.0", "d", self.root), [])
        self.assertEqual(self.read("CHANGELOG.md").decode(), BASE)

    def test_rerun_after_assembly(self):
        self.write("CHANGELOG.md", BASE)
        self.write("changelog.d/T-A-01.md", "- New\n")
        tool.assemble("2.1.0", "d", self.root)
        once = self.read("CHANGELOG.md")
        tool.assemble("2.1.0", "d", self.root)
        self.assertEqual(self.read("CHANGELOG.md"), once)


class Docs(unittest.TestCase):
    def test_contributing_describes_fragments(self):
        with open(os.path.join(REPO, "CONTRIBUTING.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("changelog.d/", text)
        self.assertIn("scripts/changelog.py assemble", text)
        self.assertTrue(os.path.exists(os.path.join(REPO, "changelog.d", "README.md")))


if __name__ == "__main__":
    unittest.main()
