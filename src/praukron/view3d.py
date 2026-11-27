"""The dashboard's 3D view: a third renderer over the shell's nodes and lines.

The view is for exploring the project, not for operational reading: phases
are stacked floors, modules cluster on their floor, and decisions and
assumptions float near the tasks they bear on. It is drawn by hand on one 2D
canvas with its own perspective projection; there is no library, no build
step and no network. It shares the shell's selection, filters, search, side
panel and waiting model rather than copying them, and it writes nothing.

Nothing of it runs before the reader first opens it: no layout, no frame and
no listener on the canvas. Frames are drawn only while the view is open, the
Work graph is visible, the tab is shown and something moves.
"""

from __future__ import annotations


def controls() -> str:
    """The canvas and the small panel of the 3D view, inside the graph surface.
    Both stay hidden until the view is opened."""
    return (
        '<canvas id="c3d" class="c3d" hidden aria-hidden="true"></canvas>'
        '<div id="v3-panel" class="v3-panel" role="group" aria-label="3D view" hidden>'
        '<div class="v3-row">'
        '<button type="button" class="edge-btn" id="v3-play" title="Pause or resume the turn (p)">Pause</button>'
        '<button type="button" class="edge-btn" id="v3-mode" title="Turn on two axes or on one (m)">Two axes</button>'
        '<button type="button" class="edge-btn" id="v3-fit" title="The whole scene (0)">Whole view</button></div>'
        '<div class="v3-row"><button type="button" class="edge-btn" id="v3-slower" title="Slower ([)" aria-label="Slower">−</button>'
        '<span id="v3-speed-o" class="v3-out">1 turn in 60 s</span>'
        '<button type="button" class="edge-btn" id="v3-faster" title="Faster (])" aria-label="Faster">+</button></div>'
        '<div class="v3-row"><button type="button" class="edge-btn" id="v3-hz" aria-pressed="true"'
        ' title="Move the view toward a node the pointer rests on">Move toward a node</button></div>'
        '<p id="v3-rm" class="note" hidden>Reduced motion: the scene does not turn by itself, and the view does not move by itself.</p>'
        '<p id="v3-status" class="note v3-out"></p></div>')


STYLE = """
/* 3D: the third view on the same surface and the same nodes */
.c3d { position: absolute; inset: 0; width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
.stage.is3d #lanes-svg { display: none; }
.c3d[hidden], .v3-panel[hidden] { display: none; }
.v3-panel { position: absolute; right: 8px; top: 8px; z-index: 4; display: flex; flex-direction: column; gap: 4px;
            background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px;
            font-size: 12px; max-width: 250px; opacity: .93; }
.v3-row { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.v3-out { color: var(--muted); font-variant-numeric: tabular-nums; }
.v3-panel .note { margin: 0; font-size: 11.5px; }
.v3-panel .edge-btn { padding: 2px 7px; font-size: 12px; }
"""


# Runs inside the shell script, after the lanes and force views are defined:
# it reads their nodes (L), lines, selection (shState), waiting items (shWait),
# stage (lStage), tooltip (lTip) and seeded generator (fRng).
SCRIPT = r"""
// --- The 3D view: a third renderer over the same nodes and lines --------
// Port of the accepted prototype: phases are stacked floors (the first on
// top), modules cluster on their floor, decisions and assumptions float near
// what they bear on. One 2D canvas, its own perspective projection.
const V3 = (() => {
  const KEY = 'praukron-3d';
  const DEF = { mode: 'dual', speed: 1, playing: true, hoverZoom: true };
  const TURN = 60;          // seconds per vertical turn at speed 1
  const IDLE = 3500;        // ms of calm after an interaction before the turn eases back in
  const DWELL = 320;        // ms a pointer rests on a node before the view moves toward it
  const HOME_TILT = 0.42;   // about 24 degrees: the floors are seen from slightly above
  const FOCUS_TILT = 0.95;  // about 54 degrees: a selected chain is seen from higher up
  const SPACING = 1.9, CAP = 60, ITER = 300, EXACT = 1200;
  const BASE = { task: 3.2, decision: 2.6, assumption: 3 };
  const LK = { dep: [115, 0.22], gov: [210, 0.018], asm: [170, 0.05] };
  let ready = false, active = false, RM = false, play = false, laid = false, laying = null;
  let S = null, cv = null, ctx = null, panel = null, W = 1, H = 1, DPR = 1;
  let list = [], idx = new Map(), N = 0, P = null;
  let SX, SY, SD, SR, SA, SV, groupOf, isWait, order = [];
  let groups = [], planes = [], floorY = new Map(), floorOf = [];
  const cam = { yaw: 0.7, tilt: HOME_TILT, tiltGoal: null, v: 0, T: [0, 0, 0], gT: [0, 0, 0], dist: 1000, gDist: 1000,
                a: 0, gA: 0, dolly: -1, R: 300, ref: 1000 };
  let hover = -1, hoverG = -1, hoverSince = 0, lastInteract = -1e9, drag = null, pointer = null;
  let hoverSet = null, hoverEdges = null;
  let running = false, lastDraw = 0, resumeTimer = 0, fpsN = 0, fpsT = 0, fpsMs = 0;
  let C = {}, dark = false, bgGrad = null, font = 'system-ui, sans-serif', SP = {};
  const widthCache = new Map();
  const stats = { openedAt: null, layoutMs: null, firstFrameMs: null, frames: 0, turning: [], labels: [] };

  /* ---------- small helpers */
  const now = () => performance.now();
  const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
  const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
  const ease = (cur, goal, tau, dt) => tau <= 0.002 ? goal : cur + (goal - cur) * (1 - Math.exp(-dt / tau));
  const wrap = a => a - 2 * Math.PI * Math.round(a / (2 * Math.PI));
  const rgb = c => {
    c = (c || '').trim(); let m;
    if ((m = /^#([0-9a-f]{3})$/i.exec(c))) return [...m[1]].map(h => parseInt(h + h, 16));
    if ((m = /^#([0-9a-f]{6})$/i.exec(c))) return [0, 2, 4].map(k => parseInt(m[1].slice(k, k + 2), 16));
    if ((m = /rgba?\(([^)]+)\)/.exec(c))) return m[1].split(',').slice(0, 3).map(Number);
    return [128, 128, 128];
  };
  const css = (a, al = 1) => 'rgba(' + (a[0] | 0) + ',' + (a[1] | 0) + ',' + (a[2] | 0) + ',' + al + ')';
  const mix = (a, b, t) => a.map((v, k) => v + (b[k] - v) * t);
  const WHITE = [255, 255, 255], BLACK = [0, 0, 0];
  const clip = (s, k) => { s = String(s || ''); return s.length > k ? s.slice(0, k - 1) + '…' : s; };
  const short = n => n.kind === 'task' ? n.id.replace(/^T-/, '') : n.id;
  // Words drawn on the canvas are plain text: the page's translation markers are for the document only.
  const title = n => n.id + ' · ' + clip(n.rec.title, 46);
  const say = text => shSay(text);

  /* ---------- the reader's settings: a stored one wins over the default */
  function loadPrefs() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (error) { saved = null; }
    const s = Object.assign({}, DEF);
    if (saved && typeof saved === 'object') {
      if (saved.mode === 'dual' || saved.mode === 'single') s.mode = saved.mode;
      if (typeof saved.speed === 'number' && isFinite(saved.speed)) s.speed = clamp(saved.speed, 0.25, 3);
      if (typeof saved.playing === 'boolean') s.playing = saved.playing;
      if (typeof saved.hoverZoom === 'boolean') s.hoverZoom = saved.hoverZoom;
    }
    return s;
  }
  function savePrefs() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (error) {} }

  /* ---------- layout: force in space, pulled toward the phase floors */
  // Seeded and run once, when the view is first opened, in slices so the page
  // stays usable. Exact repulsion for this size of graph; an octree
  // (Barnes-Hut) above EXACT nodes, so a tenfold project still settles.
  function makeLayout() {
    const sp = SPACING, rand = fRng(17), FL = SHELL.lanes.columns.map(c => c.id), nF = FL.length, GAP = 190 * sp;
    const fI = new Map(FL.map((f, i) => [f, i]));
    const phY = f => { const i = fI.get(f); return i == null ? 0 : ((nF - 1) / 2 - i) * GAP; };
    floorY = new Map(FL.map(f => [f, phY(f)]));
    const Q = new Float64Array(N * 3), V = new Float64Array(N * 3), plane = new Float64Array(N), hasPlane = new Uint8Array(N);
    const modPos = new Map();
    FL.forEach((f, fi) => {
      const ms = [...new Set(SHELL.lanes.bands.filter(b => b.phase === f).map(b => b.id))];
      ms.forEach((m, j) => {
        const a = j / Math.max(1, ms.length) * Math.PI * 2 + fi * 0.9;
        const r = ms.length > 1 ? 170 * sp * Math.sqrt(ms.length) : 0;
        modPos.set(f + '|' + m, [Math.cos(a) * r, phY(f), Math.sin(a) * r]);
      });
    });
    list.forEach((n, i) => {
      floorOf[i] = null;
      if (n.kind !== 'task') return;
      const f = n.rec.phase, m = modPos.get(f + '|' + (n.rec.module || '~none')) || [0, phY(f), 0];
      Q[i * 3] = m[0] + (rand() - 0.5) * 90 * sp; Q[i * 3 + 1] = phY(f) + (rand() - 0.5) * 20; Q[i * 3 + 2] = m[2] + (rand() - 0.5) * 90 * sp;
      plane[i] = phY(f); hasPlane[i] = 1; floorOf[i] = fI.has(f) ? f : null;
    });
    // decisions and assumptions start at the mean of what they bear on
    list.forEach((n, i) => {
      if (hasPlane[i]) return;
      const ts = lOut(n.id).map(e => idx.get(e.t)).filter(j => j != null && hasPlane[j]);
      for (let k = 0; k < 3; k++) Q[i * 3 + k] = (ts.length ? ts.reduce((s, j) => s + Q[j * 3 + k], 0) / ts.length : 0) + (rand() - 0.5) * 160 * sp;
    });
    const Ls = L.edges.map(e => ({ a: idx.get(e.s), b: idx.get(e.t), len: LK[e.k][0] * sp, str: LK[e.k][1] })).filter(l => l.a != null && l.b != null && l.a !== l.b);
    const deg = new Float64Array(N); Ls.forEach(l => { deg[l.a]++; deg[l.b]++; });
    // spacing changes the shape, not only the scale: repulsion grows faster than the link length
    const REP = 9000 * Math.pow(sp, 4.2), CUT = Math.pow(700 * sp, 2), cut = Math.sqrt(CUT);
    const exact = N <= EXACT;
    let it = 0;
    function repelExact(alpha) {
      for (let i = 0; i < N; i++) {
        const ix = i * 3;
        for (let j = i + 1; j < N; j++) {
          const jx = j * 3;
          let dx = Q[ix] - Q[jx], dy = Q[ix + 1] - Q[jx + 1], dz = Q[ix + 2] - Q[jx + 2];
          let d2 = dx * dx + dy * dy + dz * dz;
          if (d2 > CUT) continue;
          if (d2 < 1) { dx = rand() - 0.5; dz = rand() - 0.5; d2 = 1; }
          const d = Math.sqrt(d2), fr = REP * alpha / d2 / d;
          dx *= fr; dy *= fr; dz *= fr;
          V[ix] += dx; V[ix + 1] += dy; V[ix + 2] += dz; V[jx] -= dx; V[jx + 1] -= dy; V[jx + 2] -= dz;
        }
      }
    }
    // The octree: built top down by partitioning an index array in place.
    const perm = new Int32Array(N), tmp = new Int32Array(N), MAXN = N * 4 + 64, LEAF = 6;
    const tm = new Float64Array(MAXN), tx = new Float64Array(MAXN), ty = new Float64Array(MAXN), tz = new Float64Array(MAXN);
    const th = new Float64Array(MAXN), tlo = new Int32Array(MAXN), thi = new Int32Array(MAXN), tc = new Int32Array(MAXN), tn = new Uint8Array(MAXN);
    let used = 0;
    function repelTree(alpha) {
      let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
      for (let i = 0; i < N; i++) {
        perm[i] = i;
        const x = Q[i * 3], y = Q[i * 3 + 1], z = Q[i * 3 + 2];
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; if (z < z0) z0 = z; if (z > z1) z1 = z;
      }
      used = 0;
      buildBF((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2, Math.max(x1 - x0, y1 - y0, z1 - z0) / 2 + 1);
      const TH2 = 0.8 * 0.8, stack = new Int32Array(used + 64);
      for (let i = 0; i < N; i++) {
        const ix = i * 3, qx = Q[ix], qy = Q[ix + 1], qz = Q[ix + 2];
        let fx = 0, fy = 0, fz = 0, sp_ = 0; stack[sp_++] = 0;
        while (sp_) {
          const k = stack[--sp_];
          const dx = qx - tx[k], dy = qy - ty[k], dz = qz - tz[k], d2 = dx * dx + dy * dy + dz * dz;
          const reach = cut + th[k] * 1.74;
          if (d2 > reach * reach) continue;
          const s = th[k] * 2;
          if (tn[k] === 0 || (s * s < TH2 * d2)) {
            if (tn[k] !== 0) { // far enough: the cell acts as one body
              if (d2 > CUT) continue;
              const d = Math.sqrt(d2), fr = REP * alpha * tm[k] / d2 / d;
              fx += dx * fr; fy += dy * fr; fz += dz * fr; continue;
            }
            for (let p = tlo[k]; p < thi[k]; p++) {
              const j = perm[p]; if (j === i) continue;
              let ex = qx - Q[j * 3], ey = qy - Q[j * 3 + 1], ez = qz - Q[j * 3 + 2], e2 = ex * ex + ey * ey + ez * ez;
              if (e2 > CUT) continue;
              if (e2 < 1) { ex = (i < j ? 0.5 : -0.5); ez = (i < j ? -0.5 : 0.5); e2 = 1; }
              const d = Math.sqrt(e2), fr = REP * alpha / e2 / d;
              fx += ex * fr; fy += ey * fr; fz += ez * fr;
            }
            continue;
          }
          for (let c = 0; c < tn[k]; c++) stack[sp_++] = tc[k] + c;
        }
        V[ix] += fx; V[ix + 1] += fy; V[ix + 2] += fz;
      }
    }
    // Breadth first, so each cell's children sit next to each other.
    function buildBF(cx, cy, cz, h) {
      const queue = [[0, N, cx, cy, cz, h, 0]];
      used = 1;
      for (let q = 0; q < queue.length; q++) {
        const [lo, hi, x, y, z, hh, depth] = queue[q], k = q;
        th[k] = hh; tlo[k] = lo; thi[k] = hi; tn[k] = 0; tc[k] = -1;
        let sx = 0, sy = 0, sz = 0;
        for (let p = lo; p < hi; p++) { const i = perm[p]; sx += Q[i * 3]; sy += Q[i * 3 + 1]; sz += Q[i * 3 + 2]; }
        const m = hi - lo; tm[k] = m; tx[k] = sx / m; ty[k] = sy / m; tz[k] = sz / m;
        if (m <= LEAF || depth > 16 || used + 8 > MAXN) continue;
        const count = new Int32Array(9);
        const oct = i => (Q[i * 3] >= x ? 1 : 0) | (Q[i * 3 + 1] >= y ? 2 : 0) | (Q[i * 3 + 2] >= z ? 4 : 0);
        for (let p = lo; p < hi; p++) count[oct(perm[p]) + 1]++;
        for (let o = 0; o < 8; o++) count[o + 1] += count[o];
        const at = count.slice(0, 8);
        for (let p = lo; p < hi; p++) { const i = perm[p]; tmp[lo + at[oct(i)]++] = i; }
        for (let p = lo; p < hi; p++) perm[p] = tmp[p];
        const h2 = hh / 2;
        tc[k] = used;
        for (let o = 0; o < 8; o++) {
          const a = lo + count[o], b = lo + count[o + 1];
          if (b <= a) continue;
          queue.push([a, b, x + (o & 1 ? h2 : -h2), y + (o & 2 ? h2 : -h2), z + (o & 4 ? h2 : -h2), h2, depth + 1]);
          used++; tn[k]++;
        }
      }
    }
    function tick() {
      if (it >= ITER) return true;
      const alpha = 1 - it / ITER;
      if (exact) repelExact(alpha); else repelTree(alpha);
      for (const l of Ls) {
        const a = l.a * 3, b = l.b * 3;
        const dx = Q[b] - Q[a], dy = Q[b + 1] - Q[a + 1], dz = Q[b + 2] - Q[a + 2];
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1, fs = (d - l.len) / d * l.str * alpha;
        const wa = deg[l.b] / (deg[l.a] + deg[l.b]), wb = 1 - wa;
        V[a] += dx * fs * wa; V[a + 1] += dy * fs * wa; V[a + 2] += dz * fs * wa;
        V[b] -= dx * fs * wb; V[b + 1] -= dy * fs * wb; V[b + 2] -= dz * fs * wb;
      }
      for (let i = 0; i < N; i++) {
        const ix = i * 3;
        if (hasPlane[i]) V[ix + 1] += (plane[i] - Q[ix + 1]) * 0.05; // a floor has some thickness: chains do not flatten into one line
        V[ix] -= Q[ix] * 0.0015 * alpha; V[ix + 2] -= Q[ix + 2] * 0.0015 * alpha;
        for (let k = 0; k < 3; k++) { Q[ix + k] += V[ix + k] * 0.5; V[ix + k] *= 0.55; }
      }
      it += 1;
      return it >= ITER;
    }
    function result() {
      let mx = 0, mz = 0; for (let i = 0; i < N; i++) { mx += Q[i * 3]; mz += Q[i * 3 + 2]; }
      mx /= N || 1; mz /= N || 1; for (let i = 0; i < N; i++) { Q[i * 3] -= mx; Q[i * 3 + 2] -= mz; }
      return Float32Array.from(Q);
    }
    return { tick, result, get done() { return it >= ITER; }, exact };
  }

  // groups for semantic zoom (a module on its floor), floors, and the reference framing
  function buildGroups() {
    const m = new Map();
    list.forEach((n, i) => {
      groupOf[i] = -1;
      if (n.kind !== 'task') return;
      const key = n.rec.phase + '|' + (n.rec.module || '~none');
      (m.get(key) || m.set(key, []).get(key)).push(i);
    });
    groups = [...m].map(([key, mem], gi) => {
      mem.forEach(i => { groupOf[i] = gi; });
      const id = key.split('|')[1];
      return { key, id, name: id === '~none' ? 'No module' : id, mem, c: [0, 0, 0], r: 0, t: 0, n: 0, sx: 0, sy: 0, sr: 0, d: 0, vis: false };
    });
    groups.forEach(g => { const s = sphere(P, g.mem); g.c = s.c; g.r = Math.max(s.r, 45 * SPACING); });
    planes = SHELL.lanes.columns.map(col => {
      const mem = []; list.forEach((n, i) => { if (floorOf[i] === col.id) mem.push(i); });
      if (!mem.length) return null;
      const s = sphere(P, mem);
      const ds = mem.map(i => Math.hypot(P[i * 3] - s.c[0], P[i * 3 + 2] - s.c[2]));
      return { id: col.id, label: col.id + ' · ' + clip(col.name, 28), mem,
               c: [s.c[0], floorY.get(col.id), s.c[2]], r: Math.max(...ds) + 40 * SPACING };
    }).filter(Boolean);
    const all = sphere(P, list.map((_, i) => i));
    cam.R = Math.max(all.r, 80); cam.ref = cam.R * 2.7;
  }
  function sphere(Q, ids) {
    const c = [0, 0, 0]; if (!ids.length) return { c, r: 0 };
    ids.forEach(i => { for (let k = 0; k < 3; k++) c[k] += Q[i * 3 + k]; });
    for (let k = 0; k < 3; k++) c[k] /= ids.length;
    const ds = ids.map(i => Math.hypot(Q[i * 3] - c[0], Q[i * 3 + 1] - c[1], Q[i * 3 + 2] - c[2])).sort((a, b) => a - b);
    return { c, r: ds[Math.min(ds.length - 1, Math.floor(ds.length * 0.92))] };
  }

  /* ---------- colours and sprites, pre-drawn once per theme and drawn scaled */
  function readTheme() {
    const cs = getComputedStyle(document.documentElement), v = k => rgb(cs.getPropertyValue('--' + k));
    ['bg', 'panel', 'ink', 'muted', 'line', 'line-strong', 'accent', 'good', 'todo', 'wip', 'bad', 'warn', 'decision', 'assumption']
      .forEach(k => { C[k] = v(k); });
    dark = (C.bg[0] + C.bg[1] + C.bg[2]) / 3 < 110;
    font = (getComputedStyle(document.body).fontFamily) || font;
    widthCache.clear(); bgGrad = null; SP = {};
    const R0 = 20;
    const shade = (g, col, r) => {
      const gr = g.createRadialGradient(-r * 0.35, -r * 0.42, r * 0.08, 0, 0, r * 1.05);
      gr.addColorStop(0, css(mix(col, WHITE, 0.6))); gr.addColorStop(0.45, css(col)); gr.addColorStop(1, css(mix(col, BLACK, 0.5))); return gr;
    };
    const lit = (g, col, r) => { const gr = g.createLinearGradient(-r, -r, r, r); gr.addColorStop(0, css(mix(col, WHITE, 0.5))); gr.addColorStop(1, css(mix(col, BLACK, 0.2))); return gr; };
    const make = (name, size, fn) => {
      const c = document.createElement('canvas'); c.width = c.height = size;
      const g = c.getContext('2d'); g.translate(size / 2, size / 2); fn(g); c.k = size / R0; c.shape = name; return c;
    };
    const ring = (name, col, w, r, extra) => make(name, 64, g => {
      g.fillStyle = css(col, dark ? 0.18 : 0.12); g.beginPath(); g.arc(0, 0, r, 0, 7); g.fill();
      g.strokeStyle = lit(g, col, r); g.lineWidth = w; g.beginPath(); g.arc(0, 0, r, 0, 7); g.stroke();
      if (extra) extra(g);
    });
    // the shapes of the other views: done a filled circle, to do a ring, in
    // progress a ring with a dot, ready a thick ring, blocked a ring with a
    // cross, a decision a square, an assumption a triangle (filled while open)
    SP.done = make('done', 64, g => { g.fillStyle = shade(g, C.good, 18); g.beginPath(); g.arc(0, 0, 18, 0, 7); g.fill(); });
    SP.todo = ring('todo', C.todo, 4.5, 16);
    SP.wip = ring('wip', C.wip, 4.5, 16, g => { g.fillStyle = css(C.wip); g.beginPath(); g.arc(0, 0, 6, 0, 7); g.fill(); });
    SP.ready = ring('ready', C.accent, 8.5, 15);
    SP.blocked = ring('blocked', C.bad, 4.5, 16, g => {
      g.lineWidth = 4; g.lineCap = 'round'; g.beginPath(); g.moveTo(-8, -8); g.lineTo(8, 8); g.moveTo(8, -8); g.lineTo(-8, 8); g.stroke();
    });
    SP.decision = make('decision', 64, g => { g.fillStyle = shade(g, C.decision, 17); g.beginPath(); g.rect(-15, -15, 30, 30); g.fill(); });
    SP.assumptionOpen = make('assumption-open', 64, g => { g.fillStyle = shade(g, C.assumption, 18); g.beginPath(); g.moveTo(0, -19); g.lineTo(18, 14); g.lineTo(-18, 14); g.closePath(); g.fill(); });
    SP.assumption = make('assumption', 64, g => {
      g.fillStyle = css(C.assumption, dark ? 0.18 : 0.12); g.strokeStyle = lit(g, C.assumption, 16); g.lineWidth = 4; g.lineJoin = 'round';
      g.beginPath(); g.moveTo(0, -18); g.lineTo(17, 13); g.lineTo(-17, 13); g.closePath(); g.fill(); g.stroke();
    });
    SP.wait = make('wait', 112, g => {
      g.shadowColor = css(C.warn, 0.8); g.shadowBlur = 8; g.strokeStyle = css(C.warn); g.lineWidth = 5; g.lineJoin = 'round';
      g.beginPath(); g.moveTo(0, -40); g.lineTo(40, 0); g.lineTo(0, 40); g.lineTo(-40, 0); g.closePath(); g.stroke();
    });
    const halo = (name, col) => make(name, 128, g => {
      const gr = g.createRadialGradient(0, 0, 0, 0, 0, 62); gr.addColorStop(0, css(col, 0.55)); gr.addColorStop(0.35, css(col, 0.22)); gr.addColorStop(1, css(col, 0));
      g.fillStyle = gr; g.fillRect(-64, -64, 128, 128);
    });
    SP.haloSel = halo('halo-sel', C.ink); SP.haloHover = halo('halo-hover', C.accent); SP.haloWait = halo('halo-wait', C.warn);
  }
  function spriteOf(n) {
    if (n.kind === 'decision') return SP.decision;
    if (n.kind === 'assumption') return n.rec.status === 'OPEN' ? SP.assumptionOpen : SP.assumption;
    const s = n.rec.status;
    return s === 'DONE' ? SP.done : s === 'BLOCKED' ? SP.blocked : s === 'WIP' ? SP.wip : n.rec.ready ? SP.ready : SP.todo;
  }
  const verif = n => n.kind === 'task' ? (n.rec.validation === 'HUMAN_VERIFIED' ? 'H' : n.rec.validation === 'DELEGATE_VERIFIED' ? 'D' : '') : '';

  /* ---------- projection: the camera sits on +z of the turned frame, looking at the target */
  let cyw = 1, syw = 0, ctl = 1, stl = 0, T0 = 0, T1 = 0, T2 = 0, Cx = 0, Cy = 0, Cz = 1000, F = 800, NEAR = 1;
  let _x = 0, _y = 0, _d = 1, _s = 1;
  function rot(x, y, z) { x -= T0; y -= T1; z -= T2; const x1 = cyw * x + syw * z, z1 = -syw * x + cyw * z; return [x1, ctl * y - stl * z1, stl * y + ctl * z1]; }
  function pj(x, y, z) {
    x -= T0; y -= T1; z -= T2;
    const x1 = cyw * x + syw * z, z1 = -syw * x + cyw * z, y2 = ctl * y - stl * z1, z2 = stl * y + ctl * z1;
    _d = Cz - z2; if (_d < NEAR) return false;
    _s = F / _d; _x = W / 2 + (x1 - Cx) * _s; _y = H / 2 - (y2 - Cy) * _s; return true;
  }
  function setupCamera() {
    cyw = Math.cos(cam.yaw); syw = Math.sin(cam.yaw); ctl = Math.cos(cam.tilt); stl = Math.sin(cam.tilt);
    [T0, T1, T2] = cam.T; F = Math.min(W, H) * 1.1; NEAR = Math.max(1, cam.dist * 0.03);
    Cx = 0; Cy = 0; Cz = cam.dist;
    if (cam.a > 0.0005 && cam.dolly >= 0) { // toward the node under the pointer, along its ray: it stays under the pointer
      const q = rot(P[cam.dolly * 3], P[cam.dolly * 3 + 1], P[cam.dolly * 3 + 2]);
      Cx = cam.a * q[0]; Cy = cam.a * q[1]; Cz = cam.dist + cam.a * (q[2] - cam.dist);
    }
  }
  function unrot(x, y, z) { const y1 = ctl * y + stl * z, z1 = -stl * y + ctl * z; return [cyw * x - syw * z1, y1, syw * x + cyw * z1]; }

  /* ---------- per frame: returns true while something still moves */
  function step(dt, t) {
    const want = play && hover < 0 && hoverG < 0 && !drag && t - lastInteract > IDLE ? 1 : 0;
    cam.v = RM ? want : ease(cam.v, want, want > cam.v ? 1.6 : 0.25, dt);
    if (!want && cam.v < 0.002) cam.v = 0;
    const w = 2 * Math.PI / TURN * S.speed * cam.v;
    cam.yaw = (cam.yaw + w * dt) % (2 * Math.PI * 8);
    if (S.mode === 'dual') cam.tilt = (cam.tilt + w * 0.5 * dt) % (2 * Math.PI * 8); // one full tilt turn for every two vertical turns
    if (cam.tiltGoal != null) {
      cam.tilt = ease(cam.tilt, cam.tiltGoal, RM ? 0 : 0.9, dt);
      if (Math.abs(cam.tilt - cam.tiltGoal) < 1e-3) { cam.tilt = wrap(cam.tiltGoal); cam.tiltGoal = null; }
    }
    const tc = RM ? 0 : 0.42;
    for (let k = 0; k < 3; k++) cam.T[k] = ease(cam.T[k], cam.gT[k], tc, dt);
    cam.dist = ease(cam.dist, cam.gDist, tc, dt);
    // after a short dwell on a node the view moves toward it; a different node first lets the previous one go
    if (hover >= 0 && S.hoverZoom && !RM && !drag && cam.gA === 0 && cam.a < 0.01 && t - hoverSince > DWELL) { cam.dolly = hover; cam.gA = 0.5; }
    cam.a = ease(cam.a, cam.gA, RM ? 0 : 0.5, dt);
    if (cam.gA === 0 && cam.a < 0.0008) { cam.a = 0; if (hover < 0) cam.dolly = -1; }
    const eps = cam.ref * 1e-4;
    const dwellPending = hover >= 0 && S.hoverZoom && !RM && cam.gA === 0;
    return cam.v > 0 || cam.tiltGoal != null || dwellPending || Math.abs(cam.dist - cam.gDist) > eps
      || cam.T.some((v, k) => Math.abs(v - cam.gT[k]) > eps) || Math.abs(cam.a - cam.gA) > 0.001;
  }

  /* ---------- drawing */
  function labelW(text, f) { const k = f + '|' + text; let w = widthCache.get(k); if (w == null) { ctx.font = f; w = ctx.measureText(text).width; widthCache.set(k, w); } return w; }
  const overlaps = (placed, x, y, w, h) => placed.some(p => x < p[2] && x + w > p[0] && y < p[3] && y + h > p[1]);

  function draw() {
    const g = ctx;
    g.setTransform(DPR, 0, 0, DPR, 0, 0);
    g.globalCompositeOperation = 'source-over'; g.globalAlpha = 1; g.setLineDash([]);
    if (!bgGrad) {
      bgGrad = g.createRadialGradient(W / 2, H * 0.42, 0, W / 2, H * 0.42, Math.max(W, H) * 0.8);
      bgGrad.addColorStop(0, css(dark ? mix(C.bg, C.accent, 0.1) : mix(C.bg, WHITE, 0.85)));
      bgGrad.addColorStop(1, css(dark ? mix(C.bg, BLACK, 0.45) : mix(C.bg, C.line, 0.5)));
    }
    g.fillStyle = bgGrad; g.fillRect(0, 0, W, H);
    setupCamera();
    const foc = shState.focus, sel = shState.sel, hoverOn = hover >= 0 && hoverSet;
    const dNear = cam.dist - cam.R, dFar = cam.dist + cam.R;
    const depthF = d => clamp((dFar - d) / (dFar - dNear), 0, 1);
    const inChain = id => Boolean(foc && foc.F.has(id));
    const exc = i => { const id = list[i].id; return i === hover || id === sel || isWait[i] || inChain(id); };

    // semantic zoom: a module small on screen folds into one bubble with a count
    for (const gr of groups) {
      gr.n = 0; for (const i of gr.mem) if (!list[i].gone) gr.n++;
      if (!gr.n || !pj(gr.c[0], gr.c[1], gr.c[2])) { gr.t = 0; gr.vis = false; continue; }
      gr.vis = true; gr.sx = _x; gr.sy = _y; gr.d = _d; gr.sr = gr.r * _s; gr.t = smooth(22, 10, gr.sr);
    }

    // floors: a faint lit disc and ring per phase
    const labels = [];
    for (const pl of planes) {
      let ok = true, lx = Infinity, ly = 0, dsum = 0;
      g.beginPath();
      for (let k = 0; k <= 48; k++) {
        const a = k / 48 * Math.PI * 2;
        if (!pj(pl.c[0] + Math.cos(a) * pl.r, pl.c[1], pl.c[2] + Math.sin(a) * pl.r)) { ok = false; break; }
        if (k === 0) g.moveTo(_x, _y); else g.lineTo(_x, _y);
        if (_x < lx) { lx = _x; ly = _y; } dsum += _d;
      }
      if (!ok) continue;
      const df = depthF(dsum / 49), dimmed = pl.mem.every(i => list[i].dim || list[i].gone);
      g.globalAlpha = (dark ? 0.07 : 0.05) * (dimmed ? 0.4 : 1); g.fillStyle = css(C.accent); g.fill();
      g.globalAlpha = (0.18 + 0.25 * df) * (dimmed ? 0.4 : 1); g.strokeStyle = css(C.accent); g.lineWidth = 1; g.stroke();
      labels.push({ x: lx - 6, y: ly, text: pl.label, pri: 1.2, kind: 'plane', a: (0.5 + 0.5 * df) * (dimmed ? 0.5 : 1), left: true });
    }

    // nodes: project, depth, recede, fold
    order.length = 0;
    for (let i = 0; i < N; i++) {
      const n = list[i]; SV[i] = 0;
      if (n.gone) continue;
      const gi = groupOf[i], gr = gi >= 0 ? groups[gi] : null;
      const ta = gr && gr.vis && !exc(i) ? gr.t : 0;
      let x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
      if (ta > 0) { x += (gr.c[0] - x) * ta; y += (gr.c[1] - y) * ta; z += (gr.c[2] - z) * ta; }
      if (!pj(x, y, z)) continue;
      const rel = cam.ref / _d, df = depthF(_d);
      let a = (0.3 + 0.7 * df) * (1 - ta);
      if (n.dim) a *= 0.13;
      if (hoverOn) a *= hoverSet.has(n.id) ? 1 : 0.4;
      if (isWait[i]) a = Math.max(a, n.dim ? 0.3 : 0.6);
      if (i === hover || n.id === sel) a = 1;
      if (a < 0.015) continue;
      SV[i] = ta > 0.5 ? 2 : 1; SX[i] = _x; SY[i] = _y; SD[i] = _d; SA[i] = a;
      SR[i] = BASE[n.kind] * clamp(Math.pow(rel, 0.6), 0.42, 3.4) * (n.dim ? 0.8 : 1);
      order.push(i);
    }
    order.sort((p, q) => SD[q] - SD[p]); // far first: nearer nodes are drawn over farther ones

    // lines, batched by kind, emphasis and depth
    const B = new Map(), ARW = new Map();
    const KIND = { dep: 0, gov: 1, asm: 2 }, KA = [0.5, 0.3, 0.42, 0.85];
    for (const e of L.edges) {
      const ho = hoverEdges && hoverEdges.has(e);
      if (e.gone && !ho) continue;
      const a = idx.get(e.s), b = idx.get(e.t);
      if (!SV[a] || !SV[b]) continue;
      if (SV[a] === 2 && SV[b] === 2 && groupOf[a] === groupOf[b]) continue; // inside one folded module
      const hl = e.hl || ho, lvl = hl ? 2 : (e.dim || hoverOn) ? 0 : 1;
      const df = depthF((SD[a] + SD[b]) / 2), k = e.waitpath && !hl ? 3 : KIND[e.k];
      let al = hl ? 0.95 : KA[k] * (lvl ? 1 : 0.14) * (0.25 + 0.75 * df);
      if (SV[a] === 2 || SV[b] === 2) al *= 0.45;
      const q = Math.round(clamp(al, 0, 1) * 8), key = (k * 3 + lvl) * 9 + q;
      if (!q) continue;
      (B.get(key) || B.set(key, []).get(key)).push(SX[a], SY[a], SX[b], SY[b]);
      if (e.k === 'dep' && lvl && SV[b] === 1) {
        const dx = SX[b] - SX[a], dy = SY[b] - SY[a], len = Math.hypot(dx, dy);
        if (len > SR[b] * 2 + 10) {
          const ux = dx / len, uy = dy / len, tx = SX[b] - ux * (SR[b] + 1.5), ty = SY[b] - uy * (SR[b] + 1.5), s = hl ? 5 : 3.6;
          const ak = lvl * 9 + q;
          (ARW.get(ak) || ARW.set(ak, []).get(ak)).push(tx, ty, tx - ux * s - uy * s * 0.55, ty - uy * s + ux * s * 0.55, tx - ux * s + uy * s * 0.55, ty - uy * s - ux * s * 0.55);
        }
      }
    }
    g.globalCompositeOperation = dark ? 'lighter' : 'source-over';
    // the line styles of the other views: solid with an arrow (depends on), dashed (governs), dotted (relied on)
    const kcol = [C['line-strong'], C.decision, C.assumption, C.warn], dash = [[], [4, 3], [1.5, 2.5], []];
    for (const [key, pts] of B) {
      const q = key % 9, lvl = Math.floor(key / 9) % 3, k = Math.floor(key / 27);
      g.globalAlpha = q / 8; g.strokeStyle = css(lvl === 2 ? C.accent : kcol[k]); g.lineWidth = lvl === 2 ? 1.8 : k === 3 ? 1.6 : 1; g.setLineDash(dash[k]);
      g.beginPath(); for (let j = 0; j < pts.length; j += 4) { g.moveTo(pts[j], pts[j + 1]); g.lineTo(pts[j + 2], pts[j + 3]); } g.stroke();
    }
    g.setLineDash([]);
    for (const [key, pts] of ARW) {
      g.globalAlpha = (key % 9) / 8; g.fillStyle = css(Math.floor(key / 9) === 2 ? C.accent : C['line-strong']);
      g.beginPath(); for (let j = 0; j < pts.length; j += 6) { g.moveTo(pts[j], pts[j + 1]); g.lineTo(pts[j + 2], pts[j + 3]); g.lineTo(pts[j + 4], pts[j + 5]); g.closePath(); } g.fill();
    }
    g.globalCompositeOperation = 'source-over';

    // folded modules: a lit bubble with the count inside; lesser labels keep clear of it
    const bubbles = [];
    for (const gr of groups) {
      if (!gr.vis || gr.t < 0.05) continue;
      const rb = Math.max(9, gr.sr * 0.7 + 6), df = depthF(gr.d), dimmed = gr.mem.every(i => list[i].dim || list[i].gone);
      const a = gr.t * (0.45 + 0.55 * df) * (dimmed ? 0.35 : 1);
      const grd = g.createRadialGradient(gr.sx - rb * 0.3, gr.sy - rb * 0.35, 0, gr.sx, gr.sy, rb);
      const col = mix(C['line-strong'], C.accent, 0.35);
      grd.addColorStop(0, css(mix(col, WHITE, 0.45), 0.55)); grd.addColorStop(1, css(col, 0.12));
      g.globalAlpha = a; g.fillStyle = grd; g.beginPath(); g.arc(gr.sx, gr.sy, rb, 0, 7); g.fill();
      g.strokeStyle = css(col, 0.7); g.lineWidth = 1; g.stroke();
      g.fillStyle = css(C.ink); g.font = '600 ' + (rb > 12 ? 11 : 9) + 'px ' + font; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(String(gr.n), gr.sx, gr.sy + 0.5); g.textAlign = 'start'; g.textBaseline = 'alphabetic';
      gr.rb = rb; bubbles.push([gr.sx - rb, gr.sy - rb, gr.sx + rb, gr.sy + rb]);
      if (gr.t > 0.5) labels.push({ x: gr.sx + rb + 3, y: gr.sy, text: gr.name + ' · ' + gr.n, pri: 2.8, kind: 'group', a: Math.max(0.6, a) });
      else if (gr.sr > 18) labels.push({ x: gr.sx, y: gr.sy - gr.sr * 0.85, text: gr.name, pri: 1.6, kind: 'group', a: 0.7 * (1 - gr.t) * (0.5 + 0.5 * df), center: true });
    }
    for (const gr of groups) if (gr.vis && gr.t < 0.05 && gr.sr > 18 && gr.sr < 220) {
      if (gr.mem.every(i => list[i].dim || list[i].gone)) continue;
      labels.push({ x: gr.sx, y: gr.sy - gr.sr * 0.85, text: gr.name, pri: 1.6, kind: 'group', a: 0.7 * (0.5 + 0.5 * depthF(gr.d)), center: true });
    }

    // nodes, back to front, with halo, waiting diamond and verification letter
    for (const i of order) {
      if (SV[i] !== 1) continue;
      const n = list[i], r = SR[i], x = SX[i], y = SY[i];
      g.globalAlpha = SA[i];
      if (n.id === sel || i === hover) { const h = n.id === sel ? SP.haloSel : SP.haloHover, s = h.k * r * 0.9; g.drawImage(h, x - s / 2, y - s / 2, s, s); }
      else if (isWait[i]) { const h = SP.haloWait, s = h.k * r * 0.6; g.globalAlpha = SA[i] * 0.7; g.drawImage(h, x - s / 2, y - s / 2, s, s); g.globalAlpha = SA[i]; }
      const sp = spriteOf(n), s = sp.k * r;
      g.drawImage(sp, x - s / 2, y - s / 2, s, s);
      if (isWait[i]) { const ws = SP.wait.k * r; g.drawImage(SP.wait, x - ws / 2, y - ws / 2, ws, ws); }
      const rel = cam.ref / SD[i], vm = verif(n);
      if (vm && rel > 1.7) { g.fillStyle = css(C.ink); g.font = '700 9px ' + font; g.fillText(vm, x + r + 1, y - r); }
      const pri = i === hover ? 6 : n.id === sel ? 5 : isWait[i] ? 4 : inChain(n.id) ? 3.5
        : (rel > 1.8 && !n.dim && SA[i] > 0.35) ? 1 + Math.min(0.5, rel / 20) : 0;
      // the hovered node's tooltip already carries its id and title: no second label under it
      if (pri && !(i === hover && !lTip.hidden)) {
        labels.push({ x: x + r + 4, y, r, node: n.id, text: pri >= 5 ? title(n) : (isWait[i] ? '! ' + n.id : short(n)), pri,
                      kind: pri >= 5 ? 'main' : isWait[i] ? 'wait' : 'node', a: n.dim && pri < 5 ? 0.45 : Math.max(SA[i], pri >= 3 ? 0.75 : 0.5), must: pri >= 3 });
      }
    }
    if (sel) { const i = idx.get(sel); if (i != null && SV[i] === 1) { g.globalAlpha = 0.9; g.strokeStyle = css(C.ink); g.lineWidth = 1.5; g.beginPath(); g.arc(SX[i], SY[i], SR[i] + 4, 0, 7); g.stroke(); } }

    // labels: priority first, and nothing printed over another label. The
    // selection, its chain and waiting items are always labelled: when their
    // place is taken they move beside it, with a thin line to their node.
    labels.sort((p, q) => q.pri - p.pri);
    const placed = [], drawn = []; let generic = 0;
    for (const L0 of labels) {
      if (L0.pri < 2 && L0.kind === 'node' && ++generic > 45) continue;
      const f = L0.kind === 'main' ? '600 12px ' + font : L0.kind === 'wait' ? '700 11px ' + font : L0.kind === 'group' ? '600 10px ' + font : '11px ' + font;
      const w = labelW(L0.text, f) + 8, h = L0.kind === 'main' ? 17 : 15;
      let x = L0.center ? L0.x - w / 2 : L0.left ? L0.x - w : L0.x, y = L0.y - h / 2;
      if (x > W || x + w < 0 || y > H || y + h < 0) continue;
      if (L0.must) x = clamp(x, 2, W - w - 2);
      let moved = false;
      const busy = (px, py) => overlaps(placed, px - 2, py - 1, w + 4, h + 2) || (L0.pri < 4.5 && overlaps(bubbles, px, py, w, h));
      if (busy(x, y)) {
        if (!L0.must) continue;
        const spots = [[L0.x - 2 * L0.r - 8 - w, y]];
        for (let k = 1; k <= 24; k++) { spots.push([x, y - k * (h + 2)]); spots.push([x, y + k * (h + 2)]); }
        const spot = spots.find(([px, py]) => px >= 0 && px + w <= W && py >= 0 && py + h <= H && !busy(px, py));
        if (!spot) continue;
        [x, y] = spot; moved = true;
      }
      placed.push([x - 2, y - 1, x + w + 2, y + h + 1]);
      drawn.push({ text: L0.text, node: L0.node || null, kind: L0.kind, x, y, w, h, moved });
      g.globalAlpha = L0.a;
      if (moved) {
        g.strokeStyle = css(C.muted, 0.7); g.lineWidth = 1; g.beginPath();
        g.moveTo(L0.x - 4, L0.y); g.lineTo(x < L0.x ? x + w : x, y + h / 2); g.stroke();
      }
      if (L0.kind !== 'plane' && L0.kind !== 'group') {
        g.fillStyle = css(C.panel, 0.86); g.beginPath(); if (g.roundRect) g.roundRect(x, y, w, h, 4); else g.rect(x, y, w, h); g.fill();
        if (L0.kind === 'wait' || L0.kind === 'main') { g.strokeStyle = css(L0.kind === 'wait' ? C.warn : C.ink, 0.9); g.lineWidth = 1; g.stroke(); }
      }
      g.font = f; g.fillStyle = css(L0.kind === 'wait' ? (dark ? C.warn : mix(C.warn, BLACK, 0.25)) : L0.kind === 'plane' || L0.kind === 'group' ? C.muted : C.ink);
      g.textBaseline = 'middle'; g.fillText(L0.text, x + 4, y + h / 2 + 0.5); g.textBaseline = 'alphabetic';
    }
    stats.labels = drawn;
    g.globalAlpha = 1;
    if (hover >= 0 && SV[hover]) placeTip(SX[hover], SY[hover]);
  }

  /* ---------- the loop: only while the view is open, Work is shown, the tab visible, and something moves */
  function frame(t) {
    if (!active || document.hidden || !lVisible()) { running = false; lastDraw = 0; return; }
    // the cap holds on fast displays too
    if (lastDraw && t - lastDraw < 1000 / CAP - 2) { requestAnimationFrame(frame); return; }
    const dt = lastDraw ? Math.min(0.1, (t - lastDraw) / 1000) : 1 / 60; lastDraw = t;
    const moving = step(dt, t);
    if (pointer && !drag && cam.v > 0) pick(pointer.x, pointer.y, t); // nodes drift under a still pointer
    const t0 = now(); draw(); const ms = now() - t0;
    stats.frames += 1;
    if (stats.firstFrameMs == null && stats.openedAt != null) {
      stats.firstFrameMs = Math.round(now() - stats.openedAt);
      lStage.dataset.v3FirstFrameMs = String(stats.firstFrameMs);
    }
    if (cam.v > 0.5 && stats.turning.length < 600) stats.turning.push(ms);
    fpsN++; fpsMs += ms;
    if (t - fpsT > 1000) { status(Math.round(fpsN * 1000 / (t - fpsT)) + ' fps · ' + (fpsMs / fpsN).toFixed(1) + ' ms a frame'); fpsN = 0; fpsMs = 0; fpsT = t; }
    if (moving) requestAnimationFrame(frame);
    else { running = false; lastDraw = 0; status('Still: no frames drawn.'); scheduleResume(t); }
  }
  function dirty() {
    if (!active || !laid) return;
    if (!running) { running = true; lastDraw = 0; fpsN = 0; fpsMs = 0; fpsT = now(); requestAnimationFrame(frame); }
  }
  function scheduleResume(t) { // wake once when the calm after an interaction ends, instead of drawing while waiting
    clearTimeout(resumeTimer);
    if (play && hover < 0 && hoverG < 0 && !drag && t - lastInteract <= IDLE) resumeTimer = setTimeout(dirty, IDLE - (t - lastInteract) + 30);
  }
  const touch = () => { lastInteract = now(); };
  function status(text) { const e = document.getElementById('v3-status'); if (e) e.textContent = text; }

  /* ---------- picking and hover */
  function pick(x, y, t = now()) {
    let best = -1, bd = Infinity;
    for (const i of order) {
      if (SV[i] !== 1 || SA[i] < 0.1) continue;
      const dx = SX[i] - x, dy = SY[i] - y, d2 = dx * dx + dy * dy, thr = SR[i] + (i === hover ? 12 : 5);
      if (d2 <= thr * thr && d2 < bd) { bd = d2; best = i; }
    }
    let bg = -1;
    if (best < 0) groups.forEach((gr, gi) => { if (gr.vis && gr.t > 0.5 && Math.hypot(gr.sx - x, gr.sy - y) <= (gr.rb || 10) + 3) bg = gi; });
    setHover(best, bg, t);
  }
  function setHover(i, gi, t) {
    if (i === hover && gi === hoverG) return;
    const had = hover >= 0 || hoverG >= 0;
    hover = i; hoverG = gi; hoverSince = t; cam.gA = 0;
    hoverSet = null; hoverEdges = null;
    if (i >= 0) {
      const id = list[i].id; hoverSet = new Set([id]); hoverEdges = new Set();
      for (const e of lOut(id).concat(lIn(id))) {
        if (L.nodes.get(e.s).gone || L.nodes.get(e.t).gone) continue;
        hoverEdges.add(e); hoverSet.add(e.s); hoverSet.add(e.t);
      }
      lTip.innerHTML = shTipHtml(list[i]); lTip.hidden = false;
    } else if (gi >= 0) {
      const gr = groups[gi];
      lTip.innerHTML = shCode(gr.name) + ' ' + esc(protectedText(fModuleName[gr.id] || '')) + '<br><span class="note">' + gr.n
        + ' nodes folded at this distance · click to move in</span>';
      lTip.hidden = false; placeTip(gr.sx, gr.sy);
    } else lTip.hidden = true;
    cv.style.cursor = i >= 0 || gi >= 0 ? 'pointer' : drag ? 'grabbing' : 'grab';
    if (had && i < 0 && gi < 0) touch();
    dirty();
  }
  function placeTip(x, y) { lTip.style.left = Math.max(4, Math.min(W - 340, x + 16)) + 'px'; lTip.style.top = Math.max(4, Math.min(H - 70, y + 16)) + 'px'; }

  /* ---------- framing */
  const shownIds = () => list.map((n, i) => n.gone || (n.dim && !shState.sel) ? -1 : i).filter(i => i >= 0);
  function frameSphere(ids, center, minK = 0.1) {
    if (!ids.length) return false;
    const s = sphere(P, ids);
    const c = center != null ? [P[center * 3], P[center * 3 + 1], P[center * 3 + 2]] : s.c;
    let r = s.r;
    if (center != null) r = Math.max(...ids.map(i => Math.hypot(P[i * 3] - c[0], P[i * 3 + 1] - c[1], P[i * 3 + 2] - c[2]))) * 0.9;
    cam.gT = c; cam.gDist = clamp(r * 2.7, cam.ref * minK, cam.ref * 1.6); cam.gA = 0;
    if (RM) { cam.T = cam.gT.slice(); cam.dist = cam.gDist; }
    touch(); dirty(); return true;
  }
  function homeTilt(goal) { if (S.mode === 'single') { cam.tiltGoal = cam.tilt + wrap(goal - cam.tilt); if (RM) { cam.tilt = wrap(goal); cam.tiltGoal = null; } } }
  function fit() {
    if (!laid) return false;
    homeTilt(HOME_TILT);
    const ids = shownIds();
    return frameSphere(ids.length ? ids : list.map((_, i) => i));
  }
  function frameFocus() {
    const f = shState.focus, id = shState.sel;
    if (!laid || !f || !id || f.F.size < 2) return false;
    const ids = [...f.F].map(x => idx.get(x)).filter(i => i != null && !list[i].gone);
    // look down on the floor more steeply so a chain lying on it spreads out on screen
    homeTilt(FOCUS_TILT);
    return frameSphere(ids, idx.get(id), 0.16);
  }
  function framePhase(ph) {
    if (!laid) return false;
    const ids = list.map((n, i) => n.kind === 'task' && n.rec.phase === ph && !n.gone ? i : -1).filter(i => i >= 0);
    return frameSphere(ids) || fit();
  }
  function centerOn(id) {
    if (!laid) return; const i = idx.get(id); if (i == null) return;
    cam.gT = [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]]; cam.gDist = Math.min(cam.gDist, cam.ref * 0.45); cam.gA = 0;
    if (RM) { cam.T = cam.gT.slice(); cam.dist = cam.gDist; }
    touch(); dirty();
  }
  function zoomBy(f) {
    if (!laid) return; cam.gDist = clamp(cam.gDist / f, cam.ref * 0.05, cam.ref * 3.5);
    if (RM) cam.dist = cam.gDist;
    touch(); dirty();
  }
  function panKey(key) { // Shift with an arrow moves the view across the screen
    if (!laid) return;
    setupCamera();
    const st = cam.dist * 0.06, v = unrot(key === 'ArrowLeft' ? -st : key === 'ArrowRight' ? st : 0, key === 'ArrowUp' ? st : key === 'ArrowDown' ? -st : 0, 0);
    for (let j = 0; j < 3; j++) { cam.gT[j] += v[j]; if (RM) cam.T[j] = cam.gT[j]; }
    touch(); dirty();
  }
  // a click keeps the move toward the node: it becomes the framing, centred on the node
  function keepZoom(i) {
    setupCamera();
    const q = rot(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]), cz = cam.dist + cam.a * (q[2] - cam.dist);
    cam.gT = [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]];
    cam.gDist = clamp(Math.max(cz - q[2], cam.ref * 0.12), cam.ref * 0.05, cam.ref * 3.5);
    cam.gA = 0; hoverSince = Infinity;
    if (RM) { cam.T = cam.gT.slice(); cam.dist = cam.gDist; cam.a = 0; }
    dirty();
  }

  /* ---------- input, attached when the view is first opened */
  function listen() {
    cv.addEventListener('pointerdown', ev => {
      drag = { x: ev.clientX, y: ev.clientY, moved: false, pan: ev.shiftKey || ev.button === 1 || ev.button === 2 };
      touch();
    });
    cv.addEventListener('pointermove', ev => {
      const r = cv.getBoundingClientRect(); pointer = { x: ev.clientX - r.left, y: ev.clientY - r.top };
      if (drag) {
        const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) { drag.moved = true; cv.style.cursor = 'grabbing'; setHover(-1, -1, now()); }
        if (!drag.moved) return;
        drag.x = ev.clientX; drag.y = ev.clientY; touch();
        if (drag.pan) {
          setupCamera(); const k = cam.dist / F, v = unrot(-dx * k, dy * k, 0);
          for (let j = 0; j < 3; j++) { cam.T[j] += v[j]; cam.gT[j] += v[j]; }
        } else { // drag orbits the scene
          cam.yaw += dx * 0.006; cam.tiltGoal = null;
          cam.tilt = S.mode === 'dual' ? cam.tilt + dy * 0.006 : clamp(wrap(cam.tilt) + dy * 0.006, -1.45, 1.45);
        }
        dirty(); return;
      }
      if (cam.v === 0) pick(pointer.x, pointer.y);
    });
    const up = ev => {
      if (!drag) return;
      const wasClick = !drag.moved && ev.type === 'pointerup'; drag = null; cv.style.cursor = 'grab'; touch();
      if (wasClick) {
        const r = cv.getBoundingClientRect(); pick(ev.clientX - r.left, ev.clientY - r.top);
        if (hover >= 0) { const i = hover; shSelect(list[i].id, { frame: false }); keepZoom(i); }
        else if (hoverG >= 0) { const gr = groups[hoverG]; frameSphere(gr.mem.filter(i => !list[i].gone)); setHover(-1, -1, now()); }
        else { shSelect(null); fit(); }
      }
      dirty();
    };
    // A drag that ends outside the canvas still ends: the page hears the release.
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    addEventListener('pointerup', up); addEventListener('pointercancel', up);
    cv.addEventListener('pointerleave', () => { pointer = null; if (!drag) setHover(-1, -1, now()); });
    cv.addEventListener('wheel', ev => {
      ev.preventDefault(); cam.gDist = clamp(cam.gDist * Math.pow(1.0015, ev.deltaY), cam.ref * 0.05, cam.ref * 3.5);
      if (RM) cam.dist = cam.gDist;
      touch(); dirty();
    }, { passive: false });
    cv.addEventListener('contextmenu', ev => ev.preventDefault());
    document.addEventListener('visibilitychange', () => { if (!document.hidden) dirty(); });
    const scheme = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
    if (scheme && scheme.addEventListener) scheme.addEventListener('change', theme);
    if (typeof MutationObserver === 'function') new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const motion = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : null;
    if (motion && motion.addEventListener) motion.addEventListener('change', () => { RM = motion.matches; if (RM) { play = false; cam.v = 0; } syncPanel(); dirty(); });
    document.getElementById('v3-play').addEventListener('click', togglePlay);
    document.getElementById('v3-mode').addEventListener('click', toggleMode);
    document.getElementById('v3-slower').addEventListener('click', () => speed(0.8));
    document.getElementById('v3-faster').addEventListener('click', () => speed(1.25));
    document.getElementById('v3-fit').addEventListener('click', () => { shSelect(null); fit(); });
    document.getElementById('v3-hz').addEventListener('click', () => {
      S.hoverZoom = !S.hoverZoom; savePrefs(); syncPanel(); cam.gA = 0; dirty();
      say(S.hoverZoom ? 'The view moves toward a node the pointer rests on.' : 'The view stays put when the pointer rests on a node.');
    });
  }
  function syncPanel() {
    const b = document.getElementById('v3-play');
    b.textContent = play ? 'Pause' : 'Turn';
    document.getElementById('v3-mode').textContent = S.mode === 'dual' ? 'Two axes' : 'One axis';
    document.getElementById('v3-speed-o').textContent = '1 turn in ' + Math.round(TURN / S.speed) + ' s';
    document.getElementById('v3-hz').setAttribute('aria-pressed', String(Boolean(S.hoverZoom)));
    document.getElementById('v3-rm').hidden = !RM;
  }
  function togglePlay() {
    play = !play; S.playing = play; savePrefs(); syncPanel();
    if (play) lastInteract = -1e9;
    say(play ? 'Turning.' : 'Turn paused.'); dirty();
  }
  function toggleMode() {
    S.mode = S.mode === 'single' ? 'dual' : 'single';
    homeTilt(HOME_TILT); savePrefs(); syncPanel();
    say(S.mode === 'single' ? 'Turn: one axis, about the vertical.' : 'Turn: two axes, one tilt turn for every two vertical turns.'); dirty();
  }
  function speed(f) {
    S.speed = clamp(Number((S.speed * f).toFixed(2)), 0.25, 3); savePrefs(); syncPanel();
    say('Speed: one turn in ' + Math.round(TURN / S.speed) + ' seconds.'); dirty();
  }
  function key(k) { // page keys, only while the view is open
    if (!active) return false;
    if (k === 'p') togglePlay();
    else if (k === 'm') toggleMode();
    else if (k === '[' || k === ']') speed(k === ']' ? 1.25 : 0.8);
    else return false;
    return true;
  }

  /* ---------- lifecycle */
  function init() {
    ready = true;
    S = loadPrefs();
    cv = document.getElementById('c3d'); ctx = cv.getContext('2d'); panel = document.getElementById('v3-panel');
    list = [...L.nodes.values()]; N = list.length; idx = new Map(list.map((n, i) => [n.id, i]));
    SX = new Float32Array(N); SY = new Float32Array(N); SD = new Float32Array(N); SR = new Float32Array(N); SA = new Float32Array(N);
    SV = new Uint8Array(N); groupOf = new Int32Array(N); floorOf = new Array(N).fill(null);
    isWait = Uint8Array.from(list, n => shWait[n.id] ? 1 : 0);
    listen();
  }
  // The layout runs once, in slices of a few milliseconds; the scene is first
  // drawn when it rests.
  function startLayout() {
    const t0 = now();
    laying = makeLayout();
    status('Laying out the scene…');
    const slice = () => {
      if (!laying) return;
      const s = now();
      while (!laying.done && now() - s < 24) laying.tick();
      if (!laying.done) { requestAnimationFrame(slice); return; }
      P = laying.result(); laying = null; laid = true;
      stats.layoutMs = Math.round(now() - t0);
      lStage.dataset.v3LayoutMs = String(stats.layoutMs);
      status('Laid out in ' + stats.layoutMs + ' ms · ' + N + ' nodes');
      buildGroups();
      if (active) begin();
    };
    slice();
  }
  function begin() { // frame the scene and start drawing
    resize(true);
    if (!(shState.sel && frameFocus())) fit();
    cam.T = cam.gT.slice(); cam.a = 0; cam.gA = 0;
    if (!RM) { cam.dist = cam.gDist * 2.1; cam.yaw -= 0.9; } else { cam.dist = cam.gDist; if (cam.tiltGoal != null) { cam.tilt = wrap(cam.tiltGoal); cam.tiltGoal = null; } }
    cam.v = 0; lastInteract = RM ? -1e9 : now() - IDLE + 900; // the turn eases in once the view has moved in
    syncPanel(); dirty();
  }
  function resize(quiet) {
    if (!ready || !active) return;
    const r = lStage.getBoundingClientRect(); if (!r.width || !r.height) return;
    DPR = Math.min(window.devicePixelRatio || 1, 2); W = r.width; H = r.height;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR); cv.style.width = W + 'px'; cv.style.height = H + 'px';
    bgGrad = null; if (!quiet) dirty();
  }
  function activate() {
    if (active) { dirty(); return; }
    const first = !ready;
    if (first) { stats.openedAt = now(); init(); }
    RM = shReduced();
    play = S.playing && !RM; // reduced motion: it never turns by itself
    active = true; cv.hidden = false; panel.hidden = false; lStage.classList.add('is3d'); lTip.hidden = true;
    readTheme(); syncPanel();
    if (first) startLayout();
    else if (laid) begin();
    say('3D view: the project in space, to explore. ' + (play ? 'It turns; p pauses it.' : 'It is still; p starts the turn.')
      + ' The text view in the bottom panel holds the same content.');
  }
  function deactivate() {
    if (!ready || !active) return;
    active = false; running = false; clearTimeout(resumeTimer);
    cv.hidden = true; panel.hidden = true; lStage.classList.remove('is3d');
    hover = -1; hoverG = -1; hoverSet = hoverEdges = null; pointer = null; drag = null; lTip.hidden = true;
  }
  function theme() { if (ready && active) { readTheme(); dirty(); } }
  // For tests and measurement: a read-only look at the state.
  function inspect() {
    return { ready, active, laid, running, RM, play, S: Object.assign({}, S || {}), cam: JSON.parse(JSON.stringify(cam)),
             stats, N, order: order.slice(), SX, SY, SD, SA, SR, SV, idx, list, groups, planes, hover, P,
             sprite: id => { const i = idx.get(id); return i == null ? null : spriteOf(list[i]).shape; }, pick };
  }
  return { activate, deactivate, dirty, fit, frameFocus, framePhase, centerOn, zoomBy, panKey, resize, key, theme,
           isActive: () => active, inspect };
})();
"""
