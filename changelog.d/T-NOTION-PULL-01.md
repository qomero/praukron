- Two-way sync. `praukron notion push` mirrors tasks, phases, gates,
  milestones, criteria, blockers, assumptions, ADRs and technical debt by the
  stable ID in each row's title and skips rows whose Source Revision is
  unchanged; one Operations & Sync row and the Live Status page show what it
  last carried. `praukron notion pull` applies what the owner submitted in the
  Assumption Review Queue and the Agent Instructions Outbox as verbatim
  responses, logs every submission in the Action Ledger, and links the row to
  it (QUEUED → LOCAL_RECORDED → APPLIED, or CONFLICT / REJECTED). Only Notion
  users listed in an ADR's `Notion owners:` are accepted, and a listed owner
  may VERIFY from Notion (ADR-088). `praukron notion watch install` runs both
  on a schedule (Task Scheduler on Windows, crontab lines elsewhere).
