- AGENTS.md payload examples now show closing a task with evidence (`task update` sets
  `DONE` and `evidence`) and a whole lifecycle in one `batch apply`: claim, pass the criteria,
  close, and append a journal entry. A revision read once serves every operation on that
  record in the batch, even after an earlier operation changed it (T-WRITE-GUIDE-02).
