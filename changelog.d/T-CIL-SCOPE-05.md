- A scope capture now leaves out untracked cache files a repository forgot to ignore (SPEC §2.9).
  Besides Git's own ignore rules, a short built-in list applies to untracked files only: a file
  under a directory named `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox` or
  `node_modules` at any depth, and a file named `.DS_Store` or `Thumbs.db` or ending in `.pyc` or
  `.pyo`. The reply's `capture` member gives how many untracked files the list left out as
  `untrackedLeftOut`, omitted when none were. A tracked file is always recorded whatever its path,
  and `build`, `dist`, `target` and `vendor` are not on the list (T-CIL-SCOPE-05).
