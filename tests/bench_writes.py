"""Benchmark of structured writes against direct edits (T-WRITE-DOCS-01).

Not collected by `unittest discover` (the name does not start with `test`).
Run it by hand against a *copy* of a real chronicle:

    python tests/bench_writes.py --source E:/code/Qopilot/.praukron --markdown out.md

The source is copied into a temporary folder and never modified. For each of four
operations it measures what an agent must read and write by three methods:

  (a) direct edit, reading each file in full;
  (b) direct edit, reading only the target section or the file's last section;
  (c) the structured command: its command line, payload and complete JSON output,
      including any follow-up call needed to complete the same acceptance.

Bytes are UTF-8 bytes. Tokens are estimates: bytes / 4. The same four operations
run at two chronicle sizes: as copied, and after `praukron archive` of every
COMPLETE phase. The baselines are conservative: appends count only the appended
text, in-place edits count only the changed lines, and the searches an agent
makes to locate a section are not counted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from praukron import compile as compiler  # noqa: E402

LAUNCHER = ROOT / ".praukron" / "praukron"
ESTIMATOR = "UTF-8 bytes / 4"
FILES = ("TASKS.md", "ACCEPTANCE.md", "JOURNAL.md")


def tokens(size: int) -> int:
    return -(-size // 4)


# ---------------------------------------------------------------- direct edits

def last_section(data: bytes) -> bytes:
    """The final level-two section, as an agent reads the end of a file."""
    starts = [m.start() for m in re.finditer(rb"(?m)^## ", data)]
    return data[starts[-1]:] if starts else data


def section_at(data: bytes, offset: int) -> bytes:
    """The level-two section that contains `offset`."""
    starts = [m.start() for m in re.finditer(rb"(?m)^#{1,2} ", data)]
    begin = max([s for s in starts if s <= offset] or [0])
    after = [s for s in starts if s > offset]
    return data[begin:after[0] if after else len(data)]


def changed(before: bytes, after: bytes) -> dict:
    """Describe an edit as a direct editor would perform it.

    Returns `append` (text added at the end), or `replace` (old and new text of
    each changed hunk, with the offset of the first one).
    """
    if after.startswith(before):
        return {"kind": "append", "new": after[len(before):], "old": b"", "offset": len(before)}
    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    prefix = before.rfind(b"\n", 0, prefix) + 1  # whole lines, as a line-based editor sees them
    while suffix and len(before) > suffix and before[len(before) - suffix - 1] != 10:
        suffix -= 1
    old_mid = before[prefix:len(before) - suffix]
    new_mid = after[prefix:len(after) - suffix]
    old_lines = old_mid.splitlines(keepends=True)
    new_lines = new_mid.splitlines(keepends=True)
    old_bytes = new_bytes = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag != "equal":
            old_bytes += sum(len(x) for x in old_lines[i1:i2])
            new_bytes += sum(len(x) for x in new_lines[j1:j2])
    return {"kind": "replace", "old": b"x" * old_bytes, "new": b"x" * new_bytes, "offset": prefix}


def direct_costs(before: dict[str, bytes], after: dict[str, bytes]) -> dict:
    """Bytes for methods (a) and (b) given each file's content before and after."""
    full = targeted = 0
    body = anchors = 0
    files = [name for name in after if before.get(name, b"") != after[name]]
    for name in files:
        edit = changed(before.get(name, b""), after[name])
        full += len(before.get(name, b""))
        targeted += len(last_section(before[name])) if edit["kind"] == "append" \
            else len(section_at(before[name], edit["offset"]))
        body += len(edit["new"])
        anchors += len(edit["old"])
    return {"files": files, "full_read": full, "targeted_read": targeted, "body": body, "anchors": anchors}


# ----------------------------------------------------------------- the harness

class Chronicle:
    """A throwaway copy of a `.praukron` folder, driven through the CLI."""

    def __init__(self, source: Path, parent: Path, name: str):
        self.root = parent / name
        shutil.copytree(source, self.root / ".praukron", ignore=shutil.ignore_patterns("runtime", ".recovery"))
        self.authority = self.root / ".praukron" / "chronicle"

    def run(self, *argv: str, stdin: str | None = None) -> tuple[int, str, float]:
        started = time.perf_counter()
        done = subprocess.run([sys.executable, str(LAUNCHER), "-C", str(self.root), *argv],
                              input=stdin.encode("utf-8") if stdin is not None else None,
                              capture_output=True)
        elapsed = time.perf_counter() - started
        return done.returncode, done.stdout.decode("utf-8", "replace"), elapsed

    def snapshot(self) -> dict[str, bytes]:
        return {n: (self.authority / n).read_bytes() for n in FILES if (self.authority / n).is_file()}

    def size(self) -> dict:
        working = sum(p.stat().st_size for p in self.authority.glob("*.md"))
        archived = sum(p.stat().st_size for p in (self.authority / "archive").rglob("*.md")) \
            if (self.authority / "archive").is_dir() else 0
        return {"working_bytes": working, "archive_bytes": archived,
                **{n: (self.authority / n).stat().st_size for n in FILES if (self.authority / n).is_file()}}

    def validate(self) -> str:
        code, out, _ = self.run("validate", "--quiet")
        return "passed" if code == 0 else f"FAILED ({out.strip()[:120]})"

    def archive_complete_phases(self) -> list[str]:
        project = compiler.load(self.root)
        moved = []
        for phase in project.phases:
            if phase.status == "COMPLETE":
                code, _, _ = self.run("archive", phase.id)
                if code == 0:
                    moved.append(phase.id)
        return moved


def active_module(root: Path) -> str:
    """A module whose phase is not COMPLETE, so a new task adds no validation error."""
    project = compiler.load(root)
    closed = {p.id for p in project.phases if p.status == "COMPLETE"}
    for module in project.modules:
        if getattr(module, "phase", None) not in closed:
            return module.id
    raise SystemExit("no module outside a COMPLETE phase")


def payloads(module: str) -> dict:
    criteria = [
        {"text": "Given a chronicle of any size, When a task is added with its contract, "
                 "Then only that record is written.", "class": "TEST"},
        {"text": "Given a refused write, When the result is read, Then it names the rule and the next step.",
         "class": "INSPECTION"},
        {"text": "Given two writers, When both update one record, Then the second is refused as stale.",
         "class": "TEST"},
    ]
    return {
        "add": {"id": "T-BENCH-01", "title": "Benchmark a structured write", "module": module,
                "domain": "execution", "dependencies": [],
                "contract": {"id": "AC-T-BENCH-01", "title": "Benchmark a structured write", "criteria": criteria}},
        "criterion": {"criterion": "AC-T-BENCH-01-01", "state": "PASS",
                      "evidence": "python -m unittest discover -s tests: all tests pass"},
        "update": {"id": "T-BENCH-01",
                   "set": {"status": "DONE",
                           "evidence": "All three criteria pass; the unit suite covers each rule and the "
                                       "result of every refusal names the next step."}},
        "journal": {"title": "Benchmark entry",
                    "body": "Recorded the benchmark task, its contract, a criterion result and the closing "
                            "update through the structured write commands.\n\nValidation passed after each "
                            "write; the next action is to publish the figures."},
    }




def command_text(*argv: str) -> str:
    return "praukron " + " ".join(argv) + " --input -"


def call(c: Chronicle, argv: tuple[str, ...], payload: dict) -> dict:
    """One structured command: the bytes the agent emits and receives."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    _, out, seconds = c.run(*argv, "--input", "-", stdin=text)
    return {"reply": json.loads(out), "body": len(text.encode("utf-8")),
            "meta": len(command_text(*argv).encode("utf-8")), "output": len(out.encode("utf-8")),
            "seconds": seconds}


def record(name: str, c: Chronicle, before: dict, calls: list[dict]) -> dict:
    after = c.snapshot()
    direct = direct_costs(before, after)
    files = len(direct["files"])
    failed = [x["reply"] for x in calls if not x["reply"].get("ok")]
    return {
        "operation": name, "files": direct["files"],
        "a": {"read": direct["full_read"], "written": direct["body"] + direct["anchors"], "calls": 2 * files},
        "b": {"read": direct["targeted_read"], "written": direct["body"] + direct["anchors"], "calls": 3 * files},
        "c": {"read": sum(x["output"] for x in calls), "written": sum(x["body"] + x["meta"] for x in calls),
              "calls": len(calls), "bodyBytes": sum(x["body"] for x in calls),
              "outputBytes": sum(x["output"] for x in calls), "metadataBytes": sum(x["meta"] for x in calls)},
        "seconds": sum(x["seconds"] for x in calls), "validate": c.validate(),
        "failures": [f"{name}: {f.get('error')}" for f in failed],
    }


def measure(c: Chronicle, module: str) -> list[dict]:
    p = payloads(module)
    zero = "0" * 16
    rows = []

    before = c.snapshot()
    added = call(c, ("task", "add"), p["add"])
    task_revision = added["reply"].get("revision")
    rows.append(record("add a task with its contract", c, before, [added]))

    # Setup, not measured: the other two criteria, so that DONE is reachable.
    for number in (2, 3):
        state = {"criterion": f"AC-T-BENCH-01-0{number}", "state": "PASS", "evidence": "setup"}
        revision = call(c, ("criterion", "set"), {**state, "expectRevision": zero})["reply"]["error"].get("currentRevision")
        call(c, ("criterion", "set"), {**state, "expectRevision": revision})

    before = c.snapshot()
    probe = call(c, ("criterion", "set"), {**p["criterion"], "expectRevision": zero})
    revision = probe["reply"]["error"].get("currentRevision")
    probe["reply"] = {"ok": True}  # the probe is a refusal by design: it fetches the revision
    rows.append(record("record one criterion result", c, before,
                       [probe, call(c, ("criterion", "set"), {**p["criterion"], "expectRevision": revision})]))

    before = c.snapshot()
    rows.append(record("update a task's status and evidence", c, before, [call(
        c, ("task", "update"), {**p["update"], "expectRevision": task_revision})]))

    before = c.snapshot()
    rows.append(record("append a journal entry", c, before, [call(c, ("journal", "append"), p["journal"])]))
    return rows


def gate(c: Chronicle) -> list[str]:
    """Every method must complete the same acceptance."""
    project = compiler.load(c.root)
    task = next((t for t in project.tasks if t.id == "T-BENCH-01"), None)
    problems = []
    if task is None or task.status != "DONE":
        problems.append("T-BENCH-01 is not DONE")
    states = [x.state for contract in project.contracts.values() for x in contract.criteria
              if x.id.startswith("AC-T-BENCH-01-")]
    if states != ["PASS"] * 3:
        problems.append(f"criteria are {states}")
    if b"Benchmark entry" not in c.snapshot()["JOURNAL.md"]:
        problems.append("journal entry missing")
    return problems


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1f}x" if denominator > 0 and numerator > 0 else "n/a"


def render(runs: dict[str, dict], meta: dict) -> str:
    out = [f"Machine: {meta['machine']}. Python {meta['python']}. Measured {meta['date']} on a local copy of a "
           f"real chronicle. Tokens are estimates ({ESTIMATOR}), not counts from a tokenizer.", ""]
    failures = [f for run in runs.values() for f in run["failures"]]
    out += [f"Failures, reported before any aggregate: {'none' if not failures else '; '.join(failures)}", ""]
    for label, run in runs.items():
        size = run["size"]
        out += [f"### {label}", "",
                f"Working chronicle {size['working_bytes']:,} bytes (`TASKS.md` {size['TASKS.md']:,}, "
                f"`ACCEPTANCE.md` {size['ACCEPTANCE.md']:,}, `JOURNAL.md` {size['JOURNAL.md']:,}); "
                f"archived {size['archive_bytes']:,} bytes. Archived phases: {', '.join(run['archived']) or 'none'}.",
                "",
                "| Operation | Method | Read | Written | Total bytes | ~Tokens | Calls |",
                "|---|---|---:|---:|---:|---:|---:|"]
        for row in run["rows"]:
            for key, method in (("a", "(a) direct edit, full read"), ("b", "(b) direct edit, targeted read"),
                                ("c", "(c) structured command")):
                m = row[key]
                total = m["read"] + m["written"]
                out.append(f"| {row['operation'] if key == 'a' else ''} | {method} | {m['read']:,} | "
                           f"{m['written']:,} | {total:,} | {tokens(total):,} | {m['calls']} |")
        out += ["", "| Operation | (a) / (c) | (b) / (c) | Structured wall time | `validate` after |",
                "|---|---:|---:|---:|---|"]
        for row in run["rows"]:
            total = {k: row[k]["read"] + row[k]["written"] for k in "abc"}
            out.append(f"| {row['operation']} | {ratio(total['a'], total['c'])} | {ratio(total['b'], total['c'])} | "
                       f"{row['seconds']:.2f} s | {row['validate']} |")
        out.append("")
    out += ["Measurement contract for (c): `bodyBytes` is the payload, `outputBytes` the complete UTF-8 output "
            "received (JSON wrapper included), `metadataBytes` the command line; the figures are their sum over every "
            "call needed. Refreshed compiled views and `INDEX.md` are infrastructure and are excluded.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="a .praukron folder to copy (never modified)")
    parser.add_argument("--markdown", help="write the result tables here")
    parser.add_argument("--json", help="write the raw measurements here")
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    if not (source / "chronicle").is_dir():
        parser.error(f"{source} has no chronicle/")
    runs: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="praukron-bench-") as scratch:
        for label, archive in (("As copied", False), ("After archiving every COMPLETE phase", True)):
            c = Chronicle(source, Path(scratch), "archived" if archive else "copied")
            archived = c.archive_complete_phases() if archive else []
            module = active_module(c.root)
            size_before = c.size()
            rows = measure(c, module)
            problems = gate(c)
            failures = [f for row in rows for f in row["failures"]] + [f"gate: {x}" for x in problems]
            failures += [f"{row['operation']}: validate {row['validate']}" for row in rows
                         if row["validate"] != "passed"]
            runs[label] = {"rows": rows, "size": size_before, "archived": archived, "failures": failures}
    meta = {"machine": f"{platform.platform()}, {os.cpu_count()} logical CPUs", "python": platform.python_version(),
            "date": dt.date.today().isoformat(), "estimator": ESTIMATOR}
    text = render(runs, meta)
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"meta": meta, "runs": runs}, indent=2), encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 1 if any(run["failures"] for run in runs.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
