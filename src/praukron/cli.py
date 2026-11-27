"""The praukron command.

Read commands compile projections; governed write commands validate and persist
only the records they are authorized to change. Nothing here needs a network
or a model provider.
"""

from __future__ import annotations

import argparse
import datetime
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
    state = root / compiler.COMPILED_DIR / "project.json"
    before = state.read_bytes() if state.is_file() else None
    target = compiler.write(root, project)
    print(f"Wrote {target.relative_to(root)}")
    if state.is_file() and state.read_bytes() != before:
        # The page no longer matches the records; only `dashboard` renders it (ADR-086).
        dashboard.mark_stale(root / compiler.COMPILED_DIR / "dashboard.html", datetime.date.today().isoformat())
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

        return serve.run(root, args.port, args.open, lang=args.lang)
    project = _load(root)
    report = analytics.report(project)
    compiled = compiler.as_json(project)
    compiler.write(root, project)
    target = root / compiler.COMPILED_DIR / "dashboard.html"
    target.write_text(dashboard.render(project, report, compiled, lang=args.lang), encoding="utf-8")
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
    # Closed phases fold into one line so the output stays bounded as a
    # project grows; `explain` and STATE.md still list every phase.
    closed = [p for p in project.phases if p.status == "COMPLETE" and not report.phase_waits.get(p.id)]
    folded = closed if len(closed) > 3 else []
    if folded:
        span = f"{folded[0].id}–{folded[-1].id}"
        print(f"  {span:<12} {len(folded)} phases COMPLETE")
    for phase in project.phases:
        if phase in folded:
            continue
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
    print(f"{detail['id']} — {detail['title']} [revision {detail['revision'] or 'unavailable'}]")
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
        print(f"    {mark} {criterion['id']} [{criterion['class']}] {criterion['text'][:90]} [revision {criterion['revision'] or 'unavailable'}]")
    if detail["invariants"]:
        print(f"\n  Inherited invariants: {', '.join(detail['invariants'])}")
    if detail["blocks"]:
        print(f"  Blocks: {', '.join(detail['blocks'])}")
    if detail["blockers"]:
        print("\n  Blockers")
        for blocker in detail["blockers"]:
            print(f"    {blocker['type']}: {blocker['detail']}")
    if detail.get("verifications"):
        print("\n  Verification")
        for item in detail["verifications"]:
            print(f"    {item['response']} {item['date']}: {item['text']}")
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

    action = args.phase if args.phase in ("plan", "apply", "restore") else "archive"
    phase = args.selection if action != "archive" else args.phase

    def refused(code: str, message: str, *, written: bool = False) -> int:
        if args.json:
            detail = {**plan.as_json(), "applied": True} if written else {}
            print(json.dumps({**detail, "ok": False, "error": {"code": code, "message": message},
                              "written": written, "refreshed": False}, separators=(",", ":")))
            return 1
        if written:
            print(plan.render(True), end="")
        return _fail(f"{code}: {message}")

    if not phase or (action == "archive" and args.selection):
        return refused("invalid-selection", "use archive [plan|apply|restore] <phase>")
    if action == "apply" and not args.plan_digest:
        return refused("missing-plan", "archive apply requires --plan <digest>; run archive plan first")
    if action != "apply" and args.plan_digest is not None:
        return refused("invalid-option", "--plan requires archive apply <phase>")
    try:
        plan = (archive.preview(root, phase) if action == "plan" else
                archive.restore(root, phase, dry_run=args.dry_run) if action == "restore" else
                archive.archive(root, phase, dry_run=args.dry_run, digest=args.plan_digest))
    except archive.ArchiveError as error:
        return refused(error.code, str(error))
    except (ParseError, OSError, UnicodeError, respond.RespondError) as error:
        return refused("archive-failed", str(error))
    applied = any(c.moves for c in plan.candidates) and action != "plan" and not args.dry_run
    if applied:
        try:
            respond.refresh(root)
        except Exception as error:
            return refused("refresh-failed", f"archive was written; re-run only compile, graph and dashboard: {error}",
                           written=True)
    if args.json:
        print(json.dumps({**plan.as_json(), "ok": True, "applied": applied,
                          "written": applied, "refreshed": applied}, separators=(",", ":")))
    else:
        print(plan.render(applied), end="")
        if applied:
            print("Compiled views refreshed.")
    return 0


def write_options(parser: argparse.ArgumentParser, operation: str) -> None:
    """The common interface for structured write subcommands."""
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    from . import mutate
    import textwrap

    parser.description = "\n".join(
        textwrap.fill(part, width=88, subsequent_indent="  ", break_long_words=False, break_on_hyphens=False)
        if not part.startswith("EXAMPLE") else part for part in mutate.GUIDANCE[operation])
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


def cmd_criterion_set(root: Path, args: argparse.Namespace) -> int:
    from . import criterion, mutate

    if args.schema:
        result = {"schema": criterion.SCHEMA, "example": criterion.EXAMPLE}
    else:
        try:
            payload = mutate.load_payload(args.input, criterion.SCHEMA)
            result = criterion.set_result(root, payload, dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = criterion.refuse(root, error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1


def cmd_respond(root: Path, args: argparse.Namespace) -> int:
    """Append the owner's response to RESPONSES.md, validated and atomic (ADR-055)."""
    from . import mutate, respond

    if args.by:
        return _fail("--by is no longer accepted: a response records the active `gh` login (ADR-060)")
    verify = False
    if args.delegate is not None:
        # An agent verifying on the owner's authority (AUTHORITY.md): no terminal is
        # needed, and the record names the agent, the owner and the grant.
        if args.batch or args.action != "VERIFY" or not args.target:
            return _fail("--delegate applies only to `respond <task> VERIFY`")
        if args.date:
            return _fail("a delegated VERIFY is dated today: --date is not accepted with --delegate")
        try:
            text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else mutate.read_stdin_text()
        except (OSError, ValueError) as error:
            return _fail(f"cannot read what was verified: {error}")
        try:
            ids = respond.append(root, [respond.Item(args.target, "VERIFY", text)], via="cli",
                                 delegate=args.delegate)
        except respond.RespondError as error:
            return _fail(str(error))
        print(f"Recorded {', '.join(ids)} in RESPONSES.md: VERIFY of {args.target} by delegate "
              f"{args.delegate.strip()} on the owner's authority; set its Validation to DELEGATE_VERIFIED.")
        return 0
    if args.batch:
        try:
            entries = json.loads(Path(args.batch).read_text(encoding="utf-8"))
            items = [respond.Item(e["target"], e["action"], e["text"]) for e in entries]
        except (OSError, ValueError, KeyError, TypeError) as error:
            return _fail(f"--batch must be a JSON list of target, action, text: {error}")
    elif args.action == "VERIFY":
        if not args.target:
            return _fail("give the Authority: owner task to verify")
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return _fail("VERIFY is recorded only interactively: run it yourself at a terminal, "
                         "or use `praukron dashboard --serve`; an agent never issues it")
        try:
            login = respond.identity()
            print(f"Recording VERIFY of {args.target} as GitHub user {login}.")
            text = input("What did you verify? ").strip()
            typed = input(f"Type {args.target} to confirm: ").strip()
        except (EOFError, respond.RespondError) as error:
            return _fail(str(error) or "cancelled")
        if typed != args.target:
            return _fail("not confirmed; nothing was recorded")
        items = [respond.Item(args.target, "VERIFY", text)]
        verify = True
        args.via = "cli (terminal)" if args.via == "cli" else args.via
    else:
        if not args.target or not args.action:
            return _fail("give a target and an action, or --batch FILE")
        try:
            text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else mutate.read_stdin_text()
        except (OSError, ValueError) as error:
            return _fail(f"cannot read the response text: {error}")
        items = [respond.Item(args.target, args.action, text)]
    try:
        ids = respond.append(root, items, via=args.via, date=args.date, verify=verify)
    except respond.RespondError as error:
        return _fail(str(error))
    print(f"Recorded {', '.join(ids)} in RESPONSES.md; compiled views refreshed.")
    return 0


def _in_person() -> bool:
    """The interactive check VERIFY uses. It does not stop a program imitating a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_authority(root: Path, args: argparse.Namespace) -> int:
    """What agents may do on the owner's authority: list, grant, revoke (AUTHORITY.md)."""
    from . import authority, respond

    if args.authority_command is None:
        project = _load(root)
        data = authority.view(project, authority.today())
        if args.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(authority.render(data), end="")
        return 0
    if not _in_person():
        return _fail(f"a grant or revocation is recorded only by the owner in person: run `praukron authority "
                     f"{args.authority_command}` yourself at a terminal, or use `praukron dashboard --serve`; "
                     "an agent never records one")
    try:
        login = respond.identity()
        if args.authority_command == "grant":
            until = args.until or "none (until revoked)"
            print(f"Granting {args.capability} to {args.delegate} for scope {args.scope}, until {until}, "
                  f"as GitHub user {login}.")
            if args.scope.strip().lower() == authority.PROJECT_SCOPE:
                print("This grant covers the WHOLE PROJECT.")
            if not args.until:
                print("This grant has NO END: it holds until you revoke it.")
            typed = input("Type grant to confirm: ").strip()
            if typed != "grant":
                return _fail("not confirmed; nothing was recorded")
            made = authority.grant(root, args.capability, args.scope, args.delegate, args.until, via="cli")
            print(f"Recorded {made} in {authority.FILE}; compiled views refreshed.")
            return 0
        print(f"Revoking {args.grant} as GitHub user {login}.")
        typed = input(f"Type {args.grant} to confirm: ").strip()
        if typed != args.grant:
            return _fail("not confirmed; nothing was recorded")
        made = authority.revoke(root, args.grant, via="cli")
        print(f"Revoked {made} in {authority.FILE}; the grant stays readable; compiled views refreshed.")
        return 0
    except (EOFError, respond.RespondError, authority.AuthorityError) as error:
        return _fail(str(error) or "cancelled")


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


def cmd_refs(root: Path, args: argparse.Namespace) -> int:
    from . import refs

    try:
        result = refs.lookup(_load(root), args.target)
    except refs.TargetError as error:
        return _fail(str(error))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(refs.render(result), end="")
    return 0


def cmd_why(root: Path, args: argparse.Namespace) -> int:
    """Why a piece of code is as it is (ADR-103), from the chronicle alone:
    no graph library is looked for and no index is read."""
    from . import refs, why

    try:
        result = why.why(_load(root), root, args.target)
    except refs.TargetError as error:
        return _fail(str(error))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(why.render(result), end="")
    return 0


def _confirm(question: str) -> bool:
    """Ask on a terminal; anything but an explicit yes, or no terminal, is no."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_engine(root: Path, args: argparse.Namespace) -> int:
    """The optional embedded graph library (ADR-100). Imported only here, so
    no other command loads it or reads its index."""
    from . import engine

    if args.action == "map":
        # Reads the index with sqlite3 alone; locating the library loads nothing.
        from . import codemap, refs

        try:
            result = codemap.code_map(_load(root), root, args.target, engine.index_path(root),
                                      engine.locate(root).present)
        except refs.TargetError as error:
            return _fail(str(error))
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(codemap.render(result), end="")
        return 0
    if args.action == "impact":
        # What a planned change runs into (ADR-103): the index read as `map` reads it.
        from . import impact, refs

        try:
            result = impact.impact(_load(root), root, args.target, engine.index_path(root),
                                   engine.locate(root).present, depth=args.depth, exact_only=args.exact)
        except refs.TargetError as error:
            return _fail(str(error))
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(impact.render(result), end="")
        return 0
    return engine.command(root, args)


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
    board.add_argument("--lang", help="dashboard language (overrides THESIS.md Language)")
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

    lookup = subparsers.add_parser(
        "refs", help="a task's files and symbols, or the tasks that reference a file or symbol"
    )
    lookup.add_argument("target", help="a task id, a repository-relative path, or path#Qualified.symbol")
    lookup.add_argument("--json", action="store_true")
    lookup.set_defaults(handler=cmd_refs)

    reasons = subparsers.add_parser(
        "why", help="why code is as it is: the tasks, decisions, criteria and invariants behind a file or symbol"
    )
    reasons.add_argument("target", help="a repository-relative path, path#Qualified.symbol, or a directory")
    reasons.add_argument("--json", action="store_true")
    reasons.set_defaults(handler=cmd_why)

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

    embedded = subparsers.add_parser(
        "engine",
        help="optional embedded graph library: status, index, export, map, impact (derived output, never authority)",
    )
    embedded_commands = embedded.add_subparsers(dest="action", required=True)
    engine_status = embedded_commands.add_parser(
        "status", help="whether a graph library is present, its engine commit and interface version"
    )
    engine_status.add_argument("--json", action="store_true")
    engine_index = embedded_commands.add_parser(
        "index", help="index this repository, or refresh the index after edits"
    )
    engine_index.add_argument("--full", action="store_true", help="re-read every file")
    engine_index.add_argument("--json", action="store_true")
    engine_export = embedded_commands.add_parser("export", help="the whole graph as one JSON document")
    engine_export.add_argument(
        "-o", "--output", default=None, metavar="FILE", help="write it here instead of standard output"
    )
    engine_map = embedded_commands.add_parser(
        "map", help="a task's code with its graph neighbours, or the tasks and neighbours of a file or symbol, "
                    "each item labelled by source"
    )
    engine_map.add_argument("target", help="a task id, a repository-relative path, or path#Qualified.symbol")
    engine_map.add_argument("--json", action="store_true")
    engine_impact = embedded_commands.add_parser(
        "impact", help="what a planned change runs into: the code that calls or depends on a task's files and "
                       "symbols, the tasks on that code, and their decisions, frozen criteria and invariants"
    )
    engine_impact.add_argument("target", help="a task id")
    engine_impact.add_argument("--depth", type=int, default=2, metavar="N",
                               help="how many steps of callers to follow (default 2)")
    engine_impact.add_argument("--exact", action="store_true", help="follow only edges the engine marks exact")
    engine_impact.add_argument("--json", action="store_true")
    embedded.set_defaults(handler=cmd_engine)

    journal = subparsers.add_parser("journal", help="append governed journal entries")
    journal_commands = journal.add_subparsers(dest="journal_command", required=True)
    append_entry = journal_commands.add_parser("append", help="append one unique journal section")
    write_options(append_entry, "journal.append")
    append_entry.set_defaults(handler=cmd_journal_append, write_operation="journal.append")

    from . import acr, batch, contract, task

    batches = subparsers.add_parser("batch", help="apply several structured writes atomically")
    batch_commands = batches.add_subparsers(dest="batch_command", required=True)
    apply_batch = batch_commands.add_parser("apply", help="apply ordered governed writes in one transaction")
    write_options(apply_batch, "batch.apply")
    apply_batch.set_defaults(handler=batch.command, write_operation="batch.apply")

    tasks = subparsers.add_parser("task", help="structured task writes")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    add_task = task_commands.add_parser("add", help="append a task and optionally its contract")
    write_options(add_task, "task.add")
    add_task.set_defaults(handler=task.command, write_operation="task.add")
    update_task = task_commands.add_parser("update", help="change a task's status, owner, claim, evidence, validation or governing ADRs")
    write_options(update_task, "task.update")
    update_task.set_defaults(handler=cmd_task_update, write_operation="task.update")

    contracts = subparsers.add_parser("contract", help="create unfrozen acceptance contracts")
    contract_commands = contracts.add_subparsers(dest="contract_command", required=True)
    add_contract = contract_commands.add_parser("add", help="append a contract for an existing task")
    write_options(add_contract, "contract.add")
    add_contract.set_defaults(handler=contract.command, write_operation="contract.add")
    criterion = subparsers.add_parser("criterion", help="record governed criterion results")
    criterion_commands = criterion.add_subparsers(dest="criterion_command", required=True)
    set_criterion = criterion_commands.add_parser("set", help="set one criterion state and evidence")
    write_options(set_criterion, "criterion.set")
    set_criterion.set_defaults(handler=cmd_criterion_set, write_operation="criterion.set")
    requests = subparsers.add_parser("acr", help="record Acceptance Change Requests")
    request_commands = requests.add_subparsers(dest="acr_command", required=True)
    add_request = request_commands.add_parser("add", help="record a change request against a frozen criterion")
    write_options(add_request, "acr.add")
    add_request.set_defaults(handler=acr.command, write_operation="acr.add")

    from . import ref

    references = subparsers.add_parser("ref", help="structured task-code reference writes and history")
    reference_commands = references.add_subparsers(dest="ref_command", required=True)
    add_reference = reference_commands.add_parser("add", help="append one task-code reference to CODE.md")
    write_options(add_reference, "ref.add")
    add_reference.set_defaults(handler=ref.command_add, write_operation="ref.add")
    set_reference = reference_commands.add_parser(
        "set", help="change a reference's verification, lifecycle or evidence, or withdraw or replace it")
    write_options(set_reference, "ref.set")
    set_reference.set_defaults(handler=ref.command_set, write_operation="ref.set")
    reference_history = reference_commands.add_parser(
        "history", help="print a reference and every record it replaces, oldest first, unchanged")
    reference_history.add_argument("id", help="a CR- reference id")
    reference_history.add_argument("--json", action="store_true")
    reference_history.set_defaults(handler=ref.command_history)

    from . import scope

    observed = subparsers.add_parser("scope", help="observed task scope captured from Git")
    scope_commands = observed.add_subparsers(dest="scope_command", required=True)
    capture = scope_commands.add_parser(
        "capture",
        help="record the files changed between two revisions as a task's observed references",
        description="Record the files changed from --base to --head (default: the working tree at HEAD) "
                    "as observed references of TASK in CODE.md, as a DONE write does by itself from "
                    "the task's Claimed at commit. Files already recorded are skipped. Output is one JSON line.")
    capture.add_argument("task", help="a T- task id")
    capture.add_argument("--base", required=True, help="the revision the task's work starts from")
    capture.add_argument("--head", help="the revision it ends at; absent means the working tree. With "
                         "--head named the caller vouches for the range: no other task's claim makes it unresolved")
    capture.add_argument("--dry-run", action="store_true", help="report the change without writing")
    capture.set_defaults(handler=scope.command)

    answer = subparsers.add_parser(
        "respond", help="append the owner's response to RESPONSES.md"
    )
    answer.add_argument("target", nargs="?", help="an assumption (A-…) or a task (T-…)")
    answer.add_argument("action", nargs="?", type=str.upper,
                        choices=("CONFIRM", "REVISE", "REJECT", "GUIDE", "VERIFY"))
    answer.add_argument("--by", help=argparse.SUPPRESS)
    answer.add_argument("--text-file", help="read the response text from a file instead of stdin")
    answer.add_argument("--batch", help="a JSON list of {target, action, text}, written all or nothing")
    answer.add_argument("--via", default="cli", help="how it arrived: cli, dashboard, or relayed by …")
    answer.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    answer.add_argument("--delegate", metavar="AGENT",
                        help="VERIFY as this agent on the owner's authority, under a grant of verify "
                             "in AUTHORITY.md; never for a task the agent built")
    answer.set_defaults(handler=cmd_respond)

    granted = subparsers.add_parser(
        "authority", help="what agents may do on the owner's authority: list, or (owner, in person) grant and revoke")
    granted.add_argument("--json", action="store_true")
    granted_commands = granted.add_subparsers(dest="authority_command")
    give = granted_commands.add_parser("grant", help="owner only, in person: switch a capability on for an agent")
    give.add_argument("capability", help="the capability, e.g. verify")
    give.add_argument("--scope", required=True,
                      help="`project` for the whole project, or task, module and phase ids, comma-separated")
    give.add_argument("--delegate", required=True, help="the agent the grant is for")
    give.add_argument("--until", help="YYYY-MM-DD the grant ends; absent means it holds until revoked")
    take = granted_commands.add_parser("revoke", help="owner only, in person: end a grant now")
    take.add_argument("grant", help="an AG- grant id")
    granted.set_defaults(handler=cmd_authority)

    shelve = subparsers.add_parser(
        "archive",
        help="owner only: archive eligible lifecycle records for a COMPLETE phase or P-NONE (ADR-070)",
    )
    shelve.add_argument("phase", help="a COMPLETE phase or P-NONE, or plan/apply/restore followed by one")
    shelve.add_argument("selection", nargs="?", help="phase for archive plan/apply/restore, e.g. P3")
    shelve.add_argument("--plan", dest="plan_digest", metavar="DIGEST",
                        help="apply only if the locked chronicle matches this plan digest")
    shelve.add_argument("--dry-run", action="store_true", help="show what would move; write nothing")
    shelve.add_argument("--json", action="store_true")
    shelve.set_defaults(handler=cmd_archive)

    packet = subparsers.add_parser(
        "context", help="emit an agent context packet: orientation, or one task's"
    )
    packet.add_argument("task", nargs="?", help="omit for the project orientation packet")
    # A coordinator's packet carries its own conventions from COORDINATION.md (ADR-075).
    packet.add_argument("--role", choices=("builder", "reviewer", "coordinator"), default="builder")
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
        start = (Path(args.directory) if args.directory else Path.cwd()).resolve()
        if args.command == "archive":
            # A stale apply must reach the locked check even if TASKS.md was removed.
            root = next((p for p in (start, *start.parents) if (p / layout.AUTHORITY_DIR).is_dir()), None)
            if root is None:
                root = compiler.locate(start)
        else:
            root = compiler.locate(start)
    except compiler.LayoutError as error:
        if args.command == "archive":
            if args.json:
                print(json.dumps({"ok": False, "error": {"code": "invalid-project", "message": str(error)},
                                  "written": False, "refreshed": False}, separators=(",", ":")))
                return 1
            return _fail(f"invalid-project: {error}")
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
