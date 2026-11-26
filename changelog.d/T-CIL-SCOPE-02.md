- A task's changed Python symbols are captured with its files (SPEC §2.9, ADR-092). The capture
  a `DONE` write makes, and `praukron scope capture`, now also parses each changed Python file
  with the standard library's `ast`, the old text at `Base` and the new text at `Head` or in the
  working tree, and records every function, async function, class and method whose range holds
  a changed line as an observed reference (`Source: static_analysis`, `Kind`, qualified
  `Symbol`, `Lines` as a hint): `creates`, `modifies`, or `deletes` with `Lifecycle: deleted`
  and `Revision: <Base>`, where its name is true. Source is parsed, never run. Another language,
  a file that does not parse, a file over 512 KiB, a file past the capture's 4 MiB source budget or its cap of 200 symbol records, or a capture with more than 200 changed Python
  files keeps its file reference, whose evidence says its symbols are unresolved, and the reply
  lists it in `capture.symbolsUnresolved`; no symbol is recorded without a real name, and a
  symbol already recorded is not recorded again. The reply's `capture` also gives `files` and
  `symbols` (T-CIL-SCOPE-02).
