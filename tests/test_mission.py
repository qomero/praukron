"""Mission Control and global search, using synthetic records only."""

from __future__ import annotations

import html
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parent)]
from praukron import analytics, compile as compiler, dashboard, mission
from test_praukron import FixtureCase
from test_dashboard_redesign import run_js


def task(tid, deps=(), *, owner=False, status="TODO", phase="P1"):
    return {"id": tid, "title": "Work " + tid, "deps": list(deps), "authority": "owner" if owner else None,
            "status": status, "phase": phase, "source": {"file": "TASKS.md", "anchor": tid}}


def data(*tasks):
    return {"project": {"currentPhase": "P1"}, "tasks": list(tasks), "wip": [t["id"] for t in tasks if t["status"] == "WIP"],
            "execution": {"failures": [], "nextGate": None}, "operations": {"events": [], "unresolvedFailures": []},
            "responses": [], "assumptions": [], "assumptionReport": {"awaitingReconciliation": []}, "gates": []}


class TestRootProjection(unittest.TestCase):
    def test_diamond_shared_impact_and_multiple_roots(self):
        d = data(task("H", owner=True), task("F", status="BLOCKED"), task("L", ["H"]),
                 task("R", ["H", "F"]), task("END", ["L", "R"]), task("DONE", ["H"], status="DONE"))
        p = mission.project(d)
        self.assertEqual([r["id"] for r in p["roots"]], ["H", "F"])
        self.assertEqual(p["roots"][0]["affected"], ["END", "L", "R"])
        self.assertEqual(p["roots"][1]["affected"], ["END", "R"])
        self.assertEqual(p["affected"], ["END", "L", "R"])
        self.assertEqual([r["id"] for r in p["review"]["now"]], ["H"])
        self.assertEqual(p["transitiveHuman"], ["END", "L", "R"])

    def test_future_owner_and_transitive_waits_are_not_actionable(self):
        p = mission.project(data(task("H", owner=True), task("NEXT", ["H"], owner=True),
                                 task("FUTURE", owner=True, phase="P2"), task("AGENT", ["NEXT"])))
        self.assertEqual([r["id"] for r in p["review"]["now"]], ["H"])
        self.assertEqual(p["future"], ["FUTURE", "NEXT"])
        self.assertIn("AGENT", p["states"]["Waiting for dependencies"])
        self.assertNotIn("AGENT", p["states"]["Waiting for human"])

    def test_future_prerequisite_of_current_phase_is_relevant(self):
        p = mission.project(data(task("H", owner=True, phase="P0"), task("NOW", ["H"])))
        self.assertEqual([r["id"] for r in p["review"]["now"]], ["H"])

    def test_ordinary_work_is_not_a_failure_or_root(self):
        p = mission.project(data(task("A"), task("B", ["A"]), task("C", ["B"])))
        self.assertEqual(p["roots"], [])
        self.assertEqual(p["states"]["Ready"], ["A"])
        self.assertEqual(p["states"]["Waiting for dependencies"], ["B", "C"])
        self.assertEqual(p["states"]["Blocked by failure"], [])

    def test_all_states_and_runtime_unknown(self):
        p = mission.project(data(task("READY"), task("RUN", status="WIP"), task("H", owner=True),
                                 task("WAIT", ["H"]), task("FAIL", status="BLOCKED"), task("DONE", status="DONE")))
        self.assertEqual([len(p["states"][s]) for s in mission.STATES], [1] * 6)
        self.assertEqual(p["runtime"], {"RUN": "unknown"})

    def test_missing_dependency_and_cycles_terminate(self):
        p = mission.project(data(task("A", ["MISSING"]), task("B", ["C"]), task("C", ["B"])))
        self.assertEqual(p["roots"][0]["id"], "MISSING")
        self.assertEqual(p["roots"][0]["affected"], ["A"])

    def test_recorded_failures_are_separate_even_behind_another_root(self):
        d = data(task("A", owner=True), task("B", ["A"], status="WIP"), task("C", ["B"]))
        d["execution"]["failures"] = [{"task": "B"}]
        p = mission.project(d)
        self.assertEqual({r["id"] for r in p["roots"]}, {"A", "B"})
        self.assertEqual(p["states"]["Blocked by failure"], ["B"])

    def test_verified_owner_waits_for_agent_reconciliation(self):
        d = data(task("H", owner=True))
        d["responses"] = [{"target": "H", "action": "VERIFY", "fromOwner": True}]
        p = mission.project(d)
        self.assertEqual(p["review"]["now"], [])
        self.assertEqual(p["review"]["answered"][0]["id"], "H")

    def test_no_blockers_empty_and_completed_only(self):
        for d in (data(), data(task("A")), data(task("A", status="DONE"))):
            p = mission.project(d)
            self.assertEqual(p["roots"], [])
            self.assertEqual(p["runtime"], {})
            self.assertEqual(p["review"]["now"], [])

    def test_assumptions_rank_immediacy_before_permission_and_impact(self):
        d = data(task("NOW"), task("LATER", phase="P2"))
        d["assumptions"] = [
            {"id": "A-FUTURE", "tasks": ["LATER"], "status": "OPEN", "permissions": "access", "impact": "HIGH"},
            {"id": "A-NOW", "tasks": ["NOW"], "status": "OPEN", "permissions": "", "impact": "LOW"}]
        p = mission.project(d)
        self.assertEqual([a["id"] for a in p["review"]["assumptions"]], ["A-NOW", "A-FUTURE"])


class TestMissionPage(FixtureCase):
    def page(self):
        d = compiler.as_json(self.project)
        return d, dashboard.render(self.project, analytics.report(self.project), d)

    def test_four_workspaces_no_sidebar_and_all_views_reachable(self):
        d, page = self.page()
        self.assertEqual(re.findall(r'role="tab" id="tab-([^"]+)"', page), ["home", "work", "review", "audit"])
        self.assertNotIn('<aside', page)
        for label in ["Full task registry", "Complete dependency graph", "Critical path", "Architecture decisions",
                      "Operational trace", "Technical debt", "Journal history", "Assumptions requiring validation"]:
            self.assertIn(label, page)

    def test_home_orientation_and_no_inferred_timestamp(self):
        d, page = self.page()
        home = page.split('id="panel-home"')[1].split('id="panel-work"')[0]
        for label in ["Foundation", "The thing works.", "Execution completion", "Validation strength",
                      "Operations activity", "Most consequential blocker", "Next human action", "Next phase gate",
                      "Timestamp not recorded in metadata"]:
            self.assertIn(label, home)
        d["project"]["compiledAt"] = "2026-10-01T12:00:00Z"
        self.assertIn(d["project"]["compiledAt"], dashboard.render(self.project, analytics.report(self.project), d))

    def test_static_determinism_no_network_and_content_in_html(self):
        d, page = self.page()
        self.assertEqual(page, dashboard.render(self.project, analytics.report(self.project), compiler.as_json(self.project)))
        self.assertNotRegex(page, r'<(?:script|link|img)[^>]+(?:src|href)="https?://')
        self.assertNotIn('fetch(', page)
        for task_ in d["tasks"]:
            self.assertIn('id="task-record-' + task_["id"] + '"', page)
            self.assertIn(html.escape(task_["title"]), page)
        self.assertIn('html.js .panel { display: none; }', page)

    def test_every_search_destination_exists_and_sources_are_preserved(self):
        d, page = self.page()
        ids = [html.unescape(x) for x in re.findall(r'\bid="([^"]+)"', page.split('<script type="application/json"')[0])]
        self.assertEqual(len(ids), len(set(ids)), "DOM IDs must be unique")
        for entry in d["searchIndex"]:
            self.assertIn(entry["destination"], ids, entry)
            self.assertIn('#', entry["source"])

    def test_phone_keyboard_theme_and_accessible_search_markup(self):
        _, page = self.page()
        for marker in ['aria-labelledby="search-title"', 'aria-live="polite"', 'aria-controls="global-search"',
                       "globalSearch.showModal()", "event.key === 'Tab'", "event.preventDefault(); globalSearch.close();", 'focus-visible', 'prefers-reduced-motion',
                       '[data-theme="dark"]', '[data-theme="light"]', '@media (max-width: 640px)',
                       '.toptabs [data-tab="review"] { order: -1; }', 'data-work-scope="current"']:
            self.assertIn(marker, page)

    def test_calm_completed_empty_and_no_active_agents(self):
        for t in self.project.tasks:
            t.status = "DONE"
        self.project.current_phase = None
        d, page = self.page()
        self.assertEqual(d["mission"]["roots"], [])
        self.assertIn("No work in progress", page)
        self.assertIn("No active agent sessions are recorded", page)
        self.project.tasks.clear()
        _, page = self.page()
        self.assertIn('Nothing waits for you.', page)

    def test_search_all_record_kinds_case_insensitive_and_exact_id_first(self):
        d, _ = self.page()
        # Search is independent of rendering and supports every required record kind.
        index = [{"kind": k, "id": f"ID-{n}", "title": "Needle", "source": "RECORD.md#anchor",
                  "destination": "record", "text": f"id-{n} needle"}
                 for n, k in enumerate(["Task", "ADR", "Assumption", "Response", "Criterion", "Module", "Phase", "Journal"])]
        index.append({**index[0], "id": "NEEDLE", "text": "needle exact"})
        found = run_js(dashboard.SEARCH_JS + f"console.log(JSON.stringify(searchRecords({json.dumps(index)}, 'NeEdLe')))")
        self.assertEqual(found[0]["id"], "NEEDLE")
        self.assertEqual({x["kind"] for x in found}, {x["kind"] for x in index})
        self.assertEqual(found, run_js(dashboard.SEARCH_JS + f"console.log(JSON.stringify(searchRecords({json.dumps(list(reversed(index)))}, 'needle')))") )
        for query in ("", "not-found"):
            self.assertEqual(run_js(dashboard.SEARCH_JS + f"console.log(JSON.stringify(searchRecords({json.dumps(index)}, '{query}')))") , [])

    def test_journal_capture_preserves_text_and_anchor(self):
        entries = mission.journal('# Journal\n\n## 2026-10-01 — A change\n\nWords & evidence.\n')
        self.assertEqual(entries[0]["text"], 'Words & evidence.')
        self.project.journal = entries
        d, page = self.page()
        result = next(x for x in d["searchIndex"] if x["kind"] == "Journal")
        self.assertEqual(result["source"], 'JOURNAL.md#2026-10-01 — A change')
        self.assertIn('Words &amp; evidence.', page)

    def test_compiled_index_covers_every_record_kind(self):
        import synth_chronicle
        with tempfile.TemporaryDirectory(prefix='praukron-search-') as scratch:
            root = synth_chronicle.build(Path(scratch), phases_count=3, tasks_per_phase=3)
            p = compiler.load(root)
            d = compiler.as_json(p)
            self.assertEqual({e['kind'] for e in d['searchIndex']},
                             {'Task', 'ADR', 'Assumption', 'Response', 'Criterion', 'Module', 'Phase', 'Journal'})
            page = dashboard.render(p, analytics.report(p), d)
            ids = {html.unescape(i) for i in re.findall(r'\bid="([^"]+)"', page)}
            for entry in d['searchIndex']:
                self.assertIn(entry['destination'], ids)


if __name__ == '__main__':
    unittest.main()
