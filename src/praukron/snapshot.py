"""One immutable byte inventory, exposed to the canonical parsers without disk rereads."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping


def _case_insensitive(directory: Path, names: set[str]) -> bool:
    for name in sorted(names):
        alternate = name.swapcase()
        if alternate == name or alternate in names:
            continue
        path = directory / name
        try:
            return path.with_name(alternate).samefile(path)
        except FileNotFoundError:
            return False
    return False


@dataclass(frozen=True)
class Snapshot:
    files: Mapping[str, bytes]
    directories: frozenset[str]
    insensitive_directories: frozenset[str] = frozenset()
    aliases: Mapping[tuple[str, str], str] = field(default_factory=dict, repr=False, compare=False)
    authority: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(cls, authority: Path) -> Snapshot:
        """The caller holds the shared write lock throughout capture and planning."""
        authority = authority.resolve()
        files, directories = {}, {""}
        for path in sorted(authority.rglob("*")):
            name = path.relative_to(authority).as_posix()
            if path.is_file():
                files[name] = path.read_bytes()
            elif path.is_dir():
                directories.add(name)
            else:
                raise OSError(f"cannot snapshot chronicle path: {name}")
        children: dict[str, set[str]] = {}
        for name in set(files) | (directories - {""}):
            path = PurePosixPath(name)
            parent = path.parent.as_posix()
            children.setdefault("" if parent == "." else parent, set()).add(path.name)
        insensitive = {parent for parent, names in children.items()
                       if _case_insensitive(authority / parent, names)}
        aliases = {(parent, name.casefold()): name for parent, names in children.items()
                   if parent in insensitive for name in names}
        return cls(MappingProxyType(files), frozenset(directories), frozenset(insensitive),
                   MappingProxyType(aliases), authority=authority)

    def resolve(self, name: str) -> str:
        """Resolve native case aliases while retaining captured spellings in the inventory."""
        parent = ""
        for part in PurePosixPath(name).parts:
            part = self.aliases.get((parent, part.casefold()), part)
            parent = f"{parent}/{part}" if parent else part
        return parent

    def is_file(self, name: str) -> bool:
        return self.resolve(name) in self.files

    def is_dir(self, name: str) -> bool:
        return self.resolve(name) in self.directories

    def original(self, name: str) -> bytes | None:
        return self.files.get(self.resolve(name))

    def text(self, name: str) -> str:
        return self.files[self.resolve(name)].decode("utf-8")

    def path(self, name: str = "") -> SnapshotPath:
        return SnapshotPath(self, name)


@dataclass(frozen=True)
class SnapshotPath:
    """The read-only Path surface used by compile.load and parse.py."""

    snapshot: Snapshot
    key: str = ""

    def __truediv__(self, name: str) -> SnapshotPath:
        return SnapshotPath(self.snapshot, (PurePosixPath(self.key) / name).as_posix())

    def __lt__(self, other: SnapshotPath) -> bool:
        return self.key < other.key

    @property
    def name(self) -> str:
        return PurePosixPath(self.key).name

    @property
    def stem(self) -> str:
        return PurePosixPath(self.key).stem

    def is_file(self) -> bool:
        return self.snapshot.is_file(self.key)

    def is_dir(self) -> bool:
        return self.snapshot.is_dir(self.key)

    def exists(self) -> bool:
        return self.is_file() or self.is_dir()

    def read_bytes(self) -> bytes:
        if not self.is_file():
            raise FileNotFoundError(self.key)
        return self.snapshot.files[self.snapshot.resolve(self.key)]

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding).replace("\r\n", "\n").replace("\r", "\n")

    def iterdir(self):
        key = self.snapshot.resolve(self.key)
        prefix = key + "/" if key else ""
        for name in sorted(set(self.snapshot.files) | self.snapshot.directories):
            if name.startswith(prefix) and name != self.key and "/" not in name[len(prefix):]:
                yield SnapshotPath(self.snapshot, name)

    def glob(self, pattern: str):
        insensitive = self.snapshot.resolve(self.key) in self.snapshot.insensitive_directories
        wanted = pattern.casefold() if insensitive else pattern
        return (path for path in self.iterdir()
                if fnmatch.fnmatchcase(path.name.casefold() if insensitive else path.name, wanted))


def glob_paths(directory: Path | SnapshotPath, pattern: str):
    """Use the directory's case rules for both disk and captured authority."""
    if isinstance(directory, SnapshotPath):
        return directory.glob(pattern)
    children = list(directory.iterdir()) if directory.is_dir() else []
    insensitive = _case_insensitive(directory, {path.name for path in children})
    wanted = pattern.casefold() if insensitive else pattern
    return (path for path in children
            if fnmatch.fnmatchcase(path.name.casefold() if insensitive else path.name, wanted))
