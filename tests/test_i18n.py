"""Frozen AC-T-DASH-I18N-01: offline rendering, choice, coverage and writes."""

import copy
import ast
import html
import io
import json
import re
import shutil
import subprocess
import threading
import unittest
import urllib.request
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

from test_assumptions import AssumptionCase
from praukron import analytics, cli, compile as compiler, dashboard, i18n, review, serve, validate


def island(page, name='i18n-data'):
    return json.loads(re.search(r'id="' + name + r'">(.*?)</script>', page, re.S)[1])


def uncovered(source):
    source = re.sub(i18n.START + '.*?' + i18n.END, '', source, flags=re.S)
    source = re.sub(r'\{\w+\}', '', source)
    source = i18n.message_pattern().sub('', source)
    # Record identifiers, paths, dates and the documented keyboard shortcut
    # are data, not words in the interface catalogue.
    source = re.sub(r'\b[A-Z]+(?:-[A-Z0-9]+)+\b|\bP\d+\b', '', source)
    source = re.sub(r'[\w./-]+\.md(?:#[\w-]+)?|Ctrl K', '', source)
    return source if re.search(r'[A-Za-z]{2,}', source) else ''


def client_literals(script):
    """Read JS strings, skipping comments/regex and template interpolations."""
    literals = []

    def string_at(start):
        quote, pos, parts = script[start], start + 1, []
        while pos < len(script):
            char = script[pos]
            if char == '\\':
                parts.append(script[pos:pos + 2])
                pos += 2
                continue
            if char == quote:
                literals.append(''.join(parts))
                return pos + 1
            if quote == '`' and script[pos:pos + 2] == '${':
                pos, depth = pos + 2, 1
                while pos < len(script) and depth:
                    if script[pos] in "'\"`":
                        pos = string_at(pos)
                        continue
                    depth += (script[pos] == '{') - (script[pos] == '}')
                    pos += 1
                parts.append(' ')
                continue
            parts.append(char)
            pos += 1
        raise AssertionError('Unterminated JavaScript string')

    pos = 0
    while pos < len(script):
        if script.startswith('//', pos):
            end = script.find('\n', pos)
            pos = len(script) if end < 0 else end + 1
        elif script.startswith('/*', pos):
            pos = script.index('*/', pos) + 2
        elif script[pos] in "'\"`":
            pos = string_at(pos)
        elif script[pos] == '/' and script[:pos].rstrip()[-1:] in '(:,=[!&|?':
            # Regex literals may themselves contain quotes or slash classes.
            pos, in_class = pos + 1, False
            while pos < len(script):
                if script[pos] == '\\':
                    pos += 2
                    continue
                if script[pos] == '/' and not in_class:
                    pos += 1
                    break
                if script[pos] in '[]':
                    in_class = script[pos] == '['
                pos += 1
        else:
            pos += 1
    return literals


def client_uncovered(script):
    missing = set()
    tokens = {'ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter', 'End', 'Tab',
              'Escape', 'DETAILS', 'FEEDBACK', 'POST', 'Content-Type', 'X-Praukron-Token'}
    for literal in client_literals(script):
        # Selectors, attribute fragments, identifiers, event names and paths
        # are JS/HTML syntax rather than interface copy.
        if re.search(r'\b[\w-]+=["\']', re.sub(r'<[^>]*>', '', literal)):
            continue
        parser = i18n.Localizer('en')
        parser.feed(literal)
        parser.flush()
        for source in parser.sources + [v for attrs in parser.attributes for v in attrs.values()]:
            source = source.strip()
            if (source in tokens or source.startswith(('(prefers-', '/', '../', ':scope'))
                    or re.fullmatch(r'[\w.-]+/[\w.-]+', source)
                    or re.match(r'^(?:[.#\[]|[a-z]+(?:[.\[:]|\s*(?:>|,)))', source)
                    or re.fullmatch(r'[a-z]+ [a-z]+\[[^]]+\]', source)):
                continue
            if re.fullmatch(r'[a-z][\w.-]*|[#.\[][\w\s.#\[\]="\':>~-]+|[\w/.-]+\.[\w]+', source):
                continue
            if uncovered(source):
                missing.add(uncovered(source).strip())
    return missing


class DashboardLanguageCase(AssumptionCase):
    def page(self, lang=None, interactive=False):
        project = compiler.load(self.dir)
        return dashboard.render(project, analytics.report(project), compiler.as_json(project),
                                lang=lang, interactive=interactive)

    def set_language(self, code):
        with (self.authority / 'THESIS.md').open('a', encoding='utf-8') as stream:
            stream.write('\n- Language: ' + code + '\n')

    def test_recorded_language_is_compiled_and_rendered_without_script(self):
        self.set_language('vi')
        project = compiler.load(self.dir)
        self.assertEqual(project.thesis.language, 'vi')
        self.assertEqual(compiler.as_json(project)['thesis']['language'], 'vi')
        page = re.sub(r'<script\b.*?</script>', '', self.page(), flags=re.S)
        for text in ('<html lang="vi">', '>Trang chủ</button>', '>Công việc</button>',
                     '>Duyệt</button>', '>Kiểm tra</button>', 'Việc cần bạn quyết',
                     'Tìm mã hoặc tiêu đề công việc…', 'aria-label="Chi tiết công việc"'):
            self.assertIn(text, page)
        self.assertNotIn('>Home</button>', page)

    def test_missing_and_unsupported_language_fall_back_and_validation_warns(self):
        self.assertIn('<html lang="en">', self.page())
        self.set_language('unbundled')
        self.assertIn('<html lang="en">', self.page())
        findings = validate.check(compiler.load(self.dir))
        self.assertTrue(any(f.code == 'unsupported-language' and f.severity == 'warning' for f in findings))

    def test_catalogues_are_complete_and_rendered_interface_cannot_bypass_them(self):
        expected = set(i18n.CATALOGUES['en']['messages'])
        for code, catalog in i18n.CATALOGUES.items():
            self.assertEqual(set(catalog['messages']), expected, code)
            self.assertTrue(all(catalog['messages'].values()))
        self.assertEqual({key for key, value in i18n.CATALOGUES['vi']['messages'].items()
                          if key == value}, set())
        for interactive in (False, True):
            data = island(self.page(interactive=interactive))
            sources = data['texts'] + [v for attrs in data['attributes'] for v in attrs.values()]
            missing = {uncovered(s).strip() for s in sources if uncovered(s)}
            self.assertEqual(missing, set())
        self.assertTrue(uncovered('A newly introduced untranslated interface sentence'))

    def test_client_interface_literals_are_catalogued_and_mutations_fail(self):
        scripts = [dashboard.FILTER_JS, dashboard._SCRIPT, dashboard.SEARCH_JS,
                   dashboard._MISSION_SCRIPT, review.script(True), review.script(False)]
        for script in scripts:
            self.assertEqual(client_uncovered(script), set())
        for script, original, mutation in [
            (dashboard._SCRIPT, 'No criteria recorded.', 'Nothing has been written for this contract yet.'),
            (dashboard._MISSION_SCRIPT, 'Search by ID, title or recorded words.', 'Type something to look it up.'),
            (review.SERVE_SCRIPT, 'Recording approval…', 'Saving the selected approvals now.'),
        ]:
            self.assertIn(original, script)
            with self.assertRaises(AssertionError):
                self.assertEqual(client_uncovered(script.replace(original, mutation)), set())

    def test_attribute_translation_respects_left_boundary(self):
        page = i18n.localize('<a data-title="Home" title="Home">Home</a>', 'vi')
        self.assertIn('data-title="Home" title="Trang chủ">Trang chủ</a>', page)

    def test_counts_are_whole_phrases_and_vietnamese_states_are_clear(self):
        self.assertEqual(i18n.translate('40 of 42 execution tasks done', 'vi'), '40/42 công việc đã xong')
        self.assertEqual(i18n.translate('6 of 39 tasks match', 'vi'), '6/39 công việc khớp bộ lọc')
        self.assertEqual(i18n.translate('1 of 1 task matches', 'vi'), '1/1 công việc khớp bộ lọc')
        self.assertEqual(i18n.translate('HUMAN_VERIFIED', 'vi'), 'Chủ dự án đã xác minh')
        self.assertNotIn('hợp đồng', ' '.join(i18n.CATALOGUES['vi']['messages'].values()))

    def test_every_compiled_prose_field_is_protected_by_default(self):
        compiled = compiler.as_json(compiler.load(self.dir))
        words = 'Home Work Review done of all tasks 2026-10-18'
        # Include optional fields that this fixture does not populate yet.
        compiled['debt'].append(dict.fromkeys(('reason', 'interest', 'resolution', 'evidence'), words))
        compiled['execution']['mainBlocker']['title'] = words
        compiled['execution']['mainBlocker']['blockingTitle'] = words
        compiled['thesis']['futureProseField'] = words
        prose = []

        def fill(value, path=()):
            if isinstance(value, dict):
                return {key: fill(child, path + (key,)) for key, child in value.items()}
            if isinstance(value, list):
                return [fill(child, path + (i,)) for i, child in enumerate(value)]
            if isinstance(value, str):
                keys = tuple(key for key in path if isinstance(key, str))
                key = keys[-1]
                if (key in i18n.TECHNICAL_FIELDS or keys in i18n.TECHNICAL_PATHS
                        or keys[:2] in i18n.IDENTIFIER_MAPS
                        or key == 'recorded'
                        or key in {'detail', 'unblock', 'message'}
                        or keys[0] == 'assumptionReport' and key == 'assumption'
                        or keys[-2:] in {('mainBlocker', 'reason'), ('roots', 'reason')}
                        or key == 'note' and 'review' in keys
                        or keys[:2] == ('mission', 'review') and value.startswith('Owner action: ')):
                    return value
                prose.append(path)
                return words
            return value

        filled = fill(compiled)
        protected = i18n.protect(filled)
        self.assertGreater(len(prose), 40)
        for path in prose:
            value = protected
            for key in path:
                value = value[key]
            with self.subTest(path=path):
                self.assertEqual(i18n.localize('<p>' + html.escape(value) + '</p>', 'vi'), '<p>' + words + '</p>')
        self.assertEqual(filled['thesis']['futureProseField'], words)

    def test_compiled_machine_references_and_event_enums_remain_usable(self):
        compiled = compiler.as_json(compiler.load(self.dir))
        compiled['tasks'][0].update(events=['EV-1'], debt=['TD-1'], externalBlockers=['T-TWO'])
        compiled['operations']['events'] = [{'id': 'EV-1', 'title': 'Home', 'outcome': 'failure',
                                            'time': '2026-10-18T10:00:00Z', 'parent': 'EV-0'}]
        compiled['operations']['unresolvedFailures'] = ['EV-1']
        compiled['execution']['externalBlockers'] = {'T-ONE': ['T-TWO']}
        compiled['execution']['failures'] = [{'task': 'T-TWO', 'relatedEvents': ['EV-1'],
                                             'failedEvents': ['EV-1'], 'failedCriteria': ['AC-T-TWO-01']}]
        compiled['mission'].update(transitiveHuman=['T-THREE'], future=['T-THREE'])
        compiled['obstacles'][0]['holders'] = ['T-TWO']
        compiled['assumptionReport']['passRestingOnOpen'] = [{'criterion': 'AC-T-TWO-01', 'assumption': 'A-1'}]
        compiled['phases'][0]['waitsOn'] = 'P0'
        protected = i18n.protect(compiled)
        for field in ('events', 'debt', 'externalBlockers'):
            self.assertEqual(protected['tasks'][0][field], compiled['tasks'][0][field])
        self.assertEqual(protected['execution']['externalBlockers'], {'T-ONE': ['T-TWO']})
        self.assertEqual(protected['operations']['unresolvedFailures'], ['EV-1'])
        for field in ('id', 'outcome', 'time', 'parent'):
            self.assertEqual(protected['operations']['events'][0][field], compiled['operations']['events'][0][field])
        self.assertEqual(protected['operations']['events'][0]['title'], i18n.verbatim('Home'))
        self.assertEqual(protected['execution']['failures'], compiled['execution']['failures'])
        for field in ('transitiveHuman', 'future'):
            self.assertEqual(protected['mission'][field], ['T-THREE'])
        self.assertEqual(protected['obstacles'][0]['holders'], ['T-TWO'])
        self.assertEqual(protected['assumptionReport']['passRestingOnOpen'],
                         [{'criterion': 'AC-T-TWO-01', 'assumption': 'A-1'}])
        self.assertEqual(protected['phases'][0]['waitsOn'], 'P0')

        (self.authority / 'ADR' / 'ADR-003.md').write_text(
            '# ADR-003: Replacement\n- Status: ACCEPTED\n- Supersedes: ADR-001\n', encoding='utf-8')
        (self.authority / 'TRACE.md').write_text(
            '# Trace\n\n## EV-1: Home\n- Type: failure\n- Task: T-TWO\n- Outcome: failure\n', encoding='utf-8')
        project = compiler.load(self.dir)
        compiled = compiler.as_json(project)
        self.assertEqual(compiled['execution']['failures'][0]['relatedEvents'], ['EV-1'])
        for lang, superseded, heading, event_type in (
            ('en', 'Superseded by', 'Execution failures', 'failure'),
            ('vi', 'Được thay thế bởi', 'Lỗi thực hiện', 'thất bại'),
        ):
            page = dashboard.render(project, analytics.report(project), compiled, lang=lang)
            with self.subTest(lang=lang):
                self.assertIn('<dt>' + superseded + '</dt><dd>ADR-003</dd>', page)
                failure = page.split('<h2>' + heading + '</h2>', 1)[1].split('</li>', 1)[0]
                self.assertIn('<code>EV-1</code></a> <span class="pill failure">' + event_type + '</span>', failure)

    def test_generated_owner_explanations_translate_in_every_projection(self):
        project = compiler.load(self.dir)
        task = project.task('T-TWO')
        task.authority, task.validation = 'owner', 'HUMAN_VERIFIED'
        compiled = compiler.as_json(project)
        self.assertTrue(any(root['kind'] == 'human' for root in compiled['mission']['roots']))
        self.assertTrue(any(f['code'] == 'verification-without-owner' for f in compiled['validationFindings']))
        page = dashboard.render(project, analytics.report(project), compiled, lang='vi')
        visible = html.unescape(re.sub(r'<script\b.*?</script>', '', page, flags=re.S))
        self.assertIn('Thẩm quyền của chủ dự án cần người trực tiếp quyết định hoặc xác minh.', visible)
        self.assertNotIn('Owner authority requires a human decision or verification.', visible)
        self.assertIn('Chủ dự án đã xác minh cần phản hồi VERIFY từ chủ dự án đã được khai báo', visible)
        self.assertNotIn('HUMAN_VERIFIED needs a VERIFY from a named owner', visible)

        # The same generated values may be projected through a new path.
        compiled['projection'] = {'finding': {'message': 'No evidence recorded'},
                                  'roots': [{'reason': 'Owner authority requires a human decision or verification.'}],
                                  'mainBlocker': {'reason': 'phase exit'}, 'unblock': 'No evidence recorded'}
        projected = i18n.protect(compiled)['projection']
        self.assertEqual(projected, compiled['projection'])

    def test_other_generated_root_labels_notes_and_runtime_are_translatable(self):
        project = compiler.load(self.dir)
        project.task('T-TWO').dependencies.append('T-MISSING')
        compiled = compiler.as_json(project)
        page = dashboard.render(project, analytics.report(project), compiled, lang='vi')
        visible = re.sub(r'<script\b.*?</script>', '', page, flags=re.S)
        self.assertIn('Thiếu hồ sơ công việc phụ thuộc', visible)
        self.assertIn('Chưa có hồ sơ công việc phụ thuộc.', visible)
        self.assertNotIn('Missing dependency record', visible)
        self.assertNotIn('Dependency record is missing.', visible)

        compiled['mission']['review']['upcoming'].append({
            'kind': 'task', 'note': 'Upcoming owner decision blocking current work'})
        compiled['mission']['runtime'] = {'T-TWO': 'unknown'}
        compiled['assumptionReport']['waitingOnOwner'] = [{'unblock': 'Owner authority requires a human decision or verification.'}]
        protected = i18n.protect(compiled)
        self.assertEqual(protected['mission']['runtime'], {'T-TWO': 'unknown'})
        self.assertEqual(i18n.translate(protected['mission']['review']['upcoming'][-1]['note'], 'vi'),
                         'Quyết định sắp tới của chủ dự án đang chặn công việc hiện tại')
        self.assertEqual(i18n.translate(protected['assumptionReport']['waitingOnOwner'][0]['unblock'], 'vi'),
                         'Thẩm quyền của chủ dự án cần người trực tiếp quyết định hoặc xác minh.')

    def test_static_interface_literals_and_empty_states_are_catalogued(self):
        source_dir = Path(dashboard.__file__).parent
        missing = set()
        for name in ('dashboard.py', 'review.py', 'drawings.py'):
            tree = ast.parse((source_dir / name).read_text(encoding='utf-8'))
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Module)) and n.body
                          and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef):
                    continue
                for node in ast.walk(function):
                    if (not isinstance(node, ast.Constant) or id(node) in docstrings
                            or not isinstance(node.value, str) or not re.search(r'<[a-z][^>]*>', node.value)):
                        continue
                    parser = i18n.Localizer('en')
                    parser.feed(node.value)
                    parser.flush()
                    for text in parser.sources + [v for a in parser.attributes for v in a.values()]:
                        # F-string fragments can begin halfway through an attribute.
                        if re.search(r'\b[\w-]+=["\']', text):
                            continue
                        if uncovered(text):
                            missing.add((name, node.lineno, uncovered(text)))
        self.assertEqual(missing, set())
        for name in ('TASKS', 'PHASES', 'MODULES', 'ASSUMPTIONS', 'RESPONSES'):
            (self.authority / (name + '.md')).write_text('# ' + name + '\n', encoding='utf-8')
        data = island(self.page())
        self.assertEqual({uncovered(s).strip() for s in data['texts'] if uncovered(s)}, set())

    def test_authored_copy_and_embedded_project_are_never_translated(self):
        project = compiler.load(self.dir)
        compiled = compiler.as_json(project)
        words = 'Home Work Review <script>alert(1)</script> & "Evidence" 2026-10-18'
        compiled['tasks'][0]['title'] = words
        compiled['tasks'][0]['evidence'] = words
        compiled['phases'][0]['outcome'] = words
        compiled['acceptance']['AC-T-ONE']['criteria'][0]['text'] = words
        before = copy.deepcopy(compiled)
        page = dashboard.render(project, analytics.report(project), compiled, lang='vi')
        self.assertEqual(compiled, before)
        self.assertEqual(island(page, 'project-data'), before)
        self.assertIn('Home Work Review &lt;script&gt;alert(1)&lt;/script&gt; &amp; "Evidence" 2026-10-18', page)
        visible = re.sub(r'<script\b.*?</script>', '', page, flags=re.S)
        self.assertNotIn('<script>alert(1)</script>', visible)
        self.assertNotIn(i18n.START, visible)

    def test_switcher_bundles_every_language_and_output_is_deterministic(self):
        self.set_language('vi')
        first = self.page()
        self.assertEqual(first, self.page())
        data = island(first)
        self.assertEqual(data['recorded'], 'vi')
        self.assertEqual(set(data['catalogues']), set(i18n.CATALOGUES))
        self.assertIn('for="dashboard-language"', first)
        self.assertIn('<option value="vi" selected>Tiếng Việt</option>', first)
        self.assertNotIn('fetch(', first)
        self.assertIn("try { savedLanguage = localStorage.getItem", first)
        self.assertIn("try { localStorage.setItem(I18N.storageKey", first)

    def test_remembered_language_is_scoped_to_project(self):
        first = island(i18n.localize('<!--i18n-runtime-->', 'vi', 'vi', 'First project'))
        second = island(i18n.localize('<!--i18n-runtime-->', 'vi', 'vi', 'Second project'))
        self.assertNotEqual(first['storageKey'], second['storageKey'])
        storage = {first['storageKey']: 'en'}
        self.assertIsNone(storage.get(second['storageKey']))
        self.assertIn('localStorage.getItem(I18N.storageKey)', i18n.SCRIPT)

    @unittest.skipUnless(shutil.which('node'), 'node needed for client rule parity')
    def test_client_choice_and_translation_match_server_including_placeholders(self):
        script = i18n.SCRIPT
        translate = script[script.index('const protectedText'):script.index('function uiNodes')]
        choose = script[script.index('function chooseLanguage'):script.index('let savedLanguage')]
        values = ['Home', '1 task matches', '2 tasks match', '2026-10-18',
                  '40 of 42 execution tasks done', '6 of 39 tasks match', '1 of 1 task matches',
                  '{fail} fail · {notrun} not run', i18n.verbatim('Home') + ' · Work']
        code = 'const I18N = ' + json.dumps({'catalogues': i18n.CATALOGUES}) + ';\nlet activeLanguage="vi";\n'
        code += translate + choose
        code += '\nconsole.log(JSON.stringify({choice:['
        code += "chooseLanguage(null,'vi','en-US'),chooseLanguage(null,'en','vi-VN'),"
        code += "chooseLanguage('en','vi','vi-VN'),chooseLanguage(null,null,'vi-VN'),"
        code += "chooseLanguage(null,'unsupported','vi-VN'),chooseLanguage('bad',null,'xx')],"
        code += 'translated:' + json.dumps(values) + '.map(x=>translateUI(x))}));'
        result = json.loads(subprocess.run(['node'], input=code, capture_output=True, check=True,
                                           text=True, encoding='utf-8').stdout)
        self.assertEqual(result['choice'], ['vi', 'en', 'en', 'vi', 'en', 'en'])
        self.assertEqual(result['translated'], [i18n.translate(s, 'vi') for s in values])

    def test_cli_override_is_static_and_served(self):
        self.set_language('vi')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['-C', str(self.dir), 'dashboard', '--lang', 'en']), 0)
        page = (self.dir / '.praukron/compiled/dashboard.html').read_text(encoding='utf-8')
        self.assertIn('<html lang="en">', page)
        self.assertEqual(island(page)['recorded'], 'en')
        with mock.patch.object(serve, 'run', return_value=0) as run:
            self.assertEqual(cli.main(['-C', str(self.dir), 'dashboard', '--serve', '--lang', 'en']), 0)
            self.assertEqual(run.call_args.kwargs['lang'], 'en')

    def test_response_path_preserves_actions_and_words_in_both_languages(self):
        for code in ('en', 'vi'):
            with self.subTest(code=code), mock.patch('praukron.respond.identity', return_value='language-test-owner'):
                server, token, url = serve.make_server(self.dir, lang=code)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with urllib.request.urlopen(url) as response:
                        page = response.read().decode()
                    self.assertIn('<html lang="' + code + '">', page)
                    self.assertIn('value="CONFIRM"', page)
                    self.assertIn('Gửi phản hồi' if code == 'vi' else 'Submit review', page)
                    words = 'Giữ nguyên Home & Work — ' + code
                    request = urllib.request.Request(url.split('?')[0] + 'respond',
                        data=json.dumps({'items': [{'target': 'T-TWO', 'action': 'GUIDE', 'text': words}]}).encode(),
                        headers={'Content-Type': 'application/json', 'X-Praukron-Token': token})
                    with urllib.request.urlopen(request) as response:
                        self.assertTrue(json.load(response)['ok'])
                    self.assertEqual(compiler.load(self.dir).responses[-1].text, words)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_dates_counts_styles_and_placeholder_names(self):
        self.assertEqual(i18n.translate('2026-10-18', 'vi'), '18/10/2026')
        self.assertEqual(i18n.translate('2026-10-18', 'en'), '2026-10-18')
        self.assertEqual(i18n.translate('1 task matches', 'vi'), '1 công việc phù hợp')
        self.assertEqual(i18n.translate('2 tasks match', 'vi'), '2 công việc phù hợp')
        self.assertEqual(i18n.translate('{fail} fail · {notrun} not run', 'vi'), '{fail} không đạt · {notrun} chưa chạy')
        self.assertIn('Đã trả lời, chưa gửi', i18n.style())


if __name__ == '__main__':
    unittest.main()
