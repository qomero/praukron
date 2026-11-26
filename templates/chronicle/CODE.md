# Code references

Which files and symbols each task relates to, one reference per record
(SPEC §2.9). A reference is authority like every other record: a graph
provider never writes one, and removing a provider changes none. A task needs
no references to be valid, and its `Files:` and `Symbols:` anchors keep
working beside them.

A reference is never deleted. Record a replacement with `Replaces:`, or mark
it `Withdrawn:` with a date and a reason; the earlier record stays, so the
history of a file survives its rename or deletion. Never record a secret.

Format:

    ## CR-T-FIN-01-01: Import payment terms
    - Task: T-FIN-01
    - Path: src/fin/terms.py
    - Symbol: Terms.load
    - Kind: method
    - Relation: modifies
    - Scope: planned
    - Verification: unverified
    - Authorization: proposed
    - Source: agent
    - Recorded: 2026-01-15 · agent/builder

The identifier is `CR-`, the task's identifier, and a two-digit sequence
within that task. `Path` is relative to the repository root with `/`
separators; the file need not exist yet. `Symbol` is the qualified name inside
that file, and needs `Kind`. `Relation` is `creates`, `modifies`, `deletes`,
`reads`, `depends_on`, `verifies`, `documents` or `affects`; `Scope` is
`planned` or `observed`. `Verification` (whether the reference matches the
code) and `Authorization` (whether the task may make the change) are separate.
`praukron validate` lists every field and refuses values outside them.

No references recorded yet.
