# Praukron graph library

An optional native library that gives Praukron a code graph. It is a thin C
ABI over [crowbett/codegraph](https://github.com/crowbett/codegraph), pinned by
commit in `Cargo.toml`, which parses with tree-sitter and keeps its graph in a
SQLite file. Nothing in Praukron loads it yet; Praukron works the same with or
without it.

## Building

The library is built in continuous integration (`.github/workflows/graph-library.yml`)
for macOS ARM64, macOS x86_64, Linux x86_64 and Linux ARM64. Each run uploads
the library with its size and SHA-256 as an artifact. To build it locally:

```sh
cargo build --release --locked --manifest-path native/graph/Cargo.toml
```

## ABI

Every call takes and returns UTF-8 JSON text.

| Function | Returns |
| --- | --- |
| `char *praukron_graph_info(void)` | library, ABI and engine versions |
| `void *praukron_graph_open(const char *config)` | a handle for `{"db": "<path>"}`, or null |
| `const char *praukron_graph_last_error(void)` | why the last open on this thread failed |
| `char *praukron_graph_call(void *handle, const char *request)` | `{"ok": true, "result": ...}` or `{"ok": false, "error": "..."}` |
| `void praukron_graph_free(char *text)` | frees text returned by `info` or `call` |
| `void praukron_graph_close(void *handle)` | closes the store |

A request is `{"op": "<operation>", ...}`:

| Operation | Arguments |
| --- | --- |
| `index` | `roots` (list of directories), `full` (bool) |
| `refresh` | `root`, `name` |
| `repos`, `status` | optional `repo` |
| `find_files` | `path` |
| `find` | `query`, `limit` |
| `resolve`, `def` | `symbol` |
| `refs` | `symbol`, optional `kind`, `limit` |
| `callers`, `callees` | `symbol`, `depth` |
| `path` | `from`, `to`, `depth` |
| `impact` | `target`, `depth`, `limit` |
| `graph` | optional `repo` |

All but `graph` are the engine's own queries. `graph` returns everything the
engine holds:

- `repos`: `{id, name, root}`.
- `nodes`: files `{id: "f:<n>", kind: "file", repo, path, lang, start_line, end_line}`
  and symbols `{id: "s:<n>", kind, repo, file, path, parent, name, qname,
  start_line, end_line, exported}`. Paths are relative to the repository root.
- `edges`: `{id, kind, source, target, name, qualifier, path, line, col,
  confidence}`. `kind` is the engine's (`call`, `ref`, `impl`, `inherit`,
  `import`, `value`). `source` is the enclosing symbol, or the file for a
  file-level reference; `target` is the resolved symbol, else the resolved file
  for an import, else null.
- `counts`: files, symbols, nodes, edges, and import, non-import and resolved
  edges, which match the engine's `status`.

## Tests

```sh
PRAUKRON_GRAPH_LIB=native/graph/target/release/libpraukron_graph.dylib \
    python3 -I native/graph/tests/test_library.py
```

The test uses the standard library only. It indexes `tests/fixture/`, checks
the callers of known functions, and compares the whole-graph export with the
engine's statistics and with the rows of its SQLite store.

## Third-party code

`THIRD-PARTY-NOTICES.txt` and `sbom.cdx.json` list every crate and grammar the
library compiles in, with its licence. `tools/notices.py` generates both from
`Cargo.lock` and fails on a crate with no licence or one outside its allowed
list; the workflow checks that both files are up to date.
