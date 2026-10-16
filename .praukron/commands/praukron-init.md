# /praukron-init

Create `.praukron/chronicle/` with `README.md`, `THESIS.md`, `PHASES.md`, `MODULES.md`,
`TASKS.md`, `ACCEPTANCE.md`,
`ADR/`, `INTENT.md`, `HANDOFF.md`, `JOURNAL.md`, `ASSUMPTIONS.md`,
`RESPONSES.md`, and `COORDINATION.md` if absent, and `.praukron/compiled/`
for compiled views.

If the chronicle already contains tasks, decisions, intent, or journal entries,
preserve them all and follow `.praukron/commands/praukron-resume.md`. Neither `new` nor
`existing` resets a chronicle. Add only missing files; never replace populated
records with empty templates. The entry modes below apply only to a fresh,
empty chronicle.

For a new repository, find the product thesis first: take it from the product
specification, or elicit it from the developer, and author it in `THESIS.md`.
Then work in the order thesis → phases → modules → tasks: create the first
phase, the modules that deliver it (or `P-NONE` modules for phase-independent
work), the initial tasks, each naming one module, with their dependencies, and
an acceptance contract for each task. Record the initial
product choices as ADRs.

For an existing repository, author the product thesis with the owner if one
is stated, and otherwise leave `THESIS.md` for the owner; leave modules, tasks,
and history empty. Record only work and
decisions made from this session onward. Do not inspect the repository to invent
a historical chronicle. If the owner wants the decisions the code already
depends on recorded, they can ask for `.praukron/commands/praukron-baseline.md`
separately; initialization never runs it.

In either mode, offer the owner one optional step: the assumption policy
(`docs/SPEC.md` §2.4). Ask which of these groups an agent may assume
provisionally and which must stop for the owner: `permission` (who may see,
create, change, approve, or delete what), `security` (authentication, secrets
and credentials, exposing a service or data beyond the project),
`external-disclosure` (sending project data to an outside service), `legal`
(legal, compliance, contractual, or pricing commitments), and
`source-of-truth` (which system or record owns a fact). Ask also whether an
agent that stops should notify the owner through its host. Say that
destructive or irreversible actions, and contradicting an accepted ADR or a
frozen criterion, always stop whatever the answer. Record the answer as an
`ACCEPTED` ADR naming the owner as its authority, with the fields
`- Policy: assumptions`, `- Reserved: <groups, comma-separated, or none>`,
and `- Notify: host` or `- Notify: none`. If the owner skips the step, record
nothing: every group is reserved and `Notify: host` applies by default. Ask it
once; the policy changes later only by superseding that ADR.
