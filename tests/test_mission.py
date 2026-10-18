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
from praukron import analytics, compile as compiler, dashboard, layout, mission
from test_praukron import FixtureCase, DomainCase, _domain_task
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
        self.assertIn("FUTURE", p["states"]["Upcoming"])
        self.assertNotIn("FUTURE", p["states"]["Ready"])
        self.assertIn("NEXT", p["states"]["Waiting for dependencies"])

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
                                 task("WAIT", ["H"]), task("FAIL", status="BLOCKED"), task("DONE", status="DONE"),
                                 task("LATER", owner=True, phase="P2")))
        self.assertEqual([len(p["states"][s]) for s in mission.STATES], [1] * 7)
        self.assertEqual(p["runtime"], {"RUN": "unknown"})

    def test_missing_dependency_and_cycles_terminate(self):
        p = mission.project(data(task("A", ["MISSING"]), task("B", ["C"]), task("C", ["B"])))
        self.assertEqual(p["roots"][0]["id"], "MISSING")
        self.assertEqual(p["roots"][0]["affected"], ["A"])

    def test_recorded_failures_are_separate_even_behind_another_root(self):
        d = data(task("A", owner=True), task("B", ["A"], status="WIP"), task("C", ["B"]))
        d["execution"]["failures"] = [{"task": "B", "failedCriteria": ["AC-B-01"]}]
        p = mission.project(d)
        self.assertEqual({r["id"] for r in p["roots"]}, {"A", "B"})
        self.assertEqual(p["states"]["Blocked by failure"], ["B"])

    def test_verified_owner_waits_for_agent_reconciliation(self):
        d = data(task("H", owner=True))
        d["responses"] = [{"target": "H", "action": "VERIFY", "fromOwner": True}]
        p = mission.project(d)
        self.assertEqual(p["review"]["now"], [])
        self.assertEqual(p["review"]["answered"][0]["id"], "H")
        self.assertEqual(p["states"]["Ready"], ["H"])
        self.assertEqual(p["states"]["Waiting for dependencies"], [])

    def test_later_owner_is_upcoming_even_when_verified_active_or_a_prerequisite(self):
        for status in ("TODO", "WIP"):
            for verified in (False, True):
                with self.subTest(status=status, verified=verified):
                    d = data(task("DONE", status="DONE"),
                             task("LATER", ["DONE"], owner=True, status=status, phase="P2"),
                             task("NOW", ["LATER"]))
                    d["phases"] = [{"id": "P1"}, {"id": "P2"}]
                    if verified:
                        d["responses"] = [{"target": "LATER", "action": "VERIFY", "fromOwner": True}]
                    p = mission.project(d)
                    self.assertEqual(p["states"]["Upcoming"], ["LATER"])
                    self.assertEqual(p["states"]["Ready"], [])
                    self.assertEqual(p["review"]["now"], [])
                    self.assertEqual([r["id"] for r in p["review"]["upcoming"]], ["LATER"])
                    self.assertEqual(p["roots"], [])

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

    def test_recorded_sensitivity_precedes_downstream_cost(self):
        d = data(task("NOW"), task("END", ["NOW"]))
        base = {"tasks": ["END"], "status": "OPEN", "permissions": "", "impact": "LOW"}
        # Only HIGH differs; IDs deliberately favour the ordinary assumption.
        for signal in ({"impact": "HIGH"}, {"permissions": "publish"}):
            d["assumptions"] = [{**base, "id": "A-FIRST"}, {**base, "id": "Z-SENSITIVE", **signal}]
            self.assertEqual([a["id"] for a in mission.project(d)["review"]["assumptions"]],
                             ["Z-SENSITIVE", "A-FIRST"])
            d["assumptions"][0]["tasks"] = ["NOW"]
            self.assertEqual(mission.project(d)["review"]["assumptions"][0]["id"], "Z-SENSITIVE")
        # Permission and HIGH are the same recorded proxy, then downstream wins.
        d["assumptions"] = [{**base, "id": "A-PERM", "permissions": "publish"},
                             {**base, "id": "Z-HIGH", "impact": "HIGH", "tasks": ["NOW"]}]
        self.assertEqual(mission.project(d)["review"]["assumptions"][0]["id"], "Z-HIGH")


class TestCompiledMission(DomainCase):
    def test_successful_retry_recovers_task_and_preserves_other_failures(self):
        self.build([_domain_task(t, "WIP") for t in ("T-1", "T-2", "T-3")],
                   {"T-1": "NOT_RUN", "T-2": "FAIL", "T-3": "NOT_RUN"}, trace=(
                       "## EV-1: Build\n- Type: command\n- Task: T-1\n- Outcome: failure\n\n"
                       "## EV-2: Retry\n- Type: retry\n- Task: T-1\n- Retry of: EV-1\n- Outcome: success\n\n"
                       "## EV-3: Lint\n- Type: command\n- Task: T-3\n- Outcome: failure\n"))
        self.assertEqual(self.compiled["operations"]["unresolvedFailures"], ["EV-3"])
        p = self.compiled["mission"]
        self.assertEqual(p["states"]["In progress"], ["T-1"])
        self.assertEqual(p["states"]["Blocked by failure"], ["T-2", "T-3"])
        self.assertEqual({r["id"] for r in p["roots"]}, {"T-2", "T-3"})

    def test_compiled_recorded_date_and_source_from_live_and_archived_authority(self):
        self.build([_domain_task("T-1")], {"T-1": "NOT_RUN"}, adrs={"ADR-001": "A decision"})
        authority = self.dir / layout.AUTHORITY_DIR
        journal = authority / "JOURNAL.md"
        journal.write_text("# Journal\n\n## 2030-01-01 — First\nRecorded.\n\n"
                           "## 2030-02-01 — Latest\nRecorded.\n", encoding="utf-8")
        archive = authority / "archive" / "P0"
        archive.mkdir(parents=True)
        (archive / "JOURNAL.md").write_text("# Journal\n\n## 2029-01-01 — Earlier\nOld.\n", encoding="utf-8")
        p = compiler.load(self.dir)
        d = compiler.as_json(p)
        self.assertEqual(d["project"]["recordedDate"], {
            "date": "2030-02-01", "source": {"file": "JOURNAL.md", "anchor": "2030-02-01 — Latest"}})
        page = dashboard.render(p, analytics.report(p), d)
        home = page.split('id="panel-home"')[1].split('id="panel-work"')[0]
        self.assertIn("Latest recorded chronicle date", home)
        self.assertIn("JOURNAL.md#2030-02-01 — Latest", home)
        self.assertEqual(page, dashboard.render(compiler.load(self.dir), analytics.report(p), compiler.as_json(compiler.load(self.dir))))
        journal.unlink()
        self.assertEqual(compiler.as_json(compiler.load(self.dir))["project"]["recordedDate"]["source"]["file"],
                         "archive/P0/JOURNAL.md")
        (archive / "JOURNAL.md").unlink()
        self.assertEqual(compiler.as_json(compiler.load(self.dir))["project"]["recordedDate"], {
            "date": "2026-01-01", "source": {"file": "ADR/ADR-001.md", "anchor": "ADR-001"}})

    def test_no_recorded_date_does_not_use_schedule_or_clock(self):
        self.build([_domain_task("T-1") + "- Scheduled start: 2099-01-01\n"], {"T-1": "NOT_RUN"})
        self.assertIsNone(self.compiled["project"]["recordedDate"])
        home = self.html().split('id="panel-home"')[1].split('id="panel-work"')[0]
        self.assertIn("<dd>Not recorded</dd>", home)

    def test_recorded_dates_ignore_prose_examples_invalid_dates_and_break_ties(self):
        records = [("TASKS.md", "## T-1: Work\n- Claimed: 2030-02-01\n"),
                   ("TRACE.md", "## EV-1: Action\n- Time: 2030-02-01T12:00:00Z\n"),
                   ("JOURNAL.md", "## 2030-02-30 — Invalid\nProse 2099-01-01\n"
                    "```\n- Date: 2099-01-01\n```\n")]
        expected = {"date": "2030-02-01", "source": {"file": "TASKS.md", "anchor": "T-1"}}
        self.assertEqual(mission.recorded_date(records), expected)
        self.assertEqual(mission.recorded_date(reversed(records)), expected)


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

    def test_home_orientation_and_recorded_date(self):
        d, page = self.page()
        home = page.split('id="panel-home"')[1].split('id="panel-work"')[0]
        for label in ["Foundation", "The thing works.", "Execution completion", "Validation strength",
                      "Operations activity", "Most consequential blocker", "Next human action", "Next phase gate",
                      "Latest recorded chronicle date"]:
            self.assertIn(label, home)
        self.assertNotIn("compiledAt", d["project"])

    def test_workspace_links_resolve_without_javascript(self):
        _, page = self.page()
        ids = {html.unescape(x) for x in re.findall(r'\bid="([^"]+)"', page)}
        home = page.split('id="panel-home"')[1].split('id="panel-work"')[0]
        for target in re.findall(r'href="#([^"]+)"', home):
            self.assertIn(html.unescape(target), ids)
        self.assertIn('href="#panel-work"', home)
        self.assertIn('href="#panel-review"', home)

    def test_review_names_recorded_proxy_and_work_labels_upcoming(self):
        self.project.tasks[0].phase = "P2"
        self.project.tasks[0].status = "TODO"
        self.project.tasks[0].authority = "owner"
        self.project.tasks[1].dependencies = []
        _, page = self.page()
        self.assertIn("Permission or HIGH-impact sensitivity is the recorded proxy for irreversible consequences", page)
        row = page.split('data-work-task="' + self.project.tasks[0].id + '"')[1].split('</li>')[0]
        self.assertIn("Upcoming: not in current phase", row)
        upcoming = page.split('<summary>Upcoming ·', 1)[1].split('</details>', 1)[0]
        self.assertIn('data-work-task="T-ONE"', upcoming)
        ready = page.split('<summary>Ready ·', 1)[1].split('</details>', 1)[0]
        self.assertNotIn('data-work-task="T-ONE"', ready)
        review = page.split('<summary>Upcoming decisions', 1)[1].split('id="review-T-ONE"', 1)
        self.assertEqual(len(review), 2)

    def test_work_scope_changes_rows_counts_empty_states_and_buttons(self):
        script = dashboard._MISSION_SCRIPT.split('const scopeButtons =', 1)[1].split('const initialTarget', 1)[0]
        result = run_js("""
          const DATA = {mission: {relevant: ['NOW']}};
          const rows = ['NOW', 'LATER'].map(id => ({dataset: {workTask: id}, classList: {
            hidden: false, toggle(name, value) {this.hidden = value;}, contains() {return this.hidden;}
          }}));
          const groups = rows.map(row => ({count: {}, empty: {},
            querySelectorAll() {return [row];},
            querySelector(s) {return s === '[data-state-count]' ? this.count : this.empty;}
          }));
          const buttons = ['current', 'all'].map(scope => ({dataset: {workScope: scope},
            setAttribute(k, v) {this[k] = v;}, addEventListener() {}
          }));
          const document = {querySelectorAll(s) {
            return s === '[data-work-scope]' ? buttons : s === '[data-work-task]' ? rows : groups;
          }};
          const snapshot = () => ({hidden: rows.map(r => r.classList.hidden),
            counts: groups.map(g => g.count.textContent), empty: groups.map(g => g.empty.hidden),
            pressed: buttons.map(b => b['aria-pressed'])});
        """ + 'const scopeButtons =' + script + """
          const current = snapshot(); workScope(true); const all = snapshot();
          workScope(false); console.log(JSON.stringify({current, all, again: snapshot()}));
        """)
        self.assertEqual(result["current"], {"hidden": [False, True], "counts": [1, 0],
                                           "empty": [True, False], "pressed": ["true", "false"]})
        self.assertEqual(result["all"], {"hidden": [False, False], "counts": [1, 1],
                                       "empty": [True, True], "pressed": ["false", "true"]})
        self.assertEqual(result["again"], result["current"])

    def test_phone_tab_keys_follow_visual_order_and_wrap(self):
        script = dashboard._SCRIPT.split("document.querySelector('.toptabs').addEventListener('keydown', event => {", 1)[1].split("addEventListener('hashchange'", 1)[0]
        result = run_js("""
          const document = {documentElement: {dataset: {tab: 'review'}}};
          const topTabs = ['home', 'work', 'review', 'audit'].map((name, n) => ({dataset: {tab: name},
            getBoundingClientRect() {return {left: [100, 200, 0, 300][n]};}}));
          const moves = []; let prevented = 0;
          function selectTab(name, options) {document.documentElement.dataset.tab = name; moves.push([name, options.focus]);}
          let keydown; const attach = callback => {keydown = callback;};
          attach(event => {
        """ + script + """
          for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowLeft', 'Home', 'End', 'ArrowRight', 'Escape'])
            keydown({key, preventDefault() {prevented++;}});
          console.log(JSON.stringify({moves, prevented}));
        """)
        self.assertEqual(result["moves"], [[t, True] for t in ["home", "review", "audit", "review", "audit", "review"]])
        self.assertEqual(result["prevented"], 6)

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

    def test_audit_preserves_contract_evidence_and_implementation_anchors(self):
        self.project.tasks[0].files = ['src/foundation.py']
        self.project.tasks[0].symbols = ['build_foundation']
        contract = self.project.contracts[self.project.tasks[0].contract]
        contract.evidence = 'Recorded contract evidence.'
        _, page = self.page()
        for text in ('src/foundation.py', 'build_foundation', 'Recorded contract evidence.'):
            self.assertIn(text, page)

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
