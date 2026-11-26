- `praukron refs <target>` looks up code from a task and tasks from code (SPEC §2.9). For a task
  it lists every file and symbol reference with relation, scope, verification, authorization,
  revision, evidence and its `file#anchor`; for a path or `path#Qualified.symbol` it names every
  task that references it, with their governing ADRs, acceptance criteria and assumptions.
  `--json` prints JSON; a path outside the repository is refused with the path rule's message.
  `context <task>` and `retrieve <task>` now carry a `references` field: counts by scope, the
  first twelve references, and the command that returns the rest. No existing packet field
  changes, and no graph provider is consulted (T-CIL-MAP-03).
