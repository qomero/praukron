#!/usr/bin/env python3
"""Make the dashboard images in docs/assets/ from a synthetic chronicle.

Nothing here comes from a real project. The chronicle below describes an
invented invoicing tool, Tally, with three phases, six modules, owner tasks,
assumptions, decisions, a delegated verification and code references. The
script writes it into a temporary directory, renders the static dashboard with
this checkout's own `.praukron/praukron`, and captures each image with headless
Chrome. It needs no network: the page loads nothing from outside, and Chrome
runs with a fresh profile and networking turned off.

    python3 scripts/screenshots.py                 # write every image to docs/assets/
    python3 scripts/screenshots.py --out /tmp/shots
    python3 scripts/screenshots.py --only work,3d   # some images only
    python3 scripts/screenshots.py --chronicle /tmp/tally   # write the chronicle and stop

Chrome is looked for at `PRAUKRON_CHROME`, then at the usual places on macOS
and Linux. Captures use its DevTools pipe, real animation frames and a 2x
device scale; no browser driver or additional Python package is needed.
Every image uses the page's own Dark theme setting.
Look at every image before committing it.

Asset notes for documentation (all show the invented Tally project):
- dashboard-state.png shows Work in lanes with all three phases, operations,
  and the waiting strip containing three now and two upcoming items.
- dashboard-force.png shows the task graph clustered by module in force view,
  with decision and assumption nodes hidden using the toolbar controls.
- dashboard-3d.png shows the phase floors and dependency graph in 3D with the
  turn paused and the scene enlarged through its zoom control.
- dashboard-contract.png shows the selected credit-note task in the side
  panel, with dependencies and PASS, FAIL and NOT_RUN criteria.
- dashboard-next.png shows the waiting strip with the owner-held payment
  provider choice selected and its downstream chain highlighted.
- dashboard-review.png shows the decision inbox with one actionable owner
  decision and two upcoming decisions waiting on prerequisites.
- dashboard-operations.png shows Audit with evidence and governance expanded,
  including green and red gates and the validation-strength table.
- dashboard-audit.png shows Audit's operational trace with active operations,
  a failed test command, its retry and a dependency blocker.
- dashboard-registry.png shows the task registry searched for invoice, with
  eight matching tasks and their file and symbol counts.
- dashboard-views.png shows the native critical-path diagram and its
  expandable Mermaid source under Work's Views inspection tab.
- dashboard-code.png shows a widened side panel with planned and observed
  code references, provenance, authorization, verification and revisions.

No assets are deleted, and every requested subject is shown; live response
submission is outside these static-page captures. Reproduce all images with
``python3 scripts/screenshots.py``; ``--chronicle /tmp/tally`` preserves the
synthetic fixture for inspection without loading any real project's records.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "chronicle"
TOOL = ROOT / ".praukron" / "praukron"
OWNER = "studio-owner"
REVIEWER = "reviewer-agent"

THESIS = """# Product thesis

- Statement: Small studios send correct invoices and get paid without keeping a spreadsheet.
- Source:
- Language: en
"""

PHASES = """Project: Tally

# Phases

## P1 — Foundations

Outcome:
Amounts, customers and accounts are stored once and read the same way everywhere.

Entry:
- The thesis is agreed.

Exit:
- The ledger and accounts are in use, and Gate A is green.

Exit authority:
T-CORE-03

Status:
COMPLETE

---

## P2 — Billing

Outcome:
A studio issues invoices, credit notes and refunds that add up to the cent.

Entry:
- P1 is complete.

Exit:
- Invoices and payments reconcile, and the owner approves the release.

Exit authority:
T-BILL-06

Status:
ACTIVE

---

## P3 — Reports

Outcome:
A studio sees what it is owed and what it earned each month.

Entry:
- P2 is complete.

Exit:
- Monthly reports match the ledger.

Exit authority:
T-REP-03

Status:
PLANNED

---

# Gates

## Gate A — Amounts in whole cents

Every stored amount is an integer number of cents.

Blocks: P1 exit
Verified by: `AC-T-CORE-02-01`
Status: GREEN

## Gate B — Invoices reconcile

The sum of invoices, credit notes and payments matches the ledger.

Blocks: P2 exit
Verified by: `AC-T-BILL-05-01`
Status: RED

---

# Milestones

- `M-FIRST-INVOICE` — First invoice sent. Reached when T-BILL-01 is `DONE`.
- `M-REFUNDS` — Refunds paid out. Reached when T-PAY-03 is `DONE`.
"""

MODULES = """# Modules

## M-CORE — Ledger
- Phase: P1
- Outcome: One ledger of amounts in whole cents.

## M-ACCT — Customers
- Phase: P1
- Outcome: Customers and their billing details.

## M-BILL — Invoices
- Phase: P2
- Outcome: Invoices and credit notes.

## M-PAY — Payments
- Phase: P2
- Outcome: Card payments and refunds.

## M-REP — Reports
- Phase: P3
- Outcome: Monthly statements.

## M-OPS — Upkeep
- Phase: P-NONE
- Outcome: The build and the release run on their own.
"""

# id, title, status, module, dependencies, validation, owner task, governing decisions,
# criterion states, the builder.
TASKS = [
    ("T-CORE-01", "Store amounts as cents", "DONE", "M-CORE", [], "AI_REVIEWED", False, ["ADR-002"], ["PASS"], "agent/builder"),
    ("T-CORE-02", "Post entries to the ledger", "DONE", "M-CORE", ["T-CORE-01"], "AI_REVIEWED", False, ["ADR-002"], ["PASS", "PASS"], "agent/builder"),
    ("T-ACCT-01", "Record customers", "DONE", "M-ACCT", ["T-CORE-01"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-ACCT-02", "Keep billing addresses", "DONE", "M-ACCT", ["T-ACCT-01"], "UNTESTED", False, [], ["PASS"], "agent/builder"),
    ("T-CORE-03", "Approve the ledger", "DONE", "M-CORE", ["T-CORE-02", "T-ACCT-02"], "HUMAN_VERIFIED", True, [], ["PASS"], OWNER),
    ("T-BILL-01", "Apply a discount to an invoice", "DONE", "M-BILL", ["T-CORE-03"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-BILL-02", "Round totals to cents", "DONE", "M-BILL", ["T-BILL-01"], "AI_REVIEWED", False, ["ADR-003"], ["PASS"], "agent/builder"),
    ("T-BILL-03", "Issue credit notes", "WIP", "M-BILL", ["T-BILL-02"], "UNTESTED", False, ["ADR-003"], ["PASS", "FAIL", "NOT_RUN"], "agent/builder"),
    ("T-BILL-04", "Number invoices without gaps", "TODO", "M-BILL", ["T-BILL-03"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-PAY-01", "Take card payments", "DONE", "M-PAY", ["T-CORE-03"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-PAY-02", "Choose the payment provider", "TODO", "M-PAY", ["T-PAY-01"], "UNTESTED", True, [], ["NOT_RUN"], OWNER),
    ("T-PAY-03", "Pay refunds to cards", "TODO", "M-PAY", ["T-PAY-02"], "UNTESTED", False, ["ADR-005"], ["NOT_RUN"], "agent/builder"),
    ("T-PAY-04", "Check the refund wording", "DONE", "M-PAY", ["T-PAY-01"], "DELEGATE_VERIFIED", True, [], ["PASS"], "agent/builder"),
    ("T-PAY-05", "Import bank statements", "BLOCKED", "M-PAY", ["T-PAY-01"], "UNTESTED", False, ["ADR-005"], ["FAIL"], "agent/builder"),
    ("T-BILL-05", "Reconcile invoices and payments", "TODO", "M-BILL", ["T-BILL-04", "T-PAY-03"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-BILL-06", "Approve billing for release", "TODO", "M-BILL", ["T-BILL-05"], "UNTESTED", True, [], ["NOT_RUN"], OWNER),
    ("T-REP-01", "Monthly statement", "TODO", "M-REP", ["T-BILL-02"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-REP-02", "Amounts owed by customer", "TODO", "M-REP", ["T-REP-01", "T-ACCT-02"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-REP-03", "Approve the reports", "TODO", "M-REP", ["T-REP-02"], "UNTESTED", True, [], ["NOT_RUN"], OWNER),
    ("T-OPS-01", "Run the tests on every pull request", "DONE", "M-OPS", [], "UNTESTED", False, [], ["PASS"], "agent/ops"),
    ("T-OPS-02", "Publish a release from a tag", "TODO", "M-OPS", ["T-OPS-01"], "UNTESTED", False, [], ["NOT_RUN"], "agent/ops"),
    ("T-CORE-04", "Index ledger entries by date", "DONE", "M-CORE", ["T-CORE-01"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-ACCT-03", "Validate customer tax numbers", "DONE", "M-ACCT", ["T-ACCT-01"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-ACCT-04", "Archive inactive customers", "DONE", "M-ACCT", ["T-ACCT-02"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-BILL-07", "Preview an invoice before sending", "DONE", "M-BILL", ["T-BILL-01"], "AI_REVIEWED", False, [], ["PASS"], "agent/builder"),
    ("T-BILL-08", "Email the invoice receipt", "TODO", "M-BILL", ["T-BILL-07"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-PAY-06", "Match a payment to an invoice", "WIP", "M-PAY", ["T-PAY-01", "T-BILL-02"], "UNTESTED", False, [], ["NOT_RUN"], "agent/payments"),
    ("T-REP-04", "Export statements as CSV", "TODO", "M-REP", ["T-REP-01"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-REP-05", "Summarize tax by quarter", "TODO", "M-REP", ["T-REP-01"], "UNTESTED", False, [], ["NOT_RUN"], "agent/builder"),
    ("T-OPS-03", "Check the release archive", "TODO", "M-OPS", ["T-OPS-02"], "UNTESTED", False, [], ["NOT_RUN"], "agent/ops"),
]

CRITERIA = {
    "T-BILL-03": [
        "Given a sent invoice, When a credit note is issued, Then the invoice's balance falls by its amount.",
        "Given a credit note larger than the balance, When it is issued, Then it is refused.",
        "Given a credit note, When it is printed, Then it names the invoice it corrects.",
    ],
    "T-CORE-02": [
        "Given an entry, When it is posted, Then the ledger balances.",
        "Given a posted entry, When it is read back, Then its amount is unchanged.",
    ],
}

EVIDENCE = {
    "T-BILL-03": ["tests/test_credit.py::test_balance", "tests/test_credit.py::test_refuse_over fails: the note is accepted", None],
    "T-PAY-05": ["The bank's test file has no stable column names."],
}

ASSUMPTIONS = """# Assumptions

## A-1: Refunds settle within five working days
- Status: OPEN
- Tasks: T-PAY-03
- Impact: HIGH
- Recorded: 2026-11-10 · agent/builder
- Assumption: A refund reaches the customer's card within five working days.
- Basis: The provider's documentation says so for cards issued in the EU.
- Applied in: T-PAY-03
- Responses: none
- Reconciled by: none

## A-2: Statements are monthly
- Status: OPEN
- Tasks: T-REP-01
- Impact: LOW
- Recorded: 2026-11-12 · agent/builder
- Assumption: Studios want one statement a month, not one a week.
- Basis: Every studio interviewed closes its books monthly.
- Applied in: T-REP-01
- Responses: none
- Reconciled by: none

## A-3: Customers keep one currency
- Status: OPEN
- Tasks: T-REP-02
- Impact: LOW
- Recorded: 2026-11-14 · agent/builder
- Assumption: A customer is billed in one currency for its whole history.
- Basis: No studio asked for more.
- Applied in: T-REP-02
- Responses: R-003
- Reconciled by: none
"""

ADRS = {
    "ADR-001": f"""# ADR-001: The owner
- Date: 2026-10-01
- Status: ACCEPTED
- Authority: owner
- Owners: {OWNER}
- Decision: The owner is the GitHub user {OWNER}.
""",
    "ADR-002": """# ADR-002: Store amounts as integer cents
- Date: 2026-10-03
- Status: ACCEPTED
- Supersedes: none
- Affects: T-CORE-01, T-CORE-02
- Context: Floating-point amounts drift by fractions of a cent.
- Decision: Every amount is stored and passed as an integer number of cents.
""",
    "ADR-003": """# ADR-003: Round invoice totals half up to whole cents
- Date: 2026-11-02
- Status: ACCEPTED
- Supersedes: none
- Affects: T-BILL-02, T-BILL-03
- Context: A discount can leave half a cent.
- Decision: Round half up, once, on the invoice total.
""",
    "ADR-004": """# ADR-004: Synchronize with the bank
- Date: 2026-10-20
- Status: SUPERSEDED
- Supersedes: none
- Affects: T-PAY-05
- Context: Matching payments by hand is slow.
- Decision: Read payments from the bank's interface.
""",
    "ADR-005": """# ADR-005: Import bank statements as files
- Date: 2026-11-08
- Status: ACCEPTED
- Supersedes: ADR-004
- Affects: T-PAY-03, T-PAY-05
- Context: The banks the first studios use offer no interface.
- Decision: Import the statement files the bank exports; revisit when an interface exists.
""",
}

RESPONSES = f"""
## R-001: T-CORE-03 VERIFY
- By: {OWNER}
- Date: 2026-10-30
- Via: dashboard

> The ledger matches the old spreadsheet for October.

## R-002: T-PAY-04 VERIFY
- By: {OWNER}
- Date: 2026-11-20
- Via: delegate (cli)
- Delegate: {REVIEWER}
- On authority of: {OWNER}
- Grant: AG-01

> The refund wording matches the provider's terms.

## R-003: A-3 CONFIRM
- By: {OWNER}
- Date: 2026-11-21
- Via: dashboard

> One currency per customer is right for now.
"""

GRANT = f"""
## AG-01: verify for {REVIEWER}
- Capability: verify
- Scope: T-PAY-04
- Delegate: {REVIEWER}
- Granted by: {OWNER}
- Date: 2026-11-18
- Until: 2026-12-31
- Via: cli
"""

JOURNAL = """# Journal

## 2026-11-24 — Credit notes half done

T-BILL-03 issues credit notes; the refusal of an oversized note still fails. T-PAY-05 is blocked:
the bank's test file has no stable column names.

## 2026-11-18 — Payments taken

T-PAY-01 takes card payments. T-PAY-02 waits for the owner to choose the provider.

## 2026-11-02 — Billing started

T-BILL-01 applies discounts; ADR-003 settles the rounding.

## 2026-10-30 — Foundations approved

The owner verified T-CORE-03. P1 is complete.
"""

TRACE = """
## EV-001: Run the test suite
- Time: 2026-11-24T09:12:00Z
- Type: command
- Task: T-BILL-03
- Agent: agent/builder
- Tool: shell
- Target: tests/
- Outcome: failure
- Error: 1 test failed: test_refuse_over

## EV-002: Run the test suite again
- Time: 2026-11-24T09:40:00Z
- Type: retry
- Task: T-BILL-03
- Agent: agent/builder
- Tool: shell
- Target: tests/
- Outcome: failure
- Retry of: EV-001
"""

DEBT = """
## TD-001: Statement columns mapped by position
- Status: ACCEPTED
- Introduced by: T-PAY-05, ADR-005
- Areas: payments, import
- Debt: Statement columns are read by position, not by name.
- Reason: The first bank's files have no header row.
- Interest: Every new bank needs its own column map.
- Trigger: Before a second bank is supported.
- Trigger state: NOT_REACHED
- Exit condition: Columns are matched by name with a fallback map.
- Evidence: src/pay/statement.py
- Linked tasks: T-PAY-05
- Resolution:
"""


def reference(ref_id: str, task: str, path: str, **extra: str) -> str:
    fields = {"Task": task, "Path": path, "Relation": "modifies", "Scope": "observed",
              "Verification": "verified", "Authorization": "not_applicable", "Source": "git_diff",
              "Recorded": "2026-11-04 · agent/builder"}
    fields.update(extra)
    return "\n".join([f"## {ref_id}: {ref_id.lower()}"] + [f"- {k}: {v}" for k, v in fields.items()]) + "\n"


CODE = "# Code references\n\n" + "\n".join([
    reference("CR-T-BILL-01-01", "T-BILL-01", "src/billing/invoice.py", Symbol="Invoice.total", Kind="method",
              Scope="planned", Authorization="proposed", Source="agent", Recorded="2026-11-02 · agent/builder"),
    reference("CR-T-BILL-01-02", "T-BILL-01", "src/billing/invoice.py", Revision="4d3db31"),
    reference("CR-T-BILL-01-03", "T-BILL-01", "src/billing/invoice.py", Symbol="Invoice.apply_discount",
              Kind="method", Relation="creates", Source="static_analysis", Revision="4d3db31"),
    reference("CR-T-BILL-01-04", "T-BILL-01", "src/billing/invoice.py", Symbol="Invoice.total", Kind="method",
              Source="static_analysis", Revision="4d3db31"),
    reference("CR-T-BILL-01-05", "T-BILL-01", "tests/test_invoice.py", Relation="creates", Revision="4d3db31"),
    reference("CR-T-BILL-01-06", "T-BILL-01", "web/app.js", Revision="4d3db31"),
    reference("CR-T-BILL-01-07", "T-BILL-01", "web/app.js", Symbol="renderTotal", Kind="function",
              Source="agent", Recorded="2026-11-05 · agent/builder"),
    reference("CR-T-BILL-02-01", "T-BILL-02", "src/billing/invoice.py", Revision="9a41c07"),
    reference("CR-T-BILL-02-02", "T-BILL-02", "src/billing/invoice.py", Symbol="format_total", Kind="function",
              Source="static_analysis", Revision="9a41c07"),
    reference("CR-T-BILL-03-01", "T-BILL-03", "src/billing/credit.py", Relation="creates", Scope="planned",
              Verification="unverified", Authorization="proposed", Source="agent"),
    reference("CR-T-BILL-03-02", "T-BILL-03", "src/billing/invoice.py", Symbol="Invoice.balance", Kind="method",
              Scope="planned", Verification="unverified", Authorization="proposed", Source="agent"),
    reference("CR-T-CORE-02-01", "T-CORE-02", "src/core/ledger.py", Relation="creates", Revision="1c0ffee"),
    reference("CR-T-PAY-01-01", "T-PAY-01", "src/pay/cards.py", Relation="creates", Revision="7b2e9d4"),
])


def task_text(tid, title, status, module, deps, validation, owner, decisions, states, builder) -> str:
    lines = [f"## {tid}: {title}", f"- Status: {status}", f"- Module: {module}"]
    if module == "M-OPS":
        lines.append("- Domain: operations")
    if owner:
        lines.append("- Authority: owner")
    lines += [f"- Owner: {builder}", f"- Validation: {validation}",
              f"- Dependencies: {', '.join(deps) if deps else 'none'}", f"- AC: AC-{tid}"]
    if status in ("WIP", "DONE", "BLOCKED"):
        lines.append("- Claimed: 2026-11-0{}".format(1 + len(tid) % 8))
    evidence = {"DONE": "Every criterion passes; see the contract."}.get(status, "—")
    lines.append(f"- Evidence: {evidence}")
    if decisions:
        lines.append(f"- Governed by: {', '.join(decisions)}")
    return "\n".join(lines) + "\n"


def acceptance_text() -> str:
    out = ["# Acceptance\n\n# Contracts\n"]
    for tid, title, _status, _module, _deps, _val, _owner, _dec, states, _b in TASKS:
        out.append(f"\n## AC-{tid} — {title}\n")
        texts = CRITERIA.get(tid, [f"Given the work, When {title.lower()} is checked, Then it holds."])
        notes = EVIDENCE.get(tid, [])
        for n, (state, text) in enumerate(zip(states, texts), 1):
            cls = "MANUAL" if tid in ("T-CORE-03", "T-PAY-04") else "TEST"
            out.append(f"- `AC-{tid}-{n:02d}` — {text} `{cls}` · `{state}`\n")
            note = notes[n - 1] if n - 1 < len(notes) else ("Recorded in the test run." if state == "PASS" else None)
            if note:
                out.append(f"  - Evidence: {note}\n")
    return "".join(out)


def build(root: Path) -> Path:
    """Write the Tally chronicle under root and return its chronicle directory."""
    chronicle = root / ".praukron" / "chronicle"
    shutil.copytree(TEMPLATES, chronicle)
    files = {
        "THESIS.md": THESIS, "PHASES.md": PHASES, "MODULES.md": MODULES,
        "TASKS.md": "# Tasks\n\n" + "\n".join(task_text(*t) for t in TASKS),
        "ACCEPTANCE.md": acceptance_text(), "ASSUMPTIONS.md": ASSUMPTIONS, "JOURNAL.md": JOURNAL,
        "CODE.md": CODE,
    }
    for name, text in files.items():
        (chronicle / name).write_text(text, encoding="utf-8")
    for name, extra in (("RESPONSES.md", RESPONSES), ("AUTHORITY.md", GRANT), ("TRACE.md", TRACE),
                        ("TECH_DEBT.md", DEBT)):
        path = chronicle / name
        path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    for adr, text in ADRS.items():
        (chronicle / "ADR" / f"{adr}.md").write_text(text, encoding="utf-8")
    (chronicle / "INTENT.md").write_text("# Intent\n\nT-BILL-03: the oversized credit note is accepted; "
                                         "make the refusal pass.\n", encoding="utf-8")
    return chronicle


def tool(root: Path, *args: str) -> None:
    subprocess.run([sys.executable, str(TOOL), "-C", str(root), *args], check=True,
                   stdout=subprocess.DEVNULL)


# Delays are real milliseconds between actions through the page's own controls.
# Existing asset names retain their subjects: state is Work, contract is a
# selected acceptance record, and next is the owner's waiting strip.
SHOTS = {
    "work": ("dashboard-state.png", [
        [300, "document.getElementById('tab-work').click(); press('s')"],
        [300, "document.getElementById('lanes-fit').click()"],
    ]),
    "force": ("dashboard-force.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('lanes-decisions').click(); document.getElementById('lanes-assumptions').click(); press('2')"],
        [3000, "document.getElementById('lanes-fit').click()"],
        [300, "stageKey('-'); document.getElementById('stage').blur()"],
    ]),
    "3d": ("dashboard-3d.png", [
        [300, "press('W', {shiftKey: true}); press('s'); press('3')"],
        [4500, "press('p'); window.dispatchEvent(new Event('resize')); V3.resize()"],
        [300, "stageKey('+')"],
    ]),
    "record": ("dashboard-contract.png", [
        [300, "press('W', {shiftKey: true}); press('1')"],
        [300, "shSelect('T-BILL-03')"],
        [300, "document.getElementById('lanes-fit').click(); document.getElementById('stage').blur()"],
    ]),
    "waiting": ("dashboard-next.png", [
        [300, "document.getElementById('tab-work').click(); shSelect('T-PAY-02')"],
    ]),
    "code": ("dashboard-code.png", [
        [300, "press('W', {shiftKey: true}); press('1'); shSelect('T-BILL-01')"],
        [300, "for (let n = 0; n < 7; n++) document.querySelector('[data-resize=side]').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', shiftKey: true, bubbles: true}))"],
        [300, "document.querySelector('#side .detail-code').scrollIntoView({block: 'start'}); document.getElementById('stage').blur()"],
    ]),
    "registry": ("dashboard-registry.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('tab-work').click()"],
        [300, "document.getElementById('work-tab-registry').click()"],
        [300, "setControl('task-search', 'invoice', 'input')"],
        [300, "document.getElementById('work-view-registry').scrollIntoView({block: 'start'})"],
    ]),
    "operations": ("dashboard-operations.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('tab-audit').click()"],
        [300, "document.querySelector('#panel-governance > summary').click()"],
    ]),
    "views": ("dashboard-views.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('tab-work').click()"],
        [300, "document.getElementById('work-tab-graph').click()"],
        [300, "document.getElementById('work-view-graph').scrollIntoView({block: 'start'})"],
    ]),
    "review": ("dashboard-review.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('tab-review').click()"],
    ]),
    "audit": ("dashboard-audit.png", [
        [300, "press('W', {shiftKey: true}); press('s'); document.getElementById('tab-audit').click()"],
        [300, "document.querySelector('#panel-operations > summary').click()"],
        [300, "document.getElementById('panel-operations').scrollIntoView({block: 'start'}); document.getElementById('main').scrollTop -= document.querySelector('.stickyhead').offsetHeight + 12"],
    ]),
}

# Only preferences and input helpers are injected. Product HTML, styles, code,
# records, layout and the animation scheduler remain untouched.
DRIVER = r"""
localStorage.clear();
localStorage.setItem('praukron-theme', 'dark');
localStorage.setItem('praukron-panels', JSON.stringify({tab: 'work', view: 'lanes'}));
function press(key, extra) {
  document.body.dispatchEvent(new KeyboardEvent('keydown', Object.assign({key, bubbles: true}, extra || {})));
}
function stageKey(key) {
  document.getElementById('stage').dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true}));
}
function setControl(id, value, event = 'change') {
  const element = document.getElementById(id);
  element.value = value;
  element.dispatchEvent(new Event(event, {bubbles: true}));
}
"""


def chrome() -> str:
    for candidate in (os.environ.get("PRAUKRON_CHROME"),
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("Chrome was not found; set PRAUKRON_CHROME to its executable.")


class Chrome:
    """A small synchronous DevTools client over Chrome's inherited pipes.

    Chrome uses descriptors 3 and 4; a Python exec wrapper assigns them without
    changing this process's descriptors. Everything stays on pipes: no server,
    listening port, third-party driver, network request or dependency download.
    """

    def __init__(self, work: Path):
        command_read, self.command_write = os.pipe()
        self.reply_read, reply_write = os.pipe()
        wrapper = ("import os,sys; os.dup2(int(sys.argv[1]),3); "
                   "os.dup2(int(sys.argv[2]),4); os.execv(sys.argv[3],sys.argv[3:])")
        # Duplicate above 4 so assigning descriptor 3 cannot clobber the source
        # for descriptor 4 on platforms that allocate the first pipe at 3.
        import fcntl
        read_source = fcntl.fcntl(command_read, fcntl.F_DUPFD, 10)
        write_source = fcntl.fcntl(reply_write, fcntl.F_DUPFD, 10)
        self.process = subprocess.Popen([
            sys.executable, "-c", wrapper, str(read_source), str(write_source), chrome(),
            "--headless=new", "--remote-debugging-pipe", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking", "--disable-component-update",
            "--disable-sync", "--disable-extensions", "--host-resolver-rules=MAP * ~NOTFOUND",
            "--proxy-server=127.0.0.1:9", "--force-color-profile=srgb",
            f"--user-data-dir={work / 'chrome-profile'}", "about:blank",
        ], pass_fds=(read_source, write_source), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for fd in (command_read, reply_write, read_source, write_source):
            os.close(fd)
        self.sequence = 0
        self.buffer = b""
        self.errors: list[str] = []
        self.call("Browser.getVersion")

    def call(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        payload = json.dumps(message).encode() + b"\0"
        while payload:
            payload = payload[os.write(self.command_write, payload):]
        deadline = time.monotonic() + 30
        while True:
            while b"\0" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.reply_read], [], [], remaining)[0]:
                    raise RuntimeError(f"Chrome timed out: {method}")
                chunk = os.read(self.reply_read, 1024 * 1024)
                if not chunk:
                    raise RuntimeError(f"Chrome closed its pipe: {method}")
                self.buffer += chunk
            raw, self.buffer = self.buffer.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("method") == "Runtime.exceptionThrown":
                self.errors.append(str(reply["params"]["exceptionDetails"]))
            if reply.get("id") != self.sequence:
                continue
            if "error" in reply:
                raise RuntimeError(f"{method}: {reply['error']}")
            return reply.get("result", {})

    def evaluate(self, session: str, expression: str):
        reply = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                             "awaitPromise": True}, session)
        if "exceptionDetails" in reply:
            raise RuntimeError(f"Screenshot action failed: {expression}: {reply['exceptionDetails']}")
        return reply.get("result", {}).get("value")

    def close(self):
        try:
            self.call("Browser.close")
        finally:
            self.process.wait(timeout=10)
            os.close(self.command_write)
            os.close(self.reply_read)


def capture(browser: Chrome, page: Path, out: Path, steps: list) -> None:
    target = browser.call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session = browser.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
    browser.errors.clear()
    try:
        browser.call("Page.enable", session=session)
        browser.call("Runtime.enable", session=session)
        browser.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 2, "mobile": False,
        }, session)
        browser.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "dark"},
        ]}, session)
        browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": DRIVER}, session)
        browser.call("Page.navigate", {"url": page.as_uri()}, session)
        deadline = time.monotonic() + 15
        while not browser.evaluate(session, "window.shReady === true"):
            if time.monotonic() > deadline:
                raise RuntimeError("Dashboard did not become ready")
            time.sleep(0.05)
        for delay, action in steps:
            time.sleep(delay / 1000)
            browser.evaluate(session, action)
        # scrollIntoView can also move the outer document by a few pixels;
        # leave the product's scrollable panels in place and restore its header.
        browser.evaluate(session, "window.scrollTo(0, 0)")
        # Wait for fonts and two real frames after the final layout/scroll.
        browser.evaluate(session, "document.fonts.ready.then(() => new Promise(resolve => "
                                 "requestAnimationFrame(() => requestAnimationFrame(resolve))))")
        time.sleep(0.5)
        if browser.errors:
            raise RuntimeError("Dashboard JavaScript errors: " + "\n".join(browser.errors))
        if out.name == "dashboard-3d.png":
            state = browser.evaluate(session, "(() => { const v = V3.inspect(); return "
                                     "{laid: v.laid, play: v.play, frames: v.stats.frames}; })()")
            if not state["laid"] or state["play"] or not state["frames"]:
                raise RuntimeError(f"3D scene did not settle in a paused view: {state}")
        png = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, session)
        out.write_bytes(base64.b64decode(png["data"]))
    finally:
        browser.call("Target.closeTarget", {"targetId": target})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "assets")
    parser.add_argument("--only", help="comma-separated names: " + ", ".join(SHOTS))
    parser.add_argument("--chronicle", type=Path, help="write the chronicle into this new directory and stop")
    args = parser.parse_args()
    if args.chronicle:
        build(args.chronicle)
        tool(args.chronicle, "compile")
        print(f"Wrote the synthetic chronicle to {args.chronicle}")
        return 0
    names = args.only.split(",") if args.only else list(SHOTS)
    unknown = [name for name in names if name not in SHOTS]
    if unknown:
        parser.error("unknown image: " + ", ".join(unknown))
    with tempfile.TemporaryDirectory(prefix="praukron-shots-") as tmp:
        work = Path(tmp)
        project = work / "tally"
        build(project)
        tool(project, "validate", "--quiet")
        tool(project, "compile")
        tool(project, "dashboard")
        page = project / ".praukron" / "compiled" / "dashboard.html"
        args.out.mkdir(parents=True, exist_ok=True)
        browser = Chrome(work)
        try:
            for name in names:
                file, steps = SHOTS[name]
                capture(browser, page, args.out / file, steps)
                print(f"Wrote {args.out / file}", flush=True)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
