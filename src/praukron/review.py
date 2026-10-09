"""The owner's review queue (ADR-054, ADR-055) and the side panel it lives in (ADR-065).

Everything here is laid out from compiled data. The static page is read-only;
only `praukron dashboard --serve` turns the cards into a form, and that form
writes through `praukron respond`, never into this page.
"""

from __future__ import annotations

import html
import re

STYLE = """
/* Assumptions review: an inbox, not a table. */
details.acard { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
                margin: 0 0 8px; box-shadow: var(--shadow); }
details.acard > summary { cursor: pointer; padding: 10px 14px; list-style: none; display: flex;
                          gap: 8px; align-items: baseline; flex-wrap: wrap; }
details.acard > summary::-webkit-details-marker { display: none; }
details.acard[open] > summary { border-bottom: 1px solid var(--line); }
details.acard .abody { padding: 10px 14px 12px; }
details.acard .abody p { margin: 6px 0; }
.acards { padding: 0 12px 6px; }
.perm { border-left: 3px solid var(--warn); padding: 4px 10px; background: color-mix(in srgb, var(--warn) 10%, transparent); }
.pill.permission { color: var(--warn); border-color: var(--warn); }
.resp { white-space: pre-wrap; border-left: 3px solid var(--accent); padding: 4px 10px; margin: 6px 0; }
.resp .who { color: var(--muted); white-space: normal; }
.respond-hint { color: var(--muted); font-size: 13px; }
.aform { display: grid; gap: 6px; margin-top: 8px; }
.aform textarea { min-height: 60px; font: inherit; }
"""

SCRIPT = """
// Assumptions: filter the review queue by values the compiler wrote.
const acards = [...document.querySelectorAll('#panel-assumptions details.acard')];
const aSearch = document.getElementById('assume-search');
const aSelects = ['assume-phase', 'assume-impact', 'assume-status', 'assume-kind']
  .map(id => document.getElementById(id)).filter(Boolean);
function runAssumptions() {
  if (!aSearch) return;
  const q = aSearch.value.trim().toLowerCase();
  let shown = 0;
  acards.forEach(card => {
    const [phase, impact, status, kind] = aSelects.map(s => s.value);
    const ok = (!q || card.dataset.text.includes(q))
      && (!phase || card.dataset.phase.split(' ').includes(phase))
      && (!impact || card.dataset.impact === impact)
      && (!status || card.dataset.status === status)
      && (!kind || card.dataset.kind === kind);
    card.hidden = !ok;
    if (ok) shown++;
  });
  const out = document.getElementById('assume-shown');
  if (out) out.textContent = shown + ' of ' + acards.length;
}
if (aSearch) {
  aSearch.addEventListener('input', runAssumptions);
  aSelects.forEach(s => s.addEventListener('change', runAssumptions));
}
"""

# The review rule, kept pure so it can be tested without a browser.
DECIDE_JS = """
function reviewItems(entries) {
  const items = [];
  for (const e of entries) {
    if (e.guide) { if (e.text.trim()) items.push({ target: e.target, action: 'GUIDE', text: e.text }); continue; }
    if (e.choice === 'FEEDBACK') {
      if (!e.text.trim()) throw new Error(e.target + ': write your feedback, or choose OK');
      items.push({ target: e.target, action: e.action, text: e.text });
    } else if (e.seen || e.reviewed) {
      items.push({ target: e.target, action: 'CONFIRM', text: 'OK' });
    }
  }
  return items;
}
"""

# Only the served page carries this: the static file never contains a write.
SERVE_SCRIPT = DECIDE_JS + """
// Served review (praukron dashboard --serve). Present only when forms exist.
// A card left at OK records CONFIRM only if the owner opened it or marked it
// reviewed; an unopened card records nothing (ADR-055).
const reviewBars = [...document.querySelectorAll('.review-bar')];
if (reviewBars.length) {
  const token = new URLSearchParams(location.search).get('token') || '';
  const names = [...document.querySelectorAll('.review-by')];
  let remembered = '';
  try { remembered = localStorage.getItem('praukron-reviewer') || ''; } catch (e) {}
  names.forEach(n => { n.value = remembered; n.addEventListener('input', () => {
    names.forEach(o => { if (o !== n) o.value = n.value; });
    try { localStorage.setItem('praukron-reviewer', n.value); } catch (e) {} }); });
  document.querySelectorAll('details.acard').forEach(card => card.addEventListener('toggle', () => { if (card.open) card.dataset.seen = '1'; }));
  document.querySelectorAll('.aform textarea').forEach(area => area.addEventListener('input', () => {
    const form = area.closest('.aform');
    const feedback = form.querySelector('input[value="FEEDBACK"]');
    if (feedback && area.value.trim()) feedback.checked = true;
  }));
  function collect() {
    return reviewItems([...document.querySelectorAll('.aform')].map(form => {
      const card = form.closest('details.acard');
      const radio = form.querySelector('input[type="radio"]:checked');
      const select = form.querySelector('select');
      const reviewed = form.querySelector('.reviewed');
      return { target: form.dataset.target, guide: !!form.dataset.guide,
               text: form.querySelector('textarea').value,
               choice: radio ? radio.value : 'CONFIRM', action: select ? select.value : 'REVISE',
               seen: !!card && card.dataset.seen === '1', reviewed: !!reviewed && reviewed.checked };
    }));
  }
  reviewBars.forEach(bar => bar.querySelector('button').addEventListener('click', async () => {
    const out = bar.querySelector('.review-result');
    try {
      const by = bar.querySelector('.review-by').value.trim();
      if (!by) throw new Error('Enter your name: every answer records who gave it.');
      const items = collect();
      if (!items.length) throw new Error('Nothing to record: open a card to confirm it, or write feedback.');
      const response = await fetch('/respond', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Praukron-Token': token },
        body: JSON.stringify({ by, items }) });
      const result = await response.json();
      if (!result.ok) throw new Error(result.error);
      out.textContent = 'Recorded ' + result.ids.join(', ') + '. Reloading…';
      location.reload();
    } catch (error) { out.textContent = error.message; }
  }));
}
"""


def script(interactive: bool) -> str:
    return SCRIPT + (SERVE_SCRIPT if interactive else "")


def review_bar(interactive: bool) -> str:
    """Name, submit, and result; one per panel that holds a form."""
    if not interactive:
        return ""
    return ('<div class="controls review-bar"><input type="text" class="review-by" aria-label="Your name"'
            ' placeholder="Your name (recorded with every answer)">'
            '<button type="button">Submit review</button><span class="review-result" aria-live="polite"></span></div>')


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _date(recorded: str | None) -> str:
    match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", recorded or "")
    return match.group(1) if match else ""


def _pill(value: str) -> str:
    return f'<span class="pill {esc(value)}">{esc(value)}</span>'


def guide(target: str, interactive: bool) -> str:
    """Guidance on an owner-held blocker: a form when served, a hint when static."""
    if interactive:
        return (f'<div class="aform" data-target="{esc(target)}" data-guide="1">'
                f'<textarea aria-label="Guidance on {esc(target)}" placeholder="Guidance for the agent on '
                f'{esc(target)}, recorded verbatim"></textarea></div>')
    return ('<span class="respond-hint">Guide: run <code>praukron dashboard --serve</code> to give the '
            f'agent guidance on {esc(target)}.</span>')


def guidance(compiled: dict, ref) -> str:
    """Every GUIDE response on a task, verbatim, kept after the task is done."""
    tasks = {t["id"]: t for t in compiled["tasks"]}
    items = [r for r in compiled["responses"] if r["action"] == "GUIDE" and r["target"] in tasks]
    if not items:
        return '<p class="note">No owner guidance recorded.</p>'
    return '<div class="box">' + "".join(
        f'<p class="resp">{esc(r["text"])}<span class="who"> — {esc(r["by"])}, {esc(r["date"])} · on '
        f'{ref(r["target"])} {_pill(tasks[r["target"]]["status"])} · {esc(r["id"])}</span></p>'
        for r in items
    ) + "</div>"


def panel_body(compiled: dict, ref, interactive: bool = False) -> tuple[str, str, int, int]:
    """The Assumptions tab, and the queue the side panel shows instead of it.

    Returns the tab body, the queue markup, how many cards the queue holds, and
    how many answered assumptions wait for the agent. The queue's cards render
    only in the side panel, never also in the tab (ADR-065)."""
    assumptions = compiled["assumptions"]
    report = compiled["assumptionReport"]
    if not assumptions:
        return ('<h2>Assumptions</h2><p class="note">No assumptions recorded. Agents record a '
                "provisional choice in <code>ASSUMPTIONS.md</code> before relying on it.</p>", "", 0, 0)
    tasks = {t["id"]: t for t in compiled["tasks"]}
    responses: dict[str, list[dict]] = {}
    for r in compiled["responses"]:
        responses.setdefault(r["target"], []).append(r)
    path = set(compiled["criticalPath"])
    current = compiled["project"]["currentPhase"]
    waiting = {w["assumption"] for w in report["awaitingReconciliation"]}
    resting: dict[str, list[str]] = {}
    for item in report["passRestingOnOpen"]:
        resting.setdefault(item["assumption"], []).append(item["criterion"])

    def done_tasks(a: dict) -> list[str]:
        return [t for t in a["tasks"] if tasks.get(t, {}).get("status") == "DONE"]

    def card(a: dict, anchor: bool = True) -> str:
        kind = "permission" if a["permissions"] else "other"
        text = " ".join(str(v) for v in (a["id"], a["title"], a["assumption"], a["basis"],
                                         a["appliedIn"], " ".join(a["tasks"]))).lower()
        decisions = [r for r in a["references"] if r.startswith("ADR-")]
        criteria = [r for r in a["references"] if r.startswith("AC-")]
        rests = done_tasks(a)
        said = "".join(
            f'<p class="resp">{esc(r["text"])}<span class="who"> — {esc(r["by"])}, {esc(r["date"])}'
            f' · {esc(r["action"])} · via {esc(r["via"])}</span></p>'
            for r in responses.get(a["id"], [])
        ) or '<p class="note">No response yet.</p>'
        action = (
            # A card already answered waits for the agent; offering the form
            # again recorded the same answer twice in the first owner run.
            form(a) if interactive and a["status"] in ("OPEN", "CONFIRMED") and a["id"] not in waiting else
            '<p class="respond-hint">Answered; waiting for the agent to reconcile it.</p>'
            if interactive and a["id"] in waiting else
            '<p class="respond-hint">To respond, run <code>praukron dashboard --serve</code> '
            "and open the link it prints. This page is read-only.</p>"
        )
        return (
            '<details class="acard"' + (f' id="assumption-{esc(a["id"])}"' if anchor else "")
            + f' data-id="{esc(a["id"])}" data-impact="{esc(a["impact"])}" data-status="{esc(a["status"])}"'
            f' data-phase="{esc(" ".join(a["phases"]))}" data-kind="{kind}" data-text="{esc(text)}">'
            f'<summary><code>{esc(a["id"])}</code> {esc(a["title"])} {_pill(a["impact"])} {_pill(a["status"])}'
            + (' <span class="pill permission">Permission</span>' if a["permissions"] else "")
            + (' <span class="pill">awaiting the agent</span>' if a["id"] in waiting else "")
            + '</summary><div class="abody">'
            + f'<p><strong>Assumed:</strong> {esc(a["assumption"])}</p>'
            + (f'<p class="perm"><strong>The agent assumed an access rule:</strong> {esc(a["permissions"])}</p>'
               if a["permissions"] else "")
            + f'<p><strong>Why:</strong> {esc(a["basis"] or "not stated")}</p>'
            f'<p class="note">Phase {esc(", ".join(a["phases"]) or "none")} · module '
            f'{esc(", ".join(a["modules"]) or "none")} · tasks {", ".join(ref(t) for t in a["tasks"])}'
            f' · recorded {esc(a["recorded"] or "")}</p>'
            f'<p class="note"><strong>Applied in:</strong> {esc(a["appliedIn"] or "not stated")}</p>'
            + (f'<p><strong>Already rests on it:</strong> '
               + ", ".join([*(ref(t) + " (DONE)" for t in rests), *(esc(c) + " (PASS)" for c in resting.get(a["id"], []))])
               + "</p>" if rests or resting.get(a["id"]) else "")
            + f'<p class="note"><strong>If revised:</strong> the agent reconciles '
            + ", ".join([*(ref(t) for t in a["tasks"]), *map(esc, criteria), *map(esc, decisions)])
            + " and names what changed in <code>Reconciled by</code>.</p>"
            + f"<div><strong>Responses</strong>{said}</div>{action}</div></details>"
        )

    def form(a: dict) -> str:
        return (
            f'<div class="aform" data-target="{esc(a["id"])}">'
            + (f'<p class="perm">This is an access rule the agent assumed: {esc(a["permissions"])}. '
               "OK confirms it as the rule.</p>" if a["permissions"] else "")
            + f'<label><input type="radio" name="d-{esc(a["id"])}" value="CONFIRM" checked> OK</label>'
            f'<label><input type="checkbox" class="reviewed"> Mark reviewed</label>'
            f'<label><input type="radio" name="d-{esc(a["id"])}" value="FEEDBACK"> Feedback</label>'
            f'<select aria-label="Action for {esc(a["id"])}"><option value="REVISE">Revise</option>'
            '<option value="REJECT">Reject</option><option value="GUIDE">Guide</option></select>'
            f'<textarea aria-label="Feedback on {esc(a["id"])}" placeholder="Your words, recorded verbatim"></textarea>'
            "</div>"
        )

    def rank(a: dict) -> tuple:
        on_done_or_path = bool(done_tasks(a)) or bool(path & set(a["tasks"]))
        return (a["impact"] != "HIGH", not on_done_or_path, current not in a["phases"], a["id"])

    opened = [a for a in assumptions if a["status"] == "OPEN" and a["id"] not in waiting]
    permission = sorted([a for a in opened if a["permissions"]], key=rank)
    review = sorted([a for a in opened if not a["permissions"]], key=rank)
    agent = [a for a in assumptions if a["id"] in waiting]
    settled = [a for a in assumptions if a["status"] != "OPEN" and a["id"] not in waiting]

    def latest(a: dict) -> str:
        dates = [_date(a["recorded"]), *(r["date"] or "" for r in responses.get(a["id"], []))]
        return max(dates)

    recent = sorted(assumptions, key=lambda a: (latest(a), a["id"]), reverse=True)[:10]
    recent_list = "".join(
        f'<li><a href="#assumption-{esc(a["id"])}"><code>{esc(a["id"])}</code></a> {esc(a["title"])} '
        f'{_pill(a["status"])} <span class="note">{esc(latest(a))}</span></li>'
        for a in recent
    )

    def group(title: str, items: list[dict], opened_: bool, note: str = "") -> str:
        if not items:
            return ""
        return (
            f'<details class="group"{" open" if opened_ else ""}><summary>{esc(title)}'
            f'<span class="count">{len(items)}</span></summary>{note}'
            f'<div class="acards">{"".join(card(a) for a in items)}</div></details>'
        )

    phases = sorted({p for a in assumptions for p in a["phases"]})
    statuses = [s for s in ("OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN")
                if any(a["status"] == s for a in assumptions)]
    impacts = [i for i in ("HIGH", "MEDIUM", "LOW") if any(a["impact"] == i for a in assumptions)]

    def select(element_id: str, label: str, values: list[tuple[str, str]]) -> str:
        options = "".join(f'<option value="{esc(v)}">{esc(t)}</option>' for v, t in values)
        return (f'<select id="{element_id}" aria-label="{label}">'
                f'<option value="">{label}: all</option>{options}</select>')

    summary = (
        f'{len(report["open"])} open · {len(report["openHigh"])} HIGH · '
        f'{len(report["openPermission"])} permission · {len(report["awaitingReconciliation"])} waiting for the agent'
    )
    queue = (
        group("Permission assumptions", permission, True,
              '<p class="note">Access rules an agent assumed. Review these first.</p>')
        + group("Open assumptions", review, True,
                '<p class="note">HIGH first, then those resting on finished or critical-path work, '
                'then the current phase.</p>')
    )
    in_tab = len(assumptions) - len(permission) - len(review)
    tab = (
        "<h2>Assumptions</h2>"
        f'<p class="note">{summary}. An assumption holds no authority; the records it is applied in do.</p>'
        '<div class="controls"><input type="search" id="assume-search" placeholder="Search assumptions…"'
        ' aria-label="Search assumptions">'
        + select("assume-phase", "Phase", [(p, p) for p in phases])
        + select("assume-impact", "Impact", [(i, i) for i in impacts])
        + select("assume-status", "Status", [(s, s) for s in statuses])
        + select("assume-kind", "Kind", [("permission", "Permissions")])
        + f'<span class="shown" id="assume-shown" aria-live="polite">{in_tab} of {in_tab}</span></div>'
        + '<h3>Needs your review</h3>'
        + (f'<p class="note">{len(permission) + len(review)} open, in the <a href="#needs-you">Needs you</a> '
           "panel beside every tab.</p>" if queue else '<p class="note">Nothing waits for your review.</p>')
        + "<h3>Waiting for the agent</h3>"
        + (group("Responded, not yet reconciled", agent, False) or '<p class="note">Nothing waits for the agent.</p>')
        + f'<h3>Recently changed</h3><div class="box"><ul class="plain">{recent_list}</ul></div>'
        + "<h3>Settled</h3>"
        + (group("Confirmed, revised, rejected, or withdrawn", settled, False) or '<p class="note">Nothing settled yet.</p>')
    )
    return tab, queue, len(permission) + len(review), len(agent)
