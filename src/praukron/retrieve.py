"""INDEX-guided retrieval (ADR-048).

A question is routed through the index, never through a search of every file:
the entities it names — or, for "this task", "the phase", "blocked", "debt",
the entities the index marks as current — are expanded into their context
neighbourhood, and only the canonical sections that neighbourhood touches are
returned, verbatim, each labelled with its source. Nothing here writes, and
nothing here consults a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import index, layout
from .analytics import Report
from .model import Project
from .parse import _sections


@dataclass
class Item:
    kind: str
    id: str
    source: str
    reason: str
    text: str
    revision: str | None = None
    criterionRevisions: dict[str, str] = field(default_factory=dict)

    def metadata(self) -> str:
        if self.revision is None:
            return ""
        return f"Revision: {self.revision}" + (
            " · criteria: " + ", ".join(self.criterionRevisions) + " (same revision)"
            if self.criterionRevisions else "")

    def as_json(self) -> dict:
        return {key: value for key, value in vars(self).items()
                if key not in ("revision", "criterionRevisions") or value}


DEFAULT_BUDGET = 4000  # tokens, estimated as index.tokens does


@dataclass
class Omitted:
    kind: str
    id: str
    source: str
    reason: str
    tokens: int

    def follow_up(self) -> str:
        if _ID.fullmatch(self.id) or self.id.startswith("Gate "):
            return f"praukron retrieve {self.id}"
        return f"read {self.source}"


@dataclass
class Pack:
    query: str
    routed: list[tuple[str, str]] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    omitted: list[Omitted] = field(default_factory=list)
    loaded_bytes: int = 0
    chronicle_bytes: int = 0
    budget: int = DEFAULT_BUDGET  # 0 for no budget
    index_note: str = ""
    implementation: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "query": self.query,
            "routed": [{"entity": e, "why": w} for e, w in self.routed],
            "items": [item.as_json() for item in self.items],
            "omitted": [{**vars(o), "followUp": o.follow_up()} for o in self.omitted],
            "loadedBytes": self.loaded_bytes,
            "chronicleBytes": self.chronicle_bytes,
            "budgetTokens": self.budget or None,
            "index": self.index_note,
            "implementation": self.implementation or None,
        }

    def render(self) -> str:
        lines = [f"# Context: {self.query}", ""]
        if self.index_note:
            lines += [f"_{self.index_note}_", ""]
        lines.append("Routed by INDEX.md to: " + (
            ", ".join(f"{e} ({w})" for e, w in self.routed) or "nothing specific; showing the index"))
        for item in self.items:
            # Record headings are demoted under the pack's own labels; the
            # record text is otherwise verbatim.
            body = re.sub(r"(?m)^(#{1,4}) ", r"###\1 ", item.text.strip())
            lines += ["", f"## {item.kind} · {item.id} — {item.source}", f"_{item.reason}_", *([item.metadata()] if item.revision else []), "", body]
        if self.omitted:
            # Pointers, not bodies: the agent still sees what else is relevant
            # and expands only what it needs.
            lines += ["", f"## Not loaded · over the {self.budget:,}-token budget", ""]
            lines += [
                f"- {o.kind} · {o.id} — {o.source} (_{o.reason}_, ~{o.tokens:,} tokens): `{o.follow_up()}`"
                for o in self.omitted
            ]
        share = (self.loaded_bytes / self.chronicle_bytes * 100) if self.chronicle_bytes else 0
        rest = (
            f" {len(self.omitted)} more relevant sections were not loaded; they are listed above."
            if self.omitted else " Nothing else was read into this context."
        )
        lines += [
            "", "---",
            f"Loaded {len(self.items)} sections, {self.loaded_bytes:,} of {self.chronicle_bytes:,} "
            f"bytes of the chronicle ({share:.0f}%).{rest}",
        ]
        text = "\n".join(lines) + "\n"
        # Implementation context comes after the project context, never
        # before it and never mixed into it (ADR-049).
        return text + (f"\n{self.implementation}" if self.implementation else "")


class Chronicle:
    """Section-level access to the canonical files, read lazily."""

    def __init__(self, root: Path) -> None:
        self.authority = root / layout.AUTHORITY_DIR
        self._cache: dict[str, list[tuple[str, str]]] = {}

    def total_bytes(self) -> int:
        return sum(
            p.stat().st_size for p in self.authority.rglob("*.md")
            if p.is_file() and p.name != index.FILE
        )

    def section(self, name: str, anchor: str) -> str | None:
        found = self._find(name, anchor)
        if found is None and name in ("TASKS.md", "ACCEPTANCE.md", "ASSUMPTIONS.md",
                                      "TECH_DEBT.md", "TRACE.md", "JOURNAL.md"):
            # A pointer written before its phase was archived still resolves (ADR-067).
            for archived in sorted((self.authority / layout.ARCHIVE_DIR).glob(f"*/{name}")):
                found = self._find(archived.relative_to(self.authority).as_posix(), anchor)
                if found is not None:
                    break
        return found

    def _find(self, name: str, anchor: str) -> str | None:
        if name not in self._cache:
            path = self.authority / name
            self._cache[name] = _sections(path.read_text(encoding="utf-8"), "##") if path.is_file() else []
        for heading, body in self._cache[name]:
            head = re.split(r":| — ", heading, maxsplit=1)[0].strip()
            if heading == anchor or head == anchor:
                return f"## {heading}\n{body}"
        return None

    def file(self, relative: str) -> str | None:
        path = self.authority / relative
        return path.read_text(encoding="utf-8") if path.is_file() else None


_ID = re.compile(r"\b(T-[A-Za-z0-9.\-]*[A-Za-z0-9]|ADR-\d+|TD-[A-Za-z0-9.\-]*[A-Za-z0-9]|EV-[A-Za-z0-9.\-]*[A-Za-z0-9]|(?:CV|BR)-[A-Za-z0-9.\-]*[A-Za-z0-9]|P\d+)\b")
_GATE = re.compile(r"\bgate ([A-Za-z0-9\-]+)\b", re.IGNORECASE)
_WORDS = {
    "blocked": r"\b(block\w*|stuck|waiting|why)\b",
    "task": r"\b(this|current|active) task\b|\btask\b(?! T-)",
    "phase": r"\bphase\b",
    "gate": r"\b(gate|exit)\b",
    "debt": r"\b(debt|compromise|liabilit\w*)\b",
    "decision": r"\b(decision|decided|adr|why)\b",
    "operations": r"\b(operation\w*|tooling|failure\w*|failed|retry|retries)\b",
    "handoff": r"\b(handoff|next step|next action)\b",
}


def _markers(root: Path, project: Project, report: Report) -> tuple[dict[str, str], str]:
    """The index's current markers, from the file when it is current."""
    path = root / layout.AUTHORITY_DIR / index.FILE
    fresh = index.render(project, report, root)
    if path.is_file() and path.read_text(encoding="utf-8") == fresh:
        text, note = fresh, ""
    else:
        text = fresh
        note = "INDEX.md was missing or stale; routed with an index rebuilt in memory (run praukron compile)."
    current = text.split("## Current", 1)[1].split("\n## ", 1)[0]
    markers = {k: v.strip() for k, v in re.findall(r"(?m)^- ([a-z_]+): (.*)$", current)}
    return markers, note


def _listed(value: str | None) -> list[str]:
    return [] if not value or value == "none" else [v.strip() for v in value.split(",") if v.strip()]


def retrieve(project: Project, report: Report, root: Path, query: str,
             budget: int = DEFAULT_BUDGET) -> Pack:
    chronicle = Chronicle(root)
    pack = Pack(query=query, chronicle_bytes=chronicle.total_bytes(), budget=budget)
    spent = 0
    markers, pack.index_note = _markers(root, project, report)
    lowered = query.lower()
    wants = {name for name, pattern in _WORDS.items() if re.search(pattern, lowered)}
    tasks = {t.id: t for t in project.tasks}
    gates = {g.id: g for g in project.gates}
    phases = {p.id: p for p in project.phases}
    seen: set[str] = set()

    def add(kind: str, record_id: str, source: str, reason: str, text: str | None) -> None:
        nonlocal spent
        if text is None or source in seen:
            return
        seen.add(source)
        item = Item(kind, record_id, source, reason, text)
        if kind in ("TASK", "ACCEPTANCE"):
            from .mutate import record_revision
            from .model import Source

            name, anchor = source.split("#", 1)
            item.revision = record_revision(root, Source(name, anchor))
            contract = project.contracts.get(record_id) if kind == "ACCEPTANCE" else None
            if contract:
                item.criterionRevisions = {c.id: item.revision for c in contract.criteria}
        cost = index.tokens(text + item.metadata())
        # Items arrive most relevant first. The first is always loaded; once one
        # does not fit, everything after it is listed rather than loaded, so the
        # cut is a clean prefix of the neighbourhood.
        if pack.items and budget and (pack.omitted or spent + cost > budget):
            pack.omitted.append(Omitted(kind, record_id, source, reason, cost))
            return
        spent += cost
        pack.items.append(item)
        pack.loaded_bytes += len(text.encode())

    def task(task_id: str, reason: str) -> None:
        t = tasks.get(task_id)
        if t is None:
            return
        # Each record from where it lives, the working file or its phase's archive.
        add("TASK", t.id, f"{t.source.file}#{t.id}", reason, chronicle.section(t.source.file, t.id))
        if t.contract:
            contract = project.contracts.get(t.contract)
            name = contract.source.file if contract else "ACCEPTANCE.md"
            add("ACCEPTANCE", t.contract, f"{name}#{t.contract}", f"contract of {t.id}",
                chronicle.section(name, t.contract))

    def debt(debt_id: str, reason: str) -> None:
        record = next((debt for debt in project.debts if debt.id == debt_id), None)
        if record is not None:
            add("TECH DEBT", debt_id, f"{record.source.file}#{debt_id}", reason,
                chronicle.section(record.source.file, debt_id))

    def decision(adr: str, reason: str) -> None:
        add("DECISION", adr, f"ADR/{adr}.md", reason, chronicle.file(f"ADR/{adr}.md"))

    def event(event_id: str, reason: str) -> None:
        record = next((event for event in project.events if event.id == event_id), None)
        if record is not None:
            add("OPERATIONS EVENT", event_id, f"{record.source.file}#{event_id}", reason,
                chronicle.section(record.source.file, event_id))

    def convention(convention_id: str, reason: str) -> None:
        record = project.convention(convention_id)
        if record is not None:
            add("CONVENTION", record.id, f"{record.source.file}#{record.id}", reason,
                chronicle.section(record.source.file, record.id))

    def brief(record, reason: str) -> None:
        add("BRIEF", record.id, f"{record.source.file}#{record.id}", reason,
            chronicle.section(record.source.file, record.id))

    def coordination(task_id: str) -> None:
        """The task's brief and the ACTIVE conventions it rests on (ADR-075)."""
        record = project.brief_for(task_id)
        if record is None:
            return
        brief(record, f"brief for {task_id}")
        for convention_id in record.conventions or []:
            found = project.convention(convention_id)
            if found is not None and found.status == "ACTIVE":
                convention(convention_id, f"{record.id} rests on it")

    def blocking_chain(t, depth: int) -> None:
        """Open dependencies, followed as far as `depth` levels: what a task
        waits on, and what that waits on in turn."""
        if depth <= 0:
            return
        for dependency in t.dependencies:
            dep = tasks.get(dependency)
            if dep is not None and not dep.done:
                label = "operations work " if not dep.execution else ""
                task(dependency, f"{label}{t.id} waits on it")
                for d in project.debts_for(dependency):
                    debt(d.id, f"linked to {dependency}")
                for e in project.events:
                    if e.failed and (e.task == dependency or dependency in e.affects):
                        event(e.id, f"failure recorded against {dependency}")
                blocking_chain(dep, depth - 1)

    def task_neighbourhood(task_id: str, reason: str) -> None:
        t = tasks.get(task_id)
        if t is None:
            return
        task(task_id, reason)
        for adr in t.decisions:
            if adr.startswith("ADR-"):
                decision(adr, f"governs {task_id}")
        # Below the ADRs that govern the task, as the brief itself ranks (ADR-075).
        coordination(task_id)
        for d in project.debts:
            if d in project.debts_for(task_id) or any(a in d.introduced_by for a in t.decisions):
                debt(d.id, f"linked to {task_id}")
        blocking_chain(t, 2)
        for e in project.events:
            if e.task == task_id or task_id in e.affects:
                event(e.id, f"recorded against {task_id}")

    def gate_neighbourhood(gate_id: str, reason: str) -> None:
        g = gates.get(gate_id)
        if g is None:
            return
        add("GATE", g.id, f"PHASES.md#{g.id}", reason, chronicle.section("PHASES.md", g.id))
        refs = set(g.verified_by)
        for t in project.tasks:
            contract = project.contracts.get(t.contract or "")
            if not t.done and contract and (contract.id in refs or any(c.id in refs for c in contract.criteria)):
                task(t.id, f"verifies {g.id}")

    def phase_neighbourhood(phase_id: str, reason: str) -> None:
        p = phases.get(phase_id)
        if p is None:
            return
        add("PHASE", p.id, f"PHASES.md#{p.id}", reason, chronicle.section("PHASES.md", p.id))
        for g in project.gates:
            blocks = any(entry.split()[:1] == [p.id] for entry in g.blocks if entry.split())
            ready = g.status == "GREEN"
            if blocks and (not ready or "gate" in wants):
                gate_neighbourhood(g.id, f"gate on {p.id} exit")
        authority = tasks.get(p.exit_authority or "")
        if authority is not None:
            if "blocked" in wants:
                task_neighbourhood(authority.id, f"exit authority of {p.id}")
            else:
                task(authority.id, f"exit authority of {p.id}")
        blocker = report.main_blocker
        if "blocked" in wants and blocker and blocker["kind"] == "TASK":
            blocking = tasks.get(blocker["blocking"])
            if blocking is not None and blocking.phase == p.id or blocker["reason"] == "phase exit":
                task_neighbourhood(blocker["id"], f"main blocker of {blocker['blocking']}")

    # Explicit ids first, in the order the question names them.
    explicit = list(dict.fromkeys(_ID.findall(query)))
    explicit += [f"Gate {g}" for g in _GATE.findall(query) if f"Gate {g}" in gates]
    for entity in explicit:
        if entity in tasks:
            pack.routed.append((entity, "named"))
            task_neighbourhood(entity, "named in the question")
        elif entity in phases:
            pack.routed.append((entity, "named"))
            phase_neighbourhood(entity, "named in the question")
        elif entity in gates:
            pack.routed.append((entity, "named"))
            gate_neighbourhood(entity, "named in the question")
        elif entity.startswith("ADR-"):
            pack.routed.append((entity, "named"))
            decision(entity, "named in the question")
            for d in project.debts:
                if entity in d.introduced_by:
                    debt(d.id, f"introduced by {entity}")
        elif entity.startswith("TD-"):
            pack.routed.append((entity, "named"))
            debt(entity, "named in the question")
            record = next((d for d in project.debts if d.id == entity), None)
            if record:
                for ref in record.introduced_by:
                    decision(ref, f"introduced {entity}") if ref.startswith("ADR-") else task(ref, f"introduced {entity}")
                for ref in record.linked_tasks:
                    task(ref, f"repays {entity}")
        elif entity.startswith("CV-"):
            pack.routed.append((entity, "named"))
            convention(entity, "named in the question")
        elif entity.startswith("BR-"):
            record = next((b for b in project.briefs if b.id == entity), None)
            if record is not None:
                pack.routed.append((entity, "named"))
                brief(record, "named in the question")
                task(record.task, f"briefed by {entity}")
        elif entity.startswith("EV-"):
            pack.routed.append((entity, "named"))
            event(entity, "named in the question")
            ev = next((e for e in project.events if e.id == entity), None)
            if ev and ev.task:
                task(ev.task, f"origin of {entity}")

    # Implicit references resolve through the index's current markers.
    if not explicit:
        if "task" in wants or ("blocked" in wants and "phase" not in wants and "gate" not in wants):
            for t in _listed(markers.get("execution_task"))[:1] or _listed(markers.get("blocking"))[:1]:
                pack.routed.append((t, "INDEX execution_task"))
                task_neighbourhood(t, "current execution task (INDEX.md)")
        if "phase" in wants or "gate" in wants:
            p = markers.get("phase")
            if p and p != "none":
                pack.routed.append((p, "INDEX phase"))
                phase_neighbourhood(p, "current phase (INDEX.md)")
        if "debt" in wants:
            for d in _listed(markers.get("debt_attention")):
                pack.routed.append((d, "INDEX debt_attention"))
                debt(d, "needs attention (INDEX.md)")
        if "decision" in wants and not pack.items:
            for adr in _listed(markers.get("decisions_now")):
                pack.routed.append((adr, "INDEX decisions_now"))
                decision(adr, "matters now (INDEX.md)")
        if "operations" in wants:
            for o in _listed(markers.get("operations_affecting_execution")) or _listed(markers.get("operations_active")):
                pack.routed.append((o, "INDEX operations"))
                task_neighbourhood(o, "operations affecting execution (INDEX.md)")
        if "handoff" in wants:
            pack.routed.append(("HANDOFF.md", "asked"))
            add("HANDOFF", "HANDOFF", "HANDOFF.md", "asked for the handoff", chronicle.file("HANDOFF.md"))
    if not pack.items:
        fresh = index.render(project, report, root)
        add("INDEX", "INDEX", "INDEX.md", "nothing in the question names a record; start from the index", fresh)
    return pack


def implementation(project: Project, pack: Pack, root: Path) -> str:
    """Code structure for the tasks the project context resolved, from
    CodeGraph when it works, or a pointer for native tools when it does not."""
    from . import codegraph

    tasks = [project.task(i.id) for i in pack.items if i.kind == "TASK" and project.task(i.id)][:3]
    if not tasks:
        return (
            "## Implementation\n\nThe project context names no task, so there is no code "
            "to look up yet. Resolve the task first.\n"
        )
    query = codegraph.query_for(tasks)
    names = ", ".join(t.id for t in tasks)
    anchors = sorted({f for t in tasks for f in t.files} | {s for t in tasks for s in t.symbols})
    ok, output = codegraph.explore(root, query)
    if ok:
        return (
            "## Implementation · from CodeGraph, after the project context\n\n"
            f"Query: `codegraph explore \"{query}\"` for {names}. "
            "This is code structure, not project state.\n\n" + output.rstrip() + "\n"
        )
    hint = (
        f"Anchors recorded on {names}: {', '.join(anchors)}."
        if anchors else f"No anchors are recorded; start from: {query}"
    )
    return f"## Implementation · CodeGraph not used\n\n{output}\n\nContinue with repository tools. {hint}\n"
