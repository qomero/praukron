# Agent authority

What the owner lets agents do on the owner's authority. Recorded only by the
owner in person, with `praukron authority grant` and `praukron authority
revoke` at a terminal or from `praukron dashboard --serve`; an agent never
writes, edits or widens a grant. A revocation is added to its grant, which
otherwise stays as written. `praukron authority` lists every capability, its
state, who set it and when.

Format:

    ## AG-01: verify for reviewer-agent
    - Capability: verify
    - Scope: M-CORE, T-EXPORT-01
    - Delegate: reviewer-agent
    - Granted by: github-login
    - Date: 2026-11-27
    - Until: 2026-12-31
    - Via: cli
    - Revoked: 2026-12-05
    - Revoked by: github-login
    - Revoked via: dashboard

`Scope` is `project` for the whole project, or task, module and phase ids; it
is always stated. `Until: none` means the grant holds until it is revoked.
`Granted by` is the active `gh` login, which must be named in an ACCEPTED
ADR's `Owners:`. `Via` is `cli` or `dashboard`, nothing else. The revocation
lines appear only on a revoked grant. Grants are never archived.

Under a grant of `verify`, the named agent records `praukron respond <task>
VERIFY --delegate "<agent>"` for an `Authority: owner` task it did not build,
and sets that task's `Validation: DELEGATE_VERIFIED`. The assumption policy is
not granted here: it is the ACCEPTED ADR with `Policy: assumptions`.

No grants recorded yet.
