- A task's observed files are captured from Git by themselves (SPEC §2.6, §2.9, ADR-092). In a
  Git repository a structured write that claims a task (`task update` to `WIP`, alone or in
  `batch apply`) records the current commit as `- Claimed at: <sha>`; a task claimed again keeps
  its first. The write that sets the task `DONE` records, in the same all-or-nothing write, every
  file changed since that commit, the working tree and untracked files included, as observed
  references in `CODE.md` (`Source: git_diff`, `Base`, `Head`, `Verification: verified`,
  `Authorization: not_applicable`): added `creates`, modified `modifies`, deleted `deletes` with
  `Lifecycle: deleted`, renamed `modifies` with `Was`. Files Git ignores and `.praukron/` are not
  recorded, a file already recorded is not recorded again, and a change another claimed task may
  have made is recorded `Lifecycle: unresolved` with `unresolved attribution` in its evidence.
  Capture never blocks: without Git, outside a repository, without a claim commit or when Git
  fails, the write is unchanged and its reply's `capture` member says why capture was skipped;
  otherwise `capture` says how many references were written. `praukron scope capture <task>
  --base <rev> [--head <rev>]` does the same between two named revisions (T-CIL-SCOPE-01).
