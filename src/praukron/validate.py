"""Cross-document validation.

Errors are structural facts that make compiled state untrustworthy. Warnings
are conditions worth seeing that do not. Neither is a matter of taste: a
reviewer's preference never becomes a finding here.
"""

from __future__ import annotations

import re
from pathlib import Path

from .layout import ARCHIVE_DIR, AUTHORITY_DIR, RECOVERY_DIR
from .snapshot import Snapshot
from .model import (
    ASSUMPTION_STATUSES,
    CRITERION_STATES,
    IMPACTS,
    NOTIFY_CHANNELS,
    POLICY_GROUPS,
    RESPONSE_ACTIONS,
    RESPONSE_STATUS,
    TASK_AUTHORITIES,
    DEBT_STATUSES,
    DECISION_ORIGINS,
    DOMAINS,
    TRIGGER_STATES,
    EVENT_OUTCOMES,
    EVENT_TYPES,
    EVIDENCE_CLASSES,
    GATE_STATUSES,
    NO_PHASE,
    PHASE_STATUSES,
    STATUSES,
    VALIDATIONS,
    Finding,
    Project,
)


def _cycles(project: Project) -> list[list[str]]:
    """Every dependency cycle, each reported once from its lowest member."""
    graph = {task.id: task.dependencies for task in project.tasks}
    found: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    state: dict[str, int] = {}

    def walk(node: str, trail: list[str]) -> None:
        state[node] = 1
        for dependency in graph.get(node, []):
            if dependency not in graph:
                continue
            if state.get(dependency) == 1:
                cycle = trail[trail.index(dependency) :] + [dependency]
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    found.append(cycle)
            elif state.get(dependency, 0) == 0:
                walk(dependency, [*trail, dependency])
        state[node] = 2

    for task_id in graph:
        if state.get(task_id, 0) == 0:
            walk(task_id, [task_id])
    return found


# A backticked, repository-relative file path: at least one directory, and a
# last segment with an extension. Branch names (`feature/work`), commands, and
# directories do not match, which keeps the check quiet about prose.
_CITED_PATH = re.compile(r"`((?:[\w.\-]+/)+[\w\-][\w.\-]*\.[A-Za-z0-9]+)`")


def stale_references(project: Project) -> list[Finding]:
    """Paths the current-state documents cite that no longer exist.

    Handoff and intent are prose, so nothing else resolves what they point at;
    the continuity pilot found a handoff sending agents to a moved path. This
    This prose-path check reads the filesystem and only warns.
    """
    root = Path(project.root)
    authority = root / AUTHORITY_DIR
    findings: list[Finding] = []
    for name, text in (("HANDOFF.md", project.handoff), ("INTENT.md", project.intent)):
        seen: set[str] = set()
        for cited in _CITED_PATH.findall(text):
            if cited in seen or cited.startswith("../") or "//" in cited:
                continue
            seen.add(cited)
            if not ((root / cited).exists() or (authority / cited).exists()):
                findings.append(
                    Finding("warning", "stale-reference", f"cites `{cited}`, which does not exist", name)
                )
    return findings


# Values that look like credentials. A trace is evidence for review; it must
# never become the place a token is published (ADR-045).
_SECRET = re.compile(
    r"(ghp_|gho_|ghs_|github_pat_|xox[abprs]-|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_\-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")


def domain_findings(project: Project) -> list[Finding]:
    """Domain validity and execution structure (ADR-045)."""
    findings: list[Finding] = []
    exit_authorities = {p.exit_authority: p.id for p in project.phases if p.exit_authority}
    for task in project.tasks:
        where = f"{task.source.file}#{task.id}"
        if task.declared_domain and task.declared_domain not in DOMAINS:
            findings.append(Finding(
                "error", "invalid-domain",
                f"'{task.declared_domain}' is not a domain; use execution or operations", where,
            ))
        elif task.domain_source == "unresolved":
            findings.append(Finding(
                "warning", "ambiguous-domain",
                "phase-independent task with no Domain; counted as execution until one is declared",
                where,
            ))
        if not task.execution and not task.phase_independent:
            findings.append(Finding(
                "warning", "operations-in-phase",
                f"operations task names phase {task.phase}; it is not counted in that phase's progress",
                where,
            ))
        if not task.execution and task.id in exit_authorities:
            findings.append(Finding(
                "warning", "operations-exit-authority",
                f"operations task is the exit authority of {exit_authorities[task.id]}", where,
            ))
    return findings


def trace_findings(project: Project) -> list[Finding]:
    """Trace consistency. Strict about the trace's own structure, lenient about
    what it names, so a project can start recording without its history first
    being perfect."""
    findings: list[Finding] = []
    ids: set[str] = set()
    known = (
        {t.id for t in project.tasks}
        | {g.id for g in project.gates}
        | {p.id for p in project.phases}
        | {d.id for d in project.decisions}
        | set(project.contracts)
        | {c.id for contract in project.contracts.values() for c in contract.criteria}
        | {a.id for a in project.assumptions}
        | {d.id for d in project.debts}
        | {e.id for e in project.events}
    )
    event_ids = {e.id for e in project.events}
    for event in project.events:
        where = f"{event.source.file}#{event.id}"
        if event.id in ids:
            findings.append(Finding("error", "duplicate-event", f"event {event.id} is defined more than once", where))
        ids.add(event.id)
        if event.type not in EVENT_TYPES:
            findings.append(Finding("error", "invalid-event-type", f"'{event.type}' is not an event type", where))
        if event.outcome not in EVENT_OUTCOMES:
            findings.append(Finding("error", "invalid-event-outcome", f"'{event.outcome}' is not an outcome", where))
        for field_name, reference in (("Parent", event.parent), ("Retry of", event.retry_of)):
            if reference and reference not in event_ids:
                findings.append(Finding(
                    "error", "unknown-event-reference", f"{field_name} names {reference}, which is not an event", where,
                ))
            if reference == event.id:
                findings.append(Finding("error", "self-referencing-event", f"{field_name} names the event itself", where))
        if event.task and event.task not in {t.id for t in project.tasks}:
            findings.append(Finding("warning", "unknown-event-task", f"names task {event.task}, which does not exist", where))
        for reference in event.affects:
            if reference not in known:
                findings.append(Finding("warning", "unknown-event-affects", f"affects {reference}, which is not in the chronicle", where))
        if event.time and not _TIME.match(event.time):
            findings.append(Finding("warning", "invalid-event-time", f"'{event.time}' is not an ISO date or time", where))
        text = " ".join(filter(None, (
            event.title, event.tool, event.target, event.error, event.artifact, event.evidence, event.agent,
        )))
        if _SECRET.search(text):
            findings.append(Finding(
                "warning", "possible-secret", "event text looks like it contains a credential; remove it", where,
            ))
    return findings


def debt_findings(project: Project) -> list[Finding]:
    """Technical debt lifecycle and lineage (ADR-046)."""
    findings: list[Finding] = []
    task_ids = {t.id for t in project.tasks}
    decision_ids = {d.id for d in project.decisions}
    seen: set[str] = set()
    for debt in project.debts:
        where = f"{debt.source.file}#{debt.id}"

        def error(code: str, message: str) -> None:
            findings.append(Finding("error", code, message, where))

        def warn(code: str, message: str) -> None:
            findings.append(Finding("warning", code, message, where))

        if debt.id in seen:
            error("duplicate-debt", f"debt {debt.id} is defined more than once")
        seen.add(debt.id)
        if debt.status not in DEBT_STATUSES:
            error("invalid-debt-status", f"'{debt.status}' is not a debt status")
        if debt.trigger_state not in TRIGGER_STATES:
            error("invalid-trigger-state", f"'{debt.trigger_state}' is not a trigger state")
        if not debt.debt:
            error("debt-without-description", "debt states no Debt")
        if not debt.exit_condition:
            error("debt-without-exit", "debt states no Exit condition, so it can never be resolved")
        if not debt.trigger and debt.status not in ("OPEN", "RESOLVED", "INVALIDATED"):
            warn("debt-without-trigger", "carried debt states no Trigger for when it stops being acceptable")
        if debt.status == "SCHEDULED" and not debt.linked_tasks:
            error("scheduled-debt-without-task", "SCHEDULED debt links no repayment task")
        if debt.closed and not debt.resolution:
            error("closed-debt-without-resolution", f"{debt.status} debt records no Resolution")
        for reference in debt.introduced_by:
            if reference.startswith("ADR-") and reference not in decision_ids:
                error("unknown-debt-reference", f"introduced by {reference}, which has no ADR file")
            elif reference.startswith("T-") and reference not in task_ids:
                error("unknown-debt-reference", f"introduced by {reference}, which is not a task")
        for task_id in debt.linked_tasks:
            if task_id not in task_ids:
                error("unknown-debt-reference", f"links {task_id}, which is not a task")
        if debt.status == "RESOLVED":
            open_tasks = [t for t in debt.linked_tasks if project.task(t) and not project.task(t).done]
            if open_tasks:
                warn("resolved-debt-open-task", f"RESOLVED while {', '.join(open_tasks)} is still open")
        if debt.trigger_state == "REACHED" and debt.status in ("OPEN", "ACCEPTED"):
            warn("debt-trigger-reached", "trigger reached but no repayment is scheduled")
    return findings


_RECORD_ID = re.compile(r"^(ADR-[0-9]+|AC-[A-Za-z0-9.\-]+|T-[A-Za-z0-9.\-]+)$")


def assumption_findings(project: Project) -> list[Finding]:
    """Assumptions, owner responses, owner-held tasks, and the policy (ADR-054, ADR-057)."""
    findings: list[Finding] = []
    task_ids = {t.id for t in project.tasks}
    decision_ids = {d.id for d in project.decisions}
    criterion_ids = {c.id for contract in project.contracts.values() for c in contract.criteria}
    contract_ids = set(project.contracts)
    assumption_ids = {a.id for a in project.assumptions}
    responses = {r.id: r for r in project.responses}
    policy = project.policy

    def known(reference: str) -> bool:
        if reference.startswith("ADR-"):
            return reference in decision_ids
        if reference.startswith("AC-"):
            return reference in criterion_ids or reference in contract_ids
        return reference in task_ids

    seen: set[str] = set()
    for a in project.assumptions:
        where = f"{a.source.file}#{a.id}"

        def error(code: str, message: str) -> None:
            findings.append(Finding("error", code, message, where))

        if a.id in seen:
            error("duplicate-assumption", f"assumption {a.id} is defined more than once")
        seen.add(a.id)
        if a.status not in ASSUMPTION_STATUSES:
            error("invalid-assumption-status", f"'{a.status}' is not an assumption status")
        if a.impact not in IMPACTS:
            error("invalid-impact", f"'{a.impact}' is not LOW, MEDIUM, or HIGH")
        if not a.tasks:
            error("assumption-without-task", "assumption names no task")
        for task_id in a.tasks:
            if task_id not in task_ids:
                error("unknown-assumption-reference", f"names task {task_id}, which does not exist")
        for reference in a.references:
            if not known(reference):
                error("unknown-assumption-reference", f"applied in {reference}, which does not exist")
        for reference in a.reconciled_by:
            if _RECORD_ID.match(reference) and not known(reference):
                error("unknown-assumption-reference", f"reconciled by {reference}, which does not exist")
        for response_id in a.responses:
            response = responses.get(response_id)
            if response is None:
                error("unknown-response-reference", f"lists response {response_id}, which does not exist")
            elif response.target != a.id:
                error("unknown-response-reference",
                      f"lists {response_id}, which answers {response.target}, not {a.id}")
        wanted = {v: k for k, v in RESPONSE_STATUS.items()}.get(a.status)
        if wanted and not any(r.action == wanted for r in project.responses_to(a.id)):
            error("status-without-response",
                  f"{a.status} but RESPONSES.md holds no {wanted} for {a.id}")
        if a.status in ("REVISED", "REJECTED", "WITHDRAWN") and not a.reconciled_by:
            error("missing-reconciliation", f"{a.status} but names nothing in Reconciled by")
        if a.permissions and a.impact != "HIGH":
            error("permission-not-high", "a permission assumption must be Impact: HIGH")
        if a.permissions and "permission" in policy.reserved:
            source = policy.decision or "the default policy"
            error("reserved-assumption",
                  f"assumes a permission choice, which {source} reserves for the owner")

    previous = 0
    seen_responses: set[str] = set()
    for r in project.responses:
        where = f"{r.source.file}#{r.id}"

        def error(code: str, message: str) -> None:
            findings.append(Finding("error", code, message, where))

        if r.id in seen_responses:
            error("duplicate-response", f"response {r.id} is defined more than once")
        seen_responses.add(r.id)
        number = int(r.id.split("-")[1])
        if number <= previous:
            findings.append(Finding("warning", "response-order",
                                    f"{r.id} follows R-{previous}; responses are append-only", where))
        previous = max(previous, number)
        if r.action not in RESPONSE_ACTIONS:
            error("invalid-response-action", f"'{r.action}' is not CONFIRM, REVISE, REJECT, or GUIDE")
        if r.target.startswith("A-"):
            if r.target not in assumption_ids:
                error("unknown-response-target", f"answers {r.target}, which does not exist")
        elif r.target.startswith("T-"):
            if r.target not in task_ids:
                error("unknown-response-target", f"answers {r.target}, which does not exist")
            elif r.action in RESPONSE_STATUS:
                error("invalid-response-action", f"{r.action} applies to assumptions; a task takes GUIDE")
        else:
            error("unknown-response-target", f"'{r.target}' is neither an assumption nor a task")
        for field, value in (("By", r.by), ("Date", r.date), ("Via", r.via)):
            if not value:
                error("incomplete-response", f"response records no {field}")
        if not r.text.strip():
            error("incomplete-response", "response holds no quoted text")

    for task in project.tasks:
        if task.authority is None:
            continue
        where = f"{task.source.file}#{task.id}"
        if task.authority not in TASK_AUTHORITIES:
            findings.append(Finding("error", "invalid-task-authority",
                                    f"'{task.authority}' is not a task authority; use owner", where))
        elif task.done and task.validation != "HUMAN_VERIFIED":
            findings.append(Finding("error", "owner-task-not-verified",
                                    "an Authority: owner task is DONE only with HUMAN_VERIFIED", where))

    for decision in project.decisions:
        if (decision.policy or "").lower() != "assumptions":
            continue
        where = f"{decision.source.file}#{decision.id}"
        if decision.reserved is None:
            findings.append(Finding("error", "incomplete-policy", "policy ADR states no Reserved", where))
        for group in decision.reserved or []:
            if group not in POLICY_GROUPS and group != "none":
                findings.append(Finding("error", "invalid-policy-group",
                                        f"'{group}' is not one of {', '.join(POLICY_GROUPS)}", where))
        if decision.notify and decision.notify not in NOTIFY_CHANNELS:
            findings.append(Finding("error", "invalid-policy-notify",
                                    f"'{decision.notify}' is not host or none", where))
    return findings


def journal_findings(project: Project, snapshot: Snapshot | None = None) -> list[Finding]:
    """ADR-072 diagnostics warn without blocking unrelated record writes.

    Archive/restore use this same analysis to keep only the affected journal
    entries. A captured/proposed authority must never be checked against disk.
    """
    from .archive import (_JOURNAL, _JOURNAL_ORDER, _NAMED, _ORDER_REPAIR,
                          _journal_order_state)

    if snapshot is None and project.journal_validation is not None:
        return list(project.journal_validation)
    try:
        captured = snapshot if snapshot is not None else Snapshot.capture_journals(
            Path(project.root) / AUTHORITY_DIR)
        state = _journal_order_state(captured)
    except (OSError, UnicodeError) as error:
        findings = [Finding("warning", "invalid-journal-order", f"cannot read journal input: {error}. {_ORDER_REPAIR}",
                            _JOURNAL_ORDER)]
    else:
        findings = [Finding("warning", "invalid-journal-order", message, _JOURNAL_ORDER)
                    for message in dict.fromkeys(state.warnings)]
        findings.extend(Finding("warning", "duplicate-journal-heading",
                                f"heading appears {count} times across working/archived journals: {heading}; "
                                "reconcile the duplicate before restoring it", f"JOURNAL.md#{heading}")
                        for heading, count in state.counts.items() if count > 1)
        known = ({task.id for task in project.tasks} | set(project.contracts)
                 | {criterion.id for contract in project.contracts.values()
                    for criterion in contract.criteria}
                 | {decision.id for decision in project.decisions}
                 | {assumption.id for assumption in project.assumptions}
                 | {debt.id for debt in project.debts}
                 | {event.id for event in project.events})
        for name, text in state.files.items():
            for heading, start, end in state.sections[name]:
                where = f"{name}#{heading}"
                if name.casefold().startswith(f"{ARCHIVE_DIR}/") and not _JOURNAL.fullmatch(heading):
                    findings.append(Finding("error", "invalid-journal-heading",
                                            "archived journal heading must read '## YYYY-MM-DD — <title>'", where))
                for reference in dict.fromkeys(_NAMED.findall(text[start:end])):
                    if reference not in known:
                        findings.append(Finding("warning", "unknown-journal-reference",
                                                f"names {reference}, which does not exist", where))
    if snapshot is None:
        project.journal_validation = list(findings)
    return findings


def archive_findings(project: Project, snapshot: Snapshot | None = None) -> list[Finding]:
    """Archive paths and placement that the normal record parsers cannot express."""
    from .archive import RECORD_RULES

    allowed = {rule.filename.casefold(): rule.filename for rule in RECORD_RULES}
    findings: list[Finding] = []
    if snapshot is not None:
        names = [(name, snapshot.resolve(f"{ARCHIVE_DIR}/{name.split('/')[1]}/"
                                        f"{allowed.get(name.split('/')[2].casefold(), '')}") == name)
                 for name in snapshot.files
                 if len(name.split("/")) == 3
                 and name.split("/")[0].casefold() == ARCHIVE_DIR
                 and name.split("/")[2].casefold().endswith(".md")]
        # Anything else inside archive/<phase>/ is refused by restore: other
        # extensions, nested files and empty directories. Names only, no bytes.
        refused = {"/".join(name.split("/")[:3]) for name in [*snapshot.files, *snapshot.directories]
                   if len(name.split("/")) > 3 and name.split("/")[0].casefold() == ARCHIVE_DIR}
        refused |= {name for name in snapshot.files
                    if len(name.split("/")) == 3 and name.split("/")[0].casefold() == ARCHIVE_DIR
                    and not name.split("/")[2].casefold().endswith(".md")}
        refused |= {name for name in snapshot.directories
                    if len(name.split("/")) == 3 and name.split("/")[0].casefold() == ARCHIVE_DIR}
    else:
        refused = set()
        names = []
        archive_root = Path(project.root) / AUTHORITY_DIR / ARCHIVE_DIR
        try:
            phases = tuple(archive_root.iterdir())
        except FileNotFoundError:
            phases = ()
        except OSError as error:
            findings.append(Finding("error", "invalid-archive-input",
                                    f"cannot inspect archive inputs: {error}", ARCHIVE_DIR))
            phases = ()
        for phase in phases:
            try:
                records = tuple(phase.iterdir())
            except NotADirectoryError:
                continue
            except OSError as error:
                where = phase.relative_to(archive_root.parent).as_posix()
                findings.append(Finding("error", "invalid-archive-input",
                                        f"cannot inspect archive inputs: {error}", where))
                continue
            for record in records:
                try:
                    if record.suffix.casefold() != ".md" or record.is_dir():
                        refused.add(record.relative_to(archive_root.parent).as_posix())
                        continue
                    if record.is_file():
                        expected = phase / allowed.get(record.name.casefold(), "")
                        names.append((record.relative_to(archive_root.parent).as_posix(),
                                      expected.is_file() and expected.samefile(record)))
                except OSError as error:
                    where = record.relative_to(archive_root.parent).as_posix()
                    findings.append(Finding("error", "invalid-archive-input",
                                            f"cannot inspect archive inputs: {error}", where))
    for name in sorted(refused):
        findings.append(Finding("warning", "unsupported-archive-path",
                                "restore refuses this path (unsupported archive path); remove it or move it "
                                "out of archive/ before relying on restore", name))
    for name, parsed in names:
        parts = name.split("/")
        expected = allowed.get(parts[2].casefold())
        if expected is None:
            findings.append(Finding("error", "invalid-archive-file",
                                    "archive files must be known record files under archive/<phase>/", name))
        elif not parsed:
            findings.append(Finding("warning", "case-mismatched-archive-filename",
                                    f"loader expects archive/<phase>/{expected}; this file is ignored", name))
    for task in project.tasks:
        parts = task.source.file.split("/")
        if len(parts) == 3 and parts[0].casefold() == ARCHIVE_DIR and parts[1] != task.phase:
            findings.append(Finding("warning", "wrong-archive-phase",
                                    f"task phase is {task.phase}, but it is stored in archive/{parts[1]}",
                                    f"{task.source.file}#{task.id}"))
    return findings


def check(project: Project, *, snapshot: Snapshot | None = None) -> list[Finding]:
    findings: list[Finding] = []

    recovery = Path(project.root) / RECOVERY_DIR
    for record in sorted(recovery.glob("archive-*.json")):
        findings.append(Finding(
            "error", "interrupted-archive",
            "an interrupted archive recovery record remains; run archive or respond to roll it back",
            record.relative_to(project.root).as_posix(),
        ))

    for record in sorted(recovery.glob("write-*.json")):
        findings.append(Finding(
            "error", "interrupted-write",
            "an interrupted write recovery record remains; run a write command to roll it back",
            record.relative_to(project.root).as_posix(),
        ))

    def error(code: str, message: str, where: str) -> None:
        findings.append(Finding("error", code, message, where))

    def warn(code: str, message: str, where: str) -> None:
        findings.append(Finding("warning", code, message, where))

    seen: set[str] = set()
    phase_ids = {phase.id for phase in project.phases} | {NO_PHASE}
    decision_ids = {decision.id for decision in project.decisions}
    task_ids = {task.id for task in project.tasks}
    module_ids = {module.id for module in project.modules}

    # The hierarchy (ADR-035): one thesis, modules in known phases, and tasks
    # owned by modules. A chronicle that has not adopted it yet stays readable
    # and compilable, with the conversion made visible (ADR-036).
    thesis_at = f"{project.thesis.source.file}#{project.thesis.source.anchor}"
    if not project.thesis.statement:
        if project.uses_modules:
            error("missing-thesis", "modules exist but THESIS.md has no Statement", thesis_at)
        elif project.tasks:
            warn("missing-thesis",
                 "no product thesis; author THESIS.md before converting tasks to modules",
                 thesis_at)
    seen_modules: set[str] = set()
    for module in project.modules:
        where = f"{module.source.file}#{module.id}"
        if module.id in seen_modules:
            error("duplicate-module", f"module {module.id} is defined more than once", where)
        seen_modules.add(module.id)
        if module.phase not in phase_ids:
            error("unknown-module-phase", f"module names unknown phase '{module.phase}'", where)

    for task in project.tasks:
        where = f"{task.source.file}#{task.id}"
        if task.module:
            if task.module not in module_ids:
                error("unknown-module", f"task names unknown module '{task.module}'", where)
            if task.legacy_phase:
                error("duplicate-phase-authority",
                      "task authors both Module and Phase; its phase comes from its module",
                      where)
        else:
            warn("legacy-task-phase",
                 f"task names phase {task.legacy_phase} directly; convert it to a Module", where)
        if task.id in seen:
            error("duplicate-task", f"task {task.id} is defined more than once", where)
        seen.add(task.id)

        if task.status not in STATUSES:
            error("invalid-status", f"'{task.status}' is not a known status", where)
        if task.validation not in VALIDATIONS:
            error(
                "invalid-validation",
                f"'{task.validation}' is not a known validation state",
                where,
            )
        if not task.module and task.phase not in phase_ids:
            error("unknown-phase", f"task names unknown phase '{task.phase}'", where)

        for dependency in task.dependencies:
            if dependency not in task_ids:
                error(
                    "unknown-dependency",
                    f"depends on {dependency}, which does not exist",
                    where,
                )
        for decision in task.decisions:
            if decision not in decision_ids:
                error(
                    "unknown-decision",
                    f"governed by {decision}, which has no ADR file",
                    where,
                )

        if not task.contract:
            error("missing-contract", "task has no AC reference", where)
        elif task.contract not in project.contracts:
            error(
                "unknown-contract",
                f"AC reference {task.contract} has no contract",
                where,
            )
        elif not project.contracts[task.contract].criteria:
            severity = error if task.done else warn
            severity(
                "empty-contract",
                f"contract {task.contract} states no criteria, so it sets no bar",
                where,
            )
        elif task.done:
            unresolved = [
                criterion.id
                for criterion in project.mandatory_criteria(task)
                if criterion.state != "PASS"
            ]
            if unresolved:
                error(
                    "unresolved-acceptance",
                    f"DONE with {len(unresolved)} unmet criteria: "
                    f"{', '.join(unresolved[:4])}",
                    where,
                )

        if not task.done and task.evidence:
            warn("premature-evidence", f"{task.status} task carries evidence", where)
        if task.schedule.start and not (task.schedule.end or task.schedule.estimate):
            warn(
                "partial-schedule",
                "scheduled task has no estimate or end date",
                where,
            )

    criterion_ids: set[str] = set()
    for contract in project.contracts.values():
        where = f"{contract.source.file}#{contract.id}"
        for inherited in contract.inherits:
            if inherited not in project.contracts:
                error(
                    "unknown-inheritance",
                    f"inherits {inherited}, which is not defined",
                    where,
                )
        for criterion in contract.criteria:
            if criterion.id in criterion_ids:
                error(
                    "duplicate-criterion",
                    f"criterion {criterion.id} is defined more than once",
                    where,
                )
            criterion_ids.add(criterion.id)
            if criterion.evidence_class not in EVIDENCE_CLASSES:
                error(
                    "invalid-evidence-class",
                    f"'{criterion.evidence_class}' is not a known evidence class",
                    where,
                )
            if criterion.state not in CRITERION_STATES:
                error(
                    "invalid-criterion-state",
                    f"'{criterion.state}' is not a known criterion state",
                    where,
                )
        if not contract.is_global and contract.id not in {
            task.contract for task in project.tasks
        }:
            warn("orphan-contract", "contract is referenced by no task", where)

    for cycle in _cycles(project):
        error(
            "dependency-cycle",
            " → ".join(cycle),
            f"{project.tasks[0].source.file}#{cycle[0]}" if project.tasks else "TASKS.md",
        )

    for phase in project.phases:
        where = f"{phase.source.file}#{phase.id}"
        if phase.status not in PHASE_STATUSES:
            error("invalid-phase-status", f"'{phase.status}' is not a known status", where)
        if phase.exit_authority and phase.exit_authority not in task_ids:
            error(
                "missing-exit-authority",
                f"exit authority {phase.exit_authority} is not a task",
                where,
            )
        if not phase.exit_authority and phase.status in {"ACTIVE", "EXIT_PENDING"}:
            warn("no-exit-authority", "active phase has no exit authority", where)

    for gate in project.gates:
        where = f"{gate.source.file}#{gate.id}"
        if gate.status not in GATE_STATUSES:
            error("invalid-gate-status", f"'{gate.status}' is not GREEN or RED", where)
        for reference in gate.verified_by:
            known = reference in project.contracts or reference in criterion_ids
            if not known:
                error(
                    "unknown-gate-reference",
                    f"verified by {reference}, which does not exist",
                    where,
                )

    for milestone in project.milestones:
        if milestone.task and milestone.task not in task_ids:
            error(
                "unknown-milestone-task",
                f"milestone names {milestone.task}, which does not exist",
                f"{milestone.source.file}#{milestone.id}",
            )

    # A reconstructed decision claims something the chronicle never observed.
    # It must say what it was inferred from, and it binds work only once a
    # person has confirmed it (ADR-037).
    for decision in project.decisions:
        where = f"{decision.source.file}#{decision.id}"
        if decision.origin not in DECISION_ORIGINS:
            error(
                "invalid-origin",
                f"'{decision.origin}' is not a known decision origin",
                where,
            )
        if not decision.reconstructed:
            continue
        if not decision.evidence:
            error(
                "reconstruction-without-evidence",
                "reconstructed decision cites no Evidence it was inferred from",
                where,
            )
        if decision.status == "ACCEPTED" and not decision.authority:
            error(
                "unconfirmed-reconstruction",
                "reconstructed decision is ACCEPTED but names no confirming Authority",
                where,
            )

    active = [phase for phase in project.phases if phase.status == "ACTIVE"]
    if len(active) > 1:
        error(
            "multiple-active-phases",
            f"{len(active)} phases are ACTIVE: {', '.join(p.id for p in active)}",
            "PHASES.md",
        )

    findings.extend(domain_findings(project))
    findings.extend(trace_findings(project))
    findings.extend(debt_findings(project))
    findings.extend(assumption_findings(project))
    findings.extend(stale_references(project))
    findings.extend(journal_findings(project, snapshot))
    findings.extend(archive_findings(project, snapshot))
    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.severity == "error"]
