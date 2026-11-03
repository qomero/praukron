- Structured writes (journal append, task add/update, criterion set, batch apply, respond) now work when
  `.praukron/chronicle` is a directory symlink or Windows junction to a folder outside the repository,
  instead of failing with `write-failed` (T-WRITE-LINK-01).
