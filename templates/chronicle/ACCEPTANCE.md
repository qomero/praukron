# Acceptance

Completion-contract authority. A task is not DONE because a builder says it is
done. A task is DONE when its frozen contract here has sufficient evidence.

Every criterion answers one question:

> What must be demonstrated before this work is allowed to become DONE?

`TASKS.md` records which contract governs a task. This file records what the
contract requires. Where the two disagree, this file wins.

## Criterion identity

Identifiers are stable and never reused:

```text
AC-<task>-<nn>      a single criterion          AC-T-P2-02-03
AC-<task>           the task's whole contract   AC-T-P2-02
AC-GLOBAL-<name>    an inherited contract       AC-GLOBAL-DETERMINISM
```

Renumbering a criterion breaks its evidence. Retire a criterion by marking it
`WITHDRAWN` through an Acceptance Change Request; do not delete it.

## Criterion structure

Prefer observable behaviour over implementation description:

```text
Given <precondition>
When  <action>
Then  <observable outcome>
```

Each criterion carries one evidence class and one state.

## Evidence classes

| Class | Satisfied by |
| --- | --- |
| `TEST` | An automated test that fails when the behaviour regresses. |
| `MUTATION` | A deliberate break that the test suite catches. |
| `INSPECTION` | A recorded reading of a document, diff, or generated artifact. |
| `RUNTIME` | Observed behaviour of the running system. |
| `MANUAL` | A named human performing and reporting a procedure. |

`MANUAL` evidence is the only class that may support `HUMAN_VERIFIED`
validation, and only a named human may record it.

## Criterion states

```text
PASS      evidence exists and holds
FAIL      evidence exists and contradicts the criterion
NOT_RUN   no evidence yet
```

A task may not be `DONE` while any mandatory criterion is `FAIL` or `NOT_RUN`.

## Inherited contracts

Some requirements apply to a class of work rather than one task. Record each one
once as an `AC-GLOBAL-<name>` contract. Inheriting tasks name it and must not
restate it.

None yet.

## Reviewer findings

A finding blocks completion only when classified as:

```text
ACCEPTANCE_FAILURE
INVARIANT_VIOLATION
REGRESSION
MISSING_EVIDENCE
```

The following are recorded but do not block `DONE`:

```text
RISK
MAINTAINABILITY
ARCHITECTURE_PREFERENCE
STYLE
FUTURE_IMPROVEMENT
```

Reviewer preference does not create a new acceptance criterion. A reviewer who
believes the contract is wrong raises an Acceptance Change Request.

## Frozen contracts

A contract freezes when its task moves to `WIP`. From that point neither builder
nor reviewer may reinterpret it. A required change becomes an Acceptance Change
Request recording:

```text
Task
Criterion
Current contract
Proposed contract
Reason
Impact
Decision
```

`praukron acr add` records a request in this shape with `Decision: PENDING`
and never changes the criterion. Until a request is explicitly accepted, the
existing criterion remains authoritative. Accepted requests are appended to the log below and the criterion
is edited in place with its request identifier noted. The task's `Frozen:` fingerprint stays as it is:
`validate` accepts the new text because an accepted request leads from it back to the frozen one, and
reports any other change.

## Acceptance Change Requests

None.

---

# Contracts

No contracts yet. A task gains one when it is created.
