"""Home progress semantics and one-level Review regression coverage."""

import html
import json
import re
import sys
import unittest
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard, mission, validate
from test_praukron import FixtureCase
from test_review import ReviewCase
from test_owner_verify import OwnerCase


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


def module_rows(source):
    return {html.unescape(label): (int(done), int(total)) for label, done, total in re.findall(
        r'<span class="module-label">(.*?)</span>.*?<span class="progress-value">(\d+) / (\d+)</span>', source)}


class TestHomeProgress(FixtureCase):
    def page(self, compiled=None):
        data = compiled or compiler.as_json(self.project)
        return dashboard.render(self.project, analytics.report(self.project), data)

    def test_phase_identity_outcome_and_current_modules_lead_home(self):
        data = compiler.as_json(self.project)
        home = self.page(data).split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
        phase = next(p for p in data['phases'] if p['id'] == data['project']['currentPhase'])
        overview = home.split('class="phase-overview"', 1)[1].split('</section>', 1)[0]
        self.assertIn(html.escape(phase['id'] + ' · ' + phase['name'], quote=True), overview)
        self.assertIn(html.escape(phase['outcome'], quote=True), overview)
        for module in data['modules']:
            if module['phase'] == phase['id']:
                selected = [t for t in data['tasks'] if t['module'] == module['id'] and t['domain'] == 'execution']
                label = module['id'] + ' — ' + module['name'] + ' · execution'
                self.assertEqual(module_rows(overview)[label],
                                 (sum(t['status'] == 'DONE' for t in selected), len(selected)))
        self.assertLess(home.index('phase-timeline'), home.index('Next human action'))
        self.assertLess(home.index('Next human action'), home.index('phase-overview'))

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
        modules = module_rows(overview)
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
        work = without_scripts.split('id="panel-work"', 1)[1].split('id="panel-review"', 1)[0]
        self.assertIn('Execution completion by phase', work)
        self.assertIn('Execution status breakdown', work)
        self.assertIn('<svg ', home)
        self.assertNotRegex(home, r'(?:src|href)="https?://')
        self.assertIn('.progress-fill { fill: var(--accent); }', first)

    def test_stacked_status_bar_matches_counts_with_nonzero_legend_and_patterns(self):
        rows = [(state, count, 10) for state, count in zip(mission.STATES, (2, 1, 0, 0, 0, 0, 7))]
        chart = dashboard._status_chart(rows)
        svg = ElementTree.fromstring(re.search(r'<svg class="status-bar".*?</svg>', chart, re.S).group(0))
        self.assertEqual(svg.attrib['role'], 'img')
        self.assertEqual([(float(r.attrib['x']), float(r.attrib['width'])) for r in svg.findall('rect')],
                         [(0, 200), (200, 100), (300, 700)])
        self.assertEqual(len(svg.findall('defs/pattern')), 3)
        self.assertTrue(all(r.attrib['fill'].startswith('url(#home-status-') for r in svg.findall('rect')))
        legend = chart.split('<ul class="status-legend">', 1)[1].split('</ul>', 1)[0]
        self.assertEqual(legend.count('<li>'), 3)
        for state in ('Ready', 'In progress', 'Completed'):
            self.assertIn(state, legend)
        self.assertNotIn('Upcoming', legend)
        self.assertIn('Zero tasks: Upcoming, Waiting for human, Waiting for dependencies, Blocked by failure.', chart)
        self.assertEqual(table_rows(chart, 'Execution status breakdown'), dict((s, (c, t)) for s, c, t in rows))
        self.assertEqual(chart, dashboard._status_chart(rows))

    def test_empty_status_and_phone_chart_rules(self):
        chart = dashboard._status_chart([(state, 0, 0) for state in mission.STATES])
        self.assertNotIn('<svg', chart)
        self.assertNotIn('status-legend', chart)
        self.assertIn('No execution tasks recorded.', chart)
        self.assertEqual(len(table_rows(chart, 'Execution status breakdown')), 7)
        page = self.page()
        phone = dashboard._MISSION_STYLE.split('@media (max-width: 640px)', 1)[1]
        self.assertIn('.progress-chart tr { display: grid; grid-template-columns: minmax(0, 1fr) auto;', phone)
        self.assertIn('vertical-align: middle; border-bottom: 1px solid var(--line)', page)
        self.assertIn('.progress-track { fill: var(--sunken); stroke: var(--line-strong); }', page)
        self.assertIn('html.js .status-table', page)


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
            self.assertIn('downstream', summary)
            for hidden in ('class="pill', '<textarea', 'Applied in:', 'If revised:', 'class="resp"'):
                self.assertNotIn(hidden, summary)
        self.assertIn('class="review-empty">Upcoming decisions <span class="count">0</span> · No items.</p>', review)
        self.assertNotRegex(review, r'<p[^>]*aria-label=')
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


class TestOwnerReviewActions(OwnerCase):
    def render(self, data, interactive=False):
        return dashboard.render(self.project, analytics.report(self.project), data, interactive=interactive)

    def test_decide_until_all_recorded_contract_criteria_pass_then_verify(self):
        task = self.project.task('T-TWO')
        for ready in (False, True):
            with self.subTest(ready=ready):
                for criterion in self.project.mandatory_criteria(task):
                    criterion.state = 'PASS' if ready else 'NOT_RUN'
                data = compiler.as_json(self.project)
                action = next(i for i in data['mission']['review']['now'] if i['id'] == task.id)
                self.assertEqual(action['action'], 'Verify' if ready else 'Decide')
                for interactive in (False, True):
                    card = self.render(data, interactive).split('id="review-T-TWO"', 1)[1].split('</details>', 1)[0]
                    summary = card.split('</summary>', 1)[0]
                    self.assertIn('>Verify</span>' if ready else '>Decide</span>', summary)
                    self.assertIn('The contract is met;' if ready else "The contract is not yet met;", summary)
                    self.assertIn('Affects: T-TWO · 1 downstream', summary)
                    if interactive:
                        self.assertEqual('data-verify="1"' in card, ready)
                        self.assertIn('data-guide="1"', card)
        self.project.contracts[task.contract].criteria.clear()
        action = next(i for i in compiler.as_json(self.project)['mission']['review']['now'] if i['id'] == task.id)
        self.assertEqual(action['action'], 'Decide')

    def test_done_owner_error_is_a_recorded_action_with_exact_command_and_home_count(self):
        task_file = self.authority / 'TASKS.md'
        task_file.write_text(task_file.read_text(encoding='utf-8').replace(
            '- Status: TODO\n- Module: M-FOUNDATION\n- Authority: owner\n- Validation: UNTESTED',
            '- Status: DONE\n- Module: M-FOUNDATION\n- Authority: owner\n- Validation: HUMAN_VERIFIED'), encoding='utf-8')
        self.load()
        task = self.project.task('T-TWO')
        self.assertEqual((task.status, task.validation), ('DONE', 'HUMAN_VERIFIED'))
        data = json.loads(compiler.write(self.dir, self.project).read_text(encoding='utf-8'))
        findings = [f for f in data['validationFindings'] if f['code'] == 'verification-without-owner']
        self.assertEqual(len(findings), 1)
        recorded = next(f for f in validate.check(self.project) if f.code == 'verification-without-owner')
        self.assertEqual(findings[0]['ownerAction'], list(recorded.owner_action))
        self.assertEqual(findings[0]['message'], recorded.message)
        actions = data['mission']['review']['now']
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0]['kind'], actions[0]['target']), ('validation', task.id))
        page = self.render(data)
        review = page.split('id="panel-review"', 1)[1].split('id="panel-audit"', 1)[0]
        card = review.split('id="review-' + actions[0]['id'] + '"', 1)[1].split('</details>', 1)[0]
        for command in recorded.owner_action:
            self.assertIn('<code>' + command + '</code>', card)
        self.assertIn('HUMAN_VERIFIED needs a VERIFY from a named owner', card)
        self.assertIn('<h2>Actionable now <span class="count">1</span>', review)
        self.assertEqual(Disclosures(review).maximum, 1)
        home = page.split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
        others = len(data['mission']['review']['now']) + len(data['mission']['review']['assumptions']) - 1
        self.assertIn(f'+{others} other actions', home)
        self.assertIn('href="#review-' + actions[0]['id'] + '"', home)
        self.assertEqual(page, self.render(compiler.as_json(self.project)))
        # Recorded status and progress stay unchanged; Review exposes the error.
        self.assertIn(task.id, data['mission']['states']['Completed'])
        without_findings = {**data, 'validationFindings': []}
        self.assertEqual(mission.project(without_findings)['review']['now'], [])
        self.project.responses.append(replace(self.project.responses[0], id='R-99', target=task.id,
                                              action='VERIFY', by='thien-owner'))
        self.assertEqual(compiler.as_json(self.project)['mission']['review']['now'], [])

    def test_validation_owner_action_replaces_duplicate_open_task_and_ignores_other_errors(self):
        task = self.project.task('T-TWO')
        task.validation = 'HUMAN_VERIFIED'
        task.dependencies.append('T-MISSING')
        data = compiler.as_json(self.project)
        actions = data['mission']['review']['now']
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['kind'], 'validation')
        self.assertTrue(any(f['code'] == 'unknown-dependency' for f in data['validationFindings']))
        self.assertFalse(any(i['id'] == task.id for group in data['mission']['review'].values() for i in group))


if __name__ == '__main__':
    unittest.main()
