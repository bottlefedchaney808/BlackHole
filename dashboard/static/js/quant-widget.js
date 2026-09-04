/*
 * quant-widget.js — the <quant-widget slug="..."> custom element.
 *
 * Phase 4 of the widget-native-quant-console plan, steps 1-3. First real
 * shared frontend module for the dashboard: one vanilla-JS custom element,
 * instantiable anywhere a page includes it, no React, no bundler.
 *
 * A widget owns a local {ticker, expiry, basket} scope. When its `synced`
 * attribute is on (the default) it mirrors the page-global scope bus
 * (./sync-bus.js): bus changes update its inputs, and typing in its inputs
 * while synced publishes back to the bus so sibling widgets follow. Toggling
 * sync off detaches the listener and the inputs become purely local.
 *
 * The Run button POSTs to the Phase-3 route
 *   POST /api/widgets/{slug}/run        body {scope:{ticker,expiry,basket}, params?}
 * and renders the ModuleResult JSON body through the generic output_kind
 * renderers (./widget-renderers.js). output_kind resolution order:
 * catalog entry (GET /api/widgets/catalog, fetched once per page) ->
 * data-output-kind attribute -> 'metrics'. If the catalog is not reachable
 * (404/offline) the widget still works from data-* attributes alone.
 *
 * Attributes:
 *   slug              required, stable module id (e.g. "leisen_reimer")
 *   synced            boolean, default true ("false"/"0"/"off" disables)
 *   data-output-kind  renderer override when the catalog is unavailable
 *   data-context      optional JSON object of extra run fields; keys named
 *                     ticker/expiry/basket override the scope, everything
 *                     else is sent as module params under body.params
 *
 * DOM events (dispatched on the element, bubbles, composed):
 *   qw:run-start   detail {slug, scope}
 *   qw:run-success detail {slug, scope, result}
 *   qw:run-error   detail {slug, scope, error}
 *   qw:sync-change detail {synced}
 *
 * Instance API:
 *   widget.run()            run with the current input values
 *   widget.scope()          current scope object
 *   widget.setSynced(bool)  programmatic toggle (same as the attribute)
 *   widget.outputKind()     resolved renderer kind
 *   widget.lastResult       last ModuleResult-shaped body or null
 *   widget.state            'idle' | 'running' | 'ready' | 'error'
 *
 * Requires a browser that supports custom elements v1 + shadow DOM (any
 * modern Chromium/Firefox/Safari). Loading this module registers the
 * element (idempotent).
 */

import { syncBus } from './sync-bus.js';
import { renderResult, esc } from './widget-renderers.js';

const SCOPE_FIELDS = ['ticker', 'expiry', 'basket'];

// ---------------------------------------------------------------------------
// Catalog — fetched once per page, shared by every widget instance.
// ---------------------------------------------------------------------------

let _catalogPromise = null;

function coerceCatalog(body) {
  if (!body || typeof body !== 'object') return [];
  if (Array.isArray(body)) return body;
  if (Array.isArray(body.entries)) return body.entries;
  if (Array.isArray(body.widgets)) return body.widgets;
  if (Array.isArray(body.modules)) return body.modules;
  // Object keyed by slug.
  const out = [];
  Object.keys(body).forEach(function (k) {
    if (body[k] && typeof body[k] === 'object' && typeof body[k].slug === 'string') {
      out.push(body[k]);
    }
  });
  return out;
}

/**
 * Fetch the widget catalog once per page. Never rejects: any failure
 * (including the Phase-3 route not being live yet) resolves to [] so
 * widgets degrade gracefully to data-* attributes.
 */
export function loadCatalog() {
  if (!_catalogPromise) {
    _catalogPromise = fetch('/api/widgets/catalog', { headers: { Accept: 'application/json' } })
      .then(function (res) {
        if (!res.ok) return [];
        return res.json().then(coerceCatalog);
      })
      .catch(function () {
        return [];
      });
  }
  return _catalogPromise;
}

export function catalogEntryFor(slug) {
  return loadCatalog().then(function (entries) {
    for (let i = 0; i < entries.length; i++) {
      if (entries[i] && entries[i].slug === slug) return entries[i];
    }
    return null;
  });
}

// ---------------------------------------------------------------------------
// Element chrome styles. Palette matches base.html's :root variables and
// falls back to the same dark values when the host page doesn't define them
// (custom properties pierce the shadow boundary, so a page that does define
// them wins). Renderer classes (.pill, .metric-grid, .dist-chart, pre.json,
// ...) are styled here too so widgets are self-contained; on a page that
// includes base.html the same names would style renderer output rendered
// outside a shadow root.
// ---------------------------------------------------------------------------

const SHADOW_CSS = `
:host {
  --qw-bg: var(--panel, #0c1122);
  --qw-bg2: var(--panel-2, #121a30);
  --qw-border: var(--border, #24304f);
  --qw-text: var(--text, #f8fafc);
  --qw-muted: var(--muted, #94a3b8);
  --qw-accent: var(--accent, #3b82f6);
  --qw-accent2: var(--accent-2, #38bdf8);
  --qw-accent-soft: var(--accent-soft, #3b82f622);
  --qw-accent-glow: var(--accent-glow, #3b82f699);
  --qw-ok: var(--ok, #34d399);
  --qw-ok-soft: var(--ok-soft, #34d39922);
  --qw-warn: var(--warn, #fbbf24);
  --qw-warn-soft: var(--warn-soft, #fbbf2422);
  --qw-err: var(--err, #f87171);
  --qw-err-soft: var(--err-soft, #f8717122);
  --qw-mono: var(--mono, ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace);
  display: block;
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: var(--qw-text);
  text-align: left;
}
* { box-sizing: border-box; }
.qw-panel {
  background: var(--qw-bg);
  border: 1px solid var(--qw-border);
  border-radius: 12px;
  overflow: hidden;
}
.qw-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--qw-bg2);
  border-bottom: 1px solid var(--qw-border);
}
.qw-title { font-weight: 650; letter-spacing: -0.01em; }
.qw-title .qw-slug { color: var(--qw-muted); font-weight: 400; font-size: 12px; }
.qw-toggle {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--qw-muted); font-size: 12px; cursor: pointer;
  user-select: none; white-space: nowrap;
}
.qw-toggle input { accent-color: var(--qw-accent); cursor: pointer; }
.qw-desc { color: var(--qw-muted); font-size: 12px; padding: 8px 14px 0; }
.qw-scope {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px; padding: 12px 14px 4px;
}
.qw-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.qw-field label { color: var(--qw-muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
.qw-field input {
  font: inherit; color: var(--qw-text); background: var(--qw-bg2);
  border: 1px solid var(--qw-border); border-radius: 7px; padding: 6px 9px;
  min-width: 0; width: 100%;
}
.qw-field input:focus { outline: 2px solid var(--qw-accent-soft); border-color: var(--qw-accent); }
.qw-field input:disabled { opacity: .5; }
.qw-runrow {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 8px 14px 12px;
}
.qw-run {
  background: linear-gradient(135deg, var(--qw-accent2), var(--qw-accent));
  border: 1px solid transparent; color: #fff; border-radius: 7px;
  font: inherit; font-weight: 700; cursor: pointer; padding: 7px 16px;
  box-shadow: 0 0 16px -2px var(--qw-accent-glow), 0 2px 12px -4px var(--qw-accent);
  transition: filter .15s ease, transform .1s ease, box-shadow .15s ease;
}
.qw-run:hover { filter: brightness(1.1); transform: translateY(-1px); }
.qw-run:disabled { opacity: .55; cursor: not-allowed; filter: none; transform: none; }
.qw-state { color: var(--qw-muted); font-size: 12px; }
.qw-state.ok { color: var(--qw-ok); }
.qw-state.err { color: var(--qw-err); }
.qw-out { padding: 0 14px 14px; }
.qw-empty {
  padding: 18px 12px; text-align: center; color: var(--qw-muted);
  border: 1px dashed var(--qw-border); border-radius: 8px; font-size: 12px;
}
/* ---- renderer vocabulary (self-contained copy of base.html rules) ---- */
.pill {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: .02em;
  border: 1px solid transparent; white-space: nowrap;
}
.pill.ok { background: var(--qw-ok-soft); color: var(--qw-ok); }
.pill.partial, .pill.running, .pill.queued { background: var(--qw-warn-soft); color: var(--qw-warn); }
.pill.error, .pill.timeout, .pill.failed { background: var(--qw-err-soft); color: var(--qw-err); }
.pill.plain { background: var(--qw-accent-soft); color: var(--qw-accent); border-color: var(--qw-accent); }
.errbox {
  padding: 9px 12px; border-radius: 8px; margin: 8px 0 0;
  background: var(--qw-err-soft); color: var(--qw-err);
  border: 1px solid var(--qw-border); font-family: var(--qw-mono); font-size: 12px;
  word-break: break-word;
}
.small { font-size: 12px; }
.muted { color: var(--qw-muted); }
.empty {
  padding: 18px 12px; text-align: center; color: var(--qw-muted);
  border: 1px dashed var(--qw-border); border-radius: 8px; margin: 4px 0;
}
.qw-result-head { display: flex; align-items: baseline; gap: 8px; margin: 10px 0 6px; }
.qw-result-title { color: var(--qw-muted); }
.qw-headline { margin: 6px 0 0; color: var(--qw-text); }
.qw-warnings { margin: 6px 0 0; padding-left: 18px; color: var(--qw-warn); }
.metric-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px; margin-top: 8px;
}
.metric-card {
  background: var(--qw-bg2); border: 1px solid var(--qw-border);
  border-radius: 8px; padding: 8px 10px;
}
.metric-label {
  color: var(--qw-muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .06em; word-break: break-word;
}
.metric-value {
  font-size: 16px; font-weight: 620; margin-top: 4px;
  letter-spacing: -0.02em; word-break: break-word; font-family: var(--qw-mono);
}
.qw-sub { margin-top: 8px; border: 1px solid var(--qw-border); border-radius: 8px; background: var(--qw-bg2); }
.qw-sub > summary {
  cursor: pointer; padding: 6px 10px; font-size: 12px; color: var(--qw-muted);
  text-transform: uppercase; letter-spacing: .05em; user-select: none;
}
.qw-sub[open] > summary { border-bottom: 1px solid var(--qw-border); }
.qw-sub .metric-grid { margin: 8px; }
.qw-rows { margin-top: 8px; }
.qw-rows > .small { margin: 0 0 4px; text-transform: uppercase; letter-spacing: .05em; }
.tablewrap { overflow-x: auto; max-width: 100%; border: 1px solid var(--qw-border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--qw-border); white-space: nowrap; }
th {
  background: var(--qw-bg); color: var(--qw-muted); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .05em;
}
tbody tr:last-child td { border-bottom: none; }
td.num { text-align: right; font-family: var(--qw-mono); }
pre.json {
  background: var(--qw-bg2); border: 1px solid var(--qw-border); border-radius: 8px;
  padding: 10px; overflow: auto; max-height: 420px; font-size: 12px;
  margin: 8px 0 0; font-family: var(--qw-mono); white-space: pre-wrap; word-break: break-word;
}
.dist-chart { margin: 10px 0 0; }
.dist-chart-title { color: var(--qw-muted); margin-bottom: 2px; }
.dist-chart svg { display: block; background: var(--qw-bg2); border: 1px solid var(--qw-border); border-radius: 6px; }
.dist-bar { fill: var(--qw-accent); }
.dist-percentiles { margin-top: 3px; color: var(--qw-muted); }
.qw-artifacts { margin-top: 10px; }
.qw-artifacts figure { margin: 0 0 10px; }
.qw-artifact-img {
  width: 100%; border-radius: 8px; border: 1px solid var(--qw-border);
  background: var(--qw-bg2); display: block;
}
.qw-artifacts figcaption { margin-top: 3px; }
.qw-artifact-note { margin-top: 6px; word-break: break-word; }
`;

function syncAttrEnabled(el) {
  const attr = el.getAttribute('synced');
  if (attr === null) return true; // default: synced
  const v = String(attr).trim().toLowerCase();
  return !(v === 'false' || v === '0' || v === 'no' || v === 'off');
}

function splitBasket(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== 'string') return [];
  return value.split(',').map(function (t) { return t.trim(); })
    .filter(function (t) { return t.length > 0; });
}

function joinBasket(basket) {
  return (basket || []).join(', ');
}

function parseDataContext(el) {
  const raw = el.getAttribute('data-context');
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (e) {
    console.warn('quant-widget: data-context is not valid JSON, ignoring', raw);
    return null;
  }
}

class QuantWidget extends HTMLElement {
  static get observedAttributes() {
    return ['slug', 'synced', 'data-output-kind'];
  }

  constructor() {
    super();
    this._unsub = null;
    this._catalog = null;
    this._catalogLoaded = false;
    this._hasRun = false;
    this._state = 'idle';
    this._lastResult = null;
    this._lastError = null;
    this._built = false;
    this.attachShadow({ mode: 'open' });
  }

  // -- lifecycle ----------------------------------------------------------

  connectedCallback() {
    if (!this.isConnected) return;
    if (!this._built) this._build();
    this._resync();
    this._loadCatalog();
    if (this._syncEnabled()) this._maybeFetchState();
  }

  disconnectedCallback() {
    this._detach();
  }

  attributeChangedCallback(name) {
    if (name === 'synced' && this._built && this.isConnected) {
      this._resync();
    }
    if (name === 'data-output-kind' && this._built) {
      this._renderPlaceholder();
    }
    if (name === 'slug' && this._built) {
      this._renderHeader();
      this._loadCatalog();
    }
  }

  _syncEnabled() {
    return syncAttrEnabled(this);
  }

  _build() {
    const root = this.shadowRoot;
    const style = document.createElement('style');
    style.textContent = SHADOW_CSS;
    root.appendChild(style);

    const panel = document.createElement('div');
    panel.className = 'qw-panel';

    // header: title + sync toggle
    const head = document.createElement('div');
    head.className = 'qw-head';
    const title = document.createElement('div');
    title.className = 'qw-title';
    head.appendChild(title);

    const toggleLabel = document.createElement('label');
    toggleLabel.className = 'qw-toggle';
    toggleLabel.title = 'When on, scope follows the page-global ticker/expiry/basket bus';
    this._syncBox = document.createElement('input');
    this._syncBox.type = 'checkbox';
    toggleLabel.appendChild(this._syncBox);
    const toggleText = document.createElement('span');
    toggleText.textContent = 'synced';
    toggleLabel.appendChild(toggleText);
    head.appendChild(toggleLabel);
    panel.appendChild(head);

    const desc = document.createElement('div');
    desc.className = 'qw-desc';
    panel.appendChild(desc);

    // scope inputs
    const scopeRow = document.createElement('div');
    scopeRow.className = 'qw-scope';
    this._inputs = {};
    SCOPE_FIELDS.forEach((field) => {
      const wrap = document.createElement('div');
      wrap.className = 'qw-field';
      const label = document.createElement('label');
      label.textContent = field;
      label.setAttribute('for', 'qw-' + field);
      const input = document.createElement('input');
      input.id = 'qw-' + field;
      input.className = 'qw-' + field + '-input';
      input.autocomplete = 'off';
      input.spellcheck = false;
      if (field === 'basket') input.placeholder = 'SPY, QQQ';
      if (field === 'ticker') input.placeholder = 'SPY';
      if (field === 'expiry') input.placeholder = '2026-12-18';
      input.addEventListener('input', this._onInputEdit.bind(this));
      wrap.appendChild(label);
      wrap.appendChild(input);
      scopeRow.appendChild(wrap);
      this._inputs[field] = input;
    });
    panel.appendChild(scopeRow);

    // run row
    const runRow = document.createElement('div');
    runRow.className = 'qw-runrow';
    this._runBtn = document.createElement('button');
    this._runBtn.type = 'button';
    this._runBtn.className = 'qw-run';
    this._runBtn.textContent = 'Run';
    this._runBtn.addEventListener('click', function () { this.run(); }.bind(this));
    runRow.appendChild(this._runBtn);
    this._stateEl = document.createElement('span');
    this._stateEl.className = 'qw-state';
    runRow.appendChild(this._stateEl);
    panel.appendChild(runRow);

    // output
    this._out = document.createElement('div');
    this._out.className = 'qw-out';
    panel.appendChild(this._out);

    root.appendChild(panel);
    this._built = true;

    this._syncBox.addEventListener('change', function () {
      this.setSynced(this._syncBox.checked);
    }.bind(this));

    this._renderHeader();
    this._renderPlaceholder();
  }

  _renderHeader() {
    if (!this._built) return;
    const title = this.shadowRoot.querySelector('.qw-title');
    if (!title) return;
    const slug = this.getAttribute('slug') || '';
    const name = this._catalog && this._catalog.name ? this._catalog.name : '';
    if (name && name !== slug) {
      title.innerHTML = esc(name) + ' <span class="qw-slug">' + esc(slug) + '</span>';
    } else {
      title.textContent = slug || 'quant-widget';
    }
    const desc = this.shadowRoot.querySelector('.qw-desc');
    if (desc) {
      desc.textContent = this._catalog && this._catalog.description ? this._catalog.description : '';
      desc.style.display = desc.textContent ? '' : 'none';
    }
  }

  _renderPlaceholder() {
    if (!this._built || !this._out || this._hasRun) return;
    this._out.innerHTML = '';
    const hint = document.createElement('div');
    hint.className = 'qw-empty';
    hint.textContent = this._slugAttr() ? 'Not run yet — set scope and click Run.' : 'Missing required attribute: slug.';
    this._out.appendChild(hint);
  }

  _slugAttr() {
    return (this.getAttribute('slug') || '').trim();
  }

  // -- catalog -------------------------------------------------------------

  _loadCatalog() {
    const slug = this._slugAttr();
    if (!slug) return;
    catalogEntryFor(slug).then(function (entry) {
      if (entry) {
        this._catalog = entry;
        this._catalogLoaded = true;
      }
      this._renderHeader();
    }.bind(this)).catch(function () { /* keep data-* fallback */ });
  }

  outputKind() {
    const fromCatalog = this._catalog && this._catalog.output_kind;
    const fromAttr = this.getAttribute('data-output-kind');
    const kind = fromCatalog || fromAttr || 'metrics';
    return String(kind).toLowerCase();
  }

  // -- sync bus ------------------------------------------------------------

  setSynced(on) {
    const want = !!on;
    this.setAttribute('synced', want ? 'true' : 'false');
    if (this._syncBox && this._syncBox.checked !== want) this._syncBox.checked = want;
    this._emit('qw:sync-change', { synced: want });
  }

  _resync() {
    const on = this._syncEnabled();
    if (this._syncBox) this._syncBox.checked = on;
    if (on) {
      this._attach();
    } else {
      this._detach();
    }
  }

  _attach() {
    this._detach(); // never double-subscribe
    const scope = syncBus.getScope();
    // Follow the bus into the inputs (the definition of "synced"); when the
    // bus is still empty keep whatever local values exist so a first edit
    // seeds the bus instead of wiping the form.
    if (scope.ticker || scope.expiry || (scope.basket && scope.basket.length)) {
      this._writeScopeToInputs(scope);
    }
    this._unsub = syncBus.subscribe(function (scope2) {
      this._writeScopeToInputs(scope2);
    }.bind(this));
  }

  _detach() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
  }

  _writeScopeToInputs(scope) {
    if (!this._built) return;
    if (typeof scope.ticker === 'string' && this._inputs.ticker.value !== scope.ticker) {
      this._inputs.ticker.value = scope.ticker;
    }
    if (typeof scope.expiry === 'string' && this._inputs.expiry.value !== scope.expiry) {
      this._inputs.expiry.value = scope.expiry;
    }
    const basketText = joinBasket(scope.basket);
    if (this._inputs.basket.value !== basketText) {
      this._inputs.basket.value = basketText;
    }
  }

  _onInputEdit(event) {
    if (!this._syncEnabled()) return; // detached: local only
    const input = event.target;
    const key = SCOPE_FIELDS.find(function (f) { return this._inputs[f] === input; }, this);
    if (!key) return;
    if (key === 'basket') {
      syncBus.setScope({ basket: splitBasket(input.value) });
    } else {
      syncBus.setScope({ [key]: input.value });
    }
  }

  // -- scope / run ---------------------------------------------------------

  scope() {
    return {
      ticker: this._inputs.ticker.value.trim(),
      expiry: this._inputs.expiry.value.trim(),
      basket: splitBasket(this._inputs.basket.value)
    };
  }

  _validate(scope) {
    if (!this._catalog || !this._catalog.inputs) return null;
    const spec = this._catalog.inputs;
    const label = this._catalog.name || this._slugAttr();
    const problems = [];
    if (spec.ticker === 'required' && !scope.ticker) problems.push('ticker is required');
    if (spec.expiry === 'required' && !scope.expiry) problems.push('expiry is required for ' + label);
    if (spec.basket === 'required' && !(scope.basket && scope.basket.length)) problems.push('basket is required');
    return problems.length ? problems : null;
  }

  get state() { return this._state; }
  get lastResult() { return this._lastResult; }
  get lastError() { return this._lastError; }

  _setState(state) {
    this._state = state;
    if (!this._built) return;
    this._stateEl.className = 'qw-state';
    if (state === 'running') {
      this._stateEl.textContent = 'Running…';
      this._stateEl.classList.remove('ok', 'err');
      this._runBtn.disabled = true;
    } else if (state === 'error') {
      this._stateEl.textContent = 'run failed';
      this._stateEl.classList.add('err');
      this._runBtn.disabled = false;
    } else if (state === 'ready') {
      this._stateEl.textContent = 'done';
      this._stateEl.classList.add('ok');
      this._runBtn.disabled = false;
    } else {
      this._stateEl.textContent = '';
      this._runBtn.disabled = false;
    }
  }

  /** Build the Phase-3 run body: {scope: {...}, params?} */
  _runBody() {
    const scope = this.scope();
    const body = { scope: {} };
    if (scope.ticker) body.scope.ticker = scope.ticker;
    if (scope.expiry) body.scope.expiry = scope.expiry;
    if (scope.basket && scope.basket.length) body.scope.basket = scope.basket;

    const extra = parseDataContext(this);
    if (extra) {
      const params = {};
      Object.keys(extra).forEach(function (key) {
        if (key === 'ticker' || key === 'expiry' || key === 'basket') {
          body.scope[key] = extra[key];
        } else {
          params[key] = extra[key];
        }
      });
      if (Object.keys(params).length) body.params = params;
    }
    return body;
  }

  _unwrapResponse(json) {
    if (!json || typeof json !== 'object') return json;
    if (!('status' in json) && json.result && typeof json.result === 'object') {
      return json.result;
    }
    return json;
  }

  async run() {
    const slug = this._slugAttr();
    if (!slug) {
      this._fail(new Error('quant-widget has no slug attribute'));
      return null;
    }
    const scope = this.scope();
    const problems = this._validate(scope);
    if (problems && problems.length) {
      this._fail(new Error('Missing required input: ' + problems.join(', ')), { clientSide: true });
      return null;
    }
    const body = this._runBody();
    this._setState('running');
    this._emit('qw:run-start', { slug: slug, scope: body.scope });
    try {
      const res = await fetch('/api/widgets/' + encodeURIComponent(slug) + '/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body)
      });
      let json = null;
      try { json = await res.json(); } catch (e) { /* non-JSON error body */ }
      if (!res.ok) {
        let message = 'HTTP ' + res.status;
        if (json && typeof json.detail === 'string') message = json.detail;
        else if (json && typeof json.detail === 'object' && json.detail && typeof json.detail.msg === 'string') message = json.detail.msg;
        else if (json && typeof json.message === 'string') message = json.message;
        throw new Error(message);
      }
      const result = this._unwrapResponse(json);
      this._lastResult = result;
      this._lastError = null;
      this._hasRun = true;
      this._renderResult(result);
      this._setState('ready');
      this._emit('qw:run-success', { slug: slug, scope: body.scope, result: result });
      return result;
    } catch (err) {
      this._fail(err, { slug: slug, scope: body.scope });
      return null;
    }
  }

  _fail(err, extra) {
    this._lastError = err;
    this._state = 'error';
    this._emit('qw:run-error', Object.assign({
      slug: this._slugAttr(),
      scope: this.scope(),
      error: err && err.message ? err.message : String(err)
    }, extra || {}));
    if (this._built) {
      this._stateEl.textContent = 'run failed';
      this._stateEl.classList.add('err');
      this._runBtn.disabled = false;
      this._renderError(err && err.message ? err.message : String(err));
    }
  }

  _renderError(message) {
    if (!this._out) return;
    this._out.innerHTML = '';
    const box = document.createElement('div');
    box.className = 'errbox';
    box.textContent = message;
    this._out.appendChild(box);
  }

  _renderResult(result) {
    if (!this._out) return;
    this._out.innerHTML = '';
    const title = (this._catalog && this._catalog.name) || this._slugAttr();
    const node = renderResult(result, this.outputKind(), { title: title });
    this._out.appendChild(node);
  }

  /** Best-effort "last cached result for this scope" read on mount — never
   *  errors, never auto-runs, and never clobbers a result the user produced
   *  by clicking Run. */
  _maybeFetchState() {
    const slug = this._slugAttr();
    if (!slug) return;
    const scope = syncBus.getScope();
    if (!scope.ticker && !scope.expiry && !(scope.basket && scope.basket.length)) return;
    const q = encodeURIComponent(JSON.stringify(scope));
    fetch('/api/widgets/' + encodeURIComponent(slug) + '/state?scope=' + q, {
      headers: { Accept: 'application/json' }
    }).then(function (res) {
      if (!res.ok) return null;
      return res.json();
    }).then(function (json) {
      if (!json || this._hasRun || !this._out) return;
      const result = this._unwrapResponse(json);
      if (result && typeof result === 'object' && !('status' in result)) return; // no usable cached body
      this._hasRun = true; // treat a shown cached result as the current view
      this._lastResult = result;
      this._renderResult(result);
      this._setState('ready');
    }.bind(this)).catch(function () { /* read-only best effort; ignore */ });
  }

  _emit(name, detail) {
    this.dispatchEvent(new CustomEvent(name, {
      bubbles: true,
      composed: true,
      detail: detail || {}
    }));
  }
}

export function defineQuantWidget() {
  if (!customElements.get('quant-widget')) {
    customElements.define('quant-widget', QuantWidget);
  }
  return customElements.get('quant-widget');
}

defineQuantWidget();
export { QuantWidget };
