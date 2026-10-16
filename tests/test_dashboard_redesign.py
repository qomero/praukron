"""The attention-first dashboard: cross-filtering charts and figures, the live
Needs you panel, and both themes (T-DASH-REDESIGN-01).

Behaviour is checked on what the page renders and on what its scripts compute:
the pure filter rules, the head script and the review rule run under node,
with the expected result computed here from the compiled project."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard, review  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402
import test_review  # noqa: E402

NODE = shutil.which("node")


def run_js(source: str) -> object:
    """Run a script under node and return what it printed as JSON."""
    if NODE is None:
        raise unittest.SkipTest("node is not installed")
    done = subprocess.run([NODE, "-e", source], capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def with_filters(compiled: dict, body: str) -> object:
    """The page's filter rules over the compiled project, then `body`."""
    return run_js(
        f"const DATA = {json.dumps(compiled)};\n"
        "const byId = Object.fromEntries(DATA.tasks.map(t => [t.id, t]));\n"
        + dashboard.FILTER_JS + "\n" + body
    )


def holders(page: str) -> list[dict]:
    """Every element that carries compiled figure entries, with what it shows."""
    found = []
    for match in re.finditer(r'<(\w+) [^>]*data-agg="([^"]+)"[^>]*>', page):
        tag = match.group(1)
        body = page[match.end():]
        depth, end = 1, 0
        for inner in re.finditer(rf"<(/?){tag}\b[^>]*>", body):
            depth += -1 if inner.group(1) else 1
            if depth == 0:
                end = inner.start()
                break
        body = body[:end]
        shown = dict(re.findall(r'data-agg-key="(\w+)"[^>]*>([^<]*)<', body))
        found.append({"open": match.group(0), "entries": json.loads(html.unescape(match.group(2))),
                      "shown": shown, "body": body})
    return found


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


def _mix(a: str, b: str, share: float) -> str:
    """CSS color-mix(in srgb, a share, b)."""
    return "#" + "".join(
        f"{round(int(a[i:i + 2], 16) * share + int(b[i:i + 2], 16) * (1 - share)):02x}" for i in (1, 3, 5))


class TestCrossFilter(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.compiled = compiler.as_json(self.project)
        self.html = dashboard.render(self.project, analytics.report(self.project), self.compiled)
        self.overview = self.html.split('id="panel-overview"', 1)[1].split('<section class="panel"', 1)[0]
        # Filter by the rarest status, so a filter leaves some tasks out and keeps some.
        counts = Counter(t["status"] for t in self.compiled["tasks"])
        self.status = min(counts, key=lambda s: (counts[s], s))
        self.assertLess(counts[self.status], len(self.compiled["tasks"]))

    def chart(self, dim: str) -> str:
        return self.overview.split(f'data-chart="{dim}"', 1)[1].split("</figure>", 1)[0]

    def test_every_chart_counts_compiled_tasks(self) -> None:
        for dim in ("status", "phase", "domain", "validation"):
            with self.subTest(dim=dim):
                expected = Counter(t[dim] for t in self.compiled["tasks"])
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

    def test_a_chart_counts_the_tasks_that_pass_every_other_filter(self) -> None:
        result = with_filters(self.compiled, f"filters.status.add({json.dumps(self.status)});" + """
            console.log(JSON.stringify({ phase: countsFor('phase').counts,
                                         status: countsFor('status').counts,
                                         matching: DATA.tasks.filter(t => passes(t)).map(t => t.id) }));""")
        chosen = [t for t in self.compiled["tasks"] if t["status"] == self.status]
        self.assertEqual(result["phase"], dict(Counter(t["phase"] for t in chosen)))
        # A chart never filters itself, so every status stays selectable.
        self.assertEqual(result["status"], dict(Counter(t["status"] for t in self.compiled["tasks"])))
        self.assertEqual(result["matching"], [t["id"] for t in chosen])

    def test_the_hash_keeps_tab_and_filters_and_reads_them_back(self) -> None:
        result = with_filters(self.compiled, """
            filters.module.add('~none'); filters.phase.add('P 1,x'); filters.status.add('WIP');
            const hash = hashFor('execution');
            const query = hash.split('?')[1];
            readFilters(query);
            const back = Object.fromEntries(Object.entries(filters).map(([d, s]) => [d, [...s].sort()]));
            readFilters('');
            console.log(JSON.stringify({ hash, back, cleared: filtering(), bare: hashFor('tasks') }));""")
        self.assertTrue(result["hash"].startswith("execution?status=WIP&phase=P%201%2Cx&module="))
        self.assertEqual(result["back"]["phase"], ["P 1,x"])
        self.assertEqual(result["back"]["module"], ["~none"])
        self.assertEqual((result["cleared"], result["bare"]), (False, "tasks"))
        # The head script picks the tab out of a hash that carries filters.
        boot = run_js(
            "const attrs = {}; const document = { documentElement: { className: '', "
            "setAttribute: (k, v) => { attrs[k] = v; } } };\n"
            "const localStorage = { getItem: () => null };\n"
            "const location = { hash: '#" + result["hash"] + "' };\n"
            "const window = { matchMedia: () => ({ matches: true }) }; const matchMedia = window.matchMedia;\n"
            + dashboard._BOOT + "\nconsole.log(JSON.stringify(attrs));")
        self.assertEqual(boot, {"data-theme": "dark", "data-tab": "execution"})

    def test_every_task_figure_follows_the_filters(self) -> None:
        """Every figure that counts tasks carries the compiled entries behind
        it: unfiltered they sum to the figure shown, filtered to the figure
        over the tasks that pass."""
        figures = holders(self.html)
        kinds = Counter(re.search(r'class="(\w+)', f["open"]).group(1) if "class=" in f["open"] else
                        f["open"].split()[0][1:] for f in figures)
        validation = [f for f in figures if f["open"].startswith("<tr")]
        self.assertEqual(len(validation), len(self.compiled["metrics"]["validationBreakdown"]))
        self.assertEqual(kinds["phase"], len(self.compiled["phases"]))
        self.assertEqual(kinds["module"], len(self.compiled["modules"]))
        self.assertGreaterEqual(kinds["sig"], 5)
        self.assertGreaterEqual(kinds["card"], 4 + 7)
        sums = with_filters(self.compiled, f"""
            const figures = {json.dumps([f["entries"] for f in figures])};
            const total = () => figures.map(f => Object.fromEntries(
              Object.entries(f).map(([k, entries]) => [k, aggValue(entries)])));
            const unfiltered = total();
            filters.status.add({json.dumps(self.status)});
            console.log(JSON.stringify({{ unfiltered, chosen: total() }}));""")
        for figure, computed in zip(figures, sums["unfiltered"]):
            for key, shown in figure["shown"].items():
                with self.subTest(figure=figure["open"][:60], key=key):
                    self.assertEqual(computed[key], int(shown))
        execution = [t for t in self.project.execution_tasks]
        chosen = [t for t in execution if t.status == self.status]
        for figure, computed in zip(validation, [sums["chosen"][figures.index(v)] for v in validation]):
            state = re.search(r'<span class="pill (\w+)', figure["body"]).group(1)
            self.assertEqual(computed["n"], sum(1 for t in chosen if t.validation == state))
            self.assertEqual(computed["total"], len(chosen))
        completion = next(i for i, f in enumerate(figures) if "Task completion" in f["body"])
        self.assertEqual(sums["chosen"][completion],
                         {"done": sum(1 for t in chosen if t.done), "total": len(chosen)})

    def test_a_shared_contract_counts_while_any_task_naming_it_passes(self) -> None:
        """Two started tasks share one contract; a filter that drops the first
        but keeps the second still counts the contract's criteria, once."""
        self.rewrite("TASKS.md", "## T-TWO: Build on it\n- Status: TODO", "## T-TWO: Build on it\n- Status: WIP")
        self.rewrite("TASKS.md", "- AC: AC-T-TWO", "- AC: AC-T-ONE")
        compiled = compiler.as_json(self.project)
        page = dashboard.render(self.project, analytics.report(self.project), compiled)
        card = next(f for f in holders(page) if "Criteria passed" in f["body"])
        keys = ("done", "total", "fail", "notrun", "later")
        result = with_filters(compiled, f"""
            const card = {json.dumps(card["entries"])};
            const sums = () => Object.fromEntries({json.dumps(keys)}.map(k => [k, aggValue(card[k] || [])]));
            const all = sums();
            const counts = {{}};
            for (const status of ['DONE', 'WIP']) {{
              readFilters('status=' + status);
              counts[status] = sums();
            }}
            console.log(JSON.stringify({{ all, counts }}));""")

        def expected(statuses: tuple[str, ...]) -> dict[str, int]:
            chosen = [t for t in self.project.execution_tasks if t.status in statuses]
            begun = {t.contract for t in chosen if t.status in ("WIP", "DONE") and t.contract}
            later = {t.contract for t in chosen if t.status not in ("WIP", "DONE") and t.contract}

            def count(names: set, state: str | None = None) -> int:
                return sum(1 for name in names for c in self.project.contracts[name].criteria
                           if state is None or c.state == state)
            return {"done": count(begun, "PASS"), "total": count(begun), "fail": count(begun, "FAIL"),
                    "notrun": count(begun, "NOT_RUN"), "later": count(later)}

        self.assertEqual(result["all"], expected(("TODO", "WIP", "DONE", "BLOCKED")))
        self.assertEqual(result["counts"]["DONE"], expected(("DONE",)))
        self.assertEqual(result["counts"]["WIP"], expected(("WIP",)))
        self.assertGreater(result["counts"]["WIP"]["total"], 0)

    def test_shares_round_the_same_way_in_python_and_the_page(self) -> None:
        pairs = [(part, whole) for whole in range(1, 41) for part in range(whole + 1)]
        result = with_filters(self.compiled, f"""
            console.log(JSON.stringify({json.dumps(pairs)}.map(([p, w]) => share(p, w))));""")
        self.assertEqual(result, [dashboard._percent(p, w) for p, w in pairs])
        self.assertEqual(dashboard._percent(1, 8), 13)

    def test_an_entry_with_no_task_counts_only_while_nothing_is_filtered(self) -> None:
        result = with_filters(self.compiled, """
            const entries = [['', 2], ['T-ONE', 1], ['T-NOT-A-TASK', 5]];
            const before = aggValue(entries);
            filters.status.add(byId['T-ONE'].status);
            console.log(JSON.stringify([before, aggValue(entries)]));""")
        self.assertEqual(result, [8, 1])

    def test_attention_comes_before_progress(self) -> None:
        signals = [f for f in re.findall(r'<a class="sig[^"]*"[^>]*>.*?</a>', self.overview)]
        labels = [re.search(r'class="sig-l">([^<]+)<', s).group(1) for s in signals]
        self.assertEqual(labels, ["Blocked tasks", "Failing work", "Needs you", "Review gaps",
                                  "Debt at its trigger", "Unresolved failures"])
        blocked = signals[0]
        self.assertIn(f'data-agg-n="{len(self.compiled["blocked"])}"', blocked)
        for signal in signals:
            count = int(re.search(r'data-agg-n="(\d+)"', signal).group(1))
            self.assertEqual("sig clear" in signal, count == 0)
        order = [self.overview.index(marker) for marker in
                 ('class="attention"', 'class="focus"', 'id="breakdown"', "<h2>Execution progress</h2>")]
        self.assertEqual(order, sorted(order))

    def test_lists_a_filter_narrows_are_marked(self) -> None:
        execution = self.html.split('id="panel-execution"', 1)[1].split('<section class="panel"', 1)[0]
        self.assertIn('<ul class="plain xf-list">', execution)
        self.assertIn('<ol class="chain">', execution)
        self.assertIn('id="needs-filtered" hidden', self.html)
        bar = self.html.split('id="xf-bar"', 1)[1].split("</div>", 1)[0]
        self.assertIn("data-xf-clear>Clear all</button>", bar)

    def test_the_page_stays_self_contained(self) -> None:
        external = re.findall(r'(?:src|href)="(https?:[^"]+)"', self.html)
        self.assertEqual(external, [dashboard.MERMAID_CDN])
        self.assertNotIn("@import", self.html)
        self.assertNotIn("url(", dashboard._STYLE)


class TestThemes(unittest.TestCase):
    MARKS = ("good", "wip", "bad", "todo", "warn", "accent", "ops")

    def test_both_themes_define_every_token(self) -> None:
        themes = _theme_tokens()
        self.assertEqual(set(themes["light"]), set(themes["dark"]))
        self.assertEqual(themes["dark"], themes["system-dark"])

    def test_text_and_marks_meet_wcag_aa_in_both_themes(self) -> None:
        for name, t in _theme_tokens().items():
            for surface in ("bg", "panel", "panel-raised"):
                for text in ("ink", "muted") + self.MARKS:
                    with self.subTest(theme=name, text=text, surface=surface):
                        self.assertGreaterEqual(_contrast(t[text], t[surface]), 4.5)
                with self.subTest(theme=name, border=surface):
                    self.assertGreaterEqual(_contrast(t["line-strong"], t[surface]), 3.0)
            self.assertGreaterEqual(_contrast(t["on-warn"], t["warn"]), 4.5)
            self.assertGreaterEqual(_contrast(t["on-accent"], t["accent"]), 4.5)
            self.assertGreaterEqual(_contrast(t["ink"], t["sunken"]), 4.5)

    def test_tinted_surfaces_keep_text_and_control_borders_readable(self) -> None:
        """Every tint the stylesheet lays under text or controls, composited
        as the browser does, keeps text at 4.5:1 and borders at 3:1."""
        style = dashboard._STYLE + review.STYLE
        tints = set(re.findall(
            r"color-mix\(in srgb, var\(--([\w-]+)\) (\d+)%, (?:var\(--([\w-]+)\)|transparent)\)", style))
        surfaces = [(x, int(n), y or "panel") for x, n, y in tints if int(n) <= 15]
        self.assertGreaterEqual(len(surfaces), 5)
        for name, t in _theme_tokens().items():
            for tint, share, under in surfaces:
                ground = _mix(t[tint], t[under], share / 100)
                with self.subTest(theme=name, surface=f"{tint} {share}% on {under}"):
                    self.assertGreaterEqual(_contrast(t["ink"], ground), 4.5)
                    self.assertGreaterEqual(_contrast(t["muted"], ground), 4.5)
                    self.assertGreaterEqual(_contrast(t["line-strong"], ground), 3.0)

    def test_chart_marks_keep_3_to_1_selected_and_filtered_out(self) -> None:
        """A mark is drawn in its colour whether selected (fill) or filtered
        out (outline); nothing fades it, so its colour must hold 3:1 against
        the track and the panels it sits on."""
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", dashboard._STYLE)
        off = [body for selector, body in rules if ".off" in selector]
        self.assertTrue(off)
        for body in off:
            self.assertNotIn("opacity", body)
        outline = next(body for selector, body in rules if ".seg.off" in selector)
        self.assertIn("var(--mark)", outline)
        for name, t in _theme_tokens().items():
            for mark in self.MARKS:
                for ground in ("sunken", "panel", "panel-raised"):
                    with self.subTest(theme=name, mark=mark, ground=ground):
                        self.assertGreaterEqual(_contrast(t[mark], t[ground]), 3.0)

    def test_the_theme_follows_the_system_until_a_choice_is_stored(self) -> None:
        def boot(stored: str | None, dark: bool) -> str:
            return run_js(
                "const attrs = {}; const document = { documentElement: { className: '', "
                "setAttribute: (k, v) => { attrs[k] = v; } } };\n"
                f"const localStorage = {{ getItem: () => {json.dumps(stored)} }};\n"
                "const location = { hash: '' };\n"
                f"const window = {{ matchMedia: () => ({{ matches: {json.dumps(dark)} }}) }};\n"
                "const matchMedia = window.matchMedia;\n" + dashboard._BOOT
                + "\nconsole.log(JSON.stringify(attrs['data-theme']));")
        self.assertEqual(boot(None, True), "dark")
        self.assertEqual(boot(None, False), "light")
        self.assertEqual(boot("light", True), "light")
        self.assertEqual(boot("dark", False), "dark")
        self.assertIn("@media (prefers-reduced-motion: reduce)", dashboard._STYLE)


class TestLiveNeedsPanel(test_review.TestBlockerGuidance):
    def test_the_count_and_submit_agree_on_what_is_answered(self) -> None:
        """An item counts as answered exactly when submitting would record
        something from it; Feedback without words is never answered."""
        cases = []
        for guide in (False, True):
            for choice in ("CONFIRM", "FEEDBACK"):
                for text in ("", "  ", "Words"):
                    for seen in (False, True):
                        for reviewed in (False, True):
                            cases.append({"target": "A-2", "guide": guide, "choice": choice, "text": text,
                                          "action": "REVISE", "seen": seen, "reviewed": reviewed})
        result = run_js(review.DECIDE_JS + f"""
            console.log(JSON.stringify({json.dumps(cases)}.map(e => {{
              let records;
              try {{ records = reviewItems([e]).length > 0; }} catch (error) {{ records = false; }}
              return [formAnswered(e), records];
            }})));""")
        for case, (answered, records) in zip(cases, result):
            with self.subTest(case=case):
                self.assertEqual(answered, records)
        feedback = next(i for i, c in enumerate(cases) if c["choice"] == "FEEDBACK" and not c["guide"]
                        and c["text"] == "" and c["seen"])
        self.assertEqual(result[feedback], [False, False])

    def test_the_served_panel_holds_the_bar_before_the_cards(self) -> None:
        self.render(interactive=True)
        self.assertIn('<span class="remaining" aria-live="polite"></span>', self.needs)
        # The bar comes before the cards, so focus reaches name and submit first.
        self.assertLess(self.needs.index("review-bar"), self.needs.index('details class="acard"'))
        owner = self.needs.split("<h3>Waiting on the owner</h3>", 1)[1]
        items = re.findall(r"<li>.*?</li>", owner, re.S)
        # The fixture holds an owner item nobody can guide from the page: the
        # count must be able to reach zero without it.
        self.assertTrue(any('class="aform"' not in item for item in items))
        self.assertTrue(any('class="aform"' in item for item in items))
        self.assertNotIn("This page is read-only.", self.needs)

    def test_the_static_panel_says_it_is_read_only(self) -> None:
        self.assertIn("This page is read-only. To answer, run <code>praukron dashboard --serve</code>",
                      self.needs)
        self.assertNotIn("<textarea", self.html)
        self.assertNotIn("/respond", self.html)
        held = [o for o in self.compiled["obstacles"] if o.get("actor") == "owner"]
        report = self.compiled["assumptionReport"]
        waiting = {w["assumption"] for w in report["awaitingReconciliation"]}
        queued = [a for a in report["open"] if a not in waiting]
        to_verify = [task_id for task_id in report["ownerHeld"] if not any(
            response["target"] == task_id and response["action"] == "VERIFY" and response["fromOwner"]
            for response in self.compiled["responses"]
        )]
        self.assertIn(f'data-waiting="{len(held) + len(queued) + len(to_verify)}"', self.html)


# The fixture is borrowed; its own tests run in test_review, not twice.
for _name in dir(test_review.TestBlockerGuidance):
    if _name.startswith("test_") and _name not in vars(TestLiveNeedsPanel):
        setattr(TestLiveNeedsPanel, _name, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
