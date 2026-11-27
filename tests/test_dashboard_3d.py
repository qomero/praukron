"""The 3D view, a third renderer over the shell's nodes and lines (T-DASH-3D-01).

Every test reads the synthetic chronicle of test_dashboard_shell, or a tenfold
copy of it made here; no project's own records are used. Where node is
installed, the page's own shell script runs against the small stand-in for
the document of test_dashboard_force, extended with a canvas that records what
is drawn, a clock the test moves, and timers on that clock. Opening the view,
the turn, the keys, reduced motion, labels and the shared selection are
exercised as the page runs them, not only read as text.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
import unittest

from test_dashboard_force import FAKE_DOM, NODE, ForceCase, attributes  # noqa: F401
from test_dashboard_shell import ROOT, ShellCase

from praukron import dashboard, shell, view3d

# The clock starts before the stand-in so every `performance.now()` reads it.
CLOCK = r"""
let CLOCK = 1000;
Object.defineProperty(globalThis, 'performance', { value: { now: () => CLOCK }, configurable: true, writable: true });
const TIMERS = []; let timerId = 0;
globalThis.setTimeout = (f, ms) => { TIMERS.push({ id: ++timerId, at: CLOCK + (ms || 0), f }); return timerId; };
globalThis.clearTimeout = id => { const i = TIMERS.findIndex(t => t.id === id); if (i >= 0) TIMERS.splice(i, 1); };
"""

# A canvas that records the images and words drawn, and the rest of the stand-in.
CANVAS = r"""
const CTX_LOG = { text: [], images: [] };
function fakeCtx() {
  const t = {
    globalAlpha: 1, measureText: s => ({ width: String(s).length * 6 }),
    createRadialGradient: () => ({ addColorStop() {} }), createLinearGradient: () => ({ addColorStop() {} }),
    fillText(s, x, y) { CTX_LOG.text.push([String(s), x, y, this.globalAlpha]); },
    drawImage(img, x, y, w, h) { CTX_LOG.images.push([img.shape, x + w / 2, y + h / 2, this.globalAlpha]); },
  };
  return new Proxy(t, { get: (o, k) => (k in o ? o[k] : (typeof k === 'string' ? (o[k] = () => {}) : undefined)),
                        set: (o, k, v) => { o[k] = v; return true; } });
}
El.prototype.getContext = function () { this.contexts = (this.contexts || 0) + 1; return this._ctx || (this._ctx = fakeCtx()); };
document.createElement = tag => new El(tag);
const COLORS = { '--bg': '#f3f4f6', '--panel': '#ffffff', '--ink': '#14171c' };
globalThis.getComputedStyle = () => ({ getPropertyValue: k => COLORS[k] || '#808080', fontFamily: 'sans-serif' });
document.documentElement.dataset.tab = 'work';
selectTab = name => { openTab = name; document.documentElement.dataset.tab = name; };
// Move the clock one display frame at a time: due timers fire, then the frames asked for.
function advance(ms) {
  const end = CLOCK + ms; let frames = 0;
  while (CLOCK < end) {
    CLOCK += 1000 / 60;
    TIMERS.sort((a, b) => a.at - b.at);
    while (TIMERS.length && TIMERS[0].at <= CLOCK) TIMERS.shift().f();
    const due = RAF.splice(0); due.forEach(f => f(CLOCK)); frames += due.length;
  }
  return frames;
}
function press(k, target, shift) {
  const e = { key: k, target: target || document.body, metaKey: false, ctrlKey: false, altKey: false, shiftKey: Boolean(shift),
              defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  if (target && target !== document.body) target.fire('keydown', e);
  (docHandlers.keydown || []).forEach(f => f(e));
  return e.defaultPrevented;
}
const cv = () => document.getElementById('c3d');
const I = () => V3.inspect();
const drawn = () => I().stats.frames;
const at = id => { const v = I(), i = v.idx.get(id); return [v.SX[i], v.SY[i]]; };
const pointer = (type, x, y, extra) => cv().fire(type, Object.assign({ clientX: x, clientY: y, button: 0, shiftKey: false, pointerId: 1, type, preventDefault() {} }, extra || {}));
"""

FAKE_3D = FAKE_DOM.replace("const viewButtons = ['lanes', 'force']", "const viewButtons = ['lanes', 'force', '3d']")


class View3DCase(ForceCase):
    """Runs the page's shell script with the canvas stand-in and a moving clock."""

    def run_page(self, body: str, storage: dict | None = None, reduced: bool = False, page: str | None = None) -> dict:
        page = page or self.page()
        data = re.search(r'id="project-data">(.*?)</script>', page, re.S).group(1)
        model = re.search(r'id="shell-data">(.*?)</script>', page, re.S).group(1)
        prelude = ("const INITIAL_STORAGE = " + json.dumps({k: json.dumps(v) for k, v in (storage or {}).items()})
                   + ";\nconst REDUCED = " + ("true" if reduced else "false")
                   + ";\nconst ATTRS = " + json.dumps(attributes(page))
                   + ";\nconst SHELL_JSON = " + json.dumps(model) + ";\n")
        program = (prelude + CLOCK + FAKE_3D + CANVAS + dashboard.FILTER_JS + "\nconst DATA = " + data
                   + ";\nconst byId = Object.fromEntries(DATA.tasks.map(t => [t.id, t]));"
                   + "\nconst decisionById = Object.fromEntries(DATA.decisions.map(d => [d.id, d]));\n"
                   + shell.SCRIPT
                   + "\nconst snap = () => [...L.nodes.values()].map(n => n.id + ':' + n.el.getAttribute('class')).join('|');"
                   + "\nconst ids = () => shListed().map(n => n.id);\n"
                   + "const R = {};\n" + body + "\nconsole.log(JSON.stringify(R));\n")
        out = subprocess.run([NODE, "-"], input=program, capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr[-3000:])
        return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipIf(NODE is None, "node is not installed")
class TestOpen(View3DCase):
    """AC-T-DASH-3D-01-01."""

    def test_three_opens_the_view_and_one_and_two_return(self) -> None:
        r = self.run_page("""
R.before = [I().ready, cv().contexts || 0, cv().hidden, document.getElementById('v3-panel').hidden];
key('2'); advance(2000);
R.afterForce = [I().ready, cv().contexts || 0];
R.three = press('3'); advance(3000);
R.open = [shState.view, I().active, I().laid, drawn() > 0, cv().hidden, lStage.classList.contains('is3d'),
          viewButtons.map(b => b.getAttribute('aria-pressed')), lStage.getAttribute('aria-label')];
R.stored = JSON.parse(localStorage.getItem('praukron-panels')).view;
press('1'); const f1 = drawn(); advance(3000);
R.lanes = [shState.view, I().active, drawn() === f1, cv().hidden, lStage.classList.contains('is3d'),
           [...L.nodes.values()].every(n => n.pos === n.at.lanes)];
press('2'); const f2 = drawn(); advance(3000);
R.force = [shState.view, drawn() === f2, [...L.nodes.values()].every(n => n.pos === n.at.force)];
press('3'); const f3 = drawn(); advance(1000);
R.back = [shState.view, drawn() > f3];
viewButtons[0].fire('click', {}); R.click = shState.view; viewButtons[2].fire('click', {}); R.click3 = shState.view;
""")
        self.assertEqual(r["before"], [False, 0, True, True])
        self.assertEqual(r["afterForce"], [False, 0])
        self.assertTrue(r["three"])
        self.assertEqual(r["open"], ["3d", True, True, True, False, True, ["false", "false", "true"], "3D graph"])
        self.assertEqual(r["stored"], "3d")
        self.assertEqual(r["lanes"], ["lanes", False, True, True, False, True])
        self.assertEqual(r["force"], ["force", True, True])
        self.assertEqual(r["back"], ["3d", True])
        self.assertEqual([r["click"], r["click3"]], ["lanes", "3d"])

    def test_no_frame_while_another_workspace_or_a_hidden_tab_is_shown(self) -> None:
        r = self.run_page("""
press('3'); advance(3000);
const rect = lStage.getBoundingClientRect;
selectTab('home'); lStage.getBoundingClientRect = () => ({ width: 0, height: 0, left: 0, top: 0 });
advance(100); let f = drawn(); advance(5000);
R.workspace = [drawn() === f, RAF.length];
R.keys = press('p');
selectTab('work'); lStage.getBoundingClientRect = rect; V3.resize(); f = drawn(); advance(500);
R.again = drawn() > f;
document.hidden = true; advance(100); f = drawn(); advance(5000);
R.hidden = [drawn() === f, RAF.length];
document.hidden = false; (docHandlers.visibilitychange || []).forEach(h => h()); f = drawn(); advance(500);
R.shown = drawn() > f;
""")
        self.assertEqual(r["workspace"], [True, 0])
        self.assertFalse(r["keys"])  # the 3D keys do nothing while another workspace is shown
        self.assertTrue(r["again"])
        self.assertEqual(r["hidden"], [True, 0])
        self.assertTrue(r["shown"])

    def test_the_chosen_view_persists_across_visits(self) -> None:
        r = self.run_page("advance(2000); R.view = [shState.view, I().active, I().laid, viewButtons.map(b => b.getAttribute('aria-pressed'))];",
                          storage={"praukron-panels": {"view": "3d", "tab": "work"}})
        self.assertEqual(r["view"], ["3d", True, True, ["false", "false", "true"]])

    def test_one_control_per_view_and_none_for_later_views(self) -> None:
        section = shell.lanes_section(self.compiled)
        self.assertEqual(re.findall(r'<button[^>]*data-view="(\w+)"', section), ["lanes", "force", "3d"])
        for later in ('data-view="code"', ">Code<", "Architecture", "Layers"):
            self.assertNotIn(later, section)
        handler = shell.SCRIPT.split("// --- Keys, outside text fields", 1)[1].split("// --- The 3D view", 1)[0]
        self.assertIn("'3': () => shOpenLanes(shState.sel, '3d')", handler)
        self.assertNotIn("c: ", handler)  # c stays reserved

    def test_the_canvas_and_panel_stay_hidden_until_opened(self) -> None:
        # Their own display rule must not override the hidden attribute.
        self.assertIn(".c3d[hidden], .v3-panel[hidden] { display: none; }", view3d.STYLE)
        section = shell.lanes_section(self.compiled)
        self.assertRegex(section, r'<canvas id="c3d"[^>]* hidden')
        self.assertRegex(section, r'<div id="v3-panel"[^>]* hidden')

    def test_key_c_does_nothing_visible(self) -> None:
        r = self.run_page("press('3'); advance(2000); const s = snap(), v = shState.view; R.c = [press('c'), snap() === s, shState.view === v];")
        self.assertEqual(r["c"], [False, True, True])


@unittest.skipIf(NODE is None, "node is not installed")
class TestTurn(View3DCase):
    """AC-T-DASH-3D-01-02."""

    def test_by_default_it_turns_on_two_axes_one_vertical_turn_in_about_60_seconds(self) -> None:
        r = self.run_page("""
press('3'); advance(15000);
const a = I().cam; advance(6000); const b = I().cam;
R.mode = [I().S.mode, I().play, a.v > 0.98];
R.yaw = b.yaw - a.yaw; R.tilt = b.tilt - a.tilt;
R.panel = [document.getElementById('v3-play').textContent, document.getElementById('v3-mode').textContent,
           document.getElementById('v3-speed-o').textContent];
""")
        self.assertEqual(r["mode"], ["dual", True, True])
        self.assertAlmostEqual(r["yaw"], 2 * math.pi / 60 * 6, delta=0.03)
        self.assertAlmostEqual(r["tilt"], r["yaw"] / 2, delta=0.01)
        self.assertEqual(r["panel"], ["Pause", "Two axes", "1 turn in 60 s"])

    def test_keys_switch_the_mode_pause_and_change_the_speed_and_persist(self) -> None:
        r = self.run_page("""
press('3'); advance(15000);
R.m = press('m'); advance(10000); const a = I().cam; advance(3000); const b = I().cam;
R.single = [I().S.mode, Math.abs(b.tilt - a.tilt) < 1e-6, b.yaw > a.yaw];
R.faster = [press(']'), I().S.speed]; press('['); R.slower = I().S.speed; press(']');
R.p = press('p'); advance(3000); const c = I().cam; advance(2000); const d = I().cam;
R.paused = [I().play, d.yaw === c.yaw, I().running, RAF.length];
R.stored = JSON.parse(localStorage.getItem('praukron-3d'));
press('p'); advance(3000); R.resumed = [I().play, I().cam.v > 0];
""")
        self.assertTrue(r["m"])
        self.assertEqual(r["single"], ["single", True, True])
        self.assertEqual(r["faster"], [True, 1.25])
        self.assertEqual(r["slower"], 1)
        self.assertTrue(r["p"])
        self.assertEqual(r["paused"], [False, True, False, 0])
        self.assertEqual(r["stored"], {"mode": "single", "speed": 1.25, "playing": False, "hoverZoom": True})
        self.assertEqual(r["resumed"], [True, True])

    def test_a_stored_preference_wins_over_the_default(self) -> None:
        r = self.run_page("press('3'); advance(15000); R.s = [I().S, I().play, I().cam.v];",
                          storage={"praukron-3d": {"mode": "single", "speed": 2, "playing": False, "hoverZoom": False}})
        self.assertEqual(r["s"], [{"mode": "single", "speed": 2, "playing": False, "hoverZoom": False}, False, 0])

    def test_the_turn_stops_while_the_reader_interacts_and_eases_back(self) -> None:
        r = self.run_page("""
press('3'); advance(15000);
pointer('pointerdown', 100, 100); pointer('pointermove', 140, 120); advance(1500);
R.during = I().cam.v;
pointer('pointerup', 140, 120); advance(1000);
R.calm = I().cam.v;
advance(4000); const mid = I().cam.v; advance(8000);
R.back = [mid > 0 && mid < 0.99, I().cam.v > 0.95];
""")
        self.assertLess(r["during"], 0.01)
        self.assertLess(r["calm"], 0.01)
        self.assertEqual(r["back"], [True, True])


@unittest.skipIf(NODE is None, "node is not installed")
class TestNavigate(View3DCase):
    """AC-T-DASH-3D-01-03."""

    def test_drag_orbits_wheel_and_keys_zoom_and_shift_arrows_pan(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(6000);
let c = I().cam; pointer('pointerdown', 300, 300); pointer('pointermove', 360, 330); pointer('pointerup', 360, 330);
R.orbit = [I().cam.yaw !== c.yaw, I().cam.tilt !== c.tilt];
const fitDist = I().cam.gDist;
cv().fire('wheel', { deltaY: -300, preventDefault() {} }); R.wheel = I().cam.gDist < fitDist;
press('0', lStage); R.zero = Math.abs(I().cam.gDist - fitDist) < 1e-6;
press('+', lStage); R.plus = I().cam.gDist < fitDist; press('0', lStage);
press('-', lStage); R.minus = I().cam.gDist > fitDist; press('0', lStage);
c = I().cam; R.pan = press('ArrowLeft', lStage, true);
R.panned = [JSON.stringify(I().cam.gT) !== JSON.stringify(c.gT), shState.sel];
""")
        self.assertEqual(r["orbit"], [True, True])
        self.assertTrue(r["wheel"])
        self.assertTrue(r["zero"])
        self.assertTrue(r["plus"])
        self.assertTrue(r["minus"])
        self.assertTrue(r["pan"])
        self.assertEqual(r["panned"], [True, None])

    def test_resting_on_a_node_moves_the_view_toward_it_unless_turned_off(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(8000);
let [x, y] = at('T-B2'); pointer('pointermove', x, y); advance(100);
R.early = [I().hover === I().idx.get('T-B2'), I().cam.gA];
advance(600); R.dwell = [I().cam.gA, I().cam.dolly === I().idx.get('T-B2'), I().cam.a > 0];
pointer('pointerleave', 0, 0); advance(4000);
const hz = document.getElementById('v3-hz'); hz.fire('click', {}); R.pressed = hz.getAttribute('aria-pressed');
[x, y] = at('T-B2'); pointer('pointermove', x, y); advance(1500);
R.off = [I().hover === I().idx.get('T-B2'), I().cam.gA, I().S.hoverZoom, JSON.parse(localStorage.getItem('praukron-3d')).hoverZoom];
""")
        self.assertEqual(r["early"], [True, 0])
        self.assertEqual(r["dwell"], [0.5, True, True])
        self.assertEqual(r["off"], [True, 0, False, False])
        self.assertEqual(r["pressed"], "false")


@unittest.skipIf(NODE is None, "node is not installed")
class TestReducedMotion(View3DCase):
    """AC-T-DASH-3D-01-04."""

    def test_it_does_not_turn_and_no_view_change_is_animated(self) -> None:
        r = self.run_page("""
press('3'); advance(1000);
const a = I().cam; let f = drawn(); advance(20000);
R.still = [I().RM, I().play, I().cam.yaw === a.yaw, I().cam.tilt === a.tilt, drawn() === f, I().running, RAF.length,
           document.getElementById('v3-rm').hidden, I().cam.dist === I().cam.gDist];
shSelect('T-B2');
R.select = [shState.sel, JSON.stringify(I().cam.T) === JSON.stringify(I().cam.gT), I().cam.dist === I().cam.gDist];
f = drawn(); advance(5000); R.selectFrames = drawn() - f;
press('+', lStage); R.zoom = I().cam.dist === I().cam.gDist;
let c = I().cam; pointer('pointerdown', 300, 300); pointer('pointermove', 360, 330); pointer('pointerup', 360, 330);
R.orbit = I().cam.yaw !== c.yaw;
c = I().cam; press('ArrowUp', lStage, true); R.pan = [JSON.stringify(I().cam.T) === JSON.stringify(I().cam.gT), JSON.stringify(I().cam.T) !== JSON.stringify(c.T)];
let [x, y] = at('T-A1'); pointer('pointermove', x, y); advance(2000); R.hover = I().cam.gA;
""", reduced=True)
        self.assertEqual(r["still"], [True, False, True, True, True, False, 0, False, True])
        self.assertEqual(r["select"], ["T-B2", True, True])
        self.assertLessEqual(r["selectFrames"], 2)
        self.assertTrue(r["zoom"])
        self.assertTrue(r["orbit"])
        self.assertEqual(r["pan"], [True, True])
        self.assertEqual(r["hover"], 0)


@unittest.skipIf(NODE is None, "node is not installed")
class TestShared(View3DCase):
    """AC-T-DASH-3D-01-05."""

    def test_selection_filters_side_panel_and_fading_are_the_same(self) -> None:
        r = self.run_page("""
shSelect('T-B2'); filters.status.add('TODO'); shFiltersChanged();
const lanes = { snap: snap(), side: document.getElementById('side-body').innerHTML, ids: ids() };
press('3'); advance(3000);
const v3 = { snap: snap(), side: document.getElementById('side-body').innerHTML, ids: ids() };
R.same = [lanes.snap === v3.snap, lanes.side === v3.side, JSON.stringify(lanes.ids) === JSON.stringify(v3.ids), shState.sel];
const v = I(), alpha = id => v.SA[v.idx.get(id)];
const outside = [...L.nodes.values()].filter(n => n.dim && !n.gone && !shWait[n.id]).map(n => n.id);
const inside = [...shState.focus.F].filter(id => L.nodes.get(id) && !L.nodes.get(id).dim && !shWait[id]);
R.fades = [outside.length > 0, inside.length > 0, Math.max(...outside.map(alpha)) < Math.min(...inside.map(alpha))];
filters.status.clear(); shFiltersChanged(); shSelect('T-C1');
const back3 = { snap: snap(), side: document.getElementById('side-body').innerHTML };
press('2');
R.back = [back3.snap === snap(), back3.side === document.getElementById('side-body').innerHTML, shState.sel];
""")
        self.assertEqual(r["same"], [True, True, True, "T-B2"])
        self.assertEqual(r["fades"], [True, True, True])
        self.assertEqual(r["back"], [True, True, "T-C1"])

    def test_a_waiting_item_chosen_in_the_strip_is_framed(self) -> None:
        r = self.run_page("""
press('3'); advance(3000);
const card = { closest: s => (s === '[data-select]' ? { dataset: { select: 'T-OWN' } } : null) };
(docHandlers.click || []).forEach(h => h({ target: card, preventDefault() {} }));
const v = I(), i = v.idx.get('T-OWN');
R.framed = [shState.sel, v.cam.gT.map((c, k) => Math.abs(c - v.P[i * 3 + k]) < 1e-3)];
advance(4000); R.label = I().stats.labels.some(l => l.node === 'T-OWN');
""")
        self.assertEqual(r["framed"], ["T-OWN", [True, True, True]])
        self.assertTrue(r["label"])

    def test_search_reaches_the_same_selection(self) -> None:
        # A search result opens the record through showTask, which selects through the shell.
        self.assertIn("function showTask(id) {\n  if (!byId[id]) return;\n  shSelect(id);", self.page())


@unittest.skipIf(NODE is None, "node is not installed")
class TestLabels(View3DCase):
    """AC-T-DASH-3D-01-06."""

    OVERLAP = """
const boxes = I().stats.labels;
R.overlap = boxes.some((a, i) => boxes.some((b, j) => j > i && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y));
"""

    def test_depth_order_fading_and_no_label_over_another(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(8000);
const v = I(), o = v.order;
R.order = o.every((i, k) => k === 0 || v.SD[o[k - 1]] >= v.SD[i]);
const plain = o.filter(i => !v.list[i].dim && !shWait[v.list[i].id] && v.SV[i] === 1);
const far = plain[0], near = plain[plain.length - 1];
R.fainter = [v.SD[far] > v.SD[near], v.SA[far] < v.SA[near], v.SR[far] < v.SR[near]];
""" + self.OVERLAP)
        self.assertTrue(r["order"])
        self.assertEqual(r["fainter"], [True, True, True])
        self.assertFalse(r["overlap"])

    def test_the_selection_and_its_chain_are_always_labelled_even_when_crowded(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(8000);
shSelect('T-B2'); advance(6000);
V3.zoomBy(0.08); advance(8000);
const v = I(), shown = [...shState.focus.F].filter(id => v.SV[v.idx.get(id)] === 1);
const labelled = new Set(v.stats.labels.map(l => l.node));
R.chain = [shown.length > 2, shown.filter(id => !labelled.has(id))];
R.main = v.stats.labels.filter(l => l.kind === 'main').map(l => l.text);
R.moved = v.stats.labels.some(l => l.moved);
""" + self.OVERLAP)
        self.assertEqual(r["chain"][0], True)
        self.assertEqual(r["chain"][1], [])
        self.assertEqual(r["main"], ["T-B2 · Work on t-b2"])
        self.assertTrue(r["moved"])
        self.assertFalse(r["overlap"])

    def test_more_labels_appear_as_the_reader_zooms_in(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(8000);
const count = () => I().stats.labels.filter(l => l.kind === 'node').length;
R.far = count(); V3.zoomBy(3); advance(6000); R.near = count();
""" + self.OVERLAP)
        self.assertGreater(r["near"], r["far"])
        self.assertFalse(r["overlap"])

    def test_words_on_the_canvas_are_plain_text(self) -> None:
        # The page's translation markers belong in the document only; on the canvas they would print as boxes.
        canvas_part = view3d.SCRIPT.split("/* ---------- picking and hover */", 1)[0]
        self.assertNotIn("protectedText(", canvas_part)

    def test_status_waiting_and_kind_keep_their_shapes_and_words(self) -> None:
        r = self.run_page("""
press('3'); advance(3000); press('p'); advance(8000);
R.shapes = Object.fromEntries([...L.nodes.values()].map(n => [n.id, I().sprite(n.id)]));
R.recs = Object.fromEntries([...L.nodes.values()].map(n => [n.id, [n.kind, n.rec.status || '', Boolean(n.rec.ready)]]));
R.wait = Object.keys(shWait).sort();
R.diamonds = CTX_LOG.images.filter(x => x[0] === 'wait').length > 0;
R.waitLabels = I().stats.labels.filter(l => l.kind === 'wait').map(l => l.text).sort();
const [x, y] = at('T-OWN'); pointer('pointermove', x, y);
R.tip = lTip.innerHTML === shTipHtml(L.nodes.get('T-OWN'));
""")
        for nid, (kind, status, ready) in r["recs"].items():
            if kind == "task":
                want = {"DONE": "done", "BLOCKED": "blocked", "WIP": "wip"}.get(status, "ready" if ready else "todo")
            elif kind == "decision":
                want = "decision"
            else:
                want = "assumption-open" if status == "OPEN" else "assumption"
            self.assertEqual(r["shapes"][nid], want, nid)
        self.assertIn("done", r["shapes"].values())
        self.assertTrue(r["diamonds"])
        self.assertEqual(r["waitLabels"], ["! " + w for w in r["wait"] if w in r["shapes"]])
        self.assertTrue(r["tip"])


@unittest.skipIf(NODE is None, "node is not installed")
class TestWithoutPointer(View3DCase):
    """AC-T-DASH-3D-01-07."""

    def test_arrow_keys_follow_dependencies_and_enter_opens_the_record(self) -> None:
        r = self.run_page("""
press('3'); advance(3000);
shSelect('T-B2');
press('ArrowLeft', lStage); R.left = shState.sel;
shSelect('T-B2');
press('ArrowRight', lStage); R.right = shState.sel;
press('Enter', lStage); R.enter = document.activeElement.id;
R.view = shState.view;
""")
        deps = next(t for t in self.compiled["tasks"] if t["id"] == "T-B2")["deps"]
        self.assertIn(r["left"], deps)
        self.assertIn("T-B2", next(t for t in self.compiled["tasks"] if t["id"] == r["right"])["deps"])
        self.assertEqual(r["enter"], "side-body")
        self.assertEqual(r["view"], "3d")

    def test_text_view_and_relations_cover_every_node_and_line_shown(self) -> None:
        r = self.run_page("""
press('3'); advance(3000);
document.getElementById('lanes-governs').setAttribute('aria-pressed', 'true'); lRefresh(); advance(1000);
shRenderText(); shRenderRelations();
R.rows = document.getElementById('text-rows').innerHTML;
R.rel = document.getElementById('rel-rows').innerHTML;
const v = I();
R.shown = v.list.filter((n, i) => v.SV[i] > 0).map(n => n.id);
R.lines = L.edges.filter(e => !e.gone).map(e => [e.s, e.k, e.t]);
""")
        self.assertGreater(len(r["shown"]), 5)
        for nid in r["shown"]:
            self.assertIn(f'data-node="{nid}"', r["rows"])
        sentence = {"dep": "<code>{t}</code> depends on <code>{s}</code>", "gov": "<code>{s}</code> governs <code>{t}</code>",
                    "asm": "<code>{s}</code> is relied on by <code>{t}</code>"}
        self.assertTrue(any(k == "gov" for _, k, _ in r["lines"]))
        for s, k, t in r["lines"]:
            self.assertIn(sentence[k].format(s=s, t=t), r["rel"])

    def test_no_network_and_no_library(self) -> None:
        page = self.page()
        self.assertNotRegex(page, r'<(?:script|link|img|iframe)[^>]+(?:src|href)="(?:https?:)?//')
        for source in (view3d.SCRIPT, shell.SCRIPT):
            for call in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import(", "require(",
                         "importScripts", "webgl", "WebGL", "THREE", "Worker("):
                self.assertNotIn(call, source)
        self.assertEqual(re.findall(r"getContext\('([^']+)'\)", view3d.SCRIPT), ["2d", "2d"])
        self.assertNotRegex(view3d.STYLE, r"url\(")


def tenfold(page: str) -> str:
    """The page with every task, decision and assumption copied ten times, as
    the measurement in the pull request does with the repository's own page."""
    data_m = re.search(r'(id="project-data">)(.*?)(</script>)', page, re.S)
    shell_m = re.search(r'(id="shell-data">)(.*?)(</script>)', page, re.S)
    data, model = json.loads(data_m.group(2)), json.loads(shell_m.group(2))
    ren = lambda k, i: k if i == 0 else f"{k}~{i}"  # noqa: E731
    tasks, decisions, assumptions, pos = [], [], [], {}
    for i in range(10):
        for t in data["tasks"]:
            tasks.append(dict(t, id=ren(t["id"], i), deps=[ren(d, i) for d in t["deps"]], blocks=[ren(d, i) for d in t.get("blocks", [])],
                              decisions=[ren(d, i) for d in t["decisions"]]))
        for d in data["decisions"]:
            decisions.append(dict(d, id=ren(d["id"], i), affects=[ren(t, i) for t in d["affects"]]))
        for a in data["assumptions"]:
            assumptions.append(dict(a, id=ren(a["id"], i), tasks=[ren(t, i) for t in a["tasks"]]))
        for k, p in model["lanes"]["positions"].items():
            pos[ren(k, i)] = [p[0], p[1] + i * 2000]
    data.update(tasks=tasks, decisions=decisions, assumptions=assumptions)
    model["lanes"]["positions"] = pos
    page = page.replace(data_m.group(0), data_m.group(1) + json.dumps(data).replace("</", "<\\/") + data_m.group(3))
    return page.replace(shell_m.group(0), shell_m.group(1) + json.dumps(model).replace("</", "<\\/") + shell_m.group(3))


@unittest.skipIf(NODE is None, "node is not installed")
class TestScale(View3DCase):
    """AC-T-DASH-3D-01-08: a tenfold project lays out and draws; the view adds little.

    The time to the first frame, the frame time while turning and the added
    page size for the repository's own chronicle and a tenfold fixture are
    measured in a browser and reported in the pull request."""

    def test_a_tenfold_project_lays_out_and_draws(self) -> None:
        page = tenfold(self.page())
        started = time.monotonic()
        r = self.run_page("""
const t0 = Date.now(); press('3'); advance(3000);
R.ms = Date.now() - t0; const v = I();
R.n = v.N; R.laid = v.laid; R.finite = [...v.P].every(isFinite); R.frames = drawn() > 0;
const xs = []; for (let i = 0; i < v.N; i++) xs.push(v.P[i * 3]);
R.spread = Math.max(...xs) - Math.min(...xs);
""", page=page)
        self.assertGreater(r["n"], 100)
        self.assertTrue(r["laid"])
        self.assertTrue(r["finite"])
        self.assertTrue(r["frames"])
        self.assertGreater(r["spread"], 100)
        self.assertLess(time.monotonic() - started, 120)

    def test_the_octree_path_settles_a_graph_above_the_exact_limit(self) -> None:
        # a hundred times the synthetic project: above the exact repulsion limit
        r = self.run_page("""
const t0 = Date.now(); press('3'); advance(2000);
const v = I(); R.n = v.N; R.ms = Date.now() - t0; R.finite = [...v.P].every(isFinite);
""", page=tenfold(tenfold(self.page())))
        self.assertGreater(r["n"], 1200)
        self.assertTrue(r["finite"])
        self.assertLess(r["ms"], 60000)

    def test_the_view_adds_little_to_the_page(self) -> None:
        added = len((view3d.SCRIPT + view3d.STYLE + view3d.controls()).encode())
        self.assertLess(added, 60000)


class TestReplaced(ShellCase):
    """The force pull request pinned 'no 3D control' and 'key 3 unbound'; this
    pull request adds both on purpose, so those assertions now check the
    third view instead (see TestOpen)."""

    def test_the_lanes_section_now_carries_the_third_view(self) -> None:
        section = self.page().split('id="lanes"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-view="3d" aria-pressed="false"', section)
        self.assertIn('id="c3d"', section)


class TestDocumentation(unittest.TestCase):
    """AC-T-DASH-3D-01-09."""

    def test_the_documentation_and_the_changelog_describe_the_3d_view(self) -> None:
        doc = (ROOT / "docs" / "dashboard-ux.md").read_text(encoding="utf-8")
        section = doc.split("## The shell", 1)[1].split("\n## ", 1)[0]
        for words in ("**3D**", "immersive", "not the operational", "`3` the 3D view", "two axes", "60 seconds",
                      "`m`", "`p`", "`[`", "`]`", "Shift", "reduced motion"):
            self.assertIn(words, section)
        guide = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("3D view", guide)
        fragment = (ROOT / "changelog.d" / "T-DASH-3D-01.md").read_text(encoding="utf-8")
        for words in ("3D view", "immersive", "two axes", "`3`", "`m`", "`p`", "reduced motion"):
            self.assertIn(words, fragment)


if __name__ == "__main__":
    unittest.main()
