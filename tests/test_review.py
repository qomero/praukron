"""The Assumptions tab, blocker guidance, and the Needs you side panel (T-REVIEW-TAB-01,
T-REVIEW-BLOCKERS-01, T-REVIEW-PANEL-01)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard  # noqa: E402
from test_assumptions import ASSUMPTIONS, AssumptionCase  # noqa: E402

SETTLED = """
## A-3: Today is Ho Chi Minh City time
- Status: CONFIRMED
- Tasks: T-ONE
- Impact: MEDIUM
- Recorded: 2026-09-30 · claude/primary
- Assumption: Today is the date in Asia/Ho_Chi_Minh.
- Basis: The team works in Vietnam.
- Applied in: AC-T-ONE-01
- Responses: R-3
- Reconciled by: none

## A-4: Labels say Delivered Value
- Status: OPEN
- Tasks: T-THREE
- Impact: LOW
- Recorded: 2026-10-03 · claude/primary
- Assumption: Labels read Delivered Value.
- Basis: Matches the workbook.
- Applied in: T-THREE
- Responses: none
- Reconciled by: none
"""

CONFIRM = """
## R-3: A-3 CONFIRM
- By: Thien (owner)
- Date: 2026-10-01
- Via: dashboard

> Confirm HCMC time.
"""


class ReviewCase(AssumptionCase):
    def setUp(self) -> None:
        super().setUp()
        responses = (self.authority / "RESPONSES.md").read_text(encoding="utf-8") + CONFIRM
        acceptance = self.authority / "ACCEPTANCE.md"
        acceptance.write_text(acceptance.read_text(encoding="utf-8").replace("Measured on site.", "Measured on site under A-1."), encoding="utf-8")
        self.write(assumptions=ASSUMPTIONS + SETTLED, responses=responses)
        self.render()

    def render(self, interactive: bool = False) -> None:
        self.load()
        self.report = analytics.report(self.project)
        self.compiled = compiler.as_json(self.project)
        self.html = dashboard.render(self.project, self.report, self.compiled, interactive=interactive)
        body = self.html.split('id="panel-review"', 1)[1]
        self.panel = re.split(r'<section class="panel"|<footer>', body, maxsplit=1)[0]
        self.needs = self.panel

    def section(self, heading: str) -> str:
        return self.panel.split(f"<h3>{heading}</h3>", 1)[1].split("<h3>", 1)[0]


class TestAssumptionsTab(ReviewCase):
    def test_the_queue_is_grouped_and_ordered(self) -> None:
        """AC-T-REVIEW-TAB-01-01."""
        self.assertIn('role="tab" id="tab-review"', self.html)
        review = self.needs.split('aria-label="Assumptions requiring validation"', 1)[1].split('</section>', 1)[0]
        # A-1 awaits the agent (R-1 REVISE), so it is not in the owner's queue.
        self.assertNotIn('data-id="A-1"', review)
        self.assertLess(review.index('data-id="A-2"'), review.index('data-id="A-4"'))
        answered = self.needs.split('aria-label="Previously answered"', 1)[1]
        self.assertIn('data-id="A-1"', answered)
        self.assertIn('data-id="A-3"', answered)
        for element in ('id="assume-search"', 'id="assume-phase"', 'id="assume-impact"', 'id="assume-status"'):
            self.assertIn(element, self.panel)
        phases = re.search(r'<select id="assume-phase".*?</select>', self.panel, re.S).group(0)
        self.assertEqual(re.findall(r'<option value="(\w[^"]*)"', phases), ["P1"])

    def test_a_card_explains_itself(self) -> None:
        """AC-T-REVIEW-TAB-01-02."""
        card = self.html.split('id="assumption-A-1"', 1)[1].split("</details>", 1)[0]
        for fragment in ("Each client has one payment term in days, default 30.",
                         "No term exists in the data. Researched, not confirmed.",
                         ">HIGH<", "Phase P1", "M-FOUNDATION", "ADR-001, AC-T-ONE-01",
                         "(DONE)", "AC-T-ONE-01 (PASS)", "If revised:",
                         "Mặc định 7 ngày, không phải 30.", "— Thien (owner), 2026-10-02"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, card)
        self.assertIn('data-task="T-ONE"', card)

    def test_the_static_page_is_read_only(self) -> None:
        """AC-T-REVIEW-TAB-01-03."""
        self.assertIn("run <code>praukron dashboard --serve</code>", self.panel)
        self.assertIn("This page is read-only.", self.needs)
        self.assertNotIn("<textarea", self.html)
        self.assertNotIn("<form", self.html)
        self.assertIn("Confirm HCMC time.<span class=\"who\"> — Thien (owner), 2026-10-01", self.panel)

    def test_permission_assumptions_come_first_and_can_be_filtered(self) -> None:
        """AC-T-REVIEW-TAB-01-05."""
        review = self.needs.split('aria-label="Assumptions requiring validation"', 1)[1]
        self.assertLess(review.index('data-id="A-2"'), review.index('data-id="A-4"'))
        card = self.needs.split('id="assumption-A-2"', 1)[1].split("</details>", 1)[0]
        self.assertIn('<span class="pill permission">Permission</span>', card)
        self.assertIn("The agent assumed an access rule:</strong> sales read own clients", card)
        self.assertIn('<option value="permission">permission</option>', self.panel)
        self.assertIn('data-kind="permission"', card)


class TestBlockerGuidance(ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        tasks = self.authority / "TASKS.md"
        text = tasks.read_text(encoding="utf-8")
        text = text.replace("## T-TWO: Build on it\n- Status: TODO\n- Module: M-FOUNDATION\n",
                            "## T-TWO: Build on it\n- Status: WIP\n- Module: M-FOUNDATION\n- Authority: owner\n", 1)
        text = text.replace("- Schedule: start=2026-10-01 estimate=3d", "- Schedule: start=2026-10-01")
        text = text.replace("- Status: DONE\n- Module: M-FOUNDATION\n- Validation: SYNTHETIC",
                            "- Status: DONE\n- Module: M-FOUNDATION\n- Validation: UNTESTED", 1)
        tasks.write_text(text, encoding="utf-8")
        self.render()
        self.execution = self.html.split('id="panel-execution"', 1)[1].split('<section class="panel"', 1)[0]

    def test_every_obstacle_says_what_why_what_unblocks_and_who(self) -> None:
        """AC-T-REVIEW-BLOCKERS-01-01."""
        obstacles = self.execution.split("<h2>Obstacles</h2>", 1)[1].split("<h2>", 1)[0]
        items = re.findall(r"<li>.*?</li>", obstacles, re.S)
        self.assertEqual(len(items), len(self.compiled["obstacles"]))
        for item in items:
            with self.subTest(item=item[:60]):
                self.assertIn("<strong>To unblock:</strong>", item)
                self.assertIn("<strong>Who acts:</strong>", item)
        for o in self.compiled["obstacles"]:
            self.assertIn(o["unblock"].replace("'", "&#x27;"), obstacles)
        home = self.html.split('id="panel-home"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('<h3>Root blocker</h3>', home)
        self.assertIn('T-TWO', home)

    def test_owner_held_blockers_are_grouped_and_offer_guide(self) -> None:
        """AC-T-REVIEW-BLOCKERS-01-02."""
        group = self.needs.split('id="review-T-TWO"', 1)[1].split('</details>', 1)[0]
        self.assertIn('data-task="T-THREE"', group)
        self.assertIn("Owner", self.needs)
        self.assertIn("Guide: run <code>praukron dashboard --serve</code>", group)
        self.render(interactive=True)
        served = self.needs.split('id="review-T-TWO"', 1)[1].split('</details>', 1)[0]
        self.assertIn('data-target="T-TWO" data-guide="1"', served)
        self.assertIn("<textarea", served)

    def test_guidance_is_shown_verbatim_and_survives_completion(self) -> None:
        """AC-T-REVIEW-BLOCKERS-01-03."""
        section = self.execution.split("<h2>Owner guidance</h2>", 1)[1].split("<h2>", 1)[0]
        self.assertIn("Use the staging backup. Do not touch production.", section)
        self.assertIn("— Thien (owner), 2026-10-02 · on", section)
        tasks = self.authority / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("- Status: WIP\n- Module: M-FOUNDATION\n- Authority: owner\n- Validation: UNTESTED",
                                                   "- Status: DONE\n- Module: M-FOUNDATION\n- Authority: owner\n- Validation: HUMAN_VERIFIED", 1), encoding="utf-8")
        acceptance = self.authority / "ACCEPTANCE.md"
        acceptance.write_text(acceptance.read_text(encoding="utf-8").replace("Then they stand.\n  `TEST` · `NOT_RUN`",
                                                             "Then they stand.\n  `TEST` · `PASS`"), encoding="utf-8")
        before = [(a.id, a.status) for a in compiler.load(self.dir).assumptions]
        self.render()
        self.assertEqual(self.project.task("T-TWO").status, "DONE")
        execution = self.html.split('id="panel-execution"', 1)[1].split('<section class="panel"', 1)[0]
        section = execution.split("<h2>Owner guidance</h2>", 1)[1].split("<h2>", 1)[0]
        self.assertIn("Use the staging backup. Do not touch production.", section)
        self.assertIn('<span class="pill DONE">DONE</span>', section)
        self.assertEqual([(a.id, a.status) for a in self.project.assumptions], before)
        criteria = [c.state for c in self.project.contracts["AC-T-THREE"].criteria]
        self.assertEqual(criteria, ["NOT_RUN"])


class TestNeedsPanel(TestBlockerGuidance):
    def test_everything_awaiting_the_owner_is_in_one_panel_and_nowhere_else(self) -> None:
        """AC-T-REVIEW-PANEL-01-01."""
        self.assertNotIn('<aside', self.html)
        review = self.needs.split('aria-label="Assumptions requiring validation"', 1)[1]
        self.assertLess(review.index('data-id="A-2"'), review.index('data-id="A-4"'))
        self.assertEqual(self.needs.count('id="review-T-TWO"'), 1)
        self.assertNotIn('id="review-T-THREE"', self.needs)
        for card in ('data-id="A-2"', 'data-id="A-4"', 'id="assumption-A-2"'):
            self.assertEqual(self.html.count(card), 1, card)
        self.assertIn('<span class="needcount">3</span>', self.needs)
        self.assertIn("awaiting the agent", self.needs)

    def test_served_forms_and_the_one_submit_bar_live_in_the_panel(self) -> None:
        """AC-T-REVIEW-PANEL-01-02."""
        self.render(interactive=True)
        outside = self.html.replace(self.needs, "")
        self.assertEqual(self.html.count('class="controls review-bar"'), 1)
        self.assertIn('class="controls review-bar"', self.needs)
        # Only a settled, confirmed card keeps its form in the tab, to revise it later;
        # the one submit bar in the panel collects it with the rest.
        self.assertEqual(re.findall(r'class="aform" data-target="([^"]+)"', outside), [])
        self.assertIn('data-target="A-2"', self.needs)
        self.assertIn('data-target="T-TWO" data-guide="1"', self.needs)
        self.assertIn("document.querySelectorAll('details.acard').forEach(card => card.addEventListener('toggle'",
                      self.html)

    def test_an_empty_panel_says_so(self) -> None:
        """AC-T-REVIEW-PANEL-01-03."""
        self.write(assumptions="", responses="")
        tasks = self.authority / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("- Authority: owner\n", ""), encoding="utf-8")
        self.render()
        self.assertIn('<span class="needcount zero">0</span>', self.needs)
        self.assertIn("Nothing waits for you.", self.needs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
