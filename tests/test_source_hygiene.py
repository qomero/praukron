"""Every runtime module compiles cleanly under the newest Python rules.

An invalid escape such as "\\{" inside a non-raw string is only a
SyntaxWarning today and becomes an error in a later Python, so it fails here."""

from __future__ import annotations

import unittest
import warnings
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "praukron"


class SourceHygieneTest(unittest.TestCase):
    def test_every_module_compiles_without_syntax_warnings(self) -> None:
        for path in sorted(SOURCE.glob("*.py")):
            with self.subTest(module=path.name), warnings.catch_warnings():
                warnings.simplefilter("error", SyntaxWarning)
                compile(path.read_bytes(), str(path), "exec")


if __name__ == "__main__":
    unittest.main()
