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
from typing import Iterable

from . import layout
from .layout import AUTHORITY_DIR
from .mutate import _replace
from .snapshot import Snapshot


def _git(root: Path, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root.resolve()), *args],
                          input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def mode(root: Path, files: Iterable[str] | None = None, destinations: Iterable[str] = ()) -> str:
    """Use Git only if every captured or prospective file is unignored."""
    authority = root / AUTHORITY_DIR
    try:
        tree = _git(authority, "rev-parse", "--is-inside-work-tree")
        if tree.returncode or tree.stdout.strip() != b"true":
            return "nogit"
        if files is None:
            files = (p.relative_to(authority).as_posix() for p in authority.rglob("*")
                     if p.is_file() and ".git" not in p.relative_to(authority).parts)
        paths = sorted({".", *files, *destinations})
        ignored = _git(authority, "check-ignore", "--no-index", "--stdin", "-z",
                       input=b"".join(os.fsencode(p) + b"\0" for p in paths))
        if ignored.returncode == 128:
            return "nogit"
        if ignored.returncode not in (0, 1):
            raise OSError(ignored.stderr.decode("utf-8", errors="replace"))
        return "nogit" if ignored.returncode == 0 else "git"
    except FileNotFoundError:
        return "nogit"


def require_clean(root: Path) -> None:
    result = _git(root / AUTHORITY_DIR, "status", "--porcelain=v1", "--untracked-files=all",
                  "--ignored", "--", ".")
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", errors="replace"))
    if result.stdout:
        # Imported lazily so the planner can keep its public exception type.
        from .archive import ArchiveError
        raise ArchiveError("chronicle has modified, staged or untracked files; commit or restore them first",
                           "dirty-chronicle")


def command(argv: list[str], shell: str | None = None) -> str:
    shell = shell or ("PowerShell" if os.name == "nt" else "POSIX sh")
    if shell == "PowerShell":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    if shell == "POSIX sh":
        return shlex.join(argv)
    raise ValueError(f"unsupported recovery shell: {shell}")


def backup(root: Path, originals: dict[Path, bytes | None]) -> dict:
    """Copy every original, then publish the manifest before authority changes."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    base = root.resolve() / layout.HOME_DIR / "backup"
    base.mkdir(parents=True, exist_ok=True)
    if not (base / ".gitignore").is_file():
        _replace(base / ".gitignore", b"*\n")
    directory = base / f"{stamp}-{time.time_ns()}"
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
    script = ("import json, pathlib, sys; "
              "b=pathlib.Path(sys.argv[1]); r=pathlib.Path(sys.argv[2]); "
              "m=json.loads((b/'manifest.json').read_text(encoding='utf-8')); "
              "[(r.joinpath(f['path']).unlink(missing_ok=True) if f['absent'] else "
              "(r.joinpath(f['path']).parent.mkdir(parents=True,exist_ok=True), "
              "r.joinpath(f['path']).write_bytes(b.joinpath(f['path']).read_bytes()))) for f in m['files']]")
    return {"backup": str(directory), "commands": {
        "backup": [[sys.executable, "-c", script, str(directory), str(root.resolve())]]}}


def instructions(root: Path, originals: dict[Path, bytes | None], phase: str, operation: str, *,
                 directories=(), snapshot: Snapshot | None = None) -> dict:
    authority = snapshot.authority if snapshot is not None else (root / AUTHORITY_DIR).resolve()
    result = _git(authority, "ls-files", "--cached", "-z", "--", ".")
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", errors="replace"))
    indexed = {os.fsdecode(name) for name in result.stdout.split(b"\0") if name}
    index_names = {(snapshot.resolve(name) if snapshot is not None else name): name for name in indexed}
    tracked_paths = [p for p, data in originals.items() if data is not None
                     and p.relative_to(authority).as_posix() in index_names]
    if len(tracked_paths) != sum(data is not None for data in originals.values()):
        from .archive import ArchiveError
        raise ArchiveError("chronicle tracking changed; run archive plan again", "dirty-chronicle")
    tracked = [p.relative_to(root).as_posix() for p in tracked_paths]
    pathspecs = [index_names[p.relative_to(authority).as_posix()] for p in tracked_paths]
    created = [p.relative_to(root).as_posix() for p, data in originals.items() if data is None]
    git = ["git", "-C", str(authority.resolve())]
    unstaged = [git + ["restore", "--source=HEAD", "--worktree", "--", *pathspecs]] if tracked else []
    staged = [git + ["restore", "--source=HEAD", "--staged", "--worktree", "--", *pathspecs]] if tracked else []
    if created:
        # Keep paths in argv: embedding them in Python source crosses both the
        # shell's quoting rules and Windows' native argument conversion.
        script = ("import sys; from pathlib import Path; n=int(sys.argv[1]); "
                  "[Path(p).unlink(missing_ok=True) for p in sys.argv[2:2+n]]; "
                  "[p.rmdir() if p.is_dir() and not any(p.iterdir()) else None "
                  "for p in map(Path,sys.argv[2+n:])]")
        parents = sorted(directories, key=lambda p: len(p.parts), reverse=True)
        delete = [sys.executable, "-c", script, str(len(created)),
                  *(str(root / name) for name in created), *(str(p) for p in parents)]
        unstaged.append(delete)
        new_specs = [p.relative_to(authority).as_posix() for p, data in originals.items() if data is None]
        staged += [git + ["rm", "--cached", "--ignore-unmatch", "--", *new_specs], delete]
    return {"changedTrackedFiles": tracked, "createdFiles": created,
            "createdDirectories": [p.relative_to(root).as_posix() for p in directories],
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
        for shell in ("POSIX sh", "PowerShell"):
            lines.append(f"Recovery ({state}, {shell}):")
            lines.extend("  " + command(argv, shell) for argv in commands)
    return lines
