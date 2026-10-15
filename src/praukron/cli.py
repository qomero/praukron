"""The praukron command.

Read commands compile projections; governed write commands validate and persist
only the records they are authorized to change. Nothing here needs a network
or a model provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analytics, compile as compiler, dashboard, index, layout, mermaid, migrate, retrieve, validate
from .model import Project
from .parse import ParseError

VERSION = layout.version()


def _load(root: Path) -> Project:
    return compiler.load(root)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _report_findings(findings: list, stream=sys.stdout) -> None:
    for finding in findings:
        print(f"  {finding.render()}", file=stream)


def _stale_compiled(root: Path) -> str | None:
    """The version that wrote the compiled views, when it is not this one.

    An upgrade replaces the runtime and, before ADR-031, left the generated
    views alone. A page written by an older release looks like a release that
    lost a feature, so the mismatch is reported rather than left to be noticed.
    """
    written = root / compiler.COMPILED_DIR / "project.json"
    if not written.is_file():
        return None
    try:
        recorded = json.loads(written.read_text(encoding="utf-8"))["project"].get("generatorVersion")
    except (ValueError, KeyError, OSError):
        return None
    return None if recorded in (None, VERSION) else str(recorded)


def _version_key(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    return tuple(int(part) for part in parts) if all(p.isdigit() for p in parts) else None


def _newer(version: str | None) -> bool:
    """Whether views were written by a runtime newer than this one."""
    if not version:
        return False
    theirs, ours = _version_key(version), _version_key(VERSION)
    return bool(theirs and ours and theirs > ours)


def _refuse_newer(root: Path, args: argparse.Namespace) -> int | None:
    """An older runtime rewriting a newer one's views would silently drop
    whatever the newer one knows how to show (ADR-041)."""
    stale = _stale_compiled(root)
    if not _newer(stale) or args.force:
        return None
    print(
        f"Refusing to overwrite views written by praukron {stale}; this is {VERSION}.\n"
        "Upgrade this installation, or pass --force to write them anyway.",
        file=sys.stderr,
    )
    return 1


def cmd_validate(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    findings = validate.check(project) + index.findings(project, analytics.report(project), root)
    errors = validate.errors(findings)
    warnings = [f for f in findings if f.severity == "warning"]
    if errors:
        print(f"{len(errors)} error{'s' if len(errors) != 1 else ''}:")
        _report_findings(errors, sys.stderr)
    if warnings and not args.quiet:
        print(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}:")
        _report_findings(warnings)
    # Warnings are worth seeing but do not make authority inconsistent; an
    # upgraded project with undeclared task domains is still valid (ADR-045).
    if not errors:
        suffix = f" ({len(warnings)} warning{'s' if len(warnings) != 1 else ''})" if warnings else ""
        print(f"{root.name}: authority is consistent{suffix}.")
    return 1 if errors else 0


def cmd_compile(root: Path, args: argparse.Namespace) -> int:
    refused = _refuse_newer(root, args)
    if refused is not None:
        return refused
    project = _load(root)
    errors = validate.errors(validate.check(project))
    if errors and not args.force:
        print(f"Refusing to compile with {len(errors)} validation errors:", file=sys.stderr)
        _report_findings(errors, sys.stderr)
        print("Fix the authority, or pass --force to compile anyway.", file=sys.stderr)
        return 1
    target = compiler.write(root, project)
    print(f"Wrote {target.relative_to(root)}")
    return 0


def cmd_graph(root: Path, args: argparse.Namespace) -> int:
    refused = _refuse_newer(root, args)
    if refused is not None:
        return refused
    project = _load(root)
    report = analytics.report(project)
    compiled = root / compiler.COMPILED_DIR
    compiled.mkdir(parents=True, exist_ok=True)
    for name, text in mermaid.render_all(project, report).items():
        (compiled / name).write_text(text, encoding="utf-8")
        print(f"Wrote {compiler.COMPILED_DIR}/{name}")
    return 0


def cmd_dashboard(root: Path, args: argparse.Namespace) -> int:
    refused = _refuse_newer(root, args)
    if refused is not None:
        return refused
    if args.serve:
        from . import serve

        return serve.run(root, args.port, args.open)
    project = _load(root)
    report = analytics.report(project)
    compiled = compiler.as_json(project)
    compiler.write(root, project)
    target = root / compiler.COMPILED_DIR / "dashboard.html"
    target.write_text(dashboard.render(project, report, compiled), encoding="utf-8")
    print(f"Wrote {target.relative_to(root)}")
    if args.open:
        import webbrowser

        webbrowser.open(target.as_uri())
    return 0


def cmd_status(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    report = analytics.report(project)
    metrics = report.metrics()

    print(f"{project.name} — phase {project.current_phase or 'none'}")
    stale = _stale_compiled(root)
    if _newer(stale):
        print(
            f"\n  Compiled views were written by praukron {stale}, which is newer "
            f"than this {VERSION}.\n  Upgrade this installation before compiling; "
            "an older runtime would rewrite them.\n"
        )
    elif stale:
        print(
            f"\n  Compiled views were written by praukron {stale}; this is "
            f"{VERSION}.\n  Run `praukron compile && praukron graph && praukron "
            "dashboard` to refresh them.\n"
        )
    if migrate.needs_relocation(root):
        print(
            f"\n  A chronicle is still at {migrate.layout.V02_AUTHORITY_DIR}/ and "
            "unread. Run `praukron migrate`.\n"
        )
    elif migrate.needs_migration(root):
        print(
            "\n  A v0.1 chronicle is present and unread. Run `praukron migrate`.\n"
        )
    print(
        f"  tasks        {metrics['taskCompletion']['done']} / "
        f"{metrics['taskCompletion']['total']} execution"
    )
    words = analytics.describe(metrics, report)
    print(f"  acceptance   {words['acceptanceStarted']}")
    print(f"               {words['acceptanceLater']}")
    print(f"  reviewed     {words['reviewed']}")
    if words["unreviewed"]:
        print(f"               {words['unreviewed']}")
    print(
        f"  gates        {metrics['gateReadiness']['done']} / "
        f"{metrics['gateReadiness']['total']} green"
    )
    for phase in project.phases:
        progress = report.phase_progress[phase.id]
        waits = report.phase_waits.get(phase.id)
        print(f"  {phase.id:<12} {progress} · {phase.status}"
              + (f" · exit waits on {waits}" if waits else ""))
    if words["workAhead"]:
        print(f"  ahead        {words['workAhead']}")
    line = analytics.assumption_line(report)
    if line:
        print(f"  assumptions  {line}")
    independent = metrics["phaseIndependent"]
    if independent:
        print(f"  {'no phase':<12} {independent} task{'' if independent == 1 else 's'}")
    operations = report.operations_metrics()
    if operations["tasks"]["total"] or operations["events"]["total"]:
        o = operations["tasks"]
        e = operations["events"]
        print(
            f"  operations   {o['open']} open · {o['active']} active · {o['blocked']} blocked"
            f" · {e['total']} events · {e['unresolvedFailures']} unresolved failures"
        )
    execution = {t.id for t in project.execution_tasks}
    ops_wip = [t for t in report.wip if t not in execution]
    print(f"\n  WIP       {', '.join(report.in_flight) or 'none'}")
    if ops_wip:
        print(f"  Ops WIP   {', '.join(ops_wip)}")
    print(f"  Ready     {', '.join(t for t in report.ready if t in execution) or 'none'}")
    print(f"  Blocked   {', '.join(t for t in report.blocked if t in execution) or 'none'}")
    if report.critical_path:
        print(f"  Next      {report.critical_path[0]} (critical path)")
    blocker = report.main_blocker
    if blocker:
        label = " · project operations" if blocker["domain"] == "operations" else ""
        print(f"  Blocker   {blocker['id']}{label} → {blocker['blocking']} ({blocker['reason']})")
    if report.obstacles:
        print(f"\n  {len(report.obstacles)} obstacles:")
        for obstacle in report.obstacles[: args.limit]:
            print(f"    {obstacle.type:<20} {obstacle.detail}")
        if len(report.obstacles) > args.limit:
            print(f"    ... and {len(report.obstacles) - args.limit} more")
    return 0


def cmd_explain(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    try:
        detail = analytics.explain(project, args.task)
    except KeyError:
        return _fail(f"No such task: {args.task}")
    if args.json:
        print(json.dumps(detail, indent=2))
        return 0
    print(f"{detail['id']} — {detail['title']}")
    print(f"  thesis {detail['lineage']['thesis'] or 'not authored'}")
    print(
        f"  phase {detail['phase']} · module {detail['module'] or 'none (legacy)'} · "
        f"{detail['status']} · {detail['validation']}"
    )
    if detail["dependencies"]:
        print("\n  Dependencies")
        for dependency in detail["dependencies"]:
            mark = "✓" if dependency["done"] else "○"
            print(f"    {mark} {dependency['id']}")
    print("\n  Acceptance")
    for criterion in detail["acceptance"] or []:
        mark = {"PASS": "✓", "FAIL": "✗", "NOT_RUN": "○"}[criterion["state"]]
        print(f"    {mark} {criterion['id']} [{criterion['class']}] {criterion['text'][:90]}")
    if detail["invariants"]:
        print(f"\n  Inherited invariants: {', '.join(detail['invariants'])}")
    if detail["blocks"]:
        print(f"  Blocks: {', '.join(detail['blocks'])}")
    if detail["blockers"]:
        print("\n  Blockers")
        for blocker in detail["blockers"]:
            print(f"    {blocker['type']}: {blocker['detail']}")
    print(f"\n  Evidence: {detail['evidence'] or 'none recorded'}")
    print(f"  Source:   {detail['source']['file']} → {detail['source']['anchor']}")
    return 0


def cmd_context(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    try:
        packet = analytics.context(project, args.task, args.role)
    except KeyError:
        return _fail(f"No such task: {args.task}")
    # Compact by default: indentation is for people, and cost an agent about
    # a fifth of every packet (ADR-059).
    print(json.dumps(packet, indent=2) if args.pretty else json.dumps(packet, separators=(",", ":")))
    return 0


def cmd_archive(root: Path, args: argparse.Namespace) -> int:
    from . import archive, respond

    try:
        plan = archive.archive(root, args.phase, dry_run=args.dry_run)
    except archive.ArchiveError as error:
        return _fail(str(error))
    applied = bool(plan.tasks) and not args.dry_run
    if applied:
        respond.refresh(root)
    if args.json:
        print(json.dumps({**plan.as_json(), "applied": applied}, indent=2))
    else:
        print(plan.render(applied), end="")
        if applied:
            print("Compiled views refreshed.")
    return 0


def write_options(parser: argparse.ArgumentParser) -> None:
    """The common interface for structured write subcommands."""
    parser.add_argument("--input", help="JSON payload file, or - for stdin")
    parser.add_argument("--dry-run", action="store_true", help="report the change without writing")
    parser.add_argument("--schema", action="store_true", help="print payload schema and a minimal example")


def cmd_journal_append(root: Path, args: argparse.Namespace) -> int:
    from . import journal, mutate

    if args.schema:
        result = {"schema": journal.SCHEMA, "example": journal.EXAMPLE}
    else:
        try:
            payload = mutate.load_payload(args.input, journal.SCHEMA)
            result = journal.append(root, payload, dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("journal.append", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1


def cmd_task_update(root: Path, args: argparse.Namespace) -> int:
    from . import mutate, task_update

    if args.schema:
        result = {"schema": task_update.SCHEMA, "example": task_update.EXAMPLE}
    else:
        try:
            payload = mutate.load_payload(args.input, task_update.SCHEMA)
            result = task_update.update(root, payload, dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("task.update", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1


def cmd_respond(root: Path, args: argparse.Namespace) -> int:
    """Append the owner's response to RESPONSES.md, validated and atomic (ADR-055)."""
    from . import respond

    if args.batch:
        try:
            entries = json.loads(Path(args.batch).read_text(encoding="utf-8"))
            items = [respond.Item(e["target"], e["action"], e["text"]) for e in entries]
        except (OSError, ValueError, KeyError, TypeError) as error:
            return _fail(f"--batch must be a JSON list of target, action, text: {error}")
    else:
        if not args.target or not args.action:
            return _fail("give a target and an action, or --batch FILE")
        text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else sys.stdin.read()
        items = [respond.Item(args.target, args.action, text)]
    try:
        ids = respond.append(root, items, args.by or "", via=args.via, date=args.date)
    except respond.RespondError as error:
        return _fail(str(error))
    print(f"Recorded {', '.join(ids)} in RESPONSES.md; compiled views refreshed.")
    return 0


def cmd_retrieve(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    if args.budget < 0:
        return _fail("--budget must be 0 (no budget) or a positive number of tokens")
    pack = retrieve.retrieve(project, analytics.report(project), root, " ".join(args.query), args.budget)
    if args.code:
        pack.implementation = retrieve.implementation(project, pack, root)
    if args.json:
        print(json.dumps(pack.as_json(), indent=2))
    else:
        print(pack.render(), end="")
    return 0


def _confirm(question: str) -> bool:
    """Ask on a terminal; anything but an explicit yes, or no terminal, is no."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_codegraph(root: Path, args: argparse.Namespace) -> int:
    """Implementation intelligence is project operations: tooling around the
    work. Nothing here reads or changes project state (ADR-049)."""
    from . import codegraph

    state = codegraph.status(root)
    if args.action == "status":
        if args.json:
            print(json.dumps(state, indent=2))
            return 0
        print("Project operations · implementation intelligence")
        print(f"  CodeGraph      {'AVAILABLE' if state['available'] else 'UNAVAILABLE'}"
              + (f" {state.get('version')}" if state.get("version") else ""))
        print(f"  Project index  {state['health']}"
              + (f" · {state.get('files')} files, {state.get('symbols')} symbols" if state.get("files") else ""))
        if state["health"] == "UNAVAILABLE":
            print("\n" + codegraph.INSTALL_HINT)
        elif state["health"] == "NOT_INITIALIZED":
            print("\n  Run `praukron codegraph setup` to build the project-local index.")
        elif state["health"] in ("STALE", "FAILING"):
            print(f"\n  {state.get('detail') or 'The index is behind the code; run `codegraph sync`.'}")
        return 0
    if args.action == "doctor":
        print("CodeGraph doctor")
        print(f"  on PATH:        {codegraph.binary() or 'no'}")
        print(f"  index:          {state['health']}")
        print(f"  .codegraph/:    {'present' if (root / '.codegraph').is_dir() else 'absent'}")
        print("  Praukron state:  independent — compile, gates, the critical path and debt never read CodeGraph")
        if state.get("detail"):
            print(f"  detail:         {state['detail']}")
        return 0 if state["health"] in ("HEALTHY", "STALE", "INDEXING") else 1
    if not state["available"]:
        print(codegraph.INSTALL_HINT, file=sys.stderr)
        return 1
    if args.action == "uninit":
        if not (args.yes or _confirm(f"Delete the CodeGraph index in {root}/.codegraph?")):
            print("Nothing changed.")
            return 1
        ok, output = codegraph.uninit(root)
        print(output.strip())
        return 0 if ok else 1
    # setup
    changed = False
    if state["health"] == "NOT_INITIALIZED":
        if not (args.yes or _confirm(f"Build a CodeGraph index in {root}/.codegraph?")):
            print("Nothing changed.")
            return 1
        ok, output = codegraph.init(root)
        print(output.strip())
        if not ok:
            return 1
        changed = True
    else:
        print(f"Project index already {state['health']}.")
    if args.wire_agents:
        # This edits configuration outside the repository, so a flag alone is
        # not consent: it is asked every time, and never assumed.
        if _confirm("Wire CodeGraph into your coding agents? This modifies user-level agent/MCP configuration."):
            ok, output = codegraph.install_agents()
            print(output.strip())
            return 0 if ok else 1
        print("Agent configuration left unchanged.")
        return 0 if changed else 1
    return 0


def domain_report(project: Project) -> dict[str, object]:
    """How every task's domain was decided, and what operational history
    exists. Reads only; classifying a task is an edit to TASKS.md."""
    groups: dict[str, list[str]] = {
        "explicitExecution": [], "explicitOperations": [], "inferredExecution": [],
        "inferredOperations": [], "ambiguous": [], "invalid": [],
    }
    for task in project.tasks:
        if task.declared_domain and task.domain_source != "declared":
            groups["invalid"].append(task.id)
        if task.domain_source == "declared":
            groups["explicitExecution" if task.execution else "explicitOperations"].append(task.id)
        elif task.domain_source == "unresolved":
            groups["ambiguous"].append(task.id)
        else:
            groups["inferredExecution"].append(task.id)
    events = project.events
    kinds = {
        "tool calls": [e for e in events if e.type in ("tool-call", "command")],
        "mini-actions": [e for e in events if e.type == "action"],
        "failures": [e for e in events if e.failed],
        "retries": [e for e in events if e.retry_of or e.type == "retry"],
        "mutations": [e for e in events if e.type == "mutation"],
        "timeline": [e for e in events if e.time],
    }
    return {
        "domains": groups,
        "inferredBy": {
            t.id: t.domain_source for t in project.tasks
            if t.domain_source in ("phase", "exit-authority", "gate")
        },
        "trace": {
            "recorded": bool(events),
            "counts": {name: len(found) for name, found in kinds.items()},
        },
    }


def cmd_domains(root: Path, args: argparse.Namespace) -> int:
    project = _load(root)
    result = domain_report(project)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    groups = result["domains"]
    labels = (
        ("explicitExecution", "explicit execution"),
        ("explicitOperations", "explicit operations"),
        ("inferredExecution", "inferred execution"),
        ("inferredOperations", "inferred operations"),
        ("ambiguous", "ambiguous — needs a Domain"),
        ("invalid", "invalid Domain value"),
    )
    print(f"{project.name} — task domains")
    for key, label in labels:
        print(f"  {label:<30} {len(groups[key])}")
    print(
        "\n  Operations is never inferred: nothing in a task's structure shows it\n"
        "  maintains the environment rather than the product, so it is declared."
    )
    if groups["ambiguous"]:
        print("\n  Needs classification (add `- Domain: execution` or `operations`):")
        for task_id in groups["ambiguous"]:
            task = project.task(task_id)
            print(f"    {task_id:<18} {task.status:<5} {task.title}")
    trace = result["trace"]
    print("\n  Operational trace (TRACE.md)")
    if not trace["recorded"]:
        print(
            "    No events recorded. Tool calls, mini-actions, failures, retries and\n"
            "    mutations appear only from when agents start appending to TRACE.md;\n"
            "    nothing is reconstructed from the journal."
        )
    else:
        for name, count in trace["counts"].items():
            print(f"    {name:<14} {count}")
    return 0


def cmd_migrate(root: Path, args: argparse.Namespace) -> int:
    # A v0.2 chronicle at the repository root is only in the wrong place, so it
    # is relocated before a v0.1 one is rewritten. A project needing both is
    # handled by running this twice, which is clearer than one compound move.
    relocating = migrate.needs_relocation(root)
    try:
        if relocating:
            result = migrate.relocate(root) if args.apply else migrate.relocate_plan(root)
        elif args.apply:
            result = migrate.apply(root, args.phase)
        else:
            result = migrate.plan(root, args.phase)
    except migrate.MigrationError as error:
        return _fail(str(error))
    print(result.render())
    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to perform it.")
        return 0
    if result.archive is None:
        print("\nRelocated. Every record moved unchanged.")
    else:
        print(f"\nMigrated. Originals archived in {result.archive.name}/")
    findings = validate.errors(validate.check(_load(root)))
    if findings:
        print(f"\n{len(findings)} validation errors remain:", file=sys.stderr)
        _report_findings(findings, sys.stderr)
        print("The records are migrated; fix these, then run compile.", file=sys.stderr)
        return 1
    compiler.write(root, _load(root))
    print("Authority validates. Compiled state written.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="praukron",
        description="Compile and report a repository's project chronicle.",
    )
    parser.add_argument("--version", action="version", version=f"praukron {VERSION}")
    parser.add_argument("-C", "--directory", default=None, help="run as if started here")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("validate", help="check authority for structural errors")
    check.add_argument("--quiet", action="store_true", help="errors only")
    check.set_defaults(handler=cmd_validate)

    build = subparsers.add_parser(
        "compile", help=f"write {compiler.COMPILED_DIR}/project.json"
    )
    build.add_argument(
        "--force", action="store_true", help="compile despite errors or newer views"
    )
    build.set_defaults(handler=cmd_compile)

    state = subparsers.add_parser("status", help="print current project state")
    state.add_argument("--limit", type=int, default=8, help="obstacles to show")
    state.set_defaults(handler=cmd_status)

    graph = subparsers.add_parser("graph", help="write Mermaid views")
    graph.add_argument("--force", action="store_true", help="overwrite views from a newer runtime")
    graph.set_defaults(handler=cmd_graph)

    board = subparsers.add_parser("dashboard", help="write the static dashboard")
    board.add_argument("--open", action="store_true", help="open it in a browser")
    board.add_argument("--serve", action="store_true",
                       help="serve it on 127.0.0.1 so the owner can answer assumptions and blockers")
    board.add_argument("--port", type=int, default=0, help="port for --serve; 0 picks a free one")
    board.add_argument("--force", action="store_true", help="overwrite views from a newer runtime")
    board.set_defaults(handler=cmd_dashboard)

    why = subparsers.add_parser("explain", help="explain one task")
    why.add_argument("task")
    why.add_argument("--json", action="store_true")
    why.set_defaults(handler=cmd_explain)

    move = subparsers.add_parser(
        "migrate", help="move a chronicle written under an earlier layout"
    )
    move.add_argument("--apply", action="store_true", help="perform it; otherwise report only")
    move.add_argument("--phase", default="P-NONE", help="phase to assign migrated tasks")
    move.set_defaults(handler=cmd_migrate)

    domains = subparsers.add_parser(
        "domains", help="report how each task's execution/operations domain was decided"
    )
    domains.add_argument("--json", action="store_true")
    domains.set_defaults(handler=cmd_domains)

    fetch = subparsers.add_parser(
        "retrieve", help="the chronicle records a question or task needs, routed by INDEX.md"
    )
    fetch.add_argument("query", nargs="+", help="a question, or a task, phase, gate, ADR, or debt id")
    fetch.add_argument("--json", action="store_true")
    fetch.add_argument(
        "--budget", type=int, default=retrieve.DEFAULT_BUDGET, metavar="TOKENS",
        help=f"load records up to about this many tokens and list the rest as pointers "
             f"(default {retrieve.DEFAULT_BUDGET}; 0 for no budget)",
    )
    fetch.add_argument(
        "--code", action="store_true",
        help="after the project context, add code structure from CodeGraph if installed",
    )
    fetch.set_defaults(handler=cmd_retrieve)

    graph_tool = subparsers.add_parser(
        "codegraph", help="optional CodeGraph integration: status, setup, doctor, uninit"
    )
    graph_tool.add_argument("action", choices=("status", "setup", "doctor", "uninit"))
    graph_tool.add_argument("--yes", action="store_true", help="skip the index confirmation")
    graph_tool.add_argument(
        "--wire-agents", action="store_true",
        help="also run `codegraph install`, which changes user-level agent configuration",
    )
    graph_tool.add_argument("--json", action="store_true")
    graph_tool.set_defaults(handler=cmd_codegraph)

    journal = subparsers.add_parser("journal", help="append governed journal entries")
    journal_commands = journal.add_subparsers(dest="journal_command", required=True)
    append_entry = journal_commands.add_parser("append", help="append one unique journal section")
    write_options(append_entry)
    append_entry.set_defaults(handler=cmd_journal_append, write_operation="journal.append")

    from . import contract, task

    tasks = subparsers.add_parser("task", help="structured task writes")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    add_task = task_commands.add_parser("add", help="append a task and optionally its contract")
    write_options(add_task)
    add_task.set_defaults(handler=task.command, write_operation="task.add")
    update_task = task_commands.add_parser("update", help="change a task's status, owner, claim or evidence")
    write_options(update_task)
    update_task.set_defaults(handler=cmd_task_update, write_operation="task.update")

    contracts = subparsers.add_parser("contract", help="create unfrozen acceptance contracts")
    contract_commands = contracts.add_subparsers(dest="contract_command", required=True)
    add_contract = contract_commands.add_parser("add", help="append a contract for an existing task")
    write_options(add_contract)
    add_contract.set_defaults(handler=contract.command, write_operation="contract.add")

    answer = subparsers.add_parser(
        "respond", help="append the owner's response to RESPONSES.md"
    )
    answer.add_argument("target", nargs="?", help="an assumption (A-…) or a task (T-…)")
    answer.add_argument("action", nargs="?", type=str.upper,
                        choices=("CONFIRM", "REVISE", "REJECT", "GUIDE"))
    answer.add_argument("--by", help="who is responding, as it should be recorded")
    answer.add_argument("--text-file", help="read the response text from a file instead of stdin")
    answer.add_argument("--batch", help="a JSON list of {target, action, text}, written all or nothing")
    answer.add_argument("--via", default="cli", help="how it arrived: cli, dashboard, or relayed by …")
    answer.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    answer.set_defaults(handler=cmd_respond)

    shelve = subparsers.add_parser(
        "archive",
        help="owner only: move a COMPLETE phase's DONE tasks and contracts to archive/<phase>/ (ADR-067)",
    )
    shelve.add_argument("phase", help="a COMPLETE phase, e.g. P3")
    shelve.add_argument("--dry-run", action="store_true", help="show what would move; write nothing")
    shelve.add_argument("--json", action="store_true")
    shelve.set_defaults(handler=cmd_archive)

    packet = subparsers.add_parser(
        "context", help="emit an agent context packet: orientation, or one task's"
    )
    packet.add_argument("task", nargs="?", help="omit for the project orientation packet")
    packet.add_argument("--role", choices=("builder", "reviewer"), default="builder")
    packet.add_argument("--pretty", action="store_true", help="indent the JSON for reading")
    packet.set_defaults(handler=cmd_context)

    return parser


def _utf8_streams() -> None:
    # Records are printed verbatim. Piped on Windows, stdout defaults to cp1252
    # and a Vietnamese record breaks `retrieve` and `context` mid-output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    if getattr(args, "write_operation", None) and args.schema:
        return args.handler(Path.cwd(), args)
    try:
        root = compiler.locate(Path(args.directory) if args.directory else None)
    except compiler.LayoutError as error:
        if getattr(args, "write_operation", None):
            from .mutate import WriteError, refusal

            print(json.dumps(refusal(args.write_operation, WriteError("invalid-project", str(error))),
                             separators=(",", ":")))
            return 1
        return _fail(str(error))
    try:
        return args.handler(root, args)
    except ParseError as error:
        return _fail(f"Cannot read authority: {error}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
