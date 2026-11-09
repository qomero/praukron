"""Temporal Memory: the chronicle as it stood at a Git revision (ADR-093).

`state_at_commit` extracts the chronicle committed at one revision into a fresh
temporary directory and compiles it with the current compiler, through the
same `compile.load` and `as_json` that `praukron compile` uses: there is no
second reducer. Nothing is checked out and no working tree, index or branch is
touched; only `git rev-parse`, `ls-tree`, `archive`, `rev-list`, `log`, `grep`
and `merge-base` run. A revision the compiler cannot read is UNSUPPORTED with
its reason, never migrated.

`diff` compares two such states and reports OBSERVED_STATE_TRANSITIONS of
authoritative records by stable ID. Git history is a version history that can
be rewritten, not an event log: a transition is pinned to the interval between
the two revisions, never to a single time, and where history cannot decide the
answer is INSUFFICIENT_EVIDENCE. Nothing is inferred from timing alone.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from . import compile as compiler
from . import layout, validate

# Where a chronicle has lived, tried in this order (T-PTM-INSPECT-01 section 4).
# `chronicle` is the separate-repository layout, whose work tree is `.praukron`;
# the root is the backup's first era, when the chronicle files sat at its top.
CANDIDATES: tuple[tuple[str, str], ...] = (
    (layout.AUTHORITY_DIR, "current"),
    (".prokron/chronicle", "prokron-chronicle"),
    (layout.V02_AUTHORITY_DIR, "v0.2"),
    (layout.V01_AUTHORITY_DIR, "v0.1"),
    ("chronicle", "separate-repository"),
    ("", "root"),
)

STATE = "STATE"
UNSUPPORTED = "UNSUPPORTED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
NOT_ANCESTRAL = "NOT_ANCESTRAL"
OBSERVED = "OBSERVED_STATE_TRANSITIONS"


class HistoryError(Exception):
    """A repository or revision git cannot resolve."""


class Repository:
    """A Git repository read through `git -C <dir>` or `git --git-dir <dir>`."""

    def __init__(self, directory: Path | None = None, git_dir: Path | None = None) -> None:
        if git_dir is not None:
            self.args = ["--git-dir", str(Path(git_dir).resolve())]
            self.identity = str(Path(git_dir).resolve())
            self.mode = "separate-repository"
        else:
            start = Path(directory or Path.cwd()).resolve()
            self.args = ["-C", str(start)]
            top = self.git("rev-parse", "--show-toplevel").strip()
            self.identity = str(Path(top).resolve())
            self.mode = "git"

    def git(self, *command: str, binary: bool = False, check: bool = True):
        try:
            result = subprocess.run(["git", *self.args, *command], capture_output=True)
        except OSError as error:
            raise HistoryError(f"git cannot run: {error}") from error
        if check and result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise HistoryError(message or f"git {' '.join(command)} failed")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def ok(self, *command: str) -> bool:
        return subprocess.run(["git", *self.args, *command], capture_output=True).returncode == 0

    def resolve(self, revision: str) -> str:
        found = self.git("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}", check=False).strip()
        return found or _missing(revision)

    def project_name(self) -> str:
        """The name `compile` would fall back to in a checkout of this repository."""
        if self.mode == "git":
            return Path(self.identity).name
        worktree = self.git("config", "--get", "core.worktree", check=False).strip()
        if worktree:
            path = Path(worktree)
            # The backup's work tree is `<project>/.praukron`; the project is its parent.
            return path.parent.name if path.name == layout.HOME_DIR else path.name
        return Path(self.identity).parent.name

    def has(self, revision: str, path: str) -> bool:
        return bool(self.git("ls-tree", "--name-only", revision, "--", path).strip())


def _missing(revision: str) -> str:
    raise HistoryError(f"unknown revision: {revision}")


def detect(repo: Repository, revision: str) -> tuple[str, str] | None:
    """The chronicle path and layout at a revision, or None when it has none."""
    for path, name in CANDIDATES:
        if repo.has(revision, f"{path}/TASKS.md" if path else "TASKS.md"):
            return path, name
    return None


def _unsupported_layout(repo: Repository, revision: str, path: str, name: str) -> str | None:
    """Why the current compiler cannot read this layout, without compiling it."""
    if not repo.has(revision, f"{path}/PHASES.md" if path else "PHASES.md"):
        return f"the {name} layout has no PHASES.md, which the current compiler requires"
    return None


def compiler_provenance() -> dict[str, object]:
    """The compiler's VERSION, and its code commit when it runs from a source checkout."""
    here = Path(__file__).resolve().parent
    found = {"version": layout.version(), "commit": None, "clean": None}
    try:
        top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
    except OSError:
        return found
    root = Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else None
    # An installed runtime sits inside the project it tracks; that project's
    # commit says nothing about the compiler, so only a source checkout counts.
    if root is None or not (root / "VERSION").is_file() or (root / "src" / "praukron").resolve() != here:
        return found
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain", "--", "src", "VERSION"],
                           capture_output=True, text=True)
    if head.returncode == 0:
        found["commit"] = head.stdout.strip()
        found["clean"] = dirty.returncode == 0 and not dirty.stdout.strip()
    return found


def _extract(repo: Repository, revision: str, path: str, target: Path) -> None:
    """Write the chronicle files committed at `revision` under `target`."""
    data = repo.git("archive", "--format=tar", revision, *([path] if path else []), binary=True)
    prefix = PurePosixPath(path) if path else None
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = PurePosixPath(member.name)
            if prefix is not None:
                if prefix not in name.parents:
                    continue
                name = name.relative_to(prefix)
            if name.is_absolute() or ".." in name.parts:
                continue
            destination = target.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            destination.write_bytes(source.read() if source else b"")


def _scrub(text: str, base: Path, path: str) -> str:
    """Name files as they are in the repository, not in the temporary directory."""
    shown = f"{path}/" if path else ""
    for form in {str(base), str(base).replace("\\", "/")}:
        text = text.replace(form + "\\", shown).replace(form + "/", shown).replace(form, path or ".")
    return text


def state_at_commit(repo: Repository, revision: str) -> dict[str, object]:
    """The chronicle at one revision, compiled read-only by the current compiler."""
    full = repo.resolve(revision)
    result: dict[str, object] = {
        "status": UNSUPPORTED,
        "repository": repo.identity,
        "mode": repo.mode,
        "revision": full,
        "committed": repo.git("log", "-1", "--format=%cI", full).strip(),
        "chronicle": None,
        "layout": None,
        "compiler": compiler_provenance(),
        "reason": None,
        "validation": None,
        "compileError": None,
        "state": None,
        "changeRequests": [],
    }
    found = detect(repo, full)
    if found is None:
        result["reason"] = "no chronicle at this revision (no TASKS.md in " + ", ".join(
            p or "the repository root" for p, _ in CANDIDATES) + ")"
        return result
    path, name = found
    result["chronicle"], result["layout"] = path or ".", name
    refused = _unsupported_layout(repo, full, path, name)
    if refused:
        result["reason"] = refused
        return result
    workspace = Path(tempfile.mkdtemp(prefix="praukron-history-"))
    try:
        # Named like the repository, so a PHASES.md that names no project
        # compiles under the same name as a checkout would.
        root = workspace / repo.project_name()
        authority = root / layout.AUTHORITY_DIR
        authority.mkdir(parents=True)
        _extract(repo, full, path, authority)
        try:
            project = compiler.load(root)
        except Exception as error:  # every unreadable record is reported, never raised
            result["reason"] = "the current compiler cannot read it: " + _scrub(
                f"{type(error).__name__}: {error}", authority, path)
            return result
        findings = validate.check(project)
        errors = validate.errors(findings)
        result["validation"] = {
            "rules": f"praukron {layout.version()} (current rules, not the rules of {full[:10]})",
            "errors": len(errors),
            "warnings": sum(1 for f in findings if f.severity == "warning"),
            "findings": [_scrub(f.render(), authority, path) for f in errors],
        }
        try:
            result["state"] = compiler.as_json(project)
        except Exception as error:
            result["compileError"] = _scrub(f"{type(error).__name__}: {error}", authority, path)
            result["reason"] = "compilation failed after the records were read"
            return result
        result["changeRequests"] = [{
            "id": item.id, "criterion": item.criterion, "current": item.current,
            "proposed": item.proposed, "decision": item.decision,
            "source": {"file": item.source.file, "anchor": item.source.anchor},
        } for item in project.change_requests]
        result["status"] = STATE
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# --- the temporal state diff (T-PTM-DIFF-01) ---------------------------------

# Authored fields only. Derived views (counts, obstacles, next, ready, blocks,
# progress) change whenever anything else does and are never transitions.
FIELDS: dict[str, tuple[str, ...]] = {
    "task": ("title", "module", "declaredDomain", "status", "validation", "deps", "ac", "owner", "claimed",
             "evidence", "decisions", "schedule", "implementation", "authority"),
    "criterion": ("text", "evidenceClass", "state", "evidence"),
    "decision": ("title", "status", "supersedes", "affects", "origin", "evidence"),
    "assumption": ("title", "status", "impact", "recorded", "assumption", "basis", "appliedIn",
                   "tasks", "permissions", "reconciledBy"),
    "phase": ("name", "outcome", "entry", "exit", "exitAuthority", "status"),
    "gate": ("name", "description", "blocks", "verifiedBy", "status"),
    "acr": ("criterion", "current", "proposed", "decision"),
}
_TITLE = {"task": "title", "decision": "title", "assumption": "title", "phase": "name",
          "gate": "name", "criterion": "text", "acr": "proposed"}


def records(found: dict[str, object]) -> dict[tuple[str, str], dict]:
    """Authoritative records of one state, keyed by (kind, stable ID)."""
    state = found["state"]
    out: dict[tuple[str, str], dict] = {}
    for kind, key in (("task", "tasks"), ("decision", "decisions"), ("assumption", "assumptions"),
                      ("phase", "phases"), ("gate", "gates")):
        for item in state.get(key) or []:
            if kind == "task":
                # `domain` is derived from the module unless the task declares it.
                item = {**item, "declaredDomain": item.get("domain") if item.get("domainSource") == "declared" else None}
            out[(kind, item["id"])] = item
    for contract_id, contract in (state.get("acceptance") or {}).items():
        source = contract.get("source") or {"file": "ACCEPTANCE.md", "anchor": contract_id}
        for criterion in contract.get("criteria") or []:
            out[("criterion", criterion["id"])] = {**criterion, "source": criterion.get("source") or source}
    for item in found.get("changeRequests") or []:
        out[("acr", item["id"])] = item
    return out


def _where(item: dict) -> str:
    source = item.get("source") or {}
    return f"{source.get('file')}#{source.get('anchor')}"


def _archived(item: dict) -> bool:
    return str((item.get("source") or {}).get("file", "")).startswith(f"{layout.ARCHIVE_DIR}/")


def _heading_pattern(record_id: str) -> str:
    return "^#+ " + re.escape(record_id) + "([^-A-Za-z0-9_]|$)"


def _present(repo: Repository, revision: str, path: str, kind: str, record_id: str) -> bool:
    """Whether a record's heading (or a criterion's bullet) exists at a revision."""
    spec = [path] if path else []
    if kind == "criterion":
        # Only a contract file defines a criterion; evidence elsewhere may quote its ID.
        base = f"{path}/" if path else ""
        contracts = [f"{base}ACCEPTANCE.md", f":(glob){base}{layout.ARCHIVE_DIR}/*/ACCEPTANCE.md"]
        return repo.ok("grep", "-q", "-F", f"`{record_id}`", revision, "--", *contracts)
    return repo.ok("grep", "-q", "-E", _heading_pattern(record_id), revision, "--", *spec)


def interval(repo: Repository, before: str, after: str, paths: list[str]) -> dict[str, object]:
    """The first-parent commits after `before` up to `after`, and those touching the chronicle."""
    every = repo.git("rev-list", "--first-parent", f"{before}..{after}").split()
    spec = [p for p in paths if p] if all(paths) else []
    touching = repo.git("rev-list", "--first-parent", "--parents", f"{before}..{after}", "--", *spec).splitlines()
    commits = [line.split()[0] for line in touching]
    merge = any(len(line.split()) > 2 for line in touching)
    if len(commits) == 1:
        confidence = "ONE_COMMIT_MERGE" if merge else "ONE_COMMIT"
    else:
        confidence = "SEVERAL_COMMITS" if commits else "NO_CHRONICLE_COMMIT"
    return {"after": before, "atOrBefore": after, "commits": len(every),
            "chronicleCommits": commits, "confidence": confidence,
            "firstParent": before in repo.git("rev-list", "--first-parent", after).split()}


def _gaps(repo: Repository, commits: list[str]) -> list[dict[str, str]]:
    """Commits in the interval whose chronicle is absent or in a layout no compiler reads."""
    gaps = []
    for commit in commits:
        found = detect(repo, commit)
        if found is None:
            gaps.append({"revision": commit, "reason": "no chronicle"})
        elif _unsupported_layout(repo, commit, *found):
            gaps.append({"revision": commit, "reason": _unsupported_layout(repo, commit, *found)})
    return gaps


_ID_CHARS = "A-Za-z0-9_-"


def _names(text: str | None, record_id: str) -> bool:
    return bool(text) and re.search(f"(?<![{_ID_CHARS}]){re.escape(record_id)}(?![{_ID_CHARS}])", text) is not None


def linked_evidence(before: dict, after: dict, kind: str, record_id: str,
                    record: dict | None) -> list[dict[str, object]]:
    """ADRs, responses, journal entries and ACRs that name a record by its ID.

    Matching is by ID only; a record is never attached because of when it was
    written. `inInterval` says whether it appeared between the two revisions.
    """
    def collect(found: dict) -> dict[tuple[str, str], dict]:
        state = found["state"]
        out = {}
        governing = set((record or {}).get("decisions") or []) if kind == "task" else set()
        for decision in state.get("decisions") or []:
            how = ("affects" if record_id in (decision.get("affects") or [])
                   else "governs" if decision["id"] in governing
                   else "supersedes" if record_id in (decision.get("supersedes") or []) else None)
            if how and decision["id"] != record_id:
                out[("decision", decision["id"])] = {"matchedBy": how, "source": decision.get("source")}
        for response in state.get("responses") or []:
            how = "target" if response.get("target") == record_id else "text" if _names(response.get("text"), record_id) else None
            if how:
                out[("response", response["id"])] = {"matchedBy": how, "source": response.get("source")}
        for entry in state.get("journal") or []:
            if _names(entry.get("title"), record_id) or _names(entry.get("text"), record_id):
                out[("journal", entry["id"])] = {"matchedBy": "text", "source": entry.get("source")}
        contract = (record or {}).get("ac") if kind == "task" else None
        for request in found.get("changeRequests") or []:
            criterion = request.get("criterion") or ""
            if criterion == record_id or (contract and criterion.startswith(f"{contract}-")):
                out[("acr", request["id"])] = {"matchedBy": "criterion", "source": request.get("source")}
        return out

    earlier, later = collect(before), collect(after)
    linked = []
    for (link_kind, link_id), found in {**earlier, **later}.items():
        linked.append({"kind": link_kind, "id": link_id, "matchedBy": found["matchedBy"],
                       "source": found["source"], "inInterval": (link_kind, link_id) not in earlier})
    return sorted(linked, key=lambda item: (item["kind"], item["id"]))


def diff(repo: Repository, first: str, second: str, *, entity: str | None = None,
         evidence: bool = True) -> dict[str, object]:
    """Observed state transitions between two revisions of one history."""
    before_rev, after_rev = repo.resolve(first), repo.resolve(second)
    result: dict[str, object] = {"kind": OBSERVED, "status": None, "repository": repo.identity,
                                 "from": before_rev, "to": after_rev, "reason": None,
                                 "interval": None, "transitions": [], "limits": []}
    if not repo.ok("merge-base", "--is-ancestor", before_rev, after_rev):
        result["status"] = NOT_ANCESTRAL
        reversed_order = repo.ok("merge-base", "--is-ancestor", after_rev, before_rev)
        result["reason"] = (f"{second} is an ancestor of {first}: give the earlier revision first"
                            if reversed_order else
                            f"{first} is not an ancestor of {second}; two revisions on different lines "
                            "of history are not a history, and nothing between them was observed")
        return result
    before, after = state_at_commit(repo, before_rev), state_at_commit(repo, after_rev)
    result["fromState"] = {k: before[k] for k in ("status", "chronicle", "layout", "reason")}
    result["toState"] = {k: after[k] for k in ("status", "chronicle", "layout", "reason")}
    paths = sorted({p for p, _ in CANDIDATES if p} if not (before["chronicle"] and after["chronicle"])
                   else {before["chronicle"], after["chronicle"]})
    span = interval(repo, before_rev, after_rev, [] if "." in paths else paths)
    result["interval"] = span
    if before["status"] != STATE or after["status"] != STATE:
        result["status"] = INSUFFICIENT
        side = "the earlier" if before["status"] != STATE else "the later"
        result["reason"] = f"{side} revision is UNSUPPORTED: " + str((before if before["status"] != STATE else after)["reason"])
        return result
    inside = [c for c in span["chronicleCommits"] if c != after_rev]
    gaps = _gaps(repo, inside)
    if gaps:
        result["status"] = INSUFFICIENT
        result["gaps"] = gaps
        result["reason"] = f"the interval crosses {len(gaps)} revision(s) whose chronicle cannot be read"
        return result
    result["transitions"] = compare(
        before, after, span, entity=entity, evidence=evidence,
        removed_at=lambda kind, record_id: _absent_between(repo, before_rev, after_rev, inside, kind, record_id))
    result["status"] = OBSERVED
    result["limits"] = _limits(span)
    return result


def compare(before: dict, after: dict, span: dict, *, entity: str | None = None, evidence: bool = True,
            removed_at=lambda kind, record_id: None) -> list[dict[str, object]]:
    """Transitions between two compiled states, by stable ID.

    `removed_at(kind, id)` names a commit inside the interval where a record
    present at both ends was absent; such an ID is REUSED, not CHANGED.
    """
    old, new = records(before), records(after)
    keys = sorted(set(old) | set(new))
    if entity:
        keys = [k for k in keys if k[1] == entity]
    shared = {"interval": {k: span[k] for k in ("after", "atOrBefore", "commits", "confidence")}}
    transitions = []
    for key in keys:
        kind, record_id = key
        a, b = old.get(key), new.get(key)
        pinned_from = {"revision": span["after"], "chronicle": before["chronicle"], "source": _where(a) if a else None}
        pinned_to = {"revision": span["atOrBefore"], "chronicle": after["chronicle"], "source": _where(b) if b else None}
        base = {"entity": kind, "id": record_id, "from": pinned_from, "to": pinned_to, **shared}
        title = _TITLE[kind]
        found: list[dict] = []
        if a is None:
            found.append({**base, "transition": "ADDED", "before": None, "after": b.get(title)})
        elif b is None:
            found.append({**base, "transition": "REMOVED", "before": a.get(title), "after": None})
        else:
            if (a.get("source") or {}).get("file") != (b.get("source") or {}).get("file"):
                found.append({**base, "transition": "RELOCATED" if _archived(b) or _archived(a) else "MOVED",
                              "field": "source.file", "before": a["source"]["file"], "after": b["source"]["file"]})
            differs = any(a.get(field) != b.get(field) for field in FIELDS[kind])
            reused = removed_at(kind, record_id) if differs else None
            if reused:
                found.append({**base, "transition": "REUSED", "field": title, "before": a.get(title),
                              "after": b.get(title), "removedAt": reused,
                              "note": "the ID was absent inside the interval and returned; read the two as separate records, not one that changed"})
            else:
                for field in FIELDS[kind]:
                    if a.get(field) != b.get(field):
                        found.append({**base, "transition": "CHANGED", "field": field,
                                      "before": a.get(field), "after": b.get(field)})
        if found and evidence:
            linked = linked_evidence(before, after, kind, record_id, b or a)
            for item in found:
                item["evidence"] = linked
        transitions += found
    return transitions


def _absent_between(repo: Repository, before: str, after: str, inside: list[str],
                    kind: str, record_id: str) -> str | None:
    """The first chronicle commit inside the interval where the record is gone.

    A record can only disappear in a commit that removes its heading (or its
    criterion bullet), so only those commits are opened.
    """
    if not inside:
        return None
    pattern = (["-G", "`" + re.escape(record_id) + "`"] if kind == "criterion"
               else ["-G", _heading_pattern(record_id)])
    touched = set(repo.git("log", "--first-parent", "--format=%H", *pattern, f"{before}..{after}").split())
    for commit in reversed(inside):  # rev-list lists newest first
        if commit not in touched:
            continue
        found = detect(repo, commit)
        if found is not None and not _present(repo, commit, found[0], kind, record_id):
            return commit
    return None


def _limits(span: dict) -> list[str]:
    limits = ["Transitions are differences between two compiled states, not recorded events; "
              "each happened after the first revision and at or before the second."]
    if span["confidence"] == "SEVERAL_COMMITS":
        limits.append(f"{len(span['chronicleCommits'])} commits changed the chronicle in this interval; "
                      "a field that changed more than once shows only its two ends.")
    if span["confidence"] == "ONE_COMMIT_MERGE":
        limits.append("The one commit is a merge, so its changes may span the merged branch's history.")
    if not span["firstParent"]:
        limits.append("The earlier revision is an ancestor but not on the later one's first-parent line.")
    limits.append("Git history can be rewritten; these revisions are what the repository holds now.")
    return limits


def lineage(found: dict[str, object], decision_id: str) -> dict[str, object]:
    """The `Supersedes:` chain of one ADR, in both directions, each with its source."""
    decisions = {d["id"]: d for d in found["state"].get("decisions") or []}
    if decision_id not in decisions:
        return {"id": decision_id, "status": "UNKNOWN", "supersedes": [], "supersededBy": []}

    def entry(d: dict, via: str) -> dict:
        return {"id": d["id"], "title": d.get("title"), "status": d.get("status"),
                "via": via, "source": d.get("source")}

    def walk(start: str, step) -> list[dict]:
        seen, order, queue = {start}, [], [start]
        while queue:
            current = queue.pop(0)
            for nxt in step(current):
                if nxt not in seen and nxt in decisions:
                    seen.add(nxt)
                    order.append(entry(decisions[nxt], current))
                    queue.append(nxt)
        return order

    older = walk(decision_id, lambda d: decisions[d].get("supersedes") or [])
    newer = walk(decision_id, lambda d: sorted(x for x, v in decisions.items() if d in (v.get("supersedes") or [])))
    return {"id": decision_id, "status": decisions[decision_id].get("status"),
            "source": decisions[decision_id].get("source"), "supersedes": older, "supersededBy": newer}


__all__ = ["CANDIDATES", "HistoryError", "Repository", "compare", "detect", "diff", "interval",
           "lineage", "linked_evidence", "records", "state_at_commit"]
