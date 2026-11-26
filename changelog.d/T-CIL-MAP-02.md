- Task–code references are written through structured commands (SPEC §2.6, §2.9).
  `praukron ref add` appends one reference to `CODE.md`, creating the file from its template
  header when absent, and assigns its `CR-<task>-<nn>` identifier. `praukron ref set` changes a
  reference's verification, lifecycle or evidence in place under `expectRevision`, or appends a
  record that withdraws or replaces it while the earlier record stays byte for byte. Both are
  `batch apply` operations, and every record they write carries `Source` and
  `Recorded: <date> · <who>`; an in-place `set` keeps both and writes
  `Updated: <date> · <who>`. A planned `creates`, `modifies` or `deletes` is written
  `proposed` unless `authorizedBy` names an owner response or an accepted ADR; no payload field
  sets `Authorization` directly. `praukron ref history <CR-id>` prints a reference and every
  record it replaces, oldest first, unchanged (T-CIL-MAP-02).
