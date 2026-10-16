"""Journal entries archive with the records they name (AC-T-ARCH-JOURNAL-01)."""

from __future__ import annotations

import itertools
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest import mock  # noqa: E402

from praukron import archive, cli, compile as compiler, journal, layout, retrieve, validate  # noqa: E402
from test_archive_plan import ArchivePlanCase  # noqa: E402
from test_archive_recovery import RecoveryCase  # noqa: E402

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

        def lossy(rule, archived, entries, newline, order):
            return real(rule, archived, entries, newline, order).replace("Finished T-ONE.", "Finished T-ONE")

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
        self.assertEqual(self.moved(result), [second])
        self.assertIn("not archived", self.stays(result, first))
        original = (self.authority / "JOURNAL.md").read_bytes()
        archive.archive(self.root, "P1")
        self.assertNotIn(second.encode(), (self.authority / "JOURNAL.md").read_bytes())
        self.finish_t_two()
        archive.archive(self.root, "P1")
        archived = (self.authority / "archive/P1/JOURNAL.md").read_text(encoding="utf-8")
        self.assertEqual([h for h, _, _ in archive._journal_sections(archived)], [first, second])
        self.assertEqual(archived[archived.index("## "):].encode(), original[original.index(b"## "):])

    def test_a_no_record_entry_keeps_its_order_when_a_later_same_date_entry_moves(self):
        a, b = "2026-01-01 — Note", "2026-01-01 — Work"
        self.write_journal(f"# Journal\n\n## {a}\nA thought.\n\n## {b}\nT-ONE.\n")
        original = (self.authority / "JOURNAL.md").read_bytes()
        result = archive.preview(self.root, "P1")
        self.assertEqual(self.moved(result), [b])
        self.assertIn("no archivable record", self.stays(result, a))
        archive.archive(self.root, "P1")
        archive.restore(self.root, "P1")
        self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original)

    def test_an_unknown_id_keeps_same_date_order_when_the_record_appears_later(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                first, second = "2026-01-01 — First", "2026-01-01 — Second"
                self.write_journal(f"# Journal\n\n## {first}\nWaits on T-LATER.\n\n## {second}\nT-ONE.\n", newline)
                result = archive.preview(self.root, "P1")
                self.assertEqual(self.moved(result), [second])
                self.assertIn("resolve to no record", self.stays(result, first))
                original = (self.authority / "JOURNAL.md").read_bytes()
                archive.archive(self.root, "P1")
                self.assertNotIn(second.encode(), (self.authority / "JOURNAL.md").read_bytes())
                self.add_task("T-LATER", "M-FOUNDATION")
                archive.archive(self.root, "P1")
                archived = (self.authority / "archive/P1/JOURNAL.md").read_bytes()
                self.assertLess(archived.index(b"First"), archived.index(b"Second"))
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)),
                                 sorted((h, original.decode()[s:e])
                                        for h, s, e in archive._journal_sections(original.decode())))
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


class TestJournalOrder(JournalCase):
    def round_trips_in_every_order(self, count):
        phases = [f"P{i}" for i in range(1, count + 1)]
        actions = [(operation, phase) for phase in phases for operation in ("archive", "restore")]
        schedules = [schedule for schedule in itertools.permutations(actions)
                     if all(schedule.index(("archive", p)) < schedule.index(("restore", p)) for p in phases)]
        self.assertEqual(len(schedules), 6 if count == 2 else 90)
        for newline, terminated, schedule in itertools.product((b"\n", b"\r\n"), (False, True), schedules):
            with self.subTest(newline=newline, terminated=terminated, schedule=schedule):
                self.setUp()
                for phase in phases[1:]:
                    self.add_phase(phase)
                    self.add_task(f"T-{phase}", f"M-{phase}")
                task_ids = ["T-ONE", *(f"T-{p}" for p in phases[1:])]
                titles = ("Zulu", "Alpha", "Mike", "Beta", "Yankee", "Delta")[:count * 2]
                sections = [f"## 2026-01-01 — {title}\n{task_ids[i % count]} — Tiếng Việt.\n"
                            + "\n" * (i % 3) for i, title in enumerate(titles)]
                sections[-1] = sections[-1].rstrip("\n") + ("\n" if terminated else "")
                original = self.write_journal("# Journal\n\n" + "".join(sections), newline).read_bytes()
                expected = [(h, original.decode()[s:e])
                            for h, s, e in archive._journal_sections(original.decode())]
                original_order = {h: i for i, (h, _) in enumerate(expected)}
                for operation, phase in schedule:
                    getattr(archive, operation)(self.root, phase)
                    snapshot = archive.Snapshot.capture(self.authority)
                    self.assertEqual(archive._journal_entries(snapshot), sorted(expected))
                    for text in archive._journal_files(snapshot).values():
                        headings = [h for h, _, _ in archive._journal_sections(text)]
                        self.assertEqual(headings, sorted(headings, key=original_order.__getitem__))
                    findings = validate.check(compiler.load(self.root), snapshot=snapshot)
                    self.assertEqual(validate.errors(findings), [])
                    self.assertFalse(any(f.code == "invalid-journal-order" for f in findings))
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original)
                self.assertFalse(list((self.authority / "archive").glob("*/JOURNAL.md")))

    def test_two_phase_round_trips_in_all_archive_restore_orders(self):
        self.round_trips_in_every_order(2)

    def test_three_phase_round_trips_in_all_archive_restore_orders(self):
        self.round_trips_in_every_order(3)

    def test_new_same_date_entry_and_repeated_cycles_use_saved_order(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.add_phase("P2")
                self.add_phase("P3")
                self.add_task("T-P2", "M-P2")
                self.add_task("T-P3", "M-P3")
                text = ("# Journal\n\n## 2026-01-01 — Zulu\nT-ONE.\n"
                        "## 2026-01-01 — Alpha\nT-P2.\n## 2026-01-01 — Mike\nT-P3.\n")
                original = self.write_journal(text, newline).read_bytes()
                for phase in ("P1", "P2", "P3"):
                    archive.archive(self.root, phase)
                new_entry = "## 2026-01-01 — Beta\nT-ONE — café."
                self.write_journal("# Journal\n\n" + new_entry, newline)
                archive.archive(self.root, "P1")
                expected = original + new_entry.encode().replace(b"\n", newline)
                for phase in ("P1", "P3", "P2"):
                    archive.restore(self.root, phase)
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), expected)
                for phase in ("P3", "P2", "P1"):
                    archive.archive(self.root, phase)
                archived = self.snapshot(self.authority / "archive")
                for phase in ("P2", "P1", "P3"):
                    archive.restore(self.root, phase)
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), expected)
                for phase in ("P1", "P2", "P3"):
                    archive.archive(self.root, phase)
                self.assertEqual(self.snapshot(self.authority / "archive"), archived)

    def test_order_metadata_is_in_the_plan_inventory_and_stale_guard(self):
        self.write_journal(entries())
        order_path = self.authority / "archive/JOURNAL.order.json"
        before = self.snapshot(self.root)
        result = archive.preview(self.root, "P1")
        self.assertIn("archive/JOURNAL.order.json", result.created_files)
        self.assertEqual(self.snapshot(self.root), before)
        order_path.parent.mkdir()
        order_path.write_bytes(b'{"version":1,"headings":[]}\n')
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=result.digest)
        self.assertEqual(error.exception.code, "stale-plan")
        self.assertEqual(self.snapshot(), before)
        archive.archive(self.root, "P1")
        result = archive.preview(self.root, "P1")
        order_path.write_bytes(order_path.read_bytes() + b"\n")
        before = self.snapshot()
        with self.assertRaises(archive.ArchiveError) as error:
            archive.archive(self.root, "P1", digest=result.digest)
        self.assertEqual(error.exception.code, "stale-plan")
        self.assertEqual(self.snapshot(), before)

    def test_unusable_metadata_is_rebuilt_when_order_is_wholly_observed(self):
        original = f"# Journal\n\n## {DONE}\nT-ONE.\n## 2026-01-01 — Second\nAC-T-ONE.\n"
        for invalid in (b"{", b'{"version":2,"headings":[]}',
                        json.dumps({"version": 1, "headings": [DONE, DONE]}).encode(),
                        json.dumps({"version": 1, "headings": ["2026-01-01 — Second", DONE]}).encode()):
            with self.subTest(invalid=invalid):
                self.setUp()
                self.write_journal(original)
                archive.archive(self.root, "P1")
                path = self.authority / "archive/JOURNAL.order.json"
                path.write_bytes(invalid)
                self.assertTrue(validate.journal_findings(compiler.load(self.root)))
                archive.restore(self.root, "P1")
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(), original.encode())
                self.assertEqual(validate.journal_findings(compiler.load(self.root)), [])

    def test_missing_legacy_metadata_never_guesses_cross_file_ties(self):
        self.add_phase("P2")
        self.add_task("T-P2", "M-P2")
        self.write_journal("# Journal\n\n## 2026-01-01 — Zulu\nT-ONE.\n"
                           "## 2026-01-01 — Alpha\nT-P2.\n")
        archive.archive(self.root, "P1")
        archive.archive(self.root, "P2")
        (self.authority / "archive/JOURNAL.order.json").unlink()
        before = archive._journal_entries(archive.Snapshot.capture(self.authority))
        result = archive.restore(self.root, "P1")
        self.assertEqual(self.moved(result), [])
        self.assertIn("Repair:", next(c.reason for c in result.candidates if c.kind == "journal"))
        self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)

    def test_restore_parity_rejects_added_final_newline_and_rolls_back_metadata(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                self.write_journal(f"# Journal\n\n## {DONE}\nT-ONE.", newline)
                archive.archive(self.root, "P1")
                before = self.snapshot()
                real = archive.JournalRule.restore

                def lossy(rule, working, entries, ending, order):
                    return real(rule, working, entries, ending, order) + ending

                with mock.patch.object(archive.JournalRule, "restore", lossy), self.assertRaises(archive.ArchiveError) as error:
                    archive.restore(self.root, "P1")
                self.assertIn("journal entries read back differently", str(error.exception))
                self.assertEqual(self.snapshot(), before)

    def test_read_back_rejects_reordered_sections_even_with_identical_spans(self):
        self.write_journal(f"# Journal\n\n## {DONE}\nT-ONE.\n"
                           "## 2026-01-01 — Second\nAC-T-ONE.\n")
        archive.archive(self.root, "P1")
        before = self.snapshot()
        real = archive.JournalRule.restore

        def reordered(rule, working, entries, newline, order):
            text = real(rule, working, entries, newline, order)
            sections = archive._journal_sections(text)
            return text[:sections[0][1]] + "".join(text[s:e] for _, s, e in reversed(sections))

        with mock.patch.object(archive.JournalRule, "restore", reordered), self.assertRaises(archive.ArchiveError) as error:
            archive.restore(self.root, "P1")
        self.assertIn("journal order", str(error.exception))
        self.assertEqual(self.snapshot(), before)


class TestJournalDiagnostics(JournalCase):
    def split_tie(self, newline=b"\n", later=False):
        first, second = "2026-01-01 — First", "2026-01-01 — Second"
        text = f"# Journal\n\n## {first}\nT-TWO — café.\n## {second}\nT-ONE.\n"
        if later:
            text += "## 2026-01-02 — Later\nT-ONE.\n"
        self.write_journal(text, newline)
        archive.archive(self.root, "P1")
        return first, second

    def journal_snapshot(self):
        return {name: data for name, data in self.snapshot().items()
                if name.endswith(("JOURNAL.md", "JOURNAL.order.json"))}

    def test_empty_partial_and_missing_metadata_keep_only_affected_entries(self):
        for newline, operation, coverage in itertools.product((b"\n", b"\r\n"),
                                                              ("archive", "restore"),
                                                              ("empty", "first", "missing")):
            with self.subTest(newline=newline, operation=operation, coverage=coverage):
                self.setUp()
                first, second = self.split_tie(newline)
                path = self.authority / "archive/JOURNAL.order.json"
                if coverage == "missing":
                    path.unlink()
                else:
                    headings = [] if coverage == "empty" else [first]
                    path.write_bytes(json.dumps({"version": 1, "headings": headings}).encode())
                before = self.journal_snapshot()
                if operation == "archive":
                    TestJournalApply.finish_t_two(self)
                result = getattr(archive, operation)(self.root, "P1")
                self.assertTrue(result.tasks)  # Records move despite the journal hold.
                self.assertEqual(self.moved(result), [])
                candidate = next(c for c in result.candidates if c.kind == "journal")
                self.assertIn("same-date journal order is unavailable", candidate.reason)
                self.assertIn("Git history", candidate.reason)
                self.assertIn(".praukron/backup/<timestamp>", candidate.reason)
                self.assertEqual(self.journal_snapshot(), before)
                self.assertTrue(any(f.code == "invalid-journal-order"
                                    for f in validate.check(compiler.load(self.root))))

    def test_unrelated_journal_dates_move_without_guessing_the_broken_tie(self):
        for operation, kind, newline in itertools.product(("archive", "restore"),
                                                          ("partial", "malformed", "invalid-utf8"),
                                                          (b"\n", b"\r\n")):
            with self.subTest(operation=operation, kind=kind, newline=newline):
                self.setUp()
                first, second = self.split_tie(newline, later=operation == "restore")
                path = self.authority / "archive/JOURNAL.order.json"
                payload = (json.dumps({"version": 1, "headings": [first]}).encode() if kind == "partial"
                           else b"\xff" if kind == "invalid-utf8" else b"{bad")
                path.write_bytes(payload + newline)
                if operation == "archive":
                    current = (self.authority / "JOURNAL.md").read_text(encoding="utf-8")
                    self.write_journal(current + "## 2026-01-02 — Later\nT-ONE.\n", newline)
                    TestJournalApply.finish_t_two(self)
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                result = getattr(archive, operation)(self.root, "P1")
                self.assertEqual(self.moved(result), ["2026-01-02 — Later"])
                self.assertTrue(result.tasks)
                self.assertTrue(result.contracts)
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                headings = json.loads(path.read_bytes())["headings"]
                self.assertEqual(first in headings, kind == "partial")
                self.assertNotIn(second, headings)
                self.assertTrue(path.read_bytes().endswith(newline))
                # A rewrite for another date must not bless the lost tie.
                state = archive._journal_order_state(archive.Snapshot.capture(self.authority))
                self.assertIn("2026-01-01", state.blocked)

    def test_unrelated_phase_archive_and_restore_ignore_unusable_metadata(self):
        self.split_tie()
        path = self.authority / "archive/JOURNAL.order.json"
        path.write_bytes(b"{bad")
        self.add_phase("P2")
        self.add_task("T-P2", "M-P2")
        before = self.journal_snapshot()
        for operation in ("archive", "restore"):
            result = getattr(archive, operation)(self.root, "P2")
            self.assertEqual(result.tasks, ["T-P2"])
            self.assertEqual(self.moved(result), [])
            self.assertTrue(result.warnings)
            self.assertEqual(self.journal_snapshot(), before)

    def test_append_on_a_split_date_follows_known_ties_and_can_archive_or_restore(self):
        for newline, operation, body in itertools.product((b"\n", b"\r\n"), ("archive", "restore"),
                                                         ("T-ONE — café.", "A new thought.")):
            with self.subTest(newline=newline, operation=operation, body=body):
                self.setUp()
                first, second = self.split_tie(newline)
                result = journal.append(self.root, {"date": "2026-01-01", "title": "Third", "body": body})
                self.assertTrue(result["written"], result)
                self.assertEqual(validate.journal_findings(compiler.load(self.root)), [])
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                result = getattr(archive, operation)(self.root, "P1")
                third = "2026-01-01 — Third"
                if operation == "archive":
                    self.assertEqual(third in self.moved(result), body.startswith("T-ONE"))
                    archive.restore(self.root, "P1")
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                headings = [h for h, _, _ in archive._journal_sections(
                    (self.authority / "JOURNAL.md").read_bytes().decode())]
                self.assertEqual(headings, [first, second, third])

    def test_new_working_headings_keep_neighbours_on_split_dates(self):
        for newline, operation, edit in itertools.product((b"\n", b"\r\n"),
                                                          ("archive", "restore", "archive-unrelated"),
                                                          ("rename", "insert")):
            with self.subTest(newline=newline, operation=operation, edit=edit):
                self.setUp()
                first, second, third, added = (f"2026-01-01 — {title}"
                                               for title in ("First", "Second", "Third", "New"))
                self.write_journal(f"# Journal\n\n## {first}\nT-TWO — café.\n"
                                   f"## {second}\nT-ONE.\n## {third}\nT-TWO.\n", newline)
                archive.archive(self.root, "P1")
                path = self.authority / "JOURNAL.md"
                working = path.read_bytes()
                if edit == "rename":
                    working = working.replace(first.encode(), added.encode())
                    expected = [second, added, third]
                else:
                    section = f"## {added}\nA new thought.\n".encode().replace(b"\n", newline)
                    working = working.replace(f"## {first}".encode(), section + f"## {first}".encode())
                    expected = [added, first, second, third]
                retained = working
                moving = ("2026-01-02 — Later" if operation == "archive-unrelated"
                          else "2026-01-01 — Fourth")
                if operation != "restore":
                    working += f"## {moving}\nT-ONE.\n".encode().replace(b"\n", newline)
                    expected.append(moving)
                path.write_bytes(working)
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                state = archive._journal_order_state(archive.Snapshot.capture(self.authority))
                self.assertEqual(state.blocked, {})
                self.assertEqual(list(state.order), expected)
                result = getattr(archive, "restore" if operation == "restore" else "archive")(self.root, "P1")
                self.assertEqual(self.moved(result), [second] if operation == "restore" else [moving])
                if operation != "restore":
                    self.assertEqual(path.read_bytes(), retained)
                    archive.restore(self.root, "P1")
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                self.assertEqual([h for h, _, _ in archive._journal_sections(path.read_bytes().decode())], expected)
                saved = json.loads((self.authority / "archive/JOURNAL.order.json").read_bytes())["headings"]
                self.assertEqual(saved, expected)
                self.assertEqual(validate.journal_findings(compiler.load(self.root)), [])
                archive.archive(self.root, "P1")
                archive.restore(self.root, "P1")
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                self.assertEqual([h for h, _, _ in archive._journal_sections(path.read_bytes().decode())], expected)

    def test_missing_working_heading_is_new_but_missing_archived_heading_is_history_loss(self):
        for missing_archive in (False, True):
            with self.subTest(missing_archive=missing_archive):
                self.setUp()
                first, second = self.split_tie()
                known = first if missing_archive else second
                path = self.authority / "archive/JOURNAL.order.json"
                path.write_bytes(json.dumps({"version": 1, "headings": [known]}).encode())
                result = archive.restore(self.root, "P1")
                self.assertEqual(bool(self.moved(result)), not missing_archive)
                if not missing_archive:
                    headings = [h for h, _, _ in archive._journal_sections(
                        (self.authority / "JOURNAL.md").read_bytes().decode())]
                    self.assertEqual(headings, [second, first])

    def test_metadata_writer_reads_corrupt_sidecar_bytes_once(self):
        self.split_tie()
        path = self.authority / "archive/JOURNAL.order.json"
        path.write_bytes(b"\xff\r\n")
        snapshot = archive.Snapshot.capture(self.authority)
        original = archive.Snapshot.original
        reads = []

        def once(captured, name):
            if name == "archive/JOURNAL.order.json":
                reads.append(name)
                self.assertEqual(len(reads), 1, "serializer reread unusable metadata")
            return original(captured, name)

        with mock.patch.object(archive.Snapshot, "original", once):
            text = archive._journal_order_text(snapshot, {})
        self.assertTrue(text.endswith("\r\n"))
        self.assertEqual(json.loads(text), {"version": 1, "headings": []})

    def test_unrelated_rewrite_retains_stale_headings_of_a_blocked_date(self):
        for operation in ("archive", "restore"):
            with self.subTest(operation=operation):
                self.setUp()
                first, second = self.split_tie(later=operation == "restore")
                archived = self.authority / "archive/P1/JOURNAL.md"
                archived.write_bytes(archived.read_bytes().replace(second.encode(), (second + " renamed").encode()))
                if operation == "archive":
                    working = (self.authority / "JOURNAL.md").read_text(encoding="utf-8")
                    self.write_journal(working + "## 2026-01-02 — Later\nT-ONE.\n")
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                result = getattr(archive, operation)(self.root, "P1")
                self.assertEqual(self.moved(result), ["2026-01-02 — Later"])
                saved = json.loads((self.authority / "archive/JOURNAL.order.json").read_bytes())["headings"]
                self.assertIn(first, saved)
                self.assertIn(second, saved)
                self.assertNotIn(second + " renamed", saved)
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                self.assertTrue(any("renamed or removed" in f.message
                                    for f in validate.journal_findings(compiler.load(self.root))))

    def test_validation_reads_only_journal_inputs_and_reuses_the_loaded_project(self):
        self.split_tie()
        (self.authority / "unrelated.bin").write_bytes(b"\xff")
        original = Path.read_bytes
        reads = []

        def narrow(path):
            # Task/contract parsing now captures exact bytes for revision reads.
            # Journal validation must still avoid every unrelated authority file.
            self.assertIn(path.name, ("TASKS.md", "ACCEPTANCE.md", "JOURNAL.md", "JOURNAL.order.json"))
            reads.append(path)
            return original(path)

        with mock.patch.object(archive.Snapshot, "capture", side_effect=AssertionError("full capture")), \
                mock.patch.object(Path, "read_bytes", narrow):
            project = compiler.load(self.root)
            self.assertEqual(validate.journal_findings(project), [])
            count = len(reads)
            self.assertEqual(validate.journal_findings(project), [])
            self.assertEqual(len(reads), count)
        self.assertEqual(sum(p.name in ("JOURNAL.md", "JOURNAL.order.json") for p in reads), 3)
        for path in reads:
            if path.name in ("TASKS.md", "ACCEPTANCE.md"):
                self.assertEqual(reads.count(path), 1)

    def test_validation_reuses_working_only_journal_diagnostics_without_sidecar_reads(self):
        self.write_journal("# Journal\n\n## 2026-01-01 — Work\nT-ONE.\n")
        project = compiler.load(self.root)
        with mock.patch.object(archive.Snapshot, "capture", side_effect=AssertionError("full capture")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unneeded journal read")):
            self.assertEqual(validate.journal_findings(project), [])
        self.write_journal("# Journal\n\n## 2026-01-01 — Work\nT-ONE.\n"
                           "## 2026-01-01 — Work\nDuplicate.\n")
        self.assertTrue(any(f.code == "duplicate-journal-heading"
                            for f in validate.journal_findings(compiler.load(self.root))))

    def test_validation_reports_a_non_file_sidecar_with_its_actual_cause(self):
        path = self.authority / "archive/JOURNAL.order.json"
        path.mkdir(parents=True)
        findings = validate.journal_findings(compiler.load(self.root))
        self.assertTrue(any(str(path) in f.message and "directory; expected a regular file" in f.message
                            for f in findings))

    def test_observed_owner_reorder_wins_and_is_persisted_with_the_move(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                a, b, c = (f"2026-01-01 — {name}" for name in ("A", "B", "C"))
                self.write_journal(f"# Journal\n\n## {a}\nT-TWO.\n## {b}\nT-TWO.\n## {c}\nT-ONE.\n", newline)
                archive.archive(self.root, "P1")
                self.write_journal(f"# Journal\n\n## {b}\nT-TWO.\n## {a}\nT-TWO.\n", newline)
                warnings = validate.journal_findings(compiler.load(self.root))
                self.assertTrue(any("file wins" in f.message for f in warnings))
                TestJournalApply.finish_t_two(self)
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                result = archive.archive(self.root, "P1")
                self.assertIn("ordering metadata:", result.render(True))
                self.assertEqual(json.loads((self.authority / "archive/JOURNAL.order.json").read_bytes())["headings"],
                                 [b, a, c])
                archive.restore(self.root, "P1")
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)
                text = (self.authority / "JOURNAL.md").read_bytes().decode()
                self.assertEqual([h for h, _, _ in archive._journal_sections(text)], [b, a, c])
                self.assertEqual(validate.journal_findings(compiler.load(self.root)), [])
                added = "2026-01-01 — New"
                self.write_journal(f"# Journal\n\n## {a}\nT-ONE.\n## {added}\nT-ONE.\n"
                                   f"## {b}\nT-ONE.\n## {c}\nT-ONE.\n", newline)
                self.assertTrue(any("file wins" in f.message
                                    for f in validate.journal_findings(compiler.load(self.root))))
                archive.archive(self.root, "P1")
                self.assertEqual(json.loads((self.authority / "archive/JOURNAL.order.json").read_bytes())["headings"],
                                 [a, added, b, c])

    def test_validate_warns_about_structure_stale_headings_and_missing_tie_coverage(self):
        for payload in (b"{", b'{"version":2,"headings":[]}', b'{"version":1,"headings":[]}',
                        b'{"version":1,"headings":["gone"]}', None):
            with self.subTest(payload=payload):
                self.setUp()
                self.split_tie()
                path = self.authority / "archive/JOURNAL.order.json"
                if payload is None:
                    path.unlink()
                else:
                    path.write_bytes(payload)
                findings = validate.check(compiler.load(self.root))
                issues = [f for f in findings if f.code == "invalid-journal-order"]
                self.assertTrue(issues)
                self.assertTrue(all(f.severity == "warning" for f in issues))
                if payload and b"gone" in payload:
                    self.assertTrue(any("absent from every journal" in f.message for f in issues))
                self.assertEqual(validate.errors(findings), [])

    def test_validate_command_reports_the_metadata_warning(self):
        self.split_tie()
        (self.authority / "archive/JOURNAL.order.json").write_bytes(b"{bad")
        with mock.patch.object(cli, "_report_findings") as reported, mock.patch("builtins.print"):
            code = cli.main(["-C", str(self.root), "validate"])
        self.assertEqual(code, 0)
        findings = [f for call in reported.call_args_list for f in call.args[0]]
        self.assertTrue(any(f.code == "invalid-journal-order" for f in findings))

    def test_cross_file_cycle_is_reported_and_held_but_records_still_move(self):
        a, b, c = (f"2026-01-01 — {name}" for name in ("A", "B", "C"))
        self.write_journal(f"# Journal\n\n## {a}\nT-TWO.\n## {b}\nT-ONE.\n## {c}\nT-TWO.\n")
        archive.archive(self.root, "P1")
        self.write_journal(f"# Journal\n\n## {c}\nT-TWO.\n## {a}\nT-TWO.\n")
        self.assertTrue(any("conflicting cross-file ties" in f.message
                            for f in validate.journal_findings(compiler.load(self.root))))
        TestJournalApply.finish_t_two(self)
        before = self.journal_snapshot()
        result = archive.archive(self.root, "P1")
        self.assertTrue(result.tasks)
        self.assertEqual(self.moved(result), [])
        self.assertEqual(self.journal_snapshot(), before)

    def test_restore_collisions_include_every_archive_and_the_working_journal(self):
        for target in ("JOURNAL.md", "archive/P2/JOURNAL.md"):
            with self.subTest(target=target):
                self.setUp()
                first, second = self.split_tie()
                path = self.authority / target
                path.parent.mkdir(parents=True, exist_ok=True)
                current = path.read_text(encoding="utf-8") if path.exists() else "# Journal\n\n"
                path.write_bytes((current + f"## {second}\nDuplicate.\n").encode())
                findings = validate.check(compiler.load(self.root))
                self.assertTrue(any(f.code == "duplicate-journal-heading" for f in findings))
                before = self.snapshot()
                with self.assertRaises(archive.ArchiveError) as error:
                    archive.restore(self.root, "P1")
                self.assertEqual(error.exception.code, "id-collision")
                self.assertEqual(self.snapshot(), before)

    def test_renamed_heading_is_reported_without_guessing_its_cross_file_position(self):
        first, _ = self.split_tie()
        archived = self.authority / "archive/P1/JOURNAL.md"
        old = "2026-01-01 — Second"
        archived.write_bytes(archived.read_bytes().replace(old.encode(), (old + " corrected").encode()))
        before = self.journal_snapshot()
        findings = validate.journal_findings(compiler.load(self.root))
        self.assertTrue(any("renamed or removed" in f.message for f in findings))
        result = archive.restore(self.root, "P1")
        self.assertEqual(self.moved(result), [])
        self.assertEqual(self.journal_snapshot(), before)

    def test_validation_of_a_capture_does_not_read_later_metadata_from_disk(self):
        self.split_tie()
        snapshot = archive.Snapshot.capture(self.authority)
        (self.authority / "archive/JOURNAL.order.json").write_bytes(b"{bad")
        captured = compiler.load(self.root, authority=snapshot.path())
        self.assertEqual(validate.journal_findings(captured, snapshot), [])
        self.assertTrue(validate.journal_findings(compiler.load(self.root)))

    def test_nonchronological_journal_follows_documented_date_first_placement(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.setUp()
                later = "## 2026-01-05 — Later\nT-ONE — café.\n"
                earlier = "## 2026-01-01 — Earlier\nAC-T-ONE.\n\n"
                self.write_journal("# Journal\n\n" + later + earlier, newline)
                before = archive._journal_entries(archive.Snapshot.capture(self.authority))
                archive.archive(self.root, "P1")
                archive.restore(self.root, "P1")
                self.assertEqual((self.authority / "JOURNAL.md").read_bytes(),
                                 ("# Journal\n\n" + earlier + later).encode().replace(b"\n", newline))
                self.assertEqual(archive._journal_entries(archive.Snapshot.capture(self.authority)), before)


class TestJournalOrderRecovery(RecoveryCase):
    def test_new_and_existing_metadata_recover_with_git_and_backup(self):
        for mode, state in (("nogit", "backup"), ("git", "unstaged"),
                            ("git", "staged"), ("git", "committed")):
            for existing in (False, True):
                with self.subTest(mode=mode, state=state, existing=existing):
                    self.setUp()
                    path = self.authority / "JOURNAL.md"
                    path.write_bytes(f"# Journal\n\n## {DONE}\nT-ONE.\n".encode())
                    if existing:
                        archive.archive(self.root, "P1")
                        path.write_bytes(f"# Journal\n\n## {MIXED}\nT-ONE.\n".encode())
                    if mode == "git":
                        self.init_git()
                    before = self.snapshot()
                    result = archive.archive(self.root, "P1")
                    self.assertIn("archive/JOURNAL.order.json", result.changed_files)
                    self.assertEqual("archive/JOURNAL.order.json" in result.created_files, not existing)
                    if state in ("staged", "committed"):
                        self.git("add", "--", layout.AUTHORITY_DIR)
                    if state == "committed":
                        self.git("commit", "-m", "Archive journal")
                        commit = self.git("rev-parse", "HEAD")
                    commands = result.recovery["commands"][state]
                    if state == "committed":
                        commands = [[commit if arg == "<archive-commit>" else arg for arg in argv]
                                    for argv in commands]
                    self.run_commands(commands)
                    self.assertEqual(self.snapshot(), before)

    def test_failure_after_metadata_replace_undoes_every_file(self):
        for operation in ("archive", "restore"):
            with self.subTest(operation=operation):
                self.setUp()
                (self.authority / "JOURNAL.md").write_bytes(f"# Journal\n\n## {DONE}\nT-ONE.\n".encode())
                if operation == "restore":
                    archive.archive(self.root, "P1")
                before = self.snapshot()
                real = archive._replace

                def fail_after_metadata(path, data):
                    real(path, data)
                    if path.name == "JOURNAL.order.json":
                        raise OSError("metadata written; simulated disk failure")

                with mock.patch.object(archive, "_replace", fail_after_metadata), self.assertRaises(OSError):
                    getattr(archive, operation)(self.root, "P1")
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
