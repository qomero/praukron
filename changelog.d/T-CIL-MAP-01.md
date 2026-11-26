- Task–code references (SPEC §2.9) are read from `.praukron/chronicle/CODE.md`, validated, and
  compiled into `project.json` as `references` and `artifacts`. Each task's `Files:` and
  `Symbols:` anchors are also listed as planned references with manual provenance and
  `legacy: true`; anchors are never rewritten and every existing field compiles unchanged. A
  chronicle without `CODE.md` stays valid, and `install.sh` installs the empty `CODE.md`
  template when it is absent, never replacing one (T-CIL-MAP-01).
