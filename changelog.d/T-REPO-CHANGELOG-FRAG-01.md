- Changelog fragments: a pull request now records its change in `changelog.d/<task id>.md` instead of editing
  `CHANGELOG.md`, so pull requests stop conflicting. `scripts/changelog.py assemble <version> <date>` merges the
  fragments into the release section at release time. A repository tool; it is not installed.
