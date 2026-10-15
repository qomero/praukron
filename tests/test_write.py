"""Structured mutation regression tests (AC-T-WRITE-CORE-01)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import (  # noqa: E402
    archive, cli, compile as compiler, journal, layout, mutate, respond, retrieve, validate,
)
from praukron.model import DOMAINS, Source  # noqa: E402
from praukron.parse import _sections  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402


class WriteCase(FixtureCase):
    def setUp(self):
        super().setUp()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.journal = self.authority / "JOURNAL.md"
        self.journal.write_bytes("# Journal\n\n## 2026-01-01 — Start\n\nEarlier.\n".encode())
        self.payload = {"date": "2026-10-15", "title": "Progress", "body": "Đã xong.\n\n### Chi tiết\nGiữ nguyên tiếng Việt."}
        self.heading = "2026-10-15 — Progress"

    def snapshot(self):
        return {p.relative_to(self.authority): p.read_bytes()
                for p in self.authority.rglob("*") if p.is_file()}

    def run_cli(self, *options, payload=None, raw=None):
        out, err = io.StringIO(), io.StringIO()
        stdin = raw if raw is not None else json.dumps(self.payload if payload is None else payload)
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(["-C", str(self.root), "journal", "append", *options])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        return code, json.loads(out.getvalue())

    def plan(self, project):
        task = self.authority / "TASKS.md"
        acceptance = self.authority / "ACCEPTANCE.md"
        return mutate.Plan({task: task.read_bytes() + b"\n",
                            acceptance: acceptance.read_bytes() + b"\n"}, "T-ONE", "TASKS.md#T-ONE")


class TestJournalWrite(WriteCase):
    def test_payload_refusals_are_json_and_leave_all_bytes(self):
        before = self.snapshot()
        cases = [
            ("{", "invalid-json", None),
            ('{"title":"a","body":"b","title":"c"}', "invalid-json", None),
            ('{"title":"a","body":NaN}', "invalid-json", None),
            ('[]', "wrong-type", None),
            ('null', "wrong-type", None),
            (json.dumps({**self.payload, "extra": 1}), "unknown-field", "extra"),
            (json.dumps({**self.payload, "title": 4}), "wrong-type", "title"),
            (json.dumps({**self.payload, "body": False}), "wrong-type", "body"),
            ('{"body":"hello"}', "missing-field", "title"),
            ('{"title":"hello"}', "missing-field", "body"),
        ]
        for raw, error, field in cases:
            with self.subTest(raw=raw):
                code, result = self.run_cli("--input", "-", raw=raw)
                self.assertEqual(code, 1)
                self.assertFalse(result["ok"])
                self.assertFalse(result["written"])
                self.assertEqual(result["operation"], "journal.append")
                self.assertEqual(result["error"]["code"], error)
                self.assertEqual(result["error"].get("field"), field)
                self.assertEqual(self.snapshot(), before)

    def test_invalid_dates_titles_and_splitting_bodies(self):
        before = self.snapshot()
        for field, value in [("date", "2026-02-30"), ("date", "20261015"),
                             ("date", "2026-1-01"), ("title", ""), ("title", "  "),
                             ("title", "two\nlines"), ("title", "trailing\r"),
                             ("body", "text\n## Split\ntext"), ("body", "# Split"),
                             ("body", "text\r## Split")]:
            with self.subTest(field=field, value=value):
                code, result = self.run_cli("--input", "-", payload={**self.payload, field: value})
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["field"], field)
                self.assertEqual(self.snapshot(), before)

    def test_append_lf_crlf_preserves_existing_bytes_and_round_trips_unicode(self):
        for newline in (b"\n", b"\r\n"):
            for trailing in (b"", newline, newline * 2):
                with self.subTest(newline=newline, trailing=trailing):
                    original = "# Journal\n\n## 2026-01-01 — Start\n\nEarlier.".encode().replace(b"\n", newline) + trailing
                    self.journal.write_bytes(original)
                    before = self.snapshot()
                    code, result = self.run_cli("--input", "-")
                    self.assertEqual(code, 0, result)
                    written = self.journal.read_bytes()
                    self.assertTrue(written.startswith(original))
                    if newline == b"\r\n":
                        self.assertNotIn(b"\n", written.replace(b"\r\n", b""))
                    else:
                        self.assertNotIn(b"\r", written)
                    sections = _sections(written.decode().replace("\r\n", "\n"), "##")
                    self.assertEqual(sections[-1][0], self.heading)
                    self.assertEqual(sections[-1][1].strip(), self.payload["body"])
                    self.assertEqual(result, {
                        "ok": True, "operation": "journal.append", "recordId": self.heading,
                        "source": f"JOURNAL.md#{self.heading}",
                        "revision": mutate.section(written, self.heading).revision,
                        "validation": "passed", "written": True, "refreshed": True,
                    })
                    resolved = retrieve.Chronicle(self.root).section("JOURNAL.md", self.heading)
                    self.assertIn(self.payload["body"], resolved)
                    for path, data in before.items():
                        if path.name not in ("JOURNAL.md", "INDEX.md"):
                            self.assertEqual((self.authority / path).read_bytes(), data)
                    for name in ("project.json", "task-graph.mmd", "dashboard.html"):
                        self.assertTrue((self.root / layout.COMPILED_DIR / name).is_file())

    def test_duplicate_heading_working_or_any_archive_is_refused(self):
        self.assertTrue(journal.append(self.root, self.payload)["ok"])
        before = self.snapshot()
        code, result = self.run_cli("--input", "-")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "duplicate-heading")
        self.assertEqual(self.snapshot(), before)
        self.journal.write_bytes(b"# Journal\n")
        target = self.authority / "archive/unknown-phase/JOURNAL.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(f"# Archived\r\n\r\n## {self.heading}\r\n\r\nPast.\r\n".encode())
        before = self.snapshot()
        code, result = self.run_cli("--input", "-")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "duplicate-heading")
        self.assertEqual(self.snapshot(), before)

    def test_default_date_missing_journal_and_json_file_input(self):
        self.journal.unlink()
        payload = {"title": "First", "body": ""}
        source = self.root / "entry.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        code, result = self.run_cli("--input", str(source))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["recordId"], f"{dt.date.today().isoformat()} — First")
        self.assertTrue(self.journal.read_bytes().startswith(b"# Journal\n\n## "))

    def test_title_whitespace_is_preserved_and_locator_resolves(self):
        payload = {**self.payload, "title": "  Progress  "}
        result = journal.append(self.root, payload)
        self.assertTrue(result["ok"], result)
        self.assertIn(b"## 2026-10-15 \xe2\x80\x94   Progress  \n", self.journal.read_bytes())
        anchor = result["source"].split("#", 1)[1]
        self.assertIsNotNone(retrieve.Chronicle(self.root).section("JOURNAL.md", anchor))
        self.assertEqual(journal.append(self.root, payload)["error"]["code"], "duplicate-heading")

    def test_schema_example_and_dry_run(self):
        before = self.snapshot()
        code, schema = self.run_cli("--schema")
        self.assertEqual(code, 0)
        mutate.validate_payload(schema["example"], schema["schema"])
        code, result = self.run_cli("--input", "-", "--dry-run", payload=schema["example"])
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])
        self.assertFalse(result["written"])
        self.assertFalse(result["refreshed"])
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["wouldWrite"][0]["file"], "JOURNAL.md")
        self.assertIn("recordId", result)
        self.assertIn("source", result)
        self.assertRegex(result["revision"], r"^[0-9a-f]{16}$")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / layout.RECOVERY_DIR).exists())
        self.assertTrue(journal.append(self.root, schema["example"])["ok"])

    def test_schema_needs_no_chronicle_and_missing_input_is_json(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["-C", str(self.root / "absent"), "journal", "append", "--schema"]), 0)
        self.assertIn("example", json.loads(output.getvalue()))
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "missing-input")
        code, result = self.run_cli("--input", str(self.root / "absent.json"))
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "invalid-json")

    def test_refresh_failures_leave_one_entry_and_refresh_only_retry_completes(self):
        # Fail each stage after authority has committed, including partial projections.
        for target in ("compiler.write", "mermaid.render_all", "dashboard.render"):
            with self.subTest(target=target):
                self.journal.write_bytes(b"# Journal\n")
                with mock.patch("praukron.mutate." + target, side_effect=OSError("projection failed")):
                    code, result = self.run_cli("--input", "-")
                self.assertEqual(code, 1)
                self.assertFalse(result["ok"])
                self.assertTrue(result["written"])
                self.assertFalse(result["refreshed"])
                self.assertEqual(result["error"]["code"], "refresh-failed")
                committed = self.journal.read_bytes()
                self.assertEqual(committed.count(f"## {self.heading}".encode()), 1)
                with redirect_stdout(io.StringIO()):
                    for command in ("compile", "graph", "dashboard"):
                        self.assertEqual(cli.main(["-C", str(self.root), command]), 0)
                self.assertEqual(self.journal.read_bytes(), committed)
                code, result = self.run_cli("--input", "-")
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "duplicate-heading")
                self.assertEqual(self.journal.read_bytes(), committed)

    def test_cli_process_reads_utf8_stdin_and_file(self):
        command = [sys.executable, str(Path(cli.__file__).resolve().parents[2] / ".praukron/praukron"),
                   "-C", str(self.root), "journal", "append", "--input", "-"]
        result = subprocess.run(command, input=json.dumps(self.payload, ensure_ascii=False).encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["written"])
        self.assertIn(self.payload["body"], self.journal.read_text(encoding="utf-8"))
        source = self.root / "unicode.json"
        source.write_text(json.dumps({**self.payload, "title": "Tiếp tục"}, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run([*command[:-1], str(source)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        result = subprocess.run(command, input=b"\xff", capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid-json")

    def test_payload_bom_is_accepted_from_file_and_text_or_binary_stdin(self):
        for source in ("file", "text-stdin", "binary-stdin"):
            with self.subTest(source=source):
                payload = {**self.payload, "title": f"BOM {source}"}
                raw = json.dumps(payload, ensure_ascii=False)
                if source == "file":
                    path = self.root / "bom.json"
                    path.write_text(raw, encoding="utf-8-sig")
                    code, result = self.run_cli("--input", str(path))
                elif source == "text-stdin":
                    code, result = self.run_cli("--input", "-", raw="\ufeff" + raw)
                else:
                    out, err = io.StringIO(), io.StringIO()
                    with io.TextIOWrapper(io.BytesIO(raw.encode("utf-8-sig")), encoding="cp1252") as stdin:
                        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin):
                            code = cli.main(["-C", str(self.root), "journal", "append", "--input", "-"])
                    self.assertEqual(err.getvalue(), "")
                    result = json.loads(out.getvalue())
                self.assertEqual(code, 0, result)
                expected_heading = f"2026-10-15 — BOM {source}"
                self.assertEqual(result["recordId"], expected_heading)
                resolved = retrieve.Chronicle(self.root).section("JOURNAL.md", expected_heading)
                self.assertIn(self.payload["body"], resolved)

    def test_append_with_earlier_longer_titles_returns_exact_section_and_revision(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                original = ("# Journal\n\n## 2026-10-15 — Progress — detail\n\nOlder dash.\n\n"
                            "## 2026-10-15 — Progress: detail\n\nOlder colon.\n\n").encode().replace(b"\n", newline)
                expected = f"## {self.heading}\n\n{self.payload['body']}\n".encode().replace(b"\n", newline)
                self.journal.write_bytes(original)
                with self.assertRaises(mutate.WriteError) as missing:
                    mutate.section(original, self.heading)
                self.assertEqual(missing.exception.code, "record-not-found")
                code, result = self.run_cli("--input", "-")
                self.assertEqual(code, 0, result)
                self.assertEqual(self.journal.read_bytes(), original + expected)
                expected_hash = hashlib.sha256(expected.rstrip(b"\r\n")).hexdigest()[:16]
                self.assertEqual(result["revision"], expected_hash)
                for found in (mutate.section(original + expected, self.heading),
                              mutate.working_section(self.root, Source("JOURNAL.md", self.heading))):
                    self.assertEqual(found.data, expected)
                    self.assertEqual(found.span, (len(original), len(original) + len(expected)))
                    self.assertEqual(found.revision, expected_hash)

    def test_append_keeps_previous_record_revision_stable(self):
        for newline in (b"\n", b"\r\n"):
            for trailing in (b"", newline, newline * 3):
                with self.subTest(newline=newline, trailing=trailing):
                    header = b"# Journal" + newline * 2
                    record = "## 2026-01-01 — Start\n\nEarlier.".encode().replace(b"\n", newline) + trailing
                    self.journal.write_bytes(header + record)
                    expected_hash = hashlib.sha256(record.rstrip(b"\r\n")).hexdigest()[:16]
                    before = mutate.section(self.journal.read_bytes(), "2026-01-01 — Start")
                    self.assertEqual(before.data, record)
                    self.assertEqual(before.revision, expected_hash)
                    self.assertTrue(journal.append(self.root, self.payload)["ok"])
                    after = mutate.section(self.journal.read_bytes(), "2026-01-01 — Start")
                    self.assertEqual(after.revision, expected_hash)
                    self.assertEqual(after.revision, before.revision)
                    self.assertEqual(mutate.revision(record + b"\r\n\r\n"), expected_hash)
                    self.assertNotEqual(mutate.revision(record.rstrip(b"\r\n") + b" "), expected_hash)

    def test_lock_release_failure_after_commit_retains_written_metadata(self):
        original = self.journal.read_bytes()
        entry = f"## {self.heading}\n\n{self.payload['body']}\n".encode()
        expected = original + b"\n" + entry
        lock_path = self.root / layout.COMPILED_DIR / "respond.lock"
        for method in ("read_text", "unlink"):
            with self.subTest(method=method):
                self.journal.write_bytes(original)
                before = self.snapshot()
                real = getattr(Path, method)

                def fail_release(path, *args, **kwargs):
                    if path == lock_path:
                        raise PermissionError(f"injected lock {method} failure")
                    return real(path, *args, **kwargs)

                try:
                    with mock.patch.object(Path, method, fail_release), mock.patch.object(mutate, "refresh") as refresh:
                        code, result = self.run_cli("--input", "-")
                    self.assertEqual(code, 1)
                    self.assertEqual(result, {
                        "ok": False, "operation": "journal.append", "recordId": self.heading,
                        "source": f"JOURNAL.md#{self.heading}",
                        "revision": hashlib.sha256(entry.rstrip(b"\r\n")).hexdigest()[:16],
                        "validation": "passed", "written": True, "refreshed": False,
                        "error": {"code": "lock-release-failed", "message": f"injected lock {method} failure"},
                    })
                    self.assertEqual(self.snapshot(), {**before, Path("JOURNAL.md"): expected})
                    self.assertEqual(mutate._recovery_records(self.root), [])
                    refresh.assert_not_called()
                finally:
                    lock_path.unlink(missing_ok=True)
                code, result = self.run_cli("--input", "-")
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "duplicate-heading")
                self.assertEqual(self.journal.read_bytes(), expected)

    def test_concurrent_identical_appends_serialize_and_refuse_the_retry(self):
        barrier = threading.Barrier(2)
        results = []

        def writer():
            barrier.wait(timeout=10)
            results.append(journal.append(self.root, self.payload))

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertEqual([r["error"]["code"] for r in results if not r["ok"]], ["duplicate-heading"])
        self.assertEqual(self.journal.read_bytes().count(f"## {self.heading}".encode()), 1)


class TestMutationPipeline(WriteCase):
    def test_new_validation_error_rolls_back_all_files(self):
        before = self.snapshot()
        real = validate.check

        def poisoned(project):
            found = real(project)
            if self.journal.read_bytes() != before[Path("JOURNAL.md")]:
                found.append(validate.Finding("error", "injected", "bad mutation", "JOURNAL.md"))
            return found

        with mock.patch.object(validate, "check", poisoned):
            code, result = self.run_cli("--input", "-")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(mutate._recovery_records(self.root), [])

    def test_post_replace_load_failure_restores_existing_and_absent_files(self):
        real = compiler.load
        for absent in (False, True):
            if absent:
                self.journal.unlink()
            before = self.snapshot()
            calls = 0

            def broken(root):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("cannot parse new authority")
                return real(root)

            with mock.patch.object(compiler, "load", broken):
                code, result = self.run_cli("--input", "-")
            self.assertEqual(code, 1)
            self.assertFalse(result["written"])
            self.assertEqual(self.snapshot(), before)

    def test_multifile_failure_restores_every_byte(self):
        before = self.snapshot()
        real = mutate._replace
        calls = 0

        def failing(path, data):
            nonlocal calls
            if self.authority in path.parents:
                calls += 1
                if calls == 2:
                    raise OSError("second swap failed")
            return real(path, data)

        with mock.patch.object(mutate, "_replace", failing):
            result = mutate.transaction(self.root, "test.batch", self.plan)
        self.assertFalse(result["ok"])
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)

    def test_killed_multifile_write_is_recovered_on_next_command(self):
        before = self.snapshot()
        real = mutate._replace

        def killed(path, data):
            real(path, data)
            if path.name == "TASKS.md":
                raise SystemExit("killed after first authoritative replace")

        with mock.patch.object(mutate, "_replace", killed):
            with self.assertRaises(SystemExit):
                mutate.transaction(self.root, "test.batch", self.plan)
        self.assertNotEqual(self.snapshot(), before)
        self.assertTrue(mutate._recovery_records(self.root))
        self.assertIn("interrupted-write", {f.code for f in validate.check(compiler.load(self.root))})
        code, result = self.run_cli("--input", "-")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "interrupted-write")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(mutate._recovery_records(self.root), [])

    def test_killed_single_file_write_is_recovered_by_respond(self):
        before = self.snapshot()
        real = mutate._replace

        def killed(path, data):
            real(path, data)
            if path == self.journal:
                raise SystemExit("killed after journal replace")

        with mock.patch.object(mutate, "_replace", killed):
            with self.assertRaises(SystemExit):
                journal.append(self.root, self.payload)
        with self.assertRaisesRegex(respond.RespondError, "rolled back"):
            respond.append(self.root, [respond.Item("T-ONE", "GUIDE", "hello")], "owner")
        self.assertEqual(self.snapshot(), before)

    def test_existing_errors_are_allowed_but_new_errors_are_undone(self):
        task = self.authority / "TASKS.md"
        task.write_bytes(task.read_bytes().replace(b"- Dependencies: T-ONE", b"- Dependencies: T-MISSING"))
        self.assertIn("unknown-dependency", {f.code for f in validate.check(compiler.load(self.root))})
        self.assertTrue(journal.append(self.root, self.payload)["ok"])
        before = self.snapshot()

        def broken(project):
            plan = self.plan(project)
            plan.changes[task] = task.read_bytes().replace(b"- Dependencies: T-TWO", b"- Dependencies: T-OTHER")
            return plan

        result = mutate.transaction(self.root, "test.batch", broken)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertEqual(self.snapshot(), before)

    def test_revision_byte_spans_and_heading_boundaries(self):
        data = "Préface\r\n## T-ONE: Một\r\nBody\r\n### Child\r\nNested\r\n# Boundary\r\nOutside\r\n## T-ONEX: Two\r\n".encode()
        found = mutate.section(data, "T-ONE")
        self.assertEqual(found.data, data[found.span[0]:found.span[1]])
        self.assertEqual(found.data, "## T-ONE: Một\r\nBody\r\n### Child\r\nNested\r\n".encode())
        self.assertEqual(found.revision, hashlib.sha256(found.data.rstrip(b"\r\n")).hexdigest()[:16])
        self.assertEqual(mutate.revision(found.data), mutate.revision(bytes(found.data)))
        self.assertNotEqual(found.revision, mutate.revision(found.data + b"x"))
        with self.assertRaises(mutate.WriteError):
            mutate.section(data, "T-ON")
        source = Source("TASKS.md", "T-ONE")
        self.assertEqual(mutate.working_section(self.root, source).data,
                         mutate.section((self.authority / source.file).read_bytes(), source.anchor).data)

    def test_archive_immutability_and_id_lookup_in_archives(self):
        source = Source("archive/P1/TASKS.md", "T-ARCHIVED")
        for name in (source, "archive\\P1\\TASKS.md"):
            with self.assertRaisesRegex(mutate.WriteError, "immutable"):
                mutate.ensure_mutable(name)
        tasks = self.authority / source.file
        tasks.parent.mkdir(parents=True)
        tasks.write_bytes((self.authority / "TASKS.md").read_bytes().replace(b"T-ONE", b"T-ARCHIVED"))
        project = compiler.load(self.root)
        for record_id in ("T-ARCHIVED", "AC-T-ONE", "AC-T-ONE-01", "M-FOUNDATION", "ADR-001"):
            self.assertTrue(mutate.id_exists(project, record_id), record_id)
        self.assertFalse(mutate.id_exists(project, "T-NOPE"))
        before = self.snapshot()
        result = mutate.transaction(self.root, "test.update", lambda p: mutate.Plan(
            {tasks: tasks.read_bytes() + b"\n"}, source.anchor, f"{source.file}#{source.anchor}"))
        self.assertEqual(result["error"]["code"], "archived-record")
        self.assertEqual(self.snapshot(), before)

    def test_schema_enums_use_model_vocabularies_and_exact_types(self):
        schema = {"properties": {"domain": {"type": "string", "enum": DOMAINS},
                                 "number": {"type": "integer"}}, "required": ["domain"]}
        self.assertEqual(mutate.validate_payload({"domain": DOMAINS[0]}, schema), {"domain": DOMAINS[0]})
        for payload in ({"domain": "invalid"}, {"domain": DOMAINS[0], "number": True}):
            with self.assertRaises(mutate.WriteError):
                mutate.validate_payload(payload, schema)

    def test_section_id_lookup_uses_only_the_first_delimiter(self):
        for record_id, delimiter in (("T-ONE", ": "), ("AC-T-ONE", " — ")):
            for newline in (b"\n", b"\r\n"):
                with self.subTest(record_id=record_id, newline=newline):
                    prefix = f"# Records\n\n## {record_id}X{delimiter}Other\n\nEarlier.\n\n".encode().replace(b"\n", newline)
                    expected = f"## {record_id}{delimiter}Alpha: detail — more\n\nNội dung.\n\n".encode().replace(b"\n", newline)
                    suffix = b"# Boundary" + newline + b"Outside." + newline
                    data = prefix + expected + suffix
                    found = mutate.section(data, record_id)
                    self.assertEqual(found.data, expected)
                    self.assertEqual(found.span, (len(prefix), len(prefix) + len(expected)))
                    self.assertEqual(found.revision, hashlib.sha256(expected.rstrip(b"\r\n")).hexdigest()[:16])
                    for missing in (record_id[:-1], f"{record_id}{delimiter}Alpha",
                                    f"{record_id}{delimiter}Alpha: detail"):
                        with self.assertRaises(mutate.WriteError):
                            mutate.section(data, missing)

    def test_published_schema_enforces_min_length_and_date_format(self):
        for patch, field, code in (({"title": ""}, "title", "invalid-value"),
                                   ({"date": "2026-02-29"}, "date", "invalid-date"),
                                   ({"date": "20261015"}, "date", "invalid-date"),
                                   ({"date": "2026-W42-4"}, "date", "invalid-date"),
                                   ({"date": "2026-1-01"}, "date", "invalid-date")):
            with self.subTest(patch=patch):
                with self.assertRaises(mutate.WriteError) as refused:
                    mutate.validate_payload({**self.payload, **patch}, journal.SCHEMA)
                self.assertEqual(refused.exception.field, field)
                self.assertEqual(refused.exception.code, code)
        valid = {**self.payload, "date": "2024-02-29", "title": "Đ"}
        self.assertEqual(mutate.validate_payload(valid, journal.SCHEMA), valid)
        schema = {"properties": {"text": {"type": "string", "minLength": 2}}}
        self.assertEqual(mutate.validate_payload({"text": "Đã"}, schema), {"text": "Đã"})
        with self.assertRaises(mutate.WriteError):
            mutate.validate_payload({"text": "Đ"}, schema)

    def test_pipeline_and_existing_writers_share_the_same_lock(self):
        self.assertIs(mutate._lock, respond._lock)
        self.assertIs(archive._lock, respond._lock)
        lock = respond._lock(self.root)
        try:
            before = self.snapshot()
            with mock.patch.object(respond.time, "monotonic", side_effect=(0, 11)):
                result = journal.append(self.root, self.payload)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], {"code": "locked", "message": "another write is in progress; try again"})
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(lock.path.is_file())
            with mock.patch.object(respond.time, "monotonic", side_effect=(0, 11)):
                with self.assertRaisesRegex(respond.RespondError, "another write is in progress; try again"):
                    respond._lock(self.root)
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
