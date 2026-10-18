"""Static dashboard generation.

One self-contained HTML file. The compiled project, offline dependency drawing
and diagram sources are embedded. Nothing fetches an external resource.

The dashboard is read-only by construction: it carries no control that writes.
Its tabs, theme, and cross-filters are presentation state kept in the URL hash
and localStorage; none of it reaches authority (ADR-044).
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

from .analytics import Report
from .model import DOMAINS, EVENT_TYPES, STATUSES, VALIDATIONS, Project, event_order
from . import drawings, mermaid, review


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
.layout { display: block; }
.content { min-width: 0; }
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
html.js[data-tab="home"] #panel-home,
html.js[data-tab="work"] #panel-work,
html.js[data-tab="review"] #panel-review,
html.js[data-tab="audit"] #panel-audit { display: block; }
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
      <dt>Full evidence</dt><dd><a href="#task-record-${esc(id)}" data-record="task-record-${esc(id)}">Open task record in Audit</a></dd>
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
  if (opener) { event.preventDefault(); showTask(opener.dataset.task); }
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
document.addEventListener('click', event => {
  const link = event.target.closest('[data-record]');
  if (!link) return;
  event.preventDefault();
  document.getElementById('detail').close();
  reveal(document.getElementById(link.dataset.record));
});

// Native drawings and their sources are already in the document.
const diagramTabs = [...document.querySelectorAll('[data-diagram]')];
function selectDiagram(button) {
  diagramTabs.forEach(tab => {
    const selected = tab === button;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute('aria-controls')).classList.toggle('is-selected', selected);
  });
}
diagramTabs.forEach((button, index) => {
  button.addEventListener('click', () => selectDiagram(button));
  button.addEventListener('keydown', event => {
    const next = event.key === 'ArrowRight' ? (index + 1) % diagramTabs.length :
      event.key === 'ArrowLeft' ? (index + diagramTabs.length - 1) % diagramTabs.length :
      event.key === 'Home' ? 0 : event.key === 'End' ? diagramTabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    selectDiagram(diagramTabs[next]);
    diagramTabs[next].focus();
  });
});

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
  const name = document.documentElement.dataset.tab || 'home';
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

const TOP = ['home', 'work', 'review', 'audit'];
const LEGACY = { overview: 'home', assumptions: 'review', execution: 'work', graph: 'work',
                 operations: 'audit', governance: 'audit', decisions: 'audit', tasks: 'work' };
const topTabs = [...document.querySelectorAll('.toptabs [role="tab"]')];

function selectTab(name, { focus = false, record = true } = {}) {
  name = name.replace(/^panel-/, '');
  const old = name;
  name = LEGACY[name] || name;
  if (!TOP.includes(name)) name = 'home';
  document.documentElement.dataset.tab = name;
  topTabs.forEach(tab => {
    const on = tab.dataset.tab === name;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    if (on && focus) tab.focus();
  });
  if (record && location.hash !== '#' + hashFor(name)) history.replaceState(null, '', '#' + hashFor(name));
  if (record) window.scrollTo(0, 0);
  const legacy = document.getElementById('panel-' + old);
  if (legacy && legacy.tagName === 'DETAILS') legacy.open = true;
}

topTabs.forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
document.querySelector('.toptabs').addEventListener('keydown', event => {
  const ordered = [...topTabs].sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  const at = ordered.findIndex(tab => tab.dataset.tab === document.documentElement.dataset.tab);
  const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
  let next = null;
  if (step) next = (at + step + topTabs.length) % topTabs.length;
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = topTabs.length - 1;
  if (next === null) return;
  event.preventDefault();
  selectTab(ordered[next].dataset.tab, { focus: true });
});
addEventListener('hashchange', () => {
  // A link to a card (#assumption-A-1, #needs-you) moves to it without
  // switching tabs; only a tab name selects a tab.
  const [name, query] = location.hash.slice(1).replace(/^panel-/, '').split('?');
  if (TOP.includes(name) || LEGACY[name]) {
    readFilters(query);
    applyFilters({ record: false });
    return selectTab(name, { record: false });
  }
  const target = document.getElementById(decodeURIComponent(name));
  if (!target) return;
  reveal(target);
  // The anchor has done its work; the address goes back to tab and filters,
  // so a reload or a shared link keeps what the reader is looking at.
  if (filtering()) writeHash();
});
selectTab(location.hash.slice(1).split('?')[0] || 'home', { record: false });

// A link to a tab switches to it and keeps the active filters; following the
// bare href would replace the hash and drop them.
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]:not([data-focus])');
  const name = link && link.getAttribute('href').slice(1).replace(/^panel-/, '');
  if (!name || !(TOP.includes(name) || LEGACY[name])) return;
  event.preventDefault();
  selectTab(name);
  document.getElementById('panel-' + (LEGACY[name] || name)).focus();
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
  selectTab('audit');
  const target = document.getElementById(link.dataset.focus);
  if (!target) return;
  reveal(target);
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
    ("home", "Home"), ("work", "Work"), ("review", "Review"), ("audit", "Audit"),
)

def _progress_chart(title: str, rows: list[tuple[str, int, int]], *, status: bool = False) -> str:
    """Labelled bars and their exact counts share one readable table."""
    if not rows:
        return f'<p class="note">{esc(title)}: nothing recorded.</p>'
    body = []
    for label, count, total in rows:
        percent = _percent(count, total)
        description = (f'{label}: {count} of {total} execution tasks' if status else
                       f'{label}: {count} of {total} tasks complete') if total else f'{label}: no tasks'
        body.append(
            f'<tr><th scope="row">{esc(label)}</th><td class="progress-value">{count} / {total}</td>'
            f'<td class="progress-mark"><svg viewBox="0 0 240 12" preserveAspectRatio="none" '
            f'role="img" aria-label="{esc(description)}"><title>{esc(description)}</title>'
            '<rect class="progress-track" x="0.5" y="0.5" width="239" height="11"/>'
            + (f'<rect class="progress-fill" x="1" y="1" width="{238 * count / total:.2f}" height="10"/>' if total and count else '')
            + f'</svg><span class="note">{str(percent) + "%" if total else "No tasks"}</span></td></tr>')
    return ('<table class="progress-chart"><caption>' + esc(title) + '</caption>'
            '<thead><tr><th scope="col">' + ('State' if status else 'Scope') + '</th>'
            '<th scope="col">' + ('Tasks / total' if status else 'Done / total') + '</th>'
            '<th scope="col">' + ('Share' if status else 'Completion') + '</th></tr></thead>'
            '<tbody>' + ''.join(body) + '</tbody></table>')


def _home_progress(compiled: dict) -> tuple[str, str]:
    """Execution progress is never combined with operations or validation."""
    tasks = compiled["tasks"]
    current_id = compiled["project"]["currentPhase"]
    current = next((p for p in compiled["phases"] if p["id"] == current_id), None)

    def completion(label: str, selected: list[dict]) -> tuple[str, int, int]:
        return label, sum(t["status"] == "DONE" for t in selected), len(selected)

    module_rows = []
    for module in compiled["modules"] if current else []:
        if module["phase"] != current_id:
            continue
        selected = [t for t in tasks if t["module"] == module["id"]]
        label = f'{module["id"]} — {module["name"]}'
        module_rows.append(completion(label + ' · execution', [t for t in selected if t["domain"] == "execution"]))
        operations = [t for t in selected if t["domain"] == "operations"]
        if operations:
            module_rows.append(completion(label + ' · operations', operations))
    phase_heading = (f'<h2 id="current-phase-title">{esc(current["id"])} — {esc(current["name"])}</h2>'
                     f'<p class="phase-outcome">{esc(current["outcome"] or "No phase outcome recorded.")}</p>') if current else (
                         '<h2 id="current-phase-title">No current phase</h2><p>No phase is active.</p>')
    modules = (_progress_chart('Modules in this phase', module_rows) if module_rows else
               '<p class="note">No modules recorded for this phase.</p>' if current else '')
    overview = (f'<section class="phase-overview" aria-labelledby="current-phase-title">'
                f'<div>{phase_heading}</div><div>{modules}</div></section>')
    execution = [t for t in tasks if t["domain"] == "execution"]
    phase_rows = [completion(f'{p["id"]} — {p["name"]}', [t for t in execution if t["phase"] == p["id"]])
                  for p in compiled["phases"]]
    phase_ids = {p["id"] for p in compiled["phases"]}
    unphased = [t for t in execution if t["phase"] not in phase_ids]
    if unphased:
        phase_rows.append(completion('No phase', unphased))
    execution_ids = {t["id"] for t in execution}
    states = [(state, len(execution_ids.intersection(ids)), len(execution))
              for state, ids in compiled["mission"]["states"].items()]
    charts = ('<section class="home-progress" aria-label="Execution progress">'
              + _progress_chart('Execution completion by phase', phase_rows)
              + _progress_chart('Execution status breakdown', states, status=True) + '</section>')
    return overview, charts


_MISSION_STYLE = """
.secondary { padding: 0 16px 16px; }
.secondary > summary { margin: 0 -16px; }
.secondary .box, .secondary .phase, .secondary .module, .secondary .card,
.group .group { box-shadow: none; border: 0; border-radius: 0; background: transparent; }
.group .group { border-top: 1px solid var(--line); }
#needs-you .acard { box-shadow: none; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; background: transparent; }
.diagram { padding: 12px; }
.pulse { display: grid; grid-template-columns: 1.4fr 1fr; gap: 32px; padding: 24px 0; }
.pulse h2 { font-size: 24px; margin: 0 0 12px; }
.pulse p { max-width: 70ch; }
.pulse-facts { display: grid; grid-template-columns: max-content 1fr; gap: 10px 20px; margin: 0; }
.pulse-facts dt { color: var(--muted); }
.pulse-facts dd { margin: 0; }
.phase-overview { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 40px; padding: 24px 0 28px; border-bottom: 1px solid var(--line); }
.phase-overview h2 { font-size: 28px; line-height: 1.25; margin: 0 0 12px; overflow-wrap: anywhere; }
.phase-outcome { max-width: 65ch; margin: 0; }
.home-progress { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 32px; margin: 28px 0; }
.progress-chart { width: 100%; table-layout: fixed; border-collapse: collapse; }
.progress-chart caption { text-align: left; font-size: 18px; font-weight: 650; padding-bottom: 12px; }
.progress-chart th, .progress-chart td { padding: 10px 8px; overflow-wrap: anywhere; min-width: 0; }
.progress-chart th { white-space: normal; }
.progress-chart th:first-child { width: 48%; }
.progress-chart thead { font-size: 12px; color: var(--muted); }
.progress-chart tbody th { font-size: 14px; font-weight: 500; text-align: left; color: var(--ink); background: transparent; }
.progress-value { white-space: nowrap; font-variant-numeric: tabular-nums; }
.progress-mark svg { display: block; width: 100%; height: 12px; margin-bottom: 4px; }
.progress-mark .note { font-variant-numeric: tabular-nums; }
.progress-track { fill: var(--sunken); stroke: var(--line-strong); }
.progress-fill { fill: var(--accent); }
.next-action { padding: 20px 0; border-bottom: 1px solid var(--line); }
.next-action h2 { margin: 0 0 12px; }
.next-action .primary-link { display: inline-flex; align-items: center; min-height: 44px; color: var(--accent); font-weight: 650; }
.pulse-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
.root-list, .work-list { list-style: none; padding: 0; }
.root-list > li, .work-list > li { padding: 12px 0; border-bottom: 1px solid var(--line); }
.review-item { border-block-end: 1px solid var(--line); }
#needs-you .review-item > summary, #needs-you .acard > summary { cursor: pointer; padding: 16px 0; display: grid; grid-template-columns: 1fr auto; gap: 6px 24px; list-style: none; }
#needs-you summary::-webkit-details-marker { display: none; }
.review-title { font-size: 16px; line-height: 1.4; }
.review-why, .review-target { grid-column: 1; color: var(--muted); font-size: 13px; }
.review-action { grid-column: 2; grid-row: 1 / span 3; align-self: center; color: var(--accent); font-size: 14px; font-weight: 600; text-decoration: underline; text-underline-offset: 3px; }
.review-action::after { content: " +"; }
details[open] > summary .review-action::after { content: " −"; }
.review-group { margin-top: 28px; }
.review-group > h2 { margin-bottom: 4px; }
.review-empty { margin: 24px 0; color: var(--muted); }
.review-empty .count { margin-inline: 8px; }
.review-body, .abody { overflow-wrap: anywhere; }
.review-body { padding: 0 16px 16px; }
#needs-you { max-width: 1000px; }
#needs-you .review-bar { position: sticky; top: 60px; z-index: 3; background: var(--panel); padding: 12px; }
.needs-head { display: flex; align-items: center; gap: 12px; }
.needs-head .live { display: none; }
.is-live .needs-head .live { display: inline; color: var(--accent); }
.work-state { margin: 12px 0; }
.work-state > summary { cursor: pointer; padding: 12px 0; font-weight: 650; }
.work-state li { padding: 8px 0; }
.work-scope { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }
.work-scope button, #search-open, #search-close { min-height: 44px; padding: 8px 14px; border: 1px solid var(--line-strong); border-radius: var(--radius-s); background: var(--panel); cursor: pointer; }
.work-scope button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.scope-hidden { display: none; }
.diagram-tabs { display: none; }
html.js .diagram-tabs { display: flex; }
.diagram-tabs button { min-height: 44px; }
html.js .native-view:not(.is-selected) { display: none; }
.native-view { min-width: 0; max-width: 100%; }
.native-drawing, .diagram-source pre { overflow: auto; max-width: 100%; max-height: 560px; }
.native-drawing { background: var(--panel); scrollbar-color: var(--line-strong) var(--panel); }
.native-drawing svg { display: block; max-width: none; }
.native-drawing text { fill: var(--ink); font: 14px system-ui, sans-serif; }
.native-drawing .drawing-id { font: 600 14px ui-monospace, monospace; }
.native-drawing .drawing-status { fill: var(--muted); font-size: 12px; }
.native-drawing .drawing-node rect { fill: var(--panel-raised); stroke: var(--mark); stroke-width: 2; }
.native-drawing .drawing-edge { fill: none; stroke: var(--muted); stroke-width: 1.5; }
.native-drawing .arrow-head { fill: var(--muted); }
.native-drawing .drawing-grid { fill: none; stroke: var(--line); }
.native-drawing .calendar-bar { fill: var(--mark); }
.tone-good { --mark: var(--good); } .tone-wip { --mark: var(--wip); }
.tone-bad { --mark: var(--bad); } .tone-warn { --mark: var(--warn); }
.tone-todo { --mark: var(--todo); }
.diagram-source { margin-top: 16px; }
.diagram-source summary { cursor: pointer; padding: 10px 0; }
.diagram-source pre { padding: 16px; background: var(--panel-raised); font-size: 13px; }
.ordering-bands { display: flex; width: max-content; list-style: none; margin: 0; padding: 20px; gap: 32px; }
.ordering-bands > li { width: 240px; }
.ordering-bands h3 { margin: 0 0 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line-strong); }
.ordering-bands ul { list-style: none; padding: 0; }
.ordering-bands ul li { padding: 12px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.ordering-bands span { display: block; margin-top: 4px; }
.ordering-bands .drawing-status { color: var(--muted); font-size: 12px; }
.offline-graph { overflow: auto; max-height: 560px; background: var(--panel); }
.offline-graph svg { display: block; }
.offline-graph rect { fill: var(--panel-raised); stroke: var(--line-strong); }
.offline-graph text { fill: var(--ink); font: 13px ui-monospace, monospace; }
.offline-graph path { fill: none; stroke: var(--muted); }
.offline-graph a:hover rect, .offline-graph a:focus rect { stroke: var(--accent); stroke-width: 3; }
.search-control { display: none; gap: 12px; align-items: center; }
html.js .search-control { display: inline-flex; }
#global-search { width: min(720px, calc(100vw - 32px)); padding: 20px; }
#global-search::backdrop { background: rgb(0 0 0 / .5); }
.search-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.search-head h2 { margin: 0; }
#global-query { width: 100%; box-sizing: border-box; margin-top: 16px; min-height: 44px; font: inherit; padding: 10px; border: 1px solid var(--line-strong); border-radius: var(--radius-s); color: var(--ink); background: var(--panel); }
#search-results { list-style: none; padding: 0; max-height: 55vh; overflow: auto; }
#search-results li { border-bottom: 1px solid var(--line); padding: 10px 0; overflow-wrap: anywhere; }
#search-results a { display: block; padding: 8px 4px; }
.search-source { font-size: 12px; color: var(--muted); }
.record-body { white-space: pre-wrap; overflow-wrap: anywhere; max-width: 75ch; }
.primary-link:focus-visible, summary:focus-visible, a:focus-visible, button:focus-visible, input:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
::selection { background: var(--accent); color: var(--on-accent); }
input, textarea { caret-color: var(--accent); }
@media (max-width: 640px) {
  main { padding: 12px 12px 80px; }
  .stickyhead { margin-inline: -12px; padding-inline: 12px; }
  .pulse, .pulse-bottom { grid-template-columns: 1fr; gap: 20px; }
  .phase-overview, .home-progress { grid-template-columns: 1fr; gap: 20px; }
  .phase-overview { padding-top: 16px; }
  .phase-overview h2 { font-size: 24px; }
  .progress-chart thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .progress-chart tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; padding: 8px 0; border-bottom: 1px solid var(--line); }
  .progress-chart th:first-child { width: auto; }
  .progress-chart th, .progress-chart td { border: 0; padding: 4px 0; }
  .progress-chart .progress-value { padding-left: 12px; }
  .progress-mark { grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; }
  .progress-mark svg { flex: 1; min-width: 0; margin: 0; }
  .progress-mark .note { min-width: 4em; text-align: right; }
  #needs-you .review-item > summary, #needs-you .acard > summary { grid-template-columns: 1fr; gap: 6px; }
  .review-action { grid-column: 1; grid-row: auto; padding-block: 8px; }
  .pulse-facts { grid-template-columns: 1fr; gap: 4px; }
  .pulse-facts dd { margin-bottom: 8px; }
  .toptabs { display: grid; grid-template-columns: repeat(4, 1fr); }
  .toptabs [data-tab="review"] { order: -1; }
  .toptabs button { padding-inline: 8px; min-height: 48px; }
  .theme button, .controls button, .controls select, .aform label { min-height: 44px; }
  .secondary { padding-inline: 10px; }
  .secondary > summary { margin-inline: -10px; }
  #global-search { padding: 14px; }
}
@media (prefers-reduced-motion: reduce) { .flash { animation: none; outline: 2px solid var(--accent); } }
"""

SEARCH_JS = """
function searchRecords(index, query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return index.filter(item => item.text.includes(q)).sort((a, b) => {
    const exact = Number(b.id.toLowerCase() === q) - Number(a.id.toLowerCase() === q);
    const ka = a.kind + '\\u0000' + a.id + '\\u0000' + a.source;
    const kb = b.kind + '\\u0000' + b.id + '\\u0000' + b.source;
    return exact || (ka < kb ? -1 : ka > kb ? 1 : 0);
  });
}
"""

_MISSION_SCRIPT = """
function reveal(target) {
  clearFilters();
  target.hidden = false;
  target.classList.remove('scope-hidden');
  for (let node = target; node; node = node.parentElement) {
    if (node.tagName === 'DETAILS') node.open = true;
    if (node.classList.contains('panel')) selectTab(node.id.replace('panel-', ''), {record: false});
  }
  target.setAttribute('tabindex', '-1');
  target.focus();
  target.scrollIntoView({block: 'start'});
}
const globalSearch = document.getElementById('global-search');
const globalQuery = document.getElementById('global-query');
const results = document.getElementById('search-results');
const searchStatus = document.getElementById('search-status');
let searchOpener = null;
function openSearch() {
  if (document.querySelector('dialog[open]')) return;
  searchOpener = document.activeElement;
  globalSearch.showModal();
  globalQuery.focus();
}
document.getElementById('search-open').addEventListener('click', openSearch);
document.getElementById('search-close').addEventListener('click', () => globalSearch.close());
globalSearch.addEventListener('close', () => { if (searchOpener) searchOpener.focus(); });
document.addEventListener('keydown', event => {
  const editing = event.target.isContentEditable || event.target.closest('input, textarea, select');
  if ((!editing && event.key === '/') || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k')) {
    event.preventDefault(); openSearch();
  }
});
globalQuery.addEventListener('input', () => {
  const matches = searchRecords(DATA.searchIndex, globalQuery.value);
  results.replaceChildren();
  matches.forEach(item => {
    const li = document.createElement('li');
    const link = document.createElement('a');
    link.href = '#' + encodeURIComponent(item.destination);
    link.textContent = item.kind + ' · ' + item.id + ' — ' + item.title;
    link.addEventListener('click', event => {
      event.preventDefault();
      searchOpener = null;
      globalSearch.close();
      const target = document.getElementById(item.destination);
      if (target) { reveal(target); history.replaceState(null, '', '#' + encodeURIComponent(item.destination)); }
    });
    const source = document.createElement('a');
    source.className = 'search-source'; source.textContent = item.source;
    const split = item.source.indexOf('#');
    source.href = '../chronicle/' + item.source.slice(0, split).split('/').map(encodeURIComponent).join('/') + '#' + encodeURIComponent(item.source.slice(split + 1));
    li.append(link, source); results.append(li);
  });
  searchStatus.textContent = matches.length ? matches.length + ' results. Use arrow keys or Tab to navigate.'
    : globalQuery.value.trim() ? 'Nothing matched. Try an ID or fewer words.' : 'Search by ID, title or recorded words.';
});
globalSearch.addEventListener('keydown', event => {
  const links = [...results.querySelectorAll('li > a:first-child')];
  if (event.key === 'Escape') {
    event.preventDefault(); globalSearch.close();
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const at = links.indexOf(document.activeElement);
    const next = event.key === 'ArrowDown' ? at + 1 : at < 0 ? links.length - 1 : at - 1;
    if (links.length) links[(next + links.length) % links.length].focus();
  } else if (event.key === 'Enter' && document.activeElement === globalQuery && links.length) {
    event.preventDefault(); links[0].click();
  } else if (event.key === 'Tab') {
    const focusable = [...globalSearch.querySelectorAll('button, input, a[href]')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
const scopeButtons = [...document.querySelectorAll('[data-work-scope]')];
function workScope(all) {
  document.querySelectorAll('[data-work-task]').forEach(row => {
    row.classList.toggle('scope-hidden', !all && !DATA.mission.relevant.includes(row.dataset.workTask));
  });
  document.querySelectorAll('.work-state').forEach(group => {
    const visible = [...group.querySelectorAll('[data-work-task]')].filter(row => !row.classList.contains('scope-hidden')).length;
    group.querySelector('[data-state-count]').textContent = visible;
    group.querySelector('.scope-empty').hidden = visible > 0;
  });
  scopeButtons.forEach(button => button.setAttribute('aria-pressed', String((button.dataset.workScope === 'all') === all)));
}
scopeButtons.forEach(button => button.addEventListener('click', () => workScope(button.dataset.workScope === 'all')));
workScope(false);
const initialTarget = document.getElementById(decodeURIComponent(location.hash.slice(1).split('?')[0]));
if (initialTarget && !initialTarget.classList.contains('panel')) reveal(initialTarget);
"""


def source_link(source: dict) -> str:
    pointer = source["file"] + "#" + source["anchor"]
    return f'<a href="../chronicle/{quote(source["file"])}#{quote(source["anchor"])}">{esc(pointer)}</a>'


def dependency_svg(tasks: dict) -> str:
    """Small offline SVG; full IDs, edges and a textual equivalent below it."""
    levels = {}
    pending = set(tasks)
    while pending:
        ready = sorted(t for t in pending if all(d not in pending for d in tasks[t]["deps"]))
        if not ready:  # Invalid cycles still render; validation owns diagnosis.
            ready = sorted(pending)
        for tid in ready:
            levels[tid] = 1 + max((levels.get(d, -1) for d in tasks[tid]["deps"]), default=-1)
            pending.remove(tid)
    positions, rows = {}, {}
    width = max(180, max((len(t) * 8 + 24 for t in tasks), default=180))
    for tid in sorted(tasks):
        level = levels[tid]
        row = rows.get(level, 0)
        rows[level] = row + 1
        positions[tid] = (20 + level * (width + 80), 20 + row * 64)
    edges, nodes = [], []
    for tid, (x, y) in positions.items():
        for dep in tasks[tid]["deps"]:
            if dep in positions:
                dx, dy = positions[dep]
                edges.append(f'<path d="M {dx + width} {dy + 20} L {x} {y + 20}"/>')
        nodes.append(f'<a href="#task-record-{esc(tid)}" data-task="{esc(tid)}">'
                     f'<rect x="{x}" y="{y}" width="{width}" height="40" rx="6"/>'
                     f'<text x="{x + 12}" y="{y + 25}">{esc(tid)}</text></a>')
    return (f'<div class="offline-graph"><svg role="img" aria-label="Dependency graph; dependencies flow left to right. Full links below." '
            f'width="{max((x + width + 20 for x, y in positions.values()), default=240)}" '
            f'height="{max((y + 60 for x, y in positions.values()), default=80)}">'
            + ''.join(edges + nodes) + '</svg></div>')

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
  var tab = location.hash.slice(1).replace(/^panel-/, '').split('?')[0];
  tab = ({overview:'home', execution:'work', graph:'work', tasks:'work', assumptions:'review',
          operations:'audit', governance:'audit', decisions:'audit'})[tab] || tab;
  var known = ['home', 'work', 'review', 'audit'];
  root.setAttribute('data-tab', known.indexOf(tab) >= 0 ? tab : 'home');
})();"""


def render(project: Project, report: Report, compiled: dict, interactive: bool = False,
           reviewer: str = "") -> str:
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
            f'<a href="#task-record-{esc(task_id)}" class="tasklink{" ops" if task["domain"] == "operations" else ""}"'
            f' data-task="{esc(task_id)}"><code>{esc(task_id)}</code> {esc(task["title"])}</a>'
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

    event_occurrences: dict[str, int] = {}

    def event_item(event: dict, canonical: bool = False) -> str:
        occurrence = event_occurrences.get(event["id"], 0) + 1
        event_occurrences[event["id"]] = occurrence
        anchor = "event-" + event["id"] + ("" if canonical else f"-{occurrence}")
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
            f'<li class="event" id="{esc(anchor)}" data-type="{esc(event["type"])}">'
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

    def event_list(items: list[dict], empty: str, canonical: bool = False) -> str:
        if not items:
            return f'<p class="note">{esc(empty)}</p>'
        return f'<ul class="plain events">{"".join(event_item(e, canonical) for e in items)}</ul>'

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
            f'<article id="phase-{esc(phase["id"])}" class="phase{" current" if phase["id"] == current_id else ""}"'
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
            f'<div id="module-{esc(module["id"])}" class="module"{_agg(n=_each(module["tasks"]))}><div><code>{esc(module["id"])}</code>'
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
        review_label = {"REVIEW_MISSING": "Evidence recorded, review missing",
                        "EVIDENCE_MISSING": "No exercised evidence, no review"}.get(item.get("variant"), "")
        return (
            f'<li>{_pill(item["type"])} {ref(item["subject"])}<br>'
            + (f'<strong>{review_label}</strong><br>' if review_label else '')
            +
            f'<span class="note">{esc(item["detail"])}</span><br>'
            f'<span class="note"><strong>To unblock:</strong> {esc(item.get("unblock", ""))} · '
            f'<strong>Who acts:</strong> {esc(item.get("actor", "agent"))}</span>'
            + ("".join("<br>" + review.guide(h, interactive) for h in item.get("holders", [])) if offer_guide else "")
            + "</li>"
        )

    owner_held = [o for o in compiled["obstacles"] if o.get("actor") == "owner"]

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
    native = {"task-graph.mmd": dependency_svg(tasks), **drawings.render_all(project, report)}
    labels = [("task-graph.mmd", "Task graph"), ("critical-path.mmd", "Critical path"),
              ("phases.mmd", "Phases"), ("gates.mmd", "Gates"),
              ("timeline.mmd", "Dependency ordering"), ("gantt.mmd", "Calendar Gantt")]
    diagram_tabs = "".join(
        f'<button id="view-tab-{i}" role="tab" aria-controls="view-{i}" tabindex="{0 if i == 0 else -1}" '
        f'data-diagram="{name}" aria-selected="{str(i == 0).lower()}">{label}</button>'
        for i, (name, label) in enumerate(labels))
    native_views = "".join(
        f'<section id="view-{i}" class="native-view{" is-selected" if i == 0 else ""}" '
        f'role="tabpanel" aria-labelledby="view-tab-{i}"><h3>{label}</h3>{native[name]}'
        f'<details class="diagram-source"><summary>Mermaid source · {label}</summary>'
        f'<pre><code>{esc(diagrams[name])}</code></pre></details></section>'
        for i, (name, label) in enumerate(labels))
    scheduled = len(compiled["schedule"]["scheduled"])
    unscheduled = len(compiled["schedule"]["unscheduled"])

    # --- Operations --------------------------------------------------------
    om = operations["metrics"]
    all_events = sorted(operations["events"], key=lambda e: event_order(e["id"], e["time"]))
    affects_execution = set(om["affectingExecution"])
    ops_tasks = project.operations_tasks
    blocked_ids = set(compiled["blocked"])
    unresolved = set(operations["unresolvedFailures"])

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
            f'<details class="adr" id="decision-{esc(decision["id"])}" data-status="{esc(decision["status"])}"'
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
        f' aria-controls="panel-{name}" aria-selected="{str(name == "home").lower()}"'
        f' tabindex="{0 if name == "home" else -1}">{label}</button>'
        for name, label in TOP_TABS
    )

    def panel(name: str, body: str) -> str:
        if name not in {key for key, _ in TOP_TABS}:
            labels = {"graph": "Dependency graph and diagram sources", "tasks": "Full task registry",
                      "operations": "Operational trace", "governance": "Evidence and governance",
                      "decisions": "Architecture decisions", "execution": "Execution diagnostics"}
            return (f'<details class="group secondary" id="panel-{name}">'
                    f'<summary>{labels.get(name, name.title())}</summary>{body}</details>')
        return (
            f'<section class="panel" id="panel-{name}" role="tabpanel"'
            f' aria-labelledby="tab-{name}" tabindex="0">{body}</section>'
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
    graph = panel("graph", f"""
  <h2>Views</h2>
  <div class="tabs diagram-tabs" role="tablist" aria-label="Diagram views">{diagram_tabs}</div>
  {native_views}
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
  <p class="note">{len(all_events)} recorded events · {len(open_ops)} open operations tasks.</p>
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
  <div class="box" id="timeline">{event_list(all_events, "No events recorded.", True)}</div>
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

    # Mission Control reuses the existing inspection views and response forms.
    mission = compiled["mission"]
    active_roots = [r for r in mission["roots"] if r["current"]]
    first_root = active_roots[0] if active_roots else None
    next_decision = next(iter(mission["review"]["now"]), None)
    next_assumption = next(iter(mission["review"]["assumptions"]), None)
    main_blocker = (
        f'<a href="#root-{esc(first_root["id"])}">{esc(first_root["id"])} — {esc(first_root["title"])}</a>'
        f'<span class="note"> · {first_root["count"]} downstream tasks</span>'
        + (f'<span class="note"> · {esc(first_root["reason"])}</span>' if first_root["kind"] == "upcoming" else '')
        if first_root else 'No active root blocker recorded'
    )
    if not first_root and gate:
        pending_gates = [g["id"] for g in gate["gates"] if g["status"] != "GREEN"]
        if pending_gates:
            main_blocker = (f'<a href="#panel-governance">{esc(", ".join(pending_gates))}</a>'
                            ' · phase exit awaits gate evidence')
    next_action = '<p>No direct human decision is actionable now. Continue ready work or inspect upcoming decisions.</p><a class="primary-link" href="#panel-review">Open Review →</a>'
    if next_decision:
        tid = next_decision["id"]
        next_action = (f'<h3>{esc(tasks[tid]["title"])}</h3><p>Owner authority is required for {ref(tid)}.</p>'
                       f'<p>{len(next_decision["affected"])} downstream tasks depend on it. Other dependencies may still apply.</p>'
                       f'<a class="primary-link" href="#review-{esc(tid)}">Review decision and affected work →</a>')
    elif next_assumption:
        a = next(a for a in compiled["assumptions"] if a["id"] == next_assumption["id"])
        next_action = (f'<h3>Validate: {esc(a["title"])}</h3><p>Provisional assumption affecting '
                       f'{len(next_assumption["affected"])} unfinished tasks. A response requires agent reconciliation; it does not itself unblock work.</p>'
                       f'<a class="primary-link" href="#assumption-{esc(a["id"])}">Inspect assumption and respond →</a>')
    wip_list = ''.join(f'<li>{ref(t)}<br><span class="note">Responsible: {esc(tasks[t]["owner"] or "unassigned")} · Runtime: unknown</span></li>'
                       for t in compiled["wip"])
    milestones = [m for m in compiled["milestones"] if not m["reached"]][:2]
    milestone_body = ''.join(f'<li>{esc(m["text"])} {ref(m["task"]) if m["task"] else ""}</li>' for m in milestones)
    freshness = compiled["project"].get("recordedDate")
    freshness_text = (esc(freshness["date"]) + ' · ' + source_link(freshness["source"])) if freshness else "Not recorded"
    phase_overview, progress_charts = _home_progress(compiled)
    home = panel("home", f'''
      {phase_overview}
      <section class="next-action"><h2>Next human action</h2>{next_action}</section>
      {progress_charts}
      <div class="pulse"><div><h2>Most consequential blocker</h2><p>{main_blocker}</p></div>
      <dl class="pulse-facts"><dt>Execution completion</dt><dd>{metrics["taskCompletion"]["done"]} / {metrics["taskCompletion"]["total"]} tasks</dd>
      <dt>Validation strength</dt><dd>{metrics["validationCoverage"]["done"]} / {metrics["validationCoverage"]["total"]} reviewed or verified</dd>
      <dt>Operations activity</dt><dd>{om["tasks"]["active"]} active · {om["events"]["total"]} recorded events</dd>
      <dt>Latest recorded chronicle date</dt><dd>{freshness_text}</dd></dl></div>
      <div class="pulse-bottom"><section><h2>Work in progress</h2><ul class="work-list">{wip_list}</ul>
      {"" if wip_list else '<p class="note">No work in progress. No active agent sessions are recorded.</p>'}</section>
      <section><h2>Next milestones</h2><ul class="work-list">{milestone_body}</ul>
      {"" if milestone_body else '<p class="note">No upcoming milestone recorded.</p>'}
      <h3>Next phase gate</h3>{gate_card}</section></div>
      <p class="note">{len(active_roots)} active root blockers · {len(mission["affected"])} distinct downstream tasks ·
      {len(mission["future"])} future owner obligations. <a href="#panel-work">Inspect work and dependencies →</a></p>
    ''')

    def root_item(root: dict) -> str:
        links = ', '.join(ref(t) for t in root["affected"]) or 'No unfinished downstream tasks.'
        label = {"human": "Direct human action", "upcoming": "Upcoming owner decision",
                 "technical": "Direct technical blocker"}[root["kind"]]
        return (f'<li id="root-{esc(root["id"])}"><strong>{esc(root["title"])}</strong> '
                f'<code>{esc(root["id"])}</code> · {root["count"]} downstream<br>'
                f'<span class="note">{label}'
                f' · {esc(root["actor"])} · {"current" if root["current"] else "future obligation"}</span>'
                f'<p>{esc(root["reason"])}</p><details><summary>Cause, evidence and affected tasks</summary>'
                f'<p>Cause: {ref(root["id"])}</p><p>{source_link(root["source"]) if root["source"] else "Source record missing"}</p>'
                f'<p>Transitive impact: {links}</p><p class="note">Resolving this cause removes this dependency; other roots may still block these tasks.</p></details></li>')

    roots_body = '<ul class="root-list">' + ''.join(root_item(r) for r in mission["roots"]) + '</ul>'
    if not mission["roots"]:
        roots_body = '<p class="note">No root blockers recorded. Waiting for ordinary dependencies is not a failure.</p>'
    work_states = ''.join(
        f'<details class="work-state"{" open" if state != "Completed" else ""}><summary>{state} · <span data-state-count>{len(ids)}</span></summary>'
        + ('<ul class="plain">' + ''.join(f'<li data-work-task="{esc(t)}">{ref(t)}'
            + ('<span class="note"> · Upcoming: not in current phase</span>' if state == "Upcoming" or (t not in mission["relevant"] and tasks[t]["status"] != "DONE") else '')
            + (f'<span class="note"> · Responsible: {esc(tasks[t]["owner"] or "unassigned")} · Runtime: unknown</span>' if tasks[t]["status"] == "WIP" else '')
            + '</li>' for t in ids) + '</ul>' if ids else '')
        + f'<p class="note scope-empty"{" hidden" if ids else ""}>No tasks in this state for the selected scope.</p></details>'
        for state, ids in mission["states"].items())
    dependency_list = '<ul class="plain">' + ''.join(
        f'<li>{ref(t)} ← ' + (', '.join(ref(d) for d in task["deps"]) or 'No dependencies') + '</li>'
        for t, task in tasks.items()) + '</ul>'
    work = panel("work", '<h2>Execution</h2><p class="note">Current phase, work in progress and their prerequisites. '
                 'The full registry and graph below always include every task.</p>'
                 '<div class="work-scope"><button data-work-scope="current" aria-pressed="true">Current work</button>'
                 '<button data-work-scope="all" aria-pressed="false">All work</button></div>'
                 + ('<p class="note">Nothing ready. Inspect dependency waits and upcoming decisions; this does not mean everything failed.</p>' if not mission["states"]["Ready"] else '')
                 + work_states + '<h2>Root blockers</h2>' + roots_body
                 + '<details class="group"><summary>Critical path</summary>' + critical + '</details>'
                 + '<details class="group"><summary>Phases and product hierarchy</summary><h2>Phases</h2><div class="phases">'
                 + phase_cards + '</div><h2>Product hierarchy</h2>' + hierarchy + '</details>'
                 + registry + '<details class="group"><summary>Complete dependency graph</summary>'
                 + dependency_svg(tasks) + dependency_list + '</details>' + graph
                 + '<details class="group"><summary>Execution breakdown and progress</summary>'
                 + _breakdown(compiled) + f'<h2>Execution progress</h2><div class="grid">{cards}</div>' + work_ahead + '</details>')
    review_body, _, review_count, _ = review.panel_body(compiled, ref, interactive, mission=True)
    review_workspace = panel("review", f'<div id="needs-you" data-waiting="{review_count}">'
        f'<div class="needs-head"><h2>Human decision inbox</h2><span class="needcount{"" if review_count else " zero"}">{review_count}</span>'
        '<span class="live">Answering live</span></div>'
        + review.review_bar(interactive, reviewer)
        + ('<p class="note">Each card starts at OK. A card you open or mark reviewed is confirmed when you submit. Unopened cards record nothing.</p>' if interactive else
           '<p class="respond-hint">This page is read-only. To answer, run <code>praukron dashboard --serve</code>.</p>')
        + '<p id="needs-filtered" hidden><span></span><button data-xf-clear>Show all</button></p>'
        + ('<p class="note">Nothing waits for you.</p>' if not review_count else '')
        + review_body + '</div>')
    records = ''
    for tid, task in tasks.items():
        contract = compiled["acceptance"].get(task["ac"], {})
        records += (f'<details class="group" id="task-record-{esc(tid)}"><summary><code>{esc(tid)}</code> {esc(task["title"])}</summary>'
                    f'<p>{_pill(task["status"])} {_pill(task["validation"])} · {esc(task["domain"])} · Authority: {esc(task["authority"] or "agent")}</p>'
                    f'<p>Evidence: {esc(task["evidence"] or "none recorded")}</p>'
                    f'<p>Responsible: {esc(task["owner"] or "unassigned")} · Claimed: {esc(task["claimed"] or "not recorded")}</p>'
                    f'<p>Implementation files: {esc(", ".join(task["implementation"]["files"]) or "none recorded")}</p>'
                    f'<p>Implementation symbols: {esc(", ".join(task["implementation"]["symbols"]) or "none recorded")}</p>'
                    f'<p>Schedule: {esc(json.dumps(task["schedule"])) if task["schedule"] else "No schedule recorded"}</p>'
                    + '<p>Dependencies: ' + (', '.join(ref(d) for d in task["deps"]) or 'none') + '</p>'
                    + '<p>Acceptance: ' + (', '.join(f'<a href="#criterion-{esc(c["id"])}">{esc(c["id"])}</a>' for c in contract.get("criteria", [])) or 'none') + '</p>'
                    + '<p>Assumptions: ' + (', '.join(f'<a href="#assumption-{esc(a)}">{esc(a)}</a>' for a in task["assumptions"]) or 'none') + '</p>'
                    + '<p>Decisions: ' + (', '.join(f'<a href="#decision-{esc(d)}">{esc(d)}</a>' for d in task["decisions"]) or 'none') + '</p>'
                    + source_link(task["source"]) + '</details>')
    contract_records = [*compiled["acceptance"].items(), *((c["id"], c) for c in compiled["invariants"])]
    acceptance_records = ''.join(
        f'<section><h3>{esc(cid)} — {esc(contract["title"])}</h3>'
        f'<p>Contract evidence: {esc(contract.get("evidence") or "none recorded")}</p>'
        f'<p>Inherited invariants: {esc(", ".join(contract.get("inherits", [])) or "none")}</p>'
        + source_link(contract["source"]) + ''.join(
        f'<article id="criterion-{esc(c["id"])}"><h3>{esc(c["id"])}</h3><p>{esc(c["text"])}</p>'
        f'<p>{_pill(c["state"])} · {esc(c["evidenceClass"])} · {esc(c.get("evidence") or "No evidence recorded")}</p>'
        + source_link(c.get("source", contract["source"])) + '</article>'
        for c in contract["criteria"]) + '</section>' for cid, contract in contract_records)
    response_records = ''.join(f'<article id="response-{esc(r["id"])}"><h3>{esc(r["id"])} · {esc(r["action"])}</h3>'
                               f'<p class="record-body">{esc(r["text"])}</p><p>{esc(r["by"])} · {esc(r["date"])} · {esc(r["target"])}</p>'
                               + source_link(r["source"]) + '</article>' for r in compiled["responses"])
    history = ''.join(f'<details class="group" id="journal-{esc(j["id"])}"><summary>{esc(j["title"])}</summary>'
                     f'<p class="record-body">{esc(j["text"])}</p>{source_link(j["source"])}</details>' for j in compiled["journal"])
    audit = panel("audit", '<h2>Evidence and governance</h2><p class="note">Recorded evidence, decisions and history. No events are inferred.</p>'
                  + governance + decisions
                  + '<details class="group"><summary>Acceptance criteria and evidence</summary>'
                  + (acceptance_records or '<p>No acceptance criteria recorded.</p>') + '</details>'
                  + '<details class="group"><summary>Task records and implementation evidence</summary>' + records + '</details>'
                  + operations_panel
                  + '<details class="group" id="panel-execution"><summary>Execution diagnostics</summary>'
                  + failures_section + '<h2>Obstacles</h2><ul class="plain xf-list">'
                  + ''.join(obstacle_item(o) for o in compiled["obstacles"]) + '</ul>'
                  + '<h2>Owner guidance</h2>' + review.guidance(compiled, ref) + external_overview + '</details>'
                  + '<details class="group"><summary>Responses and reconciliation history</summary>'
                  + (response_records or '<p>No responses recorded.</p>') + '</details>'
                  + '<details class="group"><summary>Journal history</summary>' + (history or '<p>No journal entries recorded.</p>') + '</details>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(project.name)} — project state</title>
<script>{_BOOT}</script>
<style>{_STYLE}{review.STYLE}{_MISSION_STYLE}</style>
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
    <button type="button" class="search-control" id="search-open" aria-haspopup="dialog" aria-controls="global-search">Search everything <kbd>Ctrl K</kbd></button>
    <div class="theme" role="group" aria-label="Theme">
      <button type="button" data-theme-choice="system" aria-pressed="false">System</button>
      <button type="button" data-theme-choice="light" aria-pressed="false">Light</button>
      <button type="button" data-theme-choice="dark" aria-pressed="false">Dark</button>
    </div>
  </header>

  <div class="layout">
  <div class="content">
  <div class="stickyhead">
<nav class="toptabs" role="tablist" aria-label="Workspaces">{tab_buttons}</nav>
  <div class="xfbar" id="xf-bar" role="region" aria-label="Active filters" hidden>
    <span class="xf-label">Filtered by</span><ul class="xf-chips" id="xf-chips"></ul>
    <button type="button" class="linkbtn" data-xf-clear>Clear all</button>
    <span class="xf-count" id="xf-count"></span>
  </div>
  </div>
  <p class="sr" id="xf-status" aria-live="polite"></p>
{home}
{work}
{review_workspace}
{audit}
  </div>
  </div>
  <footer>
    Generated by <code>praukron dashboard</code>. This page is derived and read-only:
    it reports authority and cannot change it. Edit
    <code>{esc(compiled["project"]["authority"])}/</code> and compile again.
  </footer>
</main>

<dialog id="detail" aria-label="Task details"><button class="close" aria-label="Close"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/></svg></button><div class="body"></div></dialog>
<dialog id="global-search" aria-labelledby="search-title">
  <div class="search-head"><h2 id="search-title">Search everything</h2><button id="search-close" type="button">Close</button></div>
  <label class="sr" for="global-query">Search records</label><input id="global-query" type="search" autocomplete="off" placeholder="Task, decision, assumption, or recorded words…">
  <p id="search-status" role="status" aria-live="polite">Search by ID, title or recorded words.</p><ul id="search-results"></ul>
</dialog>

<script type="application/json" id="project-data">{embed(compiled)}</script>
<script type="application/json" id="diagram-data">{embed(diagrams)}</script>
<script type="application/json" id="node-map">{embed(node_map)}</script>
<script>{FILTER_JS}{_SCRIPT}{SEARCH_JS}{_MISSION_SCRIPT}{review.script(interactive)}</script>
</body>
</html>
"""
