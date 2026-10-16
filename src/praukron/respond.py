"""The one governed write into the chronicle: appending the owner's responses.

`RESPONSES.md` is the owner's words, append-only (ADR-054). This module is the
only code that writes it, and the dashboard's write-back goes through it too
(ADR-055). A write is checked before it happens, made atomically, validated
after it lands, and undone byte for byte if it introduced an error. Nothing
else in the chronicle is touched; compiled views are refreshed afterwards.

Who responded is never typed: `By:` is the login of the active GitHub CLI
account (ADR-060). VERIFY, the owner's verification of an `Authority: owner`
task, is accepted only from an interactive act: the served page, or a
terminal confirmation. An agent never issues it.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import analytics, compile as compiler, dashboard, mermaid, validate
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


class RespondError(Exception):
    """A response was refused, or a write failed and was undone."""


def identity() -> str:
    """The login of the active `gh` account; refuses when there is none (ADR-060)."""
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


@dataclass
class Item:
    target: str
    action: str
    text: str


def _lock(root: Path):
    """An exclusive lock beside the compiled views, never inside the chronicle."""
    directory = root / COMPILED_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _LOCK
    deadline = time.monotonic() + 10
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, str(os.getpid()).encode())
            os.close(handle)
            return path
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > _STALE_SECONDS:
                    path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise RespondError("another response is being written; try again")
            time.sleep(0.05)


def _replace(path: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory, then swap it in."""
    handle, temporary = tempfile.mkstemp(prefix=".respond-", dir=path.parent)
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
    return {(f.code, f.where, f.message) for f in validate.errors(validate.check(project))}


def refresh(root: Path) -> None:
    """Recompile every view so the dashboard shows what was just written."""
    project = compiler.load(root)
    report = analytics.report(project)
    compiler.write(root, project)
    compiled = root / COMPILED_DIR
    for name, text in mermaid.render_all(project, report).items():
        (compiled / name).write_text(text)
    (compiled / "dashboard.html").write_text(
        dashboard.render(project, report, compiler.as_json(project)))


def append(root: Path, items: list[Item], via: str = "cli", date: str | None = None,
           recompile: bool = True, verify: bool = False) -> list[str]:
    """Append every item as one batch, or none of them. Returns the new ids.

    `By:` is the active `gh` login. `verify` is set only by an interactive
    caller, and is what allows a VERIFY item."""
    if not items:
        raise RespondError("nothing to record")
    items = [Item(i.target.strip(), i.action.strip().upper(), i.text.rstrip("\n")) for i in items]
    date = date or _dt.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise RespondError(f"{date} is not a YYYY-MM-DD date")
    lock = _lock(root)
    try:
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
        lock.unlink(missing_ok=True)
    if recompile:
        refresh(root)
    return ids


__all__ = ["FILE", "Item", "RespondError", "append", "identity", "refresh"]
