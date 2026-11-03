"""Notion setup, push, pull and watch against an in-memory Notion
(T-NOTION; ADR-087, ADR-088, ADR-089, ADR-090): each project mirrors into its own
copy of the PPMP template and into one shared Project Portfolio."""

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
from typing import Callable
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
BOT = "99999999-8888-7777-6666-555555555555"   # the integration, as Notion names it
SECRET = "ntn_fake_secret_for_tests_0000"
OWNERS_ADR = f"""# ADR-003: Notion owners
- Date: 2026-11-03
- Status: ACCEPTED
- Authority: owner
- Decision: Notion submissions from the owner count.
- Notion owners: {OWNER_ID}=thien-owner
"""
# The titles the template gives its databases, by kind.
TEMPLATE_TITLES = {
    "tasks": "Execution Tasks", "decisions": "Human Decisions", "phases": "Phases & Gates",
    "operations": "Operations & Sync", "portfolio": "Project Portfolio",
    "acceptance": "Acceptance & Evidence", "blockers": "Blockers & Dependencies",
    "adrs": "ADR & Technical Debt", "ledger": "Action Ledger",
    "modules": "🧩 Product Modules · Praukron", "assumptions": "✏️ Assumption Review Queue",
    "outbox": "Agent Instructions Outbox", "responses": "📜 Owner Response History · Sample",
}
# The template's hub callouts as the owner's copy holds them before any push:
# a first line, and the label lines as child blocks (the template's layout).
HUB_TEXTS = {
    "header": ("SAMPLE / PROJECT CONTROL CENTER · Executive view", "A template thesis."),
    "done": ("0 / 41", "Execution tasks DONE"), "phase": ("0 / 8", "P0 execution · 0% DONE"),
    "reviewed": ("0 / 41", "AI reviewed / human verified"), "gates": ("0 / 5", "GREEN phase gates"),
    "owner": ("VIỆC CỦA BẠN — 0 mục", "Chưa có việc."), "product": ("THE PRODUCT", "A template."),
}


def _read(value: dict) -> dict:
    """A written property value in the shape Notion returns it."""
    for kind in ("title", "rich_text"):
        if kind in value:
            return {"type": kind, kind: [{"plain_text": p["text"]["content"], **p} for p in value[kind]]}
    for kind in ("select", "checkbox", "number", "relation", "url", "date"):
        if kind in value:
            return {"type": kind, kind: value[kind]}
    raise AssertionError(value)


# Owner text whose every edge matters: indentation, blank lines, trailing spaces, Unicode.
VERBATIM = "  preserve leading spaces\n\n\tindented line\nsecond line  \n  ✓ Mặc định — 14 ngày 🙂  "


class Crash(Exception):
    """The process dies here."""


def marks(state: str) -> Callable[..., bool]:
    """Matches the worker's write of one Delivery State."""
    def match(page_id, properties=None, archived=None) -> bool:
        return ((properties or {}).get("Delivery State") or {}).get("select", {}).get("name") == state
    return match


def lines(notion: "FakeNotion", callout: str) -> list[str]:
    """A hub callout as the reader sees it: its first line, then its child blocks."""
    return [notion_sync._block_text(notion.block(callout))] + [
        notion_sync._block_text(c) for c in notion.children(callout)]


def _ids(page: dict, name: str) -> set[str]:
    return set(filter(None, notion_sync.plain(page["properties"].get(name)).split(",")))


class FakeNotion(Client):
    """The endpoints the sync uses, in memory. `writes` counts mutating calls.
    As in Notion, every page the integration writes names it as the last editor
    and moves the edit time; `edit` is the owner's hand."""

    def __init__(self) -> None:
        super().__init__(Token(SECRET, "environment"))
        self.blocks: dict[str, list[dict]] = {}
        self.databases: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.writes = 0
        self.clock = 0

    def _id(self) -> str:
        return str(uuid.uuid4())

    def _tick(self) -> str:
        self.clock += 1
        return f"2026-11-03T00:{self.clock // 60:02d}:{self.clock % 60:02d}.000Z"

    def me(self) -> dict:
        return {"bot": {"workspace_name": "Test Space"}}

    def add_page(self, name: str, parent: str | None = None) -> str:
        """A plain page the owner made, such as a duplicate of the PPMP template, inside `parent`."""
        page_id = self._id()
        self.pages[page_id] = {"id": page_id, "in_trash": False,
                               "parent": {"type": "page_id", "page_id": parent} if parent else {"type": "workspace"},
                               "properties": {"title": _read(notion_sync.title(name))}}
        self.blocks[page_id] = []
        if parent:
            self.blocks[parent].append({"id": page_id, "type": "child_page", "has_children": True,
                                        "child_page": {"title": name}})
        return page_id

    def add_block(self, parent: str, kind: str, text: str = "", children: bool = False) -> str:
        """A block the owner's template holds: a callout, a column or a column list."""
        block_id = self._id()
        body = {"rich_text": [{"type": "text", "plain_text": text, "text": {"content": text}}]} if text else {}
        self.blocks.setdefault(parent, []).append({"id": block_id, "type": kind, "has_children": children,
                                                   kind: body})
        self.blocks.setdefault(block_id, [])
        return block_id

    def seed_database(self, parent: str, kind: str, title: str, drop: tuple[str, ...] = ()) -> None:
        """A database the duplicate already holds: the template's properties, no relations, no write counted."""
        spec = notion_sync.DATABASES[kind]
        writes = self.writes
        self.create_database(parent, title, {n: v for n, v in spec["properties"].items() if n not in drop})
        self.writes = writes

    def block(self, block_id: str) -> dict:
        return next(b for blocks in self.blocks.values() for b in blocks if b["id"] == block_id)

    def page(self, page_id: str) -> dict:
        if page_id not in self.pages and page_id not in self.blocks:
            raise NotionError(404, "object_not_found", "missing")
        return self.pages.get(page_id, {"id": page_id, "in_trash": False})

    def children(self, block_id: str) -> list[dict]:
        return list(self.blocks.get(block_id, []))

    def append(self, block_id: str, blocks: list[dict], after: str | None = None) -> dict:
        self.writes += 1
        siblings = self.blocks.setdefault(block_id, [])
        at = next(i + 1 for i, b in enumerate(siblings) if b["id"] == after) if after else len(siblings)
        made = []
        for block in blocks:
            block = json.loads(json.dumps(block))
            body = block.get(block["type"]) or {}
            nested = body.pop("children", None)
            new = {"id": self._id(), "has_children": bool(nested), **block}
            siblings.insert(at, new)
            at += 1
            made.append(new)
            if nested:
                self.append(new["id"], nested)
                self.writes -= 1   # one request in Notion
        return {"results": made}

    def update_block(self, block_id: str, body: dict) -> dict:
        self.writes += 1
        block = self.block(block_id)
        for kind, value in body.items():
            block[kind] = {**block.get(kind, {}), **value}
        return block

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
                "last_edited_by": {"id": BOT}, "last_edited_time": self._tick()}
        self.pages[page_id] = page
        if parent.get("type") == "page_id":
            self.blocks.setdefault(parent["page_id"], []).append(
                {"id": page_id, "type": "child_page", "has_children": True, "child_page": {}})
        return page

    def update_page(self, page_id, properties=None, archived=None, icon=None) -> dict:
        self.writes += 1
        page = self.pages[page_id]
        for name, value in (properties or {}).items():
            page["properties"][name] = _read(value)
        if archived is not None:
            page["in_trash"] = archived
        if icon is not None:
            page["icon"] = icon
        page["last_edited_by"] = {"id": BOT}
        page["last_edited_time"] = self._tick()
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

    def shared_rows(self, config: dict) -> list[dict]:
        return self.query(config["shared"]["data_source_id"])


class Base(unittest.TestCase):
    """A project on disk and an empty Notion holding a portfolio page and a blank copy; nothing set up yet."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-notion-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        build_fixture(self.dir)
        authority = self.dir / layout.AUTHORITY_DIR
        (authority / "ASSUMPTIONS.md").write_text(ASSUMPTIONS, encoding="utf-8")
        (authority / "RESPONSES.md").write_text(RESPONSES, encoding="utf-8")
        (authority / "ADR" / "ADR-002.md").write_text(POLICY_ADR, encoding="utf-8")
        (authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        tasks = authority / "TASKS.md"   # T-TWO waits on the owner, so the hub and Human Decisions have one
        tasks.write_text(tasks.read_text(encoding="utf-8").replace(
            "- AC: AC-T-TWO\n", "- AC: AC-T-TWO\n- Authority: owner\n", 1), encoding="utf-8")
        refresh = mock.patch("praukron.respond.refresh")
        refresh.start()
        self.addCleanup(refresh.stop)
        self.notion = FakeNotion()
        self.portfolio = self.notion.add_page("Praukron · Portfolio")
        self.instance = self.notion.add_page("PPMP · Demo", self.portfolio)

    def setup_notion(self, page: str | None = None, **kw) -> dict:
        page = page or self.instance
        return notion_sync.setup(self.notion, self.dir, f"https://www.notion.so/x-{page.replace('-', '')}", **kw)

    def saved(self) -> dict:
        return json.loads((self.dir / ".praukron" / "notion.json").read_text(encoding="utf-8"))


class NotionCase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.setup_notion(key="DEMO")


def _databases_under(notion: FakeNotion, page: str) -> set[str]:
    return {b["child_database"]["title"] for b in notion.blocks.get(page, []) if b["type"] == "child_database"}


class ProvisionTest(Base):
    def test_setup_on_a_blank_copy_builds_thirteen_databases_a_shared_board_and_the_hub(self) -> None:
        config = self.setup_notion(key="DEMO")
        self.assertEqual(len(notion_sync.ORDER), 13)
        self.assertEqual(_databases_under(self.notion, self.instance),
                         {spec["title"] for spec in notion_sync.DATABASES.values()})
        # the shared board sits under the portfolio page, not under the copy
        self.assertEqual(_databases_under(self.notion, self.portfolio), {"Project Portfolio"})
        self.assertEqual(len(self.notion.databases), 14)
        self.assertNotEqual(config["shared"], config["databases"]["portfolio"])
        sources = {kind: self.notion.sources[config["databases"][kind]["data_source_id"]]["properties"]
                   for kind in notion_sync.ORDER}
        for name in ("ID", "Depends On", "Blocked By", "Blocks Tasks", "Phase Link", "Module Link",
                     "Human Feedback", "Governing ADRs", "Agent Instructions", "Work Bucket", "Review",
                     "Project Key", "Source Revision", "Evidence"):
            self.assertIn(name, sources["tasks"])
        for kind in notion_sync.ORDER:
            for name, target, dual in notion_sync.DATABASES[kind]["relations"]:
                with self.subTest(kind=kind, relation=name):
                    relation = sources[kind][name]["relation"]
                    self.assertEqual(relation["data_source_id"], config["databases"][target]["data_source_id"])
                    if dual:
                        self.assertIn(dual, sources[target])
        for name in ("Source Assumption", "Source Instruction", "Target Task Link"):
            self.assertIn(name, sources["ledger"])
        hub = config["hubBlocks"]
        self.assertEqual(set(hub), set(notion_sync.HUB_MARKERS))
        self.assertEqual(set(hub.values()), {b["id"] for b in self.notion.blocks[self.instance]
                                              if b["type"] == "callout"})
        saved = self.saved()
        self.assertEqual((saved["projectKey"], saved["template"], saved["instance"], saved["portfolioPage"]),
                         ("DEMO", "PPMP", self.instance, self.portfolio))
        for field in ("databases", "shared", "hubBlocks"):
            self.assertEqual(saved[field], config[field])
        self.assertEqual(set(saved["databases"]), set(notion_sync.ORDER))

    def test_setup_binds_a_duplicated_template_and_adds_only_what_is_missing(self) -> None:
        copy = self.notion.add_page("PPMP · Sample | TEMPLATE (1)", self.portfolio)
        layer = self.notion.add_page("90 · Dữ liệu & Schema", copy)
        for kind, title in TEMPLATE_TITLES.items():
            self.notion.seed_database(layer, kind, title, drop=("Evidence",) if kind == "tasks" else ())
        # the template's callouts sit in columns
        row = self.notion.add_block(copy, "column_list", children=True)
        left = self.notion.add_block(row, "column", children=True)
        right = self.notion.add_block(row, "column", children=True)
        callouts = {}
        for i, (name, (first, label)) in enumerate(HUB_TEXTS.items()):
            callouts[name] = self.notion.add_block(left if i % 2 else right, "callout", first, children=True)
            self.notion.add_block(callouts[name], "paragraph", label)
        # a navigation line in the header, linking a page, is the owner's and must survive
        nav = self.notion.add_block(callouts["header"], "paragraph")
        self.notion.block(nav)["paragraph"] = {"rich_text": [
            {"type": "mention", "plain_text": "Roadmap", "mention": {"type": "page", "page": {"id": left}}}]}
        self.notion.seed_database(self.portfolio, "portfolio", "Project Portfolio", drop=("Health",))
        existing = next(b["id"] for b in self.notion.blocks[self.portfolio] if b["type"] == "child_database")
        before_databases = set(self.notion.databases)
        top_level = [b["id"] for b in self.notion.blocks[copy]]

        config = self.setup_notion(page=copy, name="Demo", key="DEMO")

        self.assertEqual(set(self.notion.databases), before_databases)   # none created
        self.assertEqual(set(config["databases"]), set(notion_sync.ORDER))
        tasks = self.notion.sources[config["databases"]["tasks"]["data_source_id"]]["properties"]
        self.assertIn("Evidence", tasks)
        self.assertIn("Depends On", tasks)
        # the existing shared board is bound, repaired, and not duplicated
        self.assertEqual(config["shared"]["database_id"], existing)
        self.assertIn("Health", self.notion.sources[config["shared"]["data_source_id"]]["properties"])
        self.assertEqual(_databases_under(self.notion, self.portfolio), {"Project Portfolio"})
        # the hub is the template's own callouts; no skeleton is appended
        self.assertEqual(config["hubBlocks"], callouts)
        self.assertEqual([b["id"] for b in self.notion.blocks[copy]], top_level)
        self.assertEqual(notion_sync.page_title(self.notion.pages[copy]), "Demo · Project Management")
        # push fills those callouts in place: first line = value, label in a child, links kept
        notion_sync.push(self.notion, self.dir, config)
        self.assertEqual([b["id"] for b in self.notion.blocks[copy]], top_level)
        self.assertEqual(lines(self.notion, callouts["done"]), ["1 / 3", "Execution tasks DONE"])
        header = self.notion.children(callouts["header"])
        self.assertTrue(any(p["type"] == "mention" for c in header for p in c["paragraph"]["rich_text"]))
        self.assertFalse(any("A template thesis." in notion_sync._block_text(c) for c in header))
        self.assertIn("Project Key DEMO", " ".join(lines(self.notion, callouts["header"])))

    def test_setup_again_changes_nothing(self) -> None:
        first = self.setup_notion(key="DEMO")
        writes, databases = self.notion.writes, len(self.notion.databases)
        blocks = {k: [b["id"] for b in v] for k, v in self.notion.blocks.items()}
        again = self.setup_notion(key="DEMO")
        self.assertEqual(self.notion.writes, writes)
        self.assertEqual(len(self.notion.databases), databases)
        self.assertEqual({k: [b["id"] for b in v] for k, v in self.notion.blocks.items()}, blocks)
        self.assertEqual(again, first)

    def test_setup_fills_the_hub_before_it_returns(self) -> None:
        """T-NOTION-CLONE-01: a cloned copy never shows the template's placeholders."""
        config = self.setup_notion(key="DEMO")
        project = compiler.as_json(compiler.load(self.dir))
        s = notion_sync.summary(project, "DEMO")
        done = " ".join(lines(self.notion, config["hubBlocks"]["done"]))
        self.assertIn(f"{s['done']} / {s['total']}", done)
        header = " ".join(lines(self.notion, config["hubBlocks"]["header"]))
        self.assertIn("Project Key DEMO", header)
        for name in ("header", "done", "phase", "reviewed", "gates", "owner"):
            self.assertNotIn("— / —", " ".join(lines(self.notion, config["hubBlocks"][name])), name)
            self.assertNotIn("[PROJECT NAME]", " ".join(lines(self.notion, config["hubBlocks"][name])), name)
        self.assertIn("hubRevision", self.saved())

    def test_setup_sets_the_project_icon_and_keeps_it(self) -> None:
        """T-NOTION-CLONE-01: the copy carries the project's own icon."""
        self.setup_notion(key="DEMO", icon="🦁")
        self.assertEqual(self.notion.pages[self.instance]["icon"], {"type": "emoji", "emoji": "🦁"})
        self.assertEqual(self.saved()["icon"], "🦁")
        self.notion.pages[self.instance]["icon"] = None
        self.setup_notion(key="DEMO")
        self.assertEqual(self.notion.pages[self.instance]["icon"], {"type": "emoji", "emoji": "🦁"})

    def test_setup_refuses_the_template_itself_and_writes_nothing(self) -> None:
        template = self.notion.add_page("PPMP · Praukron Project Management Protocol| TEMPLATE", self.portfolio)
        before = self.notion.writes
        with self.assertRaises(NotionError) as caught:
            self.setup_notion(page=template, key="DEMO")
        self.assertEqual(caught.exception.code, "template-page")
        self.assertEqual(self.notion.writes, before)
        self.assertFalse((self.dir / ".praukron" / "notion.json").exists())

    def test_setup_refuses_a_copy_with_no_parent_page(self) -> None:
        orphan = self.notion.add_page("PPMP · Orphan")
        before = self.notion.writes
        with self.assertRaises(NotionError) as caught:
            self.setup_notion(page=orphan, key="DEMO")
        self.assertEqual(caught.exception.code, "no-portfolio-page")
        self.assertEqual(self.notion.writes, before)


class PushTest(NotionCase):
    def project(self) -> dict:
        return compiler.as_json(compiler.load(self.dir))

    def hub_lines(self, name: str) -> list[str]:
        return lines(self.notion, self.config["hubBlocks"][name])

    def hub_text(self, name: str) -> str:
        return "\n".join(self.hub_lines(name))

    def test_push_mirrors_every_record_kind_with_relations(self) -> None:
        first = notion_sync.push(self.notion, self.dir, self.config)
        self.assertGreater(first.created, 0)
        wanted = notion_sync.records(self.dir, self.project())
        for kind in notion_sync.MIRRORED:
            with self.subTest(kind=kind):
                self.assertEqual({notion_sync.plain(r["properties"]["ID"]) for r in self.notion.rows(kind, self.config)},
                                 {r.pid for r in wanted if r.kind == kind})

        def page(kind: str, pid: str) -> dict:
            return self.notion.row(kind, self.config, pid)

        task = page("tasks", "T-TWO")
        self.assertEqual(notion_sync.plain(task["properties"]["Status"]), "TODO")
        self.assertEqual(notion_sync.plain(task["properties"]["Authority"]), "owner")
        self.assertEqual(notion_sync.plain(task["properties"]["Project Key"]), "DEMO")
        self.assertTrue(notion_sync.plain(task["properties"]["Source Revision"]))
        self.assertEqual(_ids(task, "Depends On"), {page("tasks", "T-ONE")["id"]})
        self.assertEqual(_ids(task, "Phase Link"), {page("phases", "P1")["id"]})
        self.assertEqual(_ids(task, "Module Link"), {page("modules", "M-FOUNDATION")["id"]})
        self.assertEqual(_ids(page("modules", "M-FOUNDATION"), "Tasks"),
                         {page("tasks", t)["id"] for t in ("T-ONE", "T-TWO", "T-THREE")})
        self.assertEqual(notion_sync.plain(page("phases", "Gate A")["properties"]["Kind"]), "GATE")
        self.assertEqual(notion_sync.plain(page("phases", "M-FIRST")["properties"]["Kind"]), "MILESTONE")
        adr = page("adrs", "ADR-001")
        self.assertEqual(notion_sync.plain(adr["properties"]["Kind"]), "ADR")
        self.assertIn("ADR-001", notion_sync.plain(adr["properties"]["Rationale"]))
        self.assertIn(page("tasks", "T-ONE")["id"], _ids(adr, "Governed Tasks"))
        criterion = page("acceptance", "AC-T-ONE-01")
        self.assertEqual(notion_sync.plain(criterion["properties"]["Result"]), "PASS")
        self.assertEqual(_ids(criterion, "Task Link"), {page("tasks", "T-ONE")["id"]})
        assumption = page("assumptions", "A-1")
        self.assertEqual(_ids(assumption, "Related Task Links"),
                         {page("tasks", "T-ONE")["id"], page("tasks", "T-TWO")["id"]})
        self.assertEqual(notion_sync.plain(assumption["properties"]["Delivery State"]), "NOT_SUBMITTED")
        self.assertEqual(notion_sync.plain(assumption["properties"]["Decision"]), "PENDING")
        reply = page("responses", "R-1")
        self.assertEqual(_ids(reply, "Assumption Link"), {assumption["id"]})
        self.assertEqual(notion_sync.plain(reply["properties"]["Action"]), "REVISE")
        self.assertEqual(_ids(page("responses", "R-2"), "Task Link"), {task["id"]})
        decision = page("decisions", "T-TWO")
        self.assertEqual(notion_sync.plain(decision["properties"]["Kind"]), "APPROVAL")
        self.assertEqual(notion_sync.plain(decision["properties"]["State"]), "OPEN")
        self.assertEqual(_ids(decision, "Task Link"), {task["id"]})
        self.assertEqual({r.pid for r in wanted if r.kind == "decisions"}, {"T-TWO"})
        blocker = page("blockers", "DEPENDENCY_BLOCKER:T-THREE")
        self.assertEqual(_ids(blocker, "Affected Tasks"), {task["id"]})
        self.assertEqual(_ids(blocker, "Root Task Link"), {page("tasks", "T-THREE")["id"]})

    def test_a_second_push_writes_nothing_and_keeps_one_sync_row(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        sync = self.notion.row("operations", self.config, notion_sync.SYNC_ROW)["properties"]
        self.assertEqual(notion_sync.plain(sync["Kind"]), "SYNC")
        self.assertEqual(notion_sync.plain(sync["Record ID"]), notion_sync.SYNC_ROW)
        writes = self.notion.writes
        second = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(self.notion.writes, writes)
        self.assertEqual((second.created, second.updated, second.archived, second.hub), (0, 0, 0, False))
        self.assertEqual(len(self.notion.rows("operations", self.config)), 1)

    def test_a_removed_assumption_is_trashed(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        page = self.notion.row("assumptions", self.config, "A-2")
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.split("## A-2")[0], encoding="utf-8")
        result = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(result.archived, 1)
        self.assertTrue(self.notion.pages[page["id"]]["in_trash"])
        self.assertEqual({notion_sync.plain(r["properties"]["ID"])
                          for r in self.notion.rows("assumptions", self.config)}, {"A-1"})
        writes = self.notion.writes
        notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(self.notion.writes, writes)

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

    def test_the_hub_callouts_show_the_summary_and_list_what_waits_for_the_owner(self) -> None:
        summary = notion_sync.summary(self.project(), "DEMO")
        self.assertEqual(summary["attention"], 3)
        self.assertTrue(notion_sync.push(self.notion, self.dir, self.config).hub)
        self.assertEqual(self.hub_lines("done"), [f"{summary['done']} / {summary['total']}", "Execution tasks DONE"])
        self.assertEqual(self.hub_lines("gates"), ["0 / 1", "GREEN phase gates"])
        self.assertIn("Project Key DEMO", self.hub_text("header"))
        self.assertIn("NEEDS_OWNER", self.hub_text("header"))
        owner = self.config["hubBlocks"]["owner"]
        self.assertIn("VIỆC CỦA BẠN", self.hub_lines("owner")[0])
        self.assertIn(f"{summary['attention']} mục", self.hub_lines("owner")[0])
        children = self.notion.children(owner)
        mentioned = {part["mention"]["page"]["id"] for child in children
                     if child["type"] == "bulleted_list_item"
                     for part in child["bulleted_list_item"]["rich_text"] if part["type"] == "mention"}
        self.assertEqual(mentioned, {self.notion.row("assumptions", self.config, "A-1")["id"],
                                     self.notion.row("assumptions", self.config, "A-2")["id"],
                                     self.notion.row("tasks", self.config, "T-TWO")["id"]})
        self.assertIn("Payment term defaults to thirty days", " ".join(notion_sync._block_text(c) for c in children))

    def test_both_portfolio_rows_carry_the_counts_and_health(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        summary = notion_sync.summary(self.project(), "DEMO")
        own, shared = self.notion.rows("portfolio", self.config), self.notion.shared_rows(self.config)
        self.assertEqual((len(own), len(shared)), (1, 1))
        for row in (own[0], shared[0]):
            props = row["properties"]
            self.assertEqual(props["Execution Done"]["number"], summary["done"])
            self.assertEqual(props["Execution Total"]["number"], summary["total"])
            self.assertEqual(props["Open Assumptions"]["number"], 2)
            self.assertEqual(props["Red Gates"]["number"], 1)
            self.assertEqual(notion_sync.plain(props["Health"]), "NEEDS_OWNER")
            self.assertEqual(notion_sync.plain(props["Project Key"]), "DEMO")
            self.assertTrue(props["Last Synced"]["date"]["start"])
            self.assertIn(self.instance.replace("-", ""), notion_sync.plain(props["Project Page"]))

    def test_a_second_push_leaves_the_hub_untouched_and_a_change_rewrites_it(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        before = {name: self.hub_lines(name) for name in self.config["hubBlocks"]}
        writes = self.notion.writes
        self.assertFalse(notion_sync.push(self.notion, self.dir, self.config).hub)
        self.assertEqual(self.notion.writes, writes)
        self.assertEqual({name: self.hub_lines(name) for name in self.config["hubBlocks"]}, before)
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.split("## A-2")[0], encoding="utf-8")
        self.assertTrue(notion_sync.push(self.notion, self.dir, self.config).hub)
        self.assertIn("2 mục", self.hub_lines("owner")[0])
        texts = " ".join(notion_sync._block_text(c) for c in self.notion.children(self.config["hubBlocks"]["owner"]))
        self.assertNotIn("Sales may read their own clients only", texts)
        self.assertEqual(self.saved()["hubBlocks"], self.config["hubBlocks"])

    def test_push_repairs_mirrored_values_changed_in_notion_and_leaves_owner_input(self) -> None:
        notion_sync.push(self.notion, self.dir, self.config)
        summary = notion_sync.summary(self.project(), "DEMO")
        task = self.notion.row("tasks", self.config, "T-TWO")["id"]
        shared = self.notion.shared_rows(self.config)[0]["id"]
        own = self.notion.rows("portfolio", self.config)[0]["id"]
        assumption = self.notion.row("assumptions", self.config, "A-1")["id"]
        statement = notion_sync.plain(self.notion.pages[assumption]["properties"]["Statement"])
        critical = self.notion.pages[task]["properties"]["Critical Path"]["checkbox"]
        # someone edits mirrored values in Notion; every Source Revision stays as push wrote it
        self.notion.edit(task, Status=notion_sync.select("DONE"), **{"Critical Path": notion_sync.checkbox(not critical)})
        self.notion.edit(shared, Health=notion_sync.select("ON_TRACK"), **{"Execution Done": notion_sync.number(99)})
        self.notion.edit(own, **{"Red Gates": notion_sync.number(0)})
        self.notion.edit(assumption, Statement=notion_sync.rich("tampered"), Feedback=notion_sync.rich("draft"),
                         Decision=notion_sync.select("REVISE"))
        result = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual(result.updated, 4)
        props = {row: self.notion.pages[row]["properties"] for row in (task, shared, own, assumption)}
        self.assertEqual(notion_sync.plain(props[task]["Status"]), "TODO")
        self.assertEqual(props[task]["Critical Path"]["checkbox"], critical)
        self.assertEqual(notion_sync.plain(props[shared]["Health"]), "NEEDS_OWNER")
        self.assertEqual(props[shared]["Execution Done"]["number"], summary["done"])
        self.assertEqual(props[own]["Red Gates"]["number"], 1)
        self.assertEqual(notion_sync.plain(props[assumption]["Statement"]), statement)
        self.assertEqual(notion_sync.plain(props[assumption]["Feedback"]), "draft")
        self.assertEqual(notion_sync.plain(props[assumption]["Decision"]), "REVISE")
        writes = self.notion.writes
        again = notion_sync.push(self.notion, self.dir, self.config)
        self.assertEqual((self.notion.writes, again.updated), (writes, 0))

    def test_a_hub_refresh_leaves_views_pages_and_links_under_a_callout_in_place(self) -> None:
        hub = self.config["hubBlocks"]
        header, owner = hub["header"], hub["owner"]
        view = self.notion.add_block(header, "child_database")
        page = self.notion.add_block(header, "child_page")
        link = self.notion.add_block(header, "link_to_page")
        toggle = self.notion.add_block(header, "toggle", "More views", children=True)
        nested = self.notion.add_block(toggle, "child_database")
        nav = self.notion.add_block(header, "paragraph")
        self.notion.block(nav)["paragraph"] = {"rich_text": [
            {"type": "mention", "plain_text": "Roadmap", "mention": {"type": "page", "page": {"id": page}}}]}
        board = self.notion.add_block(owner, "child_database")
        theirs = {header: [view, page, link, toggle, nav], owner: [board]}
        deleted: list[str] = []
        original = self.notion.delete_block

        def spy(block_id):
            deleted.append(block_id)
            return original(block_id)

        def kept() -> None:
            for callout, ids in theirs.items():
                present = [c["id"] for c in self.notion.children(callout)]
                self.assertEqual([i for i in present if i in ids], ids)   # same blocks, same order
            self.assertEqual([c["id"] for c in self.notion.children(toggle)], [nested])
            self.assertFalse(set(deleted) & ({i for ids in theirs.values() for i in ids} | {nested}))

        with mock.patch.object(self.notion, "delete_block", spy):
            self.assertTrue(notion_sync.push(self.notion, self.dir, self.config).hub)
            kept()
            self.assertIn("Project Key DEMO", self.hub_text("header"))
            generated = {name: list(ids) for name, ids in self.saved()["hubGenerated"].items()}
            self.assertFalse(set(generated["header"]) & set(theirs[header]))
            (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
                ASSUMPTIONS.split("## A-2")[0], encoding="utf-8")
            self.assertTrue(notion_sync.push(self.notion, self.dir, self.config).hub)
            kept()
            self.assertIn("2 mục", self.hub_lines("owner")[0])
            # the lines push wrote are updated where they are, not deleted and rebuilt
            self.assertEqual(self.saved()["hubGenerated"]["header"], generated["header"])
            self.assertEqual(self.saved()["hubGenerated"]["done"], generated["done"])

    def test_a_first_push_fills_the_hub_before_it_writes_any_row(self) -> None:
        # setup already filled it (T-NOTION-CLONE-01); push keeps it filled while rows load
        self.assertEqual(self.hub_lines("done")[0], "1 / 3")
        tasks = self.config["databases"]["tasks"]["data_source_id"]
        seen: list[list[str]] = []
        original = self.notion.create_page

        def spy(parent, properties, *a, **k):
            if parent.get("data_source_id") == tasks and not seen:
                seen.append(self.hub_lines("done"))
            return original(parent, properties, *a, **k)

        with mock.patch.object(self.notion, "create_page", spy):
            self.assertTrue(notion_sync.push(self.notion, self.dir, self.config).hub)
        self.assertEqual(seen, [["1 / 3", "Execution tasks DONE"]])
        # the end of the same push links the rows the hub names
        children = self.notion.children(self.config["hubBlocks"]["owner"])
        self.assertTrue(any(p["type"] == "mention" for c in children
                            for p in c[c["type"]].get("rich_text", [])))


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

    def ledgers(self, row_id: str) -> list[dict]:
        """The ledger rows of every request made from one Notion row."""
        return [p["properties"] for p in self.notion.rows("ledger", self.config)
                if notion_sync.plain(p["properties"]["Source Page ID"]).startswith(f"{row_id}@")]

    def ledger(self, row_id: str) -> dict:
        (found,) = self.ledgers(row_id)
        return found

    def ledger_count(self, row_id: str) -> int:
        return len(self.ledgers(row_id))

    def responses(self) -> str:
        return (self.dir / layout.AUTHORITY_DIR / "RESPONSES.md").read_text(encoding="utf-8")

    def recorded(self) -> list:
        return compiler.load(self.dir).responses

    def pull(self, **kw) -> notion_sync.PullResult:
        return notion_sync.pull(self.notion, self.dir, self.config, **kw)

    def pull_with_states(self) -> tuple[notion_sync.PullResult, list[str]]:
        states: list[str] = []
        original = notion_sync._mark

        def spy(client, row_id, state, *a, **k):
            states.append(state)
            return original(client, row_id, state, *a, **k)

        with mock.patch.object(notion_sync, "_mark", spy):
            return notion_sync.pull(self.notion, self.dir, self.config), states

    def test_owner_decision_lands_verbatim_with_mapped_login_and_receipt(self) -> None:
        row = self.submit_assumption(Decision=notion_sync.select("REVISE"),
                                     Feedback=notion_sync.rich("Mặc định 14 ngày."))
        self.assertEqual(self.state(row), "NOT_SUBMITTED")
        result, states = self.pull_with_states()
        self.assertEqual(result.applied, ["R-3"])
        self.assertEqual(states, ["QUEUED", "LOCAL_RECORDED", "APPLIED"])
        text = self.responses()
        self.assertIn("## R-3: A-1 REVISE\n- By: thien-owner", text)
        props = self.notion.pages[row]["properties"]
        request = notion_sync.plain(props["Request ID"])
        self.assertRegex(request, rf"^{row}@[0-9a-f]{{12}}$")
        self.assertIn(f"- Via: notion {OWNER_ID.replace('-', '')} request {request}\n", text)
        self.assertIn("> Mặc định 14 ngày.", text)
        self.assertEqual(props["Apply Evidence"]["type"], "rich_text")
        self.assertIn("R-3", notion_sync.plain(props["Apply Evidence"]))
        self.assertFalse(props["Submit"]["checkbox"])   # handled: a new tick is a new submission
        ledger = self.ledger(row)
        self.assertEqual([notion_sync.plain(ledger[k]) for k in ("ID", "Status", "Source Database", "Target ID",
                                                                  "Source Page ID")],
                         ["R-3", "APPLIED", "ASSUMPTION", "A-1", request])
        self.assertIn("R-3", notion_sync.plain(ledger["Action Title"]))
        self.assertEqual(_ids({"properties": ledger}, "Source Assumption"), {row})
        journal = json.loads((self.dir / ".praukron" / notion_sync.REQUESTS_FILE).read_text(encoding="utf-8"))
        entry = journal["requests"][request]
        self.assertEqual((entry["state"], entry["status"], entry["response"], entry["editor"]),
                         ("DONE", "APPLIED", "R-3", OWNER_ID.replace("-", "")))

    def test_a_submission_ticked_again_unchanged_is_written_once_with_one_ledger_row(self) -> None:
        row = self.submit_assumption()
        self.assertEqual(self.pull().applied, ["R-3"])
        writes = self.notion.writes
        self.assertEqual(self.pull().line(), "pull applied=0 rejected=0 conflicts=0 skipped=0")
        self.assertEqual(self.notion.writes, writes)   # an untouched re-poll writes nothing
        self.notion.edit(row, Submit=notion_sync.checkbox(True))
        again = self.pull()
        self.assertEqual((again.applied, again.skipped), ([], 1))
        self.assertEqual(self.responses().count("## R-3"), 1)
        self.assertNotIn("## R-4", self.responses())
        self.assertEqual(self.ledger_count(row), 1)
        self.assertEqual(self.state(row), "APPLIED")
        self.assertFalse(self.notion.pages[row]["properties"]["Submit"]["checkbox"])

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
        ledger = self.ledger(row)
        self.assertEqual(notion_sync.plain(ledger["Status"]), "CONFLICT")
        self.assertIn("changed", notion_sync.plain(ledger["Error"]))

    def test_a_stranger_is_rejected_and_the_reason_is_in_the_ledger(self) -> None:
        row = self.submit_assumption(editor=STRANGER)
        before = self.responses()
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(self.state(row), "REJECTED")
        self.assertEqual(self.responses(), before)
        ledger = self.ledger(row)
        self.assertEqual(notion_sync.plain(ledger["Status"]), "REJECTED")
        self.assertIn("not listed", notion_sync.plain(ledger["Error"]))
        self.assertIn(STRANGER.replace("-", ""), notion_sync.plain(ledger["Error"]))

    def test_revise_or_reject_without_feedback_is_rejected(self) -> None:
        for pid, decision in (("A-1", "REVISE"), ("A-2", "REJECT")):
            with self.subTest(decision=decision):
                row = self.submit_assumption(pid, Decision=notion_sync.select(decision))
                before = self.responses()
                result = notion_sync.pull(self.notion, self.dir, self.config)
                self.assertEqual(result.rejected, 1)
                self.assertEqual(self.state(row), "REJECTED")
                self.assertEqual(self.responses(), before)
                self.assertIn("Feedback", notion_sync.plain(self.ledger(row)["Error"]))
                self.assertFalse(self.notion.pages[row]["properties"]["Submit"]["checkbox"])

    def outbox(self, intent: str, target: str = "T-TWO", editor: str = OWNER_ID, heading: str = "",
               text: str = "Chạy trên staging trước.", submit: bool = True) -> str:
        """A row the owner writes in the Outbox (created blank, then filled in)."""
        source = self.config["databases"]["outbox"]["data_source_id"]
        task = self.notion.row("tasks", self.config, target)["id"] if target.startswith("T-") else None
        page = self.notion.create_page({"type": "data_source_id", "data_source_id": source},
                                       {"ID": notion_sync.title("Use staging")})
        self.notion.edit(page["id"], editor, **{
            "Instruction Title": notion_sync.rich(heading), "Instruction Text": notion_sync.rich(text),
            "Intent": notion_sync.select(intent), "Delivery State": notion_sync.select("DRAFT"),
            **({"Target Task Link": notion_sync.relation([task])} if task else
               {"Target ID": notion_sync.rich(target)}),
            **({"Submit": notion_sync.checkbox(True)} if submit else {}),
        })
        return page["id"]

    def test_instructions_become_guide_responses_on_their_target(self) -> None:
        for intent in ("IMPLEMENT", "FIX", "REVIEW", "REPLAN", "PAUSE", "OTHER"):
            with self.subTest(intent=intent):
                row = self.outbox(intent, heading="Dùng staging")
                _, states = self.pull_with_states()
                self.assertEqual(states, ["QUEUED", "LOCAL_RECORDED", "COMPILED", "APPLIED"])
                self.assertEqual(self.state(row), "APPLIED")
                ledger = self.ledger(row)
                self.assertEqual(notion_sync.plain(ledger["Source Database"]), "INSTRUCTION")
                self.assertEqual(_ids({"properties": ledger}, "Target Task Link"),
                                 {self.notion.row("tasks", self.config, "T-TWO")["id"]})
                self.assertEqual(_ids({"properties": ledger}, "Source Instruction"), {row})
        text = self.responses()
        self.assertIn("T-TWO GUIDE\n- By: thien-owner", text)
        self.assertIn("> Dùng staging\n>\n> Chạy trên staging trước.\n", text)
        # the worker's notes ride in the Via line, never inside the owner's words
        self.assertNotIn("(Notion", text)
        pause = [r for r in self.recorded() if r.via and r.via.endswith(" · intent PAUSE")]
        self.assertEqual([r.text for r in pause], ["Dùng staging\n\nChạy trên staging trước."])
        project = compiler.as_json(compiler.load(self.dir))
        self.assertTrue(any(r["target"] == "T-TWO" and "notion" in (r["via"] or "") for r in project["responses"]))

    def test_an_instruction_may_name_its_target_by_id(self) -> None:
        row = self.outbox("ANSWER", target="A-1")
        notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(self.state(row), "APPLIED")
        self.assertIn("A-1 GUIDE\n- By: thien-owner", self.responses())
        self.assertEqual(notion_sync.plain(self.ledger(row)["Target ID"]), "A-1")

    def test_an_instruction_without_a_target_is_rejected(self) -> None:
        row = self.outbox("IMPLEMENT", target="nothing")
        before = self.responses()
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(self.state(row), "REJECTED")
        self.assertEqual(self.responses(), before)

    def test_approve_from_an_owner_is_passed_as_verify(self) -> None:
        calls = []

        def fake(root, items, via, verify, by):
            calls.append((items[0].action, verify, by))
            return ["R-9"]

        row = self.outbox("APPROVE")
        notion_sync.pull(self.notion, self.dir, self.config, append=fake)
        self.assertEqual(calls, [("VERIFY", True, "thien-owner")])
        self.assertEqual(notion_sync.plain(self.ledger(row)["Source Database"]), "APPROVAL")
        self.assertEqual(notion_sync.plain(self.ledger(row)["ID"]), "R-9")

    def test_offline_submissions_are_applied_in_edit_order_when_back(self) -> None:
        first = self.outbox("GUIDE")
        second = self.submit_assumption("A-2", Decision=notion_sync.select("CONFIRM"))
        result = notion_sync.pull(self.notion, self.dir, self.config)
        self.assertEqual(result.applied, ["R-3", "R-4"])
        self.assertTrue(self.responses().index("T-TWO GUIDE") < self.responses().index("A-2 CONFIRM"))
        self.assertEqual({self.state(first), self.state(second)}, {"APPLIED"})

    # --- an interrupted request finishes exactly once (F2) -------------------

    def crash_after(self, method: str, when: Callable[..., bool] = lambda *a, **k: True):
        """The first call of `method` that matches takes effect in Notion, then the run dies."""
        original = getattr(self.notion, method)
        fired: list[bool] = []

        def wrapper(*args, **kw):
            out = original(*args, **kw)
            if not fired and when(*args, **kw):
                fired.append(True)
                raise Crash(method)
            return out

        return mock.patch.object(self.notion, method, wrapper)

    def interrupted(self, crash, kind: str = "assumptions", lose_journal: bool = False) -> str:
        row = self.outbox("FIX") if kind == "outbox" else self.submit_assumption(
            Decision=notion_sync.select("REVISE"), Feedback=notion_sync.rich("Dùng 14 ngày."))
        with crash, self.assertRaises(Crash):
            self.pull()
        if lose_journal:
            (self.dir / ".praukron" / notion_sync.REQUESTS_FILE).unlink()
        result = self.pull()
        self.assertEqual(result.applied, ["R-3"])
        text = self.responses()
        self.assertEqual(text.count("## R-3:"), 1)
        self.assertNotIn("## R-4", text)
        self.assertIn("- By: thien-owner", text.split("## R-3:")[1])
        self.assertEqual(self.ledger_count(row), 1)
        self.assertEqual(notion_sync.plain(self.ledger(row)["Status"]), "APPLIED")
        self.assertEqual(self.state(row), "APPLIED")
        self.assertFalse(self.notion.pages[row]["properties"]["Submit"]["checkbox"])
        writes = self.notion.writes
        self.assertEqual(self.pull().line(), "pull applied=0 rejected=0 conflicts=0 skipped=0")
        self.assertEqual(self.notion.writes, writes)
        return row

    def test_restart_after_the_queued_marker_keeps_the_owner_as_submitter(self) -> None:
        # the marker made the integration the row's last editor; the journal still knows the owner
        self.interrupted(self.crash_after("update_page", marks("QUEUED")))

    def test_restart_after_the_response_is_written_does_not_append_again(self) -> None:
        def append_then_die(*args, **kw):
            from praukron import respond
            respond.append(*args, **kw)
            raise Crash("append")

        row = self.submit_assumption(Decision=notion_sync.select("REVISE"), Feedback=notion_sync.rich("x"))
        with self.assertRaises(Crash):
            self.pull(append=append_then_die)
        self.assertEqual(self.state(row), "QUEUED")
        self.assertEqual(self.pull().applied, ["R-3"])
        self.assertNotIn("## R-4", self.responses())
        self.assertEqual(self.ledger_count(row), 1)
        self.assertEqual(self.state(row), "APPLIED")

    def test_restart_after_the_local_recorded_marker(self) -> None:
        self.interrupted(self.crash_after("update_page", marks("LOCAL_RECORDED")))

    def test_restart_after_the_ledger_row_is_created(self) -> None:
        ledger = self.config["databases"]["ledger"]["data_source_id"]
        self.interrupted(self.crash_after("create_page", lambda parent, *a, **k:
                                          parent.get("data_source_id") == ledger))

    def test_restart_after_the_compiled_marker_of_an_instruction(self) -> None:
        self.interrupted(self.crash_after("update_page", marks("COMPILED")), kind="outbox")

    def test_restart_after_the_applied_marker(self) -> None:
        self.interrupted(self.crash_after("update_page", marks("APPLIED")))

    def test_restart_without_the_journal_resumes_from_responses(self) -> None:
        ledger = self.config["databases"]["ledger"]["data_source_id"]
        self.interrupted(self.crash_after("create_page", lambda parent, *a, **k:
                                          parent.get("data_source_id") == ledger), lose_journal=True)

    # --- push and pull agree on a submitted row (F3) --------------------------

    def test_push_keeps_a_submitted_assumption_and_pull_finds_it_stale(self) -> None:
        row = self.submit_assumption()
        shown = notion_sync.plain(self.notion.pages[row]["properties"]["Source Revision"])
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.replace("default 30.", "default 45."), encoding="utf-8")
        notion_sync.push(self.notion, self.dir, self.config)
        page = self.notion.pages[row]
        self.assertEqual(notion_sync.plain(page["properties"]["Source Revision"]), shown)
        self.assertEqual(page["last_edited_by"]["id"], OWNER_ID)
        self.assertEqual(notion_sync.plain(page["properties"]["Decision"]), "CONFIRM")
        before = self.responses()
        result = self.pull()
        self.assertEqual((result.conflicts, result.rejected, result.applied), (1, 0, []))
        self.assertEqual(self.state(row), "CONFLICT")
        self.assertEqual(self.responses(), before)
        # once handled, the next push brings the row up to date and a new tick applies
        notion_sync.push(self.notion, self.dir, self.config)
        self.assertNotEqual(notion_sync.plain(self.notion.pages[row]["properties"]["Source Revision"]), shown)
        self.assertIn("45", notion_sync.plain(self.notion.pages[row]["properties"]["Statement"]))
        self.notion.edit(row, Submit=notion_sync.checkbox(True))
        self.assertEqual(self.pull().applied, ["R-3"])

    def test_push_does_not_trash_a_submitted_assumption_whose_record_is_gone(self) -> None:
        row = self.submit_assumption("A-2")
        (self.dir / layout.AUTHORITY_DIR / "ASSUMPTIONS.md").write_text(
            ASSUMPTIONS.split("## A-2")[0], encoding="utf-8")
        notion_sync.push(self.notion, self.dir, self.config)
        self.assertFalse(self.notion.pages[row]["in_trash"])
        self.assertEqual(self.pull().conflicts, 1)
        notion_sync.push(self.notion, self.dir, self.config)
        self.assertTrue(self.notion.pages[row]["in_trash"])

    # --- instructions and approvals carry their target's revision (F4) -------

    def test_a_stale_approval_or_instruction_conflicts_with_the_real_append(self) -> None:
        for intent in ("APPROVE", "VERIFY", "FIX"):
            with self.subTest(intent=intent):
                row = self.outbox(intent)
                self.notion.edit(row, **{"Source Revision": notion_sync.rich("stale-revision")})
                before = self.responses()
                result = self.pull()
                self.assertEqual((result.conflicts, result.applied), (1, []))
                self.assertEqual(self.state(row), "CONFLICT")
                self.assertEqual(self.responses(), before)
                self.assertEqual(notion_sync.plain(self.ledger(row)["Status"]), "CONFLICT")

    def test_a_current_approval_is_recorded_as_verify_with_the_real_append(self) -> None:
        row = self.outbox("APPROVE")   # not yet stamped: the task row it links shows the revision
        self.assertEqual(self.pull().applied, ["R-3"])
        self.assertIn("## R-3: T-TWO VERIFY\n- By: thien-owner", self.responses())
        self.assertEqual(self.state(row), "APPLIED")

    def test_push_stamps_an_open_instruction_and_a_later_change_makes_it_stale(self) -> None:
        row = self.outbox("APPROVE", submit=False)
        notion_sync.push(self.notion, self.dir, self.config)
        task = self.notion.row("tasks", self.config, "T-TWO")
        self.assertEqual(notion_sync.plain(self.notion.pages[row]["properties"]["Source Revision"]),
                         notion_sync.plain(task["properties"]["Source Revision"]))
        tasks = self.dir / layout.AUTHORITY_DIR / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace(
            "## T-TWO: Build on it", "## T-TWO: Build on it, carefully"), encoding="utf-8")
        self.notion.edit(row, Submit=notion_sync.checkbox(True))   # approved before the mirror caught up
        before = self.responses()
        self.assertEqual(self.pull().conflicts, 1)
        self.assertEqual(self.responses(), before)
        notion_sync.push(self.notion, self.dir, self.config)   # restamped, so the owner can resend
        self.notion.edit(row, Submit=notion_sync.checkbox(True))
        self.assertEqual(self.pull().applied, ["R-3"])
        self.assertIn("T-TWO VERIFY", self.responses())

    # --- every owner edit is its own submission (F5) --------------------------

    def test_a_rejected_row_can_be_corrected_and_sent_again(self) -> None:
        row = self.submit_assumption(Decision=notion_sync.select("REVISE"))
        self.assertEqual(self.pull().rejected, 1)
        self.assertFalse(self.notion.pages[row]["properties"]["Submit"]["checkbox"])
        self.notion.edit(row, Feedback=notion_sync.rich("Dùng 14 ngày."), Submit=notion_sync.checkbox(True))
        self.assertEqual(self.pull().applied, ["R-3"])
        self.assertEqual(self.state(row), "APPLIED")
        self.assertIn("## R-3: A-1 REVISE", self.responses())
        self.assertEqual(sorted(notion_sync.plain(p["Status"]) for p in self.ledgers(row)), ["APPLIED", "REJECTED"])

    def test_one_assumption_row_can_carry_a_second_decision(self) -> None:
        row = self.submit_assumption(Decision=notion_sync.select("REVISE"), Feedback=notion_sync.rich("14 ngày."))
        self.assertEqual(self.pull().applied, ["R-3"])
        self.notion.edit(row, Decision=notion_sync.select("CONFIRM"), Feedback=notion_sync.rich("Giữ 30 ngày."),
                         Submit=notion_sync.checkbox(True))
        self.assertEqual(self.pull().applied, ["R-4"])
        (r4,) = [r for r in self.recorded() if r.id == "R-4"]
        self.assertEqual((r4.target, r4.action, r4.text), ("A-1", "CONFIRM", "Giữ 30 ngày."))
        self.assertEqual(self.ledger_count(row), 2)
        self.assertEqual(self.pull().line(), "pull applied=0 rejected=0 conflicts=0 skipped=0")

    # --- the owner's words, exactly (F8) ---------------------------------------

    def test_feedback_and_instruction_text_land_exactly_as_written(self) -> None:
        row = self.submit_assumption(Decision=notion_sync.select("REVISE"), Feedback=notion_sync.rich(VERBATIM))
        self.outbox("FIX", text=VERBATIM)
        self.assertEqual(self.pull().applied, ["R-3", "R-4"])
        texts = {r.id: r.text for r in self.recorded()}
        self.assertEqual(texts["R-3"], VERBATIM)
        self.assertEqual(texts["R-4"], VERBATIM)
        self.assertEqual(self.state(row), "APPLIED")


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
