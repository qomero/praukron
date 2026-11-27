"""Writes the third-party notices and the software bill of materials of the graph library.

Standard library only; needs `cargo` on PATH. Reads `cargo metadata --locked`
for every platform the library is built for, keeps what each build links in,
and writes, beside Cargo.toml:

- THIRD-PARTY-NOTICES.txt: every bundled crate and grammar, its licence and
  the licence files it ships;
- sbom.cdx.json: a CycloneDX 1.5 bill of materials of the same, plus the
  crates used only to build it.

It fails when a bundled crate has no licence or a licence outside ALLOWED.

    python3 native/graph/tools/notices.py          # write both files
    python3 native/graph/tools/notices.py --check  # fail if they are out of date
"""

import json
import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

CRATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(CRATE, "Cargo.toml")
NOTICES = os.path.join(CRATE, "THIRD-PARTY-NOTICES.txt")
SBOM = os.path.join(CRATE, "sbom.cdx.json")

PLATFORMS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
)

# Permissive licences: commercial use, modification, static and dynamic
# linking and redistribution, with Praukron's own source kept closed.
ALLOWED = {
    "0BSD", "Apache-2.0", "Apache-2.0 WITH LLVM-exception", "BSD-2-Clause",
    "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "ISC", "MIT", "MIT-0", "Unicode-3.0",
    "Unicode-DFS-2016", "Unlicense", "Zlib",
}

# Native code a crate compiles into the library, beyond its Rust source.
SQLITE_NOTE = ("The SQLite amalgamation compiled in by libsqlite3-sys (feature `bundled`) is in "
               "the public domain: https://www.sqlite.org/copyright.html")


def bundles(name):
    if name == "libsqlite3-sys":
        return "SQLite amalgamation (C), public domain"
    if name == "tree-sitter":
        return "tree-sitter runtime (C)"
    if name.startswith("tree-sitter-") and name != "tree-sitter-language":
        return "tree-sitter grammar: generated parser and scanner (C/C++)"
    return None


LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice|unlicense|copyright)([-._].*)?$", re.I)


def licence_allowed(expr):
    """True when the SPDX expression grants at least one allowed licence path."""
    expr = expr.replace("/", " OR ")
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+-]+(?: WITH [A-Za-z0-9.+-]+)?", expr)
    pos = 0

    def term():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            value = disjunction()
            pos += 1  # ")"
            return value
        return tok in ALLOWED

    def conjunction():
        nonlocal pos
        value = term()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            value = term() and value
        return value

    def disjunction():
        nonlocal pos
        value = conjunction()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            value = conjunction() or value
        return value

    return bool(tokens) and disjunction() and pos == len(tokens)


def metadata(platform):
    out = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--manifest-path", MANIFEST,
         "--filter-platform", platform],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def lock_checksums():
    with open(os.path.join(CRATE, "Cargo.lock"), "rb") as f:
        data = f.read()
    sums = {}
    if tomllib is not None:
        for p in tomllib.loads(data.decode("utf-8")).get("package", []):
            if "checksum" in p:
                sums[(p["name"], p["version"])] = p["checksum"]
        return sums
    for block in data.decode("utf-8").split("[[package]]"):
        fields = dict(re.findall(r'^(\w+) = "([^"]*)"$', block, re.M))
        if "checksum" in fields:
            sums[(fields["name"], fields["version"])] = fields["checksum"]
    return sums


def walk(meta):
    """Package id -> 'bundled' or 'build' for one platform's resolve."""
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]

    def is_proc_macro(pid):
        return any("proc-macro" in t["kind"] for t in packages[pid]["targets"])

    role = {}
    stack = [(root, "bundled")]
    while stack:
        pid, r = stack.pop()
        if role.get(pid) == "bundled" or role.get(pid) == r:
            continue
        role[pid] = "bundled" if r == "bundled" else role.get(pid, r)
        for dep in nodes[pid]["deps"]:
            kinds = [k["kind"] for k in dep["dep_kinds"]]
            if kinds and all(k == "dev" for k in kinds):
                continue
            child = dep["pkg"]
            normal = any(k is None for k in kinds)
            child_role = r if normal and not is_proc_macro(child) else "build"
            stack.append((child, child_role))
    del role[root]
    return packages, role


def licence_texts(package):
    folder = os.path.dirname(package["manifest_path"])
    texts = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if LICENCE_FILE.match(name) and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                texts.append((name, f.read().strip()))
    if package.get("license_file"):
        path = os.path.join(folder, package["license_file"])
        name = os.path.relpath(path, folder)
        if os.path.isfile(path) and name not in {n for n, _ in texts}:
            with open(path, encoding="utf-8", errors="replace") as f:
                texts.append((name, f.read().strip()))
    return texts


def source_label(package):
    src = package.get("source") or ""
    if src.startswith("registry+"):
        return "crates.io"
    if src.startswith("git+"):
        return src.split("?")[0][4:] + " @ " + src.rsplit("#", 1)[-1]
    return "this repository"


def purl(package):
    src = package.get("source") or ""
    base = "pkg:cargo/%s@%s" % (package["name"], package["version"])
    if src.startswith("git+"):
        return base + "?vcs_url=" + src.split("?")[0] + "%40" + src.rsplit("#", 1)[-1]
    return base


def collect():
    sums = lock_checksums()
    merged = {}
    root_pkg = None
    root_deps = set()
    for platform in PLATFORMS:
        meta = metadata(platform)
        packages, role = walk(meta)
        root_pkg = packages[meta["resolve"]["root"]]
        for node in meta["resolve"]["nodes"]:
            if node["id"] == meta["resolve"]["root"]:
                root_deps.update(d["pkg"] for d in node["deps"]
                                 if not all(k["kind"] == "dev" for k in d["dep_kinds"]))
        for pid, r in role.items():
            p = packages[pid]
            entry = merged.setdefault(pid, {"package": p, "role": "build", "platforms": set(), "deps": set()})
            if r == "bundled":
                entry["role"] = "bundled"
                entry["platforms"].add(platform)
        for node in meta["resolve"]["nodes"]:
            if node["id"] in merged:
                merged[node["id"]]["deps"].update(
                    d["pkg"] for d in node["deps"]
                    if not all(k["kind"] == "dev" for k in d["dep_kinds"]) and d["pkg"] in merged)
    rows = []
    for pid, entry in merged.items():
        p = entry["package"]
        rows.append({
            "id": pid, "name": p["name"], "version": p["version"], "licence": p.get("license") or "",
            "licence_file": p.get("license_file") or "", "role": entry["role"],
            "platforms": sorted(entry["platforms"]), "source": source_label(p), "purl": purl(p),
            "repository": p.get("repository") or "", "checksum": sums.get((p["name"], p["version"])),
            "bundles": bundles(p["name"]), "texts": licence_texts(p) if entry["role"] == "bundled" else [],
            "deps": sorted(merged[d]["package"]["name"] + "@" + merged[d]["package"]["version"]
                           for d in entry["deps"]),
        })
    rows.sort(key=lambda r: (r["name"], r["version"]))
    root_pkg = dict(root_pkg, direct=sorted(
        merged[d]["package"]["name"] + "@" + merged[d]["package"]["version"] for d in root_deps))
    return root_pkg, rows


def problems(rows):
    out = []
    for r in rows:
        if r["role"] != "bundled":
            continue
        if not r["licence"]:
            out.append("%s %s: no SPDX licence in its metadata (%s)" % (
                r["name"], r["version"], r["licence_file"] or "no licence file named"))
        elif not licence_allowed(r["licence"]):
            out.append("%s %s: licence %r is not on the allowed list" % (r["name"], r["version"], r["licence"]))
        if not r["texts"]:
            out.append("%s %s: ships no licence file" % (r["name"], r["version"]))
    return out


def render_notices(root, rows):
    bundled = [r for r in rows if r["role"] == "bundled"]
    lines = [
        "THIRD-PARTY NOTICES: libpraukron_graph",
        "",
        "The Praukron graph library (%s %s, native/graph/) is built from crowbett/codegraph"
        % (root["name"], root["version"]),
        "and the crates below, which it compiles in. This file lists each of them with its",
        "licence and reproduces the licence files each one ships. It is generated by",
        "native/graph/tools/notices.py from Cargo.lock; do not edit it by hand.",
        "",
        "Crates used only to build the library (build scripts, procedural macros) are not",
        "compiled into it; they are listed in sbom.cdx.json with scope `excluded`.",
        "",
        "Bundled: %d crates, %d of them tree-sitter grammars." % (
            len(bundled), sum(1 for r in bundled if (r["bundles"] or "").startswith("tree-sitter grammar"))),
        "",
        "%-28s %-10s %-34s %s" % ("crate", "version", "licence", "native code"),
    ]
    for r in bundled:
        lines.append(("%-28s %-10s %-34s %s" % (r["name"], r["version"], r["licence"], r["bundles"] or "")).rstrip())
    all_platforms = list(PLATFORMS)
    for r in bundled:
        lines += ["", "=" * 78, "%s %s" % (r["name"], r["version"]), "=" * 78,
                  "Licence: %s" % r["licence"], "Source: %s" % r["source"]]
        if r["repository"]:
            lines.append("Repository: %s" % r["repository"])
        if r["bundles"]:
            lines.append("Native code: %s" % r["bundles"])
        if r["platforms"] != sorted(all_platforms):
            lines.append("Platforms: %s" % ", ".join(r["platforms"]))
        if r["name"] == "libsqlite3-sys":
            lines += ["", SQLITE_NOTE]
        for name, text in r["texts"]:
            lines += ["", "--- %s ---" % name, "", text]
    return "\n".join(lines) + "\n"


def render_sbom(root, rows):
    components = []
    for r in rows:
        c = {
            "type": "library",
            "bom-ref": r["purl"],
            "name": r["name"],
            "version": r["version"],
            "purl": r["purl"],
            "scope": "required" if r["role"] == "bundled" else "excluded",
        }
        if r["licence"]:
            c["licenses"] = [{"expression": r["licence"]}]
        if r["checksum"]:
            c["hashes"] = [{"alg": "SHA-256", "content": r["checksum"]}]
        refs = []
        if r["repository"]:
            refs.append({"type": "vcs", "url": r["repository"]})
        if refs:
            c["externalReferences"] = refs
        props = [{"name": "praukron:role", "value": r["role"]}]
        if r["platforms"]:
            props.append({"name": "praukron:platforms", "value": " ".join(r["platforms"])})
        if r["bundles"]:
            props.append({"name": "praukron:native-code", "value": r["bundles"]})
        c["properties"] = props
        components.append(c)
    by_ref = {"%s@%s" % (r["name"], r["version"]): r["purl"] for r in rows}
    root_ref = "pkg:cargo/%s@%s" % (root["name"], root["version"])
    deps = [{"ref": root_ref, "dependsOn": [by_ref[d] for d in root["direct"]]}]
    deps += [{"ref": r["purl"], "dependsOn": [by_ref[d] for d in r["deps"]]} for r in rows]
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "library",
                "bom-ref": root_ref,
                "name": root["name"],
                "version": root["version"],
                "licenses": [{"expression": root.get("license") or "NOASSERTION"}],
                "description": "Praukron graph library: a C ABI over crowbett/codegraph",
            },
            "properties": [
                {"name": "praukron:generator", "value": "native/graph/tools/notices.py"},
                {"name": "praukron:platforms", "value": " ".join(PLATFORMS)},
            ],
        },
        "components": components,
        "dependencies": deps,
    }
    return json.dumps(bom, indent=1, sort_keys=True) + "\n"


def main(argv):
    check = "--check" in argv
    root, rows = collect()
    bad = problems(rows)
    for line in bad:
        print("licence problem: " + line, file=sys.stderr)
    outputs = {NOTICES: render_notices(root, rows), SBOM: render_sbom(root, rows)}
    stale = []
    for path, text in outputs.items():
        if check:
            current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if current != text:
                stale.append(os.path.basename(path))
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    bundled = [r for r in rows if r["role"] == "bundled"]
    print("bundled crates: %d; build-only crates: %d; licence problems: %d"
          % (len(bundled), len(rows) - len(bundled), len(bad)))
    for r in bundled:
        print("  %-28s %-10s %s" % (r["name"], r["version"], r["licence"]))
    if stale:
        print("out of date: %s (run native/graph/tools/notices.py)" % ", ".join(stale), file=sys.stderr)
    return 1 if bad or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
