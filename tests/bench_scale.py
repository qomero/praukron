"""Benchmark of agent read cost and write latency as a chronicle grows (T-PERF-SCALE-01).

Not collected by `unittest discover` (the name does not start with `test`).
Run it by hand:

    python tests/bench_scale.py --tasks 300 1200 3000 --markdown out.md

For each size it generates a synthetic chronicle deterministically (same
arguments, same bytes), builds its compiled views, and measures:

  - what an agent receives: INDEX.md, `context`, `context <task>`,
    `retrieve <task>`, `retrieve "<question>"`, `explain <task>`, `status`,
    and one structured write's payload and reply;
  - how long the tool takes: compile, graph, dashboard, validate, and one
    structured write including its validation and refresh of every view;
  - how large the compiled output is.

Bytes are UTF-8 bytes. Tokens are estimates: bytes / 4. Wall times are the
median of --runs runs of a fresh subprocess, so they include interpreter start.
Nothing here reads or changes a real chronicle.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / ".praukron" / "praukron"
TEMPLATES = ROOT / "templates" / "chronicle"
ESTIMATOR = "UTF-8 bytes / 4"
TASKS_PER_MODULE = 25
MODULES_PER_PHASE = 4
TASKS_PER_PHASE = TASKS_PER_MODULE * MODULES_PER_PHASE


def tokens(size: int) -> int:
    return -(-size // 4)


# ------------------------------------------------------------------ generator

EVIDENCE = ("python -m unittest discover -s tests: {n} tests pass; reviewed the diff against the "
            "contract and the governing decision; no regression in the neighbouring module.")


def generate(root: Path, tasks: int) -> dict:
    """Write a valid synthetic chronicle of `tasks` tasks under `root`."""
    authority = root / ".praukron" / "chronicle"
    shutil.copytree(TEMPLATES, authority)
    phases = -(-tasks // TASKS_PER_PHASE)
    active = phases  # the last phase is ACTIVE; every earlier one is COMPLETE
    in_active = tasks - (active - 1) * TASKS_PER_PHASE
    start = dt.date(2025, 1, 1)

    p = ["# Phases\n"]
    for i in range(1, phases + 1):
        status = "ACTIVE" if i == active else "COMPLETE"
        last = in_active if i == active else TASKS_PER_PHASE
        exit_task = f"T-{i:03d}-{(last - 1) // TASKS_PER_MODULE + 1}-{(last - 1) % TASKS_PER_MODULE + 1:02d}"
        p.append(f"""
## P{i} — Stage {i}

Outcome:
Capability {i} works end to end for its users.

Entry:
- Stage {i - 1} is complete.

Exit:
- Every module of stage {i} is done.

Exit authority:
{exit_task}

Status:
{status}

---
""")
    p.append("\n# Gates\n\nNo gates yet.\n\n---\n\n# Milestones\n\nNo milestones yet.\n")
    (authority / "PHASES.md").write_text("".join(p), encoding="utf-8")

    m, t, a, j = ["# Modules\n"], ["# Tasks\n"], ["# Acceptance\n\n# Contracts\n"], ["# Journal\n"]
    adr = authority / "ADR"
    made = 0
    ids: list[str] = []
    statuses: dict[str, str] = {}
    for ph in range(1, phases + 1):
        (adr / f"ADR-{ph:03d}.md").write_text(
            f"# ADR-{ph:03d}: Approach for stage {ph}\n- Date: {start + dt.timedelta(days=ph * 30)}\n"
            f"- Status: ACCEPTED\n- Supersedes: none\n- Affects: stage {ph}\n"
            f"- Context: Stage {ph} needs one agreed approach so its modules do not diverge.\n"
            f"- Decision: Build stage {ph} on the conventions of stage {ph - 1}, changing only what its "
            f"outcome requires.\n", encoding="utf-8")
        for mo in range(1, MODULES_PER_PHASE + 1):
            module = f"M-{ph:03d}-{mo}"
            m.append(f"\n## {module} — Stage {ph} part {mo}\n- Phase: P{ph}\n"
                     f"- Outcome: Part {mo} of capability {ph} is usable.\n")
            for k in range(1, TASKS_PER_MODULE + 1):
                if made == tasks:
                    break
                made += 1
                tid = f"T-{ph:03d}-{mo}-{k:02d}"
                deps = ids[-1] if k > 1 else (ids[-TASKS_PER_MODULE] if len(ids) >= TASKS_PER_MODULE else "none")
                position = made - (active - 1) * TASKS_PER_PHASE  # 1-based within the active phase
                if ph < active or position <= in_active // 2:
                    status, state = "DONE", "PASS"
                elif position <= in_active // 2 + 3:
                    status, state = "WIP", "NOT_RUN"
                else:
                    status, state = "TODO", "NOT_RUN"
                day = start + dt.timedelta(days=made // 3)
                done = status == "DONE"
                t.append(f"""
## {tid}: Deliver step {k} of stage {ph} part {mo}
- Status: {status}
- Module: {module}
- Validation: {"SYNTHETIC" if done else "UNTESTED"}
- Dependencies: {deps}
- Owner: {"builder" if status != "TODO" else "unassigned"}
{f"- Claimed: {day}{chr(10)}" if status != "TODO" else ""}- AC: AC-{tid}
- Evidence: {EVIDENCE.format(n=100 + made) if done else "—"}
- Governed by: ADR-{ph:03d}
""")
                crit = []
                for c, (text, klass) in enumerate((
                        ("Given the previous step, When this step ships, Then its behaviour is covered by a test "
                         "that fails on regression.", "TEST"),
                        ("Given the change, When it is reviewed against the decision, Then no rule of the "
                         "decision is broken.", "INSPECTION"),
                        ("Given the running system, When the step is exercised end to end, Then the observed "
                         "result matches the outcome.", "RUNTIME")), start=1):
                    ev = f"\n  - Evidence: {EVIDENCE.format(n=100 + made)}" if state == "PASS" else ""
                    crit.append(f"- `AC-{tid}-{c:02d}` — {text}\n  `{klass}` · `{state}`{ev}\n")
                a.append(f"\n## AC-{tid} — Deliver step {k} of stage {ph} part {mo}\n\n" + "\n".join(crit))
                if done:
                    j.append(f"\n## {day} — Closed {tid}\n\nAll three criteria pass with recorded evidence. "
                             f"The step builds on {deps} and leaves the next step of {module} ready. "
                             f"Nothing was deferred; the decision ADR-{ph:03d} held without exception.\n")
                ids.append(tid)
                statuses[tid] = status
    (authority / "MODULES.md").write_text("".join(m), encoding="utf-8")
    (authority / "TASKS.md").write_text("".join(t), encoding="utf-8")
    (authority / "ACCEPTANCE.md").write_text("".join(a), encoding="utf-8")
    (authority / "JOURNAL.md").write_text("".join(j), encoding="utf-8")
    (authority / "THESIS.md").write_text("# Product thesis\n\n- Statement: A synthetic project for "
                                         "measuring scale.\n- Source: tests/bench_scale.py\n", encoding="utf-8")
    (authority / "INTENT.md").write_text("# Intent\n\nContinue the active stage.\n", encoding="utf-8")
    (authority / "HANDOFF.md").write_text("# Handoff\n\nThree tasks are in progress.\n", encoding="utf-8")
    lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in authority.rglob("*.md"))
    size = sum(f.stat().st_size for f in authority.rglob("*.md"))
    first_todo = next(i for i in ids if statuses[i] == "TODO")
    return {"tasks": made, "phases": phases, "lines": lines, "bytes": size, "task": first_todo,
            "active": f"P{active}"}


# ---------------------------------------------------------------- measurement

class Project:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *argv: str, stdin: str | None = None) -> tuple[int, bytes, float]:
        started = time.perf_counter()
        done = subprocess.run([sys.executable, str(LAUNCHER), "-C", str(self.root), *argv],
                              input=stdin.encode("utf-8") if stdin is not None else None,
                              capture_output=True)
        return done.returncode, done.stdout, time.perf_counter() - started

    def timed(self, runs: int, *argv: str) -> float:
        times = []
        for _ in range(runs):
            code, out, seconds = self.run(*argv)
            if code != 0:
                raise SystemExit(f"{' '.join(argv)} exited {code}: {out[:300]!r}")
            times.append(seconds)
        return statistics.median(times)


def revision(project: Project, task: str) -> str:
    _, out, _ = project.run("explain", task)
    first = out.decode("utf-8", "replace").splitlines()[0]
    return first.rsplit("revision ", 1)[1].rstrip("]")


def measure(tasks: int, scratch: Path, runs: int) -> dict:
    root = scratch / f"chronicle-{tasks}"
    shape = generate(root, tasks)
    project = Project(root)
    times = {name: project.timed(runs, name) for name in ("compile", "graph", "dashboard")}
    times["validate"] = project.timed(runs, "validate", "--quiet")
    compiled = root / ".praukron" / "compiled"
    authority = root / ".praukron" / "chronicle"
    task = shape["task"]
    reads = {"INDEX.md": (authority / "INDEX.md").read_bytes()}
    for label, argv in (("context", ("context",)), ("context <task>", ("context", task)),
                        ("retrieve <task>", ("retrieve", task)),
                        ("retrieve \"<question>\"", ("retrieve", f"why is {shape['active']} not complete?")),
                        ("explain <task>", ("explain", task)), ("status", ("status",))):
        code, out, _ = project.run(*argv)
        if code != 0:
            raise SystemExit(f"{label} exited {code}: {out[:300]!r}")
        reads[label] = out
    payload = json.dumps({"id": task, "expectRevision": revision(project, task),
                          "set": {"status": "WIP", "owner": "bench", "claimed": "2026-10-27"}},
                         separators=(",", ":"))
    code, out, seconds = project.run("task", "update", "--input", "-", stdin=payload)
    reply = json.loads(out)
    if code != 0 or not reply.get("ok"):
        raise SystemExit(f"task update failed: {out[:300]!r}")
    write = {"payload": len(payload.encode("utf-8")), "reply": len(out), "seconds": seconds}
    journal = json.dumps({"title": "Bench entry", "body": "One journal entry for the scale benchmark."})
    code, out, jseconds = project.run("journal", "append", "--input", "-", stdin=journal)
    if code != 0:
        raise SystemExit(f"journal append failed: {out[:300]!r}")
    write["journal_seconds"] = jseconds
    sizes = {p.name: p.stat().st_size for p in sorted(compiled.iterdir()) if p.is_file()}
    return {"shape": shape, "times": times, "reads": {k: len(v) for k, v in reads.items()},
            "write": write, "compiled": sizes}


def render(results: list[dict], meta: dict) -> str:
    def row(label, cells):
        return f"| {label} | " + " | ".join(cells) + " |"
    head = [f"{r['shape']['lines']:,} lines" for r in results]
    sep = "|---|" + "---:|" * len(results)
    out = [f"Generated {meta['date']} on {meta['platform']}, Python {meta['python']}, "
           f"praukron {meta['version']}. Tokens: {ESTIMATOR}. Times: median of {meta['runs']} runs.\n",
           "### Chronicle", "", row("", head), sep,
           row("tasks", [f"{r['shape']['tasks']:,}" for r in results]),
           row("phases", [str(r['shape']['phases']) for r in results]),
           row("chronicle bytes", [f"{r['shape']['bytes']:,}" for r in results]),
           "", "### What an agent receives (~tokens)", "", row("", head), sep]
    for label in results[0]["reads"]:
        out.append(row(f"`{label}`", [f"{tokens(r['reads'][label]):,}" for r in results]))
    out.append(row("one `task update` (payload + reply)",
                   [f"{tokens(r['write']['payload'] + r['write']['reply']):,}" for r in results]))
    out += ["", "### Tool time (seconds)", "", row("", head), sep]
    for label in ("compile", "graph", "dashboard", "validate"):
        out.append(row(f"`{label}`", [f"{r['times'][label]:.2f}" for r in results]))
    out.append(row("`task update` incl. validate + refresh", [f"{r['write']['seconds']:.2f}" for r in results]))
    out.append(row("`journal append` incl. validate + refresh",
                   [f"{r['write']['journal_seconds']:.2f}" for r in results]))
    out += ["", "### Compiled output (bytes)", "", row("", head), sep]
    for name in results[-1]["compiled"]:
        out.append(row(f"`{name}`", [f"{r['compiled'].get(name, 0):,}" for r in results]))
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", type=int, nargs="+", default=[300, 1200, 3000])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--keep", type=Path, help="generate into this folder and keep it")
    args = parser.parse_args(argv)
    scratch = args.keep or Path(tempfile.mkdtemp(prefix="praukron-scale-"))
    try:
        results = [measure(n, scratch, args.runs) for n in args.tasks]
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
    meta = {"date": dt.date.today().isoformat(), "platform": platform.platform(),
            "python": platform.python_version(), "runs": args.runs,
            "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip()}
    text = render(results, meta)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
