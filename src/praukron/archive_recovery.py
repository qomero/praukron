"""Recovery modes, owner instructions and persistent archive backups (ADR-070)."""

from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .layout import AUTHORITY_DIR
from .mutate import _replace


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root.resolve()), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def mode(root: Path, files=None, destinations=()) -> str:
    try:
        tree = _git(root / AUTHORITY_DIR, "rev-parse", "--is-inside-work-tree")
        if tree.returncode or tree.stdout.strip() != b"true":
            return "nogit"
        ignored = _git(root, "check-ignore", "--no-index", "--", AUTHORITY_DIR)
        if ignored.returncode not in (0, 1):
            raise OSError(ignored.stderr.decode("utf-8", errors="replace"))
        return "nogit" if ignored.returncode == 0 else "git"
    except FileNotFoundError:
        return "nogit"


def require_clean(root: Path) -> None:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", AUTHORITY_DIR)
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", errors="replace"))
    if result.stdout:
        # Imported lazily so the planner can keep its public exception type.
        from .archive import ArchiveError
        raise ArchiveError("chronicle has modified, staged or untracked files; commit or restore them first",
                           "dirty-chronicle")


def command(argv: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    return shlex.join(argv)


def backup(root: Path, originals: dict[Path, bytes | None]) -> dict:
    """Copy every original, then publish the manifest before authority changes."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    directory = root.resolve() / ".praukron" / "backup" / f"{stamp}-{time.time_ns()}"
    directory.mkdir(parents=True)
    files = []
    for path, data in originals.items():
        name = path.resolve().relative_to(root.resolve()).as_posix()
        files.append({"path": name, "absent": data is None})
        if data is not None:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace(target, data)
    _replace(directory / "manifest.json", json.dumps({"files": files}, indent=2).encode("utf-8"))
    # The manifest stores project-relative paths alongside byte-exact copies.
    script = ("import json, pathlib; "
              f"b=pathlib.Path({str(directory)!r}); r=pathlib.Path({str(root.resolve())!r}); "
              "m=json.loads((b/'manifest.json').read_text(encoding='utf-8')); "
              "[(r.joinpath(f['path']).unlink(missing_ok=True) if f['absent'] else "
              "(r.joinpath(f['path']).parent.mkdir(parents=True,exist_ok=True), "
              "r.joinpath(f['path']).write_bytes(b.joinpath(f['path']).read_bytes()))) for f in m['files']]")
    return {"backup": str(directory), "commands": {"backup": [[sys.executable, "-c", script]]}}


def instructions(root: Path, originals: dict[Path, bytes | None], phase: str, operation: str, *, directories=()) -> dict:
    tracked = [p.relative_to(root).as_posix() for p, data in originals.items() if data is not None]
    created = [p.relative_to(root).as_posix() for p, data in originals.items() if data is None]
    git = ["git", "-C", str(root.resolve())]
    unstaged = [git + ["restore", "--source=HEAD", "--worktree", "--", *tracked]] if tracked else []
    staged = [git + ["restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked]] if tracked else []
    if created:
        script = ("from pathlib import Path; " + "; ".join(
            f"Path({str(root / name)!r}).unlink(missing_ok=True)" for name in created))
        delete = [sys.executable, "-c", script]
        unstaged.append(delete)
        staged += [git + ["rm", "--cached", "--ignore-unmatch", "--", *created], delete]
    return {"changedTrackedFiles": tracked, "createdFiles": created,
            "suggestedCommitMessage": f"{operation.capitalize()} {phase} chronicle records",
            "commands": {"unstaged": unstaged, "staged": staged,
                         "committed": [git + ["revert", "<archive-commit>"]]}}


def describe(info: dict) -> list[str]:
    lines = []
    for label, key in (("Changed tracked files", "changedTrackedFiles"), ("Newly created files", "createdFiles")):
        if key in info:
            lines.append(label + ": " + (", ".join(info[key]) or "none"))
    if "suggestedCommitMessage" in info:
        lines.append("Suggested commit message: " + info["suggestedCommitMessage"])
    if "backup" in info:
        lines.append("Backup (retained): " + info["backup"])
    for state, commands in info.get("commands", {}).items():
        lines.append(f"Recovery ({state}):")
        lines.extend("  " + command(argv) for argv in commands)
    return lines
