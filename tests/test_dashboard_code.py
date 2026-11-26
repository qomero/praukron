"""Files, symbols and code references in the dashboard (AC-T-CIL-MAP-04).

The task list counts each task's current references, its search finds tasks by
path or symbol, a task record carries a Code References section that the task
dialog shows, and each artifact has a reverse record in Audit. Everything is
checked on the rendered page; the search rule runs under node."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard, i18n, layout  # noqa: E402
from test_code_references import anchored, reference  # noqa: E402
from test_i18n import client_uncovered, island, uncovered  # noqa: E402
from test_praukron import FixtureCase  # noqa: E402

NODE = shutil.which("node")

CODE = "# Code references\n\n" + "\n".join([
    reference("CR-T-ONE-01", "T-ONE", "src/app/core.py", Revision="286b9a7"),
    reference("CR-T-ONE-02", "T-ONE", "src/app/core.py", Symbol="Core.run", Kind="method",
              Relation="creates", Scope="observed", Source="git_diff", Verification="verified"),
    reference("CR-T-ONE-03", "T-ONE", "src/app/util.py", Symbol="Core.run", Kind="function",
              Relation="reads", Authorization="not_applicable", Verification="stale"),
    reference("CR-T-ONE-04", "T-ONE", "src/app/renamed.py", Scope="observed",
              Authorization="not_applicable", Source="git_diff"),
    reference("CR-T-TWO-01", "T-TWO", "src/app/core.py", Relation="reads",
              Authorization="not_applicable"),
    reference("CR-T-TWO-02", "T-TWO", "src/app/gone.py"),
    reference("CR-T-TWO-03", "T-TWO", "src/app/gone.py", Replaces="CR-T-TWO-02",
              Withdrawn="2026-01-03 · no longer needed"),
    reference("CR-T-TWO-04", "T-TWO", "src/app/moved.py"),
    reference("CR-T-TWO-05", "T-TWO", "src/app/placed.py", Replaces="CR-T-TWO-04",
              Was="src/app/moved.py"),
])

# T-ONE: files CR-01, CR-04 and two `Files:` anchors; symbols CR-02, CR-03 and
# two `Symbols:` anchors. T-TWO: CR-01, CR-05 and its anchor; CR-02 and CR-04
# are replaced and CR-03 is withdrawn. T-THREE references nothing.
COUNTS = {"T-ONE": (4, 4), "T-TWO": (3, 0), "T-THREE": (0, 0)}


def registry(page: str) -> str:
    return page[page.index('<table id="task-registry">'):page.index("</table>", page.index('<table id="task-registry">'))]


def record(page: str, task: str) -> str:
    start = page.index(f'<details class="group" id="task-record-{task}">')
    return page[start:page.index("</details>", start)]


def section(page: str, task: str) -> str:
    text = record(page, task)
    return text[text.index('<div class="code-refs">'):]


def group(text: str, label: str) -> str:
    """The rows under one group heading, up to the next group or the table's end."""
    start = text.index(f'<tr class="code-group"><th colspan="7">{label}</th></tr>')
    following = text.find('<tr class="code-group">', start + 1)
    return text[start:following if following > 0 else text.index("</tbody>", start)]


def rows(text: str) -> list[str]:
    return re.findall(r"<tr><td>.*?</tr>", text, re.S)


def artifact(page: str, name: str) -> str:
    for match in re.finditer(r'<article class="artifact" id="(artifact-\d+)">(.*?)</article>', page, re.S):
        if f"<h3><code>{name}</code></h3>" in match.group(2):
            return match.group(0)
    raise AssertionError(f"no artifact record for {name}")


def visible(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


class CodeDashboardCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / layout.AUTHORITY_DIR / "CODE.md").write_text(CODE, encoding="utf-8")
        anchored(self.root)
        self.project = compiler.load(self.root)

    def page(self, lang: str | None = None) -> str:
        project = compiler.load(self.root)
        return dashboard.render(project, analytics.report(project), compiler.as_json(project), lang=lang)


class TestTaskListColumns(CodeDashboardCase):
    """AC-T-CIL-MAP-04-01."""

    def test_files_and_symbols_columns_count_current_references_and_zero(self) -> None:
        table = registry(self.page())
        self.assertIn("<th>Validation</th><th>Files</th><th>Symbols</th></tr>", table)
        found = {}
        for task, cells in re.findall(r'<tr data-task="([^"]+)"[^>]*>(.*?)</tr>', table, re.S):
            numbers = re.findall(r'<td class="num">(\d+)</td>', cells)
            self.assertEqual(len(numbers), 2, task)
            found[task] = tuple(int(n) for n in numbers)
        self.assertEqual(found, COUNTS)

    def test_a_replacement_moves_the_count_to_the_new_record(self) -> None:
        # Withdrawing T-TWO's last current authored file reference lowers its count by one.
        path = self.root / layout.AUTHORITY_DIR / "CODE.md"
        path.write_text(CODE + "\n" + reference("CR-T-TWO-06", "T-TWO", "src/app/placed.py",
                                                Replaces="CR-T-TWO-05", Withdrawn="2026-01-04 · done"),
                        encoding="utf-8")
        table = registry(self.page())
        cells = re.search(r'<tr data-task="T-TWO"[^>]*>(.*?)</tr>', table, re.S).group(1)
        self.assertEqual(re.findall(r'<td class="num">(\d+)</td>', cells), ["2", "0"])


@unittest.skipIf(NODE is None, "node is not installed")
class TestFilter(CodeDashboardCase):
    """AC-T-CIL-MAP-04-02."""

    def shown(self, page: str, queries: list[str]) -> dict[str, list[str]]:
        rows_text = {task: html.unescape(text) for task, text in
                     re.findall(r'<tr data-task="([^"]+)"[^>]*data-text="([^"]*)"', registry(page))}
        source = (dashboard.CODE_FILTER_JS
                  + f"\nconst DATA = {json.dumps(island(page, 'project-data'))};"
                  + f"\nconst notCurrent = new Set({json.dumps(island(page, 'code-not-current'))});"
                  + f"\nconst rows = {json.dumps(rows_text)};"
                  + f"\nconst queries = {json.dumps(queries)};"
                  + "\nconst code = codeIndex(DATA.references, notCurrent);"
                  + "\nconsole.log(JSON.stringify(Object.fromEntries(queries.map(q => [q,"
                  + " Object.keys(rows).filter(id => taskMatches(rows[id], code[id], q))]))));")
        done = subprocess.run([NODE, "-e", source], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_exact_and_partial_paths_and_symbols_leave_only_referencing_tasks(self) -> None:
        page = self.page()
        # The page lowercases the query before it tests a row.
        found = self.shown(page, ["src/app/core.py", "core", "src/app", "core.run",
                                  "src/app/util.py#core.run", "guide", "helper", "placed",
                                  "gone.py", "moved.py", "", "t-three", "nothing/here.py"])
        self.assertEqual(found["src/app/core.py"], ["T-ONE", "T-TWO"])
        self.assertEqual(found["core"], ["T-ONE", "T-TWO"])
        self.assertEqual(found["src/app"], ["T-ONE", "T-TWO"])
        self.assertEqual(found["core.run"], ["T-ONE"])
        self.assertEqual(found["src/app/util.py#core.run"], ["T-ONE"])
        self.assertEqual(found["guide"], ["T-ONE"])   # a `Files:` anchor
        self.assertEqual(found["helper"], ["T-ONE"])  # a `Symbols:` anchor
        self.assertEqual(found["placed"], ["T-TWO"])
        # Only replaced or withdrawn references name these: no task holds them now.
        self.assertEqual(found["gone.py"], [])
        self.assertEqual(found["moved.py"], [])
        self.assertEqual(found[""], ["T-ONE", "T-TWO", "T-THREE"])
        self.assertEqual(found["t-three"], ["T-THREE"])
        self.assertEqual(found["nothing/here.py"], [])

    def test_registry_search_uses_the_rule_and_the_page_without_script_lists_every_task(self) -> None:
        page = self.page()
        self.assertIn("taskMatches(row.dataset.text, codeText[row.dataset.task], q)", dashboard._SCRIPT)
        self.assertIn(dashboard.CODE_FILTER_JS, page)
        self.assertIn('placeholder="Search task ID, title, file or symbol…"', page)
        table = registry(page)
        self.assertEqual(re.findall(r'<tr data-task="([^"]+)"', table), ["T-ONE", "T-TWO", "T-THREE"])
        self.assertNotIn(" hidden", table)
        # The search adds no copy of the references: the page carries only the
        # ids that are no longer current.
        self.assertEqual(island(page, "code-not-current"), ["CR-T-TWO-02", "CR-T-TWO-03", "CR-T-TWO-04"])


class TestTaskDetail(CodeDashboardCase):
    """AC-T-CIL-MAP-04-03."""

    def test_section_groups_planned_and_observed_files_and_symbols_with_every_field(self) -> None:
        text = section(self.page(), "T-ONE")
        self.assertIn("<h4>Code references</h4>", text)
        for label in ("Planned files", "Planned symbols", "Observed files", "Observed symbols"):
            self.assertIn(f'<th colspan="7">{label}</th>', text)
        head = text[:text.index("</thead>")]
        for column in ("Artifact", "Relation", "Authorization", "Verification", "Revision",
                       "Provenance", "Also referenced by"):
            self.assertIn(f"<th>{column}</th>", head)
        planned = rows(group(text, "Planned files"))
        first = visible(next(r for r in planned if "CR-T-ONE-01" in r))
        for value in ("src/app/core.py", "modifies", "proposed", "unverified", "286b9a7",
                      "agent", "2026-01-02 · agent/builder", "T-TWO"):
            self.assertIn(value, first)
        observed = visible(next(r for r in rows(group(text, "Observed symbols")) if "CR-T-ONE-02" in r))
        for value in ("src/app/core.py#Core.run", "creates", "verified", "git_diff"):
            self.assertIn(value, observed)
        self.assertIn("CR-T-ONE-04", group(text, "Observed files"))

    def test_unresolved_stale_replaced_and_withdrawn_are_marked_not_hidden(self) -> None:
        page = self.page()
        one = section(page, "T-ONE")
        stale = visible(next(r for r in rows(group(one, "Planned symbols")) if "CR-T-ONE-03" in r))
        self.assertIn("Stale", stale)
        anchor = [visible(r) for r in rows(group(one, "Planned symbols")) if "helper" in r]
        self.assertEqual(len(anchor), 1)
        self.assertIn("Unresolved", anchor[0])
        two = rows(group(section(page, "T-TWO"), "Planned files"))
        marked = {ref: visible(r) for r in two for ref in re.findall(r"<code>(CR-T-TWO-0\d)</code>", r)[:1]}
        self.assertIn("Replaced by CR-T-TWO-03", marked["CR-T-TWO-02"])
        self.assertIn("Withdrawn 2026-01-03 · no longer needed", marked["CR-T-TWO-03"])
        self.assertIn("Replaced by CR-T-TWO-05", marked["CR-T-TWO-04"])
        self.assertNotIn("Replaced", marked["CR-T-TWO-05"])
        self.assertNotIn("Withdrawn", marked["CR-T-TWO-01"])

    def test_related_tasks_are_other_tasks_with_a_current_reference_to_the_artifact(self) -> None:
        page = self.page()
        core = visible(next(r for r in rows(group(section(page, "T-TWO"), "Planned files"))
                            if "CR-T-TWO-01" in r))
        self.assertIn("T-ONE", core)
        # util.py#Core.run and core.py#Core.run share only a name: two artifacts.
        util = next(r for r in rows(group(section(page, "T-ONE"), "Planned symbols")) if "CR-T-ONE-03" in r)
        self.assertNotIn('data-task="T-TWO"', util)

    def test_many_related_tasks_point_to_the_artifact_record_for_the_rest(self) -> None:
        with mock.patch.object(dashboard, "RELATED_TASKS", 0):
            page = self.page()
        core = next(r for r in rows(group(section(page, "T-TWO"), "Planned files")) if "CR-T-TWO-01" in r)
        self.assertNotIn('data-task="T-ONE"', core)
        anchor = re.search(r'href="#(artifact-\d+)" data-artifact>and 1 more</a>', core)
        self.assertIsNotNone(anchor)
        self.assertIn('data-task="T-ONE"', re.search(rf'id="{anchor.group(1)}">(.*?)</article>', page, re.S).group(1))

    def test_a_task_without_references_says_so_and_the_dialog_shows_the_record(self) -> None:
        # The record of a task with none carries no section; the dialog says so.
        self.assertNotIn("code-refs", record(self.page(), "T-THREE"))
        self.assertIn("No file or symbol is referenced.", dashboard._SCRIPT)
        # The dialog copies the record's section, so both read the same.
        self.assertIn("document.getElementById('task-record-' + id)", dashboard._SCRIPT)
        self.assertIn("record.querySelector('.code-refs')", dashboard._SCRIPT)


class TestArtifactView(CodeDashboardCase):
    """AC-T-CIL-MAP-04-04."""

    def test_current_and_earlier_paths_tasks_and_their_decisions(self) -> None:
        page = self.page()
        placed = artifact(page, "src/app/placed.py")
        text = visible(placed)
        self.assertIn("Current path src/app/placed.py", text)
        self.assertIn("Was src/app/moved.py", text)
        self.assertIn('data-task="T-TWO"', placed)
        self.assertIn('href="#decision-ADR-001"', placed)
        core = artifact(page, "src/app/core.py")
        self.assertIn('data-task="T-ONE"', core)
        self.assertIn('data-task="T-TWO"', core)
        self.assertIn("Was none recorded", visible(core))
        gone = visible(artifact(page, "src/app/gone.py"))
        self.assertIn("Tasks none", gone)
        self.assertIn("No longer current T-TWO", gone)

    def test_every_reference_links_to_its_artifact_inside_audit(self) -> None:
        page = self.page()
        audit = page[page.index('id="panel-audit"'):]
        self.assertIn('<details class="group" id="code-artifacts">', audit)
        anchors = set(re.findall(r'<article class="artifact" id="(artifact-\d+)">', page))
        links = set(re.findall(r'href="#(artifact-\d+)" data-artifact>', page))
        self.assertTrue(links)
        self.assertEqual(links, anchors)
        link = re.search(r'href="#(artifact-\d+)" data-artifact><code>src/app/util.py#Core.run</code>', page)
        self.assertIn("<h3><code>src/app/util.py#Core.run</code></h3>",
                      re.search(rf'id="{link.group(1)}">(.*?)</article>', page, re.S).group(1))
        # A `Symbols:` anchor names no file, so it has no artifact to open.
        self.assertNotIn("<h3><code>helper</code></h3>", page)
        # No fifth workspace.
        self.assertEqual(len(re.findall(r'role="tab"[^>]*data-tab=|<button[^>]*role="tab"', page.split('class="toptabs"')[1].split("</nav>")[0])), 4)


class TestLanguagesAndNoScript(CodeDashboardCase):
    """AC-T-CIL-MAP-04-05."""

    def test_every_new_label_is_catalogued_in_both_languages(self) -> None:
        for lang in ("en", "vi"):
            data = island(self.page(lang), "i18n-data")
            sources = data["texts"] + [v for attrs in data["attributes"] for v in attrs.values()]
            self.assertEqual({uncovered(s).strip() for s in sources if uncovered(s)}, set(), lang)
        for script in (dashboard.CODE_FILTER_JS, dashboard._SCRIPT):
            self.assertEqual(client_uncovered(script), set())
        for label in ("Files", "Symbols", "Code references", "Code artifacts", "Code artifact",
                      "Planned files", "Planned symbols", "Observed files", "Observed symbols",
                      "Relation", "Authorization", "Verification", "Revision", "Provenance",
                      "Also referenced by", "Unresolved", "Stale", "Replaced by", "Withdrawn",
                      "Current path", "Was", "No longer current", "No file or symbol is referenced.",
                      "and {count} more",
                      "Search task ID, title, file or symbol…"):
            self.assertIn(label, i18n.CATALOGUES["vi"]["messages"])
            self.assertNotEqual(i18n.CATALOGUES["vi"]["messages"][label], label)

    def test_vietnamese_page_reads_translated_without_script(self) -> None:
        page = re.sub(r"<script\b.*?</script>", "", self.page("vi"), flags=re.S)
        for text in ("<th>Tệp</th><th>Ký hiệu</th>", "<h4>Tham chiếu mã</h4>", '<th colspan="7">Tệp dự kiến</th>',
                     '<th colspan="7">Ký hiệu ghi nhận thực tế</th>', "<th>Trạng thái cấp phép</th>",
                     "<th>Trạng thái xác minh</th>", "<th>Nguồn gốc</th>", "Đã lỗi thời", "Chưa xác định",
                     "Đã thay bằng", "Đường dẫn hiện tại", "Đường dẫn trước đây", "Các thành phần mã",
                     'placeholder="Tìm mã, tiêu đề, tệp hoặc ký hiệu của công việc…"'):
            self.assertIn(text, page)
        # Paths, symbols, enums and identifiers stay as recorded.
        self.assertIn("<code>src/app/util.py#Core.run</code>", page)
        self.assertIn("<td><code>modifies</code></td>", page)
        self.assertIn("<code>CR-T-TWO-03</code>", page)

    def test_english_page_without_script_shows_the_columns_section_and_artifacts(self) -> None:
        page = re.sub(r"<script\b.*?</script>", "", self.page(), flags=re.S)
        self.assertIn("<th>Files</th><th>Symbols</th>", page)
        self.assertIn("<h4>Code references</h4>", page)
        self.assertIn('<article class="artifact"', page)

    def test_same_chronicle_same_bytes(self) -> None:
        self.assertEqual(self.page(), self.page())
        self.assertEqual(self.page("vi"), self.page("vi"))


if __name__ == "__main__":
    unittest.main()
