"""Append journal entries, with full-heading retry protection (ADR-068)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import mutate
from .layout import ARCHIVE_DIR, AUTHORITY_DIR
from .model import Project

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "date": {"type": "string", "format": "date", "description": "YYYY-MM-DD; defaults to today"},
        "title": {"type": "string", "minLength": 1, "description": "Non-empty, one line", "examples": ["Progress"]},
        "body": {"type": "string", "description": "Markdown without level one or two headings",
                 "examples": ["Recorded the result."]},
    },
    "required": ["title", "body"],
}
EXAMPLE = {"title": "Progress", "body": "Recorded the result."}


SCHEMA["description"] = "\n".join(mutate.GUIDANCE["journal.append"])
mutate.SCHEMAS["journal.append"] = (SCHEMA, ())


def append(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    """Preserve every existing byte and append one final section."""
    operation = "journal.append"
    try:
        mutate.validate_payload(payload, SCHEMA)
        date = payload.get("date", dt.date.today().isoformat())
        title, body = payload["title"], payload["body"]
        if not title.strip() or len(title.splitlines()) != 1 or title.endswith(("\r", "\n")):
            raise mutate.WriteError("invalid-title", "title must be non-empty and one line", "title")
        body = body.replace("\r\n", "\n").replace("\r", "\n")
        if re.search(r"(?m)^#{1,2} ", body):
            raise mutate.WriteError("invalid-body", "body may not contain level one or two headings", "body")
        heading = f"{date} — {title}"
        anchor = heading.rstrip()

        def prepare(project: Project) -> mutate.Plan:
            authority = Path(project.root) / AUTHORITY_DIR
            path = authority / "JOURNAL.md"
            for existing in [path, *sorted((authority / ARCHIVE_DIR).glob("*/JOURNAL.md"))]:
                if existing.is_file() and any(
                    h.decode("utf-8").strip() == anchor
                    for h in re.findall(rb"(?m)^## ([^\r\n]+)\r?$", existing.read_bytes())
                ):
                    raise mutate.WriteError("duplicate-heading", f"journal heading already exists: {heading}; the entry is already recorded, or change the title or date")
            original = path.read_bytes() if path.is_file() else b"# Journal\n"
            newline = b"\r\n" if b"\r\n" in original else b"\n"
            padding = b"" if original.endswith(newline * 2) else newline if original.endswith(newline) else newline * 2
            entry = (f"## {heading}\n\n{body}").encode("utf-8").replace(b"\n", newline)
            updated = original + padding + entry
            if not updated.endswith(newline):
                updated += newline
            return mutate.Plan({path: updated}, anchor, f"JOURNAL.md#{anchor}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)
