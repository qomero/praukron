"""Journal references understand range shorthand (AC-T-JOURNAL-RANGE-01)."""

from __future__ import annotations

import io
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import archive, cli, compile as compiler, validate  # noqa: E402
from praukron.archive import RANGE_BOUND, journal_references  # noqa: E402
from test_archive_journal import JournalCase  # noqa: E402
from test_praukron import ADR  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CLOSED = "2026-02-01 — Closed a range"
PARTLY = "2026-02-02 — Range with open work"
BROKEN = "2026-02-03 — Broken range"


def ids(text: str) -> list[tuple[str, ...]]:
    return [reference.ids for reference in journal_references(text)]


def problems(text: str) -> list[str | None]:
    return [reference.problem for reference in journal_references(text)]


class TestRangeParsing(unittest.TestCase):
    def test_tail_full_and_adr_forms_span_each_id_and_keep_the_width(self):
        self.assertEqual(ids("T-P0-01..05"), [("T-P0-01", "T-P0-02", "T-P0-03", "T-P0-04", "T-P0-05")])
        self.assertEqual(ids("T-P0-01..T-P0-03"), [("T-P0-01", "T-P0-02", "T-P0-03")])
        self.assertEqual(ids("ADR-003..005"), [("ADR-003", "ADR-004", "ADR-005")])
        self.assertEqual(ids("ADR-3..5"), [("ADR-3", "ADR-4", "ADR-5")])
        self.assertEqual(ids("T-X-07..07"), [("T-X-07",)])
        self.assertEqual(problems("T-P0-01..05 ADR-003..005"), [None, None])

    def test_trailing_punctuation_is_not_part_of_the_range(self):
        self.assertEqual(ids("Closed T-1..T-3."), [("T-1", "T-2", "T-3")])
        self.assertEqual(ids("(T-1..3), then T-4..."), [("T-1", "T-2", "T-3"), ("T-4",)])
        self.assertEqual([r.text for r in journal_references("See T-1..T-3.")], ["T-1..T-3"])

    def test_plain_ids_are_unchanged_and_mentioned_once_in_order(self):
        refs = journal_references("T-ONE, AC-T-ONE-01 and T-ONE again; T-1.2 too.")
        self.assertEqual([(r.text, r.ids, r.problem) for r in refs],
                         [("T-ONE", ("T-ONE",), None), ("AC-T-ONE-01", ("AC-T-ONE-01",), None),
                          ("T-1.2", ("T-1.2",), None)])

    def test_ends_that_do_not_form_a_span_stand_for_no_id(self):
        cases = {
            "T-1..A-3": "different prefixes",
            "T-P0-01..T-P1-03": "different prefixes",
            "T-5..3": "lower end is above the upper",
            "T-ONE..T-TWO": "lower end has no numeric tail",
            "T-1..T-THREE": "upper end has no numeric tail",
            "T-1..3a": "upper end is not numeric",
            "T-01..5": "different widths",
            "T-9..10": "different widths",
            "T-1..3..5": "more than two ends",
            f"T-001..{RANGE_BOUND + 1:03d}": f"more than {RANGE_BOUND} ids",
        }
        for text, problem in cases.items():
            with self.subTest(text=text):
                (reference,) = journal_references(text)
                self.assertEqual((reference.text, reference.ids), (text, ()))
                self.assertIn(problem, reference.problem)

    def test_the_bound_is_inclusive(self):
        (reference,) = journal_references(f"T-001..{RANGE_BOUND:03d}")
        self.assertIsNone(reference.problem)
        self.assertEqual(len(reference.ids), RANGE_BOUND)


class RangeCase(JournalCase):
    def setUp(self):
        super().setUp()
        for task_id in ("T-R-01", "T-R-02", "T-R-03"):
            self.add_task(task_id, "M-FOUNDATION")
        self.add_task("T-R-04", "M-FOUNDATION", status="TODO")
        for number in (2, 3):
            (self.authority / "ADR" / f"ADR-00{number}.md").write_text(
                ADR.replace("ADR-001", f"ADR-00{number}"), encoding="utf-8")

    def references(self, body: str) -> list[str]:
        self.write_journal(f"# Journal\n\n## {CLOSED}\n{body}\n")
        return [f.message for f in validate.check(compiler.load(self.root))
                if f.code == "unknown-journal-reference"]


class TestRangeValidation(RangeCase):
    def test_a_fully_resolving_range_gives_no_warning(self):
        for body in ("Closed T-R-01..03.", "Closed T-R-01..T-R-03.", "Under ADR-001..003.",
                     "AC-T-R-01-01 and (T-R-02..03), then T-R-01..T-R-04."):
            with self.subTest(body=body):
                self.assertEqual(self.references(body), [])

    def test_only_members_that_do_not_exist_are_named(self):
        self.assertEqual(self.references("T-R-02..06 and ADR-002..004; T-R-05 alone."), [
            "names T-R-05 (in T-R-02..06), which does not exist",
            "names T-R-06 (in T-R-02..06), which does not exist",
            "names ADR-004 (in ADR-002..004), which does not exist",
        ])

    def test_a_plain_unknown_id_keeps_its_message(self):
        self.assertEqual(self.references("T-R-01 and T-MISSING."), ["names T-MISSING, which does not exist"])

    def test_an_invalid_range_is_one_warning_naming_the_range(self):
        for body, problem in (("T-R-01..A-R-03", "different prefixes"), ("T-R-03..01", "above the upper"),
                              ("T-R-01..0x", "not numeric"), ("T-R-01..3", "different widths"),
                              (f"T-R-001..{RANGE_BOUND + 5}", f"more than {RANGE_BOUND} ids")):
            with self.subTest(body=body):
                (message,) = self.references(f"Closed {body}.")
                self.assertTrue(message.startswith(f"names {body}, which is not a valid range: "), message)
                self.assertIn(problem, message)


class TestRangeReaders(RangeCase):
    def test_every_reader_expands_through_the_single_function(self):
        sources = {name: (REPO / "src/praukron" / name).read_text(encoding="utf-8")
                   for name in ("archive.py", "validate.py", "analytics.py", "retrieve.py")}
        archive_source = sources["archive.py"]
        uses = [m.start() for m in re.finditer(r"_NAMED\.", archive_source)]
        body = archive_source.index("def journal_references(")
        self.assertTrue(uses and all(start > body for start in uses))
        self.assertLess(archive_source.index("def _expand_range("), archive_source.index("class JournalRule"))
        self.assertIn("journal_references(text[start:end])", archive_source)
        self.assertIn("journal_references(text[start:end])", sources["validate.py"])
        for name in ("validate.py", "analytics.py", "retrieve.py"):
            self.assertNotIn("_NAMED", sources[name], name)

    def test_an_entry_moves_only_when_every_member_is_archived(self):
        self.write_journal(f"# Journal\n\n## {CLOSED}\nClosed T-R-01..T-R-03.\n\n"
                           f"## {PARTLY}\nT-R-01..04 under ADR-001..003.\n\n## {BROKEN}\nT-R-03..01.\n")
        result = archive.preview(self.root, "P1")
        self.assertIn(CLOSED, self.moved(result))
        self.assertEqual(self.stays(result, PARTLY), "names record(s) not archived: T-R-04")
        self.assertEqual(self.stays(result, BROKEN), "names id(s) that resolve to no record: T-R-03..01")

    def test_an_invalid_adr_range_keeps_its_entry_like_any_invalid_range(self):
        for adr_range in ("ADR-003..001", "ADR-001..T-R-03", "ADR-001..999"):
            with self.subTest(adr_range=adr_range):
                self.setUp()
                self.write_journal(f"# Journal\n\n## {CLOSED}\nT-R-01 under ADR-001..003.\n\n"
                                   f"## {BROKEN}\nT-R-01 under {adr_range}.\n")
                (warning,) = [f.message for f in validate.check(compiler.load(self.root))
                              if f.code == "unknown-journal-reference"]
                self.assertTrue(warning.startswith(f"names {adr_range}, which is not a valid range"))
                result = archive.preview(self.root, "P1")
                self.assertIn(CLOSED, self.moved(result))
                self.assertEqual(self.stays(result, BROKEN),
                                 f"names id(s) that resolve to no record: {adr_range}")
                archive.archive(self.root, "P1")
                self.assertIn(BROKEN, (self.authority / "JOURNAL.md").read_text(encoding="utf-8"))
                archived = (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8")
                self.assertIn(CLOSED, archived)
                self.assertNotIn(BROKEN, archived)

    def test_apply_and_restore_round_trip_a_range_entry(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                original = self.write_journal(
                    f"# Journal\n\n## {CLOSED}\nClosed T-R-01..03.\n\n## {PARTLY}\nT-R-01..T-R-04.\n",
                    newline).read_bytes()
                archive.archive(self.root, "P1")
                self.assertIn(CLOSED, (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8"))
                self.assertIn(PARTLY, (self.authority / "JOURNAL.md").read_text(encoding="utf-8"))
                project = compiler.load(self.root)
                self.assertEqual(validate.errors(validate.check(project)), [])
                self.assertEqual([f for f in validate.journal_findings(project)
                                  if f.code == "unknown-journal-reference"], [])
                archive.restore(self.root, "P1")
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original)

    def test_context_and_retrieve_read_the_same_expanded_references(self):
        self.write_journal(f"# Journal\n\n## {CLOSED}\nClosed T-R-01..05.\n")
        project = compiler.load(self.root)
        self.assertEqual([f.message for f in project.journal_validation],
                         ["names T-R-05 (in T-R-01..05), which does not exist"])
        for command in (["context", "T-R-02"], ["retrieve", "T-R-02"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["-C", str(self.root), *command])
            self.assertEqual(code, 0, (command, out.getvalue(), err.getvalue()))
        self.assertIn("names T-R-05 (in T-R-01..05), which does not exist",
                      [f.message for f in validate.check(compiler.load(self.root))])


class TestRangeDocs(unittest.TestCase):
    def test_spec_guide_and_chronicle_readme_describe_the_range_forms(self):
        for name in ("docs/SPEC.md", "docs/GUIDE.md", "templates/chronicle/README.md"):
            with self.subTest(name=name):
                text = (REPO / name).read_text(encoding="utf-8")
                for form in ("T-P0-01..05", "T-P0-01..T-P0-05", "ADR-003..005"):
                    self.assertIn(form, text)


if __name__ == "__main__":
    unittest.main()
