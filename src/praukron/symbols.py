"""Attribute a capture's changed lines to Python symbols (SPEC §2.9, ADR-092).

For each changed Python file, the old text (at the capture's base) and the new
text (at its head, or in the working tree) are parsed with the standard
library's `ast` and compared line by line. A function, async function, class
or method whose source range intersects a changed line is a changed symbol:
present only in the new text it `creates`, only in the old text it `deletes`,
in both it `modifies`. Its name is the qualified name inside the file
(`Class.method`, nested names joined with `.`), taken from the parsed source,
so a recorded name always exists at the revision it is recorded against.

Source text is untrusted data: it is parsed, never imported, compiled or run.
Nothing here writes; `scope.plan` turns the result into records. Any failure
leaves the file without symbols and says why, so capture never blocks a write.
"""

from __future__ import annotations

import ast
import bisect
import re
from dataclasses import dataclass
from pathlib import Path

# A file larger than this on either side it is needed is not read or parsed.
MAX_FILE_BYTES = 512 * 1024
# A capture with more changed Python files than this extracts no symbols.
MAX_FILES = 200
SUFFIXES = (".py", ".pyi")
NO_EXTRACTOR = "no deterministic symbol extractor for this file type"
# The nodes that can hold a statement, and so a definition.
_BLOCKS = (ast.stmt, ast.excepthandler, *((ast.match_case,) if hasattr(ast, "match_case") else ()))


@dataclass(frozen=True)
class Symbol:
    """One symbol in one text: qualified name, kind and 1-based line range."""

    name: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class Changed:
    """One changed symbol of a file: its relation and the range on its side."""

    name: str
    kind: str
    relation: str
    start: int
    end: int
    # True when the name was read from the base text (a deleted symbol).
    from_base: bool = False


class Unresolved(Exception):
    """Why a file's symbols could not be resolved."""


def is_python(path: str) -> bool:
    return path.endswith(SUFFIXES)


def extract(source: bytes) -> dict[str, Symbol]:
    """Every function, class and method in `source`, by qualified name.

    A name defined twice in one scope (a property setter, a definition in each
    branch of an `if`) is one symbol whose range spans every definition, so a
    change to either is a change to it. Raises Unresolved when the text does
    not parse.
    """
    try:
        tree = ast.parse(source, type_comments=False)
    except SyntaxError as error:
        raise Unresolved(f"does not parse: {error.msg} at line {error.lineno}") from error
    except (ValueError, RecursionError, MemoryError) as error:
        raise Unresolved(f"does not parse: {type(error).__name__}") from error
    found: dict[str, Symbol] = {}

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kind = "class" if isinstance(child, ast.ClassDef) else ("method" if in_class else "function")
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                end = child.end_lineno or child.lineno
                known = found.get(name)
                if known is not None:
                    start, end = min(start, known.start), max(end, known.end)
                    kind = known.kind
                found[name] = Symbol(name, kind, start, end)
                visit(child, name + ".", isinstance(child, ast.ClassDef))
            elif isinstance(child, _BLOCKS):
                # Definitions inside `if`, `try`, `with` and the like keep the enclosing scope.
                # A definition is always a statement, so expressions are never walked.
                visit(child, prefix, in_class)

    try:
        visit(tree, "", False)
    except RecursionError as error:
        raise Unresolved("does not parse: nesting too deep") from error
    return found


Ranges = list  # sorted, non-overlapping (start, end) line ranges, 1-based and inclusive

_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11, ord("f"): 12, ord("r"): 13,
            ord('"'): 34, ord("\\"): 92}


def _unquote(raw: bytes) -> bytes:
    """A path as Git prints it in a patch header, C-style quoted or not."""
    if not (raw.startswith(b'"') and raw.endswith(b'"') and len(raw) >= 2):
        return raw
    body, out, i = raw[1:-1], bytearray(), 0
    while i < len(body):
        byte = body[i]
        if byte == 92 and i + 1 < len(body):
            nxt = body[i + 1]
            if 48 <= nxt <= 55:
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(byte)
        i += 1
    return bytes(out)


def hunks(patch: bytes) -> dict[str, tuple[Ranges, Ranges]]:
    """The changed line ranges of each file in a `git diff -U0` patch, by new path.

    Each hunk header gives the old side's range and the new side's; a side
    with a count of zero changed no line there. Hunk bodies are skipped by
    their counts, so a changed line that looks like a header is never read as
    one. Paths are relative to the diff's root, `b/` prefix removed.
    """
    found: dict[str, tuple[Ranges, Ranges]] = {}
    current: tuple[Ranges, Ranges] | None = None
    remaining = 0
    for line in patch.split(b"\n"):
        if remaining:
            if not line.startswith(b"\\"):
                remaining -= 1
            continue
        if line.startswith(b"+++ "):
            target = _unquote(line[4:])
            current = None
            if target.startswith(b"b/"):
                current = found.setdefault(target[2:].decode("utf-8", "replace"), ([], []))
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            a, b, c, d = (int(g) if g is not None else 1 for g in match.groups())
            if b:
                current[0].append((a, a + b - 1))
            if d:
                current[1].append((c, c + d - 1))
            remaining = b + d
    return found


def _touches(symbol: Symbol, ranges: Ranges) -> bool:
    """Whether a range overlaps the symbol's lines, by bisection.

    Git's ranges for one side are sorted and do not overlap, so their ends
    rise with their starts: the last range starting at or before the
    symbol's end is the only one that can reach back into it.
    """
    i = bisect.bisect_right(ranges, (symbol.end, float("inf")))
    return i > 0 and ranges[i - 1][1] >= symbol.start


def changed(old: bytes | None, new: bytes | None, old_ranges: Ranges = (), new_ranges: Ranges = ()) -> list[Changed]:
    """The symbols a change from `old` to `new` touches; None means the side is absent.

    `old_ranges` and `new_ranges` are the changed lines on each side, as Git
    reports them. A symbol only in the new text is created and one only in the
    old text deleted whatever the ranges; one in both is modified when a
    changed line on either side falls in its range. Raises Unresolved when a
    side that is needed does not parse.
    """
    try:
        before = extract(old) if old is not None else {}
    except Unresolved as error:
        raise Unresolved(f"{error} at the base revision") from error
    try:
        after = extract(new) if new is not None else {}
    except Unresolved as error:
        raise Unresolved(f"{error} at the head") from error
    old_lines, new_lines = sorted(old_ranges), sorted(new_ranges)
    result: list[Changed] = []
    for name, symbol in after.items():
        if name not in before:
            result.append(Changed(name, symbol.kind, "creates", symbol.start, symbol.end))
        elif _touches(symbol, new_lines) or _touches(before[name], old_lines):
            result.append(Changed(name, symbol.kind, "modifies", symbol.start, symbol.end))
    for name, symbol in before.items():
        if name not in after:
            result.append(Changed(name, symbol.kind, "deletes", symbol.start, symbol.end, from_base=True))
    return sorted(result, key=lambda c: (c.from_base, c.start, c.name))


def read_blobs(git, root: Path, specs: list[str]) -> dict[str, bytes | int | None]:
    """Each `rev:path` blob's bytes, its size when over the limit, or None when absent.

    Two Git calls whatever the number of files: sizes first, so a file over
    the limit is never read, then the contents of the rest.
    """
    if not specs:
        return {}
    request = b"".join(s.encode("utf-8") + b"\n" for s in specs)
    out: dict[str, bytes | int | None] = {}
    checked = git(root, "cat-file", "--batch-check=%(objecttype) %(objectsize)", stdin=request).split(b"\n")
    wanted: list[str] = []
    for spec, line in zip(specs, checked):
        parts = line.split()
        if len(parts) != 2 or parts[0] != b"blob":
            out[spec] = None
            continue
        size = int(parts[1])
        if size > MAX_FILE_BYTES:
            out[spec] = size
        else:
            wanted.append(spec)
    if wanted:
        data = git(root, "cat-file", "--batch", stdin=b"".join(s.encode("utf-8") + b"\n" for s in wanted))
        at = 0
        for spec in wanted:
            end = data.index(b"\n", at)
            header = data[at:end].split()
            at = end + 1
            if len(header) != 3:
                out[spec] = None
                continue
            size = int(header[2])
            out[spec] = data[at:at + size]
            at += size + 1
    return out


def read_file(path: Path) -> bytes | int | None:
    """A working-tree file's bytes, its size when over the limit, or None when absent."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return size
        return path.read_bytes()
    except OSError:
        return None
