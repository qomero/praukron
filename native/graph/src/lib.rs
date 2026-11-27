//! C ABI over crowbett/codegraph (feature `graph`) for Praukron.
//!
//! Every call takes and returns JSON text. The ABI is deliberately small:
//!
//! - `praukron_graph_info()`: library, ABI and engine versions (free the result).
//! - `praukron_graph_open({"db": path})`: a handle, or null with
//!   `praukron_graph_last_error()` set.
//! - `praukron_graph_call(handle, {"op": ..., ...})`: `{"ok": true, "result": ...}`
//!   or `{"ok": false, "error": "..."}`; free the result.
//! - `praukron_graph_free(text)`: frees text this library returned.
//! - `praukron_graph_close(handle)`: closes the store.
//!
//! Operations: `index`, `refresh`, `repos`, `status`, `find_files`, `find`,
//! `resolve`, `def`, `refs`, `callers`, `callees`, `path`, `impact` (the engine's
//! own queries) and `graph`, which returns every node and edge the engine holds.
//!
//! A panic inside the engine is caught at this boundary and returned as an error.

use codegraph::{config::Config, index, query, store::Store};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::HashSet;
use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};

/// Version of this C ABI. Raised on any incompatible change to a signature,
/// an operation's arguments or the shape of a result.
pub const ABI_VERSION: u32 = 1;

/// The crowbett/codegraph commit this library is built from (see Cargo.toml).
pub const ENGINE_COMMIT: &str = "0dbef57e463cd7511376487146b875fb91b4050d";

/// An open graph store.
pub struct Engine {
    store: Store,
    cfg: Config,
}

thread_local! {
    static LAST_ERROR: RefCell<CString> = RefCell::new(CString::default());
}

fn set_error(message: String) {
    LAST_ERROR.with(|l| *l.borrow_mut() = CString::new(message).unwrap_or_default());
}

fn into_c(value: &Value) -> *mut c_char {
    CString::new(value.to_string()).unwrap_or_default().into_raw()
}

fn parse_arg(p: *const c_char) -> Result<Value, String> {
    if p.is_null() {
        return Err("null argument".into());
    }
    let text = unsafe { CStr::from_ptr(p) }.to_str().map_err(|e| e.to_string())?;
    serde_json::from_str(text).map_err(|e| e.to_string())
}

fn req_str<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v[key].as_str().ok_or(format!("missing `{key}`"))
}

fn opt_u64(v: &Value, key: &str, default: u64) -> u64 {
    v[key].as_u64().unwrap_or(default)
}

fn err<E: std::fmt::Display>(x: E) -> String {
    x.to_string()
}

/// Library, ABI and engine versions as JSON. Free the result with
/// `praukron_graph_free`.
#[no_mangle]
pub extern "C" fn praukron_graph_info() -> *mut c_char {
    into_c(&json!({
        "library": env!("CARGO_PKG_NAME"),
        "version": env!("CARGO_PKG_VERSION"),
        "abi": ABI_VERSION,
        "engine": "crowbett/codegraph",
        "engine_commit": ENGINE_COMMIT,
        "schema_version": codegraph::store::SCHEMA_VERSION,
    }))
}

/// The last error of `praukron_graph_open` on this thread. The text belongs to
/// the library and stays valid until the next failing open on this thread.
#[no_mangle]
pub extern "C" fn praukron_graph_last_error() -> *const c_char {
    LAST_ERROR.with(|l| l.borrow().as_ptr())
}

/// Opens (or creates) the store named by `{"db": path}`.
#[no_mangle]
pub extern "C" fn praukron_graph_open(config: *const c_char) -> *mut Engine {
    let r = catch_unwind(AssertUnwindSafe(|| -> Result<Engine, String> {
        let v = parse_arg(config)?;
        let store = Store::open(Path::new(req_str(&v, "db")?)).map_err(err)?;
        Ok(Engine { store, cfg: Config::default() })
    }));
    match r {
        Ok(Ok(engine)) => Box::into_raw(Box::new(engine)),
        Ok(Err(m)) => {
            set_error(m);
            std::ptr::null_mut()
        }
        Err(_) => {
            set_error("panic in praukron_graph_open".into());
            std::ptr::null_mut()
        }
    }
}

fn filter(engine: &Engine, v: &Value) -> Result<query::Filter, String> {
    let repos = query::repo_filter(&engine.store, v["repo"].as_str()).map_err(err)?;
    Ok(query::Filter { repos, kinds: None })
}

fn dispatch(engine: &mut Engine, v: &Value) -> Result<Value, String> {
    let op = req_str(v, "op")?;
    let to_json = |x: Result<Value, serde_json::Error>| x.map_err(err);
    match op {
        "index" => {
            let roots: Vec<PathBuf> = v["roots"]
                .as_array()
                .ok_or("missing `roots`")?
                .iter()
                .filter_map(|r| r.as_str().map(PathBuf::from))
                .collect();
            let opts = index::IndexOptions { full: v["full"].as_bool().unwrap_or(false), budget: None };
            let st = index::index_roots(&mut engine.store, &engine.cfg, &roots, &opts).map_err(err)?;
            to_json(serde_json::to_value(st))
        }
        "refresh" => {
            let opts = index::IndexOptions::default();
            let st = index::refresh_repo(
                &mut engine.store,
                &engine.cfg,
                Path::new(req_str(v, "root")?),
                req_str(v, "name")?,
                &opts,
            )
            .map_err(err)?;
            to_json(serde_json::to_value(st))
        }
        "repos" => Ok(Value::Array(
            engine
                .store
                .repos()
                .map_err(err)?
                .into_iter()
                .map(|r| json!({"id": r.id, "name": r.name, "root": r.root, "indexed_at": r.indexed_at}))
                .collect(),
        )),
        "status" => to_json(serde_json::to_value(
            query::status(&engine.store, &filter(engine, v)?).map_err(err)?,
        )),
        "find_files" => to_json(serde_json::to_value(
            query::find_files(&engine.store, req_str(v, "path")?, &filter(engine, v)?).map_err(err)?,
        )),
        "find" => to_json(serde_json::to_value(
            query::find(&engine.store, req_str(v, "query")?, &filter(engine, v)?, opt_u64(v, "limit", 20) as usize)
                .map_err(err)?,
        )),
        "resolve" => to_json(serde_json::to_value(
            query::resolve_target(&engine.store, req_str(v, "symbol")?, &filter(engine, v)?).map_err(err)?,
        )),
        "def" => to_json(serde_json::to_value(
            query::def(&engine.store, req_str(v, "symbol")?, &filter(engine, v)?, 5, 5).map_err(err)?,
        )),
        "refs" => to_json(serde_json::to_value(
            query::refs(
                &engine.store,
                req_str(v, "symbol")?,
                &filter(engine, v)?,
                v["kind"].as_str(),
                opt_u64(v, "limit", 50) as usize,
            )
            .map_err(err)?,
        )),
        "callers" | "callees" => {
            let dir = if op == "callers" { query::Dir::Callers } else { query::Dir::Callees };
            to_json(serde_json::to_value(
                query::call_tree(
                    &engine.store,
                    req_str(v, "symbol")?,
                    &filter(engine, v)?,
                    dir,
                    opt_u64(v, "depth", 1) as u32,
                    true,
                )
                .map_err(err)?,
            ))
        }
        "path" => to_json(serde_json::to_value(
            query::path(
                &engine.store,
                req_str(v, "from")?,
                req_str(v, "to")?,
                &filter(engine, v)?,
                opt_u64(v, "depth", 6) as u32,
            )
            .map_err(err)?,
        )),
        "impact" => to_json(serde_json::to_value(
            query::impact(
                &engine.store,
                req_str(v, "target")?,
                &filter(engine, v)?,
                opt_u64(v, "depth", 3) as u32,
                opt_u64(v, "limit", 100) as usize,
            )
            .map_err(err)?,
        )),
        "graph" => whole_graph(engine, v),
        _ => Err(format!("unknown op `{op}`")),
    }
}

/// Every node and edge the engine holds, read from its own store.
///
/// Nodes are files (`f:<id>`) and symbols (`s:<id>`). Edges are the engine's
/// own edge rows: the source is the symbol the reference sits in, or the file
/// when it sits at file level; the target is the resolved symbol, else the
/// resolved file (imports), else null (unresolved, with the referenced name).
/// Containment is given by each node's `file` and `parent`, not as edges,
/// because the engine stores it that way.
fn whole_graph(engine: &Engine, v: &Value) -> Result<Value, String> {
    let wanted: Option<HashSet<i64>> = filter(engine, v)?.repos.map(|ids| ids.into_iter().collect());
    let keep = |repo: i64| wanted.as_ref().map_or(true, |w| w.contains(&repo));
    let conn = engine.store.conn();

    let mut repos = Vec::new();
    {
        let mut st = conn.prepare("SELECT id, name, root FROM repos ORDER BY id").map_err(err)?;
        let rows = st
            .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?)))
            .map_err(err)?;
        for row in rows {
            let (id, name, root) = row.map_err(err)?;
            if keep(id) {
                repos.push(json!({"id": id, "name": name, "root": root}));
            }
        }
    }

    let mut nodes = Vec::new();
    let mut file_nodes = 0usize;
    let mut symbol_nodes = 0usize;
    {
        let mut st = conn
            .prepare("SELECT id, repo_id, path, lang, lines FROM files ORDER BY id")
            .map_err(err)?;
        let rows = st
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, i64>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, i64>(4)?,
                ))
            })
            .map_err(err)?;
        for row in rows {
            let (id, repo, path, lang, lines) = row.map_err(err)?;
            if keep(repo) {
                file_nodes += 1;
                nodes.push(json!({
                    "id": format!("f:{id}"), "kind": "file", "repo": repo, "path": path,
                    "lang": lang, "start_line": 1, "end_line": lines,
                }));
            }
        }
    }
    {
        let mut st = conn
            .prepare(
                "SELECT s.id, s.repo_id, s.file_id, f.path, s.parent_id, s.name, s.kind, s.qname, \
                 s.start_line, s.end_line, s.exported \
                 FROM symbols s JOIN files f ON f.id = s.file_id ORDER BY s.id",
            )
            .map_err(err)?;
        let rows = st
            .query_map([], |r| {
                Ok((
                    (r.get::<_, i64>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, String>(3)?),
                    (r.get::<_, Option<i64>>(4)?, r.get::<_, String>(5)?, r.get::<_, String>(6)?),
                    (r.get::<_, String>(7)?, r.get::<_, i64>(8)?, r.get::<_, i64>(9)?, r.get::<_, i64>(10)?),
                ))
            })
            .map_err(err)?;
        for row in rows {
            let ((id, repo, file, path), (parent, name, kind), (qname, start, end, exported)) = row.map_err(err)?;
            if keep(repo) {
                symbol_nodes += 1;
                nodes.push(json!({
                    "id": format!("s:{id}"), "kind": kind, "repo": repo, "file": format!("f:{file}"),
                    "path": path, "parent": parent.map(|p| format!("s:{p}")), "name": name,
                    "qname": qname, "start_line": start, "end_line": end, "exported": exported != 0,
                }));
            }
        }
    }

    let mut edges = Vec::new();
    let (mut imports, mut non_import, mut resolved) = (0usize, 0usize, 0usize);
    {
        let mut st = conn
            .prepare(
                "SELECT e.id, e.repo_id, e.file_id, f.path, e.from_symbol, e.name, e.qualifier, e.kind, \
                 e.line, e.col, e.to_symbol, e.to_file, e.confidence \
                 FROM edges e JOIN files f ON f.id = e.file_id ORDER BY e.id",
            )
            .map_err(err)?;
        let rows = st
            .query_map([], |r| {
                Ok((
                    (r.get::<_, i64>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, String>(3)?),
                    (r.get::<_, Option<i64>>(4)?, r.get::<_, String>(5)?, r.get::<_, Option<String>>(6)?),
                    (r.get::<_, String>(7)?, r.get::<_, i64>(8)?, r.get::<_, i64>(9)?),
                    (r.get::<_, Option<i64>>(10)?, r.get::<_, Option<i64>>(11)?, r.get::<_, Option<String>>(12)?),
                ))
            })
            .map_err(err)?;
        for row in rows {
            let ((id, repo, file, path), (from, name, qualifier), (kind, line, col), (to_sym, to_file, confidence)) =
                row.map_err(err)?;
            if !keep(repo) {
                continue;
            }
            if kind == "import" {
                imports += 1;
            } else {
                non_import += 1;
                if to_sym.is_some() {
                    resolved += 1;
                }
            }
            let source = from.map_or_else(|| format!("f:{file}"), |s| format!("s:{s}"));
            let target = to_sym
                .map(|s| format!("s:{s}"))
                .or_else(|| to_file.map(|f| format!("f:{f}")));
            edges.push(json!({
                "id": id, "kind": kind, "source": source, "target": target, "name": name,
                "qualifier": qualifier, "path": path, "line": line, "col": col, "confidence": confidence,
            }));
        }
    }

    Ok(json!({
        "engine": "crowbett/codegraph",
        "engine_commit": ENGINE_COMMIT,
        "repos": repos,
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "files": file_nodes,
            "symbols": symbol_nodes,
            "nodes": file_nodes + symbol_nodes,
            "edges": edges.len(),
            "import_edges": imports,
            "non_import_edges": non_import,
            "resolved_non_import_edges": resolved,
        },
    }))
}

/// Runs one operation. Free the result with `praukron_graph_free`.
#[no_mangle]
pub extern "C" fn praukron_graph_call(handle: *mut Engine, request: *const c_char) -> *mut c_char {
    let out = if handle.is_null() {
        json!({"ok": false, "error": "null handle"})
    } else {
        let engine = unsafe { &mut *handle };
        match catch_unwind(AssertUnwindSafe(|| parse_arg(request).and_then(|v| dispatch(engine, &v)))) {
            Ok(Ok(r)) => json!({"ok": true, "result": r}),
            Ok(Err(m)) => json!({"ok": false, "error": m}),
            Err(_) => json!({"ok": false, "error": "panic"}),
        }
    };
    into_c(&out)
}

/// Frees text returned by `praukron_graph_info` or `praukron_graph_call`.
#[no_mangle]
pub extern "C" fn praukron_graph_free(text: *mut c_char) {
    if !text.is_null() {
        unsafe { drop(CString::from_raw(text)) }
    }
}

/// Closes a handle from `praukron_graph_open`. Null is ignored.
#[no_mangle]
pub extern "C" fn praukron_graph_close(handle: *mut Engine) {
    if !handle.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| unsafe { drop(Box::from_raw(handle)) }));
    }
}
