- `praukron notion notify on|off|status [--kinds …]` sends the owner's phone a
  notification through Notion: each push adds one comment that @mentions every
  user in the ADR's `Notion owners:` on the row concerned (or the hub) when a task
  becomes `DONE`, an assumption opens, an owner task becomes ready, a submission
  ends `CONFLICT` or `REJECTED`, or a phase or gate changes state. Off by default;
  the first push after `notify on` records the state and sends nothing; each event
  is commented once, across restarts, through `.praukron/notion-events.json`
  (ignored by Git). A failed comment never fails the push; it stays pending and
  `notion doctor` reports it. The integration needs the Insert comments capability.
