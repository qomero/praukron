"""One-click and atomic batch approvals through the served response pathway."""

import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from test_owner_verify import OWNERS_ADR, owner_task
from test_serve import ServeCase
from praukron import analytics, compile as compiler, dashboard, respond, review, serve


def tick_fixture(case):
    owner_task(case)
    (case.authority / 'ADR' / 'ADR-003.md').write_text(OWNERS_ADR, encoding='utf-8')
    acceptance = case.authority / 'ACCEPTANCE.md'
    acceptance.write_text(acceptance.read_text(encoding='utf-8').replace(
        '`TEST` · `NOT_RUN`', '`TEST` · `PASS`\n  - Evidence: Synthetic wall test passed.', 1), encoding='utf-8')
    case.load()


NODE = shutil.which('node')
HARNESS = Path(__file__).with_name('dom_harness.js')
STARTS = [('home', 'one'), ('review', 'one'), ('review', 'batch')]
VOID = {'area', 'br', 'col', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ['html', {}, []]
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag == 'html':
            self.root[1].update({k: v or '' for k, v in attrs})
            return
        node = [tag, {k: v or '' for k, v in attrs}, []]
        self.stack[-1][2].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.stack[-1][0] not in ('script', 'style'):
            self.stack[-1][2].append(data)


def page_tree(page):
    parser = _Tree()
    parser.feed(page)
    return parser.root


class TestDashboardTicks(ServeCase):
    def setUp(self):
        super().setUp()
        tick_fixture(self)

    def tick(self, target, action):
        return {'target': target, 'action': action, 'text': 'Verified.' if action == 'VERIFY' else 'OK'}

    def snapshot(self):
        return {str(p.relative_to(self.dir)): p.read_bytes() for p in self.dir.rglob('*') if p.is_file()}

    def test_one_click_records_identity_and_refresh_moves_item_to_answered(self):
        for target, action in [('T-TWO', 'VERIFY'), ('A-2', 'CONFIRM')]:
            with self.subTest(target=target):
                _, page = self.request('/?token=' + self.token)
                self.assertIn(f'class="approve-one" data-target="{target}" data-action="{action}"', page)
                status, result = self.post({'items': [self.tick(target, action)], 'approveOnly': True})
                self.assertEqual(status, 200, result)
                self.load()
                response = self.project.responses[-1]
                self.assertEqual((response.target, response.action, response.by, response.via),
                                 (target, action, 'thien-owner', 'dashboard'))
                data = compiler.as_json(self.project)
                self.assertIn(target, [i['id'] for i in data['mission']['review']['answered']])
                _, page = self.request('/?token=' + self.token)
                self.assertNotIn(f'class="approve-one" data-target="{target}"', page)

    def test_batch_uses_one_append_and_records_every_selected_item(self):
        items = [self.tick('T-TWO', 'VERIFY'), self.tick('A-2', 'CONFIRM')]
        with mock.patch.object(respond, 'append', wraps=respond.append) as append:
            status, result = self.post({'items': items, 'approveOnly': True})
        self.assertEqual((status, len(result['ids'])), (200, 2))
        append.assert_called_once()
        self.assertTrue(append.call_args.kwargs['approve_only'])
        self.load()
        self.assertEqual([(r.target, r.action) for r in self.project.responses[-2:]],
                         [('T-TWO', 'VERIFY'), ('A-2', 'CONFIRM')])

    def test_refusal_names_item_and_leaves_all_files_unchanged(self):
        # Initialize compiled output before comparing the entire project.
        respond.refresh(self.dir)
        cases = [('T-THREE', 'VERIFY'), ('A-1', 'CONFIRM'), ('A-404', 'CONFIRM'), ('T-TWO', 'CONFIRM')]
        for target, action in cases:
            with self.subTest(target=target, action=action):
                before = self.snapshot()
                status, result = self.post({'items': [self.tick('A-2', 'CONFIRM'), self.tick(target, action)],
                                            'approveOnly': True})
                self.assertEqual(status, 400)
                self.assertIn(target, result['error'])
                self.assertEqual(self.snapshot(), before)

    def test_stale_contract_duplicate_and_wrong_identity_refuse_atomically(self):
        respond.refresh(self.dir)
        items = [self.tick('A-2', 'CONFIRM'), self.tick('T-TWO', 'VERIFY')]
        with mock.patch.object(respond, 'identity', return_value='another-login'):
            before = self.snapshot()
            status, result = self.post({'items': items, 'approveOnly': True})
            self.assertEqual(status, 400)
            self.assertIn('T-TWO', result['error'])
            self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        self.assertEqual(self.post({'items': [items[0], items[0]], 'approveOnly': True})[0], 400)
        self.assertEqual(self.snapshot(), before)
        acceptance = self.authority / 'ACCEPTANCE.md'
        acceptance.write_text(acceptance.read_text(encoding='utf-8').replace(
            '`TEST` · `PASS`\n  - Evidence: Synthetic', '`TEST` · `FAIL`\n  - Evidence: Synthetic'), encoding='utf-8')
        before = self.snapshot()
        status, result = self.post({'items': items, 'approveOnly': True})
        self.assertEqual(status, 400)
        self.assertIn('T-TWO', result['error'])
        self.assertEqual(self.snapshot(), before)

    def test_tick_requires_token_and_same_origin(self):
        before = self.snapshot()
        body = {'items': [self.tick('T-TWO', 'VERIFY')], 'approveOnly': True}
        self.assertEqual(self.post(body, token='wrong')[0], 403)
        self.assertEqual(self.post(body, **{'X-Praukron-Token': ''})[0], 403)
        self.assertEqual(self.post(body, Origin='http://other.example')[0], 403)
        self.assertEqual(self.snapshot(), before)

    def test_static_ticks_disabled_and_no_writer(self):
        project = compiler.load(self.dir)
        page = dashboard.render(project, analytics.report(project), compiler.as_json(project))
        buttons = re.findall(r'<button[^>]*class="approve-(?:one|selected)"[^>]*>', page)
        selections = re.findall(r'<input type="checkbox" data-target="[^"]+"[^>]*>', page)
        self.assertGreaterEqual(len(buttons), 4)  # Home, two Review items, batch.
        self.assertTrue(selections)
        self.assertTrue(all(' disabled' in control for control in buttons + selections))
        self.assertIn('To approve, run <code>praukron dashboard --serve</code>', page)
        self.assertNotIn('fetch(', page)

    def test_decisions_validation_actions_and_answered_items_have_no_tick(self):
        project = compiler.load(self.dir)
        task = project.task('T-TWO')
        project.contracts[task.contract].criteria[0].state = 'NOT_RUN'
        data = compiler.as_json(project)
        page = dashboard.render(project, analytics.report(project), data, interactive=True)
        self.assertNotIn('class="approve-one" data-target="T-TWO"', page)
        self.assertIn('data-target="T-TWO" data-guide="1"', page)
        task.validation = 'HUMAN_VERIFIED'
        data = compiler.as_json(project)
        page = dashboard.render(project, analytics.report(project), data, interactive=True)
        self.assertIn('validation-action', page)
        self.assertNotIn('class="approve-one" data-target="T-TWO"', page)
        self.assertNotIn('class="approve-one" data-target="A-1"', page)

    def test_controls_are_labelled_localized_and_announced(self):
        for lang, words in [('en', ['Approve selected', 'Select A-2', 'Confirm A-2']),
                            ('vi', ['Duyệt các mục đã chọn', 'Chọn A-2', 'Xác nhận A-2'])]:
            page = serve.page(self.dir, lang)
            for word in words:
                self.assertIn(word, page)
            self.assertIn('role="status" aria-live="polite"', page)
            self.assertIn('<label class="approval-select"><input type="checkbox"', page)
            self.assertIn('button:focus-visible', page)
        self.assertIn('controls.map(approvalItem)', review.SERVE_SCRIPT)
        self.assertIn("selections.filter(input => input.checked)", review.SERVE_SCRIPT)

    def test_no_interactive_control_inside_summary(self):
        page = serve.page(self.dir, 'en')
        summaries = re.findall(r'<summary>.*?</summary>', page, re.S)
        self.assertTrue(summaries)
        for summary in summaries:
            self.assertNotRegex(summary, r'<(?:button|input|select|textarea|a)')
            self.assertNotIn('approval-controls', summary)
        for target in ('T-TWO', 'A-2'):
            self.assertRegex(page, r'</details><span class="approval-controls">.*?data-target="%s"' % target)

    def test_tick_sits_inside_its_card_and_stays_on_visible_offpage_cards(self):
        rule = re.search(r'\.tickable:has\(([^)]*)\) > \.approval-controls \{ display: none; \}', review.STYLE)
        self.assertTrue(rule)
        self.assertNotIn('is-offpage', rule.group(1))  # an off-page card is still shown
        # The divider closes the wrapper, after the tick, not the card above it.
        self.assertIn('.tickable > .review-item, #needs-you .tickable > .acard { border-block-end: 0; }', review.STYLE)
        self.assertIn('.tickable:has(> .review-item), #needs-you .tickable { border-block-end: 1px solid', review.STYLE)

    def run_page(self, tab, kind, outcome):
        """Run the served script on the rendered page in a minimal DOM (tests/dom_harness.js)."""
        payload = {'tree': page_tree(serve.page(self.dir, 'en')), 'script': review.script(True),
                   'token': self.token, 'scenario': {'tab': tab, 'kind': kind, 'outcome': outcome}}
        done = subprocess.run([NODE, str(HARNESS)], input=json.dumps(payload), capture_output=True,
                              text=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    @unittest.skipUnless(NODE, 'needs node to run the page script')
    def test_success_is_announced_once_where_the_tick_started(self):
        for tab, kind in STARTS:
            with self.subTest(tab=tab, kind=kind):
                seen = self.run_page(tab, kind, 'ok')
                self.assertTrue(seen['starterExposed'])
                self.assertTrue(seen['reloaded'])
                self.assertEqual(len(seen['regions']), 1, seen['regions'])
                self.assertTrue(seen['regions'][0]['exposed'], seen['regions'][0])

    @unittest.skipUnless(NODE, 'needs node to run the page script')
    def test_refusal_returns_focus_to_the_control_that_started_it(self):
        for tab, kind in STARTS:
            with self.subTest(tab=tab, kind=kind):
                seen = self.run_page(tab, kind, 'refused')
                self.assertFalse(seen['reloaded'])
                self.assertTrue(seen['activeIsStarter'], seen['active'])
                self.assertFalse(seen['active']['disabled'])
                self.assertEqual(len(seen['refusals']), 1, seen['refusals'])
                self.assertEqual(seen['regions'], [])
