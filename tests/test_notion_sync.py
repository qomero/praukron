"""Notion provision, push, pull and watch against an in-memory Notion
(T-NOTION-PROVISION-01, T-NOTION-PUSH-01, T-NOTION-IDENTITY-01, T-NOTION-PULL-01,
T-NOTION-SCHED-01; ADR-087, ADR-088, ADR-089)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from praukron import compile as compiler, layout, notion_sync, validate  # noqa: E402
from praukron.notion_api import Client, NotionError  # noqa: E402
from praukron.notion_config import Token  # noqa: E402
from test_assumptions import ASSUMPTIONS, POLICY_ADR, RESPONSES  # noqa: E402
from test_praukron import build_fixture  # noqa: E402

OWNER_ID = "40cd872b-594c-8191-98f7-000272497c99"
STRANGER = "11111111-2222-3333-4444-555555555555"
SECRET = "ntn_fake_secret_for_tests_0000"
OWNERS_ADR = f"""# ADR-003: Notion owners
- Date: 2026-11-03
- Status: ACCEPTED
- Authority: owner
- Decision: Notion submissions from the owner count.
- Notion owners: {OWNER_ID}=thien-owner
"""


def _read(value: dict) -> dict:
    """A written property value in the shape Notion returns it."""
    for kind in ("title", "rich_text"):
        if kind in value:
            return {"type": kind, kind: [{"plain_text": p["text"]["content"], **p} for p in value[kind]]}
    for kind in ("select", "checkbox", "number", "relation", "url", "date"):
        if kind in value:
            return {"type": kind, kind: value[kind]}
    raise AssertionError(value)


class FakeNotion(Client):
    """The endpoints the sync uses, in memory. `writes` counts mutating calls."""

    def __init__(self) -> None:
        super().__init__(Token(SECRET, "environment"))
        self.blocks: dict[str, list[dict]] = {}
        self.databases: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.writes = 0
        self.editor = OWNER_ID
        self.clock = 0

    def _id(self) -> str:
        return str(uuid.uuid4())

    def _tick(self) -> str:
        self.clock += 1
        return f"2026-11-03T00:00:{self.clock:02d}.000Z"

    def me(self) -> dict:
        return {"bot": {"workspace_name": "Test Space"}}

    def add_page(self, name: str, parent: str | None = None) -> str:
        """A plain page the owner made, such as a duplicate of the PPMP template."""
        page_id = self._id()
        self.pages[page_id] = {"id": page_id, "parent": {"type": "workspace"}, "in_trash": False,
                               "properties": {"title": _read(notion_sync.title(name))}}
        self.blocks[page_id] = []
        if parent:
            self.blocks[parent].append({"id": page_id, "type": "child_page", "has_children": True,
                                        "child_page": {"title": name}})
        return page_id

    def page(self, page_id: str) -> dict:
        if page_id not in self.pages and page_id not in self.blocks:
            raise NotionError(404, "object_not_found", "missing")
        return self.pages.get(page_id, {"id": page_id, "in_trash": False})

    def children(self, block_id: str) -> list[dict]:
        return list(self.blocks.get(block_id, []))

    def append(self, block_id: str, blocks: list[dict]) -> dict:
        self.writes += 1
        for block in blocks:
            self.blocks.setdefault(block_id, []).append({"id": self._id(), **block})
        return {}

    def delete_block(self, block_id: str) -> dict:
        self.writes += 1
        for owner in self.blocks.values():
            owner[:] = [b for b in owner if b["id"] != block_id]
        return {}

    def database(self, database_id: str) -> dict:
        return self.databases[database_id]

    def data_source(self, data_source_id: str) -> dict:
        return self.sources[data_source_id]

    def create_database(self, parent_page, title, properties, icon=None) -> dict:
        self.writes += 1
        database_id, source_id = self._id(), self._id()
        self.sources[source_id] = {"id": source_id, "properties": dict(properties),
                                   "title": [{"plain_text": title}]}
        self.databases[database_id] = {"id": database_id, "data_sources": [{"id": source_id, "name": title}]}
        self.blocks.setdefault(parent_page, []).append(
            {"id": database_id, "type": "child_database", "has_children": False,
             "child_database": {"title": title}})
        return {"id": database_id, "data_sources": [{"id": source_id, "name": title}]}

    def update_data_source(self, data_source_id, properties) -> dict:
        self.writes += 1
        self.sources[data_source_id]["properties"].update(properties)
        for name, value in properties.items():
            dual = (value.get("relation") or {}).get("dual_property")
            if dual:
                target = value["relation"]["data_source_id"]
                self.sources[target]["properties"][dual["synced_property_name"]] = {"relation": {}}
        return {}

    def create_page(self, parent, properties, children=None, icon=None) -> dict:
        self.writes += 1
        page_id = self._id()
        page = {"id": page_id, "parent": parent, "in_trash": False,
                "properties": {k: _read(v) for k, v in properties.items()},
                "last_edited_by": {"id": self.editor}, "last_edited_time": self._tick()}
        self.pages[page_id] = page
        if parent.get("type") == "page_id":
            self.blocks.setdefault(parent["page_id"], []).append(
                {"id": page_id, "type": "child_page", "has_children": True, "child_page": {}})
        return page

    def update_page(self, page_id, properties=None, archived=None) -> dict:
        self.writes += 1
        page = self.pages[page_id]
        for name, value in (properties or {}).items():
            page["properties"][name] = _read(value)
        if archived is not None:
            page["in_trash"] = archived
        return page

    def edit(self, page_id: str, editor: str = OWNER_ID, **values) -> None:
        """What the owner does in Notion."""
        page = self.pages[page_id]
        for name, value in values.items():
            page["properties"][name] = _read(value)
        page["last_edited_by"] = {"id": editor}
        page["last_edited_time"] = self._tick()

    def query(self, data_source_id, filter=None) -> list[dict]:
        def match(page, f) -> bool:
            if f is None:
                return True
            if "and" in f:
                return all(match(page, g) for g in f["and"])
            prop = page["properties"].get(f["property"])
            if "rich_text" in f:
                return notion_sync.plain(prop) == f["rich_text"]["equals"]
            if "checkbox" in f:
                return bool(prop and prop.get("checkbox")) == f["checkbox"]["equals"]
            raise AssertionError(f)
        return [p for p in self.pages.values()
                if p["parent"].get("data_source_id") == data_source_id and not p["in_trash"]
                and match(p, filter)]

    def rows(self, kind: str, config: dict) -> list[dict]:
        return self.query(config["databases"][kind]["data_source_id"])

    def row(self, kind: str, config: dict, pid: str) -> dict:
        return next(p for p in self.rows(kind, config)
                    if notion_sync.plain(p["properties"].get("ID")) == pid)


class NotionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-notion-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        build_fixture(self.dir)
        authority = self.dir / layout.AUTHORITY_DIR
        (authority / "ASSUMPTIONS.md").write_text(ASSUMPTIONS, encoding="utf-8")
        (authority / "RESPONSES.md").write_text(RESPONSES, encoding="utf-8")
        (authority / "ADR" / "ADR-002.md").write_text(POLICY_ADR, encoding="utf-8")
        (authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        refresh = mock.patch("praukron.respond.refresh")
        refresh.start()
        self.addCleanup(refresh.stop)
        self.notion = FakeNotion()
        self.portfolio = self.notion.add_page("Praukron · Portfolio")
        self.instance = self.notion.add_page("PPMP · Demo", self.portfolio)
        self.config = notion_sync.setup(self.notion, self.dir,
                                        f"https://www.notion.so/x-{self.instance.replace('-', '')}", key="DEMO")


def _databases_under(notion: FakeNotion, page: str) -> set[str]:
    return {b["child_database"]["title"] for b in notion.blocks.get(page, []) if b["type"] == "child_database"}


class ProvisionTest(NotionCase):
    def test_setup_on_a_blank_page_builds_the_nine_ppmp_databases_with_relations(self) -> None:
        self.assertEqual(_databases_under(self.notion, self.instance),
                         {spec["title"] for spec in notion_sync.DATABASES.values()})
        tasks = self.notion.sources[self.config["databases"]["tasks"]["data_source_id"]]["properties"]
        for name in ("ID", "Depends On", "Blocks Tasks", "Phase Link", "Acceptance Records", "Work Bucket",
                     "Review", "Project Key", "Source Revision", "Evidence"):
            self.assertIn(name, tasks)
        ledger = self.notion.sources[self.config["databases"]["ledger"]["data_source_id"]]["properties"]
        for name in ("Source Assumption", "Source Instruction", "Target Task Link"):
            self.assertIn(name, ledger)
        saved = json.loads((self.dir / ".praukron" / "notion.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["projectKey"], saved["template"], saved["instance"]),
                         ("DEMO", "PPMP", self.instance))
        self.assertEqual(set(saved["databases"]), set(notion_sync.ORDER))
        self.assertIn(saved["hub"], {b["id"] for b in self.notion.blocks[self.instance]})

    def test_setup_binds_a_duplicated_template_and_only_adds_what_is_missing(self) -> None:
        copy = self.notion.add_page("PPMP · Praukron Project Management Protocol| TEMPLATE (1)", self.portfolio)
        layer = self.notion.add_page("90 · Data Layer & Schema Reference", copy)
        made = notion_sync.provision(self.notion, layer)   # what the duplicate already holds
        tasks = made["tasks"]["data_source_id"]
        del self.notion.sources[tasks]["properties"]["Evidence"]
        before = len(self.notion.databases)
        other = Path(tempfile.mkdtemp(prefix="praukron-notion-b-"))
        self.addCleanup(shutil.rmtree, other, True)
        build_fixture(other)
        config = notion_sync.setup(self.notion, other, copy, key="OTHER", name="Other")
        self.assertEqual(len(self.notion.databases), before)
        self.assertEqual(config["databases"], made)
        self.assertIn("Evidence", self.notion.sources[tasks]["properties"])
        self.assertEqual(notion_sync.page_title(self.notion.pages[copy]), "PPMP · Other · OTHER")

    def test_setup_again_duplicates_nothing(self) -> None:
        before = len(self.notion.databases)
        again = notion_sync.setup(self.notion, self.dir, self.instance, key="DEMO")
        self.assertEqual(len(self.notion.databases), before)
        self.assertEqual(again["hub"], self.config["hub"])
        self.assertEqual(again["databases"], self.config["databases"])

    def test_setup_refuses_the_template_itself(self) -> None:
        template = self.notion.add_page("PPMP · Praukron Project Management Protocol| TEMPLATE", self.portfolio)
        before = self.notion.writes
        with self.assertRaises(NotionError) as caught:
            notion_sync.setup(self.notion, self.dir, template, key="DEMO")
        self.assertEqual(caught.exception.code, "template-page")
        self.assertEqual(self.notion.writes, before)


class PushTest(NotionCase):
    def test_push_mirrors_records_with_relations_and_is_idempotent(self) -> None:
        first = notion_sync.push(self.notion, self.dir, self.config)
        self.assertGreater(first.created, 0)
        task = self.notion.row("tasks", self.config, "T-TWO")
        self.assertEqual(notion_sync.plain(task["properties"]["Status"]), "TODO")
        self.assertEqual(notion_sync.plain(task["properties"]["Project Key"]), "DEMO")
        self.assertTrue(notion_sync.plain(task["properties"]["Source Revision"]))
        one = self.notion.row("tasks", self.config, "T-ONE")["id"]
        project = compiler.as_json(compiler.load(self.dir))
        deps = next(t for t in project["tasks"] if t["id"] == "T-TWO")["deps"]
        if "T-ONE" in deps:
            self.assertIn(one, notion_sync.plain(task["properties"]["Depends On"]))
        for kind, pid in (("assumptions", "A-1"), ("adrs", "ADR-001"), ("phases", "P1")):
            self.notion.row(kind, self.config, pid)
        adr = self.notion.row("adrs", self.config, "ADR-001")["properties"]
        self.assertEqual(notion_sync.plain(adr["Kind"]), "ADR")
        self.assertIn("ADR-001", notion_sync.plain(adr["Rationale"]))
        for criterion in self.notion.rows("acceptance", self.config):
            self.assertIn(notion_sync.plain(criterion["properties"]["Result"]), ("NOT_RUN", "PASS", "FAIL"))
        self.assertTrue(self.notion.rows("acceptance", self.config))
        writes = self.notion.writes
        second = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(self.notion.writes, writes)
        self.assertEqual((second.created, second.updated, second.archived), (0, 0, 0))

    def test_a_removed_record_is_archived_not_left_current(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        authority = self.dir / layout.AUTHORITY_DIR
        (authority / "ASSUMPTIONS.md").write_text(ASSUMPTIONS.split("## A-2")[0], encoding="utf-8")
        page = self.notion.row("assumptions", self.config, "A-2")
        result = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(result.archived, 1)
        self.assertTrue(self.notion.pages[page["id"]]["in_trash"])

    def test_owner_fields_survive_a_push(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        row = self.notion.row("assumptions", self.config, "A-1")
        self.notion.edit(row["id"], Feedback=notion_sync.rich("draft"), Decision=notion_sync.select("REVISE"))
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.replace("Smallest access", "Least access"), encoding="utf-8")
        notion_sync.push(self.notion, self.dir, self.config)
        props = self.notion.pages[row["id"]]["properties"]
        self.assertEqual(notion_sync.plain(props["Feedback"]), "draft")
        self.assertEqual(notion_sync.plain(props["Decision"]), "REVISE")

    def test_live_status_and_sync_row_match_status(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        project = compiler.as_json(compiler.load(self.dir))
        summary = notion_sync.summary(project, "DEMO")
        text = json.dumps(self.notion.blocks[self.config["hub"]], ensure_ascii=False)
        self.assertIn(f"{summary['done']} / {summary['total']} execution tasks DONE", text)
        self.assertIn(f"{summary['attention']} item(s) need you", text)
        for line in summary["assumptions"]:
            self.assertIn(line.split(" · ")[0], text)
        sync = self.notion.row("operations", self.config, notion_sync.SYNC_ROW)["properties"]
        self.assertEqual(notion_sync.plain(sync["Kind"]), "SYNC")
        self.assertTrue(notion_sync.plain(sync["Source Hash"]))
        writes = self.notion.writes
        notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(self.notion.writes, writes)


class RetryTest(unittest.TestCase):
    def test_rate_limits_and_server_errors_are_retried(self) -> None:
        calls = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(request, timeout):
            calls.append(request)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 429 if len(calls) == 1 else 503,
                                             "busy", {}, io.BytesIO(b"{}"))
            return Response(b'{"ok": true}')

        slept = []
        client = Client(Token(SECRET, "environment"), opener=opener, sleep=slept.append)
        self.assertEqual(client.request("GET", "users/me"), {"ok": True})
        self.assertEqual(len(slept), 2)

    def test_errors_never_carry_the_token(self) -> None:
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {},
                                         io.BytesIO(json.dumps({"code": "x", "message": SECRET}).encode()))
        client = Client(Token(SECRET, "environment"), opener=opener, sleep=lambda s: None)
        with self.assertRaises(NotionError) as caught:
            client.request("GET", "users/me")
        self.assertNotIn(SECRET, str(caught.exception))


class IdentityTest(NotionCase):
    def test_notion_owners_map_ids_to_logins(self) -> None:
        project = compiler.load(self.dir)
        self.assertEqual(project.notion_owners, {OWNER_ID.replace("-", ""): "thien-owner"})

    def test_malformed_notion_owners_is_an_error(self) -> None:
        (self.dir / layout.AUTHORITY_DIR / "ADR" / "ADR-003.md").write_text(
            OWNERS_ADR.replace(f"{OWNER_ID}=thien-owner", "qomero"), encoding="utf-8")
        codes = [f.code for f in validate.errors(validate.check(compiler.load(self.dir)))]
        self.assertIn("invalid-notion-owners", codes)


class PullTest(NotionCase):
    def setUp(self) -> None:
        super().setUp()
        notion_sync.push(self.notion, self.dir, self.config)

    def submit_assumption(self, pid: str = "A-1", editor: str = OWNER_ID, **values) -> str:
        row = self.notion.row("assumptions", self.config, pid)
        values.setdefault("Decision", notion_sync.select("CONFIRM"))
        self.notion.edit(row["id"], editor, Submit=notion_sync.checkbox(True), **values)
        return row["id"]

    def state(self, row_id: str) -> str:
        return notion_sync.plain(self.notion.pages[row_id]["properties"]["Delivery State"])

    def ledger(self, row_id: str) -> dict:
        return next(p["properties"] for p in self.notion.rows("ledger", self.config)
                    if notion_sync.plain(p["properties"]["Request ID"]) == row_id)

    def responses(self) -> str:
        return (self.dir / layout.AUTHORITY_DIR / "RESPONSES.md").read_text(encoding="utf-8")

    def test_owner_decision_lands_verbatim_with_mapped_login_and_receipt(self) -> None:
        states = []
        original = notion_sync._mark

        def spy(client, row_id, state, *a, **k):
            states.append(state)
            return original(client, row_id, state, *a, **k)

        row = self.submit_assumption(Decision=notion_sync.select("REVISE"),
                                     Feedback=notion_sync.rich("Mặc định 14 ngày."))
        with mock.patch.object(notion_sync, "_mark", spy):
            result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.applied, ["R-3"])
        self.assertEqual(states, ["QUEUED", "LOCAL_RECORDED", "APPLIED"])
        text = self.responses()
        self.assertIn("## R-3: A-1 REVISE\n- By: thien-owner", text)
        self.assertIn(f"- Via: notion {OWNER_ID.replace('-', '')} request {row}", text)
        self.assertIn("> Mặc định 14 ngày.", text)
        self.assertIn("R-3", notion_sync.plain(self.notion.pages[row]["properties"]["Apply Evidence"]))
        ledger = self.ledger(row)
        self.assertEqual([notion_sync.plain(ledger[k]) for k in ("ID", "Status", "Applied Revision", "Source")],
                         ["R-3", "APPLIED", "R-3", "ASSUMPTION"])

    def test_a_submission_seen_twice_is_written_once(self) -> None:
        row = self.submit_assumption()
        notion_sync.pull(self.notion, self.dir, self.config)
        self.notion.pages[row]["properties"]["Delivery State"] = {"type": "select", "select": {"name": "QUEUED"}}
        again = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(again.applied, ["R-3"])
        self.assertEqual(self.responses().count("## R-3"), 1)
        self.assertNotIn("## R-4", self.responses())
        self.assertEqual(len([p for p in self.notion.rows("ledger", self.config)
                              if notion_sync.plain(p["properties"]["Request ID"]) == row]), 1)

    def test_a_stale_row_conflicts_and_writes_nothing(self) -> None:
        row = self.submit_assumption()
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.replace("default 30.", "default 45."), encoding="utf-8")
        before = self.responses()
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.conflicts, 1)
        self.assertEqual(self.state(row), "CONFLICT")
        self.assertFalse(self.notion.pages[row]["properties"]["Submit"]["checkbox"])
        self.assertEqual(self.responses(), before)
        self.assertEqual(notion_sync.plain(self.ledger(row)["Status"]), "CONFLICT")

    def test_a_stranger_is_rejected_and_nothing_is_written(self) -> None:
        row = self.submit_assumption(editor=STRANGER)
        before = self.responses()
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(self.state(row), "REJECTED")
        self.assertEqual(self.responses(), before)
        self.assertIn("not listed", notion_sync.plain(self.ledger(row)["Error"]))

    def outbox(self, intent: str, target: str = "T-TWO", editor: str = OWNER_ID) -> str:
        source = self.config["databases"]["outbox"]["data_source_id"]
        task = self.notion.row("tasks", self.config, target)["id"] if target.startswith("T-") else None
        page = self.notion.create_page({"type": "data_source_id", "data_source_id": source}, {
            "ID": notion_sync.title("Use staging"),
            "Instruction Text": notion_sync.rich("Chạy trên staging trước."),
            "Intent": notion_sync.select(intent), "Delivery State": notion_sync.select("DRAFT"),
            **({"Target Task Link": notion_sync.relation([task])} if task else
               {"Target ID": notion_sync.rich(target)}),
        })
        self.notion.edit(page["id"], editor, Submit=notion_sync.checkbox(True))
        return page["id"]

    def test_instructions_become_guide_responses_on_their_target(self) -> None:
        for intent in ("IMPLEMENT", "FIX", "REVIEW", "REPLAN", "PAUSE", "OTHER"):
            with self.subTest(intent=intent):
                row = self.outbox(intent)
                notion_sync.pull(self.notion, self.dir, self.config)
                self.assertEqual(self.state(row), "APPLIED")
                self.assertEqual(notion_sync.plain(self.ledger(row)["Source"]), "INSTRUCTION")
        text = self.responses()
        self.assertIn("T-TWO GUIDE\n- By: thien-owner", text)
        self.assertIn("> Chạy trên staging trước.", text)
        self.assertIn("(Notion · intent PAUSE)", text)
        project = compiler.as_json(compiler.load(self.dir))
        self.assertTrue(any(r["target"] == "T-TWO" and "notion" in (r["via"] or "") for r in project["responses"]))

    def test_verify_from_a_listed_owner_is_passed_as_verify(self) -> None:
        calls = []

        def fake(root, items, via, verify, by):
            calls.append((items[0].action, verify, by))
            return ["R-9"]

        row = self.outbox("APPROVE")
        notion_sync.pull(self.notion, self.dir, self.config, append=fake)
        self.assertEqual(calls, [("VERIFY", True, "thien-owner")])
        self.assertEqual(notion_sync.plain(self.ledger(row)["Source"]), "APPROVAL")

    def test_offline_submissions_are_applied_in_order_when_back(self) -> None:
        first = self.outbox("GUIDE")
        second = self.submit_assumption("A-2", Decision=notion_sync.select("CONFIRM"))
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.applied, ["R-3", "R-4"])
        self.assertTrue(self.responses().index("T-TWO GUIDE") < self.responses().index("A-2 CONFIRM"))
        self.assertEqual({self.state(first), self.state(second)}, {"APPLIED"})


class WatchTest(NotionCase):
    def test_windows_installs_and_removes_two_scheduled_tasks(self) -> None:
        seen = []

        def run(args, **kw):
            seen.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(notion_sync.os, "name", "nt"):
            status, text = notion_sync.watch(self.dir, "install", 5, 2, run=run)
            self.assertEqual(status, 0)
            notion_sync.watch(self.dir, "remove", run=run)
        created = [a for a in seen if "/Create" in a]
        self.assertEqual([a[a.index("/TN") + 1] for a in created],
                         ["Praukron Notion DEMO pull", "Praukron Notion DEMO push"])
        self.assertEqual([a[a.index("/MO") + 1] for a in created], ["2", "5"])
        self.assertTrue(all("notion pull --quiet" in a[-1] or "notion push --quiet" in a[-1] for a in created))
        self.assertEqual(len([a for a in seen if "/Delete" in a]), 2)

    def test_posix_prints_crontab_lines(self) -> None:
        with mock.patch.object(notion_sync.os, "name", "posix"):
            status, text = notion_sync.watch(self.dir, "install", 5, 2)
        self.assertIn("*/2 * * * *", text)
        self.assertIn("*/5 * * * *", text)

    def test_log_has_no_token_and_lock_prevents_overlap(self) -> None:
        notion_sync.log(self.dir, "DEMO push created=1")
        log = (self.dir / ".praukron" / "notion.log").read_text(encoding="utf-8")
        self.assertIn("DEMO push created=1", log)
        self.assertNotIn(SECRET, log)
        with notion_sync.lock(self.dir):
            with self.assertRaises(notion_sync.Locked):
                with notion_sync.lock(self.dir):
                    pass
        with notion_sync.lock(self.dir):
            pass


if __name__ == "__main__":
    unittest.main()
