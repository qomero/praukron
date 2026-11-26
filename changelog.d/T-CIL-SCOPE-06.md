- `praukron scope capture <task> --base <rev> --head <rev>`, with both revisions named, now
  attributes the files and symbols changed between them to the task: the caller vouches for the
  range, so other tasks claimed over it no longer make the records `Lifecycle: unresolved`. Each
  record's evidence says `range named by the caller`, and the reply's `capture` member has
  `rangeNamed: true`. A coordinator whose builders work in other worktrees, and so claims their
  tasks at one commit, captures each merged task this way. The capture a DONE write makes by
  itself, and `scope capture` without `--head`, keep the overlap rule unchanged (SPEC §2.9,
  T-CIL-SCOPE-06).
