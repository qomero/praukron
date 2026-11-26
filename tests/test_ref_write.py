"""Structured writes of task-code references: ref add, ref set, ref history (AC-T-CIL-MAP-02)."""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, batch, cli, compile as compiler, layout, mutate, ref, refs, validate  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date.today().isoformat()

RESPONSES = """# Owner responses

## R-1: T-ONE GUIDE
- By: owner
- Date: 2026-01-03
- Via: dashboard

> T-ONE may change src/app/core.py.
"""

PROPOSED_ADR = """# ADR-002: Something not yet decided
- Date: 2026-01-01
- Status: PROPOSED
- Supersedes: none
- Affects: T-ONE
- Context: Unsettled.
- Decision: Not yet.
"""


def planned(**extra) -> dict:
    payload = {"task": "T-ONE", "title": "Run the core", "path": "src/app/core.py", "symbol": "Core.run",
               "kind": "method", "relation": "modifies", "scope": "planned", "source": "agent",
               "recordedBy": "agent/builder"}
    payload.update(extra)
    return payload


class RefCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.authority = self.root / layout.AUTHORITY_DIR
        self.code = self.authority / "CODE.md"
        (self.authority / "RESPONSES.md").write_text(RESPONSES, encoding="utf-8")
        (self.authority / "ADR" / "ADR-002.md").write_text(PROPOSED_ADR, encoding="utf-8")
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def add(self, **extra) -> dict:
        return ref.add(self.root, planned(**extra))

    def added(self, **extra) -> dict:
        result = self.add(**extra)
        self.assertTrue(result["ok"], result)
        return result

    def section(self, reference_id: str) -> bytes:
        """The record's bytes without the blank line that separates it from the next one."""
        return mutate.section(self.code.read_bytes(), reference_id).data.rstrip(b"\r\n")

    def fields(self, reference_id: str) -> dict:
        return ref._record_fields(self.section(reference_id))

    def compiled(self) -> dict:
        project = compiler.load(self.root)
        return {r["id"]: r for r in compiler.as_json(project)["references"] if not r["legacy"]}

    def set(self, reference_id: str, revision: str, **change) -> dict:
        payload = {"id": reference_id, "expectRevision": revision, "recordedBy": "agent/reviewer"}
        if "withdraw" in change or "replace" in change:
            payload["source"] = "agent"
        payload.update(change)
        return ref.set_reference(self.root, payload)

    def run_cli(self, *argv: str, stdin: str | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(stdin or "")):
            code = cli.main(["-C", str(self.root), *argv])
        return code, out.getvalue()


class TestAdd(RefCase):
    """AC-T-CIL-MAP-02-01: atomic, validated, provenance recorded."""

    def test_add_creates_code_md_from_the_template_header(self) -> None:
        self.assertFalse(self.code.exists())
        result = self.added()
        self.assertEqual(result["recordId"], "CR-T-ONE-01")
        self.assertEqual(result["source"], "CODE.md#CR-T-ONE-01")
        self.assertEqual(result["revision"], mutate.revision(self.section("CR-T-ONE-01")))
        self.assertTrue(result["written"] and result["refreshed"])
        self.assertTrue(self.code.read_text(encoding="utf-8").startswith(ref.HEADER))

    def test_header_is_the_template_without_its_placeholder(self) -> None:
        template = (ROOT / "templates" / "chronicle" / "CODE.md").read_text(encoding="utf-8")
        self.assertEqual(template.replace("\nNo references recorded yet.\n", ""), ref.HEADER)

    def test_record_carries_who_when_and_which_source(self) -> None:
        self.added(recordedOn="2026-02-03")
        fields = self.fields("CR-T-ONE-01")
        self.assertEqual(fields["Recorded"], "2026-02-03 · agent/builder")
        self.assertEqual(fields["Source"], "agent")
        compiled = self.compiled()["CR-T-ONE-01"]
        self.assertEqual((compiled["recorded"], compiled["source"]), ("2026-02-03 · agent/builder", "agent"))

    def test_recorded_date_defaults_to_today(self) -> None:
        self.added()
        self.assertEqual(self.fields("CR-T-ONE-01")["Recorded"], f"{TODAY} · agent/builder")

    def test_provenance_is_required(self) -> None:
        for missing in ("source", "recordedBy"):
            payload = planned()
            del payload[missing]
            result = ref.add(self.root, payload)
            self.assertEqual(result["error"]["code"], "missing-field", missing)
            self.assertIn("example", result["error"])
        self.assertFalse(self.code.exists())

    def test_graph_provider_needs_its_provider(self) -> None:
        result = self.add(source="graph_provider", scope="observed")
        self.assertEqual(result["error"]["code"], "missing-field")
        self.added(source="graph_provider", scope="observed", provider="codegraph 1.6.0")
        self.assertEqual(self.fields("CR-T-ONE-01")["Provider"], "codegraph 1.6.0")

    def test_identifiers_take_the_next_free_sequence_per_task(self) -> None:
        self.added()
        self.added(path="src/app/util.py", symbol="helper", kind="function")
        other = ref.add(self.root, planned(task="T-TWO"))
        self.assertEqual(other["recordId"], "CR-T-TWO-01")
        self.assertEqual(self.added(path="docs/a.md", symbol="x", kind="other")["recordId"], "CR-T-ONE-03")

    def test_optional_fields_are_written_only_when_given(self) -> None:
        self.added(scope="observed", source="git_diff", confidence=0.75, lines="10-20", commit="abc1234",
                   base="abc1234", head="def5678", verification="verified", evidence="diff shows it")
        fields = self.fields("CR-T-ONE-01")
        self.assertEqual((fields["Confidence"], fields["Lines"], fields["Revision"], fields["Verification"]),
                         ("0.75", "10-20", "abc1234", "verified"))
        self.added(path="src/app/other.py", symbol="Other", kind="class")
        self.assertNotIn("Confidence", self.fields("CR-T-ONE-02"))
        self.assertIsNone(self.compiled()["CR-T-ONE-02"]["confidence"])
        self.assertEqual(self.fields("CR-T-ONE-02")["Verification"], "unverified")

    def test_invalid_values_are_refused_before_writing(self) -> None:
        for extra, code in (({"path": "/etc/passwd"}, "invalid-reference-path"),
                            ({"path": "../out.py"}, "invalid-reference-path"),
                            ({"relation": "touches"}, "invalid-value"),
                            ({"scope": "maybe"}, "invalid-value"),
                            ({"confidence": 1.5}, "invalid-value"),
                            ({"kind": "thing"}, "invalid-value"),
                            ({"title": "two\nlines"}, "invalid-value"),
                            ({"task": "T-NOPE"}, "unknown-reference-task")):
            result = self.add(**extra)
            self.assertEqual(result["error"]["code"], code, extra)
            self.assertFalse(result["written"])
        payload = planned()
        del payload["kind"]
        self.assertEqual(ref.add(self.root, payload)["error"]["code"], "symbol-without-kind")
        self.assertFalse(self.code.exists())

    def test_a_result_that_fails_validation_is_undone_byte_for_byte(self) -> None:
        self.added()
        before = self.code.read_bytes()
        payload = planned(path="src/app/util.py")
        del payload["kind"]
        with mock.patch.object(ref, "_check_symbol", lambda fields: None):
            result = ref.add(self.root, payload)
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertIn("symbol-without-kind", result["error"]["message"])
        self.assertEqual(self.code.read_bytes(), before)

    def test_dry_run_writes_nothing(self) -> None:
        result = ref.add(self.root, planned(), dry_run=True)
        self.assertTrue(result["ok"] and result["dryRun"])
        self.assertEqual(result["wouldWrite"][0]["file"], "CODE.md")
        self.assertFalse(self.code.exists())

    def test_crlf_and_unrelated_bytes_are_preserved(self) -> None:
        original = ref.HEADER.replace("\n", "\r\n").encode("utf-8")
        self.code.write_bytes(original)
        self.added()
        data = self.code.read_bytes()
        self.assertTrue(data.startswith(original))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        revision = self.added(path="src/app/b.py")["revision"]
        result = self.set("CR-T-ONE-02", revision, set={"verification": "verified"})
        self.assertTrue(result["ok"], result)
        self.assertNotIn(b"\n", self.code.read_bytes().replace(b"\r\n", b""))

    def test_existing_records_are_untouched_by_an_append(self) -> None:
        self.added()
        first = self.section("CR-T-ONE-01")
        self.added(path="src/app/b.py")
        self.assertEqual(self.section("CR-T-ONE-01"), first)


class TestSet(RefCase):
    """AC-T-CIL-MAP-02-01: in-place changes are revision-checked and carry provenance."""

    def test_verify_records_the_writer_in_updated_and_returns_the_new_revision(self) -> None:
        revision = self.added()["revision"]
        result = self.set("CR-T-ONE-01", revision,
                          set={"verification": "verified", "evidence": "Core.run is defined", "lifecycle": "active"})
        self.assertTrue(result["ok"], result)
        fields = self.fields("CR-T-ONE-01")
        self.assertEqual(fields["Verification"], "verified")
        self.assertEqual(fields["Lifecycle"], "active")
        self.assertEqual(fields["Evidence"], "Core.run is defined")
        self.assertEqual(fields["Source"], "agent")
        self.assertEqual(fields["Recorded"], f"{TODAY} · agent/builder")
        self.assertEqual(fields["Updated"], f"{TODAY} · agent/reviewer")
        self.assertEqual(result["revision"], mutate.revision(self.section("CR-T-ONE-01")))
        lines = self.section("CR-T-ONE-01").decode("utf-8").splitlines()
        self.assertLess(lines.index("- Lifecycle: active"), lines.index("- Verification: verified"))

    def test_set_keeps_a_git_diff_origin_byte_for_byte_and_gains_updated(self) -> None:
        revision = self.added(scope="observed", source="git_diff", recordedOn="2026-02-03",
                              recordedBy="git/capture")["revision"]
        before = self.section("CR-T-ONE-01").decode("utf-8").splitlines()
        origin = [line for line in before if line.startswith(("- Source:", "- Recorded:"))]
        self.assertEqual(origin, ["- Source: git_diff", "- Recorded: 2026-02-03 · git/capture"])
        first = self.set("CR-T-ONE-01", revision, set={"verification": "verified"}, recordedOn="2026-02-04")
        self.assertTrue(first["ok"], first)
        after = self.section("CR-T-ONE-01").decode("utf-8").splitlines()
        self.assertEqual([line for line in after if line.startswith(("- Source:", "- Recorded:"))], origin)
        self.assertEqual(after[after.index(origin[1]) + 1], "- Updated: 2026-02-04 · agent/reviewer")
        second = self.set("CR-T-ONE-01", first["revision"], set={"lifecycle": "active"}, recordedBy="owner")
        after = self.section("CR-T-ONE-01").decode("utf-8").splitlines()
        self.assertEqual([line for line in after if line.startswith(("- Source:", "- Recorded:"))], origin)
        self.assertEqual([line for line in after if line.startswith("- Updated:")], [f"- Updated: {TODAY} · owner"])
        self.assertEqual(second["revision"], mutate.revision(self.section("CR-T-ONE-01")))
        self.assertEqual(self.compiled()["CR-T-ONE-01"]["source"], "git_diff")
        self.assertNotIn("updated", self.compiled()["CR-T-ONE-01"])

    def test_set_refuses_source_and_provider(self) -> None:
        revision = self.added()["revision"]
        before = self.code.read_bytes()
        for extra in ({"source": "agent"}, {"source": "graph_provider", "provider": "codegraph 1.6.0"},
                      {"provider": "codegraph 1.6.0"}):
            result = self.set("CR-T-ONE-01", revision, set={"verification": "verified"}, **extra)
            self.assertEqual(result["error"]["code"], "provenance-kept", extra)
        self.assertEqual(self.code.read_bytes(), before)
        result = ref.set_reference(self.root, {"id": "CR-T-ONE-01", "expectRevision": revision,
                                               "recordedBy": "agent", "withdraw": {"reason": "x"}})
        self.assertEqual((result["error"]["code"], result["error"]["field"]), ("missing-field", "source"))

    def test_stale_revision_is_refused_with_the_current_one(self) -> None:
        old = self.added()["revision"]
        new = self.set("CR-T-ONE-01", old, set={"verification": "verified"})["revision"]
        before = self.code.read_bytes()
        result = self.set("CR-T-ONE-01", old, set={"verification": "disputed"})
        self.assertEqual(result["error"]["code"], "stale-revision")
        self.assertEqual(result["error"]["currentRevision"], new)
        self.assertIn("- Verification: verified", result["error"]["section"])
        self.assertEqual(self.code.read_bytes(), before)

    def test_no_change_and_bad_shapes_are_refused(self) -> None:
        revision = self.added()["revision"]
        self.assertEqual(self.set("CR-T-ONE-01", revision, set={"verification": "unverified"})["error"]["code"],
                         "no-change")
        self.assertEqual(self.set("CR-T-ONE-01", revision)["error"]["code"], "invalid-change")
        self.assertEqual(self.set("CR-T-ONE-01", revision, set={"verification": "verified"},
                                  withdraw={"reason": "x"})["error"]["code"], "invalid-change")
        self.assertEqual(self.set("CR-T-NOPE-01", revision, set={"verification": "verified"})["error"]["code"],
                         "record-not-found")


class TestAuthorization(RefCase):
    """AC-T-CIL-MAP-02-02: planned scope is proposed; only an owner response or accepted ADR authorizes."""

    def test_planned_changes_are_proposed(self) -> None:
        for number, relation in enumerate(("creates", "modifies", "deletes"), 1):
            self.added(relation=relation, path=f"src/{relation}.py")
            fields = self.fields(f"CR-T-ONE-{number:02d}")
            self.assertEqual(fields["Authorization"], "proposed")
            self.assertNotIn("Authorized by", fields)

    def test_other_references_are_not_applicable(self) -> None:
        self.added(scope="observed", source="git_diff")
        self.added(relation="reads", path="src/b.py")
        self.assertEqual(self.fields("CR-T-ONE-01")["Authorization"], "not_applicable")
        self.assertEqual(self.fields("CR-T-ONE-02")["Authorization"], "not_applicable")
        result = self.add(relation="reads", path="src/c.py", authorizedBy="R-1")
        self.assertEqual(result["error"]["code"], "authorization-not-applicable")

    def test_owner_response_or_accepted_adr_authorizes(self) -> None:
        self.added(authorizedBy="R-1")
        self.added(path="src/b.py", authorizedBy="ADR-001")
        for reference_id, authority in (("CR-T-ONE-01", "R-1"), ("CR-T-ONE-02", "ADR-001")):
            fields = self.fields(reference_id)
            self.assertEqual((fields["Authorization"], fields["Authorized by"]), ("authorized", authority))
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_unknown_or_unaccepted_authority_is_refused(self) -> None:
        for authority in ("R-9", "ADR-002", "ADR-404", "owner said so"):
            result = self.add(authorizedBy=authority)
            self.assertEqual(result["error"]["code"], "unknown-authority", authority)
        self.assertFalse(self.code.exists())

    def test_no_payload_field_sets_authorization(self) -> None:
        for schema in (ref.ADD_SCHEMA, ref.SET_SCHEMA, ref.SET_SCHEMA["properties"]["set"],
                       ref.SET_SCHEMA["properties"]["replace"]):
            self.assertNotIn("authorization", schema["properties"])
        result = self.add(authorization="authorized")
        self.assertEqual(result["error"]["code"], "unknown-field")
        revision = self.added()["revision"]
        for change in ({"set": {"authorization": "authorized"}},
                       {"replace": {"authorization": "authorized"}},
                       {"authorization": "authorized", "set": {"verification": "verified"}}):
            result = self.set("CR-T-ONE-01", revision, **change)
            self.assertFalse(result["ok"], change)
            self.assertEqual(result["error"]["code"], "unknown-field", change)
        self.assertEqual(self.fields("CR-T-ONE-01")["Authorization"], "proposed")

    def test_a_replacement_is_authorized_only_through_authorized_by(self) -> None:
        revision = self.added(authorizedBy="R-1")["revision"]
        result = self.set("CR-T-ONE-01", revision, replace={"path": "src/app/engine.py"})
        self.assertEqual(self.fields(result["recordId"])["Authorization"], "proposed")
        result = self.set(result["recordId"], result["revision"],
                          replace={"path": "src/app/motor.py", "authorizedBy": "R-1"})
        fields = self.fields(result["recordId"])
        self.assertEqual((fields["Authorization"], fields["Authorized by"]), ("authorized", "R-1"))

    def test_batch_cannot_authorize_either(self) -> None:
        result = batch.apply(self.root, {"operations": [{"op": "ref add", **planned()}]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fields("CR-T-ONE-01")["Authorization"], "proposed")
        result = batch.apply(self.root, {"operations": [{"op": "ref add", **planned(authorizedBy="ADR-002")}]})
        self.assertEqual(result["error"]["code"], "unknown-authority")


class TestHistory(RefCase):
    """AC-T-CIL-MAP-02-03: replaced and withdrawn references keep their earlier record unchanged."""

    def test_replace_keeps_the_earlier_record_byte_for_byte(self) -> None:
        revision = self.added()["revision"]
        earlier = self.section("CR-T-ONE-01")
        result = self.set("CR-T-ONE-01", revision,
                          replace={"path": "src/app/engine.py", "was": "src/app/core.py#Core.run"})
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["recordId"], result["replaces"]), ("CR-T-ONE-02", "CR-T-ONE-01"))
        self.assertEqual(self.section("CR-T-ONE-01"), earlier)
        fields = self.fields("CR-T-ONE-02")
        self.assertEqual((fields["Replaces"], fields["Path"], fields["Symbol"], fields["Kind"], fields["Was"]),
                         ("CR-T-ONE-01", "src/app/engine.py", "Core.run", "method", "src/app/core.py#Core.run"))
        self.assertEqual(fields["Recorded"], f"{TODAY} · agent/reviewer")
        records = ref.history(self.root, "CR-T-ONE-02")
        self.assertEqual([r["id"] for r in records], ["CR-T-ONE-01", "CR-T-ONE-02"])
        self.assertEqual(records[0]["record"], earlier.rstrip(b"\r\n"))
        self.assertEqual(records[0]["revision"], revision)

    def test_withdraw_keeps_the_earlier_record_byte_for_byte(self) -> None:
        revision = self.added(verification="verified")["revision"]
        earlier = self.section("CR-T-ONE-01")
        result = self.set("CR-T-ONE-01", revision, withdraw={"reason": "the task no longer touches it",
                                                             "date": "2026-03-04"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.section("CR-T-ONE-01"), earlier)
        fields = self.fields(result["recordId"])
        self.assertEqual(fields["Withdrawn"], "2026-03-04 · the task no longer touches it")
        self.assertEqual((fields["Replaces"], fields["Path"], fields["Verification"], fields["Authorization"]),
                         ("CR-T-ONE-01", "src/app/core.py", "verified", "proposed"))
        compiled = self.compiled()[result["recordId"]]
        self.assertEqual(compiled["withdrawn"], "2026-03-04 · the task no longer touches it")
        records = ref.history(self.root, result["recordId"])
        self.assertEqual(records[0]["record"], earlier.rstrip(b"\r\n"))
        self.assertEqual(validate.errors(validate.check(compiler.load(self.root))), [])

    def test_a_chain_is_returned_oldest_first_and_closed_records_refuse_changes(self) -> None:
        first = self.added()
        second = self.set("CR-T-ONE-01", first["revision"], replace={"path": "src/b.py"})
        third = self.set(second["recordId"], second["revision"], withdraw={"reason": "dropped"})
        records = ref.history(self.root, third["recordId"])
        self.assertEqual([r["id"] for r in records], ["CR-T-ONE-01", "CR-T-ONE-02", "CR-T-ONE-03"])
        refused = self.set("CR-T-ONE-01", first["revision"], set={"verification": "verified"})
        self.assertEqual((refused["error"]["code"], refused["error"]["replacedBy"]),
                         ("replaced-reference", "CR-T-ONE-02"))
        refused = self.set(third["recordId"], third["revision"], set={"verification": "verified"})
        self.assertEqual(refused["error"]["code"], "withdrawn-reference")
        self.assertEqual([r["id"] for r in ref.history(self.root, "CR-T-ONE-01")], ["CR-T-ONE-01"])

    def test_history_command_prints_each_record_unchanged(self) -> None:
        revision = self.added()["revision"]
        earlier = self.section("CR-T-ONE-01").rstrip(b"\r\n")
        result = self.set("CR-T-ONE-01", revision, withdraw={"reason": "dropped"})
        later = self.section(result["recordId"]).rstrip(b"\r\n")
        out = io.BytesIO()
        stdout = io.TextIOWrapper(out, encoding="utf-8")
        with mock.patch("sys.stdout", stdout):
            code = cli.main(["-C", str(self.root), "ref", "history", result["recordId"]])
            stdout.flush()
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), earlier + b"\n\n" + later + b"\n")
        code, text = self.run_cli("ref", "history", result["recordId"], "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["records"][0]["record"], earlier.decode("utf-8"))
        code, text = self.run_cli("ref", "history", "CR-T-ONE-99", "--json")
        self.assertEqual((code, json.loads(text)["error"]["code"]), (1, "record-not-found"))


class TestLookupAfterSupersede(RefCase):
    """Review item 3: a replaced or withdrawn reference leaves the packet; `refs` lists it marked."""

    def packet_paths(self, task: str = "T-ONE") -> set[str]:
        packet = analytics.context(compiler.load(self.root), task)["references"]
        return {e["path"] for e in packet["first"]}, packet

    def retrieved(self, task: str = "T-ONE") -> dict:
        code, text = self.run_cli("retrieve", task, "--json")
        self.assertEqual(code, 0)
        return json.loads(text)["references"][0]

    def test_after_replace_the_packet_drops_the_old_artifact_and_refs_marks_it(self) -> None:
        revision = self.added()["revision"]
        result = self.set("CR-T-ONE-01", revision, replace={"path": "src/app/engine.py"})
        paths, packet = self.packet_paths()
        self.assertIn("src/app/engine.py", paths)
        self.assertNotIn("src/app/core.py", paths)
        self.assertNotIn("CR-T-ONE-01", {e["id"] for e in packet["first"]})
        authored = [e for e in packet["first"] if e["id"].startswith("CR-")]
        self.assertEqual([e["id"] for e in authored], [result["recordId"]])
        self.assertNotIn("CR-T-ONE-01", {e["id"] for e in self.retrieved()["first"]})
        code, text = self.run_cli("refs", "T-ONE", "--json")
        listed = {r["id"]: r for r in json.loads(text)["references"]}
        self.assertEqual(listed["CR-T-ONE-01"]["replacedBy"], result["recordId"])
        self.assertNotIn("replacedBy", listed[result["recordId"]])
        code, text = self.run_cli("refs", "T-ONE")
        self.assertIn(f"replaced by {result['recordId']} — CODE.md#CR-T-ONE-01", text)

    def test_after_withdraw_the_packet_drops_it_and_refs_marks_it(self) -> None:
        revision = self.added(path="src/app/gone.py", symbol="Gone", kind="class")["revision"]
        result = self.set("CR-T-ONE-01", revision, withdraw={"reason": "dropped"})
        paths, packet = self.packet_paths()
        self.assertNotIn("src/app/gone.py", paths)
        self.assertEqual(packet["total"], len(packet["first"]))
        self.assertFalse(any(e["id"].startswith("CR-") for e in self.retrieved()["first"]))
        code, text = self.run_cli("refs", "T-ONE", "--json")
        listed = {r["id"]: r for r in json.loads(text)["references"]}
        self.assertEqual(listed["CR-T-ONE-01"]["replacedBy"], result["recordId"])
        self.assertEqual(listed[result["recordId"]]["withdrawn"], f"{TODAY} · dropped")
        code, text = self.run_cli("refs", "T-ONE")
        self.assertIn(f"withdrawn {TODAY} · dropped", text)

    def test_a_path_names_only_tasks_with_a_current_reference(self) -> None:
        def file_reference(task: str) -> dict:
            payload = planned(task=task, path="src/shared.py")
            del payload["symbol"], payload["kind"]
            result = ref.add(self.root, payload)
            self.assertTrue(result["ok"], result)
            return result

        one = file_reference("T-ONE")
        file_reference("T-TWO")
        self.set("CR-T-ONE-01", one["revision"], withdraw={"reason": "no longer needed"})
        code, text = self.run_cli("refs", "src/shared.py", "--json")
        found = json.loads(text)
        self.assertEqual([t["id"] for t in found["tasks"]], ["T-TWO"])
        self.assertEqual({r["id"] for r in found["references"]}, {"CR-T-ONE-01", "CR-T-ONE-02", "CR-T-TWO-01"})
        self.assertEqual(found["current"], 1)
        code, text = self.run_cli("refs", "src/shared.py")
        self.assertIn("No longer current (replaced or withdrawn):", text)
        self.assertIn("T-ONE - src/shared.py", text)

    def test_summary_counts_current_references_only(self) -> None:
        first = self.added(scope="observed", source="git_diff")
        self.added(path="src/b.py")
        before = refs.summary(compiler.load(self.root), "T-ONE")
        self.set("CR-T-ONE-01", first["revision"], withdraw={"reason": "x"})
        after = refs.summary(compiler.load(self.root), "T-ONE")
        self.assertEqual(after["total"], before["total"] - 1)
        self.assertEqual(after["counts"]["observed"], before["counts"]["observed"] - 1)


class TestBatchAndCli(RefCase):
    """Both writes are batch operations and CLI commands with --schema and --dry-run."""

    def test_batch_adds_and_sets_in_one_transaction(self) -> None:
        result = batch.apply(self.root, {"operations": [
            {"op": "ref add", **planned()},
            {"op": "ref add", **planned(path="src/b.py")},
            {"op": "ref set", "id": "CR-T-ONE-01", "expectRevision": "$previous",
             "recordedBy": "agent/builder", "set": {"verification": "verified"}},
        ]})
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["recordId"] for r in result["results"]], ["CR-T-ONE-01", "CR-T-ONE-02", "CR-T-ONE-01"])
        self.assertEqual(self.fields("CR-T-ONE-01")["Verification"], "verified")
        self.assertEqual(result["revisions"]["CODE.md#CR-T-ONE-01"], mutate.revision(self.section("CR-T-ONE-01")))

    def test_a_refused_operation_writes_nothing(self) -> None:
        revision = self.added()["revision"]
        before = self.code.read_bytes()
        result = batch.apply(self.root, {"operations": [
            {"op": "ref add", **planned(path="src/b.py")},
            {"op": "ref set", "id": "CR-T-ONE-01", "expectRevision": "0" * 16,
             "recordedBy": "agent/builder", "set": {"verification": "verified"}},
        ]})
        self.assertEqual((result["error"]["code"], result["error"]["index"]), ("stale-revision", 1))
        self.assertEqual(result["error"]["currentRevision"], revision)
        self.assertEqual(self.code.read_bytes(), before)

    def test_cli_write_schema_and_dry_run(self) -> None:
        code, text = self.run_cli("ref", "add", "--schema")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["example"], ref.ADD_EXAMPLE)
        code, text = self.run_cli("ref", "set", "--schema")
        self.assertIn("withdraw", json.loads(text)["schema"]["properties"])
        code, text = self.run_cli("ref", "add", "--input", "-", "--dry-run", stdin=json.dumps(planned()))
        self.assertEqual((code, json.loads(text)["dryRun"]), (0, True))
        self.assertFalse(self.code.exists())
        code, text = self.run_cli("ref", "add", "--input", "-", stdin=json.dumps(planned()))
        result = json.loads(text)
        self.assertEqual((code, result["recordId"]), (0, "CR-T-ONE-01"))
        code, text = self.run_cli("ref", "set", "--input", "-", stdin=json.dumps(
            {"id": "CR-T-ONE-01", "expectRevision": result["revision"],
             "recordedBy": "owner", "set": {"verification": "verified"}}))
        self.assertEqual((code, json.loads(text)["ok"]), (0, True))

    def test_help_and_refusals_follow_the_write_conventions(self) -> None:
        for operation in ("ref.add", "ref.set"):
            parts = mutate.GUIDANCE[operation]
            self.assertEqual([p.split(":", 1)[0] for p in parts], ["WHEN", "WHEN NOT", "RETURNS", "EXAMPLE"])
        source = (ROOT / "src" / "praukron" / "ref.py").read_text(encoding="utf-8")
        import re
        codes = set(re.findall(r'WriteError\(\s*"([a-z-]+)"', source))
        self.assertEqual(codes - set(mutate.NEXT_STEP) - {"stale-revision"}, set())
        self.assertIn("ref add", batch.OPERATIONS)
        self.assertIn("ref set", batch.OPERATIONS)


if __name__ == "__main__":
    unittest.main()
