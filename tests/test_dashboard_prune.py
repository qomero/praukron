"""The dashboard without the parts the new views replace (T-DASH-PRUNE-01).

Work no longer carries the old dependency drawing, its link list, or the Task
graph and Dependency ordering views: the lanes, force and 3D views draw the
graph and its order. Every test reads a small synthetic chronicle; no
project's own records are used.
"""

from __future__ import annotations

import html
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, authority, cli, compile as compiler, drawings, i18n, mermaid, review  # noqa: E402
from test_dashboard_code import CodeDashboardCase  # noqa: E402
from test_dashboard_shell import ShellCase  # noqa: E402

GRAPH_FILES = {"task-graph.mmd", "critical-path.mmd", "phases.mmd", "gates.mmd", "timeline.mmd", "gantt.mmd"}


def workspace(page: str, name: str, following: str | None) -> str:
    start = page.index(f'id="panel-{name}"')
    return page[start:page.index(f'id="panel-{following}"', start)] if following else page[start:]


def markup(page: str) -> str:
    """The page without its scripts and embedded data."""
    return re.sub(r"<script\b[^>]*>.*?</script>", "", page, flags=re.S)


def embedded(page: str, element: str) -> object:
    text = re.search(rf'<script type="application/json" id="{element}">(.*?)</script>', page, re.S).group(1)
    return json.loads(text)


class TestWork(ShellCase):
    def test_the_replaced_parts_are_gone_and_the_rest_of_work_stays(self) -> None:
        page = self.page()
        work = workspace(page, "work", "review")
        for gone in ("Complete dependency graph", 'class="offline-graph"', "<summary>Task dependencies</summary>",
                     'data-diagram="task-graph.mmd"', 'data-diagram="timeline.mmd"', "<h3>Task graph</h3>",
                     "<h3>Dependency ordering</h3>", "ordering-bands", ">Dependency graph</a>"):
            self.assertNotIn(gone, page)
        # The new views, the state board and the views that draw what the
        # lanes do not.
        self.assertIn('<section class="lanes" id="lanes"', work)
        self.assertIn('class="work-board"', work)
        self.assertIn('id="work-view-critical"', work)
        self.assertIn('<ol class="chain">', work)
        for diagram, label in (("critical-path.mmd", "Critical path"), ("phases.mmd", "Phases"),
                               ("gates.mmd", "Gates"), ("gantt.mmd", "Calendar Gantt")):
            self.assertIn(f'data-diagram="{diagram}"', work)
            self.assertIn(f"<h3>{label}</h3>", work)
        self.assertEqual(re.findall(r'data-diagram="([^"]+)"', work),
                         ["critical-path.mmd", "phases.mmd", "gates.mmd", "gantt.mmd"])
        self.assertEqual(work.count('class="diagram-source"'), 4)
        self.assertIn('<a id="work-tab-graph" href="#work-view-graph" data-work-view="work-view-graph"'
                      ' aria-controls="work-view-graph">Views</a>', work)
        self.assertIn('<h2>Phases</h2><div class="phases">', work)
        self.assertIn("<h2>Schedule</h2>", work)
        # The embedded sources carry only the views that remain.
        self.assertEqual(set(embedded(page, "diagram-data")),
                         {"critical-path.mmd", "phases.mmd", "gates.mmd", "gantt.mmd"})
        self.assertEqual(set(drawings.render_all(self.project, analytics.report(self.project))),
                         {"critical-path.mmd", "phases.mmd", "gates.mmd", "gantt.mmd"})

    def test_praukron_graph_writes_the_same_files(self) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(cli.main(["-C", str(self.dir), "graph"]), 0)
        written = {p.name: p.read_text(encoding="utf-8")
                   for p in (self.dir / compiler.COMPILED_DIR).glob("*.mmd")}
        self.assertEqual(set(written), GRAPH_FILES)
        self.assertEqual(written, mermaid.render_all(self.project, analytics.report(self.project)))
        self.assertIn("section Step 1", written["timeline.mmd"])


class TestLinks(ShellCase):
    def test_every_link_reaches_something_on_the_page(self) -> None:
        page = self.page()
        body = markup(page)
        ids = set(re.findall(r'\sid="([^"]+)"', body))
        hrefs = [html.unescape(h) for h in re.findall(r'href="#([^"]+)"', body)]
        self.assertTrue(hrefs)
        self.assertEqual([h for h in hrefs if h not in ids], [])
        data = embedded(page, "project-data")
        tasks = {t["id"] for t in data["tasks"]}
        named = set(re.findall(r'data-task="([^"]+)"', body))
        self.assertTrue(named)
        self.assertLessEqual(named, tasks)
        self.assertEqual([r for r in re.findall(r'data-record="([^"]+)"', body) if r not in ids], [])
        # The side panel's "Open task record in Audit" and the search reach
        # records that exist.
        self.assertLessEqual({f"task-record-{t}" for t in tasks}, ids)
        destinations = {item["destination"] for item in data["searchIndex"]}
        self.assertTrue(destinations)
        self.assertEqual(sorted(destinations - ids), [])
        self.assertIn('data-record="task-record-${esc(id)}"', page)

    def test_links_from_each_workspace_open_the_side_panel_or_the_full_record(self) -> None:
        page = self.page()
        for name, following in (("home", "work"), ("work", "review"), ("review", "audit"), ("audit", None)):
            part = markup(workspace(page, name, following))
            self.assertRegex(part, r'data-task="[^"]+"|href="#task-record-', name)
        self.assertIn("showTask(opener.dataset.task)", page)
        self.assertIn("shSelect(id);", page)

    def test_the_write_controls_are_the_ones_review_and_authority_render(self) -> None:
        """Nothing here changes a write control: the served page's controls are
        rendered, unchanged, by the review and authority modules."""
        page = self.page(interactive=True)
        compiled = self.compiled
        ref = lambda value: value  # noqa: E731
        body, _, count, _ = review.panel_body(compiled, ref, True, mission=True)
        self.assertTrue(count)
        controls = re.findall(r"<(?:button|input|textarea|select|form)\b[^>]*>", body)
        self.assertTrue(controls)
        review_part = workspace(page, "review", "audit")
        self.assertIn('<div id="needs-you"', review_part)
        for control in re.findall(r"<(?:button|input|textarea|select|form)\b[^>]*>", i18n.localize(body, "en")):
            self.assertIn(control, review_part)
        self.assertIn(i18n.localize(authority.section(self.project, True), "en")[:200], page)


class TestDialog(CodeDashboardCase):
    """The dialog stays: a code reference opens its artifact record in it."""

    def test_a_code_reference_still_opens_the_dialog(self) -> None:
        page = self.page()
        self.assertEqual(page.count('<dialog id="detail"'), 1)
        self.assertRegex(markup(page), r'href="#artifact-\d+" data-artifact>')
        self.assertIn("if (artifact) { event.preventDefault(); showArtifact(", page)
        self.assertIn("if (!dialog.open) dialog.showModal();", page)

    def test_tasks_open_in_the_side_panel_with_all_the_dialog_showed(self) -> None:
        page = self.page()
        # Only an artifact opens the dialog; a task goes to the side panel.
        openers = re.findall(r"getElementById\('detail'\)[^;]*;\s*\n\s*const body[^\n]*\n[^\n]*\n\s*copyRecord", page)
        self.assertEqual(len(openers), 1)
        self.assertEqual(page.count(".showModal()"), page.count("dialog.showModal()")
                         + page.count("globalSearch.showModal()") + page.count("shHelp.showModal()"))
        for shown in ("<dt>Dependencies</dt>", "<dt>Acceptance — ", "${pill(c.state)}", "<dt>Obstacles</dt>",
                      "<dt>Decisions</dt>", "<dt>Schedule</dt>", "<dt>Source</dt>", 'class="detail-code"'):
            self.assertIn(shown, page)
        self.assertIn("fillTaskCode(body.querySelector('.detail-code'), id)", page)


if __name__ == "__main__":
    unittest.main()
