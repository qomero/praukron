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
- Each submission is one request: its submitter and text are kept in
  `.praukron/notion-requests.json` (ignored by Git) before anything is written
  back, so an interrupted pull finishes it once, with one response, one ledger
  row and one receipt. A handled row is unticked; correcting a rejected row or
  changing an applied one and ticking Submit again sends a new request. The
  owner's Feedback and Instruction Text are recorded exactly as written; the
  intent and priority go in the response's `Via:` line.
- An instruction or approval is checked against its target's revision, as an
  assumption is: one about a task that has changed since is a CONFLICT and
  records nothing. Push stamps that revision on Outbox rows not yet submitted.
- Push leaves a submitted assumption alone until pull has handled it, repairs
  mirrored values edited in Notion, and fills the hub on a new copy before its
  rows. In the hub it rewrites only the lines it wrote, in place; views, pages,
  links and other blocks under a callout stay as they are.
- `praukron notion doctor` and every sync command refuse a key containing
  spaces, line breaks or control characters before sending anything, naming
  only where the key came from.
  `praukron notion sync` runs pull then push once. `praukron notion watch install`
  schedules both with Windows Task Scheduler, or prints crontab lines to add
  on other systems.
