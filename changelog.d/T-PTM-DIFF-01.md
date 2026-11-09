- `praukron history diff <earlier> <later>` reports the
  `OBSERVED_STATE_TRANSITIONS` of authoritative records (tasks, criteria, ADRs,
  assumptions, phases, gates, ACRs) between two revisions, by stable ID, each
  pinned to both revisions and its `file#anchor`, with the interval it happened
  in and how many commits that spans. A move into `archive/` is `RELOCATED`; an
  ID removed and later given to different work is `REUSED`, never one record
  that changed; revisions on different lines of history are `NOT_ANCESTRAL`;
  an `UNSUPPORTED` side or a gap in the chronicle makes the answer
  `INSUFFICIENT_EVIDENCE`. ADRs, responses, journal entries and ACRs that name
  a record by its ID are attached as linked evidence, never by timing.
  `praukron history lineage <ADR>` follows `Supersedes:` both ways
  (T-PTM-DIFF-01, ADR-093).
