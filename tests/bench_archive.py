"""Benchmark of the archive lifecycle on a copy of a real chronicle (T-ARCH-DOCS-01).

Not collected by `unittest discover` (the name does not start with `test`).
Run it by hand; the source is copied twice into temporary folders, once with a
Git repository around the copy and once without, and is never modified:

    python tests/bench_archive.py --source E:/code/Qopilot/.praukron --markdown out.md

For each copy it archives every COMPLETE phase and then P-NONE (`archive plan`,
then `archive apply --plan`), and reports:

  - the size of each working file before and after;
  - the bytes an agent reads to edit each working file: the whole file, or only
    the target (the last section when appending, the median section when
    changing one record in place);
  - the time of `compile` and `validate` before and after (median of several
    runs, Python start-up included);
  - parity: authored records equal except `source`, derived facts equal as
    sets, and `context` and `retrieve` equal record by record.

Only aggregate numbers are printed; no record text or id is. Tokens are
estimates: UTF-8 bytes / 4.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from praukron import archive, compile as compiler  # noqa: E402
import test_archive_parity as parity  # noqa: E402

LAUNCHER = ROOT / ".praukron" / "praukron"
ESTIMATOR = "UTF-8 bytes / 4"
FILES = ("TASKS.md", "ACCEPTANCE.md", "JOURNAL.md", "ASSUMPTIONS.md", "TECH_DEBT.md", "TRACE.md")
RUNS = 9


def tokens(size: int) -> int:
    return -(-size // 4)


def sections(data: bytes) -> list[bytes]:
    starts = [m.start() for m in re.finditer(rb"(?m)^## ", data)] or [0]
    return [data[a:b] for a, b in zip(starts, [*starts[1:], len(data)])]


def read_costs(data: bytes) -> dict:
    """What an agent reads to edit this file: all of it, or only the target."""
    parts = sections(data)
    return {"full": len(data), "append": len(parts[-1]),
            "edit": int(statistics.median(len(p) for p in parts))}


class Copy:
    """A throwaway copy of a `.praukron` folder, driven through the CLI."""

    def __init__(self, source: Path, parent: Path, name: str, git: bool):
        self.root = parent / name
        shutil.copytree(source, self.root / ".praukron", ignore=shutil.ignore_patterns("runtime", ".recovery", "backup"))
        self.authority = self.root / ".praukron" / "chronicle"
        self.git = git
        if git:
            for argv in (["init", "-q"], ["config", "user.email", "bench@example.invalid"],
                         ["config", "user.name", "bench"], ["config", "core.autocrlf", "false"],
                         ["add", "-A"], ["commit", "-q", "-m", "baseline"]):
                self._git(*argv)

    def _git(self, *argv: str) -> None:
        done = subprocess.run(["git", "-C", str(self.root), *argv], capture_output=True)
        if done.returncode:
            raise RuntimeError(f"git {' '.join(argv)}: {done.stderr.decode('utf-8', 'replace').strip()}")

    def run(self, *argv: str) -> tuple[int, str, float]:
        started = time.perf_counter()
        done = subprocess.run([sys.executable, str(LAUNCHER), "-C", str(self.root), *argv], capture_output=True)
        elapsed = time.perf_counter() - started
        self.stderr = done.stderr.decode("utf-8", "replace")
        return done.returncode, done.stdout.decode("utf-8", "replace"), elapsed

    def sizes(self) -> dict:
        out = {}
        for name in FILES:
            path = self.authority / name
            if path.is_file():
                data = path.read_bytes()
                out[name] = {"bytes": len(data), **read_costs(data)}
        out["archive_bytes"] = sum(p.stat().st_size for p in (self.authority / "archive").rglob("*")
                                   if p.is_file()) if (self.authority / "archive").is_dir() else 0
        return out

    def times(self) -> dict:
        """Median and range of repeated runs, in seconds."""
        out = {}
        for name in ("compile", "validate"):
            runs = [self.run(name)[2] for _ in range(RUNS)]
            out[name] = {"median": statistics.median(runs), "min": min(runs), "max": max(runs)}
        return out

    def state(self, sample: list[str] | None = None) -> dict:
        project = compiler.load(self.root)
        if sample is None:  # the same records before and after, whatever order the loader returns
            tasks = sorted(t.id for t in project.tasks)
            sample = tasks[:: max(1, len(tasks) // 25)]
        queries = tuple(sample) + ("why is the first phase blocked?",)
        return {"sample": sample, "records": archive._records(project), "facts": parity.facts(project),
                "reads": parity.packets(self.root, project, queries, sample)}

    def archive_all(self) -> tuple[list[str], list[str], dict]:
        """Archive every COMPLETE phase, then P-NONE. Returns phases moved, failures, apply stats."""
        project = compiler.load(self.root)
        phases = [p.id for p in project.phases if p.status == "COMPLETE"] + ["P-NONE"]
        moved, failures, stats = [], [], {"seconds": [], "mode": set(), "records": 0}
        for phase in phases:
            code, out, _ = self.run("archive", "plan", phase, "--json")
            try:
                planned = json.loads(out)
            except ValueError:
                failures.append(f"archive plan {phase}: exit {code}: {(out + self.stderr).strip()[:160]}")
                continue
            if code or not planned.get("digest"):
                failures.append(f"archive plan {phase}: {planned.get('error', {}).get('message', code)}")
                continue
            code, out, elapsed = self.run("archive", "apply", phase, "--plan", planned["digest"], "--json")
            try:
                applied = json.loads(out)
            except ValueError:
                applied = {"ok": False, "error": {"message": out.strip()[:160]}}
            if code or not (applied.get("written") or not any(c["action"] == "moves" for c in applied["candidates"])):
                failures.append(f"archive apply {phase}: exit {code}: {applied.get('error', {}).get('message', '')}")
                continue
            moved.append(phase)
            stats["seconds"].append(elapsed)
            stats["mode"].add(planned["mode"])
            stats["records"] += sum(c["action"] == "moves" for c in planned["candidates"])
            if self.git:
                self._git("add", "-A")
                self._git("commit", "-q", "--allow-empty", "-m", f"archive {phase}")
        stats["mode"] = sorted(stats["mode"])
        return moved, failures, stats

    def parity(self, before: dict, after: dict) -> list[str]:
        problems = []
        for key, label in (("records", "authored records"), ("facts", "derived facts"), ("reads", "context/retrieve")):
            if before[key] != after[key]:
                problems.append(f"parity: {label} differ after archiving")
        return problems


def measure(source: Path, scratch: Path, name: str, git: bool) -> dict:
    c = Copy(source, scratch, name, git)
    size_before, time_before, state_before = c.sizes(), c.times(), c.state()
    moved, failures, stats = c.archive_all()
    if failures:
        return {"label": name, "withheld": True, "failures": failures, "archived": moved}
    problems = c.parity(state_before, c.state(state_before["sample"]))
    code, out, _ = c.run("validate", "--quiet")
    if code:
        problems.append(f"validate after archiving: {out.strip()[:120]}")
    return {"label": name, "git": git, "archived": moved, "failures": problems, "stats": stats,
            "before": size_before, "after": c.sizes(), "time_before": time_before, "time_after": c.times()}


def pct(before: int, after: int) -> str:
    return f"{(after - before) / before * 100:+.0f}%" if before else "n/a"


def render(runs: list[dict], meta: dict) -> str:
    out = [f"Machine: {meta['machine']}. Python {meta['python']}. Measured {meta['date']} on local copies of a real "
           f"chronicle (Git and no-Git). Tokens are estimates ({ESTIMATOR}), not counts from a tokenizer.", ""]
    failures = [f"{r['label']}: {f}" for r in runs for f in r["failures"]]
    out += [f"Failures, reported before any aggregate: {'none' if not failures else '; '.join(failures)}", ""]
    for run in runs:
        if run.get("withheld"):
            out += [f"### {run['label']}", "", "Not measured: archiving failed (see the failures above).", ""]
            continue
        stats = run["stats"]
        out += [f"### {run['label']}", "",
                f"Archived {len(run['archived'])} runs ({stats['records']} records moved; recovery mode "
                f"`{'/'.join(stats['mode'])}`); apply took a median {statistics.median(stats['seconds']):.2f} s per run "
                f"including the refresh. Archive files: {run['after']['archive_bytes']:,} bytes.", "",
                "| Working file | Bytes before | Bytes after | Change | Agent reads: whole file (~tokens) | "
                "Targeted append (~tokens) | Targeted edit (~tokens) |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        for name in FILES:
            if name not in run["before"]:
                continue
            b, a = run["before"][name], run["after"][name]
            out.append(f"| `{name}` | {b['bytes']:,} | {a['bytes']:,} | {pct(b['bytes'], a['bytes'])} | "
                       f"{b['full']:,} → {a['full']:,} ({tokens(b['full']):,} → {tokens(a['full']):,}) | "
                       f"{b['append']:,} → {a['append']:,} | {b['edit']:,} → {a['edit']:,} |")
        out += ["", "| Command | Before (range) | After (range) | Change in median |", "|---|---:|---:|---:|"]
        for name in ("compile", "validate"):
            b, a = run["time_before"][name], run["time_after"][name]
            out.append(f"| `{name}` (median of {RUNS}) | {b['median']:.2f} s ({b['min']:.2f}–{b['max']:.2f}) | "
                       f"{a['median']:.2f} s ({a['min']:.2f}–{a['max']:.2f}) | "
                       f"{pct(int(b['median'] * 1000), int(a['median'] * 1000))} |")
        out.append("")
    out += ["Reads are UTF-8 bytes of what an agent must read to edit the file: the whole file; the last section "
            "when appending; the median section when changing one record in place. Searches to locate a section "
            "are not counted. Times include Python start-up, the whole-chronicle load and, for `compile`, writing "
            "every compiled view. Parity compared authored records (except `source`), derived facts as sets, and "
            "`context` and `retrieve` results on a sample of records, before and after archiving.", ""]
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
    runs = []
    with tempfile.TemporaryDirectory(prefix="praukron-bench-archive-") as scratch:
        for name, git in (("Git mode", True), ("No-Git mode", False)):
            runs.append(measure(source, Path(scratch), name.replace(" ", "-").lower(), git) | {"label": name})
    meta = {"machine": f"{platform.platform()}, {os.cpu_count()} logical CPUs", "python": platform.python_version(),
            "date": dt.date.today().isoformat(), "estimator": ESTIMATOR}
    text = render(runs, meta)
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"meta": meta, "runs": runs}, indent=2), encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 1 if any(run["failures"] for run in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
