"""The one governed write into the chronicle: appending the owner's responses.

`RESPONSES.md` is the owner's words, append-only (ADR-054). This module is the
only code that writes it, and the dashboard's write-back goes through it too
(ADR-055). A write is checked before it happens, made atomically, validated
after it lands, and undone byte for byte if it introduced an error. Nothing
else in the chronicle is touched; compiled views are refreshed afterwards.
"""

from __future__ import annotations

import datetime as _dt
import ctypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from . import compile as compiler
from .layout import AUTHORITY_DIR, COMPILED_DIR
from .model import GITHUB_LOGIN, RESPONSE_ACTIONS, RESPONSE_STATUS, VERIFY

FILE = "RESPONSES.md"
HEADER = (
    "# Owner responses\n\n"
    "What the owner said, verbatim. Append-only: an agent never edits, reorders,\n"
    "normalizes, or clears an entry.\n"
)
_LOCK = "respond.lock"
_STALE_SECONDS = 60
_STAGING: ContextVar[dict[Path, Path] | None] = ContextVar("replacement_staging", default=None)


class RespondError(Exception):
    """A response was refused, or a write failed and was undone."""


def identity() -> str:
    """Return the active GitHub CLI login, or refuse an unauthenticated write."""
    gh = shutil.which("gh")
    if gh is None:
        raise RespondError("the GitHub CLI is not installed: a response records who gave it as the "
                           "active `gh` login (install gh and run `gh auth login`)")
    try:
        result = subprocess.run([gh, "api", "user", "--jq", ".login"], capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise RespondError(f"could not ask gh who is logged in: {failure}") from failure
    login = result.stdout.strip()
    if result.returncode != 0 or not GITHUB_LOGIN.match(login):
        raise RespondError("no GitHub login: run `gh auth login`; a response records who gave it "
                           "as the active `gh` login")
    return login


@dataclass(frozen=True)
class Lock:
    """The lock identity this process created."""

    path: Path
    pid: int
    token: str

    def release(self) -> None:
        try:
            owner = self.path.read_text(encoding="utf-8").strip().split()
        except FileNotFoundError:
            return
        if owner == [str(self.pid), self.token]:
            self.path.unlink(missing_ok=True)


@dataclass
class Item:
    target: str
    action: str
    text: str


def _pid_alive(pid: int) -> bool:
    """Whether a process still owns a valid lock PID."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it is still running.
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _owner(path: Path) -> tuple[int, str] | None:
    try:
        pid, token = path.read_text(encoding="utf-8").strip().split()
        return int(pid), token
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return None


def _lock(root: Path) -> Lock:
    """An exclusive lock beside the compiled views, never inside the chronicle."""
    directory = root / COMPILED_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _LOCK
    deadline = time.monotonic() + 10
    while True:
        pid, token = os.getpid(), secrets.token_hex(16)
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(handle, f"{pid} {token}\n".encode())
                os.fsync(handle)
            finally:
                os.close(handle)
            return Lock(path, pid, token)
        except FileExistsError:
            try:
                owner = _owner(path)
                stale = time.time() - path.stat().st_mtime > _STALE_SECONDS
                if owner is not None and not _pid_alive(owner[0]):
                    path.unlink()
                    continue
                if owner is None and stale:
                    path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise RespondError("another write is in progress; try again")
            time.sleep(0.05)


def recover_archive(root: Path) -> bool:
    """Compatibility name for recovery of any interrupted write."""
    from .mutate import recover_write

    return recover_write(root)


@contextmanager
def _staging_paths(paths: dict[Path, Path]):
    """Use only staging names already persisted in the transaction's recovery record."""
    token = _STAGING.set(paths)
    try:
        yield
    finally:
        _STAGING.reset(token)


def _replace(path: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory, then swap it in."""
    temporary = (_STAGING.get() or {}).get(path.resolve())
    if temporary is None:
        handle, temporary = tempfile.mkstemp(prefix=".respond-", dir=path.parent)
    else:
        handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _entry(number: int, item: Item, by: str, date: str, via: str) -> str:
    quoted = "\n".join(f"> {line}" if line else ">" for line in item.text.split("\n"))
    return (f"\n## R-{number}: {item.target} {item.action}\n- By: {by}\n- Date: {date}\n"
            f"- Via: {via}\n\n{quoted}\n")


def _check(project, items: list[Item], verify: bool) -> None:
    for item in items:
        if item.action not in RESPONSE_ACTIONS:
            raise RespondError(f"{item.action} is not one of {', '.join(RESPONSE_ACTIONS)}")
        if item.action == VERIFY:
            task = project.task(item.target)
            if task is None or not task.owner_held:
                raise RespondError(f"VERIFY applies only to an Authority: owner task, not {item.target}")
            if not verify:
                raise RespondError("VERIFY is recorded only interactively: from `praukron dashboard --serve` "
                                   "or `praukron respond` at a terminal, never by an agent")
        if item.target.startswith("A-"):
            if project.assumption(item.target) is None:
                raise RespondError(f"{item.target} is not an assumption in ASSUMPTIONS.md")
        elif item.target.startswith("T-"):
            if project.task(item.target) is None:
                raise RespondError(f"{item.target} is not a task in TASKS.md")
            if item.action in RESPONSE_STATUS:
                raise RespondError(f"{item.action} applies to assumptions; a task takes GUIDE")
        else:
            raise RespondError(f"{item.target} is neither an assumption nor a task")
        if not item.text.strip():
            raise RespondError(f"the response to {item.target} has no text")


def _errors(project) -> set[tuple[str, str, str]]:
    from .mutate import _errors as errors

    return errors(project)


def refresh(root: Path) -> None:
    """Recompile every view after recording the owner's response."""
    from .mutate import refresh as refresh_views

    refresh_views(root)


def append(root: Path, items: list[Item], via: str = "cli", date: str | None = None,
           recompile: bool = True, verify: bool = False) -> list[str]:
    """Append every item as one batch, or none of them. Returns the new ids."""
    if not items:
        raise RespondError("nothing to record")
    items = [Item(i.target.strip(), i.action.strip().upper(), i.text.rstrip("\n")) for i in items]
    date = date or _dt.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise RespondError(f"{date} is not a YYYY-MM-DD date")
    lock = _lock(root)
    try:
        if recover_archive(root):
            raise RespondError("an interrupted archive was rolled back; try again")
        project = compiler.load(root)
        _check(project, items, verify)
        by = identity()
        before = _errors(project)
        path = root / AUTHORITY_DIR / FILE
        existed = path.is_file()
        original = path.read_bytes() if existed else None
        text = original.decode() if original is not None else HEADER
        if not text.endswith("\n"):
            text += "\n"
        start = max([int(r.id.split("-")[1]) for r in project.responses] or [0]) + 1
        ids = [f"R-{start + n}" for n in range(len(items))]
        text += "".join(_entry(start + n, item, by, date, via) for n, item in enumerate(items))
        try:
            _replace(path, text.encode())
        except OSError as failure:
            # The swap is atomic: a failed write leaves the file as it was.
            raise RespondError(f"nothing was recorded: {failure}") from failure
        try:
            introduced = _errors(compiler.load(root)) - before
        except Exception as failure:  # the new text does not even parse
            introduced = {("unreadable", FILE, str(failure))}
        if introduced:
            if existed:
                _replace(path, original)
            elif path.exists():
                path.unlink()
            detail = "; ".join(f"{code}: {message}" for code, _, message in sorted(introduced))
            raise RespondError(f"nothing was recorded: {detail}")
    finally:
        lock.release()
    if recompile:
        refresh(root)
    return ids


__all__ = ["FILE", "Item", "RespondError", "append", "recover_archive", "refresh"]
