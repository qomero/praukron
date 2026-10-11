"""Optional CodeGraph integration (ADR-049).

Praukron answers what matters and why; CodeGraph answers where the code is,
what depends on it, and what a change would touch. This module only ever runs
the upstream `codegraph` CLI when it is on PATH, only after Praukron has already
resolved the project context, and never writes anything CodeGraph returns into
the chronicle. Praukron without CodeGraph is complete.

Commands verified against CodeGraph 1.6.0 and its upstream README
(github.com/colbymchenry/codegraph): `init [path]` builds the project-local
`.codegraph/` index, `status --json [path]`, `explore <query> -p <path>`,
`uninit -f [path]`, and `install`, which writes MCP configuration into
user-level agent settings and is therefore never run without consent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

INSTALL_HINT = (
    "Install CodeGraph from https://github.com/colbymchenry/codegraph, for example\n"
    "  npm i -g @colbymchenry/codegraph\n"
    "then run `praukron codegraph setup`."
)
TIMEOUT = 90
MAX_OUTPUT = 12000


def binary() -> str | None:
    return shutil.which("codegraph")


def _run(args: list[str], timeout: int = TIMEOUT) -> tuple[bool, str]:
    exe = binary()
    if exe is None:
        return False, "CodeGraph is not installed (no `codegraph` on PATH)."
    try:
        done = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"CodeGraph could not run: {error}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return False, f"CodeGraph exited {done.returncode}: {detail[-1] if detail else 'no output'}"
    return True, done.stdout


def status(root: Path) -> dict[str, object]:
    """Availability and index health, as CodeGraph itself reports them."""
    if binary() is None:
        return {"available": False, "health": "UNAVAILABLE", "detail": INSTALL_HINT}
    ok, output = _run(["status", "--json", str(root)], timeout=60)
    if not ok:
        return {"available": True, "health": "FAILING", "detail": output}
    try:
        data = json.loads(output)
    except ValueError:
        return {"available": True, "health": "FAILING", "detail": "status did not return JSON"}
    if not data.get("initialized"):
        health = "NOT_INITIALIZED"
    else:
        index = data.get("index") or {}
        pending = data.get("pendingChanges") or {}
        stale = index.get("reindexRecommended") or any(pending.get(k) for k in ("added", "modified", "removed"))
        health = "STALE" if stale else ("HEALTHY" if index.get("state", "complete") == "complete" else "INDEXING")
    return {
        "available": True,
        "health": health,
        "version": data.get("version"),
        "files": data.get("fileCount"),
        "symbols": data.get("nodeCount"),
        "lastIndexed": data.get("lastIndexed"),
        "indexPath": data.get("indexPath"),
    }


def explore(root: Path, query: str, max_files: int = 6) -> tuple[bool, str]:
    ok, output = _run(["explore", query, "-p", str(root), "--max-files", str(max_files)])
    if ok and len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n… truncated at {MAX_OUTPUT} characters; run the query directly for more.\n"
    return ok, output


def init(root: Path) -> tuple[bool, str]:
    return _run(["init", "-y", str(root)], timeout=1800)


def uninit(root: Path) -> tuple[bool, str]:
    return _run(["uninit", "-f", str(root)])


def install_agents() -> tuple[bool, str]:
    return _run(["install"], timeout=300)


def query_for(tasks: list) -> str:
    """Seed a code query from the resolved tasks: their anchors when they have
    them, their titles otherwise."""
    terms: list[str] = []
    for task in tasks:
        terms += task.symbols + task.files
    if not terms:
        terms = [task.title for task in tasks]
    return " ".join(dict.fromkeys(terms))
