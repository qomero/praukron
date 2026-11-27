- Fixed: a capture attributes only the commits this checkout made. An automatic
  capture (a `DONE` write, or `scope capture` without `--head`) reads the
  checkout's own HEAD reflog through Git (a linked worktree reads its own) and
  records every file and symbol changed by a first-parent, non-merge commit the
  reflog does not show as made here as `Lifecycle: unresolved` and
  `Verification: unverified`. This catches work of others that arrives with no
  merge commit: a squash or rebase merge pulled by fast-forward, `git pull
  --rebase`, `git reset` to a remote tip, a checkout. The evidence names each
  commit by short id and the kind of its arrival (`arrived by` `pull`,
  `fetch`, `merge`, `reset`, `checkout`, `rebase`, `clone` or `other`; never
  the action's arguments, which can hold a credential), or says the reflog could not show where it was made (no reflog, an
  expired or truncated one, reflogs turned off, a fresh clone). Commits made
  here are those an entry `commit`, `commit (initial)`, `commit (amend)`,
  `commit (merge)`, `cherry-pick`, `revert`, `am`, or a rebase step that writes
  a commit (`pick`, `reword`, `edit`, `squash`, `fixup`, `continue`) moved HEAD
  to, so the task's own commits replayed by a rebase count and the commits they
  were replayed onto do not. The reply's `capture` carries `notMadeHere` and
  `notMadeHereInvolved` beside `merges`, and the one `warning` sentence covers
  both causes; the write still succeeds, and a reflog that cannot be read
  accounts for nothing. A named capture (`--base` and `--head`) does not read
  the reflog.
- Limit: a patch of others applied and committed here by hand (`git merge
  --squash` then `git commit`, `git cherry-pick`, `git am`, `git commit --amend`
  of a commit made elsewhere) counts as made here; settle it with a named
  capture or `ref set`.
