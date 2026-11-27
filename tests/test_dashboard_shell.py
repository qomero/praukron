"""The dashboard shell: lanes, the waiting model, panels and keys (T-DASH-SHELL-01).

Every test reads a small synthetic chronicle written here; no project's own
records are used. The page script is checked as text and, where node is
installed, its pure parts are run.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard, i18n, layout, shell  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "chronicle"
DAY = "2026-11-28"

PHASES = """# Phases

## P1 — Foundation

Outcome:
The base works.

Entry:
- Nothing.

Exit:
- The base is done.

Exit authority:
T-A2

Status:
COMPLETE

---

## P2 — Growth

Outcome:
It grows.

Entry:
- P1 is complete.

Exit:
- Growth is done.

Exit authority:
T-EXIT

Status:
ACTIVE

---

# Gates

No gates yet.

---

# Milestones

No milestones yet.
"""

MODULES = """# Modules

## M-A — Base
- Phase: P1
- Outcome: The base.

## M-B — Growth core
- Phase: P2
- Outcome: The core grows.

## M-C — Growth edge
- Phase: P2
- Outcome: The edge grows.

## M-OPS — Upkeep
- Phase: P-NONE
- Outcome: Upkeep.
"""


def task(tid: str, status: str, module: str, deps: str = "none", owner: bool = False,
         validation: str = "UNTESTED", decisions: str = "") -> str:
    return (f"\n## {tid}: Work on {tid.lower()}\n- Status: {status}\n- Module: {module}\n"
            + ("- Authority: owner\n" if owner else "")
            + f"- Validation: {validation}\n- Dependencies: {deps}\n- Owner: builder\n- AC: AC-{tid}\n"
            + f"- Evidence: {'Tests pass.' if status == 'DONE' else '—'}\n"
            + (f"- Governed by: {decisions}\n" if decisions else ""))


TASKS = "# Tasks\n" + "".join([
    task("T-A1", "DONE", "M-A", validation="SYNTHETIC"),
    task("T-A2", "DONE", "M-A", "T-A1", validation="SYNTHETIC"),
    task("T-B1", "DONE", "M-B", "T-A2", validation="SYNTHETIC"),
    task("T-OWN", "TODO", "M-B", "T-B1", owner=True),
    task("T-B2", "TODO", "M-B", "T-OWN", decisions="ADR-002"),
    task("T-B3", "TODO", "M-B", "T-B2"),
    task("T-C1", "TODO", "M-C", "T-B3"),
    task("T-EXIT", "TODO", "M-C", "T-C1", owner=True),
    task("T-DONEOWN", "DONE", "M-C", "T-B1", owner=True, validation="AI_REVIEWED"),
    task("T-GRANTED", "TODO", "M-C", "T-B1", owner=True),
    task("T-OPS", "TODO", "M-OPS"),
])

ACCEPTANCE = "# Acceptance\n\n# Contracts\n" + "".join(
    f"\n## AC-{tid} — Work on {tid.lower()}\n\n- `AC-{tid}-01` — Given it, When done, Then it holds.\n"
    f"  `TEST` · `{'PASS' if tid in ('T-A1', 'T-A2', 'T-B1', 'T-DONEOWN') else 'NOT_RUN'}`\n"
    for tid in ("T-A1", "T-A2", "T-B1", "T-OWN", "T-B2", "T-B3", "T-C1", "T-EXIT", "T-DONEOWN", "T-GRANTED", "T-OPS"))

ASSUMPTIONS = """# Assumptions

## A-1: Growth uses the base format
- Status: OPEN
- Tasks: T-B2
- Impact: HIGH
- Recorded: 2026-10-01 · builder
- Assumption: Growth reads the base format.
- Basis: Nothing says otherwise.
- Applied in: T-B2
- Responses: none
- Reconciled by: none

## A-2: Upkeep runs weekly
- Status: OPEN
- Tasks: T-OPS
- Impact: LOW
- Recorded: 2026-10-03 · builder
- Assumption: Weekly.
- Basis: Habit.
- Applied in: T-OPS
- Responses: R-001
- Reconciled by: none
"""

JOURNAL = """# Journal

## 2026-10-05 — Started the growth core

T-OWN waits for the owner. AC-T-EXIT-01 is only a contract id.

## 2026-10-02 — Planned growth

T-OWN and T-B2 were planned.
"""

OWNERS = """# ADR-001: Owners
- Date: 2026-10-01
- Status: ACCEPTED
- Authority: owner
- Owners: thien-owner
- Decision: The owner is the GitHub user thien-owner.
"""

GROWTH = """# ADR-002: Growth reads the base
- Date: 2026-10-02
- Status: ACCEPTED
- Supersedes: none
- Affects: T-B2
- Context: Growth needs one format.
- Decision: Read the base format.
"""

# The owner answered A-2; it waits for the agent, not for the owner.
RESPONSE = """
## R-001: A-2 CONFIRM
- By: thien-owner
- Date: 2026-10-04
- Via: dashboard

> Weekly is right.
"""

GRANT = """
## AG-01: verify for reviewer-agent
- Capability: verify
- Scope: T-GRANTED
- Delegate: reviewer-agent
- Granted by: thien-owner
- Date: 2026-11-01
- Until: none
- Via: cli
"""


def build(root: Path, grant: bool = True) -> None:
    authority = root / layout.AUTHORITY_DIR
    shutil.copytree(TEMPLATES, authority)
    for name, text in (("PHASES.md", PHASES), ("MODULES.md", MODULES), ("TASKS.md", TASKS),
                       ("ACCEPTANCE.md", ACCEPTANCE), ("ASSUMPTIONS.md", ASSUMPTIONS), ("JOURNAL.md", JOURNAL)):
        (authority / name).write_text(text, encoding="utf-8")
    (authority / "THESIS.md").write_text("# Product thesis\n\n- Statement: A synthetic project.\n", encoding="utf-8")
    (authority / "ADR" / "ADR-001.md").write_text(OWNERS, encoding="utf-8")
    (authority / "ADR" / "ADR-002.md").write_text(GROWTH, encoding="utf-8")
    responses = authority / "RESPONSES.md"
    responses.write_text(responses.read_text(encoding="utf-8") + RESPONSE, encoding="utf-8")
    template = (TEMPLATES / "AUTHORITY.md").read_text(encoding="utf-8")
    if grant:
        (authority / "AUTHORITY.md").write_text(template + GRANT, encoding="utf-8")
    else:
        (authority / "AUTHORITY.md").unlink()


class ShellCase(unittest.TestCase):
    grant = True

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="praukron-shell-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        build(self.dir, self.grant)
        self.load()

    def load(self) -> None:
        self.project = compiler.load(self.dir)
        self.compiled = compiler.as_json(self.project)

    def page(self, interactive: bool = False) -> str:
        return dashboard.render(self.project, analytics.report(self.project), self.compiled, interactive=interactive)

    def waiting(self) -> dict:
        return shell.waiting(self.compiled, self.project, DAY)

    def item(self, wid: str) -> dict:
        return next(i for i in self.waiting()["items"] if i["id"] == wid)


class TestFixture(ShellCase):
    def test_the_synthetic_chronicle_is_valid(self) -> None:
        from praukron import validate
        # The one error is the case under test: a DONE owner task nobody verified.
        self.assertEqual([f.code for f in validate.errors(validate.check(self.project))], ["owner-task-not-verified"])


class TestShell(ShellCase):
    """AC-T-DASH-SHELL-01-01."""

    def test_one_shell_carries_four_workspaces_and_needs_no_network(self) -> None:
        page = self.page()
        self.assertNotRegex(page, r'<(?:script|link|img|iframe)[^>]+(?:src|href)="(?:https?:)?//')
        self.assertNotRegex(page, r'url\(\s*["\']?(?:https?:)?//')
        self.assertNotIn("fetch(", page)
        self.assertNotIn("@import", page)
        body = page.split("<body>", 1)[1]
        shell_part = body.split('<div class="shell">', 1)[1]
        order = [shell_part.index(marker) for marker in (
            '<header class="top shell-top">', 'id="search-open"', '<section id="waiting"', '<main id="main">',
            '<aside id="side"', '<section id="bottom"')]
        self.assertEqual(order, sorted(order))
        main = shell_part.split('<main id="main">', 1)[1].split("</main>", 1)[0]
        self.assertEqual(re.findall(r'role="tab" id="tab-([^"]+)"', main), ["home", "work", "review", "audit"])
        for name in ("home", "work", "review", "audit"):
            self.assertIn(f'id="panel-{name}"', main)
        # One data model: the shell reads the same embedded project and adds only
        # positions and the waiting model; one selection and one filter set.
        self.assertEqual(page.count('id="project-data"'), 1)
        data = json.loads(re.search(r'id="shell-data">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(set(data), {"lanes", "waiting", "verifications"})
        self.assertIn("function showTask(id) {\n  if (!byId[id]) return;\n  shSelect(id);", page)
        self.assertIn("'lanes-phase': 'phase', 'lanes-module': 'module', 'lanes-status': 'status'", page)
        self.assertIn("passes(n.rec)", shell.SCRIPT)
        self.assertIn("if (window.shReady) shFiltersChanged();", page)

    def test_the_lanes_are_the_main_surface_of_work(self) -> None:
        page = self.page()
        work = page.split('id="panel-work"', 1)[1].split('id="panel-review"', 1)[0]
        self.assertLess(work.index('id="lanes"'), work.index("<h2>Execution</h2>"))
        # Lanes, Force (T-DASH-FORCE-01) and 3D (T-DASH-3D-01) have a control; later views have none yet.
        section = page.split('id="lanes"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-view="lanes" aria-pressed="true"', section)
        for later in ('data-view="code"', ">Code<"):
            self.assertNotIn(later, section)


class TestLanes(ShellCase):
    """AC-T-DASH-SHELL-01-02."""

    def test_columns_bands_and_depth(self) -> None:
        lanes = shell.lanes(self.compiled)
        self.assertEqual([c["id"] for c in lanes["columns"]], ["P1", "P2", "P-NONE"])
        xs = {c["id"]: (c["x"], c["x"] + c["w"]) for c in lanes["columns"]}
        pos = lanes["positions"]
        for t in self.compiled["tasks"]:
            lo, hi = xs[t["phase"]]
            self.assertTrue(lo <= pos[t["id"]][0] <= hi, t["id"])
            band = next(b for b in lanes["bands"] if b["id"] == t["module"] and b["phase"] == t["phase"])
            self.assertTrue(band["y"] <= pos[t["id"]][1] <= band["y"] + band["h"], t["id"])
        self.assertEqual([b["id"] for b in lanes["bands"] if b["phase"] == "P2"], ["M-B", "M-C"])
        depth = shell.depths(self.compiled["tasks"])
        # Depth counts dependencies inside the phase only: T-B1 depends on T-A2 in P1.
        self.assertEqual((depth["T-B1"], depth["T-OWN"], depth["T-B2"], depth["T-B3"], depth["T-C1"], depth["T-EXIT"]),
                         (0, 1, 2, 3, 4, 5))
        self.assertEqual(pos["T-OWN"][0] - pos["T-B1"][0], shell.DX)
        self.assertLess(pos["T-B3"][0], pos["T-C1"][0])

    def test_decisions_and_assumptions_sit_below_what_they_bear_on(self) -> None:
        lanes = shell.lanes(self.compiled)
        pos = lanes["positions"]
        lowest_task = max(pos[t["id"]][1] for t in self.compiled["tasks"])
        rows = {r["id"]: r for r in lanes["rows"]}
        self.assertLess(lowest_task, rows["decisions"]["y"])
        self.assertLess(rows["decisions"]["y"], rows["assumptions"]["y"])
        self.assertEqual(pos["ADR-002"][0], pos["T-B2"][0])
        self.assertEqual(pos["A-1"][0], pos["T-B2"][0])
        self.assertTrue(rows["assumptions"]["y"] <= pos["A-1"][1] <= rows["assumptions"]["y"] + rows["assumptions"]["h"])

    def test_the_same_input_gives_the_same_positions(self) -> None:
        first = shell.lanes(self.compiled)
        reordered = json.loads(json.dumps(self.compiled))
        reordered["tasks"].sort(key=lambda t: t["id"], reverse=True)
        self.assertEqual(shell.lanes(self.compiled), first)
        self.assertEqual(shell.lanes(reordered)["positions"], first["positions"])
        self.assertEqual(self.page(), self.page())

    def test_a_dependency_cycle_still_lays_out(self) -> None:
        tasks = [{"id": "T-X", "phase": "P1", "deps": ["T-Y"]}, {"id": "T-Y", "phase": "P1", "deps": ["T-X"]}]
        self.assertEqual(shell.depths(tasks), shell.depths(list(reversed(tasks))))

    def test_the_view_opens_framed_on_the_current_phase(self) -> None:
        self.assertEqual(shell.lanes(self.compiled)["current"], "P2")
        self.assertIn("if (!shState.built) { lBuild(); shDrawView(); }", shell.SCRIPT)
        self.assertIn("if (force) lFit(); else lFitPhase(SHELL.lanes.current);", shell.SCRIPT)


class TestWaiting(ShellCase):
    """AC-T-DASH-SHELL-01-03."""

    def test_every_recorded_item_once_in_two_categories(self) -> None:
        w = self.waiting()
        ids = [i["id"] for i in w["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), ["A-1", "T-DONEOWN", "T-EXIT", "T-OWN"])
        self.assertEqual({i["id"]: i["kind"] for i in w["items"]},
                         {"T-OWN": "task", "T-EXIT": "task", "T-DONEOWN": "verification", "A-1": "assumption"})
        self.assertEqual({i["id"]: i["category"] for i in w["items"]},
                         {"T-OWN": "now", "T-EXIT": "upcoming", "T-DONEOWN": "now", "A-1": "now"})
        self.assertEqual((w["count"], w["now"], w["upcoming"]), (4, 3, 1))
        self.assertEqual(ids[-1], "T-EXIT")
        self.assertEqual(self.item("T-EXIT")["waitsOn"], ["T-C1"])

    def test_a_task_a_grant_lets_a_delegate_verify_is_not_counted(self) -> None:
        w = self.waiting()
        self.assertNotIn("T-GRANTED", [i["id"] for i in w["items"]])
        self.assertEqual(w["delegable"], ["T-GRANTED"])
        # Before the grant's date it is not in force, so the task waits again.
        before = shell.waiting(self.compiled, self.project, "2026-10-15")
        self.assertIn("T-GRANTED", [i["id"] for i in before["items"]])

    def test_answered_items_leave_the_model(self) -> None:
        responses = self.dir / layout.AUTHORITY_DIR / "RESPONSES.md"
        responses.write_text(responses.read_text(encoding="utf-8")
                             + "\n## R-002: T-OWN VERIFY\n- By: thien-owner\n- Date: 2026-11-20\n- Via: dashboard\n\n> Checked.\n",
                             encoding="utf-8")
        self.load()
        self.assertNotIn("T-OWN", [i["id"] for i in self.waiting()["items"]])

    def test_unrecorded_kinds_are_named_never_zero(self) -> None:
        w = self.waiting()
        self.assertEqual(w["notRecorded"], ["Pending decisions", "Requests for delegation", "Escalations"])
        page = self.page()
        strip = page.split('<section id="waiting"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Pending decisions: not recorded · Requests for delegation: not recorded · "
                      "Escalations: not recorded", strip)
        self.assertNotRegex(strip, r"(?:decisions|delegation|Escalations)[^·<]*: 0\b")


class TestBlocks(ShellCase):
    """AC-T-DASH-SHELL-01-04."""

    def test_transitive_first_then_direct(self) -> None:
        own = self.item("T-OWN")
        self.assertEqual(own["word"], "blocks")
        self.assertEqual(own["tasks"], ["T-B2", "T-B3", "T-C1", "T-EXIT"])
        self.assertEqual(own["direct"], ["T-B2"])
        self.assertEqual(own["count"], "Blocks: 4 tasks · 1 direct · 3 indirect")

    def test_an_assumption_affects_and_does_not_block(self) -> None:
        a = self.item("A-1")
        self.assertEqual(a["word"], "affected")
        self.assertEqual(a["count"], "Affected: 4 tasks · 1 direct · 3 indirect")
        self.assertNotIn("Blocks", a["count"])
        self.assertEqual(self.item("T-DONEOWN")["count"], "Blocks: no task")

    def test_phase_exit_and_critical_path_where_the_analysis_has_them(self) -> None:
        exit_ = self.item("T-EXIT")
        self.assertEqual(exit_["phaseExit"], ["P2"])
        self.assertEqual(exit_["criticalPath"], "T-EXIT" in self.compiled["criticalPath"])
        self.assertEqual(self.item("T-DONEOWN")["phaseExit"], [])
        page = self.page()
        strip = page.split('<section id="waiting"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Phase exit: P2", strip)
        self.assertIn("Blocks: 4 tasks · 1 direct · 3 indirect", strip)
        self.assertIn("Affected: 4 tasks · 1 direct · 3 indirect", strip)


class TestSince(ShellCase):
    """AC-T-DASH-SHELL-01-05."""

    def test_dates_come_from_records_and_the_fallback_says_so(self) -> None:
        a = self.item("A-1")
        self.assertEqual((a["since"]["date"], a["since"]["basis"]), ("2026-10-01", "date recorded"))
        own = self.item("T-OWN")
        self.assertEqual((own["since"]["date"], own["since"]["basis"]), ("2026-10-02", "first journal mention"))
        self.assertEqual(shell.since_text(own), "Since 2026-10-02 (first journal mention)")
        # A contract id is not a mention of its task; no record, no date.
        self.assertIsNone(self.item("T-EXIT")["since"])
        self.assertEqual(shell.since_text(self.item("T-EXIT")), "Since: not recorded")
        self.assertIsNone(self.item("T-DONEOWN")["since"])
        strip = self.page().split('<section id="waiting"', 1)[1].split("</section>", 1)[0]
        dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", strip))
        self.assertEqual(dates, {"2026-10-01", "2026-10-02"})


class TestPanels(ShellCase):
    """AC-T-DASH-SHELL-01-06."""

    def test_toggles_handles_and_defaults(self) -> None:
        page = self.page()
        for panel in ("waiting", "side", "bottom"):
            self.assertGreaterEqual(page.count(f'data-toggle="{panel}"'), 2, panel)
            self.assertIn(f"html.hide-{panel} ", page)
        handle = re.search(r'<button[^>]*id="waiting-handle".*?</button>', page, re.S).group(0)
        self.assertIn('<span class="count">4</span>', handle)
        boot = page.split("</head>", 1)[0]
        # First visit: strip and side panel shown, bottom collapsed, system theme.
        self.assertIn("if (panels.waiting === false) root.className += ' hide-waiting';", boot)
        self.assertIn("if (panels.side === false) root.className += ' hide-side';", boot)
        self.assertIn("if (panels.bottom !== true) root.className += ' hide-bottom';", boot)
        self.assertIn("matchMedia('(prefers-color-scheme: dark)')", boot)
        self.assertIn("base = { waiting: true, side: true, bottom: false", shell.SCRIPT)
        # Visibility, sizes and the workspace persist; storage may throw.
        for key in ("sideW", "bottomH", "tab", "view"):
            self.assertIn(key + ":", shell.SCRIPT)
        self.assertIn("try { panels = JSON.parse(localStorage.getItem('praukron-panels') || 'null'); } catch (error) {}", boot)
        self.assertIn("function shStore() { try { localStorage.setItem(SH_KEY, JSON.stringify(shPanels)); } catch (error) {} }",
                      shell.SCRIPT)
        # Focus hides all three and brings back what was shown; the graph takes the space.
        self.assertIn("shPanels.before = { waiting: shPanels.waiting, side: shPanels.side, bottom: shPanels.bottom };",
                      shell.SCRIPT)
        self.assertIn("html.js.hide-side .shell-middle { grid-template-columns: minmax(0, 1fr) auto; }", page)
        self.assertIn("shRoot.style.setProperty('--lanes-h'", shell.SCRIPT)


class TestKeysAndWords(ShellCase):
    """AC-T-DASH-SHELL-01-07."""

    def test_keys_work_outside_text_fields(self) -> None:
        script = shell.SCRIPT
        handler = script.split("// --- Keys, outside text fields", 1)[1]
        self.assertIn("target.closest('input, textarea, select')", handler)
        for key in ("s: () => shToggle('side')", "b: () => shToggle('bottom')", "W: () => shToggle('waiting')",
                    "f: shFocusMode", "'1': () => shOpenLanes(shState.sel, 'lanes')", "Escape:"):
            self.assertIn(key, handler)
        for key in ("case 'ArrowLeft':", "case 'ArrowRight':", "case 'ArrowUp': case 'ArrowDown':", "case 'Enter':"):
            self.assertIn(key, script)
        self.assertIn("(!editing && event.key === '/')", self.page())
        self.assertNotIn("c: ", handler)

    def test_shape_and_word_not_colour_alone(self) -> None:
        legend = dict((text, shape) for shape, text in shell.LEGEND)
        for word in ("Done: filled circle", "To do: ring", "In progress: ring with a dot", "Blocked: cross",
                     "Waiting for you: diamond and !", "Decision: square", "Assumption: triangle"):
            self.assertIn(word, legend)
        page = self.page()
        self.assertIn('<span class="when now">Now</span>', page)
        self.assertIn('<span class="when upcoming">Upcoming</span>', page)
        self.assertIn("'! ' + n.id", shell.SCRIPT)

    def test_text_view_relations_and_skip_link(self) -> None:
        page = self.page()
        self.assertIn('<a class="skip" href="#p-text">Skip to the text view</a>', page)
        self.assertLess(page.index('class="skip"'), page.index('<div class="shell">'))
        self.assertIn('id="text-rows"', page)
        self.assertIn('id="rel-rows"', page)
        self.assertIn("lKindShown(n) && lPassed.get(n.id) !== false", shell.SCRIPT)
        self.assertIn("'<li>' + shSentence(e) + '.</li>'", shell.SCRIPT)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class TestScriptRuns(ShellCase):
    """The text view lists every node under the filters; relations read as sentences."""

    def test_text_and_relations_under_filters(self) -> None:
        import subprocess
        page = self.page()
        data = re.search(r'id="project-data">(.*?)</script>', page, re.S).group(1)
        model = re.search(r'id="shell-data">(.*?)</script>', page, re.S).group(1)
        script = shell.SCRIPT
        pure = "const L = {" + script.split("const L = {", 1)[1].split("const lSvg", 1)[0]
        program = (dashboard.FILTER_JS + "\nconst DATA = " + data + ";\nconst byId = Object.fromEntries(DATA.tasks.map(t => [t.id, t]));"
                   + "\nconst SHELL = " + model + ";\nconst shWait = Object.fromEntries(SHELL.waiting.items.map(w => [w.id, w]));"
                   + "\nconst shState = { waitOnly: false };\n" + pure
                   + "\nconst pressed = { 'lanes-decisions': true, 'lanes-assumptions': true };"
                   + "\nconst lCheck = id => Boolean(pressed[id]);"
                   + "\nconst lKindShown = n => n.kind === 'task' || lCheck(n.kind === 'decision' ? 'lanes-decisions' : 'lanes-assumptions');"
                   + "\nlet lPassed = new Map();\n")
        compute = "function lComputePass() {" + script.split("function lComputePass() {", 1)[1].split("\nfunction lChain", 1)[0]
        program += compute + """
const shown = () => { lComputePass(); return [...L.nodes.values()].filter(n => lKindShown(n) && lPassed.get(n.id)).map(n => n.id).sort(); };
const all = shown();
filters.status.add('DONE');
const done = shown();
filters.status.clear(); pressed['lanes-decisions'] = false;
const noDecisions = shown();
console.log(JSON.stringify({all, done, noDecisions, edges: L.edges.map(e => [e.s, e.k, e.t])}));
"""
        out = subprocess.run(["node", "-"], input=program, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        every = sorted([t["id"] for t in self.compiled["tasks"]] + [d["id"] for d in self.compiled["decisions"]]
                       + [a["id"] for a in self.compiled["assumptions"]])
        self.assertEqual(result["all"], every)
        self.assertEqual([i for i in result["done"] if i.startswith("T-")], ["T-A1", "T-A2", "T-B1", "T-DONEOWN"])
        self.assertNotIn("ADR-002", result["noDecisions"])
        self.assertIn(["T-OWN", "dep", "T-B2"], result["edges"])
        self.assertIn(["ADR-002", "gov", "T-B2"], result["edges"])
        self.assertIn(["A-1", "asm", "T-B2"], result["edges"])


class TestWrites(ShellCase):
    """AC-T-DASH-SHELL-01-08."""

    def test_the_static_page_offers_no_write(self) -> None:
        page = self.page()
        for control in ("<form", "<textarea", 'method="post"', "fetch(", 'class="aform"'):
            self.assertNotIn(control, page)
        # The shell itself adds no field at all: its controls are buttons and selects.
        parts = [shell.waiting_strip(shell.waiting(self.compiled, self.project, DAY), self.compiled),
                 shell.lanes_section(self.compiled), shell.side_panel(), shell.help_dialog(),
                 shell.bottom_panel({"grants": shell.grants_pane(shell.grants(self.project, DAY))})]
        for part in parts:
            self.assertNotRegex(part, r"<(?:input|textarea|form)\b")

    def test_the_served_page_gains_no_write_path(self) -> None:
        from praukron import review
        served, static = self.page(interactive=True), self.page()
        # Every request the served page can make is in the scripts it had before.
        before = review.script(True)
        self.assertEqual(served.count("fetch("), before.count("fetch("))
        self.assertNotIn("fetch(", shell.SCRIPT)
        self.assertNotIn("XMLHttpRequest", shell.SCRIPT)
        grants = served.split('id="p-grants"', 1)[1].split('id="p-text"', 1)[0]
        self.assertEqual(grants, static.split('id="p-grants"', 1)[1].split('id="p-text"', 1)[0])
        self.assertNotIn("<button", grants)

    def test_grants_tab_lists_only_recorded_grants(self) -> None:
        pane = self.page().split('id="p-grants"', 1)[1].split('id="p-text"', 1)[0]
        self.assertIn("<code>AG-01</code>", pane)
        self.assertIn("reviewer-agent", pane)
        for mock in ("G-MOCK", "MOCK", "mock", "codex/primary"):
            self.assertNotIn(mock, pane)

    def test_delegate_verified_is_shown_apart_with_its_grant(self) -> None:
        authority = self.dir / layout.AUTHORITY_DIR
        tasks = authority / "TASKS.md"
        tasks.write_text(tasks.read_text(encoding="utf-8").replace(
            "## T-GRANTED: Work on t-granted\n- Status: TODO", "## T-GRANTED: Work on t-granted\n- Status: DONE")
            .replace("- Authority: owner\n- Validation: UNTESTED\n- Dependencies: T-B1\n- Owner: builder\n- AC: AC-T-GRANTED\n- Evidence: —",
                     "- Authority: owner\n- Validation: DELEGATE_VERIFIED\n- Dependencies: T-B1\n- Owner: builder\n- AC: AC-T-GRANTED\n- Evidence: Checked."),
            encoding="utf-8")
        responses = authority / "RESPONSES.md"
        responses.write_text(responses.read_text(encoding="utf-8") + (
            "\n## R-002: T-GRANTED VERIFY\n- By: thien-owner\n- Date: 2026-11-20\n- Via: delegate (cli)\n"
            "- Delegate: reviewer-agent\n- On authority of: thien-owner\n- Grant: AG-01\n\n> Checked the build.\n"),
            encoding="utf-8")
        self.load()
        model = shell.model(self.compiled, self.project, DAY)
        found = model["verifications"]["T-GRANTED"]
        self.assertEqual([(v["kind"], v["delegate"], v["grant"]) for v in found], [("delegate", "reviewer-agent", "AG-01")])
        self.assertNotIn("T-GRANTED", [i["id"] for i in model["waiting"]["items"]])
        page = self.page()
        pane = page.split('id="p-grants"', 1)[1].split('id="p-text"', 1)[0]
        self.assertIn("DELEGATE_VERIFIED, shown apart from HUMAN_VERIFIED.", pane)
        self.assertIn("<code>T-GRANTED</code>", pane)
        self.assertIn('class="delegatebox"', shell.SCRIPT)
        self.assertIn("not HUMAN_VERIFIED", shell.SCRIPT)
        self.assertIn("validation === 'DELEGATE_VERIFIED' ? 'D'", shell.SCRIPT)


class TestNoAuthorityFile(ShellCase):
    grant = False

    def test_grants_tab_says_so_without_the_file(self) -> None:
        pane = self.page().split('id="p-grants"', 1)[1].split('id="p-text"', 1)[0]
        self.assertIn("No AUTHORITY.md in this chronicle: no grant is recorded.", pane)
        self.assertEqual(self.waiting()["delegable"], [])


class TestLanguage(ShellCase):
    def test_every_shell_word_is_catalogued(self) -> None:
        from test_i18n import island, uncovered
        for interactive in (False, True):
            data = island(self.page(interactive))
            sources = data["texts"] + [v for attrs in data["attributes"] for v in attrs.values()]
            self.assertEqual({uncovered(s).strip() for s in sources if uncovered(s)}, set())
        vi = dashboard.render(self.project, analytics.report(self.project), self.compiled, lang="vi")
        visible = re.sub(r"<script\b.*?</script>", "", vi, flags=re.S)
        for english in ("Waiting for you", "Hide strip", "Review queue", "Skip to the text view"):
            self.assertNotIn(">" + english, visible)
        self.assertIn(i18n.CATALOGUES["vi"]["messages"]["Skip to the text view"], visible)



class TestDocumentation(unittest.TestCase):
    """AC-T-DASH-SHELL-01-10."""

    def test_the_documentation_and_the_changelog_describe_the_shell(self) -> None:
        doc = (ROOT / "docs" / "dashboard-ux.md").read_text(encoding="utf-8")
        section = doc.split("## The shell", 1)[1].split("\n## ", 1)[0]
        for words in ("one shell", "Lanes", "Panels", "Waiting for you", "*now*", "*upcoming*", "Focus",
                      "derived view and\nnever authority", "**Keys**", "`s` the side panel", "`b` the bottom panel",
                      "`Shift+W`", "`f` focus", "`1` the lanes", "`/` search", "arrows", "`Enter`", "`Escape`",
                      "Text view", "Relations", "not recorded"):
            self.assertIn(words, section)
        guide = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("The page is one shell", guide)
        self.assertIn("never authority", guide)
        fragment = (ROOT / "changelog.d" / "T-DASH-SHELL-01.md").read_text(encoding="utf-8")
        for words in ("one shell", "lanes", "Waiting for you", "*now*", "*upcoming*", "hides, shows and resizes",
                      "`Shift+W`", "derived\n  view"):
            self.assertIn(words, fragment)


if __name__ == "__main__":
    unittest.main()
