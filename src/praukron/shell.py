"""The dashboard shell: the lanes layout, the waiting model and the shared panels.

Everything here is derived from the compiled project and the records it names.
It decides nothing about authority and writes nothing: the page that embeds it
is a view (ADR-044). The reduction lives in Python so it is tested; the page
only draws what arrives here.

- `lanes` places every task, decision and assumption: phases are columns,
  modules are bands in their phase, a task's column inside its phase is its
  dependency depth there, and decisions and assumptions sit in bands below the
  tasks they bear on. The same input gives the same positions.
- `waiting` lists what needs the owner, each item once, split into what can be
  answered now and what is upcoming, and names the kinds of item the project
  does not record instead of counting them as none.
- `grants` lists the grants AUTHORITY.md records, read through `authority`.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from . import authority, i18n
from .layout import AUTHORITY_DIR
from .model import NO_PHASE, STATUSES, VERIFIED_STATES, Project

# Lanes geometry, in drawing units. A column wraps a depth after ROWS tasks.
DX, DY, ROWS, HEAD, GAP, COLGAP, TOP, MAX_PACK = 100, 22, 10, 24, 14, 80, 70, 6
NO_MODULE = "~none"
NO_PHASE_NAME = "Outside the phase plan"

# Kinds of item that could need the owner but that no record holds today
# (ADR-119): the page says so instead of showing them as zero.
NOT_RECORDED = (
    ("decisions", "Pending decisions"),
    ("delegation", "Requests for delegation"),
    ("escalations", "Escalations"),
)

_DATE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})")
_TOKEN = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*-[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9](?![\w-])")


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _v(value: object) -> str:
    """Record data in markup: escaped and kept out of the interface catalogue."""
    return esc(i18n.verbatim(value))


# ---------------------------------------------------------------- lanes


def phases(compiled: dict) -> list[dict]:
    """The phase columns in plan order, with `P-NONE` last when it holds work."""
    out = [{"id": p["id"], "name": p["name"], "status": p["status"],
            "exitAuthority": p.get("exitAuthority")} for p in compiled["phases"]]
    known = {p["id"] for p in out}
    used = {t["phase"] for t in compiled["tasks"]} | {m["phase"] for m in compiled["modules"]}
    if NO_PHASE not in known and NO_PHASE in used:
        out.append({"id": NO_PHASE, "name": NO_PHASE_NAME, "status": "OPEN", "exitAuthority": None})
    # A task naming a phase the plan does not hold still gets a column.
    for phase in sorted(used - known - {NO_PHASE}):
        out.append({"id": phase, "name": phase, "status": "UNKNOWN", "exitAuthority": None})
    return out


def depths(tasks: list[dict]) -> dict[str, int]:
    """Each task's dependency depth inside its own phase: 0 for a task with no
    dependency in its phase, else one more than its deepest such dependency.

    Iterative, so a long chain cannot exhaust the stack; a cycle (validate
    reports it) is cut where it closes, in id order, so the result is still
    deterministic."""
    by_id = {t["id"]: t for t in tasks}
    depth: dict[str, int] = {}
    for start in sorted(by_id):
        if start in depth:
            continue
        stack, open_ = [(start, False)], set()
        while stack:
            tid, finished = stack.pop()
            if tid in depth:
                continue
            task = by_id[tid]
            same = sorted(d for d in task["deps"] if d in by_id and by_id[d]["phase"] == task["phase"])
            if finished:
                open_.discard(tid)
                depth[tid] = max((depth[d] + 1 for d in same if d in depth), default=0)
                continue
            open_.add(tid)
            stack.append((tid, True))
            stack.extend((d, False) for d in reversed(same) if d not in depth and d not in open_)
    return depth


def _bears_on(compiled: dict) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Which tasks each decision governs and each assumption is relied on by."""
    tasks = {t["id"] for t in compiled["tasks"]}
    governs: dict[str, set[str]] = {d["id"]: {a for a in d["affects"] if a in tasks} for d in compiled["decisions"]}
    for task in compiled["tasks"]:
        for decision in task["decisions"]:
            if decision in governs:
                governs[decision].add(task["id"])
    relied = {a["id"]: sorted(t for t in a["tasks"] if t in tasks) for a in compiled["assumptions"]}
    return {k: sorted(v) for k, v in governs.items()}, relied


def lanes(compiled: dict) -> dict:
    """Deterministic positions for the lanes view. See the module docstring."""
    tasks = compiled["tasks"]
    depth = depths(tasks)
    names = {m["id"]: m["name"] for m in compiled["modules"]}
    columns, bands, positions = [], [], {}
    x = 0
    bottom = TOP
    for phase in phases(compiled):
        own = [t for t in tasks if t["phase"] == phase["id"]]
        modules = [m["id"] for m in compiled["modules"] if m["phase"] == phase["id"]]
        for task in own:
            key = task["module"] or NO_MODULE
            if key not in modules:
                modules.append(key)
        groups = []
        for module in modules:
            by_depth: dict[int, list[str]] = {}
            for task in sorted((t for t in own if (t["module"] or NO_MODULE) == module), key=lambda t: t["id"]):
                by_depth.setdefault(depth[task["id"]], []).append(task["id"])
            groups.append((module, by_depth))
        deepest = max((depth[t["id"]] for t in own), default=0)
        widths = [max([1] + [-(-len(g.get(d, [])) // ROWS) for _, g in groups]) for d in range(deepest + 1)]
        offsets, acc = [], 0
        for w in widths:
            offsets.append(acc)
            acc += w
        width = max(acc, 2) * DX
        y = TOP
        for module, by_depth in groups:
            rows = max([1] + [min(len(ids), ROWS) for ids in by_depth.values()])
            height = HEAD + rows * DY + 6
            bands.append({"id": module, "name": names.get(module), "phase": phase["id"],
                          "x": x - 10, "y": y, "w": width + 20, "h": height})
            for d, ids in by_depth.items():
                for i, tid in enumerate(ids):
                    positions[tid] = [x + (offsets[d] + i // ROWS) * DX + 16, y + HEAD + (i % ROWS) * DY + 8]
            y += height + GAP
        columns.append({"id": phase["id"], "name": phase["name"], "status": phase["status"],
                        "x": x, "w": width, "tasks": len(own),
                        "done": sum(1 for t in own if t["status"] == "DONE")})
        bottom = max(bottom, y)
        x += width + COLGAP
    governs, relied = _bears_on(compiled)
    rows = []
    y = bottom + 20
    for kind, bearing in (("decisions", governs), ("assumptions", relied)):
        ids = [d["id"] for d in compiled[kind]]
        y = _pack(kind, ids, bearing, positions, y, x, rows)
    return {"columns": columns, "bands": bands, "rows": rows, "positions": positions,
            "current": compiled["project"]["currentPhase"], "width": x, "height": y}


def _pack(kind: str, ids: list[str], bearing: dict[str, list[str]], positions: dict,
          y0: int, width: int, rows: list) -> int:
    """Place each record under the mean x of the tasks it bears on, in at most
    MAX_PACK rows; one that bears on no task sits at the left edge."""
    items = []
    for rid in ids:
        xs = [positions[t][0] for t in bearing.get(rid, []) if t in positions]
        items.append((round(sum(xs) / len(xs)) if xs else -60, rid))
    items.sort()
    ends: list[float] = []
    for mean, rid in items:
        at = next((r for r, end in enumerate(ends) if end + 64 <= mean), None)
        if at is None and len(ends) < MAX_PACK:
            at = len(ends)
            ends.append(float("-inf"))
        elif at is None:
            at = min(range(len(ends)), key=lambda r: (ends[r], r))
            mean = int(ends[at] + 64)
        ends[at] = mean
        positions[rid] = [mean, y0 + 26 + at * DY]
    height = 26 + max(len(ends), 1) * DY + 8
    rows.append({"id": kind, "x": -140, "y": y0, "w": width + 80, "h": height})
    return y0 + height + GAP


# ---------------------------------------------------------------- waiting


def first_mentions(journal: list[dict], ids: set[str]) -> dict[str, dict]:
    """The earliest dated journal entry naming each id: the fallback date a
    waiting item may show, always labelled as that (ADR-119)."""
    found: dict[str, dict] = {}
    for entry in journal:
        match = _DATE.match(entry["title"])
        if not match:
            continue
        day = match.group(1)
        for token in set(_TOKEN.findall(entry["title"] + "\n" + (entry.get("text") or ""))):
            if token in ids and (token not in found or day < found[token]["date"]):
                found[token] = {"date": day, "source": entry["source"]}
    return found


def _downstream(start: list[str], children: dict[str, list[str]], open_: set[str]) -> list[str]:
    seen, pending = set(), list(start)
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(children.get(node, ()))
    return sorted(seen & open_)


def _count(word: str, total: int, direct: int) -> str:
    label = {"blocks": "Blocks", "affected": "Affected"}[word]
    if not total:
        return f"{label}: no task"
    noun = "task" if total == 1 else "tasks"
    return f"{label}: {total} {noun} · {direct} direct · {total - direct} indirect"


def delegable(project: Project | None, day: str) -> set[str]:
    """Owner-held tasks inside the scope of a verify grant in force on `day`:
    a delegate may settle them, so they are not counted as waiting."""
    if project is None:
        return set()
    out = set()
    for task in project.tasks:
        if not task.owner_held:
            continue
        held = [g for g in project.grants if g.capability == "verify" and g.covers(task)
                and g.state(day) == "in force" and project.is_owner(g.granted_by)
                and g.via in authority.IN_PERSON]
        if held:
            out.add(task.id)
    return out


def waiting(compiled: dict, project: Project | None = None, day: str | None = None) -> dict:
    """Every recorded item that needs the owner, each once (AC-T-DASH-SHELL-01-03).

    - an open task with `Authority: owner` the owner has not verified;
    - a DONE owner task whose owner verification is missing;
    - an open assumption whose response is not yet given.

    An item is `now` when nothing it depends on is open, else `upcoming`.
    Blocking counts follow dependencies; an assumption blocks nothing by
    record, so the tasks resting on it are `affected` (AC-T-DASH-SHELL-01-04).
    """
    day = day or authority.today()
    tasks = {t["id"]: t for t in compiled["tasks"]}
    open_ = {t for t, task in tasks.items() if task["status"] != "DONE"}
    children: dict[str, list[str]] = {}
    for task in compiled["tasks"]:
        for dep in task["deps"]:
            children.setdefault(dep, []).append(task["id"])
    critical = set(compiled["criticalPath"])
    exits: dict[str, list[str]] = {}
    for phase in compiled["phases"]:
        if phase.get("exitAuthority"):
            exits.setdefault(phase["exitAuthority"], []).append(phase["id"])
    owner_verified = {r["target"] for r in compiled["responses"]
                      if r["action"] == "VERIFY" and r.get("fromOwner")}
    answered = {a["assumption"] for a in compiled["assumptionReport"]["awaitingReconciliation"]}
    mentions = first_mentions(compiled["journal"], set(tasks))
    settled = delegable(project, day)

    def marks(ids: list[str]) -> dict:
        phases_ = sorted({p for t in ids for p in exits.get(t, [])})
        return {"phaseExit": phases_, "criticalPath": any(t in critical for t in ids)}

    items = []
    for tid in sorted(tasks):
        task = tasks[tid]
        if task["authority"] != "owner" or tid in settled or tid in owner_verified:
            continue
        done = task["status"] == "DONE"
        if done and task["validation"] in VERIFIED_STATES:
            continue
        waits_on = [] if done else sorted(d for d in task["deps"] if d not in tasks or d in open_)
        blocked = [] if done else _downstream(children.get(tid, []), children, open_)
        direct = [] if done else sorted(set(children.get(tid, [])) & open_)
        criteria = compiled["acceptance"].get(task["ac"], {}).get("criteria", [])
        passing = bool(criteria) and all(c["state"] == "PASS" for c in criteria)
        mention = mentions.get(tid)
        items.append({
            "id": tid, "kind": "verification" if done else "task",
            "category": "upcoming" if waits_on else "now",
            "action": "Verify" if done or passing else "Decide",
            "why": ("Done, but the owner's verification is not recorded." if done else
                    "Owner-held task: only the owner can close it."),
            "waitsOn": waits_on, "word": "blocks", "tasks": blocked, "direct": direct,
            "count": _count("blocks", len(blocked), len(direct)),
            "since": ({"date": mention["date"], "basis": "first journal mention", "source": mention["source"]}
                      if mention else None),
            **marks([tid]),
        })
    for a in compiled["assumptions"]:
        if a["status"] != "OPEN" or a["id"] in answered:
            continue
        direct = sorted(set(a["tasks"]) & open_)
        affected = sorted(set(direct) | set(_downstream(
            [c for t in a["tasks"] for c in children.get(t, [])], children, open_)))
        recorded = _DATE.match(a["recorded"] or "")
        items.append({
            "id": a["id"], "kind": "assumption", "category": "now", "action": "Confirm or revise",
            "why": "Open assumption: agents rely on it until the owner confirms or revises it.",
            "waitsOn": [], "word": "affected", "tasks": affected, "direct": direct,
            "count": _count("affected", len(affected), len(direct)),
            "since": ({"date": recorded.group(1), "basis": "date recorded", "source": a["source"]}
                      if recorded else None),
            "impact": a["impact"], **marks(affected),
        })
    order = {"task": 0, "verification": 1, "assumption": 2}
    items.sort(key=lambda i: (i["category"] != "now", order[i["kind"]], -len(i["tasks"]), i["id"]))
    return {
        "items": items, "count": len(items),
        "now": sum(1 for i in items if i["category"] == "now"),
        "upcoming": sum(1 for i in items if i["category"] == "upcoming"),
        "kinds": {k: sum(1 for i in items if i["kind"] == k) for k in order},
        "notRecorded": [label for _, label in NOT_RECORDED],
        "delegable": sorted(settled),
    }


def since_text(item: dict) -> str:
    since = item["since"]
    if not since:
        return "Since: not recorded"
    return f"Since {since['date']} ({since['basis']})"


# ---------------------------------------------------------------- grants


def grants(project: Project | None, day: str | None = None) -> dict:
    """The grants AUTHORITY.md records, read through `authority`, and the
    delegated verifications the responses record."""
    if project is None:
        return {"file": False, "grants": [], "delegated": []}
    day = day or authority.today()
    recorded = (Path(project.root) / AUTHORITY_DIR / authority.FILE).is_file()
    view = authority.view(project, day)
    listed = [g for c in view["capabilities"] if c["kind"] == "grant" for g in c["grants"]]
    delegated = []
    for task in project.tasks:
        for v in authority.verifications(project, task.id):
            if v["kind"] == "delegate":
                delegated.append({"task": task.id, **v})
    return {"file": recorded, "grants": listed + list(view["unknown"]), "delegated": delegated}


# ---------------------------------------------------------------- the model


def model(compiled: dict, project: Project | None = None, day: str | None = None) -> dict:
    """What the shell embeds beside the compiled project: positions, the
    waiting model with its display text, and who verified each owner task."""
    w = waiting(compiled, project, day)
    for item in w["items"]:
        item["marks"] = marks_text(item)
        item["sinceText"] = since_text(item)
    verifications = {}
    if project is not None:
        for task in project.tasks:
            found = authority.verifications(project, task.id) if task.owner_held else []
            if found:
                verifications[task.id] = found
    return {"lanes": lanes(compiled), "waiting": w, "verifications": verifications}



# ---------------------------------------------------------------- markup
# Every word below is interface copy and is catalogued in i18n; record text
# (titles, names) goes through `_v` so the catalogue never translates it.

KIND_LABEL = {"task": "Owner task", "verification": "Owner verification", "assumption": "Assumption"}
CATEGORY_LABEL = {"now": "Now", "upcoming": "Upcoming"}


def _titles(compiled: dict) -> dict[str, str]:
    out = {t["id"]: t["title"] for t in compiled["tasks"]}
    out.update({a["id"]: a["title"] for a in compiled["assumptions"]})
    out.update({d["id"]: d["title"] for d in compiled["decisions"]})
    return out


def marks_text(item: dict) -> list[str]:
    out = [f"Phase exit: {p}" for p in item["phaseExit"]]
    if item["criticalPath"]:
        out.append("Critical path")
    return out


def waiting_strip(w: dict, compiled: dict) -> str:
    """The strip: one card per waiting item. Hidden, it leaves a handle that
    still carries the count (AC-T-DASH-SHELL-01-06)."""
    titles = _titles(compiled)
    cards = []
    for item in w["items"]:
        meta = " · ".join([item["action"], item["count"]])
        extra = " · ".join(marks_text(item) + [since_text(item)])
        cards.append(
            f'<li><button type="button" class="wcard" data-select="{esc(item["id"])}" data-category="{item["category"]}">'
            f'<span class="wrow"><span class="wglyph" aria-hidden="true">!</span> <code>{esc(item["id"])}</code>'
            f' <span class="when {item["category"]}">{CATEGORY_LABEL[item["category"]]}</span>'
            f' <span class="wkind">{KIND_LABEL[item["kind"]]}</span></span>'
            f'<span class="wtitle">{_v(titles.get(item["id"], ""))}</span>'
            f'<span class="wmeta">{esc(meta)}</span><span class="wmeta">{esc(extra)}</span></button></li>')
    unrecorded = " · ".join(f"{label}: not recorded" for label in w["notRecorded"])
    left_out = (f'<p class="note">Left out, because a verify grant in force lets a delegate settle them: '
                f'<code>{esc(", ".join(w["delegable"]))}</code></p>' if w["delegable"] else "")
    summary = f'{w["now"]} can be answered now · {w["upcoming"]} upcoming'
    return (
        '<section id="waiting" class="shell-waiting" aria-labelledby="waiting-h">'
        '<div class="waiting-head">'
        f'<h2 id="waiting-h"><span class="wglyph" aria-hidden="true">!</span> Waiting for you '
        f'<span class="count" data-waiting-count>{w["count"]}</span></h2>'
        f'<p class="note" id="waiting-sum">{summary}</p>'
        '<button type="button" id="waiting-only" class="chip" aria-pressed="false">Show only these and what they block</button>'
        '<span class="grow"></span>'
        '<button type="button" class="edge-btn" data-toggle="waiting" aria-controls="waiting-cards" aria-expanded="true"'
        ' title="Hide the waiting strip (Shift+W)">Hide strip</button></div>'
        + (f'<ol id="waiting-cards" class="wcards">{"".join(cards)}</ol>' if cards else
           '<p class="note" id="waiting-cards">Nothing recorded waits for you.</p>')
        + f'<p class="note waiting-unrecorded">{unrecorded}</p>' + left_out
        + '<button type="button" id="waiting-handle" class="handle handle-row wait-handle" data-toggle="waiting"'
        f' aria-expanded="false" title="Show the waiting strip (Shift+W)"><span class="wglyph" aria-hidden="true">!</span>'
        f' Waiting for you <span class="count">{w["count"]}</span> · {summary} · Show the strip</button>'
        '</section>')


def queue_pane(w: dict, compiled: dict) -> str:
    """The Review queue tab: the same items as the strip, as a table."""
    titles = _titles(compiled)
    if not w["items"]:
        return '<p class="note">Nothing recorded waits for you.</p>'
    rows = "".join(
        f'<tr tabindex="0" data-select="{esc(i["id"])}"><td><code>{esc(i["id"])}</code> {_v(titles.get(i["id"], ""))}</td>'
        f'<td>{KIND_LABEL[i["kind"]]}</td><td><span class="when {i["category"]}">{CATEGORY_LABEL[i["category"]]}</span></td>'
        f'<td>{esc(i["action"])}</td><td>{esc(i["why"])}</td><td>{esc(since_text(i))}</td>'
        f'<td>{"<code>" + esc(", ".join(i["waitsOn"])) + "</code>" if i["waitsOn"] else "Nothing"}</td>'
        f'<td>{esc(i["count"])}{"".join(" · " + esc(m) for m in marks_text(i))}</td></tr>'
        for i in w["items"])
    return ('<p class="note">Everything recorded that needs the owner. Select a row for its record.</p>'
            '<table class="shell-table"><thead><tr><th>Item</th><th>Kind</th><th>When</th><th>Action</th>'
            '<th>Why</th><th>Since</th><th>Waits on</th><th>Blocks or affects</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


JOURNAL_SHOWN = 200


def journal_pane(compiled: dict) -> str:
    """The newest journal entries, each linking to its full record in Audit."""
    entries = list(reversed(compiled["journal"]))
    if not entries:
        return '<p class="note">No journal entries recorded.</p>'
    items = "".join(
        f'<li><a href="#journal-{esc(e["id"])}" data-record="journal-{esc(e["id"])}">{_v(e["title"])}</a></li>'
        for e in entries[:JOURNAL_SHOWN])
    more = (f'<p class="note">{len(entries) - JOURNAL_SHOWN} older entries are in Audit.</p>'
            if len(entries) > JOURNAL_SHOWN else "")
    return f'<p class="note">{len(entries)} entries, newest first.</p><ul class="plain shell-list">{items}</ul>' + more


def _generated(text: str) -> str:
    """A tool-written message: translated when the catalogue holds all of its
    words, else shown as written so no half-translated sentence appears."""
    rest = i18n.message_pattern().sub("", text)
    rest = re.sub(r"\b[A-Z]+(?:-[A-Za-z0-9]+)+\b|\bP\d+\b|`[^`]*`", "", rest)
    return _v(text) if re.search(r"[A-Za-z]{2,}", rest) else esc(text)


def validation_pane(compiled: dict) -> str:
    findings = compiled["validationFindings"]
    if not findings:
        return '<p class="note">Validation reports no finding.</p>'
    rows = "".join(
        f'<tr><td>{"✕" if f["severity"] == "error" else "⚠"} <code>{esc(f["severity"])}</code></td>'
        f'<td><code>{esc(f["code"])}</code></td><td><code>{esc(f["where"])}</code></td><td>{_generated(f["message"])}</td></tr>'
        for f in findings)
    return ('<table class="shell-table"><thead><tr><th>Severity</th><th>Code</th><th>Where</th><th>Message</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>')


def grants_pane(g: dict) -> str:
    """The Agent grants tab: only what AUTHORITY.md records (AC-T-DASH-SHELL-01-08).
    It lists; the owner's grant and revoke stay where they are, in Review."""
    where = ('<p class="note">This list only reads AUTHORITY.md. The owner grants and revokes in person, '
             'at a terminal or in Review on the served page.</p>')
    if not g["file"]:
        body = '<p class="note">No AUTHORITY.md in this chronicle: no grant is recorded.</p>'
    elif not g["grants"]:
        body = '<p class="note">AUTHORITY.md records no grant.</p>'
    else:
        rows = "".join(
            f'<tr><td><code>{esc(x["id"])}</code></td><td><code>{esc(x["capability"])}</code></td>'
            f'<td>{_v(x["delegate"])}</td>'
            f'<td>{"Whole project" if x["wholeProject"] else "<code>" + esc(", ".join(x["scope"])) + "</code>"}</td>'
            f'<td>{_v(x["grantedBy"])}</td><td>{esc(x["date"])}</td>'
            f'<td>{"No end" if x["noEnd"] else esc(x["until"])}</td>'
            f'<td><code>{esc(x.get("state", ""))}</code></td></tr>'
            for x in g["grants"])
        body = ('<table class="shell-table"><thead><tr><th>Grant</th><th>Capability</th><th>Delegate</th>'
                '<th>Scope</th><th>Granted by</th><th>Date</th><th>Until</th><th>State</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>')
    if g["delegated"]:
        body += ('<h3>Delegated verifications</h3><p class="note">DELEGATE_VERIFIED, shown apart from HUMAN_VERIFIED.</p>'
                 '<ul class="plain shell-list">' + "".join(
                     f'<li><code>{esc(d["task"])}</code> · delegate {_v(d["delegate"])} · on the authority of '
                     f'{_v(d["onAuthorityOf"])} · <code>{esc(d["grant"])}</code> · <code>{esc(d["response"])}</code>'
                     f' · {esc(d["date"])}</li>' for d in g["delegated"]) + '</ul>')
    return where + body


def text_pane() -> str:
    """The text view: every node the lanes draw, under the same filters. The
    page fills the rows; the head is here so it reads without script."""
    return ('<p class="note"><span id="text-count">0</span> rows: every task, decision and assumption the lanes draw, '
            'under the same filters. Select a row with Enter.</p>'
            '<table class="shell-table" id="text-table"><thead><tr><th>Id</th><th>Kind</th><th>Title</th><th>Phase</th>'
            '<th>Module</th><th>Status</th><th>Verification</th><th>Waiting</th><th>Depends on</th><th>Leads to</th>'
            '</tr></thead><tbody id="text-rows"></tbody></table>')


def relations_pane() -> str:
    return ('<p class="note" id="rel-note">Every line the graph draws, as a sentence.</p>'
            '<ul class="plain shell-list" id="rel-rows"></ul>')


BOTTOM_TABS = (
    ("queue", "Review queue"), ("journal", "Journal"), ("findings", "Validation"),
    ("grants", "Agent grants"), ("text", "Text view"), ("rel", "Relations"),
)


def bottom_panel(panes: dict[str, str]) -> str:
    tabs = "".join(
        f'<button type="button" role="tab" id="{name}-tab" aria-controls="p-{name}"'
        f' aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">{label}</button>'
        for i, (name, label) in enumerate(BOTTOM_TABS))
    bodies = "".join(
        f'<div role="tabpanel" id="p-{name}" class="bpane{" is-selected" if i == 0 else ""}"'
        f' aria-labelledby="{name}-tab" tabindex="0">{panes.get(name, "")}</div>'
        for i, (name, _) in enumerate(BOTTOM_TABS))
    return (
        '<section id="bottom" class="shell-bottom" aria-labelledby="bottom-h">'
        '<h2 id="bottom-h" class="sr">Lists</h2>'
        '<div class="resizer resizer-h" role="separator" aria-orientation="horizontal" aria-label="Resize the bottom panel"'
        ' tabindex="0" data-resize="bottom"></div>'
        '<button type="button" id="bottom-handle" class="handle handle-row" data-toggle="bottom" aria-expanded="false"'
        ' title="Show the bottom panel (b)">Review queue · Journal · Validation · Agent grants · Text view · Relations'
        ' · Show the panel</button>'
        f'<div class="btabs"><div role="tablist" aria-label="Lists" class="btablist">{tabs}</div><span class="grow"></span>'
        '<button type="button" class="edge-btn" data-toggle="bottom" aria-controls="bottom-panes" aria-expanded="true"'
        ' title="Hide the bottom panel (b)">Hide panel</button></div>'
        f'<div class="bpanes" id="bottom-panes">{bodies}</div></section>')


def side_panel() -> str:
    return (
        '<aside id="side" class="shell-side" aria-labelledby="side-h">'
        '<div class="resizer resizer-v" role="separator" aria-orientation="vertical" aria-label="Resize the side panel"'
        ' tabindex="0" data-resize="side"></div>'
        '<div class="side-bar"><button type="button" class="edge-btn" data-toggle="side" aria-controls="side-body"'
        ' aria-expanded="true" title="Hide the side panel (s)">Hide panel</button>'
        '<button type="button" id="side-pin" class="edge-btn" aria-pressed="false">Keep hidden on select</button></div>'
        '<button type="button" id="side-handle" class="handle handle-col" data-toggle="side" aria-expanded="false"'
        ' title="Show the side panel (s)"><span class="vlabel">Details</span></button>'
        '<div id="side-body" tabindex="-1"><h2 id="side-h">Nothing selected</h2>'
        '<p class="note">Select a node, a waiting item or a row. Its record shows here.</p></div></aside>')


LEGEND = (
    ('<circle cx="11" cy="8" r="5" class="lg-done"/>', "Done: filled circle"),
    ('<circle cx="11" cy="8" r="5" class="lg-todo"/>', "To do: ring"),
    ('<circle cx="11" cy="8" r="5" class="lg-todo"/><circle cx="11" cy="8" r="2" class="lg-dot"/>', "In progress: ring with a dot"),
    ('<circle cx="11" cy="8" r="5" class="lg-ready"/>', "Ready: thick ring"),
    ('<circle cx="11" cy="8" r="5" class="lg-blocked"/><path d="M8,5L14,11M14,5L8,11" class="lg-blocked"/>', "Blocked: cross"),
    ('<path d="M11,1L18,8L11,15L4,8Z" class="lg-wait"/>', "Waiting for you: diamond and !"),
    ('<rect x="7" y="4" width="8" height="8" class="lg-decision"/>', "Decision: square"),
    ('<path d="M11,2L17,13L5,13Z" class="lg-assumption"/>', "Assumption: triangle"),
    ('<path d="M1,8L18,8" class="lg-edge"/>', "Arrow from A to B: B depends on A"),
    ('<path d="M1,8L21,8" class="lg-gov"/>', "Dashed: governs; dotted: relied on"),
    ('<circle cx="11" cy="8" r="6.5" class="lg-cluster"/>', "Force: a named ring holds one module's tasks"),
)


def lanes_section(compiled: dict) -> str:
    """The main surface of Work: the lanes graph and its toolbar. The graph is
    drawn by the page from the embedded positions; the text view and the
    relations list carry the same content as words."""
    phases_ = phases(compiled)
    modules = [m["id"] for m in compiled["modules"]]
    if any(not t["module"] for t in compiled["tasks"]):
        modules.append(NO_MODULE)
    def select(element_id: str, label: str, values: list[tuple[str, str]]) -> str:
        options = "".join(f'<option value="{esc(v)}">{text}</option>' for v, text in values)
        return (f'<label class="lanes-select"><span class="sr">{label}</span><select id="{element_id}" aria-label="{label}">'
                f'<option value="">{label}: all</option>{options}</select></label>')

    legend = "".join(f'<svg viewBox="0 0 22 16" aria-hidden="true">{shape}</svg><span>{text}</span>'
                     for shape, text in LEGEND)
    return (
        '<section class="lanes" id="lanes" aria-labelledby="lanes-title">'
        '<div class="lanes-toolbar" role="toolbar" aria-label="Lanes">'
        '<h2 id="lanes-title" class="sr">Work graph</h2>'
        '<div class="viewswitch" role="group" aria-label="View">'
        '<button type="button" class="edge-btn" data-view="lanes" aria-pressed="true"'
        ' title="Lanes: phases, modules and dependency order, to work (1)">Lanes</button>'
        '<button type="button" class="edge-btn" data-view="force" aria-pressed="false"'
        ' title="Force: tasks clustered by module, to analyse relations (2)">Force</button></div>'
        + select("lanes-phase", "Phase", [(p["id"], esc(p["id"])) for p in phases_])
        + select("lanes-module", "Module", [(m, "No module" if m == NO_MODULE else esc(m)) for m in modules])
        + select("lanes-status", "Status", [(s, esc(s)) for s in STATUSES])
        + '<button type="button" class="edge-btn" id="lanes-decisions" aria-pressed="true">Decisions</button>'
          '<button type="button" class="edge-btn" id="lanes-assumptions" aria-pressed="true">Assumptions</button>'
          '<button type="button" class="edge-btn" id="lanes-governs" aria-pressed="false">Governs lines</button>'
          '<button type="button" class="edge-btn" id="lanes-chain" aria-pressed="true">Whole chain</button>'
          '<button type="button" class="edge-btn" id="lanes-fit" title="Fit the whole graph (0)">Fit all</button>'
          '<button type="button" class="edge-btn" id="lanes-current">Current phase</button>'
          '<span id="force-status" class="note" hidden>Laying out the graph…</span>'
          '</div>'
          '<div id="stage" class="stage" tabindex="0" role="application" aria-roledescription="graph"'
          ' aria-label="Lanes graph" aria-describedby="stage-desc" data-view="lanes">'
          '<p id="stage-desc" class="sr">Arrow keys move the selection: left to a dependency, right to what it unblocks, '
          'up and down within its module. Enter opens the record. Escape clears it. The text view in the bottom panel '
          'lists the same content.</p>'
          '<svg id="lanes-svg" aria-hidden="true" focusable="false"><defs><marker id="lanes-arrow" viewBox="0 0 10 10" refX="9" refY="5"'
          ' markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead"/>'
          '</marker></defs><g id="lanes-world"><g id="lanes-decor"></g><g id="force-decor" display="none"></g>'
          '<g id="graph-lines"></g><g id="graph-shapes"></g></g></svg>'
          f'<details class="legend"><summary>Legend</summary><div class="legend-grid">{legend}'
          '<span></span><span>H or D beside a task: HUMAN_VERIFIED or DELEGATE_VERIFIED</span></div></details>'
          '<div id="lanes-tip" class="tip" hidden></div></div></section>')


KEYS = (
    ("/", "Search"), ("1", "Work: the lanes view"), ("2", "Work: the force view, tasks clustered by module"),
    ("← →", "To a dependency, or to what the task unblocks"),
    ("↑ ↓", "Previous or next in the same module"), ("Enter", "Open the record in the side panel"),
    ("Escape", "Clear the selection"), ("s", "Hide or show the side panel"), ("b", "Hide or show the bottom panel"),
    ("Shift+W", "Hide or show the waiting strip; its count stays on the handle"),
    ("f", "Focus: hide every panel; press again to bring them back"), ("w", "Next item waiting for you"),
    ("+ − 0", "Zoom in, out, fit"), ("?", "This list"),
)


def help_dialog() -> str:
    rows = "".join(f"<dt><code>{esc(k)}</code></dt><dd>{text}</dd>" for k, text in KEYS)
    return ('<dialog id="shell-help" aria-labelledby="shell-help-title"><h2 id="shell-help-title">Keys</h2>'
            f'<dl class="keys">{rows}</dl><p class="note">Keys work outside text fields. Panel visibility, sizes, '
            'the workspace and the view are remembered in this browser; without storage the page starts with its defaults.</p>'
            '<button type="button" id="shell-help-close">Close</button></dialog>')


def header_controls() -> str:
    return ('<div class="shell-controls">'
            '<button type="button" id="focus-btn" class="edge-btn" aria-pressed="false"'
            ' title="Hide every panel so the graph fills the screen, or bring them back (f)">Focus</button>'
            '<button type="button" id="keys-btn" class="edge-btn" aria-haspopup="dialog" aria-controls="shell-help">Keys</button>'
            '</div>')


# ---------------------------------------------------------------- style
# Hand-written; no library. Colours reuse the page's tokens; the two new kinds
# get their own, defined for both themes like the others.

STYLE = """
:root, :root[data-theme="light"] { --decision: #6a3fb5; --assumption: #0b6f69; --waitbg: #fff3df; }
:root[data-theme="dark"] { --decision: #c3a6ff; --assumption: #5fd4c8; --waitbg: #33270f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme]) { --decision: #c3a6ff; --assumption: #5fd4c8; --waitbg: #33270f; } }
.skip { position: absolute; left: -9999px; top: 8px; z-index: 50; background: var(--panel); color: var(--ink);
        padding: 8px 12px; border: 2px solid var(--accent); border-radius: 8px; }
.skip:focus { left: 8px; }
.shell-top { padding: 8px 20px; margin: 0; background: var(--panel); border-bottom: 1px solid var(--line); }
.shell-top h1 { font-size: 18px; }
.shell-controls { display: inline-flex; gap: 6px; }
.edge-btn { border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink); border-radius: 6px;
            padding: 3px 9px; font-size: 13px; cursor: pointer; white-space: nowrap; }
.edge-btn:hover { border-color: var(--accent); }
.edge-btn[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.grow { flex: 1; }
/* The waiting strip */
.shell-waiting { background: var(--waitbg); border-bottom: 1px solid var(--line); padding: 6px 20px 6px; min-width: 0; }
.waiting-head { display: flex; gap: 6px 14px; align-items: center; flex-wrap: wrap; }
.waiting-head h2 { font-size: 15px; margin: 0; }
.waiting-head p { margin: 0; }
.count { display: inline-block; min-width: 22px; text-align: center; background: var(--warn); color: var(--on-warn);
         border-radius: 999px; padding: 0 7px; font-size: 13px; font-weight: 700; }
.wglyph { position: relative; display: inline-grid; place-items: center; width: 18px; height: 18px;
          font-size: 11px; font-weight: 800; color: var(--ink); }
.wglyph::before { content: ""; position: absolute; inset: 3px; border: 2px solid var(--warn); transform: rotate(45deg); }
.chip { border: 1px solid var(--warn); background: transparent; border-radius: 999px; padding: 2px 10px; cursor: pointer; font-size: 13px; }
.chip[aria-pressed="true"] { background: var(--warn); color: var(--on-warn); }
.wcards { display: flex; gap: 8px; overflow-x: auto; list-style: none; margin: 6px 0 2px; padding: 0 0 4px; }
.wcard { display: flex; flex-direction: column; gap: 1px; min-width: 240px; max-width: 290px; width: 100%; text-align: left;
         background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: 8px;
         padding: 4px 8px; cursor: pointer; font-size: 12.5px; line-height: 1.35; }
.wcard[aria-current="true"] { outline: 2px solid var(--accent); }
.wcard[data-category="upcoming"] { border-left-style: dashed; }
.wtitle { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wmeta { color: var(--muted); }
.when { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; }
.when.now { color: var(--warn); } .when.upcoming { color: var(--muted); }
.wkind { font-size: 11px; color: var(--muted); }
.waiting-unrecorded { margin: 2px 0 0; }
.handle { display: none; border: 0; background: var(--panel); color: var(--ink); cursor: pointer; font-size: 12.5px; }
.handle:hover { background: var(--sunken); }
.handle-row { width: 100%; text-align: left; padding: 4px 20px; }
.wait-handle { background: var(--waitbg); font-weight: 600; }
.handle-col { writing-mode: vertical-rl; width: 28px; height: 100%; padding: 10px 0; }
html.hide-waiting .shell-waiting { padding: 0; }
html.hide-waiting .shell-waiting > :not(#waiting-handle) { display: none; }
html.hide-waiting #waiting-handle { display: block; }
/* The middle: the workspace and the side panel */
.shell-middle { min-height: 0; }
.shell-side { position: relative; background: var(--panel); border-left: 1px solid var(--line); min-width: 0; min-height: 0;
              display: flex; flex-direction: column; overflow: hidden; }
.side-bar { display: flex; gap: 6px; justify-content: space-between; padding: 8px 14px 2px; flex-wrap: wrap; }
#side-body { overflow: auto; padding: 4px 16px 24px; flex: 1; min-height: 0; }
#side-body:focus { outline: none; }
#side-body h2 { font-size: 16px; margin: 6px 0 8px; }
#side-body dl { display: grid; grid-template-columns: 1fr; gap: 2px; font-size: 13px; }
#side-body dt { color: var(--muted); margin-top: 6px; }
#side-body dd { margin: 0; overflow-wrap: anywhere; }
.crumbs { font-size: 12px; color: var(--muted); display: flex; gap: 4px; flex-wrap: wrap; }
.waitbox { border: 1px solid var(--warn); background: var(--waitbg); border-radius: 8px; padding: 6px 10px; margin: 8px 0; font-size: 13px; }
.waitbox p { margin: 4px 0; }
.delegatebox { border: 1px dashed var(--decision); border-radius: 8px; padding: 6px 10px; margin: 8px 0; font-size: 13px; }
html.hide-side .shell-side > :not(#side-handle) { display: none; }
html.hide-side #side-handle { display: block; }
/* The bottom panel */
.shell-bottom { position: relative; background: var(--panel); border-top: 1px solid var(--line); min-height: 0;
                display: flex; flex-direction: column; }
.btabs { display: flex; align-items: center; gap: 6px; padding: 0 12px; border-bottom: 1px solid var(--line); }
.btablist { display: flex; gap: 2px; overflow-x: auto; }
.btablist [role="tab"] { border: 0; background: none; padding: 7px 10px; border-bottom: 2px solid transparent; cursor: pointer;
                         white-space: nowrap; color: var(--muted); font-size: 13px; }
.btablist [role="tab"][aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); font-weight: 650; }
.bpanes { overflow: auto; flex: 1; min-height: 0; padding: 6px 16px 14px; }
html.js .bpane:not(.is-selected) { display: none; }
.shell-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.shell-table th, .shell-table td { text-align: left; padding: 3px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.shell-table th { position: sticky; top: -6px; background: var(--panel); }
.shell-table tr[data-select], .shell-table tr[data-node] { cursor: pointer; }
.shell-table tr[aria-current="true"] { background: var(--sunken); }
.shell-list li { padding: 2px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
html.hide-bottom .shell-bottom > :not(#bottom-handle) { display: none; }
html.hide-bottom #bottom-handle { display: block; }
.resizer { position: absolute; z-index: 6; background: transparent; }
.resizer:hover, .resizer:focus-visible, .resizer.dragging { background: var(--accent); opacity: .5; }
.resizer-v { left: -3px; top: 0; bottom: 0; width: 6px; cursor: col-resize; }
.resizer-h { top: -3px; left: 0; right: 0; height: 6px; cursor: row-resize; }
html.resizing { user-select: none; }
/* Lanes */
.lanes { display: flex; flex-direction: column; height: var(--lanes-h, 70vh); min-height: 320px; margin: 4px 0 18px;
         border: 1px solid var(--line); border-radius: var(--radius-s); background: var(--panel); overflow: hidden; }
.lanes-toolbar { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; padding: 6px 10px;
                 border-bottom: 1px solid var(--line); font-size: 13px; }
.lanes-title { font-size: 14px; margin: 0; }
.lanes-toolbar select { font: inherit; font-size: 13px; color: var(--ink); background: var(--panel);
                        border: 1px solid var(--line-strong); border-radius: 6px; padding: 2px 6px; max-width: 190px; }
.stage { position: relative; flex: 1; min-height: 0; overflow: hidden; background: var(--bg); cursor: grab; touch-action: none; }
.stage:focus-visible { outline: 2px solid var(--accent); outline-offset: -3px; }
#lanes-svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.legend { position: absolute; left: 8px; bottom: 8px; background: var(--panel); border: 1px solid var(--line);
          border-radius: 8px; padding: 4px 8px; font-size: 12px; max-width: calc(100% - 16px); }
.legend summary { cursor: pointer; }
.legend-grid { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; align-items: center; margin-top: 4px; }
.legend svg { width: 22px; height: 16px; }
.tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
       padding: 4px 8px; font-size: 12px; max-width: 340px; box-shadow: var(--shadow); z-index: 5; }
.lg-done { fill: var(--good); stroke: var(--good); } .lg-todo { fill: none; stroke: var(--todo); stroke-width: 1.6; }
.lg-dot { fill: var(--wip); } .lg-ready { fill: none; stroke: var(--accent); stroke-width: 3; }
.lg-blocked { fill: none; stroke: var(--bad); stroke-width: 1.6; } .lg-wait { fill: none; stroke: var(--warn); stroke-width: 2; }
.lg-decision { fill: none; stroke: var(--decision); stroke-width: 1.6; } .lg-assumption { fill: none; stroke: var(--assumption); stroke-width: 1.6; }
.lg-edge { stroke: var(--line-strong); marker-end: url(#lanes-arrow); } .lg-gov { stroke: var(--decision); stroke-dasharray: 3 3; }
.l-edge { fill: none; stroke: var(--line-strong); stroke-width: 1; opacity: .55; }
.l-edge.gov { stroke: var(--decision); stroke-dasharray: 3 3; }
.l-edge.asm { stroke: var(--assumption); stroke-dasharray: 1 3; }
.l-edge.hl { opacity: 1; stroke-width: 2; }
.l-edge.waitpath { stroke: var(--warn); stroke-width: 2; opacity: .9; }
.arrowhead { fill: var(--line-strong); }
.l-node { cursor: pointer; }
.l-node .shape { stroke-width: 1.6; fill: var(--bg); }
.l-node.task .shape { stroke: var(--todo); }
.l-node.task.DONE .shape { fill: var(--good); stroke: var(--good); }
.l-node.task.WIP .shape { stroke: var(--wip); }
.l-node.task .dot { fill: var(--wip); }
.l-node.task.ready .shape { stroke: var(--accent); stroke-width: 3; }
.l-node.task.BLOCKED .shape, .l-node .x { stroke: var(--bad); }
.l-node.decision .shape { fill: var(--panel); stroke: var(--decision); }
.l-node.assumption .shape { fill: var(--panel); stroke: var(--assumption); }
.l-node.assumption.OPEN .shape { fill: var(--assumption); }
.l-node .waitring { fill: none; stroke: var(--warn); stroke-width: 2.4; }
.l-node text { font-size: 10px; fill: var(--ink); paint-order: stroke; stroke: var(--bg); stroke-width: 3px;
               stroke-linejoin: round; pointer-events: none; }
.l-node text.opt { display: none; }
#lanes-svg.near .l-node text.opt, .l-node.sel text.opt, .l-node.hl text.opt, .l-node.wait text.opt { display: inline; }
.l-node text.verif { font-weight: 800; font-size: 8px; }
.l-node.sel .shape { stroke: var(--ink); stroke-width: 3; }
.l-band { fill: var(--sunken); opacity: .7; }
.l-band-label, .l-row-label { font-size: 11px; fill: var(--muted); }
.l-col-label { font-size: 15px; font-weight: 700; fill: var(--ink); }
.l-col-sub { font-size: 11px; fill: var(--muted); }
.l-dim { opacity: .12; }
.l-gone { display: none; }
/* Force: the second view on the same surface and the same nodes */
.viewswitch { display: inline-flex; gap: 0; }
.viewswitch .edge-btn { border-radius: 0; }
.viewswitch .edge-btn:first-child { border-radius: 6px 0 0 6px; }
.viewswitch .edge-btn:last-child { border-radius: 0 6px 6px 0; border-left-width: 0; }
.lg-cluster { fill: none; stroke: var(--line-strong); stroke-dasharray: 2 2; }
.f-cluster { fill: var(--sunken); opacity: .55; stroke: var(--line); }
.f-label { font-size: 12px; font-weight: 650; fill: var(--muted); paint-order: stroke; stroke: var(--bg);
           stroke-width: 3px; stroke-linejoin: round; }
.keys { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; }
.keys dd { margin: 0; }
#shell-help { max-width: min(560px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: 12px;
              background: var(--panel); color: var(--ink); }
/* With script, one screen: the graph and the panels share it, and the
   workspace scrolls inside the middle. Without script, the page flows. */
@media (min-width: 900px) {
  html.js body { height: 100vh; overflow: hidden; }
  html.js .shell { display: grid; height: 100vh; grid-template-rows: auto auto minmax(0, 1fr) auto; }
  html.js .shell-middle { display: grid; grid-template-columns: minmax(0, 1fr) var(--side-w, 380px); }
  html.js.hide-side .shell-middle { grid-template-columns: minmax(0, 1fr) auto; }
  html.js .shell-bottom { height: var(--bottom-h, 260px); }
  html.js.hide-bottom .shell-bottom { height: auto; }
  html.js .shell-middle > main { overflow: auto; min-height: 0; max-width: none; margin: 0; padding: 0 20px 40px; }
}
@media (max-width: 899px) {
  .resizer { display: none; }
  .shell-side { border-left: 0; border-top: 1px solid var(--line); }
  .handle-col { writing-mode: horizontal-tb; width: 100%; height: auto; padding: 4px 20px; text-align: left; }
  .shell-bottom .bpanes { max-height: 60vh; }
  .lanes { height: 70vh; }
}
@media print { .shell-waiting, .shell-side, .shell-bottom, .lanes { display: none; } }
"""


# ---------------------------------------------------------------- script
# Hand-written, no library, no network. It runs after the page's own scripts
# and reads their one embedded project (DATA, byId, decisionById), their
# cross-filters (filters, passes, applyFilters) and their tab switch
# (selectTab). Everything it keeps is presentation state.

SCRIPT = r"""
// --- Shell: one selection, the panels and the lanes view --------------
const SHELL = JSON.parse(document.getElementById('shell-data').textContent);
const SH_SVG = 'http://www.w3.org/2000/svg';
const shRoot = document.documentElement;
const shWait = Object.fromEntries(SHELL.waiting.items.map(w => [w.id, w]));
const shAssumption = Object.fromEntries(DATA.assumptions.map(a => [a.id, a]));
const shState = { sel: null, waitOnly: false, built: false, tf: { x: 0, y: 0, k: 1 }, fit: null, fitPhase: null, size: null };
const shSideEmpty = document.getElementById('side-body').innerHTML;
function shSay(text) { document.getElementById('shell-live').textContent = text; }

// Panels: visibility, sizes and the workspace, remembered in this browser.
// Storage may throw (blocked, private mode): the page then keeps its defaults.
const SH_KEY = 'praukron-panels';
const SH_PANELS = ['waiting', 'side', 'bottom'];
const SH_SHOWN = { waiting: 'Waiting strip shown.', side: 'Side panel shown.', bottom: 'Bottom panel shown.' };
const SH_HIDDEN = { waiting: 'Waiting strip hidden. Its count stays on the handle.', side: 'Side panel hidden.', bottom: 'Bottom panel hidden.' };
function shLoad() { try { return JSON.parse(localStorage.getItem(SH_KEY) || 'null'); } catch (error) { return null; } }
function shStore() { try { localStorage.setItem(SH_KEY, JSON.stringify(shPanels)); } catch (error) {} }
const shPanels = (() => {
  const base = { waiting: true, side: true, bottom: false, pin: false, sideW: 380, bottomH: 260, before: null, tab: null, view: 'lanes' };
  const saved = shLoad();
  if (saved && typeof saved === 'object') Object.keys(base).forEach(key => { if (key in saved) base[key] = saved[key]; });
  return base;
})();
function shRemember(key, value) { shPanels[key] = value; shStore(); }
// The view Work shows: lanes, or force. Both draw the same nodes and lines.
const SH_VIEWS = ['lanes', 'force'];
shState.view = SH_VIEWS.includes(shPanels.view) ? shPanels.view : 'lanes';
shState.tfs = {};
function shClamp() {
  shPanels.sideW = Math.round(Math.max(260, Math.min(Number(shPanels.sideW) || 380, Math.max(260, innerWidth - 420))));
  shPanels.bottomH = Math.round(Math.max(120, Math.min(Number(shPanels.bottomH) || 260, Math.max(120, innerHeight - 300))));
}
function shApply() {
  shClamp();
  shRoot.style.setProperty('--side-w', shPanels.sideW + 'px');
  shRoot.style.setProperty('--bottom-h', shPanels.bottomH + 'px');
  SH_PANELS.forEach(panel => {
    shRoot.classList.toggle('hide-' + panel, !shPanels[panel]);
    document.querySelectorAll('[data-toggle="' + panel + '"]').forEach(control =>
      control.setAttribute('aria-expanded', String(Boolean(shPanels[panel]))));
  });
  document.getElementById('side-pin').setAttribute('aria-pressed', String(Boolean(shPanels.pin)));
  const all = SH_PANELS.every(panel => !shPanels[panel]);
  const focus = document.getElementById('focus-btn');
  focus.setAttribute('aria-pressed', String(all));
  focus.textContent = all ? 'Show panels' : 'Focus';
  shLayout();
}
function shSet(panel, on, quiet) {
  if (Boolean(shPanels[panel]) === Boolean(on)) return;
  shPanels[panel] = Boolean(on);
  if (on) shPanels.before = null;
  shApply(); shStore();
  if (!quiet) shSay(on ? SH_SHOWN[panel] : SH_HIDDEN[panel]);
}
function shToggle(panel) {
  const on = !shPanels[panel];
  const active = document.activeElement;
  shSet(panel, on);
  // Keep the keyboard where it was useful: on hide the handle, on show the panel's own toggle.
  const target = document.querySelector(on ? '[data-toggle="' + panel + '"]:not(.handle)' : '.handle[data-toggle="' + panel + '"]');
  if (target && active && active.closest && active.closest('[data-toggle]')) target.focus();
}
function shFocusMode() {
  if (SH_PANELS.every(panel => !shPanels[panel])) {
    const before = shPanels.before || { waiting: true, side: true, bottom: false };
    SH_PANELS.forEach(panel => { shPanels[panel] = Boolean(before[panel]); });
    if (SH_PANELS.every(panel => !shPanels[panel])) { shPanels.waiting = true; shPanels.side = true; }
    shPanels.before = null;
    shSay('Panels back.');
  } else {
    shPanels.before = { waiting: shPanels.waiting, side: shPanels.side, bottom: shPanels.bottom };
    SH_PANELS.forEach(panel => { shPanels[panel] = false; });
    shSay('Focus: every panel is hidden. The waiting count stays on its handle. Press f to bring the panels back.');
  }
  shApply(); shStore();
}
document.querySelectorAll('[data-toggle]').forEach(control =>
  control.addEventListener('click', () => shToggle(control.dataset.toggle)));
document.getElementById('focus-btn').addEventListener('click', shFocusMode);
document.getElementById('side-pin').addEventListener('click', () => {
  shPanels.pin = !shPanels.pin; shApply(); shStore();
  shSay(shPanels.pin ? 'The side panel stays hidden when you select.' : 'Selecting shows the side panel.');
});
document.querySelectorAll('.resizer').forEach(handle => {
  const which = handle.dataset.resize;
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    handle.classList.add('dragging'); shRoot.classList.add('resizing');
    const x0 = event.clientX, y0 = event.clientY, w0 = shPanels.sideW, h0 = shPanels.bottomH;
    const move = e => { if (which === 'side') shPanels.sideW = w0 + (x0 - e.clientX); else shPanels.bottomH = h0 + (y0 - e.clientY); shApply(); };
    const up = () => { removeEventListener('pointermove', move); handle.classList.remove('dragging'); shRoot.classList.remove('resizing'); shStore(); };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up, { once: true });
    addEventListener('pointercancel', up, { once: true });
  });
  handle.addEventListener('keydown', event => {
    const grow = which === 'side' ? { ArrowLeft: 1, ArrowRight: -1 } : { ArrowUp: 1, ArrowDown: -1 };
    if (!(event.key in grow)) return;
    event.preventDefault();
    const step = (event.shiftKey ? 80 : 20) * grow[event.key];
    if (which === 'side') shPanels.sideW += step; else shPanels.bottomH += step;
    shApply(); shStore();
  });
});
addEventListener('resize', () => shApply());

// The bottom panel's tabs.
const shTabs = [...document.querySelectorAll('.btablist [role="tab"]')];
function shShowTab(tab, focus) {
  shTabs.forEach(other => {
    const on = other === tab;
    other.setAttribute('aria-selected', String(on));
    other.tabIndex = on ? 0 : -1;
    document.getElementById(other.getAttribute('aria-controls')).classList.toggle('is-selected', on);
  });
  if (focus) tab.focus();
  shRenderLists();
}
shTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => shShowTab(tab));
  tab.addEventListener('keydown', event => {
    const next = event.key === 'ArrowRight' ? (index + 1) % shTabs.length : event.key === 'ArrowLeft'
      ? (index + shTabs.length - 1) % shTabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? shTabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    shShowTab(shTabs[next], true);
  });
});
function shOpenList(name) {
  shSet('bottom', true, true);
  shShowTab(document.getElementById(name + '-tab'), true);
}
document.querySelector('.skip').addEventListener('click', event => { event.preventDefault(); shOpenList('text'); });

// --- One selection for every workspace --------------------------------
function shKind(id) {
  return id && byId[id] ? 'task' : id && decisionById[id] ? 'decision' : id && shAssumption[id] ? 'assumption' : null;
}
function shCode(id) { return '<code>' + esc(id) + '</code>'; }
function shTaskList(ids) {
  return ids.length ? '<ul class="plain">' + ids.map(id => '<li><a href="#task-record-' + esc(id) + '" class="tasklink" data-task="'
    + esc(id) + '">' + shCode(id) + ' ' + esc(byId[id] ? protectedText(byId[id].title) : '') + '</a> '
    + (byId[id] ? pill(byId[id].status) : '') + '</li>').join('') + '</ul>' : '<p class="note">None</p>';
}
function shRecordLink(anchor) {
  return document.getElementById(anchor)
    ? '<p><a href="#' + esc(anchor) + '" data-record="' + esc(anchor) + '">Open the full record</a></p>' : '';
}
function shWaitBox(w) {
  if (!w) return '';
  return '<div class="waitbox" role="note"><p><strong><span class="wglyph" aria-hidden="true">!</span> Waiting for you: '
    + esc(w.action) + '</strong> <span class="when ' + esc(w.category) + '">' + (w.category === 'now' ? 'Now' : 'Upcoming')
    + '</span></p><p>' + esc(w.why) + '</p><p>' + esc(w.count) + w.marks.map(m => ' · ' + esc(m)).join('') + '</p><p>'
    + esc(w.sinceText) + '</p><p>' + (w.waitsOn.length ? 'Waits on: ' + w.waitsOn.map(shCode).join(', ')
    : 'Waits on nothing: it can be answered now.') + '</p></div>';
}
function shDelegateBox(task) {
  const delegated = (SHELL.verifications[task.id] || []).filter(v => v.kind === 'delegate');
  if (task.validation !== 'DELEGATE_VERIFIED' && !delegated.length) return '';
  return '<div class="delegatebox" role="note"><p><strong>DELEGATE_VERIFIED</strong>, not HUMAN_VERIFIED: a delegate verified it on the owner’s authority.</p>'
    + (delegated.length ? delegated.map(v => '<p>Delegate ' + esc(protectedText(v.delegate)) + ' · on the authority of '
      + esc(protectedText(v.onAuthorityOf)) + ' · ' + shCode(v.grant) + ' · ' + shCode(v.response) + ' · ' + esc(v.date) + '</p>').join('')
      : '<p>No delegated response names a grant.</p>') + '</div>';
}
function shRenderSide() {
  const body = document.getElementById('side-body');
  const id = shState.sel, kind = shKind(id);
  let markup = '';
  if (kind === 'task') {
    const task = byId[id];
    markup = '<nav class="crumbs" aria-label="Lineage">' + shCode(task.phase) + ' › ' + shCode(task.module || '—') + ' › ' + shCode(id) + '</nav>'
      + taskDetailHtml(id).replace('<div class="detail-code">', shWaitBox(shWait[id]) + shDelegateBox(task) + '<div class="detail-code">');
  } else if (kind === 'decision') {
    const decision = decisionById[id];
    const governed = [...new Set([...decision.affects.filter(t => byId[t]), ...DATA.tasks.filter(t => t.decisions.includes(id)).map(t => t.id)])].sort();
    markup = '<h2 id="side-h">' + shCode(id) + ' ' + esc(protectedText(decision.title)) + '</h2><p>' + pill(decision.status)
      + (decision.origin === 'RECONSTRUCTED' ? ' ' + pill('RECONSTRUCTED') : '') + '</p><h3>Governs</h3>' + shTaskList(governed)
      + shRecordLink('decision-' + id);
  } else if (kind === 'assumption') {
    const a = shAssumption[id];
    markup = '<h2 id="side-h">' + shCode(id) + ' ' + esc(protectedText(a.title)) + '</h2><p>' + pill(a.status) + ' ' + pill(a.impact) + '</p>'
      + shWaitBox(shWait[id]) + '<h3>Assumption</h3><p>' + esc(protectedText(a.assumption)) + '</p><h3>Basis</h3><p>'
      + esc(protectedText(a.basis)) + '</p><h3>Relied on by</h3>' + shTaskList(a.tasks.filter(t => byId[t]))
      + shRecordLink('assumption-' + id);
  }
  if (!kind) { body.innerHTML = shSideEmpty; return; }
  body.innerHTML = markup + '<p><button type="button" class="edge-btn" data-lanes-show="' + esc(id) + '">Show in the graph</button></p>';
  if (kind === 'task') fillTaskCode(body.querySelector('.detail-code'), id);
}
function shSelect(id, options = {}) {
  if (id && !shKind(id)) return;
  shState.sel = id || null;
  if (id && !shPanels.pin) shSet('side', true, true);
  shRenderSide();
  lRefresh();
  if (id && shState.built && options.frame !== false && lVisible() && !lFrameFocus()) lCenter(id);
  document.querySelectorAll('[data-select]').forEach(el => el.setAttribute('aria-current', String(el.dataset.select === id)));
  document.querySelectorAll('#text-rows tr[data-node]').forEach(el => el.setAttribute('aria-current', String(el.dataset.node === id)));
  shSay(id ? 'Selected ' + id + '.' : 'Selection cleared.');
}
document.addEventListener('click', event => {
  const item = event.target.closest('[data-select]');
  if (item) { event.preventDefault(); shSelect(item.dataset.select); return; }
  const node = event.target.closest('#text-rows tr[data-node]');
  if (node && !event.target.closest('a, button')) { shSelect(node.dataset.node); return; }
  const lanes = event.target.closest('[data-lanes-show]');
  if (lanes) { event.preventDefault(); shOpenLanes(lanes.dataset.lanesShow); }
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest && event.target.closest('tr[data-select], #text-rows tr[data-node]');
  if (!row) return;
  event.preventDefault();
  shSelect(row.dataset.select || row.dataset.node);
});
document.getElementById('waiting-only').addEventListener('click', event => {
  shState.waitOnly = !shState.waitOnly;
  event.currentTarget.setAttribute('aria-pressed', String(shState.waitOnly));
  lRefresh(); shRenderLists();
  if (shState.built && lVisible()) lFit();
});

// --- The lanes view ---------------------------------------------------
const L = { nodes: new Map(), edges: [], outE: new Map(), inE: new Map(), keys: new Set() };
DATA.tasks.forEach(t => lAdd(t.id, 'task', t));
DATA.decisions.forEach(d => lAdd(d.id, 'decision', d));
DATA.assumptions.forEach(a => lAdd(a.id, 'assumption', a));
// Each node keeps one position per view; `pos` is the one the open view draws.
function lAdd(id, kind, rec) {
  const pos = SHELL.lanes.positions[id];
  if (pos) L.nodes.set(id, { id, kind, rec, pos, at: { lanes: pos, force: null } });
}
function lEdge(s, t, k) {
  const key = s + '>' + t + '>' + k;
  if (!L.nodes.has(s) || !L.nodes.has(t) || L.keys.has(key)) return;
  L.keys.add(key);
  const e = { s, t, k };
  L.edges.push(e);
  if (!L.outE.has(s)) L.outE.set(s, []);
  if (!L.inE.has(t)) L.inE.set(t, []);
  L.outE.get(s).push(e); L.inE.get(t).push(e);
}
DATA.tasks.forEach(t => t.deps.forEach(d => lEdge(d, t.id, 'dep')));
DATA.tasks.forEach(t => t.decisions.forEach(d => lEdge(d, t.id, 'gov')));
DATA.decisions.forEach(d => d.affects.forEach(t => lEdge(d.id, t, 'gov')));
DATA.assumptions.forEach(a => a.tasks.forEach(t => lEdge(a.id, t, 'asm')));
const lOut = (id, k) => (L.outE.get(id) || []).filter(e => !k || e.k === k);
const lIn = (id, k) => (L.inE.get(id) || []).filter(e => !k || e.k === k);
const shWaitRelated = new Set();
SHELL.waiting.items.forEach(w => { shWaitRelated.add(w.id); w.tasks.forEach(t => shWaitRelated.add(t)); w.waitsOn.forEach(t => shWaitRelated.add(t)); });
const lSvg = document.getElementById('lanes-svg'), lWorld = document.getElementById('lanes-world');
const lStage = document.getElementById('stage'), lTip = document.getElementById('lanes-tip');
const lCheck = id => document.getElementById(id).getAttribute('aria-pressed') === 'true';
const lKindShown = n => n.kind === 'task' || lCheck(n.kind === 'decision' ? 'lanes-decisions' : 'lanes-assumptions');
let lPassed = new Map();

// The same filters as every workspace: a task passes the shared cross-filters;
// a decision or an assumption passes while a task it bears on does.
function lComputePass() {
  lPassed = new Map();
  const active = filtering() || shState.waitOnly;
  L.nodes.forEach(n => { if (n.kind === 'task') lPassed.set(n.id, passes(n.rec) && (!shState.waitOnly || shWaitRelated.has(n.id))); });
  L.nodes.forEach(n => {
    if (n.kind === 'task') return;
    lPassed.set(n.id, !active || lOut(n.id).some(e => lPassed.get(e.t)) || (shState.waitOnly && Boolean(shWait[n.id])));
  });
}
function lChain(id) {
  const n = L.nodes.get(id);
  if (!n) return null;
  const F = new Set([id]), E = new Set(), deep = lCheck('lanes-chain');
  const walk = up => {
    const stack = [id], seen = new Set([id]);
    while (stack.length) {
      const x = stack.pop();
      for (const e of (up ? lIn(x, 'dep') : lOut(x, 'dep'))) {
        const y = up ? e.s : e.t; E.add(e); F.add(y);
        if (deep && !seen.has(y)) { seen.add(y); stack.push(y); }
      }
    }
  };
  if (n.kind === 'task') { walk(true); walk(false); lIn(id).forEach(e => { if (e.k !== 'dep') { E.add(e); F.add(e.s); } }); }
  else lOut(id).forEach(e => { E.add(e); F.add(e.t); });
  const w = shWait[id];
  if (w) {
    const extra = new Set([...w.tasks, ...w.waitsOn]);
    extra.forEach(x => { if (L.nodes.has(x)) F.add(x); });
    L.edges.forEach(e => { if (e.k === 'dep' && F.has(e.s) && extra.has(e.t)) E.add(e); });
  }
  return { F, E };
}
function lEl(tag, attrs, parent) {
  const el = document.createElementNS(SH_SVG, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  if (parent) parent.appendChild(el);
  return el;
}
function lText(parent, x, y, cls, text) { const t = lEl('text', { x, y, class: cls }, parent); t.textContent = text; return t; }
function lShape(n, g) {
  if (n.kind === 'task') {
    lEl('circle', { r: 6, class: 'shape' }, g);
    if (n.rec.status === 'WIP') lEl('circle', { r: 2.2, class: 'dot' }, g);
    if (n.rec.status === 'BLOCKED') lEl('path', { d: 'M-4,-4L4,4M4,-4L-4,4', class: 'x' }, g);
    const mark = n.rec.validation === 'HUMAN_VERIFIED' ? 'H' : n.rec.validation === 'DELEGATE_VERIFIED' ? 'D' : '';
    if (mark) lText(g, 7, -5, 'verif', mark);
    lText(g, 9, 4, 'opt', n.id.replace(/^T-/, ''));
  } else if (n.kind === 'decision') {
    lEl('rect', { x: -4.5, y: -4.5, width: 9, height: 9, class: 'shape' }, g);
    lText(g, 7, 4, 'opt', n.id);
  } else {
    lEl('path', { d: 'M0,-6L6,5L-6,5Z', class: 'shape' }, g);
    lText(g, 8, 4, 'opt', n.id);
  }
  if (shWait[n.id]) { lEl('path', { d: 'M0,-12L12,0L0,12L-12,0Z', class: 'waitring' }, g); lText(g, -4, -14, '', '! ' + n.id); }
}
function lEdgePath(a, b) {
  const dx = Math.max(30, Math.abs(b[0] - a[0]) / 2);
  return 'M' + a[0] + ',' + a[1] + 'C' + (a[0] + dx) + ',' + a[1] + ' ' + (b[0] - dx) + ',' + b[1] + ' ' + b[0] + ',' + b[1];
}
function lBuild() {
  const decor = document.getElementById('lanes-decor'), lines = document.getElementById('graph-lines');
  const shapes = document.getElementById('graph-shapes');
  [decor, lines, shapes].forEach(g => { g.textContent = ''; });
  SHELL.lanes.bands.forEach(b => {
    lEl('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 6, class: 'l-band' }, decor);
    lText(decor, b.x + 8, b.y + 15, 'l-band-label', b.id === '~none' ? 'No module' : b.id + (b.name ? ' · ' + protectedText(b.name) : ''));
  });
  SHELL.lanes.columns.forEach(c => {
    lText(decor, c.x, 26, 'l-col-label', c.id + ' · ' + protectedText(c.name));
    lText(decor, c.x, 44, 'l-col-sub', c.status + ' · ' + '{done} of {tasks} tasks done'.replace('{done}', c.done).replace('{tasks}', c.tasks));
  });
  const labels = { decisions: 'Decisions, placed under the tasks they govern', assumptions: 'Assumptions, placed under the tasks that rely on them' };
  SHELL.lanes.rows.forEach(r => {
    lEl('rect', { x: r.x, y: r.y, width: r.w, height: r.h, rx: 6, class: 'l-band' }, decor);
    lText(decor, r.x + 8, r.y + 15, 'l-row-label', labels[r.id]);
  });
  L.edges.forEach(e => {
    e.el = lEl('path', { d: lEdgePath(L.nodes.get(e.s).pos, L.nodes.get(e.t).pos), class: 'l-edge ' + e.k }, lines);
    if (e.k === 'dep') e.el.setAttribute('marker-end', 'url(#lanes-arrow)');
  });
  L.nodes.forEach(n => {
    const status = n.rec.status || '';
    const g = lEl('g', { class: 'l-node ' + n.kind + ' ' + status + (n.rec.ready ? ' ready' : '') + (shWait[n.id] ? ' wait' : ''),
                         transform: 'translate(' + n.pos[0] + ',' + n.pos[1] + ')', 'data-id': n.id }, shapes);
    lShape(n, g); n.el = g;
  });
  shState.built = true;
  lRefresh();
}
// Lines follow the open view: curves left to right in the lanes, straight in
// force, each ending short of its node so the arrowhead stays visible.
function fEdgePath(a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], d = Math.sqrt(dx * dx + dy * dy) || 1, cut = Math.min(9, d / 2);
  return 'M' + a[0] + ',' + a[1] + 'L' + Math.round((b[0] - dx / d * cut) * 10) / 10 + ',' + Math.round((b[1] - dy / d * cut) * 10) / 10;
}
function lPlace() {
  const path = shState.view === 'force' ? fEdgePath : lEdgePath;
  L.nodes.forEach(n => n.el.setAttribute('transform', 'translate(' + n.pos[0] + ',' + n.pos[1] + ')'));
  L.edges.forEach(e => e.el.setAttribute('d', path(L.nodes.get(e.s).pos, L.nodes.get(e.t).pos)));
}
function lRefresh() {
  if (!shState.built) return;
  lComputePass();
  const focus = shState.sel ? lChain(shState.sel) : null;
  shState.focus = focus;
  const governs = lCheck('lanes-governs');
  L.nodes.forEach(n => {
    const gone = !lKindShown(n), dim = !lPassed.get(n.id) || Boolean(focus && !focus.F.has(n.id));
    n.gone = gone; n.dim = dim;
    n.el.classList.toggle('l-gone', gone);
    n.el.classList.toggle('l-dim', dim);
    n.el.classList.toggle('hl', Boolean(focus && focus.F.has(n.id) && n.id !== shState.sel));
    n.el.classList.toggle('sel', n.id === shState.sel);
  });
  if (shState.view === 'force') fRefreshClusters();
  L.edges.forEach(e => {
    const a = L.nodes.get(e.s), b = L.nodes.get(e.t), inFocus = Boolean(focus && focus.E.has(e));
    const shown = e.k === 'dep' || governs || inFocus;
    e.el.classList.toggle('l-gone', a.gone || b.gone || !shown);
    e.el.classList.toggle('l-dim', !inFocus && (a.dim || b.dim));
    e.el.classList.toggle('hl', inFocus);
    e.el.classList.toggle('waitpath', shState.waitOnly && !focus && e.k === 'dep' && shWaitRelated.has(e.s) && shWaitRelated.has(e.t));
  });
}
function lVisible() { const r = lStage.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
function lApply(mode) {
  if (mode !== undefined) shState.fit = mode;
  const { x, y, k } = shState.tf;
  lWorld.setAttribute('transform', 'translate(' + x + ',' + y + ') scale(' + k + ')');
  lSvg.classList.toggle('near', k > 0.8);
}
function lBox(test) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  L.nodes.forEach(n => {
    if (test && !test(n)) return;
    x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1]); x1 = Math.max(x1, n.pos[0] + 40); y1 = Math.max(y1, n.pos[1]);
  });
  return [x0, y0, x1, y1];
}
function lFrame(box, mode, maxZoom, pad) {
  const r = lStage.getBoundingClientRect();
  const [x0, y0, x1, y1] = box;
  if (!isFinite(x0) || !r.width) return false;
  const k = Math.max(0.08, Math.min(maxZoom, r.width / (x1 - x0 + pad), r.height / (y1 - y0 + pad)));
  shState.tf = { k, x: (r.width - (x1 + x0) * k) / 2, y: (r.height - (y1 + y0) * k) / 2 };
  lApply(mode);
  return true;
}
function lFit() { lFrame(lBox(n => !n.gone && lPassed.get(n.id) !== false), 'all', 2, 60); }
// The current phase: its column, from its label down to its last band.
function lFitPhase(phase) {
  if (shState.view === 'force') {
    shState.fitPhase = phase;
    const shown = n => !n.gone && lPassed.get(n.id) !== false;
    return lFrame(lBox(n => shown(n) && n.kind === 'task' && n.rec.phase === phase), 'phase', 1.3, 80) || lFit();
  }
  const column = SHELL.lanes.columns.find(c => c.id === phase);
  if (!column) return lFit();
  const bands = SHELL.lanes.bands.filter(b => b.phase === phase);
  const bottom = Math.max(80, ...bands.map(b => b.y + b.h));
  const r = lStage.getBoundingClientRect();
  if (!r.width) return;
  const k = Math.max(0.08, Math.min(1.1, r.width / (column.w + 80), r.height / (bottom + 30)));
  shState.tf = { k, x: 30 - column.x * k, y: 10 };
  shState.fitPhase = phase;
  lApply('phase');
}
function lFrameFocus() {
  const focus = shState.focus;
  if (!focus || focus.F.size < 2) return false;
  const tasks = [...focus.F].some(id => L.nodes.get(id) && L.nodes.get(id).kind === 'task');
  return lFrame(lBox(n => focus.F.has(n.id) && !n.gone && (!tasks || n.kind === 'task')), 'focus', 1.3, 120);
}
function lCenter(id) {
  const n = L.nodes.get(id);
  if (!n) return;
  const r = lStage.getBoundingClientRect(), k = Math.max(shState.tf.k, 1);
  shState.tf = { k, x: r.width / 2 - n.pos[0] * k, y: r.height / 2 - n.pos[1] * k };
  lApply(null);
}
function lZoom(factor, cx, cy) {
  const r = lStage.getBoundingClientRect();
  const mx = cx === undefined ? r.width / 2 : cx, my = cy === undefined ? r.height / 2 : cy;
  const k = Math.max(0.08, Math.min(5, shState.tf.k * factor));
  shState.tf.x = mx - (mx - shState.tf.x) * k / shState.tf.k;
  shState.tf.y = my - (my - shState.tf.y) * k / shState.tf.k;
  shState.tf.k = k;
  lApply(null);
}
function lEnsure() {
  if (!lVisible()) return false;
  if (!shState.built) { lBuild(); shDrawView(); }
  else if (shState.pending) { shState.pending = false; shDrawView(); }
  return true;
}
function shOpenLanes(id, view) {
  selectTab('work');
  shLayout();
  if (view) shView(view);
  if (lEnsure() && id && shKind(id)) { shSelect(id); lFrameFocus() || lCenter(id); }
  lStage.focus();
}
let lDrag = null;
lStage.addEventListener('pointerdown', event => {
  if (event.button > 0) return;
  lDrag = { x: event.clientX, y: event.clientY, moved: false, tf: { ...shState.tf } };
});
// A drag pans from anywhere it goes; a press that does not move selects.
addEventListener('pointermove', event => {
  if (!lDrag) return;
  const dx = event.clientX - lDrag.x, dy = event.clientY - lDrag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) lDrag.moved = true;
  if (lDrag.moved) { shState.tf.x = lDrag.tf.x + dx; shState.tf.y = lDrag.tf.y + dy; lApply(null); }
});
addEventListener('pointerup', () => { if (lDrag && lDrag.moved) lDrag = null; });
lStage.addEventListener('pointermove', event => {
  if (lDrag) return;
  const g = event.target.closest && event.target.closest('.l-node');
  if (!g) { lTip.hidden = true; return; }
  const n = L.nodes.get(g.dataset.id), r = lStage.getBoundingClientRect(), w = shWait[n.id];
  lTip.innerHTML = shCode(n.id) + ' ' + esc(protectedText(n.rec.title)) + '<br>' + (n.rec.status ? pill(n.rec.status) : '')
    + (n.rec.validation ? ' ' + pill(n.rec.validation) : '') + (w ? '<br><strong>Waiting for you: ' + esc(w.action) + '</strong>' : '');
  lTip.hidden = false;
  lTip.style.left = Math.min(r.width - 340, event.clientX - r.left + 14) + 'px';
  lTip.style.top = (event.clientY - r.top + 14) + 'px';
});
lStage.addEventListener('pointerup', event => {
  if (lDrag && !lDrag.moved) {
    const g = event.target.closest && event.target.closest('.l-node');
    if (g) shSelect(g.dataset.id, { frame: false }); else if (event.target === lSvg) shSelect(null);
  }
  lDrag = null;
});
lStage.addEventListener('pointerleave', () => { lTip.hidden = true; });
lStage.addEventListener('wheel', event => {
  event.preventDefault();
  const r = lStage.getBoundingClientRect();
  lZoom(Math.pow(1.0015, -event.deltaY), event.clientX - r.left, event.clientY - r.top);
}, { passive: false });
// Up and down keep the lanes order in either view, so the keys mean the same.
function lSiblings(n) {
  const order = (a, b) => a.at.lanes[0] - b.at.lanes[0] || a.at.lanes[1] - b.at.lanes[1];
  if (n.kind !== 'task') return [...L.nodes.values()].filter(x => x.kind === n.kind && !x.gone).sort(order).map(x => x.id);
  return [...L.nodes.values()].filter(x => x.kind === 'task' && x.rec.module === n.rec.module && x.rec.phase === n.rec.phase)
    .sort(order).map(x => x.id);
}
lStage.addEventListener('keydown', event => {
  if (event.target !== lStage || event.metaKey || event.ctrlKey || event.altKey) return;
  const n = shState.sel && L.nodes.get(shState.sel);
  const go = id => { if (id) { event.preventDefault(); shSelect(id); } };
  const first = () => go((SHELL.waiting.items[0] || {}).id || (DATA.tasks[0] || {}).id);
  switch (event.key) {
    case 'ArrowLeft': if (!n) return first();
      return go(n.kind === 'task' ? n.rec.deps.find(d => L.nodes.has(d)) : (lOut(n.id)[0] || {}).t);
    case 'ArrowRight': if (!n) return first();
      return go(n.kind === 'task' ? n.rec.blocks.find(d => L.nodes.has(d)) : (lOut(n.id)[0] || {}).t);
    case 'ArrowUp': case 'ArrowDown': {
      if (!n) return first();
      const list = lSiblings(n), at = list.indexOf(n.id);
      return go(list[(at + (event.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length]);
    }
    case 'Enter': if (n) { event.preventDefault(); shSet('side', true, true); document.getElementById('side-body').focus(); } return;
    case '+': case '=': event.preventDefault(); return lZoom(1.25);
    case '-': event.preventDefault(); return lZoom(0.8);
    case '0': event.preventDefault(); return lFit();
  }
});
document.getElementById('lanes-fit').addEventListener('click', lFit);
document.getElementById('lanes-current').addEventListener('click', () => lFitPhase(SHELL.lanes.current));
['lanes-decisions', 'lanes-assumptions', 'lanes-governs', 'lanes-chain'].forEach(id =>
  document.getElementById(id).addEventListener('click', event => {
    event.currentTarget.setAttribute('aria-pressed', String(!lCheck(id)));
    lRefresh(); shRenderLists();
  }));

// --- The force layout: pure and seeded --------------------------------
// Two levels. Each module is a disc: its tasks are laid out inside it once,
// and the discs then settle by force, pulled together by the lines between
// their tasks and kept from overlapping. A decision or an assumption has no
// module: it settles among the tasks it bears on, so one shared across
// modules sits between them. Items and links are sorted first and the
// generator is seeded: the same input gives the same positions; nothing
// unseeded is drawn.
function fRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const F_ITER = 240, F_INNER = 120, F_GAP = 46;
function fSim(items, links) {
  const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const list = [...items].sort((a, b) => byText(a.id, b.id));
  const N = list.length, idx = new Map(list.map((n, i) => [n.id, i]));
  const P = new Float64Array(N * 2), rand = fRng(9);
  const names = [...new Set(list.map(n => n.cluster).filter(c => c != null))].sort(byText);
  const C = names.length, cIndex = new Map(names.map((c, i) => [c, i]));
  const cl = new Int32Array(N).fill(-1), members = names.map(() => []);
  list.forEach((n, i) => { if (n.cluster != null) { cl[i] = cIndex.get(n.cluster); members[cl[i]].push(i); } });
  const E = links.filter(l => idx.has(l.s) && idx.has(l.t) && l.s !== l.t)
    .map(l => ({ a: idx.get(l.s), b: idx.get(l.t), k: l.k }))
    .sort((x, y) => x.a - y.a || x.b - y.b || byText(x.k, y.k));
  const near = list.map(() => []);
  E.forEach(l => { near[l.a].push(l.b); near[l.b].push(l.a); });
  // Inside each disc: a sunflower start, then repulsion and the module's own
  // dependency lines, held within the disc. Offsets are kept per task.
  const OX = new Float64Array(N), OY = new Float64Array(N), R = new Float64Array(C);
  members.forEach((m, c) => {
    const k = m.length, r = 14 + Math.sqrt(k) * 17;
    R[c] = r + 12;
    m.forEach((i, j) => { const rr = r * Math.sqrt((j + 0.5) / k) * 0.9, a = j * 2.399963; OX[i] = Math.cos(a) * rr; OY[i] = Math.sin(a) * rr; });
    const own = E.filter(l => l.k === 'dep' && cl[l.a] === c && cl[l.b] === c);
    for (let it = 0; it < F_INNER; it++) {
      const alpha = 1 - it / F_INNER;
      for (let x = 0; x < k; x++) for (let y = x + 1; y < k; y++) {
        const i = m[x], j = m[y];
        let dx = OX[i] - OX[j], dy = OY[i] - OY[j], d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = rand() - 0.5; dy = rand() - 0.5; d2 = 0.5; }
        if (d2 > 2500) continue;
        const f = 120 * alpha / d2;
        OX[i] += dx * f; OY[i] += dy * f; OX[j] -= dx * f; OY[j] -= dy * f;
      }
      own.forEach(l => {
        const dx = OX[l.b] - OX[l.a], dy = OY[l.b] - OY[l.a], d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 30) / d * 0.08 * alpha;
        OX[l.a] += dx * f; OY[l.a] += dy * f; OX[l.b] -= dx * f; OY[l.b] -= dy * f;
      });
      m.forEach(i => { const d = Math.hypot(OX[i], OY[i]); if (d > r) { OX[i] *= r / d; OY[i] *= r / d; } });
    }
  });
  // Between discs: how many lines join two modules, directly or through one
  // decision or assumption they share.
  const W = new Map(), wkey = (c, d) => c < d ? c * C + d : d * C + c;
  const join = (c, d, w) => { if (c >= 0 && d >= 0 && c !== d) W.set(wkey(c, d), (W.get(wkey(c, d)) || 0) + w); };
  E.forEach(l => join(cl[l.a], cl[l.b], 1));
  for (let i = 0; i < N; i++) {
    if (cl[i] >= 0) continue;
    const cs = [...new Set(near[i].map(j => cl[j]).filter(c => c >= 0))].sort((x, y) => x - y);
    for (let x = 0; x < cs.length; x++) for (let y = x + 1; y < cs.length; y++) join(cs[x], cs[y], 0.3);
  }
  const pairs = [...W.entries()].sort((x, y) => x[0] - y[0]).map(([key, w]) => [Math.floor(key / C), key % C, w]);
  const X = new Float64Array(C), Y = new Float64Array(C), VX = new Float64Array(C), VY = new Float64Array(C);
  const meanR = C ? R.reduce((s, r) => s + r, 0) / C : 1;
  for (let c = 0; c < C; c++) { const r = meanR * 2.2 * Math.sqrt(c + 0.5), a = c * 2.399963; X[c] = Math.cos(a) * r; Y[c] = Math.sin(a) * r; }
  const free = [];
  for (let i = 0; i < N; i++) if (cl[i] < 0) free.push(i);
  const jitter = free.map(() => [rand() * Math.PI * 2, 10 + rand() * 18]);
  let it = 0;
  function place() {
    for (let i = 0; i < N; i++) if (cl[i] >= 0) { P[i * 2] = X[cl[i]] + OX[i]; P[i * 2 + 1] = Y[cl[i]] + OY[i]; }
    let x1 = 0, y1 = 0;
    for (let c = 0; c < C; c++) { x1 = Math.max(x1, Math.abs(X[c]) + R[c]); y1 = Math.max(y1, Math.abs(Y[c]) + R[c]); }
    free.forEach((i, f) => {
      const at = near[i].filter(j => cl[j] >= 0);
      const [a, r] = jitter[f];
      if (at.length) {
        P[i * 2] = at.reduce((s, j) => s + P[j * 2], 0) / at.length + Math.cos(a) * r;
        P[i * 2 + 1] = at.reduce((s, j) => s + P[j * 2 + 1], 0) / at.length + Math.sin(a) * r;
      } else { P[i * 2] = Math.cos(a) * (x1 + 60); P[i * 2 + 1] = Math.sin(a) * (y1 + 60); }
    });
  }
  function tick() {
    if (it >= F_ITER) return true;
    const alpha = 1 - it / F_ITER;
    for (let c = 0; c < C; c++) for (let d = c + 1; d < C; d++) {
      let dx = X[d] - X[c], dy = Y[d] - Y[c], dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 0.01) { dx = rand() - 0.5; dy = rand() - 0.5; dist = 0.5; }
      const gap = R[c] + R[d] + F_GAP;
      let f = -meanR * meanR * 0.4 * alpha / (dist * dist);
      if (dist < gap) f -= (gap - dist) * 0.25;
      VX[c] += dx / dist * f; VY[c] += dy / dist * f; VX[d] -= dx / dist * f; VY[d] -= dy / dist * f;
    }
    pairs.forEach(([c, d, w]) => {
      const dx = X[d] - X[c], dy = Y[d] - Y[c], dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = Math.max(0, dist - (R[c] + R[d] + F_GAP)) / dist * Math.min(1, w / 4) * 0.05 * alpha;
      VX[c] += dx * f; VY[c] += dy * f; VX[d] -= dx * f; VY[d] -= dy * f;
    });
    for (let c = 0; c < C; c++) {
      VX[c] -= X[c] * 0.006 * alpha; VY[c] -= Y[c] * 0.006 * alpha;
      X[c] += VX[c] * 0.5; Y[c] += VY[c] * 0.5; VX[c] *= 0.6; VY[c] *= 0.6;
    }
    // Near rest, discs that still overlap are moved apart directly.
    if (alpha < 0.25) for (let c = 0; c < C; c++) for (let d = c + 1; d < C; d++) {
      const dx = X[d] - X[c], dy = Y[d] - Y[c], dist = Math.sqrt(dx * dx + dy * dy) || 1, gap = R[c] + R[d] + F_GAP / 2;
      if (dist >= gap) continue;
      const m = (gap - dist) / dist / 2;
      X[c] -= dx * m; Y[c] -= dy * m; X[d] += dx * m; Y[d] += dy * m;
    }
    it += 1;
    return it >= F_ITER;
  }
  const out = target => {
    place();
    list.forEach((n, i) => {
      const p = target.get(n.id);
      if (p) { p[0] = Math.round(P[i * 2] * 10) / 10; p[1] = Math.round(P[i * 2 + 1] * 10) / 10; }
    });
  };
  return { tick, place: out, get done() { return it >= F_ITER; }, get ticks() { return it; } };
}

// --- The force view: a second renderer over the lanes' nodes and lines --
// Nothing is laid out before the view first opens. Then the layout runs a
// slice per frame, so the page stays usable, and ends by itself; a reader who
// prefers reduced motion gets it computed to rest and drawn once.
const FV = { sim: null, at: null, ms: null, compute: 0, frames: 0, clusters: [] };
const fDecor = document.getElementById('force-decor'), fStatus = document.getElementById('force-status');
const shReduced = () => Boolean(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const fModuleName = Object.fromEntries(SHELL.lanes.bands.map(b => [b.id, b.name]));
function fStart() {
  if (FV.sim) return;
  const t0 = performance.now();
  FV.at = new Map();
  L.nodes.forEach(n => { n.at.force = [0, 0]; FV.at.set(n.id, n.at.force); n.pos = n.at.force; });
  FV.sim = fSim([...L.nodes.values()].map(n => ({ id: n.id, cluster: n.kind === 'task' ? (n.rec.module || '~none') : null })), L.edges);
  // A slice runs for at most `budget` milliseconds and `most` steps, so even a
  // small graph moves over a few frames before it rests.
  const slice = (budget, most) => {
    const s = performance.now();
    let done = FV.sim.done, steps = 0;
    while (!done) { done = FV.sim.tick(); steps += 1; if (steps >= most || performance.now() - s > budget) break; }
    FV.compute += performance.now() - s;
    FV.sim.place(FV.at);
    return done;
  };
  const finish = () => {
    FV.ms = Math.round(performance.now() - t0);
    lStage.dataset.settledMs = String(FV.ms);
    lStage.dataset.computeMs = String(Math.round(FV.compute));
    fStatus.hidden = true;
    fDraw();
  };
  if (shReduced()) { slice(Infinity, Infinity); finish(); return; }
  fStatus.hidden = false;
  FV.sim.place(FV.at);
  fDraw();
  // When the browser takes long between frames (a large graph to paint),
  // animating would hold the page: the layout then finishes at once and is
  // drawn once more, at rest.
  // The first frame also paints the switch of view, so it is not counted.
  let before = performance.now(), seen = 0;
  const frame = () => {
    const now = performance.now(), heavy = seen++ > 0 && now - before > 60;
    const done = heavy ? slice(Infinity, Infinity) : slice(12, 16);
    before = performance.now();
    if (done) finish(); else { fDraw(); requestAnimationFrame(frame); }
  };
  requestAnimationFrame(frame);
}
function fDraw() {
  FV.frames += 1;
  if (shState.view !== 'force') return;
  lPlace();
  fClusters();
  fRefreshClusters();
  if (shState.fit === 'all') lFit();
  else if (shState.fit === 'focus') lFrameFocus();
  else if (shState.fit === 'phase') lFitPhase(shState.fitPhase);
}
// One named circle per module, around its tasks.
function fClusters() {
  const groups = new Map();
  L.nodes.forEach(n => {
    if (n.kind !== 'task') return;
    const key = n.rec.module || '~none';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  });
  if (FV.clusters.length !== groups.size) {
    fDecor.textContent = '';
    FV.clusters = [...groups.keys()].sort().map(id => ({
      id, members: groups.get(id), ring: lEl('circle', { class: 'f-cluster' }, fDecor),
      label: lText(fDecor, 0, 0, 'f-label', id === '~none' ? 'No module' : id + (fModuleName[id] ? ' · ' + protectedText(fModuleName[id]) : '')),
    }));
    FV.clusters.forEach(c => c.label.setAttribute('text-anchor', 'middle'));
  }
  FV.clusters.forEach(c => {
    const xs = c.members.map(n => n.pos[0]), ys = c.members.map(n => n.pos[1]);
    const x = xs.reduce((a, b) => a + b, 0) / xs.length, y = ys.reduce((a, b) => a + b, 0) / ys.length;
    const r = Math.max(...c.members.map(n => Math.hypot(n.pos[0] - x, n.pos[1] - y))) + 16;
    c.x = Math.round(x); c.y = Math.round(y); c.r = Math.round(r);
    c.ring.setAttribute('cx', c.x); c.ring.setAttribute('cy', c.y); c.ring.setAttribute('r', c.r);
    c.label.setAttribute('x', c.x); c.label.setAttribute('y', c.y - c.r - 5);
  });
}
// A module's circle fades with its tasks, as the lanes fade a band's nodes.
function fRefreshClusters() {
  FV.clusters.forEach(c => {
    const dim = c.members.every(n => n.dim || n.gone);
    c.ring.classList.toggle('l-dim', dim); c.label.classList.toggle('l-dim', dim);
  });
}
// Draw the open view on the built graph: positions, lines, decor, framing.
function shDrawView() {
  const view = shState.view, force = view === 'force';
  if (force) fStart();
  L.nodes.forEach(n => { n.pos = n.at[view]; });
  document.getElementById('lanes-decor').setAttribute('display', force ? 'none' : 'inline');
  fDecor.setAttribute('display', force ? 'inline' : 'none');
  lPlace();
  if (force) fClusters();
  lRefresh();
  const saved = shState.tfs[view];
  if (shState.sel && lFrameFocus()) return;
  if (saved) { shState.tf = { ...saved.tf }; shState.fitPhase = saved.fitPhase; lApply(saved.fit); if (saved.fit === 'all') lFit(); return; }
  if (force) lFit(); else lFitPhase(SHELL.lanes.current);
}
function shView(name) {
  if (!SH_VIEWS.includes(name)) return;
  const was = shState.view;
  if (shState.built) shState.tfs[was] = { tf: { ...shState.tf }, fit: shState.fit, fitPhase: shState.fitPhase };
  shState.view = name;
  shRemember('view', name);
  shViewControls();
  if (!shState.built || !lVisible()) { if (shState.built) shState.pending = true; return; }
  shState.pending = false;
  if (was !== name) { shDrawView(); shSay(name === 'force' ? 'Force view: tasks clustered by module.' : 'Lanes view.'); }
  shRenderLists();
}
function shViewControls() {
  document.querySelectorAll('[data-view]').forEach(b => { if (b !== lStage) b.setAttribute('aria-pressed', String(b.dataset.view === shState.view)); });
  lStage.dataset.view = shState.view;
  lStage.setAttribute('aria-label', shState.view === 'force' ? 'Force graph' : 'Lanes graph');
}
document.querySelectorAll('.viewswitch [data-view]').forEach(b => b.addEventListener('click', () => shView(b.dataset.view)));

// The workspace scrolls in the middle; the lanes take its height, so the
// graph takes the space a hidden panel frees. A resize repeats the framing.
function shLayout() {
  const main = document.getElementById('main'), head = document.querySelector('.stickyhead');
  const height = main.clientHeight - (head ? head.offsetHeight : 0) - 24;
  shRoot.style.setProperty('--lanes-h', Math.max(320, height) + 'px');
}
if (typeof ResizeObserver === 'function') {
  let queued = false;
  new ResizeObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (!lEnsure()) return;
      const r = lStage.getBoundingClientRect(), was = shState.size;
      shState.size = { w: r.width, h: r.height };
      if (!was || (was.w === r.width && was.h === r.height)) return;
      if (shState.fit === 'all') lFit();
      else if (shState.fit === 'phase') lFitPhase(shState.fitPhase);
      else if (!(shState.fit === 'focus' && lFrameFocus())) {
        shState.tf.x += (r.width - was.w) / 2; shState.tf.y += (r.height - was.h) / 2; lApply(null);
      }
    });
  }).observe(lStage);
  new ResizeObserver(() => shLayout()).observe(document.getElementById('main'));
}

// --- Text view and relations: the graph as words, under the same filters
function shListed() {
  if (!shState.built) lComputePass();
  const order = { task: 0, decision: 1, assumption: 2 };
  return [...L.nodes.values()].filter(n => lKindShown(n) && lPassed.get(n.id) !== false).sort((a, b) =>
    order[a.kind] - order[b.kind] || String(a.rec.phase || '').localeCompare(String(b.rec.phase || ''))
    || String(a.rec.module || '').localeCompare(String(b.rec.module || '')) || a.id.localeCompare(b.id));
}
const SH_KIND = { task: 'Task', decision: 'Decision', assumption: 'Assumption' };
function shRenderText() {
  const rows = shListed();
  document.getElementById('text-count').textContent = rows.length;
  document.getElementById('text-rows').innerHTML = rows.map(n => {
    const t = n.rec, w = shWait[n.id];
    return '<tr tabindex="0" data-node="' + esc(n.id) + '" aria-current="' + String(n.id === shState.sel) + '"><td>' + shCode(n.id) + '</td><td>'
      + SH_KIND[n.kind] + '</td><td>' + esc(protectedText(t.title)) + '</td><td>' + (t.phase ? shCode(t.phase) : '') + '</td><td>'
      + (t.module ? shCode(t.module) : '') + '</td><td>' + (t.status ? pill(t.status) + (t.ready && t.status !== 'DONE' ? ' ready' : '') : '')
      + '</td><td>' + (t.validation ? pill(t.validation) : '') + '</td><td>' + (w ? 'Waiting for you: ' + esc(w.action) : '') + '</td><td>'
      + (n.kind === 'task' ? t.deps.map(shCode).join(' ') : '') + '</td><td>'
      + lOut(n.id).map(e => shCode(e.t)).join(' ') + '</td></tr>';
  }).join('');
}
const SH_RELATION = { dep: ' depends on ', gov: ' governs ', asm: ' is relied on by ' };
function shSentence(e) {
  return e.k === 'dep' ? shCode(e.t) + ' depends on ' + shCode(e.s) : shCode(e.s) + SH_RELATION[e.k] + shCode(e.t);
}
function shRenderRelations() {
  const focus = shState.sel ? lChain(shState.sel) : null;
  const shown = new Set(shListed().map(n => n.id));
  const lines = focus ? [...focus.E] : L.edges.filter(e => shown.has(e.s) && shown.has(e.t) && (e.k === 'dep' || lCheck('lanes-governs')));
  document.getElementById('rel-note').textContent = focus
    ? 'The lines of the selection’s chain, as the graph highlights them: ' + lines.length + '.'
    : 'Every line the graph draws under the filters: ' + lines.length + '.';
  document.getElementById('rel-rows').innerHTML = lines.map(e => '<li>' + shSentence(e) + '.</li>').join('');
}
function shRenderLists() {
  const open = document.querySelector('.btablist [aria-selected="true"]');
  if (!open || !shPanels.bottom) return;
  if (open.id === 'text-tab') shRenderText();
  if (open.id === 'rel-tab') shRenderRelations();
}
// The page's cross-filters call this after every change (applyFilters).
function shFiltersChanged() { lRefresh(); shRenderLists(); if (shState.built && lVisible() && filtering()) lFit(); }

// --- Keys, outside text fields ------------------------------------------
const shHelp = document.getElementById('shell-help');
document.getElementById('keys-btn').addEventListener('click', () => shHelp.showModal());
document.getElementById('shell-help-close').addEventListener('click', () => shHelp.close());
function shNextWaiting() {
  const items = SHELL.waiting.items;
  if (!items.length) return;
  const at = items.findIndex(w => w.id === shState.sel);
  shSelect(items[(at + 1) % items.length].id);
}
document.addEventListener('keydown', event => {
  const target = event.target;
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
  if (target.isContentEditable || (target.closest && target.closest('input, textarea, select'))) return;
  if (document.querySelector('dialog[open]')) return;
  const keys = {
    s: () => shToggle('side'), b: () => shToggle('bottom'), W: () => shToggle('waiting'), f: shFocusMode,
    '1': () => shOpenLanes(shState.sel, 'lanes'), '2': () => shOpenLanes(shState.sel, 'force'),
    w: shNextWaiting, '?': () => shHelp.showModal(),
    Escape: () => { if (!shState.sel) return false; shSelect(null); },
  };
  const run = keys[event.key];
  if (!run || run() === false) return;
  event.preventDefault();
});

// --- Start ---------------------------------------------------------------
shApply();
shViewControls();
window.shReady = true;
lEnsure();
"""
