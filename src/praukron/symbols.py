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
import difflib
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


def _changed_lines(old: bytes, new: bytes) -> tuple[set[int], set[int]]:
    """Changed 1-based line numbers on the old side and on the new side.

    A pure insertion changes only new lines and a pure deletion only old
    lines, so a symbol is changed when either side's lines fall in its range.
    """
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    old_lines: set[int] = set()
    new_lines: set[int] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            old_lines.update(range(i1 + 1, i2 + 1))
            new_lines.update(range(j1 + 1, j2 + 1))
    return old_lines, new_lines


def _touches(symbol: Symbol, lines: set[int]) -> bool:
    return any(symbol.start <= line <= symbol.end for line in lines)


def changed(old: bytes | None, new: bytes | None) -> list[Changed]:
    """The symbols a change from `old` to `new` touches; None means the side is absent.

    Raises Unresolved when a side that is needed does not parse.
    """
    try:
        before = extract(old) if old is not None else {}
    except Unresolved as error:
        raise Unresolved(f"{error} at the base revision") from error
    try:
        after = extract(new) if new is not None else {}
    except Unresolved as error:
        raise Unresolved(f"{error} at the head") from error
    old_lines, new_lines = _changed_lines(old or b"", new or b"")
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
