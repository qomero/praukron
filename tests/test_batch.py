"""Atomic composition of the five governed write operations."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from test_task_update import UpdateCase
from praukron import acr, batch, cli, compile as compiler, mutate


class BatchCase(UpdateCase):
    def operation(self, name, **payload):
        return {"op": name.replace(".", " "), **payload}

    def lifecycle(self):
        revision = self.revision()
        contract_revision = mutate.section((self.authority / "ACCEPTANCE.md").read_bytes(), "AC-T-TWO").revision
        return [
            self.operation("task.update", id="T-TWO", expectRevision=revision,
                           set={"status": "WIP", "owner": "worker"}),
            self.operation("criterion.set", criterion="AC-T-TWO-01", expectRevision=contract_revision,
                           state="PASS", evidence="Synthetic check."),
            self.operation("task.update", id="T-TWO", expectRevision=revision,
                           set={"status": "DONE", "evidence": "Synthetic check."}),
            self.operation("journal.append", date="2026-10-16", title="Finished", body="Checked T-TWO. Đã xong."),
        ]

    def apply(self, operations, **options):
        return batch.apply(self.root, {"operations": operations}, **options)


class TestBatch(BatchCase):
    def test_lifecycle_orders_operations_and_commits_once_lf_and_crlf(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                self.convert(newline)
                original = self.snapshot()
                with mock.patch.object(mutate, "_lock", wraps=mutate._lock) as lock, \
                     mock.patch.object(mutate, "_errors", wraps=mutate._errors) as validation, \
                     mock.patch.object(mutate, "refresh", wraps=mutate.refresh) as refresh:
                    result = self.apply(self.lifecycle())
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["written"] and result["refreshed"])
                self.assertEqual(lock.call_count, 1)
                self.assertEqual(validation.call_count, 2)  # One baseline, one final proposed state.
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual([r["index"] for r in result["results"]], list(range(4)))
                task = compiler.load(self.root).task("T-TWO")
                self.assertEqual((task.status, task.owner, task.evidence), ("DONE", "worker", "Synthetic check."))
                self.assertEqual(result["revisions"]["TASKS.md#T-TWO"], self.revision())
                self.assertEqual(result["results"][2]["revision"], self.revision())
                self.assertNotEqual(result["results"][0]["revision"], self.revision())
                journal = self.authority / "JOURNAL.md"
                self.assertIn("Đã xong.", journal.read_text(encoding="utf-8"))
                self.assertEqual(result["revisions"]["JOURNAL.md#2026-10-16 — Finished"],
                                 mutate.section(journal.read_bytes(), "2026-10-16 — Finished").revision)
                if newline == b"\r\n":
                    for name in ("TASKS.md", "ACCEPTANCE.md"):
                        self.assertNotIn(b"\n", (self.authority / name).read_bytes().replace(b"\r\n", b""))
                for path in self.authority.rglob("*"):
                    if path.is_file() and path.relative_to(self.authority) not in original:
                        path.unlink()
                for name, data in original.items():
                    (self.authority / name).write_bytes(data)

    def test_shared_contract_revision_can_be_reused_for_sibling_criteria(self):
        self.rewrite("ACCEPTANCE.md", "## AC-T-THREE", "- `AC-T-TWO-02` — Given walls, When checked, Then they stand.\n  `TEST` · `NOT_RUN`\n\n## AC-T-THREE")
        operations = self.lifecycle()
        sibling = {**operations[1], "criterion": "AC-T-TWO-02"}
        operations.insert(2, sibling)
        result = self.apply(operations)
        self.assertTrue(result["ok"], result)
        contract = compiler.load(self.root).contracts["AC-T-TWO"]
        self.assertEqual([c.state for c in contract.criteria], ["PASS", "PASS"])
        self.assertEqual(result["revisions"]["ACCEPTANCE.md#AC-T-TWO"], result["results"][2]["revision"])

    def test_task_add_then_criterion_and_task_updates_use_previous(self):
        operations = [self.operation("task.add", id="T-NEW", title="New work", module="M-FOUNDATION",
            domain="execution", dependencies=[], contract={"id": "AC-T-NEW", "title": "New work",
                "criteria": [{"text": "Given work, When checked, Then it passes.", "class": "TEST"}]}),
            self.operation("criterion.set", criterion="AC-T-NEW-01", expectRevision="$previous", state="PASS"),
            self.operation("task.update", id="T-NEW", expectRevision="$previous",
                           set={"status": "DONE", "evidence": "Checked."})]
        result = self.apply(operations)
        self.assertTrue(result["ok"], result)
        self.assertTrue(compiler.load(self.root).task("T-NEW").done)
        self.assertIn("ACCEPTANCE.md#AC-T-NEW", result["revisions"])

    def test_contract_add_is_visible_to_later_operations(self):
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: none")
        revision = self.revision()
        result = self.apply([
            self.operation("contract.add", id="AC-T-NEW", task="T-TWO", title="New contract",
                expectTaskRevision=revision,
                criteria=[{"text": "Given work, When checked, Then it passes.", "class": "TEST"}]),
            self.operation("criterion.set", criterion="AC-T-NEW-01", expectRevision="$previous", state="PASS"),
            self.operation("task.update", id="T-TWO", expectRevision=revision,
                           set={"status": "DONE", "evidence": "Checked."}),
        ])
        self.assertTrue(result["ok"], result)
        self.assertEqual(compiler.load(self.root).task("T-TWO").contract, "AC-T-NEW")

    def test_refused_operations_leave_every_byte_and_name_the_index(self):
        cases = [
            (self.operation("task.update", id="T-TWO", expectRevision="0" * 16, set={"owner": "worker"}), "stale-revision"),
            (self.operation("task.update", id="T-TWO", expectRevision=self.revision(),
                            set={"status": "DONE", "evidence": "Checked."}), "unresolved-acceptance"),
            (self.operation("contract.add", id="AC-T-NEW", task="T-ONE", title="New contract",
                expectTaskRevision=self.revision("T-ONE"), criteria=[{"text": "Given work, When checked, Then it passes.", "class": "TEST"}]), "frozen-contract"),
            (self.operation("task.update", id="T-TWO", expectRevision="$previous", set={"owner": "worker"}), "invalid-revision"),
            (self.operation("batch.apply", operations=[]), "invalid-value"),
        ]
        before = self.snapshot()
        first = self.operation("journal.append", title="Staged only", body="Never committed.")
        for operation, code in cases:
            with self.subTest(code=code), mock.patch.object(mutate, "refresh") as refresh:
                result = self.apply([first, operation])
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["written"])
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["error"]["index"], 1)
                self.assertEqual(result["error"]["op"], operation["op"])
                self.assertEqual(self.snapshot(), before)
                refresh.assert_not_called()

    def test_owner_held_and_archived_refusals_leave_every_byte(self):
        self.rewrite("TASKS.md", "## T-TWO: Build on it", "## T-TWO: Build on it\n- Authority: owner")
        operations = self.lifecycle()
        before = self.snapshot()
        result = self.apply(operations)
        self.assertEqual(result["error"]["code"], "owner-held")
        self.assertEqual(result["error"]["index"], 2)
        self.assertEqual(self.snapshot(), before)
        archive = self.authority / "archive" / "P1"
        archive.mkdir(parents=True)
        for name in ("TASKS.md", "ACCEPTANCE.md"):
            path = self.authority / name
            (archive / name).write_bytes(path.read_bytes())
            path.write_bytes(f"# {name}\n".encode())
        before = self.snapshot()
        result = self.apply(operations)
        self.assertEqual(result["error"]["code"], "archived-record")
        self.assertEqual(self.snapshot(), before)

    def test_later_operation_checks_staged_governance_not_original_state(self):
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: none")
        result = self.apply([
            self.operation("task.update", id="T-TWO", expectRevision=self.revision(), set={"status": "WIP", "owner": "worker"}),
            self.operation("contract.add", id="AC-T-NEW", task="T-TWO", title="New contract", expectTaskRevision="$previous",
                criteria=[{"text": "Given work, When checked, Then it passes.", "class": "TEST"}]),
        ])
        self.assertEqual(result["error"]["code"], "frozen-contract")
        self.assertEqual(result["error"]["index"], 1)
        self.assertEqual(compiler.load(self.root).task("T-TWO").status, "TODO")

    def test_duplicate_journal_heading_is_seen_in_overlay(self):
        entry = self.operation("journal.append", title="Only once", body="No duplicates.")
        before = self.snapshot()
        result = self.apply([entry, entry])
        self.assertEqual(result["error"]["code"], "duplicate-heading")
        self.assertEqual(result["error"]["index"], 1)
        self.assertEqual(self.snapshot(), before)

    def test_final_validation_and_dry_run_write_nothing(self):
        operations = self.lifecycle()
        operations.append(self.operation("criterion.set", criterion="AC-T-TWO-01",
            expectRevision=operations[1]["expectRevision"], state="FAIL"))
        before = self.snapshot()
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), mock.patch.object(mutate, "_replace") as persist:
                result = self.apply(operations, dry_run=dry_run)
                self.assertEqual(result["error"]["code"], "validation-failed")
                self.assertEqual(result["error"]["stage"], "validation")
                self.assertEqual(self.snapshot(), before)
                persist.assert_not_called()
        with mock.patch.object(mutate, "refresh") as refresh:
            result = self.apply(self.lifecycle(), dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dryRun"])
        self.assertFalse(result["written"] or result["refreshed"])
        self.assertTrue(result["wouldWrite"])
        self.assertEqual(self.snapshot(), before)
        refresh.assert_not_called()

    def test_persistence_failure_restores_existing_and_absent_files(self):
        before = self.snapshot()
        replace = mutate._replace
        failed = False

        def failing(path, data):
            nonlocal failed
            if path.name == "TASKS.md" and not failed:
                failed = True
                raise OSError("injected disk failure")
            return replace(path, data)

        with mock.patch.object(mutate, "_replace", side_effect=failing), mock.patch.object(mutate, "refresh") as refresh:
            result = self.apply(self.lifecycle())
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["written"])
        self.assertTrue(failed)
        self.assertEqual(self.snapshot(), before)
        refresh.assert_not_called()

    def test_refresh_failure_reports_committed_writes(self):
        with mock.patch.object(mutate, "refresh", side_effect=OSError("injected refresh failure")):
            result = self.apply(self.lifecycle())
        self.assertFalse(result["ok"])
        self.assertTrue(result["written"])
        self.assertFalse(result["refreshed"])
        self.assertEqual(result["error"]["code"], "refresh-failed")
        self.assertTrue(compiler.load(self.root).task("T-TWO").done)

    def test_schema_cli_and_payload_refusals(self):
        for payload, code in (({"operations": []}, "invalid-value"), ({"operations": [4]}, "wrong-type"),
                              ({"operations": [{"op": "task update"}]}, "missing-field"),
                              ({"operations": [{"id": "T-TWO"}]}, "missing-field"),
                              ({"operations": [{"op": "task.update"}]}, "invalid-value")):
            result = batch.apply(self.root, payload)
            self.assertEqual(result["error"]["code"], code)
        for options, stdin in ((("--schema",), ""), (("--input", "-", "--dry-run"), json.dumps({"operations": self.lifecycle()}))):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
                code = cli.main(["-C", str(self.root), "batch", "apply", *options])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            result = json.loads(out.getvalue())
            if "--schema" in options:
                choices = result["schema"]["properties"]["operations"]["items"]["oneOf"]
                self.assertEqual(len(choices), len(batch.OPERATIONS))
            else:
                self.assertTrue(result["ok"], result)

    def test_registry_accepts_an_additional_governed_operation(self):
        with mock.patch.dict(batch.OPERATIONS, {"journal note": batch.OPERATIONS["journal append"]}):
            result = self.apply([self.operation("journal.note", title="Extension", body="Reused governed handler.")])
        self.assertTrue(result["ok"], result)

    def test_two_journal_entries_on_the_same_day_have_distinct_final_revisions(self):
        result = self.apply([self.operation("journal.append", date="2026-10-16", title=title, body=title)
                             for title in ("First", "Second")])
        self.assertTrue(result["ok"], result)
        for item in result["results"]:
            self.assertEqual(result["revisions"][item["source"]], item["revision"])

    def test_acr_add_sees_a_claim_staged_earlier_in_the_batch(self):
        acr_entry = self.operation("acr.add", task="T-TWO", criterion="AC-T-TWO-01",
                                   proposed="Given walls, When measured, Then they stand.", reason="Untestable.")
        before = self.snapshot()
        result = self.apply([acr_entry])
        self.assertEqual(result["error"]["code"], "unfrozen-contract")
        self.assertEqual((result["error"]["index"], result["error"]["op"]), (0, "acr add"))
        self.assertEqual(self.snapshot(), before)
        result = self.apply([self.lifecycle()[0], acr_entry, acr_entry])
        self.assertEqual(result["error"]["code"], "duplicate-request")
        self.assertEqual(result["error"]["index"], 2)
        self.assertEqual(self.snapshot(), before)
        result = self.apply([self.lifecycle()[0], acr_entry])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][1]["op"], "acr add")
        requests = acr.requests((self.authority / "ACCEPTANCE.md").read_text(encoding="utf-8"))
        self.assertEqual([r["criterion"] for r in requests], ["AC-T-TWO-01"])
        self.assertEqual(compiler.load(self.root).task("T-TWO").status, "WIP")

    def test_stale_revision_after_a_staged_write_names_the_revision_to_retry_with(self):
        revision = self.revision()
        stale = self.operation("task.update", id="T-TWO", expectRevision="f" * 16, set={"owner": "other"})
        before = self.snapshot()
        result = self.apply([self.lifecycle()[0], stale])
        self.assertEqual(result["error"]["code"], "stale-revision")
        self.assertEqual(result["error"]["index"], 1)
        self.assertEqual(result["error"]["currentRevision"], revision)
        self.assertIn(revision, result["error"]["message"])
        self.assertNotIn("section", result["error"])
        self.assertEqual(self.snapshot(), before)
        retried = self.apply([self.lifecycle()[0], {**stale, "expectRevision": result["error"]["currentRevision"]}])
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(compiler.load(self.root).task("T-TWO").owner, "other")

    def test_untouched_stale_revision_keeps_the_command_refusal(self):
        result = self.apply([self.operation("task.update", id="T-TWO", expectRevision="f" * 16,
                                            set={"owner": "other"})])
        self.assertEqual(result["error"]["code"], "stale-revision")
        self.assertEqual(result["error"]["currentRevision"], self.revision())
        self.assertIn("section", result["error"])

    def test_a_shape_refusal_shows_an_accepted_entry_with_its_op(self):
        result = self.apply([self.operation("criterion.set", criterion="AC-T-TWO-01", state="PASS")])
        self.assertEqual(result["error"]["code"], "missing-field")
        self.assertEqual(result["error"]["example"]["op"], "criterion set")
        self.assertIn("expectRevision", result["error"]["example"])
        result = self.apply([self.operation("journal.append", title="Wrong", body="x", status="DONE")])
        self.assertEqual(result["error"]["code"], "unknown-field")
        self.assertEqual(result["error"]["example"]["op"], "journal append")

    def test_schema_example_is_one_entry_per_command_payload_plus_op(self):
        choices = batch.schema()["properties"]["operations"]["items"]["oneOf"]
        self.assertEqual([c["properties"]["op"]["enum"] for c in choices], [[name] for name in batch.OPERATIONS])
        self.assertEqual(sorted(batch.OPERATIONS), sorted(
            ["journal append", "criterion set", "task add", "contract add", "task update", "acr add"]))
        for choice in choices:
            self.assertEqual(choice["required"][0], "op")
        for entry in batch.EXAMPLE["operations"]:
            body = {k: v for k, v in entry.items() if k != "op"}
            mutate.validate_payload(body, batch.OPERATIONS[entry["op"]].schema)
