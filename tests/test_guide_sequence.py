"""The GUIDE's structured-write sequence runs, in order, as printed (AC-T-WRITE-DOCS-01).

The starting fixture and every command are read from docs/GUIDE.md. Revisions
are content hashes and one depends on today's date (a claim writes `Claimed:`),
so each revision in the guide is mapped to the one the run produces; everything
else in the output must match the guide exactly.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from praukron import cli

ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"\b[0-9a-f]{16}\b")


def guide_section() -> str:
    text = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("### Writing records without editing files")
    return text[start:text.index("### CodeGraph (optional)")]


def fixture(directory: Path, blocks: list[str]) -> Path:
    module, task = blocks
    chronicle = directory / ".praukron" / "chronicle"
    for source in (ROOT / "templates" / "chronicle").rglob("*"):
        if source.is_file():
            target = chronicle / source.relative_to(ROOT / "templates" / "chronicle")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))

    def edit(name: str, change) -> None:
        path = chronicle / name
        path.write_bytes(change(path.read_bytes().decode("utf-8")).encode("utf-8"))

    edit("THESIS.md", lambda t: t.replace("- Statement:\n", "- Statement: A small example project.\n"))
    edit("MODULES.md", lambda t: t.replace("No modules yet. Create the first module with the first phase.\n", "")
         + "\n" + module)
    edit("TASKS.md", lambda t: "# Tasks\n\n" + task)
    return directory


def run(root: Path, command: str, payload: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(payload)):
        code = cli.main(["-C", str(root), *command.split(), "--input", "-"])
    return code, out.getvalue().strip()


class GuideSequence(unittest.TestCase):
    def test_guide_examples_run_in_order_and_print_what_is_shown(self):
        section = guide_section()
        blocks = [b.rstrip("\n") + "\n" for b in re.findall(r"```text\n(.*?)```", section, re.S)]
        self.assertEqual(len(blocks), 2, "the guide states a module and a task as the starting point")
        steps = re.findall(r"^\$ echo '(.*)' \| praukron ((?:\w+) (?:\w+)) --input -\n(.*)$", section, re.M)
        self.assertGreaterEqual(len(steps), 11)
        scratch = Path(tempfile.mkdtemp(prefix="praukron-guide-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        root = fixture(scratch, blocks)
        mapping: dict[str, str] = {}
        for payload, command, shown in steps:
            actual_payload = HEX.sub(lambda m: mapping.get(m.group(0), m.group(0)), payload)
            code, out = run(root, command, actual_payload)
            self.assertEqual(code == 0, json.loads(shown)["ok"], f"{command}: {out}")
            doc_hex, real_hex = HEX.findall(shown), HEX.findall(out)
            self.assertEqual(len(doc_hex), len(real_hex), f"{command}: {out}")
            for doc, real in zip(doc_hex, real_hex):
                if doc != "0" * 16:
                    mapping.setdefault(doc, real)
            expected = HEX.sub(lambda m: mapping.get(m.group(0), m.group(0)), shown)
            self.assertEqual(out, expected, command)

    def test_the_fixture_is_missing_only_its_contract(self):
        section = guide_section()
        blocks = [b.rstrip("\n") + "\n" for b in re.findall(r"```text\n(.*?)```", section, re.S)]
        scratch = Path(tempfile.mkdtemp(prefix="praukron-guide-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        root = fixture(scratch, blocks)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["-C", str(root), "validate"])
        self.assertEqual(code, 1)
        self.assertIn("unknown-contract", err.getvalue())
        self.assertEqual(err.getvalue().count("error:"), 1)


if __name__ == "__main__":
    unittest.main()
