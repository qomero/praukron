"""Deterministic dashboard projections; no governance state or clock reads."""

from __future__ import annotations

import re
from datetime import date


STATES = ("Ready", "In progress", "Upcoming", "Waiting for human", "Waiting for dependencies",
          "Blocked by failure", "Completed")
UPCOMING_BLOCKER_NOTE = "Upcoming owner decision blocking current work"


def journal(text: str, file: str = "JOURNAL.md") -> list[dict]:
    """Keep authored journal sections, including their original source anchors."""
    parts = re.split(r"(?m)^## (.+)\r?$", text)
    return [{"id": title.strip(), "title": title.strip(), "text": body.strip(),
             "source": {"file": file, "anchor": title.strip()}}
            for title, body in zip(parts[1::2], parts[2::2])]


def recorded_date(records) -> dict | None:
    """Newest authored record date, never a schedule or a filesystem clock.

    Journal headings and Date/Claimed/Time metadata are recorded history.
    Planned dates, prose and fenced examples are deliberately not freshness.
    Equal dates use the source pointer as a stable tie-breaker.
    """
    found = []
    for file, text in records:
        anchor, fenced = None, False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            if fenced:
                continue
            heading = re.match(r"^(#{1,2}) (.+)$", line)
            value = None
            if heading:
                title = heading[2].strip()
                anchor = re.split(r": | — ", title, maxsplit=1)[0]
                if file.endswith("JOURNAL.md") and heading[1] == "##":
                    anchor, value = title, title
            field = re.match(r"^- (?:Date|Claimed|Time):\s*(.+)$", line)
            if field:
                value = field[1]
            match = re.match(r"(\d{4}-\d{2}-\d{2})(?=$|[T\s])", value or "")
            if not match or not anchor:
                continue
            try:
                date.fromisoformat(match[1])
            except ValueError:
                continue
            found.append((match[1], file, anchor))
    if not found:
        return None
    newest, file, anchor = sorted(found, key=lambda x: (-date.fromisoformat(x[0]).toordinal(), x[1], x[2]))[0]
    return {"date": newest, "source": {"file": file, "anchor": anchor}}


def project(data: dict) -> dict:
    """Classify attention from dependencies, responsibility and recorded failures.

    A normal unfinished dependency is work to do, not an exceptional failure.
    Owner work is actionable only at the dependency frontier and in the current
    phase (or feeding it). Each explicit failure remains a separate cause.
    """
    tasks = {t["id"]: t for t in data["tasks"]}
    children: dict[str, set[str]] = {t: set() for t in tasks}
    for task in tasks.values():
        for dep in task["deps"]:
            children.setdefault(dep, set()).add(task["id"])

    def downstream(start: str) -> list[str]:
        seen, pending = {start}, list(children.get(start, ()))
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            pending.extend(children.get(node, ()))
        return sorted(t for t in seen - {start} if t in tasks and tasks[t]["status"] != "DONE")

    cache = {}

    def affected(tid):
        if tid not in cache:
            cache[tid] = downstream(tid)
        return cache[tid]
    current = data["project"]["currentPhase"]
    relevant = {t["id"] for t in tasks.values()
                if t["status"] != "DONE" and
                (not current or t["phase"] in (current, "P-NONE") or t["status"] == "WIP")}
    pending = list(relevant)
    while pending:
        for dep in tasks.get(pending.pop(), {}).get("deps", []):
            if dep in tasks and tasks[dep]["status"] != "DONE" and dep not in relevant:
                relevant.add(dep)
                pending.append(dep)
    unmet = {t: [d for d in task["deps"] if d not in tasks or tasks[d]["status"] != "DONE"]
             for t, task in tasks.items()}
    failed = {f["task"] for f in data["execution"]["failures"] if f.get("failedCriteria")}
    failed.update(t for t, task in tasks.items() if task["status"] == "BLOCKED")
    for event in data["operations"]["events"]:
        if event["id"] in data["operations"]["unresolvedFailures"]:
            failed.update(t for t in [event.get("task"), *event.get("affects", [])] if t in tasks)
    verified = {r["target"] for r in data["responses"] if r["action"] == "VERIFY" and r.get("fromOwner")}
    owner = {t for t, task in tasks.items() if task.get("authority") == "owner" and task["status"] != "DONE"}
    phase_order = {p["id"]: i for i, p in enumerate(data.get("phases", []))}
    later = {t for t in owner if current and tasks[t]["phase"] not in (current, "P-NONE")
             and (phase_order[tasks[t]["phase"]] > phase_order[current]
                  if current in phase_order and tasks[t]["phase"] in phase_order else t not in relevant)}
    actionable = {t for t in owner if t in relevant and t not in later and not unmet[t] and t not in verified}
    current_work = {t for t, task in tasks.items() if task["phase"] == current and task["status"] != "DONE"}
    upcoming_blockers = {t for t in later if set(affected(t)) & current_work}
    roots = []
    for tid in sorted(set(tasks) | (set(children) - set(tasks))):
        task = tasks.get(tid)
        missing = task is None
        if not missing and task["status"] == "DONE":
            continue
        human = tid in actionable
        upcoming = tid in upcoming_blockers
        technical = missing or tid in failed
        if not (human or technical or upcoming):
            continue
        roots.append({"id": tid, "kind": "human" if human else "upcoming" if upcoming else "technical",
                      "actor": "owner" if tid in owner else "agent",
                      "title": task["title"] if task else "Missing dependency record",
                      "affected": affected(tid), "count": len(affected(tid)),
                      "current": tid in relevant or bool(set(affected(tid)) & relevant),
                      "reason": UPCOMING_BLOCKER_NOTE + "." if upcoming else
                                "Owner authority requires a human decision or verification." if human else
                                "Dependency record is missing." if missing else
                                "Explicit BLOCKED status or recorded failure; inspect evidence.",
                      "source": task["source"] if task else None})
    roots.sort(key=lambda r: (not r["current"], -r["count"], r["kind"] != "human", r["id"]))
    active_roots = [r for r in roots if r["current"]]
    impact = sorted({t for r in active_roots for t in r["affected"]})
    human_impact = {t for r in active_roots if r["kind"] in ("human", "upcoming") for t in r["affected"]}
    states = {state: [] for state in STATES}
    for tid, task in tasks.items():
        state = ("Completed" if task["status"] == "DONE" else
                 "Blocked by failure" if tid in failed or any(d not in tasks for d in unmet[tid]) else
                 "Waiting for human" if tid in actionable else
                 "Waiting for dependencies" if unmet[tid] else
                 "Upcoming" if tid in later else
                 "Ready" if tid in verified else
                 "In progress" if task["status"] == "WIP" else "Ready")
        states[state].append(tid)
    for ids in states.values():
        ids.sort()

    awaiting = {a["assumption"] for a in data["assumptionReport"]["awaitingReconciliation"]}
    reviews = {"now": [], "upcoming": [], "assumptions": [], "answered": []}
    owner_errors = [f for f in data.get("validationFindings", [])
                    if f["severity"] == "error" and f.get("ownerAction")]
    error_targets = set()
    for finding in sorted(owner_errors, key=lambda f: (f["where"], f["code"])):
        target = finding["where"].partition("#")[2]
        error_targets.add(target)
        reviews["now"].append({
            "id": "validation-" + finding["code"] + "-" + target,
            "kind": "validation", "target": target, "finding": finding,
            "title": "Owner action: " + tasks.get(target, {}).get("title", finding["where"]),
            "affected": affected(target), "immediate": True, "permission": False,
        })
    for tid in sorted(owner):
        if tid in error_targets:
            continue
        group = "upcoming" if tid in later else "answered" if tid in verified else "now" if tid in actionable else "upcoming"
        criteria = data.get("acceptance", {}).get(tasks[tid].get("ac"), {}).get("criteria", [])
        action = "Verify" if criteria and all(c["state"] == "PASS" for c in criteria) else "Decide"
        reviews[group].append({"id": tid, "kind": "task", "affected": affected(tid),
                               "action": action,
                               "immediate": tid in relevant and (tid not in later or tid in upcoming_blockers),
                               "note": UPCOMING_BLOCKER_NOTE if tid in upcoming_blockers else None,
                               "permission": False})
    for a in data["assumptions"]:
        impact_set = sorted({t for target in a["tasks"] for t in [target, *affected(target)]
                             if t in tasks and tasks[t]["status"] != "DONE"})
        group = "answered" if a["id"] in awaiting or a["status"] != "OPEN" else "assumptions"
        reviews[group].append({"id": a["id"], "kind": "assumption", "affected": impact_set,
                               "immediate": bool(set(a["tasks"]) & relevant),
                               "permission": bool(a["permissions"]), "impact": a["impact"]})
    for items in reviews.values():
        items.sort(key=lambda x: (not x["immediate"],
                                 not (x["permission"] or x.get("impact") == "HIGH"),
                                 -len(x["affected"]), x["id"]))
    return {"roots": roots, "affected": impact, "transitiveHuman": sorted(human_impact),
            "future": sorted((owner - actionable - verified) | later), "states": states, "review": reviews,
            "relevant": sorted(relevant), "runtime": {t: "unknown" for t in data["wip"]}}


def search_index(data: dict) -> list[dict]:
    """One embedded index with explicit destinations and source provenance."""
    entries = []

    def add(kind, item, title, destination, text=""):
        source = item.get("source", {})
        pointer = source.get("file", "") + "#" + source.get("anchor", item["id"])
        entries.append({"kind": kind, "id": item["id"], "title": title,
                        "destination": destination, "source": pointer,
                        "text": " ".join((item["id"], title, text)).lower()})

    for task in data["tasks"]:
        add("Task", task, task["title"], "task-record-" + task["id"])
    for kind, key, prefix, title_key in (
        ("ADR", "decisions", "decision-", "title"),
        ("Assumption", "assumptions", "assumption-", "title"),
        ("Response", "responses", "response-", "text"),
        ("Module", "modules", "module-", "name"),
        ("Phase", "phases", "phase-", "name"),
        ("Journal", "journal", "journal-", "title"),
    ):
        for item in data.get(key, []):
            add(kind, item, item[title_key], prefix + item["id"], item.get("text", ""))
    for cid, contract in data["acceptance"].items():
        for criterion in contract["criteria"]:
            add("Criterion", criterion, criterion["text"], "criterion-" + criterion["id"],
                criterion.get("evidence", "") or "")
    for contract in data["invariants"]:
        for criterion in contract["criteria"]:
            add("Criterion", {**criterion, "source": contract["source"]}, criterion["text"],
                "criterion-" + criterion["id"])
    return sorted(entries, key=lambda e: (e["kind"], e["id"], e["source"]))
