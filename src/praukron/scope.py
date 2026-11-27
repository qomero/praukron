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
change type and paths. `changed_symbols` reads the old text (at `base`) and
the new text (at `head`, or in the working tree) of each changed Python file
and `symbols` attributes the changed lines to functions, classes and methods
(T-CIL-SCOPE-02), recorded in the same write as the files.
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

from . import mutate, ref, refs, symbols
from .layout import AUTHORITY_DIR
from .model import Project, reference_path_problem

TIMEOUT = 20
# Everything Praukron owns; never recorded as a task's change.
OWN_DIR = ".praukron/"
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# Statuses of a task that is claimed and not yet closed.
_ACTIVE = ("WIP", "BLOCKED")
_UNASSIGNED = {"unassigned", "none", "—", "-"}
# Evidence of a capture whose two revisions the caller named (T-CIL-SCOPE-06).
NAMED = "range named by the caller"
_PAST = {"creates": "created", "modifies": "modified", "deletes": "deleted"}
# Paths that are never a task's work, applied to untracked files only (SPEC
# §2.9, T-CIL-SCOPE-05): a repository with no ignore file still keeps caches,
# dependencies and operating-system litter out of a capture. A tracked file is
# always recorded. Nothing broader: `build`, `dist`, `target` and `vendor` are
# source directories in some projects.
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules"})
CACHE_FILES = frozenset({".DS_Store", "Thumbs.db"})
CACHE_SUFFIXES = (".pyc", ".pyo")


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


def never_work(path: str) -> bool:
    """True when an untracked `path` is on the built-in list of non-work paths."""
    *directories, name = path.split("/")
    return (any(part in CACHE_DIRS for part in directories) or name in CACHE_FILES
            or name.endswith(CACHE_SUFFIXES))


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


def changes(root: Path, base: str, until: str | None) -> tuple[list[Change], list[str], int]:
    """Files changed from `base` to `until`, or to the working tree when `until` is None.

    Returns the changes, the paths a record cannot hold, and how many
    untracked files the built-in list (`never_work`) left out. Git's own
    ignore rules decide what is ignored; besides them only `.praukron/` and,
    for untracked files, that list are excluded. A working-tree capture with
    no change costs two Git calls.
    """
    diff = ("diff", "-z", "--name-status", "-M", "--no-color", "--no-ext-diff", "--no-textconv", "--relative")
    skipped: list[str] = []
    found = _name_status(_paths(_git(root, *diff, base, *([until] if until else []), "--")), skipped)
    untracked: set[str] = set()
    left_out = 0
    if until is None:
        for raw in _paths(_git(root, "ls-files", "-z", "--others", "--exclude-standard")):
            path = _decode(raw, skipped)
            if path is None or _owned(path):
                continue
            if never_work(path):
                left_out += 1
                continue
            untracked.add(path)
            found.append(Change("added", path))
    found = [c for c in found if not _owned(c.path)]
    if not found:
        return [], skipped, left_out
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
    return sorted(unique.values(), key=lambda c: c.path), skipped, left_out


def _window(root: Path, base: str, head_commit: str) -> set[str]:
    """The commits after `base` up to and including `head_commit`."""
    return set(_git(root, "rev-list", f"{base}..{head_commit}", "--").decode("ascii").split())


def _is_ancestor(root: Path, commit: str, of: str) -> bool:
    if commit == of:
        return True
    done = _run(root, "merge-base", "--is-ancestor", commit, of)
    return done.returncode == 0


def merged_in(root: Path, base: str, head_commit: str) -> tuple[list[str], dict[str, list[str]]]:
    """The merge commits of `base..head_commit` and the paths their other parents brought in.

    Git's ancestry alone decides (T-SCOPE-ATTRIB-01): the first-parent chain of
    the range is the line of work the capture is for, and every commit that
    entered the range only through a merge's second or later parent is some
    other line's work. Returns the merges on the first-parent chain, oldest
    first, and for each path changed by such a commit (a merge commit's own
    resolution is not counted) the merges that brought it in. Nothing is read
    from commit messages or authors. Work of others that arrives without a
    merge commit (a squash or rebase merge, a fast-forward) has no merge here
    and cannot be told apart.
    """
    chain = _git(root, "rev-list", "--first-parent", "--parents", "--reverse", f"{base}..{head_commit}", "--")
    merges = [parts for parts in (line.split() for line in chain.decode("ascii").splitlines()) if len(parts) > 2]
    paths: dict[str, list[str]] = {}
    for commit, first, *_ in merges:
        # Commits reachable from this merge but from neither its first parent nor the base.
        listed = _git(root, "log", "-z", "--no-merges", "--no-renames", "--name-only", "--format=",
                      "--relative", commit, "--not", first, base, "--")
        for raw in _paths(listed):
            raw = raw.strip(b"\n")
            if not raw:
                continue
            path = raw.decode("utf-8", "replace")
            if commit not in paths.setdefault(path, []):
                paths[path].append(commit)
    return [m[0] for m in merges], paths


def attributable(reference) -> bool:
    """False for an observed reference recorded as unresolved attribution."""
    return not (reference.scope == "observed" and reference.lifecycle == "unresolved")


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


def _sides(root: Path, change: Change, base: str, head_commit: str, working_tree: bool) -> tuple:
    """Where a change's old and new text are read: a `rev:./path` spec, a Path, or None."""
    old = None if change.kind == "added" else f"{base}:./{change.old_path or change.path}"
    if change.kind == "deleted":
        new = None
    elif working_tree:
        new = root / change.path
    else:
        new = f"{head_commit}:./{change.path}"
    return old, new


def _hunks(root: Path, compared: list[Change], base: str, until: str | None) -> dict:
    """The changed line ranges on each side of each compared file, from one `git diff -U0`.

    Git's own diff names the lines, so the cost grows with the change, not
    with the file. Hunks are never merged across unchanged lines, every file
    is compared as text, and the user's diff programs and prefixes are not
    used. Renames are detected as in `changes`, so a renamed file's old side
    is its old path.
    """
    if not compared:
        return {}
    paths = sorted({p for c in compared for p in (c.path, c.old_path) if p})
    patch = _git(root, "--literal-pathspecs", "-c", "core.quotePath=false", "diff", "-U0",
                 "--inter-hunk-context=0", "--no-color", "--no-ext-diff", "--no-textconv", "--text", "-M",
                 "--relative", "--src-prefix=a/", "--dst-prefix=b/", base, *([until] if until else []),
                 "--", *paths)
    return symbols.hunks(patch)


def changed_symbols(root: Path, found: list[Change], base: str, head_commit: str, *,
                    working_tree: bool) -> tuple[dict[str, list[symbols.Changed]], dict[str, str]]:
    """The changed symbols of each changed file, and why a file's symbols are unresolved.

    Only changed files are read, a Python file only when it is within
    `symbols.MAX_FILE_BYTES` on each side it needs and the capture's reading
    stays within `symbols.MAX_CAPTURE_BYTES`, and none when the capture holds
    more than `symbols.MAX_FILES` changed Python files. Never raises: a
    failure leaves the affected files unresolved with the reason.
    """
    result: dict[str, list[symbols.Changed]] = {}
    unresolved = {c.path: symbols.NO_EXTRACTOR for c in found if not symbols.is_python(c.path)}
    python = [c for c in found if symbols.is_python(c.path)]
    if len(python) > symbols.MAX_FILES:
        unresolved.update({c.path: f"more than {symbols.MAX_FILES} changed Python files in one capture"
                           for c in python})
        return result, unresolved
    try:
        sides = {c.path: _sides(root, c, base, head_commit, working_tree) for c in python}
        sizes = symbols.blob_sizes(_git, root, [s for pair in sides.values() for s in pair if isinstance(s, str)])
        sizes.update({s: symbols.file_size(s) for pair in sides.values() for s in pair if isinstance(s, Path)})
        # Sizes first, so nothing over a limit is read: per file, then for the capture, in path order.
        admitted: list[Change] = []
        spent = 0
        for change in python:
            needed = [s for s in sides[change.path] if s is not None]
            if any(sizes.get(s) is None for s in needed):
                unresolved[change.path] = "its text could not be read"
                continue
            large = [sizes[s] for s in needed if sizes[s] > symbols.MAX_FILE_BYTES]
            if large:
                unresolved[change.path] = f"file larger than {symbols.MAX_FILE_BYTES} bytes ({large[0]} bytes)"
                continue
            cost = sum(sizes[s] for s in needed)
            if spent + cost > symbols.MAX_CAPTURE_BYTES:
                reason = (f"the capture's symbol budget of {symbols.MAX_CAPTURE_BYTES} bytes of Python source "
                          f"is spent")
                for later in python[python.index(change):]:
                    unresolved.setdefault(later.path, reason)
                break
            spent += cost
            admitted.append(change)
        blobs = symbols.read_blobs(_git, root, [s for c in admitted for s in sides[c.path] if isinstance(s, str)])
        texts: dict[str, tuple[bytes | None, bytes | None]] = {}
        for change in admitted:
            old_at, new_at = sides[change.path]
            old = blobs.get(old_at) if old_at else None
            new = symbols.read_file(new_at) if isinstance(new_at, Path) else (blobs.get(new_at) if new_at else None)
            if (old_at and old is None) or (new_at and new is None):
                unresolved[change.path] = "its text could not be read"
                continue
            if new is not None and len(new) > symbols.MAX_FILE_BYTES:  # grew after it was measured
                unresolved[change.path] = f"file larger than {symbols.MAX_FILE_BYTES} bytes ({len(new)} bytes)"
                continue
            texts[change.path] = (old, new)
        # Both sides of a changed text: Git names the changed lines, in one call for the capture.
        compared = [c for c in python if c.path in texts and None not in texts[c.path]
                    and texts[c.path][0] != texts[c.path][1]]
        ranges = _hunks(root, compared, base, None if working_tree else head_commit)
        for change in python:
            if change.path not in texts:
                continue
            old, new = texts[change.path]
            if change in compared and change.path not in ranges:
                unresolved[change.path] = "git reported no changed lines for a changed text"
                continue
            try:
                result[change.path] = symbols.changed(old, new, *ranges.get(change.path, ((), ())))
            except symbols.Unresolved as error:
                unresolved[change.path] = str(error)
    except Exception as error:  # symbol capture never blocks the file capture
        for change in python:
            if change.path not in result:
                unresolved.setdefault(change.path, f"symbol extraction failed: {error}")
    return result, unresolved


def _attribution(others: list[str], merges: list[str]) -> str:
    text = ""
    if others:
        text += (f"; unresolved attribution: {', '.join(others)} "
                 f"{'was' if len(others) == 1 else 'were'} also claimed over this range")
    if merges:
        text += (f"; unresolved attribution: changed by commits merged in through "
                 f"{', '.join(m[:12] for m in merges)}, not on this range's first-parent line")
    return text


def warning(task_id: str, count: int) -> str:
    """The reply's one sentence when a capture wrote unattributed references."""
    return (f"{count} {'reference is' if count == 1 else 'references are'} not attributed to {task_id}: "
            f"another claimed task or a merged-in commit may have made the change, so "
            f"{'it is' if count == 1 else 'they are'} recorded Lifecycle unresolved and Verification "
            f"unverified, never as the task's work; settle them with a named capture over the task's own range "
            f"(`praukron scope capture {task_id} --base <rev> --head <rev>`) or with `praukron ref set` on each "
            f"record (confirm with evidence, or withdraw).")


def plan(project: Project, root: Path, task, base: str, head_commit: str, *, working_tree: bool,
         actor: str, named: bool = False) -> tuple[dict, dict, list[str]]:
    """The CODE.md change for one capture, the reply's `capture` member, and the new ids.

    `named` is true when the caller named both revisions (`scope capture
    --base --head`, T-CIL-SCOPE-06): the caller vouches that the range is the
    task's, so no overlap is looked for and nothing is unresolved; each
    record's evidence says the range was named. Otherwise other tasks claimed
    over the range make the records unresolved (`overlapping`).

    Raises GitUnavailable when Git cannot answer. Returns no change when every
    changed file and symbol is already recorded as observed for the task with
    the same relation, so a second capture adds only what is new.
    """
    found, skipped, left_out = changes(root, base, None if working_tree else head_commit)
    every_observed = _observed(project, task.id)
    # A named capture settles the task's unresolved records: they never count as
    # recorded, and the record written for the same artifact replaces each one.
    settling = {} if not named else {
        (r.path, r.symbol, r.relation): r.id for r in every_observed if not attributable(r)}
    observed = [r for r in every_observed if not named or attributable(r)]
    existing = {(r.path, r.relation) for r in observed if r.symbol is None}
    existing_symbols = {(r.path, r.symbol, r.relation) for r in observed if r.symbol is not None}
    fresh_files = {c.path for c in found if (c.path, c.relation) not in existing}
    per_file, unresolved_symbols = changed_symbols(root, found, base, head_commit, working_tree=working_tree)
    fresh_symbols: dict[str, list[tuple[str, symbols.Changed]]] = {}
    known = 0
    for change in found:
        for item in per_file.get(change.path, []):
            where = change.old_path if item.from_base and change.old_path else change.path
            if (where, item.name, item.relation) in existing_symbols:
                known += 1
            else:
                fresh_symbols.setdefault(change.path, []).append((where, item))
    # At most symbols.MAX_RECORDS symbol records, whole files only, in path order.
    written, full = 0, False
    for change in found:
        if change.path not in per_file:
            continue
        count = len(fresh_symbols.get(change.path, []))
        cap = f"the capture's cap of {symbols.MAX_RECORDS} symbol records is reached"
        if not full and written + count > symbols.MAX_RECORDS:
            full = True
            cap += (f": this file has {count} changed symbols and "
                    f"{symbols.MAX_RECORDS - written} records were left")
        if full:
            fresh_symbols.pop(change.path, None)
            per_file.pop(change.path)
            unresolved_symbols[change.path] = cap
        else:
            written += count
    involved =[c for c in found if c.path in fresh_files or c.path in fresh_symbols]
    blame = {} if named else overlapping(project, root, task.id, base, head_commit,
                                         {c.path for c in involved} | {c.old_path for c in involved if c.old_path})
    merges, merged = ([], {}) if named or not involved else merged_in(root, base, head_commit)
    sequence = int(ref._next_id(project, task.id).rsplit("-", 1)[1])
    today = dt.date.today().isoformat()
    records, ids = [], []
    unresolved = files = symbol_count = 0
    replaced: list[str] = []
    contributing: set[str] = set()

    def add(title: str, fields: dict, key: tuple) -> None:
        nonlocal sequence
        reference_id = f"CR-{task.id}-{sequence:02d}"
        earlier = settling.pop(key, None)
        if earlier:
            fields["Replaces"] = earlier
            fields["Evidence"] += f"; settles {earlier}, recorded unresolved"
            replaced.append(earlier)
        records.append(ref._render(reference_id, title, fields))
        ids.append(reference_id)
        sequence += 1

    for change in involved:
        others = sorted(set(blame.get(change.path, []) + blame.get(change.old_path or "", [])))
        via = [m for m in merges if m in merged.get(change.path, []) + merged.get(change.old_path or "", [])]
        contributing.update(via)
        doubt = bool(others or via)
        where = "the working tree at " if change.uncommitted else ""
        span = f"git diff {base[:12]} to {where}{head_commit[:12]}"
        if named:
            span += f" ({NAMED})"
        if change.path in fresh_files:
            fields = {"Task": task.id, "Path": change.path, "Relation": change.relation, "Scope": "observed"}
            if doubt:
                fields["Lifecycle"] = "unresolved"
                unresolved += 1
            elif change.kind == "deleted":
                fields["Lifecycle"] = "deleted"
            # An unattributed change is never verified as the task's (T-SCOPE-ATTRIB-01).
            fields.update({"Verification": "unverified" if doubt else "verified",
                           "Authorization": "not_applicable", "Source": "git_diff",
                           "Recorded": f"{today} · {actor}"})
            if not change.uncommitted:
                fields["Revision"] = head_commit
            fields.update({"Base": base, "Head": head_commit})
            if change.old_path:
                fields["Was"] = change.old_path
            evidence = f"{span}: {change.kind}" + _attribution(others, via)
            if change.path in unresolved_symbols:
                evidence += f"; symbols unresolved: {unresolved_symbols[change.path]}"
            fields["Evidence"] = evidence
            add(f"{change.path} ({change.kind}{' from ' + change.old_path if change.old_path else ''})", fields,
                (change.path, None, change.relation))
            files += 1
        for path, item in fresh_symbols.get(change.path, []):
            fields = {"Task": task.id, "Path": path, "Symbol": item.name, "Kind": item.kind,
                      "Relation": item.relation, "Scope": "observed"}
            if doubt:
                fields["Lifecycle"] = "unresolved"
                unresolved += 1
            elif item.relation == "deletes":
                fields["Lifecycle"] = "deleted"
            fields.update({"Verification": "unverified" if doubt else "verified", "Authorization": "not_applicable",
                           "Source": "static_analysis", "Recorded": f"{today} · {actor}"})
            # A deleted symbol's name is true at the base; any other at the head commit,
            # unless its file's change is only in the working tree.
            if item.from_base:
                fields["Revision"] = base
            elif not change.uncommitted:
                fields["Revision"] = head_commit
            fields.update({"Base": base, "Head": head_commit, "Lines": f"{item.start}-{item.end}"})
            fields["Evidence"] = (f"{span}: {item.kind} {item.name} {_PAST[item.relation]} (Python ast)"
                                  + _attribution(others, via))
            add(f"{path}#{item.name} ({_PAST[item.relation]})", fields, (path, item.name, item.relation))
            symbol_count += 1
    info = {"status": "captured", "references": len(records), "files": files, "symbols": symbol_count,
            "base": base, "head": head_commit, "workingTree": working_tree,
            "alreadyRecorded": len(found) - len(fresh_files) + known, "unresolved": unresolved}
    if named:
        info["rangeNamed"] = True
        if replaced:
            info["settled"] = replaced
        if settling:
            info["stillUnresolved"] = sorted(set(settling.values()))
    if merges:
        info["merges"] = [m[:12] for m in merges]
        info["mergesInvolved"] = [m[:12] for m in merges if m in contributing]
    if unresolved:
        info["warning"] = warning(task.id, unresolved)
    if unresolved_symbols:
        info["symbolsUnresolved"] = [{"path": p, "reason": r} for p, r in sorted(unresolved_symbols.items())]
    if skipped:
        info["skippedPaths"] = skipped
    if left_out:
        info["untrackedLeftOut"] = left_out
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

    With `until` named, the caller names both revisions and vouches for the
    range, so the capture is attributed to the task with no overlap check
    (T-CIL-SCOPE-06). Without it, the capture reads the working tree and
    keeps the overlap rule of a close.

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
                                       working_tree=until is None, actor=actor(task.owner),
                                       named=until is not None)
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
