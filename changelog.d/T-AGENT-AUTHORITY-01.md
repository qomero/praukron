- Agent authority: one setting, `praukron authority`, lists every capability an
  agent can be given on the owner's authority, its state, who set it and when
  (`--json`; also on the dashboard's Review tab). Grants live in a new optional
  chronicle file, `AUTHORITY.md`, one `## AG-<nn>` record each: capability,
  scope (`project` or task, module and phase ids, always stated), delegate,
  granting `gh` login (named in `Owners:`), date, end (`Until: none` for no
  end), `Via: cli|dashboard`, and its revocation. `authority grant` and
  `authority revoke` run only for the owner in person, at an interactive
  terminal or from `praukron dashboard --serve`; the existing assumption policy
  is listed unchanged. Task packets carry `capabilities`. A chronicle without
  the file grants nothing and keeps validating; the installer adds the template.
