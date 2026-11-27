"""Agent authority: what agents may do on the owner's authority.

One setting, in one place. Each capability is listed in `CAPABILITIES`. A
capability of kind `grant` is switched on by a grant in AUTHORITY.md and off by
its end or its revocation; one of kind `policy` is read from where it already
lives (the assumption policy, an ACCEPTED ADR with `Policy: assumptions`) and
is only listed here. Adding a capability is a table entry: the grant and
revoke writes, validation, the list and the packets read the table.

A grant and a revocation are recorded only by the owner in person: at an
interactive terminal or from `praukron dashboard --serve`, under the active
`gh` login, which must be named in an ACCEPTED ADR's `Owners:`. The
interactive check is the one VERIFY uses; it does not stop a program that
imitates a terminal.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

from .layout import AUTHORITY_DIR
from .model import Grant, Project, Task

FILE = "AUTHORITY.md"
HEADER = (
    "# Agent authority\n\n"
    "What the owner lets agents do on the owner's authority. Recorded only by the\n"
    "owner in person, with `praukron authority grant` and `praukron authority\n"
    "revoke` at a terminal or from `praukron dashboard --serve`; an agent never\n"
    "writes, edits or widens a grant. A revocation is added to its grant, which\n"
    "otherwise stays as written.\n"
)
# How a grant or a revocation may arrive: the owner in person, nothing else.
IN_PERSON = ("cli", "dashboard")
PROJECT_SCOPE = "project"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Capability:
    """One thing an agent can be given. `kind` is `grant` or `policy`."""

    name: str
    title: str
    kind: str
    act: str  # what an agent does under it
    switch: str  # how the owner switches it on or changes it


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "verify", "Verify Authority: owner tasks", "grant",
        "record VERIFY of an Authority: owner task it did not build, as DELEGATE_VERIFIED",
        "praukron authority grant verify --scope <project|T-…,M-…,P…> --delegate \"<agent>\" [--until YYYY-MM-DD]"),
    Capability(
        "assumptions", "Assume without asking", "policy",
        "record an assumption in ASSUMPTIONS.md unless its group is reserved for the owner",
        "an ACCEPTED ADR with Policy: assumptions, Reserved: and Notify: (docs/SPEC.md)"),
)


def capability(name: str | None) -> Capability | None:
    return next((c for c in CAPABILITIES if c.name == (name or "").lower()), None)


def granted_capabilities() -> list[str]:
    return [c.name for c in CAPABILITIES if c.kind == "grant"]


class AuthorityError(Exception):
    """A grant or revocation was refused, or failed and was undone."""


def today() -> str:
    return _dt.date.today().isoformat()


def same_name(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().lower() == b.strip().lower()


def built(task: Task, delegate: str) -> bool:
    """Whether the delegate built the task: it is the task's Owner or named in it.

    Names are declared by agents; the record makes a false name visible and
    does not prevent one."""
    name = delegate.strip().lower()
    owner = (task.owner or "").strip().lower()
    return bool(name) and bool(owner) and (owner == name or name in owner)


def grants_for(project: Project, name: str, task: Task, delegate: str | None = None) -> list[Grant]:
    """Grants of one capability that cover a task, for a delegate if named."""
    return [g for g in project.grants
            if g.capability == name and g.covers(task)
            and (delegate is None or same_name(g.delegate, delegate))]


def in_force(project: Project, name: str, task: Task, delegate: str, day: str) -> Grant | None:
    """The newest grant in force on `day` for this capability, task and delegate.

    A record validate would reject (a granter outside Owners, a Via other than
    in person) never counts, so a hand-written grant cannot switch a capability on."""
    found = [g for g in grants_for(project, name, task, delegate)
             if g.in_force_on(day) and not g.revoked and project.is_owner(g.granted_by)
             and g.via in IN_PERSON]
    return found[-1] if found else None


def switch_on(name: str, task_id: str, delegate: str) -> str:
    return (f"the owner switches it on in person: `praukron authority grant {name} --scope {task_id} "
            f"--delegate \"{delegate}\"` at a terminal, or from `praukron dashboard --serve`")


def check_delegated_verify(project: Project, task: Task, delegate: str, day: str) -> Grant:
    """The grant a delegated VERIFY rests on, or AuthorityError naming why not."""
    if not delegate.strip():
        raise AuthorityError("--delegate names no agent")
    grant = in_force(project, "verify", task, delegate, day)
    if grant is None:
        held = grants_for(project, "verify", task, delegate)
        detail = ""
        if held:
            last = held[-1]
            detail = f" ({last.id} is {last.state(day)})"
        raise AuthorityError(
            f"capability verify is off for {task.id} and {delegate.strip()}{detail}: {switch_on('verify', task.id, delegate.strip())}. "
            "Nothing was recorded.")
    if built(task, delegate):
        raise AuthorityError(
            f"{delegate.strip()} built {task.id} (Owner: {task.owner}): the one who verifies is never the one "
            "who built. Nothing was recorded.")
    return grant


def capabilities_for(project: Project, task: Task, day: str | None = None) -> list[dict[str, object]]:
    """What is on for one task, for its packet: each capability and its grants in force."""
    day = day or today()
    out: list[dict[str, object]] = []
    for c in CAPABILITIES:
        if c.kind == "policy":
            policy = project.policy
            out.append({"capability": c.name, "kind": c.kind, "state": "policy",
                        "reserved": policy.reserved, "notify": policy.notify,
                        "decision": policy.decision, "default": policy.decision is None})
            continue
        held = [g for g in project.grants if g.capability == c.name and g.covers(task)
                and g.state(day) == "in force" and project.is_owner(g.granted_by) and g.via in IN_PERSON]
        out.append({
            "capability": c.name, "kind": c.kind, "state": "on" if held else "off",
            "grants": [{"id": g.id, "delegate": g.delegate, "grantedBy": g.granted_by,
                        "until": g.until, "wholeProject": g.whole_project,
                        "notFor": "the task's builder", "authority": f"{g.source.file}#{g.id}"}
                       for g in held],
            **({} if held else {"switchOn": c.switch}),
        })
    return out


def verifications(project: Project, task_id: str) -> list[dict[str, object]]:
    """Every VERIFY of a task, each saying whether the owner or a delegate made it."""
    out = []
    for r in project.responses_to(task_id):
        if r.action != "VERIFY":
            continue
        if r.delegated:
            out.append({"response": r.id, "kind": "delegate", "delegate": r.delegate,
                        "onAuthorityOf": r.on_authority_of, "grant": r.grant, "date": r.date,
                        "via": r.via, "authority": f"{r.source.file}#{r.id}",
                        "text": f"verified by delegate {r.delegate} on the authority of "
                                f"{r.on_authority_of}, under {r.grant}; not the owner in person"})
        else:
            out.append({"response": r.id, "kind": "owner" if project.is_owner(r.by) else "not-an-owner",
                        "by": r.by, "date": r.date, "via": r.via, "authority": f"{r.source.file}#{r.id}",
                        "text": f"verified by {r.by} in person"})
    return out


def view(project: Project, day: str | None = None) -> dict[str, object]:
    """The one list: every capability, its state, who set it and when.

    Without `day` nothing depends on the date, so compiled output stays
    reproducible; `praukron authority` and the dashboard pass today."""
    capabilities = []
    for c in CAPABILITIES:
        entry: dict[str, object] = {"name": c.name, "title": c.title, "kind": c.kind, "act": c.act,
                                    "switch": c.switch}
        if c.kind == "policy":
            policy = project.policy
            decision = next((d for d in project.decisions if d.id == policy.decision), None)
            entry.update({"state": "policy", "reserved": policy.reserved, "notify": policy.notify,
                          "decision": policy.decision, "default": policy.decision is None,
                          "setBy": owners_of(project, decision), "setOn": decision_date(project, decision)})
        else:
            grants = [g for g in project.grants if g.capability == c.name]
            entry["grants"] = [g.as_json(day) for g in grants]
            if day is not None:
                entry["state"] = "on" if any(g.state(day) == "in force" for g in grants) else "off"
        capabilities.append(entry)
    unknown = [g.as_json(day) for g in project.grants if capability(g.capability) is None
               or capability(g.capability).kind != "grant"]
    data: dict[str, object] = {"file": f"{AUTHORITY_DIR}/{FILE}", "capabilities": capabilities,
                               "unknown": unknown}
    if day is not None:
        data["date"] = day
    return data


def owners_of(project: Project, decision) -> list[str]:
    return list(project.owners) if decision is not None else []


def decision_date(project: Project, decision) -> str | None:
    """The `Date:` of an ADR file, read verbatim; None when absent."""
    if decision is None:
        return None
    path = Path(project.root) / AUTHORITY_DIR / decision.source.file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^- Date: *(\S+)", text)
    return match.group(1) if match else None


def render(data: dict[str, object]) -> str:
    """`praukron authority`: the list for a person at a terminal."""
    day = data.get("date")
    lines = [f"Agent authority · {data['file']} · as of {day}", ""]
    for c in data["capabilities"]:
        if c["kind"] == "policy":
            source = c["decision"] or "the default policy (no ADR sets one)"
            reserved = ", ".join(c["reserved"]) or "none"
            set_by = ", ".join(c["setBy"]) or "—"
            lines += [f"{c['name']}  POLICY  {c['title']}",
                      f"  reserved for the owner: {reserved}; notify: {c['notify']}",
                      f"  set by {source}" + (f" on {c['setOn']}" if c.get("setOn") else "")
                      + (f" (owners named: {set_by})" if c["decision"] else ""),
                      f"  changed by: {c['switch']}", ""]
            continue
        lines.append(f"{c['name']}  {str(c.get('state', '')).upper()}  {c['title']}")
        lines.append(f"  lets an agent {c['act']}")
        groups = {"in force": [], "pending": [], "ended": [], "revoked": []}
        for g in c["grants"]:
            groups.setdefault(g["state"], []).append(g)
        for state in ("in force", "pending", "ended", "revoked"):
            for g in groups[state]:
                lines.append("  " + grant_line(g))
        if not c["grants"]:
            lines.append("  no grant recorded")
        lines += [f"  switched on by: {c['switch']}", ""]
    for g in data.get("unknown") or []:
        lines.append(f"unknown capability {g['capability']}: {g['id']} (validate reports it)")
    return "\n".join(lines).rstrip() + "\n"


def grant_line(g: dict[str, object]) -> str:
    scope = "WHOLE PROJECT" if g["wholeProject"] else ", ".join(g["scope"]) or "NO SCOPE"
    end = "NO END (until revoked)" if g["noEnd"] else f"until {g['until']}"
    text = (f"{g['id']} {str(g.get('state', '')).upper()} · {g['delegate']} · scope {scope} · {end} · "
            f"granted by {g['grantedBy']} on {g['date']} via {g['via']}")
    if g["revoked"]:
        text += f" · revoked by {g['revokedBy']} on {g['revoked']} via {g['revokedVia']}"
    return text


# Dashboard ---------------------------------------------------------------


def _v(value: object) -> str:
    """Record data: escaped and marked so the interface catalogue never translates it."""
    import html

    from . import i18n

    return html.escape(i18n.verbatim("—" if value in (None, "") else value), quote=True)


def section(project: Project, interactive: bool = False, day: str | None = None) -> str:
    """The dashboard's agent authority page: the same list as `praukron authority`,
    and, when served to the owner, the same two writes."""
    data = view(project, day or today())
    rows = []
    for c in data["capabilities"]:
        if c["kind"] == "policy":
            reserved = ", ".join(c["reserved"]) or "none"
            rows.append(
                f'<tr data-capability="{_v(c["name"])}"><td><code>{_v(c["name"])}</code></td>'
                f'<td><span class="pill">Policy</span></td>'
                f'<td>Reserved for the owner: {_v(reserved)}. Notify: {_v(c["notify"])}.</td>'
                f'<td>{_v(", ".join(c["setBy"]) if c["decision"] else "")}</td>'
                f'<td>{_v(c["setOn"]) if c["decision"] else "Default policy"} '
                f'{_v(c["decision"]) if c["decision"] else ""}</td><td></td></tr>')
            continue
        state = "On" if c["state"] == "on" else "Off"
        rows.append(
            f'<tr data-capability="{_v(c["name"])}"><td><code>{_v(c["name"])}</code></td>'
            f'<td><span class="pill">{state}</span></td>'
            f'<td colspan="4">{"No grant recorded." if not c["grants"] else ""}</td></tr>')
        for g in c["grants"]:
            scope = ('<strong class="stand-out">Whole project</strong>' if g["wholeProject"]
                     else _v(", ".join(g["scope"])))
            end = ('<strong class="stand-out">No end: until revoked</strong>' if g["noEnd"]
                   else f'Until {_v(g["until"])}')
            label = {"in force": "In force", "pending": "Not yet in force", "ended": "Ended",
                     "revoked": "Revoked"}[g["state"]]
            revoked = (f'<br>Revoked by {_v(g["revokedBy"])}, {_v(g["revoked"])}, via {_v(g["revokedVia"])}'
                       if g["revoked"] else "")
            action = (f'<button type="button" class="authority-revoke" data-grant="{_v(g["id"])}">Revoke</button>'
                      if interactive and g["state"] in ("in force", "pending") else "")
            rows.append(
                f'<tr id="grant-{_v(g["id"])}" data-grant-state="{_v(g["state"])}"><td>{_v(g["id"])}</td>'
                f'<td><span class="pill">{label}</span></td>'
                f'<td>Delegate {_v(g["delegate"])} · Scope {scope} · {end}</td>'
                f'<td>{_v(g["grantedBy"])}</td><td>{_v(g["date"])} via {_v(g["via"])}{revoked}</td>'
                f'<td>{action}</td></tr>')
    if interactive:
        options = "".join(f'<option value="{_v(name)}">{_v(name)}</option>' for name in granted_capabilities())
        form = ('<div class="aform" id="authority-form"><h3>Grant a capability</h3>'
                f'<label>Capability <select class="authority-capability">{options}</select></label> '
                '<label>Scope <input class="authority-scope" placeholder="project, or T-…, M-…, P…"></label> '
                '<label>Delegate <input class="authority-delegate" placeholder="the agent it is for"></label> '
                '<label>Until <input class="authority-until" type="date"></label> '
                '<button type="button" class="authority-grant">Grant</button> '
                '<span class="authority-result" role="status"></span>'
                '<p class="note">Recorded under your GitHub login. Leave Until empty for a grant with no end; '
                'write project for the whole project.</p></div>')
    else:
        form = ('<p class="respond-hint">To grant or revoke, run <code>praukron authority grant</code> or '
                '<code>praukron authority revoke</code> yourself at a terminal, or open '
                '<code>praukron dashboard --serve</code>.</p>')
    return ('<section class="box" id="agent-authority"><h2>Agent authority</h2>'
            '<p class="note">What agents may do on your authority, each capability with its state, who set it '
            'and when. Only an owner switches one on or off, in person. A verification under a grant is '
            'DELEGATE_VERIFIED, never HUMAN_VERIFIED.</p>'
            '<table class="authority"><thead><tr><th>Capability</th><th>State</th><th>Details</th>'
            '<th>Set by</th><th>When</th><th></th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table>' + form + '</section>')


# Only the served page carries this: the static file never contains a write.
SERVE_SCRIPT = """
// Agent authority (praukron dashboard --serve): the owner's grant and revoke.
const authorityPanel = document.getElementById('agent-authority');
if (authorityPanel && document.getElementById('authority-form')) {
  const authorityToken = new URLSearchParams(location.search).get('token') || '';
  const authorityOut = authorityPanel.querySelector('.authority-result');
  const authorityPost = async body => {
    try {
      const response = await fetch('/authority', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Praukron-Token': authorityToken },
        body: JSON.stringify(body) });
      const result = await response.json();
      if (!result.ok) throw new Error(result.error);
      authorityOut.textContent = 'Recorded ' + result.ids.join(', ') + '. Reloading…';
      location.reload();
    } catch (error) { authorityOut.textContent = error.message; }
  };
  authorityPanel.querySelector('.authority-grant').addEventListener('click', () => authorityPost({
    op: 'grant', capability: authorityPanel.querySelector('.authority-capability').value,
    scope: authorityPanel.querySelector('.authority-scope').value,
    delegate: authorityPanel.querySelector('.authority-delegate').value,
    until: authorityPanel.querySelector('.authority-until').value }));
  authorityPanel.querySelectorAll('.authority-revoke').forEach(button => button.addEventListener('click',
    () => authorityPost({ op: 'revoke', grant: button.dataset.grant })));
}
"""


# Writes ------------------------------------------------------------------


def _clean_scope(scope: list[str] | str) -> list[str]:
    items = scope.split(",") if isinstance(scope, str) else list(scope)
    return [item.strip() for item in items if item and item.strip()]


def check_grant(project: Project, name: str, scope: list[str], delegate: str, until: str | None,
                day: str) -> None:
    """Refuse a grant the rules do not allow, before anything is written."""
    found = capability(name)
    if found is None or found.kind != "grant":
        raise AuthorityError(f"{name} is not a capability that can be granted; one of: "
                             f"{', '.join(granted_capabilities())}")
    if not scope:
        raise AuthorityError("a grant states its scope: `project` for the whole project, or task, module "
                             "and phase ids; the whole project is never assumed")
    if any(s.lower() == PROJECT_SCOPE for s in scope) and len(scope) > 1:
        raise AuthorityError("scope is either `project` or a list of tasks, modules and phases, not both")
    if not (len(scope) == 1 and scope[0].lower() == PROJECT_SCOPE):
        known = {t.id for t in project.tasks} | {m.id for m in project.modules} | {p.id for p in project.phases}
        unknown = [s for s in scope if s not in known]
        if unknown:
            raise AuthorityError(f"scope names {', '.join(unknown)}, which is not a task, module or phase")
    if not delegate.strip() or "\n" in delegate:
        raise AuthorityError("a grant names its delegate: the agent it is for, on one line")
    if until is not None and (not _DATE.match(until) or until < day):
        raise AuthorityError(f"--until {until} must be a YYYY-MM-DD date not before today ({day})")


def _grant_entry(number: int, name: str, scope: list[str], delegate: str, by: str, day: str,
                 until: str | None, via: str) -> str:
    shown = PROJECT_SCOPE if scope[0].lower() == PROJECT_SCOPE else ", ".join(scope)
    return (f"\n## AG-{number:02d}: {name} for {delegate}\n- Capability: {name}\n- Scope: {shown}\n"
            f"- Delegate: {delegate}\n- Granted by: {by}\n- Date: {day}\n- Until: {until or 'none'}\n"
            f"- Via: {via}\n")


def _in_person(via: str) -> None:
    if via not in IN_PERSON:
        raise AuthorityError(f"a grant or revocation arrives only in person ({', '.join(IN_PERSON)}), "
                             f"never {via!r}; an agent never records one")


def _write(root: Path, change) -> str:
    """Lock, change AUTHORITY.md atomically, validate, undo on a new error, refresh."""
    from . import compile as compiler
    from . import respond

    lock = respond._lock(root)
    try:
        if respond.recover_archive(root):
            raise AuthorityError("an interrupted write was rolled back; try again")
        project = compiler.load(root)
        by = respond.identity()
        if not project.is_owner(by):
            raise AuthorityError(f"{by} is not named in an ACCEPTED ADR's Owners: only an owner grants or "
                                 "revokes agent authority. Nothing was recorded.")
        before = respond._errors(project)
        path = root / AUTHORITY_DIR / FILE
        existed = path.is_file()
        original = path.read_bytes() if existed else None
        text = original.decode("utf-8") if original is not None else HEADER
        if not text.endswith("\n"):
            text += "\n"
        text, result = change(project, text, by)
        try:
            respond._replace(path, text.encode("utf-8"))
        except OSError as failure:
            raise AuthorityError(f"nothing was recorded: {failure}") from failure
        try:
            introduced = respond._errors(compiler.load(root)) - before
        except Exception as failure:  # the new text does not parse
            introduced = {("unreadable", FILE, str(failure))}
        if introduced:
            if existed:
                respond._replace(path, original)
            elif path.exists():
                path.unlink()
            detail = "; ".join(f"{code}: {message}" for code, _, message in sorted(introduced))
            raise AuthorityError(f"nothing was recorded: {detail}")
    except respond.RespondError as error:
        raise AuthorityError(str(error)) from error
    finally:
        lock.release()
    respond.refresh(root)
    return result


def grant(root: Path, name: str, scope: list[str] | str, delegate: str, until: str | None = None,
          via: str = "cli", day: str | None = None) -> str:
    """Append one grant; returns its id. Only the owner, only in person."""
    _in_person(via)
    day = day or today()
    name = (name or "").strip().lower()
    scope = _clean_scope(scope)
    until = (until or "").strip() or None
    if until is not None and until.lower() == "none":
        until = None
    delegate = (delegate or "").strip()

    def change(project: Project, text: str, by: str):
        check_grant(project, name, scope, delegate, until, day)
        number = max([int(g.id.split("-")[1]) for g in project.grants] or [0]) + 1
        return text + _grant_entry(number, name, scope, delegate, by, day, until, via), f"AG-{number:02d}"

    return _write(root, change)


def revoke(root: Path, grant_id: str, via: str = "cli", day: str | None = None) -> str:
    """Add a revocation to a grant, leaving the grant as written. Only in person."""
    _in_person(via)
    day = day or today()
    grant_id = (grant_id or "").strip().upper()

    def change(project: Project, text: str, by: str):
        found = project.grant(grant_id)
        if found is None:
            raise AuthorityError(f"{grant_id} is not a grant in {FILE}")
        if found.revoked:
            raise AuthorityError(f"{grant_id} was already revoked on {found.revoked} by {found.revoked_by}")
        heading = re.search(rf"(?m)^## {re.escape(grant_id)}:.*$", text)
        following = re.compile(r"(?m)^#{1,2} ").search(text, heading.end())
        end = following.start() if following else len(text)
        body = text[heading.end():end].rstrip("\n")
        addition = f"\n- Revoked: {day}\n- Revoked by: {by}\n- Revoked via: {via}\n"
        rest = text[end:]
        return text[:heading.end()] + body + addition + ("\n" + rest if rest else ""), grant_id

    return _write(root, change)


__all__ = ["CAPABILITIES", "Capability", "AuthorityError", "FILE", "IN_PERSON", "built", "capabilities_for",
           "check_delegated_verify", "grant", "render", "revoke", "verifications", "view"]
