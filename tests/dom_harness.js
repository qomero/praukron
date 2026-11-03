// A minimal DOM for running the served page script against a rendered page.
// Input (stdin, JSON): {tree, script, token, scenario}. The tree is the page
// parsed by Python as [tag, attrs, children] with strings for text. Output
// (stdout, JSON) is what the scenario observed. Only the APIs the review
// script uses are implemented.
'use strict';

const VOID = new Set(['input', 'br', 'hr', 'img', 'meta', 'link', 'source', 'wbr', 'col', 'area']);

class Text {
  constructor(data) { this.data = data; this.parentNode = null; }
  get textContent() { return this.data; }
}

class Element {
  constructor(doc, tag, attrs) {
    this.ownerDocument = doc;
    this.tagName = tag;
    this.attrs = new Map(Object.entries(attrs));
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = {};
    this._checked = this.attrs.has('checked');
    this._value = null;
    const el = this;
    this.dataset = new Proxy({}, {
      get(_, key) {
        const name = 'data-' + String(key).replace(/[A-Z]/g, c => '-' + c.toLowerCase());
        return el.attrs.has(name) ? el.attrs.get(name) : undefined;
      },
      set(_, key, value) {
        el.attrs.set('data-' + String(key).replace(/[A-Z]/g, c => '-' + c.toLowerCase()), String(value));
        return true;
      },
    });
    this.classList = {
      contains: name => el.classes().includes(name),
      add: name => { if (!el.classes().includes(name)) el.attrs.set('class', [...el.classes(), name].join(' ')); },
      remove: name => el.attrs.set('class', el.classes().filter(c => c !== name).join(' ')),
      toggle: (name, force) => {
        const on = force === undefined ? !el.classes().includes(name) : Boolean(force);
        on ? el.classList.add(name) : el.classList.remove(name);
        return on;
      },
    };
  }
  classes() { return (this.attrs.get('class') || '').split(/\s+/).filter(Boolean); }
  get id() { return this.attrs.get('id') || ''; }
  get parentElement() { return this.parentNode instanceof Element ? this.parentNode : null; }
  get children() { return this.childNodes.filter(n => n instanceof Element); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  removeAttribute(name) { this.attrs.delete(name); }
  hasAttribute(name) { return this.attrs.has(name); }
  flag(name, on) { on ? this.attrs.set(name, '') : this.attrs.delete(name); }
  get hidden() { return this.attrs.has('hidden'); }
  set hidden(on) { this.flag('hidden', on); }
  get open() { return this.attrs.has('open'); }
  set open(on) { this.flag('open', on); }
  get disabled() { return this.attrs.has('disabled'); }
  set disabled(on) {
    this.flag('disabled', on);
    // The focus fixup rule: a control that becomes disabled loses focus.
    if (on && this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body;
  }
  get checked() { return this._checked; }
  set checked(on) { this._checked = Boolean(on); }
  get value() {
    if (this._value !== null) return this._value;
    if (this.tagName === 'textarea') return this.textContent;
    if (this.tagName === 'select') {
      const options = this.querySelectorAll('option');
      const chosen = options.find(o => o.hasAttribute('selected')) || options[0];
      return chosen ? chosen.getAttribute('value') ?? chosen.textContent : '';
    }
    return this.attrs.get('value') ?? (this.attrs.get('type') === 'checkbox' ? 'on' : '');
  }
  set value(v) { this._value = String(v); }
  get textContent() { return this.childNodes.map(n => n.textContent).join(''); }
  set textContent(text) {
    this.childNodes = [];
    if (text !== '') this.appendChild(new Text(String(text)));
  }
  appendChild(node) { node.parentNode = this; this.childNodes.push(node); return node; }
  descendants() {
    const out = [];
    const walk = el => el.children.forEach(child => { out.push(child); walk(child); });
    walk(this);
    return out;
  }
  querySelectorAll(selector) { return this.descendants().filter(el => matches(el, selector)); }
  querySelector(selector) { return this.descendants().find(el => matches(el, selector)) || null; }
  matches(selector) { return matches(this, selector); }
  closest(selector) {
    for (let el = this; el; el = el.parentElement) if (matches(el, selector)) return el;
    return null;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (let el = this; el; el = el.parentNode) {
      (el.listeners[event.type] || []).forEach(fn => fn.call(el, event));
      if (!event.bubbles) break;
    }
    return true;
  }
  focus() { if (!this.disabled) this.ownerDocument.activeElement = this; }
  click() {
    if (this.disabled) return;
    if (this.tagName === 'input' && /^(checkbox|radio)$/.test(this.attrs.get('type'))) {
      this.checked = this.attrs.get('type') === 'radio' ? true : !this.checked;
      this.dispatchEvent({type: 'click', bubbles: true});
      this.dispatchEvent({type: 'input', bubbles: true});
      this.dispatchEvent({type: 'change', bubbles: true});
      return;
    }
    this.dispatchEvent({type: 'click', bubbles: true});
  }
}

// Selectors: lists, descendant and child combinators, and compounds of a tag,
// #id, .class, [attr], [attr="value"] and :checked.
function splitTop(text, sep) {
  const parts = [];
  let depth = 0, quote = '', start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) { if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'") quote = c;
    else if (c === '[' || c === '(') depth++;
    else if (c === ']' || c === ')') depth--;
    else if (c === sep && !depth) { parts.push(text.slice(start, i)); start = i + 1; }
  }
  parts.push(text.slice(start));
  return parts.map(p => p.trim()).filter(Boolean);
}

function parseComplex(text) {
  const tokens = [];
  let i = 0, buf = '', depth = 0, quote = '';
  const flush = () => { if (buf.trim()) tokens.push(buf.trim()); buf = ''; };
  for (; i < text.length; i++) {
    const c = text[i];
    if (quote) { buf += c; if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'") { quote = c; buf += c; continue; }
    if (c === '[') depth++;
    if (c === ']') depth--;
    if (!depth && c === '>') { flush(); tokens.push('>'); continue; }
    if (!depth && /\s/.test(c)) { flush(); continue; }
    buf += c;
  }
  flush();
  const steps = [];
  let combinator = ' ';
  for (const token of tokens) {
    if (token === '>') { combinator = '>'; continue; }
    steps.push({combinator, compound: token});
    combinator = ' ';
  }
  return steps;
}

function matchCompound(el, compound) {
  const re = /([#.]?[\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|:([\w-]+)/g;
  let m;
  while ((m = re.exec(compound))) {
    if (m[1]) {
      const part = m[1];
      if (part[0] === '#') { if (el.id !== part.slice(1)) return false; }
      else if (part[0] === '.') { if (!el.classList.contains(part.slice(1))) return false; }
      else if (part !== '*' && el.tagName !== part.toLowerCase()) return false;
    } else if (m[2]) {
      if (!el.hasAttribute(m[2])) return false;
      if (m[3] !== undefined && el.getAttribute(m[2]) !== m[3]) return false;
    } else if (m[4] === 'checked') {
      if (!el.checked) return false;
    } else {
      throw new Error('unsupported selector: ' + compound);
    }
  }
  return true;
}

function matchSteps(el, steps, index) {
  if (!matchCompound(el, steps[index].compound)) return false;
  if (index === 0) return true;
  if (steps[index].combinator === '>') {
    return Boolean(el.parentElement) && matchSteps(el.parentElement, steps, index - 1);
  }
  for (let up = el.parentElement; up; up = up.parentElement) if (matchSteps(up, steps, index - 1)) return true;
  return false;
}

function matches(el, selector) {
  return splitTop(selector, ',').some(complex => {
    const steps = parseComplex(complex);
    return matchSteps(el, steps, steps.length - 1);
  });
}

function build(tree) {
  const doc = {activeElement: null};
  const make = node => {
    if (typeof node === 'string') return new Text(node);
    const [tag, attrs, children] = node;
    const el = new Element(doc, tag, attrs);
    if (!VOID.has(tag)) children.forEach(child => el.appendChild(make(child)));
    return el;
  };
  const root = make(tree);
  doc.documentElement = root;
  doc.body = root.querySelector('body') || root;
  doc.activeElement = doc.body;
  doc.getElementById = id => root.querySelector('#' + id);
  doc.querySelector = s => root.querySelector(s);
  doc.querySelectorAll = s => root.querySelectorAll(s);
  doc.addEventListener = (...args) => root.addEventListener(...args);
  return doc;
}

// Shown when the action started in `tab`: nothing hides it, and it is not in
// a workspace panel other than that tab's (inactive panels are display: none).
function exposed(el, tab) {
  for (let up = el; up; up = up.parentElement) {
    if (up.hidden) return false;
    if (up.classList.contains('panel') && up.id !== 'panel-' + tab) return false;
  }
  return true;
}

function live(el) {
  return el.getAttribute('role') === 'status' || el.hasAttribute('aria-live');
}

function describe(el) {
  if (!el || !(el instanceof Element)) return null;
  return {tag: el.tagName, id: el.id, className: el.getAttribute('class') || '',
          target: el.getAttribute('data-target'), disabled: el.disabled};
}

async function settle(timers) {
  for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve));
  while (timers.length) { timers.shift()(); await new Promise(resolve => setImmediate(resolve)); }
}

async function load(input, session, respond) {
  const doc = build(input.tree);
  const timers = [];
  const location = {search: '?token=' + input.token, reloaded: false, reload() { this.reloaded = true; }};
  const sessionStorage = {
    getItem: key => (key in session ? session[key] : null),
    setItem: (key, value) => { session[key] = String(value); },
    removeItem: key => { delete session[key]; },
  };
  const fetch = async () => ({json: async () => respond()});
  const run = new Function('document', 'location', 'sessionStorage', 'fetch', 'setTimeout', input.script);
  run(doc, location, sessionStorage, fetch, fn => { timers.push(fn); return timers.length; });
  await settle(timers);
  return {doc, location, timers};
}

async function main() {
  const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
  const {kind, tab, outcome} = input.scenario;
  const session = {};
  const respond = () => (outcome === 'ok' ? {ok: true, ids: ['R-3']} : {ok: false, error: 'Refused for the test.'});
  const first = await load(input, session, respond);
  const panel = first.doc.getElementById('panel-' + tab);
  let starter;
  if (kind === 'one') {
    starter = panel.querySelector('.approve-one');
  } else {
    panel.querySelector('.approval-select input').click();
    starter = panel.querySelector('.approve-selected');
  }
  const result = {starter: describe(starter), starterExposed: exposed(starter, tab)};
  starter.click();
  await settle(first.timers);
  result.reloaded = first.location.reloaded;
  result.active = describe(first.doc.activeElement);
  result.activeIsStarter = first.doc.activeElement === starter;
  let doc = first.doc;
  if (first.location.reloaded) doc = (await load(input, session, respond)).doc;
  const all = doc.documentElement.descendants();
  result.regions = all.filter(el => live(el) && el.textContent.includes('Recorded R-3.'))
    .map(el => Object.assign(describe(el), {exposed: exposed(el, tab), text: el.textContent}));
  result.refusals = all.filter(el => el.textContent === 'Refused for the test.').map(describe);
  process.stdout.write(JSON.stringify(result));
}

main().catch(error => { process.stderr.write(String(error && error.stack || error)); process.exit(1); });
