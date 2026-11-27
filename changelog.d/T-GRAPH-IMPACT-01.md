- Added `praukron engine impact <task> [--depth N] [--exact] [--json]`, which
  answers what a planned change runs into. From the task's current
  references, planned and observed, it follows the graph backwards to the code
  that calls or depends on them, to a stated depth (default 2), and lists the
  tasks whose references lie on that code with their decisions, frozen
  criteria and inherited invariants, grouped by decision with the shortest
  path to each. Every path carries the `exact`, `likely` or `name_only` mark of
  its edges, and `--exact` follows `exact` edges only. The task's own
  decisions are listed apart, a superseded decision is shown as superseded,
  and each link says how its reference was recorded. A reference with no node
  or an unresolved edge on the code followed marks further reach as unknown;
  without an index the answer covers only the task's own code and says that
  reach through the graph is unknown. The answer is what the change must be
  checked against, not a verdict, and asks the reader to record the
  conclusion. The index is read with `sqlite3` alone; nothing is written. The
  installed guidance tells an agent to ask for the impact before changing
  existing code.
