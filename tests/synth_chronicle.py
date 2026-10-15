"""A synthetic large chronicle for archive parity tests and benchmarks (T-ARCH-DOCS-01).

Every record type, many phases, P-NONE work, Vietnamese text and either line ending.
Nothing here is copied from a real project.
"""

from __future__ import annotations

from pathlib import Path

DAY = "2026-10-{:02d}"


def _eol(text: str, newline: str) -> str:
    return text.replace("\n", newline)


def phases(count: int, active: str) -> str:
    out = ["# Phases\n"]
    for n in range(1, count + 1):
        status = "ACTIVE" if f"P{n}" == active else "COMPLETE"
        out.append(f"## P{n} — Giai đoạn {n}\n\nOutcome:\nKết quả {n}.\n\nEntry:\n- Sẵn sàng.\n\n"
                   f"Exit:\n- Xong {n}.\n\nExit authority:\nT-P{n}-01\n\nStatus:\n{status}\n")
    out.append("---\n\n# Gates\n\n## Gate A — Single authority\n\nOne source of truth.\n\n"
               "Blocks: P1 exit\nVerified by: `AC-GLOBAL-CARE`\nStatus: GREEN\n\n---\n\n# Milestones\n\n"
               "- `M-FIRST` — First light. Reached when T-P1-01 is `DONE`.\n")
    return "\n".join(out)


def build(root: Path, *, phases_count: int = 6, tasks_per_phase: int = 6, pnone_tasks: int = 6,
          newline: str = "\n") -> Path:
    """Write `<root>/.praukron/chronicle`. Phases 1..N-1 are COMPLETE, the last is ACTIVE.

    Each COMPLETE phase holds DONE tasks with contracts, a closed assumption, a resolved
    debt, a successful trace event and journal entries; one task per phase stays open on
    purpose (a TODO or an OPEN assumption) so something always stays behind.
    """
    authority = root / ".praukron" / "chronicle"
    (authority / "ADR").mkdir(parents=True)
    active = f"P{phases_count}"
    tasks, contracts, assumptions, debts, events, journal, modules, shared = [], [], [], [], [], [], [], []
    for n in range(1, phases_count + 1):
        modules.append(f"## M-P{n} — Mô-đun {n}\n- Phase: P{n}\n- Outcome: Mô-đun {n} chạy.\n")
        last_done = ""
        for k in range(1, tasks_per_phase + 1):
            tid = f"T-P{n}-{k:02d}"
            open_task = k == tasks_per_phase and f"P{n}" != active  # kept by an OPEN assumption
            status = "DONE" if f"P{n}" != active and not open_task else "TODO"
            dep = last_done or "none"
            tasks.append(f"## {tid}: Việc {k} của giai đoạn {n}\n- Status: {status}\n- Module: M-P{n}\n"
                         f"- Validation: {'SYNTHETIC' if status == 'DONE' else 'UNTESTED'}\n"
                         f"- Dependencies: {dep}\n- Owner: someone\n- AC: AC-{tid}\n"
                         f"- Evidence: {'Đã kiểm tra.' if status == 'DONE' else '—'}\n- Governed by: ADR-001\n")
            contracts.append(f"## AC-{tid} — Việc {k}\n\nInherits: `AC-GLOBAL-CARE`\n\n"
                             f"- `AC-{tid}-01` — Given a site, When it is built, Then it stands "
                             f"(giữ vững). `TEST` · `{'PASS' if status == 'DONE' else 'NOT_RUN'}`\n"
                             f"  - Evidence: {'đo tại chỗ' if status == 'DONE' else 'chưa chạy'}\n")
            if status == "DONE":
                last_done = tid
            if k == 1 and status == "DONE":
                assumptions.append(f"## A-P{n}: Giả định {n}\n- Status: CONFIRMED\n- Tasks: {tid}\n- Impact: LOW\n"
                                   f"- Assumption: Mọi thứ ổn định.\n- Responses: R-{n}\n")
                debts.append(f"## TD-P{n}: Nợ {n}\n- Status: RESOLVED\n- Debt: Đã trả.\n"
                             f"- Exit condition: Gỡ bỏ.\n- Linked tasks: {tid}\n- Resolution: Xong.\n")
                events.append(f"## EV-P{n}: Lệnh {n}\n- Type: command\n- Task: {tid}\n- Outcome: success\n")
                journal.append(f"## {DAY.format(n)} — Hoàn thành {tid}\n- Agent: claude/primary\n"
                               f"- Xong {tid}; xem A-P{n}, TD-P{n} và EV-P{n}.\n")
                journal.append(f"## {DAY.format(n)} — Ghi chú thứ hai về {tid}\n- Agent: claude/primary\n"
                               f"- Một mục khác cùng ngày về {tid}.\n")
                shared.append(f"## {DAY.format(30)} — Chung ngày, giai đoạn {n}\n- Agent: claude/primary\n"
                               f"- Cùng ngày với mọi giai đoạn; nêu {tid}.\n")
            if open_task:
                assumptions.append(f"## A-OPEN-P{n}: Giả định mở {n}\n- Status: OPEN\n- Tasks: {tid}\n"
                                   f"- Impact: LOW\n- Assumption: Còn chờ quyết định.\n")
    modules.append("## M-UPKEEP — Upkeep\n- Phase: P-NONE\n- Outcome: The site stays usable.\n")
    previous = ""
    for k in range(1, pnone_tasks + 1):
        tid = f"T-OPS-{k:02d}"
        tasks.append(f"## {tid}: Việc vận hành {k}\n- Status: DONE\n- Module: M-UPKEEP\n- Domain: operations\n"
                     f"- Validation: SYNTHETIC\n- Dependencies: {previous or 'none'}\n- Owner: someone\n"
                     f"- AC: AC-{tid}\n- Evidence: Chạy được.\n- Governed by: ADR-001\n")
        contracts.append(f"## AC-{tid} — Vận hành {k}\n\n- `AC-{tid}-01` — Given ops, When run, "
                         f"Then it works. `RUNTIME` · `PASS`\n  - Evidence: đã chạy\n")
        previous = tid
    journal.append(f"## {DAY.format(20)} — Vận hành {previous}\n- Agent: claude/primary\n- Xong {previous}.\n")
    journal.append(f"## {DAY.format(21)} — Ghi chú không gắn bản ghi\n- Agent: claude/primary\n"
                   f"- Mục này không nêu bản ghi nào nên ở lại.\n")
    journal.extend(shared)  # one date across every phase: the order file's job
    acceptance = ("# Acceptance\n\n## Inherited contracts\n\n### AC-GLOBAL-CARE\n\n- `AC-GLOBAL-CARE-01` — "
                  "Given anything, When it ships, Then it is careful.\n  `INSPECTION`\n\n---\n\n# Contracts\n\n"
                  + "\n".join(contracts))
    files = {
        "PHASES.md": phases(phases_count, active),
        "THESIS.md": "# Product thesis\n\n- Statement: Một tòa nhà để ở.\n- Source: docs/THESIS.md\n",
        "MODULES.md": "# Modules\n\n" + "\n".join(modules),
        "TASKS.md": "# Tasks\n\n" + "\n".join(tasks),
        "ACCEPTANCE.md": acceptance,
        "ASSUMPTIONS.md": "# Assumptions\n\n" + "\n".join(assumptions),
        "TECH_DEBT.md": "# Technical debt\n\n" + "\n".join(debts),
        "TRACE.md": "# Trace\n\n" + "\n".join(events),
        "JOURNAL.md": "# Journal\n\n" + "\n".join(journal),
        "RESPONSES.md": "# Responses\n\n" + "\n".join(
            f"## R-{n}: A-P{n} CONFIRM\n- By: owner\n- Date: 2026-10-15\n- Via: test\n\n> Đồng ý.\n"
            for n in range(1, phases_count)),
        "INTENT.md": "# Intent\n\nNo active intent.\n",
        "HANDOFF.md": "# Handoff\n\nNothing in flight.\n",
    }
    for name, text in files.items():
        (authority / name).write_bytes(_eol(text, newline).encode("utf-8"))
    (authority / "ADR" / "ADR-001.md").write_bytes(_eol(
        "# ADR-001: Do the simple thing\n- Date: 2026-01-01\n- Status: ACCEPTED\n- Supersedes: none\n"
        "- Affects: T-P1-01\n- Context: Complexity is expensive.\n- Decision: Prefer the simple thing.\n",
        newline).encode("utf-8"))
    return root
