"""One immutable byte inventory, exposed to the canonical parsers without disk rereads."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Snapshot:
    files: Mapping[str, bytes]
    directories: frozenset[str]

    @classmethod
    def capture(cls, authority: Path) -> Snapshot:
        """The caller holds the shared write lock throughout capture and planning."""
        files, directories = {}, {""}
        for path in sorted(authority.rglob("*")):
            name = path.relative_to(authority).as_posix()
            if path.is_file():
                files[name] = path.read_bytes()
            elif path.is_dir():
                directories.add(name)
            else:
                raise OSError(f"cannot snapshot chronicle path: {name}")
        return cls(MappingProxyType(files), frozenset(directories))

    def text(self, name: str) -> str:
        return self.files[name].decode("utf-8")

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
        return self.key in self.snapshot.files

    def is_dir(self) -> bool:
        return self.key in self.snapshot.directories

    def exists(self) -> bool:
        return self.is_file() or self.is_dir()

    def read_bytes(self) -> bytes:
        if not self.is_file():
            raise FileNotFoundError(self.key)
        return self.snapshot.files[self.key]

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding).replace("\r\n", "\n").replace("\r", "\n")

    def iterdir(self):
        prefix = self.key + "/" if self.key else ""
        for name in sorted(set(self.snapshot.files) | self.snapshot.directories):
            if name.startswith(prefix) and name != self.key and "/" not in name[len(prefix):]:
                yield SnapshotPath(self.snapshot, name)

    def glob(self, pattern: str):
        return (path for path in self.iterdir() if fnmatch.fnmatchcase(path.name, pattern))
