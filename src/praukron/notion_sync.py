"""Notion management: provision, push, pull and watch (ADR-087, ADR-088, ADR-090).

The chronicle is the only authority. Each project mirrors into its own copy of
the owner's PPMP template, a full project-control workspace with its
sample rows removed: `push` upserts the compiled project into the copy's databases,
keyed by the stable ID in each row's title, fills the copy's hub callouts in
place, and writes the project's row both to the copy's Project Portfolio and to
the one board every project shares; `pull` turns rows the owner submitted in
Notion into responses through `respond.append`, logs each one in the Action
Ledger, and writes the receipt back. Nothing read from Notion is ever treated
as project truth.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import layout
from .notion_api import Client, NotionError

CONFIG_FILE = "notion.json"
LOG_FILE = "notion.log"
LOCK_FILE = "notion.lock"
LOCK_STALE_SECONDS = 15 * 60
TEXT_LIMIT = 2000          # characters in one rich-text object
TEXT_PARTS = 50            # rich-text objects kept per property

# --------------------------------------------------------------------------
# Schema: the PPMP template, one set per project copy; the shared
# board uses the Project Portfolio schema.
# --------------------------------------------------------------------------


def _text() -> dict:
    return {"rich_text": {}}


def _number(fmt: str = "number") -> dict:
    return {"number": {"format": fmt}}


def _select(*names: str) -> dict:
    return {"select": {"options": [{"name": n} for n in names]}}


def _checkbox() -> dict:
    return {"checkbox": {}}


def _title() -> dict:
    return {"title": {}}


def _url() -> dict:
    return {"url": {}}


def _date() -> dict:
    return {"date": {}}


TITLE = "ID"   # every PPMP database titles a row by its stable ID
SYNC = {"Project Key": _text(), "Source Revision": _text()}
STATUS = ("TODO", "WIP", "DONE", "BLOCKED")
BUCKETS = ("READY", "IN_PROGRESS", "UPCOMING", "WAITING_HUMAN", "WAITING_DEPENDENCIES",
           "BLOCKED_FAILURE", "COMPLETED")
REVIEW = ("UNTESTED", "SYNTHETIC", "AI_REVIEWED", "HUMAN_VERIFIED")
DELIVERY = ("NOT_SUBMITTED", "QUEUED", "LOCAL_RECORDED", "APPLIED", "CONFLICT", "REJECTED")
OUTBOX_DELIVERY = ("DRAFT", "QUEUED", "LOCAL_RECORDED", "COMPILED", "APPLIED", "CONFLICT", "REJECTED")
LEDGER_STATUS = ("PENDING", "ACKNOWLEDGED", "LOCAL_RECORDED", "COMPILED", "APPLIED", "CONFLICT", "REJECTED")

PORTFOLIO = {
    "title": "Project Portfolio", "icon": "🪐",
    "properties": {
        TITLE: _title(), "Project Name": _text(), "Product Thesis": _text(), "Current Phase": _text(),
        "Project Key": _text(), "Source Revision": _text(), "Repo": _url(), "Project Page": _url(),
        "Last Synced": _date(), "Execution Done": _number(), "Execution Total": _number(),
        "Verified": _number(), "Open Assumptions": _number(), "Red Gates": _number(),
        "Health": _select("ON_TRACK", "NEEDS_OWNER", "BLOCKED", "UNKNOWN"),
    },
    "relations": [],
}

# Each kind: the data source title, its properties, and the relations added once
# every database exists: (property, target kind, dual property or None).
DATABASES: dict[str, dict[str, Any]] = {
    "tasks": {
        "title": "Execution Tasks", "icon": "✅",
        "properties": {
            TITLE: _title(), "Task Title": _text(), "Praukron ID": _text(), "Module": _text(),
            "Phase": _text(), "Owner": _text(), "Acceptance": _text(), "Governed By": _text(),
            "Evidence": _url(), "Source Link": _url(), "Downstream": _number(), "Critical Path": _checkbox(),
            "Status": _select(*STATUS), "Work Bucket": _select(*BUCKETS), "Review": _select(*REVIEW),
            "Authority": _select("owner", "worker"), "Domain": _select("execution", "operations"),
            "Evidence Notes": _text(), **SYNC,
        },
        "relations": [("Depends On", "tasks", None), ("Blocked By", "tasks", "Blocks Tasks"),
                      ("Phase Link", "phases", None), ("Module Link", "modules", None)],
    },
    "decisions": {
        "title": "Human Decisions", "icon": "🧑‍⚖️",
        "properties": {
            TITLE: _title(), "Decision Title": _text(), "Record ID": _text(), "Task IDs": _text(),
            "Instruction": _text(), "Response": _text(),
            "Kind": _select("ASSUMPTION", "INSTRUCTION", "APPROVAL", "FEEDBACK", "PERMISSION"),
            "State": _select("OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN", "SUBMITTED",
                             "ACKNOWLEDGED", "LOCAL_RECORDED", "APPLIED"),
            "Impact": _select("LOW", "MEDIUM", "HIGH"), **SYNC,
        },
        "relations": [("Task Link", "tasks", "Human Feedback")],
    },
    "phases": {
        "title": "Phases & Gates", "icon": "🚦",
        "properties": {
            TITLE: _title(), "Label": _text(), "Outcome": _text(), "Exit Authority": _text(),
            "Blocked By": _text(), "Verified By": _text(), "Record ID": _text(), "Source Anchor": _text(),
            "Source Link": _url(), "Kind": _select("PHASE", "GATE", "MILESTONE"),
            "State": _select("PLANNED", "ACTIVE", "EXIT_PENDING", "COMPLETE", "RED", "GREEN"), **SYNC,
        },
        "relations": [("Exit Task Link", "tasks", None)],
    },
    "operations": {
        "title": "Operations & Sync", "icon": "🔄",
        "properties": {
            TITLE: _title(), "Item Title": _text(), "State": _text(), "Linked Task": _text(),
            "Record ID": _text(), "Source Revision": _text(), "Evidence": _url(), "Last Sync": _date(),
            "Needs Owner": _checkbox(), "Kind": _select("OPERATION", "SYNC", "SECRETS_READINESS", "ERROR"),
        },
        "relations": [],
    },
    "portfolio": PORTFOLIO,
    "acceptance": {
        "title": "Acceptance & Evidence", "icon": "🧪",
        "properties": {
            TITLE: _title(), "Acceptance ID": _text(), "Criterion Text": _text(), "Task ID": _text(),
            "Phase": _text(), "Evidence Notes": _text(), "Source Anchor": _text(), "Evidence": _url(),
            "Result": _select("NOT_RUN", "PASS", "FAIL", "WAIVED"),
            "Validation Type": _select("TEST", "INSPECTION", "RUNTIME", "MANUAL"), **SYNC,
        },
        "relations": [("Task Link", "tasks", None)],
    },
    "blockers": {
        "title": "Blockers & Dependencies", "icon": "🚧",
        "properties": {
            TITLE: _title(), "Blocker Title": _text(), "Root Task": _text(), "Affected Task IDs": _text(),
            "Owner Action": _text(), "Evidence": _url(), "Downstream Count": _number(),
            "Blocker Kind": _select("HUMAN", "DEPENDENCY", "FAILURE", "PERMISSION", "OTHER"),
            "Status": _select("OPEN", "RESOLVED"), **SYNC,
        },
        "relations": [("Root Task Link", "tasks", None), ("Affected Tasks", "tasks", None)],
    },
    "adrs": {
        "title": "ADR & Technical Debt", "icon": "📜",
        "properties": {
            TITLE: _title(), "Record ID": _text(), "Record Title": _text(), "Decision Or Issue": _text(),
            "Rationale": _text(), "Supersedes": _text(), "Trigger": _text(), "State": _text(),
            "Linked Tasks": _text(), "Source Path": _text(), "Evidence": _url(),
            "Kind": _select("ADR", "TECHNICAL_DEBT"), **SYNC,
        },
        "relations": [("Governed Tasks", "tasks", "Governing ADRs"), ("Task Links", "tasks", None)],
    },
    "ledger": {
        "title": "Action Ledger", "icon": "🧾",
        "properties": {
            TITLE: _title(), "Action Title": _text(), "Requested Action": _text(), "Source Page ID": _text(),
            "Target ID": _text(), "Base Revision": _text(), "Error": _text(), "Project Key": _text(),
            "Result Evidence": _url(),
            "Source Database": _select("ASSUMPTION", "INSTRUCTION", "APPROVAL", "FEEDBACK"),
            "Status": _select(*LEDGER_STATUS),
        },
        "relations": [("Source Assumption", "assumptions", None), ("Source Instruction", "outbox", None),
                      ("Target Task Link", "tasks", None)],
    },
    "modules": {
        "title": "Product Modules", "icon": "🧩",
        "properties": {
            TITLE: _title(), "Module ID": _text(), "Module Name": _text(), "Outcome": _text(),
            "Phase ID": _text(), "Source Anchor": _text(), "Project Key": _text(), "Source Link": _url(),
            "Source Revision": _text(),
        },
        "relations": [("Phase Link", "phases", None), ("Tasks", "tasks", None)],
    },
    "assumptions": {
        "title": "Assumption Review Queue", "icon": "✏️",
        "properties": {
            TITLE: _title(), "Assumption ID": _text(), "Assumption Title": _text(), "Statement": _text(),
            "Basis": _text(), "Feedback": _text(), "Related Tasks": _text(), "Request ID": _text(),
            "Apply Evidence": _text(), "Source Anchor": _text(), "Source Link": _url(), "Submit": _checkbox(),
            "Impact": _select("LOW", "MEDIUM", "HIGH"),
            "Source State": _select("OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN"),
            "Decision": _select("PENDING", "CONFIRM", "REVISE", "REJECT", "WITHDRAW"),
            "Delivery State": _select(*DELIVERY), **SYNC,
        },
        "relations": [("Related Task Links", "tasks", None)],
    },
    "outbox": {
        "title": "Agent Instructions Outbox", "icon": "✉️",
        "properties": {
            TITLE: _title(), "Instruction Title": _text(), "Instruction Text": _text(), "Target ID": _text(),
            "Request ID": _text(), "Apply Evidence": _text(), "Submit": _checkbox(),
            "Intent": _select("IMPLEMENT", "REVIEW", "REPLAN", "FIX", "ANSWER", "PAUSE", "OTHER",
                              "GUIDE", "VERIFY", "APPROVE"),
            "Priority": _select("NORMAL", "HIGH", "URGENT"),
            "Target Type": _select("PROJECT", "PHASE", "MODULE", "TASK", "ASSUMPTION", "ADR"),
            "Delivery State": _select(*OUTBOX_DELIVERY), **SYNC,
        },
        "relations": [("Target Task Link", "tasks", "Agent Instructions")],
    },
    "responses": {
        "title": "Owner Response History", "icon": "📜",
        "properties": {
            TITLE: _title(), "Response ID": _text(), "Response Summary": _text(), "Target ID": _text(),
            "By": _text(), "Via": _text(), "Source Anchor": _text(), "Project Key": _text(),
            "Source Link": _url(), "Date": _date(),
            "Action": _select("GUIDE", "CONFIRM", "REVISE", "REJECT", "VERIFY"), "Source Revision": _text(),
        },
        "relations": [("Task Link", "tasks", None), ("Assumption Link", "assumptions", None)],
    },
}
ORDER = ["tasks", "decisions", "phases", "operations", "portfolio", "acceptance", "blockers", "adrs",
         "ledger", "modules", "assumptions", "outbox", "responses"]
# Kinds push owns row by row; a row whose record is gone is resolved or trashed.
MIRRORED = ("tasks", "decisions", "phases", "acceptance", "blockers", "adrs", "modules", "assumptions",
            "responses")
SYNC_ROW = "SYNC-NOTION-MIRROR"


# --------------------------------------------------------------------------
# Config and small helpers
# --------------------------------------------------------------------------


def config_path(root: Path) -> Path:
    return root / layout.HOME_DIR / CONFIG_FILE


def load_config(root: Path) -> dict:
    path = config_path(root)
    if not path.is_file():
        raise NotionError(0, "not-linked", "this project is not linked to Notion; run "
                          "`praukron notion setup <page-url>`")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    """Through a temporary file in the same directory, swapped in whole."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def save_config(root: Path, config: dict) -> None:
    write_json(config_path(root), config)


def page_id(reference: str) -> str:
    """A page id from a Notion URL or id, dashed."""
    found = re.findall(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                       reference)
    if not found:
        raise NotionError(0, "bad-page", f"no Notion page id in {reference!r}")
    raw = found[-1].replace("-", "").lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def bare(identifier: str | None) -> str:
    return (identifier or "").replace("-", "").lower()


def default_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-") or "PROJECT"


def text_parts(text: Any, bold: bool = False) -> list[dict]:
    text = "" if text is None else str(text)
    parts = [text[i:i + TEXT_LIMIT] for i in range(0, len(text), TEXT_LIMIT)][:TEXT_PARTS]
    out = [{"type": "text", "text": {"content": p}} for p in parts]
    if bold:
        for part in out:
            part["annotations"] = {"bold": True}
    return out


def rich(text: Any) -> dict:
    return {"rich_text": text_parts(text)}


def title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": str(text)[:TEXT_LIMIT]}}]}


def select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def number(value: float | int | None) -> dict:
    return {"number": value}


def checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def url(value: str | None) -> dict:
    return {"url": value or None}


def date(value: str | None) -> dict:
    return {"date": {"start": value} if value else None}


def relation(ids: Iterable[str]) -> dict:
    return {"relation": [{"id": i} for i in ids if i][:100]}


def plain(prop: dict | None) -> str:
    """The text of a property as Notion returns it."""
    if not prop:
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in prop.get(kind) or [])
    if kind == "select":
        return (prop.get("select") or {}).get("name", "")
    if kind == "checkbox":
        return "true" if prop.get("checkbox") else ""
    if kind == "relation":
        return ",".join(r["id"] for r in prop.get("relation") or [])
    if kind == "url":
        return prop.get("url") or ""
    return ""


def revision(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def page_url(identifier: str, label: str = "") -> str:
    """A Notion link to a page; the slug carries the label, so the link reads as it."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")
    return f"https://www.notion.so/{slug + '-' if slug else ''}{bare(identifier)}"


def log(root: Path, line: str) -> None:
    path = root / layout.HOME_DIR / LOG_FILE
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{now()} {line}\n")


class Locked(Exception):
    pass


class lock:
    """One watcher at a time per project; a lock older than 15 minutes is stale."""

    def __init__(self, root: Path) -> None:
        self.path = root / layout.HOME_DIR / LOCK_FILE

    def __enter__(self) -> "lock":
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - self.path.stat().st_mtime < LOCK_STALE_SECONDS:
                raise Locked(f"another Notion sync is running ({self.path})") from None
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Setup: bind the project to its PPMP copy and to the shared board
# --------------------------------------------------------------------------

CONTAINERS = {"child_page", "column_list", "column", "toggle", "synced_block", "callout"}


def canonical(name: str) -> str:
    """A data source title compared without a leading emoji, a `PPMP · ` prefix,
    a trailing `· <project>` suffix, or case."""
    name = re.sub(r"^[^\w]+", "", name or "")
    name = re.sub(r"^\s*PPMP\s*·\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*·\s*[^·]*$", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _source_title(source: dict) -> str:
    return "".join(part.get("plain_text", "") for part in source.get("title") or [])


def _walk_databases(client: Client, start: str, depth: int, wanted: dict[str, str],
                    found: dict[str, dict[str, str]]) -> None:
    seen: set[str] = set()
    queue = [(start, 0)]
    while queue:
        block_id, level = queue.pop(0)
        for block in client.children(block_id):
            kind = block.get("type")
            if kind == "child_database" and block["id"] not in seen:
                seen.add(block["id"])
                try:
                    sources = client.database(block["id"]).get("data_sources") or []
                except NotionError:
                    continue   # a linked view of a database elsewhere
                for ref in sources:
                    name = ref.get("name") or _source_title(client.data_source(ref["id"]))
                    match = wanted.get(canonical(name)) or wanted.get(
                        canonical(block["child_database"].get("title", "")))
                    if match and match not in found:
                        found[match] = {"database_id": block["id"], "data_source_id": ref["id"]}
            elif block.get("has_children") and kind in CONTAINERS and level < depth:
                queue.append((block["id"], level + 1))


def find_databases(client: Client, instance: str, depth: int = 4) -> dict[str, dict[str, str]]:
    """The PPMP databases anywhere below the copy, by kind.

    Duplicating the template gives every database a new id; they are found by
    their data source titles, so a renamed page or a moved database still binds.
    """
    wanted = {canonical(spec["title"]): kind for kind, spec in DATABASES.items()}
    found: dict[str, dict[str, str]] = {}
    _walk_databases(client, instance, depth, wanted, found)
    return found


def _data_source(client: Client, database_id: str) -> str:
    sources = client.database(database_id).get("data_sources") or []
    if not sources:
        raise NotionError(0, "no-data-source", f"database {database_id} has no data source")
    return sources[0]["id"]


def _add_missing(client: Client, source: str, properties: dict) -> None:
    have = client.data_source(source).get("properties", {})
    missing = {name: value for name, value in properties.items() if name not in have and "title" not in value}
    if missing:
        client.update_data_source(source, missing)


def _create(client: Client, parent: str, spec: dict) -> dict[str, str]:
    created = client.create_database(parent, spec["title"], spec["properties"], spec["icon"])
    source = (created.get("data_sources") or [{}])[0].get("id") or _data_source(client, created["id"])
    return {"database_id": created["id"], "data_source_id": source}


def provision(client: Client, instance: str) -> dict[str, dict[str, str]]:
    """Bind the PPMP databases below `instance`, create the ones it lacks, and add
    missing properties and relations. Nothing existing is changed or removed, and
    running it again changes nothing."""
    databases = find_databases(client, instance)
    for kind in ORDER:
        if kind in databases:
            _add_missing(client, databases[kind]["data_source_id"], DATABASES[kind]["properties"])
        else:
            databases[kind] = _create(client, instance, DATABASES[kind])
    for kind in ORDER:
        source = databases[kind]["data_source_id"]
        have = client.data_source(source).get("properties", {})
        add: dict[str, dict] = {}
        for name, target, dual in DATABASES[kind]["relations"]:
            if name in have:
                continue
            target_source = databases[target]["data_source_id"]
            target_has = have if target == kind else client.data_source(target_source).get("properties", {})
            if dual and dual not in target_has:
                add[name] = {"relation": {"data_source_id": target_source, "type": "dual_property",
                                          "dual_property": {"synced_property_name": dual}}}
            else:
                add[name] = {"relation": {"data_source_id": target_source, "type": "single_property",
                                          "single_property": {}}}
        if add:
            client.update_data_source(source, add)
    return databases


def shared_board(client: Client, parent: str) -> dict[str, str]:
    """The Project Portfolio directly under the page that holds every copy: the
    one board that lists all projects (ADR-090). Found by title, else created."""
    found: dict[str, dict[str, str]] = {}
    _walk_databases(client, parent, 0, {canonical(PORTFOLIO["title"]): "shared"}, found)
    if "shared" in found:
        _add_missing(client, found["shared"]["data_source_id"], PORTFOLIO["properties"])
        return found["shared"]
    return _create(client, parent, PORTFOLIO)


def page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain(prop)
    return ""


def setup(client: Client, root: Path, instance_reference: str, key: str | None = None,
          name: str | None = None) -> dict:
    from . import compile as compiler

    project = compiler.as_json(compiler.load(root))
    name = name or project["project"]["name"]
    key = key or default_key(name)
    instance = page_id(instance_reference)
    page = client.page(instance)
    current = page_title(page)
    if re.search(r"TEMPLATE\s*$", current):
        raise NotionError(0, "template-page",
                          f"'{current}' is the PPMP template itself. Duplicate it in Notion "
                          "(page menu > Duplicate), then run setup on the copy (ADR-090).")
    parent = (page.get("parent") or {}).get("page_id")
    if not parent:
        raise NotionError(0, "no-portfolio-page",
                          "put the copy inside the page that holds your projects (for example "
                          "Praukron · Portfolio), where the shared Project Portfolio lives (ADR-090)")
    me = client.me()
    databases = provision(client, instance)
    shared = shared_board(client, parent)
    if "TEMPLATE" in current or not current:
        client.update_page(instance, {"title": title(f"{name} · Project Management")})
    previous = json.loads(config_path(root).read_text(encoding="utf-8")) if config_path(root).is_file() else {}
    same = previous.get("instance") == instance
    hub = previous.get("hubBlocks") if same else None
    config = {
        "version": 3,
        "template": "PPMP",
        "workspace": (me.get("bot") or {}).get("workspace_name"),
        "instance": instance,
        "portfolioPage": parent,
        "projectKey": key,
        "projectName": name,
        "databases": databases,
        "shared": shared,
        "hubBlocks": hub or hub_blocks_found(client, instance) or add_hub_skeleton(client, instance),
    }
    if same and hub and previous.get("hubGenerated"):
        config["hubGenerated"] = previous["hubGenerated"]   # the lines push wrote stay push's
    save_config(root, config)
    return config


# --------------------------------------------------------------------------
# Records: compiled project → rows
# --------------------------------------------------------------------------


@dataclass
class Record:
    kind: str
    pid: str              # the stable ID, unique within the project
    props: dict           # properties without relations
    links: dict = field(default_factory=dict)   # property → [(kind, pid)]


def _bucket(task: dict, owner_held: bool) -> str:
    status = task["status"]
    if status == "DONE":
        return "COMPLETED"
    if status == "WIP":
        return "IN_PROGRESS"
    if status == "BLOCKED":
        return "BLOCKED_FAILURE"
    if task.get("ready"):
        return "WAITING_HUMAN" if owner_held else "READY"
    if task.get("blocked"):
        return "WAITING_DEPENDENCIES"
    return "UPCOMING"


def _adr_body(root: Path, source: dict | None) -> str:
    if not source:
        return ""
    path = root / layout.AUTHORITY_DIR / source["file"]
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _anchor(source: dict | None) -> str:
    return f"{source['file']}#{source.get('anchor', '')}" if source else ""


RESULT = {"PASS": "PASS", "FAIL": "FAIL", "WAIVED": "WAIVED"}
DEBT_STATE = {"ACCEPTED": "ACCEPTED", "OPEN": "OPEN", "SCHEDULED": "SCHEDULED", "PAID": "RESOLVED",
              "RESOLVED": "RESOLVED", "INVALIDATED": "INVALIDATED", "WONTFIX": "INVALIDATED"}
ACTIONS = ("GUIDE", "CONFIRM", "REVISE", "REJECT", "VERIFY")


def _gate_state(status: str | None) -> str:
    status = (status or "").upper()
    return status if status in ("GREEN", "RED") else "PLANNED"


def downstream_counts(tasks: list[dict]) -> dict[str, int]:
    by_id = {t["id"]: t for t in tasks}
    counts: dict[str, int] = {}
    for t in tasks:
        seen: set[str] = set()
        stack = list(t.get("blocks") or [])
        while stack:
            other = stack.pop()
            if other not in seen:
                seen.add(other)
                stack.extend(by_id.get(other, {}).get("blocks") or [])
        counts[t["id"]] = len(seen)
    return counts


def records(root: Path, project: dict) -> list[Record]:
    out: list[Record] = []
    tasks = project["tasks"]
    critical = set(project.get("criticalPath") or [])
    downstream = downstream_counts(tasks)
    governed: dict[str, list[str]] = {}
    for t in tasks:
        for d in t.get("decisions") or []:
            governed.setdefault(d, []).append(t["id"])
    latest: dict[str, dict] = {}
    for response in project.get("responses") or []:
        latest[response["target"]] = response

    for phase in project["phases"]:
        out.append(Record("phases", phase["id"], {
            "Label": rich(phase["name"]), "Kind": select("PHASE"), "State": select(phase["status"]),
            "Outcome": rich("\n".join(filter(None, [phase.get("outcome"), *(phase.get("exit") or [])]))),
            "Exit Authority": rich(phase.get("exitAuthority")),
            "Blocked By": rich(", ".join(phase.get("waitsOn") or [])),
            "Record ID": rich(phase["id"]), "Source Anchor": rich(_anchor(phase.get("source"))),
        }, {"Exit Task Link": [("tasks", phase.get("exitAuthority"))]}))
    for gate in project.get("gates") or []:
        out.append(Record("phases", gate["id"], {
            "Label": rich(gate.get("name")), "Kind": select("GATE"), "State": select(_gate_state(gate.get("status"))),
            "Outcome": rich(gate.get("description")), "Blocked By": rich(", ".join(gate.get("blocks") or [])),
            "Verified By": rich(", ".join(gate.get("verifiedBy") or [])),
            "Record ID": rich(gate["id"]), "Source Anchor": rich(_anchor(gate.get("source"))),
        }))
    for milestone in project.get("milestones") or []:
        out.append(Record("phases", milestone["id"], {
            "Label": rich(milestone.get("text")), "Kind": select("MILESTONE"),
            "State": select("COMPLETE" if milestone.get("reached") else "PLANNED"),
            "Exit Authority": rich(milestone.get("task")), "Record ID": rich(milestone["id"]),
            "Source Anchor": rich(_anchor(milestone.get("source"))),
        }, {"Exit Task Link": [("tasks", milestone.get("task"))]}))
    for module in project.get("modules") or []:
        out.append(Record("modules", module["id"], {
            "Module ID": rich(module["id"]), "Module Name": rich(module.get("name")),
            "Outcome": rich(module.get("outcome")), "Phase ID": rich(module.get("phase")),
            "Source Anchor": rich(_anchor(module.get("source"))),
        }, {"Phase Link": [("phases", module.get("phase"))],
            "Tasks": [("tasks", t) for t in module.get("tasks") or []]}))
    for decision in project["decisions"]:
        out.append(Record("adrs", decision["id"], {
            "Kind": select("ADR"), "Record ID": rich(decision["id"]), "Record Title": rich(decision["title"]),
            "Decision Or Issue": rich(decision["title"]),
            "Rationale": rich(_adr_body(root, decision.get("source"))),
            "State": rich((decision.get("status") or "").upper()),
            "Supersedes": rich(", ".join(decision.get("supersedes") or [])),
            "Linked Tasks": rich(", ".join(governed.get(decision["id"], []))),
            "Source Path": rich(_anchor(decision.get("source"))),
        }, {"Governed Tasks": [("tasks", t) for t in governed.get(decision["id"], [])],
            "Task Links": [("tasks", a) for a in decision.get("affects") or [] if a.startswith("T-")]}))
    for debt in project.get("debt") or []:
        linked = [i for i in debt.get("introducedBy") or [] if i.startswith("T-")]
        out.append(Record("adrs", debt["id"], {
            "Kind": select("TECHNICAL_DEBT"), "Record ID": rich(debt["id"]), "Record Title": rich(debt.get("title")),
            "Decision Or Issue": rich(debt.get("debt")),
            "Rationale": rich("\n\n".join(filter(None, [debt.get("reason"), debt.get("interest"),
                                                         debt.get("exitCondition")]))),
            "Trigger": rich(debt.get("trigger")),
            "State": rich(DEBT_STATE.get((debt.get("status") or "").upper(), "OPEN")),
            "Linked Tasks": rich(", ".join(debt.get("introducedBy") or [])),
            "Source Path": rich(_anchor(debt.get("source"))),
        }, {"Task Links": [("tasks", t) for t in linked]}))
    acceptance = project.get("acceptance") or {}
    for t in tasks:
        owner_held = (t.get("authority") or "") == "owner"
        contract = acceptance.get(t.get("ac") or "") or {}
        criteria = contract.get("criteria") or []
        passed = sum(c.get("state") == "PASS" for c in criteria)
        out.append(Record("tasks", t["id"], {
            "Task Title": rich(t["title"]), "Praukron ID": rich(t["id"]), "Status": select(t["status"]),
            "Work Bucket": select(_bucket(t, owner_held)), "Review": select(t.get("validation")),
            "Authority": select("owner" if owner_held else "worker"),
            "Domain": select(t.get("domain")), "Phase": rich(t.get("phase")),
            "Module": rich(t.get("module")), "Owner": rich(t.get("owner")),
            "Critical Path": checkbox(t["id"] in critical),
            "Downstream": number(downstream.get(t["id"], 0)),
            "Acceptance": rich(f"{t.get('ac') or ''} · {passed}/{len(criteria)} PASS" if criteria else t.get("ac")),
            "Governed By": rich(", ".join(t.get("decisions") or [])),
            "Evidence Notes": rich(t.get("evidence")),
        }, {
            "Depends On": [("tasks", d) for d in t.get("deps") or []],
            "Blocked By": [("tasks", d) for d in t.get("deps") or []],
            "Phase Link": [("phases", t.get("phase"))],
            "Module Link": [("modules", t.get("module"))],
        }))
        if owner_held:
            response = latest.get(t["id"])
            state = "APPLIED" if t["status"] == "DONE" else (
                "SUBMITTED" if response and response.get("action") == "VERIFY" else "OPEN")
            behind = downstream.get(t["id"], 0)
            out.append(Record("decisions", t["id"], {
                "Decision Title": rich(t["title"]), "Record ID": rich(t["id"]), "Task IDs": rich(t["id"]),
                "Kind": select("APPROVAL"), "State": select(state),
                "Impact": select("HIGH" if behind >= 5 else "MEDIUM" if behind else "LOW"),
                "Response": rich(f"{response['id']} {response['action']}: {response['text']}" if response else ""),
            }, {"Task Link": [("tasks", t["id"])]}))
        for criterion in criteria:
            out.append(Record("acceptance", criterion["id"], {
                "Acceptance ID": rich(criterion["id"]), "Criterion Text": rich(criterion.get("text")),
                "Validation Type": select(criterion.get("evidenceClass")),
                "Result": select(RESULT.get(criterion.get("state") or "", "NOT_RUN")),
                "Task ID": rich(t["id"]), "Phase": rich(t.get("phase")),
                "Evidence Notes": rich(criterion.get("evidence")),
            }, {"Task Link": [("tasks", t["id"])]}))
    for obstacle in project.get("obstacles") or []:
        kind = "HUMAN" if obstacle.get("actor") in ("owner", "human") else (
            "DEPENDENCY" if "DEPENDENCY" in obstacle["type"] else (
                "FAILURE" if "FAIL" in obstacle["type"] else (
                    "PERMISSION" if "PERMISSION" in obstacle["type"] else "OTHER")))
        pid = f"{obstacle['type']}:{obstacle['subject']}"
        affected = obstacle.get("blockers") or []
        out.append(Record("blockers", pid, {
            "Blocker Title": rich(obstacle.get("detail")), "Blocker Kind": select(kind),
            "Root Task": rich(obstacle["subject"]), "Affected Task IDs": rich(", ".join(affected)),
            "Owner Action": rich(obstacle.get("unblock")),
            "Downstream Count": number(downstream.get(obstacle["subject"], 0)),
            "Status": select("OPEN"),
        }, {"Root Task Link": [("tasks", obstacle["subject"])],
            "Affected Tasks": [("tasks", a) for a in affected]}))
    for assumption in project.get("assumptions") or []:
        out.append(Record("assumptions", assumption["id"], {
            "Assumption ID": rich(assumption["id"]), "Assumption Title": rich(assumption["title"]),
            "Statement": rich(assumption.get("assumption")), "Basis": rich(assumption.get("basis")),
            "Related Tasks": rich(", ".join(assumption.get("tasks") or [])),
            "Impact": select(assumption.get("impact")), "Source State": select(assumption.get("status")),
            "Source Anchor": rich(_anchor(assumption.get("source"))),
        }, {"Related Task Links": [("tasks", task) for task in assumption.get("tasks") or []]}))
    for response in project.get("responses") or []:
        target = response.get("target") or ""
        out.append(Record("responses", response["id"], {
            "Response ID": rich(response["id"]), "Response Summary": rich(response.get("text")),
            "Target ID": rich(target), "By": rich(response.get("by")), "Via": rich(response.get("via")),
            "Date": date(response.get("date")),
            "Action": select(response.get("action") if response.get("action") in ACTIONS else None),
            "Source Anchor": rich(_anchor(response.get("source"))),
        }, {"Task Link": [("tasks", target)] if target.startswith("T-") else [],
            "Assumption Link": [("assumptions", target)] if target.startswith("A-") else []}))
    return out


def revision_of(record: Record) -> str:
    """The Source Revision push writes on a record's row: its values without
    relations (they depend on page ids), so pull can compute it from the chronicle
    alone and tell a submission about what is current from a stale one."""
    return revision(record.props)


# --------------------------------------------------------------------------
# Summary: what the hub and the Portfolio rows show
# --------------------------------------------------------------------------


def summary(project: dict, key: str) -> dict:
    tasks = project["tasks"]
    execution = [t for t in tasks if t.get("domain") == "execution"]
    done = sum(t["status"] == "DONE" for t in execution)
    reviewed = sum(t.get("validation") in ("AI_REVIEWED", "HUMAN_VERIFIED") for t in execution)
    phase_id = project["project"].get("currentPhase")
    phase = next((p for p in project["phases"] if p["id"] == phase_id), None)
    in_phase = [t for t in execution if t.get("phase") == phase_id]
    gates = project.get("gates") or []
    downstream = downstream_counts(tasks)
    impact_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    open_assumptions = sorted((a for a in project.get("assumptions") or [] if a.get("status") == "OPEN"),
                              key=lambda a: (impact_order.get(a.get("impact"), 3), a["id"]))
    owner_open = [t for t in tasks if (t.get("authority") or "") == "owner" and t["status"] != "DONE"]
    waiting = sorted((t for t in owner_open if t.get("ready")), key=lambda t: (-downstream.get(t["id"], 0), t["id"]))
    main = (project.get("execution") or {}).get("mainBlocker")
    attention = len(open_assumptions) + len(waiting)
    thesis = project.get("thesis") or {}
    return {
        "key": key, "name": project["project"]["name"],
        "thesis": (thesis.get("statement") if isinstance(thesis, dict) else str(thesis)) or "",
        "phaseId": phase_id or "", "phaseName": phase["name"] if phase else "",
        "phaseStatus": phase["status"] if phase else "NONE",
        "done": done, "total": len(execution), "reviewed": reviewed,
        "phaseDone": sum(t["status"] == "DONE" for t in in_phase), "phaseTotal": len(in_phase),
        "gatesGreen": sum((g.get("status") or "").upper() == "GREEN" for g in gates),
        "gatesRed": sum((g.get("status") or "").upper() == "RED" for g in gates), "gates": len(gates),
        "attention": attention,
        "health": "NEEDS_OWNER" if attention else ("BLOCKED" if main else "ON_TRACK"),
        "mainBlocker": (main.get("detail") or main.get("id") or "") if isinstance(main, dict) else str(main or ""),
        "assumptions": [(a["id"], a["title"], a.get("impact") or "") for a in open_assumptions],
        "ownerTasks": [(t["id"], t["title"], downstream.get(t["id"], 0)) for t in waiting],
        "ownerLater": [t["id"] for t in owner_open if not t.get("ready")],
    }


# --------------------------------------------------------------------------
# The hub: the copy's own callouts, filled in place (ADR-090)
# --------------------------------------------------------------------------

# Which callout is which, by the text the template and push both write.
HUB_MARKERS = {
    "header": r"PROJECT CONTROL CENTER",
    "done": r"Execution tasks DONE",
    "phase": r"execution · .*DONE|PHASE · DONE",
    "reviewed": r"AI reviewed / human verified",
    "gates": r"GREEN phase gates",
    "owner": r"VIỆC CỦA BẠN",
    "product": r"THE PRODUCT",
}


def _block_text(block: dict) -> str:
    body = block.get(block.get("type"), {}) or {}
    return "".join(part.get("plain_text") or (part.get("text") or {}).get("content", "")
                   for part in body.get("rich_text") or [])


def hub_blocks_found(client: Client, instance: str) -> dict[str, str]:
    """The ids of the hub callouts push fills, found on the copy's own page."""
    found: dict[str, str] = {}
    queue = [instance]
    while queue:
        for block in client.children(queue.pop(0)):
            kind = block.get("type")
            if kind == "callout":
                # The template puts a callout's label lines in child blocks.
                text = _block_text(block)
                if block.get("has_children"):
                    text += "\n" + "\n".join(_block_text(child) for child in client.children(block["id"]))
                for name, pattern in HUB_MARKERS.items():
                    if name not in found and re.search(pattern, text):
                        found[name] = block["id"]
                        break
            elif kind in ("column_list", "column") and block.get("has_children"):
                queue.append(block["id"])
    return found


def _callout(parts: list[dict], icon: str, color: str = "gray_background",
             children: list[dict] | None = None) -> dict:
    body = {"rich_text": parts, "icon": {"type": "emoji", "emoji": icon}, "color": color}
    if children:
        body["children"] = children
    return {"type": "callout", "callout": body}


def add_hub_skeleton(client: Client, instance: str) -> dict[str, str]:
    """A page without the template's callouts gets them, so push has a place to write."""
    skeleton = [
        ("🪐", "purple_background", "[PROJECT NAME] / PROJECT CONTROL CENTER", None),
        ("✅", "gray_background", "— / —", "Execution tasks DONE"),
        ("📍", "purple_background", "— / —", "PHASE · DONE"),
        ("🔎", "gray_background", "— / —", "AI reviewed / human verified"),
        ("🚦", "gray_background", "— / —", "GREEN phase gates"),
        ("✏️", "red_background", "VIỆC CỦA BẠN", None),
        ("🧭", "gray_background", "THE PRODUCT", None),
    ]
    client.append(instance, [_callout(text_parts(text), icon, color,
                                      [_para(text_parts(label))] if label else None)
                             for icon, color, text, label in skeleton])
    return hub_blocks_found(client, instance)


def _mention(page: str | None, label: str) -> dict:
    if page:
        return {"type": "mention", "mention": {"type": "page", "page": {"id": page}}}
    return {"type": "text", "text": {"content": label}}


def _db_mention(database: str) -> dict:
    return {"type": "mention", "mention": {"type": "database", "database": {"id": database}}}


def _para(parts: list[dict], kind: str = "paragraph") -> dict:
    return {"type": kind, kind: {"rich_text": parts}}


def hub_content(s: dict, config: dict,
                pages: dict[tuple[str, str], str]) -> dict[str, tuple[list[dict], list[dict]]]:
    """What each hub callout says, laid out as the template does: (first line, the
    child lines push writes)."""
    def kpi(value: str, label: str) -> tuple[list[dict], list[dict]]:
        return text_parts(value, bold=True), [_para(text_parts(label))]

    pct = round(100 * s["phaseDone"] / s["phaseTotal"]) if s["phaseTotal"] else 0
    dbs = config["databases"]
    header = (text_parts(f"{s['name'].upper()} / PROJECT CONTROL CENTER", bold=True)
              + text_parts(" · Executive view & delivery intelligence"))
    header_lines = [
        _para(text_parts(s["thesis"], bold=True)),
        _para(text_parts(f"{s['phaseId']} {s['phaseStatus']}", bold=True) + text_parts(" · Health: ")
              + text_parts(s["health"], bold=True)
              + text_parts(f" — {s['attention']} việc chờ chủ dự án · Project Key {s['key']} · "
                           f"Snapshot {_dt.date.today().isoformat()} · đồng bộ từ Praukron chronicle")),
    ]
    children = [_para(text_parts("Chỉ có ") + text_parts("2 nơi bạn nhập liệu", bold=True)
                      + text_parts(": ") + [_db_mention(dbs["assumptions"]["database_id"])]
                      + text_parts(" và ") + [_db_mention(dbs["outbox"]["database_id"])]
                      + text_parts(". Mọi bảng khác chỉ để xem — đừng sửa."))]
    if s["assumptions"]:
        children.append(_para(text_parts(f"① Duyệt {len(s['assumptions'])} assumption", bold=True)
                              + text_parts(" — mở mục → chọn Decision → ghi Feedback → tick Submit")))
        for aid, name, impact in s["assumptions"]:
            children.append(_para([_mention(pages.get(("assumptions", aid)), aid)]
                                  + text_parts(f" · {name} — ") + text_parts(impact, bold=impact == "HIGH"),
                                  "bulleted_list_item"))
    if s["ownerTasks"]:
        children.append(_para(text_parts(f"② Trả lời {len(s['ownerTasks'])} việc chỉ bạn quyết được", bold=True)
                              + text_parts(" — tạo instruction trong Outbox, gắn task, ghi quyết định, tick Submit")))
        for tid, name, behind in s["ownerTasks"]:
            children.append(_para([_mention(pages.get(("tasks", tid)), tid)] + text_parts(f" · {name} — ")
                                  + text_parts(f"{behind} task phía sau", bold=behind >= 5),
                                  "bulleted_list_item"))
    if s["ownerLater"]:
        more = "…" if len(s["ownerLater"]) > 3 else ""
        children.append(_para(text_parts(f"Chưa cần làm: {len(s['ownerLater'])} task owner khác "
                                         f"({', '.join(s['ownerLater'][:3])}{more}) đang chờ phụ thuộc.")))
    if not (s["assumptions"] or s["ownerTasks"]):
        children.append(_para(text_parts("Không có việc chờ bạn.")))
    return {
        "header": (header, header_lines),
        "done": kpi(f"{s['done']} / {s['total']}", "Execution tasks DONE"),
        "phase": kpi(f"{s['phaseDone']} / {s['phaseTotal']}", f"{s['phaseId']} execution · {pct}% DONE"),
        "reviewed": kpi(f"{s['reviewed']} / {s['total']}", "AI reviewed / human verified"),
        "gates": kpi(f"{s['gatesGreen']} / {s['gates']}", "GREEN phase gates"),
        "owner": (text_parts(f"VIỆC CỦA BẠN — {s['attention']} mục cần nhập / duyệt thủ công", bold=True),
                  children),
        "product": (text_parts("THE PRODUCT", bold=True), [_para(text_parts(s["thesis"]))]),
    }


TEXT_LINES = ("paragraph", "bulleted_list_item", "numbered_list_item")


def _template_line(block: dict) -> bool:
    """A plain line of the template's sample text, which push takes over: text
    only, no links or mentions, nothing nested. Views, pages, link blocks,
    navigation lines and anything else are not push's and stay as they are."""
    kind = block.get("type")
    if kind not in TEXT_LINES or block.get("has_children"):
        return False
    parts = (block.get(kind) or {}).get("rich_text") or []
    return all(p.get("type", "text") == "text" and not (p.get("text") or {}).get("link") and not p.get("href")
               for p in parts)


def write_hub(client: Client, config: dict, content: dict) -> None:
    """Fill each callout in place. Push rewrites only the child lines it wrote
    before (their ids are kept in notion.json), or, the first time, the
    template's plain sample lines; it updates them where they are, inserts any
    extra line after them, and leaves every other child, with its id and
    nesting, alone (ADR-090)."""
    blocks = config.get("hubBlocks") or {}
    written = config.setdefault("hubGenerated", {})
    for name, (parts, lines) in content.items():
        block = blocks.get(name)
        if not block:
            continue
        client.update_block(block, {"callout": {"rich_text": parts}})
        children = client.children(block)
        if name in written:
            mine = set(written[name])
            old = [c for c in children if c["id"] in mine]
        else:
            old = [c for c in children if _template_line(c)]
        known = {c["id"] for c in children}
        ids: list[str] = []
        pending: list[dict] = []
        after: str | None = None

        def flush() -> None:
            nonlocal after
            if not pending:
                return
            made = client.append(block, list(pending), after=after).get("results") or []
            new = [b["id"] for b in made if b.get("id") and b["id"] not in known]
            known.update(new)
            ids.extend(new)
            after = new[-1] if new else after
            pending.clear()

        for index in range(max(len(old), len(lines))):
            have = old[index] if index < len(old) else None
            want = lines[index] if index < len(lines) else None
            if have and want and have["type"] == want["type"]:
                flush()
                kind = want["type"]
                client.update_block(have["id"], {kind: {"rich_text": want[kind]["rich_text"]}})
                ids.append(have["id"])
                after = have["id"]
                continue
            if have:
                client.delete_block(have["id"])
            if want:
                pending.append(want)
        flush()
        written[name] = ids


# --------------------------------------------------------------------------
# Push: compiled project → Notion
# --------------------------------------------------------------------------


@dataclass
class PushResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0
    hub: bool = False

    def line(self) -> str:
        return (f"push created={self.created} updated={self.updated} unchanged={self.unchanged} "
                f"archived={self.archived} hub={'updated' if self.hub else 'unchanged'}")


def _existing(client: Client, source: str, key: str | None) -> dict[str, dict]:
    """Rows of one database by the stable ID in their title; this project's only
    when a key is given (the database carries a Project Key)."""
    query = {"property": "Project Key", "rich_text": {"equals": key}} if key else None
    found: dict[str, dict] = {}
    for row in client.query(source, query):
        pid = plain(row["properties"].get(TITLE)).strip()
        if pid and pid not in found:
            found[pid] = row
    return found


def _norm(value: dict | None) -> Any:
    """A property value compared by meaning, whether push wrote it or Notion
    returned it; empty is None."""
    if not value:
        return None
    for kind in ("title", "rich_text"):
        if kind in value:
            text = "".join(p["plain_text"] if "plain_text" in p else (p.get("text") or {}).get("content", "")
                           for p in value[kind] or [])
            return text or None
    if "select" in value:
        return (value["select"] or {}).get("name") or None
    if "checkbox" in value:
        return bool(value["checkbox"])
    if "number" in value:
        return value["number"]
    if "url" in value:
        return value["url"] or None
    if "date" in value:
        return (value["date"] or {}).get("start") or None
    if "relation" in value:
        return sorted(bare(r.get("id")) for r in value["relation"] or []) or None
    return None


def _same(want: dict, have: dict | None) -> bool:
    a, b = _norm(want), _norm(have)
    if "relation" in want and (have or {}).get("has_more"):
        # Notion returns at most 25 related ids on a page; the rest are not shown.
        return set(b or []) <= set(a or [])
    return a == b


def drifted(row: dict, props: dict) -> list[str]:
    """The properties push owns whose value in Notion is not the one it wants."""
    have = row.get("properties") or {}
    return [name for name, value in props.items() if not _same(value, have.get(name))]


def _upsert(client: Client, source: str, row: dict | None, props: dict, rev: str, result: PushResult,
            extra: dict | None = None) -> None:
    """Write a row when its revision moved or a value push owns was changed in
    Notion; `extra` (a timestamp) rides along and is not compared."""
    if (row is not None and plain(row["properties"].get("Source Revision")) == rev
            and not drifted(row, props)):
        result.unchanged += 1
        return
    body = {**props, **(extra or {}), "Source Revision": rich(rev)}
    if row is None:
        client.create_page({"type": "data_source_id", "data_source_id": source}, body)
        result.created += 1
    else:
        client.update_page(row["id"], body)
        result.updated += 1


def repo_url(root: Path) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"(?:git@|https://)([^:/]+)[:/](.+?)(?:\.git)?$", done.stdout.strip())
    return f"https://{match.group(1)}/{match.group(2)}" if match else None


def push(client: Client, root: Path, config: dict | None = None,
         project: dict | None = None) -> PushResult:
    from . import compile as compiler

    config = config or load_config(root)
    project = project or compiler.as_json(compiler.load(root))
    key = config["projectKey"]
    dbs = config["databases"]
    result = PushResult()
    s = summary(project, key)

    wanted = records(root, project)
    present = {(r.kind, r.pid) for r in wanted}
    rows: dict[str, dict[str, dict]] = {kind: _existing(client, dbs[kind]["data_source_id"], key)
                                        for kind in MIRRORED}
    pages: dict[tuple[str, str], str] = {(kind, pid): row["id"] for kind, found in rows.items()
                                         for pid, row in found.items()}

    # A new copy shows its numbers first; a large project's rows take minutes.
    if config.get("hubBlocks") and "hubRevision" not in config:
        result.hub = _refresh_hub(client, root, config, s, pages, present)

    # Pass 1: every record has a page, so relations can point at it.
    fresh: set[tuple[str, str]] = set()
    for record in wanted:
        if (record.kind, record.pid) not in pages:
            initial = {TITLE: title(record.pid), "Project Key": rich(key)}
            if record.kind == "assumptions":
                initial.update({"Decision": select("PENDING"), "Delivery State": select("NOT_SUBMITTED")})
            created = client.create_page({"type": "data_source_id",
                                          "data_source_id": dbs[record.kind]["data_source_id"]}, initial)
            pages[(record.kind, record.pid)] = created["id"]
            rows[record.kind][record.pid] = created
            fresh.add((record.kind, record.pid))
            result.created += 1

    # Pass 2: full properties, written when the revision moved or a value push
    # owns was changed in Notion. A submitted assumption is the owner's until
    # pull has handled it: its revision and last editor are what pull checks.
    revisions: list[str] = []
    current: dict[tuple[str, str], str] = {}
    for record in wanted:
        props = dict(record.props)
        props[TITLE] = title(record.pid)
        for name, targets in record.links.items():
            props[name] = relation(sorted({pages[t] for t in targets if t in pages}))
        rev = revision_of(record)
        current[(record.kind, record.pid)] = rev
        revisions.append(f"{record.kind}:{record.pid}:{rev}")
        ident = (record.kind, record.pid)
        row = rows[record.kind][record.pid]
        if ident not in fresh and (held(record.kind, row) or (
                plain(row.get("properties", {}).get("Source Revision")) == rev and not drifted(row, props))):
            result.unchanged += 1
            continue
        client.update_page(pages[ident], {**props, "Source Revision": rich(rev)})
        if ident not in fresh:
            result.updated += 1

    # Rows whose record is gone: blockers resolve, everything else goes to the trash.
    for kind, found in rows.items():
        for pid, row in found.items():
            if (kind, pid) in present or held(kind, row):
                continue
            if kind == "blockers":
                if plain(row["properties"].get("Status")) != "RESOLVED":
                    client.update_page(row["id"], {"Status": select("RESOLVED"),
                                                   "Source Revision": rich("resolved")})
                    result.archived += 1
            else:
                client.update_page(row["id"], archived=True)
                result.archived += 1

    # An instruction not yet submitted carries its target's revision, so pull can
    # tell an approval of what the owner saw from one of something since changed.
    by_page = {bare(page): pid for (kind, pid), page in pages.items() if kind == "tasks"}
    for row in client.query(dbs["outbox"]["data_source_id"]):
        props = row["properties"]
        row_key = plain(props.get("Project Key")).strip()
        if (row_key and row_key != key) or plain(props.get("Submit")) \
                or plain(props.get("Delivery State")) not in OPEN_OUTBOX:
            continue
        target = _target(props, by_page)
        rev = current.get((_kind_of(target), target))
        if rev and plain(props.get("Source Revision")) != rev:
            client.update_page(row["id"], {"Source Revision": rich(rev)})
            result.updated += 1

    stamp = now()

    # One Operations & Sync row says what the mirror last carried.
    operations = dbs["operations"]["data_source_id"]
    watching = config.get("watch") or {}
    state = (f"READY · push every {watching['pushMinutes']} min, pull every {watching['pullMinutes']} min"
             if watching else "READY · watchers not installed (`praukron notion watch install`)")
    _upsert(client, operations, _existing(client, operations, None).get(SYNC_ROW), {
        TITLE: title(SYNC_ROW), "Item Title": rich(f"Praukron chronicle → Notion ({len(wanted)} records)"),
        "Kind": select("SYNC"), "Record ID": rich(SYNC_ROW), "State": rich(state), "Needs Owner": checkbox(False),
    }, revision({"records": sorted(revisions), "state": state}), result, {"Last Sync": date(stamp)})

    # The project's row: in the copy's own Project Portfolio and on the shared board.
    board = {
        TITLE: title(s["name"]), "Project Name": rich(s["name"]), "Project Key": rich(key),
        "Product Thesis": rich(s["thesis"]),
        "Current Phase": rich(f"{s['phaseId']} — {s['phaseName']} · {s['phaseStatus']}"),
        "Repo": url(repo_url(root)), "Project Page": url(page_url(config["instance"], s["name"])),
        "Execution Done": number(s["done"]), "Execution Total": number(s["total"]),
        "Verified": number(s["reviewed"]), "Open Assumptions": number(len(s["assumptions"])),
        "Red Gates": number(s["gatesRed"]), "Health": select(s["health"]),
    }
    for target in (dbs.get("portfolio"), config.get("shared")):
        if target:
            source = target["data_source_id"]
            _upsert(client, source, _existing(client, source, key).get(s["name"]), board, revision(board),
                    result, {"Last Synced": date(stamp)})

    if _refresh_hub(client, root, config, s, pages, present):
        result.hub = True
    return result


def _refresh_hub(client: Client, root: Path, config: dict, s: dict, pages: dict[tuple[str, str], str],
                 present: set[tuple[str, str]]) -> bool:
    """The hub callouts, rewritten only when what they show changed."""
    hub_rev = revision({"summary": s, "pages": sorted(f"{k}:{p}:{v}" for (k, p), v in pages.items()
                                                       if k in ("assumptions", "tasks") and (k, p) in present)})
    if not config.get("hubBlocks") or config.get("hubRevision") == hub_rev:
        return False
    try:
        write_hub(client, config, hub_content(s, config, pages))
    except NotionError as error:
        if error.status != 404:
            raise
        before = config.get("hubBlocks") or {}
        config["hubBlocks"] = hub_blocks_found(client, config["instance"]) or add_hub_skeleton(
            client, config["instance"])
        # A callout found again under a new id holds none of push's lines yet.
        config["hubGenerated"] = {name: ids for name, ids in (config.get("hubGenerated") or {}).items()
                                  if before.get(name) == config["hubBlocks"].get(name)}
        write_hub(client, config, hub_content(s, config, pages))
    config["hubRevision"] = hub_rev
    save_config(root, config)
    return True


# --------------------------------------------------------------------------
# What pull and push agree on about the owner's rows
# --------------------------------------------------------------------------

# Outbox rows the owner may still submit; push keeps their base revision current.
OPEN_OUTBOX = {"", "DRAFT", "NOT_SUBMITTED", "CONFLICT", "REJECTED"}


def held(kind: str, row: dict) -> bool:
    """An assumption the owner submitted that pull has not finished: push neither
    rewrites nor trashes it, so its base revision and submitter survive."""
    return kind == "assumptions" and bool(plain((row.get("properties") or {}).get("Submit")))


def _kind_of(target: str) -> str:
    return "assumptions" if target.startswith("A-") else "tasks"


def _target(props: dict, task_pages: dict[str, str]) -> str:
    """An outbox row's target: its linked task (`task_pages` maps bare page ids to
    task ids), else the id in Target ID."""
    linked = [task_pages[bare(i)] for i in plain(props.get("Target Task Link")).split(",")
              if bare(i) in task_pages]
    return linked[0] if linked else plain(props.get("Target ID")).strip()


# --------------------------------------------------------------------------
# Pull: owner submissions in Notion → RESPONSES.md (worker contract v1.0)
# --------------------------------------------------------------------------

REQUESTS_FILE = "notion-requests.json"
IN_FLIGHT = {"QUEUED", "LOCAL_RECORDED", "COMPILED"}


@dataclass
class PullResult:
    applied: list[str] = field(default_factory=list)
    rejected: int = 0
    conflicts: int = 0
    skipped: int = 0

    def line(self) -> str:
        return (f"pull applied={len(self.applied)} rejected={self.rejected} "
                f"conflicts={self.conflicts} skipped={self.skipped}")


def _mark(client: Client, row_id: str, state: str, request: str, evidence: str | None = None,
          untick: bool = False) -> None:
    """Move a submitted row along; `evidence` is the receipt text. A row the owner
    has since deleted takes no receipt."""
    props: dict = {"Delivery State": select(state), "Request ID": rich(request)}
    if evidence is not None:
        props["Apply Evidence"] = rich(evidence)
    if untick:
        props["Submit"] = checkbox(False)
    try:
        client.update_page(row_id, props)
    except NotionError as error:
        if error.status != 404:
            raise


class Requests:
    """The worker's own journal of submissions, `.praukron/notion-requests.json`
    (local, ignored by Git, written whole each time).

    A submission is keyed by its row and a hash of what was submitted, and its
    submitter and payload are kept here before the worker writes anything to
    Notion, where every write makes the integration the row's last editor. An
    interrupted request resumes from here; a new edit of the same row is a new
    key, so a refused row can be corrected and sent again, and an applied one can
    carry a second decision."""

    def __init__(self, root: Path) -> None:
        self.path = root / layout.HOME_DIR / REQUESTS_FILE
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except ValueError:
            raise NotionError(0, "bad-request-journal", f"{self.path} is not valid JSON; move it aside and "
                              "pull again (RESPONSES.md still keeps every recorded request)") from None
        self.entries: dict[str, dict] = data.get("requests") or {}

    def save(self, entry: dict) -> None:
        self.entries[entry["key"]] = entry
        write_json(self.path, {"version": 1, "requests": self.entries})


def submission(kind: str, row: dict, task_pages: dict[str, str], mirrored: Callable[[str], str]) -> dict:
    """What the owner submitted, read once, before the worker writes to the row."""
    props = row["properties"]
    if kind == "outbox":
        target = _target(props, task_pages)
        payload = {"target": target, "intent": plain(props.get("Intent")) or "GUIDE",
                   "priority": plain(props.get("Priority")), "title": plain(props.get("Instruction Title")),
                   "text": plain(props.get("Instruction Text"))}
        # The revision push stamped on the row, else the one its target's row shows.
        base = plain(props.get("Source Revision")) or mirrored(target)
    else:
        target = plain(props.get("Assumption ID")).strip() or plain(props.get(TITLE)).strip()
        payload = {"target": target, "decision": plain(props.get("Decision")),
                   "feedback": plain(props.get("Feedback"))}
        base = plain(props.get("Source Revision"))
    digest = revision({"kind": kind, "row": bare(row["id"]), "payload": payload, "base": base})
    return {
        "key": f"{row['id']}@{digest[:12]}", "kind": kind, "row": row["id"], "target": target,
        "payload": payload, "base": base, "editor": bare((row.get("last_edited_by") or {}).get("id")),
        "edited": row.get("last_edited_time", ""), "seen": now(), "state": "NEW",
    }


class Ledger:
    """One Action Ledger row per request, found again by the request key in its
    Source Page ID."""

    def __init__(self, client: Client, config: dict) -> None:
        self.client = client
        self.key = config["projectKey"]
        self.source = config["databases"]["ledger"]["data_source_id"]
        self.rows: dict[str, str] = {}
        for row in client.query(self.source, {"property": "Project Key", "rich_text": {"equals": self.key}}):
            request = plain(row["properties"].get("Source Page ID"))
            if request:
                self.rows.setdefault(request, row["id"])

    def record(self, entry: dict, status: str, label: str, task_page: str | None, applied: str = "",
               error: str = "") -> str:
        request, action = entry["key"], entry["action"]
        props = {
            TITLE: title(label), "Action Title": rich(f"{action} · {applied or status}"),
            "Requested Action": rich(action), "Source Page ID": rich(request),
            "Source Database": select(entry["source"]), "Status": select(status),
            "Base Revision": rich(entry["base"]), "Target ID": rich(entry["target"]),
            "Error": rich(error), "Project Key": rich(self.key),
            "Source Assumption" if entry["kind"] == "assumptions" else "Source Instruction":
                relation([entry["row"]]),
        }
        if task_page:
            props["Target Task Link"] = relation([task_page])
        if request in self.rows:
            self.client.update_page(self.rows[request], props)
        else:
            self.rows[request] = self.client.create_page(
                {"type": "data_source_id", "data_source_id": self.source}, props)["id"]
        return page_url(self.rows[request], label)


def _already(project, request: str) -> str | None:
    """The response a request was recorded as, from its `Via: … request <key>`."""
    for response in project.responses:
        found = re.search(r"\brequest (\S+)", response.via or "")
        if found and found.group(1) == request:
            return response.id
    return None


def _decide(entry: dict, login: str | None, current: dict[tuple[str, str], str]) -> dict:
    """A refusal ({status, error}) or the response to append ({action, text,
    verify, via}). Text is the owner's, verbatim; stripped copies only test for
    emptiness, and the worker's notes go in the Via line."""
    target, payload, base = entry["target"], entry["payload"], entry["base"]
    if not login:
        return {"status": "REJECTED",
                "error": f"Submitted by Notion user {entry['editor'] or 'unknown'}, who is not listed in an "
                         "ADR's Notion owners (ADR-088). Nothing was written."}
    via = f"notion {entry['editor']} request {entry['key']}"
    stale = (f"{target} changed in the chronicle after this row was shown; the row is now current. "
             "Read it again and tick Submit to resend.")
    if entry["kind"] == "assumptions":
        decision, feedback = payload["decision"], payload["feedback"]
        if base != current.get(("assumptions", target)):
            return {"status": "CONFLICT", "error": stale}
        if decision in ("REVISE", "REJECT") and not feedback.strip():
            return {"status": "REJECTED", "error": f"{decision} needs Feedback saying what to change; write it, "
                                                   "then Submit."}
        if decision in ("CONFIRM", "REVISE", "REJECT"):
            return {"action": decision, "text": feedback if feedback.strip() else
                    "CONFIRM (from Notion, no feedback)", "verify": False, "via": via}
        if decision == "WITHDRAW":
            return {"action": "GUIDE", "text": feedback if feedback.strip() else "WITHDRAW", "verify": False,
                    "via": f"{via} · decision WITHDRAW"}
        if feedback.strip():
            return {"action": "GUIDE", "text": feedback, "verify": False, "via": via}
        return {"status": "REJECTED", "error": "Choose a Decision or write Feedback, then Submit."}
    intent, body = payload["intent"], payload["text"]
    if not target or target[:2] not in ("T-", "A-"):
        return {"status": "REJECTED",
                "error": "Link a task (Target Task Link) or name a T-/A- id in Target ID, then Submit."}
    if (_kind_of(target), target) not in current:
        return {"status": "REJECTED", "error": f"{target} is not a task or an assumption in the chronicle."}
    if not body.strip():
        return {"status": "REJECTED", "error": "Write the Instruction Text, then Submit."}
    if base != current[(_kind_of(target), target)]:
        return {"status": "CONFLICT", "error": stale}
    text = "\n\n".join(part for part in (payload["title"], body) if part.strip())
    note = f" · intent {intent}" + (f" · priority {payload['priority']}" if payload["priority"] else "")
    verify = intent in ("VERIFY", "APPROVE")
    return {"action": "VERIFY" if verify else "GUIDE", "text": text, "verify": verify, "via": via + note}


def pull(client: Client, root: Path, config: dict | None = None,
         append: Callable | None = None) -> PullResult:
    from . import compile as compiler, respond
    from .respond import Item, RespondError

    config = config or load_config(root)
    append = append or respond.append
    key = config["projectKey"]
    dbs = config["databases"]
    result = PullResult()
    journal = Requests(root)

    def load() -> tuple[Any, dict[tuple[str, str], str]]:
        project = compiler.load(root)
        return project, {(r.kind, r.pid): revision_of(r) for r in records(root, compiler.as_json(project))
                         if r.kind in ("assumptions", "tasks")}

    project, current = load()
    owners = project.notion_owners
    tasks = _existing(client, dbs["tasks"]["data_source_id"], key)
    task_pages = {bare(row["id"]): pid for pid, row in tasks.items()}
    shown: dict[str, dict[str, dict]] = {"tasks": tasks}

    def mirrored(target: str) -> str:
        kind = _kind_of(target)
        if kind not in shown:
            shown[kind] = _existing(client, dbs[kind]["data_source_id"], key)
        row = shown[kind].get(target)
        return plain(row["properties"].get("Source Revision")) if row else ""

    # 1. Read every submitted row and keep what is new before writing anything.
    submitted = {"property": "Submit", "checkbox": {"equals": True}}
    rows: list[tuple[str, dict]] = [
        ("assumptions", row) for row in client.query(
            dbs["assumptions"]["data_source_id"],
            {"and": [submitted, {"property": "Project Key", "rich_text": {"equals": key}}]})]
    rows += [("outbox", row) for row in client.query(dbs["outbox"]["data_source_id"], submitted)]
    latest: dict[str, str] = {}
    again: list[dict] = []
    for kind, row in rows:
        row_key = plain(row["properties"].get("Project Key")).strip()
        if kind == "outbox" and row_key and row_key != key:   # a copy serves one project; skip strays
            result.skipped += 1
            continue
        entry = submission(kind, row, task_pages, mirrored)
        latest[row["id"]] = entry["key"]
        known = journal.entries.get(entry["key"])
        if known:
            if known["state"] == "DONE":
                again.append(known)
            continue
        # The journal lost, a request in flight still names itself on the row.
        named = plain(row["properties"].get("Request ID")).strip()
        state = plain(row["properties"].get("Delivery State"))
        if (state in IN_FLIGHT and named.startswith(f"{row['id']}@") and named not in journal.entries
                and _already(project, named)):
            entry["key"] = latest[row["id"]] = named
        journal.save(entry)

    # 2. Carry every unfinished request to its receipt, oldest edit first.
    ledger = Ledger(client, config)
    for entry in sorted((e for e in journal.entries.values() if e["state"] != "DONE"),
                        key=lambda e: (e.get("edited", ""), e.get("seen", ""))):
        request, row_id = entry["key"], entry["row"]
        untick = latest.get(row_id) == request   # a newer submission of the row stays ticked
        payload = entry["payload"]
        entry.setdefault("source", "ASSUMPTION" if entry["kind"] == "assumptions" and payload["decision"]
                         not in ("", "PENDING") else "FEEDBACK" if entry["kind"] == "assumptions" else
                         "APPROVAL" if payload["intent"] in ("VERIFY", "APPROVE") else "INSTRUCTION")
        entry.setdefault("action", f"{entry['target'] or '?'} " + (
            payload.get("decision") or "FEEDBACK" if entry["kind"] == "assumptions" else payload["intent"]))
        task_page = tasks[entry["target"]]["id"] if entry["target"] in tasks else None
        short = f"REQ-{request.rsplit('@', 1)[-1][:8]}"

        if entry["state"] == "NEW":
            done = _already(project, request)
            if done:
                entry.update(state="LOCAL_RECORDED", response=done)
            else:
                decided = _decide(entry, owners.get(entry["editor"]), current)
                if "status" not in decided:
                    _mark(client, row_id, "QUEUED", request)
                    try:
                        ids = append(root, [Item(entry["target"], decided["action"], decided["text"])],
                                     via=decided["via"], verify=decided["verify"], by=owners[entry["editor"]])
                    except RespondError as error:
                        decided = {"status": "REJECTED", "error": str(error)}
                    else:
                        entry.update(state="LOCAL_RECORDED", response=ids[0])
                        project, current = load()
                if "status" in decided:
                    entry.update(state="DECIDED", status=decided["status"], error=decided["error"])
            journal.save(entry)

        if entry["state"] == "LOCAL_RECORDED":
            response = entry["response"]
            _mark(client, row_id, "LOCAL_RECORDED", request)
            if entry["kind"] == "outbox":
                _mark(client, row_id, "COMPILED", request)
            link = ledger.record(entry, "APPLIED", response, task_page, applied=response)
            evidence = f"{response} in RESPONSES.md; views compiled · ledger {link}"
            _mark(client, row_id, "APPLIED", request, evidence, untick=untick)
            entry.update(state="DONE", status="APPLIED", evidence=evidence)
            journal.save(entry)
            result.applied.append(response)
        elif entry["state"] == "DECIDED":
            link = ledger.record(entry, entry["status"], short, task_page, error=entry["error"])
            evidence = f"{entry['error']} Ledger: {link}"
            _mark(client, row_id, entry["status"], request, evidence, untick=untick)
            entry.update(state="DONE", evidence=evidence)
            journal.save(entry)
            if entry["status"] == "CONFLICT":
                result.conflicts += 1
            else:
                result.rejected += 1

    # 3. A finished request ticked again unchanged gets its receipt again, once.
    for entry in again:
        _mark(client, entry["row"], entry["status"], entry["key"], entry["evidence"], untick=True)
        result.skipped += 1
    return result


# --------------------------------------------------------------------------
# Watchers
# --------------------------------------------------------------------------

TASK_PREFIX = "Praukron Notion"


def _launcher(root: Path) -> list[str]:
    # pythonw runs without opening a console window every few minutes.
    python = sys.executable
    windowless = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.name == "nt" and os.path.isfile(windowless):
        python = windowless
    return [python, str(root / layout.HOME_DIR / "praukron"), "-C", str(root)]


def schedule_lines(root: Path, key: str, push_minutes: int, pull_minutes: int) -> list[tuple[str, int, str]]:
    base = " ".join(f'"{part}"' for part in _launcher(root))
    return [(f"{TASK_PREFIX} {key} pull", pull_minutes, f"{base} notion pull --quiet"),
            (f"{TASK_PREFIX} {key} push", push_minutes, f"{base} notion push --quiet")]


def watch(root: Path, action: str, push_minutes: int = 5, pull_minutes: int = 2,
          run: Callable[..., Any] | None = None) -> tuple[int, str]:
    run = run or subprocess.run
    config = load_config(root)
    entries = schedule_lines(root, config["projectKey"], push_minutes, pull_minutes)
    if os.name != "nt":
        if action == "install":
            lines = [f"*/{minutes} * * * * {command} >/dev/null 2>&1" for _, minutes, command in entries]
            return 0, "Add these lines with `crontab -e`:\n" + "\n".join(lines)
        if action == "remove":
            return 0, "Remove the two `praukron ... notion` lines with `crontab -e`."
        return 0, "Check `crontab -l` for the two `praukron ... notion` lines."
    out = []
    status = 0
    for name, minutes, command in entries:
        if action == "install":
            args = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes),
                    "/TN", name, "/TR", command]
        elif action == "remove":
            args = ["schtasks", "/Delete", "/F", "/TN", name]
        else:
            args = ["schtasks", "/Query", "/TN", name]
        done = run(args, capture_output=True, text=True)
        ok = done.returncode == 0
        status |= 0 if ok else 1
        out.append(f"{name}: {'ok' if ok else (done.stderr or done.stdout).strip()}")
    if action == "install" and status == 0:
        config["watch"] = {"pushMinutes": push_minutes, "pullMinutes": pull_minutes}
        save_config(root, config)
    return status, "\n".join(out)


__all__ = ["DATABASES", "PORTFOLIO", "PullResult", "PushResult", "Locked", "lock", "log", "provision",
           "pull", "push", "records", "setup", "shared_board", "summary", "watch"]
