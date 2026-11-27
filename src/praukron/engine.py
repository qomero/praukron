"""The optional embedded graph library (ADR-100), loaded with ctypes.

`native/graph/` builds a small C library over crowbett/codegraph. This module
finds it, loads it in process, checks the interface version it reports, and
wraps the few operations Praukron uses: open a store, index or refresh the
repository, report status, and export the whole graph as data.

The library is optional. When it is absent Praukron works exactly as in 4.0.0:
no command that existed then imports this module, and the `engine` commands
say that no library is present and succeed. When it is present but cannot be
used (not a library, another interface version, a missing symbol, an error or
a caught panic inside a call), the cause comes back as an `EngineError` with a
one-line message, never as a crash.

Where Praukron looks, first match wins:

1. the path in the `PRAUKRON_GRAPH_LIB` environment variable, when it is set
   and not empty; nothing else is tried then;
2. `.praukron/lib/<platform>/<file>` in the repository, where `<platform>` is
   `macos-arm64`, `macos-x86_64`, `linux-x86_64` or `linux-arm64` (the names
   the `Graph library` workflow gives its artifacts) and `<file>` is
   `libpraukron_graph.dylib` on macOS and `libpraukron_graph.so` on Linux.

On any other platform there is no default place, and the library is absent
unless the environment variable names one.

The index is derived output, like `.praukron/compiled/`: the library's SQLite
store at `.praukron/engine/graph.db`, in a directory whose own `.gitignore`
ignores everything in it. It is never authority. Nothing that validates,
compiles, or answers `context` or `retrieve` reads it, no record cites it, and
it is safe to delete.

Standard library only.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform as _platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import layout

ENV_VAR = "PRAUKRON_GRAPH_LIB"
ABI_VERSION = 1
LIBRARY_DIR = f"{layout.HOME_DIR}/lib"
INDEX_DIR = f"{layout.HOME_DIR}/engine"
INDEX_FILE = "graph.db"
SOURCE = "graph-library"

_SYMBOLS = (
    "praukron_graph_info",
    "praukron_graph_open",
    "praukron_graph_last_error",
    "praukron_graph_call",
    "praukron_graph_free",
    "praukron_graph_close",
)
_IGNORE = b"*\n"
_MAX_MESSAGE = 400

# The loader, replaceable in tests so that a library can be simulated without
# a native build.
_cdll: Callable[[str], Any] = ctypes.CDLL


class EngineError(Exception):
    """A graph library that could not be used, with a one-line reason."""

    def __init__(self, message: str) -> None:
        super().__init__(one_line(message))


def one_line(text: object) -> str:
    """`text` on one line, shortened when very long."""
    line = " ".join(str(text).split())
    return line if len(line) <= _MAX_MESSAGE else line[: _MAX_MESSAGE - 1] + "…"


def platform_tag(system: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    """The artifact name of this platform, or None where none is built."""
    system = sys.platform if system is None else system
    machine = (_platform.machine() if machine is None else machine).lower()
    os_name = "macos" if system == "darwin" else "linux" if system.startswith("linux") else None
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    if os_name is None or arch is None:
        return None
    return f"{os_name}-{arch}"


def library_file(tag: Optional[str]) -> Optional[str]:
    if tag is None:
        return None
    return "libpraukron_graph.dylib" if tag.startswith("macos-") else "libpraukron_graph.so"


def default_path(root: Path, tag: Optional[str] = None) -> Optional[Path]:
    """Where the library sits in the repository for this platform."""
    tag = platform_tag() if tag is None else tag
    name = library_file(tag)
    return None if name is None else root / LIBRARY_DIR / tag / name


def index_path(root: Path) -> Path:
    return root / INDEX_DIR / INDEX_FILE


@dataclass
class Location:
    """Where the library was looked for, and where it was found."""

    platform: Optional[str]
    searched: List[str] = field(default_factory=list)
    path: Optional[Path] = None
    source: Optional[str] = None  # "environment" or "repository"
    # The environment named a file that does not exist: an error, not absence.
    missing: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.path is not None


def locate(root: Path, environ: Optional[Dict[str, str]] = None) -> Location:
    environ = os.environ if environ is None else environ
    tag = platform_tag()
    where = Location(platform=tag)
    named = environ.get(ENV_VAR, "")
    if named:
        path = Path(named).expanduser()
        where.searched.append(f"{ENV_VAR}={named}")
        if path.is_file():
            where.path, where.source = path, "environment"
        else:
            where.missing = f"{ENV_VAR} names {named}, which is not a file"
        return where
    candidate = default_path(root, tag)
    if candidate is None:
        return where
    where.searched.append(_relative(root, candidate))
    if candidate.is_file():
        where.path, where.source = candidate, "repository"
    return where


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


class Library:
    """A loaded graph library whose interface version has been checked."""

    def __init__(self, handle: Any, path: Path) -> None:
        self.path = path
        self._lib = handle
        try:
            functions = {name: getattr(handle, name) for name in _SYMBOLS}
        except AttributeError as error:
            missing = next(name for name in _SYMBOLS if not hasattr(handle, name))
            raise EngineError(f"{path} is not a Praukron graph library: it has no `{missing}`") from error
        _signature(functions["praukron_graph_info"], ctypes.c_void_p, [])
        _signature(functions["praukron_graph_open"], ctypes.c_void_p, [ctypes.c_char_p])
        _signature(functions["praukron_graph_last_error"], ctypes.c_char_p, [])
        _signature(functions["praukron_graph_call"], ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p])
        _signature(functions["praukron_graph_free"], None, [ctypes.c_void_p])
        _signature(functions["praukron_graph_close"], None, [ctypes.c_void_p])
        self._fn = functions
        self.info = self._info()

    def _take(self, pointer: Optional[int], what: str) -> Any:
        if not pointer:
            raise EngineError(f"the graph library returned nothing from {what}")
        try:
            text = ctypes.string_at(pointer)
        finally:
            self._fn["praukron_graph_free"](pointer)
        try:
            return json.loads(text.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise EngineError(f"the graph library returned malformed JSON from {what}: {error}") from error

    def _info(self) -> Dict[str, Any]:
        info = self._take(self._fn["praukron_graph_info"](), "info")
        if not isinstance(info, dict) or "abi" not in info:
            raise EngineError(f"{self.path} did not report an interface version")
        if info["abi"] != ABI_VERSION:
            raise EngineError(
                f"{self.path} reports interface version {info['abi']}; "
                f"this Praukron needs version {ABI_VERSION}"
            )
        return info

    def open(self, db: Path) -> "Store":
        db.parent.mkdir(parents=True, exist_ok=True)
        pointer = self._fn["praukron_graph_open"](json.dumps({"db": str(db)}).encode("utf-8"))
        if not pointer:
            reason = self._fn["praukron_graph_last_error"]()
            reason = reason.decode("utf-8", "replace") if reason else "no reason given"
            raise EngineError(f"the graph library could not open {db}: {reason}")
        return Store(self, pointer)

    def summary(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.info.get("version"),
            "abi": self.info.get("abi"),
            "engine": self.info.get("engine"),
            "engine_commit": self.info.get("engine_commit"),
            "schema_version": self.info.get("schema_version"),
        }


def _signature(function: Any, restype: Any, argtypes: list) -> None:
    function.restype = restype
    function.argtypes = argtypes


class Store:
    """An open graph store. Close it, or use it as a context manager."""

    def __init__(self, library: Library, pointer: int) -> None:
        self._library = library
        self._pointer: Optional[int] = pointer

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._pointer:
            self._library._fn["praukron_graph_close"](self._pointer)
            self._pointer = None

    def call(self, request: Dict[str, Any]) -> Any:
        """The result of one operation; an `EngineError` when it fails."""
        if not self._pointer:
            raise EngineError("the graph store is closed")
        op = request.get("op", "?")
        response = self._library._take(
            self._library._fn["praukron_graph_call"](self._pointer, json.dumps(request).encode("utf-8")),
            f"`{op}`",
        )
        if not isinstance(response, dict) or "ok" not in response:
            raise EngineError(f"the graph library answered `{op}` with something that is not a response")
        if not response["ok"]:
            raise EngineError(f"the graph library failed in `{op}`: {response.get('error') or 'no reason given'}")
        return response.get("result")

    def index(self, roots: List[Path], full: bool = False) -> Dict[str, Any]:
        return self.call({"op": "index", "roots": [str(r) for r in roots], "full": full})

    def refresh(self, root: Path, name: str) -> Dict[str, Any]:
        return self.call({"op": "refresh", "root": str(root), "name": name})

    def repos(self) -> List[Dict[str, Any]]:
        return self.call({"op": "repos"}) or []

    def status(self) -> Dict[str, Any]:
        return self.call({"op": "status"}) or {}

    def graph(self) -> Dict[str, Any]:
        return self.call({"op": "graph"}) or {}


def load(path: Path) -> Library:
    """Load and check the library at `path`; an `EngineError` when it cannot be used."""
    try:
        handle = _cdll(str(path))
    except OSError as error:
        raise EngineError(f"cannot load {path}: {error}") from error
    return Library(handle, path)


def ensure_index_dir(root: Path) -> Path:
    """The index directory, created with a `.gitignore` that ignores all of it."""
    directory = root / INDEX_DIR
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.is_file():
        ignore.write_bytes(_IGNORE)
    return directory


def _same(a: object, b: Path) -> bool:
    try:
        return Path(str(a)).resolve() == b.resolve()
    except OSError:
        return False


def index(library: Library, root: Path, full: bool = False) -> Dict[str, Any]:
    """Index `root`, or refresh it when the index already holds it.

    A refresh re-reads only what changed. A full run, a first run, or an index
    that also holds other repositories (nested ones, or a moved checkout) goes
    through `index`, which is incremental unless `full` is set.
    """
    ensure_index_dir(root)
    with library.open(index_path(root)) as store:
        repos = store.repos()
        own = [r for r in repos if _same(r.get("root"), root)]
        if own and not full and len(repos) == 1:
            action, stats = "refreshed", store.refresh(root, str(own[0].get("name")))
        else:
            action, stats = "indexed", store.index([root], full=full or not repos)
        status = store.status()
    return {"action": action, "root": str(root), "index": str(index_path(root)), "stats": stats, "status": status}


def export(library: Library, root: Path) -> Dict[str, Any]:
    """The whole graph as one document, with the library's own counts."""
    db = index_path(root)
    if not db.is_file():
        raise EngineError(f"there is no index at {_relative(root, db)}; run `praukron engine index` first")
    with library.open(db) as store:
        graph = store.graph()
    if not isinstance(graph, dict):
        raise EngineError("the graph library returned a graph that is not an object")
    return {"source": SOURCE, "library": library.summary(), "repository": str(root), **graph}


# Commands ------------------------------------------------------------------


def _absent_message(where: Location) -> str:
    looked = ", ".join(where.searched) if where.searched else f"no default place on {sys.platform}"
    return (f"No graph library is present (looked at {looked}). "
            f"Praukron works without it; set {ENV_VAR} to use one elsewhere.")


def _probe(root: Path) -> Dict[str, Any]:
    where = locate(root)
    db = index_path(root)
    state: Dict[str, Any] = {
        "source": SOURCE,
        "present": where.present,
        "platform": where.platform,
        "searched": where.searched,
        "index": {"path": _relative(root, db), "present": db.is_file()},
    }
    if where.missing:
        state["error"] = where.missing
        return state
    if where.present:
        state["path"] = str(where.path)
        state["from"] = where.source
    return state


def command(root: Path, args: Any) -> int:
    """`praukron engine status|index|export`. Never raises for a library fault."""
    try:
        return _command(root, args)
    except EngineError as error:
        print(f"praukron engine {args.action}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # a fault at the native boundary must not crash Praukron
        print(f"praukron engine {args.action}: unexpected failure: {one_line(error)}", file=sys.stderr)
        return 1


def _command(root: Path, args: Any) -> int:
    state = _probe(root)
    if state.get("error"):
        raise EngineError(state["error"])
    if not state["present"]:
        where = locate(root)
        if args.action == "status" and args.json:
            print(json.dumps(state, indent=2))
        else:
            # Export keeps standard output for the JSON document alone.
            stream = sys.stderr if args.action == "export" else sys.stdout
            print(_absent_message(where), file=stream)
        return 0
    library = load(Path(state["path"]))
    if args.action == "status":
        return _status(root, library, state, args.json)
    if args.action == "index":
        result = index(library, root, full=args.full)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(_index_line(root, result))
        return 0
    document = export(library, root)
    return _write_export(root, document, args.output)


def _status(root: Path, library: Library, state: Dict[str, Any], as_json: bool) -> int:
    state["library"] = library.summary()
    if state["index"]["present"]:
        with library.open(index_path(root)) as store:
            state["index"]["status"] = store.status()
    if as_json:
        print(json.dumps(state, indent=2))
        return 0
    summary = state["library"]
    print("Graph library")
    print(f"  path            {summary['path']} ({state['from']})")
    print(f"  version         {summary['version']} · interface {summary['abi']}")
    print(f"  engine          {summary['engine']} {summary['engine_commit']}")
    index_state = state["index"]
    if index_state["present"]:
        counts = index_state.get("status") or {}
        print(f"  index           {index_state['path']} · {counts.get('files', 0)} files, "
              f"{counts.get('symbols', 0)} symbols, {counts.get('edges', 0)} non-import edges")
    else:
        print(f"  index           none yet; `praukron engine index` writes {index_state['path']}")
    print("  authority       none: the index is derived output and no record depends on it")
    return 0


def _index_line(root: Path, result: Dict[str, Any]) -> str:
    stats = result.get("stats") or {}
    status = result.get("status") or {}
    return (f"{result['action'].capitalize()} {root}: {stats.get('files', 0)} files "
            f"({stats.get('parsed', 0)} parsed, {stats.get('unchanged', 0)} unchanged, "
            f"{stats.get('removed', 0)} removed); index holds {status.get('files', 0)} files, "
            f"{status.get('symbols', 0)} symbols, {status.get('edges', 0)} non-import edges "
            f"in {_relative(root, index_path(root))}")


def _write_export(root: Path, document: Dict[str, Any], output: Optional[str]) -> int:
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    if output in (None, "-"):
        sys.stdout.write(text)
        return 0
    target = Path(output).expanduser().resolve()
    chronicle = (root / layout.AUTHORITY_DIR).resolve()
    if target == chronicle or chronicle in target.parents:
        raise EngineError(f"refusing to write {target}: the chronicle holds authority, not derived output")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    counts = document.get("counts") or {}
    print(f"Wrote {counts.get('nodes', len(document.get('nodes', [])))} nodes and "
          f"{counts.get('edges', len(document.get('edges', [])))} edges to {target}")
    return 0
