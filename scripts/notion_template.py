#!/usr/bin/env python3
"""Empty a PPMP template's databases; a repository tool, not installed.

    python scripts/notion_template.py clean <template-page-url> [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from praukron import notion_config  # noqa: E402
from praukron.notion_api import Client, NotionError  # noqa: E402
from praukron.notion_sync import page_id, page_title  # noqa: E402

CONTAINERS = {"child_page", "column_list", "column", "toggle", "callout"}


class Refusal(Exception):
    pass


def clean(client: Client, reference: str, dry_run: bool = False) -> list[tuple[str, int]]:
    """Trash every row below a template, leaving its databases and layout intact."""
    instance = page_id(reference)
    if not re.search(r"TEMPLATE\s*$", page_title(client.page(instance))):
        raise Refusal("refusing to clean: the page title must end with TEMPLATE; "
                      "a project's copy must never be emptied")

    counts: list[tuple[str, int]] = []
    queue = deque([instance])
    seen: set[str] = set()
    while queue:
        block_id = queue.popleft()
        if block_id in seen:
            continue
        seen.add(block_id)
        for block in client.children(block_id):
            kind = block.get("type")
            if kind == "child_database" and block["id"] not in seen:
                seen.add(block["id"])
                database = client.database(block["id"])
                count = 0
                for source in database.get("data_sources") or []:
                    rows = client.query(source["id"])
                    if not dry_run:
                        for row in rows:
                            client.update_page(row["id"], archived=True)
                    count += len(rows)
                counts.append((block["child_database"].get("title", block["id"]), count))
            elif kind in CONTAINERS and block.get("has_children"):
                queue.append(block["id"])
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("clean", help="trash all rows below a TEMPLATE page")
    command.add_argument("template_page_url")
    command.add_argument("--dry-run", action="store_true", help="count rows without writing")
    args = parser.parse_args(argv)
    token = notion_config.resolve(ROOT)
    try:
        counts = clean(Client(token), args.template_page_url, args.dry_run)
    except (Refusal, NotionError, OSError, ValueError) as error:
        print(notion_config.redact(str(error), token), file=sys.stderr)
        return 2
    action = "would be trashed" if args.dry_run else "trashed"
    for name, count in counts:
        print(notion_config.redact(f"{name}: {count} rows {action}", token))
    print(f"Total: {sum(count for _, count in counts)} rows {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
