"""Where the Notion token comes from, and whether it works (T-NOTION-CRED-01, ADR-087).

One Notion Internal Integration ("Developer connection") belongs to a workspace,
and its one token serves every project linked to that workspace. The token is
never stored in the chronicle, compiled views, logs or output; this module
reports only where it was found.

Resolution, first found wins:

1. the ``NOTION_TOKEN`` environment variable;
2. ``.praukron/notion.env`` in the project;
3. ``.env.local``, then ``.env``, at the project root, where many projects
   already keep local secrets (doctor warns when Git would not ignore them);
4. ``~/.praukron/notion.env`` for the user, shared by every project.

An empty value is absent, so the blank file the installer leaves never hides
a token set further down the list.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import layout

ENV_VAR = "NOTION_TOKEN"
ENV_FILE = "notion.env"
ROOT_ENV_FILES = (".env.local", ".env")
API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
TIMEOUT = 20

TEMPLATE = """\
# Notion connection for Praukron (ADR-087). This file is ignored by Git:
# never commit it, and never paste the token into a chat or a record.
#
# Where the key comes from
#   Notion > Settings > Connections > Add connection > Developer connection.
#   Type: Internal. Capabilities: Read, Update and Insert content, Read
#   comments, and Read user information including email addresses.
#   Copy its "Internal Integration Secret" (it starts with ntn_).
#
# One key per Notion workspace
#   The same key serves every project linked to that workspace. Put it in
#   ~/.praukron/notion.env once and leave this file blank, or set it here to
#   use another workspace for this project only. NOTION_TOKEN in the
#   environment overrides both.
#
# Then share the page that holds the PPMP template with the connection (page
# menu > Connections), duplicate the template for this project, and run:
#   praukron notion doctor
#   praukron notion setup <url of the copy>
NOTION_TOKEN=
"""


@dataclass(frozen=True)
class Token:
    value: str | None
    source: str | None  # "environment", the file path, or None when absent

    def __repr__(self) -> str:  # never let a stray print reveal the value
        return f"Token(source={self.source!r}, present={self.value is not None})"


def project_file(root: Path) -> Path:
    return root / layout.HOME_DIR / ENV_FILE


def user_file() -> Path:
    return Path.home() / layout.HOME_DIR / ENV_FILE


def candidates(root: Path) -> list[Path]:
    return [project_file(root), *(root / name for name in ROOT_ENV_FILES), user_file()]


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; # comments, blank lines, `export ` and quotes allowed."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def resolve(root: Path, environ: dict[str, str] | None = None) -> Token:
    environ = os.environ if environ is None else environ
    value = (environ.get(ENV_VAR) or "").strip()
    if value:
        return Token(value, "environment")
    for path in candidates(root):
        value = read_env_file(path).get(ENV_VAR, "").strip()
        if value:
            return Token(value, str(path))
    return Token(None, None)


def write_template(path: Path) -> bool:
    """Write the blank template unless a file is already there. True if written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(TEMPLATE)
    return True


def redact(text: str, token: Token) -> str:
    return text.replace(token.value, "***") if token.value else text


def check(token: Token) -> dict[str, object]:
    """Ask Notion who the token is. Never raises; never returns the token."""
    if token.value is None:
        return {"state": "MISSING", "source": None}
    request = urllib.request.Request(
        f"{API}/users/me",
        headers={
            "Authorization": f"Bearer {token.value}",
            "Notion-Version": NOTION_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        state = "INVALID" if error.code in (401, 403) else "UNREACHABLE"
        return {"state": state, "source": token.source, "detail": f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"state": "UNREACHABLE", "source": token.source,
                "detail": redact(type(error).__name__ + ": " + str(error), token)}
    bot = body.get("bot") or {}
    return {
        "state": "VALID",
        "source": token.source,
        "integration": body.get("name"),
        "workspace": bot.get("workspace_name"),
    }


NEXT_STEP = {
    "MISSING": "Put the key in {project} (this project) or {user} (every project), "
               "or set NOTION_TOKEN. The file explains where the key comes from.",
    "INVALID": "Notion refused the key from {source}. Create a new secret for the "
               "connection, or check that it was copied whole.",
    "UNREACHABLE": "Could not reach api.notion.com. Check the network, then run "
                   "`praukron notion doctor` again.",
    "VALID": "Share the page above the PPMP template with this connection, duplicate the "
             "template for this project, then run `praukron notion setup <copy-url>`.",
}


def tracked_safely(root: Path, path: Path) -> bool:
    """False only when the key file is inside a Git work tree that would commit it."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return True
    try:
        inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, timeout=10)
        if inside.returncode != 0:
            return True
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(path)],
                                 capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return ignored.returncode == 0


def doctor(root: Path, environ: dict[str, str] | None = None,
           probe=None) -> tuple[int, dict[str, object], str]:
    token = resolve(root, environ)
    report = (probe or check)(token)
    lines = ["Notion doctor"]
    lines.append(f"  key:          {'found in ' + str(report['source']) if report['source'] else 'not set'}")
    lines.append(f"  connection:   {report['state']}"
                 + (f" · {report['integration']}" if report.get("integration") else "")
                 + (f" in {report['workspace']}" if report.get("workspace") else ""))
    if report.get("detail"):
        lines.append(f"  detail:       {report['detail']}")
    if token.source and not tracked_safely(root, Path(token.source)):
        report["warning"] = "not-ignored"
        lines.append(f"  WARNING:      Git does not ignore {token.source}; add it to .gitignore "
                     "before committing, or the key will be published.")
    lines.append("")
    lines.append("  " + NEXT_STEP[str(report["state"])].format(
        project=project_file(root), user=user_file(), source=report.get("source")))
    text = redact("\n".join(lines), token)
    return (0 if report["state"] == "VALID" else 1), report, text
