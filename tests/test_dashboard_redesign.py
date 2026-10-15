"""The attention-first dashboard: cross-filtering charts, the live Needs you
panel, and both themes (T-DASH-REDESIGN-01)."""

from __future__ import annotations

import re
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402
import test_review  # noqa: E402


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", block))


def _theme_tokens() -> dict[str, dict[str, str]]:
    style = dashboard._STYLE
    light = style.split(':root, :root[data-theme="light"] {', 1)[1].split("}", 1)[0]
    dark = style.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    system = style.split(":root:not([data-theme]) {", 1)[1].split("}", 1)[0]
    return {"light": _tokens(light), "dark": _tokens(dark), "system-dark": _tokens(system)}


def _contrast(a: str, b: str) -> float:
    def luminance(colour: str) -> float:
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class TestCrossFilter(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.compiled = compiler.as_json(self.project)
        self.html = dashboard.render(self.project, analytics.report(self.project), self.compiled)
        self.overview = self.html.split('id="panel-overview"', 1)[1].split('<section class="panel"', 1)[0]

    def chart(self, dim: str) -> str:
        return self.overview.split(f'data-chart="{dim}"', 1)[1].split("</figure>", 1)[0]

    def test_every_chart_counts_compiled_tasks(self) -> None:
        keys = {"status": "status", "phase": "phase", "domain": "domain", "validation": "validation"}
        for dim, field in keys.items():
            with self.subTest(dim=dim):
                expected = Counter(t[field] for t in self.compiled["tasks"])
                found = dict(re.findall(
                    r'class="key[^"]*" data-dim="' + dim + r'" data-value="([^"]+)" aria-pressed="false">'
                    r'.*?<span class="n">(\d+)</span>', self.chart(dim)))
                self.assertEqual({k: int(v) for k, v in found.items()}, dict(expected))
        modules = Counter(t["module"] or "~none" for t in self.compiled["tasks"])
        found = re.findall(r'data-dim="module" data-value="([^"]+)"', self.chart("module"))
        self.assertEqual(sorted(found), sorted(modules))

    def test_filters_are_buttons_and_never_inputs(self) -> None:
        """The read-only guarantee holds: a filter is a pressed button, not a field."""
        for key in re.findall(r"<[^>]*data-dim=[^>]*>", self.overview):
            self.assertTrue(key.startswith(("<button type=\"button\"", "<span class=\"seg")), key)
        self.assertNotIn("<input", self.overview)

    def test_active_filters_are_visible_removable_and_in_the_hash(self) -> None:
        bar = self.html.split('id="xf-bar"', 1)[1].split("</div>", 1)[0]
        self.assertIn('role="region" aria-label="Active filters" hidden', self.html)
        self.assertIn("data-xf-clear>Clear all</button>", bar)
        self.assertIn('id="xf-status" aria-live="polite"', self.html)
        self.assertIn("aria-label=\"Remove filter ${esc(DIM_LABEL[dim])}: ${esc(label)}\"", self.html)
        # The hash is `#tab?dim=a,b`; the head script reads only the tab.
        self.assertIn("return query ? name + '?' + query : name;", self.html)
        self.assertIn("var tab = location.hash.slice(1).split('?')[0];", self.html)
        self.assertIn("readFilters(location.hash.slice(1).split('?')[1]);", self.html)

    def test_filters_reach_tables_obstacles_and_the_needs_panel(self) -> None:
        self.assertIn("const SCOPED = 'ul.xf-list > li, ol.chain > li, details.acard';", self.html)
        self.assertIn("document.querySelectorAll('tbody tr[data-task]')", self.html)
        self.assertIn('id="needs-filtered" hidden', self.html)
        execution = self.html.split('id="panel-execution"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('<ul class="plain xf-list">', execution)
        # The registry's selects drive the same filters as the charts.
        self.assertIn("'task-status': 'status'", self.html)

    def test_attention_comes_before_progress(self) -> None:
        signals = re.findall(r'<a class="sig (\w+)" href="#([\w-]+)"><span class="sig-n">(\d+)</span>'
                             r'<span class="sig-l">([^<]+)</span>', self.overview)
        self.assertEqual([s[3] for s in signals], [
            "Blocked tasks", "Failing work", "Needs you", "Review gaps",
            "Debt at its trigger", "Unresolved failures"])
        blocked = signals[0]
        self.assertEqual(int(blocked[2]), len(self.compiled["blocked"]))
        self.assertEqual(blocked[0], "bad" if self.compiled["blocked"] else "clear")
        for tone, href, count, _ in signals:
            self.assertEqual(tone == "clear", count == "0")
            self.assertIn(href, ("execution", "needs-you", "governance", "operations"))
        order = [self.overview.index(marker) for marker in
                 ('class="attention"', 'class="focus"', 'id="breakdown"', "<h2>Execution progress</h2>")]
        self.assertEqual(order, sorted(order))

    def test_the_page_stays_self_contained(self) -> None:
        external = re.findall(r'(?:src|href)="(https?:[^"]+)"', self.html)
        self.assertEqual(external, [dashboard.MERMAID_CDN])
        self.assertNotIn("@import", self.html)
        self.assertNotIn("url(", dashboard._STYLE)


class TestThemes(unittest.TestCase):
    def test_both_themes_define_every_token(self) -> None:
        themes = _theme_tokens()
        self.assertEqual(set(themes["light"]), set(themes["dark"]))
        self.assertEqual(themes["dark"], themes["system-dark"])

    def test_text_and_marks_meet_wcag_aa_in_both_themes(self) -> None:
        for name, t in _theme_tokens().items():
            for surface in ("bg", "panel", "panel-raised"):
                for text in ("ink", "muted", "accent", "good", "warn", "bad", "wip", "todo", "ops"):
                    with self.subTest(theme=name, text=text, surface=surface):
                        self.assertGreaterEqual(_contrast(t[text], t[surface]), 4.5)
                with self.subTest(theme=name, border=surface):
                    self.assertGreaterEqual(_contrast(t["line-strong"], t[surface]), 3.0)
            self.assertGreaterEqual(_contrast(t["on-warn"], t["warn"]), 4.5)
            self.assertGreaterEqual(_contrast(t["on-accent"], t["accent"]), 4.5)
            self.assertGreaterEqual(_contrast(t["ink"], t["sunken"]), 4.5)

    def test_system_is_a_choice_and_motion_can_be_reduced(self) -> None:
        self.assertIn("@media (prefers-reduced-motion: reduce)", dashboard._STYLE)
        self.assertIn("localStorage.removeItem(THEME_KEY)", dashboard._SCRIPT)


class TestLiveNeedsPanel(test_review.TestBlockerGuidance):
    def test_the_served_panel_is_live_and_counts_down(self) -> None:
        self.render(interactive=True)
        self.assertIn('<span class="remaining" aria-live="polite"></span>', self.needs)
        self.assertIn("needsPanel.classList.add('is-live')", self.html)
        self.assertIn("badge.textContent = left;", self.html)
        self.assertIn('<ul class="plain owner-list xf-list">', self.needs)
        # The bar comes before the cards, so focus reaches name and submit first.
        self.assertLess(self.needs.index("review-bar"), self.needs.index("details class=\"acard\""))
        self.assertNotIn("This page is read-only.", self.needs)

    def test_the_static_panel_says_it_is_read_only(self) -> None:
        self.assertIn("This page is read-only. To answer, run <code>praukron dashboard --serve</code>",
                      self.needs)
        self.assertNotIn("needsPanel.classList.add('is-live')", self.html)
        held = [o for o in self.compiled["obstacles"] if o.get("actor") == "owner"]
        self.assertIn(f'data-waiting="{len(held) + 2}"', self.html)


# The fixture is borrowed; its own tests run in test_review, not twice.
for _name in dir(test_review.TestBlockerGuidance):
    if _name.startswith("test_") and _name not in vars(TestLiveNeedsPanel):
        setattr(TestLiveNeedsPanel, _name, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
