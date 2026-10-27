- Writes stay fast on large chronicles. Structured writes, `respond`, `archive`
  and `compile` refresh what agents read (`INDEX.md`, `project.json`, the
  Markdown and Mermaid views) and no longer re-render the dashboard; an
  existing page gets a visible "out of date" notice instead, in its language
  (ADR-086). `praukron dashboard` renders it, and `--serve` renders it per
  request as before. Quadratic scans in journal-order checking, section
  location and orphan-contract validation are now linear, and task lookup is
  indexed. On a synthetic 100k-line chronicle one write takes 2.2 s instead of
  21 s, and `compile` 1.0 s instead of 4.8 s.
- `status` folds more than three COMPLETE phases into one line, so its output no
  longer grows with the project's history.
