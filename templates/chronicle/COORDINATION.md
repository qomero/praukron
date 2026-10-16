# Coordination

How this project runs its agents, kept here so that it survives the loss of
any agent session (ADR-075). A relaunched agent resumes from
`praukron context <task>`, which carries the task's brief and the conventions
it rests on; nothing a coordinator needs lives only in a session.

Two kinds of record. A convention is a standing rule: what every worker or
reviewer follows, which agent builds or reviews, how a session is started, how
review rounds run. A brief adds to one task what its contract does not hold:
branch, base, pull-request title, who builds and reviews it, where its review
round stands, and notes. A brief never restates, narrows or widens a
criterion; where it and a contract, invariant or ADR differ, they win.

The coordinator writes these records: edit only the section you change, then
run `praukron validate`. Close a brief when its task is merged or abandoned.
Never record a secret: name where it is found, never its value.

Format:

    ## CV-01: Worker rules
    - Status: ACTIVE
    - Roles: builder

    Run the full test suite before opening a pull request. Add a regression
    test for every behaviour you add. Open the pull request; do not merge.

    ## BR-01: Payment terms import
    - Task: T-FIN-01
    - Status: ACTIVE
    - Conventions: CV-01
    - Branch: fin-terms
    - Base: main
    - Reviewers: two, independent
    - Review: round 1, waiting

    Keep the importer behind the existing feature flag until review passes.

Fields are the block directly under the heading; everything after the first
blank line is the record's text. A convention's `Status` is `ACTIVE` or
`RETIRED`, and its optional `Roles` any of `builder`, `reviewer`,
`coordinator` (absent means every role). A brief's `Status` is `ACTIVE` or
`CLOSED`; each task has at most one `ACTIVE` brief. Further one-line fields
are the project's own.

No conventions or briefs recorded yet.
