"""The force view beside the lanes, and the page opening on Work (T-DASH-FORCE-01).

Every test reads the synthetic chronicle of test_dashboard_shell; no project's
own records are used. Where node is installed, the page's own shell script
runs against a small stand-in for the document, so switching views, the
shared selection and filters, the keys and reduced motion are exercised as the
page runs them, not only read as text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import dashboard, shell  # noqa: E402
from test_dashboard_shell import ROOT, ShellCase  # noqa: E402

NODE = shutil.which("node")

# A stand-in for the few document features the shell script touches. Every
# element is an object with attributes, classes, children and listeners; the
# stage measures 800 by 600, so the Work graph counts as visible.
FAKE_DOM = r"""
const STORE = new Map(Object.entries(INITIAL_STORAGE));
globalThis.localStorage = { getItem: k => (STORE.has(k) ? STORE.get(k) : null), setItem: (k, v) => STORE.set(k, String(v)),
                            removeItem: k => STORE.delete(k), clear: () => STORE.clear() };
const RAF = []; let rafCalls = 0;
globalThis.requestAnimationFrame = f => { rafCalls += 1; RAF.push(f); return RAF.length; };
globalThis.matchMedia = q => ({ matches: q.includes('reduced-motion') ? REDUCED : false });
globalThis.innerWidth = 1400; globalThis.innerHeight = 900; globalThis.window = globalThis;
const winHandlers = {};
globalThis.addEventListener = (t, f) => { (winHandlers[t] = winHandlers[t] || []).push(f); };
globalThis.removeEventListener = () => {};
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase(); this.id = id || ''; this.attrs = {}; this.children = [];
    this.dataset = {}; this.style = { setProperty() {} }; this.cls = new Set(); this.hidden = false; this.handlers = {};
    this._text = ''; this.innerHTML = ''; this.tabIndex = 0; this.isContentEditable = false;
    const self = this;
    this.classList = {
      add: (...c) => c.forEach(x => self.cls.add(x)), remove: (...c) => c.forEach(x => self.cls.delete(x)),
      contains: c => self.cls.has(c),
      toggle: (c, on) => { on = on === undefined ? !self.cls.has(c) : Boolean(on); if (on) self.cls.add(c); else self.cls.delete(c); return on; },
    };
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'class') this.cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  getAttribute(k) { return k === 'class' ? [...this.cls].join(' ') : (k in this.attrs ? this.attrs[k] : null); }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  fire(t, e) { (this.handlers[t] || []).forEach(f => f(e)); }
  getBoundingClientRect() { return { width: 800, height: 600, left: 0, top: 0 }; }
  closest() { return null; }
  focus() { document.activeElement = this; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  get offsetHeight() { return 0; }
  get clientHeight() { return 600; }
  showModal() {} close() {}
}
const ELS = new Map();
const viewButtons = ['lanes', 'force'].map(v => { const b = new El('button'); b.dataset.view = v; return b; });
const docHandlers = {};
globalThis.document = {
  documentElement: new El('html'), body: new El('body'), activeElement: null,
  getElementById(id) {
    if (!ELS.has(id)) {
      const el = new El('div', id);
      Object.entries(ATTRS[id] || {}).forEach(([k, v]) => { if (k === 'hidden') el.hidden = true; else el.setAttribute(k, v); });
      if (id === 'shell-data') el._text = SHELL_JSON;
      ELS.set(id, el);
    }
    return ELS.get(id);
  },
  createElementNS: (ns, tag) => new El(tag),
  querySelector: sel => (sel === '.skip' ? new El('a') : null),
  querySelectorAll: sel => (sel.includes('[data-view]') ? viewButtons : []),
  addEventListener: (t, f) => { (docHandlers[t] = docHandlers[t] || []).push(f); },
};
document.activeElement = document.body;
function key(k, target) {
  const e = { key: k, target: target || document.body, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false,
              defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  if (target && target !== document.body) target.fire('keydown', e);
  (docHandlers.keydown || []).forEach(f => f(e));
  return e.defaultPrevented;
}
function flush() { let n = 0; while (RAF.length && n < 10000) { RAF.shift()(performance.now()); n += 1; } return n; }
// The page's own helpers the shell reads, reduced to what the shell needs.
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const pill = s => '<span class="pill">' + esc(s) + '</span>';
const protectedText = s => String(s == null ? '' : s);
const taskDetailHtml = id => '<h2 id="side-h">' + esc(id) + '</h2><div class="detail-code"></div>';
const fillTaskCode = () => {};
let openTab = 'work';
function selectTab(name) { openTab = name; }
"""


def attributes(page: str) -> dict:
    """The starting attributes of every element with an id, as the page writes them."""
    out = {}
    for tag in re.findall(r'<[a-z]+\b[^>]*\bid="[^"]+"[^>]*>', page):
        attrs = dict(re.findall(r'\s([\w-]+)="([^"]*)"', tag))
        if re.search(r"\shidden(?=[\s>])", tag):
            attrs["hidden"] = ""
        out[attrs.pop("id")] = {k: v for k, v in attrs.items() if k in ("aria-pressed", "hidden")}
    return out


class ForceCase(ShellCase):
    def run_page(self, body: str, storage: dict | None = None, reduced: bool = True) -> dict:
        page = self.page()
        data = re.search(r'id="project-data">(.*?)</script>', page, re.S).group(1)
        model = re.search(r'id="shell-data">(.*?)</script>', page, re.S).group(1)
        prelude = ("const INITIAL_STORAGE = " + json.dumps({k: json.dumps(v) for k, v in (storage or {}).items()})
                   + ";\nconst REDUCED = " + ("true" if reduced else "false")
                   + ";\nconst ATTRS = " + json.dumps(attributes(page))
                   + ";\nconst SHELL_JSON = " + json.dumps(model) + ";\n")
        program = (prelude + FAKE_DOM + dashboard.FILTER_JS + "\nconst DATA = " + data
                   + ";\nconst byId = Object.fromEntries(DATA.tasks.map(t => [t.id, t]));"
                   + "\nconst decisionById = Object.fromEntries(DATA.decisions.map(d => [d.id, d]));\n"
                   + shell.SCRIPT
                   + "\nconst snap = () => [...L.nodes.values()].map(n => n.id + ':' + n.el.getAttribute('class')).join('|');"
                   + "\nconst ids = () => shListed().map(n => n.id);\n"
                   + "const R = {};\n" + body + "\nconsole.log(JSON.stringify(R));\n")
        out = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-3000:])
        return json.loads(out.stdout.strip().splitlines()[-1])


class TestOpensOnWork(ShellCase):
    """AC-T-DASH-FORCE-01-01."""

    def boot(self, hash_: str, stored: dict | None) -> str:
        script = (
            "const attrs = {}; const document = { documentElement: { className: '', setAttribute: (k, v) => { attrs[k] = v; } } };"
            f"const location = {{ hash: {json.dumps(hash_)} }};"
            f"const saved = {json.dumps(json.dumps(stored) if stored is not None else None)};"
            "const localStorage = { getItem: k => (k === 'praukron-panels' ? saved : null) };"
            "const window = { matchMedia: () => ({ matches: false }) }; const matchMedia = window.matchMedia;"
            + dashboard._BOOT + "\nconsole.log(attrs['data-tab']);")
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_first_visit_opens_on_work_and_a_fragment_or_stored_workspace_wins(self) -> None:
        self.assertEqual(self.boot("", None), "work")
        self.assertEqual(self.boot("", {}), "work")
        self.assertEqual(self.boot("#home", None), "home")
        self.assertEqual(self.boot("#audit", {"tab": "review"}), "audit")
        self.assertEqual(self.boot("", {"tab": "home"}), "home")
        self.assertEqual(self.boot("", {"tab": "review"}), "review")
        self.assertEqual(self.boot("#assumptions", None), "review")

    def test_the_lanes_are_the_first_view_and_home_stays_reachable(self) -> None:
        self.assertIn("base = { waiting: true, side: true, bottom: false, pin: false, sideW: 380, bottomH: 260,"
                      " before: null, tab: null, view: 'lanes' }", shell.SCRIPT)
        self.assertIn("shState.view = SH_VIEWS.includes(shPanels.view) ? shPanels.view : 'lanes';", shell.SCRIPT)
        page = self.page()
        self.assertIn('role="tab" id="tab-home"', page)
        # The workspace tabs move with the arrow keys, Home and End.
        self.assertIn("else if (event.key === 'Home') next = 0;", page)


@unittest.skipIf(NODE is None, "node is not installed")
class TestSwitch(ForceCase):
    """AC-T-DASH-FORCE-01-02."""

    def test_two_opens_force_one_returns_and_nothing_is_laid_out_before(self) -> None:
        r = self.run_page("""
R.start = [shState.view, shState.built, FV.sim === null, [...L.nodes.values()].every(n => n.at.force === null)];
R.two = key('2');
R.force = [shState.view, Boolean(FV.sim && FV.sim.done), [...L.nodes.values()].every(n => n.pos === n.at.force),
           viewButtons.map(b => b.getAttribute('aria-pressed')), lStage.getAttribute('aria-label')];
R.stored = JSON.parse(localStorage.getItem('praukron-panels')).view;
R.one = key('1');
R.lanes = [shState.view, [...L.nodes.values()].every(n => n.pos === n.at.lanes), viewButtons.map(b => b.getAttribute('aria-pressed'))];
R.stored1 = JSON.parse(localStorage.getItem('praukron-panels')).view;
R.reserved = [key('c'), shState.view];
viewButtons[1].fire('click', {});
R.click = shState.view;
""")
        self.assertEqual(r["start"], ["lanes", True, True, True])
        self.assertTrue(r["two"])
        self.assertEqual(r["force"], ["force", True, True, ["false", "true"], "Force graph"])
        self.assertEqual(r["stored"], "force")
        self.assertTrue(r["one"])
        self.assertEqual(r["lanes"], ["lanes", True, ["true", "false"]])
        self.assertEqual(r["stored1"], "lanes")
        self.assertEqual(r["reserved"], [False, "lanes"])  # `3` opens the 3D view since T-DASH-3D-01
        self.assertEqual(r["click"], "force")

    def test_the_chosen_view_persists_across_visits(self) -> None:
        r = self.run_page("R.view = [shState.view, Boolean(FV.sim && FV.sim.done), viewButtons.map(b => b.getAttribute('aria-pressed'))];",
                          storage={"praukron-panels": {"view": "force", "tab": "work"}})
        self.assertEqual(r["view"], ["force", True, ["false", "true"]])

    def test_one_control_per_view_and_none_for_later_views(self) -> None:
        # T-DASH-3D-01 adds the third view on purpose: its control and key `3` now exist.
        section = shell.lanes_section(self.compiled)
        self.assertEqual(re.findall(r'<button[^>]*data-view="(\w+)"', section), ["lanes", "force", "3d"])
        for later in (">Code<", "Architecture"):
            self.assertNotIn(later, section)
        handler = shell.SCRIPT.split("// --- Keys, outside text fields", 1)[1]
        self.assertIn("'2': () => shOpenLanes(shState.sel, 'force')", handler)
        self.assertNotIn("c: ", handler)


@unittest.skipIf(NODE is None, "node is not installed")
class TestShared(ForceCase):
    """AC-T-DASH-FORCE-01-03."""

    def test_selection_filters_and_side_panel_are_the_same_in_both_views(self) -> None:
        r = self.run_page("""
shSelect('T-B2');
const lanes = { snap: snap(), side: document.getElementById('side-body').innerHTML, ids: ids() };
key('2');
const force = { snap: snap(), side: document.getElementById('side-body').innerHTML, ids: ids() };
R.selection = [lanes.snap === force.snap, lanes.side === force.side, shState.sel];
R.dimmed = [...L.nodes.values()].filter(n => n.el.classList.contains('l-dim')).map(n => n.id).sort();
R.chain = [...shState.focus.F].sort();
filters.status.add('DONE'); shFiltersChanged();
const fFiltered = { snap: snap(), ids: ids() };
key('1');
R.filters = [fFiltered.snap === snap(), JSON.stringify(fFiltered.ids) === JSON.stringify(ids()), ids().length, lanes.ids.length];
R.lines = L.edges.filter(e => e.el.classList.contains('hl')).length;
""")
        self.assertEqual(r["selection"], [True, True, "T-B2"])
        self.assertIn("T-OWN", r["chain"])
        for tid in r["chain"]:
            self.assertNotIn(tid, r["dimmed"])
        self.assertTrue(r["filters"][0] and r["filters"][1])
        self.assertLess(r["filters"][2], r["filters"][3])

    def test_a_waiting_item_frames_its_chain_in_the_force_view(self) -> None:
        r = self.run_page("""
key('2');
shSelect('T-OWN');
const k = shState.tf.k;
R.fit = shState.fit;
R.inside = [...shState.focus.F].filter(id => L.nodes.get(id).kind === 'task').every(id => {
  const p = L.nodes.get(id).pos, x = p[0] * k + shState.tf.x, y = p[1] * k + shState.tf.y;
  return x >= 0 && x <= 800 && y >= 0 && y <= 600;
});
R.view = shState.view;
""")
        self.assertEqual(r, {"fit": "focus", "inside": True, "view": "force"})


@unittest.skipIf(NODE is None, "node is not installed")
class TestDrawn(ForceCase):
    """AC-T-DASH-FORCE-01-04."""

    LAYOUT = r"""
const a = fSim(ITEMS, LINKS); while (!a.tick());
const shuffled = [...ITEMS].reverse(), back = [...LINKS].reverse();
const b = fSim(shuffled, back); while (!b.tick());
const pa = new Map(ITEMS.map(i => [i.id, [0, 0]])), pb = new Map(ITEMS.map(i => [i.id, [0, 0]]));
a.place(pa); b.place(pb);
R.same = JSON.stringify([...pa]) === JSON.stringify([...pb]);
R.positions = Object.fromEntries(pa);
"""

    def graph(self) -> tuple[list, list]:
        items = [{"id": t["id"], "cluster": t["module"] or "~none"} for t in self.compiled["tasks"]]
        items += [{"id": d["id"], "cluster": None} for d in self.compiled["decisions"]]
        items += [{"id": a["id"], "cluster": None} for a in self.compiled["assumptions"]]
        links = [{"s": d, "t": t["id"], "k": "dep"} for t in self.compiled["tasks"] for d in t["deps"]]
        links += [{"s": d["id"], "t": t, "k": "gov"} for d in self.compiled["decisions"] for t in d["affects"]]
        links += [{"s": a["id"], "t": t, "k": "asm"} for a in self.compiled["assumptions"] for t in a["tasks"]]
        return items, links

    def test_same_input_same_positions_and_no_unseeded_random(self) -> None:
        items, links = self.graph()
        body = f"const ITEMS = {json.dumps(items)}, LINKS = {json.dumps(links)};" + self.LAYOUT
        first, second = self.run_page(body), self.run_page(body)
        self.assertTrue(first["same"])
        self.assertEqual(first["positions"], second["positions"])
        self.assertNotIn("Math.random", shell.SCRIPT)

    def test_tasks_cluster_by_module(self) -> None:
        items, links = self.graph()
        r = self.run_page(f"const ITEMS = {json.dumps(items)}, LINKS = {json.dumps(links)};" + self.LAYOUT)
        pos = r["positions"]
        modules: dict[str, list] = {}
        for item in items:
            if item["cluster"]:
                modules.setdefault(item["cluster"], []).append(pos[item["id"]])
        centre = {m: (sum(p[0] for p in ps) / len(ps), sum(p[1] for p in ps) / len(ps)) for m, ps in modules.items()}
        reach = {m: max(((p[0] - centre[m][0]) ** 2 + (p[1] - centre[m][1]) ** 2) ** .5 for p in ps)
                 for m, ps in modules.items()}
        names = sorted(modules)
        self.assertGreater(len(names), 2)
        # The discs of two modules never overlap, so every task is nearer its own.
        for i, m in enumerate(names):
            for n in names[i + 1:]:
                gap = ((centre[m][0] - centre[n][0]) ** 2 + (centre[m][1] - centre[n][1]) ** 2) ** .5
                self.assertGreater(gap, reach[m] + reach[n], (m, n))

    def test_clusters_are_named_and_marks_and_lines_keep_their_meaning(self) -> None:
        r = self.run_page("""
const before = snap();
key('2');
R.same = before === snap();
R.labels = [...fDecor.children].filter(c => c.tagName === 'TEXT').map(c => c.textContent);
R.rings = [...fDecor.children].filter(c => c.tagName === 'CIRCLE').length;
R.arrows = L.edges.filter(e => e.k === 'dep').every(e => e.el.getAttribute('marker-end') === 'url(#lanes-arrow)');
R.lines = [...new Set(L.edges.map(e => e.el.getAttribute('class').split(' ').slice(0, 2).join(' ')))].sort();
R.straight = L.edges.every(e => /^M[-\\d.]+,[-\\d.]+L[-\\d.]+,[-\\d.]+$/.test(e.el.getAttribute('d')));
R.wait = [...L.nodes.values()].filter(n => n.el.classList.contains('wait')).map(n => n.id).sort();
""")
        self.assertTrue(r["same"])
        modules = sorted({t["module"] for t in self.compiled["tasks"] if t["module"]})
        for module in modules:
            self.assertTrue(any(label.startswith(module + " · ") for label in r["labels"]), module)
        self.assertEqual(r["rings"], len(r["labels"]))
        self.assertTrue(r["arrows"])
        self.assertEqual(r["lines"], ["l-edge asm", "l-edge dep", "l-edge gov"])
        self.assertTrue(r["straight"])
        self.assertEqual(r["wait"], ["A-1", "T-DONEOWN", "T-EXIT", "T-OWN"])


@unittest.skipIf(NODE is None, "node is not installed")
class TestMotion(ForceCase):
    """AC-T-DASH-FORCE-01-05."""

    def test_reduced_motion_computes_to_rest_and_draws_once(self) -> None:
        r = self.run_page("key('2'); R.r = [rafCalls, FV.frames, FV.sim.done, document.getElementById('force-status').hidden];",
                          reduced=True)
        self.assertEqual(r["r"], [0, 1, True, True])

    def test_otherwise_the_animation_ends_by_itself_and_the_page_stays_usable(self) -> None:
        r = self.run_page("""
key('2');
R.running = [FV.sim.done, rafCalls > 0, document.getElementById('force-status').hidden];
shSelect('T-B2');
R.usable = shState.sel;
R.frames = flush();
R.end = [FV.sim.done, RAF.length, document.getElementById('force-status').hidden, FV.frames > 1, lStage.dataset.settledMs !== undefined];
""", reduced=False)
        self.assertEqual(r["running"], [False, True, False])
        self.assertEqual(r["usable"], "T-B2")
        self.assertGreater(r["frames"], 0)
        self.assertEqual(r["end"], [True, 0, True, True, True])


@unittest.skipIf(NODE is None, "node is not installed")
class TestWithoutPointer(ForceCase):
    """AC-T-DASH-FORCE-01-06."""

    def test_arrow_keys_follow_dependencies_and_enter_opens_the_record(self) -> None:
        r = self.run_page("""
key('2');
shSelect('T-B2');
key('ArrowLeft', lStage); R.left = shState.sel;
shSelect('T-B2');
key('ArrowRight', lStage); R.right = shState.sel;
key('Enter', lStage); R.enter = document.activeElement.id;
""")
        deps = next(t for t in self.compiled["tasks"] if t["id"] == "T-B2")["deps"]
        self.assertIn(r["left"], deps)
        self.assertIn("T-B2", next(t for t in self.compiled["tasks"] if t["id"] == r["right"])["deps"])
        self.assertEqual(r["enter"], "side-body")

    def test_text_view_and_relations_cover_every_node_and_line_shown(self) -> None:
        r = self.run_page("""
key('2');
shRenderText(); shRenderRelations();
R.rows = document.getElementById('text-rows').innerHTML;
R.rel = document.getElementById('rel-rows').innerHTML;
R.shown = [...L.nodes.values()].filter(n => !n.el.classList.contains('l-gone')).map(n => n.id);
R.lines = L.edges.filter(e => !e.el.classList.contains('l-gone')).map(e => [e.s, e.k, e.t]);
""")
        for nid in r["shown"]:
            self.assertIn(f'data-node="{nid}"', r["rows"])
        sentence = {"dep": "<code>{t}</code> depends on <code>{s}</code>", "gov": "<code>{s}</code> governs <code>{t}</code>",
                    "asm": "<code>{s}</code> is relied on by <code>{t}</code>"}
        for s, k, t in r["lines"]:
            self.assertIn(sentence[k].format(s=s, t=t), r["rel"])

    def test_no_request_to_any_network_address(self) -> None:
        page = self.page()
        self.assertNotRegex(page, r'<(?:script|link|img|iframe)[^>]+(?:src|href)="(?:https?:)?//')
        for call in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import("):
            self.assertNotIn(call, shell.SCRIPT)
        self.assertNotRegex(shell.STYLE, r"url\(\s*[\"']?(?:https?:)?//")


@unittest.skipIf(NODE is None, "node is not installed")
class TestScale(ForceCase):
    """AC-T-DASH-FORCE-01-07: the layout of a large graph settles; its weight is small.

    The figures for the repository's own chronicle and its tenfold fixture are
    measured in a browser and reported in the pull request."""

    def test_a_tenfold_sized_graph_settles(self) -> None:
        r = self.run_page("""
const items = [], links = [];
for (let m = 0; m < 250; m++) for (let t = 0; t < 9; t++) {
  items.push({ id: 'T-' + m + '-' + t, cluster: 'M-' + m });
  if (t) links.push({ s: 'T-' + m + '-' + (t - 1), t: 'T-' + m + '-' + t, k: 'dep' });
  if (t === 0 && m) links.push({ s: 'T-' + (m - 1) + '-8', t: 'T-' + m + '-0', k: 'dep' });
}
for (let d = 0; d < 1190; d++) {
  items.push({ id: 'ADR-' + d, cluster: null });
  for (let j = 0; j < 4; j++) links.push({ s: 'ADR-' + d, t: 'T-' + ((d * 7 + j * 31) % 250) + '-' + ((d + j) % 9), k: 'gov' });
}
for (let a = 0; a < 140; a++) { items.push({ id: 'A-' + a, cluster: null }); links.push({ s: 'A-' + a, t: 'T-' + (a % 250) + '-1', k: 'asm' }); }
const t0 = performance.now(); const sim = fSim(items, links); while (!sim.tick());
const at = new Map(items.map(i => [i.id, [0, 0]])); sim.place(at);
R.ms = performance.now() - t0; R.n = items.length;
R.finite = [...at.values()].every(p => isFinite(p[0]) && isFinite(p[1]));
""")
        self.assertEqual(r["n"], 3580)
        self.assertTrue(r["finite"])
        self.assertLess(r["ms"], 20000)

    def test_the_force_code_adds_little_to_the_page(self) -> None:
        script = shell.SCRIPT
        added = script.split("// --- The force layout: pure and seeded", 1)[1].split("// The workspace scrolls in the middle", 1)[0]
        self.assertLess(len(added.encode()), 20000)


class TestDocumentation(unittest.TestCase):
    """AC-T-DASH-FORCE-01-08."""

    def test_the_documentation_and_the_changelog_describe_the_force_view(self) -> None:
        doc = (ROOT / "docs" / "dashboard-ux.md").read_text(encoding="utf-8")
        section = doc.split("## The shell", 1)[1].split("\n## ", 1)[0]
        for words in ("**Force**", "analyse relations", "clustered by module", "`2` the force view", "`1` the lanes",
                      "opens on Work", "reduced motion", "same input"):
            self.assertIn(words, section)
        guide = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("opens on Work", guide)
        self.assertIn("force view", guide)
        fragment = (ROOT / "changelog.d" / "T-DASH-FORCE-01.md").read_text(encoding="utf-8")
        for words in ("force view", "clustered by module", "`2`", "`1`", "opens on Work"):
            self.assertIn(words, fragment)


if __name__ == "__main__":
    unittest.main()
