"""Archive parity on a synthetic large chronicle (AC-T-ARCH-DOCS-01-02, -03).

After every eligible phase and P-NONE is archived, authored records are equal
except `source`, derived facts are equal as sets, and `context` and `retrieve`
return the same records with only their sources updated. Restore returns them.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth_chronicle  # noqa: E402
from praukron import analytics, archive, cli, compile as compiler, layout, retrieve, validate  # noqa: E402

PHASES = 6
COMPLETE = [f"P{n}" for n in range(1, PHASES)]
SOURCE = re.compile(r"archive/[A-Za-z0-9.\-]+/")


def facts(project):
    report = analytics.report(project)
    return {
        "ready": set(report.ready), "blocked": set(report.blocked), "wip": set(report.wip),
        "gates": {(gate.id, gate.status) for gate in project.gates},
        "phase_progress": {(phase, progress.done, progress.total)
                           for phase, progress in report.phase_progress.items()},
        "unresolved_failures": set(analytics.unresolved_failures(project)),
        "awaiting_reconciliation": {item["assumption"] for item in analytics.awaiting_reconciliation(project)},
        "open_debt": {debt.id for debt in project.debts if not debt.closed},
        "open_assumptions": {assumption.id for assumption in project.assumptions if assumption.open},
    }


def unlocated(value):
    """Drop where a record lives: `archive/<phase>/` prefixes in any string."""
    if isinstance(value, dict):
        # `events.recent` is the last events in load order (working files, then each
        # archive), so only the set of ids is stable across an archive.
        return {key: sorted(item) if key == "recent" else unlocated(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unlocated(item) for item in value]
    return SOURCE.sub("", value) if isinstance(value, str) else value


QUERIES = ("T-P1-01", "T-P3-02", "T-OPS-06", "A-P2", "TD-P4", "EV-P5", "AC-T-P2-03",
           "why is P1 blocked?", "Hoàn thành T-P1-01")


def packets(root, project, queries=QUERIES, tasks=None):
    """`context` for every task, and a spread of `retrieve` queries, as pure content.

    A record that becomes the last in its file loses trailing blank lines, which is
    layout and not content, so item text is compared without them.
    """
    out = {}
    for task in (project.tasks if tasks is None else [project.task(i) for i in tasks]):
        out[f"context {task.id}"] = unlocated(analytics.context(project, task.id))
    report = analytics.report(project)
    for query in queries:
        pack = retrieve.retrieve(project, report, root, query, budget=0)
        out[f"retrieve {query}"] = {
            "routed": pack.routed,
            "items": sorted((item.kind, item.id, unlocated(item.source), item.text.rstrip()) for item in pack.items),
            "omitted": [vars(o) for o in pack.omitted],
        }
    return out


class ArchiveParity(unittest.TestCase):
    newline = "\n"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-parity-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root = synth_chronicle.build(self.dir, phases_count=PHASES, tasks_per_phase=6,
                                          pnone_tasks=6, newline=self.newline)
        self.authority = self.root / layout.AUTHORITY_DIR

    def run_cli(self, *options):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-C", str(self.root), "archive", *options, "--json"])
        self.assertEqual(err.getvalue(), "")
        return code, json.loads(out.getvalue())

    def apply(self, phase):
        code, planned = self.run_cli("plan", phase)
        self.assertEqual(code, 0, planned)
        code, applied = self.run_cli("apply", phase, "--plan", planned["digest"])
        self.assertEqual(code, 0, applied)
        self.assertTrue(applied["written"] and applied["refreshed"])
        return applied

    def test_generated_chronicle_is_valid_and_complete(self):
        project = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(project)), [])
        self.assertGreater(len(project.tasks), 30)
        self.assertEqual(set(archive._records(project)),
                         {"tasks", "contracts", "assumptions", "debts", "events"})
        text = (self.authority / "TASKS.md").read_bytes()
        self.assertEqual(b"\r\n" in text, self.newline == "\r\n")
        self.assertIn("Việc".encode("utf-8"), text)

    def test_archiving_every_phase_and_pnone_keeps_records_facts_and_reads(self):
        before = compiler.load(self.root)
        records, derived, reads = archive._records(before), facts(before), packets(self.root, before)
        sizes = {name: (self.authority / name).stat().st_size
                 for name in ("TASKS.md", "ACCEPTANCE.md", "JOURNAL.md")}
        archived = 0
        for phase in [*COMPLETE, "P-NONE"]:
            archived += len(self.apply(phase)["candidates"])
        self.assertGreater(archived, 0)
        after = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(after)), [])
        self.assertEqual(archive._records(after), records)
        self.assertEqual(facts(after), derived)
        self.assertEqual(packets(self.root, after), reads)
        # The working files shrank; the open work, the ADRs and the responses stayed.
        self.assertLess((self.authority / "TASKS.md").stat().st_size, sizes["TASKS.md"])
        self.assertLess((self.authority / "ACCEPTANCE.md").stat().st_size, sizes["ACCEPTANCE.md"])
        self.assertTrue(any(t.source.file.startswith("archive/") for t in after.tasks))
        self.assertTrue(any(t.source.file == "TASKS.md" for t in after.tasks))
        self.assertFalse((self.authority / "archive" / "P1" / "RESPONSES.md").exists())
        self.assertEqual({p.name for p in (self.authority / "archive" / "P1").iterdir()},
                         {"TASKS.md", "ACCEPTANCE.md", "ASSUMPTIONS.md", "TECH_DEBT.md", "TRACE.md",
                          "JOURNAL.md"})
        self.assertEqual([p.name for p in (self.authority / "ADR").iterdir()], ["ADR-001.md"])
        self.assertIn("R-1", (self.authority / "RESPONSES.md").read_text(encoding="utf-8"))

    def test_archive_files_take_the_line_endings_of_their_source(self):
        for phase in [*COMPLETE, "P-NONE"]:
            self.apply(phase)
        for path in (self.authority / "archive").rglob("*.md"):
            data = path.read_bytes()
            self.assertEqual(b"\r\n" in data, self.newline == "\r\n", path.name)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b"") if self.newline == "\r\n" else b"")

    def test_restore_in_another_order_returns_the_same_records_and_reads(self):
        before = compiler.load(self.root)
        records, derived, reads = archive._records(before), facts(before), packets(self.root, before)
        for phase in [*COMPLETE, "P-NONE"]:
            self.apply(phase)
        for phase in ["P-NONE", *reversed(COMPLETE)]:
            code, restored = self.run_cli("restore", phase)
            self.assertEqual(code, 0, restored)
            self.assertTrue(restored["written"] and restored["refreshed"])
        after = compiler.load(self.root)
        self.assertEqual(validate.errors(validate.check(after)), [])
        self.assertEqual(archive._records(after), records)
        self.assertEqual(facts(after), derived)
        self.assertEqual(packets(self.root, after), reads)
        self.assertTrue(all(t.source.file == "TASKS.md" for t in after.tasks))

    def test_journal_entries_round_trip_byte_for_byte(self):
        def entries():
            return archive._journal_entries(archive.Snapshot.capture(self.authority))

        before = entries()
        self.assertGreater(len(before), 10)
        for phase in [*COMPLETE, "P-NONE"]:
            self.apply(phase)
        archived = entries()
        self.assertEqual(sorted(archived), sorted(before))
        for phase in reversed(COMPLETE):
            self.run_cli("restore", phase)
        self.assertEqual(entries(), before)


class ArchiveParityCrlf(ArchiveParity):
    newline = "\r\n"


if __name__ == "__main__":
    unittest.main()
