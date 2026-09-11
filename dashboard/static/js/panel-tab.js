/*
 * panel-tab.js — the shared engine behind the Volatility / Models / Sentiment tabs.
 *
 * One scope bar, a checkbox rail of panels, and a results grid. The three
 * pages differ only in which panels the server hands them and what extra
 * scope fields they declare, so they all call initPanelTab() and add their
 * own trimmings.
 *
 * Why this is not the desk's tool-card rail:
 *
 *   - The desk is a *catalog*: pick any of 57 modules, one card each, run
 *     them individually. That is right for "I'm going to use the hedge
 *     optimizer now".
 *   - These tabs are a *workbench*: a fixed set of panels that belong
 *     together, all on one scope, checked on or off, run as a batch. That is
 *     right for "show me everything this ticker's vol surface is doing".
 *
 * RATE LIMITING IS LOAD-BEARING, NOT POLITENESS. Every panel here is a real
 * billed ThetaData pull. CLAUDE.md's ThetaData rule is explicit: sleep
 * 0.3-0.5s between calls, never fan out. So `run all` is a sequential queue
 * with a gap between panels, not Promise.all. Firing eleven surface builds
 * in parallel gets the whole batch throttled and every card fails at once.
 */

import { renderResult } from '/static/js/widget-renderers.js';
import { syncBus } from '/static/js/sync-bus.js';

const RUN_GAP_MS = 400;   // between sequential panel runs — see above
const BOOK_STALE_MIN = 360; // 6h: past a session, so it predates today's fills

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function storageKey(tab, what) {
  return 'panelTab.' + tab + '.' + what;
}

function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}

function saveJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    /* per-viewer convenience only; the tab works without it */
  }
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

// ---------------------------------------------------------------------------
// Scope bar
// ---------------------------------------------------------------------------

/**
 * Build the page's one scope bar. `extraFields` lets a tab add scope-ish
 * inputs that are not ticker/expiry/basket — the Models tab needs strike,
 * right and tenor, which are part of "which contract", not part of "which
 * panel". They live here rather than on each card for the same reason
 * ticker does: entered once.
 */
function buildScopeBar(host, opts) {
  const bar = el('section', 'scopebar');
  const main = el('div', 'scopebar-main');

  function field(labelText, id, placeholder) {
    const label = el('label', 'sc-field');
    label.appendChild(el('span', null, labelText));
    const input = el('input');
    input.id = id;
    input.autocomplete = 'off';
    input.spellcheck = false;
    if (placeholder) input.placeholder = placeholder;
    label.appendChild(input);
    main.appendChild(label);
    return input;
  }

  const tickerInput = field('Ticker', 'scTicker', 'SPY');
  const expiryInput = field('Expiry', 'scExpiry', '2026-12-18');

  const basketWrap = el('div', 'sc-field sc-basket');
  basketWrap.appendChild(el('span', null, 'Basket'));
  const chips = el('div', 'chips');
  basketWrap.appendChild(chips);
  main.appendChild(basketWrap);

  const extraInputs = {};
  (opts.extraFields || []).forEach(function (spec) {
    const label = el('label', 'sc-field');
    label.appendChild(el('span', null, spec.label));
    let input;
    if (spec.choices) {
      input = document.createElement('select');
      spec.choices.forEach(function (choice) {
        const option = document.createElement('option');
        option.value = choice;
        option.textContent = choice;
        input.appendChild(option);
      });
    } else {
      input = el('input');
      input.autocomplete = 'off';
      if (spec.placeholder) input.placeholder = spec.placeholder;
    }
    input.id = 'sc_' + spec.name;
    if (spec.default != null) input.value = spec.default;
    label.appendChild(input);
    main.appendChild(label);
    extraInputs[spec.name] = input;
  });

  const fromBook = el('button', 'ghost', 'Basket = my book');
  fromBook.type = 'button';
  fromBook.title = 'Set the basket to every ticker you hold';
  main.appendChild(fromBook);

  const clear = el('button', 'ghost', 'Clear');
  clear.type = 'button';
  main.appendChild(clear);

  bar.appendChild(main);
  const note = el('div', 'scopebar-note muted', opts.note || '');
  bar.appendChild(note);
  host.appendChild(bar);

  function renderBasket(basket) {
    chips.innerHTML = '';
    if (!basket || !basket.length) {
      chips.appendChild(el('em', 'muted', 'empty'));
      return;
    }
    basket.forEach(function (t) {
      const chip = el('button', 'chip', t);
      chip.type = 'button';
      chip.title = 'Focus ' + t;
      chip.addEventListener('click', function () { syncBus.setScope({ ticker: t }); });
      const x = el('span', 'chip-x', '×');
      x.addEventListener('click', function (ev) {
        ev.stopPropagation();
        const next = (syncBus.getScope().basket || []).filter(function (v) { return v !== t; });
        syncBus.setScope({ basket: next });
      });
      chip.appendChild(x);
      chips.appendChild(chip);
    });
  }

  syncBus.subscribe(function (scope) {
    if (tickerInput.value !== scope.ticker) tickerInput.value = scope.ticker;
    if (expiryInput.value !== scope.expiry) expiryInput.value = scope.expiry;
    renderBasket(scope.basket);
    if (opts.onScopeChange) opts.onScopeChange(scope);
  });

  tickerInput.addEventListener('input', function () {
    syncBus.setScope({ ticker: tickerInput.value.toUpperCase() });
  });
  expiryInput.addEventListener('input', function () {
    syncBus.setScope({ expiry: expiryInput.value });
  });
  clear.addEventListener('click', function () {
    syncBus.reset({ ticker: '', expiry: '', basket: null });
    renderBasket(null);
  });

  // Hydrate from the (persisted) bus so arriving from another tab keeps scope.
  const initial = syncBus.getScope();
  tickerInput.value = initial.ticker;
  expiryInput.value = initial.expiry;
  renderBasket(initial.basket);

  return {
    note: note,
    fromBookButton: fromBook,
    extras: function () {
      const out = {};
      Object.keys(extraInputs).forEach(function (name) {
        const value = extraInputs[name].value;
        if (value !== '' && value != null) out[name] = value;
      });
      return out;
    }
  };
}

// ---------------------------------------------------------------------------
// The book strip — the scope source, same contract as the desk's book panel
// ---------------------------------------------------------------------------

function buildBookStrip(host, scopeBar) {
  const panel = el('section', 'panel bookstrip');
  const header = el('header');
  header.appendChild(el('h2', null, 'Your book'));
  const stamp = el('span', 'note', 'loading…');
  header.appendChild(stamp);
  const refresh = el('button', 'ghost sm', 'Refresh');
  refresh.type = 'button';
  header.appendChild(refresh);
  panel.appendChild(header);
  const body = el('div', 'body');
  const row = el('div', 'bookchips');
  body.appendChild(row);
  panel.appendChild(body);
  host.appendChild(panel);

  let held = [];

  function renderStamp(computedAt) {
    stamp.classList.remove('stale', 'ok');
    if (!computedAt) { stamp.textContent = 'no timestamp'; stamp.classList.add('stale'); return; }
    const then = new Date(computedAt);
    if (isNaN(then.getTime())) { stamp.textContent = 'as of ' + computedAt; return; }
    const mins = Math.max(0, (Date.now() - then.getTime()) / 60000);
    let age;
    if (mins < 90) age = Math.round(mins) + 'm ago';
    else if (mins < 60 * 36) age = Math.round(mins / 60) + 'h ago';
    else age = Math.round(mins / 1440) + 'd ago';
    stamp.textContent = 'as of ' + age;
    stamp.title = computedAt;
    stamp.classList.add(mins > BOOK_STALE_MIN ? 'stale' : 'ok');
  }

  async function load() {
    stamp.textContent = 'loading…';
    try {
      const res = await fetch('/api/widgets/positions/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ scope: {} })
      });
      const json = await res.json();
      const metrics = (json && json.metrics) || {};
      held = metrics.held_tickers || [];
      renderStamp(metrics.computed_at);
      row.innerHTML = '';
      if (!held.length) {
        row.appendChild(el('span', 'muted small', 'no positions pushed yet'));
        return;
      }
      held.forEach(function (t) {
        const chip = el('button', 'chip ghostchip', t);
        chip.type = 'button';
        chip.title = 'Focus ' + t;
        chip.addEventListener('click', function () { syncBus.setScope({ ticker: t }); });
        row.appendChild(chip);
      });
    } catch (err) {
      stamp.textContent = 'failed';
      row.innerHTML = '';
      row.appendChild(el('span', 'muted small', String(err && err.message ? err.message : err)));
    }
  }

  refresh.addEventListener('click', load);
  scopeBar.fromBookButton.addEventListener('click', function () {
    if (!held.length) {
      scopeBar.note.textContent = 'No book to build a basket from — nothing has been pushed.';
      return;
    }
    syncBus.setScope({ basket: held });
    scopeBar.note.textContent = 'Basket set to your ' + held.length + ' held tickers.';
  });

  load();
  return { held: function () { return held; } };
}

// ---------------------------------------------------------------------------
// Panel cards
// ---------------------------------------------------------------------------

function buildParamControls(spec, values, onChange) {
  if (!spec.params || !spec.params.length) return null;
  const wrap = el('div', 'panel-params');
  spec.params.forEach(function (p) {
    const label = el('label', 'pp-field');
    label.appendChild(el('span', null, p.label));
    let input;
    if (p.kind === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.checked = values[p.name] === undefined ? !!p.default : !!values[p.name];
      label.classList.add('pp-bool');
      input.addEventListener('change', function () { onChange(p.name, input.checked); });
    } else if (p.kind === 'choice') {
      input = document.createElement('select');
      (p.choices || []).forEach(function (choice) {
        const option = document.createElement('option');
        option.value = choice;
        option.textContent = String(choice);
        input.appendChild(option);
      });
      input.value = values[p.name] !== undefined ? values[p.name] : p.default;
      input.addEventListener('change', function () { onChange(p.name, input.value); });
    } else {
      input = el('input');
      input.type = p.kind === 'number' ? 'number' : 'text';
      const current = values[p.name] !== undefined ? values[p.name] : p.default;
      if (current != null) input.value = current;
      input.addEventListener('change', function () { onChange(p.name, input.value); });
    }
    if (p.help) label.title = p.help;
    label.appendChild(input);
    wrap.appendChild(label);
  });
  return wrap;
}

function buildPanelCard(spec, state, runOne) {
  const card = el('article', 'panelcard');
  card.dataset.panel = spec.id;

  const head = el('header', 'panelcard-head');

  const check = el('input');
  check.type = 'checkbox';
  check.className = 'panel-toggle';
  check.checked = state.selected.indexOf(spec.id) !== -1;
  check.title = 'Include in Run selected';
  check.addEventListener('change', function () {
    const i = state.selected.indexOf(spec.id);
    if (check.checked && i === -1) state.selected.push(spec.id);
    if (!check.checked && i !== -1) state.selected.splice(i, 1);
    state.save();
    card.classList.toggle('off', !check.checked);
  });
  head.appendChild(check);

  const titleWrap = el('div', 'panelcard-title');
  titleWrap.appendChild(el('h3', null, spec.title));
  titleWrap.appendChild(el('p', 'small muted', spec.blurb));
  head.appendChild(titleWrap);

  const runBtn = el('button', 'ghost sm', 'Run');
  runBtn.type = 'button';
  head.appendChild(runBtn);
  card.appendChild(head);

  const paramValues = state.params[spec.id] || (state.params[spec.id] = {});
  const controls = buildParamControls(spec, paramValues, function (name, value) {
    paramValues[name] = value;
    state.save();
  });
  if (controls) card.appendChild(controls);

  const provenance = el('div', 'panelcard-prov small muted');
  provenance.hidden = true;
  card.appendChild(provenance);

  const body = el('div', 'panelcard-body');
  body.appendChild(el('div', 'empty', 'not run yet'));
  card.appendChild(body);

  card.classList.toggle('off', !check.checked);

  const api = {
    spec: spec,
    node: card,
    isSelected: function () { return check.checked; },
    params: function () { return paramValues; },
    setBusy: function (busy) {
      runBtn.disabled = busy;
      card.classList.toggle('busy', busy);
      if (busy) {
        body.innerHTML = '';
        body.appendChild(el('div', 'empty', 'running…'));
      }
    },
    render: function (result) {
      body.innerHTML = '';
      body.appendChild(renderResult(result, result.output_kind || spec.output_kind, {}));
      // The provenance line is the first thing to check when a number looks
      // wrong: `ran` is what actually executed (a `requires` dependency runs
      // too), `fed` is which stored context keys the run was seeded with.
      const bits = [];
      if (result.ran && result.ran.length) bits.push('ran: ' + result.ran.join(' → '));
      if (result.context_seeded && result.context_seeded.length) {
        bits.push('fed: ' + result.context_seeded.join(', '));
      }
      if (bits.length) {
        provenance.textContent = bits.join('   ·   ');
        provenance.hidden = false;
      } else {
        provenance.hidden = true;
      }
    }
  };

  runBtn.addEventListener('click', function () { runOne(api); });
  return api;
}

// ---------------------------------------------------------------------------
// Context strip — what previous runs left behind for this scope
// ---------------------------------------------------------------------------

function buildContextStrip(host) {
  const panel = el('section', 'panel ctxstrip');
  const header = el('header');
  header.appendChild(el('h2', null, 'Available context'));
  header.appendChild(el('span', 'note', 'what earlier runs left in the Context Store for this scope'));
  const refresh = el('button', 'ghost sm', 'Refresh');
  refresh.type = 'button';
  header.appendChild(refresh);
  panel.appendChild(header);
  const body = el('div', 'body ctxbody');
  panel.appendChild(body);
  host.appendChild(panel);

  async function load() {
    body.innerHTML = '';
    body.appendChild(el('span', 'muted small', 'loading…'));
    try {
      const res = await fetch('/api/context', { headers: { Accept: 'application/json' } });
      const json = await res.json();
      const entries = (json && json.entries) || [];
      body.innerHTML = '';
      if (!entries.length) {
        body.appendChild(el('span', 'muted small',
          'nothing stored yet — run a panel and its results become context for the next one'));
        return;
      }
      const scope = syncBus.getScope();
      const ticker = (scope.ticker || '').toUpperCase();
      const relevant = entries.filter(function (e) {
        const key = String(e.scope || '');
        return !ticker || key.indexOf(ticker) !== -1 || key.indexOf('GLOBAL') !== -1;
      });
      (relevant.length ? relevant : entries).slice(0, 40).forEach(function (e) {
        const chip = el('span', 'ctxchip');
        chip.appendChild(el('b', null, e.key));
        chip.appendChild(el('em', 'muted', e.scope));
        chip.title = 'written by ' + (e.source_slug || 'unknown') + ' at ' + (e.updated_at || '?');
        body.appendChild(chip);
      });
    } catch (err) {
      body.innerHTML = '';
      body.appendChild(el('span', 'muted small', 'context read failed: ' + err));
    }
  }

  refresh.addEventListener('click', load);
  load();
  return { reload: load };
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * @param {object} opts
 *   tab          {string}  tab id, used for storage keys and the run body
 *   panels       {array}   PanelSpec.to_json() list from the server
 *   mount        {Element} where to build everything
 *   extraFields  {array}   extra scope-bar inputs (Models: strike/right/tenor)
 *   note         {string}  scope-bar caption
 *   showBook     {bool}    render the book strip (default true)
 *   showContext  {bool}    render the context strip (default true)
 */
export function initPanelTab(opts) {
  const tab = opts.tab;
  const mount = opts.mount;
  const specs = opts.panels || [];

  const state = {
    selected: loadJSON(storageKey(tab, 'selected'),
      specs.filter(function (s) { return s.default_on; }).map(function (s) { return s.id; })),
    params: loadJSON(storageKey(tab, 'params'), {}),
    save: function () {
      saveJSON(storageKey(tab, 'selected'), state.selected);
      saveJSON(storageKey(tab, 'params'), state.params);
    }
  };

  // Declared BEFORE buildScopeBar, not after: the scope bar's onScopeChange
  // closes over it, and a `let` read before its declaration is a TDZ
  // ReferenceError, not undefined. Nothing publishes a scope change during
  // construction today, so the ordering was survivable -- but "survivable
  // because nothing happens to fire yet" is not a thing to leave in place.
  let contextStrip = null;

  const scopeBar = buildScopeBar(mount, {
    extraFields: opts.extraFields,
    note: opts.note,
    onScopeChange: function () { if (contextStrip) contextStrip.reload(); }
  });

  const topRow = el('div', 'tab-toprow');
  mount.appendChild(topRow);
  if (opts.showBook !== false) buildBookStrip(topRow, scopeBar);
  if (opts.showContext !== false) contextStrip = buildContextStrip(topRow);

  // ---- controls ----
  const controls = el('section', 'panel tabcontrols');
  const controlsHead = el('header');
  controlsHead.appendChild(el('h2', null, opts.title || 'Panels'));
  const status = el('span', 'note', '');
  controlsHead.appendChild(status);
  const runSelected = el('button', null, 'Run selected');
  runSelected.type = 'button';
  controlsHead.appendChild(runSelected);
  const runAll = el('button', 'ghost sm', 'Run all');
  runAll.type = 'button';
  controlsHead.appendChild(runAll);
  const selectAll = el('button', 'ghost sm', 'Select all');
  selectAll.type = 'button';
  controlsHead.appendChild(selectAll);
  const selectNone = el('button', 'ghost sm', 'Select none');
  selectNone.type = 'button';
  controlsHead.appendChild(selectNone);
  controls.appendChild(controlsHead);
  mount.appendChild(controls);

  // ---- panel grid, grouped ----
  const cards = [];
  const groups = {};
  specs.forEach(function (spec) {
    (groups[spec.group || ''] = groups[spec.group || ''] || []).push(spec);
  });

  async function runOne(card) {
    const scope = syncBus.getScope();
    const body = {
      scope: {
        ticker: scope.ticker,
        expiry: scope.expiry,
        basket: scope.basket
      },
      params: Object.assign({}, scopeBar.extras(), card.params())
    };
    card.setBusy(true);
    try {
      const res = await fetch('/api/panels/' + encodeURIComponent(card.spec.id) + '/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body)
      });
      const json = await res.json();
      if (!res.ok) {
        card.render({
          status: 'failed',
          metrics: { error: (json && json.detail) || ('HTTP ' + res.status) }
        });
      } else {
        card.render(json);
      }
    } catch (err) {
      card.render({ status: 'failed', metrics: { error: String(err && err.message ? err.message : err) } });
    } finally {
      card.setBusy(false);
      if (contextStrip) contextStrip.reload();
    }
  }

  let running = false;
  async function runMany(list) {
    if (running) return;
    if (!list.length) { status.textContent = 'nothing selected'; return; }
    running = true;
    runSelected.disabled = runAll.disabled = true;
    // Sequential, with a gap. See the rate-limit note at the top of this file.
    for (let i = 0; i < list.length; i += 1) {
      status.textContent = 'running ' + (i + 1) + '/' + list.length + ' — ' + list[i].spec.title;
      await runOne(list[i]);
      if (i < list.length - 1) await sleep(RUN_GAP_MS);
    }
    status.textContent = 'done — ' + list.length + ' panel' + (list.length === 1 ? '' : 's');
    running = false;
    runSelected.disabled = runAll.disabled = false;
  }

  Object.keys(groups).forEach(function (groupName) {
    if (groupName) {
      const heading = el('h2', 'groupheading', groupName);
      mount.appendChild(heading);
    }
    const grid = el('div', 'panel-grid');
    groups[groupName].forEach(function (spec) {
      const card = buildPanelCard(spec, state, runOne);
      cards.push(card);
      grid.appendChild(card.node);
    });
    mount.appendChild(grid);
  });

  runSelected.addEventListener('click', function () {
    runMany(cards.filter(function (c) { return c.isSelected(); }));
  });
  runAll.addEventListener('click', function () { runMany(cards.slice()); });
  selectAll.addEventListener('click', function () {
    cards.forEach(function (c) {
      const box = c.node.querySelector('.panel-toggle');
      if (!box.checked) { box.checked = true; box.dispatchEvent(new Event('change')); }
    });
  });
  selectNone.addEventListener('click', function () {
    cards.forEach(function (c) {
      const box = c.node.querySelector('.panel-toggle');
      if (box.checked) { box.checked = false; box.dispatchEvent(new Event('change')); }
    });
  });

  return { cards: cards, runMany: runMany, scopeBar: scopeBar, state: state };
}
