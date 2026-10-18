"""Work board: AC-T-DASH-WORK-02-01 through -04."""

import html
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard
from test_dashboard_redesign import run_js
from test_praukron import FixtureCase


def layout_style(css, selectors, viewport):
    """Resolve matching layout rules in cascade order, including phone overrides."""
    declarations = []

    def rules(text):
        offset = 0
        while '{' in text[offset:]:
            start = text.index('{', offset)
            selector = text[offset:start].strip()
            end, depth = start + 1, 1
            while depth:
                depth += (text[end] == '{') - (text[end] == '}')
                end += 1
            body = text[start + 1:end - 1]
            if selector.startswith('@media'):
                limit = re.search(r'max-width:\s*(\d+)px', selector)
                if limit and viewport <= int(limit.group(1)):
                    rules(body)
            elif not selector.startswith('@'):
                for part in selector.split(','):
                    part = part.strip()
                    if part in selectors:
                        declarations.append((part.count('#'), part.count('.'), len(declarations), body))
            offset = end

    rules(re.sub(r'/\*.*?\*/', '', css, flags=re.S))
    style = {}
    for _, _, _, body in sorted(declarations):
        for item in body.split(';'):
            if ':' not in item:
                continue
            key, value = (s.strip() for s in item.split(':', 1))
            style[key] = value
            if key == 'flex' and len(value.split()) == 3:
                style.update(zip(['flex-grow', 'flex-shrink', 'flex-basis'], value.split()))
    return style


class TestWorkBoard(FixtureCase):
    def populated(self):
        base = self.project.tasks[1]
        self.project.phases.append(replace(self.project.phases[0], id='P2', status='PLANNED', exit_authority=None))
        self.project.modules.append(replace(self.project.modules[0], id='M-FUTURE', phase='P2'))
        self.project.tasks.extend([
            replace(base, id='T-WIP', title='Build <safe> & sound', status='WIP', owner='Builder'),
            replace(base, id='T-OWNER', authority='owner', dependencies=[]),
            replace(base, id='T-BLOCKED', status='BLOCKED', dependencies=[]),
            replace(base, id='T-FUTURE', phase='P2', module='M-FUTURE', authority='owner', dependencies=[]),
        ])

    def render(self):
        data = compiler.as_json(self.project)
        page = dashboard.render(self.project, analytics.report(self.project), data)
        return data, page, page.split('id="panel-work"', 1)[1].split('id="panel-review"', 1)[0]

    def test_01_columns_cards_metadata_and_completed_disclosure(self):
        self.populated()
        data, _, work = self.render()
        columns = re.findall(r'<section class="work-state work-column[^>]*data-state="([^"]+)".*?</section>', work)
        self.assertEqual(columns, [s for s in data['mission']['states'] if s != 'Completed'])
        for state, ids in data['mission']['states'].items():
            section = work.split(f'data-state="{state}"', 1)[1].split('</details>' if state == 'Completed' else '</section>', 1)[0]
            self.assertIn(f'<span data-state-count>{len(ids)}</span>', section)
            self.assertEqual(re.findall(r'data-work-task="([^"]+)"', section), ids)
            for tid in ids:
                card = section.split(f'data-work-task="{tid}"', 1)[1].split('</li>', 1)[0]
                task = next(t for t in data['tasks'] if t['id'] == tid)
                for text in [task['title'], task['module'], 'Authority: owner' if task['authority'] == 'owner'
                             else 'Responsible: ' + (task['owner'] or 'unassigned')]:
                    self.assertIn(html.escape(text), card)
                self.assertIn(f'data-task="{tid}"', card)
                self.assertIn(f'href="#task-record-{tid}"', card)
        self.assertRegex(work, r'<details class="work-state work-completed"[^>]*>')
        self.assertNotRegex(work, r'<details class="work-state work-completed"[^>]*\bopen\b')
        self.assertIn('Runtime: unknown', work)

    def test_01_empty_states_are_compact_labelled_columns(self):
        _, _, work = self.render()
        self.assertIn('work-column is-empty', work)
        self.assertIn('No tasks in in progress for the selected scope.', work)
        for width in (390, 1440):
            self.assert_compact_layout(dashboard._MISSION_STYLE, width)
            # These are the old stretching bug and a widening regression.
            for mutation in ('.work-board { align-items: stretch; }',
                             '.work-column.is-empty { flex: 1 0 100px; }'):
                with self.assertRaises(AssertionError):
                    self.assert_compact_layout(dashboard._MISSION_STYLE + mutation, width)

    def assert_compact_layout(self, css, width):
        board = layout_style(css, {'.work-board'}, width)
        empty = layout_style(css, {'.work-state', '.work-column', '.work-column.is-empty'}, width)
        align = empty.get('align-self', 'auto')
        self.assertIn(board.get('align-items', 'stretch') if align == 'auto' else align, ('start', 'flex-start'))
        self.assertEqual(empty['flex-grow'], '0')
        self.assertEqual(empty['flex-basis'], 'auto')
        self.assertEqual(empty['width'], 'max-content')
        self.assertEqual(empty.get('height', 'auto'), 'auto')
        self.assertEqual(empty.get('min-width', '0'), '0')

    def test_01_busy_state_has_three_card_preview_and_native_more_disclosure(self):
        base = self.project.tasks[2]
        self.project.tasks.extend(replace(base, id=f'T-WAIT-{i:02}') for i in range(30))
        data, _, work = self.render()
        lane = work.split('data-state="Waiting for dependencies"', 1)[1].split('</section>', 1)[0]
        preview, remainder = lane.split('<details class="work-more">', 1)
        self.assertEqual(len(re.findall(r'data-work-task=', preview)), 3)
        count = len(data['mission']['states']['Waiting for dependencies'])
        self.assertIn(f'<span data-work-more-count>+{count - 3} more</span>', remainder)
        self.assertEqual(len(re.findall(r'data-work-task=', remainder)), count - 3)
        self.assertIn('<ul class="work-cards" data-work-overflow>', remainder)
        self.assertNotIn('<details class="work-more" open', lane)

    def test_01_completed_and_tabpanel_static_labels(self):
        self.populated()
        _, page, work = self.render()
        completed = work.split('data-state="Completed"', 1)[1].split('</details>', 1)[0]
        self.assertIn('<span data-completed-scope>overall</span>', completed)
        self.assertIn('data-work-completed="true" data-work-phase="P1"', completed)
        for tag in re.findall(r'<section class="work-view[^>]+>', page):
            self.assertEqual(len(re.findall(r'aria-label(?:ledby)?=', tag)), 1)
            self.assertIn('aria-labelledby="work-tab-', tag)
        for state in ['Waiting for dependencies', 'Blocked by failure']:
            self.assertIn(f'title="{state}"', work)

    def test_02_context_pair_and_five_anchored_views_without_javascript(self):
        _, _, work = self.render()
        board, completed, context, tabs = [work.index(x) for x in
            ['class="work-board"', 'work-completed', 'class="work-context"', 'class="work-tabs"']]
        self.assertLess(board, completed)
        self.assertLess(completed, context)
        self.assertLess(context, tabs)
        pair = work[context:tabs]
        self.assertIn('id="work-blockers-title"', pair)
        self.assertIn('id="work-gate-title"', pair)
        self.assertIn('Next milestones', pair)
        destinations = re.findall(r'href="#(work-view-[^"]+)"', work)
        self.assertEqual(destinations, ['work-view-' + x for x in ['critical', 'phases', 'registry', 'graph', 'breakdown']])
        for target in destinations:
            self.assertRegex(work, rf'<section class="work-view[^"]*" id="{target}"')
        self.assertEqual(work.count('class="work-view is-selected"'), 1)
        self.assertIn('html.js .work-view:not(.is-selected) { display: none; }', dashboard._MISSION_STYLE)
        self.assertIn('id="panel-tasks"', work)
        self.assertIn('id="panel-graph"', work)

    def test_02_03_keyboard_tabs_select_one_view_and_keep_focus(self):
        script = dashboard._MISSION_SCRIPT.split('function selectWorkView', 1)[1].split('scopeButtons.forEach(button => button.addEventListener', 1)[0]
        result = run_js("""
          const names = ['critical', 'phases', 'registry', 'graph', 'breakdown'];
          const views = names.map(name => ({id: 'work-view-' + name, selected: name === 'critical',
            setAttribute(k, v) {this[k] = v;}, classList: {toggle(k, on) {views.find(v => v.classList === this).selected = on;}}}));
          let focused = null, prevented = 0, hash = '';
          const tabs = names.map(name => ({id: 'work-tab-' + name, dataset: {workView: 'work-view-' + name}, handlers: {},
            setAttribute(k, v) {this[k] = v;}, focus() {focused = this.id;}, addEventListener(k, fn) {this.handlers[k] = fn;}}));
          const document = {querySelectorAll(s) {return s === '[data-work-view]' ? tabs : views;},
            querySelector(s) {return s === '.work-tabs' ? {setAttribute() {}} : views.find(v => v.selected);},
            getElementById(id) {return views.find(v => v.id === id);}};
          const history = {replaceState(a, b, value) {hash = value;}};
          function hashFor(name) {return name + '?status=TODO';}
          function key(index, value) {tabs[index].handlers.keydown({key: value, preventDefault() {prevented++;}});}
          function snapshot() {return {selected: tabs.map(t => t['aria-selected']),
            indexes: tabs.map(t => t.tabIndex), views: views.filter(v => v.selected).map(v => v.id), focused, hash};}
        """ + 'function selectWorkView' + script + """
          key(0, 'End'); const end = snapshot();
          key(4, 'ArrowRight'); const wrap = snapshot();
          key(0, 'ArrowLeft'); const left = snapshot();
          key(4, 'Home'); key(0, 'ArrowRight'); const right = snapshot();
          tabs[3].handlers.click({preventDefault() {prevented++;}}); const click = snapshot();
          console.log(JSON.stringify({end, wrap, left, right, click, prevented}));
        """)
        for key, selected in [('end', 4), ('wrap', 0), ('left', 4), ('right', 1), ('click', 3)]:
            state = result[key]
            self.assertEqual(state['selected'], ['true' if i == selected else 'false' for i in range(5)])
            self.assertEqual(state['indexes'], [0 if i == selected else -1 for i in range(5)])
            self.assertEqual(len(state['views']), 1)
            self.assertEqual(state['hash'], '#' + state['views'][0] + '?status=TODO')
        self.assertEqual(result['right']['focused'], 'work-tab-phases')

    def test_03_reveal_opens_inspection_before_focusing_search_or_hash_target(self):
        script = dashboard._MISSION_SCRIPT.split('function reveal(target)', 1)[1].split('const globalSearch', 1)[0]
        result = run_js("""
          const calls = [];
          function clearFilters() {calls.push('clear');}
          function workScope(all) {calls.push('scope:' + all);}
          function selectWorkView(id) {calls.push(id);}
          function selectTab(id) {calls.push(id);}
          const document = {querySelector() {return null;}};
          function element(id, classes, parent, tagName='SECTION') {return {id, tagName, parentElement: parent,
            classList: {contains(x) {return classes.includes(x);}, remove() {}}, closest() {return false;},
            style: {}, setAttribute() {}, focus() {calls.push('focus:' + id);}, scrollIntoView() {calls.push('scroll');}};}
          const panel = element('panel-work', ['panel'], null);
          const view = element('work-view-phases', ['work-view'], panel);
          const detail = element('phase-detail', [], view, 'DETAILS');
          const target = element('phase-P2', [], detail);
        """ + 'function reveal(target)' + script + """
          reveal(target);
          console.log(JSON.stringify({calls, open: detail.open, hidden: target.hidden, margin: target.style.scrollMarginTop}));
        """)
        self.assertEqual(result['calls'], ['clear', 'work-view-phases', 'work', 'focus:phase-P2', 'scroll'])
        self.assertTrue(result['open'])
        self.assertFalse(result['hidden'])
        self.assertEqual(result['margin'], '16px')

    def test_03_inspection_reveal_preserves_filters_but_record_reveal_clears_them(self):
        script = dashboard._MISSION_SCRIPT.split('function reveal(target)', 1)[1].split('const globalSearch', 1)[0]
        result = run_js("""
          let clears = 0;
          function clearFilters() {clears++;}
          function selectWorkView() {}
          const document = {querySelector() {return null;}};
          const target = {id: 'work-view-registry', hidden: true, tagName: 'SECTION', parentElement: null,
            classList: {contains(x) {return x === 'work-view';}, remove() {}}, closest() {return false;},
            style: {}, setAttribute() {}, focus() {}, scrollIntoView() {}};
        """ + 'function reveal(target)' + script + """
          reveal(target); console.log(JSON.stringify({clears, hidden: target.hidden}));
        """)
        self.assertEqual(result, {'clears': 0, 'hidden': False})

    def test_03_scope_refills_preview_counts_overflow_and_counts_completed_phase(self):
        script = dashboard._MISSION_SCRIPT.split('const scopeButtons =', 1)[1].split('function selectWorkView', 1)[0]
        result = run_js("""
          const DATA = {project: {currentPhase: 'P1'}, mission: {relevant: ['NOW3', 'NOW4', 'NOW5', 'NOW6', 'NOW7']}};
          function row(id, order, phase, done=false) {return {dataset: {workTask:id, workOrder:order,
            workPhase:phase, workCompleted:String(done)}, classList: {hidden:false,
              toggle(k,v) {this.hidden=v;}, contains() {return this.hidden;}}};}
          const waiting = Array.from({length:8}, (_,i) => row(i<3 ? 'LATER'+i : 'NOW'+i, i, 'P1'));
          const completed = [row('DONE-NOW',0,'P1',true), row('DONE-PAST',1,'P0',true)];
          function list(rows) {return {rows, append(row) {
            for(const list of lists) list.rows=list.rows.filter(x=>x!==row);
            this.rows.push(row);
          }};}
          const preview=list(waiting.slice(0,3)), overflow=list(waiting.slice(3));
          const lists=[preview,overflow];
          const moreCount={}; const more={hidden:false,open:true,querySelector() {return moreCount;}};
          const doneScope={};
          const groups=[waiting,completed].map((rows,i)=>({dataset:{state:i?'Completed':'Waiting for dependencies',workLimit:'3',completedTotal:'2'},
            count:{},empty:{},classList:{toggle(){}},querySelectorAll(){return i?rows:[...preview.rows,...overflow.rows];},
            querySelector(s){return {'[data-state-count]':this.count,'.scope-empty':this.empty,
              '.work-more':i?null:more,'[data-work-preview]':preview,'[data-work-overflow]':overflow,
              '[data-completed-scope]':i?doneScope:null}[s];}}));
          const buttons=['current','all'].map(workScope=>({dataset:{workScope},setAttribute(){}}));
          const document={getElementById(){return {};},querySelectorAll(s){return s==='[data-work-scope]'?buttons:
            s==='[data-work-task]'?[...waiting,...completed]:s==='.work-column'?[groups[0]]:groups;}};
          function snapshot(){return {preview:preview.rows.map(r=>r.dataset.workTask),
            counts:groups.map(g=>g.count.textContent),more:moreCount.textContent,hidden:more.hidden,
            done:doneScope.textContent,doneVisible:completed.filter(r=>!r.classList.hidden).map(r=>r.dataset.workTask)};}
        """ + 'const scopeButtons =' + script + """
          workScope(false); const current=snapshot();
          workScope(true); const all=snapshot();
          workScope(false); const again=snapshot();
          DATA.mission.relevant=['NOW7'];workScope(false);const small=snapshot();
          console.log(JSON.stringify({current,all,again,small,closed:!more.open}));
        """)
        self.assertEqual(result['current']['preview'], ['NOW3', 'NOW4', 'NOW5'])
        self.assertEqual(result['current']['counts'], [5, 1])
        self.assertEqual(result['current']['more'], '+2 more')
        self.assertEqual(result['current']['done'], 'in P1 · 2 overall')
        self.assertEqual(result['current']['doneVisible'], ['DONE-NOW'])
        self.assertEqual(result['all']['preview'], ['LATER0', 'LATER1', 'LATER2'])
        self.assertEqual(result['all']['counts'], [8, 2])
        self.assertEqual(result['all']['more'], '+5 more')
        self.assertEqual(result['all']['done'], 'overall')
        self.assertEqual(result['again'], result['current'])
        self.assertEqual(result['small']['preview'], ['NOW7'])
        self.assertTrue(result['small']['hidden'])
        self.assertTrue(result['closed'])

    def test_04_deterministic_self_contained_responsive_theme_focus_and_motion(self):
        self.populated()
        _, first, _ = self.render()
        _, second, _ = self.render()
        self.assertEqual(first.encode(), second.encode())
        self.assertNotRegex(first, r'<(?:script|link)[^>]+(?:src|href)="https?://')
        css = dashboard._MISSION_STYLE
        self.assertRegex(css, r'\.work-board \{[^}]*overflow-x: auto')
        self.assertRegex(css, r'\.work-context \{[^}]*minmax\(0, 1fr\) minmax\(0, 1fr\)')
        self.assertIn('@media (max-width: 640px)', css)
        self.assertIn('.work-context { grid-template-columns: minmax(0, 1fr)', css)
        self.assertIn('.work-board:focus-visible', css)
        self.assertIn('@media (prefers-reduced-motion: reduce)', css)
        self.assertIn('background: var(--panel-raised)', css)
