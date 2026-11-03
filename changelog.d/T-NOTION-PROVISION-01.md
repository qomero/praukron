- `praukron notion setup <page-url>` binds a project to its own copy of the
  PPMP template (Praukron Project Management Protocol, ADR-089): duplicate the
  template in Notion once per project, then run setup on the copy. It finds
  the nine PPMP databases below the page by title (Task Registry, Phases &
  Gates, Acceptance & Evidence, Blockers & Dependencies, Assumption Review
  Queue, Agent Instructions Outbox, ADR & Technical Debt, Action Ledger,
  Operations & Sync), creates any that are missing, only ever adds missing
  properties and relations, and refuses the unduplicated template. It adds a
  Live Status page under the copy and names the copy after the project.
