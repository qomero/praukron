- Two-way sync for each PPMP copy. `praukron notion push` mirrors the
  chronicle by stable row ID and Source Revision, fills the hub's KPI and
  "Việc của bạn" callouts in place, and updates the project's row in both the
  copy's Project Portfolio and the shared board (ADR-090). Operations & Sync
  records sync status; Owner Response History mirrors recorded responses.
- Answer in the Assumption Review Queue or Agent Instructions Outbox, then
  tick Submit. `praukron notion pull` records accepted submissions as owner
  responses, logs each submission in the Action Ledger, and writes a receipt
  back to the row (APPLIED, CONFLICT or REJECTED). Only Notion users listed in
  an ADR's `Notion owners:` are accepted; listed owners may submit VERIFY for
  an `Authority: owner` task (ADR-088).
  `praukron notion sync` runs pull then push once. `praukron notion watch install`
  schedules both with Windows Task Scheduler, or prints crontab lines to add
  on other systems.
