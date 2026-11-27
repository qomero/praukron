- Added `praukron engine status`, `praukron engine index` and
  `praukron engine export`, which load the optional embedded graph library
  (`native/graph/`) in process with `ctypes`, check its interface version,
  index or refresh the repository into `.praukron/engine/graph.db` (derived
  output, ignored by Git, never authority) and export the whole graph as one
  JSON document with the library's own counts. Praukron looks for the library
  at `PRAUKRON_GRAPH_LIB`, then at `.praukron/lib/<platform>/`. Without one the
  `engine` commands say so and succeed, and every other command is unchanged
  and never loads it; a library that cannot be used is reported in one line.
  The `Graph library` workflow now runs these commands against the library it
  builds.
