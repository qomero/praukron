"""Where Praukron keeps its files.

Everything Praukron owns lives inside one directory, so installing it into a
repository adds one entry to that repository's root rather than five (ADR-024).
The separation between authored authority and compiled output that ADR-014
established is unchanged: it is now two subdirectories instead of two
directories at the root.

Paths a host reads by fixed address — `AGENTS.md`, `CLAUDE.md`,
`.claude/commands/`, `.opencode/commands/`, `.agents/skills/praukron/` — are not
listed here. Praukron does not choose them.
"""

from __future__ import annotations

from pathlib import Path

FALLBACK_VERSION = "0.4.3"

HOME_DIR = ".praukron"
AUTHORITY_DIR = f"{HOME_DIR}/chronicle"
COMPILED_DIR = f"{HOME_DIR}/compiled"
RECOVERY_DIR = f"{HOME_DIR}/recovery"
RUNTIME_DIR = f"{HOME_DIR}/runtime"
COMMANDS_DIR = f"{HOME_DIR}/commands"
ENTRY_POINT = f"{HOME_DIR}/praukron"
# Closed phases' tasks and contracts, inside the chronicle and still authority
# (ADR-067): `<AUTHORITY_DIR>/archive/<phase>/TASKS.md` and `ACCEPTANCE.md`.
ARCHIVE_DIR = "archive"

# Earlier layouts, kept so records written under them can still be found.
# v0.1 held authority in the directory that now holds the installation; v0.2
# held it at the repository root, beside a compiled `.praukron/`.
V01_AUTHORITY_DIR = ".prokron"
V02_AUTHORITY_DIR = "prokron"
V02_COMPILED_DIR = ".prokron"
V02_RUNTIME_DIR = ".prokron-runtime"
V02_COMMANDS_DIR = "commands"
V02_ENTRY_POINT = "bin/prokron"

__all__ = [
    "AUTHORITY_DIR",
    "COMMANDS_DIR",
    "COMPILED_DIR",
    "RECOVERY_DIR",
    "ENTRY_POINT",
    "HOME_DIR",
    "RUNTIME_DIR",
    "V01_AUTHORITY_DIR",
    "V02_AUTHORITY_DIR",
    "V02_COMMANDS_DIR",
    "V02_COMPILED_DIR",
    "V02_ENTRY_POINT",
    "V02_RUNTIME_DIR",
    "version",
]


def version() -> str:
    """The version of the runtime that is executing, never the tracked project's.

    Installed, this module sits in `.praukron/runtime/praukron/`, so VERSION is
    one level up. In a source checkout it is at the repository root. A VERSION
    belonging to the project being tracked is deliberately out of reach.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "VERSION", here.parents[2] / "VERSION"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or FALLBACK_VERSION
    return FALLBACK_VERSION
