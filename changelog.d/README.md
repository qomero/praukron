# Changelog fragments

A pull request records its change here, not in `CHANGELOG.md`.

Add one file named for your task, `changelog.d/<task id>.md` (for example
`changelog.d/T-EXAMPLE-01.md`), holding one or more bullets in the style of
`CHANGELOG.md`. Do not edit `CHANGELOG.md`: two pull requests then never touch
the same file, so they stop conflicting.

A fragment starts with a `- ` bullet, may continue it on indented lines, and
holds no headings. At release the maintainer runs
`python3 scripts/changelog.py assemble <version> <date>`, which adds every
fragment, in task-id order, to that version's section of `CHANGELOG.md` and
deletes the fragments. This `README.md` is never assembled.
