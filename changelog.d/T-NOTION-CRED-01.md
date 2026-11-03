- Praukron v3 begins Notion management (ADR-087). `praukron notion doctor` says
  where the Notion key was found (never its value) and whether it works;
  `praukron notion env [--user]` writes the key file. The key is read from
  `NOTION_TOKEN`, `.praukron/notion.env`, the project's `.env.local` or `.env`,
  then `~/.praukron/notion.env`, so one key serves every project in a
  workspace. The installer leaves a blank, Git-ignored `.praukron/notion.env`
  and never overwrites it.
