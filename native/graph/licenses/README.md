# Licence texts the crate archives omit

Two grammar crates declare `license = "MIT"` in their metadata but publish no
licence file in their crates.io archive. Their licence text is taken from the
upstream repository at the tag the crate version was released from, and
`tools/notices.py` uses it when the archive ships none.

| File | Upstream | Tag | Commit |
| --- | --- | --- | --- |
| `tree-sitter-cpp-0.23.4.LICENSE` | https://github.com/tree-sitter/tree-sitter-cpp | `v0.23.4` | `f41e1a044c8a84ea9fa8577fdd2eab92ec96de02` |
| `tree-sitter-typescript-0.23.2.LICENSE` | https://github.com/tree-sitter/tree-sitter-typescript | `v0.23.2` | `f975a621f4e7f532fe322e13c4f79495e0a7b2e7` |
