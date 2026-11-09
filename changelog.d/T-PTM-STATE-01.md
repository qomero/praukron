- `praukron history state <revision>` reads the chronicle as it stood at a Git
  revision: it extracts the committed files into a fresh temporary directory
  and compiles them with the current compiler, the same reducer as `compile`,
  without checking anything out. The result names the repository, the full
  revision, the chronicle path and layout found there, and the compiler's
  version, commit and whether its sources were clean. Validation findings are
  reported under current rules; a revision with no chronicle, the v0.1 layout
  or a record the compiler cannot read is `UNSUPPORTED` with its reason, and
  nothing is migrated. `--git-dir` reads a separate chronicle repository
  (T-PTM-STATE-01, ADR-093).
