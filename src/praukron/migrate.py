"""Move a chronicle written under an earlier layout into the current one.

Two upgrades are handled, and a project can need either:

- v0.1 kept authority in `.prokron/`. ADR-014 moved it to `prokron/` and left
  `.prokron/` for compiled output. That migration rewrites documents: prose
  acceptance becomes contracts, a single decisions file becomes an ADR
  directory. The originals are archived untouched.
- v0.2 kept authority at the repository root. ADR-024 moved everything
  Prokron owned inside `.prokron/`; the current target is `.praukron/`. This is a relocation and not a
  rewrite: the records move whole, byte for byte, and nothing is archived
  because nothing is transformed.

Updating the tool alone does not move the records, so a project that upgrades
keeps its history but reports nothing until this has run. Neither migration
runs over authority that already holds work.

Two files are treated differently from the rest:

- `TASK_GRAPH.md` is dropped, because every fact in it is derivable from
  `TASKS.md` and the compiler regenerates it on every run. Keeping a
  hand-written copy in the compiled directory would only invite it to go stale.
- `STATE.md` is not dropped, because its risks and next steps are judgement
  rather than derivation. That prose moves into `HANDOFF.md`, where authored
  narrative belongs. Losing it would be the one genuinely destructive thing a
  naive migration could do.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import layout
from .layout import AUTHORITY_DIR

LEGACY_FILES = (
    "TASKS.md",
    "DECISIONS.md",
    "STATE.md",
    "TASK_GRAPH.md",
    "INTENT.md",
    "JOURNAL.md",
    "README.md",
)


class MigrationError(Exception):
    """Raised when migrating would lose or overwrite something."""


@dataclass
class Plan:
    root: Path
    tasks: int = 0
    decisions: int = 0
    contracts: int = 0
    archive: Path | None = None
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"Migration plan for {self.root}", ""]
        lines += [f"  {step}" for step in self.steps]
        if self.notes:
            lines += ["", "Notes:"]
            lines += [f"  {note}" for note in self.notes]
        return "\n".join(lines)


def _legacy_dir(root: Path) -> Path:
    return root / layout.V01_AUTHORITY_DIR


def _holds_work(path: Path) -> bool:
    return path.is_file() and bool(re.search(r"(?m)^## T-", path.read_text(encoding="utf-8")))


def _unread(root: Path, legacy_authority: str) -> bool:
    """True when that layout holds tasks the current layout cannot see."""
    if not _holds_work(root / legacy_authority / "TASKS.md"):
        return False
    return not _holds_work(root / AUTHORITY_DIR / "TASKS.md")


def needs_migration(root: Path) -> bool:
    """True when a v0.1 chronicle holds work the current layout cannot see."""
    return _unread(root, layout.V01_AUTHORITY_DIR)


def needs_relocation(root: Path) -> bool:
    """True when a v0.2 chronicle sits at the root, unread by the current layout."""
    return _unread(root, layout.V02_AUTHORITY_DIR)


def _split_entries(text: str, pattern: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(pattern, text, re.M))
    out = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((match.group(1), text[match.start() : end].rstrip() + "\n"))
    return out


def _evidence_class(evidence: str) -> str:
    """Classify recorded evidence by what it actually describes."""
    low = evidence.lower()
    if any(word in low for word in ("test", "unittest", "suite", "check")):
        return "TEST"
    if any(word in low for word in ("rendered", "browser", "ran ", "observed")):
        return "RUNTIME"
    return "INSPECTION"


def _migrate_tasks(text: str, phase: str) -> tuple[str, str, int]:
    """Rewrite tasks to reference contracts, and build those contracts.

    The prose acceptance statement becomes the criterion, worded as it was
    written. Restating a closed contract after the fact would change what its
    recorded evidence attests to.
    """
    entries = _split_entries(text, r"^## (T-[A-Za-z0-9.\-]+):")
    if not entries:
        return text, "", 0
    rewritten = [text[: text.index(entries[0][1])].rstrip()]
    contracts: list[str] = []

    for task_id, body in entries:
        acceptance = re.search(r"(?m)^- Acceptance: (.+(?:\n(?!- )[^\n]+)*)", body)
        status = re.search(r"(?m)^- Status: (\w+)", body)
        evidence = re.search(r"(?m)^- Evidence: (.+(?:\n(?!- )[^\n]+)*)", body)
        evidence_text = " ".join(evidence.group(1).split()) if evidence else ""

        if acceptance:
            statement = " ".join(acceptance.group(1).split())
            state = "PASS" if status and status.group(1) == "DONE" else "NOT_RUN"
            cls = _evidence_class(evidence_text)
            contracts.append(
                f"## AC-{task_id}\n\n"
                f"- `AC-{task_id}-01` — {statement} `{cls}` · `{state}`\n"
                + (f"  - Evidence: {evidence_text}\n" if evidence_text else "")
            )
            body = body.replace(acceptance.group(0), f"- AC: AC-{task_id}", 1)
        else:
            contracts.append(
                f"## AC-{task_id}\n\n"
                f"- `AC-{task_id}-01` — This task predates acceptance contracts and "
                f"recorded no acceptance statement. Replace this criterion with what "
                f"must be demonstrated. `INSPECTION` · `NOT_RUN`\n"
            )
            body = re.sub(
                r"(?m)^(- Status: [^\n]*\n)", rf"\1- AC: AC-{task_id}\n", body, count=1
            )

        if not re.search(r"(?m)^- Phase: ", body):
            body = re.sub(r"(?m)^(- Status: [^\n]*\n)", rf"\1- Phase: {phase}\n", body, count=1)
        rewritten.append(body.rstrip())

    header = (
        "# Acceptance\n\n"
        "Completion-contract authority. A task is done when its frozen contract\n"
        "has sufficient evidence.\n\n"
        "These contracts were migrated from prose acceptance statements written\n"
        "before this file existed. Their wording is preserved as it stood, because\n"
        "restating a closed contract would change what its evidence attests to.\n\n"
        "---\n\n# Contracts\n\n"
    )
    return "\n\n".join(rewritten) + "\n", header + "\n".join(contracts), len(entries)


def _migrate_decisions(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for adr_id, body in _split_entries(text, r"^## (ADR-[0-9]+):"):
        files[f"{adr_id}.md"] = body.replace(f"## {adr_id}", f"# {adr_id}", 1)
    return files


def _adr_index(files: dict[str, str]) -> str:
    lines = [
        "# Decisions",
        "",
        "Accepted engineering decision authority. One file per ADR, append-only.",
        "A decision that changes is superseded by a new ADR; the earlier file stays,",
        "with its original status intact.",
        "",
    ]
    superseded: dict[str, list[str]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for name, body in sorted(files.items()):
        adr_id = name[:-3]
        title = re.match(r"# ADR-[0-9]+: (.+)", body)
        status = re.search(r"- Status: (\w+)", body)
        meta[adr_id] = (title.group(1) if title else adr_id, status.group(1) if status else "UNKNOWN")
        for target in re.findall(r"- Supersedes: (.+)", body):
            for ref in re.findall(r"ADR-[0-9]+", target):
                superseded.setdefault(ref, []).append(adr_id)
    for adr_id, (title, status) in sorted(meta.items()):
        note = f" — superseded by {', '.join(superseded[adr_id])}" if adr_id in superseded else ""
        lines.append(f"- [{adr_id}]({adr_id}.md) — {title} ({status}){note}")
    return "\n".join(lines) + "\n"


def _handoff_from_state(state: str) -> str:
    return (
        "# Handoff\n\n"
        "Current implementation continuity. Overwritten, never appended.\n\n"
        "## Migrated from STATE.md\n\n"
        "The v0.1 chronicle kept risks, position and next steps in `STATE.md`.\n"
        f"`{layout.COMPILED_DIR}/STATE.md` is now generated from compiled facts, so this\n"
        "prose — which is judgement, not derivation — was moved here rather than lost.\n"
        "Rewrite it as an ordinary handoff when convenient.\n\n"
        "---\n\n" + state.strip() + "\n"
    )


def plan(root: Path, phase: str = "P-NONE") -> Plan:
    legacy = _legacy_dir(root)
    authority = root / AUTHORITY_DIR
    result = Plan(root=root)

    if not needs_migration(root):
        raise MigrationError(
            f"Nothing to migrate: no v0.1 chronicle with tasks found in "
            f"{legacy}, or {authority} already holds work."
        )

    stamp = date.today().isoformat()
    result.archive = root / f"{layout.V01_AUTHORITY_DIR}-v0.1-backup-{stamp}"
    if result.archive.exists():
        raise MigrationError(f"Archive already exists: {result.archive}")

    tasks_text = (legacy / "TASKS.md").read_text(encoding="utf-8")
    _, contracts, count = _migrate_tasks(tasks_text, phase)
    result.tasks = count
    result.contracts = len(re.findall(r"(?m)^## AC-", contracts))

    decisions_path = legacy / "DECISIONS.md"
    adrs = _migrate_decisions(decisions_path.read_text(encoding="utf-8")) if decisions_path.is_file() else {}
    result.decisions = len(adrs)

    result.steps = [
        f"move {count} tasks to {AUTHORITY_DIR}/TASKS.md, each referencing a contract",
        f"write {result.contracts} contracts to {AUTHORITY_DIR}/ACCEPTANCE.md from prose acceptance",
        f"split {len(adrs)} decisions into {AUTHORITY_DIR}/ADR/ with an index",
        f"move INTENT.md and JOURNAL.md to {AUTHORITY_DIR}/",
        f"move STATE.md prose into {AUTHORITY_DIR}/HANDOFF.md",
        f"drop TASK_GRAPH.md, which the compiler regenerates",
        f"archive the originals unchanged to {result.archive.name}/",
    ]
    result.notes = [
        f"every task is marked phase {phase}; v0.1 had no phases, so none are invented",
        "no file is deleted; the archive keeps the originals byte for byte",
    ]
    if phase == "P-NONE":
        result.notes.append(
            "phase-independent work is excluded from phase progress — create a phase "
            f"in {AUTHORITY_DIR}/PHASES.md and reassign tasks when you are ready"
        )
    return result


def apply(root: Path, phase: str = "P-NONE") -> Plan:
    result = plan(root, phase)
    legacy = _legacy_dir(root)
    authority = root / AUTHORITY_DIR
    (authority / "ADR").mkdir(parents=True, exist_ok=True)

    tasks, contracts, _ = _migrate_tasks((legacy / "TASKS.md").read_text(encoding="utf-8"), phase)
    (authority / "TASKS.md").write_text(tasks, encoding="utf-8")
    (authority / "ACCEPTANCE.md").write_text(contracts, encoding="utf-8")

    decisions_path = legacy / "DECISIONS.md"
    if decisions_path.is_file():
        adrs = _migrate_decisions(decisions_path.read_text(encoding="utf-8"))
        for name, body in adrs.items():
            (authority / "ADR" / name).write_text(body, encoding="utf-8")
        (authority / "ADR" / "README.md").write_text(_adr_index(adrs), encoding="utf-8")

    for name in ("INTENT.md", "JOURNAL.md"):
        source = legacy / name
        if source.is_file():
            (authority / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    state = legacy / "STATE.md"
    if state.is_file():
        (authority / "HANDOFF.md").write_text(_handoff_from_state(state.read_text(encoding="utf-8")), encoding="utf-8")

    assert result.archive is not None
    result.archive.mkdir()
    for name in LEGACY_FILES:
        source = legacy / name
        if source.is_file():
            shutil.move(str(source), str(result.archive / name))
    return result


# Compiled output the v0.2 layout left loose in `.prokron/`. Every one of these
# is regenerated by `compile`, `graph` and `dashboard`, so relocation clears
# them rather than carrying them into the new compiled directory.
STALE_COMPILED = (
    "README.md",
    "STATE.md",
    "TASK_GRAPH.md",
    "project.json",
    "dashboard.html",
)

# Files a host reads by fixed address. Each one names the procedure document it
# points at, so moving those documents makes the pointer wrong.
POINTER_DIRS = (".claude/commands", ".opencode/commands")


def _pointer_files(root: Path) -> list[Path]:
    found = []
    for directory in POINTER_DIRS:
        path = root / directory
        if path.is_dir():
            found += sorted(path.glob("praukron-*.md"))
            found += sorted(path.glob("prokron-*.md"))
    return found


def _repoint(text: str) -> str:
    """Name the relocated procedure documents, leaving any other prose alone."""
    return re.sub(
        r"(?<![.\w/])" + re.escape(layout.V02_COMMANDS_DIR) + r"/(?:praukron|prokron)-",
        f"{layout.COMMANDS_DIR}/praukron-",
        text,
    )


def relocate_plan(root: Path) -> Plan:
    """What moving a v0.2 installation into the single directory would do."""
    result = Plan(root=root)
    source = root / layout.V02_AUTHORITY_DIR
    if not needs_relocation(root):
        raise MigrationError(
            f"Nothing to relocate: no v0.2 chronicle with tasks found in {source}, "
            f"or {root / AUTHORITY_DIR} already holds work."
        )

    documents = sorted(path.name for path in source.iterdir())
    result.tasks = len(re.findall(r"(?m)^## T-", (source / "TASKS.md").read_text(encoding="utf-8")))
    result.decisions = len(sorted((source / "ADR").glob("ADR-*.md")))
    compiled = root / layout.V02_COMPILED_DIR
    stale = [name for name in STALE_COMPILED if (compiled / name).is_file()]
    stale += [path.name for path in sorted(compiled.glob("*.mmd"))]
    pointers = [
        path
        for path in _pointer_files(root)
        if _repoint(path.read_text(encoding="utf-8")) != path.read_text(encoding="utf-8")
    ]

    result.steps = [
        f"move {len(documents)} authored documents from {layout.V02_AUTHORITY_DIR}/ "
        f"to {AUTHORITY_DIR}/, including {result.decisions} decision records",
        f"discard {len(stale)} compiled files left loose in "
        f"{layout.V02_COMPILED_DIR}/, which compiling regenerates",
    ]
    if (root / layout.V02_COMMANDS_DIR).is_dir():
        result.steps.append(
            f"move the procedure documents from {layout.V02_COMMANDS_DIR}/ "
            f"to {layout.COMMANDS_DIR}/"
        )
    if pointers:
        result.steps.append(
            f"point {len(pointers)} host command files at their new location"
        )
    for obsolete in (layout.V02_RUNTIME_DIR, layout.V02_ENTRY_POINT):
        if (root / obsolete).exists():
            result.steps.append(
                f"remove {obsolete}, replaced by {layout.RUNTIME_DIR}/ "
                f"and {layout.ENTRY_POINT}"
            )
    result.notes = [
        f"{result.tasks} tasks move unchanged; this is a move, not a rewrite, so "
        "nothing is archived and no document is reformatted",
        "run `praukron compile` afterwards to rebuild the compiled directory",
    ]
    return result


def relocate(root: Path) -> Plan:
    """Move a v0.2 installation into the single directory ADR-024 defines."""
    result = relocate_plan(root)
    source = root / layout.V02_AUTHORITY_DIR
    authority = root / AUTHORITY_DIR
    authority.mkdir(parents=True, exist_ok=True)

    for path in sorted(source.iterdir()):
        target = authority / path.name
        if target.is_dir() and path.is_dir():
            for inner in sorted(path.iterdir()):
                shutil.move(str(inner), str(target / inner.name))
            path.rmdir()
        else:
            if target.exists():
                target.unlink()
            shutil.move(str(path), str(target))
    source.rmdir()

    compiled = root / layout.V02_COMPILED_DIR
    for name in STALE_COMPILED:
        stale = compiled / name
        if stale.is_file():
            stale.unlink()
    for stale in sorted(compiled.glob("*.mmd")):
        stale.unlink()

    commands = root / layout.V02_COMMANDS_DIR
    if commands.is_dir():
        target = root / layout.COMMANDS_DIR
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted([*commands.glob("praukron-*.md"), *commands.glob("prokron-*.md")]):
            destination = target / path.name.replace("prokron-", "praukron-", 1)
            if destination.exists():
                destination.unlink()
            shutil.move(str(path), str(destination))
        if not any(commands.iterdir()):
            commands.rmdir()

    for path in _pointer_files(root):
        text = path.read_text(encoding="utf-8")
        repointed = _repoint(text)
        if repointed != text:
            path.write_text(repointed, encoding="utf-8")

    runtime = root / layout.V02_RUNTIME_DIR
    if runtime.is_dir():
        shutil.rmtree(runtime)
    entry = root / layout.V02_ENTRY_POINT
    if entry.is_file():
        entry.unlink()
        parent = entry.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    return result
