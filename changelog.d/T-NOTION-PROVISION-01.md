- `praukron notion setup <copy-url>` binds a project to its own PPMP template
  copy, a full project-control workspace with its sample rows removed
  (ADR-090). Duplicate it once per project inside the page that holds your
  projects. Setup finds the copy's 13 databases by title, adds only missing
  databases, properties and relations, and refuses a page whose title ends
  with `TEMPLATE`. It finds or creates one shared Project Portfolio directly
  under the parent page so every linked project appears on the same board.
- Add the repository tool `python scripts/notion_template.py clean <template-page-url>`
  to trash every row in the template's nested databases while preserving its
  layout and schema. It refuses pages whose titles do not end with `TEMPLATE`;
  `--dry-run` reports per-database counts without writing.
