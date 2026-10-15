"""Journal entries archive with the records they name (AC-T-ARCH-JOURNAL-01)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest import mock  # noqa: E402

from praukron import archive, compile as compiler, journal, layout, retrieve, validate  # noqa: E402
from test_archive_plan import ArchivePlanCase  # noqa: E402

DONE = "2026-01-01 — Done work"
OPEN = "2026-01-02 — Open work"
NOTHING = "2026-01-03 — Nothing named"
ADR = "2026-01-04 — Only a decision"
MIXED = "2026-01-05 — Decision and done work"
CRITERION = "2026-01-06 — Criterion note"
UNKNOWN = "2026-01-07 — Unknown reference"


def entries(newline: str = "\n") -> str:
    text = (f"# Journal\n\n## {DONE}\nFinished T-ONE.\n\n## {OPEN}\nT-ONE waits on T-TWO.\n\n"
            f"## {NOTHING}\nA thought.\n\n## {ADR}\nSee ADR-001 only.\n\n"
            f"## {MIXED}\nADR-001 shaped AC-T-ONE.\n\n## {CRITERION}\nAC-T-ONE-01 passed.\n\n## {UNKNOWN}\nT-ONE and T-MISSING.\n")
    return text.replace("\n", newline)


class JournalCase(ArchivePlanCase):
    def write_journal(self, text: str, newline: bytes = b"\n") -> Path:
        path = self.authority / "JOURNAL.md"
        path.write_bytes(text.encode("utf-8").replace(b"\n", newline))
        return path

    def stays(self, result, heading: str) -> str:
        candidate = next(c for c in result.candidates if c.id == heading)
        self.assertFalse(candidate.moves)
        return candidate.reason

    def moved(self, result) -> list[str]:
        return [c.id for c in result.candidates if c.kind == "journal" and c.moves]


class TestJournalEligibility(JournalCase):
    def test_entry_moves_only_when_every_named_record_is_archived(self):
        self.write_journal(entries())
        result = archive.preview(self.root, "P1")
        self.assertEqual(self.moved(result), [DONE, MIXED, CRITERION])
        self.assertIn("T-TWO", self.stays(result, OPEN))
        self.assertTrue(all(c.destination == "archive/P1/JOURNAL.md"
                            for c in result.candidates if c.kind == "journal" and c.moves))

    def test_entries_naming_nothing_or_only_adrs_stay(self):
        self.write_journal(entries())
        result = archive.preview(self.root, "P1")
        self.assertIn("T-MISSING", self.stays(result, UNKNOWN))
        self.assertEqual(self.stays(result, NOTHING), "names no archivable record")
        self.assertEqual(self.stays(result, ADR), "names no archivable record")

    def test_duplicate_heading_stays_and_is_reported_across_working_and_archive(self):
        self.write_journal(entries() + f"\n## {DONE}\nAgain T-ONE.\n")
        result = archive.preview(self.root, "P1")
        self.assertIn("duplicate", self.stays(result, DONE))
        self.assertIn(f"kept {DONE}: duplicate", result.render(False))
        archive.archive(self.root, "P1")
        self.assertIn(DONE, (self.authority / "JOURNAL.md").read_text(encoding="utf-8"))
        self.assertNotIn(DONE, (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8"))
        self.write_journal(f"# Journal\n\n## {MIXED}\nAC-T-ONE again.\n")
        self.assertIn("duplicate", self.stays(archive.preview(self.root, "P1"), MIXED))

    def test_destination_is_the_latest_phase_among_named_records(self):
        self.add_phase("P2")
        archive.archive(self.root, "P1")
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        start, end = text.index("## T-TWO:"), text.index("## T-THREE:")
        section = text[start:end].replace("- Module: M-FOUNDATION", "- Module: M-P2")
        tasks.write_text(text[:start] + section.replace("- Status: TODO", "- Status: DONE") + text[end:],
                         encoding="utf-8")
        self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`\n  - Evidence: Walls stand.")
        self.write_journal(f"# Journal\n\n## {OPEN}\nT-ONE then T-TWO.\n\n## {DONE}\nOnly T-ONE.\n")
        result = archive.preview(self.root, "P2")
        destinations = {c.id: c.destination for c in result.candidates if c.kind == "journal"}
        self.assertEqual(destinations[OPEN], "archive/P2/JOURNAL.md")
        self.assertEqual(destinations[DONE], "archive/P1/JOURNAL.md")
        archive.archive(self.root, "P2")
        self.assertTrue((self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8")
                        .startswith("# Journal · P1 (archived)"))
        self.assertTrue((self.authority / "archive/P2/JOURNAL.md").read_text(encoding="utf-8")
                        .startswith("# Journal · P2 (archived)"))
        self.assertIn(OPEN, (self.authority / "archive/P2/JOURNAL.md").read_text(encoding="utf-8"))
        self.assertIn(DONE, (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8"))
        self.assertEqual((self.authority / "JOURNAL.md").read_text(encoding="utf-8").strip(), "# Journal")
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_p_none_sorts_last_for_the_destination(self):
        archive.archive(self.root, "P1")
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        start = text.index("## T-THREE:")
        section = text[start:].replace("- Module: M-FOUNDATION", "- Module: M-UPKEEP").replace(
            "- Status: TODO", "- Status: DONE")
        tasks.write_text(text[:start], encoding="utf-8")
        (self.authority / "archive/P-NONE").mkdir()
        (self.authority / "archive/P-NONE/TASKS.md").write_text("# Tasks\n\n" + section, encoding="utf-8")
        self.rewrite("ACCEPTANCE.md", "`NOT_RUN`\n\n## AC-T-THREE", "`NOT_RUN`\n\n## AC-T-THREE")
        self.write_journal(f"# Journal\n\n## {DONE}\nT-ONE and T-THREE.\n\n## {OPEN}\nOnly T-THREE.\n")
        project = compiler.load(self.root)
        self.assertEqual(project.task("T-THREE").phase, "P-NONE")
        # T-ONE is already archived in P1, so both entries name only archived records.
        result = archive.plan(project, "P1", self.root)
        destinations = {c.id: c.destination for c in result.candidates if c.kind == "journal"}
        self.assertEqual(destinations[DONE], "archive/P-NONE/JOURNAL.md")
        self.assertEqual(destinations[OPEN], "archive/P-NONE/JOURNAL.md")

    def test_the_journal_rule_sees_every_other_rules_moves_in_any_order(self):
        self.write_journal(entries())
        expected = self.moved(archive.preview(self.root, "P1"))
        rules = (archive.RECORD_RULES[-1], *archive.RECORD_RULES[:-1])
        self.assertIsInstance(rules[0], archive.JournalRule)
        with mock.patch.object(archive, "RECORD_RULES", rules):
            self.assertEqual(self.moved(archive.preview(self.root, "P1")), expected)

    def test_text_plan_lists_each_entry_that_moves_or_stays_with_its_reason(self):
        self.write_journal(entries())
        text = archive.preview(self.root, "P1").render(False)
        self.assertIn(f"journal {DONE}: JOURNAL.md -> archive/P1/JOURNAL.md", text)
        self.assertIn(f"kept {OPEN}: names record(s) not archived: T-TWO", text)
        self.assertIn(f"kept {NOTHING}: names no archivable record", text)
        self.assertIn(f"kept {UNKNOWN}: names id(s) that resolve to no record: T-MISSING", text)

    def test_without_a_journal_file_nothing_is_planned(self):
        result = archive.preview(self.root, "P1")
        self.assertEqual([c for c in result.candidates if c.kind == "journal"], [])


class TestJournalApply(JournalCase):
    def test_entries_move_verbatim_and_keep_order_in_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.write_journal(entries(), newline)
                digest = archive.preview(self.root, "P1").digest
                archive.archive(self.root, "P1", digest=digest)
                moved = (self.authority / "archive/P1/JOURNAL.md").read_bytes()
                kept = (self.authority / "JOURNAL.md").read_bytes()
                for section in (f"## {DONE}\nFinished T-ONE.", f"## {MIXED}\nADR-001 shaped AC-T-ONE."):
                    self.assertIn(section.encode().replace(b"\n", newline), moved)
                self.assertLess(moved.index(DONE.encode()), moved.index(MIXED.encode()))
                self.assertLess(moved.index(MIXED.encode()), moved.index(CRITERION.encode()))
                for heading in (OPEN, NOTHING, ADR):
                    self.assertIn(heading.encode(), kept)
                for heading in (DONE, MIXED, CRITERION):
                    self.assertNotIn(heading.encode(), kept)
                self.assertEqual(moved.count(b"\r\n"), moved.count(b"\n") if newline == b"\r\n" else 0)
                self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_sections_move_byte_for_byte_with_trailing_breaks_and_unicode(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                first = f"## {DONE}\nFinished T-ONE — Tiếng Việt.\n\n\n\n"
                second = f"## {OPEN}\nStill T-TWO.\n\n"
                third = f"## {MIXED}\nAC-T-ONE.\n\n\n"
                self.write_journal("# Journal\n\n" + first + second + third, newline)
                original = (self.authority / "JOURNAL.md").read_bytes()
                archive.archive(self.root, "P1")
                encode = lambda text: text.encode("utf-8").replace(b"\n", newline)  # noqa: E731
                moved = (self.authority / "archive/P1/JOURNAL.md").read_bytes()
                kept = (self.authority / "JOURNAL.md").read_bytes()
                self.assertIn(encode(first), moved)
                self.assertIn(encode(third), moved)
                self.assertEqual(kept, encode("# Journal\n\n" + second))
                self.assertEqual(original.count(newline), kept.count(newline) + encode(first + third).count(newline))

    def test_a_failed_read_back_of_journal_sections_rolls_the_move_back(self):
        self.write_journal(entries())
        before = self.snapshot()
        real = archive.JournalRule.place

        def lossy(rule, archived, entries, newline):
            return real(rule, archived, entries, newline).replace("Finished T-ONE.", "Finished T-ONE")

        with mock.patch.object(archive.JournalRule, "place", lossy), self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1")
        self.assertIn("journal entries read back differently", str(error.exception))
        self.assertEqual(self.snapshot(), before)

    def test_an_older_entry_archived_later_is_inserted_by_date(self):
        older, newer = "2026-01-01 — Older", "2026-01-02 — Newer"
        self.write_journal(f"# Journal\n\n## {older}\nWaits on T-TWO and T-ONE.\n\n## {newer}\nT-ONE.\n")
        archive.archive(self.root, "P1")
        headings = [h for h, _, _ in archive._journal_sections(
            (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8"))]
        self.assertEqual(headings, [newer])
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        start, end = text.index("## T-TWO:"), text.index("## T-THREE:")
        tasks.write_text(text[:start] + text[start:end].replace("- Status: TODO", "- Status: DONE") + text[end:],
                         encoding="utf-8")
        self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`\n  - Evidence: Walls stand.")
        archive.archive(self.root, "P1")
        archived = (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8")
        self.assertEqual([h for h, _, _ in archive._journal_sections(archived)], [older, newer])
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def finish_t_two(self):
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        start, end = text.index("## T-TWO:"), text.index("## T-THREE:")
        tasks.write_text(text[:start] + text[start:end].replace("- Status: TODO", "- Status: DONE") + text[end:],
                         encoding="utf-8")
        self.rewrite("ACCEPTANCE.md", "`NOT_RUN`", "`PASS`\n  - Evidence: Walls stand.")

    def test_an_unterminated_final_section_moves_byte_for_byte(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                text = f"## {DONE}\nFinished T-ONE."
                last = text.encode().replace(b"\n", newline)
                self.write_journal(f"# Journal\n\n## {OPEN}\nT-TWO.\n\n" + text, newline)
                archive.archive(self.root, "P1")
                moved = (self.authority / "archive/P1/JOURNAL.md").read_bytes()
                self.assertTrue(moved.endswith(newline + newline + last), moved)
                self.assertFalse(moved.endswith(newline))
                self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_placing_next_to_an_unterminated_archived_entry_never_alters_it(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                first, second = "2026-01-01 — First", "2026-01-02 — Second"
                self.write_journal(f"# Journal\n\n## {first}\nT-ONE.\n", newline)
                archive.archive(self.root, "P1")
                path = self.authority / "archive/P1/JOURNAL.md"
                path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
                self.write_journal(f"# Journal\n\n## {second}\nT-ONE.\n", newline)
                self.assertIn("no final line ending", self.stays(archive.preview(self.root, "P1"), second))
                older = "2025-12-31 — Older"
                self.write_journal(f"# Journal\n\n## {older}\nT-ONE.\n", newline)
                archive.archive(self.root, "P1")
                archived = path.read_bytes()
                self.assertTrue(archived.endswith(f"## {first}\nT-ONE.".encode().replace(b"\n", newline)))
                self.assertLess(archived.index(b"Older"), archived.index(b"First"))

    def test_same_date_entries_keep_their_original_order_across_runs(self):
        first, second = "2026-01-01 — First", "2026-01-01 — Second"
        self.write_journal(f"# Journal\n\n## {first}\nWaits on T-TWO and T-ONE.\n\n## {second}\nT-ONE.\n")
        result = archive.preview(self.root, "P1")
        self.assertEqual(self.moved(result), [])
        self.assertIn("original order", self.stays(result, second))
        self.finish_t_two()
        archive.archive(self.root, "P1")
        archived = (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8")
        self.assertEqual([h for h, _, _ in archive._journal_sections(archived)], [first, second])

    def test_any_earlier_entry_that_stays_holds_a_later_same_date_entry_back(self):
        a, b = "2026-01-01 — Note", "2026-01-01 — Work"
        self.write_journal(f"# Journal\n\n## {a}\nA thought.\n\n## {b}\nT-ONE.\n")
        result = archive.preview(self.root, "P1")
        self.assertEqual(self.moved(result), [])
        self.assertIn("original order", self.stays(result, b))

    def test_an_unknown_id_keeps_same_date_order_when_the_record_appears_later(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                first, second = "2026-01-01 — First", "2026-01-01 — Second"
                self.write_journal(f"# Journal\n\n## {first}\nWaits on T-LATER.\n\n## {second}\nT-ONE.\n", newline)
                result = archive.preview(self.root, "P1")
                self.assertEqual(self.moved(result), [])
                self.assertIn("resolve to no record", self.stays(result, first))
                self.assertIn("original order", self.stays(result, second))
                tasks = self.authority / "TASKS.md"
                text = tasks.read_text(encoding="utf-8")
                start, end = text.index("## T-ONE:"), text.index("## T-TWO:")
                later = text[start:end].replace("T-ONE", "T-LATER").replace("AC: AC-T-LATER", "AC: AC-T-ONE")
                tasks.write_text(text + "\n" + later, encoding="utf-8")
                archive.archive(self.root, "P1")
                archived = (self.authority / "archive/P1/JOURNAL.md").read_bytes()
                self.assertLess(archived.index(b"First"), archived.index(b"Second"))
                self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_archive_then_restore_round_trips_journals_byte_for_byte(self):
        a, b, c = "2026-01-01 — Done", "2026-01-02 — Open", "2026-01-03 — Done again"
        cases = {
            "retained final entry ends in one newline": f"# Journal\n\n## {a}\nT-ONE.\n\n## {b}\nT-TWO.\n",
            "retained final entry is unterminated": f"# Journal\n\n## {a}\nT-ONE.\n\n## {b}\nT-TWO.",
            "adjacent sections move together": f"# Journal\n\n## {a}\nT-ONE.\n\n## 2026-01-01 — Done too\nAC-T-ONE.\n\n## {b}\nT-TWO.\n",
            "moved final entry is unterminated": f"# Journal\n\n## {b}\nT-TWO.\n\n## {c}\nT-ONE.",
            "moved final entry ends in one newline": f"# Journal\n\n## {b}\nT-TWO.\n\n## {c}\nT-ONE.\n",
            "extra blank lines between entries": f"# Journal\n\n## {a}\nT-ONE.\n\n\n\n## {b}\nT-TWO.\n\n\n",
        }
        for newline in (b"\n", b"\r\n"):
            for label, text in cases.items():
                with self.subTest(newline=newline, case=label):
                    self.setUp()
                    original = self.write_journal(text, newline).read_bytes()
                    archive.archive(self.root, "P1")
                    self.assertNotEqual((self.authority / "JOURNAL.md").read_bytes(), original)
                    archive.restore(self.root, "P1")
                    self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original)
                    self.assertFalse((self.authority / "archive/P1/JOURNAL.md").exists())
                    self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_restore_places_same_date_entries_ahead_of_retained_ones_in_order(self):
        first, second, kept = "2026-01-01 — First", "2026-01-01 — Second", "2026-01-01 — Kept"
        text = f"# Journal\n\n## {first}\nT-ONE.\n\n## {second}\nAC-T-ONE.\n\n## {kept}\nT-TWO.\n"
        original = self.write_journal(text).read_bytes()
        archive.archive(self.root, "P1")
        archive.restore(self.root, "P1")
        self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original)

    def test_restore_refuses_rather_than_alter_an_unterminated_retained_entry(self):
        later, kept = "2026-01-05 — Later", "2026-01-02 — Kept"
        self.write_journal(f"# Journal\n\n## {later}\nT-ONE.\n\n## {kept}\nT-TWO.")
        archive.archive(self.root, "P1")
        self.write_journal(f"# Journal\n\n## 2026-01-01 — New\nNothing.\n\n## 2026-01-02 — Last\nOpen.")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.restore(self.root, "P1")
        self.assertIn("cannot restore journal entry", str(error.exception))
        self.assertEqual(self.snapshot(), before)

    def test_index_and_old_pointers_resolve_through_retrieve(self):
        self.write_journal(entries())
        project = compiler.load(self.root)
        compiler.write(self.root, project)
        before = (self.authority / "INDEX.md").read_text(encoding="utf-8")
        pointers = [line.rsplit("#", 1)[1] for line in before.splitlines() if "JOURNAL.md#" in line]
        self.assertIn(CRITERION, pointers)
        archive.archive(self.root, "P1")
        chronicle = retrieve.Chronicle(self.root)
        for heading in (DONE, MIXED, CRITERION):
            self.assertIn(f"## {heading}", chronicle.section("JOURNAL.md", heading))
        self.assertIn("T-TWO", chronicle.section("JOURNAL.md", OPEN))
        compiler.write(self.root, compiler.load(self.root))
        after = (self.authority / "INDEX.md").read_text(encoding="utf-8")
        for line in after.splitlines():
            if "JOURNAL.md#" in line:
                self.assertTrue(chronicle.section("JOURNAL.md", line.rsplit("#", 1)[1]))

    def test_journal_append_still_refuses_a_heading_only_an_archive_holds(self):
        self.write_journal(entries())
        archive.archive(self.root, "P1")
        result = journal.append(self.root, {"date": "2026-01-01", "title": "Done work", "body": "Again."})
        self.assertEqual(result["error"]["code"], "duplicate-heading")
        self.assertNotIn(DONE, (self.authority / "JOURNAL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
