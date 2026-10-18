"""Timeline-first Home: AC-T-DASH-HOME-03-01 through -04."""

import html
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard
from test_praukron import FixtureCase
from test_dashboard_redesign import run_js
from test_review import ReviewCase


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
        module = self.project.modules[0]
        self.project.modules.extend([replace(module, id='M-PAST', phase='P0'),
                                     replace(module, id='M-FUTURE', phase='P2')])
        task = self.project.tasks[0]
        self.project.tasks.extend([replace(task, id='T-PAST', module='M-PAST', phase='P0'),
                                   replace(task, id='T-FUTURE', module='M-FUTURE', phase='P2', status='TODO'),
                                   replace(task, id='T-FUTURE-2', module='M-FUTURE', phase='P2', status='TODO')])
        data, page, home = self.render()
        self.assertRegex(home, r'tabindex="0">\s*<nav class="phase-timeline"')
        timeline = home.split('</nav>', 1)[0]
        self.assertEqual(re.findall(r'href="#phase-([^"]+)"', timeline), ['P0', 'P1', 'P2'])
        for state, symbol in [('Completed', '✓'), ('Current', '●'), ('Upcoming', '○')]:
            self.assertIn(f'class="timeline-step {state.lower()}"', timeline)
            self.assertIn(f'{symbol}</span> {state}', timeline)
        self.assertIn('href="#phase-P1" aria-current="step"', timeline)
        self.assertIn('id="phase-P1"', page)
        pairs = re.findall(r'<li class="timeline-step [^"]+">.*?<strong>(.*?)</strong>.*?'
                           r'<span class="timeline-count">(\d+) / (\d+) done</span>.*?</li>', timeline)
        self.assertEqual([(pid, int(done), int(total)) for pid, done, total in pairs],
                         [(p['id'], p['progress']['done'], p['progress']['total']) for p in data['phases']])
        self.assertEqual([(done, total) for _, done, total in pairs], [('1', '1'), ('1', '3'), ('0', '2')])

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
            self.assertIn('+1 owner action</a>', box)

    def test_03_tables_removed_details_retained_and_all_home_links_resolve(self):
        # Independent roots share a downstream task: the summary must count the union.
        self.project.tasks[0].status = 'TODO'
        self.project.tasks[0].authority = 'owner'
        self.project.tasks[1].authority = 'owner'
        self.project.tasks[1].dependencies = []
        self.project.tasks[2].dependencies = [self.project.tasks[0].id, self.project.tasks[1].id]
        self.project.phases.append(replace(self.project.phases[0], id='P2', status='PLANNED', exit_authority=None))
        self.project.modules.append(replace(self.project.modules[0], id='M-FUTURE', phase='P2'))
        self.project.tasks.append(replace(self.project.tasks[1], id='T-FUTURE', module='M-FUTURE', phase='P2'))
        data, page, home = self.render()
        self.assertNotIn('<table', home)
        self.assertNotIn('Most consequential blocker', home)
        self.assertNotIn('Execution completion by phase', home)
        work = page.split('id="panel-work"', 1)[1].split('id="panel-review"', 1)[0]
        for title in ['Execution completion by phase', 'Execution status breakdown',
                      'Root blockers', 'Next milestones', 'Next phase gate', 'Full task registry']:
            self.assertIn(title, work)
        mission = data['mission']
        self.assertTrue(mission['affected'])
        self.assertTrue(mission['future'])
        self.assertGreater(sum(r['count'] for r in mission['roots']), len(mission['affected']))
        summary = (f'{sum(r["current"] for r in mission["roots"])} active root blockers · '
                   f'{len(mission["affected"])} distinct downstream tasks · '
                   f'{len(mission["future"])} future owner obligations.')
        self.assertIn('<h2>Root blockers</h2><p class="note">' + summary + '</p>', work)
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
        self.assertNotIn('class="other-actions"', home)
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

    def test_phone_without_javascript_wraps_all_steps(self):
        phone = dashboard._MISSION_STYLE.split('@media (max-width: 640px)', 1)[1]
        self.assertRegex(phone, r'html:not\(\.js\) \.phase-timeline ol\s*\{[^}]*flex-wrap: wrap;[^}]*min-width: 0;')

    def test_current_step_is_centred_using_only_internal_scroll(self):
        positioning = dashboard._MISSION_SCRIPT.split('function reveal(target)', 1)[0]
        self.assertIn('function positionHomeTimeline()', positioning)
        self.assertIn("if (name === 'home') positionHomeTimeline();", dashboard._SCRIPT)
        result = run_js('''
const current = {getBoundingClientRect(){return {left:652,width:160};}};
const strip = {scrollLeft:0,clientWidth:350,querySelector(){return current;},
  getBoundingClientRect(){return {left:12};}};
const document = {querySelector(){return strip;}};
''' + positioning + '''
positionHomeTimeline(); console.log(JSON.stringify(strip.scrollLeft));
''')
        self.assertEqual(result, 545)

    def test_phase_reveal_leaves_room_for_measured_sticky_header(self):
        reveal = dashboard._MISSION_SCRIPT.split('function reveal(target)', 1)[1].split('const globalSearch', 1)[0]
        for header_height in (49, 112):
            result = run_js('''
const target = {hidden:true, style:{}, parentElement:null, tagName:'ARTICLE',
  classList:{remove(){}, contains(){return false;}}, setAttribute(){},
  focus(){}, scrollIntoView(){this.marginAtScroll = this.style.scrollMarginTop || '0px';}};
function clearFilters() {}
const document = {querySelector(){return {getBoundingClientRect(){return {height:HEIGHT};}};}};
function reveal(target) BODY
reveal(target); console.log(JSON.stringify(target.marginAtScroll));
'''.replace('HEIGHT', str(header_height)).replace('BODY', reveal))
            self.assertGreaterEqual(int(result.removesuffix('px')), header_height + 8)


class TestHomeActionCounts(ReviewCase):
    def test_owner_actions_and_assumptions_are_separate_and_pluralised(self):
        # Use real owner and assumption records; vary only how many are actionable.
        self.project.tasks[1].authority = 'owner'
        self.project.tasks[2].authority = 'owner'
        self.project.tasks[2].dependencies = []
        self.project.tasks.append(replace(self.project.tasks[2], id='T-EXTRA'))
        data = compiler.as_json(self.project)
        owners = data['mission']['review']['now']
        assumptions = data['mission']['review']['assumptions']
        self.assertGreaterEqual(len(owners), 3)
        self.assertGreaterEqual(len(assumptions), 2)
        for owner_count, assumption_count, expected in [
            (0, 0, ''), (1, 0, ''), (2, 0, '+1 owner action'),
            (3, 0, '+2 owner actions'),
            (1, 1, '1 assumption to validate'), (1, 2, '2 assumptions to validate'),
            (2, 2, '+1 owner action · 2 assumptions to validate'),
            (0, 1, '1 assumption to validate'),
        ]:
            with self.subTest(owners=owner_count, assumptions=assumption_count):
                data['mission']['review']['now'] = owners[:owner_count]
                data['mission']['review']['assumptions'] = assumptions[:assumption_count]
                page = dashboard.render(self.project, analytics.report(self.project), data)
                home = page.split('id="panel-home"', 1)[1].split('id="panel-work"', 1)[0]
                if expected:
                    self.assertIn(f'<p class="other-actions"><a href="#panel-review">{expected}</a></p>', home)
                else:
                    self.assertNotIn('class="other-actions"', home)


if __name__ == '__main__':
    import unittest
    unittest.main()
