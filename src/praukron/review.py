"""The owner's review queue (ADR-054, ADR-055) and the side panel it lives in (ADR-065).

Everything here is laid out from compiled data. The static page is read-only;
only `praukron dashboard --serve` turns the cards into a form, and that form
writes through `praukron respond`, never into this page.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from .mission import approval_actions

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
details.acard > summary:hover { background: var(--panel-raised); }
.perm { padding: 6px 10px; border-radius: var(--radius-s); color: var(--ink);
        background: color-mix(in srgb, var(--warn) 12%, var(--panel)); }
.pill.permission { color: var(--warn); border-color: var(--warn); }
.resp { white-space: pre-wrap; padding: 6px 10px; margin: 6px 0; border-radius: var(--radius-s);
        background: var(--sunken); }
.resp .who { color: var(--muted); white-space: normal; }
.respond-hint { color: var(--muted); font-size: 13px; }
.aform { display: grid; gap: 6px; margin-top: 8px; }
.aform label { display: flex; gap: 8px; align-items: center; min-height: 24px; cursor: pointer; }
.aform input[type="radio"], .aform input[type="checkbox"] { accent-color: var(--accent); width: 16px; height: 16px; margin: 0; }
.aform select, .aform textarea { font: inherit; font-size: 14px; color: var(--ink); background: var(--panel);
        border: 1px solid var(--line-strong); border-radius: var(--radius-s); padding: 6px 10px; }
.aform textarea { min-height: 64px; resize: vertical; }
.review-bar button { cursor: pointer; font-weight: 600; min-height: 34px; padding: 5px 14px;
        border: 1px solid var(--accent); border-radius: var(--radius-s); background: var(--accent);
        color: var(--on-accent); }
.review-bar button:hover { filter: brightness(1.08); }
.review-result { font-size: 13px; flex: 1 0 100%; }
.review-result:empty { display: none; }
.approval-controls { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin-block: 8px; }
summary .approval-controls { flex: 1 0 100%; grid-column: 1 / -1; }
.approval-select { display: inline-flex; gap: 8px; align-items: center; min-height: 44px; cursor: pointer; }
.approval-select input { accent-color: var(--accent); width: 18px; height: 18px; margin: 0; }
.approve-one, .approve-selected { display: inline-flex; align-items: center; gap: 8px; min-height: 44px;
  padding: 8px 12px; font: inherit; font-weight: 600; border: 1px solid var(--accent);
  border-radius: var(--radius-s); color: var(--on-accent); background: var(--accent); cursor: pointer; }
.approve-one svg { width: 18px; height: 18px; flex-shrink: 0; }
.approve-one:hover, .approve-selected:hover { filter: brightness(1.08); }
.approval-controls button:disabled, .approval-controls input:disabled { opacity: .55; cursor: default; }
.approval-controls button:active:not(:disabled) { transform: translateY(1px); }
.approval-result, .approval-controls .respond-hint { flex: 1 0 100%; margin: 0; }
.approval-result:empty { display: none; }
/* Served: a card already answered in this sitting says so before it is submitted. */
details.acard.is-answered > summary::after, details.review-item.is-answered > summary::after, ul.owner-list > li.is-answered::after {
  content: var(--ui-answered); flex: 1 0 100%; font-size: 12px; font-weight: 600;
  color: var(--good); }
ul.owner-list > li.is-answered::after { display: block; margin-top: 6px; }
ul.owner-list > li.is-offpage::after { content: var(--ui-offpage); display: block;
  margin-top: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
"""

SCRIPT = """
// Assumptions: filter the review queue by values the compiler wrote.
const acards = [...document.querySelectorAll('#panel-review details.acard, #panel-assumptions details.acard')];
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
    if (e.verify) { if (e.checked) items.push({ target: e.target, action: 'VERIFY', text: e.text.trim() || 'Verified.' }); continue; }
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

// A form is answered when submitting now would record something from it:
// words for Guide and Feedback, an opened or reviewed card for OK. The live
// count uses this, so it never calls a card done that submit would refuse.
function formAnswered(e) {
  if (e.verify) return Boolean(e.checked);
  if (e.guide || e.choice === 'FEEDBACK') return Boolean(e.text.trim());
  return Boolean(e.seen || e.reviewed);
}
"""

# Only the served page carries this: the static file never contains a write.
SERVE_SCRIPT = DECIDE_JS + """
// Served review (praukron dashboard --serve). Present only when forms exist.
// A card left at OK records CONFIRM only if the owner opened it or marked it
// reviewed; an unopened card records nothing (ADR-055). Who answers is the
// active gh login, read by the server, never typed here (ADR-060).
const reviewBars = [...document.querySelectorAll('.review-bar')];
if (reviewBars.length) {
  const token = new URLSearchParams(location.search).get('token') || '';
  const selections = [...document.querySelectorAll('.approval-select input')];
  const batchButton = document.querySelector('.approve-selected');
  let approving = false;
  function updateSelection() {
    const count = selections.filter(input => input.checked).length;
    if (batchButton) batchButton.disabled = approving || count === 0;
    const countLabel = document.getElementById('approval-count');
    if (countLabel) countLabel.textContent = count + ' selected';
  }
  selections.forEach(input => input.addEventListener('change', updateSelection));
  document.querySelectorAll('.approval-controls').forEach(control => {
    control.addEventListener('click', event => event.stopPropagation());
    control.addEventListener('keydown', event => event.stopPropagation());
  });
  function approvalItem(control) {
    return {target: control.dataset.target, action: control.dataset.action,
      text: control.dataset.action === 'VERIFY' ? 'Verified.' : 'OK'};
  }
  async function approve(controls, out) {
    if (approving || !controls.length) return;
    approving = true;
    const writers = [...document.querySelectorAll('.approve-one, .approve-selected, .review-bar button, .approval-select input')];
    const disabled = writers.map(control => control.disabled);
    writers.forEach(control => { control.disabled = true; });
    out.textContent = 'Recording approval…';
    try {
      const response = await fetch('/respond', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Praukron-Token': token},
        body: JSON.stringify({items: controls.map(approvalItem), approveOnly: true})});
      const result = await response.json();
      if (!result.ok) throw new Error(result.error);
      const message = 'Recorded ' + result.ids.join(', ') + '. Reloading…';
      out.textContent = message;
      try { sessionStorage.setItem('praukron-approval-' + token, message.replace(' Reloading…', '')); } catch (_) {}
      location.reload();
    } catch (error) {
      out.textContent = error.message;
      approving = false;
      writers.forEach((control, index) => { control.disabled = disabled[index]; });
      updateSelection();
    }
  }
  document.querySelectorAll('.approve-one').forEach(button => button.addEventListener('click', () => {
    approve([button], button.closest('.approval-controls').querySelector('.approval-result'));
  }));
  if (batchButton) batchButton.addEventListener('click', () => {
    approve(selections.filter(input => input.checked), document.getElementById('approval-result'));
  });
  let recorded = '';
  try {
    recorded = sessionStorage.getItem('praukron-approval-' + token);
    sessionStorage.removeItem('praukron-approval-' + token);
  } catch (_) {}
  if (recorded) {
    // Announce after the refreshed document's live regions have mounted.
    setTimeout(() => document.querySelectorAll('.approval-result').forEach(out => { out.textContent = recorded; }), 100);
  }
  updateSelection();
  document.querySelectorAll('details.acard').forEach(card => card.addEventListener('toggle', () => { if (card.open) card.dataset.seen = '1'; }));
  document.querySelectorAll('.aform textarea').forEach(area => area.addEventListener('input', () => {
    const form = area.closest('.aform');
    const feedback = form.querySelector('input[value="FEEDBACK"]');
    if (feedback && area.value.trim()) feedback.checked = true;
  }));
  function entryOf(form) {
    const card = form.closest('details.acard');
    const radio = form.querySelector('input[type="radio"]:checked');
    const select = form.querySelector('select');
    const reviewed = form.querySelector('.reviewed');
    const verified = form.querySelector('.verify');
    return { target: form.dataset.target, guide: !!form.dataset.guide, verify: !!form.dataset.verify,
             checked: !!verified && verified.checked,
             text: form.querySelector('textarea').value,
             choice: radio ? radio.value : 'CONFIRM', action: select ? select.value : 'REVISE',
             seen: !!card && card.dataset.seen === '1', reviewed: !!reviewed && reviewed.checked };
  }
  function collect() {
    return reviewItems([...document.querySelectorAll('.aform')].map(entryOf));
  }
  // The panel is where the owner answers, so it is marked live, and its count
  // falls as items are answered by the same rule submit applies. An item with
  // no form (an owner-held blocker nobody here can guide) is work outside
  // this page: it is labelled, and never holds the count above zero.
  const needsPanel = document.getElementById('needs-you');
  const remaining = needsPanel && needsPanel.querySelector('.review-bar .remaining');
  if (remaining) {
    needsPanel.classList.add('is-live');
    const badge = needsPanel.querySelector('.needcount');
    const all = [...needsPanel.querySelectorAll('details.acard, details.review-item[data-actionable], ul.owner-list > li')]
      .filter(item => !item.parentElement.closest('details.review-item'));
    const items = all.filter(item => item.querySelector('.aform'));
    const elsewhere = all.length - items.length;
    all.forEach(item => item.classList.toggle('is-offpage', !item.querySelector('.aform')));
    const answered = item => [...item.querySelectorAll('.aform')].some(form => formAnswered(entryOf(form)));
    const recount = () => {
      let left = 0;
      items.forEach(item => {
        const done = answered(item);
        item.classList.toggle('is-answered', done);
        if (!done) left += 1;
      });
      const ownerCommands = all.filter(item => item.classList.contains('validation-action')).length;
      badge.textContent = left + ownerCommands;
      badge.classList.toggle('zero', !(left + ownerCommands));
      const outside = elsewhere ? ` · ${elsewhere} need${elsewhere === 1 ? 's' : ''} work outside this page` : '';
      remaining.textContent = (!items.length ? 'Nothing here takes an answer'
        : left ? `${left} of ${items.length} still need${left === 1 ? 's' : ''} a response`
        : `All ${items.length} answered. Submit to record them.`) + outside;
    };
    needsPanel.addEventListener('input', recount);
    needsPanel.addEventListener('change', recount);
    items.forEach(item => item.addEventListener('toggle', recount));
    recount();
  }
  reviewBars.forEach(bar => bar.querySelector('button').addEventListener('click', async () => {
    const out = bar.querySelector('.review-result');
    try {
      const items = collect();
      if (!items.length) throw new Error('Nothing to record: open a card to confirm it, or write feedback.');
      const response = await fetch('/respond', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Praukron-Token': token },
        body: JSON.stringify({ items }) });
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


def review_bar(interactive: bool, reviewer: str = "") -> str:
    """Identity, submit, and result; one per panel that holds a form."""
    if not interactive:
        return ""
    who = (f'<span class="review-by">Recording as GitHub user <strong>{esc(reviewer)}</strong></span>'
           if reviewer else '<span class="review-by">No GitHub login: run <code>gh auth login</code>, then reload.</span>')
    return ('<div class="controls review-bar">' + who
            + f'<button type="button"{"" if reviewer else " disabled"}>Submit review</button>'
            '<span class="remaining" aria-live="polite"></span>'
            '<span class="review-result" aria-live="polite"></span></div>')


def approval_control(target: str, action: str, interactive: bool, selectable: bool = True) -> str:
    """A direct approval and an independent, explicit batch selection."""
    disabled = '' if interactive else ' disabled'
    label = 'Verify' if action == 'VERIFY' else 'Confirm'
    attributes = f' data-target="{esc(target)}" data-action="{action}"'
    return ('<span class="approval-controls">'
            + (f'<label class="approval-select"><input type="checkbox"{attributes}{disabled}>'
               f'Select {esc(target)}</label>' if selectable else '')
            + f'<button type="button" class="approve-one"{attributes}{disabled}>'
            '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2">'
            '<path d="m5 12 4 4L19 6"/></svg>'
            f'{label} {esc(target)}</button>'
            + ('' if interactive else '<span class="respond-hint">To approve, run <code>praukron dashboard --serve</code>.</span>')
            + '<span class="approval-result" role="status" aria-live="polite"></span></span>')


def approval_bar(interactive: bool) -> str:
    return ('<div class="approval-controls approval-bar">'
            '<button type="button" class="approve-selected" disabled>Approve selected</button>'
            '<span id="approval-count" aria-live="polite">0 selected</span>'
            + ('' if interactive else '<span class="respond-hint">To approve, run <code>praukron dashboard --serve</code>.</span>')
            + '<span class="approval-result" id="approval-result" role="status" aria-live="polite"></span></div>')


def verification(compiled: dict, ref, interactive: bool = False) -> tuple[str, int]:
    """Open owner-held tasks, each offering the owner a governed VERIFY response."""
    tasks = {task["id"]: task for task in compiled["tasks"]}
    held = [task_id for task_id in compiled["assumptionReport"]["ownerHeld"] if task_id in tasks]
    if not held:
        return "", 0
    items, waiting = [], 0
    for task_id in held:
        verified = [response for response in compiled["responses"]
                    if response["target"] == task_id and response["action"] == "VERIFY"
                    and response["fromOwner"]]
        if verified:
            response = verified[-1]
            body = (f'<p class="note">Verified by {esc(response["by"])}, {esc(response["date"])} '
                    f'({esc(response["id"])}); waiting for the agent to record HUMAN_VERIFIED.</p>')
        else:
            waiting += 1
            body = (f'<div class="aform" data-target="{esc(task_id)}" data-verify="1">'
                    f'<label><input type="checkbox" class="verify"> I verified {esc(task_id)}</label>'
                    f'<textarea aria-label="What you verified on {esc(task_id)}" placeholder="What you '
                    'checked, recorded verbatim"></textarea></div>' if interactive else
                    '<span class="respond-hint">Verify: run <code>praukron dashboard --serve</code>, or '
                    f'<code>praukron respond {esc(task_id)} VERIFY</code> at a terminal.</span>')
        items.append(f'<li>{_pill(tasks[task_id]["status"])} {ref(task_id)}<br>{body}</li>')
    return ('<p class="note">Only an owner named in <code>Owners:</code> can verify these. '
            'An agent never does.</p>'
            f'<ul class="plain owner-list">{"".join(items)}</ul>'), waiting


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


def panel_body(compiled: dict, ref, interactive: bool = False, mission: bool = False) -> tuple[str, str, int, int]:
    """The Assumptions tab, and the queue the side panel shows instead of it.

    Returns the tab body, the queue markup, how many cards the queue holds, and
    how many answered assumptions wait for the agent. The queue's cards render
    only in the side panel, never also in the tab (ADR-065)."""
    assumptions = compiled["assumptions"]
    report = compiled["assumptionReport"]
    if not assumptions and not mission:
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
        badges = (f'{_pill(a["impact"])} {_pill(a["status"])}'
                  + (' <span class="pill permission">Permission</span>' if a["permissions"] else "")
                  + (' <span class="pill">awaiting the agent</span>' if a["id"] in waiting else ""))
        summary = f'<code>{esc(a["id"])}</code> {esc(a["title"])} {badges}'
        impact = ''
        if mission:
            item = next((item for items in compiled["mission"]["review"].values() for item in items
                         if item["kind"] == "assumption" and item["id"] == a["id"]), None)
            if item:
                impact = (f'<p>{len(item["affected"])} affected unfinished tasks: '
                          + (', '.join(ref(t) for t in item["affected"]) or 'none') + '</p>')
            why = ('Owner response awaits agent reconciliation.' if a["id"] in waiting else
                   'Access rule needs owner validation.' if a["permissions"] and a["status"] == "OPEN" else
                   'Provisional choice needs owner validation.' if a["status"] == "OPEN" else
                   'Previously answered assumption; retained for reference.')
            primary = ('Inspect reconciliation' if a["id"] in waiting else
                       'Review response options' if a["status"] in ("OPEN", "CONFIRMED") else 'Inspect record')
            affected = ', '.join(a["tasks"][:2]) or 'No task linked'
            if len(a["tasks"]) > 2:
                affected += f' + {len(a["tasks"]) - 2} more'
            downstream = len(set(item["affected"]) - set(a["tasks"])) if item else 0
            summary = (f'<strong class="review-title">{esc(a["title"])}</strong>'
                       f'<span class="review-why">{why}</span>'
                       f'<span class="review-target">Affects: {esc(affected)} · {downstream} downstream</span>'
                       f'<span class="review-action">{primary}</span>')
        if a["id"] in approval_actions(compiled):
            summary += approval_control(a["id"], "CONFIRM", interactive)
        return (
            '<details class="acard"' + (f' id="assumption-{esc(a["id"])}"' if anchor else "")
            + f' data-id="{esc(a["id"])}" data-impact="{esc(a["impact"])}" data-status="{esc(a["status"])}"'
            f' data-phase="{esc(" ".join(a["phases"]))}" data-kind="{kind}" data-text="{esc(text)}">'
            f'<summary>{summary}</summary><div class="abody">'
            + (f'<p><code>{esc(a["id"])}</code> {badges}</p>{action}{impact}' if mission else '')
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
            + f'<p><a href="../chronicle/{quote(a["source"]["file"])}#{quote(a["source"]["anchor"])}">'
              f'{esc(a["source"]["file"])}#{esc(a["source"]["anchor"])}</a></p>'
            + f"<div><strong>Responses</strong>{said}</div>" + ('' if mission else action) + '</div></details>'
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

    if mission:
        by_id = {a["id"]: a for a in assumptions}

        def task_card(item: dict, group: str) -> str:
            tid = item["id"]
            task = tasks[tid]
            subset = {**compiled, "assumptionReport": {**report, "ownerHeld": [tid]}}
            verifying = item["action"] == "Verify"
            verification_body, _ = verification(subset, ref, interactive and group == "now" and verifying)
            body = ((verification_body if verifying else '') + guide(tid, interactive) if group == "now" else
                    verification_body if group == "answered" else
                    '<p class="note">Future obligation. Finish dependencies and reach the relevant phase first.</p>')
            body += '<p>Dependencies: ' + (", ".join(ref(t) for t in task["deps"]) or "none") + '</p>'
            body += '<p>Affected work: ' + (", ".join(ref(t) for t in item["affected"]) or "none") + '</p>'
            body += f'<p class="note">Owner authority · {len(item["affected"])} downstream tasks</p>'
            body += f'<p>Evidence: {esc(task["evidence"] or "none recorded")}</p>'
            body += '<p>Review acceptance and decisions: ' + ref(tid) + '</p>'
            body += ''.join(f'<p class="resp">{esc(r["text"])} · {esc(r["id"])}</p>'
                            for r in compiled["responses"] if r["target"] == tid)
            why = item.get("note") or {
                "now": ("The contract is met; owner verification is required." if verifying else
                        "The contract is not yet met; the owner's decision is needed."),
                "upcoming": "Owner decision awaits dependencies or its phase.",
                "answered": "Owner verification awaits agent reconciliation.",
            }[group]
            primary = item["action"] if group == "now" else (
                "Inspect prerequisites" if group == "upcoming" else "Inspect reconciliation")
            return (f'<details class="review-item" id="review-{esc(tid)}"' + (' data-actionable' if group == "now" else '') + '><summary>'
                    f'<strong class="review-title">{esc(task["title"])}</strong>'
                    f'<span class="review-why">{esc(why)}</span>'
                    f'<span class="review-target">Affects: {esc(tid)} · {len(item["affected"])} downstream</span>'
                    f'<span class="review-action">{primary}</span>'
                    + (approval_control(tid, "VERIFY", interactive) if group == "now" and verifying else '') +
                    f'</summary><div class="review-body">{body}</div></details>')

        def validation_card(item: dict) -> str:
            finding = item["finding"]
            why = finding["message"].split(';', 1)[0]
            commands = ''.join(f'<pre class="owner-command"><code>{esc(command)}</code></pre>'
                               for command in finding["ownerAction"])
            return (f'<details class="review-item validation-action" data-actionable id="review-{esc(item["id"])}"><summary>'
                    f'<strong class="review-title">{esc(item["title"])}</strong>'
                    f'<span class="review-why">{esc(why)}</span>'
                    f'<span class="review-target">Affects: {esc(item["target"])} · {len(item["affected"])} downstream</span>'
                    '<span class="review-action">Run owner command</span></summary>'
                    f'<div class="review-body"><p>{esc(finding["message"])}</p>{commands}'
                    f'<p>Validation: {esc(finding["code"])} · {esc(finding["where"])}</p>'
                    f'<p>Affected record: {ref(item["target"])}</p>'
                    '<p>Affected work: ' + (', '.join(ref(t) for t in item["affected"]) or 'none') + '</p></div></details>')

        sections = []
        for key, label in (("now", "Actionable now"), ("upcoming", "Upcoming decisions"),
                           ("assumptions", "Assumptions requiring validation"), ("answered", "Previously answered")):
            items = compiled["mission"]["review"][key]
            body = ''.join(card(by_id[i["id"]]) if i["kind"] == "assumption" else
                           validation_card(i) if i["kind"] == "validation" else task_card(i, key) for i in items)
            if items:
                sections.append(f'<section class="review-group" aria-label="{label}"><h2>{label} <span class="count">{len(items)}</span></h2>{body}</section>')
            else:
                sections.append(f'<p class="review-empty">{label} <span class="count">0</span> · No items.</p>')
        filters = ('<div class="controls"><input type="search" id="assume-search" aria-label="Search assumptions" placeholder="Filter assumptions…">'
                   + ''.join(f'<select id="assume-{key}" aria-label="{label}"><option value="">{label}: all</option>'
                             + ''.join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values) + '</select>'
                             for key, label, values in (
                                 ("phase", "Phase", sorted({p for a in assumptions for p in a["phases"]})),
                                 ("impact", "Impact", ["HIGH", "MEDIUM", "LOW"]),
                                 ("status", "Status", ["OPEN", "CONFIRMED", "REVISED", "REJECTED", "WITHDRAWN"]),
                                 ("kind", "Kind", ["permission"])))
                   + f'<span id="assume-shown" aria-live="polite">{len(assumptions)} assumptions</span></div>')
        tools = ('<details class="review-tools"><summary>Filter assumptions and review order</summary>'
                 + (filters if assumptions else '<p class="note">No assumptions to filter.</p>')
                 + '<p class="note">Ordered by immediate impact, recorded permission or HIGH-impact sensitivity, then downstream cost. '
                 'Permission or HIGH-impact sensitivity is the recorded proxy for irreversible consequences.</p></details>')
        return ('<p class="note">Owner decisions are separate from agent work. Assumptions are provisional, never approval.</p>'
                + approval_bar(interactive) + tools + ''.join(sections), "", len(compiled["mission"]["review"]["now"])
                + len(compiled["mission"]["review"]["assumptions"]), len(waiting))

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
