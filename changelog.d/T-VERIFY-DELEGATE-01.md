- Delegated verification: under a grant of `verify`, the named agent records
  `praukron respond <task> VERIFY --delegate "<agent>"` for an `Authority:
  owner` task it did not build. The response keeps `By:` and adds `Delegate:`,
  `On authority of:` and `Grant:`, with `Via: delegate (cli)`. The task takes
  the new validation state `DELEGATE_VERIFIED`, which closes an owner task like
  `HUMAN_VERIFIED` but is shown and counted apart everywhere and gives way to
  `HUMAN_VERIFIED` when the owner verifies in person. `validate` reports a
  delegated verification whose grant is missing, does not cover the task, was
  not in force on its date, or whose delegate built the task.
