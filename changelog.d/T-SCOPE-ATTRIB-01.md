- Fixed: a capture that cannot be attributed is never recorded as verified. An
  automatic capture (a `DONE` write, or `scope capture` without `--head`) now
  writes `Lifecycle: unresolved` with `Verification: unverified` when another
  claimed task overlaps a path, and also, whether or not any task is claimed,
  for every file and symbol changed by a commit that entered the range only
  through a merge's second or later parent; the evidence names the merge
  commits by short id. Git's ancestry alone decides. The reply's `capture`
  carries `merges`, `mergesInvolved` and one `warning` sentence naming the two
  ways to settle; the write still succeeds. A named capture (`--base` and
  `--head`) is unchanged and now replaces the task's unresolved records for
  what it records (`settled`, `stillUnresolved`).
- `refs`, `why`, `engine map`, `engine impact` and the task packet mark each unresolved observed
  reference `"attribution": "unresolved"` (text: `UNRESOLVED ATTRIBUTION`) and
  count them; a task reached only through such references is marked.
- `ref set` settling an unresolved reference (any other lifecycle) needs
  evidence and appends a successor that replaces it, so the unresolved record
  stays as history; it refuses to set one `verified` in place.
- `validate` warns once per task (`unattributed-verified-references`) about
  current captured references recorded unresolved yet verified by an earlier
  version.
- Limit: work merged by squash, rebase or fast-forward has no merge commit and
  is not detected; close such a task with a named capture.
