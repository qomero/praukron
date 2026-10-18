"""Timeline-first Home: AC-T-DASH-HOME-03-01 through -04."""

import html
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard
from test_praukron import FixtureCase


class TestHomeTimeline(FixtureCase):
    def render(self):
        data = compiler.as_json(self.project)
        page = dashboard.render(self.project, analytics.report(self.project), data)
        home = page.split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
        return data, page, home

    def test_01_timeline_first_order_states_counts_and_current_destination(self):
        phase = self.project.phases[0]
        self.project.phases.insert(0, replace(phase, id='P0', status='COMPLETE', exit_authority=None))
        self.project.phases.append(replace(phase, id='P2', status='PLANNED', exit_authority=None))
        data, page, home = self.render()
        self.assertRegex(home, r'tabindex="0">\s*<nav class="phase-timeline"')
        timeline = home.split('</nav>', 1)[0]
        self.assertEqual(re.findall(r'href="#phase-([^"]+)"', timeline), ['P0', 'P1', 'P2'])
        for state, symbol in [('Completed', '✓'), ('Current', '●'), ('Upcoming', '○')]:
            self.assertIn(f'class="timeline-step {state.lower()}"', timeline)
            self.assertIn(f'{symbol}</span> {state}', timeline)
        self.assertIn('href="#phase-P1" aria-current="step"', timeline)
        self.assertIn('id="phase-P1"', page)
        for phase in data['phases']:
            self.assertIn(f'{phase["progress"]["done"]} / {phase["progress"]["total"]} done', timeline)

    def test_02_phase_groups_outcome_counts_gate_blocker_and_wip(self):
        self.project.tasks[1].status = 'WIP'
        self.project.tasks[1].owner = 'Builder'
        data, _, home = self.render()
        phase = home.split('class="phase-overview"', 1)[1].split('</section>', 1)[0]
        for value in ['P1 · Foundation', 'The thing works.', '1 / 3', 'M-FOUNDATION',
                      'Next phase gate', '1 exit criteria unmet', 'Root blocker',
                      'Work in progress', 'Responsible: Builder', 'Runtime: unknown']:
            self.assertIn(value, phase)
        self.assertIn('class="module-progress"', phase)
        self.assertNotRegex(phase, r'>\d+%</')
        self.assertIn('Execution completion', home.split('class="home-figures"', 1)[1])

    def test_02_action_names_decide_verify_impact_destination_and_other_count(self):
        self.project.tasks[1].authority = 'owner'
        self.project.tasks[2].authority = 'owner'
        self.project.tasks[2].dependencies = []
        for passed in (False, True):
            for criterion in self.project.mandatory_criteria(self.project.tasks[1]):
                criterion.state = 'PASS' if passed else 'NOT_RUN'
            data, _, home = self.render()
            action = data['mission']['review']['now'][0]
            box = home.split('class="next-action"', 1)[1].split('</section>', 1)[0]
            self.assertIn(html.escape(self.project.task(action['id']).title, quote=True), box)
            self.assertIn(f'>{action["action"]} in Review', box)
            self.assertIn(f'href="#review-{action["id"]}"', box)
            self.assertIn(f'{len(action["affected"])} downstream tasks depend on it', box)
            count = len(data['mission']['review']['now']) + len(data['mission']['review']['assumptions']) - 1
            self.assertIn(f'+{count} other actions', box)

    def test_03_tables_removed_details_retained_and_all_home_links_resolve(self):
        _, page, home = self.render()
        self.assertNotIn('<table', home)
        self.assertNotIn('Most consequential blocker', home)
        self.assertNotIn('Execution completion by phase', home)
        work = page.split('id="panel-work"', 1)[1].split('id="panel-review"', 1)[0]
        for title in ['Execution completion by phase', 'Execution status breakdown',
                      'Root blockers', 'Next milestones', 'Next phase gate', 'Full task registry']:
            self.assertIn(title, work)
        ids = {html.unescape(x) for x in re.findall(r'\bid="([^"]+)"', page)}
        for target in re.findall(r'href="#([^"]+)"', home):
            self.assertIn(html.unescape(target), ids)

    def test_04_phone_dom_order_theme_focus_and_no_script_determinism(self):
        _, page, home = self.render()
        self.assertEqual(page, self.render()[1])
        self.assertLess(home.index('phase-timeline'), home.index('next-action'))
        self.assertLess(home.index('next-action'), home.index('phase-overview'))
        self.assertLess(home.index('phase-overview'), home.index('home-figures'))
        self.assertNotIn('<script', home)
        self.assertNotRegex(home, r'(?:src|href)="https?://')
        self.assertIn('overflow-x: auto; scrollbar-color:', page)
        phone = dashboard._MISSION_STYLE.split('@media (max-width: 640px)', 1)[1]
        self.assertIn('grid-template-areas: "action" "phase" "figures"', phone)
        self.assertIn('a:focus-visible', page)
        self.assertIn(':root[data-theme="light"]', page)
        self.assertIn(':root[data-theme="dark"]', page)
        self.assertIn('border-color: var(--accent); background: var(--panel)', page)

    def test_empty_completed_unphased_and_escaped_content(self):
        data, _, home = self.render()
        self.assertIn('None. No active agent sessions are recorded.', home)
        self.assertIn('+0 other actions', home)
        data['project']['currentPhase'] = None
        self.assertIn('No current phase', dashboard._home_progress(data)[0])
        data['phases'][0]['status'] = 'COMPLETE'
        self.assertNotIn('aria-current', dashboard._phase_timeline(data))
        self.assertIn('Completed', dashboard._phase_timeline(data))
        data['phases'] = []
        self.assertIn('No phases recorded.', dashboard._phase_timeline(data))
        row = dashboard._inline_progress([('Module <script> & long label', 0, 0)])
        self.assertIn('Module &lt;script&gt; &amp; long label', row)
        self.assertNotIn('<script>', row)
        self.assertIn('No tasks', row)
        self.assertIn('0 / 0', row)
        self.assertNotIn('nan', row.lower())


if __name__ == '__main__':
    import unittest
    unittest.main()
