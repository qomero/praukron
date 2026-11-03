"""The repository-only PPMP cleaner against an in-memory Notion client."""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("notion_template_tool", ROOT / "scripts" / "notion_template.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

from praukron.notion_api import Client, NotionError  # noqa: E402
from praukron.notion_config import Token  # noqa: E402

SECRET = "ntn_fake_template_secret_0000"
PAGE = "11111111-2222-3333-4444-555555555555"
URL = "https://www.notion.so/PPMP-" + PAGE.replace("-", "")


class FakeNotion(Client):
    def __init__(self, title: str = "PPMP · TEMPLATE") -> None:
        super().__init__(Token(SECRET, "environment"))
        self.title = title
        self.blocks: dict[str, list[dict]] = {PAGE: []}
        self.databases: dict[str, dict] = {}
        self.rows: dict[str, list[dict]] = {}
        self.writes: list[tuple[str, bool]] = []
        self.visited: list[str] = []

    def page(self, page_id: str) -> dict:
        assert page_id == PAGE
        return {"properties": {"title": {"type": "title", "title": [{"plain_text": self.title}]}}}

    def children(self, block_id: str) -> list[dict]:
        self.visited.append(block_id)
        return self.blocks[block_id]

    def database(self, database_id: str) -> dict:
        return self.databases[database_id]

    def query(self, data_source_id: str, filter=None) -> list[dict]:
        return [row for row in self.rows[data_source_id] if not row.get("in_trash")]

    def update_page(self, page_id: str, properties=None, archived=None) -> dict:
        assert properties is None and archived is True
        self.writes.append((page_id, archived))
        row = next(row for rows in self.rows.values() for row in rows if row["id"] == page_id)
        row["in_trash"] = archived
        return row

    def add_container(self, parent: str, kind: str) -> str:
        block_id = f"container-{len(self.blocks)}"
        self.blocks[parent].append({"id": block_id, "type": kind, "has_children": True})
        self.blocks[block_id] = []
        return block_id

    def add_database(self, parent: str, title: str, *sizes: int) -> None:
        database_id = f"database-{len(self.databases)}"
        self.blocks[parent].append({"id": database_id, "type": "child_database",
                                    "child_database": {"title": title}, "has_children": False})
        sources = []
        for index, size in enumerate(sizes):
            source = f"{database_id}-source-{index}"
            sources.append({"id": source})
            self.rows[source] = [{"id": f"{source}-row-{i}"} for i in range(size)]
        self.databases[database_id] = {"data_sources": sources}


class TemplateCleaner(unittest.TestCase):
    def run_tool(self, client: FakeNotion, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(tool.notion_config, "resolve", return_value=client._token) as resolve, \
                mock.patch.object(tool, "Client", return_value=client) as factory, \
                redirect_stdout(out), redirect_stderr(err):
            status = tool.main(["clean", URL, *args])
        resolve.assert_called_once_with(ROOT)
        factory.assert_called_once_with(client._token)
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        return status, out.getvalue(), err.getvalue()

    def fixture(self) -> FakeNotion:
        client = FakeNotion()
        client.add_database(PAGE, "Execution Tasks", 2, 3)
        parent = PAGE
        for kind in ("child_page", "column_list", "column", "toggle", "callout", "toggle"):
            parent = client.add_container(parent, kind)
        client.add_database(parent, "Human Decisions", 1)
        client.add_database(parent, "Action Ledger", 0)
        return client

    def test_refuses_non_template_before_traversal_or_writes(self):
        for title in ("Project · Project Management", "PPMP TEMPLATE copy", "", "PPMP template"):
            with self.subTest(title=title):
                client = self.fixture()
                client.title = title
                status, out, err = self.run_tool(client)
                self.assertEqual(status, 2)
                self.assertIn("title must end with TEMPLATE", err)
                self.assertEqual(out, "")
                self.assertEqual(client.writes, [])
                self.assertEqual(client.visited, [])

    def test_dry_run_counts_all_sources_without_writing(self):
        client = self.fixture()
        status, out, err = self.run_tool(client, "--dry-run")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "Execution Tasks: 5 rows would be trashed\n"
                             "Human Decisions: 1 rows would be trashed\n"
                             "Action Ledger: 0 rows would be trashed\n"
                             "Total: 6 rows would be trashed\n")
        self.assertEqual(client.writes, [])
        self.assertFalse(any(row.get("in_trash") for rows in client.rows.values() for row in rows))

    def test_clean_trashes_every_row_in_nested_databases(self):
        client = self.fixture()
        expected = {row["id"] for rows in client.rows.values() for row in rows}
        status, out, err = self.run_tool(client)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("Execution Tasks: 5 rows trashed", out)
        self.assertIn("Human Decisions: 1 rows trashed", out)
        self.assertIn("Action Ledger: 0 rows trashed", out)
        self.assertIn("Total: 6 rows trashed", out)
        self.assertEqual({row_id for row_id, _ in client.writes}, expected)
        self.assertEqual(len(client.writes), len(expected))
        self.assertTrue(all(row["in_trash"] for rows in client.rows.values() for row in rows))
        status, out, err = self.run_tool(client)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("Total: 0 rows trashed", out)

    def test_token_redacted_from_database_titles_and_errors(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                client = FakeNotion()
                client.add_database(PAGE, f"Private {SECRET}", 1)
                status, out, err = self.run_tool(client, *(["--dry-run"] if dry_run else []))
                self.assertEqual((status, err), (0, ""))
                self.assertIn("Private ***", out)
        client = FakeNotion()
        with mock.patch.object(client, "page", side_effect=NotionError(403, "forbidden", SECRET)):
            status, out, err = self.run_tool(client)
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("***", err)
        self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
