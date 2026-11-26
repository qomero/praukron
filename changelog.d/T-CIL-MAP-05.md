- The installed guidance now says how a task gets its files and symbols (SPEC §2.9, ADR-092).
  The `AGENTS.md` block, the `praukron-work` and `praukron-checkpoint` workflows, the skill and
  the chronicle README tell an agent that a claimed task's changed files and Python symbols are
  recorded by the write that sets it `DONE`, to read that reply's `capture` and report a skip,
  and to record only what the tool cannot know with `praukron ref add`: planned scope when the
  task is created or claimed, the symbols of the files `capture.symbolsUnresolved` lists, and
  files a task reads, verifies or documents without changing them. They name `praukron refs`
  and the packet's `references` for finding a task's code or a file's tasks, and
  `praukron scope capture` for a coordinator whose builders work in other worktrees. The
  chronicle README lists `CODE.md` and says it is written through `ref add` and `ref set`, never
  by hand. The `AGENTS.md` block grows by 495 bytes (T-CIL-MAP-05).
