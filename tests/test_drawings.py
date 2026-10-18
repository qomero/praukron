"""Native view/export parity from synthetic authority, without browser libraries."""

from __future__ import annotations

import html
import re
import unittest
from dataclasses import replace
from datetime import datetime
from html.parser import HTMLParser

from test_praukron import FixtureCase
from test_dashboard_redesign import run_js
from praukron import analytics, compile as compiler, dashboard, drawings, mermaid
from praukron.model import Schedule


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes, self.edges, self.steps, self.rows = [], [], {}, []
        self.bars = {}
        self.node = None
        self.step = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "data-step" in a:
            self.step = int(a["data-step"])
            self.steps[self.step] = []
        if "data-node" in a:
            self.node = a["data-node"]
            self.nodes.append(a["data-node"])
            if self.step is not None:
                self.steps[self.step].append(a["data-node"])
            if "data-start" in a:
                self.rows.append((a["data-phase"], a["data-node"], a["data-start"], a["data-finish"]))
        if "data-from" in a:
            self.edges.append((a["data-from"], a["data-to"]))
        if tag == "rect" and a.get("class") == "calendar-bar":
            self.bars[self.node] = (float(a["x"]), float(a["width"]))


class TestNativeViews(FixtureCase):
    def setUp(self):
        super().setUp()
        p = self.project
        p.phases.append(replace(p.phases[0], id="P2", name="Delivery", status="PLANNED", exit_authority=None))
        p.gates.append(replace(p.gates[0], id="Gate B", name="Review", status="GREEN", blocks=["P1 exit", "P2 exit"]))
        p.tasks[1].status = "WIP"
        p.tasks[1].schedule = Schedule(start="2026-09-28", end="2026-10-02")
        p.tasks[2].phase = "P2"
        p.tasks.append(replace(p.tasks[2], id="T-FOUR", title="Parallel finish", dependencies=["T-TWO"], schedule=Schedule()))
        self.report = analytics.report(p)

    def assert_graph_parity(self, drawing, source, expected_nodes, expected_edges):
        parsed = Markup(drawing)
        self.assertEqual(parsed.nodes, expected_nodes)
        self.assertEqual(parsed.edges, expected_edges)
        exported_nodes = re.findall(r'^    (\w+)[\[\{]', source, re.M)
        exported_edges = re.findall(r'^    (\w+) --> (\w+)$', source, re.M)
        self.assertEqual([mermaid.node_id(n) for n in parsed.nodes], exported_nodes)
        self.assertEqual([(mermaid.node_id(a), mermaid.node_id(b)) for a, b in parsed.edges], exported_edges)

    def test_critical_path_matches_mermaid_nodes_and_edges(self):
        self.assert_graph_parity(drawings.critical_path(self.project, self.report),
                                 mermaid.critical_path(self.project, self.report),
                                 ["T-TWO", "T-THREE"], [("T-TWO", "T-THREE")])

    def test_phase_flow_matches_mermaid_nodes_edges_and_progress(self):
        drawing = drawings.phase_flow(self.project, self.report)
        source = mermaid.phase_flow(self.project, self.report)
        self.assert_graph_parity(drawing, source, ["P1", "P2"], [("P1", "P2")])
        for p in self.project.phases:
            self.assertIn(p.status, drawing)
            self.assertIn(str(self.report.phase_progress[p.id]), drawing)
            self.assertIn(str(self.report.phase_progress[p.id]), source)

    def test_gate_graph_matches_mermaid_nodes_and_edges(self):
        self.assert_graph_parity(drawings.gate_graph(self.project, self.report), mermaid.gate_graph(self.project),
                                 ["Gate A", "Gate B", "P1", "P2"],
                                 [("Gate A", "P1"), ("Gate B", "P1"), ("Gate B", "P2")])

    def test_dependency_ordering_matches_mermaid_bands_and_order(self):
        parsed = Markup(drawings.dependency_timeline(self.project, self.report))
        source = mermaid.dependency_timeline(self.project, self.report)
        bands, step = {}, None
        for line in source.splitlines():
            if line.strip().startswith("section Step "):
                step = int(line.split()[-1])
                bands[step] = []
            elif " : " in line:
                nid, value = line.strip().split(" : ")
                bands[step].append(nid)
                self.assertTrue(value.endswith(f'{step - 1}, {step}'))
        self.assertEqual(parsed.steps, {1: ["T-TWO"], 2: ["T-THREE", "T-FOUR"]})
        self.assertEqual(parsed.steps, bands)
        self.assertNotIn("2026-", drawings.dependency_timeline(self.project, self.report))

    def test_calendar_matches_mermaid_phase_row_order_and_dates(self):
        drawing = drawings.calendar_gantt(self.project, self.report)
        parsed = Markup(drawing)
        rows, phase = [], None
        for line in mermaid.calendar_gantt(self.project, self.report).splitlines():
            if line.strip().startswith("section "):
                phase = line.strip()[8:]
            elif " : " in line:
                nid, values = line.strip().split(" : ")
                start, finish = values.split(", ")
                rows.append((phase, nid, start, finish))
        self.assertEqual(rows, [("P1", "T-TWO", "2026-09-28", "2026-10-02"),
                                ("P2", "T-THREE", "2026-10-01", "3d")])
        self.assertEqual(parsed.rows, rows)
        self.assertEqual(drawings.interval(self.report.scheduled[1]),
                         (datetime(2026, 10, 1), datetime(2026, 10, 4)))
        self.assertIn("2026-10-04", drawing)
        self.assertNotIn('data-node="T-FOUR"', drawing)
        # Six-day axis: T-TWO starts at zero and lasts four days; T-THREE
        # starts halfway through and lasts three. Check the visible geometry.
        self.assertEqual(set(parsed.bars), {"T-TWO", "T-THREE"})
        for nid, expected in {"T-TWO": (180, 700 * 4 / 6),
                              "T-THREE": (180 + 700 * 3 / 6, 700 * 3 / 6)}.items():
            for actual, value in zip(parsed.bars[nid], expected):
                self.assertAlmostEqual(actual, value, places=3)

    def test_deterministic_named_drawings_and_adjacent_sources(self):
        first = drawings.render_all(self.project, self.report)
        self.assertEqual(first, drawings.render_all(self.project, analytics.report(self.project)))
        page = dashboard.render(self.project, self.report, compiler.as_json(self.project))
        for name, drawing in first.items():
            from praukron import i18n
            self.assertIn(i18n.localize(drawing, 'en'), page)
            self.assertIn(html.escape(mermaid.render_all(self.project, self.report)[name], quote=True), page)
            self.assertIn('aria-label', drawing)
        self.assertNotRegex(page, r'<(?:script|link)[^>]+(?:src|href)=["\']https?://')
        self.assertNotIn('window.mermaid', page)
        self.assertIn('WIP</text>', first['critical-path.mmd'])
        self.assertIn('RED</text>', first['gates.mmd'])

    def test_empty_completed_and_undated_states(self):
        p = self.project
        for t in p.tasks:
            t.schedule = Schedule()
        r = analytics.report(p)
        self.assertIn('drawing-empty', drawings.calendar_gantt(p, r))
        self.assertNotIn('<svg', drawings.calendar_gantt(p, r))
        # Undated tasks still have an honest dependency order, not invented dates.
        self.assertEqual(Markup(drawings.dependency_timeline(p, r)).nodes, ['T-TWO', 'T-THREE', 'T-FOUR'])
        for t in p.tasks:
            t.status = 'DONE'
        r = analytics.report(p)
        self.assertIn('drawing-empty', drawings.critical_path(p, r))
        self.assertIn('drawing-empty', drawings.dependency_timeline(p, r))
        p.tasks, p.phases, p.gates = [], [], []
        for drawing in drawings.render_all(p, analytics.report(p)).values():
            self.assertIn('drawing-empty', drawing)
            self.assertNotIn('<svg', drawing)

    def test_authored_text_is_escaped_and_long_labels_wrap(self):
        self.project.tasks[1].title = '<script>alert("x")</script> & ' + 'long title ' * 20
        drawing = drawings.critical_path(self.project, self.report)
        self.assertNotIn('<script>', drawing)
        self.assertIn('&lt;script&gt;', drawing)
        self.assertGreater(drawing.count('<text'), 10)

    def test_invalid_calendar_metadata_never_invents_a_bar(self):
        for entry in ({'start': 'bad', 'end': '2026-10-02'},
                      {'start': '2026-10-03', 'end': '2026-10-02'},
                      {'start': '2026-10-01', 'estimate': 'sometime'}):
            self.assertIsNone(drawings.interval(entry))
        self.assertEqual(drawings.interval({'start': '2026-10-01', 'estimate': '1w'})[1], datetime(2026, 10, 8))

    def test_native_view_tabs_switch_drawings_and_keyboard_focus(self):
        script = dashboard._SCRIPT.split('// Native drawings', 1)[1].split('// --- Cross-filtering', 1)[0]
        result = run_js('''
          const panels = Array.from({length: 6}, () => ({selected: false, classList: {
            toggle(key, value) {this.owner.selected = value;}
          }})); panels.forEach(p => p.classList.owner = p);
          const buttons = panels.map((p, i) => ({tabIndex: -1, attrs: {'aria-controls': String(i)},
            events: {}, setAttribute(k, v) {this.attrs[k] = v;}, getAttribute(k) {return this.attrs[k];},
            addEventListener(k, fn) {this.events[k] = fn;}, focus() {focused = i;}}));
          let focused = null;
          const document = {querySelectorAll: () => buttons, getElementById: i => panels[Number(i)]};
        ''' + '// Native drawings' + script + '''
          buttons[3].events.click();
          const clicked = panels.map(p => p.selected);
          buttons[3].events.keydown({key: 'End', preventDefault() {}});
          const end = focused;
          buttons[5].events.keydown({key: 'ArrowRight', preventDefault() {}});
          console.log(JSON.stringify({clicked, end, wrapped: focused, selected: buttons[0].attrs['aria-selected']}));
        ''')
        self.assertEqual(result, {'clicked': [False, False, False, True, False, False],
                                  'end': 5, 'wrapped': 0, 'selected': 'true'})


if __name__ == '__main__':
    unittest.main()
