- The README, `docs/GUIDE.md` and `docs/SPEC.md` describe task–code references and their
  capture. The README explains, problem first, that a task claimed and closed through a
  structured write ends up with its files and Python symbols in `CODE.md` with no extra step,
  and shows the claim, the close and its `capture` reply, `praukron refs` in both directions and
  the packet's `references` field from a synthetic repository. The guide covers reading,
  adding (`ref add`) and correcting (`ref set`: verify, replace, withdraw; `ref history`)
  references, planned against observed scope, the dashboard's Files and Symbols columns and
  Code References section, `scope capture`, and the limits: symbols for Python only, unresolved
  attribution when two tasks are open over the same commits, pulled and merged changes counting
  for the open task, a range named with both `--base` and `--head` being attributed to its task,
  untracked caches left out (`untrackedLeftOut`), capture needing Git, and no graph provider
  needed, a provider's network behaviour being the user's to handle. SPEC §2.9 reads as one specification of what is built.
  The command, file and task-field tables list `refs`, `ref add`, `ref set`, `ref history`,
  `scope capture`, `CODE.md` and `Claimed at`; the dashboard task dialog screenshot is
  regenerated and two new ones show the Code References section and the task registry
  (T-DOCS-CIL-01).
