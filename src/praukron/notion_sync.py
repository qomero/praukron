"""Notion management: provision, push, pull and watch (ADR-087, ADR-088, ADR-089).

The chronicle is the only authority. Each project mirrors into its own
instance of the owner's PPMP template (Praukron Project Management Protocol):
`push` upserts the compiled project into the instance's nine databases, keyed
by the stable ID in each row's title; `pull` turns rows the owner submitted in
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
# Schema: the PPMP template's nine databases, one set per project instance.
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


TITLE = "ID"   # every PPMP database titles a row by its stable ID
SYNC_FIELDS = {"Project Key": _text(), "Source Revision": _text()}
DELIVERY = ("NOT_SUBMITTED", "QUEUED", "LOCAL_RECORDED", "APPLIED", "CONFLICT", "REJECTED")
OUTBOX_DELIVERY = ("DRAFT", "QUEUED", "LOCAL_RECORDED", "COMPILED", "APPLIED", "CONFLICT", "REJECTED")

# Each kind: the PPMP data source title, its properties, and the relations added
# once every database exists: (property, target kind, dual property or None).
# `extra` names the properties PPMP lacks that the full-text mirror adds (ADR-089).
DATABASES: dict[str, dict[str, Any]] = {
    "tasks": {
        "title": "PPMP · Task Registry", "icon": "✅",
        "properties": {
            TITLE: _title(), "Task Title": _text(),
            "Status": _select("TODO", "WIP", "DONE", "BLOCKED"),
            "Work Bucket": _select("READY", "IN_PROGRESS", "UPCOMING", "WAITING_HUMAN",
                                   "WAITING_DEPENDENCIES", "BLOCKED_FAILURE", "COMPLETED"),
            "Review": _select("UNTESTED", "SYNTHETIC", "AI_REVIEWED", "HUMAN_VERIFIED"),
            "Authority": _select("owner", "worker"), "Domain": _select("execution", "operations"),
            "Phase": _text(), "Module": _text(), "Owner": _text(),
            "Critical Path": _checkbox(), "Downstream": _number(),
            "Acceptance IDs": _text(), "Depends On IDs": _text(), "Evidence URL": _url(),
            "Evidence": _text(), **SYNC_FIELDS,
        },
        "relations": [("Depends On", "tasks", "Blocks Tasks"), ("Phase Link", "phases", None)],
        "extra": {"Evidence"},
    },
    "phases": {
        "title": "PPMP · Phases & Gates", "icon": "🚦",
        "properties": {TITLE: _title(), "Label": _text(), "Kind": _select("PHASE", "GATE", "MILESTONE"),
                       "State": _select("PLANNED", "ACTIVE", "EXIT_PENDING", "COMPLETE", "RED", "GREEN"),
                       "Outcome": _text(), "Exit Authority": _text(), "Blocked By IDs": _text(),
                       "Evidence URL": _url(), **SYNC_FIELDS},
        "relations": [("Exit Task Link", "tasks", None)],
    },
    "acceptance": {
        "title": "PPMP · Acceptance & Evidence", "icon": "🧪",
        "properties": {TITLE: _title(), "Criterion": _text(),
                       "Method": _select("TEST", "RUNTIME", "INSPECTION", "MANUAL"),
                       "Result": _select("NOT_RUN", "PASS", "FAIL"), "Task ID": _text(),
                       "Verifier": _text(), "Evidence URL": _url(), "Evidence": _text(), **SYNC_FIELDS},
        "relations": [("Task Link", "tasks", "Acceptance Records")],
        "extra": {"Evidence"},
    },
    "blockers": {
        "title": "PPMP · Blockers & Dependencies", "icon": "🚧",
        "properties": {TITLE: _title(), "Blocker Title": _text(),
                       "Kind": _select("HUMAN", "DEPENDENCY", "FAILURE", "PERMISSION", "OTHER"),
                       "Root Task ID": _text(), "Owner Action": _text(), "Downstream Count": _number(),
                       "Status": _select("OPEN", "RESOLVED"), "Evidence URL": _url(), **SYNC_FIELDS},
        "relations": [("Root Task Link", "tasks", None), ("Affected Tasks", "tasks", None)],
    },
    "assumptions": {
        "title": "PPMP · Assumption Review Queue", "icon": "✏️",
        "properties": {
            TITLE: _title(), "Statement": _text(), "Basis": _text(), "Related Task IDs": _text(),
            "Impact": _select("LOW", "MEDIUM", "HIGH"),
            "Source State": _select("OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN"),
            "Decision": _select("PENDING", "CONFIRM", "REVISE", "REJECT", "WITHDRAW"),
            "Feedback": _text(), "Submit": _checkbox(), "Delivery State": _select(*DELIVERY),
            "Apply Evidence": _url(), **SYNC_FIELDS,
        },
        "relations": [("Related Task Links", "tasks", None)],
    },
    "outbox": {
        "title": "PPMP · Agent Instructions Outbox", "icon": "✉️",
        "properties": {
            TITLE: _title(), "Instruction Text": _text(),
            "Intent": _select("IMPLEMENT", "REVIEW", "REPLAN", "FIX", "ANSWER", "PAUSE", "OTHER",
                              "GUIDE", "VERIFY", "APPROVE"),
            "Priority": _select("NORMAL", "HIGH", "URGENT"),
            "Target Type": _select("PROJECT", "PHASE", "MODULE", "TASK", "ASSUMPTION", "ADR"),
            "Target ID": _text(), "Submit": _checkbox(), "Delivery State": _select(*OUTBOX_DELIVERY),
            "Apply Evidence": _url(), **SYNC_FIELDS,
        },
        "relations": [("Target Task Link", "tasks", None)],
    },
    "adrs": {
        "title": "PPMP · ADR & Technical Debt", "icon": "📜",
        "properties": {TITLE: _title(), "Kind": _select("ADR", "TECHNICAL_DEBT"),
                       "State": _select("PROPOSED", "ACCEPTED", "SUPERSEDED", "OPEN", "SCHEDULED",
                                        "RESOLVED", "INVALIDATED"),
                       "Decision Or Issue": _text(), "Rationale": _text(), "Supersedes": _text(),
                       "Affected Task IDs": _text(), "Evidence URL": _url(), **SYNC_FIELDS},
        "relations": [("Affected Tasks", "tasks", None)],
    },
    "ledger": {
        "title": "PPMP · Action Ledger", "icon": "🧾",
        "properties": {TITLE: _title(), "Request ID": _text(), "Requested Action": _text(),
                       "Source": _select("ASSUMPTION", "INSTRUCTION", "APPROVAL", "FEEDBACK"),
                       "Status": _select("PENDING", "ACKNOWLEDGED", "LOCAL_RECORDED", "COMPILED",
                                         "APPLIED", "CONFLICT", "REJECTED"),
                       "Base Revision": _text(), "Applied Revision": _text(), "Target ID": _text(),
                       "Error": _text(), "Receipt URL": _url(), "Project Key": _text()},
        "relations": [("Target Task Link", "tasks", None), ("Source Assumption", "assumptions", None),
                      ("Source Instruction", "outbox", None)],
    },
    "operations": {
        "title": "PPMP · Operations & Sync", "icon": "🔄",
        "properties": {TITLE: _title(), "Item": _text(),
                       "Kind": _select("SECRETS_READINESS", "ENVIRONMENT", "SYNC", "DEPLOYMENT", "OTHER"),
                       "Status": _select("READY", "BLOCKED", "DEGRADED", "UNKNOWN"),
                       "Last Compile": {"date": {}}, "Owner Guidance": _text(),
                       "Required Env Names": _text(), "Source Hash": _text(), "Audit Evidence": _url(),
                       "Approved Provider": _text(), "Project Key": _text()},
        "relations": [("Related Task Link", "tasks", None)],
    },
}
ORDER = ["tasks", "phases", "acceptance", "blockers", "assumptions", "outbox", "adrs", "ledger", "operations"]
# Kinds push owns row by row; a row whose record is gone is resolved or trashed.
MIRRORED = ("tasks", "phases", "acceptance", "blockers", "assumptions", "adrs")
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


def save_config(root: Path, config: dict) -> None:
    path = config_path(root)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def rich(text: Any) -> dict:
    text = "" if text is None else str(text)
    parts = [text[i:i + TEXT_LIMIT] for i in range(0, len(text), TEXT_LIMIT)][:TEXT_PARTS]
    return {"rich_text": [{"type": "text", "text": {"content": p}} for p in parts]}


def title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": str(text)[:TEXT_LIMIT]}}]}


def select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def number(value: float | int | None) -> dict:
    return {"number": value}


def checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


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
# Setup: bind the project to its PPMP instance
# --------------------------------------------------------------------------


CONTAINERS = {"child_page", "column_list", "column", "toggle", "synced_block", "callout"}


def canonical(name: str) -> str:
    """A data source title compared without the `PPMP · ` prefix or case."""
    name = re.sub(r"^\s*PPMP\s*·\s*", "", name or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip().lower()


def _source_title(source: dict) -> str:
    return "".join(part.get("plain_text", "") for part in source.get("title") or [])


def find_databases(client: Client, instance: str, depth: int = 4) -> dict[str, dict[str, str]]:
    """The PPMP databases anywhere below the instance page, by kind.

    Duplicating the template gives every database a new id; they are found by
    their data source titles, so a renamed page or a moved database still binds.
    """
    wanted = {canonical(spec["title"]): kind for kind, spec in DATABASES.items()}
    found: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    queue = [(instance, 0)]
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
    return found


def _data_source(client: Client, database_id: str) -> str:
    sources = client.database(database_id).get("data_sources") or []
    if not sources:
        raise NotionError(0, "no-data-source", f"database {database_id} has no data source")
    return sources[0]["id"]


def provision(client: Client, instance: str) -> dict[str, dict[str, str]]:
    """Bind the PPMP databases below `instance`, create the ones it lacks, and add
    missing properties and relations. Nothing existing is changed or removed, and
    running it again changes nothing."""
    databases = find_databases(client, instance)
    for kind in ORDER:
        spec = DATABASES[kind]
        if kind in databases:
            source = databases[kind]["data_source_id"]
            have = client.data_source(source).get("properties", {})
            missing = {name: value for name, value in spec["properties"].items()
                       if name not in have and "title" not in value}
            if missing:
                client.update_data_source(source, missing)
        else:
            created = client.create_database(instance, spec["title"], spec["properties"], spec["icon"])
            database_id = created["id"]
            source = (created.get("data_sources") or [{}])[0].get("id") or _data_source(client, database_id)
            databases[kind] = {"database_id": database_id, "data_source_id": source}
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
                          "(page menu > Duplicate), then run setup on the copy (ADR-089).")
    me = client.me()
    databases = provision(client, instance)
    if "TEMPLATE" in current:
        client.update_page(instance, {"title": title(f"PPMP · {name} · {key}")})
    config = {
        "version": 2,
        "template": "PPMP",
        "workspace": (me.get("bot") or {}).get("workspace_name"),
        "instance": instance,
        "projectKey": key,
        "projectName": name,
        "databases": databases,
    }
    previous = {}
    if config_path(root).is_file():
        previous = json.loads(config_path(root).read_text(encoding="utf-8"))
    hub = previous.get("hub") if previous.get("instance") == instance else None
    if hub:
        try:
            if client.page(hub).get("in_trash"):
                hub = None
        except NotionError:
            hub = None
    if not hub:
        hub = client.create_page({"type": "page_id", "page_id": instance},
                                 {"title": title(f"Live Status · {key}")}, icon="📡")["id"]
    config["hub"] = hub
    save_config(root, config)
    return config


# --------------------------------------------------------------------------
# Push: compiled project → Notion
# --------------------------------------------------------------------------


@dataclass
class Record:
    kind: str
    pid: str              # Praukron ID, unique within the project
    label: str            # the title shown in Notion
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


RESULT = {"PASS": "PASS", "FAIL": "FAIL"}
ADR_STATE = {"PROPOSED": "PROPOSED", "ACCEPTED": "ACCEPTED", "SUPERSEDED": "SUPERSEDED",
             "REJECTED": "INVALIDATED", "DEPRECATED": "SUPERSEDED"}
DEBT_STATE = {"OPEN": "OPEN", "ACCEPTED": "OPEN", "SCHEDULED": "SCHEDULED", "PAID": "RESOLVED",
              "RESOLVED": "RESOLVED", "INVALIDATED": "INVALIDATED", "WONTFIX": "INVALIDATED"}


def _gate_state(status: str | None) -> str:
    status = (status or "").upper()
    return status if status in ("GREEN", "RED") else "PLANNED"


def records(root: Path, project: dict) -> list[Record]:
    out: list[Record] = []
    tasks = project["tasks"]
    critical = set(project.get("criticalPath") or [])
    downstream: dict[str, int] = {}

    def reach(task_id: str, seen: set[str]) -> None:
        for other in by_id.get(task_id, {}).get("blocks", []):
            if other not in seen:
                seen.add(other)
                reach(other, seen)

    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        seen: set[str] = set()
        reach(t["id"], seen)
        downstream[t["id"]] = len(seen)

    for phase in project["phases"]:
        out.append(Record("phases", phase["id"], phase["id"], {
            "Label": rich(phase["name"]), "Kind": select("PHASE"), "State": select(phase["status"]),
            "Outcome": rich("\n".join(filter(None, [phase.get("outcome"), *(phase.get("exit") or [])]))),
            "Exit Authority": rich(phase.get("exitAuthority")),
            "Blocked By IDs": rich(", ".join(phase.get("blockedBy") or [])),
        }, {"Exit Task Link": [("tasks", phase.get("exitAuthority"))]}))
    for gate in project.get("gates") or []:
        out.append(Record("phases", gate["id"], gate["id"], {
            "Label": rich(gate.get("name")), "Kind": select("GATE"), "State": select(_gate_state(gate.get("status"))),
            "Outcome": rich(gate.get("description")),
            "Exit Authority": rich(", ".join(gate.get("verifiedBy") or [])),
            "Blocked By IDs": rich(", ".join(gate.get("blocks") or [])),
        }))
    for milestone in project.get("milestones") or []:
        out.append(Record("phases", milestone["id"], milestone["id"], {
            "Label": rich(milestone.get("text")), "Kind": select("MILESTONE"),
            "State": select("COMPLETE" if milestone.get("reached") else "PLANNED"),
            "Exit Authority": rich(milestone.get("task")),
        }, {"Exit Task Link": [("tasks", milestone.get("task"))]}))
    for decision in project["decisions"]:
        status = (decision.get("status") or "").upper()
        out.append(Record("adrs", decision["id"], decision["id"], {
            "Kind": select("ADR"), "State": select(ADR_STATE.get(status, "PROPOSED")),
            "Decision Or Issue": rich(decision["title"]),
            "Rationale": rich(_adr_body(root, decision.get("source"))),
            "Supersedes": rich(", ".join(decision.get("supersedes") or [])),
            "Affected Task IDs": rich(", ".join(decision.get("affects") or [])),
        }, {"Affected Tasks": [("tasks", a) for a in decision.get("affects") or []]}))
    for debt in project.get("debt") or []:
        affected = [i for i in debt.get("introducedBy") or [] if i.startswith("T-")]
        out.append(Record("adrs", debt["id"], debt["id"], {
            "Kind": select("TECHNICAL_DEBT"),
            "State": select(DEBT_STATE.get((debt.get("status") or "").upper(), "OPEN")),
            "Decision Or Issue": rich(debt.get("title")),
            "Rationale": rich("\n\n".join(filter(None, [debt.get("debt"), debt.get("reason"),
                                                         debt.get("interest"), debt.get("exitCondition")]))),
            "Affected Task IDs": rich(", ".join(debt.get("introducedBy") or [])),
        }, {"Affected Tasks": [("tasks", a) for a in affected]}))
    acceptance = project.get("acceptance") or {}
    for t in tasks:
        owner_held = (t.get("authority") or "") == "owner"
        contract = acceptance.get(t.get("ac") or "") or {}
        criteria = contract.get("criteria") or []
        out.append(Record("tasks", t["id"], t["id"], {
            "Task Title": rich(t["title"]), "Status": select(t["status"]),
            "Work Bucket": select(_bucket(t, owner_held)),
            "Review": select(t.get("validation")),
            "Authority": select("owner" if owner_held else "worker"),
            "Domain": select(t.get("domain")), "Phase": rich(t.get("phase")),
            "Module": rich(t.get("module")), "Owner": rich(t.get("owner")),
            "Critical Path": checkbox(t["id"] in critical),
            "Downstream": number(downstream.get(t["id"], 0)),
            "Acceptance IDs": rich(", ".join(c["id"] for c in criteria)),
            "Depends On IDs": rich(", ".join(t.get("deps") or [])),
            "Evidence": rich(t.get("evidence")),
        }, {
            "Depends On": [("tasks", d) for d in t.get("deps") or []],
            "Phase Link": [("phases", t.get("phase"))],
            "Acceptance Records": [("acceptance", c["id"]) for c in criteria],
        }))
        for criterion in criteria:
            out.append(Record("acceptance", criterion["id"], criterion["id"], {
                "Criterion": rich(criterion.get("text")),
                "Method": select(criterion.get("evidenceClass")),
                "Result": select(RESULT.get(criterion.get("state") or "", "NOT_RUN")),
                "Task ID": rich(t["id"]), "Verifier": rich(t.get("validation")),
                "Evidence": rich(criterion.get("evidence")),
            }, {"Task Link": [("tasks", t["id"])]}))
    for obstacle in project.get("obstacles") or []:
        kind = "HUMAN" if obstacle.get("actor") in ("owner", "human") else (
            "DEPENDENCY" if "DEPENDENCY" in obstacle["type"] else (
                "FAILURE" if "FAIL" in obstacle["type"] else (
                    "PERMISSION" if "PERMISSION" in obstacle["type"] else "OTHER")))
        pid = f"{obstacle['type']}:{obstacle['subject']}"
        affected = obstacle.get("blockers") or []
        out.append(Record("blockers", pid, pid, {
            "Blocker Title": rich(obstacle.get("detail")), "Kind": select(kind),
            "Root Task ID": rich(obstacle["subject"]),
            "Owner Action": rich(obstacle.get("unblock")),
            "Downstream Count": number(downstream.get(obstacle["subject"], 0)),
            "Status": select("OPEN"),
        }, {"Root Task Link": [("tasks", obstacle["subject"])],
            "Affected Tasks": [("tasks", a) for a in affected]}))
    for assumption in project.get("assumptions") or []:
        out.append(Record("assumptions", assumption["id"], assumption["id"], {
            "Statement": rich(f"{assumption['title']}\n\n{assumption.get('assumption') or ''}".strip()),
            "Basis": rich(assumption.get("basis")),
            "Related Task IDs": rich(", ".join(assumption.get("tasks") or [])),
            "Impact": select(assumption.get("impact")),
            "Source State": select(assumption.get("status")),
        }, {"Related Task Links": [("tasks", task) for task in assumption.get("tasks") or []]}))
    return out


def summary(project: dict, key: str) -> dict:
    tasks = [t for t in project["tasks"] if t.get("domain") == "execution"]
    done = sum(t["status"] == "DONE" for t in tasks)
    phase_id = project["project"].get("currentPhase")
    phase = next((p for p in project["phases"] if p["id"] == phase_id), None)
    open_assumptions = [a for a in project.get("assumptions") or [] if a.get("status") == "OPEN"]
    waiting = [t for t in project["tasks"] if (t.get("authority") or "") == "owner"
               and t["status"] != "DONE" and t.get("ready")]
    blockers = [o for o in project.get("obstacles") or []]
    main = (project.get("execution") or {}).get("mainBlocker")
    attention = len(open_assumptions) + len(waiting)
    health = "NEEDS_OWNER" if attention else ("BLOCKED" if main else "ON_TRACK")
    intent = (project.get("handoff") or {}).get("intent") or ""
    return {
        "key": key, "name": project["project"]["name"],
        "phase": f"{phase['id']} — {phase['name']}" if phase else "none",
        "phaseStatus": phase["status"] if phase else "NONE",
        "done": done, "total": len(tasks),
        "progress": round(done / len(tasks), 4) if tasks else 0,
        "attention": attention, "health": health,
        "mainBlocker": (main.get("detail") or main.get("id") or "") if isinstance(main, dict) else str(main or ""),
        "intent": re.sub(r"^#\s*Intent\s*", "", str(intent or "")).strip(),
        "assumptions": [f"{a['id']} · {a['title']} ({a.get('impact')})" for a in open_assumptions],
        "ownerTasks": [f"{t['id']} · {t['title']}" for t in waiting],
        "blockers": len(blockers),
    }


def _callout(text: str, icon: str, color: str = "gray_background") -> dict:
    return {"type": "callout", "callout": {"rich_text": rich(text)["rich_text"],
                                           "icon": {"type": "emoji", "emoji": icon}, "color": color}}


def _bullets(lines: list[str]) -> list[dict]:
    return [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich(line)["rich_text"]}}
            for line in lines]


def hub_blocks(s: dict, config: dict) -> list[dict]:
    pct = f"{round(s['progress'] * 100)}%"
    blocks = [
        _callout(f"{s['name'].upper()} / LIVE STATUS · Project Key {s['key']}\n"
                 f"{s['phase']} · {s['phaseStatus']} · Health: {s['health']}\n"
                 "Mirror of the Praukron chronicle — the repository is the authority. "
                 "Edit only the Assumption Review Queue and the Agent Instructions Outbox.",
                 "📡", "purple_background"),
        _callout(f"{s['done']} / {s['total']} execution tasks DONE · {pct}", "✅"),
        _callout(f"{s['attention']} item(s) need you · {s['blockers']} open blocker(s)", "🚦",
                 "red_background" if s["attention"] else "gray_background"),
    ]
    if s["mainBlocker"]:
        blocks.append(_callout(f"Main blocker: {s['mainBlocker']}", "🚧"))
    if s["intent"]:
        blocks.append({"type": "heading_3", "heading_3": {"rich_text": rich("Intent")["rich_text"]}})
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich(s["intent"])["rich_text"]}})
    blocks.append({"type": "heading_3", "heading_3": {"rich_text": rich("Việc của bạn")["rich_text"]}})
    if s["assumptions"]:
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich(
            "Assumptions OPEN — Assumption Review Queue: Decision → Feedback → Submit")["rich_text"]}})
        blocks += _bullets(s["assumptions"])
    if s["ownerTasks"]:
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich(
            "Owner tasks ready — Agent Instructions Outbox: Target Task Link → Intent → Instruction → Submit")["rich_text"]}})
        blocks += _bullets(s["ownerTasks"])
    if not (s["assumptions"] or s["ownerTasks"]):
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": rich("Không có việc chờ bạn.")["rich_text"]}})
    blocks.append({"type": "divider", "divider": {}})
    for kind in ("assumptions", "outbox", "tasks", "blockers", "phases", "acceptance", "adrs", "ledger",
                 "operations"):
        database = config["databases"][kind]["database_id"]
        blocks.append({"type": "paragraph", "paragraph": {"rich_text": [
            {"type": "mention", "mention": {"type": "database", "database": {"id": database}}}]}})
    return blocks


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


def _existing(client: Client, source: str, key: str) -> dict[str, dict]:
    """This project's rows of one database, by the stable ID in their title."""
    rows = client.query(source, {"property": "Project Key", "rich_text": {"equals": key}})
    found: dict[str, dict] = {}
    for row in rows:
        pid = plain(row["properties"].get(TITLE)).strip()
        if pid and pid not in found:
            found[pid] = row
    return found


def _sync_row(client: Client, config: dict, key: str, source_hash: str, count: int,
              result: "PushResult") -> None:
    """One Operations & Sync row says what the mirror last carried."""
    source = config["databases"]["operations"]["data_source_id"]
    row = _existing(client, source, key).get(SYNC_ROW)
    if row is not None and plain(row["properties"].get("Source Hash")) == source_hash:
        return
    watching = config.get("watch") or {}
    guidance = (f"Push every {watching.get('pushMinutes')} min, pull every {watching.get('pullMinutes')} min."
                if watching else "Watchers not installed: run `praukron notion watch install`.")
    props = {TITLE: title(SYNC_ROW), "Item": rich(f"Praukron chronicle → Notion ({count} records)"),
             "Kind": select("SYNC"), "Status": select("READY"),
             "Last Compile": {"date": {"start": now()}}, "Owner Guidance": rich(guidance),
             "Source Hash": rich(source_hash), "Project Key": rich(key)}
    if row is None:
        client.create_page({"type": "data_source_id", "data_source_id": source}, props)
        result.created += 1
    else:
        client.update_page(row["id"], props)
        result.updated += 1


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
    rows: dict[str, dict[str, dict]] = {kind: _existing(client, dbs[kind]["data_source_id"], key)
                                        for kind in MIRRORED}
    pages: dict[tuple[str, str], str] = {(kind, pid): row["id"] for kind, found in rows.items()
                                         for pid, row in found.items()}

    # Pass 1: every record has a page, so relations can point at it.
    fresh: set[tuple[str, str]] = set()
    for record in wanted:
        if (record.kind, record.pid) not in pages:
            initial = {TITLE: title(record.label), "Project Key": rich(key)}
            if record.kind == "assumptions":
                initial.update({"Decision": select("PENDING"), "Delivery State": select("NOT_SUBMITTED")})
            created = client.create_page({"type": "data_source_id",
                                          "data_source_id": dbs[record.kind]["data_source_id"]}, initial)
            pages[(record.kind, record.pid)] = created["id"]
            rows[record.kind][record.pid] = created
            fresh.add((record.kind, record.pid))
            result.created += 1

    # Pass 2: full properties, written only when the revision moved.
    revisions: list[str] = []
    for record in wanted:
        props = dict(record.props)
        props[TITLE] = title(record.label)
        for name, targets in record.links.items():
            props[name] = relation(sorted({pages[t] for t in targets if t in pages}))
        # An assumption row's revision covers only its text, which pull compares.
        rev = revision_of(record) if record.kind == "assumptions" else revision(props)
        revisions.append(f"{record.kind}:{record.pid}:{rev}")
        ident = (record.kind, record.pid)
        row = rows[record.kind][record.pid]
        if ident not in fresh and plain(row.get("properties", {}).get("Source Revision")) == rev:
            result.unchanged += 1
            continue
        client.update_page(pages[ident], {**props, "Source Revision": rich(rev)})
        if ident not in fresh:
            result.updated += 1

    # Rows whose record is gone: blockers resolve, everything else goes to the trash.
    present = {(r.kind, r.pid) for r in wanted}
    for kind, found in rows.items():
        for pid, row in found.items():
            if (kind, pid) in present:
                continue
            if kind == "blockers":
                if plain(row["properties"].get("Status")) != "RESOLVED":
                    client.update_page(row["id"], {"Status": select("RESOLVED"),
                                                   "Source Revision": rich("resolved")})
                    result.archived += 1
            else:
                client.update_page(row["id"], archived=True)
                result.archived += 1

    _sync_row(client, config, key, revision({"records": sorted(revisions)}), len(wanted), result)

    # The Live Status page: rewritten only when what it shows changed.
    hub_rev = revision({"summary": s, "databases": dbs})
    if config.get("hub") and config.get("hubRevision") != hub_rev:
        for block in client.children(config["hub"]):
            if block.get("type") not in ("child_database", "child_page"):
                client.delete_block(block["id"])
        client.append(config["hub"], hub_blocks(s, config))
        config["hubRevision"] = hub_rev
        save_config(root, config)
        result.hub = True
    return result


# --------------------------------------------------------------------------
# Pull: owner submissions in Notion → RESPONSES.md
# --------------------------------------------------------------------------

PENDING_STATES = {"", "NOT_SUBMITTED", "DRAFT", "QUEUED", "CONFLICT"}


@dataclass
class PullResult:
    applied: list[str] = field(default_factory=list)
    rejected: int = 0
    conflicts: int = 0
    skipped: int = 0

    def line(self) -> str:
        return (f"pull applied={len(self.applied)} rejected={self.rejected} "
                f"conflicts={self.conflicts} skipped={self.skipped}")


def page_url(page_id: str, label: str = "") -> str:
    """A Notion link to a page; the slug carries the label, so the link reads as it."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")
    return f"https://www.notion.so/{slug + '-' if slug else ''}{bare(page_id)}"


def _mark(client: Client, row_id: str, state: str, evidence: str | None = None,
          untick: bool = False) -> None:
    """Move a submitted row along; `evidence` is the link to its Action Ledger row."""
    props: dict = {"Delivery State": select(state)}
    if evidence is not None:
        props["Apply Evidence"] = {"url": evidence}
    if untick:
        props["Submit"] = checkbox(False)
    client.update_page(row_id, props)


class Ledger:
    """One Action Ledger row per submission, found again by its Request ID."""

    def __init__(self, client: Client, config: dict) -> None:
        self.client = client
        self.key = config["projectKey"]
        self.source = config["databases"]["ledger"]["data_source_id"]
        self.rows: dict[str, str] = {}
        for row in client.query(self.source, {"property": "Project Key", "rich_text": {"equals": self.key}}):
            request = plain(row["properties"].get("Request ID"))
            if request:
                self.rows.setdefault(request, row["id"])

    def record(self, request: str, status: str, *, kind: str, label: str, action: str, source: str,
               target: str = "", task_page: str | None = None, base: str = "", applied: str = "",
               error: str = "") -> str:
        props = {
            TITLE: title(label), "Request ID": rich(request), "Requested Action": rich(action),
            "Source": select(source), "Status": select(status), "Base Revision": rich(base),
            "Applied Revision": rich(applied), "Target ID": rich(target), "Error": rich(error),
            "Project Key": rich(self.key),
            "Source Assumption" if kind == "assumptions" else "Source Instruction": relation([request]),
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
    for response in project.responses:
        if response.via and f"request {request}" in response.via:
            return response.id
    return None


def pull(client: Client, root: Path, config: dict | None = None,
         append: Callable | None = None) -> PullResult:
    from . import compile as compiler, respond

    config = config or load_config(root)
    append = append or respond.append
    key = config["projectKey"]
    dbs = config["databases"]
    result = PullResult()
    project = compiler.load(root)
    owners = project.notion_owners
    data = compiler.as_json(project)
    current = {r.pid: revision_of(r) for r in records(root, data) if r.kind == "assumptions"}
    tasks = _existing(client, dbs["tasks"]["data_source_id"], key)
    task_pages = {row["id"]: pid for pid, row in tasks.items()}
    ledger = Ledger(client, config)

    submitted = {"property": "Submit", "checkbox": {"equals": True}}
    queue: list[tuple[str, dict]] = []
    for row in client.query(dbs["assumptions"]["data_source_id"],
                            {"and": [submitted, {"property": "Project Key", "rich_text": {"equals": key}}]}):
        queue.append(("assumptions", row))
    for row in client.query(dbs["outbox"]["data_source_id"], submitted):
        queue.append(("outbox", row))
    queue.sort(key=lambda item: item[1].get("last_edited_time", ""))

    for kind, row in queue:
        props = row["properties"]
        state = plain(props.get("Delivery State"))
        if state not in PENDING_STATES:
            continue
        request = row["id"]
        editor = bare((row.get("last_edited_by") or {}).get("id"))
        base = plain(props.get("Source Revision"))
        if kind == "outbox":
            row_key = plain(props.get("Project Key")).strip()
            if row_key and row_key != key:   # an instance serves one project; skip strays
                result.skipped += 1
                continue
            linked = [task_pages[i] for i in plain(props.get("Target Task Link")).split(",") if i in task_pages]
            target = linked[0] if linked else plain(props.get("Target ID")).strip()
            intent = plain(props.get("Intent")) or "GUIDE"
            source = "APPROVAL" if intent in ("VERIFY", "APPROVE") else "INSTRUCTION"
            asked = f"{target or '?'} {intent}"
        else:
            target = plain(props.get(TITLE)).strip()
            decision = plain(props.get("Decision"))
            feedback = plain(props.get("Feedback")).strip()
            source = "ASSUMPTION" if decision not in ("", "PENDING") else "FEEDBACK"
            asked = f"{target} {decision or 'FEEDBACK'}"
        task_page = tasks[target]["id"] if target in tasks else None
        receipt = dict(kind=kind, action=asked, source=source, target=target, task_page=task_page, base=base)
        short = f"REQ-{bare(request)[:8]}"

        login = owners.get(editor)
        if not login:
            link = ledger.record(request, "REJECTED", label=short, **receipt,
                                 error=f"Submitted by Notion user {editor or 'unknown'}, who is not listed in "
                                       "an ADR's Notion owners (ADR-088). Nothing was written.")
            _mark(client, row["id"], "REJECTED", link, untick=True)
            result.rejected += 1
            continue
        if state != "QUEUED":
            _mark(client, row["id"], "QUEUED")
        done = _already(project, request)
        if done:
            link = ledger.record(request, "APPLIED", label=done, **receipt, applied=done)
            _mark(client, row["id"], "APPLIED", link)
            result.applied.append(done)
            continue

        def refuse(status: str, error: str) -> None:
            link = ledger.record(request, status, label=short, **receipt, error=error)
            _mark(client, row["id"], status, link, untick=True)

        verify = False
        if kind == "assumptions":
            if base != current.get(target):
                refuse("CONFLICT", f"{target} changed in the chronicle after this row was shown; the row is "
                                   "now current. Read it again and tick Submit to resend.")
                result.conflicts += 1
                continue
            if decision in ("CONFIRM", "REVISE", "REJECT"):
                action, text = decision, feedback or f"{decision} (from Notion, no feedback)"
            elif decision == "WITHDRAW":
                action, text = "GUIDE", "WITHDRAW" + (f": {feedback}" if feedback else "")
            elif feedback:
                action, text = "GUIDE", feedback
            else:
                refuse("REJECTED", "Choose a Decision or write Feedback, then Submit.")
                result.rejected += 1
                continue
        else:
            heading = plain(props.get(TITLE)).strip()
            body = plain(props.get("Instruction Text")).strip()
            text = "\n\n".join(p for p in (heading, body) if p)
            if not target or target[:2] not in ("T-", "A-"):
                refuse("REJECTED", "Link a task (Target Task Link) or name a T-/A- id in Target ID, then Submit.")
                result.rejected += 1
                continue
            if not body:
                refuse("REJECTED", "Write the Instruction Text, then Submit.")
                result.rejected += 1
                continue
            priority = plain(props.get("Priority"))
            text += f"\n\n(Notion · intent {intent}" + (f" · priority {priority}" if priority else "") + ")"
            if intent in ("VERIFY", "APPROVE"):
                action, verify = "VERIFY", True
            else:
                action = "GUIDE"
        from .respond import Item, RespondError

        try:
            ids = append(root, [Item(target, action, text)],
                         via=f"notion {editor} request {request}", verify=verify, by=login)
        except RespondError as error:
            refuse("REJECTED", str(error))
            result.rejected += 1
            continue
        _mark(client, row["id"], "LOCAL_RECORDED")
        if kind == "outbox":
            _mark(client, row["id"], "COMPILED")
        link = ledger.record(request, "APPLIED", label=ids[0], **receipt, applied=ids[0])
        _mark(client, row["id"], "APPLIED", link)
        result.applied.extend(ids)
        project = compiler.load(root)
    return result


def revision_of(record: Record) -> str:
    """The Source Revision push writes for an assumption row, without relations
    (they depend on page ids), so pull can compare it."""
    return revision(record.props)


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
    import subprocess

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


__all__ = ["DATABASES", "PullResult", "PushResult", "Locked", "lock", "log", "provision", "pull",
           "push", "records", "setup", "summary", "watch"]
