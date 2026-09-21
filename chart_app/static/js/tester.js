/* Live strategy tester: move a knob, watch the marks and the P&L move.

   Modelled on TradingView's Strategy Tester, with one difference that matters:
   it scores at NEXT-BAR-OPEN fills with costs charged (see
   `chart_app/backtest.py`), so the number it shows is one you could have
   taken. The marks it draws come from the live state machine, which records a
   decision on the bar that triggered it -- so the arrow sits on the signal bar
   while the P&L assumes the bar you could actually trade.

   It runs entirely against the bars already cached for the chart. Dragging a
   slider never calls a data provider and never costs anything.
*/

window.CTest = (function () {
  const { compact } = window.CT;

  // [key, label, min, max, step, default, target]
  // `target` says which engine the knob belongs to: the signal engine's
  // config, or ELMo's own defaults.
  /* Every level the state machine actually reads, in the order it reads them.
     `add_long` and `trim_long` were missing, so two of the four long levels
     were unreachable from the UI -- the panel implied entry/exit were the
     whole rule while a position could also scale in at +55 and scale out at
     +12 with no way to see or move either. The short pair appears for the
     same reason: `allow_short` was a checkbox that turned on two levels
     nobody could set.
     The 6th column is only a FALLBACK. Real values are seeded from
     `GET /api/indicator-defaults` (`signal`), so a slider can never sit at a
     number the engine does not use -- same rule the gears follow. */
  const KNOBS = [
    ["entry_long",      "entry",        0,  100,  1,   30,   "config"],
    ["add_long",        "add",          0,  100,  1,   55,   "config"],
    ["trim_long",       "trim",       -40,   80,  1,   12,   "config"],
    ["exit_long",       "exit",       -60,   20,  1,  -12,   "config"],
    ["entry_short",     "entry short", -100,  0,  1,  -35,   "config"],
    ["add_short",       "add short",   -100,  0,  1,  -55,   "config"],
    ["trim_short",      "trim short",   -80, 40,  1,  -12,   "config"],
    ["exit_short",      "exit short",  -40,  40,  1,    8,   "config"],
    ["max_units",       "max units",      1,  5,  1,    3,   "config"],
    ["atr_stop_mult",   "atr stop",     0.5, 12,  0.5,  4.0, "config"],
    ["cooldown_bars",   "cooldown",     0,   30,  1,    3,   "config"],
    ["cost_bps",        "cost bps",     0,   50,  0.5,  2.0, "cost"],
  ];

  // Shipped values from the server, so this panel never hardcodes a default.
  let shipped = {};
  // The saved profile live on the chart, fed in by `app.js` from
  // `state.profile`. It OVERRIDES shipped for every key it carries.
  let profile = null;

  /* A knob's baseline: the saved profile's value, else shipped, else the
     table's fallback.

     The profile layer is new, and its absence was half of "my presets don't
     load". `server.post_backtest` has always merged the active profile under
     the request -- but this panel posts every knob it owns, so the merge only
     ever saw the shipped value this panel had put there. The chart drew the
     profile, the tester scored shipped, and the two disagreed silently. */
  function seed(key, fallback) {
    if (profile && profile.config && profile.config[key] != null) {
      return profile.config[key];
    }
    const v = shipped[key];
    return v == null ? fallback : v;
  }

  function paintKnobs() {
    KNOBS.forEach(function (k) {
      const base = seed(k[0], null);
      if (base == null) return;
      const el = document.getElementById("t_" + k[0]);
      const out = document.getElementById("v_" + k[0]);
      if (!el) return;
      el.value = base;
      if (out) out.textContent = el.value;
    });
    // The non-slider controls carry profile values too, and they were the
    // easiest to miss: `allow_short` and `exit_style` are strategy, not
    // cosmetics.
    const shortEl = document.getElementById("t_allow_short");
    if (shortEl && profile && profile.config && profile.config.allow_short != null) {
      shortEl.checked = !!profile.config.allow_short;
    }
    const styleEl = document.getElementById("t_exit_style");
    if (styleEl && profile && profile.config && profile.config.exit_style) {
      styleEl.value = profile.config.exit_style;
    }
    const capEl = document.getElementById("t_capital");
    if (capEl && profile && profile.capital != null) capEl.value = profile.capital;
  }

  /* Called by `app.js` on the first frame and on every symbol change. */
  function seedProfile(next) {
    profile = next || null;
    paintKnobs();
    if (enabled) schedule();   // the panel's numbers moved; rescore them
  }

  async function loadDefaults() {
    try {
      const res = await fetch("/api/indicator-defaults");
      const body = await res.json();
      shipped = Object.assign({}, body.signal || {});
    } catch (e) {
      // The fallbacks in KNOBS still stand; the panel must not fail to build
      // because a defaults fetch did.
    }
    paintKnobs();
  }

  /* INDICATOR periods are deliberately NOT here.
     ELMo's nine windows belong to the gears (`gears.js` TABLE.elmo), which is
     the one place indicators are configured. This panel used to duplicate four
     of them with different ranges, which meant the same parameter had two UIs
     that could disagree -- and the reading the backtest scored was then not
     the reading the chart was drawing.
     The split: indicators decide what is READ, this panel decides what is DONE
     with the read (levels, stop, cooldown, costs). The ELMo settings the run
     uses are inherited from the gears via `CGear.current()`. */

  let debounce = null;
  let lastResult = null;
  let onResult = function () {};
  let enabled = false;

  function values() {
    // `elmo` comes from the gears, never from this panel -- so the backtest
    // scores exactly the indicator reads the chart is drawing.
    const gears = window.CGear ? window.CGear.current() : { elmo: {} };
    const out = {
      config: {},
      elmo: gears.elmo || {},
      cost_bps: 2.0,
      capital: 100000,
    };
    const capEl = document.getElementById("t_capital");
    if (capEl) {
      const cap = parseFloat(String(capEl.value).replace(/[^0-9.]/g, ""));
      if (!isNaN(cap) && cap > 0) out.capital = cap;
    }
    // Empty date = "whatever the cache holds on that side".
    const s = document.getElementById("t_start");
    const e = document.getElementById("t_end");
    if (s && s.value) out.start = s.value;
    if (e && e.value) out.end = e.value;
    KNOBS.forEach(function (k) {
      const el = document.getElementById("t_" + k[0]);
      if (!el) return;
      const v = parseFloat(el.value);
      if (isNaN(v)) return;
      if (k[6] === "config") out.config[k[0]] = v;
      else if (k[6] === "elmo") out.elmo[k[0]] = v;
      else out.cost_bps = v;
    });
    const shortEl = document.getElementById("t_allow_short");
    if (shortEl) out.config.allow_short = !!shortEl.checked;

    const styleEl = document.getElementById("t_exit_style");
    if (styleEl) out.config.exit_style = styleEl.value;

    // `unit_fraction` null means "1/max_units", which the engine resolves.
    const capEl2 = document.getElementById("t_capital_model");
    if (capEl2) {
      out.config.unit_fraction = capEl2.value === "pyramiding" ? 1.0 : null;
    }
    return out;
  }

  function paintStats(m, err, win) {
    const el = document.getElementById("testerStats");
    if (err) {
      el.innerHTML = '<span class="bad">' + err + "</span>";
      return;
    }
    const cls = function (v) { return v > 0 ? "good" : v < 0 ? "bad" : ""; };
    const pf = m.profit_factor;
    // Beating buy-and-hold is the only comparison that matters for a
    // long-only timing rule, so it sits next to the return rather than
    // buried -- a green +30% against a +90% hold is a losing strategy.
    const edge = m.total_return_pct - m.buy_hold_pct;
    /* Dollars AND percent side by side: the dollar figure is the one that
       reads at a glance while tuning, the percent is the one that compares
       across symbols and windows. `money` is the percent scaled by the
       capital box -- not an account balance, so it is labelled "P&L on $100k"
       rather than left as a bare figure (a bare dollar amount in this app has
       already been misread as profit once -- see the whale cutoff in app.js). */
    const money = function (v) {
      return (v >= 0 ? "+$" : "-$") + compact(Math.abs(v));
    };
    el.innerHTML =
      stat("P&L on " + "$" + compact(m.capital), money(m.pnl_dollars), cls(m.pnl_dollars)) +
      stat("return", m.total_return_pct.toFixed(1) + "%", cls(m.total_return_pct)) +
      stat("buy&hold", m.buy_hold_pct.toFixed(1) + "%", "") +
      stat("vs b&h", (edge >= 0 ? "+" : "") + edge.toFixed(1) + "%  " +
           money(m.excess_vs_bh_dollars), cls(edge)) +
      stat("trades", m.trades, "") +
      stat("win", m.win_rate.toFixed(0) + "%", "") +
      stat("PF", pf == null ? "n/a" : pf.toFixed(2), pf == null ? "" : cls(pf - 1)) +
      stat("max DD", m.max_drawdown_pct.toFixed(1) + "%  " +
           money(-Math.abs(m.max_drawdown_dollars)), "bad") +
      stat("sharpe", m.sharpe.toFixed(2), cls(m.sharpe)) +
      stat("expo", m.exposure_pct.toFixed(0) + "%", "") +
      // Average share of CAPITAL at work, not share of bars in the market.
      // Buy-and-hold is 100%, so a return that trails it at 40% capital is a
      // different animal from one that trails it fully invested.
      stat(
        "capital used",
        (m.avg_exposure_pct == null ? "-" : m.avg_exposure_pct.toFixed(0) + "%") +
          "  " + (m.capital_model || ""),
        m.capital_model === "pyramiding" ? "warn" : ""
      ) +
      stat("avg hold", m.avg_bars_held.toFixed(0) + "b", "") +
      // Which bars this actually scored. A narrowed window is called out,
      // because an out-of-sample check is only meaningful if you can see the
      // span it ran on.
      (win
        ? stat(
            win.narrowed ? "window (narrowed)" : "window",
            win.start + " → " + win.end + "  " + win.bars + "b",
            win.narrowed ? "warn" : ""
          )
        : "");
  }

  function stat(label, value, cls) {
    return (
      '<span class="stat"><i>' + label + '</i><b class="' + (cls || "") + '">' +
      value + "</b></span>"
    );
  }

  async function run() {
    if (!enabled) return;
    const body = values();
    document.getElementById("testerStats").classList.add("busy");
    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const out = await res.json();
      if (!out.ok) {
        paintStats(null, out.error || "backtest failed");
        lastResult = null;
      } else {
        paintStats(out.metrics, null, out.window);
        boundDates(out.cache_span);
        lastResult = out;
      }
    } catch (e) {
      paintStats(null, String(e));
      lastResult = null;
    } finally {
      document.getElementById("testerStats").classList.remove("busy");
      onResult(lastResult);
    }
  }

  async function saveProfile(scope) {
    const v = values();
    const body = {
      config: v.config,
      elmo: v.elmo,
      capital: v.capital,
      // A slider drag against visible bars IS an in-sample fit. Only
      // `backtest_runner`, after actually running folds, may claim better.
      validation: "in_sample",
      metrics: lastResult ? lastResult.metrics : {},
      note: "saved from the live tester",
    };
    if (scope === "timeframe") body.ticker = "*";
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await res.json();
    flashProfile(out.ok ? "saved " + out.saved.ticker + "|" + out.saved.interval
                        : (out.error || "save failed"), !out.ok);
  }

  async function clearProfile() {
    const res = await fetch("/api/profiles", { method: "DELETE" });
    const out = await res.json();
    flashProfile(out.removed ? "profile cleared" : "no profile to clear", false);
  }

  /* Bound the pickers to the bars that actually exist.

     Typing a range wider than the cache used to intersect straight back to the
     full span, which looks exactly like the dates being ignored -- and on an
     intraday chart no amount of refreshing fixes it, because intraday history
     is capped at 30 days at the provider. Saying so beats letting someone
     retype dates at a window that cannot move. */
  function boundDates(avail) {
    if (!avail) return;
    const s = document.getElementById("t_start");
    const e = document.getElementById("t_end");
    [s, e].forEach(function (el) {
      if (!el) return;
      el.min = avail.start;
      el.max = avail.end;
    });
    fillSpans(avail.interval);
    const note = document.getElementById("dataSpan");
    if (note) {
      note.textContent =
        "data " + avail.start + " \u2192 " + avail.end +
        " (" + avail.bars + "b)" +
        (avail.intraday_chunked ? " \u00b7 deeper pulls chunk, so they are slow" : "");
    }
  }

  /* Span choices depend on the timeframe: a 3-minute chart over two years is
     hundreds of thousands of one-minute rows to aggregate, and a daily chart
     has no business asking for 30 days. */
  const LOAD_SPANS = {
    fast:  ["30d", "60d", "90d", "180d"],           // 3m / 5m / 10m
    mid:   ["60d", "180d", "1y", "2y"],             // 15m / 30m / 1h / 4h
    daily: ["1y", "2y", "3y", "5y"],                // 1d
  };

  function spansFor(interval) {
    if (interval === "1d") return LOAD_SPANS.daily;
    if (["3m", "5m", "10m"].indexOf(interval) >= 0) return LOAD_SPANS.fast;
    return LOAD_SPANS.mid;
  }

  function fillSpans(interval) {
    const sel = document.getElementById("t_loadSpan");
    if (!sel) return;
    const want = spansFor(interval || "15m");
    if (sel.dataset.interval === interval) return;
    sel.dataset.interval = interval || "";
    sel.innerHTML = want
      .map(function (s) { return '<option value="' + s + '">' + s + "</option>"; })
      .join("");
  }

  async function loadHistory() {
    const sel = document.getElementById("t_loadSpan");
    const btn = document.getElementById("loadHistory");
    if (!sel || !btn) return;
    const span = sel.value;
    btn.disabled = true;
    const was = btn.textContent;
    // A deep intraday pull chunks at 21 days and sleeps between chunks, so a
    // year is minutes, not seconds. Say so rather than look hung.
    btn.textContent = "loading " + span + "...";
    flashProfile("fetching " + span + " -- chunked, this can take minutes", false);
    try {
      const res = await fetch("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback: span }),
      });
      const out = await res.json();
      if (!out.ok) {
        flashProfile(out.error || "load failed", true);
      } else {
        boundDates(out.cache_span);
        flashProfile(
          "+" + out.upserted + " bars \u00b7 now " +
          (out.available && out.available.start) + " \u2192 " +
          (out.available && out.available.end),
          false
        );
        rerun();   // score the history we just went and got
      }
    } catch (e) {
      flashProfile(String(e), true);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
  }

  function flashProfile(text, bad) {
    const el = document.getElementById("profMsg");
    if (!el) return;
    el.textContent = text;
    el.className = bad ? "bad" : "good";
    setTimeout(function () { el.textContent = ""; el.className = ""; }, 2600);
  }

  function schedule() {
    // Debounced: a slider drag fires an input event per pixel, and each run
    // re-evaluates every indicator over the whole series.
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(run, 180);
  }

  function build(container) {
    const parts = KNOBS.map(function (k) {
      return (
        '<label class="knob" title="' + k[0] + '">' +
        "<i>" + k[1] + "</i>" +
        '<input type="range" id="t_' + k[0] + '" min="' + k[2] + '" max="' + k[3] +
        '" step="' + k[4] + '" value="' + k[5] + '">' +
        '<b id="v_' + k[0] + '">' + k[5] + "</b></label>"
      );
    });
    parts.push(
      '<label class="knob chk"><input type="checkbox" id="t_allow_short"><i>allow short</i></label>'
    );
    /* Two CHOICES, not ranges -- they change what a return means, so they are
       selects rather than sliders you can drift across by accident.

       capital model: "fractional" makes one unit 1/max_units of the book, so
       full conviction is 100% invested and it never borrows. "pyramiding"
       makes one unit the whole book and the adds lever it to max_units x --
       which raised SPY 1d from 17.3% to 55.5% purely by taking 3x the
       exposure and 3x the drawdown. That is leverage, not edge.

       exit style: "full" closes on an exit signal; "scale" sheds one unit per
       action, so a position built in three steps comes off in three. A STOP
       always closes everything either way. */
    parts.push(
      '<label class="knob box"><i>capital</i>' +
      '<select id="t_capital_model">' +
      '<option value="fractional">fractional (no margin)</option>' +
      '<option value="pyramiding">pyramiding (levers up)</option>' +
      "</select></label>"
    );
    parts.push(
      '<label class="knob box"><i>exit</i>' +
      '<select id="t_exit_style">' +
      '<option value="full">full exit</option>' +
      '<option value="scale">scale out</option>' +
      "</select></label>"
    );
    /* Capital scales the dollar figures; the dates narrow the scored span.
       Both are text/date boxes rather than sliders -- they are not things you
       drag, and a stray drag on a date would re-run the engine per pixel. */
    parts.push(
      '<label class="knob box"><i>capital $</i>' +
      '<input type="text" id="t_capital" value="100000" size="8"></label>'
    );
    /* THE BUTTON THIS WHOLE THREAD STARTED FOR.
       The date pickers only ever NARROW bars already cached, deliberately --
       dragging a slider must not fetch. So a longer backtest was impossible
       until something explicitly went and got the history. That used to be
       flatly refused above 30 days, which turned out to be httpx's 5s default
       rather than any provider limit (see CLAUDE.md). It is not refused now.

       Nothing here fetches on its own: this pulls only when pressed. */
    parts.push(
      '<div class="knob prof" id="loadRow">' +
      '<select id="t_loadSpan"></select>' +
      '<button type="button" id="loadHistory" title="Fetch this much history for the current symbol and timeframe, into the bar cache">load history</button>' +
      "</div>"
    );
    parts.push(
      '<label class="knob box"><i>from</i>' +
      '<input type="date" id="t_start"></label>'
    );
    parts.push(
      '<label class="knob box"><i>to</i>' +
      '<input type="date" id="t_end"></label>'
    );
    parts.push('<button type="button" id="testerReset">reset</button>');
    /* Saving is what makes tuning reach the CHART: the live snapshot resolves
       a profile for (ticker, interval) and feeds it to ELMo and the signal
       engine. Without this the knobs only ever moved the tester's own P&L.
       Two scopes on purpose -- the timeframe one is structural (windows are
       counted in bars, so a horizon is timeframe-specific), the symbol one is
       the kind the walk-forward warned about and is stamped `in_sample`. */
    parts.push(
      '<div class="knob prof">' +
      '<button type="button" id="profSaveSym" title="Apply these to THIS symbol at this timeframe">save · symbol</button>' +
      '<button type="button" id="profSaveTf" title="Apply these to EVERY symbol at this timeframe (structural: windows are counted in bars)">save · timeframe</button>' +
      '<button type="button" id="profClear" title="Delete this symbol’s profile">clear</button>' +
      "</div>"
    );
    /* A floating panel rather than a strip above the chart. The strip pushed
       the plot down by 62-87px, so every time it opened the candles resized
       and the thing you were reading moved. A panel overlays instead: the
       chart behind it never reflows while you tune. */
    container.innerHTML =
      '<div id="testerHead">' +
      '<span>strategy tester <b id="profMsg"></b></span>' +
      '<span id="dataSpan"></span>' +
      '<button type="button" id="testerClose" title="close (key: t)">×</button>' +
      "</div>" +
      '<div id="testerKnobs">' + parts.join("") + "</div>" +
      '<div id="testerStats">move a knob to score the loaded bars</div>';

    KNOBS.forEach(function (k) {
      const el = document.getElementById("t_" + k[0]);
      const out = document.getElementById("v_" + k[0]);
      el.addEventListener("input", function () {
        out.textContent = el.value;
        schedule();
      });
    });
    document.getElementById("t_allow_short").addEventListener("change", schedule);
    loadDefaults();
    document.getElementById("loadHistory").addEventListener("click", loadHistory);
    document.getElementById("profSaveSym")
      .addEventListener("click", function () { saveProfile("symbol"); });
    document.getElementById("profSaveTf")
      .addEventListener("click", function () { saveProfile("timeframe"); });
    document.getElementById("profClear")
      .addEventListener("click", clearProfile);
    ["t_capital", "t_start", "t_end", "t_capital_model", "t_exit_style"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", schedule);
    });
    document.getElementById("testerReset").addEventListener("click", function () {
      // Back to the BASELINE -- the saved profile if there is one, else what
      // the engine ships. Resetting to shipped while a profile was loaded
      // would silently discard it, which is the behaviour this whole change
      // exists to remove.
      paintKnobs();
      if (!(profile && profile.config && profile.config.allow_short != null)) {
        document.getElementById("t_allow_short").checked = false;
      }
      schedule();
    });
  }

  function setEnabled(on, cb) {
    enabled = !!on;
    if (cb) onResult = cb;
    if (enabled) run();
    else {
      lastResult = null;
      onResult(null);
    }
  }

  /* Overlay the tester's run onto a /api/state payload so the chart draws the
     parameters being tested instead of the shipped ones. Returns a shallow
     copy -- the live payload is left untouched so turning the tester off
     restores the real chart with no refetch. */
  function applyTo(state) {
    if (!enabled || !lastResult || !state) return state;
    if (lastResult.ticker !== state.ticker || lastResult.interval !== state.interval) {
      return state;   // stale: symbol changed since the last run
    }
    const out = Object.assign({}, state);
    out.algo = Object.assign({}, state.algo, {
      score: lastResult.score,
      actions: lastResult.actions,
      position: lastResult.position,
      stop: lastResult.stop,
      atr: lastResult.atr,
      components: lastResult.components,
      available: lastResult.available,
      trades: lastResult.trades,
      config: Object.assign({}, (state.algo || {}).config, lastResult.config),
    });
    out.elmo = lastResult.elmo || state.elmo;
    return out;
  }

  function rerun() { if (enabled) schedule(); }

  return {
    build: build, setEnabled: setEnabled, applyTo: applyTo, rerun: rerun,
    seedProfile: seedProfile,
  };
})();
