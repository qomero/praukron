"""Phase-first Home and one-level Review (AC-T-DASH-HOME-02-01 through -04)."""

import html
import re
import sys
import unittest
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard
from test_praukron import FixtureCase
from test_review import ReviewCase


class Disclosures(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.depth = self.maximum = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.depth += 1
            self.maximum = max(self.maximum, self.depth)

    def handle_endtag(self, tag):
        if tag == "details":
            self.depth -= 1


def table_rows(source, caption):
    table = source.split('<caption>' + caption + '</caption>', 1)[1].split('</table>', 1)[0]
    return {html.unescape(label): (int(done), int(total)) for label, done, total in re.findall(
        r'<th scope="row">(.*?)</th><td class="progress-value">(\d+) / (\d+)</td>', table)}


class TestHomeProgress(FixtureCase):
    def page(self, compiled=None):
        data = compiled or compiler.as_json(self.project)
        return dashboard.render(self.project, analytics.report(self.project), data)

    def test_phase_identity_outcome_and_current_modules_lead_home(self):
        data = compiler.as_json(self.project)
        home = self.page(data).split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
        phase = next(p for p in data['phases'] if p['id'] == data['project']['currentPhase'])
        overview = home.split('class="phase-overview"', 1)[1].split('</section>', 1)[0]
        self.assertIn(html.escape(phase['id'] + ' — ' + phase['name'], quote=True), overview)
        self.assertIn(html.escape(phase['outcome'], quote=True), overview)
        for module in data['modules']:
            if module['phase'] == phase['id']:
                selected = [t for t in data['tasks'] if t['module'] == module['id'] and t['domain'] == 'execution']
                label = module['id'] + ' — ' + module['name'] + ' · execution'
                self.assertEqual(table_rows(overview, 'Modules in this phase')[label],
                                 (sum(t['status'] == 'DONE' for t in selected), len(selected)))
        self.assertLess(home.index('phase-overview'), home.index('Next human action'))
        self.assertLess(home.index('phase-overview'), home.index('Most consequential blocker'))

    def test_phase_module_and_state_counts_never_mix_domains(self):
        first = self.project.modules[0]
        self.project.phases.append(replace(self.project.phases[0], id='P2', status='PLANNED'))
        self.project.modules.extend([replace(first, id='M-EMPTY'), replace(first, id='M-LATER', phase='P2')])
        self.project.tasks.extend([
            replace(self.project.tasks[0], id='T-OPS', domain='operations', status='DONE'),
            replace(self.project.tasks[0], id='T-LATER', module='M-LATER', phase='P2', status='WIP'),
        ])
        data = compiler.as_json(self.project)
        overview, charts = dashboard._home_progress(data)
        modules = table_rows(overview, 'Modules in this phase')
        label = first.id + ' — ' + first.name
        self.assertEqual(modules[label + ' · execution'], (1, 3))
        self.assertEqual(modules[label + ' · operations'], (1, 1))
        self.assertEqual(modules['M-EMPTY — ' + first.name + ' · execution'], (0, 0))
        self.assertNotIn('M-LATER', overview)
        phases = table_rows(charts, 'Execution completion by phase')
        self.assertEqual(phases['P1 — Foundation'], (1, 3))
        self.assertEqual(phases['P2 — Foundation'], (0, 1))
        states = table_rows(charts, 'Execution status breakdown')
        self.assertEqual(sum(count for count, _ in states.values()), 4)
        self.assertTrue(all(total == 4 for _, total in states.values()))
        self.assertEqual(states['Completed'], (1, 4))

    def test_no_current_phase_empty_modules_and_unphased_execution(self):
        data = compiler.as_json(self.project)
        data['project']['currentPhase'] = None
        overview, _ = dashboard._home_progress(data)
        self.assertIn('No current phase', overview)
        self.assertNotIn('Modules in this phase', overview)
        data['project']['currentPhase'] = 'P1'
        data['modules'] = []
        self.assertIn('No modules recorded for this phase.', dashboard._home_progress(data)[0])
        data['tasks'][0]['phase'] = 'P-NONE'
        self.assertEqual(table_rows(dashboard._home_progress(data)[1], 'Execution completion by phase')['No phase'], (1, 1))

    def test_named_inline_marks_exact_text_empty_and_escaped_labels(self):
        chart = dashboard._progress_chart('A < B', [('Build < & >', 1, 4), ('Empty', 0, 0)])
        self.assertEqual(chart.count('<svg '), 2)
        self.assertEqual(chart.count('role="img"'), 2)
        self.assertEqual(chart.count('<title>'), 2)
        self.assertIn('aria-label="Build &lt; &amp; &gt;: 1 of 4 tasks complete"', chart)
        self.assertIn('width="59.50"', chart)
        self.assertIn('>25%</span>', chart)
        self.assertIn('>No tasks</span>', chart)
        self.assertEqual(table_rows(chart, 'A &lt; B')['Build < & >'], (1, 4))

    def test_repeat_render_no_javascript_dependency_or_external_chart_resource(self):
        first = self.page()
        self.assertEqual(first, self.page())
        without_scripts = re.sub(r'<script\b.*?</script>', '', first, flags=re.S)
        home = without_scripts.split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
        self.assertIn('Modules in this phase', home)
        self.assertIn('Execution completion by phase', home)
        self.assertIn('Execution status breakdown', home)
        self.assertIn('<svg ', home)
        self.assertNotRegex(home, r'(?:src|href)="https?://')
        self.assertIn('.progress-fill { fill: var(--accent); }', first)


class TestLayeredReview(ReviewCase):
    def test_each_item_exposes_title_why_target_action_and_one_disclosure(self):
        self.project.tasks[1].authority = 'owner'
        data = compiler.as_json(self.project)
        page = dashboard.render(self.project, analytics.report(self.project), data)
        review = page.split('id="panel-review"', 1)[1].split('id="panel-audit"', 1)[0]
        self.assertEqual(Disclosures(review).maximum, 1)
        summaries = re.findall(r'<details class="(?:acard|review-item)".*?<summary>(.*?)</summary>', review, re.S)
        self.assertGreater(len(summaries), 2)
        for summary in summaries:
            for part in ('review-title', 'review-why', 'review-target', 'review-action'):
                self.assertIn('class="' + part + '"', summary)
            for hidden in ('class="pill', '<textarea', 'Applied in:', 'If revised:', 'class="resp"'):
                self.assertNotIn(hidden, summary)
        self.assertIn('class="review-empty" aria-label="Upcoming decisions">Upcoming decisions <span class="count">0</span> · No items.</p>', review)
        self.assertIn('<h2>Previously answered <span class="count">2</span>', review)

    def test_records_responses_and_forms_remain_inside_one_level(self):
        self.render(interactive=True)
        self.assertEqual(Disclosures(self.needs).maximum, 1)
        for target in ('A-2', 'A-4'):
            card = self.needs.split('id="assumption-' + target + '"', 1)[1].split('</details>', 1)[0]
            summary, detail = card.split('</summary>', 1)
            self.assertIn('Review response options', summary)
            self.assertIn('data-target="' + target + '"', detail)
            for label in ('Assumed:', 'Why:', 'Applied in:', 'If revised:', 'Responses'):
                self.assertIn(label, detail)
        answered = self.needs.split('id="assumption-A-1"', 1)[1].split('</details>', 1)[0]
        self.assertIn('Inspect reconciliation', answered)
        self.assertNotIn('data-target="A-1"', answered)
        self.assertIn('Mặc định 7 ngày, không phải 30.', answered)
        self.assertIn("fetch('/respond'", self.html)
        self.assertIn('X-Praukron-Token', self.html)


if __name__ == '__main__':
    unittest.main()
