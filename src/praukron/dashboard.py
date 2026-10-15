"""Static dashboard generation.

One self-contained HTML file. The compiled project and the Mermaid sources are
embedded, so the page opens straight from the filesystem with no server and no
network. Mermaid renders the diagrams when it is reachable; when it is not, the
page shows the diagram source instead of an empty box.

The dashboard is read-only by construction: it carries no control that writes.
Its tabs, theme, and cross-filters are presentation state kept in the URL hash
and localStorage; none of it reaches authority (ADR-044).
"""

from __future__ import annotations

import html
import json
import re

from .analytics import Report
from .model import DOMAINS, EVENT_TYPES, STATUSES, VALIDATIONS, Project, event_order
from . import mermaid, review

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"


def esc(value: object) -> str:
    """Escape authored text before it becomes markup.

    Titles, outcomes and evidence are prose written by people. A stray angle
    bracket or ampersand must render as itself, not as markup.
    """
    return html.escape("" if value is None else str(value), quote=True)


def embed(data: object) -> str:
    """Serialize for a <script> island.

    A `</script>` sequence anywhere in authored text would otherwise close the
    island early and take the whole page down with it.
    """
    return json.dumps(data).replace("</", "<\\/").replace("\u2028", "\\u2028")

_STYLE = """
/* Colours are tokens. The reader's explicit choice sets data-theme on <html>;
   without one the head script copies the operating system preference, and
   without script the media query below does (ADR-044). Every text token meets
   WCAG AA (4.5:1) on --bg, --panel and --panel-raised in both themes, and every
   chart fill and control border meets 3:1. */
:root, :root[data-theme="light"] {
  color-scheme: light;
  --bg: #f3f4f6; --panel: #ffffff; --panel-raised: #f8f9fa; --sunken: #eceef1;
  --ink: #14171c; --muted: #565d68; --line: #dfe2e7; --line-strong: #7a818c;
  --accent: #3b3fb6; --on-accent: #ffffff; --good: #1d7036; --warn: #8a5100; --bad: #b3261e;
  --wip: #0a63a8; --todo: #646b76; --ops: #4a5f80; --on-warn: #ffffff;
  --shadow: 0 1px 2px rgba(16, 20, 28, 0.06), 0 2px 6px rgba(16, 20, 28, 0.04);
  --radius: 12px; --radius-s: 8px;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0e1013; --panel: #161a1f; --panel-raised: #1c2127; --sunken: #232931;
  --ink: #e7e9ed; --muted: #a0a8b4; --line: #2a3038; --line-strong: #717986;
  --accent: #a9b1ff; --on-accent: #0e1013; --good: #5fc684; --warn: #e3a64a; --bad: #f08a80;
  --wip: #5db2ec; --todo: #8f98a5; --ops: #9db4d3; --on-warn: #0e1013;
  --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    color-scheme: dark;
    --bg: #0e1013; --panel: #161a1f; --panel-raised: #1c2127; --sunken: #232931;
    --ink: #e7e9ed; --muted: #a0a8b4; --line: #2a3038; --line-strong: #717986;
    --accent: #a9b1ff; --on-accent: #0e1013; --good: #5fc684; --warn: #e3a64a; --bad: #f08a80;
    --wip: #5db2ec; --todo: #8f98a5; --ops: #9db4d3; --on-warn: #0e1013;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  font-variant-numeric: tabular-nums;
}
::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); color: var(--ink); }
a { color: var(--accent); text-underline-offset: 3px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0);
      white-space: nowrap; }
main { max-width: 1600px; margin: 0 auto; padding: 20px 20px 96px; }
/* The side panel: everything awaiting the owner, beside whichever tab is open
   (ADR-065). Narrow windows stack it above the tabs. */
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 28px; align-items: start; }
.content { min-width: 0; }
aside.needs { position: sticky; top: 12px; max-height: calc(100vh - 24px); overflow-y: auto;
              background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
              box-shadow: var(--shadow); padding: 16px; scrollbar-width: thin; }
aside.needs > .needs-head { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; }
aside.needs > .needs-head h2 { margin: 0; font-size: 17px; }
aside.needs .needcount { font-size: 12px; line-height: 20px; min-width: 22px; text-align: center;
              padding: 0 7px; border-radius: 999px; background: var(--warn); color: var(--on-warn);
              font-weight: 700; }
aside.needs .needcount.zero { background: var(--sunken); color: var(--ink); font-weight: 600; }
aside.needs .live { display: none; margin-left: auto; font-size: 12px; font-weight: 600; color: var(--accent); }
aside.needs h3 { margin: 18px 0 8px; }
/* Inside the panel, items are rows split by rules, not cards in a card. */
aside.needs .acards { padding: 0; }
aside.needs details.acard, aside.needs ul.plain > li { background: none; border: 0; border-radius: 0;
              border-top: 1px solid var(--line); box-shadow: none; margin: 0; padding: 10px 0; }
aside.needs details.acard { padding: 0; }
aside.needs details.acard > summary { padding: 10px 0; }
aside.needs details.acard .abody { padding: 0 0 12px; }
aside.needs ul.plain > li:last-child { border-bottom: 0; }
aside.needs .controls { flex-wrap: wrap; }
aside.needs .review-by { flex: 1 1 160px; min-width: 0; }
aside.needs details.group { background: none; border: 0; box-shadow: none; margin: 0; }
aside.needs details.group > summary { padding: 4px 0; }
aside.needs details.group > .note { padding: 0; margin: 0 0 8px; }
/* Served, the panel is where the owner answers, so it is marked live: an
   accent frame, a live label, and a submit bar that stays in reach. */
aside.needs.is-live { border: 2px solid var(--accent); padding: 15px;
              background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
aside.needs.is-live .live { display: inline; }
aside.needs.is-live .review-bar { position: sticky; top: -15px; z-index: 2; margin: 10px -15px 0;
              padding: 10px 15px; background: var(--panel);
              border-block: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
aside.needs .remaining { flex: 1 0 100%; font-size: 13px; font-weight: 600; }
@media (max-width: 1099px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  /* The panel no longer scrolls itself here, so the page is what the
     submit bar pins against. */
  aside.needs { position: static; max-height: none; overflow: visible; order: -1; }
  aside.needs.is-live .review-bar { top: 0; }
  aside.needs[data-waiting="0"] { order: 1; }
}
header.top { display: flex; gap: 12px 16px; align-items: center; justify-content: space-between;
             flex-wrap: wrap; padding: 4px 0 14px; }
h1 { font-size: 22px; line-height: 1.25; margin: 0 0 4px; letter-spacing: -0.015em; font-weight: 700; }
h2 { font-size: 16px; line-height: 1.3; font-weight: 650; color: var(--ink); margin: 36px 0 12px;
     letter-spacing: -0.005em; }
h3 { font-size: 14px; font-weight: 650; margin: 22px 0 8px; }
.panel > h2:first-child, .panel > .attention + h2 { margin-top: 20px; }
.sub { color: var(--muted); margin: 0; font-size: 13px; }
button { font: inherit; color: inherit; }
.linkbtn { border: 0; background: none; padding: 2px 4px; color: var(--accent); cursor: pointer;
           text-decoration: underline; text-underline-offset: 3px; font-size: 13px; border-radius: 4px; }

/* Theme control: follow the system until the reader picks, then remember. */
.theme { display: inline-flex; border: 1px solid var(--line-strong); border-radius: 999px;
         padding: 2px; background: var(--panel); }
.theme button { border: 0; background: none; cursor: pointer; font-size: 13px;
                padding: 4px 12px; border-radius: 999px; color: var(--muted); }
.theme button:hover { color: var(--ink); }
.theme button[aria-pressed="true"] { background: var(--ink); color: var(--bg); }

/* Top-level tabs and the filter bar stay in reach while the page scrolls.
   Without script every panel shows, one after another. */
.stickyhead { position: sticky; top: 0; z-index: 5; background: var(--bg);
              margin: 0 -20px; padding: 0 20px; }
.toptabs { display: flex; gap: 2px; overflow-x: auto; white-space: nowrap;
           border-bottom: 1px solid var(--line); scrollbar-width: thin; }
html:not(.js) .toptabs { display: none; }
.toptabs button { border: 0; background: none; cursor: pointer; padding: 11px 12px 9px;
           color: var(--muted); font-size: 14px; border-bottom: 2px solid transparent;
           flex: 0 0 auto; font-weight: 500; }
.toptabs button:hover { color: var(--ink); }
.toptabs button[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent);
           font-weight: 650; }
.toptabs button:focus-visible, .tasklink:focus-visible, tr[data-task]:focus-visible,
summary:focus-visible, .chips button:focus-visible, .theme button:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
.toptabs button:focus-visible { outline-offset: -2px; }
html.js .panel { display: none; }
html.js[data-tab="overview"] #panel-overview,
html.js[data-tab="assumptions"] #panel-assumptions,
html.js[data-tab="execution"] #panel-execution,
html.js[data-tab="graph"] #panel-graph,
html.js[data-tab="operations"] #panel-operations,
html.js[data-tab="governance"] #panel-governance,
html.js[data-tab="decisions"] #panel-decisions,
html.js[data-tab="tasks"] #panel-tasks { display: block; }
.panel { padding-top: 4px; }
.panel:focus { outline: none; }

/* Active cross-filters: every one visible, every one removable. */
.xfbar { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 8px 0;
         border-bottom: 1px solid var(--line); font-size: 13px; }
.xfbar[hidden] { display: none; }
.xfbar .xf-label { color: var(--muted); }
.xf-chips { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; }
.chip-x { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer;
          padding: 2px 6px 2px 10px; border-radius: 999px; color: var(--ink);
          border: 1px solid var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--panel)); }
.chip-x:hover { background: color-mix(in srgb, var(--accent) 18%, var(--panel)); }
.chip-x svg { width: 14px; height: 14px; flex: none; }
.xf-count { color: var(--muted); margin-left: auto; }
.xf-out { display: none !important; }
.tablewrap.xf-none::after { content: "No task here matches the active filters."; display: block;
         padding: 10px; color: var(--muted); font-size: 13px; }
.xf-needs { font-size: 13px; color: var(--ink); background: var(--sunken); border-radius: var(--radius-s);
         padding: 6px 10px; margin: 8px 0; }

/* Attention first: what is stuck, failing, or waiting on a person, before any
   progress figure. Each count is a compiled selection and links to its records. */
.attention { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px;
             background: var(--line); border: 1px solid var(--line); border-radius: var(--radius);
             overflow: hidden; box-shadow: var(--shadow); margin-top: 16px; }
.sig { position: relative; display: flex; flex-direction: column; gap: 1px; min-width: 0;
       padding: 12px 14px 12px; background: var(--panel); color: var(--ink); text-decoration: none; }
.sig:hover { background: var(--panel-raised); }
.sig:focus-visible { outline-offset: -3px; }
.sig::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: transparent; }
.sig.bad::before { background: var(--bad); }
.sig.warn::before { background: var(--warn); }
.sig-n { font-size: 22px; font-weight: 700; line-height: 1.15; }
.sig.bad .sig-n { color: var(--bad); }
.sig.warn .sig-n { color: var(--warn); }
.sig.clear .sig-n { color: var(--muted); font-weight: 600; }
.sig-l { font-size: 13px; font-weight: 600; }
.sig-note { font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }

/* Cards */
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line); box-shadow: var(--shadow);
        border-radius: var(--radius); padding: 14px 16px; min-width: 0; }
.card .k, .box .k { color: var(--muted); font-size: 12px; font-weight: 600; }
.card .v { font-size: 22px; font-weight: 650; margin-top: 2px; }
.card .more { color: var(--muted); font-size: 13px; margin-top: 6px; }
.card.alert .v { color: var(--bad); }
.bar { height: 6px; background: var(--sunken); border-radius: 3px; margin-top: 10px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--good); border-radius: 3px; }

/* The focus strip answers "where are we" right after what needs attention:
   one band, four cells, so no cell competes as a separate card. */
.focus { display: grid; gap: 1px; margin-top: 12px; background: var(--line);
         border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden;
         grid-template-columns: repeat(4, minmax(0, 1fr)); }
.focus .card { border: 0; border-radius: 0; box-shadow: none; }
.focus .card.alert { background: color-mix(in srgb, var(--bad) 6%, var(--panel)); }
.focus .card.alert .k { color: var(--bad); }
.focus .v { font-size: 15px; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.focus .card.alert .v { color: var(--ink); }
.focus .more { margin-top: 6px; }

/* Cross-filter charts. A segment or legend entry toggles a filter; every
   other chart recounts the tasks that pass the remaining filters. */
.xf-grid { display: grid; gap: 12px; grid-template-columns: repeat(6, minmax(0, 1fr)); }
.chart { margin: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
         box-shadow: var(--shadow); padding: 14px 14px 10px; min-width: 0; grid-column: span 2; }
.chart.half { grid-column: span 3; }
.chart figcaption { display: flex; gap: 8px; justify-content: space-between; align-items: baseline;
         font-weight: 650; margin: 0 2px 10px; }
.chart figcaption .note { font-weight: 400; }
.stack { display: flex; gap: 2px; height: 14px; border-radius: 4px; overflow: hidden;
         background: var(--sunken); margin: 0 2px 10px; }
.stack .seg { flex: 0 1 0; min-width: 3px; cursor: pointer; transition: flex-grow 0.2s ease-out, opacity 0.15s; }
.stack .seg.empty { display: none; }
/* A mark a filter leaves out keeps its colour as an outline, so it still
   reads at 3:1 against the track and the panel; only fill means selected. */
.seg, .sw, .track i { background: var(--mark); }
.seg.off, .key.off .sw { background: transparent; box-shadow: inset 0 0 0 2px var(--mark); }
.legend, .bars { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px;
                 grid-template-columns: minmax(0, 1fr); }
.legend > li, .bars > li { min-width: 0; }
.key { width: 100%; display: flex; align-items: center; gap: 8px; padding: 5px 8px; cursor: pointer;
       border: 1px solid transparent; border-radius: var(--radius-s); background: none;
       text-align: left; font-size: 13px; color: var(--ink); }
.key:hover { background: var(--panel-raised); border-color: var(--line); }
.key[aria-pressed="true"] { border-color: var(--accent);
       background: color-mix(in srgb, var(--accent) 10%, var(--panel)); font-weight: 600; }
.key.off { color: var(--muted); }
.key .lab { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.key .n { margin-left: auto; font-weight: 600; }
.key.empty .n { color: var(--muted); font-weight: 400; }
.sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.row .lab { flex: 0 0 40%; }
.row .track { flex: 1 1 auto; display: flex; gap: 1px; height: 10px; border-radius: 3px;
              overflow: hidden; background: var(--sunken); }
.row .track i { display: block; height: 100%; flex: none; transition: width 0.2s ease-out; }
.row .n { flex: 0 0 4ch; text-align: right; }
details.more-rows > summary { cursor: pointer; font-size: 13px; color: var(--accent); padding: 6px 8px;
       list-style: none; border-radius: var(--radius-s); }
details.more-rows > summary::-webkit-details-marker { display: none; }
.c-DONE, .c-HUMAN_VERIFIED { --mark: var(--good); }
.c-WIP, .c-AI_REVIEWED { --mark: var(--wip); }
.c-BLOCKED { --mark: var(--bad); }
.c-TODO, .c-UNTESTED { --mark: var(--todo); }
.c-SYNTHETIC { --mark: var(--warn); }
.c-execution { --mark: var(--accent); }
.c-operations { --mark: var(--ops); }

/* Phases */
.phases { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
.phase { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
         padding: 12px 14px; box-shadow: var(--shadow); min-width: 0; }
.phase.current { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), var(--shadow); }
.phase header { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.phase .outcome { color: var(--muted); font-size: 13px; margin: 6px 0 8px; }
.phase .mono { font-family: inherit; font-size: 13px; }
.thesis { padding: 12px 16px; margin-bottom: 10px; }
.thesis p { margin: 4px 0; font-size: 15px; max-width: 75ch; }
.hierarchy .module { padding: 8px 16px 10px; border-top: 1px solid var(--line); }
.hierarchy .module .outcome { color: var(--muted); font-size: 13px; margin: 2px 0 6px; }

/* Tables */
.tablewrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--radius);
             background: var(--panel); box-shadow: var(--shadow); }
.tablewrap.tall { max-height: 70vh; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--muted); font-weight: 600; font-size: 12px;
     padding: 8px 10px; border-bottom: 1px solid var(--line); background: var(--panel-raised);
     white-space: nowrap; }
.tablewrap th { position: sticky; top: 0; z-index: 1; }
td { padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr[data-task] { cursor: pointer; }
tbody tr[data-task]:hover { background: color-mix(in srgb, var(--accent) 7%, transparent); }
td.num { text-align: right; white-space: nowrap; }

.pill { display: inline-block; font-size: 11px; line-height: 16px; padding: 1px 7px; border-radius: 999px;
        border: 1px solid var(--line-strong); color: var(--muted); white-space: nowrap; font-weight: 600;
        letter-spacing: 0.01em; vertical-align: 1px; }
.pill.DONE, .pill.GREEN, .pill.PASS, .pill.COMPLETE, .pill.HUMAN_VERIFIED { color: var(--good);
        border-color: color-mix(in srgb, var(--good) 55%, transparent); }
.pill.WIP, .pill.ACTIVE, .pill.AI_REVIEWED { color: var(--wip);
        border-color: color-mix(in srgb, var(--wip) 55%, transparent); }
.pill.EXIT_PENDING, .pill.SYNTHETIC, .pill.HIGH { color: var(--warn);
        border-color: color-mix(in srgb, var(--warn) 55%, transparent); }
.pill.RED, .pill.FAIL, .pill.BLOCKED { color: var(--bad);
        border-color: color-mix(in srgb, var(--bad) 55%, transparent);
        background: color-mix(in srgb, var(--bad) 7%, transparent); }
.pill.RECONSTRUCTED, .pill.PROPOSED { color: var(--warn); border-style: dashed;
        border-color: color-mix(in srgb, var(--warn) 55%, transparent); }
.pill.current { color: var(--on-accent); background: var(--accent); border-color: var(--accent); }
/* Operations is labelled wherever it appears, in its own calm colour: it is
   not a warning, it is a different kind of work (ADR-045). */
.pill.domain-operations, .pill.operations { color: var(--ops);
        border-color: color-mix(in srgb, var(--ops) 55%, transparent); border-style: dashed; }
.tasklink.ops code { color: var(--ops); }
.tracelink { color: var(--ops); font-size: 13px; text-decoration: none; }
.tracelink:hover { text-decoration: underline; }
.external { margin-top: 4px; width: 100%; font-size: 13px; color: var(--muted); }
.lead { font-size: 14px; margin: 18px 0 0; max-width: 75ch; }
ul.events li.event { padding: 8px 0; }
.evhead { display: flex; gap: 6px; flex-wrap: wrap; align-items: baseline; }
.evmeta { color: var(--muted); font-size: 13px; margin-top: 3px; overflow-wrap: anywhere; }
.everror { color: var(--bad); font-size: 13px; margin-top: 3px; overflow-wrap: anywhere; }
.opbody { padding: 0 16px 10px; }
details.group.op > summary { font-weight: 400; }
.flash { animation: flash 1.6s ease-out 1; }
@keyframes flash { from { background: color-mix(in srgb, var(--ops) 22%, transparent); } to { background: transparent; } }

.tasklink { border: 0; background: none; padding: 0; cursor: pointer; text-align: left;
            color: var(--ink); border-radius: 3px; }
.tasklink:hover { text-decoration: underline; text-underline-offset: 3px; }
.tasklink code { color: var(--muted); }

ul.plain { list-style: none; padding: 0; margin: 0; }
ul.plain li { padding: 9px 0; border-bottom: 1px solid var(--line); }
ul.plain li:last-child { border-bottom: 0; }
code, .mono { font-family: ui-monospace, "Cascadia Mono", SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.box { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
       padding: 4px 16px; box-shadow: var(--shadow); }

/* Obstacle groups and decisions disclose on demand. */
details.group { background: var(--panel); border: 1px solid var(--line);
                border-radius: var(--radius); margin-bottom: 10px; box-shadow: var(--shadow); }
details.group > summary { cursor: pointer; padding: 10px 16px; font-weight: 600; list-style: none;
                border-radius: var(--radius); }
details.group > summary::-webkit-details-marker { display: none; }
details.group > summary::before, details.more-rows > summary::before {
  content: ""; display: inline-block; width: 6px; height: 6px; margin: 0 10px 2px 2px;
  border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor;
  transform: rotate(-45deg); transition: transform 0.15s ease-out; color: var(--muted); }
details.group[open] > summary::before, details.more-rows[open] > summary::before { transform: rotate(45deg); }
details.group > .note, details.group > ul { padding: 0 16px 10px; }
/* A group inside a group is a section of it, not a second card. */
details.group details.group, details.group details.acard { border: 0; border-top: 1px solid var(--line);
                border-radius: 0; box-shadow: none; margin: 0; }
.count { color: var(--muted); font-weight: 400; margin-left: 6px; }

/* The critical path reads top to bottom. */
ol.chain { list-style: none; margin: 0; padding: 0; }
ol.chain li { position: relative; padding: 8px 12px; background: var(--panel);
              border: 1px solid var(--line); border-radius: var(--radius-s); display: flex; gap: 8px;
              align-items: baseline; flex-wrap: wrap; }
ol.chain li + li { margin-top: 16px; }
ol.chain li + li::before { content: ""; position: absolute; left: 22px; top: -17px; height: 16px;
              border-left: 2px solid var(--line-strong); }
ol.chain li.done { background: var(--panel-raised); }
ol.chain li.done .tasklink { color: var(--muted); }

/* Filters */
.controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.controls input[type="search"], .controls input[type="text"], .controls select {
  font: inherit; font-size: 14px; color: var(--ink); background: var(--panel); min-height: 34px;
  border: 1px solid var(--line-strong); border-radius: var(--radius-s); padding: 5px 10px; }
.controls input::placeholder, .aform textarea::placeholder { color: var(--muted); opacity: 1; }
.controls input[type="search"] { flex: 1 1 240px; min-width: 0; }
.chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.chips button { cursor: pointer; font-size: 13px; padding: 4px 10px; border-radius: 999px;
  background: var(--panel); color: var(--muted); border: 1px solid var(--line-strong); }
.chips button[aria-pressed="true"] { color: var(--ink); border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, var(--panel)); font-weight: 600; }
.shown { color: var(--muted); font-size: 13px; margin-left: auto; }

details.adr { border-bottom: 1px solid var(--line); }
details.adr:last-child { border-bottom: 0; }
details.adr > summary { cursor: pointer; padding: 9px 0; display: flex; gap: 8px;
                        align-items: baseline; flex-wrap: wrap; }
details.adr dl { margin: 0 0 10px 18px; }
details.adr dt { color: var(--muted); font-size: 12px; font-weight: 600; margin-top: 6px; }
details.adr dd { margin: 2px 0 0; }

.diagram { background: var(--panel); border: 1px solid var(--line);
           border-radius: var(--radius); position: relative; overflow: hidden; }
.diagram pre { margin: 0; white-space: pre-wrap; color: var(--muted); font-size: 12px; }
.canvas { position: relative; height: clamp(380px, 64vh, 780px); overflow: hidden;
          cursor: grab; touch-action: none; }
.canvas.grabbing { cursor: grabbing; }
/* Without a rendered diagram the canvas holds diagram source, which is read by
   scrolling like any other text. */
.canvas.plain { height: auto; overflow: auto; padding: 16px; cursor: auto; }
.canvas.plain .stage { position: static; transform: none !important; }
.canvas.plain .zoom, .canvas.plain .trace { display: none; }
.stage { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
.stage svg { max-width: none !important; display: block; }
.zoom { position: absolute; right: 10px; top: 10px; z-index: 2; display: flex; gap: 2px;
        align-items: center; background: var(--panel); border: 1px solid var(--line-strong);
        border-radius: 999px; padding: 3px; }
.zoom button { font: inherit; font-size: 13px; line-height: 1; width: 28px; height: 26px;
        cursor: pointer; background: none; border: 0; color: var(--muted); border-radius: 999px; }
.zoom button.wide { width: auto; padding: 0 10px; font-size: 12px; }
.zoom button:hover { color: var(--ink); background: var(--panel-raised); }
.zoom .level { font-size: 12px; color: var(--muted); min-width: 42px; text-align: center; }
.trace { position: absolute; left: 12px; bottom: 10px; z-index: 2; font-size: 12px;
         color: var(--muted); pointer-events: none; max-width: calc(100% - 140px);
         overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* Tracing dims everything outside the hovered task's chain. Opacity is used
   rather than colour so a task's status colour still reads. */
.stage.tracing g.node { opacity: 0.12; }
.stage.tracing g.node.chain { opacity: 1; }
.stage.tracing g.node.focus > rect, .stage.tracing g.node.focus > polygon,
.stage.tracing g.node.focus > path { stroke-width: 3px; }
.stage.tracing .flowchart-link { opacity: 0.06; }
.stage.tracing .flowchart-link.chain { opacity: 1; }
.stage.tracing g.cluster { opacity: 0.45; }
/* Only nodes that are tasks open anything, so only those look clickable. */
.stage g.node.opens { cursor: pointer; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.tabs button { font: inherit; font-size: 13px; padding: 5px 12px; cursor: pointer;
  background: var(--panel); color: var(--muted); border: 1px solid var(--line-strong);
  border-radius: 999px; }
.tabs button[aria-selected="true"] { color: var(--ink); border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, var(--panel)); font-weight: 600; }
dialog { border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel);
  color: var(--ink); max-width: 680px; width: calc(100% - 32px); padding: 0;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25); }
dialog::backdrop { background: rgba(10, 12, 16, 0.5); }
dialog .body { padding: 22px 24px 26px; }
dialog h3 { margin: 0 0 6px; font-size: 18px; padding-right: 32px; }
dialog dt { color: var(--muted); font-size: 12px; font-weight: 600; margin-top: 14px; }
dialog dd { margin: 4px 0 0; }
dialog button.close { position: sticky; top: 0; float: right; margin: 10px 10px 0 0; width: 32px; height: 32px;
  display: grid; place-items: center; background: none; border: 0; border-radius: 8px;
  color: var(--muted); cursor: pointer; }
dialog button.close:hover { background: var(--panel-raised); color: var(--ink); }
dialog button.close svg { width: 18px; height: 18px; }
.note { color: var(--muted); font-size: 13px; }
footer { color: var(--muted); font-size: 12px; margin-top: 48px;
         border-top: 1px solid var(--line); padding-top: 14px; }

@media (max-width: 1280px) {
  .attention { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .focus { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .xf-grid { grid-template-columns: minmax(0, 1fr); }
  .chart, .chart.half { grid-column: auto; }
}
@media (max-width: 560px) {
  main { padding: 14px 16px 64px; }
  .stickyhead { margin: 0 -16px; padding: 0 16px; }
  h1 { font-size: 20px; }
  .attention { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .focus { grid-template-columns: minmax(0, 1fr); }
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .phases { grid-template-columns: minmax(0, 1fr); }
  .shown, .xf-count { margin-left: 0; width: 100%; }
  .canvas { height: 60vh; }
  .row .lab { flex-basis: 46%; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important;
                           scroll-behavior: auto !important; }
}
"""

# The cross-filter rules, kept free of the page so they can be run and
# tested without a browser (see tests/test_dashboard_redesign.py).
FILTER_JS = """
// --- Cross-filter rules ------------------------------------------------
// Pure: nothing here reads the page, so the rules run and are tested anywhere.
// DATA and byId are the embedded compiled project, defined below.

const DIMS = {
  status: t => t.status, phase: t => t.phase, module: t => t.module || '~none',
  domain: t => t.domain, validation: t => t.validation,
};
const DIM_LABEL = { status: 'Status', phase: 'Phase', module: 'Module', domain: 'Domain', validation: 'Validation' };
const filters = Object.fromEntries(Object.keys(DIMS).map(dim => [dim, new Set()]));

function filtering() { return Object.keys(DIMS).some(dim => filters[dim].size); }

function passes(task, skip) {
  if (!task) return !filtering();
  return Object.keys(DIMS).every(dim =>
    dim === skip || !filters[dim].size || filters[dim].has(DIMS[dim](task)));
}

function filterQuery() {
  return Object.keys(DIMS).filter(dim => filters[dim].size)
    .map(dim => dim + '=' + [...filters[dim]].map(encodeURIComponent).join(',')).join('&');
}

function readFilters(query) {
  Object.values(filters).forEach(chosen => chosen.clear());
  (query || '').split('&').forEach(pair => {
    const [dim, values] = pair.split('=');
    if (!filters[dim] || !values) return;
    values.split(',').forEach(value => {
      try { filters[dim].add(decodeURIComponent(value)); } catch (error) {}
    });
  });
}

function hashFor(name) {
  const query = filterQuery();
  return query ? name + '?' + query : name;
}

// A chart counts the tasks that pass every filter but its own, by value, and
// splits each value by status.
function countsFor(dim) {
  const counts = {};
  const byStatus = {};
  DATA.tasks.filter(task => passes(task, dim)).forEach(task => {
    const value = DIMS[dim](task);
    counts[value] = (counts[value] || 0) + 1;
    byStatus[value] = byStatus[value] || {};
    byStatus[value][task.status] = (byStatus[value][task.status] || 0) + 1;
  });
  return { counts, byStatus };
}

// A share as a whole percent, rounded half up: the same rule as _percent()
// in the compiler, so a recount over every task reads as the rendered page.
function share(part, whole) {
  return whole ? Math.floor(part / whole * 100 + 0.5) : 0;
}

// An aggregate is a list of [task ids, weight] entries written by the
// compiler's selections. An entry counts while any of its tasks passes; one
// that names no task can only be counted while nothing is filtered.
function aggValue(entries) {
  return entries.reduce((sum, [ids, weight]) => {
    const known = ids.split(' ').filter(id => byId[id]);
    const counted = known.length ? known.some(id => passes(byId[id])) : !filtering();
    return sum + (counted ? weight : 0);
  }, 0);
}
"""

_SCRIPT = """
const DATA = JSON.parse(document.getElementById('project-data').textContent);
const DIAGRAMS = JSON.parse(document.getElementById('diagram-data').textContent);
const NODE_MAP = JSON.parse(document.getElementById('node-map').textContent);
const NODE_KEY = Object.fromEntries(
  Object.entries(NODE_MAP).map(([drawn, taskId]) => [taskId, drawn]));
const byId = Object.fromEntries(DATA.tasks.map(t => [t.id, t]));
const decisionById = Object.fromEntries(DATA.decisions.map(d => [d.id, d]));
const eventById = Object.fromEntries(DATA.operations.events.map(e => [e.id, e]));

// Authored prose becomes markup here too, so it is escaped here too.
const esc = value => String(value == null ? '' : value).replace(
  /[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function pill(value) { return `<span class="pill ${esc(value)}">${esc(value)}</span>`; }

function showTask(id) {
  const task = byId[id];
  if (!task) return;
  const contract = DATA.acceptance[task.ac] || { criteria: [], inherits: [] };
  const criteria = contract.criteria.map(c =>
    `<li>${pill(c.state)} <code>${esc(c.id)}</code> <span class="note">${esc(c.class || c.evidenceClass)}</span><br>${esc(c.text)}</li>`
  ).join('') || '<li class="note">No criteria recorded.</li>';
  const deps = task.deps.length
    ? task.deps.map(d => `<code>${esc(d)}</code> ${pill(byId[d] ? byId[d].status : 'MISSING')}`).join('<br>')
    : '<span class="note">None</span>';
  const blocks = task.blocks.length
    ? task.blocks.map(d => `<code>${esc(d)}</code>`).join(', ')
    : '<span class="note">Nothing downstream</span>';
  const obstacles = DATA.obstacles.filter(o => o.subject === id);
  document.querySelector('#detail .body').innerHTML = `
    <h3>${esc(task.id)} — ${esc(task.title)}</h3>
    <p class="sub">${pill(task.status)} ${pill(task.validation)}
       <span class="pill">${esc(task.phase)}</span>
       ${task.module ? `<span class="pill">${esc(task.module)}</span>` : ''}
       <span class="pill ${esc(task.domain)}">${esc(task.domain)}</span>
       ${task.owner ? `<span class="pill">${esc(task.owner)}</span>` : ''}</p>
    <dl>
      <dt>Dependencies</dt><dd>${deps}</dd>
      <dt>Blocks</dt><dd>${blocks}</dd>
      <dt>Acceptance — ${esc(task.ac || 'none')}</dt><dd><ul class="plain">${criteria}</ul></dd>
      ${contract.inherits.length ? `<dt>Inherited invariants</dt><dd>${contract.inherits.map(i => `<code>${esc(i)}</code>`).join(', ')}</dd>` : ''}
      ${obstacles.length ? `<dt>Obstacles</dt><dd>${obstacles.map(o => `${pill(o.type)} ${esc(o.detail)}`).join('<br>')}</dd>` : ''}
      <dt>Domain</dt><dd>${esc(task.domain)} <span class="note">(${esc(task.domainSource)})</span></dd>
      ${task.externalBlockers.length ? `<dt>External blockers · project operations</dt><dd>${task.externalBlockers.map(o => `<code>${esc(o)}</code> ${pill(byId[o] ? byId[o].status : 'MISSING')}`).join('<br>')}</dd>` : ''}
      <dt>Evidence</dt><dd>${task.evidence ? esc(task.evidence) : '<span class="note">None recorded</span>'}</dd>
      ${task.events.length ? `<dt>Operational events</dt><dd>${task.events.map(e => { const ev = eventById[e]; return ev ? `<code>${esc(e)}</code> ${pill(ev.type)} ${pill(ev.outcome)} ${esc(ev.title)}` : `<code>${esc(e)}</code>`; }).join('<br>')}</dd>` : ''}
      ${task.decisions.length ? `<dt>Decisions</dt><dd>${task.decisions.map(d => `<code>${esc(d)}</code>${decisionById[d] && decisionById[d].origin === 'RECONSTRUCTED' ? ' ' + pill('RECONSTRUCTED') : ''}`).join(', ')}</dd>` : ''}
      <dt>Schedule</dt><dd>${task.schedule ? esc(JSON.stringify(task.schedule)) : '<span class="note">Unscheduled</span>'}</dd>
      <dt>Source</dt><dd><code>${esc(DATA.project.authority)}/${esc(task.source.file)} → ${esc(task.source.anchor)}</code></dd>
    </dl>`;
  document.getElementById('detail').showModal();
}

// A task opens from any element that names it: a table row, a link in the
// focus strip, a step on the critical path. All read the one embedded project.
document.addEventListener('click', event => {
  const opener = event.target.closest('[data-task]');
  if (opener) showTask(opener.dataset.task);
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest && event.target.closest('tr[data-task]');
  if (!row) return;
  event.preventDefault();
  showTask(row.dataset.task);
});
document.querySelector('#detail .close').addEventListener('click',
  () => document.getElementById('detail').close());

// --- The diagram canvas ------------------------------------------------
// A 48-task graph drawn to fit a panel is too small to read, so the diagram
// is given its natural size and the canvas is zoomed and panned instead.

const canvas = document.getElementById('canvas');
const stage = document.getElementById('stage');
const target = document.getElementById('diagram');
const levelText = document.getElementById('zoom-level');
const traceNote = document.getElementById('trace-note');
const view = { k: 1, x: 0, y: 0 };
let adjusted = false;   // the reader has zoomed or panned; stop re-fitting
let drag = null;
let panned = false;
let traced = null;

function applyView() {
  stage.style.transform =
    `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
  levelText.textContent = Math.round(view.k * 100) + '%';
}

function naturalSize() {
  const svg = stage.querySelector('svg');
  if (!svg) return null;
  // Mermaid ships the drawing at width:100% with a max-width, which is what
  // shrinks it. The viewBox carries the size it was actually laid out at.
  const box = svg.viewBox && svg.viewBox.baseVal;
  const width = box && box.width ? box.width : svg.getBoundingClientRect().width;
  const height = box && box.height ? box.height : svg.getBoundingClientRect().height;
  if (!width || !height) return null;
  svg.style.maxWidth = 'none';
  svg.style.width = width + 'px';
  svg.style.height = height + 'px';
  return { width, height };
}

function fitScale(size, box) {
  return Math.min(box.width / size.width, box.height / size.height, 1);
}

function fit() {
  const size = naturalSize();
  if (!size) return;
  const box = canvas.getBoundingClientRect();
  if (!box.width || !box.height) return;
  view.k = fitScale(size, box);
  view.x = (box.width - size.width * view.k) / 2;
  view.y = (box.height - size.height * view.k) / 2;
  adjusted = false;
  applyView();
}

// Fitting a large graph into a panel is what made it unreadable in the first
// place, so a diagram that only fits below this is opened at a legible scale,
// centred on the work that matters now, with Fit one click away.
const LEGIBLE = 0.6;

function openingTask() {
  // What is in flight, else what can start, else the critical path.
  const candidates = [...DATA.wip, ...DATA.ready, ...DATA.criticalPath];
  return candidates.find(id => byId[id]) || null;
}

function centreOn(taskId) {
  const node = taskId && stage.querySelector(
    `g.node[data-id="${NODE_KEY[taskId] || ''}"]`);
  if (!node) return false;
  const nodeBox = node.getBoundingClientRect();
  const canvasBox = canvas.getBoundingClientRect();
  view.x += canvasBox.left + canvasBox.width / 2 - (nodeBox.left + nodeBox.width / 2);
  view.y += canvasBox.top + canvasBox.height / 2 - (nodeBox.top + nodeBox.height / 2);
  applyView();
  return true;
}

function openView() {
  const size = naturalSize();
  if (!size) return;
  const box = canvas.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const fitted = fitScale(size, box);
  if (fitted >= LEGIBLE) { fit(); return; }
  view.k = LEGIBLE;
  view.x = (box.width - size.width * view.k) / 2;
  view.y = (box.height - size.height * view.k) / 2;
  adjusted = false;
  applyView();
  centreOn(openingTask());
}

function zoomAt(factor, cx, cy) {
  const next = Math.min(8, Math.max(0.05, view.k * factor));
  const ratio = next / view.k;
  view.x = cx - (cx - view.x) * ratio;
  view.y = cy - (cy - view.y) * ratio;
  view.k = next;
  adjusted = true;
  applyView();
}

function zoomCentre(factor) {
  const box = canvas.getBoundingClientRect();
  zoomAt(factor, box.width / 2, box.height / 2);
}

document.querySelectorAll('.zoom button').forEach(button =>
  button.addEventListener('click', () => {
    if (button.dataset.zoom === 'fit') fit();
    else zoomCentre(button.dataset.zoom === 'in' ? 1.25 : 1 / 1.25);
  }));

// Plain wheel keeps scrolling the page. A trackpad pinch arrives as
// ctrl+wheel, so pinch-to-zoom works without claiming ordinary scrolling.
canvas.addEventListener('wheel', event => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const box = canvas.getBoundingClientRect();
  zoomAt(Math.exp(-event.deltaY / 240),
         event.clientX - box.left, event.clientY - box.top);
}, { passive: false });

canvas.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  // The zoom controls sit on the canvas. Capturing the pointer for them would
  // deliver their click to the canvas, and the button would never fire.
  if (event.target.closest && event.target.closest('.zoom')) return;
  drag = { x: event.clientX - view.x, y: event.clientY - view.y,
           fromX: event.clientX, fromY: event.clientY };
  panned = false;
  canvas.setPointerCapture(event.pointerId);
  canvas.classList.add('grabbing');
});
canvas.addEventListener('pointermove', event => {
  if (!drag) return;
  // A click carries a little movement with it. Past this the gesture was a
  // pan, and releasing over a task must not open it.
  if (Math.abs(event.clientX - drag.fromX) > 3 ||
      Math.abs(event.clientY - drag.fromY) > 3) panned = true;
  view.x = event.clientX - drag.x;
  view.y = event.clientY - drag.y;
  adjusted = true;
  applyView();
});
['pointerup', 'pointercancel'].forEach(name =>
  canvas.addEventListener(name, () => {
    drag = null;
    canvas.classList.remove('grabbing');
  }));
canvas.addEventListener('dblclick', fit);
// Re-open rather than re-fit: fitting is what made the graph unreadable,
// and openView() is the rule for what a fresh view should show.
addEventListener('resize', () => { if (!adjusted) openView(); });

// --- Tracing a dependency chain ----------------------------------------
// The chain comes from the compiled dependencies, never from the drawing, so
// a highlight and `praukron explain` cannot disagree (ADR-025).

function chainOf(id) {
  const chain = new Set([id]);
  for (const direction of ['deps', 'blocks']) {
    const queue = [id];
    while (queue.length) {
      const task = byId[queue.pop()];
      if (!task) continue;
      for (const next of task[direction]) {
        if (!chain.has(next)) { chain.add(next); queue.push(next); }
      }
    }
  }
  return chain;
}

function edgeEnd(element, prefix) {
  for (const name of element.classList) {
    if (name.startsWith(prefix)) return NODE_MAP[name.slice(prefix.length)];
  }
  return null;
}

function trace(id) {
  const chain = chainOf(id);
  const task = byId[id];
  stage.classList.add('tracing');
  stage.querySelectorAll('g.node[data-id]').forEach(node => {
    const taskId = NODE_MAP[node.dataset.id];
    node.classList.toggle('chain', chain.has(taskId));
    node.classList.toggle('focus', taskId === id);
  });
  stage.querySelectorAll('.flowchart-link').forEach(edge => {
    const from = edgeEnd(edge, 'LS-');
    const to = edgeEnd(edge, 'LE-');
    edge.classList.toggle('chain', chain.has(from) && chain.has(to));
  });
  const others = chain.size - 1;
  traceNote.textContent = `${id} — ${task ? task.title : ''} · ` +
    (others ? `${others} connected task${others === 1 ? '' : 's'}` : 'nothing connected');
}

function clearTrace() {
  stage.classList.remove('tracing');
  stage.querySelectorAll('.chain, .focus').forEach(
    element => element.classList.remove('chain', 'focus'));
  traceNote.textContent = HINT;
}

const HINT = 'Hover a task to trace its chain · click it for detail · drag to pan · ' +
  'pinch or \u2318/Ctrl-scroll to zoom';

canvas.addEventListener('mousemove', event => {
  if (drag) return;
  const node = event.target.closest && event.target.closest('g.node[data-id]');
  const id = node ? NODE_MAP[node.dataset.id] : null;
  if (id === traced) return;
  traced = id;
  if (id) trace(id); else clearTrace();
});
canvas.addEventListener('mouseleave', () => { traced = null; clearTrace(); });

// The drawing is only how the element is found. What opens is the same dialog
// the task tables open, reading the same compiled project (ADR-025).
// The canvas captures the pointer so a drag can leave it, and a captured
// pointer's click is delivered to the canvas, not the node under it. The node
// is therefore found from where the click landed.
function nodeAt(event) {
  const direct = event.target.closest && event.target.closest('g.node[data-id]');
  if (direct) return direct;
  const under = document.elementFromPoint(event.clientX, event.clientY);
  return under && under.closest ? under.closest('g.node[data-id]') : null;
}
canvas.addEventListener('click', event => {
  if (panned) return;
  const node = nodeAt(event);
  const id = node ? NODE_MAP[node.dataset.id] : null;
  if (id) showTask(id);
});

function markOpenable() {
  stage.querySelectorAll('g.node[data-id]').forEach(node =>
    node.classList.toggle('opens', Boolean(NODE_MAP[node.dataset.id])));
}

// --- Rendering ---------------------------------------------------------
// A diagram is drawn only once its tab is visible: laid out inside a hidden
// panel, every measurement Mermaid takes would be zero (ADR-044).

const graphPanel = document.getElementById('panel-graph');
let currentDiagram = 'task-graph.mmd';
let drawn = false;

function graphVisible() { return graphPanel.getBoundingClientRect().width > 0; }

function mermaidTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'neutral';
}

function draw() {
  // Mermaid sizes each label from the rendered text. Under a scaled stage it
  // would measure the scaled size and draw every label that much too small.
  view.k = 1; view.x = 0; view.y = 0;
  applyView();
  target.textContent = DIAGRAMS[currentDiagram];
  target.removeAttribute('data-processed');
  traced = null;
  clearTrace();
  drawn = true;
  const ready = () => { openView(); markOpenable(); };
  const done = window.mermaid.run({ nodes: [target] });
  if (done && typeof done.then === 'function') done.then(ready, ready);
  else ready();
}

const tabs = document.querySelectorAll('.tabs button');
tabs.forEach(button => button.addEventListener('click', () => {
  tabs.forEach(other => other.setAttribute('aria-selected', String(other === button)));
  currentDiagram = button.dataset.diagram;
  if (window.mermaid) draw();
  else target.textContent = DIAGRAMS[currentDiagram];
}));

if (window.mermaid) {
  window.mermaid.initialize({ startOnLoad: false, maxTextSize: 200000, theme: mermaidTheme() });
  traceNote.textContent = HINT;
} else {
  // Nothing below depends on a diagram: the page shows the source and every
  // number on it still reports.
  canvas.classList.add('plain');
  document.querySelectorAll('.mermaid').forEach(node => {
    const source = document.createElement('pre');
    source.textContent = node.textContent;
    node.replaceWith(source);
  });
  document.getElementById('offline-note').hidden = false;
}

function graphShown() {
  if (!window.mermaid) return;
  if (!drawn) draw();
  else if (!adjusted) openView();
}

// --- Cross-filtering ---------------------------------------------------
// Selecting a chart segment narrows every chart, table, figure and the Needs
// you panel to the tasks that pass all active filters. It hides what is
// already on the page and recounts compiled selections; it decides nothing
// new (ADR-044). The rules are above, in FILTER_JS; this only applies them.

const SCOPED = 'ul.xf-list > li, ol.chain > li, details.acard';
const CLOSE_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" ' +
  'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/></svg>';

function visible(element) { return Boolean(element && element.getClientRects().length); }

// An element belongs to the tasks it names. One with no task (an assumption
// recorded against a phase only) can be placed by a phase filter alone.
function scopeMatches(element) {
  const ids = [...element.querySelectorAll('[data-task]')]
    .map(node => node.dataset.task).filter(id => byId[id]);
  if (element.dataset.task && byId[element.dataset.task]) ids.push(element.dataset.task);
  if (ids.length) return ids.some(id => passes(byId[id]));
  if (Object.keys(DIMS).some(dim => dim !== 'phase' && filters[dim].size)) return false;
  const phases = (element.dataset.phase || '').split(' ').filter(Boolean);
  return !filters.phase.size || phases.some(phase => filters.phase.has(phase));
}

function keyLabel(dim, value) {
  const key = [...document.querySelectorAll(`[data-chart] .key[data-dim="${dim}"]`)]
    .find(node => node.dataset.value === value);
  return key ? key.querySelector('.lab').textContent : value;
}

function renderCharts() {
  document.querySelectorAll('[data-chart]').forEach(chart => {
    const dim = chart.dataset.chart;
    const { counts, byStatus } = countsFor(dim);
    const top = Math.max(1, ...Object.values(counts));
    const chosen = filters[dim];
    chart.querySelectorAll('[data-value]').forEach(node => {
      const value = node.dataset.value;
      const n = counts[value] || 0;
      node.classList.toggle('off', chosen.size > 0 && !chosen.has(value));
      node.classList.toggle('empty', !n);
      if (node.classList.contains('seg')) node.style.flexGrow = n;
      if (node.matches('button')) node.setAttribute('aria-pressed', String(chosen.has(value)));
      const number = node.querySelector('.n');
      if (number) number.textContent = n;
      node.querySelectorAll('.track i').forEach(part => {
        part.style.width = ((byStatus[value] || {})[part.dataset.s] || 0) / top * 100 + '%';
      });
    });
  });
}

// Every figure that counts tasks carries its compiled entries in data-agg.
// Unfiltered it shows exactly what it was rendered with; filtered, the sums.
function original(node, value) {
  if (!('aggOrig' in node.dataset)) node.dataset.aggOrig = value;
  return node.dataset.aggOrig;
}

function renderAggregates() {
  const on = filtering();
  const needs = document.getElementById('needs-you');
  const shown = selector => [...needs.querySelectorAll(selector)]
    .filter(item => !item.classList.contains('xf-out')).length;
  document.querySelectorAll('[data-agg], [data-agg-from]').forEach(holder => {
    const sums = {};
    if (holder.dataset.aggFrom === 'needs') {
      sums.review = shown('details.acard');
      sums.held = shown('ul.owner-list > li');
      sums.n = sums.review + sums.held;
    } else {
      Object.entries(JSON.parse(holder.dataset.agg)).forEach(([key, entries]) => {
        sums[key] = aggValue(entries);
      });
    }
    holder.querySelectorAll('[data-agg-key]').forEach(node => {
      const before = original(node, node.textContent);
      node.textContent = on ? sums[node.dataset.aggKey] : before;
    });
    holder.querySelectorAll('[data-agg-pct]').forEach(node => {
      const before = original(node, node.textContent);
      const [part, whole] = node.dataset.aggPct.split('/');
      node.textContent = on ? share(sums[part], sums[whole]) + '%' : before;
    });
    holder.querySelectorAll('[data-agg-bar]').forEach(bar => {
      const before = original(bar, bar.style.width);
      const [part, whole] = bar.dataset.aggBar.split('/');
      bar.style.width = on ? (sums[whole] ? sums[part] / sums[whole] * 100 : 0) + '%' : before;
    });
    const count = on ? sums.n : Number(holder.dataset.aggN);
    holder.querySelectorAll('[data-agg-text]').forEach(node => {
      const before = original(node, node.textContent);
      node.textContent = !on ? before
        : holder.dataset.tone && !count ? 'Clear'
        : node.dataset.aggText.replace(/\\{(\\w+)\\}/g, (_, key) => sums[key]);
    });
    if (holder.dataset.tone) {
      holder.classList.toggle('clear', !count);
      holder.classList.toggle(holder.dataset.tone, Boolean(count));
    }
  });
  // A group's count is the number of its items still shown.
  document.querySelectorAll('details.group').forEach(group => {
    const counter = group.querySelector(':scope > summary > .count');
    const items = [...group.querySelectorAll(SCOPED)];
    if (!counter || !items.length) return;
    const before = original(counter, counter.textContent);
    if (!/^\\d+$/.test(before)) return;
    counter.textContent = on ? items.filter(item => !item.classList.contains('xf-out')).length : before;
  });
}

function renderChips() {
  const bar = document.getElementById('xf-bar');
  const matching = DATA.tasks.filter(task => passes(task)).length;
  bar.hidden = !filtering();
  document.getElementById('xf-chips').innerHTML = Object.keys(DIMS).flatMap(dim =>
    [...filters[dim]].map(value => {
      const label = keyLabel(dim, value);
      return `<li><button type="button" class="chip-x" data-remove-dim="${esc(dim)}" ` +
        `data-remove-value="${esc(value)}" aria-label="Remove filter ${esc(DIM_LABEL[dim])}: ${esc(label)}">` +
        `${esc(DIM_LABEL[dim])}: <strong>${esc(label)}</strong>${CLOSE_ICON}</button></li>`;
    })).join('');
  const summary = `${matching} of ${DATA.tasks.length} tasks match`;
  document.getElementById('xf-count').textContent = summary;
  const status = document.getElementById('xf-status');
  status.textContent = filtering() ? summary : status.textContent ? 'Filters cleared' : '';
}

function renderScoped() {
  const on = filtering();
  document.querySelectorAll('tbody tr[data-task]').forEach(row => {
    if (!row.closest('#task-registry')) row.classList.toggle('xf-out', !passes(byId[row.dataset.task]));
  });
  document.querySelectorAll(SCOPED).forEach(element =>
    element.classList.toggle('xf-out', on && !scopeMatches(element)));
  // Only a filter empties a table this way; the registry's own search says so
  // in its own count.
  document.querySelectorAll('.tablewrap').forEach(wrap => {
    const rows = [...wrap.querySelectorAll('tbody tr[data-task]')];
    wrap.classList.toggle('xf-none', on && rows.length > 0 &&
      rows.every(row => row.hidden || row.classList.contains('xf-out')));
  });
  const needs = document.getElementById('needs-you');
  const hiddenHere = needs.querySelectorAll('.xf-out').length;
  const note = document.getElementById('needs-filtered');
  note.hidden = !hiddenHere;
  note.querySelector('span').textContent =
    `${hiddenHere} item${hiddenHere === 1 ? '' : 's'} hidden by the active filters.`;
}

// The registry's own selects are the same filters, one value at a time.
const REGISTRY_SELECTS = { 'task-domain': 'domain', 'task-phase': 'phase',
                           'task-status': 'status', 'task-validation': 'validation' };

function syncSelects() {
  Object.entries(REGISTRY_SELECTS).forEach(([id, dim]) => {
    const select = document.getElementById(id);
    if (!select) return;
    const all = select.options[0];
    all.dataset.label = all.dataset.label || all.textContent;
    const chosen = [...filters[dim]];
    select.value = chosen.length === 1 ? chosen[0] : '';
    all.textContent = chosen.length > 1
      ? `${DIM_LABEL[dim]}: ${chosen.length} selected` : all.dataset.label;
  });
}

let runRegistry = null;

function applyFilters({ record = true } = {}) {
  renderCharts();
  if (runRegistry) runRegistry();
  renderScoped();
  renderAggregates();
  renderChips();
  syncSelects();
  if (record) writeHash();
}

function toggleFilter(dim, value) {
  if (!filters[dim]) return;
  if (filters[dim].has(value)) filters[dim].delete(value);
  else filters[dim].add(value);
  applyFilters();
}

// Focus that sat on a control the filters just hid (a chip, Clear all, Show
// all) moves to the charts when they are on screen, else to the open tab, so
// a keyboard reader is never dropped at the top of the page.
function keepFocus() {
  if (visible(document.activeElement) && document.activeElement !== document.body) return;
  const fallback = [document.querySelector('#breakdown .key'),
                    document.querySelector('.toptabs [aria-selected="true"]')].find(visible);
  if (fallback) fallback.focus();
}

function clearFilters() {
  Object.values(filters).forEach(chosen => chosen.clear());
  applyFilters();
  keepFocus();
}

function writeHash() {
  const name = document.documentElement.dataset.tab || 'overview';
  if (location.hash !== '#' + hashFor(name)) history.replaceState(null, '', '#' + hashFor(name));
}

document.addEventListener('click', event => {
  const key = event.target.closest('[data-chart] [data-dim][data-value]');
  if (key) { toggleFilter(key.dataset.dim, key.dataset.value); return; }
  const chip = event.target.closest('[data-remove-dim]');
  if (chip) {
    // Focus moves to the next chip; with none left, keepFocus() places it.
    const next = chip.closest('li').nextElementSibling || chip.closest('li').previousElementSibling;
    const nextValue = next && next.querySelector('[data-remove-dim]');
    const target = nextValue ? [nextValue.dataset.removeDim, nextValue.dataset.removeValue] : null;
    filters[chip.dataset.removeDim].delete(chip.dataset.removeValue);
    applyFilters();
    const again = target && [...document.querySelectorAll('[data-remove-dim]')].find(node =>
      node.dataset.removeDim === target[0] && node.dataset.removeValue === target[1]);
    if (again) again.focus();
    else keepFocus();
    return;
  }
  if (event.target.closest('[data-xf-clear]')) clearFilters();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && filtering() && event.target.closest &&
      event.target.closest('[data-chart], #xf-bar')) clearFilters();
});
Object.entries(REGISTRY_SELECTS).forEach(([id, dim]) => {
  const select = document.getElementById(id);
  if (!select) return;
  select.addEventListener('change', () => {
    filters[dim].clear();
    if (select.value) filters[dim].add(select.value);
    applyFilters();
  });
});
readFilters(location.hash.slice(1).split('?')[1]);

// --- Top-level tabs ----------------------------------------------------
// Which tab is open is presentation state. It lives in the URL hash with the
// filters, so a reload or a shared link opens the same view, and it never
// touches authority.

const TOP = ['overview', 'assumptions', 'execution', 'graph', 'operations', 'governance', 'decisions', 'tasks'];
const topTabs = [...document.querySelectorAll('.toptabs [role="tab"]')];

function selectTab(name, { focus = false, record = true } = {}) {
  if (!TOP.includes(name)) name = 'overview';
  document.documentElement.dataset.tab = name;
  topTabs.forEach(tab => {
    const on = tab.dataset.tab === name;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    if (on && focus) tab.focus();
  });
  if (record && location.hash !== '#' + hashFor(name)) history.replaceState(null, '', '#' + hashFor(name));
  if (name === 'graph') graphShown();
}

topTabs.forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
document.querySelector('.toptabs').addEventListener('keydown', event => {
  const at = topTabs.findIndex(tab => tab.dataset.tab === document.documentElement.dataset.tab);
  const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
  let next = null;
  if (step) next = (at + step + topTabs.length) % topTabs.length;
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = topTabs.length - 1;
  if (next === null) return;
  event.preventDefault();
  selectTab(topTabs[next].dataset.tab, { focus: true });
});
addEventListener('hashchange', () => {
  // A link to a card (#assumption-A-1, #needs-you) moves to it without
  // switching tabs; only a tab name selects a tab.
  const [name, query] = location.hash.slice(1).split('?');
  if (TOP.includes(name)) {
    readFilters(query);
    applyFilters({ record: false });
    return selectTab(name, { record: false });
  }
  const target = document.getElementById(name);
  if (!target) return;
  if (target.tagName === 'DETAILS') target.open = true;
  const holder = target.parentElement && target.parentElement.closest('details');
  if (holder) holder.open = true;
  target.scrollIntoView({ block: 'start' });
  // The anchor has done its work; the address goes back to tab and filters,
  // so a reload or a shared link keeps what the reader is looking at.
  if (filtering()) writeHash();
});
selectTab(location.hash.slice(1).split('?')[0] || 'overview', { record: Boolean(location.hash) });

// A link to a tab switches to it and keeps the active filters; following the
// bare href would replace the hash and drop them.
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]:not([data-focus])');
  const name = link && link.getAttribute('href').slice(1);
  if (!name || !TOP.includes(name)) return;
  event.preventDefault();
  selectTab(name);
  document.getElementById('panel-' + name).focus();
});

// --- Theme -------------------------------------------------------------
// The reader's explicit choice is kept in localStorage. Until they make one,
// or after they choose System again, the operating system preference decides,
// and keeps deciding if it changes.

const THEME_KEY = 'praukron-theme';
const themeButtons = document.querySelectorAll('[data-theme-choice]');
const osDark = matchMedia('(prefers-color-scheme: dark)');

function storedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch (error) { return null; }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const stored = storedTheme();
  const choice = stored === 'light' || stored === 'dark' ? stored : 'system';
  themeButtons.forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice)));
  if (!window.mermaid) return;
  window.mermaid.initialize({ startOnLoad: false, maxTextSize: 200000, theme: mermaidTheme() });
  if (drawn && graphVisible()) draw();
  else drawn = false;
}

themeButtons.forEach(button => button.addEventListener('click', () => {
  if (button.dataset.themeChoice === 'system') {
    try { localStorage.removeItem(THEME_KEY); } catch (error) {}
    applyTheme(osDark.matches ? 'dark' : 'light');
    return;
  }
  try { localStorage.setItem(THEME_KEY, button.dataset.themeChoice); } catch (error) {}
  applyTheme(button.dataset.themeChoice);
}));
applyTheme(document.documentElement.dataset.theme || 'light');
const followOs = () => {
  const stored = storedTheme();
  if (stored !== 'light' && stored !== 'dark') applyTheme(osDark.matches ? 'dark' : 'light');
};
if (osDark.addEventListener) osDark.addEventListener('change', followOs);

// --- Filters -----------------------------------------------------------
// Filtering hides rows already on the page. It computes nothing new. The
// registry's selects are the cross-filters above; only search is its own.

function filterable(rows, controls, countEl, test) {
  const run = () => {
    let shown = 0;
    rows.forEach(row => { const on = test(row); row.hidden = !on; if (on) shown += 1; });
    countEl.textContent = `${shown} of ${rows.length}`;
  };
  controls.forEach(control => control.addEventListener('input', run));
  run();
  return run;
}

const taskRows = [...document.querySelectorAll('#task-registry tbody tr[data-task]')];
const taskSearch = document.getElementById('task-search');
runRegistry = filterable(taskRows, [taskSearch], document.getElementById('task-shown'), row => {
  const q = taskSearch.value.trim().toLowerCase();
  return (!q || row.dataset.text.includes(q)) && passes(byId[row.dataset.task]);
});
taskSearch.addEventListener('input', () => renderScoped());

const adrs = [...document.querySelectorAll('#decision-list details.adr')];
const adrSearch = document.getElementById('decision-search');
const chips = [...document.querySelectorAll('#decision-chips button')];
let adrFilter = '';
const runAdrs = filterable(adrs, [adrSearch], document.getElementById('decision-shown'), item => {
  const q = adrSearch.value.trim().toLowerCase();
  const matches = !adrFilter
    || (adrFilter === 'reconstructed' ? item.dataset.origin === 'RECONSTRUCTED'
                                      : item.dataset.status === adrFilter);
  return matches && (!q || item.dataset.text.includes(q));
});
chips.forEach(chip => chip.addEventListener('click', () => {
  adrFilter = chip.dataset.filter;
  chips.forEach(other => other.setAttribute('aria-pressed', String(other === chip)));
  runAdrs();
}));

const timelineItems = [...document.querySelectorAll('#timeline li.event')];
const timelineChips = [...document.querySelectorAll('#timeline-chips button')];
let timelineFilter = '';
const runTimeline = filterable(timelineItems, [], document.getElementById('timeline-shown'),
  item => !timelineFilter || item.dataset.type === timelineFilter);
timelineChips.forEach(chip => chip.addEventListener('click', () => {
  timelineFilter = chip.dataset.filter;
  timelineChips.forEach(other => other.setAttribute('aria-pressed', String(other === chip)));
  runTimeline();
}));

// A link into the operations trace opens that tab and brings the operation or
// event into view. It moves the reader; it changes nothing.
document.addEventListener('click', event => {
  const link = event.target.closest('a[data-focus]');
  if (!link) return;
  event.preventDefault();
  const dialog = document.getElementById('detail');
  if (dialog.open) dialog.close();
  selectTab('operations');
  const target = document.getElementById(link.dataset.focus);
  if (!target) return;
  if (target.tagName === 'DETAILS') target.open = true;
  const holder = target.closest('details');
  if (holder) holder.open = true;
  target.scrollIntoView({ block: 'center' });
  target.classList.remove('flash'); void target.offsetWidth; target.classList.add('flash');
});

applyFilters({ record: false });
"""


def _agg(**figures: list) -> str:
    """The compiled entries behind a card's figures, for the cross-filters.

    Each figure is a list of (task ids, weight). Unfiltered, the weights sum
    to the compiled figure; filtered, the page sums the entries whose tasks
    pass, so it recounts the compiler's selection and decides nothing (ADR-044)."""
    data = {
        name: [[" ".join(ids), weight] for ids, weight in entries if weight]
        for name, entries in figures.items()
    }
    return f' data-agg="{esc(json.dumps(data, separators=(",", ":")))}"'


def _percent(part: int, whole: int) -> int:
    """A share rounded half up, as the page's recount rounds it."""
    return int(part / whole * 100 + 0.5) if whole else 0


def _each(ids, weight: int = 1) -> list:
    return [([task_id], weight) for task_id in ids]


def _text(template: str) -> str:
    return f' data-agg-text="{esc(template)}"' if template else ""


def _metric_card(label: str, progress: dict, note: str = "", agg: str = "", template: str = "") -> str:
    percent = round(progress["fraction"] * 100)
    return (
        f'<div class="card"{agg}><div class="k">{label}</div>'
        f'<div class="v"><span data-agg-key="done">{progress["done"]}</span> <span class="note">/ '
        f'<span data-agg-key="total">{progress["total"]}</span></span></div>'
        f'<div class="bar"><i data-agg-bar="done/total" style="width:{percent}%"></i></div>'
        + (f'<div class="more"{_text(template)}>{esc(note)}</div>' if note else "")
        + "</div>"
    )


def _count_card(label: str, value: object, note: str = "", alert: bool = False,
                agg: str = "", template: str = "") -> str:
    return (
        f'<div class="card{" alert" if alert else ""}"{agg}><div class="k">{esc(label)}</div>'
        f'<div class="v" data-agg-key="n">{esc(value)}</div>'
        + (f'<div class="more"{_text(template)}>{esc(note)}</div>' if note else "")
        + "</div>"
    )


# Stacks read left to right as progress: finished work first.
_STATUS_STACK = ("DONE", "WIP", "BLOCKED", "TODO")
_VALIDATION_STACK = ("HUMAN_VERIFIED", "AI_REVIEWED", "SYNTHETIC", "UNTESTED")
_MODULE_ROWS = 8


def _breakdown(compiled: dict) -> str:
    """Charts the reader filters the page by.

    Every count is of compiled tasks, by a field the compiler wrote. The script
    recounts the same tasks when a filter narrows them; nothing is scored or
    inferred (ADR-044)."""
    tasks = compiled["tasks"]
    dims = {
        "status": lambda t: t["status"], "phase": lambda t: t["phase"],
        "module": lambda t: t["module"] or "~none", "domain": lambda t: t["domain"],
        "validation": lambda t: t["validation"],
    }
    counts: dict[str, dict[str, int]] = {dim: {} for dim in dims}
    mix: dict[str, dict[str, dict[str, int]]] = {dim: {} for dim in dims}
    for task in tasks:
        for dim, key in dims.items():
            value = key(task)
            counts[dim][value] = counts[dim].get(value, 0) + 1
            row = mix[dim].setdefault(value, {})
            row[task["status"]] = row.get(task["status"], 0) + 1

    def key(dim: str, value: str, label: str, swatch: str = "", track: str = "") -> str:
        return (
            f'<li><button type="button" class="key{" row" if track else ""}" data-dim="{dim}"'
            f' data-value="{esc(value)}" aria-pressed="false">{swatch}<span class="lab">{label}</span>{track}'
            f'<span class="n">{counts[dim][value]}</span><span class="sr"> tasks</span></button></li>'
        )

    def stack(dim: str, title: str, order: tuple) -> str:
        values = [v for v in order if counts[dim].get(v)]
        segments = "".join(
            f'<span class="seg c-{esc(v)}" data-dim="{dim}" data-value="{esc(v)}"'
            f' style="flex-grow:{counts[dim][v]}"></span>'
            for v in values
        )
        keys = "".join(key(dim, v, esc(v), f'<i class="sw c-{esc(v)}"></i>') for v in values)
        return (
            f'<figure class="chart" data-chart="{dim}"><figcaption>{title}</figcaption>'
            f'<div class="stack" aria-hidden="true">{segments}</div>'
            f'<ul class="legend" aria-label="{title}">{keys}</ul></figure>'
        )

    def rows(dim: str, entries: list[tuple[str, str]]) -> list[str]:
        top = max(counts[dim].values(), default=1)
        out = []
        for value, label in entries:
            if not counts[dim].get(value):
                continue
            track = "".join(
                f'<i class="c-{s}" data-s="{s}" style="width:{mix[dim][value].get(s, 0) / top * 100:.2f}%"></i>'
                for s in _STATUS_STACK
            )
            out.append(key(dim, value, label, track=f'<span class="track" aria-hidden="true">{track}</span>'))
        return out

    phase_names = {p["id"]: p["name"] for p in compiled["phases"]}
    phase_ids = [p["id"] for p in compiled["phases"]]
    phase_ids += sorted({t["phase"] for t in tasks} - set(phase_ids))
    phase_rows = rows("phase", [
        (p, f"<code>{esc(p)}</code> {esc(phase_names.get(p, 'Phase-independent' if p == 'P-NONE' else ''))}")
        for p in phase_ids
    ])
    module_ids = [m["id"] for m in compiled["modules"]]
    module_names = {m["id"]: m["name"] for m in compiled["modules"]}
    module_ids += sorted({t["module"] for t in tasks if t["module"]} - set(module_ids)) + ["~none"]
    module_rows = rows("module", [
        (m, "No module" if m == "~none" else f"<code>{esc(m)}</code> {esc(module_names.get(m, ''))}")
        for m in module_ids
    ])
    shown, rest = module_rows[:_MODULE_ROWS], module_rows[_MODULE_ROWS:]
    modules = f'<ul class="bars" aria-label="Module">{"".join(shown)}</ul>' + (
        f'<details class="more-rows"><summary>Show {len(rest)} more module{"" if len(rest) == 1 else "s"}'
        f'</summary><ul class="bars" aria-label="More modules">{"".join(rest)}</ul></details>'
        if rest else ""
    )
    return (
        '<section class="breakdown" id="breakdown" aria-labelledby="breakdown-title">'
        '<h2 id="breakdown-title">Breakdown</h2>'
        f'<p class="note">{len(tasks)} tasks. Select a bar or legend entry to filter every chart, '
        "table, and the Needs you panel; select it again to remove it. Bars split by status: "
        "done, in progress, blocked, to do.</p>"
        '<div class="xf-grid">'
        + stack("status", "Status", _STATUS_STACK)
        + stack("domain", "Domain", DOMAINS)
        + stack("validation", "Validation strength", _VALIDATION_STACK)
        + '<figure class="chart half" data-chart="phase"><figcaption>Phase</figcaption>'
        f'<ul class="bars" aria-label="Phase">{"".join(phase_rows)}</ul></figure>'
        f'<figure class="chart half" data-chart="module"><figcaption>Module</figcaption>{modules}</figure>'
        "</div></section>"
    )


def _signal(count: int, label: str, href: str, tone: str, note: str,
            agg: str = "", template: str = "") -> str:
    """One attention count. A zero reads as clear rather than disappearing, so
    the band keeps its shape and its absence of trouble is visible too."""
    return (
        f'<a class="sig {tone if count else "clear"}" href="{href}"{agg}'
        f' data-tone="{tone}" data-agg-n="{count}">'
        f'<span class="sig-n" data-agg-key="n">{count}</span><span class="sig-l">{esc(label)}</span>'
        f'<span class="sig-note"{_text(template or note)}>{esc(note if count else "Clear")}</span></a>'
    )


def _pill(value: object, extra: str = "") -> str:
    classes = f"pill {esc(value)} {extra}".strip()
    return f'<span class="{classes}">{esc(value)}</span>'


def _domain_pill(domain: str) -> str:
    return (
        '<span class="pill domain-operations">Project operations</span>'
        if domain == "operations" else ""
    )


TOP_TABS = (
    ("overview", "Overview"),
    ("assumptions", "Assumptions"),
    ("execution", "Execution"),
    ("graph", "Graph"),
    ("operations", "Operations"),
    ("governance", "Governance"),
    ("decisions", "Decisions"),
    ("tasks", "All Tasks"),
)

# Obstacles in the order a reader acts on them. Dependency blockers come last
# and are split by whether the work they wait on can start now (ADR-044).
_OBSTACLE_GROUPS = (
    ("ACCEPTANCE_BLOCKER", "Acceptance blockers"),
    ("GATE_BLOCKER", "Gate blockers"),
    ("PHASE_BLOCKER", "Phase blockers"),
    ("VALIDATION_GAP", "Review gaps"),
    ("SCHEDULE_BLOCKER", "Schedule blockers"),
)

_EVENT_SECTIONS = (
    ("tool-calls", "Tool calls", ("tool-call", "command")),
    ("mini-actions", "Mini-actions", ("action",)),
    ("mutations", "Changes and mutations", ("mutation",)),
)

# Head script: runs before the stylesheet paints, so the page never flashes
# the wrong theme or the wrong tab. Everything it sets is presentation state.
_BOOT = """(function () {
  var root = document.documentElement, theme = null;
  root.className += ' js';
  try { theme = localStorage.getItem('praukron-theme'); } catch (error) {}
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  root.setAttribute('data-theme', theme);
  var tab = location.hash.slice(1).split('?')[0];
  var known = ['overview', 'assumptions', 'execution', 'graph', 'operations', 'governance', 'decisions', 'tasks'];
  root.setAttribute('data-tab', known.indexOf(tab) >= 0 ? tab : 'overview');
})();"""


def render(project: Project, report: Report, compiled: dict, interactive: bool = False) -> str:
    """Lay out the compiled project. Every selection — what is in flight, what
    blocks, which gate is next, which domain a task belongs to — was made by
    analytics and arrives in `compiled`; nothing here decides it (ADR-045)."""
    metrics = compiled["metrics"]
    diagrams = mermaid.render_all(project, report)
    # The drawing names its nodes by a sanitized identifier. The map back to
    # task identifiers is built here, from the same function that drew them,
    # so the page never has to re-derive the rule (ADR-025).
    node_map = {mermaid.node_id(task.id): task.id for task in project.tasks}
    tasks = {task["id"]: task for task in compiled["tasks"]}
    phases = {phase["id"]: phase for phase in compiled["phases"]}
    execution = compiled["execution"]
    operations = compiled["operations"]
    events = {event["id"]: event for event in operations["events"]}
    current_id = compiled["project"]["currentPhase"]
    current = phases.get(current_id) if current_id else None

    def ref(task_id: str | None) -> str:
        if not task_id:
            return '<span class="note">none</span>'
        task = tasks.get(task_id)
        if task is None:
            return f"<code>{esc(task_id)}</code>"
        return (
            f'<button type="button" class="tasklink{" ops" if task["domain"] == "operations" else ""}"'
            f' data-task="{esc(task_id)}"><code>{esc(task_id)}</code> {esc(task["title"])}</button>'
        )

    def trace_link(task_id: str, label: str = "Open its operations trace") -> str:
        return (
            f'<a class="tracelink" href="#operations" data-focus="op-{esc(task_id)}">'
            f"{esc(label)} →</a>"
        )

    def task_rows(ids: list[str], registry: bool = False) -> str:
        rows = ""
        for task_id in ids:
            task = tasks.get(task_id)
            if task is None:
                continue
            filters = (
                f' data-phase="{esc(task["phase"])}" data-status="{esc(task["status"])}"'
                f' data-validation="{esc(task["validation"])}" data-domain="{esc(task["domain"])}"'
                f' data-text="{esc((task["id"] + " " + task["title"]).lower())}"'
                if registry
                else ""
            )
            rows += (
                f'<tr data-task="{esc(task["id"])}" tabindex="0"{filters}>'
                f'<td><code>{esc(task["id"])}</code></td>'
                f"<td>{esc(task['title'])}</td><td>{esc(task['phase'])}</td>"
                f'<td>{_pill(task["domain"], "domain")}</td>'
                f"<td>{_pill(task['status'])}</td><td>{_pill(task['validation'])}</td></tr>"
            )
        return rows or '<tr><td colspan="6" class="note">Nothing here.</td></tr>'

    table_head = (
        "<thead><tr><th>Task</th><th>Title</th><th>Phase</th><th>Domain</th>"
        "<th>Status</th><th>Validation</th></tr></thead>"
    )

    def event_item(event: dict) -> str:
        meta = []
        if event["task"]:
            meta.append(f"from {ref(event['task'])}")
        for label, key in (("tool", "tool"), ("target", "target"), ("agent", "agent"),
                           ("artifact", "artifact")):
            if event[key]:
                meta.append(f"{label} <code>{esc(event[key])}</code>")
        if event["retryOf"]:
            meta.append(f"retry of <code>{esc(event['retryOf'])}</code>")
        if event["parent"]:
            meta.append(f"under <code>{esc(event['parent'])}</code>")
        affects = " ".join(
            ref(a) if a in tasks else f"<code>{esc(a)}</code>" for a in event["affects"]
        )
        unresolved = event["id"] in operations["unresolvedFailures"]
        return (
            f'<li class="event" id="event-{esc(event["id"])}" data-type="{esc(event["type"])}">'
            f'<div class="evhead"><code>{esc(event["id"])}</code> {_pill(event["type"])} '
            f'{_pill(event["outcome"], {"failure": "FAIL", "success": "PASS"}.get(event["outcome"], ""))}'
            + (' <span class="pill FAIL">unresolved</span>' if unresolved else "")
            + f' <strong>{esc(event["title"])}</strong>'
            + (f' <span class="note">{esc(event["time"])}</span>' if event["time"] else "")
            + "</div>"
            + (f'<div class="evmeta">{" · ".join(meta)}</div>' if meta else "")
            + (f'<div class="everror">{esc(event["error"])}</div>' if event["error"] else "")
            + (f'<div class="evmeta">affects {affects}</div>' if affects else "")
            + (f'<div class="evmeta">evidence {esc(event["evidence"])}</div>' if event["evidence"] else "")
            + "</li>"
        )

    def event_list(items: list[dict], empty: str) -> str:
        if not items:
            return f'<p class="note">{esc(empty)}</p>'
        return f'<ul class="plain events">{"".join(event_item(e) for e in items)}</ul>'

    # --- Overview: the focus strip ------------------------------------------
    # Primary cards describe execution. Operations appears here only when it
    # blocks execution. Future derived signals (attention, risk) belong beside
    # these values; the compiled project carries none today.
    if current:
        phase_card = (
            f'<div class="v"><code>{esc(current["id"])}</code> · {esc(current["name"])}</div>'
            f'<div class="more">{_pill(current["status"])} '
            f'{current["progress"]["done"]} / {current["progress"]["total"]} execution tasks done</div>'
        )
    else:
        phase_card = '<div class="v">No phase is active</div>'

    in_flight = execution["inFlight"]
    ready_execution = [t for t in compiled["ready"] if tasks.get(t, {}).get("domain") == "execution"]
    upcoming = [t for t in compiled["criticalPath"] if t not in in_flight and t in tasks]
    if in_flight:
        active_card = f'<div class="v">{ref(in_flight[0])}</div>'
        if len(in_flight) > 1:
            active_card += f'<div class="more">and {len(in_flight) - 1} more in flight</div>'
    elif ready_execution:
        active_card = (
            '<div class="v">No execution work in flight</div>'
            f'<div class="more">Ready to start: {ref(ready_execution[0])}</div>'
        )
    else:
        active_card = '<div class="v">No execution work in flight</div>'
    if upcoming:
        active_card += f'<div class="more">Next on the critical path: {ref(upcoming[0])}</div>'

    blocker = execution["mainBlocker"]
    if blocker and blocker["kind"] == "TASK":
        blocker_card = (
            f'<div class="v">{ref(blocker["id"])}</div>'
            f'<div class="more">{_domain_pill(blocker["domain"])} {_pill(blocker["status"])} '
            f'blocking {ref(blocker["blocking"])} · {esc(blocker["reason"])}</div>'
            + (f'<div class="more">{trace_link(blocker["id"])}</div>' if blocker["domain"] == "operations" else "")
        )
    elif blocker:
        blocker_card = (
            f'<div class="v">{esc(blocker["id"])}</div>'
            f'<div class="more">{_pill(blocker["status"])} blocking {esc(blocker["blocking"])} exit</div>'
        )
    else:
        blocker_card = '<div class="v">Nothing is blocking execution</div>'
    if blocker:
        blocker_card += (
            f'<div class="more"><strong>To unblock:</strong> {esc(blocker["unblock"])} · '
            f'<strong>Who acts:</strong> {esc(blocker["actor"])}</div>'
        )

    gate = execution["nextGate"]
    if gate:
        gate_card = f'<div class="v">{ref(gate["exitAuthority"])}</div>'
        gate_card += (
            f'<div class="more">Exit authority for {esc(gate["phase"])}'
            + (f' · {_pill(gate["exitAuthorityStatus"])}' if gate["exitAuthorityStatus"] else "")
            + "</div>"
        )
        if gate["gates"]:
            gate_card += '<div class="more">' + " ".join(
                f'{_pill(g["status"])} {esc(g["id"])}' for g in gate["gates"]
            ) + "</div>"
        gate_card += (
            '<div class="more">Ready to exit</div>' if gate["ready"] else
            f'<div class="more">{len(gate["unfinishedExecution"])} execution tasks open'
            + (f' · {len(gate["unmetCriteria"])} exit criteria unmet' if gate["unmetCriteria"] else "")
            + "</div>"
        )
        for external in gate["externalBlockers"]:
            gate_card += (
                f'<div class="more">{_domain_pill(external["domain"])} {ref(external["id"])} '
                f'holds up {esc(external["via"])} · {trace_link(external["id"], "trace")}</div>'
            )
    else:
        gate_card = '<div class="v">No phase exit pending</div>'

    focus = (
        '<div class="focus">'
        f'<div class="card"><div class="k">Current phase</div>{phase_card}</div>'
        f'<div class="card"><div class="k">In flight</div>{active_card}</div>'
        f'<div class="card{" alert" if blocker else ""}"><div class="k">Main blocker</div>{blocker_card}</div>'
        f'<div class="card"><div class="k">Next gate</div>{gate_card}</div>'
        "</div>"
    )

    started = metrics["acceptanceBreakdown"]["started"]
    later = metrics["acceptanceBreakdown"]["notStarted"]
    # Each figure below also carries the tasks it counts, chosen by the same
    # rules analytics used, so a cross-filter recounts rather than re-derives.
    exec_tasks = project.execution_tasks
    exec_ids = [t.id for t in exec_tasks]
    contracts = {c.id: c for c in project.contracts.values() if not c.is_global}

    def criteria(begun: bool, state: str | None = None) -> list:
        # Analytics counts a contract once per group, however many tasks name
        # it. So does this: one entry per contract, naming every task in the
        # group that names it, counted while any of them passes the filters.
        named: dict[str, list[str]] = {}
        for task in exec_tasks:
            if (task.status in ("WIP", "DONE")) == begun and task.contract in contracts:
                named.setdefault(task.contract, []).append(task.id)
        return [
            (ids, sum(1 for c in contracts[contract].criteria if state is None or c.state == state))
            for contract, ids in named.items()
        ]

    def with_validation(*states: str) -> list:
        return _each(t.id for t in exec_tasks if t.validation in states)

    cards = "".join(
        [
            _metric_card("Task completion", metrics["taskCompletion"],
                         agg=_agg(done=_each(t.id for t in exec_tasks if t.done), total=_each(exec_ids))),
            _metric_card(
                "Criteria passed, started work",
                {"done": started["PASS"], "total": sum(started.values()),
                 "fraction": started["PASS"] / sum(started.values()) if sum(started.values()) else 0.0},
                f"{started['FAIL']} fail · {started['NOT_RUN']} not run · "
                f"{sum(later.values())} more in work not started",
                agg=_agg(done=criteria(True, "PASS"), total=criteria(True), fail=criteria(True, "FAIL"),
                         notrun=criteria(True, "NOT_RUN"), later=criteria(False)),
                template="{fail} fail · {notrun} not run · {later} more in work not started",
            ),
            _metric_card(
                "Reviewed or verified tasks", metrics["validationCoverage"],
                f"of all execution tasks · SYNTHETIC {metrics['validationBreakdown'].get('SYNTHETIC', 0)}"
                f" · UNTESTED {metrics['validationBreakdown'].get('UNTESTED', 0)}",
                agg=_agg(done=with_validation(*metrics["validationCounts"]), total=_each(exec_ids),
                         synthetic=with_validation("SYNTHETIC"), untested=with_validation("UNTESTED")),
                template="of all execution tasks · SYNTHETIC {synthetic} · UNTESTED {untested}",
            ),
            _metric_card("Gate readiness", metrics["gateReadiness"]),
            _metric_card("Critical path", metrics["criticalPathCompletion"], agg=_agg(
                done=_each(t for t in compiled["criticalPath"] if project.task(t) and project.task(t).done),
                total=_each(compiled["criticalPath"]))),
        ]
    )

    phase_cards = "".join(
        f'<article class="phase{" current" if phase["id"] == current_id else ""}"'
        + _agg(done=_each(t.id for t in project.execution_in(phase["id"]) if t.done),
               total=_each(t.id for t in project.execution_in(phase["id"])))
        + '>'
        f'<header><strong><code>{esc(phase["id"])}</code> {esc(phase["name"])}</strong> '
        f'{_pill(phase["status"])}'
        + (' <span class="pill current">Current</span>' if phase["id"] == current_id else "")
        + "</header>"
        f'<div class="outcome">{esc(phase["outcome"])}</div>'
        f'<div class="mono"><span data-agg-key="done">{phase["progress"]["done"]}</span> / '
        f'<span data-agg-key="total">{phase["progress"]["total"]}</span> execution tasks'
        + (f' · exit authority {esc(phase["exitAuthority"])}' if phase["exitAuthority"] else "")
        + (f' · exit waits on {esc(phase["waitsOn"])}' if phase.get("waitsOn") else "")
        + f'</div><div class="bar"><i data-agg-bar="done/total" style="width:{round(phase["progress"]["fraction"] * 100)}%"></i></div>'
        "</article>"
        for phase in compiled["phases"]
    ) or '<p class="note">No phases recorded.</p>'

    # --- Product hierarchy (ADR-035) ---------------------------------------
    # Thesis, then phase -> module -> task, exactly as compiled. The
    # relationships come from `compiled`; the page only lays them out.
    thesis = compiled["thesis"]
    modules_by_phase: dict[str, list[dict]] = {}
    for module in compiled["modules"]:
        modules_by_phase.setdefault(module["phase"], []).append(module)

    def module_block(module: dict) -> str:
        items = "".join(f"<li>{_pill(tasks[t]['status'])} {ref(t)}</li>" for t in module["tasks"])
        return (
            f'<div class="module"{_agg(n=_each(module["tasks"]))}><div><code>{esc(module["id"])}</code>'
            f' <strong>{esc(module["name"])}</strong>'
            f' <span class="note" data-agg-text="{{n}} tasks">{len(module["tasks"])} tasks</span></div>'
            f'<div class="outcome">{esc(module["outcome"])}</div>'
            + (f'<ul class="plain xf-list">{items}</ul>' if items else '<p class="note">No tasks yet.</p>')
            + "</div>"
        )

    def phase_group(phase_id: str, label: str, opened: bool) -> str:
        modules = modules_by_phase.get(phase_id, [])
        if not modules:
            return ""
        return (
            f'<details class="group hierarchy-phase" data-phase="{esc(phase_id)}"{" open" if opened else ""}>'
            f"<summary><code>{esc(phase_id)}</code> {esc(label)}"
            f'<span class="count">{len(modules)} modules</span></summary>'
            + "".join(module_block(m) for m in modules)
            + "</details>"
        )

    legacy = [t["id"] for t in compiled["tasks"] if not t["module"]]
    hierarchy = (
        '<div class="box thesis"><div class="k">Product thesis</div>'
        + (f'<p>{esc(thesis["statement"])}</p>' if thesis["statement"]
           else '<p class="note">No product thesis authored in THESIS.md.</p>')
        + (f'<div class="note">Detail: <code>{esc(thesis["reference"])}</code></div>' if thesis["reference"] else "")
        + "</div>"
        + "".join(
            phase_group(phase["id"], phase["name"], phase["id"] == current_id)
            for phase in compiled["phases"]
        )
        + phase_group("P-NONE", "Phase-independent", False)
        + (
            '<details class="group hierarchy-phase" data-phase="legacy"><summary>Legacy tasks with no module'
            f'<span class="count">{len(legacy)}</span></summary><ul class="plain xf-list">'
            + "".join(f"<li>{_pill(tasks[t]['status'])} {ref(t)}</li>" for t in legacy)
            + "</ul></details>"
            if legacy else ""
        )
    )

    external_overview = ""
    if execution["externalBlockers"]:
        external_overview = (
            "<h2>Operations holding up execution</h2><div class=\"box\"><ul class=\"plain\">"
            + "".join(
                f"<li>{ref(task_id)} waits on "
                + ", ".join(f'{_domain_pill("operations")} {ref(o)}' for o in blockers)
                + f' · {trace_link(blockers[0], "trace")}</li>'
                for task_id, blockers in sorted(execution["externalBlockers"].items())
            )
            + "</ul></div>"
        )

    # --- Execution ---------------------------------------------------------
    def obstacle_item(item: dict, offer_guide: bool = False) -> str:
        # What is blocked (the subject), why (the detail), what clears it,
        # and who must act: all four from compiled fields, none invented here.
        return (
            f'<li>{_pill(item["type"])} {ref(item["subject"])}<br>'
            f'<span class="note">{esc(item["detail"])}</span><br>'
            f'<span class="note"><strong>To unblock:</strong> {esc(item.get("unblock", ""))} · '
            f'<strong>Who acts:</strong> {esc(item.get("actor", "agent"))}</span>'
            + ("".join("<br>" + review.guide(h, interactive) for h in item.get("holders", [])) if offer_guide else "")
            + "</li>"
        )

    # Every obstacle only the owner can clear goes to the side panel, not to a
    # tab (ADR-065); the groups below hold the rest.
    owner_held = [o for o in compiled["obstacles"] if o.get("actor") == "owner"]
    execution_obstacles = [o for o in compiled["obstacles"]
                           if o.get("domain", "execution") == "execution" and o not in owner_held]
    blocked = set(compiled["blocked"])
    groups = []
    for kind, label in _OBSTACLE_GROUPS:
        items = [o for o in execution_obstacles if o["type"] == kind]
        if kind == "VALIDATION_GAP":
            # Evidence recorded but nobody reviewed it is not the same as
            # nothing ever exercised; never let one read as the other.
            for variant, name in (("REVIEW_MISSING", "Evidence recorded, review missing"),
                                  ("EVIDENCE_MISSING", "No exercised evidence, no review")):
                chosen = [o for o in items if o.get("variant") == variant]
                if chosen:
                    groups.append((name, chosen, variant == "EVIDENCE_MISSING", ""))
            continue
        if items:
            groups.append((label, items, True, ""))
    dependency = [o for o in execution_obstacles if o["type"] == "DEPENDENCY_BLOCKER"]
    direct = [o for o in dependency if any(b not in blocked for b in o["blockers"])]
    downstream = [o for o in dependency if o not in direct]
    if direct:
        groups.append(("Waiting on work that can move now", direct, True, ""))
    if downstream:
        groups.append((
            "Waiting on work that is itself blocked", downstream, False,
            '<p class="note">Each of these clears once the obstacles above do.</p>',
        ))
    known_kinds = {kind for kind, _ in _OBSTACLE_GROUPS} | {"DEPENDENCY_BLOCKER"}
    other = [o for o in execution_obstacles if o["type"] not in known_kinds]
    if other:
        groups.append(("Other obstacles", other, True, ""))
    obstacles = (
        f'<p class="note">{len(owner_held)} wait{"s" if len(owner_held) == 1 else ""} on the owner, in the '
        '<a href="#needs-you">Needs you</a> panel.</p>' if owner_held else ""
    ) + ("".join(
        f'<details class="group"{" open" if opened else ""}><summary>{esc(label)}'
        f'<span class="count">{len(items)}</span></summary>{note}'
        f'<ul class="plain xf-list">{"".join(obstacle_item(o) for o in items)}</ul></details>'
        for label, items, opened, note in groups
    ) or '<p class="note">No other execution obstacles.</p>')

    critical = "".join(
        f'<li class="{"done" if tasks.get(t, {}).get("status") == "DONE" else ""}">'
        f'{_pill(tasks[t]["status"]) if t in tasks else ""} {ref(t)}'
        + "".join(
            f'<div class="external">external blocker {_domain_pill("operations")} {ref(o)}</div>'
            for o in execution["externalBlockers"].get(t, [])
        )
        + "</li>"
        for t in compiled["criticalPath"]
    )
    critical = (
        f'<ol class="chain">{critical}</ol>' if critical
        else '<p class="note">No open execution work.</p>'
    )

    failures = "".join(
        f"<li>{ref(f['task'])}"
        + (f' · failed criteria {esc(", ".join(f["failedCriteria"]))}' if f["failedCriteria"] else "")
        + (
            '<div class="evmeta">related operational evidence: '
            + ", ".join(
                f'<a class="tracelink" href="#operations" data-focus="event-{esc(e)}"><code>{esc(e)}</code></a>'
                + (f' {_pill(events[e]["type"])}' if e in events else "")
                for e in f["relatedEvents"]
            )
            + "</div>"
            if f["relatedEvents"] else '<div class="evmeta">no operational evidence recorded</div>'
        )
        + "</li>"
        for f in execution["failures"]
    )
    failures_section = (
        f'<h2>Execution failures</h2><div class="box"><ul class="plain xf-list">{failures}</ul></div>'
        if failures else ""
    )

    # --- Graph -------------------------------------------------------------
    diagram_tabs = "".join(
        f'<button data-diagram="{name}" aria-selected="{str(index == 0).lower()}">{label}</button>'
        for index, (name, label) in enumerate(
            [
                ("task-graph.mmd", "Task graph"),
                ("critical-path.mmd", "Critical path"),
                ("phases.mmd", "Phases"),
                ("gates.mmd", "Gates"),
                ("timeline.mmd", "Dependency timeline"),
                ("gantt.mmd", "Calendar Gantt"),
            ]
        )
    )
    scheduled = len(compiled["schedule"]["scheduled"])
    unscheduled = len(compiled["schedule"]["unscheduled"])

    # --- Operations --------------------------------------------------------
    om = operations["metrics"]
    all_events = sorted(operations["events"], key=lambda e: event_order(e["id"], e["time"]))
    affects_execution = set(om["affectingExecution"])
    ops_tasks = project.operations_tasks
    blocked_ids = set(compiled["blocked"])
    unresolved = set(operations["unresolvedFailures"])

    def event_entries(chosen) -> list:
        # An event belongs to the task it came from and the tasks it affects.
        return [([e.task] if e.task else []) + list(e.affects) for e in chosen]

    def events_where(test) -> list:
        return [(ids, 1) for ids in event_entries([e for e in project.events if test(e)])]

    summary = "".join([
        _count_card("Active operations", om["tasks"]["active"], f'{om["tasks"]["open"]} open',
                    agg=_agg(n=_each(t.id for t in ops_tasks if t.status == "WIP"),
                             open=_each(t.id for t in ops_tasks if not t.done)),
                    template="{open} open"),
        _count_card("Blocked operations", om["tasks"]["blocked"], alert=bool(om["tasks"]["blocked"]),
                    agg=_agg(n=_each(t.id for t in ops_tasks if t.id in blocked_ids))),
        _count_card("Affecting execution", len(om["affectingExecution"]),
                    f'{len(om["affectingCriticalPath"])} on the critical path · {len(om["affectingGate"])} on the next gate',
                    alert=bool(om["affectingExecution"]),
                    agg=_agg(n=_each(om["affectingExecution"]), path=_each(om["affectingCriticalPath"]),
                             gate=_each(om["affectingGate"])),
                    template="{path} on the critical path · {gate} on the next gate"),
        _count_card("Events recorded", om["events"]["total"], agg=_agg(n=events_where(lambda e: True))),
        _count_card("Failures", om["events"]["failures"], f'{om["events"]["unresolvedFailures"]} unresolved',
                    alert=bool(om["events"]["unresolvedFailures"]),
                    agg=_agg(n=events_where(lambda e: e.failed),
                             unresolved=events_where(lambda e: e.id in unresolved)),
                    template="{unresolved} unresolved"),
        _count_card("Retries", om["events"]["retries"],
                    agg=_agg(n=events_where(lambda e: e.retry_of or e.type == "retry"))),
        _count_card("Mutations", om["events"]["mutations"],
                    agg=_agg(n=events_where(lambda e: e.type == "mutation"))),
    ])

    def operation_row(task_id: str) -> str:
        task = tasks[task_id]
        blocking = sorted(t for t, blockers in execution["externalBlockers"].items() if task_id in blockers)
        trace = [events[e] for e in task["events"] if e in events]
        latest = max(trace, key=lambda e: event_order(e["id"], e["time"])) if trace else None
        return (
            f'<details class="group op" id="op-{esc(task_id)}"{" open" if blocking else ""}>'
            f'<summary>{_pill(task["status"])} <code>{esc(task_id)}</code> {esc(task["title"])}'
            + (' <span class="pill FAIL">blocks execution</span>' if blocking else "")
            + f'<span class="count">{len(trace)} event{"" if len(trace) == 1 else "s"}</span></summary>'
            f'<div class="opbody"><div class="evmeta">'
            + " · ".join(filter(None, [
                f'owner {esc(task["owner"])}' if task["owner"] else "",
                f'phase {esc(task["phase"])}' if task["phase"] != "P-NONE" else "",
                f'claimed {esc(task["claimed"])}' if task["claimed"] else "",
                f'latest {esc(latest["title"])}' if latest else "",
                f'<button type="button" class="tasklink" data-task="{esc(task_id)}">details</button>',
            ]))
            + "</div>"
            + (f'<div class="evmeta">blocks {" ".join(ref(b) for b in blocking)}</div>' if blocking else "")
            + event_list(trace, "No events recorded for this task.")
            + "</div></details>"
        )

    open_ops = [t for t in operations["tasks"] if tasks[t]["status"] != "DONE"]
    open_ops.sort(key=lambda t: (t not in affects_execution, tasks[t]["status"] != "WIP", operations["tasks"].index(t)))
    done_ops = [t for t in operations["tasks"] if tasks[t]["status"] == "DONE"]
    active_ops = "".join(operation_row(t) for t in open_ops) or '<p class="note">No open operations tasks.</p>'
    if done_ops:
        active_ops += (
            f'<details class="group"><summary>Completed operations<span class="count">{len(done_ops)}</span></summary>'
            + "".join(operation_row(t) for t in done_ops) + "</details>"
        )

    ops_obstacles = [o for o in compiled["obstacles"]
                     if o.get("domain") == "operations" and o not in owner_held]
    failed_events = [e for e in all_events if e["type"] == "failure" or e["outcome"] == "failure"]
    warnings = event_list(failed_events, "No failed events recorded.")
    if ops_obstacles:
        warnings += '<ul class="plain">' + "".join(obstacle_item(o) for o in ops_obstacles) + "</ul>"

    retries = [e for e in all_events if e["retryOf"] or e["type"] == "retry"]
    sections = "".join(
        f"<h2>{esc(label)}</h2>"
        + event_list([e for e in all_events if e["type"] in kinds], f"No {label.lower()} recorded.")
        for key, label, kinds in _EVENT_SECTIONS
    )
    present_types = [t for t in EVENT_TYPES if any(e["type"] == t for e in all_events)]
    timeline_chips = "".join(
        f'<button type="button" data-filter="{esc(v)}" aria-pressed="{str(v == "").lower()}">{esc(l)}</button>'
        for v, l in [("", "All")] + [(t, t) for t in present_types]
    ) if len(present_types) > 1 else ""
    no_trace = (
        '<p class="note">No operational events are recorded. Tool calls, mini-actions, failures, '
        "retries and mutations appear here once agents append them to "
        f'<code>{esc(compiled["project"]["authority"])}/TRACE.md</code>; nothing is reconstructed '
        "from the journal.</p>"
    ) if not all_events else ""

    # --- Governance --------------------------------------------------------
    gate_verifiers = {g["id"]: g for g in (gate["gates"] if gate else [])}
    gates = "".join(
        f'<li>{_pill(g["status"])} <strong>{esc(g["id"])}</strong> — {esc(g["name"])}'
        + (f' <span class="note">blocks {esc(", ".join(g["blocks"]))}</span>' if g["blocks"] else "")
        + f'<br><span class="note">{esc(g["description"])}</span>'
        + "".join(
            f'<div class="evmeta">open verifier {_domain_pill(v["domain"])} {ref(v["id"])}</div>'
            for v in gate_verifiers.get(g["id"], {}).get("openVerifiers", [])
        )
        + "</li>"
        for g in compiled["gates"]
    ) or '<li class="note">No gates recorded.</li>'
    def debt_item(debt: dict) -> str:
        lineage = " · ".join(filter(None, [
            f'introduced by {esc(", ".join(debt["introducedBy"]))}' if debt["introducedBy"] else "",
            "repaid by " + " ".join(ref(t) for t in debt["linkedTasks"]) if debt["linkedTasks"] else "",
        ]))
        return (
            f'<li id="debt-{esc(debt["id"])}"><code>{esc(debt["id"])}</code> {_pill(debt["status"])} '
            + (f'{_pill("trigger " + debt["triggerState"].lower().replace("_", " "), "FAIL" if debt["triggerState"] == "REACHED" else "WIP" if debt["triggerState"] == "APPROACHING" else "")} ')
            + f'<strong>{esc(debt["title"])}</strong>'
            + (f'<div class="evmeta">{esc(debt["debt"])}</div>' if debt["debt"] else "")
            + (f'<div class="evmeta">trigger: {esc(debt["trigger"])}</div>' if debt["trigger"] else "")
            + (f'<div class="evmeta">exit: {esc(debt["exitCondition"])}</div>' if debt["exitCondition"] else "")
            + (f'<div class="evmeta">{lineage}</div>' if lineage else "")
            + "</li>"
        )

    debts = compiled["debt"]
    open_debt = [d for d in debts if d["status"] not in ("RESOLVED", "INVALIDATED")]
    closed_debt = [d for d in debts if d["status"] in ("RESOLVED", "INVALIDATED")]
    debt_section = (
        '<div class="box"><ul class="plain">' + "".join(debt_item(d) for d in open_debt) + "</ul></div>"
        if open_debt else '<p class="note">No open technical debt recorded.</p>'
    ) + (
        f'<details class="group"><summary>Resolved or invalidated<span class="count">{len(closed_debt)}</span></summary>'
        '<ul class="plain" style="padding:0 16px 10px">' + "".join(debt_item(d) for d in closed_debt) + "</ul></details>"
        if closed_debt else ""
    )
    total_tasks = metrics["domainBreakdown"]["execution"]
    # Execution tasks only, as compiled; the cross-filters recount the rows.
    validation_rows = "".join(
        f'<tr{_agg(n=with_validation(state), total=_each(exec_ids))}><td>{_pill(state)}</td>'
        f'<td class="num" data-agg-key="n">{count}</td>'
        f'<td class="num" data-agg-pct="n/total">{_percent(count, total_tasks)}%</td>'
        f'<td style="width:40%"><div class="bar"><i data-agg-bar="n/total" style="width:{(count / total_tasks * 100) if total_tasks else 0:.1f}%"></i></div></td></tr>'
        for state, count in sorted(metrics["validationBreakdown"].items())
    )

    # --- Decisions ---------------------------------------------------------
    all_decisions = compiled["decisions"]
    superseded_by: dict[str, list[str]] = {}
    for decision in all_decisions:
        for earlier in decision["supersedes"]:
            superseded_by.setdefault(earlier, []).append(decision["id"])
    reconstructed_count = sum(1 for d in all_decisions if d["origin"] == "RECONSTRUCTED")

    # A reconstructed decision was inferred after the fact (ADR-037). It is
    # labelled wherever it appears, so it is never read as a record of a
    # decision the project watched being made.
    def decision_item(decision: dict) -> str:
        reconstructed = decision["origin"] == "RECONSTRUCTED"
        detail = []
        if decision["supersedes"]:
            detail.append(("Supersedes", ", ".join(decision["supersedes"])))
        if superseded_by.get(decision["id"]):
            detail.append(("Superseded by", ", ".join(superseded_by[decision["id"]])))
        if decision["affects"]:
            detail.append(("Affects", ", ".join(decision["affects"])))
        if reconstructed:
            detail.append(("Inferred from", decision["evidence"] or "nothing cited"))
        detail.append((
            "Source", f'{compiled["project"]["authority"]}/{decision["source"]["file"]}'
        ))
        body = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in detail)
        return (
            f'<details class="adr" data-status="{esc(decision["status"])}"'
            f' data-origin="{esc(decision["origin"])}"'
            f' data-text="{esc((decision["id"] + " " + decision["title"]).lower())}">'
            f'<summary><code>{esc(decision["id"])}</code> {_pill(decision["status"])}'
            + (' <span class="pill RECONSTRUCTED">RECONSTRUCTED</span>' if reconstructed else "")
            + f" {esc(decision['title'])}</summary><dl>{body}</dl></details>"
        )

    decision_items = "".join(decision_item(d) for d in all_decisions)
    statuses = sorted({d["status"] for d in all_decisions})
    chip_values = [("", "All")] + [(s, s) for s in statuses]
    if reconstructed_count:
        chip_values.append(("reconstructed", "Reconstructed"))
    decision_chips = "".join(
        f'<button type="button" data-filter="{esc(value)}" aria-pressed="{str(value == "").lower()}">'
        f"{esc(label)}</button>"
        for value, label in chip_values
    ) if len(chip_values) > 2 else ""
    decision_summary = (
        f'{len(all_decisions)} {"decision" if len(all_decisions) == 1 else "decisions"}'
        + (f" · {reconstructed_count} reconstructed" if reconstructed_count else "")
    )

    # --- All tasks ---------------------------------------------------------
    phase_order = [p["id"] for p in compiled["phases"]]
    present_phases = sorted(
        {t["phase"] for t in compiled["tasks"]},
        key=lambda p: (phase_order.index(p) if p in phase_order else len(phase_order), p),
    )
    present_statuses = [s for s in STATUSES if any(t["status"] == s for t in compiled["tasks"])]
    present_validations = [
        v for v in VALIDATIONS if any(t["validation"] == v for t in compiled["tasks"])
    ]
    present_domains = [d for d in DOMAINS if any(t["domain"] == d for t in compiled["tasks"])]
    total_all = len(compiled["tasks"])

    def select(element_id: str, label: str, values: list[str]) -> str:
        options = "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values)
        return (
            f'<select id="{element_id}" aria-label="{label}">'
            f'<option value="">{label}: all</option>{options}</select>'
        )

    tab_buttons = "".join(
        f'<button type="button" role="tab" id="tab-{name}" data-tab="{name}"'
        f' aria-controls="panel-{name}" aria-selected="{str(name == "overview").lower()}"'
        f' tabindex="{0 if name == "overview" else -1}">{label}</button>'
        for name, label in TOP_TABS
    )

    def panel(name: str, body: str) -> str:
        return (
            f'<section class="panel" id="panel-{name}" role="tabpanel"'
            f' aria-labelledby="tab-{name}" tabindex="0">{body}</section>'
        )

    exec_in_flight = in_flight
    assumptions_body, queue, queued, awaiting_agent = review.panel_body(compiled, ref, interactive)
    waiting = len(owner_held) + queued

    # --- Attention: what to act on, before any progress figure -------------
    review_gaps = [o for o in execution_obstacles if o["type"] == "VALIDATION_GAP"]
    open_risk = [d for d in compiled["debt"] if d["status"] not in ("RESOLVED", "INVALIDATED")
                 and d["triggerState"] in ("REACHED", "APPROACHING")]
    reached = sum(1 for d in open_risk if d["triggerState"] == "REACHED")
    events_by_id = {e.id: e for e in project.events}
    attention = (
        '<h2 class="sr">Needs attention</h2><div class="attention">'
        + _signal(len(compiled["blocked"]), "Blocked tasks", "#execution", "bad",
                  "waiting on unfinished dependencies", agg=_agg(n=_each(compiled["blocked"])))
        + _signal(len(execution["failures"]), "Failing work", "#execution", "bad",
                  f'{started["FAIL"]} criteria fail' if started["FAIL"] else "failed operational evidence",
                  agg=_agg(n=_each(f["task"] for f in execution["failures"]), fail=criteria(True, "FAIL")),
                  template="{fail} criteria fail" if started["FAIL"] else "")
        + _signal(waiting, "Needs you", "#needs-you", "warn",
                  f"{queued} to review · {len(owner_held)} held by you",
                  agg=' data-agg-from="needs"', template="{review} to review · {held} held by you")
        + _signal(len(review_gaps), "Review gaps", "#execution", "warn",
                  "done without a recorded review", agg=_agg(n=_each(o["subject"] for o in review_gaps)))
        + _signal(len(open_risk), "Debt at its trigger", "#governance", "bad" if reached else "warn",
                  f"{reached} reached · {len(open_risk) - reached} approaching",
                  agg=_agg(n=[(d["linkedTasks"], 1) for d in open_risk],
                           reached=[(d["linkedTasks"], 1) for d in open_risk if d["triggerState"] == "REACHED"],
                           approaching=[(d["linkedTasks"], 1) for d in open_risk if d["triggerState"] != "REACHED"]),
                  template="{reached} reached · {approaching} approaching")
        + _signal(om["events"]["unresolvedFailures"], "Unresolved failures", "#operations", "bad",
                  "operational events not yet retried or explained",
                  agg=_agg(n=[(ids, 1) for ids in event_entries(
                      [events_by_id[e] for e in operations["unresolvedFailures"] if e in events_by_id])]))
        + "</div>"
    )
    ahead = execution.get("workAhead")
    work_ahead = (
        '<div class="box" id="work-ahead"><strong>Work ahead of phase closure</strong> '
        f'<span class="note">{ahead["tasks"]} done execution task{"" if ahead["tasks"] == 1 else "s"} '
        f'in phases whose predecessor has not exited. Oldest open exit: '
        f'{esc(ahead["oldestOpenExit"]["phase"])}'
        + (f' ({ref(ahead["oldestOpenExit"]["exitAuthority"])})' if ahead["oldestOpenExit"]["exitAuthority"] else "")
        + ". " + " · ".join(f"{esc(k)} {v}" for k, v in ahead["phases"].items())
        + ". A description, not a judgment: the phase rules are unchanged.</span></div>"
        if ahead else ""
    )
    overview = panel("overview", f"""
  {attention}
  {focus}
  {external_overview}
  {work_ahead}
  {_breakdown(compiled)}
  <h2>Execution progress</h2>
  <div class="grid">{cards}</div>
  <h2>Phases</h2>
  <div class="phases">{phase_cards}</div>
  <h2>Product hierarchy</h2>
  <div class="hierarchy">{hierarchy}</div>
""")
    execution_panel = panel("execution", f"""
  <h2>In flight</h2>
  <div class="tablewrap"><table>{table_head}<tbody>{task_rows(exec_in_flight)}</tbody></table></div>
  <h2>Ready</h2>
  <div class="tablewrap"><table>{table_head}<tbody>{task_rows(ready_execution)}</tbody></table></div>
  <h2>Obstacles</h2>
  {obstacles}
  <h2>Owner guidance</h2>
  {review.guidance(compiled, ref)}
  {failures_section}
  <h2>Critical path</h2>
  {critical}
""")
    graph = panel("graph", f"""
  <h2>Views</h2>
  <div class="tabs">{diagram_tabs}</div>
  <div class="diagram">
    <div class="canvas" id="canvas">
      <div class="stage" id="stage"><div class="mermaid" id="diagram">{esc(diagrams["task-graph.mmd"])}</div></div>
      <div class="zoom">
        <button type="button" data-zoom="out" aria-label="Zoom out">&minus;</button>
        <span class="level" id="zoom-level">100%</span>
        <button type="button" data-zoom="in" aria-label="Zoom in">+</button>
        <button type="button" class="wide" data-zoom="fit">Fit</button>
      </div>
      <div class="trace" id="trace-note"></div>
    </div>
  </div>
  <p class="note" id="offline-note" hidden>
    Mermaid could not be loaded, so diagram source is shown instead. The page and
    every number on it work offline.
  </p>
  <p class="note">Dashed nodes in the task graph are project operations; everything else is execution.</p>
  <h2>Schedule</h2>
  <p class="note">
    {scheduled} {"task carries" if scheduled == 1 else "tasks carry"} real schedule
    metadata; {unscheduled} {"is" if unscheduled == 1 else "are"} unscheduled. Dependency
    ordering is shown separately from calendar dates, and no duration is inferred.
  </p>
""")
    operations_panel = panel("operations", f"""
  <p class="note lead">Operations maintains and inspects the environment execution happens in. It
  never counts as project progress, and it stays reviewable because it can explain why execution
  is blocked, failing, or wrong.</p>
  <h2>Summary</h2>
  <div class="grid">{summary}</div>
  {no_trace}
  <h2>Active operations</h2>
  {active_ops}
  <h2>Failures and warnings</h2>
  {warnings}
  {sections}
  <h2>Retries</h2>
  {event_list(retries, "No retries recorded.")}
  <h2>Timeline</h2>
  <div class="controls">
    <div class="chips" id="timeline-chips" role="group" aria-label="Filter timeline">{timeline_chips}</div>
    <span class="shown" id="timeline-shown" aria-live="polite">{len(all_events)} events</span>
  </div>
  <div class="box" id="timeline">{event_list(all_events, "No events recorded.")}</div>
""")
    governance = panel("governance", f"""
  <h2>Gates</h2>
  <div class="box"><ul class="plain">{gates}</ul></div>
  <h2>Validation</h2>
  <div class="tablewrap"><table><thead><tr><th>Strength</th><th>Tasks</th><th>Share</th><th></th></tr></thead>
  <tbody>{validation_rows}</tbody></table></div>
  <p class="note">Execution tasks only. Validation strength records review, not testing: UNTESTED means no review was recorded, even when criteria hold TEST or RUNTIME evidence. Completion is separate.</p>
  <h2>Technical debt</h2>
  {debt_section}
""")
    decisions = panel("decisions", f"""
  <h2>Decisions</h2>
  <div class="controls">
    <input type="search" id="decision-search" placeholder="Search decision ID or title…" aria-label="Search decisions">
    <div class="chips" id="decision-chips" role="group" aria-label="Filter decisions">{decision_chips}</div>
    <span class="shown" id="decision-shown" aria-live="polite">{decision_summary}</span>
  </div>
  <div class="box" id="decision-list">{decision_items or '<p class="note">No decisions recorded.</p>'}</div>
""")
    assumptions_panel = panel("assumptions", assumptions_body)
    needs = (
        f'<aside class="needs" id="needs-you" aria-labelledby="needs-title" data-waiting="{waiting}">'
        '<div class="needs-head"><h2 id="needs-title">Needs you</h2>'
        f'<span class="needcount{"" if waiting else " zero"}">{waiting}</span>'
        '<span class="live">Answering live</span></div>'
        '<p class="note">Everything waiting on a person, whichever tab is open.</p>'
        '<p class="xf-needs" id="needs-filtered" hidden><span></span> '
        '<button type="button" class="linkbtn" data-xf-clear>Show all</button></p>'
        + (review.review_bar(True) + '<p class="note">Each card starts at OK. A card you open or mark '
           "reviewed is recorded as confirmed when you submit; a card you never open records nothing. "
           "Feedback is recorded verbatim with your name.</p>" if interactive and waiting else "")
        + ('<p class="respond-hint">This page is read-only. To answer, run '
           "<code>praukron dashboard --serve</code> and open the link it prints.</p>"
           if waiting and not interactive else "")
        + (f"<h3>Assumptions to review</h3>{queue}" if queue else "")
        + (
            "<h3>Waiting on the owner</h3>"
            '<p class="note">Only a named person can finish the work these wait on.</p>'
            f'<ul class="plain owner-list xf-list">{"".join(obstacle_item(o, True) for o in owner_held)}</ul>'
            if owner_held else ""
        )
        + ("" if waiting else '<p class="note">Nothing waits for you.</p>')
        + (f'<p class="note">{awaiting_agent} answered, waiting for the agent: '
           '<a href="#assumptions">Assumptions</a>.</p>' if awaiting_agent else "")
        + "</aside>"
    )
    registry = panel("tasks", f"""
  <h2>All tasks</h2>
  <div class="controls">
    <input type="search" id="task-search" placeholder="Search task ID or title…" aria-label="Search tasks">
    {select("task-domain", "Domain", present_domains)}
    {select("task-phase", "Phase", present_phases)}
    {select("task-status", "Status", present_statuses)}
    {select("task-validation", "Validation", present_validations)}
    <span class="shown" id="task-shown" aria-live="polite">{total_all} of {total_all}</span>
  </div>
  <div class="tablewrap tall"><table id="task-registry">{table_head}
  <tbody>{task_rows([t.id for t in project.tasks], registry=True)}</tbody></table></div>
""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(project.name)} — project state</title>
<script>{_BOOT}</script>
<style>{_STYLE}{review.STYLE}</style>
</head>
<body>
<main>
  <header class="top">
    <div>
      <h1>{esc(project.name)}</h1>
      <p class="sub">
        Current phase <strong>{esc(compiled["project"]["currentPhase"] or "none")}</strong> ·
        {metrics["taskCompletion"]["done"]} of {metrics["taskCompletion"]["total"]} execution tasks done ·
        compiled from <code>{esc(compiled["project"]["authority"])}/</code>
      </p>
    </div>
    <div class="theme" role="group" aria-label="Theme">
      <button type="button" data-theme-choice="system" aria-pressed="false">System</button>
      <button type="button" data-theme-choice="light" aria-pressed="false">Light</button>
      <button type="button" data-theme-choice="dark" aria-pressed="false">Dark</button>
    </div>
  </header>

  <div class="layout">
  <div class="content">
  <div class="stickyhead">
  <nav class="toptabs" role="tablist" aria-label="Report sections">{tab_buttons}</nav>
  <div class="xfbar" id="xf-bar" role="region" aria-label="Active filters" hidden>
    <span class="xf-label">Filtered by</span><ul class="xf-chips" id="xf-chips"></ul>
    <button type="button" class="linkbtn" data-xf-clear>Clear all</button>
    <span class="xf-count" id="xf-count"></span>
  </div>
  </div>
  <p class="sr" id="xf-status" aria-live="polite"></p>
{overview}
{assumptions_panel}
{execution_panel}
{graph}
{operations_panel}
{governance}
{decisions}
{registry}
  </div>
  {needs}
  </div>
  <footer>
    Generated by <code>praukron dashboard</code>. This page is derived and read-only:
    it reports authority and cannot change it. Edit
    <code>{esc(compiled["project"]["authority"])}/</code> and compile again.
  </footer>
</main>

<dialog id="detail"><button class="close" aria-label="Close"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/></svg></button><div class="body"></div></dialog>

<script type="application/json" id="project-data">{embed(compiled)}</script>
<script type="application/json" id="diagram-data">{embed(diagrams)}</script>
<script type="application/json" id="node-map">{embed(node_map)}</script>
<script src="{MERMAID_CDN}" onerror="window.mermaidFailed=true"></script>
<script>{FILTER_JS}{_SCRIPT}{review.script(interactive)}</script>
</body>
</html>
"""
