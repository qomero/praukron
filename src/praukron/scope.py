"""Capture a task's observed files from Git (SPEC §2.9, ADR-092).

A structured write that claims a task records the current commit on it
(`Claimed at`). A structured write that sets the task DONE then captures, in
the same write, every file changed since that commit, the working tree and
untracked files included, as observed references in CODE.md. `praukron scope
capture` does the same between two named revisions.

Capture never blocks a write. Without Git, outside a repository, without a
claim commit, or when Git fails, the write goes ahead unchanged and its reply
says why capture was skipped.

Git is reached only through `_run`, by subprocess with a timeout, using the
standard library. `changes` is the one place that knows a changed file's
change type and paths; symbol capture (T-CIL-SCOPE-02) extends a `Change`
there, where the old text (at `base`) and the new text (at `head`, or in the
working tree) of each file can be read.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import mutate, ref, refs
from .layout import AUTHORITY_DIR
from .model import Project, reference_path_problem

TIMEOUT = 20
# Everything Praukron owns; never recorded as a task's change.
OWN_DIR = ".praukron/"
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# Statuses of a task that is claimed and not yet closed.
_ACTIVE = ("WIP", "BLOCKED")
_UNASSIGNED = {"unassigned", "none", "—", "-"}


class GitUnavailable(Exception):
    """Why Git could not answer; the capture is skipped with this reason."""


@dataclass(frozen=True)
class Change:
    """One changed file between two revisions.

    `kind` is `added`, `modified`, `deleted` or `renamed`; `old_path` is set
    for a rename. Paths are relative to the project root, with `/`.
    `uncommitted` is true when the change is in the working tree and not in
    the head commit.
    """

    kind: str
    path: str
    old_path: str | None = None
    uncommitted: bool = False

    @property
    def relation(self) -> str:
        return {"added": "creates", "deleted": "deletes"}.get(self.kind, "modifies")


def _run(root: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which("git")
    if exe is None:
        raise GitUnavailable("git is not installed (no `git` on PATH)")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run([exe, "-C", str(root), *args], input=stdin, capture_output=True,
                              timeout=TIMEOUT, env=env, check=False)
    except subprocess.TimeoutExpired as error:
        raise GitUnavailable(f"git {args[0]} timed out after {TIMEOUT} seconds") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise GitUnavailable(f"git could not run: {error}") from error


def _failure(done: subprocess.CompletedProcess, args: tuple[str, ...]) -> GitUnavailable:
    detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
    if any("not a git repository" in line.lower() for line in detail):
        return GitUnavailable("not a Git repository")
    return GitUnavailable(f"git {args[0]} failed: {detail[0] if detail else f'exit {done.returncode}'}")


def _git(root: Path, *args: str, stdin: bytes | None = None, ok: tuple[int, ...] = (0,)) -> bytes:
    done = _run(root, *args, stdin=stdin)
    if done.returncode not in ok:
        raise _failure(done, args)
    return done.stdout


def head(root: Path) -> str:
    """The current commit's full identifier, in one Git call."""
    done = _run(root, "rev-parse", "--is-inside-work-tree", "HEAD^{commit}")
    lines = done.stdout.decode("ascii", "replace").split()
    if done.returncode != 0:
        if lines[:1] == ["true"]:
            raise GitUnavailable("the repository has no commit yet")
        raise _failure(done, ("rev-parse",))
    if lines[:1] != ["true"] or len(lines) != 2 or not _SHA.fullmatch(lines[1]):
        raise GitUnavailable("not inside a Git working tree")
    return lines[1]


def resolve(root: Path, revision: str) -> str:
    if not revision or revision.startswith("-"):
        raise GitUnavailable(f"'{revision}' is not a revision")
    done = _run(root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    found = done.stdout.decode("ascii", "replace").strip()
    if done.returncode != 0 or not _SHA.fullmatch(found):
        if any("not a git repository" in line.lower()
               for line in done.stderr.decode("utf-8", "replace").splitlines()):
            raise GitUnavailable("not a Git repository")
        raise GitUnavailable(f"revision {revision} is not a commit in this repository")
    return found


def _paths(data: bytes) -> list[bytes]:
    parts = data.split(b"\0")
    return parts[:-1] if parts and parts[-1] == b"" else parts


def _decode(raw: bytes, skipped: list[str]) -> str | None:
    """A path a record can hold, or None (and the reason kept) when it cannot."""
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError:
        skipped.append(raw.decode("utf-8", "replace") + " (not UTF-8)")
        return None
    if path != path.strip() or "\n" in path or "\r" in path:
        skipped.append(path.replace("\n", "\\n").replace("\r", "\\r") + " (whitespace a record cannot hold)")
        return None
    problem = reference_path_problem(path)
    if problem:
        skipped.append(f"{path} ({problem})")
        return None
    return path


def _owned(path: str | None) -> bool:
    return path is not None and (path + "/").startswith(OWN_DIR)


def _name_status(fields: list[bytes], skipped: list[str]) -> list[Change]:
    found: list[Change] = []
    i = 0
    while i < len(fields):
        letter = fields[i][:1].decode("ascii", "replace")
        if letter in ("R", "C"):
            old_path, path = _decode(fields[i + 1], skipped), _decode(fields[i + 2], skipped)
            i += 3
            if path is None:
                continue
            if letter == "C" or old_path is None or _owned(old_path):
                found.append(Change("added", path))
            else:
                found.append(Change("renamed", path, old_path))
            continue
        path = _decode(fields[i + 1], skipped)
        i += 2
        if path is not None:
            found.append(Change({"A": "added", "D": "deleted"}.get(letter, "modified"), path))
    return found


def changes(root: Path, base: str, until: str | None) -> tuple[list[Change], list[str]]:
    """Files changed from `base` to `until`, or to the working tree when `until` is None.

    Returns the changes and the paths a record cannot hold. Git's own ignore
    rules decide what is ignored; only `.praukron/` is excluded besides. A
    working-tree capture with no change costs two Git calls.
    """
    diff = ("diff", "-z", "--name-status", "-M", "--no-color", "--no-ext-diff", "--no-textconv", "--relative")
    skipped: list[str] = []
    found = _name_status(_paths(_git(root, *diff, base, *([until] if until else []), "--")), skipped)
    untracked: set[str] = set()
    if until is None:
        for raw in _paths(_git(root, "ls-files", "-z", "--others", "--exclude-standard")):
            path = _decode(raw, skipped)
            if path is not None:
                untracked.add(path)
                found.append(Change("added", path))
    found = [c for c in found if not _owned(c.path)]
    if not found:
        return [], skipped
    # A tracked file can still match an ignore rule (a committed build output or
    # dependency); Git's rules say it is ignored, so it is not recorded either.
    listed = b"".join(c.path.encode("utf-8") + b"\0" for c in found)
    ignored = {p.decode("utf-8", "replace") for p in _paths(
        _git(root, "check-ignore", "-z", "--stdin", "--no-index", stdin=listed, ok=(0, 1)))}
    found = [c for c in found if c.path not in ignored]
    if until is None and found:
        dirty = set(untracked)
        for raw in _paths(_git(root, "diff", "-z", "--name-only", "--no-renames", "--relative", "HEAD", "--")):
            try:
                dirty.add(raw.decode("utf-8"))
            except UnicodeDecodeError:
                continue
        found = [Change(c.kind, c.path, c.old_path, c.path in dirty or (c.old_path or "") in dirty)
                 for c in found]
    unique: dict[str, Change] = {}
    for change in found:
        unique.setdefault(change.path, change)
    return sorted(unique.values(), key=lambda c: c.path), skipped


def _window(root: Path, base: str, head_commit: str) -> set[str]:
    """The commits after `base` up to and including `head_commit`."""
    return set(_git(root, "rev-list", f"{base}..{head_commit}", "--").decode("ascii").split())


def _is_ancestor(root: Path, commit: str, of: str) -> bool:
    if commit == of:
        return True
    done = _run(root, "merge-base", "--is-ancestor", commit, of)
    return done.returncode == 0


def _observed(project: Project, task_id: str) -> list:
    superseded = refs.replaced_by(project.references)
    return [r for r in project.references
            if r.task == task_id and r.scope == "observed" and refs.is_current(r, superseded)]


def overlapping(project: Project, root: Path, task_id: str, base: str, head_commit: str,
                paths: set[str]) -> dict[str, list[str]]:
    """For each path, the other tasks that may also have changed it in this range.

    Another task still claimed (WIP or BLOCKED) whose `Claimed at` is an
    ancestor of, or equal to, `head_commit` may have changed any file. A DONE
    task may have changed only the files its own capture recorded, and only if
    that capture is not wholly before `base`: its head is after `base`, or its
    head is `base` and the change was still uncommitted then (no `Revision`).
    Git alone cannot tell such changes apart, so they are never guessed.
    """
    others = [t for t in project.tasks if t.id != task_id and t.claimed_at]
    if not others or not paths:
        return {}
    window: set[str] | None = None
    blame: dict[str, list[str]] = {}
    for other in others:
        if other.status in _ACTIVE:
            if _is_ancestor(root, other.claimed_at, head_commit):
                for path in paths:
                    blame.setdefault(path, []).append(other.id)
            continue
        if not other.done:
            continue
        captured = [r for r in _observed(project, other.id) if r.provenance == "git_diff" and r.head]
        if not captured:
            continue
        if window is None:
            window = _window(root, base, head_commit)
        touched = set()
        for record in captured:
            if record.head in window or (record.head == base and not record.revision):
                touched.update(p for p in (record.path, record.was) if p)
        for path in paths & touched:
            blame.setdefault(path, []).append(other.id)
    return blame


def plan(project: Project, root: Path, task, base: str, head_commit: str, *, working_tree: bool,
         actor: str) -> tuple[dict, dict, list[str]]:
    """The CODE.md change for one capture, the reply's `capture` member, and the new ids.

    Raises GitUnavailable when Git cannot answer. Returns no change when every
    changed file is already recorded as observed for the task with the same
    relation, so a second capture adds only what is new.
    """
    found, skipped = changes(root, base, None if working_tree else head_commit)
    existing = {(r.path, r.relation) for r in _observed(project, task.id) if r.symbol is None}
    fresh = [c for c in found if (c.path, c.relation) not in existing]
    blame = overlapping(project, root, task.id, base, head_commit,
                        {c.path for c in fresh} | {c.old_path for c in fresh if c.old_path})
    sequence = int(ref._next_id(project, task.id).rsplit("-", 1)[1])
    today = dt.date.today().isoformat()
    records, ids = [], []
    unresolved = 0
    for change in fresh:
        others = sorted(set(blame.get(change.path, []) + blame.get(change.old_path or "", [])))
        fields = {"Task": task.id, "Path": change.path, "Relation": change.relation, "Scope": "observed"}
        if others:
            fields["Lifecycle"] = "unresolved"
            unresolved += 1
        elif change.kind == "deleted":
            fields["Lifecycle"] = "deleted"
        fields.update({"Verification": "verified", "Authorization": "not_applicable", "Source": "git_diff",
                       "Recorded": f"{today} · {actor}"})
        if not change.uncommitted:
            fields["Revision"] = head_commit
        fields.update({"Base": base, "Head": head_commit})
        if change.old_path:
            fields["Was"] = change.old_path
        where = "the working tree at " if change.uncommitted else ""
        evidence = f"git diff {base[:12]} to {where}{head_commit[:12]}: {change.kind}"
        if others:
            evidence += (f"; unresolved attribution: {', '.join(others)} "
                         f"{'was' if len(others) == 1 else 'were'} also claimed over this range")
        fields["Evidence"] = evidence
        title = f"{change.path} ({change.kind}{' from ' + change.old_path if change.old_path else ''})"
        reference_id = f"CR-{task.id}-{sequence:02d}"
        records.append(ref._render(reference_id, title, fields))
        ids.append(reference_id)
        sequence += 1
    info = {"status": "captured", "references": len(records), "base": base, "head": head_commit,
            "workingTree": working_tree, "alreadyRecorded": len(found) - len(fresh),
            "unresolved": unresolved}
    if skipped:
        info["skippedPaths"] = skipped
    if not records:
        return {}, info, []
    path = root / AUTHORITY_DIR / ref.FILE
    return {path: ref._append(path, "\n".join(records))}, info, ids


def skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}


def actor(owner: str | None) -> str:
    text = " ".join((owner or "").split())
    return text if text and text.lower() not in _UNASSIGNED else "praukron/scope-capture"


def claim_commit(root: Path) -> tuple[str | None, dict]:
    """The commit to record on a claimed task, or None with the reason; never raises."""
    try:
        commit = head(root)
    except GitUnavailable as error:
        return None, skipped(str(error))
    except Exception as error:  # capture never blocks a write
        return None, skipped(f"git could not be read: {error}")
    return commit, {"status": "recorded", "commit": commit}


def at_close(project: Project, root: Path, task, owner: str | None) -> tuple[dict, dict]:
    """The capture a DONE write makes by itself; never raises."""
    if not task.claimed_at:
        return {}, skipped("the task has no Claimed at commit")
    try:
        current = head(root)
        # A full commit id needs no lookup of its own; a missing one is named below.
        base = task.claimed_at if _SHA.fullmatch(task.claimed_at) else resolve(root, task.claimed_at)
        try:
            planned, info, _ = plan(project, root, task, base, current, working_tree=True, actor=actor(owner))
        except GitUnavailable:
            resolve(root, base)
            raise
        return planned, info
    except GitUnavailable as error:
        return {}, skipped(str(error))
    except Exception as error:  # capture never blocks a write
        return {}, skipped(f"capture failed: {error}")


def capture(root: Path, task_id: str, base: str, until: str | None = None, *, dry_run: bool = False) -> dict:
    """`praukron scope capture`: the same capture between two named revisions.

    It plans through the same code as a close and commits through the same
    transaction, so it is validated and refreshed like any structured write.
    """
    root = root.resolve()
    operation = "scope.capture"
    info: dict = {}

    def prepare(project: Project) -> mutate.Plan:
        task = project.task(task_id)
        if task is None:
            raise mutate.WriteError("record-not-found", f"no task {task_id}", "task")
        mutate.ensure_mutable(task.source)
        try:
            head_commit = resolve(root, until) if until else head(root)
            base_commit = resolve(root, base)
            planned, found, ids = plan(project, root, task, base_commit, head_commit,
                                       working_tree=until is None, actor=actor(task.owner))
        except GitUnavailable as error:
            raise mutate.WriteError("capture-failed", str(error), "base") from error
        info.update(found)
        if not planned:
            raise mutate.WriteError("no-change", "every changed file is already recorded",
                                    details={"capture": found})
        return mutate.Plan(planned, ids[0], f"{ref.FILE}#{ids[0]}")

    result = mutate.transaction(root, operation, prepare, dry_run=dry_run)
    error = result.get("error", {})
    if not result.get("ok") and error.get("code") == "no-change" and "capture" in error:
        return {"ok": True, "operation": operation, "written": False, "refreshed": False,
                "capture": error["capture"]}
    if result.get("ok"):
        result["capture"] = info
    return result


def command(root: Path, args) -> int:
    result = capture(root, args.task, args.base, args.head, dry_run=args.dry_run)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") else 1
