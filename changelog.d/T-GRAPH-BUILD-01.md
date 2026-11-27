- Added `native/graph/`, an optional native graph library: a small C ABI over
  crowbett/codegraph (pinned by commit) with a whole-graph export, a `ctypes`
  test, third-party notices and a CycloneDX bill of materials. A new
  `Graph library` workflow builds and tests it on macOS ARM64, macOS x86_64,
  Linux x86_64 and Linux ARM64. Nothing in Praukron loads it yet, and
  Praukron behaves exactly as before with or without it.
