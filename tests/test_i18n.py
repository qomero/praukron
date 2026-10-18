"""Frozen AC-T-DASH-I18N-01: offline rendering, choice, coverage and writes."""

import copy
import ast
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
from praukron import analytics, cli, compile as compiler, dashboard, i18n, serve, validate


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
        for interactive in (False, True):
            data = island(self.page(interactive=interactive))
            sources = data['texts'] + [v for attrs in data['attributes'] for v in attrs.values()]
            missing = {uncovered(s).strip() for s in sources if uncovered(s)}
            self.assertEqual(missing, set())
        self.assertTrue(uncovered('A newly introduced untranslated interface sentence'))

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
        self.assertIn("try { localStorage.setItem('praukron-language'", first)

    @unittest.skipUnless(shutil.which('node'), 'node needed for client rule parity')
    def test_client_choice_and_translation_match_server_including_placeholders(self):
        script = i18n.SCRIPT
        translate = script[script.index('const protectedText'):script.index('function uiNodes')]
        choose = script[script.index('function chooseLanguage'):script.index('let savedLanguage')]
        values = ['Home', '1 task matches', '2 tasks match', '2026-10-18',
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
