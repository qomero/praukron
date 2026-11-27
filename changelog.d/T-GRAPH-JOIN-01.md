- Added `praukron engine map <task|path|path#symbol>`, which joins the
  embedded graph's index to the task-code references. For a task it lists the
  files and symbols the task touched, the engine nodes they match, the
  resolved edges among them and to their direct neighbours, the edges the
  engine left unresolved, and the references that match no node with the
  reason; for a file or symbol it lists the tasks that touched it and its
  neighbours. Every item names its source (`engine`, `engine-unresolved`,
  `reference:<Source>`), and an unresolved edge is shown as unknown, never as
  "no relation". The index is read with Python's `sqlite3` alone, read only,
  so no library is needed to read one; without an index the command answers
  from the references alone and says so. Nothing it computes is written to
  the chronicle. The agent workflow now says to read the code behind an
  unresolved edge and record it with `ref add` and source `agent`.
