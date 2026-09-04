/*
 * widget-renderers.js — generic output_kind renderers for <quant-widget>.
 *
 * Phase 4 of the widget-native-quant-console plan, step 3. One home for the
 * generic renderers selected by a ModuleSpec's output_kind:
 *
 *   renderDistribution()  — single shared implementation of what previously
 *                           existed twice: quant.html's `renderDistribution`
 *                           and index.html's `renderDistChart` (near-identical
 *                           inline-SVG histogram copies). Handles bins objects
 *                           ({low,high,count}), bin tuples, or raw values
 *                           (auto-binned).
 *   renderMetricsTable()  — scalar cards + nested-object disclosure + real
 *                           <table> for list-of-object metrics.
 *   renderArtifacts()     — PNG/image artifact viewer (artifacts[].path ->
 *                           URL), the generic JSON <pre> fallback, and
 *                           renderResult(), the per-output_kind orchestrator.
 *
 * Pure DOM builders: they return detached nodes and declare classes, no
 * inline styles for layout. The widget styles them from its shadow
 * stylesheet; a plain page that includes base.html styles them too (class
 * names reuse base.html's vocabulary: .pill, .metric-grid, .metric-card,
 * .dist-chart, .dist-bar, .json, ...). No framework, no bundler, no I/O.
 */

export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

/* Number formatter matching quant.html's fmtMetricValue: integers get
   locale separators, floats get 4 decimals, everything else passes through. */
export function fmtValue(v) {
  if (v === null || v === undefined || v === '') return '--';
  if (typeof v === 'number') {
    if (!isFinite(v)) return String(v);
    return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(4);
  }
  return String(v);
}

export function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

// ---------------------------------------------------------------------------
// Distribution histogram — the shared de-duplicated implementation.
// Quant console's quant.html::renderDistribution and Overview's
// index.html::renderDistChart were near-identical copies; this is the one
// canonical version both pages (and <quant-widget>) should use going forward.
// Accepts {label, unit, bins, percentiles} where bins entries are either
// {low, high, count} objects or [low, high, count] tuples, or a raw
// {values: [...]} payload that gets auto-binned here.
// ---------------------------------------------------------------------------

export const DIST_UNIT_LABELS = {
  price: 'terminal price distribution',
  portfolio_value: 'terminal portfolio-value distribution',
  unknown: 'terminal distribution (units unreported)'
};

function distUnitLabel(unit) {
  return DIST_UNIT_LABELS[unit] || DIST_UNIT_LABELS.unknown;
}

function autoBin(values) {
  const nums = (values || []).filter(function (v) {
    return typeof v === 'number' && isFinite(v);
  });
  if (!nums.length) return [];
  let lo = Infinity;
  let hi = -Infinity;
  nums.forEach(function (v) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  });
  if (lo === hi) { lo -= 0.5; hi += 0.5; }
  const n = nums.length;
  const binCount = Math.max(8, Math.min(48, Math.ceil(Math.sqrt(n))));
  const width = (hi - lo) / binCount;
  const bins = [];
  for (let i = 0; i < binCount; i++) {
    const low = lo + i * width;
    const high = i === binCount - 1 ? hi : low + width;
    bins.push({ low: low, high: high, count: 0 });
  }
  nums.forEach(function (v) {
    let idx = Math.floor((v - lo) / width);
    if (idx >= binCount) idx = binCount - 1;
    if (idx < 0) idx = 0;
    bins[idx].count += 1;
  });
  return bins;
}

function normalizeBins(dist) {
  const raw = (dist && dist.bins) || null;
  if (raw && Array.isArray(raw) && raw.length) {
    return raw.map(function (b) {
      if (Array.isArray(b)) {
        return { low: b[0], high: b[1], count: b[2] };
      }
      return b;
    });
  }
  if (dist && Array.isArray(dist.values) && dist.values.length) {
    return autoBin(dist.values);
  }
  return [];
}

/**
 * Build the inline-SVG histogram element. Returns null when there is nothing
 * drawable. Pure DOM; the CSS classes (.dist-chart, .dist-bar, ...) are
 * styled by the page or the widget's shadow stylesheet.
 */
export function renderDistribution(dist) {
  if (!dist || typeof dist !== 'object') return null;
  const bins = normalizeBins(dist);
  if (!bins.length) return null;

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const wrap = document.createElement('div');
  wrap.className = 'dist-chart';

  const title = document.createElement('div');
  title.className = 'small dist-chart-title';
  title.textContent = (dist.label || 'simulation') + ' — ' + distUnitLabel(dist.unit);
  wrap.appendChild(title);

  const W = 320, H = 80, PAD = 2;
  const barW = W / bins.length;
  // An all-zero histogram would divide by zero; floor the scale at 1 so the
  // chart renders flat-empty rather than producing NaN geometry.
  const maxCount = bins.reduce(function (m, b) {
    return Math.max(m, typeof b.count === 'number' ? b.count : 0);
  }, 0) || 1;

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', String(H));
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', title.textContent);

  bins.forEach(function (b, i) {
    const count = typeof b.count === 'number' ? b.count : 0;
    const barH = (count / maxCount) * (H - PAD);
    const rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('x', String(i * barW));
    rect.setAttribute('y', String(H - barH));
    rect.setAttribute('width', String(Math.max(barW - 1, 0.5)));
    rect.setAttribute('height', String(barH));
    rect.setAttribute('class', 'dist-bar');
    const tip = document.createElementNS(SVG_NS, 'title');
    tip.textContent = fmtValue(b.low) + '–' + fmtValue(b.high) + ': ' + fmtValue(count);
    rect.appendChild(tip);
    svg.appendChild(rect);
  });
  wrap.appendChild(svg);

  // Percentiles are omitted upstream (not zero-filled) when the sim doesn't
  // publish them, so render only the ones that exist.
  const pct = (dist && dist.percentiles) || {};
  const parts = ['p5', 'p50', 'p95'].filter(function (k) {
    return typeof pct[k] === 'number';
  }).map(function (k) {
    return k + ': ' + fmtValue(pct[k]);
  });
  const line = document.createElement('div');
  line.className = 'small dist-percentiles';
  line.textContent = parts.length
    ? parts.join('  ·  ')
    : 'percentiles not reported by this simulation';
  wrap.appendChild(line);

  return wrap;
}

// ---------------------------------------------------------------------------
// Metrics — card grid for scalars (the repo's metric-grid/metric-card
// language), <details> disclosure for nested objects (e.g. greeks), and a
// real <table> when a metric value is a list of row objects (e.g. positions,
// rows). 'headline' / 'warnings' are drawn as chrome, not as cards.
// ---------------------------------------------------------------------------

function isRowArray(v) {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every(function (r) { return isPlainObject(r); })
  );
}

function rowTable(rows) {
  const tablewrap = document.createElement('div');
  tablewrap.className = 'tablewrap';
  const table = document.createElement('table');
  const cols = [];
  rows.forEach(function (row) {
    Object.keys(row).forEach(function (k) {
      if (cols.indexOf(k) === -1) cols.push(k);
    });
  });
  const thead = document.createElement('thead');
  const trh = document.createElement('tr');
  cols.forEach(function (k) {
    const th = document.createElement('th');
    th.textContent = k;
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  rows.forEach(function (row) {
    const tr = document.createElement('tr');
    cols.forEach(function (k) {
      const td = document.createElement('td');
      const v = row[k];
      td.textContent = typeof v === 'number' && !Number.isInteger(v)
        ? v.toFixed(4)
        : fmtValue(v);
      td.className = typeof v === 'number' ? 'num' : '';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  tablewrap.appendChild(table);
  return tablewrap;
}

function scalarGrid(entries, labelPrefix) {
  const grid = document.createElement('div');
  grid.className = 'metric-grid';
  entries.forEach(function (entry) {
    const card = document.createElement('div');
    card.className = 'metric-card';
    const lab = document.createElement('div');
    lab.className = 'metric-label';
    lab.textContent = labelPrefix ? labelPrefix + ' · ' + entry.key : entry.key;
    const val = document.createElement('div');
    val.className = 'metric-value';
    const v = entry.value;
    if (Array.isArray(v)) {
      // Small scalar lists (prices, ivs, ...) join inline rather than carding.
      val.textContent = v.length > 40
        ? '[' + v.length + ' items — see JSON]'
        : v.map(fmtValue).join(', ');
    } else {
      val.textContent = fmtValue(v);
    }
    card.appendChild(lab);
    card.appendChild(val);
    grid.appendChild(card);
  });
  return grid;
}

function renderMetricValue(key, value, container) {
  if (isRowArray(value)) {
    const group = document.createElement('div');
    group.className = 'qw-rows';
    const label = document.createElement('div');
    label.className = 'small muted';
    label.textContent = key;
    group.appendChild(label);
    group.appendChild(rowTable(value));
    container.appendChild(group);
    return true;
  }
  if (isPlainObject(value)) {
    const details = document.createElement('details');
    details.className = 'qw-sub';
    const summary = document.createElement('summary');
    summary.textContent = key;
    details.appendChild(summary);
    details.appendChild(renderMetricsTable(value));
    container.appendChild(details);
    return true;
  }
  return false;
}

const CHROME_KEYS = ['headline', 'warnings', 'error', 'note'];

/** Render a metrics object into cards / disclosures / tables. */
export function renderMetricsTable(metrics) {
  const out = document.createElement('div');
  out.className = 'qw-metrics';
  if (!metrics || typeof metrics !== 'object') {
    out.appendChild(document.createTextNode('(no metrics)'));
    return out;
  }

  const scalars = [];
  const chrome = { headline: null, warnings: null };
  const rowsHost = document.createElement('div');

  Object.keys(metrics).forEach(function (key) {
    const value = metrics[key];
    if (key === 'headline' && typeof value === 'string') { chrome.headline = value; return; }
    if (key === 'warnings' && Array.isArray(value)) { chrome.warnings = value; return; }
    if (CHROME_KEYS.indexOf(key) !== -1) return;
    if (isPlainObject(value) || isRowArray(value)) {
      if (!renderMetricValue(key, value, rowsHost)) {
        scalars.push({ key: key, value: value });
      }
      return;
    }
    scalars.push({ key: key, value: value });
  });

  if (chrome.headline) {
    const hl = document.createElement('div');
    hl.className = 'small qw-headline';
    hl.textContent = chrome.headline;
    out.appendChild(hl);
  }
  if (chrome.warnings && chrome.warnings.length) {
    const ul = document.createElement('ul');
    ul.className = 'qw-warnings small';
    chrome.warnings.forEach(function (w) {
      const li = document.createElement('li');
      li.textContent = w;
      ul.appendChild(li);
    });
    out.appendChild(ul);
  }
  if (scalars.length) out.appendChild(scalarGrid(scalars, null));
  while (rowsHost.firstChild) out.appendChild(rowsHost.firstChild);
  if (!chrome.headline && !chrome.warnings && !scalars.length && !rowsHost.firstChild) {
    out.appendChild(document.createTextNode('(empty metrics)'));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Artifacts — PNG/image viewer. artifacts[].path is a filesystem path in
// ModuleResult JSON; the dashboard already serves artifact files via the
// GET /files?path=<path-under-artifacts/> route (dealer-book <img> convention,
// app.py::files), so relative/absolute filesystem paths resolve through it.
// data:/http(s):/absolute-URL paths pass through untouched.
// ---------------------------------------------------------------------------

export function resolveArtifactURL(path) {
  if (typeof path !== 'string' || !path) return null;
  if (/^(data:|blob:|https?:|file:)/i.test(path)) return path;
  if (path.charAt(0) === '/') return path; // already a URL path
  // Anything else is treated as an artifact filesystem path.
  return '/files?path=' + encodeURIComponent(path);
}

export function isImageArtifact(artifact) {
  if (!artifact || typeof artifact !== 'object') return false;
  const kind = String(artifact.kind || '').toLowerCase();
  const path = String(artifact.path || '');
  return (
    kind === 'png' || kind === 'image' || kind === 'jpg' || kind === 'jpeg' ||
    /\.(png|jpe?g|gif|webp|svg)$/i.test(path)
  );
}

/** Render every image artifact as an <img>; returns null when none. */
export function renderArtifacts(artifacts) {
  const list = (artifacts || []).filter(isImageArtifact);
  if (!list.length) return null;
  const host = document.createElement('div');
  host.className = 'qw-artifacts';
  list.forEach(function (a) {
    const url = resolveArtifactURL(a.path);
    if (!url) return;
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.className = 'qw-artifact-img';
    img.loading = 'lazy';
    img.alt = a.path ? String(a.path).split(/[\\/]/).pop() : 'artifact';
    img.src = url;
    figure.appendChild(img);
    const cap = document.createElement('figcaption');
    cap.className = 'small muted';
    cap.textContent = img.alt;
    figure.appendChild(cap);
    host.appendChild(figure);
  });
  return host.firstChild ? host : null;
}

/** JSON fallback: pretty-printed, safely textContent'd. */
export function renderJSON(value) {
  const pre = document.createElement('pre');
  pre.className = 'json';
  let text;
  try {
    text = JSON.stringify(value, null, 2);
  } catch (e) {
    text = String(value);
  }
  pre.textContent = text;
  return pre;
}

// ---------------------------------------------------------------------------
// Payload discovery for output_kind="distribution". The Phase-2/3 contract
// doesn't pin where the distribution lives inside ModuleResult JSON yet, so
// this accepts the shapes the existing backend produces today: a top-level
// `distribution` object, a `distributions` array, or any metrics value that
// carries {bins|values} (e.g. metrics.price_dist / metrics[slug + '_dist']).
// ---------------------------------------------------------------------------

function looksLikeDistribution(v) {
  return isPlainObject(v) && (
    (Array.isArray(v.bins) && v.bins.length > 0) ||
    (Array.isArray(v.values) && v.values.length > 0)
  );
}

export function findDistributions(result) {
  const found = [];
  if (!result || typeof result !== 'object') return found;

  function pushIfDist(v) {
    if (looksLikeDistribution(v)) found.push(v);
    return false;
  }

  if (looksLikeDistribution(result.distribution)) {
    found.push(result.distribution);
  }
  if (Array.isArray(result.distributions)) {
    result.distributions.forEach(pushIfDist);
  }
  const metrics = result.metrics;
  if (isPlainObject(metrics)) {
    Object.keys(metrics).forEach(function (key) {
      if (key === 'distribution' || key === 'distributions') return;
      const v = metrics[key];
      if (looksLikeDistribution(v)) {
        found.push(v);
      } else if (Array.isArray(v)) {
        v.forEach(function (item) {
          if (looksLikeDistribution(item)) {
            // Name array items without their own label after the metric key
            // they rode in under, so stacked charts are distinguishable.
            found.push(item.label ? item : Object.assign({}, item, { label: key }));
          }
        });
      }
    });
  }
  return found;
}

// ---------------------------------------------------------------------------
// renderResult — the generic orchestrator the <quant-widget> calls with a
// ModuleResult-shaped JSON body and an output_kind. Never throws for bad
// input; falls back to the JSON <pre> so nothing a widget returns is
// invisible.
// ---------------------------------------------------------------------------

/**
 * @param {object}  result     ModuleResult-shaped JSON (status/artifacts/
 *                             metrics/context_patch) — possibly wrapped.
 * @param {string}  outputKind 'metrics' | 'table' | 'distribution' |
 *                             'chart' | 'surface' | 'image' | 'json' | ...
 * @param {object}  opts       {title: string}
 * @returns {HTMLElement}      container node
 */
export function renderResult(result, outputKind, opts) {
  const optsSafe = opts || {};
  const host = document.createElement('div');
  host.className = 'qw-result';
  if (!result || typeof result !== 'object') {
    host.appendChild(renderJSON(result));
    return host;
  }

  // Defensive unwrap: some future route may wrap ModuleResult under a key.
  let res = result;
  if (!('status' in res) && isPlainObject(res.result)) res = res.result;

  const status = String(res.status || '').toLowerCase();

  const head = document.createElement('div');
  head.className = 'qw-result-head';
  const pill = document.createElement('span');
  pill.className = 'pill ' + statusPillClass(status);
  pill.textContent = status || 'ok';
  head.appendChild(pill);
  if (optsSafe.title) {
    const name = document.createElement('span');
    name.className = 'qw-result-title small';
    name.textContent = optsSafe.title;
    head.appendChild(name);
  }
  host.appendChild(head);

  if (status === 'failed' || status === 'error') {
    const msg =
      (typeof res.error === 'string' && res.error) ||
      (res.metrics && typeof res.metrics.error === 'string' ? res.metrics.error : null) ||
      (res.metrics && typeof res.metrics.headline === 'string' ? res.metrics.headline : null) ||
      'widget run failed';
    const errbox = document.createElement('div');
    errbox.className = 'errbox';
    errbox.textContent = msg;
    host.appendChild(errbox);
    host.appendChild(renderJSON(res));
    return host;
  }

  const kind = String(outputKind || 'metrics').toLowerCase();
  let body = null;

  if (kind === 'distribution') {
    const dists = findDistributions(res);
    if (dists.length) {
      body = document.createElement('div');
      dists.forEach(function (d) {
        const chart = renderDistribution(d);
        if (chart) body.appendChild(chart);
      });
    }
  } else if (kind === 'chart' || kind === 'surface' || kind === 'image' || kind === 'png') {
    body = renderArtifacts(res.artifacts);
  } else if (kind === 'metrics' || kind === 'table') {
    const metrics = res.metrics;
    if (metrics && typeof metrics === 'object' && Object.keys(metrics).length) {
      body = renderMetricsTable(metrics);
    }
  }
  // kind === 'json' (or anything unrecognized) -> body stays null -> JSON.

  if (body) {
    host.appendChild(body);
  } else {
    const hint = kind === 'json' ? null : document.createElement('div');
    if (hint) {
      hint.className = 'small muted';
      hint.textContent = 'no ' + kind + ' renderer found for this result — showing raw JSON';
      host.appendChild(hint);
    }
    host.appendChild(renderJSON(res));
  }

  // Chart/surface kinds consumed image artifacts as the body; every other
  // kind still gets any PNG artifacts appended below the primary body.
  const consumed = kind === 'chart' || kind === 'surface' || kind === 'image' || kind === 'png';
  if (!consumed) {
    const imgs = renderArtifacts(res.artifacts);
    if (imgs) host.appendChild(imgs);
  }

  // Non-image artifacts become a muted filename note (no fetch, no URL guess).
  const others = (res.artifacts || []).filter(function (a) {
    return !isImageArtifact(a) && a && typeof a.path === 'string';
  });
  if (others.length) {
    const note = document.createElement('div');
    note.className = 'small muted qw-artifact-note';
    note.textContent = 'artifacts: ' + others.map(function (a) {
      return String(a.path).split(/[\\/]/).pop() + (a.kind ? ' (' + a.kind + ')' : '');
    }).join(', ');
    host.appendChild(note);
  }

  return host;
}

function statusPillClass(status) {
  if (status === 'ok' || status === 'done' || status === 'completed') return 'ok';
  if (status === 'skipped' || status === 'partial' || status === 'running' || status === 'queued') return 'partial';
  if (status === 'failed' || status === 'error' || status === 'timeout') return 'error';
  return 'plain';
}
