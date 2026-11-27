- Added `praukron why <path|path#symbol|directory>`, which answers why a piece
  of code is as it is from the chronicle alone. It lists the tasks whose
  current references reach the target, oldest first, and for each how its
  reference was recorded (source, verification, relation, date), the decisions
  that reach it through `Governed by:` or `Affects:` with their standing (a
  superseded decision names what replaced it and is never shown as the rule in
  force; one not accepted is marked), its acceptance criteria and its inherited
  invariants, each with the record it comes from. Archived tasks are included.
  A symbol no reference names is answered plainly, with the history of the
  nearest enclosing symbol, file or directory that has a record. No graph
  library or index is read. `--json`
  gives the same answer as data. Nothing is written, and no existing command's
  output changes.
